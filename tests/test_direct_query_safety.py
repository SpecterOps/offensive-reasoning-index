from __future__ import annotations

import asyncio
import json

import pytest

from ori.eval.bhce import BHHealthResult, CypherResult
from ori.eval.direct_query_safety import (
    DIRECT_QUERY_POLICY_VERSION,
    DirectQueryCoordinator,
    DirectQueryPolicy,
    DirectQuerySafetyConfig,
    QueryDenyCache,
    normalize_query_for_fingerprint,
    query_fingerprint,
)


def _policy() -> DirectQueryPolicy:
    return DirectQueryPolicy(DirectQuerySafetyConfig())


def test_native_inflight_survives_restart_and_failed_completion_write(tmp_path, monkeypatch):
    config = DirectQuerySafetyConfig()
    path = tmp_path / "deny.json"
    cache = QueryDenyCache(path, manifest_fingerprint="fixture",
                          policy_version=config.policy_version)
    cache.record("unrelated", rule="server_query_timeout", detail="fixture")
    cache.record("native", rule="native_inflight", detail="fixture")
    loaded = QueryDenyCache(path, manifest_fingerprint="fixture",
                           policy_version=config.policy_version)
    assert loaded.native_recovery_required
    with monkeypatch.context() as context:
        def fail_write():
            raise OSError("write failed")
        context.setattr(loaded, "_persist", fail_write)
        with pytest.raises(OSError):
            loaded.complete_native("native")
        assert loaded.native_recovery_required
    loaded.complete_native("native")
    assert not loaded.native_recovery_required
    assert loaded.reason_for("unrelated") == "server_query_timeout"
    assert not QueryDenyCache(path, manifest_fingerprint="fixture",
                             policy_version=config.policy_version).native_recovery_required


def test_phase0_greedy_query_is_rejected_without_bloodhound() -> None:
    query = (
        "MATCH p=(a {name:'TTHOMAS@GRANITEMANUFACTURING.LOCAL'})-[*1..]->"
        "(b {name:'DC01.GRANITEMANUFACTURING.LOCAL'}) RETURN p"
    )
    decision = _policy().evaluate(query)
    assert decision.allowed is False
    assert decision.rule == "unbounded_wildcard_path_enumeration"


def test_policy_rejects_observed_recursive_alternation_explosion() -> None:
    query = (
        "MATCH (s:User {name: 'CAGUIRRE@GRANITEMANUFACTURING.LOCAL'}), "
        "(t:Group {name: 'DOMAIN ADMINS@GRANITEMANUFACTURING.LOCAL'}) "
        "MATCH p=(s)-[:MemberOf|AdminTo|HasSession|GenericAll|GenericWrite|"
        "WriteDacl|WriteOwner|Owns|AllExtendedRights|ForceChangePassword|"
        "AddMember|AddSelf|AddKeyCredentialLink|WriteSPN|AllowedToAct|"
        "AllowedToDelegate|CanRDP|CanPSRemote|ExecuteDCOM|SQLAdmin|"
        "ReadLAPSPassword|ReadGMSAPassword|ReadMSAPassword|DCSync|GetChanges|"
        "GetChangesAll*0..8]->()-[:ADCSESC1|ADCSESC3|ADCSESC4|ADCSESC6a|"
        "ADCSESC6b|ADCSESC9a|ADCSESC9b|ADCSESC10a|ADCSESC10b|ADCSESC13|"
        "GoldenCert|CanAbuseUPNCertMapping|CanAbuseWeakCertBinding]->()-"
        "[:MemberOf|AdminTo|HasSession|GenericAll|GenericWrite|WriteDacl|"
        "WriteOwner|Owns|AllExtendedRights|ForceChangePassword|AddMember|"
        "AddSelf|AddKeyCredentialLink|WriteSPN|AllowedToAct|AllowedToDelegate|"
        "CanRDP|CanPSRemote|ExecuteDCOM|SQLAdmin|ReadLAPSPassword|"
        "ReadGMSAPassword|ReadMSAPassword|DCSync|GetChanges|GetChangesAll*0..8]"
        "->(t) RETURN p ORDER BY length(p) LIMIT 1"
    )

    decision = _policy().evaluate(query)

    assert decision.allowed is False
    assert decision.rule == "recursive_expansion_complexity_exceeded"
    assert "416 exceeds 256" in decision.detail


