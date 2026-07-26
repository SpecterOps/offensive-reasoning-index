from __future__ import annotations

import asyncio
import inspect
import json
from types import SimpleNamespace
from typing import Any

import pytest
from inspect_ai.tool import tool

from ori.eval.adapter import ModelResponse
from ori.eval.bhce import CypherResult
from ori.eval.mcp_runtime import (
    MCPServerBundle,
    _ollama_tool_spec,
    _tool_result_to_text,
    _wrap_read_only_tool,
)
from ori.eval.v2 import campaign_runner, model_runtime
from ori.eval.v2.mcp import (
    EvidenceEventKind,
    FinalizationPhase,
    MCPToolLoop,
)
from ori.eval.v2.model_runtime import (
    MCPTranscriptProjector,
    V2ModelRuntimeError,
    direct_system_prompt,
    parse_direct_submission,
    run_direct_model_task_v2,
    run_mcp_model_task_v2,
)
from ori.eval.v2.schema import (
    ExactSetPolicy,
    ExecutionClass,
    Track,
)
from ori.eval.v2.scoring import SampleOutcomeCode, SampleResult

from .test_v2_direct_adapter import (
    ORACLE as DIRECT_ORACLE,
)
from .test_v2_direct_adapter import (
    RESOLVER as DIRECT_RESOLVER,
)
from .test_v2_direct_adapter import SET_TASK as DIRECT_SET_TASK
from .test_v2_direct_adapter import (
    TASK as DIRECT_TASK,
)
from .test_v2_direct_adapter import (
    FakeCoordinator,
    _raw_route,
)
from .test_v2_mcp_adapter import (
    ORACLE as MCP_ORACLE,
)
from .test_v2_mcp_adapter import (
    PROFILE,
    _answer,
)
from .test_v2_mcp_adapter import (
    RESOLVER as MCP_RESOLVER,
)
from .test_v2_mcp_adapter import (
    TASK as MCP_TASK,
)


def _response(text: str, *, error: str | None = None) -> ModelResponse:
    return ModelResponse(
        raw_text=text,
        cypher=None,
        parse_stage="raw_text" if text else "none",
        tokens_input=10,
        tokens_output=5,
        elapsed_seconds=0.01,
        model="codex/gpt-test",
        error=error,
    )


def test_direct_public_request_includes_bounds_without_oracle_material() -> None:
    prompt = direct_system_prompt(DIRECT_TASK)

    assert '"track": "direct"' in prompt
    assert '"max_hops": 1' in prompt
    assert DIRECT_ORACLE.oracle_id not in prompt
    assert "route_variants" not in prompt
    assert "reference_cypher" not in prompt


def test_direct_prompt_preserves_v1_cysql_contract_and_separates_answer_schema() -> None:
    route_prompt = direct_system_prompt(DIRECT_TASK)
    set_prompt = direct_system_prompt(DIRECT_SET_TASK)

    assert "Use RETURN p for path queries" in route_prompt
    assert "list comprehensions" in route_prompt
    assert "after the final RETURN projection" in route_prompt
    assert '"version": "ori-direct-result-contract-v1"' in route_prompt
    assert "does not need to match the task answer_schema" in route_prompt
    assert "For set queries return only the answer nodes" in set_prompt
    assert "RETURN entity ORDER BY entity.objectid" in set_prompt


def test_direct_submission_schema_rejects_extra_oracle_fields() -> None:
    valid = json.dumps(
        {
            "query": "MATCH p=(a)-[:MemberOf]->(b) RETURN p LIMIT 1",
            "assertion": {},
        }
    )
    assert parse_direct_submission(valid, DIRECT_TASK).query.startswith("MATCH")

    with pytest.raises(V2ModelRuntimeError, match="schema mismatch"):
        parse_direct_submission(
            json.dumps(
                {
                    "query": "MATCH (n) RETURN n LIMIT 1",
                    "assertion": {},
                    "reference_results": ["secret"],
                }
            ),
            DIRECT_TASK,
        )


