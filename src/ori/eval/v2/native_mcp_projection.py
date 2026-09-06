"""Source-specific observations, never certification or claim-completeness proof.

Armadin shapes: 6ad4a4703d1117c3019400539911ca689a537197,
tools/active_directory/{attack_path_tools,domain_tools}.py.
MorDavid envelope: 1eb21b01da14fd2eda941234e3a545e876bef296,
BloodHound-MCP.py. Its Record.data() paths lose direction and are not projected.
No scorer oracle, graph snapshot, identity resolver, or finalizer is consulted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from pydantic import ValidationError

from ori.relationships import canonical_relationship_kind

from .fingerprint import canonical_sha256
from .graph import _LIVE_OBJECT_TYPES, _V2_CE_RELATIONSHIPS, _object_properties
from .native_bolt_graph import _edge_properties, _json_value
from .native_mcp_profiles import get_native_implementation
from .schema import EdgeWitness, EntityPropertyFact, EntityRef, EvidenceIR, PathStatus, TaskBundle


@dataclass(frozen=True)
class NativeProjection:
    evidence: EvidenceIR | None
    status: Literal["observed", "inconclusive", "tool_error", "unsupported"]
    reason: str
    raw_result_fingerprint: str | None


class _ShapeError(ValueError):
    pass


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _ShapeError("duplicate_json_key")
        result[key] = value
    return result


def _nonfinite(_value):
    raise _ShapeError("nonfinite_json")


def _decode(value):
    if isinstance(value, str):
        value = json.loads(value, object_pairs_hook=_object, parse_constant=_nonfinite)
    if not isinstance(value, dict):
        raise _ShapeError("non_object_payload")
    return value


def _payload(result):
    """Accept one native JSON payload, optionally repeated identically in both surfaces."""
    content = result.get("content", [])
    if not isinstance(content, list) or len(content) > 1:
        raise _ShapeError("multiple_or_malformed_content")
    text_payload = None
    if content:
        block = content[0]
        if not isinstance(block, dict) or block.get("type") != "text":
            raise _ShapeError("non_text_content")
        if not isinstance(block.get("text"), str):
            raise _ShapeError("malformed_text")
        text_payload = _decode(block["text"])
    structured = result.get("structuredContent")
    if structured is not None:
        if not isinstance(structured, dict):
            raise _ShapeError("malformed_structured_content")
        # FastMCP may wrap a string return value under its sole `result` key.
        if set(structured) == {"result"}:
            structured = _decode(structured["result"])
        if text_payload is not None and canonical_sha256(structured) != canonical_sha256(
            text_payload
        ):
            raise _ShapeError("conflicting_payloads")
        return structured
    if text_payload is None:
        raise _ShapeError("missing_payload")
    return text_payload


def _nonnegative_int(value):
    return type(value) is int and value >= 0


def _main_graph(data, payload, task_id, digest):
    """Project the actual CE graph forwarded unchanged by native _cypher_run.

    Map keys locate edge endpoints; only objectId supplies stable identity.
    Source/target fields establish direction, not dictionary iteration order.
    Scalar columns accompanying a graph do not become count/completeness proof.
    """
    nodes, edges = data.get("nodes"), data.get("edges")
    if not isinstance(nodes, dict) or not isinstance(edges, list) or not nodes:
        raise _ShapeError("unsupported_native_graph_shape")
    if any(type(payload.get(key)) is not int or payload[key] != length for key, length in (
        ("node_count", len(nodes)), ("edge_count", len(edges)),
    )):
        raise _ShapeError("graph_counter_mismatch")
    entities, properties, by_key, ids = [], [], {}, set()
    for key, node in nodes.items():
        if not isinstance(node, dict) or not isinstance(key, str):
            raise _ShapeError("malformed_native_node")
        object_id, kind = node.get("objectId"), node.get("kind")
        if not isinstance(object_id, str) or not object_id.strip() or object_id in ids:
            raise _ShapeError("missing_or_duplicate_native_identity")
        if not isinstance(kind, str) or kind not in _LIVE_OBJECT_TYPES:
            raise _ShapeError("unknown_native_type")
        props = node.get("properties", {})
        if not isinstance(props, dict):
            raise _ShapeError("malformed_native_properties")
        _json_value(props)
        if "objectid" in props and props["objectid"] != object_id:
            raise _ShapeError("contradictory_native_identity")
        name = props.get("name", node.get("label"))
        if "name" in props and "label" in node and (
            not isinstance(name, str) or not isinstance(node["label"], str)
            or name.casefold() != node["label"].casefold()
        ):
            raise _ShapeError("contradictory_native_name")
        entities.append(EntityRef(
            object_id=object_id, object_type=kind, canonical_name=name,
            domain=props.get("domain"), role=f"observed_{len(entities)}",
        ))
        properties.extend(EntityPropertyFact(entity_id=object_id, key=fact.key, value=fact.value)
                          for fact in _object_properties(props, object_type=kind))
        ids.add(object_id)
        by_key[key] = object_id
    witnesses, edge_keys = [], set()
    for edge in edges:
        if not isinstance(edge, dict):
            raise _ShapeError("malformed_native_edge")
        endpoints = []
        for field in ("source", "target"):
            token = edge.get(field)
            if type(token) not in (str, int) or str(token) not in by_key:
                raise _ShapeError("missing_native_endpoint")
            endpoints.append(by_key[str(token)])
        kind = edge.get("kind", edge.get("label"))
        if not isinstance(kind, str) or not kind.strip():
            raise _ShapeError("missing_native_edge_type")
        if kind not in _V2_CE_RELATIONSHIPS and canonical_relationship_kind(kind) != kind:
            raise _ShapeError("noncanonical_native_edge_type")
        if "kind" in edge and "label" in edge and edge["kind"] != edge["label"]:
            raise _ShapeError("contradictory_native_edge_type")
        if "direction" in edge and edge["direction"] != "outbound":
            raise _ShapeError("unsupported_native_edge_direction")
        edge_key = (endpoints[0], kind, endpoints[1])
        if edge_key in edge_keys:
            raise _ShapeError("duplicate_native_edge")
        edge_keys.add(edge_key)
        props = edge.get("properties", {})
        if not isinstance(props, dict):
            raise _ShapeError("malformed_native_edge_properties")
        _json_value(props)
        witnesses.append(EdgeWitness(
            source_id=endpoints[0], target_id=endpoints[1], relationship=kind,
            properties=_edge_properties(props),
        ))
    return EvidenceIR(
        task_id=task_id, entities=tuple(entities), edges=tuple(witnesses),
        observed_properties=tuple(properties), raw_digest=digest,
    )


def _entity(row, index, *, domain=False):
    if not isinstance(row, dict):
        raise _ShapeError("malformed_entity")
    object_id = row.get("objectid")
    object_type = "Domain" if domain else row.get("type")
    if not isinstance(object_id, str) or not object_id.strip():
        raise _ShapeError("missing_stable_identity")
    if not isinstance(object_type, str) or not object_type.strip():
        raise _ShapeError("missing_native_type")
    return EntityRef(
        object_id=object_id,
        object_type=object_type,
        role=f"observed_{index}",
        canonical_name=row.get("name"),
        domain=row.get("domain"),
    )


def _armadin(tool_name, arguments, payload, task_id, digest):
    if tool_name == "find_domains":
        if arguments:
            raise _ShapeError("unexpected_arguments")
        rows, count = payload.get("domains"), payload.get("count")
        if not isinstance(rows, list) or not _nonnegative_int(count) or count != len(rows):
            raise _ShapeError("domain_count_mismatch")
        entities = tuple(_entity(row, index, domain=True) for index, row in enumerate(rows))
        if len({entity.object_id for entity in entities}) != len(entities):
            raise _ShapeError("duplicate_identity")
        return EvidenceIR(task_id=task_id, entities=entities, count=count, raw_digest=digest)
    if set(arguments) != {"source", "target"} or not all(
        isinstance(value, str) and value.strip() for value in arguments.values()
    ):
        raise _ShapeError("malformed_path_arguments")
    if payload.get("path_found") is not True:
        raise _ShapeError("no_positive_path_witness")
    if any(payload.get(key) != arguments[key] for key in ("source", "target")):
        raise _ShapeError("path_arguments_mismatch")
    nodes, edges, length = payload.get("nodes"), payload.get("edges"), payload.get("path_length")
    if (
        not isinstance(nodes, list)
        or not isinstance(edges, list)
        or not _nonnegative_int(length)
        or length < 1
        or len(edges) != length
        or len(nodes) != length + 1
    ):
        raise _ShapeError("path_cardinality_mismatch")
    entities = tuple(_entity(row, index) for index, row in enumerate(nodes))
    if len({entity.object_id for entity in entities}) != len(entities):
        raise _ShapeError("duplicate_path_identity")
    for entity, key in ((entities[0], "source"), (entities[-1], "target")):
        if not entity.canonical_name or entity.canonical_name.lower() != arguments[key].lower():
            raise _ShapeError("path_endpoint_mismatch")
    witnesses = []
    for index, edge in enumerate(edges):
        if not isinstance(edge, dict) or not isinstance(edge.get("type"), str):
            raise _ShapeError("missing_relationship_type")
        # The pinned query is directed start-[*1..]->end. Its adjacent ordered
        # node/relationship arrays therefore mechanically establish direction.
        witnesses.append(
            EdgeWitness(
                source_id=entities[index].object_id,
                relationship=edge["type"],
                target_id=entities[index + 1].object_id,
            )
        )
    return EvidenceIR(
        task_id=task_id,
        entities=entities,
        edges=tuple(witnesses),
        path_status=PathStatus.FOUND,
        raw_digest=digest,
    )


def project_native_result(
    implementation_id: str,
    tool_name: str,
    arguments: dict,
    result: dict,
    task: TaskBundle,
) -> NativeProjection:
    """Extract observed data only; callers must not treat `observed` as proof admission."""
    try:
        digest = canonical_sha256(result)
    except (TypeError, ValueError):
        return NativeProjection(None, "inconclusive", "invalid_transport_json", None)
    supported = {
        "armadin": {"find_shortest_path", "find_domains"},
        "mordavid": {"query_bloodhound"},
        "mwnickerson": {"cypher_query"},
    }
    try:
        implementation = get_native_implementation(implementation_id)
    except ValueError:
        return NativeProjection(None, "unsupported", "unsupported_native_shape", digest)
    if tool_name not in implementation.native_tool_names:
        return NativeProjection(None, "unsupported", "unsupported_native_shape", digest)
    try:
        if not isinstance(result, dict) or not isinstance(arguments, dict):
            raise _ShapeError("malformed_call")
        if result.get("isError", False) is True:
            return NativeProjection(None, "tool_error", "mcp_tool_error", digest)
        if type(result.get("isError", False)) is not bool:
            raise _ShapeError("malformed_error_flag")
        payload = _payload(result)
        if payload.get("success") is False:
            return NativeProjection(None, "tool_error", "native_tool_error", digest)
        if tool_name not in supported[implementation_id]:
            return NativeProjection(None, "unsupported", "unsupported_native_shape", digest)
        if implementation_id == "mwnickerson" and arguments.get("info_type") != "run":
            return NativeProjection(None, "unsupported", "unsupported_native_operation", digest)
        if payload.get("success") is not True or "error" in payload:
            raise _ShapeError("malformed_success_envelope")
        if any(
            document.get(key) is not None and document.get(key) is not False
            for document in (result, payload)
            for key in ("truncated", "has_more")
        ):
            raise _ShapeError("truncated_result")
        if implementation_id == "armadin":
            evidence = _armadin(tool_name, arguments, payload, task.task_id, digest)
        elif implementation_id == "mordavid":
            if set(arguments) != {"query"} or not isinstance(arguments["query"], str):
                raise _ShapeError("malformed_query_arguments")
            rows = payload.get("data")
            if not isinstance(rows, list) or len(rows) != 1:
                raise _ShapeError("missing_unambiguous_scalar")
            row = rows[0]
            if not isinstance(row, dict) or len(row) != 1:
                raise _ShapeError("missing_unambiguous_scalar")
            count = next(iter(row.values()))
            if not _nonnegative_int(count):
                raise _ShapeError("lossy_or_unsupported_row")
            evidence = EvidenceIR(task_id=task.task_id, count=count, raw_digest=digest)
        else:
            # Pinned 92a37dd main.py:_cypher_run forwards API data.literals
            # unchanged. Its node_count/edge_count are graph lengths, NOT a
            # scalar answer, and no ORI coordinator wrapper is consumed here.
            if payload.get("info_type") != "run" or not isinstance(arguments.get("query"), str):
                raise _ShapeError("malformed_run_envelope")
            data = payload.get("data")
            if (task.claim_kind == "set" and isinstance(data, dict)
                    and data.get("nodes") == {} and data.get("edges") == []
                    and data.get("literals", []) == []
                    and type(payload.get("has_results")) is bool
                    and type(payload.get("node_count")) is int and payload["node_count"] == 0
                    and type(payload.get("edge_count")) is int and payload["edge_count"] == 0):
                evidence = EvidenceIR(task_id=task.task_id, raw_digest=digest)
                return NativeProjection(evidence, "observed", "mechanical_observation_only", digest)
            if not isinstance(data, dict) or payload.get("has_results") is not True:
                raise _ShapeError("missing_native_scalar")
            if data.get("nodes"):
                evidence = _main_graph(data, payload, task.task_id, digest)
                return NativeProjection(evidence, "observed", "mechanical_observation_only", digest)
            if data.get("nodes") not in ({}, []) or data.get("edges") != []:
                raise _ShapeError("unsupported_mixed_graph_result")
            if any(
                type(payload.get(key)) is not int or payload[key] != 0
                for key in ("node_count", "edge_count")
            ):
                raise _ShapeError("graph_counter_mismatch")
            literals = data.get("literals")
            if not isinstance(literals, list) or len(literals) != 1:
                raise _ShapeError("missing_unambiguous_scalar")
            literal = literals[0]
            if (
                not isinstance(literal, dict)
                or set(literal) != {"key", "value"}
                or not isinstance(literal["key"], str)
                or not literal["key"].strip()
                or not _nonnegative_int(literal["value"])
            ):
                raise _ShapeError("unsupported_native_literal")
            evidence = EvidenceIR(task_id=task.task_id, count=literal["value"], raw_digest=digest)
        return NativeProjection(evidence, "observed", "mechanical_observation_only", digest)
    except (ValueError, TypeError, KeyError, ValidationError):
        return NativeProjection(None, "inconclusive", "native_shape_inconclusive", digest)
