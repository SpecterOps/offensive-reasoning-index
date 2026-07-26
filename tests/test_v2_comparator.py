from __future__ import annotations

import inspect

import pytest

from ori.eval.v2.comparator import COMPARATOR_FINGERPRINT, compare
from ori.eval.v2.evidence import (
    EvidenceNormalizationError,
    normalize_direct_evidence,
    normalize_evidence,
    normalize_mcp_evidence,
    normalize_offline_evidence,
)
from ori.eval.v2.identity import IdentityResolver
from ori.eval.v2.schema import (
    AbsenceClaim,
    BoundedNegativePolicy,
    ClosedRouteVariantsPolicy,
    CountClaim,
    DecisionClaim,
    DecisionPolicy,
    EdgeWitness,
    EntityPropertyFact,
    EntityRef,
    EntitySelector,
    EvidenceIR,
    ExactCountPolicy,
    ExactRoutePolicy,
    ExactSetPolicy,
    MechanismValidRoutePolicy,
    NegativeReasonCode,
    NegativeWitness,
    OracleBundle,
    PathStatus,
    PopulationScope,
    RelationshipPattern,
    RelationshipSemantics,
    RouteClaim,
    RouteVariant,
    SelectionExpression,
    SetClaim,
    VerdictStatus,
)

FP = "a" * 64
RAW = "b" * 64


def _entity(
    object_id: str,
    name: str,
    *,
    object_type: str = "User",
    aliases: tuple[str, ...] = (),
) -> EntityRef:
    return EntityRef(
        object_id=object_id,
        object_type=object_type,
        domain="EXAMPLE.LOCAL",
        role=f"role-{object_id}",
        canonical_name=name,
        aliases=aliases,
    )


ALICE = _entity("USER-A", "ALICE", aliases=("alice@example.local",))
BOB = _entity("USER-B", "BOB")
CAROL = _entity("USER-C", "CAROL")
DECOY = _entity("USER-E", "DECOY")
TARGET = _entity("GROUP-D", "DOMAIN ADMINS", object_type="Group")
EXTRA = _entity("USER-X", "EXTRA")


def _edge(source: EntityRef, relationship: str, target: EntityRef) -> EdgeWitness:
    return EdgeWitness(
        source_id=source.object_id,
        relationship=relationship,
        target_id=target.object_id,
    )


CANONICAL = (
    _edge(ALICE, "GenericAll", BOB),
    _edge(BOB, "MemberOf", TARGET),
)
ALTERNATE = (
    _edge(ALICE, "GenericAll", CAROL),
    _edge(CAROL, "MemberOf", TARGET),
)
CONTEXT = _edge(CAROL, "PublishedTo", TARGET)


def _oracle(task_id: str, **updates: object) -> OracleBundle:
    if "expected_count" in updates:
        claim = CountClaim(
            kind="count",
            claim_id=task_id,
            selection=SelectionExpression(
                projection_role="item",
                projection_type="User",
            ),
            semantics=RelationshipSemantics.DIRECT,
            population_scope=PopulationScope.DECLARED,
        )
    elif "expected_decision" in updates:
        claim = DecisionClaim(
            kind="decision",
            claim_id=task_id,
            subjects=(EntitySelector(role="subject"),),
            required_relationships=(
                RelationshipPattern(
                    source_role="subject",
                    relationship="PublishedTo",
                    target_role="target",
                ),
            ),
            semantics=RelationshipSemantics.DIRECT,
            population_scope=PopulationScope.DECLARED,
        )
    elif "negative_witnesses" in updates:
        claim = AbsenceClaim(
            kind="absence",
            claim_id=task_id,
            source=EntitySelector(role="source"),
            target=EntitySelector(role="target"),
            relationships=("MemberOf",),
            reason_codes=(NegativeReasonCode.TEMPLATE_AUTHENTICATION_DISABLED,),
            max_hops=4,
            semantics=RelationshipSemantics.DIRECT,
            population_scope=PopulationScope.DECLARED,
        )
    elif "route_variants" in updates:
        claim = RouteClaim(
            kind="route",
            claim_id=task_id,
            source=EntitySelector(role="source"),
            target=EntitySelector(role="target"),
            semantics=RelationshipSemantics.DIRECT,
            population_scope=PopulationScope.DECLARED,
            max_hops=8,
        )
    else:
        claim = SetClaim(
            kind="set",
            claim_id=task_id,
            selection=SelectionExpression(
                projection_role="item",
                projection_type="User",
            ),
            semantics=RelationshipSemantics.DIRECT,
            population_scope=PopulationScope.DECLARED,
        )
    values: dict[str, object] = {
        "oracle_id": f"oracle-{task_id}",
        "task_id": task_id,
        "claim": claim,
        "claim_fingerprint": FP,
        "task_fingerprint": FP,
        "graph_fingerprint": FP,
        "oracle_fingerprint": FP,
    }
    values.update(updates)
    return OracleBundle(**values)


