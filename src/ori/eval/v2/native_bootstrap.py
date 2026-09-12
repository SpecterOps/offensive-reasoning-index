"""Portable observed native-profile bootstrap; never qualification or model work."""

from __future__ import annotations

import asyncio
import hashlib
import math
import os
import sys
from contextlib import nullcontext
from copy import deepcopy
from pathlib import Path

from ori.eval.bhce import BHCEClient
from ori.mcp_launcher import NativeMCPLauncherConfig
from ori.native_runtime import verify_native_runtime_roots

from .campaign_runner import _atomic_write, _exclusive_output_dir_lock
from .fingerprint import canonical_sha256
from .graph import build_archive_snapshot
from .native_bolt_runtime import verify_native_bolt_read_only_graphs
from .native_capability import build_native_capability_profile
from .native_ce_runtime import native_ce_connection, observe_native_ce_graph
from .native_mcp_profiles import get_native_implementation
from .native_mcp_runtime import (
    NativeCallDecision,
    NativeSessionCleanupPending,
    open_native_mcp_session,
    validate_native_session_observations,
)
from .protocol import _read_json


async def discover_native_profile_files(
    *,
    config: NativeMCPLauncherConfig,
    source_manifest_path: Path,
    archive_path: Path,
    product: str,
    databases: tuple[str, ...],
    output_dir: Path | None,
    execute: bool = False,
    timeout_seconds: float = 1200.0,
    page_size: int = 500,
) -> dict:
    """Check inputs offline, or explicitly observe a profile across an owned session.

    Offline checking deliberately does not prepare the launcher: that operation
    invokes Git. Source and Python startup checks belong to explicit execution.
    """
    if (
        type(config) is not NativeMCPLauncherConfig
        or type(execute) is not bool
        or type(timeout_seconds) not in (int, float)
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
        or type(page_size) is not int
        or not 1 <= page_size <= 2000
        or product not in {"simple", "complex", "oaic-2026-v1"}
    ):
        raise ValueError("NATIVE_BOOTSTRAP_INPUT_INVALID")
    source = get_native_implementation(config.implementation_id)
    if (
        not isinstance(databases, tuple)
        or any(not isinstance(name, str) or not name.strip() for name in databases)
        or source.backend == "bhce"
        and databases
        or source.implementation_id == "mordavid"
        and databases != ("neo4j", "bloodhound")
        or source.implementation_id == "armadin"
        and len(databases) != 1
    ):
        raise ValueError("NATIVE_BOOTSTRAP_DATABASE_SCOPE_INVALID")
    if (
        not config.runtime_roots
        or config.runtime_fingerprint is None
        or config.dependency_lock is None
        or config.dependency_lock_fingerprint is None
    ):
        raise ValueError("NATIVE_BOOTSTRAP_RUNTIME_BINDING_REQUIRED")
    checkout = config.checkout.resolve(strict=True)
    python = config.python_executable.absolute()
    if (
        not checkout.is_dir()
        or not python.is_file()
        or not os.access(python, os.X_OK)
        or python.is_relative_to(checkout)
        or python.resolve().is_relative_to(checkout)
    ):
        raise ValueError("NATIVE_BOOTSTRAP_PATH_INVALID")
    verify_native_runtime_roots(config.runtime_roots, config.runtime_fingerprint)
    if hashlib.sha256(config.dependency_lock.read_bytes()).hexdigest() != (
        config.dependency_lock_fingerprint
    ):
        raise ValueError("NATIVE_DEPENDENCY_LOCK_CHANGED")
    expected = build_archive_snapshot(
        archive_path.read_bytes(),
        _read_json(source_manifest_path),
        product=product,
    )
    summary = {
        "schema_version": "ori-native-profile-discovery-v1",
        "artifacts": {},
        "implementation": source.implementation_id,
        "status": "offline_inputs_checked",
        "provider_calls": 0,
        "tool_calls": 0,
        "prompt_reads": 0,
        "resource_reads": 0,
        "source_bytes_verified": False,
        "python_startup_verified": False,
        "discovery_verified": False,
        "campaign_admitted": False,
    }
    if not execute:
        return summary
    if output_dir is None:
        raise ValueError("NATIVE_BOOTSTRAP_OUTPUT_REQUIRED")
    if source.backend == "bhce":
        connection = {
            f"BLOODHOUND_{key}": os.environ.get(f"BLOODHOUND_{key}", "")
            for key in ("DOMAIN", "TOKEN_ID", "TOKEN_KEY")
        }
        connection.update(
            {
                f"BLOODHOUND_{key}": os.environ[f"BLOODHOUND_{key}"]
                for key in ("SCHEME", "PORT", "VERIFY_TLS")
                if f"BLOODHOUND_{key}" in os.environ
            }
        )
        connection, client_options, binding = native_ce_connection(connection)
    else:
        prefix = "BLOODHOUND" if source.implementation_id == "mordavid" else "NEO4J"
        connection = {
            f"{prefix}_{key}": os.environ.get(f"{prefix}_{key}", "")
            for key in ("URI", "USERNAME", "PASSWORD")
        }
        if any(not value.strip() for value in connection.values()):
            raise ValueError("NATIVE_CONNECTION_ENVIRONMENT_INVALID")
        client_options, binding = None, None
    output_dir.mkdir(parents=True, mode=0o700)
    if output_dir.stat().st_mode & 0o077 or output_dir.stat().st_uid != os.getuid():
        raise ValueError("NATIVE_BOOTSTRAP_OUTPUT_PERMISSIONS")

    def guard(*_args):
        return NativeCallDecision(False, "NATIVE_BOOTSTRAP_DISCOVERY_ONLY")

    with _exclusive_output_dir_lock(output_dir):
        descriptor = os.open(
            output_dir / "native-stderr.private.log",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as private_log:
            async with asyncio.timeout(timeout_seconds):
                async with (
                    BHCEClient(**client_options)
                    if client_options is not None
                    else nullcontext(None)
                ) as client:

                    async def observe():
                        if source.backend == "bhce":
                            return await observe_native_ce_graph(
                                client, expected, page_size=page_size
                            )
                        return await verify_native_bolt_read_only_graphs(
                            implementation_id=source.implementation_id,
                            uri=connection[f"{prefix}_URI"],
                            username=connection[f"{prefix}_USERNAME"],
                            password=connection[f"{prefix}_PASSWORD"],
                            databases=databases,
                            expected=expected,
                            timeout_seconds=timeout_seconds,
                            page_size=page_size,
                            require_supported_topology=True,
                        )

                    before = await observe()
                    backend_fingerprint = canonical_sha256(
                        binding if binding is not None else before["access_before"],
                    )
                    try:
                        async with open_native_mcp_session(
                            config,
                            connection=connection,
                            guard=guard,
                            private_stderr=private_log,
                            capability_profile=None,
                            max_calls=1,
                        ) as (session, runtime):
                            discovery = deepcopy(
                                {
                                    "tools": session.discovered_tools,
                                    "prompts": session.prompts,
                                    "resources": session.resources,
                                    "resource_templates": session.resource_templates,
                                    "surface_availability": session.surface_availability,
                                }
                            )
                            profile = build_native_capability_profile(
                                source.implementation_id,
                                runtime_fingerprint=config.runtime_fingerprint,
                                dependency_lock_fingerprint=config.dependency_lock_fingerprint,
                                backend_binding_fingerprint=backend_fingerprint,
                                **discovery,
                            )
                            validate_native_session_observations(
                                {"runtime": runtime, "native_discovery": discovery},
                                profile=profile,
                                backend_binding_fingerprint=backend_fingerprint,
                            )
                    except NativeSessionCleanupPending as pending:
                        try:
                            print(
                                "NATIVE_SESSION_CLEANUP_PENDING: waiting; no profile publication",
                                file=sys.stderr,
                                flush=True,
                            )
                        finally:
                            await pending.wait_for_cleanup()
                        raise
                    after = await observe()
                    if (
                        source.backend == "neo4j"
                        and before["access_before"] != after["access_after"]
                    ):
                        raise ValueError("NATIVE_BOOTSTRAP_BACKEND_CHANGED")
            observations = {
                "implementation_id": source.implementation_id,
                "capability_profile_fingerprint": profile.profile_fingerprint,
                "graph_before": before,
                "graph_after": after,
                "runtime": runtime,
                "native_discovery": discovery,
                "scope": "completed-native-session-interval",
                "session_cleanup_confirmed": True,
                "campaign_admitted": False,
            }
            if binding is not None:
                observations["backend_binding"] = binding
            _atomic_write(output_dir / "native-discovery-v1.private.json", observations)
            _atomic_write(
                output_dir / "native-capability-profile-v1.private.json",
                profile.model_dump(mode="json"),
            )
    from .native_setup import file_reference

    return {
        **summary,
        "status": "native_profile_observed",
        "source_bytes_verified": True,
        "artifacts": {
            "profile": file_reference(
                output_dir / "native-capability-profile-v1.private.json",
                schema_version="ori-native-capability-v1",
            ),
            "discovery": file_reference(output_dir / "native-discovery-v1.private.json"),
        },
        "python_startup_verified": True,
        "discovery_verified": True,
    }
