"""Independent inventory collection with synthetic rows and real canonical types."""

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from ori.eval.v2.graph import (
    CE_NORMALIZED_ARTIFACTS,
    GraphObject,
    GraphSnapshot,
    _object_properties,
    graph_snapshot_fingerprint,
)
from ori.eval.v2.native_bolt_graph import (
    EDGE_COUNT_QUERY,
    EDGE_PAGE_QUERY,
    NODE_COUNT_QUERY,
    NODE_PAGE_QUERY,
    collect_bolt_snapshot,
)
from ori.eval.v2.schema import EdgeWitness, EntityRef, PropertyFact


def _fixture():
    nodes = [
        {"object_id": oid, "labels": [kind], "properties": {"objectid": oid, "name": oid}}
        for oid, kind in (("A", "User"), ("B", "Group"), ("C-544", "ADLocalGroup"))
    ]
    edges = [
        {"source_id": "A", "relationship": "WriteDacl", "target_id": "B", "properties": {}},
        {"source_id": "B", "relationship": "MemberOfLocalGroup", "target_id": "C-544",
         "properties": {}},
    ]
    payload = dict(
        schema_version="ori-graph-snapshot-v2", manifest_schema_version="test",
        product="test", seed=67, domain="TEST.LOCAL", domain_sid="S-1-5-21-1",
        objects=tuple(GraphObject(
            entity=EntityRef(object_id=n["object_id"], object_type=n["labels"][0],
                             role="benchmark_object", canonical_name=n["object_id"]),
            properties=_object_properties(n["properties"], object_type=n["labels"][0]),
        ) for n in nodes),
        relationships=tuple(EdgeWitness(**edge) for edge in (
            {key: value for key, value in row.items() if key != "properties"} for row in edges
        )),
        relationship_counts=(PropertyFact(key="MemberOfLocalGroup", value=1),
                             PropertyFact(key="WriteDacl", value=1)),
        normalized_artifacts=CE_NORMALIZED_ARTIFACTS,
        graph_fingerprint="0" * 64,
    )
    payload["graph_fingerprint"] = graph_snapshot_fingerprint(payload)
    return GraphSnapshot.model_validate(payload), nodes, edges


def _reader(nodes, edges, calls):
    async def read(query, parameters):
        calls.append((query, parameters))
        if query == NODE_COUNT_QUERY:
            return [{"count": len(nodes)}]
        if query == EDGE_COUNT_QUERY:
            return [{"count": len(edges)}]
        assert query in (NODE_PAGE_QUERY, EDGE_PAGE_QUERY)
        data = nodes if query == NODE_PAGE_QUERY else edges
        offset, limit = parameters["offset"], parameters["limit"]
        return deepcopy(data[offset:offset + limit])
    return read


def test_bolt_snapshot_exact_pages_native_facts_and_semantic_properties(subtests):
    expected, nodes, edges = _fixture()
    calls = []
    observed, receipt, native = asyncio.run(collect_bolt_snapshot(
        _reader(nodes, edges, calls), expected, page_size=2,
    ))
    assert observed == expected
    assert receipt.object_queries == 4 and receipt.relationship_queries == 3
    assert [params["limit"] for q, params in calls if q == NODE_PAGE_QUERY] == [2, 1]
    assert len(native) == 64
    for case in ("native-property", "auxiliary-label", "normalized-edge", "semantic-edge"):
        with subtests.test(case=case):
            changed_nodes, changed_edges = deepcopy(nodes), deepcopy(edges)
            kwargs = {}
            target = expected
            if case == "native-property":
                changed_nodes[0]["properties"]["unscored"] = ["raw", {"value": 1}]
            elif case == "auxiliary-label":
                changed_nodes[0]["labels"].append("Base")
                kwargs["auxiliary_labels"] = ("Base",)
            elif case == "normalized-edge":
                changed_edges[0]["properties"]["isacl"] = True
            else:
                changed_edges[0]["properties"]["enabled"] = True
                payload = expected.model_dump(mode="python")
                payload["relationships"][0]["properties"] = ({"key": "enabled", "value": True},)
                payload["graph_fingerprint"] = graph_snapshot_fingerprint(payload)
                target = GraphSnapshot.model_validate(payload)
            actual, _, changed = asyncio.run(collect_bolt_snapshot(
                _reader(changed_nodes, changed_edges, []), target, page_size=1, **kwargs,
            ))
            assert actual == target and changed != native
            if case == "auxiliary-label":
                changed_nodes[0]["labels"].reverse()
                reordered, _, reordered_hash = asyncio.run(collect_bolt_snapshot(
                    _reader(changed_nodes, changed_edges, []), target, page_size=1, **kwargs,
                ))
                assert reordered == target
                assert reordered_hash != changed


