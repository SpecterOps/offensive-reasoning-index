from __future__ import annotations

from ori.eval.v2.evidence import normalize_offline_evidence
from ori.eval.v2.identity import IdentityResolver
from ori.eval.v2.mcp import (
    EvidenceEventKind,
    FinalizationPhase,
    MCPToolLoop,
    build_mcp_capability_profile,
    classify_evidence_event,
)
from ori.eval.v2.mcp_adapter import score_mcp_transcript_v2
from ori.eval.v2.schema import (
    EdgeWitness,
    EntityRef,
    EntitySelector,
    ExactRoutePolicy,
    ExecutionBounds,
    ExecutionClass,
    MCPBindingMode,
    OracleBundle,
    PopulationScope,
    RelationshipSemantics,
    RouteClaim,
    RouteVariant,
    TaskBundle,
    Track,
    TrackBinding,
    VerdictStatus,
)

FP = "a" * 64
ALICE = EntityRef(
    object_id="USER-A",
    object_type="User",
    domain="EXAMPLE.LOCAL",
    role="source",
    canonical_name="ALICE@EXAMPLE.LOCAL",
)
TARGET = EntityRef(
    object_id="GROUP-B",
    object_type="Group",
    domain="EXAMPLE.LOCAL",
    role="target",
    canonical_name="DOMAIN ADMINS@EXAMPLE.LOCAL",
)
EDGE = EdgeWitness(
    source_id=ALICE.object_id,
    relationship="MemberOf",
    target_id=TARGET.object_id,
)
CLAIM = RouteClaim(
    kind="route",
    claim_id="claim:route",
    source=EntitySelector(role="source", object_type="User"),
    target=EntitySelector(role="target", object_type="Group"),
    semantics=RelationshipSemantics.DIRECT,
    population_scope=PopulationScope.BENCHMARK_NAMESPACE,
    required_mechanisms=("MemberOf",),
    max_hops=1,
)
PROFILE = build_mcp_capability_profile()
TASK = TaskBundle(
    task_id="simple.mcp.route@2",
    revision=2,
    product="simple",
    claim_kind="route",
    answer_policy=ExactRoutePolicy(kind="exact_route"),
    binding=TrackBinding(
        track=Track.MCP,
        capability_profile_id=PROFILE.profile_id,
        semantics=RelationshipSemantics.DIRECT,
        bounds=ExecutionBounds(
            max_hops=1,
            max_result_cardinality=1,
            page_size=1,
            max_pages=1,
            require_total_count=False,
            require_stable_ordering=True,
            max_output_bytes=65_536,
            max_transcript_bytes=262_144,
            max_tool_calls=12,
            timeout_seconds=120.0,
        ),
        mcp_tool_loop=MCPToolLoop.NATIVE_OPENAI_COMPATIBLE.value,
        mcp_resource_mode="off",
        mcp_binding_mode=MCPBindingMode.CYPHER_ENABLED,
    ),
    input_entities=(ALICE, TARGET),
    question="Find the exact edge route.",
    answer_schema={"type": "object"},
    claim_fingerprint=FP,
    prompt_fingerprint=FP,
    task_fingerprint=FP,
)
ORACLE = OracleBundle(
    oracle_id="oracle:route",
    task_id=TASK.task_id,
    claim=CLAIM,
    claim_fingerprint=FP,
    task_fingerprint=FP,
    graph_fingerprint=FP,
    resolved_roles=(ALICE, TARGET),
    route_variants=(RouteVariant(variant_id="canonical", edges=(EDGE,)),),
    graph_edge_registry=(EDGE,),
    required_mechanisms=("MemberOf",),
    source_id=ALICE.object_id,
    target_id=TARGET.object_id,
    oracle_fingerprint=FP,
)
RESOLVER = IdentityResolver((ALICE, TARGET))


def _answer(*, relationship: str = "MemberOf"):
    return {
        "task_id": TASK.task_id,
        "path_status": "found",
        "edges": [
            {
                "source_id": "EXAMPLE\\ALICE",
                "relationship": relationship,
                "target_id": "DOMAIN ADMINS@EXAMPLE.LOCAL",
            }
        ],
    }


