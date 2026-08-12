from __future__ import annotations

import asyncio

import pytest

from ori.eval.bhce import CypherResult
from ori.eval.direct_query_safety import (
    DirectQueryCoordinator,
    DirectQuerySafetyConfig,
    QueryDenyCache,
    query_fingerprint,
)
from ori.eval.v2.comparator import compare
from ori.eval.v2.compiler import (
    DIRECT_RESULT_CONTRACT_VERSION,
    compile_acceptance_spec,
)
from ori.eval.v2.direct_adapter import (
    DirectAdapterError,
    execute_direct_v2,
    project_direct_evidence,
)
from ori.eval.v2.fingerprint import canonical_sha256
from ori.eval.v2.identity import IdentityResolver
from ori.eval.v2.profiles import (
    DIRECT_CAPABILITY_PROFILE_FINGERPRINT,
    build_direct_capability_profile,
    validate_direct_capability_profile,
)
from ori.eval.v2.runtime import sample_from_direct_outcome
from ori.eval.v2.schema import (
    DIRECT_QUERY_POLICY_VERSION,
    AbsenceClaim,
    BoundedNegativePolicy,
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
    ExecutionBounds,
    ExecutionClass,
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
    TaskBundle,
    Track,
    TrackBinding,
    VerdictStatus,
)
from ori.eval.v2.scoring import SampleOutcomeCode

FP = "a" * 64

ALICE = EntityRef(
    object_id="USER-A",
    object_type="User",
    domain="EXAMPLE.LOCAL",
    role="benchmark_object",
    canonical_name="ALICE@EXAMPLE.LOCAL",
    aliases=("ALICE",),
)
TARGET = EntityRef(
    object_id="GROUP-B",
    object_type="Group",
    domain="EXAMPLE.LOCAL",
    role="benchmark_object",
    canonical_name="DOMAIN ADMINS@EXAMPLE.LOCAL",
    aliases=("DOMAIN ADMINS",),
)
BOB = EntityRef(
    object_id="USER-C",
    object_type="User",
    domain="EXAMPLE.LOCAL",
    role="benchmark_object",
    canonical_name="BOB@EXAMPLE.LOCAL",
    aliases=("BOB",),
)
EDGE = EdgeWitness(
    source_id=ALICE.object_id,
    relationship="MemberOf",
    target_id=TARGET.object_id,
)
CLAIM = RouteClaim(
    kind="route",
    claim_id="claim:route",
    source=EntitySelector(role="source", object_type="User"),
    target=EntitySelector(role="target", object_type="Group"),
    semantics=RelationshipSemantics.DIRECT,
    population_scope=PopulationScope.BENCHMARK_NAMESPACE,
    required_mechanisms=("MemberOf",),
    max_hops=1,
)
POLICY = ExactRoutePolicy(kind="exact_route")
BINDING = TrackBinding(
    track=Track.DIRECT,
    capability_profile_id="direct-v3",
    semantics=RelationshipSemantics.DIRECT,
    bounds=ExecutionBounds(
        max_hops=1,
        max_result_cardinality=1,
        page_size=1,
        max_pages=1,
        require_total_count=False,
        require_stable_ordering=True,
        max_output_bytes=1024,
        max_transcript_bytes=4096,
        max_tool_calls=0,
        timeout_seconds=10.0,
    ),
    direct_query_policy_version=DIRECT_QUERY_POLICY_VERSION,
)
TASK = TaskBundle(
    task_id="simple.direct.route@2",
    revision=2,
    product="simple",
    claim_kind="route",
    answer_policy=POLICY,
    acceptance_spec=compile_acceptance_spec(CLAIM, POLICY, BINDING),
    binding=BINDING,
    question="Find the edge route.",
    answer_schema={"type": "object"},
    claim_fingerprint=FP,
    prompt_fingerprint=FP,
    task_fingerprint=FP,
)
ORACLE = OracleBundle(
    oracle_id="oracle:route",
    task_id=TASK.task_id,
    claim=CLAIM,
    claim_fingerprint=FP,
    task_fingerprint=FP,
    graph_fingerprint=FP,
    resolved_roles=(
        ALICE.model_copy(update={"role": "source"}),
        TARGET.model_copy(update={"role": "target"}),
    ),
    route_variants=(RouteVariant(variant_id="canonical", edges=(EDGE,)),),
    graph_edge_registry=(EDGE,),
    graph_fact_registry_fingerprint=FP,
    required_mechanisms=("MemberOf",),
    source_id=ALICE.object_id,
    target_id=TARGET.object_id,
    oracle_fingerprint=FP,
)
RESOLVER = IdentityResolver((ALICE, TARGET))
COUNT_CLAIM = CountClaim(
    kind="count",
    claim_id="claim:count",
    selection=SelectionExpression(
        anchors=(EntitySelector(role="subject", object_type="User"),),
        projection_role="subject",
        projection_type="User",
    ),
    semantics=RelationshipSemantics.DIRECT,
    population_scope=PopulationScope.BENCHMARK_NAMESPACE,
)
COUNT_TASK = TASK.model_copy(
    update={
        "task_id": "simple.direct.count@2",
        "claim_kind": "count",
        "answer_policy": (count_policy := ExactCountPolicy(kind="exact_count")),
        "acceptance_spec": compile_acceptance_spec(
            COUNT_CLAIM,
            count_policy,
            TASK.binding,
        ),
        "question": "Count the matching users.",
    }
)
COUNT_ORACLE = ORACLE.model_copy(
    update={
        "oracle_id": "oracle:count",
        "task_id": COUNT_TASK.task_id,
        "claim": COUNT_CLAIM,
        "expected_count": 7,
        "route_variants": (),
        "required_mechanisms": (),
        "source_id": None,
        "target_id": None,
    }
)
SET_CLAIM = SetClaim(
    kind="set",
    claim_id="claim:set",
    selection=SelectionExpression(
        anchors=(EntitySelector(role="subject", object_type="Base"),),
        projection_role="subject",
        projection_type="Base",
    ),
    semantics=RelationshipSemantics.DIRECT,
    population_scope=PopulationScope.BENCHMARK_NAMESPACE,
)
SET_TASK = TASK.model_copy(
    update={
        "task_id": "simple.direct.set@2",
        "claim_kind": "set",
        "answer_policy": (set_policy := ExactSetPolicy(kind="exact_set")),
        "binding": (
            set_binding := TASK.binding.model_copy(
                update={
                    "bounds": TASK.binding.bounds.model_copy(
                        update={"max_result_cardinality": 2, "page_size": 2}
                    )
                }
            )
        ),
        "acceptance_spec": compile_acceptance_spec(
            SET_CLAIM,
            set_policy,
            set_binding,
        ),
        "question": "Return the complete matching set.",
    }
)
SET_ORACLE = ORACLE.model_copy(
    update={
        "oracle_id": "oracle:set",
        "task_id": SET_TASK.task_id,
        "claim": SET_CLAIM,
        "expected_entities": (ALICE, TARGET),
        "route_variants": (),
        "required_mechanisms": (),
        "source_id": None,
        "target_id": None,
    }
)