def test_bolt_snapshot_rejects_inventory_and_semantic_drift(subtests):
    for case in (
        "foreign-node", "extra-node", "missing-local-group", "duplicate-id", "node-order",
        "ambiguous-label", "unknown-label", "missing-label", "duplicate-edge", "foreign-edge",
        "legacy-edge", "wrong-name", "property-drift", "nonfinite", "bytes", "temporal",
        "edge-property", "edge-container", "short-page", "post-count", "bool-count",
    ):
        with subtests.test(case=case):
            expected, nodes, edges = _fixture()
            if case == "foreign-node":
                nodes[-1]["object_id"] = "Z"
                nodes[-1]["properties"]["objectid"] = "Z"
            elif case == "extra-node":
                nodes.append(deepcopy(nodes[-1]))
            elif case == "missing-local-group":
                nodes.pop()
            elif case == "duplicate-id":
                nodes[1] = deepcopy(nodes[0])
            elif case == "node-order":
                nodes.reverse()
            elif case == "ambiguous-label":
                nodes[0]["labels"].append("Group")
            elif case == "unknown-label":
                nodes[0]["labels"].append("Unknown")
            elif case == "missing-label":
                nodes[0]["labels"] = []
            elif case == "duplicate-edge":
                edges[1] = deepcopy(edges[0])
            elif case == "foreign-edge":
                edges[0]["source_id"] = "0"
            elif case == "legacy-edge":
                edges[0]["relationship"] = "WriteDACL"
            elif case == "wrong-name":
                nodes[0]["properties"]["name"] = "OTHER"
            elif case == "property-drift":
                nodes[0]["properties"]["enabled"] = False
            elif case in ("nonfinite", "bytes", "temporal"):
                from datetime import datetime
                nodes[0]["properties"]["raw"] = {
                    "nonfinite": float("nan"), "bytes": b"bytes", "temporal": datetime(2020, 1, 1),
                }[case]
            elif case == "edge-property":
                edges[0]["properties"]["enabled"] = True
            elif case == "edge-container":
                edges[0]["properties"]["semantic"] = {"nested": True}
            calls = []
            delegate = _reader(nodes, edges, calls)

            async def read(query, params):
                result = await delegate(query, params)
                if case == "short-page" and query == NODE_PAGE_QUERY:
                    return result[:-1]
                if case == "post-count" and query == NODE_COUNT_QUERY and len(calls) > 2:
                    return [{"count": 4}]
                if case == "bool-count" and query == NODE_COUNT_QUERY:
                    return [{"count": True}]
                return result

            with pytest.raises(ValueError):
                asyncio.run(collect_bolt_snapshot(read, expected, page_size=2))


def test_bolt_snapshot_limits_timeout_and_cancellation(subtests):
    expected, nodes, edges = _fixture()
    for options in (
        {"page_size": True}, {"page_size": 0}, {"timeout_seconds": False},
        {"timeout_seconds": float("inf")}, {"auxiliary_labels": ("User",)},
        {"auxiliary_labels": ("Base", "Base")},
    ):
        with subtests.test(options=options):
            calls = []
            with pytest.raises(ValueError):
                asyncio.run(collect_bolt_snapshot(
                    _reader(nodes, edges, calls), expected, **options,
                ))
            assert not calls

    async def slow(*args):
        await asyncio.Event().wait()

    with pytest.raises(TimeoutError):
        asyncio.run(collect_bolt_snapshot(slow, expected, timeout_seconds=0.001))

    async def cancelled(*args):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(collect_bolt_snapshot(cancelled, expected))


def _driver_double(monkeypatch, nodes, edges, *, database="neo4j", delay=False, overflow=False):
    import neo4j

    events = []
    reader = _reader(nodes, edges, events)

    class Result:
        def __init__(self, rows):
            self.rows = rows

        async def __aiter__(self):
            for row in self.rows:
                yield SimpleNamespace(data=lambda row=row: deepcopy(row))

        async def consume(self):
            events.append("consume")
            return SimpleNamespace(
                database=database,
                server=SimpleNamespace(address=("test.invalid", 7687), agent="Neo4j/test",
                                       protocol_version=(5, 8)),
            )

    class Transaction:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *error):
            events.append(("transaction_exit", error[0]))

        async def run(self, query, parameters):
            if delay:
                await asyncio.Event().wait()
            rows = await reader(query, parameters)
            return Result(rows * 2 if overflow else rows)

        async def rollback(self):
            events.append("rollback")

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *error):
            events.append("session_closed")

        async def begin_transaction(self, **kwargs):
            events.append(("begin", kwargs))
            return Transaction()

    class Driver:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *error):
            events.append("driver_closed")

        def session(self, **kwargs):
            events.append(("session", kwargs))
            return Session()

    def driver(uri, **kwargs):
        events.append(("driver", uri, kwargs))
        return Driver()

    monkeypatch.setattr(neo4j.AsyncGraphDatabase, "driver", driver)
    return events


