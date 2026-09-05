"""Build a public-safe V30 model card from a completed V2 campaign."""

# SVG elements remain one line each so the generated image is deterministic and
# reviewable without an XML formatter.
# ruff: noqa: E501

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, Field, ValidationError, model_validator

from ori.eval.v2.campaign_runner import (
    CampaignLifecycleV2,
    CampaignReadinessV2,
    ModelPublicReportV2,
    PrivateRunStateV2,
    TrackCompletionV2,
    _run_operational_metrics,
)
from ori.eval.v2.schema import ExecutionClass, StrictModel, Track
from ori.eval.v2.scoring import CampaignSummary

SCHEMA_VERSION = "ori-v30-model-card-v3"
JSON_NAME = "v30-model-card.json"
SVG_NAME = "v30-model-card.svg"
RUN_STATE_NAME = "run-state-v7.private.json"

_MODEL = TypeVar("_MODEL", bound=BaseModel)
_SENSITIVE_LABEL = re.compile(
    r"(?i)(?:api[_-]?key|password|passwd|secret|bearer|authorization|token)\s*[:=]"
)
_WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")


class ModelCardBuildError(ValueError):
    """Raised when campaign evidence cannot support a public model card."""


@dataclass(frozen=True)
class LoadedRun:
    report: ModelPublicReportV2
    state: PrivateRunStateV2
    sha256: str
    state_sha256: str

    @property
    def key(self) -> tuple[str, str, int]:
        identity = self.report.run_identity
        return identity.provider, identity.model, identity.run_index


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _load_model(path: Path, model: type[_MODEL], label: str) -> tuple[_MODEL, bytes]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ModelCardBuildError(f"cannot read {label}: {exc}") from exc
    try:
        return model.model_validate_json(raw), raw
    except ValidationError as exc:
        raise ModelCardBuildError(f"invalid {label}: {exc}") from exc


def _public_label(value: str, label: str) -> str:
    value = value.strip()
    if not value:
        raise ModelCardBuildError(f"{label} must be non-empty")
    if any(character in value for character in ("\x00", "\r", "\n")):
        raise ModelCardBuildError(f"{label} contains control characters")
    if Path(value).is_absolute() or _WINDOWS_ABSOLUTE.match(value):
        raise ModelCardBuildError(f"{label} must not contain a local path")
    if _SENSITIVE_LABEL.search(value) or value.casefold().startswith("sk-"):
        raise ModelCardBuildError(f"{label} resembles secret material")
    return value


def _run_operational_summary(state: PrivateRunStateV2) -> OperationalSummary:
    metrics = _run_operational_metrics(state)
    runs = 1
    return OperationalSummary(
        runs=runs,
        attempts_total=metrics.attempts_total,
        attempts_average=metrics.attempts_total / runs,
        retries_total=metrics.retries_total,
        retries_average=metrics.retries_total / runs,
        immediate_retries_total=metrics.immediate_retries_total,
        immediate_retries_average=metrics.immediate_retries_total / runs,
        deferred_retries_total=metrics.deferred_retries_total,
        deferred_retries_average=metrics.deferred_retries_total / runs,
        recovered_infrastructure_tasks=metrics.recovered_infrastructure_tasks,
        exhausted_infrastructure_tasks=metrics.exhausted_infrastructure_tasks,
        completed_recovery_rounds=metrics.completed_recovery_rounds,
        tokens_input_total=metrics.tokens_input_total,
        tokens_input_average=metrics.tokens_input_total / runs,
        tokens_output_total=metrics.tokens_output_total,
        tokens_output_average=metrics.tokens_output_total / runs,
        total_tokens_total=metrics.total_tokens_total,
        total_tokens_average=metrics.total_tokens_total / runs,
        elapsed_seconds_total=metrics.elapsed_seconds_total,
        elapsed_seconds_average=metrics.elapsed_seconds_total / runs,
        mcp_tool_calls_total=metrics.mcp_tool_calls_total,
        mcp_tool_calls_average=metrics.mcp_tool_calls_total / runs,
        cypher_query_calls_total=metrics.cypher_query_calls_total,
        cypher_query_calls_average=metrics.cypher_query_calls_total / runs,
        non_cypher_tool_calls_total=metrics.non_cypher_tool_calls_total,
        non_cypher_tool_calls_average=metrics.non_cypher_tool_calls_total / runs,
        failed_tool_calls_total=metrics.failed_tool_calls_total,
        failed_tool_calls_average=metrics.failed_tool_calls_total / runs,
        resource_read_calls_total=metrics.resource_read_calls_total,
        resource_read_calls_average=metrics.resource_read_calls_total / runs,
    )


