"""Read-only, bounded diagnostic admission projection owned and validated by ORI."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .campaign_config import load_v2_campaign_config
from .campaign_runner import ModelPublicReportV2, RunOperationalMetricsV2, TrackCompletionV2
from .campaign_status import inspect_v2_campaign_status
from .diagnostic_selection import CANARY_KINDS, ClaimKind, DiagnosticSelectionReceiptV1
from .fingerprint import canonical_sha256
from .schema import Fingerprint, StrictModel, Track
from .scoring import SampleOutcomeCode

DIAGNOSTIC_RESULT_SCHEMA_VERSION = "ori-v2-diagnostic-result-v1"


class DiagnosticKindOutcomeV1(StrictModel):
    kind: ClaimKind
    outcome: SampleOutcomeCode
    reasoning_correct: bool | None = Field(default=None, strict=True)
    output_compliant: bool | None = Field(default=None, strict=True)

    @model_validator(mode="after")
    def correctness_belongs_to_completed(self):
        if (self.outcome is SampleOutcomeCode.COMPLETED) != (self.reasoning_correct is not None):
            raise ValueError("diagnostic correctness must belong to a completed outcome")
        return self


class DiagnosticResultV1(StrictModel):
    schema_version: Literal["ori-v2-diagnostic-result-v1"] = DIAGNOSTIC_RESULT_SCHEMA_VERSION
    purpose: Literal["diagnostic_canary"] = "diagnostic_canary"
    ranking_eligible: Literal[False] = False
    suite: Literal["native-five-kind-v1"] = "native-five-kind-v1"
    source_config_fingerprint: Fingerprint
    selection_fingerprint: Fingerprint
    schedule_fingerprint: Fingerprint
    completion_fingerprint: Fingerprint
    report_fingerprint: Fingerprint
    graph_fingerprint: Fingerprint
    graph_valid: Literal[True] = True
    campaign_valid: Literal[True] = True
    outcomes: tuple[DiagnosticKindOutcomeV1, ...]
    usage: RunOperationalMetricsV2
    result_fingerprint: Fingerprint

    @model_validator(mode="after")
    def exactly_five(self):
        if Counter(row.kind for row in self.outcomes) != {kind: 1 for kind in CANARY_KINDS}:
            raise ValueError("diagnostic result requires exactly one outcome per kind")
        if self.result_fingerprint != canonical_sha256(
            self, exclude_fields=("result_fingerprint",)
        ):
            raise ValueError("diagnostic result fingerprint mismatch")
        return self


def inspect_canary_result(config_path: Path) -> DiagnosticResultV1:
    """Validate real roster/completion/report lineage before dropping private identities."""
    resolved = load_v2_campaign_config(config_path)
    if (
        resolved.config.purpose != "diagnostic_canary"
        or resolved.canary_selection is None
        or resolved.config.modes != ["mcp"]
        or len(resolved.config.models) != 1
        or resolved.config.models[0].runs_per_model not in {None, 1}
        or resolved.config.defaults.runs_per_model != 1
    ):
        raise ValueError("DIAGNOSTIC_INSPECTION_SINGLE_CANARY_REQUIRED")
    status = inspect_v2_campaign_status(config_path)
    if (
        status.observed_state != "completed"
        or status.next_action != "campaign_complete"
        or status.graph_fingerprint is None
    ):
        raise ValueError("DIAGNOSTIC_COMPLETION_INVALID")
    # Re-derive recipe eligibility from the original certified source. Coherently
    # rehashed user-authored five-item artifacts do not establish membership.
    if resolved.native_mcp_paths is not None:
        from .campaign_runner import prepare_native_qualification

        _, prepared, _, _, _, _ = prepare_native_qualification(config_path)
    else:
        import json

        from .campaign_runner import _prepare_track, prepare_diagnostic_canary_track
        from .graph import build_archive_snapshot

        snapshot = build_archive_snapshot(
            resolved.archive,
            json.loads(resolved.source_manifest.read_text()),
            product="oaic-2026-v1",
        )
        prepared = prepare_diagnostic_canary_track(
            resolved, snapshot, {Track.MCP: _prepare_track(resolved, Track.MCP, snapshot)}
        )[Track.MCP]
    selection = DiagnosticSelectionReceiptV1.model_validate_json(
        resolved.canary_selection.read_text()
    )
    completion = TrackCompletionV2.model_validate_json(
        (resolved.output_dir / "mcp" / "track-completion-v2.private.json").read_text()
    )
    model_name = resolved.config.models[0].name
    report = ModelPublicReportV2.model_validate_json(
        (resolved.output_dir / "mcp" / model_name / "run-001" / "public-report-v2.json").read_text()
    )
    if (
        completion.schedule.purpose != "diagnostic_canary"
        or completion.schedule.task_ids != selection.selected_task_ids
        or tuple(prepared.task_ids) != selection.selected_task_ids
        or completion.schedule.selection_fingerprint != selection.selection_fingerprint
        or completion.source_config_fingerprint != status.source_config_fingerprint
        or completion.schedule.schedule_fingerprint != report.schedule_fingerprint
        or completion.runs[0].public_report_fingerprint != report.artifact_fingerprint
        or completion.run_count != 1
        or completion.result_count != 5
        or not completion.campaign_valid
        or not report.report.summary.campaign_valid
        or report.report.graph_fingerprint != status.graph_fingerprint
    ):
        raise ValueError("DIAGNOSTIC_ROSTER_COMPLETION_MISMATCH")
    rows = {row.task_id: row for row in report.report.rows}
    if set(rows) != set(selection.selected_task_ids):
        raise ValueError("DIAGNOSTIC_REPORT_ROSTER_MISMATCH")
    task_fingerprints = {task.task_id: task.task_fingerprint for task in prepared.pair.public.tasks}
    if any(rows[task_id].task_fingerprint != task_fingerprints[task_id] for task_id in rows):
        raise ValueError("DIAGNOSTIC_REPORT_TASK_BINDING_MISMATCH")
    payload = {
        "schema_version": DIAGNOSTIC_RESULT_SCHEMA_VERSION,
        "purpose": "diagnostic_canary",
        "ranking_eligible": False,
        "suite": selection.suite,
        "source_config_fingerprint": status.source_config_fingerprint,
        "selection_fingerprint": selection.selection_fingerprint,
        "schedule_fingerprint": completion.schedule.schedule_fingerprint,
        "completion_fingerprint": completion.receipt_fingerprint,
        "report_fingerprint": report.artifact_fingerprint,
        "graph_fingerprint": status.graph_fingerprint,
        "graph_valid": True,
        "campaign_valid": True,
        "usage": report.operational_metrics,
        "outcomes": tuple(
            DiagnosticKindOutcomeV1(
                kind=entry.claim_kind,
                outcome=rows[entry.task_id].outcome,
                reasoning_correct=rows[entry.task_id].reasoning_correct,
                output_compliant=rows[entry.task_id].output_compliant,
            )
            for entry in selection.entries
        ),
    }
    return DiagnosticResultV1.model_validate(
        {**payload, "result_fingerprint": canonical_sha256(payload)}
    )
