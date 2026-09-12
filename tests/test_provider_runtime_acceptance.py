"""Actual adapter failures crossing legacy and durable V2 runtime boundaries."""

import asyncio
import json
import os
import socket
import subprocess
from types import SimpleNamespace

import openai
import yaml

from ori.eval import adapter, codex_oauth, grader, runner
from ori.eval.bhce import CypherResult
from ori.eval.tasks import Task
from ori.eval.v2 import campaign_runner
from ori.eval.v2.campaign_config import load_v2_campaign_config
from ori.eval.v2.model_runtime import run_direct_model_task_v2
from ori.eval.v2.profiles import capability_profile_for_track
from ori.eval.v2.protocol import V2ArtifactPair, build_artifacts
from ori.eval.v2.schema import ExecutionClass, Track
from ori.eval.v2.scoring import SampleOutcomeCode, summarize_results
from tests.support.v2_campaign import _config
from tests.support.v2_compiler import simple_compiled  # noqa: F401 -- shared fixture
from tests.support.v2_direct import ORACLE, RESOLVER, TASK


def _deny(*args, **kwargs):
    raise AssertionError("offline runtime acceptance attempted external access")


def _isolate(monkeypatch):
    for key in tuple(os.environ):
        monkeypatch.delenv(key)
    monkeypatch.setenv("OPENAI_COMPAT_API_KEY", "synthetic")
    monkeypatch.setattr(socket.socket, "connect", _deny)
    monkeypatch.setattr(socket.socket, "connect_ex", _deny)
    monkeypatch.setattr(socket, "getaddrinfo", _deny)
    monkeypatch.setattr(subprocess, "Popen", _deny)
    monkeypatch.setattr(codex_oauth, "_installation_id", lambda: "synthetic-installation")
    monkeypatch.setattr(codex_oauth, "_session_id", lambda: "synthetic-session")


class _NoGraph:
    circuit_open = False
    execute = staticmethod(_deny)
    wait_until_healthy = staticmethod(_deny)

    def require_confirmed_native_completion(self) -> None:
        # A local cache check is not a graph operation; this fixture has no native work.
        return None


def test_v1_adapter_internal_fault_retains_legacy_artifact_contract(monkeypatch):
    _isolate(monkeypatch)
    calls = []

    async def create(**kwargs):
        calls.append(kwargs)
        raise ValueError("S1-05F-legacy")

    monkeypatch.setattr(
        openai,
        "AsyncOpenAI",
        lambda **kwargs: SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        ),
    )
    task = Task("synthetic", "synthetic", 1, "path_finding", "synthetic?", "", "path_exists")
    response = asyncio.run(
        adapter.call_model(task, "openai-compat/test", "https://provider.invalid/v1")
    )
    result = CypherResult(success=False, query_executed=False, execution_attempts=0)
    grade = grader.grade(task, response, result, result, set())
    payload = runner._direct_result_to_checkpoint(
        runner.EvalResult(task, response, grade, result, result)
    )
    restored = runner._direct_result_from_checkpoint(json.loads(json.dumps(payload)))
    assert runner._direct_result_to_checkpoint(restored) == payload
    assert set(payload["model_response"]) == {
        "raw_text",
        "cypher",
        "parse_stage",
        "tokens_input",
        "tokens_output",
        "elapsed_seconds",
        "model",
        "thinking",
        "error",
        "provider_metrics",
    }
    assert restored.grade.outcome == "MODEL_ERROR"
    assert restored.model_response.error == "S1-05F-legacy"
    assert restored.model_response.cypher is None
    assert len(calls) == 1


def test_codex_auth_failure_is_nonretryable_direct_infrastructure(tmp_path, monkeypatch):
    _isolate(monkeypatch)
    path = tmp_path / "missing-auth.json"
    monkeypatch.setenv("CODEX_AUTH_FILE", str(path))
    monkeypatch.setattr(openai, "AsyncOpenAI", _deny)
    _, sample, record = asyncio.run(
        run_direct_model_task_v2(
            task=TASK,
            oracle=ORACLE,
            resolver=RESOLVER,
            coordinator=_NoGraph(),
            model="codex/synthetic",
            model_base_url="https://chatgpt.com/backend-api/codex",
        )
    )
    assert sample.execution_class is ExecutionClass.INFRA_FAILURE
    assert sample.outcome is SampleOutcomeCode.INFRA_ERROR
    assert sample.reasoning_correct is None
    assert record.provider_metrics["infra_error_subtype"] == "PROVIDER_AUTH"
    assert str(path) not in record.provider_error
    assert campaign_runner._infrastructure_retry_policy(sample, record) == ("provider", False)


