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
    "run_index",
    "runs_per_model",
    "task_id",
    "template_id",
    "tier",
    "category",
    "model",
    "score",
    "outcome",
    "failure_subtype",
    "query_executed",
    "query_attempts",
    "query_fingerprint",
    "safety_policy_version",
    "safety_rule",
    "bhce_health_after",
    "circuit_state",
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
    "available_prompt_names",
    "prompt_discovery_status",
    "resource_mode",
    "mcp_tool_loop",
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
    "partial_result",
    "infra_error_subtype",
    "failure_stage",
    "evidence_found",
    "evidence_depth_score",
    "reference_entities_seen_count",
    "reference_path_nodes_seen_count",
    "final_answer_contract_valid",
    "invalid_entities",
    "missing_required_entities",
    "finalization_guard_used",
    "repair_turn_used",
    "minimum_evidence_satisfied",
    "successful_tool_results",
    "reasoning_capture_mode",
    "reasoning_token_count",
    "loop_exhaustion_with_evidence",
    "attempt_number",
    "result_source",
]


def _truthy(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _int_field(row: dict[str, object], key: str) -> int:
    try:
        return int(str(row.get(key, 0) or 0))
    except ValueError:
        return 0


def classify_summary_health(row: dict[str, object]) -> tuple[str, str]:
    """Classify run health from durable summary fields."""
    completed = _int_field(row, "completed_tasks")
    expected = _int_field(row, "expected_tasks")
    infra_errors = _int_field(row, "infra_errors")
    run_complete = _truthy(row.get("run_complete", True))
    partial_result = _truthy(row.get("partial_result", False))
    reasons: list[str] = []
    if not run_complete:
        reasons.append("run_complete=False")
    if partial_result:
        reasons.append("partial_result=True")
    if expected and completed < expected:
        reasons.append(f"completed_tasks={completed}<expected_tasks={expected}")
    if reasons:
        if infra_errors:
            reasons.append(f"infra_errors={infra_errors}")
        return "partial", "; ".join(reasons)
    if infra_errors:
        return "suspect", f"infra_errors={infra_errors}"
    return "complete", "run_complete=True"


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
    diag = mcp_meta.final_answer_diagnostics if mcp_meta else {}
    options_json = ""
    if run_config and isinstance(run_config.get("options"), dict):
        options_json = json.dumps(run_config["options"], sort_keys=True)
    return {
        "run_name": run_name,
        "requested_model": requested_model,
        "resolved_model": r.model_response.model,
        "config_identity_json": config_identity_json,
        "options_json": options_json,
        "run_index": run_config.get("run_index", 1) if run_config else 1,
        "runs_per_model": run_config.get("runs_per_model", 1) if run_config else 1,
        "task_id": r.task.id,
        "template_id": r.task.template_id,
        "tier": r.task.tier,
        "category": r.task.category,
        "model": r.model_response.model,
        "score": r.grade.score,
        "outcome": r.grade.outcome,
        "failure_subtype": (
            mcp_meta.failure_subtype
            if mcp_meta
            else r.model_result.failure_subtype
        ),
        "query_executed": r.model_result.query_executed if not mcp_meta else "",
        "query_attempts": r.model_result.execution_attempts if not mcp_meta else "",
        "query_fingerprint": r.model_result.query_fingerprint if not mcp_meta else "",
        "safety_policy_version": (
            r.model_result.safety_policy_version if not mcp_meta else ""
        ),
        "safety_rule": r.model_result.safety_rule if not mcp_meta else "",
        "bhce_health_after": r.model_result.bhce_health_after if not mcp_meta else "",
        "circuit_state": r.model_result.circuit_state if not mcp_meta else "",
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
            in (
                "CYPHER_ERROR",
                "QUERY_TOO_EXPENSIVE",
                "MODEL_ERROR",
                "LOOP_EXHAUSTED",
                "INFRA_ERROR",
            )
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
        "available_prompt_names": (",".join(mcp_meta.available_prompt_names) if mcp_meta else ""),
        "prompt_discovery_status": mcp_meta.prompt_discovery_status if mcp_meta else "",
        "resource_mode": mcp_meta.resource_mode if mcp_meta else "",
        "mcp_tool_loop": mcp_meta.tool_loop if mcp_meta else "",
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
        "partial_result": getattr(r, "partial_result", False),
        "infra_error_subtype": (
            mcp_meta.infra_error_subtype
            if mcp_meta
            else (
                r.model_result.failure_subtype
                if r.grade.outcome == "INFRA_ERROR"
                else ""
            )
        ),
        "failure_stage": diag.get("failure_stage", ""),
        "evidence_found": diag.get("evidence_found", False),
        "evidence_depth_score": diag.get("evidence_depth_score", 0),
        "reference_entities_seen_count": diag.get("reference_entities_seen_count", 0),
        "reference_path_nodes_seen_count": diag.get("reference_path_nodes_seen_count", 0),
        "final_answer_contract_valid": diag.get("final_answer_contract_valid", False),
        "invalid_entities": ",".join(diag.get("invalid_entities", [])),
        "missing_required_entities": ",".join(diag.get("missing_required_entities", [])),
        "finalization_guard_used": diag.get(
            "finalization_guard_used", mcp_meta.finalization_guard_used if mcp_meta else False
        ),
        "repair_turn_used": diag.get(
            "repair_turn_used", mcp_meta.repair_turn_used if mcp_meta else False
        ),
        "minimum_evidence_satisfied": diag.get(
            "minimum_evidence_satisfied", mcp_meta.minimum_evidence_satisfied if mcp_meta else False
        ),
        "successful_tool_results": diag.get(
            "successful_tool_results", mcp_meta.successful_tool_results if mcp_meta else 0
        ),
        "reasoning_capture_mode": diag.get(
            "reasoning_capture_mode", "model_thinking" if r.model_response.thinking else "none"
        ),
        "reasoning_token_count": diag.get(
            "reasoning_token_count", len((r.model_response.thinking or "").split())
        ),
        "loop_exhaustion_with_evidence": (
            mcp_meta.loop_exhaustion_with_evidence if mcp_meta else False
        ),
        "attempt_number": getattr(r, "attempt_number", 1),
        "result_source": getattr(r, "result_source", "first_pass"),
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
    outcome_counts = {
        outcome: sum(1 for r in results if r.grade.outcome == outcome)
        for outcome in sorted({r.grade.outcome for r in results})
    }
    accounted_outcomes = sum(outcome_counts.values())
    incorrect = sum(1 for r in results if r.grade.outcome == "INCORRECT")
    hallucs = sum(1 for r in results if r.grade.hallucination)
    cypher_errors = sum(1 for r in results if r.grade.outcome == "CYPHER_ERROR")
    query_too_expensive = sum(1 for r in results if r.grade.outcome == "QUERY_TOO_EXPENSIVE")
    policy_rejections = sum(
        1 for r in results if r.model_result.failure_type == "policy_rejected"
    )
    server_query_timeouts = sum(
        1 for r in results if r.model_result.failure_type == "query_timeout"
    )
    circuit_open_skips = sum(
        1 for r in results if r.model_result.failure_type == "circuit_open"
    )
    executed_direct_queries = sum(
        1 for r in results if not r.mcp and r.model_result.query_executed
    )
    parse_fails = sum(1 for r in results if r.grade.outcome == "PARSE_FAIL")
    model_errors = sum(1 for r in results if r.grade.outcome == "MODEL_ERROR")
    loop_exhaustions = sum(1 for r in results if r.grade.outcome == "LOOP_EXHAUSTED")
    no_path_reported = sum(
        1 for r in results if r.mcp and r.mcp.failure_subtype == "NO_PATH_REPORTED"
    )
    wrong_path = sum(1 for r in results if r.mcp and r.mcp.failure_subtype == "WRONG_PATH")
    incomplete_answers = sum(
        1 for r in results if r.mcp and r.mcp.failure_subtype == "INCOMPLETE_ANSWER"
    )
    infra_errors = sum(1 for r in results if r.grade.outcome == "INFRA_ERROR")
    partial_results = sum(1 for r in results if getattr(r, "partial_result", False))
    completed_tasks = total - partial_results
    correct_completed = sum(
        1 for r in results if r.grade.score == 1.0 and not getattr(r, "partial_result", False)
    )
    timeout_errors = sum(
        1
        for r in results
        if r.mcp
        and r.mcp.infra_error_subtype
        in {"MCP_TURN_TIMEOUT", "NO_PROGRESS_TIMEOUT", "SAMPLE_TIMEOUT", "OLLAMA_STREAM_TIMEOUT"}
    )
    tool_error_results = cypher_errors + query_too_expensive
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
    for tier in (1, 2, 3, 4, 5, 6):
        t = [r for r in results if r.task.tier == tier]
        tiers[tier] = (sum(1 for r in t if r.grade.score == 1.0), len(t)) if t else (0, 0)
    return {
        "total": total,
        "correct": correct,
        "incorrect": incorrect,
        "outcome_counts": outcome_counts,
        "accounted_outcomes": accounted_outcomes,
        "outcome_accounting_ok": accounted_outcomes == total,
        "completed_samples": completed_tasks,
        "correct_completed": correct_completed,
        "reasoning_accuracy": correct_completed / completed_tasks if completed_tasks else 0.0,
        "effective_accuracy": correct / total if total else 0.0,
        "infra_failure_rate": infra_errors / total if total else 0.0,
        "tool_error_rate": tool_error_results / total if total else 0.0,
        "timeout_rate": timeout_errors / total if total else 0.0,
        "hallucs": hallucs,
        "cypher_errors": cypher_errors,
        "query_too_expensive": query_too_expensive,
        "policy_rejections": policy_rejections,
        "server_query_timeouts": server_query_timeouts,
        "circuit_open_skips": circuit_open_skips,
        "executed_direct_queries": executed_direct_queries,
        "greedy_query_rate": query_too_expensive / total if total else 0.0,
        "parse_fails": parse_fails,
        "model_errors": model_errors,
        "loop_exhaustions": loop_exhaustions,
        "no_path_reported": no_path_reported,
        "wrong_path": wrong_path,
        "incomplete_answers": incomplete_answers,
        "infra_errors": infra_errors,
        "completed_tasks": completed_tasks,
        "expected_tasks": total,
        "partial_results": partial_results,
        "run_complete": partial_results == 0,
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
        "run_index",
        "runs_per_model",
        "model",
        "completed_tasks",
        "expected_tasks",
        "run_complete",
        "partial_result",
        "total_tasks",
        "correct",
        "incorrect",
        "outcome_accounted_tasks",
        "outcome_accounting_ok",
        "outcome_counts_json",
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
        "tier4_correct",
        "tier4_total",
        "tier4_pct",
        "tier5_correct",
        "tier5_total",
        "tier5_pct",
        "tier6_correct",
        "tier6_total",
        "tier6_pct",
        "hallucinations",
        "cypher_errors",
        "query_too_expensive",
        "policy_rejections",
        "server_query_timeouts",
        "circuit_open_skips",
        "executed_direct_queries",
        "greedy_query_rate",
        "parse_fails",
        "model_errors",
        "loop_exhaustions",
        "no_path_reported",
        "wrong_path",
        "incomplete_answers",
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
        "run_status",
        "run_status_detail",
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
            t4c, t4t, t4p = tier_fields(4)
            t5c, t5t, t5p = tier_fields(5)
            t6c, t6t, t6p = tier_fields(6)
            row = {
                "run_name": run_name,
                "requested_model": requested_model,
                "resolved_model": resolved_model,
                "config_identity_json": config_identity_json,
                "options_json": options_json,
                "run_index": first_run_config.get("run_index", 1) if first_run_config else 1,
                "runs_per_model": first_run_config.get("runs_per_model", 1)
                if first_run_config
                else 1,
                "model": model,
                "completed_tasks": s["completed_tasks"],
                "expected_tasks": s["expected_tasks"],
                "run_complete": s["run_complete"],
                "partial_result": s["partial_results"] > 0,
                "total_tasks": s["total"],
                "correct": s["correct"],
                "incorrect": s["incorrect"],
                "outcome_accounted_tasks": s["accounted_outcomes"],
                "outcome_accounting_ok": s["outcome_accounting_ok"],
                "outcome_counts_json": json.dumps(s["outcome_counts"], sort_keys=True),
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
                "tier4_correct": t4c,
                "tier4_total": t4t,
                "tier4_pct": t4p,
                "tier5_correct": t5c,
                "tier5_total": t5t,
                "tier5_pct": t5p,
                "tier6_correct": t6c,
                "tier6_total": t6t,
                "tier6_pct": t6p,
                "hallucinations": s["hallucs"],
                "cypher_errors": s["cypher_errors"],
                "query_too_expensive": s["query_too_expensive"],
                "policy_rejections": s["policy_rejections"],
                "server_query_timeouts": s["server_query_timeouts"],
                "circuit_open_skips": s["circuit_open_skips"],
                "executed_direct_queries": s["executed_direct_queries"],
                "greedy_query_rate": round(s["greedy_query_rate"], 6),
                "parse_fails": s["parse_fails"],
                "model_errors": s["model_errors"],
                "loop_exhaustions": s["loop_exhaustions"],
                "no_path_reported": s["no_path_reported"],
                "wrong_path": s["wrong_path"],
                "incomplete_answers": s["incomplete_answers"],
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
            row["run_status"], row["run_status_detail"] = classify_summary_health(row)
            writer.writerow(row)


def print_comparison(all_results: dict[str, list[EvalResult]]) -> None:
    """Print a multi-model comparison table."""
    col_w = 36
    print("\n" + "=" * 138)
    print("BASELINE COMPARISON")
    print("=" * 138)
    header = (
        f"{'Model':<{col_w}} {'Overall':>8} {'Tier1':>7} {'Tier2':>7} "
        f"{'Tier3':>7} {'Tier4':>7} {'Tier5':>7} {'Tier6':>7} "
        f"{'Hallucs':>8} {'CyErr':>7} {'QExp':>6} {'Fails':>6} "
        f"{'ModelErr':>9} {'LoopExh':>8} {'InfraErr':>9}"
    )
    print(header)
    print("-" * 138)

    for model, results in all_results.items():
        s = _stats(results)
        f"{100 * s['correct'] // s['total']}%" if s["total"] else "0%"

        def tp(tier):
            c, t = s["tiers"][tier]
            return f"{100 * c // t}%" if t else "n/a"

        short_model = model.split("/", 1)[-1][:col_w]
        print(
            f"{short_model:<{col_w}} {s['correct']:>4}/{s['total']:<3} "
            f"{tp(1):>7} {tp(2):>7} {tp(3):>7} {tp(4):>7} {tp(5):>7} {tp(6):>7} "
            f"{s['hallucs']:>8} {s['cypher_errors']:>7} {s['query_too_expensive']:>6} "
            f"{s['parse_fails']:>6} "
            f"{s['model_errors']:>9} {s['loop_exhaustions']:>8} {s['infra_errors']:>9}"
        )
        if s["mcp_samples"]:
            avg_tools = s["tool_calls_total"] / s["mcp_samples"]
            print(
                f"{'':<{col_w}} {'tools(avg)':>8} {avg_tools:>7.2f} "
                f"{'cypher':>7} {s['cypher_query_calls']:>7} {'noncy':>7} {s['non_cypher_tool_calls']:>7} "  # noqa: E501
                f"{'toolfail':>9} {s['failed_tool_calls']:>9} {'policy':>9} {s['policy_violations']:>9}"  # noqa: E501
            )
        status, detail = classify_summary_health(
            {
                "completed_tasks": s["completed_tasks"],
                "expected_tasks": s["expected_tasks"],
                "run_complete": s["run_complete"],
                "partial_result": s["partial_results"] > 0,
                "infra_errors": s["infra_errors"],
            }
        )
        if status != "complete":
            print(f"{'':<{col_w}} {'status':>8} {status}: {detail}")

    print("=" * 138)


def print_summary(results: list[EvalResult], model: str) -> None:
    """Print a summary table to stdout."""
    total = len(results)
    correct = sum(1 for r in results if r.grade.score == 1.0)
    hallucinations = sum(1 for r in results if r.grade.hallucination)
    parse_fails = sum(1 for r in results if r.grade.outcome == "PARSE_FAIL")
    cypher_errors = sum(1 for r in results if r.grade.outcome == "CYPHER_ERROR")
    query_too_expensive = sum(1 for r in results if r.grade.outcome == "QUERY_TOO_EXPENSIVE")
    model_errors = sum(1 for r in results if r.grade.outcome == "MODEL_ERROR")
    loop_exhaustions = sum(1 for r in results if r.grade.outcome == "LOOP_EXHAUSTED")
    no_path_reported = sum(
        1 for r in results if r.mcp and r.mcp.failure_subtype == "NO_PATH_REPORTED"
    )
    wrong_path = sum(1 for r in results if r.mcp and r.mcp.failure_subtype == "WRONG_PATH")
    incomplete_answers = sum(
        1 for r in results if r.mcp and r.mcp.failure_subtype == "INCOMPLETE_ANSWER"
    )
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
    print(
        f"Tier 1: {tier_score(1)}  |  Tier 2: {tier_score(2)}  |  "
        f"Tier 3: {tier_score(3)}  |  Tier 4: {tier_score(4)}  |  Tier 5: {tier_score(5)}"
    )
    print(
        f"Hallucinations: {hallucinations}  |  Parse failures: {parse_fails}  |  "
        f"Cypher errors: {cypher_errors}  |  Query too expensive: {query_too_expensive}  |  "
        f"Model errors: {model_errors}  |  Loop exhausted: {loop_exhaustions}  |  "
        f"Infra errors: {infra_errors}"
    )
    direct_stats = _stats(results)
    if not any(r.mcp for r in results):
        print(
            f"Direct safety: policy rejections={direct_stats['policy_rejections']}  |  "
            f"server timeouts={direct_stats['server_query_timeouts']}  |  "
            f"circuit skips={direct_stats['circuit_open_skips']}  |  "
            f"queries executed={direct_stats['executed_direct_queries']}"
        )
    if no_path_reported or wrong_path or incomplete_answers:
        print(
            f"MCP incorrect subtypes: no_path={no_path_reported}  |  "
            f"wrong_path={wrong_path}  |  incomplete={incomplete_answers}"
        )
    status, detail = classify_summary_health(
        {
            "completed_tasks": sum(1 for r in results if not getattr(r, "partial_result", False)),
            "expected_tasks": total,
            "run_complete": not any(getattr(r, "partial_result", False) for r in results),
            "partial_result": any(getattr(r, "partial_result", False) for r in results),
            "infra_errors": infra_errors,
        }
    )
    if status != "complete":
        print(f"Run status: {status} ({detail})")
    if any(r.mcp for r in results):
        mcp = direct_stats
        avg_tools = mcp["tool_calls_total"] / mcp["mcp_samples"] if mcp["mcp_samples"] else 0.0
        print(
            f"Reasoning accuracy: {mcp['correct_completed']}/{mcp['completed_samples']} "
            f"({100 * mcp['reasoning_accuracy']:.1f}%)  |  "
            f"Effective accuracy: {100 * mcp['effective_accuracy']:.1f}%  |  "
            f"Infra/tool/timeout rates: {100 * mcp['infra_failure_rate']:.1f}%/"
            f"{100 * mcp['tool_error_rate']:.1f}%/{100 * mcp['timeout_rate']:.1f}%"
        )
        print(
            f"Avg tool calls: {avg_tools:.2f}  |  Cypher tool calls: {mcp['cypher_query_calls']}  |  "  # noqa: E501
            f"Non-cypher tool calls: {mcp['non_cypher_tool_calls']}  |  "
            f"Failed tool calls: {mcp['failed_tool_calls']}  |  "
            f"Policy violations: {mcp['policy_violations']}"
        )
    print("=" * 60)