def _raw_route() -> dict:
    return {
        "data": {
            "nodes": {
                "0": {
                    "objectId": ALICE.object_id,
                    "label": ALICE.canonical_name,
                    "kind": "User",
                    "properties": {"name": ALICE.canonical_name},
                },
                "1": {
                    "objectId": TARGET.object_id,
                    "label": TARGET.canonical_name,
                    "kind": "Group",
                    "properties": {"name": TARGET.canonical_name},
                },
            },
            "edges": [
                {
                    "source": "0",
                    "target": "1",
                    "kind": "MemberOf",
                }
            ],
        }
    }


class FakeCoordinator:
    def __init__(self, result: CypherResult, *, enabled: bool = True) -> None:
        self.result = result
        self.config = DirectQuerySafetyConfig(enabled=enabled)
        self.queries: list[str] = []

    async def execute(self, query: str) -> CypherResult:
        self.queries.append(query)
        return self.result


def test_direct_adapter_executes_once_and_scores_returned_edge_evidence() -> None:
    result = CypherResult(
        success=True,
        raw=_raw_route(),
        status_code=200,
        query_executed=True,
        execution_attempts=1,
        query_fingerprint="b" * 64,
        safety_policy_version=DIRECT_QUERY_POLICY_VERSION,
        safety_rule="allowed",
        bhce_health_after="not_checked",
        circuit_state="closed",
    )
    coordinator = FakeCoordinator(result)
    query = "MATCH p=(a)-[:MemberOf]->(b) RETURN p LIMIT 1"

    outcome = asyncio.run(
        execute_direct_v2(
            coordinator,
            query=query,
            task=TASK,
            oracle=ORACLE,
            resolver=RESOLVER,
        )
    )

    assert coordinator.queries == [query]
    assert outcome.receipt.execution_class is ExecutionClass.SUCCESS
    assert outcome.receipt.query_fingerprint == "b" * 64
    assert outcome.evidence is not None
    assert outcome.evidence.edges == (EDGE,)
    assert outcome.verdict is not None
    assert outcome.verdict.status is VerdictStatus.CORRECT


