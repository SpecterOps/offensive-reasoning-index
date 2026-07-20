from __future__ import annotations

import json

from click.testing import CliRunner

from ori.cli import main
from ori.eval.answer_scoring import _answer_final, score_answers_projection
from ori.eval.bhce import CypherResult
from ori.eval.contracts import AnswerContract, task_contract_for
from ori.eval.grader import grade_mcp_diagnostic
from ori.eval.mcp_runtime import MCPNoProgressTimeout, _mcp_system_prompt, _raise_if_no_progress
from ori.eval.tasks import Task, generate_mcp_tasks


def _task(task_id: str = "sample", mode: str = "node_set") -> Task:
    return Task(
        id=task_id,
        template_id="t4_adcs_esc1",
        tier=4,
        category="mcp_analysis",
        question="Find root CA and NTAuth objects.",
        reference_cypher="RETURN 1",
        grade_mode=mode,
        tags=["phase4", "adcs"],
        metadata={"domain": "CORP.LOCAL"},
    )


def test_offline_answer_extraction_preserves_mechanism_evidence() -> None:
    assert _answer_final(
        {
            "answer_type": "path_exists",
            "path_found": True,
            "node_names": ["A", "B"],
            "mechanisms": ["Enroll"],
        }
    ) == {
        "answer_type": "path_exists",
        "path_found": True,
        "node_names": ["A", "B"],
        "mechanisms": ["Enroll"],
    }


def test_extra_valid_nodes_are_not_hallucinations() -> None:
    task = _task()
    result = grade_mcp_diagnostic(
        task=task,
        final_answer={
            "answer_type": "node_set",
            "node_names": ["USER1@CORP.LOCAL", "ROOTCA@CORP.LOCAL"],
        },
        ref_result=CypherResult(success=True, node_names={"USER1@CORP.LOCAL"}),
        valid_node_names={"USER1@CORP.LOCAL", "ROOTCA@CORP.LOCAL"},
    )

    assert result.grade.outcome == "CORRECT"
    assert result.hallucinated_nodes == []
    assert result.extra_valid_nodes == ["ROOTCA@CORP.LOCAL"]
    assert result.metrics["precision"] == 0.5
    assert result.metrics["recall"] == 1.0


def test_unknown_answer_nodes_are_hallucinations() -> None:
    result = grade_mcp_diagnostic(
        task=_task(),
        final_answer={"answer_type": "node_set", "node_names": ["PHANTOM@CORP.LOCAL"]},
        ref_result=CypherResult(success=True, node_names={"USER1@CORP.LOCAL"}),
        valid_node_names={"USER1@CORP.LOCAL"},
    )

    assert result.grade.outcome == "HALLUCINATION"
    assert result.hallucinated_nodes == ["PHANTOM@CORP.LOCAL"]
    assert result.missing_reference_nodes == ["USER1@CORP.LOCAL"]


def test_contract_required_and_optional_nodes_affect_diagnostics() -> None:
    contract = AnswerContract(
        task_id="sample",
        required_nodes=("REQ@CORP.LOCAL",),
        optional_nodes=("OPT@CORP.LOCAL",),
    )

    result = grade_mcp_diagnostic(
        task=_task(),
        final_answer={
            "answer_type": "node_set",
            "node_names": ["REQ@CORP.LOCAL", "OPT@CORP.LOCAL"],
        },
        ref_result=CypherResult(success=True, node_names={"REQ@CORP.LOCAL"}),
        valid_node_names={"REQ@CORP.LOCAL", "OPT@CORP.LOCAL"},
        contract=contract,
    )

    assert result.grade.outcome == "CORRECT"
    assert result.missing_required_nodes == []
    assert result.extra_valid_nodes == []
    assert result.optional_nodes_present == ["OPT@CORP.LOCAL"]


def test_phase4_adcs_tasks_have_contracts_from_manifest_metadata() -> None:
    manifest = {
        "domain": "CORP.LOCAL",
        "planted_paths": [
            {
                "template_id": "t4_adcs_esc1",
                "tier": 4,
                "source_name": "ALICE@CORP.LOCAL",
                "target_name": "DOMAIN ADMINS@CORP.LOCAL",
                "description": "adcs",
                "verification_cypher": "MATCH p=() RETURN p",
                "critical_nodes": ["ALICE@CORP.LOCAL", "ROOTCA@CORP.LOCAL", "NTAUTH@CORP.LOCAL"],
            }
        ],
    }
    task = next(t for t in generate_mcp_tasks(manifest) if t.id == "t4_adcs_esc1-01")

    contract = task_contract_for(task)

    assert contract is not None
    assert "ROOTCA@CORP.LOCAL" in contract.required_nodes
    assert "NTAUTH@CORP.LOCAL" in contract.required_nodes


