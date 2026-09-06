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
    AbsenceClaim,
    BoundedNegativePolicy,
    CountClaim,
    DecisionClaim,
    DecisionPolicy,
    EdgeDirection,
    EntityRef,
    EvidenceIR,
    ExactCountPolicy,
    ExactRoutePolicy,
    ExactSetPolicy,
    NegativeReasonCode,
    PopulationScope,
    RelationshipPattern,
    RouteClaim,
    SelectionExpression,
    SetClaim,
    Track,
)


@pytest.mark.parametrize("implementation", ["mwnickerson", "mordavid"])
def test_native_absence_requires_complete_scope_and_zero_preserving_counts(
    implementation, subtests,
):
    exact = ("MATCH p=(s {objectid:'user-1'})-[:MemberOf*1..2]->"
             "(t {objectid:'group-1'}) RETURN count(p) AS total")
    optional = ("MATCH (s {objectid:'user-1'}), (t {objectid:'group-1'}) "
                "OPTIONAL MATCH p=(s)-[:MemberOf*1..2]->(t) RETURN count(p) AS total")
    cases = (
        (exact, 0, "valid_negative"),
        (exact, 1, "useful_positive"),
        (exact.replace("count(p)", "count(*)"), 1, "useful_positive"),
        (optional, 0, "valid_negative"),
        (optional.replace("count(p)", "count(DISTINCT p)"), 0, "valid_negative"),
        (optional.replace("count(p)", "count(*)"), 1, "irrelevant"),
        (exact.replace("1..2", "1..3"), 0, "valid_negative"),
        (exact.replace("1..2", "1..3"), 1, "irrelevant"),
        (exact.replace("1..2", "1..1"), 0, "irrelevant"),
        (exact.replace("->", "-"), 0, "valid_negative"),
        (exact.replace("->", "-"), 1, "irrelevant"),
        (exact.replace("MemberOf", "AdminTo"), 0, "irrelevant"),
        (exact.replace("user-1", "other-user"), 0, "irrelevant"),
        (exact.replace("RETURN", "WITH p LIMIT 1 RETURN"), 0, "irrelevant"),
        (exact.replace("RETURN", "WHERE s.enabled = true RETURN"), 0, "irrelevant"),
        ("RETURN 0 AS total", 0, "irrelevant"),
    )
    for query, count, expected in cases:
        with subtests.test(query=query, count=count):
            payload = (
                {"success": True, "info_type": "run", "has_results": True,
                 "node_count": 0, "edge_count": 0,
                 "data": {"nodes": {}, "edges": [],
                          "literals": [{"key": "total", "value": count}]}}
                if implementation == "mwnickerson"
                else {"success": True, "data": [{"total": count}]}
            )
            profile, session, calls = _native_session(implementation, payload)
            claim = AbsenceClaim(
                kind="absence", claim_id="bounded-no-path",
                source={"role": "source", "object_type": "User"},
                target={"role": "target", "object_type": "Group"},
                relationships=("MemberOf",), max_hops=2, semantics="direct",
                reason_codes=(NegativeReasonCode.OBJECTIVE_UNREACHABLE,),
                population_scope=PopulationScope.BENCHMARK_NAMESPACE,
            )
            task = _fingerprinted_task_bundle(
                task_id="native-absence", product="simple", claim=claim,
                policy=BoundedNegativePolicy(kind="bounded_negative"),
                binding=_binding(Track.MCP, claim=claim, expected_cardinality=0,
                                 native_profile=profile),
                input_entities=(
                    EntityRef(object_id="user-1", object_type="User", role="source"),
                    EntityRef(object_id="group-1", object_type="Group", role="target"),
                ), question="Return no_path with reason codes for the bounded absence claim.",
            )
            arguments = {"query": query}
            if implementation == "mwnickerson":
                arguments["info_type"] = "run"
            tool = get_native_implementation(implementation).generic_query_tool
            outcome = asyncio.run(session.call_tool(tool, arguments, task))
            assert calls == [(tool, arguments)]
            assert outcome.executed and outcome.failure is None
            assert outcome.proof_event.kind.value == expected


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


