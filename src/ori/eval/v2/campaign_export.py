"""Fail-closed, redacted export for completed V2 campaigns.

This is intentionally separate from model-card generation.  Model cards retain
their historical Direct-plus-MCP presentation contract; this exporter exposes
the validated public evidence for either an official MCP-only campaign or a
diagnostic canary without re-scoring model answers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .campaign_config import load_v2_campaign_config
from .campaign_runner import ModelPublicReportV2, _atomic_write
from .campaign_status import CampaignStatusError, inspect_v2_campaign_status
from .fingerprint import canonical_sha256
from .schema import StrictModel, Track

PUBLIC_EXPORT_SCHEMA_VERSION = "ori-v2-campaign-public-export-v2"


class PublicRunMetricV1(StrictModel):
    track: Track
    model_label: str = Field(pattern=r"^model-[0-9]{3}$")
    run_index: int = Field(strict=True, ge=1)
    scheduled: int = Field(strict=True, ge=1)
    completed: int = Field(strict=True, ge=0)
    correct: int = Field(strict=True, ge=0)
    incorrect: int = Field(strict=True, ge=0)
    reasoning_accuracy: float | None = None
    effective_accuracy: float | None = None
    campaign_valid: Literal[True]


class PublicTrackScheduleV1(StrictModel):
    """Public binding to one validated, selected task roster.

    The task IDs remain private so this artifact cannot become a task-discovery
    side channel.  The roster fingerprint, selection lineage, and cardinality
    let a public consumer bind every aggregate to the exact released schedule.
    """

    track: Track
    task_count: int = Field(strict=True, gt=0)
    schedule_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    selection_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    suite: str = Field(min_length=1)
    candidate_release_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    live_certification_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class PublicCampaignExportV1(StrictModel):
    schema_version: Literal["ori-v2-campaign-public-export-v2"] = PUBLIC_EXPORT_SCHEMA_VERSION
    purpose: Literal["official", "diagnostic_canary"]
    ranking_eligible: bool
    protocol: Literal["ori-eval-protocol-v2"] = "ori-eval-protocol-v2"
    source_config_fingerprint: str
    graph_fingerprint: str
    schedules: tuple[PublicTrackScheduleV1, ...]
    runs: tuple[PublicRunMetricV1, ...]
    export_fingerprint: str

    @model_validator(mode="after")
    def exact_public_export(self) -> PublicCampaignExportV1:
        if self.ranking_eligible != (self.purpose == "official"):
            raise ValueError("public export ranking eligibility conflicts with campaign purpose")
        if not self.runs:
            raise ValueError("public export requires at least one validated run")
        if not self.schedules:
            raise ValueError("public export requires at least one validated schedule")
        schedule_by_track = {schedule.track: schedule for schedule in self.schedules}
        if len(schedule_by_track) != len(self.schedules):
            raise ValueError("public export contains duplicate track schedules")
        identities = [(run.track, run.model_label, run.run_index) for run in self.runs]
        if len(identities) != len(set(identities)):
            raise ValueError("public export contains duplicate run identities")
        for run in self.runs:
            schedule = schedule_by_track.get(run.track)
            if schedule is None:
                raise ValueError("public export run has no validated track schedule")
            if run.scheduled != schedule.task_count:
                raise ValueError("public export run count does not match its validated schedule")
        if self.export_fingerprint != canonical_sha256(
            self, exclude_fields=("export_fingerprint",)
        ):
            raise ValueError("public export fingerprint mismatch")
        return self


def _public_run_metric(path: Path, *, model_label: str) -> PublicRunMetricV1:
    try:
        report = ModelPublicReportV2.model_validate_json(path.read_text())
    except (OSError, ValueError) as exc:
        raise ValueError(f"invalid public model report: {exc}") from exc
    summary = report.report.summary
    if not summary.campaign_valid:
        raise ValueError("cannot export an invalid model campaign")
    identity = report.run_identity
    return PublicRunMetricV1(
        track=report.report.track,
        model_label=model_label,
        run_index=identity.run_index,
        scheduled=summary.scheduled,
        completed=summary.completed,
        correct=summary.correct,
        incorrect=summary.incorrect,
        reasoning_accuracy=summary.reasoning_accuracy,
        effective_accuracy=summary.effective_accuracy,
        campaign_valid=True,
    )


def export_v2_campaign_public(*, config_path: Path, output_dir: Path) -> Path:
    """Validate and write a compact public export without contacting services.

    ``campaign-status`` remains the authority for lifecycle/completion checks.
    It validates every report against completion, graph, readiness, and durable
    state evidence before this function reads a public report's aggregate
    metrics.  Raw responses, tool data, query text, private receipts, paths,
    and endpoint data are never copied to the export.
    """

    resolved = load_v2_campaign_config(config_path)
    try:
        status = inspect_v2_campaign_status(config_path)
    except CampaignStatusError as exc:
        raise ValueError(f"campaign status validation failed: {exc}") from exc
    if status.next_action != "campaign_complete" or status.observed_state != "completed":
        raise ValueError("campaign is not validly completed and cannot be publicly exported")
    if status.graph_fingerprint is None:
        raise ValueError("completed campaign is missing graph identity")

    schedules: list[PublicTrackScheduleV1] = []
    for track in status.tracks:
        if (
            track.expected_tasks_per_run is None
            or track.schedule_fingerprint is None
            or track.selection_fingerprint is None
            or track.suite is None
            or track.candidate_release_fingerprint is None
            or track.live_certification_fingerprint is None
        ):
            raise ValueError("completed campaign track is missing validated schedule evidence")
        schedules.append(
            PublicTrackScheduleV1(
                track=track.track,
                task_count=track.expected_tasks_per_run,
                schedule_fingerprint=track.schedule_fingerprint,
                selection_fingerprint=track.selection_fingerprint,
                suite=track.suite,
                candidate_release_fingerprint=track.candidate_release_fingerprint,
                live_certification_fingerprint=track.live_certification_fingerprint,
            )
        )

    paths = sorted(resolved.output_dir.glob("*/*/run-*/public-report-v2.json"))
    if not paths:
        raise ValueError("completed campaign has no public reports")
    labels = {
        model.name: f"model-{index:03d}"
        for index, model in enumerate(resolved.config.models, start=1)
    }
    runs = tuple(
        sorted(
            (
                _public_run_metric(
                    path,
                    model_label=labels[path.parent.parent.name],
                )
                for path in paths
                if path.parent.parent.name in labels
            ),
            key=lambda item: (
                item.track.value,
                item.model_label,
                item.run_index,
            ),
        )
    )
    if len(runs) != len(paths):
        raise ValueError("public export report path is not selected by campaign config")
    expected = sum(track.expected_runs for track in status.tracks)
    if len(runs) != expected:
        raise ValueError("public export report count does not match validated campaign status")
    payload = {
        "purpose": resolved.config.purpose,
        "ranking_eligible": resolved.config.purpose == "official",
        "source_config_fingerprint": status.source_config_fingerprint,
        "graph_fingerprint": status.graph_fingerprint,
        "schedules": tuple(sorted(schedules, key=lambda item: item.track.value)),
        "runs": runs,
    }
    document_payload = {
        "schema_version": PUBLIC_EXPORT_SCHEMA_VERSION,
        "protocol": "ori-eval-protocol-v2",
        **payload,
        "export_fingerprint": "0" * 64,
    }
    document = PublicCampaignExportV1.model_validate(
        {
            **document_payload,
            "export_fingerprint": canonical_sha256(
                document_payload, exclude_fields=("export_fingerprint",)
            ),
        }
    )
    destination = output_dir / "campaign-public-summary-v1.json"
    if destination.exists():
        try:
            existing = PublicCampaignExportV1.model_validate_json(destination.read_text())
        except (OSError, ValueError) as exc:
            raise ValueError(f"existing public export is invalid: {exc}") from exc
        if existing != document:
            raise ValueError("public export destination already contains another campaign")
        return destination
    _atomic_write(destination, document.model_dump(mode="json"))
    return destination
