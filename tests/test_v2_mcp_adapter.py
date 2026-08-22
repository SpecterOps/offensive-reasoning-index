from __future__ import annotations

import pytest

from ori.eval.v2.compiler import compile_acceptance_spec
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
    AbsenceClaim,
    BoundedNegativePolicy,
    EdgeWitness,
    EntityRef,
    EntitySelector,
    ExactRoutePolicy,
    ExecutionBounds,
    ExecutionClass,
    MCPBindingMode,
    MCPClaimEvidenceContract,
    NegativeReasonCode,
    NegativeWitness,
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
from ori.eval.v2.scoring import SampleOutcomeCode, summarize_results

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
POLICY = ExactRoutePolicy(kind="exact_route")
BINDING = TrackBinding(
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
    mcp_evidence_contract=MCPClaimEvidenceContract(
        result_kind="path",
        required_input_roles=("source", "target"),
    ),
)
TASK = TaskBundle(
    task_id="simple.mcp.route@2",
    revision=2,
    product="simple",
    claim_kind="route",
    answer_policy=POLICY,
    acceptance_spec=compile_acceptance_spec(CLAIM, POLICY, BINDING),
    binding=BINDING,
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
    graph_fact_registry_fingerprint=FP,
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


def test_mcp_adapter_contains_nonfinite_malformed_output() -> None:
    answer = _answer()
    answer["observed_properties"] = [
        {
            "entity_id": ALICE.object_id,
            "key": "risk",
            "value": float("nan"),
        }
    ]

    outcome = score_mcp_transcript_v2(
        task=TASK,
        oracle=ORACLE,
        resolver=RESOLVER,
        profile=PROFILE,
        tool_loop=MCPToolLoop.NATIVE_OPENAI_COMPATIBLE,
        events=(_useful_event(),),
        final_answer=answer,
        retry_answer=None,
    )

    assert outcome.finalization.phase is FinalizationPhase.OUTPUT_INVALID
    assert outcome.sample.execution_class is ExecutionClass.MODEL_FAILURE
    assert outcome.sample.outcome is SampleOutcomeCode.OUTPUT_INVALID
    assert outcome.sample.reasoning_correct is False


def test_tool_proven_identity_missing_from_catalog_is_harness_failure() -> None:
    answer = _answer()
    answer["edges"][0]["target_id"] = "CE-LOCAL-GROUP-544"

    outcome = score_mcp_transcript_v2(
        task=TASK,
        oracle=ORACLE,
        resolver=RESOLVER,
        profile=PROFILE,
        tool_loop=MCPToolLoop.NATIVE_OPENAI_COMPATIBLE,
        events=(_useful_event(),),
        final_answer=answer,
        observed_identity_ids=("CE-LOCAL-GROUP-544",),
    )

    assert outcome.finalization.phase is FinalizationPhase.HARNESS_FAILURE
    assert outcome.sample.execution_class is ExecutionClass.HARNESS_FAILURE
    assert outcome.sample.outcome is SampleOutcomeCode.HARNESS_ERROR
    assert outcome.sample.reasoning_correct is None
    assert "IDENTITY_NOT_IN_CATALOG" in (outcome.sample.detail or "")


def test_unproven_unknown_identity_remains_invalid_model_output() -> None:
    answer = _answer()
    answer["edges"][0]["target_id"] = "HALLUCINATED-IDENTITY"

    outcome = score_mcp_transcript_v2(
        task=TASK,
        oracle=ORACLE,
        resolver=RESOLVER,
        profile=PROFILE,
        tool_loop=MCPToolLoop.NATIVE_OPENAI_COMPATIBLE,
        events=(_useful_event(),),
        final_answer=answer,
    )

    assert outcome.finalization.phase is FinalizationPhase.OUTPUT_INVALID
    assert outcome.sample.execution_class is ExecutionClass.MODEL_FAILURE
    assert outcome.sample.outcome is SampleOutcomeCode.OUTPUT_INVALID
    assert outcome.sample.reasoning_correct is False


def test_mcp_adapter_contains_unexpected_comparator_failure(
    monkeypatch,
) -> None:
    def raise_internal_error(*args: object, **kwargs: object):
        raise AttributeError("internal comparator defect")

    monkeypatch.setattr(
        "ori.eval.v2.mcp_adapter.compare",
        raise_internal_error,
    )

    outcome = score_mcp_transcript_v2(
        task=TASK,
        oracle=ORACLE,
        resolver=RESOLVER,
        profile=PROFILE,
        tool_loop=MCPToolLoop.NATIVE_OPENAI_COMPATIBLE,
        events=(_useful_event(),),
        final_answer=_answer(),
    )

    assert outcome.finalization.phase is FinalizationPhase.HARNESS_FAILURE
    assert outcome.sample.execution_class is ExecutionClass.HARNESS_FAILURE
    assert outcome.sample.outcome is SampleOutcomeCode.HARNESS_ERROR
    assert outcome.sample.reasoning_correct is None
    assert outcome.sample.verdict is None
    assert outcome.sample.detail == "AttributeError: internal comparator defect"


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

    assert outcome.finalization.phase is FinalizationPhase.EVIDENCE_INSUFFICIENT
    assert outcome.sample.execution_class is ExecutionClass.PROOF_FAILURE
    assert outcome.sample.outcome is SampleOutcomeCode.PROOF_INSUFFICIENT
    assert outcome.sample.reasoning_correct is None
    assert outcome.sample.verdict is None
    summary = summarize_results((TASK.task_id,), (outcome.sample,))
    assert summary.proof_failures == 1
    assert summary.correct == 0
    assert summary.incorrect == 0
    assert summary.reasoning_accuracy is None
    assert summary.effective_accuracy == 0.0
    assert summary.campaign_valid is True


def test_positive_exact_scope_count_overrides_conflicting_negative_answer() -> None:
    claim = AbsenceClaim(
        kind="absence",
        claim_id="claim:negative",
        source=EntitySelector(role="source", object_type="User"),
        target=EntitySelector(role="target", object_type="Group"),
        relationships=("MemberOf",),
        reason_codes=(NegativeReasonCode.OBJECTIVE_UNREACHABLE,),
        max_hops=1,
        semantics=RelationshipSemantics.DIRECT,
        population_scope=PopulationScope.BENCHMARK_NAMESPACE,
    )
    policy = BoundedNegativePolicy(kind="bounded_negative")
    binding = BINDING.model_copy(
        update={
            "mcp_evidence_contract": MCPClaimEvidenceContract(
                result_kind="scalar_count",
                required_input_roles=("source", "target"),
            )
        }
    )
    task = TASK.model_copy(
        update={
            "task_id": "simple.mcp.negative@2",
            "claim_kind": "absence",
            "answer_policy": policy,
            "acceptance_spec": compile_acceptance_spec(claim, policy, binding),
            "binding": binding,
            "answer_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "path_status": {"const": "no_path"},
                    "negative_reason_codes": {
                        "type": "array",
                        "items": {"enum": ["objective_unreachable"]},
                        "uniqueItems": True,
                    },
                },
                "required": ["path_status", "negative_reason_codes"],
            },
        }
    )
    oracle = ORACLE.model_copy(
        update={
            "task_id": task.task_id,
            "claim": claim,
            "route_variants": (),
            "required_mechanisms": (),
            "negative_witnesses": (
                NegativeWitness(
                    reason_code=NegativeReasonCode.OBJECTIVE_UNREACHABLE,
                    checked_entity_ids=(ALICE.object_id, TARGET.object_id),
                    max_hops=1,
                    witness_absent=True,
                ),
            ),
        }
    )
    positive = classify_evidence_event(
        task,
        PROFILE,
        kind=EvidenceEventKind.USEFUL_POSITIVE,
        tool_name="cypher_query",
        operation="run",
    )

    outcome = score_mcp_transcript_v2(
        task=task,
        oracle=oracle,
        resolver=RESOLVER,
        profile=PROFILE,
        tool_loop=MCPToolLoop.NATIVE_OPENAI_COMPATIBLE,
        events=(positive,),
        final_answer={
            "path_status": "no_path",
            "negative_reason_codes": ["objective_unreachable"],
        },
    )

    assert outcome.sample.outcome is SampleOutcomeCode.COMPLETED
    assert outcome.sample.reasoning_correct is False
    assert outcome.sample.evidence is not None
    assert outcome.sample.evidence.path_status.value == "found"
    assert outcome.sample.verdict is not None
    assert outcome.sample.verdict.reason == "BOUNDED_NEGATIVE_INVALID"


