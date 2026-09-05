from __future__ import annotations

import json

import pytest
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
    assert manifest["metadata"]["generator_version"] == "seeded-benchmark-v2"
    assert manifest["metadata"]["shared_dataset_across_tracks"] is True
    assert set(manifest["metadata"]["benchmark_tracks"]) == {"direct", "mcp"}
    assert manifest["metadata"]["benchmark_tracks"]["direct"]["task_count"] == 40
    assert 80 <= manifest["metadata"]["scale"]["users"] <= 120

    assert manifest["schema_version"] == "ori-generated-manifest-v2"
    assert manifest["metadata"]["relationship_contract_version"] == "1.0"
    sharphound = manifest["metadata"]["sharphound"]
    assert sharphound["encoding_profile"] == "bloodhound-ce-9.1.0-sharphound-v5-v6"
    assert set(sharphound["file_versions"].values()) == {5, 6}
    compatibility = manifest["metadata"]["compatibility"]["bloodhound_ce"]
    assert compatibility == {
        "tested_version": "9.1.0",
        "supported_baseline": "9.1.0",
    }

    projected_counts = manifest["stats"]["projected_nodes_by_file"]
    assert set(projected_counts) == set(sharphound["file_versions"])
    assert manifest["stats"]["domains"] == 1
    assert all(
        key in projected_counts
        for key in ("enterprisecas", "rootcas", "aiacas", "ntauthstores", "certtemplates")
    )
    assert sum(projected_counts.values()) == manifest["stats"]["total_nodes"]
    assert all(manifest["stats"][key] == count for key, count in projected_counts.items())
    assert manifest["stats"]["total_edges_scope"] == "internal_graph"
    assert manifest["stats"]["total_edges"] == manifest["stats"]["internal_graph_edges"]

    relationship_summary = manifest["relationship_summary"]
    assert relationship_summary["total_relationships"] == sum(
        relationship_summary["counts_by_kind"].values()
    )
    emitted_kinds = {
        edge["edge"]
        for path in manifest["planted_paths"]
        for edge in [*path["path_edges"], *path["supporting_edges"]]
    }
    assert emitted_kinds.isdisjoint({"WriteDACL", "TrustedBy"})


def test_generate_simple_alias_writes_seeded_artifacts(tmp_path) -> None:
    result = CliRunner().invoke(
        main,
        ["generate", "simple", "--seed", "1234", "--output", str(tmp_path)],
    )

    assert result.exit_code == 0, result.output
    assert (tmp_path / "simple-v1-seed-1234.zip").exists()
    assert (tmp_path / "simple-v1-seed-1234_manifest.json").exists()


@pytest.mark.parametrize(
    ("command", "output_option"),
    [(["benchmark", "generate", "simple"], "--output-dir"), (["generate", "simple"], "--output")],
)
def test_simple_artifacts_ignore_wall_clock_but_vary_by_seed(
    tmp_path, monkeypatch, command, output_option
) -> None:
    artifacts = []
    for index, (clock, seed) in enumerate(
        [(1_000_000_000, 67), (2_000_000_000, 67), (2_000_000_000, 68)]
    ):
        monkeypatch.setattr("time.time", lambda: clock)
        output_dir = tmp_path / str(index)
        result = CliRunner().invoke(
            main, [*command, "--seed", str(seed), output_option, str(output_dir)]
        )
        assert result.exit_code == 0, result.output
        prefix = output_dir / f"simple-v1-seed-{seed}"
        artifacts.append(
            (
                prefix.with_suffix(".zip").read_bytes(),
                prefix.with_name(prefix.name + "_manifest.json").read_bytes(),
            )
        )

    assert artifacts[0] == artifacts[1]
    assert artifacts[0][0] != artifacts[2][0]
    assert artifacts[0][1] != artifacts[2][1]


def test_preflight_tasks_accepts_public_direct_track_name(tmp_path) -> None:
    manifest_path = tmp_path / "manifest.json"
    report_path = tmp_path / "preflight.json"
    manifest_path.write_text(json.dumps({"planted_paths": []}))

    result = CliRunner().invoke(
        main,
        [
            "preflight-tasks",
            "--manifest",
            str(manifest_path),
            "--track",
            "direct",
            "--output",
            str(report_path),
        ],
    )

    assert result.exit_code == 0, result.output
    report = json.loads(report_path.read_text())
    assert report["track"] == "direct"
    assert report["ok"] is True
