"""Optional read-only graph-verification transport for native Bolt MCP servers.

This is a harness-owned verifier, not a replacement for native model tool calls.
READ routing and a matching snapshot do not certify database privileges,
quiescence, source/runtime isolation, or a native benchmark campaign.
"""

from __future__ import annotations

import asyncio
import math
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .graph import GraphSnapshot
from .native_bolt_graph import collect_bolt_snapshot

NEO4J_DRIVER_VERSION = "5.28.2"
NATIVE_TRANSACTION_TIMEOUT_MAX_SECONDS = 60
NATIVE_TRANSACTION_TIMEOUT_QUERY = (
    "SHOW SETTINGS 'db.transaction.timeout' YIELD name, value RETURN name, value"
)


class NativeBackendError(ValueError):
    """Safe diagnostic code; backend errors can contain private connection data."""


def validate_native_transaction_timeout(rows) -> float:
    """Interpret the observed server default, not a whole-tool cancellation guarantee.

    Neo4j settings render duration amounts with unit suffixes. Only fixed-duration
    units are admitted; disabled, unknown and excessive values fail closed.
    """
    if (not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict)
            or set(rows[0]) != {"name", "value"}
            or rows[0]["name"] != "db.transaction.timeout"):
        raise NativeBackendError("NATIVE_TRANSACTION_TIMEOUT_UNVERIFIED")
    value = rows[0]["value"]
    if not isinstance(value, str) or len(value) > 64:
        raise NativeBackendError("NATIVE_TRANSACTION_TIMEOUT_UNVERIFIED")
    parts = re.findall(r"([0-9]+)(ms|s|m|h|d)", value)
    if not parts or "".join(number + unit for number, unit in parts) != value:
        raise NativeBackendError("NATIVE_TRANSACTION_TIMEOUT_UNVERIFIED")
    units = {"ms": .001, "s": 1, "m": 60, "h": 3600, "d": 86400}
    seconds = sum(int(number) * units[unit] for number, unit in parts)
    if not 0 < seconds <= NATIVE_TRANSACTION_TIMEOUT_MAX_SECONDS:
        raise NativeBackendError("NATIVE_TRANSACTION_TIMEOUT_UNBOUNDED")
    return seconds


_TOPOLOGY_FIELDS = (
    "role", "writer", "currentPrimariesCount", "currentSecondariesCount",
    "requestedPrimariesCount", "requestedSecondariesCount", "replicationLag",
)


def validate_native_serving_topology(rows: list[dict], *, databases: tuple[str, ...]) -> dict:
    """Support one primary allocation and no secondaries, not 'standalone forever'."""
    _validate_access_scope(databases, None)
    observations = {}
    server_ids = set()
    for name in databases:
        matches = [row for row in rows if isinstance(row, dict) and row.get("name") == name]
        if len(matches) != 1:
            raise NativeBackendError("NATIVE_SERVING_TOPOLOGY_UNVERIFIED")
        row = matches[0]
        expected_counts = {
            "currentPrimariesCount": 1, "currentSecondariesCount": 0,
            "requestedPrimariesCount": 1, "requestedSecondariesCount": 0, "replicationLag": 0,
        }
        if (row.get("role") != "primary" or row.get("writer") is not True
                or any(type(row.get(key)) is not int or row[key] != value
                       for key, value in expected_counts.items())
                or not isinstance(row.get("serverID"), str) or not row["serverID"].strip()):
            raise NativeBackendError("NATIVE_SERVING_TOPOLOGY_UNSUPPORTED")
        server_ids.add(row["serverID"])
        observations[name] = {key: row[key] for key in (*_TOPOLOGY_FIELDS, "serverID")}
    if len(server_ids) != 1:
        raise NativeBackendError("NATIVE_SERVING_TOPOLOGY_UNSUPPORTED")
    return {"shape": "single-primary-no-secondaries", "databases": observations}


def _validate_access_scope(databases, home_database):
    if (not isinstance(databases, tuple) or not databases
            or any(not isinstance(name, str) or not name.strip() for name in databases)
            or len(set(databases)) != len(databases)
            or home_database is not None and home_database not in databases):
        raise NativeBackendError("NATIVE_DATABASE_ACCESS_SCOPE_INVALID")


