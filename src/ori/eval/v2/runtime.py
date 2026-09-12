"""Protocol-v2 runtime dispatch with no legacy scoring fallback."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Any

from .direct_adapter import DirectV2Outcome, execute_direct_v2
from .mcp import EvidenceEvent, MCPToolLoop
from .mcp_adapter import MCPV2Outcome, score_mcp_transcript_v2
from .native_capability import NativeCapabilityProfile
from .schema import (
    CapabilityProfile,
    ExecutionClass,
    GraphFactRegistry,
    OracleBundle,
    TaskBundle,
    TaskCertification,
    VerdictStatus,
)
from .scoring import SampleOutcomeCode, SampleResult


class V2RuntimeSurface(StrEnum):
    DIRECT = "direct"
    MCP_NATIVE_OLLAMA = "mcp-native-ollama"
    MCP_NATIVE_OPENAI_COMPATIBLE = "mcp-native-openai-compatible"
    MCP_NATIVE_ANTHROPIC = "mcp-native-anthropic"
    INSPECT = "inspect"
    OFFLINE = "offline"


_DIRECT_MODEL_OUTCOMES = {
    "policy_rejected": SampleOutcomeCode.POLICY_REJECTED,
    "query_timeout": SampleOutcomeCode.QUERY_TIMEOUT,
    "query_error": SampleOutcomeCode.QUERY_ERROR,
}


def sample_from_direct_outcome(
    task: TaskBundle,
    oracle: OracleBundle,
    outcome: DirectV2Outcome,
) -> SampleResult:
    """Map authoritative direct execution into exact v2 accounting."""

    base = {
        "task_id": task.task_id,
        "task_fingerprint": task.task_fingerprint,
        "oracle_fingerprint": oracle.oracle_fingerprint,
    }
    receipt = outcome.receipt
    if outcome.harness_error:
        return SampleResult(
            **base,
            execution_class=ExecutionClass.HARNESS_FAILURE,
            outcome=SampleOutcomeCode.HARNESS_ERROR,
            detail=outcome.error,
        )
    if outcome.proof_insufficient:
        return SampleResult(
            **base,
            execution_class=ExecutionClass.PROOF_FAILURE,
            outcome=SampleOutcomeCode.PROOF_INSUFFICIENT,
            output_compliant=True,
            detail=outcome.error,
        )
    if (
        receipt.execution_class is ExecutionClass.SUCCESS
        and outcome.evidence is not None
        and outcome.verdict is not None
    ):
        return SampleResult(
            **base,
            execution_class=ExecutionClass.SUCCESS,
            outcome=SampleOutcomeCode.COMPLETED,
            reasoning_correct=outcome.verdict.status is VerdictStatus.CORRECT,
            output_compliant=True,
            evidence=outcome.evidence,
            verdict=outcome.verdict,
        )
    if receipt.execution_class is ExecutionClass.MODEL_FAILURE:
        return SampleResult(
            **base,
            execution_class=ExecutionClass.MODEL_FAILURE,
            outcome=_DIRECT_MODEL_OUTCOMES.get(
                receipt.failure_type or "",
                SampleOutcomeCode.QUERY_ERROR,
            ),
            output_compliant=True,
            detail=outcome.error,
        )
    if receipt.execution_class is ExecutionClass.UNEXECUTED:
        return SampleResult(
            **base,
            execution_class=ExecutionClass.UNEXECUTED,
            outcome=SampleOutcomeCode.CIRCUIT_OPEN,
            detail=outcome.error,
        )
    if receipt.execution_class is ExecutionClass.INFRA_FAILURE:
        return SampleResult(
            **base,
            execution_class=ExecutionClass.INFRA_FAILURE,
            outcome=SampleOutcomeCode.INFRA_ERROR,
            detail=outcome.error,
        )
    if receipt.execution_class is ExecutionClass.SUCCESS:
        # The query executed, but its output could not satisfy the public
        # evidence schema (for example, nodes without ordered edge witnesses).
        return SampleResult(
            **base,
            execution_class=ExecutionClass.MODEL_FAILURE,
            outcome=SampleOutcomeCode.OUTPUT_INVALID,
            output_compliant=True,
            detail=outcome.error,
        )
    return SampleResult(
        **base,
        execution_class=ExecutionClass.HARNESS_FAILURE,
        outcome=SampleOutcomeCode.HARNESS_ERROR,
        detail=outcome.error,
    )


async def run_direct_task_v2(
    coordinator: Any,
    *,
    query: str,
    task: TaskBundle,
    oracle: OracleBundle,
    resolver: Any,
    answer_payload: Mapping[str, Any] | None = None,
) -> tuple[DirectV2Outcome, SampleResult]:
    """Run one direct task only through the containment coordinator."""

    outcome = await execute_direct_v2(
        coordinator,
        query=query,
        task=task,
        oracle=oracle,
        resolver=resolver,
        answer_payload=answer_payload,
    )
    return outcome, sample_from_direct_outcome(task, oracle, outcome)


def run_mcp_task_v2(
    *,
    surface: V2RuntimeSurface,
    task: TaskBundle,
    oracle: OracleBundle,
    resolver: Any,
    profile: CapabilityProfile | NativeCapabilityProfile,
    events: Sequence[EvidenceEvent],
    final_answer: Mapping[str, Any] | None,
    retry_answer: Mapping[str, Any] | None = None,
    observed_identity_ids: Sequence[str] = (),
    graph_fact_registry: GraphFactRegistry | None = None,
    final_output_normalized: bool = False,
    retry_output_normalized: bool = False,
    final_output_compliant: bool | None = None,
    retry_output_compliant: bool | None = None,
    final_receipt_attested: bool = True,
    retry_receipt_attested: bool = True,
    retry_contract_error: str | None = None,
    terminal_protocol_output_invalid: bool = False,
    certified: bool = True,
    native_certification: TaskCertification | None = None,
) -> MCPV2Outcome:
    """Dispatch every MCP/Inspect loop through the common finalizer."""

    loop_by_surface = {
        V2RuntimeSurface.MCP_NATIVE_OLLAMA: MCPToolLoop.NATIVE_OLLAMA,
        V2RuntimeSurface.MCP_NATIVE_OPENAI_COMPATIBLE: (
            MCPToolLoop.NATIVE_OPENAI_COMPATIBLE
        ),
        V2RuntimeSurface.INSPECT: MCPToolLoop.INSPECT,
        V2RuntimeSurface.MCP_NATIVE_ANTHROPIC: MCPToolLoop.NATIVE_ANTHROPIC,
    }
    try:
        tool_loop = loop_by_surface[surface]
    except KeyError as exc:
        raise ValueError(f"{surface.value} is not an MCP runtime surface") from exc
    return score_mcp_transcript_v2(
        task=task,
        oracle=oracle,
        resolver=resolver,
        profile=profile,
        tool_loop=tool_loop,
        events=events,
        final_answer=final_answer,
        retry_answer=retry_answer,
        observed_identity_ids=observed_identity_ids,
        graph_fact_registry=graph_fact_registry,
        final_output_normalized=final_output_normalized,
        retry_output_normalized=retry_output_normalized,
        final_output_compliant=final_output_compliant,
        retry_output_compliant=retry_output_compliant,
        final_receipt_attested=final_receipt_attested,
        retry_receipt_attested=retry_receipt_attested,
        retry_contract_error=retry_contract_error,
        terminal_protocol_output_invalid=terminal_protocol_output_invalid,
        certified=certified,
        native_certification=native_certification,
    )
