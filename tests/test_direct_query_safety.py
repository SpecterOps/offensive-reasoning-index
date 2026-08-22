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
        "MATCH (u:User {hasspn: true}) RETURN u",
        "MATCH (u:User) WHERE u.hasspn = true RETURN u",
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
