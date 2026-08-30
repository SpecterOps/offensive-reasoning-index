from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.build_offensive_ai_con_assets import EvidenceBuildError, build_assets


def _report(model: str, run_index: int, correct: int = 2) -> dict[str, object]:
    scheduled = 2
    return {
        "run_identity": {
            "provider": "codex",
            "model": model,
            "run_index": run_index,
            "target_fingerprint": str(run_index) * 64,
            "tool_loop": None,
        },
        "artifact_fingerprint": f"{model}-{run_index}",
        "report": {
            "rows": [{"task_id": "one"}, {"task_id": "two"}],
            "summary": {
                "campaign_valid": True,
                "completed": scheduled,
                "correct": correct,
                "effective_accuracy": correct / scheduled,
                "harness_failures": 0,
                "infrastructure_failures": 0,
                "model_failures": 0,
                "scheduled": scheduled,
                "unexecuted": 0,
            },
        },
    }


def _bundle(tmp_path: Path) -> Path:
    root = tmp_path / "frozen"
    manifest = {
        "campaign_id": "historical-test",
        "runner_head": "a" * 40,
        "runner_clean": True,
        "required_v29_base": "a" * 40,
        "models": ["model-a", "model-b"],
        "runs_per_model": 2,
    }
    manifest_path = root / "bundle" / "campaign-manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps(manifest))
    for model in manifest["models"]:
        for run_index in (1, 2):
            path = (
                root
                / "private-copy"
                / "campaign"
                / "runtime"
                / "direct"
                / model
                / f"run-{run_index:03d}"
                / "public-report-v2.json"
            )
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(_report(model, run_index)))
    return root


def test_build_assets_is_deterministic_and_public_safe(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    first = tmp_path / "first"
    second = tmp_path / "second"

    evidence = build_assets(bundle, first)
    build_assets(bundle, second)

    assert evidence["scheduled_samples"] == 8
    assert len(evidence["source_reports"]) == 4
    assert {path.name for path in first.iterdir()} == {
        "historical-v29-direct-evidence.json",
        "historical-v29-direct-results.csv",
        "historical-v29-direct-results.svg",
    }
    for path in first.iterdir():
        assert path.read_bytes() == (second / path.name).read_bytes()
        text = path.read_text()
        assert str(tmp_path) not in text
        assert "private-copy" not in text


def test_build_assets_counts_typed_model_failures_in_effective_denominator(
    tmp_path: Path,
) -> None:
    bundle = _bundle(tmp_path)
    path = next(bundle.glob("*/campaign/runtime/direct/*/run-*/public-report-v2.json"))
    payload = json.loads(path.read_text())
    summary = payload["report"]["summary"]
    summary.update(
        {
            "completed": 1,
            "correct": 1,
            "effective_accuracy": 0.5,
            "model_failures": 1,
        }
    )
    path.write_text(json.dumps(payload))

    evidence = build_assets(bundle, tmp_path / "output")

    assert evidence["scheduled_samples"] == 8


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("campaign_valid", False, "invalid Direct campaign"),
        ("infrastructure_failures", 1, "non-zero infrastructure_failures"),
        ("harness_failures", 1, "non-zero harness_failures"),
        ("unexecuted", 1, "non-zero unexecuted"),
    ),
)
def test_build_assets_rejects_invalid_campaign_evidence(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    bundle = _bundle(tmp_path)
    path = next(bundle.glob("*/campaign/runtime/direct/*/run-*/public-report-v2.json"))
    payload = json.loads(path.read_text())
    payload["report"]["summary"][field] = value
    path.write_text(json.dumps(payload))

    with pytest.raises(EvidenceBuildError, match=message):
        build_assets(bundle, tmp_path / "output")


def test_build_assets_requires_exact_report_schedule(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    extra = (
        next(bundle.glob("*/campaign/runtime/direct"))
        / "model-a"
        / "run-003"
        / "public-report-v2.json"
    )
    extra.parent.mkdir(parents=True)
    extra.write_text(json.dumps(_report("model-a", 3)))

    with pytest.raises(EvidenceBuildError, match="exactly match campaign schedule"):
        build_assets(bundle, tmp_path / "output")
