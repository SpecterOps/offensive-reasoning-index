"""Shared provider request and normalized turn contracts.

This module intentionally contains no network transport. Provider adapters turn
wire/SDK responses into :class:`ProviderTurn` values here so direct and MCP
consumers interpret the same provider envelope identically.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal, TypeAlias

GEMINI_OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


class ProviderApiSurface(StrEnum):
    """Provider API surface requested by campaign configuration."""

    AUTO = "auto"
    CHAT_COMPLETIONS = "chat_completions"
    RESPONSES = "responses"
    MESSAGES = "messages"


class ProviderTurnStatus(StrEnum):
    """Protocol-level terminal state of one normalized provider turn."""

    COMPLETED = "completed"
    TOOL_CALLS = "tool_calls"
    REFUSED = "refused"
    REASONING_ONLY = "reasoning_only"
    TRUNCATED = "truncated"
    CONTENT_FILTERED = "content_filtered"
    EMPTY = "empty"
    CONTINUATION_REQUIRED = "continuation_required"


class ToolArgumentParseStatus(StrEnum):
    """Whether tool-call arguments can safely be passed to a tool executor."""

    VALID = "valid"
    EMPTY = "empty"
    MALFORMED = "malformed"
    NON_OBJECT = "non_object"


class ProviderContractError(RuntimeError):
    """Base class for provider contract failures known to ORI."""

    code = "PROVIDER_CONTRACT_ERROR"
    retryable = False


class ProviderProtocolError(ProviderContractError):
    """The provider returned a successful but incompatible response envelope."""

    code = "PROVIDER_PROTOCOL"


class ProviderGenerationError(ProviderContractError):
    """A provider explicitly failed generation without a structured retry cause."""

    code = "PROVIDER_GENERATION_ERROR"


class ProviderCapabilityError(ProviderContractError):
    """The selected provider does not support the requested API surface."""

    code = "PROVIDER_CAPABILITY"


class ProviderAuthenticationError(ProviderContractError):
    """The selected endpoint has no credential scoped to its provider family."""

    code = "PROVIDER_AUTH"


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    """Provider-neutral inference request.

    ``api_surface`` is the selected (not merely requested) wire surface. Campaign
    configuration records the requested value separately for provenance.
    """

    messages: tuple[dict[str, Any], ...]
    api_surface: ProviderApiSurface
    tools: tuple[dict[str, Any], ...] = ()
    tool_choice: str | dict[str, Any] | None = None
    output_limit: int | None = None
    reasoning: dict[str, Any] = field(default_factory=dict)
    structured_output_schema: dict[str, Any] | None = None


def chat_completions_payload(
    request: ProviderRequest,
    *,
    model: str,
) -> dict[str, Any]:
    """Project the shared request contract onto Chat Completions."""

    if request.api_surface is not ProviderApiSurface.CHAT_COMPLETIONS:
        raise ProviderCapabilityError(
            "Chat Completions payload requires api_surface='chat_completions'"
        )
    payload: dict[str, Any] = {
        "model": model,
        "messages": [dict(message) for message in request.messages],
    }
    if request.tools:
        payload["tools"] = [dict(tool) for tool in request.tools]
    if request.tool_choice is not None:
        payload["tool_choice"] = request.tool_choice
    if request.output_limit is not None:
        payload["max_tokens"] = request.output_limit
    if request.reasoning:
        payload.update(dict(request.reasoning))
    if request.structured_output_schema is not None:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": dict(request.structured_output_schema),
        }
    return payload


@dataclass(frozen=True, slots=True)
class ProviderUsage:
    """Token usage without converting unknown values to zero."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    usage_reported: bool = False
    usage_complete: bool = False
    cache_read_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class ProviderTextOutput:
    type: Literal["text"] = "text"
    text: str = ""


@dataclass(frozen=True, slots=True)
class ProviderRefusalOutput:
    refusal: str
    type: Literal["refusal"] = "refusal"


@dataclass(frozen=True, slots=True)
class ProviderReasoningOutput:
    reasoning: str
    type: Literal["reasoning"] = "reasoning"


