"""Environment-backed authentication helpers for model providers."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlsplit

OpenAIEndpointFamily = Literal["openrouter", "nous", "openai", "generic"]


@dataclass(frozen=True, slots=True)
class OpenAICompatCredential:
    """Resolved credential and non-secret provenance for a compatible endpoint."""

    endpoint_family: OpenAIEndpointFamily
    credential_source: str | None
    api_key: str | None = field(repr=False)


def _openai_endpoint_family(base_url: str | None) -> OpenAIEndpointFamily:
    """Classify an endpoint without trusting path fragments or lookalike hosts."""

    hostname = (urlsplit(base_url).hostname or "").lower() if base_url else ""
    if hostname == "openrouter.ai" or hostname.endswith(".openrouter.ai"):
        return "openrouter"
    if hostname in {"inference-api.nousresearch.com", "portal.nousresearch.com"}:
        return "nous"
    if hostname == "api.openai.com":
        return "openai"
    return "generic"


def openai_endpoint_family(base_url: str | None) -> OpenAIEndpointFamily:
    """Return the public, normalized family for one OpenAI-style endpoint."""

    return _openai_endpoint_family(base_url)


def official_openai_endpoint_is_secure(base_url: str | None) -> bool:
    """Require the exact TLS origin before allowing an official OpenAI key."""

    parsed = urlsplit(base_url or "")
    try:
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.lower() == "https"
        and (parsed.hostname or "").lower() == "api.openai.com"
        and port in {None, 443}
        and parsed.username is None
        and parsed.password is None
    )


def sanitized_provider_endpoint(base_url: str | None) -> str:
    """Return a secret-free scheme/host/port identity for private telemetry."""

    parsed = urlsplit(base_url or "")
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return ""
    scheme = (parsed.scheme or "https").lower()
    port = f":{parsed.port}" if parsed.port is not None else ""
    return f"{scheme}://{hostname}{port}"


def openai_compat_endpoint_is_local(base_url: str | None) -> bool:
    """Return whether an unauthenticated compatible endpoint is loopback-local."""

    hostname = (urlsplit(base_url).hostname or "").lower() if base_url else ""
    return hostname in {"localhost", "127.0.0.1", "::1", "0.0.0.0"} or hostname.endswith(
        ".localhost"
    )


def _scoped_compat_origin_is_secure(base_url: str | None) -> bool:
    """Check transport/userinfo after a scoped provider family is recognized."""

    parsed = urlsplit(base_url or "")
    try:
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.lower() == "https"
        and port in {None, 443}
        and parsed.username is None
        and parsed.password is None
    )


def resolve_openai_compat_credential(
    base_url: str | None = None,
) -> OpenAICompatCredential:
    """Resolve a key without allowing credentials to cross endpoint families.

    ``OPENAI_COMPAT_API_KEY`` is an intentional override for generic compatible
    endpoints only. OpenRouter, Nous, and official OpenAI use their scoped
    credentials exclusively. Unknown and missing hosts never inherit a
    credential belonging to OpenRouter, Nous, or official OpenAI.
    """

    endpoint_family = _openai_endpoint_family(base_url)
    if endpoint_family in {"openrouter", "nous"} and not _scoped_compat_origin_is_secure(
        base_url
    ):
        return OpenAICompatCredential(
            endpoint_family=endpoint_family,
            credential_source=None,
            api_key=None,
        )
    if endpoint_family == "generic":
        explicit = os.getenv("OPENAI_COMPAT_API_KEY")
        if explicit:
            return OpenAICompatCredential(
                endpoint_family=endpoint_family,
                credential_source="OPENAI_COMPAT_API_KEY",
                api_key=explicit,
            )

    candidates: tuple[str, ...] = {
        "openrouter": ("OPENROUTER_API_KEY",),
        "nous": ("NOUS_API_KEY", "NOUS_PORTAL_API_KEY"),
        "openai": (
            ("OPENAI_API_KEY",)
            if official_openai_endpoint_is_secure(base_url)
            else ()
        ),
        "generic": (),
    }[endpoint_family]
    for source in candidates:
        api_key = os.getenv(source)
        if api_key:
            return OpenAICompatCredential(
                endpoint_family=endpoint_family,
                credential_source=source,
                api_key=api_key,
            )

    return OpenAICompatCredential(
        endpoint_family=endpoint_family,
        credential_source=None,
        api_key=None,
    )


def openai_compat_api_key(base_url: str | None = None) -> str | None:
    """Return the isolated key for an OpenAI-compatible endpoint, if configured."""

    return resolve_openai_compat_credential(base_url).api_key