def test_direct_model_runtime_executes_exactly_once_through_coordinator() -> None:
    query = "MATCH p=(a)-[:MemberOf]->(b) RETURN p LIMIT 1"

    async def transport(**_kwargs: Any) -> ModelResponse:
        return _response(json.dumps({"query": query, "assertion": {}}))

    coordinator = FakeCoordinator(
        CypherResult(
            success=True,
            raw=_raw_route(),
            status_code=200,
            query_executed=True,
            execution_attempts=1,
            query_fingerprint="b" * 64,
            safety_policy_version="3",
            safety_rule="allowed",
            bhce_health_after="not_checked",
            circuit_state="closed",
        )
    )
    outcome, sample, record = asyncio.run(
        run_direct_model_task_v2(
            coordinator=coordinator,
            task=DIRECT_TASK,
            oracle=DIRECT_ORACLE,
            resolver=DIRECT_RESOLVER,
            model="codex/gpt-test",
            transport=transport,
        )
    )

    assert coordinator.queries == [query]
    assert outcome is not None
    assert sample.execution_class is ExecutionClass.SUCCESS
    assert sample.reasoning_correct is True
    assert record.direct_query_digest is not None
    assert record.direct_receipt is not None


def test_invalid_direct_output_never_reaches_bloodhound() -> None:
    async def transport(**_kwargs: Any) -> ModelResponse:
        return _response('{"query": "MATCH (n) RETURN n LIMIT 1"}')

    coordinator = FakeCoordinator(CypherResult(success=True, raw={}))
    outcome, sample, record = asyncio.run(
        run_direct_model_task_v2(
            coordinator=coordinator,
            task=DIRECT_TASK,
            oracle=DIRECT_ORACLE,
            resolver=DIRECT_RESOLVER,
            model="codex/gpt-test",
            transport=transport,
        )
    )

    assert outcome is None
    assert coordinator.queries == []
    assert sample.outcome is SampleOutcomeCode.OUTPUT_INVALID
    assert sample.execution_class is ExecutionClass.MODEL_FAILURE
    assert record.direct_receipt is None


def test_shortest_path_wrapper_is_positive_evidence_not_empty() -> None:
    projector = MCPTranscriptProjector(MCP_TASK, PROFILE)
    ready = projector.observe(
        "graph_analysis",
        {"info_type": "shortest_path"},
        json.dumps(
            {
                "info_type": "shortest_path",
                "data": {
                    "nodes": {"0": {"objectid": "USER-A"}},
                    "edges": [
                        {
                            "source": "0",
                            "target": "1",
                            "kind": "MemberOf",
                        }
                    ],
                },
            }
        ),
        None,
    )

    assert ready is True
    assert projector.events[-1].kind is EvidenceEventKind.USEFUL_POSITIVE


def test_mcp_content_text_wrapper_is_unwrapped_before_projection() -> None:
    payload = {
        "info_type": "list",
        "data": [
            {
                "type": "active-directory",
                "name": "TEST.LOCAL",
                "id": "S-1-5-21-1",
            }
        ],
    }
    result_text = _tool_result_to_text(
        [
            SimpleNamespace(
                type="text",
                text=json.dumps(payload),
            )
        ]
    )

    assert json.loads(result_text) == payload
    projector = MCPTranscriptProjector(MCP_TASK, PROFILE)
    projector.observe(
        "domain_info",
        {"info_type": "list"},
        result_text,
        None,
    )
    assert projector.events[-1].kind not in {
        EvidenceEventKind.INFRASTRUCTURE_FAILURE,
        EvidenceEventKind.HARNESS_FAILURE,
    }


