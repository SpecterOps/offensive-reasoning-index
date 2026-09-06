"""Native session-to-public-count proof, without a model or live backend."""

import asyncio
import json
from copy import deepcopy

import pytest
from mcp import types

from ori.eval.v2.compiler import _binding, _fingerprinted_task_bundle
from ori.eval.v2.native_capability import build_native_capability_profile
from ori.eval.v2.native_mcp_profiles import get_native_implementation
from ori.eval.v2.native_mcp_runtime import NativeCallDecision, NativeMCPSession
from ori.eval.v2.schema import (
    CountClaim,
    EntityRef,
    ExactCountPolicy,
    ExactRoutePolicy,
    ExactSetPolicy,
    PopulationScope,
    RelationshipPattern,
    RouteClaim,
    SelectionExpression,
    SetClaim,
    Track,
)


def _native_session(implementation, payload):
    """Real session dispatch with only the external MCP transport stubbed."""
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
    calls = []

    class Session:
        async def call_tool(self, name, arguments):
            calls.append((name, deepcopy(arguments)))
            return types.CallToolResult(content=[
                types.TextContent(type="text", text=json.dumps(payload)),
            ])

    return profile, NativeMCPSession(
        Session(), implementation, **discovery, surface_availability=availability,
        capability_profile=profile, guard=lambda *_: NativeCallDecision(True, "test-only guard"),
    ), calls


def test_armadin_domain_set_proof_uses_complete_native_rows(subtests):
    for case in ("complete", "empty", "wrong-count", "duplicate", "truncated", "missing-id"):
        with subtests.test(case=case):
            rows = [{"objectid": "domain-1", "name": "EXAMPLE.TEST"}]
            payload = {"success": True, "domains": rows, "count": 1}
            if case == "empty":
                payload.update(domains=[], count=0)
            elif case == "wrong-count":
                payload["count"] = 2
            elif case == "duplicate":
                rows.append(deepcopy(rows[0]))
                payload["count"] = 2
            elif case == "truncated":
                payload["has_more"] = True
            elif case == "missing-id":
                del rows[0]["objectid"]
            profile, session, calls = _native_session("armadin", payload)
            claim = SetClaim(
                kind="set", claim_id="all-domains", semantics="direct",
                population_scope=PopulationScope.BENCHMARK_NAMESPACE,
                selection=SelectionExpression(projection_role="item", projection_type="Domain"),
            )
            task = _fingerprinted_task_bundle(
                task_id="native-domains", product="simple", claim=claim,
                policy=ExactSetPolicy(kind="exact_set"),
                binding=_binding(Track.MCP, claim=claim, expected_cardinality=1,
                                 native_profile=profile),
                input_entities=(), question="Return the complete set of Domain objects.",
            )
            outcome = asyncio.run(session.call_tool("find_domains", {}, task))
            assert calls == [("find_domains", {})]
            assert outcome.executed and outcome.failure is None
            assert outcome.proof_event.unlocks_finalization is (case in {"complete", "empty"})
            if case in {"complete", "empty"}:
                assert outcome.proof_event.reason == "native_complete_domain_set"
                assert outcome.projection.evidence.count == (0 if case == "empty" else 1)
            if case == "complete":
                for change in (
                    {"offset": 1}, {"limit": 1}, {"require_complete": False},
                    {"projection_type": "User"},
                    {"predicates": [{"role": "item", "property_name": "name",
                                     "operator": "equals", "value": "EXAMPLE.TEST"}]},
                ):
                    with subtests.test(scope=change):
                        selection = SelectionExpression.model_validate_json(json.dumps({
                            **claim.selection.model_dump(mode="json"), **change,
                        }))
                        # The compiler refuses these contracts for this native
                        # tool. Also exercise the runtime defense independently
                        # with a deliberately inconsistent internal task.
                        changed_claim = claim.model_copy(update={"selection": selection})
                        with pytest.raises(ValueError, match="unsupported Armadin"):
                            _binding(Track.MCP, claim=changed_claim,
                                     expected_cardinality=1, native_profile=profile)
                        changed_task = task.model_copy(update={
                            "acceptance_spec": task.acceptance_spec.model_copy(
                                update={"selection": selection}),
                        })
                        denied = asyncio.run(session.call_tool("find_domains", {}, changed_task))
                        assert denied.executed and denied.failure is None
                        assert not denied.proof_event.unlocks_finalization
                        assert denied.proof_event.reason == "native_domain_scope_unproven"


