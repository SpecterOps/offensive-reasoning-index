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