def test_graph_search_keyed_result_map_has_explicit_cardinality() -> None:
    projector = MCPTranscriptProjector(MCP_TASK, PROFILE)
    projector.observe(
        "graph_analysis",
        {"info_type": "search", "query": "DOMAIN ADMINS"},
        json.dumps(
            {
                "info_type": "search",
                "data": {
                    "data": {
                        "17242": {
                            "data": {
                                "name": "DOMAIN ADMINS@TEST.LOCAL",
                                "objectid": "S-1-5-21-1-512",
                            }
                        }
                    }
                },
            }
        ),
        None,
    )

    assert projector.events[-1].kind not in {
        EvidenceEventKind.INFRASTRUCTURE_FAILURE,
        EvidenceEventKind.HARNESS_FAILURE,
    }


def test_exact_set_requires_companion_count_and_bounded_page() -> None:
    bounds = MCP_TASK.binding.bounds.model_copy(
        update={
            "max_result_cardinality": 2,
            "page_size": 2,
            "require_total_count": True,
        }
    )
    set_task = MCP_TASK.model_copy(
        update={
            "task_id": "simple.mcp.set@2",
            "claim_kind": "set",
            "answer_policy": ExactSetPolicy(kind="exact_set"),
            "binding": MCP_TASK.binding.model_copy(update={"bounds": bounds}),
        }
    )
    projector = MCPTranscriptProjector(set_task, PROFILE)

    assert (
        projector.observe(
            "cypher_query",
            {
                "info_type": "run",
                "query": "MATCH (n:User) RETURN count(n) AS count",
            },
            json.dumps(
                {
                    "info_type": "run",
                    "success": True,
                    "data": {
                        "nodes": {},
                        "edges": [],
                        "literals": [{"key": "count", "value": 2}],
                    },
                    "node_count": 0,
                    "edge_count": 0,
                }
            ),
            None,
        )
        is False
    )
    assert projector.events[-1].kind is EvidenceEventKind.INCONCLUSIVE_EMPTY

    assert (
        projector.observe(
            "cypher_query",
            {
                "info_type": "run",
                "query": (
                    "MATCH (n:User) RETURN n "
                    "ORDER BY n.objectid SKIP 0 LIMIT 2"
                ),
            },
            json.dumps(
                {
                    "info_type": "run",
                    "success": True,
                    "data": {"nodes": {"0": {}, "1": {}}, "edges": []},
                    "node_count": 2,
                    "edge_count": 0,
                }
            ),
            None,
        )
        is True
    )
    assert projector.events[-1].kind is EvidenceEventKind.USEFUL_POSITIVE


def test_exact_set_aggregates_contiguous_stable_pages() -> None:
    bounds = MCP_TASK.binding.bounds.model_copy(
        update={
            "max_result_cardinality": 3,
            "page_size": 2,
            "max_pages": 2,
            "require_total_count": True,
        }
    )
    set_task = MCP_TASK.model_copy(
        update={
            "task_id": "simple.mcp.paged-set@2",
            "claim_kind": "set",
            "answer_policy": ExactSetPolicy(kind="exact_set"),
            "binding": MCP_TASK.binding.model_copy(update={"bounds": bounds}),
        }
    )
    projector = MCPTranscriptProjector(set_task, PROFILE)
    projector.observe(
        "cypher_query",
        {
            "info_type": "run",
            "query": "MATCH (n:User) RETURN count(n) AS count",
        },
        json.dumps(
            {
                "success": True,
                "data": {"literals": [{"key": "count", "value": 3}]},
                "node_count": 0,
                "edge_count": 0,
            }
        ),
        None,
    )

    assert (
        projector.observe(
            "cypher_query",
            {
                "info_type": "run",
                "query": (
                    "MATCH (n:User) RETURN n "
                    "ORDER BY n.objectid SKIP 0 LIMIT 2"
                ),
            },
            json.dumps({"success": True, "node_count": 2, "edge_count": 0}),
            None,
        )
        is False
    )
    assert (
        projector.observe(
            "cypher_query",
            {
                "info_type": "run",
                "query": (
                    "MATCH (n:User) RETURN n "
                    "ORDER BY n.objectid SKIP 2 LIMIT 2"
                ),
            },
            json.dumps({"success": True, "node_count": 1, "edge_count": 0}),
            None,
        )
        is True
    )
    assert projector.events[-1].kind is EvidenceEventKind.USEFUL_POSITIVE