def test_main_native_sets_require_matching_count_and_distinct_pages(subtests):
    for case in ("count-first", "page-first", "missing-count", "wrong-count",
                 "wrong-population", "nondistinct", "wrong-offset", "wrong-type", "empty"):
        with subtests.test(case=case):
            payload = {}
            profile, session, calls = _native_session("mwnickerson", payload)
            claim = SetClaim(
                kind="set", claim_id="all-users", semantics="direct",
                population_scope=PopulationScope.BENCHMARK_NAMESPACE,
                selection=SelectionExpression(projection_role="item", projection_type="User"),
            )
            task = _fingerprinted_task_bundle(
                task_id="native-users", product="simple", claim=claim,
                policy=ExactSetPolicy(kind="exact_set"),
                binding=_binding(Track.MCP, claim=claim, expected_cardinality=3,
                                 native_profile=profile), input_entities=(),
                question="Return the complete set of User objects.",
            )
            count = 0 if case == "empty" else 4 if case == "wrong-count" else 3
            nodes = {} if case == "empty" else {
                str(i): {"objectId": f"user-{i}", "kind": "User", "label": f"USER{i}"}
                for i in range(3)
            }
            if case == "wrong-type":
                nodes["0"]["kind"] = "Group"
            count_query = "MATCH (u:User) RETURN count(DISTINCT u) AS total"
            if case == "wrong-population":
                count_query = count_query.replace(":User", ":Group")
            page_query = ("MATCH (u:User) RETURN DISTINCT u ORDER BY u.objectid "
                          f"SKIP {500 if case == 'wrong-offset' else 0} LIMIT 500")
            if case == "nondistinct":
                page_query = page_query.replace("DISTINCT ", "")
            count_payload = {
                "success": True, "info_type": "run", "has_results": True,
                "node_count": 0, "edge_count": 0,
                "data": {"nodes": {}, "edges": [], "literals": [{"key": "total", "value": count}]},
            }
            page_payload = {
                "success": True, "info_type": "run", "has_results": bool(nodes),
                "node_count": len(nodes), "edge_count": 0,
                "data": {"nodes": nodes, "edges": [], "literals": []},
            }
            sequence = [(count_query, count_payload), (page_query, page_payload)]
            if case == "page-first":
                sequence.reverse()
            elif case == "missing-count":
                sequence = sequence[1:]
            outcomes = []
            for query, response in sequence:
                payload.clear()
                payload.update(response)
                outcomes.append(asyncio.run(session.call_tool(
                    "cypher_query", {"info_type": "run", "query": query}, task,
                    attempt_id="model-a/run-1/attempt-1",
                )))
            assert len(calls) == len(sequence)
            assert all(outcome.executed and outcome.failure is None for outcome in outcomes)
            assert outcomes[-1].proof_event.unlocks_finalization is (
                case in {"count-first", "page-first", "empty"}
            )
            if len(outcomes) == 2:
                assert not outcomes[0].proof_event.unlocks_finalization
            if case == "count-first":
                # Reusing the transport for another repetition/retry must not
                # reuse the prior count. The new attempt sees only a page.
                retry = asyncio.run(session.call_tool(
                    "cypher_query", {"info_type": "run", "query": page_query}, task,
                    attempt_id="model-a/run-1/attempt-2",
                ))
                assert retry.executed and retry.failure is None
                assert not retry.proof_event.unlocks_finalization
                prior_calls = len(calls)
                missing_attempt = asyncio.run(session.call_tool(
                    "cypher_query", {"info_type": "run", "query": page_query}, task,
                ))
                assert not missing_attempt.executed and missing_attempt.failure == "HARNESS_ERROR"
                assert len(calls) == prior_calls


