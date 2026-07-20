from __future__ import annotations

from pathlib import Path

import pytest

from ori.run_config import RunConfigOverrides, list_run_profiles, load_run_profile


def test_shipped_run_configs_load_all_profiles() -> None:
    """Every checked-in run config profile should load with real YAML parsing."""
    for config in (
        Path("run-config.example.yaml"),
        Path("run-config-phase3-m4.yaml"),
        Path("run-config-phase4-v1.yaml"),
        Path("run-config-death-star.yaml"),
    ):
        profiles = list_run_profiles(config)
        assert profiles, f"{config} should define at least one profile"
        for profile in profiles:
            resolved = load_run_profile(config, profile_name=profile.profile_name)
            assert resolved.profile_name == profile.profile_name


def test_phase3_m4_preflight_loads_with_unquoted_resource_mode_off() -> None:
    resolved = load_run_profile(Path("run-config-phase3-m4.yaml"), profile_name="preflight_local")
    assert resolved.kind == "preflight"
    assert resolved.resource_mode == "off"


def test_death_star_profiles_use_openai_compatible_endpoint() -> None:
    resolved = load_run_profile(
        Path("run-config-death-star.yaml"), profile_name="death_star_qwen_fast_mcp"
    )

    assert resolved.kind == "baseline_mcp"
    assert resolved.concurrency == 1
    assert resolved.model_base_url == "http://death-star:8080/v1"
    assert resolved.manifest == (
        "/Users/anton/projects/ori-run-artifacts/"
        "phase4b-v2-medium-corp-20260603/medium/manifest.json"
    )
    assert resolved.output_dir is not None
    assert resolved.output_dir.endswith("results/death-star/phase4b-v2-medium/qwen-fast-mcp")
    assert resolved.mcp_tool_loop == "native-openai-compatible"
    assert resolved.openai_compat_telemetry_adapter == "llama-cpp"
    assert resolved.mcp_ollama_read_timeout_seconds == 1800.0
    assert resolved.model_entries == [
        {
            "name": "death-star-qwen-fast-64k-mcp",
            "model": "openai-compat/qwen-fast",
            "concurrency": 1,
            "options": {"temperature": 0},
        }
    ]


def test_load_run_profile_resolves_paths_and_defaults(tmp_path: Path) -> None:
    mcp_dir = tmp_path / "bloodhound-mcp"
    mcp_dir.mkdir()
    config = tmp_path / "run.yaml"
    config.write_text(
        """
version: 1
defaults:
  concurrency: 2
  runs_per_model: 3
  bhce_url: http://bh.local
  model_base_url: http://models.local/v1
  max_model_reruns_on_infra: 3
  health:
    timeout_seconds: 11
    poll_interval: 1.5
  mcp:
    mcp_dir: bloodhound-mcp
    max_steps: 22
    resource_mode: on-demand
    tool_loop: native-openai-compatible
    openai_compat_telemetry_adapter: llama-cpp
    ollama_read_timeout_seconds: 1200
  telemetry:
    enabled: false
profiles:
  phase3b:
    kind: baseline-mcp
    manifest: datasets/phase3b_manifest.json
    output_dir: results/out
    models:
      - model: ollama/gemma4:e4b
"""
    )

    resolved = load_run_profile(config, profile_name="phase3b")
    assert resolved.kind == "baseline_mcp"
    assert resolved.concurrency == 2
    assert resolved.runs_per_model == 3
    assert resolved.bhce_url == "http://bh.local"
    assert resolved.model_base_url == "http://models.local/v1"
    assert resolved.max_model_reruns_on_infra == 3
    assert resolved.health_timeout_seconds == 11.0
    assert resolved.health_poll_interval == 1.5
    assert resolved.max_steps == 22
    assert resolved.resource_mode == "on-demand"
    assert resolved.mcp_tool_loop == "native-openai-compatible"
    assert resolved.openai_compat_telemetry_adapter == "llama-cpp"
    assert resolved.mcp_ollama_read_timeout_seconds == 1200.0
    assert resolved.telemetry_enabled is False
    assert resolved.manifest == str((tmp_path / "datasets/phase3b_manifest.json").resolve())
    assert resolved.output_dir == str((tmp_path / "results/out").resolve())
    assert resolved.mcp_dir == str(mcp_dir.resolve())


def test_load_generate_profile_resolves_phase4_outputs(tmp_path: Path) -> None:
    config = tmp_path / "run.yaml"
    config.write_text(
        """
version: 1
profiles:
  phase4_v1:
    kind: generate
    domain: corp.local
    seed: 4401
    generator:
      profile: phase4_v1
    sizing:
      users: 12
      workstations: 5
      servers: 3
    output_zip: datasets/phase4-v1.zip
    output_manifest: datasets/phase4-v1_manifest.json
"""
    )

    resolved = load_run_profile(config, profile_name="phase4_v1")
    assert resolved.kind == "generate"
    assert resolved.domain == "corp.local"
    assert resolved.seed == 4401
    assert resolved.generator_profile == "phase4_v1"
    assert resolved.users == 12
    assert resolved.workstations == 5
    assert resolved.servers == 3
    assert resolved.output_zip == str((tmp_path / "datasets/phase4-v1.zip").resolve())
    assert resolved.output_manifest == str(
        (tmp_path / "datasets/phase4-v1_manifest.json").resolve()
    )


