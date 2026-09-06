"""Native MCP protocol execution, separate from the historical CE adapter.

This layer accepts an already connected session. It neither provisions a backend
nor certifies it: the campaign admission layer must bind the isolated runtime,
backend graph/policy, discovery fingerprint and projector before using it.
Results are private native protocol payloads, never public report artifacts.
"""

from __future__ import annotations

import asyncio
import inspect
import math
import time
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

import anyio
import httpx
import jsonschema
from mcp import ClientSession, McpError

from ori.eval.mcp_runtime import _READ_ONLY_MCP_INFO_TYPES

from .fingerprint import canonical_json_bytes, canonical_sha256
from .native_mcp_profiles import get_native_implementation, validate_native_discovery
from .native_mcp_projection import NativeProjection, project_native_result
from .schema import TaskBundle

if TYPE_CHECKING:
    from .mcp import EvidenceEvent
    from .native_capability import NativeCapabilityProfile

# Native 92a37dd also provides read-only operations on two mixed admin tools.
# Do not inherit the historical lane's omission of those native capabilities.
_MAIN_READ_ONLY_OPERATIONS = {
    **{name: frozenset(operations) for name, operations in _READ_ONLY_MCP_INFO_TYPES.items()},
    "custom_nodes": frozenset({
        "list", "get", "validate_icon", "extension_list", "extension_edges",
    }),
    "asset_groups": frozenset({
        "list", "get", "collections", "member_counts", "list_tags", "tag_members",
    }),
}
_MAIN_OPERATION_DEFAULTS = {
    "domain_info": "list", "user_info": "info", "group_info": "info",
    "computer_info": "info", "ou_info": "info", "gpo_info": "info",
    "data_quality": "completeness", "custom_nodes": "list", "asset_groups": "list",
}

NativeFailure = Literal[
    "POLICY_REJECTED", "ARGUMENT_INVALID", "CAPABILITY_UNAVAILABLE", "CALL_LIMIT",
    "INFRA_ERROR", "HARNESS_ERROR", "TOOL_ERROR", "OUTPUT_LIMIT",
]


@dataclass(frozen=True)
class NativeCallDecision:
    allowed: bool
    reason: str


NativeCallGuard = Callable[
    [str, str, dict[str, Any]], NativeCallDecision | Awaitable[NativeCallDecision]
]


@dataclass(frozen=True)
class NativeCallOutcome:
    """Private call accounting; an observation is not certified task proof."""

    executed: bool
    duration_seconds: float
    raw_result: dict[str, Any] | None = None
    projection: NativeProjection | None = None
    failure: NativeFailure | None = None
    proof_event: EvidenceEvent | None = None


async def _inventory(session: ClientSession, method: str, field: str) -> list[dict[str, Any]]:
    """Bound pagination and reject cursor loops rather than accept a partial list."""

    items: list[dict[str, Any]] = []
    cursor = None
    seen: set[str] = set()
    for _ in range(100):
        page = await getattr(session, method)(cursor=cursor)
        items.extend(item.model_dump(mode="json", by_alias=True) for item in getattr(page, field))
        if len(items) > 10000:
            raise ValueError("native discovery inventory exceeds bound")
        cursor = page.nextCursor
        if cursor is None:
            return items
        if not cursor or cursor in seen:
            raise ValueError("native discovery pagination is cyclic or invalid")
        seen.add(cursor)
    raise ValueError("native discovery pagination exceeds bound")