def test_native_set_coverage_handles_multiple_pages_and_fixed_windows(subtests):
    from ori.eval.v2.native_proof import NativeSetProofState

    profile, _, _ = _native_session("mwnickerson", {})
    for case in ("complete", "gap", "overlap", "short-prefix", "changed-page", "window"):
        with subtests.test(case=case):
            windowed = case == "window"
            claim = SetClaim(
                kind="set", claim_id="users", semantics="direct",
                population_scope=PopulationScope.BENCHMARK_NAMESPACE,
                selection=SelectionExpression(
                    projection_role="item", projection_type="User",
                    offset=500 if windowed else 0, limit=500 if windowed else None,
                ),
            )
            task = _fingerprinted_task_bundle(
                task_id="page-test", product="simple", claim=claim,
                policy=ExactSetPolicy(kind="exact_set"), input_entities=(),
                binding=_binding(Track.MCP, claim=claim,
                                 expected_cardinality=500 if windowed else 501,
                                 native_profile=profile),
                question="Return the set of User objects in the declared window.",
            )
            state = NativeSetProofState()

            def page(offset, ids):
                return state.observe(
                    profile, task, "MATCH (u:User) RETURN DISTINCT u ORDER BY u.objectid "
                    f"SKIP {offset} LIMIT 500",
                    EvidenceIR(task_id=task.task_id, raw_digest="a" * 64, entities=tuple(
                        EntityRef(object_id=f"user-{i}", object_type="User", role="item")
                        for i in ids
                    )),
                )

            if windowed:
                assert not page(0, range(2))
                assert page(500, range(2))
                continue
            assert not state.observe(
                profile, task, "MATCH (u:User) RETURN count(DISTINCT u) AS total",
                EvidenceIR(task_id=task.task_id, raw_digest="b" * 64, count=501),
            )
            assert not page(0, range(499 if case == "short-prefix" else 500))
            if case == "changed-page":
                with pytest.raises(ValueError, match="page changed"):
                    page(0, range(1, 501))
                continue
            last_ids = [0] if case == "overlap" else [499, 500] if case == "short-prefix" else [500]
            assert page(1000 if case == "gap" else 500, last_ids) is (case == "complete")


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


def test_native_decision_subjects_require_one_directed_witness(subtests):
    for case in ("forward", "reverse", "fork", "wrong-type", "node-only", "detached"):
        with subtests.test(case=case):
            nodes = {
                "0": {"objectId": "user-1", "kind": "User", "label": "ALICE"},
                "1": {"objectId": "group-1", "kind": "Group", "label": "ADMINS"},
                "2": {"objectId": "group-2", "kind": "Group", "label": "MID"},
            }
            pairs = [("0", "1")]
            if case == "reverse":
                pairs = [("1", "0")]
            elif case == "fork":
                pairs = [("2", "0"), ("2", "1")]
            elif case == "node-only":
                pairs = []
            elif case == "wrong-type":
                nodes["0"]["kind"] = "Computer"
            edges = [{"source": a, "target": b, "kind": "MemberOf"} for a, b in pairs]
            payload = {"success": True, "info_type": "run", "has_results": True,
                       "node_count": len(nodes), "edge_count": len(edges),
                       "data": {"nodes": nodes, "edges": edges, "literals": []}}
            profile, session, _ = _native_session("mwnickerson", payload)
            claim = DecisionClaim(
                kind="decision", claim_id="subjects", semantics="direct",
                subjects=({"role": "source", "object_type": "User"},
                          {"role": "target", "object_type": "Group"}),
                required_relationships=(RelationshipPattern(
                    source_role="source", source_type="User",
                    target_role="target", target_type="Group",
                    relationship="MemberOf", direction=EdgeDirection.OUTBOUND,
                ),),
                population_scope=PopulationScope.BENCHMARK_NAMESPACE,
            )
            task = _fingerprinted_task_bundle(
                task_id="native-decision", product="simple", claim=claim,
                policy=DecisionPolicy(kind="decision", require_supporting_evidence=True),
                binding=_binding(Track.MCP, claim=claim, expected_cardinality=3,
                                 native_profile=profile),
                input_entities=(
                    EntityRef(object_id="user-1", object_type="User", role="source"),
                    EntityRef(object_id="group-1", object_type="Group", role="target"),
                ), question="Decide the claim using relationship evidence for both subjects.",
            )
            selectors = ["(s {objectid:'user-1'})", "(t {objectid:'group-1'})"]
            if case == "reverse":
                selectors.reverse()
            query = f"MATCH p={selectors[0]}-[:MemberOf]->{selectors[1]} RETURN p"
            if case == "detached":
                query = ("MATCH (s {objectid:'user-1'}), (t {objectid:'group-1'}) "
                         "MATCH p=(a)-[:MemberOf]->(b) RETURN p")
            outcome = asyncio.run(session.call_tool(
                "cypher_query", {"info_type": "run", "query": query}, task,
            ))
            assert outcome.executed and outcome.failure is None
            assert outcome.proof_event.unlocks_finalization is (case in {"forward", "reverse"})


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
