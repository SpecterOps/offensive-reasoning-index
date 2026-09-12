from __future__ import annotations

import builtins
import inspect
import os
import signal
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from ori.eval.v2.campaign_status import CAMPAIGN_STATUS_SCHEMA_VERSION
from ori.eval.v2.campaign_supervisor import (
    CampaignSupervisorError,
    SupervisorAlreadyRunningError,
    SupervisorStateStore,
    V2CampaignSupervisor,
    decide_supervisor_action,
)
from ori.eval.v2.diagnostic_selection import DiagnosticSelectionReceiptV1, DiagnosticTaskIdentity
from ori.eval.v2.fingerprint import canonical_sha256
from ori.eval.v2.schema import Track


def _config(tmp_path: Path) -> Path:
    for name in (
        "manifest.json",
        "archive.zip",
        "direct-public.json",
        "direct-private.json",
        "direct-candidates.json",
        "direct-live.json",
    ):
        (tmp_path / name).touch()
    (tmp_path / "mcp").mkdir(exist_ok=True)
    payload = {
        "version": 2,
        "protocol": "ori-eval-protocol-v2",
        "source": {"manifest": "manifest.json", "archive": "archive.zip"},
        "tracks": {
            "direct": {
                "public": "direct-public.json",
                "oracles": "direct-private.json",
                "candidates": "direct-candidates.json",
                "live_certification": "direct-live.json",
            }
        },
        "modes": ["direct"],
        "output_dir": "campaign",
        "defaults": {
            "concurrency": 1,
            "runs_per_model": 1,
            "mcp": {
                "mcp_dir": "mcp",
                "resource_mode": "off",
                "tool_loop": "native-openai-compatible",
            },
        },
        "models": [{"name": "test", "provider": "codex", "model": "gpt-test"}],
    }
    path = tmp_path / "models-v2.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False))
    return path


def _fingerprint(value: int) -> str:
    return f"{value:064x}"


def _write_diagnostic_selection(path: Path, *, seed: int) -> None:
    entries = tuple(
        DiagnosticTaskIdentity(
            task_id=f"diagnostic-{kind}",
            recipe_id=f"recipe-{kind}",
            variant_id="control",
            claim_kind=kind,
            task_fingerprint=_fingerprint(index + seed),
            public_semantic_fingerprint=_fingerprint(index + seed + 100),
        )
        for index, kind in enumerate(("set", "count", "route", "decision", "absence"), start=1)
    )
    payload = {
        "schema_version": "ori-diagnostic-selection-v1",
        "purpose": "diagnostic_canary",
        "ranking_eligible": False,
        "suite": "native-five-kind-v1",
        "selector_version": "ori-oaic-diagnostic-selector-v1",
        "product": "oaic-2026-v1",
        "seed": seed,
        "track": Track.MCP,
        "source_manifest_fingerprint": _fingerprint(20 + seed),
        "source_archive_sha256": _fingerprint(30 + seed),
        "graph_fingerprint": _fingerprint(40 + seed),
        "compiler_fingerprint": _fingerprint(50 + seed),
        "public_artifact_fingerprint": _fingerprint(60 + seed),
        "candidate_release_fingerprint": _fingerprint(70 + seed),
        "metadata_fingerprint": _fingerprint(80 + seed),
        "paired_release_fingerprint": _fingerprint(90 + seed),
        "original_mcp_selection_fingerprint": _fingerprint(100 + seed),
        "entries": entries,
    }
    receipt = DiagnosticSelectionReceiptV1.model_validate(
        {**payload, "selection_fingerprint": canonical_sha256(payload)}
    )
    path.write_text(receipt.model_dump_json(indent=2) + "\n")


