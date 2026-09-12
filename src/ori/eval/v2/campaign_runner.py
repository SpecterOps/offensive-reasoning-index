"""Fail-closed, model-backed campaign orchestration for protocol V2."""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import math
import os
import signal
import stat
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

from pydantic import Field, model_validator

from ori.eval.adapter import call_provider_text
from ori.eval.anthropic_binding import (
    AnthropicBinding,
    anthropic_binding_identity,
    anthropic_private_headers,
    prepare_anthropic_binding,
)
from ori.eval.bhce import BHCEClient, parse_bhce_url, resolve_bhce_target
from ori.eval.codex_oauth import (
    codex_endpoint_binding,
    codex_request_base_url,
    resolve_codex_credential,
)
from ori.eval.direct_query_safety import (
    DirectQueryCoordinator,
    DirectQuerySafetyConfig,
    QueryDenyCache,
)
from ori.eval.mcp_runtime import _load_bloodhound_mcp_bundle
from ori.eval.ollama_binding import (
    OllamaEndpoint,
    ollama_model_name,
    prepare_ollama_endpoint,
)
from ori.eval.provider_auth import (
    official_openai_endpoint_is_secure,
    openai_compat_endpoint_is_local,
    resolve_openai_compat_credential,
)
from ori.eval.provider_contract import (
    GEMINI_OPENAI_BASE_URL,
    ProviderApiSurface,
    ProviderContractError,
    resolve_api_surface,
    validate_release1_api_surface,
)
from ori.mcp_launcher import (
    MCPLauncherConfig,
    MCPLauncherRuntime,
    resolve_mcp_launcher_runtime,
)

from .campaign import (
    CheckpointV2,
    PublicReportV2,
    RunIdentity,
    RunProvenanceV2,
    build_checkpoint,
    build_public_report,
    build_run_provenance,
    validate_checkpoint,
)
from .campaign_config import (
    ReasoningEffort,
    ResolvedV2CampaignConfig,
    ResolvedV2TrackPaths,
    StructuredOutputMode,
    V2ModelEntry,
    load_v2_campaign_config,
)
from .certification import LiveCertificationCatalog
from .fingerprint import canonical_sha256, certifier_fingerprint
from .graph import (
    GraphSnapshot,
    LiveGraphVerification,
    build_archive_snapshot,
    collect_live_snapshot,
    require_live_graph_match,
)
from .identity import IdentityResolver
from .mcp import EvidenceEventKind, MCPToolLoop
from .model_runtime import (
    ProviderRunRecord,
    V2ModelTaskCancelled,
    contain_model_runtime_exception,
    run_direct_model_task_v2,
    run_mcp_model_task_v2,
    unexecuted_model_record,
)
from .profiles import capability_profile_for_track
from .protocol import OracleRegistry, V2ArtifactPair, load_v2_pair
from .schema import (
    PROTOCOL_VERSION,
    CapabilityProfile,
    CatalogRelease,
    CertificationState,
    ExecutionClass,
    StrictModel,
    TaskCertification,
    Track,
)
from .scoring import SampleOutcomeCode, SampleResult, summarize_results

RUNNER_VERSION = "ori-v2-model-campaign-v16"
RUN_STATE_SCHEMA_VERSION = "ori-v2-private-run-state-v7"
RUN_STATE_NAME = "run-state-v7.private.json"
MODEL_REPORT_SCHEMA_VERSION = "ori-v2-model-report-v5"
READINESS_SCHEMA_VERSION = "ori-v2-run-readiness-v13"
CAMPAIGN_LIFECYCLE_SCHEMA_VERSION = "ori-v2-campaign-lifecycle-v3"
TRACK_COMPLETION_SCHEMA_VERSION = "ori-v2-track-completion-v2"
_RUNNER_IMPLEMENTATION_SOURCES = {
    "release_selection": Path(__file__).with_name("release_selection.py"),
    "diagnostic_selection": Path(__file__).with_name("diagnostic_selection.py"),
    "oaic_recipes": Path(__file__).with_name("oaic_recipes.py"),
    "adapter": Path(__file__).parent.parent / "adapter.py",
    "anthropic_binding": Path(__file__).parent.parent / "anthropic_binding.py",
    "bhce": Path(__file__).parent.parent / "bhce.py",
    "campaign": Path(__file__).with_name("campaign.py"),
    "campaign_runner": Path(__file__),
    "campaign_config": Path(__file__).with_name("campaign_config.py"),
    "codex_oauth": Path(__file__).parent.parent / "codex_oauth.py",
    "direct_adapter": Path(__file__).with_name("direct_adapter.py"),
    "direct_query_safety": Path(__file__).parent.parent / "direct_query_safety.py",
    "evidence": Path(__file__).with_name("evidence.py"),
    "identity": Path(__file__).with_name("identity.py"),
    "mcp_adapter": Path(__file__).with_name("mcp_adapter.py"),
    "mcp_launcher": Path(__file__).parent.parent.parent / "mcp_launcher.py",
    "mcp_state_machine": Path(__file__).with_name("mcp.py"),
    "model_runtime": Path(__file__).with_name("model_runtime.py"),
    "ollama_binding": Path(__file__).parent.parent / "ollama_binding.py",
    "output_compliance": Path(__file__).with_name("output_compliance.py"),
    "query_contract": Path(__file__).with_name("query_contract.py"),
    "provider_auth": Path(__file__).parent.parent / "provider_auth.py",
    "provider_contract": Path(__file__).parent.parent / "provider_contract.py",
    "provider_transport": Path(__file__).parent.parent / "provider_transport.py",
    "ollama_stream": Path(__file__).parent.parent / "ollama_stream.py",
    "provider_loops": Path(__file__).parent.parent / "mcp_runtime.py",
    "runtime": Path(__file__).with_name("runtime.py"),
    "schema": Path(__file__).with_name("schema.py"),
    "scoring": Path(__file__).with_name("scoring.py"),
}
RUNNER_IMPLEMENTATION_FINGERPRINT = canonical_sha256(
    {
        name: hashlib.sha256(path.read_bytes()).hexdigest()
        for name, path in sorted(_RUNNER_IMPLEMENTATION_SOURCES.items())
    }
)
ProgressReporter = Callable[[str], None]


class V2CampaignRunError(ValueError):
    """Raised before model work when campaign state is unsafe or inconsistent."""


def _emit_progress(progress: ProgressReporter | None, message: str) -> None:
    """Report model-blind operator progress without affecting campaign state."""

    if progress is None:
        return
    try:
        progress(message)
    except Exception:
        # Progress output must never change scoring or execution.
        return


def _graph_verification_progress(
    *,
    track: Track,
    stage: Literal["pre", "post"],
    receipt: LiveGraphVerification,
) -> str:
    return (
        f"[{track.value}] {stage}-track graph verified ({receipt.observed_graph_fingerprint[:12]})"
    )


def _display_outcome(sample: SampleResult) -> str:
    if sample.reasoning_correct is True:
        return "CORRECT"
    if sample.reasoning_correct is False and sample.outcome.value == "COMPLETED":
        return "INCORRECT"
    return sample.outcome.value


def _task_completion_progress(
    *,
    sample: SampleResult,
    provider: ProviderRunRecord,
    task_elapsed_seconds: float,
    results: Sequence[SampleResult],
) -> str:
    correct = sum(item.reasoning_correct is True for item in results)
    graded = sum(item.reasoning_correct is not None for item in results)
    infrastructure = sum(item.execution_class.value == "infra_failure" for item in results)
    proof_failures = sum(item.execution_class.value == "proof_failure" for item in results)
    score = (
        "1.0"
        if sample.reasoning_correct is True
        else "0.0"
        if sample.reasoning_correct is False
        else "n/a"
    )
    details = [
        f"score={score}",
        f"elapsed={task_elapsed_seconds:.1f}s",
        f"tokens={provider.tokens_input}+{provider.tokens_output}",
    ]
    if provider.surface.startswith("mcp"):
        tool_events = [event for event in provider.mcp_events if event.tool_name is not None]
        details.extend(
            (
                f"tools={len(tool_events)}",
                "cypher=" + str(sum(event.tool_name == "cypher_query" for event in tool_events)),
            )
        )
    details.extend(
        (
            f"running_correct={correct}/{graded}",
            f"proof={proof_failures}",
            f"infra={infrastructure}",
        )
    )
    return f"           → {_display_outcome(sample)} ({', '.join(details)})"


def _infrastructure_attempt_progress(
    *,
    sample: SampleResult,
    attempt_number: int,
    max_infra_retries: int,
    attempt_index: int | None = None,
) -> str:
    retry_index = attempt_number if attempt_index is None else attempt_index
    if retry_index <= max_infra_retries:
        state = "infrastructure healthy, retrying"
        arrow = "↻"
    else:
        state = "retry budget exhausted"
        arrow = "→"
    return f"           {arrow} {sample.outcome.value} on attempt {attempt_number}; {state}"


class ScheduledTaskRosterV2(StrictModel):
    """The exact public task schedule admitted for one V2 track.

    Candidate catalogs can contain certified but intentionally unscheduled
    semantic equivalents.  A count alone cannot prove which subset ran, so the
    immutable roster is carried by every durable campaign boundary.
    """

    track: Track
    purpose: Literal["official", "diagnostic_canary"]
    ranking_eligible: bool
    suite: str = Field(min_length=1)
    selection_fingerprint: str
    task_ids: tuple[str, ...]
    schedule_fingerprint: str

    @model_validator(mode="after")
    def roster_is_exact(self) -> ScheduledTaskRosterV2:
        if not self.task_ids or any(not task_id for task_id in self.task_ids):
            raise ValueError("scheduled task roster must contain non-empty task IDs")
        if len(self.task_ids) != len(set(self.task_ids)):
            raise ValueError("scheduled task roster contains duplicate task IDs")
        if self.ranking_eligible != (self.purpose == "official"):
            raise ValueError("scheduled task roster ranking eligibility conflicts with purpose")
        if self.purpose == "diagnostic_canary":
            if self.track is not Track.MCP or self.suite != "native-five-kind-v1":
                raise ValueError("diagnostic canary roster must be the native MCP suite")
            if len(self.task_ids) != 5:
                raise ValueError("diagnostic canary roster must contain exactly five tasks")
        expected = canonical_sha256(self, exclude_fields=("schedule_fingerprint",))
        if self.schedule_fingerprint != expected:
            raise ValueError("scheduled task roster fingerprint mismatch")
        return self


def _scheduled_task_roster(
    resolved: ResolvedV2CampaignConfig,
    prepared: PreparedTrack,
) -> ScheduledTaskRosterV2:
    """Bind purpose, selector, and exact task IDs before any campaign write."""

    purpose = getattr(getattr(resolved, "config", None), "purpose", "official")
    selection_fingerprint = (
        getattr(prepared, "selection_fingerprint", None) or prepared.release.release_fingerprint
    )
    if purpose == "diagnostic_canary":
        suite = "native-five-kind-v1"
    elif getattr(prepared, "selection_fingerprint", None) is not None:
        suite = "official-selected-release-v1"
    else:
        suite = "candidate-catalog-v1"
    payload = {
        "track": prepared.track,
        "purpose": purpose,
        "ranking_eligible": purpose == "official",
        "suite": suite,
        "selection_fingerprint": selection_fingerprint,
        "task_ids": prepared.task_ids,
        "schedule_fingerprint": "0" * 64,
    }
    payload["schedule_fingerprint"] = canonical_sha256(
        payload, exclude_fields=("schedule_fingerprint",)
    )
    return ScheduledTaskRosterV2.model_validate(payload)


class ModelRunProvenanceV2(StrictModel):
    schema_version: Literal["ori-v2-model-campaign-v16"] = RUNNER_VERSION
    protocol_version: Literal["ori-eval-protocol-v2"] = PROTOCOL_VERSION
    base: RunProvenanceV2
    run_identity: RunIdentity
    source_manifest_sha256: str
    archive_sha256: str
    candidate_release_fingerprint: str
    live_certification_fingerprint: str
    schedule: ScheduledTaskRosterV2
    containment_config_fingerprint: str
    runtime_implementation_fingerprint: str
    runtime_config_fingerprint: str
    requested_api_surface: ProviderApiSurface
    resolved_api_surface: ProviderApiSurface
    structured_output_mode: StructuredOutputMode
    endpoint_family: str
    credential_source: str | None = None
    mcp_launcher_provenance: dict[str, str | None] | None = None
    provenance_fingerprint: str

    @model_validator(mode="after")
    def fingerprint_matches(self) -> ModelRunProvenanceV2:
        expected = canonical_sha256(
            self,
            exclude_fields=("provenance_fingerprint",),
        )
        if self.provenance_fingerprint != expected:
            raise ValueError("model-run provenance fingerprint mismatch")
        return self


class ProviderAttemptV2(StrictModel):
    task_id: str
    attempt: int = Field(strict=True, ge=1)
    scheduler_phase: Literal["initial", "immediate_retry", "deferred_retry"]
    recovery_round: int = Field(default=0, strict=True, ge=0)
    started_at_utc: str
    completed_at_utc: str
    sample: SampleResult
    provider: ProviderRunRecord
    attempt_fingerprint: str

    @model_validator(mode="after")
    def attempt_is_exact(self) -> ProviderAttemptV2:
        if self.task_id != self.sample.task_id or self.task_id != self.provider.task_id:
            raise ValueError("provider attempt task IDs do not match")
        if self.scheduler_phase == "deferred_retry" and self.recovery_round < 1:
            raise ValueError("deferred attempts require a positive recovery round")
        if self.scheduler_phase != "deferred_retry" and self.recovery_round != 0:
            raise ValueError("non-deferred attempts cannot name a recovery round")
        started = datetime.fromisoformat(self.started_at_utc)
        completed = datetime.fromisoformat(self.completed_at_utc)
        if started.tzinfo is None or completed.tzinfo is None or completed < started:
            raise ValueError("provider attempt timestamps are invalid")
        if started.utcoffset() != timedelta(0) or completed.utcoffset() != timedelta(0):
            raise ValueError("provider attempt timestamps must use UTC")
        expected = canonical_sha256(
            self,
            exclude_fields=("attempt_fingerprint",),
        )
        if self.attempt_fingerprint != expected:
            raise ValueError("provider attempt fingerprint mismatch")
        return self


class RetrySchedulerStateV2(StrictModel):
    phase: Literal["primary", "deferred_cooldown", "deferred_retry", "complete"] = "primary"
    recovery_round: int = Field(default=0, strict=True, ge=0)
    pending_task_ids: tuple[str, ...] = ()
    deferred_not_before_utc: str | None = None

    @model_validator(mode="after")
    def phase_is_coherent(self) -> RetrySchedulerStateV2:
        if len(self.pending_task_ids) != len(set(self.pending_task_ids)):
            raise ValueError("retry scheduler contains duplicate pending task IDs")
        deferred = self.phase in {"deferred_cooldown", "deferred_retry"}
        if deferred != (self.recovery_round > 0):
            raise ValueError("retry scheduler recovery round does not match its phase")
        if deferred and not self.pending_task_ids:
            raise ValueError("deferred retry phases require pending tasks")
        if self.phase == "deferred_cooldown":
            if self.deferred_not_before_utc is None:
                raise ValueError("deferred cooldown requires a not-before time")
            not_before = datetime.fromisoformat(self.deferred_not_before_utc)
            if not_before.tzinfo is None or not_before.utcoffset() != timedelta(0):
                raise ValueError("deferred cooldown not-before time must include a timezone")
        elif self.deferred_not_before_utc is not None:
            raise ValueError("only deferred cooldown may retain a not-before time")
        if self.phase in {"primary", "complete"} and self.pending_task_ids:
            raise ValueError("primary and complete scheduler phases cannot retain pending tasks")
        return self


class PrivateRunStateV2(StrictModel):
    schema_version: Literal["ori-v2-private-run-state-v7"] = RUN_STATE_SCHEMA_VERSION
    protocol_version: Literal["ori-eval-protocol-v2"] = PROTOCOL_VERSION
    provenance_fingerprint: str
    checkpoint: CheckpointV2
    attempts: tuple[ProviderAttemptV2, ...]
    scheduler: RetrySchedulerStateV2 = Field(default_factory=RetrySchedulerStateV2)
    state_fingerprint: str

    @model_validator(mode="after")
    def state_is_exact(self) -> PrivateRunStateV2:
        final_by_task: dict[str, ProviderAttemptV2] = {}
        attempt_numbers: dict[str, list[int]] = {}
        for attempt in self.attempts:
            final_by_task[attempt.task_id] = attempt
            attempt_numbers.setdefault(attempt.task_id, []).append(attempt.attempt)
        for task_id, numbers in attempt_numbers.items():
            if numbers != list(range(1, len(numbers) + 1)):
                raise ValueError(f"provider attempts for {task_id} are not contiguous")
        result_by_task = {result.task_id: result for result in self.checkpoint.results}
        if set(final_by_task) != set(result_by_task):
            raise ValueError("private trace and checkpoint task sets differ")
        if not set(self.scheduler.pending_task_ids).issubset(final_by_task):
            raise ValueError("retry scheduler contains tasks without durable attempts")
        for task_id, result in result_by_task.items():
            if final_by_task[task_id].sample != result:
                raise ValueError(f"final provider attempt for {task_id} is stale")
        expected = canonical_sha256(
            self,
            exclude_fields=("state_fingerprint",),
        )
        if self.state_fingerprint != expected:
            raise ValueError("private run-state fingerprint mismatch")
        return self


