"""Finite fixed-destination Gemini checks with real SDK, synthetic HTTP only."""

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
import openai
import pytest
from pydantic import ValidationError

from ori.eval import adapter
from ori.eval.v2 import campaign_runner as runner
from ori.eval.v2.campaign_config import V2CampaignConfig
from ori.eval.v2.schema import canonical_sha256
from tests.support.gemini_destination import (
    MODEL,
    _config_payload,
    _provenance,
    _resolved,
)

CURRENT_CASE = None
EXPECTED_BASE = "https://generativelanguage.googleapis.com/v1beta/openai/"
EXPECTED_HTTP = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
DISTRACTOR_A = "https://distractor-a.invalid/private-route/"
DISTRACTOR_B = "https://distractor-b.invalid/other-route/"
KEY = "synthetic-gemini-one"
ROTATED_KEY = "synthetic-gemini-two"
COMPETITORS = {
    "OPENAI_API_KEY": "synthetic-openai",
    "OPENAI_COMPAT_API_KEY": "synthetic-compatible",
    "OPENROUTER_API_KEY": "synthetic-router",
    "NOUS_API_KEY": "synthetic-nous",
    "ANTHROPIC_API_KEY": "synthetic-anthropic",
    "CODEX_COMPAT_API_KEY": "synthetic-codex",
}
SCHEMA = {
    "name": "synthetic_submission",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
        "additionalProperties": False,
    },
}


@contextmanager
def _case(case, tmp_path, monkeypatch):
    global CURRENT_CASE
    CURRENT_CASE = case
    root = tmp_path / case
    root.mkdir()
    state = SimpleNamespace(root=root, counts=Counter(), requests=[], constructors=[], clients=[])
    original_sdk = openai.AsyncOpenAI
    original_transport = openai.DefaultAsyncHttpxClient
    with monkeypatch.context() as patch:
        state.patch = patch
        patch.setattr(os, "environ", {"GEMINI_API_KEY": KEY})
        patch.setattr(Path, "home", classmethod(lambda cls: root))
        patch.setattr(platform, "platform", lambda: "Synthetic-OS")
        patch.setattr(platform, "mac_ver", lambda: ("0", ("", "", ""), "synthetic"))

        def denied(*args, **kwargs):
            state.counts["external"] += 1
            raise AssertionError("GEMINI_TEST_EXTERNAL_OPERATION")

        for owner, name in (
            (socket.socket, "connect"),
            (socket.socket, "connect_ex"),
            (socket, "getaddrinfo"),
            (subprocess, "Popen"),
        ):
            patch.setattr(owner, name, denied)

        def receive(request):
            state.counts["request"] += 1
            state.requests.append(request)
            return httpx.Response(
                200,
                json={
                    "id": "synthetic-completion",
                    "object": "chat.completion",
                    "created": 0,
                    "model": json.loads(request.content)["model"],
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "synthetic-answer"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 13, "completion_tokens": 5, "total_tokens": 18},
                },
            )

        def sdk(**kwargs):
            state.counts["sdk_constructor"] += 1
            state.constructors.append(dict(kwargs))
            client = original_sdk(**kwargs)
            state.clients.append(client)
            return client

        def transport(**kwargs):
            return original_transport(**kwargs, transport=httpx.MockTransport(receive))

        patch.setattr(openai, "AsyncOpenAI", sdk)
        patch.setattr(openai, "DefaultAsyncHttpxClient", transport)
        try:
            yield state
        finally:
            CURRENT_CASE = None
            assert state.counts["external"] == 0
            assert all(client.is_closed() for client in state.clients)


def _call(s, *, model=MODEL, base_url=None, **kwargs):
    async def invoke():
        first = len(s.clients)
        try:
            return await adapter.call_provider_text(
                model="gemini/" + model,
                base_url=base_url,
                system="synthetic-system",
                messages=[{"role": "user", "content": "synthetic-question"}],
                **kwargs,
            )
        finally:
            # Production ownership is deliberately unchanged by this slice.
            # These clients were allocated by our fixture and close on this loop.
            for client in s.clients[first:]:
                await client.close()
                s.counts["fixture_close"] += 1

    return asyncio.run(invoke())


def _assert_success(response):
    assert response.error is None and response.raw_text == "synthetic-answer"
    assert (response.tokens_input, response.tokens_output) == (13, 5)
    assert response.provider_metrics["endpoint_family"] == "gemini"
    assert response.provider_metrics["credential_source"] == "GEMINI_API_KEY"


