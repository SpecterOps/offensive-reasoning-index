from __future__ import annotations

from typing import cast

import pytest

from ori.eval.v2.fingerprint import canonical_sha256
from ori.eval.v2.mcp import (
    CERTIFIED_MCP_TOOL_LOOPS,
    MCP_BLOODHOUND_CE_VERSION,
    MCP_CAPABILITY_PROFILE_FINGERPRINT,
    MCP_CAPABILITY_PROFILE_ID,
    MCP_FINALIZATION_POLICY_FINGERPRINT,
    MCP_SERVER_REVISION,
    SCHEMA_ONLY_RETRY_INSTRUCTION,
    EvidenceEventKind,
    FinalizationAttempt,
    FinalizationPhase,
    FinalOutputStatus,
    MCPToolLoop,
    ToolObservation,
    build_mcp_capability_profile,
    build_mcp_loop_conformance_matrix,
    capability_for_operation,
    classify_evidence_event,
    classify_mcp_binding,
    classify_tool_observation,
    initial_finalization_state,
    reduce_finalization,
    validate_certified_mcp_loop,
    validate_mcp_capability_profile,
)
from ori.eval.v2.schema import (
    BoundedNegativePolicy,
    ExactRoutePolicy,
    ExecutionBounds,
    MCPBindingMode,
    RelationshipSemantics,
    TaskBundle,
    Track,
    TrackBinding,
)

FP = "a" * 64


def _bounds(*, max_pages: int = 1) -> ExecutionBounds:
    return ExecutionBounds(
        max_hops=6,
        max_result_cardinality=100,
        page_size=100,
        max_pages=max_pages,
        require_total_count=False,
        require_stable_ordering=True,
        max_output_bytes=65_536,
        max_transcript_bytes=262_144,
        max_tool_calls=12,
        timeout_seconds=120.0,
    )


def _task(
    *,
    claim_kind: str = "route",
    mode: MCPBindingMode = MCPBindingMode.CYPHER_ENABLED,
    max_pages: int = 1,
) -> TaskBundle:
    answer_policy = (
        BoundedNegativePolicy(kind="bounded_negative")
        if claim_kind == "absence"
        else ExactRoutePolicy(kind="exact_route")
    )
    return TaskBundle(
        task_id=f"complex.mcp.{claim_kind}@2",
        revision=2,
        product="complex",
        claim_kind=claim_kind,
        answer_policy=answer_policy,
        binding=TrackBinding(
            track=Track.MCP,
            capability_profile_id=MCP_CAPABILITY_PROFILE_ID,
            semantics=RelationshipSemantics.EFFECTIVE,
            bounds=_bounds(max_pages=max_pages),
            mcp_tool_loop=MCPToolLoop.NATIVE_OPENAI_COMPATIBLE.value,
            mcp_resource_mode="off",
            mcp_binding_mode=mode,
        ),
        question="Return the bounded effective route evidence.",
        answer_schema={"type": "object"},
        claim_fingerprint=FP,
        prompt_fingerprint=FP,
        task_fingerprint=FP,
    )


def _useful_event(task: TaskBundle):
    return classify_evidence_event(
        task,
        build_mcp_capability_profile(),
        kind=EvidenceEventKind.USEFUL_POSITIVE,
        tool_name="graph_analysis",
        operation="shortest_path",
    )


def test_pinned_profile_is_fingerprinted_and_declares_exact_revision() -> None:
    profile = build_mcp_capability_profile()

    assert profile.profile_id == MCP_CAPABILITY_PROFILE_ID
    assert profile.bloodhound_ce_version == MCP_BLOODHOUND_CE_VERSION == "9.1.0"
    assert profile.mcp_server_revision == MCP_SERVER_REVISION
    assert profile.finalization_policy_fingerprint == MCP_FINALIZATION_POLICY_FINGERPRINT
    assert MCP_SERVER_REVISION == "009c88f41fae302becad4b00777a3749a0f6f0fa"
    assert profile.profile_fingerprint == MCP_CAPABILITY_PROFILE_FINGERPRINT
    assert profile.profile_fingerprint == canonical_sha256(
        profile,
        exclude_fields=("profile_fingerprint",),
    )
    assert not profile.resources
    assert len({(tool.name, tool.operation) for tool in profile.tools}) == len(profile.tools)
    assert validate_mcp_capability_profile(profile) is profile


