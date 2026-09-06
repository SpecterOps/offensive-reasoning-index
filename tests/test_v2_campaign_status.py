from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from ori.cli import main
from ori.eval.v2 import campaign_runner, campaign_status
from ori.eval.v2.campaign import (
    CheckpointTaskBinding,
    CheckpointV2,
)
from ori.eval.v2.campaign_status import CampaignStatusError, inspect_v2_campaign_status
from ori.eval.v2.fingerprint import canonical_sha256
from ori.eval.v2.schema import ExecutionClass, Track
from ori.eval.v2.scoring import SampleOutcomeCode, SampleResult
from tests.support.v2_campaign import (
    _D,
    _completed_campaign,
    _config,
    _dump,
    _lifecycle,
    _provenance,
    _provider,
    _readiness,
    _state,
)


def test_not_started_is_read_only_and_does_not_create_output(tmp_path: Path) -> None:
    config = _config(tmp_path)
    output = tmp_path / "campaign"

    status = inspect_v2_campaign_status(config)

    assert status.observed_state == "not_started"
    assert status.next_action == "run_readiness"
    assert output.exists() is False


def test_status_does_not_count_pending_infrastructure_as_terminal() -> None:
    sample = SampleResult(
        task_id="direct-task",
        task_fingerprint="a" * 64,
        oracle_fingerprint="b" * 64,
        execution_class=ExecutionClass.INFRA_FAILURE,
        outcome=SampleOutcomeCode.INFRA_ERROR,
        detail="provider unavailable",
    )
    provider = _provider(sample, Track.DIRECT).model_copy(
        update={
            "provider_metrics": {
                "infra_scope": "provider",
                "infra_retryable": True,
            }
        }
    )
    state = SimpleNamespace(
        checkpoint=SimpleNamespace(results=(sample,)),
        attempts=(
            SimpleNamespace(
                task_id=sample.task_id,
                attempt=1,
                sample=sample,
                provider=provider,
            ),
        ),
    )

    assert campaign_status._run_retry_progress(state, max_infra_retries=2) == (0, 1)


@pytest.mark.parametrize("artifact_name", (".ori-v2-campaign.lock", "partial.tmp"))
def test_evidence_without_lifecycle_fails_closed(
    tmp_path: Path,
    artifact_name: str,
) -> None:
    config = _config(tmp_path)
    output = tmp_path / "campaign"
    output.mkdir()
    (output / artifact_name).touch()

    with pytest.raises(CampaignStatusError, match="without a lifecycle"):
        inspect_v2_campaign_status(config)


def test_non_directory_output_path_fails_closed(tmp_path: Path) -> None:
    config = _config(tmp_path)
    (tmp_path / "campaign").touch()

    with pytest.raises(CampaignStatusError, match="not a directory"):
        inspect_v2_campaign_status(config)


def test_running_uses_actual_lock_contention(tmp_path: Path) -> None:
    config = _config(tmp_path)
    from ori.eval.v2.campaign_config import load_v2_campaign_config

    resolved = load_v2_campaign_config(config)
    resolved.output_dir.mkdir()
    _dump(
        resolved.output_dir / "campaign-lifecycle-v2.private.json",
        _lifecycle(
            resolved.source_config_fingerprint,
            status="running",
            active=(Track.DIRECT, "gpt-test", 1),
        ),
    )

    with campaign_runner._exclusive_output_dir_lock(resolved.output_dir):
        status = inspect_v2_campaign_status(config)

    assert status.observed_state == "running"
    assert status.next_action == "monitor"
    assert status.active_run is not None


def test_running_with_free_lock_is_stale_and_resumable(tmp_path: Path) -> None:
    config = _config(tmp_path)
    from ori.eval.v2.campaign_config import load_v2_campaign_config

    resolved = load_v2_campaign_config(config)
    resolved.output_dir.mkdir()
    (resolved.output_dir / ".ori-v2-campaign.lock").touch()
    _dump(
        resolved.output_dir / "campaign-lifecycle-v2.private.json",
        _lifecycle(resolved.source_config_fingerprint, status="running"),
    )

    status = inspect_v2_campaign_status(config)

    assert status.observed_state == "stale_running"
    assert status.resume_allowed is True
    assert status.next_action == "resume_campaign"