class RunOperationalMetricsV2(StrictModel):
    resource_mode: Literal["off", "native", "not_applicable"]
    attempts_total: int = Field(strict=True, ge=0)
    retries_total: int = Field(strict=True, ge=0)
    immediate_retries_total: int = Field(strict=True, ge=0)
    deferred_retries_total: int = Field(strict=True, ge=0)
    recovered_infrastructure_tasks: int = Field(strict=True, ge=0)
    exhausted_infrastructure_tasks: int = Field(strict=True, ge=0)
    completed_recovery_rounds: int = Field(strict=True, ge=0)
    tokens_input_total: int = Field(strict=True, ge=0)
    tokens_output_total: int = Field(strict=True, ge=0)
    total_tokens_total: int = Field(strict=True, ge=0)
    elapsed_seconds_total: float = Field(strict=True, ge=0)
    mcp_tool_calls_total: int = Field(strict=True, ge=0)
    cypher_query_calls_total: int = Field(strict=True, ge=0)
    non_cypher_tool_calls_total: int = Field(strict=True, ge=0)
    failed_tool_calls_total: int = Field(strict=True, ge=0)
    resource_read_calls_total: int = Field(strict=True, ge=0)

    @model_validator(mode="after")
    def totals_are_coherent(self) -> RunOperationalMetricsV2:
        if self.retries_total < (self.immediate_retries_total + self.deferred_retries_total):
            raise ValueError("operational retry phase accounting exceeds total retries")
        if self.total_tokens_total != self.tokens_input_total + self.tokens_output_total:
            raise ValueError("operational total token accounting mismatch")
        if self.non_cypher_tool_calls_total != (
            self.mcp_tool_calls_total - self.cypher_query_calls_total
        ):
            raise ValueError("operational MCP tool accounting mismatch")
        if not math.isfinite(self.elapsed_seconds_total):
            raise ValueError("operational elapsed time must be finite")
        if self.resource_mode != "native" and self.resource_read_calls_total != 0:
            raise ValueError("certified resource_mode=off cannot report resource reads")
        return self


class ModelPublicReportV2(StrictModel):
    schema_version: Literal["ori-v2-model-report-v5"] = MODEL_REPORT_SCHEMA_VERSION
    protocol_version: Literal["ori-eval-protocol-v2"] = PROTOCOL_VERSION
    run_identity: RunIdentity
    candidate_release_fingerprint: str
    live_certification_fingerprint: str
    schedule_fingerprint: str
    graph_verification_before_fingerprint: str
    graph_verification_after_fingerprint: str
    operational_metrics: RunOperationalMetricsV2
    report: PublicReportV2
    artifact_fingerprint: str

    @model_validator(mode="after")
    def fingerprint_matches(self) -> ModelPublicReportV2:
        scheduled = self.report.summary.scheduled
        if self.operational_metrics.attempts_total < scheduled:
            raise ValueError("operational attempts cannot be below scheduled tasks")
        if self.operational_metrics.retries_total != (
            self.operational_metrics.attempts_total - scheduled
        ):
            raise ValueError("operational retries do not match attempts minus tasks")
        if self.run_identity.tool_loop is None:
            if self.operational_metrics.resource_mode != "not_applicable":
                raise ValueError("Direct operational metrics require not_applicable resources")
            if self.operational_metrics.mcp_tool_calls_total != 0:
                raise ValueError("Direct operational metrics cannot contain MCP tool calls")
        elif self.operational_metrics.resource_mode not in {"off", "native"}:
            raise ValueError("certified MCP operational metrics require off or native resources")
        expected = canonical_sha256(
            self,
            exclude_fields=("artifact_fingerprint",),
        )
        if self.artifact_fingerprint != expected:
            raise ValueError("model public report fingerprint mismatch")
        return self


def _run_operational_metrics(
    state: PrivateRunStateV2,
    *,
    native: bool = False,
) -> RunOperationalMetricsV2:
    from .model_runtime import NativeExecutionTrace

    attempts = tuple(state.attempts)
    native_traces = []
    for attempt in attempts:
        raw = attempt.provider.provider_metrics.get("native_execution")
        if raw is not None:
            trace = NativeExecutionTrace.model_validate_json(json.dumps(raw, allow_nan=False))
            if not native or not trace.certified:
                raise ValueError("native metrics require a certified native campaign")
            native_traces.append(trace)
    mcp_tool_calls = sum(len(attempt.provider.mcp_tool_receipts) for attempt in attempts)
    cypher_query_calls = sum(
        sum(receipt.tool_name == "cypher_query" for receipt in attempt.provider.mcp_tool_receipts)
        for attempt in attempts
    )
    failed_tool_calls = sum(
        sum(
            receipt.tool_error is not None or not receipt.observation.succeeded
            for receipt in attempt.provider.mcp_tool_receipts
        )
        for attempt in attempts
    )
    resource_read_calls = sum(
        sum(event.kind == EvidenceEventKind.RESOURCE_READ for event in attempt.provider.mcp_events)
        for attempt in attempts
        if "native_execution" not in attempt.provider.provider_metrics
    )
    native_calls = [call for trace in native_traces for call in trace.tool_calls]
    mcp_tool_calls += len(native_calls)
    cypher_query_calls += sum(
        call.name == "query_bloodhound"
        or (call.name == "cypher_query" and call.arguments.get("info_type") == "run")
        for call in native_calls
    )
    failed_tool_calls += sum(
        call.interrupted
        or call.outcome is None
        or call.outcome.failure is not None
        or (
            call.outcome.query_receipt is not None
            and call.outcome.query_receipt.get("success") is False
        )
        for call in native_calls
    )
    resource_read_calls += sum(
        call.operation == "read_resource"
        for trace in native_traces
        for call in trace.protocol_calls
    )
    tokens_input = sum(attempt.provider.tokens_input for attempt in attempts)
    tokens_output = sum(attempt.provider.tokens_output for attempt in attempts)
    total_tokens = tokens_input + tokens_output
    elapsed_seconds = sum(attempt.provider.elapsed_seconds for attempt in attempts)
    retries_total = max(0, len(attempts) - len(state.checkpoint.results))
    attempts_by_task: dict[str, list[ProviderAttemptV2]] = {}
    for attempt in attempts:
        attempts_by_task.setdefault(attempt.task_id, []).append(attempt)

    def retryable_infrastructure(attempt: ProviderAttemptV2) -> bool:
        policy = _infrastructure_retry_policy(attempt.sample, attempt.provider)
        return policy is not None and policy[1]

    recovered_infrastructure_tasks = sum(
        any(retryable_infrastructure(item) for item in rows[:-1])
        and rows[-1].sample.execution_class is not ExecutionClass.INFRA_FAILURE
        for rows in attempts_by_task.values()
    )
    exhausted_infrastructure_tasks = sum(
        rows[-1].sample.execution_class is ExecutionClass.INFRA_FAILURE
        for rows in attempts_by_task.values()
    )
    return RunOperationalMetricsV2(
        resource_mode=(
            "native"
            if native
            else "off"
            if state.checkpoint.run_identity.tool_loop is not None
            else "not_applicable"
        ),
        attempts_total=len(attempts),
        retries_total=retries_total,
        immediate_retries_total=sum(
            attempt.scheduler_phase == "immediate_retry" for attempt in attempts
        ),
        deferred_retries_total=sum(
            attempt.scheduler_phase == "deferred_retry" for attempt in attempts
        ),
        recovered_infrastructure_tasks=recovered_infrastructure_tasks,
        exhausted_infrastructure_tasks=exhausted_infrastructure_tasks,
        completed_recovery_rounds=max(
            (attempt.recovery_round for attempt in attempts),
            default=0,
        ),
        tokens_input_total=tokens_input,
        tokens_output_total=tokens_output,
        total_tokens_total=total_tokens,
        elapsed_seconds_total=elapsed_seconds,
        mcp_tool_calls_total=mcp_tool_calls,
        cypher_query_calls_total=cypher_query_calls,
        non_cypher_tool_calls_total=mcp_tool_calls - cypher_query_calls,
        failed_tool_calls_total=failed_tool_calls,
        resource_read_calls_total=resource_read_calls,
    )


class ReadinessTrackV2(StrictModel):
    track: Track
    target_fingerprint: str
    public_artifact_fingerprint: str
    oracle_artifact_fingerprint: str
    candidate_release_fingerprint: str
    live_certification_fingerprint: str
    capability_profile_fingerprint: str
    graph_verification_fingerprint: str
    task_count: int = Field(strict=True, gt=0)
    schedule: ScheduledTaskRosterV2


class ModelReadinessV2(StrictModel):
    name: str
    provider: str
    model: str
    credential_check: str
    capability_check: str
    requested_api_surface: ProviderApiSurface
    resolved_api_surface: ProviderApiSurface
    structured_output_mode: StructuredOutputMode
    endpoint_family: str
    credential_source: str | None = None
    reasoning_effort: ReasoningEffort | None = None
    anthropic_binding_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def anthropic_binding_matches_provider(self) -> ModelReadinessV2:
        if (self.provider == "anthropic") != (self.anthropic_binding_fingerprint is not None):
            raise ValueError("Anthropic binding fingerprint does not match provider")
        return self


class CampaignReadinessV2(StrictModel):
    schema_version: Literal["ori-v2-run-readiness-v13"] = READINESS_SCHEMA_VERSION
    protocol_version: Literal["ori-eval-protocol-v2"] = PROTOCOL_VERSION
    runner_version: Literal["ori-v2-model-campaign-v16"] = RUNNER_VERSION
    purpose: Literal["official", "diagnostic_canary"]
    ranking_eligible: bool
    source_config_fingerprint: str
    source_manifest_sha256: str
    archive_sha256: str
    graph_fingerprint: str
    target_fingerprint: str
    mcp_server_revision: str
    mcp_launcher_provenance: dict[str, str | None] | None = None
    tracks: tuple[ReadinessTrackV2, ...]
    models: tuple[ModelReadinessV2, ...]
    model_count: int = Field(strict=True, gt=0)
    readiness_fingerprint: str

    @model_validator(mode="after")
    def fingerprint_matches(self) -> CampaignReadinessV2:
        if self.model_count != len(self.models):
            raise ValueError("readiness model count does not match model receipts")
        targets = {item.track.value: item.target_fingerprint for item in self.tracks}
        if (
            len(targets) != len(self.tracks)
            or not targets
            or self.target_fingerprint != canonical_sha256(targets)
        ):
            raise ValueError("readiness track target binding mismatch")
        if self.ranking_eligible != (self.purpose == "official"):
            raise ValueError("readiness ranking eligibility conflicts with purpose")
        if any(item.schedule.purpose != self.purpose for item in self.tracks):
            raise ValueError("readiness schedule purpose does not match campaign purpose")
        expected = canonical_sha256(
            self,
            exclude_fields=("readiness_fingerprint",),
        )
        if self.readiness_fingerprint != expected:
            raise ValueError("campaign readiness fingerprint mismatch")
        return self


class CampaignInterruptionV2(StrictModel):
    kind: Literal[
        "task_cancelled",
        "keyboard_interrupt",
        "signal",
        "unclean_previous_process",
    ]
    signal_name: str | None = None
    recorded_at_utc: str


class CampaignCompletedTrackV2(StrictModel):
    track: Track
    receipt_fingerprint: str


class CampaignLifecycleV2(StrictModel):
    schema_version: Literal["ori-v2-campaign-lifecycle-v3"] = CAMPAIGN_LIFECYCLE_SCHEMA_VERSION
    protocol_version: Literal["ori-eval-protocol-v2"] = PROTOCOL_VERSION
    runner_version: Literal["ori-v2-model-campaign-v16"] = RUNNER_VERSION
    source_config_fingerprint: str
    purpose: Literal["official", "diagnostic_canary"]
    ranking_eligible: bool
    schedules: tuple[ScheduledTaskRosterV2, ...]
    mode: Literal["readiness", "execution"]
    status: Literal["running", "interrupted", "failed", "completed"]
    started_at_utc: str
    updated_at_utc: str
    pid: int = Field(strict=True, gt=0)
    resume_count: int = Field(strict=True, ge=0)
    checkpointed_results: int = Field(strict=True, ge=0)
    active_track: Track | None = None
    active_model: str | None = None
    active_run_index: int | None = Field(default=None, strict=True, ge=1)
    completed_tracks: tuple[CampaignCompletedTrackV2, ...] = ()
    interruptions: tuple[CampaignInterruptionV2, ...] = ()
    failure_type: str | None = None
    lifecycle_fingerprint: str

    @model_validator(mode="after")
    def lifecycle_is_exact(self) -> CampaignLifecycleV2:
        completed = [entry.track for entry in self.completed_tracks]
        if len(completed) != len(set(completed)):
            raise ValueError("campaign lifecycle contains duplicate completed tracks")
        if self.ranking_eligible != (self.purpose == "official"):
            raise ValueError("campaign lifecycle ranking eligibility conflicts with purpose")
        if len({item.track for item in self.schedules}) != len(self.schedules):
            raise ValueError("campaign lifecycle contains duplicate scheduled tracks")
        if any(item.purpose != self.purpose for item in self.schedules):
            raise ValueError("campaign lifecycle schedule purpose mismatch")
        expected = canonical_sha256(
            self,
            exclude_fields=("lifecycle_fingerprint",),
        )
        if self.lifecycle_fingerprint != expected:
            raise ValueError("campaign lifecycle fingerprint mismatch")
        return self


class TrackRunCompletionV2(StrictModel):
    provider: str
    model: str
    run_index: int = Field(strict=True, ge=1)
    result_count: int = Field(strict=True, ge=0)
    public_report_fingerprint: str
    campaign_valid: bool
    invalid_reasons: tuple[str, ...] = ()


class TrackCompletionV2(StrictModel):
    schema_version: Literal["ori-v2-track-completion-v2"] = TRACK_COMPLETION_SCHEMA_VERSION
    protocol_version: Literal["ori-eval-protocol-v2"] = PROTOCOL_VERSION
    runner_version: Literal["ori-v2-model-campaign-v16"] = RUNNER_VERSION
    source_config_fingerprint: str
    track: Track
    candidate_release_fingerprint: str
    live_certification_fingerprint: str
    graph_verification_before_fingerprint: str
    graph_verification_after_fingerprint: str
    expected_task_count_per_run: int = Field(strict=True, gt=0)
    schedule: ScheduledTaskRosterV2
    run_count: int = Field(strict=True, gt=0)
    result_count: int = Field(strict=True, ge=0)
    campaign_valid: bool
    runs: tuple[TrackRunCompletionV2, ...]
    completed_at_utc: str
    receipt_fingerprint: str

    @model_validator(mode="after")
    def receipt_is_exact(self) -> TrackCompletionV2:
        if self.run_count != len(self.runs):
            raise ValueError("track completion run count does not match run receipts")
        if self.result_count != sum(run.result_count for run in self.runs):
            raise ValueError("track completion result count does not match run receipts")
        if (
            self.schedule.track is not self.track
            or len(self.schedule.task_ids) != self.expected_task_count_per_run
        ):
            raise ValueError("track completion schedule does not match expected task count")
        if self.result_count != self.run_count * self.expected_task_count_per_run:
            raise ValueError("track completion result count is not the exact scheduled total")
        identities = [(run.provider, run.model, run.run_index) for run in self.runs]
        if len(identities) != len(set(identities)):
            raise ValueError("track completion contains duplicate run identities")
        if self.campaign_valid != all(run.campaign_valid for run in self.runs):
            raise ValueError("track completion validity does not match run receipts")
        expected = canonical_sha256(
            self,
            exclude_fields=("receipt_fingerprint",),
        )
        if self.receipt_fingerprint != expected:
            raise ValueError("track completion fingerprint mismatch")
        return self


@dataclass(frozen=True)
class PreparedTrack:
    track: Track
    pair: V2ArtifactPair
    profile: CapabilityProfile
    release: CatalogRelease
    live: LiveCertificationCatalog
    certifications: Mapping[str, TaskCertification]
    selected_task_ids: tuple[str, ...] | None = None
    selection_fingerprint: str | None = None
    paired_release_fingerprint: str | None = None

    @property
    def task_ids(self) -> tuple[str, ...]:
        if self.selected_task_ids is not None:
            return self.selected_task_ids
        return tuple(entry.task_id for entry in self.release.entries)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "wb") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        directory_descriptor = os.open(path.parent, directory_flags)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


@contextmanager
def _exclusive_output_dir_lock(output_dir: Path) -> Iterator[None]:
    """Hold one process-scoped advisory lock for every write to an output root."""

    output_dir.mkdir(parents=True, exist_ok=True)
    lock_path = output_dir / ".ori-v2-campaign.lock"
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    file_descriptor = os.open(lock_path, flags, 0o600)
    handle = os.fdopen(file_descriptor, "r+", encoding="utf-8")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.seek(0)
            owner = handle.read().strip()
            detail = f"; owner={owner}" if owner else ""
            raise V2CampaignRunError(
                f"{output_dir} is already locked by another v2 campaign{detail}"
            ) from exc
        handle.seek(0)
        handle.truncate()
        json.dump(
            {
                "pid": os.getpid(),
                "acquired_at_utc": _utc_now(),
            },
            handle,
            sort_keys=True,
        )
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        yield
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _campaign_lifecycle(payload: Mapping[str, Any]) -> CampaignLifecycleV2:
    document = {
        "schema_version": CAMPAIGN_LIFECYCLE_SCHEMA_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "runner_version": RUNNER_VERSION,
        **payload,
        "lifecycle_fingerprint": "0" * 64,
    }
    document["lifecycle_fingerprint"] = canonical_sha256(
        document,
        exclude_fields=("lifecycle_fingerprint",),
    )
    return CampaignLifecycleV2.model_validate(document)


