"""Offline campaign publication acceptance, with external boundaries sealed.

Artifact preparation is supplied by deterministic compiler fixtures. Everything
from graph comparison through task scheduling, checkpoints, redacted reports,
track completion, and lifecycle persistence runs through production code.
"""

from __future__ import annotations

import asyncio
import socket
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from ori.eval.provider_contract import ProviderApiSurface
from ori.eval.v2 import campaign_runner
from ori.eval.v2.campaign_config import load_v2_campaign_config
from ori.eval.v2.fingerprint import canonical_sha256
from ori.eval.v2.graph import LiveGraphVerification
from ori.eval.v2.profiles import capability_profile_for_track
from ori.eval.v2.protocol import V2ArtifactPair, build_artifacts
from ori.eval.v2.schema import ExecutionClass, Track
from ori.eval.v2.scoring import SampleOutcomeCode, SampleResult

from .test_v2_campaign_status import _config, _provider
from .test_v2_compiler import simple_compiled  # noqa: F401 -- shared compiled fixture


@pytest.mark.parametrize("drift_gate", [None, 1, 2, 3, 4])
def test_campaign_publication_requires_each_independent_graph_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
    drift_gate: int | None,
) -> None:
    def forbidden(*_args, **_kwargs):
        raise AssertionError("offline acceptance attempted external access")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", forbidden)

    config_path = _config(tmp_path, (Track.DIRECT, Track.MCP))
    resolved = load_v2_campaign_config(config_path)
    resolved = resolved.model_copy(
        update={
            "config": resolved.config.model_copy(
                update={
                    "defaults": resolved.config.defaults.model_copy(
                        update={
                            "bhce_url": "http://127.0.0.1:1",
                        }
                    ),
                }
            ),
        }
    )
    _, snapshot, direct, mcp = request.getfixturevalue("simple_compiled")
    prepared = {}
    for track, corpus in ((Track.DIRECT, direct), (Track.MCP, mcp)):
        public, private = build_artifacts(corpus, identity_catalog=snapshot.entities)
        prepared[track] = campaign_runner.PreparedTrack(
            track=track,
            pair=V2ArtifactPair(public=public, private=private),
            profile=capability_profile_for_track(track),
            release=SimpleNamespace(
                entries=(SimpleNamespace(task_id=public.tasks[0].task_id),),
                release_fingerprint="a" * 64,
            ),
            live=SimpleNamespace(artifact_fingerprint="b" * 64),
            certifications={},
        )
    model_readiness = (
        campaign_runner.ModelReadinessV2(
            name="gpt-test",
            provider="codex",
            model="gpt-test",
            credential_check="offline_fixture",
            capability_check="offline_fixture",
            requested_api_surface=ProviderApiSurface.AUTO,
            resolved_api_surface=ProviderApiSurface.RESPONSES,
            structured_output_mode="prompt_local_validation",
            endpoint_family="codex_oauth",
        ),
    )
    monkeypatch.setattr(
        campaign_runner,
        "prepare_v2_campaign",
        lambda _: (
            resolved,
            snapshot,
            prepared,
            "offline-fixture",
            model_readiness,
        ),
    )
    # No MCP child or provider is started. Provenance resolution remains real;
    # the injected runtime is the launcher boundary, not a simulated runner.
    monkeypatch.setattr(
        campaign_runner,
        "resolve_mcp_launcher_runtime",
        lambda _: SimpleNamespace(provenance=lambda _: None),
    )

    async def load_bundle(*_args, **_kwargs):
        return object()

    monkeypatch.setattr(campaign_runner, "_load_bloodhound_mcp_bundle", load_bundle)
    events = []
    samples = []
    receipts = []
    preserved_direct = {}

    class OfflineBHCE:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def wait_until_healthy(self, **_kwargs):
            return SimpleNamespace(ok=True)

    monkeypatch.setattr(campaign_runner, "BHCEClient", OfflineBHCE)

    async def collect(_client, expected, *, page_size):
        gate = len(receipts) + 1
        events.append(f"graph-{gate}")
        if gate == 3:
            preserved_direct.update(
                {
                    str(path.relative_to(resolved.output_dir)): path.read_bytes()
                    for path in (resolved.output_dir / "direct").rglob("*.json")
                }
            )
        observed = expected
        if gate == drift_gate:
            # A real semantic graph change; production fingerprint comparison
            # must refuse it, rather than trusting the receipt's success shape.
            payload = expected.model_dump(mode="python")
            payload["relationships"] = payload["relationships"][1:]
            payload["graph_fingerprint"] = canonical_sha256(
                payload,
                exclude_fields=("graph_fingerprint",),
            )
            observed = type(expected).model_validate(payload)
        payload = {
            "schema_version": "ori-live-graph-verification-v2",
            "expected_graph_fingerprint": expected.graph_fingerprint,
            "observed_graph_fingerprint": observed.graph_fingerprint,
            "page_size": page_size,
            "object_queries": gate,
            "relationship_queries": 1,
            "object_count": len(observed.objects),
            "relationship_count": len(observed.relationships),
            "normalized_artifacts": (),
            "verification_fingerprint": "0" * 64,
        }
        payload["verification_fingerprint"] = canonical_sha256(
            payload,
            exclude_fields=("verification_fingerprint",),
        )
        receipt = LiveGraphVerification.model_validate(payload)
        receipts.append(receipt)
        return observed, receipt

    monkeypatch.setattr(campaign_runner, "collect_live_snapshot", collect)

    def result(task, oracle, track):
        events.append(f"provider-{track.value}")
        sample = SampleResult(
            task_id=task.task_id,
            task_fingerprint=task.task_fingerprint,
            oracle_fingerprint=oracle.oracle_fingerprint,
            execution_class=ExecutionClass.MODEL_FAILURE,
            outcome=SampleOutcomeCode.OUTPUT_INVALID,
            detail="PRIVATE_ACCEPTANCE_DETAIL",
        )
        samples.append(sample)
        return sample, _provider(sample, track)

    async def direct_call(*, task, oracle, **_kwargs):
        sample, provider = result(task, oracle, Track.DIRECT)
        return None, sample, provider

    async def mcp_call(*, task, oracle, **_kwargs):
        sample, provider = result(task, oracle, Track.MCP)
        return SimpleNamespace(sample=sample), provider

    monkeypatch.setattr(campaign_runner, "run_direct_model_task_v2", direct_call)
    monkeypatch.setattr(campaign_runner, "run_mcp_model_task_v2", mcp_call)
    if drift_gate is None:
        asyncio.run(campaign_runner.run_v2_campaign(config_path))
    else:
        with pytest.raises(ValueError, match="live graph fingerprint mismatch"):
            asyncio.run(campaign_runner.run_v2_campaign(config_path))

    expected_events = [
        "graph-1",
        "provider-direct",
        "graph-2",
        "graph-3",
        "provider-mcp",
        "graph-4",
    ]
    if drift_gate is not None:
        expected_events = expected_events[: expected_events.index(f"graph-{drift_gate}") + 1]
    assert events == expected_events
    assert len({receipt.verification_fingerprint for receipt in receipts}) == len(receipts)
    lifecycle = campaign_runner.CampaignLifecycleV2.model_validate_json(
        (resolved.output_dir / "campaign-lifecycle-v2.private.json").read_text(),
    )
    assert lifecycle.status == ("completed" if drift_gate is None else "failed")
    assert lifecycle.checkpointed_results == len(samples)
    assert (resolved.output_dir / "v2-run-readiness.private.json").exists() is (
        drift_gate is None
    )
    if drift_gate is not None:
        assert lifecycle.failure_type == "ValueError"
    for track, post_gate in ((Track.DIRECT, 2), (Track.MCP, 4)):
        published = drift_gate is None or drift_gate > post_gate
        track_dir = resolved.output_dir / track.value
        report_path = track_dir / "gpt-test" / "run-001" / "public-report-v2.json"
        completion_path = track_dir / "track-completion-v2.private.json"
        assert report_path.exists() is published
        assert completion_path.exists() is published
        if published:
            report = campaign_runner.ModelPublicReportV2.model_validate_json(
                report_path.read_text(),
            )
            completion = campaign_runner.TrackCompletionV2.model_validate_json(
                completion_path.read_text(),
            )
            assert completion.campaign_valid
            assert completion.result_count == 1
            assert completion.runs[0].public_report_fingerprint == report.artifact_fingerprint
            assert completion.graph_verification_before_fingerprint == (
                receipts[post_gate - 2].verification_fingerprint
            )
            assert completion.graph_verification_after_fingerprint == (
                receipts[post_gate - 1].verification_fingerprint
            )
            assert "PRIVATE_ACCEPTANCE_DETAIL" not in report_path.read_text()
            assert "SECRET_PROVIDER_RESPONSE" not in report_path.read_text()
        checkpoint_path = track_dir / "gpt-test" / "run-001" / campaign_runner.RUN_STATE_NAME
        attempted = f"provider-{track.value}" in events
        assert checkpoint_path.exists() is attempted
        if attempted:
            state = campaign_runner.PrivateRunStateV2.model_validate_json(
                checkpoint_path.read_text(),
            )
            assert state.scheduler.phase == "complete"
            assert len(state.attempts) == len(state.checkpoint.results) == 1
            assert state.checkpoint.results[0].detail == "PRIVATE_ACCEPTANCE_DETAIL"
    for relative, original in preserved_direct.items():
        assert (resolved.output_dir / relative).read_bytes() == original
    with campaign_runner._exclusive_output_dir_lock(resolved.output_dir):
        pass
