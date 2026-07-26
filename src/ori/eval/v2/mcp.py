"""Certified MCP capability and evidence-finalization contracts for protocol v2.

This module deliberately has no dependency on the legacy MCP runtimes.  Provider
loops translate their observations into :class:`EvidenceEvent` values and feed
those values to the same immutable reducer.
"""

from __future__ import annotations

import hashlib
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .fingerprint import canonical_sha256
from .schema import (
    CapabilityProfile,
    Fingerprint,
    MCPBindingMode,
    NonEmptyStr,
    ProofStrength,
    RelationshipSemantics,
    StrictModel,
    TaskBundle,
    ToolCapability,
    Track,
)

MCP_CAPABILITY_PROFILE_VERSION = "1"
MCP_CAPABILITY_PROFILE_ID = "ori-mcp-009c88f-bhce-9.1-cypher-v1"
MCP_BLOODHOUND_CE_VERSION = "9.1.0"
MCP_SERVER_REVISION = "009c88f41fae302becad4b00777a3749a0f6f0fa"
MCP_CAPABILITY_MAX_OUTPUT_BYTES = 65_536
MCP_EVIDENCE_STATE_MACHINE_VERSION = "ori-mcp-evidence-v1"
MCP_FINALIZATION_POLICY_FINGERPRINT = canonical_sha256(
    {
        "component": MCP_EVIDENCE_STATE_MACHINE_VERSION,
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
)

SCHEMA_ONLY_RETRY_INSTRUCTION = (
    "Return only one JSON object matching the declared answer schema. "
    "Do not call tools or add commentary."
)


class MCPToolLoop(StrEnum):
    """Known loop implementations.

    ``auto`` remains representable so non-certified configuration can be parsed,
    but it is rejected by :func:`validate_certified_mcp_loop`.
    """

    AUTO = "auto"
    INSPECT = "inspect"
    NATIVE_OLLAMA = "native-ollama"
    NATIVE_OPENAI_COMPATIBLE = "native-openai-compatible"


CERTIFIED_MCP_TOOL_LOOPS = (
    MCPToolLoop.NATIVE_OLLAMA,
    MCPToolLoop.NATIVE_OPENAI_COMPATIBLE,
    MCPToolLoop.INSPECT,
)


class EvidenceEventKind(StrEnum):
    """Typed result of classifying one MCP observation."""

    USEFUL_POSITIVE = "useful_positive"
    VALID_NEGATIVE = "valid_negative"
    CONCLUSIVE_EMPTY = "conclusive_empty"
    INCONCLUSIVE_EMPTY = "inconclusive_empty"
    TRUNCATED = "truncated"
    INVALID_ARGUMENTS = "invalid_arguments"
    POLICY_REJECTION = "policy_rejection"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"
    IRRELEVANT = "irrelevant"
    RESOURCE_READ = "resource_read"


FINALIZATION_UNLOCKING_EVENT_KINDS = frozenset(
    {
        EvidenceEventKind.USEFUL_POSITIVE,
        EvidenceEventKind.VALID_NEGATIVE,
        EvidenceEventKind.CONCLUSIVE_EMPTY,
    }
)


class EvidenceEvent(StrictModel):
    """A model-visible observation classified against one public task contract."""

    kind: EvidenceEventKind
    task_fingerprint: Fingerprint
    capability_profile_fingerprint: Fingerprint
    tool_name: NonEmptyStr | None = None
    operation: NonEmptyStr | None = None
    resource_uri: NonEmptyStr | None = None
    reason: NonEmptyStr | None = None

    @model_validator(mode="after")
    def source_matches_kind(self) -> EvidenceEvent:
        if (self.tool_name is None) != (self.operation is None):
            raise ValueError("tool_name and operation must be declared together")
        if self.kind is EvidenceEventKind.RESOURCE_READ:
            if self.resource_uri is None:
                raise ValueError("resource_read events require a resource_uri")
            if self.tool_name is not None:
                raise ValueError("resource_read events cannot declare a tool operation")
        elif self.resource_uri is not None:
            raise ValueError("only resource_read events may declare a resource_uri")
        elif self.kind is not EvidenceEventKind.INFRASTRUCTURE_FAILURE and self.tool_name is None:
            raise ValueError(f"{self.kind.value} events require a tool operation")
        return self

    @property
    def unlocks_finalization(self) -> bool:
        return self.kind in FINALIZATION_UNLOCKING_EVENT_KINDS


class ToolObservation(StrictModel):
    """Provider-neutral facts about one completed MCP tool operation.

    Provider loops may report only these mechanical facts.  The harness derives
    claim relevance and finalization usefulness from the public task contract
    and pinned capability profile.
    """

    tool_name: NonEmptyStr
    operation: NonEmptyStr
    succeeded: bool
    arguments_valid: bool = True
    policy_rejected: bool = False
    infrastructure_failure: bool = False
    result_count: int | None = Field(default=None, strict=True, ge=0)
    total_count: int | None = Field(default=None, strict=True, ge=0)
    pages_received: int = Field(default=1, strict=True, ge=0)
    complete: bool = False
    truncated: bool = False
    negative_proof: bool = False
    output_bytes: int = Field(default=0, strict=True, ge=0)

    @model_validator(mode="after")
    def observation_is_coherent(self) -> ToolObservation:
        failure_flags = (
            not self.arguments_valid,
            self.policy_rejected,
            self.infrastructure_failure,
        )
        if sum(failure_flags) > 1:
            raise ValueError("tool observation failure states are mutually exclusive")
        if any(failure_flags) and self.succeeded:
            raise ValueError("failed tool observations cannot be successful")
        if self.complete and self.truncated:
            raise ValueError("a truncated observation cannot be complete")
        if self.total_count is not None and self.result_count is None:
            raise ValueError("total_count requires result_count")
        if self.complete and self.result_count is None:
            raise ValueError("complete observations require result_count")
        if (
            self.total_count is not None
            and self.result_count is not None
            and self.result_count > self.total_count
        ):
            raise ValueError("result_count cannot exceed total_count")
        if self.negative_proof and (
            not self.succeeded
            or not self.complete
            or self.truncated
            or self.result_count != 0
        ):
            raise ValueError(
                "negative proof requires a successful, complete, empty observation"
            )
        return self


class FinalOutputStatus(StrEnum):
    VALID = "valid"
    MALFORMED = "malformed"
    MISSING = "missing"


class FinalizationAttempt(StrictModel):
    """One provider-independent attempt to emit the declared final JSON."""

    status: FinalOutputStatus
    output_digest: Fingerprint | None = None

    @model_validator(mode="after")
    def digest_matches_status(self) -> FinalizationAttempt:
        if self.status is FinalOutputStatus.MISSING and self.output_digest is not None:
            raise ValueError("missing final output cannot have an output digest")
        return self


class FinalizationPhase(StrEnum):
    COLLECTING = "collecting"
    READY = "ready"
    RETRY_SCHEMA_ONLY = "retry_schema_only"
    FINALIZED = "finalized"
    OUTPUT_INVALID = "output_invalid"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"


TERMINAL_FINALIZATION_PHASES = frozenset(
    {
        FinalizationPhase.FINALIZED,
        FinalizationPhase.OUTPUT_INVALID,
        FinalizationPhase.INFRASTRUCTURE_FAILURE,
    }
)


class FinalizationState(StrictModel):
    """Immutable evidence/finalization state shared by every certified loop."""

    state_machine_version: Literal["ori-mcp-evidence-v1"] = MCP_EVIDENCE_STATE_MACHINE_VERSION
    task_fingerprint: Fingerprint
    capability_profile_fingerprint: Fingerprint
    tool_loop: MCPToolLoop
    certified: bool
    phase: FinalizationPhase = FinalizationPhase.COLLECTING
    events: tuple[EvidenceEvent, ...] = ()
    finalization_unlocked: bool = False
    schema_retry_count: int = Field(default=0, strict=True, ge=0, le=1)
    final_output_attempts: int = Field(default=0, strict=True, ge=0)
    output_digest: Fingerprint | None = None
    terminal_reason: NonEmptyStr | None = None

    @model_validator(mode="after")
    def state_is_coherent(self) -> FinalizationState:
        expected_unlocked = any(event.unlocks_finalization for event in self.events)
        if self.finalization_unlocked != expected_unlocked:
            raise ValueError("finalization_unlocked must be derived from evidence events")
        if self.certified and self.tool_loop not in CERTIFIED_MCP_TOOL_LOOPS:
            raise ValueError("certified MCP state requires an explicit certified loop")
        if self.phase is FinalizationPhase.COLLECTING and self.finalization_unlocked:
            raise ValueError("claim-relevant evidence must move state to ready")
        if self.phase is FinalizationPhase.READY and not self.finalization_unlocked:
            raise ValueError("ready state requires claim-relevant evidence")
        if self.phase is FinalizationPhase.RETRY_SCHEMA_ONLY:
            if not self.finalization_unlocked or self.schema_retry_count != 1:
                raise ValueError("schema-only retry requires evidence and exactly one retry")
        if self.phase is FinalizationPhase.FINALIZED:
            if not self.finalization_unlocked:
                raise ValueError("finalized state requires claim-relevant evidence")
            if self.terminal_reason is not None:
                raise ValueError("successful finalization cannot have a terminal reason")
        elif self.phase in TERMINAL_FINALIZATION_PHASES:
            if self.terminal_reason is None:
                raise ValueError("terminal failure state requires a terminal reason")
        elif self.terminal_reason is not None:
            raise ValueError("non-terminal state cannot have a terminal reason")
        return self

    @property
    def is_terminal(self) -> bool:
        return self.phase in TERMINAL_FINALIZATION_PHASES

    @property
    def retry_instruction(self) -> str | None:
        if self.phase is FinalizationPhase.RETRY_SCHEMA_ONLY:
            return SCHEMA_ONLY_RETRY_INSTRUCTION
        return None


class LoopConformanceResult(StrictModel):
    """One row in the common provider-loop conformance matrix."""

    tool_loop: MCPToolLoop
    ignored_events_unlock: bool
    useful_event_phase: FinalizationPhase
    first_malformed_phase: FinalizationPhase
    second_malformed_phase: FinalizationPhase
    infrastructure_phase: FinalizationPhase
    schema_retry_count: int = Field(strict=True, ge=0, le=1)


_DIRECT = (RelationshipSemantics.DIRECT,)
_TRANSITIVE = (RelationshipSemantics.TRANSITIVE,)
_EFFECTIVE = (RelationshipSemantics.EFFECTIVE,)
_ALL_SEMANTICS = (
    RelationshipSemantics.DIRECT,
    RelationshipSemantics.TRANSITIVE,
    RelationshipSemantics.EFFECTIVE,
)

# Operations that return administrative metadata or transform already-observed
# data cannot establish a benchmark graph claim.  They remain declared so an
# adapter can classify them as irrelevant instead of treating mere success as
# evidence.
_NON_EVIDENCE_OPERATIONS = {
    ("cypher_query", "get_saved"),
    ("cypher_query", "interpret"),
    ("cypher_query", "list_saved"),
    ("cypher_query", "validate"),
    ("data_quality", "ad_domain"),
    ("data_quality", "azure_tenant"),
    ("data_quality", "completeness"),
    ("data_quality", "platform"),
}

_PAGED_OPERATIONS: dict[str, tuple[str, ...]] = {
    "domain_info": (
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
    ),
    "user_info": (
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
    ),
    "group_info": (
        "admin_rights",
        "controllables",
        "controllers",
        "dcom_rights",
        "members",
        "memberships",
        "ps_remote_rights",
        "rdp_rights",
        "sessions",
    ),
    "computer_info": (
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
    ),
    "ou_info": ("computers", "groups", "gpos", "users"),
    "gpo_info": ("computers", "controllers", "ous", "tier_zeros", "users"),
    "adcs_info": (
        "cert_template_controllers",
        "root_ca_controllers",
        "enterprise_ca_controllers",
        "aia_ca_controllers",
    ),
}

_SCALAR_OPERATIONS: dict[str, tuple[str, ...]] = {
    "domain_info": ("list", "search"),
    "user_info": ("info",),
    "group_info": ("info",),
    "computer_info": ("info",),
    "ou_info": ("info",),
    "gpo_info": ("info",),
    "adcs_info": (
        "cert_template_info",
        "root_ca_info",
        "enterprise_ca_info",
    ),
}

_EFFECTIVE_OPERATION_NAMES = {
    "admin_rights",
    "admin_users",
    "controllables",
    "controllers",
    "dc_syncers",
    "dcom_rights",
    "dcom_users",
    "foreign_admins",
    "foreign_gpo_controllers",
    "ps_remote_rights",
    "ps_remote_users",
    "rdp_rights",
    "rdp_users",
    "relay_targets",
    "sql_admin_rights",
    "sql_admins",
    "tier_zeros",
}

_TRANSITIVE_OPERATION_NAMES = {
    "memberships",
}


def _operation_semantics(operation: str) -> tuple[RelationshipSemantics, ...]:
    if operation in _EFFECTIVE_OPERATION_NAMES:
        return _EFFECTIVE
    if operation in _TRANSITIVE_OPERATION_NAMES:
        return _TRANSITIVE
    return _DIRECT


def _capability(
    *,
    name: str,
    operation: str,
    semantics: tuple[RelationshipSemantics, ...],
    supports_pagination: bool,
    stable_ordering: bool,
    reports_truncation: bool = False,
    reports_total_count: bool = False,
    proof_strength: ProofStrength = ProofStrength.POSITIVE_ONLY,
) -> ToolCapability:
    return ToolCapability(
        name=name,
        operation=operation,
        semantics=semantics,
        supports_pagination=supports_pagination,
        stable_ordering=stable_ordering,
        reports_truncation=reports_truncation,
        reports_total_count=reports_total_count,
        proof_strength=proof_strength,
        max_output_bytes=MCP_CAPABILITY_MAX_OUTPUT_BYTES,
    )


def _pinned_tool_capabilities() -> tuple[ToolCapability, ...]:
    tools: list[ToolCapability] = []
    for name, operations in _SCALAR_OPERATIONS.items():
        for operation in operations:
            tools.append(
                _capability(
                    name=name,
                    operation=operation,
                    semantics=_operation_semantics(operation),
                    supports_pagination=False,
                    stable_ordering=True,
                )
            )
    for name, operations in _PAGED_OPERATIONS.items():
        for operation in operations:
            tools.append(
                _capability(
                    name=name,
                    operation=operation,
                    semantics=_operation_semantics(operation),
                    supports_pagination=True,
                    # The pinned server exposes skip/limit but does not promise a
                    # stable sort, total count, or explicit truncation signal.
                    stable_ordering=False,
                )
            )
    tools.extend(
        (
            _capability(
                name="graph_analysis",
                operation="search",
                semantics=_DIRECT,
                supports_pagination=False,
                stable_ordering=True,
            ),
            _capability(
                name="graph_analysis",
                operation="shortest_path",
                semantics=_ALL_SEMANTICS,
                supports_pagination=False,
                stable_ordering=True,
                reports_truncation=True,
                proof_strength=ProofStrength.BOUNDED_NEGATIVE,
            ),
            _capability(
                name="graph_analysis",
                operation="edge_composition",
                semantics=_EFFECTIVE,
                supports_pagination=False,
                stable_ordering=True,
            ),
            _capability(
                name="graph_analysis",
                operation="relay_targets",
                semantics=_EFFECTIVE,
                supports_pagination=False,
                stable_ordering=False,
            ),
            _capability(
                name="cypher_query",
                operation="run",
                semantics=_ALL_SEMANTICS,
                # These guarantees describe the certified ORI adapter around
                # the pinned raw operation: it requires ORDER BY object ID,
                # bounded SKIP/LIMIT pages, and a companion total count before
                # classifying a result as complete.
                supports_pagination=True,
                stable_ordering=True,
                reports_truncation=True,
                reports_total_count=True,
                proof_strength=ProofStrength.COMPLETE_ENUMERATION,
            ),
        )
    )
    for name, operation in sorted(_NON_EVIDENCE_OPERATIONS):
        tools.append(
            _capability(
                name=name,
                operation=operation,
                semantics=(),
                supports_pagination=operation == "list_saved",
                stable_ordering=False,
            )
        )
    return tuple(sorted(tools, key=lambda item: (item.name, item.operation)))


def _assemble_pinned_profile() -> CapabilityProfile:
    payload = {
        "profile_id": MCP_CAPABILITY_PROFILE_ID,
        "track": Track.MCP,
        "bloodhound_ce_version": MCP_BLOODHOUND_CE_VERSION,
        "mcp_server_revision": MCP_SERVER_REVISION,
        "finalization_policy_fingerprint": MCP_FINALIZATION_POLICY_FINGERPRINT,
        "tools": _pinned_tool_capabilities(),
        # Certified v2 bindings use resource_mode=off. Resources may still be
        # observed by an adapter, but they are never evidence.
        "resources": (),
        "cypher_enabled": True,
        "profile_fingerprint": "0" * 64,
    }
    profile = CapabilityProfile(**payload)
    return profile.model_copy(
        update={
            "profile_fingerprint": canonical_sha256(
                profile,
                exclude_fields=("profile_fingerprint",),
            )
        }
    )


PINNED_MCP_CAPABILITY_PROFILE = _assemble_pinned_profile()
MCP_CAPABILITY_PROFILE_FINGERPRINT = PINNED_MCP_CAPABILITY_PROFILE.profile_fingerprint


def build_mcp_capability_profile() -> CapabilityProfile:
    """Return the immutable, version-pinned ORI MCP capability profile."""

    return PINNED_MCP_CAPABILITY_PROFILE


def validate_mcp_capability_profile(
    profile: CapabilityProfile,
) -> CapabilityProfile:
    """Validate the complete pinned profile and its content fingerprint."""

    if type(profile) is not CapabilityProfile:
        raise TypeError("profile must be a CapabilityProfile")
    expected_fingerprint = canonical_sha256(
        profile,
        exclude_fields=("profile_fingerprint",),
    )
    if profile.profile_fingerprint != expected_fingerprint:
        raise ValueError("MCP capability profile fingerprint mismatch")
    if profile.track is not Track.MCP:
        raise ValueError("MCP capability profile must use the MCP track")
    if profile.profile_id != MCP_CAPABILITY_PROFILE_ID:
        raise ValueError("unexpected MCP capability profile ID")
    if profile.bloodhound_ce_version != MCP_BLOODHOUND_CE_VERSION:
        raise ValueError("unexpected BloodHound CE version")
    if profile.mcp_server_revision != MCP_SERVER_REVISION:
        raise ValueError("unexpected MCP server revision")
    if profile.resources:
        raise ValueError("certified MCP capability profile requires resource_mode=off")
    operations = [(tool.name, tool.operation) for tool in profile.tools]
    if len(operations) != len(set(operations)):
        raise ValueError("MCP capability profile contains duplicate tool operations")
    for tool in profile.tools:
        if len(tool.semantics) != len(set(tool.semantics)):
            raise ValueError(f"{tool.name}.{tool.operation} repeats relationship semantics")
        if tool.max_output_bytes > MCP_CAPABILITY_MAX_OUTPUT_BYTES:
            raise ValueError(f"{tool.name}.{tool.operation} exceeds the certified output bound")
    if profile.profile_fingerprint != MCP_CAPABILITY_PROFILE_FINGERPRINT:
        raise ValueError("MCP capability profile content does not match the pinned revision")
    return profile


def capability_for_operation(
    profile: CapabilityProfile,
    *,
    tool_name: str,
    operation: str,
) -> ToolCapability | None:
    """Resolve one exact composite-tool operation without prefix matching."""

    validate_mcp_capability_profile(profile)
    matches = tuple(
        tool for tool in profile.tools if tool.name == tool_name and tool.operation == operation
    )
    if len(matches) > 1:
        raise ValueError(f"duplicate MCP capability for {tool_name}.{operation}")
    return matches[0] if matches else None


def _require_public_task(task: TaskBundle) -> TaskBundle:
    if type(task) is not TaskBundle:
        raise TypeError(
            "task must be a public TaskBundle; scorer-only or arbitrary objects are forbidden"
        )
    if task.binding.track is not Track.MCP:
        raise ValueError("MCP capability classification requires an MCP TaskBundle")
    return task


def _base_capability_supports_task(
    task: TaskBundle,
    profile: CapabilityProfile,
    capability: ToolCapability,
) -> bool:
    if task.binding.capability_profile_id != profile.profile_id:
        return False
    if task.binding.semantics not in capability.semantics:
        return False
    if capability.max_output_bytes > task.binding.bounds.max_output_bytes:
        return False
    if (
        task.binding.mcp_binding_mode is MCPBindingMode.TOOL_ONLY
        and capability.name == "cypher_query"
    ):
        return False
    return True


def capability_supports_task(
    task: TaskBundle,
    profile: CapabilityProfile,
    capability: ToolCapability,
) -> bool:
    """Return whether a capability can satisfy the public task and bounds.

    This function accepts no oracle argument and intentionally has no access to
    scorer-only expected answers.
    """

    _require_public_task(task)
    validate_mcp_capability_profile(profile)
    if type(capability) is not ToolCapability:
        raise TypeError("capability must be a ToolCapability")
    if not _base_capability_supports_task(task, profile, capability):
        return False
    bounds = task.binding.bounds
    if bounds.max_pages > 1 and not capability.supports_pagination:
        return False
    if bounds.require_stable_ordering and not capability.stable_ordering:
        return False
    if bounds.require_total_count and not capability.reports_total_count:
        return False
    if task.claim_kind in {"set", "count"}:
        if capability.proof_strength is not ProofStrength.COMPLETE_ENUMERATION:
            return False
        if not capability.reports_truncation:
            return False
    return True


def classify_mcp_binding(
    task: TaskBundle,
    profile: CapabilityProfile,
) -> MCPBindingMode:
    """Classify a public task's declared binding against the pinned profile."""

    _require_public_task(task)
    validate_mcp_capability_profile(profile)
    declared = task.binding.mcp_binding_mode
    if task.binding.capability_profile_id != profile.profile_id or declared is None:
        return MCPBindingMode.BLOCKED
    if declared is MCPBindingMode.CYPHER_ENABLED:
        if not profile.cypher_enabled:
            return MCPBindingMode.BLOCKED
        cypher = capability_for_operation(
            profile,
            tool_name="cypher_query",
            operation="run",
        )
        if cypher is None or not capability_supports_task(task, profile, cypher):
            return MCPBindingMode.BLOCKED
        return MCPBindingMode.CYPHER_ENABLED
    if declared is MCPBindingMode.TOOL_ONLY:
        if any(
            capability_supports_task(task, profile, capability)
            for capability in profile.tools
            if capability.name != "cypher_query"
        ):
            return MCPBindingMode.TOOL_ONLY
    return MCPBindingMode.BLOCKED


def validate_mcp_binding(
    task: TaskBundle,
    profile: CapabilityProfile,
) -> MCPBindingMode:
    """Return a usable binding mode or fail closed for a blocked task."""

    classification = classify_mcp_binding(task, profile)
    if classification is MCPBindingMode.BLOCKED:
        raise ValueError("public task is blocked by the pinned MCP capability profile")
    return classification


def _capability_proves_negative(capability: ToolCapability) -> bool:
    return capability.proof_strength in {
        ProofStrength.COMPLETE_ENUMERATION,
        ProofStrength.BOUNDED_NEGATIVE,
    }


def classify_evidence_event(
    task: TaskBundle,
    profile: CapabilityProfile,
    *,
    kind: EvidenceEventKind,
    tool_name: str | None = None,
    operation: str | None = None,
    resource_uri: str | None = None,
    reason: str | None = None,
) -> EvidenceEvent:
    """Classify one observation using only a public task and capability profile."""

    _require_public_task(task)
    validate_mcp_capability_profile(profile)
    if type(kind) is not EvidenceEventKind:
        raise TypeError("kind must be an EvidenceEventKind")

    resolved_kind = kind
    if kind is EvidenceEventKind.RESOURCE_READ:
        tool_name = None
        operation = None
    elif kind is not EvidenceEventKind.INFRASTRUCTURE_FAILURE:
        if not tool_name or not operation:
            raise ValueError(f"{kind.value} classification requires a tool operation")
        capability = capability_for_operation(
            profile,
            tool_name=tool_name,
            operation=operation,
        )
        diagnostic_kinds = {
            EvidenceEventKind.INCONCLUSIVE_EMPTY,
            EvidenceEventKind.TRUNCATED,
            EvidenceEventKind.INVALID_ARGUMENTS,
            EvidenceEventKind.POLICY_REJECTION,
            EvidenceEventKind.IRRELEVANT,
        }
        if capability is None:
            if kind not in diagnostic_kinds:
                resolved_kind = EvidenceEventKind.IRRELEVANT
        elif kind not in diagnostic_kinds:
            if not capability_supports_task(task, profile, capability):
                resolved_kind = EvidenceEventKind.IRRELEVANT
            elif kind is EvidenceEventKind.VALID_NEGATIVE and (
                task.claim_kind not in {"absence", "decision"}
                or not _capability_proves_negative(capability)
            ):
                resolved_kind = EvidenceEventKind.INCONCLUSIVE_EMPTY
            elif kind is EvidenceEventKind.CONCLUSIVE_EMPTY and (
                not _capability_proves_negative(capability) or not capability.reports_truncation
            ):
                resolved_kind = EvidenceEventKind.INCONCLUSIVE_EMPTY

    return EvidenceEvent(
        kind=resolved_kind,
        task_fingerprint=task.task_fingerprint,
        capability_profile_fingerprint=profile.profile_fingerprint,
        tool_name=tool_name,
        operation=operation,
        resource_uri=resource_uri,
        reason=reason,
    )


def classify_tool_observation(
    task: TaskBundle,
    profile: CapabilityProfile,
    observation: ToolObservation,
) -> EvidenceEvent:
    """Derive an evidence event from raw MCP outcome facts.

    This fail-closed classifier prevents provider loops from declaring their own
    activity useful.  It intentionally sees only the public task contract.
    """

    _require_public_task(task)
    validate_mcp_capability_profile(profile)
    capability = capability_for_operation(
        profile,
        tool_name=observation.tool_name,
        operation=observation.operation,
    )
    event_args = {
        "tool_name": observation.tool_name,
        "operation": observation.operation,
    }

    if observation.infrastructure_failure:
        return classify_evidence_event(
            task,
            profile,
            kind=EvidenceEventKind.INFRASTRUCTURE_FAILURE,
            reason="tool infrastructure failure",
        )
    if observation.policy_rejected:
        return classify_evidence_event(
            task,
            profile,
            kind=EvidenceEventKind.POLICY_REJECTION,
            reason="tool policy rejection",
            **event_args,
        )
    if not observation.arguments_valid:
        return classify_evidence_event(
            task,
            profile,
            kind=EvidenceEventKind.INVALID_ARGUMENTS,
            reason="invalid model tool arguments",
            **event_args,
        )
    if not observation.succeeded:
        return classify_evidence_event(
            task,
            profile,
            kind=EvidenceEventKind.IRRELEVANT,
            reason="unsuccessful tool result without an infrastructure classification",
            **event_args,
        )
    if capability is None or not capability_supports_task(task, profile, capability):
        return classify_evidence_event(
            task,
            profile,
            kind=EvidenceEventKind.IRRELEVANT,
            reason="operation cannot prove the public claim",
            **event_args,
        )

    bounds = task.binding.bounds
    count = observation.result_count
    exceeds_bounds = (
        observation.output_bytes > min(bounds.max_output_bytes, capability.max_output_bytes)
        or observation.pages_received > bounds.max_pages
        or (count is not None and count > bounds.max_result_cardinality)
    )
    witness_claim = task.claim_kind in {"route", "decision"}
    incomplete_total = (
        capability.reports_total_count
        and not witness_claim
        and observation.complete
        and (
            observation.total_count is None
            or count is None
            or observation.total_count != count
        )
    )
    if observation.truncated or exceeds_bounds or incomplete_total:
        return classify_evidence_event(
            task,
            profile,
            kind=EvidenceEventKind.TRUNCATED,
            reason="tool evidence exceeded or failed its declared completeness bounds",
            **event_args,
        )

    if (
        count is not None
        and count > 0
        and not observation.complete
        and not witness_claim
    ):
        return classify_evidence_event(
            task,
            profile,
            kind=EvidenceEventKind.TRUNCATED,
            reason="positive tool evidence lacks a completeness proof",
            **event_args,
        )

    if count is not None and count > 0:
        return classify_evidence_event(
            task,
            profile,
            kind=EvidenceEventKind.USEFUL_POSITIVE,
            reason=(
                "claim-relevant bounded witness evidence"
                if witness_claim
                else "claim-relevant bounded complete evidence"
            ),
            **event_args,
        )

    if count == 0 and observation.negative_proof:
        return classify_evidence_event(
            task,
            profile,
            kind=EvidenceEventKind.VALID_NEGATIVE,
            reason="complete bounded negative proof",
            **event_args,
        )

    if count == 0 and observation.complete:
        return classify_evidence_event(
            task,
            profile,
            kind=EvidenceEventKind.CONCLUSIVE_EMPTY,
            reason="complete bounded empty result",
            **event_args,
        )

    return classify_evidence_event(
        task,
        profile,
        kind=EvidenceEventKind.INCONCLUSIVE_EMPTY,
        reason="empty or missing result without a completeness proof",
        **event_args,
    )


def validate_certified_mcp_loop(tool_loop: MCPToolLoop | str) -> MCPToolLoop:
    """Validate an explicit certified loop; ``auto`` always fails closed."""

    try:
        resolved = MCPToolLoop(tool_loop)
    except ValueError as exc:
        supported = ", ".join(loop.value for loop in CERTIFIED_MCP_TOOL_LOOPS)
        raise ValueError(f"unknown MCP tool loop; certified values: {supported}") from exc
    if resolved is MCPToolLoop.AUTO:
        raise ValueError("mcp_tool_loop='auto' is forbidden for certified v2 runs")
    return resolved


def initial_finalization_state(
    task: TaskBundle,
    profile: CapabilityProfile,
    *,
    tool_loop: MCPToolLoop | str,
    certified: bool = True,
) -> FinalizationState:
    """Create the immutable initial state for one MCP sample."""

    _require_public_task(task)
    validate_mcp_capability_profile(profile)
    validate_mcp_binding(task, profile)
    resolved_loop = validate_certified_mcp_loop(tool_loop) if certified else MCPToolLoop(tool_loop)
    return FinalizationState(
        task_fingerprint=task.task_fingerprint,
        capability_profile_fingerprint=profile.profile_fingerprint,
        tool_loop=resolved_loop,
        certified=certified,
    )


def _reduce_evidence_event(
    state: FinalizationState,
    event: EvidenceEvent,
) -> FinalizationState:
    if event.task_fingerprint != state.task_fingerprint:
        raise ValueError("evidence event task fingerprint mismatch")
    if event.capability_profile_fingerprint != state.capability_profile_fingerprint:
        raise ValueError("evidence event capability profile fingerprint mismatch")
    if state.is_terminal:
        return state
    if state.phase is FinalizationPhase.RETRY_SCHEMA_ONLY:
        if event.kind is not EvidenceEventKind.INFRASTRUCTURE_FAILURE:
            return state.model_copy(
                update={
                    "phase": FinalizationPhase.OUTPUT_INVALID,
                    "terminal_reason": "SCHEMA_RETRY_TOOL_USE",
                }
            )
    events = (*state.events, event)
    unlocked = any(item.unlocks_finalization for item in events)
    if event.kind is EvidenceEventKind.INFRASTRUCTURE_FAILURE:
        return state.model_copy(
            update={
                "events": events,
                "finalization_unlocked": unlocked,
                "phase": FinalizationPhase.INFRASTRUCTURE_FAILURE,
                "terminal_reason": "INFRASTRUCTURE_FAILURE",
            }
        )
    return state.model_copy(
        update={
            "events": events,
            "finalization_unlocked": unlocked,
            "phase": (FinalizationPhase.READY if unlocked else FinalizationPhase.COLLECTING),
        }
    )


def _reduce_finalization_attempt(
    state: FinalizationState,
    attempt: FinalizationAttempt,
) -> FinalizationState:
    if state.is_terminal:
        return state
    attempts = state.final_output_attempts + 1
    if attempt.status is FinalOutputStatus.VALID:
        if not state.finalization_unlocked:
            return state.model_copy(
                update={
                    "phase": FinalizationPhase.OUTPUT_INVALID,
                    "final_output_attempts": attempts,
                    "output_digest": attempt.output_digest,
                    "terminal_reason": "NO_CLAIM_RELEVANT_EVIDENCE",
                }
            )
        return state.model_copy(
            update={
                "phase": FinalizationPhase.FINALIZED,
                "final_output_attempts": attempts,
                "output_digest": attempt.output_digest,
            }
        )
    if state.finalization_unlocked and state.schema_retry_count == 0:
        return state.model_copy(
            update={
                "phase": FinalizationPhase.RETRY_SCHEMA_ONLY,
                "schema_retry_count": 1,
                "final_output_attempts": attempts,
                "output_digest": attempt.output_digest,
            }
        )
    return state.model_copy(
        update={
            "phase": FinalizationPhase.OUTPUT_INVALID,
            "final_output_attempts": attempts,
            "output_digest": attempt.output_digest,
            "terminal_reason": "OUTPUT_INVALID",
        }
    )


def reduce_finalization(
    state: FinalizationState,
    transition: EvidenceEvent | FinalizationAttempt,
) -> FinalizationState:
    """Apply one transition without mutating the prior state."""

    if type(state) is not FinalizationState:
        raise TypeError("state must be a FinalizationState")
    if isinstance(transition, EvidenceEvent):
        return _reduce_evidence_event(state, transition)
    if isinstance(transition, FinalizationAttempt):
        return _reduce_finalization_attempt(state, transition)
    raise TypeError("transition must be an EvidenceEvent or FinalizationAttempt")


def build_mcp_loop_conformance_matrix(
    task: TaskBundle,
    profile: CapabilityProfile,
    *,
    useful_event: EvidenceEvent,
) -> tuple[LoopConformanceResult, ...]:
    """Exercise the shared reducer against every certified provider-loop label."""

    _require_public_task(task)
    validate_mcp_capability_profile(profile)
    if not useful_event.unlocks_finalization:
        raise ValueError("conformance matrix requires a finalization-unlocking event")
    if useful_event.task_fingerprint != task.task_fingerprint:
        raise ValueError("conformance event task fingerprint mismatch")
    if useful_event.capability_profile_fingerprint != profile.profile_fingerprint:
        raise ValueError("conformance event capability profile fingerprint mismatch")

    ignored_tool_kinds = (
        EvidenceEventKind.IRRELEVANT,
        EvidenceEventKind.INCONCLUSIVE_EMPTY,
        EvidenceEventKind.TRUNCATED,
        EvidenceEventKind.POLICY_REJECTION,
        EvidenceEventKind.INVALID_ARGUMENTS,
    )
    results: list[LoopConformanceResult] = []
    for tool_loop in CERTIFIED_MCP_TOOL_LOOPS:
        ignored_state = initial_finalization_state(
            task,
            profile,
            tool_loop=tool_loop,
        )
        for kind in ignored_tool_kinds:
            ignored_state = reduce_finalization(
                ignored_state,
                useful_event.model_copy(update={"kind": kind}),
            )
        ignored_state = reduce_finalization(
            ignored_state,
            EvidenceEvent(
                kind=EvidenceEventKind.RESOURCE_READ,
                task_fingerprint=task.task_fingerprint,
                capability_profile_fingerprint=profile.profile_fingerprint,
                resource_uri="bloodhound://guides/ad",
            ),
        )

        ready_state = reduce_finalization(
            initial_finalization_state(task, profile, tool_loop=tool_loop),
            useful_event,
        )
        retry_state = reduce_finalization(
            ready_state,
            FinalizationAttempt(status=FinalOutputStatus.MALFORMED),
        )
        invalid_state = reduce_finalization(
            retry_state,
            FinalizationAttempt(status=FinalOutputStatus.MALFORMED),
        )
        infra_state = reduce_finalization(
            initial_finalization_state(task, profile, tool_loop=tool_loop),
            EvidenceEvent(
                kind=EvidenceEventKind.INFRASTRUCTURE_FAILURE,
                task_fingerprint=task.task_fingerprint,
                capability_profile_fingerprint=profile.profile_fingerprint,
                reason="conformance-infrastructure-failure",
            ),
        )
        results.append(
            LoopConformanceResult(
                tool_loop=tool_loop,
                ignored_events_unlock=ignored_state.finalization_unlocked,
                useful_event_phase=ready_state.phase,
                first_malformed_phase=retry_state.phase,
                second_malformed_phase=invalid_state.phase,
                infrastructure_phase=infra_state.phase,
                schema_retry_count=invalid_state.schema_retry_count,
            )
        )
    return tuple(results)


# Clear aliases for callers that name the domain object rather than the operation.
classify_binding = classify_mcp_binding
classify_tool_evidence = classify_evidence_event
reduce_mcp_finalization = reduce_finalization
mcp_loop_conformance_matrix = build_mcp_loop_conformance_matrix