def test_direct_route_projects_required_property_predicate_by_property_name() -> None:
    claim = CLAIM.model_copy(
        update={
            "required_properties": (
                PropertyPredicate(
                    role="source",
                    property_name="hasspn",
                    operator=PredicateOperator.EQUALS,
                    value=True,
                ),
            )
        }
    )
    oracle = ORACLE.model_copy(
        update={
            "claim": claim,
            "required_properties": (
                EntityPropertyFact(
                    entity_id=ALICE.object_id,
                    key="hasspn",
                    value=True,
                ),
            ),
        }
    )
    raw = _raw_route()
    raw["data"]["nodes"]["0"]["properties"]["hasSPN"] = True

    evidence = project_direct_evidence(
        CypherResult(success=True, raw=raw),
        task=TASK,
        oracle=oracle,
        resolver=RESOLVER,
    )

    assert evidence.observed_properties == oracle.required_properties


def test_direct_property_boundary_rejects_unresolved_claim_predicate() -> None:
    predicate = PropertyPredicate(
        role="source",
        property_name="hasspn",
        operator=PredicateOperator.EQUALS,
        value=True,
    )
    claim = CLAIM.model_copy(update={"required_properties": (predicate,)})
    # model_copy intentionally bypasses validation here to simulate a stale or
    # incorrectly paired private artifact reaching the runtime boundary.
    malformed_oracle = ORACLE.model_copy(
        update={
            "claim": claim,
            "required_properties": claim.required_properties,
        }
    )

    with pytest.raises(
        DirectAdapterError,
        match="requires resolved EntityPropertyFact",
    ):
        project_direct_evidence(
            CypherResult(success=True, raw=_raw_route()),
            task=TASK,
            oracle=malformed_oracle,
            resolver=RESOLVER,
        )


def test_direct_property_projection_is_scoped_to_the_resolved_entity() -> None:
    extra = EntityRef(
        object_id="USER-EXTRA",
        object_type="User",
        domain="EXAMPLE.LOCAL",
        role="benchmark_object",
        canonical_name="EXTRA@EXAMPLE.LOCAL",
    )
    property_fact = EntityPropertyFact(
        entity_id=ALICE.object_id,
        key="hasspn",
        value=True,
    )
    oracle = ORACLE.model_copy(update={"required_properties": (property_fact,)})
    raw = _raw_route()
    raw["data"]["nodes"]["0"]["properties"]["hasSPN"] = True
    raw["data"]["nodes"]["2"] = {
        "objectId": extra.object_id,
        "label": extra.canonical_name,
        "kind": extra.object_type,
        "properties": {
            "name": extra.canonical_name,
            "hasSPN": False,
        },
    }

    evidence = project_direct_evidence(
        CypherResult(success=True, raw=raw),
        task=TASK,
        oracle=oracle,
        resolver=IdentityResolver((ALICE, TARGET, extra)),
    )

    assert evidence.observed_properties == (property_fact,)
    assert extra.object_id in {entity.object_id for entity in evidence.entities}


def test_direct_decision_rejects_nodes_outside_the_sealed_context() -> None:
    extra = EntityRef(
        object_id="USER-EXTRA",
        object_type="User",
        domain="EXAMPLE.LOCAL",
        role="benchmark_object",
        canonical_name="EXTRA@EXAMPLE.LOCAL",
    )
    decision_claim = DecisionClaim(
        kind="decision",
        claim_id="claim:decision",
        subjects=(
            EntitySelector(role="source", object_type="User"),
            EntitySelector(role="target", object_type="Group"),
        ),
        required_relationships=(
            RelationshipPattern(
                source_role="source",
                relationship="MemberOf",
                target_role="target",
            ),
        ),
        semantics=RelationshipSemantics.DIRECT,
        population_scope=PopulationScope.BENCHMARK_NAMESPACE,
    )
    task = TASK.model_copy(
        update={
            "task_id": "simple.direct.decision@2",
            "claim_kind": "decision",
            "answer_policy": (decision_policy := DecisionPolicy(kind="decision")),
            "acceptance_spec": compile_acceptance_spec(
                decision_claim,
                decision_policy,
                TASK.binding,
            ),
        }
    )
    oracle = ORACLE.model_copy(
        update={
            "task_id": task.task_id,
            "claim": decision_claim,
            "expected_entities": (ALICE, TARGET),
            "expected_decision": True,
            "required_context": (EDGE,),
            "route_variants": (),
        }
    )
    raw = _raw_route()
    raw["data"]["nodes"]["2"] = {
        "objectId": extra.object_id,
        "label": extra.canonical_name,
        "kind": extra.object_type,
        "properties": {"name": extra.canonical_name},
    }

    with pytest.raises(
        DirectAdapterError,
        match="outside the sealed decision/proof context",
    ):
        project_direct_evidence(
            CypherResult(success=True, raw=raw),
            task=task,
            oracle=oracle,
            resolver=IdentityResolver((ALICE, TARGET, extra)),
            answer_payload={"decision": True},
        )