def test_gemini_destination_parity(monkeypatch, tmp_path, subtests):
    ids = tuple(f"D{i:02}" for i in range(7))
    visited = []
    for case in ids:
        visited.append(case)
        with subtests.test(case=case), _case(case, tmp_path, monkeypatch) as s:
            model_url = DISTRACTOR_A if case in {"D01", "D03", "D05"} else None
            defaults_url = DISTRACTOR_B if case in {"D02", "D03"} else None
            model_name = MODEL + ("@" + DISTRACTOR_B if case in {"D04", "D05"} else "")
            if case == "D06":
                os.environ.update(
                    GEMINI_BASE_URL=DISTRACTOR_A,
                    OPENAI_COMPAT_BASE_URL=DISTRACTOR_B,
                    OPENAI_BASE_URL=DISTRACTOR_A,
                )
            resolved = _resolved(
                s, model=model_name, model_url=model_url, defaults_url=defaults_url
            )
            (receipt,) = runner._model_readiness(resolved)
            assert (
                receipt.endpoint_family == "gemini"
                and receipt.credential_source == "GEMINI_API_KEY"
            )
            assert not s.counts
            model = resolved.config.models[0]
            base = runner._model_base_url(model, resolved)
            fingerprint = runner._provider_endpoint_fingerprint(model, resolved)
            response = _call(s, model=model_name, base_url=base)
            _assert_success(response)
            assert base == EXPECTED_BASE
            assert str(s.requests[0].url) == EXPECTED_HTTP
            assert fingerprint == canonical_sha256({"endpoint": EXPECTED_BASE})
            assert json.loads(s.requests[0].content)["model"] == model_name
            assert s.requests[0].headers["authorization"] == "Bearer " + KEY
            assert s.counts == {"sdk_constructor": 1, "request": 1, "fixture_close": 1}
    assert visited == list(ids)


def test_gemini_scoped_credentials(monkeypatch, tmp_path, subtests):
    ids = ("C00", "C01", "C02", "C03")
    visited = []
    for case in ids:
        visited.append(case)
        with subtests.test(case=case), _case(case, tmp_path, monkeypatch) as s:
            if case in {"C00", "C02", "C03"}:
                os.environ.update(COMPETITORS)
            if case == "C01":
                os.environ.clear()
            elif case == "C02":
                os.environ["GEMINI_API_KEY"] = ""
            resolved = _resolved(s)
            identity = runner._provider_identity(resolved.config.models[0], resolved)
            assert identity.endpoint_family == "gemini"
            if case in {"C01", "C02"}:
                assert identity.credential_source is None
                with pytest.raises(runner.V2CampaignRunError, match="requires GEMINI_API_KEY"):
                    runner._model_readiness(resolved)
                response = _call(s)
                assert response.error == "Gemini requires GEMINI_API_KEY"
                assert response.provider_metrics["infra_error_subtype"] == "PROVIDER_AUTH"
                assert not s.counts
            else:
                (receipt,) = runner._model_readiness(resolved)
                assert identity.credential_source == receipt.credential_source == "GEMINI_API_KEY"
                _assert_success(_call(s))
                expected = ["Bearer " + KEY]
                if case == "C03":
                    os.environ["GEMINI_API_KEY"] = ROTATED_KEY
                    _assert_success(_call(s))
                    expected.append("Bearer " + ROTATED_KEY)
                assert [r.headers["authorization"] for r in s.requests] == expected
                assert all(str(r.url) == EXPECTED_HTTP for r in s.requests)
                assert s.counts == {
                    "sdk_constructor": len(expected),
                    "request": len(expected),
                    "fixture_close": len(expected),
                }
    assert visited == list(ids)


