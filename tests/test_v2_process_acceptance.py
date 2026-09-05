"""Real-process containment checks, without provider or graph qualification.

The lifecycle child replaces artifact preparation and the execution body only;
the production runner owns locking, signals, persistence, and interruption.
These tests do not certify live graph parity, providers, or remote deployment.
"""

from __future__ import annotations

import os
import selectors
import signal
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from ori.eval.v2 import campaign_runner, campaign_status
from ori.eval.v2.campaign_supervisor import (
    SupervisorAlreadyRunningError,
    SupervisorStateStore,
)

_TIMEOUT = 30
_CHILD = r"""
import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

# Fail closed if a future import or test seam attempts networking or a launcher.
def prohibit_external(event, args):
    if event in {"socket.connect", "socket.connect_ex", "socket.getaddrinfo",
                 "subprocess.Popen", "os.system", "os.posix_spawn"}:
        raise RuntimeError("external access forbidden in process acceptance")
sys.addaudithook(prohibit_external)

from ori.eval.v2 import campaign_runner as runner
from ori.eval.v2.campaign_supervisor import SupervisorStateStore
from ori.eval.v2.schema import Track

root = Path(sys.argv[1])
mode = sys.argv[2]

def wait_for_parent():
    print("READY", flush=True)
    command = sys.stdin.readline().strip()
    if command == "crash":
        os._exit(23)
    if command != "release":
        raise RuntimeError("unexpected parent command")

if mode == "campaign_lock":
    with runner._exclusive_output_dir_lock(root / "campaign"):
        wait_for_parent()
elif mode == "supervisor_lock":
    with SupervisorStateStore(root / "supervisor.json", root / "campaign"):
        wait_for_parent()
else:
    resolved = SimpleNamespace(
        output_dir=root / "campaign",
        source_config_fingerprint="a" * 64,
        config=SimpleNamespace(models=(), track_modes=()),
    )
    runner.prepare_v2_campaign = lambda _: (resolved, None, {}, "test", ())

    async def controlled_body(**kwargs):
        kwargs["lifecycle"].activate_track(Track.DIRECT)
        print("READY", flush=True)
        await asyncio.Event().wait()

    runner._run_prepared_v2_campaign = controlled_body
    try:
        asyncio.run(runner.run_v2_campaign(root / "unused.yaml"))
    except runner.V2CampaignRunError as exc:
        if str(exc) != "v2 campaign interrupted by SIGTERM":
            raise
        print("INTERRUPTED", flush=True)
"""


@contextmanager
def _child(root: Path, mode: str) -> Iterator[subprocess.Popen[str]]:
    # Do not inherit credentials, proxy settings, or user package configuration.
    environment = {
        "PATH": os.defpath,
        "HOME": str(root),
        "TMPDIR": str(root),
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
    }
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", _CHILD, str(root), mode],
        cwd=root,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        assert process.stdout is not None
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(_TIMEOUT), "child readiness timed out"
            assert process.stdout.readline() == "READY\n", "child failed before readiness"
        yield process
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=_TIMEOUT)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=_TIMEOUT)
        for pipe in (process.stdin, process.stdout):
            if pipe is not None:
                pipe.close()


@pytest.mark.parametrize("mode", ["campaign_lock", "supervisor_lock"])
@pytest.mark.parametrize("exit_mode", ["release", "crash"])
def test_real_process_locks_exclude_contenders_and_release_after_exit(
    tmp_path: Path, mode: str, exit_mode: str,
) -> None:
    def lock():
        if mode == "campaign_lock":
            return campaign_runner._exclusive_output_dir_lock(tmp_path / "campaign")
        return SupervisorStateStore(tmp_path / "supervisor.json", tmp_path / "campaign")

    error = (
        campaign_runner.V2CampaignRunError
        if mode == "campaign_lock"
        else SupervisorAlreadyRunningError
    )
    with _child(tmp_path, mode) as process:
        with pytest.raises(error):
            with lock():
                pytest.fail("contender entered child-owned lock")
        if mode == "campaign_lock":
            assert campaign_status._lock_is_contended(tmp_path / "campaign")
        assert process.stdin is not None
        process.stdin.write(exit_mode + "\n")
        process.stdin.flush()
        assert process.wait(timeout=_TIMEOUT) == (0 if exit_mode == "release" else 23)

    # Reacquisition must work while the persistent lock file still exists.
    with lock():
        pass
    if mode == "campaign_lock":
        assert not campaign_status._lock_is_contended(tmp_path / "campaign")


@pytest.mark.parametrize("termination", [signal.SIGTERM, signal.SIGKILL])
def test_real_runner_interruption_is_durable_and_recoverable(
    tmp_path: Path, termination: signal.Signals,
) -> None:
    output = tmp_path / "campaign"
    receipt_path = output / "campaign-lifecycle-v2.private.json"

    def receipt():
        return campaign_runner.CampaignLifecycleV2.model_validate_json(receipt_path.read_text())

    with _child(tmp_path, "runner") as process:
        before = receipt()
        assert before.status == "running"
        assert before.pid == process.pid
        assert before.resume_count == 0
        assert campaign_status._lock_is_contended(output)
        process.send_signal(termination)
        assert process.wait(timeout=_TIMEOUT) == (
            0 if termination == signal.SIGTERM else -signal.SIGKILL
        )
        if termination == signal.SIGTERM:
            assert process.stdout is not None
            assert process.stdout.read() == "INTERRUPTED\n"

    interrupted = receipt()
    assert not campaign_status._lock_is_contended(output)
    if termination == signal.SIGTERM:
        assert interrupted.status == "interrupted"
        assert interrupted.interruptions[-1].kind == "signal"
        assert interrupted.interruptions[-1].signal_name == "SIGTERM"
    else:
        assert interrupted.status == "running"
        assert interrupted.interruptions == ()

    # A second interpreter must load the actual receipt, audit an unclean exit,
    # and hold the same persistent lock. It does not claim task-level resume.
    with _child(tmp_path, "runner") as restarted:
        recovered = receipt()
        assert recovered.pid == restarted.pid
        assert recovered.resume_count == 1
        assert recovered.started_at_utc == before.started_at_utc
        assert recovered.status == "running"
        assert recovered.interruptions[-1].kind == (
            "signal" if termination == signal.SIGTERM else "unclean_previous_process"
        )
        restarted.terminate()
        assert restarted.wait(timeout=_TIMEOUT) == 0

    final = receipt()
    assert final.status == "interrupted"
    assert final.interruptions[-1].signal_name == "SIGTERM"
    assert final.checkpointed_results == 0
    assert final.completed_tracks == ()
    assert not campaign_status._lock_is_contended(output)
