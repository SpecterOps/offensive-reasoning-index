"""CSV output and summary reporting."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .runner import EvalResult


CSV_FIELDNAMES = [
    "run_name",
    "requested_model",
    "resolved_model",
    "config_identity_json",
    "options_json",
    "task_id",
    "template_id",
    "tier",
    "category",
    "model",
    "score",
    "outcome",
    "hallucination",
    "tokens_input",
    "tokens_output",
    "elapsed_seconds",
    "task_wall_seconds",
    "output_tokens_per_second",
    "tokens_per_second_source",
    "grade_mode",
    "question",
    "model_cypher",
    "model_thinking",
    "error_detail",
    "parse_stage",
    "inspect_log",
    "inspect_sample_id",
    "inspect_sample_uuid",
    "inspect_model_calls",
    "inspect_error_retries",
    "eval_mode",
    "task_track",
    "final_answer_raw",
    "final_answer_normalized",
    "tool_calls_total",
    "failed_tool_calls",
    "unique_tools_used",
    "cypher_query_calls",
    "non_cypher_tool_calls",
    "agent_turns",
    "attempted_policy_violations",
    "trajectory_log",
    "server_prompt_used",
    "server_prompt_name",
    "resource_mode",
    "resource_reads_total",
    "unique_resources_used",
    "resource_characters_total",
    "ollama_version",
    "ollama_model_digest",
    "ollama_model_context_length",
    "ollama_model_size_bytes",
    "ollama_model_size_vram_bytes",
    "model_quantization_level",
    "telemetry_sample_ref",
]


def _run_name_for_result(r: EvalResult) -> str:
    return (
        getattr(r, "run_name", None)
        or getattr(r, "requested_model", None)
        or r.model_response.model
    )


def _requested_model_for_result(r: EvalResult) -> str:
    return getattr(r, "requested_model", None) or r.model_response.model


def _row_for_result(r: EvalResult) -> dict[str, object]:
    inspect_meta = r.inspect
    mcp_meta = r.mcp
    run_name = _run_name_for_result(r)
    requested_model = _requested_model_for_result(r)
    run_config = getattr(r, "run_config", None)
    telemetry = getattr(r, "telemetry", None) or {}
    config_identity_json = json.dumps(run_config, sort_keys=True) if run_config else ""
    options_json = ""
    if run_config and isinstance(run_config.get("options"), dict):
        options_json = json.dumps(run_config["options"], sort_keys=True)
    return {
        "run_name": run_name,
        "requested_model": requested_model,
        "resolved_model": r.model_response.model,
        "config_identity_json": config_identity_json,
        "options_json": options_json,
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
        "task_wall_seconds": (
            f"{telemetry.get('task_wall_seconds'):.2f}"
            if isinstance(telemetry.get("task_wall_seconds"), int | float)
            else ""
        ),
        "output_tokens_per_second": (
            f"{telemetry.get('output_tokens_per_second'):.4f}"
            if isinstance(telemetry.get("output_tokens_per_second"), int | float)
            else ""
        ),
        "tokens_per_second_source": telemetry.get("tokens_per_second_source", ""),
        "grade_mode": r.task.grade_mode,
        "question": r.task.question,
        "model_cypher": (r.model_response.cypher or "").replace("\n", " "),
        "model_thinking": (r.model_response.thinking or "").replace("\n", " "),
        "error_detail": (
            r.grade.details
            if r.grade.outcome
            in ("CYPHER_ERROR", "QUERY_TOO_EXPENSIVE", "MODEL_ERROR", "INFRA_ERROR")
            else ""
        ),
        "parse_stage": r.model_response.parse_stage,
        "inspect_log": inspect_meta.log_location if inspect_meta else "",
        "inspect_sample_id": inspect_meta.sample_id if inspect_meta else "",
        "inspect_sample_uuid": inspect_meta.sample_uuid if inspect_meta else "",
        "inspect_model_calls": inspect_meta.model_calls if inspect_meta else 0,
        "inspect_error_retries": inspect_meta.error_retries if inspect_meta else 0,
        "eval_mode": "mcp" if mcp_meta else "direct_cypher",
        "task_track": r.task.metadata.get("mcp_track", ""),
        "final_answer_raw": (mcp_meta.final_answer_raw if mcp_meta else "").replace("\n", " "),
        "final_answer_normalized": (
            mcp_meta.final_answer_normalized_json if mcp_meta else ""
        ).replace("\n", " "),
        "tool_calls_total": mcp_meta.tool_calls_total if mcp_meta else 0,
        "failed_tool_calls": mcp_meta.failed_tool_calls if mcp_meta else 0,
        "unique_tools_used": ",".join(mcp_meta.unique_tools_used) if mcp_meta else "",
        "cypher_query_calls": mcp_meta.cypher_query_calls if mcp_meta else 0,
        "non_cypher_tool_calls": mcp_meta.non_cypher_tool_calls if mcp_meta else 0,
        "agent_turns": mcp_meta.agent_turns if mcp_meta else 0,
        "attempted_policy_violations": (mcp_meta.attempted_policy_violations if mcp_meta else 0),
        "trajectory_log": mcp_meta.trajectory_log if mcp_meta else "",
        "server_prompt_used": mcp_meta.server_prompt_used if mcp_meta else False,
        "server_prompt_name": mcp_meta.server_prompt_name if mcp_meta else "",
        "resource_mode": mcp_meta.resource_mode if mcp_meta else "",
        "resource_reads_total": mcp_meta.resource_reads_total if mcp_meta else 0,
        "unique_resources_used": ",".join(mcp_meta.unique_resources_used) if mcp_meta else "",
        "resource_characters_total": mcp_meta.resource_characters_total if mcp_meta else 0,
        "ollama_version": telemetry.get("ollama_version", ""),
        "ollama_model_digest": telemetry.get("ollama_model_digest", ""),
        "ollama_model_context_length": telemetry.get("ollama_model_context_length", ""),
        "ollama_model_size_bytes": telemetry.get("ollama_model_size_bytes", ""),
        "ollama_model_size_vram_bytes": telemetry.get("ollama_model_size_vram_bytes", ""),
        "model_quantization_level": telemetry.get("model_quantization_level", ""),
        "telemetry_sample_ref": telemetry.get("sample_ref", ""),
    }


def write_csv(results: list[EvalResult], output_path: Path) -> None:
    """Write evaluation results to CSV."""
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()
        for r in results:
            writer.writerow(_row_for_result(r))


def write_combined_csv(all_results: dict[str, list[EvalResult]], output_path: Path) -> None:
    """Write all baseline task rows into a single CSV."""
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()
        for _model, results in all_results.items():
            for result in results:
                writer.writerow(_row_for_result(result))


def _stats(results: list[EvalResult]) -> dict:
    total = len(results)
    correct = sum(1 for r in results if r.grade.score == 1.0)
    hallucs = sum(1 for r in results if r.grade.hallucination)
    cypher_errors = sum(1 for r in results if r.grade.outcome == "CYPHER_ERROR")
    query_too_expensive = sum(1 for r in results if r.grade.outcome == "QUERY_TOO_EXPENSIVE")
    parse_fails = sum(1 for r in results if r.grade.outcome == "PARSE_FAIL")
    model_errors = sum(1 for r in results if r.grade.outcome == "MODEL_ERROR")
    infra_errors = sum(1 for r in results if r.grade.outcome == "INFRA_ERROR")
    tool_calls_total = sum(r.mcp.tool_calls_total for r in results if r.mcp)
    cypher_query_calls = sum(r.mcp.cypher_query_calls for r in results if r.mcp)
    non_cypher_tool_calls = sum(r.mcp.non_cypher_tool_calls for r in results if r.mcp)
    failed_tool_calls = sum(r.mcp.failed_tool_calls for r in results if r.mcp)
    policy_violations = sum(r.mcp.attempted_policy_violations for r in results if r.mcp)
    resource_reads_total = sum(r.mcp.resource_reads_total for r in results if r.mcp)
    resource_characters_total = sum(r.mcp.resource_characters_total for r in results if r.mcp)
    prompt_used = any(r.mcp.server_prompt_used for r in results if r.mcp)
    output_tps_values = [
        r.telemetry.get("output_tokens_per_second")
        for r in results
        if r.telemetry and isinstance(r.telemetry.get("output_tokens_per_second"), int | float)
    ]
    mcp_samples = sum(1 for r in results if r.mcp)
    tiers = {}
    for tier in (1, 2, 3):
        t = [r for r in results if r.task.tier == tier]
        tiers[tier] = (sum(1 for r in t if r.grade.score == 1.0), len(t)) if t else (0, 0)
    return {
        "total": total,
        "correct": correct,
        "hallucs": hallucs,
        "cypher_errors": cypher_errors,
        "query_too_expensive": query_too_expensive,
        "parse_fails": parse_fails,
        "model_errors": model_errors,
        "infra_errors": infra_errors,
        "tool_calls_total": tool_calls_total,
        "cypher_query_calls": cypher_query_calls,
        "non_cypher_tool_calls": non_cypher_tool_calls,
        "failed_tool_calls": failed_tool_calls,
        "policy_violations": policy_violations,
        "resource_reads_total": resource_reads_total,
        "resource_characters_total": resource_characters_total,
        "prompt_used": prompt_used,
        "avg_output_tokens_per_second": (
            sum(output_tps_values) / len(output_tps_values) if output_tps_values else None
        ),
        "mcp_samples": mcp_samples,
        "tiers": tiers,
    }


def write_summary_csv(all_results: dict[str, list[EvalResult]], output_path: Path) -> None:
    """Write one summary row per model."""
    fieldnames = [
        "run_name",
        "requested_model",
        "resolved_model",
        "config_identity_json",
        "options_json",
        "model",
        "total_tasks",
        "correct",
        "score_pct",
        "tier1_correct",
        "tier1_total",
        "tier1_pct",
        "tier2_correct",
        "tier2_total",
        "tier2_pct",
        "tier3_correct",
        "tier3_total",
        "tier3_pct",
        "hallucinations",
        "cypher_errors",
        "query_too_expensive",
        "parse_fails",
        "model_errors",
        "infra_errors",
        "avg_tool_calls",
        "cypher_query_calls",
        "non_cypher_tool_calls",
        "failed_tool_calls",
        "policy_violations",
        "avg_resource_reads",
        "resource_characters_total",
        "server_prompt_used",
        "avg_output_tokens_per_second",
        "ollama_version",
        "ollama_model_digest",
        "ollama_model_context_length",
        "model_quantization_level",
    ]
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for model, results in all_results.items():
            s = _stats(results)
            first = results[0] if results else None
            requested_model = _requested_model_for_result(first) if first else model
            resolved_model = first.model_response.model if first else requested_model
            run_name = _run_name_for_result(first) if first else model
            first_run_config = getattr(first, "run_config", None) if first else None
            config_identity_json = (
                json.dumps(first_run_config, sort_keys=True) if first_run_config else ""
            )
            options_json = ""
            if first_run_config and isinstance(first_run_config.get("options"), dict):
                options_json = json.dumps(first_run_config["options"], sort_keys=True)
            first_telemetry = getattr(first, "telemetry", None) if first else None
            first_telemetry = first_telemetry or {}

            def tier_fields(tier: int) -> tuple[int, int, int]:
                c, t = s["tiers"][tier]
                pct = (100 * c // t) if t else 0
                return c, t, pct

            t1c, t1t, t1p = tier_fields(1)
            t2c, t2t, t2p = tier_fields(2)
            t3c, t3t, t3p = tier_fields(3)
            writer.writerow(
                {
                    "run_name": run_name,
                    "requested_model": requested_model,
                    "resolved_model": resolved_model,
                    "config_identity_json": config_identity_json,
                    "options_json": options_json,
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
                    "cypher_errors": s["cypher_errors"],
                    "query_too_expensive": s["query_too_expensive"],
                    "parse_fails": s["parse_fails"],
                    "model_errors": s["model_errors"],
                    "infra_errors": s["infra_errors"],
                    "avg_tool_calls": round((s["tool_calls_total"] / s["mcp_samples"]), 2)
                    if s["mcp_samples"]
                    else 0.0,
                    "cypher_query_calls": s["cypher_query_calls"],
                    "non_cypher_tool_calls": s["non_cypher_tool_calls"],
                    "failed_tool_calls": s["failed_tool_calls"],
                    "policy_violations": s["policy_violations"],
                    "avg_resource_reads": round((s["resource_reads_total"] / s["mcp_samples"]), 2)
                    if s["mcp_samples"]
                    else 0.0,
                    "resource_characters_total": s["resource_characters_total"],
                    "server_prompt_used": s["prompt_used"],
                    "avg_output_tokens_per_second": (
                        round(s["avg_output_tokens_per_second"], 4)
                        if s["avg_output_tokens_per_second"] is not None
                        else ""
                    ),
                    "ollama_version": first_telemetry.get("ollama_version", ""),
                    "ollama_model_digest": first_telemetry.get("ollama_model_digest", ""),
                    "ollama_model_context_length": first_telemetry.get(
                        "ollama_model_context_length", ""
                    ),
                    "model_quantization_level": first_telemetry.get("model_quantization_level", ""),
                }
            )


def print_comparison(all_results: dict[str, list[EvalResult]]) -> None:
    """Print a multi-model comparison table."""
    col_w = 36
    print("\n" + "=" * 129)
    print("BASELINE COMPARISON")
    print("=" * 129)
    header = (
        f"{'Model':<{col_w}} {'Overall':>8} {'Tier1':>7} {'Tier2':>7} {'Tier3':>7} "
        f"{'Hallucs':>8} {'CyErr':>7} {'QExp':>6} {'Fails':>6} {'ModelErr':>9} {'InfraErr':>9}"
    )
    print(header)
    print("-" * 129)

    for model, results in all_results.items():
        s = _stats(results)
        f"{100 * s['correct'] // s['total']}%" if s["total"] else "0%"

        def tp(tier):
            c, t = s["tiers"][tier]
            return f"{100 * c // t}%" if t else "n/a"

        short_model = model.split("/", 1)[-1][:col_w]
        print(
            f"{short_model:<{col_w}} {s['correct']:>4}/{s['total']:<3} "
            f"{tp(1):>7} {tp(2):>7} {tp(3):>7} "
            f"{s['hallucs']:>8} {s['cypher_errors']:>7} {s['query_too_expensive']:>6} "
            f"{s['parse_fails']:>6} "
            f"{s['model_errors']:>9} {s['infra_errors']:>9}"
        )
        if s["mcp_samples"]:
            avg_tools = s["tool_calls_total"] / s["mcp_samples"]
            print(
                f"{'':<{col_w}} {'tools(avg)':>8} {avg_tools:>7.2f} "
                f"{'cypher':>7} {s['cypher_query_calls']:>7} {'noncy':>7} {s['non_cypher_tool_calls']:>7} "  # noqa: E501
                f"{'toolfail':>9} {s['failed_tool_calls']:>9} {'policy':>9} {s['policy_violations']:>9}"  # noqa: E501
            )

    print("=" * 129)


def print_summary(results: list[EvalResult], model: str) -> None:
    """Print a summary table to stdout."""
    total = len(results)
    correct = sum(1 for r in results if r.grade.score == 1.0)
    hallucinations = sum(1 for r in results if r.grade.hallucination)
    parse_fails = sum(1 for r in results if r.grade.outcome == "PARSE_FAIL")
    cypher_errors = sum(1 for r in results if r.grade.outcome == "CYPHER_ERROR")
    query_too_expensive = sum(1 for r in results if r.grade.outcome == "QUERY_TOO_EXPENSIVE")
    model_errors = sum(1 for r in results if r.grade.outcome == "MODEL_ERROR")
    infra_errors = sum(1 for r in results if r.grade.outcome == "INFRA_ERROR")

    def tier_score(tier: int) -> str:
        t = [r for r in results if r.task.tier == tier]
        if not t:
            return "n/a"
        c = sum(1 for r in t if r.grade.score == 1.0)
        return f"{c}/{len(t)} ({100 * c // len(t)}%)"

    pct = f"{100 * correct // total}%" if total else "0%"
    print("\n" + "=" * 60)
    print(f"Model:  {model}")
    print(f"Tasks:  {total}  |  Score: {correct}/{total} ({pct})")
    print(f"Tier 1: {tier_score(1)}  |  Tier 2: {tier_score(2)}  |  Tier 3: {tier_score(3)}")
    print(
        f"Hallucinations: {hallucinations}  |  Parse failures: {parse_fails}  |  "
        f"Cypher errors: {cypher_errors}  |  Query too expensive: {query_too_expensive}  |  "
        f"Model errors: {model_errors}  |  Infra errors: {infra_errors}"
    )
    if any(r.mcp for r in results):
        mcp = _stats(results)
        avg_tools = mcp["tool_calls_total"] / mcp["mcp_samples"] if mcp["mcp_samples"] else 0.0
        print(
            f"Avg tool calls: {avg_tools:.2f}  |  Cypher tool calls: {mcp['cypher_query_calls']}  |  "  # noqa: E501
            f"Non-cypher tool calls: {mcp['non_cypher_tool_calls']}  |  "
            f"Failed tool calls: {mcp['failed_tool_calls']}  |  "
            f"Policy violations: {mcp['policy_violations']}"
        )
    print("=" * 60)