def _diagnostic_config(tmp_path: Path) -> Path:
    for name in (
        "manifest.json",
        "archive.zip",
        "mcp-public.json",
        "mcp-private.json",
        "mcp-candidates.json",
        "mcp-live.json",
        "mcp-metadata.json",
        "mcp-selection.json",
        "selected-release.json",
    ):
        (tmp_path / name).touch()
    (tmp_path / "mcp").mkdir(exist_ok=True)
    selection = tmp_path / "diagnostic-selection.json"
    _write_diagnostic_selection(selection, seed=1)
    payload = {
        "version": 2,
        "protocol": "ori-eval-protocol-v2",
        "source": {"manifest": "manifest.json", "archive": "archive.zip"},
        "tracks": {
            "mcp": {
                "public": "mcp-public.json",
                "oracles": "mcp-private.json",
                "candidates": "mcp-candidates.json",
                "live_certification": "mcp-live.json",
                "release_metadata": "mcp-metadata.json",
                "selection": "mcp-selection.json",
            }
        },
        "modes": ["mcp"],
        "output_dir": "campaign",
        "defaults": {
            "concurrency": 1,
            "runs_per_model": 1,
            "mcp": {
                "mcp_dir": "mcp",
                "resource_mode": "off",
                "tool_loop": "native-openai-compatible",
            },
        },
        "models": [{"name": "test", "provider": "codex", "model": "gpt-test"}],
        "purpose": "diagnostic_canary",
        "selected_release": "selected-release.json",
        "canary_selection": selection.name,
    }
    path = tmp_path / "canary-v2.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False))
    return path


def _status(
    observed: str,
    *,
    mode: str | None = None,
    next_action: str | None = None,
    resume_allowed: bool | None = None,
    tokens: int = 0,
    valid: bool = True,
) -> Any:
    defaults = {
        "not_started": ("run_readiness", False),
        "readiness_complete": ("execute_campaign", True),
        "running": ("monitor", False),
        "stale_running": (
            "resume_campaign" if mode == "execution" else "run_readiness",
            True,
        ),
        "interrupted": (
            "resume_campaign" if mode == "execution" else "run_readiness",
            True,
        ),
        "failed": ("investigate_failure", False),
        "completed": (
            "campaign_complete" if valid else "campaign_complete_invalid",
            False,
        ),
    }
    default_action, default_resume = defaults[observed]
    return SimpleNamespace(
        schema_version=CAMPAIGN_STATUS_SCHEMA_VERSION,
        source_config_fingerprint="a" * 64,
        observed_state=observed,
        mode=mode,
        next_action=next_action or default_action,
        resume_allowed=default_resume if resume_allowed is None else resume_allowed,
        progress=SimpleNamespace(total_tokens=tokens),
        tracks=(SimpleNamespace(completion_present=True, campaign_valid=valid),),
    )


class _Child:
    pid = 123

    def __init__(self) -> None:
        self.returncode: int | None = None
        self.terminated = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def send_signal(self, signum: int) -> None:
        self.terminated = True
        self.returncode = -signum

    def kill(self) -> None:
        self.send_signal(9)


class _Inspector:
    def __init__(self, *statuses: Any):
        self.statuses = list(statuses)
        self.calls = 0

    def __call__(self, _path: Path) -> Any:
        index = min(self.calls, len(self.statuses) - 1)
        self.calls += 1
        value = self.statuses[index]
        if isinstance(value, Exception):
            raise value
        return value


class _Launcher:
    def __init__(self) -> None:
        self.commands: list[tuple[str, ...]] = []
        self.children: list[_Child] = []

    def __call__(self, command: Any) -> _Child:
        self.commands.append(tuple(command))
        child = _Child()
        self.children.append(child)
        return child


def _supervisor(
    tmp_path: Path,
    inspector: _Inspector,
    launcher: _Launcher,
    *,
    approved: bool = False,
    ceiling: int = 100,
    restarts: int = 1,
    config_path: Path | None = None,
) -> V2CampaignSupervisor:
    return V2CampaignSupervisor(
        config_path=config_path or _config(tmp_path),
        state_path=tmp_path / "supervisor" / "state.json",
        token_ceiling=ceiling,
        max_restarts=restarts,
        execute_approved=approved,
        poll_interval_seconds=0.01,
        ori_executable="/approved/ori",
        inspector=inspector,
        launcher=launcher,
        sleeper=lambda _seconds: None,
        now=lambda: "2026-08-30T12:00:00+00:00",
    )


