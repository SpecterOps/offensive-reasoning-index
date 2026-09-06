"""Track-specific replay of fixture evidence across verified graph snapshots."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal

from ori.eval.bhce import CypherResult

from .comparator import compare
from .direct_adapter import (
    DIRECT_ASSERTION_FIELDS,
    DirectAdapterError,
    project_direct_evidence,
)
from .evidence import EvidenceNormalizationError, validate_and_normalize_evidence
from .fingerprint import canonical_sha256
from .fixtures import FixtureCase
from .graph import (
    GraphSnapshot,
    build_graph_fact_registry,
    graph_edge_key_buckets,
    graph_entity_property_index,
    graph_identity_resolver,
    graph_object_id_set,
    graph_object_index,
)
from .mcp import MCPToolLoop
from .mcp_adapter import score_mcp_transcript_v2
from .model_runtime import MCPTranscriptProjector
from .schema import (
    CapabilityProfile,
    EdgeDirection,
    EdgeWitness,
    EvidenceIR,
    ExecutionClass,
    OracleBundle,
    PathStatus,
    TaskBundle,
    Verdict,
)
from .scoring import SampleOutcomeCode

ProjectionSource = Literal[
    "graph_snapshot_replay",
    "adversarial_replay",
    "malformed_replay",
]


@dataclass(frozen=True)
class SurfaceProjection:
    """One adapter result retained by private certification evidence."""

    projection_source: ProjectionSource
    execution_class: ExecutionClass
    outcome: SampleOutcomeCode
    evidence: EvidenceIR | None
    verdict: Verdict | None
    raw_source_digest: str
    rejection_reason: str | None = None


def semantic_evidence_fingerprint(evidence: EvidenceIR) -> str:
    """Fingerprint normalized meaning without conflating surface raw digests."""

    return canonical_sha256(evidence, exclude_fields=("raw_digest",))


def _snapshot_objects(snapshot: GraphSnapshot) -> dict[str, Any]:
    return graph_object_index(snapshot)


def _evidence_ids(evidence: EvidenceIR) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            (
                *(entity.object_id for entity in evidence.entities),
                *(
                    endpoint
                    for edge in (*evidence.edges, *evidence.supporting_edges)
                    for endpoint in (edge.source_id, edge.target_id)
                ),
                *(fact.entity_id for fact in evidence.observed_properties),
            )
        )
    )


def _edge_matches_snapshot(edge: EdgeWitness, snapshot: GraphSnapshot) -> bool:
    candidates = graph_edge_key_buckets(snapshot).get(
        (edge.source_id, edge.relationship, edge.target_id),
        (),
    )
    for candidate in candidates:
        if candidate.direction != edge.direction:
            continue
        candidate_properties = {
            fact.key.casefold(): fact.value for fact in candidate.properties
        }
        return all(
            candidate_properties.get(fact.key.casefold()) == fact.value
            for fact in edge.properties
        )
    return False


def _projection_source(
    case: FixtureCase,
    snapshot: GraphSnapshot,
) -> ProjectionSource:
    if case.evidence is None:
        return "malformed_replay"
    known_ids = graph_object_id_set(snapshot)
    properties = graph_entity_property_index(snapshot)
    evidence = case.evidence
    if not set(_evidence_ids(evidence)).issubset(known_ids):
        return "adversarial_replay"
    if any(
        not _edge_matches_snapshot(edge, snapshot)
        for edge in (*evidence.edges, *evidence.supporting_edges)
    ):
        return "adversarial_replay"
    if any(
        (fact.entity_id, fact.key.casefold(), fact.value) not in properties
        for fact in evidence.observed_properties
    ):
        return "adversarial_replay"
    return "graph_snapshot_replay"


def _raw_node(
    snapshot: GraphSnapshot,
    object_id: str,
    observed_properties: dict[tuple[str, str], Any],
) -> dict[str, Any]:
    item = _snapshot_objects(snapshot).get(object_id)
    if item is None:
        raise DirectAdapterError(
            f"fixture references object absent from graph snapshot: {object_id}"
        )
    properties = {fact.key: fact.value for fact in item.properties}
    for (entity_id, key), value in observed_properties.items():
        if entity_id == object_id:
            properties[key] = value
    properties["objectid"] = item.entity.object_id
    if item.entity.canonical_name is not None:
        properties["name"] = item.entity.canonical_name
    return {
        "objectId": item.entity.object_id,
        "label": item.entity.canonical_name or item.entity.object_id,
        "kind": item.entity.object_type,
        "properties": properties,
    }


def _direct_raw(
    task: TaskBundle,
    evidence: EvidenceIR,
    snapshot: GraphSnapshot,
) -> dict[str, Any]:
    if task.claim_kind in {"count", "absence"}:
        count = evidence.count if evidence.count is not None else 0
        return {
            "data": {
                "nodes": {},
                "edges": [],
                "literals": [{"key": "count", "value": count}],
            }
        }
    observed = {
        (fact.entity_id, fact.key): fact.value
        for fact in evidence.observed_properties
    }
    object_ids = _evidence_ids(evidence)
    nodes = {
        str(index): _raw_node(snapshot, object_id, observed)
        for index, object_id in enumerate(object_ids)
    }
    edges = [
        {
            "source_id": edge.source_id,
            "relationship": edge.relationship,
            "target_id": edge.target_id,
            "direction": edge.direction.value,
            "properties": {
                fact.key: fact.value for fact in edge.properties
            },
        }
        for edge in (*evidence.edges, *evidence.supporting_edges)
    ]
    return {"data": {"nodes": nodes, "edges": edges}}


def project_direct_fixture(
    *,
    task: TaskBundle,
    oracle: OracleBundle,
    case: FixtureCase,
    snapshot: GraphSnapshot,
) -> SurfaceProjection:
    """Replay one fixture through the real direct Evidence IR projector."""

    if case.answer_payload is None:
        raise ValueError("applicable direct fixture has no answer payload")
    resolver = graph_identity_resolver(snapshot)
    source = _projection_source(case, snapshot)
    if case.evidence is None:
        try:
            validate_and_normalize_evidence(
                case.answer_payload,
                answer_schema=task.answer_schema,
                resolver=resolver,
                task_id=task.task_id,
            )
        except EvidenceNormalizationError as exc:
            return SurfaceProjection(
                projection_source=source,
                execution_class=ExecutionClass.MODEL_FAILURE,
                outcome=SampleOutcomeCode.OUTPUT_INVALID,
                evidence=None,
                verdict=None,
                raw_source_digest=canonical_sha256(case.answer_payload),
                rejection_reason=str(exc),
            )
        raise ValueError("fixture declares no Evidence IR but its answer is valid")

    raw = _direct_raw(task, case.evidence, snapshot)
    assertion = {
        key: value
        for key, value in case.answer_payload.items()
        if key in DIRECT_ASSERTION_FIELDS
    }
    try:
        evidence = project_direct_evidence(
            CypherResult(success=True, raw=raw),
            task=task,
            oracle=oracle,
            resolver=resolver,
            answer_payload=assertion,
        )
    except DirectAdapterError as exc:
        return SurfaceProjection(
            projection_source=source,
            execution_class=ExecutionClass.MODEL_FAILURE,
            outcome=SampleOutcomeCode.OUTPUT_INVALID,
            evidence=None,
            verdict=None,
            raw_source_digest=canonical_sha256(raw),
            rejection_reason=str(exc),
        )
    verdict = compare(task.answer_policy, oracle, evidence)
    return SurfaceProjection(
        projection_source=source,
        execution_class=ExecutionClass.SUCCESS,
        outcome=SampleOutcomeCode.COMPLETED,
        evidence=evidence,
        verdict=verdict,
        raw_source_digest=canonical_sha256(raw),
    )


def _mcp_node_payload(snapshot: GraphSnapshot, evidence: EvidenceIR) -> dict[str, Any]:
    observed = {
        (fact.entity_id, fact.key): fact.value
        for fact in evidence.observed_properties
    }
    return {
        str(index): _raw_node(snapshot, object_id, observed)
        for index, object_id in enumerate(_evidence_ids(evidence))
    }


def _mcp_graph_payload(
    snapshot: GraphSnapshot,
    evidence: EvidenceIR,
) -> dict[str, Any]:
    nodes = _mcp_node_payload(snapshot, evidence)
    entity_ids = _evidence_ids(evidence)
    edges = [
        {
            "source": edge.source_id,
            "target": edge.target_id,
            "kind": edge.relationship,
            "properties": {
                fact.key: fact.value for fact in edge.properties
            },
        }
        for edge in (*evidence.edges, *evidence.supporting_edges)
    ]
    literals: list[dict[str, Any]] = []
    if entity_ids:
        literals.append({"key": "source_id", "value": entity_ids[0]})
        literals.append({"key": "target_id", "value": entity_ids[-1]})
    return {
        "success": True,
        "has_results": bool(nodes or edges),
        "data": {"nodes": nodes, "edges": edges, "literals": literals},
        "node_count": len(nodes),
        "edge_count": len(edges),
    }


def _mcp_entity_literal_payload(evidence: EvidenceIR) -> dict[str, Any]:
    """Mirror BloodHound MCP's flattened scalar-row response for set queries."""

    include_names = bool(evidence.entities) and all(
        entity.canonical_name is not None for entity in evidence.entities
    )
    literals: list[dict[str, Any]] = []
    for entity in evidence.entities:
        literals.append({"key": "object_id", "value": entity.object_id})
        if include_names:
            literals.append({"key": "name", "value": entity.canonical_name})
    return {
        "success": True,
        "has_results": bool(evidence.entities),
        "data": {"nodes": {}, "edges": [], "literals": literals},
        "node_count": 0,
        "edge_count": 0,
    }


