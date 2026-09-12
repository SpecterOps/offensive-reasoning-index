"""Offline Codex destination binding across real consumers and readiness."""

import asyncio
import json
import os
import socket
import subprocess
from pathlib import Path
from types import SimpleNamespace

import openai
import pytest

from ori.eval import adapter, codex_oauth, mcp_runtime
from ori.eval.provider_contract import ProviderAuthenticationError, ProviderCapabilityError
from ori.eval.v2 import campaign_runner as cr
from ori.eval.v2.campaign_config import ResolvedV2CampaignConfig, V2CampaignConfig
from ori.eval.v2.fingerprint import canonical_sha256
from ori.eval.v2.profiles import capability_profile_for_track
from ori.eval.v2.protocol import V2ArtifactPair, build_artifacts
from ori.eval.v2.schema import Track
from tests.support.v2_compiler import simple_compiled  # noqa: F401

OFFICIAL = "https://chatgpt.com/backend-api/codex"
CUSTOM = "https://proxy.invalid/codex"


def _deny(*args, **kwargs):
    raise AssertionError("unexpected credential/client/external operation")


def _isolate(mp):
    for key in tuple(os.environ):
        mp.delenv(key)
    for name in ("connect", "connect_ex"):
        mp.setattr(socket.socket, name, _deny)
    mp.setattr(socket, "getaddrinfo", _deny)
    mp.setattr(socket, "create_connection", _deny)
    mp.setattr(subprocess, "Popen", _deny)
    mp.setattr(subprocess, "run", _deny)
    mp.setattr(asyncio, "create_subprocess_exec", _deny)
    mp.setattr(asyncio, "create_subprocess_shell", _deny)
    mp.setattr(openai, "AsyncOpenAI", _deny)
    mp.setattr(codex_oauth, "codex_auth_path", _deny)
    mp.setattr(codex_oauth, "codex_user_agent", lambda: "synthetic-agent")
    mp.setattr(codex_oauth, "_installation_id", lambda: "synthetic-install")
    mp.setattr(codex_oauth, "_session_id", lambda: "synthetic-session")


def _resolved(tmp_path, models=None, default_url=None, effort=None):
    config = V2CampaignConfig.model_validate(
        {
            "version": 2,
            "source": {"manifest": "manifest.json", "archive": "archive.zip"},
            "tracks": {
                "direct": {
                    "public": "public.json",
                    "oracles": "oracles.json",
                    "candidates": "candidates.json",
                    "live_certification": "live.json",
                }
            },
            "modes": ["direct"],
            "output_dir": "results",
            "defaults": {
                "model_base_url": default_url,
                "reasoning_effort": effort,
                "bhce_url": "https://graph.invalid",
                "mcp": {"mcp_dir": "."},
            },
            "models": models or [_model()],
        }
    )
    manifest, archive = tmp_path / "manifest.json", tmp_path / "archive.zip"
    manifest.touch()
    archive.touch()
    return ResolvedV2CampaignConfig(
        source_manifest=manifest,
        archive=archive,
        tracks={},
        output_dir=tmp_path / "results",
        mcp_dir=tmp_path,
        config=config,
        source_config_fingerprint="f" * 64,
    )


def _model(name="gpt-test", url=None, slug=None):
    return {
        "name": name,
        "provider": "codex",
        "model": slug if slug is not None else name,
        "model_base_url": url,
    }


def _auth(mp, path, content='{"tokens":{"access_token":"synthetic-file-token"}}'):
    if content is not None:
        path.write_text(content)
    mp.setenv("CODEX_AUTH_FILE", str(path))
    mp.setattr(codex_oauth, "codex_auth_path", lambda: path)


