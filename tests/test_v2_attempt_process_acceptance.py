"""Fresh-interpreter retry acceptance; no providers, graph services, or launchers.

The scheduler, Direct runtime, checkpoint validation and atomic persistence are
production code. Tiny task/artifact/provenance fixtures replace certification;
only the provider transport fails synthetically. This is not live qualification.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from ori.eval.v2 import campaign_runner

_CHILD = r'''
import sys
def deny_external(event, args):
    if event in {"socket.connect", "socket.connect_ex", "socket.getaddrinfo",
                 "socket.sendto", "subprocess.Popen", "os.system", "os.posix_spawn"}:
        raise RuntimeError("external access forbidden")
sys.addaudithook(deny_external)

import asyncio
import json
import os
from functools import partial
from pathlib import Path
from types import SimpleNamespace as NS
import httpx
from ori.eval.v2 import campaign_runner as runner, model_runtime
from ori.eval.v2.profiles import build_direct_capability_profile
from ori.eval.v2.schema import Track
from tests.test_v2_direct_adapter import TASK, ORACLE
from tests.test_v2_campaign_status import _provenance

root, mode = Path(sys.argv[1]), sys.argv[2]
profile = build_direct_capability_profile()
TASK = TASK.model_copy(update={"binding": TASK.binding.model_copy(
    update={"capability_profile_id": profile.profile_id}
)})
pair = NS(
    public=NS(tasks=(TASK,), track=Track.DIRECT, product="simple",
              artifact_fingerprint="a"*64, catalog_fingerprint="b"*64,
              graph_fingerprint="c"*64, compiler_fingerprint="d"*64),
    private=NS(oracles=(ORACLE,), identity_catalog=ORACLE.resolved_roles,
               artifact_fingerprint="e"*64),
)
prepared = runner.PreparedTrack(
    track=Track.DIRECT, pair=pair, profile=profile,
    release=NS(entries=(NS(task_id=TASK.task_id),)), live=NS(), certifications={},
)
resolved = NS(output_dir=root, config=NS(defaults=NS(
    max_infra_retries=2, infra_retry=NS(immediate_retries=0, deferred_cooldown_seconds=3),
    model_base_url=None, reasoning_effort=None,
    health=NS(timeout_seconds=1, poll_interval=.01),
)))
model = NS(name="gpt-test", provider="codex", model="gpt-test",
           requested_model="codex/gpt-test", model_base_url=None, options={})
runner._provenance = lambda **kwargs: _provenance(Track.DIRECT)
calls = 0
async def failing_transport(**kwargs):
    global calls
    calls += 1
    raise httpx.ConnectError("synthetic provider outage")
runner.run_direct_model_task_v2 = partial(
    model_runtime.run_direct_model_task_v2, transport=failing_transport
)
def progress(message):
    if "cooldown=" in message:
        # Progress is emitted only after the scheduler's production durable write.
        if mode == "first" or mode == "same_cooldown":
            print(json.dumps({"calls": calls, "message": message}), flush=True)
            os._exit(23)
        if mode == "next_round" and "round 2:" in message:
            print(json.dumps({"calls": calls, "message": message}), flush=True)
            os._exit(23)
asyncio.run(runner._run_model(
    resolved=resolved, prepared=prepared, model=model, run_index=1,
    bhce=NS(), coordinator=NS(circuit_open=False), loop=None, runs_total=1,
    progress=progress,
))
print(json.dumps({"calls": calls}), flush=True)
'''


def _run(root: Path, mode: str) -> dict:
    repo = Path(__file__).resolve().parents[1]
    environment = {
        "PATH": os.defpath,
        "HOME": str(root),
        "TMPDIR": str(root),
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": os.pathsep.join((str(repo), str(repo / "src"))),
    }
    # subprocess.run kills and reaps its owned child on timeout.
    result = subprocess.run(
        [sys.executable, "-u", "-c", _CHILD, str(root), mode],
        cwd=root, env=environment, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == (0 if mode == "finish" else 23), result.stderr
    return json.loads(result.stdout.splitlines()[-1])


def _state(root: Path) -> campaign_runner.PrivateRunStateV2:
    path = root / "direct" / "gpt-test" / "run-001" / campaign_runner.RUN_STATE_NAME
    return campaign_runner.PrivateRunStateV2.model_validate_json(path.read_text())


def test_process_resume_preserves_cooldown_round_and_lifetime_retry_budget(tmp_path: Path):
    assert _run(tmp_path, "first")["calls"] == 1
    initial = _state(tmp_path)
    assert [a.attempt for a in initial.attempts] == [1]
    assert initial.scheduler.phase == "deferred_cooldown"
    assert initial.scheduler.recovery_round == 1
    assert initial.scheduler.deferred_not_before_utc is not None
    assert len(initial.checkpoint.results) == 1

    # Repeated interpreter death during cooldown neither charges nor resets it.
    assert _run(tmp_path, "same_cooldown")["calls"] == 0
    same = _state(tmp_path)
    assert same == initial

    assert _run(tmp_path, "next_round")["calls"] == 1
    second = _state(tmp_path)
    assert [a.attempt for a in second.attempts] == [1, 2]
    assert [a.recovery_round for a in second.attempts] == [0, 1]
    assert second.scheduler.recovery_round == 2
    assert second.scheduler.deferred_not_before_utc > initial.scheduler.deferred_not_before_utc
    assert datetime.fromisoformat(second.attempts[-1].started_at_utc) >= datetime.fromisoformat(
        initial.scheduler.deferred_not_before_utc
    )
    assert len(second.checkpoint.results) == 1

    assert _run(tmp_path, "finish")["calls"] == 1
    final = _state(tmp_path)
    assert [a.attempt for a in final.attempts] == [1, 2, 3]
    assert [a.scheduler_phase for a in final.attempts] == [
        "initial", "deferred_retry", "deferred_retry",
    ]
    assert [a.recovery_round for a in final.attempts] == [0, 1, 2]
    assert datetime.fromisoformat(final.attempts[-1].started_at_utc) >= datetime.fromisoformat(
        second.scheduler.deferred_not_before_utc
    )
    assert all(a.sample.outcome.value == "INFRA_ERROR" for a in final.attempts)
    assert final.scheduler.phase == "complete"
    assert len(final.checkpoint.results) == 1
    # Exhaustion is lifetime-scoped, not reset by yet another interpreter.
    assert _run(tmp_path, "finish")["calls"] == 0
    assert _state(tmp_path) == final
