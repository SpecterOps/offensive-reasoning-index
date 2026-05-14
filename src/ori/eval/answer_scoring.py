"""Offline scoring projection for structured answer JSON files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .bhce import CypherResult
from .contracts import contract_nodes, task_contract_for
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
    return final if isinstance(final, dict) else None


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

    valid_nodes = {str(node) for node in answer_data.get("valid_node_names", [])}
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
        contract = task_contract_for(task)
        answer_nodes = {
            str(node)
            for node in (_answer_final(answer) or {}).get("node_names", [])
            if str(node).strip()
        }
        reference_nodes = _answer_reference_nodes(answer, task)
        merged_valid_nodes = (
            set(valid_nodes) | reference_nodes | contract_nodes(contract) | answer_nodes
        )
        ref_error = _answer_ref_error(answer)
        ref_result = CypherResult(
            success=ref_error is None,
            node_names=reference_nodes,
            error=ref_error,
        )
        diagnostic = grade_mcp_diagnostic(
            task=task,
            final_answer=_answer_final(answer),
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
