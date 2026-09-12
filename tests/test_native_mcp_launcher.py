"""Offline preparation never launches native servers or qualifies dependencies."""

import asyncio
import logging
import os
import subprocess
import sys
import tempfile
import threading
from contextlib import asynccontextmanager, contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest

from ori.eval.v2 import native_mcp_profiles
from ori.mcp_launcher import (
    MCPLaunchSpec,
    NativeMCPLauncherConfig,
    native_mcp_subprocess_env,
    prepare_native_mcp_launch,
)


def test_slow_native_inspection_remains_owned_without_late_spawn(monkeypatch):
    from ori.eval.v2 import native_mcp_runtime as runtime

    release = threading.Event()
    entered = threading.Event()
    monkeypatch.setattr(runtime, "prepare_native_mcp_launch", lambda _: (
        MCPLaunchSpec("unused", (), None), {},
    ))

    def slow_probe(**kwargs):
        entered.set()
        assert release.wait(5)
        return {}

    monkeypatch.setattr("ori.native_runtime.inspect_native_python_startup", slow_probe)
    spawned = []
    monkeypatch.setattr(runtime, "stdio_client", lambda *a, **kw: spawned.append(True))
    config = SimpleNamespace(
        implementation_id="mordavid", runtime_roots=("fixture",),
        python_executable="fixture", checkout="fixture", runtime_fingerprint="fixture",
    )

    async def scenario():
        with tempfile.TemporaryFile(mode="w+") as log:
            async def start():
                async with runtime.open_native_mcp_session(
                    config, connection={"BLOODHOUND_URI": "bolt://fixture",
                                        "BLOODHOUND_USERNAME": "fixture",
                                        "BLOODHOUND_PASSWORD": "fixture"},
                    guard=lambda *_: None, private_stderr=log,
                    startup_timeout=0.05, cleanup_timeout=0.01,
                ):
                    pytest.fail("timed-out inspection launched a session")

            task = asyncio.create_task(start())
            try:
                for _ in range(100):
                    if entered.is_set():
                        break
                    await asyncio.sleep(0.001)
                assert entered.is_set()
                await asyncio.sleep(0.001)  # event loop remains responsive
                with pytest.raises(runtime.NativeSessionCleanupPending) as failure:
                    await asyncio.wait_for(task, 1)
                worker = failure.value.cleanup_task
                assert failure.value.startup_timed_out
                assert worker in runtime._native_cleanup_workers and not worker.done()
                assert not spawned
            finally:
                release.set()
            with pytest.raises(asyncio.CancelledError):
                await worker
            assert not spawned and worker not in runtime._native_cleanup_workers

    asyncio.run(scenario())


@pytest.mark.parametrize("malformed", [False, True, "overflow"])
def test_native_lifecycle_with_real_sdk_and_local_stub(tmp_path, monkeypatch, caplog, malformed):
    from ori.eval.v2 import native_mcp_runtime as runtime

    script = tmp_path / "stub.py"
    script.write_text(
        (
            'print(\'{"jsonrpc":"2.0","id":1,"result":"private-marker"}\', flush=True)\n'
            if malformed is True
            else "import os; os.write(2, b'x' * 2000000)\n" if malformed == "overflow" else ""
        )
        + "from mcp.server.fastmcp import FastMCP\nFastMCP('offline-process-fixture').run()\n"
    )
    spec = MCPLaunchSpec(sys.executable, ("-E", "-s", "-B", str(script)), str(tmp_path))
    monkeypatch.setattr(
        runtime,
        "prepare_native_mcp_launch",
        lambda _: (
            spec,
            {
                "runtime_qualified": False,
                "campaign_admitted": False,
            },
        ),
    )

    async def discover(client, *args, **kwargs):
        assert client.get_server_capabilities() is not None
        assert kwargs["timeout_seconds"] == 125.0
        native = SimpleNamespace(send_ping=client.send_ping, active=True)
        native.invalidate = lambda: setattr(native, "active", False)
        return native

    monkeypatch.setattr(runtime.NativeMCPSession, "discover", discover)

    private_logs = []

    @contextmanager
    def private_log_file():
        with tempfile.TemporaryFile(mode="w+") as stream:
            try:
                yield stream
            finally:
                stream.seek(0)
                private_logs.append(stream.read())

    async def scenario():
        with private_log_file() as private_log:
            async with runtime.open_native_mcp_session(
                SimpleNamespace(implementation_id="mordavid"),
                connection={
                    "BLOODHOUND_URI": "bolt://unused-fixture",
                    "BLOODHOUND_USERNAME": "fixture",
                    "BLOODHOUND_PASSWORD": "fixture",
                },
                guard=lambda *_: None,
                private_stderr=private_log,
                startup_timeout=5.0,
                session_call_timeout_seconds=125.0,
                cleanup_timeout=10.0,
            ) as (client, provenance):
                await client.send_ping()
                assert not provenance["campaign_admitted"]

    if malformed is True:
        with pytest.raises(ValueError, match="START_FAILED"):
            asyncio.run(scenario())
        assert "private-marker" not in caplog.text
        assert "private-marker" in "".join(private_logs)
        logging.getLogger("mcp.client.stdio").error("unrelated-sdk-diagnostic")
        assert "unrelated-sdk-diagnostic" in caplog.text
    elif malformed == "overflow":
        with pytest.raises(ValueError, match="NATIVE_DIAGNOSTIC_LIMIT_EXCEEDED"):
            asyncio.run(scenario())
        assert len("".join(private_logs).encode()) <= 1_048_576
        assert "truncated=true" in "".join(private_logs)
    else:
        asyncio.run(scenario())


