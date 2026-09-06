"""Finite, offline Anthropic configuration and attempt-boundary acceptance cases."""

from __future__ import annotations

import asyncio
import builtins
import hashlib
import importlib
import io
import json
import os
import sys
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import anthropic
import pytest
from anthropic.lib.credentials import _providers
from pydantic import ValidationError

from ori.eval import adapter
from ori.eval.provider_contract import ProviderAuthenticationError, ProviderCapabilityError
from ori.eval.v2 import campaign_runner as runner
from ori.eval.v2.campaign_config import ResolvedV2CampaignConfig, V2CampaignConfig
from ori.eval.v2.schema import Track
from tests.support.anthropic_binding import (
    OFFICIAL,
    _binding_module,
    _denied,
    _synthetic_profile,
)
from tests.support.anthropic_binding import (
    _anthropic_case_context as _shared_anthropic_case_context,
)
from tests.support.v2_campaign import _base_provenance

KEY = "synthetic-native-key"
BEARER = "synthetic-native-bearer"
CUSTOM = "synthetic-dedicated-key"
CURRENT_CASE = None
URL_CASES = (
    ("O0", OFFICIAL),
    ("O1", "https://API.ANTHROPIC.COM:443/"),
    ("O2", "https://api.anthropic.com///"),
    ("C0", "https://proxy.invalid/custom"),
    ("C1", "http://127.0.0.1:8080/custom"),
    ("C2", "http://gpu.invalid:8080/custom"),
    ("C3", "http://[::1]:8080/custom"),
    ("C4", "https://proxy.invalid:8443/custom/path"),
    ("C5", "https://edge.api.anthropic.com/custom"),
    ("X0", "http://api.anthropic.com"),
    ("X1", "https://api.anthropic.com:8443"),
    ("X2", "https://api.anthropic.com/v1"),
    ("X3", "https://user:pass@proxy.invalid/custom"),
    ("X4", "https://proxy.invalid/custom?q=1"),
    ("X5", "https://proxy.invalid/custom#fragment"),
    ("X6", "https://proxy.invalid:65536/custom"),
    ("X7", "https://proxy.invalid:/custom"),
    ("X8", "https://proxy.invalid\\custom"),
    ("X9", "http://[::1/custom"),
    ("X10", "https:///custom"),
    ("X11", "ftp://proxy.invalid/custom"),
    ("X12", "\thttps://proxy.invalid/custom"),
    ("X13", "https://@proxy.invalid/custom"),
    ("X14", "https://proxy.invalid:bad/custom"),
    ("X15", "https://pro\nxy.invalid/custom"),
    ("X16", "\0https://proxy.invalid/custom"),
    ("X17", "https://proxy.invalid/cu\x7fstom"),
    ("X18", "/"),
    ("X19", "///"),
)


@contextmanager
def _anthropic_case_context(case, tmp_path, monkeypatch, environment=None):
    global CURRENT_CASE
    CURRENT_CASE = case
    with _shared_anthropic_case_context(case, tmp_path, monkeypatch, environment) as state:
        yield state
    CURRENT_CASE = None


def _synthetic_token_file(path, *, token="synthetic-token", expires=4102444800):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "access_token": token,
                "refresh_token": "synthetic-refresh",
                "expires_at": expires,
            }
        )
    )
    path.chmod(0o600)


def _wif_environment():
    return {
        "ANTHROPIC_IDENTITY_TOKEN": "synthetic-identity",
        "ANTHROPIC_FEDERATION_RULE_ID": "synthetic-rule",
        "ANTHROPIC_ORGANIZATION_ID": "synthetic-org",
    }


def _assert_attempt_counters(
    state, *, sdk=0, inference=0, http_close=0, provider_close=0, exchange=0
):
    assert state.counts["sdk_constructor"] == sdk
    assert state.counts["inference"] == inference
    assert state.counts["http_close"] == http_close
    assert state.counts["provider_close"] == provider_close
    assert state.counts["exchange"] == exchange


def _prepare(state, base_url=None, model="anthropic/synthetic-model"):
    binding = _binding_module().prepare_anthropic_binding(model, base_url)
    assert state.counts["token_read"] == state.counts["provider_call"] == 0
    assert state.counts["sdk_constructor"] == state.counts["async_constructor"] == 0
    assert state.counts["sync_constructor"] == state.counts["request"] == 0
    assert binding.model_slug == "synthetic-model"
    return binding


async def _call(state, binding=None, **overrides):
    state.forbid_tokens = False
    kwargs = {
        "model": "anthropic/synthetic-model",
        "system": "synthetic-system",
        "messages": [{"role": "user", "content": "synthetic-question"}],
        "max_tokens": state.max_tokens,
    }
    if binding is not None:
        kwargs["anthropic_binding"] = binding
    kwargs.update(overrides)
    return await adapter.call_provider_text(**kwargs)


def _assert_response(response):
    assert response.error is None
    assert response.raw_text == "synthetic-answer"
    assert response.tokens_input == 3 and response.tokens_output == 2


def _prepared_campaign(state, *, models=None, defaults=None, prepare=True):
    for name in ("manifest.json", "archive.zip"):
        (state.root / name).touch(exist_ok=True)
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
            "models": models
            or [{"name": "synthetic", "provider": "anthropic", "model": "synthetic-model"}],
        }
    )
    resolved = ResolvedV2CampaignConfig(
        source_manifest=state.root / "manifest.json",
        archive=state.root / "archive.zip",
        tracks={},
        output_dir=state.root / "campaign",
        config=config,
        source_config_fingerprint="a" * 64,
        mcp_dir=None,
    )
    if prepare:
        runner._prepare_anthropic_bindings(resolved)
    return resolved


def _provenance_fixture(state, resolved):
    state.patch.setattr(
        runner, "build_run_provenance", lambda *args: _base_provenance(Track.DIRECT)
    )
    prepared = SimpleNamespace(
        track=Track.DIRECT,
        pair=object(),
        profile=object(),
        release=SimpleNamespace(release_fingerprint="b" * 64),
        live=SimpleNamespace(artifact_fingerprint="c" * 64),
    )
    return runner._provenance(
        resolved=resolved,
        prepared=prepared,
        model=resolved.config.models[0],
        run_index=1,
        loop=None,
    )


