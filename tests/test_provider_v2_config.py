"""Focused V2 provider-surface, readiness, and provenance identity tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from ori.eval.provider_contract import ProviderApiSurface
from ori.eval.v2.campaign_config import (
    ResolvedV2CampaignConfig,
    V2CampaignConfig,
    load_v2_campaign_config,
)
from ori.eval.v2.campaign_runner import (
    _RUNNER_IMPLEMENTATION_SOURCES,
    V2CampaignRunError,
    _model_readiness,
    _provider_identity,
)


def _config(
    *,
    provider: str,
    api_surface: str = "auto",
    model_base_url: str | None = None,
    modes: tuple[str, ...] = ("direct",),
) -> V2CampaignConfig:
    model: dict[str, object] = {
        "name": "test-model",
        "provider": provider,
        "model": "provider/model",
        "api_surface": api_surface,
    }
    if model_base_url is not None:
        model["model_base_url"] = model_base_url
    return V2CampaignConfig.model_validate(
        {
            "version": 2,
            "source": {"manifest": "manifest.json", "archive": "archive.zip"},
            "tracks": {
                mode: {
                    "public": f"{mode}-public.json",
                    "oracles": f"{mode}-oracles.private.json",
                    "candidates": f"{mode}-candidates.json",
                    "live_certification": f"{mode}-live.private.json",
                }
                for mode in modes
            },
            "modes": list(modes),
            "output_dir": "results/provider-test",
            "defaults": {"mcp": {"mcp_dir": "."}},
            "models": [model],
        }
    )


def _resolved(config: V2CampaignConfig) -> ResolvedV2CampaignConfig:
    return ResolvedV2CampaignConfig(
        source_manifest=Path("manifest.json"),
        archive=Path("archive.zip"),
        tracks={},
        output_dir=Path("results/provider-test"),
        mcp_dir=Path("."),
        config=config,
        source_config_fingerprint="f" * 64,
    )


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        ("openai", ProviderApiSurface.CHAT_COMPLETIONS),
        ("openai-compat", ProviderApiSurface.CHAT_COMPLETIONS),
        ("ollama", ProviderApiSurface.CHAT_COMPLETIONS),
        ("gemini", ProviderApiSurface.CHAT_COMPLETIONS),
        ("codex", ProviderApiSurface.RESPONSES),
    ],
)
def test_v2_auto_surface_preserves_release1_behavior(
    provider: str,
    expected: ProviderApiSurface,
) -> None:
    config = _config(provider=provider)
    identity = _provider_identity(config.models[0], _resolved(config))

    assert identity.requested_api_surface is ProviderApiSurface.AUTO
    assert identity.resolved_api_surface is expected
    assert identity.structured_output_mode == "prompt_local_validation"


@pytest.mark.parametrize(
    ("provider", "surface", "message"),
    [
        ("codex", "chat_completions", "Codex requires Responses"),
        ("openai", "responses", "Responses only for Codex"),
        ("openai-compat", "responses", "Responses only for Codex"),
    ],
)
def test_release1_unsupported_surfaces_fail_before_provider_probe(
    provider: str,
    surface: str,
    message: str,
) -> None:
    config = _config(provider=provider, api_surface=surface)

    with pytest.raises(V2CampaignRunError, match=message):
        _model_readiness(_resolved(config))


def test_api_surface_is_typed_and_cannot_hide_in_options() -> None:
    with pytest.raises(ValidationError, match="api_surface"):
        _config(provider="openai-compat", api_surface="completions")

    payload = _config(provider="openai-compat").model_dump(mode="json")
    payload["models"][0]["options"] = {"api_surface": "responses"}
    with pytest.raises(ValidationError, match="set model api_surface"):
        V2CampaignConfig.model_validate(payload)


@pytest.mark.parametrize(
    ("endpoint", "family", "source"),
    [
        ("https://openrouter.ai/api/v1", "openrouter", "OPENROUTER_API_KEY"),
        ("https://inference-api.nousresearch.com/v1", "nous", "NOUS_API_KEY"),
    ],
)
def test_readiness_records_safe_endpoint_and_credential_identity(
    monkeypatch,
    endpoint: str,
    family: str,
    source: str,
) -> None:
    monkeypatch.delenv("OPENAI_COMPAT_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-secret")
    monkeypatch.setenv("NOUS_API_KEY", "nous-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")
    config = _config(provider="openai-compat", model_base_url=endpoint)

    receipt = _model_readiness(_resolved(config))[0]

    assert receipt.requested_api_surface is ProviderApiSurface.AUTO
    assert receipt.resolved_api_surface is ProviderApiSurface.CHAT_COMPLETIONS
    assert receipt.structured_output_mode == "prompt_local_validation"
    assert receipt.endpoint_family == family
    assert receipt.credential_source == source
    serialized = json.dumps(receipt.model_dump(mode="json"))
    assert "openrouter-secret" not in serialized
    assert "nous-secret" not in serialized
    assert "openai-secret" not in serialized


def test_official_openai_never_accepts_compat_override(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_COMPAT_API_KEY", "compat-secret")
    config = _config(provider="openai")

    with pytest.raises(V2CampaignRunError, match="requires OPENAI_API_KEY"):
        _model_readiness(_resolved(config))


@pytest.mark.parametrize(
    "endpoint",
    (
        "http://api.openai.com/v1",
        "https://api.openai.com:8443/v1",
    ),
)
def test_official_openai_readiness_rejects_untrusted_origins(
    monkeypatch,
    endpoint: str,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "official-openai-key")
    config = _config(provider="openai", model_base_url=endpoint)

    with pytest.raises(V2CampaignRunError, match="official HTTPS"):
        _model_readiness(_resolved(config))


def test_remote_generic_compat_requires_explicit_compat_key(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_COMPAT_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "official-openai-key")
    config = _config(
        provider="openai-compat",
        model_base_url="https://generic.example/v1",
    )

    with pytest.raises(V2CampaignRunError, match="generic endpoint"):
        _model_readiness(_resolved(config))


def test_mcp_provider_gating_does_not_block_direct_only_campaigns() -> None:
    direct = _config(provider="anthropic", modes=("direct",))
    assert direct.modes == ["direct"]

    with pytest.raises(ValidationError, match="native v2 MCP campaigns"):
        _config(provider="anthropic", modes=("mcp",))


def test_direct_only_config_does_not_require_mcp_checkout(tmp_path: Path) -> None:
    for name in (
        "manifest.json",
        "archive.zip",
        "direct-public.json",
        "direct-oracles.private.json",
        "direct-candidates.json",
        "direct-live.private.json",
    ):
        (tmp_path / name).touch()
    config_path = tmp_path / "direct-only.yaml"
    config_path.write_text(
        """\
version: 2
source:
  manifest: manifest.json
  archive: archive.zip
tracks:
  direct:
    public: direct-public.json
    oracles: direct-oracles.private.json
    candidates: direct-candidates.json
    live_certification: direct-live.private.json
modes: [direct]
output_dir: results/direct-only
defaults: {}
models:
  - name: direct-model
    provider: openai-compat
    model: provider/model
    model_base_url: http://127.0.0.1:8080/v1
"""
    )

    resolved = load_v2_campaign_config(config_path)

    assert resolved.config.defaults.mcp is None
    assert resolved.mcp_dir is None


def test_mcp_mode_requires_explicit_mcp_configuration() -> None:
    payload = _config(provider="openai-compat", modes=("mcp",)).model_dump(mode="json")
    payload["defaults"].pop("mcp")

    with pytest.raises(ValidationError, match="require defaults.mcp"):
        V2CampaignConfig.model_validate(payload)


def test_provider_behavior_sources_are_runtime_fingerprinted() -> None:
    assert {
        "mcp_launcher",
        "provider_auth",
        "provider_contract",
    } <= _RUNNER_IMPLEMENTATION_SOURCES.keys()
