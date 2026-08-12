from __future__ import annotations

import asyncio
import inspect
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from ori.eval.v2 import campaign_runner, runtime
from ori.eval.v2.campaign_config import V2ModelEntry
from ori.eval.v2.campaign_runner import _RUNNER_IMPLEMENTATION_SOURCES
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


def test_runner_fingerprint_covers_shared_runtime_contracts() -> None:
    assert {
        "adapter",
        "campaign",
        "campaign_config",
        "campaign_runner",
        "codex_oauth",
        "direct_adapter",
        "evidence",
        "identity",
        "mcp_adapter",
        "mcp_state_machine",
        "model_runtime",
        "provider_loops",
        "runtime",
        "schema",
        "scoring",
    } <= set(_RUNNER_IMPLEMENTATION_SOURCES)


def test_one_reasoning_effort_is_propagated_to_both_campaign_tracks() -> None:
    model = V2ModelEntry(
        name="gpt-test",
        provider="codex",
        model="gpt-test",
        options={"existing": 1},
    )

    assert campaign_runner._provider_options(model, "high") == {
        "existing": 1,
        "reasoning_effort": "high",
    }
    assert model.options == {"existing": 1}
    assert inspect.getsource(campaign_runner._run_model).count(
        "ollama_options=provider_options"
    ) == 2


def test_codex_readiness_requires_and_records_requested_effort(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex" / "models_cache.json").write_text(
        json.dumps(
            {
                "models": [
                    {
                        "slug": "gpt-test",
                        "supported_reasoning_levels": [
                            {"effort": "medium"},
                            {"effort": "high"},
                        ],
                    }
                ]
            }
        )
    )
    monkeypatch.setattr(campaign_runner.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout="Logged in", stderr=""),
    )
    resolved = SimpleNamespace(
        config=SimpleNamespace(
            models=[V2ModelEntry(name="gpt-test", provider="codex", model="gpt-test")],
            defaults=SimpleNamespace(reasoning_effort="high"),
        )
    )

    receipts = campaign_runner._model_readiness(resolved)

    assert receipts[0].reasoning_effort == "high"
    assert receipts[0].capability_check == "codex-model-cache+reasoning-effort"

    resolved.config.defaults.reasoning_effort = "xhigh"
    with pytest.raises(campaign_runner.V2CampaignRunError, match="does not advertise"):
        campaign_runner._model_readiness(resolved)


def test_v11_campaign_schemas_cannot_accept_prior_run_state() -> None:
    provenance_schema = campaign_runner.ModelRunProvenanceV2.model_json_schema()
    state_schema = campaign_runner.PrivateRunStateV2.model_json_schema()
    readiness_schema = campaign_runner.CampaignReadinessV2.model_json_schema()
    runner_source = inspect.getsource(campaign_runner._run_model)

    assert provenance_schema["properties"]["schema_version"]["const"] == (
        "ori-v2-model-campaign-v11"
    )
    assert state_schema["properties"]["schema_version"]["const"] == (
        "ori-v2-private-run-state-v5"
    )
    assert readiness_schema["properties"]["schema_version"]["const"] == (
        "ori-v2-run-readiness-v8"
    )
    assert "run-state-v5.private.json" in runner_source
    assert "run-state-v4.private.json" not in runner_source


def test_no_model_readiness_reuses_one_exact_graph_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    observed = object()
    receipt = object()

    async def fake_health_and_graph(*_args: object) -> tuple[object, object]:
        nonlocal calls
        calls += 1
        return observed, receipt

    monkeypatch.setattr(
        campaign_runner,
        "_health_and_graph",
        fake_health_and_graph,
    )
    first_observed, first_receipt, first_reused = asyncio.run(
        campaign_runner._graph_before_track(
            object(),
            object(),
            object(),
            preflight_only=True,
            shared_preflight=None,
        )
    )
    second_observed, second_receipt, second_reused = asyncio.run(
        campaign_runner._graph_before_track(
            object(),
            object(),
            object(),
            preflight_only=True,
            shared_preflight=(first_observed, first_receipt),
        )
    )

    assert calls == 1
    assert (first_observed, first_receipt, first_reused) == (
        observed,
        receipt,
        False,
    )
    assert (second_observed, second_receipt, second_reused) == (
        observed,
        receipt,
        True,
    )


def test_executable_campaign_never_reuses_graph_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    fresh = (object(), object())

    async def fake_health_and_graph(*_args: object) -> tuple[object, object]:
        nonlocal calls
        calls += 1
        return fresh

    monkeypatch.setattr(
        campaign_runner,
        "_health_and_graph",
        fake_health_and_graph,
    )
    observed, receipt, reused = asyncio.run(
        campaign_runner._graph_before_track(
            object(),
            object(),
            object(),
            preflight_only=False,
            shared_preflight=(object(), object()),
        )
    )

    assert calls == 1
    assert (observed, receipt, reused) == (*fresh, False)


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


def test_direct_runtime_classifies_internal_adapter_error_as_harness_failure() -> None:
    sample = sample_from_direct_outcome(
        TASK,
        ORACLE,
        DirectV2Outcome(
            receipt=_receipt(ExecutionClass.SUCCESS),
            evidence=None,
            verdict=None,
            error="AttributeError: internal projector defect",
            harness_error=True,
        ),
    )

    assert sample.execution_class is ExecutionClass.HARNESS_FAILURE
    assert sample.outcome is SampleOutcomeCode.HARNESS_ERROR
    assert sample.reasoning_correct is None
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
