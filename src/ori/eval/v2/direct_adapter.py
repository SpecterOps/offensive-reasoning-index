"""Narrow v2 adapter around the authoritative direct-query coordinator."""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from typing import Any

from ori.eval.direct_query_safety import query_fingerprint

from .comparator import compare
from .evidence import EvidenceNormalizationError, normalize_direct_evidence
from .fingerprint import canonical_sha256
from .identity import IdentityResolutionError, IdentityResolver
from .schema import (
    DIRECT_QUERY_POLICY_VERSION,
    CircuitState,
    DirectExecutionReceipt,
    EdgeWitness,
    EntityPropertyFact,
    EvidenceIR,
    ExecutionClass,
    HealthState,
    OracleBundle,
    TaskBundle,
    Verdict,
)

_MODEL_FAILURES = frozenset({"policy_rejected", "query_timeout", "query_error"})
_INFRA_FAILURES = frozenset(
    {
        "auth_error",
        "client_timeout",
        "transport_error",
        "server_unavailable",
        "server_error",
        "rate_limited",
        "response_error",
        "circuit_open",
    }
)
DIRECT_ASSERTION_FIELDS = frozenset(
    {
        "count",
        "decision",
        "negative_reason_codes",
        "no_path",
        "path_status",
        "rejected_decoy_ids",
        "truncated",
    }
)
_COUNT_FIELDS = (
    "count",
    "total_count",
    "total",
    "COUNT(*)",
)


class DirectAdapterError(ValueError):
    """Raised when a successful direct result cannot become trustworthy evidence."""


class DirectV2Outcome:
    """Execution, evidence, and verdict kept as independent result dimensions."""

    def __init__(
        self,
        *,
        receipt: DirectExecutionReceipt,
        evidence: EvidenceIR | None,
        verdict: Verdict | None,
        error: str | None,
        harness_error: bool = False,
    ) -> None:
        self.receipt = receipt
        self.evidence = evidence
        self.verdict = verdict
        self.error = error
        self.harness_error = harness_error


def _execution_class(result: Any) -> ExecutionClass:
    if bool(result.success):
        return ExecutionClass.SUCCESS
    failure_type = str(result.failure_type or "")
    if failure_type in _MODEL_FAILURES:
        return ExecutionClass.MODEL_FAILURE
    if failure_type in _INFRA_FAILURES:
        return (
            ExecutionClass.UNEXECUTED
            if failure_type == "circuit_open"
            else ExecutionClass.INFRA_FAILURE
        )
    return ExecutionClass.HARNESS_FAILURE


def _health_state(value: str | None) -> HealthState:
    normalized = str(value or "not_checked").casefold()
    if normalized == "healthy":
        return HealthState.HEALTHY
    if normalized == "unhealthy":
        return HealthState.UNHEALTHY
    return HealthState.NOT_CHECKED


def _circuit_state(value: str | None) -> CircuitState:
    return CircuitState.OPEN if str(value).casefold() == "open" else CircuitState.CLOSED


def _receipt(result: Any, *, elapsed_seconds: float) -> DirectExecutionReceipt:
    raw = result.raw if isinstance(result.raw, Mapping) else {}
    return DirectExecutionReceipt(
        execution_class=_execution_class(result),
        failure_type=str(result.failure_type) if result.failure_type else None,
        failure_subtype=(
            str(result.failure_subtype) if result.failure_subtype else None
        ),
        status_code=result.status_code,
        query_executed=bool(result.query_executed),
        attempts=int(result.execution_attempts),
        query_fingerprint=(
            str(result.query_fingerprint) if result.query_fingerprint else None
        ),
        policy_version=str(
            result.safety_policy_version or DIRECT_QUERY_POLICY_VERSION
        ),
        policy_rule=str(result.safety_rule) if result.safety_rule else None,
        elapsed_seconds=float(elapsed_seconds),
        post_query_health=_health_state(result.bhce_health_after),
        circuit_state=_circuit_state(result.circuit_state),
        response_digest=canonical_sha256(raw) if raw else None,
    )


