"""Native inference redirect containment and owned-transport lifecycle checks."""

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
from pathlib import Path
from types import SimpleNamespace

import httpx
import openai
import pytest

from ori.eval import adapter, codex_oauth, mcp_runtime
from ori.eval.provider_contract import ProviderProtocolError
from ori.eval.v2 import campaign_runner, model_runtime
from ori.eval.v2.campaign import build_public_report
from ori.eval.v2.schema import Track
from ori.eval.v2.scoring import summarize_results
from tests.support.v2_mcp import ORACLE, PROFILE, TASK

CURRENT_CASE = None
MESSAGE = "Provider redirects are not supported; configure the final endpoint"
KEY = "synthetic-redirect-key"
BODY_SENTINEL = "synthetic-redirect-body-secret"
LOCATION = "https://redirect.invalid/target?synthetic-location-secret=1"
PATHS = (
    "openai",
    "compatible",
    "gemini",
    "codex",
    "anthropic",
    "ollama",
    "native-codex",
    "native-compatible",
    "native-ollama",
)
BASES = {
    "openai": "https://api.openai.com/v1",
    "compatible": "https://compatible.invalid/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "codex": "https://codex.invalid/responses-root",
    "anthropic": "https://api.anthropic.com",
    "ollama": "http://ollama.invalid:11434",
}


def _transport_module():
    return importlib.import_module("ori.eval.provider_transport")


def _initialize_httpx(state, client, original, args, kwargs):
    """Intercept allocation below SDK ownership without replacing SDK defaults."""
    state.counts["transport"] += 1
    state.transport_kwargs.append(dict(kwargs))
    kwargs["transport"] = httpx.MockTransport(state.receive)
    kwargs["trust_env"] = False
    original(client, *args, **kwargs)
    state.transports.append(client)


@contextmanager
def _case(case, tmp_path, monkeypatch):
    global CURRENT_CASE
    CURRENT_CASE = case
    root = tmp_path / case
    root.mkdir()
    state = SimpleNamespace(
        root=root,
        counts=Counter(),
        requests=[],
        transports=[],
        transport_kwargs=[],
        status=200,
        path="openai",
        location=LOCATION,
        retry=False,
        sdk_close_fault=None,
    )
    original_init, original_close = httpx.AsyncClient.__init__, httpx.AsyncClient.aclose
    original_exit = httpx.AsyncClient.__aexit__
    with monkeypatch.context() as patch:
        state.patch = patch
        patch.setattr(
            os,
            "environ",
            {
                "OPENAI_API_KEY": KEY,
                "OPENAI_COMPAT_API_KEY": KEY,
                "GEMINI_API_KEY": KEY,
                "CODEX_COMPAT_API_KEY": KEY,
                "ANTHROPIC_API_KEY": KEY,
            },
        )
        patch.setattr(Path, "home", classmethod(lambda cls: root))
        patch.setattr(platform, "platform", lambda: "Synthetic-OS")
        patch.setattr(platform, "mac_ver", lambda: ("0", ("", "", ""), "synthetic"))
        patch.setattr(codex_oauth, "_installation_id", lambda: "synthetic-install")
        patch.setattr(codex_oauth, "_session_id", lambda: "synthetic-session")

        def denied(*args, **kwargs):
            state.counts["external"] += 1
            raise AssertionError("REDIRECT_EXTERNAL_OPERATION")

        for owner, name in (
            (socket.socket, "connect"),
            (socket.socket, "connect_ex"),
            (socket, "getaddrinfo"),
            (subprocess, "Popen"),
        ):
            patch.setattr(owner, name, denied)

        def receive(request):
            state.requests.append(request)
            state.counts["request"] += 1
            if state.status != 200 and str(request.url) != state.location:
                headers = {"location": state.location}
                if state.retry and len(state.requests) == 1:
                    headers["x-should-retry"] = "true"
                return httpx.Response(
                    state.status, headers=headers, json={"error": {"message": BODY_SENTINEL}}
                )
            return _success_response(state.path)

        state.receive = receive

        def initialize(client, *args, **kwargs):
            _initialize_httpx(state, client, original_init, args, kwargs)

        async def close(client):
            state.counts["transport_close"] += 1
            await original_close(client)

        async def context_exit(client, *args):
            state.counts["transport_context_exit"] += 1
            await original_exit(client, *args)

        patch.setattr(httpx.AsyncClient, "__init__", initialize)
        patch.setattr(httpx.AsyncClient, "aclose", close)
        patch.setattr(httpx.AsyncClient, "__aexit__", context_exit)
        try:
            yield state
        finally:
            CURRENT_CASE = None
            assert state.counts["external"] == 0


