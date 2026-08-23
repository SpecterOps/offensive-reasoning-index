from __future__ import annotations

import asyncio
import re

import pytest

from ori.eval.bhce import CypherResult
from ori.eval.v2.graph import (
    GraphSnapshot,
    _scalar_properties,
    build_archive_snapshot,
    collect_live_snapshot,
    entity_property_fact_key,
    require_live_graph_match,
)
from ori.eval.v2.schema import EdgeWitness
from ori.generator.archive_validation import _relationships_from_archive
from ori.generator.graph import ADGraph
from ori.generator.org import build_org
from ori.generator.security import apply_baseline_security
from ori.generator.serializer import _build_zip, project_nodes_for_sharphound


def _archive_and_manifest(
    *,
    seed: int = 1234,
) -> tuple[bytes, dict[str, object]]:
    graph = ADGraph("EXAMPLE.LOCAL", seed=seed)
    build_org(graph, num_users=8, num_workstations=3, num_servers=1)
    apply_baseline_security(graph)
    archive = _build_zip(graph)
    manifest: dict[str, object] = {
        "schema_version": "ori-generated-manifest-v2",
        "seed": seed,
        "domain": graph.domain,
        "domain_sid": graph.domain_sid,
        "metadata": {"benchmark_name": "simple"},
        "stats": {"total_nodes": len(project_nodes_for_sharphound(graph))},
        "relationship_summary": {
            "total_relationships": len(_relationships_from_archive(archive))
        },
    }
    return archive, manifest


def test_archive_snapshot_is_deterministic_and_resolves_aliases() -> None:
    archive, manifest = _archive_and_manifest()
    first = build_archive_snapshot(archive, manifest)
    second = build_archive_snapshot(archive, manifest)

    assert first == second
    assert first.graph_fingerprint == second.graph_fingerprint
    archive_nodes = manifest["stats"]["total_nodes"]  # type: ignore[index]
    computer_count = sum(
        entity.object_type == "Computer" for entity in first.entities
    )
    assert len(first.objects) == archive_nodes + (computer_count * 4)
    user = next(entity for entity in first.entities if entity.object_type == "User")
    assert user.object_id in first.entity(user.object_id).object_id
    assert user.canonical_name
    assert user.aliases


def test_archive_snapshot_includes_ce_local_group_identity_and_edges() -> None:
    archive, manifest = _archive_and_manifest()
    snapshot = build_archive_snapshot(archive, manifest)
    computer = next(
        entity for entity in snapshot.entities if entity.object_type == "Computer"
    )
    local_group_ids = {
        f"{computer.object_id}-{rid}" for rid in ("544", "555", "562", "580")
    }

    assert local_group_ids <= {
        entity.object_id
        for entity in snapshot.entities
        if entity.object_type == "ADLocalGroup"
    }
    assert {
        (local_group_id, "LocalToComputer", computer.object_id)
        for local_group_id in local_group_ids
    } <= snapshot.edge_keys


def test_archive_snapshot_seed_change_changes_graph_identity() -> None:
    archive_a, manifest_a = _archive_and_manifest(seed=1234)
    archive_b, manifest_b = _archive_and_manifest(seed=5678)

    first = build_archive_snapshot(archive_a, manifest_a)
    changed = build_archive_snapshot(archive_b, manifest_b)

    assert first.graph_fingerprint != changed.graph_fingerprint


def test_graph_fact_projection_keeps_stable_context_and_list_membership() -> None:
    facts = _scalar_properties(
        {
            "enabled": True,
            "admincount": False,
            "operatingsystem": "Windows 10 Enterprise",
            "ekus": ["Client Authentication", "Smart Card Logon"],
            "lastseen": "normalized-away",
            "system_tags": "admin_tier_0",
        }
    )

    assert {(fact.key.casefold(), fact.value) for fact in facts} == {
        ("admincount", False),
        ("ekus", "client authentication"),
        ("ekus", "smart card logon"),
        ("enabled", True),
        ("operatingsystem", "windows 10 enterprise"),
    }
    assert entity_property_fact_key(
        "COMPUTER-A",
        "operatingsystem",
        "Windows 10 Enterprise",
    ) == entity_property_fact_key(
        "computer-a",
        "OperatingSystem",
        "WINDOWS 10 ENTERPRISE",
    )