def test_recursive_expansion_complexity_budget_has_an_inclusive_boundary() -> None:
    config = DirectQuerySafetyConfig(max_recursive_expansion_complexity=16)
    policy = DirectQueryPolicy(config)
    allowed = (
        "MATCH p=(u:User {name:'A@TEST.LOCAL'})-"
        "[:MemberOf|GenericAll|AdminTo|HasSession*1..4]->(g:Group) RETURN p"
    )
    rejected = allowed.replace("|HasSession*1..4", "|HasSession|CanRDP*1..4")

    assert policy.evaluate(allowed).allowed is True
    decision = policy.evaluate(rejected)
    assert decision.allowed is False
    assert decision.rule == "recursive_expansion_complexity_exceeded"


def test_zero_hop_recursive_pattern_does_not_consume_fallback_hop_budget() -> None:
    config = DirectQuerySafetyConfig(max_recursive_expansion_complexity=1)
    policy = DirectQueryPolicy(config)
    query = (
        "MATCH p=(u:User {name:'A@TEST.LOCAL'})-"
        "[:MemberOf|GenericAll|AdminTo*0..0]->(g:Group) RETURN p"
    )

    assert policy.evaluate(query).allowed is True


def test_greedy_open_ended_recursive_query_is_rejected() -> None:
    query = (
        "MATCH p=(u:User {name:'A@TEST.LOCAL'})-[:MemberOf*100..]"
        "->(g:Group {name:'DOMAIN ADMINS@TEST.LOCAL'}) RETURN p"
    )

    decision = _policy().evaluate(query)

    assert decision.allowed is False
    assert decision.rule == "recursive_hop_limit_exceeded"


@pytest.mark.parametrize(
    "query",
    [
        (
            "MATCH p=shortestPath((u:User)-[*1..]->(g:Group)) "
            "WHERE g.name = 'DOMAIN ADMINS@TEST.LOCAL' RETURN p"
        ),
        ("MATCH p=(u:User)-[:MemberOf*1..]->(g:Group {name: 'DOMAIN ADMINS@TEST.LOCAL'}) RETURN p"),
        (
            "MATCH p=(u:User {name: 'A@TEST.LOCAL'})-[*1..5]->"
            "(c:Computer {name: 'C.TEST.LOCAL'}) RETURN p LIMIT 1"
        ),
        (
            "MATCH p=shortestPath((u {name: 'A@TEST.LOCAL'})-[*1..12]->"
            "(g {name: 'DOMAIN ADMINS@TEST.LOCAL'})) RETURN p"
        ),
        (
            "MATCH p=shortestPath((u:User)-[*1..]->(g:Group)) "
            "WHERE g.name = 'DOMAIN ADMINS@TEST.LOCAL' RETURN p LIMIT 1"
        ),
    ],
)
def test_documented_selective_cysql_shapes_are_admitted(query: str) -> None:
    decision = _policy().evaluate(query)
    assert decision.allowed is True, decision


@pytest.mark.parametrize(
    "query",
    [
        "MATCH (u:User {hasspn: true}) RETURN u",
        "MATCH (u:User) WHERE u.hasspn = true RETURN u",
        ("MATCH (c:Computer {unconstraineddelegation: true}) WHERE NOT c.isdc = true RETURN c"),
        ("MATCH (c:Computer) WHERE c.unconstraineddelegation = true AND c.isdc = false RETURN c"),
    ],
)
def test_inline_and_where_scalar_filters_admit_standalone_node_sets(
    query: str,
) -> None:
    assert _policy().evaluate(query).allowed is True


