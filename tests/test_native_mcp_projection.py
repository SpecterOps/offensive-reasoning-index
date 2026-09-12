"""Source-shaped fixtures; observed evidence is not a certified proof."""

import json
from copy import deepcopy

import pytest

from ori.eval.v2.fingerprint import canonical_sha256
from ori.eval.v2.native_mcp_projection import project_native_result
from ori.eval.v2.schema import EdgeDirection, PathStatus
from tests.support.v2_compiler import simple_compiled as _simple_compiled

simple_compiled = _simple_compiled


@pytest.fixture(scope="module")
def task(simple_compiled):
    return simple_compiled[3].tasks[0].public


def _call(payload):
    return {"content": [{"type": "text", "text": json.dumps(payload)}], "isError": False}


def _path():
    return {
        "success": True,
        "path_found": True,
        "source": "ALICE",
        "target": "ADMINS",
        "path_length": 1,
        "nodes": [
            {"name": "alice", "type": "User", "objectid": "user-1", "enabled": True},
            {"name": "admins", "type": "Group", "objectid": "group-1", "enabled": None},
        ],
        "edges": [{"type": "MemberOf", "isacl": False}],
        "analysis": {},
        "risk_level": "CRITICAL",
    }


@pytest.mark.parametrize("surface", ("text", "structured", "both", "string-wrapper"))
def test_armadin_directed_path_preserves_native_identity_and_raw_digest(task, surface):
    payload = _path()
    result = _call(payload)
    if surface in {"structured", "both"}:
        result["structuredContent"] = deepcopy(payload)
    if surface == "structured":
        result["content"] = []
    if surface == "string-wrapper":
        result["structuredContent"] = {"result": json.dumps(payload)}
    original = deepcopy(result)
    arguments = {"source": "ALICE", "target": "ADMINS"}
    projection = project_native_result("armadin", "find_shortest_path", arguments, result, task)
    assert projection.status == "observed"
    assert projection.reason == "mechanical_observation_only"
    assert projection.raw_result_fingerprint == canonical_sha256(original)
    evidence = projection.evidence
    assert evidence.raw_digest == projection.raw_result_fingerprint
    assert evidence.task_id == task.task_id
    assert [(e.object_id, e.object_type) for e in evidence.entities] == [
        ("user-1", "User"),
        ("group-1", "Group"),
    ]
    assert len(evidence.edges) == 1
    edge = evidence.edges[0]
    assert (edge.source_id, edge.relationship, edge.target_id, edge.direction) == (
        "user-1",
        "MemberOf",
        "group-1",
        EdgeDirection.OUTBOUND,
    )
    assert edge.properties == ()
    assert evidence.path_status is PathStatus.FOUND
    assert evidence.graph_fact_attestation is None
    assert evidence.negative_reason_codes == ()
    assert result == original
    assert arguments == {"source": "ALICE", "target": "ADMINS"}


@pytest.mark.parametrize("empty", (False, True))
def test_armadin_domain_rows_are_observations_not_absence_proofs(task, empty):
    domains = (
        []
        if empty
        else [
            {
                "objectid": "domain-1",
                "name": "EXAMPLE.TEST",
                "domain": "EXAMPLE.TEST",
                "sid": "domain-1",
                "functional_level": 7,
            }
        ]
    )
    result = _call({"success": True, "domains": domains, "count": len(domains)})
    original = deepcopy(result)
    projection = project_native_result("armadin", "find_domains", {}, result, task)
    assert projection.status == "observed"
    assert projection.evidence.count == len(domains)
    assert [e.object_type for e in projection.evidence.entities] == ([] if empty else ["Domain"])
    assert projection.evidence.path_status is PathStatus.UNKNOWN
    assert projection.evidence.negative_reason_codes == ()
    assert projection.evidence.graph_fact_attestation is None
    assert result == original


