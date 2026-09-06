from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import traceback
from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx
import openai
import pytest

from ori.eval import codex_oauth
from ori.eval.adapter import call_provider_text
from ori.eval.codex_oauth import (
    CodexResponseStreamError,
    chat_request_to_codex_responses_params,
    codex_headers,
    codex_model_name,
    codex_responses_events_to_chat_completion,
)
from ori.eval.provider_contract import ProviderAuthenticationError, ProviderCapabilityError
from tests.support.codex_destinations import CODEX_DESTINATIONS


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
    assert "reasoning" not in params


def test_codex_chat_translation_sets_explicit_reasoning_effort() -> None:
    params = chat_request_to_codex_responses_params(
        {
            "model": "gpt-5.6-sol",
            "messages": [{"role": "user", "content": "question"}],
            "options": {"reasoning_effort": "high"},
        }
    )

    assert params["reasoning"] == {"effort": "high"}


def test_codex_chat_translation_rejects_unknown_reasoning_effort() -> None:
    with pytest.raises(ValueError, match="unsupported Codex reasoning effort"):
        chat_request_to_codex_responses_params(
            {
                "model": "gpt-5.6-sol",
                "messages": [{"role": "user", "content": "question"}],
                "options": {"reasoning_effort": "extreme"},
            }
        )


def test_codex_provider_sends_reasoning_effort_to_responses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    monkeypatch.delenv("CODEX_BASE_URL", raising=False)
    monkeypatch.delenv("CODEX_COMPAT_API_KEY", raising=False)

    class EventStream:
        def __aiter__(self):
            async def events():
                yield SimpleNamespace(type="response.output_text.delta", delta="answer",
                                      item_id="message-1", output_index=0, content_index=0)
                yield SimpleNamespace(
                    type="response.completed",
                    response=SimpleNamespace(
                        id="response-1", status="completed", output=[SimpleNamespace(
                            type="message", id="message-1", status="completed",
                            content=[SimpleNamespace(type="output_text", text="answer")])],
                        usage=SimpleNamespace(
                            input_tokens=10,
                            output_tokens=2,
                            total_tokens=12,
                        )
                    ),
                )

            return events()

    class Responses:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return EventStream()

    class Client:
        def __init__(self, **_kwargs):
            self.responses = Responses()

        async def close(self) -> None:
            return None

    monkeypatch.setattr(openai, "AsyncOpenAI", Client)
    monkeypatch.setenv("CODEX_API_KEY", "test-token")
    response = asyncio.run(
        call_provider_text(
            model="codex/gpt-5.6-sol",
            messages=[{"role": "user", "content": "question"}],
            system="system",
            ollama_options={"reasoning_effort": "high"},
        )
    )

    assert captured["reasoning"] == {"effort": "high"}
    assert response.provider_metrics["reasoning_effort"] == "high"


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
    monkeypatch.delenv("CODEX_BASE_URL", raising=False)
    monkeypatch.delenv("CODEX_COMPAT_API_KEY", raising=False)
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
        SimpleNamespace(type="response.output_text.delta", delta="hello",
                        item_id="message-1", output_index=0, content_index=0),
        SimpleNamespace(
            type="response.output_item.done",
            output_index=1,
            item=SimpleNamespace(
                type="function_call",
                call_id="call-1",
                name="group_info",
                arguments='{"group_name":"DA"}',
            ),
        ),
        SimpleNamespace(type="response.completed", response=SimpleNamespace(
            usage=usage, id="response-1", status="completed", output=[
                SimpleNamespace(type="message", id="message-1", status="completed",
                                content=[SimpleNamespace(type="output_text", text="hello")]),
                SimpleNamespace(type="function_call", call_id="call-1", name="group_info",
                                arguments='{"group_name":"DA"}')
            ])),
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
        output=[SimpleNamespace(type="message", id="message-1", status="completed",
                                content=[SimpleNamespace(type="output_text",
                                    text="MATCH (n:Domain) RETURN n LIMIT 1")])],
    )
    events = [SimpleNamespace(type="response.completed", response=response)]

    data = codex_responses_events_to_chat_completion(events, "gpt-5.5")

    assert data["choices"][0]["message"]["content"] == "MATCH (n:Domain) RETURN n LIMIT 1"


