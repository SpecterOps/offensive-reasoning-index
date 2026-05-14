from __future__ import annotations

import json

from click.testing import CliRunner

from ori.cli import main
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
