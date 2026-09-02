"""Read-only, fail-closed status projection for protocol V2 campaigns.

This module deliberately does not prepare a campaign.  It loads the exact
operator-supplied configuration, validates already-persisted receipts, and
projects only model-blind operational state.  It must never contact a provider,
BloodHound, MCP, or the live graph and must never create campaign files.
"""

from __future__ import annotations

import fcntl
import os
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import Field

from .campaign_config import ResolvedV2CampaignConfig, load_v2_campaign_config
from .campaign_runner import (
    CampaignLifecycleV2,
    CampaignReadinessV2,
    ModelPublicReportV2,
    ModelRunProvenanceV2,
    PrivateRunStateV2,
    TrackCompletionV2,
)
from .schema import PROTOCOL_VERSION, StrictModel, Track

CAMPAIGN_STATUS_SCHEMA_VERSION = "ori-v2-campaign-status-v1"
_LIFECYCLE_NAME = "campaign-lifecycle-v2.private.json"
_READINESS_NAME = "v2-run-readiness.private.json"
_LOCK_NAME = ".ori-v2-campaign.lock"
_PROVENANCE_NAME = "campaign-provenance-v2.json"
_STATE_NAME = "run-state-v6.private.json"
_REPORT_NAME = "public-report-v2.json"
_TRACK_RECEIPT_NAME = "track-completion-v2.private.json"


class CampaignStatusError(ValueError):
    """Raised when persisted status evidence is corrupt or incompatible."""


class ActiveRunIdentityV1(StrictModel):
    track: Track
    model: str
    run_index: int = Field(strict=True, ge=1)


class OutcomeCountV1(StrictModel):
    outcome: str
    count: int = Field(strict=True, ge=0)


class CampaignRunStatusV1(StrictModel):
    track: Track
    model: str
    run_index: int = Field(strict=True, ge=1)
    expected_tasks: int | None = Field(default=None, strict=True, gt=0)
    started: bool
    checkpointed_results: int = Field(strict=True, ge=0)
    provider_attempts: int = Field(default=0, strict=True, ge=0)
    tokens_input: int = Field(default=0, strict=True, ge=0)
    tokens_output: int = Field(default=0, strict=True, ge=0)
    total_tokens: int = Field(default=0, strict=True, ge=0)
    report_present: bool
    campaign_valid: bool | None = Field(default=None, strict=True)
    outcomes: tuple[OutcomeCountV1, ...] = ()


class CampaignTrackStatusV1(StrictModel):
    track: Track
    expected_runs: int = Field(strict=True, gt=0)
    expected_tasks_per_run: int | None = Field(default=None, strict=True, gt=0)
    checkpointed_results: int = Field(strict=True, ge=0)
    completed_results: int = Field(strict=True, ge=0)
    provider_attempts: int = Field(default=0, strict=True, ge=0)
    tokens_input: int = Field(default=0, strict=True, ge=0)
    tokens_output: int = Field(default=0, strict=True, ge=0)
    total_tokens: int = Field(default=0, strict=True, ge=0)
    completion_present: bool
    campaign_valid: bool | None = Field(default=None, strict=True)
    candidate_release_fingerprint: str | None = None
    live_certification_fingerprint: str | None = None
    graph_verification_before_fingerprint: str | None = None
    graph_verification_after_fingerprint: str | None = None


class CampaignProgressV1(StrictModel):
    expected_runs: int = Field(strict=True, gt=0)
    runs_started: int = Field(strict=True, ge=0)
    runs_reported: int = Field(strict=True, ge=0)
    expected_results: int | None = Field(default=None, strict=True, gt=0)
    checkpointed_results: int = Field(strict=True, ge=0)
    completed_results: int = Field(strict=True, ge=0)
    provider_attempts: int = Field(default=0, strict=True, ge=0)
    tokens_input: int = Field(default=0, strict=True, ge=0)
    tokens_output: int = Field(default=0, strict=True, ge=0)
    total_tokens: int = Field(default=0, strict=True, ge=0)


