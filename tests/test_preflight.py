from __future__ import annotations

from ori.eval.bhce import CypherResult
from ori.eval.preflight import check_reference_result, preflight_tasks
from ori.eval.tasks import Task


def _task(reference_cypher: str) -> Task:
    return Task(
        id="edge-evidence-01",
        template_id="edge-evidence",
        tier=6,
        category="path_finding",
        question="Find the exact path.",
        reference_cypher=reference_cypher,
        grade_mode="path_exists",
        metadata={
            "reference_evidence": {
                "required_edges": [
                    {"source": "SOURCE-ID", "edge": "AdminTo", "target": "TARGET-ID"}
                ],
                "expected_cardinality": 1,
                "endpoint_anchored": True,
            }
        },
    )


def test_reference_result_check_rejects_missing_expected_edge() -> None:
    result = CypherResult(success=True, nodes=[], node_names=set(), raw={})

    check = check_reference_result(_task("MATCH (a)-[:AdminTo]->(b) RETURN a, b"), result)

    assert check["ok"] is False
    assert "no exact anchored relationship evidence" in check["detail"]


def test_reference_result_check_accepts_anchored_edge_evidence() -> None:
    result = CypherResult(success=True, nodes=[{"label": "SOURCE"}], raw={})

    check = check_reference_result(_task("MATCH (a)-[:AdminTo]->(b) RETURN a, b"), result)

    assert check["ok"] is True


def test_offline_preflight_requires_cardinality_guard_without_claiming_execution() -> None:
    report = preflight_tasks([_task("MATCH (a)-[:AdminTo]->(b) RETURN a, b")])

    assert report["ok"] is False
    finding = next(item for item in report["findings"] if item["severity"] == "error")
    assert finding["code"] == "REFERENCE_CARDINALITY_NOT_ENFORCED"
    assert "offline preflight does not execute" in finding["detail"]


def test_preflight_rejects_anchored_prompt_that_omits_endpoint() -> None:
    task = _task("MATCH (a)-[:AdminTo]->(b) RETURN a, b")
    task.metadata = {
        "source_name": "ALICE@CORP.LOCAL",
        "target_name": "DC01.CORP.LOCAL",
        "reference_scope": "anchored",
    }
    task.question = "Find the path from ALICE@CORP.LOCAL."

    report = preflight_tasks([task])

    finding = next(
        item
        for item in report["findings"]
        if item["code"] == "ANCHORED_PROMPT_SCOPE_MISMATCH"
    )
    assert finding["severity"] == "error"
    assert "DC01.CORP.LOCAL" in finding["detail"]


def test_preflight_accepts_exact_materialized_node_set_contract() -> None:
    task = _task("MATCH (u:User {hasspn: true}) RETURN u")
    task.grade_mode = "node_set"
    task.question = "List every Kerberoastable account."
    task.metadata = {
        "reference_scope": "reference_defined",
        "answer_contract": {
            "grade_mode": "node_set",
            "oracle": "materialized_reference",
            "set_semantics": "exact",
        },
    }

    report = preflight_tasks([task])

    assert report["summary"] == {"tasks_checked": 1, "errors": 0, "warnings": 0}