@pytest.mark.parametrize(
    "change",
    (
        "negative-path",
        "truncated",
        "missing-id",
        "missing-type",
        "wrong-length",
        "boolean-length",
        "missing-edge-type",
        "wrong-endpoint",
        "duplicate-node",
        "conflicting-surfaces",
        "multiple-text",
        "malformed-json",
        "duplicate-json-key",
        "nonfinite-json",
        "non-text",
        "false-success-shape",
    ),
)
def test_native_path_malformed_or_lossy_data_is_inconclusive(task, change):
    payload = _path()
    result = _call(payload)
    if change == "negative-path":
        payload = {"success": True, "path_found": False, "message": "No path found"}
    elif change == "truncated":
        payload["truncated"] = True
    elif change == "missing-id":
        del payload["nodes"][0]["objectid"]
    elif change == "missing-type":
        del payload["nodes"][0]["type"]
    elif change == "wrong-length":
        payload["path_length"] = 2
    elif change == "boolean-length":
        payload["path_length"] = True
    elif change == "missing-edge-type":
        del payload["edges"][0]["type"]
    elif change == "wrong-endpoint":
        payload["nodes"][0]["name"] = "other-user"
    elif change == "duplicate-node":
        payload["nodes"][1]["objectid"] = "user-1"
    elif change == "false-success-shape":
        payload["success"] = 1
    result = _call(payload)
    if change == "conflicting-surfaces":
        result["structuredContent"] = {"success": True, "count": 3}
    elif change == "multiple-text":
        result["content"] *= 2
    elif change == "malformed-json":
        result["content"][0]["text"] = "not json"
    elif change == "duplicate-json-key":
        result["content"][0]["text"] = '{"success":true,"success":false}'
    elif change == "nonfinite-json":
        result["content"][0]["text"] = '{"success":true,"count":NaN}'
    elif change == "non-text":
        result["content"] = [{"type": "image", "data": "ignored", "mimeType": "image/png"}]
    original = deepcopy(result)
    projection = project_native_result(
        "armadin", "find_shortest_path", {"source": "ALICE", "target": "ADMINS"}, result, task
    )
    assert projection.status == "inconclusive"
    assert projection.evidence is None
    assert projection.raw_result_fingerprint == canonical_sha256(original)
    assert result == original


@pytest.mark.parametrize(
    "data,expected",
    (
        ([{"count": 3}], "observed"),
        ([{"count": 0}], "observed"),
        ([{"count": True}], "inconclusive"),
        ([{"count": -1}], "inconclusive"),
        ([{"count": 2.0}], "inconclusive"),
        ([], "inconclusive"),
        ([{"count": 1}, {"count": 2}], "inconclusive"),
        ([{"count": 1, "other": 1}], "inconclusive"),
        ([{"n": {"objectid": "user-1", "name": "ALICE"}}], "inconclusive"),
        ([{"p": [{"objectid": "user-1"}, "MemberOf", {"objectid": "group-1"}]}], "inconclusive"),
    ),
)
def test_mordavid_scalar_only_and_lossy_paths(task, data, expected):
    result = _call({"success": True, "data": data})
    original = deepcopy(result)
    projection = project_native_result(
        "mordavid",
        "query_bloodhound",
        {"query": "MATCH (n) RETURN count(n) AS count"},
        result,
        task,
    )
    assert projection.status == expected
    if expected == "observed":
        assert projection.evidence.count == data[0]["count"]
        assert projection.evidence.entities == ()
        assert projection.evidence.edges == ()
        assert projection.evidence.path_status is PathStatus.UNKNOWN
        assert projection.evidence.negative_reason_codes == ()
    else:
        assert projection.evidence is None
    assert result == original
    assert projection.raw_result_fingerprint == canonical_sha256(original)


@pytest.mark.parametrize("native", (False, True))
def test_native_and_mcp_errors_are_not_graph_evidence(task, native):
    result = _call({"success": False, "error": "private backend detail"})
    if not native:
        result["isError"] = True
    projection = project_native_result("armadin", "find_domains", {}, result, task)
    assert projection.status == "tool_error"
    assert projection.evidence is None
    assert "private backend detail" not in projection.reason


