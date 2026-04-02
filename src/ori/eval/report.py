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
        "parse_stage", "inspect_log", "inspect_sample_id", "inspect_sample_uuid",
        "inspect_model_calls", "inspect_error_retries",
    ]
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            inspect_meta = r.inspect
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
                "parse_stage": r.model_response.parse_stage,
                "inspect_log": inspect_meta.log_location if inspect_meta else "",
                "inspect_sample_id": inspect_meta.sample_id if inspect_meta else "",
                "inspect_sample_uuid": inspect_meta.sample_uuid if inspect_meta else "",
                "inspect_model_calls": inspect_meta.model_calls if inspect_meta else 0,
                "inspect_error_retries": inspect_meta.error_retries if inspect_meta else 0,
            })


def print_comparison(all_results: dict[str, list["EvalResult"]]) -> None:
    """Print a multi-model comparison table."""
    def _stats(results: list["EvalResult"]) -> dict:
        total = len(results)
        correct = sum(1 for r in results if r.grade.score == 1.0)
        hallucs = sum(1 for r in results if r.grade.hallucination)
        errors = sum(1 for r in results if r.grade.outcome == "CYPHER_ERROR")
        parse_fails = sum(1 for r in results if r.grade.outcome == "PARSE_FAIL")
        tiers = {}
        for tier in (1, 2, 3):
            t = [r for r in results if r.task.tier == tier]
            tiers[tier] = (sum(1 for r in t if r.grade.score == 1.0), len(t)) if t else (0, 0)
        return {"total": total, "correct": correct, "hallucs": hallucs,
                "errors": errors, "parse_fails": parse_fails, "tiers": tiers}

    col_w = 36
    print("\n" + "=" * 100)
    print("BASELINE COMPARISON")
    print("=" * 100)
    header = f"{'Model':<{col_w}} {'Overall':>8} {'Tier1':>7} {'Tier2':>7} {'Tier3':>7} {'Hallucs':>8} {'Errors':>7} {'Fails':>6}"
    print(header)
    print("-" * 100)

    for model, results in all_results.items():
        s = _stats(results)
        pct = f"{100 * s['correct'] // s['total']}%" if s['total'] else "0%"

        def tp(tier):
            c, t = s['tiers'][tier]
            return f"{100*c//t}%" if t else "n/a"

        short_model = model.split("/", 1)[-1][:col_w]
        print(f"{short_model:<{col_w}} {s['correct']:>4}/{s['total']:<3} {tp(1):>7} {tp(2):>7} {tp(3):>7} {s['hallucs']:>8} {s['errors']:>7} {s['parse_fails']:>6}")

    print("=" * 100)


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
