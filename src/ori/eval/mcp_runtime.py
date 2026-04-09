"""Inspect AI-backed runtime for Phase 3B MCP evaluation."""

from __future__ import annotations

import inspect
import json
import os
from dataclasses import asdict, dataclass, field
from functools import wraps
from pathlib import Path
from typing import Any

from mcp.types import EmbeddedResource, PromptMessage, ResourceLink, TextContent
from inspect_ai import Task as InspectTask
from inspect_ai import eval_async as inspect_eval_async
from inspect_ai._util.registry import registry_info
from inspect_ai.dataset import Sample
from inspect_ai.log import EvalLog, EvalSample
from inspect_ai.model import (
    ChatMessageAssistant,
    ChatMessageSystem,
    ChatMessageTool,
    ChatMessageUser,
    ModelOutput,
)
from inspect_ai.scorer import Score, accuracy, scorer, stderr
from inspect_ai.solver import Generate, TaskState, solver, use_tools
from inspect_ai.tool import ToolError, mcp_server_stdio, mcp_tools, tool

from .adapter import ModelResponse
from .bhce import BHCEClient, CypherResult
from .grader import grade_mcp
from .inspect_runtime import (
    InspectEvalMetadata,
    _configure_inspect_runtime_dirs,
    _cypher_result_from_dict,
    _cypher_result_to_dict,
    _grade_result_to_dict,
    _inspect_supported_model,
    _log_dir_for_output,
    _model_response_from_dict,
    _model_response_to_dict,
    _resolve_model_base_url,
    _score_metadata_to_grade_result,
    _task_from_dict,
    _task_name_for_model,
    _task_to_dict,
)
from .tasks import Task

RESOURCE_MODE_OFF = "off"
RESOURCE_MODE_ON_DEMAND = "on-demand"
BLOODHOUND_PROMPT_NAME = "bloodhound_assistant"
RESOURCE_LIST_TOOL_NAME = "list_bloodhound_resources"
RESOURCE_READ_TOOL_NAME = "read_bloodhound_resource"


@dataclass
class MCPServerBundle:
    tools: list[Any]
    server_prompt_text: str = ""
    server_prompt_name: str = ""


@dataclass
class MCPRunMetadata:
    final_answer_raw: str = ""
    final_answer_normalized: dict[str, Any] | None = None
    tool_calls_total: int = 0
    failed_tool_calls: int = 0
    unique_tools_used: list[str] = field(default_factory=list)
    cypher_query_calls: int = 0
    non_cypher_tool_calls: int = 0
    agent_turns: int = 0
    attempted_policy_violations: int = 0
    trajectory_log: str = ""
    infra_tool_errors: int = 0
    server_prompt_used: bool = False
    server_prompt_name: str = ""
    resource_mode: str = RESOURCE_MODE_OFF
    resource_reads_total: int = 0
    unique_resources_used: list[str] = field(default_factory=list)
    resource_characters_total: int = 0

    @property
    def final_answer_normalized_json(self) -> str:
        if self.final_answer_normalized is None:
            return ""
        return json.dumps(self.final_answer_normalized, sort_keys=True)


_READ_ONLY_MCP_INFO_TYPES: dict[str, set[str]] = {
    "domain_info": {
        "list", "search", "users", "groups", "computers", "controllers",
        "gpos", "ous", "dc_syncers", "foreign_admins", "foreign_gpo_controllers",
        "foreign_groups", "foreign_users", "inbound_trusts", "outbound_trusts",
    },
    "user_info": {
        "info", "admin_rights", "constrained_delegation", "controllables",
        "controllers", "dcom_rights", "memberships", "ps_remote_rights",
        "rdp_rights", "sessions", "sql_admin_rights",
    },
    "group_info": {
        "info", "admin_rights", "controllables", "controllers", "dcom_rights",
        "members", "memberships", "ps_remote_rights", "rdp_rights", "sessions",
    },
    "computer_info": {
        "info", "admin_rights", "admin_users", "constrained_delegation",
        "constrained_users", "controllables", "controllers", "dcom_rights",
        "dcom_users", "group_membership", "ps_remote_rights", "ps_remote_users",
        "rdp_rights", "rdp_users", "sessions", "sql_admins",
    },
    "ou_info": {"info", "computers", "groups", "gpos", "users"},
    "gpo_info": {"info", "computers", "controllers", "ous", "tier_zeros", "users"},
    "graph_analysis": {"search", "shortest_path", "edge_composition", "relay_targets"},
    "adcs_info": {
        "cert_template_info", "cert_template_controllers", "root_ca_info",
        "root_ca_controllers", "enterprise_ca_info", "enterprise_ca_controllers",
        "aia_ca_controllers",
    },
    "cypher_query": {"run", "interpret", "list_saved", "get_saved", "validate"},
    "data_quality": {"completeness", "ad_domain", "azure_tenant", "platform"},
}


