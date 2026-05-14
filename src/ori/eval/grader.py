"""Pure grading functions — no I/O, no side effects."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .adapter import ModelResponse
from .bhce import BHCEClient, CypherResult
from .contracts import AnswerContract, task_contract_for
from .tasks import Task


@dataclass
class GradeResult:
    score: float  # 0.0 or 1.0
    outcome: str  # CORRECT | INCORRECT | PARSE_FAIL | CYPHER_ERROR | QUERY_TOO_EXPENSIVE | HALLUCINATION | MODEL_ERROR | INFRA_ERROR  # noqa: E501
    hallucination: bool
    details: str


@dataclass
class GradeDiagnostic:
    task_id: str
    grade_mode: str
    grade: GradeResult
    reference_nodes: list[str] = field(default_factory=list)
    answer_nodes: list[str] = field(default_factory=list)
    missing_reference_nodes: list[str] = field(default_factory=list)
    missing_required_nodes: list[str] = field(default_factory=list)
    extra_valid_nodes: list[str] = field(default_factory=list)
    hallucinated_nodes: list[str] = field(default_factory=list)
    optional_nodes_present: list[str] = field(default_factory=list)
    forbidden_nodes_present: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    ref_error: str | None = None
    contract: dict[str, Any] | None = None

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "grade_mode": self.grade_mode,
            "score": self.grade.score,
            "outcome": self.grade.outcome,
            "hallucination": self.grade.hallucination,
            "details": self.grade.details,
            "reference_nodes": self.reference_nodes,
            "answer_nodes": self.answer_nodes,
            "missing_reference_nodes": self.missing_reference_nodes,
            "missing_required_nodes": self.missing_required_nodes,
            "extra_valid_nodes": self.extra_valid_nodes,
            "hallucinated_nodes": self.hallucinated_nodes,
            "optional_nodes_present": self.optional_nodes_present,
            "forbidden_nodes_present": self.forbidden_nodes_present,
            "metrics": self.metrics,
            "ref_error": self.ref_error,
            "contract": self.contract,
        }


def grade(
    task: Task,
    model_response: ModelResponse,
    model_result: CypherResult,
    ref_result: CypherResult,
    valid_node_names: set[str],
) -> GradeResult:
    """Grade a model response against the reference result."""

    # Model-level error (API call failed)
    if model_response.error:
        return GradeResult(
            score=0.0,
            outcome="MODEL_ERROR",
            hallucination=False,
            details=f"Model call failed: {model_response.error}",
        )

    # Cypher parse failure
    if model_response.cypher is None:
        return GradeResult(
            score=0.0,
            outcome="PARSE_FAIL",
            hallucination=False,
            details="Could not extract Cypher query from model response",
        )

    # Infrastructure/runtime failure reaching BHCE
    if not ref_result.success and BHCEClient.classify_error(ref_result.error) == "infra":
        return GradeResult(
            score=0.0,
            outcome="INFRA_ERROR",
            hallucination=False,
            details=f"Reference Cypher could not be graded due to BHCE availability: {ref_result.error}",  # noqa: E501
        )

    # Cypher execution error
    if not model_result.success:
        if BHCEClient.classify_error(model_result.error) == "infra":
            return GradeResult(
                score=0.0,
                outcome="QUERY_TOO_EXPENSIVE",
                hallucination=False,
                details=(
                    "Model-generated Cypher could not be executed by BloodHound CE "
                    f"(likely overly expensive or otherwise non-viable): {model_result.error}"
                ),
            )
        return GradeResult(
            score=0.0,
            outcome="CYPHER_ERROR",
            hallucination=False,
            details=f"Cypher execution failed: {model_result.error}",
        )

    # Hallucination check — must run before scoring
    hallucination = _check_hallucination(model_response.raw_text, valid_node_names, task)
    if hallucination:
        return GradeResult(
            score=0.0,
            outcome="HALLUCINATION",
            hallucination=True,
            details="Response references node names not present in the graph",
        )

    # Grade by mode
    mode = task.grade_mode
    if mode == "path_exists":
        # Model must return at least one result AND include all reference nodes.
        # Checking only len > 0 would accept any non-empty query (e.g. MATCH (n) RETURN n LIMIT 1).
        model_nonempty = len(model_result.nodes) > 0
        if not ref_result.node_names:
            # Reference returned nothing — planted path not found in BH CE, can't grade
            correct = False
            details = "path_exists: reference result is empty — verify BH CE ingest"
        elif not model_nonempty:
            correct = False
            details = f"path_exists: model returned 0 nodes (ref: {len(ref_result.nodes)})"
        else:
            # Model must contain all nodes that appear in the reference path
            correct = ref_result.node_names.issubset(model_result.node_names)
            details = (
                f"path_exists: model={len(model_result.node_names)} nodes, "
                f"ref={len(ref_result.node_names)} nodes, "
                f"overlap={len(ref_result.node_names & model_result.node_names)}"
            )
        return GradeResult(
            score=1.0 if correct else 0.0,
            outcome="CORRECT" if correct else "INCORRECT",
            hallucination=False,
            details=details,
        )

    elif mode == "node_set":
        # Reference nodes must be a subset of model nodes (superset_ok)
        if not ref_result.node_names:
            # Reference returned nothing — any non-empty result is wrong
            correct = len(model_result.node_names) == 0
        else:
            correct = ref_result.node_names.issubset(model_result.node_names)
        return GradeResult(
            score=1.0 if correct else 0.0,
            outcome="CORRECT" if correct else "INCORRECT",
            hallucination=False,
            details=(
                f"node_set: ref has {len(ref_result.node_names)} names, "
                f"model has {len(model_result.node_names)} names, "
                f"overlap: {len(ref_result.node_names & model_result.node_names)}"
            ),
        )

    elif mode == "row_count":
        ref_count = len(ref_result.nodes)
        model_count = len(model_result.nodes)
        # Within ±20% of reference count
        if ref_count == 0:
            correct = model_count == 0
        else:
            correct = abs(model_count - ref_count) / ref_count <= 0.20
        return GradeResult(
            score=1.0 if correct else 0.0,
            outcome="CORRECT" if correct else "INCORRECT",
            hallucination=False,
            details=f"row_count: ref={ref_count}, model={model_count}",
        )

    else:
        return GradeResult(
            score=0.0,
            outcome="INCORRECT",
            hallucination=False,
            details=f"Unknown grade_mode: {mode!r}",
        )


def _answer_node_names(final_answer: dict | None) -> set[str]:
    if not isinstance(final_answer, dict):
        return set()
    return {str(name).strip() for name in final_answer.get("node_names", []) if str(name).strip()}


def _row_count_from_ref(ref_result: CypherResult) -> int:
    raw = ref_result.raw or {}
    for key in ("expected_count", "count"):
        value = raw.get(key)
        if isinstance(value, int):
            return value
    if len(ref_result.nodes) == 1:
        row = ref_result.nodes[0]
        if isinstance(row, dict):
            for key in ("count", "expected_count", "COUNT(*)"):
                value = row.get(key)
                if isinstance(value, int):
                    return value
    return len(ref_result.nodes) if ref_result.nodes else len(ref_result.node_names)


def _metrics(reference_nodes: set[str], answer_nodes: set[str]) -> dict[str, Any]:
    overlap = reference_nodes & answer_nodes
    missing = reference_nodes - answer_nodes
    extra = answer_nodes - reference_nodes
    precision = (
        len(overlap) / len(answer_nodes) if answer_nodes else (1.0 if not reference_nodes else 0.0)
    )
    recall = (
        len(overlap) / len(reference_nodes)
        if reference_nodes
        else (1.0 if not answer_nodes else 0.0)
    )
    return {
        "reference_count": len(reference_nodes),
        "answer_count": len(answer_nodes),
        "overlap_count": len(overlap),
        "missing_count": len(missing),
        "extra_count": len(extra),
        "precision": precision,
        "recall": recall,
    }


def grade_mcp_diagnostic(
    task: Task,
    final_answer: dict | None,
    ref_result: CypherResult,
    valid_node_names: set[str],
    *,
    contract: AnswerContract | None = None,
    model_error: str | None = None,
    infra_tool_errors: int = 0,
) -> GradeDiagnostic:
    """Grade a structured MCP final answer and expose node-level diagnostics."""
    contract = contract if contract is not None else task_contract_for(task)
    reference_nodes = (
        set(contract.required_nodes)
        if contract and contract.required_nodes
        else set(ref_result.node_names)
    )
    answer_nodes = _answer_node_names(final_answer)
    valid_upper = {node.upper(): node for node in valid_node_names}
    hallucinated = sorted(node for node in answer_nodes if node.upper() not in valid_upper)
    optional = set(contract.optional_nodes) if contract else set()
    forbidden = set(contract.forbidden_nodes) if contract else set()
    extra_valid = sorted((answer_nodes - reference_nodes - optional) - set(hallucinated))
    metrics = _metrics(reference_nodes, answer_nodes)

    if model_error:
        grade_result = GradeResult(0.0, "MODEL_ERROR", False, f"Model call failed: {model_error}")
    elif not ref_result.success and BHCEClient.classify_error(ref_result.error) == "infra":
        grade_result = GradeResult(
            0.0,
            "INFRA_ERROR",
            False,
            f"Reference Cypher could not be graded due to BHCE availability: {ref_result.error}",
        )
    elif final_answer is None:
        if infra_tool_errors > 0:
            grade_result = GradeResult(
                0.0,
                "INFRA_ERROR",
                False,
                "Structured final answer missing after MCP/BloodHound infrastructure errors",
            )
        else:
            grade_result = GradeResult(
                0.0,
                "PARSE_FAIL",
                False,
                "Could not extract structured final answer from model response",
            )
    elif hallucinated:
        grade_result = GradeResult(
            0.0,
            "HALLUCINATION",
            True,
            "Response references node names not present in the graph: " + ", ".join(hallucinated),
        )
    elif task.grade_mode == "path_exists":
        found = bool(final_answer.get("path_found"))
        correct = bool(reference_nodes) and found and reference_nodes.issubset(answer_nodes)
        detail_suffix = f"precision={metrics['precision']:.3f}, recall={metrics['recall']:.3f}"
        if not reference_nodes:
            details = "path_exists: reference/contract node set is empty — verify BH CE ingest"
        elif not found:
            details = "path_exists: model reported no path found"
        else:
            details = (
                f"path_exists (mcp): answer={len(answer_nodes)} nodes, "
                f"ref={len(reference_nodes)} nodes, overlap={len(reference_nodes & answer_nodes)}, "
                f"{detail_suffix}"
            )
        grade_result = GradeResult(
            1.0 if correct else 0.0, "CORRECT" if correct else "INCORRECT", False, details
        )
    elif task.grade_mode == "node_set":
        correct = (
            answer_nodes == set() if not reference_nodes else reference_nodes.issubset(answer_nodes)
        )
        details = (
            f"node_set (mcp): ref has {len(reference_nodes)} names, "
            f"answer has {len(answer_nodes)} names, "
            f"overlap: {len(reference_nodes & answer_nodes)}, "
            f"precision={metrics['precision']:.3f}, recall={metrics['recall']:.3f}"
        )
        grade_result = GradeResult(
            1.0 if correct else 0.0, "CORRECT" if correct else "INCORRECT", False, details
        )
    elif task.grade_mode == "row_count":
        ref_count = _row_count_from_ref(ref_result)
        answer_count = int(final_answer.get("count", 0))
        correct = (
            answer_count == 0
            if ref_count == 0
            else abs(answer_count - ref_count) / ref_count <= 0.20
        )
        metrics.update({"reference_count_literal": ref_count, "answer_count_literal": answer_count})
        grade_result = GradeResult(
            1.0 if correct else 0.0,
            "CORRECT" if correct else "INCORRECT",
            False,
            f"row_count (mcp): ref={ref_count}, answer={answer_count}",
        )
    else:
        grade_result = GradeResult(
            0.0, "INCORRECT", False, f"Unknown grade_mode: {task.grade_mode!r}"
        )

    missing_reference = sorted(reference_nodes - answer_nodes)
    return GradeDiagnostic(
        task_id=task.id,
        grade_mode=task.grade_mode,
        grade=grade_result,
        reference_nodes=sorted(reference_nodes),
        answer_nodes=sorted(answer_nodes),
        missing_reference_nodes=missing_reference,
        missing_required_nodes=missing_reference,
        extra_valid_nodes=extra_valid,
        hallucinated_nodes=hallucinated,
        optional_nodes_present=sorted(answer_nodes & optional),
        forbidden_nodes_present=sorted(answer_nodes & forbidden),
        metrics=metrics,
        ref_error=ref_result.error,
        contract=contract.to_jsonable() if contract else None,
    )


def grade_mcp(
    task: Task,
    model_response: ModelResponse,
    final_answer: dict | None,
    ref_result: CypherResult,
    valid_node_names: set[str],
    infra_tool_errors: int = 0,
) -> GradeResult:
    """Grade a structured MCP final answer against the reference result."""
    return grade_mcp_diagnostic(
        task=task,
        final_answer=final_answer,
        ref_result=ref_result,
        valid_node_names=valid_node_names,
        model_error=model_response.error,
        infra_tool_errors=infra_tool_errors,
    ).grade


def _check_hallucination(
    text: str,
    valid_node_names: set[str],
    task: Task,
) -> bool:
    """
    Check if the response references node names not in the graph.
    Looks for NAME@DOMAIN patterns in the model's raw text.
    """
    if not valid_node_names:
        return False  # can't check without allowlist

    domain = task.metadata.get("domain", "")
    if not domain:
        return False

    # Find all NAME@DOMAIN patterns in the text, including BloodHound group
    # names that commonly contain spaces (e.g. "DOMAIN ADMINS@CORP.LOCAL").
    pattern = re.compile(
        r"\b([A-Z0-9_\-\. ]+@" + re.escape(domain.upper()) + r")\b",
        re.IGNORECASE,
    )
    mentioned = {m.group(1).strip().upper() for m in pattern.finditer(text)}

    # Check if any mentioned name is not in the valid set
    valid_upper = {n.upper() for n in valid_node_names}
    unknown = mentioned - valid_upper
    return len(unknown) > 0
