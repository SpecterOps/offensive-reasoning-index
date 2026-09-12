from __future__ import annotations

import asyncio
import json

import httpx

from ori.eval.bhce import BHCEClient, CypherResult


def test_native_ce_effective_connection_and_port_routing(monkeypatch):
    import httpx

    from ori.eval.bhce import BHCEClient
    from ori.eval.v2.native_ce_runtime import native_ce_connection

    original = httpx.AsyncClient
    observed = []

    def client(**kwargs):
        assert kwargs["trust_env"] is False
        return original(**kwargs, transport=httpx.MockTransport(lambda request: (
            observed.append(str(request.url)),
            httpx.Response(200, json={"data": {"nodes": {}, "edges": []}}),
        )[1]))

    monkeypatch.setattr(httpx, "AsyncClient", client)
    monkeypatch.setenv("HTTPS_PROXY", "http://unrelated.invalid:9999")
    for scheme, port in (("https", "443"), ("http", "80"), ("https", "80"), ("http", "443")):
        child, options, binding = native_ce_connection({
            "BLOODHOUND_DOMAIN": "Fixture.Invalid", "BLOODHOUND_TOKEN_ID": "fixture-id",
            "BLOODHOUND_TOKEN_KEY": "fixture-key", "BLOODHOUND_SCHEME": scheme,
            "BLOODHOUND_PORT": port,
        })
        assert child["BLOODHOUND_DOMAIN"] == options["domain"] == binding["domain"]
        assert int(child["BLOODHOUND_PORT"]) == options["port"] == binding["port"]
        assert "fixture-id" not in str(binding) and "fixture-key" not in str(binding)

        async def query():
            async with BHCEClient(**options) as bhce:
                assert (await bhce.check_health()).ok

        asyncio.run(query())
        suffix = "" if (scheme, port) in {("https", "443"), ("http", "80")} else f":{port}"
        assert observed[-1] == f"{scheme}://fixture.invalid{suffix}/api/v2/graphs/cypher"


def test_classify_error_infra() -> None:
    assert BHCEClient.classify_error("HTTP 502: Bad Gateway") == "infra"
    assert BHCEClient.classify_error("Request failed: timed out") == "infra"


def test_classify_error_query() -> None:
    assert BHCEClient.classify_error("Cypher syntax error: bad query") == "query"
    assert BHCEClient.classify_error("HTTP 400: no viable alternative") == "query"
    assert BHCEClient.classify_error("HTTP 404: { resource not found}") == "query"
    assert (
        BHCEClient.classify_error("HTTP 500: Neo4jError: Neo.ClientError.Statement.SyntaxError")
        == "query"
    )


class FakeHealthClient(BHCEClient):
    def __init__(self, results):
        self._results = list(results)

    async def run_cypher(self, query: str) -> CypherResult:
        return self._results.pop(0)


def test_wait_until_healthy_succeeds_after_retry() -> None:
    client = FakeHealthClient(
        [
            CypherResult(success=False, error="HTTP 502: Bad Gateway"),
            CypherResult(success=True, nodes=[], node_names=set(), raw={}),
        ]
    )
    result = asyncio.run(client.wait_until_healthy(timeout_seconds=0.1, poll_interval=0.01))
    assert result.ok is True


def test_run_cypher_resilient_returns_query_error_without_retry() -> None:
    client = FakeHealthClient([CypherResult(success=False, error="HTTP 400: bad query")])
    result = asyncio.run(client.run_cypher_resilient("MATCH (n) RETURN n"))
    assert result.success is False
    assert result.error == "HTTP 400: bad query"


def test_run_cypher_resilient_recovers_one_transient_timeout() -> None:
    client = FakeHealthClient(
        [
            CypherResult(
                success=False,
                error="Client timed out waiting for BloodHound",
                failure_type="client_timeout",
            ),
            CypherResult(success=True),
            CypherResult(success=True),
        ]
    )

    result = asyncio.run(
        client.run_cypher_resilient(
            "MATCH (n) RETURN n",
            recovery_timeout_seconds=0.1,
            recovery_poll_interval=0.01,
        )
    )

    assert result.success is True
    assert result.execution_attempts == 2


def test_run_cypher_resilient_uses_structured_server_failure_type() -> None:
    client = FakeHealthClient(
        [
            CypherResult(
                success=False,
                error="opaque upstream failure",
                failure_type="server_error",
            ),
            CypherResult(success=True),
            CypherResult(success=True),
        ]
    )

    result = asyncio.run(
        client.run_cypher_resilient(
            "MATCH (n) RETURN n",
            recovery_timeout_seconds=0.1,
            recovery_poll_interval=0.01,
        )
    )

    assert result.success is True
    assert result.execution_attempts == 2


def test_connect_failure_does_not_claim_query_execution() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("DNS lookup failed", request=request)

    client = BHCEClient(
        domain="bloodhound.test",
        token_id="token-id",
        token_key="token-key",
    )
    asyncio.run(client._client.aclose())
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    result = asyncio.run(client.run_cypher("MATCH (n:Domain) RETURN n LIMIT 1"))
    asyncio.run(client.close())

    assert result.failure_type == "transport_error"
    assert result.query_executed is False


