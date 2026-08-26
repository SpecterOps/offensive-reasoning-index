"""Environment-backed authentication helpers for model providers."""

from __future__ import annotations

import os
from urllib.parse import urlsplit


def openai_compat_api_key(base_url: str | None = None) -> str | None:
    """Return the key for an OpenAI-compatible endpoint, if configured.

    ``OPENAI_COMPAT_API_KEY`` is the explicit universal override. Provider-
    specific keys are selected from the endpoint when possible so a process
    can safely keep both OpenRouter and Nous credentials configured. The
    unscoped fallback preserves the historical OpenRouter-before-OpenAI order.
    """

    explicit = os.getenv("OPENAI_COMPAT_API_KEY")
    if explicit:
        return explicit

    hostname = (urlsplit(base_url).hostname or "").lower() if base_url else ""
    if hostname in {"inference-api.nousresearch.com", "portal.nousresearch.com"}:
        nous_key = os.getenv("NOUS_API_KEY") or os.getenv("NOUS_PORTAL_API_KEY")
        if nous_key:
            return nous_key
    if hostname == "openrouter.ai" or hostname.endswith(".openrouter.ai"):
        openrouter_key = os.getenv("OPENROUTER_API_KEY")
        if openrouter_key:
            return openrouter_key

    return (
        os.getenv("OPENROUTER_API_KEY")
        or os.getenv("NOUS_API_KEY")
        or os.getenv("NOUS_PORTAL_API_KEY")
        or os.getenv("OPENAI_API_KEY")
    )
