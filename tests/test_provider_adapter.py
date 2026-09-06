"""Direct adapter regressions for normalized provider turns."""

from __future__ import annotations

import asyncio
import socket
import subprocess
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx
import openai
import pytest

from ori.eval import adapter, codex_oauth
from ori.eval.adapter import _provider_exception_metrics, call_provider_text
from ori.eval.provider_contract import (
    ProviderAuthenticationError,
    ProviderCapabilityError,
    ProviderProtocolError,
)
from tests.support.provider_origins import (
    CLEARED_ENVIRONMENT,
    DENIED_ORIGINS,
    SYNTHETIC_KEYS,
    VALID_ORIGINS,
)


def _sdk_response(
    *,
    content: str | None = '{"query":"MATCH (n) RETURN n LIMIT 1"}',
    finish_reason: str = "stop",
    message_fields: dict[str, Any] | None = None,
    usage: object = SimpleNamespace(
        prompt_tokens=13,
        completion_tokens=5,
        total_tokens=18,
    ),
) -> SimpleNamespace:
    fields: dict[str, Any] = {
        "content": content,
        "refusal": None,
        "tool_calls": None,
    }
    fields.update(message_fields or {})
    return SimpleNamespace(
        id="chatcmpl-adapter-test",
        model="remote-model",
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(**fields),
                finish_reason=finish_reason,
            )
        ],
        usage=usage,
    )


def _call_with_response(
    monkeypatch: pytest.MonkeyPatch,
    response: object,
    *,
    api_surface: str = "auto",
    base_url: str | None = "https://compatible.example/v1",
    set_compat_key: bool = True,
    request_timeout_seconds: float | None = None,
    model: str = "openai-compat/provider/model",
):
    captured: dict[str, Any] = {}

    class FakeCompletions:
        async def create(self, **kwargs: Any) -> object:
            captured["request"] = kwargs
            return response

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs: Any) -> None:
            captured["client"] = kwargs
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(openai, "AsyncOpenAI", FakeAsyncOpenAI)
    if set_compat_key:
        monkeypatch.setenv("OPENAI_COMPAT_API_KEY", "test-only-key")
    else:
        monkeypatch.delenv("OPENAI_COMPAT_API_KEY", raising=False)
    result = asyncio.run(
        call_provider_text(
            model=model,
            messages=[{"role": "user", "content": "test"}],
            system="system",
            base_url=base_url,
            api_surface=api_surface,
            request_timeout_seconds=request_timeout_seconds,
        )
    )
    return result, captured


def test_chat_adapter_normalizes_text_usage_and_surface(monkeypatch) -> None:
    response, captured = _call_with_response(monkeypatch, _sdk_response())

    assert response.error is None
    assert response.raw_text == '{"query":"MATCH (n) RETURN n LIMIT 1"}'
    assert response.tokens_input == 13
    assert response.tokens_output == 5
    assert response.provider_metrics["provider_turn_status"] == "completed"
    assert response.provider_metrics["requested_api_surface"] == "auto"
    assert response.provider_metrics["resolved_api_surface"] == "chat_completions"
    assert response.provider_metrics["response_id"] == "chatcmpl-adapter-test"
    assert response.provider_metrics["usage_reported"] is True
    assert response.provider_metrics["usage_complete"] is True
    assert captured["request"]["model"] == "provider/model"


def test_openai_compatible_client_receives_explicit_request_timeout(monkeypatch) -> None:
    response, captured = _call_with_response(
        monkeypatch,
        _sdk_response(),
        request_timeout_seconds=37.5,
    )

    assert response.error is None
    assert captured["client"]["timeout"] == 37.5