def test_get_all_node_names_includes_adcs_labels() -> None:
    seen_queries: list[str] = []

    class FakeInventoryClient(BHCEClient):
        def __init__(self):
            pass

        async def run_cypher(self, query: str) -> CypherResult:
            seen_queries.append(query)
            label = query.split("MATCH (n:", 1)[1].split(")", 1)[0]
            return CypherResult(success=True, node_names={f"{label.upper()}-NODE@CORP.LOCAL"})

    names = asyncio.run(FakeInventoryClient().get_all_node_names())

    assert "ENTERPRISECA-NODE@CORP.LOCAL" in names
    assert "CERTTEMPLATE-NODE@CORP.LOCAL" in names
    assert any("MATCH (n:EnterpriseCA)" in query for query in seen_queries)
    assert any("MATCH (n:CertTemplate)" in query for query in seen_queries)


def test_run_cypher_uses_official_prefer_wait_header_and_payload() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        captured["payload"] = json.loads(request.content)
        return httpx.Response(200, json={"data": {"nodes": {}, "edges": []}})

    client = BHCEClient(
        domain="bloodhound.test",
        token_id="token-id",
        token_key="token-key",
    )
    asyncio.run(client._client.aclose())
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    result = asyncio.run(
        client.run_cypher(
            "MATCH (n:Domain) RETURN n LIMIT 1",
            server_timeout_seconds=10,
            client_timeout_seconds=15,
        )
    )
    asyncio.run(client.close())

    assert result.success is True
    headers = captured["headers"]
    assert isinstance(headers, httpx.Headers)
    assert headers["Prefer"] == "wait=10"
    assert captured["payload"] == {
        "query": "MATCH (n:Domain) RETURN n LIMIT 1",
        "include_properties": True,
    }


def test_run_cypher_classifies_bloodhound_timeout_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="query timeout", request=request)

    client = BHCEClient(
        domain="bloodhound.test",
        token_id="token-id",
        token_key="token-key",
    )
    asyncio.run(client._client.aclose())
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    result = asyncio.run(
        client.run_cypher(
            "MATCH (n:Domain) RETURN n LIMIT 1",
            server_timeout_seconds=10,
            client_timeout_seconds=15,
        )
    )
    asyncio.run(client.close())

    assert result.success is False
    assert result.failure_type == "query_timeout"
    assert result.failure_subtype == "bloodhound_query_timeout"
    assert result.status_code == 500


def test_run_cypher_classifies_bloodhound_complexity_rejection() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "http_status": 400,
                "errors": [
                    {
                        "message": (
                            "cypher query is too complex and is likely to result in "
                            "poor or unstable database performance"
                        )
                    }
                ],
            },
            request=request,
        )

    client = BHCEClient(
        domain="bloodhound.test",
        token_id="token-id",
        token_key="token-key",
    )
    asyncio.run(client._client.aclose())
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    result = asyncio.run(client.run_cypher("MATCH p=(s)-[*1..8]->(t) RETURN p"))
    asyncio.run(client.close())

    assert result.success is False
    assert result.failure_type == "query_timeout"
    assert result.failure_subtype == "bloodhound_query_too_complex"
    assert result.status_code == 400


def test_run_cypher_keeps_ordinary_http_400_in_syntax_bucket() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            text="line 1:7 no viable alternative at input 'MATCH (start'",
            request=request,
        )

    client = BHCEClient(
        domain="bloodhound.test",
        token_id="token-id",
        token_key="token-key",
    )
    asyncio.run(client._client.aclose())
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    result = asyncio.run(client.run_cypher("MATCH (start:User) RETURN start"))
    asyncio.run(client.close())

    assert result.success is False
    assert result.failure_type == "query_error"
    assert result.failure_subtype == "cysql_syntax_error"
    assert result.status_code == 400


def test_run_cypher_keeps_gateway_timeout_in_infrastructure_bucket() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(504, text="gateway timeout", request=request)

    client = BHCEClient(
        domain="bloodhound.test",
        token_id="token-id",
        token_key="token-key",
    )
    asyncio.run(client._client.aclose())
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    result = asyncio.run(
        client.run_cypher(
            "MATCH (n:Domain) RETURN n LIMIT 1",
            server_timeout_seconds=10,
            client_timeout_seconds=15,
        )
    )
    asyncio.run(client.close())

    assert result.success is False
    assert result.failure_type == "server_unavailable"
    assert result.failure_subtype == "bloodhound_server_unavailable"
    assert result.status_code == 504


def test_run_cypher_keeps_known_500_cypher_error_in_query_bucket() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            text="Neo4jError: Neo.ClientError.Statement.SyntaxError",
            request=request,
        )

    client = BHCEClient(
        domain="bloodhound.test",
        token_id="token-id",
        token_key="token-key",
    )
    asyncio.run(client._client.aclose())
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    result = asyncio.run(client.run_cypher("MATCH broken"))
    asyncio.run(client.close())

    assert result.success is False
    assert result.failure_type == "query_error"
    assert result.failure_subtype == "bloodhound_query_error"
    assert result.status_code == 500


def test_run_cypher_classifies_client_transport_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("client wait expired", request=request)

    client = BHCEClient(
        domain="bloodhound.test",
        token_id="token-id",
        token_key="token-key",
    )
    asyncio.run(client._client.aclose())
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    result = asyncio.run(
        client.run_cypher(
            "MATCH (n:Domain) RETURN n LIMIT 1",
            server_timeout_seconds=10,
            client_timeout_seconds=15,
        )
    )
    asyncio.run(client.close())

    assert result.success is False
    assert result.failure_type == "client_timeout"
    assert result.failure_subtype == "client_transport_timeout"
