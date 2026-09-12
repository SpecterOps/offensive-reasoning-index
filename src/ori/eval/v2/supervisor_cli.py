"""Public result envelope helpers for the installed V2 supervisor command."""

from __future__ import annotations

from typing import Any

from .campaign_supervisor import SupervisorCampaignBindingV1, SupervisorDecision

SUPERVISOR_RESULT_SCHEMA_VERSION = "ori-v2-supervisor-result-v1"
SUPERVISOR_ERROR_CODE = "SUPERVISOR_FAILED"
SUPERVISOR_ERROR_MESSAGE = "supervisor stopped; inspect local supervisor state and campaign status"


def supervisor_exit_code(decision: SupervisorDecision) -> int:
    """Map an already-made supervisor decision to its stable CLI exit status."""

    if decision.action == "await_execution_approval":
        return 2
    if decision.action == "stop":
        return 1
    return 0


def supervisor_result_payload(
    decision: SupervisorDecision, *, execute_approved: bool
) -> dict[str, Any]:
    """Build a path-free, secret-free machine result from the real supervisor."""

    payload: dict[str, Any] = {
        "schema_version": SUPERVISOR_RESULT_SCHEMA_VERSION,
        "outcome": "decision",
        "execute_approved": execute_approved,
        "decision": {
            "action": decision.action,
            "reason": decision.reason,
        },
        "exit_code": supervisor_exit_code(decision),
    }
    if decision.campaign is not None:
        payload["campaign"] = _campaign_payload(decision.campaign)
    return payload


def _campaign_payload(campaign: SupervisorCampaignBindingV1) -> dict[str, Any]:
    """Project immutable purpose data without config paths or private inputs."""

    return {
        "purpose": campaign.purpose,
        "ranking_eligible": campaign.purpose == "official",
        "canary": (
            None
            if campaign.purpose == "official"
            else {
                "suite": campaign.canary_suite,
                "selection_fingerprint": campaign.canary_selection_fingerprint,
            }
        ),
    }


def supervisor_error_payload() -> dict[str, Any]:
    """Build a stable, path-free error result for ``ori supervise-v2 --json``."""

    return {
        "schema_version": SUPERVISOR_RESULT_SCHEMA_VERSION,
        "outcome": "error",
        "error": {
            "code": SUPERVISOR_ERROR_CODE,
            "message": SUPERVISOR_ERROR_MESSAGE,
        },
        "exit_code": 1,
    }
