"""Explicit native CE fixture qualification, not database privilege certification."""

from __future__ import annotations

from urllib.parse import urlsplit

from ori.eval.bhce import BHCEClient

from .fingerprint import canonical_sha256

CE_QUALIFICATION_KIND = "native-ce-fixture-session-v1"


def native_ce_connection(connection):
    """Validate once and bind the effective client/child target without secrets."""
    required = {"BLOODHOUND_DOMAIN", "BLOODHOUND_TOKEN_ID", "BLOODHOUND_TOKEN_KEY"}
    optional = {"BLOODHOUND_SCHEME", "BLOODHOUND_PORT", "BLOODHOUND_VERIFY_TLS"}
    try:
        if (not isinstance(connection, dict) or not required <= set(connection)
                or set(connection) - required - optional
                or any(not isinstance(value, str) or not value.strip() or "\0" in value
                       for value in connection.values())):
            raise ValueError("connection shape")
        scheme = connection.get("BLOODHOUND_SCHEME", "https")
        domain = connection["BLOODHOUND_DOMAIN"]
        port_text = connection.get("BLOODHOUND_PORT", "443" if scheme == "https" else "80")
        tls = connection.get("BLOODHOUND_VERIFY_TLS", "true").lower()
        if (scheme not in {"http", "https"} or not port_text.isascii() or not port_text.isdigit()
                or not 1 <= int(port_text) <= 65535 or tls not in {"true", "false"}
                or any(char.isspace() for char in domain)):
            raise ValueError("target shape")
        parsed = urlsplit(f"{scheme}://{domain}")
        if (not parsed.hostname or parsed.username is not None or parsed.password is not None
                or parsed.port is not None or parsed.path or parsed.query or parsed.fragment):
            raise ValueError("domain shape")
        canonical_domain = parsed.hostname.lower()
        if ":" in canonical_domain:
            canonical_domain = f"[{canonical_domain}]"
        child = {**connection, "BLOODHOUND_DOMAIN": canonical_domain,
                 "BLOODHOUND_SCHEME": scheme, "BLOODHOUND_PORT": str(int(port_text)),
                 "BLOODHOUND_VERIFY_TLS": tls}
        binding = {
            "kind": CE_QUALIFICATION_KIND, "scheme": scheme, "domain": canonical_domain,
            "port": int(port_text), "verify_tls": tls == "true", "trust_env": False,
            "token_id_fingerprint": canonical_sha256(connection["BLOODHOUND_TOKEN_ID"]),
        }
        client_options = {
            "domain": canonical_domain, "scheme": scheme, "port": int(port_text),
            "verify_tls": tls == "true", "trust_env": False,
            "token_id": connection["BLOODHOUND_TOKEN_ID"],
            "token_key": connection["BLOODHOUND_TOKEN_KEY"],
        }
        return child, client_options, binding
    except (ValueError, TypeError, KeyError):
        raise ValueError("NATIVE_CE_CONNECTION_INVALID") from None


async def observe_native_ce_graph(client: BHCEClient, expected, *, page_size=500):
    """Observe a complete exact graph without inferring privilege or quiescence."""
    from .graph import collect_live_snapshot, require_live_graph_match

    health = await client.check_health()
    if not health.ok:
        raise ValueError("NATIVE_CE_HEALTH_FAILED")
    observed, receipt = await collect_live_snapshot(client, expected, page_size=page_size)
    require_live_graph_match(expected, observed)
    return {"graph_verification": receipt.model_dump(mode="json")}