def _harness_receipt(
    query: str,
    *,
    elapsed_seconds: float,
    policy_version: str,
    failure_subtype: str,
    result: Any | None = None,
) -> DirectExecutionReceipt:
    """Record an internal boundary failure without inventing provenance."""

    raw_query_executed = getattr(result, "query_executed", None)
    raw_attempts = getattr(result, "execution_attempts", None)
    result_fingerprint = getattr(result, "query_fingerprint", None)

    return DirectExecutionReceipt(
        execution_class=ExecutionClass.HARNESS_FAILURE,
        failure_type="harness_error",
        failure_subtype=failure_subtype,
        query_executed=(
            raw_query_executed if isinstance(raw_query_executed, bool) else None
        ),
        attempts=(
            raw_attempts
            if isinstance(raw_attempts, int)
            and not isinstance(raw_attempts, bool)
            and raw_attempts >= 0
            else None
        ),
        query_fingerprint=(
            result_fingerprint
            if isinstance(result_fingerprint, str)
            and len(result_fingerprint) == 64
            else query_fingerprint(query)
        ),
        policy_version=policy_version,
        policy_rule=(
            "receipt_construction_failed"
            if result is not None
            else "execution_state_unconfirmed"
        ),
        elapsed_seconds=float(elapsed_seconds),
        post_query_health=HealthState.NOT_CHECKED,
        circuit_state=CircuitState.CLOSED,
    )


def _node_identity(node: Mapping[str, Any]) -> str:
    properties = node.get("properties")
    if not isinstance(properties, Mapping):
        properties = node.get("Props")
    if not isinstance(properties, Mapping):
        properties = node.get("props")
    props = properties if isinstance(properties, Mapping) else {}
    for key in (
        "objectId",
        "objectid",
        "ObjectIdentifier",
        "objectidentifier",
        "label",
        "name",
    ):
        value = node.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    for key in (
        "objectId",
        "objectid",
        "ObjectIdentifier",
        "objectidentifier",
        "name",
        "Name",
    ):
        value = props.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    raise DirectAdapterError(f"BloodHound node has no stable identity: {node!r}")


def _looks_like_literal_node(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    if any(
        value.get(key) is not None
        for key in ("objectId", "objectid", "ObjectIdentifier", "label")
    ):
        return True
    for properties_key in ("properties", "Props", "props"):
        properties = value.get(properties_key)
        if not isinstance(properties, Mapping):
            continue
        if any(
            properties.get(key) is not None
            for key in (
                "objectId",
                "objectid",
                "ObjectIdentifier",
                "objectidentifier",
                "name",
                "Name",
            )
        ):
            return True
    return False


def _literal_node_collections(
    raw: Mapping[str, Any],
) -> tuple[tuple[str, tuple[Mapping[str, Any], ...]], ...]:
    """Read collected CE nodes without confusing collected relationships."""

    inner = raw.get("data", raw)
    if not isinstance(inner, Mapping):
        return ()
    literals = inner.get("literals")
    if not isinstance(literals, Sequence) or isinstance(literals, (str, bytes)):
        return ()
    collections: list[tuple[str, tuple[Mapping[str, Any], ...]]] = []
    for literal in literals:
        if not isinstance(literal, Mapping):
            continue
        value = literal.get("value")
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            continue
        items = tuple(value)
        key = str(literal.get("key") or "")
        if items and all(_looks_like_literal_node(item) for item in items):
            collections.append((key, items))
        elif not items and key.casefold() in {"entities", "entity"}:
            collections.append((key, ()))
    return tuple(collections)


def _project_set_nodes(
    raw: Mapping[str, Any],
    graph_nodes: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], ...]:
    """Select answer nodes from one unambiguous direct result surface."""

    collections = _literal_node_collections(raw)
    named = tuple(
        nodes for key, nodes in collections if key.casefold() in {"entities", "entity"}
    )
    candidates = named or tuple(nodes for _, nodes in collections)
    if len(candidates) > 1:
        raise DirectAdapterError(
            "direct set result contains multiple answer-node collections"
        )
    if candidates:
        return candidates[0]
    return tuple(graph_nodes)