def test_exact_set_rejects_non_identity_ordering() -> None:
    bounds = MCP_TASK.binding.bounds.model_copy(
        update={
            "max_result_cardinality": 2,
            "page_size": 2,
            "require_total_count": True,
        }
    )
    set_task = MCP_TASK.model_copy(
        update={
            "task_id": "simple.mcp.unstable-set@2",
            "claim_kind": "set",
            "answer_policy": ExactSetPolicy(kind="exact_set"),
            "binding": MCP_TASK.binding.model_copy(update={"bounds": bounds}),
        }
    )
    projector = MCPTranscriptProjector(set_task, PROFILE)
    projector.total_count = 2

    assert (
        projector.observe(
            "cypher_query",
            {
                "info_type": "run",
                "query": (
                    "MATCH (n:User) RETURN n "
                    "ORDER BY n.name SKIP 0 LIMIT 2"
                ),
            },
            json.dumps({"success": True, "node_count": 2, "edge_count": 0}),
            None,
        )
        is False
    )
    assert projector.events[-1].kind is EvidenceEventKind.TRUNCATED


@pytest.mark.parametrize(
    ("payload", "kind"),
    [
        (
            {"error": "Connection error: timed out"},
            EvidenceEventKind.INFRASTRUCTURE_FAILURE,
        ),
        (
            {
                "success": False,
                "error": "query blocked",
                "error_type": "policy_rejected",
            },
            EvidenceEventKind.POLICY_REJECTION,
        ),
        (
            {
                "success": False,
                "error": "syntax error",
                "error_type": "syntax_error",
            },
            EvidenceEventKind.IRRELEVANT,
        ),
    ],
)
def test_tool_errors_cannot_masquerade_as_empty_evidence(
    payload: dict[str, Any],
    kind: EvidenceEventKind,
) -> None:
    projector = MCPTranscriptProjector(MCP_TASK, PROFILE)
    ready = projector.observe(
        "cypher_query",
        {"info_type": "run", "query": "MATCH garbage"},
        json.dumps(payload),
        None,
    )

    assert ready is False
    assert projector.events[-1].kind is kind


def test_mcp_cypher_tool_routes_only_through_policy_coordinator() -> None:
    original_calls: list[str] = []
    coordinator_calls: list[str] = []

    @tool(name="cypher_query")
    def cypher_query():
        async def execute(info_type: str, query: str | None = None) -> str:
            original_calls.append(f"{info_type}:{query}")
            return "{}"

        return execute

    async def coordinator(query: str) -> CypherResult:
        coordinator_calls.append(query)
        return CypherResult(
            success=True,
            raw={
                "data": {
                    "nodes": {"0": {"objectid": "USER-A"}},
                    "edges": [],
                }
            },
            query_executed=True,
        )

    wrapped = _wrap_read_only_tool(
        cypher_query(),
        cypher_executor=coordinator,
    )
    _spec, executor = _ollama_tool_spec(wrapped)
    result = json.loads(
        asyncio.run(
            executor(
                info_type="run",
                query="MATCH (n:User) RETURN n LIMIT 1",
            )
        )
    )

    assert original_calls == []
    assert coordinator_calls == ["MATCH (n:User) RETURN n LIMIT 1"]
    assert result["success"] is True
    assert result["query_executed"] is True


