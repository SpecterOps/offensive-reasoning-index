"""Finite offline destination binding tests for native Ollama inference."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import socket
import subprocess
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from ori.eval import adapter, mcp_runtime
from ori.eval.provider_contract import ProviderCapabilityError
from ori.eval.v2 import campaign_runner as runner
from ori.eval.v2.campaign_config import ResolvedV2CampaignConfig, V2CampaignConfig
from ori.eval.v2.schema import Track, canonical_sha256
from tests.support.v2_campaign import _base_provenance

CURRENT_CASE = None
A = "http://endpoint-a.invalid:11434/private-route"
B = "http://endpoint-b.invalid:22434/other-route"
LOCAL = "http://localhost:11434"
MODEL = {"name": "synthetic", "provider": "ollama", "model": "namespace/model:tag"}
OTHER = {"name": "other", "provider": "openai", "model": "synthetic-other"}
OPTIONS = {"num_ctx": 8192, "temperature": 0}


def _binding():
    return importlib.import_module("ori.eval.ollama_binding")


@contextmanager
def _case(case, tmp_path, monkeypatch):
    global CURRENT_CASE
    CURRENT_CASE = case
    root = tmp_path / case
    root.mkdir()
    state = SimpleNamespace(
        root=root,
        counts=Counter(),
        requests=[],
        clients=[],
        text="synthetic-answer",
        status=200,
    )
    original_client = httpx.AsyncClient
    with monkeypatch.context() as patch:
        state.patch = patch
        patch.setattr(os, "environ", {})
        patch.setattr(Path, "home", classmethod(lambda cls: root))

        def denied(*args, **kwargs):
            state.counts["external"] += 1
            raise AssertionError("OLLAMA_EXTERNAL_OPERATION")

        for owner, name in (
            (socket.socket, "connect"),
            (socket.socket, "connect_ex"),
            (socket, "getaddrinfo"),
            (subprocess, "Popen"),
        ):
            patch.setattr(owner, name, denied)

        def receive(request):
            state.counts["request"] += 1
            state.requests.append(request)
            chunks = (
                {
                    "model": "namespace/model:tag",
                    "message": {"content": state.text, "thinking": "synthetic-thinking"},
                },
                {
                    "model": "namespace/model:tag",
                    "done": True,
                    "prompt_eval_count": 11,
                    "eval_count": 7,
                    "total_duration": 100,
                    "load_duration": 10,
                    "prompt_eval_duration": 30,
                    "eval_duration": 60,
                },
            )
            return httpx.Response(state.status, content="\n".join(map(json.dumps, chunks)))

        class Client(original_client):
            def __init__(self, *args, **kwargs):
                state.counts["constructor"] += 1
                super().__init__(*args, transport=httpx.MockTransport(receive), **kwargs)
                state.clients.append(self)

            async def __aexit__(self, *args):
                await super().__aexit__(*args)
                state.counts["close"] += 1

        patch.setattr(httpx, "AsyncClient", Client)
        try:
            yield state
        finally:
            CURRENT_CASE = None
            assert state.counts["external"] == 0
            assert all(client.is_closed for client in state.clients)


def _resolved(s, *, models=None, defaults=None, prepare=True):
    for name in ("manifest.json", "archive.zip"):
        (s.root / name).touch(exist_ok=True)
    config = V2CampaignConfig.model_validate(
        {
            "version": 2,
            "source": {"manifest": "manifest.json", "archive": "archive.zip"},
            "tracks": {
                "direct": {
                    "public": "public.json",
                    "oracles": "oracle.json",
                    "candidates": "candidate.json",
                    "live_certification": "live.json",
                }
            },
            "modes": ["direct"],
            "output_dir": "campaign",
            "defaults": defaults or {},
            "models": [dict(MODEL)] if models is None else models,
        }
    )
    resolved = ResolvedV2CampaignConfig(
        source_manifest=s.root / "manifest.json",
        archive=s.root / "archive.zip",
        tracks={},
        output_dir=s.root / "campaign",
        config=config,
        source_config_fingerprint="a" * 64,
        mcp_dir=s.root,
    )
    if prepare:
        runner._prepare_ollama_endpoints(resolved)
    return resolved


def _endpoint(resolved):
    return runner._ollama_endpoint(resolved.config.models[0], resolved)


def _no_env(s):
    original = os.getenv

    def lookup(name, default=None):
        if name == "OLLAMA_BASE_URL":
            s.counts["forbidden_env"] += 1
            raise AssertionError("OLLAMA_ENVIRONMENT_RESELECTED")
        return original(name, default)

    s.patch.setattr(os, "getenv", lookup)


def _direct(*, base_url=None, model="ollama/namespace/model:tag"):
    return asyncio.run(
        adapter.call_provider_text(
            model=model,
            base_url=base_url,
            system="synthetic-system",
            messages=[{"role": "user", "content": "synthetic-question"}],
            ollama_options=dict(OPTIONS),
        )
    )


def _native(*, base_url=None, model="ollama/namespace/model:tag"):
    return asyncio.run(
        mcp_runtime._ollama_chat_turn(
            url=mcp_runtime._native_ollama_chat_url(base_url),
            model_name=model,
            messages=[{"role": "user", "content": "synthetic-question"}],
            tools=[],
            ollama_options=dict(OPTIONS),
            read_timeout_seconds=1.0,
        )
    )


def _provenance(s, resolved):
    s.patch.setattr(runner, "build_run_provenance", lambda *args: _base_provenance(Track.DIRECT))
    return runner._provenance(
        resolved=resolved,
        prepared=SimpleNamespace(
            track=Track.DIRECT,
            pair=object(),
            profile=object(),
            release=SimpleNamespace(release_fingerprint="b" * 64),
            live=SimpleNamespace(artifact_fingerprint="c" * 64),
        ),
        model=resolved.config.models[0],
        run_index=1,
        loop=None,
    )


def test_ollama_endpoint_selection(monkeypatch, tmp_path, subtests):
    rows = (
        ("R00", A, B, A, A + "/api/chat"),
        ("R01", "", B, B, B + "/api/chat"),
        ("R02", None, None, LOCAL, LOCAL + "/api/chat"),
        ("R03", None, A, A, A + "/api/chat"),
        (
            "R04",
            "http://node.invalid:22434",
            None,
            "http://node.invalid:22434",
            "http://node.invalid:22434/api/chat",
        ),
        ("R05", "http://[::1]:11434", None, "http://[::1]:11434", "http://[::1]:11434/api/chat"),
        ("R06", A + "///", None, A + "///", A + "/api/chat"),
        ("R07", A + "/v1", None, A + "/v1", A + "/api/chat"),
        ("R08", A + "/v1/", None, A + "/v1/", A + "/api/chat"),
        ("R09", A + "/api", None, A + "/api", A + "/api/api/chat"),
        ("R10", A + "/api/chat", None, A + "/api/chat", A + "/api/chat/api/chat"),
        ("R11", A + "/v1/v1", None, A + "/v1/v1", A + "/v1/api/chat"),
        ("R12", None, "", None, None),
        ("R13", None, "///", None, None),
        ("R14", "///", B, None, None),
    )
    visited = []
    for case, explicit, env, selected, final in rows:
        visited.append(case)
        with subtests.test(case=case), _case(case, tmp_path, monkeypatch) as s:
            if env is not None:
                os.environ["OLLAMA_BASE_URL"] = env
            if selected is None:
                with pytest.raises(ProviderCapabilityError) as error:
                    _binding().prepare_ollama_endpoint(explicit)
                assert A not in str(error.value) and B not in str(error.value)
            else:
                endpoint = _binding().prepare_ollama_endpoint(explicit)
                assert endpoint.selected_base_url == selected
                assert endpoint.chat_url == final
                assert selected not in repr(endpoint)
            assert not s.counts
    assert visited == [f"R{i:02}" for i in range(15)]


def test_ollama_model_contract(monkeypatch, tmp_path, subtests):
    rows = (
        ("M00", "ollama/model", "model"),
        ("M01", "ollama/namespace/model:tag", "namespace/model:tag"),
        ("M02", "ollama/ Model:TAG ", " Model:TAG "),
        ("M03", "ollama/   ", None),
        ("M04", "ollama/", None),
        ("M05", "ollama/model@" + A, None),
        ("M06", "ollama/model@" + B, None),
    )
    visited = []
    for case, model, expected in rows:
        visited.append(case)
        with subtests.test(case=case), _case(case, tmp_path, monkeypatch) as s:
            if expected is not None:
                assert _binding().ollama_model_name(model) == expected
            else:
                with pytest.raises(ProviderCapabilityError) as caught:
                    _binding().ollama_model_name(model)
                assert model not in str(caught.value)
                result = _direct(model=model, base_url=A)
                assert result.provider_metrics["infra_error_subtype"] == "PROVIDER_CAPABILITY"
                with pytest.raises(ProviderCapabilityError):
                    _native(model=model, base_url=A)
            assert not s.counts
    assert visited == [f"M{i:02}" for i in range(7)]


def test_ollama_direct_native_request_parity(monkeypatch, tmp_path, subtests):
    rows = (
        ("P00", A, B, A + "/api/chat"),
        ("P01", None, A, A + "/api/chat"),
        ("P02", None, None, LOCAL + "/api/chat"),
        ("P03", A + "/v1/v1", None, A + "/v1/api/chat"),
        ("P04", "http://[::1]:11434", None, "http://[::1]:11434/api/chat"),
    )
    visited = []
    for case, explicit, env, expected in rows:
        visited.append(case)
        with subtests.test(case=case), _case(case, tmp_path, monkeypatch) as s:
            if env is not None:
                os.environ["OLLAMA_BASE_URL"] = env
            direct = _direct(base_url=explicit)
            native = _native(base_url=explicit)
            assert direct.error is None and direct.raw_text == s.text
            assert (direct.tokens_input, direct.tokens_output) == (11, 7)
            assert native["content"] == s.text and native["thinking"] == "synthetic-thinking"
            assert (native["prompt_eval_count"], native["eval_count"]) == (11, 7)
            assert [str(request.url) for request in s.requests] == [expected, expected]
            for request in s.requests:
                body = json.loads(request.content)
                assert request.method == "POST"
                assert body["model"] == "namespace/model:tag"
                assert body["stream"] is True and body["options"] == OPTIONS
                assert body["messages"][-1] == {"role": "user", "content": "synthetic-question"}
            assert s.counts == {"constructor": 2, "request": 2, "close": 2}
    assert visited == [f"P{i:02}" for i in range(5)]


def test_ollama_v2_preparation(monkeypatch, tmp_path, subtests):
    ids = tuple(f"V{i:02}" for i in range(7))
    visited = []
    for case in ids:
        visited.append(case)
        with subtests.test(case=case), _case(case, tmp_path, monkeypatch) as s:
            os.environ["OLLAMA_BASE_URL"] = B
            models, defaults = [dict(MODEL)], {}
            if case == "V00":
                models[0]["model_base_url"] = A
                defaults["model_base_url"] = LOCAL
            if case == "V01":
                defaults["model_base_url"] = A
            if case == "V03":
                del os.environ["OLLAMA_BASE_URL"]
            if case == "V04":
                models.append({**MODEL, "name": "bad", "model_base_url": "///"})
            if case == "V05":
                models = [dict(OTHER)]
            if case == "V06":
                models.append(dict(OTHER))
            resolved = _resolved(s, models=models, defaults=defaults, prepare=False)
            if case == "V04":
                with pytest.raises(runner.V2CampaignRunError):
                    runner._prepare_ollama_endpoints(resolved)
                assert resolved._ollama_endpoints is None
                assert resolved._ollama_mutation_fingerprint is None
            elif case == "V06":
                with pytest.raises(runner.V2CampaignRunError, match="OPENAI_API_KEY"):
                    runner._model_readiness(resolved)
                assert _endpoint(resolved).selected_base_url == B
            else:
                runner._prepare_ollama_endpoints(resolved)
                if case == "V05":
                    assert dict(resolved._ollama_endpoints) == {}
                else:
                    expected = A if case in {"V00", "V01"} else LOCAL if case == "V03" else B
                    assert _endpoint(resolved).selected_base_url == expected
                with pytest.raises(TypeError):
                    resolved._ollama_endpoints["new"] = object()
            assert not s.counts
    assert visited == list(ids)


def test_ollama_v2_immutable_selection(monkeypatch, tmp_path, subtests):
    ids = tuple(f"I{i:02}" for i in range(5))
    visited = []
    for case in ids:
        visited.append(case)
        with subtests.test(case=case), _case(case, tmp_path, monkeypatch) as s:
            os.environ["OLLAMA_BASE_URL"] = A
            resolved = _resolved(s, prepare=case != "I04")
            mapping = resolved._ollama_endpoints
            os.environ["OLLAMA_BASE_URL"] = B
            if case == "I00":
                assert _endpoint(resolved).selected_base_url == A
            elif case in {"I01", "I02"}:
                _no_env(s)
                if case == "I01":
                    receipts = runner._model_readiness(resolved)
                    assert len(receipts) == 1
                assert _endpoint(resolved).selected_base_url == A
            elif case == "I03":
                fresh = _resolved(s)
                assert _endpoint(fresh).selected_base_url == B
                assert fresh._ollama_endpoints is not mapping
            else:
                _no_env(s)
                with pytest.raises(runner.V2CampaignRunError):
                    _endpoint(resolved)
            assert resolved._ollama_endpoints is mapping
            assert not s.counts
    assert visited == list(ids)


def test_ollama_v2_mutation_boundary(monkeypatch, tmp_path, subtests):
    ids = tuple(f"C{i:02}" for i in range(11))
    visited = []
    for case in ids:
        visited.append(case)
        with subtests.test(case=case), _case(case, tmp_path, monkeypatch) as s:
            models = [dict(OTHER)] if case == "C05" else [dict(MODEL), dict(OTHER)]
            if case == "C09":
                models[0]["model_base_url"] = A
            resolved = _resolved(s, models=models, defaults={"model_base_url": A})
            mapping = resolved._ollama_endpoints
            original = resolved.config.models[0]
            updated = list(resolved.config.models)
            fields = {
                "C00": {"name": "renamed"},
                "C01": {"model": "other-model"},
                "C02": {"api_surface": "responses"},
                "C03": {"structured_output_mode": "json_schema"},
                "C07": {"provider": "openai"},
            }
            if case in fields:
                updated[0] = original.model_copy(update=fields[case])
            elif case == "C05":
                updated.append(type(original).model_validate(MODEL))
            elif case == "C06":
                updated = updated[1:]
            elif case == "C08":
                updated.reverse()
            elif case == "C10":
                updated[1] = updated[1].model_copy(update={"model": "changed-other"})
            changes = {"models": updated}
            if case in {"C04", "C09"}:
                changes["defaults"] = resolved.config.defaults.model_copy(
                    update={"model_base_url": B}
                )
            resolved = resolved.model_copy(
                update={"config": resolved.config.model_copy(update=changes)}
            )
            _no_env(s)
            if case in {"C08", "C09", "C10"}:
                runner._prepare_ollama_endpoints(resolved)
                model = next(m for m in updated if m.provider == "ollama")
                assert runner._ollama_endpoint(model, resolved).selected_base_url == A
            else:
                if case == "C05":
                    with pytest.raises(runner.V2CampaignRunError, match="configuration changed"):
                        runner._prepare_ollama_endpoints(resolved)
                with pytest.raises(runner.V2CampaignRunError) as error:
                    runner._ollama_endpoint(original, resolved)
                if case in {"C02", "C03"}:
                    assert str(error.value) == (
                        "Prepared Ollama configuration changed; reload configuration"
                    )
                assert A not in str(error.value) and B not in str(error.value)
            assert resolved._ollama_endpoints is mapping
            assert not s.counts
    assert visited == list(ids)


def test_ollama_v2_dispatch_binding(monkeypatch, tmp_path, subtests):
    from ori.eval.bhce import CypherResult
    from ori.eval.v2 import model_runtime
    from ori.eval.v2.mcp import MCPToolLoop
    from tests.support.v2_direct import ORACLE, TASK, FakeCoordinator, _raw_route
    from tests.support.v2_mcp import ORACLE as MCP_ORACLE
    from tests.support.v2_mcp import PROFILE, _answer
    from tests.support.v2_mcp import TASK as MCP_TASK

    ids = ("D00", "D01")
    visited = []
    for case in ids:
        visited.append(case)
        with subtests.test(case=case), _case(case, tmp_path, monkeypatch) as s:
            os.environ["OLLAMA_BASE_URL"] = A
            resolved = _resolved(
                s,
                models=[{**MODEL, "options": {"num_predict": 37, "temperature": 0}}],
                defaults={
                    "mcp": {"mcp_dir": str(s.root), "tool_loop": "native-ollama", "max_steps": 1}
                },
            )
            model = resolved.config.models[0]
            bound = _endpoint(resolved)
            endpoint_hash = runner._provider_endpoint_fingerprint(model, resolved)
            provenance = _provenance(s, resolved)
            os.environ["OLLAMA_BASE_URL"] = B
            track = Track.DIRECT if case == "D00" else Track.MCP
            task, oracle = (TASK, ORACLE) if case == "D00" else (MCP_TASK, MCP_ORACLE)
            if case == "D01":
                task = task.model_copy(
                    update={
                        "binding": task.binding.model_copy(
                            update={"mcp_tool_loop": "native-ollama"}
                        )
                    }
                )
            query = "MATCH p=(a)-[:MemberOf]->(b) RETURN p LIMIT 1"
            s.text = (
                json.dumps({"query": query, "assertion": {}})
                if case == "D00"
                else json.dumps(_answer())
            )
            coordinator = FakeCoordinator(
                CypherResult(
                    success=True,
                    raw=_raw_route(),
                    status_code=200,
                    query_executed=True,
                    execution_attempts=1,
                    query_fingerprint="b" * 64,
                    safety_policy_version="3",
                    safety_rule="allowed",
                    bhce_health_after="not_checked",
                    circuit_state="closed",
                )
            )
            coordinator.circuit_open = False
            captured = []
            actual = (
                model_runtime.run_direct_model_task_v2
                if case == "D00"
                else model_runtime.run_mcp_model_task_v2
            )

            async def observed(**kwargs):
                captured.append(kwargs["model_base_url"])
                return await actual(**kwargs)

            async def bundle(*args, **kwargs):
                return SimpleNamespace(
                    tools=[], available_prompt_names=[], prompt_discovery_status="synthetic"
                )

            s.patch.setattr(runner, "_load_bloodhound_mcp_bundle", bundle)
            s.patch.setattr(
                runner,
                "run_direct_model_task_v2" if case == "D00" else "run_mcp_model_task_v2",
                observed,
            )
            # Only persistence/graph/server discovery are synthetic. Dispatch,
            # provider runtime, URL constructor and HTTP request remain real.
            s.patch.setattr(runner, "_provenance", lambda **kwargs: provenance)
            s.patch.setattr(runner, "_guard_run_dir", lambda *args: None)
            s.patch.setattr(runner, "_load_state", lambda *args, **kwargs: None)
            s.patch.setattr(
                runner,
                "build_checkpoint",
                lambda *args, **kwargs: SimpleNamespace(results=tuple(kwargs["results"])),
            )
            s.patch.setattr(runner, "_state", lambda **kwargs: kwargs)
            persisted = []
            s.patch.setattr(runner, "_write_model", lambda path, state: persisted.append(state))
            prepared = runner.PreparedTrack(
                track=track,
                pair=SimpleNamespace(
                    public=SimpleNamespace(tasks=(task,)),
                    private=SimpleNamespace(
                        oracles=(oracle,),
                        identity_catalog=oracle.resolved_roles,
                        graph_fact_registry=(),
                    ),
                ),
                profile=PROFILE,
                release=SimpleNamespace(entries=(SimpleNamespace(task_id=task.task_id),)),
                live=SimpleNamespace(),
                certifications={},
            )
            _, results = asyncio.run(
                runner._run_model(
                    resolved=resolved,
                    prepared=prepared,
                    model=model,
                    run_index=1,
                    bhce=SimpleNamespace(),
                    coordinator=coordinator,
                    loop=None if case == "D00" else MCPToolLoop.NATIVE_OLLAMA,
                    runs_total=1,
                )
            )
            assert len(s.requests) == 1 and persisted and len(results) == 1, [
                attempt.provider.provider_error
                for state in persisted
                if isinstance(state, dict)
                for attempt in state.get("attempts", ())
            ]
            assert captured == [bound.selected_base_url]
            assert str(s.requests[0].url) == bound.chat_url
            assert json.loads(s.requests[0].content)["options"] == {
                "num_predict": 37,
                "temperature": 0,
            }
            assert endpoint_hash == canonical_sha256({"endpoint": bound.chat_url})
            assert s.counts == {"constructor": 1, "request": 1, "close": 1}
    assert visited == list(ids)


def test_ollama_provenance_compatibility(monkeypatch, tmp_path, subtests):
    ids = tuple(f"F{i:02}" for i in range(7))
    visited = []
    for case in ids:
        visited.append(case)
        with subtests.test(case=case), _case(case, tmp_path, monkeypatch) as s:
            os.environ["OLLAMA_BASE_URL"] = A
            first = _resolved(s)
            if case in {"F00", "F01"}:
                second = _resolved(
                    s, defaults={"model_base_url": A + ("/v1" if case == "F00" else "/api")}
                )
                left = runner._provider_endpoint_fingerprint(first.config.models[0], first)
                right = runner._provider_endpoint_fingerprint(second.config.models[0], second)
                assert (left == right) is (case == "F00")
            elif case in {"F02", "F06"}:
                p1 = _provenance(s, first)
                os.environ["OLLAMA_BASE_URL"] = B
                second = _resolved(s)
                p2 = _provenance(s, second)
                assert p1.runtime_config_fingerprint != p2.runtime_config_fingerprint
                assert p1 != p2
                if case == "F06":
                    path = s.root / "resume"
                    runner._guard_run_dir(path, p1)
                    before = (path / "campaign-provenance-v2.json").read_bytes()
                    with pytest.raises(runner.V2CampaignRunError, match="incompatible"):
                        runner._guard_run_dir(path, p2)
                    assert (path / "campaign-provenance-v2.json").read_bytes() == before
            elif case == "F03":
                assert Path(_binding().__file__) in runner._RUNNER_IMPLEMENTATION_SOURCES.values()
            elif case == "F04":
                serialized = first.model_dump_json()
                assert "_ollama_endpoints" not in serialized
                assert "_ollama_mutation_fingerprint" not in serialized
                assert A not in serialized
            else:
                from ori.eval.v2.model_card import build_model_card
                from tests.support.v2_model_card import _MODEL, _campaign

                root = _campaign(s.root / "fixture")
                private = root / ".ori-private"
                private.mkdir()
                (private / "endpoint.private.json").write_text(
                    json.dumps(
                        {
                            "selected": _endpoint(first).selected_base_url,
                            "chat_url": _endpoint(first).chat_url,
                            "provenance": _provenance(s, first).model_dump(mode="json"),
                        }
                    )
                )
                output = s.root / "public"
                card = build_model_card(root, output, model=_MODEL)
                serialized = json.dumps(card) + "".join(p.read_text() for p in output.iterdir())
                assert len(tuple(output.iterdir())) == 2
                for forbidden in (
                    A,
                    "/private-route",
                    str(root),
                    ".ori-private",
                    "endpoint.private.json",
                ):
                    assert forbidden not in serialized
                assert not (output / ".ori-private").exists()
            assert not s.counts
    assert visited == list(ids)


def test_ollama_failure_and_legacy_boundary(monkeypatch, tmp_path, subtests):
    ids = tuple(f"B{i:02}" for i in range(5))
    visited = []
    for case in ids:
        visited.append(case)
        with subtests.test(case=case), _case(case, tmp_path, monkeypatch) as s:
            if case == "B00":
                response = _direct(model="ollama/model@" + A)
                assert response.provider_metrics["infra_error_subtype"] == "PROVIDER_CAPABILITY"
                assert A not in response.error
            elif case == "B01":
                with pytest.raises(ProviderCapabilityError):
                    _native(model="ollama/model@" + A)
            elif case == "B02":
                os.environ["OLLAMA_BASE_URL"] = ""
                response = _direct()
                assert response.provider_metrics["infra_error_subtype"] == "PROVIDER_CAPABILITY"
                with pytest.raises(ProviderCapabilityError):
                    _native()
            elif case == "B03":
                from ori.eval.tasks import Task

                s.text = "MATCH (n) RETURN n LIMIT 1"
                task = Task(
                    "synthetic", "synthetic", 1, "enumeration", "question", s.text, "node_set"
                )
                response = asyncio.run(
                    adapter.call_model(
                        task, "ollama/namespace/model:tag", base_url=A, ollama_options=dict(OPTIONS)
                    )
                )
                assert response.error is None and response.raw_text == s.text
                assert response.cypher == s.text
                assert (response.tokens_input, response.tokens_output) == (11, 7)
                assert json.loads(s.requests[0].content)["options"] == OPTIONS
            else:
                s.status = 500
                with pytest.raises(httpx.HTTPStatusError) as caught:
                    _native(base_url=A)
                assert caught.value.response.status_code == 500
            expected = (
                {}
                if case in {"B00", "B01", "B02"}
                else {"constructor": 1, "request": 1, "close": 1}
            )
            assert s.counts == expected
    assert visited == list(ids)


def test_ollama_pure_identity_and_compatibility(monkeypatch, tmp_path, subtests):
    ids = tuple(f"X{i:02}" for i in range(4))
    visited = []
    for case in ids:
        visited.append(case)
        with subtests.test(case=case), _case(case, tmp_path, monkeypatch) as s:
            if case in {"X00", "X01"}:
                resolved = _resolved(s)
                if case == "X00":
                    _no_env(s)
                    identity = runner._provider_identity(resolved.config.models[0], resolved)
                    assert identity.endpoint_family == "ollama"
                    assert identity.credential_source is None
                else:
                    (receipt,) = runner._model_readiness(resolved)
                    assert receipt.credential_check == "provider-does-not-require-a-key"
                    assert receipt.capability_check == "configured-not-probed"
            elif case == "X02":
                from ori.eval.inspect_runtime import _resolve_model_base_url

                assert _resolve_model_base_url("ollama/model", A + "/v1") == A + "/v1"
                assert _resolve_model_base_url("ollama/model", A + "/api") == A + "/api/v1"
            else:
                from ori.telemetry import normalize_ollama_base_url

                assert normalize_ollama_base_url(A + "/api") == A
                assert normalize_ollama_base_url(A + "/api/v1/") == A
            assert not s.counts
    assert visited == list(ids)
