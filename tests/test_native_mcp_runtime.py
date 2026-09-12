"""Native protocol integration with an in-memory MCP server, no live backend."""

import asyncio
import json
from copy import deepcopy

import pytest
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.shared.memory import create_connected_server_and_client_session

from ori.eval.v2.native_mcp_profiles import get_native_implementation
from ori.eval.v2.native_mcp_runtime import NativeCallDecision, NativeMCPSession, _inventory
from tests.support.v2_mcp import TASK


def _server(implementation, calls, *, delay=0, error=False, native_error=False):
    profile = get_native_implementation(implementation)
    server = Server("offline-native-protocol-test")

    @server.list_tools()
    async def list_tools():
        return [
            types.Tool(
                name=name, description=f"Exact native test description: {name}",
                inputSchema={
                    "type": "object", "properties": {
                        "info_type": {"type": "string"}, "query": {"type": "string"},
                    }, "additionalProperties": False,
                },
            ) for name in profile.native_tool_names
        ]

    @server.call_tool()
    async def call_tool(name, arguments):
        calls.append((name, deepcopy(arguments)))
        if delay:
            await asyncio.sleep(delay)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps({
                "success": not (error or native_error), "data": [{"count": 3}],
            }))], isError=error,
        )

    if profile.prompt_names:
        @server.list_prompts()
        async def list_prompts():
            return [types.Prompt(name=name) for name in profile.prompt_names]

        @server.get_prompt()
        async def get_prompt(name, arguments):
            return types.GetPromptResult(messages=[types.PromptMessage(
                role="user", content=types.TextContent(type="text", text="Native prompt body"),
            )])

    if profile.resource_uris:
        @server.list_resources()
        async def list_resources():
            return [types.Resource(name=uri, uri=uri) for uri in profile.resource_uris]

        @server.list_resource_templates()
        async def list_resource_templates():
            return []

        @server.read_resource()
        async def read_resource(uri):
            return [ReadResourceContents(content="Native resource body", mime_type="text/plain")]

    return server


def _allow(implementation, name, arguments):
    return NativeCallDecision(True, "offline test backend policy")


