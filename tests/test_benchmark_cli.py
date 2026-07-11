from __future__ import annotations

import json

from click.testing import CliRunner

from ori.cli import main


def test_benchmark_list_cli_shows_simple_and_complex() -> None:
    result = CliRunner().invoke(main, ["benchmark", "list"])

    assert result.exit_code == 0
    assert "simple" in result.output
    assert "complex" in result.output
    assert "Fast Phase 3-derived benchmark" in result.output
    assert "tracks=direct,mcp" in result.output


def test_benchmark_describe_cli_shows_complex_contract() -> None:
    result = CliRunner().invoke(main, ["benchmark", "describe", "complex"])

    assert result.exit_code == 0
    assert "Complex benchmark (complex)" in result.output
    assert "Graph profile: phase4_complex" in result.output
    assert "Diagnostic task set: phase4_complex_diagnostic" in result.output
    assert "Tracks: direct, mcp" in result.output
    assert "direct: 100 tasks (phase4_complex_direct_official)" in result.output
    assert "mcp: 100 tasks (phase4_complex_mcp_official)" in result.output


def test_benchmark_run_cli_is_dry_run_placeholder() -> None:
    result = CliRunner().invoke(main, ["benchmark", "run", "complex", "--mode", "diagnostic"])

    assert result.exit_code == 0
    assert "Benchmark: complex" in result.output
    assert "Mode: diagnostic" in result.output
    assert "Task set: phase4_complex_diagnostic" in result.output
    assert "Status: planned" in result.output


def test_benchmark_run_cli_rejects_unsupported_mode() -> None:
    result = CliRunner().invoke(main, ["benchmark", "run", "simple", "--mode", "diagnostic"])

    assert result.exit_code != 0
    assert "does not support mode 'diagnostic'" in result.output


def test_benchmark_generate_simple_writes_seeded_artifacts(tmp_path) -> None:
    result = CliRunner().invoke(
        main,
        [
            "benchmark",
            "generate",
            "simple",
            "--seed",
            "1234",
            "--output-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    manifest_path = tmp_path / "simple-v1-seed-1234_manifest.json"
    assert (tmp_path / "simple-v1-seed-1234.zip").exists()
    manifest = json.loads(manifest_path.read_text())
    assert manifest["seed"] == 1234
    assert manifest["domain"] == manifest["metadata"]["identity"]["domain"]
    assert manifest["metadata"]["benchmark"] == "simple"
    assert manifest["metadata"]["generator_version"] == "seeded-benchmark-v1"
    assert manifest["metadata"]["shared_dataset_across_tracks"] is True
    assert set(manifest["metadata"]["benchmark_tracks"]) == {"direct", "mcp"}
    assert manifest["metadata"]["benchmark_tracks"]["direct"]["task_count"] == 40
    assert 80 <= manifest["metadata"]["scale"]["users"] <= 120


def test_generate_simple_alias_writes_seeded_artifacts(tmp_path) -> None:
    result = CliRunner().invoke(
        main,
        ["generate", "simple", "--seed", "1234", "--output", str(tmp_path)],
    )

    assert result.exit_code == 0, result.output
    assert (tmp_path / "simple-v1-seed-1234.zip").exists()
    assert (tmp_path / "simple-v1-seed-1234_manifest.json").exists()
