from __future__ import annotations

import asyncio
import inspect
import json
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest
from inspect_ai.tool import tool

from ori.eval.adapter import ModelResponse
from ori.eval.bhce import CypherResult
from ori.eval.mcp_runtime import (
    MCPNoProgressTimeout,
    MCPServerBundle,
    MCPToolInfrastructureError,
    _execute_mcp_tool,
    _ollama_tool_spec,
    _tool_result_to_text,
    _wrap_read_only_tool,
)
from ori.eval.provider_contract import ProviderProtocolError
from ori.eval.v2 import campaign_runner, model_runtime
from ori.eval.v2.compiler import compile_acceptance_spec
from ori.eval.v2.graph import entity_property_fact_key
from ori.eval.v2.identity import IdentityResolver
from ori.eval.v2.live_projection import _mcp_fixture_query
from ori.eval.v2.mcp import (
    EvidenceEventKind,
    FinalizationPhase,
    MCPToolLoop,
)
from ori.eval.v2.model_runtime import (
    MCPTranscriptProjector,
    V2ModelRuntimeError,
    V2ModelTaskCancelled,
    direct_system_prompt,
    mcp_system_prompt,
    parse_direct_submission,
    run_direct_model_task_v2,
    run_mcp_model_task_v2,
)
from ori.eval.v2.schema import (
    AbsenceClaim,
    BoundedNegativePolicy,
    DecisionPolicy,
    EntityRef,
    EntitySelector,
    ExactCountPolicy,
    ExactSetPolicy,
    ExecutionClass,
    MCPClaimEvidenceContract,
    NegativeReasonCode,
    PopulationScope,
    PredicateOperator,
    PropertyPredicate,
    RelationshipPattern,
    RelationshipSemantics,
    RouteAcceptanceKind,
    SelectionExpression,
    SetClaim,
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


def _response(
    text: str,
    *,
    error: str | None = None,
    provider_metrics: dict[str, object] | None = None,
) -> ModelResponse:
    return ModelResponse(
        raw_text=text,
        cypher=None,
        parse_stage="raw_text" if text else "none",
        tokens_input=10,
        tokens_output=5,
        elapsed_seconds=0.01,
        model="codex/gpt-test",
        error=error,
        provider_metrics=provider_metrics or {},
    )


def _cypher_set_binding(*, bounds, projection_type: str):
    return MCP_TASK.binding.model_copy(
        update={
            "bounds": bounds,
            "mcp_evidence_contract": MCPClaimEvidenceContract(
                result_kind="entities",
                projection_types=(projection_type,),
            ),
        }
    )


def _public_selection_task(selection: SelectionExpression):
    required_roles = tuple(anchor.role for anchor in selection.anchors)
    binding = MCP_TASK.binding.model_copy(
        update={
            "bounds": MCP_TASK.binding.bounds.model_copy(
                update={"require_total_count": True}
            ),
            "mcp_evidence_contract": MCPClaimEvidenceContract(
                result_kind="entities",
                required_input_roles=required_roles,
                projection_types=(selection.projection_type,),
            )
        }
    )
    policy = ExactSetPolicy(kind="exact_set")
    claim = SetClaim(
        kind="set",
        claim_id="claim:public-selection",
        selection=selection,
        semantics=RelationshipSemantics.DIRECT,
        population_scope=PopulationScope.BENCHMARK_NAMESPACE,
    )
    return MCP_TASK.model_copy(
        update={
            "claim_kind": "set",
            "answer_policy": policy,
            "acceptance_spec": compile_acceptance_spec(claim, policy, binding),
            "binding": binding,
        }
    )


def _observe_route_evidence(observer) -> None:
    observer(
        "cypher_query",
        {
            "info_type": "run",
            "query": (
                "MATCH p=(a {objectid:'USER-A'})-[:MemberOf]->(b {objectid:'GROUP-B'}) RETURN p"
            ),
        },
        json.dumps(
            {
                "info_type": "run",
                "success": True,
                "data": {
                    "nodes": {
                        "0": {"objectid": "USER-A"},
                        "1": {"objectid": "GROUP-B"},
                    },
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


def test_direct_public_request_includes_bounds_without_oracle_material() -> None:
    prompt = direct_system_prompt(DIRECT_TASK)
    request = json.loads(prompt.split("\n\n", maxsplit=1)[1])

    assert '"track": "direct"' in prompt
    assert '"max_hops": 1' in prompt
    assert DIRECT_ORACLE.oracle_id not in prompt
    assert "route_variants" not in prompt
    assert "reference_cypher" not in prompt
    assert DIRECT_TASK.question not in prompt
    assert "task_fingerprint" not in json.dumps(request)
    assert "envelope_fingerprint" not in json.dumps(request)
    assert "answer_schema" not in json.dumps(request)
    assert json.dumps(request).count('"max_hops"') == 1
    assert request["task_contract"]["acceptance_spec"] == (
        DIRECT_TASK.acceptance_spec.model_dump(mode="json")
    )
    assert set(request) == {
        "query_result_contract",
        "submission_schema",
        "task_contract",
    }


def test_direct_prompt_preserves_v1_cysql_contract_and_separates_answer_schema() -> (
    None
):
    route_prompt = direct_system_prompt(DIRECT_TASK)
    set_prompt = direct_system_prompt(DIRECT_SET_TASK)

    assert "Use RETURN p for path queries" in route_prompt
    assert "list comprehensions" in route_prompt
    assert "after the final RETURN projection" in route_prompt
    assert '"version": "ori-direct-result-contract-v14"' in route_prompt
    assert "toString() on a Path" in route_prompt
    assert "reduce()" in route_prompt
    assert "globally sort Path values" in route_prompt
    assert "both endpoint node variables" in route_prompt
    assert "quadratic pairwise node-inequality" in route_prompt
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


def test_direct_absence_submission_uses_the_public_reason_vocabulary() -> None:
    task = DIRECT_TASK.model_copy(update={"claim_kind": "absence"})
    valid = {
        "query": "MATCH (n) RETURN n LIMIT 1",
        "assertion": {
            "path_status": "no_path",
            "negative_reason_codes": ["objective_unreachable"],
        },
    }

    assert parse_direct_submission(json.dumps(valid), task).assertion == valid["assertion"]

    valid["assertion"]["negative_reason_codes"] = ["invented_reason"]
    with pytest.raises(V2ModelRuntimeError, match="schema mismatch"):
        parse_direct_submission(json.dumps(valid), task)


def test_mcp_negative_proof_query_must_cover_public_route_scope() -> None:
    claim = AbsenceClaim(
        kind="absence",
        claim_id="claim:negative",
        source=EntitySelector(role="source", object_type="User"),
        target=EntitySelector(role="target", object_type="Group"),
        relationships=("MemberOf", "Enroll", "PublishedTo"),
        reason_codes=(NegativeReasonCode.OBJECTIVE_UNREACHABLE,),
        max_hops=12,
        semantics=RelationshipSemantics.DIRECT,
        population_scope=PopulationScope.BENCHMARK_NAMESPACE,
    )
    binding = MCP_TASK.binding.model_copy(
        update={
            "bounds": MCP_TASK.binding.bounds.model_copy(
                update={"max_hops": 12}
            ),
            "mcp_evidence_contract": MCPClaimEvidenceContract(
                result_kind="scalar_count",
                required_input_roles=("source", "target"),
            ),
        }
    )
    policy = BoundedNegativePolicy(kind="bounded_negative")
    task = MCP_TASK.model_copy(
        update={
            "claim_kind": "absence",
            "answer_policy": policy,
            "acceptance_spec": compile_acceptance_spec(
                claim,
                policy,
                binding,
            ),
            "binding": binding,
        }
    )
    prefix = (
        "MATCH p=(a {objectid:'USER-A'})"
    )
    target = "(b {objectid:'GROUP-B'})"

    assert model_runtime._query_matches_public_claim(
        task,
        f"{prefix}-[*1..12]->{target} RETURN count(p) AS count",
        is_count=True,
    )
    assert model_runtime._query_matches_public_claim(
        task,
        f"{prefix}-[*1..12]->{target} RETURN count(*) AS count",
        is_count=True,
    )
    assert model_runtime._query_matches_public_claim(
        task,
        (
            f"{prefix}-[:MemberOf|Enroll|PublishedTo*1..12]->"
            f"{target} RETURN count(p) AS count"
        ),
        is_count=True,
    )
    assert model_runtime._query_matches_public_claim(
        task,
        (
            "MATCH (a {objectid:'USER-A'}), (b {objectid:'GROUP-B'}) "
            "OPTIONAL MATCH p=(a)-[:MemberOf|Enroll|PublishedTo*1..12]->(b) "
            "RETURN count(p) AS count"
        ),
        is_count=True,
    )
    assert not model_runtime._query_matches_public_claim(
        task,
        f"{prefix}-[*1..3]->{target} RETURN count(p) AS count",
        is_count=True,
    )
    assert not model_runtime._query_matches_public_claim(
        task,
        f"{prefix}-[*12..12]->{target} RETURN count(p) AS count",
        is_count=True,
    )
    assert not model_runtime._query_matches_public_claim(
        task,
        f"{prefix}-[:InventedEdge]->{target} RETURN count(p) AS count",
        is_count=True,
    )
    assert not model_runtime._query_matches_public_claim(
        task,
        (
            "MATCH p=(a {objectid:'USER-A'})"
            "-[:MemberOf|Enroll|PublishedTo*1..12]->(x) "
            "MATCH (b {objectid:'GROUP-B'}) RETURN count(p) AS count"
        ),
        is_count=True,
    )
    assert not model_runtime._query_matches_public_claim(
        task,
        (
            "MATCH p=(a {objectid:'USER-A'})"
            "-[r:MemberOf|Enroll|PublishedTo*1..12 WHERE r.active = true]->"
            "(b {objectid:'GROUP-B'}) RETURN count(p) AS count"
        ),
        is_count=True,
    )
    assert not model_runtime._query_matches_public_claim(
        task,
        (
            "MATCH p=(a:User:Computer {objectid:'USER-A'})"
            "-[:MemberOf|Enroll|PublishedTo*1..12]->"
            "(b:Group {objectid:'GROUP-B'}) RETURN count(p) AS count"
        ),
        is_count=True,
    )
    assert not model_runtime._query_matches_public_claim(
        task,
        (
            "MATCH p=(a:USER {objectid:'USER-A'})"
            "-[:MemberOf|Enroll|PublishedTo*1..12]->"
            "(b:Group {objectid:'GROUP-B'}) RETURN count(p) AS count"
        ),
        is_count=True,
    )
    assert not model_runtime._query_matches_public_claim(
        task,
        (
            "MATCH p=(a {OBJECTID:'USER-A'})"
            "-[:MemberOf|Enroll|PublishedTo*1..12]->"
            "(b {objectid:'GROUP-B'}) RETURN count(p) AS count"
        ),
        is_count=True,
    )
    assert not model_runtime._query_matches_public_claim(
        task,
        (
            "MATCH p=(a {name:'ALICE'})"
            "-[:MemberOf|Enroll|PublishedTo*1..12]->"
            "(b {objectid:'GROUP-B'}) RETURN count(p) AS count"
        ),
        is_count=True,
    )
    assert model_runtime._query_matches_public_claim(
        task,
        (
            "MATCH p=(a {name:'ALICE@EXAMPLE.LOCAL'})"
            "-[:MemberOf|Enroll|PublishedTo*1..12]->"
            "(b {objectid:'GROUP-B'}) RETURN count(p) AS count"
        ),
        is_count=True,
    )
    assert model_runtime._query_matches_public_claim(
        task,
        (
            "MATCH p=(a)-[:MemberOf|Enroll|PublishedTo*1..12]->(b) "
            "WHERE TOUPPER(a.name) = TOUPPER('alice@example.local') "
            "AND b.objectid = 'GROUP-B' RETURN count(p) AS count"
        ),
        is_count=True,
    )
    assert model_runtime._query_matches_public_claim(
        task,
        (
            "MATCH (a {objectid:'USER-A'}), "
            "(b {objectid:'GROUP-B'}) "
            "MATCH p=(a)-[:MemberOf|Enroll|PublishedTo*1..12]->(b) "
            "RETURN count(p) AS count"
        ),
        is_count=True,
    )
    assert model_runtime._query_matches_public_claim(
        task,
        (
            "MATCH (a {objectid:'USER-A'}) "
            "MATCH p=(a)-[:MemberOf|Enroll|PublishedTo*1..12]->"
            "(b {objectid:'GROUP-B'}) RETURN count(p) AS count"
        ),
        is_count=True,
    )
    assert not model_runtime._query_matches_public_claim(
        task,
        (
            "MATCH (a {objectid:'USER-A'}), "
            "(b {objectid:'GROUP-B'}), "
            "(x {objectid:'USER-A'}) "
            "MATCH p=(a)-[:MemberOf|Enroll|PublishedTo*1..12]->(b) "
            "RETURN count(p) AS count"
        ),
        is_count=True,
    )
    assert not model_runtime._query_matches_public_claim(
        task,
        (
            "MATCH (a {objectid:'GROUP-B'}) "
            "MATCH p=(a)-[:MemberOf|Enroll|PublishedTo*1..12]->"
            "(b {objectid:'GROUP-B'}) RETURN count(p) AS count"
        ),
        is_count=True,
    )
    assert not model_runtime._query_matches_public_claim(
        task,
        (
            "MATCH p=(a)-[:MemberOf|Enroll|PublishedTo*1..12]->(b) "
            "WHERE a.name = 'ALICE' AND b.objectid = 'GROUP-B' "
            "RETURN count(p) AS count"
        ),
        is_count=True,
    )
    assert not model_runtime._query_matches_public_claim(
        task,
        (
            "MATCH p=(a {objectid:'USER-A'})"
            "-[:memberof|Enroll|PublishedTo*1..12]->"
            "(b {objectid:'GROUP-B'}) RETURN count(p) AS count"
        ),
        is_count=True,
    )
    assert not model_runtime._query_matches_public_claim(
        task,
        (
            "MATCH p=(a)-[:MemberOf|Enroll|PublishedTo*1..12]->(b) "
            "WHERE A.objectid = 'USER-A' AND b.objectid = 'GROUP-B' "
            "RETURN count(p) AS count"
        ),
        is_count=True,
    )
    assert not model_runtime._query_matches_public_claim(
        task,
        (
            f"{prefix}-[:MemberOf|Enroll|PublishedTo*1..12]->"
            f"{target} RETURN count(P) AS count"
        ),
        is_count=True,
    )
    assert not model_runtime._query_matches_public_claim(
        task,
        (
            "MATCH p=(a {objectid:'USER-A'})"
            "-[r:MemberOf|Enroll|PublishedTo*1..12]->"
            "(b {objectid:'GROUP-B'}) "
            "WHERE r.objectid = 'USER-A' RETURN count(p) AS count"
        ),
        is_count=True,
    )
    assert not model_runtime._query_matches_public_claim(
        task,
        (
            "MATCH p=(a {objectid:'USER-A'})"
            "-[:MemberOf|Enroll|PublishedTo*1..12]->(x), "
            "(b {objectid:'GROUP-B'}) RETURN count(p) AS count"
        ),
        is_count=True,
    )
    assert not model_runtime._query_matches_public_claim(
        task,
        (
            f"{prefix}<-[:MemberOf|Enroll|PublishedTo*1..12]-"
            f"{target} RETURN count(p) AS count"
        ),
        is_count=True,
    )
    assert not model_runtime._query_matches_public_claim(
        task,
        (
            "MATCH p=(a)-[:MemberOf|Enroll|PublishedTo*1..12]->(b) "
            "WHERE a.objectid = 'USER-A' AND b.objectid = 'GROUP-B' "
            "AND false = true RETURN count(p) AS count"
        ),
        is_count=True,
    )
    assert model_runtime._query_matches_public_claim(
        task,
        (
            "MATCH p=(a)-[:MemberOf|Enroll|PublishedTo*1..12]->(b) "
            "WHERE a.objectid = 'USER-A' AND b.objectid = 'GROUP-B' "
            "RETURN count(p) AS count"
        ),
        is_count=True,
    )
    assert model_runtime._query_matches_public_claim(
        task,
        (
            "MATCH p=(a)-[:MemberOf|Enroll|PublishedTo*1..12]->(b) "
            "WHERE (a.objectid = 'USER-A') AND "
            "((b.objectid = 'GROUP-B')) RETURN count(p) AS count"
        ),
        is_count=True,
    )
    assert model_runtime._query_matches_public_claim(
        task,
        (
            "MATCH p=(a)-[:MemberOf|Enroll|PublishedTo*1..12]->(b) "
            "WHERE (a.objectid = 'USER-A' AND "
            "b.objectid = 'GROUP-B') RETURN count(p) AS count"
        ),
        is_count=True,
    )
    assert model_runtime._query_matches_public_claim(
        task,
        (
            "MATCH p=(a)-[:MemberOf|Enroll|PublishedTo*1..12]->(b) "
            "WHERE (a.objectid = 'USER-A' AND "
            "b.objectid = 'GROUP-B') AND "
            "a.name = 'ALICE@EXAMPLE.LOCAL' RETURN count(p) AS count"
        ),
        is_count=True,
    )
    assert not model_runtime._query_matches_public_claim(
        task,
        (
            "MATCH p=(a {objectid:'USER-A'})"
            "-[:MemberOf|Enroll|PublishedTo*1..12]->"
            "(b {objectid:'GROUP-B'}) "
            "WHERE a.objectid = 'GROUP-B' RETURN count(p) AS count"
        ),
        is_count=True,
    )

    wildcard = MCPTranscriptProjector(task, PROFILE)
    assert (
        wildcard.observe(
            "cypher_query",
            {
                "info_type": "run",
                "query": f"{prefix}-[*1..12]->{target} RETURN count(p) AS count",
            },
            json.dumps(
                {
                    "success": True,
                    "data": {"literals": [{"key": "count", "value": 1}]},
                    "node_count": 0,
                    "edge_count": 0,
                }
            ),
            None,
        )
        is False
    )
    assert wildcard.events[-1].kind is EvidenceEventKind.IRRELEVANT

    undirected = MCPTranscriptProjector(task, PROFILE)
    assert (
        undirected.observe(
            "cypher_query",
            {
                "info_type": "run",
                "query": (
                    f"{prefix}-[:MemberOf|Enroll|PublishedTo*1..12]-"
                    f"{target} RETURN count(p) AS count"
                ),
            },
            json.dumps(
                {
                    "success": True,
                    "data": {"literals": [{"key": "count", "value": 1}]},
                    "node_count": 0,
                    "edge_count": 0,
                }
            ),
            None,
        )
        is False
    )
    assert undirected.events[-1].kind is EvidenceEventKind.IRRELEVANT

    exact = MCPTranscriptProjector(task, PROFILE)
    assert (
        exact.observe(
            "cypher_query",
            {
                "info_type": "run",
                "query": (
                    f"{prefix}-[:MemberOf|Enroll|PublishedTo*1..12]->"
                    f"{target} RETURN count(p) AS count"
                ),
            },
            json.dumps(
                {
                    "success": True,
                    "data": {"literals": [{"key": "count", "value": 1}]},
                    "node_count": 0,
                    "edge_count": 0,
                }
            ),
            None,
        )
        is True
    )
    assert exact.events[-1].kind is EvidenceEventKind.USEFUL_POSITIVE

    narrow_claim = claim.model_copy(update={"max_hops": 1})
    narrow_binding = binding.model_copy(
        update={
            "bounds": binding.bounds.model_copy(update={"max_hops": 1}),
        }
    )
    narrow_task = task.model_copy(
        update={
            "acceptance_spec": compile_acceptance_spec(
                narrow_claim,
                policy,
                narrow_binding,
            ),
            "binding": narrow_binding,
        }
    )
    assert model_runtime._query_matches_public_claim(
        narrow_task,
        (
            f"{prefix}-[:MemberOf|Enroll|PublishedTo]->"
            f"{target} RETURN count(p) AS count"
        ),
        is_count=True,
    )
    assert model_runtime._query_matches_public_claim(
        narrow_task,
        f"{prefix}-->{target} RETURN count(p) AS count",
        is_count=True,
    )
    assert model_runtime._query_matches_public_claim(
        narrow_task,
        (
            "MATCH p=(a /* source */ {objectid:'USER-A'})"
            "-[:MemberOf|Enroll|PublishedTo*1]->"
            "(b {objectid:'GROUP-B'}) RETURN count(p) AS count"
        ),
        is_count=True,
    )
    exact_one_hop = MCPTranscriptProjector(narrow_task, PROFILE)
    assert (
        exact_one_hop.observe(
            "cypher_query",
            {
                "info_type": "run",
                "query": (
                    f"{prefix}-[:MemberOf|Enroll|PublishedTo]->"
                    f"{target} RETURN count(p) AS count"
                ),
            },
            json.dumps(
                {
                    "success": True,
                    "data": {"literals": [{"key": "count", "value": 0}]},
                    "node_count": 0,
                    "edge_count": 0,
                }
            ),
            None,
        )
        is True
    )
    assert exact_one_hop.events[-1].kind is EvidenceEventKind.VALID_NEGATIVE

    lower_zero = MCPTranscriptProjector(narrow_task, PROFILE)
    assert (
        lower_zero.observe(
            "cypher_query",
            {
                "info_type": "run",
                "query": (
                    f"{prefix}-[:MemberOf|Enroll|PublishedTo*0..1]->"
                    f"{target} RETURN count(p) AS count"
                ),
            },
            json.dumps(
                {
                    "success": True,
                    "data": {"literals": [{"key": "count", "value": 1}]},
                    "node_count": 0,
                    "edge_count": 0,
                }
            ),
            None,
        )
        is False
    )
    assert lower_zero.events[-1].kind is EvidenceEventKind.IRRELEVANT

    contradictory_selector = MCPTranscriptProjector(task, PROFILE)
    assert (
        contradictory_selector.observe(
            "cypher_query",
            {
                "info_type": "run",
                "query": (
                    "MATCH p=(a {objectid:'USER-A'})"
                    "-[:MemberOf|Enroll|PublishedTo*1..12]->"
                    "(b {objectid:'GROUP-B'}) "
                    "WHERE a.objectid = 'GROUP-B' RETURN count(p) AS count"
                ),
            },
            json.dumps(
                {
                    "success": True,
                    "data": {"literals": [{"key": "count", "value": 0}]},
                    "node_count": 0,
                    "edge_count": 0,
                }
            ),
            None,
        )
        is False
    )
    assert (
        contradictory_selector.events[-1].kind
        is EvidenceEventKind.IRRELEVANT
    )

    non_endpoint_selector = MCPTranscriptProjector(task, PROFILE)
    assert (
        non_endpoint_selector.observe(
            "cypher_query",
            {
                "info_type": "run",
                "query": (
                    "MATCH p=(a {objectid:'USER-A'})"
                    "-[r:MemberOf|Enroll|PublishedTo*1..12]->"
                    "(b {objectid:'GROUP-B'}) "
                    "WHERE r.objectid = 'USER-A' RETURN count(p) AS count"
                ),
            },
            json.dumps(
                {
                    "success": True,
                    "data": {"literals": [{"key": "count", "value": 0}]},
                    "node_count": 0,
                    "edge_count": 0,
                }
            ),
            None,
        )
        is False
    )
    assert non_endpoint_selector.events[-1].kind is EvidenceEventKind.IRRELEVANT

    overwide = MCPTranscriptProjector(narrow_task, PROFILE)
    assert (
        overwide.observe(
            "cypher_query",
            {
                "info_type": "run",
                "query": (
                    f"{prefix}-[:MemberOf|Enroll|PublishedTo*1..12]->"
                    f"{target} RETURN count(p) AS count"
                ),
            },
            json.dumps(
                {
                    "success": True,
                    "data": {"literals": [{"key": "count", "value": 1}]},
                    "node_count": 0,
                    "edge_count": 0,
                }
            ),
            None,
        )
        is False
    )
    assert overwide.events[-1].kind is EvidenceEventKind.IRRELEVANT


def test_public_selection_requires_the_declared_relationship() -> None:
    task = _public_selection_task(
        SelectionExpression(
            anchors=(EntitySelector(role="target", object_type="Group"),),
            relationships=(
                RelationshipPattern(
                    source_role="result",
                    relationship="MemberOf",
                    target_role="target",
                    source_type="User",
                    target_type="Group",
                ),
            ),
            projection_role="result",
            projection_type="User",
        )
    )
    connected = (
        "MATCH (g:Group {objectid:'GROUP-B'}) "
        "MATCH (u:User)-[:MemberOf]->(g) "
        "RETURN u.objectid AS object_id ORDER BY u.objectid"
    )
    anonymous_anchor = (
        "MATCH (:Group {objectid:'GROUP-B'})<-[:MemberOf]-(u:User) "
        "RETURN u.objectid AS object_id ORDER BY u.objectid"
    )
    disconnected = (
        "MATCH (g:Group {objectid:'GROUP-B'}) MATCH (u:User) "
        "RETURN u.objectid AS object_id ORDER BY u.objectid"
    )
    case_distinct_selector = (
        "MATCH (G:Group {objectid:'GROUP-B'}) "
        "MATCH (u:User)-[:MemberOf]->(g:Group) "
        "RETURN u.objectid AS object_id ORDER BY u.objectid"
    )

    assert model_runtime._query_matches_public_claim(
        task,
        connected,
        is_count=False,
    )
    assert model_runtime._query_matches_public_claim(
        task,
        anonymous_anchor,
        is_count=False,
    )
    assert not model_runtime._query_matches_public_claim(
        task,
        disconnected,
        is_count=False,
    )
    assert not model_runtime._query_matches_public_claim(
        task,
        case_distinct_selector,
        is_count=False,
    )


def test_returned_path_must_bind_the_public_endpoint_selectors() -> None:
    bound = (
        "MATCH p=(a {objectid:'USER-A'})-[:MemberOf]->"
        "(b {objectid:'GROUP-B'}) RETURN p"
    )
    separately_bound = (
        "MATCH (a {objectid:'USER-A'}), (b {objectid:'GROUP-B'}) "
        "MATCH p=(a)-[:MemberOf]->(b) RETURN p"
    )
    detached = (
        "MATCH p=(x)-[:MemberOf]->(y) "
        "MATCH (a {objectid:'USER-A'}), (b {objectid:'GROUP-B'}) "
        "RETURN p"
    )
    detached_in_same_clause = (
        "MATCH p=(a {objectid:'USER-A'})-[:MemberOf]->(x), "
        "(b {objectid:'GROUP-B'}) RETURN p"
    )
    aliased_passthrough = (
        "MATCH p=(a {objectid:'USER-A'})-[:MemberOf]->"
        "(b {objectid:'GROUP-B'}) WITH p AS route RETURN route"
    )
    rebound = (
        "MATCH p=(a {objectid:'USER-A'})-[:MemberOf]->"
        "(b {objectid:'GROUP-B'}) WITH p "
        "MATCH (x:User {objectid:'USER-X'}) WITH x AS p RETURN p"
    )
    detached_after_path_passthrough = (
        "MATCH p=(x)-[:MemberOf]->(y) WITH p "
        "MATCH (a {objectid:'USER-A'}), (b {objectid:'GROUP-B'}) RETURN p"
    )
    selector_passthrough = (
        "MATCH (a {objectid:'USER-A'}), (b {objectid:'GROUP-B'}) "
        "WITH a AS source, b AS target "
        "MATCH p=(source)-[:MemberOf]->(target) RETURN p"
    )
    constrained_after_assignment = (
        "MATCH p=(x)-[:MemberOf]->(y) "
        "MATCH (x {objectid:'USER-A'}), (y {objectid:'GROUP-B'}) RETURN p"
    )
    constrained_after_with = (
        "MATCH p=(x)-[:MemberOf]->(y) WITH p, x, y "
        "WHERE x.objectid='USER-A' AND y.objectid='GROUP-B' RETURN p"
    )

    assert model_runtime._query_matches_public_claim(
        MCP_TASK,
        bound,
        is_count=False,
    )
    assert model_runtime._query_matches_public_claim(
        MCP_TASK,
        separately_bound,
        is_count=False,
    )
    assert model_runtime._query_matches_public_claim(
        MCP_TASK,
        aliased_passthrough,
        is_count=False,
    )
    assert model_runtime._query_matches_public_claim(
        MCP_TASK,
        selector_passthrough,
        is_count=False,
    )
    assert model_runtime._query_matches_public_claim(
        MCP_TASK,
        constrained_after_assignment,
        is_count=False,
    )
    assert model_runtime._query_matches_public_claim(
        MCP_TASK,
        constrained_after_with,
        is_count=False,
    )
    assert not model_runtime._query_matches_public_claim(
        MCP_TASK,
        detached,
        is_count=False,
    )
    assert not model_runtime._query_matches_public_claim(
        MCP_TASK,
        detached_in_same_clause,
        is_count=False,
    )
    assert not model_runtime._query_matches_public_claim(
        MCP_TASK,
        rebound,
        is_count=False,
    )
    assert not model_runtime._query_matches_public_claim(
        MCP_TASK,
        detached_after_path_passthrough,
        is_count=False,
    )


def test_query_selectors_use_live_identity_properties_not_answer_aliases() -> None:
    source = MCP_TASK.input_entities[0].model_copy(
        update={"aliases": ("ALICE",)}
    )
    task = MCP_TASK.model_copy(
        update={"input_entities": (source, MCP_TASK.input_entities[1])}
    )

    assert model_runtime._query_matches_public_claim(
        task,
        (
            "MATCH p=(a {name:'ALICE@EXAMPLE.LOCAL'})-[:MemberOf]->"
            "(b {objectid:'GROUP-B'}) RETURN p"
        ),
        is_count=False,
    )
    assert not model_runtime._query_matches_public_claim(
        task,
        (
            "MATCH p=(a {name:'ALICE'})-[:MemberOf]->"
            "(b {objectid:'GROUP-B'}) RETURN p"
        ),
        is_count=False,
    )
    assert not model_runtime._query_matches_public_claim(
        task,
        (
            "MATCH p=(a {name:'USER-A'})-[:MemberOf]->"
            "(b {objectid:'GROUP-B'}) RETURN p"
        ),
        is_count=False,
    )
    assert not model_runtime._query_matches_public_claim(
        task,
        (
            "MATCH p=(a {OBJECTID:'USER-A'})-[:MemberOf]->"
            "(b {objectid:'GROUP-B'}) RETURN p"
        ),
        is_count=False,
    )


def test_public_selection_requires_unproven_concrete_projection_label() -> None:
    task = _public_selection_task(
        SelectionExpression(
            anchors=(EntitySelector(role="target", object_type="Group"),),
            relationships=(
                RelationshipPattern(
                    source_role="result",
                    relationship="MemberOf",
                    target_role="target",
                    source_type="User",
                    target_type="Group",
                ),
            ),
            projection_role="result",
            projection_type="User",
        )
    )
    explicit_user = (
        "MATCH (g:Group {objectid:'GROUP-B'}) "
        "MATCH (result:User)-[:MemberOf]->(g) "
        "RETURN result.objectid AS object_id ORDER BY result.objectid"
    )
    unlabeled_principal = (
        "MATCH (g:Group {objectid:'GROUP-B'}) "
        "MATCH (result)-[:MemberOf]->(g) "
        "RETURN result.objectid AS object_id ORDER BY result.objectid"
    )

    assert model_runtime._query_matches_public_claim(
        task,
        explicit_user,
        is_count=False,
    )
    assert not model_runtime._query_matches_public_claim(
        task,
        unlabeled_principal,
        is_count=False,
    )


def test_public_selection_rejects_undeclared_population_filters() -> None:
    selection = SelectionExpression(
        predicates=(
            PropertyPredicate(
                role="result",
                property_name="hasspn",
                operator=PredicateOperator.EQUALS,
                value=True,
            ),
        ),
        projection_role="result",
        projection_type="User",
    )
    task = _public_selection_task(selection)
    declared = (
        "MATCH (u:User) WHERE u.hasspn = true "
        "RETURN u.objectid AS object_id ORDER BY u.objectid"
    )
    coalesced = (
        "MATCH (u:User) WHERE coalesce(u.hasspn, false) = true "
        "RETURN u.objectid AS object_id ORDER BY u.objectid"
    )
    undeclared = (
        "MATCH (u:User) WHERE u.hasspn = true AND u.enabled = true "
        "RETURN u.objectid AS object_id ORDER BY u.objectid"
    )

    assert model_runtime._query_matches_public_claim(
        task,
        declared,
        is_count=False,
    )
    assert model_runtime._query_matches_public_claim(
        task,
        coalesced,
        is_count=False,
    )
    assert not model_runtime._query_matches_public_claim(
        task,
        undeclared,
        is_count=False,
    )


def test_public_selection_uses_exact_bloodhound_schema_identifiers() -> None:
    selection = SelectionExpression(
        anchors=(EntitySelector(role="target", object_type="Group"),),
        relationships=(
            RelationshipPattern(
                source_role="result",
                relationship="MemberOf",
                target_role="target",
                source_type="User",
                target_type="Group",
            ),
        ),
        predicates=(
            PropertyPredicate(
                role="result",
                property_name="hasspn",
                operator=PredicateOperator.EQUALS,
                value=True,
            ),
        ),
        projection_role="result",
        projection_type="User",
    )
    base_task = _public_selection_task(selection)
    target = base_task.input_entities[1].model_copy(
        update={"aliases": ("DOMAIN ADMINS",)}
    )
    task = base_task.model_copy(
        update={"input_entities": (base_task.input_entities[0], target)}
    )
    valid = (
        "MATCH (g:Group {objectid:'GROUP-B'}) "
        "MATCH (u:User)-[:MemberOf]->(g) WHERE u.hasspn = true "
        "RETURN u.objectid AS object_id ORDER BY u.objectid"
    )

    assert model_runtime._query_matches_public_claim(task, valid, is_count=False)
    assert not model_runtime._query_matches_public_claim(
        task,
        valid.replace(":User", ":user"),
        is_count=False,
    )
    assert not model_runtime._query_matches_public_claim(
        task,
        valid.replace(":MemberOf", ":memberof"),
        is_count=False,
    )
    assert not model_runtime._query_matches_public_claim(
        task,
        valid.replace(".hasspn", ".HASSPN"),
        is_count=False,
    )
    assert not model_runtime._query_matches_public_claim(
        task,
        valid.replace("objectid:'GROUP-B'", "name:'DOMAIN ADMINS'"),
        is_count=False,
    )


def test_public_not_true_boolean_accepts_equivalent_coalesce_false() -> None:
    selection = SelectionExpression(
        predicates=(
            PropertyPredicate(
                role="result",
                property_name="isdc",
                operator=PredicateOperator.NOT_EQUALS,
                value=True,
            ),
        ),
        projection_role="result",
        projection_type="Computer",
    )
    task = _public_selection_task(selection)
    query = (
        "MATCH (c:Computer) WHERE coalesce(c.isdc, false) = false "
        "RETURN c.objectid AS object_id ORDER BY c.objectid"
    )

    assert model_runtime._query_matches_public_claim(
        task,
        query,
        is_count=False,
    )


def test_selection_fixture_query_realizes_the_public_contract() -> None:
    task = _public_selection_task(
        SelectionExpression(
            anchors=(EntitySelector(role="target", object_type="Group"),),
            relationships=(
                RelationshipPattern(
                    source_role="result",
                    relationship="MemberOf",
                    target_role="target",
                    source_type="User",
                    target_type="Group",
                ),
            ),
            projection_role="result",
            projection_type="User",
        )
    )

    page_query = _mcp_fixture_query(task, count=False, limit=100)
    count_query = _mcp_fixture_query(task, count=True)

    assert model_runtime._query_matches_public_claim(
        task,
        page_query,
        is_count=False,
    )
    assert model_runtime._query_matches_public_claim(
        task,
        count_query,
        is_count=True,
        allow_set_companion_count=True,
    )


def test_direct_model_runtime_executes_exactly_once_through_coordinator() -> None:
    query = "MATCH p=(a)-[:MemberOf]->(b) RETURN p LIMIT 1"
    provider_request: dict[str, Any] = {}

    async def transport(**kwargs: Any) -> ModelResponse:
        provider_request.update(kwargs)
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
    assert provider_request["messages"] == [
        {"role": "user", "content": DIRECT_TASK.question}
    ]
    assert DIRECT_TASK.question not in provider_request["system"]


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


def test_laguna_nullable_content_projection_is_output_invalid_not_harness_error() -> None:
    async def transport(**_kwargs: Any) -> ModelResponse:
        return replace(
            _response(""),
            provider_metrics={
                "provider_turn_status": "tool_calls",
                "model_output_error": True,
                "model_output_subtype": "TOOL_CALL_ONLY",
            },
        )

    outcome, sample, record = asyncio.run(
        run_direct_model_task_v2(
            coordinator=FakeCoordinator(CypherResult(success=True, raw={})),
            task=DIRECT_TASK,
            oracle=DIRECT_ORACLE,
            resolver=DIRECT_RESOLVER,
            model="openai-compat/poolside/laguna-s-2.1",
            transport=transport,
        )
    )

    assert outcome is None
    assert sample.execution_class is ExecutionClass.MODEL_FAILURE
    assert sample.outcome is SampleOutcomeCode.OUTPUT_INVALID
    assert sample.outcome is not SampleOutcomeCode.HARNESS_ERROR
    assert record.provider_metrics["model_output_subtype"] == "TOOL_CALL_ONLY"


def test_provider_protocol_error_is_nonretryable_infrastructure() -> None:
    assert model_runtime._provider_infrastructure_details(
        ProviderProtocolError("missing choices")
    ) == ("PROVIDER_PROTOCOL", False)


@pytest.mark.parametrize("status_code", (301, 307, 400))
def test_mcp_http_status_is_nonretryable_provider_protocol_not_harness_error(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
) -> None:
    async def rejected_loop(**_kwargs: Any):
        request = httpx.Request(
            "POST",
            "https://inference-api.nousresearch.com/v1/chat/completions",
        )
        response = httpx.Response(status_code, request=request)
        raise httpx.HTTPStatusError(
            "provider rejected request",
            request=request,
            response=response,
        )

    monkeypatch.setattr(
        model_runtime,
        "_run_openai_compat_mcp_loop",
        rejected_loop,
    )
    outcome, record = asyncio.run(
        run_mcp_model_task_v2(
            task=MCP_TASK,
            oracle=MCP_ORACLE,
            resolver=MCP_RESOLVER,
            profile=PROFILE,
            bundle=MCPServerBundle(tools=[]),
            model="openai-compat/poolside/laguna-s-2.1",
            model_base_url="https://inference-api.nousresearch.com/v1",
            tool_loop=MCPToolLoop.NATIVE_OPENAI_COMPATIBLE,
            max_steps=4,
        )
    )

    assert outcome.sample.execution_class is ExecutionClass.INFRA_FAILURE
    assert outcome.sample.outcome is SampleOutcomeCode.INFRA_ERROR
    assert outcome.sample.outcome is not SampleOutcomeCode.HARNESS_ERROR
    assert record.provider_metrics["infra_scope"] == "provider"
    assert record.provider_metrics["infra_error_subtype"] == "PROVIDER_PROTOCOL"
    assert record.provider_metrics["infra_retryable"] is False


def test_nonretryable_provider_attempt_is_terminal_on_resume() -> None:
    sample = SampleResult(
        task_id=DIRECT_TASK.task_id,
        task_fingerprint=DIRECT_TASK.task_fingerprint,
        oracle_fingerprint=DIRECT_ORACLE.oracle_fingerprint,
        execution_class=ExecutionClass.INFRA_FAILURE,
        outcome=SampleOutcomeCode.INFRA_ERROR,
        reasoning_correct=None,
        detail="provider protocol mismatch",
    )
    provider = model_runtime._record(
        task=DIRECT_TASK,
        model="openai-compat/test",
        surface="direct",
        response=replace(
            _response("", error="missing choices"),
            provider_metrics={
                "infra_scope": "provider",
                "infra_error_subtype": "PROVIDER_PROTOCOL",
                "infra_retryable": False,
            },
        ),
    )
    attempt = campaign_runner._attempt(
        task_id=DIRECT_TASK.task_id,
        number=1,
        sample=sample,
        provider=provider,
    )

    assert campaign_runner._attempt_is_terminal_on_resume(attempt) is True


def test_operator_interruption_is_not_terminal_on_resume() -> None:
    sample = SampleResult(
        task_id=DIRECT_TASK.task_id,
        task_fingerprint=DIRECT_TASK.task_fingerprint,
        oracle_fingerprint=DIRECT_ORACLE.oracle_fingerprint,
        execution_class=ExecutionClass.UNEXECUTED,
        outcome=SampleOutcomeCode.INTERRUPTED,
        reasoning_correct=None,
        detail="interrupted",
    )
    provider = model_runtime._record(
        task=DIRECT_TASK,
        model="codex/gpt-test",
        surface="direct",
        response=replace(
            _response("", error="interrupted"),
            provider_metrics={
                "infra_scope": "operator",
                "infra_error_subtype": "INTERRUPTED",
                "infra_retryable": False,
            },
        ),
    )
    attempt = campaign_runner._attempt(
        task_id=DIRECT_TASK.task_id,
        number=1,
        sample=sample,
        provider=provider,
    )

    assert campaign_runner._attempt_is_terminal_on_resume(attempt) is False


def test_untyped_provider_error_response_fails_closed_without_string_inference() -> None:
    async def transport(**_kwargs: Any) -> ModelResponse:
        return _response("", error="HTTP 403 Forbidden permission_denied")

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
    assert sample.execution_class is ExecutionClass.INFRA_FAILURE
    assert record.provider_metrics["infra_scope"] == "provider"
    assert record.provider_metrics["infra_error_subtype"] == "PROVIDER_UNTYPED"
    assert record.provider_metrics["infra_retryable"] is False


def test_direct_provider_cancellation_carries_private_attempt() -> None:
    async def stalled_transport(**_kwargs: Any) -> ModelResponse:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def cancel() -> V2ModelTaskCancelled:
        task = asyncio.create_task(
            run_direct_model_task_v2(
                coordinator=FakeCoordinator(CypherResult(success=True, raw={})),
                task=DIRECT_TASK,
                oracle=DIRECT_ORACLE,
                resolver=DIRECT_RESOLVER,
                model="codex/gpt-test",
                transport=stalled_transport,
            )
        )
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except V2ModelTaskCancelled as exc:
            return exc
        raise AssertionError("direct provider cancellation did not preserve an attempt")

    cancellation = asyncio.run(cancel())
    assert cancellation.sample.outcome is SampleOutcomeCode.INTERRUPTED
    assert cancellation.provider.provider_metrics["infra_scope"] == "operator"
    assert cancellation.provider.direct_query_digest is None


def test_direct_execution_cancellation_preserves_model_query() -> None:
    query = "MATCH p=(a)-[:MemberOf]->(b) RETURN p LIMIT 1"

    async def transport(**_kwargs: Any) -> ModelResponse:
        return _response(json.dumps({"query": query, "assertion": {}}))

    class StalledCoordinator(FakeCoordinator):
        async def execute(self, submitted_query: str) -> CypherResult:
            self.queries.append(submitted_query)
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    coordinator = StalledCoordinator(CypherResult(success=True, raw={}))

    async def cancel() -> V2ModelTaskCancelled:
        task = asyncio.create_task(
            run_direct_model_task_v2(
                coordinator=coordinator,
                task=DIRECT_TASK,
                oracle=DIRECT_ORACLE,
                resolver=DIRECT_RESOLVER,
                model="codex/gpt-test",
                transport=transport,
            )
        )
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except V2ModelTaskCancelled as exc:
            return exc
        raise AssertionError("direct execution cancellation did not preserve an attempt")

    cancellation = asyncio.run(cancel())
    assert cancellation.sample.outcome is SampleOutcomeCode.INTERRUPTED
    assert cancellation.provider.direct_query_digest is not None
    assert coordinator.queries == [query]


def test_claim_bound_cypher_route_is_positive_evidence() -> None:
    projector = MCPTranscriptProjector(MCP_TASK, PROFILE)
    _observe_route_evidence(projector.observe)
    ready = projector.finalization_ready

    assert ready is True
    assert projector.events[-1].kind is EvidenceEventKind.USEFUL_POSITIVE


def test_irrelevant_observation_preserves_cumulative_finalization_readiness() -> None:
    projector = MCPTranscriptProjector(MCP_TASK, PROFILE)
    _observe_route_evidence(projector.observe)

    ready = projector.observe(
        "group_info",
        {
            "group_id": "GROUP-B",
            "info_type": "info",
        },
        json.dumps(
            {
                "success": True,
                "data": {"name": "GROUP-B"},
            }
        ),
        None,
    )

    assert projector.events[-1].kind is EvidenceEventKind.IRRELEVANT
    assert ready is True
    assert projector.finalization_ready is True


def test_projector_records_only_successful_cypher_node_object_ids() -> None:
    projector = MCPTranscriptProjector(MCP_TASK, PROFILE)
    projector.observe(
        "cypher_query",
        {
            "info_type": "run",
            "query": (
                "MATCH p=(a {objectid:'USER-A'})-[:MemberOf]->"
                "(b {objectid:'GROUP-B'}) RETURN p"
            ),
        },
        json.dumps(
            {
                "success": True,
                "data": {
                    "nodes": {
                        "0": {"objectId": "USER-A"},
                        "1": {"properties": {"objectid": "GROUP-B"}},
                    },
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
    projector.observe(
        "cypher_query",
        {"info_type": "run", "query": "MATCH invalid"},
        json.dumps(
            {
                "success": False,
                "error": "syntax error",
                "error_type": "syntax_error",
                "data": {"nodes": {"0": {"objectid": "MUST-NOT-TRUST"}}},
            }
        ),
        None,
    )
    projector.observe(
        "graph_analysis",
        {"info_type": "shortest_path"},
        json.dumps(
            {
                "success": True,
                "data": {"nodes": {"0": {"objectid": "HIGH-LEVEL-NOT-PROOF"}}},
            }
        ),
        None,
    )

    assert projector.observed_identity_ids == {"USER-A", "GROUP-B"}


def test_projector_records_successful_literal_row_object_ids() -> None:
    task = _public_selection_task(
        SelectionExpression(
            projection_role="result",
            projection_type="User",
        )
    )
    projector = MCPTranscriptProjector(task, PROFILE)
    projector.observe(
        "cypher_query",
        {
            "info_type": "run",
            "query": (
                "MATCH (u:User) RETURN u.objectid AS object_id "
                "ORDER BY u.objectid SKIP 0 LIMIT 1"
            ),
        },
        json.dumps(
            {
                "success": True,
                "data": {
                    "nodes": {},
                    "edges": [],
                    "literals": [
                        {"key": "object_id", "value": "USER-LITERAL"},
                    ],
                },
                "node_count": 0,
                "edge_count": 0,
            }
        ),
        None,
    )

    assert projector.observed_identity_ids == {"USER-LITERAL"}


def test_identity_catalog_miss_is_not_treated_as_schema_retryable() -> None:
    answer = _answer()
    answer["edges"][0]["source_id"] = "S-1-5-21-UNKNOWN"

    assert model_runtime._answer_schema_valid(
        MCP_TASK,
        answer,
        resolver=MCP_RESOLVER,
    )


@pytest.mark.parametrize(
    ("claim_kind", "answer_policy"),
    [
        ("route", MCP_TASK.answer_policy),
        ("decision", DecisionPolicy(kind="decision")),
    ],
)
def test_claim_bound_witness_with_graph_and_scalar_literals_is_positive_evidence(
    claim_kind: str,
    answer_policy,
) -> None:
    task = MCP_TASK.model_copy(
        update={
            "claim_kind": claim_kind,
            "answer_policy": answer_policy,
        }
    )
    projector = MCPTranscriptProjector(task, PROFILE)
    ready = projector.observe(
        "cypher_query",
        {
            "info_type": "run",
            "query": (
                "MATCH p=(a {objectid:'USER-A'})-[:MemberOf]->"
                "(b {objectid:'GROUP-B'}) "
                "RETURN p, a.objectid AS source_id, b.objectid AS target_id"
            ),
        },
        json.dumps(
            {
                "info_type": "run",
                "success": True,
                "has_results": True,
                "data": {
                    "nodes": {
                        "0": {"objectid": "USER-A"},
                        "1": {"objectid": "GROUP-B"},
                    },
                    "edges": [
                        {
                            "source": "0",
                            "target": "1",
                            "kind": "MemberOf",
                        }
                    ],
                    "literals": [
                        {"key": "source_id", "value": "USER-A"},
                        {"key": "target_id", "value": "GROUP-B"},
                    ],
                },
                "node_count": 2,
                "edge_count": 1,
            }
        ),
        None,
    )

    assert ready is True
    receipt = projector.receipts[-1]
    assert receipt.observation.result_count == 1
    assert receipt.observation.complete is False
    assert receipt.event.kind is EvidenceEventKind.USEFUL_POSITIVE


def test_claim_bound_route_with_only_unknown_literals_remains_inconclusive() -> None:
    projector = MCPTranscriptProjector(MCP_TASK, PROFILE)
    ready = projector.observe(
        "cypher_query",
        {
            "info_type": "run",
            "query": (
                "MATCH p=(a {objectid:'USER-A'})-[:MemberOf]->"
                "(b {objectid:'GROUP-B'}) RETURN p"
            ),
        },
        json.dumps(
            {
                "info_type": "run",
                "success": True,
                "has_results": True,
                "data": {
                    "nodes": {"0": {"objectid": "USER-A"}},
                    "edges": [],
                    "literals": [{"key": "nodes", "value": ["USER-A", "GROUP-B"]}],
                },
                "node_count": 0,
                "edge_count": 0,
            }
        ),
        None,
    )

    assert ready is False
    receipt = projector.receipts[-1]
    assert receipt.observation.result_count is None
    assert receipt.observation.claim_relevant is False
    assert receipt.event.kind is EvidenceEventKind.IRRELEVANT


def test_claim_bound_route_with_node_only_result_cannot_unlock() -> None:
    projector = MCPTranscriptProjector(MCP_TASK, PROFILE)
    ready = projector.observe(
        "cypher_query",
        {
            "info_type": "run",
            "query": (
                "MATCH p=(a {objectid:'USER-A'})-[:MemberOf]->"
                "(b {objectid:'GROUP-B'}) RETURN p"
            ),
        },
        json.dumps(
            {
                "success": True,
                "data": {
                    "nodes": {"0": {"objectid": "USER-X"}},
                    "edges": [],
                },
                "node_count": 1,
                "edge_count": 0,
            }
        ),
        None,
    )

    assert ready is False
    receipt = projector.receipts[-1]
    assert receipt.observation.claim_relevant is False
    assert receipt.event.kind is EvidenceEventKind.IRRELEVANT


def test_comment_only_claim_markers_cannot_unlock_mcp_evidence() -> None:
    task = MCP_TASK.model_copy(
        update={
            "binding": MCP_TASK.binding.model_copy(
                update={
                    "mcp_evidence_contract": MCPClaimEvidenceContract(
                        result_kind="path",
                        required_input_roles=("source", "target"),
                    )
                }
            )
        }
    )
    projector = MCPTranscriptProjector(task, PROFILE)
    result = json.dumps(
        {
            "success": True,
            "data": {
                "nodes": {
                    "0": {"objectid": "USER-A"},
                    "1": {"objectid": "GROUP-B"},
                },
                "edges": [{"source": "0", "target": "1", "kind": "MemberOf"}],
            },
            "node_count": 2,
            "edge_count": 1,
        }
    )
    ready = projector.observe(
        "cypher_query",
        {
            "info_type": "run",
            "query": (
                "MATCH (a {objectid:'USER-A'}), "
                "(b {objectid:'GROUP-B'}) RETURN a, b "
                "// MATCH p=(a)-[:MemberOf]->(b) RETURN p"
            ),
        },
        result,
        None,
    )

    assert ready is False
    assert projector.events[-1].kind is EvidenceEventKind.IRRELEVANT
    ready = projector.observe(
        "cypher_query",
        {
            "info_type": "run",
            "query": (
                "MATCH p=(a {objectid:'USER-A'})-[:MemberOf]->"
                "(b {objectid:'GROUP-B'}) "
                "WHERE a.enabled IS NOT NULL RETURN p"
            ),
        },
        result,
        None,
    )
    assert ready is True
    assert projector.events[-1].kind is EvidenceEventKind.USEFUL_POSITIVE


def test_unrelated_high_level_result_cannot_unlock_route() -> None:
    projector = MCPTranscriptProjector(MCP_TASK, PROFILE)
    ready = projector.observe(
        "domain_info",
        {"info_type": "list"},
        json.dumps({"data": {"unrelated": "metadata"}}),
        None,
    )

    assert ready is False
    assert projector.events[-1].kind is EvidenceEventKind.IRRELEVANT


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
            "binding": _cypher_set_binding(
                bounds=bounds,
                projection_type="User",
            ),
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
    assert projector.events[-1].kind is EvidenceEventKind.IRRELEVANT

    assert (
        projector.observe(
            "cypher_query",
            {
                "info_type": "run",
                "query": ("MATCH (n:User) RETURN n ORDER BY n.objectid LIMIT 2"),
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


def test_exact_set_companion_count_can_arrive_after_the_complete_page() -> None:
    bounds = MCP_TASK.binding.bounds.model_copy(
        update={
            "max_result_cardinality": 2,
            "page_size": 2,
            "require_total_count": True,
        }
    )
    set_task = MCP_TASK.model_copy(
        update={
            "task_id": "simple.mcp.set-page-first@2",
            "claim_kind": "set",
            "answer_policy": ExactSetPolicy(kind="exact_set"),
            "binding": _cypher_set_binding(
                bounds=bounds,
                projection_type="User",
            ),
        }
    )
    projector = MCPTranscriptProjector(set_task, PROFILE)

    assert (
        projector.observe(
            "cypher_query",
            {
                "info_type": "run",
                "query": (
                    "MATCH (n:User) RETURN n.objectid AS object_id "
                    "ORDER BY n.objectid LIMIT 2"
                ),
            },
            json.dumps(
                {
                    "info_type": "run",
                    "success": True,
                    "data": {
                        "nodes": {},
                        "edges": [],
                        "literals": [
                            {"key": "object_id", "value": "USER-A"},
                            {"key": "object_id", "value": "USER-B"},
                        ],
                    },
                    "node_count": 0,
                    "edge_count": 0,
                }
            ),
            None,
        )
        is False
    )
    assert projector.events[-1].kind is EvidenceEventKind.TRUNCATED

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
        is True
    )
    assert projector.events[-1].kind is EvidenceEventKind.USEFUL_POSITIVE
    assert projector.receipts[-1].observation.complete is True
    assert projector.receipts[-1].observation.result_count == 2
    assert projector.receipts[-1].observation.total_count == 2


def test_truncated_set_receipts_cannot_supply_later_completeness_state() -> None:
    bounds = MCP_TASK.binding.bounds.model_copy(
        update={
            "max_result_cardinality": 2,
            "page_size": 2,
            "require_total_count": True,
        }
    )
    set_task = MCP_TASK.model_copy(
        update={
            "task_id": "simple.mcp.truncated-proof-state@2",
            "claim_kind": "set",
            "answer_policy": ExactSetPolicy(kind="exact_set"),
            "binding": _cypher_set_binding(
                bounds=bounds,
                projection_type="User",
            ),
        }
    )
    page_query = (
        "MATCH (n:User) RETURN n.objectid AS object_id "
        "ORDER BY n.objectid LIMIT 2"
    )
    count_query = "MATCH (n:User) RETURN count(n) AS count"
    page_payload = json.dumps(
        {
            "success": True,
            "data": {
                "literals": [
                    {"key": "object_id", "value": "USER-A"},
                    {"key": "object_id", "value": "USER-B"},
                ],
            },
            "node_count": 0,
            "edge_count": 0,
            "truncated": True,
        }
    )
    count_payload = json.dumps(
        {
            "success": True,
            "data": {"literals": [{"key": "count", "value": 2}]},
            "node_count": 0,
            "edge_count": 0,
        }
    )

    truncated_page = MCPTranscriptProjector(set_task, PROFILE)
    assert (
        truncated_page.observe(
            "cypher_query",
            {"info_type": "run", "query": page_query},
            page_payload,
            None,
        )
        is False
    )
    assert truncated_page.page_counts == {}
    assert (
        truncated_page.observe(
            "cypher_query",
            {"info_type": "run", "query": count_query},
            count_payload,
            None,
        )
        is False
    )

    truncated_count = MCPTranscriptProjector(set_task, PROFILE)
    truncated_count_payload = json.loads(count_payload)
    truncated_count_payload["truncated"] = True
    assert (
        truncated_count.observe(
            "cypher_query",
            {"info_type": "run", "query": count_query},
            json.dumps(truncated_count_payload),
            None,
        )
        is False
    )
    assert truncated_count.total_count is None
    assert (
        truncated_count.observe(
            "cypher_query",
            {"info_type": "run", "query": page_query},
            page_payload.replace('"truncated": true', '"truncated": false'),
            None,
        )
        is False
    )


def test_failed_set_receipts_cannot_supply_later_completeness_state() -> None:
    bounds = MCP_TASK.binding.bounds.model_copy(
        update={
            "max_result_cardinality": 2,
            "page_size": 2,
            "require_total_count": True,
        }
    )
    set_task = MCP_TASK.model_copy(
        update={
            "task_id": "simple.mcp.failed-proof-state@2",
            "claim_kind": "set",
            "answer_policy": ExactSetPolicy(kind="exact_set"),
            "binding": _cypher_set_binding(
                bounds=bounds,
                projection_type="User",
            ),
        }
    )
    page_query = (
        "MATCH (n:User) RETURN n.objectid AS object_id "
        "ORDER BY n.objectid LIMIT 2"
    )
    count_query = "MATCH (n:User) RETURN count(n) AS count"
    page_data = {
        "data": {
            "literals": [
                {"key": "object_id", "value": "USER-A"},
                {"key": "object_id", "value": "USER-B"},
            ],
        },
        "node_count": 0,
        "edge_count": 0,
    }
    count_data = {
        "data": {"literals": [{"key": "count", "value": 2}]},
        "node_count": 0,
        "edge_count": 0,
    }
    failure = {
        "success": False,
        "error": "syntax error",
        "error_type": "syntax_error",
    }

    failed_page = MCPTranscriptProjector(set_task, PROFILE)
    assert (
        failed_page.observe(
            "cypher_query",
            {"info_type": "run", "query": page_query},
            json.dumps({**page_data, **failure}),
            None,
        )
        is False
    )
    assert failed_page.page_counts == {}
    assert (
        failed_page.observe(
            "cypher_query",
            {"info_type": "run", "query": count_query},
            json.dumps({**count_data, "success": True}),
            None,
        )
        is False
    )

    failed_count = MCPTranscriptProjector(set_task, PROFILE)
    assert (
        failed_count.observe(
            "cypher_query",
            {"info_type": "run", "query": count_query},
            json.dumps({**count_data, **failure}),
            None,
        )
        is False
    )
    assert failed_count.total_count is None
    assert (
        failed_count.observe(
            "cypher_query",
            {"info_type": "run", "query": page_query},
            json.dumps({**page_data, "success": True}),
            None,
        )
        is False
    )


def test_certification_companion_count_matches_distinct_set_window() -> None:
    bounds = MCP_TASK.binding.bounds.model_copy(
        update={"require_total_count": True}
    )
    set_task = MCP_TASK.model_copy(
        update={
            "claim_kind": "set",
            "answer_policy": ExactSetPolicy(kind="exact_set"),
            "binding": _cypher_set_binding(
                bounds=bounds,
                projection_type="User",
            ),
        }
    )

    query = _mcp_fixture_query(set_task, count=True)

    assert "RETURN count(DISTINCT result) AS certified_count" in query


def test_count_projection_accepts_one_unambiguous_scalar_alias() -> None:
    bounds = MCP_TASK.binding.bounds.model_copy(
        update={"require_total_count": True}
    )
    task = MCP_TASK.model_copy(
        update={
            "claim_kind": "set",
            "answer_policy": ExactSetPolicy(kind="exact_set"),
            "binding": _cypher_set_binding(
                bounds=bounds,
                projection_type="User",
            )
        }
    )
    projector = MCPTranscriptProjector(task, PROFILE)

    projector.observe(
        "cypher_query",
        {
            "info_type": "run",
            "query": "MATCH (n:User) RETURN count(n) AS member_count",
        },
        json.dumps(
            {
                "success": True,
                "data": {
                    "literals": [
                        {"key": "member_count", "value": 2},
                    ]
                },
            }
        ),
        None,
    )

    assert projector.total_count == 2


def test_count_star_accepts_one_unambiguous_typed_population() -> None:
    task = MCP_TASK.model_copy(
        update={
            "claim_kind": "count",
            "answer_policy": ExactCountPolicy(kind="exact_count"),
            "binding": MCP_TASK.binding.model_copy(
                update={
                    "mcp_evidence_contract": MCPClaimEvidenceContract(
                        result_kind="scalar_count",
                        projection_types=("User",),
                    )
                }
            ),
        }
    )
    projector = MCPTranscriptProjector(task, PROFILE)

    ready = projector.observe(
        "cypher_query",
        {
            "info_type": "run",
            "query": "MATCH (u:User) RETURN count(*) AS count",
        },
        json.dumps(
            {
                "success": True,
                "data": {"literals": [{"key": "count", "value": 3}]},
            }
        ),
        None,
    )

    assert ready is True
    assert projector.total_count == 3
    assert projector.events[-1].kind is EvidenceEventKind.USEFUL_POSITIVE


def test_count_star_accepts_one_selector_bound_typed_population() -> None:
    source = MCP_TASK.input_entities[0]
    task = MCP_TASK.model_copy(
        update={
            "claim_kind": "count",
            "answer_policy": ExactCountPolicy(kind="exact_count"),
            "binding": MCP_TASK.binding.model_copy(
                update={
                    "mcp_evidence_contract": MCPClaimEvidenceContract(
                        result_kind="scalar_count",
                        required_input_roles=("source",),
                        projection_types=("User",),
                    )
                }
            ),
        }
    )
    projector = MCPTranscriptProjector(task, PROFILE)

    ready = projector.observe(
        "cypher_query",
        {
            "info_type": "run",
            "query": (
                f"MATCH (u:User {{objectid: '{source.object_id}'}}) "
                "RETURN count(*) AS count"
            ),
        },
        json.dumps(
            {
                "success": True,
                "data": {"literals": [{"key": "count", "value": 1}]},
            }
        ),
        None,
    )

    assert ready is True
    assert projector.total_count == 1
    assert projector.events[-1].kind is EvidenceEventKind.USEFUL_POSITIVE


def test_count_star_accepts_one_anonymous_typed_population() -> None:
    task = MCP_TASK.model_copy(
        update={
            "claim_kind": "count",
            "answer_policy": ExactCountPolicy(kind="exact_count"),
            "binding": MCP_TASK.binding.model_copy(
                update={
                    "mcp_evidence_contract": MCPClaimEvidenceContract(
                        result_kind="scalar_count",
                        projection_types=("User",),
                    )
                }
            ),
        }
    )
    projector = MCPTranscriptProjector(task, PROFILE)

    ready = projector.observe(
        "cypher_query",
        {
            "info_type": "run",
            "query": "MATCH (:User) RETURN count(*) AS count",
        },
        json.dumps(
            {
                "success": True,
                "data": {"literals": [{"key": "count", "value": 3}]},
            }
        ),
        None,
    )

    assert ready is True
    assert projector.total_count == 3
    assert projector.events[-1].kind is EvidenceEventKind.USEFUL_POSITIVE


def test_count_star_rejects_ambiguous_typed_populations() -> None:
    task = MCP_TASK.model_copy(
        update={
            "claim_kind": "count",
            "answer_policy": ExactCountPolicy(kind="exact_count"),
            "binding": MCP_TASK.binding.model_copy(
                update={
                    "mcp_evidence_contract": MCPClaimEvidenceContract(
                        result_kind="scalar_count",
                        projection_types=("User",),
                    )
                }
            ),
        }
    )
    projector = MCPTranscriptProjector(task, PROFILE)

    ready = projector.observe(
        "cypher_query",
        {
            "info_type": "run",
            "query": "MATCH (u:User), (v:User) RETURN count(*) AS count",
        },
        json.dumps(
            {
                "success": True,
                "data": {"literals": [{"key": "count", "value": 3}]},
            }
        ),
        None,
    )

    assert ready is False
    assert projector.total_count is None
    assert projector.events[-1].kind is EvidenceEventKind.IRRELEVANT


def test_count_star_rejects_fanout_row_populations() -> None:
    task = MCP_TASK.model_copy(
        update={
            "claim_kind": "count",
            "answer_policy": ExactCountPolicy(kind="exact_count"),
            "binding": MCP_TASK.binding.model_copy(
                update={
                    "mcp_evidence_contract": MCPClaimEvidenceContract(
                        result_kind="scalar_count",
                        projection_types=("User",),
                    )
                }
            ),
        }
    )
    projector = MCPTranscriptProjector(task, PROFILE)

    ready = projector.observe(
        "cypher_query",
        {
            "info_type": "run",
            "query": (
                "MATCH (u:User)-[:MemberOf]->(g:Group) "
                "RETURN count(*) AS count"
            ),
        },
        json.dumps(
            {
                "success": True,
                "data": {"literals": [{"key": "count", "value": 3}]},
            }
        ),
        None,
    )

    assert ready is False
    assert projector.total_count is None
    assert projector.events[-1].kind is EvidenceEventKind.IRRELEVANT


@pytest.mark.parametrize(
    "query",
    (
        "MATCH (u:User)-->() RETURN count(*) AS count",
        "MATCH (u:User)--(:Group) RETURN count(*) AS count",
    ),
)
def test_count_star_rejects_anonymous_fanout_row_populations(query: str) -> None:
    task = MCP_TASK.model_copy(
        update={
            "claim_kind": "count",
            "answer_policy": ExactCountPolicy(kind="exact_count"),
            "binding": MCP_TASK.binding.model_copy(
                update={
                    "mcp_evidence_contract": MCPClaimEvidenceContract(
                        result_kind="scalar_count",
                        projection_types=("User",),
                    )
                }
            ),
        }
    )
    projector = MCPTranscriptProjector(task, PROFILE)

    ready = projector.observe(
        "cypher_query",
        {"info_type": "run", "query": query},
        json.dumps(
            {
                "success": True,
                "data": {"literals": [{"key": "count", "value": 3}]},
            }
        ),
        None,
    )

    assert ready is False
    assert projector.total_count is None
    assert projector.events[-1].kind is EvidenceEventKind.IRRELEVANT


def test_count_projection_accepts_limit_one_and_collected_entity_page() -> None:
    bounds = MCP_TASK.binding.bounds.model_copy(
        update={
            "max_result_cardinality": 2,
            "page_size": 2,
            "max_pages": 1,
            "require_total_count": True,
        }
    )
    task = MCP_TASK.model_copy(
        update={
            "claim_kind": "set",
            "answer_policy": ExactSetPolicy(kind="exact_set"),
            "binding": _cypher_set_binding(
                bounds=bounds,
                projection_type="User",
            ),
        }
    )
    projector = MCPTranscriptProjector(task, PROFILE)
    projector.observe(
        "cypher_query",
        {
            "info_type": "run",
            "query": "MATCH (n:User) RETURN count(n) AS total LIMIT 1",
        },
        json.dumps(
            {
                "success": True,
                "data": {"literals": [{"key": "total", "value": 2}]},
                "node_count": 0,
                "edge_count": 0,
            }
        ),
        None,
    )
    ready = projector.observe(
        "cypher_query",
        {
            "info_type": "run",
            "query": (
                "MATCH (n:User) RETURN collect(n) AS entities ORDER BY n.objectid SKIP 0 LIMIT 2"
            ),
        },
        json.dumps(
            {
                "success": True,
                "data": {
                    "nodes": {"aux": {"objectid": "AUXILIARY"}},
                    "edges": [],
                    "literals": [
                        {
                            "key": "entities",
                            "value": [
                                {"objectid": "USER-A"},
                                {"objectid": "USER-B"},
                            ],
                        }
                    ],
                },
                "node_count": 1,
                "edge_count": 0,
            }
        ),
        None,
    )

    assert projector.total_count == 2
    assert ready is True
    assert projector.receipts[-1].observation.result_count == 2


def test_count_projection_rejects_multiple_scalar_literals() -> None:
    task = MCP_TASK.model_copy(
        update={
            "binding": _cypher_set_binding(
                bounds=MCP_TASK.binding.bounds,
                projection_type="User",
            )
        }
    )
    projector = MCPTranscriptProjector(task, PROFILE)

    projector.observe(
        "cypher_query",
        {
            "info_type": "run",
            "query": "MATCH (n:User) RETURN count(n) AS a, count(n) AS b",
        },
        json.dumps(
            {
                "success": True,
                "data": {
                    "literals": [
                        {"key": "a", "value": 2},
                        {"key": "b", "value": 2},
                    ]
                },
            }
        ),
        None,
    )

    assert projector.total_count is None


def test_count_projection_rejects_non_count_only_query() -> None:
    task = MCP_TASK.model_copy(
        update={
            "binding": _cypher_set_binding(
                bounds=MCP_TASK.binding.bounds,
                projection_type="User",
            )
        }
    )
    projector = MCPTranscriptProjector(task, PROFILE)

    projector.observe(
        "cypher_query",
        {
            "info_type": "run",
            "query": "MATCH (n:User) RETURN count(n) AS ignored, 2 AS member_count",
        },
        json.dumps(
            {
                "success": True,
                "data": {
                    "literals": [
                        {"key": "member_count", "value": 2},
                    ]
                },
            }
        ),
        None,
    )

    assert projector.total_count is None
    assert projector.receipts[-1].observation.claim_relevant is False
    assert projector.events[-1].kind is EvidenceEventKind.IRRELEVANT


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
            "binding": _cypher_set_binding(
                bounds=bounds,
                projection_type="User",
            ),
        }
    )
    projector = MCPTranscriptProjector(set_task, PROFILE)
    projector.observe(
        "cypher_query",
        {
            "info_type": "run",
            "query": "MATCH (:User) RETURN count(*) AS count",
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
                "query": ("MATCH (n:User) RETURN n ORDER BY n.objectid LIMIT 2"),
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
                "query": ("MATCH (u:User) RETURN u ORDER BY u.objectid SKIP 2 LIMIT 2"),
            },
            json.dumps({"success": True, "node_count": 1, "edge_count": 0}),
            None,
        )
        is True
    )
    assert projector.events[-1].kind is EvidenceEventKind.USEFUL_POSITIVE


def test_exact_set_population_key_preserves_post_with_filters() -> None:
    bounds = MCP_TASK.binding.bounds.model_copy(
        update={
            "max_result_cardinality": 3,
            "page_size": 3,
            "max_pages": 1,
            "require_total_count": True,
        }
    )
    set_task = MCP_TASK.model_copy(
        update={
            "task_id": "simple.mcp.filtered-set@2",
            "claim_kind": "set",
            "answer_policy": ExactSetPolicy(kind="exact_set"),
            "binding": _cypher_set_binding(
                bounds=bounds,
                projection_type="User",
            ),
        }
    )

    mismatched = MCPTranscriptProjector(set_task, PROFILE)
    mismatched.observe(
        "cypher_query",
        {
            "info_type": "run",
            "query": "MATCH (n:User) RETURN count(*) AS count",
        },
        json.dumps(
            {
                "success": True,
                "data": {"literals": [{"key": "count", "value": 2}]},
                "node_count": 0,
                "edge_count": 0,
            }
        ),
        None,
    )
    assert (
        mismatched.observe(
            "cypher_query",
            {
                "info_type": "run",
                "query": (
                    "MATCH (u:User) WITH u WHERE u.enabled = true "
                    "RETURN u ORDER BY u.objectid LIMIT 3"
                ),
            },
            json.dumps({"success": True, "node_count": 2, "edge_count": 0}),
            None,
        )
        is False
    )
    assert mismatched.receipts[-1].observation.total_count is None

    matching = MCPTranscriptProjector(set_task, PROFILE)
    matching.observe(
        "cypher_query",
        {
            "info_type": "run",
            "query": (
                "MATCH (n:User) WITH n WHERE n.enabled = true "
                "RETURN count(*) AS count"
            ),
        },
        json.dumps(
            {
                "success": True,
                "data": {"literals": [{"key": "count", "value": 2}]},
                "node_count": 0,
                "edge_count": 0,
            }
        ),
        None,
    )
    assert (
        matching.observe(
            "cypher_query",
            {
                "info_type": "run",
                "query": (
                    "MATCH (u:User) WITH u WHERE u.enabled = true "
                    "RETURN u ORDER BY u.objectid LIMIT 3"
                ),
            },
            json.dumps({"success": True, "node_count": 2, "edge_count": 0}),
            None,
        )
        is True
    )
    assert matching.events[-1].kind is EvidenceEventKind.USEFUL_POSITIVE


def test_exact_set_population_key_accepts_multiline_distinct_aliases() -> None:
    bounds = MCP_TASK.binding.bounds.model_copy(
        update={
            "max_result_cardinality": 3,
            "page_size": 3,
            "max_pages": 1,
            "require_total_count": True,
        }
    )
    set_task = MCP_TASK.model_copy(
        update={
            "task_id": "simple.mcp.multiline-distinct-set@2",
            "claim_kind": "set",
            "answer_policy": ExactSetPolicy(kind="exact_set"),
            "binding": _cypher_set_binding(
                bounds=bounds,
                projection_type="User",
            ),
        }
    )
    projector = MCPTranscriptProjector(set_task, PROFILE)
    projector.observe(
        "cypher_query",
        {
            "info_type": "run",
            "query": (
                "MATCH (u:User)-[:MemberOf]->(:Group)\n"
                "RETURN count(DISTINCT u.objectid) AS count"
            ),
        },
        json.dumps(
            {
                "success": True,
                "data": {"literals": [{"key": "count", "value": 2}]},
                "node_count": 0,
                "edge_count": 0,
            }
        ),
        None,
    )

    ready = projector.observe(
        "cypher_query",
        {
            "info_type": "run",
            "query": (
                "MATCH (member:User)-[:MemberOf]->(:Group)\n"
                "WITH DISTINCT member AS entity\n"
                "RETURN entity.objectid AS object_id, entity.name AS name\n"
                "ORDER BY entity.objectid SKIP 0 LIMIT 3"
            ),
        },
        json.dumps({"success": True, "node_count": 2, "edge_count": 0}),
        None,
    )

    assert ready is True
    assert projector.receipts[-1].observation.total_count == 2
    assert projector.events[-1].kind is EvidenceEventKind.USEFUL_POSITIVE


def test_exact_set_population_key_rejects_distinctness_mismatch() -> None:
    count_query = "MATCH (u:User)-[:MemberOf]->(:Group) RETURN count(u) AS count"
    distinct_page = (
        "MATCH (member:User)-[:MemberOf]->(:Group) "
        "WITH DISTINCT member AS entity "
        "RETURN entity.objectid AS object_id "
        "ORDER BY entity.objectid LIMIT 3"
    )

    assert (
        model_runtime._query_population_key(count_query)
        != model_runtime._query_population_key(distinct_page)
    )
    assert (
        model_runtime._count_population_matches_page(
            model_runtime._query_population_key(count_query),
            model_runtime._query_population_key(distinct_page),
        )
        is False
    )

    distinct_count = (
        "MATCH (u:User)-[:MemberOf]->(:Group) "
        "RETURN count(DISTINCT u) AS count"
    )
    row_page = (
        "MATCH (member:User)-[:MemberOf]->(:Group) "
        "RETURN member.objectid AS object_id "
        "ORDER BY member.objectid LIMIT 3"
    )
    assert (
        model_runtime._count_population_matches_page(
            model_runtime._query_population_key(distinct_count),
            model_runtime._query_population_key(row_page),
        )
        is False
    )


def test_exact_set_population_key_does_not_strip_population_changing_with() -> None:
    count_query = (
        "MATCH (u:User) WITH u ORDER BY u.name LIMIT 2 "
        "RETURN count(*) AS count"
    )
    page_query = (
        "MATCH (member:User) "
        "RETURN member.objectid AS object_id "
        "ORDER BY member.objectid LIMIT 3"
    )

    assert (
        model_runtime._query_population_key(count_query)
        != model_runtime._query_population_key(page_query)
    )


def test_bounded_page_uses_its_public_offset_and_limit_without_hidden_subpages() -> None:
    bounds = MCP_TASK.binding.bounds.model_copy(
        update={
            "max_result_cardinality": 500,
            "page_size": 500,
            "result_offset": 500,
            "max_pages": 1,
            "require_total_count": False,
            "require_stable_ordering": True,
            "max_output_bytes": 524_288,
        }
    )
    page_task = MCP_TASK.model_copy(
        update={
            "task_id": "complex.mcp.page-002@2",
            "claim_kind": "set",
            "answer_policy": ExactSetPolicy(kind="exact_set"),
            "binding": _cypher_set_binding(
                bounds=bounds,
                projection_type="Computer",
            ),
        }
    )
    projector = MCPTranscriptProjector(page_task, PROFILE)

    ready = projector.observe(
        "cypher_query",
        {
            "info_type": "run",
            "include_properties": False,
            "query": ("MATCH (n:Computer) RETURN n ORDER BY n.objectid SKIP 500 LIMIT 500"),
        },
        json.dumps({"success": True, "node_count": 500, "edge_count": 0}),
        None,
    )

    assert ready is True
    assert projector.events[-1].kind is EvidenceEventKind.USEFUL_POSITIVE
    assert projector.page_counts == {500: 500}
    assert projector.receipts[-1].arguments["include_properties"] is False
    assert projector.receipts[-1].result_text


def test_page_bound_violation_revokes_an_earlier_unlock() -> None:
    bounds = MCP_TASK.binding.bounds.model_copy(
        update={
            "max_result_cardinality": 2,
            "page_size": 2,
            "max_pages": 1,
            "require_total_count": False,
        }
    )
    task = MCP_TASK.model_copy(
        update={
            "claim_kind": "set",
            "answer_policy": ExactSetPolicy(kind="exact_set"),
            "binding": _cypher_set_binding(
                bounds=bounds,
                projection_type="User",
            ),
        }
    )
    projector = MCPTranscriptProjector(task, PROFILE)
    first = projector.observe(
        "cypher_query",
        {
            "info_type": "run",
            "query": ("MATCH (n:User) RETURN n ORDER BY n.objectid SKIP 0 LIMIT 2"),
        },
        json.dumps({"success": True, "node_count": 2, "edge_count": 0}),
        None,
    )
    second = projector.observe(
        "cypher_query",
        {
            "info_type": "run",
            "query": ("MATCH (n:User) RETURN n ORDER BY n.objectid SKIP 2 LIMIT 2"),
        },
        json.dumps({"success": True, "node_count": 1, "edge_count": 0}),
        None,
    )

    assert first is True
    assert second is False
    assert projector.events[-1].kind is EvidenceEventKind.TRUNCATED
    assert projector.finalization_ready is False


def test_later_independently_complete_proof_supersedes_earlier_truncation() -> None:
    bounds = MCP_TASK.binding.bounds.model_copy(
        update={
            "max_result_cardinality": 2,
            "page_size": 2,
            "max_pages": 1,
            "require_total_count": False,
        }
    )
    task = MCP_TASK.model_copy(
        update={
            "claim_kind": "set",
            "answer_policy": ExactSetPolicy(kind="exact_set"),
            "binding": _cypher_set_binding(
                bounds=bounds,
                projection_type="User",
            ),
        }
    )
    projector = MCPTranscriptProjector(task, PROFILE)
    assert (
        projector.observe(
            "cypher_query",
            {
                "info_type": "run",
                "query": "MATCH (n:User) RETURN n ORDER BY n.objectid SKIP 0 LIMIT 2",
            },
            json.dumps(
                {
                    "success": True,
                    "node_count": 1,
                    "edge_count": 0,
                    "truncated": True,
                }
            ),
            None,
        )
        is False
    )
    assert projector.events[-1].kind is EvidenceEventKind.TRUNCATED

    ready = projector.observe(
        "cypher_query",
        {
            "info_type": "run",
            "query": "MATCH (entity:User) RETURN entity ORDER BY entity.objectid SKIP 0 LIMIT 2",
        },
        json.dumps({"success": True, "node_count": 2, "edge_count": 0}),
        None,
    )

    assert ready is True
    assert projector.events[-1].kind is EvidenceEventKind.USEFUL_POSITIVE
    assert projector.finalization_ready is True


def test_wrong_public_selectors_and_projection_cannot_unlock() -> None:
    route = MCPTranscriptProjector(MCP_TASK, PROFILE)
    route.observe(
        "cypher_query",
        {
            "info_type": "run",
            "query": ("MATCH p=(a {objectid:'USER-X'})-[*1..1]->(b {objectid:'GROUP-Z'}) RETURN p"),
        },
        json.dumps({"success": True, "node_count": 2, "edge_count": 1}),
        None,
    )
    count_task = MCP_TASK.model_copy(
        update={
            "claim_kind": "count",
            "answer_policy": ExactCountPolicy(kind="exact_count"),
            "binding": MCP_TASK.binding.model_copy(
                update={
                    "mcp_evidence_contract": MCPClaimEvidenceContract(
                        result_kind="scalar_count",
                        projection_types=("User",),
                    )
                }
            ),
        }
    )
    count = MCPTranscriptProjector(count_task, PROFILE)
    count.observe(
        "cypher_query",
        {
            "info_type": "run",
            "query": "MATCH (n:Computer) RETURN count(n) AS count",
        },
        json.dumps(
            {
                "success": True,
                "data": {"literals": [{"key": "count", "value": 1}]},
            }
        ),
        None,
    )

    assert route.events[-1].kind is EvidenceEventKind.IRRELEVANT
    assert count.events[-1].kind is EvidenceEventKind.IRRELEVANT


def test_high_level_result_cannot_bypass_cypher_claim_contract() -> None:
    bounds = MCP_TASK.binding.bounds.model_copy(
        update={
            "max_result_cardinality": 2,
            "page_size": 100,
            "result_offset": 0,
            "max_pages": 1,
            "require_total_count": True,
            "require_stable_ordering": False,
        }
    )
    set_task = MCP_TASK.model_copy(
        update={
            "task_id": "complex.mcp.computer-sessions@2",
            "claim_kind": "set",
            "answer_policy": ExactSetPolicy(kind="exact_set"),
            "binding": _cypher_set_binding(
                bounds=bounds,
                projection_type="User",
            ),
        }
    )
    projector = MCPTranscriptProjector(set_task, PROFILE)

    ready = projector.observe(
        "computer_info",
        {
            "computer_id": "COMPUTER-A",
            "info_type": "sessions",
            "limit": 100,
            "skip": 0,
        },
        json.dumps(
            {
                "info_type": "sessions",
                "data": {
                    "count": 2,
                    "limit": 100,
                    "skip": 0,
                    "data": [
                        {"objectID": "USER-A"},
                        {"objectID": "USER-B"},
                    ],
                },
            }
        ),
        None,
    )

    assert ready is False
    assert projector.events[-1].kind is EvidenceEventKind.IRRELEVANT
    assert projector.receipts[-1].observation.total_count == 2
    assert projector.receipts[-1].observation.result_count == 2


def test_high_level_completeness_requires_response_reported_window() -> None:
    bounds = MCP_TASK.binding.bounds.model_copy(
        update={
            "max_result_cardinality": 2,
            "page_size": 100,
            "result_offset": 0,
            "max_pages": 1,
            "require_total_count": True,
            "require_stable_ordering": False,
        }
    )
    set_task = MCP_TASK.model_copy(
        update={
            "task_id": "complex.mcp.computer-sessions-unproven-window@2",
            "claim_kind": "set",
            "answer_policy": ExactSetPolicy(kind="exact_set"),
            "binding": _cypher_set_binding(
                bounds=bounds,
                projection_type="User",
            ),
        }
    )
    projector = MCPTranscriptProjector(set_task, PROFILE)

    ready = projector.observe(
        "computer_info",
        {
            "computer_id": "COMPUTER-A",
            "info_type": "sessions",
            "limit": 100,
            "skip": 0,
        },
        json.dumps(
            {
                "info_type": "sessions",
                "data": {
                    "count": 2,
                    "data": [
                        {"objectID": "USER-A"},
                        {"objectID": "USER-B"},
                    ],
                },
            }
        ),
        None,
    )

    assert ready is False
    assert projector.events[-1].kind is EvidenceEventKind.IRRELEVANT
    assert projector.receipts[-1].observation.complete is False


def test_mcp_prompt_declares_mechanical_result_contract() -> None:
    prompt = mcp_system_prompt(MCP_TASK)
    request = json.loads(prompt.split("\n\n", maxsplit=1)[1])

    assert '"version": "ori-mcp-result-contract-v22"' in prompt
    assert "evidence_result_contract" in prompt
    assert "include_properties=false" in prompt
    assert "mechanically materializes the final entities" in mcp_system_prompt(
        MCP_TASK.model_copy(update={"claim_kind": "set"})
    )
    assert "OPTIONAL MATCH" in mcp_system_prompt(
        MCP_TASK.model_copy(update={"claim_kind": "absence"})
    )
    assert "list comprehensions" in prompt
    assert "quadratic pairwise node-inequality" in prompt
    assert MCP_ORACLE.oracle_id not in prompt
    assert MCP_TASK.question not in prompt
    assert "task_fingerprint" not in json.dumps(request)
    assert "envelope_fingerprint" not in json.dumps(request)
    assert "answer_schema" not in json.dumps(request)
    assert json.dumps(request).count('"max_hops"') == 1
    assert request["task_contract"]["acceptance_spec"] == (
        MCP_TASK.acceptance_spec.model_dump(mode="json")
    )
    assert set(request) == {
        "evidence_result_contract",
        "submission_schema",
        "task_contract",
    }
    assert "obtain a later independently complete proof" in prompt
    assert "latest complete proof revokes readiness" in prompt
    assert "toString() on a Path" in prompt


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
            "binding": _cypher_set_binding(
                bounds=bounds,
                projection_type="User",
            ),
        }
    )
    projector = MCPTranscriptProjector(set_task, PROFILE)
    projector.total_count = 2

    assert (
        projector.observe(
            "cypher_query",
            {
                "info_type": "run",
                "query": ("MATCH (n:User) RETURN n ORDER BY n.name SKIP 0 LIMIT 2"),
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
            EvidenceEventKind.QUERY_ERROR,
        ),
        (
            {
                "success": False,
                "error": "HTTP 500: query timeout",
                "error_type": "query_timeout",
            },
            EvidenceEventKind.QUERY_TIMEOUT,
        ),
        (
            {
                "success": False,
                "error": "Direct query circuit is open",
                "error_type": "circuit_open",
                "query_executed": False,
            },
            EvidenceEventKind.INFRASTRUCTURE_FAILURE,
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
    coordinator_calls: list[tuple[str, bool]] = []

    @tool(name="cypher_query")
    def cypher_query():
        async def execute(info_type: str, query: str | None = None) -> str:
            original_calls.append(f"{info_type}:{query}")
            return "{}"

        return execute

    async def coordinator(
        query: str,
        *,
        include_properties: bool = True,
    ) -> CypherResult:
        coordinator_calls.append((query, include_properties))
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
                include_properties=False,
            )
        )
    )

    assert original_calls == []
    assert coordinator_calls == [("MATCH (n:User) RETURN n LIMIT 1", False)]
    assert result["success"] is True
    assert result["query_executed"] is True


def test_schema_only_retry_runs_once_after_useful_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    retry_calls: list[dict[str, Any]] = []

    async def fake_loop(**kwargs: Any):
        _observe_route_evidence(kwargs["tool_result_observer"])
        malformed = "commentary " + json.dumps(_answer())
        return _response(malformed), object(), [{"role": "assistant"}]

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
    assert retry_calls[0]["messages"][-1]["content"].startswith("Return only one JSON object")
    assert retry_calls[0]["max_tokens"] == 16_384
    assert outcome.sample.execution_class is ExecutionClass.SUCCESS
    assert outcome.sample.reasoning_correct is True
    assert record.mcp_finalization is not None
    assert record.mcp_finalization["schema_retry_count"] == 1
    assert len(record.mcp_tool_receipts) == 1
    assert record.mcp_tool_receipts[0].event.kind is EvidenceEventKind.USEFUL_POSITIVE


def test_certified_mcp_loop_suppresses_contradictory_discovered_server_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop_arguments: dict[str, Any] = {}

    async def fake_loop(**kwargs: Any):
        loop_arguments.update(kwargs)
        _observe_route_evidence(kwargs["tool_result_observer"])
        return _response(json.dumps(_answer())), object(), []

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
            bundle=MCPServerBundle(
                tools=[],
                server_prompt_text=(
                    "Read unavailable MCP resources first and prefer high-level tools."
                ),
                server_prompt_name="bloodhound-operator",
            ),
            model="codex/gpt-test",
            model_base_url=None,
            tool_loop=MCPToolLoop.NATIVE_OPENAI_COMPATIBLE,
            max_steps=4,
        )
    )

    assert outcome.sample.execution_class is ExecutionClass.SUCCESS
    assert loop_arguments["server_prompt_text"] == ""
    assert loop_arguments["server_prompt_name"] == ""
    assert loop_arguments["public_question"] == MCP_TASK.question
    assert MCP_TASK.question not in loop_arguments["system_prompt_override"]
    assert "only the claim-bound cypher_query" in loop_arguments["system_prompt_override"]


@pytest.mark.parametrize(
    ("tool_proves_identity", "expected_class", "expected_outcome"),
    (
        (
            True,
            ExecutionClass.HARNESS_FAILURE,
            SampleOutcomeCode.HARNESS_ERROR,
        ),
        (
            False,
            ExecutionClass.MODEL_FAILURE,
            SampleOutcomeCode.OUTPUT_INVALID,
        ),
    ),
)
def test_unknown_final_identity_uses_successful_cypher_receipt_for_classification(
    monkeypatch: pytest.MonkeyPatch,
    tool_proves_identity: bool,
    expected_class: ExecutionClass,
    expected_outcome: SampleOutcomeCode,
) -> None:
    unknown_id = "S-1-5-21-UNKNOWN"

    async def fake_loop(**kwargs: Any):
        if tool_proves_identity:
            kwargs["tool_result_observer"](
                "cypher_query",
                {
                    "info_type": "run",
                    "query": (
                        "MATCH p=(a {objectid:'USER-A'})-[:MemberOf*1..2]->"
                        "(b {objectid:'GROUP-B'}) RETURN p LIMIT 1"
                    ),
                },
                json.dumps(
                    {
                        "success": True,
                        "data": {
                            "nodes": {
                                "0": {"objectid": "USER-A"},
                                "1": {"objectid": unknown_id},
                                "2": {"objectid": "GROUP-B"},
                            },
                            "edges": [
                                {
                                    "source": "0",
                                    "target": "1",
                                    "kind": "MemberOf",
                                },
                                {
                                    "source": "1",
                                    "target": "2",
                                    "kind": "MemberOf",
                                },
                            ],
                        },
                    }
                ),
                None,
            )
        else:
            _observe_route_evidence(kwargs["tool_result_observer"])
        answer = _answer()
        answer["edges"][0]["source_id"] = unknown_id
        return _response(json.dumps(answer)), object(), []

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
        )
    )

    assert outcome.sample.execution_class is expected_class
    assert outcome.sample.outcome is expected_outcome
    assert record.mcp_finalization is not None
    if tool_proves_identity:
        assert unknown_id in {
            identity
            for receipt in record.mcp_tool_receipts
            for identity in model_runtime._successful_cypher_identity_ids(
                model_runtime._tool_payload(receipt.result_text)
            )
        }


def test_schema_only_retry_uses_schema_and_normalization_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    retry_calls: list[dict[str, Any]] = []

    async def fake_loop(**kwargs: Any):
        _observe_route_evidence(kwargs["tool_result_observer"])
        return _response(json.dumps({"edges": [{}]})), object(), []

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
    assert outcome.sample.execution_class is ExecutionClass.MODEL_FAILURE
    assert outcome.sample.reasoning_correct is False
    assert record.mcp_finalization is not None
    assert record.mcp_finalization["schema_retry_count"] == 1


def test_schema_retry_untyped_provider_failure_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_loop(**kwargs: Any):
        _observe_route_evidence(kwargs["tool_result_observer"])
        malformed = "commentary " + json.dumps(_answer())
        return _response(malformed), object(), []

    async def failed_retry(**_kwargs: Any) -> ModelResponse:
        return _response("", error="provider unavailable")

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
            transport=failed_retry,
        )
    )

    assert outcome.sample.execution_class is ExecutionClass.INFRA_FAILURE
    assert outcome.sample.reasoning_correct is None
    assert outcome.sample.outcome is SampleOutcomeCode.INFRA_ERROR
    assert record.provider_metrics["infra_scope"] == "provider"
    assert record.provider_metrics["infra_error_subtype"] == "PROVIDER_UNTYPED"
    assert record.provider_metrics["infra_retryable"] is False
    assert campaign_runner._infrastructure_retry_policy(
        outcome.sample,
        record,
    ) == ("provider", False)