def test_direct_absence_requires_exact_zero_count_proof() -> None:
    claim = AbsenceClaim(
        kind="absence",
        claim_id="claim:absence",
        source=EntitySelector(role="source", object_type="User"),
        target=EntitySelector(role="target", object_type="Group"),
        relationships=("MemberOf",),
        reason_codes=(NegativeReasonCode.OBJECTIVE_UNREACHABLE,),
        max_hops=1,
        semantics=RelationshipSemantics.DIRECT,
        population_scope=PopulationScope.BENCHMARK_NAMESPACE,
    )
    task = TASK.model_copy(
        update={
            "task_id": "simple.direct.absence@2",
            "claim_kind": "absence",
            "answer_policy": (absence_policy := BoundedNegativePolicy(kind="bounded_negative")),
            "acceptance_spec": compile_acceptance_spec(
                claim,
                absence_policy,
                TASK.binding,
            ),
        }
    )
    oracle = ORACLE.model_copy(
        update={
            "task_id": task.task_id,
            "claim": claim,
            "route_variants": (),
            "required_context": (),
            "required_properties": (),
            "negative_witnesses": (
                NegativeWitness(
                    reason_code=NegativeReasonCode.OBJECTIVE_UNREACHABLE,
                    checked_entity_ids=(ALICE.object_id, TARGET.object_id),
                    max_hops=1,
                    witness_absent=True,
                ),
            ),
        }
    )

    evidence = project_direct_evidence(
        CypherResult(
            success=True,
            raw={
                "data": {
                    "nodes": {},
                    "edges": [],
                    "literals": [{"key": "count", "value": 0}],
                }
            },
        ),
        task=task,
        oracle=oracle,
        resolver=RESOLVER,
        answer_payload={
            "path_status": "no_path",
            "negative_reason_codes": ["objective_unreachable"],
        },
    )

    assert evidence.edges == ()
    assert evidence.supporting_edges == ()
    assert evidence.observed_properties == ()
    assert evidence.path_status.value == "no_path"

    contradictory = project_direct_evidence(
        CypherResult(
            success=True,
            raw={
                "data": {
                    "nodes": {},
                    "edges": [],
                    "literals": [{"key": "count", "value": 1}],
                }
            },
        ),
        task=task,
        oracle=oracle,
        resolver=RESOLVER,
        answer_payload={
            "path_status": "no_path",
            "negative_reason_codes": ["objective_unreachable"],
        },
    )
    verdict = compare(task.answer_policy, oracle, contradictory)

    assert contradictory.path_status.value == "found"
    assert verdict.status is VerdictStatus.INCORRECT
    assert verdict.reason == "BOUNDED_NEGATIVE_INVALID"


def test_direct_absence_rejects_unrelated_zero_count_before_execution() -> None:
    claim = AbsenceClaim(
        kind="absence",
        claim_id="claim:absence",
        source=EntitySelector(role="source", object_type="User"),
        target=EntitySelector(role="target", object_type="Group"),
        relationships=("MemberOf",),
        reason_codes=(NegativeReasonCode.OBJECTIVE_UNREACHABLE,),
        max_hops=1,
        semantics=RelationshipSemantics.DIRECT,
        population_scope=PopulationScope.BENCHMARK_NAMESPACE,
    )
    policy = BoundedNegativePolicy(kind="bounded_negative")
    task = TASK.model_copy(
        update={
            "task_id": "simple.direct.absence-scope@2",
            "claim_kind": "absence",
            "answer_policy": policy,
            "acceptance_spec": compile_acceptance_spec(claim, policy, TASK.binding),
            "input_entities": (
                ALICE.model_copy(update={"role": "source"}),
                TARGET.model_copy(update={"role": "target"}),
            ),
        }
    )
    coordinator = FakeCoordinator(
        CypherResult(
            success=True,
            raw={
                "data": {
                    "nodes": {},
                    "edges": [],
                    "literals": [{"key": "count", "value": 0}],
                }
            },
        )
    )

    outcome = asyncio.run(
        execute_direct_v2(
            coordinator,
            query="MATCH (n:Computer) RETURN count(n) AS count",
            task=task,
            oracle=ORACLE,
            resolver=RESOLVER,
            answer_payload={
                "path_status": "no_path",
                "negative_reason_codes": ["objective_unreachable"],
            },
        )
    )

    assert coordinator.queries == []
    assert outcome.receipt.execution_class is ExecutionClass.MODEL_FAILURE
    assert outcome.receipt.failure_subtype == "negative_scope_invalid"
    assert outcome.receipt.query_executed is False
    assert outcome.evidence is None
    assert outcome.verdict is None


