"""Tests for the Phase 2 eval harness — adapter, grader, tasks, bhce helpers."""

from __future__ import annotations

import asyncio
import json
import os
import time
from types import SimpleNamespace

from ori.eval.adapter import call_model, extract_cypher
from ori.eval.bhce import _extract_node_names, _extract_nodes
from ori.eval.grader import _check_hallucination, grade, grade_mcp_diagnostic
from ori.eval.mcp_runtime import (
    _inspect_eval_async_safe_print,
    _inspect_eval_async_with_artifact_recovery,
    _load_last_eval_log_from_runtime_dir,
    _safe_print,
)
from ori.eval.tasks import Task, generate_mcp_tasks, generate_tasks

# ---------------------------------------------------------------------------
# extract_cypher
# ---------------------------------------------------------------------------


def test_extract_cypher_fenced_with_language():
    text = "Here is the query:\n```cypher\nMATCH (u:User) RETURN u\n```"
    assert extract_cypher(text) == "MATCH (u:User) RETURN u"


def test_extract_cypher_fenced_no_language():
    text = "```\nMATCH (u:User {hasspn: true}) RETURN u\n```"
    assert extract_cypher(text) == "MATCH (u:User {hasspn: true}) RETURN u"


def test_extract_cypher_bare_match():
    text = "The answer is:\nMATCH (u:User)-[:MemberOf]->(g:Group) RETURN u, g"
    result = extract_cypher(text)
    assert result is not None
    assert result.startswith("MATCH")


def test_extract_cypher_optional_match():
    text = "OPTIONAL MATCH (u:User)-[:HasSession]->(c:Computer) RETURN u, c"
    result = extract_cypher(text)
    assert result is not None
    assert "OPTIONAL MATCH" in result


def test_extract_cypher_returns_none_when_no_cypher():
    assert extract_cypher("I cannot answer this question.") is None
    assert extract_cypher("") is None


def test_extract_cypher_strips_think_block():
    text = "<think>I should reason first</think>\nMATCH (u:User {hasspn: true}) RETURN u"
    assert extract_cypher(text) == "MATCH (u:User {hasspn: true}) RETURN u"


def test_extract_cypher_multiline_fenced():
    text = (
        "```cypher\n"
        "MATCH p=shortestPath(\n"
        "  (u:User {name: 'DJOHNSON@CORP.LOCAL'})\n"
        "  -[*1..]->(c:Computer {name: 'DC01.CORP.LOCAL'})\n"
        ")\n"
        "RETURN p\n"
        "```"
    )
    result = extract_cypher(text)
    assert result is not None
    assert "shortestPath" in result
    assert "RETURN p" in result


# ---------------------------------------------------------------------------
# BH CE response normalization
# ---------------------------------------------------------------------------


def test_extract_nodes_dict_format():
    data = {
        "data": {
            "nodes": {
                "1": {
                    "label": "DJOHNSON@CORP.LOCAL",
                    "kind": "User",
                    "properties": {"name": "DJOHNSON@CORP.LOCAL"},
                },
                "2": {
                    "label": "DC01.CORP.LOCAL",
                    "kind": "Computer",
                    "properties": {"name": "DC01.CORP.LOCAL"},
                },
            },
            "edges": [],
        }
    }
    nodes = _extract_nodes(data)
    assert len(nodes) == 2


def test_extract_nodes_empty():
    assert _extract_nodes({}) == []
    assert _extract_nodes({"data": {}}) == []


def test_extract_node_names_old_format():
    """Old BH CE format: name in properties.name"""
    nodes = [
        {"properties": {"name": "DJOHNSON@CORP.LOCAL"}},
        {"properties": {"name": "IT-ADMINS@CORP.LOCAL"}},
        {"properties": {}},  # no name — should be skipped
    ]
    names = _extract_node_names(nodes)
    assert names == {"DJOHNSON@CORP.LOCAL", "IT-ADMINS@CORP.LOCAL"}


def test_extract_node_names_new_format():
    """New BH CE v1.9+ format: name in label field, no properties object"""
    nodes = [
        {"label": "DJOHNSON@CORP.LOCAL", "kind": "User", "objectId": "S-1-5-21-1-2-3-100"},
        {"label": "DC01.CORP.LOCAL", "kind": "Computer", "objectId": "S-1-5-21-1-2-3-101"},
        {"kind": "Group"},  # no label, no name — should be skipped
    ]
    names = _extract_node_names(nodes)
    assert names == {"DJOHNSON@CORP.LOCAL", "DC01.CORP.LOCAL"}


