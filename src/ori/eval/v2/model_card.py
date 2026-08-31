"""Build a public-safe V29 model card from a completed V2 campaign."""

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

from pydantic import BaseModel, ValidationError

from ori.eval.v2.campaign_runner import (
    CampaignLifecycleV2,
    CampaignReadinessV2,
    ModelPublicReportV2,
    TrackCompletionV2,
)
from ori.eval.v2.schema import ExecutionClass, Track
from ori.eval.v2.scoring import CampaignSummary

SCHEMA_VERSION = "ori-v29-model-card-v1"
JSON_NAME = "v29-model-card.json"
SVG_NAME = "v29-model-card.svg"

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
    sha256: str

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


def _readiness_track(
    readiness: CampaignReadinessV2,
    track: Track,
):
    matches = [item for item in readiness.tracks if item.track is track]
    if len(matches) != 1:
        raise ModelCardBuildError(
            f"readiness must contain exactly one {track.value} track receipt"
        )
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
        reasoning_accuracy=(
            correct / reasoning_denominator if reasoning_denominator else None
        ),
        effective_accuracy=(correct / len(rows) if rows else None),
        campaign_valid=not invalid_reasons,
        invalid_reasons=tuple(invalid_reasons),
    )
    if body.summary != expected:
        raise ModelCardBuildError(
            f"{track.value} public summary disagrees with its redacted rows"
        )


