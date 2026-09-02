"""Provider-independent v2 MCP transcript finalization and scoring."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator

from .comparator import compare
from .evidence import (
    EvidenceIdentityCatalogError,
    EvidenceNormalizationError,
    attest_graph_facts,
    diagnostic_payload_digest,
    validate_and_normalize_evidence,
)
from .fingerprint import canonical_sha256
from .identity import IdentityResolver
from .mcp import (
    EvidenceEvent,
    EvidenceEventKind,
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
    GraphFactRegistry,
    OracleBundle,
    PathStatus,
    TaskBundle,
    VerdictStatus,
)
from .scoring import SampleOutcomeCode, SampleResult


@dataclass(frozen=True)
class MCPV2Outcome:
    """Private MCP state plus the provider-independent sample result."""

    finalization: FinalizationState
    sample: SampleResult


def _schema_compliant_answer(
    answer: Mapping[str, Any] | None,
    task: TaskBundle,
) -> bool | None:
    if answer is None:
        return None

    def finite(value: Any) -> bool:
        if isinstance(value, float):
            return math.isfinite(value)
        if isinstance(value, Mapping):
            return all(finite(item) for item in value.values())
        if isinstance(value, (list, tuple)):
            return all(finite(item) for item in value)
        return True

    return finite(answer) and Draft202012Validator(task.answer_schema).is_valid(
        dict(answer)
    )


def _output_attempt(
    answer: Mapping[str, Any] | None,
    *,
    task: TaskBundle,
    resolver: IdentityResolver,
    observed_identity_ids: frozenset[str],
    graph_fact_registry: GraphFactRegistry | None,
) -> tuple[FinalizationAttempt, EvidenceIR | None, str | None]:
    if answer is None:
        return (
            FinalizationAttempt(status=FinalOutputStatus.MISSING),
            None,
            "structured final answer is missing",
        )
    try:
        evidence = validate_and_normalize_evidence(
            answer,
            answer_schema=task.answer_schema,
            resolver=resolver,
            task_id=task.task_id,
        )
        if graph_fact_registry is not None:
            evidence = attest_graph_facts(evidence, graph_fact_registry)
    except EvidenceIdentityCatalogError as exc:
        if exc.token.casefold() in observed_identity_ids:
            # The identity was returned by a successful BloodHound tool call.
            # Its absence from the sealed registry is a certification defect,
            # not malformed model output.
            raise
        return (
            FinalizationAttempt(
                status=FinalOutputStatus.MALFORMED,
                output_digest=diagnostic_payload_digest(answer),
            ),
            None,
            str(exc),
        )
    except EvidenceNormalizationError as exc:
        return (
            FinalizationAttempt(
                status=FinalOutputStatus.MALFORMED,
                output_digest=diagnostic_payload_digest(answer),
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
    observed_identity_ids: Sequence[str] = (),
    graph_fact_registry: GraphFactRegistry | None = None,
    final_output_normalized: bool = False,
    retry_output_normalized: bool = False,
    final_output_compliant: bool | None = None,
    retry_output_compliant: bool | None = None,
    final_receipt_attested: bool = True,
    retry_receipt_attested: bool = True,
    retry_contract_error: str | None = None,
) -> MCPV2Outcome:
    """Reduce one typed transcript and call the shared comparator exactly once."""

    state = initial_finalization_state(
        task,
        profile,
        tool_loop=tool_loop,
    )
    for event in events:
        state = reduce_finalization(state, event)

    base = {
        "task_id": task.task_id,
        "task_fingerprint": task.task_fingerprint,
        "oracle_fingerprint": oracle.oracle_fingerprint,
    }
    observed_ids = frozenset(
        value.casefold() for value in observed_identity_ids
    )
    output_compliant = final_output_compliant
    if output_compliant is None and final_answer is not None:
        output_compliant = _schema_compliant_answer(final_answer, task)
    output_normalized = final_output_normalized and output_compliant is True
    receipt_attested = final_receipt_attested
    try:
        attempt, evidence, normalization_error = _output_attempt(
            final_answer,
            task=task,
            resolver=resolver,
            observed_identity_ids=observed_ids,
            graph_fact_registry=graph_fact_registry,
        )
    except EvidenceIdentityCatalogError as exc:
        state = state.model_copy(
            update={
                "phase": FinalizationPhase.HARNESS_FAILURE,
                "terminal_reason": exc.code,
            }
        )
        return MCPV2Outcome(
            finalization=state,
            sample=SampleResult(
                **base,
                execution_class=ExecutionClass.HARNESS_FAILURE,
                outcome=SampleOutcomeCode.HARNESS_ERROR,
                detail=str(exc),
            ),
        )
    state = reduce_finalization(state, attempt)
    if state.phase is FinalizationPhase.RETRY_SCHEMA_ONLY:
        try:
            retry_attempt, retry_evidence, retry_error = _output_attempt(
                retry_answer,
                task=task,
                resolver=resolver,
                observed_identity_ids=observed_ids,
                graph_fact_registry=graph_fact_registry,
            )
        except EvidenceIdentityCatalogError as exc:
            state = state.model_copy(
                update={
                    "phase": FinalizationPhase.HARNESS_FAILURE,
                    "terminal_reason": exc.code,
                }
            )
            return MCPV2Outcome(
                finalization=state,
                sample=SampleResult(
                    **base,
                    execution_class=ExecutionClass.HARNESS_FAILURE,
                    outcome=SampleOutcomeCode.HARNESS_ERROR,
                    detail=str(exc),
                ),
            )
        state = reduce_finalization(state, retry_attempt)
        evidence = retry_evidence
        normalization_error = retry_error
        output_compliant = retry_output_compliant
        if retry_answer is not None:
            output_compliant = (
                retry_output_compliant
                if retry_output_compliant is not None
                else _schema_compliant_answer(retry_answer, task)
            )
            output_normalized = retry_output_normalized and output_compliant is True
            receipt_attested = retry_receipt_attested
        elif output_compliant is None:
            output_compliant = False

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
    if state.phase is FinalizationPhase.HARNESS_FAILURE:
        return MCPV2Outcome(
            finalization=state,
            sample=SampleResult(
                **base,
                execution_class=ExecutionClass.HARNESS_FAILURE,
                outcome=SampleOutcomeCode.HARNESS_ERROR,
                detail=state.terminal_reason,
            ),
        )
    if state.phase is FinalizationPhase.TASK_TIMEOUT:
        return MCPV2Outcome(
            finalization=state,
            sample=SampleResult(
                **base,
                execution_class=ExecutionClass.MODEL_FAILURE,
                outcome=SampleOutcomeCode.TASK_TIMEOUT,
                detail=state.terminal_reason,
            ),
        )
    if retry_contract_error is not None:
        state = state.model_copy(
            update={
                "phase": FinalizationPhase.OUTPUT_INVALID,
                "terminal_reason": retry_contract_error,
            }
        )
        return MCPV2Outcome(
            finalization=state,
            sample=SampleResult(
                **base,
                execution_class=ExecutionClass.MODEL_FAILURE,
                outcome=SampleOutcomeCode.OUTPUT_INVALID,
                output_compliant=False,
                detail=retry_contract_error,
            ),
        )
    if state.phase is FinalizationPhase.EVIDENCE_INSUFFICIENT:
        event_kinds = {event.kind for event in state.events}
        if EvidenceEventKind.POLICY_REJECTION in event_kinds:
            return MCPV2Outcome(
                finalization=state,
                sample=SampleResult(
                    **base,
                    execution_class=ExecutionClass.MODEL_FAILURE,
                    outcome=SampleOutcomeCode.POLICY_REJECTED,
                    output_compliant=output_compliant,
                    output_normalized=output_normalized,
                    detail="model-authored MCP query was rejected by policy",
                ),
            )
        if EvidenceEventKind.QUERY_TIMEOUT in event_kinds:
            return MCPV2Outcome(
                finalization=state,
                sample=SampleResult(
                    **base,
                    execution_class=ExecutionClass.MODEL_FAILURE,
                    outcome=SampleOutcomeCode.QUERY_TIMEOUT,
                    output_compliant=output_compliant,
                    output_normalized=output_normalized,
                    detail="model-authored MCP query exceeded the BloodHound query budget",
                ),
            )
        if (
            EvidenceEventKind.QUERY_ERROR in event_kinds
            or EvidenceEventKind.INVALID_ARGUMENTS in event_kinds
        ):
            return MCPV2Outcome(
                finalization=state,
                sample=SampleResult(
                    **base,
                    execution_class=ExecutionClass.MODEL_FAILURE,
                    outcome=SampleOutcomeCode.QUERY_ERROR,
                    output_compliant=output_compliant,
                    output_normalized=output_normalized,
                    detail="model-authored MCP query or arguments were invalid",
                ),
            )
        return MCPV2Outcome(
            finalization=state,
            sample=SampleResult(
                **base,
                execution_class=ExecutionClass.PROOF_FAILURE,
                outcome=SampleOutcomeCode.PROOF_INSUFFICIENT,
                output_compliant=output_compliant,
                output_normalized=output_normalized,
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
                output_compliant=(False if output_compliant is None else output_compliant),
                output_normalized=output_normalized,
                detail=normalization_error or state.terminal_reason,
            ),
        )

    if not receipt_attested:
        state = state.model_copy(
            update={
                "phase": FinalizationPhase.OUTPUT_INVALID,
                "terminal_reason": "FINAL_ANSWER_NOT_ATTESTED_BY_TOOL_RECEIPTS",
            }
        )
        return MCPV2Outcome(
            finalization=state,
            sample=SampleResult(
                **base,
                execution_class=ExecutionClass.MODEL_FAILURE,
                outcome=SampleOutcomeCode.OUTPUT_INVALID,
                output_compliant=True,
                output_normalized=output_normalized,
                detail="final answer contains facts not attested by claim-bound tool receipts",
            ),
        )

    if task.claim_kind == "absence" and any(
        event.kind is EvidenceEventKind.USEFUL_POSITIVE
        for event in state.events
    ):
        # A complete non-zero count over the exact public negative scope is
        # authoritative contradictory graph evidence. Keep the model's reason
        # codes for diagnostics, but force the shared comparator to grade the
        # path as found rather than accepting a conflicting ``no_path`` claim.
        evidence = evidence.model_copy(
            update={
                "path_status": PathStatus.FOUND,
                "raw_digest": canonical_sha256(
                    {
                        "answer_evidence_digest": evidence.raw_digest,
                        "authoritative_path_status": PathStatus.FOUND.value,
                    }
                ),
            }
        )

    try:
        verdict = compare(task.answer_policy, oracle, evidence)
    except Exception as exc:
        state = state.model_copy(
            update={
                "phase": FinalizationPhase.HARNESS_FAILURE,
                "terminal_reason": "HARNESS_FAILURE",
            }
        )
        return MCPV2Outcome(
            finalization=state,
            sample=SampleResult(
                **base,
                execution_class=ExecutionClass.HARNESS_FAILURE,
                outcome=SampleOutcomeCode.HARNESS_ERROR,
                detail=f"{type(exc).__name__}: {exc}",
            ),
        )
    return MCPV2Outcome(
        finalization=state,
        sample=SampleResult(
            **base,
            execution_class=ExecutionClass.SUCCESS,
            outcome=SampleOutcomeCode.COMPLETED,
            reasoning_correct=verdict.status is VerdictStatus.CORRECT,
            output_compliant=True,
            output_normalized=output_normalized,
            evidence=evidence,
            verdict=verdict,
        ),
    )