class CampaignStatusV1(StrictModel):
    schema_version: Literal["ori-v2-campaign-status-v1"] = CAMPAIGN_STATUS_SCHEMA_VERSION
    protocol_version: Literal["ori-eval-protocol-v2"] = PROTOCOL_VERSION
    source_config_fingerprint: str
    lifecycle_state: Literal[
        "not_started",
        "running",
        "interrupted",
        "failed",
        "completed",
    ]
    observed_state: Literal[
        "not_started",
        "running",
        "stale_running",
        "interrupted",
        "failed",
        "readiness_complete",
        "completed",
    ]
    mode: Literal["readiness", "execution"] | None = None
    started_at_utc: str | None = None
    updated_at_utc: str | None = None
    resume_count: int = Field(default=0, strict=True, ge=0)
    active_run: ActiveRunIdentityV1 | None = None
    completed_tracks: tuple[Track, ...] = ()
    progress: CampaignProgressV1
    tracks: tuple[CampaignTrackStatusV1, ...]
    runs: tuple[CampaignRunStatusV1, ...]
    graph_fingerprint: str | None = None
    resume_allowed: bool
    next_action: Literal[
        "run_readiness",
        "monitor",
        "resume_campaign",
        "investigate_failure",
        "execute_campaign",
        "campaign_complete",
        "campaign_complete_invalid",
    ]


def _load_model(path: Path, model: type[StrictModel], label: str) -> StrictModel:
    try:
        return model.model_validate_json(path.read_text())
    except (OSError, ValueError) as exc:
        raise CampaignStatusError(f"invalid {label}: {exc}") from exc


def _lock_is_contended(output_dir: Path) -> bool:
    """Probe an existing campaign lock without creating or modifying it."""

    lock_path = output_dir / _LOCK_NAME
    if not lock_path.exists():
        raise CampaignStatusError(
            "campaign lifecycle exists without its persistent campaign lock"
        )
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_path, flags)
    except OSError as exc:
        raise CampaignStatusError(f"cannot safely inspect campaign lock: {exc}") from exc
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        else:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            return False
    finally:
        os.close(descriptor)


def _expected_runs(
    resolved: ResolvedV2CampaignConfig,
) -> tuple[tuple[Track, str, str, str, int], ...]:
    runs: list[tuple[Track, str, str, str, int]] = []
    for track in resolved.config.track_modes:
        for model in resolved.config.models:
            count = model.runs_per_model or resolved.config.defaults.runs_per_model
            for run_index in range(1, count + 1):
                runs.append((track, model.name, model.provider, model.model, run_index))
    return tuple(runs)


def _outcome_counts(values: list[str]) -> tuple[OutcomeCountV1, ...]:
    counts = Counter(values)
    return tuple(
        OutcomeCountV1(outcome=outcome, count=count)
        for outcome, count in sorted(counts.items())
    )


def _assert_no_unexpected_run_directories(
    output_dir: Path,
    expected: set[tuple[Track, str, int]],
) -> None:
    run_artifacts: set[Path] = set()
    for artifact_name in (_PROVENANCE_NAME, _STATE_NAME, _REPORT_NAME):
        run_artifacts.update(output_dir.glob(f"*/*/run-*/{artifact_name}"))
    for artifact_path in sorted(run_artifacts):
        relative = artifact_path.relative_to(output_dir)
        try:
            track = Track(relative.parts[0])
            model = relative.parts[1]
            run_component = relative.parts[2]
            if not run_component.startswith("run-"):
                raise ValueError
            run_index = int(run_component.removeprefix("run-"))
        except (IndexError, ValueError) as exc:
            raise CampaignStatusError(
                f"unexpected v2 run-state location: {relative}"
            ) from exc
        if (track, model, run_index) not in expected:
            raise CampaignStatusError(f"run state is not selected by config: {relative}")


