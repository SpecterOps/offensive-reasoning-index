"""Installed command coverage for the durable V2 campaign supervisor."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from click.testing import CliRunner

from ori.cli import main
from ori.eval.v2.campaign_supervisor import (
    CampaignSupervisorError,
    SupervisorCampaignBindingV1,
    SupervisorDecision,
)


def _inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    config = tmp_path / "campaign.yaml"
    config.write_text("not inspected by the fake supervisor\n")
    state = tmp_path / "supervisor" / "state.private.json"
    executable = tmp_path / "ori"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    return config, state, executable


def _arguments(config: Path, state: Path, executable: Path) -> list[str]:
    return [
        "supervise-v2",
        "--config",
        str(config),
        "--state",
        str(state),
        "--ori-executable",
        str(executable),
        "--token-ceiling",
        "100",
        "--max-restarts",
        "1",
    ]


def test_supervise_v2_delegates_to_the_existing_supervisor_and_emits_json(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config, state, executable = _inputs(tmp_path)
    captured: dict[str, object] = {}

    def fake_supervise(**kwargs: object) -> SupervisorDecision:
        captured.update(kwargs)
        return SupervisorDecision(
            action="complete",
            reason="campaign is complete",
            campaign=SupervisorCampaignBindingV1(purpose="official"),
        )

    monkeypatch.setattr("ori.eval.v2.campaign_supervisor.supervise_v2_campaign", fake_supervise)

    result = CliRunner().invoke(main, [*_arguments(config, state, executable), "--json"])

    assert result.exit_code == 0, result.output
    assert captured == {
        "config_path": config,
        "state_path": state,
        "token_ceiling": 100,
        "max_restarts": 1,
        "execute_approved": False,
        "poll_interval_seconds": 15.0,
        "ori_executable": str(executable),
    }
    assert json.loads(result.output) == {
        "schema_version": "ori-v2-supervisor-result-v1",
        "outcome": "decision",
        "execute_approved": False,
        "decision": {"action": "complete", "reason": "campaign is complete"},
        "campaign": {
            "purpose": "official",
            "ranking_eligible": True,
            "canary": None,
        },
        "exit_code": 0,
    }


def test_supervise_v2_retains_the_explicit_execution_approval_boundary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config, state, executable = _inputs(tmp_path)

    def fake_supervise(**_kwargs: object) -> SupervisorDecision:
        return SupervisorDecision(
            action="await_execution_approval",
            reason="execution requires explicit approval",
        )

    monkeypatch.setattr("ori.eval.v2.campaign_supervisor.supervise_v2_campaign", fake_supervise)

    result = CliRunner().invoke(main, [*_arguments(config, state, executable), "--json"])

    assert result.exit_code == 2
    assert json.loads(result.output) == {
        "schema_version": "ori-v2-supervisor-result-v1",
        "outcome": "decision",
        "execute_approved": False,
        "decision": {
            "action": "await_execution_approval",
            "reason": "execution requires explicit approval",
        },
        "exit_code": 2,
    }


def test_supervise_v2_json_exposes_diagnostic_canary_identity(tmp_path: Path, monkeypatch) -> None:
    config, state, executable = _inputs(tmp_path)
    selection_fingerprint = "a" * 64

    def fake_supervise(**_kwargs: object) -> SupervisorDecision:
        return SupervisorDecision(
            action="monitor",
            reason="readiness child is running",
            campaign=SupervisorCampaignBindingV1(
                purpose="diagnostic_canary",
                canary_suite="native-five-kind-v1",
                canary_selection_fingerprint=selection_fingerprint,
            ),
        )

    monkeypatch.setattr("ori.eval.v2.campaign_supervisor.supervise_v2_campaign", fake_supervise)

    result = CliRunner().invoke(main, [*_arguments(config, state, executable), "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["campaign"] == {
        "purpose": "diagnostic_canary",
        "ranking_eligible": False,
        "canary": {
            "suite": "native-five-kind-v1",
            "selection_fingerprint": selection_fingerprint,
        },
    }


def test_supervise_v2_rejects_relative_inputs_before_delegating(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config, _state, executable = _inputs(tmp_path)
    calls: list[object] = []

    def forbidden_supervise(**_kwargs: object) -> SupervisorDecision:
        calls.append("called")
        raise AssertionError("relative inputs must fail before supervisor delegation")

    monkeypatch.setattr(
        "ori.eval.v2.campaign_supervisor.supervise_v2_campaign", forbidden_supervise
    )
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(
        main,
        [
            *_arguments(config, Path("relative-state.json"), executable),
            "--json",
        ],
    )

    assert result.exit_code == 1
    assert json.loads(result.output) == {
        "schema_version": "ori-v2-supervisor-result-v1",
        "outcome": "error",
        "error": {
            "code": "SUPERVISOR_FAILED",
            "message": "supervisor stopped; inspect local supervisor state and campaign status",
        },
        "exit_code": 1,
    }
    assert calls == []


def test_supervise_v2_json_errors_are_machine_readable(tmp_path: Path, monkeypatch) -> None:
    config, state, executable = _inputs(tmp_path)

    raw_detail = "/private/runner/token=must-not-appear"

    def fake_supervise(**_kwargs: object) -> SupervisorDecision:
        raise CampaignSupervisorError(raw_detail)

    monkeypatch.setattr("ori.eval.v2.campaign_supervisor.supervise_v2_campaign", fake_supervise)

    result = CliRunner().invoke(main, [*_arguments(config, state, executable), "--json"])

    assert result.exit_code == 1
    assert json.loads(result.output) == {
        "schema_version": "ori-v2-supervisor-result-v1",
        "outcome": "error",
        "error": {
            "code": "SUPERVISOR_FAILED",
            "message": "supervisor stopped; inspect local supervisor state and campaign status",
        },
        "exit_code": 1,
    }
    assert raw_detail not in result.output


def test_run_v2_rejects_diagnostic_purpose_before_dispatch(tmp_path: Path, monkeypatch) -> None:
    config = tmp_path / "canary.yaml"
    config.write_text("canary fixture\n")
    calls: list[str] = []

    async def forbidden_run(*_args: object, **_kwargs: object) -> object:
        calls.append("called")
        raise AssertionError("diagnostic canaries must not dispatch through run-v2")

    monkeypatch.setattr(
        "ori.eval.v2.campaign_config.load_v2_campaign_config",
        lambda _: SimpleNamespace(config=SimpleNamespace(purpose="diagnostic_canary")),
    )
    monkeypatch.setattr("ori.eval.v2.campaign_runner.run_v2_campaign", forbidden_run)

    result = CliRunner().invoke(main, ["run-v2", "--config", str(config), "--execute"])

    assert result.exit_code == 1
    assert "requires purpose: official" in result.output
    assert calls == []


def test_run_v2_json_error_is_generic_for_a_diagnostic_config(tmp_path: Path, monkeypatch) -> None:
    config = tmp_path / "canary.yaml"
    config.write_text("canary fixture\n")

    monkeypatch.setattr(
        "ori.eval.v2.campaign_config.load_v2_campaign_config",
        lambda _: SimpleNamespace(config=SimpleNamespace(purpose="diagnostic_canary")),
    )

    result = CliRunner().invoke(main, ["run-v2", "--config", str(config), "--json"])

    assert result.exit_code == 1
    assert json.loads(result.output) == {
        "schema_version": "ori-v2-run-result-v1",
        "outcome": "error",
        "error": {
            "code": "V2_RUN_FAILED",
            "message": "v2 campaign command failed; inspect local operator logs",
        },
        "exit_code": 1,
    }
