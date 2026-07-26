from __future__ import annotations

import inspect

import pytest

from ori.eval.v2 import runtime
from ori.eval.v2.direct_adapter import DirectV2Outcome
from ori.eval.v2.mcp import EvidenceEventKind, classify_evidence_event
from ori.eval.v2.runtime import (
    V2RuntimeSurface,
    run_mcp_task_v2,
    sample_from_direct_outcome,
)
from ori.eval.v2.schema import (
    CircuitState,
    DirectExecutionReceipt,
    ExecutionClass,
    HealthState,
)
from ori.eval.v2.scoring import SampleOutcomeCode

from .test_v2_mcp_adapter import (
    ORACLE,
    PROFILE,
    RESOLVER,
    TASK,
    _answer,
)


def _receipt(
    execution_class: ExecutionClass,
    *,
    failure_type: str | None = None,
) -> DirectExecutionReceipt:
    return DirectExecutionReceipt(
        execution_class=execution_class,
        failure_type=failure_type,
        query_executed=execution_class is not ExecutionClass.UNEXECUTED,
        attempts=0 if execution_class is ExecutionClass.UNEXECUTED else 1,
        policy_version="3",
        elapsed_seconds=0.01,
        post_query_health=HealthState.NOT_CHECKED,
        circuit_state=(
            CircuitState.OPEN
            if execution_class is ExecutionClass.UNEXECUTED
            else CircuitState.CLOSED
        ),
    )


@pytest.mark.parametrize(
    ("execution_class", "failure_type", "outcome_code", "reasoning"),
    [
        (
            ExecutionClass.MODEL_FAILURE,
            "policy_rejected",
            SampleOutcomeCode.POLICY_REJECTED,
            False,
        ),
        (
            ExecutionClass.MODEL_FAILURE,
            "query_timeout",
            SampleOutcomeCode.QUERY_TIMEOUT,
            False,
        ),
        (
            ExecutionClass.INFRA_FAILURE,
            "transport_error",
            SampleOutcomeCode.INFRA_ERROR,
            None,
        ),
        (
            ExecutionClass.UNEXECUTED,
            "circuit_open",
            SampleOutcomeCode.CIRCUIT_OPEN,
            None,
        ),
    ],
)
def test_direct_runtime_preserves_execution_reasoning_separation(
    execution_class: ExecutionClass,
    failure_type: str,
    outcome_code: SampleOutcomeCode,
    reasoning: bool | None,
) -> None:
    sample = sample_from_direct_outcome(
        TASK,
        ORACLE,
        DirectV2Outcome(
            receipt=_receipt(execution_class, failure_type=failure_type),
            evidence=None,
            verdict=None,
            error=failure_type,
        ),
    )

    assert sample.execution_class is execution_class
    assert sample.outcome is outcome_code
    assert sample.reasoning_correct is reasoning
    assert sample.verdict is None


@pytest.mark.parametrize(
    "surface",
    [
        V2RuntimeSurface.MCP_NATIVE_OLLAMA,
        V2RuntimeSurface.MCP_NATIVE_OPENAI_COMPATIBLE,
        V2RuntimeSurface.INSPECT,
    ],
)
def test_every_mcp_surface_uses_the_common_finalizer(
    surface: V2RuntimeSurface,
) -> None:
    event = classify_evidence_event(
        TASK,
        PROFILE,
        kind=EvidenceEventKind.USEFUL_POSITIVE,
        tool_name="cypher_query",
        operation="run",
    )

    outcome = run_mcp_task_v2(
        surface=surface,
        task=TASK,
        oracle=ORACLE,
        resolver=RESOLVER,
        profile=PROFILE,
        events=(event,),
        final_answer=_answer(),
    )

    assert outcome.sample.execution_class is ExecutionClass.SUCCESS
    assert outcome.sample.reasoning_correct is True


def test_v2_runtime_has_no_legacy_grader_fallback() -> None:
    source = inspect.getsource(runtime)

    assert "grade(" not in source
    assert "grade_mcp_diagnostic" not in source
    assert "answer.correct" not in source
    assert "template_id" not in source
