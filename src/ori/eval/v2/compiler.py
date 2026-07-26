"""Typed claim compiler and sealed-oracle builder for ORI protocol v2.

Legacy task IDs are used only at this authoring boundary.  They select a
declarative claim recipe; neither the Evidence IR nor the comparator can see a
legacy ``Task`` or ``template_id``.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from ori.eval.tasks import Task, generate_mcp_tasks, generate_tasks
from ori.relationships import canonical_relationship_kind

from .fingerprint import canonical_sha256
from .graph import GraphSnapshot
from .schema import (
    DIRECT_QUERY_POLICY_VERSION,
    MANIFEST_SCHEMA_VERSION,
    PROTOCOL_VERSION,
    AbsenceClaim,
    AnswerPolicy,
    BoundedNegativePolicy,
    ClaimSpec,
    CountClaim,
    DecisionClaim,
    DecisionPolicy,
    EdgeWitness,
    EntityPropertyFact,
    EntityRef,
    EntitySelector,
    ExactCountPolicy,
    ExactRoutePolicy,
    ExactSetPolicy,
    ExecutionBounds,
    MCPBindingMode,
    MechanismValidRoutePolicy,
    NegativeReasonCode,
    NegativeWitness,
    OracleBundle,
    PopulationScope,
    PredicateOperator,
    PropertyPredicate,
    RelationshipPattern,
    RelationshipSemantics,
    RouteClaim,
    RouteVariant,
    SelectionExpression,
    SetClaim,
    StrictModel,
    TaskBundle,
    Track,
    TrackBinding,
)
from .selection import evaluate_selection

COMPILER_VERSION = "ori-claim-compiler-v2.0.0"
DIRECT_CAPABILITY_PROFILE = "ori-direct-policy-v3-bhce-9.1"
MCP_CAPABILITY_PROFILE = "ori-mcp-009c88f-bhce-9.1-cypher-v1"
MCP_SERVER_REVISION = "009c88f41fae302becad4b00777a3749a0f6f0fa"

_FORBIDDEN_PUBLIC_KEYS = frozenset(
    {
        "correct",
        "expected_answer",
        "expected_count",
        "expected_decision",
        "expected_entities",
        "reference_cypher",
        "reference_nodes",
        "reference_results",
        "ref_result",
        "route_variants",
        "valid_node_names",
    }
)

_DECISION_TASKS = {
    "t3_unconstrained_delegation-01",
    "t4_adcs_esc1-01",
    "t5_adcs_to_delegation_composite-01",
}
_NEGATIVE_TASKS = {"t6_negative_control_invalid_cert-01"}
_ROUTE_CONTEXT_EDGE_INDEX = {
    "t6_host_session_pivot_rbcd_tier0-01": 4,
    "t6_acl_group_nesting_tier0-01": 0,
}
_NATIVE_ROUTE_TEMPLATES = {
    "mcp-shortest-path-has-session": "t1_has_session",
    "mcp-shortest-path-admin-to": "t1_admin_to",
    "mcp-shortest-path-nested-groups": "t2_nested_groups",
    "mcp-shortest-path-kerberoast-chain": "t2_kerberoast_chain",
    "mcp-shortest-path-acl-chain": "t2_acl_chain",
}


class V2CompileError(ValueError):
    """Raised when a legacy capability cannot be represented or certified."""


class MigrationRecord(StrictModel):
    product: str
    track: Track
    legacy_task_id: str
    legacy_template_id: str
    legacy_grade_mode: str
    family: str
    tier: int = Field(strict=True, ge=1)
    cost_band: Literal["low", "medium", "high"]
    path_concentration_key: str
    candidate_task_ids: tuple[str, ...]
    status: Literal["migrated", "replaced"]
    claim_kind: Literal["route", "set", "count", "decision", "absence"]
    semantics: RelationshipSemantics
    reference_source: Literal["archive_graph", "planted_path", "declarative_selection"]
    notes: tuple[str, ...] = ()


class CompiledTask(StrictModel):
    public: TaskBundle
    oracle: OracleBundle
    migration: MigrationRecord

    @model_validator(mode="after")
    def public_and_private_bindings_match(self) -> CompiledTask:
        if self.public.task_id != self.oracle.task_id:
            raise ValueError("public task and oracle task IDs differ")
        if self.public.task_fingerprint != self.oracle.task_fingerprint:
            raise ValueError("public task and oracle fingerprints differ")
        if self.public.claim_fingerprint != self.oracle.claim_fingerprint:
            raise ValueError("public task and oracle claim fingerprints differ")
        return self


class CompiledCorpus(StrictModel):
    protocol_version: Literal["ori-eval-protocol-v2"] = PROTOCOL_VERSION
    manifest_schema_version: Literal["ori-generated-manifest-v3"] = MANIFEST_SCHEMA_VERSION
    product: str
    track: Track
    seed: int = Field(strict=True)
    source_manifest_fingerprint: str
    graph_fingerprint: str
    graph_object_count: int = Field(strict=True, gt=0)
    compiler_fingerprint: str
    tasks: tuple[CompiledTask, ...]
    catalog_fingerprint: str

    @model_validator(mode="after")
    def catalog_fingerprint_matches(self) -> CompiledCorpus:
        expected = canonical_sha256(self, exclude_fields=("catalog_fingerprint",))
        if expected != self.catalog_fingerprint:
            raise ValueError(
                "catalog fingerprint mismatch: "
                f"declared={self.catalog_fingerprint} computed={expected}"
            )
        return self


class MigrationInventoryEntry(StrictModel):
    task_id: str
    legacy_task_id: str
    legacy_template_id: str
    track: Track
    claim_kind: Literal["route", "set", "count", "decision", "absence"]
    public_question: str
    legacy_grade_mode: str
    reference_source: Literal["archive_graph", "planted_path", "declarative_selection"]
    semantics: RelationshipSemantics
    bounds: ExecutionBounds
    migration_status: Literal["migrated", "replaced"]
    family: str
    tier: int = Field(strict=True, ge=1)
    cost_band: Literal["low", "medium", "high"]
    path_concentration_key: str
    task_fingerprint: str
    oracle_fingerprint: str


class MigrationInventoryArtifact(StrictModel):
    schema_version: Literal["ori-eval-migration-inventory-v2"] = (
        "ori-eval-migration-inventory-v2"
    )
    protocol_version: Literal["ori-eval-protocol-v2"] = PROTOCOL_VERSION
    product: str
    track: Track
    seed: int = Field(strict=True)
    graph_fingerprint: str
    compiler_fingerprint: str
    entries: tuple[MigrationInventoryEntry, ...]
    inventory_fingerprint: str

    @model_validator(mode="after")
    def fingerprint_matches(self) -> MigrationInventoryArtifact:
        expected = canonical_sha256(
            self,
            exclude_fields=("inventory_fingerprint",),
        )
        if self.inventory_fingerprint != expected:
            raise ValueError("migration inventory fingerprint mismatch")
        return self


@dataclass(frozen=True)
class _ClaimDraft:
    legacy: Task
    claim: ClaimSpec
    policy: AnswerPolicy
    question_template: str
    resolved_roles: tuple[EntityRef, ...]
    expected_entities: tuple[EntityRef, ...] = ()
    expected_count: int | None = None
    expected_decision: bool | None = None
    route_variants: tuple[RouteVariant, ...] = ()
    graph_edge_registry: tuple[EdgeWitness, ...] = ()
    required_context: tuple[EdgeWitness, ...] = ()
    required_properties: tuple[EntityPropertyFact, ...] = ()
    source_id: str | None = None
    target_id: str | None = None
    forbidden_entity_ids: tuple[str, ...] = ()
    forbidden_edges: tuple[EdgeWitness, ...] = ()
    negative_witnesses: tuple[NegativeWitness, ...] = ()
    reference_source: Literal["archive_graph", "planted_path", "declarative_selection"] = (
        "archive_graph"
    )
    status: Literal["migrated", "replaced"] = "migrated"
    notes: tuple[str, ...] = ()


def compiler_fingerprint() -> str:
    """Bind compilation output to the exact semantic implementation sources."""

    module_paths = (
        Path(__file__),
        Path(__file__).with_name("schema.py"),
        Path(__file__).with_name("selection.py"),
    )
    source_digests = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in module_paths
    }
    return canonical_sha256(
        {
            "compiler_version": COMPILER_VERSION,
            "source_digests": source_digests,
        }
    )


def _paths_by_template(manifest: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    paths: dict[str, Mapping[str, Any]] = {}
    for path in manifest.get("planted_paths") or ():
        template_id = str(path.get("template_id") or "")
        if not template_id:
            raise V2CompileError("planted path has no template_id")
        if template_id in paths:
            raise V2CompileError(f"duplicate planted template: {template_id}")
        paths[template_id] = path
    return paths


def _entity_with_role(snapshot: GraphSnapshot, object_id: str, role: str) -> EntityRef:
    entity = snapshot.entity(object_id)
    return entity.model_copy(update={"role": role})


def _entity_named(
    snapshot: GraphSnapshot,
    canonical_name: str,
    *,
    role: str,
) -> EntityRef:
    matches = [
        entity
        for entity in snapshot.entities
        if (entity.canonical_name or "").casefold() == canonical_name.casefold()
    ]
    if len(matches) != 1:
        raise V2CompileError(
            f"expected one graph object named {canonical_name!r}, found {len(matches)}"
        )
    return matches[0].model_copy(update={"role": role})


def _domain_name(snapshot: GraphSnapshot, short_name: str) -> str:
    return f"{short_name}@{snapshot.domain}"


def _graph_properties(snapshot: GraphSnapshot, object_id: str) -> dict[str, Any]:
    for item in snapshot.objects:
        if item.entity.object_id == object_id:
            return {fact.key.casefold(): fact.value for fact in item.properties}
    raise V2CompileError(f"missing graph properties for {object_id}")


def _property_fact(
    snapshot: GraphSnapshot,
    object_id: str,
    property_name: str,
    *,
    expected: Any | None = None,
) -> EntityPropertyFact:
    properties = _graph_properties(snapshot, object_id)
    key = property_name.casefold()
    if key not in properties:
        raise V2CompileError(f"{object_id} has no property {property_name!r}")
    value = properties[key]
    if expected is not None and value != expected:
        raise V2CompileError(f"{object_id}.{property_name} is {value!r}, expected {expected!r}")
    return EntityPropertyFact(entity_id=object_id, key=property_name, value=value)


def _edge_from_raw(raw: Mapping[str, Any] | Sequence[Any]) -> EdgeWitness:
    if isinstance(raw, Mapping):
        source = raw.get("source")
        relationship = raw.get("edge")
        target = raw.get("target")
    elif len(raw) == 3:
        source, relationship, target = raw
    else:
        raise V2CompileError(f"invalid edge declaration: {raw!r}")
    return EdgeWitness(
        source_id=str(source),
        relationship=canonical_relationship_kind(str(relationship)),
        target_id=str(target),
    )


def _assert_edges_exist(
    snapshot: GraphSnapshot,
    edges: Sequence[EdgeWitness],
    *,
    purpose: str,
) -> None:
    missing = [
        edge
        for edge in edges
        if (edge.source_id, edge.relationship, edge.target_id) not in snapshot.edge_keys
    ]
    if missing:
        rendered = [f"{edge.source_id}-[{edge.relationship}]->{edge.target_id}" for edge in missing]
        raise V2CompileError(f"{purpose} contains graph-absent edges: {rendered}")


def _route_roles(
    snapshot: GraphSnapshot,
    path_edges: Sequence[EdgeWitness],
    context_edges: Sequence[EdgeWitness],
) -> tuple[tuple[EntityRef, ...], dict[str, str]]:
    if not path_edges:
        raise V2CompileError("route has no edges")
    ids = [path_edges[0].source_id, *(edge.target_id for edge in path_edges)]
    roles_by_id: dict[str, str] = {}
    resolved: list[EntityRef] = []
    for index, object_id in enumerate(ids):
        role = (
            "source" if index == 0 else "target" if index == len(ids) - 1 else f"path_{index:03d}"
        )
        roles_by_id.setdefault(object_id, role)
        resolved.append(_entity_with_role(snapshot, object_id, role))
    for index, edge in enumerate(context_edges, start=1):
        for endpoint_name, object_id in (
            ("source", edge.source_id),
            ("target", edge.target_id),
        ):
            if object_id in roles_by_id:
                continue
            role = f"context_{index:03d}_{endpoint_name}"
            roles_by_id[object_id] = role
            resolved.append(_entity_with_role(snapshot, object_id, role))
    return tuple(resolved), roles_by_id


def _relationship_pattern(
    edge: EdgeWitness,
    roles_by_id: Mapping[str, str],
    snapshot: GraphSnapshot,
) -> RelationshipPattern:
    return RelationshipPattern(
        source_role=roles_by_id[edge.source_id],
        relationship=edge.relationship,
        target_role=roles_by_id[edge.target_id],
        semantics=RelationshipSemantics.DIRECT,
        source_type=snapshot.entity(edge.source_id).object_type,
        target_type=snapshot.entity(edge.target_id).object_type,
    )


def _route_is_contiguous(edges: Sequence[EdgeWitness]) -> bool:
    return all(
        previous.target_id == current.source_id
        for previous, current in zip(edges, edges[1:], strict=False)
    )


def _route_registry_for_sequence(
    snapshot: GraphSnapshot,
    *,
    source_id: str,
    target_id: str,
    relationships: Sequence[str],
    context_edges: Sequence[EdgeWitness] = (),
) -> tuple[EdgeWitness, ...]:
    """Return only edges that can occupy a position in a matching bounded route."""

    sequence = tuple(relationships)
    forward: list[set[str]] = [{source_id}]
    for relationship in sequence:
        next_nodes = {
            edge.target_id
            for edge in snapshot.relationships
            if edge.relationship == relationship and edge.source_id in forward[-1]
        }
        forward.append(next_nodes)

    backward: list[set[str]] = [set() for _ in range(len(sequence) + 1)]
    backward[-1] = {target_id}
    for index in range(len(sequence) - 1, -1, -1):
        relationship = sequence[index]
        backward[index] = {
            edge.source_id
            for edge in snapshot.relationships
            if edge.relationship == relationship
            and edge.target_id in backward[index + 1]
        }

    registry: dict[tuple[str, str, str], EdgeWitness] = {}
    for index, relationship in enumerate(sequence):
        for edge in snapshot.relationships:
            if (
                edge.relationship == relationship
                and edge.source_id in forward[index]
                and edge.target_id in backward[index + 1]
            ):
                registry[(edge.source_id, edge.relationship, edge.target_id)] = edge
    for edge in context_edges:
        registry[(edge.source_id, edge.relationship, edge.target_id)] = edge
    return tuple(
        registry[key]
        for key in sorted(
            registry,
            key=lambda item: (
                item[0].casefold(),
                item[1].casefold(),
                item[2].casefold(),
            ),
        )
    )


def _path_for_task(
    task: Task,
    paths: Mapping[str, Mapping[str, Any]],
) -> Mapping[str, Any]:
    template_id = _NATIVE_ROUTE_TEMPLATES.get(task.id, task.template_id)
    path = paths.get(template_id)
    if path is None:
        raise V2CompileError(f"task {task.id!r} requires missing planted template {template_id!r}")
    return path


def _route_draft(
    task: Task,
    snapshot: GraphSnapshot,
    paths: Mapping[str, Mapping[str, Any]],
) -> _ClaimDraft:
    path = _path_for_task(task, paths)
    path_edges = tuple(_edge_from_raw(edge) for edge in path.get("path_edges") or ())
    context_edges = tuple(_edge_from_raw(edge) for edge in path.get("supporting_edges") or ())

    if task.id == "t2_nested_groups-02":
        path_edges = path_edges[1:3]
    moved_index = _ROUTE_CONTEXT_EDGE_INDEX.get(task.id)
    notes: list[str] = []
    status: Literal["migrated", "replaced"] = "migrated"
    if moved_index is not None:
        context_edges = (*context_edges, path_edges[moved_index])
        path_edges = tuple(edge for index, edge in enumerate(path_edges) if index != moved_index)
        status = "replaced"
        notes.append("non-contiguous prerequisite was reclassified as supporting context")

    if not _route_is_contiguous(path_edges):
        raise V2CompileError(f"task {task.id!r} has a non-contiguous route")
    _assert_edges_exist(snapshot, path_edges, purpose=f"{task.id} route")
    _assert_edges_exist(snapshot, context_edges, purpose=f"{task.id} context")

    expected_source = (
        path_edges[0].source_id
        if task.id == "t2_nested_groups-02"
        else str(path.get("source_node"))
    )
    expected_target = (
        path_edges[-1].target_id
        if task.id == "t2_nested_groups-02"
        else str(path.get("target_node"))
    )
    if path_edges[0].source_id != expected_source or path_edges[-1].target_id != expected_target:
        raise V2CompileError(f"task {task.id!r} route endpoints do not match its claim")

    resolved_roles, roles_by_id = _route_roles(snapshot, path_edges, context_edges)
    forbidden_edges = tuple(
        _edge_from_raw(edge)
        for edge in (
            *(path.get("decoy_edges") or ()),
            *(path.get("invalidated_edges") or ()),
        )
    )
    for edge in forbidden_edges:
        if edge.source_id not in roles_by_id:
            role = f"excluded_{len(roles_by_id):03d}_source"
            roles_by_id[edge.source_id] = role
            resolved_roles = (
                *resolved_roles,
                _entity_with_role(snapshot, edge.source_id, role),
            )
        if edge.target_id not in roles_by_id:
            role = f"excluded_{len(roles_by_id):03d}_target"
            roles_by_id[edge.target_id] = role
            resolved_roles = (
                *resolved_roles,
                _entity_with_role(snapshot, edge.target_id, role),
            )

    property_predicates: list[PropertyPredicate] = []
    required_properties: list[EntityPropertyFact] = []
    if task.id in {
        "t2_kerberoast_chain-01",
        "mcp-shortest-path-kerberoast-chain",
    }:
        source_id = path_edges[0].source_id
        property_predicates.append(
            PropertyPredicate(
                role=roles_by_id[source_id],
                property_name="hasspn",
                operator=PredicateOperator.EQUALS,
                value=True,
            )
        )
        required_properties.append(_property_fact(snapshot, source_id, "hasspn", expected=True))
    if task.id == "t6_kerberoast_privilege_chain_tier0-01":
        service_id = path_edges[0].target_id
        property_predicates.append(
            PropertyPredicate(
                role=roles_by_id[service_id],
                property_name="hasspn",
                operator=PredicateOperator.EQUALS,
                value=True,
            )
        )
        required_properties.append(_property_fact(snapshot, service_id, "hasspn", expected=True))
    exact_route = len(path_edges) == 1 or task.id == "t2_nested_groups-02"
    policy: AnswerPolicy = (
        ExactRoutePolicy(kind="exact_route")
        if exact_route
        else MechanismValidRoutePolicy(kind="mechanism_valid_route")
    )
    mechanism_sequence = tuple(edge.relationship for edge in path_edges)
    claim = RouteClaim(
        kind="route",
        claim_id=f"claim:{task.id}",
        source=EntitySelector(
            role="source",
            object_type=snapshot.entity(path_edges[0].source_id).object_type,
        ),
        target=EntitySelector(
            role="target",
            object_type=snapshot.entity(path_edges[-1].target_id).object_type,
        ),
        semantics=RelationshipSemantics.DIRECT,
        population_scope=PopulationScope.BENCHMARK_NAMESPACE,
        required_mechanisms=mechanism_sequence,
        required_context=tuple(
            _relationship_pattern(edge, roles_by_id, snapshot) for edge in context_edges
        ),
        required_properties=tuple(property_predicates),
        excluded_relationships=tuple(
            _relationship_pattern(edge, roles_by_id, snapshot) for edge in forbidden_edges
        ),
        mechanisms_are_ordered=True,
        max_hops=len(path_edges),
    )
    source_name = snapshot.entity(path_edges[0].source_id).canonical_name or path_edges[0].source_id
    target_name = (
        snapshot.entity(path_edges[-1].target_id).canonical_name or path_edges[-1].target_id
    )
    return _ClaimDraft(
        legacy=task,
        claim=claim,
        policy=policy,
        question_template=(
            f"Find a valid ordered graph route from {source_name} to {target_name}. "
            "Return every traversed edge in order and report supporting context separately."
        ),
        resolved_roles=tuple(resolved_roles),
        route_variants=(RouteVariant(variant_id="planted", edges=path_edges),),
        graph_edge_registry=_route_registry_for_sequence(
            snapshot,
            source_id=path_edges[0].source_id,
            target_id=path_edges[-1].target_id,
            relationships=mechanism_sequence,
            context_edges=context_edges,
        ),
        required_context=context_edges,
        required_properties=tuple(required_properties),
        source_id=path_edges[0].source_id,
        target_id=path_edges[-1].target_id,
        forbidden_edges=forbidden_edges,
        reference_source="planted_path",
        status=status,
        notes=tuple(notes),
    )


def _max_depth_to(
    snapshot: GraphSnapshot,
    *,
    relationship: str,
    target_id: str,
    cap: int = 12,
) -> int:
    incoming: dict[str, set[str]] = defaultdict(set)
    for edge in snapshot.relationships:
        if edge.relationship == relationship:
            incoming[edge.target_id].add(edge.source_id)
    frontier = {target_id}
    visited = {target_id}
    maximum = 1
    for depth in range(1, cap + 1):
        next_frontier = {
            source
            for target in frontier
            for source in incoming.get(target, ())
            if source not in visited
        }
        if not next_frontier:
            return maximum
        maximum = depth
        visited.update(next_frontier)
        frontier = next_frontier
    return maximum


def _max_depth_from(
    snapshot: GraphSnapshot,
    *,
    relationship: str,
    source_id: str,
    cap: int = 12,
) -> int:
    outgoing: dict[str, set[str]] = defaultdict(set)
    for edge in snapshot.relationships:
        if edge.relationship == relationship:
            outgoing[edge.source_id].add(edge.target_id)
    frontier = {source_id}
    visited = {source_id}
    maximum = 1
    for depth in range(1, cap + 1):
        next_frontier = {
            target
            for source in frontier
            for target in outgoing.get(source, ())
            if target not in visited
        }
        if not next_frontier:
            return maximum
        maximum = depth
        visited.update(next_frontier)
        frontier = next_frontier
    return maximum


def _direct_pattern(
    source_role: str,
    relationship: str,
    target_role: str,
    *,
    source_type: str | None = None,
    target_type: str | None = None,
) -> RelationshipPattern:
    return RelationshipPattern(
        source_role=source_role,
        relationship=relationship,
        target_role=target_role,
        semantics=RelationshipSemantics.DIRECT,
        source_type=source_type,
        target_type=target_type,
    )


def _transitive_pattern(
    source_role: str,
    relationship: str,
    target_role: str,
    *,
    max_hops: int,
    source_type: str | None = None,
    target_type: str | None = None,
) -> RelationshipPattern:
    return RelationshipPattern(
        source_role=source_role,
        relationship=relationship,
        target_role=target_role,
        semantics=RelationshipSemantics.TRANSITIVE,
        min_hops=1,
        max_hops=max(2, max_hops),
        source_type=source_type,
        target_type=target_type,
    )


def _selection_recipe(
    task: Task,
    snapshot: GraphSnapshot,
    paths: Mapping[str, Mapping[str, Any]],
) -> tuple[SelectionExpression, tuple[EntityRef, ...], str, RelationshipSemantics]:
    domain_admins = _entity_named(
        snapshot,
        _domain_name(snapshot, "DOMAIN ADMINS"),
        role="domain_admins",
    )
    da_depth = _max_depth_to(
        snapshot,
        relationship="MemberOf",
        target_id=domain_admins.object_id,
    )

    property_only: dict[str, tuple[str, tuple[PropertyPredicate, ...], str]] = {
        "t2_kerberoast_chain-02": (
            "User",
            (
                PropertyPredicate(
                    role="result",
                    property_name="hasspn",
                    operator=PredicateOperator.EQUALS,
                    value=True,
                ),
            ),
            "user accounts with an SPN",
        ),
        "global-kerberoastable": (
            "User",
            (
                PropertyPredicate(
                    role="result",
                    property_name="hasspn",
                    operator=PredicateOperator.EQUALS,
                    value=True,
                ),
            ),
            "user accounts with an SPN",
        ),
        "t3_unconstrained_delegation-02": (
            "Computer",
            (
                PropertyPredicate(
                    role="result",
                    property_name="unconstraineddelegation",
                    operator=PredicateOperator.EQUALS,
                    value=True,
                ),
                PropertyPredicate(
                    role="result",
                    property_name="isdc",
                    operator=PredicateOperator.NOT_EQUALS,
                    value=True,
                ),
            ),
            "non-domain-controller computers with unconstrained delegation",
        ),
        "global-unconstrained": (
            "Computer",
            (
                PropertyPredicate(
                    role="result",
                    property_name="unconstraineddelegation",
                    operator=PredicateOperator.EQUALS,
                    value=True,
                ),
                PropertyPredicate(
                    role="result",
                    property_name="isdc",
                    operator=PredicateOperator.NOT_EQUALS,
                    value=True,
                ),
            ),
            "non-domain-controller computers with unconstrained delegation",
        ),
        "t3_constrained_delegation-02": (
            "User",
            (
                PropertyPredicate(
                    role="result",
                    property_name="trustedtoauth",
                    operator=PredicateOperator.EQUALS,
                    value=True,
                ),
            ),
            "users with trusted-to-auth delegation enabled",
        ),
    }
    if task.id in property_only:
        object_type, predicates, description = property_only[task.id]
        return (
            SelectionExpression(
                predicates=predicates,
                projection_role="result",
                projection_type=object_type,
            ),
            (),
            description,
            RelationshipSemantics.DIRECT,
        )

    if task.id in {
        "t1_has_session-01",
        "global-privileged-sessions",
        "mcp-global-privileged-sessions",
    }:
        selection = SelectionExpression(
            anchors=(EntitySelector(role="domain_admins", object_type="Group"),),
            relationships=(
                _direct_pattern(
                    "result",
                    "HasSession",
                    "session_user",
                    source_type="Computer",
                    target_type="User",
                ),
                _transitive_pattern(
                    "session_user",
                    "MemberOf",
                    "domain_admins",
                    max_hops=da_depth,
                    source_type="User",
                    target_type="Group",
                ),
            ),
            projection_role="result",
            projection_type="Computer",
        )
        return (
            selection,
            (domain_admins,),
            "computers with sessions from transitive Domain Admin members",
            RelationshipSemantics.TRANSITIVE,
        )

    if task.id in {
        "t1_group_membership-02",
        "mcp-global-da-direct-members",
        "mcp-global-da-direct-member-count",
    }:
        result_type = "User" if task.id == "t1_group_membership-02" else "Principal"
        selection = SelectionExpression(
            anchors=(EntitySelector(role="domain_admins", object_type="Group"),),
            relationships=(
                _direct_pattern(
                    "result",
                    "MemberOf",
                    "domain_admins",
                    source_type=result_type,
                    target_type="Group",
                ),
            ),
            projection_role="result",
            projection_type=result_type,
        )
        return (
            selection,
            (domain_admins,),
            "direct members of the Domain Admins group",
            RelationshipSemantics.DIRECT,
        )

    if task.id == "global-da-members":
        selection = SelectionExpression(
            anchors=(EntitySelector(role="domain_admins", object_type="Group"),),
            relationships=(
                _transitive_pattern(
                    "result",
                    "MemberOf",
                    "domain_admins",
                    max_hops=da_depth,
                    source_type="User",
                    target_type="Group",
                ),
            ),
            projection_role="result",
            projection_type="User",
        )
        return (
            selection,
            (domain_admins,),
            "users with bounded transitive membership in Domain Admins",
            RelationshipSemantics.TRANSITIVE,
        )

    if task.id in {"global-admin-to", "mcp-global-admin-to"}:
        selection = SelectionExpression(
            anchors=(EntitySelector(role="domain_admins", object_type="Group"),),
            relationships=(
                _direct_pattern(
                    "domain_admins",
                    "AdminTo",
                    "result",
                    source_type="Group",
                    target_type="Computer",
                ),
            ),
            projection_role="result",
            projection_type="Computer",
        )
        return (
            selection,
            (domain_admins,),
            "computers with a direct Domain Admins AdminTo relationship",
            RelationshipSemantics.DIRECT,
        )

    path_source = paths["t1_group_membership"]["source_node"]
    membership_user = _entity_with_role(snapshot, str(path_source), "subject_user")
    membership_depth = _max_depth_from(
        snapshot,
        relationship="MemberOf",
        source_id=membership_user.object_id,
    )
    if task.id in {
        "mcp-user-privileged-group-memberships",
        "mcp-user-group-memberships-mmoore",
    }:
        predicates = (
            (
                PropertyPredicate(
                    role="result",
                    property_name="highvalue",
                    operator=PredicateOperator.EQUALS,
                    value=True,
                ),
            )
            if task.id == "mcp-user-privileged-group-memberships"
            else ()
        )
        selection = SelectionExpression(
            anchors=(EntitySelector(role="subject_user", object_type="User"),),
            relationships=(
                _transitive_pattern(
                    "subject_user",
                    "MemberOf",
                    "result",
                    max_hops=membership_depth,
                    source_type="User",
                    target_type="Group",
                ),
            ),
            predicates=predicates,
            projection_role="result",
            projection_type="Group",
        )
        description = (
            "high-value groups reached by bounded transitive membership from the subject user"
            if predicates
            else "groups reached by bounded transitive membership from the subject user"
        )
        return (
            selection,
            (membership_user,),
            description,
            RelationshipSemantics.TRANSITIVE,
        )

    session_path = paths["t1_has_session"]
    session_computer = _entity_with_role(
        snapshot, str(session_path["source_node"]), "session_computer"
    )
    session_user = _entity_with_role(snapshot, str(session_path["target_node"]), "session_user")
    if task.id == "mcp-computer-active-sessions":
        return (
            SelectionExpression(
                anchors=(EntitySelector(role="session_computer", object_type="Computer"),),
                relationships=(
                    _direct_pattern(
                        "session_computer",
                        "HasSession",
                        "result",
                        source_type="Computer",
                        target_type="User",
                    ),
                ),
                projection_role="result",
                projection_type="User",
            ),
            (session_computer,),
            "users with a direct session on the subject computer",
            RelationshipSemantics.DIRECT,
        )
    if task.id == "mcp-user-session-locations-privileged-user":
        return (
            SelectionExpression(
                anchors=(EntitySelector(role="session_user", object_type="User"),),
                relationships=(
                    _direct_pattern(
                        "result",
                        "HasSession",
                        "session_user",
                        source_type="Computer",
                        target_type="User",
                    ),
                ),
                projection_role="result",
                projection_type="Computer",
            ),
            (session_user,),
            "computers with a direct session for the subject user",
            RelationshipSemantics.DIRECT,
        )

    admin_path = paths["t1_admin_to"]
    dc = _entity_with_role(snapshot, str(admin_path["target_node"]), "subject_computer")
    if task.id == "mcp-computer-admin-users-dc01":
        return (
            SelectionExpression(
                anchors=(EntitySelector(role="subject_computer", object_type="Computer"),),
                relationships=(
                    _direct_pattern(
                        "result",
                        "AdminTo",
                        "subject_computer",
                        source_type="Principal",
                        target_type="Computer",
                    ),
                ),
                projection_role="result",
                projection_type="Principal",
            ),
            (dc,),
            "principals with a direct AdminTo relationship to the subject computer",
            RelationshipSemantics.DIRECT,
        )

    nested_path = paths["t2_nested_groups"]
    nested_edges = tuple(_edge_from_raw(edge) for edge in nested_path["path_edges"])
    infra_team = _entity_with_role(snapshot, nested_edges[0].target_id, "infra_team")
    server_admins = _entity_with_role(snapshot, nested_edges[2].target_id, "server_admins")
    file_server = _entity_with_role(snapshot, str(nested_path["target_node"]), "subject_computer")
    if task.id == "mcp-group-members-server-admins":
        return (
            SelectionExpression(
                anchors=(EntitySelector(role="server_admins", object_type="Group"),),
                relationships=(
                    _direct_pattern(
                        "result",
                        "MemberOf",
                        "server_admins",
                        source_type="Principal",
                        target_type="Group",
                    ),
                ),
                projection_role="result",
                projection_type="Principal",
            ),
            (server_admins,),
            "direct members of the SERVER-ADMINS group",
            RelationshipSemantics.DIRECT,
        )
    if task.id == "mcp-group-memberships-infra-team":
        return (
            SelectionExpression(
                anchors=(EntitySelector(role="infra_team", object_type="Group"),),
                relationships=(
                    _transitive_pattern(
                        "infra_team",
                        "MemberOf",
                        "result",
                        max_hops=3,
                        source_type="Group",
                        target_type="Group",
                    ),
                ),
                projection_role="result",
                projection_type="Group",
            ),
            (infra_team,),
            "groups reached from INFRA-TEAM by bounded transitive membership",
            RelationshipSemantics.TRANSITIVE,
        )
    if task.id == "mcp-group-admin-rights-server-admins":
        return (
            SelectionExpression(
                anchors=(EntitySelector(role="server_admins", object_type="Group"),),
                relationships=(
                    _direct_pattern(
                        "server_admins",
                        "AdminTo",
                        "result",
                        source_type="Group",
                        target_type="Computer",
                    ),
                ),
                projection_role="result",
                projection_type="Computer",
            ),
            (server_admins,),
            "computers with a direct AdminTo relationship from SERVER-ADMINS",
            RelationshipSemantics.DIRECT,
        )
    if task.id == "mcp-computer-admin-users-srv-file-01":
        return (
            SelectionExpression(
                anchors=(EntitySelector(role="subject_computer", object_type="Computer"),),
                relationships=(
                    _direct_pattern(
                        "result",
                        "AdminTo",
                        "subject_computer",
                        source_type="Principal",
                        target_type="Computer",
                    ),
                ),
                projection_role="result",
                projection_type="Principal",
            ),
            (file_server,),
            "principals with a direct AdminTo relationship to the subject computer",
            RelationshipSemantics.DIRECT,
        )

    unconstrained_path = paths["t3_unconstrained_delegation"]
    unconstrained_computer = _entity_with_role(
        snapshot, str(unconstrained_path["target_node"]), "subject_computer"
    )
    if task.id == "mcp-computer-active-sessions-unconstrained":
        return (
            SelectionExpression(
                anchors=(EntitySelector(role="subject_computer", object_type="Computer"),),
                relationships=(
                    _direct_pattern(
                        "subject_computer",
                        "HasSession",
                        "result",
                        source_type="Computer",
                        target_type="User",
                    ),
                ),
                projection_role="result",
                projection_type="User",
            ),
            (unconstrained_computer,),
            "users with direct sessions on the unconstrained-delegation computer",
            RelationshipSemantics.DIRECT,
        )

    constrained_path = paths["t3_constrained_delegation"]
    constrained_user = _entity_with_role(
        snapshot, str(constrained_path["source_node"]), "subject_user"
    )
    if task.id == "mcp-user-constrained-delegation-targets":
        return (
            SelectionExpression(
                anchors=(EntitySelector(role="subject_user", object_type="User"),),
                relationships=(
                    _direct_pattern(
                        "subject_user",
                        "AllowedToDelegate",
                        "result",
                        source_type="User",
                    ),
                ),
                projection_role="result",
                projection_type="Any",
            ),
            (constrained_user,),
            "objects reached by a direct AllowedToDelegate relationship from the subject user",
            RelationshipSemantics.DIRECT,
        )

    raise V2CompileError(f"task {task.id!r} has no declarative selection recipe")


def _selection_drafts(
    task: Task,
    snapshot: GraphSnapshot,
    paths: Mapping[str, Mapping[str, Any]],
) -> tuple[_ClaimDraft, ...]:
    selection, roles, description, semantics = _selection_recipe(task, snapshot, paths)
    base_result = evaluate_selection(
        snapshot,
        selection,
        role_bindings={entity.role: entity.object_id for entity in roles},
    )
    selections = (selection,)
    status: Literal["migrated", "replaced"] = "migrated"
    notes: tuple[str, ...] = ()
    if base_result.unpaged_count > 1000:
        selections = tuple(
            selection.model_copy(update={"offset": offset, "limit": 500})
            for offset in range(0, base_result.unpaged_count, 500)
        )
        status = "replaced"
        notes = (
            "oversized enumeration was replaced by deterministic object-ID pages",
            f"unpaged cardinality={base_result.unpaged_count}",
        )

    drafts: list[_ClaimDraft] = []
    for index, page_selection in enumerate(selections, start=1):
        result = evaluate_selection(
            snapshot,
            page_selection,
            role_bindings={entity.role: entity.object_id for entity in roles},
        )
        is_count = task.grade_mode == "row_count"
        claim_id = f"claim:{task.id}"
        question = (
            f"Return the exact count of {description}."
            if is_count
            else f"Return the complete exact set of {description}."
        )
        if len(selections) > 1:
            claim_id += f":page-{index:03d}"
            question = (
                f"Return deterministic page {index} of {len(selections)} for {description}, "
                f"ordered by object ID with offset {page_selection.offset} and "
                f"limit {page_selection.limit}."
            )
        claim: ClaimSpec
        policy: AnswerPolicy
        if is_count:
            claim = CountClaim(
                kind="count",
                claim_id=claim_id,
                selection=page_selection,
                semantics=semantics,
                population_scope=PopulationScope.BENCHMARK_NAMESPACE,
            )
            policy = ExactCountPolicy(kind="exact_count")
        else:
            claim = SetClaim(
                kind="set",
                claim_id=claim_id,
                selection=page_selection,
                semantics=semantics,
                population_scope=PopulationScope.BENCHMARK_NAMESPACE,
            )
            policy = ExactSetPolicy(kind="exact_set")
        drafts.append(
            _ClaimDraft(
                legacy=task,
                claim=claim,
                policy=policy,
                question_template=question,
                resolved_roles=roles,
                expected_entities=() if is_count else result.entities,
                expected_count=result.unpaged_count if is_count else None,
                reference_source="declarative_selection",
                status=status,
                notes=notes,
            )
        )
    return tuple(drafts)


def _decision_draft(
    task: Task,
    snapshot: GraphSnapshot,
    paths: Mapping[str, Mapping[str, Any]],
) -> _ClaimDraft:
    path = _path_for_task(task, paths)
    edges = tuple(_edge_from_raw(edge) for edge in path.get("path_edges") or ())
    _assert_edges_exist(snapshot, edges, purpose=f"{task.id} decision evidence")
    resolved_roles, roles_by_id = _route_roles(snapshot, edges[:1], edges[1:])
    relationship_patterns = tuple(
        _relationship_pattern(edge, roles_by_id, snapshot) for edge in edges
    )
    predicates: list[PropertyPredicate] = []
    required_properties: list[EntityPropertyFact] = []
    if task.id == "t3_unconstrained_delegation-01":
        computer_id = edges[0].source_id
        for key, expected in (
            ("unconstraineddelegation", True),
            ("isdc", False),
        ):
            predicates.append(
                PropertyPredicate(
                    role=roles_by_id[computer_id],
                    property_name=key,
                    operator=PredicateOperator.EQUALS,
                    value=expected,
                )
            )
            required_properties.append(
                _property_fact(snapshot, computer_id, key, expected=expected)
            )
        question = (
            "Decide whether the subject user has a direct session on the declared "
            "non-domain-controller computer with unconstrained delegation. Return "
            "the relationship and property evidence."
        )
    elif task.id in {"t4_adcs_esc1-01", "t5_adcs_to_delegation_composite-01"}:
        template_id = next(edge.target_id for edge in edges if edge.relationship == "Enroll")
        for key, expected in (
            ("authenticationenabled", True),
            ("enrolleesuppliessubject", True),
            ("requiresmanagerapproval", False),
        ):
            predicates.append(
                PropertyPredicate(
                    role=roles_by_id[template_id],
                    property_name=key,
                    operator=PredicateOperator.EQUALS,
                    value=expected,
                )
            )
            required_properties.append(
                _property_fact(snapshot, template_id, key, expected=expected)
            )
        question = (
            "Decide whether the declared objects form the complete certificate-abuse "
            "evidence chain"
            + (" and delegation branch" if task.id == "t5_adcs_to_delegation_composite-01" else "")
            + ". Return every supporting edge and decisive template property."
        )
    else:
        raise V2CompileError(f"task {task.id!r} has no decision recipe")

    subjects = tuple(
        EntitySelector(role=entity.role, object_type=entity.object_type)
        for entity in resolved_roles
    )
    claim = DecisionClaim(
        kind="decision",
        claim_id=f"claim:{task.id}",
        subjects=subjects,
        required_relationships=relationship_patterns,
        required_properties=tuple(predicates),
        semantics=RelationshipSemantics.DIRECT,
        population_scope=PopulationScope.BENCHMARK_NAMESPACE,
    )
    expected_entities = tuple(
        snapshot.entity(object_id)
        for object_id in dict.fromkeys(
            endpoint for edge in edges for endpoint in (edge.source_id, edge.target_id)
        )
    )
    return _ClaimDraft(
        legacy=task,
        claim=claim,
        policy=DecisionPolicy(kind="decision", require_supporting_evidence=True),
        question_template=question,
        resolved_roles=resolved_roles,
        expected_entities=expected_entities,
        expected_decision=True,
        graph_edge_registry=edges,
        required_context=edges,
        required_properties=tuple(required_properties),
        reference_source="planted_path",
        status="replaced",
        notes=("legacy non-route evidence was reclassified as a typed decision",),
    )


def _negative_draft(
    task: Task,
    snapshot: GraphSnapshot,
    paths: Mapping[str, Mapping[str, Any]],
) -> _ClaimDraft:
    path = _path_for_task(task, paths)
    partial_edges = tuple(_edge_from_raw(edge) for edge in path.get("path_edges") or ())
    _assert_edges_exist(snapshot, partial_edges, purpose=f"{task.id} partial evidence")
    resolved_roles, roles_by_id = _route_roles(snapshot, partial_edges, ())
    template_id = partial_edges[-1].target_id
    property_predicate = PropertyPredicate(
        role=roles_by_id[template_id],
        property_name="authenticationenabled",
        operator=PredicateOperator.EQUALS,
        value=False,
    )
    property_fact = _property_fact(
        snapshot,
        template_id,
        "authenticationenabled",
        expected=False,
    )
    reasons = (
        NegativeReasonCode.TEMPLATE_AUTHENTICATION_DISABLED,
        NegativeReasonCode.MISSING_PUBLISHED_TO,
        NegativeReasonCode.MISSING_PRIVILEGED_IDENTITY_TRANSITION,
    )
    claim = AbsenceClaim(
        kind="absence",
        claim_id=f"claim:{task.id}",
        source=EntitySelector(
            role="source",
            object_type=snapshot.entity(partial_edges[0].source_id).object_type,
        ),
        target=EntitySelector(
            role="target",
            object_type=snapshot.entity(str(path["target_node"])).object_type,
        ),
        relationships=("MemberOf", "Enroll", "PublishedTo"),
        blocking_properties=(property_predicate,),
        reason_codes=reasons,
        max_hops=12,
        semantics=RelationshipSemantics.DIRECT,
        population_scope=PopulationScope.BENCHMARK_NAMESPACE,
    )
    target_entity = _entity_with_role(snapshot, str(path["target_node"]), "target")
    resolved_roles = (*resolved_roles, target_entity)
    witnesses = (
        NegativeWitness(
            reason_code=NegativeReasonCode.TEMPLATE_AUTHENTICATION_DISABLED,
            checked_entity_ids=(template_id,),
            checked_edges=partial_edges,
            checked_properties=(property_fact,),
            max_hops=12,
            witness_absent=True,
        ),
        NegativeWitness(
            reason_code=NegativeReasonCode.MISSING_PUBLISHED_TO,
            checked_entity_ids=(template_id,),
            max_hops=12,
            witness_absent=True,
        ),
        NegativeWitness(
            reason_code=NegativeReasonCode.MISSING_PRIVILEGED_IDENTITY_TRANSITION,
            checked_entity_ids=(template_id, target_entity.object_id),
            max_hops=12,
            witness_absent=True,
        ),
    )
    return _ClaimDraft(
        legacy=task,
        claim=claim,
        policy=BoundedNegativePolicy(kind="bounded_negative"),
        question_template=(
            "Determine whether the declared certificate-enrollment branch can reach "
            "the privileged objective within 12 hops. If it cannot, return no_path, "
            "the decisive property evidence, and all applicable structured reason codes."
        ),
        resolved_roles=tuple(resolved_roles),
        graph_edge_registry=partial_edges,
        required_context=partial_edges,
        required_properties=(property_fact,),
        source_id=partial_edges[0].source_id,
        target_id=target_entity.object_id,
        negative_witnesses=witnesses,
        reference_source="planted_path",
        status="replaced",
        notes=("legacy negative explanation became a typed bounded-negative proof",),
    )


def _answer_schema(claim: ClaimSpec) -> dict[str, Any]:
    edge_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "source_id": {"type": "string"},
            "relationship": {"type": "string"},
            "target_id": {"type": "string"},
            "direction": {"enum": ["outbound", "inbound"]},
            "properties": {"type": "object"},
        },
        "required": ["source_id", "relationship", "target_id"],
    }
    entity_schema = {
        "type": "array",
        "items": {
            "oneOf": [
                {"type": "string"},
                {
                    "type": "object",
                    "properties": {
                        "object_id": {"type": "string"},
                        "name": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            ]
        },
    }
    base: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {},
        "required": [],
    }
    if claim.kind == "set":
        base["properties"] = {"entities": entity_schema}
        base["required"] = ["entities"]
    elif claim.kind == "count":
        base["properties"] = {"count": {"type": "integer", "minimum": 0}}
        base["required"] = ["count"]
    elif claim.kind == "route":
        base["properties"] = {
            "entities": entity_schema,
            "edges": {"type": "array", "items": edge_schema},
            "supporting_edges": {"type": "array", "items": edge_schema},
            "path_status": {"const": "found"},
        }
        base["required"] = ["edges", "path_status"]
    elif claim.kind == "decision":
        base["properties"] = {
            "decision": {"type": "boolean"},
            "entities": entity_schema,
            "supporting_edges": {"type": "array", "items": edge_schema},
            "observed_properties": {"type": "array"},
        }
        base["required"] = [
            "decision",
            "entities",
            "supporting_edges",
            "observed_properties",
        ]
    elif claim.kind == "absence":
        base["properties"] = {
            "path_status": {"const": "no_path"},
            "entities": entity_schema,
            "negative_reason_codes": {
                "type": "array",
                "items": {"type": "string"},
            },
            "observed_properties": {"type": "array"},
            "supporting_edges": {"type": "array", "items": edge_schema},
        }
        base["required"] = [
            "path_status",
            "entities",
            "negative_reason_codes",
            "observed_properties",
            "supporting_edges",
        ]
    return base


def _validate_public_question(question: str, claim: ClaimSpec) -> None:
    if not question.strip():
        raise V2CompileError("compiled question is empty")
    lowered = question.casefold()
    leaked = sorted(key for key in _FORBIDDEN_PUBLIC_KEYS if key in lowered)
    if leaked:
        raise V2CompileError(f"compiled question contains oracle vocabulary: {leaked}")
    placeholders = set(re.findall(r"\{([a-zA-Z0-9_]+)\}", question))
    if placeholders:
        raise V2CompileError(f"compiled question has unresolved roles: {sorted(placeholders)}")
    requested = {
        "set": ("set",),
        "count": ("count",),
        "route": ("edge",),
        "decision": ("decid", "evidence"),
        "absence": ("no_path", "reason"),
    }[claim.kind]
    if not all(token in lowered for token in requested):
        raise V2CompileError(
            f"compiled {claim.kind} question does not request its public answer fields"
        )


def _binding(
    track: Track,
    *,
    claim: ClaimSpec,
    expected_cardinality: int,
) -> TrackBinding:
    max_hops = (
        claim.max_hops
        if isinstance(claim, (RouteClaim, AbsenceClaim))
        else max(
            (relationship.max_hops for relationship in claim.selection.relationships),
            default=0,
        )
        if isinstance(claim, (SetClaim, CountClaim))
        else max(
            (
                relationship.max_hops
                for relationship in (
                    *claim.required_relationships,
                    *claim.required_route,
                )
            ),
            default=1,
        )
    )
    if track is Track.DIRECT:
        page_size = min(1000, max(expected_cardinality, 1))
        bounds = ExecutionBounds(
            max_hops=max_hops,
            max_result_cardinality=min(1000, max(expected_cardinality, 1)),
            page_size=page_size,
            max_pages=1,
            require_total_count=claim.kind in {"set", "count"},
            require_stable_ordering=True,
            max_output_bytes=524_288,
            max_transcript_bytes=1_048_576,
            max_tool_calls=0,
            timeout_seconds=60.0,
        )
        return TrackBinding(
            track=track,
            capability_profile_id=DIRECT_CAPABILITY_PROFILE,
            semantics=claim.semantics,
            bounds=bounds,
            direct_query_policy_version=DIRECT_QUERY_POLICY_VERSION,
        )

    page_size = 100
    max_pages = max(1, math.ceil(max(expected_cardinality, 1) / page_size))
    bounds = ExecutionBounds(
        max_hops=max_hops,
        max_result_cardinality=max(expected_cardinality, 1),
        page_size=page_size,
        max_pages=max_pages,
        require_total_count=claim.kind in {"set", "count"},
        require_stable_ordering=True,
        max_output_bytes=524_288,
        max_transcript_bytes=2_097_152,
        max_tool_calls=max(12, max_pages + 4),
        timeout_seconds=120.0,
    )
    return TrackBinding(
        track=track,
        capability_profile_id=MCP_CAPABILITY_PROFILE,
        semantics=claim.semantics,
        bounds=bounds,
        mcp_tool_loop="native-openai-compatible",
        mcp_resource_mode="off",
        mcp_binding_mode=MCPBindingMode.CYPHER_ENABLED,
    )


def _fingerprinted_task_bundle(
    *,
    task_id: str,
    product: str,
    claim: ClaimSpec,
    policy: AnswerPolicy,
    binding: TrackBinding,
    input_entities: tuple[EntityRef, ...],
    question: str,
) -> TaskBundle:
    _validate_public_question(question, claim)
    answer_schema = _answer_schema(claim)
    generic_instructions = (
        "Return only JSON that matches the declared answer schema.",
        "Use stable object IDs when available; aliases must resolve unambiguously.",
        "Do not claim completeness when any result is truncated or pagination is incomplete.",
    )
    claim_fingerprint = canonical_sha256(claim)
    prompt_fingerprint = canonical_sha256(
        {
            "question": question,
            "input_entities": input_entities,
            "answer_schema": answer_schema,
            "generic_instructions": generic_instructions,
        }
    )
    payload = {
        "task_id": task_id,
        "revision": 2,
        "product": product,
        "claim_kind": claim.kind,
        "answer_policy": policy,
        "binding": binding,
        "input_entities": input_entities,
        "question": question,
        "answer_schema": answer_schema,
        "generic_instructions": generic_instructions,
        "claim_fingerprint": claim_fingerprint,
        "prompt_fingerprint": prompt_fingerprint,
        "task_fingerprint": "0" * 64,
    }
    payload["task_fingerprint"] = canonical_sha256(
        {
            "protocol_version": PROTOCOL_VERSION,
            "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
            **payload,
        },
        exclude_fields=("task_fingerprint",),
    )
    return TaskBundle.model_validate(payload)


def _public_input_entities(draft: _ClaimDraft) -> tuple[EntityRef, ...]:
    """Return only solver-required selectors, never expected result entities."""

    if isinstance(draft.claim, (SetClaim, CountClaim)):
        candidates = draft.resolved_roles
    else:
        candidates = tuple(
            entity
            for entity in draft.resolved_roles
            if entity.role in {"source", "target"}
        )
    by_id: dict[str, EntityRef] = {}
    for entity in candidates:
        by_id.setdefault(entity.object_id, entity)
    return tuple(by_id.values())


def _question_with_public_inputs(
    question: str,
    input_entities: Sequence[EntityRef],
) -> str:
    if not input_entities:
        return question
    rendered = "; ".join(
        f"{entity.role}={entity.canonical_name or entity.object_id}"
        for entity in input_entities
    )
    return f"Inputs: {rendered}. {question}"


def _fingerprinted_oracle(
    *,
    draft: _ClaimDraft,
    public: TaskBundle,
    snapshot: GraphSnapshot,
) -> OracleBundle:
    payload = {
        "oracle_id": f"oracle:{canonical_sha256({'task_id': public.task_id})[:24]}",
        "task_id": public.task_id,
        "claim": draft.claim,
        "claim_fingerprint": public.claim_fingerprint,
        "task_fingerprint": public.task_fingerprint,
        "graph_fingerprint": snapshot.graph_fingerprint,
        "resolved_roles": draft.resolved_roles,
        "expected_entities": draft.expected_entities,
        "expected_count": draft.expected_count,
        "expected_decision": draft.expected_decision,
        "route_variants": draft.route_variants,
        "graph_edge_registry": draft.graph_edge_registry,
        "required_mechanisms": (
            draft.claim.required_mechanisms if isinstance(draft.claim, RouteClaim) else ()
        ),
        "required_context": draft.required_context,
        "required_properties": draft.required_properties,
        "source_id": draft.source_id,
        "target_id": draft.target_id,
        "forbidden_entity_ids": draft.forbidden_entity_ids,
        "forbidden_edges": draft.forbidden_edges,
        "negative_witnesses": draft.negative_witnesses,
        "oracle_fingerprint": "0" * 64,
    }
    payload["oracle_fingerprint"] = canonical_sha256(
        {
            "protocol_version": PROTOCOL_VERSION,
            **payload,
        },
        exclude_fields=("oracle_fingerprint",),
    )
    return OracleBundle.model_validate(payload)


def _candidate_id(
    product: str,
    track: Track,
    legacy_task_id: str,
    claim: ClaimSpec,
) -> str:
    suffix = ""
    if isinstance(claim, (SetClaim, CountClaim)) and claim.selection.limit is not None:
        suffix = f".page-{claim.selection.offset // claim.selection.limit + 1:03d}"
    return f"{product}.{track.value}.{legacy_task_id}{suffix}@2"


def _cost_band(binding: TrackBinding) -> Literal["low", "medium", "high"]:
    bounds = binding.bounds
    if (
        bounds.max_hops <= 1
        and bounds.max_result_cardinality <= 100
        and bounds.max_pages == 1
    ):
        return "low"
    if (
        bounds.max_hops <= 4
        and bounds.max_result_cardinality <= 500
        and bounds.max_pages <= 5
    ):
        return "medium"
    return "high"


def _drafts_for_task(
    task: Task,
    snapshot: GraphSnapshot,
    paths: Mapping[str, Mapping[str, Any]],
) -> tuple[_ClaimDraft, ...]:
    if task.id in _NEGATIVE_TASKS:
        return (_negative_draft(task, snapshot, paths),)
    if task.id in _DECISION_TASKS:
        return (_decision_draft(task, snapshot, paths),)
    if task.grade_mode in {"node_set", "row_count"}:
        return _selection_drafts(task, snapshot, paths)
    if task.grade_mode == "path_exists":
        return (_route_draft(task, snapshot, paths),)
    raise V2CompileError(f"task {task.id!r} uses unsupported legacy grade mode {task.grade_mode!r}")


def compile_legacy_product(
    manifest: Mapping[str, Any],
    snapshot: GraphSnapshot,
    *,
    product: str,
    track: Track,
) -> CompiledCorpus:
    """Compile a v1 generated product into explicit v2 public/private artifacts."""

    if manifest.get("schema_version") != "ori-generated-manifest-v2":
        raise V2CompileError(
            "legacy product compilation requires ori-generated-manifest-v2; "
            "v2 artifacts must be loaded through protocol dispatch"
        )
    if int(manifest.get("seed")) != snapshot.seed:
        raise V2CompileError("source manifest seed does not match graph snapshot")
    if str(manifest.get("domain")) != snapshot.domain:
        raise V2CompileError("source manifest domain does not match graph snapshot")
    paths = _paths_by_template(manifest)
    legacy_tasks = (
        generate_tasks(dict(manifest))
        if track is Track.DIRECT
        else generate_mcp_tasks(dict(manifest))
    )

    compiled: list[CompiledTask] = []
    for legacy in legacy_tasks:
        drafts = _drafts_for_task(legacy, snapshot, paths)
        candidate_ids = tuple(
            _candidate_id(product, track, legacy.id, draft.claim) for draft in drafts
        )
        for candidate_id, draft in zip(candidate_ids, drafts, strict=True):
            expected_cardinality = (
                draft.expected_count
                if draft.expected_count is not None
                else len(draft.expected_entities)
                if draft.expected_entities
                else len(draft.route_variants)
                if draft.route_variants
                else 1
            )
            binding = _binding(
                track,
                claim=draft.claim,
                expected_cardinality=expected_cardinality,
            )
            input_entities = _public_input_entities(draft)
            public = _fingerprinted_task_bundle(
                task_id=candidate_id,
                product=product,
                claim=draft.claim,
                policy=draft.policy,
                binding=binding,
                input_entities=input_entities,
                question=_question_with_public_inputs(
                    draft.question_template,
                    input_entities,
                ),
            )
            oracle = _fingerprinted_oracle(
                draft=draft,
                public=public,
                snapshot=snapshot,
            )
            migration = MigrationRecord(
                product=product,
                track=track,
                legacy_task_id=legacy.id,
                legacy_template_id=legacy.template_id,
                legacy_grade_mode=legacy.grade_mode,
                family=legacy.template_id,
                tier=legacy.tier,
                cost_band=_cost_band(binding),
                path_concentration_key=f"legacy:{legacy.template_id}",
                candidate_task_ids=candidate_ids,
                status=draft.status,
                claim_kind=draft.claim.kind,
                semantics=draft.claim.semantics,
                reference_source=draft.reference_source,
                notes=draft.notes,
            )
            compiled.append(CompiledTask(public=public, oracle=oracle, migration=migration))

    source_manifest_fingerprint = canonical_sha256(manifest)
    compiler_digest = compiler_fingerprint()
    payload = {
        "product": product,
        "track": track,
        "seed": snapshot.seed,
        "source_manifest_fingerprint": source_manifest_fingerprint,
        "graph_fingerprint": snapshot.graph_fingerprint,
        "graph_object_count": len(snapshot.objects),
        "compiler_fingerprint": compiler_digest,
        "tasks": tuple(compiled),
        "catalog_fingerprint": "0" * 64,
    }
    payload["catalog_fingerprint"] = canonical_sha256(
        {
            "protocol_version": PROTOCOL_VERSION,
            "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
            **payload,
        },
        exclude_fields=("catalog_fingerprint",),
    )
    corpus = CompiledCorpus.model_validate(payload)

    legacy_ids = {task.id for task in legacy_tasks}
    migrated_ids = {task.migration.legacy_task_id for task in corpus.tasks}
    if legacy_ids != migrated_ids:
        raise V2CompileError(
            f"incomplete migration: missing={sorted(legacy_ids - migrated_ids)} "
            f"unknown={sorted(migrated_ids - legacy_ids)}"
        )
    return corpus


def build_migration_inventory(
    corpus: CompiledCorpus,
) -> MigrationInventoryArtifact:
    entries = tuple(
        MigrationInventoryEntry(
            task_id=task.public.task_id,
            legacy_task_id=task.migration.legacy_task_id,
            legacy_template_id=task.migration.legacy_template_id,
            track=task.public.binding.track,
            claim_kind=task.public.claim_kind,
            public_question=task.public.question,
            legacy_grade_mode=task.migration.legacy_grade_mode,
            reference_source=task.migration.reference_source,
            semantics=task.public.binding.semantics,
            bounds=task.public.binding.bounds,
            migration_status=task.migration.status,
            family=task.migration.family,
            tier=task.migration.tier,
            cost_band=task.migration.cost_band,
            path_concentration_key=task.migration.path_concentration_key,
            task_fingerprint=task.public.task_fingerprint,
            oracle_fingerprint=task.oracle.oracle_fingerprint,
        )
        for task in corpus.tasks
    )
    payload = {
        "product": corpus.product,
        "track": corpus.track,
        "seed": corpus.seed,
        "graph_fingerprint": corpus.graph_fingerprint,
        "compiler_fingerprint": corpus.compiler_fingerprint,
        "entries": entries,
        "inventory_fingerprint": "0" * 64,
    }
    payload["inventory_fingerprint"] = canonical_sha256(
        {
            "schema_version": "ori-eval-migration-inventory-v2",
            "protocol_version": PROTOCOL_VERSION,
            **payload,
        },
        exclude_fields=("inventory_fingerprint",),
    )
    return MigrationInventoryArtifact.model_validate(payload)
