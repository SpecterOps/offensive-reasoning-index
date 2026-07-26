"""Shared v2 offline scoring and exact campaign accounting.

This module is intentionally provider-agnostic.  Runtime adapters may project
direct, MCP, or replayed answers into :class:`EvidenceIR`, but every gradeable
sample reaches the same comparator and every campaign is reconciled against the
sealed public catalog.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, model_validator

from .comparator import COMPARATOR_FINGERPRINT, compare
from .evidence import EvidenceNormalizationError, validate_and_normalize_evidence
from .fingerprint import canonical_sha256
from .identity import IdentityResolver
from .protocol import (
    OracleRegistry,
    OracleV2Artifact,
    PublicV2Artifact,
    V2ArtifactPair,
)
from .schema import (
    PROTOCOL_VERSION,
    EvidenceIR,
    ExecutionClass,
    StrictModel,
    Track,
    Verdict,
    VerdictStatus,
)

ANSWERS_ARTIFACT_VERSION = "ori-eval-answers-v2"
SCORING_ARTIFACT_VERSION = "ori-eval-scoring-v2"


class V2ScoringError(ValueError):
    """Raised when scorer inputs cannot belong to one certified v2 campaign."""


class SampleOutcomeCode(StrEnum):
    COMPLETED = "COMPLETED"
    OUTPUT_INVALID = "OUTPUT_INVALID"
    POLICY_REJECTED = "POLICY_REJECTED"
    QUERY_TIMEOUT = "QUERY_TIMEOUT"
    QUERY_ERROR = "QUERY_ERROR"
    INFRA_ERROR = "INFRA_ERROR"
    HARNESS_ERROR = "HARNESS_ERROR"
    CIRCUIT_OPEN = "CIRCUIT_OPEN"


class AnswerSubmission(StrictModel):
    task_id: str
    task_fingerprint: str
    answer: dict[str, Any]


class AnswersV2Artifact(StrictModel):
    schema_version: Literal["ori-eval-answers-v2"] = ANSWERS_ARTIFACT_VERSION
    protocol_version: Literal["ori-eval-protocol-v2"] = PROTOCOL_VERSION
    public_artifact_fingerprint: str
    catalog_fingerprint: str
    answers: tuple[AnswerSubmission, ...]
    artifact_fingerprint: str

    @model_validator(mode="after")
    def fingerprint_matches(self) -> AnswersV2Artifact:
        expected = canonical_sha256(self, exclude_fields=("artifact_fingerprint",))
        if self.artifact_fingerprint != expected:
            raise ValueError("answer artifact fingerprint mismatch")
        return self


class SampleResult(StrictModel):
    task_id: str
    task_fingerprint: str
    oracle_fingerprint: str
    execution_class: ExecutionClass
    outcome: SampleOutcomeCode
    reasoning_correct: bool | None = Field(default=None, strict=True)
    evidence: EvidenceIR | None = None
    verdict: Verdict | None = None
    detail: str | None = None

    @model_validator(mode="after")
    def execution_and_reasoning_are_separate(self) -> SampleResult:
        if self.execution_class is ExecutionClass.SUCCESS:
            if self.outcome is not SampleOutcomeCode.COMPLETED:
                raise ValueError("successful samples must use COMPLETED")
            if self.evidence is None or self.verdict is None:
                raise ValueError("successful samples require evidence and a verdict")
            if self.reasoning_correct is not (
                self.verdict.status is VerdictStatus.CORRECT
            ):
                raise ValueError("reasoning correctness must match comparator verdict")
        elif self.execution_class in {
            ExecutionClass.INFRA_FAILURE,
            ExecutionClass.HARNESS_FAILURE,
            ExecutionClass.UNEXECUTED,
        }:
            if self.reasoning_correct is not None or self.verdict is not None:
                raise ValueError(
                    "infrastructure, harness, and unexecuted samples have no reasoning verdict"
                )
        elif self.execution_class is ExecutionClass.MODEL_FAILURE:
            if self.reasoning_correct is not False:
                raise ValueError("model-attributable failures count as incorrect")
            if self.verdict is not None:
                raise ValueError("ungradeable model failures cannot forge a comparator verdict")
        return self


class CampaignSummary(StrictModel):
    scheduled: int = Field(strict=True, ge=0)
    completed: int = Field(strict=True, ge=0)
    correct: int = Field(strict=True, ge=0)
    incorrect: int = Field(strict=True, ge=0)
    model_failures: int = Field(strict=True, ge=0)
    infrastructure_failures: int = Field(strict=True, ge=0)
    harness_failures: int = Field(strict=True, ge=0)
    unexecuted: int = Field(strict=True, ge=0)
    reasoning_accuracy: float | None = Field(default=None, strict=True, ge=0, le=1)
    effective_accuracy: float | None = Field(default=None, strict=True, ge=0, le=1)
    campaign_valid: bool
    invalid_reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def counts_reconcile(self) -> CampaignSummary:
        if (
            self.completed
            + self.model_failures
            + self.infrastructure_failures
            + self.harness_failures
            + self.unexecuted
            != self.scheduled
        ):
            raise ValueError("campaign outcome counts do not match scheduled tasks")
        if self.correct + self.incorrect != self.completed + self.model_failures:
            raise ValueError("reasoning denominator does not match gradeable/model failures")
        return self


class ScoringV2Artifact(StrictModel):
    schema_version: Literal["ori-eval-scoring-v2"] = SCORING_ARTIFACT_VERSION
    protocol_version: Literal["ori-eval-protocol-v2"] = PROTOCOL_VERSION
    product: str
    track: Track
    public_artifact_fingerprint: str
    oracle_artifact_fingerprint: str
    answer_artifact_fingerprint: str
    graph_fingerprint: str
    compiler_fingerprint: str
    comparator_fingerprint: str
    catalog_fingerprint: str
    results: tuple[SampleResult, ...]
    summary: CampaignSummary
    artifact_fingerprint: str

    @model_validator(mode="after")
    def fingerprint_matches(self) -> ScoringV2Artifact:
        expected = canonical_sha256(self, exclude_fields=("artifact_fingerprint",))
        if self.artifact_fingerprint != expected:
            raise ValueError("scoring artifact fingerprint mismatch")
        return self


def build_answers_artifact(
    public: PublicV2Artifact,
    answers: Mapping[str, Mapping[str, Any]]
    | Sequence[AnswerSubmission],
) -> AnswersV2Artifact:
    """Bind structured answers to one immutable public task catalog."""

    if isinstance(answers, Mapping):
        task_by_id = {task.task_id: task for task in public.tasks}
        unknown = sorted(set(answers) - set(task_by_id))
        if unknown:
            raise V2ScoringError(f"answers contain unknown task IDs: {unknown}")
        submissions = tuple(
            AnswerSubmission(
                task_id=task_id,
                task_fingerprint=task_by_id[task_id].task_fingerprint,
                answer=dict(answer),
            )
            for task_id, answer in sorted(answers.items())
        )
    else:
        submissions = tuple(answers)

    payload = {
        "public_artifact_fingerprint": public.artifact_fingerprint,
        "catalog_fingerprint": public.catalog_fingerprint,
        "answers": submissions,
        "artifact_fingerprint": "0" * 64,
    }
    payload["artifact_fingerprint"] = canonical_sha256(
        {
            "schema_version": ANSWERS_ARTIFACT_VERSION,
            "protocol_version": PROTOCOL_VERSION,
            **payload,
        },
        exclude_fields=("artifact_fingerprint",),
    )
    return AnswersV2Artifact.model_validate(payload)


def _validate_artifact_bindings(
    public: PublicV2Artifact,
    private: OracleV2Artifact,
    answers: AnswersV2Artifact,
) -> None:
    try:
        V2ArtifactPair(public=public, private=private)
    except ValueError as exc:
        raise V2ScoringError(str(exc)) from exc
    mismatches: list[str] = []
    if answers.public_artifact_fingerprint != public.artifact_fingerprint:
        mismatches.append("answer/public artifact")
    if answers.catalog_fingerprint != public.catalog_fingerprint:
        mismatches.append("answer/public catalog")
    if mismatches:
        raise V2ScoringError(
            "v2 scoring artifact fingerprint mismatch: " + ", ".join(mismatches)
        )


def _exact_submissions(
    public: PublicV2Artifact,
    answers: AnswersV2Artifact,
) -> dict[str, AnswerSubmission]:
    task_by_id = {task.task_id: task for task in public.tasks}
    answer_counts = Counter(item.task_id for item in answers.answers)
    duplicates = sorted(task_id for task_id, count in answer_counts.items() if count != 1)
    missing = sorted(set(task_by_id) - set(answer_counts))
    unknown = sorted(set(answer_counts) - set(task_by_id))
    if duplicates or missing or unknown:
        raise V2ScoringError(
            "answer accounting failed: "
            f"missing={missing} duplicates={duplicates} unknown={unknown}"
        )

    submissions = {item.task_id: item for item in answers.answers}
    fingerprint_mismatches = sorted(
        task_id
        for task_id, submission in submissions.items()
        if submission.task_fingerprint != task_by_id[task_id].task_fingerprint
    )
    if fingerprint_mismatches:
        raise V2ScoringError(
            "answer task fingerprint mismatch: " + ", ".join(fingerprint_mismatches)
        )
    return submissions


def summarize_results(
    expected_task_ids: Sequence[str],
    results: Sequence[SampleResult],
) -> CampaignSummary:
    """Reconcile exactly one terminal sample result per scheduled task."""

    expected = tuple(expected_task_ids)
    if len(expected) != len(set(expected)):
        raise V2ScoringError("certified catalog contains duplicate task IDs")
    counts = Counter(result.task_id for result in results)
    duplicates = sorted(task_id for task_id, count in counts.items() if count != 1)
    missing = sorted(set(expected) - set(counts))
    unknown = sorted(set(counts) - set(expected))
    if duplicates or missing or unknown:
        raise V2ScoringError(
            "result accounting failed: "
            f"missing={missing} duplicates={duplicates} unknown={unknown}"
        )

    completed = sum(
        result.execution_class is ExecutionClass.SUCCESS for result in results
    )
    model_failures = sum(
        result.execution_class is ExecutionClass.MODEL_FAILURE for result in results
    )
    infrastructure_failures = sum(
        result.execution_class is ExecutionClass.INFRA_FAILURE for result in results
    )
    harness_failures = sum(
        result.execution_class is ExecutionClass.HARNESS_FAILURE for result in results
    )
    unexecuted = sum(
        result.execution_class is ExecutionClass.UNEXECUTED for result in results
    )
    correct = sum(result.reasoning_correct is True for result in results)
    incorrect = sum(result.reasoning_correct is False for result in results)
    reasoning_denominator = correct + incorrect
    invalid_reasons: list[str] = []
    if infrastructure_failures:
        invalid_reasons.append("UNRESOLVED_INFRASTRUCTURE")
    if harness_failures:
        invalid_reasons.append("HARNESS_FAILURE")
    if unexecuted:
        invalid_reasons.append("UNEXECUTED_TASK")

    return CampaignSummary(
        scheduled=len(expected),
        completed=completed,
        correct=correct,
        incorrect=incorrect,
        model_failures=model_failures,
        infrastructure_failures=infrastructure_failures,
        harness_failures=harness_failures,
        unexecuted=unexecuted,
        reasoning_accuracy=(
            correct / reasoning_denominator if reasoning_denominator else None
        ),
        effective_accuracy=correct / len(expected) if expected else None,
        campaign_valid=not invalid_reasons,
        invalid_reasons=tuple(invalid_reasons),
    )


def score_answers_v2(
    public: PublicV2Artifact,
    private: OracleV2Artifact,
    answers: AnswersV2Artifact,
) -> ScoringV2Artifact:
    """Score a complete replay using sealed identities and the shared comparator."""

    _validate_artifact_bindings(public, private, answers)
    submissions = _exact_submissions(public, answers)
    resolver = IdentityResolver(private.identity_catalog)
    registry = OracleRegistry(private)

    results: list[SampleResult] = []
    for task in public.tasks:
        oracle = registry.for_task(task.task_id)
        submission = submissions[task.task_id]
        try:
            evidence = validate_and_normalize_evidence(
                submission.answer,
                answer_schema=task.answer_schema,
                resolver=resolver,
                task_id=task.task_id,
            )
        except (EvidenceNormalizationError, ValueError) as exc:
            results.append(
                SampleResult(
                    task_id=task.task_id,
                    task_fingerprint=task.task_fingerprint,
                    oracle_fingerprint=oracle.oracle_fingerprint,
                    execution_class=ExecutionClass.MODEL_FAILURE,
                    outcome=SampleOutcomeCode.OUTPUT_INVALID,
                    reasoning_correct=False,
                    detail=str(exc),
                )
            )
            continue

        try:
            verdict = compare(task.answer_policy, oracle, evidence)
        except Exception as exc:
            results.append(
                SampleResult(
                    task_id=task.task_id,
                    task_fingerprint=task.task_fingerprint,
                    oracle_fingerprint=oracle.oracle_fingerprint,
                    execution_class=ExecutionClass.HARNESS_FAILURE,
                    outcome=SampleOutcomeCode.HARNESS_ERROR,
                    detail=f"{type(exc).__name__}: {exc}",
                )
            )
            continue
        results.append(
            SampleResult(
                task_id=task.task_id,
                task_fingerprint=task.task_fingerprint,
                oracle_fingerprint=oracle.oracle_fingerprint,
                execution_class=ExecutionClass.SUCCESS,
                outcome=SampleOutcomeCode.COMPLETED,
                reasoning_correct=verdict.status is VerdictStatus.CORRECT,
                evidence=evidence,
                verdict=verdict,
            )
        )

    summary = summarize_results(
        tuple(task.task_id for task in public.tasks),
        results,
    )
    payload = {
        "product": public.product,
        "track": public.track,
        "public_artifact_fingerprint": public.artifact_fingerprint,
        "oracle_artifact_fingerprint": private.artifact_fingerprint,
        "answer_artifact_fingerprint": answers.artifact_fingerprint,
        "graph_fingerprint": public.graph_fingerprint,
        "compiler_fingerprint": public.compiler_fingerprint,
        "comparator_fingerprint": COMPARATOR_FINGERPRINT,
        "catalog_fingerprint": public.catalog_fingerprint,
        "results": tuple(results),
        "summary": summary,
        "artifact_fingerprint": "0" * 64,
    }
    payload["artifact_fingerprint"] = canonical_sha256(
        {
            "schema_version": SCORING_ARTIFACT_VERSION,
            "protocol_version": PROTOCOL_VERSION,
            **payload,
        },
        exclude_fields=("artifact_fingerprint",),
    )
    return ScoringV2Artifact.model_validate(payload)