@pytest.mark.parametrize(
    ("query", "rule"),
    [
        (
            "MATCH p=allShortestPaths((u:User)-[*1..]->(g:Group)) "
            "WHERE g.name='DOMAIN ADMINS@TEST.LOCAL' RETURN p",
            "unbounded_all_shortest_paths",
        ),
        (
            "MATCH p=(u:User)-[*1..13]->(g:Group) RETURN p LIMIT 1",
            "recursive_hop_limit_exceeded",
        ),
        (
            "MATCH p=(u:User)-[*1..5]->(g:Group) RETURN p",
            "unselective_recursive_expansion",
        ),
        (
            "MATCH p=(u:User)-[:MemberOf*1..]->(g:Group) RETURN p LIMIT 10",
            "unselective_recursive_expansion",
        ),
        (
            "MATCH p=shortestPath((u:User)-[*1..]->(g:Group)) RETURN p LIMIT 1",
            "unselective_shortest_path",
        ),
        (
            "MATCH p=(n)-->(m) RETURN p",
            "unselective_relationship_enumeration",
        ),
        (
            "MATCH (d:Domain {name: 'TEST.LOCAL'}), "
            "p=(u:User)-[:MemberOf*1..12]->(g:Group) RETURN p LIMIT 1000",
            "unselective_recursive_expansion",
        ),
        (
            "MATCH (u:User {name:'A@TEST.LOCAL'})-[:MemberOf]->(g:Group), "
            "(x:User)-[r]->(y:Group) RETURN x,y",
            "unselective_relationship_enumeration",
        ),
        (
            "MATCH p=(c:Computer {enabled: true})-[r]->(n) RETURN p",
            "unselective_relationship_enumeration",
        ),
        (
            "MATCH p=(c:Computer)-[r]->(n) WHERE c.enabled = true RETURN p",
            "unselective_relationship_enumeration",
        ),
        ("MATCH (n) RETURN n", "unselective_node_enumeration"),
        ("MATCH (u:User) RETURN u", "unselective_node_enumeration"),
        (
            "MATCH (u:User {name:'A@TEST.LOCAL'}), (c:Computer) RETURN c",
            "unselective_node_enumeration",
        ),
        (
            "MATCH (u:User) WITH u LIMIT 1 MATCH (c:Computer) RETURN c",
            "unselective_node_enumeration",
        ),
        (
            "MATCH (n) RETURN count(n), n",
            "unselective_node_enumeration",
        ),
        (
            "MATCH (n)-[r]->(m) RETURN count(r), r",
            "unselective_relationship_enumeration",
        ),
        (
            "MATCH (u:User) RETURN u.enabled = true",
            "unselective_node_enumeration",
        ),
        (
            "MATCH (u:User)-[r]->(g:Group) RETURN u.name = 'A@TEST.LOCAL', r",
            "unselective_relationship_enumeration",
        ),
        (
            "MATCH p=(u:User)-[:MemberOf*1..]->(g:Group) RETURN u.name = 'A@TEST.LOCAL', p",
            "unselective_recursive_expansion",
        ),
        (
            "MATCH (u:User {name:'A@TEST.LOCAL'}) WITH 1 AS ignored "
            "MATCH (u:User)-[r]->(g:Group) RETURN r",
            "unselective_relationship_enumeration",
        ),
        (
            "MATCH (u:User) WHERE u.name='A@TEST.LOCAL' WITH 1 AS ignored "
            "MATCH p=(u:User)-[:MemberOf*1..]->(g:Group) RETURN p",
            "unselective_recursive_expansion",
        ),
        (
            "MATCH (u:User {hasspn:true}) WITH 1 AS ignored MATCH (u:User) RETURN u",
            "unselective_node_enumeration",
        ),
        (
            "MATCH (n) WITH count(n) AS total, n RETURN total, n",
            "unselective_node_enumeration",
        ),
        (
            "MATCH (n)-[r]->(m) WITH count(r) AS total, r RETURN total, r",
            "unselective_relationship_enumeration",
        ),
        (
            "MATCH (u:User {name:'A@TEST.LOCAL'}) RETURN u "
            "UNION MATCH (u:User)-[r]->(g:Group) RETURN r",
            "unsupported_set_operation",
        ),
        ("MATCH (n) DELETE n", "non_read_only_query"),
        ("MATCH (n) RETURN n LIMIT 1001", "result_limit_too_large"),
    ],
)
def test_high_risk_cysql_shapes_are_rejected(query: str, rule: str) -> None:
    decision = _policy().evaluate(query)
    assert decision.allowed is False
    assert decision.rule == rule