def test_extract_node_names_mixed_format():
    """Handle mix of old and new formats in same response"""
    nodes = [
        {"label": "DJOHNSON@CORP.LOCAL", "kind": "User"},  # new format
        {"properties": {"name": "IT-ADMINS@CORP.LOCAL"}},  # old format
    ]
    names = _extract_node_names(nodes)
    assert names == {"DJOHNSON@CORP.LOCAL", "IT-ADMINS@CORP.LOCAL"}


# ---------------------------------------------------------------------------
# Grader
# ---------------------------------------------------------------------------


def _make_task(grade_mode: str = "path_exists") -> Task:
    return Task(
        id="test-01",
        template_id="t1_admin_to",
        tier=1,
        category="path_finding",
        question="Find the path",
        reference_cypher="MATCH p=shortestPath((u)-[*1..]->(c)) RETURN p",
        grade_mode=grade_mode,
        metadata={"domain": "CORP.LOCAL"},
    )


def _make_cypher_result(names: list[str], success: bool = True):
    from ori.eval.bhce import CypherResult

    nodes = [{"properties": {"name": n}} for n in names]
    return CypherResult(success=success, nodes=nodes, node_names=set(names))


def _make_model_response(cypher: str | None = "MATCH (u:User) RETURN u", error: str | None = None):
    from ori.eval.adapter import ModelResponse

    return ModelResponse(
        raw_text=cypher or "",
        cypher=cypher,
        parse_stage="bare_match" if cypher else "none",
        tokens_input=10,
        tokens_output=5,
        elapsed_seconds=1.0,
        model="test/model",
        error=error,
    )


def test_grade_parse_fail():
    task = _make_task()
    resp = _make_model_response(cypher=None)
    result = grade(
        task, resp, _make_cypher_result([]), _make_cypher_result(["A@CORP.LOCAL"]), set()
    )
    assert result.score == 0.0
    assert result.outcome == "PARSE_FAIL"


def test_grade_model_error():
    task = _make_task()
    resp = _make_model_response(cypher=None, error="API timeout")
    result = grade(task, resp, _make_cypher_result([]), _make_cypher_result([]), set())
    assert result.score == 0.0
    assert result.outcome == "MODEL_ERROR"


def test_safe_print_ignores_broken_pipe(monkeypatch):
    def broken_print(*args, **kwargs):
        raise BrokenPipeError(32, "Broken pipe")

    monkeypatch.setattr("builtins.print", broken_print)
    _safe_print("progress that should not fail scoring")


def test_inspect_eval_safe_print_restores_print(monkeypatch):
    import builtins

    original_print = builtins.print

    async def fake_eval(*args, **kwargs):
        builtins.print("inspect progress")
        return []

    async def run_case():
        monkeypatch.setattr("ori.eval.mcp_runtime.inspect_eval_async", fake_eval)
        return await _inspect_eval_async_safe_print("task")

    result = asyncio.run(run_case())

    assert result == []
    assert builtins.print is original_print


def test_inspect_eval_artifact_recovery_handles_shutdown_system_exit(monkeypatch, tmp_path):
    marker_log = object()

    async def fake_eval(*args, **kwargs):
        raise BaseExceptionGroup("unhandled errors in a TaskGroup", [SystemExit(1)])

    monkeypatch.setattr("ori.eval.mcp_runtime._inspect_eval_async_safe_print", fake_eval)
    monkeypatch.setattr(
        "ori.eval.mcp_runtime._load_last_eval_log_from_runtime_dir",
        lambda log_dir, **kwargs: marker_log,
    )

    result = asyncio.run(
        _inspect_eval_async_with_artifact_recovery(
            "task", recovery_log_dir=tmp_path, expected_sample_count=1
        )
    )

    assert result == [marker_log]


def test_inspect_eval_artifact_recovery_reraises_non_shutdown_groups(monkeypatch, tmp_path):
    async def fake_eval(*args, **kwargs):
        raise BaseExceptionGroup("real failure", [RuntimeError("boom")])

    monkeypatch.setattr("ori.eval.mcp_runtime._inspect_eval_async_safe_print", fake_eval)

    try:
        asyncio.run(
            _inspect_eval_async_with_artifact_recovery(
                "task", recovery_log_dir=tmp_path, expected_sample_count=1
            )
        )
    except BaseExceptionGroup as exc:
        assert "real failure" in str(exc)
    else:
        raise AssertionError("Expected non-SystemExit BaseExceptionGroup to be re-raised")


