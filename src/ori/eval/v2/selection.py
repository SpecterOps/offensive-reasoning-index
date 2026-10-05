"""Bounded evaluator for the v2 typed selection language.

This is deliberately smaller than Cypher.  It supports the finite conjunctions,
typed filters, joins, and bounded homogeneous traversals needed by the current
ORI corpus.  Unsupported semantics fail closed during compilation.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .graph import GraphSnapshot
from .schema import (
    EdgeDirection,
    EntityRef,
    PredicateOperator,
    PropertyPredicate,
    RelationshipPattern,
    RelationshipSemantics,
    SelectionExpression,
)


class SelectionEvaluationError(ValueError):
    """Raised when a selection cannot be evaluated deterministically."""


@dataclass(frozen=True)
class SelectionResult:
    entities: tuple[EntityRef, ...]
    unpaged_count: int


def _property_map(snapshot: GraphSnapshot) -> dict[str, dict[str, Any]]:
    return {
        item.entity.object_id: {fact.key.casefold(): fact.value for fact in item.properties}
        for item in snapshot.objects
    }


def _matches_predicate(
    object_id: str,
    predicate: PropertyPredicate,
    properties: Mapping[str, Mapping[str, Any]],
) -> bool:
    entity_properties = properties.get(object_id, {})
    key = predicate.property_name.casefold()
    exists = key in entity_properties
    actual = entity_properties.get(key)
    expected = predicate.value
    if predicate.operator is PredicateOperator.EXISTS:
        return exists
    if predicate.operator is PredicateOperator.NOT_EXISTS:
        return not exists
    if predicate.operator is PredicateOperator.EQUALS:
        return exists and actual == expected
    if predicate.operator is PredicateOperator.NOT_EQUALS:
        return not exists or actual != expected
    if predicate.operator is PredicateOperator.IN:
        return exists and actual in expected
    if predicate.operator is PredicateOperator.NOT_IN:
        return not exists or actual not in expected
    raise SelectionEvaluationError(f"unsupported predicate operator: {predicate.operator}")


def _direct_pairs(
    snapshot: GraphSnapshot,
    pattern: RelationshipPattern,
) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for edge in snapshot.relationships:
        if edge.relationship != pattern.relationship:
            continue
        if pattern.direction is EdgeDirection.OUTBOUND:
            pairs.add((edge.source_id, edge.target_id))
        else:
            pairs.add((edge.target_id, edge.source_id))
    return pairs


def _bounded_pairs(
    snapshot: GraphSnapshot,
    pattern: RelationshipPattern,
) -> set[tuple[str, str]]:
    if pattern.semantics is RelationshipSemantics.EFFECTIVE:
        raise SelectionEvaluationError(
            "effective semantics require a separately certified derivation"
        )
    direct = _direct_pairs(snapshot, pattern)
    if pattern.semantics is RelationshipSemantics.DIRECT:
        return direct

    outgoing: dict[str, set[str]] = defaultdict(set)
    for source, target in direct:
        outgoing[source].add(target)
    pairs: set[tuple[str, str]] = set()
    for source in sorted(outgoing):
        frontier = {source}
        visited_depth: dict[str, int] = {source: 0}
        for depth in range(1, pattern.max_hops + 1):
            next_frontier: set[str] = set()
            for node in frontier:
                next_frontier.update(outgoing.get(node, ()))
            if depth >= pattern.min_hops:
                pairs.update((source, target) for target in next_frontier)
            for target in next_frontier:
                visited_depth.setdefault(target, depth)
            frontier = {
                target for target in next_frontier if visited_depth.get(target, depth) >= depth
            }
            if not frontier:
                break
    return pairs


def _object_type_index(snapshot: GraphSnapshot) -> dict[str, str]:
    return {entity.object_id: entity.object_type.casefold() for entity in snapshot.entities}


def _role_domains(
    snapshot: GraphSnapshot,
    selection: SelectionExpression,
    role_bindings: Mapping[str, str],
) -> dict[str, set[str]]:
    all_ids = {entity.object_id for entity in snapshot.entities}
    type_by_id = _object_type_index(snapshot)
    roles = {selection.projection_role}
    expected_types: dict[str, str] = {
        selection.projection_role: selection.projection_type.casefold()
    }
    for anchor in selection.anchors:
        roles.add(anchor.role)
        if anchor.object_type:
            expected_types[anchor.role] = anchor.object_type.casefold()
    for relationship in selection.relationships:
        roles.update((relationship.source_role, relationship.target_role))
        if relationship.source_type:
            expected_types[relationship.source_role] = relationship.source_type.casefold()
        if relationship.target_type:
            expected_types[relationship.target_role] = relationship.target_type.casefold()

    domains: dict[str, set[str]] = {}

    def type_matches(actual: str, expected: str | None) -> bool:
        if expected is None or expected in {"any", "object"}:
            return True
        if expected == "principal":
            return actual in {"user", "group", "computer"}
        return actual == expected

    for role in roles:
        if role in role_bindings:
            object_id = role_bindings[role]
            if object_id not in all_ids:
                raise SelectionEvaluationError(
                    f"role {role!r} resolves to unknown object {object_id!r}"
                )
            expected_type = expected_types.get(role)
            if not type_matches(type_by_id[object_id], expected_type):
                raise SelectionEvaluationError(
                    f"role {role!r} resolves to object {object_id!r} with incompatible type"
                )
            domains[role] = {object_id}
            continue
        expected_type = expected_types.get(role)
        domains[role] = {
            object_id for object_id in all_ids if type_matches(type_by_id[object_id], expected_type)
        }
    return domains


def _reduce_domains(
    snapshot: GraphSnapshot,
    selection: SelectionExpression,
    domains: dict[str, set[str]],
) -> dict[str, set[str]]:
    properties = _property_map(snapshot)
    for predicate in selection.predicates:
        domains[predicate.role] = {
            object_id
            for object_id in domains[predicate.role]
            if _matches_predicate(object_id, predicate, properties)
        }

    relation_pairs = [
        (pattern, _bounded_pairs(snapshot, pattern)) for pattern in selection.relationships
    ]
    changed = True
    while changed:
        changed = False
        for pattern, pairs in relation_pairs:
            allowed_sources = {
                source
                for source, target in pairs
                if source in domains[pattern.source_role] and target in domains[pattern.target_role]
            }
            allowed_targets = {
                target
                for source, target in pairs
                if source in domains[pattern.source_role] and target in domains[pattern.target_role]
            }
            if allowed_sources != domains[pattern.source_role]:
                domains[pattern.source_role] = allowed_sources
                changed = True
            if allowed_targets != domains[pattern.target_role]:
                domains[pattern.target_role] = allowed_targets
                changed = True
    return domains


def evaluate_selection(
    snapshot: GraphSnapshot,
    selection: SelectionExpression,
    *,
    role_bindings: Mapping[str, str] | None = None,
) -> SelectionResult:
    """Evaluate a finite typed selection against the certified archive graph."""

    bindings = dict(role_bindings or {})
    domains = _role_domains(snapshot, selection, bindings)
    domains = _reduce_domains(snapshot, selection, domains)
    projected_ids = sorted(
        domains[selection.projection_role],
        key=lambda object_id: (object_id.casefold(), object_id),
    )
    unpaged_count = len(projected_ids)
    start = selection.offset
    stop = None if selection.limit is None else start + selection.limit
    selected = projected_ids[start:stop]
    entities_by_id = {entity.object_id: entity for entity in snapshot.entities}
    return SelectionResult(
        entities=tuple(entities_by_id[object_id] for object_id in selected),
        unpaged_count=unpaged_count,
    )


def all_role_bindings(
    roles: Iterable[EntityRef],
) -> dict[str, str]:
    """Build a fail-closed role map from resolved oracle entities."""

    bindings: dict[str, str] = {}
    for entity in roles:
        previous = bindings.get(entity.role)
        if previous is not None and previous != entity.object_id:
            raise SelectionEvaluationError(
                f"logical role {entity.role!r} resolves to multiple objects"
            )
        bindings[entity.role] = entity.object_id
    return bindings