def test_lifecycle_without_persistent_lock_fails_closed(tmp_path: Path) -> None:
    config = _config(tmp_path)
    from ori.eval.v2.campaign_config import load_v2_campaign_config

    resolved = load_v2_campaign_config(config)
    resolved.output_dir.mkdir()
    _dump(
        resolved.output_dir / "campaign-lifecycle-v2.private.json",
        _lifecycle(resolved.source_config_fingerprint, status="running"),
    )
    (resolved.output_dir / ".ori-v2-campaign.lock").unlink()

    with pytest.raises(CampaignStatusError, match="without its persistent campaign lock"):
        inspect_v2_campaign_status(config)


@pytest.mark.parametrize(
    ("lifecycle_status", "observed_state"),
    (("running", "stale_running"), ("interrupted", "interrupted")),
)
def test_interrupted_or_stale_readiness_never_selects_paid_execution(
    tmp_path: Path,
    lifecycle_status: str,
    observed_state: str,
) -> None:
    config = _config(tmp_path)
    from ori.eval.v2.campaign_config import load_v2_campaign_config

    resolved = load_v2_campaign_config(config)
    resolved.output_dir.mkdir()
    if lifecycle_status == "running":
        (resolved.output_dir / ".ori-v2-campaign.lock").touch()
    _dump(
        resolved.output_dir / "campaign-lifecycle-v2.private.json",
        _lifecycle(
            resolved.source_config_fingerprint,
            status=lifecycle_status,
            mode="readiness",
        ),
    )

    status = inspect_v2_campaign_status(config)

    assert status.observed_state == observed_state
    assert status.resume_allowed is True
    assert status.next_action == "run_readiness"


def test_running_projection_allows_atomic_report_before_track_receipt(
    tmp_path: Path,
) -> None:
    config, resolved = _completed_campaign(tmp_path)
    (resolved.output_dir / "direct" / "track-completion-v2.private.json").unlink()
    _dump(
        resolved.output_dir / "campaign-lifecycle-v2.private.json",
        _lifecycle(
            resolved.source_config_fingerprint,
            status="running",
            checkpointed_results=1,
        ),
    )

    status = inspect_v2_campaign_status(config)

    assert status.observed_state == "stale_running"
    assert status.runs[0].report_present is True
    assert status.tracks[0].completion_present is False


def test_interrupted_projection_allows_atomic_report_before_track_receipt(
    tmp_path: Path,
) -> None:
    config, resolved = _completed_campaign(tmp_path)
    (resolved.output_dir / "direct" / "track-completion-v2.private.json").unlink()
    _dump(
        resolved.output_dir / "campaign-lifecycle-v2.private.json",
        _lifecycle(
            resolved.source_config_fingerprint,
            status="interrupted",
            checkpointed_results=1,
        ),
    )

    status = inspect_v2_campaign_status(config)

    assert status.observed_state == "interrupted"
    assert status.next_action == "resume_campaign"
    assert status.runs[0].report_present is True
    assert status.tracks[0].completion_present is False


def test_candidate_subset_checkpoint_may_bind_full_compiled_inventory(
    tmp_path: Path,
) -> None:
    config, resolved = _completed_campaign(tmp_path)
    run_dir = resolved.output_dir / "direct" / "gpt-test" / "run-001"
    provenance = _provenance(Track.DIRECT)
    state = _state(provenance, Track.DIRECT)
    checkpoint_payload = state.checkpoint.model_dump()
    checkpoint_payload["task_bindings"] = (
        *state.checkpoint.task_bindings,
        CheckpointTaskBinding(
            task_id="compiled-but-not-scheduled",
            task_fingerprint="7" * 64,
            oracle_fingerprint="8" * 64,
            bounds_fingerprint="9" * 64,
        ),
    )
    checkpoint_payload["checkpoint_fingerprint"] = canonical_sha256(
        checkpoint_payload,
        exclude_fields=("checkpoint_fingerprint",),
    )
    checkpoint = CheckpointV2.model_validate(checkpoint_payload)
    expanded_state = campaign_runner._state(
        provenance=provenance,
        checkpoint=checkpoint,
        attempts=state.attempts,
    )
    _dump(run_dir / "run-state-v7.private.json", expanded_state)

    status = inspect_v2_campaign_status(config)

    assert status.progress.expected_results == 1
    assert status.progress.checkpointed_results == 1
    assert status.runs[0].expected_tasks == 1


