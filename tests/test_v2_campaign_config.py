from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ori.eval.v2.campaign_config import load_v2_campaign_config
from ori.eval.v2.schema import Track


def _config(tmp_path: Path) -> tuple[Path, dict]:
    for name in (
        "manifest.json",
        "archive.zip",
        "direct-public.json",
        "direct-private.json",
        "direct-candidates.json",
        "direct-live.json",
    ):
        (tmp_path / name).touch()
    (tmp_path / "mcp").mkdir()
    payload = {
        "version": 2,
        "protocol": "ori-eval-protocol-v2",
        "source": {
            "manifest": "manifest.json",
            "archive": "archive.zip",
        },
        "tracks": {
            "direct": {
                "public": "direct-public.json",
                "oracles": "direct-private.json",
                "candidates": "direct-candidates.json",
                "live_certification": "direct-live.json",
            }
        },
        "modes": ["direct"],
        "output_dir": "campaign",
        "defaults": {
            "concurrency": 1,
            "runs_per_model": 1,
            "mcp": {
                "mcp_dir": "mcp",
                "resource_mode": "off",
                "tool_loop": "native-openai-compatible",
            },
        },
        "models": [
            {
                "name": "gpt-test",
                "provider": "codex",
                "model": "gpt-test",
            }
        ],
    }
    path = tmp_path / "models-v2.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False))
    return path, payload


def test_v2_config_is_strict_and_resolves_paths_from_config(
    tmp_path: Path,
) -> None:
    path, _payload = _config(tmp_path)

    resolved = load_v2_campaign_config(path)

    assert resolved.source_manifest == tmp_path / "manifest.json"
    assert resolved.output_dir == tmp_path / "campaign"
    assert set(resolved.tracks) == {Track.DIRECT}
    assert resolved.config.models[0].requested_model == "codex/gpt-test"


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        ({"version": 1}, "Input should be 2"),
        ({"protocol": "ori-eval-protocol-v1"}, "ori-eval-protocol-v2"),
        ({"modes": ["direct", "direct"]}, "non-empty and unique"),
        (
            {"defaults": {"concurrency": 2}},
            "concurrency=1",
        ),
    ],
)
def test_v2_config_rejects_mixed_or_uncertified_campaigns(
    tmp_path: Path,
    mutation: dict,
    match: str,
) -> None:
    path, payload = _config(tmp_path)
    for key, value in mutation.items():
        if key == "defaults":
            payload["defaults"].update(value)
        else:
            payload[key] = value
    path.write_text(yaml.safe_dump(payload, sort_keys=False))

    with pytest.raises(ValueError, match=match):
        load_v2_campaign_config(path)


def test_v2_config_forbids_resource_and_auto_loop_modes(tmp_path: Path) -> None:
    path, payload = _config(tmp_path)
    payload["defaults"]["mcp"]["resource_mode"] = "on-demand"
    path.write_text(yaml.safe_dump(payload, sort_keys=False))
    with pytest.raises(ValueError, match="off"):
        load_v2_campaign_config(path)

    payload["defaults"]["mcp"]["resource_mode"] = "off"
    payload["defaults"]["mcp"]["tool_loop"] = "auto"
    path.write_text(yaml.safe_dump(payload, sort_keys=False))
    with pytest.raises(ValueError, match="native-openai-compatible"):
        load_v2_campaign_config(path)