def test_direct_broader_nonzero_absence_is_proof_insufficient() -> None:
    claim = AbsenceClaim(
        kind="absence",
        claim_id="claim:absence-broader",
        source=EntitySelector(role="source", object_type="User"),
        target=EntitySelector(role="target", object_type="Group"),
        relationships=("MemberOf",),
        reason_codes=(NegativeReasonCode.OBJECTIVE_UNREACHABLE,),
        max_hops=1,
        semantics=RelationshipSemantics.DIRECT,
        population_scope=PopulationScope.BENCHMARK_NAMESPACE,
    )
    policy = BoundedNegativePolicy(kind="bounded_negative")
    binding = TASK.binding.model_copy(
        update={
            "bounds": TASK.binding.bounds.model_copy(update={"max_hops": 1}),
        }
    )
    task = TASK.model_copy(
        update={
            "task_id": "simple.direct.absence-broader@2",
            "claim_kind": "absence",
            "answer_policy": policy,
            "acceptance_spec": compile_acceptance_spec(
                claim,
                policy,
                binding,
            ),
            "binding": binding,
            "input_entities": (
                ALICE.model_copy(update={"role": "source"}),
                TARGET.model_copy(update={"role": "target"}),
            ),
        }
    )
    oracle = ORACLE.model_copy(
        update={
            "task_id": task.task_id,
            "claim": claim,
            "route_variants": (),
            "required_context": (),
            "required_properties": (),
            "negative_witnesses": (
                NegativeWitness(
                    reason_code=NegativeReasonCode.OBJECTIVE_UNREACHABLE,
                    checked_entity_ids=(ALICE.object_id, TARGET.object_id),
                    max_hops=1,
                    witness_absent=True,
                ),
            ),
        }
    )
    coordinator = FakeCoordinator(
        CypherResult(
            success=True,
            raw={
                "data": {
                    "nodes": {},
                    "edges": [],
                    "literals": [{"key": "count", "value": 1}],
                }
            },
        )
    )

    outcome = asyncio.run(
        execute_direct_v2(
            coordinator,
            query=(
                "MATCH p=(s {objectid:'USER-A'})-[:MemberOf*1..12]->"
                "(t {objectid:'GROUP-B'}) RETURN count(p) AS count"
            ),
            task=task,
            oracle=oracle,
            resolver=RESOLVER,
            answer_payload={
                "path_status": "no_path",
                "negative_reason_codes": ["objective_unreachable"],
            },
        )
    )
    sample = sample_from_direct_outcome(task, oracle, outcome)

    assert len(coordinator.queries) == 1
    assert outcome.proof_insufficient is True
    assert outcome.evidence is None
    assert outcome.verdict is None
    assert sample.execution_class is ExecutionClass.PROOF_FAILURE
    assert sample.outcome is SampleOutcomeCode.PROOF_INSUFFICIENT
    assert sample.reasoning_correct is None


def test_direct_adapter_contains_unexpected_projection_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = CypherResult(
        success=True,
        raw=_raw_route(),
        status_code=200,
        query_executed=True,
        execution_attempts=1,
        query_fingerprint="b" * 64,
        safety_policy_version=DIRECT_QUERY_POLICY_VERSION,
        safety_rule="allowed",
        bhce_health_after="not_checked",
        circuit_state="closed",
    )
    coordinator = FakeCoordinator(result)

    def raise_internal_error(*args: object, **kwargs: object) -> EvidenceIR:
        raise AttributeError("internal projector defect")

    monkeypatch.setattr(
        "ori.eval.v2.direct_adapter.project_direct_evidence",
        raise_internal_error,
    )

    outcome = asyncio.run(
        execute_direct_v2(
            coordinator,
            query="MATCH p=(a)-[:MemberOf]->(b) RETURN p LIMIT 1",
            task=TASK,
            oracle=ORACLE,
            resolver=RESOLVER,
        )
    )

    assert outcome.harness_error is True
    assert outcome.evidence is None
    assert outcome.verdict is None
    assert outcome.error == "AttributeError: internal projector defect"