def test_interrupted_campaign_is_resumable(tmp_path: Path) -> None:
    config = _config(tmp_path)
    from ori.eval.v2.campaign_config import load_v2_campaign_config

    resolved = load_v2_campaign_config(config)
    resolved.output_dir.mkdir()
    _dump(
        resolved.output_dir / "campaign-lifecycle-v2.private.json",
        _lifecycle(resolved.source_config_fingerprint, status="interrupted"),
    )

    status = inspect_v2_campaign_status(config)

    assert status.observed_state == "interrupted"
    assert status.next_action == "resume_campaign"


def test_missing_readiness_is_allowed_before_execution_evidence(tmp_path: Path) -> None:
    config = _config(tmp_path)
    from ori.eval.v2.campaign_config import load_v2_campaign_config

    resolved = load_v2_campaign_config(config)
    resolved.output_dir.mkdir()
    _dump(
        resolved.output_dir / "campaign-lifecycle-v2.private.json",
        _lifecycle(resolved.source_config_fingerprint, status="interrupted"),
    )

    status = inspect_v2_campaign_status(config)

    assert status.observed_state == "interrupted"
    assert status.progress.expected_results is None
    assert status.next_action == "resume_campaign"


def test_execution_evidence_without_readiness_fails_closed(tmp_path: Path) -> None:
    config, resolved = _completed_campaign(tmp_path)
    (resolved.output_dir / "v2-run-readiness.private.json").unlink()
    _dump(
        resolved.output_dir / "campaign-lifecycle-v2.private.json",
        _lifecycle(
            resolved.source_config_fingerprint,
            status="interrupted",
            checkpointed_results=1,
        ),
    )

    with pytest.raises(
        CampaignStatusError,
        match="execution evidence exists without campaign readiness",
    ):
        inspect_v2_campaign_status(config)