def test_native_model_ce_query_policy_preserves_native_execution():
    async def scenario():
        calls = []
        async with create_connected_server_and_client_session(_server("mwnickerson", calls)) as raw:
            session = await NativeMCPSession.discover(raw, "mwnickerson", guard=_allow)
            token = object()
            session._bridge_attempts[(TASK.task_fingerprint, "model-attempt")] = token
            for query in (
                "CREATE (n:User)", "MATCH (n) RETURN n",
                "MATCH p=(a)-[*]->(b) RETURN p LIMIT 1",
            ):
                outcome = await session.call_tool(
                    "cypher_query", {"info_type": "run", "query": query}, TASK,
                    attempt_id="model-attempt", _bridge_token=token,
                )
                assert outcome.failure == "POLICY_REJECTED" and not outcome.executed
            assert calls == []
            arguments = {"info_type": "run", "query": "MATCH (n:Domain) RETURN count(n) AS count"}
            outcome = await session.call_tool(
                "cypher_query", arguments, TASK, attempt_id="model-attempt", _bridge_token=token,
            )
            assert outcome.executed and outcome.failure is None
            assert calls == [("cypher_query", arguments)]
            # Retain the real native reply, not a fabricated coordinator result.
            assert outcome.raw_result["content"][0]["text"] == json.dumps({
                "success": True, "data": [{"count": 3}],
            })

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "case", ["valid", "cache", "circuit", "tool_error", "timeout", "transport",
             "persist_failure", "unconfirmed_response"],
)
@pytest.mark.parametrize("tool_name", ["cypher_query", "domain_info"])
def test_native_model_calls_share_coordinator_without_substituting_replies(tmp_path, case,
                                                                         monkeypatch, tool_name):
    from types import SimpleNamespace

    from ori.eval.direct_query_safety import (
        DirectQueryCoordinator,
        DirectQuerySafetyConfig,
        QueryDenyCache,
        query_fingerprint,
    )

    calls, health_calls = [], []

    class Backend:
        async def run_cypher(self, *args, **kwargs):
            pytest.fail("native execution was replaced by direct execution")

        async def check_health(self):
            health_calls.append(True)
            return SimpleNamespace(ok=case == "transport")

    config = DirectQuerySafetyConfig()
    coordinator = DirectQueryCoordinator(
        bhce=Backend(), config=config,
        deny_cache=QueryDenyCache(tmp_path / "deny.json", manifest_fingerprint="fixture",
                                 policy_version=config.policy_version),
    )
    query = "MATCH (n:Domain) RETURN count(n) AS count"
    arguments = ({"info_type": "run", "query": query} if tool_name == "cypher_query"
                 else {"info_type": "list"})
    from ori.eval.v2.fingerprint import canonical_sha256

    fingerprint = (query_fingerprint(query) if tool_name == "cypher_query" else canonical_sha256({
        "native_operation": "mwnickerson", "tool": tool_name, "arguments": arguments,
    }))
    if case == "cache":
        coordinator.deny_cache.record(fingerprint, rule="server_query_timeout",
                                      detail="fixture")
    elif case == "circuit":
        coordinator.circuit_open = True
    elif case == "persist_failure":
        def fail_persistence():
            raise OSError("synthetic cache write failure")
        monkeypatch.setattr(coordinator.deny_cache, "_persist", fail_persistence)

    async def scenario():
        server = _server("mwnickerson", calls, native_error=case == "tool_error",
                         delay=1 if case == "timeout" else 0)
        async with create_connected_server_and_client_session(server) as raw:
            session = await NativeMCPSession.discover(
                raw, "mwnickerson", guard=_allow, query_coordinator=coordinator,
            )
            if case == "timeout":
                session._timeout = 0.02
            elif case == "transport":
                import httpx

                async def transport_failure(name, arguments):
                    calls.append((name, deepcopy(arguments)))
                    raise httpx.ReadTimeout("private transport diagnostic")

                raw.call_tool = transport_failure
            elif case == "unconfirmed_response":
                from mcp.types import CallToolResult, TextContent

                async def unconfirmed_response(name, arguments):
                    calls.append((name, deepcopy(arguments)))
                    return CallToolResult(content=[TextContent(type="text", text='{"data": []}')])

                raw.call_tool = unconfirmed_response
            token = object()
            session._bridge_attempts[(TASK.task_fingerprint, "attempt")] = token
            outcome = await session.call_tool(
                tool_name, arguments, TASK, attempt_id="attempt", _bridge_token=token,
            )
            if tool_name != "cypher_query" and outcome.query_receipt is not None:
                assert outcome.query_receipt["safety_policy_version"] is None
                if case != "cache":
                    assert outcome.query_receipt["safety_rule"] == "native_read_only_operation"
            if case == "persist_failure":
                assert not outcome.executed and calls == []
                assert outcome.failure == "HARNESS_ERROR"
                assert coordinator.deny_cache.native_recovery_required
            elif case == "unconfirmed_response":
                assert outcome.executed and outcome.raw_result is not None
                assert outcome.query_receipt["success"] is False
                assert outcome.query_receipt["failure_subtype"] == "native_completion_uncertain"
                assert outcome.query_receipt["circuit_state"] == "open"
            elif case in {"cache", "circuit"}:
                assert not outcome.executed and calls == []
                assert outcome.failure == ("POLICY_REJECTED" if case == "cache" else "INFRA_ERROR")
                assert outcome.query_receipt["query_executed"] is False
            elif case in {"timeout", "transport"}:
                assert outcome.executed and outcome.failure == "INFRA_ERROR"
                assert coordinator.circuit_open
                assert coordinator.deny_cache.reason_for(
                    fingerprint,
                ) == ("native_interrupted" if case == "timeout" else "native_transport_uncertain")
                if case == "transport":
                    assert health_calls == [True]
                    assert outcome.query_receipt["bhce_health_after"] == "healthy"
                    assert outcome.query_receipt["circuit_state"] == "open"
            else:
                assert calls == [(tool_name, arguments)] and outcome.executed
                assert outcome.raw_result["content"][0]["text"] == json.dumps({
                    "success": case == "valid", "data": [{"count": 3}],
                })
                assert outcome.failure == (None if case == "valid" else "TOOL_ERROR")
                assert outcome.query_receipt["query_fingerprint"] == fingerprint
                assert health_calls == ([] if case == "valid" else [True])
                assert coordinator.circuit_open is (case == "tool_error")
                assert coordinator.deny_cache.reason_for(fingerprint) == (
                    "native_transport_uncertain" if case == "tool_error" else None
                )

            if case in {"timeout", "transport", "tool_error", "unconfirmed_response"}:
                with pytest.raises(ValueError, match="NATIVE_QUERY_RECOVERY_REQUIRED"):
                    coordinator.close_circuit()
                restarted = DirectQueryCoordinator(
                    bhce=Backend(), config=config,
                    deny_cache=QueryDenyCache(
                        tmp_path / "deny.json", manifest_fingerprint="fixture",
                        policy_version=config.policy_version,
                    ),
                )
                assert restarted.circuit_open
                with pytest.raises(ValueError, match="NATIVE_QUERY_RECOVERY_REQUIRED"):
                    restarted.require_confirmed_native_completion()
                result = await restarted.execute("MATCH (n:User) RETURN count(n)")
                assert not result.query_executed and result.failure_type == "circuit_open"

    asyncio.run(scenario())


