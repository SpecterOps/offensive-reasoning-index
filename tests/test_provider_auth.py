"""Tests for environment-backed model provider authentication."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import openai

from ori.eval.adapter import call_model
from ori.eval.provider_auth import openai_compat_api_key
from ori.eval.tasks import Task


def test_openai_compat_api_key_selects_explicit_and_provider_specific_keys(monkeypatch) -> None:
    for name in (
        "OPENAI_COMPAT_API_KEY",
        "NOUS_API_KEY",
        "NOUS_PORTAL_API_KEY",
        "OPENROUTER_API_KEY",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    monkeypatch.setenv("OPENAI_COMPAT_API_KEY", "compat-key")
    monkeypatch.setenv("NOUS_API_KEY", "nous-key")
    monkeypatch.setenv("NOUS_PORTAL_API_KEY", "nous-portal-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-key")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    assert openai_compat_api_key() == "compat-key"
    assert (
        openai_compat_api_key("https://inference-api.nousresearch.com/v1") == "compat-key"
    )

    monkeypatch.delenv("OPENAI_COMPAT_API_KEY")
    assert openai_compat_api_key() == "openrouter-key"
    assert (
        openai_compat_api_key("https://inference-api.nousresearch.com/v1") == "nous-key"
    )
    assert openai_compat_api_key("https://openrouter.ai/api/v1") == "openrouter-key"
    assert openai_compat_api_key("https://example.test/nousresearch.com/v1") == "openrouter-key"

    monkeypatch.delenv("OPENROUTER_API_KEY")
    assert openai_compat_api_key() == "nous-key"
    monkeypatch.delenv("NOUS_API_KEY")
    assert openai_compat_api_key() == "nous-portal-key"
    monkeypatch.delenv("NOUS_PORTAL_API_KEY")
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


def test_direct_openai_compat_uses_nous_api_key(monkeypatch) -> None:
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
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("NOUS_API_KEY", "nous-key")

    task = Task(
        id="auth-nous-01",
        template_id="auth-nous",
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
            "openai-compat/openai/gpt-5.5",
            base_url="https://inference-api.nousresearch.com/v1",
        )
    )

    assert response.error is None
    assert response.cypher == "MATCH (u) RETURN u"
    assert captured["client"] == {
        "base_url": "https://inference-api.nousresearch.com/v1",
        "api_key": "nous-key",
    }