def _evidence(task_id: str, **updates: object) -> EvidenceIR:
    values: dict[str, object] = {"task_id": task_id, "raw_digest": RAW}
    values.update(updates)
    return EvidenceIR(**values)


def test_exact_set_accepts_aliases_and_rejects_wrong_empty_and_extra_entities() -> None:
    resolver = IdentityResolver((ALICE, BOB, CAROL))
    oracle = _oracle("set", expected_entities=(ALICE, BOB))
    policy = ExactSetPolicy(kind="exact_set")

    perfect = normalize_evidence(
        {"task_id": "set", "entities": ["alice@example.local", "bob"]},
        resolver=resolver,
    )
    assert compare(policy, oracle, perfect).status is VerdictStatus.CORRECT

    wrong = normalize_evidence(
        {"task_id": "set", "entities": ["alice", "carol"]},
        resolver=resolver,
    )
    wrong_verdict = compare(policy, oracle, wrong)
    assert wrong_verdict.status is VerdictStatus.INCORRECT
    assert wrong_verdict.diagnostics.missing_entity_ids == (BOB.object_id,)
    assert wrong_verdict.diagnostics.extra_entity_ids == (CAROL.object_id,)

    empty = normalize_evidence(
        {"task_id": "set", "entities": []},
        resolver=resolver,
    )
    assert compare(policy, oracle, empty).status is VerdictStatus.INCORRECT

    extra = normalize_evidence(
        {"task_id": "set", "entities": ["alice", "bob", "carol"]},
        resolver=resolver,
    )
    assert compare(policy, oracle, extra).status is VerdictStatus.INCORRECT


def test_evidence_normalization_is_shared_and_rejects_oracle_injection() -> None:
    resolver = IdentityResolver((ALICE, BOB))
    payload = {
        "task_id": "shared",
        "entities": ["alice"],
        "edges": [
            {
                "source": "alice",
                "relationship": "WriteDACL",
                "target": "bob",
                "properties": {"inherited": False},
            }
        ],
    }

    direct = normalize_direct_evidence(payload, resolver=resolver)
    mcp = normalize_mcp_evidence(payload, resolver=resolver)
    offline = normalize_offline_evidence(payload, resolver=resolver)

    assert direct == mcp == offline
    assert direct.edges[0].relationship == "WriteDacl"
    assert direct.raw_digest == mcp.raw_digest == offline.raw_digest

    with pytest.raises(EvidenceNormalizationError, match="forbidden oracle field"):
        normalize_evidence(
            {
                "task_id": "shared",
                "entities": ["alice"],
                "reference_results": ["USER-A"],
            },
            resolver=resolver,
        )


def test_exact_count_and_comparator_fingerprint() -> None:
    policy = ExactCountPolicy(kind="exact_count")
    oracle = _oracle("count", expected_count=2)

    correct = compare(policy, oracle, _evidence("count", count=2))
    wrong = compare(policy, oracle, _evidence("count", count=3))
    empty = compare(policy, oracle, _evidence("count"))

    assert correct.status is VerdictStatus.CORRECT
    assert correct.comparator_fingerprint == COMPARATOR_FINGERPRINT
    assert wrong.status is VerdictStatus.INCORRECT
    assert empty.status is VerdictStatus.INCORRECT


def test_mechanism_valid_route_accepts_sealed_alternate_route() -> None:
    policy = MechanismValidRoutePolicy(kind="mechanism_valid_route")
    oracle = _oracle(
        "mechanism",
        route_variants=(RouteVariant(variant_id="canonical", edges=CANONICAL),),
        graph_edge_registry=(*CANONICAL, *ALTERNATE, CONTEXT),
        required_mechanisms=("GenericAll", "MemberOf"),
        required_context=(CONTEXT,),
        source_id=ALICE.object_id,
        target_id=TARGET.object_id,
    )
    evidence = _evidence(
        "mechanism",
        edges=ALTERNATE,
        supporting_edges=(CONTEXT,),
        path_status=PathStatus.FOUND,
    )

    verdict = compare(policy, oracle, evidence)

    assert verdict.status is VerdictStatus.CORRECT
    assert verdict.reason == "ROUTE_VALID"
    assert verdict.diagnostics.route_overlap < 1.0


