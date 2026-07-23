"""Task/scorer consistency preflight checks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .bhce import CypherResult
from .contracts import contract_nodes, task_contract_for
from .tasks import Task, generate_mcp_tasks, generate_tasks

_ROW_COUNT_HINTS = ("count", "how many", "number of")
_NODE_WORDS = ("root ca", "ntauth", "certificate template", "domain admins", "privileged")


def check_reference_result(task: Task, result: CypherResult) -> dict[str, Any]:
    """Validate live positive-reference evidence before a model answer is graded."""
    if not result.success:
        return {"ok": False, "detail": f"reference query failed: {result.error}"}
    evidence = task.metadata.get("reference_evidence")
    if task.grade_mode == "no_path" or not isinstance(evidence, dict):
        return {"ok": True, "detail": "no positive edge-evidence contract"}
    expected = int(evidence.get("expected_cardinality", 1))
    if not result.nodes and not result.node_names:
        return {
            "ok": False,
            "detail": (
                "reference query returned no exact anchored relationship evidence "
                f"(expected cardinality {expected})"
            ),
        }
    actual = (result.raw or {}).get("reference_evidence_count")
    if actual is not None and actual != expected:
        return {
            "ok": False,
            "detail": f"reference evidence cardinality mismatch: expected {expected}, got {actual}",
        }
    return {"ok": True, "detail": f"exact anchored relationship evidence present ({expected})"}


def preflight_tasks(
    tasks: list[Task], *, valid_node_names: set[str] | None = None
) -> dict[str, Any]:
    valid_upper = {node.upper() for node in (valid_node_names or set())}
    findings: list[dict[str, Any]] = []
    for task in tasks:
        contract = task_contract_for(task)
        ref_empty = not task.reference_cypher.strip()
        if ref_empty:
            findings.append(
                {
                    "task_id": task.id,
                    "severity": "error",
                    "code": "EMPTY_REFERENCE",
                    "detail": "reference_cypher is empty",
                }
            )
        if task.grade_mode == "row_count" and not any(
            hint in task.question.lower() for hint in _ROW_COUNT_HINTS
        ):
            findings.append(
                {
                    "task_id": task.id,
                    "severity": "warn",
                    "code": "ROW_COUNT_WORDING",
                    "detail": "row_count task wording lacks an explicit count cue",
                }
            )
        if contract:
            nodes = contract_nodes(contract)
            missing_from_valid = sorted(
                node for node in nodes if valid_upper and node.upper() not in valid_upper
            )
            if missing_from_valid:
                findings.append(
                    {
                        "task_id": task.id,
                        "severity": "error",
                        "code": "CONTRACT_NODE_NOT_IN_VALID_INVENTORY",
                        "detail": ", ".join(missing_from_valid),
                    }
                )
        evidence = task.metadata.get("reference_evidence")
        if isinstance(evidence, dict):
            required_edges = evidence.get("required_edges", [])
            if not evidence.get("endpoint_anchored") or not required_edges:
                findings.append(
                    {
                        "task_id": task.id,
                        "severity": "error",
                        "code": "INVALID_REFERENCE_EVIDENCE_CONTRACT",
                        "detail": (
                            "positive reference evidence must declare anchored required edges"
                        ),
                    }
                )
            elif "ori_evidence_count = 1" not in task.reference_cypher:
                findings.append(
                    {
                        "task_id": task.id,
                        "severity": "error",
                        "code": "REFERENCE_CARDINALITY_NOT_ENFORCED",
                        "detail": (
                            "reference Cypher does not enforce exact evidence cardinality; "
                            "offline preflight does not execute the live query"
                        ),
                    }
                )
        q = task.question.lower()
        if (
            any(word in q for word in _NODE_WORDS)
            and contract is None
            and task.grade_mode in {"node_set", "path_exists"}
        ):
            findings.append(
                {
                    "task_id": task.id,
                    "severity": "warn",
                    "code": "PROMPT_ENTITY_WITHOUT_CONTRACT",
                    "detail": (
                        "task mentions important entities but has no explicit answer contract"
                    ),
                }
            )
    return {
        "ok": not any(item["severity"] == "error" for item in findings),
        "findings": findings,
        "summary": {
            "tasks_checked": len(tasks),
            "errors": sum(1 for item in findings if item["severity"] == "error"),
            "warnings": sum(1 for item in findings if item["severity"] == "warn"),
        },
    }


def preflight_manifest(
    manifest_path: Path, *, track: str, valid_nodes_path: Path | None = None
) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text())
    tasks = generate_mcp_tasks(manifest) if track == "mcp" else generate_tasks(manifest)
    valid_nodes: set[str] = set()
    if valid_nodes_path:
        raw = json.loads(valid_nodes_path.read_text())
        valid_nodes = {
            str(item)
            for item in (raw.get("valid_node_names", raw) if isinstance(raw, dict) else raw)
        }
    result = preflight_tasks(tasks, valid_node_names=valid_nodes)
    result.update({"manifest": str(manifest_path), "track": track})
    return result
