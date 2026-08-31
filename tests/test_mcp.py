from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from inspect_ai.model import ChatMessageAssistant, ChatMessageTool
from inspect_ai.tool import ToolCall, ToolError, tool
from inspect_ai.tool._tool_def import ToolDef

from ori.cli import main
from ori.eval.adapter import ModelResponse
from ori.eval.bhce import BHHealthResult, CypherResult
from ori.eval.grader import GradeResult, grade_mcp
from ori.eval.inspect_runtime import _model_response_to_dict
from ori.eval.mcp_runtime import (
    RESOURCE_LIST_TOOL_NAME,
    RESOURCE_READ_TOOL_NAME,
    MCPRunMetadata,
    _cypher_result_to_dict,
    _discover_bloodhound_mcp_prompt,
    _mcp_metadata_to_dict,
    _mcp_subprocess_env,
    _normalize_final_answer,
    _ollama_chat_turn,
    _ollama_tool_spec,
    _openai_compat_chat_turn,
    _openai_compat_provider_metrics,
    _parse_json_object,
    _resource_tools,
    _result_from_sample,
    _run_ollama_mcp_loop,
    _run_openai_compat_mcp_loop,
    _task_to_dict,
    _trajectory_from_messages,
    _wrap_read_only_tool,
    ori_mcp_scorer,
    ori_mcp_solver,
    run_mcp_eval_with_inspect,
)
from ori.eval.provider_contract import ProviderAuthenticationError, ProviderProtocolError
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
                "verification_cypher": "MATCH p=(u:User {name: 'JDOE@TEST.LOCAL'})-[*1..]->(c:Computer {name: 'DC01.TEST.LOCAL'}) RETURN p",  # noqa: E501
                "path_edges": [],
            },
            {
                "template_id": "t1_has_session",
                "tier": 1,
                "description": "session path",
                "source_name": "WS-01.TEST.LOCAL",
                "target_name": "JDOE@TEST.LOCAL",
                "verification_cypher": "MATCH p=(c:Computer {name: 'WS-01.TEST.LOCAL'})-[:HasSession]->(u:User {name: 'JDOE@TEST.LOCAL'}) RETURN p",  # noqa: E501
                "path_edges": [],
            },
            {
                "template_id": "t1_group_membership",
                "tier": 1,
                "description": "membership path",
                "source_name": "MMOORE@TEST.LOCAL",
                "target_name": "DOMAIN ADMINS@TEST.LOCAL",
                "verification_cypher": "MATCH p=(u:User {name: 'MMOORE@TEST.LOCAL'})-[:MemberOf*1..]->(g:Group {name: 'DOMAIN ADMINS@TEST.LOCAL'}) RETURN p",  # noqa: E501
                "path_edges": [],
            },
            {
                "template_id": "t2_nested_groups",
                "tier": 2,
                "description": "nested path",
                "source_name": "CDAVIS@TEST.LOCAL",
                "target_name": "SRV-01.TEST.LOCAL",
                "verification_cypher": "MATCH p=(u:User {name: 'CDAVIS@TEST.LOCAL'})-[:MemberOf*1..]->(:Group)-[:AdminTo]->(c:Computer {name: 'SRV-01.TEST.LOCAL'}) RETURN p",  # noqa: E501
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
        reference_cypher="MATCH (g:Group {name: 'DOMAIN ADMINS@TEST.LOCAL'})-[:AdminTo]->(c:Computer) RETURN c",  # noqa: E501
        grade_mode="node_set",
        metadata={"domain": "TEST.LOCAL", "mcp_track": "mcp_non_cypher_analysis"},
    )


class _FakeBHCE:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def wait_until_healthy(
        self,
        timeout_seconds: float = 60.0,
        poll_interval: float = 5.0,
    ) -> BHHealthResult:
        return BHHealthResult(ok=True, detail="ok", query="RETURN 1", classification="ok")

    async def get_all_node_names(self) -> set[str]:
        return {"DOMAIN ADMINS@TEST.LOCAL", "WS-01.TEST.LOCAL"}

    async def run_cypher_resilient(self, query: str) -> CypherResult:
        return CypherResult(
            success=True,
            nodes=[{"name": "WS-01.TEST.LOCAL"}],
            node_names={"WS-01.TEST.LOCAL"},
            raw={"query": query},
        )


def test_generate_mcp_tasks_includes_both_tracks() -> None:
    tasks = generate_mcp_tasks(_manifest())
    tracks = {task.metadata.get("mcp_track") for task in tasks}
    assert "mcp_cypher_assisted" in tracks
    assert "mcp_non_cypher_analysis" in tracks
    assert any(task.id == "mcp-global-admin-to" for task in tasks)
    assert any(task.id == "mcp-shortest-path-admin-to" for task in tasks)