@pytest.mark.parametrize(
    "case",
    [
        "complete",
        "discovery_failure",
        "timeout",
        "cancel",
        "drift",
        "cancel_cleanup",
        "cleanup_timeout",
        "cleanup_pending",
        "cancel_cleanup_done",
        "runtime_valid",
        "runtime_drift",
        "runtime_rejected",
    ],
)
def test_native_session_lifecycle_owns_cleanup_and_private_startup(monkeypatch, case):
    from ori.eval.v2 import native_mcp_runtime as runtime

    events = []
    preparations = []
    native = SimpleNamespace(active=True)
    native.invalidate = lambda: setattr(native, "active", False)
    cleanup_started = asyncio.Event()
    cleanup_release = asyncio.Event()
    caller_task = []
    config = SimpleNamespace(implementation_id="mordavid")
    startup_checks = []
    if case.startswith("runtime_"):
        config.runtime_roots = ("fixture",)
        config.python_executable = "fixture-python"
        config.checkout = "fixture-source"
        config.runtime_fingerprint = "fixture-fingerprint"

        def inspect_startup(**kwargs):
            startup_checks.append(kwargs)
            if case == "runtime_rejected":
                raise ValueError("NATIVE_RUNTIME_STARTUP_HOOK_UNSUPPORTED")
            return {"paths": "changed" if case == "runtime_drift"
                    and len(startup_checks) == 2 else "frozen"}

        monkeypatch.setattr("ori.native_runtime.inspect_native_python_startup", inspect_startup)

    def prepare(config):
        preparations.append(config)
        fingerprint = "changed" if case == "drift" and len(preparations) > 2 else "same"
        return MCPLaunchSpec("/explicit/python", ("-B", "native.py"), "/source"), {
            "fingerprint": fingerprint,
            "runtime_qualified": False,
            "campaign_admitted": False,
        }

    @asynccontextmanager
    async def stdio(params, errlog):
        assert params.env["HOME"] == "" and params.env["OPENAI_API_KEY"] == ""
        assert not errlog.closed
        events.append("spawn")
        try:
            yield object(), object()
        finally:
            assert not errlog.closed
            if case == "cleanup_pending":
                errlog.write("private late teardown\n")
            events.append("process_closed")

    class Client:
        def __init__(self, *streams):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            try:
                if case == "cancel_cleanup":
                    cleanup_started.set()
                    await asyncio.sleep(0.05)
                if case == "cleanup_timeout":
                    await asyncio.Event().wait()
                if case == "cleanup_pending":
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        await cleanup_release.wait()
                        raise RuntimeError("late private cleanup error")
                if case == "cancel_cleanup_done":
                    asyncio.get_running_loop().call_soon(caller_task[0].cancel)
            finally:
                events.append("client_closed")

        async def initialize(self):
            if case == "timeout":
                await asyncio.Event().wait()
            events.append("initialized")

    async def discover(*args, **kwargs):
        if case == "discovery_failure":
            raise RuntimeError("private credential and endpoint")
        return native

    monkeypatch.setattr(runtime, "prepare_native_mcp_launch", prepare)
    monkeypatch.setattr(runtime, "stdio_client", stdio)
    monkeypatch.setattr(runtime, "ClientSession", Client)
    monkeypatch.setattr(runtime.NativeMCPSession, "discover", discover)
    monkeypatch.setattr(
        runtime,
        "get_default_environment",
        lambda: {
            "HOME": "/private",
            "OPENAI_API_KEY": "private-key",
        },
    )

    async def scenario():
        caller_task.append(asyncio.current_task())
        with tempfile.TemporaryFile(mode="w+") as private_log:
            async with runtime.open_native_mcp_session(
                config,
                connection={
                    "BLOODHOUND_URI": "bolt://fixture",
                    "BLOODHOUND_USERNAME": "fixture",
                    "BLOODHOUND_PASSWORD": "fixture",
                },
                guard=lambda *_: None,
                private_stderr=private_log,
                # Only the startup-timeout case tests this deadline. Give the
                # thread-backed inspection room to run in cleanup-only cases.
                startup_timeout=0.02 if case == "timeout" else 2.0,
                cleanup_timeout=0.01 if case in {"cleanup_timeout", "cleanup_pending"} else 1.0,
            ) as (session, provenance):
                assert session is native and not provenance["runtime_qualified"]
                if case == "cancel":
                    raise asyncio.CancelledError

    if case == "cleanup_pending":

        async def late_cleanup():
            started = asyncio.get_running_loop().time()
            with pytest.raises(runtime.NativeSessionCleanupPending) as failure:
                await scenario()
            assert asyncio.get_running_loop().time() - started < 1.0
            worker = failure.value.cleanup_task
            assert failure.value.cleanup_timed_out
            assert not failure.value.startup_timed_out
            assert not failure.value.cancellation_requested
            assert not native.active and not worker.done()
            worker_log = runtime._native_cleanup_workers[worker]
            assert not worker_log.closed
            cleanup_release.set()
            with pytest.raises(ValueError, match="NATIVE_SESSION_FAILED"):
                await worker
            assert worker_log.closed and worker not in runtime._native_cleanup_workers

        asyncio.run(late_cleanup())
    elif case == "cancel_cleanup":

        async def interrupt_cleanup():
            task = asyncio.create_task(scenario())
            started = asyncio.create_task(cleanup_started.wait())
            try:
                done, _ = await asyncio.wait(
                    {task, started}, timeout=5.0, return_when=asyncio.FIRST_COMPLETED,
                )
                if task in done:
                    # Surface early startup errors instead of waiting forever
                    # for a cleanup event that can no longer occur.
                    await task
                    pytest.fail("scenario ended before cleanup cancellation")
                assert started in done, "cleanup did not start within the test deadline"
                task.cancel()
                await asyncio.sleep(0.001)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert events[-2:] == ["client_closed", "process_closed"]
            finally:
                for pending in (task, started):
                    if not pending.done():
                        pending.cancel()
                await asyncio.gather(task, started, return_exceptions=True)

        asyncio.run(interrupt_cleanup())
    elif case in {"complete", "runtime_valid"}:
        asyncio.run(scenario())
    elif case in {"cancel", "cancel_cleanup_done"}:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(scenario())
    else:
        expected = {
            "discovery_failure": "START_FAILED",
            "timeout": "START_TIMEOUT",
            "drift": "LAUNCH_CHANGED",
            "cleanup_timeout": "CLEANUP_TIMEOUT",
            "runtime_drift": "STARTUP_CHANGED",
            "runtime_rejected": "START_FAILED",
        }[case]
        with pytest.raises(ValueError, match=expected):
            asyncio.run(scenario())
    if case == "runtime_rejected":
        assert not events
    else:
        assert events[-2:] == ["client_closed", "process_closed"]
    if case in {"runtime_valid", "runtime_drift"}:
        assert len(startup_checks) == 2


