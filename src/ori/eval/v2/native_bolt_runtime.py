"""Optional read-only graph-verification transport for native Bolt MCP servers.

This is a harness-owned verifier, not a replacement for native model tool calls.
READ routing and a matching snapshot do not certify database privileges,
quiescence, source/runtime isolation, or a native benchmark campaign.
"""

from __future__ import annotations

import asyncio
import math
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .graph import GraphSnapshot
from .native_bolt_graph import collect_bolt_snapshot

NEO4J_DRIVER_VERSION = "5.28.2"


class NativeBackendError(ValueError):
    """Safe diagnostic code; backend errors can contain private connection data."""


def _validate_connection(uri: str, username: str, password: str, database: str) -> None:
    if any(not isinstance(value, str) or not value.strip()
           for value in (uri, username, password, database)):
        raise NativeBackendError("NATIVE_BACKEND_CONFIG_INVALID")
    try:
        parsed = urlsplit(uri)
        if (
            parsed.scheme not in {"bolt", "bolt+s"}
            or not parsed.hostname or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or parsed.path not in {"", "/"}
            or (parsed.port is not None and not 1 <= parsed.port <= 65535)
        ):
            raise ValueError("invalid URI")
    except ValueError as exc:
        raise NativeBackendError("NATIVE_BACKEND_CONFIG_INVALID") from exc