@pytest.mark.parametrize("implementation", ["mordavid", "armadin"])
@pytest.mark.parametrize("failure", [False, True])
def test_bolt_owned_calls_bind_backend_without_ce_probe(tmp_path, implementation, failure):
    from ori.eval.direct_query_safety import (
        DirectQueryCoordinator,
        DirectQuerySafetyConfig,
        QueryDenyCache,
    )
    from ori.eval.v2.compiler import _binding, _fingerprinted_task_bundle
    from ori.eval.v2.fingerprint import canonical_sha256
    from ori.eval.v2.native_capability import build_native_capability_profile
    from ori.eval.v2.schema import Track
    from tests.support.v2_mcp import ALICE, CLAIM, POLICY, TARGET

    class NoCE:
        async def check_health(self):
            pytest.fail("a Bolt tool must not probe BloodHound CE")

    async def scenario():
        calls = []
        async with create_connected_server_and_client_session(
            _server(implementation, calls, native_error=failure),
        ) as raw:
            discovered = await NativeMCPSession.discover(raw, implementation, guard=_allow)
            profile = build_native_capability_profile(
                implementation, tools=discovered.tools, prompts=discovered.prompts,
                resources=discovered.resources, resource_templates=discovered.resource_templates,
                surface_availability=discovered.surface_availability,
                runtime_fingerprint="a" * 64, dependency_lock_fingerprint="b" * 64,
                backend_binding_fingerprint="c" * 64,
            )
            config = DirectQuerySafetyConfig()
            coordinator = DirectQueryCoordinator(
                bhce=NoCE(), config=config,
                deny_cache=QueryDenyCache(tmp_path / "deny.json", manifest_fingerprint="fixture",
                                         policy_version=config.policy_version),
                native_backend_binding_fingerprint="c" * 64,
            )
            session = await NativeMCPSession.discover(
                raw, implementation, guard=_allow, capability_profile=profile,
                query_coordinator=coordinator,
            )
            claim = CLAIM.model_copy(update={"required_mechanisms": ()})
            task = _fingerprinted_task_bundle(
                task_id="native-containment", product="simple", claim=claim, policy=POLICY,
                binding=_binding(Track.MCP, claim=claim, expected_cardinality=2,
                                 native_profile=profile),
                input_entities=(ALICE, TARGET), question="Find the declared route.",
            )
            token = object()
            session._bridge_attempts[(task.task_fingerprint, "attempt")] = token
            name = "query_bloodhound" if implementation == "mordavid" else "find_domains"
            args = {"query": "RETURN 3 AS count"} if implementation == "mordavid" else {}
            outcome = await session.call_tool(name, args, task, attempt_id="attempt",
                                              _bridge_token=token)
            assert outcome.executed and calls == [(name, args)]
            receipt = outcome.query_receipt
            assert receipt["safety_policy_version"] is None
            assert receipt["bhce_health_after"] == "not_applicable"
            assert receipt["query_fingerprint"] == canonical_sha256({
                "backend_binding": "c" * 64,
                "native_operation": canonical_sha256({
                    "native_operation": implementation, "tool": name, "arguments": args,
                }),
            })
            assert coordinator.deny_cache.native_recovery_required is failure
            assert receipt["success"] is not failure
            if failure:
                with pytest.raises(ValueError, match="NATIVE_QUERY_RECOVERY_REQUIRED"):
                    coordinator.close_circuit()
            coordinator.native_backend_binding_fingerprint = "d" * 64
            with pytest.raises(ValueError, match="backend binding mismatch"):
                await NativeMCPSession.discover(
                    raw, implementation, guard=_allow, capability_profile=profile,
                    query_coordinator=coordinator,
                )
            assert calls == [(name, args)]

    asyncio.run(scenario())