def test_mcp_subprocess_env_allows_only_bloodhound_connection_fields(monkeypatch) -> None:
    monkeypatch.setenv("VIRTUAL_ENV", "/tmp/fake-venv")
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("UNRELATED_SECRET", "never-forward-this")
    monkeypatch.setenv("BLOODHOUND_DOMAIN", "bh.example")
    monkeypatch.setenv("BLOODHOUND_TOKEN_KEY", "credential-sentinel")
    env = _mcp_subprocess_env()
    assert env == {
        "BLOODHOUND_DOMAIN": "bh.example",
        "BLOODHOUND_TOKEN_KEY": "credential-sentinel",
    }
    assert "never-forward-this" not in repr(env)


def test_parse_and_normalize_final_answer_handles_fenced_json() -> None:
    raw = '```json\n{"answer_type":"node_set","node_names":["A","B"]}\n```'
    parsed = _parse_json_object(raw)
    normalized = _normalize_final_answer(parsed, _task())
    assert normalized == {"answer_type": "node_set", "node_names": ["A", "B"]}


def test_normalize_final_answer_preserves_declared_relationship_evidence() -> None:
    task = _task()
    task.grade_mode = "path_exists"
    task.metadata["supporting_edges"] = [{"source": "A", "edge": "TrustedBy", "target": "B"}]
    normalized = _normalize_final_answer(
        {
            "answer_type": "path_exists",
            "path_found": True,
            "node_names": ["A", "B"],
            "relationships": [{"source": "A", "edge": "TrustedBy", "target": "B"}],
        },
        task,
    )

    assert normalized == {
        "answer_type": "path_exists",
        "path_found": True,
        "node_names": ["A", "B"],
        "relationships": [{"source": "A", "edge": "TrustedBy", "target": "B"}],
        "mechanisms": [],
    }


def test_run_mcp_eval_with_inspect_mock_perfect_exercises_solver(tmp_path: Path) -> None:
    import asyncio

    results = asyncio.run(
        run_mcp_eval_with_inspect(
            [_task()],
            "mock/mcp_perfect",
            _FakeBHCE(),
            tmp_path / "mock-perfect.csv",
            log_dir=tmp_path / "logs",
        )
    )

    assert len(results) == 1
    result = results[0]
    assert result.grade.outcome == "CORRECT"
    assert result.grade.score == 1.0
    assert result.model_response.model == "mock/mcp_perfect"
    assert result.model_response.parse_stage == "mcp_final_answer"
    assert result.mcp is not None
    assert result.mcp.final_answer_normalized == {
        "answer_type": "node_set",
        "node_names": ["WS-01.TEST.LOCAL"],
    }
    assert result.mcp.final_answer_raw == result.model_response.raw_text
    assert result.mcp.tool_calls_total == 0


def test_run_smoke_mcp_eval_exercises_actual_runtime(tmp_path: Path, monkeypatch) -> None:
    import asyncio

    from ori.eval.ops import run_smoke_mcp_eval

    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(_manifest()))
    monkeypatch.setattr("ori.eval.runner.BHCEClient", lambda **kwargs: _FakeBHCE())
    monkeypatch.setattr("ori.eval.runner.generate_mcp_tasks", lambda manifest: [_task()])

    result = asyncio.run(run_smoke_mcp_eval(manifest_path, tmp_path / "smoke"))

    assert result.ok
    assert {check.model: check.actual_counts for check in result.checks} == {
        "mock/mcp_perfect": {"CORRECT": 1},
        "mock/mcp_wrong": {"INCORRECT": 1},
        "mock/mcp_empty": {"PARSE_FAIL": 1},
    }
    assert all(len(results) == 1 for results in result.results_by_model.values())


def test_ollama_chat_turn_streams_payload_options_and_tool_calls(monkeypatch) -> None:
    import asyncio

    captured: dict[str, object] = {}
    lines = [
        json.dumps({"model": "ori-test", "message": {"thinking": "think ", "content": "part1"}}),
        json.dumps(
            {
                "model": "ori-test",
                "message": {
                    "thinking": "again",
                    "content": " part2",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "function": {
                                "name": "group_info",
                                "arguments": {"group_name": "DOMAIN ADMINS@TEST.LOCAL"},
                            },
                        }
                    ],
                },
            }
        ),
        json.dumps({"done": True, "prompt_eval_count": 11, "eval_count": 7}),
    ]

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        async def aiter_lines(self):
            for line in lines:
                yield line

    class FakeStream:
        async def __aenter__(self):
            return FakeResponse()

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            captured["timeout"] = kwargs.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        def stream(self, method: str, url: str, json: dict):
            captured["method"] = method
            captured["url"] = url
            captured["payload"] = json
            return FakeStream()

    monkeypatch.setattr("ori.eval.mcp_runtime.httpx.AsyncClient", FakeAsyncClient)

    turn = asyncio.run(
        _ollama_chat_turn(
            url="http://127.0.0.1:11434/api/chat",
            model_name="ollama/ori-test",
            messages=[{"role": "user", "content": "q"}],
            tools=[{"type": "function", "function": {"name": "group_info"}}],
            ollama_options={"num_ctx": 32768, "temperature": 0},
        )
    )

    assert captured["method"] == "POST"
    assert captured["url"] == "http://127.0.0.1:11434/api/chat"
    assert captured["timeout"].read == 900.0
    payload = captured["payload"]
    assert payload["model"] == "ori-test"
    assert payload["stream"] is True
    assert payload["options"] == {"num_ctx": 32768, "temperature": 0}
    assert turn["thinking"] == "think again"
    assert turn["content"] == "part1 part2"
    assert turn["tool_calls"][0]["function"]["name"] == "group_info"
    assert turn["prompt_eval_count"] == 11
    assert turn["eval_count"] == 7


