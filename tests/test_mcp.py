from __future__ import annotations

import csv
import json
from pathlib import Path

from click.testing import CliRunner

from ori.cli import main
from ori.eval.adapter import ModelResponse
from ori.eval.bhce import CypherResult
from ori.eval.grader import GradeResult, grade_mcp
from inspect_ai.tool import tool

from ori.eval.mcp_runtime import (
    MCPRunMetadata,
    _mcp_subprocess_env,
    _normalize_final_answer,
    _parse_json_object,
    _run_ollama_mcp_loop,
)
from ori.eval.report import write_combined_csv, write_summary_csv
from ori.eval.runner import EvalResult
from ori.eval.tasks import Task, generate_mcp_tasks


def _manifest() -> dict:
    return {
        "domain": "TEST.LOCAL",
        "stats": {"users": 2, "computers": 1, "groups": 3, "ous": 1},
        "planted_paths": [
            {
                "template_id": "t1_admin_to",
                "tier": 1,
                "description": "admin path",
                "source_name": "JDOE@TEST.LOCAL",
                "target_name": "DC01.TEST.LOCAL",
                "verification_cypher": "MATCH p=(u:User {name: 'JDOE@TEST.LOCAL'})-[*1..]->(c:Computer {name: 'DC01.TEST.LOCAL'}) RETURN p",
                "path_edges": [],
            },
            {
                "template_id": "t1_has_session",
                "tier": 1,
                "description": "session path",
                "source_name": "WS-01.TEST.LOCAL",
                "target_name": "JDOE@TEST.LOCAL",
                "verification_cypher": "MATCH p=(c:Computer {name: 'WS-01.TEST.LOCAL'})-[:HasSession]->(u:User {name: 'JDOE@TEST.LOCAL'}) RETURN p",
                "path_edges": [],
            },
            {
                "template_id": "t1_group_membership",
                "tier": 1,
                "description": "membership path",
                "source_name": "MMOORE@TEST.LOCAL",
                "target_name": "DOMAIN ADMINS@TEST.LOCAL",
                "verification_cypher": "MATCH p=(u:User {name: 'MMOORE@TEST.LOCAL'})-[:MemberOf*1..]->(g:Group {name: 'DOMAIN ADMINS@TEST.LOCAL'}) RETURN p",
                "path_edges": [],
            },
            {
                "template_id": "t2_nested_groups",
                "tier": 2,
                "description": "nested path",
                "source_name": "CDAVIS@TEST.LOCAL",
                "target_name": "SRV-01.TEST.LOCAL",
                "verification_cypher": "MATCH p=(u:User {name: 'CDAVIS@TEST.LOCAL'})-[:MemberOf*1..]->(:Group)-[:AdminTo]->(c:Computer {name: 'SRV-01.TEST.LOCAL'}) RETURN p",
                "path_edges": [],
            },
        ],
    }


def _task() -> Task:
    return Task(
        id="mcp-test",
        template_id="global",
        tier=1,
        category="mcp_analysis",
        question="List all computers where Domain Admins has admin rights",
        reference_cypher="MATCH (g:Group {name: 'DOMAIN ADMINS@TEST.LOCAL'})-[:AdminTo]->(c:Computer) RETURN c",
        grade_mode="node_set",
        metadata={"domain": "TEST.LOCAL", "mcp_track": "mcp_non_cypher_analysis"},
    )


def test_generate_mcp_tasks_includes_both_tracks() -> None:
    tasks = generate_mcp_tasks(_manifest())
    tracks = {task.metadata.get("mcp_track") for task in tasks}
    assert "mcp_cypher_assisted" in tracks
    assert "mcp_non_cypher_analysis" in tracks
    assert any(task.id == "mcp-global-admin-to" for task in tasks)
    assert any(task.id == "mcp-shortest-path-admin-to" for task in tasks)


def test_mcp_subprocess_env_strips_virtual_env(monkeypatch) -> None:
    monkeypatch.setenv("VIRTUAL_ENV", "/tmp/fake-venv")
    monkeypatch.setenv("PATH", "/usr/bin")
    env = _mcp_subprocess_env()
    assert "VIRTUAL_ENV" not in env
    assert env["PATH"] == "/usr/bin"


def test_parse_and_normalize_final_answer_handles_fenced_json() -> None:
    raw = '```json\n{"answer_type":"node_set","node_names":["A","B"]}\n```'
    parsed = _parse_json_object(raw)
    normalized = _normalize_final_answer(parsed, _task())
    assert normalized == {"answer_type": "node_set", "node_names": ["A", "B"]}


