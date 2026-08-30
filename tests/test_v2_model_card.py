from __future__ import annotations

import json
from pathlib import Path

import pytest

from ori.eval.provider_contract import ProviderApiSurface
from ori.eval.v2 import campaign_runner
from ori.eval.v2.campaign import PublicReportV2, PublicResultRow, RunIdentity
from ori.eval.v2.fingerprint import canonical_sha256
from ori.eval.v2.schema import ExecutionClass, Track
from ori.eval.v2.scoring import CampaignSummary, SampleOutcomeCode
from scripts.build_v2_model_card import ModelCardBuildError, build_model_card

_CONFIG = "a" * 64
_GRAPH = "b" * 64
_TARGET = "c" * 64
_PUBLIC = "d" * 64
_CAPABILITY = "f" * 64
_MODEL = "poolside/laguna-s-2.1"
_PROVIDER = "openai-compat"


def _catalog(track: Track) -> str:
    return ("e" if track is Track.DIRECT else "8") * 64


def _dump(path: Path, model: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(model.model_dump_json(indent=2) + "\n")  # type: ignore[attr-defined]


def _summary(track: Track, *, contradict_rows: bool = False) -> CampaignSummary:
    if track is Track.DIRECT:
        if contradict_rows:
            return CampaignSummary(
                scheduled=2,
                completed=1,
                correct=0,
                incorrect=2,
                model_failures=1,
                proof_failures=0,
                infrastructure_failures=0,
                harness_failures=0,
                unexecuted=0,
                reasoning_accuracy=0.0,
                effective_accuracy=0.0,
                campaign_valid=True,
            )
        return CampaignSummary(
            scheduled=2,
            completed=1,
            correct=1,
            incorrect=1,
            model_failures=1,
            proof_failures=0,
            infrastructure_failures=0,
            harness_failures=0,
            unexecuted=0,
            reasoning_accuracy=0.5,
            effective_accuracy=0.5,
            campaign_valid=True,
        )
    return CampaignSummary(
        scheduled=2,
        completed=0,
        correct=0,
        incorrect=1,
        model_failures=1,
        proof_failures=1,
        infrastructure_failures=0,
        harness_failures=0,
        unexecuted=0,
        reasoning_accuracy=0.0,
        effective_accuracy=0.0,
        campaign_valid=True,
    )


def _public_rows(track: Track) -> tuple[PublicResultRow, ...]:
    first = PublicResultRow(
        product="complex",
        track=track,
        task_id=f"{track.value}-one",
        task_fingerprint="1" * 64,
        execution_class=(
            ExecutionClass.SUCCESS
            if track is Track.DIRECT
            else ExecutionClass.MODEL_FAILURE
        ),
        outcome=(
            SampleOutcomeCode.COMPLETED
            if track is Track.DIRECT
            else SampleOutcomeCode.OUTPUT_INVALID
        ),
        reasoning_correct=(True if track is Track.DIRECT else False),
    )
    second = PublicResultRow(
        product="complex",
        track=track,
        task_id=f"{track.value}-two",
        task_fingerprint="2" * 64,
        execution_class=(
            ExecutionClass.MODEL_FAILURE
            if track is Track.DIRECT
            else ExecutionClass.PROOF_FAILURE
        ),
        outcome=(
            SampleOutcomeCode.OUTPUT_INVALID
            if track is Track.DIRECT
            else SampleOutcomeCode.PROOF_INSUFFICIENT
        ),
        reasoning_correct=(False if track is Track.DIRECT else None),
    )
    return first, second


def _model_report(
    track: Track,
    *,
    model: str = _MODEL,
    graph: str = _GRAPH,
    summary_mismatch: bool = False,
) -> campaign_runner.ModelPublicReportV2:
    report_payload = {
        "schema_version": "ori-eval-public-report-v2",
        "protocol_version": "ori-eval-protocol-v2",
        "product": "complex",
        "track": track,
        "public_artifact_fingerprint": _PUBLIC,
        "catalog_fingerprint": _catalog(track),
        "graph_fingerprint": graph,
        "capability_profile_fingerprint": _CAPABILITY,
        "rows": _public_rows(track),
        "summary": _summary(track, contradict_rows=summary_mismatch),
        "report_fingerprint": "0" * 64,
    }
    report_payload["report_fingerprint"] = canonical_sha256(
        report_payload, exclude_fields=("report_fingerprint",)
    )
    report = PublicReportV2.model_validate(report_payload)
    payload = {
        "schema_version": campaign_runner.MODEL_REPORT_SCHEMA_VERSION,
        "protocol_version": "ori-eval-protocol-v2",
        "run_identity": RunIdentity(
            provider=_PROVIDER,
            model=model,
            run_index=1,
            target_fingerprint=_TARGET,
            tool_loop=(None if track is Track.DIRECT else "native-openai-compatible"),
        ),
        "candidate_release_fingerprint": f"candidate-{track.value}",
        "live_certification_fingerprint": f"live-{track.value}",
        "graph_verification_before_fingerprint": f"before-{track.value}",
        "graph_verification_after_fingerprint": f"after-{track.value}",
        "report": report,
        "artifact_fingerprint": "0" * 64,
    }
    payload["artifact_fingerprint"] = canonical_sha256(
        payload, exclude_fields=("artifact_fingerprint",)
    )
    return campaign_runner.ModelPublicReportV2.model_validate(payload)


def _track_receipt(
    track: Track,
    report: campaign_runner.ModelPublicReportV2,
    *,
    valid: bool = True,
) -> campaign_runner.TrackCompletionV2:
    run = campaign_runner.TrackRunCompletionV2(
        provider=report.run_identity.provider,
        model=report.run_identity.model,
        run_index=report.run_identity.run_index,
        result_count=2,
        public_report_fingerprint=report.artifact_fingerprint,
        campaign_valid=valid,
        invalid_reasons=(() if valid else ("HARNESS_FAILURE",)),
    )
    payload = {
        "schema_version": campaign_runner.TRACK_COMPLETION_SCHEMA_VERSION,
        "protocol_version": "ori-eval-protocol-v2",
        "runner_version": campaign_runner.RUNNER_VERSION,
        "source_config_fingerprint": _CONFIG,
        "track": track,
        "candidate_release_fingerprint": report.candidate_release_fingerprint,
        "live_certification_fingerprint": report.live_certification_fingerprint,
        "graph_verification_before_fingerprint": report.graph_verification_before_fingerprint,
        "graph_verification_after_fingerprint": report.graph_verification_after_fingerprint,
        "expected_task_count_per_run": 2,
        "run_count": 1,
        "result_count": 2,
        "campaign_valid": valid,
        "runs": (run,),
        "completed_at_utc": "2026-08-30T12:00:00+00:00",
        "receipt_fingerprint": "0" * 64,
    }
    payload["receipt_fingerprint"] = canonical_sha256(
        payload, exclude_fields=("receipt_fingerprint",)
    )
    return campaign_runner.TrackCompletionV2.model_validate(payload)


def _readiness(
    reports: dict[Track, campaign_runner.ModelPublicReportV2],
) -> campaign_runner.CampaignReadinessV2:
    models = sorted({report.run_identity.model for report in reports.values()})
    model_receipts = tuple(
        campaign_runner.ModelReadinessV2(
            name=model.replace("/", "-"),
            provider=_PROVIDER,
            model=model,
            credential_check="NOUS_API_KEY",
            capability_check="openai-compatible-chat-completions",
            requested_api_surface=ProviderApiSurface.CHAT_COMPLETIONS,
            resolved_api_surface=ProviderApiSurface.CHAT_COMPLETIONS,
            structured_output_mode="prompt_local_validation",
            endpoint_family="nous",
            credential_source="NOUS_API_KEY",
        )
        for model in models
    )
    payload = {
        "schema_version": campaign_runner.READINESS_SCHEMA_VERSION,
        "protocol_version": "ori-eval-protocol-v2",
        "runner_version": campaign_runner.RUNNER_VERSION,
        "source_config_fingerprint": _CONFIG,
        "source_manifest_sha256": "3" * 64,
        "archive_sha256": "4" * 64,
        "graph_fingerprint": _GRAPH,
        "target_fingerprint": _TARGET,
        "mcp_server_revision": "92a37dd",
        "mcp_launcher_provenance": None,
        "tracks": tuple(
            campaign_runner.ReadinessTrackV2(
                track=track,
                public_artifact_fingerprint=_PUBLIC,
                oracle_artifact_fingerprint="5" * 64,
                candidate_release_fingerprint=report.candidate_release_fingerprint,
                live_certification_fingerprint=report.live_certification_fingerprint,
                capability_profile_fingerprint=_CAPABILITY,
                graph_verification_fingerprint=f"readiness-{track.value}",
                task_count=2,
            )
            for track, report in reports.items()
        ),
        "models": model_receipts,
        "model_count": len(model_receipts),
        "readiness_fingerprint": "0" * 64,
    }
    payload["readiness_fingerprint"] = canonical_sha256(
        payload, exclude_fields=("readiness_fingerprint",)
    )
    return campaign_runner.CampaignReadinessV2.model_validate(payload)


def _lifecycle(
    receipts: dict[Track, campaign_runner.TrackCompletionV2],
    *,
    status: str = "completed",
) -> campaign_runner.CampaignLifecycleV2:
    return campaign_runner._campaign_lifecycle(
        {
        "source_config_fingerprint": _CONFIG,
        "mode": "execution",
        "status": status,
        "started_at_utc": "2026-08-30T11:00:00+00:00",
        "updated_at_utc": "2026-08-30T12:00:00+00:00",
        "pid": 123,
        "resume_count": 0,
        "checkpointed_results": sum(item.result_count for item in receipts.values()),
        "active_track": None,
        "active_model": None,
        "active_run_index": None,
        "completed_tracks": tuple(
            campaign_runner.CampaignCompletedTrackV2(
                track=track,
                receipt_fingerprint=receipt.receipt_fingerprint,
            )
            for track, receipt in receipts.items()
        ),
        "interruptions": (),
        "failure_type": None,
        }
    )


def _campaign(
    tmp_path: Path,
    *,
    mcp_model: str = _MODEL,
    mcp_graph: str = _GRAPH,
    valid: bool = True,
    status: str = "completed",
    summary_mismatch: bool = False,
) -> Path:
    root = tmp_path / "private-campaign-root"
    reports = {
        Track.DIRECT: _model_report(
            Track.DIRECT,
            summary_mismatch=summary_mismatch,
        ),
        Track.MCP: _model_report(Track.MCP, model=mcp_model, graph=mcp_graph),
    }
    receipts = {
        track: _track_receipt(track, report, valid=valid)
        for track, report in reports.items()
    }
    for track, report in reports.items():
        _dump(
            root / track.value / "local-config-name" / "run-001" / "public-report-v2.json",
            report,
        )
        _dump(root / track.value / "track-completion-v2.private.json", receipts[track])
    _dump(root / "v2-run-readiness.private.json", _readiness(reports))
    _dump(root / "campaign-lifecycle-v2.private.json", _lifecycle(receipts, status=status))
    return root


def test_build_model_card_is_deterministic_separate_and_public_safe(
    tmp_path: Path,
) -> None:
    campaign = _campaign(tmp_path)
    first = tmp_path / "first"
    second = tmp_path / "second"

    card = build_model_card(
        campaign,
        first,
        model=_MODEL,
        display_name="Poolside: Laguna S 2.1",
    )
    build_model_card(
        campaign,
        second,
        model=_MODEL,
        display_name="Poolside: Laguna S 2.1",
    )

    assert card["campaign_valid"] is True
    assert card["tracks"]["direct"]["scheduled"] == 2
    assert card["tracks"]["direct"]["correct"] == 1
    assert card["tracks"]["direct"]["effective_accuracy"] == 0.5
    assert card["tracks"]["direct"]["catalog_fingerprint"] == _catalog(Track.DIRECT)
    assert card["tracks"]["mcp"]["proof_failures"] == 1
    assert card["tracks"]["mcp"]["effective_accuracy"] == 0.0
    assert card["tracks"]["mcp"]["catalog_fingerprint"] == _catalog(Track.MCP)
    assert set(path.name for path in first.iterdir()) == {
        "v29-model-card.json",
        "v29-model-card.svg",
    }
    for path in first.iterdir():
        assert path.read_bytes() == (second / path.name).read_bytes()
        text = path.read_text()
        assert str(tmp_path) not in text
        assert "private-campaign-root" not in text
        assert "NOUS_API_KEY" not in text
    svg = (first / "v29-model-card.svg").read_text()
    assert 'width="1280" height="720"' in svg
    assert "No combined score is reported" in svg


def test_build_model_card_rejects_incomplete_lifecycle(tmp_path: Path) -> None:
    campaign = _campaign(tmp_path, status="interrupted")

    with pytest.raises(ModelCardBuildError, match="not completed execution"):
        build_model_card(campaign, tmp_path / "output", model=_MODEL)


def test_build_model_card_rejects_invalid_track_completion(tmp_path: Path) -> None:
    campaign = _campaign(tmp_path, valid=False)

    with pytest.raises(ModelCardBuildError, match="track completion is invalid"):
        build_model_card(campaign, tmp_path / "output", model=_MODEL)


def test_build_model_card_rejects_mismatched_graph(tmp_path: Path) -> None:
    campaign = _campaign(tmp_path, mcp_graph="9" * 64)

    with pytest.raises(ModelCardBuildError, match="mcp graph fingerprint mismatch"):
        build_model_card(campaign, tmp_path / "output", model=_MODEL)


def test_build_model_card_rejects_mismatched_direct_mcp_models(tmp_path: Path) -> None:
    campaign = _campaign(tmp_path, mcp_model="different/model")

    with pytest.raises(ModelCardBuildError, match="exactly one common"):
        build_model_card(campaign, tmp_path / "output", model=_MODEL)


def test_build_model_card_rejects_extra_public_report(tmp_path: Path) -> None:
    campaign = _campaign(tmp_path)
    source = next((campaign / "direct").glob("*/run-*/public-report-v2.json"))
    extra = campaign / "direct" / "stale-local-name" / "run-001" / source.name
    extra.parent.mkdir(parents=True)
    extra.write_bytes(source.read_bytes())

    with pytest.raises(ModelCardBuildError, match="duplicate direct public run identity"):
        build_model_card(campaign, tmp_path / "output", model=_MODEL)


def test_build_model_card_rejects_local_path_in_public_label(tmp_path: Path) -> None:
    campaign = _campaign(tmp_path)

    with pytest.raises(ModelCardBuildError, match="must not contain a local path"):
        build_model_card(
            campaign,
            tmp_path / "output",
            model=_MODEL,
            display_name="/Users/operator/private-model",
        )


def test_build_model_card_rejects_summary_that_disagrees_with_rows(
    tmp_path: Path,
) -> None:
    campaign = _campaign(tmp_path, summary_mismatch=True)

    with pytest.raises(ModelCardBuildError, match="direct public summary disagrees"):
        build_model_card(campaign, tmp_path / "output", model=_MODEL)


def test_build_model_card_json_contains_no_rows_or_private_payloads(
    tmp_path: Path,
) -> None:
    campaign = _campaign(tmp_path)
    output = tmp_path / "output"
    build_model_card(campaign, output, model=_MODEL)

    payload = json.loads((output / "v29-model-card.json").read_text())
    serialized = json.dumps(payload).casefold()
    for forbidden in (
        '"rows":',
        '"prompt":',
        '"response":',
        '"tool_body":',
        '"credential_source":',
        '"target_fingerprint":',
        "local-config-name",
    ):
        assert forbidden not in serialized