def _cypher_literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _cypher_scalar(value: Any) -> str:
    if isinstance(value, str):
        return _cypher_literal(value)
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, tuple):
        return "[" + ", ".join(_cypher_scalar(item) for item in value) + "]"
    raise ValueError(f"unsupported public selection value: {value!r}")


def _selection_fixture_population(
    task: TaskBundle,
) -> tuple[list[str], str]:
    """Compile the exact public selection into bounded certification Cypher."""

    selection = task.acceptance_spec.selection
    if selection is None:
        raise ValueError(f"task {task.task_id} lacks a public selection")
    role_entities = {entity.role: entity for entity in task.input_entities}
    roles = tuple(
        dict.fromkeys(
            (
                *(anchor.role for anchor in selection.anchors),
                *(
                    role
                    for relationship in selection.relationships
                    for role in (
                        relationship.source_role,
                        relationship.target_role,
                    )
                ),
                *(predicate.role for predicate in selection.predicates),
                selection.projection_role,
            )
        )
    )
    variables = {role: f"role{index}" for index, role in enumerate(roles)}
    role_types: dict[str, str] = {}

    def register_type(role: str, object_type: str | None) -> None:
        if object_type is None or object_type.casefold() in {"any", "principal"}:
            return
        existing = role_types.get(role)
        if existing is not None and existing.casefold() != object_type.casefold():
            raise ValueError(
                f"task {task.task_id} assigns conflicting public types to role {role}"
            )
        role_types[role] = object_type

    for anchor in selection.anchors:
        register_type(anchor.role, anchor.object_type)
    for relationship in selection.relationships:
        register_type(relationship.source_role, relationship.source_type)
        register_type(relationship.target_role, relationship.target_type)
    register_type(selection.projection_role, selection.projection_type)

    def node(role: str, *, selector: str | None = None) -> str:
        label = f":{role_types[role]}" if role in role_types else ""
        properties = f" {{objectid: {_cypher_literal(selector)}}}" if selector else ""
        return f"({variables[role]}{label}{properties})"

    clauses: list[str] = []
    for anchor in selection.anchors:
        entity = role_entities.get(anchor.role)
        if entity is None:
            raise ValueError(
                f"task {task.task_id} has unresolved public role {anchor.role}"
            )
        clauses.append(f"MATCH {node(anchor.role, selector=entity.object_id)}")

    for relationship in selection.relationships:
        if relationship.min_hops == relationship.max_hops == 1:
            hop_range = ""
        else:
            hop_range = f"*{relationship.min_hops}..{relationship.max_hops}"
        edge = f"[:{relationship.relationship}{hop_range}]"
        source = node(relationship.source_role)
        target = node(relationship.target_role)
        if relationship.direction.value == "outbound":
            clauses.append(f"MATCH {source}-{edge}->{target}")
        else:
            clauses.append(f"MATCH {source}<-{edge}-{target}")

    declared_roles = {
        anchor.role for anchor in selection.anchors
    } | {
        role
        for relationship in selection.relationships
        for role in (relationship.source_role, relationship.target_role)
    }
    if selection.projection_role not in declared_roles:
        clauses.append(f"MATCH {node(selection.projection_role)}")

    predicates: list[str] = []
    for predicate in selection.predicates:
        lhs = f"{variables[predicate.role]}.{predicate.property_name}"
        operator = predicate.operator.value
        if operator == "exists":
            predicates.append(f"{lhs} IS NOT NULL")
        elif operator == "not_exists":
            predicates.append(f"{lhs} IS NULL")
        elif operator == "equals":
            if predicate.value is None:
                predicates.append(f"{lhs} IS NULL")
            else:
                predicates.append(f"{lhs} = {_cypher_scalar(predicate.value)}")
        elif operator == "not_equals":
            if predicate.value is None:
                predicates.append(f"{lhs} IS NOT NULL")
            else:
                predicates.append(f"{lhs} <> {_cypher_scalar(predicate.value)}")
        elif operator == "in":
            predicates.append(f"{lhs} IN {_cypher_scalar(predicate.value)}")
        elif operator == "not_in":
            predicates.append(f"{lhs} NOT IN {_cypher_scalar(predicate.value)}")
        else:
            raise ValueError(
                f"task {task.task_id} has unsupported predicate {operator}"
            )
    if predicates:
        clauses.append("WHERE " + " AND ".join(predicates))
    return clauses, variables[selection.projection_role]