def test_schema_only_retry_runs_once_after_useful_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    retry_calls: list[dict[str, Any]] = []

    async def fake_loop(**kwargs: Any):
        observer = kwargs["tool_result_observer"]
        observer(
            "graph_analysis",
            {"info_type": "shortest_path"},
            json.dumps(
                {
                    "info_type": "shortest_path",
                    "data": {
                        "nodes": {"0": {}, "1": {}},
                        "edges": [{"source": "0", "target": "1"}],
                    },
                }
            ),
            None,
        )
        return _response("not-json"), object(), [{"role": "assistant"}]

    async def retry_transport(**kwargs: Any) -> ModelResponse:
        retry_calls.append(kwargs)
        return _response(json.dumps(_answer()))

    monkeypatch.setattr(
        model_runtime,
        "_run_openai_compat_mcp_loop",
        fake_loop,
    )
    outcome, record = asyncio.run(
        run_mcp_model_task_v2(
            task=MCP_TASK,
            oracle=MCP_ORACLE,
            resolver=MCP_RESOLVER,
            profile=PROFILE,
            bundle=MCPServerBundle(tools=[]),
            model="codex/gpt-test",
            model_base_url=None,
            tool_loop=MCPToolLoop.NATIVE_OPENAI_COMPATIBLE,
            max_steps=4,
            transport=retry_transport,
        )
    )

    assert len(retry_calls) == 1
    assert retry_calls[0]["messages"][-1]["content"].startswith(
        "Return only one JSON object"
    )
    assert outcome.sample.execution_class is ExecutionClass.SUCCESS
    assert outcome.sample.reasoning_correct is True
    assert record.mcp_finalization is not None
    assert record.mcp_finalization["schema_retry_count"] == 1


def test_schema_retry_provider_failure_is_infrastructure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_loop(**kwargs: Any):
        kwargs["tool_result_observer"](
            "graph_analysis",
            {"info_type": "shortest_path"},
            json.dumps(
                {
                    "info_type": "shortest_path",
                    "data": {
                        "nodes": {"0": {}, "1": {}},
                        "edges": [{"source": "0", "target": "1"}],
                    },
                }
            ),
            None,
        )
        return _response("not-json"), object(), []

    async def failed_retry(**_kwargs: Any) -> ModelResponse:
        return _response("", error="provider unavailable")

    monkeypatch.setattr(
        model_runtime,
        "_run_openai_compat_mcp_loop",
        fake_loop,
    )
    outcome, _record = asyncio.run(
        run_mcp_model_task_v2(
            task=MCP_TASK,
            oracle=MCP_ORACLE,
            resolver=MCP_RESOLVER,
            profile=PROFILE,
            bundle=MCPServerBundle(tools=[]),
            model="codex/gpt-test",
            model_base_url=None,
            tool_loop=MCPToolLoop.NATIVE_OPENAI_COMPATIBLE,
            max_steps=4,
            transport=failed_retry,
        )
    )

    assert outcome.sample.execution_class is ExecutionClass.INFRA_FAILURE
    assert outcome.sample.reasoning_correct is None
    assert outcome.sample.outcome is SampleOutcomeCode.INFRA_ERROR


def test_internal_runtime_exception_is_harness_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def invalid_projector_loop(**kwargs: Any):
        raise ValueError("internal runtime invariant failed")

    monkeypatch.setattr(
        model_runtime,
        "_run_openai_compat_mcp_loop",
        invalid_projector_loop,
    )
    outcome, record = asyncio.run(
        run_mcp_model_task_v2(
            task=MCP_TASK,
            oracle=MCP_ORACLE,
            resolver=MCP_RESOLVER,
            profile=PROFILE,
            bundle=MCPServerBundle(tools=[]),
            model="codex/gpt-test",
            model_base_url=None,
            tool_loop=MCPToolLoop.NATIVE_OPENAI_COMPATIBLE,
            max_steps=4,
        )
    )

    assert outcome.sample.execution_class is ExecutionClass.HARNESS_FAILURE
    assert outcome.sample.outcome is SampleOutcomeCode.HARNESS_ERROR
    assert outcome.sample.reasoning_correct is None
    assert outcome.finalization.phase is FinalizationPhase.HARNESS_FAILURE
    assert record.provider_error is not None
    assert "internal runtime invariant failed" in record.provider_error