def test_anthropic_native_selection(monkeypatch, tmp_path, subtests):
    ids = tuple(f"N{i:02}" for i in range(16))
    visited = []
    for case in ids:
        visited.append(case)
        with subtests.test(case=case), _anthropic_case_context(case, tmp_path, monkeypatch) as s:
            expected = None
            if case in {"N00", "N02", "N13"}:
                os.environ["ANTHROPIC_API_KEY"] = KEY
                expected = "ANTHROPIC_API_KEY"
            if case in {"N01", "N02"}:
                os.environ["ANTHROPIC_AUTH_TOKEN"] = BEARER
            if case == "N03":
                os.environ["ANTHROPIC_API_KEY"] = ""
            if case in {"N04", "N12"}:
                os.environ["ANTHROPIC_CUSTOM_HEADERS"] = "X-Api-Key: synthetic-header"
            if case in {"N05", "N14"}:
                os.environ["ANTHROPIC_CUSTOM_HEADERS"] = "Authorization: Bearer synthetic-header"
            if case == "N06":
                os.environ["ANTHROPIC_CUSTOM_HEADERS"] = "authorization: Bearer synthetic-lower"
            if case in {"N03", "N07", "N08", "N09", "N10", "N11", "N12", "N13", "N14", "N15"}:
                _path, token, _payload = _synthetic_profile(
                    s,
                    explicit=case not in {"N08", "N09", "N12"},
                    federation=case == "N15",
                    malformed=case in {"N11", "N12", "N13"},
                )
                _synthetic_token_file(token)
            if case in {"N09", "N10"}:
                os.environ.update(_wif_environment())
            if case in {"N03", "N06", "N11"}:
                with pytest.raises(ProviderAuthenticationError):
                    _prepare(s)
                _assert_attempt_counters(s)
                assert s.counts["token_read"] == 0
                if case == "N03":
                    assert s.counts["config_read"] == 0
                continue
            binding = _prepare(s)
            identity = _binding_module().anthropic_binding_identity(binding)
            assert identity["model_slug"] == "synthetic-model"
            assert identity["endpoint_family"] == "anthropic"
            if expected:
                assert expected in identity["credential_source"]
            for selected, name in (
                ("N07", "explicit-oauth"),
                ("N08", "fallback-oauth"),
                ("N09", "environment-federation"),
                ("N10", "explicit-oauth"),
                ("N15", "explicit-profile-federation"),
            ):
                if case == selected:
                    assert name == identity["credential_source"]
            if case in {"N04", "N05", "N12", "N14"}:
                assert identity["credential_source"] == "ANTHROPIC_CUSTOM_HEADERS"
            response = asyncio.run(_call(s, binding))
            _assert_response(response)
            kwargs = s.kwargs[0]
            assert kwargs["base_url"] == OFFICIAL
            assert kwargs["api_key"] == (
                KEY
                if case in {"N00", "N02", "N13"}
                else ""
                if case in {"N04", "N05", "N12"}
                else None
            )
            assert kwargs["auth_token"] == (BEARER if case in {"N01", "N02"} else None)
            assert (kwargs["credentials"] is not None) == (
                case in {"N07", "N08", "N09", "N10", "N14", "N15"}
            )
            if case == "N14":
                assert s.counts["provider_call"] == 0
            if case == "N13":
                assert s.counts["config_read"] == 0
    assert visited == list(ids)


def test_anthropic_inference_url_admission(monkeypatch, tmp_path, subtests):
    visited = []
    for case, url in URL_CASES:
        visited.append(case)
        with (
            subtests.test(case=case),
            _anthropic_case_context(
                case,
                tmp_path,
                monkeypatch,
                {
                    "ANTHROPIC_API_KEY": KEY,
                    "ANTHROPIC_COMPAT_API_KEY": CUSTOM,
                },
            ) as s,
        ):
            if case.startswith("X"):
                with pytest.raises(ProviderCapabilityError) as caught:
                    _prepare(s, url)
                assert url not in str(caught.value)
            else:
                binding = _prepare(s, url)
                assert binding.base_url == url.rstrip("/")
                assert binding.credential_source == (
                    "ANTHROPIC_API_KEY" if case.startswith("O") else "ANTHROPIC_COMPAT_API_KEY"
                )
            _assert_attempt_counters(s)
    assert visited == [case for case, _ in URL_CASES]


def test_anthropic_destination_precedence(monkeypatch, tmp_path, subtests):
    ids = ("P0", "P1", "P2", "P3", "P4", "P5")
    visited = []
    for index, case in enumerate(ids):
        visited.append(case)
        with subtests.test(case=case), _anthropic_case_context(case, tmp_path, monkeypatch) as s:
            urls = tuple(f"https://route-{n}.invalid/custom" for n in range(5))
            if index < 5:
                os.environ["ANTHROPIC_COMPAT_API_KEY"] = CUSTOM
                _synthetic_profile(s, base_url=urls[4])
                if index <= 3:
                    os.environ["ANTHROPIC_BASE_URL"] = urls[3]
            else:
                os.environ["ANTHROPIC_API_KEY"] = KEY
            model = {"name": "synthetic", "provider": "anthropic", "model": "synthetic-model"}
            if index <= 2:
                model["model"] += "@" + urls[2]
            if index == 0:
                model["model_base_url"] = urls[0]
            defaults = {"model_base_url": urls[1]} if index <= 1 else {}
            resolved = _prepared_campaign(s, models=[model], defaults=defaults)
            binding = runner._anthropic_binding(resolved.config.models[0], resolved)
            expected = urls[index] if index < 5 else OFFICIAL
            assert binding.base_url == expected
            response = asyncio.run(
                _call(s, binding, model="anthropic/" + model["model"], base_url=expected)
            )
            _assert_response(response)
            assert str(s.requests[-1].url) == expected + "/v1/messages"
            assert s.counts["provider_call"] == s.counts["token_read"] == 0
    assert visited == list(ids)


