"""Offline, public integration metadata for external ORI controllers.

The integration contract deliberately reports only packaged, deterministic
metadata.  It does not inspect credentials, model providers, MCP servers, a
live graph, or a source checkout.  Consumers must bind this installed-source
identity to a separate build receipt when they need wheel provenance.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import Any

from .campaign import PUBLIC_REPORT_SCHEMA_VERSION
from .campaign_export import PUBLIC_EXPORT_SCHEMA_VERSION
from .campaign_runner import (
    CAMPAIGN_LIFECYCLE_SCHEMA_VERSION,
    MODEL_REPORT_SCHEMA_VERSION,
    READINESS_SCHEMA_VERSION,
    RUN_STATE_SCHEMA_VERSION,
    RUNNER_VERSION,
    TRACK_COMPLETION_SCHEMA_VERSION,
)
from .campaign_status import CAMPAIGN_STATUS_SCHEMA_VERSION
from .campaign_supervisor import SUPERVISOR_STATE_SCHEMA_VERSION
from .diagnostic_result import DIAGNOSTIC_RESULT_SCHEMA_VERSION
from .diagnostic_selection import CANARY_SUITE
from .native_capability import native_implementation_fingerprint
from .native_mcp_profiles import get_native_implementation
from .protocol import ORACLE_ARTIFACT_VERSION
from .schema import MANIFEST_SCHEMA_VERSION, PROTOCOL_VERSION

INTEGRATION_INFO_SCHEMA_VERSION = "ori-integration-info-v1"
INTEGRATION_CONTRACT_VERSION = "ori-hermes-integration-v1"
_DISTRIBUTION_NAME = "offensive-reasoning-index"
_NATIVE_IMPLEMENTATION_IDS = ("mwnickerson", "mordavid", "armadin")


def _distribution_version() -> str:
    """Return installed package metadata without inspecting a source checkout."""

    try:
        return version(_DISTRIBUTION_NAME)
    except PackageNotFoundError:
        # This fallback makes direct source-tree invocation explicit rather than
        # pretending it is an installed wheel.  The console script in a wheel
        # always has distribution metadata.
        return "uninstalled-source-tree"


def integration_info_payload() -> dict[str, Any]:
    """Return the versioned, secret-free external integration contract.

    ``installed_source_fingerprint`` is calculated from all packaged ORI
    Python source.  It is deliberately not a Git revision: a checkout commit
    does not authenticate an installed wheel.  A caller that requires wheel
    provenance must bind this value to its own wheel-digest build receipt.
    """

    native_implementations = []
    for implementation_id in _NATIVE_IMPLEMENTATION_IDS:
        implementation = get_native_implementation(implementation_id)
        native_implementations.append(
            {
                "implementation_id": implementation.implementation_id,
                "repository": implementation.repository,
                "revision": implementation.revision,
                "backend": implementation.backend,
            }
        )

    return {
        "schema_version": INTEGRATION_INFO_SCHEMA_VERSION,
        "integration_contract_version": INTEGRATION_CONTRACT_VERSION,
        "offline": True,
        "build": {
            "distribution": _DISTRIBUTION_NAME,
            "distribution_version": _distribution_version(),
            "installed_source_fingerprint": native_implementation_fingerprint(),
            "wheel_digest": None,
            "wheel_digest_status": "requires_external_build_receipt",
        },
        "v2": {
            "protocol_version": PROTOCOL_VERSION,
            "runner_version": RUNNER_VERSION,
            "schemas": {
                "manifest": MANIFEST_SCHEMA_VERSION,
                "oracles": ORACLE_ARTIFACT_VERSION,
                "campaign_status": CAMPAIGN_STATUS_SCHEMA_VERSION,
                "supervisor_state": SUPERVISOR_STATE_SCHEMA_VERSION,
                "readiness": READINESS_SCHEMA_VERSION,
                "campaign_lifecycle": CAMPAIGN_LIFECYCLE_SCHEMA_VERSION,
                "private_run_state": RUN_STATE_SCHEMA_VERSION,
                "model_report": MODEL_REPORT_SCHEMA_VERSION,
                "track_completion": TRACK_COMPLETION_SCHEMA_VERSION,
                "public_report": PUBLIC_REPORT_SCHEMA_VERSION,
                "diagnostic_selection": "ori-diagnostic-selection-v1",
                "public_export": PUBLIC_EXPORT_SCHEMA_VERSION,
                "diagnostic_result": DIAGNOSTIC_RESULT_SCHEMA_VERSION,
                "artifact_index": "ori-v2-artifact-index-v1",
                "native_runtime": "ori-native-runtime-fingerprint-v1",
                "native_discovery": "ori-native-profile-discovery-v1",
                "native_feasibility": "ori-native-feasibility-v1",
                "native_qualification": "ori-native-qualification-v1",
            },
        },
        "capabilities": {
            "generate_json": True,
            "inspect_artifacts_v2": True,
            "prepare_native_config_v2": True,
            "fingerprint_native_runtime": True,
            "discover_native_profile": True,
            "check_native_feasibility": True,
            "qualify_native_mcp": True,
            "inspect_canary_v2": True,
            "compile_v2": True,
            "compile_v2_json": True,
            "certify_v2_live": True,
            "certify_v2_live_json": True,
            "select_v2": True,
            "select_v2_json": True,
            "run_v2": True,
            "run_v2_json": True,
            "campaign_status_json": True,
            "supervise_v2": True,
            "supervise_v2_json": True,
            "prepare_canary_v2": True,
            "run_canary_v2": True,
            "diagnostic_canary": True,
            "diagnostic_canary_suite": CANARY_SUITE,
            "export_campaign_v2_public": True,
            "validated_public_export": True,
        },
        "native_implementation_registry": native_implementations,
    }
