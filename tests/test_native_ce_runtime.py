"""Native CE qualification uses explicit routing and owned fixture-only intervals."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from ori.eval.v2.fingerprint import canonical_sha256
from ori.eval.v2.native_ce_runtime import native_ce_connection, qualify_native_ce_corpus
from tests.support.v2_mcp import native_profile


def test_completed_ce_qualification_bindings(monkeypatch, subtests):
    """CE-specific replay rejects changed work, graph, target and privilege claims."""
    from copy import deepcopy

    from ori.eval.v2 import native_mcp_runtime
    from ori.eval.v2.graph import LiveGraphVerification
    from ori.eval.v2.native_ce_runtime import (
        CE_QUALIFICATION_KIND,
        validate_completed_native_ce_qualification,
    )

    _, _, binding = native_ce_connection({
        "BLOODHOUND_DOMAIN": "fixture.invalid", "BLOODHOUND_TOKEN_ID": "fixture-id",
        "BLOODHOUND_TOKEN_KEY": "fixture-key",
    })
    payload = native_profile("mwnickerson").model_dump(mode="python")
    payload["backend_binding_fingerprint"] = canonical_sha256(binding)
    payload["profile_fingerprint"] = canonical_sha256(
        payload, exclude_fields=("profile_id", "profile_fingerprint"),
    )
    payload["profile_id"] = "ori-native-mwnickerson-v1-" + payload["profile_fingerprint"]
    profile = type(native_profile("mwnickerson")).model_validate(payload)
    expected = SimpleNamespace(graph_fingerprint="a" * 64, entities=(), relationships=())
    graph = dict(schema_version="ori-live-graph-verification-v2",
                 expected_graph_fingerprint=expected.graph_fingerprint,
                 observed_graph_fingerprint=expected.graph_fingerprint,
                 page_size=500, object_queries=1, relationship_queries=1,
                 object_count=0, relationship_count=0, normalized_artifacts=())
    graph["verification_fingerprint"] = canonical_sha256(graph)
    graph = LiveGraphVerification.model_validate(graph).model_dump(mode="json")
    work = {"tasks": []}
    report = dict(
        qualification_kind=CE_QUALIFICATION_KIND, backend_binding=binding,
        implementation_id="mwnickerson", capability_profile_fingerprint=profile.profile_fingerprint,
        scope="completed-native-session-interval", session_cleanup_confirmed=True,
        graph_before={"graph_verification": graph}, graph_after={"graph_verification": graph},
        qualification_work_fingerprint=canonical_sha256(work),
        read_only_privileges_verified=False, continuous_quiescence_verified=False,
        campaign_admitted=False,
    )
    calls = []

    def runtime(record, **kwargs):
        calls.append(record)
        assert kwargs["profile"] == profile
        if record.get("reject_runtime"):
            raise ValueError("private runtime diagnostic")

    # The shared runtime validator has independent source/startup/discovery tests.
    monkeypatch.setattr(native_mcp_runtime, "validate_native_session_observations", runtime)
    for case in ("valid", "kind", "cleanup", "privileges", "quiescence", "admission",
                 "work", "target", "graph", "runtime"):
        with subtests.test(case=case):
            changed = deepcopy(report)
            if case == "kind":
                changed["qualification_kind"] = "bolt"
            elif case == "cleanup":
                changed["session_cleanup_confirmed"] = False
            elif case in {"privileges", "quiescence", "admission"}:
                key = {"privileges": "read_only_privileges_verified",
                       "quiescence": "continuous_quiescence_verified",
                       "admission": "campaign_admitted"}[case]
                changed[key] = True
            elif case == "work":
                changed["qualification_work_fingerprint"] = "b" * 64
            elif case == "target":
                changed["backend_binding"]["domain"] = "other.invalid"
            elif case == "graph":
                changed["graph_after"]["graph_verification"]["object_count"] = 1
            elif case == "runtime":
                changed["reject_runtime"] = True
            if case == "valid":
                assert validate_completed_native_ce_qualification(
                    changed, profile=profile, expected=expected, work_result=work,
                ) == canonical_sha256(changed)
                assert calls
            else:
                with pytest.raises(ValueError, match="^NATIVE_COMPLETED_CE_QUALIFICATION_INVALID$"):
                    validate_completed_native_ce_qualification(
                        changed, profile=profile, expected=expected, work_result=work,
                    )


@pytest.mark.parametrize(
    "case", ["valid", "graph_before", "work", "cleanup", "graph_after", "cancel"],
)
@pytest.mark.parametrize("fixture_qualification", [True, False])
def test_native_ce_owned_interval(monkeypatch, case, fixture_qualification, subtests):
    from ori.eval.v2 import live_projection, native_ce_runtime, native_mcp_runtime
    from ori.eval.v2.native_capability import build_native_capability_profile
    from ori.eval.v2.native_mcp_profiles import get_native_implementation

    source = get_native_implementation("mwnickerson")

    connection = {"BLOODHOUND_DOMAIN": "fixture.invalid", "BLOODHOUND_TOKEN_ID": "fixture-id",
                  "BLOODHOUND_TOKEN_KEY": "fixture-key"}
    child, options, binding = native_ce_connection(connection)
    profile = native_profile("mwnickerson")
    payload = profile.model_dump(mode="python")
    payload["backend_binding_fingerprint"] = canonical_sha256(binding)
    payload["profile_fingerprint"] = canonical_sha256(
        payload, exclude_fields=("profile_id", "profile_fingerprint"),
    )
    payload["profile_id"] = "ori-native-mwnickerson-v1-" + payload["profile_fingerprint"]
    profile = type(profile).model_validate(payload)
    config = SimpleNamespace(implementation_id="mwnickerson",
                             runtime_fingerprint=profile.runtime_fingerprint,
                             dependency_lock_fingerprint=profile.dependency_lock_fingerprint)
    events = []
    coordinators = []

    class Client:
        def __init__(self, **kwargs):
            assert kwargs == options
            for key, value in kwargs.items():
                setattr(self, key, value)

        async def __aenter__(self):
            events.append("client")
            return self

        async def __aexit__(self, *args):
            events.append("client_closed")

    async def graph(*args, **kwargs):
        events.append("graph")
        if (case == "graph_before" and events.count("graph") == 1
                or case == "graph_after" and events.count("graph") == 2):
            raise ValueError("fixture graph mismatch")
        return {"graph_verification": "fixture"}

    @asynccontextmanager
    async def owner(*args, **kwargs):
        assert kwargs["connection"] == child and kwargs["max_calls"] == 7
        assert kwargs["session_call_timeout_seconds"] == (60.0 if fixture_qualification else 125.0)
        coordinators.append(kwargs.get("query_coordinator"))
        events.append("session")
        try:
            session = native_mcp_runtime.NativeMCPSession(
                object(), "mwnickerson",
                [{"name": name, "inputSchema": {"type": "object"}}
                 for name in source.native_tool_names],
                [{"name": name} for name in source.prompt_names],
                [{"uri": uri, "name": uri} for uri in source.resource_uris],
                [], guard=kwargs["guard"], capability_profile=profile,
            )
            assert "file_upload" not in {tool["name"] for tool in session.tools}
            yield session, {"fixture": True}
        finally:
            events.append("session_closed")
            if case == "cleanup":
                raise ValueError("fixture cleanup")

    def runtime(observation, **kwargs):
        assert kwargs["profile"] == profile
        assert kwargs["backend_binding_fingerprint"] == canonical_sha256(binding)
        # Startup/graph I/O is synthetic; actual complete inventory must rebuild
        # the same immutable profile while provider-safe tools omit file_upload.
        assert build_native_capability_profile(
            "mwnickerson", runtime_fingerprint=profile.runtime_fingerprint,
            dependency_lock_fingerprint=profile.dependency_lock_fingerprint,
            backend_binding_fingerprint=canonical_sha256(binding),
            **observation["native_discovery"],
        ) == profile
        events.append("runtime")

    async def work(**kwargs):
        events.append("work")
        if case == "cancel":
            raise asyncio.CancelledError
        if case == "work":
            raise ValueError("fixture work")
        return {"tasks": []}

    monkeypatch.setattr(native_ce_runtime, "BHCEClient", Client)
    monkeypatch.setattr(native_ce_runtime, "observe_native_ce_graph", graph)
    monkeypatch.setattr(native_mcp_runtime, "open_native_mcp_session", owner)
    monkeypatch.setattr(native_mcp_runtime, "validate_native_session_observations", runtime)
    monkeypatch.setattr(live_projection, "native_qualification_guard",
                        lambda **_: (lambda *args: True, 7))
    monkeypatch.setattr(live_projection, "verify_native_corpus_interoperability", work)
    async def generic_work(session):
        return await work(session=session)

    arguments = dict(config=config, profile=profile, connection=connection,
                     private_stderr=None, expected=None)
    if fixture_qualification:
        operation = qualify_native_ce_corpus(corpus=None, offline=None, **arguments)
    else:
        arguments.update(guard=lambda *args: True, max_calls=7, work=generic_work,
                         timeout_seconds=1200.0, session_call_timeout_seconds=125.0)
        operation = native_ce_runtime.run_native_ce_session_work(**arguments)
    if case == "valid":
        result, report = asyncio.run(operation)
        assert events == ["client", "graph", "session", "runtime", "work",
                          "session_closed", "graph", "client_closed"]
        if fixture_qualification:
            assert report["qualification_work_fingerprint"] == canonical_sha256(result)
            assert report["qualification_kind"] == native_ce_runtime.CE_QUALIFICATION_KIND
        else:
            assert "qualification_work_fingerprint" not in report
            assert "qualification_kind" not in report
            for hook_case in ("valid", "reject", "cancel", "cleanup", "graph_after"):
                with subtests.test(before_work=hook_case):
                    events.clear()
                    case = hook_case if hook_case in {"cleanup", "graph_after"} else "valid"

                    async def before_work(observed):
                        assert events == ["client", "graph", "session", "runtime"]
                        assert observed["backend_binding"] == binding
                        assert observed["capability_profile_fingerprint"] == (
                            profile.profile_fingerprint
                        )
                        assert observed["implementation_id"] == "mwnickerson"
                        assert observed["runtime"] == {"fixture": True}
                        assert observed["graph_before"] == {"graph_verification": "fixture"}
                        assert "graph_after" not in observed
                        assert "session_cleanup_confirmed" not in observed
                        events.append("before_work")
                        observed["runtime"]["fixture"] = False
                        observed["native_discovery"]["tools"].append({"name": "invented"})
                        observed["graph_before"]["graph_verification"] = "changed"
                        observed["backend_binding"].clear()
                        if hook_case == "cancel":
                            raise asyncio.CancelledError
                        if hook_case != "valid":
                            raise ValueError("fixture admission")

                    operation = native_ce_runtime.run_native_ce_session_work(
                        **arguments, before_work=before_work,
                    )
                    if hook_case == "valid":
                        _, observed = asyncio.run(operation)
                        assert observed["runtime"] == {"fixture": True}
                        assert {item["name"] for item in observed["native_discovery"]["tools"]} == (
                            set(source.native_tool_names)
                        )
                        assert observed["graph_before"] == {"graph_verification": "fixture"}
                        assert observed["backend_binding"] == binding
                        assert events == ["client", "graph", "session", "runtime", "before_work",
                                          "work", "session_closed", "graph", "client_closed"]
                    else:
                        expected_error = (asyncio.CancelledError if hook_case == "cancel"
                                          else ValueError)
                        with pytest.raises(expected_error) as failure:
                            asyncio.run(operation)
                        if hook_case in {"cleanup", "graph_after"}:
                            assert "fixture admission" not in str(failure.value)
                        assert "work" not in events and "session_closed" in events
                        assert events[-1] == "client_closed"
                        assert events.count("graph") == (1 if hook_case == "cleanup" else 2)
                    case = "valid"
        assert not report["read_only_privileges_verified"]
        assert not report["continuous_quiescence_verified"] and not report["campaign_admitted"]
        if not fixture_qualification:
            for key, invalid in (("max_calls", 0), ("max_calls", True), ("page_size", 0),
                                 ("page_size", 0.5), ("work", None), ("guard", None),
                                 ("before_work", False),
                                 ("timeout_seconds", float("nan")),
                                 ("session_call_timeout_seconds", float("inf")),
                                 ("session_call_timeout_seconds", True),
                                 ("session_call_timeout_seconds", 0),
                                 ("timeout_seconds", True)):
                with subtests.test(key=key, invalid=invalid):
                    events.clear()
                    with pytest.raises(ValueError, match="^NATIVE_CE_.*_INVALID$"):
                        asyncio.run(native_ce_runtime.run_native_ce_session_work(
                            **{**arguments, key: invalid},
                        ))
                    assert not events
            from ori.eval.direct_query_safety import (
                DirectQueryCoordinator,
                DirectQuerySafetyConfig,
                QueryDenyCache,
            )

            safety = DirectQuerySafetyConfig()
            shared = Client(**options)
            cache = QueryDenyCache(None, manifest_fingerprint="a" * 64,
                                   policy_version=safety.policy_version)
            coordinator = DirectQueryCoordinator(bhce=shared, config=safety, deny_cache=cache)
            coordinator.circuit_open = True
            coordinator.circuit_reason = "fixture pending query"
            cache.record("b" * 64, rule="native_interrupted", detail="fixture")
            for _ in range(2):
                events.clear()
                asyncio.run(native_ce_runtime.run_native_ce_session_work(
                    **arguments, query_coordinator=coordinator,
                ))
                assert events == ["graph", "session", "runtime", "work",
                                  "session_closed", "graph"]
                assert coordinators[-1] is coordinator
                assert coordinator.bhce is shared and coordinator.deny_cache is cache
                assert coordinator.circuit_open and "b" * 64 in cache.entries
            for field, changed in (("domain", "other.invalid"), ("trust_env", True),
                                   ("token_key", "different"), ("verify_tls", False)):
                with subtests.test(shared_client_field=field):
                    events.clear()
                    prior = getattr(shared, field)
                    setattr(shared, field, changed)
                    with pytest.raises(ValueError, match="^NATIVE_CE_COORDINATOR_BINDING_INVALID$"):
                        asyncio.run(native_ce_runtime.run_native_ce_session_work(
                            **arguments, query_coordinator=coordinator,
                        ))
                    setattr(shared, field, prior)
                    assert not events
    else:
        with pytest.raises(asyncio.CancelledError if case == "cancel" else ValueError):
            asyncio.run(operation)
        assert events[-1] == "client_closed"
        if "session" in events:
            assert "session_closed" in events
