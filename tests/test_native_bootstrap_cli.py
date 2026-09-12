"""Portable CLI wiring; runtime lifecycle coverage lives with the bootstrap owner."""

import hashlib
import json
import re
import subprocess
from copy import deepcopy
from pathlib import Path

import yaml
from click.testing import CliRunner

from ori.cli import main
from ori.native_runtime import fingerprint_native_runtime_roots


def test_native_setup_examples_match_current_config_contract():
    from ori.eval.v2.campaign_config import V2CampaignConfig

    document = (Path(__file__).parents[1] / "docs/native-mcp-setup.md").read_text()
    blocks = re.findall(r"```yaml\n(.*?)\n```", document, re.DOTALL)
    assert len(blocks) == 4
    common = yaml.safe_load(blocks[0])
    implementations = []
    for block in blocks[1:]:
        data = deepcopy(common)
        native = yaml.safe_load(block)["mcp"]
        native["runtime_fingerprint"] = "a" * 64
        native["dependency_lock_fingerprint"] = "b" * 64
        data["defaults"]["mcp"] = native
        config = V2CampaignConfig.model_validate(data)
        implementations.append(config.defaults.mcp.implementation_id)
        assert config.models[0].provider == "openai-compat"
        assert config.models[0].name == "local-qwen"
        assert set(config.tracks) == {"direct", "mcp"}
        data["modes"] = ["mcp"]
        assert V2CampaignConfig.model_validate(data).modes == ["mcp"]
    assert implementations == ["mwnickerson", "mordavid", "armadin"]


def test_runtime_fingerprint_cli_is_offline_and_reports_only_hashes(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    body = runtime / "module.py"
    body.write_text("value = 1\n")
    lock = tmp_path / "requirements.txt"
    lock.write_text("example==1.0\n")

    def forbidden(*args, **kwargs):
        raise AssertionError("fingerprinting must not launch processes")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    args = [
        "fingerprint-native-runtime",
        "--runtime-root",
        str(runtime),
        "--dependency-lock",
        str(lock),
        "--json",
    ]
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload == {
        "schema_version": "ori-native-runtime-fingerprint-v1",
        "provider_calls": 0,
        "runtime_fingerprint": fingerprint_native_runtime_roots((runtime,)),
        "dependency_lock_fingerprint": hashlib.sha256(lock.read_bytes()).hexdigest(),
    }
    assert str(tmp_path) not in result.output
    body.write_text("value = 2\n")
    changed = CliRunner().invoke(main, args)
    assert changed.exit_code == 0
    assert json.loads(changed.output)["runtime_fingerprint"] != payload["runtime_fingerprint"]
    rejected = CliRunner().invoke(main, args + ["--runtime-root", str(runtime)])
    assert rejected.exit_code != 0
    assert "NATIVE_RUNTIME_FINGERPRINT_FAILED" in rejected.output


def test_discovery_cli_preserves_explicit_inputs_and_default_no_execute(tmp_path, monkeypatch):
    from ori.eval.v2 import native_bootstrap

    checkout, runtime = tmp_path / "checkout", tmp_path / "runtime"
    checkout.mkdir()
    runtime.mkdir()
    python, lock = runtime / "python", tmp_path / "requirements.txt"
    manifest, archive = tmp_path / "manifest.json", tmp_path / "archive.zip"
    for path in (python, lock, manifest, archive):
        path.write_text("fixture")
    calls = []

    async def discover(**kwargs):
        calls.append(kwargs)
        return {"status": "discovered" if kwargs["execute"] else "offline_inputs_checked"}

    monkeypatch.setattr(native_bootstrap, "discover_native_profile_files", discover)
    args = [
        "discover-native-profile",
        "--implementation",
        "mordavid",
        "--mcp-dir",
        str(checkout),
        "--python-executable",
        str(python),
        "--runtime-root",
        str(runtime),
        "--runtime-fingerprint",
        "a" * 64,
        "--dependency-lock",
        str(lock),
        "--dependency-lock-fingerprint",
        "b" * 64,
        "--manifest",
        str(manifest),
        "--archive",
        str(archive),
        "--product",
        "oaic-2026-v1",
        "--database",
        "neo4j",
        "--database",
        "bloodhound",
        "--output-dir",
        str(tmp_path / "new"),
        "--json",
    ]
    for execute in (False, True):
        result = CliRunner().invoke(main, args + (["--execute"] if execute else []))
        assert result.exit_code == 0, result.output
        assert calls[-1]["execute"] is execute
        assert calls[-1]["databases"] == ("neo4j", "bloodhound")
        assert calls[-1]["config"].python_executable == python
        assert str(tmp_path) not in result.output

    async def fail(**kwargs):
        raise ValueError("private-endpoint-and-secret")

    monkeypatch.setattr(native_bootstrap, "discover_native_profile_files", fail)
    result = CliRunner().invoke(main, args)
    assert result.exit_code != 0
    assert "NATIVE_PROFILE_DISCOVERY_FAILED" in result.output
    assert "private-endpoint" not in result.output
