"""Diagnostic-only attribution helpers for ORI grading.

These helpers deliberately do not change strict scores. They derive fields that
explain where a failure appears to have occurred so reports can separate model
reasoning, evidence search, final-answer contract, and scorer friction.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .bhce import CypherResult
from .contracts import AnswerContract
from .tasks import Task


@dataclass
class FinalAnswerDiagnostics:
    failure_stage: str = ""
    evidence_found: bool = False
    evidence_depth_score: int = 0
    reference_entities_seen_count: int = 0
    reference_path_nodes_seen_count: int = 0
    final_answer_contract_valid: bool = False
    invalid_entities: list[str] = field(default_factory=list)
    missing_required_entities: list[str] = field(default_factory=list)
    finalization_guard_used: bool = False
    repair_turn_used: bool = False
    minimum_evidence_satisfied: bool = False
    successful_tool_results: int = 0
    reasoning_capture_mode: str = "none"
    reasoning_token_count: int = 0

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "failure_stage": self.failure_stage,
            "evidence_found": self.evidence_found,
            "evidence_depth_score": self.evidence_depth_score,
            "reference_entities_seen_count": self.reference_entities_seen_count,
            "reference_path_nodes_seen_count": self.reference_path_nodes_seen_count,
            "final_answer_contract_valid": self.final_answer_contract_valid,
            "invalid_entities": self.invalid_entities,
            "missing_required_entities": self.missing_required_entities,
            "finalization_guard_used": self.finalization_guard_used,
            "repair_turn_used": self.repair_turn_used,
            "minimum_evidence_satisfied": self.minimum_evidence_satisfied,
            "successful_tool_results": self.successful_tool_results,
            "reasoning_capture_mode": self.reasoning_capture_mode,
            "reasoning_token_count": self.reasoning_token_count,
        }


def answer_node_names(final_answer: dict[str, Any] | None) -> set[str]:
    if not isinstance(final_answer, dict):
        return set()
    names: set[str] = set()
    for key in ("node_names", "nodes"):
        value = final_answer.get(key)
        if isinstance(value, list):
            names.update(str(v).strip() for v in value if str(v).strip())
    path = final_answer.get("path") or final_answer.get("paths")
    if isinstance(path, list):
        for item in path:
            if isinstance(item, str) and item.strip():
                names.add(item.strip())
            elif isinstance(item, dict):
                for key in ("name", "source", "target", "node", "node_name"):
                    value = item.get(key)
                    if isinstance(value, str) and value.strip():
                        names.add(value.strip())
    return names


def validate_final_answer_contract(task: Task, final_answer: dict[str, Any] | None) -> bool:
    if not isinstance(final_answer, dict):
        return False
    answer_type = final_answer.get("answer_type")
    if task.grade_mode == "path_exists":
        if answer_type not in {"path_exists", "path", None}:
            return False
        if "path_found" not in final_answer:
            return False
        if final_answer.get("path_found") is True:
            return bool(answer_node_names(final_answer))
        return final_answer.get("path_found") is False
    if task.grade_mode == "node_set":
        return answer_type in {"node_set", None} and isinstance(
            final_answer.get("node_names"), list
        )
    if task.grade_mode == "row_count":
        return answer_type in {"row_count", "count", None} and isinstance(
            final_answer.get("count"), int
        )
    return bool(final_answer)


def compute_evidence_depth(
    *, successful_tool_results: int, cypher_query_calls: int, resource_reads: int
) -> int:
    score = 0
    if successful_tool_results > 0:
        score += 1
    if successful_tool_results >= 3:
        score += 1
    if cypher_query_calls > 0:
        score += 2
    if resource_reads > 0:
        score += 1
    return score


def _node_alias_groups(ref_result: CypherResult) -> dict[str, set[str]]:
    groups: dict[str, set[str]] = {}
    for node in ref_result.nodes:
        if not isinstance(node, dict):
            continue
        raw_props = node.get("properties")
        props = raw_props if isinstance(raw_props, dict) else {}
        aliases = {
            str(value).strip()
            for key in (
                "label",
                "name",
                "Name",
                "objectId",
                "objectid",
                "ObjectIdentifier",
                "objectidentifier",
            )
            for value in (node.get(key), props.get(key))
            if value and str(value).strip()
        }
        if aliases:
            canonical = (
                node.get("label") or props.get("name") or props.get("Name") or sorted(aliases)[0]
            )
            groups[str(canonical)] = aliases
    return groups


def _covered_reference_nodes(
    reference_nodes: set[str], answer_nodes: set[str], ref_result: CypherResult
) -> tuple[set[str], set[str]]:
    groups = _node_alias_groups(ref_result)
    answer_upper = {node.upper() for node in answer_nodes}
    covered: set[str] = set()
    for ref in reference_nodes:
        aliases = set(groups.get(ref, set())) | {ref}
        if {alias.upper() for alias in aliases} & answer_upper:
            covered.add(ref)
    return covered, reference_nodes - covered


def build_final_answer_diagnostics(
    *,
    task: Task,
    final_answer: dict[str, Any] | None,
    ref_result: CypherResult,
    valid_node_names: set[str],
    contract: AnswerContract | None,
    successful_tool_results: int = 0,
    cypher_query_calls: int = 0,
    resource_reads_total: int = 0,
    finalization_guard_used: bool = False,
    repair_turn_used: bool = False,
    model_thinking: str = "",
    grade_outcome: str = "",
    failure_subtype: str = "",
) -> FinalAnswerDiagnostics:
    reference_nodes = (
        set(contract.required_nodes)
        if contract and contract.required_nodes
        else set(ref_result.node_names)
    )
    answer_nodes = answer_node_names(final_answer)
    covered_nodes, missing_nodes = _covered_reference_nodes(
        reference_nodes, answer_nodes, ref_result
    )
    valid_upper = {v.upper() for v in valid_node_names}
    invalid = sorted(n for n in answer_nodes if n.upper() not in valid_upper)
    missing = sorted(missing_nodes)
    contract_valid = validate_final_answer_contract(task, final_answer)
    evidence_depth = compute_evidence_depth(
        successful_tool_results=successful_tool_results,
        cypher_query_calls=cypher_query_calls,
        resource_reads=resource_reads_total,
    )
    ref_seen = len(covered_nodes)
    evidence_found = bool(successful_tool_results or ref_seen or evidence_depth)
    if grade_outcome == "CORRECT":
        stage = "strict_correct"
    elif grade_outcome in {"CYPHER_ERROR", "QUERY_TOO_EXPENSIVE"}:
        stage = "direct_cypher_execution"
    elif invalid:
        stage = "final_answer_invalid_entity"
    elif not contract_valid:
        stage = "final_answer_contract"
    elif failure_subtype == "NO_PATH_REPORTED" and evidence_found:
        stage = "evidence_found_no_synthesis"
    elif failure_subtype == "INCOMPLETE_ANSWER":
        stage = "final_answer_incomplete"
    elif successful_tool_results == 0 and grade_outcome != "CORRECT":
        stage = "search_failure"
    else:
        stage = "strict_wrong_answer"
    return FinalAnswerDiagnostics(
        failure_stage=stage,
        evidence_found=evidence_found,
        evidence_depth_score=evidence_depth,
        reference_entities_seen_count=ref_seen,
        reference_path_nodes_seen_count=ref_seen if task.grade_mode == "path_exists" else 0,
        final_answer_contract_valid=contract_valid,
        invalid_entities=invalid,
        missing_required_entities=missing,
        finalization_guard_used=finalization_guard_used,
        repair_turn_used=repair_turn_used,
        minimum_evidence_satisfied=bool(ref_seen or evidence_depth >= 2),
        successful_tool_results=successful_tool_results,
        reasoning_capture_mode="model_thinking" if model_thinking else "none",
        reasoning_token_count=len(model_thinking.split()) if model_thinking else 0,
    )


def diagnostics_json(diag: FinalAnswerDiagnostics | None) -> str:
    return json.dumps(diag.to_jsonable(), sort_keys=True) if diag else ""
