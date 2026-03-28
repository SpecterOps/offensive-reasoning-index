"""Minimal BloodHound CE API client (HMAC auth + Cypher execution)."""

from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import os
import re
from dataclasses import dataclass, field

import httpx


@dataclass
class CypherResult:
    success: bool
    nodes: list[dict] = field(default_factory=list)
    node_names: set[str] = field(default_factory=set)
    error: str | None = None
    raw: dict = field(default_factory=dict)


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
    ) -> None:
        self.domain = domain or os.environ["BLOODHOUND_DOMAIN"]
        self.token_id = token_id or os.environ["BLOODHOUND_TOKEN_ID"]
        self.token_key = token_key or os.environ["BLOODHOUND_TOKEN_KEY"]
        self.scheme = scheme or os.getenv("BLOODHOUND_SCHEME", "https")
        self.port = port or int(os.getenv("BLOODHOUND_PORT", "443"))
        self._client = httpx.AsyncClient(timeout=30.0)

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
        if self.port not in (80, 443):
            base += f":{self.port}"
        return base + path

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

    async def run_cypher(self, query: str) -> CypherResult:
        """Execute a Cypher query against BH CE and return normalized result."""
        import json
        path = "/api/v2/graphs/cypher"
        query = self._normalize_cypher(query)
        body = json.dumps({"query": query, "includeproperties": True}).encode()
        headers = self._sign("POST", path, body)

        try:
            resp = await self._client.post(self._url(path), content=body, headers=headers)
        except httpx.RequestError as e:
            return CypherResult(success=False, error=f"Request failed: {e}")

        if resp.status_code == 404:
            # BH CE returns 404 for queries that return no results
            return CypherResult(success=True, nodes=[], node_names=set(), raw={})

        if resp.status_code == 400:
            return CypherResult(success=False, error=f"Cypher syntax error: {resp.text}")

        if resp.status_code not in (200, 201):
            return CypherResult(success=False, error=f"HTTP {resp.status_code}: {resp.text}")

        try:
            data = resp.json()
        except Exception as e:
            return CypherResult(success=False, error=f"JSON parse error: {e}")

        nodes = _extract_nodes(data)
        names = _extract_node_names(nodes)
        return CypherResult(success=True, nodes=nodes, node_names=names, raw=data)

    async def get_all_node_names(self) -> set[str]:
        """
        Fetch all node names from BH CE for hallucination detection.

        CySQL doesn't support unlabeled MATCH (n) — query each type separately.
        Fails closed: if any query fails, returns empty set to disable hallucination
        checking rather than building a partial allowlist that would cause false positives.
        """
        names: set[str] = set()
        for label in ("User", "Computer", "Group", "Domain", "OU"):
            result = await self.run_cypher(f"MATCH (n:{label}) RETURN n LIMIT 300")
            if not result.success:
                # Fail closed — partial allowlist is worse than no allowlist
                return set()
            names |= result.node_names
        return names

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "BHCEClient":
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
