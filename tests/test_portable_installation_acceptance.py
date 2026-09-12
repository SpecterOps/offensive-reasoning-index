"""Driver contract tests; real wheel qualification is an explicit separate run."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from scripts import verify_portable_installation as portable


@pytest.fixture
def wheel(tmp_path: Path) -> Path:
    path = tmp_path / "offensive_reasoning_index-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("ori/__init__.py", "")
        archive.writestr("ori/cli.py", "def main(): pass\n")
        archive.writestr(
            "offensive_reasoning_index-0.1.0.dist-info/METADATA",
            "Metadata-Version: 2.3\nName: offensive-reasoning-index\nVersion: 0.1.0\n",
        )
        archive.writestr(
            "offensive_reasoning_index-0.1.0.dist-info/entry_points.txt",
            "[console_scripts]\nori = ori.cli:main\n",
        )
    return path


def _observed(tmp_path: Path, info: dict) -> dict:
    prefix = tmp_path / "wheel environment"
    site = prefix / "lib/python3.12/site-packages"
    package = site / "ori"
    package.mkdir(parents=True)
    (package / "__init__.py").touch()
    (package / "cli.py").write_text("def main(): pass\n")
    console = prefix / "bin/ori"
    console.parent.mkdir()
    console.write_text("console placeholder")
    console.chmod(0o700)
    return {
        "prefix": str(prefix),
        "base_prefix": str(tmp_path / "base python"),
        "system_site_packages": False,
        "package": str(package),
        "cli": str(package / "cli.py"),
        "console_script": str(console),
        "site_packages": [str(site)],
        "members": dict(info["members"]),
        "metadata": info["metadata"],
        "entry_points": info["entry_points"],
        "entry_point": "ori.cli:main",
        "version": "0.1.0",
        "python": "3.12.13",
        "platform": "test-only",
        "architecture": "test-only",
    }


def _status() -> dict:
    payload = {
        "schema_version": "ori-v2-campaign-status-v3",
        "protocol_version": "ori-eval-protocol-v2",
        "purpose": "official",
        "ranking_eligible": True,
        "lifecycle_state": "not_started",
        "observed_state": "not_started",
        "mode": None,
        "active_run": None,
        "active_phase": "not_started",
        "recovery_round": 0,
        "completed_tracks": [],
        "graph_fingerprint": None,
        "resume_allowed": False,
        "next_action": "run_readiness",
        "progress": {
            "expected_runs": 1,
            "expected_results": None,
            **dict.fromkeys(
                (
                    "runs_started",
                    "runs_reported",
                    "checkpointed_results",
                    "terminal_results",
                    "pending_infra_retries",
                    "completed_results",
                    "provider_attempts",
                    "tokens_input",
                    "tokens_output",
                    "total_tokens",
                ),
                0,
            ),
        },
        "runs": [
            {
                "track": "direct",
                "model": "portable-placeholder",
                "run_index": 1,
                "started": False,
                "report_present": False,
            }
        ],
        "tracks": [{"track": "direct", "expected_runs": 1, "completion_present": False}],
    }
    for record in (*payload["runs"], *payload["tracks"]):
        record.update(
            dict.fromkeys(
                (
                    "checkpointed_results",
                    "provider_attempts",
                    "tokens_input",
                    "tokens_output",
                    "total_tokens",
                ),
                0,
            )
        )
    return payload


@pytest.mark.parametrize("kind", ["directory", "file", "dangling-symlink"])
def test_existing_output_is_never_followed_or_overwritten(tmp_path, wheel, kind):
    output = tmp_path / "existing output"
    if kind == "directory":
        output.mkdir()
    elif kind == "file":
        output.write_text("preserve")
    else:
        output.symlink_to(tmp_path / "must not be created")
    with pytest.raises(portable.PortableFailure):
        portable.validate_arguments(Path(sys.executable), wheel, output)
    assert not (tmp_path / "must not be created").exists()
    assert output.is_symlink() if kind == "dangling-symlink" else output.exists()
    if kind == "file":
        assert output.read_text() == "preserve"


@pytest.mark.parametrize("argument", ["python", "wheel"])
def test_relative_input_paths_fail_before_output_creation(tmp_path, wheel, argument):
    interpreter = Path("relative/python") if argument == "python" else Path(sys.executable)
    wheel_path = Path(wheel.name) if argument == "wheel" else wheel
    output = tmp_path / "new output"
    with pytest.raises(portable.PortableFailure):
        portable.validate_arguments(interpreter, wheel_path, output)
    assert not output.exists()


def test_environment_is_allowlisted_and_disables_dotenv(tmp_path, monkeypatch):
    for name in ("OPENAI_API_KEY", "BLOODHOUND_TOKEN_KEY", "PYTHONPATH", "HTTPS_PROXY"):
        monkeypatch.setenv(name, "must-not-forward")
    env = portable.child_environment(Path(sys.executable), tmp_path)
    assert "must-not-forward" not in env.values()
    assert env["PYTHON_DOTENV_DISABLED"] == "1"
    assert Path(env["HOME"]).is_relative_to(tmp_path)
    assert env["PATH"] == str(Path(sys.executable).parent)
    assert not set(env) & {"OPENAI_API_KEY", "BLOODHOUND_TOKEN_KEY", "PYTHONPATH", "HTTPS_PROXY"}


@pytest.mark.parametrize("member", ["../escape.py", "/absolute.py", "ori/../escape.py"])
def test_unsafe_wheel_members_fail(tmp_path, wheel, member):
    with zipfile.ZipFile(wheel, "a") as archive:
        archive.writestr(member, "bad")
    with pytest.raises(portable.PortableFailure):
        portable.wheel_inventory(wheel)


def test_duplicate_and_malformed_wheels_fail(tmp_path, wheel, subtests):
    with subtests.test(case="duplicate member"):
        with pytest.warns(UserWarning, match="Duplicate"):
            with zipfile.ZipFile(wheel, "a") as archive:
                archive.writestr("ori/cli.py", "duplicate")
        with pytest.raises(portable.PortableFailure):
            portable.wheel_inventory(wheel)
    with subtests.test(case="not a zip"):
        broken = tmp_path / "broken.whl"
        broken.write_text("not a wheel")
        with pytest.raises(portable.PortableFailure):
            portable.wheel_inventory(broken)


@pytest.mark.parametrize(
    "corruption",
    [
        "missing",
        "extra",
        "hash",
        "entry-point",
        "metadata",
        "cli-origin",
        "package-origin",
        "system-site-packages",
        "not-venv",
    ],
)
def test_installed_package_drift_is_rejected(tmp_path, wheel, corruption):
    info = portable.wheel_inventory(wheel)
    observed = _observed(tmp_path, info)
    portable.verify_installation(info, observed)
    if corruption == "missing":
        observed["members"].pop("ori/cli.py")
    elif corruption == "extra":
        observed["members"]["ori/extra.py"] = "a" * 64
    elif corruption == "hash":
        observed["members"]["ori/cli.py"] = "b" * 64
    elif corruption == "entry-point":
        observed["entry_point"] = "foreign:main"
    elif corruption == "metadata":
        observed["metadata"] += "Changed: true\n"
    elif corruption == "cli-origin":
        observed["cli"] = str(tmp_path / "source checkout/ori/cli.py")
    elif corruption == "package-origin":
        observed["package"] = str(tmp_path / "source checkout/ori")
    elif corruption == "system-site-packages":
        observed["system_site_packages"] = True
    else:
        observed["base_prefix"] = observed["prefix"]
    with pytest.raises(portable.PortableFailure):
        portable.verify_installation(info, observed)


@pytest.mark.parametrize(
    "stderr",
    [
        "",
        "{prefix}not-json\n",
        "{prefix}{{}}\n",
        '{prefix}{{"events":[]}}\n{prefix}{{"events":[]}}\n',
    ],
)
def test_missing_malformed_or_duplicate_guard_summary_fails(stderr):
    with pytest.raises(portable.PortableFailure):
        portable.parse_guard(stderr.format(prefix=portable.GUARD_PREFIX))


@pytest.mark.parametrize(
    "field,value",
    [
        ("observed_state", "completed"),
        ("next_action", "execute"),
        ("resume_allowed", True),
        ("provider_attempts", 1),
        ("total_tokens", 1),
        ("checkpointed_results", 1),
    ],
)
def test_status_validation_rejects_false_not_started_claims(field, value):
    status = _status()
    portable.validate_status(status)
    target = status["progress"] if field in status["progress"] else status
    target[field] = value
    with pytest.raises(portable.PortableFailure):
        portable.validate_status(status)


def test_real_child_audit_guard_blocks_socket_and_process(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    result = portable.run_probe(
        "guard-self-test",
        [sys.executable, "-I", "-c", portable.BOOTSTRAP, "self-test"],
        portable.child_environment(Path(sys.executable), tmp_path),
        tmp_path,
        0,
        30,
        logs,
    )
    assert result["actual_exit"] == 0
    assert set(result["guard"]["events"]) == {"socket.connect", "subprocess.Popen"}


@pytest.mark.parametrize("fault", ["caught-network", "unexpected-exit", "timeout"])
def test_failed_child_preserves_failed_probe_and_logs(tmp_path, monkeypatch, fault):
    logs = tmp_path / "logs"
    logs.mkdir()
    stderr = (
        portable.GUARD_PREFIX
        + json.dumps(
            {
                "events": [],
                "blocked_ipv6_capability_probes": [],
            }
        )
        + "\n"
    )
    if fault == "caught-network":
        stderr = (
            portable.GUARD_PREFIX
            + json.dumps(
                {
                    "events": ["socket.connect"],
                    "blocked_ipv6_capability_probes": [],
                }
            )
            + "\n"
        )

    def fake_run(command, **kwargs):
        if fault == "timeout":
            raise subprocess.TimeoutExpired(command, 1, output=b"partial", stderr=b"partial error")
        return subprocess.CompletedProcess(
            command, 1 if fault == "unexpected-exit" else 0, b"ordinary output", stderr.encode()
        )

    monkeypatch.setattr(portable.subprocess, "run", fake_run)
    with pytest.raises(portable.PortableFailure) as error:
        portable.run_probe("normal", [sys.executable], {}, tmp_path, 0, 1, logs)
    assert error.value.probe["id"] == "normal"
    assert Path(error.value.probe["stdout_path"]).is_file()
    assert Path(error.value.probe["stderr_path"]).is_file()
    if fault == "timeout":
        assert Path(error.value.probe["stdout_path"]).read_text() == "partial"


def test_wheel_inventory_is_content_bound(wheel):
    info = portable.wheel_inventory(wheel)
    assert info["sha256"] == hashlib.sha256(wheel.read_bytes()).hexdigest()
    assert info["members"]["ori/cli.py"] == hashlib.sha256(b"def main(): pass\n").hexdigest()


@pytest.mark.parametrize("location", ["git-directory", "git-file", "target-venv"])
def test_qualification_output_must_be_outside_git_worktree_and_venv(tmp_path, wheel, location):
    root = tmp_path / "isolated boundary"
    root.mkdir()
    interpreter = Path(sys.executable)
    if location.startswith("git"):
        marker = root / ".git"
        if location == "git-directory":
            marker.mkdir()
        else:
            marker.write_text("gitdir: irrelevant")
    else:
        (root / "bin").mkdir()
        interpreter = root / "bin/python"
        interpreter.symlink_to(sys.executable)
    with pytest.raises(portable.PortableFailure):
        portable.validate_arguments(interpreter, wheel, root / "new output")
    assert not (root / "new output").exists()


@pytest.mark.parametrize(
    "path,value",
    [
        (("schema_version",), "old-schema"),
        (("protocol_version",), "old-protocol"),
        (("runs", 0, "model"), "wrong-model"),
        (("runs", 0, "run_index"), 2),
        (("runs", 0, "started"), True),
        (("runs", 0, "report_present"), True),
        (("tracks", 0, "expected_runs"), 2),
        (("tracks", 0, "completion_present"), True),
    ],
)
def test_status_validation_rejects_contradictory_inventory(path, value):
    status = _status()
    portable.validate_status(status)
    target = status
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(portable.PortableFailure):
        portable.validate_status(status)


@pytest.mark.parametrize("fault", ["seed", "identity", "extra-file", "json", "zip", "crc"])
def test_generation_artifact_faults_are_rejected(tmp_path, fault):
    root = tmp_path / "generated artifacts"
    root.mkdir()
    stem = "simple-v1-seed-1234"
    manifest_path = root / f"{stem}_manifest.json"
    payload = {
        "metadata": {
            "benchmark": "simple",
            "benchmark_version": "v1",
            "seed": 1234,
            "identity": {"company_name": "Fixture", "domain": "FIXTURE.LOCAL"},
        }
    }
    manifest_path.write_text(json.dumps(payload))
    archive_path = root / f"{stem}.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("fixture.txt", "unique CRC fixture bytes")
    hashes = portable.validate_generation(root, "simple", 1234)
    assert set(hashes) == {manifest_path.name, archive_path.name}
    if fault == "seed":
        payload["metadata"]["seed"] = 9
        manifest_path.write_text(json.dumps(payload))
    elif fault == "identity":
        payload["metadata"]["identity"] = {}
        manifest_path.write_text(json.dumps(payload))
    elif fault == "extra-file":
        (root / "unexpected.txt").touch()
    elif fault == "json":
        manifest_path.write_text("not json")
    elif fault == "zip":
        archive_path.write_bytes(b"not zip")
    else:
        archive_path.write_bytes(
            archive_path.read_bytes().replace(
                b"unique CRC fixture bytes",
                b"broken CRC fixture bytes",
                1,
            )
        )
    with pytest.raises(portable.PortableFailure):
        portable.validate_generation(root, "simple", 1234)


def test_later_probe_failure_keeps_receipt_failed_after_earlier_success(
    tmp_path, wheel, monkeypatch
):
    info = portable.wheel_inventory(wheel)
    observed = _observed(tmp_path, info)
    output = tmp_path / "fresh qualification"

    def fake_probe(probe_id, command, environment, cwd, expected_exit, timeout, logs_dir):
        result = {"id": probe_id, "actual_exit": 0, "expected_exit": expected_exit}
        if probe_id == "help":
            result["actual_exit"] = 1
            raise portable.PortableFailure("exit", "injected help failure", result)
        path = logs_dir / f"{probe_id}.stdout.private.log"
        path.write_text(json.dumps(observed) if probe_id == "installation" else "")
        result["stdout_path"] = str(path)
        return result

    monkeypatch.setattr(portable, "run_probe", fake_probe)
    result = portable.main(
        ["--python", sys.executable, "--wheel", str(wheel), "--output-dir", str(output)]
    )
    assert result == 1
    receipt = json.loads((output / "qualification.private.json").read_text())
    assert receipt["state"] == "failed"
    assert receipt["failure"]["kind"] == "exit"
    assert [p["id"] for p in receipt["probes"]] == ["guard-self-test", "installation", "help"]
    assert receipt["probes"][-1]["actual_exit"] == 1


@pytest.mark.parametrize(
    "fault",
    [
        "none",
        "family",
        "module",
        "function",
        "address",
        "port-type",
        "repeat",
        "outbound",
        "wrong-probe",
    ],
)
def test_only_specific_blocked_capability_probe_is_accounted_for(tmp_path, fault):
    # Explicit audit events exercise the hook without attempting a socket operation.
    family = "socket.AF_INET" if fault == "family" else "socket.AF_INET6"
    module = "other.module" if fault == "module" else "urllib3.util.connection"
    function = "different_function" if fault == "function" else "_has_ipv6"
    address = (
        "('::2', 0)"
        if fault == "address"
        else "('::1', False)"
        if fault == "port-type"
        else "('::1', 0)"
    )
    count = 2 if fault == "repeat" else 1
    source = f"""import socket, sys
