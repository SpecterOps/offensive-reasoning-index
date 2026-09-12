"""Minimal BloodHound CE API client (HMAC auth + Cypher execution)."""

from __future__ import annotations

import asyncio
import base64
import datetime
import hashlib
import hmac
import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx


@dataclass
class CypherResult:
    success: bool
    nodes: list[dict] = field(default_factory=list)
    node_names: set[str] = field(default_factory=set)
    error: str | None = None
    raw: dict = field(default_factory=dict)
    failure_type: str | None = None
    failure_subtype: str = ""
    status_code: int | None = None
    query_executed: bool = True
    execution_attempts: int = 1
    query_fingerprint: str = ""
    safety_policy_version: str = ""
    safety_rule: str = ""
    bhce_health_after: str = ""
    circuit_state: str = "closed"


@dataclass
class BHHealthResult:
    ok: bool
    detail: str
    query: str
    status_code: int | None = None
    classification: str = "ok"


def _env_bool(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value when set.")


def parse_bhce_url(bhce_url: str | None) -> dict[str, str | int]:
    """Resolve explicit non-secret BHCE connection settings from a URL."""
    if not bhce_url:
        return {}
    parsed = urlparse(bhce_url)
    kwargs: dict[str, str | int] = {}
    if parsed.hostname:
        kwargs["domain"] = parsed.hostname
    if parsed.scheme:
        kwargs["scheme"] = parsed.scheme
    if parsed.port is not None:
        kwargs["port"] = parsed.port
    return kwargs


def resolve_bhce_target(bhce_url: str | None) -> dict[str, str | int]:
    """Return the effective non-secret BloodHound target used by the client."""

    explicit = parse_bhce_url(bhce_url)
    return {
        "scheme": str(explicit.get("scheme", "https")).lower(),
        "domain": str(explicit.get("domain") or os.environ.get("BLOODHOUND_DOMAIN", "")).lower(),
        "port": int(explicit.get("port", 443)),
    }


class BHCEClient:
    """
    Async BloodHound CE client.

    Auth uses HMAC-SHA256 signatures matching the bloodhound_mcp pattern.
    Reads credentials from env vars: BLOODHOUND_DOMAIN, BLOODHOUND_TOKEN_ID,
    BLOODHOUND_TOKEN_KEY. These are the same vars used by bloodhound_mcp.
    """

    def __init__(
        self,
        domain: str | None = None,
        token_id: str | None = None,
        token_key: str | None = None,
        scheme: str = "https",
        port: int = 443,
        verify_tls: bool | None = None,
        trust_env: bool = True,
    ) -> None:
        self.domain = domain or os.environ["BLOODHOUND_DOMAIN"]
        self.token_id = token_id or os.environ["BLOODHOUND_TOKEN_ID"]
        self.token_key = token_key or os.environ["BLOODHOUND_TOKEN_KEY"]
        self.scheme = scheme or os.getenv("BLOODHOUND_SCHEME", "https")
        self.port = port or int(os.getenv("BLOODHOUND_PORT", "443"))
        self.verify_tls = (
            verify_tls
            if verify_tls is not None
            else _env_bool("BLOODHOUND_VERIFY_TLS", default=True)
        )
        self.trust_env = trust_env
        self._client = httpx.AsyncClient(timeout=30.0, verify=self.verify_tls, trust_env=trust_env)

    def _sign(self, method: str, path: str, body: bytes = b"") -> dict:
        """
        Build HMAC-signed request headers using BH CE's chained HMAC-SHA256 scheme.

        Chain 1: HMAC(token_key, method+path)
        Chain 2: HMAC(chain1_digest, datetime_truncated_to_hour)
        Chain 3: HMAC(chain2_digest, body)
        Signature: base64(chain3_digest)
        """
        datetime_formatted = datetime.datetime.now().astimezone().isoformat("T")

        # Chain 1: sign method + URI
        d = hmac.new(self.token_key.encode(), None, hashlib.sha256)
        d.update(f"{method}{path}".encode())

        # Chain 2: sign datetime truncated to hour (e.g. "2026-03-28T00")
        d = hmac.new(d.digest(), None, hashlib.sha256)
        d.update(datetime_formatted[:13].encode())

        # Chain 3: sign body
        d = hmac.new(d.digest(), None, hashlib.sha256)
        if body:
            d.update(body)

        return {
            "Authorization": f"bhesignature {self.token_id}",
            "RequestDate": datetime_formatted,
            "Signature": base64.b64encode(d.digest()).decode(),
            "Content-Type": "application/json",
        }

    def _url(self, path: str) -> str:
        base = f"{self.scheme}://{self.domain}"
        if self.port != (443 if self.scheme == "https" else 80):
            base += f":{self.port}"
        return base + path

    @staticmethod
    def classify_error(error: str | None) -> str:
        """Classify BHCE failures as infra, query, or unknown."""
        if not error:
            return "ok"
        text = error.lower()

        infra_markers = (
            "502 bad gateway",
            "503",
            "504",
            "gateway",
            "connection refused",
            "timed out",
            "timeout",
            "temporarily unavailable",
            "request failed:",
            "server disconnected",
            "name or service not known",
            "nodename nor servname provided",
            "tls",
            "certificate verify failed",
            "internal error has occurred that is preventing the service from servicing this request",  # noqa: E501
        )
        if any(marker in text for marker in infra_markers):
            return "infra"

        query_markers = (
            "cypher syntax error",
            "neo.clienterror",
            "syntaxerror",
            "variable `p` not defined",
            "no viable alternative",
            "mismatched input",
            "extraneous input",
            "token recognition error",
            "resource not found",
            "http 400",
            "http 404",
        )
        if any(marker in text for marker in query_markers):
            return "query"

        if "http 500" in text and ("neo4jerror" in text or "syntax" in text):
            return "query"
        if "http 500" in text:
            return "unknown"

        return "unknown"

    @staticmethod
    def _normalize_cypher(query: str) -> str:
        """
        Normalize Cypher for BH CE's CySQL parser.

        CySQL rejects whitespace (newlines or spaces) immediately after shortestPath(
        and other function-call open-parens. Collapse all whitespace to single spaces
        then remove spaces after opening parentheses so the pattern starts immediately.
        """
        # Collapse all newlines and runs of whitespace to a single space
        q = " ".join(query.split())
        # Remove spaces after opening parens — CySQL requires pattern to start right after (
        q = re.sub(r"\(\s+", "(", q)
        # Remove spaces before closing parens — CySQL rejects ' )' at end of shortestPath(...)
        q = re.sub(r"\s+\)", ")", q)
        return q.strip()

    @staticmethod
    def _is_query_complexity_rejection(response_text: str) -> bool:
        text = response_text.lower()
        complexity_markers = (
            "query is too complex",
            "query too complex",
            "poor or unstable database performance",
        )
        return any(marker in text for marker in complexity_markers)

    @staticmethod
    def _http_failure_type(status_code: int, response_text: str) -> str:
        text = response_text.lower()
        if status_code in {401, 403}:
            return "auth_error"
        if status_code == 429:
            return "rate_limited"
        if status_code in {408, 502, 503, 504}:
            return "server_unavailable"
        if BHCEClient._is_query_complexity_rejection(response_text):
            return "query_timeout"
        query_markers = (
            "cypher syntax error",
            "neo.clienterror",
            "syntaxerror",
            "no viable alternative",
            "mismatched input",
            "extraneous input",
            "token recognition error",
        )
        if any(marker in text for marker in query_markers):
            return "query_error"
        timeout_markers = (
            "query timeout",
            "query timed out",
            "timed out",
            "timeout",
            "deadline exceeded",
            "statement timeout",
            "operation was canceled",
            "operation was cancelled",
        )
        if any(marker in text for marker in timeout_markers):
            return "query_timeout"
        if status_code >= 500:
            return "server_error"
        return "query_error"

    async def run_cypher(
        self,
        query: str,
        *,
        include_properties: bool = True,
        server_timeout_seconds: float | None = None,
        client_timeout_seconds: float | None = None,
    ) -> CypherResult:
        """Execute a Cypher query against BH CE and return normalized result."""
        import json

        path = "/api/v2/graphs/cypher"
        query = self._normalize_cypher(query)
        body = json.dumps(
            {
                "query": query,
                "include_properties": include_properties,
            }
        ).encode()
        headers = self._sign("POST", path, body)
        if server_timeout_seconds is not None:
            wait_seconds = max(1, int(server_timeout_seconds))
            headers["Prefer"] = f"wait={wait_seconds}"

        try:
            request_kwargs = {
                "content": body,
                "headers": headers,
            }
            if client_timeout_seconds is not None:
                request_kwargs["timeout"] = client_timeout_seconds
            resp = await self._client.post(self._url(path), **request_kwargs)
        except httpx.TimeoutException as e:
            return CypherResult(
                success=False,
                error=f"Client timed out waiting for BloodHound: {e}",
                failure_type="client_timeout",
                failure_subtype="client_transport_timeout",
                query_executed=True,
                execution_attempts=1,
            )
        except httpx.RequestError as e:
            request_may_have_reached_server = not isinstance(
                e,
                (
                    httpx.ConnectError,
                    httpx.ConnectTimeout,
                    httpx.PoolTimeout,
                ),
            )
            return CypherResult(
                success=False,
                error=f"Request failed: {e}",
                failure_type="transport_error",
                failure_subtype="request_transport_error",
                query_executed=request_may_have_reached_server,
                execution_attempts=1,
            )

        if resp.status_code == 404:
            # BH CE returns 404 for queries that return no results
            return CypherResult(
                success=True,
                nodes=[],
                node_names=set(),
                raw={},
                status_code=resp.status_code,
            )

        if resp.status_code == 400:
            if self._is_query_complexity_rejection(resp.text):
                return CypherResult(
                    success=False,
                    error=f"HTTP 400: {resp.text}",
                    failure_type="query_timeout",
                    failure_subtype="bloodhound_query_too_complex",
                    status_code=resp.status_code,
                )
            return CypherResult(
                success=False,
                error=f"Cypher syntax error: {resp.text}",
                failure_type="query_error",
                failure_subtype="cysql_syntax_error",
                status_code=resp.status_code,
            )

        if resp.status_code not in (200, 201):
            failure_type = self._http_failure_type(resp.status_code, resp.text)
            return CypherResult(
                success=False,
                error=f"HTTP {resp.status_code}: {resp.text}",
                failure_type=failure_type,
                failure_subtype=f"bloodhound_{failure_type}",
                status_code=resp.status_code,
            )

        try:
            data = resp.json()
        except Exception as e:
            return CypherResult(
                success=False,
                error=f"JSON parse error: {e}",
                failure_type="response_error",
                failure_subtype="invalid_json_response",
                status_code=resp.status_code,
            )

        nodes = _extract_nodes(data)
        names = _extract_node_names(nodes)
        return CypherResult(
            success=True,
            nodes=nodes,
            node_names=names,
            raw=data,
            status_code=resp.status_code,
        )

    async def wait_until_healthy(
        self,
        timeout_seconds: float = 90.0,
        poll_interval: float = 5.0,
        query: str = "MATCH (n:Domain) RETURN n LIMIT 1",
    ) -> BHHealthResult:
        """Poll BHCE until a simple query succeeds or timeout is reached."""
        deadline = asyncio.get_event_loop().time() + timeout_seconds
        last = BHHealthResult(ok=False, detail="not started", query=query, classification="unknown")
        while True:
            health = await self.check_health(query=query)
            if health.ok:
                return health
            last = health
            if asyncio.get_event_loop().time() >= deadline:
                return last
            await asyncio.sleep(poll_interval)

    async def run_cypher_resilient(
        self,
        query: str,
        *,
        recovery_timeout_seconds: float = 90.0,
        recovery_poll_interval: float = 5.0,
    ) -> CypherResult:
        """Run Cypher and retry once after waiting if the failure looks infra-related."""
        result = await self.run_cypher(query)
        if result.success:
            return result
        classification = (
            "infra"
            if result.failure_type
            in {
                "auth_error",
                "client_timeout",
                "rate_limited",
                "response_error",
                "server_error",
                "server_unavailable",
                "transport_error",
            }
            else self.classify_error(result.error)
        )
        if classification != "infra":
            return result

        health = await self.wait_until_healthy(
            timeout_seconds=recovery_timeout_seconds,
            poll_interval=recovery_poll_interval,
        )
        if not health.ok:
            return CypherResult(
                success=False,
                error=(
                    f"BHCE unavailable after recovery wait: {result.error} "
                    f"(last health check: {health.detail})"
                ),
                failure_type="server_unavailable",
                failure_subtype="recovery_wait_exhausted",
                query_executed=result.query_executed,
                execution_attempts=result.execution_attempts,
            )
        retry = await self.run_cypher(query)
        retry.execution_attempts = result.execution_attempts + retry.execution_attempts
        return retry

    async def check_health(
        self,
        query: str = "MATCH (n:Domain) RETURN n LIMIT 1",
    ) -> BHHealthResult:
        """Check whether BHCE is reachable and can execute a safe query."""
        result = await self.run_cypher(query)
        if result.success:
            return BHHealthResult(
                ok=True, detail="query succeeded", query=query, classification="ok"
            )
        classification = self.classify_error(result.error)
        return BHHealthResult(
            ok=False,
            detail=result.error or "unknown error",
            query=query,
            classification=classification,
        )

    async def get_all_node_names(self) -> set[str]:
        """
        Fetch all node names from BH CE for hallucination detection.

        CySQL doesn't support unlabeled MATCH (n) — query each type separately.
        Fails closed: if any query fails, returns empty set to disable hallucination
        checking rather than building a partial allowlist that would cause false positives.

        The allowlist must be broad enough to cover the full benchmark fixture.
        A small per-label cap creates false positives: reference/mock-perfect
        Cypher can mention valid but later-page users such as ADUDLEY@CORP.LOCAL
        or CDAVIS@CORP.LOCAL, which should never be scored as hallucinations.
        """
        names: set[str] = set()
        # Include every graph node label that benchmark answers may legitimately
        # cite. ADCS tasks return EnterpriseCA/RootCA/CertTemplate names and
        # sometimes object IDs/SIDs through BloodHound's ADCS node shapes; if
        # the smoke/mock-perfect allowlist omits those labels, the scorer turns
        # valid reference answers into false HALLUCINATION failures.
        labels = (
            "User",
            "Computer",
            "Group",
            "Domain",
            "OU",
            "RootCA",
            "EnterpriseCA",
            "CertTemplate",
            "AIACA",
            "NTAuthStore",
        )
        for label in labels:
            result = await self.run_cypher(f"MATCH (n:{label}) RETURN n LIMIT 10000")
            if not result.success:
                # Fail closed — partial allowlist is worse than no allowlist
                return set()
            names |= result.node_names
            names |= _extract_node_identifiers(result.nodes)
        return names

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> BHCEClient:
        return self

    async def __aexit__(self, *_) -> None:
        await self.close()


def _extract_nodes(data: dict) -> list[dict]:
    """Pull node objects out of BH CE's graph/cypher response."""
    nodes: list[dict] = []
    # BH CE returns {"data": {"nodes": {...}, "edges": [...]}} or similar
    if isinstance(data, dict):
        inner = data.get("data", data)
        if isinstance(inner, dict):
            raw_nodes = inner.get("nodes", {})
            if isinstance(raw_nodes, dict):
                nodes = list(raw_nodes.values())
            elif isinstance(raw_nodes, list):
                nodes = raw_nodes
        elif isinstance(inner, list):
            # Some queries return a list directly
            nodes = inner
    return nodes


def _extract_node_identifiers(nodes: list[dict]) -> set[str]:
    """Extract non-name object identifiers for hallucination allowlists only.

    These identifiers are useful for deciding whether a model mentioned a real
    graph object, but they must not be mixed into CypherResult.node_names. Strict
    answer grading compares final answer node_names to reference node_names; if
    object IDs are added there, MCP final answers that correctly cite labels only
    are incorrectly marked incomplete.
    """
    identifiers: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict):
            continue
        raw_props = node.get("properties")
        props = raw_props if isinstance(raw_props, dict) else {}
        for key in ("objectId", "objectid", "ObjectIdentifier", "objectidentifier"):
            value = node.get(key) or props.get(key)
            if value:
                identifiers.add(str(value))
    return identifiers


def _extract_node_names(nodes: list[dict]) -> set[str]:
    """
    Extract name strings from BH CE node objects.

    BH CE returns nodes in two formats depending on version:
      New (v1.9+): {"label": "NAME@DOMAIN", "kind": "User", "objectId": "...", ...}
      Old:         {"properties": {"name": "NAME@DOMAIN", ...}, ...}
    Check both.
    """
    names: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict):
            continue
        # New format: name is in "label"
        name = node.get("label")
        if not name:
            # Old format: name is nested under "properties"
            props = node.get("properties", {})
            name = props.get("name") or props.get("Name")
        if name:
            names.add(str(name))
    return names