def _aggregate_operational_summaries(
    summaries: Iterable[OperationalSummary],
) -> OperationalSummary:
    items = tuple(summaries)
    if not items:
        raise ModelCardBuildError("operational summary requires at least one run")
    total_runs = sum(item.runs for item in items)
    payload: dict[str, Any] = {
        "runs": total_runs,
        "attempts_total": sum(item.attempts_total for item in items),
        "retries_total": sum(item.retries_total for item in items),
        "immediate_retries_total": sum(item.immediate_retries_total for item in items),
        "deferred_retries_total": sum(item.deferred_retries_total for item in items),
        "recovered_infrastructure_tasks": sum(
            item.recovered_infrastructure_tasks for item in items
        ),
        "exhausted_infrastructure_tasks": sum(
            item.exhausted_infrastructure_tasks for item in items
        ),
        "completed_recovery_rounds": sum(item.completed_recovery_rounds for item in items),
        "tokens_input_total": sum(item.tokens_input_total for item in items),
        "tokens_output_total": sum(item.tokens_output_total for item in items),
        "total_tokens_total": sum(item.total_tokens_total for item in items),
        "elapsed_seconds_total": sum(item.elapsed_seconds_total for item in items),
        "mcp_tool_calls_total": sum(item.mcp_tool_calls_total for item in items),
        "cypher_query_calls_total": sum(item.cypher_query_calls_total for item in items),
        "non_cypher_tool_calls_total": sum(item.non_cypher_tool_calls_total for item in items),
        "failed_tool_calls_total": sum(item.failed_tool_calls_total for item in items),
        "resource_read_calls_total": sum(item.resource_read_calls_total for item in items),
    }
    for field_name in (
        "attempts",
        "retries",
        "immediate_retries",
        "deferred_retries",
        "tokens_input",
        "tokens_output",
        "total_tokens",
        "elapsed_seconds",
        "mcp_tool_calls",
        "cypher_query_calls",
        "non_cypher_tool_calls",
        "failed_tool_calls",
        "resource_read_calls",
    ):
        payload[f"{field_name}_average"] = payload[f"{field_name}_total"] / total_runs
    return OperationalSummary.model_validate(payload)


def _assert_run_state_matches_report(
    report: ModelPublicReportV2,
    state: PrivateRunStateV2,
) -> None:
    if report.run_identity != state.checkpoint.run_identity:
        raise ModelCardBuildError("public report run identity disagrees with private state")
    if len(report.report.rows) != len(state.checkpoint.results):
        raise ModelCardBuildError("private state result count disagrees with public report")
    rows_by_task = {row.task_id: row for row in report.report.rows}
    results_by_task = {result.task_id: result for result in state.checkpoint.results}
    if set(rows_by_task) != set(results_by_task):
        raise ModelCardBuildError("private state task set disagrees with public report")
    for task_id, row in rows_by_task.items():
        result = results_by_task[task_id]
        if row.execution_class != result.execution_class or row.outcome != result.outcome:
            raise ModelCardBuildError(f"private state outcome disagrees for {task_id}")
        if row.reasoning_correct != result.reasoning_correct:
            raise ModelCardBuildError(f"private state reasoning flag disagrees for {task_id}")
        if row.output_compliant != result.output_compliant:
            raise ModelCardBuildError(
                f"private state output compliance flag disagrees for {task_id}"
            )
        if row.output_normalized != result.output_normalized:
            raise ModelCardBuildError(
                f"private state output normalization flag disagrees for {task_id}"
            )
    if report.operational_metrics != _run_operational_metrics(state):
        raise ModelCardBuildError("private state operational metrics disagree with public report")