def _useful_event():
    return classify_evidence_event(
        TASK,
        PROFILE,
        kind=EvidenceEventKind.USEFUL_POSITIVE,
        tool_name="cypher_query",
        operation="run",
    )


def test_mcp_adapter_and_offline_replay_produce_identical_evidence_and_verdict() -> None:
    answer = _answer()
    outcome = score_mcp_transcript_v2(
        task=TASK,
        oracle=ORACLE,
        resolver=RESOLVER,
        profile=PROFILE,
        tool_loop=MCPToolLoop.NATIVE_OPENAI_COMPATIBLE,
        events=(_useful_event(),),
        final_answer=answer,
    )
    offline = normalize_offline_evidence(
        answer,
        resolver=RESOLVER,
        task_id=TASK.task_id,
    )

    assert outcome.finalization.phase is FinalizationPhase.FINALIZED
    assert outcome.sample.execution_class is ExecutionClass.SUCCESS
    assert outcome.sample.evidence == offline
    assert outcome.sample.verdict is not None
    assert outcome.sample.verdict.status is VerdictStatus.CORRECT


def test_mcp_adapter_uses_comparator_for_wrong_but_well_formed_evidence() -> None:
    outcome = score_mcp_transcript_v2(
        task=TASK,
        oracle=ORACLE,
        resolver=RESOLVER,
        profile=PROFILE,
        tool_loop=MCPToolLoop.INSPECT,
        events=(_useful_event(),),
        final_answer=_answer(relationship="AdminTo"),
    )

    assert outcome.sample.execution_class is ExecutionClass.SUCCESS
    assert outcome.sample.reasoning_correct is False
    assert outcome.sample.verdict is not None
    assert outcome.sample.verdict.status is VerdictStatus.INCORRECT


def test_irrelevant_activity_cannot_unlock_mcp_finalization() -> None:
    irrelevant = classify_evidence_event(
        TASK,
        PROFILE,
        kind=EvidenceEventKind.USEFUL_POSITIVE,
        tool_name="data_quality",
        operation="completeness",
    )
    outcome = score_mcp_transcript_v2(
        task=TASK,
        oracle=ORACLE,
        resolver=RESOLVER,
        profile=PROFILE,
        tool_loop=MCPToolLoop.NATIVE_OLLAMA,
        events=(irrelevant,),
        final_answer=_answer(),
    )

    assert outcome.finalization.phase is FinalizationPhase.OUTPUT_INVALID
    assert outcome.sample.execution_class is ExecutionClass.MODEL_FAILURE
    assert outcome.sample.reasoning_correct is False
    assert outcome.sample.verdict is None


def test_one_schema_retry_and_infrastructure_have_distinct_accounting() -> None:
    repaired = score_mcp_transcript_v2(
        task=TASK,
        oracle=ORACLE,
        resolver=RESOLVER,
        profile=PROFILE,
        tool_loop=MCPToolLoop.INSPECT,
        events=(_useful_event(),),
        final_answer={"unknown": "field"},
        retry_answer=_answer(),
    )
    assert repaired.finalization.phase is FinalizationPhase.FINALIZED
    assert repaired.finalization.schema_retry_count == 1

    infrastructure = classify_evidence_event(
        TASK,
        PROFILE,
        kind=EvidenceEventKind.INFRASTRUCTURE_FAILURE,
        reason="transport_error",
    )
    failed = score_mcp_transcript_v2(
        task=TASK,
        oracle=ORACLE,
        resolver=RESOLVER,
        profile=PROFILE,
        tool_loop=MCPToolLoop.INSPECT,
        events=(infrastructure,),
        final_answer=None,
    )
    assert failed.finalization.phase is FinalizationPhase.INFRASTRUCTURE_FAILURE
    assert failed.sample.execution_class is ExecutionClass.INFRA_FAILURE
    assert failed.sample.reasoning_correct is None
    assert failed.sample.verdict is None
    assert failed.sample.evidence is None