def test_schema_retry_provider_auth_failure_is_not_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_loop(**kwargs: Any):
        _observe_route_evidence(kwargs["tool_result_observer"])
        malformed = "commentary " + json.dumps(_answer())
        return _response(malformed), object(), []

    async def failed_retry(**_kwargs: Any) -> ModelResponse:
        return _response(
            "",
            error="Error code: 401 - invalid_api_key",
            provider_metrics={
                "infra_scope": "provider",
                "infra_error_subtype": "PROVIDER_AUTH",
                "infra_retryable": False,
            },
        )

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
            transport=failed_retry,
        )
    )

    assert outcome.sample.execution_class is ExecutionClass.INFRA_FAILURE
    assert record.provider_metrics["infra_scope"] == "provider"
    assert record.provider_metrics["infra_error_subtype"] == "PROVIDER_AUTH"
    assert record.provider_metrics["infra_retryable"] is False
    assert campaign_runner._infrastructure_retry_policy(
        outcome.sample,
        record,
    ) == ("provider", False)


def test_schema_retry_provider_exception_preserves_retry_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_loop(**kwargs: Any):
        _observe_route_evidence(kwargs["tool_result_observer"])
        malformed = "commentary " + json.dumps(_answer())
        return _response(malformed), object(), []

    async def failed_retry(**_kwargs: Any) -> ModelResponse:
        request = httpx.Request("POST", "https://provider.invalid/v1/responses")
        raise httpx.ConnectError("provider unavailable", request=request)

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
            transport=failed_retry,
        )
    )

    assert outcome.sample.execution_class is ExecutionClass.INFRA_FAILURE
    assert record.provider_metrics["infra_scope"] == "provider"
    assert record.provider_metrics["infra_error_subtype"] == "PROVIDER_TRANSPORT"
    assert record.provider_metrics["infra_retryable"] is True


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