def test_bolt_transport_explicit_database_rollback_and_typed_failures(monkeypatch, subtests):
    pytest.importorskip("neo4j")
    from ori.eval.v2.native_bolt_runtime import NativeBackendError, verify_bolt_graph

    expected, nodes, edges = _fixture()
    options = dict(uri="bolt://test.invalid:7687", username="readonly", password="test-secret",
                   database="neo4j", expected=expected, page_size=2)
    for case in ("success", "database", "overflow", "timeout", "invalid-config"):
        with subtests.test(case=case):
            events = _driver_double(
                monkeypatch, nodes, edges, database="wrong" if case == "database" else "neo4j",
                overflow=case == "overflow", delay=case == "timeout",
            )
            supplied = dict(options)
            if case == "timeout":
                supplied.update(timeout_seconds=0.01, transaction_timeout_seconds=0.005)
            if case == "invalid-config":
                supplied["uri"] = "bolt://readonly:test-secret@test.invalid"
            if case == "success":
                report = asyncio.run(verify_bolt_graph(**supplied))
                assert report["graph_verification"]["observed_graph_fingerprint"] == (
                    expected.graph_fingerprint
                )
                assert report["connection_observation"]["database"] == "neo4j"
                assert report["read_only_privileges_verified"] is False
                assert report["quiescence_verified"] is False
                assert report["campaign_admitted"] is False
                assert "test-secret" not in json.dumps(report)
                assert "rollback" in events
                session = next(item[1] for item in events if isinstance(item, tuple)
                               and item[0] == "session")
                assert session == {"database": "neo4j", "default_access_mode": "READ",
                                   "fetch_size": 2}
            else:
                with pytest.raises(NativeBackendError) as error:
                    asyncio.run(verify_bolt_graph(**supplied))
                assert "test-secret" not in str(error.value)
                if case == "database":
                    assert str(error.value) == "NATIVE_BACKEND_DATABASE_MISMATCH"
                if case == "timeout":
                    assert str(error.value) == "NATIVE_BACKEND_TIMEOUT"
                if case == "invalid-config":
                    assert events == []
                    continue
            assert events[-2:] == ["session_closed", "driver_closed"]


def test_native_graph_file_command_credential_scope_and_private_output(tmp_path, monkeypatch):
    pytest.importorskip("neo4j")
    from click.testing import CliRunner

    from ori.cli import main
    from ori.eval.v2 import graph

    expected, nodes, edges = _fixture()
    manifest = tmp_path / "manifest.json"
    archive = tmp_path / "archive.zip"
    manifest.write_text('{"source": "test"}')
    archive.write_bytes(b"test archive")
    calls = []

    def snapshot(data, metadata, *, product):
        assert data == b"test archive" and metadata == {"source": "test"} and product == "simple"
        calls.append("archive-validated")
        return expected

    monkeypatch.setattr(graph, "build_archive_snapshot", snapshot)
    events = _driver_double(monkeypatch, nodes, edges)
    monkeypatch.setenv("BLOODHOUND_URI", "bolt://test.invalid:7687")
    monkeypatch.setenv("BLOODHOUND_USERNAME", "readonly")
    monkeypatch.setenv("BLOODHOUND_PASSWORD", "scoped-test-secret")
    monkeypatch.setenv("NEO4J_PASSWORD", "wrong-implementation-secret")
    output = tmp_path / "verification.private.json"
    command = [
        "verify-native-graph", "--manifest", str(manifest), "--archive", str(archive),
        "--product", "simple", "--implementation", "mordavid", "--database", "neo4j",
        "--output", str(output), "--page-size", "2",
    ]
    runner = CliRunner()
    result = runner.invoke(main, command)
    assert result.exit_code == 0, result.output
    assert "NATIVE GRAPH SCORING PARITY: PASS" in result.output
    assert calls == ["archive-validated"]
    auth = events[0][2]["auth"]
    assert auth == ("readonly", "scoped-test-secret")
    report = json.loads(output.read_text())
    assert report["mcp_source_verified"] is False and report["campaign_admitted"] is False
    assert report["credential_source_names"] == ["BLOODHOUND_USERNAME", "BLOODHOUND_PASSWORD"]
    assert output.stat().st_mode & 0o777 == 0o600
    assert "secret" not in output.read_text() and "secret" not in result.output
    before = output.read_bytes()
    result = runner.invoke(main, command)
    assert result.exit_code != 0 and "NATIVE_BACKEND_OUTPUT_EXISTS" in result.output
    assert output.read_bytes() == before and len(calls) == 1
    result = runner.invoke(main, command[:-3] + [str(tmp_path / "public.json")])
    assert result.exit_code != 0 and "NATIVE_BACKEND_PRIVATE_OUTPUT_REQUIRED" in result.output


