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

COMPARATOR_VERSION = "ori-v2-comparator-2"
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
    return {fact.key: fact.value for fact in edge.properties}


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
        sorted((_fold(fact.key), repr(fact.value)) for fact in edge.properties)
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
    return (_fold(fact.entity_id), _fold(fact.key), repr(fact.value))


def _properties_cover(
    actual: Sequence[EntityPropertyFact],
    required: Sequence[EntityPropertyFact],
) -> bool:
    actual_keys = {_property_key(fact) for fact in actual}
    return all(_property_key(fact) in actual_keys for fact in required)


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
    if len({_edge_key(edge) for edge in edges}) != len(edges):
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
        expected_ids = {
            _fold(entity.object_id) for entity in oracle.expected_entities
        }
        actual_ids = {_fold(entity.object_id) for entity in evidence.entities}
        if expected_ids != actual_ids:
            evidence_errors.append("missing_decision_entities")

        observed_edges = (*evidence.edges, *evidence.supporting_edges)
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
        if any(
            not any(
                _edge_satisfies(actual, registered)
                for registered in oracle.graph_edge_registry
            )
            for actual in observed_edges
        ):
            evidence_errors.append("unsealed_decision_edge")

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
    route_keys = tuple(_edge_key(edge) for edge in evidence.edges)
    matching_variants = tuple(
        index
        for index, variant in enumerate(oracle.route_variants)
        if route_keys == tuple(_edge_key(edge) for edge in variant.edges)
    )

    if isinstance(policy, ExactRoutePolicy):
        policy_match = bool(matching_variants) and matching_variants[0] == 0
        if not policy_match:
            constraint_errors.append("not_exact_route")
        expected_context = {_edge_key(edge) for edge in oracle.required_context}
        actual_context = {
            _edge_key(edge) for edge in evidence.supporting_edges
        }
        if actual_context - expected_context:
            constraint_errors.append("extra_supporting_edge")
        expected_properties = {
            _property_key(fact) for fact in oracle.required_properties
        }
        actual_properties = {
            _property_key(fact) for fact in evidence.observed_properties
        }
        if actual_properties - expected_properties:
            constraint_errors.append("extra_observed_property")
    elif isinstance(policy, ClosedRouteVariantsPolicy):
        policy_match = bool(matching_variants)
        if not policy_match:
            constraint_errors.append("not_closed_variant")
        if {
            _edge_key(edge) for edge in evidence.supporting_edges
        } - {_edge_key(edge) for edge in oracle.required_context}:
            constraint_errors.append("extra_supporting_edge")
    else:
        required = oracle.required_mechanisms
        observed = tuple(edge.relationship for edge in evidence.edges)
        if policy.forbid_extra_edges:
            policy_match = tuple(map(_fold, required)) == tuple(map(_fold, observed))
        else:
            policy_match = _ordered_subsequence(required, observed)
        if not policy_match:
            constraint_errors.append("wrong_mechanism_sequence")
        if policy.forbid_extra_edges and {
            _edge_key(edge) for edge in evidence.supporting_edges
        } - {_edge_key(edge) for edge in oracle.required_context}:
            constraint_errors.append("extra_supporting_edge")
        if any(
            not any(
                _edge_satisfies(actual, registered)
                for registered in oracle.graph_edge_registry
            )
            for actual in (*evidence.edges, *evidence.supporting_edges)
        ):
            constraint_errors.append("unsealed_graph_edge")

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
    required_reason_codes = {
        witness.reason_code for witness in oracle.negative_witnesses
    }
    actual_reason_codes = set(evidence.negative_reason_codes)
    required_properties = tuple(
        {
            _property_key(fact): fact
            for fact in (
                *oracle.required_properties,
                *(
                    fact
                    for witness in oracle.negative_witnesses
                    for fact in witness.checked_properties
                ),
            )
        }.values()
    )
    property_keys = {_property_key(fact) for fact in evidence.observed_properties}
    required_property_keys = {_property_key(fact) for fact in required_properties}
    checked_ids = {
        _fold(entity_id)
        for witness in oracle.negative_witnesses
        for entity_id in witness.checked_entity_ids
    }
    evidence_ids = {_fold(entity.object_id) for entity in evidence.entities}
    checked_edges = tuple(
        edge
        for witness in oracle.negative_witnesses
        for edge in witness.checked_edges
    )
    observed_proof_edges = (*evidence.edges, *evidence.supporting_edges)
    exact_reason_coverage = actual_reason_codes == required_reason_codes
    exact_property_coverage = property_keys == required_property_keys
    entity_coverage = checked_ids == evidence_ids
    checked_edge_coverage = all(
        any(_edge_satisfies(actual, required) for actual in observed_proof_edges)
        for required in checked_edges
    )
    correct = (
        oracle_proves_absence
        and reported_absent
        and not evidence.edges
        and exact_reason_coverage
        and exact_property_coverage
        and entity_coverage
        and checked_edge_coverage
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
