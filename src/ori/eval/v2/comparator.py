"""Policy-driven v2 answer comparison.

This module intentionally has no dependency on legacy ``Task`` objects,
template IDs, or arbitrary task metadata. All correctness behavior is expressed
by a typed answer policy plus its sealed oracle.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path

from .fingerprint import canonical_sha256
from .graph import normalized_property_value
from .schema import (
    AnswerPolicy,
    BoundedNegativePolicy,
    ClosedRouteVariantsPolicy,
    DecisionPolicy,
    EdgeDirection,
    EdgeWitness,
    EntityPropertyFact,
    EvidenceIR,
    ExactCountPolicy,
    ExactRoutePolicy,
    ExactSetPolicy,
    MechanismValidRoutePolicy,
    OracleBundle,
    PathStatus,
    Verdict,
    VerdictDiagnostics,
    VerdictStatus,
)

COMPARATOR_VERSION = "ori-v2-comparator-8"
COMPARATOR_FINGERPRINT = canonical_sha256(
    {
        "component": "ori-v2-policy-comparator",
        "version": COMPARATOR_VERSION,
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
)


def _fold(value: str) -> str:
    return value.strip().casefold()


def _property_map(edge: EdgeWitness) -> dict[str, object]:
    return {
        _fold(fact.key): normalized_property_value(fact.key, fact.value)
        for fact in edge.properties
    }


def _edge_key(
    edge: EdgeWitness,
    *,
    include_properties: bool = True,
) -> tuple[object, ...]:
    base: tuple[object, ...] = (
        _fold(edge.source_id),
        _fold(edge.relationship),
        _fold(edge.target_id),
        edge.direction.value,
    )
    if not include_properties:
        return base
    properties = tuple(
        sorted(
            (
                _fold(fact.key),
                repr(normalized_property_value(fact.key, fact.value)),
            )
            for fact in edge.properties
        )
    )
    return (*base, properties)


def _edge_satisfies(actual: EdgeWitness, required: EdgeWitness) -> bool:
    if _edge_key(actual, include_properties=False) != _edge_key(
        required, include_properties=False
    ):
        return False
    actual_properties = _property_map(actual)
    return all(
        actual_properties.get(key) == value
        for key, value in _property_map(required).items()
    )


def _property_key(fact: EntityPropertyFact) -> tuple[object, ...]:
    return (
        _fold(fact.entity_id),
        _fold(fact.key),
        repr(normalized_property_value(fact.key, fact.value)),
    )


def _properties_cover(
    actual: Sequence[EntityPropertyFact],
    required: Sequence[EntityPropertyFact],
) -> bool:
    actual_keys = {_property_key(fact) for fact in actual}
    return all(_property_key(fact) in actual_keys for fact in required)


def _has_extra_properties(
    actual: Sequence[EntityPropertyFact],
    required: Sequence[EntityPropertyFact],
) -> bool:
    required_keys = {_property_key(fact) for fact in required}
    return any(_property_key(fact) not in required_keys for fact in actual)


def _has_extra_edges(
    actual: Sequence[EdgeWitness],
    required: Sequence[EdgeWitness],
) -> bool:
    return bool(_extra_edges(actual, required))


def _extra_edges(
    actual: Sequence[EdgeWitness],
    required: Sequence[EdgeWitness],
) -> tuple[EdgeWitness, ...]:
    return tuple(
        observed
        for observed in actual
        if not any(_edge_satisfies(observed, expected) for expected in required)
    )


def _edges_connect_to_evidence(
    edges: Sequence[EdgeWitness],
    *,
    seed_ids: set[str],
) -> bool:
    """Require every supporting-edge component to touch the claimed evidence."""

    connected = {_fold(entity_id) for entity_id in seed_ids}
    pending = list(edges)
    while pending:
        next_pending: list[EdgeWitness] = []
        progressed = False
        for edge in pending:
            source = _fold(edge.source_id)
            target = _fold(edge.target_id)
            if source in connected or target in connected:
                connected.update((source, target))
                progressed = True
            else:
                next_pending.append(edge)
        if not progressed:
            return False
        pending = next_pending
    return True


def _properties_belong_to_evidence(
    properties: Sequence[EntityPropertyFact],
    *,
    evidence_ids: set[str],
) -> bool:
    permitted = {_fold(entity_id) for entity_id in evidence_ids}
    return all(_fold(fact.entity_id) in permitted for fact in properties)


def _extras_are_graph_attested(
    oracle: OracleBundle,
    evidence: EvidenceIR,
) -> bool:
    return (
        evidence.graph_fact_attestation
        == oracle.graph_fact_registry_fingerprint
    )


def _traversal_endpoints(edge: EdgeWitness) -> tuple[str, str]:
    if edge.direction is EdgeDirection.INBOUND:
        return edge.target_id, edge.source_id
    return edge.source_id, edge.target_id


def _ordered_subsequence(required: Sequence[str], actual: Sequence[str]) -> bool:
    if not required:
        return True
    position = 0
    for item in actual:
        if _fold(item) == _fold(required[position]):
            position += 1
            if position == len(required):
                return True
    return False


def _route_shape_errors(
    edges: Sequence[EdgeWitness],
    *,
    require_ordered_edges: bool,
    forbid_cycles: bool,
) -> tuple[str, ...]:
    if not edges:
        return ("empty_route",)

    errors: set[str] = set()
    traversed: list[str] = []
    for index, edge in enumerate(edges):
        start, end = _traversal_endpoints(edge)
        if index == 0:
            traversed.extend((start, end))
        else:
            previous_end = _traversal_endpoints(edges[index - 1])[1]
            if require_ordered_edges and _fold(previous_end) != _fold(start):
                errors.add("disconnected_route")
            traversed.append(end)

    normalized_nodes = [_fold(node) for node in traversed]
    if forbid_cycles and len(set(normalized_nodes)) != len(normalized_nodes):
        errors.add("cyclic_route")
    if (
        len({_edge_key(edge, include_properties=False) for edge in edges})
        != len(edges)
    ):
        errors.add("duplicate_edge")
    return tuple(sorted(errors))


def _route_entity_ids(
    path_edges: Sequence[EdgeWitness],
    context_edges: Sequence[EdgeWitness],
) -> set[str]:
    return {
        *(edge.source_id for edge in (*path_edges, *context_edges)),
        *(edge.target_id for edge in (*path_edges, *context_edges)),
    }


def _route_constraint_errors(
    oracle: OracleBundle,
    evidence: EvidenceIR,
    *,
    require_ordered_edges: bool,
    forbid_cycles: bool,
) -> tuple[str, ...]:
    path_edges = evidence.edges
    context_edges = evidence.supporting_edges
    errors = set(
        _route_shape_errors(
            path_edges,
            require_ordered_edges=require_ordered_edges,
            forbid_cycles=forbid_cycles,
        )
    )
    if not path_edges:
        return tuple(sorted(errors))

    route_start = _traversal_endpoints(path_edges[0])[0]
    route_end = _traversal_endpoints(path_edges[-1])[1]
    if oracle.source_id is not None and _fold(route_start) != _fold(oracle.source_id):
        errors.add("wrong_source")
    if oracle.target_id is not None and _fold(route_end) != _fold(oracle.target_id):
        errors.add("wrong_target")

    observed_entity_ids = {
        _fold(entity_id)
        for entity_id in _route_entity_ids(path_edges, context_edges)
        | {entity.object_id for entity in evidence.entities}
    }
    if {_fold(item) for item in oracle.forbidden_entity_ids} & observed_entity_ids:
        errors.add("forbidden_entity")

    observed_edges = (*path_edges, *context_edges)
    if any(
        _edge_satisfies(observed, forbidden)
        for observed in observed_edges
        for forbidden in oracle.forbidden_edges
    ):
        errors.add("forbidden_edge")

    connected_entity_ids = {
        _fold(item) for item in _route_entity_ids(path_edges, context_edges)
    }
    declared_entity_ids = {_fold(entity.object_id) for entity in evidence.entities}
    if declared_entity_ids - connected_entity_ids:
        errors.add("disconnected_extra_entity")

    if any(
        not any(_edge_satisfies(actual, required) for actual in observed_edges)
        for required in oracle.required_context
    ):
        errors.add("missing_context")
    if not _properties_cover(
        evidence.observed_properties,
        oracle.required_properties,
    ):
        errors.add("missing_required_properties")
    return tuple(sorted(errors))


def _verdict(
    *,
    correct: bool,
    reason: str,
    oracle: OracleBundle,
    evidence: EvidenceIR,
    diagnostics: VerdictDiagnostics | None = None,
) -> Verdict:
    return Verdict(
        task_id=oracle.task_id,
        status=VerdictStatus.CORRECT if correct else VerdictStatus.INCORRECT,
        reason=reason,
        task_fingerprint=oracle.task_fingerprint,
        oracle_fingerprint=oracle.oracle_fingerprint,
        evidence_fingerprint=evidence.raw_digest,
        comparator_fingerprint=COMPARATOR_FINGERPRINT,
        diagnostics=diagnostics or VerdictDiagnostics(),
    )


def _compare_exact_set(
    oracle: OracleBundle,
    evidence: EvidenceIR,
) -> Verdict:
    expected_by_fold = {
        _fold(entity.object_id): entity.object_id for entity in oracle.expected_entities
    }
    actual_by_fold = {
        _fold(entity.object_id): entity.object_id for entity in evidence.entities
    }
    expected = set(expected_by_fold)
    actual = set(actual_by_fold)
    missing = tuple(expected_by_fold[item] for item in sorted(expected - actual))
    extra = tuple(actual_by_fold[item] for item in sorted(actual - expected))
    correct = not missing and not extra
    return _verdict(
        correct=correct,
        reason="EXACT_SET_MATCH" if correct else "EXACT_SET_MISMATCH",
        oracle=oracle,
        evidence=evidence,
        diagnostics=VerdictDiagnostics(
            missing_entity_ids=missing,
            extra_entity_ids=extra,
            precision=(
                len(expected & actual) / len(actual)
                if actual
                else float(not expected)
            ),
            recall=(
                len(expected & actual) / len(expected)
                if expected
                else float(not actual)
            ),
        ),
    )


def _compare_exact_count(
    oracle: OracleBundle,
    evidence: EvidenceIR,
) -> Verdict:
    correct = (
        evidence.count is not None
        and oracle.expected_count is not None
        and evidence.count == oracle.expected_count
    )
    return _verdict(
        correct=correct,
        reason="EXACT_COUNT_MATCH" if correct else "EXACT_COUNT_MISMATCH",
        oracle=oracle,
        evidence=evidence,
    )


def _compare_decision(
    policy: DecisionPolicy,
    oracle: OracleBundle,
    evidence: EvidenceIR,
) -> Verdict:
    decision_matches = (
        evidence.decision is not None
        and oracle.expected_decision is not None
        and evidence.decision is oracle.expected_decision
    )
    evidence_errors: list[str] = []
    if policy.require_supporting_evidence:
        if not (
            evidence.entities
            or evidence.edges
            or evidence.supporting_edges
            or evidence.observed_properties
        ):
            evidence_errors.append("missing_decision_evidence")
        observed_edges = (*evidence.edges, *evidence.supporting_edges)
        expected_ids = {
            _fold(entity.object_id) for entity in oracle.expected_entities
        }
        actual_ids = {_fold(entity.object_id) for entity in evidence.entities}
        if policy.require_evidence_entities:
            if not expected_ids.issubset(actual_ids):
                evidence_errors.append("missing_decision_entities")
        if policy.forbid_unrelated_entities:
            related_ids = {
                *expected_ids,
                *(
                    _fold(endpoint)
                    for edge in observed_edges
                    for endpoint in (edge.source_id, edge.target_id)
                ),
            }
            if actual_ids - related_ids:
                evidence_errors.append("unrelated_decision_entity")

        if any(
            not any(_edge_satisfies(actual, required) for actual in observed_edges)
            for required in oracle.required_context
        ):
            evidence_errors.append("missing_decision_context")
        if not _properties_cover(
            evidence.observed_properties,
            oracle.required_properties,
        ):
            evidence_errors.append("missing_decision_properties")
        extra_decision_edge_values = _extra_edges(
            observed_edges,
            oracle.required_context,
        )
        extra_decision_edges = bool(extra_decision_edge_values)
        if policy.forbid_extra_supporting_edges and extra_decision_edges:
            evidence_errors.append("extra_decision_supporting_edge")
        elif extra_decision_edges and not _extras_are_graph_attested(
            oracle,
            evidence,
        ):
            evidence_errors.append("unsealed_decision_supporting_edge")
        elif extra_decision_edges and not _edges_connect_to_evidence(
            extra_decision_edge_values,
            seed_ids={
                *(entity.object_id for entity in oracle.expected_entities),
                *(
                    endpoint
                    for edge in oracle.required_context
                    for endpoint in (edge.source_id, edge.target_id)
                ),
            },
        ):
            evidence_errors.append("disconnected_decision_supporting_edge")
        extra_decision_properties = _has_extra_properties(
            evidence.observed_properties,
            oracle.required_properties,
        )
        if policy.forbid_extra_properties and extra_decision_properties:
            evidence_errors.append("extra_decision_property")
        elif extra_decision_properties and not _extras_are_graph_attested(
            oracle,
            evidence,
        ):
            evidence_errors.append("unsealed_decision_property")
        elif extra_decision_properties and not _properties_belong_to_evidence(
            evidence.observed_properties,
            evidence_ids={
                *(entity.object_id for entity in evidence.entities),
                *(
                    endpoint
                    for edge in observed_edges
                    for endpoint in (edge.source_id, edge.target_id)
                ),
            },
        ):
            evidence_errors.append("decision_property_outside_evidence")

    correct = decision_matches and not evidence_errors
    return _verdict(
        correct=correct,
        reason=(
            "DECISION_MATCH"
            if correct
            else (
                f"DECISION_{evidence_errors[0].upper()}"
                if evidence_errors
                else "DECISION_MISMATCH"
            )
        ),
        oracle=oracle,
        evidence=evidence,
    )


def _route_diagnostics(
    expected_edges: Sequence[EdgeWitness],
    actual_edges: Sequence[EdgeWitness],
) -> VerdictDiagnostics:
    expected_keys = {_edge_key(edge, include_properties=False) for edge in expected_edges}
    actual_keys = {_edge_key(edge, include_properties=False) for edge in actual_edges}
    missing_edges = tuple(
        edge
        for edge in expected_edges
        if _edge_key(edge, include_properties=False) not in actual_keys
    )
    extra_edges = tuple(
        edge
        for edge in actual_edges
        if _edge_key(edge, include_properties=False) not in expected_keys
    )
    overlap = (
        len(expected_keys & actual_keys) / len(expected_keys | actual_keys)
        if expected_keys or actual_keys
        else 1.0
    )
    return VerdictDiagnostics(
        missing_edges=missing_edges,
        extra_edges=extra_edges,
        route_overlap=overlap,
    )


def _compare_route(
    policy: ExactRoutePolicy
    | MechanismValidRoutePolicy
    | ClosedRouteVariantsPolicy,
    oracle: OracleBundle,
    evidence: EvidenceIR,
) -> Verdict:
    constraint_errors = list(
        _route_constraint_errors(
            oracle,
            evidence,
            require_ordered_edges=policy.require_ordered_edges,
            forbid_cycles=policy.forbid_cycles,
        )
    )
    if evidence.path_status is not PathStatus.FOUND:
        constraint_errors.append("path_status_not_found")
    matching_variants = tuple(
        index
        for index, variant in enumerate(oracle.route_variants)
        if len(evidence.edges) == len(variant.edges)
        and all(
            _edge_satisfies(actual, required)
            for actual, required in zip(evidence.edges, variant.edges, strict=True)
        )
    )

    if isinstance(policy, ExactRoutePolicy):
        policy_match = bool(matching_variants) and matching_variants[0] == 0
        if not policy_match:
            constraint_errors.append("not_exact_route")
    elif isinstance(policy, ClosedRouteVariantsPolicy):
        policy_match = bool(matching_variants)
        if not policy_match:
            constraint_errors.append("not_closed_variant")
    else:
        required = oracle.required_mechanisms
        observed = tuple(edge.relationship for edge in evidence.edges)
        if policy.forbid_extra_edges:
            policy_match = tuple(map(_fold, required)) == tuple(map(_fold, observed))
        else:
            policy_match = _ordered_subsequence(required, observed)
        if not policy_match:
            constraint_errors.append("wrong_mechanism_sequence")
        if any(
            not any(
                _edge_satisfies(actual, registered)
                for registered in oracle.graph_edge_registry
            )
            for actual in evidence.edges
        ):
            constraint_errors.append("unsealed_graph_edge")

    extra_supporting_edge_values = _extra_edges(
        evidence.supporting_edges,
        oracle.required_context,
    )
    extra_supporting_edges = bool(extra_supporting_edge_values)
    if policy.forbid_extra_supporting_edges and extra_supporting_edges:
        constraint_errors.append("extra_supporting_edge")
    elif extra_supporting_edges and not _extras_are_graph_attested(
        oracle,
        evidence,
    ):
        constraint_errors.append("unsealed_supporting_edge")
    elif extra_supporting_edges and not _edges_connect_to_evidence(
        extra_supporting_edge_values,
        seed_ids={
            *(
                endpoint
                for edge in (*evidence.edges, *oracle.required_context)
                for endpoint in (edge.source_id, edge.target_id)
            ),
        },
    ):
        constraint_errors.append("disconnected_supporting_edge")
    extra_properties = _has_extra_properties(
        evidence.observed_properties,
        oracle.required_properties,
    )
    if policy.forbid_extra_properties and extra_properties:
        constraint_errors.append("extra_observed_property")
    elif extra_properties and not _extras_are_graph_attested(
        oracle,
        evidence,
    ):
        constraint_errors.append("unsealed_observed_property")
    elif extra_properties and not _properties_belong_to_evidence(
        evidence.observed_properties,
        evidence_ids={
            *(entity.object_id for entity in evidence.entities),
            *(
                endpoint
                for edge in (*evidence.edges, *evidence.supporting_edges)
                for endpoint in (edge.source_id, edge.target_id)
            ),
        },
    ):
        constraint_errors.append("property_outside_evidence")

    correct = policy_match and not constraint_errors
    expected_edges = (
        oracle.route_variants[0].edges if oracle.route_variants else ()
    )
    reason = (
        "ROUTE_VALID"
        if correct
        else f"ROUTE_{sorted(set(constraint_errors))[0].upper()}"
    )
    return _verdict(
        correct=correct,
        reason=reason,
        oracle=oracle,
        evidence=evidence,
        diagnostics=_route_diagnostics(expected_edges, evidence.edges),
    )


def _compare_bounded_negative(
    policy: BoundedNegativePolicy,
    oracle: OracleBundle,
    evidence: EvidenceIR,
) -> Verdict:
    checks_complete = bool(oracle.negative_witnesses) and all(
        witness.witness_absent for witness in oracle.negative_witnesses
    )
    if not policy.require_complete_proof:
        checks_complete = not oracle.route_variants
    oracle_proves_absence = not oracle.route_variants and checks_complete
    reported_absent = (
        evidence.path_status is PathStatus.NO_PATH or evidence.decision is False
    )
    required_reason_codes = set(oracle.claim.reason_codes)
    actual_reason_codes = set(evidence.negative_reason_codes)
    property_keys = {_property_key(fact) for fact in evidence.observed_properties}
    required_property_keys = {
        _property_key(fact) for fact in oracle.required_properties
    }
    reason_coverage = (
        actual_reason_codes == required_reason_codes
        if policy.require_reason_codes
        else True
    )
    property_coverage = required_property_keys.issubset(property_keys)
    context_coverage = all(
        any(
            _edge_satisfies(actual, required)
            for actual in evidence.supporting_edges
        )
        for required in oracle.required_context
    )
    closed_context = not (
        policy.forbid_extra_supporting_edges
        and _has_extra_edges(evidence.supporting_edges, oracle.required_context)
    )
    closed_properties = not (
        policy.forbid_extra_properties
        and _has_extra_properties(
            evidence.observed_properties,
            oracle.required_properties,
        )
    )
    extra_context_edges = _extra_edges(
        evidence.supporting_edges,
        oracle.required_context,
    )
    has_extra_context = bool(extra_context_edges)
    has_extra_properties = _has_extra_properties(
        evidence.observed_properties,
        oracle.required_properties,
    )
    graph_valid_extras = not (
        (has_extra_context or has_extra_properties)
        and not _extras_are_graph_attested(oracle, evidence)
    )
    checked_ids = {
        *(
            entity_id
            for witness in oracle.negative_witnesses
            for entity_id in witness.checked_entity_ids
        ),
        *(
            endpoint
            for edge in oracle.required_context
            for endpoint in (edge.source_id, edge.target_id)
        ),
    }
    connected_extras = not has_extra_context or _edges_connect_to_evidence(
        extra_context_edges,
        seed_ids=checked_ids,
    )
    property_scope = not has_extra_properties or _properties_belong_to_evidence(
        evidence.observed_properties,
        evidence_ids={
            *checked_ids,
            *(entity.object_id for entity in evidence.entities),
            *(
                endpoint
                for edge in evidence.supporting_edges
                for endpoint in (edge.source_id, edge.target_id)
            ),
        },
    )
    correct = (
        oracle_proves_absence
        and reported_absent
        and not evidence.edges
        and reason_coverage
        and property_coverage
        and context_coverage
        and closed_context
        and closed_properties
        and graph_valid_extras
        and connected_extras
        and property_scope
    )
    reason = "BOUNDED_NEGATIVE_VALID" if correct else "BOUNDED_NEGATIVE_INVALID"
    return _verdict(
        correct=correct,
        reason=reason,
        oracle=oracle,
        evidence=evidence,
    )


def compare(
    policy: AnswerPolicy,
    oracle: OracleBundle,
    evidence: EvidenceIR,
    /,
) -> Verdict:
    """Return a strict binary verdict for normalized evidence."""

    if oracle.task_id != evidence.task_id:
        return _verdict(
            correct=False,
            reason="TASK_ID_MISMATCH",
            oracle=oracle,
            evidence=evidence,
        )
    if evidence.truncated:
        return _verdict(
            correct=False,
            reason="TRUNCATED_EVIDENCE",
            oracle=oracle,
            evidence=evidence,
        )

    irrelevant_fields = False
    if isinstance(policy, ExactSetPolicy):
        irrelevant_fields = bool(
            evidence.edges
            or evidence.supporting_edges
            or evidence.observed_properties
            or evidence.negative_reason_codes
            or evidence.count is not None
            or evidence.decision is not None
            or evidence.path_status is not PathStatus.UNKNOWN
        )
    elif isinstance(policy, ExactCountPolicy):
        irrelevant_fields = bool(
            evidence.entities
            or evidence.edges
            or evidence.supporting_edges
            or evidence.observed_properties
            or evidence.negative_reason_codes
            or evidence.decision is not None
            or evidence.path_status is not PathStatus.UNKNOWN
        )
    elif isinstance(
        policy,
        (ExactRoutePolicy, MechanismValidRoutePolicy, ClosedRouteVariantsPolicy),
    ):
        irrelevant_fields = bool(
            evidence.count is not None
            or evidence.decision is not None
            or evidence.negative_reason_codes
        )
    elif isinstance(policy, DecisionPolicy):
        irrelevant_fields = bool(
            evidence.count is not None
            or evidence.negative_reason_codes
            or evidence.path_status is not PathStatus.UNKNOWN
        )
    elif isinstance(policy, BoundedNegativePolicy):
        irrelevant_fields = evidence.count is not None
    if irrelevant_fields:
        return _verdict(
            correct=False,
            reason="POLICY_FIELD_MISMATCH",
            oracle=oracle,
            evidence=evidence,
        )

    if isinstance(policy, ExactSetPolicy):
        return _compare_exact_set(oracle, evidence)
    if isinstance(policy, ExactCountPolicy):
        return _compare_exact_count(oracle, evidence)
    if isinstance(policy, DecisionPolicy):
        return _compare_decision(policy, oracle, evidence)
    if isinstance(
        policy,
        (ExactRoutePolicy, MechanismValidRoutePolicy, ClosedRouteVariantsPolicy),
    ):
        return _compare_route(policy, oracle, evidence)
    if isinstance(policy, BoundedNegativePolicy):
        return _compare_bounded_negative(policy, oracle, evidence)
    raise TypeError(f"Unsupported v2 answer policy: {type(policy).__name__}")