def test_inspect_eval_artifact_recovery_reraises_without_complete_artifact(monkeypatch, tmp_path):
    async def fake_eval(*args, **kwargs):
        raise SystemExit(1)

    monkeypatch.setattr("ori.eval.mcp_runtime._inspect_eval_async_safe_print", fake_eval)
    monkeypatch.setattr(
        "ori.eval.mcp_runtime._load_last_eval_log_from_runtime_dir", lambda log_dir, **kwargs: None
    )

    try:
        asyncio.run(
            _inspect_eval_async_with_artifact_recovery(
                "task", recovery_log_dir=tmp_path, expected_sample_count=1
            )
        )
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("Expected SystemExit to be re-raised without a recoverable artifact")


def test_inspect_eval_artifact_loader_rejects_stale_pointer(monkeypatch, tmp_path):
    log_path = tmp_path / "old.eval"
    log_path.write_text("placeholder")
    pointer = tmp_path / "_inspect_runtime" / "view" / "last-eval-result"
    pointer.parent.mkdir(parents=True)
    pointer.write_text(json.dumps({"location": str(log_path)}))
    old = time.time() - 120
    os.utime(log_path, (old, old))
    os.utime(pointer, (old, old))
    monkeypatch.setattr(
        "ori.eval.mcp_runtime.read_eval_log",
        lambda path: SimpleNamespace(status="success", error=None, samples=[object()], results=None),
    )

    result = _load_last_eval_log_from_runtime_dir(
        tmp_path,
        started_at_monotonic=time.monotonic(),
        expected_sample_count=1,
    )

    assert result is None


def test_inspect_eval_artifact_loader_rejects_wrong_sample_count(monkeypatch, tmp_path):
    log_path = tmp_path / "current.eval"
    log_path.write_text("placeholder")
    pointer = tmp_path / "_inspect_runtime" / "view" / "last-eval-result"
    pointer.parent.mkdir(parents=True)
    pointer.write_text(json.dumps({"location": str(log_path)}))
    now = time.time()
    os.utime(log_path, (now, now))
    os.utime(pointer, (now, now))
    monkeypatch.setattr(
        "ori.eval.mcp_runtime.read_eval_log",
        lambda path: SimpleNamespace(status="success", error=None, samples=[object()], results=None),
    )

    result = _load_last_eval_log_from_runtime_dir(
        tmp_path,
        started_at_monotonic=time.monotonic() - 10,
        expected_sample_count=2,
    )

    assert result is None


def test_grade_mcp_loop_exhausted_is_not_model_error():
    task = _make_task()
    diagnostic = grade_mcp_diagnostic(
        task,
        None,
        _make_cypher_result(["A@CORP.LOCAL"]),
        {"A@CORP.LOCAL"},
        model_error="MCP loop exhausted without final answer",
    )
    assert diagnostic.grade.score == 0.0
    assert diagnostic.grade.outcome == "LOOP_EXHAUSTED"
    assert "MCP loop exhausted" in diagnostic.grade.details


def test_mcp_no_path_reported_subtype():
    task = _make_task("path_exists")
    diagnostic = grade_mcp_diagnostic(
        task,
        {"answer_type": "path_exists", "path_found": False, "node_names": []},
        _make_cypher_result(["A@CORP.LOCAL"]),
        {"A@CORP.LOCAL"},
    )
    assert diagnostic.grade.outcome == "INCORRECT"
    assert diagnostic.failure_subtype == "NO_PATH_REPORTED"


def test_mcp_wrong_path_subtype():
    task = _make_task("path_exists")
    diagnostic = grade_mcp_diagnostic(
        task,
        {"answer_type": "path_exists", "path_found": True, "node_names": ["B@CORP.LOCAL"]},
        _make_cypher_result(["A@CORP.LOCAL"]),
        {"A@CORP.LOCAL", "B@CORP.LOCAL"},
    )
    assert diagnostic.grade.outcome == "INCORRECT"
    assert diagnostic.failure_subtype == "WRONG_PATH"