def test_run_ollama_mcp_loop_preserves_thinking_and_final_answer(monkeypatch) -> None:
    import asyncio

    calls = {"count": 0}

    async def fake_turn(**kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return {
                "model": "ori-qwen35-9b-32k",
                "thinking": "need group info first",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "function": {
                            "name": "group_info",
                            "arguments": {"group_name": "DOMAIN ADMINS@TEST.LOCAL", "info_type": "members"},
                        },
                    }
                ],
                "prompt_eval_count": 100,
                "eval_count": 20,
            }
        return {
            "model": "ori-qwen35-9b-32k",
            "thinking": "now answer",
            "content": '{"answer_type":"node_set","node_names":["WS-01.TEST.LOCAL"]}',
            "tool_calls": [],
            "prompt_eval_count": 120,
            "eval_count": 15,
        }

    monkeypatch.setattr("ori.eval.mcp_runtime._ollama_chat_turn", fake_turn)

    @tool(name="group_info")
    def group_info():
        async def execute(group_name: str, info_type: str) -> str:
            return '{"success": true, "nodes": ["WS-01.TEST.LOCAL"]}'

        return execute

    response, trajectory, messages = asyncio.run(
        _run_ollama_mcp_loop(
            task=_task(),
            model_name="ollama/ori-qwen35-9b-32k",
            base_url="http://127.0.0.1:11434/v1",
            ollama_options=None,
            tools=[group_info],
            max_steps=4,
        )
    )

    assert response.error is None
    assert response.parse_stage == "mcp_final_answer"
    assert response.raw_text == '{"answer_type":"node_set","node_names":["WS-01.TEST.LOCAL"]}'
    assert "need group info first" in response.thinking
    assert "now answer" in response.thinking
    assert response.tokens_input == 220
    assert response.tokens_output == 35
    assert trajectory.tool_calls_total == 1
    assert trajectory.non_cypher_tool_calls == 1
    assert trajectory.cypher_query_calls == 0
    assert trajectory.final_answer_raw == response.raw_text
    assert len(messages) >= 4


def test_hallucination_check_allows_group_names_with_spaces() -> None:
    from ori.eval.grader import _check_hallucination

    task = Task(
        id="group-test",
        template_id="global",
        tier=1,
        category="mcp_analysis",
        question="q",
        reference_cypher="MATCH (g:Group) RETURN g",
        grade_mode="node_set",
        metadata={"domain": "CORP.LOCAL"},
    )
    text = '{"answer_type":"node_set","node_names":["DOMAIN ADMINS@CORP.LOCAL"]}'
    assert _check_hallucination(text, {"DOMAIN ADMINS@CORP.LOCAL"}, task) is False


def test_hallucination_check_still_flags_fake_names() -> None:
    from ori.eval.grader import _check_hallucination

    task = Task(
        id="hallucination-test",
        template_id="global",
        tier=1,
        category="mcp_analysis",
        question="q",
        reference_cypher="MATCH (u:User) RETURN u",
        grade_mode="node_set",
        metadata={"domain": "CORP.LOCAL"},
    )
    text = '{"answer_type":"node_set","node_names":["FAKEUSER@CORP.LOCAL"]}'
    assert _check_hallucination(text, {"REALUSER@CORP.LOCAL"}, task) is True


def test_grade_mcp_correct() -> None:
    result = grade_mcp(
        task=_task(),
        model_response=ModelResponse(
            raw_text='{"answer_type":"node_set","node_names":["WS-01.TEST.LOCAL"]}',
            cypher=None,
            parse_stage="mcp_final_answer",
            tokens_input=0,
            tokens_output=0,
            elapsed_seconds=0.0,
            model="mock/mcp_perfect",
        ),
        final_answer={"answer_type": "node_set", "node_names": ["WS-01.TEST.LOCAL"]},
        ref_result=CypherResult(success=True, nodes=[{}], node_names={"WS-01.TEST.LOCAL"}, raw={}),
        valid_node_names={"WS-01.TEST.LOCAL"},
    )
    assert result.outcome == "CORRECT"