def test_openai_compat_chat_turn_posts_tool_payload(monkeypatch) -> None:
    import asyncio

    captured: dict[str, object] = {}
    monkeypatch.delenv("OPENAI_COMPAT_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("NOUS_API_KEY", "nous-key")

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "id": "chatcmpl-nous-1",
                "model": "ori-test",
                "system_fingerprint": "fp-local",
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "content": None,
                            "reasoning_content": "inspect group",
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "group_info",
                                        "arguments": '{"group_name":"DOMAIN ADMINS@TEST.LOCAL"}',
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {"prompt_tokens": 13, "completion_tokens": 5, "total_tokens": 18},
            }

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            captured["timeout"] = kwargs.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, url: str, json: dict, headers: dict):
            captured["url"] = url
            captured["payload"] = json
            captured["headers"] = headers
            return FakeResponse()

    monkeypatch.setattr("ori.eval.mcp_runtime.httpx.AsyncClient", FakeAsyncClient)

    turn = asyncio.run(
        _openai_compat_chat_turn(
            url="https://inference-api.nousresearch.com/v1/chat/completions",
            model_name="openai-compat/openai/gpt-5.5",
            messages=[{"role": "user", "content": "q"}],
            tools=[{"type": "function", "function": {"name": "group_info"}}],
            max_tokens=8192,
            extra_body={"temperature": 0},
            telemetry_adapter="llama-cpp",
        )
    )

    assert captured["url"] == "https://inference-api.nousresearch.com/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer nous-key"
    payload = captured["payload"]
    assert payload["model"] == "openai/gpt-5.5"
    assert payload["stream"] is False
    assert payload["tool_choice"] == "auto"
    assert payload["max_tokens"] == 8192
    assert payload["temperature"] == 0
    assert turn["content"] == ""
    assert turn["thinking"] == "inspect group"
    assert turn["finish_reason"] == "tool_calls"
    assert turn["provider_metrics"]["telemetry_adapter"] == "llama-cpp"
    assert turn["provider_metrics"]["system_fingerprint"] == "fp-local"
    assert turn["provider_metrics"]["status"] == "tool_calls"
    assert turn["provider_metrics"]["refusal"] == ""
    assert turn["provider_metrics"]["usage_reported"] is True
    assert turn["provider_metrics"]["usage_complete"] is True
    assert turn["provider_metrics"]["response_id"] == "chatcmpl-nous-1"
    assert turn["provider_metrics"]["api_surface"] == "chat_completions"
    assert turn["provider_metrics"]["endpoint_family"] == "nous"
    assert turn["provider_metrics"]["credential_source"] == "NOUS_API_KEY"
    assert turn["tool_calls"][0]["function"]["name"] == "group_info"
    assert turn["tool_calls"][0]["argument_parse_status"] == "valid"
    assert turn["tool_calls"][0]["parsed_arguments"] == {
        "group_name": "DOMAIN ADMINS@TEST.LOCAL"
    }
    assert turn["prompt_tokens"] == 13
    assert turn["completion_tokens"] == 5


@pytest.mark.parametrize(
    ("envelope", "detail"),
    [
        ({}, "missing choices"),
        ({"choices": [{}]}, "missing its message"),
    ],
)
def test_openai_compat_chat_turn_rejects_malformed_envelopes(
    monkeypatch, envelope: dict, detail: str
) -> None:
    import asyncio

    monkeypatch.setenv("OPENAI_COMPAT_API_KEY", "compat-key")

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return envelope

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, url: str, json: dict, headers: dict):
            return FakeResponse()

    monkeypatch.setattr("ori.eval.mcp_runtime.httpx.AsyncClient", FakeAsyncClient)

    with pytest.raises(ProviderProtocolError, match=detail):
        asyncio.run(
            _openai_compat_chat_turn(
                url="https://generic.example/v1/chat/completions",
                model_name="openai-compat/test-model",
                messages=[{"role": "user", "content": "q"}],
                tools=[],
            )
        )


def test_openai_compat_chat_turn_rejects_missing_remote_credential(monkeypatch) -> None:
    import asyncio

    for name in (
        "OPENAI_COMPAT_API_KEY",
        "OPENROUTER_API_KEY",
        "NOUS_API_KEY",
        "NOUS_PORTAL_API_KEY",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ProviderAuthenticationError, match="requires a credential"):
        asyncio.run(
            _openai_compat_chat_turn(
                url="https://inference-api.nousresearch.com/v1/chat/completions",
                model_name="openai-compat/test-model",
                messages=[{"role": "user", "content": "q"}],
                tools=[],
            )
        )


