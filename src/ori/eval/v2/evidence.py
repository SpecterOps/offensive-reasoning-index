"""Normalization of direct, MCP, and offline answers into v2 Evidence IR."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Iterable, Mapping
from enum import Enum
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from ori.relationships import canonical_relationship_kind

from .graph import (
    NORMALIZED_EDGE_METADATA_KEYS,
    NORMALIZED_ENTITY_METADATA_KEYS,
    edge_fact_key,
    edge_property_fact_key,
    entity_property_fact_key,
)
from .identity import (
    AmbiguousIdentityError,
    IdentityResolutionError,
    IdentityResolver,
    UnknownIdentityError,
)
from .schema import (
    EdgeDirection,
    EdgeWitness,
    EntityPropertyFact,
    EntityRef,
    EvidenceIR,
    GraphFactRegistry,
    NegativeReasonCode,
    PathStatus,
    PropertyFact,
)


class EvidenceNormalizationError(ValueError):
    """Raised when structured answer evidence violates the public contract."""


class EvidenceIdentityCatalogError(EvidenceNormalizationError):
    """Raised when otherwise structured evidence cannot bind to the graph catalog.

    This remains distinct from malformed structured output so runtimes can
    classify a live identity missing from the sealed catalog as a harness or
    certification defect instead of blaming the model's JSON shape.
    """

    def __init__(
        self,
        *,
        token: str,
        identity_error: IdentityResolutionError,
    ) -> None:
        self.token = token
        self.identity_error = identity_error
        self.code = (
            "IDENTITY_NOT_IN_CATALOG"
            if isinstance(identity_error, UnknownIdentityError)
            else "IDENTITY_AMBIGUOUS"
        )
        super().__init__(f"{self.code}: {identity_error}")


_GRAPH_FACT_LOOKUP_CACHE: dict[
    str,
    tuple[frozenset[str], frozenset[str], frozenset[str]],
] = {}


FORBIDDEN_ORACLE_FIELDS = frozenset(
    {
        "correct",
        "expected_answer",
        "expected_count",
        "expected_decision",
        "expected_entities",
        "expected_entity_ids",
        "forbidden_edges",
        "forbidden_entity_ids",
        "graph_fingerprint",
        "graph_edge_registry",
        "graph_fact_attestation",
        "graph_fact_registry",
        "graph_fact_registry_fingerprint",
        "negative_witnesses",
        "oracle_bundle",
        "oracle_fingerprint",
        "reference_cypher",
        "reference_nodes",
        "reference_results",
        "ref_result",
        "route_variants",
        "required_context",
        "required_mechanisms",
        "required_properties",
        "valid_node_names",
    }
)


def attest_graph_facts(
    evidence: EvidenceIR,
    registry: GraphFactRegistry,
) -> EvidenceIR:
    """Attest evidence only when every asserted graph fact is in the sealed graph."""

    lookups = _GRAPH_FACT_LOOKUP_CACHE.get(registry.registry_fingerprint)
    if lookups is None:
        lookups = (
            frozenset(registry.edge_keys),
            frozenset(registry.edge_property_facts),
            frozenset(registry.entity_property_facts),
        )
        _GRAPH_FACT_LOOKUP_CACHE[registry.registry_fingerprint] = lookups
    edge_keys, edge_property_facts, entity_property_facts = lookups

    for edge in (*evidence.edges, *evidence.supporting_edges):
        if edge_fact_key(edge) not in edge_keys:
            return evidence.model_copy(update={"graph_fact_attestation": None})
        if any(
            edge_property_fact_key(edge, fact)
            not in edge_property_facts
            for fact in edge.properties
        ):
            return evidence.model_copy(update={"graph_fact_attestation": None})

    if any(
        entity_property_fact_key(fact.entity_id, fact.key, fact.value)
        not in entity_property_facts
        for fact in evidence.observed_properties
    ):
        return evidence.model_copy(update={"graph_fact_attestation": None})

    return evidence.model_copy(
        update={"graph_fact_attestation": registry.registry_fingerprint}
    )

_KNOWN_TOP_LEVEL_FIELDS = frozenset(
    {
        "answer",
        "count",
        "decision",
        "edges",
        "entities",
        "entity_ids",
        "evidence",
        "explanation",
        "no_path",
        "node_names",
        "nodes",
        "path",
        "path_edges",
        "path_status",
        "raw_digest",
        "rejected_decoy_ids",
        "relationships",
        "route",
        "source",
        "status",
        "supporting_edges",
        "supporting_evidence",
        "task_id",
        "truncated",
        "normalization_warnings",
        "negative_reason_codes",
        "observed_properties",
    }
)


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="python"))
    if dataclasses.is_dataclass(value):
        return _jsonable(dataclasses.asdict(value))
    if isinstance(value, Enum):
        return _jsonable(value.value)
    if isinstance(value, Mapping):
        return {
            str(key): _jsonable(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (set, frozenset)):
        converted = [_jsonable(item) for item in value]
        return sorted(converted, key=lambda item: json.dumps(item, sort_keys=True))
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise EvidenceNormalizationError(
        f"Evidence contains a non-canonical value of type {type(value).__name__}"
    )


def raw_payload_digest(payload: Any) -> str:
    """Hash one answer payload using deterministic JSON serialization."""

    encoded = json.dumps(
        _jsonable(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def diagnostic_payload_digest(payload: Any) -> str:
    """Hash untrusted malformed output without asserting canonical JSON."""

    return hashlib.sha256(
        repr(payload).encode("utf-8", errors="backslashreplace")
    ).hexdigest()


def _assert_no_oracle_fields(value: Any, *, path: str = "$") -> None:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="python")
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized_key = str(key).strip().casefold()
            if normalized_key in FORBIDDEN_ORACLE_FIELDS:
                raise EvidenceNormalizationError(
                    f"Solver answer contains forbidden oracle field at {path}.{key}"
                )
            _assert_no_oracle_fields(item, path=f"{path}.{key}")
    elif isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
        for index, item in enumerate(value):
            _assert_no_oracle_fields(item, path=f"{path}[{index}]")


def _sequence(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes, Mapping)):
        return (value,)
    if isinstance(value, Iterable):
        return tuple(value)
    return (value,)


def _value(value: Any, *names: str, default: Any = None) -> Any:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="python")
    if isinstance(value, Mapping):
        for name in names:
            if name in value:
                return value[name]
    return default


def _identity_token(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="python")
    if isinstance(value, Mapping):
        direct = _value(
            value,
            "object_id",
            "objectid",
            "objectId",
            "entity_id",
            "id",
            "canonical_name",
            "name",
            "label",
        )
        if direct is None:
            properties = _value(value, "properties", "props", default={})
            direct = _value(
                properties,
                "object_id",
                "objectid",
                "objectId",
                "name",
                "Name",
                "label",
            )
        if direct is not None:
            return str(direct).strip()
    raise EvidenceNormalizationError(f"Cannot extract graph identity from {value!r}")


def _resolve_identity(value: Any, resolver: IdentityResolver | None) -> str:
    token = _identity_token(value)
    if resolver is None:
        return token
    try:
        return resolver.resolve(token)
    except (UnknownIdentityError, AmbiguousIdentityError) as exc:
        raise EvidenceIdentityCatalogError(
            token=token,
            identity_error=exc,
        ) from exc


def _resolve_entity(value: Any, resolver: IdentityResolver | None) -> EntityRef:
    if isinstance(value, EntityRef):
        if resolver is None:
            return value
        token = value.object_id
        try:
            return resolver.entity_for(token)
        except (UnknownIdentityError, AmbiguousIdentityError) as exc:
            raise EvidenceIdentityCatalogError(
                token=token,
                identity_error=exc,
            ) from exc
    if resolver is not None:
        token = _identity_token(value)
        try:
            return resolver.entity_for(token)
        except (UnknownIdentityError, AmbiguousIdentityError) as exc:
            raise EvidenceIdentityCatalogError(
                token=token,
                identity_error=exc,
            ) from exc
    if isinstance(value, Mapping):
        try:
            return EntityRef(**value)
        except (TypeError, ValueError) as exc:
            raise EvidenceNormalizationError(
                "Entity evidence without an identity resolver must contain a complete EntityRef"
            ) from exc
    raise EvidenceNormalizationError(
        "Entity aliases require the sealed task identity resolver"
    )


def _canonical_relationship(value: Any) -> str:
    relationship = str(value).strip()
    if not relationship:
        raise EvidenceNormalizationError("Edge witness is missing a relationship")
    try:
        return canonical_relationship_kind(relationship)
    except ValueError:
        # Unknown relationships remain evidence and will fail comparator policy
        # checks. Normalization must not turn a model error into infrastructure.
        return relationship


def _make_edge(value: Any, resolver: IdentityResolver | None) -> EdgeWitness:
    if isinstance(value, EdgeWitness):
        source = _resolve_identity(
            _value(value, "source_id", "source", "source_object_id"), resolver
        )
        target = _resolve_identity(
            _value(value, "target_id", "target", "target_object_id"), resolver
        )
        relationship = _canonical_relationship(
            _value(value, "relationship", "edge", "kind", "relationship_kind")
        )
        direction = _value(value, "direction", default=EdgeDirection.OUTBOUND)
        properties = _value(value, "properties", default={})
    elif isinstance(value, Mapping):
        source = _resolve_identity(
            _value(value, "source_id", "source", "source_object_id", "start"), resolver
        )
        target = _resolve_identity(
            _value(value, "target_id", "target", "target_object_id", "end"), resolver
        )
        relationship = _canonical_relationship(
            _value(value, "relationship", "edge", "kind", "type", "relationship_kind")
        )
        direction = _value(value, "direction", default=EdgeDirection.OUTBOUND)
        properties = _value(value, "properties", "props", default={})
    elif isinstance(value, (list, tuple)) and len(value) == 3:
        source = _resolve_identity(value[0], resolver)
        relationship = _canonical_relationship(value[1])
        target = _resolve_identity(value[2], resolver)
        direction = EdgeDirection.OUTBOUND
        properties = ()
    else:
        raise EvidenceNormalizationError(f"Invalid edge witness: {value!r}")

    if not isinstance(direction, EdgeDirection):
        try:
            direction = EdgeDirection(str(direction).strip().casefold())
        except ValueError as exc:
            raise EvidenceNormalizationError(
                f"Invalid edge direction: {direction!r}"
            ) from exc

    if isinstance(properties, Mapping):
        property_facts = tuple(
            PropertyFact(key=str(key), value=item)
            for key, item in sorted(properties.items(), key=lambda pair: str(pair[0]))
            if str(key).casefold() not in NORMALIZED_EDGE_METADATA_KEYS
        )
    elif isinstance(properties, Iterable) and not isinstance(properties, (str, bytes)):
        property_facts = tuple(
            item if isinstance(item, PropertyFact) else PropertyFact(**item)
            for item in properties
            if str(
                item.key
                if isinstance(item, PropertyFact)
                else item.get("key", "")
            ).casefold()
            not in NORMALIZED_EDGE_METADATA_KEYS
        )
    else:
        raise EvidenceNormalizationError("Edge properties must be a mapping or PropertyFact list")

    return EdgeWitness(
        source_id=source,
        relationship=relationship,
        target_id=target,
        direction=direction,
        properties=property_facts,
    )


def _make_entity_property(
    value: Any,
    resolver: IdentityResolver | None,
) -> EntityPropertyFact:
    if isinstance(value, EntityPropertyFact):
        entity_id = _resolve_identity(value.entity_id, resolver)
        key = value.key
        fact_value = value.value
    elif isinstance(value, Mapping):
        entity_id = _resolve_identity(
            _value(value, "entity_id", "object_id", "entity", "subject"),
            resolver,
        )
        key = _value(value, "key", "property")
        fact_value = _value(value, "value")
    else:
        raise EvidenceNormalizationError(f"Invalid entity property fact: {value!r}")

    if not isinstance(key, str) or not key:
        raise EvidenceNormalizationError("Entity property fact requires a non-empty key")
    return EntityPropertyFact(entity_id=entity_id, key=key, value=fact_value)


def _entity_values(payload: Mapping[str, Any]) -> tuple[Any, ...]:
    raw = _value(
        payload,
        "entity_ids",
        "entities",
        "node_names",
        "nodes",
        default=(),
    )
    return _sequence(raw)


def _edge_values(payload: Mapping[str, Any]) -> tuple[Any, ...]:
    raw = _value(
        payload,
        "edges",
        "path_edges",
        "route",
        "relationships",
        "path",
        default=(),
    )
    if isinstance(raw, Mapping):
        raw = _value(raw, "edges", "path_edges", default=raw)
    return _sequence(raw)


def normalize_evidence(
    payload: Mapping[str, Any] | EvidenceIR,
    *,
    resolver: IdentityResolver | None = None,
    task_id: str | None = None,
    strict: bool = True,
) -> EvidenceIR:
    """Normalize a structured answer into the shared Evidence IR.

    ``strict=False`` is reserved for trusted adapters that first project
    provider-specific records into the public answer shape.
    """

    _assert_no_oracle_fields(payload)
    if isinstance(payload, EvidenceIR):
        raw: Mapping[str, Any] = payload.model_dump(mode="python")
    elif isinstance(payload, Mapping):
        raw = payload
    else:
        raise EvidenceNormalizationError("Structured evidence must be a mapping")

    if strict:
        unknown = {str(key) for key in raw} - _KNOWN_TOP_LEVEL_FIELDS
        if unknown:
            raise EvidenceNormalizationError(
                f"Structured evidence contains unknown fields: {sorted(unknown)}"
            )

    resolved_task_id = task_id or _value(raw, "task_id", default=None)
    if not isinstance(resolved_task_id, str) or not resolved_task_id:
        raise EvidenceNormalizationError("Evidence requires a task_id")

    entities = tuple(_resolve_entity(item, resolver) for item in _entity_values(raw))
    edges = tuple(_make_edge(item, resolver) for item in _edge_values(raw))
    supporting = tuple(
        _make_edge(item, resolver)
        for item in _sequence(
            _value(
                raw,
                "supporting_edges",
                "supporting_evidence",
                default=(),
            )
        )
    )
    observed_properties = tuple(
        fact
        for item in _sequence(_value(raw, "observed_properties", default=()))
        if (
            fact := _make_entity_property(item, resolver)
        ).key.casefold()
        not in NORMALIZED_ENTITY_METADATA_KEYS
    )
    observed_property_keys = {
        (fact.entity_id.casefold(), fact.key.casefold(), repr(fact.value))
        for fact in observed_properties
    }
    if len(observed_property_keys) != len(observed_properties):
        raise EvidenceNormalizationError("Observed entity property facts must be unique")

    negative_reason_codes = tuple(
        item
        if isinstance(item, NegativeReasonCode)
        else NegativeReasonCode(str(item).strip().casefold())
        for item in _sequence(_value(raw, "negative_reason_codes", default=()))
    )
    if len(set(negative_reason_codes)) != len(negative_reason_codes):
        raise EvidenceNormalizationError("Negative reason codes must be unique")

    path_status = _value(raw, "path_status", "status", default=None)
    no_path = _value(raw, "no_path", default=False)
    if not isinstance(no_path, bool):
        raise EvidenceNormalizationError("no_path must be a boolean")
    if no_path:
        path_status = PathStatus.NO_PATH
    elif path_status is None and edges:
        path_status = PathStatus.FOUND
    elif path_status is None:
        path_status = PathStatus.UNKNOWN
    elif not isinstance(path_status, PathStatus):
        try:
            normalized_status = str(path_status).strip().casefold()
            if normalized_status == "path":
                normalized_status = PathStatus.FOUND.value
            path_status = PathStatus(normalized_status)
        except ValueError as exc:
            raise EvidenceNormalizationError(
                f"Invalid path status: {path_status!r}"
            ) from exc

    rejected_decoy_ids = tuple(
        _resolve_identity(item, resolver)
        for item in _sequence(_value(raw, "rejected_decoy_ids", default=()))
    )

    truncated = _value(raw, "truncated", default=False)
    if not isinstance(truncated, bool):
        raise EvidenceNormalizationError("truncated must be a boolean")
    warnings = tuple(
        _sequence(_value(raw, "normalization_warnings", default=()))
    )
    if any(not isinstance(item, str) or not item for item in warnings):
        raise EvidenceNormalizationError(
            "normalization_warnings must contain non-empty strings"
        )

    return EvidenceIR(
        task_id=resolved_task_id,
        entities=entities,
        edges=edges,
        count=_value(raw, "count", default=None),
        decision=_value(raw, "decision", default=None),
        path_status=path_status,
        supporting_edges=supporting,
        observed_properties=observed_properties,
        negative_reason_codes=negative_reason_codes,
        rejected_decoy_ids=rejected_decoy_ids,
        truncated=truncated,
        raw_digest=(
            payload.raw_digest
            if isinstance(payload, EvidenceIR)
            else raw_payload_digest(payload)
        ),
        normalization_warnings=warnings,
    )


def normalize_direct_evidence(
    payload: Mapping[str, Any] | EvidenceIR,
    *,
    resolver: IdentityResolver | None = None,
    task_id: str | None = None,
) -> EvidenceIR:
    return normalize_evidence(
        payload,
        resolver=resolver,
        task_id=task_id,
    )


def normalize_mcp_evidence(
    payload: Mapping[str, Any] | EvidenceIR,
    *,
    resolver: IdentityResolver | None = None,
    task_id: str | None = None,
) -> EvidenceIR:
    return normalize_evidence(
        payload,
        resolver=resolver,
        task_id=task_id,
    )


def normalize_offline_evidence(
    payload: Mapping[str, Any] | EvidenceIR,
    *,
    resolver: IdentityResolver | None = None,
    task_id: str | None = None,
) -> EvidenceIR:
    return normalize_evidence(
        payload,
        resolver=resolver,
        task_id=task_id,
    )


def validate_and_normalize_evidence(
    payload: Mapping[str, Any],
    *,
    answer_schema: Mapping[str, Any],
    resolver: IdentityResolver | None = None,
    task_id: str,
) -> EvidenceIR:
    """Apply the one public-schema and Evidence IR boundary used by v2 scoring."""

    try:
        schema = dict(answer_schema)
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(dict(payload))
        return normalize_evidence(
            payload,
            resolver=resolver,
            task_id=task_id,
        )
    except EvidenceNormalizationError:
        raise
    except (SchemaError, ValidationError, ValueError) as exc:
        raise EvidenceNormalizationError(str(exc)) from exc