def test_score_answers_cli_writes_per_task_diagnostics(tmp_path) -> None:
    manifest = {
        "domain": "CORP.LOCAL",
        "planted_paths": [
            {
                "template_id": "t4_adcs_esc1",
                "tier": 4,
                "source_name": "ALICE@CORP.LOCAL",
                "target_name": "DOMAIN ADMINS@CORP.LOCAL",
                "description": "adcs",
                "verification_cypher": "MATCH p=() RETURN p",
                "critical_nodes": ["ALICE@CORP.LOCAL", "ROOTCA@CORP.LOCAL"],
            }
        ],
    }
    manifest_path = tmp_path / "manifest.json"
    answers_path = tmp_path / "answers.json"
    output_path = tmp_path / "projection.json"
    manifest_path.write_text(json.dumps(manifest))
    answers_path.write_text(
        json.dumps(
            {
                "valid_node_names": ["ALICE@CORP.LOCAL", "ROOTCA@CORP.LOCAL", "OTHER@CORP.LOCAL"],
                "answers": [
                    {
                        "task_id": "t4_adcs_esc1-01",
                        "final_answer": {
                            "answer_type": "path_exists",
                            "path_found": True,
                            "node_names": [
                                "ALICE@CORP.LOCAL",
                                "ROOTCA@CORP.LOCAL",
                                "OTHER@CORP.LOCAL",
                            ],
                        },
                        "reference_nodes": ["ALICE@CORP.LOCAL", "ROOTCA@CORP.LOCAL"],
                    }
                ],
            }
        )
    )

    result = CliRunner().invoke(
        main,
        [
            "score-answers",
            "--manifest",
            str(manifest_path),
            "--answers",
            str(answers_path),
            "--track",
            "mcp",
            "--output",
            str(output_path),
        ],
    )

    assert result.exit_code == 0, result.output
    projection = json.loads(output_path.read_text())
    assert projection["summary"]["completed_samples"] == 1
    diagnostic = projection["tasks"][0]
    assert diagnostic["task_id"] == "t4_adcs_esc1-01"
    assert diagnostic["outcome"] == "CORRECT"
    assert diagnostic["extra_valid_nodes"] == ["OTHER@CORP.LOCAL"]
    assert diagnostic["hallucinated_nodes"] == []


def test_no_progress_watchdog_raises_explicit_subtype() -> None:
    try:
        _raise_if_no_progress(last_activity=10.0, now=15.1, timeout_seconds=5.0, scope="turn")
    except MCPNoProgressTimeout as exc:
        assert exc.subtype == "MCP_TURN_TIMEOUT"
        assert "no progress" in str(exc)
    else:
        raise AssertionError("expected timeout")


def test_mcp_prompt_includes_bloodhound_cypher_quirks() -> None:
    prompt = _mcp_system_prompt(_task())

    assert "never duplicate RETURN columns" in prompt
    assert "avoid unsupported UNION" in prompt
    assert "COALESCE" in prompt


def test_score_answers_accepts_top_level_manual_answer_shape(tmp_path) -> None:
    manifest = {
        "domain": "CORP.LOCAL",
        "planted_paths": [
            {
                "template_id": "t4_adcs_esc1",
                "tier": 4,
                "source_name": "ALICE@CORP.LOCAL",
                "target_name": "DOMAIN ADMINS@CORP.LOCAL",
                "description": "adcs",
                "verification_cypher": "MATCH p=() RETURN p",
                "critical_nodes": ["ALICE@CORP.LOCAL", "ROOTCA@CORP.LOCAL"],
            }
        ],
    }
    manifest_path = tmp_path / "manifest.json"
    answers_path = tmp_path / "answers.json"
    manifest_path.write_text(json.dumps(manifest))
    answers_path.write_text(
        json.dumps(
            {
                "valid_node_names": ["ALICE@CORP.LOCAL", "ROOTCA@CORP.LOCAL"],
                "answers": [
                    {
                        "id": "t4_adcs_esc1-01",
                        "answer_type": "path",
                        "path": ["ALICE@CORP.LOCAL", "ROOTCA@CORP.LOCAL"],
                    }
                ],
            }
        )
    )

    projection = score_answers_projection(
        manifest_path=manifest_path,
        answers_path=answers_path,
        track="mcp",
    )

    assert projection["tasks"][0]["outcome"] != "PARSE_FAIL"
    assert projection["tasks"][0]["answer_nodes"] == ["ALICE@CORP.LOCAL", "ROOTCA@CORP.LOCAL"]