def test_native_graph_command_real_archive_and_ce_derived_inventory(tmp_path, monkeypatch):
    pytest.importorskip("neo4j")
    from click.testing import CliRunner

    from ori.cli import main
    from ori.eval.v2.graph import build_archive_snapshot
    from ori.generator.archive_validation import _relationships_from_archive
    from ori.generator.graph import ADGraph
    from ori.generator.org import build_org
    from ori.generator.serializer import _build_zip, project_nodes_for_sharphound

    graph = ADGraph("TEST.LOCAL", seed=67)
    build_org(graph, num_users=2, num_workstations=1, num_servers=1)
    archive = _build_zip(graph)
    manifest = {
        "schema_version": "ori-generated-manifest-v2", "seed": 67,
        "domain": graph.domain, "domain_sid": graph.domain_sid,
        "stats": {"total_nodes": len(project_nodes_for_sharphound(graph))},
        "relationship_summary": {"total_relationships": len(_relationships_from_archive(archive))},
    }
    expected = build_archive_snapshot(archive, manifest, product="simple")
    nodes = []
    for item in expected.objects:
        properties = {}
        for fact in item.properties:
            if fact.key == "kind":
                continue
            if fact.key in properties:
                existing = properties[fact.key]
                properties[fact.key] = (
                    [*existing, fact.value]
                    if isinstance(existing, list) else [existing, fact.value]
                )
            else:
                properties[fact.key] = fact.value
        # A canonical display fallback is not a native name property. In
        # particular, archive-derived ADLocalGroup objects may have no name.
        properties["objectid"] = item.entity.object_id
        if item.entity.domain:
            properties["domain"] = item.entity.domain
        nodes.append({"object_id": item.entity.object_id, "labels": [item.entity.object_type],
                      "properties": properties})
    nodes.sort(key=lambda row: row["object_id"])
    edges = [{"source_id": edge.source_id, "relationship": edge.relationship,
              "target_id": edge.target_id, "properties": {p.key: p.value for p in edge.properties}}
             for edge in expected.relationships]
    edges.sort(key=lambda row: (row["source_id"], row["relationship"], row["target_id"]))
    assert any(row["labels"] == ["ADLocalGroup"] for row in nodes)
    asyncio.run(collect_bolt_snapshot(_reader(nodes, edges, []), expected, page_size=3))
    invented_name = deepcopy(nodes)
    nameless = next(row for row in invented_name if "name" not in row["properties"])
    nameless["properties"]["name"] = nameless["object_id"]
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        asyncio.run(collect_bolt_snapshot(_reader(invented_name, edges, []), expected))
    _driver_double(monkeypatch, nodes, edges)
    monkeypatch.setenv("NEO4J_URI", "bolt://test.invalid:7687")
    monkeypatch.setenv("NEO4J_USERNAME", "readonly")
    monkeypatch.setenv("NEO4J_PASSWORD", "test-secret")
    manifest_path, archive_path = tmp_path / "source.json", tmp_path / "source.zip"
    manifest_path.write_text(json.dumps(manifest))
    archive_path.write_bytes(archive)
    output = tmp_path / "real-archive.private.json"
    result = CliRunner().invoke(main, [
        "verify-native-graph", "--implementation", "armadin", "--database", "neo4j",
        "--manifest", str(manifest_path), "--archive", str(archive_path), "--product", "simple",
        "--output", str(output), "--page-size", "3",
    ])
    assert result.exit_code == 0, result.output
    receipt = json.loads(output.read_text())["graph_verification"]
    assert receipt["observed_graph_fingerprint"] == expected.graph_fingerprint
    assert receipt["object_count"] == len(nodes)
    assert receipt["relationship_count"] == len(edges)
