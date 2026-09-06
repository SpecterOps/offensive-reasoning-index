"""Finite real-wire Ollama framing and consumer-admission scenarios."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import platform
import socket
import subprocess
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
from functools import partial
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from inspect_ai.tool import tool

from ori.eval import adapter, mcp_runtime
from ori.eval.v2 import campaign_runner as runner
from ori.eval.v2 import mcp as mcp_contract
from ori.eval.v2 import model_runtime
from ori.eval.v2.campaign import build_public_report
from ori.eval.v2.schema import Track, canonical_sha256
from ori.eval.v2.scoring import summarize_results
from tests.support.v2_campaign import _provenance
from tests.support.v2_campaign import _state as _fixture_state
from tests.support.v2_mcp import ORACLE, PROFILE, RESOLVER, TASK, _answer

CURRENT_CASE = None
ENDPOINT = "http://stream.invalid:11434"
CONTENT = "synthetic-stream-answer"
THINKING = "synthetic-thinking"
OPTIONS = {"num_predict": 37, "temperature": 0}
QUERY = "MATCH p=(a {objectid:'USER-A'})-[:MemberOf]->(b {objectid:'GROUP-B'}) RETURN p"
ARGUMENTS = {"info_type": "run", "query": QUERY}
CALL = {"id": "first-call", "function": {"name": "cypher_query", "arguments": ARGUMENTS}}
TERMINAL = {
    "done": True,
    "prompt_eval_count": 11,
    "eval_count": 7,
    "total_duration": 100,
    "load_duration": 10,
    "prompt_eval_duration": 30,
    "eval_duration": 60,
}


def _public_diagnostic(detail, *, sample=None):
    sample = sample or model_runtime._model_infrastructure_sample(TASK, ORACLE, detail)
    pair = SimpleNamespace(
        public=SimpleNamespace(
            tasks=(TASK,),
            product="complex",
            track=Track.MCP,
            artifact_fingerprint="a" * 64,
            catalog_fingerprint="b" * 64,
            graph_fingerprint="c" * 64,
        )
    )
    report = build_public_report(
        pair, PROFILE, [sample], summarize_results([TASK.task_id], [sample])
    )
    encoded = report.model_dump_json()
    for sentinel in (
        CONTENT,
        ENDPOINT,
        "synthetic-error-secret",
        "synthetic-metric-secret",
        "synthetic-answer-prefix",
        "synthetic-overflow",
        QUERY,
        RESULT_TEXT,
    ):
        assert sentinel not in encoded


def _public_record(state, task, sample, record, truncated_source):
    assert record.provider_metrics["initial"]["truncated_output_text"] == truncated_source
    assert any(m.get("content") == truncated_source for m in record.mcp_transcript)
    provenance = _provenance(Track.MCP)
    template = _fixture_state(provenance, Track.MCP).checkpoint
    data = template.model_dump()
    data["task_bindings"] = (
        template.task_bindings[0].model_copy(
            update={
                "task_id": sample.task_id,
                "task_fingerprint": sample.task_fingerprint,
                "oracle_fingerprint": sample.oracle_fingerprint,
            }
        ),
    )
    data["results"] = (sample,)
    data["checkpoint_fingerprint"] = canonical_sha256(
        data, exclude_fields=("checkpoint_fingerprint",)
    )
    checkpoint = type(template).model_validate(data)
    attempt = runner._attempt(
        task.task_id,
        1,
        sample,
        record,
        started_at_utc="2026-09-06T00:00:00+00:00",
        completed_at_utc="2026-09-06T00:00:01+00:00",
    )
    private = runner._state(
        provenance=provenance,
        checkpoint=checkpoint,
        attempts=(attempt,),
        scheduler=runner.RetrySchedulerStateV2(phase="complete"),
    )
    assert "truncated_output_text" in private.model_dump_json()
    pair = SimpleNamespace(
        public=SimpleNamespace(
            tasks=(task,),
            product="complex",
            track=Track.MCP,
            artifact_fingerprint="a" * 64,
            catalog_fingerprint="b" * 64,
            graph_fingerprint="c" * 64,
        )
    )
    prepared = SimpleNamespace(
        task_ids=(task.task_id,),
        pair=pair,
        profile=PROFILE,
        certifications=(),
        release=SimpleNamespace(release_fingerprint="d" * 64),
        live=SimpleNamespace(artifact_fingerprint="e" * 64),
    )

    def load(path, **kwargs):
        assert path == state.root / runner.RUN_STATE_NAME
        assert kwargs == {"provenance": provenance, "prepared": prepared}
        state.counts["publication_state_load"] += 1
        return private

    state.patch.setattr(runner, "_load_state", load)
    public = runner._model_report(
        provenance=provenance,
        prepared=prepared,
        results=(sample,),
        run_dir=state.root,
        before=SimpleNamespace(verification_fingerprint="f" * 64),
        after=SimpleNamespace(verification_fingerprint="1" * 64),
    )
    assert state.counts["publication_state_load"] == 1
    assert public.operational_metrics.tokens_input_total == record.tokens_input
    assert public.operational_metrics.tokens_output_total == record.tokens_output
    assert public.operational_metrics.mcp_tool_calls_total == 1
    encoded = public.model_dump_json()
    for sentinel in (
        "truncated_output_text",
        "synthetic-answer-prefix",
        "synthetic-overflow",
        truncated_source,
        QUERY,
        RESULT_TEXT,
        ENDPOINT,
    ):
        assert sentinel not in encoded


def _stream_module():
    return importlib.import_module("ori.eval.ollama_stream")


def _lines(*values):
    return [v if isinstance(v, str) else json.dumps(v) for v in values]


@contextmanager
def _case(case, tmp_path, monkeypatch):
    global CURRENT_CASE
    CURRENT_CASE = case
    root = tmp_path / case
    root.mkdir()
    original_init = httpx.AsyncClient.__init__
    original_close = httpx.AsyncClient.aclose
    original_exit = httpx.AsyncClient.__aexit__
    with monkeypatch.context() as patch:
        state = SimpleNamespace(
            root=root,
            patch=patch,
            streams=[],
            tool_calls=[],
            requests=[],
            transports=[],
            transport_kwargs=[],
            counts=Counter(),
        )
        patch.setattr(os, "environ", {"OPENAI_COMPAT_API_KEY": "synthetic-redirect-key"})
        patch.setattr(Path, "home", classmethod(lambda cls: root))
        patch.setattr(platform, "platform", lambda: "Synthetic-OS")
        patch.setattr(platform, "mac_ver", lambda: ("0", ("", "", ""), "synthetic"))

        def denied(*args, **kwargs):
            state.counts["external"] += 1
            raise AssertionError("OLLAMA_STREAM_EXTERNAL_OPERATION")

        for owner, name in (
            (socket.socket, "connect"),
            (socket.socket, "connect_ex"),
            (socket, "getaddrinfo"),
            (subprocess, "Popen"),
        ):
            patch.setattr(owner, name, denied)
        for name in (
            "ChatMessageSystem",
            "ChatMessageUser",
            "ChatMessageAssistant",
            "ChatMessageTool",
        ):
            original = getattr(mcp_runtime, name)

            def initialize(self, *args, _class=original, _name=name, **kwargs):
                state.counts[_name] += 1
                _class.__init__(self, *args, id=f"{_name}-{state.counts[_name]}", **kwargs)

            fixed = type(name, (original,), {"__init__": initialize})
            state.patch.setattr(mcp_runtime, name, fixed)

        def receive(request):
            state.requests.append(request)
            state.counts["request"] += 1
            index = len(state.requests) - 1
            assert index < len(state.streams), "UNEXPECTED_OLLAMA_REQUEST"
            stream = state.streams[index]
            if isinstance(stream, dict):
                return httpx.Response(200, json=stream)
            if isinstance(stream, httpx.AsyncByteStream):
                return httpx.Response(200, stream=stream)
            return httpx.Response(200, content="\n".join(stream) + "\n")

        state.receive = receive

        def initialize_httpx(client, *args, **kwargs):
            state.counts["transport"] += 1
            state.transport_kwargs.append(dict(kwargs))
            kwargs["transport"] = httpx.MockTransport(state.receive)
            kwargs["trust_env"] = False
            original_init(client, *args, **kwargs)
            state.transports.append(client)

        async def close(client):
            state.counts["transport_close"] += 1
            await original_close(client)

        async def context_exit(client, *args):
            state.counts["transport_context_exit"] += 1
            await original_exit(client, *args)

        patch.setattr(httpx.AsyncClient, "__init__", initialize_httpx)
        patch.setattr(httpx.AsyncClient, "aclose", close)
        patch.setattr(httpx.AsyncClient, "__aexit__", context_exit)
        try:
            yield state
        finally:
            CURRENT_CASE = None
            assert state.counts["external"] == 0
            assert all(client.is_closed for client in state.transports)


async def _direct():
    return await adapter.call_provider_text(
        model="ollama/synthetic-model",
        base_url=ENDPOINT,
        system="synthetic-system",
        messages=[{"role": "user", "content": "synthetic-question"}],
        ollama_options=deepcopy(OPTIONS),
    )


async def _native(**kwargs):
    return await mcp_runtime._ollama_chat_turn(
        url=mcp_runtime._native_ollama_chat_url(ENDPOINT),
        model_name="ollama/synthetic-model",
        messages=[{"role": "user", "content": "synthetic-question"}],
        tools=[],
        ollama_options=deepcopy(OPTIONS),
        **kwargs,
    )


def _assert_request(state, *, direct):
    assert len(state.requests) == 1
    request = state.requests[0]
    assert str(request.url) == ENDPOINT + "/api/chat"
    assert request.method == "POST"
    body = json.loads(request.content)
    assert body["model"] == "synthetic-model" and body["stream"] is True
    assert body["options"] == OPTIONS
    user = {"role": "user", "content": "synthetic-question"}
    assert body["messages"] == (
        [{"role": "system", "content": "synthetic-system"}, user] if direct else [user]
    )
    assert all(client.follow_redirects is False for client in state.transports)
    assert state.counts["transport"] == state.counts["transport_context_exit"] == 1


def _wire_case(index):
    content, thinking, calls, reason = CONTENT, "", [], ""
    frame = {"message": {"content": CONTENT}}
    frames = _lines(frame, TERMINAL)
    if index == 0:
        frames, content = _lines(TERMINAL), ""
    elif index == 1:
        frames = _lines(
            "",
            {"message": {"content": "synthetic-"}},
            "   ",
            {"message": {"content": "stream-answer"}},
            TERMINAL,
            "",
        )
    elif index == 2:
        frames = _lines(
            {"message": {"thinking": "synthetic-", "tool_calls": [CALL]}},
            {"message": {"thinking": "thinking", "content": CONTENT}},
            TERMINAL,
        )
        thinking, calls = THINKING, [deepcopy(CALL)]
    elif index == 3:
        frames = _lines({**frame, "done": False}, TERMINAL)
    elif index == 5:
        frames = _lines({**TERMINAL, **frame})
    elif index == 6:
        frames, reason = _lines(frame, {**TERMINAL, "done_reason": "stop"}), "stop"
    elif index == 7:
        frames = _lines(
            {"message": {"content": CONTENT, "thinking": THINKING, "tool_calls": [CALL]}},
            {**TERMINAL, "done_reason": "length"},
        )
        content, thinking, reason = "", THINKING, "length"
    elif index == 8:
        frames = []
    elif index == 9:
        frames = _lines(frame)
    elif index == 10:
        frames = ['{"synthetic-secret":']
    elif index == 11:
        frames = _lines([])
    elif index == 12:
        frames = _lines(None)
    elif index in {13, 14, 15}:
        frames = _lines({**frame, "done": {13: "true", 14: 1, 15: None}[index]})
    elif index == 16:
        frames = _lines(TERMINAL, TERMINAL)
    elif index == 17:
        frames = _lines(TERMINAL, frame)
    elif index in {18, 19}:
        frames = _lines(
            {"error": "synthetic-error-secret", **({"done": True} if index == 19 else {})}
        )
    elif index == 20:
        frames = _lines({"message": []}, TERMINAL)
    elif index == 21:
        frames = _lines({"message": {"tool_calls": {}}}, TERMINAL)
    elif index == 22:
        frames = _lines({"message": {"tool_calls": ["bad"]}}, TERMINAL)
    elif index == 23:
        frames = _lines({**TERMINAL, "done_reason": 1})
    elif index == 24:
        frames = _lines({"message": {"tool_calls": [{"function": []}]}}, TERMINAL)
    elif index in {25, 26}:
        frames = _lines({"message": {{25: "content", 26: "thinking"}[index]: 1}}, TERMINAL)
    elif index == 27:
        frames = _lines({"model": []}, TERMINAL)
    elif index == 28:
        frames = _lines({**TERMINAL, "eval_count": "synthetic-metric-secret"})
    elif index == 29:
        reason = "future-reason"
        frames = _lines(frame, {**TERMINAL, "done_reason": reason})
    elif index == 30:
        frames = _lines(frame, {**TERMINAL, "done_reason": None})
    elif index == 31:
        frames = ['{"done": true, "ignored": NaN}']
    valid = index in {*range(8), 29, 30}
    return frames, valid, content, thinking, calls, reason


def test_ollama_stream_wire_contract(monkeypatch, tmp_path, subtests):
    ids = tuple(f"W{i:02}-{surface}" for i in range(32) for surface in ("D", "N"))
    visited = []
    for case in ids:
        visited.append(case)
        index, direct = int(case[1:3]), case.endswith("-D")
        with subtests.test(case=case), _case(case, tmp_path, monkeypatch) as s:
            frames, valid, content, thinking, calls, reason = _wire_case(index)
            s.streams = [frames]
            result, error = None, None
            try:
                result = asyncio.run(_direct() if direct else _native())
            except Exception as caught:
                error = caught
            _assert_request(s, direct=direct)
            if not valid:
                subtype = "PROVIDER_GENERATION_ERROR" if index in {18, 19} else "PROVIDER_PROTOCOL"
                if direct:
                    assert error is None and result.error is not None
                    assert result.raw_text == ""
                    assert result.provider_metrics["infra_error_subtype"] == subtype
                    assert result.provider_metrics["infra_retryable"] is False
                    detail = result.error
                else:
                    assert model_runtime._provider_infrastructure_details(error) == (subtype, False)
                    assert result is None
                    detail = str(error)
                if index in {18, 19}:
                    assert detail == "Ollama reported a generation error"
                else:
                    assert detail == "Ollama stream is incomplete or malformed"
                for sentinel in (
                    CONTENT,
                    ENDPOINT,
                    "synthetic-error-secret",
                    "synthetic-metric-secret",
                ):
                    assert sentinel not in detail
                _public_diagnostic(detail)
            elif direct:
                assert error is None and result.error is None
                assert result.raw_text == content and result.thinking == thinking
                assert (result.tokens_input, result.tokens_output) == (11, 7)
                assert result.provider_metrics["finish_reason"] == reason
                assert result.provider_metrics["provider_turn_status"] == (
                    "truncated" if index == 7 else "completed"
                )
                for key, expected in (
                    ("total_duration_ns", 100),
                    ("load_duration_ns", 10),
                    ("prompt_eval_duration_ns", 30),
                    ("eval_duration_ns", 60),
                ):
                    assert result.provider_metrics[key] == expected
                if index == 7:
                    assert result.provider_metrics["model_output_subtype"] == "TRUNCATED"
            else:
                assert error is None
                assert result["content"] == content and result["thinking"] == thinking
                assert result["tool_calls"] == calls
                assert (result["prompt_eval_count"], result["eval_count"]) == (11, 7)
                assert result["finish_reason"] == reason
                assert result["metrics"] == {
                    "total_duration_ns": 100,
                    "load_duration_ns": 10,
                    "prompt_eval_duration_ns": 30,
                    "eval_duration_ns": 60,
                }
                if index == 7:
                    assert result["provider_metrics"]["model_output_subtype"] == "TRUNCATED"
    assert visited == list(ids)


RESULT_TEXT = json.dumps(
    {
        "info_type": "run",
        "success": True,
        "data": {
            "nodes": {"0": {"objectid": "USER-A"}, "1": {"objectid": "GROUP-B"}},
            "edges": [{"source": "0", "target": "1", "kind": "MemberOf"}],
        },
    }
)


def _tools(state):
    @tool(name="cypher_query")
    def cypher_query():
        async def execute(info_type: str, query: str) -> str:
            """Read synthetic graph evidence.

            Args:
                info_type: Operation name.
                query: Read-only query.
            """
            state.tool_calls.append({"info_type": info_type, "query": query})
            return RESULT_TEXT

        return execute

    return [cypher_query()]


def _task():
    return TASK.model_copy(
        deep=True,
        update={"binding": TASK.binding.model_copy(update={"mcp_tool_loop": "native-ollama"})},
    )


async def _v2(state, task=None):
    return await model_runtime.run_mcp_model_task_v2(
        task=task or _task(),
        oracle=ORACLE,
        resolver=RESOLVER,
        profile=PROFILE,
        bundle=mcp_runtime.MCPServerBundle(tools=_tools(state)),
        model="ollama/synthetic-model",
        model_base_url=ENDPOINT,
        tool_loop="native-ollama",
        max_steps=4,
        ollama_options=deepcopy(OPTIONS),
    )


async def _loop(state):
    return await mcp_runtime._run_ollama_mcp_loop(
        task=None,
        public_question="synthetic-question",
        model_name="ollama/synthetic-model",
        base_url=ENDPOINT,
        ollama_options=deepcopy(OPTIONS),
        tools=_tools(state),
        max_steps=4,
        system_prompt_override="synthetic-system",
    )


def _assert_prior(state, record, *, tokens, task=None):
    assert state.tool_calls == [ARGUMENTS]
    assert (record.tokens_input, record.tokens_output) == tokens
    observation = {
        "tool_name": "cypher_query",
        "operation": "run",
        "succeeded": True,
        "claim_relevant": True,
        "arguments_valid": True,
        "policy_rejected": False,
        "query_timeout": False,
        "query_error": False,
        "infrastructure_failure": False,
        "result_count": 1,
        "total_count": None,
        "pages_received": 1,
        "complete": False,
        "truncated": False,
        "negative_proof": False,
        "output_bytes": len(RESULT_TEXT.encode()),
    }
    event = {
        "kind": "useful_positive",
        "task_fingerprint": TASK.task_fingerprint,
        "capability_profile_fingerprint": PROFILE.profile_fingerprint,
        "tool_name": "cypher_query",
        "operation": "run",
        "resource_uri": None,
        "reason": "claim-relevant bounded witness evidence",
    }
    receipt = {
        "sequence": 1,
        "tool_name": "cypher_query",
        "operation": "run",
        "arguments": deepcopy(ARGUMENTS),
        "result_text": RESULT_TEXT,
        "tool_error": None,
        "observation": observation,
        "event": event,
    }
    receipt["receipt_fingerprint"] = canonical_sha256(receipt)
    assert [r.model_dump(mode="json") for r in record.mcp_tool_receipts] == [receipt]
    base = {"source": None, "metadata": None}
    expected = [
        {
            **base,
            "id": "ChatMessageSystem-1",
            "role": "system",
            "content": model_runtime.mcp_system_prompt(task or _task()),
        },
        {
            **base,
            "id": "ChatMessageUser-1",
            "role": "user",
            "content": TASK.question,
            "tool_call_id": None,
        },
        {
            **base,
            "id": "ChatMessageAssistant-1",
            "role": "assistant",
            "content": "",
            "model": "ollama/synthetic-model",
            "tool_calls": [
                {
                    "id": "first-call",
                    "function": "cypher_query",
                    "arguments": deepcopy(ARGUMENTS),
                    "parse_error": None,
                    "view": None,
                    "type": "function",
                }
            ],
        },
        {
            **base,
            "id": "ChatMessageTool-1",
            "role": "tool",
            "content": RESULT_TEXT,
            "tool_call_id": "first-call",
            "function": "cypher_query",
            "error": None,
        },
    ]
    assert list(record.mcp_transcript[:4]) == expected


def test_ollama_stream_consumer_admission(monkeypatch, tmp_path, subtests):
    ids = tuple(f"C{i:02}" for i in range(11))
    visited = []
    for case in ids:
        visited.append(case)
        with subtests.test(case=case), _case(case, tmp_path, monkeypatch) as s:
            index = int(case[1:])
            malformed = "synthetic-answer-prefix " + json.dumps(_answer())
            if index == 10:
                malformed = "synthetic-overflow " * 150 + malformed
            tempting = {"message": {"content": malformed, "tool_calls": [CALL]}}
            variant = index if index < 4 else index - 4 if index < 8 else 3
            bad = _lines(tempting)
            if variant == 1:
                bad += ['{"broken":']
            elif variant == 2:
                bad += _lines({"error": "synthetic-error-secret"})
            elif variant == 3:
                bad += _lines({**TERMINAL, "done_reason": "length"})
            if index < 4:
                s.streams = [bad, _lines({"message": {"content": CONTENT}}, TERMINAL)]
                response, error = None, None
                try:
                    response = asyncio.run(_loop(s))[0]
                except Exception as caught:
                    error = caught
                assert s.tool_calls == []
                assert len(s.requests) == 1
                _assert_loop_requests(s, system="synthetic-system", question="synthetic-question")
                if index < 3:
                    subtype = "PROVIDER_GENERATION_ERROR" if index == 2 else "PROVIDER_PROTOCOL"
                    assert model_runtime._provider_infrastructure_details(error) == (subtype, False)
                else:
                    assert error is None and response.error is None and response.raw_text == ""
                    assert response.provider_metrics["model_output_subtype"] == "TRUNCATED"
                    assert not response.provider_metrics.get("loop_exhaustion_with_evidence", False)
                    assert (response.tokens_input, response.tokens_output) == (11, 7)
            elif index == 9:
                from tests.support.v2_direct import ORACLE as DIRECT_ORACLE
                from tests.support.v2_direct import RESOLVER as DIRECT_RESOLVER
                from tests.support.v2_direct import TASK as DIRECT_TASK

                class Coordinator:
                    async def execute(self, *args, **kwargs):
                        s.counts["coordinator"] += 1
                        raise AssertionError("TRUNCATED_QUERY_EXECUTED")

                s.streams = [
                    _lines(
                        {"message": {"content": json.dumps({"query": QUERY})}},
                        {**TERMINAL, "done_reason": "length"},
                    )
                ]
                _, sample, record = asyncio.run(
                    model_runtime.run_direct_model_task_v2(
                        coordinator=Coordinator(),
                        task=DIRECT_TASK,
                        oracle=DIRECT_ORACLE,
                        resolver=DIRECT_RESOLVER,
                        model="ollama/synthetic-model",
                        model_base_url=ENDPOINT,
                        ollama_options=deepcopy(OPTIONS),
                    )
                )
                assert sample.outcome.value == "OUTPUT_INVALID"
                assert s.counts["coordinator"] == 0 and len(s.requests) == 1
                assert (record.tokens_input, record.tokens_output) == (11, 7)
            else:
                task = _task()
                if index in {7, 10}:
                    limit = (
                        1024
                        if index == 10
                        else len(malformed.encode()) + len(json.dumps(_answer()).encode())
                    )
                    bounds = task.binding.bounds.model_copy(update={"max_output_bytes": limit})
                    task = task.model_copy(
                        update={
                            "binding": task.binding.model_copy(update={"bounds": bounds}),
                            "acceptance_spec": task.acceptance_spec.model_copy(
                                update={"bounds": bounds}
                            ),
                        }
                    )
                repaired = _answer(relationship="AdminTo") if index == 8 else _answer()
                s.streams = [
                    _lines({"message": {"tool_calls": [CALL]}}, TERMINAL),
                    bad,
                    _lines({"message": {"content": json.dumps(repaired)}}, TERMINAL),
                ]
                outcome, record = asyncio.run(_v2(s, task))
                _assert_loop_requests(
                    s, system=model_runtime.mcp_system_prompt(task), question=task.question
                )
                _assert_prior(s, record, tokens=(11, 7) if index < 7 else (33, 21), task=task)
                if index < 7:
                    assert len(s.requests) == 2
                    assert outcome.sample.outcome.value == "INFRA_ERROR"
                    assert record.provider_metrics["infra_retryable"] is False
                else:
                    assert len(s.requests) == 3
                    body = json.loads(s.requests[2].content)
                    assert "tools" not in body
                    prior_call = next(m for m in body["messages"] if m.get("tool_calls"))
                    assert prior_call["tool_calls"] == [
                        {
                            "id": "first-call",
                            "type": "function",
                            "function": {"name": "cypher_query", "arguments": ARGUMENTS},
                        }
                    ]
                    assert body["messages"] == [
                        {"role": "system", "content": model_runtime.mcp_system_prompt(task)},
                        {"role": "user", "content": task.question},
                        {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "first-call",
                                    "type": "function",
                                    "function": {
                                        "name": "cypher_query",
                                        "arguments": deepcopy(ARGUMENTS),
                                    },
                                }
                            ],
                        },
                        {"role": "tool", "content": RESULT_TEXT, "tool_call_id": "first-call"},
                        {"role": "assistant", "content": malformed},
                        {"role": "user", "content": model_runtime.SCHEMA_ONLY_RETRY_INSTRUCTION},
                    ]
                    if index != 10:
                        assert record.mcp_finalization["schema_retry_count"] == 1
                    if index == 7:
                        assert outcome.sample.reasoning_correct is True
                    elif index == 8:
                        assert "SCHEMA_RETRY_ADDED_NEW_FACTS" in json.dumps(
                            record.model_dump(mode="json")
                        )
                        assert outcome.sample.reasoning_correct is not True
                    else:
                        assert record.mcp_finalization["schema_retry_count"] == 0
                        assert record.provider_metrics["schema_repair_execution"] == {
                            "schema_version": "ori-schema-repair-execution-v1",
                            "transport_invocations": 1,
                            "responses_received": 1,
                            "status": "returned",
                        }
                        assert outcome.sample.reasoning_correct is None
                        assert outcome.sample.outcome.value == "OUTPUT_INVALID"
                        assert outcome.sample.execution_class.value == "model_failure"
                        assert outcome.sample.verdict is None
                        assert any(
                            e.kind.value == "truncated"
                            and e.reason == "MCP sample exceeded its execution bounds"
                            for e in record.mcp_events
                        )
                    _public_record(s, task, outcome.sample, record, malformed)
                _public_diagnostic(record.provider_error or malformed, sample=outcome.sample)
    assert visited == list(ids)


def _assert_loop_requests(state, *, system, question):
    for request in state.requests:
        assert str(request.url) == ENDPOINT + "/api/chat" and request.method == "POST"
        assert "authorization" not in request.headers and "x-api-key" not in request.headers
        body = json.loads(request.content)
        assert body["model"] == "synthetic-model" and body["stream"] is True
        assert body["options"] == OPTIONS
        assert body["messages"][:2] == [
            {"role": "system", "content": system},
            {"role": "user", "content": question},
        ]
    assert all(t.follow_redirects is False for t in state.transports)
    assert len(state.transports) == len(state.requests)


class _InterruptedStream(httpx.AsyncByteStream):
    def __init__(self, kind, *, done=False):
        self.kind, self.done = kind, done

    async def __aiter__(self):
        if self.done:
            yield (json.dumps(TERMINAL) + "\n").encode()
        if self.kind == "read":
            raise httpx.ReadTimeout("synthetic read timeout")
        if self.kind == "cancel":
            raise asyncio.CancelledError()
        await asyncio.Future()


def test_ollama_stream_runtime_boundaries(monkeypatch, tmp_path, subtests):
    ids = tuple(f"T{i:02}" for i in range(8))
    visited = []
    for case in ids:
        visited.append(case)
        with subtests.test(case=case), _case(case, tmp_path, monkeypatch) as s:
            if case in {"T00", "T01", "T02", "T03"}:
                s.streams = [
                    _InterruptedStream(
                        "read" if case == "T02" else "cancel" if case == "T03" else "stall",
                        done=case == "T01",
                    )
                ]
                if case == "T03":
                    with pytest.raises(asyncio.CancelledError):
                        asyncio.run(_native())
                else:
                    with pytest.raises(Exception) as caught:
                        asyncio.run(_native(no_progress_timeout_seconds=0.001))
                    if case == "T02":
                        assert model_runtime._provider_infrastructure_details(caught.value) == (
                            "PROVIDER_TIMEOUT",
                            True,
                        )
                    else:
                        assert isinstance(caught.value, mcp_runtime.MCPNoProgressTimeout)
                        assert caught.value.subtype == "MCP_TURN_TIMEOUT"
                assert len(s.requests) == 1 and s.tool_calls == []
            elif case == "T04":
                s.streams = [
                    _lines({"message": {"tool_calls": [CALL]}}, TERMINAL),
                    _InterruptedStream("cancel"),
                ]
                with pytest.raises(model_runtime.V2ModelTaskCancelled) as caught:
                    asyncio.run(_v2(s))
                _assert_prior(s, caught.value.provider, tokens=(11, 7))
                assert (
                    caught.value.provider.provider_metrics["infra_error_subtype"] == "INTERRUPTED"
                )
                assert caught.value.provider.provider_metrics["infra_retryable"] is False
                assert len(s.requests) == 2
            elif case == "T05":

                def defect(self, line):
                    raise RuntimeError("synthetic reducer defect")

                s.patch.setattr(_stream_module().OllamaStreamState, "feed_line", defect)
                s.streams = [_lines(TERMINAL)]
                outcome, record = asyncio.run(_v2(s))
                assert outcome.sample.outcome.value == "HARNESS_ERROR"
                assert s.tool_calls == [] and len(s.requests) == 1
                assert record.tokens_input == record.tokens_output == 0
            elif case == "T06":
                s.streams = [
                    _lines({"message": {"content": CONTENT}}, TERMINAL),
                    _lines({"message": {"content": CONTENT}}),
                ]

                async def sequential():
                    first = await _native()
                    assert first["content"] == CONTENT
                    with pytest.raises(Exception) as caught:
                        await _native()
                    assert model_runtime._provider_infrastructure_details(caught.value) == (
                        "PROVIDER_PROTOCOL",
                        False,
                    )

                asyncio.run(sequential())
                assert len(s.requests) == 2
            else:
                s.streams = [_lines({"message": {"content": CONTENT}}, TERMINAL, "", "  ")]
                result = asyncio.run(_native())
                assert result["content"] == CONTENT
                assert (result["prompt_eval_count"], result["eval_count"]) == (11, 7)
                assert len(s.requests) == 1
    assert visited == list(ids)


def test_ollama_stream_provenance(monkeypatch, tmp_path, subtests):
    ids = ("F00", "F01", "F02")
    visited = []
    for case in ids:
        visited.append(case)
        with subtests.test(case=case), _case(case, tmp_path, monkeypatch) as s:
            path = Path(_stream_module().__file__)
            if case == "F00":
                assert path in runner._RUNNER_IMPLEMENTATION_SOURCES.values()
            elif case == "F01":
                assert path in mcp_contract._MCP_FINALIZATION_SOURCES.values()
            else:
                old = _provenance(Track.MCP)
                new = old.model_copy(update={"runtime_implementation_fingerprint": "b" * 64})
                # Rebuild the sealed provenance with the changed implementation identity.
                data = new.model_dump(mode="json")
                data["provenance_fingerprint"] = canonical_sha256(
                    data, exclude_fields=("provenance_fingerprint",)
                )
                new = type(old).model_validate_json(json.dumps(data))
                root = s.root / "resume"
                runner._guard_run_dir(root, old)
                before = {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}
                with pytest.raises(runner.V2CampaignRunError, match="incompatible"):
                    runner._guard_run_dir(root, new)
                assert {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()} == before
            assert s.counts["request"] == 0
    assert visited == list(ids)


async def _schema_transport(state, **kwargs):
    state.counts["schema_transport"] += 1
    return await adapter.call_provider_text(**kwargs)


def test_ollama_schema_retry_wire_projection(monkeypatch, tmp_path, subtests):
    ids = tuple(f"P{i:02}" for i in range(8))
    visited = []
    for case in ids:
        visited.append(case)
        with subtests.test(case=case), _case(case, tmp_path, monkeypatch) as s:
            native = case != "P04"
            arguments = {} if case == "P07" else deepcopy(ARGUMENTS)
            raw = {"id": "first-call", "function": {"name": "cypher_query", "arguments": arguments}}
            if case == "P01":
                raw = {
                    "id": "first-call",
                    "function": "cypher_query",
                    "arguments": json.dumps(arguments),
                }
            elif case in {"P02", "P03"}:
                raw["function"]["arguments"] = '{"broken":' if case == "P02" else "[]"
            elif case == "P05":
                del raw["function"]["arguments"]
            elif case == "P06":
                raw = {"id": "first-call", "function": "cypher_query"}
            transcript = (
                {"role": "user", "content": TASK.question},
                {"role": "assistant", "content": "", "tool_calls": [raw]},
                {"role": "tool", "tool_call_id": "first-call", "content": RESULT_TEXT},
                {"role": "assistant", "content": "synthetic-repair-source"},
            )
            before = deepcopy(transcript)
            if native:
                s.streams = [_lines({"message": {"content": CONTENT}}, TERMINAL)]
            else:
                s.streams = [
                    {
                        "id": "synthetic-response",
                        "object": "chat.completion",
                        "created": 1,
                        "model": "synthetic-model",
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": CONTENT},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
                    }
                ]
            call = model_runtime._schema_only_retry(
                task=_task(),
                model=("ollama/" if native else "openai-compat/") + "synthetic-model",
                malformed_output="synthetic-repair-source",
                model_base_url=ENDPOINT,
                ollama_options=deepcopy(OPTIONS),
                transport=partial(_schema_transport, s),
                max_tokens=37,
                api_surface="auto",
                request_timeout_seconds=3.0,
                transcript=transcript,
            )
            if case in {"P02", "P03", "P05", "P06"}:
                with pytest.raises(model_runtime.V2ModelRuntimeError) as caught:
                    asyncio.run(call)
                assert (
                    str(caught.value) == "Ollama repair transcript requires object tool arguments"
                )
                assert s.counts["schema_transport"] == s.counts["request"] == 0
                assert not s.transports
            else:
                result = asyncio.run(call)
                assert result.error is None and result.raw_text == CONTENT
                assert (result.tokens_input, result.tokens_output) == (11, 7)
                assert s.counts["schema_transport"] == s.counts["request"] == 1
                request = s.requests[0]
                assert str(request.url) == ENDPOINT + (
                    "/api/chat" if native else "/chat/completions"
                )
                body = json.loads(request.content)
                expected_arguments = (
                    arguments
                    if native
                    else json.dumps(arguments, sort_keys=True, separators=(",", ":"))
                )
                # Explicit wire-type assertion is FC4's intended detection point.
                if native:
                    assert isinstance(
                        body["messages"][2]["tool_calls"][0]["function"]["arguments"], dict
                    )
                assert body["messages"] == [
                    {"role": "system", "content": model_runtime.mcp_system_prompt(_task())},
                    {"role": "user", "content": TASK.question},
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "first-call",
                                "type": "function",
                                "function": {
                                    "name": "cypher_query",
                                    "arguments": expected_arguments,
                                },
                            }
                        ],
                    },
                    {"role": "tool", "content": RESULT_TEXT, "tool_call_id": "first-call"},
                    {"role": "assistant", "content": "synthetic-repair-source"},
                    {"role": "user", "content": model_runtime.SCHEMA_ONLY_RETRY_INSTRUCTION},
                ]
                assert "tools" not in body and body["model"] == "synthetic-model"
                if native:
                    assert body["options"] == OPTIONS and body["stream"] is True
                    assert "authorization" not in request.headers
                else:
                    assert body["max_tokens"] == 37
                    assert request.headers["authorization"] == "Bearer synthetic-redirect-key"
                assert all(t.follow_redirects is False for t in s.transports)
            assert transcript == before and s.tool_calls == []
    assert visited == list(ids)
