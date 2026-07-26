from __future__ import annotations

import asyncio

import pytest

from ori.eval.bhce import CypherResult
from ori.eval.direct_query_safety import (
    DirectQueryCoordinator,
    DirectQuerySafetyConfig,
    QueryDenyCache,
)
from ori.eval.v2.compiler import DIRECT_RESULT_CONTRACT_VERSION
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
from ori.eval.v2.schema import (
    DIRECT_QUERY_POLICY_VERSION,
    CountClaim,
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
    OracleBundle,
    PopulationScope,
    PredicateOperator,
    PropertyPredicate,
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
TASK = TaskBundle(
    task_id="simple.direct.route@2",
    revision=2,
    product="simple",
    claim_kind="route",
    answer_policy=ExactRoutePolicy(kind="exact_route"),
    binding=TrackBinding(
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
    ),
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
        "answer_policy": ExactCountPolicy(kind="exact_count"),
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
        "answer_policy": ExactSetPolicy(kind="exact_set"),
        "binding": TASK.binding.model_copy(
            update={
                "bounds": TASK.binding.bounds.model_copy(
                    update={"max_result_cardinality": 2, "page_size": 2}
                )
            }
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
    raw["data"]["nodes"]["0"]["properties"]["hasspn"] = True

    evidence = project_direct_evidence(
        CypherResult(success=True, raw=raw),
        task=TASK,
        oracle=oracle,
        resolver=RESOLVER,
    )

    assert evidence.observed_properties == oracle.required_properties


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


def test_direct_capability_profile_binds_result_contract() -> None:
    profile = build_direct_capability_profile()

    assert (
        profile.direct_result_contract_version
        == DIRECT_RESULT_CONTRACT_VERSION
        == "ori-direct-result-contract-v1"
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
