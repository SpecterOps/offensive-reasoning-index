"""Finite Codex stream reconciliation through real SDK decoding and consumers."""

from __future__ import annotations

import asyncio
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
from openai._models import construct_type
from openai.types.responses import ResponseStreamEvent
from pydantic import TypeAdapter

from ori.eval import adapter, codex_oauth, mcp_runtime
from ori.eval.provider_contract import ProviderProtocolError
from ori.eval.v2 import campaign_runner as runner
from ori.eval.v2 import mcp as mcp_contract
from ori.eval.v2 import model_runtime
from ori.eval.v2.schema import Track, canonical_sha256
from tests.support.v2_campaign import _provenance
from tests.support.v2_campaign import _state as _fixture_state
from tests.support.v2_mcp import ORACLE, PROFILE, RESOLVER, TASK

CURRENT_CASE = None
ENDPOINT = "https://codex.invalid/responses-root"
CONTENT = "synthetic-codex-answer"
ERROR = "Codex successful stream is inconsistent or malformed"
QUERY = "MATCH p=(a {objectid:'USER-A'})-[:MemberOf]->(b {objectid:'GROUP-B'}) RETURN p"
ARGUMENTS = {"info_type": "run", "query": QUERY}
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


def _task():
    return TASK


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
        patch.setattr(os, "environ", {"CODEX_COMPAT_API_KEY": "synthetic-codex-key"})
        patch.setattr(Path, "home", classmethod(lambda cls: root))
        patch.setattr(platform, "platform", lambda: "Synthetic-OS")
        patch.setattr(platform, "mac_ver", lambda: ("0", ("", "", ""), "synthetic"))

        def denied(*args, **kwargs):
            state.counts["external"] += 1
            raise AssertionError("CODEX_STREAM_EXTERNAL_OPERATION")

        for owner, name in (
            (socket.socket, "connect"),
            (socket.socket, "connect_ex"),
            (socket, "getaddrinfo"),
            (subprocess, "Popen"),
        ):
            patch.setattr(owner, name, denied)
        patch.setattr(codex_oauth, "_installation_id", lambda: "synthetic-install")
        patch.setattr(codex_oauth, "_session_id", lambda: "synthetic-session")

        def credentials_forbidden():
            state.counts["credential_file"] += 1
            raise AssertionError("CODEX_REAL_CREDENTIAL_FILE")

        patch.setattr(codex_oauth, "_codex_file_token", credentials_forbidden)
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
            assert index < len(state.streams), "UNEXPECTED_CODEX_REQUEST"
            stream = state.streams[index]
            if isinstance(stream, dict):
                return httpx.Response(200, json=stream)
            if isinstance(stream, httpx.AsyncByteStream):
                return httpx.Response(
                    200, headers={"content-type": "text/event-stream"}, stream=stream
                )
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content="".join("data: " + json.dumps(e) + "\n\n" for e in stream),
            )

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
            assert state.counts["credential_file"] == 0
            assert all(client.is_closed for client in state.transports)


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
            "model": "synthetic-model",
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


def _public_record(state, task, sample, record):
    assert record.mcp_tool_receipts[0].result_text == RESULT_TEXT
    assert record.mcp_tool_receipts[0].arguments == ARGUMENTS
    assert any(m.get("content") == RESULT_TEXT for m in record.mcp_transcript)
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
    assert "USER-A" in private.model_dump_json()
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
    for sentinel in (QUERY, RESULT_TEXT, ENDPOINT, "USER-A", "first-call"):
        assert sentinel not in encoded


def test_codex_stream_provenance(monkeypatch, tmp_path, subtests):
    ids = ("F00", "F01", "F02")
    visited = []
    for case in ids:
        visited.append(case)
        with subtests.test(case=case), _case(case, tmp_path, monkeypatch) as s:
            path = Path(codex_oauth.__file__)
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