def test_anthropic_actual_request_headers(monkeypatch, tmp_path, subtests):
    ids = tuple(f"A{i}" for i in range(8))
    visited = []
    for case in ids:
        visited.append(case)
        with subtests.test(case=case), _anthropic_case_context(case, tmp_path, monkeypatch) as s:
            expected = {}
            url = OFFICIAL
            if case in {"A0", "A1"}:
                url = "https://proxy.invalid/custom"
                os.environ.update(ANTHROPIC_API_KEY=KEY, ANTHROPIC_AUTH_TOKEN=BEARER)
            if case == "A1":
                os.environ["ANTHROPIC_COMPAT_API_KEY"] = CUSTOM
                os.environ["ANTHROPIC_CUSTOM_HEADERS"] = (
                    "\n".join(
                        f"{name}: synthetic-inherited"
                        for family in (
                            ("Authorization", "authorization", "AUTHORIZATION"),
                            ("X-Api-Key", "x-api-key", "X-API-KEY"),
                            ("Proxy-Authorization", "proxy-authorization", "PROXY-AUTHORIZATION"),
                            ("Cookie", "cookie", "COOKIE"),
                        )
                        for name in family
                    )
                    + "\nX-Benign: synthetic-benign"
                )
                expected = {"x-api-key": [CUSTOM], "x-benign": ["synthetic-benign"]}
            if case in {"A2", "A4", "A5", "A7"}:
                os.environ["ANTHROPIC_API_KEY"] = KEY
                expected["x-api-key"] = [KEY]
            if case in {"A3", "A4", "A6"}:
                os.environ["ANTHROPIC_AUTH_TOKEN"] = BEARER
                expected["authorization"] = ["Bearer " + BEARER]
            if case == "A5":
                os.environ["ANTHROPIC_CUSTOM_HEADERS"] = "X-Api-Key: synthetic-override"
                expected["x-api-key"] = ["synthetic-override"]
            if case == "A6":
                os.environ["ANTHROPIC_CUSTOM_HEADERS"] = "Authorization: Bearer synthetic-override"
                expected["authorization"] = ["Bearer synthetic-override"]
            if case == "A7":
                os.environ["ANTHROPIC_CUSTOM_HEADERS"] = "authorization: Bearer synthetic-lower"
                expected["authorization"] = ["Bearer synthetic-lower"]
            if case == "A0":
                with pytest.raises(ProviderAuthenticationError):
                    binding = _prepare(s, url)
                    asyncio.run(_call(s, binding))
                _assert_attempt_counters(s)
                continue
            binding = _prepare(s, url)
            response = asyncio.run(_call(s, binding))
            _assert_response(response)
            headers = s.requests[-1].headers
            for name in ("x-api-key", "authorization", "proxy-authorization", "cookie", "x-benign"):
                assert [
                    value for key, value in headers.multi_items() if key == name
                ] == expected.get(name, [])
            if case in {"A5", "A6"}:
                assert "ANTHROPIC_CUSTOM_HEADERS" in binding.credential_source
            _assert_attempt_counters(s, sdk=1, inference=1, http_close=1)
    assert visited == list(ids)


def test_anthropic_native_token_compatibility(monkeypatch, tmp_path, subtests):
    ids = ("T0", "T1", "T2", "T3")
    visited = []
    for case in ids:
        visited.append(case)
        with subtests.test(case=case), _anthropic_case_context(case, tmp_path, monkeypatch) as s:
            if case == "T3":
                os.environ.update(_wif_environment())
                token = None
            else:
                _path, token, _ = _synthetic_profile(
                    s,
                    federation=case == "T2",
                    client_id="synthetic-client" if case == "T1" else None,
                )
                _synthetic_token_file(token, expires=1 if case == "T1" else 4102444800)
            binding = _prepare(s)
            _assert_response(asyncio.run(_call(s, binding)))
            if case in {"T0", "T2"}:
                _synthetic_token_file(
                    token, token="synthetic-rotated", expires=1 if case == "T2" else 4102444800
                )
            if case == "T3":
                os.environ["ANTHROPIC_IDENTITY_TOKEN"] = "synthetic-identity-rotated"
            if case != "T1":
                _assert_response(asyncio.run(_call(s, binding)))
            expected_exchanges = {"T0": 0, "T1": 1, "T2": 1, "T3": 2}[case]
            count = 1 if case == "T1" else 2
            _assert_attempt_counters(
                s,
                sdk=count,
                inference=count,
                http_close=count,
                provider_close=count,
                exchange=expected_exchanges,
            )
            inference = [r for r in s.requests if r.url.path.endswith("/messages")]
            expected_tokens = {
                "T0": ["synthetic-token", "synthetic-rotated"],
                "T1": ["synthetic-exchanged"],
                "T2": ["synthetic-token", "synthetic-exchanged"],
                "T3": ["synthetic-exchanged", "synthetic-exchanged"],
            }[case]
            assert [r.headers["authorization"] for r in inference] == [
                "Bearer " + t for t in expected_tokens
            ]
            if case == "T1":
                assert s.exchange_bodies == [
                    {
                        "grant_type": "refresh_token",
                        "refresh_token": "synthetic-refresh",
                        "client_id": "synthetic-client",
                    }
                ]
                assert json.loads(token.read_text())["refresh_token"] == "synthetic-refreshed"
            if case in {"T2", "T3"}:
                assertions = (
                    ["synthetic-identity"]
                    if case == "T2"
                    else ["synthetic-identity", "synthetic-identity-rotated"]
                )
                assert s.exchange_bodies == [
                    {
                        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                        "assertion": assertion,
                        "federation_rule_id": "synthetic-rule",
                        "organization_id": "synthetic-org",
                    }
                    for assertion in assertions
                ]
            if case == "T3":
                assert [b["assertion"] for b in s.exchange_bodies] == [
                    "synthetic-identity",
                    "synthetic-identity-rotated",
                ]
            if case == "T2":
                assert json.loads(token.read_text())["access_token"] == "synthetic-exchanged"
    assert visited == list(ids)


def test_anthropic_binding_identity_is_pure(monkeypatch, tmp_path, subtests):
    ids = ("I0", "I1")
    visited = []
    for case in ids:
        visited.append(case)
        with subtests.test(case=case), _anthropic_case_context(case, tmp_path, monkeypatch) as s:
            if case == "I0":
                os.environ["ANTHROPIC_API_KEY"] = KEY
            else:
                _synthetic_profile(s)
            binding = _prepare(s)
            expected = deepcopy(_binding_module().anthropic_binding_identity(binding))
            with monkeypatch.context() as trap:

                def denied(*args, **kwargs):
                    raise AssertionError("ANTHROPIC_IDENTITY_PURITY")

                class DeniedEnvironment(dict):
                    get = __getitem__ = __iter__ = __contains__ = denied

                trap.setattr(os, "environ", DeniedEnvironment())
                trap.setattr(os, "getenv", denied)
                trap.setattr(Path, "open", denied)
                trap.setattr(builtins, "open", denied)
                trap.setattr(_providers.CredentialsFile, "__init__", denied)
                assert _binding_module().anthropic_binding_identity(binding) == expected
                assert _binding_module().anthropic_binding_identity(binding) == expected
            assert KEY not in json.dumps(expected)
            assert "snapshot" not in expected
            _assert_attempt_counters(s)
    assert visited == list(ids)


def test_anthropic_readiness_is_configuration_only(monkeypatch, tmp_path, subtests):
    ids = ("R0", "R1", "R2", "R3")
    visited = []
    for case in ids:
        visited.append(case)
        with subtests.test(case=case), _anthropic_case_context(case, tmp_path, monkeypatch) as s:
            defaults = {}
            if case in {"R0", "R3"}:
                _synthetic_profile(s, malformed=case == "R3")
            elif case == "R1":
                os.environ["ANTHROPIC_API_KEY"] = KEY
            else:
                defaults["model_base_url"] = "https://proxy.invalid/custom"
            resolved = _prepared_campaign(s, defaults=defaults, prepare=False)
            if case in {"R2", "R3"}:
                with pytest.raises(runner.V2CampaignRunError) as caught:
                    runner._model_readiness(resolved)
                assert str(s.root) not in str(caught.value)
            else:
                receipts = runner._model_readiness(resolved)
                assert len(receipts) == 1
                assert receipts[0].capability_check == "configuration-validated-not-probed"
                assert len(receipts[0].anthropic_binding_fingerprint) == 64
            _assert_attempt_counters(s)
            assert s.counts["token_read"] == s.counts["provider_call"] == 0
    assert visited == list(ids)


