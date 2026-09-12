"""Native Messages development loop over the existing guarded MCP bridge."""

from __future__ import annotations

import asyncio
import json
import math
import time
from copy import deepcopy

import httpx
from inspect_ai.model import ChatMessageAssistant, ChatMessageTool, ChatMessageUser
from inspect_ai.tool import ToolCall, ToolCallError

from .adapter import ModelResponse, _direct_text_projection, _provider_turn_metrics
from .anthropic_binding import materialize_anthropic_client
from .mcp_runtime import FINALIZATION_GUARD_PROMPT, _execute_mcp_tool, _tool_result_to_text
from .provider_contract import (
    ProviderAuthenticationError,
    ProviderCapabilityError,
    ProviderProtocolError,
    ProviderTurnStatus,
    _optional_token_count,
    anthropic_output_config,
    normalize_anthropic_message,
)


async def run_anthropic_mcp_loop(
    *, bridge, binding, system_prompt: str, max_steps: int, max_tokens: int,
    read_timeout_seconds: float, tool_timeout_seconds: float, progress_observer=None,
    finalization_schema=None,
    streaming: bool = False,
):
    """Return a response and Inspect trace; native history stays private in metrics.

    The owning V2 runner supplies the whole-task deadline. This function does
    not certify the session or expose a new campaign admission path.
    """
    from .v2.native_mcp_runtime import NativeModelToolBridge, NativeToolCallable

    output_config = anthropic_output_config(finalization_schema)
    if type(streaming) is not bool:
        raise ProviderCapabilityError("Anthropic streaming selection must be boolean")
    if not isinstance(bridge, NativeModelToolBridge) or not all(
        isinstance(tool, NativeToolCallable) for tool in bridge.tools
    ):
        raise ProviderCapabilityError("Anthropic MCP requires native callable descriptors")
    if (type(max_steps) is not int or max_steps <= 0
            or type(max_tokens) is not int or max_tokens <= 0
            or any(isinstance(value, bool) or not isinstance(value, (int, float))
                   or not math.isfinite(value) or value <= 0
                   for value in (read_timeout_seconds, tool_timeout_seconds))):
        raise ProviderCapabilityError("Anthropic MCP requires finite positive bounds")
    tools, runners = [], {}
    for tool in bridge.tools:
        descriptor = tool.descriptor
        name = descriptor["name"]
        if name in runners:
            raise ProviderCapabilityError("Duplicate native tool name")
        runners[name] = tool
        tools.append({"name": name, "description": descriptor.get("description", ""),
                      "input_schema": descriptor["inputSchema"]})
    system_prompt += "\n" + bridge.protocol_instructions
    question = bridge.task.question + "\n" + bridge.protocol_catalog
    history = [{"role": "user", "content": question}]
    messages = [ChatMessageUser(content=question)]
    pending, seen_ids, turns = [], set(), []
    input_tokens = output_tokens = executed = requested = 0
    thinking, final_text = [], ""
    started = time.monotonic()
    metrics = {}
    stream_partial = None
    first_token_seconds = None
    stream_raw_inputs = {}

    def response(text="", error=None):
        def total(key, *, cache=False):
            values = [turn.get(key) if cache else turn["usage"].get(key) for turn in turns]
            return (sum(values) if values and requested == len(turns)
                    and all(value is not None for value in values) else None)

        usage = {key: total(key) for key in ("input_tokens", "output_tokens", "total_tokens")}
        caches = {key: total(key, cache=True) for key in (
            "cache_read_input_tokens", "cache_creation_input_tokens",
        )}
        partial_usage = (stream_partial or {}).get("usage") or {}
        partial_input = partial_usage.get("input_tokens")
        partial_output = partial_usage.get("output_tokens")
        return ModelResponse(
            raw_text=text, cypher=None, parse_stage="mcp_final_answer" if text else "mcp_partial",
            tokens_input=input_tokens + (partial_input if type(partial_input) is int else 0),
            tokens_output=output_tokens + (partial_output if type(partial_output) is int else 0),
            elapsed_seconds=time.monotonic() - started, model=binding.model_slug,
            thinking="".join(thinking), error=error,
            provider_metrics={**metrics, "provider": "anthropic_native_messages_mcp_loop",
                              "turn_metrics": deepcopy(turns), "native_history": deepcopy(history),
                              "pending_tool_calls": deepcopy(pending),
                              "usage": usage, **caches,
                              "usage_reported": any(t["usage_reported"] for t in turns),
                              "usage_complete": bool(turns) and requested == len(turns)
                              and all(t["usage_complete"] for t in turns),
                              "provider_request_count": requested,
                              "completed_provider_turns": len(turns),
                              "stream_partial": deepcopy(stream_partial),
                              "stream_tool_inputs": deepcopy(stream_raw_inputs),
                              "time_to_first_token_seconds": first_token_seconds,
                              "input_tokens_semantics": "uncached_input"},
        )

    def progress():
        if progress_observer is not None:
            progress_observer(response(), list(messages))

    progress()
    attempt = await materialize_anthropic_client(binding)
    try:
        import anthropic

        client = attempt.client
        headers = {key: value for key, value in client.default_headers.items()
                   if not isinstance(value, anthropic.Omit)}
        try:
            client._validate_headers(headers, {})
        except TypeError:
            raise ProviderAuthenticationError("Anthropic authentication is unavailable") from None
        for step in range(max_steps):
            finalize = bridge.finalization_ready and (
                step == max_steps - 1 or executed + len(bridge.protocol_calls) >= max_steps
            )
            if finalize:
                history.append({"role": "user", "content": FINALIZATION_GUARD_PROMPT})
            requested += 1
            progress()
            request = dict(model=binding.model_slug, system=system_prompt,
                           messages=deepcopy(history), tools=[] if finalize else tools,
                           max_tokens=max_tokens)
            if finalize and output_config is not None:
                request["output_config"] = output_config
            try:
                async with asyncio.timeout(read_timeout_seconds):
                    if streaming:
                        started_message = stopped_message = False
                        active_index = None
                        next_index = 0
                        saw_delta = False
                        stream_raw_inputs = {}
                        async with client.messages.stream(**request) as stream:
                            async for event in stream:
                                if stopped_message:
                                    raise ProviderProtocolError("Anthropic data after message_stop")
                                if event.type == "message_start":
                                    if started_message or event.message.content:
                                        raise ProviderProtocolError("Duplicate message_start")
                                    started_message = True
                                elif event.type == "content_block_start":
                                    if (not started_message or saw_delta or active_index is not None
                                            or type(event.index) is not int
                                            or event.index != next_index):
                                        raise ProviderProtocolError("Invalid stream block start")
                                    active_index = next_index
                                    next_index += 1
                                elif event.type == "content_block_delta":
                                    if active_index is None or event.index != active_index:
                                        raise ProviderProtocolError("Invalid stream block delta")
                                elif event.type == "content_block_stop":
                                    if active_index is None or event.index != active_index:
                                        raise ProviderProtocolError("Invalid stream block stop")
                                    block = stream.current_message_snapshot.content[active_index]
                                    raw_input = getattr(block, "__json_buf", None)
                                    if raw_input is not None:
                                        stream_raw_inputs[str(active_index)] = raw_input.decode()
                                        try:
                                            parsed = json.loads(raw_input)
                                            json.dumps(parsed, allow_nan=False)
                                            if (not isinstance(parsed, dict)
                                                    or parsed != block.input):
                                                raise ValueError("input mismatch")
                                        except (ValueError, TypeError):
                                            raise ProviderProtocolError(
                                                "Incomplete or invalid streamed tool arguments"
                                            ) from None
                                    active_index = None
                                elif event.type == "message_delta":
                                    if not started_message or saw_delta or active_index is not None:
                                        raise ProviderProtocolError("Unclosed stream block")
                                    saw_delta = True
                                elif event.type == "message_stop":
                                    if active_index is not None or not saw_delta:
                                        raise ProviderProtocolError("Premature message_stop")
                                    stopped_message = True
                                if (event.type == "content_block_delta"
                                        and event.delta.type in {
                                            "text_delta", "thinking_delta", "input_json_delta",
                                        } and first_token_seconds is None):
                                    first_token_seconds = time.monotonic() - started
                                snapshot = stream.current_message_snapshot.model_dump(mode="json")
                                if (event.type == "content_block_delta"
                                        and event.delta.type == "input_json_delta"):
                                    block = stream.current_message_snapshot.content[event.index]
                                    stream_raw_inputs[str(event.index)] = getattr(
                                        block, "__json_buf", b"",
                                    ).decode()
                                for key in (
                                    "input_tokens", "output_tokens", "cache_read_input_tokens",
                                    "cache_creation_input_tokens",
                                ):
                                    _optional_token_count((snapshot.get("usage") or {}).get(key))
                                stream_partial = snapshot
                                progress()
                            if not started_message or not stopped_message:
                                raise ProviderProtocolError("Anthropic stream missing message_stop")
                            raw = await stream.get_final_message()
                            if len(raw.content) != next_index:
                                raise ProviderProtocolError("Stream block inventory mismatch")
                    else:
                        raw = await client.messages.create(**request)
            except TimeoutError:
                raise httpx.ReadTimeout("Anthropic provider read deadline exceeded") from None
            turn = normalize_anthropic_message(raw, endpoint=binding.base_url,
                                               fallback_model=binding.model_slug)
            input_tokens += turn.usage.input_tokens or 0
            output_tokens += turn.usage.output_tokens or 0
            stream_partial = None
            turn_metrics = _provider_turn_metrics(turn)
            turn_metrics.update(cache_read_input_tokens=turn.usage.cache_read_input_tokens,
                                cache_creation_input_tokens=turn.usage.cache_creation_input_tokens,
                                stream_tool_inputs=deepcopy(stream_raw_inputs))
            turns.append(turn_metrics)
            thinking.append(turn.reasoning)
            history.append({"role": "assistant", "content": list(turn.native_content_blocks)})
            pending[:] = [{"id": call.id, "name": call.name,
                           "arguments": deepcopy(call.parsed_arguments)}
                          for call in turn.tool_calls]
            messages.append(ChatMessageAssistant(
                content=turn.text, model=turn.model,
                tool_calls=[ToolCall(id=c.id, function=c.name, arguments=c.parsed_arguments)
                            for c in turn.tool_calls] or None,
            ))
            progress()
            protocol = await _execute_mcp_tool(
                bridge.dispatch_protocol_text,
                {"text": turn.text, "mixed_tools": bool(turn.tool_calls),
                 "enabled": not finalize and executed + len(bridge.protocol_calls) < max_steps
                 and turn.status is ProviderTurnStatus.COMPLETED},
                timeout_seconds=tool_timeout_seconds,
            )
            if protocol is not None:
                text, accepted = protocol
                if not accepted:
                    return response(turn.text), messages
                history.append({"role": "user", "content": text})
                messages.append(ChatMessageUser(content=text))
                progress()
                continue
            if turn.status is not ProviderTurnStatus.TOOL_CALLS:
                metrics = turn_metrics
                final_text = _direct_text_projection(turn, metrics)
                error = ("PROVIDER_CAPABILITY: unsupported Anthropic continuation"
                         if turn.status is ProviderTurnStatus.CONTINUATION_REQUIRED else None)
                return response(final_text, error), messages
            if finalize or any(c.id in seen_ids or c.name not in runners for c in turn.tool_calls):
                raise ProviderProtocolError("Inadmissible Anthropic native tool request")
            if executed + len(bridge.protocol_calls) + len(turn.tool_calls) > max_steps:
                metrics.update(model_output_error=True, model_output_subtype="TOOL_CALL_BUDGET")
                break
            seen_ids.update(c.id for c in turn.tool_calls)
            results = []
            for call in turn.tool_calls:
                tool_error = failure = None
                try:
                    value = await _execute_mcp_tool(
                        runners[call.name], call.parsed_arguments,
                        timeout_seconds=tool_timeout_seconds,
                    )
                    text = _tool_result_to_text(value)
                except asyncio.CancelledError:
                    progress()
                    raise
                except Exception as exc:
                    failure = exc
                    text = str(exc)
                    tool_error = ToolCallError(type="unknown", message=text)
                executed += 1
                bridge.observe(call.name, call.parsed_arguments, text, tool_error)
                actual = bridge.native_tool_calls[-1]["outcome"]
                is_error = tool_error is not None or actual is None or actual.failure is not None
                results.append({"type": "tool_result", "tool_use_id": call.id,
                                "content": text, "is_error": is_error})
                messages.append(ChatMessageTool(content=text, tool_call_id=call.id,
                                                function=call.name, error=tool_error))
                pending.pop(0)
                # Keep partial results even when a later call never completes.
                if len(results) == 1:
                    history.append({"role": "user", "content": results})
                progress()
                if failure is not None and (actual is None or actual.failure in {
                    None, "INFRA_ERROR", "HARNESS_ERROR",
                }):
                    raise failure
        return response(final_text), messages
    finally:
        await attempt.aclose()
