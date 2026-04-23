from __future__ import annotations

import asyncio

from ori.eval.bhce import BHCEClient, CypherResult


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
