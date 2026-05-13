"""Inspect AI-backed runtime for Phase 3B MCP evaluation."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from functools import wraps
from pathlib import Path
from typing import Any

import httpx
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
from inspect_ai.tool import ToolCall, ToolCallError, ToolError, mcp_server_stdio, mcp_tools, tool
from inspect_ai.tool._tool_info import parse_tool_info
from mcp.types import EmbeddedResource, PromptMessage, ResourceLink, TextContent

from .adapter import ModelResponse
from .bhce import BHCEClient, CypherResult
from .grader import GradeResult, grade_mcp
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
DEFAULT_MCP_OLLAMA_READ_TIMEOUT_SECONDS = 900.0
MCP_TOOL_LOOP_AUTO = "auto"
MCP_TOOL_LOOP_INSPECT = "inspect"
MCP_TOOL_LOOP_NATIVE_OLLAMA = "native-ollama"
MCP_TOOL_LOOP_NATIVE_OPENAI_COMPAT = "native-openai-compatible"
MCP_TOOL_LOOP_VALUES = {
    MCP_TOOL_LOOP_AUTO,
    MCP_TOOL_LOOP_INSPECT,
    MCP_TOOL_LOOP_NATIVE_OLLAMA,
    MCP_TOOL_LOOP_NATIVE_OPENAI_COMPAT,
}
OPENAI_COMPAT_TELEMETRY_AUTO = "auto"
OPENAI_COMPAT_TELEMETRY_GENERIC = "generic"
OPENAI_COMPAT_TELEMETRY_LLAMA_CPP = "llama-cpp"
OPENAI_COMPAT_TELEMETRY_MLX_LM = "mlx-lm"
OPENAI_COMPAT_TELEMETRY_VLLM = "vllm"
OPENAI_COMPAT_TELEMETRY_LM_STUDIO = "lm-studio"
OPENAI_COMPAT_TELEMETRY_VALUES = {
    OPENAI_COMPAT_TELEMETRY_AUTO,
    OPENAI_COMPAT_TELEMETRY_GENERIC,
    OPENAI_COMPAT_TELEMETRY_LLAMA_CPP,
    OPENAI_COMPAT_TELEMETRY_MLX_LM,
    OPENAI_COMPAT_TELEMETRY_VLLM,
    OPENAI_COMPAT_TELEMETRY_LM_STUDIO,
}


@dataclass
class MCPServerBundle:
    tools: list[Any]
    server_prompt_text: str = ""
    server_prompt_name: str = ""
    available_prompt_names: list[str] = field(default_factory=list)
    prompt_discovery_status: str = "not_requested"


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
    available_prompt_names: list[str] = field(default_factory=list)
    prompt_discovery_status: str = ""
    resource_mode: str = RESOURCE_MODE_OFF
    tool_loop: str = MCP_TOOL_LOOP_AUTO
    resource_reads_total: int = 0
    unique_resources_used: list[str] = field(default_factory=list)
    resource_characters_total: int = 0
    infra_error_subtype: str = ""

    @property
    def final_answer_normalized_json(self) -> str:
        if self.final_answer_normalized is None:
            return ""
        return json.dumps(self.final_answer_normalized, sort_keys=True)


_READ_ONLY_MCP_INFO_TYPES: dict[str, set[str]] = {
    "domain_info": {
        "list",
        "search",
        "users",
        "groups",
        "computers",
        "controllers",
        "gpos",
        "ous",
        "dc_syncers",
        "foreign_admins",
        "foreign_gpo_controllers",
        "foreign_groups",
        "foreign_users",
        "inbound_trusts",
        "outbound_trusts",
    },
    "user_info": {
        "info",
        "admin_rights",
        "constrained_delegation",
        "controllables",
        "controllers",
        "dcom_rights",
        "memberships",
        "ps_remote_rights",
        "rdp_rights",
        "sessions",
        "sql_admin_rights",
    },
    "group_info": {
        "info",
        "admin_rights",
        "controllables",
        "controllers",
        "dcom_rights",
        "members",
        "memberships",
        "ps_remote_rights",
        "rdp_rights",
        "sessions",
    },
    "computer_info": {
        "info",
        "admin_rights",
        "admin_users",
        "constrained_delegation",
        "constrained_users",
        "controllables",
        "controllers",
        "dcom_rights",
        "dcom_users",
        "group_membership",
        "ps_remote_rights",
        "ps_remote_users",
        "rdp_rights",
        "rdp_users",
        "sessions",
        "sql_admins",
    },
    "ou_info": {"info", "computers", "groups", "gpos", "users"},
    "gpo_info": {"info", "computers", "controllers", "ous", "tier_zeros", "users"},
    "graph_analysis": {"search", "shortest_path", "edge_composition", "relay_targets"},
    "adcs_info": {
        "cert_template_info",
        "cert_template_controllers",
        "root_ca_info",
        "root_ca_controllers",
        "enterprise_ca_info",
        "enterprise_ca_controllers",
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


def _rank_mcp_prompt_name(name: str) -> tuple[int, int, str]:
    normalized = name.lower().replace("-", "_")
    score = 0
    if "bloodhound" in normalized:
        score += 10
    if "assistant" in normalized:
        score += 8
    if "analyst" in normalized or "analysis" in normalized:
        score += 4
    if "ad" in normalized or "active_directory" in normalized:
        score += 2
    if normalized == BLOODHOUND_PROMPT_NAME:
        score += 20
    return (-score, len(name), name)


def _prompt_name(prompt: Any) -> str:
    if isinstance(prompt, str):
        return prompt
    return str(getattr(prompt, "name", "") or getattr(prompt, "id", "") or "")


async def _discover_bloodhound_mcp_prompt(server: Any) -> tuple[str, str, list[str], str]:
    """Discover and load the best BloodHound-like MCP prompt.

    Returns prompt_text, selected_name, available_names, status.
    """

    session_handle = server._task_session()
    try:
        async with session_handle._client_session() as session:
            listed = await session.list_prompts()
            prompts = list(getattr(listed, "prompts", listed) or [])
            names = sorted({name for prompt in prompts if (name := _prompt_name(prompt))})
            if not names:
                print("  WARNING: MCP server exposed no prompts; using built-in ORI MCP prompt.")
                return "", "", [], "no_prompts"
            selected = sorted(names, key=_rank_mcp_prompt_name)[0]
            prompt = await session.get_prompt(selected)
        text = _prompt_messages_to_text(prompt.messages)
        if not text:
            print(
                f"  WARNING: MCP prompt {selected!r} was empty; using built-in ORI MCP prompt."
            )
            return "", selected, names, "empty_selected_prompt"
        return text, selected, names, "selected"
    except Exception as exc:
        print(f"  WARNING: MCP prompt discovery failed ({exc}); using built-in ORI MCP prompt.")
        return "", "", [], "discovery_failed"


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
            """List available BloodHound MCP reference resources."""
            session_handle = server._task_session()
            async with session_handle._client_session() as session:
                result = await session.list_resources()
            return _format_resource_list(result.resources)

        return execute

    @tool(name=RESOURCE_READ_TOOL_NAME)
    def read_bloodhound_resource() -> Any:
        """Read a BloodHound MCP reference resource by URI."""

        async def execute(uri: str) -> str:
            """Read a BloodHound MCP reference resource by URI.

            Args:
                uri: BloodHound MCP resource URI to read.
            """
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
    available_prompt_names: list[str] = []
    prompt_discovery_status = "not_requested"
    if include_prompt:
        (
            prompt_text,
            prompt_name,
            available_prompt_names,
            prompt_discovery_status,
        ) = await _discover_bloodhound_mcp_prompt(server)

    return MCPServerBundle(
        tools=wrapped,
        server_prompt_text=prompt_text,
        server_prompt_name=prompt_name,
        available_prompt_names=available_prompt_names,
        prompt_discovery_status=prompt_discovery_status,
    )


def _mcp_system_prompt(task: Task) -> str:
    domain = task.metadata.get("domain", "CORP.LOCAL")
    preferred = task.metadata.get("preferred_tool_family", "cypher_or_mixed")
    track = task.metadata.get("mcp_track", "mcp_cypher_assisted")
    preference_text = (
        "Prefer higher-level BloodHound MCP tools (domain_info, user_info, group_info, "
        "computer_info, graph_analysis, data_quality). Use cypher_query only if those tools "
        "are insufficient."
        if preferred == "non_cypher"
        else "You may use BloodHound MCP tools, including cypher_query, to investigate, validate, and answer."  # noqa: E501
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


def _mcp_conversation_messages(
    task: Task,
    server_prompt_text: str = "",
    server_prompt_name: str = BLOODHOUND_PROMPT_NAME,
) -> list[Any]:
    messages: list[Any] = []
    if server_prompt_text.strip():
        messages.append(
            ChatMessageSystem(
                content=(
                    f"BloodHound MCP prompt ({server_prompt_name or 'discovered'}):\n"
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
        candidates.append(text[start : end + 1])
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
        normalized["node_names"] = [
            str(name).strip() for name in answer.get("node_names", []) if str(name).strip()
        ]
    elif task.grade_mode == "node_set":
        normalized["node_names"] = [
            str(name).strip() for name in answer.get("node_names", []) if str(name).strip()
        ]
    elif task.grade_mode == "row_count":
        try:
            normalized["count"] = int(answer.get("count", 0))
        except Exception:
            normalized["count"] = 0
    else:
        return None
    return normalized


def _tool_result_classification(
    tool_name: str, content: str, error_message: str | None
) -> str | None:
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
    if error_type in {
        "syntax_error",
        "not_found",
        "server_failure",
        "auth_error",
        "permission_error",
    }:
        return "query"
    if (
        tool_name == "cypher_query"
        and BHCEClient.classify_error(str(parsed.get("error", ""))) == "infra"
    ):
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
        final_answer_normalized=dict(data["final_answer_normalized"])
        if data.get("final_answer_normalized")
        else None,
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
        tool_loop=data.get("tool_loop", MCP_TOOL_LOOP_AUTO),
        resource_reads_total=int(data.get("resource_reads_total", 0)),
        unique_resources_used=list(data.get("unique_resources_used", [])),
        resource_characters_total=int(data.get("resource_characters_total", 0)),
        available_prompt_names=list(data.get("available_prompt_names", [])),
        prompt_discovery_status=data.get("prompt_discovery_status", ""),
        infra_error_subtype=data.get("infra_error_subtype", ""),
    )


def _is_ollama_model(model_name: str) -> bool:
    return model_name.startswith("ollama/")


def _is_openai_compat_model(model_name: str) -> bool:
    return model_name.startswith("openai-compat/")


def _resolve_mcp_tool_loop(model_name: str, requested: str) -> str:
    if requested not in MCP_TOOL_LOOP_VALUES:
        supported = ", ".join(sorted(MCP_TOOL_LOOP_VALUES))
        raise ValueError(f"Unsupported MCP tool loop {requested!r}. Supported values: {supported}")
    if requested == MCP_TOOL_LOOP_AUTO:
        if _is_ollama_model(model_name):
            return MCP_TOOL_LOOP_NATIVE_OLLAMA
        return MCP_TOOL_LOOP_INSPECT
    if requested == MCP_TOOL_LOOP_NATIVE_OLLAMA and not _is_ollama_model(model_name):
        raise ValueError("MCP tool loop native-ollama requires an ollama/... model.")
    if requested == MCP_TOOL_LOOP_NATIVE_OPENAI_COMPAT and not _is_openai_compat_model(model_name):
        raise ValueError(
            "MCP tool loop native-openai-compatible requires an openai-compat/... model."
        )
    return requested


def _native_ollama_chat_url(base_url: str | None) -> str:
    resolved = (base_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")).rstrip("/")
    if resolved.endswith("/v1"):
        resolved = resolved[:-3].rstrip("/")
    return f"{resolved}/api/chat"


def _openai_compat_model_name(model_name: str) -> str:
    if "/" not in model_name:
        return model_name
    model = model_name.split("/", 1)[1]
    if "@" in model:
        model = model.split("@", 1)[0]
    return model


def _openai_compat_chat_url(base_url: str | None, model_name: str) -> str:
    resolved = base_url
    if not resolved and "@" in model_name:
        resolved = model_name.rsplit("@", 1)[1]
    resolved = (resolved or os.getenv("OPENAI_COMPAT_BASE_URL", "")).rstrip("/")
    if not resolved:
        raise ValueError(
            "native-openai-compatible MCP loop requires model_base_url, "
            "OPENAI_COMPAT_BASE_URL, or openai-compat/model@url."
        )
    if resolved.endswith("/chat/completions"):
        return resolved
    if resolved.endswith("/v1"):
        return f"{resolved}/chat/completions"
    return f"{resolved}/v1/chat/completions"


def _openai_compat_api_key() -> str:
    return os.getenv("OPENAI_COMPAT_API_KEY") or os.getenv("OPENAI_API_KEY") or "not-needed"


def _normalize_openai_compat_telemetry_adapter(raw_adapter: str | None) -> str:
    adapter = raw_adapter or OPENAI_COMPAT_TELEMETRY_AUTO
    if adapter not in OPENAI_COMPAT_TELEMETRY_VALUES:
        supported = ", ".join(sorted(OPENAI_COMPAT_TELEMETRY_VALUES))
        raise ValueError(
            f"Unsupported OpenAI-compatible telemetry adapter {adapter!r}. "
            f"Supported values: {supported}"
        )
    return adapter


def _infer_openai_compat_telemetry_adapter(
    telemetry_adapter: str | None,
    *,
    base_url: str | None,
    model_name: str,
) -> str:
    adapter = _normalize_openai_compat_telemetry_adapter(telemetry_adapter)
    if adapter != OPENAI_COMPAT_TELEMETRY_AUTO:
        return adapter

    haystack = " ".join(part.lower() for part in (base_url or "", model_name) if part)
    if "llama" in haystack and ("cpp" in haystack or "8080" in haystack):
        return OPENAI_COMPAT_TELEMETRY_LLAMA_CPP
    if "mlx" in haystack:
        return OPENAI_COMPAT_TELEMETRY_MLX_LM
    if "vllm" in haystack:
        return OPENAI_COMPAT_TELEMETRY_VLLM
    if "lm-studio" in haystack or "lmstudio" in haystack or "1234" in haystack:
        return OPENAI_COMPAT_TELEMETRY_LM_STUDIO
    return OPENAI_COMPAT_TELEMETRY_GENERIC


def _string_field(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value:
            return value
    return ""


def _split_thinking_from_content(content: str) -> tuple[str, str]:
    thinking_parts: list[str] = []

    def replace(match: re.Match[str]) -> str:
        thinking_parts.append(match.group(1).strip())
        return ""

    cleaned = re.sub(
        r"<think>\s*(.*?)\s*</think>",
        replace,
        content,
        flags=re.DOTALL | re.IGNORECASE,
    ).strip()
    return cleaned, "\n".join(part for part in thinking_parts if part)


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def _openai_compat_provider_metrics(
    *,
    data: dict[str, Any],
    choice: dict[str, Any],
    message: dict[str, Any],
    usage: dict[str, Any],
    telemetry_adapter: str,
) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "telemetry_adapter": telemetry_adapter,
        "response_id": data.get("id", ""),
        "object": data.get("object", ""),
        "system_fingerprint": data.get("system_fingerprint", ""),
        "finish_reason": choice.get("finish_reason", ""),
        "usage": _json_safe(usage),
    }
    for key in ("prompt_tokens_details", "completion_tokens_details"):
        if key in usage:
            metrics[key] = _json_safe(usage[key])

    if telemetry_adapter == OPENAI_COMPAT_TELEMETRY_LLAMA_CPP:
        for key in (
            "timings",
            "generation_settings",
            "tokens_cached",
            "tokens_evaluated",
            "truncated",
        ):
            if key in data:
                metrics[f"llama_cpp_{key}"] = _json_safe(data[key])
    elif telemetry_adapter == OPENAI_COMPAT_TELEMETRY_MLX_LM:
        if choice.get("logprobs") is not None:
            metrics["mlx_lm_logprobs"] = _json_safe(choice["logprobs"])
    elif telemetry_adapter == OPENAI_COMPAT_TELEMETRY_VLLM:
        for key in ("request_id", "kv_transfer_params"):
            if key in data:
                metrics[f"vllm_{key}"] = _json_safe(data[key])
    elif telemetry_adapter == OPENAI_COMPAT_TELEMETRY_LM_STUDIO:
        for key in ("stats", "model_info"):
            if key in data:
                metrics[f"lm_studio_{key}"] = _json_safe(data[key])

    reasoning = _string_field(
        message.get("reasoning"),
        message.get("reasoning_content"),
        message.get("thinking"),
        data.get("reasoning"),
    )
    if reasoning:
        metrics["reasoning_source"] = "structured_field"
    return metrics


def _tool_result_to_text(result: Any) -> str:
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result)
    except Exception:
        return str(result)


def _ollama_tool_spec(tool_obj: Any) -> tuple[dict[str, Any], Any]:
    canonical_name = _canonical_tool_name(tool_obj)
    # Tools loaded from MCP are already Inspect executor callables. Local test
    # tools may still be passed as @tool factories, so only instantiate those.
    executor = tool_obj if hasattr(tool_obj, "__registry_params__") else tool_obj()
    info = parse_tool_info(executor)
    return (
        {
            "type": "function",
            "function": {
                "name": canonical_name,
                "description": info.description or "",
                "parameters": info.parameters.model_dump(),
            },
        },
        executor,
    )


async def _ollama_chat_turn(
    *,
    url: str,
    model_name: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    ollama_options: dict[str, Any] | None,
    read_timeout_seconds: float = DEFAULT_MCP_OLLAMA_READ_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model_name.split("/", 1)[1],
        "messages": messages,
        "tools": tools,
        "stream": True,
    }
    if ollama_options:
        payload["options"] = dict(ollama_options)

    thinking_parts: list[str] = []
    content_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    prompt_eval_count = 0
    eval_count = 0
    final_model = model_name
    done_metrics: dict[str, int] = {}

    timeout = httpx.Timeout(connect=10.0, read=read_timeout_seconds, write=30.0, pool=30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", url, json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                data = json.loads(line)
                final_model = data.get("model") or final_model
                message = data.get("message") or {}
                thinking = message.get("thinking")
                if isinstance(thinking, str) and thinking:
                    thinking_parts.append(thinking)
                content = message.get("content")
                if isinstance(content, str) and content:
                    content_parts.append(content)
                chunk_tool_calls = message.get("tool_calls") or []
                if chunk_tool_calls:
                    tool_calls.extend(chunk_tool_calls)
                if data.get("done"):
                    prompt_eval_count = int(data.get("prompt_eval_count") or prompt_eval_count or 0)
                    eval_count = int(data.get("eval_count") or eval_count or 0)
                    done_metrics = {
                        "total_duration_ns": int(data.get("total_duration") or 0),
                        "load_duration_ns": int(data.get("load_duration") or 0),
                        "prompt_eval_duration_ns": int(data.get("prompt_eval_duration") or 0),
                        "eval_duration_ns": int(data.get("eval_duration") or 0),
                    }

    return {
        "model": final_model,
        "thinking": "".join(thinking_parts),
        "content": "".join(content_parts),
        "tool_calls": tool_calls,
        "prompt_eval_count": prompt_eval_count,
        "eval_count": eval_count,
        "metrics": done_metrics,
    }


async def _openai_compat_chat_turn(
    *,
    url: str,
    model_name: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    extra_body: dict[str, Any] | None = None,
    telemetry_adapter: str = OPENAI_COMPAT_TELEMETRY_GENERIC,
    read_timeout_seconds: float = DEFAULT_MCP_OLLAMA_READ_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": _openai_compat_model_name(model_name),
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
        "stream": False,
    }
    if extra_body:
        payload.update(dict(extra_body))

    timeout = httpx.Timeout(connect=10.0, read=read_timeout_seconds, write=30.0, pool=30.0)
    headers = {"Authorization": f"Bearer {_openai_compat_api_key()}"}
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    choices = list(data.get("choices") or [])
    choice = choices[0] if choices else {}
    message = dict(choice.get("message") or {})
    usage = dict(data.get("usage") or {})
    content = message.get("content") or ""
    content, think_block = _split_thinking_from_content(content)
    thinking = _string_field(
        message.get("reasoning"),
        message.get("reasoning_content"),
        message.get("thinking"),
        data.get("reasoning"),
        think_block,
    )
    provider_metrics = _openai_compat_provider_metrics(
        data=data,
        choice=choice,
        message=message,
        usage=usage,
        telemetry_adapter=telemetry_adapter,
    )
    if think_block and "reasoning_source" not in provider_metrics:
        provider_metrics["reasoning_source"] = "think_block"
    return {
        "model": data.get("model") or _openai_compat_model_name(model_name),
        "content": content,
        "thinking": thinking,
        "tool_calls": message.get("tool_calls") or [],
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or 0),
        "finish_reason": choice.get("finish_reason") or "",
        "provider_metrics": provider_metrics,
    }


def _tool_call_arguments(raw_arguments: Any) -> dict[str, Any]:
    if isinstance(raw_arguments, dict):
        return raw_arguments
    if isinstance(raw_arguments, str) and raw_arguments.strip():
        try:
            parsed = json.loads(raw_arguments)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return {}
    return {}


async def _run_ollama_mcp_loop(
    *,
    task: Task,
    model_name: str,
    base_url: str | None,
    ollama_options: dict[str, Any] | None,
    tools: list[Any],
    max_steps: int,
    server_prompt_text: str = "",
    server_prompt_name: str = "",
    available_prompt_names: list[str] | None = None,
    prompt_discovery_status: str = "",
    resource_mode: str = RESOURCE_MODE_OFF,
    ollama_read_timeout_seconds: float = DEFAULT_MCP_OLLAMA_READ_TIMEOUT_SECONDS,
) -> tuple[ModelResponse, MCPRunMetadata, list[Any]]:
    url = _native_ollama_chat_url(base_url)
    messages_payload: list[dict[str, Any]] = []
    if server_prompt_text.strip():
        messages_payload.append({"role": "system", "content": server_prompt_text})
    messages_payload.append({"role": "system", "content": _mcp_system_prompt(task)})
    messages_payload.append({"role": "user", "content": task.question})

    inspect_messages = _mcp_conversation_messages(
        task,
        server_prompt_text=server_prompt_text,
        server_prompt_name=server_prompt_name,
    )
    tool_specs: list[dict[str, Any]] = []
    tool_runners: dict[str, Any] = {}
    for tool_obj in tools:
        spec, executor = _ollama_tool_spec(tool_obj)
        tool_specs.append(spec)
        tool_runners[spec["function"]["name"]] = executor

    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_metrics = {
        "total_duration_ns": 0,
        "load_duration_ns": 0,
        "prompt_eval_duration_ns": 0,
        "eval_duration_ns": 0,
    }
    thinking_parts: list[str] = []
    final_content = ""
    resolved_model = model_name
    t0 = time.monotonic()

    for step in range(max_steps):
        turn = await _ollama_chat_turn(
            url=url,
            model_name=model_name,
            messages=messages_payload,
            tools=tool_specs,
            ollama_options=ollama_options,
            read_timeout_seconds=ollama_read_timeout_seconds,
        )
        total_prompt_tokens += int(turn["prompt_eval_count"])
        total_completion_tokens += int(turn["eval_count"])
        for key in total_metrics:
            total_metrics[key] += int((turn.get("metrics") or {}).get(key) or 0)
        resolved_model = str(turn["model"] or resolved_model)
        thinking = str(turn["thinking"] or "")
        content = str(turn["content"] or "")
        raw_tool_calls = list(turn["tool_calls"] or [])
        if thinking:
            thinking_parts.append(thinking)

        inspect_tool_calls: list[ToolCall] = []
        payload_assistant: dict[str, Any] = {"role": "assistant", "content": content}
        if thinking:
            payload_assistant["thinking"] = thinking
        if raw_tool_calls:
            for idx, call in enumerate(raw_tool_calls):
                function = dict(call.get("function") or {})
                name = str(function.get("name") or "")
                arguments = function.get("arguments") or {}
                if not isinstance(arguments, dict):
                    arguments = {}
                call_id = str(call.get("id") or f"ollama-call-{step + 1}-{idx + 1}")
                inspect_tool_calls.append(ToolCall(id=call_id, function=name, arguments=arguments))
            payload_assistant["tool_calls"] = raw_tool_calls

        messages_payload.append(payload_assistant)
        inspect_messages.append(
            ChatMessageAssistant(
                content=content,
                tool_calls=inspect_tool_calls or None,
                model=resolved_model,
            )
        )

        if not raw_tool_calls:
            final_content = content
            break

        for tool_call in inspect_tool_calls:
            tool_name = tool_call.function
            tool_runner = tool_runners.get(tool_name)
            result_text = ""
            tool_error: ToolCallError | None = None
            try:
                if tool_runner is None:
                    raise RuntimeError(f"Unknown tool: {tool_name}")
                result = tool_runner(**tool_call.arguments)
                if inspect.isawaitable(result):
                    result = await result
                result_text = _tool_result_to_text(result)
            except Exception as exc:
                result_text = json.dumps(
                    {"success": False, "error": str(exc), "error_type": "tool_error"}
                )
                tool_error = ToolCallError(type="unknown", message=str(exc))

            messages_payload.append(
                {"role": "tool", "tool_name": tool_name, "content": result_text}
            )
            inspect_messages.append(
                ChatMessageTool(
                    content=result_text,
                    tool_call_id=tool_call.id,
                    function=tool_name,
                    error=tool_error,
                )
            )
    else:
        final_content = ""

    elapsed = time.monotonic() - t0
    model_response = ModelResponse(
        raw_text=final_content,
        cypher=None,
        parse_stage="mcp_final_answer" if final_content else "none",
        tokens_input=total_prompt_tokens,
        tokens_output=total_completion_tokens,
        elapsed_seconds=elapsed,
        model=resolved_model,
        thinking="".join(thinking_parts),
        error=None if final_content else "MCP loop exhausted without final answer",
        provider_metrics={
            "provider": "ollama_native_chat_mcp_loop",
            "prompt_eval_count": total_prompt_tokens,
            "eval_count": total_completion_tokens,
            **total_metrics,
        },
    )
    trajectory = _trajectory_from_messages(inspect_messages, final_answer_raw=final_content)
    trajectory.server_prompt_used = bool(server_prompt_text.strip())
    trajectory.server_prompt_name = server_prompt_name if server_prompt_text.strip() else ""
    trajectory.available_prompt_names = sorted(available_prompt_names or [])
    trajectory.prompt_discovery_status = prompt_discovery_status
    trajectory.resource_mode = resource_mode
    trajectory.tool_loop = MCP_TOOL_LOOP_NATIVE_OLLAMA
    return model_response, trajectory, inspect_messages


async def _run_openai_compat_mcp_loop(
    *,
    task: Task,
    model_name: str,
    base_url: str | None,
    extra_body: dict[str, Any] | None,
    tools: list[Any],
    max_steps: int,
    server_prompt_text: str = "",
    server_prompt_name: str = "",
    available_prompt_names: list[str] | None = None,
    prompt_discovery_status: str = "",
    resource_mode: str = RESOURCE_MODE_OFF,
    telemetry_adapter: str = OPENAI_COMPAT_TELEMETRY_AUTO,
    read_timeout_seconds: float = DEFAULT_MCP_OLLAMA_READ_TIMEOUT_SECONDS,
) -> tuple[ModelResponse, MCPRunMetadata, list[Any]]:
    url = _openai_compat_chat_url(base_url, model_name)
    resolved_telemetry_adapter = _infer_openai_compat_telemetry_adapter(
        telemetry_adapter,
        base_url=base_url,
        model_name=model_name,
    )
    messages_payload: list[dict[str, Any]] = []
    if server_prompt_text.strip():
        messages_payload.append({"role": "system", "content": server_prompt_text})
    messages_payload.append({"role": "system", "content": _mcp_system_prompt(task)})
    messages_payload.append({"role": "user", "content": task.question})

    inspect_messages = _mcp_conversation_messages(
        task,
        server_prompt_text=server_prompt_text,
        server_prompt_name=server_prompt_name,
    )
    tool_specs: list[dict[str, Any]] = []
    tool_runners: dict[str, Any] = {}
    for tool_obj in tools:
        spec, executor = _ollama_tool_spec(tool_obj)
        tool_specs.append(spec)
        tool_runners[spec["function"]["name"]] = executor

    total_prompt_tokens = 0
    total_completion_tokens = 0
    turn_provider_metrics: list[dict[str, Any]] = []
    finish_reasons: list[str] = []
    thinking_parts: list[str] = []
    final_content = ""
    resolved_model = _openai_compat_model_name(model_name)
    t0 = time.monotonic()

    for step in range(max_steps):
        turn = await _openai_compat_chat_turn(
            url=url,
            model_name=model_name,
            messages=messages_payload,
            tools=tool_specs,
            extra_body=extra_body,
            telemetry_adapter=resolved_telemetry_adapter,
            read_timeout_seconds=read_timeout_seconds,
        )
        total_prompt_tokens += int(turn["prompt_tokens"])
        total_completion_tokens += int(turn["completion_tokens"])
        turn_provider_metrics.append(dict(turn.get("provider_metrics") or {}))
        finish_reason = str(turn.get("finish_reason") or "")
        if finish_reason:
            finish_reasons.append(finish_reason)
        resolved_model = str(turn["model"] or resolved_model)
        thinking = str(turn.get("thinking") or "")
        if thinking:
            thinking_parts.append(thinking)
        content = str(turn["content"] or "")
        raw_tool_calls = list(turn["tool_calls"] or [])

        inspect_tool_calls: list[ToolCall] = []
        normalized_tool_calls: list[dict[str, Any]] = []
        for idx, call in enumerate(raw_tool_calls):
            function = dict(call.get("function") or {})
            name = str(function.get("name") or "")
            arguments = _tool_call_arguments(function.get("arguments"))
            call_id = str(call.get("id") or f"openai-compat-call-{step + 1}-{idx + 1}")
            normalized_tool_calls.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }
            )
            inspect_tool_calls.append(ToolCall(id=call_id, function=name, arguments=arguments))

        payload_assistant: dict[str, Any] = {"role": "assistant", "content": content}
        if normalized_tool_calls:
            payload_assistant["tool_calls"] = normalized_tool_calls
        messages_payload.append(payload_assistant)
        inspect_messages.append(
            ChatMessageAssistant(
                content=content,
                tool_calls=inspect_tool_calls or None,
                model=resolved_model,
            )
        )

        if not raw_tool_calls:
            final_content = content
            break

        for tool_call in inspect_tool_calls:
            tool_name = tool_call.function
            tool_runner = tool_runners.get(tool_name)
            result_text = ""
            tool_error: ToolCallError | None = None
            try:
                if tool_runner is None:
                    raise RuntimeError(f"Unknown tool: {tool_name}")
                result = tool_runner(**tool_call.arguments)
                if inspect.isawaitable(result):
                    result = await result
                result_text = _tool_result_to_text(result)
            except Exception as exc:
                result_text = json.dumps(
                    {"success": False, "error": str(exc), "error_type": "tool_error"}
                )
                tool_error = ToolCallError(type="unknown", message=str(exc))

            messages_payload.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": tool_name,
                    "content": result_text,
                }
            )
            inspect_messages.append(
                ChatMessageTool(
                    content=result_text,
                    tool_call_id=tool_call.id,
                    function=tool_name,
                    error=tool_error,
                )
            )
    else:
        final_content = ""

    elapsed = time.monotonic() - t0
    model_response = ModelResponse(
        raw_text=final_content,
        cypher=None,
        parse_stage="mcp_final_answer" if final_content else "none",
        tokens_input=total_prompt_tokens,
        tokens_output=total_completion_tokens,
        elapsed_seconds=elapsed,
        model=resolved_model,
        thinking="\n".join(thinking_parts),
        error=None if final_content else "MCP loop exhausted without final answer",
        provider_metrics={
            "provider": "openai_compat_native_chat_mcp_loop",
            "telemetry_adapter": resolved_telemetry_adapter,
            "prompt_tokens": total_prompt_tokens,
            "completion_tokens": total_completion_tokens,
            "finish_reasons": finish_reasons,
            "turn_metrics": turn_provider_metrics,
        },
    )
    trajectory = _trajectory_from_messages(inspect_messages, final_answer_raw=final_content)
    trajectory.server_prompt_used = bool(server_prompt_text.strip())
    trajectory.server_prompt_name = server_prompt_name if server_prompt_text.strip() else ""
    trajectory.available_prompt_names = sorted(available_prompt_names or [])
    trajectory.prompt_discovery_status = prompt_discovery_status
    trajectory.resource_mode = resource_mode
    trajectory.tool_loop = MCP_TOOL_LOOP_NATIVE_OPENAI_COMPAT
    return model_response, trajectory, inspect_messages


def _mock_mcp_answer(
    task: Task, ref_result: CypherResult, model_name: str
) -> tuple[str, dict[str, Any] | None]:
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


def _exception_detail(exc: BaseException) -> str:
    message = str(exc).strip()
    exc_type = type(exc).__name__
    return f"{exc_type}: {message}" if message else exc_type


def _fallback_mcp_state(
    *,
    model_name: str,
    messages: list[Any],
    detail: str,
    subtype: str,
    tool_loop: str,
    server_prompt_text: str,
    server_prompt_name: str,
    available_prompt_names: list[str],
    prompt_discovery_status: str,
    resource_mode: str,
    elapsed_seconds: float,
) -> tuple[ModelResponse, MCPRunMetadata, list[Any]]:
    model_response = ModelResponse(
        raw_text="",
        cypher=None,
        parse_stage="none",
        tokens_input=0,
        tokens_output=0,
        elapsed_seconds=elapsed_seconds,
        model=model_name,
        thinking="",
        error=detail,
        provider_metrics={"infra_error_subtype": subtype, "tool_loop": tool_loop},
    )
    trajectory = _trajectory_from_messages(messages, final_answer_raw="")
    trajectory.server_prompt_used = bool(server_prompt_text.strip())
    trajectory.server_prompt_name = server_prompt_name if server_prompt_text.strip() else ""
    trajectory.available_prompt_names = sorted(available_prompt_names or [])
    trajectory.prompt_discovery_status = prompt_discovery_status
    trajectory.resource_mode = resource_mode
    trajectory.tool_loop = tool_loop
    trajectory.infra_error_subtype = subtype
    return model_response, trajectory, messages


def _persist_mcp_state(
    state: TaskState,
    *,
    model_response: ModelResponse,
    trajectory: MCPRunMetadata,
    task_t0: float,
    model_calls: int = 1,
) -> None:
    state.store.set("ori_model_response", _model_response_to_dict(model_response))
    state.store.set("ori_mcp_trajectory", _mcp_metadata_to_dict(trajectory))
    state.store.set("ori_model_calls", model_calls)
    state.store.set("ori_task_wall_seconds", time.monotonic() - task_t0)


def _metadata_task_id(metadata: dict[str, Any]) -> str:
    task_data = metadata.get("ori_task") or {}
    if isinstance(task_data, dict):
        return str(task_data.get("id") or "?")
    return "?"


def _incomplete_mcp_score(
    *,
    metadata: dict[str, Any],
    model_response: ModelResponse | None,
    mcp_meta: MCPRunMetadata | None,
    missing_keys: list[str],
    subtype: str,
    detail: str | None = None,
) -> Score:
    task_id = _metadata_task_id(metadata)
    sample_index = metadata.get("sample_index", "?")
    sample_total = metadata.get("sample_total", "?")
    requested_model = metadata.get("requested_model", "")
    missing_detail = ", ".join(f"missing {key}" for key in missing_keys)
    diagnostic = detail or f"MCP sample incomplete: {missing_detail}"
    details = (
        f"{diagnostic}; subtype={subtype}; task_id={task_id}; "
        f"sample={sample_index}/{sample_total}"
    )
    if model_response is None:
        model_response = ModelResponse(
            raw_text="",
            cypher=None,
            parse_stage="none",
            tokens_input=0,
            tokens_output=0,
            elapsed_seconds=0.0,
            model=str(requested_model),
            thinking="",
            error=details,
            provider_metrics={"infra_error_subtype": subtype},
        )
    if mcp_meta is None:
        mcp_meta = MCPRunMetadata(final_answer_raw=model_response.raw_text)
    mcp_meta.infra_error_subtype = subtype
    result = GradeResult(
        score=0.0,
        outcome="INFRA_ERROR",
        hallucination=False,
        details=details,
    )
    print(f"  [{sample_index}/{sample_total}] {task_id} → INFRA_ERROR ({details})")
    return Score(
        value=0.0,
        answer=model_response.raw_text,
        explanation=details,
        metadata={
            "grade": _grade_result_to_dict(result),
            "mcp": _mcp_metadata_to_dict(mcp_meta),
        },
    )


@solver
def ori_mcp_solver(
    tools: list[Any],
    *,
    max_steps: int = 12,
    server_prompt_text: str = "",
    server_prompt_name: str = "",
    available_prompt_names: list[str] | None = None,
    prompt_discovery_status: str = "",
    resource_mode: str = RESOURCE_MODE_OFF,
    mcp_tool_loop: str = MCP_TOOL_LOOP_AUTO,
    openai_compat_telemetry_adapter: str = OPENAI_COMPAT_TELEMETRY_AUTO,
    ollama_read_timeout_seconds: float = DEFAULT_MCP_OLLAMA_READ_TIMEOUT_SECONDS,
) -> Generate:
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        task_t0 = time.monotonic()
        metadata = state.metadata
        task = _task_from_dict(metadata["ori_task"])
        ref_result = _cypher_result_from_dict(metadata["ref_result"])
        model_name = metadata.get("requested_model", str(state.model))
        resolved_tool_loop = (
            "mock"
            if model_name.startswith("mock/mcp_")
            else _resolve_mcp_tool_loop(model_name, mcp_tool_loop)
        )

        prompt_names = sorted(available_prompt_names or [])
        state.messages = _mcp_conversation_messages(
            task,
            server_prompt_text=server_prompt_text,
            server_prompt_name=server_prompt_name,
        )
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
                thinking="",
                error=None,
            )
            state.output = ModelOutput.from_content(model=model_name, content=completion)
            trajectory = _trajectory_from_messages(state.messages, final_answer_raw=completion)
            trajectory.server_prompt_used = bool(server_prompt_text.strip())
            trajectory.server_prompt_name = server_prompt_name if server_prompt_text.strip() else ""
            trajectory.available_prompt_names = prompt_names
            trajectory.prompt_discovery_status = prompt_discovery_status
            trajectory.resource_mode = resource_mode
            trajectory.tool_loop = "mock"
        elif resolved_tool_loop == MCP_TOOL_LOOP_NATIVE_OLLAMA:
            try:
                model_response, trajectory, ollama_messages = await _run_ollama_mcp_loop(
                    task=task,
                    model_name=model_name,
                    base_url=metadata.get("model_base_url"),
                    ollama_options=metadata.get("ollama_options"),
                    tools=tools,
                    max_steps=max_steps,
                    server_prompt_text=server_prompt_text,
                    server_prompt_name=server_prompt_name,
                    available_prompt_names=prompt_names,
                    prompt_discovery_status=prompt_discovery_status,
                    resource_mode=resource_mode,
                    ollama_read_timeout_seconds=ollama_read_timeout_seconds,
                )
            except asyncio.CancelledError as exc:
                detail = f"MCP native Ollama sample cancelled: {_exception_detail(exc)}"
                model_response, trajectory, ollama_messages = _fallback_mcp_state(
                    model_name=model_name,
                    messages=state.messages,
                    detail=detail,
                    subtype="sample_cancelled",
                    tool_loop=MCP_TOOL_LOOP_NATIVE_OLLAMA,
                    server_prompt_text=server_prompt_text,
                    server_prompt_name=server_prompt_name,
                    available_prompt_names=prompt_names,
                    prompt_discovery_status=prompt_discovery_status,
                    resource_mode=resource_mode,
                    elapsed_seconds=time.monotonic() - task_t0,
                )
                state.messages = ollama_messages
                state.output = ModelOutput.from_content(
                    model=model_response.model,
                    content="",
                    error=model_response.error,
                )
                _persist_mcp_state(
                    state,
                    model_response=model_response,
                    trajectory=trajectory,
                    task_t0=task_t0,
                    model_calls=0,
                )
                raise
            except Exception as exc:
                detail = f"MCP native Ollama solver exception: {_exception_detail(exc)}"
                model_response, trajectory, ollama_messages = _fallback_mcp_state(
                    model_name=model_name,
                    messages=state.messages,
                    detail=detail,
                    subtype="solver_exception",
                    tool_loop=MCP_TOOL_LOOP_NATIVE_OLLAMA,
                    server_prompt_text=server_prompt_text,
                    server_prompt_name=server_prompt_name,
                    available_prompt_names=prompt_names,
                    prompt_discovery_status=prompt_discovery_status,
                    resource_mode=resource_mode,
                    elapsed_seconds=time.monotonic() - task_t0,
                )
            state.messages = ollama_messages
            state.output = ModelOutput.from_content(
                model=model_response.model,
                content=model_response.raw_text,
                error=model_response.error,
            )
        elif resolved_tool_loop == MCP_TOOL_LOOP_NATIVE_OPENAI_COMPAT:
            model_response, trajectory, openai_messages = await _run_openai_compat_mcp_loop(
                task=task,
                model_name=model_name,
                base_url=metadata.get("model_base_url"),
                extra_body=metadata.get("ollama_options"),
                tools=tools,
                max_steps=max_steps,
                server_prompt_text=server_prompt_text,
                server_prompt_name=server_prompt_name,
                available_prompt_names=prompt_names,
                prompt_discovery_status=prompt_discovery_status,
                resource_mode=resource_mode,
                telemetry_adapter=openai_compat_telemetry_adapter,
                read_timeout_seconds=ollama_read_timeout_seconds,
            )
            state.messages = openai_messages
            state.output = ModelOutput.from_content(
                model=model_response.model,
                content=model_response.raw_text,
                error=model_response.error,
            )
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
                    thinking="",
                    error=output.error,
                    provider_metrics={},
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
                    thinking="",
                    error=str(exc),
                    provider_metrics={},
                )
                state.output = ModelOutput.from_content(
                    model=model_name, content="", error=str(exc)
                )

        parsed = _parse_json_object(model_response.raw_text)
        normalized = _normalize_final_answer(parsed, task)
        if resolved_tool_loop == MCP_TOOL_LOOP_INSPECT and not model_name.startswith("mock/mcp_"):
            trajectory = _trajectory_from_messages(
                state.messages, final_answer_raw=model_response.raw_text
            )
            trajectory.server_prompt_used = bool(server_prompt_text.strip())
            trajectory.server_prompt_name = server_prompt_name if server_prompt_text.strip() else ""
            trajectory.available_prompt_names = prompt_names
            trajectory.prompt_discovery_status = prompt_discovery_status
            trajectory.resource_mode = resource_mode
            trajectory.tool_loop = MCP_TOOL_LOOP_INSPECT
        trajectory.final_answer_normalized = normalized
        _persist_mcp_state(
            state,
            model_response=model_response,
            trajectory=trajectory,
            task_t0=task_t0,
        )
        return state

    return solve


@scorer(metrics=[accuracy(), stderr()])
def ori_mcp_scorer():
    async def score(state: TaskState, target: Any) -> Score:
        metadata = state.metadata
        task = _task_from_dict(metadata["ori_task"])
        ref_result = _cypher_result_from_dict(metadata["ref_result"])
        valid_names = set(metadata.get("valid_node_names", []))
        model_response_data = state.store.get("ori_model_response")
        mcp_meta_data = state.store.get("ori_mcp_trajectory")
        missing_keys = [
            key
            for key, data in (
                ("ori_model_response", model_response_data),
                ("ori_mcp_trajectory", mcp_meta_data),
            )
            if not isinstance(data, dict)
        ]
        if missing_keys:
            model_response = (
                _model_response_from_dict(model_response_data)
                if isinstance(model_response_data, dict)
                else None
            )
            mcp_meta = (
                _mcp_metadata_from_dict(mcp_meta_data) if isinstance(mcp_meta_data, dict) else None
            )
            subtype = (
                "missing_model_response_and_mcp_trajectory"
                if len(missing_keys) == 2
                else "missing_model_response"
                if missing_keys == ["ori_model_response"]
                else "missing_mcp_trajectory"
            )
            return _incomplete_mcp_score(
                metadata=metadata,
                model_response=model_response,
                mcp_meta=mcp_meta,
                missing_keys=missing_keys,
                subtype=subtype,
            )
        model_response = _model_response_from_dict(model_response_data)
        mcp_meta = _mcp_metadata_from_dict(mcp_meta_data)
        if mcp_meta.infra_error_subtype in {"sample_cancelled", "solver_exception"}:
            return _incomplete_mcp_score(
                metadata=metadata,
                model_response=model_response,
                mcp_meta=mcp_meta,
                missing_keys=[],
                subtype=mcp_meta.infra_error_subtype,
                detail=model_response.error or "MCP sample failed before final answer",
            )

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
            f"           → {result.outcome} (score={result.score}, tools={mcp_meta.tool_calls_total}, "  # noqa: E501
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


def _sample_error_detail(sample: EvalSample, fallback: str) -> str:
    error = getattr(sample, "error", None)
    if error:
        message = getattr(error, "message", None)
        return str(message or error)
    return fallback


def _inspect_meta_from_sample(sample: EvalSample, log: EvalLog) -> InspectEvalMetadata:
    return InspectEvalMetadata(
        log_location=log.location or None,
        sample_id=str(sample.id),
        sample_uuid=sample.uuid,
        model_calls=int(sample.store.get("ori_model_calls", 0)),
        error_retries=len(sample.error_retries or []),
    )


def _incomplete_result_from_sample(
    sample: EvalSample,
    log: EvalLog,
    detail: str,
    subtype: str = "incomplete_sample",
):
    from .runner import EvalResult

    task = _task_from_dict(sample.metadata["ori_task"])
    model_response_data = sample.store.get("ori_model_response")
    model_response = (
        _model_response_from_dict(model_response_data)
        if model_response_data
        else ModelResponse(
            raw_text="",
            cypher=None,
            parse_stage="none",
            tokens_input=0,
            tokens_output=0,
            elapsed_seconds=0.0,
            model=sample.metadata.get("requested_model", ""),
            thinking="",
            error=detail,
            provider_metrics={},
        )
    )
    mcp_meta = _mcp_metadata_from_dict(sample.store.get("ori_mcp_trajectory", {}))
    mcp_meta.trajectory_log = log.location or ""
    mcp_meta.infra_error_subtype = subtype
    return EvalResult(
        task=task,
        model_response=model_response,
        grade=GradeResult(
            score=0.0,
            outcome="INFRA_ERROR",
            hallucination=False,
            details=f"Inspect MCP sample incomplete: subtype={subtype}; {detail}",
        ),
        ref_result=_cypher_result_from_dict(sample.metadata["ref_result"]),
        model_result=CypherResult(success=False, error=detail),
        inspect=_inspect_meta_from_sample(sample, log),
        mcp=mcp_meta,
        task_wall_seconds=float(
            sample.store.get("ori_task_wall_seconds", model_response.elapsed_seconds)
        ),
    )


def _result_from_sample(sample: EvalSample, log: EvalLog):
    from .runner import EvalResult

    task = _task_from_dict(sample.metadata["ori_task"])
    if "ori_model_response" not in sample.store:
        detail = _sample_error_detail(sample, "missing ori_model_response")
        return _incomplete_result_from_sample(sample, log, detail, "missing_model_response")
    if "ori_mcp_trajectory" not in sample.store:
        detail = _sample_error_detail(sample, "missing ori_mcp_trajectory")
        return _incomplete_result_from_sample(sample, log, detail, "missing_mcp_trajectory")
    model_response = _model_response_from_dict(sample.store["ori_model_response"])
    if not sample.scores:
        detail = _sample_error_detail(sample, f"Inspect MCP sample {sample.id!r} missing scores")
        sample_subtype = sample.store.get("ori_mcp_trajectory", {}).get("infra_error_subtype")
        subtype = (
            sample_subtype
            if sample_subtype in {"sample_cancelled", "solver_exception"}
            else "sample_missing_scores"
        )
        return _incomplete_result_from_sample(sample, log, detail, subtype)
    score = next(iter(sample.scores.values()))
    grade_result = _score_metadata_to_grade_result(score)
    score_metadata = score.metadata or {}
    if "mcp" not in score_metadata:
        detail = _sample_error_detail(sample, f"Inspect MCP sample {sample.id!r} missing MCP score")
        return _incomplete_result_from_sample(sample, log, detail, "missing_mcp_score_metadata")
    mcp_meta = _mcp_metadata_from_dict(score_metadata["mcp"])
    mcp_meta.trajectory_log = log.location or ""
    inspect_meta = _inspect_meta_from_sample(sample, log)
    return EvalResult(
        task=task,
        model_response=model_response,
        grade=grade_result,
        ref_result=_cypher_result_from_dict(sample.metadata["ref_result"]),
        model_result=CypherResult(success=True, nodes=[], node_names=set(), raw={}),
        inspect=inspect_meta,
        mcp=mcp_meta,
        task_wall_seconds=float(
            sample.store.get("ori_task_wall_seconds", model_response.elapsed_seconds)
        ),
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
    mcp_tool_loop: str = MCP_TOOL_LOOP_AUTO,
    openai_compat_telemetry_adapter: str = OPENAI_COMPAT_TELEMETRY_AUTO,
    ollama_read_timeout_seconds: float = DEFAULT_MCP_OLLAMA_READ_TIMEOUT_SECONDS,
):
    if resource_mode not in {RESOURCE_MODE_OFF, RESOURCE_MODE_ON_DEMAND}:
        raise ValueError(f"Unsupported resource mode: {resource_mode!r}")
    resolved_tool_loop = (
        "mock" if model.startswith("mock/mcp_") else _resolve_mcp_tool_loop(model, mcp_tool_loop)
    )
    resolved_openai_compat_telemetry_adapter = _normalize_openai_compat_telemetry_adapter(
        openai_compat_telemetry_adapter
    )

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
    inspect_model = (
        model
        if (
            _inspect_supported_model(model)
            and resolved_tool_loop == MCP_TOOL_LOOP_INSPECT
            and not model.startswith("mock/mcp_")
        )
        else "none/none"
    )
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
    available_prompt_names: list[str] = []
    prompt_discovery_status = "not_requested"
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
        available_prompt_names = bundle.available_prompt_names
        prompt_discovery_status = bundle.prompt_discovery_status
        if server_prompt_name:
            print(
                f"Loaded BloodHound MCP prompt: {server_prompt_name} "
                f"({len(server_prompt_text)} chars)"
            )
        elif prompt_discovery_status != "selected":
            print(f"MCP prompt discovery status: {prompt_discovery_status}")
        if resource_mode == RESOURCE_MODE_ON_DEMAND:
            print("Resource mode: on-demand")
        print(f"MCP tool loop: {resolved_tool_loop}")
        if resolved_tool_loop == MCP_TOOL_LOOP_NATIVE_OPENAI_COMPAT:
            print(
                "OpenAI-compatible telemetry adapter: "
                f"{resolved_openai_compat_telemetry_adapter}"
            )

    inspect_task = InspectTask(
        dataset=samples,
        solver=ori_mcp_solver(
            tools=tools,
            max_steps=max_steps,
            server_prompt_text=server_prompt_text,
            server_prompt_name=server_prompt_name,
            available_prompt_names=available_prompt_names,
            prompt_discovery_status=prompt_discovery_status,
            resource_mode=resource_mode,
            mcp_tool_loop=mcp_tool_loop,
            openai_compat_telemetry_adapter=resolved_openai_compat_telemetry_adapter,
            ollama_read_timeout_seconds=ollama_read_timeout_seconds,
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
        extra_body={"options": ollama_options}
        if ollama_options and inspect_model.startswith("ollama/")
        else None,
    )
    if not eval_logs:
        raise RuntimeError("Inspect MCP eval returned no logs")
    log = eval_logs[0]
    if not log.samples:
        raise RuntimeError("Inspect MCP eval log did not contain sample results")
    return [_result_from_sample(sample, log) for sample in log.samples]