def test_unknown_complete_response_shape_fails_closed_without_crashing() -> None:
    projector = MCPTranscriptProjector(MCP_TASK, PROFILE)
    ready = projector.observe(
        "domain_info",
        {"info_type": "list"},
        "{}",
        None,
    )

    assert ready is False
    assert projector.events[-1].kind not in {
        EvidenceEventKind.INFRASTRUCTURE_FAILURE,
        EvidenceEventKind.HARNESS_FAILURE,
    }


def test_transcript_bound_overrun_invalidates_prior_useful_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bounded_task = MCP_TASK.model_copy(
        update={
            "binding": MCP_TASK.binding.model_copy(
                update={
                    "bounds": MCP_TASK.binding.bounds.model_copy(
                        update={"max_transcript_bytes": 1}
                    )
                }
            )
        }
    )

    async def fake_loop(**kwargs: Any):
        kwargs["tool_result_observer"](
            "graph_analysis",
            {"info_type": "shortest_path"},
            json.dumps(
                {
                    "info_type": "shortest_path",
                    "data": {
                        "nodes": {"0": {}, "1": {}},
                        "edges": [{"source": "0", "target": "1"}],
                    },
                }
            ),
            None,
        )
        return _response(json.dumps(_answer())), object(), [{"content": "too large"}]

    monkeypatch.setattr(
        model_runtime,
        "_run_openai_compat_mcp_loop",
        fake_loop,
    )
    outcome, _record = asyncio.run(
        run_mcp_model_task_v2(
            task=bounded_task,
            oracle=MCP_ORACLE,
            resolver=MCP_RESOLVER,
            profile=PROFILE,
            bundle=MCPServerBundle(tools=[]),
            model="codex/gpt-test",
            model_base_url=None,
            tool_loop=MCPToolLoop.NATIVE_OPENAI_COMPATIBLE,
            max_steps=4,
        )
    )

    assert outcome.sample.execution_class is ExecutionClass.MODEL_FAILURE
    assert outcome.sample.outcome is SampleOutcomeCode.OUTPUT_INVALID
    assert outcome.sample.reasoning_correct is False


def test_v2_model_runtime_has_no_legacy_grader_or_template_dispatch() -> None:
    source = inspect.getsource(model_runtime) + inspect.getsource(campaign_runner)

    assert "grade_mcp_diagnostic" not in source
    assert "answer.correct" not in source
    assert "template_id" not in source
    assert "from ori.eval.grader" not in source
    assert "from .grader" not in source
    assert "Track.DIRECT" in source
    assert "Track.MCP" in source
    assert 'resolved.output_dir\n                    / "direct-query-deny-cache' in source
    assert 'run_dir / "direct-query-deny-cache' not in source
    assert "progress=progress" in source
    assert "_emit_progress" in source