@pytest.mark.parametrize("implementation", ["mwnickerson", "mordavid", "armadin"])
@pytest.mark.parametrize("provider", ["openai", "ollama", "anthropic"])
@pytest.mark.parametrize("protocol_case", [
    "success", "timeout", "truncated", "mixed", "exhaustion",
])
def test_native_bridge_protocol_surfaces_remain_native_and_nonproof(implementation, monkeypatch,
                                                                  provider, protocol_case):
    from ori.eval.v2.compiler import _binding, _fingerprinted_task_bundle
    from ori.eval.v2.native_capability import build_native_capability_profile
    from ori.eval.v2.native_mcp_runtime import NativeCallOutcome, NativeModelToolBridge
    from ori.eval.v2.schema import (
        ExactSetPolicy,
        PopulationScope,
        SelectionExpression,
        SetClaim,
        Track,
    )

    async def scenario():
        async with create_connected_server_and_client_session(_server(implementation, [])) as raw:
            discovered = await NativeMCPSession.discover(raw, implementation, guard=_allow)
            # Match the actual discovery descriptors, including mixed tools
            # retained privately by session admission rather than tool exposure.
            descriptors = dict(tools=list(discovered._tools.values()),
                               prompts=discovered.prompts, resources=discovered.resources,
                               resource_templates=discovered.resource_templates,
                               surface_availability=discovered.surface_availability)
            profile = build_native_capability_profile(
                implementation, runtime_fingerprint="1" * 64,
                dependency_lock_fingerprint="2" * 64, backend_binding_fingerprint="3" * 64,
                **descriptors,
            )
            session = NativeMCPSession(raw, implementation, guard=_allow,
                                       capability_profile=profile, **descriptors)
            claim = SetClaim(kind="set", claim_id="domains", semantics="direct",
                             population_scope=PopulationScope.BENCHMARK_NAMESPACE,
                             selection=SelectionExpression(projection_role="item",
                                                           projection_type="Domain"))
            task = _fingerprinted_task_bundle(
                task_id="native-domains", product="simple", claim=claim,
                policy=ExactSetPolicy(kind="exact_set"),
                binding=_binding(Track.MCP, claim=claim, expected_cardinality=1,
                                 native_profile=profile),
                input_entities=(), question="Return the set of benchmark domains.",
            )
            bridge = NativeModelToolBridge(session, task, attempt_id="protocol-test")
            surface = bridge.protocol_surfaces
            assert surface["availability"] == descriptors["surface_availability"]
            tool_names = [t.descriptor["name"] for t in bridge.tools]
            if surface["prompts"]:
                prompt = await bridge.get_prompt(surface["prompts"][0]["name"], {})
                assert prompt.raw_result["messages"][0]["content"]["text"] == "Native prompt body"
            else:
                assert (await bridge.get_prompt("invented", {})).failure == "CAPABILITY_UNAVAILABLE"
            if surface["resources"]:
                uri = surface["resources"][0]["uri"]
                for _ in range(2):
                    resource = await bridge.read_resource(uri)
                    assert resource.raw_result["contents"][0]["text"] == "Native resource body"
                reads = [r for r in bridge.protocol_calls if r["operation"] == "read_resource"]
                assert [r["duplicate_read"] for r in reads] == [False, True]
                assert all(r["outcome"].executed and r["outcome"].failure is None for r in reads)
                assert [event.kind.value for event in bridge.events] == ["resource_read"] * 2
            unavailable = await bridge.read_resource("invented://resource")
            assert unavailable.failure == "CAPABILITY_UNAVAILABLE" and not unavailable.executed
            assert all(not r["evidence_producing"] for r in bridge.protocol_calls)
            assert not bridge.finalization_ready and bridge.outcomes == []
            assert tool_names == [t.descriptor["name"] for t in bridge.tools]
            copied = bridge.protocol_calls
            copied.clear()
            assert bridge.protocol_calls
            from types import SimpleNamespace

            from ori.eval import anthropic_mcp, mcp_runtime

            requests = []
            before_calls = len(bridge.protocol_calls)
            steps = before_calls if protocol_case == "exhaustion" else 16
            if protocol_case == "timeout":
                async def stall(uri):
                    await asyncio.sleep(1)

                monkeypatch.setattr(session, "read_resource", stall)
            request = json.dumps({"ori_native_protocol": {
                "operation": "read_resource",
                "uri": surface["resources"][0]["uri"] if surface["resources"] else "missing://uri",
            }})
            async def turn(**kwargs):
                requests.append(deepcopy(kwargs))
                content = request if len(requests) == 1 else "{}"
                mixed = [{"id": "mixed-call", "type": "function", "function": {
                    "name": tool_names[0], "arguments": "{}" if provider == "openai" else {},
                }}] if protocol_case == "mixed" else []
                return dict(model="fixture", thinking="", content=content, tool_calls=mixed,
                            prompt_tokens=10, completion_tokens=5, prompt_eval_count=10,
                            eval_count=5, finish_reason="length" if protocol_case == "truncated"
                            else "stop")

            if provider == "anthropic":
                async def create(**kwargs):
                    response = await turn(**kwargs)
                    return {"id": "fixture", "model": "fixture", "type": "message",
                            "role": "assistant", "stop_reason": "max_tokens"
                            if protocol_case == "truncated" else "tool_use"
                            if protocol_case == "mixed" else "end_turn",
                            "content": [{"type": "text", "text": response["content"]}] + ([{
                                "type": "tool_use", "id": "mixed-call", "name": tool_names[0],
                                "input": {},
                            }] if protocol_case == "mixed" else []),
                            "usage": {"input_tokens": 10, "output_tokens": 5}}

                async def close():
                    pass

                async def materialize(_):
                    return SimpleNamespace(client=SimpleNamespace(default_headers={},
                        _validate_headers=lambda *_: None, messages=SimpleNamespace(create=create)),
                        aclose=close)

                monkeypatch.setattr(anthropic_mcp, "materialize_anthropic_client", materialize)
                operation = anthropic_mcp.run_anthropic_mcp_loop(
                    bridge=bridge, binding=SimpleNamespace(model_slug="fixture", base_url="https://api.anthropic.com"),
                    system_prompt="Authoritative contract", max_steps=steps, max_tokens=100,
                    read_timeout_seconds=10, tool_timeout_seconds=0.01,
                )
            else:
                monkeypatch.setattr(mcp_runtime, "_openai_compat_chat_turn", turn)
                monkeypatch.setattr(mcp_runtime, "_ollama_chat_turn", turn)
                run_loop = (mcp_runtime._run_ollama_mcp_loop if provider == "ollama"
                            else mcp_runtime._run_openai_compat_mcp_loop)
                operation = run_loop(
                    task=None, public_question=task.question, model_name="fixture",
                    base_url="http://127.0.0.1:8080", tools=bridge.tools, max_steps=steps,
                    system_prompt_override="Authoritative contract", native_protocol_bridge=bridge,
                    tool_timeout_seconds=0.01,
                    **({"ollama_options": None} if provider == "ollama" else {"extra_body": None}),
                )
            if protocol_case == "timeout":
                with pytest.raises(mcp_runtime.MCPToolInfrastructureError):
                    await operation
                assert bridge.interrupted and bridge.protocol_calls[-1]["interrupted"]
                assert len(requests) == 1
                return
            response = (await operation)[0]
            if protocol_case != "success":
                assert len(requests) == 1 and not bridge.native_tool_calls
                assert len(bridge.protocol_calls) == before_calls + 1
                assert bridge.protocol_calls[-1]["operation"] == "invalid_request"
                return
            assert response.raw_text == "{}" and response.tokens_input == 20
            assert len(requests) == 2
            assert "ori_native_protocol_catalog" in json.dumps(requests[0]["messages"])
            assert "ori_native_protocol_result" in json.dumps(requests[1]["messages"])
            assert len(requests[0]["tools"]) == len(tool_names)
            assert not bridge.finalization_ready and not bridge.native_tool_calls
            with monkeypatch.context() as context:
                async def nested(uri):
                    return NativeCallOutcome(True, 0.01, {"contents": [{"text": request}]})

                context.setattr(session, "read_resource", nested)
                count = len(bridge.protocol_calls)
                text, accepted = await bridge.dispatch_protocol_text(request)
                assert accepted and len(bridge.protocol_calls) == count + 1
                data = json.loads(text)["ori_native_protocol_result"]["data"]
                assert data["contents"][0]["text"] == request
                async def oversized(uri):
                    return NativeCallOutcome(True, 0.01, {"contents": [{"text": "x" * 65537}]})

                context.setattr(session, "read_resource", oversized)
                text, accepted = await bridge.dispatch_protocol_text(request)
                assert accepted and len(text.encode()) <= 65536
                assert json.loads(text)["ori_native_protocol_result"]["failure"] == "OUTPUT_LIMIT"
                assert bridge.protocol_calls[-1]["outcome"].raw_result is not None
            for malformed in (
                '{"ori_native_protocol":{"operation":"read_resource","uri":123}}',
                '{"ori_native_protocol":{"operation":"get_prompt","name":"x","arguments":[]}}',
                '{"ori_native_protocol":{"operation":"get_prompt","name":"x","arguments":{"x":1}}}',
                '{"ori_native_protocol":{},"ori_native_protocol":{}}',
                'prefix {"ori_native_protocol":{}}',
                '{"ori_native_protocol":{"operation":"get_prompt","name":"x","arguments":{"x":NaN}}}',
            ):
                assert await bridge.dispatch_protocol_text(malformed) == ("", False)
                assert bridge.protocol_calls[-1]["operation"] == "invalid_request"
                assert not bridge.protocol_calls[-1]["outcome"].executed
            escaped = request.replace("ori_native_protocol", "ori_native_\\u0070rotocol")
            assert await bridge.dispatch_protocol_text(escaped, mixed_tools=True) == ("", False)
            assert await bridge.dispatch_protocol_text(request, enabled=False) == ("", False)
            async def timed_out(uri):
                return NativeCallOutcome(True, 0.1, failure="INFRA_ERROR")

            monkeypatch.setattr(session, "read_resource", timed_out)
            failed = await bridge.read_resource("native://timeout-fixture")
            assert failed.failure == "INFRA_ERROR"
            assert bridge.events[-1].kind.value == "infrastructure_failure"
            assert not bridge.finalization_ready

            async def cancelled(name, arguments):
                raise asyncio.CancelledError

            monkeypatch.setattr(session, "get_prompt", cancelled)
            with pytest.raises(asyncio.CancelledError):
                await bridge.get_prompt("cancelled-prompt", {})
            assert bridge.protocol_calls[-1]["interrupted"]
            assert bridge.protocol_calls[-1]["outcome"] is None
            assert bridge.interrupted and not bridge.finalization_ready
            with pytest.raises(RuntimeError, match="ATTEMPT_INTERRUPTED"):
                await bridge.read_resource("native://after-interruption")

    asyncio.run(scenario())