def _canonical_tool_name(tool_obj: Any) -> str:
    try:
        return registry_info(tool_obj).name.rsplit("/", 1)[-1]
    except Exception:
        return getattr(tool_obj, "__name__", "unknown")


def _mcp_subprocess_env() -> dict[str, str]:
    env = dict(os.environ)
    # Avoid noisy uv warnings when the parent ORI process is already inside a
    # different virtualenv than the MCP project being launched via `uv run`.
    env.pop("VIRTUAL_ENV", None)
    env.setdefault("UV_CACHE_DIR", str(Path.cwd() / ".uv-cache"))
    return env


def _tool_doc(tool_obj: Any, name: str, allowed: set[str]) -> str:
    original = inspect.getdoc(tool_obj) or f"Read-only wrapper for {name}"
    if "Allowed info_type values:" in original:
        return original
    allowed_text = ", ".join(sorted(allowed))
    return f"{original}\n\nAllowed info_type values: {allowed_text}."


def _wrap_read_only_tool(tool_obj: Any) -> Any:
    tool_name = _canonical_tool_name(tool_obj)
    allowed = _READ_ONLY_MCP_INFO_TYPES[tool_name]

    @tool(name=tool_name)
    def wrapped_tool() -> Any:
        @wraps(tool_obj)
        async def execute(*args: Any, **kwargs: Any) -> Any:
            info_type = kwargs.get("info_type")
            if info_type is not None and info_type not in allowed:
                raise ToolError(
                    f"POLICY_VIOLATION: {tool_name}.{info_type} is not allowed in ORI eval mode"
                )
            result = tool_obj(*args, **kwargs)
            if inspect.isawaitable(result):
                return await result
            return result

        execute.__doc__ = _tool_doc(tool_obj, tool_name, allowed)
        return execute

    return wrapped_tool()


def _create_bloodhound_mcp_server(mcp_dir: Path) -> Any:
    return mcp_server_stdio(
        command="uv",
        args=["--directory", str(mcp_dir), "run", "main.py"],
        cwd=str(mcp_dir),
        env=_mcp_subprocess_env(),
    )


def _prompt_content_to_text(content: Any) -> str:
    if isinstance(content, TextContent):
        return content.text
    if isinstance(content, ResourceLink):
        title = content.title or content.name
        description = f" — {content.description}" if content.description else ""
        return f"Resource link: {title} ({content.uri}){description}"
    if isinstance(content, EmbeddedResource):
        resource = content.resource
        text = getattr(resource, "text", None)
        if text is not None:
            return text
        blob = getattr(resource, "blob", None)
        if blob is not None:
            return f"[binary resource: {resource.uri}]"
    return str(content)


def _prompt_messages_to_text(messages: list[PromptMessage]) -> str:
    rendered: list[str] = []
    for message in messages:
        content = _prompt_content_to_text(message.content).strip()
        if content:
            rendered.append(f"[{message.role.upper()}]\n{content}")
    return "\n\n".join(rendered).strip()


async def _load_bloodhound_mcp_prompt(server: Any, prompt_name: str) -> str:
    session_handle = server._task_session()
    async with session_handle._client_session() as session:
        prompt = await session.get_prompt(prompt_name)
    return _prompt_messages_to_text(prompt.messages)


def _format_resource_list(resources: list[Any]) -> str:
    lines = ["Available BloodHound MCP resources:"]
    for resource in resources:
        title = f" ({resource.title})" if getattr(resource, "title", None) else ""
        description = f" — {resource.description}" if getattr(resource, "description", None) else ""
        lines.append(f"- {resource.uri}{title}{description}")
    return "\n".join(lines)


def _format_resource_contents(contents: list[Any]) -> str:
    chunks: list[str] = []
    for item in contents:
        if hasattr(item, "text"):
            chunks.append(f"URI: {item.uri}\n{item.text}")
        elif hasattr(item, "blob"):
            chunks.append(f"URI: {item.uri}\n[binary resource content omitted]")
        else:
            chunks.append(str(item))
    return "\n\n".join(chunks).strip()