class OperationalSummary(StrictModel):
    runs: int = Field(strict=True, ge=0)
    attempts_total: int = Field(strict=True, ge=0)
    attempts_average: float = Field(strict=True, ge=0)
    retries_total: int = Field(strict=True, ge=0)
    retries_average: float = Field(strict=True, ge=0)
    immediate_retries_total: int = Field(strict=True, ge=0)
    immediate_retries_average: float = Field(strict=True, ge=0)
    deferred_retries_total: int = Field(strict=True, ge=0)
    deferred_retries_average: float = Field(strict=True, ge=0)
    recovered_infrastructure_tasks: int = Field(strict=True, ge=0)
    exhausted_infrastructure_tasks: int = Field(strict=True, ge=0)
    completed_recovery_rounds: int = Field(strict=True, ge=0)
    tokens_input_total: int = Field(strict=True, ge=0)
    tokens_input_average: float = Field(strict=True, ge=0)
    tokens_output_total: int = Field(strict=True, ge=0)
    tokens_output_average: float = Field(strict=True, ge=0)
    total_tokens_total: int = Field(strict=True, ge=0)
    total_tokens_average: float = Field(strict=True, ge=0)
    elapsed_seconds_total: float = Field(strict=True, ge=0)
    elapsed_seconds_average: float = Field(strict=True, ge=0)
    mcp_tool_calls_total: int = Field(strict=True, ge=0)
    mcp_tool_calls_average: float = Field(strict=True, ge=0)
    cypher_query_calls_total: int = Field(strict=True, ge=0)
    cypher_query_calls_average: float = Field(strict=True, ge=0)
    non_cypher_tool_calls_total: int = Field(strict=True, ge=0)
    non_cypher_tool_calls_average: float = Field(strict=True, ge=0)
    failed_tool_calls_total: int = Field(strict=True, ge=0)
    failed_tool_calls_average: float = Field(strict=True, ge=0)
    resource_read_calls_total: int = Field(strict=True, ge=0)
    resource_read_calls_average: float = Field(strict=True, ge=0)

    @model_validator(mode="after")
    def averages_are_finite(self) -> OperationalSummary:
        for field_name in (
            "attempts_average",
            "retries_average",
            "immediate_retries_average",
            "deferred_retries_average",
            "tokens_input_average",
            "tokens_output_average",
            "total_tokens_average",
            "elapsed_seconds_average",
            "mcp_tool_calls_average",
            "cypher_query_calls_average",
            "non_cypher_tool_calls_average",
            "failed_tool_calls_average",
            "resource_read_calls_average",
        ):
            if not math.isfinite(getattr(self, field_name)):
                raise ValueError(f"{field_name} must be finite")
        return self


def _readiness_track(
    readiness: CampaignReadinessV2,
    track: Track,
):
    matches = [item for item in readiness.tracks if item.track is track]
    if len(matches) != 1:
        raise ModelCardBuildError(f"readiness must contain exactly one {track.value} track receipt")
    return matches[0]


def _assert_summary_matches_rows(
    report: ModelPublicReportV2,
    track: Track,
) -> None:
    body = report.report
    rows = body.rows
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
    expected = CampaignSummary(
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
        reasoning_accuracy=(correct / reasoning_denominator if reasoning_denominator else None),
        effective_accuracy=(correct / len(rows) if rows else None),
        output_compliance_rate=(output_compliant / observed_outputs if observed_outputs else None),
        campaign_valid=not invalid_reasons,
        invalid_reasons=tuple(invalid_reasons),
    )
    if body.summary != expected:
        raise ModelCardBuildError(f"{track.value} public summary disagrees with its redacted rows")


