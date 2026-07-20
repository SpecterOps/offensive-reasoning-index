from ori.eval.bhce import CypherResult
from ori.eval.diagnostics import build_final_answer_diagnostics, validate_final_answer_contract
from ori.eval.grader import grade_mcp_diagnostic
from ori.eval.tasks import Task


def _task(mode="path_exists"):
    return Task(
        id="diag-01",
        template_id="t_path",
        tier=3,
        category="path",
        question="Find path",
        reference_cypher="MATCH p RETURN p",
        grade_mode=mode,
        metadata={"domain": "CORP.LOCAL"},
    )


def _ref(names):
    return CypherResult(
        success=True,
        nodes=[{"label": n, "objectId": f"SID-{i}"} for i, n in enumerate(names, start=1)],
        node_names=set(names),
    )


def test_contract_validation_path_requires_path_found_and_nodes_when_true():
    task = _task()
    assert not validate_final_answer_contract(task, {"answer_type": "path_exists"})
    assert not validate_final_answer_contract(
        task, {"answer_type": "path_exists", "path_found": True}
    )
    assert validate_final_answer_contract(
        task,
        {"answer_type": "path_exists", "path_found": True, "node_names": ["A@CORP.LOCAL"]},
    )
    assert validate_final_answer_contract(
        task,
        {"answer_type": "path_exists", "path_found": False, "node_names": []},
    )


def test_contract_validation_no_path_requires_explicit_empty_rejection() -> None:
    task = _task("no_path")

    assert validate_final_answer_contract(
        task, {"answer_type": "no_path", "path_found": False, "node_names": []}
    )
    assert validate_final_answer_contract(
        task, {"answer_type": "path_exists", "path_found": False, "node_names": []}
    )
    assert not validate_final_answer_contract(
        task, {"answer_type": "no_path", "path_found": True, "node_names": []}
    )
    assert not validate_final_answer_contract(
        task, {"answer_type": "no_path", "path_found": False, "node_names": ["A"]}
    )


def test_mcp_diagnostic_records_invalid_and_missing_entities_without_changing_strict_score():
    task = _task()
    final_answer = {
        "answer_type": "path_exists",
        "path_found": True,
        "node_names": ["A@CORP.LOCAL", "INVENTED@CORP.LOCAL"],
    }
    diag = grade_mcp_diagnostic(
        task=task,
        final_answer=final_answer,
        ref_result=_ref(["A@CORP.LOCAL", "B@CORP.LOCAL"]),
        valid_node_names={"A@CORP.LOCAL", "B@CORP.LOCAL"},
    )
    assert diag.grade.score == 0.0
    assert diag.grade.outcome == "HALLUCINATION"
    assert diag.final_answer_diagnostics is not None
    fields = diag.final_answer_diagnostics.to_jsonable()
    assert fields["invalid_entities"] == ["INVENTED@CORP.LOCAL"]
    assert fields["missing_required_entities"] == ["B@CORP.LOCAL"]
    assert fields["final_answer_contract_valid"] is True


def test_evidence_found_no_synthesis_stage_for_no_path_with_tool_evidence():
    task = _task()
    diag = build_final_answer_diagnostics(
        task=task,
        final_answer={"answer_type": "path_exists", "path_found": False, "node_names": []},
        ref_result=_ref(["A@CORP.LOCAL"]),
        valid_node_names={"A@CORP.LOCAL"},
        contract=None,
        successful_tool_results=2,
        cypher_query_calls=1,
        grade_outcome="INCORRECT",
        failure_subtype="NO_PATH_REPORTED",
    )
    assert diag.evidence_found is True
    assert diag.failure_stage == "evidence_found_no_synthesis"
    assert diag.evidence_depth_score >= 3


def test_mcp_diagnostic_treats_reference_only_identifiers_as_valid():
    task = _task("node_set")
    final_answer = {
        "answer_type": "node_set",
        "node_names": [
            "ORI-ENTERPRISE-CA@CORP.LOCAL",
            "S-1-5-21-EXAMPLE-ENTERPRISECA-PHASE4",
        ],
    }
    diag = grade_mcp_diagnostic(
        task=task,
        final_answer=final_answer,
        ref_result=_ref(
            [
                "ORI-ENTERPRISE-CA@CORP.LOCAL",
                "S-1-5-21-EXAMPLE-ENTERPRISECA-PHASE4",
            ]
        ),
        valid_node_names={"ORI-ENTERPRISE-CA@CORP.LOCAL"},
    )

    assert diag.grade.outcome == "CORRECT"
    assert diag.hallucinated_nodes == []
    assert diag.final_answer_diagnostics is not None
    assert diag.final_answer_diagnostics.invalid_entities == []


def test_mcp_diagnostic_accepts_object_id_alias_for_reference_label():
    task = _task("node_set")
    final_answer = {"answer_type": "node_set", "node_names": ["SID-1"]}
    diag = grade_mcp_diagnostic(
        task=task,
        final_answer=final_answer,
        ref_result=_ref(["DOMAIN ADMINS@CORP.LOCAL"]),
        valid_node_names={"DOMAIN ADMINS@CORP.LOCAL", "SID-1"},
    )

    assert diag.grade.outcome == "CORRECT"
    assert diag.missing_required_nodes == []
    assert diag.metrics["alias_overlap_count"] == 1
    assert diag.final_answer_diagnostics is not None
    assert diag.final_answer_diagnostics.missing_required_entities == []


def test_mcp_diagnostic_accepts_label_when_reference_has_object_id_alias():
    task = _task("path_exists")
    final_answer = {
        "answer_type": "path_exists",
        "path_found": True,
        "node_names": ["TGARZA@CORP.LOCAL", "DOMAIN ADMINS@CORP.LOCAL"],
    }
    diag = grade_mcp_diagnostic(
        task=task,
        final_answer=final_answer,
        ref_result=_ref(["TGARZA@CORP.LOCAL", "DOMAIN ADMINS@CORP.LOCAL"]),
        valid_node_names={
            "TGARZA@CORP.LOCAL",
            "DOMAIN ADMINS@CORP.LOCAL",
            "SID-1",
            "SID-2",
        },
    )

    assert diag.grade.outcome == "CORRECT"
    assert diag.grade.score == 1.0
    assert diag.missing_required_nodes == []