def _mcp_fixture_query(
    task: TaskBundle,
    *,
    count: bool,
    offset: int = 0,
    limit: int | None = None,
) -> str:
    """Build a bounded replay query using only the public MCP evidence contract."""

    contract = task.binding.mcp_evidence_contract
    if contract is None:
        raise ValueError(f"MCP task {task.task_id} lacks an evidence contract")
    role_entities = {entity.role: entity for entity in task.input_entities}
    bound_entities = [
        role_entities[role]
        for role in contract.required_input_roles
        if role in role_entities
    ]
    if len(bound_entities) != len(contract.required_input_roles):
        raise ValueError(f"MCP task {task.task_id} has unresolved public input roles")
    if task.claim_kind == "absence":
        if len(bound_entities) != 2 or not task.acceptance_spec.allowed_mechanisms:
            raise ValueError(
                f"MCP absence task {task.task_id} lacks two endpoints or mechanisms"
            )
        relationship_types = "|".join(task.acceptance_spec.allowed_mechanisms)
        return (
            "MATCH p="
            f"(input0 {{objectid: {_cypher_literal(bound_entities[0].object_id)}}})"
            f"-[:{relationship_types}*1..{max(1, task.binding.bounds.max_hops)}]->"
            f"(input1 {{objectid: {_cypher_literal(bound_entities[1].object_id)}}}) "
            "RETURN count(p) AS certified_count"
        )

    if task.claim_kind in {"set", "count"} and task.acceptance_spec.selection is not None:
        clauses, result_expression = _selection_fixture_population(task)
        if count:
            clauses.append(
                f"RETURN count(DISTINCT {result_expression}) AS certified_count"
            )
        else:
            clauses.append(
                f"WITH DISTINCT {result_expression} "
                f"ORDER BY {result_expression}.objectid "
                f"RETURN {result_expression}.objectid AS object_id, "
                f"{result_expression}.name AS name "
                f"SKIP {offset} LIMIT {limit or task.binding.bounds.page_size}"
            )
        return " ".join(clauses)

    clauses = []
    for index, entity in enumerate(bound_entities):
        if entity.canonical_name:
            selector = (
                "name: TOUPPER("
                f"{_cypher_literal(entity.canonical_name)}"
                ")"
            )
        else:
            selector = f"objectid: {_cypher_literal(entity.object_id)}"
        clauses.append(f"MATCH (input{index} {{{selector}}})")
    projection_type = ""
    if (
        contract.projection_types
        and contract.projection_types[0].casefold() not in {"any", "principal"}
    ):
        projection_type = f":{contract.projection_types[0]}"
    max_hops = max(1, task.binding.bounds.max_hops)
    relationship = f"[*1..{max_hops}]"

    if task.claim_kind in {"route", "absence", "decision"} and len(bound_entities) >= 2:
        clauses.append(f"MATCH p=(input0)-{relationship}->(input1)")
        result_expression = "p"
    elif bound_entities and contract.result_kind == "entities":
        clauses.append(f"MATCH p=(input0)-{relationship}-(result{projection_type})")
        result_expression = "result"
    elif bound_entities and not contract.projection_types:
        result_expression = "input0"
    else:
        clauses.append(f"MATCH (result{projection_type})")
        result_expression = "result"

    if count:
        count_expression = (
            f"DISTINCT {result_expression}"
            if task.claim_kind == "set"
            else result_expression
        )
        clauses.append(f"RETURN count({count_expression}) AS certified_count")
    else:
        if task.claim_kind == "set":
            clauses.append(
                f"WITH DISTINCT {result_expression} "
                f"ORDER BY {result_expression}.objectid "
                f"RETURN {result_expression}.objectid AS object_id, "
                f"{result_expression}.name AS name "
                f"SKIP {offset} LIMIT {limit or task.binding.bounds.page_size}"
            )
        else:
            if (
                task.claim_kind in {"route", "decision"}
                and len(bound_entities) >= 2
            ):
                clauses.append(
                    f"RETURN {result_expression}, "
                    "input0.objectid AS source_id, "
                    "input1.objectid AS target_id"
                )
            else:
                clauses.append(f"RETURN {result_expression}")
            clauses.append("LIMIT 1")
    return " ".join(clauses)


