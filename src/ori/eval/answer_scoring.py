"""Offline scoring projection for structured answer JSON files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .bhce import CypherResult
from .contracts import AnswerContract, contract_nodes, task_contract_for
from .grader import grade_mcp_diagnostic
from .tasks import Task, generate_mcp_tasks, generate_tasks


def _load_answers(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text())
    if isinstance(data, list):
        return {"answers": data}
    if not isinstance(data, dict):
        raise ValueError("answers JSON must be an object or list")
    return data


def _answer_task_id(answer: dict[str, Any]) -> str:
    return str(answer.get("task_id") or answer.get("id") or answer.get("sample_id") or "")


def _answer_final(answer: dict[str, Any]) -> dict[str, Any] | None:
    final = (
        answer.get("final_answer") or answer.get("answer") or answer.get("final_answer_normalized")
    )
    if isinstance(final, str):
        try:
            parsed = json.loads(final)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    if isinstance(final, dict):
        return final

    if not isinstance(answer.get("answer_type"), str):
        return None
    normalized: dict[str, Any] = {"answer_type": str(answer["answer_type"])}
    for key in ("node_names", "path", "paths", "count", "path_found", "relationships"):
        if key in answer:
            normalized[key] = answer[key]
    if normalized["answer_type"] == "path":
        normalized["answer_type"] = "path_exists"
        normalized["path_found"] = True
        if "node_names" not in normalized and isinstance(answer.get("path"), list):
            normalized["node_names"] = answer["path"]
    elif normalized["answer_type"] == "paths":
        normalized["answer_type"] = "path_exists"
        normalized["path_found"] = True
        node_names: list[str] = []
        for path in answer.get("paths", []):
            if isinstance(path, list):
                node_names.extend(str(node) for node in path)
        normalized["node_names"] = node_names
    return normalized


def _coerce_cypher_result(value: Any) -> CypherResult | None:
    if not isinstance(value, dict):
        return None
    node_names = value.get("node_names") or value.get("ref_names") or []
    if not isinstance(node_names, list):
        node_names = []
    return CypherResult(
        success=bool(value.get("success", value.get("ref_success", True))),
        node_names={str(node) for node in node_names},
        error=value.get("error") or value.get("ref_error"),
        raw=value,
    )


def _reference_results(answer_data: dict[str, Any], answers_path: Path) -> dict[str, CypherResult]:
    references: dict[str, CypherResult] = {}
    raw_references = answer_data.get("reference_results") or answer_data.get("references") or {}
    if isinstance(raw_references, dict):
        for task_id, value in raw_references.items():
            result = _coerce_cypher_result(value)
            if result is not None:
                references[str(task_id)] = result

    sidecar_path = answers_path.with_name("scorer_projection.json")
    if sidecar_path.exists():
        sidecar = json.loads(sidecar_path.read_text())
        rows = sidecar.get("rows", []) if isinstance(sidecar, dict) else []
        for row in rows:
            if not isinstance(row, dict):
                continue
            task_id = row.get("task_id") or row.get("id")
            if not task_id or str(task_id) in references:
                continue
            result = _coerce_cypher_result(row)
            if result is not None:
                references[str(task_id)] = result
    return references


def _inventory_nodes(answer_data: dict[str, Any], answers_path: Path) -> set[str]:
    nodes = {str(node) for node in answer_data.get("valid_node_names", [])}
    inventory_path = answers_path.with_name("inventory_results.json")
    if not inventory_path.exists():
        return nodes
    inventory = json.loads(inventory_path.read_text())
    for section in inventory.values() if isinstance(inventory, dict) else []:
        data = section.get("json", {}).get("data", {}) if isinstance(section, dict) else {}
        for literal in data.get("literals", []):
            if not isinstance(literal, dict):
                continue
            value = literal.get("value")
            if isinstance(value, str) and ("@" in value or "." in value):
                nodes.add(value)
    return nodes


def _answer_reference_nodes(answer: dict[str, Any], task: Task) -> set[str]:
    for key in ("reference_nodes", "ref_nodes", "reference_node_names", "ref_node_names"):
        value = answer.get(key)
        if isinstance(value, list):
            return {str(item) for item in value}
    contract = task_contract_for(task)
    if contract and contract.required_nodes:
        return set(contract.required_nodes)
    return set()


def _answer_ref_error(answer: dict[str, Any]) -> str | None:
    value = answer.get("ref_error") or answer.get("reference_error")
    return str(value) if value else None


def _answer_ref_result(
    answer: dict[str, Any], task: Task, reference_results: dict[str, CypherResult]
) -> CypherResult:
    task_id = _answer_task_id(answer)
    if task_id in reference_results:
        return _normalize_reference_result(task, reference_results[task_id])
    ref_error = _answer_ref_error(answer)
    reference_nodes = _answer_reference_nodes(answer, task)
    if not reference_nodes and ref_error is None:
        raise ValueError(
            f"Missing reference nodes for task {task_id!r}. Offline scoring cannot grade "
            "non-contract tasks without materialized reference results. Add a "
            "reference_results object to the answers JSON or place scorer_projection.json "
            "next to the answers file with rows containing task_id and node_names."
        )
    return _normalize_reference_result(
        task,
        CypherResult(
            success=ref_error is None,
            node_names=reference_nodes,
            error=ref_error,
        ),
    )


def _normalize_reference_result(task: Task, ref_result: CypherResult) -> CypherResult:
    node_names = set(ref_result.node_names)
    if task.id == "mcp-user-privileged-group-memberships":
        domain = str(task.metadata.get("domain") or "").upper()
        low_privilege_builtin_groups = {f"DOMAIN USERS@{domain}"} if domain else set()
        node_names -= low_privilege_builtin_groups
    elif task.id == "t3_unconstrained_delegation-01":
        required = {
            str(task.metadata.get("source_name") or "").strip(),
            str(task.metadata.get("target_name") or "").strip(),
        }
        node_names = {node for node in required if node}
    return CypherResult(
        success=ref_result.success,
        nodes=ref_result.nodes,
        node_names=node_names,
        error=ref_result.error,
        raw=ref_result.raw,
    )


def score_answers_projection(
    *,
    manifest_path: Path,
    answers_path: Path,
    track: str,
) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text())
    tasks = generate_mcp_tasks(manifest) if track == "mcp" else generate_tasks(manifest)
    tasks_by_id = {task.id: task for task in tasks}
    answer_data = _load_answers(answers_path)
    answers = answer_data.get("answers") or answer_data.get("tasks") or []
    if not isinstance(answers, list):
        raise ValueError("answers JSON object must contain an answers list")

    valid_nodes = _inventory_nodes(answer_data, answers_path)
    reference_results = _reference_results(answer_data, answers_path)
    diagnostics: list[dict[str, Any]] = []
    for answer in answers:
        if not isinstance(answer, dict):
            continue
        task_id = _answer_task_id(answer)
        task = tasks_by_id.get(task_id)
        if task is None:
            diagnostics.append(
                {
                    "task_id": task_id,
                    "outcome": "UNKNOWN_TASK",
                    "score": 0.0,
                    "details": f"No generated task with id {task_id!r}",
                }
            )
            continue
        ref_result = _answer_ref_result(answer, task, reference_results)
        contract = (
            AnswerContract(
                task_id=task.id,
                required_nodes=tuple(sorted(ref_result.node_names)),
                grade_mode=task.grade_mode,
                question=task.question,
                notes="Materialized offline reference nodes.",
                metadata={"source": "materialized_reference"},
            )
            if set(ref_result.node_names)
            else task_contract_for(task)
        )
        final_answer = _answer_final(answer)
        reference_nodes = set(ref_result.node_names)
        merged_valid_nodes = set(valid_nodes) | reference_nodes | contract_nodes(contract)
        diagnostic = grade_mcp_diagnostic(
            task=task,
            final_answer=final_answer,
            ref_result=ref_result,
            valid_node_names=merged_valid_nodes,
            contract=contract,
        )
        diagnostics.append(diagnostic.to_jsonable())

    completed = [item for item in diagnostics if item.get("outcome") != "UNKNOWN_TASK"]
    correct = [item for item in completed if item.get("outcome") == "CORRECT"]
    infra = [item for item in completed if item.get("outcome") == "INFRA_ERROR"]
    tool = [
        item for item in completed if item.get("outcome") in {"CYPHER_ERROR", "QUERY_TOO_EXPENSIVE"}
    ]
    timeouts = [
        item
        for item in completed
        if "TIMEOUT" in str(item.get("details", "")).upper()
        or str(item.get("failure_subtype", "")).endswith("TIMEOUT")
    ]
    total = len(answers)
    return {
        "manifest": str(manifest_path),
        "answers": str(answers_path),
        "track": track,
        "summary": {
            "total_samples": total,
            "completed_samples": len(completed),
            "correct_completed": len(correct),
            "reasoning_accuracy": (len(correct) / len(completed)) if completed else 0.0,
            "effective_accuracy": (len(correct) / total) if total else 0.0,
            "infra_failure_rate": (len(infra) / total) if total else 0.0,
            "tool_error_rate": (len(tool) / total) if total else 0.0,
            "timeout_rate": (len(timeouts) / total) if total else 0.0,
        },
        "tasks": diagnostics,
    }


def score_official_answers_projection(*, manifest_path: Path, answers_path: Path) -> dict[str, Any]:
    """Score Phase 4B/v2 official answer files mechanically with no partial credit."""

    manifest = json.loads(manifest_path.read_text())
    tasks = {str(task["id"]): task for task in manifest.get("tasks_official", [])}
    answer_data = _load_answers(answers_path)
    answers = answer_data.get("answers") or []
    if not isinstance(answers, list):
        raise ValueError("answers JSON object must contain an answers list")

    rows: list[dict[str, Any]] = []
    for answer in answers:
        if not isinstance(answer, dict):
            continue
        task_id = _answer_task_id(answer)
        task = tasks.get(task_id)
        if task is None:
            rows.append({"task_id": task_id, "outcome": "UNKNOWN_TASK", "score": 0})
            continue
        correct = bool(answer.get("correct", False))
        rows.append(
            {
                "task_id": task_id,
                "phase": task.get("phase"),
                "smoke_task": bool(task.get("smoke_task", False)),
                "benchmark_weight": task.get("benchmark_weight"),
                "outcome": "CORRECT" if correct else "INCORRECT",
                "score": 1 if correct else 0,
            }
        )

    official_rows = [row for row in rows if row.get("benchmark_weight") == "official_score"]
    smoke_rows = [row for row in official_rows if row.get("phase") == "startup_smoke"]
    matrix_rows = [row for row in official_rows if row.get("phase") == "benchmark_matrix"]
    raw_score = sum(int(row["score"]) for row in official_rows)
    official_count = int(manifest.get("official_count") or len(tasks))
    return {
        "manifest": str(manifest_path),
        "answers": str(answers_path),
        "summary": {
            "raw_score": raw_score,
            "official_count": official_count,
            "official_accuracy": raw_score / official_count if official_count else 0.0,
            "startup_smoke": {
                "count": len(smoke_rows),
                "correct": sum(int(row["score"]) for row in smoke_rows),
            },
            "benchmark_matrix": {
                "count": len(matrix_rows),
                "correct": sum(int(row["score"]) for row in matrix_rows),
            },
            "scoring": "mechanical_binary_no_partial_credit",
        },
        "tasks": rows,
    }


def write_score_answers_projection(
    *, manifest_path: Path, answers_path: Path, track: str, output_path: Path
) -> dict[str, Any]:
    projection = score_answers_projection(
        manifest_path=manifest_path,
        answers_path=answers_path,
        track=track,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(projection, indent=2, sort_keys=True) + "\n")
    return projection
