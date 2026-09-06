"""Codex OAuth support for ORI model calls.

This module is adapted from Adam Chester's SpecterOps/CodexAdapter work, which
maps Chat Completions-shaped requests onto the Codex Responses API surface while
using Codex CLI OAuth credentials.
"""

from __future__ import annotations

import json
import os
import platform
import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .provider_contract import (
    ProviderAuthenticationError,
    ProviderCapabilityError,
    ProviderProtocolError,
)

DEFAULT_CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
DEFAULT_CODEX_ORIGINATOR = "codex_cli_rs"
DEFAULT_CODEX_VERSION = "0.133.0"
DEFAULT_CODEX_MODEL = "gpt-5.5-codex"
CODEX_REASONING_EFFORTS = frozenset(
    {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
)
_STREAM_PROTOCOL_ERROR = "Codex successful stream is inconsistent or malformed"


class CodexResponseStreamError(RuntimeError):
    """Raised when a Codex Responses stream does not complete successfully."""


def codex_auth_path() -> Path:
    default_path = Path.home() / ".codex" / "auth.json"
    return Path(os.environ.get("CODEX_AUTH_FILE", default_path)).expanduser()


def _codex_file_token() -> str:
    path = codex_auth_path()
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise ProviderAuthenticationError(
            "Codex OAuth credential file could not be read"
        ) from None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise ProviderAuthenticationError("Codex OAuth credential file is invalid") from None
    if not isinstance(data, dict):
        raise ProviderAuthenticationError("Codex OAuth credential file is invalid") from None
    tokens = data.get("tokens") or {}
    if not isinstance(tokens, dict):
        raise ProviderAuthenticationError("Codex OAuth credential file is invalid") from None
    token = tokens.get("access_token") or data.get("access_token")
    if not isinstance(token, str) or not token:
        raise ProviderAuthenticationError("Codex OAuth access token is unavailable") from None
    return token


def codex_base_url(base_url: str | None = None) -> str:
    url = (base_url or os.environ.get("CODEX_BASE_URL") or DEFAULT_CODEX_BASE_URL).rstrip("/")
    if not url:
        raise ProviderCapabilityError("Codex endpoint configuration is invalid")
    return url


@dataclass(frozen=True, slots=True)
class CodexEndpointBinding:
    base_url: str
    endpoint_family: str
    credential_source: str | None


def codex_endpoint_binding(base_url: str | None = None) -> CodexEndpointBinding:
    """Admit a destination and select source metadata without loading credentials."""
    url = codex_base_url(base_url)
    try:
        if any(ord(char) <= 32 or ord(char) == 127 or char == "\\" for char in url):
            raise ValueError
        parsed = urlsplit(url)
        port = parsed.port
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or "?" in url
            or "#" in url
            or parsed.netloc.endswith(":")
        ):
            raise ValueError
        official = parsed.hostname.casefold() == "chatgpt.com"
        if official and (
            parsed.scheme != "https"
            or port not in {None, 443}
            or parsed.path != "/backend-api/codex"
        ):
            raise ValueError
    except ValueError:
        raise ProviderCapabilityError("Codex endpoint configuration is invalid") from None
    if official:
        source = "CODEX_API_KEY" if os.environ.get("CODEX_API_KEY") else "codex-auth-file"
        return CodexEndpointBinding(url, "codex_oauth", source)
    source = "CODEX_COMPAT_API_KEY" if os.environ.get("CODEX_COMPAT_API_KEY") else None
    return CodexEndpointBinding(url, "codex_compat", source)


@dataclass(frozen=True, slots=True)
class CodexCredential:
    binding: CodexEndpointBinding
    token: str = field(repr=False)

    def headers(self, thread_id: str | None = None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "User-Agent": codex_user_agent(),
            "originator": _originator(),
            "session-id": _session_id(),
            "x-codex-installation-id": _installation_id(),
        }
        if thread_id:
            headers["thread-id"] = thread_id
        return headers


def resolve_codex_credential(base_url: str | None = None) -> CodexCredential:
    binding = codex_endpoint_binding(base_url)
    if binding.credential_source is None:
        raise ProviderAuthenticationError(
            "Custom Codex endpoint requires CODEX_COMPAT_API_KEY"
        )
    token = (
        _codex_file_token()
        if binding.credential_source == "codex-auth-file"
        else os.environ[binding.credential_source]
    )
    return CodexCredential(binding, token)


def codex_access_token(*, base_url: str | None = None) -> str:
    return resolve_codex_credential(base_url).token