def _resource_tools(server: Any) -> list[Any]:
    @tool(name=RESOURCE_LIST_TOOL_NAME)
    def list_bloodhound_resources() -> Any:
        """List available BloodHound MCP reference resources."""

        async def execute() -> str:
            session_handle = server._task_session()
            async with session_handle._client_session() as session:
                result = await session.list_resources()
            return _format_resource_list(result.resources)

        return execute

    @tool(name=RESOURCE_READ_TOOL_NAME)
    def read_bloodhound_resource(uri: str) -> Any:
        """Read a BloodHound MCP reference resource by URI."""

        async def execute(uri: str) -> str:
            print(f"           → resource read: {uri}")
            session_handle = server._task_session()
            async with session_handle._client_session() as session:
                result = await session.read_resource(uri)
            return _format_resource_contents(result.contents)

        return execute

    return [list_bloodhound_resources(), read_bloodhound_resource()]


async def _load_bloodhound_mcp_bundle(
    mcp_dir: Path,
    *,
    include_resources: bool,
    include_prompt: bool,
) -> MCPServerBundle:
    server = _create_bloodhound_mcp_server(mcp_dir)
    raw_tools = await mcp_tools(server).tools()
    wrapped: list[Any] = []
    for raw_tool in raw_tools:
        name = _canonical_tool_name(raw_tool)
        if name in _READ_ONLY_MCP_INFO_TYPES:
            wrapped.append(_wrap_read_only_tool(raw_tool))
    if include_resources:
        wrapped.extend(_resource_tools(server))

    prompt_text = ""
    prompt_name = ""
    if include_prompt:
        prompt_text = await _load_bloodhound_mcp_prompt(server, BLOODHOUND_PROMPT_NAME)
        prompt_name = BLOODHOUND_PROMPT_NAME if prompt_text else ""

    return MCPServerBundle(
        tools=wrapped,
        server_prompt_text=prompt_text,
        server_prompt_name=prompt_name,
    )
    raw_tools = await mcp_tools(server).tools()
    wrapped: list[Any] = []
    for raw_tool in raw_tools:
        name = _canonical_tool_name(raw_tool)
        if name in _READ_ONLY_MCP_INFO_TYPES:
            wrapped.append(_wrap_read_only_tool(raw_tool))
    return wrapped


def _mcp_system_prompt(task: Task) -> str:
    domain = task.metadata.get("domain", "CORP.LOCAL")
    preferred = task.metadata.get("preferred_tool_family", "cypher_or_mixed")
    track = task.metadata.get("mcp_track", "mcp_cypher_assisted")
    preference_text = (
        "Prefer higher-level BloodHound MCP tools (domain_info, user_info, group_info, "
        "computer_info, graph_analysis, data_quality). Use cypher_query only if those tools "
        "are insufficient."
        if preferred == "non_cypher"
        else "You may use BloodHound MCP tools, including cypher_query, to investigate, validate, and answer."
    )
    return f"""\
You are evaluating BloodHound analysis capability through MCP tools.
Domain: {domain}
Track: {track}

You have read-only BloodHound MCP tools. Never attempt write or mutation operations.
{preference_text}

After using tools, return ONLY a single JSON object as your final answer.
Do not include commentary or markdown fences.

JSON contract:
- For path_exists tasks:
  {{"answer_type":"path_exists","path_found":true|false,"node_names":["NAME1","NAME2"]}}
- For node_set tasks:
  {{"answer_type":"node_set","node_names":["NAME1","NAME2"]}}
- For row_count tasks:
  {{"answer_type":"row_count","count":123}}

Rules:
- node_names must be BloodHound object names, not object IDs
- If you cannot find a path, set path_found to false and return an empty node_names list
- Keep the final JSON compact and valid
"""


def _mcp_conversation_messages(task: Task, server_prompt_text: str = "") -> list[Any]:
    messages: list[Any] = []
    if server_prompt_text.strip():
        messages.append(
            ChatMessageSystem(
                content=(
                    f"BloodHound MCP prompt ({BLOODHOUND_PROMPT_NAME}):\n"
                    f"{server_prompt_text.strip()}"
                )
            )
        )
    messages.extend(
        [
            ChatMessageSystem(content=_mcp_system_prompt(task)),
            ChatMessageUser(content=task.question),
        ]
    )
    return messages