def test_selectivity_propagates_across_one_connected_multihop_pattern() -> None:
    query = (
        "MATCH (u:User {name:'A@TEST.LOCAL'})-[:MemberOf]->(g:Group)"
        "-[:AdminTo]->(c:Computer) RETURN u,g,c"
    )
    assert _policy().evaluate(query).allowed is True


def test_selectivity_propagates_through_anonymous_node_in_connected_path() -> None:
    query = (
        "MATCH (u:User {name:'A@TEST.LOCAL'})-[:MemberOf]->(:Group)"
        "-[:AdminTo]->(c:Computer) RETURN u,c"
    )
    assert _policy().evaluate(query).allowed is True


@pytest.mark.parametrize(
    "query",
    [
        (
            "MATCH (u:User {name:'A@TEST.LOCAL'}) WITH u "
            "MATCH (u)-[:HasSession]->(c:Computer) RETURN c"
        ),
        (
            "MATCH (u:User) WHERE u.name='A@TEST.LOCAL' WITH u AS source "
            "MATCH (source)-[:HasSession]->(c:Computer) RETURN c"
        ),
        (
            "MATCH (u:User {name:'A@TEST.LOCAL'}) WITH * "
            "MATCH (u)-[:HasSession]->(c:Computer) RETURN c"
        ),
    ],
)
def test_exact_selectors_propagate_through_explicit_with_projection(
    query: str,
) -> None:
    assert _policy().evaluate(query).allowed is True


@pytest.mark.parametrize(
    "query",
    [
        "MATCH (n) RETURN count(n)",
        "MATCH (n) RETURN count(*) AS total",
        "MATCH (n)-[r]->(m) RETURN count(r) AS relationship_count",
        "MATCH (n) WITH count(n) AS total RETURN total",
        "MATCH (n)-[r]->(m) WITH count(r) AS total RETURN total",
        "MATCH (n) WITH count(n) AS total, count(*) AS rows RETURN total, rows",
    ],
)
def test_aggregate_only_counts_are_admitted(query: str) -> None:
    assert _policy().evaluate(query).allowed is True


def test_keywords_inside_literals_and_comments_do_not_trigger_mutation_rule() -> None:
    query = "MATCH (u:User {name: 'CREATE DELETE CALL'}) /* DELETE everything */ RETURN u"
    assert _policy().evaluate(query).allowed is True


@pytest.mark.parametrize(
    "query",
    [
        "MATCH (u:User) WHERE u.name STARTS WITH 'A' RETURN u",
        "MATCH (u:User) WHERE u.name ENDS WITH '@TEST.LOCAL' RETURN u",
    ],
)
def test_string_with_predicates_do_not_create_query_stages(query: str) -> None:
    assert _policy().evaluate(query).allowed is True