def test_openai_sdk_transport_failure_is_provider_infrastructure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failed_loop(**_kwargs: Any):
        raise openai.APIConnectionError(
            request=httpx.Request("POST", "https://api.openai.com/v1/responses")
        )

    monkeypatch.setattr(model_runtime, "_run_openai_compat_mcp_loop", failed_loop)
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

    assert outcome.sample.execution_class is ExecutionClass.INFRA_FAILURE
    assert record.provider_metrics["infra_scope"] == "provider"
    assert record.provider_metrics["infra_error_subtype"] == "PROVIDER_TRANSPORT"


def test_untyped_inner_timeout_is_harness_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failed_loop(**_kwargs: Any):
        raise TimeoutError("inner library timeout")

    monkeypatch.setattr(model_runtime, "_run_openai_compat_mcp_loop", failed_loop)
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
    assert record.provider_error == "inner library timeout"


def test_tool_transport_failure_preserves_receipt_and_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failed_loop(**kwargs: Any):
        kwargs["tool_result_observer"](
            "cypher_query",
            {
                "info_type": "run",
                "query": (
                    "MATCH p=(a {objectid:'USER-A'})-[:MemberOf]->(b {objectid:'GROUP-B'}) RETURN p"
                ),
            },
            json.dumps(
                {
                    "success": False,
                    "error": "connection reset",
                    "error_type": "transport_error",
                }
            ),
            None,
        )
        raise MCPToolInfrastructureError(
            subtype="MCP_TOOL_TRANSPORT",
            detail="connection reset",
        )

    monkeypatch.setattr(model_runtime, "_run_openai_compat_mcp_loop", failed_loop)
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

    assert outcome.sample.execution_class is ExecutionClass.INFRA_FAILURE
    assert record.mcp_tool_receipts[-1].observation.infrastructure_failure is True
    assert record.provider_metrics["infra_scope"] == "mcp_tool"