def test_grade_mcp_infra_if_no_final_answer_after_tool_failure() -> None:
    result = grade_mcp(
        task=_task(),
        model_response=ModelResponse(
            raw_text="",
            cypher=None,
            parse_stage="none",
            tokens_input=0,
            tokens_output=0,
            elapsed_seconds=0.0,
            model="mock/mcp_empty",
        ),
        final_answer=None,
        ref_result=CypherResult(success=True, nodes=[{}], node_names={"WS-01.TEST.LOCAL"}, raw={}),
        valid_node_names={"WS-01.TEST.LOCAL"},
        infra_tool_errors=1,
    )
    assert result.outcome == "INFRA_ERROR"


def test_mock_wrong_smoke_style_answer_stays_incorrect_not_hallucination() -> None:
    response = ModelResponse(
        raw_text='{"answer_type":"node_set","node_names":[]}',
        cypher=None,
        parse_stage="mcp_final_answer",
        tokens_input=0,
        tokens_output=0,
        elapsed_seconds=0.0,
        model="mock/mcp_wrong",
    )
    result = grade_mcp(
        task=_task(),
        model_response=response,
        final_answer={"answer_type": "node_set", "node_names": []},
        ref_result=CypherResult(success=True, nodes=[{}], node_names={"WS-01.TEST.LOCAL"}, raw={}),
        valid_node_names={"WS-01.TEST.LOCAL"},
    )
    assert result.outcome == "INCORRECT"


def test_report_includes_mcp_fields(tmp_path: Path) -> None:
    result = EvalResult(
        task=_task(),
        model_response=ModelResponse(
            raw_text='{"answer_type":"node_set","node_names":["WS-01.TEST.LOCAL"]}',
            cypher=None,
            parse_stage="mcp_final_answer",
            tokens_input=10,
            tokens_output=10,
            elapsed_seconds=1.0,
            model="mock/mcp_perfect",
        ),
        grade=GradeResult(score=1.0, outcome="CORRECT", hallucination=False, details="ok"),
        ref_result=CypherResult(success=True, nodes=[{}], node_names={"WS-01.TEST.LOCAL"}, raw={}),
        model_result=CypherResult(success=True, nodes=[], node_names=set(), raw={}),
        mcp=MCPRunMetadata(
            final_answer_raw='{"answer_type":"node_set","node_names":["WS-01.TEST.LOCAL"]}',
            final_answer_normalized={"answer_type": "node_set", "node_names": ["WS-01.TEST.LOCAL"]},
            tool_calls_total=3,
            failed_tool_calls=1,
            unique_tools_used=["domain_info", "group_info"],
            cypher_query_calls=1,
            non_cypher_tool_calls=2,
            agent_turns=2,
            attempted_policy_violations=0,
            trajectory_log="trace.eval",
        ),
    )
    combined = tmp_path / "combined.csv"
    summary = tmp_path / "summary.csv"
    write_combined_csv({"mock/mcp_perfect": [result]}, combined)
    write_summary_csv({"mock/mcp_perfect": [result]}, summary)
    with combined.open() as f:
        row = next(csv.DictReader(f))
    assert row["eval_mode"] == "mcp"
    assert row["tool_calls_total"] == "3"
    assert row["cypher_query_calls"] == "1"
    with summary.open() as f:
        summary_row = next(csv.DictReader(f))
    assert summary_row["avg_tool_calls"] == "3.0"
    assert summary_row["non_cypher_tool_calls"] == "2"


def test_cli_smoke_mcp(tmp_path: Path, monkeypatch) -> None:
    from ori.eval.ops import SmokeCheck, SmokeEvalResult

    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(_manifest()))

    async def fake_smoke_mcp_eval(
        manifest_path: Path,
        output_dir: Path,
        bhce_url: str | None = None,
        mcp_dir: Path | None = None,
        max_steps: int = 12,
        resource_mode: str = "off",
    ) -> SmokeEvalResult:
        return SmokeEvalResult(
            checks=[
                SmokeCheck("mock/mcp_perfect", "CORRECT", {"CORRECT": 2}),
                SmokeCheck("mock/mcp_wrong", "INCORRECT", {"INCORRECT": 2}),
                SmokeCheck("mock/mcp_empty", "PARSE_FAIL", {"PARSE_FAIL": 2}),
            ],
            results_by_model={"mock/mcp_perfect": [], "mock/mcp_wrong": [], "mock/mcp_empty": []},
        )

    monkeypatch.setattr("ori.eval.ops.run_smoke_mcp_eval", fake_smoke_mcp_eval)
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["smoke-mcp", "-m", str(manifest_path), "-o", str(tmp_path / "smoke")],
    )
    assert result.exit_code == 0
    assert "SMOKE TEST: PASS" in result.output