def test_fingerprint_ignores_comments_keyword_case_and_formatting() -> None:
    first = "MATCH (u:User {name: 'Alice@TEST.LOCAL'}) WHERE u.enabled = true RETURN u"
    second = (
        "match/* same query */(u:User{name:'Alice@TEST.LOCAL'})"
        "where u.enabled=true return u // trailing comment"
    )

    assert normalize_query_for_fingerprint(first) == normalize_query_for_fingerprint(second)
    assert query_fingerprint(first) == query_fingerprint(second)


def test_fingerprint_preserves_identifier_and_literal_case() -> None:
    baseline = "MATCH (u:User {name: 'Alice@TEST.LOCAL'}) RETURN u"

    assert query_fingerprint(baseline) != query_fingerprint(
        "MATCH (U:User {name: 'Alice@TEST.LOCAL'}) RETURN U"
    )
    assert query_fingerprint(baseline) != query_fingerprint(
        "MATCH (u:User {name: 'ALICE@TEST.LOCAL'}) RETURN u"
    )


def test_config_requires_client_timeout_longer_than_server_timeout() -> None:
    with pytest.raises(ValueError, match="greater than"):
        DirectQuerySafetyConfig.from_mapping(
            {"server_timeout_seconds": 10, "client_timeout_seconds": 10}
        )


def test_config_requires_positive_recursive_expansion_complexity() -> None:
    with pytest.raises(ValueError, match="must be at least 1"):
        DirectQuerySafetyConfig.from_mapping({"max_recursive_expansion_complexity": 0})


class FakeBHCE:
    def __init__(
        self,
        results: list[CypherResult],
        *,
        health_ok: bool = True,
    ) -> None:
        self.results = list(results)
        self.health_ok = health_ok
        self.queries: list[tuple[str, float | None, float | None]] = []
        self.include_properties: list[bool] = []
        self.health_calls = 0

    async def run_cypher(
        self,
        query: str,
        *,
        include_properties: bool = True,
        server_timeout_seconds: float | None = None,
        client_timeout_seconds: float | None = None,
    ) -> CypherResult:
        self.queries.append((query, server_timeout_seconds, client_timeout_seconds))
        self.include_properties.append(include_properties)
        return self.results.pop(0)

    async def check_health(self) -> BHHealthResult:
        self.health_calls += 1
        return BHHealthResult(
            ok=self.health_ok,
            detail="healthy" if self.health_ok else "unhealthy",
            query="MATCH (n:Domain) RETURN n LIMIT 1",
            classification="ok" if self.health_ok else "infra",
        )


def _coordinator(tmp_path, bhce: FakeBHCE) -> DirectQueryCoordinator:
    config = DirectQuerySafetyConfig()
    return DirectQueryCoordinator(
        bhce=bhce,
        config=config,
        deny_cache=QueryDenyCache(
            tmp_path / "deny.json",
            manifest_fingerprint="manifest-sha256",
            policy_version=config.policy_version,
        ),
    )


def test_policy_rejection_never_calls_bloodhound(tmp_path) -> None:
    bhce = FakeBHCE([])
    coordinator = _coordinator(tmp_path, bhce)
    result = asyncio.run(coordinator.execute("MATCH p=(a)-[*1..]->(b) RETURN p"))
    assert result.success is False
    assert result.failure_type == "policy_rejected"
    assert result.query_executed is False
    assert result.execution_attempts == 0
    assert bhce.queries == []