@pytest.mark.parametrize(
    ("finish_reason", "subtype"),
    [("length", "TRUNCATED"), ("content_filter", "CONTENT_FILTERED")],
)
def test_openai_compat_chat_turn_suppresses_terminal_partial_tool_calls(
    monkeypatch,
    finish_reason: str,
    subtype: str,
) -> None:
    import asyncio

    monkeypatch.setenv("OPENAI_COMPAT_API_KEY", "compat-key")

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "choices": [
                    {
                        "finish_reason": finish_reason,
                        "message": {
                            "content": "partial",
                            "tool_calls": [
                                {
                                    "id": "partial-call",
                                    "function": {
                                        "name": "cypher_query.run",
                                        "arguments": '{"query":"MATCH (n) RETURN n"}',
                                    },
                                }
                            ],
                        },
                    }
                ]
            }

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, url: str, json: dict, headers: dict):
            return FakeResponse()

    monkeypatch.setattr("ori.eval.mcp_runtime.httpx.AsyncClient", FakeAsyncClient)

    turn = asyncio.run(
        _openai_compat_chat_turn(
            url="https://generic.example/v1/chat/completions",
            model_name="openai-compat/test-model",
            messages=[{"role": "user", "content": "q"}],
            tools=[],
        )
    )

    assert turn["content"] == ""
    assert turn["tool_calls"] == []
    assert turn["provider_metrics"]["model_output_error"] is True
    assert turn["provider_metrics"]["model_output_subtype"] == subtype


