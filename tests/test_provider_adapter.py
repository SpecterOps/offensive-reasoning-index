"""Direct adapter regressions for normalized provider turns."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest

from ori.eval.adapter import _provider_exception_metrics, call_provider_text


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
            model="openai-compat/provider/model",
            messages=[{"role": "user", "content": "test"}],
            system="system",
            base_url=base_url,
            api_surface=api_surface,
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
