"""Strict bounded diagnostic consumer contract and safe installed command failures."""

import json

import pytest
from click.testing import CliRunner

from ori.cli import main
from ori.eval.v2.campaign_runner import RunOperationalMetricsV2
from ori.eval.v2.diagnostic_result import DiagnosticKindOutcomeV1, DiagnosticResultV1
from ori.eval.v2.diagnostic_selection import CANARY_KINDS
from ori.eval.v2.fingerprint import canonical_sha256
from ori.eval.v2.scoring import SampleOutcomeCode


def metrics():
    return RunOperationalMetricsV2(
        resource_mode="native",
        attempts_total=5,
        retries_total=0,
        immediate_retries_total=0,
        deferred_retries_total=0,
        recovered_infrastructure_tasks=0,
        exhausted_infrastructure_tasks=0,
        completed_recovery_rounds=0,
        tokens_input_total=10,
        tokens_output_total=5,
        total_tokens_total=15,
        elapsed_seconds_total=1.0,
        mcp_tool_calls_total=5,
        cypher_query_calls_total=5,
        non_cypher_tool_calls_total=0,
        failed_tool_calls_total=0,
        resource_read_calls_total=0,
    )


def document(outcome):
    payload = {
        "schema_version": "ori-v2-diagnostic-result-v1",
        "purpose": "diagnostic_canary",
        "ranking_eligible": False,
        "suite": "native-five-kind-v1",
        **{
            key: "a" * 64
            for key in (
                "source_config_fingerprint",
                "selection_fingerprint",
                "schedule_fingerprint",
                "completion_fingerprint",
                "report_fingerprint",
                "graph_fingerprint",
            )
        },
        "graph_valid": True,
        "campaign_valid": True,
        "usage": metrics(),
        "outcomes": tuple(
            DiagnosticKindOutcomeV1(
                kind=kind,
                outcome=outcome,
                reasoning_correct=(False if outcome is SampleOutcomeCode.COMPLETED else None),
                output_compliant=True if outcome is SampleOutcomeCode.COMPLETED else None,
            )
            for kind in CANARY_KINDS
        ),
    }
    return DiagnosticResultV1.model_validate(
        {**payload, "result_fingerprint": canonical_sha256(payload)}
    )


@pytest.mark.parametrize("outcome", list(SampleOutcomeCode))
def test_all_frozen_outcomes_are_explicit_without_task_identity(outcome):
    result = document(outcome)
    encoded = result.model_dump_json()
    assert DiagnosticResultV1.model_validate_json(encoded) == result
    assert len(result.outcomes) == 5
    assert result.ranking_eligible is False
    assert "task_id" not in encoded and "query" not in encoded.replace(
        "cypher_query_calls_total", ""
    )
    with pytest.raises(ValueError):
        DiagnosticResultV1.model_validate_json(encoded.replace(outcome.value, "UNKNOWN_OUTCOME"))


@pytest.mark.parametrize(
    "key,value", [("ranking_eligible", True), ("graph_valid", False), ("purpose", "official")]
)
def test_diagnostic_projection_cannot_claim_official_or_invalid_completion(key, value):
    raw = document(SampleOutcomeCode.COMPLETED).model_dump(mode="json")
    raw[key] = value
    raw["result_fingerprint"] = canonical_sha256(raw, exclude_fields=("result_fingerprint",))
    with pytest.raises(ValueError):
        DiagnosticResultV1.model_validate_json(json.dumps(raw))


def test_duplicate_or_missing_kind_and_extra_private_fields_are_rejected():
    raw = document(SampleOutcomeCode.COMPLETED).model_dump(mode="json")
    for outcomes in (raw["outcomes"][:-1], [raw["outcomes"][0]] * 5):
        altered = {**raw, "outcomes": outcomes}
        altered["result_fingerprint"] = canonical_sha256(
            altered, exclude_fields=("result_fingerprint",)
        )
        with pytest.raises(ValueError):
            DiagnosticResultV1.model_validate_json(json.dumps(altered))
    raw["private_path"] = "/private/never-export"
    with pytest.raises(ValueError):
        DiagnosticResultV1.model_validate_json(json.dumps(raw))


def test_inspector_cli_redacts_failure_and_performs_no_provider_calls(tmp_path, monkeypatch):
    path = tmp_path / "private-config.yaml"
    path.write_text("secret-invalid-value")
    calls = []

    async def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError("provider called")

    monkeypatch.setattr("ori.eval.adapter.call_provider_text", forbidden)
    result = CliRunner().invoke(main, ["inspect-canary-v2", "--config", str(path), "--json"])
    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["code"] == "DIAGNOSTIC_INSPECTION_FAILED"
    assert "secret-invalid-value" not in result.output and str(tmp_path) not in result.output
    assert calls == []
