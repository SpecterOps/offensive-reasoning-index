"""Public-claim adjudication of native results, without oracle supplementation.

This is proof-event classification, not certification or campaign admission.
Only implemented, mechanically complete proof shapes can unlock finalization.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field

from ori.eval.direct_query_safety import (
    _mask_literals_and_comments,
    normalize_query_for_fingerprint,
)

from .mcp import EvidenceEvent, EvidenceEventKind
from .native_capability import NativeCapabilityProfile, validate_native_capability_profile
from .native_mcp_projection import project_native_result
from .schema import EdgeDirection, EvidenceIR, NativeClaimEvidenceContract, TaskBundle


@dataclass
class NativeSetProofState:
    """Task-local native page coverage; uses the existing public query keys."""

    binding: tuple[str, str] | None = None
    total: tuple[str, int] | None = None
    pages: dict[str, dict[int, frozenset[str]]] = field(default_factory=dict)

    def observe(self, profile, task, query: str, evidence: EvidenceIR) -> bool:
        from .model_runtime import (
            _count_population_matches_page,
            _is_count_query,
            _page_window,
            _query_matches_public_claim,
            _query_population_key,
        )

        binding = (profile.profile_fingerprint, task.task_fingerprint)
        if self.binding not in (None, binding):
            raise ValueError("native set state cannot cross task/profile bindings")
        self.binding = binding
        bounds = task.binding.bounds
        if _is_count_query(query):
            if (not bounds.require_total_count or evidence.count is None
                    or not _complete_count_shape(task, query)
                    or not _query_matches_public_claim(
                        task, query, is_count=True, allow_set_companion_count=True,
                    )):
                return False
            total = (_query_population_key(query), evidence.count)
            if self.total is not None and self.total != total:
                raise ValueError("native set population/count changed during proof")
            self.total = total
        else:
            if (evidence.count is not None or evidence.edges
                    or not _query_matches_public_claim(task, query, is_count=False)):
                return False
            expected = task.acceptance_spec.selection.projection_type
            # Match the declarative selector's abstract Principal vocabulary;
            # concrete native labels still have to be mechanically present.
            accepted = {"User", "Group", "Computer"} if expected == "Principal" else {expected}
            if expected not in {"Any", "Object"} and any(
                entity.object_type not in accepted for entity in evidence.entities
            ):
                return False
            window = _page_window(query)
            key = _query_population_key(query)
            # CE graph nodes are deduplicated, so only a DISTINCT identity
            # projection can make graph cardinality authoritative as page rows.
            if (window is None or not key.endswith("|identity_distinct=true")
                    or window[1] != bounds.page_size or window[0] < bounds.result_offset
                    or (window[0] - bounds.result_offset) % bounds.page_size):
                return False
            ids = frozenset(entity.object_id for entity in evidence.entities)
            if len(ids) != len(evidence.entities) or len(ids) > bounds.page_size:
                return False
            pages = self.pages.setdefault(key, {})
            if window[0] in pages and pages[window[0]] != ids:
                raise ValueError("native set page changed during proof")
            pages[window[0]] = ids
            if not bounds.require_total_count:
                return (window[0] == bounds.result_offset
                        and len(ids) <= bounds.max_result_cardinality)
        if self.total is None:
            return False
        count_key, total_count = self.total
        if total_count > bounds.max_result_cardinality:
            return False
        for key, pages in self.pages.items():
            if not _count_population_matches_page(count_key, key):
                continue
            offsets = sorted(pages)
            if offsets != [bounds.result_offset + index * bounds.page_size
                           for index in range(len(offsets))]:
                continue
            if any(len(pages[offset]) != bounds.page_size for offset in offsets[:-1]):
                continue
            identities = set().union(*pages.values())
            if sum(map(len, pages.values())) == len(identities) == total_count:
                return True
        return False


def validate_native_task_binding(profile: NativeCapabilityProfile, task: TaskBundle) -> None:
    """Validate before backend dispatch, not after a mismatched call executed."""
    validate_native_capability_profile(profile)
    contract = task.binding.mcp_evidence_contract
    if (
        not isinstance(contract, NativeClaimEvidenceContract)
        or task.binding.capability_profile_id != profile.profile_id
        or contract.implementation_id != profile.implementation_id
    ):
        raise ValueError("native proof task/profile binding mismatch")


def _complete_count_shape(task: TaskBundle, query: str) -> bool:
    """The initial native count proof accepts one untransformed graph population."""
    query = normalize_query_for_fingerprint(query).replace("\x1f", " ")
    masked = _mask_literals_and_comments(query)
    if re.search(
        r"\b(?:WITH|UNWIND|CALL|UNION|LOAD|CREATE|MERGE|DELETE|SET|REMOVE|FOREACH|"
        r"OPTIONAL|SKIP|LIMIT|ORDER|EXISTS)\b", masked, re.IGNORECASE,
    ):
        return False
    returns = list(re.finditer(r"\bRETURN\b", masked, re.IGNORECASE))
    if len(returns) != 1:
        return False
    # No arithmetic, nullable/nonunique properties or multiple output columns.
    identifier = r"(?:[A-Za-z_][A-Za-z0-9_]*|`[^`]+`)"
    projection = re.fullmatch(
        rf"RETURN\s+COUNT\s*\(\s*(?P<distinct>DISTINCT\s+)?"
        rf"(?P<variable>{identifier}|\*)\s*\)(?:\s+AS\s+{identifier})?\s*;?\s*",
        query[returns[0].start():], re.IGNORECASE,
    )
    if projection is None:
        return False
    selection = task.acceptance_spec.selection
    if selection is None or selection.offset != 0 or selection.limit is not None:
        return False
    distinct = projection.group("distinct") is not None
    if distinct and projection.group("variable") == "*":
        return False
    # A single node population has one row per identity. Relationships may
    # multiply rows, so a distinct public selection needs explicit node DISTINCT.
    if selection.relationships and not distinct:
        return False
    return True


def _positive_route_is_observed(task: TaskBundle, evidence: EvidenceIR) -> bool:
    """Find bounded connectivity only in the returned, directed graph facts.

    This establishes claim-relevant execution evidence, not answer correctness.
    The eventual scorer still evaluates the submitted witness against the public
    mechanisms/properties and sealed oracle. No missing edge is manufactured.
    """
    public = {entity.role: entity for entity in task.input_entities}
    source = public.get(task.acceptance_spec.source_role)
    target = public.get(task.acceptance_spec.target_role)
    observed = {entity.object_id: entity for entity in evidence.entities}
    if source is None or target is None or source.object_id == target.object_id:
        return False
    for endpoint in (source, target):
        actual = observed.get(endpoint.object_id)
        if actual is None or actual.object_type != endpoint.object_type:
            return False
    adjacency: dict[str, set[str]] = {}
    for edge in evidence.edges:
        if edge.source_id not in observed or edge.target_id not in observed:
            return False
        start, end = edge.source_id, edge.target_id
        if edge.direction is EdgeDirection.INBOUND:
            start, end = end, start
        elif edge.direction is not EdgeDirection.OUTBOUND:
            return False
        adjacency.setdefault(start, set()).add(end)
    queue = deque([(source.object_id, 0)])
    visited = {source.object_id}
    while queue:
        start, hops = queue.popleft()
        if hops >= task.binding.bounds.max_hops:
            continue
        for end in adjacency.get(start, ()):
            if end == target.object_id:
                return True
            if end not in visited:
                visited.add(end)
                queue.append((end, hops + 1))
    return False


def classify_native_result(
    profile: NativeCapabilityProfile,
    task: TaskBundle,
    tool_name: str,
    arguments: dict,
    result: dict,
    *, set_state: NativeSetProofState | None = None,
) -> EvidenceEvent:
    """Classify actual native call data against its explicitly bound public task.

