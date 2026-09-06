from __future__ import annotations

from ori.eval.bhce import CypherResult
from ori.eval.direct_query_safety import DirectQuerySafetyConfig
from ori.eval.v2.compiler import compile_acceptance_spec
from ori.eval.v2.identity import IdentityResolver
from ori.eval.v2.schema import (
    DIRECT_QUERY_POLICY_VERSION,
    EdgeWitness,
    EntityRef,
    EntitySelector,
    ExactRoutePolicy,
    ExactSetPolicy,
    ExecutionBounds,
    OracleBundle,
    PopulationScope,
    RelationshipSemantics,
    RouteClaim,
    RouteVariant,
    SelectionExpression,
    SetClaim,
    TaskBundle,
    Track,
    TrackBinding,
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