def _raw_graph(raw: Mapping[str, Any]) -> tuple[
    tuple[Mapping[str, Any], ...],
    tuple[Mapping[str, Any], ...],
]:
    inner = raw.get("data", raw)
    if not isinstance(inner, Mapping):
        return (), ()
    raw_nodes = inner.get("nodes") or ()
    raw_edges = inner.get("edges") or ()
    nodes = (
        tuple(raw_nodes.values())
        if isinstance(raw_nodes, Mapping)
        else tuple(raw_nodes)
        if isinstance(raw_nodes, Sequence) and not isinstance(raw_nodes, (str, bytes))
        else ()
    )
    edges = (
        tuple(raw_edges.values())
        if isinstance(raw_edges, Mapping)
        else tuple(raw_edges)
        if isinstance(raw_edges, Sequence) and not isinstance(raw_edges, (str, bytes))
        else ()
    )
    if any(not isinstance(node, Mapping) for node in nodes):
        raise DirectAdapterError("BloodHound response contains a non-object node")
    if any(not isinstance(edge, Mapping) for edge in edges):
        raise DirectAdapterError("BloodHound response contains a non-object edge")
    return nodes, edges


def _endpoint_token(
    edge: Mapping[str, Any],
    names: Sequence[str],
    node_keys: Mapping[str, str],
) -> str:
    for name in names:
        value = edge.get(name)
        if value is None:
            continue
        token = str(value)
        return node_keys.get(token, token)
    raise DirectAdapterError(f"BloodHound edge has no endpoint field {names}: {edge!r}")


def _relationship_token(edge: Mapping[str, Any]) -> str:
    for name in ("relationship", "kind", "label", "type", "edge", "name"):
        value = edge.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    raise DirectAdapterError(f"BloodHound edge has no relationship kind: {edge!r}")