def test_mordavid_explicit_node_labels_are_required(simple_compiled, subtests):
    from neo4j import Record
    from neo4j.graph import Graph, Node

    task = next(t.public for t in simple_compiled[3].tasks if t.public.claim_kind == "set")
    base = ("MATCH (u:User) RETURN DISTINCT u AS entity, labels(u) AS labels "
            "ORDER BY u.objectid SKIP 0 LIMIT 500")
    for case in ("valid", "empty", "literal-labels", "other-variable", "case-variable",
                 "constructed-map", "unknown-label", "two-types", "missing-id", "duplicate",
                 "unknown-column", "empty-invalid-query"):
        with subtests.test(case=case):
            query = base
            node = Node(Graph(), "1", 1, ["Base", "User"],
                        {"objectid": "user-1", "name": "ALICE", "enabled": True})
            # The pinned server uses Record.data(); preserve its real lossy
            # node serialization plus the explicit labels column.
            rows = [Record([("entity", node), ("labels", ["Base", "User"])]).data()]
            if case in {"empty", "empty-invalid-query"}:
                rows = []
            if case in {"literal-labels", "empty-invalid-query"}:
                query = query.replace("labels(u)", "['User']")
            elif case == "other-variable":
                query = query.replace("labels(u)", "labels(v)")
            elif case == "case-variable":
                query = query.replace("labels(u)", "labels(U)")
            elif case == "constructed-map":
                query = query.replace("u AS entity", "{objectid:u.objectid} AS entity")
            elif case == "unknown-label":
                rows[0]["labels"].append("UnqualifiedLabel")
            elif case == "two-types":
                rows[0]["labels"].append("Group")
            elif case == "missing-id":
                del rows[0]["entity"]["objectid"]
            elif case == "duplicate":
                rows.append(deepcopy(rows[0]))
            elif case == "unknown-column":
                rows[0]["extra"] = 1
            result = _call({"success": True, "data": rows})
            projection = project_native_result(
                "mordavid", "query_bloodhound", {"query": query}, result, task,
            )
            assert (projection.status == "observed") is (case in {"valid", "empty"})
            if case == "valid":
                assert projection.evidence.entities[0].object_id == "user-1"
                assert projection.evidence.entities[0].object_type == "User"
                assert any(fact.key == "enabled" and fact.value is True
                           for fact in projection.evidence.observed_properties)


@pytest.mark.parametrize(
    "implementation,tool",
    (
        ("unknown", "find_domains"),
        ("armadin", "unknown"),
        ("mordavid", "find_all_domain_admins"),
        ("mwnickerson", "unknown"),
    ),
)
def test_unknown_native_shapes_are_explicitly_unsupported(task, implementation, tool):
    result = _call({"success": True, "data": []})
    projection = project_native_result(implementation, tool, {}, result, task)
    assert projection.status == "unsupported"
    assert projection.evidence is None
    assert projection.raw_result_fingerprint == canonical_sha256(result)


@pytest.mark.parametrize(
    "implementation,tool",
    (
        ("armadin", "find_domain_admins"),
        ("mordavid", "find_all_domain_admins"),
        ("mwnickerson", "domain_info"),
    ),
)
@pytest.mark.parametrize("mcp_error", (False, True))
def test_registered_tools_without_projectors_still_report_errors(
    task, implementation, tool, mcp_error
):
    result = _call({"success": False, "error": "private backend failure"})
    if mcp_error:
        result["isError"] = True
    projection = project_native_result(implementation, tool, {}, result, task)
    assert projection.status == "tool_error"
    assert projection.evidence is None
    assert "private" not in projection.reason


