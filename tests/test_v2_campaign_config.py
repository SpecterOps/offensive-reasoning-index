from __future__ import annotations

import json
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
    assert resolved.config.models[0].max_output_tokens == 2048
    assert resolved.config.defaults.mcp.read_timeout_seconds == 240.0
    assert resolved.config.defaults.mcp.tool_timeout_seconds == 60.0
    assert resolved.native_mcp_paths is None
    assert "mode" not in resolved.config.defaults.mcp.model_dump()
    assert "native_mcp_paths" not in resolved.model_dump()


@pytest.mark.parametrize("implementation", ["mwnickerson", "mordavid", "armadin"])
def test_native_config_resolves_explicit_lane_and_refuses_unqualified_readiness(
    tmp_path, monkeypatch, implementation,
):
    from ori.eval.v2.campaign_runner import V2CampaignRunError, prepare_v2_campaign
    from tests.support.v2_mcp import native_profile

    path, payload = _config(tmp_path)
    profile = native_profile(implementation)
    (tmp_path / "profile.json").write_text(profile.model_dump_json())
    (tmp_path / "runtime" / "bin").mkdir(parents=True)
    target = tmp_path / "runtime" / "base-python"
    target.touch()
    (tmp_path / "runtime" / "bin" / "python").symlink_to(target)
    (tmp_path / "requirements.lock").touch()
    native = {
        **payload["defaults"]["mcp"], "mode": "native",
        "implementation_id": implementation, "capability_profile": "profile.json",
        "python_executable": "runtime/bin/python", "runtime_roots": ["runtime"],
        "runtime_fingerprint": "a" * 64, "dependency_lock": "requirements.lock",
        "dependency_lock_fingerprint": "b" * 64, "backend": profile.backend,
        "databases": [] if implementation == "mwnickerson" else
                     ["neo4j", "bloodhound"] if implementation == "mordavid" else ["neo4j"],
    }
    payload["defaults"]["mcp"] = native
    payload["tracks"] = {"mcp": payload["tracks"]["direct"]}
    payload["modes"] = ["mcp"]
    path.write_text(yaml.safe_dump(payload))
    resolved = load_v2_campaign_config(path)
    assert resolved.native_mcp_paths.python_executable == tmp_path / "runtime/bin/python"
    assert resolved.native_mcp_paths.runtime_roots == (tmp_path / "runtime",)

    from ori.eval.v2.campaign_runner import _model_loop

    anthropic = json.loads(json.dumps(payload))
    anthropic["defaults"]["mcp"]["tool_loop"] = "native-anthropic"
    anthropic["models"][0].update(provider="anthropic", model="claude-fixture")
    path.write_text(yaml.safe_dump(anthropic))
    parsed = load_v2_campaign_config(path)
    assert _model_loop(parsed.config.models[0], parsed).value == "native-anthropic"
    anthropic["models"][0].update(provider="codex", model="gpt-test")
    path.write_text(yaml.safe_dump(anthropic))
    parsed = load_v2_campaign_config(path)
    with pytest.raises(V2CampaignRunError, match="explicit native Anthropic cell"):
        _model_loop(parsed.config.models[0], parsed)
    path.write_text(yaml.safe_dump(payload))

    from ori.eval.v2.campaign_config import load_v2_native_qualification_config

    # Qualification can name future outputs, but ordinary loading requires both
    # private inputs to exist; neither presence nor configuration grants admission.
    payload["defaults"]["mcp"] = {
        **native, "qualification": "native-qualification.private.json",
        "qualification_work": "native-work.private.json",
    }
    path.write_text(yaml.safe_dump(payload))
    pending = load_v2_native_qualification_config(path)
    assert pending.native_mcp_paths.qualification == tmp_path / "native-qualification.private.json"
    with pytest.raises(ValueError, match="NATIVE_QUALIFICATION_PATHS_INVALID"):
        load_v2_campaign_config(path)
    for name in ("native-qualification.private.json", "native-work.private.json"):
        (tmp_path / name).write_text("{}")
    qualified_paths = load_v2_campaign_config(path).native_mcp_paths
    assert qualified_paths.qualification_work == tmp_path / "native-work.private.json"
    payload["defaults"]["mcp"] = native

    original_paths = dict(payload["tracks"]["mcp"])
    payload["tracks"]["mcp"].update(
        candidates="future-candidates.json", live_certification="future-live.private.json",
    )
    path.write_text(yaml.safe_dump(payload))
    qualification = load_v2_native_qualification_config(path)
    assert qualification.native_mcp_paths == resolved.native_mcp_paths
    with pytest.raises(ValueError, match="paths do not exist"):
        load_v2_campaign_config(path)
    payload["tracks"]["mcp"] = original_paths
    path.write_text(yaml.safe_dump(payload))

    def forbidden(*args, **kwargs):
        pytest.fail("unqualified native campaign reached model/backend/artifact execution")

    monkeypatch.setattr("ori.eval.v2.campaign_runner._model_readiness", forbidden)
    monkeypatch.setattr("ori.eval.v2.campaign_runner.build_archive_snapshot", forbidden)
    with pytest.raises(V2CampaignRunError, match="NATIVE_SELECTION_REQUIRED"):
        prepare_v2_campaign(path)

    for changes in (
        {"runtime_fingerprint": "d" * 64},
        {"dependency_lock_fingerprint": "e" * 64},
    ):
        payload["defaults"]["mcp"] = {**native, **changes}
        path.write_text(yaml.safe_dump(payload))
        with pytest.raises(V2CampaignRunError, match="NATIVE_CONFIG_PROFILE_MISMATCH"):
            prepare_v2_campaign(path)
    for changes in (
        {"mode": "historical"}, {"runtime_roots": []}, {"backend": "unknown"},
        {"password": "forbidden"}, {"runtime_qualified": True},
        {"qualification": "one-sided.json"},
        {"qualification": " ", "qualification_work": "work.json"},
    ):
        payload["defaults"]["mcp"] = {**native, **changes}
        path.write_text(yaml.safe_dump(payload))
        with pytest.raises(ValueError):
            load_v2_campaign_config(path)

    from types import SimpleNamespace

    # The real replay loader has separate evidence tests. This boundary ensures
    # configured evidence is routed to it and never unlocks provider execution.
    payload["defaults"]["mcp"] = {
        **native, "qualification": "native-qualification.private.json",
        "qualification_work": "native-work.private.json",
    }
    path.write_text(yaml.safe_dump(payload))
    prepared = SimpleNamespace(corpus=SimpleNamespace(product="oaic-2026-v1"))
    snapshot = object()
    monkeypatch.setattr("ori.eval.v2.campaign_runner.prepare_native_campaign_artifacts",
                        lambda *_: prepared)
    monkeypatch.setattr("ori.eval.v2.campaign_runner.build_archive_snapshot",
                        lambda *_, **__: snapshot)
    (tmp_path / "manifest.json").write_text("{}")
    replay_calls = []

    def replay(actual_prepared, actual_snapshot, **paths):
        assert actual_prepared is prepared and actual_snapshot is snapshot
        assert paths["qualification_path"] == tmp_path / "native-qualification.private.json"
        assert paths["work_path"] == tmp_path / "native-work.private.json"
        replay_calls.append(paths)

    monkeypatch.setattr("ori.eval.v2.native_qualification.load_native_qualification_artifacts",
                        replay)
    with pytest.raises(V2CampaignRunError, match="NATIVE_PAIRED_RELEASE_REQUIRED"):
        prepare_v2_campaign(path)
    assert len(replay_calls) == 1

    # Artifact replay and paired selection have their own real-data tests. Here
    # check that the public preparation path returns the exact validated mapping
    # for CE, while Bolt remains stopped before model readiness.
    from ori.eval.v2 import campaign_runner

    real_resolved = load_v2_campaign_config(path)
    direct = object()
    qualified = SimpleNamespace(profile=profile)
    selected = {Track.DIRECT: direct, Track.MCP: qualified}
    routed = SimpleNamespace(
        native_mcp_paths=real_resolved.native_mcp_paths,
        config=real_resolved.config, source_manifest=real_resolved.source_manifest,
        archive=real_resolved.archive, selected_release=tmp_path / "paired.json",
        tracks={Track.DIRECT: object(), Track.MCP: real_resolved.tracks[Track.MCP]},
    )
    validated = []
    with monkeypatch.context() as context:
        context.setattr(campaign_runner, "load_v2_campaign_config", lambda _: routed)
        context.setattr("ori.eval.v2.native_qualification.load_native_qualification_artifacts",
                        lambda *_, **__: qualified)
        context.setattr(campaign_runner, "_prepare_track", lambda *_: direct)
        context.setattr(campaign_runner, "prepare_native_selected_tracks", lambda *_: selected)
        context.setattr(campaign_runner, "_validate_model_bindings",
                        lambda _, mapping: validated.append(("models", mapping)))
        context.setattr(campaign_runner, "_validate_runtime_bounds",
                        lambda _, mapping: validated.append(("bounds", mapping)))
        context.setattr(campaign_runner, "_model_readiness", lambda _: ())
        result = prepare_v2_campaign(path)
        assert result == (routed, snapshot, {Track.MCP: qualified}, profile.source_revision, ())
        assert validated == [("models", result[2]), ("bounds", result[2])]

    def reject(*args, **kwargs):
        raise ValueError("private artifact mismatch")

    monkeypatch.setattr("ori.eval.v2.native_qualification.load_native_qualification_artifacts",
                        reject)
    with pytest.raises(V2CampaignRunError, match="^NATIVE_QUALIFICATION_ARTIFACT_INVALID$"):
        prepare_v2_campaign(path)


