"""Synthetic, fingerprinted campaign artifacts shared by offline tests."""

from __future__ import annotations

from pathlib import Path

import yaml

from ori.eval.provider_contract import ProviderApiSurface
from ori.eval.v2 import campaign_runner
from ori.eval.v2.campaign import (
    CheckpointTaskBinding,
    CheckpointV2,
    PublicReportV2,
    PublicResultRow,
    RunIdentity,
    RunProvenanceV2,
)
from ori.eval.v2.fingerprint import canonical_sha256
from ori.eval.v2.model_runtime import ProviderRunRecord
from ori.eval.v2.schema import ExecutionClass, Track
from ori.eval.v2.scoring import CampaignSummary, SampleOutcomeCode, SampleResult

_A = "a" * 64
_B = "b" * 64
_C = "c" * 64
_D = "d" * 64
_E = "e" * 64
_F = "f" * 64


def _schedule(track: Track) -> campaign_runner.ScheduledTaskRosterV2:
    payload = {
        "track": track,
        "purpose": "official",
        "ranking_eligible": True,
        "suite": "candidate-catalog-v1",
        "selection_fingerprint": "5" * 64,
        "task_ids": (f"{track.value}-task",),
        "schedule_fingerprint": "0" * 64,
    }
    payload["schedule_fingerprint"] = canonical_sha256(
        payload, exclude_fields=("schedule_fingerprint",)
    )
    return campaign_runner.ScheduledTaskRosterV2.model_validate(payload)


def _config(tmp_path: Path, tracks: tuple[Track, ...] = (Track.DIRECT,)) -> Path:
    for name in ("manifest.json", "archive.zip"):
        (tmp_path / name).touch()
    (tmp_path / "mcp").mkdir()
    track_paths: dict[str, dict[str, str]] = {}
    for track in tracks:
        names = {
            key: f"{track.value}-{suffix}.json"
            for key, suffix in (
                ("public", "public"),
                ("oracles", "private"),
                ("candidates", "candidates"),
                ("live_certification", "live"),
            )
        }
        for name in names.values():
            (tmp_path / name).touch()
        track_paths[track.value] = names
    payload = {
        "version": 2,
        "protocol": "ori-eval-protocol-v2",
        "source": {"manifest": "manifest.json", "archive": "archive.zip"},
        "tracks": track_paths,
        "modes": [track.value for track in tracks],
        "output_dir": "campaign",
        "defaults": {
            "concurrency": 1,
            "runs_per_model": 1,
            "mcp": {
                "mcp_dir": "mcp",
                "resource_mode": "off",
                "tool_loop": "native-openai-compatible",
            },
        },
        "models": [
            {
                "name": "gpt-test",
                "provider": "codex",
                "model": "gpt-test",
            }
        ],
    }
    path = tmp_path / "models-v2.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False))
    return path