def test_anthropic_direct_context_propagation(monkeypatch, tmp_path, subtests):
    from ori.eval.bhce import CypherResult
    from ori.eval.v2 import model_runtime
    from tests.support.v2_direct import ORACLE, TASK, FakeCoordinator, _raw_route

    ids = ("D0", "D1")
    visited = []
    for case in ids:
        visited.append(case)
        with (
            subtests.test(case=case),
            _anthropic_case_context(
                case,
                tmp_path,
                monkeypatch,
                {
                    "ANTHROPIC_API_KEY": KEY,
                    "ANTHROPIC_COMPAT_API_KEY": CUSTOM,
                },
            ) as s,
        ):
            defaults = {} if case == "D0" else {"model_base_url": "https://proxy.invalid/custom"}
            resolved = _prepared_campaign(
                s,
                defaults=defaults,
                models=[
                    {
                        "name": "synthetic",
                        "provider": "anthropic",
                        "model": "synthetic-model",
                        "max_output_tokens": 17,
                    }
                ],
            )
            model = resolved.config.models[0]
            binding = runner._anthropic_binding(model, resolved)
            query = "MATCH p=(a)-[:MemberOf]->(b) RETURN p LIMIT 1"
            s.text = json.dumps({"query": query, "assertion": {}})
            s.forbid_tokens = False
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
            seen = []
            original_materialize = adapter.materialize_anthropic_client

            async def materialize(actual):
                seen.append(actual)
                return await original_materialize(actual)

            s.patch.setattr(adapter, "materialize_anthropic_client", materialize)
            actual_direct = model_runtime.run_direct_model_task_v2
            captured = []

            async def observed_direct(**kwargs):
                assert kwargs["transport"].keywords["anthropic_binding"] is binding
                assert kwargs["model_base_url"] == binding.base_url
                assert kwargs["max_tokens"] == 17
                result = await actual_direct(**kwargs)
                captured.append(result)
                return result

            # Persistence is a synthetic sink; campaign dispatch, runtime, adapter,
            # projector and native SDK request remain the actual production path.
            provenance = _provenance_fixture(s, resolved)
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
            s.patch.setattr(runner, "run_direct_model_task_v2", observed_direct)
            coordinator.circuit_open = False
            prepared = runner.PreparedTrack(
                track=Track.DIRECT,
                pair=SimpleNamespace(
                    public=SimpleNamespace(tasks=(TASK,)),
                    private=SimpleNamespace(
                        oracles=(ORACLE,), identity_catalog=ORACLE.resolved_roles
                    ),
                ),
                profile=SimpleNamespace(),
                release=SimpleNamespace(entries=(SimpleNamespace(task_id=TASK.task_id),)),
                live=SimpleNamespace(),
                certifications={},
            )
            _, results = asyncio.run(
                runner._run_model(
                    resolved=resolved,
                    prepared=prepared,
                    model=model,
                    run_index=1,
                    bhce=SimpleNamespace(wait_until_healthy=_denied),
                    coordinator=coordinator,
                    loop=None,
                    runs_total=1,
                )
            )
            assert len(captured) == 1
            outcome, sample, record = captured[0]
            assert results == (sample,) and persisted
            assert seen == [binding] and seen[0] is binding
            assert outcome is not None and sample.reasoning_correct is True
            assert coordinator.queries == [query]
            assert record.tokens_input == 3 and record.tokens_output == 2
            assert str(s.requests[0].url) == binding.base_url + "/v1/messages"
            before = s.counts.copy()
            mismatch = (
                {"model": "anthropic/other-model"}
                if case == "D0"
                else {"base_url": "https://other.invalid"}
            )
            response = asyncio.run(_call(s, binding, **mismatch))
            assert response.error is not None
            assert response.provider_metrics["infra_error_subtype"] == "PROVIDER_CAPABILITY"
            assert s.counts == before
            inline_model = "anthropic/synthetic-model@https://conflicting.invalid"
            response = asyncio.run(_call(s, binding, model=inline_model))
            assert response.provider_metrics["infra_error_subtype"] == "PROVIDER_CAPABILITY"
            assert s.counts == before
            response = asyncio.run(_call(s, binding, model=inline_model, base_url=binding.base_url))
            assert response.error is None
            assert str(s.requests[-1].url) == binding.base_url + "/v1/messages"
    assert visited == list(ids)


def test_anthropic_provenance_binding(monkeypatch, tmp_path, subtests):
    ids = ("F0", "F1", "F2")
    visited = []
    for case in ids:
        visited.append(case)
        with subtests.test(case=case), _anthropic_case_context(case, tmp_path, monkeypatch) as s:
            if case == "F1":
                path, _token, payload = _synthetic_profile(s)
            else:
                os.environ.update(
                    ANTHROPIC_API_KEY=KEY,
                    ANTHROPIC_COMPAT_API_KEY=CUSTOM,
                    ANTHROPIC_CUSTOM_HEADERS="X-Benign: synthetic-one",
                )
                if case == "F2":
                    os.environ["ANTHROPIC_CUSTOM_HEADERS"] += (
                        "\nAuthorization: Bearer synthetic-auth-one"
                    )
            original = _prepared_campaign(s)
            first = _provenance_fixture(s, original)
            if case == "F0":
                os.environ["ANTHROPIC_BASE_URL"] = "https://different.invalid/custom"
            elif case == "F1":
                payload["organization_id"] = "synthetic-other-org"
                path.write_text(json.dumps(payload))
            else:
                os.environ["ANTHROPIC_API_KEY"] = "synthetic-rotated-key"
                os.environ["ANTHROPIC_CUSTOM_HEADERS"] = (
                    "X-Benign: synthetic-two\nAuthorization: Bearer synthetic-auth-two"
                )
            changed = _prepared_campaign(s)
            second = _provenance_fixture(s, changed)
            if case == "F2":
                assert first.runtime_config_fingerprint == second.runtime_config_fingerprint
                assert first.provenance_fingerprint == second.provenance_fingerprint
            else:
                assert first.runtime_config_fingerprint != second.runtime_config_fingerprint
                assert first.provenance_fingerprint != second.provenance_fingerprint
            if case == "F1":
                os.environ["ANTHROPIC_CUSTOM_HEADERS"] = "X-New-Name: synthetic-value"
                third = _provenance_fixture(s, _prepared_campaign(s))
                assert second.runtime_config_fingerprint != third.runtime_config_fingerprint
            assert "anthropic_binding" in runner._RUNNER_IMPLEMENTATION_SOURCES
            serialized = first.model_dump_json()
            for forbidden in (KEY, CUSTOM, str(s.root), "synthetic-one", "snapshot"):
                assert forbidden not in serialized
            _assert_attempt_counters(s)
    assert visited == list(ids)