def _load_track_reports(
    campaign_root: Path,
    track: Track,
    receipt: TrackCompletionV2,
    readiness: CampaignReadinessV2,
) -> dict[tuple[str, str, int], LoadedRun]:
    track_readiness = _readiness_track(readiness, track)
    expected_runs = {(run.provider, run.model, run.run_index): run for run in receipt.runs}
    paths = sorted((campaign_root / track.value).glob("*/run-*/public-report-v2.json"))
    loaded: dict[tuple[str, str, int], LoadedRun] = {}
    for path in paths:
        report, raw = _load_model(path, ModelPublicReportV2, f"{track.value} public report")
        state, state_raw = _load_model(
            path.parent / RUN_STATE_NAME,
            PrivateRunStateV2,
            f"{track.value} private run state",
        )
        key = (
            report.run_identity.provider,
            report.run_identity.model,
            report.run_identity.run_index,
        )
        if key in loaded:
            raise ModelCardBuildError(f"duplicate {track.value} public run identity")
        loaded[key] = LoadedRun(
            report=report,
            state=state,
            sha256=_sha256(raw),
            state_sha256=_sha256(state_raw),
        )

    if set(loaded) != set(expected_runs):
        raise ModelCardBuildError(
            f"{track.value} public reports do not exactly match track completion"
        )

    for key, loaded_run in loaded.items():
        report = loaded_run.report
        body = report.report
        summary = body.summary
        expected = expected_runs[key]
        if body.track is not track:
            raise ModelCardBuildError(f"{track.value} report has the wrong track")
        _assert_summary_matches_rows(report, track)
        if report.artifact_fingerprint != expected.public_report_fingerprint:
            raise ModelCardBuildError(
                f"{track.value} public report fingerprint disagrees with completion receipt"
            )
        if len(body.rows) != expected.result_count or summary.scheduled != expected.result_count:
            raise ModelCardBuildError(
                f"{track.value} report result count disagrees with completion receipt"
            )
        if summary.scheduled != receipt.expected_task_count_per_run:
            raise ModelCardBuildError(
                f"{track.value} report schedule disagrees with track completion"
            )
        if summary.campaign_valid is not True or expected.campaign_valid is not True:
            raise ModelCardBuildError(f"{track.value} contains an invalid model run")
        if expected.invalid_reasons or summary.invalid_reasons:
            raise ModelCardBuildError(f"{track.value} contains invalid campaign reasons")
        if body.graph_fingerprint != readiness.graph_fingerprint:
            raise ModelCardBuildError(f"{track.value} graph fingerprint mismatch")
        if body.public_artifact_fingerprint != track_readiness.public_artifact_fingerprint:
            raise ModelCardBuildError(f"{track.value} public artifact mismatch")
        _assert_run_state_matches_report(report, loaded_run.state)
        if body.catalog_fingerprint == "":
            raise ModelCardBuildError(f"{track.value} catalog fingerprint is empty")
        if body.capability_profile_fingerprint != track_readiness.capability_profile_fingerprint:
            raise ModelCardBuildError(f"{track.value} capability fingerprint mismatch")
        if (
            report.candidate_release_fingerprint != receipt.candidate_release_fingerprint
            or report.candidate_release_fingerprint != track_readiness.candidate_release_fingerprint
        ):
            raise ModelCardBuildError(f"{track.value} candidate release mismatch")
        if (
            report.live_certification_fingerprint != receipt.live_certification_fingerprint
            or report.live_certification_fingerprint
            != track_readiness.live_certification_fingerprint
        ):
            raise ModelCardBuildError(f"{track.value} live certification mismatch")
        if (
            report.graph_verification_before_fingerprint
            != receipt.graph_verification_before_fingerprint
            or report.graph_verification_after_fingerprint
            != receipt.graph_verification_after_fingerprint
        ):
            raise ModelCardBuildError(f"{track.value} graph-gate receipt mismatch")
        if report.run_identity.target_fingerprint != readiness.target_fingerprint:
            raise ModelCardBuildError(f"{track.value} target fingerprint mismatch")
        if track is Track.DIRECT and report.run_identity.tool_loop is not None:
            raise ModelCardBuildError("Direct report unexpectedly declares an MCP loop")
        if track is Track.MCP and not report.run_identity.tool_loop:
            raise ModelCardBuildError("MCP report is missing its tool loop")
    return loaded