def validate_database_access_observation(
    rows: list[dict], *, databases: tuple[str, ...], home_database: str | None = None,
) -> dict[str, dict]:
    """Interpret SHOW DATABASES, never READ routing or operator assertions.

    Visibility is privilege-filtered. Missing destinations are unqualified, not
    evidence of enforced inaccessibility. This does not prove standalone topology,
    an RBAC policy, or that an administrator cannot change access later.
    """
    _validate_access_scope(databases, home_database)
    observed = {}
    for name in databases:
        matches = [row for row in rows if isinstance(row, dict) and row.get("name") == name]
        if len(matches) != 1:
            raise NativeBackendError("NATIVE_DATABASE_ACCESS_SCOPE_UNVERIFIED")
        row = matches[0]
        if (row.get("type") != "standard" or row.get("access") != "read-only"
                or row.get("currentStatus") != "online" or row.get("requestedStatus") != "online"):
            raise NativeBackendError("NATIVE_DATABASE_NOT_READ_ONLY_ONLINE")
        if any(not isinstance(row.get(key), str) or not row[key].strip()
               for key in ("databaseID", "serverID", "address", "lastStartTime")):
            raise NativeBackendError("NATIVE_DATABASE_IDENTITY_UNVERIFIED")
        if type(row.get("lastCommittedTxn")) is not int or row["lastCommittedTxn"] < 0:
            raise NativeBackendError("NATIVE_DATABASE_TRANSACTION_UNVERIFIED")
        if type(row.get("home")) is not bool:
            raise NativeBackendError("NATIVE_DATABASE_HOME_UNVERIFIED")
        observed[name] = {key: row[key] for key in (
            "name", "type", "access", "currentStatus", "requestedStatus", "databaseID",
            "serverID", "address", "lastStartTime", "lastCommittedTxn", "home",
        )}
    if home_database is not None:
        homes = [row.get("name") for row in rows
                 if isinstance(row, dict) and row.get("home") is True]
        if homes != [home_database]:
            raise NativeBackendError("NATIVE_DATABASE_HOME_UNVERIFIED")
    return observed