@pytest.mark.parametrize(
    ("status", "approved", "state_exists", "action"),
    [
        (_status("not_started"), False, False, "run_readiness"),
        (_status("readiness_complete", mode="readiness"), False, True, "await_execution_approval"),
        (_status("readiness_complete", mode="readiness"), True, True, "run_execution"),
        (_status("running", mode="execution"), True, True, "monitor"),
        (_status("interrupted", mode="execution"), True, False, "stop"),
        (_status("interrupted", mode="execution"), True, True, "run_execution"),
        (_status("failed", mode="execution"), True, True, "stop"),
        (_status("completed", mode="execution"), True, True, "complete"),
        (_status("completed", mode="execution", valid=False), True, True, "stop"),
    ],
)
def test_decision_table(status: Any, approved: bool, state_exists: bool, action: str) -> None:
    assert (
        decide_supervisor_action(
            status, execute_approved=approved, state_exists=state_exists
        ).action
        == action
    )


def test_initial_readiness_launch_is_exact_and_not_paid(tmp_path: Path) -> None:
    launcher = _Launcher()
    supervisor = _supervisor(tmp_path, _Inspector(_status("not_started")), launcher)

    result = supervisor.run(max_polls=1)

    assert result.action == "monitor"
    assert launcher.commands == [
        ("/approved/ori", "run-v2", "--config", str((tmp_path / "models-v2.yaml").resolve()))
    ]
    state = supervisor.store.load()
    assert state.launches == 1
    assert state.restarts_used == 0
    assert state.campaign.purpose == "official"
    assert result.campaign == state.campaign


def test_diagnostic_supervisor_uses_canary_command_and_binds_selection(tmp_path: Path) -> None:
    launcher = _Launcher()
    config = _diagnostic_config(tmp_path)
    supervisor = _supervisor(
        tmp_path,
        _Inspector(_status("not_started")),
        launcher,
        config_path=config,
    )

    result = supervisor.run(max_polls=1)

    assert launcher.commands == [
        ("/approved/ori", "run-canary-v2", "--config", str(config.resolve()))
    ]
    state = supervisor.store.load()
    assert state.campaign.purpose == "diagnostic_canary"
    assert state.campaign.canary_suite == "native-five-kind-v1"
    assert (
        state.campaign.canary_selection_fingerprint
        == supervisor.campaign.canary_selection_fingerprint
    )
    assert result.campaign == state.campaign


def test_diagnostic_supervisor_refuses_changed_selection_receipt(tmp_path: Path) -> None:
    launcher = _Launcher()
    config = _diagnostic_config(tmp_path)
    initial = _supervisor(
        tmp_path,
        _Inspector(_status("not_started")),
        launcher,
        config_path=config,
    )
    initial.run(max_polls=1)
    _write_diagnostic_selection(tmp_path / "diagnostic-selection.json", seed=2)
    resumed = _supervisor(
        tmp_path,
        _Inspector(_status("running", mode="readiness")),
        launcher,
        config_path=config,
    )

    with pytest.raises(CampaignSupervisorError, match="diagnostic-canary binding changed"):
        resumed.run()


def test_owned_child_is_monitored_while_status_has_not_transitioned(
    tmp_path: Path,
) -> None:
    launcher = _Launcher()
    supervisor = _supervisor(
        tmp_path,
        _Inspector(_status("not_started"), _status("not_started")),
        launcher,
    )

    result = supervisor.run(max_polls=2)

    assert result.action == "monitor"
    assert len(launcher.commands) == 1
    assert supervisor.store.load().restarts_used == 0