def {function}():
    sock = socket.socket({family})
    try:
        for _ in range({count}):
            try:
                sys.audit("socket.bind", sock, {address})
            except RuntimeError:
                pass
            else:
                raise AssertionError("bind not blocked")
        if {fault == "outbound"!r}:
            try:
                sys.audit("socket.connect", sock, ("::1", 9))
            except RuntimeError:
                pass
            else:
                raise AssertionError("connect not blocked")
    finally:
        sock.close()
{function}()
"""
    injected = (
        'if mode == "fixture":\n'
        f'        exec({source!r}, {{"__name__": {module!r}}})\n'
        '    elif mode == "self-test":'
    )
    assert portable.BOOTSTRAP.count('if mode == "self-test":') == 1
    bootstrap = portable.BOOTSTRAP.replace('if mode == "self-test":', injected)
    logs = tmp_path / "logs"
    logs.mkdir()
    identifier = "help" if fault == "wrong-probe" else "status-relative"
    arguments = (
        identifier,
        [sys.executable, "-I", "-c", bootstrap, "fixture"],
        portable.child_environment(Path(sys.executable), tmp_path),
        tmp_path,
        0,
        30,
        logs,
    )
    if fault == "none":
        result = portable.run_probe(*arguments)
        assert result["guard"] == {"events": ["socket.bind"], "blocked_ipv6_capability_probes": [0]}
    else:
        with pytest.raises(portable.PortableFailure) as error:
            portable.run_probe(*arguments)
        assert error.value.kind == "guard"
        assert error.value.probe["actual_exit"] == 0