def _success_response(path):
    if "codex" in path:
        events = (
            {
                "type": "response.output_text.delta",
                "delta": "synthetic-answer",
                "item_id": "item",
                "output_index": 0,
                "content_index": 0,
            },
            {
                "type": "response.completed",
                "response": {
                    "id": "response",
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "id": "item",
                            "status": "completed",
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "synthetic-answer",
                                    "annotations": [],
                                }
                            ],
                        }
                    ],
                    "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
                },
            },
        )
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content="".join("data: " + json.dumps(e) + "\n\n" for e in events),
        )
    if "ollama" in path:
        return httpx.Response(
            200,
            content=json.dumps(
                {
                    "model": "synthetic-model",
                    "message": {"content": "synthetic-answer"},
                    "done": True,
                    "prompt_eval_count": 3,
                    "eval_count": 2,
                }
            )
            + "\n",
        )
    if path == "anthropic":
        return httpx.Response(
            200,
            json={
                "id": "message",
                "type": "message",
                "role": "assistant",
                "model": "synthetic-model",
                "content": [{"type": "text", "text": "synthetic-answer"}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 3, "output_tokens": 2},
            },
        )
    return httpx.Response(
        200,
        json={
            "id": "completion",
            "object": "chat.completion",
            "created": 0,
            "model": "synthetic-model",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "synthetic-answer"},
                }
            ],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        },
    )


async def _invoke(state):
    path = state.path
    messages = [{"role": "user", "content": "synthetic-question"}]
    if not path.startswith("native-"):
        provider = "openai-compat" if path == "compatible" else path
        return await adapter.call_provider_text(
            model=provider + "/synthetic-model",
            base_url=BASES[path],
            system="synthetic-system",
            messages=messages,
        )
    if path == "native-ollama":
        return await mcp_runtime._ollama_chat_turn(
            url=mcp_runtime._native_ollama_chat_url(BASES["ollama"]),
            model_name="ollama/synthetic-model",
            messages=messages,
            tools=[],
            ollama_options={},
        )
    codex = path == "native-codex"
    return await mcp_runtime._openai_compat_chat_turn(
        url=BASES["codex"] if codex else BASES["compatible"] + "/chat/completions",
        model_name=("codex/" if codex else "openai-compat/") + "synthetic-model",
        messages=messages,
        tools=[],
    )


def _public_diagnostic(detail):
    sample = model_runtime._model_infrastructure_sample(TASK, ORACLE, detail)
    assert sample.detail == detail
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
    for sentinel in (LOCATION, "synthetic-location-secret", BODY_SENTINEL, KEY, "redirect.invalid"):
        assert sentinel not in encoded


def _assert_original_request(state):
    path = state.path.removeprefix("native-")
    suffix = (
        "/responses"
        if path == "codex"
        else "/v1/messages"
        if path == "anthropic"
        else "/api/chat"
        if path == "ollama"
        else "/chat/completions"
    )
    expected_url = BASES[path].rstrip("/") + suffix
    for request in state.requests:
        assert str(request.url) == expected_url
        assert request.method == "POST"
        if path == "anthropic":
            assert request.headers["x-api-key"] == KEY
        elif path == "ollama":
            assert "authorization" not in request.headers and "x-api-key" not in request.headers
        else:
            assert request.headers["authorization"] == "Bearer " + KEY
        body = json.loads(request.content)
        assert body["model"] == "synthetic-model"
        user = {"role": "user", "content": "synthetic-question"}
        system = {"role": "system", "content": "synthetic-system"}
        if path == "codex":
            assert body["input"] == [user]
            if state.path == "codex":
                assert body["instructions"] == "synthetic-system"
            else:
                assert "instructions" not in body
        elif path == "anthropic":
            assert body["messages"] == [user]
            assert body["system"] == "synthetic-system"
        else:
            assert body["messages"] == (
                [user] if state.path.startswith("native-") else [system, user]
            )