def test_profile_validation_rejects_fingerprint_and_rehashed_content_drift() -> None:
    profile = build_mcp_capability_profile()
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        validate_mcp_capability_profile(
            profile.model_copy(update={"profile_fingerprint": "b" * 64})
        )

    first = profile.tools[0]
    changed_tool = first.model_copy(update={"max_output_bytes": first.max_output_bytes - 1})
    changed = profile.model_copy(update={"tools": (changed_tool, *profile.tools[1:])})
    changed = changed.model_copy(
        update={
            "profile_fingerprint": canonical_sha256(
                changed,
                exclude_fields=("profile_fingerprint",),
            )
        }
    )
    with pytest.raises(ValueError, match="does not match the pinned revision"):
        validate_mcp_capability_profile(changed)

    changed_finalization = profile.model_copy(
        update={"finalization_policy_fingerprint": "c" * 64}
    )
    changed_finalization = changed_finalization.model_copy(
        update={
            "profile_fingerprint": canonical_sha256(
                changed_finalization,
                exclude_fields=("profile_fingerprint",),
            )
        }
    )
    with pytest.raises(ValueError, match="does not match the pinned revision"):
        validate_mcp_capability_profile(changed_finalization)


def test_binding_classification_is_explicit_and_fails_closed() -> None:
    profile = build_mcp_capability_profile()

    assert classify_mcp_binding(_task(), profile) is MCPBindingMode.CYPHER_ENABLED
    assert (
        classify_mcp_binding(_task(mode=MCPBindingMode.TOOL_ONLY), profile)
        is MCPBindingMode.TOOL_ONLY
    )
    assert (
        classify_mcp_binding(_task(max_pages=2), profile)
        is MCPBindingMode.CYPHER_ENABLED
    )
    assert (
        classify_mcp_binding(
            _task(mode=MCPBindingMode.TOOL_ONLY, max_pages=2),
            profile,
        )
        is MCPBindingMode.BLOCKED
    )


def test_event_classifier_accepts_only_public_task_contracts() -> None:
    with pytest.raises(TypeError, match="public TaskBundle"):
        classify_evidence_event(
            cast(TaskBundle, object()),
            build_mcp_capability_profile(),
            kind=EvidenceEventKind.USEFUL_POSITIVE,
            tool_name="graph_analysis",
            operation="shortest_path",
        )


@pytest.mark.parametrize(
    ("observation", "expected"),
    [
        (
            ToolObservation(
                tool_name="graph_analysis",
                operation="shortest_path",
                succeeded=True,
                result_count=1,
                complete=True,
                output_bytes=256,
            ),
            EvidenceEventKind.USEFUL_POSITIVE,
        ),
        (
            ToolObservation(
                tool_name="graph_analysis",
                operation="shortest_path",
                succeeded=True,
                result_count=1,
                complete=False,
                output_bytes=256,
            ),
            EvidenceEventKind.TRUNCATED,
        ),
        (
            ToolObservation(
                tool_name="graph_analysis",
                operation="shortest_path",
                succeeded=True,
                result_count=0,
                complete=True,
                negative_proof=True,
                output_bytes=64,
            ),
            EvidenceEventKind.VALID_NEGATIVE,
        ),
        (
            ToolObservation(
                tool_name="graph_analysis",
                operation="shortest_path",
                succeeded=True,
                result_count=0,
                complete=True,
                output_bytes=64,
            ),
            EvidenceEventKind.CONCLUSIVE_EMPTY,
        ),
        (
            ToolObservation(
                tool_name="graph_analysis",
                operation="shortest_path",
                succeeded=True,
                result_count=0,
                complete=False,
                output_bytes=64,
            ),
            EvidenceEventKind.INCONCLUSIVE_EMPTY,
        ),
        (
            ToolObservation(
                tool_name="graph_analysis",
                operation="shortest_path",
                succeeded=True,
                result_count=1,
                truncated=True,
                output_bytes=64,
            ),
            EvidenceEventKind.TRUNCATED,
        ),
        (
            ToolObservation(
                tool_name="graph_analysis",
                operation="shortest_path",
                succeeded=False,
                arguments_valid=False,
            ),
            EvidenceEventKind.INVALID_ARGUMENTS,
        ),
        (
            ToolObservation(
                tool_name="graph_analysis",
                operation="shortest_path",
                succeeded=False,
                policy_rejected=True,
            ),
            EvidenceEventKind.POLICY_REJECTION,
        ),
        (
            ToolObservation(
                tool_name="graph_analysis",
                operation="shortest_path",
                succeeded=False,
                infrastructure_failure=True,
            ),
            EvidenceEventKind.INFRASTRUCTURE_FAILURE,
        ),
    ],
)
def test_raw_tool_observations_are_classified_by_public_contract(
    observation: ToolObservation,
    expected: EvidenceEventKind,
) -> None:
    task = _task(claim_kind="absence")
    event = classify_tool_observation(
        task,
        build_mcp_capability_profile(),
        observation,
    )

    assert event.kind is expected


