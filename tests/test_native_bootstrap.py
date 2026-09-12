"""Bootstrap orchestration tests; backend/source verification is mocked explicitly."""

import asyncio
import hashlib
import json
from contextlib import asynccontextmanager

import pytest

from ori.eval.v2 import native_bootstrap as bootstrap
from ori.eval.v2.native_capability import NativeCapabilityProfile
from ori.eval.v2.native_mcp_runtime import NativeSessionCleanupPending
from ori.mcp_launcher import NativeMCPLauncherConfig
from ori.native_runtime import fingerprint_native_runtime_roots


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    python = runtime / "python"
    python.write_text("fixture bytes, never executed")
    python.chmod(0o700)
    lock = tmp_path / "requirements.txt"
    lock.write_text("fixture==1.0\n")
    manifest, archive = tmp_path / "manifest.json", tmp_path / "archive.zip"
    manifest.write_text("{}")
    archive.write_bytes(b"archive fixture")
    def archive_snapshot(*args, **kwargs):
        assert kwargs["product"] == "oaic-2026-v1"
        return object()

    monkeypatch.setattr(bootstrap, "build_archive_snapshot", archive_snapshot)
    config = NativeMCPLauncherConfig(
        implementation_id="mordavid", checkout=checkout, python_executable=python,
        runtime_roots=(runtime,), runtime_fingerprint=fingerprint_native_runtime_roots((runtime,)),
        dependency_lock=lock,
        dependency_lock_fingerprint=hashlib.sha256(lock.read_bytes()).hexdigest(),
    )
    return dict(config=config, source_manifest_path=manifest, archive_path=archive,
                product="oaic-2026-v1", databases=("neo4j", "bloodhound"),
                output_dir=tmp_path / "output")


def test_offline_bootstrap_never_starts_process_or_service(inputs, monkeypatch):
    import subprocess

    def forbidden(*args, **kwargs):
        pytest.fail("offline bootstrap invoked a process or service")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(bootstrap, "open_native_mcp_session", forbidden)
    monkeypatch.setattr(bootstrap, "verify_native_bolt_read_only_graphs", forbidden)
    monkeypatch.setattr(bootstrap, "BHCEClient", forbidden)
    result = asyncio.run(bootstrap.discover_native_profile_files(**inputs))
    assert result["status"] == "offline_inputs_checked"
    assert result["provider_calls"] == 0 and result["source_bytes_verified"] is False
    assert not inputs["output_dir"].exists()
    inputs["config"].dependency_lock.write_text("changed==2\n")
    with pytest.raises(ValueError, match="LOCK_CHANGED"):
        asyncio.run(bootstrap.discover_native_profile_files(**inputs))