async def observe_bolt_database_access(
    *, uri: str, username: str, password: str, databases: tuple[str, ...],
    home_database: str | None = None, timeout_seconds: float = 30.0,
    require_read_only_privileges: bool = False,
    require_supported_topology: bool = False,
) -> dict[str, Any]:
    """Bounded harness-owned system query; never changes database access mode."""
    _validate_connection(uri, username, password, "system")
    _validate_access_scope(databases, home_database)
    if (type(require_read_only_privileges) is not bool
            or type(require_supported_topology) is not bool):
        raise NativeBackendError("NATIVE_BACKEND_CONFIG_INVALID")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
        raise NativeBackendError("NATIVE_BACKEND_CONFIG_INVALID")
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise NativeBackendError("NATIVE_BACKEND_CONFIG_INVALID")
    try:
        import neo4j
    except ImportError:
        raise NativeBackendError("NATIVE_DRIVER_MISSING") from None
    if neo4j.__version__ != NEO4J_DRIVER_VERSION:
        raise NativeBackendError("NATIVE_DRIVER_VERSION_MISMATCH")
    query = (
        "SHOW DATABASES YIELD name, type, access, currentStatus, requestedStatus, "
        "databaseID, serverID, address, lastStartTime, lastCommittedTxn, home "
        "RETURN name, type, access, currentStatus, requestedStatus, databaseID, "
        "serverID, address, toString(lastStartTime) AS lastStartTime, lastCommittedTxn, home"
    )
    if require_supported_topology:
        extra = ", " + ", ".join(_TOPOLOGY_FIELDS)
        query = query.replace("home RETURN", "home" + extra + " RETURN") + extra
    try:
        async with asyncio.timeout(timeout_seconds):
            async with neo4j.AsyncGraphDatabase.driver(
                uri, auth=(username, password), connection_timeout=timeout_seconds,
                connection_acquisition_timeout=timeout_seconds,
            ) as driver:
                async with driver.session(database="system", default_access_mode=neo4j.READ_ACCESS,
                                          fetch_size=100) as session:
                    async with await session.begin_transaction(timeout=timeout_seconds) as tx:
                        result = await tx.run(query, {})
                        rows = []
                        async for record in result:
                            if len(rows) >= 1000:
                                raise NativeBackendError("NATIVE_DATABASE_INVENTORY_OVERFLOW")
                            rows.append(record.data())
                        summary = await result.consume()
                        if summary.database != "system":
                            raise NativeBackendError("NATIVE_BACKEND_DATABASE_MISMATCH")
                        observed = validate_database_access_observation(
                            rows, databases=databases, home_database=home_database,
                        )
                        topology = (validate_native_serving_topology(rows, databases=databases)
                                    if require_supported_topology else None)
                        privileges = None
                        transaction_timeout = None
                        if require_supported_topology:
                            setting_result = await tx.run(NATIVE_TRANSACTION_TIMEOUT_QUERY, {})
                            transaction_timeout = []
                            async for record in setting_result:
                                if transaction_timeout:
                                    raise NativeBackendError(
                                        "NATIVE_TRANSACTION_TIMEOUT_UNVERIFIED",
                                    )
                                transaction_timeout.append(record.data())
                            setting_summary = await setting_result.consume()
                            if (setting_summary.database != "system"
                                    or setting_summary.server.address != summary.server.address
                                    or setting_summary.server.agent != summary.server.agent
                                    or setting_summary.server.protocol_version
                                    != summary.server.protocol_version):
                                raise NativeBackendError("NATIVE_BACKEND_CONNECTION_CHANGED")
                            validate_native_transaction_timeout(transaction_timeout)
                        if require_read_only_privileges:
                            privilege_result = await tx.run("SHOW USER PRIVILEGES", {})
                            privilege_rows = []
                            async for record in privilege_result:
                                if len(privilege_rows) >= 1000:
                                    raise NativeBackendError("NATIVE_PRIVILEGE_INVENTORY_OVERFLOW")
                                privilege_rows.append(record.data())
                            privilege_summary = await privilege_result.consume()
                            if privilege_summary.database != "system":
                                raise NativeBackendError("NATIVE_BACKEND_DATABASE_MISMATCH")
                            homes = [row["name"] for row in rows if row.get("home") is True]
                            privileges = validate_read_only_privileges(
                                privilege_rows, username=username, databases=databases,
                                home_database=homes[0] if len(homes) == 1 else None,
                            )
                        await tx.rollback()
    except NativeBackendError:
        raise
    except TimeoutError:
        raise NativeBackendError("NATIVE_BACKEND_TIMEOUT") from None
    except Exception:
        raise NativeBackendError("NATIVE_DATABASE_ACCESS_UNVERIFIED") from None
    return {
        "databases": observed, "principal_name": username,
        "requested_endpoint": uri,
        "server_address": list(summary.server.address),
        "server_agent": summary.server.agent,
        "protocol_version": list(summary.server.protocol_version),
        "read_only_database_access_observed": True,
        "read_only_privileges_verified": privileges is not None,
        "privileges": privileges, "quiescence_verified": False,
        "transaction_timeout": transaction_timeout,
        "serving_topology": topology,
        "standalone_topology_verified": False, "campaign_admitted": False,
    }