def _sdk(mp):
    calls = {"constructor": [], "create": [], "close": 0}

    async def create(**kwargs):
        calls["create"].append(kwargs)

        async def stream():
            yield SimpleNamespace(
                type="response.output_text.delta",
                delta="answer",
                item_id="message-1",
                output_index=0,
                content_index=0,
            )
            yield SimpleNamespace(
                type="response.completed",
                response=SimpleNamespace(
                    id="synthetic-response",
                    status="completed",
                    output=[
                        SimpleNamespace(
                            type="message",
                            id="message-1",
                            status="completed",
                            content=[SimpleNamespace(type="output_text", text="answer")],
                        )
                    ],
                    usage=SimpleNamespace(input_tokens=3, output_tokens=2, total_tokens=5),
                ),
            )

        return stream()

    async def close():
        calls["close"] += 1

    def construct(**kwargs):
        calls["constructor"].append(kwargs)
        return SimpleNamespace(responses=SimpleNamespace(create=create), close=close)

    mp.setattr(openai, "AsyncOpenAI", construct)
    return calls


def _consume(consumer, url, model="codex/gpt-test"):
    if consumer == "direct":
        result = asyncio.run(
            adapter.call_provider_text(
                model=model,
                base_url=url,
                system="system",
                messages=[{"role": "user", "content": "question"}],
            )
        )
        return result.error, result.provider_metrics
    result = asyncio.run(
        mcp_runtime._openai_compat_chat_turn(
            url=mcp_runtime._openai_compat_chat_url(url, model),
            model_name=model,
            messages=[
                {"role": "system", "content": "system"},
                {"role": "user", "content": "question"},
            ],
            tools=[],
        )
    )
    return None, result["provider_metrics"]


def _assert_request(calls, url, token):
    assert len(calls["constructor"]) == len(calls["create"]) == calls["close"] == 1
    assert calls["constructor"][0]["base_url"] == url
    assert calls["constructor"][0]["api_key"] == token
    request = calls["create"][0]
    assert request["extra_headers"]["Authorization"] == f"Bearer {token}"
    assert request["model"] == "gpt-test"
    assert request["instructions"] == "system"
    assert request["input"] == [{"role": "user", "content": "question"}]


def test_codex_direct_and_native_binding_parity(tmp_path, monkeypatch, subtests):
    cases = (
        (OFFICIAL, "CODEX_API_KEY", "synthetic-key", "codex_oauth"),
        (OFFICIAL, None, "synthetic-file-token", "codex_oauth"),
        (OFFICIAL, "OPENAI_API_KEY", "synthetic-file-token", "codex_oauth"),
        (CUSTOM, "CODEX_COMPAT_API_KEY", "synthetic-key", "codex_compat"),
        ("http://gpu.invalid:8080/codex", "CODEX_COMPAT_API_KEY", "synthetic-key", "codex_compat"),
        ("http://[::1]:8080/codex", "CODEX_COMPAT_API_KEY", "synthetic-key", "codex_compat"),
        (CUSTOM, "unrelated", None, None),
        ("http://chatgpt.com/backend-api/codex", "CODEX_API_KEY", None, None),
        ("https://user:pass@proxy.invalid/codex", "CODEX_COMPAT_API_KEY", None, None),
        ("/", "CODEX_API_KEY", None, None),
        ("///", "CODEX_API_KEY", None, None),
    )
    visited = []
    for index, (url, key, token, family) in enumerate(cases):
        for consumer in ("direct", "native"):
            case = f"B{index}/{consumer}"
            visited.append(case)
            with subtests.test(msg=case), monkeypatch.context() as mp:
                _isolate(mp)
                if key == "unrelated":
                    for name in ("CODEX_API_KEY", "OPENAI_API_KEY", "OPENAI_COMPAT_API_KEY"):
                        mp.setenv(name, "unrelated-sentinel")
                elif key:
                    mp.setenv(key, "synthetic-key")
                if token == "synthetic-file-token":
                    _auth(mp, tmp_path / f"B{index}-{consumer}.json")
                calls = _sdk(mp)
                if token is None and consumer == "native":
                    expected_error = (
                        ProviderAuthenticationError if index == 6 else ProviderCapabilityError
                    )
                    with pytest.raises(expected_error):
                        _consume(consumer, url)
                else:
                    error, metrics = _consume(consumer, url)
                    if token is None:
                        assert error
                        assert metrics["infra_retryable"] is False
                        assert metrics["infra_error_subtype"] == (
                            "PROVIDER_AUTH" if index == 6 else "PROVIDER_CAPABILITY"
                        )
                    else:
                        assert error is None
                        _assert_request(calls, url, token)
                        assert metrics["endpoint_family"] == family
                        assert metrics["credential_source"] == (
                            "codex-auth-file" if token == "synthetic-file-token" else key
                        )
                if token is None:
                    assert calls == {"constructor": [], "create": [], "close": 0}
    assert visited == [f"B{i}/{c}" for i in range(11) for c in ("direct", "native")]