def _project_edges(
    raw: Mapping[str, Any],
    nodes: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    inner = raw.get("data", raw)
    if not isinstance(inner, Mapping):
        return ()
    raw_nodes = inner.get("nodes") or ()
    node_keys: dict[str, str] = {}
    if isinstance(raw_nodes, Mapping):
        for key, node in raw_nodes.items():
            if isinstance(node, Mapping):
                node_keys[str(key)] = _node_identity(node)
    for index, node in enumerate(nodes):
        node_keys.setdefault(str(index), _node_identity(node))

    _, edges = _raw_graph(raw)
    projected: list[dict[str, Any]] = []
    for edge in edges:
        properties = edge.get("properties")
        projected.append(
            {
                "source_id": _endpoint_token(
                    edge,
                    (
                        "source_id",
                        "source",
                        "start_id",
                        "start",
                        "sourceNodeId",
                        "source_node_id",
                    ),
                    node_keys,
                ),
                "relationship": _relationship_token(edge),
                "target_id": _endpoint_token(
                    edge,
                    (
                        "target_id",
                        "target",
                        "end_id",
                        "end",
                        "targetNodeId",
                        "target_node_id",
                    ),
                    node_keys,
                ),
                "direction": str(edge.get("direction") or "outbound").casefold(),
                "properties": (
                    dict(properties) if isinstance(properties, Mapping) else {}
                ),
            }
        )
    return tuple(projected)


def _project_properties(
    nodes: Sequence[Mapping[str, Any]],
    *,
    allowed_fields: Mapping[tuple[str, str], str],
    resolver: IdentityResolver,
) -> tuple[dict[str, Any], ...]:
    if not allowed_fields:
        return ()
    facts: list[dict[str, Any]] = []
    for node in nodes:
        raw_identity = _node_identity(node)
        try:
            identity = resolver.resolve(raw_identity)
        except IdentityResolutionError:
            # Unknown returned nodes are rejected later by entity
            # normalization. They must not contribute a property fact for a
            # different sealed identity in the meantime.
            continue
        raw_properties = node.get("properties")
        if not isinstance(raw_properties, Mapping):
            raw_properties = node.get("Props")
        if not isinstance(raw_properties, Mapping):
            raw_properties = node.get("props")
        properties = raw_properties if isinstance(raw_properties, Mapping) else node
        for key, value in properties.items():
            canonical_key = allowed_fields.get(
                (identity.casefold(), str(key).casefold())
            )
            if canonical_key is not None and (
                value is None or isinstance(value, (str, int, float, bool))
            ):
                facts.append(
                    {
                        "entity_id": identity,
                        "key": canonical_key,
                        "value": value,
                    }
                )
    return tuple(facts)


def _claim_entity_projection(
    task: TaskBundle,
    oracle: OracleBundle,
    nodes: Sequence[Mapping[str, Any]],
) -> tuple[str, ...]:
    identities = tuple(_node_identity(node) for node in nodes)
    if task.claim_kind == "decision":
        primary_ids = {
            entity.object_id.casefold()
            for entity in oracle.expected_entities
        }
        allowed_ids = {
            *primary_ids,
            *(
                endpoint.casefold()
                for edge in oracle.required_context
                for endpoint in (edge.source_id, edge.target_id)
            ),
            *(fact.entity_id.casefold() for fact in oracle.required_properties),
        }
    elif task.claim_kind == "absence":
        checked_ids = {
            entity_id.casefold()
            for witness in oracle.negative_witnesses
            for entity_id in witness.checked_entity_ids
        }
        checked_edges = tuple(
            edge
            for witness in oracle.negative_witnesses
            for edge in witness.checked_edges
        )
        checked_properties = tuple(
            fact
            for witness in oracle.negative_witnesses
            for fact in witness.checked_properties
        )
        primary_ids = checked_ids
        allowed_ids = {
            *checked_ids,
            *(
                endpoint.casefold()
                for edge in checked_edges
                for endpoint in (edge.source_id, edge.target_id)
            ),
            *(fact.entity_id.casefold() for fact in checked_properties),
            *(fact.entity_id.casefold() for fact in oracle.required_properties),
        }
    else:
        return identities

    unexpected = sorted(
        identity for identity in identities if identity.casefold() not in allowed_ids
    )
    if unexpected:
        raise DirectAdapterError(
            "direct result contains nodes outside the sealed decision/proof "
            f"context: {unexpected}"
        )
    return tuple(
        identity for identity in identities if identity.casefold() in primary_ids
    )


def _required_property_fields(
    oracle: OracleBundle,
) -> dict[tuple[str, str], str]:
    """Keep unresolved claim predicates out of the graph-evidence boundary."""

    facts = (
        *oracle.required_properties,
        *(
            fact
            for witness in oracle.negative_witnesses
            for fact in witness.checked_properties
        ),
    )
    unresolved = tuple(
        type(fact).__name__
        for fact in facts
        if not isinstance(fact, EntityPropertyFact)
    )
    if unresolved:
        raise DirectAdapterError(
            "direct property projection requires resolved EntityPropertyFact "
            f"objects, received {unresolved}"
        )
    return {
        (fact.entity_id.casefold(), fact.key.casefold()): fact.key
        for fact in facts
    }


def _non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _count_from_mapping(value: Mapping[str, Any]) -> tuple[int, ...]:
    explicit = tuple(
        parsed
        for key in _COUNT_FIELDS
        if (parsed := _non_negative_int(value.get(key))) is not None
    )
    if explicit:
        return explicit
    scalar_values = tuple(
        parsed
        for item in value.values()
        if (parsed := _non_negative_int(item)) is not None
    )
    return scalar_values if len(value) == 1 else ()


def _scalar_count_candidates(result: Any, raw: Mapping[str, Any]) -> set[int]:
    candidates: list[int] = []
    for container in (raw, raw.get("data")):
        if isinstance(container, Mapping):
            candidates.extend(_count_from_mapping(container))
            literals = container.get("literals")
            if isinstance(literals, Sequence) and not isinstance(
                literals, (str, bytes)
            ):
                for literal in literals:
                    if not isinstance(literal, Mapping):
                        continue
                    key = str(literal.get("key") or "")
                    parsed = _non_negative_int(literal.get("value"))
                    if key in _COUNT_FIELDS and parsed is not None:
                        candidates.append(parsed)

    rows = result.nodes if isinstance(result.nodes, Sequence) else ()
    if len(rows) == 1 and isinstance(rows[0], Mapping):
        candidates.extend(_count_from_mapping(rows[0]))
    return set(candidates)


def _project_scalar_count(
    result: Any,
    raw: Mapping[str, Any],
    *,
    required: bool = True,
) -> int | None:
    """Extract one unambiguous non-negative scalar count from graph output."""

    distinct = _scalar_count_candidates(result, raw)
    if not distinct and not required:
        return None
    if len(distinct) != 1:
        detail = "missing" if not distinct else "ambiguous"
        raise DirectAdapterError(
            f"direct count evidence is {detail}; expected one non-negative scalar"
        )
    return distinct.pop()


def _edge_key(edge: Mapping[str, Any] | EdgeWitness) -> tuple[str, str, str]:
    if isinstance(edge, EdgeWitness):
        return (edge.source_id, edge.relationship, edge.target_id)
    return (
        str(edge["source_id"]),
        str(edge["relationship"]),
        str(edge["target_id"]),
    )


def _partition_and_order_edges(
    projected_edges: Sequence[dict[str, Any]],
    oracle: OracleBundle,
) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
    context_keys = {_edge_key(edge) for edge in oracle.required_context}
    supporting = tuple(
        edge for edge in projected_edges if _edge_key(edge) in context_keys
    )
    route = tuple(
        edge for edge in projected_edges if _edge_key(edge) not in context_keys
    )
    if not route:
        return (), supporting
    if all(
        route[index]["target_id"] == route[index + 1]["source_id"]
        for index in range(len(route) - 1)
    ):
        return route, supporting

    if oracle.source_id is None:
        raise DirectAdapterError("unordered route has no sealed source binding")
    outgoing: dict[str, list[dict[str, Any]]] = {}
    for edge in route:
        outgoing.setdefault(str(edge["source_id"]), []).append(edge)
    ordered: list[dict[str, Any]] = []
    current = oracle.source_id
    unused = list(route)
    while unused:
        choices = [edge for edge in outgoing.get(current, ()) if edge in unused]
        if len(choices) != 1:
            raise DirectAdapterError(
                "BloodHound route edges are unordered or branch ambiguously"
            )
        selected = choices[0]
        ordered.append(selected)
        unused.remove(selected)
        current = str(selected["target_id"])
    if oracle.target_id is not None and current != oracle.target_id:
        raise DirectAdapterError("ordered BloodHound route ends at the wrong target")
    return tuple(ordered), supporting


def project_direct_evidence(
    result: Any,
    *,
    task: TaskBundle,
    oracle: OracleBundle,
    resolver: IdentityResolver,
    answer_payload: Mapping[str, Any] | None = None,
) -> EvidenceIR:
    """Project a successful CypherResult without consulting the query text."""

    if not result.success:
        raise DirectAdapterError("cannot project evidence from a failed query")
    raw = result.raw if isinstance(result.raw, Mapping) else {}
    nodes, _ = _raw_graph(raw)
    projected_edges = _project_edges(raw, nodes)
    route_edges, supporting_edges = _partition_and_order_edges(
        projected_edges,
        oracle,
    )

    payload: dict[str, Any] = {"task_id": task.task_id}
    if task.claim_kind == "set":
        answer_nodes = _project_set_nodes(raw, nodes)
        if len(answer_nodes) > task.binding.bounds.max_result_cardinality:
            raise DirectAdapterError(
                "direct set result exceeds its certified cardinality bound"
            )
        payload["entities"] = [_node_identity(node) for node in answer_nodes]
        declared_count = _project_scalar_count(result, raw, required=False)
        if declared_count is not None and declared_count != len(answer_nodes):
            raise DirectAdapterError(
                "direct set total_count does not match returned answer nodes"
            )
    elif task.claim_kind == "count":
        payload["count"] = _project_scalar_count(result, raw)
    else:
        required_property_fields = _required_property_fields(oracle)
        payload["entities"] = _claim_entity_projection(
            task,
            oracle,
            nodes,
        )
        payload["edges"] = route_edges
        payload["supporting_edges"] = supporting_edges
        payload["observed_properties"] = _project_properties(
            nodes,
            allowed_fields=required_property_fields,
            resolver=resolver,
        )
        if route_edges:
            payload["path_status"] = "found"

    if answer_payload:
        for key, value in answer_payload.items():
            if key not in DIRECT_ASSERTION_FIELDS:
                raise DirectAdapterError(
                    f"direct answer payload cannot override graph evidence field {key!r}"
                )
            payload[key] = value

    if task.claim_kind == "route" and not route_edges and nodes:
        raise DirectAdapterError(
            "path result contains no ordered edge witness; nodes alone are insufficient"
        )
    try:
        return normalize_direct_evidence(payload, resolver=resolver)
    except (EvidenceNormalizationError, ValueError) as exc:
        raise DirectAdapterError(str(exc)) from exc


async def execute_direct_v2(
    coordinator: Any,
    *,
    query: str,
    task: TaskBundle,
    oracle: OracleBundle,
    resolver: IdentityResolver,
    answer_payload: Mapping[str, Any] | None = None,
) -> DirectV2Outcome:
    """Execute exactly once through DirectQueryCoordinator and score on success."""

    config = coordinator.config
    if not config.enabled or config.policy_version != DIRECT_QUERY_POLICY_VERSION:
        raise DirectAdapterError(
            "certified v2 direct execution requires enabled direct-query policy v3"
        )
    started = time.monotonic()
    try:
        result = await coordinator.execute(query)
    except Exception as exc:
        return DirectV2Outcome(
            receipt=_harness_receipt(
                query,
                elapsed_seconds=time.monotonic() - started,
                policy_version=config.policy_version,
                failure_subtype=type(exc).__name__,
            ),
            evidence=None,
            verdict=None,
            error=f"{type(exc).__name__}: {exc}",
            harness_error=True,
        )
    try:
        receipt = _receipt(result, elapsed_seconds=time.monotonic() - started)
    except Exception as exc:
        return DirectV2Outcome(
            receipt=_harness_receipt(
                query,
                elapsed_seconds=time.monotonic() - started,
                policy_version=config.policy_version,
                failure_subtype=type(exc).__name__,
                result=result,
            ),
            evidence=None,
            verdict=None,
            error=f"{type(exc).__name__}: {exc}",
            harness_error=True,
        )
    if receipt.execution_class is not ExecutionClass.SUCCESS:
        return DirectV2Outcome(
            receipt=receipt,
            evidence=None,
            verdict=None,
            error=str(result.error or receipt.failure_type or "direct execution failed"),
        )
    try:
        evidence = project_direct_evidence(
            result,
            task=task,
            oracle=oracle,
            resolver=resolver,
            answer_payload=answer_payload,
        )
        verdict = compare(task.answer_policy, oracle, evidence)
    except DirectAdapterError as exc:
        return DirectV2Outcome(
            receipt=receipt,
            evidence=None,
            verdict=None,
            error=str(exc),
        )
    except Exception as exc:
        return DirectV2Outcome(
            receipt=receipt,
            evidence=None,
            verdict=None,
            error=f"{type(exc).__name__}: {exc}",
            harness_error=True,
        )
    return DirectV2Outcome(
        receipt=receipt,
        evidence=evidence,
        verdict=verdict,
        error=None,
    )
