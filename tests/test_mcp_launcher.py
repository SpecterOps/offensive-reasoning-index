from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from inspect_ai.tool import tool
from mcp.types import TextContent

from ori.eval.mcp_runtime import (
    MCPReadinessResult,
    MCPServerBundle,
    _canonical_tool_name,
    _create_bloodhound_mcp_server,
    _load_bloodhound_mcp_bundle,
    _readiness_response_failed,
    verify_mcp_launcher_readiness,
    write_mcp_readiness_artifact,
)
from ori.mcp_launcher import (
    CANONICAL_BLOODHOUND_MCP_EXECUTABLE,
    CANONICAL_BLOODHOUND_MCP_GIT_PREFIX,
    MCPLauncherConfig,
    build_mcp_launch_spec,
    resolve_mcp_launcher_config,
)

PIN = "cdb17097e761c8a8622cb93bc3ba49a9e150bb6e"
SOURCE = f"{CANONICAL_BLOODHOUND_MCP_GIT_PREFIX}{PIN}"


def test_uvx_git_builds_exact_shell_free_argv() -> None:
    launcher = MCPLauncherConfig.uvx_git(
        source=SOURCE,
        executable=CANONICAL_BLOODHOUND_MCP_EXECUTABLE,
    )

    spec = build_mcp_launch_spec(launcher)

    assert spec.command == "uvx"
    assert list(spec.args) == ["--from", SOURCE, "bloodhound-mcp"]
    assert spec.cwd is None
    assert launcher.revision == PIN


def test_local_checkout_preserves_exact_legacy_uv_invocation(tmp_path: Path) -> None:
    launcher = MCPLauncherConfig.local_checkout(tmp_path)

    spec = build_mcp_launch_spec(launcher)

    assert spec.command == "uv"
    assert list(spec.args) == ["--directory", str(tmp_path.resolve()), "run", "main.py"]
    assert spec.cwd == str(tmp_path.resolve())


@pytest.mark.parametrize(
    ("source", "executable"),
    [
        (f"{CANONICAL_BLOODHOUND_MCP_GIT_PREFIX}main", "bloodhound-mcp"),
        (f"{CANONICAL_BLOODHOUND_MCP_GIT_PREFIX}{PIN[:12]}", "bloodhound-mcp"),
        (f"git+https://github.com/other/bloodhound_mcp@{PIN}", "bloodhound-mcp"),
        (SOURCE, "main.py"),
    ],
)
def test_uvx_git_rejects_mutable_short_noncanonical_or_wrong_executable(
    source: str,
    executable: str,
) -> None:
    with pytest.raises(ValueError):
        MCPLauncherConfig.uvx_git(source=source, executable=executable)


def test_direct_launcher_construction_is_also_fail_closed() -> None:
    with pytest.raises(ValueError, match="source and full revision"):
        MCPLauncherConfig(
            launcher="uvx_git",
            source=SOURCE,
            executable="bloodhound-mcp",
            revision="0" * 40,
        )


def test_config_resolution_supports_pinned_uvx_and_legacy_local(tmp_path: Path) -> None:
    uvx = resolve_mcp_launcher_config(
        {"launcher": "uvx_git", "source": SOURCE, "executable": "bloodhound-mcp"},
        config_dir=tmp_path,
    )
    local = resolve_mcp_launcher_config(
        {"mcp_dir": "../Bloodhound-MCP"},
        config_dir=tmp_path,
    )

    assert uvx.revision == PIN
    assert local.mcp_dir == (tmp_path / "../Bloodhound-MCP").resolve()


@pytest.mark.parametrize("field", ["command", "args", "shell"])
def test_config_resolution_rejects_process_or_shell_fields(
    tmp_path: Path,
    field: str,
) -> None:
    section: dict[str, object] = {
        "launcher": "uvx_git",
        "source": SOURCE,
        "executable": "bloodhound-mcp",
        field: "bloodhound-mcp",
    }

    with pytest.raises(ValueError, match="must not define process fields"):
        resolve_mcp_launcher_config(section, config_dir=tmp_path)