def test_codex_configured_destination_precedence(tmp_path, monkeypatch, subtests):
    visited = []
    for index in range(5):
        for consumer in ("direct", "native"):
            case = f"P{index}/{consumer}"
            visited.append(case)
            with subtests.test(msg=case), monkeypatch.context() as mp:
                _isolate(mp)
                mp.setenv("CODEX_API_KEY", "official-key")
                mp.setenv("CODEX_COMPAT_API_KEY", "custom-key")
                env = "https://environment-precedence.invalid/codex"
                if index < 4:
                    mp.setenv("CODEX_BASE_URL", env)
                urls = [
                    "https://model.invalid/codex",
                    "https://defaults.invalid/codex",
                    "https://inline.invalid/codex",
                    env,
                    OFFICIAL,
                ]
                model = _model(
                    slug="gpt-test@https://inline.invalid/codex" if index < 3 else "gpt-test",
                    url=urls[0] if index == 0 else None,
                )
                resolved = _resolved(tmp_path, [model], urls[1] if index < 2 else None)
                entry = resolved.config.models[0]
                endpoint = cr._model_base_url(entry, resolved)
                identity = cr._provider_identity(entry, resolved)
                calls = _sdk(mp)
                error, metrics = _consume(consumer, endpoint, f"codex/{entry.model}")
                assert error is None
                _assert_request(calls, urls[index], "official-key" if index == 4 else "custom-key")
                assert endpoint == calls["constructor"][0]["base_url"]
                assert cr._provider_endpoint_fingerprint(entry, resolved) == canonical_sha256(
                    {"endpoint": urls[index]}
                )
                assert (
                    identity.credential_source
                    == metrics["credential_source"]
                    == ("CODEX_API_KEY" if index == 4 else "CODEX_COMPAT_API_KEY")
                )
    assert visited == [f"P{i}/{c}" for i in range(5) for c in ("direct", "native")]


def _local_readiness(mp, tmp_path, basenames=("gpt-test",)):
    counters = {"file": 0, "login": 0, "cache": 0}
    original_loader = codex_oauth._codex_file_token
    original_read = Path.read_text
    cache = tmp_path / ".codex" / "models_cache.json"
    cache.parent.mkdir(exist_ok=True)
    cache.write_text(
        json.dumps(
            {
                "models": [
                    {"slug": name, "supported_reasoning_levels": [{"effort": "high"}]}
                    for name in basenames
                ]
            }
        )
    )

    def load():
        counters["file"] += 1
        return original_loader()

    def read(path, *args, **kwargs):
        if path == cache:
            counters["cache"] += 1
        return original_read(path, *args, **kwargs)

    def login(args, **kwargs):
        assert args == ["codex", "login", "status"]
        counters["login"] += 1
        return SimpleNamespace(stdout="Logged in", stderr="")

    mp.setattr(codex_oauth, "_codex_file_token", load)
    mp.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    mp.setattr(Path, "read_text", read)
    mp.setattr(subprocess, "run", login)
    return counters


def _receipt(receipt, source, oauth=False):
    assert receipt.credential_source == source
    assert receipt.credential_check == ("codex-auth-file+codex-login-status" if oauth else source)
    assert receipt.capability_check == (
        "codex-model-cache+reasoning-effort" if oauth else "configured-not-probed"
    )
    assert receipt.reasoning_effort == "high"


