"""Named benchmark product registry for ORI.

This layer keeps public/operator benchmark names stable while the underlying
phase/config implementation can evolve.  It deliberately does not launch runs;
it gives the CLI and docs one source of truth for what "simple" and "complex"
mean.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

BenchmarkMode = Literal["direct", "mcp", "mock", "diagnostic"]


@dataclass(frozen=True)
class BenchmarkDefinition:
    """Public benchmark product metadata."""

    name: str
    title: str
    summary: str
    source_phase: str
    graph_profile: str
    official_task_set: str
    diagnostic_task_set: str | None
    default_task_count: int
    diagnostic_task_count: int | None
    supported_modes: tuple[BenchmarkMode, ...]
    expected_runtime: str
    scoring_profile: str
    status: str

    @property
    def supports_diagnostic(self) -> bool:
        return self.diagnostic_task_set is not None and "diagnostic" in self.supported_modes


BENCHMARKS: dict[str, BenchmarkDefinition] = {
    "simple": BenchmarkDefinition(
        name="simple",
        title="Simple benchmark",
        summary=(
            "Fast Phase 3-derived benchmark for smoke testing, local model triage, "
            "and broad user adoption."
        ),
        source_phase="phase3",
        graph_profile="phase3_simple",
        official_task_set="phase3_simple_official",
        diagnostic_task_set=None,
        default_task_count=40,
        diagnostic_task_count=None,
        supported_modes=("direct", "mcp", "mock"),
        expected_runtime="short",
        scoring_profile="standard",
        status="planned",
    ),
    "complex": BenchmarkDefinition(
        name="complex",
        title="Complex benchmark",
        summary=(
            "Phase 4-derived realistic enterprise benchmark for serious model/tool "
            "reasoning, including the highest-tier attacker-decision tasks."
        ),
        source_phase="phase4",
        graph_profile="phase4_complex",
        official_task_set="phase4_complex_official",
        diagnostic_task_set="phase4_complex_diagnostic",
        default_task_count=100,
        diagnostic_task_count=24,
        supported_modes=("direct", "mcp", "mock", "diagnostic"),
        expected_runtime="long",
        scoring_profile="mechanism_decision_complex",
        status="planned",
    ),
}


def list_benchmarks() -> tuple[BenchmarkDefinition, ...]:
    """Return benchmark definitions in stable public order."""

    return tuple(BENCHMARKS[name] for name in ("simple", "complex"))


def get_benchmark(name: str) -> BenchmarkDefinition:
    """Look up a benchmark definition by public name."""

    normalized = name.strip().lower()
    try:
        return BENCHMARKS[normalized]
    except KeyError as exc:
        known = ", ".join(BENCHMARKS)
        raise ValueError(f"Unknown benchmark {name!r}. Available benchmarks: {known}") from exc


def describe_benchmark(name: str) -> str:
    """Render a concise human-readable benchmark description."""

    benchmark = get_benchmark(name)
    lines = [
        f"{benchmark.title} ({benchmark.name})",
        f"Status: {benchmark.status}",
        f"Source phase: {benchmark.source_phase}",
        f"Graph profile: {benchmark.graph_profile}",
        f"Official task set: {benchmark.official_task_set}",
        f"Default tasks: {benchmark.default_task_count}",
        f"Supported modes: {', '.join(benchmark.supported_modes)}",
        f"Expected runtime: {benchmark.expected_runtime}",
        f"Scoring profile: {benchmark.scoring_profile}",
        f"Summary: {benchmark.summary}",
    ]
    if benchmark.diagnostic_task_set:
        lines.insert(5, f"Diagnostic task set: {benchmark.diagnostic_task_set}")
        lines.insert(7, f"Diagnostic tasks: {benchmark.diagnostic_task_count}")
    return "\n".join(lines)
