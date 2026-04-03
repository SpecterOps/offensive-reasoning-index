"""Pure grading functions — no I/O, no side effects."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .adapter import ModelResponse
from .bhce import BHCEClient
from .bhce import CypherResult
from .tasks import Task


@dataclass
class GradeResult:
    score: float        # 0.0 or 1.0
    outcome: str        # CORRECT | INCORRECT | PARSE_FAIL | CYPHER_ERROR | HALLUCINATION | MODEL_ERROR | INFRA_ERROR
    hallucination: bool
    details: str


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
            score=0.0, outcome="MODEL_ERROR",
            hallucination=False,
            details=f"Model call failed: {model_response.error}",
        )

    # Cypher parse failure
    if model_response.cypher is None:
        return GradeResult(
            score=0.0, outcome="PARSE_FAIL",
            hallucination=False,
            details="Could not extract Cypher query from model response",
        )

    # Infrastructure/runtime failure reaching BHCE
    if not ref_result.success and BHCEClient.classify_error(ref_result.error) == "infra":
        return GradeResult(
            score=0.0,
            outcome="INFRA_ERROR",
            hallucination=False,
            details=f"Reference Cypher could not be graded due to BHCE availability: {ref_result.error}",
        )

    # Cypher execution error
    if not model_result.success:
        if BHCEClient.classify_error(model_result.error) == "infra":
            return GradeResult(
                score=0.0,
                outcome="INFRA_ERROR",
                hallucination=False,
                details=f"BloodHound CE unavailable during model query execution: {model_result.error}",
            )
        return GradeResult(
            score=0.0, outcome="CYPHER_ERROR",
            hallucination=False,
            details=f"Cypher execution failed: {model_result.error}",
        )

    # Hallucination check — must run before scoring
    hallucination = _check_hallucination(model_response.raw_text, valid_node_names, task)
    if hallucination:
        return GradeResult(
            score=0.0, outcome="HALLUCINATION",
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
            score=0.0, outcome="INCORRECT",
            hallucination=False,
            details=f"Unknown grade_mode: {mode!r}",
        )


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

    # Find all NAME@DOMAIN patterns in the text
    pattern = re.compile(
        r"\b([A-Z0-9_\-\.]+@" + re.escape(domain.upper()) + r")\b",
        re.IGNORECASE,
    )
    mentioned = {m.group(1).upper() for m in pattern.finditer(text)}

    # Check if any mentioned name is not in the valid set
    valid_upper = {n.upper() for n in valid_node_names}
    unknown = mentioned - valid_upper
    return len(unknown) > 0