def test_codex_readiness_source_branches(tmp_path, monkeypatch, subtests):
    visited = []
    for index in range(10):
        case = f"R{index}"
        visited.append(case)
        with subtests.test(msg=case), monkeypatch.context() as mp:
            _isolate(mp)
            counters = _local_readiness(mp, tmp_path)
            url = CUSTOM if index in (3, 4) else OFFICIAL
            if index == 5:
                url = "http://chatgpt.com/backend-api/codex"
            if index in (8, 9):
                url = "/" if index == 8 else "///"
            if index == 0:
                mp.setenv("CODEX_API_KEY", "synthetic-key")
            if index == 3:
                mp.setenv("CODEX_COMPAT_API_KEY", "synthetic-custom")
            if index in (2, 4):
                mp.setenv("OPENAI_API_KEY", "unrelated")
            if index == 4:
                mp.setenv("CODEX_API_KEY", "unrelated-codex")
            if index in (1, 2, 6, 7):
                _auth(
                    mp,
                    tmp_path / f"R{index}-auth.json",
                    None
                    if index == 6
                    else "{"
                    if index == 7
                    else '{"access_token":"synthetic-file-token"}',
                )
            resolved = _resolved(tmp_path, [_model(url=url)], effort="high")
            if index >= 4:
                with pytest.raises(cr.V2CampaignRunError):
                    cr._model_readiness(resolved)
                assert counters == {"file": int(index in (6, 7)), "login": 0, "cache": 0}
            else:
                (receipt,) = cr._model_readiness(resolved)
                oauth = index in (1, 2)
                _receipt(
                    receipt,
                    "codex-auth-file"
                    if oauth
                    else "CODEX_API_KEY"
                    if index == 0
                    else "CODEX_COMPAT_API_KEY",
                    oauth,
                )
                assert counters == {key: int(oauth) for key in counters}
    assert visited == [f"R{i}" for i in range(10)]


def test_codex_readiness_capability_scope(tmp_path, monkeypatch, subtests):
    visited = []
    for index in range(3):
        case = f"Q{index}"
        visited.append(case)
        with subtests.test(msg=case), monkeypatch.context() as mp:
            _isolate(mp)
            counters = _local_readiness(mp, tmp_path)
            if index == 2:
                mp.setenv("CODEX_COMPAT_API_KEY", "synthetic-custom")
            else:
                _auth(mp, tmp_path / f"Q{index}.json")
            resolved = _resolved(
                tmp_path,
                [
                    _model(
                        url=CUSTOM if index == 2 else OFFICIAL,
                        slug="remote-unknown" if index == 2 else "gpt-test",
                    )
                ],
                effort="xhigh" if index == 1 else "high",
            )
            if index == 1:
                with pytest.raises(cr.V2CampaignRunError, match="does not advertise"):
                    cr._model_readiness(resolved)
            else:
                (receipt,) = cr._model_readiness(resolved)
                _receipt(
                    receipt, "CODEX_COMPAT_API_KEY" if index == 2 else "codex-auth-file", index != 2
                )
            assert counters == {key: int(index != 2) for key in counters}
    assert visited == [f"Q{i}" for i in range(3)]


def test_codex_mixed_readiness_shares_only_login_and_cache(tmp_path, monkeypatch, subtests):
    visited = []
    for index, order in enumerate(
        (("custom", "oauth-a", "oauth-b"), ("oauth-a", "custom", "oauth-b"))
    ):
        visited.append(f"M{index}")
        with subtests.test(msg=f"M{index}"), monkeypatch.context() as mp:
            _isolate(mp)
            mp.setenv("CODEX_COMPAT_API_KEY", "synthetic-custom")
            _auth(mp, tmp_path / f"M{index}.json")
            counters = _local_readiness(mp, tmp_path, ("oauth-a", "oauth-b"))
            models = [
                _model(
                    name,
                    CUSTOM if name == "custom" else OFFICIAL,
                    "remote-unknown" if name == "custom" else name,
                )
                for name in order
            ]
            receipts = cr._model_readiness(_resolved(tmp_path, models, effort="high"))
            assert tuple(r.name for r in receipts) == order
            for receipt in receipts:
                oauth = receipt.name != "custom"
                _receipt(receipt, "codex-auth-file" if oauth else "CODEX_COMPAT_API_KEY", oauth)
            assert counters == {"file": 2, "login": 1, "cache": 1}
    assert visited == ["M0", "M1"]