def test_anthropic_snapshot_and_resume(monkeypatch, tmp_path, subtests):
    ids = ("S0", "S1")
    visited = []
    for case in ids:
        visited.append(case)
        with subtests.test(case=case), _anthropic_case_context(case, tmp_path, monkeypatch) as s:
            path, token, payload = _synthetic_profile(s)
            _synthetic_token_file(token)
            resolved = _prepared_campaign(s)
            binding = runner._anthropic_binding(resolved.config.models[0], resolved)
            first = _provenance_fixture(s, resolved)
            payload["organization_id"] = "synthetic-changed"
            if case == "S0":
                payload["base_url"] = "https://changed.invalid"
            path.write_text(json.dumps(payload))
            if case == "S0":
                s.forbid_config = True
                _synthetic_token_file(token, token="synthetic-rotated")
                _assert_response(asyncio.run(_call(s, binding)))
                assert str(s.requests[-1].url) == OFFICIAL + "/v1/messages"
                assert s.requests[-1].headers["authorization"] == "Bearer synthetic-rotated"
            else:
                run_dir = s.root / "existing-run"
                runner._guard_run_dir(run_dir, first)
                original_bytes = (run_dir / "campaign-provenance-v2.json").read_bytes()
                second = _provenance_fixture(s, _prepared_campaign(s))
                with pytest.raises(runner.V2CampaignRunError, match="incompatible"):
                    runner._guard_run_dir(run_dir, second)
                assert (run_dir / "campaign-provenance-v2.json").read_bytes() == original_bytes
    assert visited == list(ids)


def test_anthropic_binding_error_taxonomy(monkeypatch, tmp_path, subtests):
    ids = tuple(f"E{i}" for i in range(8))
    visited = []
    for case in ids:
        visited.append(case)
        with subtests.test(case=case), _anthropic_case_context(case, tmp_path, monkeypatch) as s:
            module = _binding_module()
            if case == "E0":
                os.environ["ANTHROPIC_PROFILE"] = "missing-synthetic"
            elif case == "E2":
                path, _, payload = _synthetic_profile(s)
                payload["authentication"]["type"] = "unsupported-synthetic"
                path.write_text(json.dumps(payload))
            else:
                os.environ["ANTHROPIC_API_KEY"] = KEY
            if case == "E1":
                s.patch.setattr(anthropic, "__version__", "unsupported-synthetic")
            elif case == "E3":

                def defect(*args, **kwargs):
                    raise TypeError("synthetic-internal-defect")

                s.patch.setattr(module, "_native_configuration", defect)
                os.environ.pop("ANTHROPIC_API_KEY")
            elif case == "E4":
                identity_token_class = _providers.IdentityTokenFile
                s.patch.delattr(_providers, "IdentityTokenFile")
            elif case == "E5":
                s.patch.setattr(module, "_SDK_HASHES", {"_client.py": "0" * 64})
            elif case in {"E6", "E7"}:
                original = Path.read_bytes

                def source_read(path):
                    if path == Path(anthropic.__file__).parent / "_client.py":
                        raise (FileNotFoundError if case == "E6" else PermissionError)(
                            "synthetic-private-source"
                        )
                    return original(path)

                s.patch.setattr(Path, "read_bytes", source_read)
            if case == "E3":
                with pytest.raises(adapter.ProviderAdapterInternalError) as caught:
                    asyncio.run(_call(s))
                assert isinstance(caught.value.__cause__, TypeError)
                assert str(caught.value.__cause__) == "synthetic-internal-defect"
            else:
                category = (
                    ProviderAuthenticationError if case in {"E0", "E2"} else ProviderCapabilityError
                )
                with pytest.raises(category) as caught:
                    module.prepare_anthropic_binding("anthropic/synthetic-model")
                assert str(s.root) not in str(caught.value)
                assert "synthetic-private-source" not in str(caught.value)
            if case in {"E4", "E6"}:
                if case == "E4":
                    s.patch.setattr(
                        _providers, "IdentityTokenFile", identity_token_class, raising=False
                    )
                else:
                    s.patch.setattr(Path, "read_bytes", original)
                module._check_sdk_compatibility.cache_clear()
                missing = "anthropic.lib.credentials._providers" if case == "E4" else "anthropic"
                with monkeypatch.context() as absent:
                    absent.setitem(sys.modules, missing, None)
                    if case == "E4":
                        package = importlib.import_module("anthropic.lib.credentials")
                        absent.delattr(package, "_providers")
                    with pytest.raises(ProviderCapabilityError, match="Unsupported Anthropic SDK"):
                        module.prepare_anthropic_binding("anthropic/synthetic-model")
            _assert_attempt_counters(s)
    assert visited == list(ids)


def test_anthropic_attempt_cleanup(monkeypatch, tmp_path, subtests):
    ids = tuple(f"L{i:02}" for i in range(10))
    visited = []
    for case in ids:
        visited.append(case)
        with subtests.test(case=case), _anthropic_case_context(case, tmp_path, monkeypatch) as s:
            _, token, _ = _synthetic_profile(s, federation=case == "L09")
            _synthetic_token_file(token)
            binding = _prepare(s)
            if case == "L01":
                s.constructor_fault = anthropic.AnthropicError("synthetic-constructor")
            if case in {"L02", "L05"}:
                s.request_fault = anthropic.AnthropicError("synthetic-request")
            if case in {"L03", "L05"}:
                s.http_close_fault = RuntimeError("synthetic-http-cleanup")
            if case in {"L04", "L05"}:
                s.provider_close_fault = RuntimeError("synthetic-provider-cleanup")
            if case == "L06":
                s.request_fault = asyncio.CancelledError("synthetic-request-cancel")
            if case == "L07":
                s.http_close_fault = asyncio.CancelledError("synthetic-http-cancel")
            if case == "L08":
                s.provider_close_fault = asyncio.CancelledError("synthetic-provider-cancel")
            if case in {"L06", "L07", "L08"}:
                with pytest.raises(asyncio.CancelledError):
                    asyncio.run(_call(s, binding))
            else:
                response = asyncio.run(_call(s, binding))
                if case in {"L01", "L02", "L05"}:
                    assert response.provider_metrics["infra_error_subtype"] == "PROVIDER_ERROR"
                    assert response.provider_metrics["infra_retryable"] is False
                    assert "cleanup" not in response.error
                else:
                    _assert_response(response)
            assert s.counts["http_close"] == 1
            assert s.counts["provider_close"] == 1
            assert s.counts["sdk_constructor"] == 1
            assert s.counts["request"] == (0 if case == "L01" else 1)
            if case == "L09":
                assert s.counts["workload_close"] == 0
                assert s.counts["borrowed_workload_close"] == 1
                assert s.counts["sync_close"] == 1
    assert visited == list(ids)


