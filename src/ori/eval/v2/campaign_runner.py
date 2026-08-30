"""Fail-closed, model-backed campaign orchestration for protocol V2."""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from ori.eval.bhce import BHCEClient, parse_bhce_url, resolve_bhce_target
from ori.eval.direct_query_safety import (
    DirectQueryCoordinator,
    DirectQuerySafetyConfig,
    QueryDenyCache,
)
from ori.eval.mcp_runtime import _load_bloodhound_mcp_bundle
from ori.eval.provider_auth import (
    official_openai_endpoint_is_secure,
    openai_compat_endpoint_is_local,
    resolve_openai_compat_credential,
)
from ori.eval.provider_contract import ProviderApiSurface, resolve_api_surface

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
from .mcp import MCPToolLoop
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

RUNNER_VERSION = "ori-v2-model-campaign-v12"
RUN_STATE_SCHEMA_VERSION = "ori-v2-private-run-state-v5"
MODEL_REPORT_SCHEMA_VERSION = "ori-v2-model-report-v2"
READINESS_SCHEMA_VERSION = "ori-v2-run-readiness-v8"
CAMPAIGN_LIFECYCLE_SCHEMA_VERSION = "ori-v2-campaign-lifecycle-v1"
TRACK_COMPLETION_SCHEMA_VERSION = "ori-v2-track-completion-v1"
_RUNNER_IMPLEMENTATION_SOURCES = {
    "adapter": Path(__file__).parent.parent / "adapter.py",
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
    "mcp_state_machine": Path(__file__).with_name("mcp.py"),
    "model_runtime": Path(__file__).with_name("model_runtime.py"),
    "query_contract": Path(__file__).with_name("query_contract.py"),
    "provider_auth": Path(__file__).parent.parent / "provider_auth.py",
    "provider_contract": Path(__file__).parent.parent / "provider_contract.py",
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


class ModelRunProvenanceV2(StrictModel):
    schema_version: Literal["ori-v2-model-campaign-v12"] = RUNNER_VERSION
    protocol_version: Literal["ori-eval-protocol-v2"] = PROTOCOL_VERSION
    base: RunProvenanceV2
    run_identity: RunIdentity
    source_manifest_sha256: str
    archive_sha256: str
    candidate_release_fingerprint: str
    live_certification_fingerprint: str
    containment_config_fingerprint: str
    runtime_implementation_fingerprint: str
    runtime_config_fingerprint: str
    requested_api_surface: ProviderApiSurface
    resolved_api_surface: ProviderApiSurface
    structured_output_mode: Literal["prompt_local_validation"]
    endpoint_family: str
    credential_source: str | None = None
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
    sample: SampleResult
    provider: ProviderRunRecord
    attempt_fingerprint: str

    @model_validator(mode="after")
    def attempt_is_exact(self) -> ProviderAttemptV2:
        if self.task_id != self.sample.task_id or self.task_id != self.provider.task_id:
            raise ValueError("provider attempt task IDs do not match")
        expected = canonical_sha256(
            self,
            exclude_fields=("attempt_fingerprint",),
        )
        if self.attempt_fingerprint != expected:
            raise ValueError("provider attempt fingerprint mismatch")
        return self


class PrivateRunStateV2(StrictModel):
    schema_version: Literal["ori-v2-private-run-state-v5"] = RUN_STATE_SCHEMA_VERSION
    protocol_version: Literal["ori-eval-protocol-v2"] = PROTOCOL_VERSION
    provenance_fingerprint: str
    checkpoint: CheckpointV2
    attempts: tuple[ProviderAttemptV2, ...]
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


class ModelPublicReportV2(StrictModel):
    schema_version: Literal["ori-v2-model-report-v2"] = MODEL_REPORT_SCHEMA_VERSION
    protocol_version: Literal["ori-eval-protocol-v2"] = PROTOCOL_VERSION
    run_identity: RunIdentity
    candidate_release_fingerprint: str
    live_certification_fingerprint: str
    graph_verification_before_fingerprint: str
    graph_verification_after_fingerprint: str
    report: PublicReportV2
    artifact_fingerprint: str

    @model_validator(mode="after")
    def fingerprint_matches(self) -> ModelPublicReportV2:
        expected = canonical_sha256(
            self,
            exclude_fields=("artifact_fingerprint",),
        )
        if self.artifact_fingerprint != expected:
            raise ValueError("model public report fingerprint mismatch")
        return self


class ReadinessTrackV2(StrictModel):
    track: Track
    public_artifact_fingerprint: str
    oracle_artifact_fingerprint: str
    candidate_release_fingerprint: str
    live_certification_fingerprint: str
    capability_profile_fingerprint: str
    graph_verification_fingerprint: str
    task_count: int = Field(strict=True, gt=0)


class ModelReadinessV2(StrictModel):
    name: str
    provider: str
    model: str
    credential_check: str
    capability_check: str
    requested_api_surface: ProviderApiSurface
    resolved_api_surface: ProviderApiSurface
    structured_output_mode: Literal["prompt_local_validation"]
    endpoint_family: str
    credential_source: str | None = None
    reasoning_effort: ReasoningEffort | None = None


class CampaignReadinessV2(StrictModel):
    schema_version: Literal["ori-v2-run-readiness-v8"] = READINESS_SCHEMA_VERSION
    protocol_version: Literal["ori-eval-protocol-v2"] = PROTOCOL_VERSION
    runner_version: Literal["ori-v2-model-campaign-v12"] = RUNNER_VERSION
    source_config_fingerprint: str
    source_manifest_sha256: str
    archive_sha256: str
    graph_fingerprint: str
    target_fingerprint: str
    mcp_server_revision: str
    tracks: tuple[ReadinessTrackV2, ...]
    models: tuple[ModelReadinessV2, ...]
    model_count: int = Field(strict=True, gt=0)
    readiness_fingerprint: str

    @model_validator(mode="after")
    def fingerprint_matches(self) -> CampaignReadinessV2:
        if self.model_count != len(self.models):
            raise ValueError("readiness model count does not match model receipts")
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
    schema_version: Literal["ori-v2-campaign-lifecycle-v1"] = CAMPAIGN_LIFECYCLE_SCHEMA_VERSION
    protocol_version: Literal["ori-eval-protocol-v2"] = PROTOCOL_VERSION
    runner_version: Literal["ori-v2-model-campaign-v12"] = RUNNER_VERSION
    source_config_fingerprint: str
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
    schema_version: Literal["ori-v2-track-completion-v1"] = TRACK_COMPLETION_SCHEMA_VERSION
    protocol_version: Literal["ori-eval-protocol-v2"] = PROTOCOL_VERSION
    runner_version: Literal["ori-v2-model-campaign-v12"] = RUNNER_VERSION
    source_config_fingerprint: str
    track: Track
    candidate_release_fingerprint: str
    live_certification_fingerprint: str
    graph_verification_before_fingerprint: str
    graph_verification_after_fingerprint: str
    expected_task_count_per_run: int = Field(strict=True, gt=0)
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

    @property
    def task_ids(self) -> tuple[str, ...]:
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
    for state_path in output_dir.glob("*/*/run-*/run-state-v5.private.json"):
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
    ) -> _CampaignLifecycleController:
        path = output_dir / "campaign-lifecycle-v2.private.json"
        previous: CampaignLifecycleV2 | None = None
        if path.exists():
            previous = CampaignLifecycleV2.model_validate_json(path.read_text())
            if previous.source_config_fingerprint != source_config_fingerprint:
                raise V2CampaignRunError("campaign lifecycle belongs to a different source config")
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
    paths = resolved.tracks[track]
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
    structured_output_mode: Literal["prompt_local_validation"]
    endpoint_family: str
    credential_source: str | None