@pytest.mark.parametrize("implementation", ["mwnickerson", "armadin"])
def test_native_route_requires_returned_public_directed_connectivity(implementation, subtests):
    for case in ("positive", "reversed", "disconnected", "wrong-id", "wrong-type",
                 "node-only", "wrong-selector", "over-bound"):
        with subtests.test(case=case):
            source = EntityRef(object_id="user-1", object_type="User", role="source",
                               canonical_name="ALICE")
            target = EntityRef(object_id="group-1", object_type="Group", role="target",
                               canonical_name="ADMINS")
            node_id = "other-user" if case == "wrong-id" else "user-1"
            node_type = "Computer" if case == "wrong-type" else "User"
            arguments = {"source": "OTHER" if case == "wrong-selector" else "ALICE",
                         "target": "ADMINS"}
            if implementation == "armadin":
                nodes = [{"objectid": node_id, "name": "ALICE", "type": node_type},
                         {"objectid": "group-1", "name": "ADMINS", "type": "Group"}]
                payload = {"success": True, "path_found": True, **arguments,
                           "path_length": 1, "nodes": nodes, "edges": [{"type": "MemberOf"}]}
                if case == "reversed":
                    nodes.reverse()
                elif case == "disconnected":
                    nodes[-1]["objectid"] = "other-group"
                elif case == "node-only":
                    payload["edges"] = []
                elif case == "over-bound":
                    nodes.insert(1, {"objectid": "group-2", "name": "MID", "type": "Group"})
                    payload.update(path_length=2, edges=[{"type": "MemberOf"}] * 2)
                tool = "find_shortest_path"
            else:
                nodes = {"9": {"objectId": "group-1", "kind": "Group", "label": "ADMINS"},
                         "2": {"objectId": node_id, "kind": node_type, "label": "ALICE"}}
                edges = [{"source": "2", "target": "9", "kind": "MemberOf"}]
                if case == "reversed":
                    edges[0].update(source="9", target="2")
                elif case == "disconnected":
                    nodes["8"] = {"objectId": "other-group", "kind": "Group", "label": "OTHER"}
                    edges[0]["target"] = "8"
                elif case == "node-only":
                    edges = []
                elif case == "over-bound":
                    nodes["8"] = {"objectId": "group-2", "kind": "Group", "label": "MID"}
                    edges[0]["target"] = "8"
                    edges.append({"source": "8", "target": "9", "kind": "MemberOf"})
                payload = {"success": True, "info_type": "run", "has_results": True,
                           "node_count": len(nodes), "edge_count": len(edges),
                           "data": {"nodes": nodes, "edges": edges,
                                    "literals": [{"key": "endpoint", "value": "ALICE"}]}}
                arguments = {"info_type": "run", "query": (
                    f"MATCH p=(s:User {{name: '{arguments['source']}'}})"
                    "-[:MemberOf*1..1]->(t:Group {name: 'ADMINS'}) RETURN p"
                )}
                tool = "cypher_query"
            profile, session, calls = _native_session(implementation, payload)
            claim = RouteClaim(
                kind="route", claim_id="public-route", semantics="direct",
                source={"role": "source", "object_type": "User"},
                target={"role": "target", "object_type": "Group"}, max_hops=1,
                population_scope=PopulationScope.BENCHMARK_NAMESPACE,
            )
            task = _fingerprinted_task_bundle(
                task_id="native-route", product="simple", claim=claim,
                policy=ExactRoutePolicy(kind="exact_route"),
                binding=_binding(Track.MCP, claim=claim, expected_cardinality=2,
                                 native_profile=profile),
                input_entities=(source, target),
                question="Return a directed path with edges from ALICE to ADMINS.",
            )
            outcome = asyncio.run(session.call_tool(tool, arguments, task))
            assert calls == [(tool, arguments)]
            assert outcome.executed and outcome.failure is None
            assert outcome.proof_event.unlocks_finalization is (case == "positive")


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