def test_callback_transport_shares_rejection_cache_and_health(tmp_path, subtests) -> None:
    query = "MATCH (u:User {name: 'A@TEST.LOCAL'}) RETURN u"
    bhce = FakeBHCE([])
    coordinator = _coordinator(tmp_path, bhce)
    calls = []

    async def callback():
        calls.append(True)
        return CypherResult(success=False, failure_type="query_timeout", error="timeout")

    rejected = asyncio.run(coordinator.execute_with(
        "MATCH p=(a)-[*1..]->(b) RETURN p", execute_query=callback,
    ))
    assert rejected.failure_type == "policy_rejected" and not calls
    result = asyncio.run(coordinator.execute_with(query, execute_query=callback))
    assert result.failure_type == "query_timeout" and result.bhce_health_after == "healthy"
    assert bhce.health_calls == 1 and calls == [True] and not bhce.queries
    cached = asyncio.run(coordinator.execute_with(query, execute_query=callback))
    assert cached.failure_subtype == "known_expensive_query" and calls == [True]
    legacy_cached = asyncio.run(coordinator.execute(query))
    assert legacy_cached.failure_subtype == "known_expensive_query" and not bhce.queries

    other_query = "MATCH (u:User {name: 'B@TEST.LOCAL'}) RETURN u"
    for failure, healthy, expected_health, opens in (
        ("transport_error", False, "unhealthy", True),
        ("transport_error", True, "healthy", False),
        ("native_tool_error", True, "healthy", False),
        ("native_tool_error", False, "unhealthy", True),
        ("auth_error", True, "not_checked", True),
    ):
        with subtests.test(failure=failure, healthy=healthy):
            coordinator.close_circuit()
            bhce.health_ok = healthy

            async def failed():
                calls.append(True)
                return CypherResult(success=False, failure_type=failure, error="fixture")

            result = asyncio.run(coordinator.execute_with(other_query, execute_query=failed))
            assert result.failure_type == failure and result.bhce_health_after == expected_health
            assert coordinator.circuit_open is opens
            if opens:
                prior_calls = len(calls)
                skipped = asyncio.run(coordinator.execute_with(other_query, execute_query=failed))
                assert skipped.failure_type == "circuit_open" and not skipped.query_executed
                assert len(calls) == prior_calls
            assert coordinator.deny_cache.reason_for(coordinator.policy.evaluate(
                other_query,
            ).fingerprint) is None


@pytest.mark.parametrize("native_operation", [False, True])
def test_callback_and_legacy_execution_share_one_lock(tmp_path, native_operation) -> None:
    query = "MATCH (u:User {name: 'A@TEST.LOCAL'}) RETURN u"

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        bhce = FakeBHCE([CypherResult(success=True)])
        coordinator = _coordinator(tmp_path, bhce)

        async def callback():
            entered.set()
            await release.wait()
            return CypherResult(success=True)

        native = asyncio.create_task(
            coordinator.execute_native_with(query_fingerprint(query), execute_query=callback)
            if native_operation else coordinator.execute_with(query, execute_query=callback)
        )
        await entered.wait()
        direct = asyncio.create_task(coordinator.execute(query, include_properties=False))
        await asyncio.sleep(0)
        assert not bhce.queries and not direct.done()
        release.set()
        first, second = await asyncio.gather(native, direct)
        assert first.success and second.success
        assert first.query_fingerprint == second.query_fingerprint
        assert first.safety_rule == (
            "native_read_only_operation" if native_operation else "allowed"
        )
        assert bhce.queries == [(query, 10.0, 15.0)]
        assert bhce.include_properties == [False]

    asyncio.run(scenario())


def test_callback_contract_and_exceptions_do_not_poison_coordinator(tmp_path, subtests) -> None:
    query = "MATCH (u:User {name: 'A@TEST.LOCAL'}) RETURN u"
    bhce = FakeBHCE([CypherResult(success=True)])
    coordinator = _coordinator(tmp_path, bhce)
    with pytest.raises(TypeError, match="callable"):
        asyncio.run(coordinator.execute_with(query, execute_query=None))
    for value in (None, RuntimeError("fixture"), asyncio.CancelledError()):
        with subtests.test(value=type(value).__name__):
            async def callback():
                if isinstance(value, BaseException):
                    raise value
                return value

            expected = type(value) if isinstance(value, BaseException) else TypeError
            with pytest.raises(expected):
                asyncio.run(coordinator.execute_with(query, execute_query=callback))
            assert not coordinator.circuit_open
            assert not coordinator._lock.locked()
            assert bhce.health_calls == 0 and not bhce.queries
    assert asyncio.run(coordinator.execute(query)).success


