"""Private checkpointing and redacted public reporting for v2 campaigns."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .comparator import COMPARATOR_FINGERPRINT
from .fingerprint import canonical_sha256
from .mcp import MCP_FINALIZATION_POLICY_FINGERPRINT
from .native_capability import NativeCapabilityProfile, validate_native_capability_profile
from .native_proof import validate_native_task_binding
from .profiles import validate_capability_profile
from .protocol import V2ArtifactPair
from .schema import (
    PROTOCOL_VERSION,
    CapabilityProfile,
    ExecutionClass,
    NativeClaimEvidenceContract,
    StrictModel,
    TaskCertification,
    Track,
)
from .scoring import (
    CampaignSummary,
    SampleOutcomeCode,
    SampleResult,
    summarize_results,
)

CHECKPOINT_SCHEMA_VERSION = "ori-eval-checkpoint-v2"
PUBLIC_REPORT_SCHEMA_VERSION = "ori-eval-public-report-v3"
RUN_PROVENANCE_SCHEMA_VERSION = "ori-eval-run-provenance-v2"


class CampaignArtifactError(ValueError):
    """Raised when resume/report state is stale, mixed, or incomplete."""


class RunIdentity(StrictModel):
    provider: str
    model: str
    run_index: int = Field(strict=True, ge=1)
    target_fingerprint: str
    tool_loop: str | None = None


class CheckpointTaskBinding(StrictModel):
    task_id: str
    task_fingerprint: str
    oracle_fingerprint: str
    bounds_fingerprint: str


class CheckpointV2(StrictModel):
    schema_version: Literal["ori-eval-checkpoint-v2"] = CHECKPOINT_SCHEMA_VERSION
    protocol_version: Literal["ori-eval-protocol-v2"] = PROTOCOL_VERSION
    product: str
    track: Track
    public_artifact_fingerprint: str
    oracle_artifact_fingerprint: str
    catalog_fingerprint: str
    graph_fingerprint: str
    compiler_fingerprint: str
    comparator_fingerprint: str
    capability_profile_id: str
    capability_profile_fingerprint: str
    containment_policy_version: str | None = None
    finalization_policy_fingerprint: str | None = None
    run_identity: RunIdentity
    task_bindings: tuple[CheckpointTaskBinding, ...]
    results: tuple[SampleResult, ...] = ()
    checkpoint_fingerprint: str

    @model_validator(mode="after")
    def checkpoint_is_exact(self) -> CheckpointV2:
        task_ids = [item.task_id for item in self.task_bindings]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("checkpoint task bindings contain duplicate IDs")
        result_counts = Counter(result.task_id for result in self.results)
        duplicates = sorted(
            task_id for task_id, count in result_counts.items() if count != 1
        )
        unknown = sorted(set(result_counts) - set(task_ids))
        if duplicates or unknown:
            raise ValueError(
                f"checkpoint result accounting failed: duplicates={duplicates} "
                f"unknown={unknown}"
            )
        binding_by_id = {item.task_id: item for item in self.task_bindings}
        for result in self.results:
            if (
                result.task_fingerprint
                != binding_by_id[result.task_id].task_fingerprint
            ):
                raise ValueError(
                    f"checkpoint result {result.task_id} has a stale task fingerprint"
                )
            if (
                result.oracle_fingerprint
                != binding_by_id[result.task_id].oracle_fingerprint
            ):
                raise ValueError(
                    f"checkpoint result {result.task_id} has a stale oracle fingerprint"
                )
        expected = canonical_sha256(
            self,
            exclude_fields=("checkpoint_fingerprint",),
        )
        if self.checkpoint_fingerprint != expected:
            raise ValueError("checkpoint fingerprint mismatch")
        return self


class PublicResultRow(StrictModel):
    protocol_version: Literal["ori-eval-protocol-v2"] = PROTOCOL_VERSION
    product: str
    track: Track
    task_id: str
    task_fingerprint: str
    execution_class: ExecutionClass
    outcome: SampleOutcomeCode
    reasoning_correct: bool | None = Field(default=None, strict=True)
    output_compliant: bool | None = Field(default=None, strict=True)
    output_normalized: bool = Field(default=False, strict=True)
    verdict_reason: str | None = None
    certification_fingerprint: str | None = None

    @model_validator(mode="after")
    def outcome_dimensions_are_coherent(self) -> PublicResultRow:
        if self.output_normalized and self.output_compliant is not True:
            raise ValueError("normalized output must be compliant")
        if self.execution_class is ExecutionClass.SUCCESS:
            if self.outcome is not SampleOutcomeCode.COMPLETED:
                raise ValueError("successful public rows must use COMPLETED")
            if self.reasoning_correct is None:
                raise ValueError("successful public rows require a reasoning verdict")
            if self.output_compliant is not True:
                raise ValueError("successful public rows require compliant output")
        elif self.reasoning_correct is not None:
            raise ValueError(
                "only comparator-graded successful rows have reasoning verdicts"
            )
        return self


class PublicReportV2(StrictModel):
    schema_version: Literal["ori-eval-public-report-v3"] = PUBLIC_REPORT_SCHEMA_VERSION
    protocol_version: Literal["ori-eval-protocol-v2"] = PROTOCOL_VERSION
    product: str
    track: Track
    public_artifact_fingerprint: str
    catalog_fingerprint: str
    graph_fingerprint: str
    capability_profile_fingerprint: str
    rows: tuple[PublicResultRow, ...]
    summary: CampaignSummary
    report_fingerprint: str

    @model_validator(mode="after")
    def report_is_exact_and_redacted(self) -> PublicReportV2:
        row_ids = [row.task_id for row in self.rows]
        if len(row_ids) != len(set(row_ids)):
            raise ValueError("public report contains duplicate task rows")
        if len(row_ids) != self.summary.scheduled:
            raise ValueError("public report row count does not match summary")
        expected_summary = _summarize_public_rows(self.rows)
        if self.summary != expected_summary:
            raise ValueError("public report summary does not match its rows")
        expected = canonical_sha256(
            self,
            exclude_fields=("report_fingerprint",),
        )
        if self.report_fingerprint != expected:
            raise ValueError("public report fingerprint mismatch")
        _assert_public_report(self.model_dump(mode="json"))
        return self


def _summarize_public_rows(rows: Sequence[PublicResultRow]) -> CampaignSummary:
    """Recompute every public summary dimension from redacted task rows."""

    classes = Counter(row.execution_class for row in rows)
    correct = sum(row.reasoning_correct is True for row in rows)
    incorrect = sum(row.reasoning_correct is False for row in rows)
    reasoning_denominator = correct + incorrect
    output_compliant = sum(row.output_compliant is True for row in rows)
    output_noncompliant = sum(row.output_compliant is False for row in rows)
    output_normalized = sum(row.output_normalized for row in rows)
    observed_outputs = output_compliant + output_noncompliant
    infrastructure = classes[ExecutionClass.INFRA_FAILURE]
    harness = classes[ExecutionClass.HARNESS_FAILURE]
    unexecuted = classes[ExecutionClass.UNEXECUTED]
    invalid_reasons: list[str] = []
    if infrastructure:
        invalid_reasons.append("UNRESOLVED_INFRASTRUCTURE")
    if harness:
        invalid_reasons.append("HARNESS_FAILURE")
    if unexecuted:
        invalid_reasons.append("UNEXECUTED_TASK")
    return CampaignSummary(
        scheduled=len(rows),
        completed=classes[ExecutionClass.SUCCESS],
        correct=correct,
        incorrect=incorrect,
        model_failures=classes[ExecutionClass.MODEL_FAILURE],
        proof_failures=classes[ExecutionClass.PROOF_FAILURE],
        infrastructure_failures=infrastructure,
        harness_failures=harness,
        unexecuted=unexecuted,
        output_compliant=output_compliant,
        output_noncompliant=output_noncompliant,
        output_normalized=output_normalized,
        reasoning_accuracy=(
            correct / reasoning_denominator if reasoning_denominator else None
        ),
        effective_accuracy=(correct / len(rows) if rows else None),
        output_compliance_rate=(
            output_compliant / observed_outputs if observed_outputs else None
        ),
        campaign_valid=not invalid_reasons,
        invalid_reasons=tuple(invalid_reasons),
    )


class RunProvenanceV2(StrictModel):
    schema_version: Literal["ori-eval-run-provenance-v2"] = (
        RUN_PROVENANCE_SCHEMA_VERSION
    )
    protocol_version: Literal["ori-eval-protocol-v2"] = PROTOCOL_VERSION
    product: str
    track: Track
    public_artifact_fingerprint: str
    oracle_artifact_fingerprint: str
    catalog_fingerprint: str
    graph_fingerprint: str
    compiler_fingerprint: str
    comparator_fingerprint: str
    capability_profile_fingerprint: str
    provenance_fingerprint: str

    @model_validator(mode="after")
    def fingerprint_matches(self) -> RunProvenanceV2:
        expected = canonical_sha256(
            self,
            exclude_fields=("provenance_fingerprint",),
        )
        if self.provenance_fingerprint != expected:
            raise ValueError("run provenance fingerprint mismatch")
        return self


_PUBLIC_REPORT_FORBIDDEN_KEYS = frozenset(
    {
        "answer_artifact_fingerprint",
        "evidence",
        "expected_entities",
        "oracle_artifact_fingerprint",
        "oracle_fingerprint",
        "oracle_id",
        "reference_cypher",
        "route_variants",
    }
)


def _assert_public_report(value: object, *, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if str(key).casefold() in _PUBLIC_REPORT_FORBIDDEN_KEYS:
                raise CampaignArtifactError(
                    f"private scorer field reached public report at {path}.{key}"
                )
            _assert_public_report(nested, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            _assert_public_report(nested, path=f"{path}[{index}]")


def _task_bindings(pair: V2ArtifactPair) -> tuple[CheckpointTaskBinding, ...]:
    oracle_by_task = {
        oracle.task_id: oracle for oracle in pair.private.oracles
    }
    return tuple(
        CheckpointTaskBinding(
            task_id=task.task_id,
            task_fingerprint=task.task_fingerprint,
            oracle_fingerprint=oracle_by_task[task.task_id].oracle_fingerprint,
            bounds_fingerprint=canonical_sha256(task.binding.bounds),
        )
        for task in pair.public.tasks
    )


def build_checkpoint(
    pair: V2ArtifactPair,
    profile: CapabilityProfile | NativeCapabilityProfile,
    run_identity: RunIdentity,
    *,
    results: Sequence[SampleResult] = (),
) -> CheckpointV2:
    """Build one private, fully fingerprint-bound resume checkpoint."""

    _validate_pair_profile(pair, profile)
    if pair.public.track is not profile.track:
        raise CampaignArtifactError("checkpoint capability profile track mismatch")
    if any(
        task.binding.capability_profile_id != profile.profile_id
        for task in pair.public.tasks
    ):
        raise CampaignArtifactError("checkpoint task capability profile mismatch")
    payload = {
        "product": pair.public.product,
        "track": pair.public.track,
        "public_artifact_fingerprint": pair.public.artifact_fingerprint,
        "oracle_artifact_fingerprint": pair.private.artifact_fingerprint,
        "catalog_fingerprint": pair.public.catalog_fingerprint,
        "graph_fingerprint": pair.public.graph_fingerprint,
        "compiler_fingerprint": pair.public.compiler_fingerprint,
        "comparator_fingerprint": COMPARATOR_FINGERPRINT,
        "capability_profile_id": profile.profile_id,
        "capability_profile_fingerprint": profile.profile_fingerprint,
        "containment_policy_version": (
            profile.direct_query_policy_version
            if profile.track is Track.DIRECT
            else None
        ),
        "finalization_policy_fingerprint": (
            MCP_FINALIZATION_POLICY_FINGERPRINT
            if profile.track is Track.MCP
            else None
        ),
        "run_identity": run_identity,
        "task_bindings": _task_bindings(pair),
        "results": tuple(results),
        "checkpoint_fingerprint": "0" * 64,
    }
    payload["checkpoint_fingerprint"] = canonical_sha256(
        {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "protocol_version": PROTOCOL_VERSION,
            **payload,
        },
        exclude_fields=("checkpoint_fingerprint",),
    )
    return CheckpointV2.model_validate(payload)


def validate_checkpoint(
    checkpoint: CheckpointV2,
    pair: V2ArtifactPair,
    profile: CapabilityProfile | NativeCapabilityProfile,
    run_identity: RunIdentity,
) -> CheckpointV2:
    """Reject stale checkpoints before any task is scheduled."""

    expected = build_checkpoint(
        pair,
        profile,
        run_identity,
        results=checkpoint.results,
    )
    compared_fields = (
        "product",
        "track",
        "public_artifact_fingerprint",
        "oracle_artifact_fingerprint",
        "catalog_fingerprint",
        "graph_fingerprint",
        "compiler_fingerprint",
        "comparator_fingerprint",
        "capability_profile_id",
        "capability_profile_fingerprint",
        "containment_policy_version",
        "finalization_policy_fingerprint",
        "run_identity",
        "task_bindings",
    )
    stale = [
        field
        for field in compared_fields
        if getattr(checkpoint, field) != getattr(expected, field)
    ]
    if stale:
        raise CampaignArtifactError(
            "checkpoint is incompatible with this v2 run: " + ", ".join(stale)
        )
    return checkpoint


def build_public_report(
    pair: V2ArtifactPair,
    profile: CapabilityProfile | NativeCapabilityProfile,
    results: Sequence[SampleResult],
    summary: CampaignSummary,
    *,
    certifications: Mapping[str, TaskCertification] | None = None,
    scheduled_task_ids: Sequence[str] | None = None,
) -> PublicReportV2:
    """Redact scorer-only state while preserving exact public accounting."""

    _validate_pair_profile(pair, profile)
    task_by_id = {task.task_id: task for task in pair.public.tasks}
    scheduled = (
        tuple(task_by_id)
        if scheduled_task_ids is None
        else tuple(scheduled_task_ids)
    )
    if len(scheduled) != len(set(scheduled)):
        raise CampaignArtifactError("public report schedule contains duplicate task IDs")
    unknown_scheduled = sorted(set(scheduled) - set(task_by_id))
    if unknown_scheduled:
        raise CampaignArtifactError(
            "public report schedule contains unknown task IDs: "
            + ", ".join(unknown_scheduled)
        )
    expected_summary = summarize_results(scheduled, results)
    if summary != expected_summary:
        raise CampaignArtifactError("public report summary does not match exact results")
    stale = sorted(
        result.task_id
        for result in results
        if result.task_fingerprint != task_by_id[result.task_id].task_fingerprint
    )
    if stale:
        raise CampaignArtifactError(
            "public report contains stale task fingerprints: " + ", ".join(stale)
        )
    certifications = certifications or {}
    rows = tuple(
        PublicResultRow(
            product=pair.public.product,
            track=pair.public.track,
            task_id=result.task_id,
            task_fingerprint=result.task_fingerprint,
            execution_class=result.execution_class,
            outcome=result.outcome,
            reasoning_correct=result.reasoning_correct,
            output_compliant=result.output_compliant,
            output_normalized=result.output_normalized,
            verdict_reason=result.verdict.reason if result.verdict else None,
            certification_fingerprint=(
                certifications[result.task_id].certification_fingerprint
                if result.task_id in certifications
                else None
            ),
        )
        for result in sorted(results, key=lambda item: item.task_id)
    )
    payload = {
        "product": pair.public.product,
        "track": pair.public.track,
        "public_artifact_fingerprint": pair.public.artifact_fingerprint,
        "catalog_fingerprint": pair.public.catalog_fingerprint,
        "graph_fingerprint": pair.public.graph_fingerprint,
        "capability_profile_fingerprint": profile.profile_fingerprint,
        "rows": rows,
        "summary": summary,
        "report_fingerprint": "0" * 64,
    }
    payload["report_fingerprint"] = canonical_sha256(
        {
            "schema_version": PUBLIC_REPORT_SCHEMA_VERSION,
            "protocol_version": PROTOCOL_VERSION,
            **payload,
        },
        exclude_fields=("report_fingerprint",),
    )
    return PublicReportV2.model_validate(payload)


def _validate_pair_profile(
    pair: V2ArtifactPair,
    profile: CapabilityProfile | NativeCapabilityProfile,
) -> None:
    if isinstance(profile, NativeCapabilityProfile):
        validate_native_capability_profile(profile)
        if pair.public.track is not Track.MCP:
            raise CampaignArtifactError("native provenance requires an MCP task catalog")
        for task in pair.public.tasks:
            validate_native_task_binding(profile, task)
    else:
        validate_capability_profile(profile)
        if any(isinstance(task.binding.mcp_evidence_contract, NativeClaimEvidenceContract)
               for task in pair.public.tasks):
            raise CampaignArtifactError("native task provenance requires its native profile")


def build_run_provenance(
    pair: V2ArtifactPair,
    profile: CapabilityProfile | NativeCapabilityProfile,
) -> RunProvenanceV2:
    _validate_pair_profile(pair, profile)
    payload = {
        "product": pair.public.product,
        "track": pair.public.track,
        "public_artifact_fingerprint": pair.public.artifact_fingerprint,
        "oracle_artifact_fingerprint": pair.private.artifact_fingerprint,
        "catalog_fingerprint": pair.public.catalog_fingerprint,
        "graph_fingerprint": pair.public.graph_fingerprint,
        "compiler_fingerprint": pair.public.compiler_fingerprint,
        "comparator_fingerprint": COMPARATOR_FINGERPRINT,
        "capability_profile_fingerprint": profile.profile_fingerprint,
        "provenance_fingerprint": "0" * 64,
    }
    payload["provenance_fingerprint"] = canonical_sha256(
        {
            "schema_version": RUN_PROVENANCE_SCHEMA_VERSION,
            "protocol_version": PROTOCOL_VERSION,
            **payload,
        },
        exclude_fields=("provenance_fingerprint",),
    )
    return RunProvenanceV2.model_validate(payload)


def guard_v2_output_directory(
    output_dir: Path,
    provenance: RunProvenanceV2,
) -> Path:
    """Create or validate the v2 provenance guard for one output directory."""

    output_dir.mkdir(parents=True, exist_ok=True)
    guard = output_dir / "campaign-provenance-v2.json"
    if guard.exists():
        existing = RunProvenanceV2.model_validate_json(guard.read_text())
        if existing != provenance:
            raise CampaignArtifactError(
                "output directory contains different v2 campaign provenance"
            )
        return guard
    existing_entries = tuple(path.name for path in output_dir.iterdir())
    if existing_entries:
        raise CampaignArtifactError(
            "non-empty output directory lacks v2 campaign provenance"
        )
    guard.write_text(
        json.dumps(provenance.model_dump(mode="json"), indent=2, sort_keys=True)
        + "\n"
    )
    return guard