def test_openai_compat_provider_metrics_extracts_backend_specific_fields() -> None:
    llama_metrics = _openai_compat_provider_metrics(
        data={
            "id": "cmpl-1",
            "model": "qwen",
            "timings": {"predicted_per_second": 42.0},
            "tokens_cached": 100,
            "tokens_evaluated": 120,
            "truncated": False,
        },
        choice={"finish_reason": "stop"},
        message={"content": "ok"},
        usage={"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
        telemetry_adapter="llama-cpp",
    )
    assert llama_metrics["llama_cpp_timings"] == {"predicted_per_second": 42.0}
    assert llama_metrics["llama_cpp_tokens_cached"] == 100
    assert llama_metrics["llama_cpp_tokens_evaluated"] == 120

    mlx_metrics = _openai_compat_provider_metrics(
        data={},
        choice={"finish_reason": "stop", "logprobs": {"tokens": [1, 2]}},
        message={"reasoning_content": "reason"},
        usage={},
        telemetry_adapter="mlx-lm",
    )
    assert mlx_metrics["mlx_lm_logprobs"] == {"tokens": [1, 2]}
    assert mlx_metrics["reasoning_source"] == "structured_field"

    vllm_metrics = _openai_compat_provider_metrics(
        data={"request_id": "req-1"},
        choice={},
        message={},
        usage={},
        telemetry_adapter="vllm",
    )
    assert vllm_metrics["vllm_request_id"] == "req-1"

    lm_studio_metrics = _openai_compat_provider_metrics(
        data={"stats": {"tokens_per_second": 100.0}},
        choice={},
        message={},
        usage={},
        telemetry_adapter="lm-studio",
    )
    assert lm_studio_metrics["lm_studio_stats"] == {"tokens_per_second": 100.0}


def test_ollama_tool_spec_accepts_executor_callable_without_calling_it() -> None:
    import asyncio

    @tool(name="group_info")
    def group_info():
        async def execute(group_name: str, info_type: str) -> str:
            return f"{group_name}:{info_type}"

        return execute

    executor_tool = group_info()
    spec, executor = _ollama_tool_spec(executor_tool)

    assert executor is executor_tool
    assert spec["function"]["name"] == "group_info"
    assert set(spec["function"]["parameters"]["properties"]) == {"group_name", "info_type"}
    assert asyncio.run(executor(group_name="DOMAIN ADMINS@TEST.LOCAL", info_type="members")) == (
        "DOMAIN ADMINS@TEST.LOCAL:members"
    )


def test_openai_compatible_tool_spec_omits_null_schema_keywords() -> None:
    @tool(name="domain_info")
    def domain_info():
        async def execute(info_type: str = "list", domain_id: str | None = None) -> str:
            return f"{info_type}:{domain_id}"

        return execute

    spec, _executor = _ollama_tool_spec(domain_info())

    def assert_no_nulls(value: object) -> None:
        if isinstance(value, dict):
            assert all(item is not None for item in value.values())
            for item in value.values():
                assert_no_nulls(item)
        elif isinstance(value, list):
            for item in value:
                assert_no_nulls(item)

    parameters = spec["function"]["parameters"]
    assert_no_nulls(parameters)
    assert parameters["properties"]["info_type"]["default"] == "list"


def test_read_only_wrapper_preserves_allowed_calls_and_blocks_mutation_modes() -> None:
    import asyncio

    @tool(name="domain_info")
    def domain_info():
        async def execute(info_type: str) -> str:
            return info_type

        return execute

    wrapped = _wrap_read_only_tool(domain_info())
    _, executor = _ollama_tool_spec(wrapped)

    assert asyncio.run(executor(info_type="list")) == "list"
    with pytest.raises(ToolError, match="POLICY_VIOLATION"):
        asyncio.run(executor(info_type="delete"))


def test_resource_tools_construct_read_tool_without_uri() -> None:
    class FakeServer:
        pass

    tools = _resource_tools(FakeServer())
    tool_defs = [ToolDef(tool_obj) for tool_obj in tools]
    assert {tool_def.name for tool_def in tool_defs} == {
        RESOURCE_LIST_TOOL_NAME,
        RESOURCE_READ_TOOL_NAME,
    }
    assert all(tool_def.description for tool_def in tool_defs)

    specs = [_ollama_tool_spec(tool_def.tool)[0] for tool_def in tool_defs]
    specs_by_name = {spec["function"]["name"]: spec for spec in specs}

    assert set(specs_by_name) == {RESOURCE_LIST_TOOL_NAME, RESOURCE_READ_TOOL_NAME}
    read_params = specs_by_name[RESOURCE_READ_TOOL_NAME]["function"]["parameters"]
    assert set(read_params["properties"]) == {"uri"}
    assert read_params["required"] == ["uri"]
    assert read_params["properties"]["uri"]["description"] == "BloodHound MCP resource URI to read."


def test_prompt_discovery_selects_best_bloodhound_prompt() -> None:
    import asyncio
    from types import SimpleNamespace

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def list_prompts(self):
            return SimpleNamespace(
                prompts=[
                    SimpleNamespace(name="generic_helper"),
                    SimpleNamespace(name="bloodhound_assistant"),
                ]
            )

        async def get_prompt(self, name: str):
            assert name == "bloodhound_assistant"
            return SimpleNamespace(
                messages=[
                    SimpleNamespace(
                        role="system",
                        content=SimpleNamespace(text="Use BloodHound safely."),
                    )
                ]
            )

    class FakeHandle:
        def _client_session(self):
            return FakeSession()

    class FakeServer:
        def _task_session(self):
            return FakeHandle()

    text, selected, available, status = asyncio.run(_discover_bloodhound_mcp_prompt(FakeServer()))
    assert selected == "bloodhound_assistant"
    assert available == ["bloodhound_assistant", "generic_helper"]
    assert status == "selected"
    assert "Use BloodHound safely." in text


def test_prompt_discovery_warns_and_continues_without_prompts(capsys) -> None:
    import asyncio
    from types import SimpleNamespace

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def list_prompts(self):
            return SimpleNamespace(prompts=[])

    class FakeHandle:
        def _client_session(self):
            return FakeSession()

    class FakeServer:
        def _task_session(self):
            return FakeHandle()

    text, selected, available, status = asyncio.run(_discover_bloodhound_mcp_prompt(FakeServer()))
    assert (text, selected, available, status) == ("", "", [], "no_prompts")
    assert "exposed no prompts" in capsys.readouterr().out


def test_result_from_sample_missing_model_response_returns_infra_error() -> None:
    from types import SimpleNamespace

    ref_result = CypherResult(
        success=True,
        nodes=[{"name": "WS-01.TEST.LOCAL"}],
        node_names={"WS-01.TEST.LOCAL"},
        raw={},
    )
    sample = SimpleNamespace(
        id="mcp-test",
        uuid="sample-uuid",
        metadata={
            "ori_task": _task_to_dict(_task()),
            "ref_result": _cypher_result_to_dict(ref_result),
            "requested_model": "ollama/gemma4-e4b-32k",
        },
        store={},
        scores=None,
        error=TimeoutError("ReadTimeout"),
        error_retries=[object()],
    )
    log = SimpleNamespace(location="trace.eval")

    result = _result_from_sample(sample, log)

    assert result.grade.outcome == "INFRA_ERROR"
    assert result.model_response.model == "ollama/gemma4-e4b-32k"
    assert result.model_response.error == "ReadTimeout"
    assert result.inspect is not None
    assert result.inspect.error_retries == 1
    assert result.mcp is not None
    assert result.mcp.trajectory_log == "trace.eval"
    assert result.mcp.infra_error_subtype == "missing_model_response"


def _score_state(store: dict) -> object:
    from types import SimpleNamespace

    ref_result = CypherResult(
        success=True,
        nodes=[{"name": "WS-01.TEST.LOCAL"}],
        node_names={"WS-01.TEST.LOCAL"},
        raw={},
    )
    return SimpleNamespace(
        metadata={
            "ori_task": _task_to_dict(_task()),
            "ref_result": _cypher_result_to_dict(ref_result),
            "valid_node_names": ["WS-01.TEST.LOCAL"],
            "requested_model": "ollama/ori-qwen35-9b-64k",
            "sample_index": 1,
            "sample_total": 43,
        },
        store=store,
    )


def test_ori_mcp_scorer_missing_model_response_returns_infra_error() -> None:
    import asyncio

    score = asyncio.run(ori_mcp_scorer()(_score_state({}), None))

    assert score.value == 0.0
    assert score.metadata["grade"]["outcome"] == "INFRA_ERROR"
    assert "missing ori_model_response" in score.explanation
    assert score.metadata["mcp"]["infra_error_subtype"] == (
        "missing_model_response_and_mcp_trajectory"
    )


def test_ori_mcp_scorer_missing_mcp_trajectory_returns_infra_error() -> None:
    import asyncio

    response = ModelResponse(
        raw_text="partial",
        cypher=None,
        parse_stage="none",
        tokens_input=0,
        tokens_output=0,
        elapsed_seconds=0.0,
        model="ollama/ori-qwen35-9b-64k",
    )
    score = asyncio.run(
        ori_mcp_scorer()(
            _score_state({"ori_model_response": _model_response_to_dict(response)}),
            None,
        )
    )

    assert score.value == 0.0
    assert score.answer == "partial"
    assert score.metadata["grade"]["outcome"] == "INFRA_ERROR"
    assert "missing ori_mcp_trajectory" in score.explanation
    assert score.metadata["mcp"]["infra_error_subtype"] == "missing_mcp_trajectory"


def test_ori_mcp_scorer_with_both_keys_present_still_grades_normally() -> None:
    import asyncio

    response = ModelResponse(
        raw_text='{"answer_type":"node_set","node_names":["WS-01.TEST.LOCAL"]}',
        cypher=None,
        parse_stage="mcp_final_answer",
        tokens_input=0,
        tokens_output=0,
        elapsed_seconds=0.0,
        model="mock/mcp_perfect",
    )
    trajectory = MCPRunMetadata(
        final_answer_raw=response.raw_text,
        final_answer_normalized={"answer_type": "node_set", "node_names": ["WS-01.TEST.LOCAL"]},
    )

    score = asyncio.run(
        ori_mcp_scorer()(
            _score_state(
                {
                    "ori_model_response": _model_response_to_dict(response),
                    "ori_mcp_trajectory": _mcp_metadata_to_dict(trajectory),
                }
            ),
            None,
        )
    )

    assert score.value == 1.0
    assert score.metadata["grade"]["outcome"] == "CORRECT"


def test_native_ollama_solver_exception_persists_fallback_state(monkeypatch) -> None:
    import asyncio
    from types import SimpleNamespace

    class Store(dict):
        def set(self, key, value):
            self[key] = value

    async def fake_loop(**kwargs):
        raise RuntimeError("native loop died before assistant")

    def fake_use_tools(tools, tool_choice="auto"):
        async def apply(state, generate):
            return state

        return apply

    monkeypatch.setattr("ori.eval.mcp_runtime._run_ollama_mcp_loop", fake_loop)
    monkeypatch.setattr("ori.eval.mcp_runtime.use_tools", fake_use_tools)

    ref_result = CypherResult(
        success=True,
        nodes=[{"name": "WS-01.TEST.LOCAL"}],
        node_names={"WS-01.TEST.LOCAL"},
        raw={},
    )
    state = SimpleNamespace(
        metadata={
            "ori_task": _task_to_dict(_task()),
            "ref_result": _cypher_result_to_dict(ref_result),
            "requested_model": "ollama/ori-qwen35-9b-64k",
        },
        model="ollama/ori-qwen35-9b-64k",
        messages=[],
        store=Store(),
    )
    solver = ori_mcp_solver([], mcp_tool_loop="native-ollama")
    solved = asyncio.run(solver(state, None))

    assert solved.store["ori_model_response"]["error"].startswith(
        "MCP native Ollama solver exception: RuntimeError"
    )
    assert solved.store["ori_mcp_trajectory"]["tool_loop"] == "native-ollama"
    assert solved.store["ori_mcp_trajectory"]["infra_error_subtype"] == "solver_exception"
    score = asyncio.run(ori_mcp_scorer()(solved, None))
    assert score.metadata["grade"]["outcome"] == "INFRA_ERROR"
    assert score.metadata["mcp"]["infra_error_subtype"] == "solver_exception"


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
                            "arguments": {
                                "group_name": "DOMAIN ADMINS@TEST.LOCAL",
                                "info_type": "members",
                            },
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


def test_run_openai_compat_mcp_loop_executes_tool_calls(monkeypatch) -> None:
    import asyncio

    calls = {"count": 0}

    async def fake_turn(**kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return {
                "model": "ori-mlx",
                "content": "",
                "thinking": "look up group members",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "group_info",
                            "arguments": json.dumps(
                                {
                                    "group_name": "DOMAIN ADMINS@TEST.LOCAL",
                                    "info_type": "members",
                                }
                            ),
                        },
                    }
                ],
                "prompt_tokens": 70,
                "completion_tokens": 12,
                "finish_reason": "tool_calls",
                "provider_metrics": {
                    "telemetry_adapter": "mlx-lm",
                    "finish_reason": "tool_calls",
                },
            }
        return {
            "model": "ori-mlx",
            "content": '{"answer_type":"node_set","node_names":["WS-01.TEST.LOCAL"]}',
            "tool_calls": [],
            "prompt_tokens": 80,
            "completion_tokens": 10,
            "finish_reason": "stop",
            "provider_metrics": {"telemetry_adapter": "mlx-lm", "finish_reason": "stop"},
        }

    monkeypatch.setattr("ori.eval.mcp_runtime._openai_compat_chat_turn", fake_turn)

    @tool(name="group_info")
    def group_info():
        async def execute(group_name: str, info_type: str) -> str:
            return '{"success": true, "nodes": ["WS-01.TEST.LOCAL"]}'

        return execute

    response, trajectory, messages = asyncio.run(
        _run_openai_compat_mcp_loop(
            task=_task(),
            model_name="openai-compat/ori-mlx@http://127.0.0.1:8080/v1",
            base_url=None,
            extra_body={"temperature": 0},
            tools=[group_info],
            max_steps=4,
            telemetry_adapter="mlx-lm",
        )
    )

    assert response.error is None
    assert response.parse_stage == "mcp_final_answer"
    assert response.raw_text == '{"answer_type":"node_set","node_names":["WS-01.TEST.LOCAL"]}'
    assert response.tokens_input == 150
    assert response.tokens_output == 22
    assert response.thinking == "look up group members"
    assert response.provider_metrics["provider"] == "openai_compat_native_chat_mcp_loop"
    assert response.provider_metrics["telemetry_adapter"] == "mlx-lm"
    assert response.provider_metrics["finish_reasons"] == ["tool_calls", "stop"]
    assert response.provider_metrics["turn_metrics"][0]["telemetry_adapter"] == "mlx-lm"
    assert trajectory.tool_calls_total == 1
    assert trajectory.tool_loop == "native-openai-compatible"
    assert len(messages) >= 4


