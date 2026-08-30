"""Focused contract tests for provider-neutral Chat Completions turns."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ori.eval.provider_contract import (
    ProviderApiSurface,
    ProviderCapabilityError,
    ProviderProtocolError,
    ProviderReasoningOutput,
    ProviderRefusalOutput,
    ProviderRequest,
    ProviderTextOutput,
    ProviderTurnStatus,
    ToolArgumentParseStatus,
    chat_completions_payload,
    normalize_chat_completion,
    resolve_api_surface,
    validate_release1_api_surface,
)


def test_chat_completions_payload_projects_shared_request_contract() -> None:
    request = ProviderRequest(
        messages=({"role": "user", "content": "question"},),
        api_surface=ProviderApiSurface.CHAT_COMPLETIONS,
        tools=({"type": "function", "function": {"name": "lookup"}},),
        tool_choice="auto",
        output_limit=321,
        reasoning={"reasoning_effort": "low"},
        structured_output_schema={"name": "answer", "schema": {"type": "object"}},
    )

    assert chat_completions_payload(request, model="model-id") == {
        "model": "model-id",
        "messages": [{"role": "user", "content": "question"}],
        "tools": [{"type": "function", "function": {"name": "lookup"}}],
        "tool_choice": "auto",
        "max_tokens": 321,
        "reasoning_effort": "low",
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "answer", "schema": {"type": "object"}},
        },
    }


def _response(
    *,
    content: str | None = "answer",
    finish_reason: str = "stop",
    message_fields: dict[str, object] | None = None,
    include_usage: bool = True,
) -> dict[str, object]:
    message: dict[str, object] = {"role": "assistant", "content": content}
    message.update(message_fields or {})
    response: dict[str, object] = {
        "id": "chatcmpl-test",
        "model": "provider/model",
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
    }
    if include_usage:
        response["usage"] = {
            "prompt_tokens": 11,
            "completion_tokens": 7,
            "total_tokens": 18,
        }
    return response


def test_api_surface_resolution_preserves_existing_chat_and_codex_behavior() -> None:
    for provider in ("openai", "openai-compat", "ollama", "gemini"):
        assert resolve_api_surface(provider) is ProviderApiSurface.CHAT_COMPLETIONS
    assert resolve_api_surface("codex") is ProviderApiSurface.RESPONSES
    assert (
        resolve_api_surface("openai-compat", "responses") is ProviderApiSurface.RESPONSES
    )

    validate_release1_api_surface("codex", "responses")
    with pytest.raises(ProviderCapabilityError, match="does not support"):
        validate_release1_api_surface("openai-compat", "responses")


def test_normalize_chat_completion_retains_text_identity_and_usage() -> None:
    turn = normalize_chat_completion(
        _response(),
        provider="openrouter",
        endpoint="https://openrouter.ai/api/v1/chat/completions",
    )

    assert turn.text == "answer"
    assert turn.refusal == ""
    assert turn.reasoning == ""
    assert turn.tool_calls == ()
    assert turn.output_items == (ProviderTextOutput(text="answer"),)
    assert turn.status is ProviderTurnStatus.COMPLETED
    assert turn.finish_reason == "stop"
    assert turn.usage.input_tokens == 11
    assert turn.usage.output_tokens == 7
    assert turn.usage.total_tokens == 18
    assert turn.usage.usage_reported is True
    assert turn.usage.usage_complete is True
    assert turn.response_id == "chatcmpl-test"
    assert turn.model == "provider/model"
    assert turn.provider == "openrouter"


def test_nullable_content_with_valid_tool_call_is_lossless() -> None:
    turn = normalize_chat_completion(
        _response(
            content=None,
            finish_reason="tool_calls",
            message_fields={
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "cypher_query.run",
                            "arguments": '{"query":"MATCH (n) RETURN n LIMIT 1"}',
                        },
                    }
                ]
            },
        ),
        provider="nous",
    )

    assert turn.text == ""
    assert turn.status is ProviderTurnStatus.TOOL_CALLS
    assert len(turn.tool_calls) == 1
    tool_call = turn.tool_calls[0]
    assert tool_call.id == "call-1"
    assert tool_call.name == "cypher_query.run"
    assert tool_call.raw_arguments == '{"query":"MATCH (n) RETURN n LIMIT 1"}'
    assert tool_call.parsed_arguments == {"query": "MATCH (n) RETURN n LIMIT 1"}
    assert tool_call.argument_parse_status is ToolArgumentParseStatus.VALID
    assert tool_call.argument_parse_error is None
    assert turn.output_items == (tool_call,)


def test_refusal_only_response_remains_distinct_from_empty_output() -> None:
    turn = normalize_chat_completion(
        _response(content=None, message_fields={"refusal": "I cannot help with that."}),
        provider="openrouter",
    )

    assert turn.text == ""
    assert turn.refusal == "I cannot help with that."
    assert turn.status is ProviderTurnStatus.REFUSED
    assert turn.output_items == (
        ProviderRefusalOutput(refusal="I cannot help with that."),
    )


def test_reasoning_only_response_remains_distinct_from_empty_output() -> None:
    turn = normalize_chat_completion(
        _response(content=None, message_fields={"reasoning_content": "working"}),
        provider="nous",
    )

    assert turn.text == ""
    assert turn.reasoning == "working"
    assert turn.status is ProviderTurnStatus.REASONING_ONLY
    assert turn.output_items == (ProviderReasoningOutput(reasoning="working"),)


def test_genuinely_empty_response_has_explicit_empty_status() -> None:
    turn = normalize_chat_completion(
        _response(content=None),
        provider="nous",
    )

    assert turn.text == ""
    assert turn.output_items == ()
    assert turn.status is ProviderTurnStatus.EMPTY


def test_missing_usage_stays_unknown_instead_of_becoming_zero() -> None:
    turn = normalize_chat_completion(
        _response(include_usage=False),
        provider="openrouter",
    )

    assert turn.usage.input_tokens is None
    assert turn.usage.output_tokens is None
    assert turn.usage.total_tokens is None
    assert turn.usage.usage_reported is False
    assert turn.usage.usage_complete is False


@pytest.mark.parametrize(
    ("finish_reason", "expected_status"),
    [
        ("stop", ProviderTurnStatus.COMPLETED),
        ("length", ProviderTurnStatus.TRUNCATED),
        ("content_filter", ProviderTurnStatus.CONTENT_FILTERED),
    ],
)
def test_finish_reason_controls_typed_terminal_status(
    finish_reason: str,
    expected_status: ProviderTurnStatus,
) -> None:
    turn = normalize_chat_completion(
        _response(finish_reason=finish_reason),
        provider="openrouter",
    )
    assert turn.finish_reason == finish_reason
    assert turn.status is expected_status


def test_malformed_tool_arguments_are_retained_without_becoming_empty_object() -> None:
    raw_arguments = '{"query":'
    turn = normalize_chat_completion(
        _response(
            content=None,
            finish_reason="tool_calls",
            message_fields={
                "tool_calls": [
                    {
                        "id": "call-bad",
                        "function": {
                            "name": "cypher_query.run",
                            "arguments": raw_arguments,
                        },
                    }
                ]
            },
        ),
        provider="nous",
    )

    tool_call = turn.tool_calls[0]
    assert tool_call.raw_arguments == raw_arguments
    assert tool_call.parsed_arguments is None
    assert tool_call.argument_parse_status is ToolArgumentParseStatus.MALFORMED
    assert tool_call.argument_parse_error


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"choices": None},
        {"choices": []},
        {"choices": [{}]},
    ],
)
def test_missing_choices_or_message_is_nonretryable_protocol_error(
    response: dict[str, object],
) -> None:
    with pytest.raises(ProviderProtocolError) as exc_info:
        normalize_chat_completion(response, provider="openrouter")

    assert exc_info.value.retryable is False
    assert exc_info.value.code == "PROVIDER_PROTOCOL"


def test_normalizer_accepts_openai_sdk_style_objects() -> None:
    response = SimpleNamespace(
        id="chatcmpl-sdk",
        model="sdk-model",
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(content="sdk answer", refusal=None, tool_calls=None),
            )
        ],
        usage=SimpleNamespace(prompt_tokens=2, completion_tokens=3, total_tokens=5),
    )
    turn = normalize_chat_completion(response, provider="openai-compat")

    assert turn.text == "sdk answer"
    assert turn.usage.input_tokens == 2
    assert turn.usage.output_tokens == 3
    assert turn.usage.total_tokens == 5
