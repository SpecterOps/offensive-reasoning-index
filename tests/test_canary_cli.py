"""Public command adapters for typed diagnostic canaries and exports."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from click.testing import CliRunner

from ori.cli import main
from ori.eval.v2.schema import Track


def test_prepare_canary_v2_emits_only_the_requested_json_contract(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = tmp_path / "official.yaml"
    config.write_text("official fixture\n")
    output_dir = tmp_path / "canary"
    captured: dict[str, object] = {}

    def fake_prepare(**kwargs: object) -> dict[str, Path]:
        captured.update(kwargs)
        return {
            "selection": output_dir / "diagnostic-selection-v1.json",
            "config": output_dir / "canary-config-v2.yaml",
        }

    monkeypatch.setattr("ori.eval.v2.cli_support.prepare_canary_v2_files", fake_prepare)

    result = CliRunner().invoke(
        main,
        [
            "prepare-canary-v2",
            "--config",
            str(config),
            "--suite",
            "native-five-kind-v1",
            "--output-dir",
            str(output_dir),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured == {
        "config_path": config,
        "suite": "native-five-kind-v1",
        "output_dir": output_dir,
    }
    assert json.loads(result.output) == {
        "schema_version": "ori-v2-canary-preparation-result-v1",
        "outcome": "prepared",
        "purpose": "diagnostic_canary",
        "ranking_eligible": False,
        "suite": "native-five-kind-v1",
        "artifacts": {
            "selection": str(output_dir / "diagnostic-selection-v1.json"),
            "config": str(output_dir / "canary-config-v2.yaml"),
        },
    }


def test_run_canary_v2_preflights_with_the_shared_runner_and_clean_json(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = tmp_path / "canary.yaml"
    config.write_text("{}\n")
    captured: dict[str, object] = {}
    resolved = SimpleNamespace(
        config=SimpleNamespace(purpose="diagnostic_canary"),
        output_dir=tmp_path / "campaign",
    )

    async def fake_run(config_path: Path, **kwargs: object) -> SimpleNamespace:
        captured["config_path"] = config_path
        captured.update(kwargs)
        return SimpleNamespace(
            graph_fingerprint="a" * 64,
            model_count=1,
            tracks=(
                SimpleNamespace(
                    track=Track.MCP,
                    task_count=5,
                    candidate_release_fingerprint="b" * 64,
                ),
            ),
        )

    monkeypatch.setattr("ori.eval.v2.campaign_config.load_v2_campaign_config", lambda _: resolved)
    monkeypatch.setattr("ori.eval.v2.campaign_runner.run_v2_campaign", fake_run)

    result = CliRunner().invoke(main, ["run-canary-v2", "--config", str(config), "--json"])

    assert result.exit_code == 0, result.output
    assert captured == {
        "config_path": config,
        "preflight_only": True,
        "progress": None,
    }
    assert json.loads(result.output) == {
        "schema_version": "ori-v2-canary-run-result-v1",
        "outcome": "readiness_passed",
        "purpose": "diagnostic_canary",
        "ranking_eligible": False,
        "execute": False,
        "readiness": {
            "graph_fingerprint": "a" * 64,
            "model_count": 1,
            "tracks": [
                {
                    "track": "mcp",
                    "task_count": 5,
                    "candidate_release_fingerprint": "b" * 64,
                }
            ],
        },
    }


def test_run_canary_v2_rejects_official_purpose_before_dispatch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = tmp_path / "official.yaml"
    config.write_text("{}\n")
    calls: list[str] = []
    resolved = SimpleNamespace(config=SimpleNamespace(purpose="official"))

    async def forbidden_run(*_args: object, **_kwargs: object) -> SimpleNamespace:
        calls.append("called")
        raise AssertionError("official campaigns must not dispatch through run-canary-v2")

    monkeypatch.setattr("ori.eval.v2.campaign_config.load_v2_campaign_config", lambda _: resolved)
    monkeypatch.setattr("ori.eval.v2.campaign_runner.run_v2_campaign", forbidden_run)

    result = CliRunner().invoke(
        main,
        ["run-canary-v2", "--config", str(config), "--execute"],
    )

    assert result.exit_code == 1
    assert "requires purpose: diagnostic_canary" in result.output
    assert calls == []


def test_export_campaign_v2_requires_explicit_public_acknowledgement(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = tmp_path / "canary.yaml"
    config.write_text("canary fixture\n")
    calls: list[str] = []

    def forbidden_export(**_kwargs: object) -> Path:
        calls.append("called")
        raise AssertionError("--public must be required")

    monkeypatch.setattr("ori.eval.v2.campaign_export.export_v2_campaign_public", forbidden_export)

    result = CliRunner().invoke(
        main,
        ["export-campaign-v2", "--config", str(config), "--output-dir", str(tmp_path / "public")],
    )

    assert result.exit_code == 2
    assert "requires --public" in result.output
    assert calls == []


def test_json_v2_command_failures_use_fixed_redacted_envelopes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = tmp_path / "canary.yaml"
    config.write_text("canary fixture\n")

    def fail_prepare(**_kwargs: object) -> dict[str, Path]:
        raise ValueError(f"sensitive local path: {tmp_path}")

    monkeypatch.setattr("ori.eval.v2.cli_support.prepare_canary_v2_files", fail_prepare)
    prepared = CliRunner().invoke(
        main,
        [
            "prepare-canary-v2",
            "--config",
            str(config),
            "--suite",
            "native-five-kind-v1",
            "--output-dir",
            str(tmp_path / "output"),
            "--json",
        ],
    )
    assert prepared.exit_code == 1
    assert json.loads(prepared.output) == {
        "error": {
            "code": "V2_CANARY_PREPARATION_FAILED",
            "message": "v2 campaign command failed; inspect local operator logs",
        },
        "exit_code": 1,
        "outcome": "error",
        "schema_version": "ori-v2-canary-preparation-result-v1",
    }
    assert str(tmp_path) not in prepared.output

    missing_acknowledgement = CliRunner().invoke(
        main,
        [
            "export-campaign-v2",
            "--config",
            str(config),
            "--output-dir",
            str(tmp_path / "output"),
            "--json",
        ],
    )
    assert missing_acknowledgement.exit_code == 1
    assert json.loads(missing_acknowledgement.output)["error"]["code"] == (
        "V2_CAMPAIGN_EXPORT_FAILED"
    )


def test_export_campaign_v2_labels_diagnostic_results_and_emits_json(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = tmp_path / "canary.yaml"
    config.write_text("canary fixture\n")
    output_dir = tmp_path / "public"
    destination = output_dir / "campaign-public-summary-v1.json"
    resolved = SimpleNamespace(config=SimpleNamespace(purpose="diagnostic_canary"))
    captured: dict[str, object] = {}

    def fake_export(**kwargs: object) -> Path:
        captured.update(kwargs)
        return destination

    monkeypatch.setattr("ori.eval.v2.campaign_config.load_v2_campaign_config", lambda _: resolved)
    monkeypatch.setattr("ori.eval.v2.campaign_export.export_v2_campaign_public", fake_export)

    result = CliRunner().invoke(
        main,
        [
            "export-campaign-v2",
            "--config",
            str(config),
            "--output-dir",
            str(output_dir),
            "--public",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured == {"config_path": config, "output_dir": output_dir}
    assert json.loads(result.output) == {
        "schema_version": "ori-v2-campaign-export-result-v1",
        "outcome": "exported",
        "purpose": "diagnostic_canary",
        "ranking_eligible": False,
        "public_export": str(destination),
    }