@pytest.mark.parametrize(
    "case",
    (
        "positive",
        "zero",
        "boolean",
        "float",
        "multiple",
        "empty",
        "string",
        "counter-not-answer",
        "wrong-operation",
        "wrong-response-operation",
        "has-no-results",
    ),
)
def test_main_native_run_projects_only_explicit_scalar_literals(task, case):
    # Source envelope: pinned main.py:_cypher_run, NOT ORI's execution wrapper.
    payload = {
        "info_type": "run",
        "success": True,
        "has_results": True,
        "query_compatibility": {},
        "node_count": 0,
        "edge_count": 0,
        "data": {"nodes": {}, "edges": [], "literals": [{"key": "total", "value": 7}]},
    }
    arguments = {"info_type": "run", "query": "MATCH (u:User) RETURN count(u) AS total"}
    if case in {"zero", "boolean", "float", "string"}:
        payload["data"]["literals"][0]["value"] = {
            "zero": 0,
            "boolean": True,
            "float": 7.0,
            "string": "7",
        }[case]
    elif case == "multiple":
        payload["data"]["literals"] *= 2
    elif case == "empty":
        payload["data"]["literals"] = []
    elif case == "counter-not-answer":
        del payload["data"]["literals"]
        payload["node_count"] = 7
    elif case == "wrong-operation":
        arguments["info_type"] = "list_saved"
    elif case == "wrong-response-operation":
        payload["info_type"] = "list_saved"
    elif case == "has-no-results":
        payload["has_results"] = False
    result = _call(payload)
    original = deepcopy(result)
    projection = project_native_result("mwnickerson", "cypher_query", arguments, result, task)
    if case in {"positive", "zero"}:
        assert projection.status == "observed"
        assert projection.evidence.count == (7 if case == "positive" else 0)
        assert projection.evidence.path_status is PathStatus.UNKNOWN
        assert projection.evidence.negative_reason_codes == ()
        assert projection.evidence.graph_fact_attestation is None
    else:
        assert projection.status == ("unsupported" if case == "wrong-operation" else "inconclusive")
        assert projection.evidence is None
    assert result == original
    assert projection.raw_result_fingerprint == canonical_sha256(original)


def test_main_graph_uses_observed_stable_ids_and_explicit_edge_endpoints(task, subtests):
    for case in ("positive", "missing-id", "duplicate-id", "contradictory-id",
                 "unknown-type", "wrong-count", "boolean-count", "missing-endpoint",
                 "wrong-direction", "duplicate-edge", "unknown-edge", "bad-properties"):
        with subtests.test(case=case):
            # Deliberately reversed map order and nonidentity CE map keys.
            nodes = {
                "9": {"objectId": "group-1", "kind": "Group", "label": "ADMINS"},
                "2": {"objectId": "user-1", "kind": "User", "label": "ALICE",
                      "properties": {"name": "ALICE", "enabled": True}},
            }
            edges = [{"source": "2", "target": "9", "kind": "MemberOf"}]
            payload = {"success": True, "info_type": "run", "has_results": True,
                       "node_count": 2, "edge_count": 1,
                       "data": {"nodes": nodes, "edges": edges,
                                "literals": [{"key": "endpoint", "value": "ALICE"}]}}
            if case == "missing-id":
                del nodes["2"]["objectId"]
            elif case == "duplicate-id":
                nodes["2"]["objectId"] = "group-1"
            elif case == "contradictory-id":
                nodes["2"]["properties"]["objectid"] = "other-user"
            elif case == "unknown-type":
                nodes["2"]["kind"] = "Invented"
            elif case == "wrong-count":
                payload["node_count"] = 3
            elif case == "boolean-count":
                payload["edge_count"] = True
            elif case == "missing-endpoint":
                edges[0]["target"] = "group-1"
            elif case == "wrong-direction":
                edges[0]["direction"] = "inbound"
            elif case == "duplicate-edge":
                edges.append(deepcopy(edges[0]))
                payload["edge_count"] = 2
            elif case == "unknown-edge":
                edges[0]["kind"] = "Invented"
            elif case == "bad-properties":
                nodes["2"]["properties"] = []
            result = _call(payload)
            original = deepcopy(result)
            projection = project_native_result(
                "mwnickerson", "cypher_query",
                {"info_type": "run", "query": "MATCH p=(s)-[:MemberOf]->(t) RETURN p"},
                result, task,
            )
            assert result == original
            assert projection.raw_result_fingerprint == canonical_sha256(original)
            if case != "positive":
                assert projection.status == "inconclusive"
                assert projection.evidence is None
                continue
            evidence = projection.evidence
            assert projection.status == "observed"
            assert [entity.object_id for entity in evidence.entities] == ["group-1", "user-1"]
            assert [(e.source_id, e.target_id, e.direction) for e in evidence.edges] == [
                ("user-1", "group-1", EdgeDirection.OUTBOUND),
            ]
            assert any(fact.entity_id == "user-1" and fact.key == "enabled" and fact.value is True
                       for fact in evidence.observed_properties)
            assert evidence.count is None
            assert evidence.path_status is PathStatus.UNKNOWN
            assert evidence.graph_fact_attestation is None