def test_v2_tool_subdeadline_raises_typed_infrastructure() -> None:
    async def slow_tool(**_arguments: Any) -> str:
        await asyncio.sleep(1)
        return "late"

    with pytest.raises(MCPToolInfrastructureError) as failure:
        asyncio.run(
            _execute_mcp_tool(
                slow_tool,
                {},
                timeout_seconds=0.001,
            )
        )

    assert failure.value.subtype == "MCP_TOOL_TIMEOUT"


def test_cancellation_carries_partial_private_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def stalled_loop(**kwargs: Any):
        kwargs["progress_observer"](
            _response("partial streamed output"),
            [{"role": "assistant", "content": "partial streamed output"}],
        )
        await asyncio.Event().wait()

    monkeypatch.setattr(model_runtime, "_run_openai_compat_mcp_loop", stalled_loop)

    async def cancel() -> V2ModelTaskCancelled:
        task = asyncio.create_task(
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
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except V2ModelTaskCancelled as exc:
            return exc
        raise AssertionError("cancellation did not preserve a V2 attempt")

    cancellation = asyncio.run(cancel())
    assert cancellation.sample.outcome is SampleOutcomeCode.INTERRUPTED
    assert cancellation.provider.raw_response == "partial streamed output"
    assert cancellation.provider.mcp_transcript[-1]["content"] == ("partial streamed output")


def test_schema_retry_cancellation_carries_initial_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    retry_started = asyncio.Event()

    async def malformed_loop(**kwargs: Any):
        _observe_route_evidence(kwargs["tool_result_observer"])
        return _response("commentary " + json.dumps(_answer())), object(), []

    async def stalled_retry(**_kwargs: Any) -> ModelResponse:
        retry_started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    monkeypatch.setattr(model_runtime, "_run_openai_compat_mcp_loop", malformed_loop)

    async def cancel() -> V2ModelTaskCancelled:
        task = asyncio.create_task(
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
                transport=stalled_retry,
            )
        )
        await retry_started.wait()
        task.cancel()
        try:
            await task
        except V2ModelTaskCancelled as exc:
            return exc
        raise AssertionError("schema retry cancellation did not preserve an attempt")

    cancellation = asyncio.run(cancel())
    assert cancellation.sample.outcome is SampleOutcomeCode.INTERRUPTED
    assert cancellation.provider.raw_response.startswith("commentary ")
    assert cancellation.provider.mcp_tool_receipts


