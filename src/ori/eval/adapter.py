"""Model adapter: call LLM providers and extract Cypher from responses."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

from .provider_auth import (
    official_openai_endpoint_is_secure,
    openai_compat_endpoint_is_local,
    resolve_openai_compat_credential,
    sanitized_provider_endpoint,
)
from .provider_contract import (
    ProviderApiSurface,
    ProviderAuthenticationError,
    ProviderCapabilityError,
    ProviderContractError,
    ProviderRequest,
    ProviderTurn,
    ProviderTurnStatus,
    chat_completions_payload,
    normalize_chat_completion,
    resolve_api_surface,
    validate_release1_api_surface,
)
from .tasks import Task

# BH CE Cypher constraints injected into system prompt
_SYSTEM_PROMPT = """\
You are analyzing an Active Directory environment using BloodHound CE.
Domain: {domain}

Write a single Cypher query to answer the question.
Return ONLY the Cypher query — no explanation, no markdown fences, no commentary.

Rules:
- Use RETURN p for path queries (not RETURN n, m separately — edges won't render)
- Do NOT use: UNWIND, CASE expressions, list comprehensions [x IN y | ...], labels(), XOR
- shortestPath requires a single variable-length pattern: (a)-[*1..]->(b)
- String ops: STARTS WITH, CONTAINS, ENDS WITH, =~ are supported
- count() works but use RETURN node for set queries
"""


@dataclass
class ModelResponse:
    raw_text: str
    cypher: str | None  # None = parse failed
    parse_stage: str
    tokens_input: int
    tokens_output: int
    elapsed_seconds: float
    model: str
    thinking: str = ""
    error: str | None = None
    provider_metrics: dict[str, object] = field(default_factory=dict)


def extract_cypher_details(text: str) -> tuple[str | None, str]:
    """Extract Cypher query from model response text and record parse stage.

    Handles thinking models (Qwen3, DeepSeek-R1) that wrap reasoning in
    <think>...</think> blocks before the actual answer.
    """
    # Strip thinking-model reasoning blocks before extraction
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()

    # 1. Fenced code block (```cypher or ```)
    m = re.search(r"```(?:cypher)?\s*\n(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip(), "fenced_code"
    # 2. First MATCH / OPTIONAL MATCH line through end of text
    m = re.search(r"((?:OPTIONAL\s+)?MATCH\b.*)", text, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip(), "bare_match"
    return None, "none"


def extract_cypher(text: str) -> str | None:
    """Extract Cypher query from model response text."""
    cypher, _stage = extract_cypher_details(text)
    return cypher


async def call_model(
    task: Task,
    model: str,
    base_url: str | None = None,
    max_tokens: int = 1024,
    ollama_options: dict | None = None,
) -> ModelResponse:
    """Call the model and return a structured response.

    Special providers (no API key needed):
      mock/perfect       — reference Cypher verbatim → all CORRECT
      mock/empty         — no Cypher returned → all PARSE_FAIL
      mock/hallucinate   — Cypher referencing invented node names → all HALLUCINATION
      mock/wrong         — valid Cypher returning unrelated nodes → all INCORRECT
      mock/syntax_error  — malformed Cypher → all CYPHER_ERROR
    """
    # Mock providers — smoke-test all outcome code paths without API keys
    if model.startswith("mock/"):
        variant = model.split("/", 1)[1]
        domain = task.metadata.get("domain", "CORP.LOCAL")

        if variant == "perfect":
            # Returns reference Cypher verbatim — should score CORRECT on every task
            cypher = task.reference_cypher
            return ModelResponse(
                raw_text=cypher,
                cypher=cypher,
                parse_stage="reference",
                tokens_input=0,
                tokens_output=0,
                elapsed_seconds=0.0,
                model=model,
            )

        elif variant == "empty":
            # Returns no Cypher — should score PARSE_FAIL on every task
            return ModelResponse(
                raw_text="I cannot answer this question.",
                cypher=None,
                parse_stage="none",
                tokens_input=0,
                tokens_output=0,
                elapsed_seconds=0.0,
                model=model,
            )

        elif variant == "hallucinate":
            # Returns Cypher with invented node name — should score HALLUCINATION on every task.
            # FAKE_SENTINEL_NODE will not exist in any generated graph.
            cypher = (
                f"MATCH (u:User {{name: 'FAKE_SENTINEL_NODE@{domain}'}})"
                f"-[:MemberOf]->(g:Group) RETURN u, g"
            )
            raw = f"The attacker FAKE_SENTINEL_NODE@{domain} is a member of Domain Admins."
            return ModelResponse(
                raw_text=raw,
                cypher=cypher,
                parse_stage="mock_hallucinate",
                tokens_input=0,
                tokens_output=0,
                elapsed_seconds=0.0,
                model=model,
            )

        elif variant == "wrong":
            # Returns valid Cypher that executes but returns wrong nodes — should score INCORRECT.
            # Returns all GPO objects, which won't overlap with any planted path nodes.
            cypher = "MATCH (g:GPO) RETURN g LIMIT 1"
            return ModelResponse(
                raw_text=cypher,
                cypher=cypher,
                parse_stage="mock_wrong",
                tokens_input=0,
                tokens_output=0,
                elapsed_seconds=0.0,
                model=model,
            )

        elif variant == "syntax_error":
            # Returns malformed Cypher — should score CYPHER_ERROR on every task.
            cypher = "MATCH (u:User WHERE RETURN u"
            return ModelResponse(
                raw_text=cypher,
                cypher=cypher,
                parse_stage="mock_syntax_error",
                tokens_input=0,
                tokens_output=0,
                elapsed_seconds=0.0,
                model=model,
            )

        else:
            return ModelResponse(
                raw_text="",
                cypher=None,
                parse_stage="none",
                tokens_input=0,
                tokens_output=0,
                elapsed_seconds=0.0,
                model=model,
                error=(
                    f"Unknown mock variant {variant!r}. "
                    "Use: mock/perfect, mock/empty, mock/hallucinate, mock/wrong, mock/syntax_error"
                ),
            )

    domain = task.metadata.get("domain", "CORP.LOCAL")
    system = _SYSTEM_PROMPT.format(domain=domain)
    messages = [{"role": "user", "content": task.question}]

    t0 = time.monotonic()
    try:
        response = await call_provider_text(
            model=model,
            messages=messages,
            system=system,
            max_tokens=max_tokens,
            base_url=base_url,
            ollama_options=ollama_options,
        )
        cypher, parse_stage = extract_cypher_details(response.raw_text)
        return ModelResponse(
            raw_text=response.raw_text,
            cypher=cypher,
            parse_stage=parse_stage,
            tokens_input=response.tokens_input,
            tokens_output=response.tokens_output,
            elapsed_seconds=response.elapsed_seconds,
            model=response.model,
            thinking=response.thinking,
            error=response.error,
            provider_metrics=response.provider_metrics,
        )
    except Exception as exc:
        elapsed = time.monotonic() - t0
        return ModelResponse(
            raw_text="",
            cypher=None,
            parse_stage="none",
            tokens_input=0,
            tokens_output=0,
            elapsed_seconds=elapsed,
            model=model,
            error=str(exc),
        )


async def call_provider_text(
    *,
    model: str,
    messages: list[dict],
    system: str,
    base_url: str | None = None,
    max_tokens: int = 1024,
    ollama_options: dict | None = None,
    api_surface: ProviderApiSurface | str = ProviderApiSurface.AUTO,
    request_timeout_seconds: float | None = None,
) -> ModelResponse:
    """Call a provider without imposing a legacy task or Cypher parse contract.

    Protocol-v2 runtimes use this transport boundary so solver requests contain
    only their public prompt envelope. Provider failures remain explicit in the
    returned response and are classified by the owning runtime.
    """

    started = time.monotonic()
    requested_surface = ProviderApiSurface(api_surface)
    provider = model.split("/", 1)[0]
    resolved_surface = resolve_api_surface(provider, requested_surface)
    try:
        validate_release1_api_surface(provider, resolved_surface)
        text, tokens_in, tokens_out, thinking, provider_metrics = await _call_provider(
            model=model,
            messages=messages,
            system=system,
            max_tokens=max_tokens,
            base_url=base_url,
            ollama_options=ollama_options,
            api_surface=resolved_surface,
            request_timeout_seconds=request_timeout_seconds,
        )
        provider_metrics = {
            **provider_metrics,
            "requested_api_surface": requested_surface.value,
            "resolved_api_surface": resolved_surface.value,
        }
        return ModelResponse(
            raw_text=text if isinstance(text, str) else "",
            cypher=None,
            parse_stage="raw_text",
            tokens_input=tokens_in,
            tokens_output=tokens_out,
            elapsed_seconds=time.monotonic() - started,
            model=model,
            thinking=thinking,
            provider_metrics=provider_metrics,
        )
    except ProviderContractError as exc:
        return ModelResponse(
            raw_text="",
            cypher=None,
            parse_stage="provider_contract_error",
            tokens_input=0,
            tokens_output=0,
            elapsed_seconds=time.monotonic() - started,
            model=model,
            error=str(exc),
            provider_metrics={
                "requested_api_surface": requested_surface.value,
                "resolved_api_surface": resolved_surface.value,
                "infra_scope": "provider",
                "infra_error_subtype": exc.code,
                "infra_retryable": exc.retryable,
            },
        )
    except Exception as exc:
        provider_error_metrics = _provider_exception_metrics(exc)
        return ModelResponse(
            raw_text="",
            cypher=None,
            parse_stage="none",
            tokens_input=0,
            tokens_output=0,
            elapsed_seconds=time.monotonic() - started,
            model=model,
            error=str(exc),
            provider_metrics={
                "requested_api_surface": requested_surface.value,
                "resolved_api_surface": resolved_surface.value,
                **provider_error_metrics,
            },
        )


async def _call_provider(
    model: str,
    messages: list[dict],
    system: str,
    max_tokens: int,
    base_url: str | None,
    ollama_options: dict | None = None,
    api_surface: ProviderApiSurface = ProviderApiSurface.CHAT_COMPLETIONS,
    request_timeout_seconds: float | None = None,
) -> tuple[str, int, int, str, dict[str, object]]:
    """Dispatch to the correct provider SDK.

    Returns (final_text, input_tokens, output_tokens, thinking_text, provider_metrics).
    """
    provider, name = model.split("/", 1)

    if provider == "anthropic":
        import anthropic

        client = anthropic.AsyncAnthropic()
        resp = await client.messages.create(
            model=name,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
        )
        return resp.content[0].text, resp.usage.input_tokens, resp.usage.output_tokens, "", {}

    elif provider == "ollama":
        import os

        import httpx

        resolved_base = (base_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")).rstrip(
            "/"
        )
        if resolved_base.endswith("/v1"):
            resolved_base = resolved_base[:-3].rstrip("/")
        url = f"{resolved_base}/api/chat"

        full_messages = [{"role": "system", "content": system}] + messages
        options = dict(ollama_options or {})
        payload: dict[str, object] = {
            "model": name,
            "messages": full_messages,
            "stream": True,
        }
        if options:
            payload["options"] = options

        content_parts: list[str] = []
        thinking_parts: list[str] = []
        prompt_eval_count = 0
        eval_count = 0
        done_metrics: dict[str, object] = {}

        timeout = httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=30.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", url, json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    message = data.get("message") or {}
                    thinking = message.get("thinking")
                    if isinstance(thinking, str) and thinking:
                        thinking_parts.append(thinking)
                    content = message.get("content")
                    if isinstance(content, str) and content:
                        content_parts.append(content)
                    if data.get("done"):
                        prompt_eval_count = int(
                            data.get("prompt_eval_count") or prompt_eval_count or 0
                        )
                        eval_count = int(data.get("eval_count") or eval_count or 0)
                        done_metrics = {
                            "provider": "ollama_native_chat",
                            "prompt_eval_count": prompt_eval_count,
                            "eval_count": eval_count,
                            "total_duration_ns": int(data.get("total_duration") or 0),
                            "load_duration_ns": int(data.get("load_duration") or 0),
                            "prompt_eval_duration_ns": int(data.get("prompt_eval_duration") or 0),
                            "eval_duration_ns": int(data.get("eval_duration") or 0),
                        }

        return (
            "".join(content_parts),
            prompt_eval_count,
            eval_count,
            "".join(thinking_parts),
            done_metrics,
        )

    elif provider in ("openai", "openai-compat", "gemini"):
        import os

        import openai

        resolved_base = {
            "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
        }.get(provider, base_url)
        # handle "modelname@http://custom-url" for openai-compat
        if "@" in name and provider == "openai-compat":
            name, resolved_base = name.split("@", 1)

        client_kwargs = {"base_url": resolved_base}
        credential = None
        if provider == "openai-compat":
            if not resolved_base:
                raise ProviderCapabilityError(
                    "provider='openai-compat' requires an explicit model_base_url "
                    "or model@URL endpoint"
                )
            credential = resolve_openai_compat_credential(resolved_base)
            # Supplying an explicit placeholder prevents the OpenAI SDK from
            # silently borrowing OPENAI_API_KEY for an unrelated compatible
            # endpoint. Authenticated provider families are rejected when their
            # scoped credential is absent; generic/local servers may ignore the
            # placeholder.
            if credential.api_key is None and not openai_compat_endpoint_is_local(
                resolved_base
            ):
                raise ProviderAuthenticationError(
                    f"OpenAI-compatible {credential.endpoint_family} endpoint "
                    "requires its scoped API credential"
                )
            client_kwargs["api_key"] = credential.api_key or "not-needed"
        elif provider == "openai":
            official_base = resolved_base or "https://api.openai.com/v1"
            if not official_openai_endpoint_is_secure(official_base):
                raise ProviderCapabilityError(
                    "provider='openai' requires the official HTTPS api.openai.com origin; "
                    "use provider='openai-compat' for custom endpoints"
                )
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise ProviderAuthenticationError(
                    "Official OpenAI requires OPENAI_API_KEY"
                )
            client_kwargs["base_url"] = official_base
            client_kwargs["api_key"] = api_key
            resolved_base = official_base
        else:
            api_key = os.getenv("GEMINI_API_KEY")
            if not api_key:
                raise ProviderAuthenticationError("Gemini requires GEMINI_API_KEY")
            client_kwargs["api_key"] = api_key
        if request_timeout_seconds is not None:
            client_kwargs["timeout"] = request_timeout_seconds
        client = openai.AsyncOpenAI(**client_kwargs)
        # Inject system prompt as first message for OpenAI-compat providers
        full_messages = [{"role": "system", "content": system}] + messages
        request = ProviderRequest(
            messages=tuple(full_messages),
            api_surface=ProviderApiSurface.CHAT_COMPLETIONS,
            output_limit=max_tokens,
        )
        resp = await client.chat.completions.create(
            **chat_completions_payload(request, model=name)
        )
        turn = normalize_chat_completion(
            resp,
            provider=provider,
            endpoint=resolved_base or "https://api.openai.com/v1",
            fallback_model=name,
        )
        metrics = _provider_turn_metrics(turn)
        if credential is not None:
            metrics.update(
                {
                    "endpoint_family": credential.endpoint_family,
                    "credential_source": credential.credential_source,
                }
            )
        elif provider == "openai":
            metrics.update(
                {
                    "endpoint_family": "openai",
                    "credential_source": "OPENAI_API_KEY",
                }
            )
        elif provider == "gemini":
            metrics.update(
                {
                    "endpoint_family": "gemini",
                    "credential_source": "GEMINI_API_KEY",
                }
            )
        return (
            _direct_text_projection(turn, metrics),
            turn.usage.input_tokens or 0,
            turn.usage.output_tokens or 0,
            turn.reasoning,
            metrics,
        )

    elif provider == "codex":
        import openai

        from .codex_oauth import (
            chat_request_to_codex_responses_params,
            codex_headers,
            codex_model_name,
            codex_request_base_url,
            codex_responses_events_to_chat_completion,
        )

        resolved_base = codex_request_base_url(model, base_url)
        resolved_model = codex_model_name(model)
        reasoning_effort = (ollama_options or {}).get("reasoning_effort")
        full_messages = [{"role": "system", "content": system}] + messages
        body: dict[str, object] = {
            "model": resolved_model,
            "messages": full_messages,
            "max_tokens": max_tokens,
        }
        if reasoning_effort is not None:
            body["reasoning_effort"] = reasoning_effort
        params = chat_request_to_codex_responses_params(body)
        thread_id = str(params.get("prompt_cache_key") or "")
        headers = codex_headers(thread_id=thread_id)
        client_kwargs = {
            "api_key": headers["Authorization"].removeprefix("Bearer "),
            "base_url": resolved_base,
        }
        if request_timeout_seconds is not None:
            client_kwargs["timeout"] = request_timeout_seconds
        client = openai.AsyncOpenAI(**client_kwargs)
        try:
            events = await client.responses.create(**params, stream=True, extra_headers=headers)
            data = codex_responses_events_to_chat_completion(
                [event async for event in events], resolved_model
            )
        finally:
            await client.close()
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        usage = data.get("usage") or {}
        return (
            message.get("content") or "",
            int(usage.get("prompt_tokens") or 0),
            int(usage.get("completion_tokens") or 0),
            "",
            {
                "provider": "codex_oauth",
                "response_id": data.get("id", ""),
                "reasoning_effort": reasoning_effort or "native_default",
                "provider_turn_status": "completed",
                "finish_reason": choice.get("finish_reason") or "",
            },
        )

    else:
        raise ValueError(
            f"Unknown provider: {provider!r}. "
            "Supported: anthropic, openai, ollama, openai-compat, gemini, codex"
        )


def _provider_turn_metrics(turn: ProviderTurn) -> dict[str, object]:
    """Private, secret-free metadata retained with a normalized provider turn."""

    return {
        "provider": turn.provider,
        "provider_model": turn.model,
        "provider_endpoint": sanitized_provider_endpoint(turn.endpoint),
        "provider_turn_status": turn.status.value,
        "finish_reason": turn.finish_reason,
        "response_id": turn.response_id,
        "refusal": turn.refusal,
        "reasoning": turn.reasoning,
        "tool_call_count": len(turn.tool_calls),
        "tool_argument_parse_statuses": [
            call.argument_parse_status.value for call in turn.tool_calls
        ],
        "usage_reported": turn.usage.usage_reported,
        "usage_complete": turn.usage.usage_complete,
        "usage": {
            "input_tokens": turn.usage.input_tokens,
            "output_tokens": turn.usage.output_tokens,
            "total_tokens": turn.usage.total_tokens,
        },
        "api_surface": turn.api_surface.value,
    }


def _provider_exception_metrics(exc: Exception) -> dict[str, object]:
    """Classify SDK/HTTP failures without parsing provider error strings."""

    import httpx

    subtype = "PROVIDER_ERROR"
    retryable = True
    status = getattr(exc, "status_code", None)
    if isinstance(exc, httpx.TimeoutException) or type(exc).__name__ == "APITimeoutError":
        subtype = "PROVIDER_TIMEOUT"
    elif isinstance(exc, httpx.RequestError) or type(exc).__name__ == "APIConnectionError":
        subtype = "PROVIDER_TRANSPORT"
    elif type(exc).__name__ in {"AuthenticationError", "PermissionDeniedError"} or status in {
        401,
        403,
    }:
        subtype = "PROVIDER_AUTH"
        retryable = False
    elif type(exc).__name__ == "RateLimitError" or status == 429:
        subtype = "PROVIDER_RATE_LIMIT"
    elif status == 408:
        subtype = "PROVIDER_TIMEOUT"
    elif type(exc).__name__ == "InternalServerError" or (
        isinstance(status, int) and status >= 500
    ):
        subtype = "PROVIDER_SERVER"
    elif isinstance(status, int) and 400 <= status < 500:
        subtype = "PROVIDER_REQUEST"
        retryable = False
    return {
        "infra_scope": "provider",
        "infra_error_subtype": subtype,
        "infra_retryable": retryable,
    }


def _direct_text_projection(turn: ProviderTurn, metrics: dict[str, object]) -> str:
    """Project direct final text while retaining typed model-output failures.

    Direct inference cannot consume tool calls or incomplete terminal states.
    Returning an empty string lets the existing V2 JSON boundary classify these
    as OUTPUT_INVALID without mislabeling them as provider infrastructure.
    """

    failure_subtypes = {
        ProviderTurnStatus.TOOL_CALLS: "TOOL_CALL_ONLY",
        ProviderTurnStatus.REFUSED: "REFUSAL",
        ProviderTurnStatus.REASONING_ONLY: "REASONING_ONLY",
        ProviderTurnStatus.TRUNCATED: "TRUNCATED",
        ProviderTurnStatus.CONTENT_FILTERED: "CONTENT_FILTERED",
        ProviderTurnStatus.EMPTY: "EMPTY_OUTPUT",
    }
    subtype = failure_subtypes.get(turn.status)
    if subtype is None:
        return turn.text
    metrics["model_output_error"] = True
    metrics["model_output_subtype"] = subtype
    return ""
