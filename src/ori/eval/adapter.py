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
    parse_stage: str
    tokens_input: int
    tokens_output: int
    elapsed_seconds: float
    model: str
    error: str | None = None


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
                raw_text=cypher, cypher=cypher, parse_stage="reference",
                tokens_input=0, tokens_output=0,
                elapsed_seconds=0.0, model=model,
            )

        elif variant == "empty":
            # Returns no Cypher — should score PARSE_FAIL on every task
            return ModelResponse(
                raw_text="I cannot answer this question.",
                cypher=None, parse_stage="none",
                tokens_input=0, tokens_output=0,
                elapsed_seconds=0.0, model=model,
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
                raw_text=raw, cypher=cypher, parse_stage="mock_hallucinate",
                tokens_input=0, tokens_output=0,
                elapsed_seconds=0.0, model=model,
            )

        elif variant == "wrong":
            # Returns valid Cypher that executes but returns wrong nodes — should score INCORRECT.
            # Returns all GPO objects, which won't overlap with any planted path nodes.
            cypher = "MATCH (g:GPO) RETURN g"
            return ModelResponse(
                raw_text=cypher, cypher=cypher, parse_stage="mock_wrong",
                tokens_input=0, tokens_output=0,
                elapsed_seconds=0.0, model=model,
            )

        elif variant == "syntax_error":
            # Returns malformed Cypher — should score CYPHER_ERROR on every task.
            cypher = "MATCH (u:User WHERE RETURN u"
            return ModelResponse(
                raw_text=cypher, cypher=cypher, parse_stage="mock_syntax_error",
                tokens_input=0, tokens_output=0,
                elapsed_seconds=0.0, model=model,
            )

        else:
            return ModelResponse(
                raw_text="", cypher=None, parse_stage="none",
                tokens_input=0, tokens_output=0,
                elapsed_seconds=0.0, model=model,
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
        text, tokens_in, tokens_out = await _call_provider(
            model=model, messages=messages, system=system,
            max_tokens=max_tokens, base_url=base_url,
            ollama_options=ollama_options,
        )
        elapsed = time.monotonic() - t0
        cypher, parse_stage = extract_cypher_details(text)
        return ModelResponse(
            raw_text=text, cypher=cypher, parse_stage=parse_stage,
            tokens_input=tokens_in, tokens_output=tokens_out,
            elapsed_seconds=elapsed, model=model,
        )
    except Exception as exc:
        elapsed = time.monotonic() - t0
        return ModelResponse(
            raw_text="", cypher=None, parse_stage="none",
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
    ollama_options: dict | None = None,
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
        # ollama_options (e.g. {"num_ctx": 4096}) are passed via extra_body —
        # Ollama's OpenAI-compatible API accepts them; standard OpenAI ignores them.
        kwargs = {}
        if ollama_options and provider == "ollama":
            kwargs["extra_body"] = {"options": ollama_options}
        resp = await client.chat.completions.create(
            model=name,
            max_tokens=max_tokens,
            messages=full_messages,
            **kwargs,
        )
        m = resp.choices[0].message
        usage = resp.usage
        return m.content, usage.prompt_tokens, usage.completion_tokens

    else:
        raise ValueError(
            f"Unknown provider: {provider!r}. "
            "Supported: anthropic, openai, ollama, openai-compat, gemini"
        )