def test_run_openai_compat_mcp_loop_rejects_malformed_tool_arguments(monkeypatch) -> None:
    import asyncio

    calls = {"turn": 0, "tool": 0}

    async def fake_turn(**kwargs):
        calls["turn"] += 1
        if calls["turn"] == 1:
            return {
                "model": "ori-test",
                "content": "",
                "thinking": "call the tool",
                "tool_calls": [
                    {
                        "id": "bad-call-1",
                        "type": "function",
                        "function": {
                            "name": "group_info",
                            "arguments": '{"group_name":',
                        },
                        "parsed_arguments": None,
                        "argument_parse_status": "malformed",
                        "argument_parse_error": "invalid JSON tool arguments",
                    }
                ],
                "prompt_tokens": 10,
                "completion_tokens": 4,
                "finish_reason": "tool_calls",
                "provider_metrics": {"status": "tool_calls"},
            }
        return {
            "model": "ori-test",
            "content": '{"answer_type":"node_set","node_names":[]}',
            "thinking": "",
            "tool_calls": [],
            "prompt_tokens": 12,
            "completion_tokens": 5,
            "finish_reason": "stop",
            "provider_metrics": {"status": "completed"},
        }

    monkeypatch.setattr("ori.eval.mcp_runtime._openai_compat_chat_turn", fake_turn)

    @tool(name="group_info")
    def group_info():
        async def execute(group_name: str) -> str:
            calls["tool"] += 1
            return '{"success": true}'

        return execute

    response, trajectory, messages = asyncio.run(
        _run_openai_compat_mcp_loop(
            task=_task(),
            model_name="openai-compat/ori-test@http://127.0.0.1:8080/v1",
            base_url=None,
            extra_body=None,
            tools=[group_info],
            max_steps=3,
        )
    )

    assert response.error is None
    assert calls["tool"] == 0
    assert trajectory.tool_calls_total == 1
    assert trajectory.failed_tool_calls == 1
    tool_messages = [message for message in messages if isinstance(message, ChatMessageTool)]
    assert len(tool_messages) == 1
    assert tool_messages[0].error is not None
    assert tool_messages[0].error.type == "parsing"
    assert "invalid_tool_arguments" in tool_messages[0].text