def test_codex_v2_model_identity_is_explicit(tmp_path, monkeypatch, subtests):
    visited = []
    for index, slug in enumerate(
        ("gpt-test", "codex/gpt-test@https://proxy.invalid/codex", "codex/", "codex/   ")
    ):
        visited.append(f"I{index}")
        with subtests.test(msg=f"I{index}"), monkeypatch.context() as mp:
            _isolate(mp)
            mp.setattr(codex_oauth, "resolve_codex_credential", _deny)
            mp.setenv("CODEX_DEFAULT_MODEL", "must-not-enter-certified-identity")
            resolved = _resolved(tmp_path, [_model(slug=slug)])
            model = resolved.config.models[0]
            if index >= 2:
                with pytest.raises(cr.V2CampaignRunError):
                    cr._provider_identity(model, resolved)
            else:
                cr._provider_identity(model, resolved)
                assert cr._codex_model_slug(model) == "gpt-test"
    assert visited == [f"I{i}" for i in range(4)]


def test_codex_provenance_is_secret_free_and_endpoint_bound(
    tmp_path, monkeypatch, subtests, request
):
    _, snapshot, corpus, _ = request.getfixturevalue("simple_compiled")
    public, private = build_artifacts(corpus, identity_catalog=snapshot.entities)
    prepared = cr.PreparedTrack(
        track=Track.DIRECT,
        pair=V2ArtifactPair(public=public, private=private),
        profile=capability_profile_for_track(Track.DIRECT),
        release=SimpleNamespace(release_fingerprint="a" * 64),
        live=SimpleNamespace(artifact_fingerprint="b" * 64),
        certifications={},
        selected_task_ids=tuple(task.task_id for task in public.tasks),
    )
    visited = []
    for index in range(2):
        visited.append(f"V{index}")
        with subtests.test(msg=f"V{index}"), monkeypatch.context() as mp:
            _isolate(mp)
            mp.setattr(codex_oauth, "resolve_codex_credential", _deny)
            auth_path = tmp_path / "never-read-private-auth.json"
            auth_path.write_text('{"access_token":"rotation-a"}')
            mp.setenv("CODEX_AUTH_FILE", str(auth_path))
            mp.setenv("CODEX_COMPAT_API_KEY", "rotation-a")
            mp.setenv("CODEX_BASE_URL", OFFICIAL if index == 0 else CUSTOM)
            resolved = _resolved(tmp_path)

            def provenance():
                return cr._provenance(
                    resolved=resolved,
                    prepared=prepared,
                    model=resolved.config.models[0],
                    run_index=1,
                    loop=None,
                )

            first = provenance()
            assert first.credential_source == (
                "codex-auth-file" if index == 0 else "CODEX_COMPAT_API_KEY"
            )
            mp.setenv("CODEX_COMPAT_API_KEY", "rotation-b")
            auth_path.write_text('{"access_token":"rotation-b"}')
            assert provenance() == first
            mp.setenv("CODEX_BASE_URL", "https://second-proxy.invalid/codex")
            second = provenance()
            assert first.runtime_config_fingerprint != second.runtime_config_fingerprint
            serialized = first.model_dump_json() + second.model_dump_json()
            assert all(
                value not in serialized
                for value in (
                    "rotation-a",
                    "rotation-b",
                    str(tmp_path / "never-read-private-auth.json"),
                )
            )
    assert visited == ["V0", "V1"]