def test_mcp_incomplete_answer_subtype():
    task = _make_task("path_exists")
    diagnostic = grade_mcp_diagnostic(
        task,
        {"answer_type": "path_exists", "path_found": True, "node_names": ["A@CORP.LOCAL"]},
        _make_cypher_result(["A@CORP.LOCAL", "B@CORP.LOCAL"]),
        {"A@CORP.LOCAL", "B@CORP.LOCAL"},
    )
    assert diagnostic.grade.outcome == "INCORRECT"
    assert diagnostic.failure_subtype == "INCOMPLETE_ANSWER"


def test_grade_cypher_error():
    task = _make_task()
    resp = _make_model_response()
    model_result = _make_cypher_result([], success=False)
    model_result.error = "Syntax error"
    result = grade(task, resp, model_result, _make_cypher_result(["A@CORP.LOCAL"]), set())
    assert result.score == 0.0
    assert result.outcome == "CYPHER_ERROR"


def test_grade_query_too_expensive_for_model_query_failure():
    task = _make_task()
    resp = _make_model_response()
    model_result = _make_cypher_result([], success=False)
    model_result.error = "HTTP 502: Bad Gateway"
    result = grade(task, resp, model_result, _make_cypher_result(["A@CORP.LOCAL"]), set())
    assert result.score == 0.0
    assert result.outcome == "QUERY_TOO_EXPENSIVE"


def test_grade_infra_error_for_reference_query_failure():
    task = _make_task()
    resp = _make_model_response()
    ref_result = _make_cypher_result([], success=False)
    ref_result.error = "Request failed: timed out"
    result = grade(task, resp, _make_cypher_result(["A@CORP.LOCAL"]), ref_result, set())
    assert result.score == 0.0
    assert result.outcome == "INFRA_ERROR"


def test_grade_path_exists_correct():
    task = _make_task("path_exists")
    resp = _make_model_response()
    ref = _make_cypher_result(["DJOHNSON@CORP.LOCAL", "DC01.CORP.LOCAL"])
    model = _make_cypher_result(["DJOHNSON@CORP.LOCAL", "IT-ADMINS@CORP.LOCAL", "DC01.CORP.LOCAL"])
    result = grade(task, resp, model, ref, set())
    assert result.score == 1.0
    assert result.outcome == "CORRECT"


def test_grade_path_exists_rejects_empty_model_result():
    """path_exists must return INCORRECT when model returns nothing."""
    task = _make_task("path_exists")
    resp = _make_model_response()
    ref = _make_cypher_result(["DJOHNSON@CORP.LOCAL", "DC01.CORP.LOCAL"])
    model = _make_cypher_result([])
    result = grade(task, resp, model, ref, set())
    assert result.score == 0.0
    assert result.outcome == "INCORRECT"


def test_grade_path_exists_rejects_unrelated_result():
    """path_exists must check ref nodes are in model result, not just any non-empty result."""
    task = _make_task("path_exists")
    resp = _make_model_response()
    ref = _make_cypher_result(["DJOHNSON@CORP.LOCAL", "DC01.CORP.LOCAL"])
    # Model returns unrelated nodes — does NOT contain the reference nodes
    model = _make_cypher_result(["MMOORE@CORP.LOCAL", "WS-IT-01.CORP.LOCAL"])
    result = grade(task, resp, model, ref, set())
    assert result.score == 0.0
    assert result.outcome == "INCORRECT"


def test_grade_node_set_superset_ok():
    task = _make_task("node_set")
    resp = _make_model_response()
    ref = _make_cypher_result(["SVC_BACKUP@CORP.LOCAL"])
    model = _make_cypher_result(["SVC_BACKUP@CORP.LOCAL", "SVC_MSSQL@CORP.LOCAL"])
    result = grade(task, resp, model, ref, set())
    assert result.score == 1.0


def test_grade_node_set_missing_ref_node():
    task = _make_task("node_set")
    resp = _make_model_response()
    ref = _make_cypher_result(["SVC_BACKUP@CORP.LOCAL", "SVC_MSSQL@CORP.LOCAL"])
    model = _make_cypher_result(["SVC_BACKUP@CORP.LOCAL"])  # missing SVC_MSSQL
    result = grade(task, resp, model, ref, set())
    assert result.score == 0.0
    assert result.outcome == "INCORRECT"


