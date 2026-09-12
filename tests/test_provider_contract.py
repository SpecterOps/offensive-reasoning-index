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
    normalize_anthropic_message,
    normalize_chat_completion,
    resolve_api_surface,
    validate_release1_api_surface,
)


def test_anthropic_blocks_usage_and_private_continuation():
    blocks = [
        {"type": "thinking", "thinking": "reason", "signature": "opaque-signature"},
        {"type": "redacted_thinking", "data": "opaque-data"},
        {"type": "text", "text": "one"}, {"type": "text", "text": "two"},
    ]
    message = {"content": blocks, "stop_reason": "end_turn", "usage": {
        "input_tokens": 3, "output_tokens": 2, "cache_read_input_tokens": 7,
        "cache_creation_input_tokens": 5,
    }}
    turn = normalize_anthropic_message(message)
    assert turn.text == "onetwo" and turn.reasoning == "reason"
    assert turn.api_surface is ProviderApiSurface.MESSAGES
    assert turn.usage.total_tokens == 17 and turn.usage.input_tokens == 3
    assert turn.usage.cache_read_input_tokens == 7 and turn.usage.usage_complete
    assert turn.native_content_blocks == tuple(blocks)
    blocks[0]["signature"] = "changed"
    assert turn.native_content_blocks[0]["signature"] == "opaque-signature"
    assert resolve_api_surface("anthropic") is ProviderApiSurface.MESSAGES
    with pytest.raises(ProviderCapabilityError):
        validate_release1_api_surface("openai", "messages")


def test_anthropic_output_config_preserves_constraints_and_rejects_invalid(subtests):
    from copy import deepcopy

    from ori.eval.provider_contract import anthropic_output_config

    schema = {"type": "object", "properties": {"count": {"type": "integer", "minimum": 0}},
              "required": ["count"], "additionalProperties": False}
    descriptor = {"name": "local-name", "strict": True, "schema": schema}
    result = anthropic_output_config(descriptor)
    assert result == {"format": {"type": "json_schema", "schema": schema}}
    result["format"]["schema"]["properties"]["count"]["minimum"] = 2
    assert schema["properties"]["count"]["minimum"] == 0
    assert anthropic_output_config(None) is None
    for case in ("strict", "extra", "dialect", "nonfinite", "invalid"):
        with subtests.test(case=case):
            value = deepcopy(descriptor)
            if case == "strict":
                value["strict"] = False
            elif case == "extra":
                value["unsupported"] = True
            elif case == "dialect":
                value["schema"]["$schema"] = "https://example.invalid/schema"
            elif case == "nonfinite":
                value["schema"]["minimum"] = float("nan")
            else:
                value["schema"]["type"] = "not-a-type"
            with pytest.raises(ProviderCapabilityError):
                anthropic_output_config(value)


@pytest.mark.parametrize("reason,status", [
    ("end_turn", ProviderTurnStatus.COMPLETED),
    ("stop_sequence", ProviderTurnStatus.COMPLETED),
    ("refusal", ProviderTurnStatus.REFUSED),
    ("max_tokens", ProviderTurnStatus.TRUNCATED),
    ("model_context_window_exceeded", ProviderTurnStatus.TRUNCATED),
    ("pause_turn", ProviderTurnStatus.CONTINUATION_REQUIRED),
])
def test_anthropic_terminal_states_preserve_usage(reason, status):
    from ori.eval.adapter import _direct_text_projection, _provider_turn_metrics

    turn = normalize_anthropic_message({
        "content": [{"type": "text", "text": "answer"}], "stop_reason": reason,
        "usage": {"input_tokens": 2, "output_tokens": 1},
    })
    assert turn.status is status and turn.usage.output_tokens == 1
    assert turn.usage.cache_read_input_tokens is None and turn.usage.total_tokens is None
    assert not turn.usage.usage_complete
    metrics = _provider_turn_metrics(turn)
    projected = _direct_text_projection(turn, metrics)
    assert projected == ("answer" if status is ProviderTurnStatus.COMPLETED else "")
    if reason == "pause_turn":
        assert metrics["infra_error_subtype"] == "PROVIDER_CAPABILITY"
        assert "model_output_error" not in metrics and metrics["infra_retryable"] is False


