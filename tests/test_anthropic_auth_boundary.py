"""Offline checks of the locked SDK's native authentication admission boundary."""

import asyncio
import os
import socket
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import anthropic
import anthropic._client as sdk
import httpx
import pytest
from anthropic._models import FinalRequestOptions
from anthropic.lib.credentials._types import CredentialResult

from ori.eval import adapter


def _deny(*args, **kwargs):
    raise AssertionError("external operation prohibited")


def _isolate(context, environment):
    for key in tuple(os.environ):
        context.delenv(key)
    for key, value in environment.items():
        context.setenv(key, value)
    context.setattr(socket.socket, "connect", _deny)
    context.setattr(socket.socket, "connect_ex", _deny)
    context.setattr(socket, "getaddrinfo", _deny)
    context.setattr(subprocess, "Popen", _deny)
    context.setattr(sdk, "_warn_env_shadow", lambda **kwargs: None)
    context.setattr(sdk, "_warn_explicit_shadow", lambda **kwargs: None)


def _client(context, *, discovered=None, default_headers=None, native_init=None):
    context.setattr(sdk, "default_credentials", lambda **kwargs: discovered)
    if native_init is not None:
        client = object.__new__(anthropic.AsyncAnthropic)
        native_init(
            client,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(_deny), trust_env=False),
            default_headers=default_headers,
        )
        return client
    return anthropic.AsyncAnthropic(
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(_deny), trust_env=False),
        default_headers=default_headers,
    )


def _message():
    return SimpleNamespace(
        content=[{"type": "text", "text": "synthetic"}],
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=3, output_tokens=2),
    )


async def _call(binding=None):
    return await adapter.call_provider_text(
        model="anthropic/synthetic",
        system="synthetic",
        messages=[],
        **({"anthropic_binding": binding} if binding is not None else {}),
    )


def test_anthropic_native_auth_header_parity(monkeypatch, tmp_path, subtests):
    from tests.support.anthropic_binding import (
        _anthropic_case_context,
        _binding_module,
        _synthetic_profile,
    )

    cases = [
        ("H0", {}, None, None, False),
        ("H1", {"ANTHROPIC_API_KEY": "synthetic"}, None, None, True),
        ("H2", {"ANTHROPIC_AUTH_TOKEN": "synthetic"}, None, None, True),
        ("H3", {"ANTHROPIC_CUSTOM_HEADERS": "X-Api-Key: synthetic"}, None, None, True),
        ("H4", {"ANTHROPIC_CUSTOM_HEADERS": "Authorization: Bearer synthetic"}, None, None, True),
        ("H5", {"ANTHROPIC_CUSTOM_HEADERS": "x-api-key: synthetic"}, None, None, False),
        ("H6", {"ANTHROPIC_CUSTOM_HEADERS": "authorization: Bearer synthetic"}, None, None, False),
        ("H7", {}, CredentialResult(provider=_deny), None, True),
        ("H8", {}, None, {"X-Api-Key": anthropic.Omit()}, False),
    ]
    visited = []
    for case, environment, discovered, headers, accepted in cases:
        visited.append(case)
        with (
            subtests.test(case=case),
            _anthropic_case_context(case, tmp_path, monkeypatch, environment) as state,
        ):
            context = state.patch

            async def scenario():
                client = _client(
                    context,
                    discovered=discovered,
                    default_headers=headers,
                    native_init=state.native_sdk_init,
                )
                reference_transport = client._client
                create = AsyncMock(return_value=_message())
                context.setattr(client.messages, "create", create)
                try:
                    options = FinalRequestOptions.construct(method="post", url="/v1/messages")
                    if accepted:
                        client._build_headers(options)
                    else:
                        with pytest.raises(TypeError):
                            client._build_headers(options)
                    if case == "H7":
                        _synthetic_profile(state)
                    if case == "H8":
                        context.setattr(_binding_module(), "_environment_headers", lambda: headers)
                    state.counts.clear()
                    context.setattr(sdk, "default_credentials", _deny)
                    native_class = anthropic.AsyncAnthropic

                    # Preserve the real reference admission; replace only allocation.
                    def construct(**kwargs):
                        assert set(kwargs) == {
                            "base_url",
                            "api_key",
                            "auth_token",
                            "credentials",
                            "default_headers",
                            "http_client",
                        }
                        assert kwargs["base_url"] == "https://api.anthropic.com"
                        assert kwargs["api_key"] == (
                            "synthetic" if case == "H1" else "" if case in {"H3", "H4"} else None
                        )
                        assert kwargs["auth_token"] == ("synthetic" if case == "H2" else None)
                        assert (kwargs["credentials"] is not None) == (case == "H7")
                        assert isinstance(kwargs["http_client"], httpx.AsyncClient)
                        assert isinstance(kwargs["default_headers"], dict)
                        state.counts["sdk_constructor"] += 1
                        client._client = kwargs["http_client"]
                        return client

                    # Compatibility checks see the actual class before its factory replacement.
                    _binding_module()._check_sdk_compatibility()
                    context.setattr(anthropic, "AsyncAnthropic", construct)
                    context.setattr(_binding_module(), "_check_sdk_compatibility", lambda: None)
                    # The pure admission calls the native unbound validator, not the factory.
                    construct._validate_headers = native_class._validate_headers
                    response = await _call()
                    if accepted:
                        assert response.error is None
                        assert create.await_count == 1
                        assert state.counts["http_close"] == 1
                        assert state.counts["provider_close"] == (1 if case == "H7" else 0)
                    else:
                        assert response.provider_metrics["infra_error_subtype"] == "PROVIDER_AUTH"
                        assert response.provider_metrics["infra_retryable"] is False
                        assert create.await_count == 0
                        assert state.counts["sdk_constructor"] == 0
                        assert state.counts["http_close"] == 0
                        assert state.counts["provider_close"] == 0
                finally:
                    await state.native_async_close(reference_transport)

            asyncio.run(scenario())
    assert visited == [f"H{i}" for i in range(9)]