def test_run_openai_compat_mcp_loop_enforces_total_tool_call_budget(monkeypatch) -> None:
    import asyncio

    executed = {"count": 0}

    async def fake_turn(**kwargs):
        assert kwargs["tools"]
        return {
            "model": "ori-mlx",
            "content": "",
            "thinking": "request two calls in one turn",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "group_info",
                        "arguments": json.dumps(
                            {
                                "group_name": "DOMAIN ADMINS@TEST.LOCAL",
                                "info_type": "members",
                            }
                        ),
                    },
                },
                {
                    "id": "call-2",
                    "type": "function",
                    "function": {
                        "name": "group_info",
                        "arguments": json.dumps(
                            {
                                "group_name": "ENTERPRISE ADMINS@TEST.LOCAL",
                                "info_type": "members",
                            }
                        ),
                    },
                },
            ],
            "prompt_tokens": 70,
            "completion_tokens": 12,
            "finish_reason": "tool_calls",
            "provider_metrics": {
                "telemetry_adapter": "mlx-lm",
                "finish_reason": "tool_calls",
            },
        }

    monkeypatch.setattr("ori.eval.mcp_runtime._openai_compat_chat_turn", fake_turn)

    @tool(name="group_info")
    def group_info():
        async def execute(group_name: str, info_type: str) -> str:
            executed["count"] += 1
            return '{"success": true, "nodes": ["WS-01.TEST.LOCAL"]}'

        return execute

    response, trajectory, messages = asyncio.run(
        _run_openai_compat_mcp_loop(
            task=_task(),
            model_name="openai-compat/ori-mlx@http://127.0.0.1:8080/v1",
            base_url=None,
            extra_body=None,
            tools=[group_info],
            max_steps=1,
            telemetry_adapter="mlx-lm",
        )
    )

    assert response.error == "MCP loop exhausted without final answer"
    assert executed["count"] == 1
    assert trajectory.tool_calls_total == 2
    tool_messages = [message for message in messages if isinstance(message, ChatMessageTool)]
    assert len(tool_messages) == 2
    assert "tool_call_budget_exceeded" in tool_messages[1].text


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