def test_failed_campaign_requires_investigation_and_is_not_resumable(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    from ori.eval.v2.campaign_config import load_v2_campaign_config

    resolved = load_v2_campaign_config(config)
    resolved.output_dir.mkdir()
    _dump(
        resolved.output_dir / "campaign-lifecycle-v2.private.json",
        _lifecycle(resolved.source_config_fingerprint, status="failed"),
    )

    status = inspect_v2_campaign_status(config)

    assert status.observed_state == "failed"
    assert status.next_action == "investigate_failure"
    assert status.resume_allowed is False


def test_completed_readiness_lifecycle_is_ready_for_execution(tmp_path: Path) -> None:
    config = _config(tmp_path)
    from ori.eval.v2.campaign_config import load_v2_campaign_config

    resolved = load_v2_campaign_config(config)
    resolved.output_dir.mkdir()
    _dump(
        resolved.output_dir / "v2-run-readiness.private.json",
        _readiness(resolved.source_config_fingerprint, (Track.DIRECT,)),
    )
    _dump(
        resolved.output_dir / "campaign-lifecycle-v2.private.json",
        _lifecycle(
            resolved.source_config_fingerprint,
            status="completed",
            mode="readiness",
        ),
    )

    status = inspect_v2_campaign_status(config)

    assert status.observed_state == "readiness_complete"
    assert status.next_action == "execute_campaign"
    assert status.resume_allowed is True


def test_contended_lock_with_non_running_lifecycle_fails_closed(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    from ori.eval.v2.campaign_config import load_v2_campaign_config

    resolved = load_v2_campaign_config(config)
    resolved.output_dir.mkdir()
    _dump(
        resolved.output_dir / "campaign-lifecycle-v2.private.json",
        _lifecycle(resolved.source_config_fingerprint, status="interrupted"),
    )

    with campaign_runner._exclusive_output_dir_lock(resolved.output_dir):
        with pytest.raises(
            CampaignStatusError,
            match="lock is held but lifecycle is not running",
        ):
            inspect_v2_campaign_status(config)


def test_config_fingerprint_mismatch_fails_closed(tmp_path: Path) -> None:
    config = _config(tmp_path)
    output = tmp_path / "campaign"
    output.mkdir()
    _dump(
        output / "campaign-lifecycle-v2.private.json",
        _lifecycle("f" * 64, status="interrupted"),
    )

    with pytest.raises(CampaignStatusError, match="different source config"):
        inspect_v2_campaign_status(config)


@pytest.mark.parametrize(
    "relative",
    (
        "campaign-lifecycle-v2.private.json",
        "direct/gpt-test/run-001/run-state-v7.private.json",
        "direct/gpt-test/run-001/public-report-v2.json",
        "direct/track-completion-v2.private.json",
    ),
)
def test_corrupt_evidence_fails_closed(tmp_path: Path, relative: str) -> None:
    config, resolved = _completed_campaign(tmp_path)
    (resolved.output_dir / relative).write_text("{broken")

    with pytest.raises(CampaignStatusError, match="invalid"):
        inspect_v2_campaign_status(config)


def test_completed_campaign_validates_full_accounting(tmp_path: Path) -> None:
    config, _resolved = _completed_campaign(tmp_path)

    status = inspect_v2_campaign_status(config)

    assert status.observed_state == "completed"
    assert status.next_action == "campaign_complete"
    assert status.progress.expected_results == 1
    assert status.progress.checkpointed_results == 1
    assert status.progress.completed_results == 1
    assert status.progress.provider_attempts == 1
    assert status.progress.tokens_input == 1
    assert status.progress.tokens_output == 1
    assert status.progress.total_tokens == 2
    assert status.tracks[0].total_tokens == 2
    assert status.runs[0].total_tokens == 2
    assert status.runs[0].outcomes[0].outcome == "OUTPUT_INVALID"
    assert status.graph_fingerprint == _D


def test_status_usage_counts_every_durable_provider_attempt(tmp_path: Path) -> None:
    config, resolved = _completed_campaign(tmp_path)
    provenance = _provenance(Track.DIRECT)
    _dump(
        resolved.output_dir / "direct" / "gpt-test" / "run-001" / "run-state-v7.private.json",
        _state(provenance, Track.DIRECT, attempt_count=2),
    )

    status = inspect_v2_campaign_status(config)

    assert status.progress.provider_attempts == 2
    assert status.progress.tokens_input == 2
    assert status.progress.tokens_output == 2
    assert status.progress.total_tokens == 4


def test_completed_invalid_campaign_is_explicitly_nonresumable(tmp_path: Path) -> None:
    config, _resolved = _completed_campaign(tmp_path, campaign_valid=False)

    status = inspect_v2_campaign_status(config)

    assert status.observed_state == "completed"
    assert status.next_action == "campaign_complete_invalid"
    assert status.resume_allowed is False
    assert status.tracks[0].campaign_valid is False
    assert status.runs[0].campaign_valid is False


@pytest.mark.parametrize(
    "relative",
    (
        "direct/gpt-test/run-001/public-report-v2.json",
        "direct/track-completion-v2.private.json",
    ),
)
def test_missing_completion_evidence_fails_closed(tmp_path: Path, relative: str) -> None:
    config, resolved = _completed_campaign(tmp_path)
    (resolved.output_dir / relative).unlink()

    with pytest.raises(CampaignStatusError):
        inspect_v2_campaign_status(config)


def test_direct_and_mcp_accounting_is_separate_and_complete(tmp_path: Path) -> None:
    config, _resolved = _completed_campaign(tmp_path, (Track.DIRECT, Track.MCP))

    status = inspect_v2_campaign_status(config)

    assert status.completed_tracks == (Track.DIRECT, Track.MCP)
    assert status.progress.expected_runs == 2
    assert status.progress.expected_results == 2
    assert status.progress.checkpointed_results == 2
    assert {item.track: item.completed_results for item in status.tracks} == {
        Track.DIRECT: 1,
        Track.MCP: 1,
    }


def test_status_never_calls_campaign_preparation_or_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("status must not call campaign or network preparation")

    monkeypatch.setattr(campaign_runner, "prepare_v2_campaign", forbidden)
    monkeypatch.setattr(campaign_runner, "BHCEClient", forbidden)
    monkeypatch.setattr(campaign_runner, "resolve_mcp_launcher_runtime", forbidden)

    assert inspect_v2_campaign_status(config).observed_state == "not_started"


def test_status_does_not_modify_existing_files(tmp_path: Path) -> None:
    config, resolved = _completed_campaign(tmp_path)
    before = {
        path.relative_to(resolved.output_dir): (path.stat().st_mtime_ns, path.read_bytes())
        for path in resolved.output_dir.rglob("*")
        if path.is_file()
    }

    inspect_v2_campaign_status(config)

    after = {
        path.relative_to(resolved.output_dir): (path.stat().st_mtime_ns, path.read_bytes())
        for path in resolved.output_dir.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_serialized_status_omits_private_runtime_data(tmp_path: Path) -> None:
    config, resolved = _completed_campaign(tmp_path)

    serialized = inspect_v2_campaign_status(config).model_dump_json()

    forbidden_values = (
        "SECRET_PROMPT",
        "SECRET_PROVIDER_RESPONSE",
        "SECRET_TOOL_ARGUMENT",
        "SECRET_CREDENTIAL_VALUE",
        "/private/operator/campaign",
        str(config),
        str(resolved.output_dir),
    )
    forbidden_fields = (
        '"raw_response"',
        '"mcp_transcript"',
        '"mcp_tool_receipts"',
        '"credential_source"',
        '"detail"',
    )
    assert all(value not in serialized for value in forbidden_values)
    assert all(field not in serialized for field in forbidden_fields)


def test_campaign_status_cli_supports_human_and_json_output(tmp_path: Path) -> None:
    config = _config(tmp_path)
    runner = CliRunner()

    human = runner.invoke(main, ["campaign-status", "--config", str(config)])
    machine = runner.invoke(
        main,
        ["campaign-status", "--config", str(config), "--json"],
    )

    assert human.exit_code == 0
    assert "V2 CAMPAIGN: NOT_STARTED" in human.output
    assert "Next action: run readiness" in human.output
    assert "Usage: 0 total tokens (0 input + 0 output) across 0 provider attempts" in human.output
    assert machine.exit_code == 0
    assert '"schema_version": "ori-v2-campaign-status-v2"' in machine.output


@pytest.mark.parametrize("failure", ("corrupt", "incompatible"))
def test_campaign_status_cli_exits_nonzero_on_untrusted_evidence(
    tmp_path: Path,
    failure: str,
) -> None:
    config = _config(tmp_path)
    from ori.eval.v2.campaign_config import load_v2_campaign_config

    resolved = load_v2_campaign_config(config)
    resolved.output_dir.mkdir()
    lifecycle_path = resolved.output_dir / "campaign-lifecycle-v2.private.json"
    if failure == "corrupt":
        lifecycle_path.write_text("{broken")
    else:
        _dump(lifecycle_path, _lifecycle("f" * 64, status="interrupted"))

    result = CliRunner().invoke(
        main,
        ["campaign-status", "--config", str(config), "--json"],
    )

    assert result.exit_code != 0
    assert "Error:" in result.output
    assert "ori-v2-campaign-status-v2" not in result.output
