"""Inspect AI-backed runtime for Phase 3B MCP evaluation."""

from __future__ import annotations

import asyncio
import builtins
import hashlib
import inspect
import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Any

import httpx
from inspect_ai import Task as InspectTask
from inspect_ai import eval_async as inspect_eval_async
from inspect_ai._util.registry import registry_info
from inspect_ai.dataset import Sample
from inspect_ai.log import EvalLog, EvalSample, read_eval_log
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

from ori.mcp_launcher import (
    MCPLauncherConfig,
    MCPLauncherRuntime,
    MCPLaunchSpec,
    build_mcp_launch_spec,
    resolve_mcp_launcher_runtime,
)

from .adapter import ModelResponse
from .bhce import BHCEClient, CypherResult
from .grader import GradeResult, grade_mcp_diagnostic
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
from .provider_auth import (
    openai_compat_endpoint_is_local,
    resolve_openai_compat_credential,
)
from .provider_contract import (
    ProviderApiSurface,
    ProviderAuthenticationError,
    ProviderProtocolError,
    ToolArgumentParseStatus,
    normalize_chat_completion,
)
from .tasks import Task

RESOURCE_MODE_OFF = "off"
RESOURCE_MODE_ON_DEMAND = "on-demand"
BLOODHOUND_PROMPT_NAME = "bloodhound_assistant"
RESOURCE_LIST_TOOL_NAME = "list_bloodhound_resources"
RESOURCE_READ_TOOL_NAME = "read_bloodhound_resource"
DEFAULT_MCP_OLLAMA_READ_TIMEOUT_SECONDS = 900.0
DEFAULT_MCP_NO_PROGRESS_TIMEOUT_SECONDS = float(
    os.getenv("ORI_MCP_NO_PROGRESS_TIMEOUT_SECONDS", "300")
)
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
_ORIGINAL_PRINT = builtins.print


def _safe_print(*args: Any, **kwargs: Any) -> None:
    """Best-effort CLI progress output that cannot fail an eval sample.

    Inspect scorers may run under background launchers or pipes whose stdout closes
    before sample scoring finishes. A raw print() can then raise BrokenPipeError and
    turn an otherwise valid sample into an eval failure.
    """
    try:
        _ORIGINAL_PRINT(*args, **kwargs)
    except BrokenPipeError:
        return


def _load_last_eval_log_from_runtime_dir(
    log_dir: Path,
    *,
    started_at_monotonic: float,
    expected_sample_count: int,
) -> EvalLog | None:
    """Best-effort recovery for Inspect TaskGroup shutdown/SystemExit bugs.

    Inspect can successfully write the .eval artifact and then still raise a
    BaseExceptionGroup/SystemExit during display/task shutdown. In that case the
    benchmark data is valid but the caller never reaches CSV/report writing.
    The runtime `view/last-eval-result` pointer is the safest recovery handle.
    Recovery is deliberately tied to this invocation so a stale successful
    artifact cannot mask a real new failure.
    """
    pointer = log_dir / "_inspect_runtime" / "view" / "last-eval-result"
    try:
        data = json.loads(pointer.read_text())
        location = data.get("location")
        if not location:
            return None
        root = log_dir.resolve()
        path = Path(location).resolve()
        if not path.exists() or not path.is_relative_to(root):
            return None
        # `st_mtime` is wall-clock seconds; derive the invocation wall-clock start
        # from the monotonic start so clock changes during a long eval do not matter.
        invocation_started_at = time.time() - (time.monotonic() - started_at_monotonic)
        if (
            path.stat().st_mtime < invocation_started_at
            or pointer.stat().st_mtime < invocation_started_at
        ):
            return None
        log = read_eval_log(str(path))
    except Exception:
        return None
    if log.status != "success" or log.error is not None or not log.samples:
        return None
    if len(log.samples) != expected_sample_count:
        return None
    if log.results and log.results.completed_samples != expected_sample_count:
        return None
    return log


def _exception_group_contains_only_system_exit(exc: BaseExceptionGroup) -> bool:
    """Return true only for Inspect's observed shutdown-only SystemExit group."""
    leaves: list[BaseException] = []

    def collect(error: BaseException) -> None:
        if isinstance(error, BaseExceptionGroup):
            for child in error.exceptions:
                collect(child)
        else:
            leaves.append(error)

    collect(exc)
    return bool(leaves) and all(isinstance(error, SystemExit) for error in leaves)


async def _inspect_eval_async_safe_print(*args: Any, **kwargs: Any) -> list[EvalLog]:
    """Run Inspect with builtins.print guarded against closed stdout pipes."""
    previous_print = builtins.print
    builtins.print = _safe_print
    try:
        return await inspect_eval_async(*args, **kwargs)
    finally:
        builtins.print = previous_print


async def _inspect_eval_async_with_artifact_recovery(
    *args: Any,
    recovery_log_dir: Path,
    expected_sample_count: int,
    **kwargs: Any,
) -> list[EvalLog]:
    """Run Inspect and recover only from the known post-success SystemExit shutdown bug."""
    started_at_monotonic = time.monotonic()
    try:
        return await _inspect_eval_async_safe_print(*args, **kwargs)
    except BaseExceptionGroup as exc:
        if not _exception_group_contains_only_system_exit(exc):
            raise
        recovered_log = _load_last_eval_log_from_runtime_dir(
            recovery_log_dir,
            started_at_monotonic=started_at_monotonic,
            expected_sample_count=expected_sample_count,
        )
        if recovered_log:
            _safe_print(
                "Recovered Inspect MCP eval results from last-eval-result after "
                "Inspect shutdown SystemExit"
            )
            return [recovered_log]
        raise
    except SystemExit:
        recovered_log = _load_last_eval_log_from_runtime_dir(
            recovery_log_dir,
            started_at_monotonic=started_at_monotonic,
            expected_sample_count=expected_sample_count,
        )
        if recovered_log:
            _safe_print("Recovered Inspect MCP eval results from last-eval-result after SystemExit")
            return [recovered_log]
        raise


class MCPNoProgressTimeout(TimeoutError):
    """Raised when an MCP/model turn makes no observable progress before its watchdog expires."""

    def __init__(self, *, subtype: str, scope: str, timeout_seconds: float) -> None:
        self.subtype = subtype
        self.scope = scope
        self.timeout_seconds = timeout_seconds
        super().__init__(
            f"{subtype}: no progress observed for {timeout_seconds:.1f}s during MCP {scope}"
        )


class MCPToolInfrastructureError(RuntimeError):
    """Raised after preserving a failed MCP tool receipt for infrastructure faults."""

    def __init__(self, *, subtype: str, detail: str) -> None:
        self.subtype = subtype
        super().__init__(f"{subtype}: {detail}")


def _mcp_tool_infrastructure_subtype(exc: Exception) -> str | None:
    """Classify transport failures without treating model/tool defects as infra."""

    if isinstance(exc, httpx.TimeoutException):
        return "MCP_TOOL_TIMEOUT"
    if isinstance(exc, httpx.RequestError):
        return "MCP_TOOL_TRANSPORT"
    if isinstance(exc, (ConnectionError, BrokenPipeError, EOFError)):
        return "MCP_TOOL_TRANSPORT"
    name = type(exc).__name__.casefold()
    if "timeout" in name:
        return "MCP_TOOL_TIMEOUT"
    if any(
        marker in name
        for marker in (
            "connection",
            "disconnect",
            "transport",
            "brokenresource",
            "closedresource",
            "endofstream",
        )
    ):
        return "MCP_TOOL_TRANSPORT"
    return None