def _codex_acquisition_traps(scoped):
    def forbidden(*args, **kwargs):
        raise AssertionError("S1-05F credential test attempted external activity")

    for variable in (
        "CODEX_API_KEY",
        "OPENAI_API_KEY",
        "CODEX_AUTH_FILE",
        "CODEX_BASE_URL",
        "CODEX_COMPAT_API_KEY",
        "OPENAI_COMPAT_API_KEY",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "OPENAI_BASE_URL",
        "OPENAI_COMPAT_BASE_URL",
    ):
        scoped.delenv(variable, raising=False)
    scoped.setattr(socket, "getaddrinfo", forbidden)
    scoped.setattr(socket, "create_connection", forbidden)
    scoped.setattr(socket.socket, "connect", forbidden)
    scoped.setattr(subprocess, "Popen", forbidden)
    scoped.setattr(openai, "AsyncOpenAI", forbidden)
    scoped.setattr(anthropic, "AsyncAnthropic", forbidden)
    scoped.setattr(httpx, "AsyncClient", forbidden)
    scoped.setattr(codex_oauth, "codex_auth_path", forbidden)


def _codex_rejected_document(index):
    return {
        5: ["S1-05F-document-sentinel"],
        6: "S1-05F-document-sentinel",
        7: None,
        8: {"tokens": ["S1-05F-document-sentinel"]},
        9: {"sentinel": "S1-05F-document-sentinel"},
        10: {"access_token": ""},
        11: {"access_token": 123},
        12: {"tokens": {"access_token": 123}, "access_token": "S1-05F-top-token"},
        13: {
            "tokens": {"access_token": ["S1-05F-nested-token"]},
            "access_token": "S1-05F-top-token",
        },
    }[index]


def test_codex_auth_file_rejection_boundary(monkeypatch, tmp_path, subtests) -> None:
    visited = []
    for index in range(14):
        case = f"A{index}"
        visited.append(case)
        with subtests.test(msg=case), monkeypatch.context() as scoped:
            _codex_acquisition_traps(scoped)
            path = tmp_path / f"{case}-auth.json"
            scoped.setattr(codex_oauth, "codex_auth_path", lambda: path)
            if index == 1:
                path.mkdir()
            elif index == 2:
                path.write_text('{"access_token":"S1-05F-permission-token"}')
                original = Path.read_text

                def denied_read(self, *args, **kwargs):
                    if self == path:
                        raise PermissionError("S1-05F-permission-document")
                    return original(self, *args, **kwargs)

                scoped.setattr(Path, "read_text", denied_read)
            elif index == 3:
                path.write_bytes(bytes([255]))
            elif index == 4:
                path.write_text('{"S1-05F-document-sentinel":')
            elif index >= 5:
                path.write_text(json.dumps(_codex_rejected_document(index)))
            with pytest.raises(ProviderAuthenticationError) as error:
                codex_oauth.codex_access_token()
            expected = (
                "Codex OAuth credential file could not be read"
                if index <= 3
                else "Codex OAuth credential file is invalid"
                if index <= 8
                else "Codex OAuth access token is unavailable"
            )
            assert str(error.value) == expected
            assert error.value.code == "PROVIDER_AUTH"
            assert error.value.retryable is False
            for rendered in (str(error.value), "".join(traceback.format_exception(error.value))):
                for forbidden in (
                    str(path),
                    "S1-05F-document-sentinel",
                    "S1-05F-top-token",
                    "S1-05F-nested-token",
                    "S1-05F-permission-token",
                    "S1-05F-permission-document",
                ):
                    assert forbidden not in rendered
    assert visited == [f"A{index}" for index in range(14)]