def validate_read_only_privileges(
    rows: list[dict], *, username: str, databases: tuple[str, ...],
    home_database: str | None,
) -> list[dict]:
    """Accept only explicit data-read grants, never role-name inference.

    General procedure/function execution is deliberately unsupported: this gate
    does not audit installed or unrestricted procedure implementations.
    """
    fields = ("access", "action", "resource", "graph", "segment", "role", "user")
    if not rows:
        raise NativeBackendError("NATIVE_PRIVILEGES_UNVERIFIED")
    normalized = []
    access = set()
    for row in rows:
        setting = (isinstance(row, dict) and row.get("action") == "show_setting"
                   and row.get("segment") == "SETTING(db.transaction.timeout)")
        if (not isinstance(row, dict) or set(row) != {*fields, "immutable"}
                or any(not isinstance(row.get(key), str)
                or not row[key].strip() for key in fields
                       if not setting or key not in {"resource", "graph"})
                or setting and any(row[key] is not None and not isinstance(row[key], str)
                                   for key in ("resource", "graph"))
                or type(row.get("immutable")) is not bool or row["user"] != username
                or row["access"] not in {"GRANTED", "DENIED"}):
            raise NativeBackendError("NATIVE_PRIVILEGE_SHAPE_INVALID")
        if row["access"] == "GRANTED":
            if not setting and ((row["action"], row["resource"], row["segment"]) not in {
                ("access", "database", "database"),
                ("match", "all_properties", "NODE(*)"),
                ("match", "all_properties", "RELATIONSHIP(*)"),
            } or row["graph"] == "DEFAULT"):
                raise NativeBackendError("NATIVE_PRIVILEGE_GRANT_UNSUPPORTED")
            if row["action"] == "access":
                scope = row["graph"]
                access.update(databases if scope == "*" else
                              (home_database,) if scope == "HOME" else (scope,))
        normalized.append({key: row[key] for key in (*fields, "immutable")})
    if not set(databases) <= access:
        raise NativeBackendError("NATIVE_PRIVILEGE_ACCESS_UNVERIFIED")
    return sorted(
        normalized, key=lambda row: tuple(str(row[key]) for key in (*fields, "immutable")),
    )


async def verify_native_bolt_read_only_graphs(
    *, implementation_id: str, uri: str, username: str, password: str,
    databases: tuple[str, ...], expected: GraphSnapshot, timeout_seconds: float = 600.0,
    page_size: int = 500, transaction_timeout_seconds: float = 60.0,
    auxiliary_labels: tuple[str, ...] = (),
    require_supported_topology: bool = False,
) -> dict[str, Any]:
    """Observe access across every native destination and verify exact graphs.

    Equal transaction/identity observations are bounded retrospective stability,
    not an RBAC audit, topology proof or unconditional campaign admission.
    """
    _validate_access_scope(databases, None)
    if (implementation_id not in {"mordavid", "armadin"}
            or implementation_id == "mordavid" and databases != ("neo4j", "bloodhound")
            or implementation_id == "armadin" and len(databases) != 1):
        raise NativeBackendError("NATIVE_DATABASE_ACCESS_SCOPE_INVALID")
    if type(page_size) is not int or not 1 <= page_size <= 2000:
        raise NativeBackendError("NATIVE_BACKEND_CONFIG_INVALID")
    for value in (timeout_seconds, transaction_timeout_seconds):
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value <= 0):
            raise NativeBackendError("NATIVE_BACKEND_CONFIG_INVALID")
    if transaction_timeout_seconds > timeout_seconds:
        raise NativeBackendError("NATIVE_BACKEND_CONFIG_INVALID")
    connection = {"uri": uri, "username": username, "password": password}
    control = {**connection, "databases": databases,
               "home_database": databases[0] if implementation_id == "armadin" else None,
               "timeout_seconds": min(timeout_seconds, 30.0),
               "require_read_only_privileges": True}
    if require_supported_topology:
        control["require_supported_topology"] = True
    try:
        async with asyncio.timeout(timeout_seconds):
            before = await observe_bolt_database_access(**control)
            graphs = {}
            for database in databases:
                graph = await verify_bolt_graph(
                    **connection, database=database, expected=expected, page_size=page_size,
                    timeout_seconds=timeout_seconds,
                    transaction_timeout_seconds=transaction_timeout_seconds,
                    auxiliary_labels=auxiliary_labels,
                )
                actual = graph["connection_observation"]
                if any(actual[key] != before[key] for key in (
                    "server_address", "server_agent", "protocol_version",
                )):
                    raise NativeBackendError("NATIVE_BACKEND_CONNECTION_CHANGED")
                graphs[database] = graph
            after = await observe_bolt_database_access(**control)
            if before != after:
                raise NativeBackendError("NATIVE_DATABASE_ACCESS_CHANGED")
    except TimeoutError:
        raise NativeBackendError("NATIVE_BACKEND_TIMEOUT") from None
    return {
        "implementation_id": implementation_id, "access_before": before, "access_after": after,
        "graphs": graphs, "bounded_database_stability_observed": True,
        "read_only_privileges_verified": before["read_only_privileges_verified"],
        "quiescence_verified": False,
        "standalone_topology_verified": False, "campaign_admitted": False,
    }


