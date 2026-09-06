from __future__ import annotations

import importlib
import json
import os
import platform
import socket
import subprocess
from collections import Counter
from contextlib import contextmanager
from functools import partial
from pathlib import Path
from types import SimpleNamespace

import anthropic
import anthropic._client as sdk
import httpx
from anthropic.lib.credentials import _providers, _workload

OFFICIAL = "https://api.anthropic.com"


def _binding_module():
    return importlib.import_module("ori.eval.anthropic_binding")


def _denied(*args, **kwargs):
    raise AssertionError("ANTHROPIC_TEST_EXTERNAL_OPERATION")


def _native_attempt_receiver(state, request):
    state.requests.append(request)
    state.counts["request"] += 1
    if state.request_fault is not None:
        raise state.request_fault
    assert request.method == "POST"
    body = json.loads(request.content)
    if request.url.path.endswith("/oauth/token"):
        state.counts["exchange"] += 1
        assert str(request.url) == OFFICIAL + "/v1/oauth/token"
        state.exchange_bodies.append(body)
        return httpx.Response(
            200,
            json={
                "access_token": "synthetic-exchanged",
                "refresh_token": "synthetic-refreshed",
                "token_type": "Bearer",
                "expires_in": 3600,
            },
        )
    state.counts["inference"] += 1
    assert request.url.path.endswith("/v1/messages")
    assert body["model"] == "synthetic-model"
    assert body["max_tokens"] == state.max_tokens
    return httpx.Response(
        200,
        json={
            "id": "synthetic-message",
            "type": "message",
            "role": "assistant",
            "model": "synthetic-model",
            "content": [{"type": "text", "text": state.text}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 3, "output_tokens": 2},
        },
    )


