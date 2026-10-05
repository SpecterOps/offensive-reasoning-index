"""Tests for environment-backed model provider authentication."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import openai

from ori.eval.adapter import call_model
from ori.eval.provider_auth import (
    official_openai_endpoint_is_secure,
    openai_compat_api_key,
    resolve_openai_compat_credential,
)
from ori.eval.tasks import Task


def test_openai_compat_api_key_selects_scoped_keys_and_generic_override_only(
    monkeypatch,
) -> None:
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
    for endpoint in (None, "https://example.test/v1"):
        credential = resolve_openai_compat_credential(endpoint)
        assert credential.api_key == "compat-key"
        assert credential.credential_source == "OPENAI_COMPAT_API_KEY"

    for endpoint, expected_key, expected_source in (
        ("https://openrouter.ai/api/v1", "openrouter-key", "OPENROUTER_API_KEY"),
        ("https://inference-api.nousresearch.com/v1", "nous-key", "NOUS_API_KEY"),
        ("https://api.openai.com/v1", "openai-key", "OPENAI_API_KEY"),
    ):
        credential = resolve_openai_compat_credential(endpoint)
        assert credential.api_key == expected_key
        assert credential.credential_source == expected_source

    monkeypatch.delenv("OPENAI_COMPAT_API_KEY")
    assert (
        openai_compat_api_key("https://inference-api.nousresearch.com/v1") == "nous-key"
    )
    assert openai_compat_api_key("https://openrouter.ai/api/v1") == "openrouter-key"
    assert openai_compat_api_key("https://api.openai.com/v1") == "openai-key"
    assert openai_compat_api_key("https://example.test/nousresearch.com/v1") is None
    assert openai_compat_api_key("https://openrouter.ai.example.test/v1") is None
    assert openai_compat_api_key() is None

    monkeypatch.delenv("NOUS_API_KEY")
    assert (
        openai_compat_api_key("https://inference-api.nousresearch.com/v1")
        == "nous-portal-key"
    )
    monkeypatch.delenv("NOUS_PORTAL_API_KEY")
    assert openai_compat_api_key("https://inference-api.nousresearch.com/v1") is None


def test_explicit_compat_key_does_not_override_scoped_provider_keys(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_COMPAT_API_KEY", "compat-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-key")
    monkeypatch.setenv("NOUS_API_KEY", "nous-key")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")

    assert openai_compat_api_key("https://openrouter.ai/api/v1") == "openrouter-key"
    assert (
        openai_compat_api_key("https://inference-api.nousresearch.com/v1")
        == "nous-key"
    )
    assert openai_compat_api_key("https://api.openai.com/v1") == "openai-key"
    assert openai_compat_api_key("https://generic.example/v1") == "compat-key"


def test_official_openai_key_requires_the_exact_tls_origin(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")

    assert official_openai_endpoint_is_secure("https://api.openai.com/v1") is True
    assert official_openai_endpoint_is_secure("https://api.openai.com:443/v1") is True
    for endpoint in (
        "http://api.openai.com/v1",
        "https://api.openai.com:8443/v1",
        "https://user@api.openai.com/v1",
    ):
        assert official_openai_endpoint_is_secure(endpoint) is False
        credential = resolve_openai_compat_credential(endpoint)
        assert credential.endpoint_family == "openai"
        assert credential.api_key is None
        assert credential.credential_source is None


def test_credential_metadata_is_safe_and_endpoint_scoped(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_COMPAT_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-secret")
    monkeypatch.setenv("NOUS_API_KEY", "nous-secret")
    monkeypatch.setenv("NOUS_PORTAL_API_KEY", "nous-portal-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")

    expected = {
        "https://openrouter.ai/api/v1": ("openrouter", "OPENROUTER_API_KEY"),
        "https://inference-api.nousresearch.com/v1": ("nous", "NOUS_API_KEY"),
        "https://api.openai.com/v1": ("openai", "OPENAI_API_KEY"),
        "https://localhost:8000/v1": ("generic", None),
    }
    for endpoint, (family, source) in expected.items():
        credential = resolve_openai_compat_credential(endpoint)
        assert credential.endpoint_family == family
        assert credential.credential_source == source
        assert "api_key=" not in repr(credential)


def test_no_provider_key_crosses_endpoint_families(monkeypatch) -> None:
    key_names = (
        "OPENAI_COMPAT_API_KEY",
        "OPENROUTER_API_KEY",
        "NOUS_API_KEY",
        "NOUS_PORTAL_API_KEY",
        "OPENAI_API_KEY",
    )
    endpoints = (
        "https://openrouter.ai/api/v1",
        "https://inference-api.nousresearch.com/v1",
        "https://api.openai.com/v1",
        "https://generic.example/v1",
    )
    cases = {
        "OPENROUTER_API_KEY": ("openrouter-key", ("openrouter-key", None, None, None)),
        "NOUS_API_KEY": ("nous-key", (None, "nous-key", None, None)),
        "NOUS_PORTAL_API_KEY": (
            "nous-portal-key",
            (None, "nous-portal-key", None, None),
        ),
        "OPENAI_API_KEY": ("openai-key", (None, None, "openai-key", None)),
    }
    for configured_name, (configured_key, expected_keys) in cases.items():
        for name in key_names:
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv(configured_name, configured_key)
        for endpoint, expected_key in zip(endpoints, expected_keys, strict=True):
            assert openai_compat_api_key(endpoint) == expected_key


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
