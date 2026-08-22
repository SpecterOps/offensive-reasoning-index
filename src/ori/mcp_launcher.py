"""Typed, provenance-safe BloodHound MCP launcher configuration."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

MCP_LAUNCHER_LOCAL_CHECKOUT = "local_checkout"
MCP_LAUNCHER_UVX_GIT = "uvx_git"
MCP_LAUNCHERS = {MCP_LAUNCHER_LOCAL_CHECKOUT, MCP_LAUNCHER_UVX_GIT}

CANONICAL_BLOODHOUND_MCP_GIT_PREFIX = "git+https://github.com/mwnickerson/bloodhound_mcp@"
CANONICAL_BLOODHOUND_MCP_EXECUTABLE = "bloodhound-mcp"
_FULL_GIT_SOURCE_RE = re.compile(
    rf"\A{re.escape(CANONICAL_BLOODHOUND_MCP_GIT_PREFIX)}(?P<revision>[0-9a-f]{{40}})\Z"
)


@dataclass(frozen=True)
class MCPLauncherConfig:
    """One validated way to start the BloodHound MCP stdio server."""

    launcher: Literal["local_checkout", "uvx_git"]
    mcp_dir: Path | None = None
    source: str | None = None
    executable: str | None = None
    revision: str | None = None

    def __post_init__(self) -> None:
        if self.launcher == MCP_LAUNCHER_LOCAL_CHECKOUT:
            if self.mcp_dir is None:
                raise ValueError("MCP local_checkout launcher requires mcp_dir.")
            if not isinstance(self.mcp_dir, Path):
                raise ValueError("MCP local_checkout mcp_dir must be a Path.")
            if self.source is not None or self.executable is not None or self.revision is not None:
                raise ValueError(
                    "MCP local_checkout launcher must not define source, executable, or revision."
                )
            return
        if self.launcher != MCP_LAUNCHER_UVX_GIT:
            supported = ", ".join(sorted(MCP_LAUNCHERS))
            raise ValueError(
                f"Unsupported MCP launcher {self.launcher!r}. Supported values: {supported}"
            )
        if not all(
            isinstance(value, str) for value in (self.source, self.executable, self.revision)
        ):
            raise ValueError("MCP uvx_git launcher requires source, executable, and revision.")
        match = _FULL_GIT_SOURCE_RE.fullmatch(self.source)
        if match is None or match.group("revision") != self.revision:
            raise ValueError("MCP uvx_git source and full revision must match exactly.")
        if self.executable != CANONICAL_BLOODHOUND_MCP_EXECUTABLE:
            raise ValueError(
                f"MCP uvx_git executable must be exactly {CANONICAL_BLOODHOUND_MCP_EXECUTABLE!r}."
            )

    @classmethod
    def local_checkout(cls, mcp_dir: Path) -> MCPLauncherConfig:
        return cls(
            launcher=MCP_LAUNCHER_LOCAL_CHECKOUT,
            mcp_dir=mcp_dir.resolve(),
        )

    @classmethod
    def uvx_git(cls, *, source: str, executable: str) -> MCPLauncherConfig:
        match = _FULL_GIT_SOURCE_RE.fullmatch(source)
        if match is None:
            raise ValueError(
                "MCP uvx_git source must be the canonical "
                f"{CANONICAL_BLOODHOUND_MCP_GIT_PREFIX}<40-character lowercase hex commit>. "
                "Branches, tags, abbreviated SHAs, mutable refs, and alternate repositories "
                "are not supported for benchmark profiles."
            )
        if executable != CANONICAL_BLOODHOUND_MCP_EXECUTABLE:
            raise ValueError(
                f"MCP uvx_git executable must be exactly {CANONICAL_BLOODHOUND_MCP_EXECUTABLE!r}."
            )
        revision = match.group("revision")
        return cls(
            launcher=MCP_LAUNCHER_UVX_GIT,
            source=f"{CANONICAL_BLOODHOUND_MCP_GIT_PREFIX}{revision}",
            executable=CANONICAL_BLOODHOUND_MCP_EXECUTABLE,
            revision=revision,
        )

    def to_config(self) -> dict[str, str]:
        if self.launcher == MCP_LAUNCHER_LOCAL_CHECKOUT:
            if self.mcp_dir is None:  # pragma: no cover - protected by constructors.
                raise ValueError("local_checkout launcher requires mcp_dir.")
            return {"launcher": self.launcher, "mcp_dir": str(self.mcp_dir)}
        if not self.source or not self.executable or not self.revision:
            raise ValueError("uvx_git launcher requires source, executable, and revision.")
        return {
            "launcher": self.launcher,
            "source": self.source,
            "executable": self.executable,
        }

    def provenance(self, *, uv_version: str) -> dict[str, str | None]:
        return {
            "mcp_launcher": self.launcher,
            "mcp_source": self.source,
            "mcp_revision": self.revision,
            "mcp_executable": (
                self.executable if self.launcher == MCP_LAUNCHER_UVX_GIT else "main.py"
            ),
            "uv_version": uv_version,
        }


@dataclass(frozen=True)
class MCPLaunchSpec:
    """Shell-free subprocess specification derived from a validated launcher."""

    command: str
    args: tuple[str, ...]
    cwd: str | None


def resolve_mcp_launcher_config(
    section: Mapping[str, Any] | None,
    *,
    config_dir: Path,
    mcp_dir_override: str | Path | None = None,
) -> MCPLauncherConfig:
    """Resolve legacy/local or pinned-uvx launcher settings from an MCP mapping."""

    raw_section: Mapping[str, Any] = section or {}
    if not isinstance(raw_section, Mapping):
        raise ValueError("mcp must be a mapping when present.")
    forbidden_process_fields = sorted(
        key for key in ("command", "args", "shell") if key in raw_section
    )
    if forbidden_process_fields:
        raise ValueError(
            "MCP launcher configuration must not define process fields: "
            + ", ".join(forbidden_process_fields)
            + "."
        )

    if mcp_dir_override is not None:
        override_path = Path(mcp_dir_override)
        if not override_path.is_absolute():
            override_path = config_dir / override_path
        return MCPLauncherConfig.local_checkout(override_path)

    raw_launcher = raw_section.get("launcher", MCP_LAUNCHER_LOCAL_CHECKOUT)
    if not isinstance(raw_launcher, str) or raw_launcher not in MCP_LAUNCHERS:
        supported = ", ".join(sorted(MCP_LAUNCHERS))
        raise ValueError(
            f"Unsupported MCP launcher {raw_launcher!r}. Supported values: {supported}"
        )

    if raw_launcher == MCP_LAUNCHER_UVX_GIT:
        if raw_section.get("mcp_dir") is not None:
            raise ValueError("MCP uvx_git configuration must not define mcp_dir.")
        if raw_section.get("revision") is not None:
            raise ValueError("MCP uvx_git revision is derived from source and must not be set.")
        source = raw_section.get("source")
        executable = raw_section.get("executable")
        if not isinstance(source, str) or not isinstance(executable, str):
            raise ValueError("MCP uvx_git configuration requires string source and executable.")
        return MCPLauncherConfig.uvx_git(source=source, executable=executable)

    if any(raw_section.get(key) is not None for key in ("source", "executable", "revision")):
        raise ValueError(
            "MCP local_checkout configuration must not define uvx_git source, executable, "
            "or revision."
        )
    raw_mcp_dir = raw_section.get("mcp_dir", "../bloodhound-mcp")
    if not isinstance(raw_mcp_dir, str | Path) or not str(raw_mcp_dir).strip():
        raise ValueError("MCP local_checkout mcp_dir must be a non-empty path.")
    mcp_path = Path(raw_mcp_dir)
    if not mcp_path.is_absolute():
        mcp_path = config_dir / mcp_path
    return MCPLauncherConfig.local_checkout(mcp_path)


def build_mcp_launch_spec(config: MCPLauncherConfig) -> MCPLaunchSpec:
    """Build the exact argv without invoking a shell."""

    if config.launcher == MCP_LAUNCHER_LOCAL_CHECKOUT:
        if config.mcp_dir is None:
            raise ValueError("local_checkout launcher requires mcp_dir.")
        return MCPLaunchSpec(
            command="uv",
            args=("--directory", str(config.mcp_dir), "run", "main.py"),
            cwd=str(config.mcp_dir),
        )
    if config.launcher == MCP_LAUNCHER_UVX_GIT:
        if not config.source or not config.executable:
            raise ValueError("uvx_git launcher requires source and executable.")
        return MCPLaunchSpec(
            command="uvx",
            args=("--from", config.source, config.executable),
            cwd=None,
        )
    raise ValueError(f"Unsupported MCP launcher {config.launcher!r}.")


def detect_uv_version() -> str:
    """Return the exact uv version string used for launcher provenance."""

    try:
        completed = subprocess.run(
            ["uv", "--version"],
            text=True,
            capture_output=True,
            timeout=5.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"Unable to capture uv version: {exc}") from exc
    version = completed.stdout.strip()
    if completed.returncode != 0 or not version.startswith("uv "):
        detail = completed.stderr.strip() or version or f"exit {completed.returncode}"
        raise RuntimeError(f"Unable to capture uv version: {detail}")
    return version
