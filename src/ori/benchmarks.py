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
BenchmarkTrackName = Literal["direct", "mcp"]


@dataclass(frozen=True)
class BenchmarkTrack:
    """One scoring surface for a benchmark using the same generated dataset."""

    name: BenchmarkTrackName
    title: str
    task_set: str
    diagnostic_task_set: str | None
    task_count: int
    diagnostic_task_count: int | None
    scoring_profile: str
    description: str


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
    tracks: tuple[BenchmarkTrack, ...]

    @property
    def supports_diagnostic(self) -> bool:
        return self.diagnostic_task_set is not None and "diagnostic" in self.supported_modes

    def track(self, name: BenchmarkTrackName) -> BenchmarkTrack:
        """Return one evaluation track by name."""

        for track in self.tracks:
            if track.name == name:
                return track
        known = ", ".join(track.name for track in self.tracks)
        raise ValueError(
            f"Benchmark {self.name!r} has no track {name!r}. Available tracks: {known}"
        )


def _standard_tracks(
    prefix: str, *, task_count: int, diagnostic_task_count: int | None
) -> tuple[BenchmarkTrack, ...]:
    diagnostic_direct = f"{prefix}_direct_diagnostic" if diagnostic_task_count is not None else None
    diagnostic_mcp = f"{prefix}_mcp_diagnostic" if diagnostic_task_count is not None else None
    return (
        BenchmarkTrack(
            name="direct",
            title="Direct Cypher track",
            task_set=f"{prefix}_direct_official",
            diagnostic_task_set=diagnostic_direct,
            task_count=task_count,
            diagnostic_task_count=diagnostic_task_count,
            scoring_profile="direct_cypher",
            description=(
                "No-tool scoring surface. The model answers from the prompt by producing "
                "Cypher or a direct structured answer against the same generated graph."
            ),
        ),
        BenchmarkTrack(
            name="mcp",
            title="BloodHound MCP track",
            task_set=f"{prefix}_mcp_official",
            diagnostic_task_set=diagnostic_mcp,
            task_count=task_count,
            diagnostic_task_count=diagnostic_task_count,
            scoring_profile="tool_assisted_graph_reasoning",
            description=(
                "Tool-use scoring surface. The model uses BloodHound MCP tools against "
                "the same generated and verified BloodHound CE graph."
            ),
        ),
    )


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
        tracks=_standard_tracks("phase3_simple", task_count=40, diagnostic_task_count=None),
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
        tracks=_standard_tracks("phase4_complex", task_count=100, diagnostic_task_count=24),
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
        f"Default tasks per track: {benchmark.default_task_count}",
        f"Supported modes: {', '.join(benchmark.supported_modes)}",
        f"Tracks: {', '.join(track.name for track in benchmark.tracks)}",
        f"Expected runtime: {benchmark.expected_runtime}",
        f"Scoring profile: {benchmark.scoring_profile}",
        f"Summary: {benchmark.summary}",
        "",
        "Evaluation tracks use the same generated dataset and manifest:",
    ]
    for track in benchmark.tracks:
        lines.extend(
            [
                f"- {track.name}: {track.task_count} tasks ({track.task_set})",
                f"  {track.description}",
            ]
        )
        if track.diagnostic_task_set:
            lines.append(
                f"  Diagnostic: {track.diagnostic_task_count} tasks ({track.diagnostic_task_set})"
            )
    if benchmark.diagnostic_task_set:
        lines.insert(5, f"Diagnostic task set: {benchmark.diagnostic_task_set}")
        lines.insert(7, f"Diagnostic tasks per track: {benchmark.diagnostic_task_count}")
    return "\n".join(lines)
