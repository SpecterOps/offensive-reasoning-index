"""Real supervisor/child cancellation, using only synthetic status and heartbeats."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ori.eval.v2 import campaign_supervisor

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX supervisor signals")


def _write_synthetic_config(tmp_path: Path) -> None:
    """Satisfy config/path loading only; the injected inspector owns fake status."""
    for name in (
        "manifest.json",
        "archive.zip",
        "direct-public.json",
        "direct-private.json",
        "direct-candidates.json",
        "direct-live.json",
    ):
        (tmp_path / name).touch()
    (tmp_path / "mcp").mkdir()
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
    (tmp_path / "models-v2.yaml").write_text(json.dumps(payload))


_HEARTBEAT = r'''
import os, signal, sys, time
from pathlib import Path
root = Path(__file__).parent
def stop(signum, _frame):
    (root / "child-signal").write_text(str(signum))
    if not (root / "ignore-stop").exists():
        raise SystemExit(128 + signum)
for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
    signal.signal(signum, stop)
(root / "child-pid").write_text(str(os.getpid()))
deadline = time.monotonic() + 15
while time.monotonic() < deadline:
    (root / "heartbeat").write_text(str(time.monotonic_ns()))
    time.sleep(.02)
'''

_SUPERVISOR = r'''
import json, sys, time
from pathlib import Path
from types import SimpleNamespace as NS
from ori.eval.v2 import campaign_supervisor as module
root = Path(sys.argv[1])
mode = sys.argv[2]
module._CHILD_TERMINATE_GRACE_SECONDS = .3
module._CHILD_KILL_GRACE_SECONDS = 1
calls = 0
def inspect(_config):
    global calls
    calls += 1
    if calls > 1 and mode in {"inspector_error", "budget", "busy_inspector"}:
        deadline = time.monotonic() + 5
        while not (root / "heartbeat").exists():
            if time.monotonic() > deadline:
                raise RuntimeError("synthetic child did not start")
            time.sleep(.01)
        if mode == "inspector_error":
            raise ValueError("synthetic inspector failure")
        if mode == "busy_inspector":
            (root / "inspect-window").touch()
            time.sleep(.8)
    return NS(
        schema_version=module.CAMPAIGN_STATUS_SCHEMA_VERSION,
        source_config_fingerprint="a" * 64,
        observed_state="readiness_complete" if calls == 1 else "running",
        next_action="execute_campaign" if calls == 1 else "monitor",
        resume_allowed=calls == 1,
        mode="readiness" if calls == 1 else "execution",
        progress=NS(total_tokens=100 if calls > 1 and mode == "budget" else 0),
        tracks=(),
    )
if mode == "startup":
    original_popen = module.subprocess.Popen
    def delayed_popen(*args, **kwargs):
        child = original_popen(*args, **kwargs)
        deadline = time.monotonic() + 5
        while not (root / "heartbeat").exists():
            if time.monotonic() > deadline:
                child.kill()
                child.wait()
                raise RuntimeError("synthetic child did not start")
            time.sleep(.01)
        (root / "startup-window").touch()
        time.sleep(.4)
        return child
    module.subprocess.Popen = delayed_popen
supervisor = module.V2CampaignSupervisor(
    config_path=root / "models-v2.yaml",
    state_path=root / "supervisor" / "state.json",
    token_ceiling=100, max_restarts=0, execute_approved=True,
    ori_executable=str(root / "synthetic-ori"),
    poll_interval_seconds=.02, inspector=inspect,
)
try:
    supervisor.run()
except module.CampaignSupervisorError as error:
    child = supervisor._owned_child
    (root / "supervisor-result.json").write_text(json.dumps({
        "error_type": type(error).__name__,
        "child_exit": child.poll() if child is not None else None,
    }))
    raise SystemExit(1)
'''


def _wait_for(path: Path, process: subprocess.Popen, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while not path.exists():
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            pytest.fail(f"synthetic supervisor exited early: {stdout!r} {stderr!r}")
        if time.monotonic() >= deadline:
            pytest.fail(f"synthetic process did not publish {path.name}")
        time.sleep(.01)


def _start(tmp_path: Path, mode: str, *, ignore_stop: bool = False) -> subprocess.Popen:
    _write_synthetic_config(tmp_path)
    executable = tmp_path / "synthetic-ori"
    executable.write_text(f"#!{sys.executable}\n" + _HEARTBEAT)
    executable.chmod(0o700)
    if ignore_stop:
        (tmp_path / "ignore-stop").touch()
    return subprocess.Popen(
        [sys.executable, "-B", "-c", _SUPERVISOR, str(tmp_path), mode],
        env={"PATH": os.defpath, "PYTHONPATH": str(Path(campaign_supervisor.__file__).parents[3])},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )


def _assert_stopped(tmp_path: Path, process: subprocess.Popen) -> dict:
    stdout, stderr = process.communicate(timeout=5)
    assert process.returncode == 1, (stdout, stderr)
    result = json.loads((tmp_path / "supervisor-result.json").read_text())
    assert result["child_exit"] is not None
    before = (tmp_path / "heartbeat").read_bytes()
    time.sleep(.08)
    assert (tmp_path / "heartbeat").read_bytes() == before
    return result


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT, signal.SIGHUP])
@pytest.mark.parametrize("mode", ["running", "startup", "busy_inspector"])
def test_supervisor_forwards_stop_and_reaps_original_heartbeat_child(
    tmp_path: Path, signum: int, mode: str
) -> None:
    process = _start(tmp_path, mode)
    try:
        ready = {
            "running": "heartbeat",
            "startup": "startup-window",
            "busy_inspector": "inspect-window",
        }[mode]
        _wait_for(tmp_path / ready, process)
        process.send_signal(signum)
        if mode == "busy_inspector":
            _wait_for(tmp_path / "child-signal", process, timeout=.5)
            assert not (tmp_path / "supervisor-result.json").exists()
        result = _assert_stopped(tmp_path, process)
        assert result["error_type"] == "SupervisorInterruptedError"
        assert int((tmp_path / "child-signal").read_text()) == signum
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)
        # The synthetic child also has a fixed self-expiry, so a failed
        # regression cannot leave an indefinitely running orphan. Never adopt
        # its published PID to acquire signal authority in the test driver.


@pytest.mark.parametrize("mode", ["inspector_error", "budget"])
def test_exceptional_supervisor_exit_contains_real_child(tmp_path: Path, mode: str) -> None:
    process = _start(tmp_path, mode, ignore_stop=True)
    try:
        result = _assert_stopped(tmp_path, process)
        assert result["error_type"] == "CampaignSupervisorError"
        assert result["child_exit"] == -signal.SIGKILL
        assert int((tmp_path / "child-signal").read_text()) == signal.SIGTERM
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)


def test_repeated_signals_do_not_interrupt_finite_escalation(tmp_path: Path) -> None:
    process = _start(tmp_path, "running", ignore_stop=True)
    try:
        _wait_for(tmp_path / "heartbeat", process)
        process.send_signal(signal.SIGTERM)
        _wait_for(tmp_path / "child-signal", process)
        process.send_signal(signal.SIGINT)
        process.send_signal(signal.SIGHUP)
        result = _assert_stopped(tmp_path, process)
        assert result["error_type"] == "SupervisorInterruptedError"
        assert result["child_exit"] == -signal.SIGKILL
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)