def test_load_run_profile_requires_explicit_profile_when_multiple(tmp_path: Path) -> None:
    config = tmp_path / "run.yaml"
    config.write_text(
        """
version: 1
profiles:
  a:
    kind: preflight
    manifest: a.json
    output_dir: out-a
  b:
    kind: preflight
    manifest: b.json
    output_dir: out-b
"""
    )
    with pytest.raises(ValueError, match="multiple profiles"):
        load_run_profile(config)


def test_load_run_profile_applies_cli_overrides(tmp_path: Path) -> None:
    config = tmp_path / "run.yaml"
    config.write_text(
        """
version: 1
defaults:
  concurrency: 1
  health:
    timeout_seconds: 60
    poll_interval: 5
profiles:
  smoke:
    kind: smoke-mcp
    manifest: manifest.json
    output_dir: out
    mcp:
      resource_mode: off
"""
    )

    resolved = load_run_profile(
        config,
        profile_name="smoke",
        overrides=RunConfigOverrides(
            concurrency=4,
            max_steps=30,
            resource_mode="on-demand",
            mcp_tool_loop="inspect",
            openai_compat_telemetry_adapter="vllm",
            mcp_ollama_read_timeout_seconds=1800,
            health_timeout_seconds=10,
            health_poll_interval=0.5,
        ),
    )
    assert resolved.concurrency == 4
    assert resolved.max_steps == 30
    assert resolved.resource_mode == "on-demand"
    assert resolved.mcp_tool_loop == "inspect"
    assert resolved.openai_compat_telemetry_adapter == "vllm"
    assert resolved.mcp_ollama_read_timeout_seconds == 1800.0
    assert resolved.health_timeout_seconds == 10.0
    assert resolved.health_poll_interval == 0.5


def test_load_run_profile_defaults_resource_mode_to_off(tmp_path: Path) -> None:
    config = tmp_path / "run.yaml"
    config.write_text(
        """
version: 1
profiles:
  smoke:
    kind: smoke-mcp
    manifest: manifest.json
    output_dir: out
"""
    )

    resolved = load_run_profile(config, profile_name="smoke")
    assert resolved.resource_mode == "off"
    assert resolved.mcp_tool_loop == "auto"
    assert resolved.openai_compat_telemetry_adapter == "auto"
    assert resolved.mcp_ollama_read_timeout_seconds == 900.0
    assert resolved.telemetry_enabled is True


def test_load_run_profile_accepts_unquoted_yaml_off_resource_mode(tmp_path: Path) -> None:
    config = tmp_path / "run.yaml"
    config.write_text(
        """
version: 1
defaults:
  mcp:
    resource_mode: off
profiles:
  smoke:
    kind: smoke-mcp
    manifest: manifest.json
    output_dir: out
"""
    )

    resolved = load_run_profile(config, profile_name="smoke")
    assert resolved.resource_mode == "off"


def test_load_run_profile_applies_telemetry_override(tmp_path: Path) -> None:
    config = tmp_path / "run.yaml"
    config.write_text(
        """
version: 1
defaults:
  telemetry:
    enabled: true
profiles:
  phase:
    kind: baseline
    manifest: manifest.json
    output_dir: out
    models:
      - model: ollama/gemma4:e4b
"""
    )

    resolved = load_run_profile(
        config,
        profile_name="phase",
        overrides=RunConfigOverrides(telemetry_enabled=False),
    )
    assert resolved.telemetry_enabled is False


def test_list_run_profiles_preserves_order_and_enabled(tmp_path: Path) -> None:
    config = tmp_path / "run.yaml"
    config.write_text(
        """
version: 1
profiles:
  prep:
    kind: preflight
    manifest: prep.json
    output_dir: out-prep
  disabled_phase:
    kind: baseline
    enabled: false
    manifest: manifest.json
    output_dir: out-disabled
    models:
      - model: ollama/gemma4:e4b
  final_phase:
    kind: smoke-eval
    manifest: manifest.json
    output_dir: out-final
"""
    )

    profiles = list_run_profiles(config)
    assert [profile.profile_name for profile in profiles] == [
        "prep",
        "disabled_phase",
        "final_phase",
    ]
    assert [profile.kind for profile in profiles] == ["preflight", "baseline", "smoke_eval"]
    assert [profile.enabled for profile in profiles] == [True, False, True]
