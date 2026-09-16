"""Independent inventory collection with synthetic rows and real canonical types."""

import asyncio
import hashlib
import json
from copy import deepcopy
from pathlib import Path
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
from ori.eval.v2.schema import EdgeWitness, EntityRef, PropertyFact, Track


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


def _driver_double(
    monkeypatch, nodes, edges, *, database="neo4j", delay=False, overflow=False, control_rows=None,
    privilege_rows=None, setting_rows=None,
):
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
            if query.startswith("SHOW DATABASES") and control_rows is not None:
                events.append(query)
                return Result(control_rows)
            if query == "SHOW USER PRIVILEGES" and privilege_rows is not None:
                events.append(query)
                return Result(privilege_rows)
            if query.startswith("SHOW SETTINGS "):
                events.append(query)
                return Result(setting_rows if setting_rows is not None else [
                    {"name": "db.transaction.timeout", "value": "60s"},
                ])
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


@pytest.mark.parametrize("outcome", ["success", "failure", "cancelled"])
def test_retained_cleanup_drain_consumes_terminal_worker(outcome):
    from ori.eval.v2.native_mcp_runtime import NativeSessionCleanupPending

    async def scenario():
        async def worker():
            if outcome == "failure":
                raise RuntimeError("private fixture failure")
            if outcome == "cancelled":
                raise asyncio.CancelledError

        task = asyncio.create_task(worker())
        pending = NativeSessionCleanupPending(
            task, cancellation_requested=False, startup_timed_out=False,
        )
        await pending.wait_for_cleanup()
        assert task.done()
        assert task.cancelled() is (outcome == "cancelled")
        # Reuse does not resurrect work or leak a terminal worker exception.
        await pending.wait_for_cleanup()
        assert str(pending) == "NATIVE_SESSION_CLEANUP_PENDING"

    asyncio.run(scenario())


def test_native_qualification_cli_drains_pending_cleanup(tmp_path, monkeypatch):
    import click
    from click.testing import CliRunner

    from ori.cli import main
    from ori.eval.v2 import cli_support
    from ori.eval.v2.native_mcp_runtime import NativeSessionCleanupPending

    state = {"closed": False, "pending_before_close": False, "cancellations": 0}
    echo = click.echo

    def observed_echo(message=None, **kwargs):
        if message and "waiting for owned cleanup" in str(message):
            assert kwargs.get("err") is True
            state["pending_before_close"] = not state["closed"]
        return echo(message, **kwargs)

    async def qualification(**kwargs):
        release = asyncio.Event()

        async def worker():
            try:
                while not release.is_set():
                    try:
                        await release.wait()
                    except asyncio.CancelledError:
                        state["cancellations"] += 1
                raise RuntimeError("private terminal cleanup detail")
            finally:
                state["closed"] = True

        task = asyncio.create_task(worker())
        await asyncio.sleep(0)
        loop = asyncio.get_running_loop()
        owner = asyncio.current_task()
        loop.call_later(0.001, task.cancel)
        loop.call_later(0.002, owner.cancel)
        loop.call_later(0.003, task.cancel)
        loop.call_later(0.004, owner.cancel)
        loop.call_later(0.02, release.set)
        raise NativeSessionCleanupPending(
            task, cancellation_requested=False, startup_timed_out=False,
        )

    monkeypatch.setattr(cli_support, "qualify_native_mcp_files", qualification)
    monkeypatch.setattr(click, "echo", observed_echo)
    output = tmp_path / "output"
    result = CliRunner().invoke(main, [
        "qualify-native-mcp", "--config", "fixture.yaml", "--output-dir", str(output),
        "--execute", "--json",
    ])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"]["code"] == "NATIVE_SESSION_CLEANUP_PENDING"
    assert "NATIVE_SESSION_CLEANUP_PENDING" in result.stderr
    assert "private terminal" not in result.output
    assert state == {"closed": True, "pending_before_close": True, "cancellations": 2}
    assert not output.exists()


