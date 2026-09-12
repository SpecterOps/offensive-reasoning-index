"""Strict, path-resolved configuration for explicit V2 model campaigns."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import Field, PrivateAttr, field_validator, model_validator

from ..anthropic_binding import AnthropicBinding
from ..ollama_binding import OllamaEndpoint
from .schema import PROTOCOL_VERSION, Fingerprint, StrictModel, Track

ReasoningEffort = Literal[
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
]

StructuredOutputMode = Literal[
    "prompt_local_validation",
    "json_schema",
]

CampaignPurpose = Literal["official", "diagnostic_canary"]


class V2TrackArtifactPaths(StrictModel):
    public: str
    oracles: str
    candidates: str
    live_certification: str
    release_metadata: str | None = None
    selection: str | None = None


class V2SourcePaths(StrictModel):
    manifest: str
    archive: str


class V2HealthConfig(StrictModel):
    timeout_seconds: float = Field(default=60.0, strict=True, gt=0)
    poll_interval: float = Field(default=5.0, strict=True, gt=0)


class V2InfrastructureRetryConfig(StrictModel):
    immediate_retries: int = Field(default=1, strict=True, ge=0, le=1)
    deferred_cooldown_seconds: float = Field(default=300.0, strict=True, ge=300.0)


class V2MCPConfig(StrictModel):
    mcp_dir: str
    max_steps: int = Field(default=16, strict=True, gt=0)
    resource_mode: Literal["off"] = "off"
    tool_loop: Literal[
        "native-openai-compatible",
        "native-ollama",
    ] = "native-openai-compatible"
    telemetry_adapter: Literal[
        "auto",
        "generic",
        "llama-cpp",
        "mlx-lm",
        "vllm",
        "lm-studio",
    ] = "auto"
    read_timeout_seconds: float = Field(default=240.0, strict=True, gt=0)
    tool_timeout_seconds: float = Field(default=60.0, strict=True, gt=0)


class V2NativeMCPConfig(V2MCPConfig):
    """Explicit native lane; configuration never asserts qualification."""

    mode: Literal["native"]
    tool_loop: Literal["native-openai-compatible", "native-ollama", "native-anthropic"] = (
        "native-openai-compatible"
    )
    implementation_id: Literal["mwnickerson", "mordavid", "armadin"]
    capability_profile: str
    python_executable: str
    runtime_roots: tuple[str, ...] = Field(min_length=1)
    runtime_fingerprint: Fingerprint
    dependency_lock: str
    dependency_lock_fingerprint: Fingerprint
    backend: Literal["bhce", "neo4j"]
    databases: tuple[str, ...]
    qualification: str | None = None
    qualification_work: str | None = None
    original_config: str | None = None

    @field_validator("runtime_roots", "databases", mode="before")
    @classmethod
    def yaml_sequences(cls, value):
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def native_backend_is_explicit(self) -> V2NativeMCPConfig:
        if self.implementation_id == "mwnickerson":
            valid = self.backend == "bhce" and not self.databases
        elif self.implementation_id == "mordavid":
            valid = self.backend == "neo4j" and self.databases == ("neo4j", "bloodhound")
        else:
            valid = (
                self.backend == "neo4j"
                and len(self.databases) == 1
                and bool(self.databases[0].strip())
            )
        if not valid:
            raise ValueError("NATIVE_BACKEND_CONFIGURATION_MISMATCH")
        if (self.qualification is None) != (self.qualification_work is None) or any(
            value is not None and not value.strip()
            for value in (self.qualification, self.qualification_work)
        ):
            raise ValueError("NATIVE_QUALIFICATION_PATHS_INVALID")
        if any(
            not path.strip()
            for path in (
                self.capability_profile,
                self.python_executable,
                self.dependency_lock,
                *self.runtime_roots,
            )
        ):
            raise ValueError("NATIVE_PATH_CONFIGURATION_INVALID")
        return self


class V2Defaults(StrictModel):
    concurrency: int = Field(default=1, strict=True, gt=0)
    runs_per_model: int = Field(default=1, strict=True, gt=0)
    bhce_url: str | None = None
    model_base_url: str | None = None
    reasoning_effort: ReasoningEffort | None = None
    max_infra_retries: int = Field(default=1, strict=True, ge=0)
    infra_retry: V2InfrastructureRetryConfig = V2InfrastructureRetryConfig()
    graph_page_size: int = Field(default=500, strict=True, gt=0, le=2000)
    health: V2HealthConfig = V2HealthConfig()
    mcp: V2NativeMCPConfig | V2MCPConfig | None = None

    @model_validator(mode="after")
    def certified_concurrency_is_serial(self) -> V2Defaults:
        if self.concurrency != 1:
            raise ValueError("certified v2 campaigns currently require concurrency=1")
        if self.infra_retry.immediate_retries > self.max_infra_retries:
            raise ValueError(
                "defaults.infra_retry.immediate_retries cannot exceed defaults.max_infra_retries"
            )
        return self


class V2ModelEntry(StrictModel):
    name: str
    provider: Literal[
        "anthropic",
        "codex",
        "gemini",
        "ollama",
        "openai",
        "openai-compat",
    ]
    model: str
    api_surface: Literal[
        "auto",
        "chat_completions",
        "responses",
        "messages",
    ] = "auto"
    structured_output_mode: StructuredOutputMode = "prompt_local_validation"
    max_output_tokens: int = Field(default=2048, strict=True, gt=0, le=32768)
    runs_per_model: int | None = Field(default=None, strict=True, gt=0)
    model_base_url: str | None = None
    mcp_tool_loop: (
        Literal[
            "native-anthropic",
            "native-openai-compatible",
            "native-ollama",
        ]
        | None
    ) = None
    options: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name", "model")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("model names cannot be blank")
        return value.strip()

    @model_validator(mode="after")
    def options_do_not_shadow_typed_fields(self) -> V2ModelEntry:
        if "reasoning_effort" in self.options:
            raise ValueError(
                "set defaults.reasoning_effort instead of model options.reasoning_effort"
            )
        if "api_surface" in self.options:
            raise ValueError("set model api_surface instead of model options.api_surface")
        if "structured_output_mode" in self.options:
            raise ValueError(
                "set model structured_output_mode instead of model options.structured_output_mode"
            )
        if "max_output_tokens" in self.options or "max_tokens" in self.options:
            raise ValueError(
                "set model max_output_tokens instead of a free-form output-token option"
            )
        if self.structured_output_mode == "json_schema" and self.provider not in {
            "openai",
            "openai-compat",
            "gemini",
        }:
            raise ValueError(
                "structured_output_mode='json_schema' is currently supported only "
                "by Chat Completions providers"
            )
        return self

    @property
    def requested_model(self) -> str:
        prefix = f"{self.provider}/"
        return self.model if self.model.startswith(prefix) else prefix + self.model


class V2CampaignConfig(StrictModel):
    version: Literal[2] = 2
    protocol: Literal["ori-eval-protocol-v2"] = PROTOCOL_VERSION
    source: V2SourcePaths
    tracks: dict[Literal["direct", "mcp"], V2TrackArtifactPaths]
    modes: list[Literal["direct", "mcp"]]
    output_dir: str
    defaults: V2Defaults
    models: list[V2ModelEntry]
    # Omitted historical configurations retain the ordinary selected-release
    # behavior.  Diagnostic canaries opt in explicitly and can never be
    # admitted through the official branch below.
    purpose: CampaignPurpose = "official"
    selected_release: str | None = None
    canary_selection: str | None = None

    @model_validator(mode="after")
    def exact_modes_and_models(self) -> V2CampaignConfig:
        if not self.modes or len(self.modes) != len(set(self.modes)):
            raise ValueError("v2 modes must be non-empty and unique")
        if self.purpose == "diagnostic_canary":
            if self.modes != ["mcp"] or set(self.tracks) != {"mcp"}:
                raise ValueError("diagnostic canaries require exactly the MCP track")
            if self.selected_release is None or self.canary_selection is None:
                raise ValueError(
                    "diagnostic canaries require paired selected_release and canary_selection"
                )
            mcp = self.tracks["mcp"]
            if mcp.release_metadata is None or mcp.selection is None:
                raise ValueError("diagnostic canaries require MCP release metadata and selection")
        elif self.selected_release is not None:
            if set(self.tracks) != {"direct", "mcp"} or any(
                paths.release_metadata is None or paths.selection is None
                for paths in self.tracks.values()
            ):
                raise ValueError("selected releases require both tracks, metadata and selections")
        elif any(
            p.release_metadata is not None or p.selection is not None for p in self.tracks.values()
        ):
            raise ValueError("track selections require a paired selected_release")
        elif set(self.tracks) != set(self.modes):
            raise ValueError("v2 tracks must exactly match configured modes")
        if not self.models:
            raise ValueError("v2 campaigns require at least one model")
        if self.defaults.reasoning_effort is not None:
            unsupported_effort_providers = sorted(
                model.name for model in self.models if model.provider != "codex"
            )
            if unsupported_effort_providers:
                raise ValueError(
                    "v2 reasoning_effort is currently certified only for Codex models: "
                    + ", ".join(unsupported_effort_providers)
                )
        names = [model.name for model in self.models]
        if len(names) != len(set(names)):
            raise ValueError("v2 model names must be unique")
        if "mcp" in self.modes:
            if self.defaults.mcp is None:
                raise ValueError("v2 MCP campaigns require defaults.mcp configuration")
            unsupported = sorted(
                model.name
                for model in self.models
                if model.provider
                not in {
                    "codex",
                    "ollama",
                    "openai",
                    "openai-compat",
                }
                | ({"anthropic"} if isinstance(self.defaults.mcp, V2NativeMCPConfig) else set())
            )
            if unsupported:
                raise ValueError(
                    "native v2 MCP campaigns do not support provider(s): " + ", ".join(unsupported)
                )
        return self

    @property
    def track_modes(self) -> tuple[Track, ...]:
        return tuple(Track(mode) for mode in self.modes)


class ResolvedV2TrackPaths(StrictModel):
    public: Path
    oracles: Path
    candidates: Path
    live_certification: Path
    release_metadata: Path | None = None
    selection: Path | None = None


class ResolvedNativeMCPPaths(StrictModel):
    capability_profile: Path
    python_executable: Path
    runtime_roots: tuple[Path, ...]
    dependency_lock: Path
    qualification: Path | None = None
    qualification_work: Path | None = None
    original_config: Path | None = None


class ResolvedV2CampaignConfig(StrictModel):
    _native_mcp_paths: ResolvedNativeMCPPaths | None = PrivateAttr(default=None)

    @property
    def native_mcp_paths(self) -> ResolvedNativeMCPPaths | None:
        return self._native_mcp_paths

    _anthropic_bindings: Mapping[str, AnthropicBinding] | None = PrivateAttr(default=None)
    _anthropic_mutation_fingerprint: str | None = PrivateAttr(default=None)
    _ollama_endpoints: Mapping[str, OllamaEndpoint] | None = PrivateAttr(default=None)
    _ollama_mutation_fingerprint: str | None = PrivateAttr(default=None)

    source_manifest: Path
    archive: Path
    tracks: dict[Track, ResolvedV2TrackPaths]
    output_dir: Path
    mcp_dir: Path | None
    config: V2CampaignConfig
    source_config_fingerprint: str
    selected_release: Path | None = None
    canary_selection: Path | None = None


def _resolve(base: Path, raw: str) -> Path:
    path = Path(raw).expanduser()
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def load_v2_campaign_config(path: Path) -> ResolvedV2CampaignConfig:
    """Load a V2-only YAML file and resolve paths relative to that file."""
    return _load_v2_config(path, native_qualification=False)


def load_v2_native_qualification_config(path: Path) -> ResolvedV2CampaignConfig:
    """Load qualification inputs without demanding the certification outputs."""
    return _load_v2_config(path, native_qualification=True)


def _load_v2_config(path: Path, *, native_qualification: bool) -> ResolvedV2CampaignConfig:

    from .fingerprint import canonical_sha256

    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid v2 campaign YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("v2 campaign config must be a YAML mapping")
    config = V2CampaignConfig.model_validate(raw)
    if native_qualification and (
        not isinstance(config.defaults.mcp, V2NativeMCPConfig) or Track.MCP not in config.tracks
    ):
        raise ValueError("NATIVE_QUALIFICATION_CONFIGURATION_REQUIRED")
    base = path.parent.resolve()
    resolved_tracks = {
        Track(track): ResolvedV2TrackPaths(
            public=_resolve(base, paths.public),
            oracles=_resolve(base, paths.oracles),
            candidates=_resolve(base, paths.candidates),
            live_certification=_resolve(base, paths.live_certification),
            release_metadata=_resolve(base, paths.release_metadata)
            if paths.release_metadata
            else None,
            selection=_resolve(base, paths.selection) if paths.selection else None,
        )
        for track, paths in config.tracks.items()
    }
    resolved = ResolvedV2CampaignConfig(
        source_manifest=_resolve(base, config.source.manifest),
        archive=_resolve(base, config.source.archive),
        tracks=resolved_tracks,
        output_dir=_resolve(base, config.output_dir),
        selected_release=_resolve(base, config.selected_release)
        if config.selected_release
        else None,
        canary_selection=_resolve(base, config.canary_selection)
        if config.canary_selection
        else None,
        mcp_dir=(
            _resolve(base, config.defaults.mcp.mcp_dir) if config.defaults.mcp is not None else None
        ),
        config=config,
        source_config_fingerprint=canonical_sha256(raw),
    )
    required = (
        resolved.source_manifest,
        resolved.archive,
        *(
            value
            for paths in resolved.tracks.values()
            for value in (
                paths.public,
                paths.oracles,
                paths.candidates if not native_qualification else None,
                paths.live_certification if not native_qualification else None,
                paths.release_metadata if not native_qualification else None,
                paths.selection,
            )
            if value is not None
        ),
        *((resolved.selected_release,) if resolved.selected_release is not None else ()),
        *((resolved.canary_selection,) if resolved.canary_selection is not None else ()),
    )
    missing = [str(item) for item in required if not item.exists()]
    if missing:
        raise ValueError("v2 campaign config paths do not exist: " + ", ".join(missing))
    if resolved.mcp_dir is not None and not resolved.mcp_dir.exists():
        raise ValueError("v2 campaign config paths do not exist: " + str(resolved.mcp_dir))
    if resolved.mcp_dir is not None and not resolved.mcp_dir.is_dir():
        raise ValueError("v2 MCP path is not a directory")
    native = config.defaults.mcp
    if isinstance(native, V2NativeMCPConfig):
        # Preserve the venv executable's spelling: resolving its symlink here
        # would silently choose the base interpreter instead of the venv.
        python = Path(native.python_executable).expanduser()
        python = python.absolute() if python.is_absolute() else (base / python).absolute()
        paths = ResolvedNativeMCPPaths(
            capability_profile=_resolve(base, native.capability_profile),
            python_executable=python,
            runtime_roots=tuple(_resolve(base, root) for root in native.runtime_roots),
            dependency_lock=_resolve(base, native.dependency_lock),
            qualification=_resolve(base, native.qualification) if native.qualification else None,
            qualification_work=(
                _resolve(base, native.qualification_work) if native.qualification_work else None
            ),
            original_config=_resolve(base, native.original_config)
            if native.original_config
            else None,
        )
        if any(
            not item.is_file()
            for item in (
                paths.capability_profile,
                paths.python_executable,
                paths.dependency_lock,
            )
        ) or any(not root.is_dir() for root in paths.runtime_roots):
            raise ValueError("NATIVE_CONFIGURATION_PATHS_INVALID")
        if not native_qualification and any(
            item is not None and not item.is_file()
            for item in (paths.qualification, paths.qualification_work)
        ):
            raise ValueError("NATIVE_QUALIFICATION_PATHS_INVALID")
        resolved._native_mcp_paths = paths
    return resolved