def codex_model_name(model: str) -> str:
    if model.startswith("codex/"):
        value = model.split("/", 1)[1]
    else:
        value = model
    if "@" in value:
        value = value.split("@", 1)[0]
    return value or os.environ.get("CODEX_DEFAULT_MODEL", DEFAULT_CODEX_MODEL)


def codex_request_base_url(model: str, base_url: str | None = None) -> str:
    if not base_url and "@" in model:
        base_url = model.rsplit("@", 1)[1]
    return codex_base_url(base_url)


def _codex_version() -> str:
    return os.environ.get("CODEX_ADAPTER_VERSION", DEFAULT_CODEX_VERSION)


def _originator() -> str:
    return os.environ.get("CODEX_ADAPTER_ORIGINATOR", DEFAULT_CODEX_ORIGINATOR)


@lru_cache(maxsize=1)
def _installation_id() -> str:
    return os.environ.get("CODEX_ADAPTER_INSTALLATION_ID") or str(uuid.uuid4())


@lru_cache(maxsize=1)
def _session_id() -> str:
    return str(uuid.uuid4())


def codex_new_thread_id() -> str:
    return str(uuid.uuid4())


def codex_user_agent() -> str:
    os_type = platform.system() or "unknown"
    os_version = platform.release() or "unknown"
    arch = platform.machine() or "unknown"
    runtime = f"python/{platform.python_version()}"
    return f"{_originator()}/{_codex_version()} ({os_type} {os_version}; {arch}) {runtime}"


def codex_headers(
    *, thread_id: str | None = None, base_url: str | None = None
) -> dict[str, str]:
    return resolve_codex_credential(base_url).headers(thread_id)


def chat_request_to_codex_responses_params(body: dict[str, Any]) -> dict[str, Any]:
    """Translate an OpenAI Chat Completions body to Responses API params.

    Based on Adam Chester's CodexAdapter translator, narrowed to ORI's
    non-streaming direct/MCP calls.
    """
    messages: list[dict[str, Any]] = body.get("messages", []) or []
    instructions_parts: list[str] = []
    input_items: list[dict[str, Any]] = []

    for msg in messages:
        role = msg.get("role")
        if role in {"system", "developer"}:
            content = _stringify_content(msg.get("content"))
            if content:
                instructions_parts.append(content)
            continue
        if role == "user":
            input_items.append({"role": "user", "content": _stringify_content(msg.get("content"))})
            continue
        if role == "assistant":
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function", {})
                input_items.append(
                    {
                        "type": "function_call",
                        "call_id": tc.get("id") or _new_id("call"),
                        "name": fn.get("name", ""),
                        "arguments": fn.get("arguments", "") or "{}",
                    }
                )
            text = _stringify_content(msg.get("content"))
            if text:
                input_items.append({"role": "assistant", "content": text})
            continue
        if role == "tool":
            input_items.append(
                {
                    "type": "function_call_output",
                    "call_id": msg.get("tool_call_id", ""),
                    "output": _stringify_content(msg.get("content")),
                }
            )

    thread_id = codex_new_thread_id()
    params: dict[str, Any] = {
        "model": body.get("model") or DEFAULT_CODEX_MODEL,
        "input": input_items,
        "store": False,
        "tool_choice": body.get("tool_choice") or "auto",
        "parallel_tool_calls": True,
        "prompt_cache_key": thread_id,
        "extra_body": {"client_metadata": {"x-codex-installation-id": _installation_id()}},
    }
    if instructions_parts:
        params["instructions"] = "\n\n".join(instructions_parts)
    if body.get("tools"):
        params["tools"] = [_translate_tool(tool) for tool in body["tools"]]
    options = body.get("options")
    reasoning_effort = body.get("reasoning_effort")
    if reasoning_effort is None and isinstance(options, dict):
        reasoning_effort = options.get("reasoning_effort")
    if reasoning_effort is not None:
        if reasoning_effort not in CODEX_REASONING_EFFORTS:
            raise ValueError(f"unsupported Codex reasoning effort: {reasoning_effort!r}")
        params["reasoning"] = {"effort": reasoning_effort}
    # The ChatGPT Codex backend used by Codex OAuth currently rejects some
    # optional Responses parameters that ORI's OpenAI-compatible callers may
    # supply by default. Omit them unless explicitly opted in so Codex model
    # probes and eval runs can use the backend's native defaults.
    if os.environ.get("CODEX_SEND_MAX_OUTPUT_TOKENS") == "1":
        max_tokens = body.get("max_tokens")
        if max_tokens is not None:
            params["max_output_tokens"] = max_tokens
    if os.environ.get("CODEX_SEND_TEMPERATURE") == "1":
        temperature = body.get("temperature")
        if temperature is not None:
            params["temperature"] = temperature
    return params