@pytest.mark.parametrize(
    ("kind", "expected"),
    (
        (EvidenceEventKind.POLICY_REJECTION, SampleOutcomeCode.POLICY_REJECTED),
        (EvidenceEventKind.QUERY_TIMEOUT, SampleOutcomeCode.QUERY_TIMEOUT),
        (EvidenceEventKind.QUERY_ERROR, SampleOutcomeCode.QUERY_ERROR),
        (EvidenceEventKind.INVALID_ARGUMENTS, SampleOutcomeCode.QUERY_ERROR),
    ),
)
def test_model_authored_mcp_query_failures_count_as_incorrect(
    kind: EvidenceEventKind,
    expected: SampleOutcomeCode,
) -> None:
    failed_event = classify_evidence_event(
        TASK,
        PROFILE,
        kind=kind,
        tool_name="cypher_query",
        operation="run",
    )
    outcome = score_mcp_transcript_v2(
        task=TASK,
        oracle=ORACLE,
        resolver=RESOLVER,
        profile=PROFILE,
        tool_loop=MCPToolLoop.NATIVE_OPENAI_COMPATIBLE,
        events=(failed_event,),
        final_answer=_answer(),
    )

    assert outcome.sample.execution_class is ExecutionClass.MODEL_FAILURE
    assert outcome.sample.outcome is expected
    assert outcome.sample.reasoning_correct is False


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
