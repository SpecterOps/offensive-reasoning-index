"""Track-specific replay of fixture evidence across verified graph snapshots."""

from __future__ import annotations

import json
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
from .graph import GraphSnapshot
from .identity import IdentityResolver
from .mcp import MCPToolLoop
from .mcp_adapter import score_mcp_transcript_v2
from .model_runtime import MCPTranscriptProjector
from .schema import (
    CapabilityProfile,
    EdgeWitness,
    EvidenceIR,
    ExecutionClass,
    OracleBundle,
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
    return {item.entity.object_id: item for item in snapshot.objects}


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
    for candidate in snapshot.relationships:
        if (
            candidate.source_id,
            candidate.relationship,
            candidate.target_id,
            candidate.direction,
        ) != (
            edge.source_id,
            edge.relationship,
            edge.target_id,
            edge.direction,
        ):
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
    known_ids = {entity.object_id for entity in snapshot.entities}
    properties = {
        (item.entity.object_id, fact.key.casefold(), fact.value)
        for item in snapshot.objects
        for fact in item.properties
    }
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
    if task.claim_kind == "count":
        return {
            "data": {
                "nodes": {},
                "edges": [],
                "literals": [{"key": "count", "value": evidence.count}],
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
    resolver = IdentityResolver(snapshot.entities)
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
    return {
        "success": True,
        "data": {"nodes": nodes, "edges": edges},
        "node_count": len(nodes),
        "edge_count": len(edges),
    }


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
        observe(
            "cypher_query",
            {
                "info_type": "run",
                "query": "MATCH (n) RETURN count(n) AS certified_count",
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
        for offset in range(0, max(total, 1), page_size):
            page_ids = {
                entity.object_id
                for entity in entities[offset : offset + page_size]
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
            nodes = _mcp_node_payload(snapshot, page_evidence)
            observe(
                "cypher_query",
                {
                    "info_type": "run",
                    "query": (
                        "MATCH (n) RETURN n ORDER BY n.objectid "
                        f"SKIP {offset} LIMIT {page_size}"
                    ),
                },
                {
                    "success": True,
                    "data": {"nodes": nodes, "edges": []},
                    "node_count": len(nodes),
                    "edge_count": 0,
                },
            )
    elif task.claim_kind in {"count", "absence"}:
        count = perfect_evidence.count
        if count is None:
            count = 0
        observe(
            "cypher_query",
            {
                "info_type": "run",
                "query": "MATCH (n) RETURN count(n) AS certified_count",
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
            "graph_analysis",
            {"info_type": "shortest_path"},
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
    resolver = IdentityResolver(snapshot.entities)
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