def _select_model(
    reports: dict[Track, dict[tuple[str, str, int], LoadedRun]],
    requested_model: str | None,
) -> tuple[str, str, tuple[int, ...]]:
    identities_by_track: dict[Track, set[tuple[str, str]]] = {
        track: {(provider, model) for provider, model, _index in runs}
        for track, runs in reports.items()
    }
    common = set.intersection(*identities_by_track.values())
    if requested_model is not None:
        requested_model = _public_label(requested_model, "requested model")
        common = {identity for identity in common if identity[1] == requested_model}
    if len(common) != 1:
        raise ModelCardBuildError(
            "campaign does not identify exactly one common Direct/MCP provider-model; "
            "supply --model when the campaign contains multiple models"
        )
    provider, model = next(iter(common))
    run_sets = {
        track: tuple(
            sorted(
                index
                for run_provider, run_model, index in runs
                if (run_provider, run_model) == (provider, model)
            )
        )
        for track, runs in reports.items()
    }
    if not run_sets[Track.DIRECT] or run_sets[Track.DIRECT] != run_sets[Track.MCP]:
        raise ModelCardBuildError("Direct and MCP model run indexes do not match")
    return provider, model, run_sets[Track.DIRECT]


def _aggregate(
    loaded: Iterable[LoadedRun],
    receipt: TrackCompletionV2,
) -> dict[str, Any]:
    runs = tuple(loaded)
    catalog_fingerprints = {item.report.report.catalog_fingerprint for item in runs}
    if len(catalog_fingerprints) != 1:
        raise ModelCardBuildError(
            f"{receipt.track.value} public reports disagree on catalog fingerprint"
        )
    summaries = tuple(item.report.report.summary for item in runs)
    operational_summaries = tuple(_run_operational_summary(item.state) for item in runs)
    scheduled = sum(summary.scheduled for summary in summaries)
    correct = sum(summary.correct for summary in summaries)
    incorrect = sum(summary.incorrect for summary in summaries)
    output_compliant = sum(summary.output_compliant for summary in summaries)
    output_noncompliant = sum(summary.output_noncompliant for summary in summaries)
    observed_outputs = output_compliant + output_noncompliant
    operations = _aggregate_operational_summaries(operational_summaries).model_dump()
    operations["resource_mode"] = "off" if receipt.track is Track.MCP else "not_applicable"
    attempts = operations["attempts_total"]
    operations.update(
        {
            "attempts_average_per_task": attempts / scheduled,
            "tokens_average_per_task": operations["total_tokens_total"] / scheduled,
            "tokens_average_per_attempt": (
                operations["total_tokens_total"] / attempts if attempts else None
            ),
            "elapsed_seconds_average_per_attempt": (
                operations["elapsed_seconds_total"] / attempts if attempts else None
            ),
            "mcp_tool_calls_average_per_task": (operations["mcp_tool_calls_total"] / scheduled),
            "cypher_query_calls_average_per_task": (
                operations["cypher_query_calls_total"] / scheduled
            ),
            "non_cypher_tool_calls_average_per_task": (
                operations["non_cypher_tool_calls_total"] / scheduled
            ),
            "failed_tool_calls_average_per_task": (
                operations["failed_tool_calls_total"] / scheduled
            ),
            "resource_read_calls_average_per_task": (
                operations["resource_read_calls_total"] / scheduled
            ),
        }
    )
    payload: dict[str, Any] = {
        "runs": len(runs),
        "scheduled": scheduled,
        "completed": sum(summary.completed for summary in summaries),
        "correct": correct,
        "incorrect": incorrect,
        "reasoning_accuracy": (correct / (correct + incorrect) if correct + incorrect else None),
        "effective_accuracy": correct / scheduled,
        "output_compliant": output_compliant,
        "output_noncompliant": output_noncompliant,
        "output_normalized": sum(summary.output_normalized for summary in summaries),
        "output_compliance_rate": (
            output_compliant / observed_outputs if observed_outputs else None
        ),
        "model_failures": sum(summary.model_failures for summary in summaries),
        "proof_failures": sum(summary.proof_failures for summary in summaries),
        "infrastructure_failures": sum(summary.infrastructure_failures for summary in summaries),
        "harness_failures": sum(summary.harness_failures for summary in summaries),
        "unexecuted": sum(summary.unexecuted for summary in summaries),
        "campaign_valid": True,
        "catalog_fingerprint": next(iter(catalog_fingerprints)),
        "candidate_release_fingerprint": receipt.candidate_release_fingerprint,
        "live_certification_fingerprint": receipt.live_certification_fingerprint,
        "graph_verification_before_fingerprint": receipt.graph_verification_before_fingerprint,
        "graph_verification_after_fingerprint": receipt.graph_verification_after_fingerprint,
        "track_completion_fingerprint": receipt.receipt_fingerprint,
        "public_report_fingerprints": sorted(item.report.artifact_fingerprint for item in runs),
        "public_report_sha256": sorted(item.sha256 for item in runs),
        "public_run_state_sha256": sorted(item.state_sha256 for item in runs),
        "operational_metrics": operations,
    }
    if not math.isfinite(payload["effective_accuracy"]):
        raise ModelCardBuildError("effective accuracy is not finite")
    return payload