def test_grade_hallucination_auto_fail():
    task = _make_task("path_exists")
    resp = _make_model_response(cypher="MATCH (u:User {name: 'ADMIN@CORP.LOCAL'}) RETURN u")
    resp.raw_text = "MATCH (u:User {name: 'ADMIN@CORP.LOCAL'}) RETURN u"
    ref = _make_cypher_result(["ADMIN@CORP.LOCAL"])
    model = _make_cypher_result(["ADMIN@CORP.LOCAL"])
    valid_names = {"DJOHNSON@CORP.LOCAL", "DC01.CORP.LOCAL"}  # ADMIN not in valid set
    result = grade(task, resp, model, ref, valid_names)
    assert result.score == 0.0
    assert result.outcome == "HALLUCINATION"
    assert result.hallucination is True


def test_grade_row_count_within_tolerance():
    task = _make_task("row_count")
    resp = _make_model_response()
    ref = _make_cypher_result(["A@D", "B@D", "C@D", "D@D", "E@D"])  # 5 nodes
    model = _make_cypher_result(["A@D", "B@D", "C@D", "D@D"])  # 4 nodes — 20% diff
    result = grade(task, resp, model, ref, set())
    assert result.score == 1.0


def test_grade_row_count_outside_tolerance():
    task = _make_task("row_count")
    resp = _make_model_response()
    ref = _make_cypher_result(["A@D", "B@D", "C@D", "D@D", "E@D"])  # 5 nodes
    model = _make_cypher_result(["A@D"])  # 1 node — 80% diff
    result = grade(task, resp, model, ref, set())
    assert result.score == 0.0


# ---------------------------------------------------------------------------
# Hallucination detection
# ---------------------------------------------------------------------------


def test_hallucination_no_domain_names():
    task = _make_task()
    assert _check_hallucination("MATCH (u:User) RETURN u", {"DJOHNSON@CORP.LOCAL"}, task) is False


def test_hallucination_real_name_not_flagged():
    task = _make_task()
    valid = {"DJOHNSON@CORP.LOCAL", "DC01.CORP.LOCAL"}
    assert (
        _check_hallucination("MATCH (u:User {name: 'DJOHNSON@CORP.LOCAL'}) RETURN u", valid, task)
        is False
    )


def test_hallucination_invented_name_flagged():
    task = _make_task()
    valid = {"DJOHNSON@CORP.LOCAL"}
    text = "MATCH (u:User {name: 'HACKER@CORP.LOCAL'}) RETURN u"
    assert _check_hallucination(text, valid, task) is True


def test_hallucination_case_insensitive():
    task = _make_task()
    valid = {"DJOHNSON@CORP.LOCAL"}
    text = "djohnson@corp.local"
    assert _check_hallucination(text, valid, task) is False


# ---------------------------------------------------------------------------
# Task generation
# ---------------------------------------------------------------------------


def _make_manifest() -> dict:
    return {
        "domain": "TEST.LOCAL",
        "domain_sid": "S-1-5-21-1-2-3",
        "seed": 1,
        "planted_paths": [
            {
                "template_id": "t1_admin_to",
                "tier": 1,
                "category": "path_finding",
                "description": "User is member of admins",
                "source_node": "S-1-5-21-1-2-3-1100",
                "source_name": "JDOE@TEST.LOCAL",
                "target_node": "S-1-5-21-1-2-3-1101",
                "target_name": "DC01.TEST.LOCAL",
                "path_edges": [],
                "verification_cypher": "MATCH p=shortestPath((u:User {name: 'JDOE@TEST.LOCAL'})-[*1..]->(c:Computer {name: 'DC01.TEST.LOCAL'})) RETURN p",  # noqa: E501
                "mitre": ["T1078.002"],
            },
            {
                "template_id": "t1_group_membership",
                "tier": 1,
                "category": "path_finding",
                "description": "User is direct DA member",
                "source_node": "S-1-5-21-1-2-3-1102",
                "source_name": "RSMITH@TEST.LOCAL",
                "target_node": "S-1-5-21-1-2-3-512",
                "target_name": "DOMAIN ADMINS@TEST.LOCAL",
                "path_edges": [],
                "verification_cypher": "MATCH p=(u:User {name: 'RSMITH@TEST.LOCAL'})-[:MemberOf]->(g:Group {name: 'DOMAIN ADMINS@TEST.LOCAL'}) RETURN p",  # noqa: E501
                "mitre": ["T1078.002"],
            },
            {
                "template_id": "t2_acl_chain",
                "tier": 2,
                "category": "path_finding",
                "description": "DWOLFE has GenericAll on Server-Admins",
                "source_node": "S-1-5-21-1-2-3-1119",
                "source_name": "DWOLFE@TEST.LOCAL",
                "target_node": "S-1-5-21-1-2-3-1150",
                "target_name": "SRV-FILE-01.TEST.LOCAL",
                "path_edges": [],
                "verification_cypher": "MATCH p=shortestPath((u:User {name: 'DWOLFE@TEST.LOCAL'})-[*1..]->(c:Computer {name: 'SRV-FILE-01.TEST.LOCAL'})) RETURN p",
                "mitre": ["T1222.001"],
            },
            {
                "template_id": "t5_adcs_to_delegation_composite",
                "tier": 5,
                "category": "path_finding",
                "description": "TBERGER chains ADCS to delegation",
                "source_node": "S-1-5-21-1-2-3-1138",
                "source_name": "TBERGER@TEST.LOCAL",
                "target_node": "S-1-5-21-1-2-3-512",
                "target_name": "DOMAIN ADMINS@TEST.LOCAL",
                "path_edges": [],
                "verification_cypher": "MATCH p=(u:User {name: 'TBERGER@TEST.LOCAL'}) RETURN p",
                "mitre": ["T1649"],
            },
        ],
    }