def _model_base_url(
    model: V2ModelEntry,
    resolved: ResolvedV2CampaignConfig,
) -> str | None:
    base_url = model.model_base_url or resolved.config.defaults.model_base_url
    if not base_url and "@" in model.model:
        base_url = model.model.rsplit("@", 1)[1]
    if not base_url and model.provider == "openai-compat":
        base_url = os.getenv("OPENAI_COMPAT_BASE_URL")
    if not base_url and model.provider == "openai":
        base_url = "https://api.openai.com/v1"
    return base_url


def _provider_identity(
    model: V2ModelEntry,
    resolved: ResolvedV2CampaignConfig,
) -> _ProviderIdentityV2:
    requested = ProviderApiSurface(model.api_surface)
    selected = resolve_api_surface(model.provider, requested)
    if model.provider == "codex" and selected is not ProviderApiSurface.RESPONSES:
        raise V2CampaignRunError(
            f"model {model.name} cannot use api_surface={selected.value!r}; "
            "Release 1 Codex requires Responses"
        )
    if model.provider != "codex" and selected is not ProviderApiSurface.CHAT_COMPLETIONS:
        raise V2CampaignRunError(
            f"model {model.name} cannot use api_surface={selected.value!r}; "
            "Release 1 enables Responses only for Codex"
        )

    credential_source: str | None = None
    endpoint_family = model.provider
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
        endpoint_family = "codex_oauth"
        credential_source = "codex-login-status"
    elif model.provider == "anthropic":
        credential_source = "ANTHROPIC_API_KEY" if os.getenv("ANTHROPIC_API_KEY") else None
    elif model.provider == "gemini":
        credential_source = "GEMINI_API_KEY" if os.getenv("GEMINI_API_KEY") else None

    return _ProviderIdentityV2(
        requested_api_surface=requested,
        resolved_api_surface=selected,
        structured_output_mode="prompt_local_validation",
        endpoint_family=endpoint_family,
        credential_source=credential_source,
    )