def test_native_protocol_discovery_dispatch_and_missing_surfaces(subtests):
    async def scenario(implementation):
        calls = []
        server = _server(implementation, calls)
        async with create_connected_server_and_client_session(server) as raw:
            native = await NativeMCPSession.discover(raw, implementation, guard=_allow)
            profile = get_native_implementation(implementation)
            listed = await raw.list_tools()
            originals = {item.name: item.model_dump(mode="json", by_alias=True)
                         for item in listed.tools}
            assert native.tools
            assert all(item == originals[item["name"]] for item in native.tools)
            leaked_copy = native.tools
            leaked_copy[0]["description"] = "changed"
            assert native.tools[0]["description"] != "changed"
            assert native.surface_availability["resources"] == profile.resources_expected
            if not profile.resources_expected:
                assert native.resources == []
            else:
                assert native.resources
                resource = await native.read_resource(profile.resource_uris[0])
                assert resource.raw_result["contents"][0]["text"] == "Native resource body"
            name = profile.generic_query_tool or "find_domains"
            args = {"query": "RETURN 3 AS count"} if profile.generic_query_tool else {}
            if implementation == "mwnickerson":
                args["info_type"] = "run"
            result = await native.call_tool(name, args, TASK)
            assert result.executed and result.failure is None
            assert calls == [(name, args)]  # actual native callback, not CE substitution
            assert result.raw_result["content"][0]["text"] == json.dumps({
                "success": True, "data": [{"count": 3}],
            })
            assert result.projection is not None
            assert result.projection.status in {"observed", "inconclusive", "unsupported"}
            unavailable = await native.read_resource("invented://resource")
            assert unavailable.failure == "CAPABILITY_UNAVAILABLE" and not unavailable.executed
            if profile.prompt_names:
                prompt = await native.get_prompt(profile.prompt_names[0], {})
                assert prompt.raw_result["messages"][0]["content"]["text"] == "Native prompt body"
            else:
                assert not native.surface_availability["prompts"]
                assert (await native.get_prompt("invented", {})).failure == "CAPABILITY_UNAVAILABLE"
            before_close = native.calls
            native.invalidate()
            closed = await native.call_tool(name, args, TASK)
            assert not closed.executed and closed.failure == "HARNESS_ERROR"
            assert native.calls == before_close and calls == [(name, args)]
            if profile.prompt_names:
                assert not (await native.get_prompt(profile.prompt_names[0], {})).executed
            if profile.resource_uris:
                assert not (await native.read_resource(profile.resource_uris[0])).executed

    for implementation in ("mwnickerson", "mordavid", "armadin"):
        with subtests.test(implementation=implementation):
            asyncio.run(scenario(implementation))