def validate_completed_native_ce_qualification(report, *, profile, expected, work_result):
    """Replay CE boundary observations without asserting Bolt privileges."""
    import json

    from .graph import LiveGraphVerification
    from .native_capability import validate_native_capability_profile
    from .native_mcp_runtime import validate_native_session_observations

    try:
        profile = validate_native_capability_profile(profile)
        if (profile.backend != "bhce" or profile.implementation_id != "mwnickerson"
                or report["qualification_kind"] != CE_QUALIFICATION_KIND
                or report["implementation_id"] != profile.implementation_id
                or report["capability_profile_fingerprint"] != profile.profile_fingerprint
                or report["scope"] != "completed-native-session-interval"
                or report["session_cleanup_confirmed"] is not True
                or report["read_only_privileges_verified"] is not False
                or report["continuous_quiescence_verified"] is not False
                or report["campaign_admitted"] is not False
                or report["qualification_work_fingerprint"] != canonical_sha256(work_result)):
            raise ValueError("CE interval binding")
        binding = report["backend_binding"]
        if (binding["kind"] != CE_QUALIFICATION_KIND or binding["trust_env"] is not False
                or type(binding["verify_tls"]) is not bool or type(binding["port"]) is not int
                or canonical_sha256(binding) != profile.backend_binding_fingerprint):
            raise ValueError("CE backend binding")
        token_fingerprint = binding["token_id_fingerprint"]
        if (not isinstance(token_fingerprint, str) or len(token_fingerprint) != 64
                or any(char not in "0123456789abcdef" for char in token_fingerprint)):
            raise ValueError("CE credential identity binding")
        # Replay canonical endpoint normalization without needing the credential.
        _, _, normalized = native_ce_connection({
            "BLOODHOUND_DOMAIN": binding["domain"], "BLOODHOUND_SCHEME": binding["scheme"],
            "BLOODHOUND_PORT": str(binding["port"]),
            "BLOODHOUND_VERIFY_TLS": str(binding["verify_tls"]).lower(),
            "BLOODHOUND_TOKEN_ID": "validation-only", "BLOODHOUND_TOKEN_KEY": "validation-only",
        })
        normalized["token_id_fingerprint"] = token_fingerprint
        if normalized != binding:
            raise ValueError("noncanonical CE binding")
        for interval in (report["graph_before"], report["graph_after"]):
            receipt = LiveGraphVerification.model_validate_json(json.dumps(
                interval["graph_verification"], allow_nan=False,
            ))
            if (receipt.expected_graph_fingerprint != expected.graph_fingerprint
                    or receipt.observed_graph_fingerprint != expected.graph_fingerprint
                    or receipt.object_count != len(expected.entities)
                    or receipt.relationship_count != len(expected.relationships)):
                raise ValueError("CE graph mismatch")
        validate_native_session_observations(
            report, profile=profile,
            backend_binding_fingerprint=profile.backend_binding_fingerprint,
        )
        return canonical_sha256(report)
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ValueError("NATIVE_COMPLETED_CE_QUALIFICATION_INVALID") from None


async def qualify_native_ce_corpus(
    *, corpus, offline, config, profile, connection, private_stderr, expected,
    timeout_seconds=1200.0, page_size=500,
):
    """Own native fixture calls between exact CE graph gates, with confirmed cleanup."""
    from .live_projection import native_qualification_guard, verify_native_corpus_interoperability

    guard, budget = native_qualification_guard(
        corpus=corpus, offline=offline, profile=profile, snapshot=expected,
    )

    async def work(session):
        return await verify_native_corpus_interoperability(
            corpus=corpus, offline=offline, profile=profile, snapshot=expected, session=session,
        )

    result, observations = await run_native_ce_session_work(
        config=config, profile=profile, connection=connection, private_stderr=private_stderr,
        expected=expected, guard=guard, max_calls=budget, work=work,
        timeout_seconds=timeout_seconds, page_size=page_size,
    )
    return result, {
        **observations, "qualification_kind": CE_QUALIFICATION_KIND,
        "qualification_work_fingerprint": canonical_sha256(result),
    }