def test_score_answers_flattens_top_level_manual_paths_shape(tmp_path) -> None:
    manifest = {
        "domain": "CORP.LOCAL",
        "planted_paths": [
            {
                "template_id": "custom_path",
                "tier": 1,
                "source_name": "ALICE@CORP.LOCAL",
                "target_name": "DC01.CORP.LOCAL",
                "description": "custom",
                "verification_cypher": "MATCH p=() RETURN p",
            }
        ],
    }
    manifest_path = tmp_path / "manifest.json"
    answers_path = tmp_path / "answers.json"
    manifest_path.write_text(json.dumps(manifest))
    answers_path.write_text(
        json.dumps(
            {
                "valid_node_names": ["ALICE@CORP.LOCAL", "BOB@CORP.LOCAL", "DC01.CORP.LOCAL"],
                "answers": [
                    {
                        "task_id": "mcp-global-admin-to",
                        "answer_type": "paths",
                        "paths": [["ALICE@CORP.LOCAL", "DC01.CORP.LOCAL"], ["BOB@CORP.LOCAL"]],
                    }
                ],
                "reference_results": {
                    "mcp-global-admin-to": {
                        "success": True,
                        "node_names": ["ALICE@CORP.LOCAL", "DC01.CORP.LOCAL"],
                    }
                },
            }
        )
    )

    projection = score_answers_projection(
        manifest_path=manifest_path,
        answers_path=answers_path,
        track="mcp",
    )

    assert projection["tasks"][0]["outcome"] == "CORRECT"
    assert projection["tasks"][0]["answer_nodes"] == [
        "ALICE@CORP.LOCAL",
        "BOB@CORP.LOCAL",
        "DC01.CORP.LOCAL",
    ]


def test_score_answers_requires_reference_input_for_non_contract_tasks(tmp_path) -> None:
    manifest = {
        "domain": "CORP.LOCAL",
        "planted_paths": [
            {
                "template_id": "custom_path",
                "tier": 1,
                "source_name": "ALICE@CORP.LOCAL",
                "target_name": "DC01.CORP.LOCAL",
                "description": "custom",
                "verification_cypher": "MATCH p=() RETURN p",
            }
        ],
    }
    manifest_path = tmp_path / "manifest.json"
    answers_path = tmp_path / "answers.json"
    manifest_path.write_text(json.dumps(manifest))
    answers_path.write_text(
        json.dumps(
            {
                "valid_node_names": ["ALICE@CORP.LOCAL", "DC01.CORP.LOCAL"],
                "answers": [
                    {
                        "task_id": "mcp-global-admin-to",
                        "answer_type": "path_exists",
                        "path_found": True,
                        "node_names": ["ALICE@CORP.LOCAL", "DC01.CORP.LOCAL"],
                    }
                ],
            }
        )
    )

    result = CliRunner().invoke(
        main,
        [
            "score-answers",
            "--manifest",
            str(manifest_path),
            "--answers",
            str(answers_path),
            "--track",
            "mcp",
            "--output",
            str(tmp_path / "projection.json"),
        ],
    )

    assert result.exit_code != 0
    assert "reference_results" in result.output
    assert "scorer_projection.json" in result.output


def test_score_answers_uses_reference_sidecar_for_non_contract_tasks(tmp_path) -> None:
    manifest = {
        "domain": "CORP.LOCAL",
        "planted_paths": [
            {
                "template_id": "custom_path",
                "tier": 1,
                "source_name": "ALICE@CORP.LOCAL",
                "target_name": "DC01.CORP.LOCAL",
                "description": "custom",
                "verification_cypher": "MATCH p=() RETURN p",
            }
        ],
    }
    manifest_path = tmp_path / "manifest.json"
    answers_path = tmp_path / "answers.json"
    manifest_path.write_text(json.dumps(manifest))
    answers_path.write_text(
        json.dumps(
            {
                "valid_node_names": ["ALICE@CORP.LOCAL", "DC01.CORP.LOCAL"],
                "answers": [
                    {
                        "task_id": "mcp-global-admin-to",
                        "answer_type": "path_exists",
                        "path_found": True,
                        "node_names": ["ALICE@CORP.LOCAL", "DC01.CORP.LOCAL"],
                    }
                ],
                "reference_results": {
                    "mcp-global-admin-to": {
                        "success": True,
                        "node_names": ["ALICE@CORP.LOCAL", "DC01.CORP.LOCAL"],
                    }
                },
            }
        )
    )

    projection = score_answers_projection(
        manifest_path=manifest_path,
        answers_path=answers_path,
        track="mcp",
    )

    assert projection["tasks"][0]["reference_nodes"] == ["ALICE@CORP.LOCAL", "DC01.CORP.LOCAL"]
    assert projection["tasks"][0]["outcome"] == "CORRECT"