def test_queued_duplicate_observes_new_timeout_quarantine(tmp_path) -> None:
    query = "MATCH (u:User {name: 'A@TEST.LOCAL'}) RETURN u"

    async def scenario():
        entered, release, queued = asyncio.Event(), asyncio.Event(), asyncio.Event()
        bhce = FakeBHCE([])
        coordinator = _coordinator(tmp_path, bhce)
        calls = []

        async def first_callback():
            calls.append("first")
            entered.set()
            await release.wait()
            return CypherResult(success=False, failure_type="query_timeout", error="timeout")

        async def duplicate_callback():
            calls.append("duplicate")
            return CypherResult(success=True)

        async def duplicate():
            queued.set()
            return await coordinator.execute_with(query, execute_query=duplicate_callback)

        first = asyncio.create_task(coordinator.execute_with(query, execute_query=first_callback))
        await entered.wait()
        second = asyncio.create_task(duplicate())
        await queued.wait()
        assert not second.done() and calls == ["first"]
        release.set()
        timed_out, skipped = await asyncio.gather(first, second)
        assert timed_out.failure_type == "query_timeout"
        assert timed_out.bhce_health_after == "healthy" and not coordinator.circuit_open
        assert skipped.failure_type == "policy_rejected"
        assert skipped.failure_subtype == "known_expensive_query"
        assert not skipped.query_executed and skipped.execution_attempts == 0
        assert skipped.query_fingerprint == timed_out.query_fingerprint
        assert calls == ["first"] and bhce.health_calls == 1 and not bhce.queries

    asyncio.run(scenario())


def test_admitted_query_executes_once_with_bloodhound_timeout(tmp_path) -> None:
    bhce = FakeBHCE([CypherResult(success=True, nodes=[], node_names=set())])
    coordinator = _coordinator(tmp_path, bhce)
    query = "MATCH (u:User {name: 'A@TEST.LOCAL'}) RETURN u"
    result = asyncio.run(coordinator.execute(query))
    assert result.success is True
    assert len(bhce.queries) == 1
    assert bhce.queries[0] == (query, 10.0, 15.0)
    assert result.execution_attempts == 1
    assert result.safety_policy_version == DIRECT_QUERY_POLICY_VERSION
    cache = json.loads((tmp_path / "deny.json").read_text())
    assert cache["entries"] == {}
    assert cache["manifest_fingerprint"] == "manifest-sha256"


def test_admitted_mcp_projection_can_omit_properties_without_changing_policy(
    tmp_path,
) -> None:
    bhce = FakeBHCE([CypherResult(success=True, nodes=[], node_names=set())])
    coordinator = _coordinator(tmp_path, bhce)
    query = "MATCH (u:User {name: 'A@TEST.LOCAL'}) RETURN u"

    result = asyncio.run(coordinator.execute(query, include_properties=False))

    assert result.success is True
    assert bhce.queries == [(query, 10.0, 15.0)]
    assert bhce.include_properties == [False]
    assert result.safety_rule == "allowed"


def test_server_timeout_quarantines_query_and_does_not_repeat_it(tmp_path) -> None:
    bhce = FakeBHCE(
        [
            CypherResult(
                success=False,
                error="HTTP 500: query timeout",
                failure_type="query_timeout",
                failure_subtype="bloodhound_query_timeout",
            )
        ],
        health_ok=True,
    )
    coordinator = _coordinator(tmp_path, bhce)
    query = "MATCH (u:User {name: 'A@TEST.LOCAL'}) RETURN u"
    first = asyncio.run(coordinator.execute(query))
    second = asyncio.run(coordinator.execute(query))

    assert first.failure_type == "query_timeout"
    assert first.bhce_health_after == "healthy"
    assert second.failure_type == "policy_rejected"
    assert second.failure_subtype == "known_expensive_query"
    assert second.query_executed is False
    assert len(bhce.queries) == 1
    cache = json.loads((tmp_path / "deny.json").read_text())
    assert len(cache["entries"]) == 1