def codex_responses_events_to_chat_completion(events: Iterable[Any], model: str) -> dict[str, Any]:
    completion_id = _new_id("chatcmpl")
    created = int(time.time())
    text_events: list[Any] = []
    call_events: list[Any] = []
    completed_response: Any | None = None
    completed_seen = False
    post_terminal_event = False

    for event in events:
        etype = getattr(event, "type", None)
        if etype == "error":
            raise CodexResponseStreamError(_stream_error_detail(event))
        if etype in ("response.failed", "response.incomplete"):
            response = getattr(event, "response", None)
            raise CodexResponseStreamError(_terminal_response_error_detail(etype, response))
        if completed_seen:
            # Defer rejection so later explicit failures retain legacy precedence.
            post_terminal_event = True
        elif etype == "response.completed":
            completed_seen = True
            completed_response = getattr(event, "response", None)
        elif etype == "response.output_text.delta":
            text_events.append(event)
        elif etype == "response.output_item.done":
            if getattr(getattr(event, "item", None), "type", None) == "function_call":
                call_events.append(event)

    if completed_response is None:
        raise CodexResponseStreamError(
            "Codex Responses API stream ended without a response.completed event"
        )
    if post_terminal_event:
        raise ProviderProtocolError(_STREAM_PROTOCOL_ERROR)

    content, calls_by_index, text_slots = _codex_completed_output(completed_response)
    observed_text: dict[tuple[int, int], list[str]] = {}
    for event in text_events:
        output_index = _codex_output_index(getattr(event, "output_index", None))
        content_index = _codex_output_index(getattr(event, "content_index", None))
        slot = (output_index, content_index)
        item_id = getattr(event, "item_id", None)
        delta = getattr(event, "delta", None)
        if (
            slot not in text_slots
            or not isinstance(item_id, str)
            or not item_id
            or item_id != text_slots[slot][0]
            or not isinstance(delta, str)
        ):
            raise ProviderProtocolError(_STREAM_PROTOCOL_ERROR)
        observed_text.setdefault(slot, []).append(delta)
    if any("".join(parts) != text_slots[slot][1] for slot, parts in observed_text.items()):
        raise ProviderProtocolError(_STREAM_PROTOCOL_ERROR)

    observed_calls: set[str] = set()
    observed_indices: set[int] = set()
    for event in call_events:
        index = _codex_output_index(getattr(event, "output_index", None))
        call = _codex_function_call(event.item)
        item_id = _codex_item_id(event.item, required=False)
        if (
            index not in calls_by_index
            or call != calls_by_index[index]
            or call["id"] in observed_calls
            or index in observed_indices
        ):
            raise ProviderProtocolError(_STREAM_PROTOCOL_ERROR)
        final_id = getattr(completed_response.output[index], "id", None)
        if item_id is not None and final_id is not None and item_id != final_id:
            raise ProviderProtocolError(_STREAM_PROTOCOL_ERROR)
        observed_calls.add(call["id"])
        observed_indices.add(index)

    tool_calls = list(calls_by_index.values())
    if not content.strip() and not tool_calls:
        raise CodexResponseStreamError(
            "Codex Responses API response completed without text or tool calls"
        )

    message: dict[str, Any] = {"role": "assistant", "content": content or None}
    if tool_calls:
        message["tool_calls"] = tool_calls
    completion: dict[str, Any] = {
        "id": completion_id,
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if tool_calls else "stop",
            }
        ],
    }
    if getattr(completed_response, "usage", None) is not None:
        completion["usage"] = _translate_usage(completed_response.usage)
    return completion


def _stream_error_detail(event: Any) -> str:
    code = getattr(event, "code", None)
    message = getattr(event, "message", None)
    detail = ": ".join(str(value) for value in (code, message) if value)
    return f"Codex Responses API stream error{f': {detail}' if detail else ''}"


def _terminal_response_error_detail(event_type: str, response: Any) -> str:
    state = event_type.removeprefix("response.")
    detail = ""
    if state == "failed":
        error = getattr(response, "error", None)
        code = getattr(error, "code", None)
        message = getattr(error, "message", None)
        detail = ": ".join(str(value) for value in (code, message) if value)
    elif state == "incomplete":
        incomplete = getattr(response, "incomplete_details", None)
        reason = getattr(incomplete, "reason", None)
        detail = str(reason or "")
    return f"Codex Responses API response {state}{f': {detail}' if detail else ''}"


