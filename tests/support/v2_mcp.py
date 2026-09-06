"""Synthetic MCP route fixtures shared by adapter and runtime tests."""

from __future__ import annotations

from ori.eval.v2.compiler import compile_acceptance_spec
from ori.eval.v2.identity import IdentityResolver
from ori.eval.v2.mcp import MCPToolLoop, build_mcp_capability_profile
from ori.eval.v2.schema import (
    EdgeWitness,
    EntityRef,
    EntitySelector,
    ExactRoutePolicy,
    ExecutionBounds,
    MCPBindingMode,
    MCPClaimEvidenceContract,
    OracleBundle,
    PopulationScope,
    RelationshipSemantics,
    RouteClaim,
    RouteVariant,
    TaskBundle,
    Track,
    TrackBinding,
)

FP = "a" * 64

ALICE = EntityRef(
    object_id="USER-A",
    object_type="User",
    domain="EXAMPLE.LOCAL",
    role="source",
    canonical_name="ALICE@EXAMPLE.LOCAL",
)

TARGET = EntityRef(
    object_id="GROUP-B",
    object_type="Group",
    domain="EXAMPLE.LOCAL",
    role="target",
    canonical_name="DOMAIN ADMINS@EXAMPLE.LOCAL",
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

PROFILE = build_mcp_capability_profile()

POLICY = ExactRoutePolicy(kind="exact_route")

BINDING = TrackBinding(
    track=Track.MCP,
    capability_profile_id=PROFILE.profile_id,
    semantics=RelationshipSemantics.DIRECT,
    bounds=ExecutionBounds(
        max_hops=1,
        max_result_cardinality=1,
        page_size=1,
        max_pages=1,
        require_total_count=False,
        require_stable_ordering=True,
        max_output_bytes=65_536,
        max_transcript_bytes=262_144,
        max_tool_calls=12,
        timeout_seconds=120.0,
    ),
    mcp_tool_loop=MCPToolLoop.NATIVE_OPENAI_COMPATIBLE.value,
    mcp_resource_mode="off",
    mcp_binding_mode=MCPBindingMode.CYPHER_ENABLED,
    mcp_evidence_contract=MCPClaimEvidenceContract(
        result_kind="path",
        required_input_roles=("source", "target"),
    ),
)

TASK = TaskBundle(
    task_id="simple.mcp.route@2",
    revision=2,
    product="simple",
    claim_kind="route",
    answer_policy=POLICY,
    acceptance_spec=compile_acceptance_spec(CLAIM, POLICY, BINDING),
    binding=BINDING,
    input_entities=(ALICE, TARGET),
    question="Find the exact edge route.",
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
    resolved_roles=(ALICE, TARGET),
    route_variants=(RouteVariant(variant_id="canonical", edges=(EDGE,)),),
    graph_edge_registry=(EDGE,),
    graph_fact_registry_fingerprint=FP,
    required_mechanisms=("MemberOf",),
    source_id=ALICE.object_id,
    target_id=TARGET.object_id,
    oracle_fingerprint=FP,
)

RESOLVER = IdentityResolver((ALICE, TARGET))

def _answer(*, relationship: str = "MemberOf"):
    return {
        "task_id": TASK.task_id,
        "path_status": "found",
        "edges": [
            {
                "source_id": "EXAMPLE\\ALICE",
                "relationship": relationship,
                "target_id": "DOMAIN ADMINS@EXAMPLE.LOCAL",
            }
        ],
    }