def test_raw_tool_observation_cannot_claim_completeness_without_total_count() -> None:
    task = _task(claim_kind="absence")
    event = classify_tool_observation(
        task,
        build_mcp_capability_profile(),
        ToolObservation(
            tool_name="cypher_query",
            operation="run",
            succeeded=True,
            result_count=0,
            complete=True,
            output_bytes=64,
        ),
    )

    assert event.kind is EvidenceEventKind.TRUNCATED
    assert not event.unlocks_finalization


def test_event_classifier_demotes_non_capability_success_and_inconclusive_empty() -> None:
    task = _task()
    profile = build_mcp_capability_profile()
    useful = _useful_event(task)
    irrelevant = classify_evidence_event(
        task,
        profile,
        kind=EvidenceEventKind.USEFUL_POSITIVE,
        tool_name="data_quality",
        operation="completeness",
    )
    unknown = classify_evidence_event(
        task,
        profile,
        kind=EvidenceEventKind.USEFUL_POSITIVE,
        tool_name="unknown",
        operation="success",
    )
    empty = classify_evidence_event(
        task,
        profile,
        kind=EvidenceEventKind.CONCLUSIVE_EMPTY,
        tool_name="graph_analysis",
        operation="edge_composition",
    )

    assert useful.kind is EvidenceEventKind.USEFUL_POSITIVE
    assert irrelevant.kind is EvidenceEventKind.IRRELEVANT
    assert unknown.kind is EvidenceEventKind.IRRELEVANT
    assert empty.kind is EvidenceEventKind.INCONCLUSIVE_EMPTY


def test_bounded_shortest_path_can_supply_valid_negative_and_conclusive_empty() -> None:
    task = _task(claim_kind="absence")
    profile = build_mcp_capability_profile()

    valid_negative = classify_evidence_event(
        task,
        profile,
        kind=EvidenceEventKind.VALID_NEGATIVE,
        tool_name="graph_analysis",
        operation="shortest_path",
    )
    empty = classify_evidence_event(
        task,
        profile,
        kind=EvidenceEventKind.CONCLUSIVE_EMPTY,
        tool_name="graph_analysis",
        operation="shortest_path",
    )

    assert valid_negative.kind is EvidenceEventKind.VALID_NEGATIVE
    assert empty.kind is EvidenceEventKind.CONCLUSIVE_EMPTY
    assert valid_negative.unlocks_finalization
    assert empty.unlocks_finalization


@pytest.mark.parametrize(
    "kind",
    [
        EvidenceEventKind.IRRELEVANT,
        EvidenceEventKind.INCONCLUSIVE_EMPTY,
        EvidenceEventKind.TRUNCATED,
        EvidenceEventKind.INVALID_ARGUMENTS,
        EvidenceEventKind.POLICY_REJECTION,
    ],
)
def test_non_evidence_tool_events_do_not_unlock_finalization(
    kind: EvidenceEventKind,
) -> None:
    task = _task()
    profile = build_mcp_capability_profile()
    event = classify_evidence_event(
        task,
        profile,
        kind=kind,
        tool_name="graph_analysis",
        operation="shortest_path",
    )
    initial = initial_finalization_state(
        task,
        profile,
        tool_loop=MCPToolLoop.INSPECT,
    )
    reduced = reduce_finalization(initial, event)

    assert initial.phase is FinalizationPhase.COLLECTING
    assert reduced.phase is FinalizationPhase.COLLECTING
    assert not reduced.finalization_unlocked


def test_resource_read_does_not_unlock_finalization() -> None:
    task = _task()
    profile = build_mcp_capability_profile()
    resource = classify_evidence_event(
        task,
        profile,
        kind=EvidenceEventKind.RESOURCE_READ,
        resource_uri="bloodhound://guides/ad",
    )
    reduced = reduce_finalization(
        initial_finalization_state(task, profile, tool_loop=MCPToolLoop.INSPECT),
        resource,
    )

    assert resource.kind is EvidenceEventKind.RESOURCE_READ
    assert not reduced.finalization_unlocked