def test_generate_tasks_count():
    tasks = generate_tasks(_make_manifest())
    assert len(tasks) == 11


def test_generate_tasks_source_name_in_question():
    tasks = generate_tasks(_make_manifest())
    admin_to = next(t for t in tasks if t.template_id == "t1_admin_to")
    assert "JDOE@TEST.LOCAL" in admin_to.question
    assert "DC01.TEST.LOCAL" in admin_to.question


def test_enumeration_task_has_own_reference_cypher():
    """Enumeration tasks must NOT use the planted path's verification_cypher."""
    tasks = generate_tasks(_make_manifest())
    enum_task = next(
        t for t in tasks if t.template_id == "t1_group_membership" and t.grade_mode == "node_set"
    )
    # Reference Cypher should query all DA members, not just the planted user
    assert "RSMITH@TEST.LOCAL" not in enum_task.reference_cypher
    assert "DOMAIN ADMINS@TEST.LOCAL" in enum_task.reference_cypher


def test_path_finding_task_uses_verification_cypher():
    """path_finding tasks should use the manifest's verification_cypher."""
    tasks = generate_tasks(_make_manifest())
    path_task = next(t for t in tasks if t.id == "t1_admin_to-01")
    assert "JDOE@TEST.LOCAL" in path_task.reference_cypher


def test_global_tasks_present():
    tasks = generate_tasks(_make_manifest())
    global_ids = {t.id for t in tasks if t.template_id == "global"}
    assert "global-kerberoastable" in global_ids
    assert "global-admin-to" in global_ids
    assert "global-da-members" in global_ids


def test_global_admin_to_returns_computers_not_paths():
    """global-admin-to reference Cypher must RETURN c (not p) to avoid polluting node_names."""
    tasks = generate_tasks(_make_manifest())
    task = next(t for t in tasks if t.id == "global-admin-to")
    assert "RETURN c" in task.reference_cypher
    assert "RETURN p" not in task.reference_cypher


def test_global_privileged_sessions_returns_computers_not_paths():
    tasks = generate_tasks(_make_manifest())
    task = next(t for t in tasks if t.id == "global-privileged-sessions")
    assert "RETURN c" in task.reference_cypher
    assert "RETURN p" not in task.reference_cypher


def test_acl_chain_02_names_source_and_target():
    tasks = generate_tasks(_make_manifest())
    task = next(t for t in tasks if t.id == "t2_acl_chain-02")
    assert "DWOLFE@TEST.LOCAL" in task.question
    assert "SRV-FILE-01.TEST.LOCAL" in task.question
    assert "abused group" in task.question


def test_phase4_composite_names_bridge_and_delegation_target():
    tasks = generate_tasks(_make_manifest())
    task = next(t for t in tasks if t.id == "t5_adcs_to_delegation_composite-01")
    assert "TBERGER@TEST.LOCAL" in task.question
    assert "SVC_PHASE4_BRIDGE@TEST.LOCAL" in task.question
    assert "WS-IT-04.TEST.LOCAL" in task.question


# ---------------------------------------------------------------------------
# Mock providers
# ---------------------------------------------------------------------------