def _checkpoint_counts(output_dir: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for state_path in output_dir.glob(f"*/*/run-*/{RUN_STATE_NAME}"):
        try:
            payload = json.loads(state_path.read_text())
            results = payload["checkpoint"]["results"]
            if not isinstance(results, list):
                continue
            relative = state_path.relative_to(output_dir)
            counts["/".join(relative.parts[:3])] = len(results)
        except (KeyError, OSError, TypeError, ValueError):
            # The authoritative run-state validator reports corruption later.
            # Lifecycle accounting must not turn an unreadable checkpoint into
            # an invented completed sample.
            continue
    return counts


@dataclass
class _CampaignLifecycleController:
    path: Path
    receipt: CampaignLifecycleV2
    _run_counts: dict[str, int]

    @classmethod
    def start(
        cls,
        *,
        output_dir: Path,
        source_config_fingerprint: str,
        preflight_only: bool,
        purpose: Literal["official", "diagnostic_canary"] = "official",
        schedules: tuple[ScheduledTaskRosterV2, ...] = (),
    ) -> _CampaignLifecycleController:
        path = output_dir / "campaign-lifecycle-v2.private.json"
        previous: CampaignLifecycleV2 | None = None
        if path.exists():
            previous = CampaignLifecycleV2.model_validate_json(path.read_text())
            if previous.source_config_fingerprint != source_config_fingerprint:
                raise V2CampaignRunError("campaign lifecycle belongs to a different source config")
            if (
                previous.purpose != purpose
                or previous.ranking_eligible != (purpose == "official")
                or previous.schedules != schedules
            ):
                raise V2CampaignRunError("campaign lifecycle schedule or purpose changed")
        now = _utc_now()
        interruptions = list(previous.interruptions if previous is not None else ())
        if previous is not None and previous.status == "running":
            interruptions.append(
                CampaignInterruptionV2(
                    kind="unclean_previous_process",
                    recorded_at_utc=now,
                )
            )
        run_counts = _checkpoint_counts(output_dir)
        if (
            not run_counts
            and previous is not None
            and previous.active_track is not None
            and previous.active_model is not None
            and previous.active_run_index is not None
        ):
            key = (
                f"{previous.active_track.value}/{previous.active_model}/"
                f"run-{previous.active_run_index:03d}"
            )
            run_counts[key] = previous.checkpointed_results
        receipt = _campaign_lifecycle(
            {
                "source_config_fingerprint": source_config_fingerprint,
                "purpose": purpose,
                "ranking_eligible": purpose == "official",
                "schedules": schedules,
                "mode": "readiness" if preflight_only else "execution",
                "status": "running",
                "started_at_utc": (previous.started_at_utc if previous is not None else now),
                "updated_at_utc": now,
                "pid": os.getpid(),
                "resume_count": (previous.resume_count + 1 if previous is not None else 0),
                "checkpointed_results": (
                    sum(run_counts.values())
                    if run_counts
                    else previous.checkpointed_results
                    if previous is not None
                    else 0
                ),
                "active_track": previous.active_track if previous is not None else None,
                "active_model": previous.active_model if previous is not None else None,
                "active_run_index": (previous.active_run_index if previous is not None else None),
                "completed_tracks": (previous.completed_tracks if previous is not None else ()),
                "interruptions": tuple(interruptions),
                "failure_type": None,
            }
        )
        controller = cls(path=path, receipt=receipt, _run_counts=run_counts)
        controller._persist()
        return controller

    def _update(self, **updates: Any) -> None:
        payload = self.receipt.model_dump(
            mode="python",
            exclude={
                "schema_version",
                "protocol_version",
                "runner_version",
                "lifecycle_fingerprint",
            },
        )
        payload.update(updates)
        payload["updated_at_utc"] = _utc_now()
        self.receipt = _campaign_lifecycle(payload)
        self._persist()

    def _persist(self) -> None:
        _write_model(self.path, self.receipt)

    def activate_track(self, track: Track) -> None:
        self._update(
            status="running",
            active_track=track,
            active_model=None,
            active_run_index=None,
            failure_type=None,
        )

    def record_checkpoint(
        self,
        *,
        track: Track,
        model_name: str,
        run_index: int,
        result_count: int,
    ) -> None:
        key = f"{track.value}/{model_name}/run-{run_index:03d}"
        self._run_counts[key] = result_count
        self._update(
            status="running",
            checkpointed_results=sum(self._run_counts.values()),
            active_track=track,
            active_model=model_name,
            active_run_index=run_index,
            failure_type=None,
        )

    def record_track(self, receipt: TrackCompletionV2) -> None:
        completed = {entry.track: entry for entry in self.receipt.completed_tracks}
        completed[receipt.track] = CampaignCompletedTrackV2(
            track=receipt.track,
            receipt_fingerprint=receipt.receipt_fingerprint,
        )
        self._update(
            status="running",
            completed_tracks=tuple(completed.values()),
            active_track=None,
            active_model=None,
            active_run_index=None,
            failure_type=None,
        )

    def interrupt(
        self,
        *,
        kind: Literal["task_cancelled", "keyboard_interrupt", "signal"],
        signal_name: str | None,
    ) -> None:
        interruption = CampaignInterruptionV2(
            kind=kind,
            signal_name=signal_name,
            recorded_at_utc=_utc_now(),
        )
        self._update(
            status="interrupted",
            interruptions=(*self.receipt.interruptions, interruption),
            failure_type=None,
        )

    def fail(self, failure_type: str) -> None:
        self._update(status="failed", failure_type=failure_type)

    def complete(self) -> None:
        self._update(
            status="completed",
            active_track=None,
            active_model=None,
            active_run_index=None,
            failure_type=None,
        )


@dataclass
class _SignalCancellation:
    task: asyncio.Task[Any]
    installed: tuple[signal.Signals, ...]
    received_signal: str | None = None

    @classmethod
    def install(cls) -> _SignalCancellation:
        loop = asyncio.get_running_loop()
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("campaign signal handling requires an asyncio task")
        controller = cls(task=task, installed=())
        installed: list[signal.Signals] = []
        for candidate in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            try:
                loop.add_signal_handler(candidate, controller._cancel, candidate)
            except (NotImplementedError, RuntimeError):
                continue
            installed.append(candidate)
        controller.installed = tuple(installed)
        return controller

    def _cancel(self, received: signal.Signals) -> None:
        if self.received_signal is None:
            self.received_signal = received.name
        self.task.cancel(f"received {received.name}")

    def close(self) -> None:
        loop = asyncio.get_running_loop()
        for installed in self.installed:
            loop.remove_signal_handler(installed)


def _write_model(path: Path, model: Any) -> None:
    _atomic_write(path, model.model_dump(mode="json"))


def _load_release(path: Path) -> CatalogRelease:
    return CatalogRelease.model_validate_json(path.read_text())


def _load_live(path: Path) -> LiveCertificationCatalog:
    return LiveCertificationCatalog.model_validate_json(path.read_text())


def _prepare_track(
    resolved: ResolvedV2CampaignConfig,
    track: Track,
    snapshot: GraphSnapshot,
) -> PreparedTrack:
    return prepare_track_artifacts(resolved.tracks[track], track, snapshot)


def prepare_track_artifacts(
    paths: ResolvedV2TrackPaths,
    track: Track,
    snapshot: GraphSnapshot,
) -> PreparedTrack:
    """Validate the complete artifact inventory without contacting external services."""
    pair = load_v2_pair(paths.public, paths.oracles)
    profile = capability_profile_for_track(track)
    release = _load_release(paths.candidates)
    live = _load_live(paths.live_certification)
    mismatches: list[str] = []
    if pair.public.track is not track:
        mismatches.append("public track")
    if pair.public.product != snapshot.product:
        mismatches.append("product")
    if pair.public.graph_fingerprint != snapshot.graph_fingerprint:
        mismatches.append("public graph")
    if release != live.candidate_catalog:
        mismatches.append("candidate/live catalog")
    if live.track is not track:
        mismatches.append("live certification track")
    if live.graph_fingerprint != snapshot.graph_fingerprint:
        mismatches.append("live certification graph")
    if release.graph_fingerprint != snapshot.graph_fingerprint:
        mismatches.append("candidate graph")
    if release.compiler_fingerprint != pair.public.compiler_fingerprint:
        mismatches.append("compiler")
    if release.comparator_fingerprint != pair.public.comparator_fingerprint:
        mismatches.append("comparator")
    if release.capability_profile_fingerprint != profile.profile_fingerprint:
        mismatches.append("capability profile")
    if live.capability_profile_fingerprint != profile.profile_fingerprint:
        mismatches.append("live capability profile")
    if live.certifier_fingerprint != certifier_fingerprint():
        mismatches.append("live certifier")

    task_by_id = {task.task_id: task for task in pair.public.tasks}
    oracle_by_id = {oracle.task_id: oracle for oracle in pair.private.oracles}
    certification_by_id = {
        certification.task_id: certification for certification in live.certifications
    }
    entry_ids = [entry.task_id for entry in release.entries]
    if len(entry_ids) != len(set(entry_ids)):
        mismatches.append("duplicate candidate IDs")
    if not set(entry_ids).issubset(task_by_id):
        mismatches.append("candidate/public task subset")
    if not set(entry_ids).issubset(oracle_by_id):
        mismatches.append("candidate/oracle task subset")
    if not set(entry_ids).issubset(certification_by_id):
        mismatches.append("candidate/certification task subset")
    for entry in release.entries:
        task = task_by_id.get(entry.task_id)
        oracle = oracle_by_id.get(entry.task_id)
        certification = certification_by_id.get(entry.task_id)
        if task is None or oracle is None or certification is None:
            continue
        if entry.track is not track:
            mismatches.append(f"{entry.task_id} entry track")
        if entry.task_fingerprint != task.task_fingerprint:
            mismatches.append(f"{entry.task_id} task")
        if entry.oracle_fingerprint != oracle.oracle_fingerprint:
            mismatches.append(f"{entry.task_id} oracle")
        if entry.certification_fingerprint != certification.certification_fingerprint:
            mismatches.append(f"{entry.task_id} certification")
        if certification.state is not CertificationState.CANDIDATE:
            mismatches.append(f"{entry.task_id} state")
    if mismatches:
        raise V2CampaignRunError(
            f"{track.value} candidate certification mismatch: " + ", ".join(sorted(set(mismatches)))
        )
    return PreparedTrack(
        track=track,
        pair=pair,
        profile=profile,
        release=release,
        live=live,
        certifications=certification_by_id,
    )


def prepare_selected_tracks(
    resolved: ResolvedV2CampaignConfig,
    snapshot: GraphSnapshot,
    prepared: Mapping[Track, PreparedTrack],
) -> dict[Track, PreparedTrack]:
    """Rederive both certified selections, including the non-executed sibling track."""
    from .oaic_recipes import OAICRecipeMetadata
    from .release_selection import (
        PairedSelectedRelease,
        SelectedReleaseReceipt,
        pair_selected_tracks,
        select_candidate_track,
    )

    if resolved.selected_release is None or set(prepared) != {Track.DIRECT, Track.MCP}:
        raise V2CampaignRunError(
            "OAIC requires both certified tracks and a paired selected release"
        )
    selections = {}
    for track, item in prepared.items():
        paths = resolved.tracks[track]
        if paths.release_metadata is None or paths.selection is None:
            raise V2CampaignRunError("OAIC requires per-track metadata and selected receipts")
        metadata = OAICRecipeMetadata.model_validate_json(paths.release_metadata.read_text())
        selection = select_candidate_track(
            public=item.pair.public,
            candidates=item.release,
            metadata=metadata,
            source_archive_sha256=_sha256(resolved.archive),
        )
        if selection != SelectedReleaseReceipt.model_validate_json(paths.selection.read_text()):
            raise V2CampaignRunError("OAIC selected receipt differs from certified selection")
        if (
            selection.seed != snapshot.seed
            or selection.graph_fingerprint != snapshot.graph_fingerprint
        ):
            raise V2CampaignRunError("OAIC selection seed or graph mismatch")
        if selection.source_manifest_fingerprint != canonical_sha256(
            json.loads(resolved.source_manifest.read_text())
        ):
            raise V2CampaignRunError("OAIC selection source manifest mismatch")
        selections[track] = selection
    paired = pair_selected_tracks(selections[Track.DIRECT], selections[Track.MCP])
    if paired != PairedSelectedRelease.model_validate_json(resolved.selected_release.read_text()):
        raise V2CampaignRunError("OAIC paired selected release mismatch")
    return {
        track: replace(
            item,
            selected_task_ids=selections[track].selected_task_ids,
            selection_fingerprint=selections[track].selection_fingerprint,
            paired_release_fingerprint=paired.release_fingerprint,
        )
        for track, item in prepared.items()
    }


def prepare_diagnostic_canary_track(
    resolved: ResolvedV2CampaignConfig,
    snapshot: GraphSnapshot,
    prepared: Mapping[Track, PreparedTrack],
) -> dict[Track, PreparedTrack]:
    """Rederive the fixed five-task MCP canary without weakening official selection.

    A canary config deliberately carries only MCP execution artifacts, while
    retaining the paired selected-release reference needed to bind it to the
    original 50/50 OAIC release.  The selected receipt and diagnostic receipt
    are both rederived from the complete candidate pool before anything can
    reach provider configuration.
    """

    from .diagnostic_selection import (
        DiagnosticSelectionReceiptV1,
        select_diagnostic_canary,
    )
    from .oaic_recipes import OAICRecipeMetadata
    from .release_selection import PairedSelectedRelease, SelectedReleaseReceipt

    if (
        resolved.config.purpose != "diagnostic_canary"
        or resolved.selected_release is None
        or resolved.canary_selection is None
        or set(prepared) != {Track.MCP}
    ):
        raise V2CampaignRunError("diagnostic canary configuration is incomplete")
    item = prepared[Track.MCP]
    paths = resolved.tracks[Track.MCP]
    if paths.release_metadata is None or paths.selection is None:
        raise V2CampaignRunError("diagnostic canary is missing MCP selection metadata")
    metadata = OAICRecipeMetadata.model_validate_json(paths.release_metadata.read_text())
    mcp_selection = SelectedReleaseReceipt.model_validate_json(paths.selection.read_text())
    paired = PairedSelectedRelease.model_validate_json(resolved.selected_release.read_text())
    receipt = select_diagnostic_canary(
        public=item.pair.public,
        candidates=item.release,
        metadata=metadata,
        source_archive_sha256=_sha256(resolved.archive),
        paired_release=paired,
        mcp_selection=mcp_selection,
    )
    persisted = DiagnosticSelectionReceiptV1.model_validate_json(
        resolved.canary_selection.read_text()
    )
    if persisted != receipt:
        raise V2CampaignRunError("diagnostic canary receipt differs from certified selection")
    if (
        receipt.seed != snapshot.seed
        or receipt.graph_fingerprint != snapshot.graph_fingerprint
        or receipt.source_manifest_fingerprint
        != canonical_sha256(json.loads(resolved.source_manifest.read_text()))
    ):
        raise V2CampaignRunError("diagnostic canary source binding mismatch")
    return {
        Track.MCP: replace(
            item,
            selected_task_ids=receipt.selected_task_ids,
            selection_fingerprint=receipt.selection_fingerprint,
            paired_release_fingerprint=receipt.paired_release_fingerprint,
        )
    }


def prepare_native_selected_tracks(resolved, snapshot, direct, native):
    """Bind a qualified native roster to the original certified 50/50 selection."""
    from .native_qualification import NativeQualifiedArtifacts
    from .oaic_recipes import OAICRecipeMetadata
    from .release_selection import (
        PairedSelectedRelease,
        SelectedReleaseReceipt,
        pair_selected_tracks,
        select_candidate_track,
    )

    if (
        resolved.selected_release is None
        or set(resolved.tracks) != {Track.DIRECT, Track.MCP}
        or type(native) is not NativeQualifiedArtifacts
        or type(direct) is not PreparedTrack
        or direct.track is not Track.DIRECT
    ):
        raise V2CampaignRunError("NATIVE_PAIRED_RELEASE_REQUIRED")
    try:
        native.__post_init__()
        paths = resolved.tracks[Track.DIRECT]
        direct_selection = select_candidate_track(
            public=direct.pair.public,
            candidates=direct.release,
            metadata=OAICRecipeMetadata.model_validate_json(paths.release_metadata.read_text()),
            source_archive_sha256=_sha256(resolved.archive),
        )
        if direct_selection != SelectedReleaseReceipt.model_validate_json(
            paths.selection.read_text(),
        ):
            raise ValueError("Direct selection mismatch")
        native_selection = native.prepared.selection
        if native_selection != SelectedReleaseReceipt.model_validate_json(
            resolved.tracks[Track.MCP].selection.read_text(),
        ):
            raise ValueError("native original selection mismatch")
        paired = pair_selected_tracks(direct_selection, native_selection)
        stored_pair = PairedSelectedRelease.model_validate_json(
            resolved.selected_release.read_text(),
        )
        if (
            paired != stored_pair
            or paired.graph_fingerprint != snapshot.graph_fingerprint
            or paired.seed != snapshot.seed
            or paired.source_manifest_fingerprint
            != canonical_sha256(json.loads(resolved.source_manifest.read_text()))
            or paired.source_archive_sha256 != _sha256(resolved.archive)
        ):
            raise ValueError("paired source mismatch")
        for item in (direct, native):
            if any(
                value != snapshot.graph_fingerprint
                for value in (
                    item.pair.public.graph_fingerprint,
                    item.release.graph_fingerprint,
                    item.live.graph_fingerprint,
                )
            ):
                raise ValueError("paired graph mismatch")
        return {
            Track.DIRECT: replace(
                direct,
                selected_task_ids=direct_selection.selected_task_ids,
                selection_fingerprint=direct_selection.selection_fingerprint,
                paired_release_fingerprint=paired.release_fingerprint,
            ),
            Track.MCP: replace(native, paired_release=paired),
        }
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        raise V2CampaignRunError("NATIVE_PAIRED_RELEASE_MISMATCH") from exc


def _git_revision(path: Path) -> str:
    try:
        revision = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(path), "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except subprocess.CalledProcessError as exc:
        raise V2CampaignRunError(f"cannot inspect MCP checkout: {exc}") from exc
    if dirty:
        raise V2CampaignRunError("certified v2 MCP checkout must be clean")
    return revision


@dataclass(frozen=True)
class _ProviderIdentityV2:
    requested_api_surface: ProviderApiSurface
    resolved_api_surface: ProviderApiSurface
    structured_output_mode: StructuredOutputMode
    endpoint_family: str
    credential_source: str | None
    anthropic_binding_fingerprint: str | None = None


def _anthropic_mutation_projection(resolved: ResolvedV2CampaignConfig) -> list[dict[str, Any]]:
    return sorted(
        (
            {
                "name": model.name,
                "model": model.model,
                "api_surface": model.api_surface,
                "structured_output_mode": model.structured_output_mode,
                "configured_base_url": (
                    model.model_base_url or resolved.config.defaults.model_base_url
                ),
            }
            for model in resolved.config.models
            if model.provider == "anthropic"
        ),
        key=lambda entry: entry["name"],
    )


def _prepare_anthropic_bindings(resolved: ResolvedV2CampaignConfig) -> None:
    # Validate the whole selection before opening any provider configuration.
    for model in resolved.config.models:
        _model_api_surfaces(model)
        if model.provider == "codex":
            _codex_model_slug(model)
        elif model.provider == "anthropic":
            slug = model.model.removeprefix("anthropic/").split("@", 1)[0]
            if not slug.strip():
                raise V2CampaignRunError("Anthropic model slug must be explicit and nonempty")
    fingerprint = canonical_sha256(_anthropic_mutation_projection(resolved))
    if getattr(resolved, "_anthropic_bindings", None) is not None:
        if resolved._anthropic_mutation_fingerprint != fingerprint:
            raise V2CampaignRunError(
                "Prepared Anthropic configuration changed; reload configuration"
            )
        return
    pending: dict[str, AnthropicBinding] = {}
    for model in resolved.config.models:
        if model.provider == "anthropic":
            try:
                pending[model.name] = prepare_anthropic_binding(
                    model.requested_model,
                    model.model_base_url or resolved.config.defaults.model_base_url,
                )
            except ProviderContractError as exc:
                raise V2CampaignRunError(str(exc)) from None
    resolved._anthropic_mutation_fingerprint = fingerprint
    resolved._anthropic_bindings = MappingProxyType(pending)


def _anthropic_binding(model: V2ModelEntry, resolved: ResolvedV2CampaignConfig) -> AnthropicBinding:
    bindings = getattr(resolved, "_anthropic_bindings", None)
    if bindings is None or model.name not in bindings:
        raise V2CampaignRunError("Anthropic configuration has not been prepared")
    if resolved._anthropic_mutation_fingerprint != canonical_sha256(
        _anthropic_mutation_projection(resolved)
    ):
        raise V2CampaignRunError("Prepared Anthropic configuration changed; reload configuration")
    return bindings[model.name]


def _guard_anthropic_headers(resolved: ResolvedV2CampaignConfig) -> None:
    """Compare secret-bearing header configuration only inside the campaign lock."""

    selected = [model for model in resolved.config.models if model.provider == "anthropic"]
    if not selected:
        return
    expected = {
        "schema_version": 1,
        "source_config_fingerprint": resolved.source_config_fingerprint,
        "models": {
            model.name: {
                "model_slug": (binding := _anthropic_binding(model, resolved)).model_slug,
                "binding_fingerprint": binding.config_fingerprint,
                "headers": anthropic_private_headers(binding),
            }
            for model in sorted(selected, key=lambda item: item.name)
        },
    }
    private_dir = resolved.output_dir / ".ori-private"
    guard = private_dir / "anthropic-headers-v1.private.json"
    safe_error = "Anthropic private header configuration is unavailable or incompatible"

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(safe_error)
            result[key] = value
        return result

    try:
        root_entries = {entry.name for entry in resolved.output_dir.iterdir()}
        if private_dir.exists() or private_dir.is_symlink():
            info = private_dir.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077:
                raise ValueError(safe_error)
        else:
            if root_entries - {".ori-v2-campaign.lock"}:
                raise ValueError(safe_error)
            private_dir.mkdir(mode=0o700)

        if guard.exists() or guard.is_symlink():
            info = guard.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                raise ValueError(safe_error)
            descriptor = os.open(
                guard,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
            )
            with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                    raise ValueError(safe_error)
                actual = json.load(stream, object_pairs_hook=unique_object)
            if (
                not isinstance(actual, dict)
                or type(actual.get("schema_version")) is not int
                or actual != expected
            ):
                raise ValueError(safe_error)
            return

        if root_entries - {".ori-v2-campaign.lock", ".ori-private"} or any(private_dir.iterdir()):
            raise ValueError(safe_error)
        _atomic_write(guard, expected)
    except (OSError, UnicodeError, ValueError):
        raise V2CampaignRunError(safe_error) from None


def _ollama_mutation_projection(resolved: ResolvedV2CampaignConfig) -> list[dict[str, Any]]:
    return sorted(
        (
            {
                "name": model.name,
                "model": model.model,
                "api_surface": model.api_surface,
                "structured_output_mode": model.structured_output_mode,
                "configured_base_url": (
                    model.model_base_url or resolved.config.defaults.model_base_url
                ),
            }
            for model in resolved.config.models
            if model.provider == "ollama"
        ),
        key=lambda entry: entry["name"],
    )


def _prepare_ollama_endpoints(resolved: ResolvedV2CampaignConfig) -> None:
    # Validate the whole surface selection before preparing any provider config.
    for model in resolved.config.models:
        _model_api_surfaces(model)
        if model.provider == "ollama":
            try:
                ollama_model_name(model.requested_model)
            except ProviderContractError as exc:
                raise V2CampaignRunError(str(exc)) from None
    fingerprint = canonical_sha256(_ollama_mutation_projection(resolved))
    if getattr(resolved, "_ollama_endpoints", None) is not None:
        if resolved._ollama_mutation_fingerprint != fingerprint:
            raise V2CampaignRunError("Prepared Ollama configuration changed; reload configuration")
        return
    pending: dict[str, OllamaEndpoint] = {}
    for model in resolved.config.models:
        if model.provider == "ollama":
            try:
                pending[model.name] = prepare_ollama_endpoint(
                    model.model_base_url or resolved.config.defaults.model_base_url,
                )
            except ProviderContractError as exc:
                raise V2CampaignRunError(str(exc)) from None
    resolved._ollama_mutation_fingerprint = fingerprint
    resolved._ollama_endpoints = MappingProxyType(pending)


def _ollama_endpoint(model: V2ModelEntry, resolved: ResolvedV2CampaignConfig) -> OllamaEndpoint:
    bindings = getattr(resolved, "_ollama_endpoints", None)
    if bindings is None or model.name not in bindings:
        raise V2CampaignRunError("Ollama configuration has not been prepared")
    if resolved._ollama_mutation_fingerprint != canonical_sha256(
        _ollama_mutation_projection(resolved)
    ):
        raise V2CampaignRunError("Prepared Ollama configuration changed; reload configuration")
    return bindings[model.name]


def _model_base_url(
    model: V2ModelEntry,
    resolved: ResolvedV2CampaignConfig,
) -> str | None:
    base_url = model.model_base_url or resolved.config.defaults.model_base_url
    if model.provider == "anthropic":
        return _anthropic_binding(model, resolved).base_url
    if model.provider == "ollama":
        return _ollama_endpoint(model, resolved).selected_base_url
    if model.provider == "gemini":
        return GEMINI_OPENAI_BASE_URL
    if model.provider == "codex":
        return codex_request_base_url(model.model, base_url)
    if not base_url and "@" in model.model:
        base_url = model.model.rsplit("@", 1)[1]
    if not base_url and model.provider == "openai-compat":
        base_url = os.getenv("OPENAI_COMPAT_BASE_URL")
    if not base_url and model.provider == "openai":
        base_url = "https://api.openai.com/v1"
    return base_url


def _provider_endpoint_fingerprint(
    model: V2ModelEntry,
    resolved: ResolvedV2CampaignConfig,
) -> str:
    """Hash the exact effective endpoint into the resume identity."""

    endpoint = (
        _ollama_endpoint(model, resolved).chat_url
        if model.provider == "ollama"
        else _model_base_url(model, resolved) or ""
    )
    return canonical_sha256({"endpoint": endpoint})


def _model_api_surfaces(model: V2ModelEntry) -> tuple[ProviderApiSurface, ProviderApiSurface]:
    requested = ProviderApiSurface(model.api_surface)
    selected = resolve_api_surface(model.provider, requested)
    try:
        validate_release1_api_surface(model.provider, selected)
    except ProviderContractError:
        requirement = (
            "Release 1 Codex requires Responses"
            if model.provider == "codex"
            else "Release 1 enables Responses only for Codex"
        )
        raise V2CampaignRunError(
            f"model {model.name} cannot use api_surface={selected.value!r}; {requirement}"
        ) from None
    return requested, selected


def _provider_identity(
    model: V2ModelEntry,
    resolved: ResolvedV2CampaignConfig,
) -> _ProviderIdentityV2:
    requested, selected = _model_api_surfaces(model)

    credential_source: str | None = None
    endpoint_family = model.provider
    anthropic_fingerprint: str | None = None
    if model.provider == "openai":
        base_url = _model_base_url(model, resolved)
        if not official_openai_endpoint_is_secure(base_url):
            raise V2CampaignRunError(
                f"model {model.name} provider='openai' requires the official "
                "HTTPS api.openai.com origin; "
                "use provider='openai-compat' for custom endpoints"
            )
        endpoint_family = "openai"
        credential_source = "OPENAI_API_KEY" if os.getenv("OPENAI_API_KEY") else None
    elif model.provider == "openai-compat":
        base_url = _model_base_url(model, resolved)
        credential = resolve_openai_compat_credential(base_url)
        endpoint_family = credential.endpoint_family
        credential_source = credential.credential_source
    elif model.provider == "codex":
        _codex_model_slug(model)
        try:
            binding = codex_endpoint_binding(_model_base_url(model, resolved))
        except ProviderContractError as exc:
            raise V2CampaignRunError(str(exc)) from None
        endpoint_family = binding.endpoint_family
        credential_source = binding.credential_source
    elif model.provider == "anthropic":
        identity = anthropic_binding_identity(_anthropic_binding(model, resolved))
        endpoint_family = identity["endpoint_family"]
        credential_source = identity["credential_source"]
        anthropic_fingerprint = identity["config_fingerprint"]
    elif model.provider == "gemini":
        credential_source = "GEMINI_API_KEY" if os.getenv("GEMINI_API_KEY") else None

    return _ProviderIdentityV2(
        requested_api_surface=requested,
        resolved_api_surface=selected,
        structured_output_mode=model.structured_output_mode,
        endpoint_family=endpoint_family,
        credential_source=credential_source,
        anthropic_binding_fingerprint=anthropic_fingerprint,
    )


def _codex_model_slug(model: V2ModelEntry) -> str:
    slug = model.model.split("/", 1)[1] if model.model.startswith("codex/") else model.model
    slug = slug.split("@", 1)[0]
    if not slug.strip():
        raise V2CampaignRunError("Codex model slug must be explicit and nonempty")
    return slug


def _model_readiness(
    resolved: ResolvedV2CampaignConfig,
) -> tuple[ModelReadinessV2, ...]:
    """Verify local credentials/configuration without invoking a model."""

    receipts: list[ModelReadinessV2] = []
    _prepare_ollama_endpoints(resolved)
    _prepare_anthropic_bindings(resolved)
    codex_status_checked = False
    codex_capabilities: dict[str, set[str]] | None = None
    for model in resolved.config.models:
        identity = _provider_identity(model, resolved)
        if model.provider == "codex":
            slug = _codex_model_slug(model)
            reasoning_effort = resolved.config.defaults.reasoning_effort
            if identity.credential_source != "codex-auth-file":
                if identity.credential_source is None:
                    raise V2CampaignRunError("Custom Codex endpoint requires CODEX_COMPAT_API_KEY")
                receipts.append(
                    ModelReadinessV2(
                        name=model.name,
                        provider=model.provider,
                        model=slug,
                        credential_check=identity.credential_source,
                        capability_check="configured-not-probed",
                        requested_api_surface=identity.requested_api_surface,
                        resolved_api_surface=identity.resolved_api_surface,
                        structured_output_mode=identity.structured_output_mode,
                        endpoint_family=identity.endpoint_family,
                        credential_source=identity.credential_source,
                        reasoning_effort=reasoning_effort,
                    )
                )
                continue
            try:
                resolve_codex_credential(_model_base_url(model, resolved))
            except ProviderContractError as exc:
                raise V2CampaignRunError(str(exc)) from None
            if not codex_status_checked:
                try:
                    status = subprocess.run(
                        ["codex", "login", "status"],
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                except (FileNotFoundError, subprocess.CalledProcessError) as exc:
                    raise V2CampaignRunError(
                        "Codex OAuth readiness failed; run `codex login`"
                    ) from exc
                if "logged in" not in (status.stdout + status.stderr).casefold():
                    raise V2CampaignRunError("Codex OAuth readiness failed; run `codex login`")
                cache_path = Path.home() / ".codex" / "models_cache.json"
                try:
                    cache = json.loads(cache_path.read_text())
                    codex_capabilities = {}
                    for item in cache.get("models", ()):
                        if not isinstance(item, Mapping):
                            continue
                        slug = str(item.get("slug") or "")
                        if not slug:
                            continue
                        supported: set[str] = set()
                        for level in item.get("supported_reasoning_levels", ()):
                            effort = level.get("effort") if isinstance(level, Mapping) else level
                            if isinstance(effort, str) and effort:
                                supported.add(effort)
                        codex_capabilities[slug] = supported
                except (FileNotFoundError, json.JSONDecodeError, AttributeError) as exc:
                    raise V2CampaignRunError(
                        "Codex model cache is unavailable; run Codex once to refresh it"
                    ) from exc
                codex_status_checked = True
            if codex_capabilities is None or slug not in codex_capabilities:
                raise V2CampaignRunError(
                    f"Codex model {slug!r} is absent from ~/.codex/models_cache.json"
                )
            if reasoning_effort is not None and reasoning_effort not in codex_capabilities[slug]:
                raise V2CampaignRunError(
                    f"Codex model {slug!r} does not advertise reasoning effort "
                    f"{reasoning_effort!r} in ~/.codex/models_cache.json"
                )
            receipts.append(
                ModelReadinessV2(
                    name=model.name,
                    provider=model.provider,
                    model=slug,
                    credential_check="codex-auth-file+codex-login-status",
                    capability_check=(
                        "codex-model-cache+reasoning-effort"
                        if reasoning_effort is not None
                        else "codex-model-cache"
                    ),
                    requested_api_surface=identity.requested_api_surface,
                    resolved_api_surface=identity.resolved_api_surface,
                    structured_output_mode=identity.structured_output_mode,
                    endpoint_family=identity.endpoint_family,
                    credential_source=identity.credential_source,
                    reasoning_effort=reasoning_effort,
                )
            )
            continue

        if model.provider == "anthropic":
            binding = _anthropic_binding(model, resolved)
            receipts.append(
                ModelReadinessV2(
                    name=model.name,
                    provider=model.provider,
                    model=binding.model_slug,
                    credential_check="configuration-validated-not-probed",
                    capability_check="configuration-validated-not-probed",
                    requested_api_surface=identity.requested_api_surface,
                    resolved_api_surface=identity.resolved_api_surface,
                    structured_output_mode=identity.structured_output_mode,
                    endpoint_family=identity.endpoint_family,
                    credential_source=identity.credential_source,
                    anthropic_binding_fingerprint=binding.config_fingerprint,
                )
            )
            continue

        required_key = {
            "gemini": "GEMINI_API_KEY",
            "openai": "OPENAI_API_KEY",
        }.get(model.provider)
        if model.provider == "openai-compat":
            base_url = _model_base_url(model, resolved)
            if not base_url:
                raise V2CampaignRunError(
                    f"model {model.name} requires an OpenAI-compatible base URL"
                )
            if identity.credential_source is None and not openai_compat_endpoint_is_local(base_url):
                raise V2CampaignRunError(
                    f"model {model.name} has no credential for {identity.endpoint_family} endpoint"
                )
        if required_key is not None and identity.credential_source is None:
            raise V2CampaignRunError(f"model {model.name} requires {required_key}")
        receipts.append(
            ModelReadinessV2(
                name=model.name,
                provider=model.provider,
                model=model.model,
                credential_check=(identity.credential_source or "provider-does-not-require-a-key"),
                capability_check="configured-not-probed",
                requested_api_surface=identity.requested_api_surface,
                resolved_api_surface=identity.resolved_api_surface,
                structured_output_mode=identity.structured_output_mode,
                endpoint_family=identity.endpoint_family,
                credential_source=identity.credential_source,
            )
        )
    return tuple(receipts)


def _model_loop(
    model: V2ModelEntry,
    resolved: ResolvedV2CampaignConfig,
) -> MCPToolLoop:
    configured = resolved.config.defaults.mcp
    if configured is None:
        raise V2CampaignRunError("MCP model loop requested without defaults.mcp")
    raw = model.mcp_tool_loop or configured.tool_loop
    loop = MCPToolLoop(raw)
    if loop is MCPToolLoop.AUTO:
        raise V2CampaignRunError("certified v2 campaigns forbid MCP tool_loop=auto")
    if loop is MCPToolLoop.INSPECT:
        raise V2CampaignRunError("Inspect-backed v2 model campaigns are not enabled by run-v2")
    if loop is MCPToolLoop.NATIVE_ANTHROPIC:
        from .campaign_config import V2NativeMCPConfig

        if not isinstance(configured, V2NativeMCPConfig) or model.provider != "anthropic":
            raise V2CampaignRunError("native-anthropic requires an explicit native Anthropic cell")
    elif model.provider == "anthropic":
        raise V2CampaignRunError("Anthropic MCP requires native-anthropic")
    if model.provider == "ollama" and loop is not MCPToolLoop.NATIVE_OLLAMA:
        raise V2CampaignRunError(f"model {model.name} requires native-ollama MCP loop")
    if model.provider != "ollama" and loop is MCPToolLoop.NATIVE_OLLAMA:
        raise V2CampaignRunError(f"model {model.name} cannot use native-ollama MCP loop")
    return loop


def _provider_options(
    model: V2ModelEntry,
    reasoning_effort: ReasoningEffort | None,
) -> dict[str, Any]:
    """Return one immutable provider-option map for every task and track."""

    options = dict(model.options)
    if model.provider == "codex" and reasoning_effort is not None:
        options["reasoning_effort"] = reasoning_effort
    return options


def _validate_model_bindings(
    resolved: ResolvedV2CampaignConfig,
    prepared: Mapping[Track, PreparedTrack],
) -> None:
    if Track.MCP not in prepared:
        return
    for model in resolved.config.models:
        loop = _model_loop(model, resolved)
        mismatched = [
            task.task_id
            for task in prepared[Track.MCP].pair.public.tasks
            if task.binding.mcp_tool_loop != loop.value
        ]
        if mismatched:
            raise V2CampaignRunError(
                f"model {model.name} loop {loop.value!r} does not match "
                f"the certified MCP catalog binding ({len(mismatched)} tasks)"
            )


def _validate_runtime_bounds(
    resolved: ResolvedV2CampaignConfig,
    prepared: Mapping[Track, PreparedTrack],
) -> None:
    """Reject hidden runtime caps that are stricter than certified task bounds."""

    mcp = prepared.get(Track.MCP)
    if mcp is None:
        return
    tasks = mcp.pair.public.tasks
    required_steps = max(task.binding.bounds.max_tool_calls for task in tasks)
    minimum_task_timeout = min(task.binding.bounds.timeout_seconds for task in tasks)
    configured = resolved.config.defaults.mcp
    if configured is None:
        raise V2CampaignRunError("MCP runtime bounds requested without defaults.mcp")
    if configured.max_steps < required_steps:
        raise V2CampaignRunError(
            "configured MCP max_steps is below the certified task budget: "
            f"configured={configured.max_steps} required={required_steps}"
        )
    for name, value in (
        ("read_timeout_seconds", configured.read_timeout_seconds),
        ("tool_timeout_seconds", configured.tool_timeout_seconds),
    ):
        if value >= minimum_task_timeout:
            raise V2CampaignRunError(
                f"configured MCP {name} must be below every task deadline: "
                f"configured={value} minimum_task_timeout={minimum_task_timeout}"
            )


def prepare_native_campaign_artifacts(resolved, profile):
    """Check native selected artifacts offline, without granting live admission."""
    from .native_feasibility import (
        NativeFeasibilityInputError,
        assess_native_selected_cell,
        validate_native_development_track,
    )
    from .release_selection import SelectedReleaseReceipt

    paths = resolved.tracks[Track.MCP]
    if resolved.config.purpose == "diagnostic_canary":
        from .native_diagnostic import compile_native_diagnostic

        prepared = compile_native_diagnostic(resolved, profile)
        if load_v2_pair(paths.public, paths.oracles) != prepared.pair:
            raise V2CampaignRunError("NATIVE_DIAGNOSTIC_ARTIFACT_BINDING_MISMATCH")
        return prepared
    if paths.selection is None:
        raise V2CampaignRunError("NATIVE_SELECTION_REQUIRED")
    try:
        result = assess_native_selected_cell(
            manifest=json.loads(resolved.source_manifest.read_text()),
            archive=resolved.archive.read_bytes(),
            selection=SelectedReleaseReceipt.model_validate_json(paths.selection.read_text()),
            profile=profile,
        )
    except (OSError, ValueError, TypeError) as exc:
        raise V2CampaignRunError("NATIVE_ARTIFACT_INPUT_INVALID") from exc
    if any(item.status == "harness_error" for item in result.tasks):
        raise V2CampaignRunError("NATIVE_CERTIFICATION_HARNESS_ERROR")
    if not result.offline_feasible or result.development is None:
        raise V2CampaignRunError("NATIVE_SELECTED_CELL_UNSUPPORTED")
    try:
        prepared = validate_native_development_track(result.development)
        supplied = load_v2_pair(paths.public, paths.oracles)
        if supplied != prepared.pair:
            raise NativeFeasibilityInputError("native artifacts differ from selected compilation")
    except (OSError, ValueError, TypeError) as exc:
        raise V2CampaignRunError("NATIVE_ARTIFACT_BINDING_MISMATCH") from exc
    return prepared


def prepare_native_qualification(config_path: Path):
    """Prepare exact selected native qualification inputs without service calls."""
    from .campaign_config import load_v2_native_qualification_config
    from .live_projection import native_qualification_guard
    from .native_capability import NativeCapabilityProfile, validate_native_capability_profile
    from .native_feasibility import native_qualification_artifacts

    resolved = load_v2_native_qualification_config(config_path)
    profile = validate_native_capability_profile(
        NativeCapabilityProfile.model_validate_json(
            resolved.native_mcp_paths.capability_profile.read_text(),
        )
    )
    config = resolved.config.defaults.mcp
    if (
        profile.implementation_id != config.implementation_id
        or profile.backend != config.backend
        or profile.runtime_fingerprint != config.runtime_fingerprint
        or profile.dependency_lock_fingerprint != config.dependency_lock_fingerprint
    ):
        raise V2CampaignRunError("NATIVE_CONFIG_PROFILE_MISMATCH")
    prepared = prepare_native_campaign_artifacts(resolved, profile)
    corpus, offline = native_qualification_artifacts(prepared)
    manifest = json.loads(resolved.source_manifest.read_text())
    snapshot = build_archive_snapshot(resolved.archive, manifest, product=corpus.product)
    guard, budget = native_qualification_guard(
        corpus=corpus,
        offline=offline,
        profile=profile,
        snapshot=snapshot,
    )
    return resolved, prepared, snapshot, offline, guard, budget


def prepare_v2_campaign(
    config_path: Path,
) -> tuple[
    ResolvedV2CampaignConfig,
    GraphSnapshot,
    dict[Track, PreparedTrack],
    str,
    tuple[ModelReadinessV2, ...],
]:
    """Load every immutable artifact and reject mismatches before network/model work."""

    resolved = load_v2_campaign_config(config_path)
    if resolved.native_mcp_paths is not None and Track.MCP in resolved.config.track_modes:
        from .native_capability import NativeCapabilityProfile, validate_native_capability_profile

        native_profile = validate_native_capability_profile(
            NativeCapabilityProfile.model_validate_json(
                resolved.native_mcp_paths.capability_profile.read_text(),
            ),
        )
        native_config = resolved.config.defaults.mcp
        if (
            native_profile.implementation_id != native_config.implementation_id
            or native_profile.backend != native_config.backend
            or native_profile.runtime_fingerprint != native_config.runtime_fingerprint
            or native_profile.dependency_lock_fingerprint
            != native_config.dependency_lock_fingerprint
        ):
            raise V2CampaignRunError("NATIVE_CONFIG_PROFILE_MISMATCH")
        native_prepared = prepare_native_campaign_artifacts(resolved, native_profile)
        if resolved.native_mcp_paths.qualification is not None:
            from .native_qualification import load_native_qualification_artifacts

            native_snapshot = build_archive_snapshot(
                resolved.archive,
                json.loads(resolved.source_manifest.read_text()),
                product=native_prepared.corpus.product,
            )
            native_paths = resolved.tracks[Track.MCP]
            try:
                qualified = load_native_qualification_artifacts(
                    native_prepared,
                    native_snapshot,
                    qualification_path=resolved.native_mcp_paths.qualification,
                    work_path=resolved.native_mcp_paths.qualification_work,
                    candidate_path=native_paths.candidates,
                    live_path=native_paths.live_certification,
                )
            except (OSError, ValueError, TypeError) as exc:
                raise V2CampaignRunError("NATIVE_QUALIFICATION_ARTIFACT_INVALID") from exc
            if resolved.config.purpose == "diagnostic_canary":
                from .native_diagnostic import original_diagnostic_inputs

                _, _, _, paired = original_diagnostic_inputs(resolved)
                prepared_all = {Track.MCP: replace(qualified, paired_release=paired)}
            else:
                if resolved.selected_release is None or Track.DIRECT not in resolved.tracks:
                    raise V2CampaignRunError("NATIVE_PAIRED_RELEASE_REQUIRED")
                prepared_all = prepare_native_selected_tracks(
                    resolved,
                    native_snapshot,
                    _prepare_track(resolved, Track.DIRECT, native_snapshot),
                    qualified,
                )
            native = prepared_all[Track.MCP]
            prepared = {track: prepared_all[track] for track in resolved.config.track_modes}
            _validate_model_bindings(resolved, prepared)
            _validate_runtime_bounds(resolved, prepared)
            return (
                resolved,
                native_snapshot,
                prepared,
                native.profile.source_revision,
                _model_readiness(resolved),
            )
        # Replayed fixture qualification is not production execution admission.
        # Never fall through to CE certification or invent verified privileges
        # from a configured backend fingerprint. This boundary makes no calls.
        raise V2CampaignRunError("NATIVE_CERTIFICATION_ADMISSION_UNAVAILABLE")
    manifest = json.loads(resolved.source_manifest.read_text())
    metadata = manifest.get("metadata") or {}
    snapshot = build_archive_snapshot(
        resolved.archive,
        manifest,
        product=str(
            metadata.get("benchmark")
            or metadata.get("benchmark_name")
            or metadata.get("generator_profile")
            or ""
        ),
    )
    prepared_all = {track: _prepare_track(resolved, track, snapshot) for track in resolved.tracks}
    oaic = snapshot.product == "oaic-2026-v1" or any(
        item.pair.public.product == "oaic-2026-v1" for item in prepared_all.values()
    )
    if resolved.config.purpose == "diagnostic_canary":
        prepared_all = prepare_diagnostic_canary_track(resolved, snapshot, prepared_all)
    elif oaic or resolved.selected_release is not None:
        prepared_all = prepare_selected_tracks(resolved, snapshot, prepared_all)
    prepared = {track: prepared_all[track] for track in resolved.config.track_modes}
    _validate_model_bindings(resolved, prepared)
    _validate_runtime_bounds(resolved, prepared)
    model_readiness = _model_readiness(resolved)
    mcp_revision = "not-applicable"
    if Track.MCP in prepared:
        if resolved.mcp_dir is None:
            raise V2CampaignRunError("MCP campaign is missing its resolved MCP checkout")
        mcp_revision = _git_revision(resolved.mcp_dir)
        expected_revision = prepared[Track.MCP].profile.mcp_server_revision
        if mcp_revision != expected_revision:
            raise V2CampaignRunError(
                "MCP checkout revision mismatch: "
                f"expected={expected_revision} actual={mcp_revision}"
            )
    return resolved, snapshot, prepared, mcp_revision, model_readiness


async def _health_and_graph(
    resolved: ResolvedV2CampaignConfig,
    snapshot: GraphSnapshot,
    bhce: BHCEClient,
) -> tuple[GraphSnapshot, LiveGraphVerification]:
    health = await bhce.wait_until_healthy(
        timeout_seconds=resolved.config.defaults.health.timeout_seconds,
        poll_interval=resolved.config.defaults.health.poll_interval,
    )
    if not health.ok:
        raise V2CampaignRunError(
            "BloodHound health failed before v2 model work: "
            f"{health.detail} ({health.classification})"
        )
    observed, receipt = await collect_live_snapshot(
        bhce,
        snapshot,
        page_size=resolved.config.defaults.graph_page_size,
    )
    require_live_graph_match(snapshot, observed)
    return observed, receipt


async def _graph_before_track(
    resolved: ResolvedV2CampaignConfig,
    snapshot: GraphSnapshot,
    bhce: BHCEClient,
    *,
    preflight_only: bool,
    shared_preflight: tuple[GraphSnapshot, LiveGraphVerification] | None,
) -> tuple[GraphSnapshot, LiveGraphVerification, bool]:
    """Reuse one exact graph gate only when the campaign cannot execute work."""

    if preflight_only and shared_preflight is not None:
        observed, receipt = shared_preflight
        return observed, receipt, True
    observed, receipt = await _health_and_graph(resolved, snapshot, bhce)
    return observed, receipt, False


def _readiness(
    *,
    resolved: ResolvedV2CampaignConfig,
    snapshot: GraphSnapshot,
    prepared: Mapping[Track, PreparedTrack],
    receipts: Mapping[Track, LiveGraphVerification],
    mcp_revision: str,
    mcp_launcher_provenance: dict[str, str | None] | None,
    model_readiness: tuple[ModelReadinessV2, ...],
    native_session_observations: dict | None = None,
) -> CampaignReadinessV2:
    native_mapping = _native_campaign_provenance(
        prepared.get(Track.MCP),
        native_session_observations,
        mcp_launcher_provenance,
    )
    if native_mapping is not None:
        native = prepared[Track.MCP]
        try:
            graph_receipts = tuple(
                LiveGraphVerification.model_validate_json(
                    receipts[track].model_dump_json(),
                )
                for track in prepared
            )
        except (ValueError, KeyError, AttributeError) as exc:
            raise V2CampaignRunError("NATIVE_READINESS_BINDING_MISMATCH") from exc
        if (
            mcp_revision != native.profile.source_revision
            or any(
                fingerprint != snapshot.graph_fingerprint
                for item in prepared.values()
                for fingerprint in (
                    item.pair.public.graph_fingerprint,
                    item.release.graph_fingerprint,
                    item.live.graph_fingerprint,
                )
            )
            or any(
                receipt.expected_graph_fingerprint != snapshot.graph_fingerprint
                or receipt.observed_graph_fingerprint != snapshot.graph_fingerprint
                or receipt.object_count != len(snapshot.entities)
                or receipt.relationship_count != len(snapshot.relationships)
                for receipt in graph_receipts
            )
        ):
            raise V2CampaignRunError("NATIVE_READINESS_BINDING_MISMATCH")
        mcp_launcher_provenance = native_mapping
    targets = {
        track: (
            native_mapping["backend_binding_fingerprint"]
            if track is Track.MCP and native_mapping is not None
            else canonical_sha256(resolve_bhce_target(resolved.config.defaults.bhce_url))
        )
        for track in prepared
    }
    payload = {
        "purpose": resolved.config.purpose,
        "ranking_eligible": resolved.config.purpose == "official",
        "source_config_fingerprint": resolved.source_config_fingerprint,
        "source_manifest_sha256": _sha256(resolved.source_manifest),
        "archive_sha256": _sha256(resolved.archive),
        "graph_fingerprint": snapshot.graph_fingerprint,
        "target_fingerprint": canonical_sha256(
            {track.value: value for track, value in targets.items()}
        ),
        "mcp_server_revision": mcp_revision,
        "mcp_launcher_provenance": mcp_launcher_provenance,
        "tracks": tuple(
            ReadinessTrackV2(
                track=track,
                target_fingerprint=targets[track],
                public_artifact_fingerprint=item.pair.public.artifact_fingerprint,
                oracle_artifact_fingerprint=item.pair.private.artifact_fingerprint,
                candidate_release_fingerprint=item.release.release_fingerprint,
                live_certification_fingerprint=item.live.artifact_fingerprint,
                capability_profile_fingerprint=item.profile.profile_fingerprint,
                graph_verification_fingerprint=receipts[track].verification_fingerprint,
                task_count=len(item.task_ids),
                schedule=_scheduled_task_roster(resolved, item),
            )
            for track, item in prepared.items()
        ),
        "models": model_readiness,
        "model_count": len(model_readiness),
        "readiness_fingerprint": "0" * 64,
    }
    payload["readiness_fingerprint"] = canonical_sha256(
        {
            "schema_version": READINESS_SCHEMA_VERSION,
            "protocol_version": PROTOCOL_VERSION,
            "runner_version": RUNNER_VERSION,
            **payload,
        },
        exclude_fields=("readiness_fingerprint",),
    )
    return CampaignReadinessV2.model_validate(payload)


async def preflight_v2_campaign(config_path: Path) -> CampaignReadinessV2:
    """Perform exact no-model gates through the same locked campaign path."""

    return await run_v2_campaign(config_path, preflight_only=True)


def _native_campaign_provenance(prepared, observations, historical_launcher):
    """Bind native identity explicitly; never label Bolt with CE containment."""
    from .native_qualification import NativeQualifiedArtifacts, native_launcher_provenance

    if not isinstance(prepared, NativeQualifiedArtifacts):
        if observations is not None:
            raise V2CampaignRunError("NATIVE_PROVENANCE_TRACK_MISMATCH")
        return None
    if historical_launcher is not None or observations is None:
        raise V2CampaignRunError("NATIVE_SESSION_OBSERVATIONS_REQUIRED")
    try:
        return native_launcher_provenance(prepared, observations)
    except ValueError as exc:
        raise V2CampaignRunError("NATIVE_LAUNCHER_PROVENANCE_INVALID") from exc


def _provenance(
    *,
    resolved: ResolvedV2CampaignConfig,
    prepared: PreparedTrack,
    model: V2ModelEntry,
    run_index: int,
    loop: MCPToolLoop | None,
    mcp_launcher_provenance: dict[str, str | None] | None = None,
    native_session_observations: dict | None = None,
) -> ModelRunProvenanceV2:
    native_mapping = _native_campaign_provenance(
        prepared,
        native_session_observations,
        mcp_launcher_provenance,
    )
    if native_mapping is not None:
        mcp_launcher_provenance = native_mapping
    provider_identity = _provider_identity(model, resolved)
    run_identity = RunIdentity(
        provider=model.provider,
        model=model.model,
        run_index=run_index,
        target_fingerprint=(
            native_mapping["backend_binding_fingerprint"]
            if native_mapping is not None
            else canonical_sha256(resolve_bhce_target(resolved.config.defaults.bhce_url))
        ),
        tool_loop=loop.value if loop is not None else None,
    )
    direct_config = DirectQuerySafetyConfig()
    payload = {
        "base": build_run_provenance(prepared.pair, prepared.profile),
        "run_identity": run_identity,
        "source_manifest_sha256": _sha256(resolved.source_manifest),
        "archive_sha256": _sha256(resolved.archive),
        "candidate_release_fingerprint": prepared.release.release_fingerprint,
        "live_certification_fingerprint": prepared.live.artifact_fingerprint,
        "schedule": _scheduled_task_roster(resolved, prepared),
        "containment_config_fingerprint": canonical_sha256(
            {
                "native_backend_binding_fingerprint": native_mapping["backend_binding_fingerprint"],
                "shared_coordination": direct_config.to_jsonable(),
            }
            if native_mapping is not None and prepared.profile.backend == "neo4j"
            else direct_config.to_jsonable()
        ),
        "runtime_implementation_fingerprint": (RUNNER_IMPLEMENTATION_FINGERPRINT),
        "requested_api_surface": provider_identity.requested_api_surface,
        "resolved_api_surface": provider_identity.resolved_api_surface,
        "structured_output_mode": provider_identity.structured_output_mode,
        "endpoint_family": provider_identity.endpoint_family,
        "credential_source": provider_identity.credential_source,
        "mcp_launcher_provenance": mcp_launcher_provenance,
        "runtime_config_fingerprint": canonical_sha256(
            {
                "runner_version": RUNNER_VERSION,
                "source_config_fingerprint": resolved.source_config_fingerprint,
                "model": model.model_dump(mode="json"),
                "track": prepared.track,
                "loop": loop,
                "requested_api_surface": provider_identity.requested_api_surface,
                "resolved_api_surface": provider_identity.resolved_api_surface,
                "structured_output_mode": provider_identity.structured_output_mode,
                "endpoint_family": provider_identity.endpoint_family,
                "credential_source": provider_identity.credential_source,
                "resolved_endpoint_fingerprint": _provider_endpoint_fingerprint(model, resolved),
                **(
                    {
                        "anthropic_binding_fingerprint": (
                            provider_identity.anthropic_binding_fingerprint
                        )
                    }
                    if model.provider == "anthropic"
                    else {}
                ),
                "mcp_launcher_provenance": mcp_launcher_provenance,
                **(
                    {
                        "selection_fingerprint": prepared.selection_fingerprint,
                        **(
                            {"paired_release_fingerprint": prepared.paired_release_fingerprint}
                            if (
                                native_mapping is None
                                or prepared.paired_release_fingerprint is not None
                            )
                            else {}
                        ),
                    }
                    if getattr(prepared, "selection_fingerprint", None) is not None
                    else {}
                ),
            }
        ),
        "provenance_fingerprint": "0" * 64,
    }
    payload["provenance_fingerprint"] = canonical_sha256(
        {
            "schema_version": RUNNER_VERSION,
            "protocol_version": PROTOCOL_VERSION,
            **payload,
        },
        exclude_fields=("provenance_fingerprint",),
    )
    return ModelRunProvenanceV2.model_validate(payload)


def _guard_run_dir(path: Path, provenance: ModelRunProvenanceV2) -> None:
    path.mkdir(parents=True, exist_ok=True)
    guard = path / "campaign-provenance-v2.json"
    if guard.exists():
        existing = ModelRunProvenanceV2.model_validate_json(guard.read_text())
        if existing != provenance:
            raise V2CampaignRunError(f"{path} contains incompatible v2 campaign provenance")
        return
    entries = tuple(path.iterdir())
    if entries:
        raise V2CampaignRunError(f"{path} is non-empty and has no v2 provenance guard")
    _write_model(guard, provenance)


def _attempt(
    task_id: str,
    number: int,
    sample: SampleResult,
    provider: ProviderRunRecord,
    *,
    scheduler_phase: Literal["initial", "immediate_retry", "deferred_retry"] = "initial",
    recovery_round: int = 0,
    started_at_utc: str | None = None,
    completed_at_utc: str | None = None,
) -> ProviderAttemptV2:
    completed_at_utc = completed_at_utc or _utc_now()
    payload = {
        "task_id": task_id,
        "attempt": number,
        "scheduler_phase": scheduler_phase,
        "recovery_round": recovery_round,
        "started_at_utc": started_at_utc or completed_at_utc,
        "completed_at_utc": completed_at_utc,
        "sample": sample,
        "provider": provider,
        "attempt_fingerprint": "0" * 64,
    }
    payload["attempt_fingerprint"] = canonical_sha256(
        payload,
        exclude_fields=("attempt_fingerprint",),
    )
    return ProviderAttemptV2.model_validate(payload)


def _attempt_consumes_retry_budget(attempt: ProviderAttemptV2) -> bool:
    """Do not charge an operator interruption as an infrastructure retry.

    The partial attempt remains durably numbered and auditable, but a later
    process must still be allowed to execute the task's original retry budget.
    """

    sample = getattr(attempt, "sample", None)
    provider = getattr(attempt, "provider", None)
    metrics = getattr(provider, "provider_metrics", {})
    outcome = getattr(getattr(sample, "outcome", None), "value", None)
    return not (
        outcome == "INTERRUPTED"
        and metrics.get("infra_scope") == "operator"
        and metrics.get("infra_error_subtype") == "INTERRUPTED"
    )


def _attempt_is_terminal_on_resume(attempt: ProviderAttemptV2) -> bool:
    """Keep typed non-retryable failures terminal across process restarts."""

    sample = attempt.sample
    if sample.outcome is SampleOutcomeCode.INTERRUPTED:
        return False
    policy = _infrastructure_retry_policy(sample, attempt.provider)
    return policy is not None and policy[1] is False


def _attempt_is_retry_eligible(
    attempt: ProviderAttemptV2,
    *,
    consumed_attempts: int,
    max_attempts: int,
) -> bool:
    if consumed_attempts >= max_attempts:
        return False
    if attempt.sample.outcome is SampleOutcomeCode.INTERRUPTED:
        return True
    policy = _infrastructure_retry_policy(attempt.sample, attempt.provider)
    return policy is not None and policy[1]


def _state(
    *,
    provenance: ModelRunProvenanceV2,
    checkpoint: CheckpointV2,
    attempts: Sequence[ProviderAttemptV2],
    scheduler: RetrySchedulerStateV2 | None = None,
) -> PrivateRunStateV2:
    payload = {
        "provenance_fingerprint": provenance.provenance_fingerprint,
        "checkpoint": checkpoint,
        "attempts": tuple(attempts),
        "scheduler": scheduler or RetrySchedulerStateV2(),
        "state_fingerprint": "0" * 64,
    }
    payload["state_fingerprint"] = canonical_sha256(
        {
            "schema_version": RUN_STATE_SCHEMA_VERSION,
            "protocol_version": PROTOCOL_VERSION,
            **payload,
        },
        exclude_fields=("state_fingerprint",),
    )
    return PrivateRunStateV2.model_validate(payload)


def _load_state(
    path: Path,
    *,
    provenance: ModelRunProvenanceV2,
    prepared: PreparedTrack,
) -> PrivateRunStateV2 | None:
    if not path.exists():
        return None
    state = PrivateRunStateV2.model_validate_json(path.read_text())
    if state.provenance_fingerprint != provenance.provenance_fingerprint:
        raise V2CampaignRunError("private run state belongs to different provenance")
    validate_checkpoint(
        state.checkpoint,
        prepared.pair,
        prepared.profile,
        provenance.run_identity,
    )
    if prepared.selected_task_ids is not None:
        selected = set(prepared.task_ids)
        if any(result.task_id not in selected for result in state.checkpoint.results) or any(
            attempt.task_id not in selected for attempt in state.attempts
        ):
            raise V2CampaignRunError("private run state contains off-selection tasks")
    return state


def _model_report(
    *,
    provenance: ModelRunProvenanceV2,
    prepared: PreparedTrack,
    results: Sequence[SampleResult],
    run_dir: Path,
    before: LiveGraphVerification,
    after: LiveGraphVerification,
) -> ModelPublicReportV2:
    summary = summarize_results(prepared.task_ids, results)
    state = _load_state(
        run_dir / RUN_STATE_NAME,
        provenance=provenance,
        prepared=prepared,
    )
    if state is None:
        raise V2CampaignRunError("missing private run state for completed run")
    if state.scheduler.phase != "complete":
        raise V2CampaignRunError("cannot publish a run with pending retry scheduler work")
    from .native_qualification import NativeQualifiedArtifacts

    operational_metrics = _run_operational_metrics(
        state,
        native=type(prepared) is NativeQualifiedArtifacts,
    )
    report = build_public_report(
        prepared.pair,
        prepared.profile,
        results,
        summary,
        certifications=prepared.certifications,
        scheduled_task_ids=prepared.task_ids,
    )
    payload = {
        "run_identity": provenance.run_identity,
        "candidate_release_fingerprint": prepared.release.release_fingerprint,
        "live_certification_fingerprint": prepared.live.artifact_fingerprint,
        "schedule_fingerprint": provenance.schedule.schedule_fingerprint,
        "graph_verification_before_fingerprint": before.verification_fingerprint,
        "graph_verification_after_fingerprint": after.verification_fingerprint,
        "operational_metrics": operational_metrics,
        "report": report,
        "artifact_fingerprint": "0" * 64,
    }
    payload["artifact_fingerprint"] = canonical_sha256(
        {
            "schema_version": MODEL_REPORT_SCHEMA_VERSION,
            "protocol_version": PROTOCOL_VERSION,
            **payload,
        },
        exclude_fields=("artifact_fingerprint",),
    )
    return ModelPublicReportV2.model_validate(payload)


def _track_completion(
    *,
    resolved: ResolvedV2CampaignConfig,
    prepared: PreparedTrack,
    runs: Sequence[TrackRunCompletionV2],
    before: LiveGraphVerification,
    after: LiveGraphVerification,
) -> TrackCompletionV2:
    payload = {
        "source_config_fingerprint": resolved.source_config_fingerprint,
        "track": prepared.track,
        "candidate_release_fingerprint": prepared.release.release_fingerprint,
        "live_certification_fingerprint": prepared.live.artifact_fingerprint,
        "graph_verification_before_fingerprint": before.verification_fingerprint,
        "graph_verification_after_fingerprint": after.verification_fingerprint,
        "expected_task_count_per_run": len(prepared.task_ids),
        "schedule": _scheduled_task_roster(resolved, prepared),
        "run_count": len(runs),
        "result_count": sum(run.result_count for run in runs),
        "campaign_valid": all(run.campaign_valid for run in runs),
        "runs": tuple(runs),
        "completed_at_utc": _utc_now(),
        "receipt_fingerprint": "0" * 64,
    }
    payload["receipt_fingerprint"] = canonical_sha256(
        {
            "schema_version": TRACK_COMPLETION_SCHEMA_VERSION,
            "protocol_version": PROTOCOL_VERSION,
            "runner_version": RUNNER_VERSION,
            **payload,
        },
        exclude_fields=("receipt_fingerprint",),
    )
    return TrackCompletionV2.model_validate(payload)


def _publish_track_completion(
    *,
    resolved: ResolvedV2CampaignConfig,
    prepared: PreparedTrack,
    completed_runs: Sequence[tuple[ModelRunProvenanceV2, Sequence[SampleResult], Path]],
    before: LiveGraphVerification,
    after: LiveGraphVerification,
) -> tuple[TrackCompletionV2, tuple[str, ...]]:
    """Publish one track after its post-graph gate, independent of later tracks."""

    run_receipts: list[TrackRunCompletionV2] = []
    invalid_campaigns: list[str] = []
    for provenance, results, run_dir in completed_runs:
        report = _model_report(
            provenance=provenance,
            prepared=prepared,
            results=results,
            run_dir=run_dir,
            before=before,
            after=after,
        )
        _write_model(run_dir / "public-report-v2.json", report)
        summary = report.report.summary
        invalid_reasons = tuple(summary.invalid_reasons)
        run_receipts.append(
            TrackRunCompletionV2(
                provider=provenance.run_identity.provider,
                model=provenance.run_identity.model,
                run_index=provenance.run_identity.run_index,
                result_count=len(results),
                public_report_fingerprint=report.artifact_fingerprint,
                campaign_valid=summary.campaign_valid,
                invalid_reasons=invalid_reasons,
            )
        )
        if not summary.campaign_valid:
            invalid_campaigns.append(
                f"{prepared.track.value}/{provenance.run_identity.model}/"
                f"run-{provenance.run_identity.run_index:03d}:" + ",".join(invalid_reasons)
            )
    receipt = _track_completion(
        resolved=resolved,
        prepared=prepared,
        runs=run_receipts,
        before=before,
        after=after,
    )
    _write_model(
        resolved.output_dir / prepared.track.value / "track-completion-v2.private.json",
        receipt,
    )
    return receipt, tuple(invalid_campaigns)


def _infrastructure_retry_policy(
    sample: SampleResult,
    provider: ProviderRunRecord,
) -> tuple[str, bool] | None:
    if sample.execution_class not in {
        ExecutionClass.INFRA_FAILURE,
        ExecutionClass.UNEXECUTED,
    }:
        return None
    scope = provider.provider_metrics.get("infra_scope")
    retryable = provider.provider_metrics.get("infra_retryable")
    if isinstance(scope, str) and isinstance(retryable, bool):
        if scope in {"bloodhound", "provider", "mcp_tool"}:
            return scope, retryable
        # Operator cancellation has separate resume semantics above.  Unknown
        # scopes must never acquire retries solely because an adapter supplied
        # a truthy flag in an otherwise untyped metrics mapping.
        return scope, False
    receipt = provider.direct_receipt
    if receipt is not None:
        return (
            "bloodhound",
            receipt.failure_type
            not in {
                "auth_error",
                "policy_rejected",
            },
        )
    return "unknown", False


def _remaining_cooldown_seconds(
    not_before_utc: str,
    *,
    now: datetime | None = None,
) -> float:
    not_before = datetime.fromisoformat(not_before_utc)
    current = now or datetime.now(UTC)
    return max(0.0, (not_before - current).total_seconds())


async def run_native_development_task(
    *,
    prepared,
    task_id: str,
    native_bridge,
    model: str,
    model_base_url: str | None,
    max_steps: int,
    max_tokens: int = 2048,
    read_timeout_seconds: float = 240.0,
    tool_timeout_seconds: float = 60.0,
    structured_output_mode: str = "prompt_local_validation",
):
    """Private development dispatch through the shared task runner, not a campaign.

    The caller owns a fresh native session/bridge and its cleanup. No candidate
    promotion, public reporting, readiness or durable campaign state is created.
    """
    from .native_feasibility import (
        NativeFeasibilityInputError,
        validate_native_development_track,
    )

    validated = validate_native_development_track(prepared)
    if task_id not in validated.task_ids:
        raise NativeFeasibilityInputError("task is outside the selected native cell")
    index = validated.task_ids.index(task_id)
    task = validated.pair.public.tasks[index]
    return await run_mcp_model_task_v2(
        task=task,
        oracle=validated.pair.private.oracles[index],
        resolver=IdentityResolver(validated.pair.private.identity_catalog),
        profile=validated.profile,
        bundle=None,
        native_bridge=native_bridge,
        certified=False,
        model=model,
        model_base_url=model_base_url,
        tool_loop=task.binding.mcp_tool_loop,
        max_steps=max_steps,
        max_tokens=max_tokens,
        read_timeout_seconds=read_timeout_seconds,
        tool_timeout_seconds=tool_timeout_seconds,
        structured_output_mode=structured_output_mode,
        graph_fact_registry=validated.pair.private.graph_fact_registry,
    )


async def _run_model(
    *,
    resolved: ResolvedV2CampaignConfig,
    prepared: PreparedTrack,
    model: V2ModelEntry,
    run_index: int,
    bhce: BHCEClient,
    coordinator: DirectQueryCoordinator,
    loop: MCPToolLoop | None,
    runs_total: int,
    mcp_launcher_runtime: MCPLauncherRuntime | None = None,
    progress: ProgressReporter | None = None,
    lifecycle: _CampaignLifecycleController | None = None,
    native_session_work=None,
    native_observations: dict | None = None,
) -> tuple[ModelRunProvenanceV2, tuple[SampleResult, ...]]:
    from uuid import uuid4

    from .campaign_config import V2NativeMCPConfig
    from .native_feasibility import NativeDevelopmentTrack
    from .native_mcp_runtime import (
        NativeMCPSession,
        NativeModelToolBridge,
        NativeSessionCleanupPending,
    )
    from .native_qualification import NativeQualifiedArtifacts

    if isinstance(prepared, NativeDevelopmentTrack):
        raise V2CampaignRunError("NATIVE_DEVELOPMENT_NOT_QUALIFIED")
    native = type(prepared) is NativeQualifiedArtifacts
    if native:
        prepared.__post_init__()
        if not callable(native_session_work) or native_observations is None:
            raise V2CampaignRunError("NATIVE_ATTEMPT_SCOPE_REQUIRED")
        configured = resolved.config.defaults.mcp
        if (
            not isinstance(configured, V2NativeMCPConfig)
            or configured.implementation_id != prepared.profile.implementation_id
            or configured.backend != prepared.profile.backend
            or configured.runtime_fingerprint != prepared.profile.runtime_fingerprint
            or configured.dependency_lock_fingerprint
            != prepared.profile.dependency_lock_fingerprint
            or loop is None
            or loop.value != configured.tool_loop
            or mcp_launcher_runtime is not None
        ):
            raise V2CampaignRunError("NATIVE_ATTEMPT_SCOPE_REQUIRED")
    elif native_session_work is not None or native_observations is not None:
        raise V2CampaignRunError("NATIVE_ATTEMPT_TRACK_MISMATCH")
    coordinator.require_confirmed_native_completion()
    provenance = _provenance(
        resolved=resolved,
        prepared=prepared,
        model=model,
        run_index=run_index,
        loop=loop,
        mcp_launcher_provenance=(
            mcp_launcher_runtime.provenance(MCPLauncherConfig.local_checkout(resolved.mcp_dir))
            if mcp_launcher_runtime is not None and resolved.mcp_dir is not None
            else None
        ),
        **({"native_session_observations": native_observations} if native else {}),
    )
    run_dir = resolved.output_dir / prepared.track.value / model.name / f"run-{run_index:03d}"
    _guard_run_dir(run_dir, provenance)
    state_path = run_dir / RUN_STATE_NAME
    state = _load_state(
        state_path,
        provenance=provenance,
        prepared=prepared,
    )
    results = list(state.checkpoint.results if state is not None else ())
    attempts = list(state.attempts if state is not None else ())
    if lifecycle is not None:
        lifecycle.record_checkpoint(
            track=prepared.track,
            model_name=model.name,
            run_index=run_index,
            result_count=len(results),
        )
    retryable_execution_classes = {ExecutionClass.INFRA_FAILURE, ExecutionClass.UNEXECUTED}
    latest_attempts: dict[str, ProviderAttemptV2] = {}
    for attempt in attempts:
        latest_attempts[attempt.task_id] = attempt
    completed = {
        result.task_id
        for result in results
        if result.execution_class not in retryable_execution_classes
    }
    completed.update(
        task_id
        for task_id, attempt in latest_attempts.items()
        if _attempt_is_terminal_on_resume(attempt)
    )
    _emit_progress(
        progress,
        (
            f"\n[{prepared.track.value}] {model.name} -> "
            f"{model.requested_model} run {run_index}/{runs_total} "
            f"({len(prepared.task_ids)} tasks, {len(completed)} resumed)"
        ),
    )

    bundle = None
    if prepared.track is Track.MCP and not native:
        if resolved.mcp_dir is None:
            raise V2CampaignRunError("MCP run is missing its resolved MCP checkout")
        bundle = await _load_bloodhound_mcp_bundle(
            MCPLauncherConfig.local_checkout(resolved.mcp_dir),
            include_resources=False,
            include_prompt=True,
            cypher_executor=coordinator.execute,
            launcher_runtime=mcp_launcher_runtime,
        )

    task_by_id = {task.task_id: task for task in prepared.pair.public.tasks}
    registry = OracleRegistry(prepared.pair.private)
    resolver = IdentityResolver(prepared.pair.private.identity_catalog)
    provider_options = _provider_options(
        model,
        resolved.config.defaults.reasoning_effort,
    )
    scheduler = (
        getattr(state, "scheduler", RetrySchedulerStateV2())
        if state is not None
        else RetrySchedulerStateV2()
    )
    max_attempts = resolved.config.defaults.max_infra_retries + 1
    retry_config = getattr(resolved.config.defaults, "infra_retry", None)
    immediate_retries = getattr(retry_config, "immediate_retries", 1)
    deferred_cooldown_seconds = getattr(
        retry_config,
        "deferred_cooldown_seconds",
        300.0,
    )
    primary_attempt_limit = min(
        max_attempts,
        1 + immediate_retries,
    )
    task_positions = {task_id: index for index, task_id in enumerate(prepared.task_ids, 1)}

    def _task_attempts(task_id: str) -> tuple[ProviderAttemptV2, ...]:
        return tuple(attempt for attempt in attempts if attempt.task_id == task_id)

    def _consumed_attempts(task_id: str) -> int:
        return sum(_attempt_consumes_retry_budget(attempt) for attempt in _task_attempts(task_id))

    def _latest_attempt(task_id: str) -> ProviderAttemptV2 | None:
        rows = _task_attempts(task_id)
        return rows[-1] if rows else None

    def _eligible_for_retry(task_id: str) -> bool:
        latest = _latest_attempt(task_id)
        if latest is None:
            return False
        return _attempt_is_retry_eligible(
            latest,
            consumed_attempts=_consumed_attempts(task_id),
            max_attempts=max_attempts,
        )

    def _persist(scheduler_state: RetrySchedulerStateV2) -> None:
        checkpoint = build_checkpoint(
            prepared.pair,
            prepared.profile,
            provenance.run_identity,
            results=results,
        )
        _write_model(
            state_path,
            _state(
                provenance=provenance,
                checkpoint=checkpoint,
                attempts=attempts,
                scheduler=scheduler_state,
            ),
        )
        if lifecycle is not None:
            lifecycle.record_checkpoint(
                track=prepared.track,
                model_name=model.name,
                run_index=run_index,
                result_count=len(results),
            )

    def _record_attempt(
        *,
        task_id: str,
        sample: SampleResult,
        provider: ProviderRunRecord,
        phase: Literal["initial", "immediate_retry", "deferred_retry"],
        recovery_round: int,
        started_at_utc: str,
        scheduler_state: RetrySchedulerStateV2,
    ) -> None:
        nonlocal results
        attempt_number = (
            max(
                (attempt.attempt for attempt in _task_attempts(task_id)),
                default=0,
            )
            + 1
        )
        attempts.append(
            _attempt(
                task_id,
                attempt_number,
                sample,
                provider,
                scheduler_phase=phase,
                recovery_round=recovery_round,
                started_at_utc=started_at_utc,
                completed_at_utc=_utc_now(),
            )
        )
        results = [result for result in results if result.task_id != task_id]
        results.append(sample)
        results.sort(key=lambda result: task_positions[result.task_id])
        _persist(scheduler_state)

    async def _execute_attempt(
        task_id: str,
        *,
        phase: Literal["initial", "immediate_retry", "deferred_retry"],
        recovery_round: int = 0,
        scheduler_state: RetrySchedulerStateV2,
        completed_scheduler_state: RetrySchedulerStateV2 | None = None,
    ) -> tuple[SampleResult, ProviderRunRecord]:
        coordinator.require_confirmed_native_completion()
        task = task_by_id[task_id]
        oracle = registry.for_task(task_id)
        task_started = time.monotonic()
        started_at_utc = _utc_now()
        _emit_progress(
            progress,
            (
                f"  [{task_positions[task_id]}/{len(prepared.task_ids)}] {task.task_id} "
                f"({task.claim_kind}, {task.answer_policy.kind}; phase={phase}"
                + (f", recovery_round={recovery_round}" if recovery_round else "")
                + ")"
            ),
        )
        sample: SampleResult
        provider: ProviderRunRecord
        cancellation: V2ModelTaskCancelled | None = None
        direct_preflight_blocked = False
        mcp_preflight_blocked = False
        native_recorded = False
        # Operator interruptions are auditable attempts but do not consume the
        # lifetime retry budget and must not hide the durable infrastructure
        # scope that caused this retry to be scheduled.
        prior_attempt = next(
            (
                attempt
                for attempt in reversed(_task_attempts(task_id))
                if _attempt_consumes_retry_budget(attempt)
            ),
            None,
        )
        prior_retry_policy = (
            _infrastructure_retry_policy(prior_attempt.sample, prior_attempt.provider)
            if prior_attempt is not None
            else None
        )
        bloodhound_retry = (
            phase != "initial"
            and prior_retry_policy is not None
            and (prior_retry_policy[0] == "bloodhound")
        )
        model_base_url = _model_base_url(model, resolved)
        try:
            sample: SampleResult
            provider: ProviderRunRecord
            if prepared.track is Track.DIRECT:
                # The circuit is process-local.  A resumed deferred retry must
                # therefore derive the health gate from its durable preceding
                # attempt as well as the current in-memory circuit state.
                if coordinator.circuit_open or bloodhound_retry:
                    health = await bhce.wait_until_healthy(
                        timeout_seconds=(resolved.config.defaults.health.timeout_seconds),
                        poll_interval=(resolved.config.defaults.health.poll_interval),
                    )
                    if health.ok:
                        coordinator.close_circuit()
                    else:
                        direct_preflight_blocked = True
                        sample, provider = unexecuted_model_record(
                            task=task,
                            oracle=oracle,
                            model=model.requested_model,
                            surface=prepared.track.value,
                            detail=("BloodHound circuit remained open before provider execution"),
                        )
                if not direct_preflight_blocked:
                    transport_options = (
                        {
                            "transport": partial(
                                call_provider_text,
                                anthropic_binding=_anthropic_binding(model, resolved),
                            )
                        }
                        if model.provider == "anthropic"
                        else {}
                    )
                    _outcome, sample, provider = await run_direct_model_task_v2(
                        coordinator=coordinator,
                        task=task,
                        oracle=oracle,
                        resolver=resolver,
                        model=model.requested_model,
                        model_base_url=model_base_url,
                        max_tokens=getattr(model, "max_output_tokens", 2048),
                        api_surface=getattr(model, "api_surface", "auto"),
                        structured_output_mode=getattr(
                            model,
                            "structured_output_mode",
                            "prompt_local_validation",
                        ),
                        ollama_options=provider_options,
                        **transport_options,
                    )
            else:
                if (bundle is None and not native) or loop is None:
                    raise AssertionError("MCP model run is missing its runtime bundle")
                if (
                    native
                    and prepared.profile.backend == "neo4j"
                    and (coordinator.circuit_open or bloodhound_retry)
                ):
                    mcp_preflight_blocked = True
                    sample, provider = unexecuted_model_record(
                        task=task,
                        oracle=oracle,
                        model=model.requested_model,
                        surface=prepared.track.value,
                        detail="Native Bolt backend recovery requires a fresh verified interval",
                    )
                elif coordinator.circuit_open or bloodhound_retry:
                    health = await bhce.wait_until_healthy(
                        timeout_seconds=(resolved.config.defaults.health.timeout_seconds),
                        poll_interval=(resolved.config.defaults.health.poll_interval),
                    )
                    if health.ok:
                        coordinator.close_circuit()
                    else:
                        mcp_preflight_blocked = True
                        sample, provider = unexecuted_model_record(
                            task=task,
                            oracle=oracle,
                            model=model.requested_model,
                            surface=prepared.track.value,
                            detail=("BloodHound circuit remained open before provider execution"),
                        )
                if not mcp_preflight_blocked:
                    configured_mcp = resolved.config.defaults.mcp
                    if configured_mcp is None:
                        raise V2CampaignRunError("MCP run is missing defaults.mcp configuration")

                    async def invoke_mcp(native_bridge=None):
                        return await run_mcp_model_task_v2(
                            task=task,
                            oracle=oracle,
                            resolver=resolver,
                            profile=prepared.profile,
                            bundle=bundle,
                            model=model.requested_model,
                            model_base_url=model_base_url,
                            max_tokens=getattr(model, "max_output_tokens", 2048),
                            api_surface=getattr(model, "api_surface", "auto"),
                            structured_output_mode=getattr(
                                model,
                                "structured_output_mode",
                                "prompt_local_validation",
                            ),
                            tool_loop=loop,
                            max_steps=configured_mcp.max_steps,
                            ollama_options=provider_options,
                            telemetry_adapter=(configured_mcp.telemetry_adapter),
                            read_timeout_seconds=(configured_mcp.read_timeout_seconds),
                            tool_timeout_seconds=(configured_mcp.tool_timeout_seconds),
                            graph_fact_registry=(prepared.pair.private.graph_fact_registry),
                            **(
                                {"native_bridge": native_bridge, "native_qualification": prepared}
                                if native
                                else {}
                            ),
                            **(
                                {"anthropic_binding": _anthropic_binding(model, resolved)}
                                if native and model.provider == "anthropic"
                                else {}
                            ),
                        )

                    if native:
                        admission_checked = False
                        work_started = False

                        async def before_work(observations):
                            nonlocal admission_checked
                            if admission_checked or work_started:
                                raise V2CampaignRunError("NATIVE_ATTEMPT_SCOPE_REUSED")
                            current = _provenance(
                                resolved=resolved,
                                prepared=prepared,
                                model=model,
                                run_index=run_index,
                                loop=loop,
                                native_session_observations=observations,
                            )
                            if current != provenance:
                                raise V2CampaignRunError("NATIVE_ATTEMPT_PROVENANCE_MISMATCH")
                            admission_checked = True

                        async def native_work(session):
                            nonlocal native_recorded, work_started
                            if (
                                not admission_checked
                                or work_started
                                or not isinstance(session, NativeMCPSession)
                                or session._query_coordinator is not coordinator
                                or session._capability_profile != prepared.profile
                            ):
                                raise V2CampaignRunError("NATIVE_ATTEMPT_SCOPE_INVALID")
                            work_started = True
                            coordinator.require_confirmed_native_completion()
                            bridge = NativeModelToolBridge(
                                session,
                                task,
                                attempt_id=uuid4().hex,
                                native_certification=prepared.certifications[task_id],
                            )
                            interrupted = None
                            try:
                                outcome, record = await invoke_mcp(bridge)
                                result = outcome.sample
                            except V2ModelTaskCancelled as exc:
                                result, record, interrupted = exc.sample, exc.provider, exc
                            except NativeSessionCleanupPending:
                                raise
                            except Exception as exc:
                                result, record = contain_model_runtime_exception(
                                    task=task,
                                    oracle=oracle,
                                    model=model.requested_model,
                                    surface=prepared.track.value,
                                    error=exc,
                                )
                            native_recorded = True
                            _record_attempt(
                                task_id=task_id,
                                sample=result,
                                provider=record,
                                phase=phase,
                                recovery_round=recovery_round,
                                started_at_utc=started_at_utc,
                                scheduler_state=(
                                    scheduler_state
                                    if interrupted is not None
                                    else completed_scheduler_state or scheduler_state
                                ),
                            )
                            if interrupted is not None:
                                raise interrupted
                            coordinator.require_confirmed_native_completion()
                            return result, record

                        (sample, provider), _observations = await native_session_work(
                            work=native_work,
                            before_work=before_work,
                        )
                        if not native_recorded:
                            raise V2CampaignRunError("NATIVE_ATTEMPT_NOT_EXECUTED")
                    else:
                        outcome, provider = await invoke_mcp()
                        sample = outcome.sample
        except V2ModelTaskCancelled as exc:
            if native_recorded:
                raise
            sample = exc.sample
            provider = exc.provider
            cancellation = exc
        except NativeSessionCleanupPending:
            raise
        except Exception as exc:
            if native_recorded:
                raise
            sample, provider = contain_model_runtime_exception(
                task=task,
                oracle=oracle,
                model=model.requested_model,
                surface=prepared.track.value,
                error=exc,
            )
        if not native_recorded:
            _record_attempt(
                task_id=task_id,
                sample=sample,
                provider=provider,
                phase=phase,
                recovery_round=recovery_round,
                started_at_utc=started_at_utc,
                scheduler_state=(
                    scheduler_state
                    if cancellation is not None
                    else completed_scheduler_state
                    if completed_scheduler_state is not None
                    else scheduler_state
                ),
            )
        if cancellation is not None:
            raise cancellation
        coordinator.require_confirmed_native_completion()
        _emit_progress(
            progress,
            _task_completion_progress(
                sample=sample,
                provider=provider,
                task_elapsed_seconds=time.monotonic() - task_started,
                results=results,
            ),
        )
        return sample, provider

    if scheduler.phase == "primary":
        for task_id in prepared.task_ids:
            if task_id in completed:
                continue
            latest = _latest_attempt(task_id)
            if latest is not None:
                policy = _infrastructure_retry_policy(latest.sample, latest.provider)
                if latest.sample.outcome is not SampleOutcomeCode.INTERRUPTED and (
                    policy is None or not policy[1]
                ):
                    continue
            consumed = _consumed_attempts(task_id)
            if consumed >= max_attempts:
                _emit_progress(
                    progress,
                    f"  {task_id} retry budget already exhausted; no provider call made",
                )
                continue
            while consumed < primary_attempt_limit:
                phase = "initial" if consumed == 0 else "immediate_retry"
                sample, provider = await _execute_attempt(
                    task_id,
                    phase=phase,
                    scheduler_state=RetrySchedulerStateV2(),
                )
                policy = _infrastructure_retry_policy(sample, provider)
                if policy is None or not policy[1]:
                    break
                consumed = _consumed_attempts(task_id)
                if consumed >= primary_attempt_limit or consumed >= max_attempts:
                    break
                scope, _retryable = policy
                if scope not in {"bloodhound", "provider", "mcp_tool"}:
                    break
                _emit_progress(
                    progress,
                    f"           ↻ {sample.outcome.value}; retrying infrastructure immediately",
                )

    while True:
        eligible = tuple(task_id for task_id in prepared.task_ids if _eligible_for_retry(task_id))
        if not eligible:
            scheduler = RetrySchedulerStateV2(phase="complete")
            _persist(scheduler)
            break

        if scheduler.phase in {"deferred_cooldown", "deferred_retry"}:
            recovery_round = scheduler.recovery_round
            round_tasks = tuple(
                task_id for task_id in scheduler.pending_task_ids if task_id in eligible
            )
            if not round_tasks:
                scheduler = RetrySchedulerStateV2()
                continue
        else:
            recovery_round = (
                max(
                    (attempt.recovery_round for attempt in attempts),
                    default=0,
                )
                + 1
            )
            round_tasks = eligible
            not_before = datetime.now(UTC) + timedelta(seconds=deferred_cooldown_seconds)
            scheduler = RetrySchedulerStateV2(
                phase="deferred_cooldown",
                recovery_round=recovery_round,
                pending_task_ids=round_tasks,
                deferred_not_before_utc=not_before.isoformat(),
            )
            _persist(scheduler)

        if scheduler.phase == "deferred_cooldown":
            delay = _remaining_cooldown_seconds(scheduler.deferred_not_before_utc or "")
            _emit_progress(
                progress,
                (
                    f"[{prepared.track.value}] deferred infrastructure recovery round "
                    f"{recovery_round}: {len(round_tasks)} task(s), cooldown={delay:.1f}s"
                ),
            )
            if delay:
                await asyncio.sleep(delay)
            scheduler = RetrySchedulerStateV2(
                phase="deferred_retry",
                recovery_round=recovery_round,
                pending_task_ids=round_tasks,
            )
            _persist(scheduler)

        for index, task_id in enumerate(round_tasks):
            if not _eligible_for_retry(task_id):
                continue
            remaining = round_tasks[index:]
            scheduler = RetrySchedulerStateV2(
                phase="deferred_retry",
                recovery_round=recovery_round,
                pending_task_ids=remaining,
            )
            _persist(scheduler)
            remaining_after_attempt = round_tasks[index + 1 :]
            completed_scheduler = (
                RetrySchedulerStateV2(
                    phase="deferred_retry",
                    recovery_round=recovery_round,
                    pending_task_ids=remaining_after_attempt,
                )
                if remaining_after_attempt
                else RetrySchedulerStateV2()
            )
            await _execute_attempt(
                task_id,
                phase="deferred_retry",
                recovery_round=recovery_round,
                scheduler_state=scheduler,
                completed_scheduler_state=completed_scheduler,
            )
        scheduler = RetrySchedulerStateV2()
        _persist(scheduler)

    summary = summarize_results(prepared.task_ids, results)
    _write_model(
        run_dir / "campaign-summary-v2.private.json",
        summary,
    )
    _emit_progress(
        progress,
        (
            f"[{prepared.track.value}] {model.name} run {run_index}/{runs_total} "
            f"complete: correct={summary.correct}/{summary.correct + summary.incorrect}, "
            f"proof={summary.proof_failures}, "
            f"infra={summary.infrastructure_failures}, "
            f"campaign_valid={summary.campaign_valid}"
        ),
    )
    return provenance, tuple(results)


async def _run_prepared_v2_campaign(
    *,
    resolved: ResolvedV2CampaignConfig,
    snapshot: GraphSnapshot,
    prepared: Mapping[Track, PreparedTrack],
    mcp_revision: str,
    model_readiness: tuple[ModelReadinessV2, ...],
    preflight_only: bool,
    progress: ProgressReporter | None,
    lifecycle: _CampaignLifecycleController,
) -> CampaignReadinessV2:
    from ori.mcp_launcher import NativeMCPLauncherConfig

    from .native_bolt_runtime import run_native_bolt_session_work
    from .native_ce_runtime import native_ce_connection, run_native_ce_session_work
    from .native_mcp_runtime import NativeCallDecision, NativeSessionCleanupPending
    from .native_qualification import NativeQualifiedArtifacts

    receipts_before: dict[Track, LiveGraphVerification] = {}
    shared_preflight: tuple[GraphSnapshot, LiveGraphVerification] | None = None
    invalid_campaigns: list[str] = []
    native = prepared.get(Track.MCP)
    native = native if type(native) is NativeQualifiedArtifacts else None
    native_bolt = native is not None and native.profile.backend == "neo4j"
    native_observations = None
    connection = None
    native_launcher = None
    client_options = parse_bhce_url(resolved.config.defaults.bhce_url)
    if native is not None:
        paths = resolved.native_mcp_paths
        if paths is None or resolved.mcp_dir is None:
            raise V2CampaignRunError("NATIVE_ATTEMPT_SCOPE_REQUIRED")
        if native_bolt:
            prefix = "BLOODHOUND" if native.profile.implementation_id == "mordavid" else "NEO4J"
            connection = {
                f"{prefix}_{key}": os.environ.get(f"{prefix}_{key}", "")
                for key in ("URI", "USERNAME", "PASSWORD")
            }
            if any(not value.strip() for value in connection.values()):
                raise V2CampaignRunError("NATIVE_CONNECTION_ENVIRONMENT_INVALID")
        else:
            connection = {
                f"BLOODHOUND_{key}": os.environ.get(f"BLOODHOUND_{key}", "")
                for key in ("DOMAIN", "TOKEN_ID", "TOKEN_KEY")
            }
            connection.update(
                {
                    f"BLOODHOUND_{key}": os.environ[f"BLOODHOUND_{key}"]
                    for key in ("SCHEME", "PORT", "VERIFY_TLS")
                    if f"BLOODHOUND_{key}" in os.environ
                }
            )
            connection, client_options, _binding = native_ce_connection(connection)
            if resolve_bhce_target(resolved.config.defaults.bhce_url) != {
                key: client_options[key] for key in ("scheme", "domain", "port")
            }:
                raise V2CampaignRunError("NATIVE_CE_TARGET_MISMATCH")
        native_launcher = NativeMCPLauncherConfig(
            implementation_id=native.profile.implementation_id,
            checkout=resolved.mcp_dir,
            python_executable=paths.python_executable,
            runtime_roots=paths.runtime_roots,
            runtime_fingerprint=native.profile.runtime_fingerprint,
            dependency_lock=paths.dependency_lock,
            dependency_lock_fingerprint=native.profile.dependency_lock_fingerprint,
        )
    mcp_launcher = None
    if Track.MCP in resolved.config.track_modes and native is None:
        if resolved.mcp_dir is None:
            raise V2CampaignRunError("MCP campaign is missing its resolved MCP checkout")
        mcp_launcher = MCPLauncherConfig.local_checkout(resolved.mcp_dir)
    mcp_launcher_runtime = (
        resolve_mcp_launcher_runtime(mcp_launcher) if mcp_launcher is not None else None
    )
    mcp_launcher_provenance = (
        mcp_launcher_runtime.provenance(mcp_launcher)
        if mcp_launcher_runtime is not None and mcp_launcher is not None
        else None
    )

    def native_graph_receipt(observations, stage):
        interval = observations[stage]
        if not native_bolt:
            return interval["graph_verification"]
        databases = resolved.config.defaults.mcp.databases
        if set(interval["graphs"]) != set(databases):
            raise V2CampaignRunError("NATIVE_BOLT_GRAPH_SCOPE_MISMATCH")
        # The owner has already verified every destination against the same
        # expected snapshot. The legacy report needs one representative receipt;
        # the complete interval remains in private native observations.
        return interval["graphs"][databases[0]]["graph_verification"]

    client_scope = (
        nullcontext(None)
        if native_bolt and Track.DIRECT not in prepared
        else BHCEClient(**client_options)
    )
    async with client_scope as bhce:
        direct_config = DirectQuerySafetyConfig()
        coordinator = DirectQueryCoordinator(
            bhce=bhce,
            config=direct_config,
            deny_cache=QueryDenyCache(
                (resolved.output_dir / "direct-query-deny-cache-v3.private.json"),
                manifest_fingerprint=_sha256(resolved.source_manifest),
                policy_version=direct_config.policy_version,
            ),
            **(
                {"native_backend_binding_fingerprint": native.profile.backend_binding_fingerprint}
                if native_bolt
                else {}
            ),
        )

        async def run_models(track, *, attempt_scope=None, observations=None):
            completed_runs = []
            for model in resolved.config.models:
                loop = _model_loop(model, resolved) if track is Track.MCP else None
                runs = (
                    model.runs_per_model
                    if model.runs_per_model is not None
                    else resolved.config.defaults.runs_per_model
                )
                for run_index in range(1, runs + 1):
                    provenance, results = await _run_model(
                        resolved=resolved,
                        prepared=prepared[track],
                        model=model,
                        run_index=run_index,
                        bhce=bhce,
                        coordinator=coordinator,
                        loop=loop,
                        runs_total=runs,
                        mcp_launcher_runtime=(mcp_launcher_runtime if track is Track.MCP else None),
                        progress=progress,
                        lifecycle=lifecycle,
                        **(
                            {
                                "native_session_work": attempt_scope,
                                "native_observations": observations,
                            }
                            if attempt_scope is not None
                            else {}
                        ),
                    )
                    run_dir = (
                        resolved.output_dir / track.value / model.name / f"run-{run_index:03d}"
                    )
                    completed_runs.append((provenance, results, run_dir))
            return completed_runs

        def finish_track(track, completed_runs, before, after):
            _write_model(
                resolved.output_dir / track.value / "graph-verification-after-v2.private.json",
                after,
            )
            _emit_progress(
                progress,
                _graph_verification_progress(
                    track=track,
                    stage="post",
                    receipt=after,
                ),
            )
            completion, track_invalid = _publish_track_completion(
                resolved=resolved,
                prepared=prepared[track],
                completed_runs=completed_runs,
                before=before,
                after=after,
            )
            lifecycle.record_track(completion)
            invalid_campaigns.extend(track_invalid)
            _emit_progress(
                progress,
                f"[{track.value}] reports published and track completion "
                f"recorded ({completion.receipt_fingerprint[:12]})",
            )

        for track in resolved.config.track_modes:
            lifecycle.activate_track(track)
            if track is Track.MCP and native is not None:
                coordinator.require_confirmed_native_completion()
                count_runs = sum(
                    model.runs_per_model
                    if model.runs_per_model is not None
                    else resolved.config.defaults.runs_per_model
                    for model in resolved.config.models
                )
                attempts_per_task = resolved.config.defaults.max_infra_retries + 1
                tasks = native.pair.public.tasks
                task_calls = sum(
                    max(task.binding.bounds.max_tool_calls, resolved.config.defaults.mcp.max_steps)
                    for task in tasks
                )
                max_calls = max(1, count_runs * attempts_per_task * task_calls)
                retry_config = getattr(resolved.config.defaults, "infra_retry", None)
                cooldown = getattr(retry_config, "deferred_cooldown_seconds", 300.0)
                timeout = 1200.0 + count_runs * (
                    attempts_per_task * sum(task.binding.bounds.timeout_seconds for task in tasks)
                    + attempts_per_task
                    * len(tasks)
                    * resolved.config.defaults.health.timeout_seconds
                    + resolved.config.defaults.max_infra_retries * cooldown
                )

                async def before_native_work(observations):
                    nonlocal native_observations
                    native_observations = observations
                    _atomic_write(
                        resolved.output_dir / track.value / "native-session-before-v2.private.json",
                        observations,
                    )
                    before = LiveGraphVerification.model_validate_json(
                        json.dumps(
                            native_graph_receipt(observations, "graph_before"),
                        )
                    )
                    receipts_before[track] = before
                    _write_model(
                        resolved.output_dir
                        / track.value
                        / "graph-verification-before-v2.private.json",
                        before,
                    )
                    _emit_progress(
                        progress,
                        _graph_verification_progress(
                            track=track,
                            stage="pre",
                            receipt=before,
                        ),
                    )

                async def native_work(session):
                    if preflight_only:
                        return []

                    async def attempt_scope(*, work, before_work):
                        await before_work(native_observations)
                        return await work(session), native_observations

                    return await run_models(
                        track, attempt_scope=attempt_scope, observations=native_observations
                    )

                def guard(*args):
                    coordinator.require_confirmed_native_completion()
                    return NativeCallDecision(True, "native_read_only_shared_backend_containment")

                track_dir = resolved.output_dir / track.value
                track_dir.mkdir(parents=True, exist_ok=True)
                descriptor, _log_path = tempfile.mkstemp(
                    prefix="native-stderr-",
                    suffix=".private.log",
                    dir=track_dir,
                )
                with os.fdopen(descriptor, "w", encoding="utf-8") as private_log:
                    try:
                        owner = (
                            run_native_bolt_session_work
                            if native_bolt
                            else run_native_ce_session_work
                        )
                        completed_runs, observations = await owner(
                            config=native_launcher,
                            profile=native.profile,
                            connection=connection,
                            private_stderr=private_log,
                            expected=snapshot,
                            guard=guard,
                            max_calls=max_calls,
                            work=native_work,
                            before_work=before_native_work,
                            timeout_seconds=timeout,
                            session_call_timeout_seconds=(
                                resolved.config.defaults.mcp.tool_timeout_seconds
                            ),
                            page_size=resolved.config.defaults.graph_page_size,
                            query_coordinator=coordinator,
                            **(
                                {"databases": resolved.config.defaults.mcp.databases}
                                if native_bolt
                                else {}
                            ),
                        )
                    except NativeSessionCleanupPending as pending:
                        try:
                            _emit_progress(
                                progress, "NATIVE_SESSION_CLEANUP_PENDING: waiting; no publication"
                            )
                        finally:
                            await pending.wait_for_cleanup()
                        raise
                native_observations = observations
                _atomic_write(track_dir / "native-session-completed-v2.private.json", observations)
                if not preflight_only:
                    after = LiveGraphVerification.model_validate_json(
                        json.dumps(
                            native_graph_receipt(observations, "graph_after"),
                        )
                    )
                    finish_track(track, completed_runs, receipts_before[track], after)
                continue
            _emit_progress(
                progress,
                f"[{track.value}] verifying BloodHound graph before track",
            )
            observed, before, reused = await _graph_before_track(
                resolved,
                snapshot,
                bhce,
                preflight_only=preflight_only,
                shared_preflight=shared_preflight,
            )
            if preflight_only and shared_preflight is None:
                shared_preflight = (observed, before)
            receipts_before[track] = before
            track_dir = resolved.output_dir / track.value
            _write_model(
                track_dir / "graph-verification-before-v2.private.json",
                before,
            )
            _emit_progress(
                progress,
                _graph_verification_progress(
                    track=track,
                    stage="pre",
                    receipt=before,
                ),
            )
            if reused:
                _emit_progress(
                    progress,
                    (
                        f"[{track.value}] reused no-model graph verification "
                        f"({before.observed_graph_fingerprint[:12]})"
                    ),
                )
            if preflight_only:
                # No provider, tool, or model-authored query can run in this
                # mode. One exact live snapshot therefore proves the graph for
                # every prepared track; repeated post-track snapshots add no
                # evidence and can turn readiness into an hours-long operation.
                continue
            completed_runs = await run_models(track)
            _emit_progress(
                progress,
                f"[{track.value}] verifying BloodHound graph after track",
            )
            _observed, after = await _health_and_graph(
                resolved,
                snapshot,
                bhce,
            )
            finish_track(track, completed_runs, before, after)

    readiness = _readiness(
        resolved=resolved,
        snapshot=snapshot,
        prepared=prepared,
        receipts=receipts_before,
        mcp_revision=mcp_revision,
        mcp_launcher_provenance=mcp_launcher_provenance,
        model_readiness=model_readiness,
        **({"native_session_observations": native_observations} if native is not None else {}),
    )
    _write_model(
        resolved.output_dir / "v2-run-readiness.private.json",
        readiness,
    )
    if preflight_only:
        _emit_progress(progress, "V2 CAMPAIGN: readiness checks complete")
        return readiness

    if invalid_campaigns:
        raise V2CampaignRunError(
            "v2 campaign completed with invalid execution accounting: "
            + "; ".join(invalid_campaigns)
        )
    _emit_progress(progress, "V2 CAMPAIGN: all model runs and reports complete")
    return readiness


async def run_v2_campaign(
    config_path: Path,
    *,
    preflight_only: bool = False,
    progress: ProgressReporter | None = None,
) -> CampaignReadinessV2:
    """Run exact V2 candidate catalogs, or stop after readiness when requested."""

    from .native_mcp_runtime import NativeSessionCleanupPending

    _emit_progress(progress, "V2 CAMPAIGN: validating sealed artifacts and capabilities")
    (
        resolved,
        snapshot,
        prepared,
        mcp_revision,
        model_readiness,
    ) = prepare_v2_campaign(config_path)
    _emit_progress(
        progress,
        (
            "V2 CAMPAIGN: artifact readiness passed "
            f"({len(resolved.config.models)} models, "
            f"{len(resolved.config.track_modes)} tracks)"
        ),
    )
    with _exclusive_output_dir_lock(resolved.output_dir):
        _guard_anthropic_headers(resolved)
        lifecycle = _CampaignLifecycleController.start(
            output_dir=resolved.output_dir,
            source_config_fingerprint=resolved.source_config_fingerprint,
            preflight_only=preflight_only,
            purpose=getattr(resolved.config, "purpose", "official"),
            schedules=tuple(
                _scheduled_task_roster(resolved, prepared[track])
                for track in resolved.config.track_modes
            ),
        )
        signals = _SignalCancellation.install()
        try:
            readiness = await _run_prepared_v2_campaign(
                resolved=resolved,
                snapshot=snapshot,
                prepared=prepared,
                mcp_revision=mcp_revision,
                model_readiness=model_readiness,
                preflight_only=preflight_only,
                progress=progress,
                lifecycle=lifecycle,
            )
        except NativeSessionCleanupPending as pending:
            lifecycle.fail("NativeSessionCleanupPending")
            try:
                _emit_progress(progress, "NATIVE_SESSION_CLEANUP_PENDING: waiting; no publication")
            finally:
                await pending.wait_for_cleanup()
            raise V2CampaignRunError("NATIVE_SESSION_CLEANUP_PENDING") from None
        except asyncio.CancelledError:
            lifecycle.interrupt(
                kind="signal" if signals.received_signal is not None else "task_cancelled",
                signal_name=signals.received_signal,
            )
            _emit_progress(
                progress,
                (
                    "V2 CAMPAIGN: interruption checkpointed"
                    + (
                        f" ({signals.received_signal})"
                        if signals.received_signal is not None
                        else ""
                    )
                ),
            )
            if signals.received_signal is not None:
                raise V2CampaignRunError(
                    f"v2 campaign interrupted by {signals.received_signal}"
                ) from None
            raise
        except KeyboardInterrupt:
            lifecycle.interrupt(kind="keyboard_interrupt", signal_name="SIGINT")
            _emit_progress(progress, "V2 CAMPAIGN: interruption checkpointed (SIGINT)")
            raise
        except BaseException as exc:
            lifecycle.fail(type(exc).__name__)
            raise
        else:
            lifecycle.complete()
            return readiness
        finally:
            signals.close()