def test_repeated_pre_lifecycle_child_exit_consumes_restart_budget(
    tmp_path: Path,
) -> None:
    launcher = _Launcher()
    supervisor = _supervisor(
        tmp_path,
        _Inspector(_status("not_started")),
        launcher,
        restarts=1,
    )
    supervisor.run(max_polls=1)
    launcher.children[-1].returncode = 1

    supervisor.run(max_polls=1)
    assert supervisor.store.load().restarts_used == 1
    launcher.children[-1].returncode = 1

    with pytest.raises(CampaignSupervisorError, match="restart limit"):
        supervisor.run(max_polls=1)
    assert len(launcher.commands) == 2


def test_paid_execution_requires_explicit_approval(tmp_path: Path) -> None:
    launcher = _Launcher()
    result = _supervisor(
        tmp_path,
        _Inspector(_status("readiness_complete", mode="readiness")),
        launcher,
    ).run()

    assert result.action == "await_execution_approval"
    assert launcher.commands == []


def test_approved_execution_uses_execute_flag(tmp_path: Path) -> None:
    launcher = _Launcher()
    supervisor = _supervisor(
        tmp_path,
        _Inspector(_status("readiness_complete", mode="readiness")),
        launcher,
        approved=True,
    )

    supervisor.run(max_polls=1)

    assert launcher.commands[0][-1] == "--execute"


