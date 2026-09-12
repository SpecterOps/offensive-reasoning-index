"""Offline integration fixture: real scheduler/provider parser, synthetic I/O only.

This fixture is test data, never deployment readiness or model-quality evidence.
It intentionally returns malformed model answers and cannot admit a paid run.
"""

from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path

import httpx
import yaml

from ori.eval.mcp_runtime import MCPServerBundle
from ori.eval.v2 import campaign_runner
from ori.eval.v2.campaign_export import export_v2_campaign_public
from ori.eval.v2.campaign_status import inspect_v2_campaign_status
from ori.eval.v2.diagnostic_result import inspect_canary_result
from ori.eval.v2.profiles import capability_profile_for_track
from ori.eval.v2.schema import Track


def run_synthetic_diagnostic(config_path, snapshot, verification, monkeypatch):
    """Run the installed common runtime with five invalid synthetic HTTP replies.

    Only external graph/server discovery and HTTP transport are replaced. Task
    preparation, roster, provider request/parser, MCP loop, finalizer, scoring,
    scheduler, checkpoint, graph receipt binding, publication and inspector are
    real ORI code. No socket is permitted, and no successful answer is forged.
    """
    document = yaml.safe_load(config_path.read_text())
    document["models"] = [
        {
            "name": "synthetic-invalid",
            "provider": "openai-compat",
            "model": "synthetic-invalid",
            "model_base_url": "http://127.0.0.1:8999/v1",
            "api_surface": "chat_completions",
        }
    ]
    document["defaults"]["max_infra_retries"] = 0
    document["defaults"]["infra_retry"]["immediate_retries"] = 0
    document["defaults"]["mcp"]["max_steps"] = 128
    config_path.write_text(yaml.safe_dump(document))
    requests = []
    actual_client = httpx.AsyncClient

    def deny(*args, **kwargs):
        raise AssertionError("synthetic acceptance attempted a real network connection")

    def response(request):
        assert str(request.url) == "http://127.0.0.1:8999/v1/chat/completions"
        payload = json.loads(request.content)
        assert payload["model"] == "synthetic-invalid"
        requests.append(payload)
        return httpx.Response(
            200,
            json={
                "id": "synthetic-invalid",
                "object": "chat.completion",
                "created": 0,
                "model": "synthetic-invalid",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": "deliberately not a structured answer",
                        },
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    def client(**kwargs):
        return actual_client(**kwargs, transport=httpx.MockTransport(response), trust_env=False)

    class GraphBoundary:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        def __getattr__(self, _):
            return deny

    async def graph(*args, **kwargs):
        return snapshot, verification

    async def bundle(*args, **kwargs):
        return MCPServerBundle(tools=[])

    with monkeypatch.context() as patch:
        patch.setattr(socket.socket, "connect", deny)
        patch.setattr(socket.socket, "connect_ex", deny)
        patch.setattr(socket, "getaddrinfo", deny)
        patch.delenv("OPENAI_COMPAT_API_KEY", raising=False)
        patch.setattr(httpx, "AsyncClient", client)
        patch.setattr(campaign_runner, "BHCEClient", GraphBoundary)
        patch.setattr(campaign_runner, "_health_and_graph", graph)
        patch.setattr(campaign_runner, "_load_bloodhound_mcp_bundle", bundle)
        patch.setattr(
            campaign_runner,
            "_git_revision",
            lambda _: capability_profile_for_track(Track.MCP).mcp_server_revision,
        )
        readiness = asyncio.run(campaign_runner.run_v2_campaign(config_path))
    assert len(requests) == 5
    assert readiness.purpose == "diagnostic_canary"
    status = inspect_v2_campaign_status(config_path)
    assert status.observed_state == "completed"
    result = inspect_canary_result(config_path)
    assert len(result.outcomes) == 5
    assert all(
        row.reasoning_correct is None and row.outcome.value != "COMPLETED"
        for row in result.outcomes
    )
    export = export_v2_campaign_public(
        config_path=config_path, output_dir=config_path.parent / "synthetic-public-export"
    )
    fixture = {
        "schema_version": "ori-synthetic-acceptance-fixture-v1",
        "synthetic_only": True,
        "deployment_evidence": False,
        "real_provider_calls": 0,
        "synthetic_provider_calls": len(requests),
        "config": str(config_path),
        "public_export": str(export),
        "expected_diagnostic_outcomes": [row.model_dump(mode="json") for row in result.outcomes],
    }
    (config_path.parent / "synthetic-acceptance.json").write_text(json.dumps(fixture, indent=2))
    return fixture


def acceptance_root(default: Path) -> Path:
    """Opt-in durable output for installed-wheel producer/consumer acceptance."""
    import os

    value = os.environ.get("ORI_SYNTHETIC_ACCEPTANCE_DIR")
    if value is None:
        return default
    root = Path(value)
    if not root.is_absolute() or root.exists():
        raise ValueError("synthetic acceptance requires a new absolute directory")
    root.mkdir(parents=True, mode=0o700)
    (root / "SYNTHETIC-ACCEPTANCE.md").write_text(
        "# Synthetic test fixtures only\n\n"
        "Never use these artifacts as live certification, deployment readiness, or model scores.\n"
        "The common MCP runtime receives malformed in-process synthetic HTTP replies.\n"
        "Original graph certification uses archive-backed test observations, not a live server.\n"
        "The native profile/runtime are synthetic bytes, not installed MCP qualification.\n"
    )
    return root