def _parse_json_object(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if not text:
        return None
    candidates = [text]
    if "```" in text:
        for block in text.split("```"):
            block = block.strip()
            if block.startswith("json"):
                block = block[4:].strip()
            if block.startswith("{") and block.endswith("}"):
                candidates.append(block)
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidates.append(text[start:end + 1])
    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            continue
    return None


def _normalize_final_answer(answer: dict[str, Any] | None, task: Task) -> dict[str, Any] | None:
    if answer is None:
        return None
    answer_type = str(answer.get("answer_type") or task.grade_mode)
    normalized: dict[str, Any] = {"answer_type": answer_type}
    if task.grade_mode == "path_exists":
        normalized["path_found"] = bool(answer.get("path_found"))
        normalized["node_names"] = [str(name).strip() for name in answer.get("node_names", []) if str(name).strip()]
    elif task.grade_mode == "node_set":
        normalized["node_names"] = [str(name).strip() for name in answer.get("node_names", []) if str(name).strip()]
    elif task.grade_mode == "row_count":
        try:
            normalized["count"] = int(answer.get("count", 0))
        except Exception:
            normalized["count"] = 0
    else:
        return None
    return normalized


def _tool_result_classification(tool_name: str, content: str, error_message: str | None) -> str | None:
    if error_message:
        if "POLICY_VIOLATION" in error_message:
            return "policy"
        return BHCEClient.classify_error(error_message)

    if not content:
        return None

    try:
        parsed = json.loads(content)
    except Exception:
        return None

    if not isinstance(parsed, dict) or parsed.get("success", True):
        return None

    error_type = str(parsed.get("error_type", ""))
    if error_type == "server_error":
        return "infra"
    if error_type in {"syntax_error", "not_found", "server_failure", "auth_error", "permission_error"}:
        return "query"
    if tool_name == "cypher_query" and BHCEClient.classify_error(str(parsed.get("error", ""))) == "infra":
        return "infra"
    return None


def _trajectory_from_messages(messages: list[Any], final_answer_raw: str = "") -> MCPRunMetadata:
    tool_calls_total = 0
    failed_tool_calls = 0
    unique_tools: set[str] = set()
    cypher_query_calls = 0
    non_cypher_tool_calls = 0
    agent_turns = 0
    attempted_policy_violations = 0
    infra_tool_errors = 0
    resource_reads_total = 0
    unique_resources: set[str] = set()
    resource_characters_total = 0
    tool_calls_by_id: dict[str, tuple[str, dict[str, Any]]] = {}

    for message in messages:
        if isinstance(message, ChatMessageAssistant):
            agent_turns += 1
            for call in message.tool_calls or []:
                tool_calls_total += 1
                unique_tools.add(call.function)
                if call.id:
                    tool_calls_by_id[call.id] = (call.function, call.arguments)
                if call.function == "cypher_query":
                    cypher_query_calls += 1
                else:
                    non_cypher_tool_calls += 1
        elif isinstance(message, ChatMessageTool):
            tool_name = message.function or ""
            tool_args: dict[str, Any] = {}
            if message.tool_call_id and message.tool_call_id in tool_calls_by_id:
                tool_name, tool_args = tool_calls_by_id[message.tool_call_id]
            classification = _tool_result_classification(
                tool_name,
                message.text,
                message.error.message if message.error else None,
            )
            if message.error or classification in {"policy", "infra"}:
                failed_tool_calls += 1
            if classification == "policy":
                attempted_policy_violations += 1
            if classification == "infra":
                infra_tool_errors += 1
            if tool_name == RESOURCE_READ_TOOL_NAME:
                resource_reads_total += 1
                uri = str(tool_args.get("uri", "")).strip()
                if uri:
                    unique_resources.add(uri)
                resource_characters_total += len(message.text or "")

    return MCPRunMetadata(
        final_answer_raw=final_answer_raw,
        tool_calls_total=tool_calls_total,
        failed_tool_calls=failed_tool_calls,
        unique_tools_used=sorted(unique_tools),
        cypher_query_calls=cypher_query_calls,
        non_cypher_tool_calls=non_cypher_tool_calls,
        agent_turns=agent_turns,
        attempted_policy_violations=attempted_policy_violations,
        infra_tool_errors=infra_tool_errors,
        resource_reads_total=resource_reads_total,
        unique_resources_used=sorted(unique_resources),
        resource_characters_total=resource_characters_total,
    )


def _mcp_metadata_to_dict(metadata: MCPRunMetadata) -> dict[str, Any]:
    payload = asdict(metadata)
    payload["final_answer_normalized_json"] = metadata.final_answer_normalized_json
    return payload


def _mcp_metadata_from_dict(data: dict[str, Any]) -> MCPRunMetadata:
    return MCPRunMetadata(
        final_answer_raw=data.get("final_answer_raw", ""),
        final_answer_normalized=dict(data["final_answer_normalized"]) if data.get("final_answer_normalized") else None,
        tool_calls_total=int(data.get("tool_calls_total", 0)),
        failed_tool_calls=int(data.get("failed_tool_calls", 0)),
        unique_tools_used=list(data.get("unique_tools_used", [])),
        cypher_query_calls=int(data.get("cypher_query_calls", 0)),
        non_cypher_tool_calls=int(data.get("non_cypher_tool_calls", 0)),
        agent_turns=int(data.get("agent_turns", 0)),
        attempted_policy_violations=int(data.get("attempted_policy_violations", 0)),
        trajectory_log=data.get("trajectory_log", ""),
        infra_tool_errors=int(data.get("infra_tool_errors", 0)),
        server_prompt_used=bool(data.get("server_prompt_used", False)),
        server_prompt_name=data.get("server_prompt_name", ""),
        resource_mode=data.get("resource_mode", RESOURCE_MODE_OFF),
        resource_reads_total=int(data.get("resource_reads_total", 0)),
        unique_resources_used=list(data.get("unique_resources_used", [])),
        resource_characters_total=int(data.get("resource_characters_total", 0)),
    )


def _mock_mcp_answer(task: Task, ref_result: CypherResult, model_name: str) -> tuple[str, dict[str, Any] | None]:
    if model_name == "mock/mcp_empty":
        return "", None
    if model_name == "mock/mcp_wrong":
        if task.grade_mode == "path_exists":
            answer = {"answer_type": "path_exists", "path_found": True, "node_names": []}
        elif task.grade_mode == "row_count":
            answer = {"answer_type": "row_count", "count": 0}
        else:
            valid_but_wrong = sorted(ref_result.node_names)
            if valid_but_wrong:
                valid_but_wrong = valid_but_wrong[:-1]
            answer = {"answer_type": "node_set", "node_names": valid_but_wrong}
        return json.dumps(answer), answer

    if task.grade_mode == "path_exists":
        answer = {
            "answer_type": "path_exists",
            "path_found": bool(ref_result.node_names),
            "node_names": sorted(ref_result.node_names),
        }
    elif task.grade_mode == "row_count":
        answer = {"answer_type": "row_count", "count": len(ref_result.nodes)}
    else:
        answer = {"answer_type": "node_set", "node_names": sorted(ref_result.node_names)}
    return json.dumps(answer), answer


@solver
def ori_mcp_solver(
    tools: list[Any],
    *,
    max_steps: int = 12,
    server_prompt_text: str = "",
    server_prompt_name: str = "",
    resource_mode: str = RESOURCE_MODE_OFF,
) -> Generate:
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        metadata = state.metadata
        task = _task_from_dict(metadata["ori_task"])
        ref_result = _cypher_result_from_dict(metadata["ref_result"])
        model_name = metadata.get("requested_model", str(state.model))

        state.messages = _mcp_conversation_messages(task, server_prompt_text=server_prompt_text)
        state.message_limit = max(8, max_steps * 4)
        state = await use_tools(tools, tool_choice="auto")(state, generate)

        if model_name.startswith("mock/mcp_"):
            completion, normalized = _mock_mcp_answer(task, ref_result, model_name)
            model_response = ModelResponse(
                raw_text=completion,
                cypher=None,
                parse_stage="mcp_final_answer" if normalized is not None else "none",
                tokens_input=0,
                tokens_output=0,
                elapsed_seconds=0.0,
                model=model_name,
                error=None,
            )
            state.output = ModelOutput.from_content(model=model_name, content=completion)
        else:
            try:
                state = await generate(state, tool_calls="loop")
                output = state.output
                usage = output.usage
                model_response = ModelResponse(
                    raw_text=output.completion,
                    cypher=None,
                    parse_stage="mcp_final_answer",
                    tokens_input=usage.input_tokens if usage else 0,
                    tokens_output=usage.output_tokens if usage else 0,
                    elapsed_seconds=output.time or 0.0,
                    model=output.model or model_name,
                    error=output.error,
                )
            except Exception as exc:
                model_response = ModelResponse(
                    raw_text="",
                    cypher=None,
                    parse_stage="none",
                    tokens_input=0,
                    tokens_output=0,
                    elapsed_seconds=0.0,
                    model=model_name,
                    error=str(exc),
                )
                state.output = ModelOutput.from_content(model=model_name, content="", error=str(exc))

        parsed = _parse_json_object(model_response.raw_text)
        normalized = _normalize_final_answer(parsed, task)
        trajectory = _trajectory_from_messages(state.messages, final_answer_raw=model_response.raw_text)
        trajectory.final_answer_normalized = normalized
        trajectory.server_prompt_used = bool(server_prompt_text.strip())
        trajectory.server_prompt_name = server_prompt_name if server_prompt_text.strip() else ""
        trajectory.resource_mode = resource_mode
        state.store.set("ori_model_response", _model_response_to_dict(model_response))
        state.store.set("ori_mcp_trajectory", _mcp_metadata_to_dict(trajectory))
        state.store.set("ori_model_calls", 1)
        return state

    return solve


@scorer(metrics=[accuracy(), stderr()])
def ori_mcp_scorer():
    async def score(state: TaskState, target: Any) -> Score:
        metadata = state.metadata
        task = _task_from_dict(metadata["ori_task"])
        ref_result = _cypher_result_from_dict(metadata["ref_result"])
        valid_names = set(metadata.get("valid_node_names", []))
        model_response = _model_response_from_dict(state.store.get("ori_model_response"))
        mcp_meta = _mcp_metadata_from_dict(state.store.get("ori_mcp_trajectory"))

        result = grade_mcp(
            task=task,
            model_response=model_response,
            final_answer=mcp_meta.final_answer_normalized,
            ref_result=ref_result,
            valid_node_names=valid_names,
            infra_tool_errors=mcp_meta.infra_tool_errors,
        )
        sample_index = metadata.get("sample_index", "?")
        sample_total = metadata.get("sample_total", "?")
        print(f"  [{sample_index}/{sample_total}] {task.id} ({task.tier=}, {task.grade_mode})")
        print(
            f"           → {result.outcome} (score={result.score}, tools={mcp_meta.tool_calls_total}, "
            f"cypher={mcp_meta.cypher_query_calls}, noncypher={mcp_meta.non_cypher_tool_calls}, "
            f"resources={mcp_meta.resource_reads_total})"
        )
        return Score(
            value=result.score,
            answer=model_response.raw_text,
            explanation=result.details,
            metadata={
                "grade": _grade_result_to_dict(result),
                "mcp": _mcp_metadata_to_dict(mcp_meta),
            },
        )

    return score


def _sample_for_task(
    task: Task,
    ref_result: CypherResult,
    valid_node_names: set[str],
    requested_model: str,
    model_base_url: str | None,
    ollama_options: dict[str, Any] | None,
    bhce_domain: str | None,
    sample_index: int,
    sample_total: int,
) -> Sample:
    return Sample(
        id=task.id,
        input=task.question,
        target="",
        metadata={
            "ori_task": _task_to_dict(task),
            "ref_result": _cypher_result_to_dict(ref_result),
            "valid_node_names": sorted(valid_node_names),
            "requested_model": requested_model,
            "model_base_url": model_base_url,
            "ollama_options": ollama_options,
            "bhce_domain": bhce_domain,
            "sample_index": sample_index,
            "sample_total": sample_total,
        },
    )


def _result_from_sample(sample: EvalSample, log: EvalLog):
    from .runner import EvalResult

    task = _task_from_dict(sample.metadata["ori_task"])
    model_response = _model_response_from_dict(sample.store["ori_model_response"])
    if not sample.scores:
        raise ValueError(f"Inspect MCP sample {sample.id!r} missing scores")
    score = next(iter(sample.scores.values()))
    grade_result = _score_metadata_to_grade_result(score)
    mcp_meta = _mcp_metadata_from_dict(score.metadata["mcp"])
    mcp_meta.trajectory_log = log.location or ""
    inspect_meta = InspectEvalMetadata(
        log_location=log.location or None,
        sample_id=str(sample.id),
        sample_uuid=sample.uuid,
        model_calls=int(sample.store.get("ori_model_calls", 0)),
        error_retries=len(sample.error_retries or []),
    )
    return EvalResult(
        task=task,
        model_response=model_response,
        grade=grade_result,
        ref_result=_cypher_result_from_dict(sample.metadata["ref_result"]),
        model_result=CypherResult(success=True, nodes=[], node_names=set(), raw={}),
        inspect=inspect_meta,
        mcp=mcp_meta,
    )


async def run_mcp_eval_with_inspect(
    tasks: list[Task],
    model: str,
    bhce: BHCEClient,
    output_path: Path,
    concurrency: int = 1,
    base_url: str | None = None,
    ollama_options: dict[str, Any] | None = None,
    log_dir: Path | None = None,
    bhce_domain: str | None = None,
    mcp_dir: Path | None = None,
    max_steps: int = 12,
    resource_mode: str = RESOURCE_MODE_OFF,
):
    if resource_mode not in {RESOURCE_MODE_OFF, RESOURCE_MODE_ON_DEMAND}:
        raise ValueError(f"Unsupported resource mode: {resource_mode!r}")

    print("Fetching valid node names for hallucination detection...")
    valid_names = await bhce.get_all_node_names()
    if len(valid_names) == 0:
        print("  WARNING: 0 node names loaded — BH CE graph appears empty.")
    else:
        print(f"  {len(valid_names)} node names loaded")

    print(f"Pre-fetching reference Cypher results for {len(tasks)} tasks...")
    ref_results: dict[str, CypherResult] = {}
    for task in tasks:
        ref_results[task.id] = await bhce.run_cypher_resilient(task.reference_cypher)
    print("  Done")

    resolved_base_url = _resolve_model_base_url(model, base_url)
    inspect_model = model if _inspect_supported_model(model) else "none/none"
    samples = [
        _sample_for_task(
            task=task,
            ref_result=ref_results[task.id],
            valid_node_names=valid_names,
            requested_model=model,
            model_base_url=resolved_base_url,
            ollama_options=ollama_options,
            bhce_domain=bhce_domain,
            sample_index=i + 1,
            sample_total=len(tasks),
        )
        for i, task in enumerate(tasks)
    ]

    server_prompt_text = ""
    server_prompt_name = ""
    if model.startswith("mock/mcp_"):
        tools: list[Any] = []
    else:
        resolved_mcp_dir = (mcp_dir or (Path.cwd().parent / "bloodhound-mcp")).resolve()
        bundle = await _load_bloodhound_mcp_bundle(
            resolved_mcp_dir,
            include_resources=resource_mode == RESOURCE_MODE_ON_DEMAND,
            include_prompt=True,
        )
        tools = bundle.tools
        server_prompt_text = bundle.server_prompt_text
        server_prompt_name = bundle.server_prompt_name
        if server_prompt_name:
            print(
                f"Loaded BloodHound MCP prompt: {server_prompt_name} "
                f"({len(server_prompt_text)} chars)"
            )
        if resource_mode == RESOURCE_MODE_ON_DEMAND:
            print("Resource mode: on-demand")

    inspect_task = InspectTask(
        dataset=samples,
        solver=ori_mcp_solver(
            tools=tools,
            max_steps=max_steps,
            server_prompt_text=server_prompt_text,
            server_prompt_name=server_prompt_name,
            resource_mode=resource_mode,
        ),
        scorer=ori_mcp_scorer(),
        name=f"{_task_name_for_model(model)}_mcp",
    )

    resolved_log_dir = log_dir or _log_dir_for_output(output_path, f"{model}_mcp")
    resolved_log_dir.mkdir(parents=True, exist_ok=True)
    _configure_inspect_runtime_dirs(resolved_log_dir)

    eval_logs = await inspect_eval_async(
        inspect_task,
        model=inspect_model,
        model_base_url=resolved_base_url,
        log_dir=str(resolved_log_dir),
        max_samples=max(concurrency, 1),
        max_subprocesses=1,
        log_level="warning",
        log_level_transcript="warning",
        extra_body={"options": ollama_options} if ollama_options and model.startswith("ollama/") else None,
    )
    if not eval_logs:
        raise RuntimeError("Inspect MCP eval returned no logs")
    log = eval_logs[0]
    if not log.samples:
        raise RuntimeError("Inspect MCP eval log did not contain sample results")
    return [_result_from_sample(sample, log) for sample in log.samples]