def test_v11_graph_schema_rejects_pre_local_group_snapshots() -> None:
    archive, manifest = _archive_and_manifest()
    snapshot = build_archive_snapshot(archive, manifest)
    legacy = snapshot.model_dump(mode="python")
    legacy["schema_version"] = "ori-graph-snapshot-v1"

    with pytest.raises(ValueError, match="schema_version"):
        GraphSnapshot.model_validate(legacy)


def test_archive_snapshot_detects_manifest_count_mismatch() -> None:
    archive, manifest = _archive_and_manifest()
    manifest["stats"] = {"total_nodes": 1}

    try:
        build_archive_snapshot(archive, manifest)
    except ValueError as exc:
        assert "does not match manifest" in str(exc)
    else:
        raise AssertionError("graph count mismatch was accepted")


class FakeLiveBHCE:
    def __init__(
        self,
        snapshot: GraphSnapshot,
        *,
        extra_edges: tuple[EdgeWitness, ...] = (),
    ) -> None:
        self.snapshot = snapshot
        self.edges = (*snapshot.relationships, *extra_edges)

    @staticmethod
    def _page(query: str) -> tuple[int, int]:
        match = re.search(r"SKIP (\d+) LIMIT (\d+)", query)
        if not match:
            return (0, 1)
        return int(match.group(1)), int(match.group(2))

    def _node(self, object_id: str) -> dict:
        item = next(
            value
            for value in self.snapshot.objects
            if value.entity.object_id == object_id
        )
        properties = {
            fact.key: fact.value for fact in item.properties
        }
        properties.update(
            {
                "objectid": item.entity.object_id,
                "domain": item.entity.domain,
                "lastseen": "normalized-away",
            }
        )
        if item.entity.object_type != "ADLocalGroup":
            properties["name"] = item.entity.canonical_name
        return {
            "objectId": item.entity.object_id,
            "label": item.entity.canonical_name,
            "kind": item.entity.object_type,
            "properties": properties,
        }

    async def run_cypher(self, query: str) -> CypherResult:
        node_count = re.fullmatch(
            r"MATCH \(n:(\w+)\) RETURN count\(n\) AS count LIMIT 1",
            query,
        )
        if node_count:
            object_type = node_count.group(1)
            count = sum(
                item.entity.object_type == object_type
                for item in self.snapshot.objects
            )
            return CypherResult(
                success=True,
                raw={
                    "data": {
                        "nodes": {},
                        "edges": [],
                        "literals": [{"key": "count", "value": count}],
                    }
                },
            )

        node_page = re.fullmatch(
            r"MATCH \(n:(\w+)\) RETURN n ORDER BY n.objectid "
            r"SKIP \d+ LIMIT \d+",
            query,
        )
        if node_page:
            object_type = node_page.group(1)
            offset, limit = self._page(query)
            items = sorted(
                (
                    item
                    for item in self.snapshot.objects
                    if item.entity.object_type == object_type
                ),
                key=lambda item: item.entity.object_id,
            )[offset : offset + limit]
            nodes = {
                str(index): self._node(item.entity.object_id)
                for index, item in enumerate(items)
            }
            return CypherResult(success=True, raw={"data": {"nodes": nodes, "edges": []}})

        relationship = re.fullmatch(
            r"MATCH (.+) RETURN (count\(r\) AS count LIMIT 1|"
            r"a, r, b ORDER BY .+ SKIP \d+ LIMIT \d+)",
            query,
        )
        if not relationship:
            return CypherResult(success=False, error=f"unexpected query: {query}")
        pattern, projection = relationship.groups()
        outgoing = re.fullmatch(r"\(a:(\w+)\)-\[r\]->\(b\)", pattern)
        incoming = re.fullmatch(r"\(a\)-\[r\]->\(b:(\w+)\)", pattern)
        object_type = (outgoing or incoming).group(1)  # type: ignore[union-attr]
        type_by_id = {
            item.entity.object_id: item.entity.object_type
            for item in self.snapshot.objects
        }
        edges = [
            edge
            for edge in self.edges
            if (
                type_by_id[edge.source_id] == object_type
                if outgoing
                else type_by_id[edge.target_id] == object_type
            )
        ]
        edges.sort(
            key=lambda edge: (
                edge.source_id,
                edge.relationship,
                edge.target_id,
            )
        )
        if projection.startswith("count"):
            return CypherResult(
                success=True,
                raw={
                    "data": {
                        "nodes": {},
                        "edges": [],
                        "literals": [{"key": "count", "value": len(edges)}],
                    }
                },
            )
        offset, limit = self._page(query)
        page = edges[offset : offset + limit]
        endpoint_ids = sorted(
            {
                endpoint
                for edge in page
                for endpoint in (edge.source_id, edge.target_id)
            }
        )
        internal = {
            object_id: str(index)
            for index, object_id in enumerate(endpoint_ids)
        }
        return CypherResult(
            success=True,
            raw={
                "data": {
                    "nodes": {
                        internal[object_id]: self._node(object_id)
                        for object_id in endpoint_ids
                    },
                    "edges": [
                        {
                            "source": internal[edge.source_id],
                            "target": internal[edge.target_id],
                            "kind": edge.relationship,
                        }
                        for edge in page
                    ],
                    "literals": [],
                }
            },
        )