def _read_run(
    *,
    run_dir: Path,
    track: Track,
    model_name: str,
    provider: str,
    provider_model: str,
    run_index: int,
    expected_tasks: int | None,
    track_receipt: TrackCompletionV2 | None,
    allow_uncommitted_report: bool,
) -> tuple[CampaignRunStatusV1, ModelRunProvenanceV2 | None]:
    provenance_path = run_dir / _PROVENANCE_NAME
    state_path = run_dir / _STATE_NAME
    report_path = run_dir / _REPORT_NAME
    if not provenance_path.exists():
        if state_path.exists() or report_path.exists():
            raise CampaignStatusError(
                f"{track.value}/{model_name}/run-{run_index:03d} has evidence without provenance"
            )
        return (
            CampaignRunStatusV1(
                track=track,
                model=model_name,
                run_index=run_index,
                expected_tasks=expected_tasks,
                started=False,
                checkpointed_results=0,
                report_present=False,
            ),
            None,
        )

    provenance = _load_model(
        provenance_path, ModelRunProvenanceV2, "model-run provenance"
    )
    assert isinstance(provenance, ModelRunProvenanceV2)
    identity = provenance.run_identity
    if (
        provenance.base.track is not track
        or identity.provider != provider
        or identity.model != provider_model
        or identity.run_index != run_index
    ):
        raise CampaignStatusError(
            f"{track.value}/{model_name}/run-{run_index:03d} provenance does not match config"
        )

    state: PrivateRunStateV2 | None = None
    if state_path.exists():
        loaded = _load_model(state_path, PrivateRunStateV2, "private run state")
        assert isinstance(loaded, PrivateRunStateV2)
        state = loaded
        if state.provenance_fingerprint != provenance.provenance_fingerprint:
            raise CampaignStatusError("private run state belongs to different provenance")
        if state.checkpoint.run_identity != identity or state.checkpoint.track is not track:
            raise CampaignStatusError("private run state identity does not match provenance")
        base = provenance.base
        checkpoint = state.checkpoint
        if (
            checkpoint.public_artifact_fingerprint
            != base.public_artifact_fingerprint
            or checkpoint.oracle_artifact_fingerprint
            != base.oracle_artifact_fingerprint
            or checkpoint.catalog_fingerprint != base.catalog_fingerprint
            or checkpoint.graph_fingerprint != base.graph_fingerprint
            or checkpoint.compiler_fingerprint != base.compiler_fingerprint
            or checkpoint.comparator_fingerprint != base.comparator_fingerprint
            or checkpoint.capability_profile_fingerprint
            != base.capability_profile_fingerprint
        ):
            raise CampaignStatusError("private run state disagrees with run provenance")
        # Checkpoints bind the complete compiled artifact so resume validation can
        # reject stale task/oracle/bounds data.  A candidate release may schedule
        # only a semantic-unique subset of those bindings (for example V29's
        # 42/55 release from the larger 46/70 certification inventory).  Compare
        # readiness with durable scheduled results, not the full binding inventory.
        if expected_tasks is not None and len(state.checkpoint.results) > expected_tasks:
            raise CampaignStatusError(
                "private run state result count exceeds readiness schedule"
            )

    report: ModelPublicReportV2 | None = None
    if report_path.exists():
        loaded = _load_model(report_path, ModelPublicReportV2, "model public report")
        assert isinstance(loaded, ModelPublicReportV2)
        report = loaded
        if report.run_identity != identity or report.report.track is not track:
            raise CampaignStatusError("model public report identity does not match provenance")
        if (
            report.candidate_release_fingerprint
            != provenance.candidate_release_fingerprint
            or report.live_certification_fingerprint
            != provenance.live_certification_fingerprint
        ):
            raise CampaignStatusError("model public report release evidence is incompatible")
        if (
            report.report.public_artifact_fingerprint
            != provenance.base.public_artifact_fingerprint
            or report.report.catalog_fingerprint != provenance.base.catalog_fingerprint
            or report.report.graph_fingerprint != provenance.base.graph_fingerprint
            or report.report.capability_profile_fingerprint
            != provenance.base.capability_profile_fingerprint
        ):
            raise CampaignStatusError("model public report disagrees with run provenance")
        if state is None:
            raise CampaignStatusError("completed model public report has no private run state")
        if len(state.checkpoint.results) != len(report.report.rows):
            raise CampaignStatusError("public report and checkpoint result counts differ")

    receipt_run = None
    if track_receipt is not None:
        receipt_run = next(
            (
                item
                for item in track_receipt.runs
                if item.provider == provider
                and item.model == provider_model
                and item.run_index == run_index
            ),
            None,
        )
        if receipt_run is None:
            raise CampaignStatusError("track completion is missing a configured run")
        if report is None:
            raise CampaignStatusError("track completion run is missing its public report")
        if state is None:
            raise CampaignStatusError("track completion run is missing its private state")
        if (
            receipt_run.public_report_fingerprint != report.artifact_fingerprint
            or receipt_run.result_count != len(state.checkpoint.results)
            or receipt_run.campaign_valid != report.report.summary.campaign_valid
            or receipt_run.invalid_reasons != report.report.summary.invalid_reasons
        ):
            raise CampaignStatusError("track completion run does not match its artifacts")
        if (
            report.graph_verification_before_fingerprint
            != track_receipt.graph_verification_before_fingerprint
            or report.graph_verification_after_fingerprint
            != track_receipt.graph_verification_after_fingerprint
        ):
            raise CampaignStatusError("run and track graph receipts differ")
    elif report is not None and not allow_uncommitted_report:
        raise CampaignStatusError("public report exists without track completion receipt")

    if report is not None:
        summary = report.report.summary
        outcomes = _outcome_counts([row.outcome.value for row in report.report.rows])
        campaign_valid = summary.campaign_valid
    elif state is not None:
        outcomes = _outcome_counts(
            [result.outcome.value for result in state.checkpoint.results]
        )
        campaign_valid = None
    else:
        outcomes = ()
        campaign_valid = None
    provider_attempts = len(state.attempts) if state is not None else 0
    tokens_input = (
        sum(attempt.provider.tokens_input for attempt in state.attempts)
        if state is not None
        else 0
    )
    tokens_output = (
        sum(attempt.provider.tokens_output for attempt in state.attempts)
        if state is not None
        else 0
    )
    return (
        CampaignRunStatusV1(
            track=track,
            model=model_name,
            run_index=run_index,
            expected_tasks=expected_tasks,
            started=True,
            checkpointed_results=(
                len(state.checkpoint.results) if state is not None else 0
            ),
            provider_attempts=provider_attempts,
            tokens_input=tokens_input,
            tokens_output=tokens_output,
            total_tokens=tokens_input + tokens_output,
            report_present=report is not None,
            campaign_valid=campaign_valid,
            outcomes=outcomes,
        ),
        provenance,
    )


