"""CSV output and summary reporting."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .runner import EvalResult


def write_csv(results: list["EvalResult"], output_path: Path) -> None:
    """Write evaluation results to CSV."""
    fieldnames = [
        "task_id", "template_id", "tier", "category", "model",
        "score", "outcome", "hallucination",
        "tokens_input", "tokens_output", "elapsed_seconds",
        "grade_mode", "question", "model_cypher", "error_detail",
    ]
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            writer.writerow({
                "task_id": r.task.id,
                "template_id": r.task.template_id,
                "tier": r.task.tier,
                "category": r.task.category,
                "model": r.model_response.model,
                "score": r.grade.score,
                "outcome": r.grade.outcome,
                "hallucination": r.grade.hallucination,
                "tokens_input": r.model_response.tokens_input,
                "tokens_output": r.model_response.tokens_output,
                "elapsed_seconds": f"{r.model_response.elapsed_seconds:.2f}",
                "grade_mode": r.task.grade_mode,
                "question": r.task.question,
                "model_cypher": (r.model_response.cypher or "").replace("\n", " "),
                "error_detail": r.grade.details if r.grade.outcome in ("CYPHER_ERROR", "MODEL_ERROR") else "",
            })


def print_summary(results: list["EvalResult"], model: str) -> None:
    """Print a summary table to stdout."""
    total = len(results)
    correct = sum(1 for r in results if r.grade.score == 1.0)
    hallucinations = sum(1 for r in results if r.grade.hallucination)
    parse_fails = sum(1 for r in results if r.grade.outcome == "PARSE_FAIL")
    cypher_errors = sum(1 for r in results if r.grade.outcome == "CYPHER_ERROR")

    def tier_score(tier: int) -> str:
        t = [r for r in results if r.task.tier == tier]
        if not t:
            return "n/a"
        c = sum(1 for r in t if r.grade.score == 1.0)
        return f"{c}/{len(t)} ({100*c//len(t)}%)"

    pct = f"{100 * correct // total}%" if total else "0%"
    print("\n" + "=" * 60)
    print(f"Model:  {model}")
    print(f"Tasks:  {total}  |  Score: {correct}/{total} ({pct})")
    print(f"Tier 1: {tier_score(1)}  |  Tier 2: {tier_score(2)}  |  Tier 3: {tier_score(3)}")
    print(f"Hallucinations: {hallucinations}  |  Parse failures: {parse_fails}  |  Cypher errors: {cypher_errors}")
    print("=" * 60)