def test_loop_exhaustion_cannot_use_schema_retry_as_a_fresh_solver_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    retry_calls: list[dict[str, Any]] = []

    async def exhausted_loop(**kwargs: Any):
        _observe_route_evidence(kwargs["tool_result_observer"])
        return (
            _response("", error="MCP loop exhausted without final answer"),
            object(),
            [],
        )

    async def retry_transport(**kwargs: Any) -> ModelResponse:
        retry_calls.append(kwargs)
        return _response(json.dumps(_answer()))

    monkeypatch.setattr(model_runtime, "_run_openai_compat_mcp_loop", exhausted_loop)
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
            transport=retry_transport,
        )
    )

    assert retry_calls == []
    assert outcome.sample.execution_class is ExecutionClass.MODEL_FAILURE
    assert outcome.sample.outcome is SampleOutcomeCode.OUTPUT_INVALID


@pytest.mark.parametrize(
    "raw",
    (
        'commentary {"count": 1} after',
        '```json\n{"count": 1}\n```',
    ),
)
def test_mcp_output_parser_requires_exact_json_object(raw: str) -> None:
    with pytest.raises(V2ModelRuntimeError, match="exactly one JSON object"):
        model_runtime._extract_json_object(raw)


def test_whole_task_timeout_is_model_failure_and_preserves_partial_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timed_bounds = MCP_TASK.binding.bounds.model_copy(
        update={"timeout_seconds": 0.01}
    )
    timed_task = MCP_TASK.model_copy(
        update={
            "binding": MCP_TASK.binding.model_copy(
                update={"bounds": timed_bounds}
            ),
            "acceptance_spec": MCP_TASK.acceptance_spec.model_copy(
                update={"bounds": timed_bounds}
            ),
        }
    )

    async def slow_loop(**kwargs: Any):
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
        kwargs["progress_observer"](
            ModelResponse(
                raw_text="partial assistant turn",
                cypher=None,
                parse_stage="mcp_partial",
                tokens_input=111,
                tokens_output=22,
                elapsed_seconds=0.005,
                model="codex/gpt-test",
            ),
            [{"role": "assistant", "content": "partial assistant turn"}],
        )
        await asyncio.sleep(1)

    monkeypatch.setattr(
        model_runtime,
        "_run_openai_compat_mcp_loop",
        slow_loop,
    )
    outcome, record = asyncio.run(
        run_mcp_model_task_v2(
            task=timed_task,
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

    assert outcome.finalization.phase is FinalizationPhase.TASK_TIMEOUT
    assert outcome.sample.execution_class is ExecutionClass.MODEL_FAILURE
    assert outcome.sample.outcome is SampleOutcomeCode.TASK_TIMEOUT
    assert record.tokens_input == 111
    assert record.tokens_output == 22
    assert record.mcp_transcript[-1]["content"] == "partial assistant turn"
    assert record.mcp_tool_receipts[-1].tool_name == "graph_analysis"
    assert record.provider_error == "MCP task execution budget exhausted"


def test_native_no_progress_timeout_preserves_subtype_as_infrastructure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def stalled_loop(**kwargs: Any):
        kwargs["progress_observer"](
            ModelResponse(
                raw_text="partial assistant turn",
                cypher=None,
                parse_stage="mcp_partial",
                tokens_input=77,
                tokens_output=11,
                elapsed_seconds=0.02,
                model="codex/gpt-test",
                provider_metrics={"provider": "fixture"},
            ),
            [{"role": "assistant", "content": "partial assistant turn"}],
        )
        raise MCPNoProgressTimeout(
            subtype="MCP_TURN_TIMEOUT",
            scope="turn",
            timeout_seconds=0.25,
        )

    monkeypatch.setattr(
        model_runtime,
        "_run_openai_compat_mcp_loop",
        stalled_loop,
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

    assert outcome.finalization.phase is FinalizationPhase.INFRASTRUCTURE_FAILURE
    assert outcome.sample.execution_class is ExecutionClass.INFRA_FAILURE
    assert outcome.sample.outcome is SampleOutcomeCode.INFRA_ERROR
    assert outcome.sample.reasoning_correct is None
    assert record.tokens_input == 77
    assert record.tokens_output == 11
    assert record.mcp_transcript[-1]["content"] == "partial assistant turn"
    assert record.provider_error is not None
    assert "MCP_TURN_TIMEOUT" in record.provider_error
    assert record.provider_metrics["infra_error_subtype"] == "MCP_TURN_TIMEOUT"


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
    transcript_bounds = MCP_TASK.binding.bounds.model_copy(
        update={"max_transcript_bytes": 1}
    )
    bounded_task = MCP_TASK.model_copy(
        update={
            "binding": MCP_TASK.binding.model_copy(
                update={"bounds": transcript_bounds}
            ),
            "acceptance_spec": MCP_TASK.acceptance_spec.model_copy(
                update={"bounds": transcript_bounds}
            ),
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

    assert outcome.sample.execution_class is ExecutionClass.PROOF_FAILURE
    assert outcome.sample.outcome is SampleOutcomeCode.PROOF_INSUFFICIENT
    assert outcome.sample.reasoning_correct is None


def test_v2_model_runtime_has_no_legacy_grader_or_template_dispatch() -> None:
    source = inspect.getsource(model_runtime) + inspect.getsource(campaign_runner)

    assert "grade_mcp_diagnostic" not in source
    assert "answer.correct" not in source
    assert "template_id" not in source
    assert "from ori.eval.grader" not in source
    assert "from .grader" not in source
    assert "Track.DIRECT" in source
    assert "Track.MCP" in source
    assert "direct-query-deny-cache-v3.private.json" in source
    assert 'run_dir / "direct-query-deny-cache' not in source
    assert "progress=progress" in source
    assert "_emit_progress" in source
    assert "MCPLauncherConfig.local_checkout(resolved.mcp_dir)" in source


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
        response=replace(
            _response("{}"),
            provider_metrics={
                "infra_scope": "provider",
                "infra_retryable": True,
            },
        ),
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


@pytest.mark.parametrize(
    ("max_steps", "read_timeout", "tool_timeout", "match"),
    (
        (11, 119.0, 60.0, "max_steps"),
        (12, 119.0, 120.0, "tool_timeout_seconds"),
        (12, 120.0, 60.0, "read_timeout_seconds"),
    ),
)
def test_readiness_rejects_hidden_mcp_runtime_caps(
    max_steps: int,
    read_timeout: float,
    tool_timeout: float,
    match: str,
) -> None:
    timed_task = MCP_TASK.model_copy(
        update={
            "binding": MCP_TASK.binding.model_copy(
                update={
                    "bounds": MCP_TASK.binding.bounds.model_copy(
                        update={
                            "max_tool_calls": 12,
                            "timeout_seconds": 120.0,
                        }
                    )
                }
            )
        }
    )
    resolved = SimpleNamespace(
        config=SimpleNamespace(
            defaults=SimpleNamespace(
                mcp=SimpleNamespace(
                    max_steps=max_steps,
                    read_timeout_seconds=read_timeout,
                    tool_timeout_seconds=tool_timeout,
                )
            )
        )
    )
    prepared = {
        Track.MCP: SimpleNamespace(
            pair=SimpleNamespace(public=SimpleNamespace(tasks=(timed_task,)))
        )
    }

    with pytest.raises(campaign_runner.V2CampaignRunError, match=match):
        campaign_runner._validate_runtime_bounds(resolved, prepared)


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
        response=replace(
            _response("{}"),
            provider_metrics={
                "infra_scope": "provider",
                "infra_retryable": True,
            },
        ),
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
            self.circuit_open = False

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
                reasoning_effort=None,
                health=SimpleNamespace(
                    timeout_seconds=1.0,
                    poll_interval=0.01,
                ),
            )
        ),
    )
    model = SimpleNamespace(
        name="gpt-test",
        provider="codex",
        model="gpt-test",
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
    assert coordinator.closed == 0
    assert any("0 resumed" in message for message in progress)
    assert any("[1/1]" in message and DIRECT_TASK.task_id in message for message in progress)
    assert any("INFRA_ERROR" in message and "retrying" in message for message in progress)
    assert any(
        "QUERY_TIMEOUT" in message and "running_correct=0/1" in message for message in progress
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

    state_holder["value"] = SimpleNamespace(
            checkpoint=SimpleNamespace(results=(infrastructure,)),
            attempts=(
                SimpleNamespace(
                    task_id=DIRECT_TASK.task_id,
                    attempt=1,
                    sample=infrastructure,
                    provider=provider,
                ),
                SimpleNamespace(
                    task_id=DIRECT_TASK.task_id,
                    attempt=2,
                    sample=infrastructure,
                    provider=provider,
                ),
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

    assert recovered_results == (infrastructure,)
    assert resumed_attempt_numbers == []
    assert any("0 resumed" in message for message in resumed_infrastructure)
    assert any(
        "retry budget already exhausted" in message and DIRECT_TASK.task_id in message
        for message in resumed_infrastructure
    )

    nonretryable_provider = provider.model_copy(
        update={
            "provider_metrics": {
                "infra_scope": "provider",
                "infra_error_subtype": "PROVIDER_AUTH",
                "infra_retryable": False,
            }
        }
    )
    state_holder["value"] = SimpleNamespace(
        checkpoint=SimpleNamespace(results=(infrastructure,)),
        attempts=(
            SimpleNamespace(
                task_id=DIRECT_TASK.task_id,
                attempt=1,
                sample=infrastructure,
                provider=nonretryable_provider,
            ),
        ),
    )
    resumed_nonretryable: list[str] = []
    _provenance, nonretryable_results = asyncio.run(
        campaign_runner._run_model(
            resolved=resolved,
            prepared=prepared,
            model=model,
            run_index=1,
            bhce=HealthyBHCE(),
            coordinator=coordinator,
            loop=None,
            runs_total=1,
            progress=resumed_nonretryable.append,
        )
    )

    assert nonretryable_results == (infrastructure,)
    assert resumed_attempt_numbers == []
    assert any("1 resumed" in message for message in resumed_nonretryable)
    assert not any("[1/1]" in message for message in resumed_nonretryable)

    async def exploding_direct_run(**_kwargs: Any):
        raise AttributeError("future adapter schema drift")

    state_holder["value"] = None
    monkeypatch.setattr(
        campaign_runner,
        "run_direct_model_task_v2",
        exploding_direct_run,
    )
    contained_progress: list[str] = []
    _provenance, contained_results = asyncio.run(
        campaign_runner._run_model(
            resolved=resolved,
            prepared=prepared,
            model=model,
            run_index=1,
            bhce=HealthyBHCE(),
            coordinator=coordinator,
            loop=None,
            runs_total=1,
            progress=contained_progress.append,
        )
    )

    assert len(contained_results) == 1
    assert contained_results[0].execution_class is ExecutionClass.HARNESS_FAILURE
    assert contained_results[0].outcome is SampleOutcomeCode.HARNESS_ERROR
    assert contained_results[0].reasoning_correct is None
    assert contained_results[0].detail == ("AttributeError: future adapter schema drift")
    assert any("HARNESS_ERROR" in message for message in contained_progress)


@pytest.mark.parametrize(
    "query",
    (
        (
            "MATCH p=(a {objectid:'USER-A'})-[:MemberOf]->"
            "(b {objectid:'GROUP-B'})-[:Enroll]->(t:CertTemplate)"
            "-[:PublishedTo]->(ca:EnterpriseCA) RETURN p"
        ),
        (
            "MATCH p=(ca:EnterpriseCA)<-[:PublishedTo]-(t:CertTemplate)"
            "<-[:Enroll]-(b {objectid:'GROUP-B'})<-[:MemberOf]-"
            "(a {objectid:'USER-A'})-[:GenericWrite]->(u:User)"
            "-[:AllowedToDelegate]->(c:Computer) RETURN p"
        ),
    ),
)
def test_decision_claim_accepts_public_roles_inside_returned_path(query: str) -> None:
    source = MCP_TASK.input_entities[0].model_copy(update={"role": "source"})
    target = MCP_TASK.input_entities[1].model_copy(update={"role": "target"})
    decision_contract = MCP_TASK.binding.mcp_evidence_contract.model_copy(
        update={"required_input_roles": ("source", "target")}
    )
    task = MCP_TASK.model_copy(
        update={
            "claim_kind": "decision",
            "answer_policy": DecisionPolicy(kind="decision"),
            "binding": MCP_TASK.binding.model_copy(
                update={"mcp_evidence_contract": decision_contract}
            ),
            "input_entities": (source, target),
            "acceptance_spec": MCP_TASK.acceptance_spec.model_copy(
                update={
                    "claim_kind": "decision",
                    "answer_policy": DecisionPolicy(kind="decision"),
                    "source_role": None,
                    "target_role": None,
                    "route_acceptance": RouteAcceptanceKind.NOT_APPLICABLE,
                    "required_mechanisms": (),
                }
            ),
        }
    )
    projector = MCPTranscriptProjector(task, PROFILE)

    ready = projector.observe(
        "cypher_query",
        {"info_type": "run", "query": query},
        json.dumps(
            {
                "success": True,
                "data": {
                    "nodes": {
                        "0": {"objectid": "CA-A"},
                        "1": {"objectid": "TEMPLATE-A"},
                        "2": {"objectid": "GROUP-B"},
                        "3": {"objectid": "USER-A"},
                        "4": {"objectid": "USER-C"},
                        "5": {"objectid": "COMPUTER-D"},
                    },
                    "edges": [
                        {"source": "3", "target": "2", "kind": "MemberOf"},
                        {"source": "2", "target": "1", "kind": "Enroll"},
                        {"source": "1", "target": "0", "kind": "PublishedTo"},
                    ],
                },
                "node_count": 6,
                "edge_count": 3,
            }
        ),
        None,
    )

    assert ready is True
    assert projector.events[-1].kind is EvidenceEventKind.USEFUL_POSITIVE


def test_decision_claim_rejects_public_role_detached_from_returned_path() -> None:
    source = MCP_TASK.input_entities[0].model_copy(update={"role": "source"})
    target = MCP_TASK.input_entities[1].model_copy(update={"role": "target"})
    decision_contract = MCP_TASK.binding.mcp_evidence_contract.model_copy(
        update={"required_input_roles": ("source", "target")}
    )
    task = MCP_TASK.model_copy(
        update={
            "claim_kind": "decision",
            "answer_policy": DecisionPolicy(kind="decision"),
            "binding": MCP_TASK.binding.model_copy(
                update={"mcp_evidence_contract": decision_contract}
            ),
            "input_entities": (source, target),
            "acceptance_spec": MCP_TASK.acceptance_spec.model_copy(
                update={
                    "claim_kind": "decision",
                    "answer_policy": DecisionPolicy(kind="decision"),
                    "source_role": None,
                    "target_role": None,
                    "route_acceptance": RouteAcceptanceKind.NOT_APPLICABLE,
                    "required_mechanisms": (),
                }
            ),
        }
    )
    projector = MCPTranscriptProjector(task, PROFILE)

    ready = projector.observe(
        "cypher_query",
        {
            "info_type": "run",
            "query": (
                "MATCH p=(a {objectid:'USER-A'})-[:GenericWrite]->(u:User), "
                "(b {objectid:'GROUP-B'}) RETURN p, b"
            ),
        },
        json.dumps(
            {
                "success": True,
                "data": {
                    "nodes": {
                        "0": {"objectid": "USER-A"},
                        "1": {"objectid": "USER-C"},
                        "2": {"objectid": "GROUP-B"},
                    },
                    "edges": [
                        {"source": "0", "target": "1", "kind": "GenericWrite"},
                    ],
                },
                "node_count": 3,
                "edge_count": 1,
            }
        ),
        None,
    )

    assert ready is False
    assert projector.events[-1].kind is EvidenceEventKind.IRRELEVANT


def test_final_route_facts_are_projected_from_claim_bound_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_loop(**kwargs: Any):
        kwargs["tool_result_observer"](
            "cypher_query",
            {
                "info_type": "run",
                "query": (
                    "MATCH p=(a {objectid:'USER-A'})-[:AdminTo]->(b {objectid:'GROUP-B'}) RETURN p"
                ),
            },
            json.dumps(
                {
                    "success": True,
                    "data": {
                        "nodes": {
                            "0": {"objectid": "USER-A"},
                            "1": {"objectid": "GROUP-B"},
                        },
                        "edges": [
                            {
                                "source": "0",
                                "target": "1",
                                "kind": "AdminTo",
                            }
                        ],
                    },
                    "node_count": 2,
                    "edge_count": 1,
                }
            ),
            None,
        )
        # The final answer hallucinates the oracle edge instead of reporting
        # the different relationship actually returned by BloodHound.
        return _response(json.dumps(_answer())), object(), []

    monkeypatch.setattr(model_runtime, "_run_openai_compat_mcp_loop", fake_loop)
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
        )
    )

    assert outcome.sample.reasoning_correct is False
    assert outcome.sample.outcome is SampleOutcomeCode.OUTPUT_INVALID
    assert outcome.sample.verdict is None
    assert outcome.sample.evidence is None


def test_receipt_property_facts_use_canonical_case_insensitive_keys() -> None:
    predicate = PropertyPredicate(
        role="source",
        property_name="hasspn",
        operator=PredicateOperator.EQUALS,
        value=True,
    )
    task = MCP_TASK.model_copy(
        update={
            "acceptance_spec": MCP_TASK.acceptance_spec.model_copy(
                update={"required_properties": (predicate,)}
            )
        }
    )

    _identities, _edges, properties = model_runtime._receipt_graph_facts(
        task,
        {
            "data": {
                "nodes": {
                    "0": {
                        "objectid": "USER-A",
                        "properties": {"hasSPN": True},
                    }
                },
                "edges": [],
            }
        },
    )

    assert properties == frozenset(
        {entity_property_fact_key("USER-A", "hasspn", True)}
    )


def test_complete_500_identity_receipt_materializes_set_after_empty_schema_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bounds = MCP_TASK.binding.bounds.model_copy(
        update={
            "max_result_cardinality": 500,
            "page_size": 500,
            "max_pages": 1,
            "require_total_count": False,
            "require_stable_ordering": True,
            "max_output_bytes": 524_288,
        }
    )
    policy = ExactSetPolicy(kind="exact_set")
    binding = _cypher_set_binding(bounds=bounds, projection_type="User")
    claim = SetClaim(
        kind="set",
        claim_id="claim:500-identity-window",
        selection=SelectionExpression(
            projection_role="result",
            projection_type="User",
            limit=500,
        ),
        semantics=RelationshipSemantics.DIRECT,
        population_scope=PopulationScope.BENCHMARK_NAMESPACE,
    )
    task = MCP_TASK.model_copy(
        update={
            "task_id": "complex.mcp.500-identity-window@2",
            "claim_kind": "set",
            "answer_policy": policy,
            "binding": binding,
            "answer_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "entities": {
                        "type": "array",
                        "items": {"type": "string"},
                    }
                },
                "required": ["entities"],
            },
            "acceptance_spec": compile_acceptance_spec(claim, policy, binding),
        }
    )
    expected = tuple(
        EntityRef(
            object_id=f"USER-{index:04d}",
            object_type="User",
            domain="EXAMPLE.LOCAL",
            role=f"result_{index:04d}",
            canonical_name=f"USER-{index:04d}@EXAMPLE.LOCAL",
        )
        for index in range(500)
    )
    resolver = IdentityResolver(expected)
    oracle = MCP_ORACLE.model_copy(
        update={
            "task_id": task.task_id,
            "expected_entities": expected,
            "route_variants": (),
            "required_mechanisms": (),
            "source_id": None,
            "target_id": None,
        }
    )

    async def fake_loop(**kwargs: Any):
        kwargs["tool_result_observer"](
            "cypher_query",
            {
                "info_type": "run",
                "include_properties": False,
                "query": (
                    "MATCH (u:User) RETURN u.objectid AS object_id "
                    "ORDER BY u.objectid SKIP 0 LIMIT 500"
                ),
            },
            json.dumps(
                {
                    "success": True,
                    "data": {
                        "nodes": {},
                        "edges": [],
                        "literals": [
                            {"key": "object_id", "value": entity.object_id}
                            for entity in expected
                        ],
                    },
                    "node_count": 0,
                    "edge_count": 0,
                }
            ),
            None,
        )
        return _response('{"entities":["USER-0000"'), object(), []

    async def empty_retry(**_kwargs: Any) -> ModelResponse:
        return _response('{"entities":[]}')

    monkeypatch.setattr(model_runtime, "_run_openai_compat_mcp_loop", fake_loop)
    outcome, record = asyncio.run(
        run_mcp_model_task_v2(
            task=task,
            oracle=oracle,
            resolver=resolver,
            profile=PROFILE,
            bundle=MCPServerBundle(tools=[]),
            model="codex/gpt-test",
            model_base_url=None,
            tool_loop=MCPToolLoop.NATIVE_OPENAI_COMPATIBLE,
            max_steps=4,
            transport=empty_retry,
        )
    )

    assert outcome.sample.execution_class is ExecutionClass.SUCCESS
    assert outcome.sample.reasoning_correct is True
    assert outcome.sample.evidence is not None
    assert len(outcome.sample.evidence.entities) == 500
    assert record.mcp_finalization is not None
    assert record.mcp_finalization["schema_retry_count"] == 1
