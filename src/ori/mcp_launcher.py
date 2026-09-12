"""Typed, provenance-safe BloodHound MCP launcher configuration."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
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
_UV_TOOL_NAMES = frozenset({"uv", "uvx"})
_UV_TOOL_OVERRIDE_ENV = {
    "uv": "ORI_UV_EXECUTABLE",
    "uvx": "ORI_UVX_EXECUTABLE",
}


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

    def provenance(
        self,
        *,
        uv_version: str,
        uv_executable: str,
        mcp_runtime_executable: str,
    ) -> dict[str, str | None]:
        return {
            "mcp_launcher": self.launcher,
            "mcp_source": self.source,
            "mcp_revision": self.revision,
            "mcp_executable": (
                self.executable if self.launcher == MCP_LAUNCHER_UVX_GIT else "main.py"
            ),
            "uv_version": uv_version,
            "uv_executable": uv_executable,
            "mcp_runtime_executable": mcp_runtime_executable,
        }


@dataclass(frozen=True)
class MCPLaunchSpec:
    """Shell-free subprocess specification derived from a validated launcher."""

    command: str
    args: tuple[str, ...]
    cwd: str | None


@dataclass(frozen=True)
class NativeMCPLauncherConfig:
    """Explicit offline-installed native source and interpreter, never an installer."""

    implementation_id: str
    checkout: Path
    python_executable: Path
    runtime_roots: tuple[Path, ...] = ()
    runtime_fingerprint: str | None = None
    dependency_lock: Path | None = None
    dependency_lock_fingerprint: str | None = None


def prepare_native_mcp_launch(config: NativeMCPLauncherConfig) -> tuple[MCPLaunchSpec, dict]:
    """Verify pinned source bytes and prepare argv without starting the server.

    Interpreter identity is observed, not qualification of its dependency/import
    closure. Callers must separately qualify that closure and recheck at spawn
    and session completion. This is not a sandbox against concurrent local writes.
    """
    from .eval.v2.fingerprint import canonical_sha256
    from .eval.v2.native_mcp_profiles import get_native_implementation

    if os.name != "posix":
        raise ValueError("NATIVE_LAUNCH_PLATFORM_UNQUALIFIED")
    source = get_native_implementation(config.implementation_id)
    if bool(config.runtime_roots) != (config.runtime_fingerprint is not None):
        raise ValueError("NATIVE_RUNTIME_BINDING_INCOMPLETE")
    if ((config.dependency_lock is None) != (config.dependency_lock_fingerprint is None)
            or config.dependency_lock is not None and not config.runtime_roots):
        raise ValueError("NATIVE_DEPENDENCY_BINDING_INCOMPLETE")
    runtime_fingerprint = None
    if config.runtime_roots:
        from .native_runtime import verify_native_runtime_roots

        runtime_fingerprint = verify_native_runtime_roots(
            config.runtime_roots, config.runtime_fingerprint,
        )
    checkout = config.checkout.resolve(strict=True)
    python = config.python_executable.absolute()
    if not checkout.is_dir() or not python.is_file() or not os.access(python, os.X_OK):
        raise ValueError("NATIVE_LAUNCH_PATH_INVALID")
    if python.is_relative_to(checkout) or python.resolve().is_relative_to(checkout):
        raise ValueError("NATIVE_RUNTIME_MUST_BE_EXTERNAL")
    for ancestor in (checkout, *checkout.parents):
        dotenv = ancestor / ".env"
        if dotenv.exists() or dotenv.is_symlink():
            raise ValueError("NATIVE_DOTENV_CONFIGURATION_FORBIDDEN")

    def git(*args):
        try:
            result = subprocess.run(
                ["git", "--no-replace-objects", "-c", "core.fsmonitor=false",
                 "-C", str(checkout), *args],
                capture_output=True, timeout=10, check=True,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError("NATIVE_SOURCE_VERIFICATION_FAILED") from exc
        return result.stdout

    if git("rev-parse", "HEAD").decode().strip() != source.revision:
        raise ValueError("NATIVE_SOURCE_REVISION_MISMATCH")
    expected = {}
    for record in git("ls-tree", "-rz", "--full-tree", source.revision).split(b"\0"):
        if not record:
            continue
        metadata, name = record.split(b"\t", 1)
        mode, kind, object_id = metadata.split()
        path = Path(os.fsdecode(name))
        if (kind != b"blob" or mode not in {b"100644", b"100755"}
                or path.is_absolute() or ".." in path.parts or ".git" in path.parts):
            raise ValueError("NATIVE_SOURCE_ENTRY_UNSUPPORTED")
        expected[path.as_posix()] = (mode, object_id)
    expected_directories = {
        parent.as_posix() for name in expected for parent in Path(name).parents
        if parent != Path(".")
    }

    def traversal_error(error):
        raise ValueError("NATIVE_SOURCE_TRAVERSAL_FAILED") from None

    observed = set()
    for directory, directories, files in os.walk(
        checkout, followlinks=False, onerror=traversal_error,
    ):
        root = Path(directory)
        if root == checkout:
            directories[:] = [name for name in directories if name != ".git"]
            files = [name for name in files if name != ".git"]
        if any((root / name).is_symlink() for name in (*directories, *files)):
            raise ValueError("NATIVE_SOURCE_SYMLINK_FORBIDDEN")
        if any(
            (root / name).relative_to(checkout).as_posix() not in expected_directories
            or not stat.S_ISDIR((root / name).lstat().st_mode)
            for name in directories
        ):
            raise ValueError("NATIVE_SOURCE_EXTRA_DIRECTORY")
        for name in files:
            path = root / name
            relative = path.relative_to(checkout).as_posix()
            if relative not in expected or not stat.S_ISREG(path.lstat().st_mode):
                raise ValueError("NATIVE_SOURCE_EXTRA_FILE")
            mode, object_id = expected[relative]
            content = path.read_bytes()
            digest = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content)
            executable = bool(path.stat().st_mode & 0o111)
            if digest.hexdigest().encode() != object_id or executable != (mode == b"100755"):
                raise ValueError("NATIVE_SOURCE_CONTENT_MISMATCH")
            observed.add(relative)
    if observed != set(expected) or source.entrypoint not in observed:
        raise ValueError("NATIVE_SOURCE_MISSING_FILE")
    spec = MCPLaunchSpec(str(python), ("-E", "-s", "-B", str(checkout / source.entrypoint)),
                         str(checkout))
    provenance = {
        "implementation_id": source.implementation_id, "source_revision": source.revision,
        "checkout": str(checkout), "python_executable": str(python),
        "interpreter_sha256": hashlib.sha256(python.read_bytes()).hexdigest(),
        "source_tree_fingerprint": canonical_sha256(
            {name: [mode.decode(), blob.decode()] for name, (mode, blob) in expected.items()},
        ),
        "source_bytes_verified": True, "runtime_qualified": False, "campaign_admitted": False,
        "runtime_content_fingerprint": runtime_fingerprint,
        "runtime_content_verified": runtime_fingerprint is not None,
    }
    return spec, provenance


def native_mcp_subprocess_env(
    implementation_id: str, *, connection: Mapping[str, str],
    sdk_defaults: Mapping[str, str],
) -> dict[str, str]:
    """Override SDK defaults explicitly; do not forward arbitrary parent values."""
    from .eval.v2.native_mcp_profiles import get_native_implementation

    source = get_native_implementation(implementation_id)
    if source.backend == "bhce":
        required = {"BLOODHOUND_DOMAIN", *source.credential_env_names}
        optional = {"BLOODHOUND_PORT", "BLOODHOUND_SCHEME", "BLOODHOUND_VERIFY_TLS"}
    else:
        prefix = "BLOODHOUND" if implementation_id == "mordavid" else "NEO4J"
        required = {f"{prefix}_URI", *source.credential_env_names}
        optional = {"REQUEST_TIMEOUT", "MAX_RETRIES", "LOG_LEVEL"} if prefix == "NEO4J" else set()
    if (set(connection) - required - optional or not required <= set(connection)
            or any(not isinstance(value, str) or not value.strip() or "\0" in value
                   for value in connection.values())):
        raise ValueError("NATIVE_CONNECTION_ENVIRONMENT_INVALID")
    # stdio_client merges SDK defaults before explicit values. Empty overrides
    # prevent inheritance without modifying this process's environment.
    environment = {key: "" for key in sdk_defaults}
    environment.update({"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1"})
    environment.update(connection)
    return environment


@dataclass(frozen=True)
class MCPLauncherRuntime:
    """One resolved launcher runtime reused for provenance and process execution."""

    launch_spec: MCPLaunchSpec
    uv_executable: str
    uv_version: str

    def provenance(self, config: MCPLauncherConfig) -> dict[str, str | None]:
        return config.provenance(
            uv_version=self.uv_version,
            uv_executable=self.uv_executable,
            mcp_runtime_executable=self.launch_spec.command,
        )


def resolve_uv_tool(
    tool: Literal["uv", "uvx"],
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Resolve a trusted uv tool from PATH or an explicit absolute override."""

    if tool not in _UV_TOOL_NAMES:  # pragma: no cover - protected by the type boundary.
        raise ValueError(f"Unsupported uv tool {tool!r}.")
    effective_environ = os.environ if environ is None else environ
    override_name = _UV_TOOL_OVERRIDE_ENV[tool]
    override = effective_environ.get(override_name)
    if override:
        candidate = Path(override)
        if not candidate.is_absolute():
            raise RuntimeError(f"{override_name} must be an absolute executable path.")
        if not candidate.is_file() or not os.access(candidate, os.X_OK):
            raise RuntimeError(f"{override_name} does not reference an executable file.")
        return str(candidate)
    discovered = shutil.which(tool, path=effective_environ.get("PATH"))
    if discovered:
        return str(Path(discovered).absolute())
    raise RuntimeError(
        f"Unable to locate executable {tool!r}. Expose it through PATH or set "
        f"{override_name} to an operator-approved absolute path."
    )


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
            command=resolve_uv_tool("uv"),
            args=("--directory", str(config.mcp_dir), "run", "main.py"),
            cwd=str(config.mcp_dir),
        )
    if config.launcher == MCP_LAUNCHER_UVX_GIT:
        if not config.source or not config.executable:
            raise ValueError("uvx_git launcher requires source and executable.")
        return MCPLaunchSpec(
            command=resolve_uv_tool("uvx"),
            args=("--from", config.source, config.executable),
            cwd=None,
        )
    raise ValueError(f"Unsupported MCP launcher {config.launcher!r}.")


def detect_uv_version(uv_command: str | None = None) -> str:
    """Return the exact uv version string used for launcher provenance."""

    command = uv_command or resolve_uv_tool("uv")
    try:
        completed = subprocess.run(
            [command, "--version"],
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


def resolve_mcp_launcher_runtime(config: MCPLauncherConfig) -> MCPLauncherRuntime:
    """Resolve and fingerprint the exact commands that will launch MCP."""

    launch_spec = build_mcp_launch_spec(config)
    uv_executable = (
        launch_spec.command
        if config.launcher == MCP_LAUNCHER_LOCAL_CHECKOUT
        else resolve_uv_tool("uv")
    )
    return MCPLauncherRuntime(
        launch_spec=launch_spec,
        uv_executable=uv_executable,
        uv_version=detect_uv_version(uv_executable),
    )
