"""Strict, path-resolved configuration for explicit V2 model campaigns."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import Field, field_validator, model_validator

from .schema import PROTOCOL_VERSION, StrictModel, Track

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


class V2TrackArtifactPaths(StrictModel):
    public: str
    oracles: str
    candidates: str
    live_certification: str


class V2SourcePaths(StrictModel):
    manifest: str
    archive: str


class V2HealthConfig(StrictModel):
    timeout_seconds: float = Field(default=60.0, strict=True, gt=0)
    poll_interval: float = Field(default=5.0, strict=True, gt=0)


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


class V2Defaults(StrictModel):
    concurrency: int = Field(default=1, strict=True, gt=0)
    runs_per_model: int = Field(default=1, strict=True, gt=0)
    bhce_url: str | None = None
    model_base_url: str | None = None
    reasoning_effort: ReasoningEffort | None = None
    max_infra_retries: int = Field(default=1, strict=True, ge=0)
    graph_page_size: int = Field(default=500, strict=True, gt=0, le=2000)
    health: V2HealthConfig = V2HealthConfig()
    mcp: V2MCPConfig | None = None

    @model_validator(mode="after")
    def certified_concurrency_is_serial(self) -> V2Defaults:
        if self.concurrency != 1:
            raise ValueError("certified v2 campaigns currently require concurrency=1")
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
    ] = "auto"
    structured_output_mode: StructuredOutputMode = "prompt_local_validation"
    max_output_tokens: int = Field(default=2048, strict=True, gt=0, le=32768)
    runs_per_model: int | None = Field(default=None, strict=True, gt=0)
    model_base_url: str | None = None
    mcp_tool_loop: (
        Literal[
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
        if (
            self.structured_output_mode == "json_schema"
            and self.provider not in {"openai", "openai-compat", "gemini"}
        ):
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

    @model_validator(mode="after")
    def exact_modes_and_models(self) -> V2CampaignConfig:
        if not self.modes or len(self.modes) != len(set(self.modes)):
            raise ValueError("v2 modes must be non-empty and unique")
        if set(self.tracks) != set(self.modes):
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
                raise ValueError(
                    "v2 MCP campaigns require defaults.mcp configuration"
                )
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


class ResolvedV2CampaignConfig(StrictModel):
    source_manifest: Path
    archive: Path
    tracks: dict[Track, ResolvedV2TrackPaths]
    output_dir: Path
    mcp_dir: Path | None
    config: V2CampaignConfig
    source_config_fingerprint: str


def _resolve(base: Path, raw: str) -> Path:
    path = Path(raw).expanduser()
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def load_v2_campaign_config(path: Path) -> ResolvedV2CampaignConfig:
    """Load a V2-only YAML file and resolve paths relative to that file."""

    from .fingerprint import canonical_sha256

    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid v2 campaign YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("v2 campaign config must be a YAML mapping")
    config = V2CampaignConfig.model_validate(raw)
    base = path.parent.resolve()
    resolved_tracks = {
        Track(track): ResolvedV2TrackPaths(
            public=_resolve(base, paths.public),
            oracles=_resolve(base, paths.oracles),
            candidates=_resolve(base, paths.candidates),
            live_certification=_resolve(base, paths.live_certification),
        )
        for track, paths in config.tracks.items()
    }
    resolved = ResolvedV2CampaignConfig(
        source_manifest=_resolve(base, config.source.manifest),
        archive=_resolve(base, config.source.archive),
        tracks=resolved_tracks,
        output_dir=_resolve(base, config.output_dir),
        mcp_dir=(
            _resolve(base, config.defaults.mcp.mcp_dir)
            if config.defaults.mcp is not None
            else None
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
                paths.candidates,
                paths.live_certification,
            )
        ),
    )
    missing = [str(item) for item in required if not item.exists()]
    if missing:
        raise ValueError("v2 campaign config paths do not exist: " + ", ".join(missing))
    if resolved.mcp_dir is not None and not resolved.mcp_dir.exists():
        raise ValueError("v2 campaign config paths do not exist: " + str(resolved.mcp_dir))
    if resolved.mcp_dir is not None and not resolved.mcp_dir.is_dir():
        raise ValueError("v2 MCP path is not a directory")
    return resolved
