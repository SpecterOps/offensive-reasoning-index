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