def test_direct_live_identity_missing_from_catalog_is_harness_failure() -> None:
    raw = _raw_route()
    raw["data"]["nodes"]["1"].update(
        {
            "objectId": "CE-LOCAL-GROUP-544",
            "label": "CE-LOCAL-GROUP-544",
            "kind": "ADLocalGroup",
            "properties": {"name": "CE-LOCAL-GROUP-544"},
        }
    )
    result = CypherResult(
        success=True,
        raw=raw,
        status_code=200,
        query_executed=True,
        execution_attempts=1,
        query_fingerprint="b" * 64,
        safety_policy_version=DIRECT_QUERY_POLICY_VERSION,
        safety_rule="allowed",
        bhce_health_after="not_checked",
        circuit_state="closed",
    )

    outcome = asyncio.run(
        execute_direct_v2(
            FakeCoordinator(result),
            query="MATCH p=(a)-[:MemberOf]->(b) RETURN p LIMIT 1",
            task=TASK,
            oracle=ORACLE,
            resolver=RESOLVER,
        )
    )

    assert outcome.harness_error is True
    assert outcome.evidence is None
    assert outcome.verdict is None
    assert "IDENTITY_NOT_IN_CATALOG" in (outcome.error or "")


def test_direct_adapter_contains_unexpected_coordinator_failure() -> None:
    class RaisingCoordinator:
        config = DirectQuerySafetyConfig(enabled=True)

        async def execute(self, query: str) -> CypherResult:
            raise AttributeError("internal coordinator defect")

    outcome = asyncio.run(
        execute_direct_v2(
            RaisingCoordinator(),
            query="MATCH p=(a)-[:MemberOf]->(b) RETURN p LIMIT 1",
            task=TASK,
            oracle=ORACLE,
            resolver=RESOLVER,
        )
    )

    assert outcome.harness_error is True
    assert outcome.receipt.execution_class is ExecutionClass.HARNESS_FAILURE
    assert outcome.receipt.query_executed is None
    assert outcome.receipt.attempts is None
    assert outcome.receipt.query_fingerprint == query_fingerprint(
        "MATCH p=(a)-[:MemberOf]->(b) RETURN p LIMIT 1"
    )
    assert outcome.receipt.failure_type == "harness_error"
    assert outcome.error == "AttributeError: internal coordinator defect"


def test_direct_adapter_preserves_receipt_provenance_when_projection_fails() -> None:
    class MalformedResult:
        query_executed = True
        execution_attempts = 2
        query_fingerprint = "c" * 64

    class MalformedCoordinator:
        config = DirectQuerySafetyConfig(enabled=True)

        async def execute(self, query: str) -> MalformedResult:
            return MalformedResult()

    outcome = asyncio.run(
        execute_direct_v2(
            MalformedCoordinator(),
            query="MATCH p=(a)-[:MemberOf]->(b) RETURN p LIMIT 1",
            task=TASK,
            oracle=ORACLE,
            resolver=RESOLVER,
        )
    )

    assert outcome.harness_error is True
    assert outcome.receipt.execution_class is ExecutionClass.HARNESS_FAILURE
    assert outcome.receipt.query_executed is True
    assert outcome.receipt.attempts == 2
    assert outcome.receipt.query_fingerprint == "c" * 64
    assert outcome.receipt.policy_rule == "receipt_construction_failed"


def test_direct_capability_profile_binds_result_contract() -> None:
    profile = build_direct_capability_profile()

    assert (
        profile.direct_result_contract_version
        == DIRECT_RESULT_CONTRACT_VERSION
        == "ori-direct-result-contract-v13"
    )
    assert profile.profile_fingerprint == DIRECT_CAPABILITY_PROFILE_FINGERPRINT
    assert profile.profile_fingerprint == canonical_sha256(
        profile,
        exclude_fields=("profile_fingerprint",),
    )
    assert validate_direct_capability_profile(profile) is profile


def test_direct_path_rejects_nodes_without_ordered_edges() -> None:
    result = CypherResult(
        success=True,
        raw={"data": {"nodes": _raw_route()["data"]["nodes"], "edges": []}},
    )

    with pytest.raises(DirectAdapterError, match="no ordered edge witness"):
        project_direct_evidence(
            result,
            task=TASK,
            oracle=ORACLE,
            resolver=RESOLVER,
        )


def test_direct_count_projects_scalar_instead_of_returned_row_count() -> None:
    result = CypherResult(
        success=True,
        nodes=[{"count": 7}],
        raw={"data": {"count": 7}},
    )

    evidence = project_direct_evidence(
        result,
        task=COUNT_TASK,
        oracle=COUNT_ORACLE,
        resolver=RESOLVER,
    )

    assert evidence.count == 7


