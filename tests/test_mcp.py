from __future__ import annotations

import csv
import json
from pathlib import Path

from mcp.types import EmbeddedResource, PromptMessage, TextContent, TextResourceContents
from click.testing import CliRunner

from ori.cli import main
from ori.eval.adapter import ModelResponse
from ori.eval.bhce import CypherResult
from ori.eval.grader import GradeResult, grade_mcp
from ori.eval.mcp_runtime import (
    BLOODHOUND_PROMPT_NAME,
    MCPRunMetadata,
    RESOURCE_MODE_ON_DEMAND,
    _mcp_conversation_messages,
    _mcp_subprocess_env,
    _normalize_final_answer,
    _parse_json_object,
    _prompt_messages_to_text,
    _trajectory_from_messages,
)
from ori.eval.report import write_combined_csv, write_summary_csv
from ori.eval.runner import EvalResult
from ori.eval.tasks import Task, generate_mcp_tasks
from inspect_ai.model import ChatMessageAssistant, ChatMessageTool


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


def test_prompt_messages_to_text_handles_text_and_embedded_resource() -> None:
    prompt_text = _prompt_messages_to_text(
        [
            PromptMessage(role="user", content=TextContent(type="text", text="Use resources first")),
            PromptMessage(
                role="assistant",
                content=EmbeddedResource(
                    type="resource",
                    resource=TextResourceContents(
                        uri="bloodhound://cypher/reference",
                        text="MATCH (n) RETURN n",
                    ),
                ),
            ),
        ]
    )
    assert "[USER]" in prompt_text
    assert "Use resources first" in prompt_text
    assert "MATCH (n) RETURN n" in prompt_text


def test_mcp_conversation_messages_prepends_server_prompt() -> None:
    messages = _mcp_conversation_messages(_task(), server_prompt_text="Load bloodhound resources.")
    assert messages[0].role == "system"
    assert BLOODHOUND_PROMPT_NAME in messages[0].text
    assert "Load bloodhound resources." in messages[0].text
    assert messages[1].role == "system"
    assert messages[2].role == "user"


def test_trajectory_tracks_resource_reads_from_tool_calls() -> None:
    messages = [
        ChatMessageAssistant(
            content="",
            tool_calls=[
                {
                    "id": "tool-1",
                    "function": "read_bloodhound_resource",
                    "arguments": {"uri": "bloodhound://cypher/reference"},
                }
            ],
        ),
        ChatMessageTool(
            content="URI: bloodhound://cypher/reference\nMATCH (n) RETURN n",
            function="read_bloodhound_resource",
            tool_call_id="tool-1",
        ),
    ]
    meta = _trajectory_from_messages(messages, final_answer_raw="{}")
    assert meta.tool_calls_total == 1
    assert meta.resource_reads_total == 1
    assert meta.unique_resources_used == ["bloodhound://cypher/reference"]
    assert meta.resource_characters_total > 0


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
            server_prompt_used=True,
            server_prompt_name=BLOODHOUND_PROMPT_NAME,
            resource_mode=RESOURCE_MODE_ON_DEMAND,
            resource_reads_total=1,
            unique_resources_used=["bloodhound://cypher/reference"],
            resource_characters_total=54,
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
    assert row["server_prompt_used"] == "True"
    assert row["server_prompt_name"] == BLOODHOUND_PROMPT_NAME
    assert row["resource_mode"] == RESOURCE_MODE_ON_DEMAND
    assert row["resource_reads_total"] == "1"
    assert row["unique_resources_used"] == "bloodhound://cypher/reference"
    with summary.open() as f:
        summary_row = next(csv.DictReader(f))
    assert summary_row["avg_tool_calls"] == "3.0"
    assert summary_row["non_cypher_tool_calls"] == "2"
    assert summary_row["avg_resource_reads"] == "1.0"
    assert summary_row["server_prompt_used"] == "True"


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
        [
            "smoke-mcp",
            "-m",
            str(manifest_path),
            "-o",
            str(tmp_path / "smoke"),
            "--resource-mode",
            "on-demand",
        ],
    )
    assert result.exit_code == 0
    assert "SMOKE TEST: PASS" in result.output
