from __future__ import annotations

import pytest

from ori.benchmarks import describe_benchmark, get_benchmark, list_benchmarks


def test_list_benchmarks_exposes_simple_and_complex_in_public_order() -> None:
    benchmarks = list_benchmarks()

    assert [benchmark.name for benchmark in benchmarks] == ["simple", "complex"]
    assert benchmarks[0].source_phase == "phase3"
    assert benchmarks[1].source_phase == "phase4"


def test_simple_benchmark_is_phase3_fast_profile() -> None:
    simple = get_benchmark("simple")

    assert simple.title == "Simple benchmark"
    assert simple.graph_profile == "phase3_simple"
    assert simple.official_task_set == "phase3_simple_official"
    assert simple.default_task_count == 40
    assert simple.diagnostic_task_set is None
    assert simple.supported_modes == ("direct", "mcp", "mock")
    assert simple.scoring_profile == "standard"


def test_complex_benchmark_is_phase4_profile_with_diagnostic_slice() -> None:
    complex_benchmark = get_benchmark("complex")

    assert complex_benchmark.title == "Complex benchmark"
    assert complex_benchmark.graph_profile == "phase4_complex"
    assert complex_benchmark.official_task_set == "phase4_complex_official"
    assert complex_benchmark.diagnostic_task_set == "phase4_complex_diagnostic"
    assert complex_benchmark.default_task_count == 100
    assert complex_benchmark.diagnostic_task_count == 24
    assert complex_benchmark.supports_diagnostic is True
    assert complex_benchmark.scoring_profile == "mechanism_decision_complex"


def test_benchmark_lookup_is_case_and_whitespace_insensitive() -> None:
    assert get_benchmark(" Complex ").name == "complex"


def test_unknown_benchmark_reports_known_names() -> None:
    with pytest.raises(ValueError, match="Available benchmarks: simple, complex"):
        get_benchmark("phase4c")


def test_describe_benchmark_renders_operator_summary() -> None:
    description = describe_benchmark("complex")

    assert "Complex benchmark (complex)" in description
    assert "Graph profile: phase4_complex" in description
    assert "Diagnostic task set: phase4_complex_diagnostic" in description
    assert "Supported modes: direct, mcp, mock, diagnostic" in description