@contextmanager
def _anthropic_case_context(case, tmp_path, monkeypatch, environment=None):
    """Fresh synthetic configuration and real-SDK HTTP receivers for one cell."""
    root = tmp_path / case
    root.mkdir()
    state = SimpleNamespace(
        root=root,
        counts=Counter(),
        requests=[],
        kwargs=[],
        exchange_bodies=[],
        request_fault=None,
        http_close_fault=None,
        provider_close_fault=None,
        constructor_fault=None,
        text="synthetic-answer",
        max_tokens=17,
        close_depth=0,
        token_paths=set(),
        config_paths=set(),
        forbid_tokens=True,
        forbid_config=False,
    )
    with monkeypatch.context() as context:
        state.patch = context
        _binding_module()._check_sdk_compatibility.cache_clear()
        context.setattr(platform, "platform", lambda: "Synthetic-OS")
        context.setattr(platform, "mac_ver", lambda: ("0", ("", "", ""), "synthetic"))
        context.setattr(os, "environ", {"HOME": str(root), **(environment or {})})
        context.setattr(Path, "home", classmethod(lambda cls: root))
        for owner, attr in (
            (socket.socket, "connect"),
            (socket.socket, "connect_ex"),
            (socket, "getaddrinfo"),
            (subprocess, "Popen"),
        ):

            def external(*args, **kwargs):
                state.counts["external"] += 1
                return _denied(*args, **kwargs)

            context.setattr(owner, attr, external)
        context.setattr(sdk, "default_credentials", _denied)
        original_open = Path.open

        def guarded_open(path, *args, **kwargs):
            mode = args[0] if args else kwargs.get("mode", "r")
            if "r" in mode:
                if path in state.config_paths:
                    state.counts["config_read"] += 1
                    if state.forbid_config:
                        raise AssertionError("ANTHROPIC_PROFILE_REOPEN")
                if path in state.token_paths:
                    state.counts["token_read"] += 1
                    assert not state.forbid_tokens, "ANTHROPIC_PREPARATION_TOKEN_READ"
                if (
                    "credentials" in path.parts or path.name == "auth.json"
                ) and path.suffix != ".py":
                    assert path.is_relative_to(root), "ANTHROPIC_REAL_CREDENTIAL_FILE"
            return original_open(path, *args, **kwargs)

        context.setattr(Path, "open", guarded_open)
        async_init = httpx.AsyncClient.__init__
        sync_init = httpx.Client.__init__
        async_close = httpx.AsyncClient.aclose
        sync_close = httpx.Client.close
        sdk_init = anthropic.AsyncAnthropic.__init__
        state.native_sdk_init = sdk_init
        state.native_async_close = async_close
        transport = httpx.MockTransport(partial(_native_attempt_receiver, state))

        def init_async(client, *args, **kwargs):
            state.counts["async_constructor"] += 1
            kwargs["transport"] = transport
            kwargs["trust_env"] = False
            async_init(client, *args, **kwargs)

        def init_sync(client, *args, **kwargs):
            state.counts["sync_constructor"] += 1
            kwargs["transport"] = transport
            kwargs["trust_env"] = False
            sync_init(client, *args, **kwargs)

        async def close_async(client):
            state.counts["http_close"] += 1
            await async_close(client)
            if state.http_close_fault is not None:
                raise state.http_close_fault

        def close_sync(client):
            state.counts["sync_close"] += 1
            sync_close(client)

        def init_sdk(client, *args, **kwargs):
            state.counts["sdk_constructor"] += 1
            state.kwargs.append(dict(kwargs))
            assert {
                "base_url",
                "api_key",
                "auth_token",
                "credentials",
                "default_headers",
                "http_client",
            } <= kwargs.keys()
            assert "profile" not in kwargs and "config" not in kwargs
            if state.constructor_fault is not None:
                raise state.constructor_fault
            sdk_init(client, *args, **kwargs)

        context.setattr(httpx.AsyncClient, "__init__", init_async)
        context.setattr(httpx.Client, "__init__", init_sync)
        context.setattr(httpx.AsyncClient, "aclose", close_async)
        context.setattr(httpx.Client, "close", close_sync)
        context.setattr(anthropic.AsyncAnthropic, "__init__", init_sdk)
        context.setattr(anthropic.AsyncAnthropic, "close", _denied)
        for provider in (_providers.CredentialsFile, _workload.WorkloadIdentityCredentials):
            original_call, original_close = provider.__call__, provider.close

            def invoke(obj, *args, _original=original_call, **kwargs):
                state.counts["provider_call"] += 1
                assert not state.forbid_tokens, "ANTHROPIC_PREPARATION_PROVIDER_CALL"
                return _original(obj, *args, **kwargs)

            def close_provider(obj, _original=original_close):
                owned_call = state.close_depth == 0
                state.counts["provider_close"] += int(owned_call)
                if isinstance(obj, _workload.WorkloadIdentityCredentials):
                    state.counts["workload_close" if owned_call else "borrowed_workload_close"] += 1
                state.close_depth += 1
                try:
                    _original(obj)
                finally:
                    state.close_depth -= 1
                if owned_call and state.provider_close_fault is not None:
                    raise state.provider_close_fault

            context.setattr(provider, "__call__", invoke)
            context.setattr(provider, "close", close_provider)
        yield state
        assert state.counts["external"] == 0


def _synthetic_profile(
    state, *, explicit=True, federation=False, malformed=False, base_url=None, client_id=None
):
    directory = state.root / ".config" / "anthropic"
    path = directory / "configs" / "default.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    token = directory / "credentials" / "default.json"
    identity = state.root / "identity-token"
    auth = {"type": "oidc_federation" if federation else "user_oauth"}
    if federation:
        auth.update(
            federation_rule_id="synthetic-rule",
            identity_token={
                "source": "file",
                "path": str(identity),
            },
        )
        identity.write_text("synthetic-identity")
        state.token_paths.add(identity)
    if client_id:
        auth["client_id"] = client_id
    payload = {"authentication": auth, "organization_id": "synthetic-org"}
    if base_url is not None:
        payload["base_url"] = base_url
    path.write_text("{malformed" if malformed else json.dumps(payload))
    state.config_paths.add(path)
    state.token_paths.add(token)
    if explicit:
        os.environ["ANTHROPIC_PROFILE"] = "default"
    return path, token, payload