def test_reducer_is_immutable_and_allows_one_generic_schema_retry() -> None:
    task = _task()
    profile = build_mcp_capability_profile()
    initial = initial_finalization_state(
        task,
        profile,
        tool_loop=MCPToolLoop.NATIVE_OPENAI_COMPATIBLE,
    )
    ready = reduce_finalization(initial, _useful_event(task))
    retry = reduce_finalization(
        ready,
        FinalizationAttempt(status=FinalOutputStatus.MALFORMED),
    )
    invalid = reduce_finalization(
        retry,
        FinalizationAttempt(status=FinalOutputStatus.MALFORMED),
    )

    assert initial.events == ()
    assert initial.phase is FinalizationPhase.COLLECTING
    assert ready.phase is FinalizationPhase.READY
    assert retry.phase is FinalizationPhase.RETRY_SCHEMA_ONLY
    assert retry.retry_instruction == SCHEMA_ONLY_RETRY_INSTRUCTION
    assert retry.schema_retry_count == 1
    assert invalid.phase is FinalizationPhase.OUTPUT_INVALID
    assert invalid.terminal_reason == "OUTPUT_INVALID"
    assert invalid.schema_retry_count == 1


def test_valid_output_requires_claim_relevant_evidence() -> None:
    task = _task()
    profile = build_mcp_capability_profile()
    initial = initial_finalization_state(task, profile, tool_loop=MCPToolLoop.INSPECT)
    without_evidence = reduce_finalization(
        initial,
        FinalizationAttempt(status=FinalOutputStatus.VALID, output_digest="c" * 64),
    )
    with_evidence = reduce_finalization(
        reduce_finalization(initial, _useful_event(task)),
        FinalizationAttempt(status=FinalOutputStatus.VALID, output_digest="c" * 64),
    )

    assert without_evidence.phase is FinalizationPhase.OUTPUT_INVALID
    assert without_evidence.terminal_reason == "NO_CLAIM_RELEVANT_EVIDENCE"
    assert with_evidence.phase is FinalizationPhase.FINALIZED


def test_infrastructure_failure_is_terminal_and_absorbing() -> None:
    task = _task()
    profile = build_mcp_capability_profile()
    initial = initial_finalization_state(task, profile, tool_loop=MCPToolLoop.INSPECT)
    infrastructure = classify_evidence_event(
        task,
        profile,
        kind=EvidenceEventKind.INFRASTRUCTURE_FAILURE,
        reason="transport_error",
    )
    terminal = reduce_finalization(initial, infrastructure)
    after = reduce_finalization(terminal, _useful_event(task))

    assert terminal.phase is FinalizationPhase.INFRASTRUCTURE_FAILURE
    assert terminal.terminal_reason == "INFRASTRUCTURE_FAILURE"
    assert after is terminal


def test_certified_loop_validation_forbids_auto() -> None:
    for tool_loop in CERTIFIED_MCP_TOOL_LOOPS:
        assert validate_certified_mcp_loop(tool_loop) is tool_loop

    with pytest.raises(ValueError, match="auto.*forbidden"):
        validate_certified_mcp_loop(MCPToolLoop.AUTO)


def test_common_loop_conformance_matrix_has_identical_semantics() -> None:
    task = _task()
    rows = build_mcp_loop_conformance_matrix(
        task,
        build_mcp_capability_profile(),
        useful_event=_useful_event(task),
    )

    assert {row.tool_loop for row in rows} == set(CERTIFIED_MCP_TOOL_LOOPS)
    assert {
        (
            row.ignored_events_unlock,
            row.useful_event_phase,
            row.first_malformed_phase,
            row.second_malformed_phase,
            row.infrastructure_phase,
            row.schema_retry_count,
        )
        for row in rows
    } == {
        (
            False,
            FinalizationPhase.READY,
            FinalizationPhase.RETRY_SCHEMA_ONLY,
            FinalizationPhase.OUTPUT_INVALID,
            FinalizationPhase.INFRASTRUCTURE_FAILURE,
            1,
        )
    }


def test_profile_exposes_only_exact_composite_operations() -> None:
    profile = build_mcp_capability_profile()

    assert (
        capability_for_operation(
            profile,
            tool_name="graph_analysis",
            operation="shortest_path",
        )
        is not None
    )
    assert (
        capability_for_operation(
            profile,
            tool_name="graph",
            operation="shortest",
        )
        is None
    )
