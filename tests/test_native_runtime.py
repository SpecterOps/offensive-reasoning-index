"""Runtime byte changes cannot hide behind unchanged package metadata."""

import hashlib
import os
from types import SimpleNamespace

import pytest

from ori.native_runtime import (
    fingerprint_native_runtime_roots,
    inspect_native_python_startup,
    verify_native_dependency_lock,
    verify_native_runtime_roots,
)


@pytest.mark.parametrize("case", [
    "valid", "extra", "missing", "wrong_version", "duplicate_lock", "duplicate_install",
    "duplicate_metadata", "malformed_metadata", "legacy", "range", "editable", "changed_lock",
])
def test_native_dependency_lock_matches_complete_wheel_inventory(tmp_path, case):
    site = tmp_path / "site-packages"
    site.mkdir()
    package = site / "demo_pkg-1.0.dist-info"
    package.mkdir()
    metadata = package / "METADATA"
    metadata.write_text("Name: Demo_Pkg\nVersion: 1.0\n")
    lock = tmp_path / "requirements.lock"
    lock.write_text("# frozen environment\ndemo-pkg==1.0\n")
    if case == "extra":
        extra = site / "extra-2.dist-info"
        extra.mkdir()
        (extra / "METADATA").write_text("Name: extra\nVersion: 2\n")
    if case == "missing":
        lock.write_text("demo-pkg==1.0\nmissing==2\n")
    if case == "wrong_version":
        metadata.write_text("Name: Demo_Pkg\nVersion: 2\n")
    if case == "duplicate_lock":
        lock.write_text("demo-pkg==1.0\nDemo_Pkg==1.0\n")
    if case == "duplicate_install":
        extra = site / "duplicate.dist-info"
        extra.mkdir()
        (extra / "METADATA").write_text(metadata.read_text())
    if case == "duplicate_metadata":
        metadata.write_text("Name: Demo_Pkg\nName: other\nVersion: 1.0\n")
    if case == "malformed_metadata":
        metadata.write_text("Name: Demo_Pkg\nVersion: nonsense\n")
    if case == "legacy":
        (site / "legacy.egg-info").mkdir()
    if case == "range":
        lock.write_text("demo-pkg>=1\n")
    if case == "editable":
        lock.write_text("-e ./source\n")
    fingerprint = hashlib.sha256(lock.read_bytes()).hexdigest()
    if case == "changed_lock":
        lock.write_text("demo-pkg==2\n")
    if case == "valid":
        result = verify_native_dependency_lock(lock, fingerprint, site_packages=(site,))
        assert result["dependency_inventory_verified"]
        assert result["installed_distributions"] == {"demo-pkg": "1.0"}
        assert result["dependency_lock_fingerprint"] == fingerprint
    else:
        with pytest.raises(ValueError, match="NATIVE_DEPENDENCY_"):
            verify_native_dependency_lock(lock, fingerprint, site_packages=(site,))


@pytest.mark.parametrize("change", ["body", "bytecode", "hook", "extra", "mode", "removed"])
def test_frozen_runtime_detects_content_and_layout_changes(tmp_path, change):
    root = tmp_path / "runtime"
    root.mkdir()
    body = root / "package.py"
    body.write_text("VALUE = 1\n")
    (root / "METADATA").write_text("Name: fixture\nVersion: 1\n")
    frozen = fingerprint_native_runtime_roots((root,))
    assert verify_native_runtime_roots((root,), frozen) == frozen
    if change == "body":
        body.write_text("VALUE = 2\n")
    elif change == "mode":
        body.chmod(0o755)
    elif change == "removed":
        body.unlink()
    else:
        (root / {"bytecode": "package.pyc", "hook": "injection.pth", "extra": "extra.py"}[
            change
        ]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="CONTENT_MISMATCH"):
        verify_native_runtime_roots((root,), frozen)


def test_frozen_runtime_symlinks_and_fail_closed_inputs(tmp_path):
    root = tmp_path / "runtime"
    root.mkdir()
    target = root / "library"
    target.write_text("bytes")
    link = root / "alias"
    link.symlink_to(target)
    frozen = fingerprint_native_runtime_roots((root,))
    target.write_text("changed")
    assert fingerprint_native_runtime_roots((root,)) != frozen
    link.unlink()
    redirect = tmp_path / "redirect"
    redirect.symlink_to(target)
    link.symlink_to(redirect)
    with pytest.raises(ValueError, match="EXTERNAL_SYMLINK"):
        fingerprint_native_runtime_roots((root,))
    link.unlink()
    outside = tmp_path / "outside"
    outside.write_text("private")
    link.symlink_to(outside)
    with pytest.raises(ValueError, match="EXTERNAL_SYMLINK"):
        fingerprint_native_runtime_roots((root,))
    link.unlink()
    os.mkfifo(root / "fifo")
    with pytest.raises(ValueError, match="SPECIAL_FILE"):
        fingerprint_native_runtime_roots((root,))
    with pytest.raises(ValueError, match="ROOTS_OVERLAP"):
        fingerprint_native_runtime_roots((root, root))
    with pytest.raises(ValueError, match="FINGERPRINT_INVALID"):
        verify_native_runtime_roots((root,), "")