def test_supervisor_state_must_be_outside_campaign_root(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with pytest.raises(CampaignSupervisorError, match="outside"):
        V2CampaignSupervisor(
            config_path=config,
            state_path=tmp_path / "campaign" / "supervisor.json",
            token_ceiling=10,
            max_restarts=0,
            execute_approved=False,
            ori_executable="/approved/ori",
            launcher=_Launcher(),
        )


def test_supervisor_requires_absolute_ori_executable(tmp_path: Path) -> None:
    with pytest.raises(CampaignSupervisorError, match="absolute path"):
        V2CampaignSupervisor(
            config_path=_config(tmp_path),
            state_path=tmp_path / "supervisor" / "state.json",
            token_ceiling=10,
            max_restarts=0,
            execute_approved=False,
            ori_executable="ori",
            launcher=_Launcher(),
        )


def test_duplicate_supervisor_state_lock_fails_closed(tmp_path: Path) -> None:
    state_path = tmp_path / "supervisor" / "state.json"
    root = tmp_path / "campaign"

    with SupervisorStateStore(state_path, root):
        with pytest.raises(SupervisorAlreadyRunningError, match="another"):
            with SupervisorStateStore(state_path, root):
                raise AssertionError("unreachable")


def test_refuses_new_state_for_interrupted_execution(tmp_path: Path) -> None:
    supervisor = _supervisor(
        tmp_path,
        _Inspector(_status("interrupted", mode="execution")),
        _Launcher(),
        approved=True,
    )

    with pytest.raises(CampaignSupervisorError, match="already-interrupted"):
        supervisor.run()


def test_restart_allowance_is_durable_and_cannot_reset(tmp_path: Path) -> None:
    launcher = _Launcher()
    initial = _supervisor(
        tmp_path,
        _Inspector(_status("readiness_complete", mode="readiness")),
        launcher,
        approved=True,
    )
    initial.run(max_polls=1)

    # Recovery requires the original owner to finish cleanup, not adoption of
    # a child inferred dead from a synthetic status change alone.
    with initial.store:
        initial._cleanup_owned_child()

    resumed = _supervisor(
        tmp_path,
        _Inspector(_status("interrupted", mode="execution")),
        launcher,
        approved=True,
    )
    resumed.run(max_polls=1)
    assert resumed.store.load().restarts_used == 1
    with resumed.store:
        resumed._cleanup_owned_child()

    exhausted = _supervisor(
        tmp_path,
        _Inspector(_status("interrupted", mode="execution")),
        launcher,
        approved=True,
    )
    with pytest.raises(CampaignSupervisorError, match="restart limit"):
        exhausted.run(max_polls=1)


def test_policy_change_is_rejected_across_invocations(tmp_path: Path) -> None:
    first = _supervisor(tmp_path, _Inspector(_status("not_started")), _Launcher())
    first.run(max_polls=1)
    changed = _supervisor(
        tmp_path,
        _Inspector(_status("running", mode="readiness")),
        _Launcher(),
        ceiling=101,
    )
    with pytest.raises(CampaignSupervisorError, match="policy changed"):
        changed.run(max_polls=1)


def test_budget_terminates_only_owned_child(tmp_path: Path) -> None:
    launcher = _Launcher()
    supervisor = _supervisor(
        tmp_path,
        _Inspector(
            _status("readiness_complete", mode="readiness", tokens=0),
            _status("running", mode="execution", tokens=10),
        ),
        launcher,
        approved=True,
        ceiling=10,
    )

    with pytest.raises(CampaignSupervisorError, match="token ceiling"):
        supervisor.run()

    assert launcher.children[0].terminated is True
    assert supervisor.store.load().budget_stop_requested is True


def test_budget_never_signals_adopted_process(tmp_path: Path) -> None:
    launcher = _Launcher()
    initial = _supervisor(tmp_path, _Inspector(_status("not_started")), launcher, ceiling=10)
    initial.run(max_polls=1)
    adopted = _supervisor(
        tmp_path,
        _Inspector(_status("running", mode="execution", tokens=10)),
        launcher,
        approved=True,
        ceiling=10,
    )

    with pytest.raises(CampaignSupervisorError, match="cleanup remains unconfirmed"):
        adopted.run()

    assert launcher.children[0].terminated is False


def test_decreasing_token_accounting_fails_closed(tmp_path: Path) -> None:
    launcher = _Launcher()
    first = _supervisor(tmp_path, _Inspector(_status("not_started", tokens=5)), launcher)
    first.run(max_polls=1)
    with first.store:
        first._cleanup_owned_child()
    second = _supervisor(
        tmp_path, _Inspector(_status("running", mode="readiness", tokens=4)), launcher
    )
    with pytest.raises(CampaignSupervisorError, match="decreased"):
        second.run()


def test_status_errors_are_terminal_and_do_not_launch(tmp_path: Path) -> None:
    launcher = _Launcher()
    supervisor = _supervisor(tmp_path, _Inspector(ValueError("corrupt evidence")), launcher)
    with pytest.raises(CampaignSupervisorError, match="failed closed"):
        supervisor.run()
    assert launcher.commands == []


def test_exception_cleanup_retains_ownership_lock_and_restores_signal_handlers(
    tmp_path: Path,
) -> None:
    launcher = _Launcher()
    supervisor = _supervisor(
        tmp_path,
        _Inspector(_status("readiness_complete", mode="readiness"), RuntimeError("defect")),
        launcher,
        approved=True,
    )
    handlers = {
        signum: signal.getsignal(signum)
        for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)
    }
    forwarded = []
    original_launch = supervisor.launcher

    def launch(command):
        child = original_launch(command)
        original_signal = child.send_signal

        def cleanup_signal(signum):
            with pytest.raises(SupervisorAlreadyRunningError):
                with SupervisorStateStore(supervisor.store.path, supervisor.campaign_root):
                    raise AssertionError("cleanup released the external ownership lock")
            forwarded.append(signum)
            original_signal(signum)

        child.send_signal = cleanup_signal
        return child

    supervisor.launcher = launch
    with pytest.raises(RuntimeError, match="defect"):
        supervisor.run()
    assert forwarded == [signal.SIGTERM]
    assert launcher.children[0].poll() == -signal.SIGTERM
    assert {signum: signal.getsignal(signum) for signum in handlers} == handlers
    with SupervisorStateStore(supervisor.store.path, supervisor.campaign_root):
        pass