def test_anthropic_tool_calls_and_rejections(subtests):
    from copy import deepcopy

    from ori.eval.adapter import _direct_text_projection, _provider_turn_metrics

    base = {"stop_reason": "tool_use", "content": [
        {"type": "text", "text": "not a final answer"},
        {"type": "tool_use", "id": "call-1", "name": "native_query", "input": {"q": "x"}},
    ], "usage": {"input_tokens": 1, "output_tokens": 2}}
    turn = normalize_anthropic_message(base)
    assert turn.tool_calls[0].parsed_arguments == {"q": "x"}
    assert _direct_text_projection(turn, _provider_turn_metrics(turn)) == ""
    for case in ("duplicate", "input", "unknown_block", "unknown_stop", "no_calls", "bool_usage",
                 "paused_tool"):
        with subtests.test(case=case):
            message = deepcopy(base)
            if case == "duplicate":
                message["content"].append(deepcopy(message["content"][-1]))
            elif case == "input":
                message["content"][-1]["input"] = []
            elif case == "unknown_block":
                message["content"][-1]["type"] = "server_tool_use"
            elif case == "unknown_stop":
                message["stop_reason"] = "new_reason"
            elif case == "no_calls":
                message["content"].pop()
            elif case == "paused_tool":
                message["stop_reason"] = "pause_turn"
            else:
                message["usage"]["input_tokens"] = True
            with pytest.raises((ProviderProtocolError, ProviderCapabilityError)):
                normalize_anthropic_message(message)


@pytest.mark.parametrize("stop_reason", ["end_turn", "pause_turn"])
def test_anthropic_direct_adapter_consumes_full_message_and_closes_attempt(
    monkeypatch, stop_reason,
):
    import asyncio
    from unittest.mock import AsyncMock

    from ori.eval import adapter

    binding = SimpleNamespace(model_slug="fixture", base_url="https://fixture.invalid")
    response = {"content": [
        {"type": "thinking", "thinking": "reason", "signature": "sig"},
        {"type": "text", "text": '{"answer":'}, {"type": "text", "text": "1}"},
    ], "stop_reason": stop_reason, "usage": {
        "input_tokens": 3, "output_tokens": 2, "cache_read_input_tokens": 7,
        "cache_creation_input_tokens": 5,
    }}
    create = AsyncMock(return_value=response)
    attempt = SimpleNamespace(client=SimpleNamespace(
        default_headers={}, _validate_headers=lambda *args: None,
        messages=SimpleNamespace(create=create),
    ), aclose=AsyncMock())
    monkeypatch.setattr(adapter, "prepare_anthropic_binding", lambda *args: binding)
    monkeypatch.setattr(adapter, "materialize_anthropic_client", AsyncMock(return_value=attempt))
    monkeypatch.setattr(adapter, "anthropic_binding_identity", lambda value: {
        "endpoint_family": "anthropic_compat", "credential_source": "fixture",
    })
    result = asyncio.run(adapter.call_provider_text(
        model="anthropic/fixture", messages=[{"role": "user", "content": "question"}],
        system="system",
        structured_output_schema={"name": "answer", "strict": True,
                                  "schema": {"type": "object", "additionalProperties": False}},
    ))
    assert create.call_args.kwargs["output_config"] == {"format": {
        "type": "json_schema", "schema": {"type": "object", "additionalProperties": False},
    }}
    assert result.thinking == "reason"
    if stop_reason == "end_turn":
        assert result.raw_text == '{"answer":1}' and result.error is None
    else:
        assert result.raw_text == "" and "PROVIDER_CAPABILITY" in result.error
        assert not result.provider_metrics["infra_retryable"]
    assert result.tokens_input == 3 and result.tokens_output == 2
    assert result.provider_metrics["cache_read_input_tokens"] == 7
    assert result.provider_metrics["resolved_api_surface"] == "messages"
    assert "native_content_blocks" not in result.provider_metrics
    attempt.aclose.assert_awaited_once()
    materializations = adapter.materialize_anthropic_client.await_count
    invalid = asyncio.run(adapter.call_provider_text(
        model="anthropic/fixture", messages=[], system="system",
        structured_output_schema={"name": "answer", "strict": False, "schema": {}},
    ))
    assert invalid.error is not None
    assert adapter.materialize_anthropic_client.await_count == materializations


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
