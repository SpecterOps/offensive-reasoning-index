from __future__ import annotations

from types import SimpleNamespace

import pytest

from ori.eval.codex_oauth import (
    CodexResponseStreamError,
    chat_request_to_codex_responses_params,
    codex_headers,
    codex_model_name,
    codex_responses_events_to_chat_completion,
)


def test_codex_model_name_strips_provider_and_inline_url() -> None:
    assert codex_model_name("codex/gpt-5.5-codex@https://example.test/codex") == "gpt-5.5-codex"


def test_codex_chat_translation_preserves_tool_conversation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODEX_ADAPTER_INSTALLATION_ID", "install-1")
    body = {
        "model": "gpt-5.5-codex",
        "messages": [
            {"role": "system", "content": "system rules"},
            {"role": "user", "content": "question"},
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "group_info", "arguments": '{"group_name":"DA"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call-1", "content": "tool output"},
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "group_info",
                    "description": "inspect group",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        "tool_choice": "auto",
    }

    params = chat_request_to_codex_responses_params(body)

    assert params["model"] == "gpt-5.5-codex"
    assert params["instructions"] == "system rules"
    assert params["input"] == [
        {"role": "user", "content": "question"},
        {
            "type": "function_call",
            "call_id": "call-1",
            "name": "group_info",
            "arguments": '{"group_name":"DA"}',
        },
        {"type": "function_call_output", "call_id": "call-1", "output": "tool output"},
    ]
    assert params["tools"] == [
        {
            "type": "function",
            "name": "group_info",
            "description": "inspect group",
            "parameters": {"type": "object", "properties": {}},
        }
    ]
    assert params["extra_body"]["client_metadata"]["x-codex-installation-id"] == "install-1"


def test_codex_chat_translation_normalizes_null_tool_parameters() -> None:
    params = chat_request_to_codex_responses_params(
        {
            "model": "gpt-5.5-codex",
            "messages": [{"role": "user", "content": "question"}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "domain_info",
                        "description": None,
                        "parameters": None,
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "group_info",
                        "description": "inspect group",
                        "parameters": {"type": "object", "format": None, "properties": {}},
                    },
                },
            ],
        }
    )

    assert params["tools"][0]["parameters"] == {"type": "object", "properties": {}}
    assert params["tools"][1]["parameters"] == {"type": "object", "properties": {}}


def test_codex_chat_translation_omits_unsupported_optional_params_by_default() -> None:
    params = chat_request_to_codex_responses_params(
        {
            "model": "gpt-5.5-codex",
            "messages": [{"role": "user", "content": "question"}],
            "max_tokens": 123,
            "temperature": 0,
        }
    )

    assert "max_output_tokens" not in params
    assert "temperature" not in params


def test_codex_chat_translation_can_opt_into_optional_params(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODEX_SEND_MAX_OUTPUT_TOKENS", "1")
    monkeypatch.setenv("CODEX_SEND_TEMPERATURE", "1")
    params = chat_request_to_codex_responses_params(
        {
            "model": "gpt-5.5-codex",
            "messages": [{"role": "user", "content": "question"}],
            "max_tokens": 123,
            "temperature": 0,
        }
    )

    assert params["max_output_tokens"] == 123
    assert params["temperature"] == 0


def test_codex_headers_load_oauth_token_from_codex_auth_file(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth = tmp_path / "auth.json"
    auth.write_text('{"tokens":{"access_token":"token-123"}}')
    monkeypatch.delenv("CODEX_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("CODEX_AUTH_FILE", str(auth))
    headers = codex_headers(thread_id="thread-1")
    assert headers["Authorization"] == "Bearer token-123"
    assert headers["originator"] == "codex_cli_rs"
    assert headers["thread-id"] == "thread-1"


def test_codex_responses_events_collect_chat_completion() -> None:
    usage = SimpleNamespace(input_tokens=10, output_tokens=3, total_tokens=13)
    events = [
        SimpleNamespace(type="response.output_text.delta", delta="hello"),
        SimpleNamespace(
            type="response.output_item.done",
            item=SimpleNamespace(
                type="function_call",
                call_id="call-1",
                name="group_info",
                arguments='{"group_name":"DA"}',
            ),
        ),
        SimpleNamespace(type="response.completed", response=SimpleNamespace(usage=usage)),
    ]
    data = codex_responses_events_to_chat_completion(events, "gpt-5.5-codex")
    choice = data["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    assert choice["message"]["content"] == "hello"
    assert choice["message"]["tool_calls"][0]["function"]["name"] == "group_info"
    assert data["usage"] == {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13}


def test_codex_responses_events_raise_on_stream_error() -> None:
    events = [
        SimpleNamespace(
            type="error",
            code="server_error",
            message="The response could not be generated.",
        )
    ]

    with pytest.raises(
        CodexResponseStreamError,
        match="stream error: server_error: The response could not be generated",
    ):
        codex_responses_events_to_chat_completion(events, "gpt-5.5")


def test_codex_responses_events_raise_on_failed_response() -> None:
    response = SimpleNamespace(
        error=SimpleNamespace(code="server_error", message="The model failed."),
    )
    events = [SimpleNamespace(type="response.failed", response=response)]

    with pytest.raises(
        CodexResponseStreamError,
        match="response failed: server_error: The model failed",
    ):
        codex_responses_events_to_chat_completion(events, "gpt-5.5")


def test_codex_responses_events_raise_on_incomplete_response() -> None:
    response = SimpleNamespace(
        incomplete_details=SimpleNamespace(reason="max_output_tokens"),
    )
    events = [SimpleNamespace(type="response.incomplete", response=response)]

    with pytest.raises(
        CodexResponseStreamError,
        match="response incomplete: max_output_tokens",
    ):
        codex_responses_events_to_chat_completion(events, "gpt-5.5")


def test_codex_responses_events_raise_without_terminal_event() -> None:
    events = [SimpleNamespace(type="response.output_text.delta", delta="partial")]

    with pytest.raises(
        CodexResponseStreamError,
        match=r"ended without a response\.completed event",
    ):
        codex_responses_events_to_chat_completion(events, "gpt-5.5")


def test_codex_responses_events_raise_on_empty_completed_response() -> None:
    events = [
        SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(usage=None, output=[], output_text=""),
        )
    ]

    with pytest.raises(
        CodexResponseStreamError,
        match="completed without text or tool calls",
    ):
        codex_responses_events_to_chat_completion(events, "gpt-5.5")


def test_codex_responses_events_fall_back_to_completed_response_output() -> None:
    response = SimpleNamespace(
        usage=None,
        output_text="MATCH (n:Domain) RETURN n LIMIT 1",
        output=[],
    )
    events = [SimpleNamespace(type="response.completed", response=response)]

    data = codex_responses_events_to_chat_completion(events, "gpt-5.5")

    assert data["choices"][0]["message"]["content"] == "MATCH (n:Domain) RETURN n LIMIT 1"