def test_anthropic_legacy_ephemeral_binding(monkeypatch, tmp_path, subtests):
    from ori.eval.tasks import Task

    ids = ("V0", "V1")
    visited = []
    for case in ids:
        visited.append(case)
        with (
            subtests.test(case=case),
            _anthropic_case_context(case, tmp_path, monkeypatch, {"ANTHROPIC_API_KEY": KEY}) as s,
        ):
            preparations = []
            original = adapter.prepare_anthropic_binding

            def prepare(*args, **kwargs):
                preparations.append((args, kwargs))
                return original(*args, **kwargs)

            s.patch.setattr(adapter, "prepare_anthropic_binding", prepare)
            task = Task(
                id="synthetic",
                template_id="synthetic",
                tier=1,
                category="enumeration",
                question="synthetic-question",
                reference_cypher="MATCH (n) RETURN n LIMIT 1",
                grade_mode="node_set",
            )

            async def legacy_call():
                s.forbid_tokens = False
                return await adapter.call_model(
                    task,
                    "anthropic/synthetic-model",
                    max_tokens=17,
                    ollama_options={"temperature": 0},
                )

            response = asyncio.run(_call(s) if case == "V0" else legacy_call())
            _assert_response(response)
            assert len(preparations) == 1
            request = json.loads(s.requests[0].content)
            if case == "V0":
                assert request["system"] == "synthetic-system"
            else:
                assert request["system"] == adapter._SYSTEM_PROMPT.format(domain="CORP.LOCAL")
                assert request["messages"] == [{"role": "user", "content": task.question}]
                assert request["max_tokens"] == 17
            fields = set(asdict(response))
            if case == "V1":
                s.request_fault = anthropic.AnthropicError("synthetic-request")
                error = asyncio.run(legacy_call())
                assert set(asdict(error)) == fields
                assert error.error is not None and error.raw_text == ""
                assert error.tokens_input == error.tokens_output == 0
                assert error.provider_metrics["infra_error_subtype"] == "PROVIDER_ERROR"
                assert error.provider_metrics["infra_retryable"] is False
                assert len(preparations) == 2
            assert s.counts["http_close"] == len(preparations)
    assert visited == list(ids)


def _guard_document(resolved):
    return resolved.output_dir / ".ori-private" / "anthropic-headers-v1.private.json"