def test_timeout_quarantine_matches_comment_case_and_format_variants(tmp_path) -> None:
    bhce = FakeBHCE(
        [
            CypherResult(
                success=False,
                error="HTTP 500: query timeout",
                failure_type="query_timeout",
                failure_subtype="bloodhound_query_timeout",
            )
        ],
        health_ok=True,
    )
    coordinator = _coordinator(tmp_path, bhce)
    first_query = "MATCH (u:User {name: 'A@TEST.LOCAL'}) RETURN u"
    equivalent_query = "match/* retry */(u:User{name:'A@TEST.LOCAL'})return u"

    first = asyncio.run(coordinator.execute(first_query))
    second = asyncio.run(coordinator.execute(equivalent_query))

    assert first.failure_type == "query_timeout"
    assert second.failure_type == "policy_rejected"
    assert second.failure_subtype == "known_expensive_query"
    assert second.query_executed is False
    assert len(bhce.queries) == 1


def test_complexity_rejection_is_health_checked_and_quarantined(tmp_path) -> None:
    bhce = FakeBHCE(
        [
            CypherResult(
                success=False,
                error="HTTP 400: cypher query is too complex",
                failure_type="query_timeout",
                failure_subtype="bloodhound_query_too_complex",
                status_code=400,
            )
        ],
        health_ok=True,
    )
    coordinator = _coordinator(tmp_path, bhce)
    query = "MATCH (u:User {name: 'A@TEST.LOCAL'}) RETURN u"

    first = asyncio.run(coordinator.execute(query))
    second = asyncio.run(coordinator.execute(query))

    assert first.failure_type == "query_timeout"
    assert first.failure_subtype == "bloodhound_query_too_complex"
    assert first.bhce_health_after == "healthy"
    assert bhce.health_calls == 1
    assert second.failure_type == "policy_rejected"
    assert second.failure_subtype == "known_expensive_query"
    assert second.query_executed is False
    assert len(bhce.queries) == 1


def test_unhealthy_post_failure_opens_circuit_and_skips_later_queries(tmp_path) -> None:
    bhce = FakeBHCE(
        [
            CypherResult(
                success=False,
                error="Request failed: server disconnected",
                failure_type="transport_error",
                failure_subtype="request_transport_error",
            )
        ],
        health_ok=False,
    )
    coordinator = _coordinator(tmp_path, bhce)
    first = asyncio.run(coordinator.execute("MATCH (u:User {name: 'A@TEST.LOCAL'}) RETURN u"))
    second = coordinator.skipped_result()

    assert first.failure_type == "transport_error"
    assert first.circuit_state == "open"
    assert coordinator.circuit_open is True
    assert second.failure_type == "circuit_open"
    assert second.query_executed is False
    assert len(bhce.queries) == 1


def test_transient_infra_failure_does_not_poison_shared_query_cache(tmp_path) -> None:
    bhce = FakeBHCE(
        [
            CypherResult(
                success=False,
                error="Request failed: server disconnected",
                failure_type="transport_error",
                failure_subtype="request_transport_error",
            ),
            CypherResult(success=True, nodes=[], node_names=set()),
        ],
        health_ok=False,
    )
    coordinator = _coordinator(tmp_path, bhce)
    query = "MATCH (u:User {name: 'A@TEST.LOCAL'}) RETURN u"

    first = asyncio.run(coordinator.execute(query))
    coordinator.close_circuit()
    second = asyncio.run(coordinator.execute(query))

    assert first.failure_type == "transport_error"
    assert second.success is True
    assert len(bhce.queries) == 2
    cache = json.loads((tmp_path / "deny.json").read_text())
    assert cache["entries"] == {}