@pytest.mark.parametrize("implementation", ["mwnickerson", "mordavid", "armadin"])
def test_native_launch_checks_source_and_keeps_native_entrypoint(
    tmp_path, monkeypatch, implementation
):
    source = native_mcp_profiles.get_native_implementation(implementation)
    checkout = tmp_path / "server"
    checkout.mkdir()
    script = checkout / source.entrypoint
    script.write_text("raise AssertionError('must never launch during preparation')\n")

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(checkout), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init")
    git("add", source.entrypoint)
    git(
        "-c",
        "commit.gpgsign=false",
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-m",
        "fixture",
    )
    pinned = replace(source, revision=git("rev-parse", "HEAD"))
    monkeypatch.setattr(native_mcp_profiles, "get_native_implementation", lambda _: pinned)
    config = NativeMCPLauncherConfig(implementation, checkout, type(checkout)(sys.executable))
    spec, provenance = prepare_native_mcp_launch(config)
    assert spec.args == ("-E", "-s", "-B", str(script))
    assert spec.cwd == str(checkout)
    assert provenance["source_bytes_verified"]
    assert not provenance["runtime_qualified"] and not provenance["campaign_admitted"]

    from ori.native_runtime import fingerprint_native_runtime_roots

    runtime_root = tmp_path / "frozen-runtime"
    runtime_root.mkdir()
    dependency = runtime_root / "dependency.py"
    dependency.write_text("VERSION = 1\n")
    bound = replace(
        config, runtime_roots=(runtime_root,),
        runtime_fingerprint=fingerprint_native_runtime_roots((runtime_root,)),
    )
    _, content_provenance = prepare_native_mcp_launch(bound)
    assert content_provenance["runtime_content_verified"]
    assert not content_provenance["runtime_qualified"]
    dependency.write_text("VERSION = 2\n")
    with pytest.raises(ValueError, match="RUNTIME_CONTENT_MISMATCH"):
        prepare_native_mcp_launch(bound)
    with pytest.raises(ValueError, match="RUNTIME_BINDING_INCOMPLETE"):
        prepare_native_mcp_launch(replace(config, runtime_roots=(runtime_root,)))

    def inaccessible_tree(*args, **kwargs):
        kwargs["onerror"](PermissionError("private filesystem detail"))

    with monkeypatch.context() as patch:
        patch.setattr("ori.mcp_launcher.os.walk", inaccessible_tree)
        with pytest.raises(ValueError, match="TRAVERSAL_FAILED"):
            prepare_native_mcp_launch(config)

    script.write_text("changed native source\n")
    with pytest.raises(ValueError, match="CONTENT_MISMATCH"):
        prepare_native_mcp_launch(config)
    script.write_text("raise AssertionError('must never launch during preparation')\n")
    extra = checkout / "ignored.py"
    extra.write_text("unexpected import\n")
    with pytest.raises(ValueError, match="EXTRA_FILE"):
        prepare_native_mcp_launch(config)
    extra.unlink()
    directory = checkout / "untracked_namespace"
    directory.mkdir()
    with pytest.raises(ValueError, match="EXTRA_DIRECTORY"):
        prepare_native_mcp_launch(config)
    directory.rmdir()
    link = checkout / "external"
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="SYMLINK_FORBIDDEN"):
        prepare_native_mcp_launch(config)
    link.unlink()
    (tmp_path / ".env").write_text("PRIVATE_SETTING=fixture\n")
    with pytest.raises(ValueError, match="DOTENV"):
        prepare_native_mcp_launch(config)


