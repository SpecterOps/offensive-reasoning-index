from __future__ import annotations

import hashlib
import importlib.util
import json
import struct
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts/build_offensive_ai_con_demo.py"


def _module():
    spec = importlib.util.spec_from_file_location("build_offensive_ai_con_demo", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_demo_builder_is_deterministic_and_public_safe(tmp_path: Path) -> None:
    module = _module()
    first = tmp_path / "first"
    second = tmp_path / "second"
    module.build(first)
    module.build(second)

    assert (first / "index.html").read_bytes() == (second / "index.html").read_bytes()
    assert (first / "demo-evidence.json").read_bytes() == (
        second / "demo-evidence.json"
    ).read_bytes()

    page = (first / "index.html").read_text(encoding="utf-8")
    evidence = json.loads((first / "demo-evidence.json").read_text(encoding="utf-8"))
    assert evidence["schema_version"] == "ori-offensive-ai-con-offline-demo-v1"
    assert evidence["beat_count"] == 6
    assert evidence["offline_only"] is True
    assert evidence["provider_calls"] == 0
    assert evidence["v29_release"] == {
        "status": "PASS",
        "direct_tasks": 42,
        "mcp_tasks": 55,
        "provider_calls": 0,
    }
    assert evidence["historical_direct"]["scheduled_samples"] == 1260
    assert len(evidence["nous_interoperability_canaries"]) == 2
    assert page.count('class="beat"') == 6
    assert 'src="http' not in page
    assert 'href="http' not in page
    assert "/Users/" not in page
    assert "OPENROUTER_API_KEY=" not in page
    assert "NOUS_API_KEY=" not in page
    assert "BLOODHOUND_TOKEN" not in page


def test_demo_builder_fails_closed_on_provider_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    provider = module._load_json(module.PROVIDER_EVIDENCE)
    provider["full_v29_readiness"]["provider_calls"] = 1
    monkeypatch.setattr(
        module,
        "_load_json",
        lambda path: provider
        if path == module.PROVIDER_EVIDENCE
        else json.loads(path.read_text()),
    )
    with pytest.raises(ValueError, match="readiness made provider calls"):
        module.build(tmp_path / "out")


def test_static_fallbacks_match_render_manifest() -> None:
    output = REPO_ROOT / "docs/assets/offensive-ai-con/offline-demo"
    manifest = json.loads((output / "render-manifest.json").read_text())

    assert manifest["schema_version"] == "ori-offensive-ai-con-offline-demo-render-v1"
    assert manifest["viewport"] == {"width": 1280, "height": 720}
    assert manifest["beat_count"] == 6
    assert sorted(manifest["artifacts"]) == [
        f"static/beat-{beat:02d}.png" for beat in range(1, 7)
    ]
    for relative, expected_sha256 in manifest["artifacts"].items():
        payload = (output / relative).read_bytes()
        assert payload.startswith(b"\x89PNG\r\n\x1a\n")
        width, height = struct.unpack(">II", payload[16:24])
        assert (width, height) == (1280, 720)
        assert hashlib.sha256(payload).hexdigest() == expected_sha256