@pytest.mark.parametrize("first_failure", ["exception", "signal"])
def test_real_signal_at_exception_cleanup_entry_cannot_skip_owned_cleanup(
    tmp_path: Path, first_failure: str
) -> None:
    launcher = _Launcher()
    calls = 0

    def inspector(_path):
        nonlocal calls
        calls += 1
        if calls == 1:
            return _status("readiness_complete", mode="readiness")
        if first_failure == "exception":
            raise RuntimeError("synthetic defect")
        os.kill(os.getpid(), signal.SIGINT)
        return _status("running", mode="execution")

    supervisor = _supervisor(tmp_path, inspector, launcher, approved=True)
    function = V2CampaignSupervisor._cancellation_boundary.__wrapped__
    lines, start = inspect.getsourcelines(function)
    except_index = next(
        index
        for index, line in enumerate(lines)
        if line.startswith("        except BaseException:")
    )
    cleanup_line = start + except_index + 1
    injected = []

    def trace(frame, event, _argument):
        if (
            not injected
            and event == "line"
            and frame.f_code is function.__code__
            and frame.f_lineno == cleanup_line
        ):
            injected.append(frame.f_lineno)
            os.kill(os.getpid(), signal.SIGTERM)
        return trace

    previous_trace = sys.gettrace()
    try:
        sys.settrace(trace)
        with pytest.raises((RuntimeError, CampaignSupervisorError)):
            supervisor.run()
    finally:
        sys.settrace(previous_trace)
    assert injected == [cleanup_line]
    assert launcher.children[0].poll() == -(
        signal.SIGTERM if first_failure == "exception" else signal.SIGINT
    )
    assert not supervisor._ownership_path.exists()
    with SupervisorStateStore(supervisor.store.path, supervisor.campaign_root):
        pass


def test_unconfirmed_child_cleanup_is_durable_and_cannot_be_adopted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher = _Launcher()
    supervisor = _supervisor(
        tmp_path,
        _Inspector(_status("readiness_complete", mode="readiness"), RuntimeError("defect")),
        launcher,
        approved=True,
    )
    monkeypatch.setattr("ori.eval.v2.campaign_supervisor._CHILD_TERMINATE_GRACE_SECONDS", 0)
    monkeypatch.setattr("ori.eval.v2.campaign_supervisor._CHILD_KILL_GRACE_SECONDS", 0)
    monkeypatch.setattr(_Child, "send_signal", lambda *_args: None)
    monkeypatch.setattr(_Child, "kill", lambda *_args: None)
    with pytest.raises(CampaignSupervisorError, match="cleanup remains unconfirmed"):
        supervisor.run()
    assert launcher.children[0].poll() is None
    assert supervisor._ownership_path.exists()
    replacement = _supervisor(tmp_path, _Inspector(_status("not_started")), launcher, approved=True)
    with pytest.raises(CampaignSupervisorError, match="cleanup remains unconfirmed"):
        replacement.run(max_polls=1)
    assert len(launcher.commands) == 1
    assert supervisor._ownership_path.exists()


def test_pre_spawn_ownership_marker_survives_ambiguous_launcher_failure(tmp_path: Path) -> None:
    supervisor = _supervisor(tmp_path, _Inspector(_status("not_started")), _Launcher())

    def ambiguous_launch(_command):
        assert supervisor._ownership_path.exists()
        raise OSError("synthetic startup failure")

    supervisor.launcher = ambiguous_launch
    with pytest.raises(CampaignSupervisorError, match="cannot launch"):
        supervisor.run()
    assert supervisor._ownership_path.exists()
    replacement = _supervisor(tmp_path, _Inspector(_status("not_started")), _Launcher())
    with pytest.raises(CampaignSupervisorError, match="cleanup remains unconfirmed"):
        replacement.run(max_polls=1)