def test_v2_config_accepts_typed_model_output_limit(tmp_path: Path) -> None:
    path, payload = _config(tmp_path)
    payload["models"][0]["max_output_tokens"] = 8192
    path.write_text(yaml.safe_dump(payload, sort_keys=False))

    resolved = load_v2_campaign_config(path)

    assert resolved.config.models[0].max_output_tokens == 8192


@pytest.mark.parametrize("option_name", ("max_tokens", "max_output_tokens"))
def test_v2_config_rejects_output_limit_hidden_in_options(
    tmp_path: Path,
    option_name: str,
) -> None:
    path, payload = _config(tmp_path)
    payload["models"][0]["options"] = {option_name: 8192}
    path.write_text(yaml.safe_dump(payload, sort_keys=False))

    with pytest.raises(ValueError, match="model max_output_tokens"):
        load_v2_campaign_config(path)


def test_v2_config_accepts_one_codex_reasoning_effort_for_all_models(
    tmp_path: Path,
) -> None:
    path, payload = _config(tmp_path)
    payload["defaults"]["reasoning_effort"] = "high"
    payload["models"].append(
        {
            "name": "gpt-test-two",
            "provider": "codex",
            "model": "gpt-test-two",
        }
    )
    path.write_text(yaml.safe_dump(payload, sort_keys=False))

    resolved = load_v2_campaign_config(path)

    assert resolved.config.defaults.reasoning_effort == "high"
    assert all("reasoning_effort" not in model.options for model in resolved.config.models)


