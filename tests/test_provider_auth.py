"""Tests for environment-backed model provider authentication."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import openai

from ori.eval.adapter import call_model
from ori.eval.provider_auth import (
    official_openai_endpoint_is_secure,
    openai_compat_api_key,
    resolve_openai_compat_credential,
)
from ori.eval.tasks import Task
from tests.support.provider_origins import (
    ALIAS_ORIGINS,
    CLEARED_ENVIRONMENT,
    COMPATIBILITY_ORIGINS,
    DENIED_ORIGINS,
    SYNTHETIC_KEYS,
    VALID_ORIGINS,
)


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
        "http_client": captured["client"]["http_client"],
    }
    assert captured["client"]["http_client"].follow_redirects is False
    assert captured["client"]["http_client"].is_closed


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
        "http_client": captured["client"]["http_client"],
    }
    assert captured["client"]["http_client"].follow_redirects is False
    assert captured["client"]["http_client"].is_closed


def test_scoped_origin_resolver_acceptance(monkeypatch, subtests) -> None:
    assert len(VALID_ORIGINS) == 12 and len(ALIAS_ORIGINS) == 2
    for case, endpoint, family, source, key in VALID_ORIGINS + ALIAS_ORIGINS:
        with subtests.test(msg=case), monkeypatch.context() as scoped:
            for variable in CLEARED_ENVIRONMENT:
                scoped.delenv(variable, raising=False)
            for variable, value in SYNTHETIC_KEYS:
                scoped.setenv(variable, value)
            if case.endswith("-alias"):
                scoped.delenv("NOUS_API_KEY")
            def forbidden_client(*args, **kwargs):
                raise AssertionError("resolver must not construct a provider client")
            scoped.setattr(openai, "AsyncOpenAI", forbidden_client)
            scoped.setattr(httpx, "AsyncClient", forbidden_client)
            credential = resolve_openai_compat_credential(endpoint)
            assert credential.endpoint_family == family
            assert credential.credential_source == source
            assert credential.api_key == key
            assert openai_compat_api_key(endpoint) == key
            for _, secret in SYNTHETIC_KEYS:
                assert secret not in repr(credential)


def test_scoped_origin_resolver_denial(monkeypatch, subtests) -> None:
    assert len(DENIED_ORIGINS) == 32
    for case, endpoint, family, _, _ in DENIED_ORIGINS:
        with subtests.test(msg=case), monkeypatch.context() as scoped:
            for variable in CLEARED_ENVIRONMENT:
                scoped.delenv(variable, raising=False)
            for variable, value in SYNTHETIC_KEYS:
                scoped.setenv(variable, value)
            def forbidden_client(*args, **kwargs):
                raise AssertionError("resolver must not construct a provider client")
            scoped.setattr(openai, "AsyncOpenAI", forbidden_client)
            scoped.setattr(httpx, "AsyncClient", forbidden_client)
            credential = resolve_openai_compat_credential(endpoint)
            assert credential.endpoint_family == family
            assert credential.api_key is None
            assert credential.credential_source is None
            assert openai_compat_api_key(endpoint) is None
            for _, secret in SYNTHETIC_KEYS:
                assert secret not in repr(credential)


def test_scoped_origin_compatibility(monkeypatch, subtests) -> None:
    assert len(COMPATIBILITY_ORIGINS) == 8
    for index, (endpoint, family, key) in enumerate(COMPATIBILITY_ORIGINS):
        with subtests.test(msg=f"C{index}"), monkeypatch.context() as scoped:
            for variable in CLEARED_ENVIRONMENT:
                scoped.delenv(variable, raising=False)
            for variable, value in SYNTHETIC_KEYS:
                scoped.setenv(variable, value)
            def forbidden_client(*args, **kwargs):
                raise AssertionError("resolver must not construct a provider client")
            scoped.setattr(openai, "AsyncOpenAI", forbidden_client)
            scoped.setattr(httpx, "AsyncClient", forbidden_client)
            credential = resolve_openai_compat_credential(endpoint)
            assert credential.endpoint_family == family
            assert credential.api_key == key
            assert credential.credential_source == (
                "OPENAI_COMPAT_API_KEY" if family == "generic"
                else "OPENAI_API_KEY" if key is not None else None
            )
            assert openai_compat_api_key(endpoint) == key
            scoped.delenv("OPENAI_COMPAT_API_KEY")
            assert openai_compat_api_key(endpoint) == (None if family == "generic" else key)
            if family == "openai":
                assert official_openai_endpoint_is_secure(endpoint) is (key is not None)