def test_frozen_runtime_rejects_unreadable_traversal(tmp_path, monkeypatch):
    def walk(*args, onerror, **kwargs):
        onerror(PermissionError())
    monkeypatch.setattr(os, "walk", walk)
    with pytest.raises(ValueError, match="TRAVERSAL_FAILED"):
        fingerprint_native_runtime_roots((tmp_path,))


@pytest.mark.parametrize("case", [
    "valid", "pth", "customize", "editable", "system_site", "external_path",
    "identity", "changed", "stderr", "timeout", "customize_package", "archive",
    "isolated_stdlib",
    "dependency_valid", "dependency_missing", "dependency_alias",
])
def test_native_startup_qualification_is_bounded_and_fail_closed(tmp_path, monkeypatch, case):
    import subprocess

    root = tmp_path / "runtime"
    venv = root / "venv"
    (venv / "bin").mkdir(parents=True)
    python = venv / "bin" / "python"
    python.write_text("offline fixture")
    (venv / "pyvenv.cfg").write_text(
        "include-system-site-packages = " + ("true" if case == "system_site" else "false")
    )
    checkout = tmp_path / "source"
    checkout.mkdir()
    stdlib = tmp_path / "stdlib"
    if case == "isolated_stdlib":
        stdlib.mkdir()
    dependency_options = {}
    site = venv / "lib" / "python3.12" / "site-packages"
    if case.startswith("dependency_"):
        metadata = site / "demo-1.dist-info"
        metadata.mkdir(parents=True)
        (metadata / "METADATA").write_text("Name: demo\nVersion: 1\n")
        lock = tmp_path / "requirements.lock"
        lock.write_text("missing==1\n" if case == "dependency_missing" else "demo==1\n")
        if case == "dependency_alias":
            target = root / "packages"
            site.rename(target)
            site.symlink_to(target, target_is_directory=True)
        dependency_options = {
            "dependency_lock": lock,
            "dependency_lock_fingerprint": hashlib.sha256(lock.read_bytes()).hexdigest(),
        }
    if case == "pth":
        (root / "escape.pth").write_text("import dangerous")
    if case == "customize":
        (root / "sitecustomize.cpython-312.pyc").write_bytes(b"bytecode")
    if case == "customize_package":
        (root / "sitecustomize").mkdir()
        (root / "sitecustomize" / "__init__.py").write_text("raise RuntimeError('hook')")
    if case == "archive":
        (root / "python312.zip").write_bytes(b"opaque archive")
    if case == "editable":
        (root / "direct_url.json").write_text('{"dir_info":{"editable":true}}')
    expected = fingerprint_native_runtime_roots((root,))
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        assert kwargs["timeout"] == 15
        assert kwargs["env"] == {"PATH": os.defpath}
        assert kwargs["capture_output"]
        if case == "timeout":
            raise subprocess.TimeoutExpired(argv, 15)
        normal = "-S" not in argv
        if normal and case == "changed":
            python.write_text("runtime drift")
        if case == "isolated_stdlib":
            paths = [str(stdlib), str(root)]
            base_prefix = str(tmp_path)
        elif case == "external_path":
            paths = [str(root)] if not normal else [str(tmp_path)]
            base_prefix = str(root)
        else:
            paths = [str(site)] if case.startswith("dependency_") else [str(root)]
            base_prefix = str(root)
        return SimpleNamespace(
            stderr=b"private diagnostic" if case == "stderr" else b"",
            stdout=repr({
                "paths": paths,
                "prefix": str(root if case == "identity" or not normal else venv),
                "base_prefix": base_prefix, "version": [3, 12, 0],
                "abi": "cpython-312", "platform": "fixture",
            }).encode(),
        )

    monkeypatch.setattr(subprocess, "run", run)
    if case in {"valid", "dependency_valid", "dependency_alias", "isolated_stdlib"}:
        result = inspect_native_python_startup(
            python=python, checkout=checkout, roots=(root,), expected_fingerprint=expected,
            **dependency_options,
        )
        assert result["python_startup_verified"]
        assert not result["runtime_qualified"] and not result["campaign_admitted"]
        assert "-S" in calls[0] and "-s" in calls[1]
        if case in {"dependency_valid", "dependency_alias"}:
            assert result["dependencies"]["installed_distributions"] == {"demo": "1"}
    else:
        with pytest.raises(ValueError, match="NATIVE_(RUNTIME|DEPENDENCY)_"):
            inspect_native_python_startup(
                python=python, checkout=checkout, roots=(root,), expected_fingerprint=expected,
                **dependency_options,
            )
        if case in {"pth", "customize", "customize_package", "archive", "editable", "system_site"}:
            assert not calls
