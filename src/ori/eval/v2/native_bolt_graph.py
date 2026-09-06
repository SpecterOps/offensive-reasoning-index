"""Bounded independent Bolt inventory, separate from model-visible tools."""

from __future__ import annotations

import asyncio
import math
from collections import Counter
from collections.abc import Awaitable, Callable

from ori.relationships import canonical_relationship_kind

from .fingerprint import canonical_sha256
from .graph import (
    _LIVE_OBJECT_TYPES,
    _V2_CE_RELATIONSHIPS,
    CE_NORMALIZED_ARTIFACTS,
    LIVE_GRAPH_VERIFICATION_VERSION,
    NORMALIZED_EDGE_METADATA_KEYS,
    GraphObject,
    GraphSnapshot,
    LiveGraphVerification,
    _assert_live_identity,
    _object_properties,
    _property_sort_key,
    graph_snapshot_fingerprint,
    normalized_property_value,
    require_live_graph_match,
)
from .schema import EdgeWitness, PropertyFact

NODE_COUNT_QUERY = "MATCH (n) RETURN count(n) AS count"
EDGE_COUNT_QUERY = "MATCH ()-[r]->() RETURN count(r) AS count"
NODE_PAGE_QUERY = (
    "MATCH (n) RETURN n.objectid AS object_id, labels(n) AS labels, "
    "properties(n) AS properties ORDER BY object_id SKIP $offset LIMIT $limit"
)
EDGE_PAGE_QUERY = (
    "MATCH (a)-[r]->(b) RETURN a.objectid AS source_id, type(r) AS relationship, "
    "b.objectid AS target_id, properties(r) AS properties "
    "ORDER BY source_id, relationship, target_id SKIP $offset LIMIT $limit"
)
Read = Callable[[str, dict], Awaitable[list[dict]]]


def _json_value(value: object, ancestors: frozenset[int] = frozenset()) -> None:
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) not in (list, dict) or id(value) in ancestors:
        raise ValueError("native graph properties require finite acyclic JSON values")
    ancestors = ancestors | {id(value)}
    if isinstance(value, dict):
        if any(type(key) is not str for key in value):
            raise ValueError("native graph property keys must be strings")
        values = value.values()
    else:
        values = value
    for item in values:
        _json_value(item, ancestors)


def _identifier(value: object) -> str:
    if type(value) is not str or not value or value.strip() != value:
        raise ValueError("native graph identity must be a nonempty exact string")
    return value


def _edge_properties(properties: dict) -> tuple[PropertyFact, ...]:
    facts = []
    for key, value in properties.items():
        if key in NORMALIZED_EDGE_METADATA_KEYS:
            continue
        if value is not None and type(value) not in (str, bool, int, float):
            raise ValueError("non-scalar semantic edge property is unsupported")
        facts.append(PropertyFact(key=key, value=normalized_property_value(key, value)))
    return tuple(sorted(facts, key=_property_sort_key))