def test_codex_auth_file_compatibility(monkeypatch, tmp_path, subtests) -> None:
    visited = []
    for index in range(6):
        case = f"C{index}"
        visited.append(case)
        with subtests.test(msg=case), monkeypatch.context() as scoped:
            _codex_acquisition_traps(scoped)
            documents = (
                ({"tokens": {"access_token": "S1-05F-nested-token"}}, "S1-05F-nested-token"),
                ({"access_token": "S1-05F-top-token"}, "S1-05F-top-token"),
                (
                    {"tokens": {"access_token": ""}, "access_token": "S1-05F-top-token"},
                    "S1-05F-top-token",
                ),
                ({"tokens": None, "access_token": "S1-05F-top-token"}, "S1-05F-top-token"),
                ({"tokens": [], "access_token": "S1-05F-top-token"}, "S1-05F-top-token"),
                (
                    {
                        "tokens": {"access_token": "S1-05F-nested-token"},
                        "access_token": "S1-05F-top-token",
                    },
                    "S1-05F-nested-token",
                ),
            )
            document, expected = documents[index]
            path = tmp_path / f"{case}-auth.json"
            path.write_text(json.dumps(document))
            scoped.setattr(codex_oauth, "codex_auth_path", lambda: path)
            assert codex_oauth.codex_access_token() == expected
    assert visited == [f"C{index}" for index in range(6)]


def test_codex_auth_environment_precedence(monkeypatch, tmp_path, subtests) -> None:
    visited = []
    for index in range(3):
        case = f"E{index}"
        visited.append(case)
        with subtests.test(msg=case), monkeypatch.context() as scoped:
            _codex_acquisition_traps(scoped)
            scoped.setenv("OPENAI_API_KEY", "S1-05F-openai-token")
            if index == 0:
                scoped.setenv("CODEX_API_KEY", "S1-05F-codex-token")
            elif index == 1:
                scoped.setenv("CODEX_API_KEY", "")
            if index != 0:
                path = tmp_path / f"{case}-auth.json"
                path.write_text(json.dumps({"tokens": {"access_token": "S1-05G-file-token"}}))
                scoped.setattr(codex_oauth, "codex_auth_path", lambda: path)
            assert codex_oauth.codex_access_token() == (
                "S1-05F-codex-token" if index == 0 else "S1-05G-file-token"
            )
    assert visited == [f"E{index}" for index in range(3)]


_CODEX_MATRIX_KEYS = (
    ("CODEX_API_KEY", "S1-05G-codex-key"),
    ("OPENAI_API_KEY", "S1-05G-openai-key"),
    ("CODEX_COMPAT_API_KEY", "S1-05G-custom-key"),
    ("OPENAI_COMPAT_API_KEY", "S1-05G-generic-key"),
)


def _codex_matrix_environment(scoped, environment_index):
    _codex_acquisition_traps(scoped)
    selected = ((), (0,), (1,), (2,), (0, 1, 2, 3))[environment_index]
    for index in selected:
        name, token = _CODEX_MATRIX_KEYS[index]
        scoped.setenv(name, token)


def _expected_codex_source(family, environment_index):
    if family == "codex_oauth":
        return "CODEX_API_KEY" if environment_index in (1, 4) else "codex-auth-file"
    if family == "codex_compat":
        return "CODEX_COMPAT_API_KEY" if environment_index in (3, 4) else None
    return None


def test_codex_endpoint_source_binding_matrix(monkeypatch, subtests) -> None:
    visited = []
    for destination_id, url, family, normalized in CODEX_DESTINATIONS:
        for environment_index in range(5):
            case = f"{destination_id}/K{environment_index}"
            visited.append(case)
            with subtests.test(msg=case), monkeypatch.context() as scoped:
                _codex_matrix_environment(scoped, environment_index)
                counts = {"file": 0, "credential": 0}

                def forbidden_file():
                    counts["file"] += 1
                    raise AssertionError("pure binding must not load a file token")

                def forbidden_credential(*args, **kwargs):
                    counts["credential"] += 1
                    raise AssertionError("pure binding must not acquire credentials")

                scoped.setattr(codex_oauth, "_codex_file_token", forbidden_file)
                scoped.setattr(codex_oauth, "resolve_codex_credential", forbidden_credential)
                if family is None:
                    with pytest.raises(ProviderCapabilityError) as error:
                        codex_oauth.codex_endpoint_binding(url)
                    assert str(error.value) == "Codex endpoint configuration is invalid"
                    assert url not in str(error.value)
                else:
                    binding = codex_oauth.codex_endpoint_binding(url)
                    assert binding.base_url == normalized
                    assert binding.endpoint_family == family
                    assert binding.credential_source == _expected_codex_source(
                        family, environment_index
                    )
                    for _, token in _CODEX_MATRIX_KEYS:
                        assert token not in repr(binding)
                assert counts == {"file": 0, "credential": 0}
    assert visited == [f"{case}/K{index}" for case, *_ in CODEX_DESTINATIONS for index in range(5)]
    assert len(visited) == 145