def _observe_mcp_unlock(
    projector: MCPTranscriptProjector,
    *,
    task: TaskBundle,
    snapshot: GraphSnapshot,
    perfect_evidence: EvidenceIR,
) -> tuple[dict[str, Any], ...]:
    calls: list[dict[str, Any]] = []

    def observe(
        tool_name: str,
        arguments: dict[str, Any],
        payload: dict[str, Any],
    ) -> None:
        projector.observe(
            tool_name,
            arguments,
            json.dumps(payload, sort_keys=True),
            None,
        )
        calls.append(
            {
                "tool_name": tool_name,
                "arguments": arguments,
                "payload": payload,
            }
        )

    if task.claim_kind == "set":
        total = len(perfect_evidence.entities)
        if task.binding.bounds.require_total_count:
            observe(
                "cypher_query",
                {
                    "info_type": "run",
                    "query": _mcp_fixture_query(task, count=True),
                },
                {
                    "success": True,
                    "data": {
                        "literals": [
                            {"key": "certified_count", "value": total},
                        ]
                    },
                    "node_count": 0,
                    "edge_count": 0,
                },
            )
        page_size = task.binding.bounds.page_size
        entities = perfect_evidence.entities
        offsets = (
            range(
                task.binding.bounds.result_offset,
                task.binding.bounds.result_offset + max(total, 1),
                page_size,
            )
            if task.binding.bounds.require_total_count
            else (task.binding.bounds.result_offset,)
        )
        for page_index, offset in enumerate(offsets):
            # The oracle's perfect evidence is already the claim-selected
            # window. Query offsets are absolute graph offsets, while slices
            # here are intentionally relative to that selected window.
            start_in_selected_window = page_index * page_size
            page_ids = {
                entity.object_id
                for entity in entities[
                    start_in_selected_window : start_in_selected_window + page_size
                ]
            }
            page_evidence = perfect_evidence.model_copy(
                update={
                    "entities": tuple(
                        entity
                        for entity in perfect_evidence.entities
                        if entity.object_id in page_ids
                    )
                }
            )
            observe(
                "cypher_query",
                {
                    "info_type": "run",
                    "query": _mcp_fixture_query(
                        task,
                        count=False,
                        offset=offset,
                        limit=page_size,
                    ),
                },
                _mcp_entity_literal_payload(page_evidence),
            )
    elif task.claim_kind in {"count", "absence"}:
        count = perfect_evidence.count
        if count is None:
            count = 0
        observe(
            "cypher_query",
            {
                "info_type": "run",
                "query": _mcp_fixture_query(task, count=True),
            },
            {
                "success": True,
                "data": {
                    "literals": [
                        {"key": "certified_count", "value": count},
                    ]
                },
                "node_count": 0,
                "edge_count": 0,
            },
        )
    else:
        observe(
            "cypher_query",
            {
                "info_type": "run",
                "query": _mcp_fixture_query(task, count=False),
            },
            _mcp_graph_payload(snapshot, perfect_evidence),
        )
    if not projector.finalization_ready:
        raise ValueError(
            f"canonical MCP replay did not unlock {task.task_id} finalization"
        )
    return tuple(calls)