def _load_track_reports(
    campaign_root: Path,
    track: Track,
    receipt: TrackCompletionV2,
    readiness: CampaignReadinessV2,
) -> dict[tuple[str, str, int], LoadedRun]:
    track_readiness = _readiness_track(readiness, track)
    expected_runs = {
        (run.provider, run.model, run.run_index): run for run in receipt.runs
    }
    paths = sorted(
        (campaign_root / track.value).glob("*/run-*/public-report-v2.json")
    )
    loaded: dict[tuple[str, str, int], LoadedRun] = {}
    for path in paths:
        report, raw = _load_model(path, ModelPublicReportV2, f"{track.value} public report")
        key = (
            report.run_identity.provider,
            report.run_identity.model,
            report.run_identity.run_index,
        )
        if key in loaded:
            raise ModelCardBuildError(f"duplicate {track.value} public run identity")
        loaded[key] = LoadedRun(report=report, sha256=_sha256(raw))

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
        if body.catalog_fingerprint == "":
            raise ModelCardBuildError(f"{track.value} catalog fingerprint is empty")
        if (
            body.capability_profile_fingerprint
            != track_readiness.capability_profile_fingerprint
        ):
            raise ModelCardBuildError(f"{track.value} capability fingerprint mismatch")
        if (
            report.candidate_release_fingerprint
            != receipt.candidate_release_fingerprint
            or report.candidate_release_fingerprint
            != track_readiness.candidate_release_fingerprint
        ):
            raise ModelCardBuildError(f"{track.value} candidate release mismatch")
        if (
            report.live_certification_fingerprint
            != receipt.live_certification_fingerprint
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
    catalog_fingerprints = {
        item.report.report.catalog_fingerprint for item in runs
    }
    if len(catalog_fingerprints) != 1:
        raise ModelCardBuildError(
            f"{receipt.track.value} public reports disagree on catalog fingerprint"
        )
    summaries = tuple(item.report.report.summary for item in runs)
    scheduled = sum(summary.scheduled for summary in summaries)
    correct = sum(summary.correct for summary in summaries)
    payload: dict[str, Any] = {
        "runs": len(runs),
        "scheduled": scheduled,
        "completed": sum(summary.completed for summary in summaries),
        "correct": correct,
        "incorrect": sum(summary.incorrect for summary in summaries),
        "effective_accuracy": correct / scheduled,
        "model_failures": sum(summary.model_failures for summary in summaries),
        "proof_failures": sum(summary.proof_failures for summary in summaries),
        "infrastructure_failures": sum(
            summary.infrastructure_failures for summary in summaries
        ),
        "harness_failures": sum(summary.harness_failures for summary in summaries),
        "unexecuted": sum(summary.unexecuted for summary in summaries),
        "campaign_valid": True,
        "catalog_fingerprint": next(iter(catalog_fingerprints)),
        "candidate_release_fingerprint": receipt.candidate_release_fingerprint,
        "live_certification_fingerprint": receipt.live_certification_fingerprint,
        "graph_verification_before_fingerprint": receipt.graph_verification_before_fingerprint,
        "graph_verification_after_fingerprint": receipt.graph_verification_after_fingerprint,
        "track_completion_fingerprint": receipt.receipt_fingerprint,
        "public_report_fingerprints": sorted(
            item.report.artifact_fingerprint for item in runs
        ),
        "public_report_sha256": sorted(item.sha256 for item in runs),
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
        f'<title id="title">ORI V29 model card for {model}</title>',
        f'<desc id="description">Separate graph-gated Direct and MCP results for {model}. No combined score is reported.</desc>',
        '<rect width="1280" height="720" fill="#0b1020"/>',
        '<style>text{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;fill:#f7f9fc}.muted{fill:#aeb8cb}.accent{fill:#66d9ef}.panel{fill:#151d33;stroke:#33415f;stroke-width:2}.good{fill:#70e1a1}.label{fill:#aeb8cb;font-size:16px}.value{fill:#f7f9fc;font-size:18px;font-weight:600}</style>',
        '<text x="54" y="48" font-size="15" font-weight="700" class="accent">OFFENSIVE REASONING INDEX · V29 DEVELOPMENT BOUNDARY</text>',
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
        lines.extend(
            [
                f'<rect x="{x}" y="174" width="576" height="430" rx="18" class="panel"/>',
                f'<text x="{x + 28}" y="216" font-size="20" font-weight="700" class="accent">{title}</text>',
                f'<text x="{x + 28}" y="286" font-size="58" font-weight="700">{_percent(item["effective_accuracy"])}</text>',
                f'<text x="{x + 30}" y="316" font-size="16" class="muted">effective accuracy · {item["correct"]}/{item["scheduled"]} correct · {item["runs"]} run(s)</text>',
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
            row_y = 365 + index * 44
            lines.append(
                f'<text x="{x + 30}" y="{row_y}" class="label">{label}</text>'
            )
            lines.append(
                f'<text x="{x + 546}" y="{row_y}" text-anchor="end" class="value">{value}</text>'
            )
        lines.append(
            f'<text x="{x + 30}" y="578" font-size="14" class="good">Graph-gated track completion verified</text>'
        )
    lines.extend(
        [
            '<text x="54" y="650" font-size="18" font-weight="700">Direct and MCP are separate evaluation surfaces. No combined score is reported.</text>',
            '<text x="54" y="680" font-size="15" class="muted">Public-safe summary generated from validated lifecycle, readiness, completion receipts, and public reports only.</text>',
            '</svg>',
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

    lifecycle_tracks = {
        item.track: item.receipt_fingerprint for item in lifecycle.completed_tracks
    }
    if set(lifecycle_tracks) != {Track.DIRECT, Track.MCP}:
        raise ModelCardBuildError("lifecycle does not complete exactly Direct and MCP")
    for track, receipt in receipts.items():
        if lifecycle_tracks[track] != receipt.receipt_fingerprint:
            raise ModelCardBuildError(f"{track.value} lifecycle receipt mismatch")
    if lifecycle.checkpointed_results != sum(
        receipt.result_count for receipt in receipts.values()
    ):
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
        item.provider == provider and item.model == selected_model
        for item in readiness.models
    ):
        raise ModelCardBuildError("selected model is absent from readiness evidence")

    selected: dict[Track, tuple[LoadedRun, ...]] = {
        track: tuple(
            runs[(provider, selected_model, index)] for index in run_indexes
        )
        for track, runs in reports.items()
    }
    products = {
        loaded.report.report.product
        for track_runs in selected.values()
        for loaded in track_runs
    }
    if len(products) != 1:
        raise ModelCardBuildError("Direct and MCP product evidence does not match")
    product = _public_label(next(iter(products)), "product")

    tracks = {
        track.value: _aggregate(selected[track], receipts[track])
        for track in (Track.DIRECT, Track.MCP)
    }
    card: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "claim_boundary": "completed_graph_gated_v29_direct_mcp_campaign",
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
        "tracks": tracks,
        "warnings": [
            "V29 development evidence; not the future official 100/100 suite.",
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