def test_direct_adapter_uses_only_endpoint_scoped_credential(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-key")
    monkeypatch.setenv("NOUS_API_KEY", "nous-key")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")

    response, captured = _call_with_response(
        monkeypatch,
        _sdk_response(),
        base_url="https://openrouter.ai/api/v1",
        set_compat_key=False,
    )

    assert response.error is None
    assert captured["client"]["api_key"] == "openrouter-key"
    assert response.provider_metrics["endpoint_family"] == "openrouter"
    assert response.provider_metrics["credential_source"] == "OPENROUTER_API_KEY"


def test_local_generic_compat_endpoint_never_borrows_official_openai_key(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "official-openai-key")

    response, captured = _call_with_response(
        monkeypatch,
        _sdk_response(),
        base_url="http://127.0.0.1:8000/v1",
        set_compat_key=False,
    )

    assert response.error is None
    assert captured["client"]["api_key"] == "not-needed"
    assert response.provider_metrics["endpoint_family"] == "generic"
    assert response.provider_metrics["credential_source"] is None


def test_remote_generic_compat_endpoint_requires_explicit_compat_key(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "official-openai-key")

    response, captured = _call_with_response(
        monkeypatch,
        _sdk_response(),
        base_url="https://generic.example/v1",
        set_compat_key=False,
    )

    assert "request" not in captured
    assert response.error is not None
    assert response.provider_metrics["infra_error_subtype"] == "PROVIDER_AUTH"
    assert response.provider_metrics["infra_retryable"] is False


def test_compat_provider_without_endpoint_fails_closed(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_COMPAT_API_KEY", "compat-key")
    monkeypatch.setenv("OPENAI_API_KEY", "official-openai-key")

    response, captured = _call_with_response(
        monkeypatch,
        _sdk_response(),
        base_url=None,
    )

    assert "client" not in captured
    assert "request" not in captured
    assert response.error is not None
    assert "requires an explicit model_base_url" in response.error
    assert response.provider_metrics["infra_error_subtype"] == "PROVIDER_CAPABILITY"
    assert response.provider_metrics["infra_retryable"] is False


@pytest.mark.parametrize(
    "base_url",
    [
        "https://openrouter.ai/api/v1",
        "https://inference-api.nousresearch.com/v1",
        "https://api.openai.com/v1",
    ],
)
def test_compat_override_does_not_replace_scoped_provider_keys(
    monkeypatch,
    base_url: str,
) -> None:
    for name in (
        "OPENAI_COMPAT_API_KEY",
        "OPENROUTER_API_KEY",
        "NOUS_API_KEY",
        "NOUS_PORTAL_API_KEY",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENAI_COMPAT_API_KEY", "compat-key")

    response, captured = _call_with_response(
        monkeypatch,
        _sdk_response(),
        base_url=base_url,
        set_compat_key=False,
    )

    assert "request" not in captured
    assert response.error is not None
    assert response.provider_metrics["infra_error_subtype"] == "PROVIDER_AUTH"
    assert response.provider_metrics["infra_retryable"] is False


def test_missing_nous_credential_is_nonretryable_auth_failure(monkeypatch) -> None:
    for name in (
        "NOUS_API_KEY",
        "NOUS_PORTAL_API_KEY",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    response, captured = _call_with_response(
        monkeypatch,
        _sdk_response(),
        base_url="https://inference-api.nousresearch.com/v1",
        set_compat_key=False,
    )

    assert "request" not in captured
    assert response.error is not None
    assert response.provider_metrics["infra_error_subtype"] == "PROVIDER_AUTH"
    assert response.provider_metrics["infra_retryable"] is False


def test_laguna_nullable_content_tool_call_is_output_invalid_not_harness_error(
    monkeypatch,
) -> None:
    response, _ = _call_with_response(
        monkeypatch,
        _sdk_response(
            content=None,
            finish_reason="tool_calls",
            message_fields={
                "tool_calls": [
                    SimpleNamespace(
                        id="call-1",
                        function=SimpleNamespace(
                            name="cypher_query.run",
                            arguments='{"query":"MATCH (n) RETURN n LIMIT 1"}',
                        ),
                    )
                ]
            },
        ),
    )

    assert isinstance(response.raw_text, str)
    assert response.raw_text == ""
    assert response.error is None
    assert response.provider_metrics["provider_turn_status"] == "tool_calls"
    assert response.provider_metrics["model_output_error"] is True
    assert response.provider_metrics["model_output_subtype"] == "TOOL_CALL_ONLY"
    assert response.provider_metrics["tool_argument_parse_statuses"] == ["valid"]


@pytest.mark.parametrize(
    ("sdk_response", "status", "subtype"),
    [
        (
            _sdk_response(
                content=None,
                message_fields={"refusal": "request refused"},
            ),
            "refused",
            "REFUSAL",
        ),
        (
            _sdk_response(
                content=None,
                message_fields={"reasoning_content": "unfinished reasoning"},
            ),
            "reasoning_only",
            "REASONING_ONLY",
        ),
        (_sdk_response(finish_reason="length"), "truncated", "TRUNCATED"),
        (
            _sdk_response(finish_reason="content_filter"),
            "content_filtered",
            "CONTENT_FILTERED",
        ),
        (_sdk_response(content=None), "empty", "EMPTY_OUTPUT"),
    ],
)
def test_nonfinal_direct_turns_are_typed_model_output_failures(
    monkeypatch,
    sdk_response: object,
    status: str,
    subtype: str,
) -> None:
    response, _ = _call_with_response(monkeypatch, sdk_response)

    assert response.raw_text == ""
    assert response.error is None
    assert response.provider_metrics["provider_turn_status"] == status
    assert response.provider_metrics["model_output_subtype"] == subtype


def test_reasoning_and_refusal_metadata_are_preserved(monkeypatch) -> None:
    response, _ = _call_with_response(
        monkeypatch,
        _sdk_response(
            content=None,
            message_fields={
                "refusal": "blocked",
                "reasoning_content": "considered policy",
            },
        ),
    )

    assert response.thinking == "considered policy"
    assert response.provider_metrics["reasoning"] == "considered policy"
    assert response.provider_metrics["refusal"] == "blocked"


def test_missing_usage_is_unknown_in_metrics_and_zero_only_at_legacy_boundary(
    monkeypatch,
) -> None:
    response, _ = _call_with_response(
        monkeypatch,
        _sdk_response(usage=None),
    )

    assert response.tokens_input == 0
    assert response.tokens_output == 0
    assert response.provider_metrics["usage_reported"] is False
    assert response.provider_metrics["usage_complete"] is False
    assert response.provider_metrics["usage"] == {
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
    }


def test_malformed_chat_envelope_is_nonretryable_provider_protocol_failure(
    monkeypatch,
) -> None:
    response, _ = _call_with_response(monkeypatch, SimpleNamespace(choices=None))

    assert response.raw_text == ""
    assert response.error is not None
    assert "missing choices" in response.error
    assert response.provider_metrics["infra_scope"] == "provider"
    assert response.provider_metrics["infra_error_subtype"] == "PROVIDER_PROTOCOL"
    assert response.provider_metrics["infra_retryable"] is False


def test_unsupported_responses_selection_fails_before_provider_call(monkeypatch) -> None:
    response, captured = _call_with_response(
        monkeypatch,
        _sdk_response(),
        api_surface="responses",
    )

    assert "request" not in captured
    assert response.error is not None
    assert response.provider_metrics["infra_error_subtype"] == "PROVIDER_CAPABILITY"
    assert response.provider_metrics["infra_retryable"] is False
    assert response.provider_metrics["requested_api_surface"] == "responses"
    assert response.provider_metrics["resolved_api_surface"] == "responses"


@pytest.mark.parametrize(
    ("error", "subtype", "retryable"),
    [
        (httpx.ReadTimeout("timeout"), "PROVIDER_TIMEOUT", True),
        (httpx.ConnectError("connect"), "PROVIDER_TRANSPORT", True),
    ],
)
def test_provider_transport_errors_use_typed_metadata(
    error: Exception,
    subtype: str,
    retryable: bool,
) -> None:
    metrics = _provider_exception_metrics(error)
    assert metrics["infra_error_subtype"] == subtype
    assert metrics["infra_retryable"] is retryable


@pytest.mark.parametrize(
    "base_url",
    (
        "https://hostile.example/v1",
        "http://api.openai.com/v1",
        "https://api.openai.com:8443/v1",
    ),
)
def test_official_openai_rejects_an_untrusted_origin_before_client_creation(
    monkeypatch,
    base_url: str,
) -> None:
    created = False

    class FakeAsyncOpenAI:
        def __init__(self, **_kwargs: Any) -> None:
            nonlocal created
            created = True

    monkeypatch.setattr(openai, "AsyncOpenAI", FakeAsyncOpenAI)
    monkeypatch.setenv("OPENAI_API_KEY", "official-key")

    response = asyncio.run(
        call_provider_text(
            model="openai/gpt-test",
            messages=[{"role": "user", "content": "test"}],
            system="system",
            base_url=base_url,
        )
    )

    assert created is False
    assert response.provider_metrics["infra_error_subtype"] == "PROVIDER_CAPABILITY"
    assert response.provider_metrics["infra_retryable"] is False


def test_official_openai_and_gemini_pass_only_their_scoped_keys(monkeypatch) -> None:
    captured: list[dict[str, Any]] = []

    class FakeCompletions:
        async def create(self, **_kwargs: Any) -> object:
            return _sdk_response()

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs: Any) -> None:
            captured.append(kwargs)
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(openai, "AsyncOpenAI", FakeAsyncOpenAI)
    monkeypatch.setenv("OPENAI_API_KEY", "official-key")
    monkeypatch.setenv("OPENAI_COMPAT_API_KEY", "compat-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-key")
    monkeypatch.setenv("NOUS_API_KEY", "nous-key")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")

    openai_response = asyncio.run(
        call_provider_text(
            model="openai/gpt-test",
            messages=[{"role": "user", "content": "test"}],
            system="system",
        )
    )
    gemini_response = asyncio.run(
        call_provider_text(
            model="gemini/gemini-test",
            messages=[{"role": "user", "content": "test"}],
            system="system",
        )
    )

    assert captured[0]["api_key"] == "official-key"
    assert captured[0]["base_url"] == "https://api.openai.com/v1"
    assert openai_response.provider_metrics["credential_source"] == "OPENAI_API_KEY"
    assert captured[1]["api_key"] == "gemini-key"
    assert gemini_response.provider_metrics["credential_source"] == "GEMINI_API_KEY"


def test_provider_endpoint_telemetry_strips_userinfo_path_and_query(monkeypatch) -> None:
    response, _ = _call_with_response(
        monkeypatch,
        _sdk_response(),
        base_url="https://user:pass@generic.example/v1?token=secret",
    )

    assert response.error is None
    assert response.provider_metrics["provider_endpoint"] == "https://generic.example"


@pytest.mark.parametrize(
    ("explicit", "inline", "expected", "family", "source", "key"),
    [
        ("https://openrouter.ai/api/v1", "https://inference-api.nousresearch.com/v1",
         "https://openrouter.ai/api/v1", "openrouter", "OPENROUTER_API_KEY", "test-router"),
        ("https://inference-api.nousresearch.com/v1", "https://openrouter.ai/api/v1",
         "https://inference-api.nousresearch.com/v1", "nous", "NOUS_API_KEY", "test-nous"),
        ("http://127.0.0.1:8000/v1", "https://openrouter.ai/api/v1",
         "http://127.0.0.1:8000/v1", "generic", None, "not-needed"),
        (None, "https://inference-api.nousresearch.com/v1",
         "https://inference-api.nousresearch.com/v1", "nous", "NOUS_API_KEY", "test-nous"),
        ("", "https://inference-api.nousresearch.com/v1",
         "https://inference-api.nousresearch.com/v1", "nous", "NOUS_API_KEY", "test-nous"),
        ("https://openrouter.ai/api/v1", "https://openrouter.ai/api/v1",
         "https://openrouter.ai/api/v1", "openrouter", "OPENROUTER_API_KEY", "test-router"),
    ],
    ids=["explicit-router", "explicit-nous", "explicit-local", "inline", "empty", "equal"],
)
def test_compatible_explicit_destination_precedes_inline(
    monkeypatch, explicit, inline, expected, family, source, key,
) -> None:
    for variable in (
        "OPENAI_API_KEY", "OPENAI_COMPAT_API_KEY", "OPENROUTER_API_KEY",
        "NOUS_API_KEY", "NOUS_PORTAL_API_KEY", "OPENAI_COMPAT_BASE_URL", "OPENAI_BASE_URL",
    ):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-router")
    monkeypatch.setenv("NOUS_API_KEY", "test-nous")
    response, captured = _call_with_response(
        monkeypatch, _sdk_response(), base_url=explicit, set_compat_key=False,
        model=f"openai-compat/provider/model@{inline}",
    )
    assert response.error is None
    assert captured["client"]["base_url"] == expected
    assert captured["client"]["api_key"] == key
    assert captured["request"]["model"] == "provider/model"
    assert captured["request"]["messages"] == [
        {"role": "system", "content": "system"}, {"role": "user", "content": "test"},
    ]
    assert response.raw_text == '{"query":"MATCH (n) RETURN n LIMIT 1"}'
    assert response.provider_metrics["endpoint_family"] == family
    assert response.provider_metrics["credential_source"] == source


def test_direct_scoped_origin_admission(monkeypatch, subtests) -> None:
    for case, endpoint, family, source, key in DENIED_ORIGINS + VALID_ORIGINS:
        with subtests.test(msg=case), monkeypatch.context() as scoped:
            for variable in CLEARED_ENVIRONMENT:
                scoped.delenv(variable, raising=False)
            for variable, value in SYNTHETIC_KEYS:
                scoped.setenv(variable, value)
            counts = {"constructors": 0, "requests": 0}
            captured = {}

            class FakeCompletions:
                async def create(self, **kwargs):
                    counts["requests"] += 1
                    captured["request"] = kwargs
                    return _sdk_response()

            class FakeClient:
                def __init__(self, **kwargs):
                    counts["constructors"] += 1
                    captured["client"] = kwargs
                    self.chat = SimpleNamespace(completions=FakeCompletions())

            class ForbiddenHTTPClient:
                def __init__(self, *args, **kwargs):
                    counts["constructors"] += 1
                    raise AssertionError("Direct must use only the fake SDK")
                async def post(self, *args, **kwargs):
                    counts["requests"] += 1
                    raise AssertionError("Direct must not issue an HTTP request")

            scoped.setattr(openai, "AsyncOpenAI", FakeClient)
            scoped.setattr(httpx, "AsyncClient", ForbiddenHTTPClient)
            response = asyncio.run(call_provider_text(
                model="openai-compat/provider/model", base_url=endpoint,
                system="system", messages=[{"role": "user", "content": "synthetic question"}],
            ))
            if "-D" in case:
                assert counts["constructors"] == 0
                assert counts["requests"] == 0
                assert response.raw_text == ""
                assert response.error is not None
                assert response.provider_metrics["infra_error_subtype"] == "PROVIDER_AUTH"
                assert response.provider_metrics["infra_retryable"] is False
            else:
                assert counts["constructors"] == 1
                assert counts["requests"] == 1
                assert captured["client"]["base_url"] == endpoint
                assert captured["client"]["api_key"] == key
                assert captured["request"]["model"] == "provider/model"
                assert response.error is None
                assert response.raw_text == '{"query":"MATCH (n) RETURN n LIMIT 1"}'
                assert response.provider_metrics["endpoint_family"] == family
                assert response.provider_metrics["credential_source"] == source


def _adapter_fault_fixture(scoped, fault: BaseException):
    counts = {"constructors": 0, "requests": 0}

    def forbidden(*args, **kwargs):
        raise AssertionError("S1-05F external activity forbidden")

    for variable in CLEARED_ENVIRONMENT + (
        "CODEX_API_KEY",
        "CODEX_AUTH_FILE",
        "CODEX_BASE_URL",
        "CODEX_COMPAT_API_KEY",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
    ):
        scoped.delenv(variable, raising=False)
    scoped.setenv("OPENAI_COMPAT_API_KEY", "test-internal-boundary")
    scoped.setattr(socket, "getaddrinfo", forbidden)
    scoped.setattr(socket, "create_connection", forbidden)
    scoped.setattr(socket.socket, "connect", forbidden)
    scoped.setattr(subprocess, "Popen", forbidden)
    scoped.setattr(httpx, "AsyncClient", forbidden)
    scoped.setattr(anthropic, "AsyncAnthropic", forbidden)
    scoped.setattr(codex_oauth, "codex_auth_path", forbidden)

    class Completions:
        async def create(self, **kwargs):
            counts["requests"] += 1
            raise fault

    class Client:
        def __init__(self, **kwargs):
            counts["constructors"] += 1
            self.chat = SimpleNamespace(completions=Completions())

    scoped.setattr(openai, "AsyncOpenAI", Client)
    return counts


def _call_fault_adapter(*, model="openai-compat/provider/model", base_url="https://provider.invalid/v1"):
    return asyncio.run(
        call_provider_text(
            model=model,
            base_url=base_url,
            system="system",
            messages=[{"role": "user", "content": "synthetic boundary"}],
        )
    )


def _internal_fault(index):
    classes = (
        ValueError,
        AttributeError,
        TypeError,
        TimeoutError,
        RuntimeError,
        type("APIConnectionError", (Exception,), {}),
        Exception,
    )
    fault = classes[index](f"S1-05F-U{index}")
    if index == 6:
        fault.status_code = 503
    return fault


def test_adapter_internal_fault_boundary(monkeypatch, subtests) -> None:
    visited = []
    for index in range(7):
        case = f"U{index}"
        visited.append(case)
        with subtests.test(msg=case), monkeypatch.context() as scoped:
            fault = _internal_fault(index)
            counts = _adapter_fault_fixture(scoped, fault)
            with pytest.raises(RuntimeError) as error:
                _call_fault_adapter()
            assert type(error.value) is adapter.ProviderAdapterInternalError
            assert error.value.__cause__ is fault
            assert str(error.value) == str(fault)
            assert counts == {"constructors": 1, "requests": 1}
    assert visited == [f"U{index}" for index in range(7)]


def _known_failure(index):
    message = f"S1-05F-K{index:02d}"
    request = httpx.Request("POST", "https://provider.invalid/v1")
    if index < 33:
        library, position = divmod(index, 11)
        sdk = (None, openai, anthropic)[library]
        if position == 0:
            fault = (
                httpx.ReadTimeout(message, request=request)
                if sdk is None
                else sdk.APITimeoutError(request)
            )
            return fault, "PROVIDER_TIMEOUT", True
        if position == 1:
            fault = (
                httpx.ConnectError(message, request=request)
                if sdk is None
                else sdk.APIConnectionError(message=message, request=request)
            )
            return fault, "PROVIDER_TRANSPORT", True
        status = (401, 403, 408, 429, 400, 404, 500, 503, 302)[position - 2]
        response = httpx.Response(status, request=request)
        fault = (
            httpx.HTTPStatusError(message, request=request, response=response)
            if sdk is None
            else sdk.APIStatusError(message, response=response, body={"synthetic": message})
        )
        expected = {
            401: ("PROVIDER_AUTH", False),
            403: ("PROVIDER_AUTH", False),
            408: ("PROVIDER_TIMEOUT", True),
            429: ("PROVIDER_RATE_LIMIT", True),
            400: ("PROVIDER_REQUEST", False),
            404: ("PROVIDER_REQUEST", False),
            500: ("PROVIDER_SERVER", True),
            503: ("PROVIDER_SERVER", True),
            302: ("PROVIDER_PROTOCOL", False),
        }[status]
        return fault, *expected
    if index in (33, 34):
        sdk = openai if index == 33 else anthropic
        return (
            sdk.APIResponseValidationError(
                httpx.Response(200, request=request), {"synthetic": message}, message=message
            ),
            "PROVIDER_PROTOCOL",
            False,
        )
    if index in (35, 36):
        sdk = openai if index == 35 else anthropic
        return sdk.APIError(message, request, body={"synthetic": message}), "PROVIDER_ERROR", False
    if index == 37:
        return codex_oauth.CodexResponseStreamError(message), "PROVIDER_ERROR", True
    cls = (ProviderAuthenticationError, ProviderCapabilityError, ProviderProtocolError)[index - 38]
    return cls(message), cls.code, False


def test_adapter_known_failure_boundary(monkeypatch, subtests) -> None:
    visited = []
    for index in range(41):
        case = f"K{index:02d}"
        visited.append(case)
        with subtests.test(msg=case), monkeypatch.context() as scoped:
            fault, subtype, retryable = _known_failure(index)
            counts = _adapter_fault_fixture(scoped, fault)
            response = _call_fault_adapter()
            assert counts == {"constructors": 1, "requests": 1}
            expected_error = (
                "Provider redirects are not supported; configure the final endpoint"
                if index == 21
                else str(fault)
            )
            assert response.error == expected_error
            assert response.raw_text == ""
            assert response.provider_metrics["infra_error_subtype"] == subtype
            assert response.provider_metrics["infra_retryable"] is retryable
    assert visited == [f"K{index:02d}" for index in range(41)]


def test_adapter_cancellation_is_not_wrapped(monkeypatch) -> None:
    with monkeypatch.context() as scoped:
        fault = asyncio.CancelledError("S1-05F-cancellation")
        counts = _adapter_fault_fixture(scoped, fault)
        with pytest.raises(asyncio.CancelledError) as error:
            _call_fault_adapter()
        assert error.value is fault
        assert counts == {"constructors": 1, "requests": 1}


def test_codex_auth_failure_stops_before_sdk(monkeypatch, tmp_path) -> None:
    with monkeypatch.context() as scoped:
        counts = _adapter_fault_fixture(scoped, AssertionError("SDK should not run"))
        scoped.setattr(codex_oauth, "_installation_id", lambda: "S1-05F-installation")
        scoped.setattr(codex_oauth, "_session_id", lambda: "S1-05F-session")
        path = tmp_path / "missing-synthetic-codex.json"
        scoped.setattr(codex_oauth, "codex_auth_path", lambda: path)
        response = _call_fault_adapter(
            model="codex/synthetic-model", base_url="https://chatgpt.com/backend-api/codex"
        )
        assert counts == {"constructors": 0, "requests": 0}
        assert response.raw_text == ""
        assert response.provider_metrics["infra_error_subtype"] == "PROVIDER_AUTH"
        assert response.provider_metrics["infra_retryable"] is False
        assert response.error == "Codex OAuth credential file could not be read"
        assert str(path) not in response.error


def test_unknown_provider_is_typed_capability_failure(monkeypatch) -> None:
    with monkeypatch.context() as scoped:
        counts = _adapter_fault_fixture(scoped, AssertionError("SDK should not run"))
        response = _call_fault_adapter(model="unsupported-synthetic/provider-model")
        assert counts == {"constructors": 0, "requests": 0}
        assert response.raw_text == ""
        assert response.error is not None
        assert response.provider_metrics["infra_error_subtype"] == "PROVIDER_CAPABILITY"
        assert response.provider_metrics["infra_retryable"] is False