def test_native_qualification_cli_boundaries(tmp_path, monkeypatch, subtests):
    from types import SimpleNamespace

    from click.testing import CliRunner

    from ori.cli import main
    from ori.eval.v2 import campaign_runner, certification, native_bolt_runtime, native_ce_runtime

    for case in (
        "offline", "execute", "collision", "failed_cleanup", "ce", "missing_output",
        "wrong_output", "wrong_target",
    ):
        with subtests.test(case=case):
            events = []
            output = tmp_path / case
            profile = SimpleNamespace(
                implementation_id="mwnickerson" if case in {"ce", "wrong_target"} else "mordavid",
                backend="bhce" if case in {"ce", "wrong_target"} else "neo4j",
                runtime_fingerprint="a" * 64, dependency_lock_fingerprint="b" * 64,
            )
            prepared = SimpleNamespace(profile=profile, task_ids=tuple(range(50)), corpus="corpus")
            paths = SimpleNamespace(
                python_executable=tmp_path / "python", runtime_roots=(tmp_path,),
                dependency_lock=tmp_path / "lock",
                qualification=None, qualification_work=None,
            )
            resolved = SimpleNamespace(native_mcp_paths=paths, mcp_dir=tmp_path,
                                       tracks={Track.MCP: SimpleNamespace(
                                           candidates=None, live_certification=None,
                                       )},
                                       config=SimpleNamespace(defaults=SimpleNamespace(
                                           bhce_url=None,
                                           mcp=SimpleNamespace(databases=("neo4j", "bloodhound")),
                                       )))
            if case == "wrong_output":
                paths.qualification = tmp_path / "other" / "native-qualification-v2.private.json"
            if case == "wrong_target":
                resolved.config.defaults.bhce_url = "https://different.invalid"
            artifact = SimpleNamespace(model_dump=lambda **_: {"synthetic": True})
            monkeypatch.setattr(campaign_runner, "prepare_native_qualification", lambda _: (
                resolved, prepared, "snapshot", artifact, "guard", 75,
            ))

            async def qualify(**kwargs):
                events.append("services")
                assert case != "offline"
                assert output.stat().st_mode & 0o777 == 0o700
                assert (output / "native-stderr.private.log").stat().st_mode & 0o777 == 0o600
                if case != "ce":
                    assert kwargs["guard"] == "guard"
                if case == "failed_cleanup":
                    raise RuntimeError("private credential fixture and endpoint")
                return {"tasks": []}, {"completed": True}

            def certify(*args, **kwargs):
                events.append("certified")
                assert events == ["services", "certified"]
                return SimpleNamespace(candidate_catalog=artifact, model_dump=artifact.model_dump)

            monkeypatch.setattr(native_bolt_runtime, "qualify_native_bolt_corpus", qualify)
            monkeypatch.setattr(native_ce_runtime, "qualify_native_ce_corpus", qualify)
            monkeypatch.setattr(certification, "live_certify_native_corpus", certify)
            for suffix in ("URI", "USERNAME", "PASSWORD"):
                monkeypatch.setenv(f"BLOODHOUND_{suffix}", "synthetic")
            for suffix in ("DOMAIN", "TOKEN_ID", "TOKEN_KEY"):
                monkeypatch.setenv(f"BLOODHOUND_{suffix}", "synthetic")
            if case == "collision":
                output.mkdir()
            args = ["qualify-native-mcp", "--config", "fixture.yaml", "--json"]
            if case != "missing_output":
                args += ["--output-dir", str(output)]
            if case != "offline":
                args += ["--execute"]
            result = CliRunner().invoke(main, args)
            if case not in {"execute", "ce"}:
                assert str(tmp_path) not in result.output
            assert "private credential" not in result.output
            if case in {"offline", "execute", "ce"}:
                assert result.exit_code == 0, result.output
                summary = json.loads(result.output)
                assert summary["provider_calls"] == 0 and not summary["campaign_admitted"]
                assert summary["schema_version"] == "ori-native-qualification-v1"
                if case != "offline":
                    for reference in summary["artifacts"].values():
                        path = Path(reference["path"])
                        assert path.parent == output
                        assert reference["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
                assert summary["status"] == (
                    "offline_prepared" if case == "offline" else "native_candidate_certified"
                )
            else:
                assert result.exit_code == 1
            if case == "offline":
                assert not output.exists() and not events
            elif case in {"execute", "ce"}:
                assert len(list(output.glob("*.json"))) == 5
                assert all(path.stat().st_mode & 0o777 == 0o600 for path in output.glob("*.json"))
            else:
                assert not list(output.glob("*.json"))
                assert events == (["services"] if case == "failed_cleanup" else [])


def test_completed_qualification_replays_observations(subtests):
    from ori.eval.v2.fingerprint import canonical_sha256
    from ori.eval.v2.native_bolt_runtime import (
        NativeBackendError,
        validate_completed_native_bolt_qualification,
        validate_native_serving_topology,
    )
    from ori.eval.v2.native_capability import build_native_capability_profile
    from ori.eval.v2.native_mcp_profiles import get_native_implementation

    expected, nodes, edges = _fixture()
    _, receipt, surface = asyncio.run(collect_bolt_snapshot(_reader(nodes, edges, []), expected))
    rows = [{"name": name, "type": "standard", "access": "read-only",
             "currentStatus": "online", "requestedStatus": "online",
             "databaseID": name + "-id", "serverID": "server", "address": "fixture:7687",
             "lastStartTime": "fixture-start", "lastCommittedTxn": 1, "home": name == "neo4j",
             "role": "primary", "writer": True, "currentPrimariesCount": 1,
             "currentSecondariesCount": 0, "requestedPrimariesCount": 1,
             "requestedSecondariesCount": 0, "replicationLag": 0}
            for name in ("neo4j", "bloodhound")]
    access = {
        "transaction_timeout": [{"name": "db.transaction.timeout", "value": "60s"}],
        "databases": {row["name"]: row for row in rows}, "principal_name": "fixture",
        "requested_endpoint": "bolt://fixture:7687", "server_address": ["fixture", 7687],
        "server_agent": "Neo4j/fixture", "protocol_version": [5, 8],
        "serving_topology": validate_native_serving_topology(
            rows, databases=("neo4j", "bloodhound"),
        ),
        "privileges": [{"access": "GRANTED", "action": "access", "resource": "database",
                        "graph": "*", "segment": "database", "role": "reader", "user": "fixture",
                        "immutable": False}],
    }
    source = get_native_implementation("mordavid")
    discovery = dict(
        tools=[{"name": name, "inputSchema": {"type": "object"}}
               for name in source.native_tool_names],
        prompts=[], resources=[], resource_templates=[],
        surface_availability={"tools": True, "prompts": True, "resources": True},
    )
    profile = build_native_capability_profile(
        "mordavid", runtime_fingerprint="a" * 64, dependency_lock_fingerprint="b" * 64,
        backend_binding_fingerprint=canonical_sha256(access), **discovery,
    )
    graphs = {name: {
        "graph_verification": receipt.model_dump(mode="json"),
        "native_surface_fingerprint": surface, "principal_name": "fixture",
        "requested_endpoint": access["requested_endpoint"], "requested_database": name,
        "connection_observation": {"database": name, **{key: access[key] for key in (
            "server_address", "server_agent", "protocol_version",
        )}},
    } for name in ("neo4j", "bloodhound")}
    interval = {"access_before": access, "access_after": access, "graphs": graphs}
    report = dict(
        implementation_id="mordavid", capability_profile_fingerprint=profile.profile_fingerprint,
        scope="completed-native-session-interval", session_cleanup_confirmed=True,
        qualification_work_fingerprint=canonical_sha256("fixture-work"),
        graph_before=deepcopy(interval), graph_after=deepcopy(interval), native_discovery=discovery,
        runtime={"source_revision": profile.source_revision, "implementation_id": "mordavid",
                 "source_bytes_verified": True, "runtime_content_verified": True,
                 "source_tree_fingerprint": "c" * 64, "interpreter_sha256": "d" * 64,
                 "python_executable": "/fixture/venv/bin/python", "checkout": "/fixture/source",
                 "runtime_content_fingerprint": profile.runtime_fingerprint, "runtime_startup": {
                     "python_startup_verified": True, "runtime_roots": ["/fixture/venv"],
                     "python_startup": {
                         "prefix": "/fixture/venv", "base_prefix": "/fixture/base",
                         "version": [3, 12, 1], "abi": "cpython-312", "platform": "fixture",
                         "paths": ["/fixture/source", "/fixture/venv/lib/site-packages"],
                         "original_paths": ["", "/fixture/venv/lib/site-packages"],
                         "site_packages": ["/fixture/venv/lib/site-packages"],
                     },
                     "python_isolated_startup": {
                         "prefix": "/fixture/base", "base_prefix": "/fixture/base",
                         "version": [3, 12, 1], "abi": "cpython-312", "platform": "fixture",
                         "paths": ["/fixture/venv/lib"], "site_packages": [],
                         "original_paths": ["/fixture/venv/lib"],
                     },
                     "runtime_content_fingerprint": profile.runtime_fingerprint,
                     "dependencies": {"dependency_inventory_verified": True,
                                      "dependency_lock_fingerprint":
                                      profile.dependency_lock_fingerprint,
                                      "installed_distributions": {},
                                      "distribution_inventory_fingerprint": canonical_sha256({})},
                 }},
    )
    for case in (
        "valid", "sorted_json", "site_alias", "isolated_stdlib", "flags_only", "drift",
        "missing_graph", "discovery",
                 "runtime", "cleanup", "borrowed_work", "false_source", "missing_digest",
                 "missing_startup", "wrong_interpreter", "external_import", "isolated_mismatch"):
        with subtests.test(case=case):
            supplied = deepcopy(report)
            if case == "sorted_json":
                supplied = json.loads(json.dumps(supplied, sort_keys=True))
            elif case == "site_alias":
                startup = supplied["runtime"]["runtime_startup"]["python_startup"]
                startup["paths"][1] = "/fixture/venv/packages"
                startup["site_packages"] = ["/fixture/venv/packages"]
            elif case == "isolated_stdlib":
                startup = supplied["runtime"]["runtime_startup"]
                for key in ("python_startup", "python_isolated_startup"):
                    startup[key]["paths"].append("/fixture/base/lib/python3.12")
                    startup[key]["original_paths"].append("/fixture/base/lib/python3.12")
            elif case == "flags_only":
                supplied = {"runtime_qualified": True, "session_cleanup_confirmed": True}
            elif case == "drift":
                supplied["graph_after"]["access_after"]["databases"]["neo4j"].update(
                    lastCommittedTxn=2,
                )
            elif case == "missing_graph":
                supplied["graph_after"]["graphs"].pop("bloodhound")
            elif case == "discovery":
                supplied["native_discovery"]["tools"].pop()
            elif case == "runtime":
                supplied["runtime"]["runtime_content_fingerprint"] = "d" * 64
            elif case == "cleanup":
                supplied["session_cleanup_confirmed"] = False
            elif case == "borrowed_work":
                supplied["qualification_work_fingerprint"] = canonical_sha256("other-session-work")
            elif case == "false_source":
                supplied["runtime"]["source_bytes_verified"] = False
            elif case == "missing_digest":
                del supplied["runtime"]["interpreter_sha256"]
            elif case == "missing_startup":
                del supplied["runtime"]["runtime_startup"]["python_startup"]
            elif case == "wrong_interpreter":
                supplied["runtime"]["python_executable"] = "/other/bin/python"
            elif case == "external_import":
                supplied["runtime"]["runtime_startup"]["python_startup"]["paths"].append("/other")
            elif case == "isolated_mismatch":
                supplied["runtime"]["runtime_startup"]["python_isolated_startup"]["abi"] = "wrong"
            if case in {"valid", "sorted_json", "site_alias", "isolated_stdlib"}:
                assert validate_completed_native_bolt_qualification(
                    supplied, profile=profile, expected=expected, work_result="fixture-work",
                ) == canonical_sha256(supplied)
            else:
                with pytest.raises(NativeBackendError):
                    validate_completed_native_bolt_qualification(
                        supplied, profile=profile, expected=expected, work_result="fixture-work",
                    )


def test_native_qualification_owns_complete_interval(monkeypatch, subtests):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from ori.eval.v2 import native_bolt_runtime as runtime
    from ori.eval.v2 import native_mcp_runtime
    from ori.eval.v2.fingerprint import canonical_sha256
    from tests.support.v2_mcp import native_profile

    profile = native_profile("mordavid")
    initial_access = {"serving_topology": {"shape": "single-primary-no-secondaries"},
                      "transaction": 1}
    payload = profile.model_dump(mode="python")
    payload["backend_binding_fingerprint"] = canonical_sha256(initial_access)
    payload["profile_fingerprint"] = canonical_sha256(
        payload, exclude_fields=("profile_id", "profile_fingerprint"),
    )
    payload["profile_id"] = "ori-native-mordavid-v1-" + payload["profile_fingerprint"]
    profile = type(profile).model_validate(payload)
    config = SimpleNamespace(
        implementation_id="mordavid", runtime_roots=("fixture",), dependency_lock="fixture",
        runtime_fingerprint=profile.runtime_fingerprint,
        dependency_lock_fingerprint=profile.dependency_lock_fingerprint,
    )
    for case in ("valid", "drift", "work_error", "cleanup_error", "cancel", "runtime_missing",
                 "backend_binding", "neutral", "admission_error", "admission_cancel",
                 "coordinator_mismatch", "runtime_invalid"):
        with subtests.test(case=case):
            events = []

            async def verify(**kwargs):
                assert kwargs["require_supported_topology"] is True
                assert kwargs["databases"] == ("neo4j", "bloodhound")
                events.append("graph")
                access = {"serving_topology": {"shape": "single-primary-no-secondaries"},
                          "transaction": 2 if case == "backend_binding" or (
                              case == "drift" and len(events) > 1
                          ) else 1}
                return {"access_before": access, "access_after": access}

            @asynccontextmanager
            async def session(*args, **kwargs):
                assert kwargs["session_call_timeout_seconds"] == (
                    125.0 if case in {"neutral", "admission_error", "admission_cancel"} else 60.0
                )
                events.append("opened")
                try:
                    yield SimpleNamespace(discovered_tools=[], tools=[], prompts=[],
                                          resources=[], resource_templates=[],
                                          surface_availability={}), {
                        "source_bytes_verified": True, "runtime_content_verified": True,
                        "runtime_startup": {"python_startup_verified": True, "dependencies": {
                            "dependency_inventory_verified": case != "runtime_missing",
                            "dependency_lock_fingerprint": profile.dependency_lock_fingerprint,
                        }},
                    }
                finally:
                    events.append("closed")
                    if case == "cleanup_error":
                        raise RuntimeError("fixture cleanup")

            async def work(native):
                events.append("work")
                if case == "work_error":
                    raise RuntimeError("fixture work")
                if case == "cancel":
                    raise asyncio.CancelledError
                return "fixture-result"

            monkeypatch.setattr(runtime, "verify_native_bolt_read_only_graphs", verify)
            monkeypatch.setattr(native_mcp_runtime, "open_native_mcp_session", session)
            def validate_observations(observations, **kwargs):
                assert kwargs["backend_binding_fingerprint"] == profile.backend_binding_fingerprint
                assert observations["runtime"]["source_bytes_verified"] is True
                if case == "runtime_invalid":
                    raise ValueError("fixture invalid source observation")

            monkeypatch.setattr(native_mcp_runtime, "validate_native_session_observations",
                                validate_observations)
            arguments = dict(config=config, profile=profile, connection={}, guard=lambda *_: True,
                             private_stderr=None, databases=("neo4j", "bloodhound"), expected=None)
            async def before_work(observations):
                events.append("admission")
                assert "session_cleanup_confirmed" not in observations
                observations["graph_before"]["access_before"]["transaction"] = 999
                observations["runtime"]["source_bytes_verified"] = False
                if case == "admission_error":
                    raise RuntimeError("fixture admission")
                if case == "admission_cancel":
                    raise asyncio.CancelledError

            if case in {"neutral", "admission_error", "admission_cancel", "coordinator_mismatch"}:
                operation = runtime.run_native_bolt_session_work(
                    **arguments, work=work, before_work=before_work,
                    session_call_timeout_seconds=125.0,
                    query_coordinator=object() if case == "coordinator_mismatch" else None,
                )
            else:
                operation = runtime.qualify_native_bolt_session(
                    **arguments, qualification_work=work,
                )
            if case in {"valid", "neutral"}:
                value, report = asyncio.run(operation)
                assert value == "fixture-result"
                assert "runtime_qualified" not in report and report["session_cleanup_confirmed"]
                assert ("qualification_work_fingerprint" in report) is (case == "valid")
                assert report["committed_data_quiescence_observed"]
                assert report["campaign_admitted"] is False
                assert report["runtime"]["source_bytes_verified"] is True
                assert report["graph_before"]["access_before"]["transaction"] == 1
                assert events == ["graph", "opened", *(["admission"] if case == "neutral" else []),
                                  "work", "closed", "graph"]
            else:
                error = asyncio.CancelledError if case in {"cancel", "admission_cancel"} else (
                    RuntimeError if case in {"cleanup_error", "work_error", "admission_error"}
                    else runtime.NativeBackendError
                )
                with pytest.raises(error):
                    asyncio.run(operation)
                assert ("closed" in events) is (
                    case not in {"backend_binding", "coordinator_mismatch"}
                )
                assert events.count("graph") == (
                    0 if case == "coordinator_mismatch" else
                    2 if case in {"drift", "admission_error", "admission_cancel"} else 1
                )
                if case in {"admission_error", "admission_cancel"}:
                    assert "work" not in events


def test_native_serving_topology_is_explicit(subtests):
    from ori.eval.v2.native_bolt_runtime import NativeBackendError, validate_native_serving_topology

    valid = {"name": "neo4j", "serverID": "server", "role": "primary", "writer": True,
             "currentPrimariesCount": 1, "currentSecondariesCount": 0,
             "requestedPrimariesCount": 1, "requestedSecondariesCount": 0, "replicationLag": 0}
    assert validate_native_serving_topology([valid], databases=("neo4j",))["shape"] == (
        "single-primary-no-secondaries"
    )
    for change in ({"role": "secondary"}, {"writer": False}, {"currentPrimariesCount": 3},
                   {"requestedSecondariesCount": 1}, {"replicationLag": -1},
                   {"currentPrimariesCount": True}, {"serverID": ""}):
        with subtests.test(change=change), pytest.raises(NativeBackendError):
            validate_native_serving_topology([{**valid, **change}], databases=("neo4j",))


def test_native_database_access_observation_requires_positive_evidence(monkeypatch, subtests):
    from ori.eval.v2.native_bolt_runtime import (
        NativeBackendError,
        observe_bolt_database_access,
        validate_database_access_observation,
    )

    rows = [{"name": name, "type": "standard", "access": "read-only",
             "currentStatus": "online", "requestedStatus": "online",
             "databaseID": name + "-id", "serverID": "server-id", "address": "fixture:7687",
             "lastStartTime": "2026-09-06T00:00:00Z", "lastCommittedTxn": 10,
             "home": name == "neo4j"} for name in ("neo4j", "bloodhound")]
    for case in ("valid", "writable", "missing", "duplicate", "identity", "txn", "home"):
        with subtests.test(case=case):
            supplied = deepcopy(rows)
            if case == "writable":
                supplied[1]["access"] = "read-write"
            if case == "missing":
                supplied.pop()
            if case == "duplicate":
                supplied.append(deepcopy(supplied[0]))
            if case == "identity":
                supplied[1]["databaseID"] = None
            if case == "txn":
                supplied[1]["lastCommittedTxn"] = True
            if case == "home":
                supplied[0]["home"] = False
            if case == "valid":
                observed = validate_database_access_observation(
                    supplied, databases=("neo4j", "bloodhound"), home_database="neo4j",
                )
                assert set(observed) == {"neo4j", "bloodhound"}
            else:
                with pytest.raises(NativeBackendError):
                    validate_database_access_observation(
                        supplied, databases=("neo4j", "bloodhound"), home_database="neo4j",
                    )
    events = _driver_double(monkeypatch, [], [], database="system", control_rows=rows)
    report = asyncio.run(observe_bolt_database_access(
        uri="bolt://test.invalid:7687", username="fixture", password="private-password",
        databases=("neo4j", "bloodhound"),
    ))
    assert report["read_only_database_access_observed"]
    assert not report["read_only_privileges_verified"] and not report["quiescence_verified"]
    assert not report["standalone_topology_verified"] and not report["campaign_admitted"]
    assert "private-password" not in json.dumps(report)
    assert "rollback" in events
    assert events[-2:] == ["session_closed", "driver_closed"]
    privileges = [{"access": "GRANTED", "action": "access", "resource": "database",
                   "graph": "*", "segment": "database", "role": "custom", "user": "fixture",
                   "immutable": False}]
    for row in rows:
        row.update(role="primary", writer=True, currentPrimariesCount=1,
                   currentSecondariesCount=0, requestedPrimariesCount=1,
                   requestedSecondariesCount=0, replicationLag=0)
    events = _driver_double(monkeypatch, [], [], database="system", control_rows=rows,
                            privilege_rows=privileges)
    report = asyncio.run(observe_bolt_database_access(
        uri="bolt://test.invalid:7687", username="fixture", password="private-password",
        databases=("neo4j", "bloodhound"), require_read_only_privileges=True,
        require_supported_topology=True,
    ))
    assert report["read_only_privileges_verified"] and report["privileges"] == privileges
    assert report["serving_topology"]["shape"] == "single-primary-no-secondaries"
    assert report["transaction_timeout"] == [{"name": "db.transaction.timeout", "value": "60s"}]
    query = next(event for event in events
                 if isinstance(event, str) and event.startswith("SHOW DATABASES"))
    assert all("currentPrimariesCount" in part for part in query.split(" RETURN "))
    assert "SHOW USER PRIVILEGES" in events and "rollback" in events
    assert not report["campaign_admitted"]


def test_native_database_interval_covers_every_destination(monkeypatch, subtests):
    from ori.eval.v2 import native_bolt_runtime as runtime

    expected, _, _ = _fixture()
    for implementation, databases in (("mordavid", ("neo4j", "bloodhound")),
                                      ("armadin", ("neo4j",))):
        for case in ("valid", "drift", "connection", "graph", "missing_fallback"):
            with subtests.test(implementation=implementation, case=case):
                calls = []
                control = {"server_address": ["fixture", 7687], "server_agent": "Neo4j/test",
                           "protocol_version": [5, 8], "transaction": 10,
                           "read_only_privileges_verified": True}

                async def access(**kwargs):
                    calls.append("access")
                    assert kwargs["databases"] == databases
                    assert kwargs["require_read_only_privileges"] is True
                    assert kwargs["home_database"] == (
                        "neo4j" if implementation == "armadin" else None
                    )
                    return {**control, "transaction": 11 if case == "drift"
                            and calls.count("access") == 2 else 10}

                async def graph(**kwargs):
                    calls.append(kwargs["database"])
                    assert kwargs["expected"] is expected
                    assert kwargs["page_size"] == 7
                    assert kwargs["transaction_timeout_seconds"] == 11.0
                    assert kwargs["auxiliary_labels"] == ("Base",)
                    if case == "graph":
                        raise runtime.NativeBackendError("NATIVE_BACKEND_GRAPH_MISMATCH")
                    return {"connection_observation": {
                        **control, "server_agent": "different" if case == "connection"
                        else control["server_agent"],
                    }}

                monkeypatch.setattr(runtime, "observe_bolt_database_access", access)
                monkeypatch.setattr(runtime, "verify_bolt_graph", graph)
                options = dict(implementation_id=implementation, uri="bolt://fixture",
                               username="fixture", password="private", expected=expected,
                               databases=() if case == "missing_fallback" else databases,
                               page_size=7, transaction_timeout_seconds=11.0,
                               auxiliary_labels=("Base",))
                if case == "valid":
                    result = asyncio.run(runtime.verify_native_bolt_read_only_graphs(**options))
                    assert calls == ["access", *databases, "access"]
                    assert result["bounded_database_stability_observed"]
                    assert not result["campaign_admitted"] and not result["quiescence_verified"]
                else:
                    with pytest.raises(runtime.NativeBackendError):
                        asyncio.run(runtime.verify_native_bolt_read_only_graphs(**options))
                    if case == "missing_fallback":
                        assert not calls


def test_native_transaction_timeout_requires_one_positive_bounded_observation(subtests):
    from ori.eval.v2.native_bolt_runtime import (
        NativeBackendError,
        validate_native_transaction_timeout,
    )

    for value, seconds in (("60s", 60), ("1m", 60), ("1500ms", 1.5), ("1s500ms", 1.5)):
        with subtests.test(value=value):
            assert validate_native_transaction_timeout([
                {"name": "db.transaction.timeout", "value": value},
            ]) == seconds
    row = {"name": "db.transaction.timeout", "value": "60s"}
    for rows in ([], [row, row], [{**row, "name": "other"}],
                 *([{**row, "value": value}] for value in
                   ("0s", "61s", "1h", "-1s", "nan", "1s extra", 60, None, ""))):
        with subtests.test(rows=rows):
            with pytest.raises(NativeBackendError):
                validate_native_transaction_timeout(rows)


def test_native_privilege_gate_rejects_authority_outside_supported_reads(subtests):
    from ori.eval.v2.native_bolt_runtime import NativeBackendError, validate_read_only_privileges

    rows = [{"access": "GRANTED", "action": action, "resource": resource,
             "graph": "*", "segment": segment, "role": "custom", "user": "fixture",
             "immutable": False} for action, resource, segment in (
                 ("access", "database", "database"),
                 ("match", "all_properties", "NODE(*)"),
                 ("match", "all_properties", "RELATIONSHIP(*)"),
             )]
    options = dict(username="fixture", databases=("neo4j", "bloodhound"), home_database="neo4j")
    assert validate_read_only_privileges(rows, **options) == validate_read_only_privileges(
        list(reversed(rows)), **options,
    )
    # DBMS administrative columns are uninterpreted observations, not invented renderer values.
    setting = {**rows[0], "action": "show_setting", "resource": None, "graph": None,
               "segment": "SETTING(db.transaction.timeout)"}
    assert setting in validate_read_only_privileges([*rows, setting], **options)
    for change in ({"segment": "SETTING(*)"}, {"action": "show_settings"},
                   {"segment": "SETTING(db.*)"}, {"graph": []}, {"resource": {}}):
        with subtests.test(setting=change):
            with pytest.raises(NativeBackendError):
                validate_read_only_privileges([*rows, {**setting, **change}], **options)
    for change in (
        {"action": "write"}, {"action": "execute", "segment": "PROCEDURE(*)"},
        {"action": "dbms_actions"}, {"resource": "dbms"}, {"user": "other"},
        {"graph": "DEFAULT"}, {"graph": "HOME"}, {"immutable": "false"},
        {"unrecognized": "extra"},
    ):
        with subtests.test(change=change):
            with pytest.raises(NativeBackendError):
                validate_read_only_privileges([{**rows[0], **change}, *rows[1:]], **options)
    with pytest.raises(NativeBackendError):
        validate_read_only_privileges([], **options)


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


def test_native_scope_cli_routes_all_destinations_and_fails_closed(tmp_path, monkeypatch, subtests):
    from click.testing import CliRunner

    from ori.cli import main
    from ori.eval.v2 import graph
    from ori.eval.v2 import native_bolt_runtime as runtime

    expected, _, _ = _fixture()
    manifest, archive = tmp_path / "source.json", tmp_path / "source.zip"
    manifest.write_text("{}")
    archive.write_bytes(b"fixture")
    monkeypatch.setattr(graph, "build_archive_snapshot", lambda *args, **kwargs: expected)
    for implementation, prefix, databases in (
        ("mordavid", "BLOODHOUND", ("neo4j", "bloodhound")),
        ("armadin", "NEO4J", ("fixture-home",)),
    ):
        monkeypatch.setenv(f"{prefix}_URI", "bolt://fixture.invalid")
        monkeypatch.setenv(f"{prefix}_USERNAME", prefix)
        monkeypatch.setenv(f"{prefix}_PASSWORD", "private-test-secret")
        for case in ("valid", "drift", "privilege", "graph", "timeout-config"):
            with subtests.test(implementation=implementation, case=case):
                calls = []
                observation = dict(server_address=["fixture", 7687], server_agent="Neo4j/test",
                                   protocol_version=[5, 8], read_only_privileges_verified=True)

                async def access(**kwargs):
                    calls.append("access")
                    assert kwargs["username"] == prefix
                    assert kwargs["databases"] == databases
                    assert kwargs["home_database"] == (
                        databases[0] if implementation == "armadin" else None
                    )
                    if case == "privilege":
                        raise runtime.NativeBackendError("NATIVE_PRIVILEGE_GRANT_UNSUPPORTED")
                    return {**observation, "transaction": len(calls) if case == "drift" else 1}

                async def verify(**kwargs):
                    calls.append(kwargs["database"])
                    assert kwargs["expected"] is expected and kwargs["page_size"] == 3
                    assert kwargs["transaction_timeout_seconds"] == 9.0
                    assert kwargs["auxiliary_labels"] == ("Base",)
                    if case == "graph":
                        raise runtime.NativeBackendError("NATIVE_BACKEND_GRAPH_MISMATCH")
                    return {"connection_observation": observation}

                monkeypatch.setattr(runtime, "observe_bolt_database_access", access)
                monkeypatch.setattr(runtime, "verify_bolt_graph", verify)
                output = tmp_path / f"{implementation}-{case}.private.json"
                command = [
                    "verify-native-graph", "--manifest", str(manifest), "--archive", str(archive),
                    "--product", "simple", "--implementation", implementation,
                    "--database", databases[0], "--read-only-scope", "--output", str(output),
                    "--page-size", "3", "--auxiliary-label", "Base",
                    "--transaction-timeout-seconds", "9", "--timeout-seconds",
                    "8" if case == "timeout-config" else "90",
                ]
                result = CliRunner().invoke(main, command)
                assert "private-test-secret" not in result.output
                if case == "valid":
                    assert result.exit_code == 0, result.output
                    assert calls == ["access", *databases, "access"]
                    assert "NATIVE READ-ONLY SCOPE OBSERVATIONS: PASS" in result.output
                    report = json.loads(output.read_text())
                    assert set(report["graphs"]) == set(databases)
                    assert report["read_only_privileges_verified"]
                    assert not report["campaign_admitted"] and not report["quiescence_verified"]
                    assert output.stat().st_mode & 0o777 == 0o600
                    assert "private-test-secret" not in output.read_text()
                    repeat = CliRunner().invoke(main, command)
                    assert repeat.exit_code != 0 and "OUTPUT_EXISTS" in repeat.output
                    assert calls == ["access", *databases, "access"]
                else:
                    assert result.exit_code != 0 and "PASS" not in result.output
                    assert not output.exists()
                    if case == "timeout-config":
                        assert not calls
                if implementation == "mordavid":
                    command[command.index("--database") + 1] = "bloodhound"
                    rejected = CliRunner().invoke(main, command)
                    assert rejected.exit_code != 0
                    assert "NATIVE_DATABASE_ACCESS_SCOPE_INVALID" in rejected.output


def test_native_graph_command_real_archive_and_ce_derived_inventory(tmp_path, monkeypatch):
    pytest.importorskip("neo4j")
    from click.testing import CliRunner

    from ori.cli import main
    from ori.eval.v2.graph import build_archive_snapshot
    from ori.generator.archive_validation import _relationships_from_archive
    from ori.generator.graph import ADGraph
    from ori.generator.org import build_org
    from ori.generator.security import apply_baseline_security
    from ori.generator.serializer import _build_zip, project_nodes_for_sharphound

    graph = ADGraph("TEST.LOCAL", seed=67)
    build_org(graph, num_users=2, num_workstations=1, num_servers=1)
    apply_baseline_security(graph)
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