The caller must separately establish that the call actually executed under the
qualified backend guard. A fabricated dictionary is not an execution receipt.
"""
    validate_native_task_binding(profile, task)
    contract = task.binding.mcp_evidence_contract
    operation = arguments.get("info_type") if profile.implementation_id == "mwnickerson" else None
    alternatives = [
        alternative for alternative in contract.alternatives
        if alternative.tool_name == tool_name and alternative.operation == operation
    ]

    def event(kind: EvidenceEventKind, reason: str) -> EvidenceEvent:
        return EvidenceEvent(
            kind=kind, task_fingerprint=task.task_fingerprint,
            capability_profile_fingerprint=profile.profile_fingerprint,
            tool_name=tool_name, operation=operation or "invoke", reason=reason,
        )

    if not alternatives:
        return event(EvidenceEventKind.IRRELEVANT, "native_operation_not_a_declared_proof")
    projection = project_native_result(
        profile.implementation_id, tool_name, arguments, result, task,
    )
    if projection.status == "tool_error":
        return event(EvidenceEventKind.QUERY_ERROR, "native_tool_failed")
    if projection.status != "observed" or projection.evidence is None:
        return event(EvidenceEventKind.IRRELEVANT, "native_evidence_inconclusive")
    if task.claim_kind == "set" and profile.implementation_id == "mwnickerson":
        query = arguments.get("query")
        if (set_state is not None and isinstance(query, str)
                and set_state.observe(profile, task, query, projection.evidence)):
            return event(EvidenceEventKind.USEFUL_POSITIVE, "native_complete_identity_pages")
        return event(EvidenceEventKind.IRRELEVANT, "native_set_coverage_incomplete")
    if (
        task.claim_kind == "absence"
        and profile.implementation_id in {"mwnickerson", "mordavid"}
        and any(alt.result_kind == "scalar_count" for alt in alternatives)
    ):
        from .query_contract import negative_query_scope_mode

        query, count = arguments.get("query"), projection.evidence.count
        scope = negative_query_scope_mode(task, query) if isinstance(query, str) else None
        if scope is None or count is None or (scope == "broader" and count > 0):
            return event(EvidenceEventKind.IRRELEVANT, "native_absence_scope_unproven")
        return event(
            EvidenceEventKind.VALID_NEGATIVE if count == 0 else EvidenceEventKind.USEFUL_POSITIVE,
            "native_complete_bounded_absence" if count == 0 else "native_exact_scope_contradiction",
        )
    if (
        profile.implementation_id == "armadin" and tool_name == "find_domains"
        and task.claim_kind == "set"
        and any(alt.result_kind == "entities" for alt in alternatives)
    ):
        # This pinned tool enumerates all Domain nodes without a limit. Its
        # explicit count is checked against unique returned identities by the
        # projector. It cannot prove a filtered population or a selected page.
        selection = task.acceptance_spec.selection
        evidence = projection.evidence
        if (
            selection is None or selection.projection_type != "Domain"
            or selection.anchors or selection.relationships or selection.predicates
            or selection.offset != 0 or selection.limit is not None
            or not selection.require_complete or arguments
            or evidence.count != len(evidence.entities)
            or any(entity.object_type != "Domain" for entity in evidence.entities)
        ):
            return event(EvidenceEventKind.IRRELEVANT, "native_domain_scope_unproven")
        return event(
            EvidenceEventKind.CONCLUSIVE_EMPTY if evidence.count == 0
            else EvidenceEventKind.USEFUL_POSITIVE,
            "native_complete_domain_set",
        )
    # Reuse the existing *public* query analysis, not the historical execution
    # adapter or its receipts. Both admitted query tools return actual Cypher
    # data; a scalar is relevant only when it counts the declared population.
    if (
        task.claim_kind == "count"
        and profile.implementation_id in {"mwnickerson", "mordavid"}
        and any(alt.result_kind == "scalar_count" for alt in alternatives)
    ):
        from .model_runtime import _is_count_query, _query_matches_public_claim

        query = arguments.get("query")
        if isinstance(query, str):
            query = normalize_query_for_fingerprint(query).replace("\x1f", " ")
        if (
            not isinstance(query, str)
            or not _complete_count_shape(task, query)
            or not _is_count_query(query)
            or not _query_matches_public_claim(task, query, is_count=True)
            or projection.evidence.count is None
        ):
            return event(EvidenceEventKind.IRRELEVANT, "native_count_scope_unproven")
        return event(
            EvidenceEventKind.CONCLUSIVE_EMPTY if projection.evidence.count == 0
            else EvidenceEventKind.USEFUL_POSITIVE,
            "native_complete_public_count",
        )
    if task.claim_kind == "route" and any(alt.result_kind == "path" for alt in alternatives):
        relevant = False
        if profile.implementation_id == "mwnickerson":
            from .model_runtime import _query_matches_public_claim

            query = arguments.get("query")
            relevant = isinstance(query, str) and _query_matches_public_claim(
                task, query, is_count=False,
            )
        elif profile.implementation_id == "armadin":
            public = {entity.role: entity for entity in task.input_entities}
            source = public.get(task.acceptance_spec.source_role)
            target = public.get(task.acceptance_spec.target_role)
            relevant = all(
                entity is not None and isinstance(entity.canonical_name, str)
                and isinstance(arguments.get(key), str)
                and arguments[key].casefold() == entity.canonical_name.casefold()
                for key, entity in (("source", source), ("target", target))
            )
        if relevant and _positive_route_is_observed(task, projection.evidence):
            return event(EvidenceEventKind.USEFUL_POSITIVE, "native_public_endpoint_path_observed")
        return event(EvidenceEventKind.IRRELEVANT, "native_route_scope_unproven")
    # Absence and paginated-set observations must cross their own
    # complete native adjudicators. Do not infer proof from an observed shape.
    return event(EvidenceEventKind.IRRELEVANT, "native_proof_shape_not_qualified")
