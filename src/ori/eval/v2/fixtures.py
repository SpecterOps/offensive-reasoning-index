"""Deterministic adversarial fixtures and offline certification for v2 tasks."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from typing import Any

from pydantic import model_validator

from .comparator import COMPARATOR_FINGERPRINT, compare
from .compiler import CompiledTask, compiler_fingerprint
from .evidence import (
    EvidenceNormalizationError,
    normalize_evidence,
    validate_and_normalize_evidence,
)
from .fingerprint import canonical_sha256, certifier_fingerprint
from .graph import GraphSnapshot, graph_identity_resolver
from .identity import AmbiguousIdentityError, IdentityResolver
from .profiles import capability_profile_for_track
from .schema import (
    BoundedNegativePolicy,
    CertificationState,
    DecisionPolicy,
    EdgeWitness,
    EntityPropertyFact,
    EntityRef,
    EvidenceIR,
    ExactCountPolicy,
    ExactRoutePolicy,
    ExactSetPolicy,
    MechanismValidRoutePolicy,
    NegativeReasonCode,
    OracleBundle,
    PathStatus,
    PopulationScope,
    RelationshipSemantics,
    RouteClaim,
    RouteVariant,
    SelectionExpression,
    SetClaim,
    StrictModel,
    TaskCertification,
    VerdictStatus,
)

REQUIRED_FIXTURES = (
    "perfect",
    "wrong",
    "empty",
    "alias",
    "extra_entity",
    "decoy",
    "alternate_route",
)
ROUTE_ADVERSARIAL_FIXTURES = (
    "reversed_edge",
    "disconnected_path",
    "cycle",
    "malformed_answer",
    "source_mismatch",
)

_FP = "a" * 64


def _micro_entity(object_id: str, name: str, *, aliases: tuple[str, ...] = ()) -> EntityRef:
    return EntityRef(
        object_id=object_id,
        object_type="User" if object_id != "TARGET" else "Group",
        role="fixture",
        canonical_name=f"{name}@EXAMPLE.LOCAL",
        domain="EXAMPLE.LOCAL",
        aliases=aliases,
    )


_MICRO_ALICE = _micro_entity("ALICE", "alice", aliases=("alice",))
_MICRO_BOB = _micro_entity("BOB", "bob")
_MICRO_CAROL = _micro_entity("CAROL", "carol")
_MICRO_TARGET = _micro_entity("TARGET", "target")
_MICRO_CANONICAL = (
    EdgeWitness(source_id="ALICE", relationship="GenericAll", target_id="BOB"),
    EdgeWitness(source_id="BOB", relationship="MemberOf", target_id="TARGET"),
)
_MICRO_ALTERNATE = (
    EdgeWitness(source_id="ALICE", relationship="GenericAll", target_id="CAROL"),
    EdgeWitness(source_id="CAROL", relationship="MemberOf", target_id="TARGET"),
)


def _micro_oracle(
    task_id: str,
    *,
    expected_entities: tuple[EntityRef, ...] = (),
    route_variants: tuple[RouteVariant, ...] = (),
    graph_edges: tuple[EdgeWitness, ...] = (),
    forbidden_entity_ids: tuple[str, ...] = (),
):
    claim = (
        RouteClaim(
            kind="route",
            claim_id=task_id,
            source={"role": "source", "object_type": "User"},
            target={"role": "target", "object_type": "Group"},
            semantics=RelationshipSemantics.DIRECT,
            population_scope=PopulationScope.DECLARED,
            required_mechanisms=("GenericAll", "MemberOf"),
            max_hops=2,
        )
        if route_variants
        else SetClaim(
            kind="set",
            claim_id=task_id,
            selection=SelectionExpression(
                projection_role="result",
                projection_type="User",
            ),
            semantics=RelationshipSemantics.DIRECT,
            population_scope=PopulationScope.DECLARED,
        )
    )
    return OracleBundle.model_construct(
        oracle_id=f"oracle:{task_id}",
        task_id=task_id,
        claim=claim,
        claim_fingerprint=_FP,
        task_fingerprint=_FP,
        graph_fingerprint=_FP,
        graph_fact_registry_fingerprint=_FP,
        expected_entities=expected_entities,
        route_variants=route_variants,
        graph_edge_registry=graph_edges,
        required_mechanisms=("GenericAll", "MemberOf") if route_variants else (),
        source_id="ALICE" if route_variants else None,
        target_id="TARGET" if route_variants else None,
        forbidden_entity_ids=forbidden_entity_ids,
        oracle_fingerprint=_FP,
    )


@cache
def _prove_identity_alias() -> bool:
    task_id = "micrograph.identity-alias-v3"
    resolver = IdentityResolver((_MICRO_ALICE,))
    evidence = normalize_evidence(
        {"task_id": task_id, "entities": ["alice"]},
        resolver=resolver,
        task_id=task_id,
    )
    oracle = _micro_oracle(task_id, expected_entities=(_MICRO_ALICE,))
    return (
        compare(ExactSetPolicy(kind="exact_set"), oracle, evidence).status
        is VerdictStatus.CORRECT
    )


def _route_evidence(task_id: str) -> EvidenceIR:
    return EvidenceIR(
        task_id=task_id,
        entities=(_MICRO_ALICE, _MICRO_CAROL, _MICRO_TARGET),
        edges=_MICRO_ALTERNATE,
        path_status=PathStatus.FOUND,
        raw_digest=_FP,
    )


@cache
def _prove_route_decoy() -> bool:
    task_id = "micrograph.route-decoy-v3"
    oracle = _micro_oracle(
        task_id,
        route_variants=(RouteVariant(variant_id="canonical", edges=_MICRO_CANONICAL),),
        graph_edges=(*_MICRO_CANONICAL, *_MICRO_ALTERNATE),
        forbidden_entity_ids=("CAROL",),
    )
    return (
        compare(
            MechanismValidRoutePolicy(kind="mechanism_valid_route"),
            oracle,
            _route_evidence(task_id),
        ).status
        is VerdictStatus.INCORRECT
    )


@cache
def _prove_route_alternative() -> bool:
    task_id = "micrograph.route-alternative-v3"
    oracle = _micro_oracle(
        task_id,
        route_variants=(RouteVariant(variant_id="canonical", edges=_MICRO_CANONICAL),),
        graph_edges=(*_MICRO_CANONICAL, *_MICRO_ALTERNATE),
    )
    return (
        compare(
            MechanismValidRoutePolicy(kind="mechanism_valid_route"),
            oracle,
            _route_evidence(task_id),
        ).status
        is VerdictStatus.CORRECT
    )


@dataclass(frozen=True, slots=True)
class _CoverageProof:
    artifact_id: str
    fixture_name: str
    expectation: str
    prover: Callable[[], bool]


_POLICY_COVERAGE_PROOFS = {
    "micrograph.identity-alias-v3": _CoverageProof(
        "micrograph.identity-alias-v3",
        "alias",
        "unambiguous aliases normalize and satisfy an exact set",
        _prove_identity_alias,
    ),
    "micrograph.route-decoy-v3": _CoverageProof(
        "micrograph.route-decoy-v3",
        "decoy",
        "a graph-attested route through a forbidden identity is rejected",
        _prove_route_decoy,
    ),
    "micrograph.route-alternative-v3": _CoverageProof(
        "micrograph.route-alternative-v3",
        "alternate_route",
        "a graph-attested alternate mechanism route is accepted",
        _prove_route_alternative,
    ),
}
_POLICY_COVERAGE = {
    "alias_not_applicable": "micrograph.identity-alias-v3",
    "decoy_not_applicable": "micrograph.route-decoy-v3",
    "alternate_not_applicable": "micrograph.route-alternative-v3",
}
POLICY_COVERAGE_REGISTRY_FINGERPRINT = canonical_sha256(
    tuple(
        {
            "artifact_id": proof.artifact_id,
            "fixture_name": proof.fixture_name,
            "expectation": proof.expectation,
            "prover": proof.prover.__name__,
        }
        for _, proof in sorted(_POLICY_COVERAGE_PROOFS.items())
    )
)


def _require_executable_coverage(coverage_id: str) -> None:
    proof = _POLICY_COVERAGE_PROOFS.get(coverage_id)
    if proof is None:
        raise ValueError(f"unknown or stale fixture coverage ID: {coverage_id!r}")
    if not proof.prover():
        raise ValueError(f"fixture coverage proof failed: {coverage_id!r}")


def policy_coverage_artifacts() -> tuple[Mapping[str, str], ...]:
    """Return fingerprinted metadata for each executable micrograph proof."""

    return tuple(
        {
            "artifact_id": proof.artifact_id,
            "fixture_name": proof.fixture_name,
            "expectation": proof.expectation,
            "prover": proof.prover.__name__,
        }
        for _, proof in sorted(_POLICY_COVERAGE_PROOFS.items())
    )


class FixtureCase(StrictModel):
    name: str
    applicable: bool
    answer_payload: dict[str, Any] | None = None
    expected_status: VerdictStatus | None = None
    actual_status: VerdictStatus | None = None
    verdict_reason: str | None = None
    evidence: EvidenceIR | None = None
    normalization_error: str | None = None
    inapplicable_reason: str | None = None
    coverage_id: str | None = None

    @model_validator(mode="after")
    def result_is_complete(self) -> FixtureCase:
        if self.applicable:
            if self.answer_payload is None:
                raise ValueError("applicable fixtures require their exact answer payload")
            if self.expected_status is None or self.actual_status is None:
                raise ValueError("applicable fixtures require expected and actual verdicts")
            if self.actual_status is not self.expected_status:
                raise ValueError(
                    f"fixture {self.name} expected {self.expected_status} "
                    f"but got {self.actual_status}"
                )
            if self.evidence is None and self.normalization_error is None:
                raise ValueError("applicable fixture has no evidence or normalization result")
        elif not (
            self.inapplicable_reason
            and self.coverage_id
            and self.coverage_id in _POLICY_COVERAGE.values()
        ):
            raise ValueError(
                "inapplicable fixtures require a reason and registered policy coverage"
            )
        elif self.answer_payload is not None:
            raise ValueError("inapplicable fixtures cannot carry an answer payload")
        if not self.applicable and self.coverage_id is not None:
            _require_executable_coverage(self.coverage_id)
        return self


class TaskFixtureManifest(StrictModel):
    task_id: str
    task_fingerprint: str
    oracle_fingerprint: str
    comparator_fingerprint: str
    coverage_registry_fingerprint: str
    cases: tuple[FixtureCase, ...]
    fixture_fingerprint: str

    @model_validator(mode="after")
    def manifest_is_complete(self) -> TaskFixtureManifest:
        if self.coverage_registry_fingerprint != POLICY_COVERAGE_REGISTRY_FINGERPRINT:
            raise ValueError("fixture coverage registry is stale")
        names = [case.name for case in self.cases]
        if len(names) != len(set(names)):
            raise ValueError("fixture names must be unique")
        missing = sorted(set(REQUIRED_FIXTURES) - set(names))
        if missing:
            raise ValueError(f"fixture manifest is missing required cases: {missing}")
        expected = canonical_sha256(self, exclude_fields=("fixture_fingerprint",))
        if self.fixture_fingerprint != expected:
            raise ValueError("fixture fingerprint mismatch")
        return self


class OfflineCertification(StrictModel):
    certification: TaskCertification
    fixtures: TaskFixtureManifest


def validate_fixture_coverage_artifacts(
    certifications: Sequence[OfflineCertification],
) -> None:
    """Require every exemption ID to bind an executed, gradeable fixture artifact."""

    used_ids = {
        case.coverage_id
        for certification in certifications
        for case in certification.fixtures.cases
        if not case.applicable and case.coverage_id is not None
    }
    unknown_ids = sorted(used_ids - set(_POLICY_COVERAGE_PROOFS))
    if unknown_ids:
        raise ValueError(f"unknown fixture coverage IDs: {unknown_ids}")
    for coverage_id in sorted(_POLICY_COVERAGE_PROOFS):
        _require_executable_coverage(coverage_id)


def _entity_token(snapshot: GraphSnapshot, object_id: str) -> str:
    entity = snapshot.entity(object_id)
    candidate = entity.canonical_name or next(iter(entity.aliases), entity.object_id)
    try:
        resolved = graph_identity_resolver(snapshot).resolve(candidate)
    except AmbiguousIdentityError:
        return object_id
    return candidate if resolved == object_id else object_id


def _edge_payload(edge: EdgeWitness, snapshot: GraphSnapshot, *, aliases: bool) -> dict[str, Any]:
    return {
        "source_id": (_entity_token(snapshot, edge.source_id) if aliases else edge.source_id),
        "relationship": edge.relationship,
        "target_id": (_entity_token(snapshot, edge.target_id) if aliases else edge.target_id),
        "direction": edge.direction.value,
        "properties": {fact.key: fact.value for fact in edge.properties},
    }


def _property_payload(
    fact: EntityPropertyFact,
    snapshot: GraphSnapshot,
    *,
    aliases: bool,
) -> dict[str, Any]:
    return {
        "entity_id": (_entity_token(snapshot, fact.entity_id) if aliases else fact.entity_id),
        "key": fact.key,
        "value": fact.value,
    }


def _unique_properties(
    properties: Sequence[EntityPropertyFact],
) -> tuple[EntityPropertyFact, ...]:
    by_key = {
        (fact.entity_id.casefold(), fact.key.casefold(), repr(fact.value)): fact
        for fact in properties
    }
    return tuple(by_key[key] for key in sorted(by_key))


def _entity_payloads(
    snapshot: GraphSnapshot,
    object_ids: Sequence[str],
    *,
    aliases: bool,
) -> list[str]:
    return [
        _entity_token(snapshot, object_id) if aliases else object_id
        for object_id in dict.fromkeys(object_ids)
    ]


def _evidence_object_ids(
    *,
    entity_ids: Sequence[str] = (),
    edges: Sequence[EdgeWitness] = (),
    properties: Sequence[EntityPropertyFact] = (),
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            (
                *entity_ids,
                *(
                    endpoint
                    for edge in edges
                    for endpoint in (edge.source_id, edge.target_id)
                ),
                *(fact.entity_id for fact in properties),
            )
        )
    )


def _negative_requirements(
    task: CompiledTask,
) -> tuple[
    tuple[str, ...],
    tuple[EdgeWitness, ...],
    tuple[EntityPropertyFact, ...],
    tuple[NegativeReasonCode, ...],
]:
    checked_ids = tuple(
        dict.fromkeys(
            entity_id
            for witness in task.oracle.negative_witnesses
            for entity_id in witness.checked_entity_ids
        )
    )
    checked_edges = tuple(
        dict.fromkeys(
            edge for witness in task.oracle.negative_witnesses for edge in witness.checked_edges
        )
    )
    properties = _unique_properties(
        (
            *task.oracle.required_properties,
            *(
                fact
                for witness in task.oracle.negative_witnesses
                for fact in witness.checked_properties
            ),
        )
    )
    reasons = tuple(witness.reason_code for witness in task.oracle.negative_witnesses)
    return checked_ids, checked_edges, properties, reasons


def _perfect_payload(
    task: CompiledTask,
    snapshot: GraphSnapshot,
    *,
    aliases: bool = False,
) -> dict[str, Any]:
    policy = task.public.answer_policy
    payload: dict[str, Any] = {}
    if isinstance(policy, ExactSetPolicy):
        payload["entities"] = [
            (_entity_token(snapshot, entity.object_id) if aliases else entity.object_id)
            for entity in task.oracle.expected_entities
        ]
    elif isinstance(policy, ExactCountPolicy):
        payload["count"] = task.oracle.expected_count
    elif isinstance(policy, (ExactRoutePolicy, MechanismValidRoutePolicy)):
        route = task.oracle.route_variants[0].edges
        context = task.oracle.required_context
        properties = task.oracle.required_properties
        payload.update(
            {
                "entities": _entity_payloads(
                    snapshot,
                    _evidence_object_ids(
                        edges=(*route, *context),
                        properties=properties,
                    ),
                    aliases=aliases,
                ),
                "path_status": "found",
                "edges": [
                    _edge_payload(edge, snapshot, aliases=aliases)
                    for edge in route
                ],
                "supporting_edges": [
                    _edge_payload(edge, snapshot, aliases=aliases)
                    for edge in context
                ],
                "observed_properties": [
                    _property_payload(fact, snapshot, aliases=aliases)
                    for fact in properties
                ],
            }
        )
    elif isinstance(policy, DecisionPolicy):
        context = task.oracle.required_context
        properties = task.oracle.required_properties
        payload.update(
            {
                "decision": task.oracle.expected_decision,
                "entities": _entity_payloads(
                    snapshot,
                    _evidence_object_ids(
                        entity_ids=tuple(
                            entity.object_id
                            for entity in task.oracle.expected_entities
                        ),
                        edges=context,
                        properties=properties,
                    ),
                    aliases=aliases,
                ),
                "supporting_edges": [
                    _edge_payload(edge, snapshot, aliases=aliases)
                    for edge in context
                ],
                "observed_properties": [
                    _property_payload(fact, snapshot, aliases=aliases)
                    for fact in properties
                ],
            }
        )
    elif isinstance(policy, BoundedNegativePolicy):
        _checked_ids, _checked_edges, _properties, reasons = _negative_requirements(task)
        payload.update(
            {
                "path_status": "no_path",
                "negative_reason_codes": [reason.value for reason in reasons],
            }
        )
    else:  # pragma: no cover - schema union protects this
        raise TypeError(f"unsupported fixture policy: {type(policy).__name__}")
    return payload


def _known_extra_ids(
    task: CompiledTask,
    snapshot: GraphSnapshot,
    *,
    count: int,
) -> tuple[str, ...]:
    used = (
        {entity.object_id for entity in task.oracle.expected_entities}
        | {
            entity_id
            for edge in (
                *task.oracle.required_context,
                *(task.oracle.route_variants[0].edges if task.oracle.route_variants else ()),
            )
            for entity_id in (edge.source_id, edge.target_id)
        }
        | {
            entity_id
            for witness in task.oracle.negative_witnesses
            for entity_id in witness.checked_entity_ids
        }
    )
    extras = tuple(
        entity.object_id
        for entity in snapshot.entities
        if entity.object_id not in used
    )[:count]
    if len(extras) != count:
        raise ValueError(
            f"graph contains fewer than {count} extra entities for adversarial fixture"
        )
    return extras


def _known_extra_id(task: CompiledTask, snapshot: GraphSnapshot) -> str:
    return _known_extra_ids(task, snapshot, count=1)[0]


def _wrong_payload(task: CompiledTask, snapshot: GraphSnapshot) -> dict[str, Any]:
    payload = _perfect_payload(task, snapshot)
    policy = task.public.answer_policy
    if isinstance(policy, ExactSetPolicy):
        if payload["entities"]:
            payload["entities"] = payload["entities"][:-1]
        else:
            payload["entities"] = [_known_extra_id(task, snapshot)]
    elif isinstance(policy, ExactCountPolicy):
        payload["count"] = int(payload["count"]) + 1
    elif isinstance(policy, (ExactRoutePolicy, MechanismValidRoutePolicy)):
        payload["edges"][0]["relationship"] = "WrongRelationship"
    elif isinstance(policy, DecisionPolicy):
        payload["decision"] = not bool(payload["decision"])
    elif isinstance(policy, BoundedNegativePolicy):
        payload["negative_reason_codes"] = payload["negative_reason_codes"][:-1]
    return payload


def _extra_payload(task: CompiledTask, snapshot: GraphSnapshot) -> dict[str, Any]:
    payload = _perfect_payload(task, snapshot)
    extra_id = _known_extra_id(task, snapshot)
    payload.setdefault("entities", []).append(extra_id)
    return payload


def _empty_payload(task: CompiledTask) -> dict[str, Any]:
    policy = task.public.answer_policy
    if isinstance(policy, ExactSetPolicy):
        return {"entities": []}
    if isinstance(policy, ExactCountPolicy):
        return {"count": 0 if task.oracle.expected_count != 0 else 1}
    if isinstance(policy, (ExactRoutePolicy, MechanismValidRoutePolicy)):
        payload: dict[str, Any] = {
            "edges": [],
            "path_status": "found",
        }
        if "observed_properties" in task.public.answer_schema["required"]:
            payload["observed_properties"] = []
        return payload
    if isinstance(policy, DecisionPolicy):
        return {
            "decision": False,
            "entities": [],
            "supporting_edges": [],
            "observed_properties": [],
        }
    if isinstance(policy, BoundedNegativePolicy):
        return {
            "path_status": "no_path",
            "negative_reason_codes": [],
        }
    raise TypeError(f"unsupported fixture policy: {type(policy).__name__}")


def _alternate_route(
    task: CompiledTask,
) -> tuple[EdgeWitness, ...] | None:
    if not isinstance(task.public.answer_policy, MechanismValidRoutePolicy):
        return None
    sequence = task.oracle.required_mechanisms
    outgoing: dict[tuple[str, str], list[EdgeWitness]] = {}
    for edge in task.oracle.graph_edge_registry:
        outgoing.setdefault((edge.source_id, edge.relationship), []).append(edge)

    canonical = task.oracle.route_variants[0].edges
    candidates: list[tuple[EdgeWitness, ...]] = []

    def walk(
        current_id: str,
        index: int,
        route: tuple[EdgeWitness, ...],
        seen: frozenset[str],
    ) -> None:
        if index == len(sequence):
            if current_id == task.oracle.target_id and route != canonical:
                candidates.append(route)
            return
        for edge in outgoing.get((current_id, sequence[index]), ()):
            if edge.target_id in seen:
                continue
            walk(
                edge.target_id,
                index + 1,
                (*route, edge),
                seen | {edge.target_id},
            )

    if task.oracle.source_id is not None:
        walk(
            task.oracle.source_id,
            0,
            (),
            frozenset({task.oracle.source_id}),
        )
    return candidates[0] if candidates else None


def _case_from_payload(
    *,
    name: str,
    payload: Mapping[str, Any],
    expected_status: VerdictStatus,
    task: CompiledTask,
    resolver: IdentityResolver,
) -> FixtureCase:
    try:
        evidence = validate_and_normalize_evidence(
            payload,
            answer_schema=task.public.answer_schema,
            resolver=resolver,
            task_id=task.public.task_id,
        )
    except (EvidenceNormalizationError, ValueError) as exc:
        if expected_status is VerdictStatus.CORRECT:
            raise
        return FixtureCase(
            name=name,
            applicable=True,
            answer_payload=dict(payload),
            expected_status=expected_status,
            actual_status=VerdictStatus.INCORRECT,
            verdict_reason="OUTPUT_INVALID",
            normalization_error=str(exc),
        )
    verdict = compare(task.public.answer_policy, task.oracle, evidence)
    return FixtureCase(
        name=name,
        applicable=True,
        answer_payload=dict(payload),
        expected_status=expected_status,
        actual_status=verdict.status,
        verdict_reason=verdict.reason,
        evidence=evidence,
    )


def _inapplicable(name: str, reason_key: str, reason: str) -> FixtureCase:
    return FixtureCase(
        name=name,
        applicable=False,
        inapplicable_reason=reason,
        coverage_id=_POLICY_COVERAGE[reason_key],
    )


def build_fixture_manifest(
    task: CompiledTask,
    snapshot: GraphSnapshot,
) -> TaskFixtureManifest:
    """Build and execute the complete deterministic fixture matrix for one task."""

    resolver = graph_identity_resolver(snapshot)
    cases: list[FixtureCase] = [
        _case_from_payload(
            name="perfect",
            payload=_perfect_payload(task, snapshot),
            expected_status=VerdictStatus.CORRECT,
            task=task,
            resolver=resolver,
        ),
        _case_from_payload(
            name="wrong",
            payload=_wrong_payload(task, snapshot),
            expected_status=VerdictStatus.INCORRECT,
            task=task,
            resolver=resolver,
        ),
        _case_from_payload(
            name="empty",
            payload=_empty_payload(task),
            expected_status=VerdictStatus.INCORRECT,
            task=task,
            resolver=resolver,
        ),
    ]

    if isinstance(
        task.public.answer_policy,
        (ExactCountPolicy, BoundedNegativePolicy),
    ):
        cases.append(
            _inapplicable(
                "alias",
                "alias_not_applicable",
                "an integer count contains no graph identity; alias policy is covered "
                "by the shared identity micrograph",
            )
        )
    else:
        cases.append(
            _case_from_payload(
                name="alias",
                payload=_perfect_payload(task, snapshot, aliases=True),
                expected_status=VerdictStatus.CORRECT,
                task=task,
                resolver=resolver,
            )
        )

    cases.append(
        _case_from_payload(
            name="extra_entity",
            payload=_extra_payload(task, snapshot),
            expected_status=VerdictStatus.INCORRECT,
            task=task,
            resolver=resolver,
        )
    )

    if task.oracle.forbidden_edges and isinstance(
        task.public.answer_policy,
        (ExactRoutePolicy, MechanismValidRoutePolicy),
    ):
        decoy_payload = _perfect_payload(task, snapshot)
        decoy_payload["edges"] = [
            _edge_payload(edge, snapshot, aliases=False) for edge in task.oracle.forbidden_edges
        ]
        decoy_payload["entities"] = _entity_payloads(
            snapshot,
            _evidence_object_ids(
                edges=(
                    *task.oracle.forbidden_edges,
                    *task.oracle.required_context,
                ),
                properties=task.oracle.required_properties,
            ),
            aliases=False,
        )
        cases.append(
            _case_from_payload(
                name="decoy",
                payload=decoy_payload,
                expected_status=VerdictStatus.INCORRECT,
                task=task,
                resolver=resolver,
            )
        )
    else:
        cases.append(
            _inapplicable(
                "decoy",
                "decoy_not_applicable",
                "the claim declares no candidate-specific decoy; decoy rejection is "
                "covered by the shared route micrograph",
            )
        )

    alternate = _alternate_route(task)
    if alternate is not None:
        alternate_payload = _perfect_payload(task, snapshot)
        alternate_payload["edges"] = [
            _edge_payload(edge, snapshot, aliases=False) for edge in alternate
        ]
        alternate_payload["entities"] = _entity_payloads(
            snapshot,
            _evidence_object_ids(
                edges=(*alternate, *task.oracle.required_context),
                properties=task.oracle.required_properties,
            ),
            aliases=False,
        )
        cases.append(
            _case_from_payload(
                name="alternate_route",
                payload=alternate_payload,
                expected_status=VerdictStatus.CORRECT,
                task=task,
                resolver=resolver,
            )
        )
    else:
        cases.append(
            _inapplicable(
                "alternate_route",
                "alternate_not_applicable",
                "the certified graph contains no distinct route satisfying this claim; "
                "alternate-route policy is covered by the shared mechanism micrograph",
            )
        )

    if isinstance(
        task.public.answer_policy,
        (ExactRoutePolicy, MechanismValidRoutePolicy),
    ):
        perfect = _perfect_payload(task, snapshot)
        first_edge = perfect["edges"][0]
        reversed_payload = _perfect_payload(task, snapshot)
        reversed_payload["edges"][0] = {
            **first_edge,
            "direction": "inbound",
        }
        cases.append(
            _case_from_payload(
                name="reversed_edge",
                payload=reversed_payload,
                expected_status=VerdictStatus.INCORRECT,
                task=task,
                resolver=resolver,
            )
        )

        disconnected = _perfect_payload(task, snapshot)
        extra_source, extra_target = _known_extra_ids(task, snapshot, count=2)
        disconnected["edges"].append(
            {
                "source_id": extra_source,
                "relationship": "MemberOf",
                "target_id": extra_target,
            }
        )
        cases.append(
            _case_from_payload(
                name="disconnected_path",
                payload=disconnected,
                expected_status=VerdictStatus.INCORRECT,
                task=task,
                resolver=resolver,
            )
        )

        cycle = _perfect_payload(task, snapshot)
        cycle["edges"].append(
            {
                "source_id": task.oracle.target_id,
                "relationship": "MemberOf",
                "target_id": task.oracle.source_id,
            }
        )
        cases.append(
            _case_from_payload(
                name="cycle",
                payload=cycle,
                expected_status=VerdictStatus.INCORRECT,
                task=task,
                resolver=resolver,
            )
        )

        malformed = _perfect_payload(task, snapshot)
        malformed["edges"][0].pop("target_id")
        cases.append(
            _case_from_payload(
                name="malformed_answer",
                payload=malformed,
                expected_status=VerdictStatus.INCORRECT,
                task=task,
                resolver=resolver,
            )
        )

        source_mismatch = _perfect_payload(task, snapshot)
        source_mismatch["edges"][0]["source_id"] = extra_source
        cases.append(
            _case_from_payload(
                name="source_mismatch",
                payload=source_mismatch,
                expected_status=VerdictStatus.INCORRECT,
                task=task,
                resolver=resolver,
            )
        )

    payload = {
        "task_id": task.public.task_id,
        "task_fingerprint": task.public.task_fingerprint,
        "oracle_fingerprint": task.oracle.oracle_fingerprint,
        "comparator_fingerprint": COMPARATOR_FINGERPRINT,
        "coverage_registry_fingerprint": POLICY_COVERAGE_REGISTRY_FINGERPRINT,
        "cases": tuple(cases),
        "fixture_fingerprint": "0" * 64,
    }
    payload["fixture_fingerprint"] = canonical_sha256(
        payload,
        exclude_fields=("fixture_fingerprint",),
    )
    return TaskFixtureManifest.model_validate(payload)


def offline_certify(
    task: CompiledTask,
    snapshot: GraphSnapshot,
) -> OfflineCertification:
    """Execute fixtures and bind one task to an offline-certified state."""

    if task.public.binding.mcp_binding_mode == "native":
        raise ValueError("NATIVE_CERTIFICATION_UNAVAILABLE: native adapter replay is required")
    fixtures = build_fixture_manifest(task, snapshot)
    binding = task.public.binding
    capability_profile = capability_profile_for_track(binding.track)
    if capability_profile.profile_id != binding.capability_profile_id:
        raise ValueError(
            f"task {task.public.task_id} does not bind the pinned capability profile"
        )
    capability_fingerprint = capability_profile.profile_fingerprint
    bounds_fingerprint = canonical_sha256(binding.bounds)
    payload = {
        "task_id": task.public.task_id,
        "state": CertificationState.OFFLINE_CERTIFIED,
        "task_fingerprint": task.public.task_fingerprint,
        "oracle_fingerprint": task.oracle.oracle_fingerprint,
        "graph_fingerprint": snapshot.graph_fingerprint,
        "compiler_fingerprint": compiler_fingerprint(),
        "comparator_fingerprint": COMPARATOR_FINGERPRINT,
        "certifier_fingerprint": certifier_fingerprint(),
        "capability_profile_fingerprint": capability_fingerprint,
        "bounds_fingerprint": bounds_fingerprint,
        "certified_profile_id": None,
        "live_proof_fingerprint": None,
        "failures": (),
        "certification_fingerprint": "0" * 64,
    }
    payload["certification_fingerprint"] = canonical_sha256(
        payload,
        exclude_fields=("certification_fingerprint",),
    )
    return OfflineCertification(
        certification=TaskCertification.model_validate(payload),
        fixtures=fixtures,
    )
