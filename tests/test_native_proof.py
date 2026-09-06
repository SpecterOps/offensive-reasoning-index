"""Native session-to-public-count proof, without a model or live backend."""

import asyncio
import json

import pytest
from mcp import types

from ori.eval.v2.compiler import _binding, _fingerprinted_task_bundle
from ori.eval.v2.native_capability import build_native_capability_profile
from ori.eval.v2.native_mcp_profiles import get_native_implementation
from ori.eval.v2.native_mcp_runtime import NativeCallDecision, NativeMCPSession
from ori.eval.v2.schema import (
    CountClaim,
    ExactCountPolicy,
    PopulationScope,
    RelationshipPattern,
    SelectionExpression,
    Track,
)


@pytest.mark.parametrize("implementation", ["mwnickerson", "mordavid"])
def test_guarded_native_call_requires_actual_public_count_scope(implementation, subtests):
    source = get_native_implementation(implementation)
    discovery = dict(
        tools=[{"name": name, "inputSchema": {"type": "object"}}
               for name in source.native_tool_names],
        prompts=[{"name": name} for name in source.prompt_names],
        resources=[{"name": uri, "uri": uri} for uri in source.resource_uris],
        resource_templates=[],
    )
    availability = {"tools": True, "prompts": bool(source.prompt_names),
                    "resources": bool(source.resource_uris)}
    profile = build_native_capability_profile(
        implementation, runtime_fingerprint="1" * 64,
        dependency_lock_fingerprint="2" * 64, backend_binding_fingerprint="3" * 64,
        surface_availability=availability, **discovery,
    )
    claim = CountClaim.model_validate({
        "kind": "count", "claim_id": "public-users", "semantics": "direct",
        "population_scope": PopulationScope.BENCHMARK_NAMESPACE,
        "selection": {"projection_role": "item", "projection_type": "User"},
    })
    task = _fingerprinted_task_bundle(
        task_id="native-users-count", product="simple", claim=claim,
        policy=ExactCountPolicy(kind="exact_count"),
        binding=_binding(Track.MCP, claim=claim, expected_cardinality=3, native_profile=profile),
        input_entities=(), question="Count all User objects in the benchmark graph.",
    )
    calls = []

    class Session:
        async def call_tool(self, name, arguments):
            calls.append((name, arguments))
            query = arguments["query"]
            count = 1 if "LIMIT 1" in query else 0 if "WHERE false" in query else 3
            payload = (
                {"success": True, "info_type": "run", "has_results": True,
                 "node_count": 0, "edge_count": 0,
                 "data": {"nodes": {}, "edges": [],
                          "literals": [{"key": "n", "value": count}]}}
                if implementation == "mwnickerson" else {"success": True, "data": [{"n": count}]}
            )
            return types.CallToolResult(content=[
                types.TextContent(type="text", text=json.dumps(payload)),
            ])

    native = NativeMCPSession(
        Session(), implementation, **discovery, surface_availability=availability,
        capability_profile=profile, guard=lambda *_: NativeCallDecision(True, "test-only guard"),
    )
    for query, unlocks in (
        ("MATCH (u:User) RETURN count(u) AS n", True),
        ("MATCH (u:User) RETURN count(DISTINCT u) AS n /* literal result */", True),
        ("RETURN 3 AS n", False),
        ("MATCH (g:Group) RETURN count(g) AS n", False),
        ("MATCH (u:User) WHERE u.enabled = true RETURN count(u) AS n", False),
        ("MATCH (u:User) WITH u LIMIT 1 RETURN count(u) AS n", False),
        ("MATCH (u:User) WITH u WHERE false RETURN count(u) AS n", False),
        ("MATCH (u:User) WITH 0 AS u RETURN count(u) AS n", False),
        ("MATCH (u:User) RETURN count(DISTINCT u.name) AS n", False),
        ("MATCH (u:User) RETURN count(u) + 1 AS n", False),
    ):
        with subtests.test(query=query):
            arguments = {"query": query}
            if implementation == "mwnickerson":
                arguments["info_type"] = "run"
            outcome = asyncio.run(native.call_tool(source.generic_query_tool, arguments, task))
            assert outcome.executed and outcome.failure is None
            assert outcome.proof_event is not None
            assert outcome.proof_event.unlocks_finalization is unlocks
            assert calls[-1] == (source.generic_query_tool, arguments)
    assert len(calls) == 10
    membership = claim.model_copy(update={"selection": SelectionExpression(
        projection_role="item", projection_type="User", relationships=(RelationshipPattern(
            source_role="item", target_role="group", source_type="User", target_type="Group",
            relationship="MemberOf", semantics=claim.semantics,
        ),),
    )})
    membership_task = _fingerprinted_task_bundle(
        task_id="native-membership-count", product="simple", claim=membership,
        policy=ExactCountPolicy(kind="exact_count"),
        binding=_binding(
            Track.MCP, claim=membership, expected_cardinality=3, native_profile=profile,
        ),
        input_entities=(), question="Count User objects with a MemberOf relationship to a Group.",
    )
    for distinct, unlocks in (("", False), ("DISTINCT ", True)):
        with subtests.test(distinct=distinct):
            arguments = {
                "query": f"MATCH (u:User)-[:MemberOf]->(g:Group) RETURN count({distinct}u) AS n",
            }
            if implementation == "mwnickerson":
                arguments["info_type"] = "run"
            outcome = asyncio.run(native.call_tool(
                source.generic_query_tool, arguments, membership_task,
            ))
            assert outcome.executed and outcome.failure is None
            assert outcome.proof_event.unlocks_finalization is unlocks
    assert len(calls) == 12
    mismatched = task.model_copy(update={"binding": task.binding.model_copy(update={
        "capability_profile_id": "different-profile",
    })})
    rejected = asyncio.run(native.call_tool(source.generic_query_tool, arguments, mismatched))
    assert not rejected.executed and rejected.failure == "HARNESS_ERROR"
    assert len(calls) == 12
    with pytest.raises(ValueError, match="discovery mismatch"):
        NativeMCPSession(
            Session(), implementation, **{**discovery, "tools": [
                {**tool, "description": "changed"} for tool in discovery["tools"]
            ]}, surface_availability=availability, capability_profile=profile,
            guard=lambda *_: NativeCallDecision(True, "test-only guard"),
        )