def _percent(value: float) -> str:
    return f"{value * 100:.1f}%"


def _svg_bytes(card: dict[str, Any]) -> bytes:
    model = escape(card["display_name"])
    provider = escape(card["provider"])
    graph = escape(card["graph_fingerprint"][:12])
    runner = escape(card["runner_version"])
    lines = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720" role="img" aria-labelledby="title description">',
        f'<title id="title">ORI V30 model card for {model}</title>',
        f'<desc id="description">Separate graph-gated Direct and MCP results for {model}. No combined score is reported.</desc>',
        '<rect width="1280" height="720" fill="#0b1020"/>',
        '<style>text{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;fill:#f7f9fc}.muted{fill:#aeb8cb}.accent{fill:#66d9ef}.panel{fill:#151d33;stroke:#33415f;stroke-width:2}.good{fill:#70e1a1}.label{fill:#aeb8cb;font-size:16px}.value{fill:#f7f9fc;font-size:18px;font-weight:600}</style>',
        '<text x="54" y="48" font-size="15" font-weight="700" class="accent">OFFENSIVE REASONING INDEX · V30 SCORING BOUNDARY</text>',
        f'<text x="54" y="104" font-size="38" font-weight="700">{model}</text>',
        f'<text x="54" y="136" font-size="17" class="muted">Provider {provider} · graph {graph} · {runner}</text>',
        '<rect x="1016" y="52" width="210" height="54" rx="27" fill="#143b2b"/>',
        '<text x="1121" y="86" text-anchor="middle" font-size="17" font-weight="700" class="good">CAMPAIGN VALID</text>',
    ]
    panel_x = {"direct": 54, "mcp": 650}
    for track in ("direct", "mcp"):
        item = card["tracks"][track]
        x = panel_x[track]
        title = "DIRECT" if track == "direct" else "MCP"
        ops = item["operational_metrics"]
        lines.extend(
            [
                f'<rect x="{x}" y="174" width="576" height="430" rx="18" class="panel"/>',
                f'<text x="{x + 28}" y="216" font-size="20" font-weight="700" class="accent">{title}</text>',
                f'<text x="{x + 28}" y="286" font-size="58" font-weight="700">{_percent(item["effective_accuracy"])}</text>',
                f'<text x="{x + 30}" y="316" font-size="16" class="muted">effective accuracy · {item["correct"]}/{item["scheduled"]} correct · {item["runs"]} run(s)</text>',
                f'<text x="{x + 30}" y="338" font-size="14" class="muted">semantic {_percent(item["reasoning_accuracy"]) if item["reasoning_accuracy"] is not None else "n/a"} · output compliance {_percent(item["output_compliance_rate"]) if item["output_compliance_rate"] is not None else "n/a"} · normalized {item["output_normalized"]}</text>',
                f'<text x="{x + 30}" y="358" font-size="14" class="muted">ops · attempts {ops["attempts_total"]} · retries {ops["retries_total"]} · tokens {ops["total_tokens_total"]} · tools {ops["mcp_tool_calls_total"]} ({ops["mcp_tool_calls_average_per_task"]:.2f}/task) · resources {ops["resource_read_calls_total"]}</text>',
            ]
        )
        rows = (
            ("Model failures", item["model_failures"]),
            ("Proof failures", item["proof_failures"]),
            ("Infrastructure", item["infrastructure_failures"]),
            ("Harness", item["harness_failures"]),
            ("Unexecuted", item["unexecuted"]),
        )
        for index, (label, value) in enumerate(rows):
            row_y = 390 + index * 40
            lines.append(f'<text x="{x + 30}" y="{row_y}" class="label">{label}</text>')
            lines.append(
                f'<text x="{x + 546}" y="{row_y}" text-anchor="end" class="value">{value}</text>'
            )
        lines.append(
            f'<text x="{x + 30}" y="590" font-size="14" class="good">Graph-gated track completion verified</text>'
        )
    lines.extend(
        [
            '<text x="54" y="650" font-size="18" font-weight="700">Direct and MCP are separate evaluation surfaces. No combined score is reported.</text>',
            '<text x="54" y="680" font-size="15" class="muted">Public-safe aggregates verified against fingerprinted private attempts; no prompts, answers, tool bodies, paths, endpoints, or credentials are included.</text>',
            "</svg>",
        ]
    )
    return ("\n".join(lines) + "\n").encode()


