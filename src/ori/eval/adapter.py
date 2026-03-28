"""Model adapter: call LLM providers and extract Cypher from responses."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

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
    cypher: str | None       # None = parse failed
    tokens_input: int
    tokens_output: int
    elapsed_seconds: float
    model: str
    error: str | None = None


def extract_cypher(text: str) -> str | None:
    """Extract Cypher query from model response text."""
    # 1. Fenced code block (```cypher or ```)
    m = re.search(r"```(?:cypher)?\s*\n(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    # 2. First MATCH / OPTIONAL MATCH line through end of text
    m = re.search(r"((?:OPTIONAL\s+)?MATCH\b.*)", text, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return None


async def call_model(
    task: Task,
    model: str,
    base_url: str | None = None,
    max_tokens: int = 1024,
) -> ModelResponse:
    """Call the model and return a structured response.

    Special providers (no API key needed):
      mock/perfect  — returns the task's reference Cypher verbatim (all tasks should score CORRECT)
      mock/empty    — returns empty response (all tasks should score INCORRECT/PARSE_FAIL)
    """
    # Mock providers — useful for smoke-testing the full pipeline without API keys
    if model.startswith("mock/"):
        variant = model.split("/", 1)[1]
        if variant == "perfect":
            cypher = task.reference_cypher
            return ModelResponse(
                raw_text=cypher, cypher=cypher,
                tokens_input=0, tokens_output=0,
                elapsed_seconds=0.0, model=model,
            )
        elif variant == "empty":
            return ModelResponse(
                raw_text="", cypher=None,
                tokens_input=0, tokens_output=0,
                elapsed_seconds=0.0, model=model,
            )
        else:
            return ModelResponse(
                raw_text="", cypher=None,
                tokens_input=0, tokens_output=0,
                elapsed_seconds=0.0, model=model,
                error=f"Unknown mock variant {variant!r}. Use mock/perfect or mock/empty.",
            )

    domain = task.metadata.get("domain", "CORP.LOCAL")
    system = _SYSTEM_PROMPT.format(domain=domain)
    messages = [{"role": "user", "content": task.question}]

    t0 = time.monotonic()
    try:
        text, tokens_in, tokens_out = await _call_provider(
            model=model, messages=messages, system=system,
            max_tokens=max_tokens, base_url=base_url,
        )
        elapsed = time.monotonic() - t0
        cypher = extract_cypher(text)
        return ModelResponse(
            raw_text=text, cypher=cypher,
            tokens_input=tokens_in, tokens_output=tokens_out,
            elapsed_seconds=elapsed, model=model,
        )
    except Exception as exc:
        elapsed = time.monotonic() - t0
        return ModelResponse(
            raw_text="", cypher=None,
            tokens_input=0, tokens_output=0,
            elapsed_seconds=elapsed, model=model,
            error=str(exc),
        )


async def _call_provider(
    model: str,
    messages: list[dict],
    system: str,
    max_tokens: int,
    base_url: str | None,
) -> tuple[str, int, int]:
    """Dispatch to the correct provider SDK. Returns (text, input_tokens, output_tokens)."""
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
        return resp.content[0].text, resp.usage.input_tokens, resp.usage.output_tokens

    elif provider in ("openai", "ollama", "openai-compat", "gemini"):
        import os
        import openai
        resolved_base = {
            "ollama": os.getenv("OLLAMA_BASE_URL", "http://localhost:11434") + "/v1",
            "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
        }.get(provider, base_url)
        api_key = "ollama" if provider == "ollama" else None

        # handle "modelname@http://custom-url" for openai-compat
        if "@" in name and provider == "openai-compat":
            name, resolved_base = name.split("@", 1)

        client = openai.AsyncOpenAI(
            base_url=resolved_base,
            **({"api_key": api_key} if api_key else {}),
        )
        # Inject system prompt as first message for OpenAI-compat providers
        full_messages = [{"role": "system", "content": system}] + messages
        resp = await client.chat.completions.create(
            model=name,
            max_tokens=max_tokens,
            messages=full_messages,
        )
        m = resp.choices[0].message
        usage = resp.usage
        return m.content, usage.prompt_tokens, usage.completion_tokens

    else:
        raise ValueError(
            f"Unknown provider: {provider!r}. "
            "Supported: anthropic, openai, ollama, openai-compat, gemini"
        )
