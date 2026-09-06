"""Shared Ollama inference destination and model-name resolution."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .provider_contract import ProviderCapabilityError


@dataclass(frozen=True, slots=True)
class OllamaEndpoint:
    """Keep the selected input so consumers normalize exactly once."""

    selected_base_url: str = field(repr=False)
    chat_url: str = field(repr=False)


def _chat_url(selected: str) -> str:
    resolved = selected.rstrip("/")
    if not resolved:
        raise ProviderCapabilityError("Ollama endpoint configuration is empty")
    if resolved.endswith("/v1"):
        resolved = resolved[:-3].rstrip("/")
    return f"{resolved}/api/chat"


def prepare_ollama_endpoint(base_url: str | None = None) -> OllamaEndpoint:
    selected = base_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    return OllamaEndpoint(selected_base_url=selected, chat_url=_chat_url(selected))


def ollama_model_name(model: str) -> str:
    name = model.removeprefix("ollama/")
    if not name.strip():
        raise ProviderCapabilityError("Ollama model identifier is required")
    if "@" in name:
        raise ProviderCapabilityError(
            "Ollama inline endpoint syntax is unsupported; use model_base_url"
        )
    return name