def test_score_answers_does_not_treat_answer_nodes_as_valid_inventory(tmp_path) -> None:
    manifest = {
        "domain": "CORP.LOCAL",
        "planted_paths": [
            {
                "template_id": "custom_path",
                "tier": 1,
                "source_name": "ALICE@CORP.LOCAL",
                "target_name": "DC01.CORP.LOCAL",
                "description": "custom",
                "verification_cypher": "MATCH p=() RETURN p",
            }
        ],
    }
    manifest_path = tmp_path / "manifest.json"
    answers_path = tmp_path / "answers.json"
    manifest_path.write_text(json.dumps(manifest))
    answers_path.write_text(
        json.dumps(
            {
                "valid_node_names": ["ALICE@CORP.LOCAL", "DC01.CORP.LOCAL"],
                "answers": [
                    {
                        "task_id": "mcp-global-admin-to",
                        "answer_type": "node_set",
                        "node_names": ["ALICE@CORP.LOCAL", "PHANTOM@CORP.LOCAL"],
                    }
                ],
                "reference_results": {
                    "mcp-global-admin-to": {"success": True, "node_names": ["ALICE@CORP.LOCAL"]}
                },
            }
        )
    )

    projection = score_answers_projection(
        manifest_path=manifest_path,
        answers_path=answers_path,
        track="mcp",
    )

    assert projection["tasks"][0]["outcome"] == "HALLUCINATION"
    assert projection["tasks"][0]["hallucinated_nodes"] == ["PHANTOM@CORP.LOCAL"]


def test_score_answers_uses_sidecar_rows_id_key_and_count(tmp_path) -> None:
    manifest = {
        "domain": "CORP.LOCAL",
        "planted_paths": [
            {
                "template_id": "custom_path",
                "tier": 1,
                "source_name": "ALICE@CORP.LOCAL",
                "target_name": "DC01.CORP.LOCAL",
                "description": "custom",
                "verification_cypher": "MATCH p=() RETURN p",
            }
        ],
    }
    manifest_path = tmp_path / "manifest.json"
    answers_path = tmp_path / "answers.json"
    sidecar_path = tmp_path / "scorer_projection.json"
    manifest_path.write_text(json.dumps(manifest))
    answers_path.write_text(
        json.dumps(
            {
                "valid_node_names": ["IT-ADMINS@CORP.LOCAL", "MSMITH@CORP.LOCAL"],
                "answers": [
                    {
                        "task_id": "mcp-global-da-direct-member-count",
                        "answer_type": "row_count",
                        "count": 2,
                    }
                ],
            }
        )
    )
    sidecar_path.write_text(
        json.dumps(
            {
                "rows": [
                    {
                        "id": "mcp-global-da-direct-member-count",
                        "ref_success": True,
                        "ref_names": ["IT-ADMINS@CORP.LOCAL", "MSMITH@CORP.LOCAL"],
                    }
                ]
            }
        )
    )

    projection = score_answers_projection(
        manifest_path=manifest_path,
        answers_path=answers_path,
        track="mcp",
    )

    assert projection["tasks"][0]["outcome"] == "CORRECT"
    assert projection["tasks"][0]["metrics"]["reference_count_literal"] == 2


def test_score_answers_filters_builtin_low_privilege_group_for_privileged_group_task(
    tmp_path,
) -> None:
    manifest = {
        "domain": "CORP.LOCAL",
        "planted_paths": [
            {
                "template_id": "t1_group_membership",
                "tier": 1,
                "source_name": "MMOORE@CORP.LOCAL",
                "target_name": "DOMAIN ADMINS@CORP.LOCAL",
                "description": "membership",
                "verification_cypher": "MATCH p=() RETURN p",
            }
        ],
    }
    manifest_path = tmp_path / "manifest.json"
    answers_path = tmp_path / "answers.json"
    manifest_path.write_text(json.dumps(manifest))
    answers_path.write_text(
        json.dumps(
            {
                "valid_node_names": [
                    "DOMAIN ADMINS@CORP.LOCAL",
                    "DOMAIN USERS@CORP.LOCAL",
                    "ENTERPRISE ADMINS@CORP.LOCAL",
                ],
                "answers": [
                    {
                        "task_id": "mcp-user-privileged-group-memberships",
                        "answer_type": "node_set",
                        "node_names": ["DOMAIN ADMINS@CORP.LOCAL", "ENTERPRISE ADMINS@CORP.LOCAL"],
                    }
                ],
                "reference_results": {
                    "mcp-user-privileged-group-memberships": {
                        "success": True,
                        "node_names": [
                            "DOMAIN ADMINS@CORP.LOCAL",
                            "DOMAIN USERS@CORP.LOCAL",
                            "ENTERPRISE ADMINS@CORP.LOCAL",
                        ],
                    }
                },
            }
        )
    )

    projection = score_answers_projection(
        manifest_path=manifest_path,
        answers_path=answers_path,
        track="mcp",
    )

    assert projection["tasks"][0]["outcome"] == "CORRECT"
    assert projection["tasks"][0]["reference_nodes"] == [
        "DOMAIN ADMINS@CORP.LOCAL",
        "ENTERPRISE ADMINS@CORP.LOCAL",
    ]