def test_native_admission_blocks_dispatch_and_cannot_rewrite_arguments(subtests):
    async def scenario():
        calls = []

        def guard(implementation, name, arguments):
            denied = arguments.get("query") == "DELETE n"
            arguments["query"] = "guard attempted rewrite"
            return NativeCallDecision(not denied, "test decision")

        async with create_connected_server_and_client_session(_server("mwnickerson", calls)) as raw:
            native = await NativeMCPSession.discover(raw, "mwnickerson", guard=guard, max_calls=6)
            assert "file_upload" not in {item["name"] for item in native.tools}
            for name, args, expected in (
                ("file_upload", {}, "POLICY_REJECTED"),
                ("user_info", {"info_type": "delete"}, "POLICY_REJECTED"),
                ("invented", {}, "CAPABILITY_UNAVAILABLE"),
                ("cypher_query", {"info_type": "run", "query": 3}, "ARGUMENT_INVALID"),
                ("cypher_query", {"info_type": "run", "query": "DELETE n"}, "POLICY_REJECTED"),
            ):
                with subtests.test(name=name, expected=expected):
                    result = await native.call_tool(name, args, TASK)
                    assert result.failure == expected and not result.executed
            assert calls == []
            args = {"info_type": "run", "query": "RETURN 3 AS count"}
            result = await native.call_tool("cypher_query", args, TASK)
            assert result.executed
            assert calls == [("cypher_query", args)]
            assert args["query"] == "RETURN 3 AS count"
            exhausted = await native.call_tool("cypher_query", args, TASK)
            assert exhausted.failure == "CALL_LIMIT" and not exhausted.executed

    asyncio.run(scenario())