async def collect_bolt_snapshot(
    read: Read,
    expected: GraphSnapshot,
    *,
    page_size: int = 500,
    timeout_seconds: float = 120.0,
    auxiliary_labels: tuple[str, ...] = (),
) -> tuple[GraphSnapshot, LiveGraphVerification, str]:
    """Measure full inventory, require scorer parity, return a separate native hash.

    A transaction is not snapshot isolation. Caller binds the actual database,
    prevents concurrent writes, and supplies bounded read-only driver operations.
    The native hash measures observed data; it does not establish archive parity
    for properties that the existing scorer intentionally normalizes away.
    Label sequence is preserved because native tools may observe labels(n)[0].
    """
    if type(page_size) is not int or page_size <= 0:
        raise ValueError("page_size must be a positive integer")
    if (
        type(timeout_seconds) not in (int, float)
        or not math.isfinite(timeout_seconds) or timeout_seconds <= 0
    ):
        raise ValueError("timeout_seconds must be finite and positive")
    if type(auxiliary_labels) is not tuple:
        raise ValueError("auxiliary_labels must be a tuple")
    labels = tuple(_identifier(label) for label in auxiliary_labels)
    if len(set(labels)) != len(labels) or set(labels) & _LIVE_OBJECT_TYPES:
        raise ValueError("auxiliary labels must be unique non-concrete labels")
    expected_by_id = {item.entity.object_id: item for item in expected.objects}
    if len(expected_by_id) != len(expected.objects):
        raise ValueError("expected graph has duplicate object IDs")

    async def query(statement: str, parameters: dict) -> list[dict]:
        rows = await read(statement, parameters)
        if type(rows) is not list or any(type(row) is not dict for row in rows):
            raise ValueError("native graph read must return rows of objects")
        return rows

    async def count(statement: str, size: int) -> None:
        rows = await query(statement, {})
        if (
            len(rows) != 1 or set(rows[0]) != {"count"}
            or type(rows[0]["count"]) is not int or rows[0]["count"] != size
        ):
            raise ValueError("native graph count differs from expected inventory")

    async with asyncio.timeout(timeout_seconds):
        await count(NODE_COUNT_QUERY, len(expected.objects))
        await count(EDGE_COUNT_QUERY, len(expected.relationships))
        objects = []
        edges = []
        raw_nodes = []
        raw_edges = []
        previous_id = None
        previous_edge = None
        object_queries = relationship_queries = 2
        for offset in range(0, len(expected.objects), page_size):
            limit = min(page_size, len(expected.objects) - offset)
            rows = await query(NODE_PAGE_QUERY, {"offset": offset, "limit": limit})
            object_queries += 1
            if len(rows) != limit:
                raise ValueError("native node page length mismatch")
            for row in rows:
                if set(row) != {"object_id", "labels", "properties"}:
                    raise ValueError("native node row shape mismatch")
                object_id = _identifier(row["object_id"])
                if previous_id is not None and object_id <= previous_id:
                    raise ValueError("native node order or duplicate identity mismatch")
                previous_id = object_id
                item = expected_by_id.get(object_id)
                if item is None:
                    raise ValueError("native graph contains an unexpected object")
                raw_labels = row["labels"]
                if type(raw_labels) is not list:
                    raise ValueError("native node labels must be a list")
                native_labels = [_identifier(label) for label in raw_labels]
                if (
                    len(set(native_labels)) != len(native_labels)
                    or item.entity.object_type not in native_labels
                    or set(native_labels) - {item.entity.object_type} - set(labels)
                ):
                    raise ValueError("native node has missing, ambiguous or unexpected labels")
                properties = row["properties"]
                if type(properties) is not dict or properties.get("objectid") != object_id:
                    raise ValueError("native object properties disagree with object ID")
                _json_value(properties)
                _assert_live_identity(
                    {"objectId": object_id, "kind": item.entity.object_type,
                     "label": properties.get("name") or object_id,
                     "properties": properties}, item.entity,
                )
                objects.append(GraphObject(
                    entity=item.entity,
                    properties=_object_properties(properties, object_type=item.entity.object_type),
                ))
                raw_nodes.append(row)
        for offset in range(0, len(expected.relationships), page_size):
            limit = min(page_size, len(expected.relationships) - offset)
            rows = await query(EDGE_PAGE_QUERY, {"offset": offset, "limit": limit})
            relationship_queries += 1
            if len(rows) != limit:
                raise ValueError("native relationship page length mismatch")
            for row in rows:
                if set(row) != {"source_id", "relationship", "target_id", "properties"}:
                    raise ValueError("native relationship row shape mismatch")
                key = tuple(_identifier(row[name]) for name in (
                    "source_id", "relationship", "target_id",
                ))
                if previous_edge is not None and key <= previous_edge:
                    raise ValueError("native relationship order or duplicate edge mismatch")
                previous_edge = key
                source, relationship, target = key
                if source not in expected_by_id or target not in expected_by_id:
                    raise ValueError("native relationship has foreign endpoints")
                if relationship not in _V2_CE_RELATIONSHIPS:
                    if canonical_relationship_kind(relationship) != relationship:
                        raise ValueError("native relationship spelling is not canonical")
                properties = row["properties"]
                if type(properties) is not dict:
                    raise ValueError("native relationship properties must be an object")
                _json_value(properties)
                edges.append(EdgeWitness(
                    source_id=source, relationship=relationship, target_id=target,
                    properties=_edge_properties(properties),
                ))
                raw_edges.append(row)
        await count(NODE_COUNT_QUERY, len(expected.objects))
        await count(EDGE_COUNT_QUERY, len(expected.relationships))
        counts = Counter(edge.relationship for edge in edges)
        payload = expected.model_dump(mode="python")
        payload.update(
            objects=tuple(sorted(objects, key=lambda item: item.entity.object_id.casefold())),
            relationships=tuple(sorted(edges, key=lambda edge: (
                edge.source_id.casefold(), edge.relationship.casefold(), edge.target_id.casefold(),
            ))),
            relationship_counts=tuple(
                PropertyFact(key=k, value=v) for k, v in sorted(counts.items())
            ),
            normalized_artifacts=CE_NORMALIZED_ARTIFACTS,
        )
        payload["graph_fingerprint"] = graph_snapshot_fingerprint(payload)
        observed = GraphSnapshot.model_validate(payload)
        require_live_graph_match(expected, observed)
        receipt = dict(
            schema_version=LIVE_GRAPH_VERIFICATION_VERSION,
            expected_graph_fingerprint=expected.graph_fingerprint,
            observed_graph_fingerprint=observed.graph_fingerprint,
            page_size=page_size, object_queries=object_queries,
            relationship_queries=relationship_queries,
            object_count=len(objects), relationship_count=len(edges),
            normalized_artifacts=CE_NORMALIZED_ARTIFACTS,
        )
        receipt["verification_fingerprint"] = canonical_sha256(receipt)
        native_fingerprint = canonical_sha256({"nodes": raw_nodes, "relationships": raw_edges})
        return observed, LiveGraphVerification.model_validate(receipt), native_fingerprint