async def _execute_mcp_tool(
    runner: Any,
    arguments: dict[str, Any],
    *,
    timeout_seconds: float | None,
) -> Any:
    """Execute one tool with an optional V2-only sub-deadline."""

    result = runner(**arguments)
    if not inspect.isawaitable(result):
        return result
    if timeout_seconds is None:
        return await result
    try:
        return await asyncio.wait_for(result, timeout=timeout_seconds)
    except TimeoutError as exc:
        raise MCPToolInfrastructureError(
            subtype="MCP_TOOL_TIMEOUT",
            detail=f"tool exceeded its {timeout_seconds:.1f}s sub-deadline",
        ) from exc


def _raise_if_no_progress(
    *, last_activity: float, now: float, timeout_seconds: float | None, scope: str
) -> None:
    if not timeout_seconds or timeout_seconds <= 0:
        return
    if now - last_activity <= timeout_seconds:
        return
    subtype = "MCP_TURN_TIMEOUT" if scope == "turn" else "NO_PROGRESS_TIMEOUT"
    raise MCPNoProgressTimeout(subtype=subtype, scope=scope, timeout_seconds=timeout_seconds)


@dataclass
class MCPServerBundle:
    tools: list[Any]
    server_prompt_text: str = ""
    server_prompt_name: str = ""
    available_prompt_names: list[str] = field(default_factory=list)
    prompt_discovery_status: str = "not_requested"
    available_resource_uris: list[str] = field(default_factory=list)
    resource_discovery_status: str = "not_requested"
    launcher_provenance: dict[str, str | None] = field(default_factory=dict)


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
    available_resource_uris: list[str] = field(default_factory=list)
    resource_discovery_status: str = ""
    resource_mode: str = RESOURCE_MODE_OFF
    tool_loop: str = MCP_TOOL_LOOP_AUTO
    resource_reads_total: int = 0
    unique_resources_used: list[str] = field(default_factory=list)
    resource_characters_total: int = 0
    infra_error_subtype: str = ""
    failure_subtype: str = ""
    successful_tool_results: int = 0
    finalization_guard_used: bool = False
    loop_exhaustion_with_evidence: bool = False
    minimum_evidence_satisfied: bool = False
    final_answer_diagnostics: dict[str, Any] = field(default_factory=dict)
    repair_turn_used: bool = False
    mcp_launcher: str = ""
    mcp_source: str = ""
    mcp_revision: str = ""
    mcp_executable: str = ""
    uv_version: str = ""

    @property
    def final_answer_normalized_json(self) -> str:
        if self.final_answer_normalized is None:
            return ""
        return json.dumps(self.final_answer_normalized, sort_keys=True)


def _apply_mcp_runtime_metadata(
    metadata: MCPRunMetadata,
    *,
    available_resource_uris: list[str],
    resource_discovery_status: str,
    launcher_provenance: dict[str, str | None],
) -> None:
    metadata.available_resource_uris = sorted(available_resource_uris)
    metadata.resource_discovery_status = resource_discovery_status
    metadata.mcp_launcher = str(launcher_provenance.get("mcp_launcher") or "")
    metadata.mcp_source = str(launcher_provenance.get("mcp_source") or "")
    metadata.mcp_revision = str(launcher_provenance.get("mcp_revision") or "")
    metadata.mcp_executable = str(launcher_provenance.get("mcp_executable") or "")
    metadata.uv_version = str(launcher_provenance.get("uv_version") or "")