def _message(text=CONTENT, *, identity="message-1"):
    return {
        "id": identity,
        "type": "message",
        "status": "completed",
        "role": "assistant",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


def _call(*, identity="tool-1", call_id="first-call", arguments=None):
    return {
        "type": "function_call",
        "id": identity,
        "status": "completed",
        "call_id": call_id,
        "name": "cypher_query",
        "arguments": json.dumps(ARGUMENTS) if arguments is None else arguments,
    }


def _delta(text=CONTENT, *, output_index=0, content_index=0, item_id="message-1"):
    return {
        "type": "response.output_text.delta",
        "sequence_number": 1,
        "logprobs": [],
        "output_index": output_index,
        "content_index": content_index,
        "item_id": item_id,
        "delta": text,
    }


def _done(call, index=0):
    return {
        "type": "response.output_item.done",
        "sequence_number": 2,
        "output_index": index,
        "item": deepcopy(call),
    }


def _completed(output):
    return {
        "type": "response.completed",
        "sequence_number": 3,
        "response": {
            "id": "response-1",
            "created_at": 1.0,
            "model": "synthetic-model",
            "object": "response",
            "status": "completed",
            "output": deepcopy(output),
            "parallel_tool_calls": True,
            "tool_choice": "auto",
            "tools": [],
            "usage": {
                "input_tokens": 11,
                "output_tokens": 7,
                "total_tokens": 18,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens_details": {"reasoning_tokens": 0},
            },
        },
    }


def _projected(call):
    return {
        "id": call["call_id"],
        "type": "function",
        "function": {"name": call["name"], "arguments": call["arguments"]},
    }


def _scenario(index):
    call = _call()
    text, calls = CONTENT, []
    events = [_delta(), _completed([_message()])]
    function_cases = {1, 3, 4, 7, 8, 9, 12, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 40, 43}
    if index in function_cases:
        events, text, calls = [_done(call), _completed([call])], "", [_projected(call)]
    if index == 2:
        events = [_delta(), _done(call, 1), _completed([_message(), call])]
        calls = [_projected(call)]
    elif index in {3, 5}:
        events = events[-1:]
    elif index == 4:
        second = _call(identity="tool-2", call_id="second-call", arguments='{"second":true}')
        events = [_done(second, 1), _completed([call, second])]
        calls = [_projected(call), _projected(second)]
    elif index == 6:
        events = [
            _delta("B", output_index=1, item_id="message-2"),
            _delta("A"),
            _delta("2", output_index=1, item_id="message-2"),
            _delta("1"),
            _completed([_message("A1"), _message("B2", identity="message-2")]),
        ]
        text = "A1B2"
    elif index in {7, 8, 9}:
        if index == 7:
            del call["id"]
        else:
            call["arguments"] = '{"broken":' if index == 8 else ""
        events, calls = [_done(call), _completed([call])], [_projected(call)]
    elif index == 10:
        events.append(deepcopy(events[-1]))
    elif index == 11:
        events.append(_delta())
    elif index == 12:
        events.append(_done(call))
    elif index == 13:
        events.append({"type": "future.event"})
    elif index == 14:
        events.insert(1, deepcopy(events[0]))
    elif index == 15:
        duplicate = deepcopy(call)
        duplicate["id"] = "tool-2"
        events[-1]["response"]["output"].append(duplicate)
    elif index in {16, 18, 20}:
        del events[-1]["response"]["output"][0][{16: "call_id", 18: "arguments", 20: "name"}[index]]
    elif index == 17:
        events[-1]["response"]["output"][0]["call_id"] = "  "
    elif index == 19:
        events[-1]["response"]["output"][0]["arguments"] = {}
    elif index == 21:
        events[-1]["response"]["output"] = [_message()]
    elif index in {22, 23, 40}:
        events[0]["item"][{22: "name", 23: "arguments", 40: "id"}[index]] = "conflict"
    elif index == 24:
        events[0]["output_index"] = 1
    elif index in {25, 26, 29, 30, 41}:
        field, value = {
            25: ("delta", "conflict"),
            26: ("item_id", "unknown"),
            29: ("delta", 7),
            30: ("output_index", True),
            41: ("output_index", -1),
        }[index]
        events[0][field] = value
    elif index == 27:
        del events[-1]["response"]["output"]
    elif index == 28:
        events[-1]["response"]["output"] = {}
    elif index == 31:
        events[-1]["response"]["status"] = "failed"
    elif index == 32:
        events = events[:1]
    elif index in {33, 34, 35, 44}:
        failure = {
            "type": "response.failed",
            "sequence_number": 4,
            "response": {
                **_completed([])["response"],
                "status": "failed",
                "error": {"code": "server_error", "message": "synthetic failure"},
            },
        }
        if index == 34:
            failure["type"] = "response.incomplete"
            failure["response"]["status"] = "incomplete"
            failure["response"]["incomplete_details"] = {"reason": "max_output_tokens"}
        elif index == 35:
            failure = {
                "type": "error",
                "sequence_number": 4,
                "code": "server_error",
                "message": "synthetic failure",
            }
        events = (
            [*events, {"type": "future.event"}, _delta(), failure] if index == 44 else [failure]
        )
    elif index == 36:
        events = [_completed([])]
    elif index == 37:
        events.insert(0, {"type": "future.event"})
    elif index in {38, 39}:
        message = _message()
        refusal = {"type": "refusal", "refusal": "synthetic-refusal"}
        message["content"] = [refusal] if index == 38 else [*message["content"], refusal]
        events = [_completed([message])]
        text = "synthetic-refusal" if index == 38 else CONTENT
    elif index == 42:
        events[-1]["response"]["output"].append(_message())
    elif index == 43:
        events[-1]["response"]["output"][0]["status"] = "incomplete"
    elif index == 45:
        message = _message()
        message["content"].insert(0, {"type": "future.content"})
        events = [
            _delta(output_index=1, content_index=1),
            _completed([{"id": "reasoning-1", "type": "reasoning", "summary": []}, message]),
        ]
    valid = index in {*range(10), 37, 38, 39, 45}
    return events, valid, text, calls


def _decode(events, *, validate=False):
    if validate:
        for event in events:
            candidate = deepcopy(event)
            if candidate["type"] == "future.event":
                continue
            if candidate["type"] == "response.completed":
                for item in candidate["response"]["output"]:
                    if item["type"] == "message":
                        item["content"] = [
                            c for c in item["content"] if c["type"] != "future.content"
                        ]
            TypeAdapter(ResponseStreamEvent).validate_python(candidate)
    return [construct_type(value=e, type_=ResponseStreamEvent) for e in events]


def test_codex_stream_reconciliation(monkeypatch, tmp_path, subtests):
    ids = tuple(f"T{i:02}" for i in range(48))
    visited = []
    for case in ids:
        visited.append(case)
        with subtests.test(case=case), _case(case, tmp_path, monkeypatch) as s:
            index = int(case[1:])
            events, valid, text, calls = _scenario(0 if index >= 46 else index)
            decoded = _decode(events, validate=valid)
            if index >= 46:
                # Translator-object compatibility, not SDK wire qualification.
                decoded.insert(0 if index == 46 else len(decoded), SimpleNamespace(type=[]))
                valid = index == 46
            if valid:
                result = codex_oauth.codex_responses_events_to_chat_completion(
                    decoded, "synthetic-model"
                )
                message = {"role": "assistant", "content": text or None}
                if calls:
                    message["tool_calls"] = calls
                assert result["choices"] == [
                    {
                        "index": 0,
                        "message": message,
                        "finish_reason": "tool_calls" if calls else "stop",
                    }
                ]
                assert (
                    result["model"] == "synthetic-model" and result["object"] == "chat.completion"
                )
                assert result["usage"] == {
                    "prompt_tokens": 11,
                    "completion_tokens": 7,
                    "total_tokens": 18,
                }
            elif index in {32, 33, 34, 35, 36, 44}:
                expected = {
                    32: "Codex Responses API stream ended without a response.completed event",
                    33: "Codex Responses API response failed: server_error: synthetic failure",
                    34: "Codex Responses API response incomplete: max_output_tokens",
                    35: "Codex Responses API stream error: server_error: synthetic failure",
                    36: "Codex Responses API response completed without text or tool calls",
                    44: "Codex Responses API response failed: server_error: synthetic failure",
                }[index]
                with pytest.raises(codex_oauth.CodexResponseStreamError) as caught:
                    codex_oauth.codex_responses_events_to_chat_completion(
                        decoded, "synthetic-model"
                    )
                assert str(caught.value) == expected
            else:
                with pytest.raises(ProviderProtocolError) as caught:
                    codex_oauth.codex_responses_events_to_chat_completion(
                        decoded, "synthetic-model"
                    )
                assert str(caught.value) == ERROR
            assert s.counts["request"] == 0
    assert visited == list(ids)


async def _direct():
    return await adapter.call_provider_text(
        model="codex/synthetic-model",
        base_url=ENDPOINT,
        system="synthetic-system",
        messages=[{"role": "user", "content": "synthetic-question"}],
    )


async def _native(state):
    state.progress = []
    return await mcp_runtime._openai_compat_chat_turn(
        url=mcp_runtime._openai_compat_chat_url(ENDPOINT, "codex/synthetic-model"),
        model_name="codex/synthetic-model",
        messages=[
            {"role": "system", "content": "synthetic-system"},
            {"role": "user", "content": "synthetic-question"},
        ],
        tools=[],
        event_progress_observer=state.progress.append,
    )


def _assert_requests(state, *, system="synthetic-system", question="synthetic-question"):
    for request in state.requests:
        assert request.method == "POST" and str(request.url) == ENDPOINT + "/responses"
        assert request.headers["authorization"] == "Bearer synthetic-codex-key"
        body = json.loads(request.content)
        assert body["model"] == "synthetic-model" and body["stream"] is True
        assert body["instructions"] == system
        assert body["input"][0] == {"role": "user", "content": question}
    assert len(state.transports) == len(state.requests)
    assert state.counts["transport_close"] == len(state.requests)
    assert all(t.follow_redirects is False and t.is_closed for t in state.transports)


def test_codex_stream_wire_admission(monkeypatch, tmp_path, subtests):
    ids = tuple(f"W{i:02}-{surface}" for i in range(8) for surface in ("D", "N"))
    visited = []
    for case in ids:
        visited.append(case)
        with subtests.test(case=case), _case(case, tmp_path, monkeypatch) as s:
            index, native = int(case[1:3]), case.endswith("-N")
            source = (0, 1, 10, 12, 14, 25, 27, 29)[index]
            events, valid, text, calls = _scenario(source)
            if valid:
                _decode(events, validate=True)
            s.streams = [events]
            response, error = None, None
            try:
                response = asyncio.run(_native(s) if native else _direct())
            except Exception as caught:
                error = caught
            assert len(s.requests) == 1
            _assert_requests(s)
            if index > 1:
                if native:
                    assert model_runtime._provider_infrastructure_details(error) == (
                        "PROVIDER_PROTOCOL",
                        False,
                    )
                    assert type(error) is ProviderProtocolError and str(error) == ERROR
                    assert response is None
                    assert len(s.progress) == len(events) and all(
                        isinstance(v, str) for v in s.progress
                    )
                else:
                    assert error is None and response.error == ERROR
                    assert (
                        response.raw_text == ""
                        and response.provider_metrics["infra_retryable"] is False
                    )
                    assert response.provider_metrics["infra_error_subtype"] == "PROVIDER_PROTOCOL"
            elif native:
                assert error is None and response["content"] == text
                assert (response["prompt_tokens"], response["completion_tokens"]) == (11, 7)
                assert [
                    {"id": c["id"], "type": c["type"], "function": c["function"]}
                    for c in response["tool_calls"]
                ] == calls
                assert response["finish_reason"] == ("tool_calls" if calls else "stop")
                assert s.progress == ([CONTENT, CONTENT] if index == 0 else ["", ""])
            else:
                assert error is None and response.error is None and response.raw_text == text
                assert (response.tokens_input, response.tokens_output) == (11, 7)
                assert response.provider_metrics["finish_reason"] == (
                    "tool_calls" if calls else "stop"
                )
            assert s.tool_calls == []
    assert visited == list(ids)


async def _loop(state):
    return await mcp_runtime._run_openai_compat_mcp_loop(
        task=None,
        public_question="synthetic-question",
        model_name="codex/synthetic-model",
        base_url=ENDPOINT,
        extra_body=None,
        tools=_tools(state),
        max_steps=4,
        system_prompt_override="synthetic-system",
    )


async def _v2(state):
    return await model_runtime.run_mcp_model_task_v2(
        task=TASK,
        oracle=ORACLE,
        resolver=RESOLVER,
        profile=PROFILE,
        bundle=mcp_runtime.MCPServerBundle(tools=_tools(state)),
        model="codex/synthetic-model",
        model_base_url=ENDPOINT,
        tool_loop="native-openai-compatible",
        max_steps=4,
    )


class _BrokenBody(httpx.AsyncByteStream):
    def __init__(self, *, cancel=False):
        self.cancel = cancel

    async def __aiter__(self):
        if self.cancel:
            raise asyncio.CancelledError()
        yield ("data: " + json.dumps(_done(_call())) + "\n\n").encode()
        raise httpx.ReadTimeout("synthetic stream read timeout")


def test_codex_stream_consumer_admission(monkeypatch, tmp_path, subtests):
    ids = tuple(f"C{i:02}" for i in range(9))
    visited = []
    for case in ids:
        visited.append(case)
        with subtests.test(case=case), _case(case, tmp_path, monkeypatch) as s:
            index = int(case[1:])
            call = _call()
            first = [_done(call), _completed([call])]
            final = _scenario(0)[0]
            if index < 3:
                if index == 0:
                    invalid = [*first, deepcopy(first[-1])]
                elif index == 1:
                    invalid = [first[0], deepcopy(first[0]), first[1]]
                else:
                    invalid = [_delta("conflict"), _done(call, 1), _completed([_message(), call])]
                s.streams = [invalid, final]
                error = None
                try:
                    asyncio.run(_loop(s))
                except Exception as caught:
                    error = caught
                assert s.tool_calls == []
                assert len(s.requests) == 1
                assert model_runtime._provider_infrastructure_details(error) == (
                    "PROVIDER_PROTOCOL",
                    False,
                )
                assert str(error) == ERROR
                _assert_requests(s)
            elif index == 3:
                second_args = {"info_type": "run", "query": QUERY + " // second"}
                second = _call(
                    identity="tool-2", call_id="second-call", arguments=json.dumps(second_args)
                )
                first = [_done(second, 1), _completed([call, second])]
                _decode(first, validate=True)
                s.streams = [first, final]
                response = asyncio.run(_loop(s))[0]
                assert s.tool_calls == [ARGUMENTS, second_args]
                assert len(s.requests) == 2
                assert response.error is None and response.raw_text == CONTENT
                assert (response.tokens_input, response.tokens_output) == (22, 14)
                _assert_requests(s)
            elif index == 6:
                from tests.support.v2_direct import ORACLE as DIRECT_ORACLE
                from tests.support.v2_direct import RESOLVER as DIRECT_RESOLVER
                from tests.support.v2_direct import TASK as DIRECT_TASK

                class Coordinator:
                    async def execute(self, *args, **kwargs):
                        s.counts["coordinator"] += 1
                        raise AssertionError("INVALID_CODEX_QUERY_EXECUTED")

                s.streams = [_scenario(10)[0]]
                _, sample, record = asyncio.run(
                    model_runtime.run_direct_model_task_v2(
                        coordinator=Coordinator(),
                        task=DIRECT_TASK,
                        oracle=DIRECT_ORACLE,
                        resolver=DIRECT_RESOLVER,
                        model="codex/synthetic-model",
                        model_base_url=ENDPOINT,
                    )
                )
                assert sample.outcome.value == "INFRA_ERROR" and sample.reasoning_correct is None
                assert record.provider_metrics["infra_error_subtype"] == "PROVIDER_PROTOCOL"
                assert s.counts["coordinator"] == 0 and len(s.requests) == 1
            else:
                if index == 4:
                    second = _call(identity="tool-2", call_id="second-call")
                    s.streams = [first, [_done(second), _done(second), _completed([second])]]
                elif index == 5:

                    def defect(events, model):
                        raise RuntimeError("synthetic translator defect")

                    s.patch.setattr(
                        codex_oauth, "codex_responses_events_to_chat_completion", defect
                    )
                    s.streams = [final]
                elif index == 7:
                    s.streams = [first, _BrokenBody(cancel=True)]
                else:
                    s.streams = [_BrokenBody()]
                if index == 7:
                    with pytest.raises(model_runtime.V2ModelTaskCancelled) as caught:
                        asyncio.run(_v2(s))
                    record = caught.value.provider
                    _assert_prior(s, record, tokens=(11, 7))
                    assert record.provider_metrics["infra_error_subtype"] == "INTERRUPTED"
                    assert record.provider_metrics["infra_retryable"] is False
                    assert len(s.requests) == 2
                else:
                    outcome, record = asyncio.run(_v2(s))
                    if index == 4:
                        _assert_prior(s, record, tokens=(11, 7))
                        assert (
                            len(s.requests) == 2 and outcome.sample.outcome.value == "INFRA_ERROR"
                        )
                        assert record.provider_metrics["infra_error_subtype"] == "PROVIDER_PROTOCOL"
                        assert record.provider_metrics["infra_retryable"] is False
                        _public_record(s, TASK, outcome.sample, record)
                    elif index == 5:
                        assert outcome.sample.outcome.value == "HARNESS_ERROR"
                        assert len(s.requests) == 1 and s.tool_calls == []
                        assert record.provider_metrics.get("infra_retryable") is not True
                    else:
                        assert outcome.sample.outcome.value == "INFRA_ERROR"
                        assert record.provider_metrics["infra_error_subtype"] == "PROVIDER_TIMEOUT"
                        assert record.provider_metrics["infra_retryable"] is True
                        assert len(s.requests) == 1 and s.tool_calls == []
                _assert_requests(
                    s, system=model_runtime.mcp_system_prompt(TASK), question=TASK.question
                )
    assert visited == list(ids)
