"""Regression coverage for the offline installed integration contract."""

from __future__ import annotations

import json
import re

from click.testing import CliRunner

from ori.cli import main
from ori.eval.v2.integration_info import (
    INTEGRATION_CONTRACT_VERSION,
    INTEGRATION_INFO_SCHEMA_VERSION,
)


def test_integration_info_json_is_offline_secret_free_and_contract_bound(
    monkeypatch,
) -> None:
    monkeypatch.setenv("BLOODHOUND_TOKEN_KEY", "integration-info-must-not-leak")

    result = CliRunner().invoke(main, ["integration-info", "--json"])

    assert result.exit_code == 0, result.output
    assert "integration-info-must-not-leak" not in result.output
    payload = json.loads(result.output)
    assert payload["schema_version"] == INTEGRATION_INFO_SCHEMA_VERSION
    assert payload["integration_contract_version"] == INTEGRATION_CONTRACT_VERSION
    assert payload["offline"] is True
    assert payload["build"]["wheel_digest"] is None
    assert payload["build"]["wheel_digest_status"] == "requires_external_build_receipt"
    assert re.fullmatch(r"[0-9a-f]{64}", payload["build"]["installed_source_fingerprint"])
    assert payload["v2"]["protocol_version"] == "ori-eval-protocol-v2"
    assert payload["v2"]["schemas"]["campaign_status"] == "ori-v2-campaign-status-v3"
    assert payload["v2"]["schemas"]["supervisor_state"] == "ori-v2-campaign-supervisor-state-v2"
    expected_capabilities = {
        "generate_json": True,
        "inspect_artifacts_v2": True,
        "fingerprint_native_runtime": True,
        "discover_native_profile": True,
        "check_native_feasibility": True,
        "prepare_native_config_v2": True,
        "qualify_native_mcp": True,
        "inspect_canary_v2": True,
        "compile_v2": True,
        "certify_v2_live": True,
        "select_v2": True,
        "run_v2": True,
        "campaign_status_json": True,
        "supervise_v2": True,
        "supervise_v2_json": True,
        "prepare_canary_v2": True,
        "run_canary_v2": True,
        "diagnostic_canary": True,
        "diagnostic_canary_suite": "native-five-kind-v1",
        "export_campaign_v2_public": True,
        "validated_public_export": True,
    }
    assert payload["capabilities"].items() >= expected_capabilities.items()
    assert [item["implementation_id"] for item in payload["native_implementation_registry"]] == [
        "mwnickerson",
        "mordavid",
        "armadin",
    ]
    assert all(
        re.fullmatch(r"[0-9a-f]{40}", item["revision"])
        for item in payload["native_implementation_registry"]
    )


def test_integration_info_has_a_human_readable_form() -> None:
    result = CliRunner().invoke(main, ["integration-info"])

    assert result.exit_code == 0, result.output
    assert result.output.startswith("ORI INTEGRATION INFO\n")
    assert "Diagnostic canary: supported" in result.output
    assert "Validated public export: supported" in result.output
    assert not result.output.lstrip().startswith("{")