async def verify_bolt_graph(
    *, uri: str, username: str, password: str, database: str,
    expected: GraphSnapshot, page_size: int = 500,
    timeout_seconds: float = 120.0, transaction_timeout_seconds: float = 60.0,
    auxiliary_labels: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Read an explicitly selected database and return private verification data.

    All operations, including connecting and cleanup, live inside a finite client
    deadline. No managed transaction retry can splice pages from separate reads.
    """

    _validate_connection(uri, username, password, database)
    if type(page_size) is not int or not 1 <= page_size <= 2000:
        raise NativeBackendError("NATIVE_BACKEND_CONFIG_INVALID")
    for value in (timeout_seconds, transaction_timeout_seconds):
        if isinstance(value, bool) or not isinstance(value, (float, int)):
            raise NativeBackendError("NATIVE_BACKEND_CONFIG_INVALID")
        if not math.isfinite(value) or value <= 0:
            raise NativeBackendError("NATIVE_BACKEND_CONFIG_INVALID")
    if transaction_timeout_seconds > timeout_seconds:
        raise NativeBackendError("NATIVE_BACKEND_CONFIG_INVALID")
    try:
        import neo4j
    except ImportError as exc:
        raise NativeBackendError("NATIVE_DRIVER_MISSING: install the native-mcp extra") from exc
    if neo4j.__version__ != NEO4J_DRIVER_VERSION:
        raise NativeBackendError("NATIVE_DRIVER_VERSION_MISMATCH")

    connection_observation: dict[str, Any] | None = None
    try:
        async with asyncio.timeout(timeout_seconds):
            async with neo4j.AsyncGraphDatabase.driver(
                uri, auth=(username, password),
                connection_timeout=min(transaction_timeout_seconds, timeout_seconds),
                connection_acquisition_timeout=min(transaction_timeout_seconds, timeout_seconds),
            ) as driver:
                async with driver.session(
                    database=database, default_access_mode=neo4j.READ_ACCESS, fetch_size=page_size,
                ) as session:
                    async with await session.begin_transaction(
                        timeout=transaction_timeout_seconds,
                    ) as transaction:

                        async def read(query: str, parameters: dict) -> list[dict]:
                            nonlocal connection_observation
                            result = await transaction.run(query, parameters)
                            rows = []
                            limit = parameters.get("limit", 1)
                            async for record in result:
                                if len(rows) >= limit:
                                    raise NativeBackendError("NATIVE_BACKEND_PAGE_OVERFLOW")
                                rows.append(record.data())
                            summary = await result.consume()
                            if summary.database != database:
                                raise NativeBackendError("NATIVE_BACKEND_DATABASE_MISMATCH")
                            server = summary.server
                            observation = {
                                "database": summary.database,
                                "server_address": list(server.address),
                                "server_agent": server.agent,
                                "protocol_version": list(server.protocol_version),
                            }
                            if connection_observation is None:
                                connection_observation = observation
                            elif observation != connection_observation:
                                raise NativeBackendError("NATIVE_BACKEND_CONNECTION_CHANGED")
                            return rows

                        _, verification, native_fingerprint = await collect_bolt_snapshot(
                            read, expected, page_size=page_size, timeout_seconds=timeout_seconds,
                            auxiliary_labels=auxiliary_labels,
                        )
                        # The pinned driver's clean context exit otherwise commits.
                        await transaction.rollback()
    except NativeBackendError:
        raise
    except TimeoutError as exc:
        raise NativeBackendError("NATIVE_BACKEND_TIMEOUT") from exc
    except ValueError as exc:
        raise NativeBackendError("NATIVE_BACKEND_GRAPH_MISMATCH") from exc
    except Exception as exc:
        raise NativeBackendError("NATIVE_BACKEND_VERIFICATION_FAILED") from exc

    return {
        "graph_verification": verification.model_dump(mode="json"),
        "native_surface_fingerprint": native_fingerprint,
        "connection_observation": connection_observation,
        "requested_endpoint": uri,
        "requested_database": database,
        "driver_version": NEO4J_DRIVER_VERSION,
        "principal_name": username,
        "auxiliary_labels": list(auxiliary_labels),
        "read_only_privileges_verified": False,
        "quiescence_verified": False,
        "campaign_admitted": False,
    }


async def verify_native_graph_files(
    *, source_manifest_path: Path, archive_path: Path, product: str,
    implementation_id: str, database: str, output_path: Path,
    page_size: int = 500, timeout_seconds: float = 120.0,
    transaction_timeout_seconds: float = 60.0, auxiliary_labels: tuple[str, ...] = (),
) -> dict[str, Any]:
    """CLI boundary: explicit invocation contacts only the selected native backend."""
    import hashlib
    import json
    import os

    from .campaign_runner import _atomic_write, _exclusive_output_dir_lock
    from .graph import build_archive_snapshot
    from .native_mcp_profiles import get_native_implementation

    implementation = get_native_implementation(implementation_id)
    if implementation.backend != "neo4j":
        raise NativeBackendError("NATIVE_BACKEND_NOT_BOLT")
    if not output_path.name.endswith(".private.json"):
        raise NativeBackendError("NATIVE_BACKEND_PRIVATE_OUTPUT_REQUIRED")
    if output_path.exists() or output_path.is_symlink():
        raise NativeBackendError("NATIVE_BACKEND_OUTPUT_EXISTS")
    manifest_bytes = source_manifest_path.read_bytes()
    archive = archive_path.read_bytes()
    expected = build_archive_snapshot(archive, json.loads(manifest_bytes), product=product)
    prefix = "BLOODHOUND" if implementation_id == "mordavid" else "NEO4J"
    fields = tuple(f"{prefix}_{suffix}" for suffix in ("URI", "USERNAME", "PASSWORD"))
    uri, username, password = (os.environ.get(name, "") for name in fields)
    report = await verify_bolt_graph(
        uri=uri, username=username, password=password, database=database,
        expected=expected, page_size=page_size, timeout_seconds=timeout_seconds,
        transaction_timeout_seconds=transaction_timeout_seconds, auxiliary_labels=auxiliary_labels,
    )
    report.update({
        "implementation_id": implementation_id,
        "expected_source_revision": implementation.revision,
        "mcp_source_verified": False,
        "source_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "source_archive_sha256": hashlib.sha256(archive).hexdigest(),
        "credential_source_names": list(fields[1:]),
    })
    with _exclusive_output_dir_lock(output_path.parent):
        if output_path.exists() or output_path.is_symlink():
            raise NativeBackendError("NATIVE_BACKEND_OUTPUT_EXISTS")
        _atomic_write(output_path, report)
    return report
