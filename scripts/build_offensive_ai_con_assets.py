#!/usr/bin/env python3
"""Build public-safe conference evidence from frozen V29 Direct reports."""

# SVG elements remain one line each so the generated vector asset has stable,
# reviewable markup rather than formatting-dependent whitespace.
# ruff: noqa: E501

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import statistics
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "ori-offensive-ai-con-direct-evidence-v1"
CSV_NAME = "historical-v29-direct-results.csv"
SVG_NAME = "historical-v29-direct-results.svg"
EVIDENCE_NAME = "historical-v29-direct-evidence.json"

DISPLAY_NAMES = {
    "gpt-5.5": "GPT-5.5",
    "gpt-5.6-luna": "GPT-5.6 Luna",
    "gpt-5.6-sol": "GPT-5.6 Sol",
    "gpt-5.6-terra": "GPT-5.6 Terra",
    "gpt-daybreak-blue-latest": "Daybreak Blue",
    "gpt-daybreak-red-latest": "Daybreak Red",
}


class EvidenceBuildError(ValueError):
    """Raised when the frozen campaign cannot support the public chart."""


@dataclass(frozen=True)
class RunEvidence:
    model: str
    run_index: int
    correct: int
    scheduled: int
    effective_accuracy: float
    artifact_fingerprint: str
    report_sha256: str
    logical_path: str


