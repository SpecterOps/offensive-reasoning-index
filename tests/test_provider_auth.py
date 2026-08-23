"""Tests for environment-backed model provider authentication."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import openai

from ori.eval.adapter import call_model
from ori.eval.provider_auth import openai_compat_api_key
from ori.eval.tasks import Task


def test_openai_compat_api_key_prefers_compat_then_openrouter_then_openai(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_COMPAT_API_KEY", "compat-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-key")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    assert openai_compat_api_key() == "compat-key"

    monkeypatch.delenv("OPENAI_COMPAT_API_KEY")
    assert openai_compat_api_key() == "openrouter-key"

    monkeypatch.delenv("OPENROUTER_API_KEY")
    assert openai_compat_api_key() == "openai-key"


def test_direct_openai_compat_uses_openrouter_api_key(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeCompletions:
        async def create(self, **kwargs):
            captured["request"] = kwargs
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="MATCH (u) RETURN u"))],
                usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2),
            )

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs) -> None:
            captured["client"] = kwargs
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(openai, "AsyncOpenAI", FakeAsyncOpenAI)
    monkeypatch.delenv("OPENAI_COMPAT_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-key")

    task = Task(
        id="auth-01",
        template_id="auth",
        tier=1,
        category="cypher_generation",
        question="Find the user.",
        reference_cypher="MATCH (u) RETURN u",
        grade_mode="node_set",
        metadata={"domain": "TEST.LOCAL"},
    )
    response = asyncio.run(
        call_model(
            task,
            "openai-compat/qwen/qwen3-32b",
            base_url="https://openrouter.ai/api/v1",
        )
    )

    assert response.error is None
    assert response.cypher == "MATCH (u) RETURN u"
    assert captured["client"] == {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": "openrouter-key",
    }