def test_native_error_in_unprojected_tool_is_still_failed_call(subtests):
    async def scenario(implementation, name, args):
        calls = []
        server = _server(implementation, calls, native_error=True)
        async with create_connected_server_and_client_session(server) as raw:
            native = await NativeMCPSession.discover(raw, implementation, guard=_allow)
            outcome = await native.call_tool(name, args, TASK)
            assert outcome.executed and outcome.failure == "TOOL_ERROR"
            assert outcome.projection.status == "tool_error"
            assert outcome.raw_result["isError"] is False
            assert calls == [(name, args)]

    for implementation, name, args in (
        ("mwnickerson", "domain_info", {"info_type": "list"}),
        ("mordavid", "map_domain_trusts", {}),
        ("armadin", "find_tier_zero_assets", {}),
    ):
        with subtests.test(implementation=implementation):
            asyncio.run(scenario(implementation, name, args))


def test_native_errors_timeouts_and_result_bounds(subtests):
    async def scenario(delay, error, limit, expected):
        calls = []
        async with create_connected_server_and_client_session(
            _server("mordavid", calls, delay=delay, error=error),
        ) as raw:
            native = await NativeMCPSession.discover(raw, "mordavid", guard=_allow)
            native._timeout = 0.01
            native._max_result_bytes = limit
            result = await native.call_tool("query_bloodhound", {}, TASK)
            assert result.executed and result.failure == expected
            assert len(calls) == 1

    for delay, error, limit, expected in (
        (0, True, 4000, "TOOL_ERROR"),
        (0.05, False, 4000, "INFRA_ERROR"),
        (0, False, 10, "OUTPUT_LIMIT"),
    ):
        with subtests.test(expected=expected):
            asyncio.run(scenario(delay, error, limit, expected))