async def run_native_ce_session_work(
    *, config, profile, connection, private_stderr, expected, guard, max_calls, work,
    timeout_seconds, page_size=500, query_coordinator=None, before_work=None,
    session_call_timeout_seconds=60.0,
):
    """Own one explicit CE work interval; not qualification or campaign admission.

    The caller supplies its own work and admission guard. Results are returned only
    after confirmed process cleanup and exact post-graph verification. Exceptions,
    including pending cleanup ownership, propagate without retry or success output.
    Campaign callbacks must checkpoint attempts before returning: a postcheck failure
    invalidates completion, but must not erase already observed provider usage.
    An optional campaign coordinator retains its caller-owned client and containment
    state across sessions. This function never closes or resets that shared client.
    Optional before_work receives a defensive copy of validated initial observations
    before work can call a provider. Its rejection still crosses cleanup and the
    post-graph gate; it never creates completed-interval observations of its own.
    """
    import asyncio
    import math
    from contextlib import nullcontext
    from copy import deepcopy

    from .native_capability import validate_native_capability_profile
    from .native_mcp_runtime import open_native_mcp_session, validate_native_session_observations

    profile = validate_native_capability_profile(profile)
    child, options, binding = native_ce_connection(connection)
    fingerprint = canonical_sha256(binding)
    if (profile.implementation_id != "mwnickerson" or profile.backend != "bhce"
            or config.implementation_id != profile.implementation_id
            or fingerprint != profile.backend_binding_fingerprint
            or config.runtime_fingerprint != profile.runtime_fingerprint
            or config.dependency_lock_fingerprint != profile.dependency_lock_fingerprint):
        raise ValueError("NATIVE_CE_BINDING_MISMATCH")
    if (type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0):
        raise ValueError("NATIVE_CE_DEADLINE_INVALID")
    if (type(session_call_timeout_seconds) not in (int, float)
            or not math.isfinite(session_call_timeout_seconds)
            or session_call_timeout_seconds <= 0):
        raise ValueError("NATIVE_CE_DEADLINE_INVALID")
    if (type(max_calls) is not int or max_calls <= 0
            or type(page_size) is not int or page_size <= 0
            or not callable(work) or not callable(guard)
            or before_work is not None and not callable(before_work)):
        raise ValueError("NATIVE_CE_WORK_BOUNDS_INVALID")
    if query_coordinator is not None:
        from ori.eval.direct_query_safety import DirectQueryCoordinator, DirectQuerySafetyConfig

        if (not isinstance(query_coordinator, DirectQueryCoordinator)
                or not isinstance(query_coordinator.bhce, BHCEClient)
                or query_coordinator.config != DirectQuerySafetyConfig()
                or any(getattr(query_coordinator.bhce, key, None) != value
                       for key, value in options.items())):
            raise ValueError("NATIVE_CE_COORDINATOR_BINDING_INVALID")
        client_scope = nullcontext(query_coordinator.bhce)
    else:
        client_scope = BHCEClient(**options)
    async with asyncio.timeout(timeout_seconds):
        async with client_scope as client:
            before = await observe_native_ce_graph(client, expected, page_size=page_size)
            admission_error = None
            async with open_native_mcp_session(
                config, connection=child, guard=guard, private_stderr=private_stderr,
                capability_profile=profile, max_calls=max_calls,
                query_coordinator=query_coordinator,
                session_call_timeout_seconds=session_call_timeout_seconds,
            ) as (session, runtime):
                discovery = deepcopy({
                    "tools": session.discovered_tools, "prompts": session.prompts,
                    "resources": session.resources,
                    "resource_templates": session.resource_templates,
                    "surface_availability": session.surface_availability,
                })
                validate_native_session_observations(
                    {"runtime": runtime, "native_discovery": discovery}, profile=profile,
                    backend_binding_fingerprint=fingerprint,
                )
                if before_work is not None:
                    try:
                        await before_work(deepcopy({
                            "backend_binding": binding,
                            "implementation_id": profile.implementation_id,
                            "capability_profile_fingerprint": profile.profile_fingerprint,
                            "graph_before": before,
                            "runtime": runtime, "native_discovery": discovery,
                        }))
                    except (Exception, asyncio.CancelledError) as exc:
                        admission_error = exc
                if admission_error is None:
                    result = await work(session)
            after = await observe_native_ce_graph(client, expected, page_size=page_size)
            if admission_error is not None:
                raise admission_error
    return result, {
        "backend_binding": binding,
        "implementation_id": profile.implementation_id,
        "capability_profile_fingerprint": profile.profile_fingerprint,
        "scope": "completed-native-session-interval", "session_cleanup_confirmed": True,
        "graph_before": before, "graph_after": after,
        "runtime": runtime, "native_discovery": discovery,
        "read_only_privileges_verified": False, "continuous_quiescence_verified": False,
        "campaign_admitted": False,
    }