@dataclass(frozen=True)
class MCPReadinessResult:
    """No-model evidence that a configured MCP launcher is usable and read-only."""

    launcher_provenance: dict[str, str | None]
    prompt_discovery_status: str
    available_prompt_names: list[str]
    resource_discovery_status: str
    available_resource_uris: list[str]
    read_only_tools: list[str]
    checks: dict[str, dict[str, Any]]
    captured_at: str

    @property
    def ok(self) -> bool:
        required_checks = {
            "startup_credential_preflight",
            "data_quality",
            "domain_info",
            "graph_analysis",
            "cypher_query",
            RESOURCE_LIST_TOOL_NAME,
        }
        return (
            self.prompt_discovery_status == "selected"
            and self.resource_discovery_status == "listed"
            and bool(self.available_resource_uris)
            and required_checks.issubset(self.checks)
            and all(self.checks[name].get("status") == "passed" for name in required_checks)
        )

    def artifact(self) -> dict[str, Any]:
        core = {
            "artifact_type": "ori.mcp_runtime_readiness",
            "schema_version": 1,
            "captured_at": self.captured_at,
            "provenance": dict(self.launcher_provenance),
            "prompt_discovery": {
                "status": self.prompt_discovery_status,
                "succeeded": self.prompt_discovery_status == "selected",
                "available_prompt_names": sorted(self.available_prompt_names),
            },
            "resource_discovery": {
                "status": self.resource_discovery_status,
                "succeeded": self.resource_discovery_status == "listed",
                "available_resource_uris": sorted(self.available_resource_uris),
            },
            "read_only_tools": sorted(self.read_only_tools),
            "checks": self.checks,
            "ready": self.ok,
        }
        capability_material = json.dumps(
            {
                "provenance": core["provenance"],
                "read_only_tools": core["read_only_tools"],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        capability_fingerprint = hashlib.sha256(capability_material).hexdigest()
        revision = str(self.launcher_provenance.get("mcp_revision") or "local")
        core["capability_identity"] = f"ori-mcp-{revision[:12]}-{capability_fingerprint[:12]}"
        core["capability_fingerprint"] = capability_fingerprint
        readiness_material = json.dumps(
            core,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        core["readiness_fingerprint"] = hashlib.sha256(readiness_material).hexdigest()
        return core


def write_mcp_readiness_artifact(result: MCPReadinessResult, output_path: Path) -> dict[str, Any]:
    """Create an immutable readiness receipt; never overwrite mismatched evidence."""

    artifact = result.artifact()
    serialized = json.dumps(artifact, indent=2, sort_keys=True) + "\n"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        if output_path.read_text(encoding="utf-8") != serialized:
            raise FileExistsError(
                f"Refusing to overwrite mismatched MCP readiness artifact: {output_path}"
            )
        return artifact
    with output_path.open("x", encoding="utf-8") as handle:
        handle.write(serialized)
    return artifact


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
    allowed = {
        "BLOODHOUND_DOMAIN",
        "BLOODHOUND_PORT",
        "BLOODHOUND_SCHEME",
        "BLOODHOUND_TOKEN_ID",
        "BLOODHOUND_TOKEN_KEY",
        "BLOODHOUND_VERIFY_TLS",
    }
    return {name: os.environ[name] for name in sorted(allowed) if name in os.environ}


def _tool_doc(tool_obj: Any, name: str, allowed: set[str]) -> str:
    original = inspect.getdoc(tool_obj) or f"Read-only wrapper for {name}"
    if "Allowed info_type values:" in original:
        return original
    allowed_text = ", ".join(sorted(allowed))
    return f"{original}\n\nAllowed info_type values: {allowed_text}."


def _coordinator_result_to_mcp_text(result: Any) -> str:
    """Render an authoritative direct-coordinator result like MCP cypher_query."""

    if not result.success:
        return json.dumps(
            {
                "info_type": "run",
                "success": False,
                "error": str(result.error or result.failure_type or "query failed"),
                "error_type": str(result.failure_type or "query_error"),
                "query_executed": bool(result.query_executed),
                "policy_rule": str(result.safety_rule or ""),
            }
        )
    raw = result.raw if isinstance(result.raw, dict) else {}
    data = raw.get("data", raw)
    if not isinstance(data, dict):
        data = {}
    nodes = data.get("nodes") or {}
    edges = data.get("edges") or ()
    node_count = len(nodes) if isinstance(nodes, (dict, list)) else 0
    edge_count = len(edges) if isinstance(edges, list) else 0
    return json.dumps(
        {
            "info_type": "run",
            "success": True,
            "has_results": bool(nodes or edges or data.get("literals")),
            "data": data,
            "node_count": node_count,
            "edge_count": edge_count,
            "query_executed": True,
        }
    )


def _wrap_read_only_tool(
    tool_obj: Any,
    *,
    cypher_executor: Callable[..., Any] | None = None,
) -> Any:
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
            if cypher_executor is not None and tool_name == "cypher_query" and info_type == "run":
                query = kwargs.get("query")
                if not isinstance(query, str) or not query.strip():
                    raise ToolError("INVALID_ARGUMENTS: cypher_query.run requires query")
                result = cypher_executor(
                    query,
                    include_properties=bool(kwargs.get("include_properties", True)),
                )
                if inspect.isawaitable(result):
                    result = await result
                return _coordinator_result_to_mcp_text(result)
            result = tool_obj(*args, **kwargs)
            if inspect.isawaitable(result):
                return await result
            return result

        execute.__doc__ = _tool_doc(tool_obj, tool_name, allowed)
        return execute

    return wrapped_tool()


def _create_bloodhound_mcp_server(
    launcher: MCPLauncherConfig,
    *,
    launch_spec: MCPLaunchSpec | None = None,
) -> Any:
    resolved_spec = launch_spec or build_mcp_launch_spec(launcher)
    return mcp_server_stdio(
        command=resolved_spec.command,
        args=list(resolved_spec.args),
        cwd=resolved_spec.cwd,
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
            print(f"  WARNING: MCP prompt {selected!r} was empty; using built-in ORI MCP prompt.")
            return "", selected, names, "empty_selected_prompt"
        return text, selected, names, "selected"
    except Exception as exc:
        print(
            "  WARNING: MCP prompt discovery failed "
            f"({exc.__class__.__name__}); using built-in ORI MCP prompt."
        )
        return "", "", [], "discovery_failed"


async def _discover_bloodhound_mcp_resources(server: Any) -> tuple[list[str], str]:
    """List resource URIs for capability provenance without exposing them to the model."""

    session_handle = server._task_session()
    try:
        async with session_handle._client_session() as session:
            listed = await session.list_resources()
        resources = list(getattr(listed, "resources", listed) or [])
        uris = sorted(
            {
                str(uri)
                for resource in resources
                if (uri := getattr(resource, "uri", None)) is not None
            }
        )
        return uris, "listed"
    except Exception as exc:
        print(f"  WARNING: MCP resource discovery failed ({exc.__class__.__name__}).")
        return [], "discovery_failed"


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
    launcher: MCPLauncherConfig,
    *,
    include_resources: bool,
    include_prompt: bool,
    cypher_executor: Callable[..., Any] | None = None,
    launcher_runtime: MCPLauncherRuntime | None = None,
) -> MCPServerBundle:
    runtime = launcher_runtime or resolve_mcp_launcher_runtime(launcher)
    server = _create_bloodhound_mcp_server(
        launcher,
        launch_spec=runtime.launch_spec,
    )
    raw_tools = await mcp_tools(server).tools()
    wrapped: list[Any] = []
    for raw_tool in raw_tools:
        name = _canonical_tool_name(raw_tool)
        if name in _READ_ONLY_MCP_INFO_TYPES:
            wrapped.append(
                _wrap_read_only_tool(
                    raw_tool,
                    cypher_executor=cypher_executor,
                )
            )
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

    available_resource_uris, resource_discovery_status = await _discover_bloodhound_mcp_resources(
        server
    )

    return MCPServerBundle(
        tools=wrapped,
        server_prompt_text=prompt_text,
        server_prompt_name=prompt_name,
        available_prompt_names=available_prompt_names,
        prompt_discovery_status=prompt_discovery_status,
        available_resource_uris=available_resource_uris,
        resource_discovery_status=resource_discovery_status,
        launcher_provenance=runtime.provenance(launcher),
    )


def _readiness_response_failed(response: Any) -> bool:
    if isinstance(response, str):
        try:
            return _readiness_response_failed(json.loads(response))
        except json.JSONDecodeError:
            normalized = response.strip().lower()
            return normalized.startswith("error") or bool(
                re.search(r"[\"']error[\"']\s*:", normalized)
            )
    if isinstance(response, dict):
        if response.get("success") is False or response.get("isError") is True:
            return True
        if response.get("error"):
            return True
        return any(
            _readiness_response_failed(response[key])
            for key in ("content", "text")
            if key in response
        )
    if isinstance(response, list | tuple):
        return any(_readiness_response_failed(item) for item in response)
    for attribute in ("content", "text"):
        value = getattr(response, attribute, None)
        if value is not None and _readiness_response_failed(value):
            return True
    return False


async def _invoke_readiness_tool(tool_obj: Any, **kwargs: Any) -> dict[str, Any]:
    """Invoke one wrapped read-only tool and retain only non-sensitive evidence."""

    try:
        _, executor = _ollama_tool_spec(tool_obj)
        response = executor(**kwargs)
        if inspect.isawaitable(response):
            response = await response
        rendered = _tool_result_to_text(response)
        if _readiness_response_failed(response):
            return {
                "status": "failed",
                "error_type": "tool_error_response",
                "response_bytes": len(rendered.encode()),
                "response_sha256": hashlib.sha256(rendered.encode()).hexdigest(),
            }
        return {
            "status": "passed",
            "response_bytes": len(rendered.encode()),
            "response_sha256": hashlib.sha256(rendered.encode()).hexdigest(),
        }
    except Exception as exc:
        return {"status": "failed", "error_type": exc.__class__.__name__}


async def verify_mcp_launcher_readiness(
    launcher: MCPLauncherConfig,
    *,
    domain_query: str,
) -> MCPReadinessResult:
    """Start MCP without a model and exercise representative read-only operations."""

    bundle = await _load_bloodhound_mcp_bundle(
        launcher,
        include_resources=True,
        include_prompt=True,
    )
    tools_by_name = {_canonical_tool_name(tool_obj): tool_obj for tool_obj in bundle.tools}
    checks: dict[str, dict[str, Any]] = {"startup_credential_preflight": {"status": "passed"}}
    requested_checks: dict[str, dict[str, Any]] = {
        "data_quality": {"info_type": "completeness"},
        "domain_info": {"info_type": "list", "limit": 10, "skip": 0},
        "graph_analysis": {
            "info_type": "search",
            "query": domain_query,
            "search_type": "exact",
        },
        "cypher_query": {
            "info_type": "run",
            "query": "MATCH (n:Domain) RETURN n LIMIT 1",
            "include_properties": False,
        },
        RESOURCE_LIST_TOOL_NAME: {},
    }
    for name, kwargs in requested_checks.items():
        tool_obj = tools_by_name.get(name)
        if tool_obj is None:
            checks[name] = {"status": "failed", "error_type": "tool_not_exposed"}
            continue
        checks[name] = await _invoke_readiness_tool(tool_obj, **kwargs)

    return MCPReadinessResult(
        launcher_provenance=bundle.launcher_provenance,
        prompt_discovery_status=bundle.prompt_discovery_status,
        available_prompt_names=bundle.available_prompt_names,
        resource_discovery_status=bundle.resource_discovery_status,
        available_resource_uris=bundle.available_resource_uris,
        read_only_tools=sorted(tools_by_name),
        checks=checks,
        captured_at=datetime.now(UTC).isoformat(),
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
  {{"answer_type":"path_exists","path_found":true|false,"node_names":["NAME1","NAME2"],"relationships":[{{"source":"NAME1","edge":"AdminTo","target":"NAME2"}}]}}
- For no_path tasks:
  {{"answer_type":"no_path","path_found":false,"node_names":[],"relationships":[]}}
- For node_set tasks:
  {{"answer_type":"node_set","node_names":["NAME1","NAME2"]}}
- For row_count tasks:
  {{"answer_type":"row_count","count":123}}

Rules:
- node_names must be BloodHound object names, not object IDs
- If you use Cypher, never duplicate RETURN columns; alias repeated or derived expressions
- Prefer scalar RETURN columns (u.name, g.name, c.name) over path/list projections
  such as nodes(p) or [n IN nodes(p)|...]
- avoid unsupported UNION in the BloodHound CE API path; use separate queries or OR conditions
- Use COALESCE(list_prop, []) for nullable list properties
- If a tool returns structured syntax_error or query_error, revise the query instead of repeating it
- Final node_names should include only task-required graph-valid nodes unless
  optional nodes are explicitly acceptable
- If you cannot find a path, set path_found to false and return an empty node_names list
- When the task names required contextual mechanisms, include supporting relationship evidence
  using source, edge, and target in relationships
- Keep the final JSON compact and valid
"""


def _mcp_conversation_messages(
    task: Task | None,
    server_prompt_text: str = "",
    server_prompt_name: str = BLOODHOUND_PROMPT_NAME,
    system_prompt_override: str | None = None,
    public_question: str | None = None,
) -> list[Any]:
    question = public_question or (task.question if task is not None else "")
    if not question:
        raise ValueError("MCP conversation requires a public question")
    system_prompt = system_prompt_override or (_mcp_system_prompt(task) if task is not None else "")
    if not system_prompt:
        raise ValueError("MCP conversation requires a system prompt")
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
            ChatMessageSystem(content=system_prompt),
            ChatMessageUser(content=question),
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
    if task.grade_mode in {"path_exists", "no_path"}:
        normalized["path_found"] = bool(answer.get("path_found"))
        normalized["node_names"] = [
            str(name).strip() for name in answer.get("node_names", []) if str(name).strip()
        ]
        if (
            task.metadata.get("supporting_edges")
            or "relationships" in answer
            or "mechanisms" in answer
        ):
            normalized["relationships"] = [
                relationship
                for relationship in answer.get("relationships", [])
                if isinstance(relationship, (dict, str))
            ]
            normalized["mechanisms"] = [
                str(mechanism).strip()
                for mechanism in answer.get("mechanisms", [])
                if str(mechanism).strip()
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
    successful_tool_results = 0
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
            if not message.error and classification not in {"policy", "infra"}:
                successful_tool_results += 1
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
        successful_tool_results=successful_tool_results,
        minimum_evidence_satisfied=(tool_calls_total >= 3 and cypher_query_calls >= 2),
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
        available_resource_uris=list(data.get("available_resource_uris", [])),
        resource_discovery_status=data.get("resource_discovery_status", ""),
        infra_error_subtype=data.get("infra_error_subtype", ""),
        failure_subtype=data.get("failure_subtype", ""),
        successful_tool_results=int(data.get("successful_tool_results", 0)),
        finalization_guard_used=bool(data.get("finalization_guard_used", False)),
        loop_exhaustion_with_evidence=bool(data.get("loop_exhaustion_with_evidence", False)),
        minimum_evidence_satisfied=bool(data.get("minimum_evidence_satisfied", False)),
        final_answer_diagnostics=dict(data.get("final_answer_diagnostics") or {}),
        repair_turn_used=bool(data.get("repair_turn_used", False)),
        mcp_launcher=data.get("mcp_launcher", ""),
        mcp_source=data.get("mcp_source", ""),
        mcp_revision=data.get("mcp_revision", ""),
        mcp_executable=data.get("mcp_executable", ""),
        uv_version=data.get("uv_version", ""),
    )


def _is_ollama_model(model_name: str) -> bool:
    return model_name.startswith("ollama/")


def _is_openai_compat_model(model_name: str) -> bool:
    return model_name.startswith("openai-compat/") or model_name.startswith("codex/")


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
    if model_name.startswith("codex/"):
        from .codex_oauth import codex_request_base_url

        return codex_request_base_url(model_name, base_url)
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
    content_items = result if isinstance(result, (list, tuple)) else (result,)
    content_text: list[str] = []
    for item in content_items:
        if isinstance(item, dict):
            item_type = item.get("type")
            text = item.get("text")
        else:
            item_type = getattr(item, "type", None)
            text = getattr(item, "text", None)
        if item_type != "text" or not isinstance(text, str):
            content_text = []
            break
        content_text.append(text)
    if content_text:
        return "\n".join(content_text)
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
                # Inspect's schema model serializes unset JSON-Schema keywords
                # as explicit nulls by default. Several OpenAI-compatible
                # gateways reject those otherwise-valid tool definitions.
                "parameters": info.parameters.model_dump(exclude_none=True),
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
    no_progress_timeout_seconds: float = DEFAULT_MCP_NO_PROGRESS_TIMEOUT_SECONDS,
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
            lines = resp.aiter_lines()
            last_activity = time.monotonic()
            while True:
                _raise_if_no_progress(
                    last_activity=last_activity,
                    now=time.monotonic(),
                    timeout_seconds=no_progress_timeout_seconds,
                    scope="turn",
                )
                try:
                    line = await asyncio.wait_for(
                        lines.__anext__(), timeout=no_progress_timeout_seconds
                    )
                except StopAsyncIteration:
                    break
                except TimeoutError as exc:
                    raise MCPNoProgressTimeout(
                        subtype="MCP_TURN_TIMEOUT",
                        scope="turn",
                        timeout_seconds=no_progress_timeout_seconds,
                    ) from exc
                if not line.strip():
                    continue
                last_activity = time.monotonic()
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
    event_progress_observer: Callable[[str], None] | None = None,
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
    endpoint_family = "codex"
    credential_source = "CODEX_OAUTH"
    if model_name.startswith("codex/"):
        import openai

        from .codex_oauth import (
            chat_request_to_codex_responses_params,
            codex_headers,
            codex_model_name,
            codex_responses_events_to_chat_completion,
        )

        payload["model"] = codex_model_name(model_name)
        params = chat_request_to_codex_responses_params(payload)
        thread_id = str(params.get("prompt_cache_key") or "")
        headers = codex_headers(thread_id=thread_id)
        client = openai.AsyncOpenAI(
            api_key=headers["Authorization"].removeprefix("Bearer "),
            base_url=url,
            timeout=timeout,
        )
        try:
            events = await client.responses.create(**params, stream=True, extra_headers=headers)
            buffered_events: list[Any] = []
            partial_text: list[str] = []
            async for event in events:
                buffered_events.append(event)
                if getattr(event, "type", None) == "response.output_text.delta":
                    partial_text.append(getattr(event, "delta", "") or "")
                if event_progress_observer is not None:
                    event_progress_observer("".join(partial_text))
            data = codex_responses_events_to_chat_completion(buffered_events, str(payload["model"]))
        finally:
            await client.close()
    else:
        credential = resolve_openai_compat_credential(url)
        endpoint_family = credential.endpoint_family
        credential_source = credential.credential_source
        if credential.api_key is None and not openai_compat_endpoint_is_local(url):
            raise ProviderAuthenticationError(
                "OpenAI-compatible remote endpoint requires a credential scoped to endpoint family "
                f"{credential.endpoint_family!r}"
            )
        headers = (
            {"Authorization": f"Bearer {credential.api_key}"}
            if credential.api_key is not None
            else {}
        )
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            try:
                data = resp.json()
            except (TypeError, ValueError) as exc:
                raise ProviderProtocolError(
                    "Chat Completions response body is not valid JSON"
                ) from exc

    turn = normalize_chat_completion(
        data,
        provider="codex" if model_name.startswith("codex/") else endpoint_family,
        endpoint=url,
        fallback_model=_openai_compat_model_name(model_name),
    )
    if not isinstance(data, dict):
        raise ProviderProtocolError("Chat Completions response envelope must be an object")
    choice = dict(data["choices"][0])
    message = dict(choice["message"])
    usage = dict(data.get("usage") or {})
    content = turn.text
    content, think_block = _split_thinking_from_content(content)
    thinking = turn.reasoning or think_block
    provider_metrics = _openai_compat_provider_metrics(
        data=data,
        choice=choice,
        message=message,
        usage=usage,
        telemetry_adapter=telemetry_adapter,
    )
    if think_block and "reasoning_source" not in provider_metrics:
        provider_metrics["reasoning_source"] = "think_block"
    provider_metrics.update(
        {
            "refusal": turn.refusal,
            "status": turn.status.value,
            "usage_reported": turn.usage.usage_reported,
            "usage_complete": turn.usage.usage_complete,
            "response_id": turn.response_id,
            "api_surface": (
                ProviderApiSurface.RESPONSES.value
                if model_name.startswith("codex/")
                else ProviderApiSurface.CHAT_COMPLETIONS.value
            ),
            "endpoint_family": endpoint_family,
            "credential_source": credential_source,
        }
    )
    terminal_output_subtype = {
        "truncated": "TRUNCATED",
        "content_filtered": "CONTENT_FILTERED",
    }.get(turn.status.value)
    if terminal_output_subtype is not None:
        content = ""
        provider_metrics["model_output_error"] = True
        provider_metrics["model_output_subtype"] = terminal_output_subtype
        tool_calls_for_execution = ()
    else:
        tool_calls_for_execution = turn.tool_calls
    normalized_tool_calls = [
        {
            "id": call.id,
            "type": "function",
            "function": {
                "name": call.name,
                "arguments": call.raw_arguments,
            },
            "parsed_arguments": call.parsed_arguments,
            "argument_parse_status": call.argument_parse_status.value,
            "argument_parse_error": call.argument_parse_error,
        }
        for call in tool_calls_for_execution
    ]
    return {
        "model": turn.model,
        "content": content,
        "thinking": thinking,
        "tool_calls": normalized_tool_calls,
        "prompt_tokens": turn.usage.input_tokens or 0,
        "completion_tokens": turn.usage.output_tokens or 0,
        "total_tokens": turn.usage.total_tokens or 0,
        "finish_reason": turn.finish_reason,
        "provider_metrics": provider_metrics,
    }


def _tool_call_arguments(
    raw_arguments: Any,
) -> tuple[dict[str, Any], ToolArgumentParseStatus, str | None]:
    if isinstance(raw_arguments, dict):
        return raw_arguments, ToolArgumentParseStatus.VALID, None
    if raw_arguments is None or raw_arguments == "":
        return {}, ToolArgumentParseStatus.EMPTY, None
    if isinstance(raw_arguments, str):
        try:
            parsed = json.loads(raw_arguments)
            if isinstance(parsed, dict):
                return parsed, ToolArgumentParseStatus.VALID, None
            return (
                {},
                ToolArgumentParseStatus.NON_OBJECT,
                "tool arguments JSON must decode to an object",
            )
        except json.JSONDecodeError as exc:
            return {}, ToolArgumentParseStatus.MALFORMED, str(exc)
    return (
        {},
        ToolArgumentParseStatus.MALFORMED,
        f"tool arguments must be a JSON object or string, got {type(raw_arguments).__name__}",
    )


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


FINALIZATION_GUARD_PROMPT = (
    "You are at the MCP tool-call limit. Do not call another tool. Using the "
    "evidence already gathered, return ONLY the required compact JSON object. "
    "If the evidence is insufficient, return the best supported JSON answer "
    "rather than continuing to search."
)


def _tool_result_has_successful_evidence(
    result_text: str, tool_error: ToolCallError | None
) -> bool:
    if tool_error is not None:
        return False
    normalized = (result_text or "").lower()
    if (
        '"success": false' in normalized
        or "error_type" in normalized
        and '"success": true' not in normalized
    ):
        return False
    return bool(result_text.strip())


def _emit_mcp_loop_progress(
    observer: Callable[[ModelResponse, list[Any]], None] | None,
    response: ModelResponse,
    messages: list[Any],
) -> None:
    """Expose a cancellation-safe private snapshot without affecting the loop."""

    if observer is None:
        return
    try:
        observer(response, list(messages))
    except Exception:
        return


async def _run_ollama_mcp_loop(
    *,
    task: Task | None,
    public_question: str | None = None,
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
    system_prompt_override: str | None = None,
    tool_result_observer: (
        Callable[[str, dict[str, Any], str, ToolCallError | None], bool] | None
    ) = None,
    progress_observer: Callable[[ModelResponse, list[Any]], None] | None = None,
    tool_timeout_seconds: float | None = None,
) -> tuple[ModelResponse, MCPRunMetadata, list[Any]]:
    question = public_question or (task.question if task is not None else "")
    if not question:
        raise ValueError("MCP loop requires a public question")
    system_prompt = system_prompt_override or (_mcp_system_prompt(task) if task is not None else "")
    if not system_prompt:
        raise ValueError("MCP loop requires a system prompt")
    url = _native_ollama_chat_url(base_url)
    messages_payload: list[dict[str, Any]] = []
    if server_prompt_text.strip():
        messages_payload.append({"role": "system", "content": server_prompt_text})
    messages_payload.append(
        {
            "role": "system",
            "content": system_prompt,
        }
    )
    messages_payload.append({"role": "user", "content": question})

    inspect_messages = _mcp_conversation_messages(
        task,
        server_prompt_text=server_prompt_text,
        server_prompt_name=server_prompt_name,
        system_prompt_override=system_prompt,
        public_question=question,
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
    successful_tool_results = 0
    executed_tool_calls = 0
    typed_finalization_ready = False
    finalization_guard_used = False
    t0 = time.monotonic()

    for step in range(max_steps):
        use_finalization_guard = (
            _env_flag("ORI_MCP_FINALIZATION_GUARD", True)
            and (step >= max_steps - 1 or executed_tool_calls >= max_steps)
            and (
                typed_finalization_ready
                if tool_result_observer is not None
                else successful_tool_results > 0
            )
            and not finalization_guard_used
        )
        if use_finalization_guard:
            messages_payload.append({"role": "user", "content": FINALIZATION_GUARD_PROMPT})
            finalization_guard_used = True
        turn = await _ollama_chat_turn(
            url=url,
            model_name=model_name,
            messages=messages_payload,
            tools=[] if use_finalization_guard else tool_specs,
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
        progress_response = ModelResponse(
            raw_text=content,
            cypher=None,
            parse_stage="mcp_partial",
            tokens_input=total_prompt_tokens,
            tokens_output=total_completion_tokens,
            elapsed_seconds=time.monotonic() - t0,
            model=resolved_model,
            thinking="\n".join(thinking_parts),
            provider_metrics={
                "provider": "ollama_native_chat_mcp_loop",
                "prompt_eval_count": total_prompt_tokens,
                "eval_count": total_completion_tokens,
                **total_metrics,
            },
        )
        _emit_mcp_loop_progress(
            progress_observer,
            progress_response,
            inspect_messages,
        )

        if not raw_tool_calls:
            final_content = content
            break

        for tool_call in inspect_tool_calls:
            tool_name = tool_call.function
            tool_runner = tool_runners.get(tool_name)
            result_text = ""
            tool_error: ToolCallError | None = None
            infrastructure_error: MCPToolInfrastructureError | None = None
            if executed_tool_calls >= max_steps:
                result_text = json.dumps(
                    {
                        "success": False,
                        "error": "MCP tool-call budget exhausted",
                        "error_type": "tool_call_budget_exceeded",
                    }
                )
                tool_error = ToolCallError(
                    type="unknown",
                    message="MCP tool-call budget exhausted",
                )
            else:
                executed_tool_calls += 1
                try:
                    if tool_runner is None:
                        raise RuntimeError(f"Unknown tool: {tool_name}")
                    result = await _execute_mcp_tool(
                        tool_runner,
                        dict(tool_call.arguments),
                        timeout_seconds=tool_timeout_seconds,
                    )
                    result_text = _tool_result_to_text(result)
                except Exception as exc:
                    subtype = (
                        exc.subtype
                        if isinstance(exc, MCPToolInfrastructureError)
                        else _mcp_tool_infrastructure_subtype(exc)
                    )
                    if subtype is not None:
                        infrastructure_error = MCPToolInfrastructureError(
                            subtype=subtype,
                            detail=str(exc),
                        )
                    result_text = json.dumps(
                        {
                            "success": False,
                            "error": str(exc),
                            "error_type": (
                                "client_timeout"
                                if subtype == "MCP_TOOL_TIMEOUT"
                                else "transport_error"
                                if subtype is not None
                                else "tool_error"
                            ),
                        }
                    )
                    tool_error = ToolCallError(type="unknown", message=str(exc))

            if _tool_result_has_successful_evidence(result_text, tool_error):
                successful_tool_results += 1
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
            if tool_result_observer is not None:
                # V2's observer reports readiness for the complete transcript,
                # not only the current observation. Replacing this value keeps
                # later irrelevant calls sticky while allowing a later
                # truncation to revoke an earlier proof.
                typed_finalization_ready = bool(
                    tool_result_observer(
                        tool_name,
                        dict(tool_call.arguments),
                        result_text,
                        tool_error,
                    )
                )
            _emit_mcp_loop_progress(
                progress_observer,
                progress_response,
                inspect_messages,
            )
            if infrastructure_error is not None:
                raise infrastructure_error
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
    trajectory.finalization_guard_used = finalization_guard_used
    trajectory.successful_tool_results = max(
        trajectory.successful_tool_results, successful_tool_results
    )
    trajectory.loop_exhaustion_with_evidence = bool(
        not final_content and successful_tool_results > 0
    )
    trajectory.server_prompt_used = bool(server_prompt_text.strip())
    trajectory.server_prompt_name = server_prompt_name if server_prompt_text.strip() else ""
    trajectory.available_prompt_names = sorted(available_prompt_names or [])
    trajectory.prompt_discovery_status = prompt_discovery_status
    trajectory.resource_mode = resource_mode
    trajectory.tool_loop = MCP_TOOL_LOOP_NATIVE_OLLAMA
    return model_response, trajectory, inspect_messages


async def _run_openai_compat_mcp_loop(
    *,
    task: Task | None,
    public_question: str | None = None,
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
    system_prompt_override: str | None = None,
    tool_result_observer: (
        Callable[[str, dict[str, Any], str, ToolCallError | None], bool] | None
    ) = None,
    progress_observer: Callable[[ModelResponse, list[Any]], None] | None = None,
    tool_timeout_seconds: float | None = None,
) -> tuple[ModelResponse, MCPRunMetadata, list[Any]]:
    question = public_question or (task.question if task is not None else "")
    if not question:
        raise ValueError("MCP loop requires a public question")
    system_prompt = system_prompt_override or (_mcp_system_prompt(task) if task is not None else "")
    if not system_prompt:
        raise ValueError("MCP loop requires a system prompt")
    url = _openai_compat_chat_url(base_url, model_name)
    resolved_telemetry_adapter = _infer_openai_compat_telemetry_adapter(
        telemetry_adapter,
        base_url=base_url,
        model_name=model_name,
    )
    messages_payload: list[dict[str, Any]] = []
    if server_prompt_text.strip():
        messages_payload.append({"role": "system", "content": server_prompt_text})
    messages_payload.append(
        {
            "role": "system",
            "content": system_prompt,
        }
    )
    messages_payload.append({"role": "user", "content": question})

    inspect_messages = _mcp_conversation_messages(
        task,
        server_prompt_text=server_prompt_text,
        server_prompt_name=server_prompt_name,
        system_prompt_override=system_prompt,
        public_question=question,
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
    successful_tool_results = 0
    executed_tool_calls = 0
    typed_finalization_ready = False
    finalization_guard_used = False
    t0 = time.monotonic()

    for step in range(max_steps):
        use_finalization_guard = (
            _env_flag("ORI_MCP_FINALIZATION_GUARD", True)
            and (step >= max_steps - 1 or executed_tool_calls >= max_steps)
            and (
                typed_finalization_ready
                if tool_result_observer is not None
                else successful_tool_results > 0
            )
            and not finalization_guard_used
        )
        if use_finalization_guard:
            messages_payload.append({"role": "user", "content": FINALIZATION_GUARD_PROMPT})
            finalization_guard_used = True

        def observe_stream_progress(partial_text: str) -> None:
            partial_messages = list(inspect_messages)
            if partial_text:
                partial_messages.append(
                    ChatMessageAssistant(
                        content=partial_text,
                        model=resolved_model,
                    )
                )
            _emit_mcp_loop_progress(
                progress_observer,
                ModelResponse(
                    raw_text=partial_text,
                    cypher=None,
                    parse_stage="mcp_stream_partial",
                    tokens_input=total_prompt_tokens,
                    tokens_output=total_completion_tokens,
                    elapsed_seconds=time.monotonic() - t0,
                    model=resolved_model,
                    thinking="\n".join(thinking_parts),
                    provider_metrics={
                        "provider": "openai_compat_native_chat_mcp_loop",
                        "telemetry_adapter": resolved_telemetry_adapter,
                        "stream_in_progress": True,
                    },
                ),
                partial_messages,
            )

        turn = await _openai_compat_chat_turn(
            url=url,
            model_name=model_name,
            messages=messages_payload,
            tools=[] if use_finalization_guard else tool_specs,
            extra_body=extra_body,
            telemetry_adapter=resolved_telemetry_adapter,
            read_timeout_seconds=read_timeout_seconds,
            event_progress_observer=observe_stream_progress,
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
        tool_argument_errors: dict[str, str] = {}
        for idx, call in enumerate(raw_tool_calls):
            function = dict(call.get("function") or {})
            name = str(function.get("name") or "")
            call_id = str(call.get("id") or f"openai-compat-call-{step + 1}-{idx + 1}")
            parse_status_raw = call.get("argument_parse_status")
            parsed_arguments = call.get("parsed_arguments")
            parse_error = call.get("argument_parse_error")
            try:
                parse_status = (
                    ToolArgumentParseStatus(parse_status_raw)
                    if parse_status_raw is not None
                    else None
                )
            except ValueError:
                parse_status = ToolArgumentParseStatus.MALFORMED
                parse_error = f"unknown argument parse status: {parse_status_raw!r}"
            if parse_status is None:
                arguments, parse_status, parse_error = _tool_call_arguments(
                    function.get("arguments")
                )
            elif parse_status is ToolArgumentParseStatus.VALID:
                if isinstance(parsed_arguments, dict):
                    arguments = parsed_arguments
                else:
                    arguments = {}
                    parse_status = ToolArgumentParseStatus.MALFORMED
                    parse_error = "normalized tool arguments are missing their parsed object"
            elif parse_status is ToolArgumentParseStatus.EMPTY:
                arguments = {}
            else:
                arguments = {}
                parse_error = str(parse_error or "tool arguments are not a JSON object")
            if parse_status in {
                ToolArgumentParseStatus.MALFORMED,
                ToolArgumentParseStatus.NON_OBJECT,
            }:
                tool_argument_errors[call_id] = str(parse_error)
            normalized_tool_calls.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": function.get("arguments") or "",
                    },
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
        progress_response = ModelResponse(
            raw_text=content,
            cypher=None,
            parse_stage="mcp_partial",
            tokens_input=total_prompt_tokens,
            tokens_output=total_completion_tokens,
            elapsed_seconds=time.monotonic() - t0,
            model=resolved_model,
            thinking="\n".join(thinking_parts),
            provider_metrics={
                "provider": "openai_compat_native_chat_mcp_loop",
                "telemetry_adapter": resolved_telemetry_adapter,
                "prompt_tokens": total_prompt_tokens,
                "completion_tokens": total_completion_tokens,
                "finish_reasons": finish_reasons,
                "turn_metrics": turn_provider_metrics,
            },
        )
        _emit_mcp_loop_progress(
            progress_observer,
            progress_response,
            inspect_messages,
        )

        if not raw_tool_calls:
            final_content = content
            break

        for tool_call in inspect_tool_calls:
            tool_name = tool_call.function
            tool_runner = tool_runners.get(tool_name)
            result_text = ""
            tool_error: ToolCallError | None = None
            infrastructure_error: MCPToolInfrastructureError | None = None
            argument_error = tool_argument_errors.get(tool_call.id)
            if argument_error is not None:
                executed_tool_calls += 1
                result_text = json.dumps(
                    {
                        "success": False,
                        "error": argument_error,
                        "error_type": "invalid_tool_arguments",
                    }
                )
                tool_error = ToolCallError(type="parsing", message=argument_error)
            elif executed_tool_calls >= max_steps:
                result_text = json.dumps(
                    {
                        "success": False,
                        "error": "MCP tool-call budget exhausted",
                        "error_type": "tool_call_budget_exceeded",
                    }
                )
                tool_error = ToolCallError(
                    type="unknown",
                    message="MCP tool-call budget exhausted",
                )
            else:
                executed_tool_calls += 1
                try:
                    if tool_runner is None:
                        raise RuntimeError(f"Unknown tool: {tool_name}")
                    result = await _execute_mcp_tool(
                        tool_runner,
                        dict(tool_call.arguments),
                        timeout_seconds=tool_timeout_seconds,
                    )
                    result_text = _tool_result_to_text(result)
                except Exception as exc:
                    subtype = (
                        exc.subtype
                        if isinstance(exc, MCPToolInfrastructureError)
                        else _mcp_tool_infrastructure_subtype(exc)
                    )
                    if subtype is not None:
                        infrastructure_error = MCPToolInfrastructureError(
                            subtype=subtype,
                            detail=str(exc),
                        )
                    result_text = json.dumps(
                        {
                            "success": False,
                            "error": str(exc),
                            "error_type": (
                                "client_timeout"
                                if subtype == "MCP_TOOL_TIMEOUT"
                                else "transport_error"
                                if subtype is not None
                                else "tool_error"
                            ),
                        }
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
            if tool_result_observer is not None:
                # V2's observer reports readiness for the complete transcript,
                # not only the current observation. Replacing this value keeps
                # later irrelevant calls sticky while allowing a later
                # truncation to revoke an earlier proof.
                typed_finalization_ready = bool(
                    tool_result_observer(
                        tool_name,
                        dict(tool_call.arguments),
                        result_text,
                        tool_error,
                    )
                )
            _emit_mcp_loop_progress(
                progress_observer,
                progress_response,
                inspect_messages,
            )
            if infrastructure_error is not None:
                raise infrastructure_error
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
    trajectory.finalization_guard_used = finalization_guard_used
    trajectory.successful_tool_results = max(
        trajectory.successful_tool_results, successful_tool_results
    )
    trajectory.loop_exhaustion_with_evidence = bool(
        not final_content and successful_tool_results > 0
    )
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
        if task.grade_mode in {"path_exists", "no_path"}:
            answer = {"answer_type": "path_exists", "path_found": True, "node_names": []}
        elif task.grade_mode == "row_count":
            answer = {"answer_type": "row_count", "count": 0}
        else:
            valid_but_wrong = sorted(ref_result.node_names)
            if valid_but_wrong:
                valid_but_wrong = valid_but_wrong[:-1]
            answer = {"answer_type": "node_set", "node_names": valid_but_wrong}
        return json.dumps(answer), answer

    if task.grade_mode == "no_path":
        answer = {
            "answer_type": "no_path",
            "path_found": False,
            "node_names": [],
            "relationships": [],
        }
    elif task.grade_mode == "path_exists":
        answer = {
            "answer_type": "path_exists",
            "path_found": bool(ref_result.node_names),
            "node_names": sorted(ref_result.node_names),
        }
        supporting_edges = task.metadata.get("supporting_edges", [])
        if supporting_edges:
            answer["relationships"] = [
                {
                    "source": edge.get("source_name", edge.get("source", "")),
                    "edge": edge.get("edge", ""),
                    "target": edge.get("target_name", edge.get("target", "")),
                }
                for edge in supporting_edges
            ]
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
        f"{diagnostic}; subtype={subtype}; task_id={task_id}; sample={sample_index}/{sample_total}"
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
    _safe_print(f"  [{sample_index}/{sample_total}] {task_id} → INFRA_ERROR ({details})")
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
    available_resource_uris: list[str] | None = None,
    resource_discovery_status: str = "",
    launcher_provenance: dict[str, str | None] | None = None,
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
        resource_uris = sorted(available_resource_uris or [])
        provenance = dict(launcher_provenance or {})
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
                _apply_mcp_runtime_metadata(
                    trajectory,
                    available_resource_uris=resource_uris,
                    resource_discovery_status=resource_discovery_status,
                    launcher_provenance=provenance,
                )
                _persist_mcp_state(
                    state,
                    model_response=model_response,
                    trajectory=trajectory,
                    task_t0=task_t0,
                    model_calls=0,
                )
                raise
            except MCPNoProgressTimeout as exc:
                detail = f"MCP native Ollama no-progress timeout: {_exception_detail(exc)}"
                model_response, trajectory, ollama_messages = _fallback_mcp_state(
                    model_name=model_name,
                    messages=state.messages,
                    detail=detail,
                    subtype=exc.subtype,
                    tool_loop=MCP_TOOL_LOOP_NATIVE_OLLAMA,
                    server_prompt_text=server_prompt_text,
                    server_prompt_name=server_prompt_name,
                    available_prompt_names=prompt_names,
                    prompt_discovery_status=prompt_discovery_status,
                    resource_mode=resource_mode,
                    elapsed_seconds=time.monotonic() - task_t0,
                )
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
        _apply_mcp_runtime_metadata(
            trajectory,
            available_resource_uris=resource_uris,
            resource_discovery_status=resource_discovery_status,
            launcher_provenance=provenance,
        )
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
        if mcp_meta.infra_error_subtype in {
            "sample_cancelled",
            "solver_exception",
            "MCP_TURN_TIMEOUT",
            "NO_PROGRESS_TIMEOUT",
            "SAMPLE_TIMEOUT",
            "OLLAMA_STREAM_TIMEOUT",
        }:
            return _incomplete_mcp_score(
                metadata=metadata,
                model_response=model_response,
                mcp_meta=mcp_meta,
                missing_keys=[],
                subtype=mcp_meta.infra_error_subtype,
                detail=model_response.error or "MCP sample failed before final answer",
            )

        diagnostic = grade_mcp_diagnostic(
            task=task,
            final_answer=mcp_meta.final_answer_normalized,
            ref_result=ref_result,
            valid_node_names=valid_names,
            model_error=model_response.error,
            infra_tool_errors=mcp_meta.infra_tool_errors,
        )
        result = diagnostic.grade
        if diagnostic.final_answer_diagnostics:
            fad = diagnostic.final_answer_diagnostics
            fad.successful_tool_results = mcp_meta.successful_tool_results
            fad.finalization_guard_used = mcp_meta.finalization_guard_used
            fad.repair_turn_used = mcp_meta.repair_turn_used
            fad.evidence_depth_score = (
                max(
                    fad.evidence_depth_score,
                    1
                    + (2 if mcp_meta.cypher_query_calls else 0)
                    + (1 if mcp_meta.resource_reads_total else 0),
                )
                if mcp_meta.successful_tool_results
                else fad.evidence_depth_score
            )
            fad.evidence_found = fad.evidence_found or mcp_meta.successful_tool_results > 0
            fad.minimum_evidence_satisfied = (
                fad.minimum_evidence_satisfied or mcp_meta.minimum_evidence_satisfied
            )
            mcp_meta.final_answer_diagnostics = fad.to_jsonable()
        mcp_meta.failure_subtype = diagnostic.failure_subtype
        mcp_meta.loop_exhaustion_with_evidence = (
            result.outcome == "LOOP_EXHAUSTED" and mcp_meta.successful_tool_results > 0
        )
        sample_index = metadata.get("sample_index", "?")
        sample_total = metadata.get("sample_total", "?")
        _safe_print(
            f"  [{sample_index}/{sample_total}] {task.id} ({task.tier=}, {task.grade_mode})"
        )
        _safe_print(
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
                "diagnostic": diagnostic.to_jsonable(),
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
    mcp_launcher: MCPLauncherConfig | None = None,
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
    available_resource_uris: list[str] = []
    resource_discovery_status = "not_requested"
    launcher_provenance: dict[str, str | None] = {}
    if model.startswith("mock/mcp_"):
        tools: list[Any] = []
    else:
        resolved_launcher = mcp_launcher or MCPLauncherConfig.local_checkout(
            (mcp_dir or (Path.cwd().parent / "bloodhound-mcp")).resolve()
        )
        bundle = await _load_bloodhound_mcp_bundle(
            resolved_launcher,
            include_resources=resource_mode == RESOURCE_MODE_ON_DEMAND,
            include_prompt=True,
        )
        tools = bundle.tools
        server_prompt_text = bundle.server_prompt_text
        server_prompt_name = bundle.server_prompt_name
        available_prompt_names = bundle.available_prompt_names
        prompt_discovery_status = bundle.prompt_discovery_status
        available_resource_uris = bundle.available_resource_uris
        resource_discovery_status = bundle.resource_discovery_status
        launcher_provenance = bundle.launcher_provenance
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
                f"OpenAI-compatible telemetry adapter: {resolved_openai_compat_telemetry_adapter}"
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
            available_resource_uris=available_resource_uris,
            resource_discovery_status=resource_discovery_status,
            launcher_provenance=launcher_provenance,
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

    eval_logs = await _inspect_eval_async_with_artifact_recovery(
        inspect_task,
        recovery_log_dir=resolved_log_dir,
        expected_sample_count=len(samples),
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