@pytest.mark.parametrize("implementation,case", [
    (implementation, case) for implementation in ("mwnickerson", "mordavid", "armadin")
    for case in ("valid", "before", "after", "discovery", "cleanup", "pending",
                 "cancel", "runtime", "existing", "access_drift")
    if case != "access_drift" or implementation != "mwnickerson"
])
def test_observed_bootstrap_requires_complete_owned_interval(inputs, monkeypatch, capsys,
                                                           implementation, case):
    from dataclasses import replace

    source = bootstrap.get_native_implementation(implementation)
    inputs["config"] = replace(inputs["config"], implementation_id=implementation)
    inputs["databases"] = (() if source.backend == "bhce" else
                           ("neo4j", "bloodhound") if implementation == "mordavid" else ("home",))
    inputs["execute"] = True
    for prefix in ("BLOODHOUND", "NEO4J"):
        for key, value in {"URI": "bolt://fixture.invalid", "USERNAME": "reader",
                           "PASSWORD": "private-password", "DOMAIN": "fixture.invalid",
                           "TOKEN_ID": "reader", "TOKEN_KEY": "private-key"}.items():
            monkeypatch.setenv(f"{prefix}_{key}", value)
    events = []
    if case == "existing":
        inputs["output_dir"].mkdir()
        (inputs["output_dir"] / "operator.txt").write_text("preserve")

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False

        async def __aenter__(self):
            events.append("client")
            return self

        async def __aexit__(self, *args):
            events.append("client_closed")

    monkeypatch.setattr(bootstrap, "BHCEClient", Client)
    async def observe(*args, **kwargs):
        events.append("graph")
        if source.backend == "neo4j":
            assert kwargs["require_supported_topology"] is True
            assert kwargs["databases"] == inputs["databases"]
        if (case == "before" and events.count("graph") == 1
                or case == "after" and events.count("graph") == 2):
            raise ValueError("fixture graph rejection")
        return {"access_before": {"fixture": True}, "access_after": {
            "fixture": not (case == "access_drift" and events.count("graph") == 2),
        }}

    monkeypatch.setattr(bootstrap, "observe_native_ce_graph", observe)
    monkeypatch.setattr(bootstrap, "verify_native_bolt_read_only_graphs", observe)
    @asynccontextmanager
    async def owner(config, **kwargs):
        events.append("session")
        assert kwargs["capability_profile"] is None
        assert not kwargs["guard"]("anything", "anything", {}).allowed
        from ori.eval.v2.native_mcp_runtime import NativeMCPSession

        session = NativeMCPSession(
            object(), source.implementation_id,
            [{"name": name, "inputSchema": {"type": "object"}}
             for name in source.native_tool_names],
            [{"name": name} for name in source.prompt_names],
            [{"uri": uri, "name": uri} for uri in source.resource_uris],
            [], guard=kwargs["guard"],
        )
        assert {tool["name"] for tool in session.discovered_tools} == set(source.native_tool_names)
        if source.implementation_id == "mwnickerson":
            assert "file_upload" not in {tool["name"] for tool in session.tools}
        if case == "discovery":
            session._tools["invented"] = {"name": "invented", "inputSchema": {"type": "object"}}
        try:
            yield session, {"fixture_runtime": True}
        finally:
            if case == "pending":
                async def drain():
                    await asyncio.sleep(0)
                    assert "client_closed" not in events
                    assert not kwargs["private_stderr"].closed
                    assert not (
                        inputs["output_dir"] / "native-capability-profile-v1.private.json"
                    ).exists()
                    with pytest.raises(ValueError):
                        with bootstrap._exclusive_output_dir_lock(inputs["output_dir"]):
                            pytest.fail("pending cleanup released output lock")
                    events.append("closed")
                    raise RuntimeError("fixture retained worker error")

                raise NativeSessionCleanupPending(
                    asyncio.create_task(drain()), cancellation_requested=True,
                    startup_timed_out=False,
                )
            events.append("closed")
            if case == "cleanup":
                raise ValueError("fixture cleanup failure")

    monkeypatch.setattr(bootstrap, "open_native_mcp_session", owner)
    def validate(observation, **kwargs):
        # Real source/startup schema replay is tested in the owned-runtime suite.
        # This test retains actual descriptor/profile validation above.
        assert observation["runtime"] == {"fixture_runtime": True}
        if case == "cancel":
            raise asyncio.CancelledError
        if case == "runtime":
            raise ValueError("fixture runtime validation failure")
        assert kwargs["profile"].backend_binding_fingerprint == (
            kwargs["backend_binding_fingerprint"]
        )

    monkeypatch.setattr(bootstrap, "validate_native_session_observations", validate)
    if case == "valid":
        result = asyncio.run(bootstrap.discover_native_profile_files(**inputs))
        assert result["status"] == "native_profile_observed" and not result["campaign_admitted"]
        assert result["tool_calls"] == result["prompt_reads"] == result["resource_reads"] == 0
        profile = NativeCapabilityProfile.model_validate_json((
            inputs["output_dir"] / "native-capability-profile-v1.private.json"
        ).read_text())
        assert profile.implementation_id == implementation
        observations = json.loads((
            inputs["output_dir"] / "native-discovery-v1.private.json"
        ).read_text())
        assert observations["session_cleanup_confirmed"] and not observations["campaign_admitted"]
        assert events.index("closed") < len(events) - 1
        assert "private-password" not in json.dumps(observations)
        assert "private-key" not in json.dumps(observations)
    else:
        error = (NativeSessionCleanupPending if case == "pending" else
                 asyncio.CancelledError if case == "cancel" else
                 FileExistsError if case == "existing" else ValueError)
        with pytest.raises(error):
            asyncio.run(bootstrap.discover_native_profile_files(**inputs))
        assert not (inputs["output_dir"] / "native-capability-profile-v1.private.json").exists()
        if case == "pending":
            assert "closed" in events
            assert "NATIVE_SESSION_CLEANUP_PENDING" in capsys.readouterr().err
        if case == "existing":
            assert not events
            assert (inputs["output_dir"] / "operator.txt").read_text() == "preserve"
