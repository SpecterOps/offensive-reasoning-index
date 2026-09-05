import csv
import json
from pathlib import Path

import pytest

from scripts import build_mcp_aliasfix_rich_report as report


def historical_run(root: Path) -> None:
    root.mkdir()
    for _, profile, _ in report.PROFILES:
        output = root / "outputs" / profile
        output.mkdir(parents=True)
        summary = dict.fromkeys([
            "correct", "tier1_correct", "tier1_total", "tier3_correct", "tier3_total",
            "tier4_correct", "tier4_total", "tier5_correct", "tier5_total",
            "avg_tool_calls", "hallucinations", "no_path_reported", "incomplete_answers",
            "cypher_query_calls", "non_cypher_tool_calls",
        ], "0")
        with (output / "baseline_summary.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(summary))
            writer.writeheader()
            writer.writerow(summary)
        with (output / "baseline_combined.csv").open("w", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["task_id", "outcome", "tier", "score", "question",
                             "final_answer_raw"])
            writer.writerow(["fixture-1", "CORRECT", "1", "1", "Synthetic question",
                             '{"entity": "<fixture>"}'])
    (root / "current-status.json").write_text("{}")
    (root / "status-events.jsonl").write_text("")


def test_report_requires_explicit_run_root(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as failure:
        report.main([])
    assert failure.value.code == 2
    assert list(tmp_path.iterdir()) == []


def test_report_uses_explicit_paths_and_preserves_existing_output(tmp_path: Path) -> None:
    run_root = tmp_path / "portable historical run"
    historical_run(run_root)
    destination = tmp_path / "private report with spaces"
    assert report.main(["--run-root", str(run_root), "--output-dir", str(destination)]) == 0
    manifest = json.loads((destination / "manifest.json").read_text())
    assert manifest["source_root"] == str(run_root.resolve())
    assert manifest["profiles"] == [p[1] for p in report.PROFILES]
    assert "not a certified public export" in (destination / "index.html").read_text()
    detail = destination / "tasks" / f"{report.PROFILES[0][1]}-fixture-1.html"
    assert "&lt;fixture&gt;" in detail.read_text()
    assert "Synthetic question" in detail.read_text()
    sentinel = destination / "do-not-delete.txt"
    sentinel.write_text("preserve")
    with pytest.raises(FileExistsError):
        report.build(run_root, destination)
    assert sentinel.read_text() == "preserve"
    assert report.build(run_root) == run_root / "rich-report"


def test_report_rejects_missing_sources_and_ancestor_output(tmp_path: Path) -> None:
    root = tmp_path / "missing run"
    with pytest.raises(FileNotFoundError):
        report.build(root)
    assert not root.exists()
    with pytest.raises(ValueError, match="source run root"):
        report.build(root, tmp_path)