def _codex_model_slug(model: V2ModelEntry) -> str:
    slug = model.model.split("/", 1)[1] if model.model.startswith("codex/") else model.model
    return slug.split("@", 1)[0]


def _model_readiness(
    resolved: ResolvedV2CampaignConfig,
) -> tuple[ModelReadinessV2, ...]:
    """Verify local credentials/configuration without invoking a model."""

    receipts: list[ModelReadinessV2] = []
    codex_status_checked = False
    codex_capabilities: dict[str, set[str]] | None = None
    for model in resolved.config.models:
        identity = _provider_identity(model, resolved)
        if model.provider == "codex":
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
            slug = _codex_model_slug(model)
            if codex_capabilities is None or slug not in codex_capabilities:
                raise V2CampaignRunError(
                    f"Codex model {slug!r} is absent from ~/.codex/models_cache.json"
                )
            reasoning_effort = resolved.config.defaults.reasoning_effort
            if (
                reasoning_effort is not None
                and reasoning_effort not in codex_capabilities[slug]
            ):
                raise V2CampaignRunError(
                    f"Codex model {slug!r} does not advertise reasoning effort "
                    f"{reasoning_effort!r} in ~/.codex/models_cache.json"
                )
            receipts.append(
                ModelReadinessV2(
                    name=model.name,
                    provider=model.provider,
                    model=slug,
                    credential_check="codex-login-status",
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

        required_key = {
            "anthropic": "ANTHROPIC_API_KEY",
            "gemini": "GEMINI_API_KEY",
            "openai": "OPENAI_API_KEY",
        }.get(model.provider)
        if model.provider == "openai-compat":
            base_url = _model_base_url(model, resolved)
            if not base_url:
                raise V2CampaignRunError(
                    f"model {model.name} requires an OpenAI-compatible base URL"
                )
            if identity.credential_source is None and not openai_compat_endpoint_is_local(
                base_url
            ):
                raise V2CampaignRunError(
                    f"model {model.name} has no credential for "
                    f"{identity.endpoint_family} endpoint"
                )
        if required_key is not None and identity.credential_source is None:
            raise V2CampaignRunError(f"model {model.name} requires {required_key}")
        receipts.append(
            ModelReadinessV2(
                name=model.name,
                provider=model.provider,
                model=model.model,
                credential_check=(
                    identity.credential_source or "provider-does-not-require-a-key"
                ),
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
    raw = model.mcp_tool_loop or resolved.config.defaults.mcp.tool_loop
    loop = MCPToolLoop(raw)
    if loop is MCPToolLoop.AUTO:
        raise V2CampaignRunError("certified v2 campaigns forbid MCP tool_loop=auto")
    if loop is MCPToolLoop.INSPECT:
        raise V2CampaignRunError("Inspect-backed v2 model campaigns are not enabled by run-v2")
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
    prepared = {
        track: _prepare_track(resolved, track, snapshot) for track in resolved.config.track_modes
    }
    _validate_model_bindings(resolved, prepared)
    _validate_runtime_bounds(resolved, prepared)
    model_readiness = _model_readiness(resolved)
    mcp_revision = _git_revision(resolved.mcp_dir)
    if Track.MCP in prepared:
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
    model_readiness: tuple[ModelReadinessV2, ...],
) -> CampaignReadinessV2:
    payload = {
        "source_config_fingerprint": resolved.source_config_fingerprint,
        "source_manifest_sha256": _sha256(resolved.source_manifest),
        "archive_sha256": _sha256(resolved.archive),
        "graph_fingerprint": snapshot.graph_fingerprint,
        "target_fingerprint": canonical_sha256(
            resolve_bhce_target(resolved.config.defaults.bhce_url)
        ),
        "mcp_server_revision": mcp_revision,
        "tracks": tuple(
            ReadinessTrackV2(
                track=track,
                public_artifact_fingerprint=item.pair.public.artifact_fingerprint,
                oracle_artifact_fingerprint=item.pair.private.artifact_fingerprint,
                candidate_release_fingerprint=item.release.release_fingerprint,
                live_certification_fingerprint=item.live.artifact_fingerprint,
                capability_profile_fingerprint=item.profile.profile_fingerprint,
                graph_verification_fingerprint=receipts[track].verification_fingerprint,
                task_count=len(item.release.entries),
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


def _provenance(
    *,
    resolved: ResolvedV2CampaignConfig,
    prepared: PreparedTrack,
    model: V2ModelEntry,
    run_index: int,
    loop: MCPToolLoop | None,
) -> ModelRunProvenanceV2:
    provider_identity = _provider_identity(model, resolved)
    run_identity = RunIdentity(
        provider=model.provider,
        model=model.model,
        run_index=run_index,
        target_fingerprint=canonical_sha256(resolve_bhce_target(resolved.config.defaults.bhce_url)),
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
        "containment_config_fingerprint": canonical_sha256(direct_config.to_jsonable()),
        "runtime_implementation_fingerprint": (RUNNER_IMPLEMENTATION_FINGERPRINT),
        "requested_api_surface": provider_identity.requested_api_surface,
        "resolved_api_surface": provider_identity.resolved_api_surface,
        "structured_output_mode": provider_identity.structured_output_mode,
        "endpoint_family": provider_identity.endpoint_family,
        "credential_source": provider_identity.credential_source,
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
) -> ProviderAttemptV2:
    payload = {
        "task_id": task_id,
        "attempt": number,
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


def _state(
    *,
    provenance: ModelRunProvenanceV2,
    checkpoint: CheckpointV2,
    attempts: Sequence[ProviderAttemptV2],
) -> PrivateRunStateV2:
    payload = {
        "provenance_fingerprint": provenance.provenance_fingerprint,
        "checkpoint": checkpoint,
        "attempts": tuple(attempts),
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
    return state


def _model_report(
    *,
    provenance: ModelRunProvenanceV2,
    prepared: PreparedTrack,
    results: Sequence[SampleResult],
    before: LiveGraphVerification,
    after: LiveGraphVerification,
) -> ModelPublicReportV2:
    summary = summarize_results(prepared.task_ids, results)
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
        "graph_verification_before_fingerprint": before.verification_fingerprint,
        "graph_verification_after_fingerprint": after.verification_fingerprint,
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
        return scope, retryable
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
    progress: ProgressReporter | None = None,
    lifecycle: _CampaignLifecycleController | None = None,
) -> tuple[ModelRunProvenanceV2, tuple[SampleResult, ...]]:
    provenance = _provenance(
        resolved=resolved,
        prepared=prepared,
        model=model,
        run_index=run_index,
        loop=loop,
    )
    run_dir = resolved.output_dir / prepared.track.value / model.name / f"run-{run_index:03d}"
    _guard_run_dir(run_dir, provenance)
    state_path = run_dir / "run-state-v5.private.json"
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
    retryable_execution_classes = {
        ExecutionClass.INFRA_FAILURE,
        ExecutionClass.UNEXECUTED,
    }
    completed = {
        result.task_id
        for result in results
        if result.execution_class not in retryable_execution_classes
    }
    latest_attempts: dict[str, ProviderAttemptV2] = {}
    for attempt in attempts:
        latest_attempts[attempt.task_id] = attempt
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
    if prepared.track is Track.MCP:
        bundle = await _load_bloodhound_mcp_bundle(
            resolved.mcp_dir,
            include_resources=False,
            include_prompt=True,
            cypher_executor=coordinator.execute,
        )

    task_by_id = {task.task_id: task for task in prepared.pair.public.tasks}
    registry = OracleRegistry(prepared.pair.private)
    resolver = IdentityResolver(prepared.pair.private.identity_catalog)
    provider_options = _provider_options(
        model,
        resolved.config.defaults.reasoning_effort,
    )
    for task_index, task_id in enumerate(prepared.task_ids, start=1):
        if task_id in completed:
            continue
        task = task_by_id[task_id]
        oracle = registry.for_task(task_id)
        existing_result = next(
            (result for result in results if result.task_id == task_id),
            None,
        )
        prior_task_attempts = tuple(
            attempt for attempt in attempts if attempt.task_id == task_id
        )
        previous_attempt_number = max(
            (attempt.attempt for attempt in prior_task_attempts),
            default=0,
        )
        consumed_attempts = sum(
            _attempt_consumes_retry_budget(attempt)
            for attempt in prior_task_attempts
        )
        max_attempts = resolved.config.defaults.max_infra_retries + 1
        if consumed_attempts >= max_attempts:
            if existing_result is None:
                raise V2CampaignRunError(
                    f"{task_id} exhausted its lifetime retry budget without a result"
                )
            _emit_progress(
                progress,
                (
                    f"  [{task_index}/{len(prepared.task_ids)}] {task.task_id} "
                    "retry budget already exhausted; no provider call made"
                ),
            )
            continue
        results = [result for result in results if result.task_id != task_id]
        task_started = time.monotonic()
        _emit_progress(
            progress,
            (
                f"  [{task_index}/{len(prepared.task_ids)}] {task.task_id} "
                f"({task.claim_kind}, {task.answer_policy.kind})"
            ),
        )
        last_sample: SampleResult | None = None
        last_provider: ProviderRunRecord | None = None
        remaining_attempts = max_attempts - consumed_attempts
        for attempt_index in range(1, remaining_attempts + 1):
            attempt_number = previous_attempt_number + attempt_index
            sample: SampleResult
            provider: ProviderRunRecord
            direct_preflight_blocked = False
            cancellation: V2ModelTaskCancelled | None = None
            model_base_url = _model_base_url(model, resolved)
            try:
                if prepared.track is Track.DIRECT:
                    if coordinator.circuit_open:
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
                                detail=(
                                    "BloodHound circuit remained open before provider execution"
                                ),
                            )
                    if not direct_preflight_blocked:
                        _outcome, sample, provider = await run_direct_model_task_v2(
                            coordinator=coordinator,
                            task=task,
                            oracle=oracle,
                            resolver=resolver,
                            model=model.requested_model,
                            model_base_url=model_base_url,
                            api_surface=getattr(model, "api_surface", "auto"),
                            ollama_options=provider_options,
                        )
                else:
                    if bundle is None or loop is None:
                        raise AssertionError("MCP model run is missing its runtime bundle")
                    outcome, provider = await run_mcp_model_task_v2(
                        task=task,
                        oracle=oracle,
                        resolver=resolver,
                        profile=prepared.profile,
                        bundle=bundle,
                        model=model.requested_model,
                        model_base_url=model_base_url,
                        api_surface=getattr(model, "api_surface", "auto"),
                        tool_loop=loop,
                        max_steps=resolved.config.defaults.mcp.max_steps,
                        ollama_options=provider_options,
                        telemetry_adapter=(resolved.config.defaults.mcp.telemetry_adapter),
                        read_timeout_seconds=(resolved.config.defaults.mcp.read_timeout_seconds),
                        tool_timeout_seconds=(resolved.config.defaults.mcp.tool_timeout_seconds),
                        graph_fact_registry=(
                            prepared.pair.private.graph_fact_registry
                        ),
                    )
                    sample = outcome.sample
            except V2ModelTaskCancelled as exc:
                sample = exc.sample
                provider = exc.provider
                cancellation = exc
            except Exception as exc:
                sample, provider = contain_model_runtime_exception(
                    task=task,
                    oracle=oracle,
                    model=model.requested_model,
                    surface=prepared.track.value,
                    error=exc,
                )
            attempts.append(_attempt(task_id, attempt_number, sample, provider))
            last_sample = sample
            last_provider = provider
            results = [result for result in results if result.task_id != task_id]
            results.append(sample)
            checkpoint = build_checkpoint(
                prepared.pair,
                prepared.profile,
                provenance.run_identity,
                results=results,
            )
            current_state = _state(
                provenance=provenance,
                checkpoint=checkpoint,
                attempts=attempts,
            )
            _write_model(state_path, current_state)
            if lifecycle is not None:
                lifecycle.record_checkpoint(
                    track=prepared.track,
                    model_name=model.name,
                    run_index=run_index,
                    result_count=len(results),
                )
            if cancellation is not None:
                raise cancellation

            retry_policy = _infrastructure_retry_policy(sample, provider)
            if retry_policy is None:
                break
            scope, retryable = retry_policy
            if not retryable or attempt_index >= remaining_attempts:
                break
            if scope == "bloodhound":
                health = await bhce.wait_until_healthy(
                    timeout_seconds=resolved.config.defaults.health.timeout_seconds,
                    poll_interval=resolved.config.defaults.health.poll_interval,
                )
                if not health.ok:
                    break
                coordinator.close_circuit()
            elif scope not in {"provider", "mcp_tool"}:
                break
            _emit_progress(
                progress,
                _infrastructure_attempt_progress(
                    sample=sample,
                    attempt_number=attempt_number,
                    attempt_index=attempt_index,
                    max_infra_retries=(resolved.config.defaults.max_infra_retries),
                ),
            )

        if last_sample is None or last_provider is None:
            raise AssertionError("v2 task loop produced no terminal attempt")
        _emit_progress(
            progress,
            _task_completion_progress(
                sample=last_sample,
                provider=last_provider,
                task_elapsed_seconds=time.monotonic() - task_started,
                results=results,
            ),
        )

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
    receipts_before: dict[Track, LiveGraphVerification] = {}
    shared_preflight: tuple[GraphSnapshot, LiveGraphVerification] | None = None
    invalid_campaigns: list[str] = []

    async with BHCEClient(**parse_bhce_url(resolved.config.defaults.bhce_url)) as bhce:
        direct_config = DirectQuerySafetyConfig()
        coordinator = DirectQueryCoordinator(
            bhce=bhce,
            config=direct_config,
            deny_cache=QueryDenyCache(
                (resolved.output_dir / "direct-query-deny-cache-v3.private.json"),
                manifest_fingerprint=_sha256(resolved.source_manifest),
                policy_version=direct_config.policy_version,
            ),
        )
        for track in resolved.config.track_modes:
            lifecycle.activate_track(track)
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
            completed_runs: list[tuple[ModelRunProvenanceV2, tuple[SampleResult, ...], Path]] = []
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
                        progress=progress,
                        lifecycle=lifecycle,
                    )
                    run_dir = (
                        resolved.output_dir / track.value / model.name / f"run-{run_index:03d}"
                    )
                    completed_runs.append((provenance, results, run_dir))
            _emit_progress(
                progress,
                f"[{track.value}] verifying BloodHound graph after track",
            )
            _observed, after = await _health_and_graph(
                resolved,
                snapshot,
                bhce,
            )
            _write_model(
                track_dir / "graph-verification-after-v2.private.json",
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
                (
                    f"[{track.value}] reports published and track completion "
                    f"recorded ({completion.receipt_fingerprint[:12]})"
                ),
            )

    readiness = _readiness(
        resolved=resolved,
        snapshot=snapshot,
        prepared=prepared,
        receipts=receipts_before,
        mcp_revision=mcp_revision,
        model_readiness=model_readiness,
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
        lifecycle = _CampaignLifecycleController.start(
            output_dir=resolved.output_dir,
            source_config_fingerprint=resolved.source_config_fingerprint,
            preflight_only=preflight_only,
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