async def qualify_native_bolt_corpus(
    *, corpus, offline, config, profile, connection, guard, private_stderr, databases,
    expected: GraphSnapshot, timeout_seconds: float = 1200.0, page_size: int = 500,
    transaction_timeout_seconds: float = 60.0,
):
    """Qualify a whole supplied corpus in one owned, graph-bracketed session.

    Selection admission remains the caller's responsibility. This never replaces
    an unsupported selected task with an easier task or runs a model.
    """
    from .live_projection import (
        native_corpus_qualification_inputs,
        verify_native_corpus_interoperability,
    )

    _, _, budget = native_corpus_qualification_inputs(
        corpus=corpus, offline=offline, profile=profile, snapshot=expected,
    )

    async def work(session):
        return await verify_native_corpus_interoperability(
            corpus=corpus, offline=offline, profile=profile, snapshot=expected, session=session,
        )

    return await qualify_native_bolt_session(
        config=config, profile=profile, connection=connection, guard=guard,
        private_stderr=private_stderr, databases=databases, expected=expected,
        qualification_work=work, max_calls=budget, timeout_seconds=timeout_seconds,
        page_size=page_size, transaction_timeout_seconds=transaction_timeout_seconds,
    )


async def qualify_native_bolt_session(
    *, config, profile, connection, guard, private_stderr, databases: tuple[str, ...],
    expected: GraphSnapshot, qualification_work, timeout_seconds: float = 1200.0,
    page_size: int = 500, transaction_timeout_seconds: float = 60.0,
    max_calls: int = 16,
):
    """Attach fixture-work binding to one completed owned interval."""
    from .fingerprint import canonical_sha256

    value, observations = await run_native_bolt_session_work(
        config=config, profile=profile, connection=connection, guard=guard,
        private_stderr=private_stderr, databases=databases, expected=expected,
        work=qualification_work, timeout_seconds=timeout_seconds, page_size=page_size,
        transaction_timeout_seconds=transaction_timeout_seconds, max_calls=max_calls,
    )
    return value, {**observations, "qualification_work_fingerprint": canonical_sha256(value)}