def test_native_discovery_cursor_loop_fails_closed():
    class Cyclic:
        async def list_tools(self, cursor=None):
            return types.ListToolsResult(tools=[], nextCursor="repeat")

    with pytest.raises(ValueError, match="cyclic"):
        asyncio.run(_inventory(Cyclic(), "list_tools", "tools"))


def test_native_malformed_call_inputs_do_not_escape_or_dispatch(subtests):
    async def scenario():
        calls = []
        async with create_connected_server_and_client_session(_server("mwnickerson", calls)) as raw:
            native = await NativeMCPSession.discover(raw, "mwnickerson", guard=_allow)
            for name, arguments in (
                ([], {}), ({}, {}), ("cypher_query", {"info_type": []}),
                ("cypher_query", {"info_type": {}}), ("cypher_query", None),
                ("cypher_query", {"info_type": "run", "query": float("nan")}),
            ):
                with subtests.test(name=name, arguments=arguments):
                    result = await native.call_tool(name, arguments, TASK)
                    assert result.failure == "ARGUMENT_INVALID" and not result.executed
            assert (await native.get_prompt([], {})).failure == "ARGUMENT_INVALID"
            assert (await native.read_resource({})).failure == "ARGUMENT_INVALID"
            assert calls == []

    asyncio.run(scenario())


def test_native_main_mixed_read_operations_and_defaults_are_preserved(subtests):
    async def scenario():
        calls = []
        async with create_connected_server_and_client_session(_server("mwnickerson", calls)) as raw:
            native = await NativeMCPSession.discover(raw, "mwnickerson", guard=_allow)
            assert {"custom_nodes", "asset_groups"}.issubset(
                item["name"] for item in native.tools
            )
            for name, arguments in (
                ("domain_info", {}), ("custom_nodes", {}), ("asset_groups", {}),
                ("custom_nodes", {"info_type": "extension_list"}),
                ("asset_groups", {"info_type": "list_tags"}),
            ):
                with subtests.test(name=name, arguments=arguments):
                    outcome = await native.call_tool(name, arguments, TASK)
                    assert outcome.executed and outcome.failure is None
                    assert calls[-1] == (name, arguments)  # no injected defaults or rewrites
            assert len(calls) == 5
            for name, mode in (
                ("custom_nodes", "extension_upsert"), ("custom_nodes", "extension_delete"),
                ("custom_nodes", "create"), ("asset_groups", "update_selectors"),
                ("asset_groups", "delete"), ("asset_groups", "create_tag"),
            ):
                with subtests.test(name=name, mode=mode):
                    outcome = await native.call_tool(name, {"info_type": mode}, TASK)
                    assert not outcome.executed and outcome.failure == "POLICY_REJECTED"
            assert len(calls) == 5

    asyncio.run(scenario())
