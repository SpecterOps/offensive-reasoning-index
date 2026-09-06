"""Private repair execution accounting, independent of semantic finalization."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import socket
import subprocess
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from inspect_ai.tool import tool
from pydantic import ValidationError

from ori.eval import mcp_runtime
from ori.eval.adapter import ModelResponse
from ori.eval.v2 import campaign_runner as runner
from ori.eval.v2 import mcp as mcp_contract
from ori.eval.v2 import model_runtime as runtime
from ori.eval.v2.campaign import build_checkpoint
from ori.eval.v2.fingerprint import canonical_sha256
from ori.eval.v2.schema import Track
from tests.support.v2_campaign import _provenance
from tests.support.v2_mcp import ORACLE, PROFILE, RESOLVER, TASK, _answer

CURRENT_CASE = None
KEY = "schema_repair_execution"
ENDPOINT = "http://repair.invalid:11434"
QUERY = "MATCH p=(a {objectid:'USER-A'})-[:MemberOf]->(b {objectid:'GROUP-B'}) RETURN p"
ARGUMENTS = {"info_type": "run", "query": QUERY}
RESULT = json.dumps(
    {
        "info_type": "run",
        "success": True,
        "data": {
            "nodes": {"0": {"objectid": "USER-A"}, "1": {"objectid": "GROUP-B"}},
            "edges": [{"source": "0", "target": "1", "kind": "MemberOf"}],
        },
    }
)
TERMINAL = {"done": True, "prompt_eval_count": 11, "eval_count": 7}


def _receipt(invocations=0, responses=0, status="not_started"):
    return {
        "schema_version": "ori-schema-repair-execution-v1",
        "transport_invocations": invocations,
        "responses_received": responses,
        "status": status,
    }


def _response(text, *, error=None, metrics=None):
    return ModelResponse(
        raw_text=text,
        cypher=None,
        parse_stage="raw_text",
        tokens_input=11,
        tokens_output=7,
        elapsed_seconds=0.01,
        model="ollama/synthetic-model",
        error=error,
        provider_metrics=metrics or {},
    )


@contextmanager
def _case(case, tmp_path, monkeypatch):
    global CURRENT_CASE
    CURRENT_CASE = case
    root = tmp_path / case
    root.mkdir()
    with monkeypatch.context() as patch:
        s = SimpleNamespace(
            root=root,
            patch=patch,
            counts=Counter(),
            requests=[],
            clients=[],
            frames=[],
            tools=[],
            repair_kwargs=[],
        )
        patch.setattr(os, "environ", {})
        patch.setattr(Path, "home", classmethod(lambda cls: root))
        patch.setattr(platform, "platform", lambda: "Synthetic-OS")
        patch.setattr(platform, "mac_ver", lambda: ("0", ("", "", ""), "synthetic"))

        def denied(*args, **kwargs):
            s.counts["external"] += 1
            raise AssertionError("REPAIR_ACCOUNTING_EXTERNAL_OPERATION")

        for owner, name in (
            (socket.socket, "connect"),
            (socket.socket, "connect_ex"),
            (socket, "getaddrinfo"),
            (subprocess, "Popen"),
        ):
            patch.setattr(owner, name, denied)
        from ori.eval import codex_oauth

        patch.setattr(codex_oauth, "_codex_file_token", denied)
        original_init, original_close = httpx.AsyncClient.__init__, httpx.AsyncClient.aclose

        def receive(request):
            s.requests.append(request)
            s.counts["request"] += 1
            assert len(s.requests) <= len(s.frames), "UNEXPECTED_REPAIR_REQUEST"
            frame = s.frames[len(s.requests) - 1]
            if isinstance(frame, httpx.AsyncByteStream):
                return httpx.Response(200, stream=frame)
            return httpx.Response(200, content="\n".join(json.dumps(v) for v in frame) + "\n")

        def initialize(client, *args, **kwargs):
            assert kwargs.get("follow_redirects") is False
            kwargs.update(transport=httpx.MockTransport(receive), trust_env=False)
            original_init(client, *args, **kwargs)
            s.clients.append(client)

        async def close(client):
            s.counts["close"] += 1
            await original_close(client)

        patch.setattr(httpx.AsyncClient, "__init__", initialize)
        patch.setattr(httpx.AsyncClient, "aclose", close)
        try:
            yield s
        finally:
            CURRENT_CASE = None
            assert s.counts["external"] == 0
            assert all(client.is_closed for client in s.clients)


def _task(**bounds):
    limit = TASK.binding.bounds.model_copy(update=bounds)
    return TASK.model_copy(
        deep=True,
        update={
            "binding": TASK.binding.model_copy(
                update={"mcp_tool_loop": "native-ollama", "bounds": limit}
            ),
            "acceptance_spec": TASK.acceptance_spec.model_copy(update={"bounds": limit}),
        },
    )


def _tools(s):
    @tool(name="cypher_query")
    def cypher_query():
        async def execute(info_type: str, query: str) -> str:
            """Read a synthetic path.

            Args:
                info_type: Operation name.
                query: Read-only query.
            """
            s.tools.append({"info_type": info_type, "query": query})
            return RESULT

        return execute

    return [cypher_query()]


def _clock(s):
    """Alter only runtime deadline observations, never the event-loop scheduler."""
    original = asyncio
    state = SimpleNamespace(offset=0.0)

    class Proxy:
        def __getattr__(self, name):
            return getattr(original, name)

        def get_running_loop(self):
            loop = original.get_running_loop()
            return SimpleNamespace(time=lambda: loop.time() + state.offset)

    s.patch.setattr(runtime, "asyncio", Proxy())
    return state


class _StalledStream(httpx.AsyncByteStream):
    def __init__(self, s):
        self.s = s
        self.entered = asyncio.Event()

    async def __aiter__(self):
        self.entered.set()
        try:
            await asyncio.Future()
        finally:
            await asyncio.sleep(0)
            self.s.counts["stream_cleanup"] += 1
        yield b"unreachable"


async def _run(s, task, transport=None):
    kwargs = {} if transport is None else {"transport": transport}
    return await runtime.run_mcp_model_task_v2(
        task=task,
        oracle=ORACLE,
        resolver=RESOLVER,
        profile=PROFILE,
        bundle=mcp_runtime.MCPServerBundle(tools=_tools(s)),
        model="ollama/synthetic-model",
        model_base_url=ENDPOINT,
        tool_loop="native-ollama",
        max_steps=4,
        ollama_options={"num_predict": 37},
        **kwargs,
    )


async def _native(s, variant):
    malformed = "synthetic-prefix " + json.dumps(_answer())
    task = _task()
    if variant == 9:
        task = _task(max_output_bytes=2048)
        malformed = "synthetic-overflow " * 150 + malformed
    s.frames = [
        [
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "first-call",
                            "function": {"name": "cypher_query", "arguments": deepcopy(ARGUMENTS)},
                        }
                    ],
                }
            },
            TERMINAL,
        ],
        [{"message": {"content": malformed}}, {**TERMINAL, "done_reason": "length"}],
        [{"message": {"content": json.dumps(_answer())}}, TERMINAL],
    ]
    if variant in {14, 15}:
        stall = _StalledStream(s)
        s.frames[2] = stall
    if variant == 14:
        clock = _clock(s)
        original_retry = runtime._schema_only_retry

        async def deadline(**kwargs):
            # The actual wait_for remains real; a small positive budget is supplied
            # by the runtime-only clock just after the initial real loop returns.
            return await original_retry(**kwargs)

        original_loop = runtime._run_ollama_mcp_loop

        async def initial(**kwargs):
            value = await original_loop(**kwargs)
            clock.offset = task.binding.bounds.timeout_seconds - 0.05
            return value

        s.patch.setattr(runtime, "_run_ollama_mcp_loop", initial)
        s.patch.setattr(runtime, "_schema_only_retry", deadline)
    if variant == 15:
        pending = asyncio.create_task(_run(s, task))
        await asyncio.wait_for(stall.entered.wait(), timeout=5)
        pending.cancel()
        with pytest.raises(runtime.V2ModelTaskCancelled) as caught:
            await pending
        sample, record = caught.value.sample, caught.value.provider
    else:
        outcome, record = await _run(s, task)
        sample = outcome.sample
    assert len(s.requests) == 3
    assert s.tools == [ARGUMENTS]
    assert len(record.mcp_tool_receipts) == 1
    assert record.mcp_tool_receipts[0].arguments == ARGUMENTS
    assert record.mcp_tool_receipts[0].result_text == RESULT
    assert record.transcript_digest == canonical_sha256(record.mcp_transcript)
    assert any(
        m.get("role") == "tool" and m.get("content") == RESULT for m in record.mcp_transcript
    )
    for request in s.requests:
        assert request.method == "POST" and str(request.url) == ENDPOINT + "/api/chat"
        assert "authorization" not in request.headers
        body = json.loads(request.content)
        assert body["model"] == "synthetic-model" and body["stream"] is True
        assert body["messages"][0] == {"role": "system", "content": runtime.mcp_system_prompt(task)}
        assert body["messages"][1] == {"role": "user", "content": task.question}
    repair = json.loads(s.requests[2].content)
    assert "tools" not in repair
    assert repair["messages"][-1] == {
        "role": "user",
        "content": runtime.SCHEMA_ONLY_RETRY_INSTRUCTION,
    }
    assert (
        next(m for m in repair["messages"] if m.get("tool_calls"))["tool_calls"][0]["function"][
            "arguments"
        ]
        == ARGUMENTS
    )
    if variant in {14, 15}:
        assert s.counts["stream_cleanup"] == 1
        assert (record.tokens_input, record.tokens_output) == (22, 14)
    else:
        assert (record.tokens_input, record.tokens_output) == (33, 21)
    return task, sample, record


async def _synthetic(s, index):
    task = _task(max_transcript_bytes=4096) if index == 10 else _task()
    clock = _clock(s) if index == 3 else None
    gate = asyncio.Event()

    async def initial(**kwargs):
        if index == 16:
            gate.set()
            await asyncio.Future()
        if index == 2:
            raise httpx.ConnectError("synthetic initial unavailable")
        if index != 1:
            kwargs["tool_result_observer"]("cypher_query", deepcopy(ARGUMENTS), RESULT, None)
        if clock is not None:
            clock.offset = task.binding.bounds.timeout_seconds + 1
        text = json.dumps(_answer()) if index == 0 else "synthetic-prefix " + json.dumps(_answer())
        messages = [{"role": "assistant", "content": text}]
        if index == 4:
            messages[0]["tool_calls"] = [{"function": {"name": "cypher_query", "arguments": "[]"}}]
        return _response(text), object(), messages

    async def transport(**kwargs):
        s.repair_kwargs.append(deepcopy(kwargs))
        if index == 12:
            raise httpx.ConnectError("synthetic repair unavailable")
        if index == 13:
            raise RuntimeError("synthetic repair defect")
        text = {
            6: "malformed",
            7: '{"bad": NaN}',
            8: json.dumps(_answer(relationship="GenericAll")),
        }.get(index, json.dumps(_answer()))
        if index == 10:
            text += " " * 5000
        if index == 11:
            return _response(
                "",
                error="synthetic auth failure",
                metrics={
                    "infra_scope": "provider",
                    "infra_error_subtype": "PROVIDER_AUTH",
                    "infra_retryable": False,
                },
            )
        return _response(text)

    s.patch.setattr(runtime, "_run_ollama_mcp_loop", initial)
    if index == 17:

        async def before_transport(**kwargs):
            gate.set()
            await asyncio.Future()

        s.patch.setattr(runtime, "_schema_only_retry", before_transport)
    if index in {16, 17}:
        pending = asyncio.create_task(_run(s, task, transport))
        await asyncio.wait_for(gate.wait(), timeout=5)
        pending.cancel()
        with pytest.raises(runtime.V2ModelTaskCancelled) as caught:
            await pending
        return task, caught.value.sample, caught.value.provider
    outcome, record = await _run(s, task, transport)
    return task, outcome.sample, record


def test_schema_repair_execution_lifecycle(monkeypatch, tmp_path, subtests):
    ids = tuple(f"A{i:02}" for i in range(18))
    visited = []
    for case in ids:
        visited.append(case)
        with subtests.test(case=case), _case(case, tmp_path, monkeypatch) as s:
            i = int(case[1:])
            task, sample, record = asyncio.run(
                _native(s, i) if i in {5, 9, 14, 15} else _synthetic(s, i)
            )
            actual = record.provider_metrics[KEY]
            expected = (
                _receipt()
                if i in {0, 1, 2, 16}
                else _receipt(status="timed_out")
                if i == 3
                else _receipt(status="preparation_failed")
                if i == 4
                else _receipt(status="interrupted")
                if i == 17
                else _receipt(1, 0, "timed_out")
                if i == 14
                else _receipt(1, 0, "interrupted")
                if i == 15
                else _receipt(1, 0, "raised")
                if i in {12, 13}
                else _receipt(1, 1, "returned")
            )
            assert actual["transport_invocations"] == expected["transport_invocations"]
            assert actual == expected
            assert runtime.ProviderRunRecord.model_validate_json(record.model_dump_json()) == record
            if i in {0, 5}:
                assert sample.reasoning_correct is True
            elif i in {3, 14}:
                assert sample.outcome.value == "TASK_TIMEOUT"
                assert record.mcp_finalization["schema_retry_count"] == 0
            elif i in {2, 11, 12}:
                assert sample.execution_class.value == "infra_failure"
            elif i in {4, 13}:
                assert sample.execution_class.value == "harness_failure"
            elif i in {15, 16, 17}:
                assert sample.outcome.value == "INTERRUPTED"
                assert record.mcp_finalization is None
            if i in {9, 10}:
                assert sample.outcome.value == "OUTPUT_INVALID"
                assert sample.execution_class.value == "model_failure"
                assert sample.reasoning_correct is None and sample.verdict is None
                assert record.mcp_finalization["schema_retry_count"] == 0
                assert any(
                    e.kind.value == "truncated"
                    and e.reason == "MCP sample exceeded its execution bounds"
                    for e in record.mcp_events
                )
            if i == 8:
                assert "SCHEMA_RETRY_ADDED_NEW_FACTS" in record.model_dump_json()
            if i not in {5, 9, 14, 15}:
                assert len(s.repair_kwargs) == expected["transport_invocations"]
                assert not s.requests
    assert visited == list(ids)


def _record(*, metrics=None, surface="mcp_native_ollama", receipt=None):
    return runtime._record(
        task=_task(),
        model="ollama/synthetic-model",
        surface=surface,
        response=_response("synthetic-private-answer", metrics=metrics),
        schema_repair_execution=receipt,
    )


def test_schema_repair_execution_contract(monkeypatch, tmp_path, subtests):
    ids = tuple(f"B{i:02}" for i in range(10))
    visited = []
    for case in ids:
        visited.append(case)
        with subtests.test(case=case), _case(case, tmp_path, monkeypatch):
            i = int(case[1:])
            if i == 0:
                value = runtime.SchemaRepairExecutionV1.model_validate(_receipt(1, 1, "returned"))
                record = _record(metrics={KEY: _receipt()}, receipt=value)
                assert record.provider_metrics[KEY] == _receipt(1, 1, "returned")
                assert (
                    runtime.ProviderRunRecord.model_validate_json(record.model_dump_json())
                    == record
                )
                data = record.model_dump(mode="json")
                data["provider_metrics"][KEY] = _receipt()
                with pytest.raises(ValidationError, match="fingerprint"):
                    runtime.ProviderRunRecord.model_validate_json(json.dumps(data))
            elif i in {1, 2, 3, 4, 5}:
                value = _receipt()
                if i == 1:
                    value["transport_invocations"] = True
                elif i == 2:
                    value["transport_invocations"] = 2
                elif i == 3:
                    value["responses_received"] = 1
                elif i == 4:
                    value["status"] = "returned"
                else:
                    value["unexpected"] = "private"
                with pytest.raises(ValidationError):
                    runtime.SchemaRepairExecutionV1.model_validate(value)
                if i == 5:
                    complete = _receipt()
                    incomplete = [{}, *(dict(complete) for _ in complete)]
                    for missing, payload in zip(complete, incomplete[1:], strict=True):
                        del payload[missing]
                    for payload in incomplete:
                        with pytest.raises(ValidationError):
                            _record(metrics={KEY: payload})
                    with pytest.raises(ValidationError):
                        _record(metrics={KEY: runtime.SchemaRepairExecutionV1()})
            elif i == 6:
                with pytest.raises(ValidationError):
                    _record(metrics={KEY: None})
            elif i == 7:
                with pytest.raises(ValidationError):
                    _record(surface="direct", receipt=runtime.SchemaRepairExecutionV1())
            elif i == 8:
                record = _record()
                old = record.model_dump(mode="json")
                assert KEY not in old["provider_metrics"]
                original_hash = old["record_fingerprint"]
                reloaded = runtime.ProviderRunRecord.model_validate_json(json.dumps(old))
                assert reloaded.model_dump(mode="json") == old
                assert reloaded.record_fingerprint == original_hash
            else:
                state = mcp_contract.initial_finalization_state(
                    _task(), PROFILE, tool_loop="native-ollama"
                )
                event = mcp_contract.classify_evidence_event(
                    _task(),
                    PROFILE,
                    kind=mcp_contract.EvidenceEventKind.USEFUL_POSITIVE,
                    tool_name="cypher_query",
                    operation="run",
                )
                state = mcp_contract.reduce_finalization(state, event)
                state = mcp_contract.reduce_finalization(
                    state,
                    mcp_contract.FinalizationAttempt(
                        status=mcp_contract.FinalOutputStatus.MALFORMED
                    ),
                )
                assert state.schema_retry_count == 1
                assert KEY not in state.model_dump(mode="json")
    assert visited == list(ids)


def _persist(s, task, sample, record):
    provenance = _provenance(Track.MCP)
    pair = SimpleNamespace(
        public=SimpleNamespace(
            tasks=(task,),
            product=task.product,
            track=Track.MCP,
            artifact_fingerprint="a" * 64,
            catalog_fingerprint="b" * 64,
            graph_fingerprint="c" * 64,
            compiler_fingerprint="d" * 64,
        ),
        private=SimpleNamespace(artifact_fingerprint="e" * 64, oracles=(ORACLE,)),
    )
    checkpoint = build_checkpoint(pair, PROFILE, provenance.run_identity, results=(sample,))
    attempt = runner._attempt(
        task.task_id,
        1,
        sample,
        record,
        started_at_utc="2026-09-06T00:00:00+00:00",
        completed_at_utc="2026-09-06T00:00:01+00:00",
    )
    state = runner._state(
        provenance=provenance,
        checkpoint=checkpoint,
        attempts=(attempt,),
        scheduler=runner.RetrySchedulerStateV2(phase="complete"),
    )
    prepared = SimpleNamespace(
        pair=pair,
        profile=PROFILE,
        task_ids=(task.task_id,),
        selected_task_ids=None,
        certifications=(),
        release=SimpleNamespace(release_fingerprint="f" * 64),
        live=SimpleNamespace(artifact_fingerprint="1" * 64),
    )
    path = s.root / runner.RUN_STATE_NAME
    runner._atomic_write(path, state.model_dump(mode="json"))
    reloaded = runner._load_state(path, provenance=provenance, prepared=prepared)
    assert reloaded == state
    return provenance, prepared, reloaded


def test_schema_repair_execution_integration(monkeypatch, tmp_path, subtests):
    ids = tuple(f"C{i:02}" for i in range(4))
    visited = []
    for case in ids:
        visited.append(case)
        with subtests.test(case=case), _case(case, tmp_path, monkeypatch) as s:
            i = int(case[1:])
            if i == 2:
                path = Path(runtime.__file__)
                assert runner._RUNNER_IMPLEMENTATION_SOURCES["model_runtime"] == path
                assert mcp_contract._MCP_FINALIZATION_SOURCES["model_runtime"] == path
                for sources, fingerprint in (
                    (
                        runner._RUNNER_IMPLEMENTATION_SOURCES,
                        runner.RUNNER_IMPLEMENTATION_FINGERPRINT,
                    ),
                    (
                        mcp_contract._MCP_FINALIZATION_SOURCES,
                        mcp_contract.MCP_FINALIZATION_POLICY_FINGERPRINT,
                    ),
                ):
                    values = {
                        name: hashlib.sha256(p.read_bytes()).hexdigest()
                        for name, p in sorted(sources.items())
                    }

                    def fingerprint_payload():
                        if sources is mcp_contract._MCP_FINALIZATION_SOURCES:
                            return {
                                "component": mcp_contract.MCP_EVIDENCE_STATE_MACHINE_VERSION,
                                "source_sha256": values,
                            }
                        return values

                    assert canonical_sha256(fingerprint_payload()) == fingerprint
                    values["model_runtime"] = "0" * 64
                    assert canonical_sha256(fingerprint_payload()) != fingerprint
                continue
            task, sample, record = asyncio.run(_native(s, 15 if i == 1 else 5))
            provenance, prepared, state = _persist(s, task, sample, record)
            expected = _receipt(1, 0, "interrupted") if i == 1 else _receipt(1, 1, "returned")
            assert state.attempts[0].provider.provider_metrics[KEY] == expected
            if i == 1:
                assert state.checkpoint.results[0].outcome.value == "INTERRUPTED"
                assert state.attempts[0].provider.mcp_tool_receipts == record.mcp_tool_receipts
                assert state.attempts[0].provider.mcp_transcript == record.mcp_transcript
            elif i == 0:
                public = runner._model_report(
                    provenance=provenance,
                    prepared=prepared,
                    results=(sample,),
                    run_dir=s.root,
                    before=SimpleNamespace(verification_fingerprint="2" * 64),
                    after=SimpleNamespace(verification_fingerprint="3" * 64),
                )
                metrics = public.operational_metrics
                assert (metrics.attempts_total, metrics.retries_total) == (1, 0)
                assert (metrics.tokens_input_total, metrics.tokens_output_total) == (33, 21)
                for sentinel in (KEY, QUERY, RESULT, ENDPOINT, "synthetic-prefix"):
                    assert sentinel not in public.model_dump_json()
                assert KEY in state.model_dump_json()
            else:
                original = (s.root / runner.RUN_STATE_NAME).read_bytes()
                payload = provenance.model_dump(mode="json")
                payload["runtime_implementation_fingerprint"] = "0" * 64
                payload["provenance_fingerprint"] = canonical_sha256(
                    payload, exclude_fields=("provenance_fingerprint",)
                )
                stale = type(provenance).model_validate_json(json.dumps(payload))
                with pytest.raises(runner.V2CampaignRunError, match="different provenance"):
                    runner._load_state(
                        s.root / runner.RUN_STATE_NAME, provenance=stale, prepared=prepared
                    )
                assert (s.root / runner.RUN_STATE_NAME).read_bytes() == original
    assert visited == list(ids)
