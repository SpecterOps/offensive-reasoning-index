"""Configuration-only Anthropic admission and attempt-owned SDK materialization.

Snapshots are private in-memory state, not serializable campaign artifacts.
Credential providers are never invoked by preparation or identity projection.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

from .provider_contract import ProviderAuthenticationError, ProviderCapabilityError

DEFAULT_ANTHROPIC_BASE_URL = "https://api.anthropic.com"
_AUTH_HEADERS = frozenset({"authorization", "x-api-key", "proxy-authorization", "cookie"})
_SDK_HASHES = {
    "lib/streaming/_messages.py": (
        "cf8088c4e60919a7d4d67c3dcfba6c16ff17eef28fd69348174e584c0630047b"
    ),
    "lib/streaming/_types.py": "f2ede3f18696d8a68c6c09a8d87174b7bf80550f45303d91988fa083799306f2",
    "resources/messages/messages.py": (
        "c0b07f6f23df15b0da19ef8d4482f85315fc74b3e0f0ff6d0849b32391a15bd0"
    ),
    "_client.py": "240329ad19a78f1b990b1856a78c8e42691c54244cf60f8f55da96074e53c944",
    "_base_client.py": "19f44698fbd96dfb93e320b54643339dcc2788907a58dc94c86884bc41048e74",
    "lib/credentials/_chain.py": "9cf6f174ae345645e293ba0ccaf0204619de17d244ef361cd0c1e43a6c9f3455",
    "lib/credentials/_providers.py": (
        "b056e454c8c120f789c642eaf12eeb08f1244903a688d94e93840ef776a68a84"
    ),
    "lib/credentials/_workload.py": (
        "08f7cd223e9b1eebf9c882790bc59e2477efd2d20e1b504d664726f1d2453b46"
    ),
    "lib/credentials/_auth.py": "f09af9e39953571a8547a8270173b0c49b22facebc977031db7242445137156c",
    "lib/credentials/_constants.py": (
        "d6284b2e2b46fc8a12f49165b682d3c6f43cb646804136c894abbf86b5fc52cf"
    ),
}


@lru_cache(maxsize=1)
def _check_sdk_compatibility() -> None:
    message = "Unsupported Anthropic SDK credential interface"
    try:
        import anthropic
        from anthropic.lib.credentials import _constants, _providers, _workload

        root = Path(anthropic.__file__).parent
        if anthropic.__version__ != "0.116.0":
            raise ProviderCapabilityError(message)
        for name, expected in _SDK_HASHES.items():
            if hashlib.sha256((root / name).read_bytes()).hexdigest() != expected:
                raise ProviderCapabilityError(message)
        for owner, names in (
            (anthropic, ("AsyncAnthropic", "DefaultAsyncHttpxClient", "Omit")),
            (_providers, ("CredentialsFile", "InMemoryConfig", "IdentityTokenFile")),
            (_workload, ("WorkloadIdentityCredentials",)),
            (
                _constants,
                (
                    "_has_explicit_active_config",
                    "_has_active_profile_config",
                    "resolve_identity_token_path",
                ),
            ),
        ):
            if any(not callable(getattr(owner, name, None)) for name in names):
                raise ProviderCapabilityError(message)
    except (ImportError, OSError, TypeError, AttributeError):
        raise ProviderCapabilityError(message) from None


@dataclass(frozen=True, slots=True)
class AnthropicConfigSnapshot:
    selection: str = field(repr=False)
    config_json: str = field(repr=False)
    headers: tuple[tuple[str, str], ...] = field(repr=False)
    header_sources: tuple[tuple[str, str], ...] = field(repr=False)
    api_key: str | None = field(repr=False)
    auth_token: str | None = field(repr=False)
    custom_key: str | None = field(repr=False)
    token_source_json: str = field(repr=False)
    inherited_header_names: tuple[str, ...] = field(repr=False)


@dataclass(frozen=True, slots=True)
class AnthropicBinding:
    base_url: str
    endpoint_family: str
    credential_source: str
    model_slug: str
    config_fingerprint: str
    snapshot: AnthropicConfigSnapshot = field(repr=False)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _endpoint(raw: str) -> tuple[str, str]:
    message = "Anthropic endpoint configuration is invalid"
    try:
        if not raw or "\\" in raw or any(ord(c) <= 32 or ord(c) == 127 for c in raw):
            raise ValueError
        normalized = raw.rstrip("/")
        parsed = urlsplit(normalized)
        if (
            not normalized
            or not parsed.hostname
            or parsed.scheme not in {"http", "https"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or "?" in raw
            or "#" in raw
            or parsed.netloc.endswith(":")
        ):
            raise ValueError
        port = parsed.port
        official = parsed.hostname.lower() == "api.anthropic.com"
        if official and (parsed.scheme != "https" or port not in (None, 443) or parsed.path):
            raise ValueError
        return normalized, "anthropic" if official else "anthropic_compat"
    except (ValueError, TypeError):
        raise ProviderCapabilityError(message) from None


def _absolute(path: Any) -> str:
    # abspath/expanduser do not resolve symlinks or require the token file to exist.
    return os.path.abspath(os.path.expanduser(str(path)))


def _environment_headers() -> dict[str, str]:
    headers = {}
    for line in (os.environ.get("ANTHROPIC_CUSTOM_HEADERS") or "").split("\n"):
        colon = line.find(":")
        if colon >= 0:
            headers[line[:colon].strip()] = line[colon + 1 :].strip()
    return headers


def _profile_configuration(explicit: bool) -> tuple[dict, dict, str | None]:
    import anthropic
    from anthropic.lib.credentials._providers import CredentialsFile

    provider = None
    try:
        provider = CredentialsFile()
        extra = provider.extra_headers()
        raw = provider._load_config()
        auth = raw["authentication"]
        kind = auth.get("type")
        if kind not in {"user_oauth", "oidc_federation"}:
            raise ProviderAuthenticationError("Unsupported Anthropic authentication mode")
        config = {
            key: raw[key] for key in ("base_url", "organization_id", "workspace_id") if key in raw
        }
        config["authentication"] = {
            key: auth[key]
            for key in (
                "type",
                "client_id",
                "scope",
                "federation_rule_id",
                "service_account_id",
                "identity_token",
            )
            if key in auth
        }
        config["authentication"]["credentials_path"] = _absolute(provider._credentials_path)
        return config, extra, provider.resolved_base_url
    except anthropic.AnthropicError:
        if not explicit:
            return {}, {}, None
        # The pinned parser assigns the resolved URL before its own HTTPS
        # check. Classify that already-read route without inspecting error text
        # or reopening configuration. Its initial official default is valid and
        # therefore leaves unrelated parsing failures as authentication errors.
        resolved_url = getattr(provider, "_base_url", None)
        if resolved_url is not None:
            _endpoint(resolved_url)
        raise ProviderAuthenticationError(
            "Anthropic profile configuration is unavailable"
        ) from None


def _native_configuration() -> tuple[str, dict, dict, str | None]:
    from anthropic.lib.credentials import _constants as c

    explicit = bool(os.environ.get(c.ENV_PROFILE) or os.environ.get(c.ENV_CONFIG_DIR))
    explicit = explicit or c._has_explicit_active_config()
    if explicit:
        config, headers, url = _profile_configuration(True)
        kind = "oauth" if config["authentication"]["type"] == "user_oauth" else "profile-federation"
        return f"explicit-{kind}", config, headers, url
    rule, organization = (
        os.environ.get(c.ENV_FEDERATION_RULE_ID),
        os.environ.get(c.ENV_ORGANIZATION_ID),
    )
    identity_path = c.resolve_identity_token_path()
    if rule and organization and (identity_path is not None or c.ENV_IDENTITY_TOKEN in os.environ):
        config = {
            "organization_id": organization,
            "workspace_id": os.environ.get(c.ENV_WORKSPACE_ID) or None,
            "authentication": {
                "type": "oidc_federation",
                "federation_rule_id": rule,
                "service_account_id": os.environ.get(c.ENV_SERVICE_ACCOUNT_ID),
                "scope": os.environ.get(c.ENV_SCOPE),
            },
        }
        if identity_path is not None:
            config["authentication"]["identity_token"] = {
                "source": "file",
                "path": str(identity_path),
            }
        return "environment-federation", config, {}, None
    if c._has_active_profile_config():
        config, headers, url = _profile_configuration(False)
        if config:
            kind = (
                "oauth"
                if config["authentication"]["type"] == "user_oauth"
                else "profile-federation"
            )
            return f"fallback-{kind}", config, headers, url
    return "header-only", {}, {}, None


def _token_source(config: dict, selection: str) -> dict:
    from anthropic.lib.credentials import _constants as c

    if not config:
        return {"kind": "static"}
    auth = config["authentication"]
    source = {"kind": auth["type"]}
    if "credentials_path" in auth:
        source["credentials_path"] = auth["credentials_path"]
    if auth["type"] == "oidc_federation":
        identity = auth.get("identity_token")
        if identity is not None:
            if (
                not isinstance(identity, dict)
                or identity.get("source") != "file"
                or not identity.get("path")
            ):
                raise ProviderCapabilityError("Unsupported Anthropic identity source")
            path = identity["path"]
        else:
            path = c.resolve_identity_token_path()
        if path is not None:
            path = _absolute(path)
            auth["identity_token"] = {"source": "file", "path": path}
            source["identity_file"] = path
        elif selection == "environment-federation":
            source["identity_environment"] = c.ENV_IDENTITY_TOKEN
        else:
            raise ProviderAuthenticationError("Anthropic identity source is unavailable")
        if not auth.get("federation_rule_id") or not config.get("organization_id"):
            raise ProviderAuthenticationError("Anthropic federation configuration is unavailable")
    return source


def _custom_api_key(snapshot: AnthropicConfigSnapshot) -> str:
    if not snapshot.custom_key:
        raise ProviderAuthenticationError(
            "Custom Anthropic endpoint requires ANTHROPIC_COMPAT_API_KEY"
        )
    return snapshot.custom_key


def _custom_headers(snapshot: AnthropicConfigSnapshot, key: str) -> dict:
    import anthropic

    headers = dict(snapshot.headers)
    for name in snapshot.inherited_header_names:
        if name.lower() in _AUTH_HEADERS:
            headers[name] = anthropic.Omit()
    headers["X-Api-Key"] = key
    return headers


def prepare_anthropic_binding(model: str, base_url: str | None = None) -> AnthropicBinding:
    slug = model.removeprefix("anthropic/").split("@", 1)[0]
    if not slug.strip():
        raise ProviderCapabilityError("Anthropic model identifier is required")
    _check_sdk_compatibility()
    import anthropic

    inline = model.rsplit("@", 1)[1] if "@" in model else None
    selected_url = base_url or inline or os.environ.get("ANTHROPIC_BASE_URL")
    admitted = _endpoint(selected_url) if selected_url else None
    custom = _environment_headers()
    key, bearer = os.environ.get("ANTHROPIC_API_KEY"), os.environ.get("ANTHROPIC_AUTH_TOKEN")
    config, extra, profile_url = {}, {}, None
    if admitted is not None and admitted[1] == "anthropic_compat":
        selection = "custom-key"
    elif key is not None or bearer is not None:
        selection = (
            "native-both"
            if key is not None and bearer is not None
            else "native-key"
            if key is not None
            else "native-bearer"
        )
    else:
        selection, config, extra, profile_url = _native_configuration()
    url, family = admitted or _endpoint(profile_url or DEFAULT_ANTHROPIC_BASE_URL)
    if family == "anthropic_compat":
        selection, config, extra, key, bearer = "custom-key", {}, {}, None, None
    exchange = None
    if config:
        exchange, exchange_family = _endpoint(config.get("base_url") or url)
        if exchange_family != "anthropic":
            raise ProviderCapabilityError("Anthropic credential endpoint configuration is invalid")
    token_source = _token_source(config, selection)
    headers, origins = dict(extra), {name: "profile" for name in extra}
    if key is not None:
        headers["X-Api-Key"], origins["X-Api-Key"] = key, "key"
    if bearer is not None:
        headers["Authorization"], origins["Authorization"] = f"Bearer {bearer}", "bearer"
    headers.update(custom)
    origins.update({name: "custom" for name in custom})
    if selection == "custom-key":
        headers = {
            name: value for name, value in custom.items() if name.lower() not in _AUTH_HEADERS
        }
        origins = {name: "custom" for name in headers}
        headers["X-Api-Key"] = os.environ.get("ANTHROPIC_COMPAT_API_KEY") or ""
        origins["X-Api-Key"] = "dedicated"
    snapshot = AnthropicConfigSnapshot(
        selection,
        _json(config),
        tuple(sorted(headers.items())),
        tuple(sorted(origins.items())),
        key,
        bearer,
        os.environ.get("ANTHROPIC_COMPAT_API_KEY"),
        _json(token_source),
        tuple(custom),
    )
    if selection == "custom-key":
        selected_key = _custom_api_key(snapshot)
        headers["X-Api-Key"] = selected_key
    try:
        anthropic.AsyncAnthropic._validate_headers(
            SimpleNamespace(_token_cache=True if config else None), headers, {}
        )
    except TypeError:
        raise ProviderAuthenticationError(
            "Anthropic authentication configuration is unavailable"
        ) from None
    sources = []
    for name in sorted(headers):
        if name.lower() in {"authorization", "x-api-key"} and headers[name]:
            origin = origins[name]
            source = {
                "key": "ANTHROPIC_API_KEY",
                "bearer": "ANTHROPIC_AUTH_TOKEN",
                "dedicated": "ANTHROPIC_COMPAT_API_KEY",
                "custom": "ANTHROPIC_CUSTOM_HEADERS",
            }.get(origin, origin)
            if source not in sources:
                sources.append(source)
    # Native admission above is case-sensitive; the later HTTPX auth-flow check
    # is not. A lowercase static bearer can shadow a configured token provider.
    if config and not any(
        value for name, value in headers.items() if name.lower() in {"authorization", "x-api-key"}
    ):
        sources.append(selection)
    credential_source = "+".join(sources) or selection
    auth = config.get("authentication", {})
    semantics = {name: config.get(name) for name in ("base_url", "organization_id", "workspace_id")}
    semantics["authentication"] = {
        name: auth.get(name)
        for name in ("type", "client_id", "scope", "federation_rule_id", "service_account_id")
    }
    payload = {
        "version": "anthropic-binding-v1",
        "model_slug": slug,
        "inference_url": url,
        "exchange_url": exchange,
        "endpoint_family": family,
        "selection": selection,
        "credential_sources": [
            f"{name}:{'absent' if value is None else 'nonempty' if value else 'empty'}"
            for name, value in (("ANTHROPIC_API_KEY", key), ("ANTHROPIC_AUTH_TOKEN", bearer))
        ]
        + sources,
        "effective_header_sources": [
            {
                "name": name,
                "source": origins[name],
                "present": True,
                "nonempty": bool(headers[name]) if name in ("X-Api-Key", "Authorization") else None,
            }
            for name in sorted(headers)
        ],
        "profile_semantics": semantics,
        "token_source": token_source,
    }
    return AnthropicBinding(
        url,
        family,
        credential_source,
        slug,
        hashlib.sha256(_json(payload).encode()).hexdigest(),
        snapshot,
    )


def anthropic_binding_identity(binding: AnthropicBinding) -> dict:
    return {
        name: getattr(binding, name)
        for name in ("endpoint_family", "credential_source", "model_slug", "config_fingerprint")
    }


def anthropic_private_headers(binding: AnthropicBinding) -> list[dict]:
    origins = dict(binding.snapshot.header_sources)
    return [
        {
            "name": name,
            "source": origins[name],
            **({} if name.lower() in _AUTH_HEADERS else {"value": value}),
        }
        for name, value in binding.snapshot.headers
    ]


def _snapshot_config(binding: AnthropicBinding) -> dict:
    return json.loads(binding.snapshot.config_json)


def _client_kwargs(binding: AnthropicBinding, transport: Any, credentials: Any) -> dict:
    import anthropic

    snapshot = binding.snapshot
    headers = dict(snapshot.headers)
    key, bearer = snapshot.api_key, snapshot.auth_token
    if snapshot.selection == "custom-key":
        key = _custom_api_key(snapshot)
        headers = _custom_headers(snapshot, key)
    elif snapshot.selection == "header-only":
        key = ""
        if "X-Api-Key" not in headers:
            headers["X-Api-Key"] = anthropic.Omit()
    return {
        "base_url": binding.base_url,
        "api_key": key,
        "auth_token": bearer,
        "credentials": credentials,
        "default_headers": headers,
        "http_client": transport,
    }


class AnthropicAttempt:
    """Exactly-once owned cleanup attempts; not worker-thread quiescence."""

    def __init__(self) -> None:
        self.client: Any = None
        self.transport: Any = None
        self.credentials: Any = None
        self._transport_close_attempted = False
        self._credentials_close_attempted = False

    async def aclose(self) -> None:
        cancellation = None
        if self.transport is not None and not self._transport_close_attempted:
            self._transport_close_attempted = True
            try:
                await self.transport.aclose()
            except asyncio.CancelledError as exc:
                cancellation = exc
            except Exception:
                pass
        if self.credentials is not None and not self._credentials_close_attempted:
            self._credentials_close_attempted = True
            try:
                self.credentials.close()
            except asyncio.CancelledError as exc:
                cancellation = exc
            except Exception:
                pass
        if cancellation is not None:
            raise cancellation


def _environment_identity_token() -> str:
    value = os.environ.get("ANTHROPIC_IDENTITY_TOKEN")
    if value is None:
        raise ProviderAuthenticationError("Anthropic identity token is unavailable")
    return value


async def materialize_anthropic_client(binding: AnthropicBinding) -> AnthropicAttempt:
    import anthropic
    from anthropic.lib.credentials._providers import IdentityTokenFile, InMemoryConfig
    from anthropic.lib.credentials._workload import WorkloadIdentityCredentials

    attempt = AnthropicAttempt()
    try:
        attempt.transport = anthropic.DefaultAsyncHttpxClient(follow_redirects=False)
        config = _snapshot_config(binding)
        if binding.snapshot.selection == "environment-federation":
            source = json.loads(binding.snapshot.token_source_json)
            identity = (
                IdentityTokenFile(source["identity_file"])
                if "identity_file" in source
                else _environment_identity_token
            )
            auth = config["authentication"]
            attempt.credentials = WorkloadIdentityCredentials(
                identity_token_provider=identity,
                federation_rule_id=auth["federation_rule_id"],
                organization_id=config["organization_id"],
                workspace_id=config.get("workspace_id"),
                service_account_id=auth.get("service_account_id"),
                scope=auth.get("scope"),
            )
        elif config:
            attempt.credentials = InMemoryConfig(config)
        attempt.client = anthropic.AsyncAnthropic(
            **_client_kwargs(binding, attempt.transport, attempt.credentials)
        )
        return attempt
    except BaseException:
        await attempt.aclose()
        raise