def _make_task_for_mock() -> Task:
    return Task(
        id="mock-test",
        template_id="t1_admin_to",
        tier=1,
        category="path_finding",
        question="Find the attack path",
        reference_cypher="MATCH p=shortestPath((u:User {name: 'JDOE@TEST.LOCAL'})-[*1..]->(c:Computer)) RETURN p",  # noqa: E501
        grade_mode="path_exists",
        metadata={"domain": "TEST.LOCAL"},
    )


def test_mock_perfect_returns_reference_cypher():
    task = _make_task_for_mock()
    resp = asyncio.run(call_model(task, "mock/perfect"))
    assert resp.cypher == task.reference_cypher
    assert resp.error is None


def test_mock_empty_returns_no_cypher():
    task = _make_task_for_mock()
    resp = asyncio.run(call_model(task, "mock/empty"))
    assert resp.cypher is None
    assert resp.error is None


def test_mock_hallucinate_references_fake_node():
    task = _make_task_for_mock()
    resp = asyncio.run(call_model(task, "mock/hallucinate"))
    assert resp.cypher is not None
    assert "FAKE_SENTINEL_NODE@TEST.LOCAL" in resp.cypher
    assert "FAKE_SENTINEL_NODE@TEST.LOCAL" in resp.raw_text


def test_mock_wrong_returns_unrelated_cypher():
    task = _make_task_for_mock()
    resp = asyncio.run(call_model(task, "mock/wrong"))
    assert resp.cypher is not None
    assert "GPO" in resp.cypher  # returns GPO nodes, unrelated to path tasks


def test_mock_syntax_error_returns_malformed_cypher():
    task = _make_task_for_mock()
    resp = asyncio.run(call_model(task, "mock/syntax_error"))
    assert resp.cypher is not None
    assert extract_cypher(resp.cypher) is not None  # extractable but invalid Cypher


def test_mock_unknown_variant_returns_error():
    task = _make_task_for_mock()
    resp = asyncio.run(call_model(task, "mock/nonexistent"))
    assert resp.error is not None
    assert "nonexistent" in resp.error


def _make_phase4b_official_manifest():
    tasks = [
        {
            "id": "p4v2-smoke-01-startup-domain-count",
            "phase": "startup_smoke",
            "question": "Confirm domains.",
            "category": "startup_smoke",
            "grade_mode": "mechanical_binary",
            "smoke_task": True,
            "track": "startup",
            "scenario_family": "startup_smoke",
            "technical_difficulty": 1,
            "reasoning_difficulty": 1,
            "template_id": "startup_smoke",
            "reference": {"domains": ["forest.example", "CHILD.forest.example"]},
        }
    ]
    for index in range(2, 101):
        track = "cypher" if index <= 50 else "mcp"
        tasks.append(
            {
                "id": f"p4v2-matrix-{index - 1:03d}",
                "phase": "benchmark_matrix",
                "question": f"Solve official task {index - 1}.",
                "category": "path_finding",
                "grade_mode": "mechanical_binary",
                "smoke_task": False,
                "track": track,
                "scenario_family": "adcs_esc1",
                "technical_difficulty": 3,
                "reasoning_difficulty": 3,
                "template_id": "t1_admin_to",
                "reference_cypher": "MATCH p=(u:User)-[:AdminTo]->(c:Computer) RETURN p",
            }
        )
    return {"official_count": 100, "raw_score_denominator": 100, "tasks_official": tasks}


def test_phase4b_official_tasks_use_explicit_100_question_corpus():
    tasks = generate_tasks(_make_phase4b_official_manifest())
    assert len(tasks) == 100
    assert tasks[0].id == "p4v2-smoke-01-startup-domain-count"
    assert tasks[-1].id == "p4v2-matrix-099"
    assert sum(1 for task in tasks if task.metadata["track"] == "startup") == 1
    assert sum(1 for task in tasks if task.metadata["track"] == "cypher") == 49
    assert sum(1 for task in tasks if task.metadata["track"] == "mcp") == 50
    assert all(task.metadata["official_grade_mode"] == "mechanical_binary" for task in tasks)


def test_phase4b_mcp_tasks_do_not_fall_back_to_legacy_generated_subset():
    tasks = generate_mcp_tasks(_make_phase4b_official_manifest())
    assert len(tasks) == 100
    assert tasks[0].metadata["official_count"] == 100
    assert all("mcp_track" in task.metadata for task in tasks)