@pytest.mark.parametrize(
    "implementation,prefix",
    [
        ("mwnickerson", "BLOODHOUND"),
        ("mordavid", "BLOODHOUND"),
        ("armadin", "NEO4J"),
    ],
)
def test_native_environment_overrides_sdk_inheritance(implementation, prefix):
    source = native_mcp_profiles.get_native_implementation(implementation)
    endpoint = f"{prefix}_DOMAIN" if implementation == "mwnickerson" else f"{prefix}_URI"
    connection = {
        endpoint: "fixture-endpoint",
        **dict.fromkeys(source.credential_env_names, "fixture"),
    }
    inherited = {
        "HOME": "/private/operator",
        "PATH": "/private/bin",
        "OPENAI_API_KEY": "do-not-forward",
        "PYTHONPATH": "/private/imports",
    }
    actual = native_mcp_subprocess_env(
        implementation, connection=connection, sdk_defaults=inherited
    )
    assert actual["HOME"] == actual["OPENAI_API_KEY"] == actual["PYTHONPATH"] == ""
    assert actual["PATH"] == os.defpath
    assert all(actual[key] == value for key, value in connection.items())
    with pytest.raises(ValueError, match="ENVIRONMENT_INVALID"):
        native_mcp_subprocess_env(implementation, connection={}, sdk_defaults=inherited)
    with pytest.raises(ValueError, match="ENVIRONMENT_INVALID"):
        native_mcp_subprocess_env(
            implementation,
            connection={**connection, "OPENAI_API_KEY": "bad"},
            sdk_defaults=inherited,
        )