def test_gemini_request_compatibility(monkeypatch, tmp_path, subtests):
    ids = ("P00", "P01", "P02", "P03")
    visited = []
    for case in ids:
        visited.append(case)
        with subtests.test(case=case), _case(case, tmp_path, monkeypatch) as s:
            if case in {"P00", "P01"}:
                kwargs = {"max_tokens": 8192}
                if case == "P01":
                    kwargs.update(
                        structured_output_schema=deepcopy(SCHEMA), request_timeout_seconds=7.5
                    )
                response = _call(s, **kwargs)
                _assert_success(response)
                body = json.loads(s.requests[0].content)
                assert body["model"] == MODEL and body["max_tokens"] == 8192
                assert body["messages"] == [
                    {"role": "system", "content": "synthetic-system"},
                    {"role": "user", "content": "synthetic-question"},
                ]
                assert "tools" not in body and "tool_choice" not in body
                if case == "P01":
                    assert body["response_format"] == {"type": "json_schema", "json_schema": SCHEMA}
                    assert s.constructors[0]["timeout"] == 7.5
                    assert s.requests[0].extensions["timeout"] == {
                        "connect": 7.5,
                        "read": 7.5,
                        "write": 7.5,
                        "pool": 7.5,
                    }
                else:
                    assert "response_format" not in body
                assert s.counts == {"sdk_constructor": 1, "request": 1, "fixture_close": 1}
            elif case == "P02":
                resolved = _resolved(s)
                model = resolved.config.models[0].model_copy(update={"api_surface": "responses"})
                resolved = resolved.model_copy(
                    update={"config": resolved.config.model_copy(update={"models": [model]})}
                )
                with pytest.raises(
                    runner.V2CampaignRunError, match="cannot use api_surface='responses'"
                ):
                    runner._model_readiness(resolved)
                assert not s.counts
            else:
                payload = _config_payload()
                payload["modes"] = ["mcp"]
                payload["tracks"] = {"mcp": payload["tracks"]["direct"]}
                payload["defaults"]["mcp"] = {"mcp_dir": str(s.root)}
                with pytest.raises(
                    ValidationError, match="native v2 MCP campaigns do not support provider"
                ):
                    V2CampaignConfig.model_validate(payload)
                assert not s.counts
    assert visited == list(ids)


def test_gemini_provenance_compatibility(monkeypatch, tmp_path, subtests):
    ids = tuple(f"F{i:02}" for i in range(5))
    visited = []
    for case in ids:
        visited.append(case)
        with subtests.test(case=case), _case(case, tmp_path, monkeypatch) as s:
            first = _resolved(s, model_url=DISTRACTOR_A)
            second = _resolved(s, defaults_url=DISTRACTOR_B, source="b" * 64)
            if case == "F00":
                left = runner._provider_endpoint_fingerprint(first.config.models[0], first)
                right = runner._provider_endpoint_fingerprint(second.config.models[0], second)
                assert left == right
                assert left == canonical_sha256({"endpoint": EXPECTED_BASE})
            elif case in {"F01", "F03"}:
                p1, p2 = _provenance(s, first), _provenance(s, second)
                assert p1 != p2
                assert first.source_config_fingerprint != second.source_config_fingerprint
                if case == "F03":
                    path = s.root / "run"
                    runner._guard_run_dir(path, p1)
                    guard = path / "campaign-provenance-v2.json"
                    original = guard.read_bytes()
                    with pytest.raises(runner.V2CampaignRunError, match="incompatible"):
                        runner._guard_run_dir(path, p2)
                    assert guard.read_bytes() == original
            elif case == "F02":
                from ori.eval import provider_contract

                sources = set(runner._RUNNER_IMPLEMENTATION_SOURCES.values())
                assert {
                    Path(provider_contract.__file__),
                    Path(adapter.__file__),
                    Path(runner.__file__),
                } <= sources
            else:
                from ori.eval.v2.model_card import build_model_card
                from tests.support.v2_model_card import _MODEL, _campaign

                root = _campaign(s.root / "fixture")
                private = root / ".ori-private"
                private.mkdir()
                (private / "gemini-config.private.json").write_text(
                    json.dumps(
                        {
                            "endpoint": EXPECTED_BASE,
                            "model_url": DISTRACTOR_A,
                            "private_sentinel": "synthetic-gemini-private-config",
                        }
                    )
                )
                output = s.root / "public"
                card = build_model_card(root, output, model=_MODEL)
                serialized = json.dumps(card) + "".join(p.read_text() for p in output.iterdir())
                assert len(tuple(output.iterdir())) == 2
                for forbidden in (
                    EXPECTED_BASE,
                    DISTRACTOR_A,
                    str(root),
                    ".ori-private",
                    "gemini-config.private.json",
                    "synthetic-gemini-private-config",
                ):
                    assert forbidden not in serialized
                assert not (output / ".ori-private").exists()
            assert not s.counts
    assert visited == list(ids)