def inspect_v2_campaign_status(config_path: Path) -> CampaignStatusV1:
    """Return a safe, read-only status projection for one exact V2 config."""

    resolved = load_v2_campaign_config(config_path)
    output_dir = resolved.output_dir
    expected_runs = _expected_runs(resolved)
    expected_keys = {(track, name, index) for track, name, _, _, index in expected_runs}

    lifecycle_path = output_dir / _LIFECYCLE_NAME
    readiness_path = output_dir / _READINESS_NAME
    if not lifecycle_path.exists():
        if output_dir.exists() and not output_dir.is_dir():
            raise CampaignStatusError("campaign output path is not a directory")
        evidence = () if not output_dir.exists() else tuple(output_dir.iterdir())
        if evidence:
            raise CampaignStatusError("campaign evidence exists without a lifecycle receipt")
        runs = tuple(
            CampaignRunStatusV1(
                track=track,
                model=name,
                run_index=index,
                started=False,
                checkpointed_results=0,
                report_present=False,
            )
            for track, name, _provider, _provider_model, index in expected_runs
        )
        tracks = tuple(
            CampaignTrackStatusV1(
                track=track,
                expected_runs=sum(item[0] is track for item in expected_runs),
                checkpointed_results=0,
                completed_results=0,
                completion_present=False,
            )
            for track in resolved.config.track_modes
        )
        return CampaignStatusV1(
            source_config_fingerprint=resolved.source_config_fingerprint,
            lifecycle_state="not_started",
            observed_state="not_started",
            progress=CampaignProgressV1(
                expected_runs=len(expected_runs),
                runs_started=0,
                runs_reported=0,
                checkpointed_results=0,
                completed_results=0,
            ),
            tracks=tracks,
            runs=runs,
            resume_allowed=False,
            next_action="run_readiness",
        )

    lifecycle = _load_model(lifecycle_path, CampaignLifecycleV2, "campaign lifecycle")
    assert isinstance(lifecycle, CampaignLifecycleV2)
    if lifecycle.source_config_fingerprint != resolved.source_config_fingerprint:
        raise CampaignStatusError("campaign lifecycle belongs to a different source config")

    lock_contended = _lock_is_contended(output_dir)
    if lifecycle.status == "running":
        observed_state = "running" if lock_contended else "stale_running"
    elif lock_contended:
        raise CampaignStatusError("campaign lock is held but lifecycle is not running")
    elif lifecycle.status == "completed" and lifecycle.mode == "readiness":
        observed_state = "readiness_complete"
    else:
        observed_state = lifecycle.status

    readiness: CampaignReadinessV2 | None = None
    if readiness_path.exists():
        loaded = _load_model(readiness_path, CampaignReadinessV2, "campaign readiness")
        assert isinstance(loaded, CampaignReadinessV2)
        readiness = loaded
        if readiness.source_config_fingerprint != resolved.source_config_fingerprint:
            raise CampaignStatusError("campaign readiness belongs to a different source config")
        if len(readiness.tracks) != len(resolved.config.track_modes) or {
            item.track for item in readiness.tracks
        } != set(resolved.config.track_modes):
            raise CampaignStatusError("campaign readiness tracks do not match config")
        configured_models = {
            item.name: (item.provider, item.model) for item in resolved.config.models
        }
        readiness_models = {
            item.name: (item.provider, item.model) for item in readiness.models
        }
        if len(readiness.models) != len(configured_models) or (
            readiness_models != configured_models
        ):
            raise CampaignStatusError("campaign readiness models do not match config")
    else:
        execution_evidence = lifecycle.checkpointed_results > 0 or bool(
            lifecycle.completed_tracks
        )
        if not execution_evidence:
            for artifact_name in (
                _PROVENANCE_NAME,
                _STATE_NAME,
                _REPORT_NAME,
                _TRACK_RECEIPT_NAME,
            ):
                if next(output_dir.glob(f"**/{artifact_name}"), None) is not None:
                    execution_evidence = True
                    break
        if lifecycle.status == "completed" or execution_evidence:
            raise CampaignStatusError(
                "campaign execution evidence exists without campaign readiness"
            )

    readiness_by_track = {
        item.track: item for item in (readiness.tracks if readiness is not None else ())
    }
    track_receipts: dict[Track, TrackCompletionV2] = {}
    for track in resolved.config.track_modes:
        receipt_path = output_dir / track.value / _TRACK_RECEIPT_NAME
        if not receipt_path.exists():
            continue
        loaded = _load_model(receipt_path, TrackCompletionV2, "track completion")
        assert isinstance(loaded, TrackCompletionV2)
        receipt = loaded
        if receipt.source_config_fingerprint != resolved.source_config_fingerprint:
            raise CampaignStatusError("track completion belongs to a different source config")
        if receipt.track is not track:
            raise CampaignStatusError("track completion is stored under the wrong track")
        expected_track_runs = sum(item[0] is track for item in expected_runs)
        if receipt.run_count != expected_track_runs:
            raise CampaignStatusError("track completion run count disagrees with config")
        ready = readiness_by_track.get(track)
        if ready is not None and (
            receipt.expected_task_count_per_run != ready.task_count
            or receipt.candidate_release_fingerprint
            != ready.candidate_release_fingerprint
            or receipt.live_certification_fingerprint
            != ready.live_certification_fingerprint
        ):
            raise CampaignStatusError("track completion disagrees with readiness")
        track_receipts[track] = receipt

    lifecycle_completed = {item.track: item for item in lifecycle.completed_tracks}
    for track, completed in lifecycle_completed.items():
        receipt = track_receipts.get(track)
        if receipt is None or completed.receipt_fingerprint != receipt.receipt_fingerprint:
            raise CampaignStatusError("lifecycle completed-track evidence is missing or stale")
    if lifecycle.status != "running" and set(track_receipts) != set(lifecycle_completed):
        raise CampaignStatusError("track completion receipts disagree with lifecycle")
    if lifecycle.status == "completed" and lifecycle.mode == "execution" and set(
        track_receipts
    ) != set(resolved.config.track_modes):
        raise CampaignStatusError("completed execution is missing a track receipt")

    _assert_no_unexpected_run_directories(output_dir, expected_keys)
    run_statuses: list[CampaignRunStatusV1] = []
    provenance_by_track: dict[Track, list[ModelRunProvenanceV2]] = {}
    for track, name, provider, provider_model, run_index in expected_runs:
        ready = readiness_by_track.get(track)
        run_status, provenance = _read_run(
            run_dir=output_dir / track.value / name / f"run-{run_index:03d}",
            track=track,
            model_name=name,
            provider=provider,
            provider_model=provider_model,
            run_index=run_index,
            expected_tasks=(ready.task_count if ready is not None else None),
            track_receipt=track_receipts.get(track),
            allow_uncommitted_report=lifecycle.status in {"running", "interrupted"},
        )
        run_statuses.append(run_status)
        if provenance is not None:
            provenance_by_track.setdefault(track, []).append(provenance)

    for track, provenances in provenance_by_track.items():
        ready = readiness_by_track.get(track)
        receipt = track_receipts.get(track)
        for provenance in provenances:
            if ready is not None and (
                provenance.candidate_release_fingerprint
                != ready.candidate_release_fingerprint
                or provenance.live_certification_fingerprint
                != ready.live_certification_fingerprint
                or provenance.base.public_artifact_fingerprint
                != ready.public_artifact_fingerprint
                or provenance.base.oracle_artifact_fingerprint
                != ready.oracle_artifact_fingerprint
                or provenance.base.capability_profile_fingerprint
                != ready.capability_profile_fingerprint
                or provenance.source_manifest_sha256 != readiness.source_manifest_sha256
                or provenance.archive_sha256 != readiness.archive_sha256
            ):
                raise CampaignStatusError("run provenance disagrees with readiness")
            if receipt is not None and (
                provenance.candidate_release_fingerprint
                != receipt.candidate_release_fingerprint
                or provenance.live_certification_fingerprint
                != receipt.live_certification_fingerprint
            ):
                raise CampaignStatusError("run provenance disagrees with track completion")

    actual_checkpointed = sum(item.checkpointed_results for item in run_statuses)
    if lifecycle.status == "running" and lock_contended:
        if lifecycle.checkpointed_results > actual_checkpointed:
            raise CampaignStatusError("lifecycle checkpoint count exceeds durable run state")
    elif lifecycle.checkpointed_results != actual_checkpointed:
        raise CampaignStatusError("lifecycle checkpoint count disagrees with run states")

    track_statuses: list[CampaignTrackStatusV1] = []
    for track in resolved.config.track_modes:
        track_runs = [item for item in run_statuses if item.track is track]
        receipt = track_receipts.get(track)
        ready = readiness_by_track.get(track)
        track_statuses.append(
            CampaignTrackStatusV1(
                track=track,
                expected_runs=len(track_runs),
                expected_tasks_per_run=(
                    ready.task_count
                    if ready is not None
                    else receipt.expected_task_count_per_run
                    if receipt is not None
                    else None
                ),
                checkpointed_results=sum(item.checkpointed_results for item in track_runs),
                completed_results=(receipt.result_count if receipt is not None else 0),
                provider_attempts=sum(item.provider_attempts for item in track_runs),
                tokens_input=sum(item.tokens_input for item in track_runs),
                tokens_output=sum(item.tokens_output for item in track_runs),
                total_tokens=sum(item.total_tokens for item in track_runs),
                completion_present=receipt is not None,
                campaign_valid=(receipt.campaign_valid if receipt is not None else None),
                candidate_release_fingerprint=(
                    receipt.candidate_release_fingerprint
                    if receipt is not None
                    else ready.candidate_release_fingerprint
                    if ready is not None
                    else None
                ),
                live_certification_fingerprint=(
                    receipt.live_certification_fingerprint
                    if receipt is not None
                    else ready.live_certification_fingerprint
                    if ready is not None
                    else None
                ),
                graph_verification_before_fingerprint=(
                    receipt.graph_verification_before_fingerprint
                    if receipt is not None
                    else None
                ),
                graph_verification_after_fingerprint=(
                    receipt.graph_verification_after_fingerprint
                    if receipt is not None
                    else None
                ),
            )
        )

    completed_results = sum(item.completed_results for item in track_statuses)
    task_counts = [item.expected_tasks_per_run for item in track_statuses]
    expected_results = (
        sum(item.expected_runs * item.expected_tasks_per_run for item in track_statuses)
        if all(value is not None for value in task_counts)
        else None
    )
    all_valid = all(
        item.campaign_valid is not False for item in track_statuses if item.completion_present
    )

    if observed_state == "running":
        resume_allowed, next_action = False, "monitor"
    elif observed_state in {"stale_running", "interrupted"}:
        resume_allowed = True
        next_action = (
            "run_readiness"
            if lifecycle.mode == "readiness"
            else "resume_campaign"
        )
    elif observed_state == "failed":
        resume_allowed, next_action = False, "investigate_failure"
    elif observed_state == "readiness_complete":
        resume_allowed, next_action = True, "execute_campaign"
    elif observed_state == "completed":
        resume_allowed = False
        next_action = "campaign_complete" if all_valid else "campaign_complete_invalid"
    else:  # pragma: no cover - not_started returned before lifecycle parsing
        resume_allowed, next_action = False, "run_readiness"

    active_run = (
        ActiveRunIdentityV1(
            track=lifecycle.active_track,
            model=lifecycle.active_model,
            run_index=lifecycle.active_run_index,
        )
        if lifecycle.active_track is not None
        and lifecycle.active_model is not None
        and lifecycle.active_run_index is not None
        else None
    )
    if active_run is not None and (
        active_run.track,
        active_run.model,
        active_run.run_index,
    ) not in expected_keys:
        raise CampaignStatusError("lifecycle active run is not selected by config")

    return CampaignStatusV1(
        source_config_fingerprint=resolved.source_config_fingerprint,
        lifecycle_state=lifecycle.status,
        observed_state=observed_state,
        mode=lifecycle.mode,
        started_at_utc=lifecycle.started_at_utc,
        updated_at_utc=lifecycle.updated_at_utc,
        resume_count=lifecycle.resume_count,
        active_run=active_run,
        completed_tracks=tuple(item.track for item in lifecycle.completed_tracks),
        progress=CampaignProgressV1(
            expected_runs=len(expected_runs),
            runs_started=sum(
                item.started for item in run_statuses
            ),
            runs_reported=sum(item.report_present for item in run_statuses),
            expected_results=expected_results,
            checkpointed_results=actual_checkpointed,
            completed_results=completed_results,
            provider_attempts=sum(item.provider_attempts for item in run_statuses),
            tokens_input=sum(item.tokens_input for item in run_statuses),
            tokens_output=sum(item.tokens_output for item in run_statuses),
            total_tokens=sum(item.total_tokens for item in run_statuses),
        ),
        tracks=tuple(track_statuses),
        runs=tuple(run_statuses),
        graph_fingerprint=(readiness.graph_fingerprint if readiness is not None else None),
        resume_allowed=resume_allowed,
        next_action=next_action,
    )
