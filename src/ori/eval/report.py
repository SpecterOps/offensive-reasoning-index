"""CSV output and summary reporting."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .runner import EvalResult


CSV_FIELDNAMES = [
    "task_id", "template_id", "tier", "category", "model",
    "score", "outcome", "hallucination",
    "tokens_input", "tokens_output", "elapsed_seconds",
    "grade_mode", "question", "model_cypher", "error_detail",
    "parse_stage", "inspect_log", "inspect_sample_id", "inspect_sample_uuid",
    "inspect_model_calls", "inspect_error_retries",
]


def _row_for_result(r: "EvalResult") -> dict[str, object]:
    inspect_meta = r.inspect
    return {
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
        "error_detail": (
            r.grade.details if r.grade.outcome in ("CYPHER_ERROR", "MODEL_ERROR", "INFRA_ERROR") else ""
        ),
        "parse_stage": r.model_response.parse_stage,
        "inspect_log": inspect_meta.log_location if inspect_meta else "",
        "inspect_sample_id": inspect_meta.sample_id if inspect_meta else "",
        "inspect_sample_uuid": inspect_meta.sample_uuid if inspect_meta else "",
        "inspect_model_calls": inspect_meta.model_calls if inspect_meta else 0,
        "inspect_error_retries": inspect_meta.error_retries if inspect_meta else 0,
    }


def write_csv(results: list["EvalResult"], output_path: Path) -> None:
    """Write evaluation results to CSV."""
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()
        for r in results:
            writer.writerow(_row_for_result(r))


def write_combined_csv(all_results: dict[str, list["EvalResult"]], output_path: Path) -> None:
    """Write all baseline task rows into a single CSV."""
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()
        for _model, results in all_results.items():
            for result in results:
                writer.writerow(_row_for_result(result))


def _stats(results: list["EvalResult"]) -> dict:
    total = len(results)
    correct = sum(1 for r in results if r.grade.score == 1.0)
    hallucs = sum(1 for r in results if r.grade.hallucination)
    errors = sum(1 for r in results if r.grade.outcome == "CYPHER_ERROR")
    parse_fails = sum(1 for r in results if r.grade.outcome == "PARSE_FAIL")
    model_errors = sum(1 for r in results if r.grade.outcome == "MODEL_ERROR")
    infra_errors = sum(1 for r in results if r.grade.outcome == "INFRA_ERROR")
    tiers = {}
    for tier in (1, 2, 3):
        t = [r for r in results if r.task.tier == tier]
        tiers[tier] = (sum(1 for r in t if r.grade.score == 1.0), len(t)) if t else (0, 0)
    return {
        "total": total,
        "correct": correct,
        "hallucs": hallucs,
        "errors": errors,
        "parse_fails": parse_fails,
        "model_errors": model_errors,
        "infra_errors": infra_errors,
        "tiers": tiers,
    }


def write_summary_csv(all_results: dict[str, list["EvalResult"]], output_path: Path) -> None:
    """Write one summary row per model."""
    fieldnames = [
        "model", "total_tasks", "correct", "score_pct",
        "tier1_correct", "tier1_total", "tier1_pct",
        "tier2_correct", "tier2_total", "tier2_pct",
        "tier3_correct", "tier3_total", "tier3_pct",
        "hallucinations", "cypher_errors", "parse_fails", "model_errors", "infra_errors",
    ]
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for model, results in all_results.items():
            s = _stats(results)

            def tier_fields(tier: int) -> tuple[int, int, int]:
                c, t = s["tiers"][tier]
                pct = (100 * c // t) if t else 0
                return c, t, pct

            t1c, t1t, t1p = tier_fields(1)
            t2c, t2t, t2p = tier_fields(2)
            t3c, t3t, t3p = tier_fields(3)
            writer.writerow({
                "model": model,
                "total_tasks": s["total"],
                "correct": s["correct"],
                "score_pct": (100 * s["correct"] // s["total"]) if s["total"] else 0,
                "tier1_correct": t1c,
                "tier1_total": t1t,
                "tier1_pct": t1p,
                "tier2_correct": t2c,
                "tier2_total": t2t,
                "tier2_pct": t2p,
                "tier3_correct": t3c,
                "tier3_total": t3t,
                "tier3_pct": t3p,
                "hallucinations": s["hallucs"],
                "cypher_errors": s["errors"],
                "parse_fails": s["parse_fails"],
                "model_errors": s["model_errors"],
                "infra_errors": s["infra_errors"],
            })


def print_comparison(all_results: dict[str, list["EvalResult"]]) -> None:
    """Print a multi-model comparison table."""
    col_w = 36
    print("\n" + "=" * 121)
    print("BASELINE COMPARISON")
    print("=" * 121)
    header = (
        f"{'Model':<{col_w}} {'Overall':>8} {'Tier1':>7} {'Tier2':>7} {'Tier3':>7} "
        f"{'Hallucs':>8} {'Errors':>7} {'Fails':>6} {'ModelErr':>9} {'InfraErr':>9}"
    )
    print(header)
    print("-" * 121)

    for model, results in all_results.items():
        s = _stats(results)
        pct = f"{100 * s['correct'] // s['total']}%" if s['total'] else "0%"

        def tp(tier):
            c, t = s['tiers'][tier]
            return f"{100*c//t}%" if t else "n/a"

        short_model = model.split("/", 1)[-1][:col_w]
        print(
            f"{short_model:<{col_w}} {s['correct']:>4}/{s['total']:<3} "
            f"{tp(1):>7} {tp(2):>7} {tp(3):>7} "
            f"{s['hallucs']:>8} {s['errors']:>7} {s['parse_fails']:>6} "
            f"{s['model_errors']:>9} {s['infra_errors']:>9}"
        )

    print("=" * 121)


def print_summary(results: list["EvalResult"], model: str) -> None:
    """Print a summary table to stdout."""
    total = len(results)
    correct = sum(1 for r in results if r.grade.score == 1.0)
    hallucinations = sum(1 for r in results if r.grade.hallucination)
    parse_fails = sum(1 for r in results if r.grade.outcome == "PARSE_FAIL")
    cypher_errors = sum(1 for r in results if r.grade.outcome == "CYPHER_ERROR")
    model_errors = sum(1 for r in results if r.grade.outcome == "MODEL_ERROR")
    infra_errors = sum(1 for r in results if r.grade.outcome == "INFRA_ERROR")

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
    print(
        f"Hallucinations: {hallucinations}  |  Parse failures: {parse_fails}  |  "
        f"Cypher errors: {cypher_errors}  |  Model errors: {model_errors}  |  Infra errors: {infra_errors}"
    )
    print("=" * 60)
