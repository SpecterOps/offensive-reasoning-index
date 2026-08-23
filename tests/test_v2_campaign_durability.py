from __future__ import annotations

import asyncio
import json
import os
import signal
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from ori.eval.v2 import campaign_runner
from ori.eval.v2.schema import Track


def test_atomic_write_fsyncs_file_and_parent_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "nested" / "receipt.json"
    real_fsync = os.fsync
    synced: list[str] = []

    def recording_fsync(file_descriptor: int) -> None:
        mode = os.fstat(file_descriptor).st_mode
        synced.append("directory" if stat.S_ISDIR(mode) else "file")
        real_fsync(file_descriptor)

    monkeypatch.setattr(campaign_runner.os, "fsync", recording_fsync)

    campaign_runner._atomic_write(destination, {"checkpointed": 407})

    assert json.loads(destination.read_text()) == {"checkpointed": 407}
    assert synced == ["file", "directory"]
    assert list(destination.parent.glob(f".{destination.name}.*.tmp")) == []


def test_output_directory_lock_rejects_concurrent_resume(tmp_path: Path) -> None:
    with campaign_runner._exclusive_output_dir_lock(tmp_path):
        with pytest.raises(
            campaign_runner.V2CampaignRunError,
            match="already locked by another v2 campaign",
        ):
            with campaign_runner._exclusive_output_dir_lock(tmp_path):
                raise AssertionError("second campaign must never enter the output directory")

    with campaign_runner._exclusive_output_dir_lock(tmp_path):
        pass


def test_operator_interruption_is_audited_without_consuming_retry_budget() -> None:
    interrupted = SimpleNamespace(
        sample=SimpleNamespace(outcome=SimpleNamespace(value="INTERRUPTED")),
        provider=SimpleNamespace(
            provider_metrics={
                "infra_scope": "operator",
                "infra_error_subtype": "INTERRUPTED",
            }
        ),
    )
    infrastructure = SimpleNamespace(
        sample=SimpleNamespace(outcome=SimpleNamespace(value="INFRA_ERROR")),
        provider=SimpleNamespace(
            provider_metrics={
                "infra_scope": "provider",
                "infra_error_subtype": "READ_TIMEOUT",
            }
        ),
    )

    assert campaign_runner._attempt_consumes_retry_budget(interrupted) is False
    assert campaign_runner._attempt_consumes_retry_budget(infrastructure) is True


def test_campaign_installs_terminal_and_process_signal_handlers() -> None:
    async def installed() -> tuple[signal.Signals, ...]:
        controller = campaign_runner._SignalCancellation.install()
        try:
            return controller.installed
        finally:
            controller.close()

    assert set(asyncio.run(installed())) == {
        signal.SIGINT,
        signal.SIGTERM,
        signal.SIGHUP,
    }


def test_campaign_lifecycle_records_checkpoint_and_interruption_then_detects_unclean_resume(
    tmp_path: Path,
) -> None:
    controller = campaign_runner._CampaignLifecycleController.start(
        output_dir=tmp_path,
        source_config_fingerprint="a" * 64,
        preflight_only=False,
    )
    controller.record_checkpoint(
        track=Track.MCP,
        model_name="gpt-test",
        run_index=2,
        result_count=407,
    )
    controller.interrupt(kind="task_cancelled", signal_name=None)

    receipt = campaign_runner.CampaignLifecycleV2.model_validate_json(
        (tmp_path / "campaign-lifecycle-v2.private.json").read_text()
    )
    assert receipt.status == "interrupted"
    assert receipt.checkpointed_results == 407
    assert receipt.active_track is Track.MCP
    assert receipt.active_model == "gpt-test"
    assert receipt.active_run_index == 2
    assert receipt.interruptions[-1].kind == "task_cancelled"

    # A process killed without a Python exception leaves a durable running
    # receipt. The next resume must turn that into explicit interruption history.
    running = campaign_runner._CampaignLifecycleController.start(
        output_dir=tmp_path,
        source_config_fingerprint="a" * 64,
        preflight_only=False,
    )
    resumed = campaign_runner._CampaignLifecycleController.start(
        output_dir=tmp_path,
        source_config_fingerprint="a" * 64,
        preflight_only=False,
    )
    assert running.receipt.status == "running"
    assert resumed.receipt.resume_count == 2
    assert resumed.receipt.interruptions[-1].kind == "unclean_previous_process"


class _FakeSummary(BaseModel):
    campaign_valid: bool
    invalid_reasons: tuple[str, ...] = ()


class _FakePublicBody(BaseModel):
    summary: _FakeSummary


class _FakeModelReport(BaseModel):
    artifact_fingerprint: str
    report: _FakePublicBody


def test_track_completion_publishes_reports_and_durable_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _FakeModelReport(
        artifact_fingerprint="b" * 64,
        report=_FakePublicBody(summary=_FakeSummary(campaign_valid=True)),
    )
    monkeypatch.setattr(campaign_runner, "_model_report", lambda **_kwargs: report)
    prepared = SimpleNamespace(
        track=Track.DIRECT,
        task_ids=("task-1", "task-2"),
        release=SimpleNamespace(release_fingerprint="c" * 64),
        live=SimpleNamespace(artifact_fingerprint="d" * 64),
    )
    provenance = SimpleNamespace(
        run_identity=SimpleNamespace(
            provider="codex",
            model="gpt-test",
            run_index=1,
            target_fingerprint="e" * 64,
            tool_loop=None,
        )
    )
    run_dir = tmp_path / "direct" / "gpt-test" / "run-001"
    before = SimpleNamespace(verification_fingerprint="f" * 64)
    after = SimpleNamespace(verification_fingerprint="1" * 64)

    receipt, invalid = campaign_runner._publish_track_completion(
        resolved=SimpleNamespace(
            output_dir=tmp_path,
            source_config_fingerprint="a" * 64,
        ),
        prepared=prepared,
        completed_runs=((provenance, (object(), object()), run_dir),),
        before=before,
        after=after,
    )

    assert invalid == ()
    assert (run_dir / "public-report-v2.json").exists()
    persisted = campaign_runner.TrackCompletionV2.model_validate_json(
        (tmp_path / "direct" / "track-completion-v2.private.json").read_text()
    )
    assert persisted == receipt
    assert persisted.run_count == 1
    assert persisted.result_count == 2
    assert persisted.runs[0].public_report_fingerprint == "b" * 64
    assert persisted.graph_verification_before_fingerprint == "f" * 64
    assert persisted.graph_verification_after_fingerprint == "1" * 64


def test_campaign_cancellation_is_recorded_and_releases_output_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved = SimpleNamespace(
        output_dir=tmp_path,
        source_config_fingerprint="a" * 64,
        config=SimpleNamespace(models=(), track_modes=()),
    )
    monkeypatch.setattr(
        campaign_runner,
        "prepare_v2_campaign",
        lambda _path: (resolved, object(), {}, "revision", ()),
    )

    async def cancel_campaign(**_kwargs: object) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(
        campaign_runner,
        "_run_prepared_v2_campaign",
        cancel_campaign,
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(campaign_runner.run_v2_campaign(tmp_path / "models.yaml"))

    receipt = campaign_runner.CampaignLifecycleV2.model_validate_json(
        (tmp_path / "campaign-lifecycle-v2.private.json").read_text()
    )
    assert receipt.status == "interrupted"
    assert receipt.interruptions[-1].kind == "task_cancelled"
    with campaign_runner._exclusive_output_dir_lock(tmp_path):
        pass