@dataclass(frozen=True, slots=True)
class ProviderToolCall:
    """A lossless normalized function call."""

    id: str
    name: str
    raw_arguments: str
    parsed_arguments: dict[str, Any] | None
    argument_parse_status: ToolArgumentParseStatus
    argument_parse_error: str | None = None
    type: Literal["tool_call"] = "tool_call"


ProviderOutputItem: TypeAlias = (
    ProviderTextOutput | ProviderRefusalOutput | ProviderReasoningOutput | ProviderToolCall
)


@dataclass(frozen=True, slots=True)
class ProviderTurn:
    """Lossless provider turn with a non-null final-text projection."""

    text: str
    refusal: str
    reasoning: str
    tool_calls: tuple[ProviderToolCall, ...]
    output_items: tuple[ProviderOutputItem, ...]
    status: ProviderTurnStatus
    finish_reason: str
    usage: ProviderUsage
    response_id: str
    provider: str
    model: str
    endpoint: str
    api_surface: ProviderApiSurface = ProviderApiSurface.CHAT_COMPLETIONS
    native_content_blocks: tuple[dict[str, Any], ...] = ()


def resolve_api_surface(
    provider: str,
    requested: ProviderApiSurface | str = ProviderApiSurface.AUTO,
) -> ProviderApiSurface:
    """Resolve ``auto`` while preserving ORI's existing provider behavior.

    Existing Codex OAuth calls use Responses. Existing official OpenAI and all
    OpenAI-compatible providers use Chat Completions. Explicit values are
    returned unchanged; readiness owns capability enforcement.
    """

    surface = ProviderApiSurface(requested)
    # Retain the historical configuration spelling as an explicit alias, while
    # recording the actual Anthropic wire surface rather than Chat Completions.
    if provider == "anthropic" and surface is ProviderApiSurface.CHAT_COMPLETIONS:
        return ProviderApiSurface.MESSAGES
    if surface is not ProviderApiSurface.AUTO:
        return surface
    if provider == "codex":
        return ProviderApiSurface.RESPONSES
    if provider == "anthropic":
        return ProviderApiSurface.MESSAGES
    return ProviderApiSurface.CHAT_COMPLETIONS


def validate_release1_api_surface(provider: str, surface: ProviderApiSurface | str) -> None:
    """Reject Release-1 surface selections that are not yet production-enabled."""

    resolved = ProviderApiSurface(surface)
    if resolved is ProviderApiSurface.AUTO:
        resolved = resolve_api_surface(provider, resolved)
    if resolved is ProviderApiSurface.MESSAGES and provider != "anthropic":
        raise ProviderCapabilityError("Messages requires the Anthropic provider")
    if provider == "anthropic" and resolved not in {
        ProviderApiSurface.MESSAGES, ProviderApiSurface.CHAT_COMPLETIONS,
    }:
        raise ProviderCapabilityError("Anthropic requires the Messages API")
    if provider == "codex" and resolved is not ProviderApiSurface.RESPONSES:
        raise ProviderCapabilityError(
            "Provider 'codex' requires api_surface='responses' in Release 1"
        )
    if provider != "codex" and resolved is ProviderApiSurface.RESPONSES:
        raise ProviderCapabilityError(
            f"Provider {provider!r} does not support api_surface='responses' in Release 1"
        )


