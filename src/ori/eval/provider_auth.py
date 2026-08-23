"""Environment-backed authentication helpers for model providers."""

from __future__ import annotations

import os


def openai_compat_api_key() -> str | None:
    """Return the key for an OpenAI-compatible endpoint, if configured.

    Keep the existing compatibility-specific precedence while allowing an
    OpenRouter-specific key to be used without renaming it to an OpenAI key.
    """

    return (
        os.getenv("OPENAI_COMPAT_API_KEY")
        or os.getenv("OPENROUTER_API_KEY")
        or os.getenv("OPENAI_API_KEY")
    )