def build_model_card(
    campaign_root: Path,
    output_dir: Path,
    *,
    model: str | None = None,
    display_name: str | None = None,
) -> dict[str, Any]:
    """Validate a completed Direct+MCP campaign and emit JSON plus SVG."""

    campaign_root = campaign_root.resolve()
    readiness, _readiness_raw = _load_model(
        campaign_root / "v2-run-readiness.private.json",
        CampaignReadinessV2,
        "campaign readiness",
    )
    lifecycle, _lifecycle_raw = _load_model(
        campaign_root / "campaign-lifecycle-v2.private.json",
        CampaignLifecycleV2,
        "campaign lifecycle",
    )
    if lifecycle.mode != "execution" or lifecycle.status != "completed":
        raise ModelCardBuildError("campaign lifecycle is not completed execution")
    if lifecycle.source_config_fingerprint != readiness.source_config_fingerprint:
        raise ModelCardBuildError("lifecycle/readiness config fingerprint mismatch")
    if lifecycle.active_track is not None or lifecycle.active_model is not None:
        raise ModelCardBuildError("completed lifecycle still declares active work")
    if lifecycle.active_run_index is not None:
        raise ModelCardBuildError("completed lifecycle still declares an active run")

    receipts: dict[Track, TrackCompletionV2] = {}
    for track in (Track.DIRECT, Track.MCP):
        receipt, _receipt_raw = _load_model(
            campaign_root / track.value / "track-completion-v2.private.json",
            TrackCompletionV2,
            f"{track.value} track completion",
        )
        if receipt.track is not track:
            raise ModelCardBuildError(f"{track.value} completion has the wrong track")
        if receipt.source_config_fingerprint != readiness.source_config_fingerprint:
            raise ModelCardBuildError(f"{track.value} config fingerprint mismatch")
        if receipt.campaign_valid is not True:
            raise ModelCardBuildError(f"{track.value} track completion is invalid")
        if receipt.result_count != receipt.expected_task_count_per_run * receipt.run_count:
            raise ModelCardBuildError(f"{track.value} track completion is incomplete")
        receipts[track] = receipt

    lifecycle_tracks = {item.track: item.receipt_fingerprint for item in lifecycle.completed_tracks}
    if set(lifecycle_tracks) != {Track.DIRECT, Track.MCP}:
        raise ModelCardBuildError("lifecycle does not complete exactly Direct and MCP")
    for track, receipt in receipts.items():
        if lifecycle_tracks[track] != receipt.receipt_fingerprint:
            raise ModelCardBuildError(f"{track.value} lifecycle receipt mismatch")
    if lifecycle.checkpointed_results != sum(receipt.result_count for receipt in receipts.values()):
        raise ModelCardBuildError("lifecycle checkpoint accounting is incomplete")

    reports = {
        track: _load_track_reports(campaign_root, track, receipt, readiness)
        for track, receipt in receipts.items()
    }
    provider, selected_model, run_indexes = _select_model(reports, model)
    provider = _public_label(provider, "provider")
    selected_model = _public_label(selected_model, "model")
    display = _public_label(display_name or selected_model, "display name")

    if not any(
        item.provider == provider and item.model == selected_model for item in readiness.models
    ):
        raise ModelCardBuildError("selected model is absent from readiness evidence")

    selected: dict[Track, tuple[LoadedRun, ...]] = {
        track: tuple(runs[(provider, selected_model, index)] for index in run_indexes)
        for track, runs in reports.items()
    }
    products = {
        loaded.report.report.product for track_runs in selected.values() for loaded in track_runs
    }
    if len(products) != 1:
        raise ModelCardBuildError("Direct and MCP product evidence does not match")
    product = _public_label(next(iter(products)), "product")

    tracks = {
        track.value: _aggregate(selected[track], receipts[track])
        for track in (Track.DIRECT, Track.MCP)
    }
    operational_metrics = _aggregate_operational_summaries(
        OperationalSummary.model_validate(
            {
                key: value
                for key, value in tracks[track.value]["operational_metrics"].items()
                if key in OperationalSummary.model_fields
            }
        )
        for track in (Track.DIRECT, Track.MCP)
    )
    card: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "claim_boundary": "completed_graph_gated_v30_direct_mcp_campaign",
        "product": product,
        "provider": provider,
        "model": selected_model,
        "display_name": display,
        "runner_version": readiness.runner_version,
        "mcp_server_revision": readiness.mcp_server_revision,
        "source_config_fingerprint": readiness.source_config_fingerprint,
        "readiness_fingerprint": readiness.readiness_fingerprint,
        "graph_fingerprint": readiness.graph_fingerprint,
        "campaign_valid": True,
        "operational_metrics": operational_metrics.model_dump(),
        "tracks": tracks,
        "warnings": [
            "V30 scoring over the development schedule; not the future official 100/100 suite.",
            "Direct and MCP are separate surfaces; no combined score is valid.",
            "This card contains no prompts, responses, tool bodies, credentials, or local paths.",
        ],
    }
    svg = _svg_bytes(card)
    card["generated_assets"] = {SVG_NAME: _sha256(svg)}
    fingerprint_payload = dict(card)
    card["evidence_fingerprint"] = _sha256(
        (json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    )
    json_content = (json.dumps(card, indent=2, sort_keys=True) + "\n").encode()

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / SVG_NAME).write_bytes(svg)
    (output_dir / JSON_NAME).write_bytes(json_content)
    return card


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model")
    parser.add_argument("--display-name")
    args = parser.parse_args()
    build_model_card(
        args.campaign_root,
        args.output_dir,
        model=args.model,
        display_name=args.display_name,
    )


if __name__ == "__main__":
    main()