def project_mcp_fixture(
    *,
    task: TaskBundle,
    oracle: OracleBundle,
    case: FixtureCase,
    perfect_evidence: EvidenceIR,
    profile: CapabilityProfile,
    snapshot: GraphSnapshot,
) -> SurfaceProjection:
    """Replay one fixture through the real MCP projector and finalizer."""

    if case.answer_payload is None:
        raise ValueError("applicable MCP fixture has no answer payload")
    resolver = graph_identity_resolver(snapshot)
    projector = MCPTranscriptProjector(task, profile)
    calls = _observe_mcp_unlock(
        projector,
        task=task,
        snapshot=snapshot,
        perfect_evidence=perfect_evidence,
    )
    outcome = score_mcp_transcript_v2(
        task=task,
        oracle=oracle,
        resolver=resolver,
        profile=profile,
        tool_loop=MCPToolLoop(task.binding.mcp_tool_loop),
        events=projector.events,
        final_answer=case.answer_payload,
        observed_identity_ids=tuple(projector.observed_identity_ids),
        graph_fact_registry=build_graph_fact_registry(snapshot),
    )
    return SurfaceProjection(
        projection_source=_projection_source(case, snapshot),
        execution_class=outcome.sample.execution_class,
        outcome=outcome.sample.outcome,
        evidence=outcome.sample.evidence,
        verdict=outcome.sample.verdict,
        raw_source_digest=canonical_sha256(calls),
        rejection_reason=(
            outcome.sample.detail
            if outcome.sample.evidence is None
            else None
        ),
    )