class NativeMCPSession:
    """Keep native descriptors/results intact while enforcing call admission.

    The required guard enforces the *backend-specific* reviewed query and tool
    policy. Returning a decision cannot confer campaign certification. There is
    deliberately no permissive default guard or default campaign launcher.
    """

    def __init__(
        self,
        session: ClientSession,
        implementation_id: str,
        tools: list[dict[str, Any]],
        prompts: list[dict[str, Any]],
        resources: list[dict[str, Any]],
        resource_templates: list[dict[str, Any]],
        *,
        guard: NativeCallGuard,
        timeout_seconds: float = 60.0,
        max_calls: int = 16,
        max_result_bytes: int = 4 * 1024 * 1024,
        surface_availability: dict[str, bool] | None = None,
        capability_profile: NativeCapabilityProfile | None = None,
    ) -> None:
        get_native_implementation(implementation_id)
        if not callable(guard):
            raise ValueError("native execution requires a backend-specific admission guard")
        if isinstance(timeout_seconds, bool) or not math.isfinite(timeout_seconds):
            raise ValueError("native timeout must be finite and positive")
        if timeout_seconds <= 0:
            raise ValueError("native timeout must be finite and positive")
        for value in (max_calls, max_result_bytes):
            if type(value) is not int or value <= 0:
                raise ValueError("native call and result limits must be positive integers")
        descriptor_fingerprint = validate_native_discovery(
            implementation_id, tools, prompts, resources, resource_templates
        )
        availability = surface_availability or {
            "tools": True, "prompts": True, "resources": True,
        }
        if set(availability) != {"tools", "prompts", "resources"} or any(
            type(value) is not bool for value in availability.values()
        ):
            raise ValueError("invalid native surface availability")
        if any(items and not availability[key] for key, items in (
            ("tools", tools), ("prompts", prompts), ("resources", resources + resource_templates),
        )):
            raise ValueError("unavailable native surface cannot contain discovered capabilities")
        self._availability = deepcopy(availability)
        self.discovery_fingerprint = canonical_sha256({
            "native_descriptors": descriptor_fingerprint,
            "surface_availability": availability,
        })
        if capability_profile is not None:
            from .native_capability import validate_native_capability_profile

            validate_native_capability_profile(capability_profile)
            if (
                capability_profile.implementation_id != implementation_id
                or capability_profile.discovery_fingerprint != self.discovery_fingerprint
            ):
                raise ValueError("native session/profile discovery mismatch")
        self._capability_profile = capability_profile
        self._session = session
        self.implementation_id = implementation_id
        self._guard = guard
        self._timeout = timeout_seconds
        self._max_calls = max_calls
        self._max_result_bytes = max_result_bytes
        self._calls = 0
        self._set_states = {}
        # Never let an external consumer mutate the descriptors bound above.
        self._tools = {item["name"]: deepcopy(item) for item in tools}
        self._prompts = {item["name"]: deepcopy(item) for item in prompts}
        self._resources = {item["uri"]: deepcopy(item) for item in resources}
        self._resource_templates = deepcopy(resource_templates)

    @classmethod
    async def discover(
        cls, session: ClientSession, implementation_id: str, *, guard: NativeCallGuard,
        timeout_seconds: float = 60.0, max_calls: int = 16,
        max_result_bytes: int = 4 * 1024 * 1024,
        capability_profile: NativeCapabilityProfile | None = None,
    ) -> NativeMCPSession:
        get_native_implementation(implementation_id)
        if isinstance(timeout_seconds, bool) or not math.isfinite(timeout_seconds):
            raise ValueError("native discovery timeout must be finite and positive")
        if timeout_seconds <= 0:
            raise ValueError("native discovery timeout must be finite and positive")
        capabilities = session.get_server_capabilities()
        if capabilities is None:
            raise ValueError("native discovery requires an initialized session")
        availability = {
            key: getattr(capabilities, key) is not None for key in ("tools", "prompts", "resources")
        }
        async with asyncio.timeout(timeout_seconds):
            tools = (
                await _inventory(session, "list_tools", "tools")
                if availability["tools"] else []
            )
            prompts = (
                await _inventory(session, "list_prompts", "prompts")
                if availability["prompts"] else []
            )
            resources = (
                await _inventory(session, "list_resources", "resources")
                if availability["resources"] else []
            )
            templates = (
                await _inventory(session, "list_resource_templates", "resourceTemplates")
                if availability["resources"] else []
            )
        return cls(
            session, implementation_id, tools, prompts, resources, templates,
            guard=guard, timeout_seconds=timeout_seconds, max_calls=max_calls,
            max_result_bytes=max_result_bytes, surface_availability=availability,
            capability_profile=capability_profile,
        )

    @property
    def surface_availability(self) -> dict[str, bool]:
        return deepcopy(self._availability)

    @property
    def tools(self) -> list[dict[str, Any]]:
        """Expose reviewed read-only tools, without rewriting native schemas."""

        return [
            deepcopy(item) for name, item in self._tools.items()
            if self.implementation_id != "mwnickerson" or name in _MAIN_READ_ONLY_OPERATIONS
        ]

    @property
    def prompts(self) -> list[dict[str, Any]]:
        return deepcopy(list(self._prompts.values()))

    @property
    def resources(self) -> list[dict[str, Any]]:
        return deepcopy(list(self._resources.values()))

    @property
    def resource_templates(self) -> list[dict[str, Any]]:
        return deepcopy(self._resource_templates)

    @property
    def calls(self) -> int:
        return self._calls

    def _start_call(self) -> bool:
        if self._calls >= self._max_calls:
            return False
        self._calls += 1
        return True

    async def call_tool(
        self, name: str, arguments: dict[str, Any], task: TaskBundle,
        *, attempt_id: str | None = None,
    ) -> NativeCallOutcome:
        started = time.monotonic()
        executed = False

        def rejected(failure: NativeFailure) -> NativeCallOutcome:
            return NativeCallOutcome(executed, time.monotonic() - started, failure=failure)

        if not self._start_call():
            return rejected("CALL_LIMIT")
        if not isinstance(name, str) or not name:
            return rejected("ARGUMENT_INVALID")
        if self._capability_profile is not None:
            from .native_proof import validate_native_task_binding

            try:
                validate_native_task_binding(self._capability_profile, task)
            except (TypeError, ValueError):
                return rejected("HARNESS_ERROR")
            if task.claim_kind == "set" and self.implementation_id == "mwnickerson":
                # The runner must supply a fresh private identifier for each
                # model/repetition/retry attempt. Never reuse prior page proof.
                if not isinstance(attempt_id, str) or not attempt_id.strip():
                    return rejected("HARNESS_ERROR")
        try:
            if not isinstance(arguments, dict):
                return rejected("ARGUMENT_INVALID")
            arguments = deepcopy(arguments)
            if len(canonical_json_bytes(arguments)) > self._max_result_bytes:
                return rejected("ARGUMENT_INVALID")
        except (ValueError, TypeError, RecursionError):
            return rejected("ARGUMENT_INVALID")
        descriptor = self._tools.get(name)
        if descriptor is None:
            return rejected("CAPABILITY_UNAVAILABLE")
        if self.implementation_id == "mwnickerson":
            allowed = _MAIN_READ_ONLY_OPERATIONS.get(name)
            if allowed is None:
                return rejected("POLICY_REJECTED")
            operation = arguments.get("info_type", _MAIN_OPERATION_DEFAULTS.get(name))
            if not isinstance(operation, str):
                return rejected("ARGUMENT_INVALID")
            if operation not in allowed:
                return rejected("POLICY_REJECTED")
        try:
            jsonschema.validate(arguments, descriptor["inputSchema"])
        except jsonschema.ValidationError:
            return rejected("ARGUMENT_INVALID")
        # The guard gets a copy: admission cannot rewrite the submitted query.
        try:
            async with asyncio.timeout(self._timeout):
                decision = self._guard(self.implementation_id, name, deepcopy(arguments))
                if inspect.isawaitable(decision):
                    decision = await decision
                if (
                    not isinstance(decision, NativeCallDecision)
                    or type(decision.allowed) is not bool
                ):
                    return rejected("HARNESS_ERROR")
                if not decision.allowed:
                    return rejected("POLICY_REJECTED")
                executed = True
                result = await self._session.call_tool(name, deepcopy(arguments))
                payload = result.model_dump(mode="json", by_alias=True)
                if len(canonical_json_bytes(payload)) > self._max_result_bytes:
                    return rejected("OUTPUT_LIMIT")
                projection = project_native_result(
                    self.implementation_id, name, arguments, payload, task,
                )
                failure = (
                    "TOOL_ERROR" if result.isError or projection.status == "tool_error" else None
                )
                proof_event = None
                if failure is None and self._capability_profile is not None:
                    from .native_proof import NativeSetProofState, classify_native_result

                    proof_event = classify_native_result(
                        self._capability_profile, task, name, arguments, payload,
                        set_state=self._set_states.setdefault(
                            (task.task_fingerprint, attempt_id), NativeSetProofState(),
                        ) if task.claim_kind == "set" else None,
                    )
                return NativeCallOutcome(
                    True, time.monotonic() - started, payload, projection, failure, proof_event,
                )
        except (TimeoutError, McpError, httpx.HTTPError,
                anyio.BrokenResourceError, anyio.ClosedResourceError):
            return rejected("INFRA_ERROR")
        except Exception:
            return rejected("HARNESS_ERROR")

    async def get_prompt(self, name: str, arguments: dict[str, str]) -> NativeCallOutcome:
        """Return native prompt messages; never inject them into the system prompt."""

        if not isinstance(name, str) or not name:
            return NativeCallOutcome(False, 0.0, failure="ARGUMENT_INVALID")
        descriptor = self._prompts.get(name)
        if descriptor is None:
            return NativeCallOutcome(False, 0.0, failure="CAPABILITY_UNAVAILABLE")
        declarations = {item["name"]: item for item in descriptor.get("arguments") or []}
        if (
            not isinstance(arguments, dict)
            or set(arguments) - set(declarations)
            or any(not isinstance(value, str) for value in arguments.values())
            or any(item.get("required") and key not in arguments
                   for key, item in declarations.items())
        ):
            return NativeCallOutcome(False, 0.0, failure="ARGUMENT_INVALID")
        return await self._protocol_call("get_prompt", name, arguments=deepcopy(arguments))

    async def read_resource(self, uri: str) -> NativeCallOutcome:
        # Reviewed pins expose static resources only. Never guess/emulate URIs.
        if not isinstance(uri, str) or not uri:
            return NativeCallOutcome(False, 0.0, failure="ARGUMENT_INVALID")
        if uri not in self._resources:
            return NativeCallOutcome(False, 0.0, failure="CAPABILITY_UNAVAILABLE")
        return await self._protocol_call("read_resource", uri)

    async def _protocol_call(self, method: str, target: str, **kwargs: Any) -> NativeCallOutcome:
        started = time.monotonic()
        if not self._start_call():
            return NativeCallOutcome(False, 0.0, failure="CALL_LIMIT")
        try:
            async with asyncio.timeout(self._timeout):
                result = await getattr(self._session, method)(target, **kwargs)
                payload = result.model_dump(mode="json", by_alias=True)
                if len(canonical_json_bytes(payload)) > self._max_result_bytes:
                    return NativeCallOutcome(
                        True, time.monotonic() - started, failure="OUTPUT_LIMIT",
                    )
                return NativeCallOutcome(True, time.monotonic() - started, payload)
        except (TimeoutError, McpError, httpx.HTTPError,
                anyio.BrokenResourceError, anyio.ClosedResourceError):
            return NativeCallOutcome(True, time.monotonic() - started, failure="INFRA_ERROR")
        except Exception:
            return NativeCallOutcome(True, time.monotonic() - started, failure="HARNESS_ERROR")