def _run_redirect(state, *, count=1):
    result, error = None, None
    try:
        result = asyncio.run(_invoke(state))
    except Exception as caught:
        error = caught
    assert len(state.requests) == count
    _assert_original_request(state)
    assert all(str(r.url) != state.location for r in state.requests)
    assert all(str(r.url) == str(state.requests[0].url) for r in state.requests)
    assert all(t.is_closed for t in state.transports)
    if state.path.startswith("native-"):
        assert error is not None
        assert model_runtime._provider_infrastructure_details(error) == ("PROVIDER_PROTOCOL", False)
        detail = str(error)
    else:
        assert error is None and result.error
        assert result.provider_metrics["infra_error_subtype"] == "PROVIDER_PROTOCOL"
        assert result.provider_metrics["infra_retryable"] is False
        detail = result.error
    if state.path in {"openai", "compatible", "gemini", "codex", "native-codex"}:
        assert detail == MESSAGE
    _public_diagnostic(detail)
    assert state.counts["transport"] == 1
    assert state.counts["transport_close"] + state.counts["transport_context_exit"] == 1


def test_inference_cross_origin_redirects(monkeypatch, tmp_path, subtests):
    ids = tuple(f"R{i:02}" for i in range(45))
    visited = []
    for index, case in enumerate(ids):
        visited.append(case)
        with subtests.test(case=case), _case(case, tmp_path, monkeypatch) as s:
            s.path = PATHS[index // 5]
            s.status = (301, 302, 303, 307, 308)[index % 5]
            _run_redirect(s)
    assert visited == list(ids)


def test_inference_same_origin_and_success(monkeypatch, tmp_path, subtests):
    ids = tuple(f"S{i:02}" for i in range(19))
    visited = []
    for index, case in enumerate(ids):
        visited.append(case)
        with subtests.test(case=case), _case(case, tmp_path, monkeypatch) as s:
            s.path = PATHS[index % 9]
            if index < 9:
                base = BASES[s.path.removeprefix("native-")]
                parsed = httpx.URL(base)
                s.location = str(
                    parsed.copy_with(path="/redirected", query=b"synthetic-location-secret=1")
                )
                s.status = 307
                _run_redirect(s)
            elif index == 18:
                s.path, s.status, s.retry = "openai", 307, True
                import openai._base_client as sdk_base

                async def no_sleep(*args, **kwargs):
                    s.counts["retry_sleep"] += 1

                s.patch.setattr(sdk_base.anyio, "sleep", no_sleep)
                _run_redirect(s, count=2)
                assert s.counts["retry_sleep"] == 1
            else:
                result = asyncio.run(_invoke(s))
                assert len(s.requests) == 1
                _assert_original_request(s)
                if not s.path.startswith("native-"):
                    assert result.error is None and result.raw_text == "synthetic-answer"
                    assert (result.tokens_input, result.tokens_output) == (3, 2)
                else:
                    assert result["content"] == "synthetic-answer"
                    fields = (
                        ("prompt_eval_count", "eval_count")
                        if s.path == "native-ollama"
                        else ("prompt_tokens", "completion_tokens")
                    )
                    assert tuple(result[f] for f in fields) == (3, 2)
                payload = json.loads(s.requests[0].content)
                assert payload["model"] == "synthetic-model"
                assert "synthetic-question" in json.dumps(payload)
                assert all(t.is_closed for t in s.transports)
                assert s.counts["transport"] == 1
                assert s.counts["transport_close"] + s.counts["transport_context_exit"] == 1
    assert visited == list(ids)


class OwnedTransport:
    """Lifecycle fixture with independent observed close state for controls."""

    def __init__(self, state, **kwargs):
        assert kwargs == {"follow_redirects": False}
        self.state, self.closed = state, False
        state.counts["owned_constructor"] += 1

    @property
    def is_closed(self):
        return self.closed

    async def aclose(self):
        self.state.counts["owned_close"] += 1
        if self.state.close_error is not None:
            raise self.state.close_error
        self.closed = True


def test_inference_transport_lifecycle(monkeypatch, tmp_path, subtests):
    ids = tuple(f"L{i:02}" for i in range(10))
    visited = []
    for case in ids:
        visited.append(case)
        with subtests.test(case=case), _case(case, tmp_path, monkeypatch) as s:
            helper = _transport_module().openai_inference_client
            if case in {"L08", "L09"}:
                if case == "L09":
                    os.environ.pop("OPENAI_API_KEY")
                    response = asyncio.run(_invoke(s))
                    assert response.provider_metrics["infra_error_subtype"] == "PROVIDER_AUTH"
                    assert s.counts["transport"] == s.counts["request"] == 0
                else:
                    s.path = "native-codex"
                    primary = ValueError("synthetic-sdk-close")

                    async def sdk_close(client):
                        s.counts["sdk_close"] += 1
                        raise primary

                    s.patch.setattr(openai.AsyncOpenAI, "close", sdk_close)
                    with pytest.raises(ValueError) as caught:
                        asyncio.run(_invoke(s))
                    assert caught.value is primary
                    assert s.counts["request"] == s.counts["sdk_close"] == 1
                    assert s.counts["transport_close"] == 1 and s.transports[0].is_closed
                continue
            s.close_error = ValueError("synthetic-cleanup") if case == "L05" else None
            allocated = []

            def transport(**kwargs):
                value = OwnedTransport(s, **kwargs)
                allocated.append(value)
                return value

            primary = ValueError("synthetic-primary")
            if case in {"L03", "L04"}:
                status = 307 if case == "L03" else 418
                response = httpx.Response(
                    status, request=httpx.Request("POST", "https://original.invalid")
                )
                primary = openai.APIStatusError("synthetic-status", response=response, body={})
            elif case == "L06":
                primary = asyncio.CancelledError("synthetic-cancel")

            def sdk(**kwargs):
                s.counts["sdk_constructor"] += 1
                if case == "L01":
                    raise primary
                assert kwargs["http_client"] is allocated[0]
                return SimpleNamespace(marker="constructed")

            s.patch.setattr(openai, "DefaultAsyncHttpxClient", transport)
            s.patch.setattr(openai, "AsyncOpenAI", sdk)

            async def invoke():
                async with helper(
                    **({"http_client": object()} if case == "L07" else {"api_key": KEY})
                ) as client:
                    assert client.marker == "constructed"
                    if case not in {"L00", "L07"}:
                        raise primary
                    return "successful"

            if case == "L00":
                assert asyncio.run(invoke()) == "successful"
            else:
                expected = (
                    ProviderProtocolError
                    if case == "L03"
                    else ValueError
                    if case == "L07"
                    else type(primary)
                )
                with pytest.raises(expected) as caught:
                    asyncio.run(invoke())
                if case == "L03":
                    assert str(caught.value) == MESSAGE
                elif case != "L07":
                    assert caught.value is primary
            expected_count = 0 if case == "L07" else 1
            assert s.counts["owned_constructor"] == expected_count
            assert s.counts["owned_close"] == expected_count
            assert s.counts["sdk_constructor"] == expected_count
            if allocated:
                assert allocated[0].closed is (case != "L05")
    assert visited == list(ids)


def test_inference_transport_provenance(monkeypatch, tmp_path, subtests):
    from ori.eval.v2 import mcp
    from tests.support.gemini_destination import _provenance, _resolved

    ids = ("F00", "F01", "F02")
    visited = []
    for case in ids:
        visited.append(case)
        with subtests.test(case=case), _case(case, tmp_path, monkeypatch) as s:
            path = Path(_transport_module().__file__)
            if case == "F00":
                assert path in campaign_runner._RUNNER_IMPLEMENTATION_SOURCES.values()
            elif case == "F01":
                assert path in mcp._MCP_FINALIZATION_SOURCES.values()
            else:
                first = _provenance(s, _resolved(s))
                second = _provenance(s, _resolved(s, source="d" * 64))
                target = s.root / "guard"
                campaign_runner._guard_run_dir(target, first)
                file = target / "campaign-provenance-v2.json"
                original = file.read_bytes()
                with pytest.raises(campaign_runner.V2CampaignRunError, match="incompatible"):
                    campaign_runner._guard_run_dir(target, second)
                assert file.read_bytes() == original
    assert visited == list(ids)