def normalize_chat_completion(
    envelope: Any,
    *,
    provider: str,
    endpoint: str = "",
    fallback_model: str = "",
) -> ProviderTurn:
    """Normalize one non-streaming Chat Completions response.

    SDK objects and decoded JSON mappings are both accepted. Missing choices or
    messages are protocol failures. Nullable content is represented as an empty
    text projection while refusal, reasoning, and tool calls remain available as
    typed output items.
    """

    if envelope is None:
        raise ProviderProtocolError("Chat Completions response envelope is null")

    choices_value = _field(envelope, "choices", _MISSING)
    if choices_value is _MISSING or choices_value is None:
        raise ProviderProtocolError("Chat Completions response is missing choices")
    if isinstance(choices_value, (str, bytes, Mapping)) or not isinstance(
        choices_value, Sequence
    ):
        raise ProviderProtocolError("Chat Completions choices must be an array")
    if not choices_value:
        raise ProviderProtocolError("Chat Completions response contains no choices")

    choice = choices_value[0]
    message = _field(choice, "message", _MISSING)
    if message is _MISSING or message is None:
        raise ProviderProtocolError("Chat Completions choice is missing its message")

    text = _normalize_text(_field(message, "content", None), field_name="message.content")
    refusal = _normalize_text(_field(message, "refusal", None), field_name="message.refusal")
    reasoning = _first_text(
        _field(message, "reasoning", None),
        _field(message, "reasoning_content", None),
        _field(message, "thinking", None),
        _field(envelope, "reasoning", None),
    )
    tool_calls = _normalize_tool_calls(_field(message, "tool_calls", None))
    finish_reason = _optional_string(_field(choice, "finish_reason", None))
    usage = _normalize_usage(envelope)

    output_items: list[ProviderOutputItem] = []
    if reasoning:
        output_items.append(ProviderReasoningOutput(reasoning=reasoning))
    if text:
        output_items.append(ProviderTextOutput(text=text))
    if refusal:
        output_items.append(ProviderRefusalOutput(refusal=refusal))
    output_items.extend(tool_calls)

    return ProviderTurn(
        text=text,
        refusal=refusal,
        reasoning=reasoning,
        tool_calls=tool_calls,
        output_items=tuple(output_items),
        status=_turn_status(
            finish_reason=finish_reason,
            text=text,
            refusal=refusal,
            reasoning=reasoning,
            tool_calls=tool_calls,
        ),
        finish_reason=finish_reason,
        usage=usage,
        response_id=_optional_string(_field(envelope, "id", None)),
        provider=provider,
        model=_optional_string(_field(envelope, "model", None)) or fallback_model,
        endpoint=endpoint,
    )