def test_v2_campaign_progress_is_model_blind_and_non_fatal() -> None:
    messages: list[str] = []
    campaign_runner._emit_progress(messages.append, "safe progress")
    assert messages == ["safe progress"]

    def broken_progress(_message: str) -> None:
        raise RuntimeError("closed terminal")

    campaign_runner._emit_progress(broken_progress, "must not affect scoring")


    graph_line = campaign_runner._graph_verification_progress(
        track=Track.MCP,
        stage="pre",
        receipt=SimpleNamespace(
            observed_graph_fingerprint="a" * 64,
        ),
    )
    assert graph_line == "[mcp] pre-track graph verified (aaaaaaaaaaaa)"

    sample = SampleResult(
        task_id=DIRECT_TASK.task_id,
        task_fingerprint=DIRECT_TASK.task_fingerprint,
        oracle_fingerprint=DIRECT_ORACLE.oracle_fingerprint,
        execution_class=ExecutionClass.MODEL_FAILURE,
        outcome=SampleOutcomeCode.QUERY_TIMEOUT,
        reasoning_correct=False,
        detail="query rejected as too complex",
    )
    provider = model_runtime._record(
        task=DIRECT_TASK,
        model="codex/gpt-test",
        surface="direct",
        response=_response("{}"),
    )
    line = campaign_runner._task_completion_progress(
        sample=sample,
        provider=provider,
        task_elapsed_seconds=12.5,
        results=(sample,),
    )

    assert "QUERY_TIMEOUT" in line
    assert "score=0.0" in line
    assert "elapsed=12.5s" in line
    assert "running_correct=0/1" in line
    assert DIRECT_ORACLE.oracle_id not in line
    assert "reference_cypher" not in line

    infrastructure = SampleResult(
        task_id=DIRECT_TASK.task_id,
        task_fingerprint=DIRECT_TASK.task_fingerprint,
        oracle_fingerprint=DIRECT_ORACLE.oracle_fingerprint,
        execution_class=ExecutionClass.INFRA_FAILURE,
        outcome=SampleOutcomeCode.INFRA_ERROR,
        reasoning_correct=None,
        detail="transport unavailable",
    )
    retry = campaign_runner._infrastructure_attempt_progress(
        sample=infrastructure,
        attempt_number=1,
        max_infra_retries=1,
    )
    exhausted = campaign_runner._infrastructure_attempt_progress(
        sample=infrastructure,
        attempt_number=2,
        max_infra_retries=1,
    )
    assert "retrying" in retry
    assert "retry budget exhausted" in exhausted