def project_native_fixture(
    *, task: TaskBundle, oracle: OracleBundle, case: FixtureCase,
    perfect_evidence: EvidenceIR, profile, snapshot: GraphSnapshot,
) -> SurfaceProjection:
    """Offline native-envelope replay through the production proof and scorer.

    The perfect fixture supplies synthetic backend data, never a live receipt.
    Production native projectors still receive only that envelope and the public
    task. Unsupported shapes cannot borrow the historical CE execution wrapper.
    """
    from .native_mcp_projection import project_native_result
    from .native_proof import classify_native_result, validate_native_task_binding

    validate_native_task_binding(profile, task)
    if case.answer_payload is None:
        raise ValueError("applicable native fixture has no answer payload")
    alternative = task.binding.mcp_evidence_contract.alternatives[0]
    if (task.claim_kind in {"count", "absence"}
            and profile.implementation_id in {"mwnickerson", "mordavid"}):
        if task.claim_kind == "absence" and perfect_evidence.path_status is not PathStatus.NO_PATH:
            raise ValueError("native absence fixture lacks a bounded no-path verdict")
        count = 0 if task.claim_kind == "absence" else perfect_evidence.count
        if count is None:
            raise ValueError("native count fixture lacks a mechanical count")
        arguments = {"query": _mcp_fixture_query(task, count=True)}
        if profile.implementation_id == "mordavid":
            payload = {"success": True, "data": [{"certified_count": count}]}
        else:
            arguments["info_type"] = "run"
            payload = {
                "success": True, "info_type": "run", "has_results": True,
                "node_count": 0, "edge_count": 0,
                "data": {"nodes": {}, "edges": [], "literals": [
                    {"key": "certified_count", "value": count},
                ]},
            }
    elif task.claim_kind == "route" and profile.implementation_id == "mwnickerson":
        arguments = {"info_type": "run", "query": _mcp_fixture_query(task, count=False)}
        nodes = _mcp_node_payload(snapshot, perfect_evidence)
        key_by_id = {node["objectId"]: key for key, node in nodes.items()}
        edges = []
        for edge in (*perfect_evidence.edges, *perfect_evidence.supporting_edges):
            start, end = edge.source_id, edge.target_id
            if edge.direction is EdgeDirection.INBOUND:
                start, end = end, start
            elif edge.direction is not EdgeDirection.OUTBOUND:
                raise ValueError("NATIVE_PROOF_UNSUPPORTED: fixture edge lacks direction")
            edges.append({"source": key_by_id[start], "target": key_by_id[end],
                          "kind": edge.relationship,
                          "properties": {fact.key: fact.value for fact in edge.properties}})
        payload = {"success": True, "info_type": "run", "has_results": True,
                   "node_count": len(nodes), "edge_count": len(edges),
                   "data": {"nodes": nodes, "edges": edges, "literals": []}}
    elif task.claim_kind == "route" and profile.implementation_id == "armadin":
        public = {entity.role: entity for entity in task.input_entities}
        source = public[task.acceptance_spec.source_role]
        target = public[task.acceptance_spec.target_role]
        if not source.canonical_name or not target.canonical_name:
            raise ValueError("NATIVE_PROOF_UNSUPPORTED: Armadin path requires native names")
        arguments = {"source": source.canonical_name, "target": target.canonical_name}
        # Armadin's directed shortest-path query serializes ordered nodes and
        # relationship types, not arbitrary supporting edges or property maps.
        ordered_ids = [source.object_id]
        edges = []
        for edge in perfect_evidence.edges:
            start, end = edge.source_id, edge.target_id
            if edge.direction is EdgeDirection.INBOUND:
                start, end = end, start
            elif edge.direction is not EdgeDirection.OUTBOUND:
                raise ValueError("NATIVE_PROOF_UNSUPPORTED: fixture edge lacks direction")
            if start != ordered_ids[-1] or end in ordered_ids:
                raise ValueError("NATIVE_PROOF_UNSUPPORTED: fixture is not a simple directed path")
            ordered_ids.append(end)
            edges.append({"type": edge.relationship})
        if ordered_ids[-1] != target.object_id or not edges:
            raise ValueError("NATIVE_PROOF_UNSUPPORTED: fixture path misses public target")
        payload = {"success": True, "path_found": True, **arguments,
                   "path_length": len(edges), "nodes": [
                       {"objectid": object_id, "name": snapshot.entity(object_id).canonical_name,
                        "type": snapshot.entity(object_id).object_type}
                       for object_id in ordered_ids
                   ], "edges": edges}
    elif profile.implementation_id == "armadin" and alternative.tool_name == "find_domains":
        arguments = {}
        payload = {"success": True, "domains": [
            {"objectid": entity.object_id, "name": entity.canonical_name,
             "domain": entity.domain}
            for entity in perfect_evidence.entities
        ], "count": len(perfect_evidence.entities)}
    else:
        raise ValueError("NATIVE_PROOF_UNSUPPORTED: native fixture shape is not implemented")
    result = {"isError": False, "content": [{"type": "text", "text": json.dumps(payload)}]}
    observation = project_native_result(
        profile.implementation_id, alternative.tool_name, arguments, result, task,
    )
    event = classify_native_result(profile, task, alternative.tool_name, arguments, result)
    if observation.evidence is None or not event.unlocks_finalization:
        raise ValueError("NATIVE_PROOF_UNSUPPORTED: perfect native fixture cannot establish proof")
    if case.name == "perfect":
        # Exercise native proof rejection independently of incorrect final
        # answers. A correct comparator must not mask an over-permissive adapter.
        negative_payloads = [
            {"success": True, "unrecognized": payload},
            {**payload, "truncated": True},
            {"success": False, "error": "synthetic fixture failure"},
        ]
        if task.claim_kind == "route":
            node_only = deepcopy(payload)
            missing_identity = deepcopy(payload)
            if profile.implementation_id == "mwnickerson":
                node_only["data"]["edges"] = []
                node_only["edge_count"] = 0
                for node in missing_identity["data"]["nodes"].values():
                    node.pop("objectId", None)
            else:
                node_only["edges"] = []
                for node in missing_identity["nodes"]:
                    node.pop("objectid", None)
                negative_payloads.append({"success": True, "path_found": False})
            negative_payloads.extend((node_only, missing_identity))
        for negative in negative_payloads:
            negative_result = {"isError": False, "content": [
                {"type": "text", "text": json.dumps(negative)},
            ]}
            rejected = classify_native_result(
                profile, task, alternative.tool_name, arguments, negative_result,
            )
            if rejected.unlocks_finalization:
                raise ValueError("native adversarial fixture incorrectly establishes proof")
        wrong_arguments = deepcopy(arguments)
        if "query" in wrong_arguments:
            wrong_arguments["query"] = "RETURN 1 AS certified_count"
        else:
            wrong_arguments["unsupported_filter"] = "fixture"
        if classify_native_result(
            profile, task, alternative.tool_name, wrong_arguments, result,
        ).unlocks_finalization:
            raise ValueError("native unrelated-scope fixture incorrectly establishes proof")
    outcome = score_mcp_transcript_v2(
        task=task, oracle=oracle, resolver=graph_identity_resolver(snapshot), profile=profile,
        tool_loop=task.binding.mcp_tool_loop, events=(event,), final_answer=case.answer_payload,
        observed_identity_ids=tuple(entity.object_id for entity in observation.evidence.entities),
        graph_fact_registry=build_graph_fact_registry(snapshot), certified=False,
    )
    return SurfaceProjection(
        projection_source=_projection_source(case, snapshot),
        execution_class=outcome.sample.execution_class, outcome=outcome.sample.outcome,
        evidence=outcome.sample.evidence, verdict=outcome.sample.verdict,
        raw_source_digest=canonical_sha256({"arguments": arguments, "result": result}),
        rejection_reason=outcome.sample.detail if outcome.sample.evidence is None else None,
    )
