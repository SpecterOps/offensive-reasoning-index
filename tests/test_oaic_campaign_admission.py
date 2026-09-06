"""OAIC paired-artifact configuration and selected schedule boundaries."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
import yaml

from ori.eval.v2 import campaign_runner as runner
from ori.eval.v2.campaign_config import V2CampaignConfig, load_v2_campaign_config
from ori.eval.v2.schema import Track
from tests.support.v2_campaign import _provenance, _state


def _payload():
    return {
        "version": 2,
        "source": {"manifest": "manifest.json", "archive": "archive.zip"},
        "tracks": {
            track: {
                key: f"{track}-{key}.json"
                for key in (
                    "public",
                    "oracles",
                    "candidates",
                    "live_certification",
                    "release_metadata",
                    "selection",
                )
            }
            for track in ("direct", "mcp")
        },
        "selected_release": "paired.json",
        "modes": ["direct"],
        "output_dir": "output",
        "defaults": {},
        "models": [{"name": "test", "provider": "ollama", "model": "test"}],
    }


def test_paired_release_config_admission(tmp_path, subtests):
    payload = _payload()
    for case in ("direct-only", "missing-pair", "missing-sibling", "missing-metadata", "paths"):
        with subtests.test(case=case):
            current = deepcopy(payload)
            if case == "missing-pair":
                del current["selected_release"]
            elif case == "missing-sibling":
                del current["tracks"]["mcp"]
            elif case == "missing-metadata":
                del current["tracks"]["mcp"]["release_metadata"]
            if case.startswith("missing"):
                with pytest.raises(ValueError):
                    V2CampaignConfig.model_validate(current)
                continue
            config = V2CampaignConfig.model_validate(current)
            assert config.track_modes == (Track.DIRECT,)
            assert config.defaults.mcp is None
            if case == "paths":
                for name in [
                    "manifest.json",
                    "archive.zip",
                    "paired.json",
                    *(value for paths in payload["tracks"].values() for value in paths.values()),
                ]:
                    (tmp_path / name).touch()
                path = tmp_path / "config.yaml"
                path.write_text(yaml.safe_dump(payload))
                resolved = load_v2_campaign_config(path)
                assert resolved.selected_release == tmp_path / "paired.json"
                assert resolved.tracks[Track.MCP].selection == tmp_path / "mcp-selection.json"
                (tmp_path / "mcp-selection.json").unlink()
                with pytest.raises(ValueError, match="paths do not exist"):
                    load_v2_campaign_config(path)


def test_selected_schedule_and_resume_reject_off_selection(tmp_path, monkeypatch):
    provenance = _provenance(Track.DIRECT)
    state = _state(provenance, Track.DIRECT)
    path = tmp_path / "state.json"
    path.write_text(state.model_dump_json())
    prepared = runner.PreparedTrack(
        track=Track.DIRECT,
        pair=object(),
        profile=object(),
        release=SimpleNamespace(entries=(SimpleNamespace(task_id="direct-task"),)),
        live=object(),
        certifications={},
        selected_task_ids=("other-task",),
    )
    assert prepared.task_ids == ("other-task",)
    # Isolate the selected-schedule check after the existing certification gate.
    monkeypatch.setattr(runner, "validate_checkpoint", lambda *args: None)
    before = path.read_bytes()
    with pytest.raises(runner.V2CampaignRunError, match="off-selection"):
        runner._load_state(path, provenance=provenance, prepared=prepared)
    assert path.read_bytes() == before