def test_v2_config_rejects_untyped_or_non_codex_reasoning_effort(tmp_path: Path) -> None:
    path, payload = _config(tmp_path)
    payload["defaults"]["reasoning_effort"] = "extreme"
    path.write_text(yaml.safe_dump(payload, sort_keys=False))
    with pytest.raises(ValueError, match="literal_error"):
        load_v2_campaign_config(path)

    payload["defaults"]["reasoning_effort"] = "high"
    payload["models"][0]["provider"] = "openai"
    path.write_text(yaml.safe_dump(payload, sort_keys=False))
    with pytest.raises(ValueError, match="only for Codex models"):
        load_v2_campaign_config(path)


def test_v2_config_rejects_reasoning_effort_hidden_in_model_options(tmp_path: Path) -> None:
    path, payload = _config(tmp_path)
    payload["models"][0]["options"] = {"reasoning_effort": "high"}
    path.write_text(yaml.safe_dump(payload, sort_keys=False))

    with pytest.raises(ValueError, match="defaults.reasoning_effort"):
        load_v2_campaign_config(path)


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


def test_v2_config_admits_only_explicit_mcp_diagnostic_canaries(tmp_path: Path) -> None:
    path, payload = _config(tmp_path)
    for name in (
        "mcp-public.json",
        "mcp-private.json",
        "mcp-candidates.json",
        "mcp-live.json",
        "mcp-metadata.json",
        "mcp-selection.json",
        "paired-selection.json",
        "canary-selection.json",
    ):
        (tmp_path / name).touch()
    payload.update(
        {
            "purpose": "diagnostic_canary",
            "modes": ["mcp"],
            "tracks": {
                "mcp": {
                    "public": "mcp-public.json",
                    "oracles": "mcp-private.json",
                    "candidates": "mcp-candidates.json",
                    "live_certification": "mcp-live.json",
                    "release_metadata": "mcp-metadata.json",
                    "selection": "mcp-selection.json",
                }
            },
            "selected_release": "paired-selection.json",
            "canary_selection": "canary-selection.json",
        }
    )
    path.write_text(yaml.safe_dump(payload, sort_keys=False))

    resolved = load_v2_campaign_config(path)

    assert resolved.config.purpose == "diagnostic_canary"
    assert resolved.config.track_modes == (Track.MCP,)
    assert resolved.canary_selection == tmp_path / "canary-selection.json"

    payload["modes"] = ["direct", "mcp"]
    path.write_text(yaml.safe_dump(payload, sort_keys=False))
    with pytest.raises(ValueError, match="exactly the MCP track"):
        load_v2_campaign_config(path)
