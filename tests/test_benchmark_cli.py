from __future__ import annotations

from click.testing import CliRunner

from ori.cli import main


def test_benchmark_list_cli_shows_simple_and_complex() -> None:
    result = CliRunner().invoke(main, ["benchmark", "list"])

    assert result.exit_code == 0
    assert "simple" in result.output
    assert "complex" in result.output
    assert "Fast Phase 3-derived benchmark" in result.output


def test_benchmark_describe_cli_shows_complex_contract() -> None:
    result = CliRunner().invoke(main, ["benchmark", "describe", "complex"])

    assert result.exit_code == 0
    assert "Complex benchmark (complex)" in result.output
    assert "Graph profile: phase4_complex" in result.output
    assert "Diagnostic task set: phase4_complex_diagnostic" in result.output


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