def test_server_factory_passes_exact_argv_and_only_allowlisted_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_server_stdio(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setenv("BLOODHOUND_DOMAIN", "bh.example")
    monkeypatch.setenv("BLOODHOUND_TOKEN_ID", "credential-id-sentinel")
    monkeypatch.setenv("UNRELATED_SECRET", "never-forward-this")
    monkeypatch.setattr("ori.eval.mcp_runtime.mcp_server_stdio", fake_server_stdio)
    launcher = MCPLauncherConfig.uvx_git(source=SOURCE, executable="bloodhound-mcp")

    _create_bloodhound_mcp_server(launcher)

    assert captured == {
        "command": "uvx",
        "args": ["--from", SOURCE, "bloodhound-mcp"],
        "cwd": None,
        "env": {
            "BLOODHOUND_DOMAIN": "bh.example",
            "BLOODHOUND_TOKEN_ID": "credential-id-sentinel",
        },
    }
    assert "shell" not in captured
    assert "never-forward-this" not in repr(captured)


@pytest.mark.parametrize(
    "response",
    [
        '{"error":"backend unavailable"}',
        {"success": False, "error_type": "server_error"},
        [TextContent(type="text", text='{"error":"backend unavailable"}')],
    ],
)
def test_readiness_rejects_structured_tool_error_envelopes(response: object) -> None:
    assert _readiness_response_failed(response) is True


def test_readiness_accepts_successful_structured_tool_response() -> None:
    response = [TextContent(type="text", text='{"info_type":"list","data":[]}')]
    assert _readiness_response_failed(response) is False


def test_bundle_keeps_read_only_tools_and_filters_mutation_tools(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    @tool(name="domain_info")
    def domain_info():
        async def execute(info_type: str) -> str:
            return info_type

        return execute

    @tool(name="file_upload")
    def file_upload():
        async def execute(path: str) -> str:
            return path

        return execute

    class FakeMCPTools:
        async def tools(self):
            return [domain_info(), file_upload()]

    async def fake_resource_discovery(server):
        return [], "listed"

    monkeypatch.setattr("ori.eval.mcp_runtime.detect_uv_version", lambda: "uv 0.test")
    monkeypatch.setattr("ori.eval.mcp_runtime._create_bloodhound_mcp_server", lambda _: object())
    monkeypatch.setattr("ori.eval.mcp_runtime.mcp_tools", lambda _: FakeMCPTools())
    monkeypatch.setattr(
        "ori.eval.mcp_runtime._discover_bloodhound_mcp_resources",
        fake_resource_discovery,
    )

    bundle = asyncio.run(
        _load_bloodhound_mcp_bundle(
            MCPLauncherConfig.local_checkout(tmp_path),
            include_resources=False,
            include_prompt=False,
        )
    )

    assert [_canonical_tool_name(tool_obj) for tool_obj in bundle.tools] == ["domain_info"]


def test_readiness_runs_representative_read_only_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = []
    for name in (
        "data_quality",
        "domain_info",
        "graph_analysis",
        "cypher_query",
        "list_bloodhound_resources",
    ):
        def tool_obj() -> None:
            return None

        tool_obj.__name__ = name
        tools.append(tool_obj)
    bundle = MCPServerBundle(
        tools=tools,
        server_prompt_name="bloodhound_assistant",
        available_prompt_names=["bloodhound_assistant"],
        prompt_discovery_status="selected",
        available_resource_uris=["bloodhound://schema"],
        resource_discovery_status="listed",
        launcher_provenance={
            "mcp_launcher": "uvx_git",
            "mcp_source": SOURCE,
            "mcp_revision": PIN,
            "mcp_executable": "bloodhound-mcp",
            "uv_version": "uv 0.test",
        },
    )
    observed: list[dict[str, object]] = []

    async def fake_load(*args, **kwargs):
        return bundle

    async def fake_invoke(tool_obj, **kwargs):
        observed.append({"tool": tool_obj.__name__, "kwargs": kwargs})
        return {"status": "passed", "response_bytes": 1, "response_sha256": "0" * 64}

    monkeypatch.setattr("ori.eval.mcp_runtime._load_bloodhound_mcp_bundle", fake_load)
    monkeypatch.setattr("ori.eval.mcp_runtime._invoke_readiness_tool", fake_invoke)
    launcher = MCPLauncherConfig.uvx_git(source=SOURCE, executable="bloodhound-mcp")

    result = asyncio.run(
        verify_mcp_launcher_readiness(
            launcher,
            domain_query="GRANITEMANUFACTURING.LOCAL",
        )
    )

    assert result.ok
    assert {item["tool"] for item in observed} == {
        "data_quality",
        "domain_info",
        "graph_analysis",
        "cypher_query",
        "list_bloodhound_resources",
    }
    cypher = next(item for item in observed if item["tool"] == "cypher_query")
    assert cypher["kwargs"] == {
        "info_type": "run",
        "query": "MATCH (n:Domain) RETURN n LIMIT 1",
        "include_properties": False,
    }


def _readiness(*, captured_at: str) -> MCPReadinessResult:
    checks = {
        name: {"status": "passed", "response_bytes": 1, "response_sha256": "0" * 64}
        for name in (
            "startup_credential_preflight",
            "data_quality",
            "domain_info",
            "graph_analysis",
            "cypher_query",
            "list_bloodhound_resources",
        )
    }
    return MCPReadinessResult(
        launcher_provenance={
            "mcp_launcher": "uvx_git",
            "mcp_source": SOURCE,
            "mcp_revision": PIN,
            "mcp_executable": "bloodhound-mcp",
            "uv_version": "uv 0.test",
        },
        prompt_discovery_status="selected",
        available_prompt_names=["bloodhound_assistant"],
        resource_discovery_status="listed",
        available_resource_uris=["bloodhound://schema"],
        read_only_tools=["data_quality", "domain_info"],
        checks=checks,
        captured_at=captured_at,
    )


def test_readiness_artifact_is_immutable_and_contains_no_credentials(tmp_path: Path) -> None:
    output = tmp_path / "mcp-readiness.json"
    first = _readiness(captured_at="2026-08-18T00:00:00Z")

    artifact = write_mcp_readiness_artifact(first, output)
    write_mcp_readiness_artifact(first, output)

    assert artifact["ready"] is True
    assert artifact["provenance"]["mcp_revision"] == PIN
    assert artifact["prompt_discovery"]["succeeded"] is True
    assert artifact["resource_discovery"]["succeeded"] is True
    serialized = output.read_text()
    assert "credential-sentinel" not in serialized
    assert "BLOODHOUND_TOKEN_KEY" not in serialized
    assert json.loads(serialized)["readiness_fingerprint"]
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        write_mcp_readiness_artifact(
            _readiness(captured_at="2026-08-18T00:00:01Z"),
            output,
        )