def test_anthropic_early_auth_cleanup(monkeypatch, tmp_path, subtests):
    from tests.support.anthropic_binding import _anthropic_case_context, _binding_module

    visited = []
    for case in ("L0", "L1", "L2"):
        visited.append(case)
        with (
            subtests.test(case=case),
            _anthropic_case_context(
                case, tmp_path, monkeypatch, {"ANTHROPIC_API_KEY": "synthetic"}
            ) as state,
        ):
            context = state.patch

            async def scenario():
                binding = _binding_module().prepare_anthropic_binding("anthropic/synthetic")
                client = _client(context, native_init=state.native_sdk_init)
                reference_transport = client._client
                state.counts.clear()
                state.http_close_fault = (
                    None
                    if case == "L0"
                    else RuntimeError("synthetic cleanup")
                    if case == "L1"
                    else asyncio.CancelledError("synthetic cleanup cancellation")
                )
                create = AsyncMock(return_value=_message())
                context.setattr(client.messages, "create", create)

                def invalid_headers(*args):
                    raise TypeError("synthetic post-allocation auth rejection")

                context.setattr(client, "_validate_headers", invalid_headers)

                def construct(**kwargs):
                    assert set(kwargs) == {
                        "base_url",
                        "api_key",
                        "auth_token",
                        "credentials",
                        "default_headers",
                        "http_client",
                    }
                    assert kwargs["base_url"] == "https://api.anthropic.com"
                    assert kwargs["api_key"] == "synthetic"
                    assert kwargs["auth_token"] is kwargs["credentials"] is None
                    assert isinstance(kwargs["http_client"], httpx.AsyncClient)
                    assert isinstance(kwargs["default_headers"], dict)
                    client._client = kwargs["http_client"]
                    return client

                context.setattr(anthropic, "AsyncAnthropic", construct)
                try:
                    if case == "L2":
                        with pytest.raises(asyncio.CancelledError):
                            await _call(binding)
                    else:
                        response = await _call(binding)
                        assert response.provider_metrics["infra_error_subtype"] == "PROVIDER_AUTH"
                        assert response.provider_metrics["infra_retryable"] is False
                        assert (
                            "Anthropic authentication configuration is unavailable"
                            in response.error
                        )
                        assert "synthetic cleanup" not in response.error
                    assert state.counts["http_close"] == 1
                    assert state.counts["provider_close"] == 0
                    assert create.await_count == 0
                finally:
                    await state.native_async_close(reference_transport)

            asyncio.run(scenario())
    assert visited == ["L0", "L1", "L2"]


def test_anthropic_non_auth_fault_boundaries(monkeypatch, tmp_path, subtests):
    from tests.support.anthropic_binding import _anthropic_case_context, _binding_module

    visited = []
    for case in ("B0", "B1", "B2", "B3", "B4"):
        visited.append(case)
        with (
            subtests.test(case=case),
            _anthropic_case_context(
                case, tmp_path, monkeypatch, {"ANTHROPIC_API_KEY": "synthetic"}
            ) as state,
        ):
            context = state.patch

            async def scenario():
                fault = (TypeError if case in {"B0", "B1", "B2"} else anthropic.AnthropicError)(
                    case
                )
                binding = _binding_module().prepare_anthropic_binding("anthropic/synthetic")
                client = _client(context, native_init=state.native_sdk_init)
                reference_transport = client._client
                state.counts.clear()
                create = AsyncMock(side_effect=fault)
                constructors = []

                def construct(**kwargs):
                    assert set(kwargs) == {
                        "base_url",
                        "api_key",
                        "auth_token",
                        "credentials",
                        "default_headers",
                        "http_client",
                    }
                    assert kwargs["base_url"] == "https://api.anthropic.com"
                    assert kwargs["api_key"] == "synthetic"
                    assert kwargs["auth_token"] is kwargs["credentials"] is None
                    assert isinstance(kwargs["http_client"], httpx.AsyncClient)
                    assert isinstance(kwargs["default_headers"], dict)
                    constructors.append(case)
                    if case in {"B0", "B3"}:
                        raise fault
                    client._client = kwargs["http_client"]
                    return client

                context.setattr(client.messages, "create", create)
                context.setattr(anthropic, "AsyncAnthropic", construct)
                if case == "B1":

                    def headers(_self):
                        raise fault

                    context.setattr(type(client), "default_headers", property(headers))
                try:
                    if case in {"B0", "B1", "B2"}:
                        with pytest.raises(RuntimeError) as caught:
                            await _call(binding)
                        assert type(caught.value).__name__ == "ProviderAdapterInternalError"
                        assert caught.value.__cause__ is fault
                    else:
                        response = await _call(binding)
                        assert response.provider_metrics["infra_error_subtype"] == "PROVIDER_ERROR"
                        assert response.provider_metrics["infra_retryable"] is False
                    assert constructors == [case]
                    assert create.await_count == (1 if case in {"B2", "B4"} else 0)
                    assert state.counts["http_close"] == 1
                    assert state.counts["provider_close"] == 0
                finally:
                    await state.native_async_close(reference_transport)

            asyncio.run(scenario())
    assert visited == [f"B{i}" for i in range(5)]