def test_codex_credential_acquisition_matrix(monkeypatch, tmp_path, subtests) -> None:
    visited = []
    for destination_id, url, family, normalized in CODEX_DESTINATIONS:
        for environment_index in range(5):
            case = f"{destination_id}/K{environment_index}"
            visited.append(case)
            with subtests.test(msg=case), monkeypatch.context() as scoped:
                _codex_matrix_environment(scoped, environment_index)
                counts = {"binding": 0, "file_token": 0, "file_read": 0}
                expected_source = _expected_codex_source(family, environment_index)
                original_binding = codex_oauth.codex_endpoint_binding
                original_file = codex_oauth._codex_file_token
                original_read = Path.read_text
                path = tmp_path / f"{destination_id}-K{environment_index}-auth.json"
                if expected_source == "codex-auth-file":
                    path.write_text(json.dumps({"tokens": {"access_token": "S1-05G-file-token"}}))
                    scoped.setattr(codex_oauth, "codex_auth_path", lambda: path)

                def observed_binding(*args, **kwargs):
                    counts["binding"] += 1
                    return original_binding(*args, **kwargs)

                def observed_file():
                    counts["file_token"] += 1
                    assert expected_source == "codex-auth-file", "unexpected file-token acquisition"
                    return original_file()

                def observed_read(self, *args, **kwargs):
                    if self == path:
                        counts["file_read"] += 1
                        assert expected_source == "codex-auth-file"
                    return original_read(self, *args, **kwargs)

                scoped.setattr(codex_oauth, "codex_endpoint_binding", observed_binding)
                scoped.setattr(codex_oauth, "_codex_file_token", observed_file)
                scoped.setattr(Path, "read_text", observed_read)
                if family is None:
                    with pytest.raises(ProviderCapabilityError) as error:
                        codex_oauth.resolve_codex_credential(url)
                    assert str(error.value) == "Codex endpoint configuration is invalid"
                    assert url not in str(error.value)
                elif expected_source is None:
                    with pytest.raises(ProviderAuthenticationError) as error:
                        codex_oauth.resolve_codex_credential(url)
                    assert str(error.value) == "Custom Codex endpoint requires CODEX_COMPAT_API_KEY"
                    assert error.value.retryable is False
                else:
                    credential = codex_oauth.resolve_codex_credential(url)
                    expected_token = {
                        "codex-auth-file": "S1-05G-file-token",
                        "CODEX_API_KEY": "S1-05G-codex-key",
                        "CODEX_COMPAT_API_KEY": "S1-05G-custom-key",
                    }[expected_source]
                    assert credential.token == expected_token
                    assert credential.binding.base_url == normalized
                    assert credential.binding.endpoint_family == family
                    assert credential.binding.credential_source == expected_source
                    for token in ("S1-05G-file-token", *[value for _, value in _CODEX_MATRIX_KEYS]):
                        assert token not in repr(credential)
                expected_files = int(expected_source == "codex-auth-file")
                assert counts == {
                    "binding": 1,
                    "file_token": expected_files,
                    "file_read": expected_files,
                }
    assert visited == [f"{case}/K{index}" for case, *_ in CODEX_DESTINATIONS for index in range(5)]
    assert len(visited) == 145