def anthropic_output_config(descriptor: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Map ORI's strict schema descriptor to Messages without schema rewriting."""
    if descriptor is None:
        return None
    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import SchemaError

    if (not isinstance(descriptor, Mapping) or set(descriptor) != {"name", "strict", "schema"}
            or descriptor["strict"] is not True
            or not isinstance(descriptor["name"], str) or not descriptor["name"].strip()
            or not isinstance(descriptor["schema"], dict)):
        raise ProviderCapabilityError("Invalid Anthropic strict output descriptor")
    schema = descriptor["schema"]
    if schema.get("$schema") not in (None, "https://json-schema.org/draft/2020-12/schema"):
        raise ProviderCapabilityError("Unsupported Anthropic output schema dialect")
    try:
        if json.loads(json.dumps(schema, allow_nan=False)) != schema:
            raise ValueError("schema must use JSON containers and keys")
        Draft202012Validator.check_schema(schema)
    except (TypeError, ValueError, SchemaError):
        raise ProviderCapabilityError("Invalid Anthropic output schema") from None
    return {"format": {"type": "json_schema", "schema": deepcopy(schema)}}


def normalize_anthropic_message(
    envelope: Any, *, endpoint: str = "", fallback_model: str = "",
) -> ProviderTurn:
    """Interpret Messages blocks without losing private continuation material."""
    content = _field(envelope, "content")
    if not isinstance(content, (list, tuple)):
        raise ProviderProtocolError("Anthropic content must be an array")
    reason = _field(envelope, "stop_reason")
    if not isinstance(reason, str) or reason not in {
        "end_turn", "stop_sequence", "tool_use", "max_tokens", "refusal",
        "pause_turn", "model_context_window_exceeded",
    }:
        raise ProviderProtocolError("Unsupported Anthropic stop reason")
    raw_blocks, items, calls = [], [], []
    texts, thoughts = [], []
    ids = set()
    for value in content:
        block = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
        if not isinstance(block, Mapping):
            raise ProviderProtocolError("Anthropic content block must be an object")
        try:
            raw = json.loads(json.dumps(dict(block), allow_nan=False))
        except (TypeError, ValueError):
            raise ProviderProtocolError("Anthropic content block is not JSON") from None
        raw_blocks.append(deepcopy(raw))
        kind = raw.get("type")
        if kind in {"text", "thinking"}:
            key = "text" if kind == "text" else "thinking"
            text = raw.get(key)
            if not isinstance(text, str):
                raise ProviderProtocolError("Anthropic text block is malformed")
            if kind == "text":
                texts.append(text)
                items.append(ProviderTextOutput(text=text))
            else:
                if not isinstance(raw.get("signature"), str):
                    raise ProviderProtocolError("Anthropic thinking signature is missing")
                thoughts.append(text)
                items.append(ProviderReasoningOutput(reasoning=text))
        elif kind == "redacted_thinking":
            if not isinstance(raw.get("data"), str):
                raise ProviderProtocolError("Anthropic redacted thinking is malformed")
        elif kind == "tool_use":
            call_id, name, arguments = raw.get("id"), raw.get("name"), raw.get("input")
            if (not isinstance(call_id, str) or not call_id or call_id in ids
                    or not isinstance(name, str) or not name or not isinstance(arguments, dict)):
                raise ProviderProtocolError("Anthropic tool request is malformed")
            ids.add(call_id)
            call = ProviderToolCall(
                id=call_id, name=name, raw_arguments=json.dumps(arguments, allow_nan=False),
                parsed_arguments=deepcopy(arguments),
                argument_parse_status=ToolArgumentParseStatus.VALID,
            )
            calls.append(call)
            items.append(call)
        else:
            raise ProviderCapabilityError("Unsupported Anthropic content block type")
    text, thinking = "".join(texts), "".join(thoughts)
    if reason == "tool_use" and not calls:
        raise ProviderProtocolError("Anthropic tool stop has no tool requests")
    if calls and reason in {"end_turn", "stop_sequence", "pause_turn"}:
        raise ProviderProtocolError("Anthropic tool requests have an incompatible stop reason")
    status = (
        ProviderTurnStatus.TRUNCATED if reason in {"max_tokens", "model_context_window_exceeded"}
        else ProviderTurnStatus.REFUSED if reason == "refusal"
        else ProviderTurnStatus.CONTINUATION_REQUIRED if reason == "pause_turn"
        else ProviderTurnStatus.TOOL_CALLS if reason == "tool_use"
        else ProviderTurnStatus.COMPLETED if text
        else ProviderTurnStatus.REASONING_ONLY if thinking
        else ProviderTurnStatus.EMPTY
    )
    usage = _field(envelope, "usage")
    if usage is not None and not isinstance(usage, Mapping) and not hasattr(usage, "__dict__"):
        raise ProviderProtocolError("Anthropic usage must be an object or null")
    counts = {key: _optional_token_count(_field(usage, key)) for key in (
        "input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens",
    )}
    # Anthropic input_tokens excludes cache reads/creation. Preserve that counter
    # separately; missing cache counters cannot establish a total context count.
    total = sum(counts.values()) if all(v is not None for v in counts.values()) else None
    return ProviderTurn(
        text=text, refusal=text if reason == "refusal" else "", reasoning=thinking,
        tool_calls=tuple(calls), output_items=tuple(items), status=status, finish_reason=reason,
        usage=ProviderUsage(**counts, total_tokens=total, usage_reported=usage is not None,
                            usage_complete=all(v is not None for v in counts.values())),
        response_id=_optional_string(_field(envelope, "id")), provider="anthropic",
        model=_optional_string(_field(envelope, "model")) or fallback_model, endpoint=endpoint,
        api_surface=ProviderApiSurface.MESSAGES, native_content_blocks=tuple(raw_blocks),
    )


_MISSING = object()


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _optional_string(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _normalize_text(value: Any, *, field_name: str) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        parts: list[str] = []
        for part in value:
            part_type = _field(part, "type", "")
            part_text = _field(part, "text", None)
            if part_type in {"text", "output_text", "refusal"} and isinstance(part_text, str):
                parts.append(part_text)
            elif isinstance(part, str):
                parts.append(part)
        return "".join(parts)
    raise ProviderProtocolError(f"{field_name} must be a string, array, or null")


def _first_text(*values: Any) -> str:
    for value in values:
        text = _normalize_text(value, field_name="reasoning")
        if text:
            return text
    return ""


def _normalize_tool_calls(value: Any) -> tuple[ProviderToolCall, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Sequence):
        raise ProviderProtocolError("message.tool_calls must be an array or null")

    normalized: list[ProviderToolCall] = []
    for index, call in enumerate(value):
        function = _field(call, "function", None)
        if function is None:
            raise ProviderProtocolError(f"message.tool_calls[{index}] is missing function")
        name = _field(function, "name", None)
        if not isinstance(name, str) or not name:
            raise ProviderProtocolError(
                f"message.tool_calls[{index}].function is missing a name"
            )
        call_id = _field(call, "id", "")
        if call_id is None:
            call_id = ""
        if not isinstance(call_id, str):
            raise ProviderProtocolError(f"message.tool_calls[{index}].id must be a string")
        raw_arguments, parsed, parse_status, parse_error = _parse_tool_arguments(
            _field(function, "arguments", None)
        )
        normalized.append(
            ProviderToolCall(
                id=call_id,
                name=name,
                raw_arguments=raw_arguments,
                parsed_arguments=parsed,
                argument_parse_status=parse_status,
                argument_parse_error=parse_error,
            )
        )
    return tuple(normalized)


def _parse_tool_arguments(
    value: Any,
) -> tuple[str, dict[str, Any] | None, ToolArgumentParseStatus, str | None]:
    if value is None or value == "":
        return "" if value is None else value, None, ToolArgumentParseStatus.EMPTY, None
    if isinstance(value, Mapping):
        parsed = dict(value)
        return (
            json.dumps(parsed, sort_keys=True, separators=(",", ":")),
            parsed,
            ToolArgumentParseStatus.VALID,
            None,
        )
    if not isinstance(value, str):
        return (
            str(value),
            None,
            ToolArgumentParseStatus.MALFORMED,
            f"tool arguments must be a JSON object or string, got {type(value).__name__}",
        )
    try:
        parsed_value = json.loads(value)
    except json.JSONDecodeError as exc:
        return value, None, ToolArgumentParseStatus.MALFORMED, str(exc)
    if not isinstance(parsed_value, dict):
        return (
            value,
            None,
            ToolArgumentParseStatus.NON_OBJECT,
            "tool arguments JSON must decode to an object",
        )
    return value, parsed_value, ToolArgumentParseStatus.VALID, None


def _normalize_usage(envelope: Any) -> ProviderUsage:
    usage_value = _field(envelope, "usage", _MISSING)
    if usage_value is _MISSING or usage_value is None:
        return ProviderUsage()
    if not isinstance(usage_value, Mapping) and not hasattr(usage_value, "__dict__"):
        raise ProviderProtocolError("Chat Completions usage must be an object or null")

    input_tokens = _optional_token_count(_field(usage_value, "prompt_tokens", None))
    output_tokens = _optional_token_count(_field(usage_value, "completion_tokens", None))
    total_tokens = _optional_token_count(_field(usage_value, "total_tokens", None))
    return ProviderUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        usage_reported=True,
        usage_complete=all(value is not None for value in (input_tokens, output_tokens)),
    )


def _optional_token_count(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProviderProtocolError("token usage values must be non-negative integers or null")
    return value


def _turn_status(
    *,
    finish_reason: str,
    text: str,
    refusal: str,
    reasoning: str,
    tool_calls: tuple[ProviderToolCall, ...],
) -> ProviderTurnStatus:
    if finish_reason == "content_filter":
        return ProviderTurnStatus.CONTENT_FILTERED
    if finish_reason == "length":
        return ProviderTurnStatus.TRUNCATED
    if tool_calls:
        return ProviderTurnStatus.TOOL_CALLS
    if refusal:
        return ProviderTurnStatus.REFUSED
    if text:
        return ProviderTurnStatus.COMPLETED
    if reasoning:
        return ProviderTurnStatus.REASONING_ONLY
    return ProviderTurnStatus.EMPTY