def test_ambiguous_second_launch_cannot_borrow_previous_childs_cleanup(tmp_path: Path) -> None:
    launcher = _Launcher()
    supervisor = _supervisor(
        tmp_path,
        _Inspector(_status("not_started"), _status("readiness_complete", mode="readiness")),
        launcher,
        approved=True,
    )
    supervisor.run(max_polls=1)
    old_child = launcher.children[0]
    old_child.returncode = 0

    def ambiguous_execution(_command):
        assert supervisor._ownership_path.exists()
        assert supervisor._owned_child is None
        raise OSError("second launch acknowledgement lost")

    supervisor.launcher = ambiguous_execution
    with pytest.raises(CampaignSupervisorError, match="cannot launch"):
        supervisor.run()
    assert old_child.poll() == 0
    assert supervisor._ownership_path.exists()
    replacement_launcher = _Launcher()
    replacement = _supervisor(
        tmp_path,
        _Inspector(_status("readiness_complete", mode="readiness")),
        replacement_launcher,
        approved=True,
    )
    with pytest.raises(CampaignSupervisorError, match="cleanup remains unconfirmed"):
        replacement.run(max_polls=1)
    assert replacement_launcher.commands == []


def test_state_fingerprint_detects_tampering(tmp_path: Path) -> None:
    supervisor = _supervisor(tmp_path, _Inspector(_status("not_started")), _Launcher())
    supervisor.run(max_polls=1)
    raw = supervisor.store.path.read_text().replace('"launches": 1', '"launches": 9')
    supervisor.store.path.write_text(raw)
    with pytest.raises(CampaignSupervisorError, match="fingerprint"):
        SupervisorStateStore(supervisor.store.path, supervisor.campaign_root).load()


def test_state_store_rejects_symlinked_state(tmp_path: Path) -> None:
    supervisor = _supervisor(tmp_path, _Inspector(_status("not_started")), _Launcher())
    supervisor.run(max_polls=1)
    victim = tmp_path / "victim.json"
    victim.write_text("{}\n")
    supervisor.store.path.unlink()
    supervisor.store.path.symlink_to(victim)

    with pytest.raises(CampaignSupervisorError, match="invalid supervisor state"):
        SupervisorStateStore(supervisor.store.path, supervisor.campaign_root).load()


def test_supervisor_rejects_symlinked_lock(tmp_path: Path) -> None:
    launcher = _Launcher()
    supervisor = _supervisor(tmp_path, _Inspector(_status("not_started")), launcher)
    supervisor.store.lock_path.parent.mkdir(parents=True, exist_ok=True)
    supervisor.store.lock_path.symlink_to(tmp_path / "victim.lock")

    with pytest.raises(CampaignSupervisorError, match="cannot open supervisor-state lock"):
        supervisor.run()


def test_valid_completion_builds_packaged_model_card_without_scripts_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Path, Path, str | None, str | None]] = []

    def fake_build(
        campaign_root: Path,
        output_dir: Path,
        *,
        model: str | None = None,
        display_name: str | None = None,
    ) -> dict[str, object]:
        calls.append((campaign_root, output_dir, model, display_name))
        return {}

    monkeypatch.setattr("ori.eval.v2.campaign_supervisor.build_model_card", fake_build)
    real_import = builtins.__import__
    scripts_imports: list[str] = []

    def import_without_scripts(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "scripts" or name.startswith("scripts."):
            scripts_imports.append(name)
            raise ModuleNotFoundError(f"No module named {name!r}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_scripts)
    config = _config(tmp_path)
    supervisor = V2CampaignSupervisor(
        config_path=config,
        state_path=tmp_path / "supervisor" / "state.json",
        token_ceiling=10,
        max_restarts=1,
        execute_approved=True,
        ori_executable="/approved/ori",
        model_card_output=tmp_path / "public-card",
        model="provider-model",
        display_name="Poolside Laguna S 2.1",
        inspector=_Inspector(_status("completed", mode="execution", tokens=10)),
        launcher=_Launcher(),
        sleeper=lambda _seconds: None,
        now=lambda: "2026-08-30T12:00:00+00:00",
    )

    result = supervisor.run()

    assert result.action == "complete"
    assert calls == [
        (
            (tmp_path / "campaign").resolve(),
            tmp_path / "public-card",
            "provider-model",
            "Poolside Laguna S 2.1",
        )
    ]
    assert supervisor.store.load().model_card_generated is True
    assert scripts_imports == []
