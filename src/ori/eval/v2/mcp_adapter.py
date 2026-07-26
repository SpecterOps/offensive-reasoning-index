"""Provider-independent v2 MCP transcript finalization and scoring."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .comparator import compare
from .evidence import EvidenceNormalizationError, normalize_mcp_evidence
from .fingerprint import canonical_sha256
from .identity import IdentityResolver
from .mcp import (
    EvidenceEvent,
    FinalizationAttempt,
    FinalizationPhase,
    FinalizationState,
    FinalOutputStatus,
    MCPToolLoop,
    initial_finalization_state,
    reduce_finalization,
)
from .schema import (
    CapabilityProfile,
    EvidenceIR,
    ExecutionClass,
    OracleBundle,
    TaskBundle,
    VerdictStatus,
)
from .scoring import SampleOutcomeCode, SampleResult


@dataclass(frozen=True)
class MCPV2Outcome:
    """Private MCP state plus the provider-independent sample result."""

    finalization: FinalizationState
    sample: SampleResult


def _output_attempt(
    answer: Mapping[str, Any] | None,
    *,
    task: TaskBundle,
    resolver: IdentityResolver,
) -> tuple[FinalizationAttempt, EvidenceIR | None, str | None]:
    if answer is None:
        return (
            FinalizationAttempt(status=FinalOutputStatus.MISSING),
            None,
            "structured final answer is missing",
        )
    try:
        evidence = normalize_mcp_evidence(
            answer,
            resolver=resolver,
            task_id=task.task_id,
        )
    except (EvidenceNormalizationError, ValueError) as exc:
        return (
            FinalizationAttempt(
                status=FinalOutputStatus.MALFORMED,
                output_digest=canonical_sha256(dict(answer)),
            ),
            None,
            str(exc),
        )
    return (
        FinalizationAttempt(
            status=FinalOutputStatus.VALID,
            output_digest=evidence.raw_digest,
        ),
        evidence,
        None,
    )


def score_mcp_transcript_v2(
    *,
    task: TaskBundle,
    oracle: OracleBundle,
    resolver: IdentityResolver,
    profile: CapabilityProfile,
    tool_loop: MCPToolLoop | str,
    events: Sequence[EvidenceEvent],
    final_answer: Mapping[str, Any] | None,
    retry_answer: Mapping[str, Any] | None = None,
) -> MCPV2Outcome:
    """Reduce one typed transcript and call the shared comparator exactly once."""

    state = initial_finalization_state(
        task,
        profile,
        tool_loop=tool_loop,
    )
    for event in events:
        state = reduce_finalization(state, event)

    attempt, evidence, normalization_error = _output_attempt(
        final_answer,
        task=task,
        resolver=resolver,
    )
    state = reduce_finalization(state, attempt)
    if state.phase is FinalizationPhase.RETRY_SCHEMA_ONLY:
        retry_attempt, retry_evidence, retry_error = _output_attempt(
            retry_answer,
            task=task,
            resolver=resolver,
        )
        state = reduce_finalization(state, retry_attempt)
        evidence = retry_evidence
        normalization_error = retry_error

    base = {
        "task_id": task.task_id,
        "task_fingerprint": task.task_fingerprint,
        "oracle_fingerprint": oracle.oracle_fingerprint,
    }
    if state.phase is FinalizationPhase.INFRASTRUCTURE_FAILURE:
        return MCPV2Outcome(
            finalization=state,
            sample=SampleResult(
                **base,
                execution_class=ExecutionClass.INFRA_FAILURE,
                outcome=SampleOutcomeCode.INFRA_ERROR,
                detail=state.terminal_reason,
            ),
        )
    if state.phase is not FinalizationPhase.FINALIZED or evidence is None:
        return MCPV2Outcome(
            finalization=state,
            sample=SampleResult(
                **base,
                execution_class=ExecutionClass.MODEL_FAILURE,
                outcome=SampleOutcomeCode.OUTPUT_INVALID,
                reasoning_correct=False,
                detail=normalization_error or state.terminal_reason,
            ),
        )

    verdict = compare(task.answer_policy, oracle, evidence)
    return MCPV2Outcome(
        finalization=state,
        sample=SampleResult(
            **base,
            execution_class=ExecutionClass.SUCCESS,
            outcome=SampleOutcomeCode.COMPLETED,
            reasoning_correct=verdict.status is VerdictStatus.CORRECT,
            evidence=evidence,
            verdict=verdict,
        ),
    )