@dataclass(frozen=True)
class ModelEvidence:
    model: str
    runs: tuple[RunEvidence, ...]

    @property
    def correct(self) -> int:
        return sum(run.correct for run in self.runs)

    @property
    def scheduled(self) -> int:
        return sum(run.scheduled for run in self.runs)

    @property
    def pooled_accuracy(self) -> float:
        return self.correct / self.scheduled

    @property
    def run_accuracies(self) -> tuple[float, ...]:
        return tuple(run.effective_accuracy for run in self.runs)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _load_json(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceBuildError(f"cannot read {label}: {exc}") from exc
    if not isinstance(payload, dict):
        raise EvidenceBuildError(f"{label} must be a JSON object")
    return payload, raw


def _required_int(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise EvidenceBuildError(f"{label} must be a non-negative integer")
    return value


def _required_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise EvidenceBuildError(f"{label} must be a non-empty string")
    return value


def _direct_report_root(bundle_root: Path) -> Path:
    candidates = sorted(bundle_root.glob("*/campaign/runtime/direct"))
    if len(candidates) != 1:
        raise EvidenceBuildError(
            "expected exactly one frozen campaign/runtime/direct report root"
        )
    return candidates[0]


def _load_run(path: Path) -> RunEvidence:
    payload, raw = _load_json(path, "public Direct report")
    identity = payload.get("run_identity")
    report = payload.get("report")
    if not isinstance(identity, dict) or not isinstance(report, dict):
        raise EvidenceBuildError("public report is missing run identity or report")
    summary = report.get("summary")
    rows = report.get("rows")
    if not isinstance(summary, dict) or not isinstance(rows, list):
        raise EvidenceBuildError("public report is missing summary or rows")
    model = _required_string(identity.get("model"), "run model")
    run_index = _required_int(identity.get("run_index"), "run index")
    if run_index == 0:
        raise EvidenceBuildError("run index must be positive")
    if identity.get("provider") != "codex" or identity.get("tool_loop") is not None:
        raise EvidenceBuildError(f"unexpected Direct provider identity for {model}")

    scheduled = _required_int(summary.get("scheduled"), "scheduled count")
    completed = _required_int(summary.get("completed"), "completed count")
    correct = _required_int(summary.get("correct"), "correct count")
    model_failures = _required_int(summary.get("model_failures"), "model failures")
    if (
        scheduled == 0
        or completed + model_failures != scheduled
        or correct > completed
        or len(rows) != scheduled
    ):
        raise EvidenceBuildError(f"incomplete Direct accounting for {model}/run-{run_index:03d}")
    if summary.get("campaign_valid") is not True:
        raise EvidenceBuildError(f"invalid Direct campaign report for {model}/run-{run_index:03d}")
    for field in ("infrastructure_failures", "harness_failures", "unexecuted"):
        if _required_int(summary.get(field), field) != 0:
            raise EvidenceBuildError(
                f"{model}/run-{run_index:03d} has non-zero {field}"
            )
    accuracy = summary.get("effective_accuracy")
    if not isinstance(accuracy, (int, float)) or isinstance(accuracy, bool):
        raise EvidenceBuildError("effective accuracy must be numeric")
    accuracy = float(accuracy)
    if not math.isfinite(accuracy) or not math.isclose(
        accuracy,
        correct / scheduled,
        rel_tol=0,
        abs_tol=1e-12,
    ):
        raise EvidenceBuildError(f"effective accuracy disagrees with counts for {model}")

    artifact_fingerprint = _required_string(
        payload.get("artifact_fingerprint"), "artifact fingerprint"
    )
    return RunEvidence(
        model=model,
        run_index=run_index,
        correct=correct,
        scheduled=scheduled,
        effective_accuracy=accuracy,
        artifact_fingerprint=artifact_fingerprint,
        report_sha256=_sha256_bytes(raw),
        logical_path=f"direct/{model}/run-{run_index:03d}/public-report-v2.json",
    )


def _load_campaign(bundle_root: Path) -> tuple[dict[str, Any], tuple[ModelEvidence, ...]]:
    manifest_path = bundle_root / "bundle" / "campaign-manifest.json"
    manifest, _raw = _load_json(manifest_path, "campaign manifest")
    campaign_id = _required_string(manifest.get("campaign_id"), "campaign id")
    runner_head = _required_string(manifest.get("runner_head"), "runner commit")
    if manifest.get("runner_clean") is not True:
        raise EvidenceBuildError("historical runner worktree was not clean")
    if manifest.get("required_v29_base") != runner_head:
        raise EvidenceBuildError("historical runner does not match its required V29 base")
    models = manifest.get("models")
    runs_per_model = _required_int(manifest.get("runs_per_model"), "runs per model")
    if not isinstance(models, list) or not models or runs_per_model == 0:
        raise EvidenceBuildError("campaign manifest has no model/run schedule")
    if len(models) != len(set(models)) or not all(
        isinstance(model, str) and model for model in models
    ):
        raise EvidenceBuildError("campaign manifest model names are invalid or duplicated")

    report_root = _direct_report_root(bundle_root)
    expected_keys = {
        (model, run_index)
        for model in models
        for run_index in range(1, runs_per_model + 1)
    }
    actual_paths = sorted(report_root.glob("*/run-*/public-report-v2.json"))
    runs_by_key: dict[tuple[str, int], RunEvidence] = {}
    fingerprints: set[str] = set()
    for path in actual_paths:
        run = _load_run(path)
        key = (run.model, run.run_index)
        if key in runs_by_key:
            raise EvidenceBuildError("duplicate public report run identity")
        if run.artifact_fingerprint in fingerprints:
            raise EvidenceBuildError("duplicate public-report artifact fingerprint")
        runs_by_key[key] = run
        fingerprints.add(run.artifact_fingerprint)
    if set(runs_by_key) != expected_keys:
        raise EvidenceBuildError("public report set does not exactly match campaign schedule")
    model_evidence = [
        ModelEvidence(
            model=model,
            runs=tuple(
                runs_by_key[(model, run_index)]
                for run_index in range(1, runs_per_model + 1)
            ),
        )
        for model in models
    ]
    manifest["campaign_id"] = campaign_id
    manifest["runner_head"] = runner_head
    return manifest, tuple(model_evidence)


def _csv_bytes(models: tuple[ModelEvidence, ...]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(
        (
            "model",
            "display_name",
            "runs",
            "correct",
            "scheduled",
            "effective_accuracy",
            "run_min",
            "run_max",
            "run_population_stdev",
            *(f"run_{index}" for index in range(1, len(models[0].runs) + 1)),
        )
    )
    for item in sorted(models, key=lambda value: (-value.pooled_accuracy, value.model)):
        accuracies = item.run_accuracies
        writer.writerow(
            (
                item.model,
                DISPLAY_NAMES.get(item.model, item.model),
                len(item.runs),
                item.correct,
                item.scheduled,
                f"{item.pooled_accuracy:.12f}",
                f"{min(accuracies):.12f}",
                f"{max(accuracies):.12f}",
                f"{statistics.pstdev(accuracies):.12f}",
                *(f"{value:.12f}" for value in accuracies),
            )
        )
    return stream.getvalue().encode()


def _svg_bytes(models: tuple[ModelEvidence, ...], runner_head: str) -> bytes:
    ordered = sorted(models, key=lambda value: (-value.pooled_accuracy, value.model))
    width, height = 1280, 720
    left, right, top, bottom = 250, 90, 150, 170
    plot_width = width - left - right
    row_height = (height - top - bottom) / len(ordered)
    domain_min, domain_max = 0.75, 1.0

    def x(value: float) -> float:
        return left + ((value - domain_min) / (domain_max - domain_min)) * plot_width

    lines = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720" role="img" aria-labelledby="title description">',
        '<title id="title">Historical V29 Direct development campaign</title>',
        '<desc id="description">Pooled effective accuracy, five individual runs, and observed run ranges for six models on the historical V29 Direct development campaign.</desc>',
        '<rect width="1280" height="720" fill="#ffffff"/>',
        '<style>text{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;fill:#15171a} .muted{fill:#5e6670} .grid{stroke:#d9dde2;stroke-width:1} .range{stroke:#1769aa;stroke-width:3} .run{fill:#77a9d1} .pooled{fill:#0b4f82;stroke:#fff;stroke-width:2}</style>',
        '<text x="40" y="50" font-size="30" font-weight="600">Historical V29 Direct development campaign</text>',
        f'<text x="40" y="82" font-size="17" class="muted">Five 42-task runs per model · 210 samples per model · {sum(item.scheduled for item in ordered):,} samples total</text>',
        '<text x="40" y="112" font-size="16" class="muted">Development evidence only · not current Release 1 qualification · not an MCP ranking</text>',
    ]
    for tick in (0.75, 0.80, 0.85, 0.90, 0.95, 1.00):
        tick_x = x(tick)
        lines.append(
            f'<line x1="{tick_x:.1f}" y1="{top - 15}" x2="{tick_x:.1f}" y2="{height - bottom}" class="grid"/>'
        )
        lines.append(
            f'<text x="{tick_x:.1f}" y="{height - bottom + 30}" text-anchor="middle" font-size="15" class="muted">{tick * 100:.0f}%</text>'
        )

    offsets = (-10, -5, 0, 5, 10)
    for row, item in enumerate(ordered):
        y = top + row_height * (row + 0.5)
        values = item.run_accuracies
        label = escape(DISPLAY_NAMES.get(item.model, item.model))
        lines.append(
            f'<text x="{left - 18}" y="{y + 6:.1f}" text-anchor="end" font-size="18">{label}</text>'
        )
        lines.append(
            f'<line x1="{x(min(values)):.1f}" y1="{y:.1f}" x2="{x(max(values)):.1f}" y2="{y:.1f}" class="range"/>'
        )
        for index, value in enumerate(values):
            offset = offsets[index] if len(values) == len(offsets) else 0
            lines.append(
                f'<circle cx="{x(value):.1f}" cy="{y + offset:.1f}" r="5" class="run"><title>{label} run {index + 1}: {value * 100:.1f}%</title></circle>'
            )
        pooled_x = x(item.pooled_accuracy)
        lines.append(
            f'<circle cx="{pooled_x:.1f}" cy="{y:.1f}" r="9" class="pooled"><title>{label} pooled: {item.pooled_accuracy * 100:.1f}% ({item.correct}/{item.scheduled})</title></circle>'
        )
        anchor = "end" if item.pooled_accuracy >= 0.985 else "start"
        label_x = pooled_x - 14 if anchor == "end" else pooled_x + 14
        lines.append(
            f'<text x="{label_x:.1f}" y="{y + 6:.1f}" text-anchor="{anchor}" font-size="17" font-weight="600">{item.pooled_accuracy * 100:.1f}%</text>'
        )

    lines.extend(
        [
            f'<text x="{left + plot_width / 2:.1f}" y="{height - bottom + 70}" text-anchor="middle" font-size="17">Effective accuracy across all scheduled Direct samples</text>',
            '<text x="40" y="655" font-size="15" class="muted">Small dots: individual runs · line: observed range · large marker: pooled result</text>',
            '<text x="40" y="680" font-size="15" class="muted">All runs: zero infrastructure, harness, and unexecuted outcomes. Top-four repeat ranges overlap.</text>',
            f'<text x="1240" y="680" text-anchor="end" font-size="13" class="muted">Runner {escape(runner_head[:12])}</text>',
            "</svg>",
        ]
    )
    return ("\n".join(lines) + "\n").encode()


def build_assets(bundle_root: Path, output_dir: Path) -> dict[str, Any]:
    manifest, models = _load_campaign(bundle_root.resolve())
    csv_content = _csv_bytes(models)
    svg_content = _svg_bytes(models, manifest["runner_head"])
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / CSV_NAME).write_bytes(csv_content)
    (output_dir / SVG_NAME).write_bytes(svg_content)

    ordered = sorted(models, key=lambda value: (-value.pooled_accuracy, value.model))
    reports = [run for item in sorted(models, key=lambda value: value.model) for run in item.runs]
    evidence: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "claim_boundary": "historical_v29_direct_development_campaign",
        "campaign_id": manifest["campaign_id"],
        "runner_commit": manifest["runner_head"],
        "runner_clean": True,
        "track": "direct",
        "runs_per_model": len(ordered[0].runs),
        "model_count": len(ordered),
        "scheduled_samples": sum(item.scheduled for item in ordered),
        "infrastructure_failures": 0,
        "harness_failures": 0,
        "unexecuted": 0,
        "models": [
            {
                "model": item.model,
                "display_name": DISPLAY_NAMES.get(item.model, item.model),
                "correct": item.correct,
                "scheduled": item.scheduled,
                "effective_accuracy": item.pooled_accuracy,
                "run_min": min(item.run_accuracies),
                "run_max": max(item.run_accuracies),
                "run_population_stdev": statistics.pstdev(item.run_accuracies),
            }
            for item in ordered
        ],
        "source_reports": [
            {
                "logical_path": run.logical_path,
                "sha256": run.report_sha256,
                "artifact_fingerprint": run.artifact_fingerprint,
            }
            for run in reports
        ],
        "generated_assets": {
            CSV_NAME: _sha256_bytes(csv_content),
            SVG_NAME: _sha256_bytes(svg_content),
        },
        "warnings": [
            "Historical development evidence; not current Release 1 qualification.",
            "Direct and MCP are separate tracks; this evidence contains no MCP scores.",
            "Top-four repeat ranges overlap; do not infer a strong total ordering.",
        ],
    }
    evidence_content = (json.dumps(evidence, indent=2, sort_keys=True) + "\n").encode()
    (output_dir / EVIDENCE_NAME).write_bytes(evidence_content)
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        evidence = build_assets(args.bundle_root, args.output_dir)
    except EvidenceBuildError as exc:
        parser.error(str(exc))
    print(
        "OFFENSIVE AI CON DIRECT ASSETS: PASS\n"
        f"  campaign: {evidence['campaign_id']}\n"
        f"  runner: {evidence['runner_commit']}\n"
        f"  reports: {len(evidence['source_reports'])}\n"
        f"  scheduled samples: {evidence['scheduled_samples']}\n"
        f"  output: {args.output_dir.resolve()}"
    )


if __name__ == "__main__":
    main()
