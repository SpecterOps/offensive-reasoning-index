"""Deterministic adversarial fixtures and offline certification for v2 tasks."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import model_validator

from .comparator import COMPARATOR_FINGERPRINT, compare
from .compiler import CompiledTask, compiler_fingerprint
from .evidence import EvidenceNormalizationError, validate_and_normalize_evidence
from .fingerprint import canonical_sha256, certifier_fingerprint
from .graph import GraphSnapshot, graph_identity_resolver
from .identity import IdentityResolver
from .profiles import capability_profile_for_track
from .schema import (
    BoundedNegativePolicy,
    CertificationState,
    DecisionPolicy,
    EdgeWitness,
    EntityPropertyFact,
    EvidenceIR,
    ExactCountPolicy,
    ExactRoutePolicy,
    ExactSetPolicy,
    MechanismValidRoutePolicy,
    NegativeReasonCode,
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

_POLICY_COVERAGE = {
    "alias_not_applicable": "micrograph.identity-alias-v2",
    "decoy_not_applicable": "micrograph.route-decoy-v2",
    "alternate_not_applicable": "micrograph.route-alternative-v2",
}


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
        return self


class TaskFixtureManifest(StrictModel):
    task_id: str
    task_fingerprint: str
    oracle_fingerprint: str
    comparator_fingerprint: str
    cases: tuple[FixtureCase, ...]
    fixture_fingerprint: str

    @model_validator(mode="after")
    def manifest_is_complete(self) -> TaskFixtureManifest:
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


def _entity_token(snapshot: GraphSnapshot, object_id: str) -> str:
    entity = snapshot.entity(object_id)
    return entity.canonical_name or next(iter(entity.aliases), entity.object_id)


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