def _dump(path: Path, model: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(model.model_dump_json(indent=2) + "\n")  # type: ignore[attr-defined]
    if isinstance(model, campaign_runner.CampaignLifecycleV2):
        (path.parent / ".ori-v2-campaign.lock").touch(exist_ok=True)


def _base_provenance(track: Track) -> RunProvenanceV2:
    payload = {
        "schema_version": "ori-eval-run-provenance-v2",
        "protocol_version": "ori-eval-protocol-v2",
        "product": "complex",
        "track": track,
        "public_artifact_fingerprint": _A,
        "oracle_artifact_fingerprint": _B,
        "catalog_fingerprint": _C,
        "graph_fingerprint": _D,
        "compiler_fingerprint": _E,
        "comparator_fingerprint": _F,
        "capability_profile_fingerprint": "1" * 64,
        "provenance_fingerprint": "0" * 64,
    }
    payload["provenance_fingerprint"] = canonical_sha256(
        payload, exclude_fields=("provenance_fingerprint",)
    )
    return RunProvenanceV2.model_validate(payload)


def _provenance(track: Track, run_index: int = 1) -> campaign_runner.ModelRunProvenanceV2:
    identity = RunIdentity(
        provider="codex",
        model="gpt-test",
        run_index=run_index,
        target_fingerprint="2" * 64,
        tool_loop=("native-openai-compatible" if track is Track.MCP else None),
    )
    payload = {
        "schema_version": campaign_runner.RUNNER_VERSION,
        "protocol_version": "ori-eval-protocol-v2",
        "base": _base_provenance(track),
        "run_identity": identity,
        "source_manifest_sha256": "3" * 64,
        "archive_sha256": "4" * 64,
        "candidate_release_fingerprint": "5" * 64,
        "live_certification_fingerprint": "6" * 64,
        "schedule": _schedule(track),
        "containment_config_fingerprint": "7" * 64,
        "runtime_implementation_fingerprint": "8" * 64,
        "runtime_config_fingerprint": "9" * 64,
        "requested_api_surface": ProviderApiSurface.AUTO,
        "resolved_api_surface": ProviderApiSurface.RESPONSES,
        "structured_output_mode": "prompt_local_validation",
        "endpoint_family": "codex_oauth",
        "credential_source": None,
        "mcp_launcher_provenance": None,
        "provenance_fingerprint": "0" * 64,
    }
    payload["provenance_fingerprint"] = canonical_sha256(
        payload, exclude_fields=("provenance_fingerprint",)
    )
    return campaign_runner.ModelRunProvenanceV2.model_validate(payload)


def _sample(track: Track, *, campaign_valid: bool = True) -> SampleResult:
    execution_class = (
        ExecutionClass.MODEL_FAILURE if campaign_valid else ExecutionClass.HARNESS_FAILURE
    )
    return SampleResult(
        task_id=f"{track.value}-task",
        task_fingerprint="a" * 64,
        oracle_fingerprint="b" * 64,
        execution_class=execution_class,
        outcome=SampleOutcomeCode.OUTPUT_INVALID,
        reasoning_correct=None,
        detail=(
            "SECRET_PROMPT SECRET_TOOL_ARGUMENT SECRET_CREDENTIAL_VALUE /private/operator/campaign"
        ),
    )


def _provider(sample: SampleResult, track: Track) -> ProviderRunRecord:
    payload = {
        "task_id": sample.task_id,
        "task_fingerprint": sample.task_fingerprint,
        "provider_model": "gpt-test",
        "surface": track.value,
        "raw_response": "SECRET_PROVIDER_RESPONSE",
        "response_digest": canonical_sha256("SECRET_PROVIDER_RESPONSE"),
        "tokens_input": 1,
        "tokens_output": 1,
        "elapsed_seconds": 0.1,
        "provider_error": None,
        "provider_metrics": {},
        "direct_query_digest": None,
        "direct_receipt": None,
        "mcp_events": (),
        "mcp_tool_receipts": (),
        "mcp_finalization": None,
        "mcp_transcript": (),
        "transcript_digest": None,
        "record_fingerprint": "0" * 64,
    }
    payload["record_fingerprint"] = canonical_sha256(
        payload, exclude_fields=("record_fingerprint",)
    )
    return ProviderRunRecord.model_validate(payload)


def _state(
    provenance: campaign_runner.ModelRunProvenanceV2,
    track: Track,
    *,
    campaign_valid: bool = True,
    attempt_count: int = 1,
) -> campaign_runner.PrivateRunStateV2:
    sample = _sample(track, campaign_valid=campaign_valid)
    checkpoint_payload = {
        "schema_version": "ori-eval-checkpoint-v2",
        "protocol_version": "ori-eval-protocol-v2",
        "product": "complex",
        "track": track,
        "public_artifact_fingerprint": _A,
        "oracle_artifact_fingerprint": _B,
        "catalog_fingerprint": _C,
        "graph_fingerprint": _D,
        "compiler_fingerprint": _E,
        "comparator_fingerprint": _F,
        "capability_profile_id": "test-profile",
        "capability_profile_fingerprint": "1" * 64,
        "containment_policy_version": "policy" if track is Track.DIRECT else None,
        "finalization_policy_fingerprint": "2" * 64 if track is Track.MCP else None,
        "run_identity": provenance.run_identity,
        "task_bindings": (
            CheckpointTaskBinding(
                task_id=sample.task_id,
                task_fingerprint=sample.task_fingerprint,
                oracle_fingerprint=sample.oracle_fingerprint,
                bounds_fingerprint="3" * 64,
            ),
        ),
        "results": (sample,),
        "checkpoint_fingerprint": "0" * 64,
    }
    checkpoint_payload["checkpoint_fingerprint"] = canonical_sha256(
        checkpoint_payload, exclude_fields=("checkpoint_fingerprint",)
    )
    checkpoint = CheckpointV2.model_validate(checkpoint_payload)
    attempts = tuple(
        campaign_runner._attempt(
            sample.task_id,
            attempt_number,
            sample,
            _provider(sample, track),
        )
        for attempt_number in range(1, attempt_count + 1)
    )
    return campaign_runner._state(
        provenance=provenance,
        checkpoint=checkpoint,
        attempts=attempts,
    )


def _report(
    provenance: campaign_runner.ModelRunProvenanceV2,
    track: Track,
    *,
    campaign_valid: bool = True,
) -> campaign_runner.ModelPublicReportV2:
    sample = _sample(track, campaign_valid=campaign_valid)
    state = _state(provenance, track, campaign_valid=campaign_valid)
    if campaign_valid:
        summary = CampaignSummary(
            scheduled=1,
            completed=0,
            correct=0,
            incorrect=0,
            model_failures=1,
            proof_failures=0,
            infrastructure_failures=0,
            harness_failures=0,
            unexecuted=0,
            output_compliant=0,
            output_noncompliant=0,
            output_normalized=0,
            reasoning_accuracy=None,
            effective_accuracy=0.0,
            output_compliance_rate=None,
            campaign_valid=True,
            invalid_reasons=(),
        )
    else:
        summary = CampaignSummary(
            scheduled=1,
            completed=0,
            correct=0,
            incorrect=0,
            model_failures=0,
            proof_failures=0,
            infrastructure_failures=0,
            harness_failures=1,
            unexecuted=0,
            output_compliant=0,
            output_noncompliant=0,
            output_normalized=0,
            reasoning_accuracy=None,
            effective_accuracy=0.0,
            output_compliance_rate=None,
            campaign_valid=False,
            invalid_reasons=("HARNESS_FAILURE",),
        )
    report_payload = {
        "schema_version": "ori-eval-public-report-v3",
        "protocol_version": "ori-eval-protocol-v2",
        "product": "complex",
        "track": track,
        "public_artifact_fingerprint": _A,
        "catalog_fingerprint": _C,
        "graph_fingerprint": _D,
        "capability_profile_fingerprint": "1" * 64,
        "rows": (
            PublicResultRow(
                product="complex",
                track=track,
                task_id=sample.task_id,
                task_fingerprint=sample.task_fingerprint,
                execution_class=sample.execution_class,
                outcome=sample.outcome,
                reasoning_correct=None,
            ),
        ),
        "summary": summary,
        "report_fingerprint": "0" * 64,
    }
    report_payload["report_fingerprint"] = canonical_sha256(
        report_payload, exclude_fields=("report_fingerprint",)
    )
    body = PublicReportV2.model_validate(report_payload)
    payload = {
        "schema_version": campaign_runner.MODEL_REPORT_SCHEMA_VERSION,
        "protocol_version": "ori-eval-protocol-v2",
        "run_identity": provenance.run_identity,
        "candidate_release_fingerprint": provenance.candidate_release_fingerprint,
        "live_certification_fingerprint": provenance.live_certification_fingerprint,
        "schedule_fingerprint": provenance.schedule.schedule_fingerprint,
        "graph_verification_before_fingerprint": "c" * 64,
        "graph_verification_after_fingerprint": "d" * 64,
        "operational_metrics": campaign_runner._run_operational_metrics(state),
        "report": body,
        "artifact_fingerprint": "0" * 64,
    }
    payload["artifact_fingerprint"] = canonical_sha256(
        payload, exclude_fields=("artifact_fingerprint",)
    )
    return campaign_runner.ModelPublicReportV2.model_validate(payload)


def _readiness(config_fingerprint: str, tracks: tuple[Track, ...]):
    track_receipts = tuple(
        campaign_runner.ReadinessTrackV2(
            track=track,
            target_fingerprint="2" * 64,
            public_artifact_fingerprint=_A,
            oracle_artifact_fingerprint=_B,
            candidate_release_fingerprint="5" * 64,
            live_certification_fingerprint="6" * 64,
            capability_profile_fingerprint="1" * 64,
            graph_verification_fingerprint="c" * 64,
            task_count=1,
            schedule=_schedule(track),
        )
        for track in tracks
    )
    models = (
        campaign_runner.ModelReadinessV2(
            name="gpt-test",
            provider="codex",
            model="gpt-test",
            credential_check="codex-oauth",
            capability_check="codex-model-cache",
            requested_api_surface=ProviderApiSurface.AUTO,
            resolved_api_surface=ProviderApiSurface.RESPONSES,
            structured_output_mode="prompt_local_validation",
            endpoint_family="codex_oauth",
        ),
    )
    payload = {
        "schema_version": campaign_runner.READINESS_SCHEMA_VERSION,
        "protocol_version": "ori-eval-protocol-v2",
        "runner_version": campaign_runner.RUNNER_VERSION,
        "purpose": "official",
        "ranking_eligible": True,
        "source_config_fingerprint": config_fingerprint,
        "source_manifest_sha256": "3" * 64,
        "archive_sha256": "4" * 64,
        "graph_fingerprint": _D,
        "target_fingerprint": canonical_sha256({track.value: "2" * 64 for track in tracks}),
        "mcp_server_revision": "revision",
        "mcp_launcher_provenance": None,
        "tracks": track_receipts,
        "models": models,
        "model_count": 1,
        "readiness_fingerprint": "0" * 64,
    }
    payload["readiness_fingerprint"] = canonical_sha256(
        payload, exclude_fields=("readiness_fingerprint",)
    )
    return campaign_runner.CampaignReadinessV2.model_validate(payload)


def _track_receipt(
    config_fingerprint: str,
    track: Track,
    report: campaign_runner.ModelPublicReportV2,
):
    run = campaign_runner.TrackRunCompletionV2(
        provider="codex",
        model="gpt-test",
        run_index=1,
        result_count=1,
        public_report_fingerprint=report.artifact_fingerprint,
        campaign_valid=report.report.summary.campaign_valid,
        invalid_reasons=report.report.summary.invalid_reasons,
    )
    payload = {
        "schema_version": campaign_runner.TRACK_COMPLETION_SCHEMA_VERSION,
        "protocol_version": "ori-eval-protocol-v2",
        "runner_version": campaign_runner.RUNNER_VERSION,
        "source_config_fingerprint": config_fingerprint,
        "track": track,
        "candidate_release_fingerprint": "5" * 64,
        "live_certification_fingerprint": "6" * 64,
        "graph_verification_before_fingerprint": "c" * 64,
        "graph_verification_after_fingerprint": "d" * 64,
        "expected_task_count_per_run": 1,
        "schedule": _schedule(track),
        "run_count": 1,
        "result_count": 1,
        "campaign_valid": report.report.summary.campaign_valid,
        "runs": (run,),
        "completed_at_utc": "2026-08-30T12:01:00+00:00",
        "receipt_fingerprint": "0" * 64,
    }
    payload["receipt_fingerprint"] = canonical_sha256(
        payload, exclude_fields=("receipt_fingerprint",)
    )
    return campaign_runner.TrackCompletionV2.model_validate(payload)


def _lifecycle(
    config_fingerprint: str,
    *,
    status: str,
    mode: str = "execution",
    checkpointed_results: int = 0,
    completed: tuple[campaign_runner.TrackCompletionV2, ...] = (),
    active: tuple[Track, str, int] | None = None,
    tracks: tuple[Track, ...] | None = None,
):
    scheduled_tracks = (
        tracks
        or tuple(item.track for item in completed)
        or ((active[0],) if active is not None else (Track.DIRECT,))
    )
    return campaign_runner._campaign_lifecycle(
        {
            "source_config_fingerprint": config_fingerprint,
            "purpose": "official",
            "ranking_eligible": True,
            "schedules": tuple(_schedule(track) for track in scheduled_tracks),
            "mode": mode,
            "status": status,
            "started_at_utc": "2026-08-30T12:00:00+00:00",
            "updated_at_utc": "2026-08-30T12:01:00+00:00",
            "pid": 123,
            "resume_count": 1,
            "checkpointed_results": checkpointed_results,
            "active_track": active[0] if active else None,
            "active_model": active[1] if active else None,
            "active_run_index": active[2] if active else None,
            "completed_tracks": tuple(
                campaign_runner.CampaignCompletedTrackV2(
                    track=item.track,
                    receipt_fingerprint=item.receipt_fingerprint,
                )
                for item in completed
            ),
            "interruptions": (),
            "failure_type": "RuntimeError" if status == "failed" else None,
        }
    )


def _completed_campaign(
    tmp_path: Path,
    tracks: tuple[Track, ...] = (Track.DIRECT,),
    *,
    campaign_valid: bool = True,
):
    config = _config(tmp_path, tracks)
    from ori.eval.v2.campaign_config import load_v2_campaign_config

    resolved = load_v2_campaign_config(config)
    receipts = []
    for track in tracks:
        provenance = _provenance(track)
        state = _state(provenance, track)
        report = _report(provenance, track, campaign_valid=campaign_valid)
        run_dir = resolved.output_dir / track.value / "gpt-test" / "run-001"
        _dump(run_dir / "campaign-provenance-v2.json", provenance)
        _dump(run_dir / "run-state-v7.private.json", state)
        _dump(run_dir / "public-report-v2.json", report)
        receipt = _track_receipt(resolved.source_config_fingerprint, track, report)
        _dump(resolved.output_dir / track.value / "track-completion-v2.private.json", receipt)
        receipts.append(receipt)
    _dump(
        resolved.output_dir / "v2-run-readiness.private.json",
        _readiness(resolved.source_config_fingerprint, tracks),
    )
    _dump(
        resolved.output_dir / "campaign-lifecycle-v2.private.json",
        _lifecycle(
            resolved.source_config_fingerprint,
            status="completed",
            checkpointed_results=len(tracks),
            completed=tuple(receipts),
            tracks=tracks,
        ),
    )
    return config, resolved