def test_trajectory_counts_resource_reads() -> None:
    trajectory = _trajectory_from_messages(
        [
            ChatMessageAssistant(
                content="",
                tool_calls=[
                    ToolCall(
                        id="resource-call-1",
                        function=RESOURCE_READ_TOOL_NAME,
                        arguments={"uri": "bloodhound://domains/TEST.LOCAL"},
                    )
                ],
            ),
            ChatMessageTool(
                content="resource body",
                tool_call_id="resource-call-1",
                function=RESOURCE_READ_TOOL_NAME,
            ),
        ],
        final_answer_raw='{"answer_type":"node_set","node_names":[]}',
    )

    assert trajectory.tool_calls_total == 1
    assert trajectory.non_cypher_tool_calls == 1
    assert trajectory.resource_reads_total == 1
    assert trajectory.unique_resources_used == ["bloodhound://domains/TEST.LOCAL"]
    assert trajectory.resource_characters_total == len("resource body")


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
            server_prompt_name="bloodhound_assistant",
            available_prompt_names=["bloodhound_assistant", "generic_helper"],
            prompt_discovery_status="selected",
            available_resource_uris=["bloodhound://schema", "bloodhound://domains"],
            resource_discovery_status="listed",
            mcp_launcher="uvx_git",
            mcp_source=(
                "git+https://github.com/mwnickerson/bloodhound_mcp@"
                "cdb17097e761c8a8622cb93bc3ba49a9e150bb6e"
            ),
            mcp_revision="cdb17097e761c8a8622cb93bc3ba49a9e150bb6e",
            mcp_executable="bloodhound-mcp",
            uv_version="uv 0.test",
            resource_mode="on-demand",
            tool_loop="native-openai-compatible",
            resource_reads_total=2,
            unique_resources_used=["bloodhound://domains/TEST.LOCAL"],
            resource_characters_total=1234,
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
    assert row["server_prompt_name"] == "bloodhound_assistant"
    assert row["available_prompt_names"] == "bloodhound_assistant,generic_helper"
    assert row["prompt_discovery_status"] == "selected"
    assert row["prompt_discovery_succeeded"] == "True"
    assert row["available_resource_uris"] == "bloodhound://schema,bloodhound://domains"
    assert row["resource_discovery_status"] == "listed"
    assert row["resource_discovery_succeeded"] == "True"
    assert row["mcp_launcher"] == "uvx_git"
    assert row["mcp_revision"] == "cdb17097e761c8a8622cb93bc3ba49a9e150bb6e"
    assert row["mcp_executable"] == "bloodhound-mcp"
    assert row["uv_version"] == "uv 0.test"
    assert row["resource_mode"] == "on-demand"
    assert row["mcp_tool_loop"] == "native-openai-compatible"
    assert row["resource_reads_total"] == "2"
    assert row["unique_resources_used"] == "bloodhound://domains/TEST.LOCAL"
    assert row["resource_characters_total"] == "1234"
    with summary.open() as f:
        summary_row = next(csv.DictReader(f))
    assert summary_row["avg_tool_calls"] == "3.0"
    assert summary_row["non_cypher_tool_calls"] == "2"
    assert summary_row["mcp_launcher"] == "uvx_git"
    assert summary_row["prompt_discovery_succeeded"] == "True"
    assert summary_row["resource_discovery_succeeded"] == "True"


def test_cli_smoke_mcp(tmp_path: Path, monkeypatch) -> None:
    from ori.eval.ops import SmokeCheck, SmokeEvalResult

    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(_manifest()))

    async def fake_smoke_mcp_eval(
        manifest_path: Path,
        output_dir: Path,
        bhce_url: str | None = None,
        mcp_dir: Path | None = None,
        mcp_launcher=None,
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