def test_direct_count_projects_bloodhound_literal_shape() -> None:
    result = CypherResult(
        success=True,
        raw={
            "data": {
                "edges": [],
                "nodes": {},
                "literals": [{"value": 7, "key": "count"}],
            }
        },
    )

    evidence = project_direct_evidence(
        result,
        task=COUNT_TASK,
        oracle=COUNT_ORACLE,
        resolver=RESOLVER,
    )

    assert evidence.count == 7


def test_direct_set_preserves_v1_graph_node_projection() -> None:
    result = CypherResult(
        success=True,
        raw={"data": {**_raw_route()["data"], "edges": [], "literals": []}},
    )

    evidence = project_direct_evidence(
        result,
        task=SET_TASK,
        oracle=SET_ORACLE,
        resolver=RESOLVER,
    )

    assert {entity.object_id for entity in evidence.entities} == {
        ALICE.object_id,
        TARGET.object_id,
    }


def test_direct_set_extra_identity_within_public_capacity_reaches_exact_comparator() -> None:
    task = SET_TASK.model_copy(
        update={
            "binding": (
                binding := SET_TASK.binding.model_copy(
                    update={
                        "bounds": SET_TASK.binding.bounds.model_copy(
                            update={"max_result_cardinality": 3, "page_size": 3}
                        )
                    }
                )
            ),
            "acceptance_spec": compile_acceptance_spec(
                SET_CLAIM,
                SET_TASK.answer_policy,
                binding,
            ),
        }
    )
    result = CypherResult(
        success=True,
        raw={
            "data": {
                "nodes": {
                    "0": {
                        "objectId": ALICE.object_id,
                        "label": ALICE.canonical_name,
                        "kind": ALICE.object_type,
                    },
                    "1": {
                        "objectId": TARGET.object_id,
                        "label": TARGET.canonical_name,
                        "kind": TARGET.object_type,
                    },
                    "2": {
                        "objectId": BOB.object_id,
                        "label": BOB.canonical_name,
                        "kind": BOB.object_type,
                    },
                },
                "edges": [],
                "literals": [],
            }
        },
        status_code=200,
        query_executed=True,
        execution_attempts=1,
        query_fingerprint="b" * 64,
        safety_policy_version=DIRECT_QUERY_POLICY_VERSION,
        safety_rule="allowed",
        bhce_health_after="not_checked",
        circuit_state="closed",
    )

    outcome = asyncio.run(
        execute_direct_v2(
            FakeCoordinator(result),
            query="MATCH (n:Base) RETURN n LIMIT 3",
            task=task,
            oracle=SET_ORACLE,
            resolver=IdentityResolver((ALICE, TARGET, BOB)),
        )
    )

    assert outcome.verdict is not None
    assert outcome.verdict.status is VerdictStatus.INCORRECT
    assert outcome.verdict.reason == "EXACT_SET_MISMATCH"


def test_direct_set_projects_real_ce_collected_node_literals() -> None:
    result = CypherResult(
        success=True,
        raw={
            "data": {
                # An auxiliary node returned outside the answer collection must
                # not contaminate exact-set evidence.
                "nodes": {
                    "99": {
                        "objectId": "AUXILIARY-NODE",
                        "label": "AUXILIARY@EXAMPLE.LOCAL",
                        "kind": "Group",
                    }
                },
                "edges": [],
                "literals": [
                    {
                        "key": "entities",
                        "value": [
                            {
                                "Id": 1,
                                "Labels": ["Base", "User"],
                                "Props": {
                                    "name": ALICE.canonical_name,
                                    "objectid": ALICE.object_id,
                                },
                            },
                            {
                                "Id": 2,
                                "Labels": ["Base", "Group"],
                                "Props": {
                                    "name": TARGET.canonical_name,
                                    "objectid": TARGET.object_id,
                                },
                            },
                        ],
                    },
                    {"key": "total_count", "value": 2},
                ],
            }
        },
    )

    evidence = project_direct_evidence(
        result,
        task=SET_TASK,
        oracle=SET_ORACLE,
        resolver=RESOLVER,
    )

    assert {entity.object_id for entity in evidence.entities} == {
        ALICE.object_id,
        TARGET.object_id,
    }


