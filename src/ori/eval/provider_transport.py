"""Owned, destination-bound transports for native OpenAI SDK inference."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from .provider_contract import ProviderProtocolError


@asynccontextmanager
async def openai_inference_client(**kwargs: Any) -> AsyncIterator[Any]:
    """Keep inference at its admitted destination and release the owned transport.

    SDK retry and timeout defaults are unchanged. Existing Codex callers still
    close their SDK client; this fallback also covers constructor/close failures.
    """
    if "http_client" in kwargs:
        raise ValueError("Inference transport is owned by the provider boundary")

    import openai

    transport = openai.DefaultAsyncHttpxClient(follow_redirects=False)
    try:
        try:
            client = openai.AsyncOpenAI(http_client=transport, **kwargs)
            yield client
        except openai.APIStatusError as exc:
            if 300 <= exc.status_code < 400:
                raise ProviderProtocolError(
                    "Provider redirects are not supported; configure the final endpoint"
                ) from exc
            raise
    finally:
        if not transport.is_closed:
            try:
                await transport.aclose()
            except Exception:
                # Cleanup failure must not replace the result or primary error.
                # Cancellation deliberately propagates; closure is not claimed.
                pass