def test_v2_run_model_emits_task_retry_completion_and_resume_progress(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    infrastructure = SampleResult(
        task_id=DIRECT_TASK.task_id,
        task_fingerprint=DIRECT_TASK.task_fingerprint,
        oracle_fingerprint=DIRECT_ORACLE.oracle_fingerprint,
        execution_class=ExecutionClass.INFRA_FAILURE,
        outcome=SampleOutcomeCode.INFRA_ERROR,
        reasoning_correct=None,
        detail="transport unavailable",
    )
    terminal = SampleResult(
        task_id=DIRECT_TASK.task_id,
        task_fingerprint=DIRECT_TASK.task_fingerprint,
        oracle_fingerprint=DIRECT_ORACLE.oracle_fingerprint,
        execution_class=ExecutionClass.MODEL_FAILURE,
        outcome=SampleOutcomeCode.QUERY_TIMEOUT,
        reasoning_correct=False,
        detail="query too complex",
    )
    provider = model_runtime._record(
        task=DIRECT_TASK,
        model="codex/gpt-test",
        surface="direct",
        response=_response("{}"),
    )
    samples = iter((infrastructure, terminal))

    async def fake_direct_run(**_kwargs: Any):
        return None, next(samples), provider

    class HealthyBHCE:
        async def wait_until_healthy(self, **_kwargs: Any):
            return SimpleNamespace(ok=True)

    class Coordinator:
        def __init__(self) -> None:
            self.closed = 0

        def close_circuit(self) -> None:
            self.closed += 1

    pair = SimpleNamespace(
        public=SimpleNamespace(tasks=(DIRECT_TASK,)),
        private=SimpleNamespace(
            identity_catalog=DIRECT_ORACLE.resolved_roles,
        ),
    )
    prepared = campaign_runner.PreparedTrack(
        track=Track.DIRECT,
        pair=pair,
        profile=SimpleNamespace(),
        release=SimpleNamespace(
            entries=(SimpleNamespace(task_id=DIRECT_TASK.task_id),),
        ),
        live=SimpleNamespace(),
        certifications={},
    )
    resolved = SimpleNamespace(
        output_dir=tmp_path,
        config=SimpleNamespace(
            defaults=SimpleNamespace(
                max_infra_retries=1,
                model_base_url=None,
                health=SimpleNamespace(
                    timeout_seconds=1.0,
                    poll_interval=0.01,
                ),
            )
        ),
    )
    model = SimpleNamespace(
        name="gpt-test",
        requested_model="codex/gpt-test",
        model_base_url=None,
        options={},
    )
    def checkpoint(*_args: Any, results, **_kwargs: Any):
        return SimpleNamespace(results=tuple(results))

    state_holder = {"value": None}
    monkeypatch.setattr(
        campaign_runner,
        "_provenance",
        lambda **_kwargs: SimpleNamespace(run_identity=object()),
    )
    monkeypatch.setattr(campaign_runner, "_guard_run_dir", lambda *_args: None)
    monkeypatch.setattr(
        campaign_runner,
        "_load_state",
        lambda *_args, **_kwargs: state_holder["value"],
    )
    monkeypatch.setattr(campaign_runner, "build_checkpoint", checkpoint)
    monkeypatch.setattr(campaign_runner, "_state", lambda **_kwargs: object())
    monkeypatch.setattr(campaign_runner, "_write_model", lambda *_args: None)
    monkeypatch.setattr(
        campaign_runner,
        "OracleRegistry",
        lambda _private: SimpleNamespace(
            for_task=lambda _task_id: DIRECT_ORACLE,
        ),
    )
    monkeypatch.setattr(
        campaign_runner,
        "run_direct_model_task_v2",
        fake_direct_run,
    )
    coordinator = Coordinator()
    progress: list[str] = []

    _provenance, results = asyncio.run(
        campaign_runner._run_model(
            resolved=resolved,
            prepared=prepared,
            model=model,
            run_index=1,
            bhce=HealthyBHCE(),
            coordinator=coordinator,
            loop=None,
            runs_total=1,
            progress=progress.append,
        )
    )

    assert results == (terminal,)
    assert coordinator.closed == 1
    assert any("0 resumed" in message for message in progress)
    assert any("[1/1]" in message and DIRECT_TASK.task_id in message for message in progress)
    assert any("INFRA_ERROR" in message and "retrying" in message for message in progress)
    assert any(
        "QUERY_TIMEOUT" in message and "running_correct=0/1" in message
        for message in progress
    )
    assert any("run 1/1 complete" in message for message in progress)

    state_holder["value"] = SimpleNamespace(
        checkpoint=SimpleNamespace(results=(terminal,)),
        attempts=(),
    )
    resumed: list[str] = []
    _provenance, resumed_results = asyncio.run(
        campaign_runner._run_model(
            resolved=resolved,
            prepared=prepared,
            model=model,
            run_index=1,
            bhce=HealthyBHCE(),
            coordinator=coordinator,
            loop=None,
            runs_total=1,
            progress=resumed.append,
        )
    )
    assert resumed_results == (terminal,)
    assert any("1 resumed" in message for message in resumed)
    assert not any("[1/1]" in message for message in resumed)

    samples = iter((terminal,))
    state_holder["value"] = SimpleNamespace(
        checkpoint=SimpleNamespace(results=(infrastructure,)),
        attempts=(
            SimpleNamespace(task_id=DIRECT_TASK.task_id, attempt=1),
            SimpleNamespace(task_id=DIRECT_TASK.task_id, attempt=2),
        ),
    )
    resumed_infrastructure: list[str] = []
    resumed_attempt_numbers: list[int] = []

    def resumed_attempt(task_id, number, sample, provider):
        resumed_attempt_numbers.append(number)
        return SimpleNamespace(
            task_id=task_id,
            attempt=number,
            sample=sample,
            provider=provider,
        )

    monkeypatch.setattr(campaign_runner, "_attempt", resumed_attempt)
    _provenance, recovered_results = asyncio.run(
        campaign_runner._run_model(
            resolved=resolved,
            prepared=prepared,
            model=model,
            run_index=1,
            bhce=HealthyBHCE(),
            coordinator=coordinator,
            loop=None,
            runs_total=1,
            progress=resumed_infrastructure.append,
        )
    )

    assert recovered_results == (terminal,)
    assert resumed_attempt_numbers == [3]
    assert any("0 resumed" in message for message in resumed_infrastructure)
    assert any(
        "[1/1]" in message and DIRECT_TASK.task_id in message
        for message in resumed_infrastructure
    )