def test_score_answers_sidecar_reference_names_override_manifest_object_id_contract(
    tmp_path,
) -> None:
    manifest = {
        "domain": "CORP.LOCAL",
        "planted_paths": [
            {
                "template_id": "t4_adcs_esc1",
                "tier": 4,
                "source_name": "ALICE@CORP.LOCAL",
                "target_name": "DOMAIN ADMINS@CORP.LOCAL",
                "description": "adcs",
                "verification_cypher": "MATCH p=() RETURN p",
                "critical_nodes": ["S-1-5-21-1-ENTERPRISECA-PHASE4"],
            }
        ],
    }
    manifest_path = tmp_path / "manifest.json"
    answers_path = tmp_path / "answers.json"
    sidecar_path = tmp_path / "scorer_projection.json"
    manifest_path.write_text(json.dumps(manifest))
    answers_path.write_text(
        json.dumps(
            {
                "valid_node_names": ["ORI-ENTERPRISE-CA@CORP.LOCAL"],
                "answers": [
                    {
                        "task_id": "t4_adcs_esc1-01",
                        "answer_type": "path",
                        "path": ["ORI-ENTERPRISE-CA@CORP.LOCAL"],
                    }
                ],
            }
        )
    )
    sidecar_path.write_text(
        json.dumps(
            {
                "rows": [
                    {
                        "id": "t4_adcs_esc1-01",
                        "ref_success": True,
                        "ref_names": ["ORI-ENTERPRISE-CA@CORP.LOCAL"],
                    }
                ]
            }
        )
    )

    projection = score_answers_projection(
        manifest_path=manifest_path,
        answers_path=answers_path,
        track="mcp",
    )

    assert projection["tasks"][0]["outcome"] == "CORRECT"
    assert projection["tasks"][0]["reference_nodes"] == ["ORI-ENTERPRISE-CA@CORP.LOCAL"]


def test_score_answers_unconstrained_delegation_uses_planted_source_and_target(tmp_path) -> None:
    manifest = {
        "domain": "CORP.LOCAL",
        "planted_paths": [
            {
                "template_id": "t3_unconstrained_delegation",
                "tier": 3,
                "source_name": "TTHOMAS@CORP.LOCAL",
                "target_name": "WS-IT-12.CORP.LOCAL",
                "description": "unconstrained",
                "verification_cypher": "MATCH p=() RETURN p",
            }
        ],
    }
    manifest_path = tmp_path / "manifest.json"
    answers_path = tmp_path / "answers.json"
    sidecar_path = tmp_path / "scorer_projection.json"
    manifest_path.write_text(json.dumps(manifest))
    answers_path.write_text(
        json.dumps(
            {
                "valid_node_names": [
                    "TTHOMAS@CORP.LOCAL",
                    "WS-IT-12.CORP.LOCAL",
                    "RBAKER@CORP.LOCAL",
                ],
                "answers": [
                    {
                        "task_id": "t3_unconstrained_delegation-01",
                        "answer_type": "paths",
                        "paths": [["WS-IT-12.CORP.LOCAL", "TTHOMAS@CORP.LOCAL"]],
                    }
                ],
            }
        )
    )
    sidecar_path.write_text(
        json.dumps(
            {
                "rows": [
                    {
                        "id": "t3_unconstrained_delegation-01",
                        "ref_success": True,
                        "ref_names": ["RBAKER@CORP.LOCAL", "WS-IT-12.CORP.LOCAL"],
                    }
                ]
            }
        )
    )

    projection = score_answers_projection(
        manifest_path=manifest_path,
        answers_path=answers_path,
        track="mcp",
    )

    assert projection["tasks"][0]["outcome"] == "CORRECT"
    assert projection["tasks"][0]["reference_nodes"] == [
        "TTHOMAS@CORP.LOCAL",
        "WS-IT-12.CORP.LOCAL",
    ]