def test_anthropic_private_header_resume_guard(monkeypatch, tmp_path, subtests):
    from ori.eval.v2 import campaign_status

    ids = tuple(f"G{i:02}" for i in range(23))
    visited = []
    for case in ids:
        visited.append(case)
        with (
            subtests.test(case=case),
            _anthropic_case_context(
                case,
                tmp_path,
                monkeypatch,
                {
                    "ANTHROPIC_API_KEY": KEY,
                    "ANTHROPIC_CUSTOM_HEADERS": "X-Benign: synthetic-guard-value",
                },
            ) as s,
        ):
            if case == "G10":
                os.environ["ANTHROPIC_CUSTOM_HEADERS"] += (
                    "\nAuthorization: Bearer synthetic-one\nX-Api-Key: synthetic-one"
                    "\nProxy-Authorization: synthetic-one\nCookie: synthetic-one"
                )
            if case == "G11":
                os.environ["ANTHROPIC_CUSTOM_HEADERS"] = "X-Benign:"
            models = (
                [{"name": "other", "provider": "ollama", "model": "synthetic-model"}]
                if case == "G20"
                else None
            )
            resolved = _prepared_campaign(s, models=models, prepare=case != "G21")
            guard = _guard_document(resolved)
            if case == "G21":
                os.environ.pop("ANTHROPIC_API_KEY")
                with pytest.raises(runner.V2CampaignRunError):
                    runner._model_readiness(resolved)
                assert not guard.exists()
                _assert_attempt_counters(s)
                continue
            if case == "G22":
                order = []
                s.patch.setattr(
                    runner, "prepare_v2_campaign", lambda path: (resolved, None, {}, "", ())
                )
                actual_guard = runner._guard_anthropic_headers

                def observed_guard(actual):
                    assert actual is resolved
                    with pytest.raises(runner.V2CampaignRunError, match="already locked"):
                        with runner._exclusive_output_dir_lock(resolved.output_dir):
                            pytest.fail("second lock admitted")
                    actual_guard(actual)
                    order.append("guard")

                class StopAfterGuard(Exception):
                    pass

                def start(**kwargs):
                    assert order == ["guard"] and guard.is_file()
                    order.append("lifecycle")
                    raise StopAfterGuard

                s.patch.setattr(runner, "_guard_anthropic_headers", observed_guard)
                s.patch.setattr(runner._CampaignLifecycleController, "start", start)
                s.patch.setattr(runner, "_run_prepared_v2_campaign", _denied)
                with pytest.raises(StopAfterGuard):
                    asyncio.run(runner.run_v2_campaign(s.root / "synthetic.yaml"))
                assert order == ["guard", "lifecycle"]
                _assert_attempt_counters(s)
                continue
            with runner._exclusive_output_dir_lock(resolved.output_dir):
                if case == "G02":
                    (resolved.output_dir / "existing.json").write_text("{}")
                elif case in {"G16", "G17"}:
                    if case == "G16":
                        target = s.root / "private-target"
                        target.mkdir(mode=0o700)
                        guard.parent.symlink_to(target, target_is_directory=True)
                    else:
                        guard.parent.mkdir(mode=0o755)
                        guard.parent.chmod(0o755)
                elif case == "G19":

                    def write_failure(source, destination):
                        assert Path(destination) == guard
                        assert Path(source).parent == guard.parent
                        assert Path(source).read_bytes()
                        raise OSError("synthetic-private-write")

                    s.patch.setattr(os, "replace", write_failure)
                elif case not in {"G00", "G20"}:
                    runner._guard_anthropic_headers(resolved)
                    payload = json.loads(guard.read_text())
                    entry = payload["models"]["synthetic"]
                    if case == "G03":
                        guard.write_text("{synthetic-malformed")
                    elif case == "G04":
                        payload["schema_version"] = 2
                    elif case == "G05":
                        payload["unknown"] = "synthetic-secret"
                    elif case == "G06":
                        entry["headers"].append(deepcopy(entry["headers"][0]))
                    elif case == "G07":
                        payload["models"] = {}
                    elif case == "G08":
                        entry["headers"] = "wrong-type"
                    elif case == "G09":
                        next(h for h in entry["headers"] if h["name"] == "X-Benign")["value"] = (
                            "synthetic-other-value"
                        )
                    elif case == "G10":
                        os.environ["ANTHROPIC_API_KEY"] = "synthetic-rotated-key"
                        os.environ["ANTHROPIC_CUSTOM_HEADERS"] = os.environ[
                            "ANTHROPIC_CUSTOM_HEADERS"
                        ].replace("synthetic-one", "synthetic-rotated")
                        resolved = _prepared_campaign(s)
                    elif case == "G11":
                        next(h for h in entry["headers"] if h["name"] == "X-Benign").pop("value")
                    elif case == "G12":
                        next(h for h in entry["headers"] if h["name"] == "X-Benign")["name"] = (
                            "x-benign"
                        )
                    elif case == "G13":
                        target = s.root / "guard-target.json"
                        guard.rename(target)
                        guard.symlink_to(target)
                    elif case == "G14":
                        guard.unlink()
                        guard.mkdir()
                    elif case == "G15":
                        guard.chmod(0o644)
                    if case in {"G04", "G05", "G06", "G07", "G08", "G09", "G11", "G12"}:
                        guard.write_text(json.dumps(payload))
                if case == "G18":
                    from tests.support.v2_campaign import _completed_campaign

                    completed_root = s.root / "completed-status"
                    completed_root.mkdir()
                    config_path, completed = _completed_campaign(completed_root)
                    private = completed.output_dir / ".ori-private"
                    private.mkdir(mode=0o700)
                    (private / guard.name).write_bytes(guard.read_bytes())
                    original_open = os.open
                    original_io_open = io.open
                    original_builtin_open = builtins.open

                    def guard_open(path, *args, **kwargs):
                        assert ".ori-private" not in str(path), "ANTHROPIC_STATUS_GUARD_READ"
                        return original_open(path, *args, **kwargs)

                    def guard_io_open(path, *args, **kwargs):
                        assert ".ori-private" not in str(path), "ANTHROPIC_STATUS_GUARD_READ"
                        return original_io_open(path, *args, **kwargs)

                    def guard_builtin_open(path, *args, **kwargs):
                        assert ".ori-private" not in str(path), "ANTHROPIC_STATUS_GUARD_READ"
                        return original_builtin_open(path, *args, **kwargs)

                    s.patch.setattr(os, "open", guard_open)
                    s.patch.setattr(io, "open", guard_io_open)
                    s.patch.setattr(builtins, "open", guard_builtin_open)
                    status = campaign_status.inspect_v2_campaign_status(config_path)
                    assert status.observed_state == "completed"
                    assert "synthetic-guard-value" not in status.model_dump_json()
                elif case in {"G00", "G01", "G10", "G20"}:
                    runner._guard_anthropic_headers(resolved)
                    if case == "G20":
                        assert not guard.parent.exists()
                    else:
                        assert guard.stat().st_mode & 0o777 == 0o600
                        assert guard.parent.stat().st_mode & 0o777 == 0o700
                        actual = json.loads(guard.read_text())
                        assert set(actual) == {
                            "schema_version",
                            "source_config_fingerprint",
                            "models",
                        }
                        assert actual["schema_version"] == 1
                        assert actual["models"]["synthetic"]["model_slug"] == "synthetic-model"
                        assert "synthetic-guard-value" in guard.read_text()
                        assert KEY not in guard.read_text()
                        assert "synthetic-rotated-key" not in guard.read_text()
                        if case == "G10":
                            auth = [
                                h
                                for h in actual["models"]["synthetic"]["headers"]
                                if h["name"].lower()
                                in {"authorization", "x-api-key", "proxy-authorization", "cookie"}
                            ]
                            assert len(auth) == 4
                            assert all("value" not in h for h in auth)
                        assert (
                            "synthetic-guard-value"
                            not in _provenance_fixture(s, resolved).model_dump_json()
                        )
                        if case == "G00":
                            from ori.eval.v2.model_card import build_model_card
                            from tests.support.v2_model_card import _MODEL, _campaign

                            public_fixture = _campaign(s.root / "model-card-fixture")
                            private = public_fixture / ".ori-private"
                            private.mkdir(mode=0o700)
                            (private / guard.name).write_bytes(guard.read_bytes())
                            output = s.root / "public-export"
                            card = build_model_card(public_fixture, output, model=_MODEL)
                            serialized = json.dumps(card) + "".join(
                                p.read_text() for p in output.iterdir()
                            )
                            assert len(tuple(output.iterdir())) == 2
                            assert not (output / ".ori-private").exists()
                            for forbidden in (
                                "synthetic-guard-value",
                                ".ori-private",
                                guard.name,
                                hashlib.sha256(b"synthetic-guard-value").hexdigest(),
                                hashlib.sha256(guard.read_bytes()).hexdigest(),
                            ):
                                assert forbidden not in serialized
                else:
                    before = (
                        guard.read_bytes() if guard.is_file() and not guard.is_symlink() else None
                    )
                    with pytest.raises(runner.V2CampaignRunError) as caught:
                        runner._guard_anthropic_headers(resolved)
                    assert str(s.root) not in str(caught.value)
                    assert "synthetic-" not in str(caught.value)
                    if before is not None:
                        assert guard.read_bytes() == before
                    if case == "G19":
                        assert not guard.exists()
                        assert list(guard.parent.iterdir()) == []
                assert not (resolved.output_dir / "campaign-lifecycle-v2.private.json").exists()
                assert not (resolved.output_dir / "v2-run-readiness.private.json").exists()
                _assert_attempt_counters(s)
    assert visited == list(ids)


