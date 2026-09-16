"""Offline byte binding for operator-frozen native Python runtime roots.

This verifies declared bytes, not discovery of the interpreter's import closure
or backend safety. Campaign admission must establish those separately.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import stat
import subprocess
from email.parser import Parser
from pathlib import Path

from packaging.version import InvalidVersion, Version


def fingerprint_native_runtime_roots(roots: tuple[Path, ...]) -> str:
    """Hash complete, disjoint trees without running Python or package hooks.

    Root ordering is meaningful and paths are private. External symlinks, special
    files and unreadable traversal fail closed. No cache or metadata-only shortcut
    can overlook a changed package body. This is not a concurrent-write sandbox.
    """
    from .eval.v2.fingerprint import canonical_sha256

    if not roots or any(not isinstance(root, Path) or not root.is_absolute() for root in roots):
        raise ValueError("NATIVE_RUNTIME_ROOTS_INVALID")
    resolved = tuple(root.resolve(strict=True) for root in roots)
    if any(not root.is_dir() or root == Path(root.anchor) for root in resolved):
        raise ValueError("NATIVE_RUNTIME_ROOTS_INVALID")
    if any(left.is_relative_to(right) for i, left in enumerate(resolved)
           for j, right in enumerate(resolved) if i != j):
        raise ValueError("NATIVE_RUNTIME_ROOTS_OVERLAP")

    def error(_):
        raise ValueError("NATIVE_RUNTIME_TRAVERSAL_FAILED") from None

    inventory = []
    try:
        for index, root in enumerate(resolved):
            for directory, directories, files in os.walk(root, followlinks=False, onerror=error):
                parent = Path(directory)
                for name in sorted((*directories, *files)):
                    path = parent / name
                    mode = path.lstat().st_mode
                    key = [index, path.relative_to(root).as_posix()]
                    if stat.S_ISLNK(mode):
                        spelling = os.readlink(path)
                        raw_target = Path(spelling)
                        lexical_target = (
                            raw_target if raw_target.is_absolute() else parent / raw_target
                        )
                        # Never follow an external redirect that happens to point
                        # back into the tree. Parent traversal can similarly hide
                        # an intermediate symlink and is intentionally unsupported.
                        if ".." in raw_target.parts or not any(
                            lexical_target.is_relative_to(candidate) for candidate in resolved
                        ):
                            raise ValueError("NATIVE_RUNTIME_EXTERNAL_SYMLINK")
                        target = path.resolve(strict=True)
                        if not any(target.is_relative_to(candidate) for candidate in resolved):
                            raise ValueError("NATIVE_RUNTIME_EXTERNAL_SYMLINK")
                        inventory.append([key, "symlink", spelling, str(target)])
                    elif stat.S_ISDIR(mode):
                        inventory.append([key, "directory"])
                    elif stat.S_ISREG(mode):
                        digest = hashlib.sha256()
                        with path.open("rb") as stream:
                            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                                digest.update(chunk)
                        inventory.append([key, "file", bool(mode & 0o111), digest.hexdigest()])
                    else:
                        raise ValueError("NATIVE_RUNTIME_SPECIAL_FILE")
    except (OSError, RuntimeError):
        raise ValueError("NATIVE_RUNTIME_SCAN_FAILED") from None
    return canonical_sha256({
        "version": "native-runtime-roots-v1",
        "roots": [str(root) for root in resolved],
        "entries": sorted(inventory, key=lambda entry: entry[0]),
    })


def verify_native_runtime_roots(roots: tuple[Path, ...], expected_fingerprint: str) -> str:
    """Compare with an independently frozen value, never self-admit a new tree."""
    if not isinstance(expected_fingerprint, str) or not re.fullmatch(
        r"[0-9a-f]{64}", expected_fingerprint,
    ):
        raise ValueError("NATIVE_RUNTIME_FINGERPRINT_INVALID")
    observed = fingerprint_native_runtime_roots(roots)
    if observed != expected_fingerprint:
        raise ValueError("NATIVE_RUNTIME_CONTENT_MISMATCH")
    return observed


def inspect_native_python_startup(
    *, python: Path, checkout: Path, roots: tuple[Path, ...], expected_fingerprint: str,
    dependency_lock: Path | None = None, dependency_lock_fingerprint: str | None = None,
) -> dict:
    """Check a restricted venv's initial import paths without starting native MCP.

    This is an explicit interpreter probe, separate from no-process preparation.
    Frozen bytes are operator-trusted code, not a sandbox or a dependency solver.
    """
    verify_native_runtime_roots(roots, expected_fingerprint)
    resolved = tuple(root.resolve(strict=True) for root in roots)
    checkout = checkout.resolve(strict=True)
    python = python.absolute()
    venv = python.parent.parent

    def contained(path):
        return any(path.is_relative_to(root) for root in resolved)

    if (python.parent.name != "bin" or not contained(python)
            or not contained(python.resolve(strict=True)) or not contained(venv)):
        raise ValueError("NATIVE_RUNTIME_VENV_REQUIRED")
    try:
        config = {}
        for line in (venv / "pyvenv.cfg").read_text().splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            key, value = line.split("=", 1)
            key = key.strip().lower()
            if key in config:
                raise ValueError("duplicate venv setting")
            config[key] = value.strip()
        if config.get("include-system-site-packages", "").lower() != "false":
            raise ValueError("system site packages")
    except (OSError, ValueError):
        raise ValueError("NATIVE_RUNTIME_VENV_CONFIGURATION_INVALID") from None

    def traversal_error(_):
        raise ValueError("NATIVE_RUNTIME_TRAVERSAL_FAILED") from None

    # Inspect before either interpreter startup. -s still executes venv .pth and
    # sitecustomize, and -B still reads existing bytecode.
    for root in (*resolved, checkout):
        for directory, directories, files in os.walk(
            root, followlinks=False, onerror=traversal_error,
        ):
            for name in (*directories, *files):
                if (name.endswith((".pth", ".egg-link"))
                        or name.split(".")[0] in {"sitecustomize", "usercustomize"}):
                    raise ValueError("NATIVE_RUNTIME_STARTUP_HOOK_UNSUPPORTED")
                if name.endswith((".zip", ".egg", ".pyz")):
                    raise ValueError("NATIVE_RUNTIME_IMPORT_ARCHIVE_UNSUPPORTED")
                if name == "direct_url.json":
                    try:
                        metadata = json.loads((Path(directory) / name).read_text())
                        if not isinstance(metadata, dict):
                            raise ValueError("metadata shape")
                        info = metadata.get("dir_info", {})
                        if not isinstance(info, dict) or info.get("editable", False) is not False:
                            raise ValueError("editable metadata")
                    except (OSError, ValueError):
                        raise ValueError("NATIVE_RUNTIME_EDITABLE_UNSUPPORTED") from None

    script = (
        "import sys;print(repr(dict(paths=sys.path,prefix=sys.prefix,"
        "base_prefix=sys.base_prefix,version=list(sys.version_info[:3]),"
        "abi=sys.implementation.cache_tag,platform=sys.platform)))"
    )

    def probe(flags, *, isolated_external_paths: frozenset[str] | None):
        try:
            result = subprocess.run(
                [str(python), *flags, "-c", script], cwd=checkout,
                env={"PATH": os.defpath}, capture_output=True, timeout=15, check=True,
            )
            if result.stderr or len(result.stdout) > 65536:
                raise ValueError("unexpected probe output")
            observation = ast.literal_eval(result.stdout.decode("utf-8"))
            if not isinstance(observation, dict) or set(observation) != {
                "paths", "prefix", "base_prefix", "version", "abi", "platform",
            }:
                raise ValueError("probe shape")
            base_prefix = Path(observation["base_prefix"])
            if not base_prefix.is_absolute():
                raise ValueError("relative base prefix")
            paths = observation["paths"]
            if not isinstance(paths, list) or not paths:
                raise ValueError("probe paths")
            normalized = []
            site_packages = []
            for item in paths:
                if not isinstance(item, str) or (item and not Path(item).is_absolute()):
                    raise ValueError("relative probe path")
                path = Path(item).resolve() if item else checkout
                if path != checkout and not contained(path):
                    # A venv necessarily imports its base interpreter's stdlib.
                    # The isolated invocation is the trusted baseline for that
                    # unavoidable external surface; normal startup may not add
                    # any other external import location.
                    if (isolated_external_paths is None
                            and not path.is_relative_to(base_prefix)):
                        raise ValueError("external isolated probe path")
                    if (isolated_external_paths is not None
                            and str(path) not in isolated_external_paths):
                        raise ValueError("external normal probe path")
                if path.exists() and not path.is_dir():
                    raise ValueError("archive import path")
                normalized.append(str(path))
                if item and Path(item).name == "site-packages":
                    site_packages.append(str(path))
            observation["original_paths"] = paths
            observation["paths"] = normalized
            observation["site_packages"] = site_packages
            return observation
        except (OSError, ValueError, SyntaxError, subprocess.SubprocessError):
            raise ValueError("NATIVE_RUNTIME_STARTUP_PROBE_FAILED") from None

    isolated = probe(("-I", "-S", "-B"), isolated_external_paths=None)
    isolated_external_paths = frozenset(
        path for path in isolated["paths"]
        if path != str(checkout) and not contained(Path(path))
    )
    normal = probe(("-E", "-s", "-B"), isolated_external_paths=isolated_external_paths)
    if (normal["prefix"] != str(venv) or normal["prefix"] == normal["base_prefix"]
            or any(normal[key] != isolated[key] for key in ("version", "abi", "platform"))):
        raise ValueError("NATIVE_RUNTIME_STARTUP_IDENTITY_MISMATCH")
    dependencies = None
    if (dependency_lock is None) != (dependency_lock_fingerprint is None):
        raise ValueError("NATIVE_DEPENDENCY_BINDING_INCOMPLETE")
    if dependency_lock is not None:
        dependencies = verify_native_dependency_lock(
            dependency_lock, dependency_lock_fingerprint,
            site_packages=tuple(Path(path) for path in normal["site_packages"]),
        )
    verify_native_runtime_roots(roots, expected_fingerprint)
    return {
        "runtime_content_fingerprint": expected_fingerprint,
        "python_startup": normal,
        "python_isolated_startup": isolated,
        "runtime_roots": [str(root) for root in roots],
        "python_startup_verified": True,
        "dependencies": dependencies,
        "runtime_qualified": False,
        "campaign_admitted": False,
    }


def verify_native_dependency_lock(
    lock: Path, expected_fingerprint: str, *, site_packages: tuple[Path, ...],
) -> dict:
    """Match an exact flat requirements lock to all installed wheel metadata.

    No resolution, installation, imports or version-range guessing. The runtime
    byte fingerprint separately binds distribution bodies, including bytecode.
    """
    from .eval.v2.fingerprint import canonical_sha256

    if not isinstance(expected_fingerprint, str) or not re.fullmatch(
        r"[0-9a-f]{64}", expected_fingerprint,
    ):
        raise ValueError("NATIVE_DEPENDENCY_FINGERPRINT_INVALID")
    try:
        content = lock.read_bytes()
        if hashlib.sha256(content).hexdigest() != expected_fingerprint:
            raise ValueError("NATIVE_DEPENDENCY_LOCK_CHANGED")
        expected = {}
        for line in content.decode("utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            match = re.fullmatch(
                r"([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)"
                r"==([A-Za-z0-9][A-Za-z0-9.!+_-]*)", line,
            )
            if match is None:
                raise ValueError("NATIVE_DEPENDENCY_LOCK_UNSUPPORTED")
            name = re.sub(r"[-_.]+", "-", match[1]).lower()
            if name in expected:
                raise ValueError("NATIVE_DEPENDENCY_LOCK_DUPLICATE")
            try:
                Version(match[2])
            except InvalidVersion:
                raise ValueError("NATIVE_DEPENDENCY_VERSION_INVALID") from None
            expected[name] = match[2]
        if not expected or not site_packages or len(set(site_packages)) != len(site_packages):
            raise ValueError("NATIVE_DEPENDENCY_INVENTORY_INVALID")
        observed = {}
        for directory in site_packages:
            for entry in directory.iterdir():
                if entry.name.endswith((".egg-info", ".egg-link", ".egg")):
                    raise ValueError("NATIVE_DEPENDENCY_INSTALL_UNSUPPORTED")
                if not entry.name.endswith(".dist-info"):
                    continue
                metadata = Parser().parsestr((entry / "METADATA").read_text(encoding="utf-8"))
                names, versions = metadata.get_all("Name", []), metadata.get_all("Version", [])
                if len(names) != 1 or len(versions) != 1:
                    raise ValueError("NATIVE_DEPENDENCY_METADATA_INVALID")
                if re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?", names[0]) is None:
                    raise ValueError("NATIVE_DEPENDENCY_METADATA_INVALID")
                try:
                    Version(versions[0])
                except InvalidVersion:
                    raise ValueError("NATIVE_DEPENDENCY_METADATA_INVALID") from None
                name = re.sub(r"[-_.]+", "-", names[0]).lower()
                if name in observed:
                    raise ValueError("NATIVE_DEPENDENCY_INSTALL_DUPLICATE")
                observed[name] = versions[0]
        if observed != expected:
            raise ValueError("NATIVE_DEPENDENCY_INVENTORY_MISMATCH")
    except (OSError, UnicodeError):
        raise ValueError("NATIVE_DEPENDENCY_READ_FAILED") from None
    return {
        "dependency_lock_fingerprint": expected_fingerprint,
        "installed_distributions": observed,
        "distribution_inventory_fingerprint": canonical_sha256(observed),
        "dependency_inventory_verified": True,
    }