def test_live_snapshot_uses_bounded_pages_and_matches_archive_digest() -> None:
    archive, manifest = _archive_and_manifest()
    expected = build_archive_snapshot(archive, manifest)

    observed, receipt = asyncio.run(
        collect_live_snapshot(FakeLiveBHCE(expected), expected, page_size=2)
    )

    require_live_graph_match(expected, observed)
    assert observed.graph_fingerprint == expected.graph_fingerprint
    assert receipt.expected_graph_fingerprint == expected.graph_fingerprint
    assert receipt.observed_graph_fingerprint == expected.graph_fingerprint
    assert receipt.object_queries > 0
    assert receipt.relationship_queries > 0


def test_live_snapshot_uses_resilient_harness_reads_when_available() -> None:
    archive, manifest = _archive_and_manifest()
    expected = build_archive_snapshot(archive, manifest)

    class ResilientFakeLiveBHCE(FakeLiveBHCE):
        def __init__(self, snapshot: GraphSnapshot) -> None:
            super().__init__(snapshot)
            self.resilient_queries = 0

        async def run_cypher_resilient(self, query: str) -> CypherResult:
            self.resilient_queries += 1
            return await self.run_cypher(query)

    bhce = ResilientFakeLiveBHCE(expected)
    observed, receipt = asyncio.run(
        collect_live_snapshot(bhce, expected, page_size=2)
    )

    require_live_graph_match(expected, observed)
    assert bhce.resilient_queries == (
        receipt.object_queries + receipt.relationship_queries
    )


def test_live_snapshot_digest_detects_injected_relationship() -> None:
    archive, manifest = _archive_and_manifest()
    expected = build_archive_snapshot(archive, manifest)
    source, target = expected.objects[:2]
    contamination = EdgeWitness(
        source_id=source.entity.object_id,
        relationship="GenericAll",
        target_id=target.entity.object_id,
    )

    observed, _ = asyncio.run(
        collect_live_snapshot(
            FakeLiveBHCE(expected, extra_edges=(contamination,)),
            expected,
            page_size=3,
        )
    )

    assert observed.graph_fingerprint != expected.graph_fingerprint
    with pytest.raises(ValueError, match="live graph fingerprint mismatch"):
        require_live_graph_match(expected, observed)