def test_adapter_fault_campaign_persists_continues_and_resumes_terminal(
    tmp_path, monkeypatch, request
):
    _isolate(monkeypatch)
    config_path = _config(tmp_path, (Track.DIRECT,))
    payload = yaml.safe_load(config_path.read_text())
    payload["models"] = [
        {
            "name": "adapter-fault",
            "provider": "openai-compat",
            "model": "test-model",
            "model_base_url": "https://provider.invalid/v1",
        }
    ]
    payload["defaults"].update(
        {
            "bhce_url": "https://graph.invalid",
            "max_infra_retries": 2,
            "infra_retry": {"immediate_retries": 1, "deferred_cooldown_seconds": 300},
        }
    )
    config_path.write_text(yaml.safe_dump(payload))
    resolved = load_v2_campaign_config(config_path)
    _, snapshot, corpus, _ = request.getfixturevalue("simple_compiled")
    public, private = build_artifacts(corpus, identity_catalog=snapshot.entities)
    task_ids = tuple(task.task_id for task in public.tasks[:2])
    assert len(set(task_ids)) == 2
    prepared = campaign_runner.PreparedTrack(
        track=Track.DIRECT,
        pair=V2ArtifactPair(public=public, private=private),
        profile=capability_profile_for_track(Track.DIRECT),
        release=SimpleNamespace(
            entries=tuple(SimpleNamespace(task_id=task_id) for task_id in task_ids),
            release_fingerprint="a" * 64,
        ),
        live=SimpleNamespace(artifact_fingerprint="b" * 64),
        certifications={},
    )
    calls = []

    async def create(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise ValueError("S1-05F-durable")
        return {
            "id": "synthetic",
            "model": "test-model",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "{}"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        }

    monkeypatch.setattr(
        openai,
        "AsyncOpenAI",
        lambda **kwargs: SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        ),
    )
    arguments = dict(
        resolved=resolved,
        prepared=prepared,
        model=resolved.config.models[0],
        run_index=1,
        bhce=_NoGraph(),
        coordinator=_NoGraph(),
        loop=None,
        runs_total=1,
    )
    _, samples = asyncio.run(campaign_runner._run_model(**arguments))
    state_path = (
        resolved.output_dir / "direct/adapter-fault/run-001" / campaign_runner.RUN_STATE_NAME
    )
    state = campaign_runner.PrivateRunStateV2.model_validate_json(state_path.read_text())
    assert len(calls) == 2
    for call, task in zip(calls, public.tasks[:2], strict=True):
        assert task.question in call["messages"][-1]["content"]
    assert len(state.attempts) == 2
    assert tuple(attempt.task_id for attempt in state.attempts) == task_ids
    assert all(
        attempt.attempt == 1
        and attempt.scheduler_phase == "initial"
        and attempt.recovery_round == 0
        for attempt in state.attempts
    )
    first, second = state.attempts
    assert first.sample.execution_class is ExecutionClass.HARNESS_FAILURE
    assert first.sample.outcome is SampleOutcomeCode.HARNESS_ERROR
    assert first.sample.reasoning_correct is None
    assert campaign_runner._infrastructure_retry_policy(first.sample, first.provider) is None
    assert second.sample.execution_class is ExecutionClass.MODEL_FAILURE
    assert second.sample.outcome is SampleOutcomeCode.OUTPUT_INVALID
    assert state.scheduler.phase == "complete"
    assert state.scheduler.pending_task_ids == ()
    assert state.scheduler.deferred_not_before_utc is None
    summary = summarize_results(task_ids, samples)
    assert (
        summary.scheduled,
        summary.harness_failures,
        summary.model_failures,
        summary.infrastructure_failures,
    ) == (2, 1, 1, 0)
    assert summary.campaign_valid is False
    metrics = campaign_runner._run_operational_metrics(state)
    assert (metrics.attempts_total, metrics.retries_total) == (2, 0)
    monkeypatch.setattr(openai, "AsyncOpenAI", _deny)
    _, resumed = asyncio.run(campaign_runner._run_model(**arguments))
    restored = campaign_runner.PrivateRunStateV2.model_validate_json(state_path.read_text())
    assert resumed == samples
    assert restored == state
