"""Focused invariants for V30 reasoning and output-compliance accounting."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ori.eval.v2.campaign import PublicResultRow, _summarize_public_rows
from ori.eval.v2.schema import (
    EvidenceIR,
    ExecutionClass,
    Track,
    Verdict,
    VerdictStatus,
)
from ori.eval.v2.scoring import (
    SampleOutcomeCode,
    SampleResult,
    summarize_results,
)

FINGERPRINT = "a" * 64


def _completed(task_id: str, *, correct: bool, normalized: bool = False) -> SampleResult:
    evidence = EvidenceIR(task_id=task_id, raw_digest=FINGERPRINT)
    verdict = Verdict(
        task_id=task_id,
        status=VerdictStatus.CORRECT if correct else VerdictStatus.INCORRECT,
        reason="exact match" if correct else "semantic mismatch",
        task_fingerprint=FINGERPRINT,
        oracle_fingerprint=FINGERPRINT,
        evidence_fingerprint=FINGERPRINT,
        comparator_fingerprint=FINGERPRINT,
    )
    return SampleResult(
        task_id=task_id,
        task_fingerprint=FINGERPRINT,
        oracle_fingerprint=FINGERPRINT,
        execution_class=ExecutionClass.SUCCESS,
        outcome=SampleOutcomeCode.COMPLETED,
        reasoning_correct=correct,
        output_compliant=True,
        output_normalized=normalized,
        evidence=evidence,
        verdict=verdict,
    )


def _failure(
    task_id: str,
    *,
    execution_class: ExecutionClass,
    outcome: SampleOutcomeCode,
    output_compliant: bool | None,
) -> SampleResult:
    return SampleResult(
        task_id=task_id,
        task_fingerprint=FINGERPRINT,
        oracle_fingerprint=FINGERPRINT,
        execution_class=execution_class,
        outcome=outcome,
        output_compliant=output_compliant,
    )


def test_summary_separates_reasoning_delivery_and_end_to_end_success() -> None:
    results = (
        _completed("correct", correct=True, normalized=True),
        _completed("incorrect", correct=False),
        _failure(
            "invalid",
            execution_class=ExecutionClass.MODEL_FAILURE,
            outcome=SampleOutcomeCode.OUTPUT_INVALID,
            output_compliant=False,
        ),
        _failure(
            "proof",
            execution_class=ExecutionClass.PROOF_FAILURE,
            outcome=SampleOutcomeCode.PROOF_INSUFFICIENT,
            output_compliant=True,
        ),
        _failure(
            "infra",
            execution_class=ExecutionClass.INFRA_FAILURE,
            outcome=SampleOutcomeCode.INFRA_ERROR,
            output_compliant=None,
        ),
    )

    summary = summarize_results(tuple(item.task_id for item in results), results)

    assert summary.completed == 2
    assert summary.correct == 1
    assert summary.incorrect == 1
    assert summary.model_failures == 1
    assert summary.proof_failures == 1
    assert summary.infrastructure_failures == 1
    assert summary.reasoning_accuracy == 0.5
    assert summary.effective_accuracy == 0.2
    assert summary.output_compliant == 3
    assert summary.output_noncompliant == 1
    assert summary.output_normalized == 1
    assert summary.output_compliance_rate == 0.75
    assert summary.campaign_valid is False
    assert summary.invalid_reasons == ("UNRESOLVED_INFRASTRUCTURE",)


def test_ungradeable_model_failure_cannot_forge_reasoning_incorrect() -> None:
    with pytest.raises(ValidationError, match="no reasoning verdict"):
        SampleResult(
            task_id="invalid",
            task_fingerprint=FINGERPRINT,
            oracle_fingerprint=FINGERPRINT,
            execution_class=ExecutionClass.MODEL_FAILURE,
            outcome=SampleOutcomeCode.OUTPUT_INVALID,
            reasoning_correct=False,
            output_compliant=False,
        )


def test_normalization_is_only_valid_for_compliant_output() -> None:
    with pytest.raises(ValidationError, match="normalized output must be compliant"):
        SampleResult(
            task_id="invalid",
            task_fingerprint=FINGERPRINT,
            oracle_fingerprint=FINGERPRINT,
            execution_class=ExecutionClass.MODEL_FAILURE,
            outcome=SampleOutcomeCode.OUTPUT_INVALID,
            output_compliant=False,
            output_normalized=True,
        )


def test_public_rows_recompute_the_same_multidimensional_summary() -> None:
    samples = (
        _completed("correct", correct=True),
        _failure(
            "invalid",
            execution_class=ExecutionClass.MODEL_FAILURE,
            outcome=SampleOutcomeCode.OUTPUT_INVALID,
            output_compliant=False,
        ),
    )
    rows = tuple(
        PublicResultRow(
            product="simple",
            track=Track.DIRECT,
            task_id=sample.task_id,
            task_fingerprint=sample.task_fingerprint,
            execution_class=sample.execution_class,
            outcome=sample.outcome,
            reasoning_correct=sample.reasoning_correct,
            output_compliant=sample.output_compliant,
            output_normalized=sample.output_normalized,
        )
        for sample in samples
    )

    assert _summarize_public_rows(rows) == summarize_results(
        tuple(sample.task_id for sample in samples),
        samples,
    )