async def run_native_bolt_session_work(
    *, config, profile, connection, guard, private_stderr, databases: tuple[str, ...],
    expected: GraphSnapshot, work, timeout_seconds: float = 1200.0,
    page_size: int = 500, transaction_timeout_seconds: float = 60.0,
    max_calls: int = 16, before_work=None, query_coordinator=None,
    session_call_timeout_seconds: float = 60.0,
):
    """Own native work across graph, runtime, cleanup and stability gates.

    Validated initial observations are copied before the optional admission callback.
    Its rejection still crosses cleanup and post-graph gates. This neutral owner
    never grants qualification or campaign admission and never resets containment.
    """
    from copy import deepcopy

    from .fingerprint import canonical_sha256
    from .native_capability import validate_native_capability_profile
    from .native_mcp_runtime import open_native_mcp_session, validate_native_session_observations

    profile = validate_native_capability_profile(profile)
    if type(max_calls) is not int or max_calls <= 0:
        raise NativeBackendError("NATIVE_SESSION_CALL_BUDGET_INVALID")
    if (profile.backend != "neo4j" or config.implementation_id != profile.implementation_id
            or not config.runtime_roots or config.dependency_lock is None
            or config.runtime_fingerprint != profile.runtime_fingerprint
            or config.dependency_lock_fingerprint != profile.dependency_lock_fingerprint):
        raise NativeBackendError("NATIVE_QUALIFICATION_RUNTIME_BINDING_INVALID")
    if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
        raise NativeBackendError("NATIVE_BACKEND_CONFIG_INVALID")
    if (type(session_call_timeout_seconds) not in (int, float)
            or not math.isfinite(session_call_timeout_seconds) or session_call_timeout_seconds <= 0
            or not callable(work) or not callable(guard)
            or before_work is not None and not callable(before_work)):
        raise NativeBackendError("NATIVE_BACKEND_CONFIG_INVALID")
    if query_coordinator is not None:
        from ori.eval.direct_query_safety import DirectQueryCoordinator

        if (not isinstance(query_coordinator, DirectQueryCoordinator)
                or getattr(query_coordinator, "native_backend_binding_fingerprint", None)
                != profile.backend_binding_fingerprint):
            raise NativeBackendError("NATIVE_BOLT_COORDINATOR_BINDING_INVALID")
    connection = deepcopy(connection)
    prefix = "BLOODHOUND" if profile.implementation_id == "mordavid" else "NEO4J"
    options = dict(
        implementation_id=profile.implementation_id,
        uri=connection.get(f"{prefix}_URI", ""),
        username=connection.get(f"{prefix}_USERNAME", ""),
        password=connection.get(f"{prefix}_PASSWORD", ""),
        databases=databases, expected=expected, timeout_seconds=timeout_seconds,
        page_size=page_size, transaction_timeout_seconds=transaction_timeout_seconds,
        require_supported_topology=True,
    )
    try:
        async with asyncio.timeout(timeout_seconds):
            before = await verify_native_bolt_read_only_graphs(**options)
            if canonical_sha256(before["access_before"]) != profile.backend_binding_fingerprint:
                raise NativeBackendError("NATIVE_QUALIFICATION_BACKEND_BINDING_MISMATCH")
            admission_error = None
            async with open_native_mcp_session(
                config, connection=connection, guard=guard, private_stderr=private_stderr,
                capability_profile=profile,
                max_calls=max_calls,
                query_coordinator=query_coordinator,
                session_call_timeout_seconds=session_call_timeout_seconds,
            ) as (native, provenance):
                startup = provenance.get("runtime_startup") or {}
                dependencies = startup.get("dependencies") or {}
                if (provenance.get("source_bytes_verified") is not True
                        or provenance.get("runtime_content_verified") is not True
                        or startup.get("python_startup_verified") is not True
                        or dependencies.get("dependency_inventory_verified") is not True
                        or dependencies.get("dependency_lock_fingerprint")
                        != profile.dependency_lock_fingerprint):
                    raise NativeBackendError("NATIVE_QUALIFICATION_RUNTIME_UNVERIFIED")
                discovery = deepcopy({
                    "tools": native.discovered_tools, "prompts": native.prompts,
                    "resources": native.resources, "resource_templates": native.resource_templates,
                    "surface_availability": native.surface_availability,
                })
                try:
                    validate_native_session_observations(
                        {"runtime": provenance, "native_discovery": discovery}, profile=profile,
                        backend_binding_fingerprint=profile.backend_binding_fingerprint,
                    )
                except (KeyError, TypeError, ValueError):
                    raise NativeBackendError("NATIVE_QUALIFICATION_RUNTIME_UNVERIFIED") from None
                if before_work is not None:
                    try:
                        await before_work(deepcopy({
                            "implementation_id": profile.implementation_id,
                            "capability_profile_fingerprint": profile.profile_fingerprint,
                            "graph_before": before, "runtime": provenance,
                            "native_discovery": discovery,
                        }))
                    except (Exception, asyncio.CancelledError) as exc:
                        admission_error = exc
                if admission_error is None:
                    value = await work(native)
            # Exiting the owner confirms cleanup and its source/runtime rechecks.
            after = await verify_native_bolt_read_only_graphs(**options)
            if before["access_before"] != after["access_after"]:
                raise NativeBackendError("NATIVE_QUALIFICATION_INTERVAL_CHANGED")
            if admission_error is not None:
                raise admission_error
    except TimeoutError:
        raise NativeBackendError("NATIVE_QUALIFICATION_TIMEOUT") from None
    return value, {
        "implementation_id": profile.implementation_id,
        "capability_profile_fingerprint": profile.profile_fingerprint,
        "scope": "completed-native-session-interval",
        "graph_before": before, "graph_after": after, "runtime": provenance,
        "native_discovery": discovery,
        "session_cleanup_confirmed": True,
        "committed_data_quiescence_observed": True,
        "serving_topology": before["access_before"]["serving_topology"],
        "campaign_admitted": False,
    }