def test_anthropic_binding_preparation_is_atomic(monkeypatch, tmp_path, subtests):
    ids = tuple(f"B{i:02}" for i in range(10))
    visited = []
    for case in ids:
        visited.append(case)
        with (
            subtests.test(case=case),
            _anthropic_case_context(case, tmp_path, monkeypatch, {"ANTHROPIC_API_KEY": KEY}) as s,
        ):
            models = [
                {"name": "first", "provider": "anthropic", "model": "synthetic-model"},
                {"name": "second", "provider": "anthropic", "model": "second-model"},
            ]
            if case == "B05":
                models[1].update(provider="openai", api_surface="responses")
            elif case == "B06":
                models[1]["model"] = "anthropic/"
            elif case == "B07":
                models[1].update(provider="codex", model="codex/   ")
            elif case == "B08":
                models = [{"name": "other", "provider": "ollama", "model": "synthetic-model"}]
            elif case == "B09":
                models[1].update(provider="openai")
            resolved = _prepared_campaign(s, models=models, prepare=False)
            calls = []
            original = runner.prepare_anthropic_binding
            fail = {"active": case in {"B01", "B02"}}

            def prepare(model, base_url=None):
                assert resolved._anthropic_bindings is None
                calls.append(model)
                if fail["active"] and (case == "B02" or "second-model" in model):
                    raise ProviderAuthenticationError("synthetic-admission")
                return original(model, base_url)

            s.patch.setattr(runner, "prepare_anthropic_binding", prepare)
            if case in {"B01", "B02", "B05", "B06", "B07"}:
                with pytest.raises(runner.V2CampaignRunError):
                    runner._prepare_anthropic_bindings(resolved)
                assert resolved._anthropic_bindings is None
                assert resolved._anthropic_mutation_fingerprint is None
                assert len(calls) == {"B01": 2, "B02": 1, "B05": 0, "B06": 0, "B07": 0}[case]
                if case == "B01":
                    fail["active"] = False
                    runner._prepare_anthropic_bindings(resolved)
                    assert calls == ["anthropic/synthetic-model", "anthropic/second-model"] * 2
                continue
            runner._prepare_anthropic_bindings(resolved)
            mapping = resolved._anthropic_bindings
            assert mapping is not None
            with pytest.raises(TypeError):
                mapping["illegal"] = object()
            if case == "B00":
                assert set(mapping) == {"first", "second"}
                assert len(calls) == 2
            elif case == "B03":
                s.patch.setattr(runner, "prepare_anthropic_binding", _denied)
                runner._model_readiness(resolved)
                runner._model_readiness(resolved)
                assert resolved._anthropic_bindings is mapping
            elif case == "B04":
                object.__setattr__(
                    resolved.config.defaults, "model_base_url", "https://mutated.invalid"
                )
                with pytest.raises(runner.V2CampaignRunError, match="changed"):
                    runner._prepare_anthropic_bindings(resolved)
                assert resolved._anthropic_bindings is mapping
                assert len(calls) == 2
            elif case == "B08":
                assert len(mapping) == 0 and calls == []
                object.__setattr__(resolved.config.models[0], "model", "changed-unrelated-model")
                runner._prepare_anthropic_bindings(resolved)
                assert resolved._anthropic_bindings is mapping
            elif case == "B09":
                with pytest.raises(runner.V2CampaignRunError):
                    runner._model_readiness(resolved)
                assert resolved._anthropic_bindings is mapping
                assert set(mapping) == {"first"}
                assert len(calls) == 1
            _assert_attempt_counters(s)
    assert visited == list(ids)


def test_anthropic_credential_route_admission(monkeypatch, tmp_path, subtests):
    ids = ("Q0", "Q1", "Q2", "Q3", "Q4", "Q5")
    routes = (
        None,
        "https://API.ANTHROPIC.COM:443/",
        "https://exchange.invalid",
        "http://api.anthropic.com",
        OFFICIAL + "/wrong",
        "https://api.anthropic.com:bad",
    )
    visited = []
    for case, route in zip(ids, routes, strict=True):
        visited.append(case)
        with subtests.test(case=case), _anthropic_case_context(case, tmp_path, monkeypatch) as s:
            _path, token, _ = _synthetic_profile(s, base_url=route, client_id="synthetic-client")
            _synthetic_token_file(token, expires=1)
            if case in {"Q0", "Q1"}:
                binding = _prepare(s, OFFICIAL)
                _assert_response(asyncio.run(_call(s, binding)))
                assert s.counts["exchange"] == 1
                assert all(r.url.host == "api.anthropic.com" for r in s.requests)
            else:
                with pytest.raises(ProviderCapabilityError) as caught:
                    _prepare(s, OFFICIAL)
                assert route not in str(caught.value)
                _assert_attempt_counters(s)
                assert s.counts["provider_call"] == s.counts["token_read"] == 0
    assert visited == list(ids)


def test_anthropic_readiness_binding_schema(monkeypatch, tmp_path, subtests):
    ids = ("J0", "J1", "J2", "J3", "J4", "J5", "J6")
    visited = []
    for case in ids:
        visited.append(case)
        with (
            subtests.test(case=case),
            _anthropic_case_context(case, tmp_path, monkeypatch, {"ANTHROPIC_API_KEY": KEY}) as s,
        ):
            resolved = _prepared_campaign(s)
            valid = runner._model_readiness(resolved)[0]
            payload = valid.model_dump(mode="json")
            if case == "J1":
                payload.pop("anthropic_binding_fingerprint")
            elif case == "J2":
                payload["anthropic_binding_fingerprint"] = "a" * 63
            elif case == "J3":
                payload["anthropic_binding_fingerprint"] = "Z" * 64
            elif case in {"J4", "J5"}:
                payload["provider"] = "ollama"
                if case == "J4":
                    payload["anthropic_binding_fingerprint"] = None
            if case in {"J1", "J2", "J3", "J5"}:
                with pytest.raises(ValidationError):
                    runner.ModelReadinessV2.model_validate_json(json.dumps(payload))
            elif case == "J4":
                assert (
                    runner.ModelReadinessV2.model_validate_json(
                        json.dumps(payload)
                    ).anthropic_binding_fingerprint
                    is None
                )
            else:
                first = runner._readiness(
                    resolved=resolved,
                    snapshot=SimpleNamespace(graph_fingerprint="b" * 64),
                    prepared={},
                    receipts={},
                    mcp_revision="",
                    mcp_launcher_provenance=None,
                    model_readiness=(valid,),
                )
                assert first.schema_version == "ori-v2-run-readiness-v12"
                if case == "J0":
                    changed = runner.ModelReadinessV2.model_validate_json(
                        json.dumps({**payload, "anthropic_binding_fingerprint": "c" * 64})
                    )
                    second = runner._readiness(
                        resolved=resolved,
                        snapshot=SimpleNamespace(graph_fingerprint="b" * 64),
                        prepared={},
                        receipts={},
                        mcp_revision="",
                        mcp_launcher_provenance=None,
                        model_readiness=(changed,),
                    )
                    assert first.readiness_fingerprint != second.readiness_fingerprint
                else:
                    historic = first.model_dump(mode="json")
                    historic["schema_version"] = "ori-v2-run-readiness-v11"
                    historic["models"][0].pop("anthropic_binding_fingerprint")
                    with pytest.raises(ValidationError):
                        runner.CampaignReadinessV2.model_validate_json(json.dumps(historic))
            _assert_attempt_counters(s)
    assert visited == list(ids)
