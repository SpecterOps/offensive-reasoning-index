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
from functools import lru_cache
from pathlib import Path
from typing import Any

DEFAULT_CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
DEFAULT_CODEX_ORIGINATOR = "codex_cli_rs"
DEFAULT_CODEX_VERSION = "0.133.0"
DEFAULT_CODEX_MODEL = "gpt-5.5-codex"


class CodexResponseStreamError(RuntimeError):
    """Raised when a Codex Responses stream does not complete successfully."""


def codex_auth_path() -> Path:
    default_path = Path.home() / ".codex" / "auth.json"
    return Path(os.environ.get("CODEX_AUTH_FILE", default_path)).expanduser()


def codex_access_token() -> str:
    explicit = os.environ.get("CODEX_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if explicit:
        return explicit
    path = codex_auth_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(
            "Codex OAuth token not found. Run Codex login first or set CODEX_API_KEY. "
            f"Missing: {path}"
        ) from exc
    token = (data.get("tokens") or {}).get("access_token") or data.get("access_token")
    if not isinstance(token, str) or not token:
        raise RuntimeError(f"Codex OAuth access_token missing in {path}")
    return token


def codex_base_url(base_url: str | None = None) -> str:
    return (base_url or os.environ.get("CODEX_BASE_URL") or DEFAULT_CODEX_BASE_URL).rstrip("/")


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


def codex_headers(*, thread_id: str | None = None) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {codex_access_token()}",
        "User-Agent": codex_user_agent(),
        "originator": _originator(),
        "session-id": _session_id(),
        "x-codex-installation-id": _installation_id(),
    }
    if thread_id:
        headers["thread-id"] = thread_id
    return headers


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
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    finish_reason = "stop"
    usage: dict[str, Any] | None = None
    completed_response: Any | None = None

    for event in events:
        etype = getattr(event, "type", None)
        if etype == "response.output_text.delta":
            text_parts.append(getattr(event, "delta", "") or "")
        elif etype == "response.output_item.done":
            item = getattr(event, "item", None)
            if item is not None and getattr(item, "type", None) == "function_call":
                tool_calls.append(
                    {
                        "id": getattr(item, "call_id", "") or _new_id("call"),
                        "type": "function",
                        "function": {
                            "name": getattr(item, "name", "") or "",
                            "arguments": getattr(item, "arguments", "") or "{}",
                        },
                    }
                )
                finish_reason = "tool_calls"
        elif etype == "error":
            raise CodexResponseStreamError(_stream_error_detail(event))
        elif etype in {"response.failed", "response.incomplete"}:
            response = getattr(event, "response", None)
            raise CodexResponseStreamError(_terminal_response_error_detail(etype, response))
        elif etype == "response.completed":
            response = getattr(event, "response", None)
            completed_response = response
            if response is not None and getattr(response, "usage", None) is not None:
                usage = _translate_usage(response.usage)

    if completed_response is None:
        raise CodexResponseStreamError(
            "Codex Responses API stream ended without a response.completed event"
        )

    if not text_parts:
        fallback_text = _response_output_text(completed_response)
        if fallback_text:
            text_parts.append(fallback_text)
    if not tool_calls:
        tool_calls.extend(_response_function_calls(completed_response))
        if tool_calls:
            finish_reason = "tool_calls"

    content = "".join(text_parts)
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
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
    }
    if usage is not None:
        completion["usage"] = usage
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


def _response_output_text(response: Any) -> str:
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str) and output_text:
        return output_text

    parts: list[str] = []
    for item in getattr(response, "output", None) or []:
        if getattr(item, "type", None) != "message":
            continue
        for content in getattr(item, "content", None) or []:
            content_type = getattr(content, "type", None)
            if content_type == "output_text":
                text = getattr(content, "text", None)
            elif content_type == "refusal":
                text = getattr(content, "refusal", None)
            else:
                text = None
            if isinstance(text, str) and text:
                parts.append(text)
    return "".join(parts)


def _response_function_calls(response: Any) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for item in getattr(response, "output", None) or []:
        if getattr(item, "type", None) != "function_call":
            continue
        calls.append(
            {
                "id": getattr(item, "call_id", "") or _new_id("call"),
                "type": "function",
                "function": {
                    "name": getattr(item, "name", "") or "",
                    "arguments": getattr(item, "arguments", "") or "{}",
                },
            }
        )
    return calls


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
