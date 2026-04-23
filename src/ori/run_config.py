"""Versioned config loader for config-driven ORI runs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class RunConfigOverrides:
    manifest: str | None = None
    output: str | None = None
    output_dir: str | None = None
    bhce_url: str | None = None
    concurrency: int | None = None
    mcp_dir: str | None = None
    max_steps: int | None = None
    model_base_url: str | None = None
    max_model_reruns_on_infra: int | None = None
    health_timeout_seconds: float | None = None
    health_poll_interval: float | None = None
    resource_mode: str | None = None
    telemetry_enabled: bool | None = None


@dataclass(frozen=True)
class ResolvedRunProfile:
    profile_name: str
    kind: str
    manifest: str | None
    output: str | None
    output_dir: str | None
    bhce_url: str | None
    model_base_url: str | None
    concurrency: int | None
    max_model_reruns_on_infra: int
    health_timeout_seconds: float
    health_poll_interval: float
    mcp_dir: str | None
    max_steps: int | None
    resource_mode: str
    telemetry_enabled: bool
    model_entry: str | dict[str, Any] | None
    model_entries: list[str | dict[str, Any]] | None


@dataclass(frozen=True)
class RunProfileInfo:
    profile_name: str
    kind: str
    enabled: bool


_KIND_ALIASES = {
    "eval": "eval",
    "baseline": "baseline",
    "eval_mcp": "eval_mcp",
    "eval-mcp": "eval_mcp",
    "baseline_mcp": "baseline_mcp",
    "baseline-mcp": "baseline_mcp",
    "smoke_eval": "smoke_eval",
    "smoke-eval": "smoke_eval",
    "smoke_mcp": "smoke_mcp",
    "smoke-mcp": "smoke_mcp",
    "preflight": "preflight",
}


def _load_run_config_data(config_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    data = yaml.safe_load(config_path.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError("Run config root must be a mapping.")
    version = data.get("version", 1)
    if version != 1:
        raise ValueError(f"Unsupported run config version {version!r}; expected 1.")

    profiles = data.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        raise ValueError("Run config must define a non-empty profiles mapping.")

    return data, profiles


def _resolve_path(config_dir: Path, value: str | None) -> str | None:
    if value is None:
        return None
    path = Path(value)
    if path.is_absolute():
        return str(path)
    return str((config_dir / path).resolve())


def _normalize_kind(raw_kind: Any) -> str:
    if not isinstance(raw_kind, str):
        raise ValueError("Profile kind must be a string.")
    try:
        return _KIND_ALIASES[raw_kind]
    except KeyError as exc:
        supported = ", ".join(sorted(_KIND_ALIASES))
        raise ValueError(
            f"Unsupported profile kind {raw_kind!r}. Supported values: {supported}"
        ) from exc


def _pick_profile_name(profiles: dict[str, Any], requested: str | None) -> str:
    if requested:
        if requested not in profiles:
            known = ", ".join(sorted(profiles))
            raise ValueError(f"Unknown profile {requested!r}. Available profiles: {known}")
        return requested
    if len(profiles) == 1:
        return next(iter(profiles))
    raise ValueError("Config contains multiple profiles; pass --profile to select one.")


def _merged_value(
    overrides_value: Any,
    profile: dict[str, Any],
    defaults: dict[str, Any],
    key: str,
) -> Any:
    if overrides_value is not None:
        return overrides_value
    if key in profile:
        return profile[key]
    return defaults.get(key)


def _merged_nested_value(
    overrides_value: Any,
    profile: dict[str, Any],
    defaults: dict[str, Any],
    section: str,
    key: str,
    *,
    fallback: Any,
) -> Any:
    if overrides_value is not None:
        return overrides_value
    profile_section = profile.get(section) or {}
    defaults_section = defaults.get(section) or {}
    if key in profile_section:
        return profile_section[key]
    if key in defaults_section:
        return defaults_section[key]
    return fallback


def _normalize_resource_mode(raw_mode: Any) -> str:
    if raw_mode is None:
        return "off"
    if not isinstance(raw_mode, str):
        raise ValueError("MCP resource_mode must be a string when present.")
    if raw_mode not in {"off", "on-demand"}:
        raise ValueError(
            f"Unsupported resource_mode {raw_mode!r}. Supported values: off, on-demand"
        )
    return raw_mode


def _normalize_telemetry_enabled(raw_value: Any) -> bool:
    if raw_value is None:
        return True
    if not isinstance(raw_value, bool):
        raise ValueError("telemetry.enabled must be a boolean when present.")
    return raw_value


def load_run_profile(
    config_path: Path,
    *,
    profile_name: str | None = None,
    overrides: RunConfigOverrides | None = None,
) -> ResolvedRunProfile:
    """Load and validate one runnable profile from a versioned config file."""
    data, profiles = _load_run_config_data(config_path)

    chosen_name = _pick_profile_name(profiles, profile_name)
    profile = profiles[chosen_name]
    if not isinstance(profile, dict):
        raise ValueError(f"Profile {chosen_name!r} must be a mapping.")

    defaults = data.get("defaults") or {}
    if not isinstance(defaults, dict):
        raise ValueError("defaults must be a mapping when present.")

    config_dir = config_path.parent
    overrides = overrides or RunConfigOverrides()
    kind = _normalize_kind(profile.get("kind"))

    manifest = _resolve_path(
        config_dir,
        _merged_value(overrides.manifest, profile, defaults, "manifest"),
    )
    output = _resolve_path(
        config_dir,
        _merged_value(overrides.output, profile, defaults, "output"),
    )
    output_dir = _resolve_path(
        config_dir,
        _merged_value(overrides.output_dir, profile, defaults, "output_dir"),
    )
    bhce_url = _merged_value(overrides.bhce_url, profile, defaults, "bhce_url")
    model_base_url = _merged_value(
        overrides.model_base_url,
        profile,
        defaults,
        "model_base_url",
    )
    concurrency = _merged_value(overrides.concurrency, profile, defaults, "concurrency")
    max_model_reruns_on_infra = _merged_value(
        overrides.max_model_reruns_on_infra,
        profile,
        defaults,
        "max_model_reruns_on_infra",
    )
    if max_model_reruns_on_infra is None:
        max_model_reruns_on_infra = 1

    health_timeout_seconds = _merged_nested_value(
        overrides.health_timeout_seconds,
        profile,
        defaults,
        "health",
        "timeout_seconds",
        fallback=60.0,
    )
    health_poll_interval = _merged_nested_value(
        overrides.health_poll_interval,
        profile,
        defaults,
        "health",
        "poll_interval",
        fallback=5.0,
    )
    mcp_dir = _resolve_path(
        config_dir,
        _merged_nested_value(
            overrides.mcp_dir,
            profile,
            defaults,
            "mcp",
            "mcp_dir",
            fallback="../bloodhound-mcp",
        ),
    )
    max_steps = _merged_nested_value(
        overrides.max_steps,
        profile,
        defaults,
        "mcp",
        "max_steps",
        fallback=12,
    )
    resource_mode = _normalize_resource_mode(
        _merged_nested_value(
            overrides.resource_mode,
            profile,
            defaults,
            "mcp",
            "resource_mode",
            fallback="off",
        )
    )
    telemetry_enabled = _normalize_telemetry_enabled(
        _merged_nested_value(
            overrides.telemetry_enabled,
            profile,
            defaults,
            "telemetry",
            "enabled",
            fallback=True,
        )
    )

    model_entry = profile.get("model")
    model_entries = profile.get("models")

    if kind in {"eval", "eval_mcp"}:
        if model_entry is None and model_entries is not None:
            if not isinstance(model_entries, list) or len(model_entries) != 1:
                raise ValueError(
                    f"Profile {chosen_name!r} must define exactly one model for {kind}."
                )
            model_entry = model_entries[0]
        if model_entry is None:
            raise ValueError(f"Profile {chosen_name!r} must define model for {kind}.")
        if output is None:
            raise ValueError(f"Profile {chosen_name!r} must define output for {kind}.")
    elif kind in {"baseline", "baseline_mcp"}:
        if not isinstance(model_entries, list) or not model_entries:
            raise ValueError(f"Profile {chosen_name!r} must define non-empty models for {kind}.")
        if output_dir is None:
            raise ValueError(f"Profile {chosen_name!r} must define output_dir for {kind}.")
    elif kind in {"smoke_eval", "smoke_mcp", "preflight"}:
        if output_dir is None:
            raise ValueError(f"Profile {chosen_name!r} must define output_dir for {kind}.")

    if kind in {
        "eval",
        "eval_mcp",
        "baseline",
        "baseline_mcp",
        "smoke_eval",
        "smoke_mcp",
        "preflight",
    }:
        if manifest is None:
            raise ValueError(f"Profile {chosen_name!r} must define manifest for {kind}.")

    return ResolvedRunProfile(
        profile_name=chosen_name,
        kind=kind,
        manifest=manifest,
        output=output,
        output_dir=output_dir,
        bhce_url=bhce_url,
        model_base_url=model_base_url,
        concurrency=concurrency,
        max_model_reruns_on_infra=int(max_model_reruns_on_infra),
        health_timeout_seconds=float(health_timeout_seconds),
        health_poll_interval=float(health_poll_interval),
        mcp_dir=mcp_dir,
        max_steps=int(max_steps) if max_steps is not None else None,
        resource_mode=resource_mode,
        telemetry_enabled=telemetry_enabled,
        model_entry=model_entry,
        model_entries=model_entries,
    )


def list_run_profiles(config_path: Path) -> list[RunProfileInfo]:
    """List profiles in file order for automation-oriented CLI workflows."""

    _, profiles = _load_run_config_data(config_path)
    infos: list[RunProfileInfo] = []
    for name, profile in profiles.items():
        if not isinstance(profile, dict):
            raise ValueError(f"Profile {name!r} must be a mapping.")
        enabled = profile.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError(f"Profile {name!r} field 'enabled' must be a boolean when present.")
        infos.append(
            RunProfileInfo(
                profile_name=name,
                kind=_normalize_kind(profile.get("kind")),
                enabled=enabled,
            )
        )
    return infos