@pytest.mark.parametrize(
    ("edges", "forbidden_ids"),
    [
        (
            (
                _edge(ALICE, "GenericWrite", CAROL),
                _edge(CAROL, "MemberOf", TARGET),
            ),
            (),
        ),
        (
            (
                _edge(BOB, "GenericAll", ALICE),
                _edge(BOB, "MemberOf", TARGET),
            ),
            (),
        ),
        ((*CANONICAL, _edge(EXTRA, "MemberOf", TARGET)), ()),
        (
            (
                _edge(ALICE, "GenericAll", BOB),
                _edge(BOB, "MemberOf", ALICE),
                _edge(ALICE, "MemberOf", TARGET),
            ),
            (),
        ),
        (
            (
                _edge(ALICE, "GenericAll", DECOY),
                _edge(DECOY, "MemberOf", TARGET),
            ),
            (DECOY.object_id,),
        ),
        (
            (
                _edge(EXTRA, "GenericAll", BOB),
                _edge(BOB, "MemberOf", TARGET),
            ),
            (),
        ),
    ],
    ids=[
        "wrong-mechanism",
        "reversed-edge",
        "disconnected-extra",
        "cycle",
        "forbidden-decoy",
        "wrong-source",
    ],
)
def test_mechanism_route_rejects_adversarial_witnesses(
    edges: tuple[EdgeWitness, ...],
    forbidden_ids: tuple[str, ...],
) -> None:
    policy = MechanismValidRoutePolicy(
        kind="mechanism_valid_route",
        forbid_extra_edges=False,
    )
    oracle = _oracle(
        "adversarial",
        route_variants=(RouteVariant(variant_id="canonical", edges=CANONICAL),),
        graph_edge_registry=(*CANONICAL, *edges),
        required_mechanisms=("GenericAll", "MemberOf"),
        source_id=ALICE.object_id,
        target_id=TARGET.object_id,
        forbidden_entity_ids=forbidden_ids,
    )

    verdict = compare(
        policy,
        oracle,
        _evidence("adversarial", edges=edges, path_status=PathStatus.FOUND),
    )

    assert verdict.status is VerdictStatus.INCORRECT


def test_mechanism_route_rejects_unsealed_alternate_edge() -> None:
    oracle = _oracle(
        "unsealed",
        route_variants=(RouteVariant(variant_id="canonical", edges=CANONICAL),),
        graph_edge_registry=CANONICAL,
        required_mechanisms=("GenericAll", "MemberOf"),
        source_id=ALICE.object_id,
        target_id=TARGET.object_id,
    )

    verdict = compare(
        MechanismValidRoutePolicy(kind="mechanism_valid_route"),
        oracle,
        _evidence("unsealed", edges=ALTERNATE, path_status=PathStatus.FOUND),
    )

    assert verdict.status is VerdictStatus.INCORRECT
    assert verdict.reason == "ROUTE_UNSEALED_GRAPH_EDGE"


def test_exact_and_closed_route_policies_use_sealed_variants() -> None:
    oracle = _oracle(
        "variants",
        route_variants=(
            RouteVariant(variant_id="canonical", edges=CANONICAL),
            RouteVariant(variant_id="alternate", edges=ALTERNATE),
        ),
        source_id=ALICE.object_id,
        target_id=TARGET.object_id,
    )
    alternate = _evidence("variants", edges=ALTERNATE, path_status=PathStatus.FOUND)

    assert (
        compare(ExactRoutePolicy(kind="exact_route"), oracle, alternate).status
        is VerdictStatus.INCORRECT
    )
    assert (
        compare(
            ClosedRouteVariantsPolicy(kind="closed_route_variants"),
            oracle,
            alternate,
        ).status
        is VerdictStatus.CORRECT
    )