def _codex_output_index(value: Any) -> int:
    if type(value) is not int or value < 0:
        raise ProviderProtocolError(_STREAM_PROTOCOL_ERROR)
    return value


def _codex_completed_status(value: Any) -> None:
    status = getattr(value, "status", None)
    if status is not None and status != "completed":
        raise ProviderProtocolError(_STREAM_PROTOCOL_ERROR)


def _codex_item_id(item: Any, *, required: bool) -> str | None:
    value = getattr(item, "id", None)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value:
        raise ProviderProtocolError(_STREAM_PROTOCOL_ERROR)
    return value


def _codex_function_call(item: Any) -> dict[str, Any]:
    _codex_completed_status(item)
    call_id, name = getattr(item, "call_id", None), getattr(item, "name", None)
    arguments = getattr(item, "arguments", None)
    if (
        not isinstance(call_id, str)
        or not call_id.strip()
        or not isinstance(name, str)
        or not name.strip()
        or not isinstance(arguments, str)
    ):
        raise ProviderProtocolError(_STREAM_PROTOCOL_ERROR)
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


def _codex_completed_output(
    response: Any,
) -> tuple[str, dict[int, dict[str, Any]], dict[tuple[int, int], tuple[str, str]]]:
    _codex_completed_status(response)
    output = getattr(response, "output", None)
    if not isinstance(output, list):
        raise ProviderProtocolError(_STREAM_PROTOCOL_ERROR)
    text_parts: list[str] = []
    refusals: list[str] = []
    calls: dict[int, dict[str, Any]] = {}
    text_slots: dict[tuple[int, int], tuple[str, str]] = {}
    item_ids: set[str] = set()
    call_ids: set[str] = set()
    for index, item in enumerate(output):
        kind = getattr(item, "type", None)
        if not isinstance(kind, str) or not kind:
            raise ProviderProtocolError(_STREAM_PROTOCOL_ERROR)
        if kind not in {"message", "function_call"}:
            continue
        _codex_completed_status(item)
        item_id = _codex_item_id(item, required=kind == "message")
        if item_id is not None:
            if item_id in item_ids:
                raise ProviderProtocolError(_STREAM_PROTOCOL_ERROR)
            item_ids.add(item_id)
        if kind == "function_call":
            call = _codex_function_call(item)
            if call["id"] in call_ids:
                raise ProviderProtocolError(_STREAM_PROTOCOL_ERROR)
            call_ids.add(call["id"])
            calls[index] = call
            continue
        blocks = getattr(item, "content", None)
        if not isinstance(blocks, list):
            raise ProviderProtocolError(_STREAM_PROTOCOL_ERROR)
        for content_index, block in enumerate(blocks):
            content_type = getattr(block, "type", None)
            if not isinstance(content_type, str) or not content_type:
                raise ProviderProtocolError(_STREAM_PROTOCOL_ERROR)
            if content_type not in {"output_text", "refusal"}:
                continue
            text = getattr(block, "text" if content_type == "output_text" else "refusal", None)
            if not isinstance(text, str):
                raise ProviderProtocolError(_STREAM_PROTOCOL_ERROR)
            if content_type == "output_text":
                text_parts.append(text)
                # The required message ID was validated above.
                assert item_id is not None
                text_slots[index, content_index] = (item_id, text)
            else:
                refusals.append(text)
    return "".join(text_parts) or "".join(refusals), calls, text_slots


def _translate_usage(usage: Any) -> dict[str, Any]:
    input_tokens = getattr(usage, "input_tokens", None) or 0
    output_tokens = getattr(usage, "output_tokens", None) or 0
    total_tokens = getattr(usage, "total_tokens", None) or input_tokens + output_tokens
    return {
        "prompt_tokens": input_tokens,
        "completion_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def _translate_tool(tool: dict[str, Any]) -> dict[str, Any]:
    if tool.get("type") != "function":
        return tool
    fn = tool.get("function", {})
    parameters = fn.get("parameters") or {"type": "object", "properties": {}}
    return {
        "type": "function",
        "name": fn.get("name", ""),
        "description": fn.get("description", ""),
        "parameters": _drop_null_schema_metadata(parameters),
    }


def _drop_null_schema_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _drop_null_schema_metadata(item) for key, item in value.items() if item is not None
        }
    if isinstance(value, list):
        return [_drop_null_schema_metadata(item) for item in value]
    return value


def _stringify_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict):
                if part.get("type") in ("text", "input_text", "output_text"):
                    parts.append(str(part.get("text", "")))
                elif "text" in part:
                    parts.append(str(part["text"]))
        return "".join(parts)
    return str(content)


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:24]}"