def test_direct_set_rejects_inconsistent_collected_total() -> None:
    result = CypherResult(
        success=True,
        raw={
            "data": {
                "nodes": {},
                "edges": [],
                "literals": [
                    {
                        "key": "entities",
                        "value": [
                            {
                                "Props": {
                                    "name": ALICE.canonical_name,
                                    "objectid": ALICE.object_id,
                                }
                            }
                        ],
                    },
                    {"key": "total_count", "value": 2},
                ],
            }
        },
    )

    with pytest.raises(DirectAdapterError, match="total_count does not match"):
        project_direct_evidence(
            result,
            task=SET_TASK,
            oracle=SET_ORACLE,
            resolver=RESOLVER,
        )


@pytest.mark.parametrize(
    "result",
    [
        CypherResult(success=True, nodes=[{"name": "not-a-count"}], raw={}),
        CypherResult(
            success=True,
            nodes=[{"count": 7}],
            raw={"data": {"count": 8}},
        ),
        CypherResult(success=True, nodes=[{"count": -1}], raw={}),
        CypherResult(success=True, nodes=[{"count": True}], raw={}),
    ],
)
def test_direct_count_rejects_missing_or_ambiguous_scalar(
    result: CypherResult,
) -> None:
    with pytest.raises(DirectAdapterError, match="direct count evidence is"):
        project_direct_evidence(
            result,
            task=COUNT_TASK,
            oracle=COUNT_ORACLE,
            resolver=RESOLVER,
        )


@pytest.mark.parametrize(
    ("failure_type", "expected"),
    [
        ("policy_rejected", ExecutionClass.MODEL_FAILURE),
        ("query_timeout", ExecutionClass.MODEL_FAILURE),
        ("query_error", ExecutionClass.MODEL_FAILURE),
        ("auth_error", ExecutionClass.INFRA_FAILURE),
        ("transport_error", ExecutionClass.INFRA_FAILURE),
        ("server_error", ExecutionClass.INFRA_FAILURE),
        ("rate_limited", ExecutionClass.INFRA_FAILURE),
        ("response_error", ExecutionClass.INFRA_FAILURE),
        ("circuit_open", ExecutionClass.UNEXECUTED),
    ],
)
def test_direct_failure_taxonomy_preserves_no_reasoning_verdict(
    failure_type: str,
    expected: ExecutionClass,
) -> None:
    coordinator = FakeCoordinator(
        CypherResult(
            success=False,
            error="failure",
            failure_type=failure_type,
            failure_subtype=f"subtype-{failure_type}",
            query_executed=failure_type != "circuit_open",
            execution_attempts=0 if failure_type == "circuit_open" else 1,
            safety_policy_version=DIRECT_QUERY_POLICY_VERSION,
            circuit_state="open" if failure_type == "circuit_open" else "closed",
        )
    )

    outcome = asyncio.run(
        execute_direct_v2(
            coordinator,
            query="MATCH (n {name: 'A'}) RETURN n LIMIT 1",
            task=TASK,
            oracle=ORACLE,
            resolver=RESOLVER,
        )
    )

    assert outcome.receipt.execution_class is expected
    assert outcome.receipt.failure_type == failure_type
    assert outcome.evidence is None
    assert outcome.verdict is None


def test_direct_adapter_requires_authoritative_enabled_policy() -> None:
    coordinator = FakeCoordinator(CypherResult(success=True), enabled=False)

    with pytest.raises(DirectAdapterError, match="requires enabled"):
        asyncio.run(
            execute_direct_v2(
                coordinator,
                query="MATCH (n) RETURN n LIMIT 1",
                task=TASK,
                oracle=ORACLE,
                resolver=RESOLVER,
            )
        )
    assert coordinator.queries == []


def test_real_coordinator_rejects_before_bloodhound_execution() -> None:
    class FakeBHCE:
        def __init__(self) -> None:
            self.calls = 0

        async def run_cypher(self, *args, **kwargs):
            self.calls += 1
            raise AssertionError("policy-rejected query reached BloodHound")

    bhce = FakeBHCE()
    coordinator = DirectQueryCoordinator(
        bhce=bhce,  # type: ignore[arg-type]
        config=DirectQuerySafetyConfig(),
        deny_cache=QueryDenyCache(
            None,
            manifest_fingerprint=FP,
            policy_version=DIRECT_QUERY_POLICY_VERSION,
        ),
    )

    outcome = asyncio.run(
        execute_direct_v2(
            coordinator,
            query="MATCH (n) DELETE n RETURN n",
            task=TASK,
            oracle=ORACLE,
            resolver=RESOLVER,
        )
    )

    assert bhce.calls == 0
    assert outcome.receipt.execution_class is ExecutionClass.MODEL_FAILURE
    assert outcome.receipt.failure_type == "policy_rejected"
    assert outcome.receipt.query_executed is False