def test_exact_route_rejects_contradictory_status_and_extra_connected_context() -> None:
    oracle = _oracle(
        "exact-route",
        route_variants=(RouteVariant(variant_id="canonical", edges=CANONICAL),),
        required_context=(CONTEXT,),
        source_id=ALICE.object_id,
        target_id=TARGET.object_id,
    )
    policy = ExactRoutePolicy(kind="exact_route")
    perfect = _evidence(
        "exact-route",
        edges=CANONICAL,
        supporting_edges=(CONTEXT,),
        path_status=PathStatus.FOUND,
    )

    assert compare(policy, oracle, perfect).status is VerdictStatus.CORRECT
    assert (
        compare(
            policy,
            oracle,
            perfect.model_copy(update={"path_status": PathStatus.NO_PATH}),
        ).status
        is VerdictStatus.INCORRECT
    )

    connected_extra = _edge(BOB, "MemberOf", EXTRA)
    extra = perfect.model_copy(
        update={"supporting_edges": (CONTEXT, connected_extra)}
    )
    verdict = compare(policy, oracle, extra)
    assert verdict.status is VerdictStatus.INCORRECT
    assert verdict.reason == "ROUTE_EXTRA_SUPPORTING_EDGE"


def test_decision_requires_sealed_entities_edges_and_properties() -> None:
    property_fact = EntityPropertyFact(
        entity_id=TARGET.object_id,
        key="enabled",
        value=True,
    )
    oracle = _oracle(
        "decision",
        expected_decision=True,
        expected_entities=(TARGET,),
        graph_edge_registry=(CONTEXT,),
        required_context=(CONTEXT,),
        required_properties=(property_fact,),
    )
    policy = DecisionPolicy(kind="decision")

    bare = compare(policy, oracle, _evidence("decision", decision=True))
    assert bare.status is VerdictStatus.INCORRECT

    supported = compare(
        policy,
        oracle,
        _evidence(
            "decision",
            decision=True,
            entities=(TARGET,),
            supporting_edges=(CONTEXT,),
            observed_properties=(property_fact,),
        ),
    )
    assert supported.status is VerdictStatus.CORRECT


def test_bounded_negative_requires_exact_reason_property_and_entity_coverage() -> None:
    property_fact = EntityPropertyFact(
        entity_id=TARGET.object_id,
        key="authentication_enabled",
        value=False,
    )
    witness = NegativeWitness(
        reason_code=NegativeReasonCode.TEMPLATE_AUTHENTICATION_DISABLED,
        checked_entity_ids=(ALICE.object_id, TARGET.object_id),
        checked_edges=(CONTEXT,),
        checked_properties=(property_fact,),
        max_hops=4,
        witness_absent=True,
    )
    oracle = _oracle("negative", negative_witnesses=(witness,))
    policy = BoundedNegativePolicy(kind="bounded_negative")
    perfect = _evidence(
        "negative",
        entities=(ALICE, TARGET),
        path_status=PathStatus.NO_PATH,
        supporting_edges=(CONTEXT,),
        observed_properties=(property_fact,),
        negative_reason_codes=(
            NegativeReasonCode.TEMPLATE_AUTHENTICATION_DISABLED,
        ),
    )

    assert compare(policy, oracle, perfect).status is VerdictStatus.CORRECT

    missing_checked_edge = perfect.model_copy(update={"supporting_edges": ()})
    assert (
        compare(policy, oracle, missing_checked_edge).status
        is VerdictStatus.INCORRECT
    )

    missing_property = perfect.model_copy(update={"observed_properties": ()})
    assert (
        compare(policy, oracle, missing_property).status
        is VerdictStatus.INCORRECT
    )

    extra_reason = perfect.model_copy(
        update={
            "negative_reason_codes": (
                NegativeReasonCode.TEMPLATE_AUTHENTICATION_DISABLED,
                NegativeReasonCode.OBJECTIVE_UNREACHABLE,
            )
        }
    )
    assert compare(policy, oracle, extra_reason).status is VerdictStatus.INCORRECT

    witness_present = witness.model_copy(update={"witness_absent": False})
    flipped_oracle = oracle.model_copy(
        update={"negative_witnesses": (witness_present,)}
    )
    assert compare(policy, flipped_oracle, perfect).status is VerdictStatus.INCORRECT


def test_task_mismatch_and_truncation_are_never_gradeable_as_correct() -> None:
    policy = ExactSetPolicy(kind="exact_set")
    oracle = _oracle("expected", expected_entities=(ALICE,))

    mismatch = _evidence("other", entities=(ALICE,))
    truncated = _evidence("expected", entities=(ALICE,), truncated=True)

    assert compare(policy, oracle, mismatch).reason == "TASK_ID_MISMATCH"
    assert compare(policy, oracle, truncated).reason == "TRUNCATED_EVIDENCE"


def test_comparator_api_has_no_task_template_or_metadata_parameter() -> None:
    assert tuple(inspect.signature(compare).parameters) == (
        "policy",
        "oracle",
        "evidence",
    )