def validate_completed_native_bolt_qualification(
    report, *, profile, expected: GraphSnapshot, work_result,
) -> str:
    """Reinterpret private observations for certification, never accept success flags alone."""
    import json

    from .fingerprint import canonical_sha256
    from .graph import LiveGraphVerification
    from .native_capability import validate_native_capability_profile
    from .native_mcp_runtime import validate_native_session_observations

    try:
        profile = validate_native_capability_profile(profile)
        if (profile.backend != "neo4j" or report["implementation_id"] != profile.implementation_id
                or report["capability_profile_fingerprint"] != profile.profile_fingerprint
                or report["scope"] != "completed-native-session-interval"
                or report["qualification_work_fingerprint"] != canonical_sha256(work_result)
                or report["session_cleanup_confirmed"] is not True):
            raise ValueError("interval binding")
        before, after = report["graph_before"], report["graph_after"]
        first = before["access_before"]
        databases = (("neo4j", "bloodhound") if profile.implementation_id == "mordavid"
                     else tuple(first["databases"]))
        if (set(first["databases"]) != set(databases)
                or profile.implementation_id == "armadin" and len(databases) != 1
                or canonical_sha256(first) != profile.backend_binding_fingerprint):
            raise ValueError("backend binding")
        for interval in (before, after):
            if (interval["access_before"] != first or interval["access_after"] != first
                    or set(interval["graphs"]) != set(databases)):
                raise ValueError("interval drift")
            access = interval["access_before"]
            validate_native_transaction_timeout(access["transaction_timeout"])
            rows = list(access["databases"].values())
            validate_database_access_observation(
                rows, databases=databases,
                home_database=databases[0] if profile.implementation_id == "armadin" else None,
            )
            homes = [row["name"] for row in rows if row["home"]]
            validate_read_only_privileges(
                access["privileges"], username=access["principal_name"], databases=databases,
                home_database=homes[0] if len(homes) == 1 else None,
            )
            topology_rows = []
            for row in rows:
                topology = access["serving_topology"]["databases"][row["name"]]
                if topology["serverID"] != row["serverID"]:
                    raise ValueError("topology identity")
                topology_rows.append({**row, **topology})
            if validate_native_serving_topology(topology_rows, databases=databases) != (
                access["serving_topology"]
            ):
                raise ValueError("topology observation")
            for database, graph in interval["graphs"].items():
                receipt = LiveGraphVerification.model_validate_json(
                    json.dumps(graph["graph_verification"], allow_nan=False),
                )
                if (receipt.expected_graph_fingerprint != expected.graph_fingerprint
                        or receipt.observed_graph_fingerprint != expected.graph_fingerprint
                        or receipt.object_count != len(expected.entities)
                        or receipt.relationship_count != len(expected.relationships)
                        or graph["principal_name"] != access["principal_name"]
                        or graph["requested_endpoint"] != access["requested_endpoint"]
                        or graph["requested_database"] != database
                        or graph["connection_observation"]["database"] != database
                        or any(graph["connection_observation"][key] != access[key] for key in (
                            "server_address", "server_agent", "protocol_version",
                        ))):
                    raise ValueError("graph observation")
        validate_native_session_observations(
            report, profile=profile, backend_binding_fingerprint=canonical_sha256(first),
        )
        return canonical_sha256(report)
    except (KeyError, TypeError, ValueError, AttributeError):
        raise NativeBackendError("NATIVE_COMPLETED_QUALIFICATION_INVALID") from None


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
    read_only_scope: bool = False,
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
    if type(read_only_scope) is not bool:
        raise NativeBackendError("NATIVE_BACKEND_CONFIG_INVALID")
    if read_only_scope and implementation_id == "mordavid" and database != "neo4j":
        raise NativeBackendError("NATIVE_DATABASE_ACCESS_SCOPE_INVALID")
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
    options = dict(
        uri=uri, username=username, password=password,
        expected=expected, page_size=page_size, timeout_seconds=timeout_seconds,
        transaction_timeout_seconds=transaction_timeout_seconds, auxiliary_labels=auxiliary_labels,
    )
    if read_only_scope:
        report = await verify_native_bolt_read_only_graphs(
            **options, implementation_id=implementation_id,
            databases=("neo4j", "bloodhound") if implementation_id == "mordavid" else (database,),
        )
    else:
        report = await verify_bolt_graph(**options, database=database)
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
