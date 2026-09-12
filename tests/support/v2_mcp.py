"""Synthetic MCP route fixtures shared by adapter and runtime tests."""

from __future__ import annotations

from ori.eval.v2.compiler import compile_acceptance_spec
from ori.eval.v2.identity import IdentityResolver
from ori.eval.v2.mcp import MCPToolLoop, build_mcp_capability_profile
from ori.eval.v2.schema import (
    EdgeWitness,
    EntityRef,
    EntitySelector,
    ExactRoutePolicy,
    ExecutionBounds,
    MCPBindingMode,
    MCPClaimEvidenceContract,
    OracleBundle,
    PopulationScope,
    RelationshipSemantics,
    RouteClaim,
    RouteVariant,
    TaskBundle,
    Track,
    TrackBinding,
)

FP = "a" * 64

ALICE = EntityRef(
    object_id="USER-A",
    object_type="User",
    domain="EXAMPLE.LOCAL",
    role="source",
    canonical_name="ALICE@EXAMPLE.LOCAL",
)

TARGET = EntityRef(
    object_id="GROUP-B",
    object_type="Group",
    domain="EXAMPLE.LOCAL",
    role="target",
    canonical_name="DOMAIN ADMINS@EXAMPLE.LOCAL",
)

EDGE = EdgeWitness(
    source_id=ALICE.object_id,
    relationship="MemberOf",
    target_id=TARGET.object_id,
)

CLAIM = RouteClaim(
    kind="route",
    claim_id="claim:route",
    source=EntitySelector(role="source", object_type="User"),
    target=EntitySelector(role="target", object_type="Group"),
    semantics=RelationshipSemantics.DIRECT,
    population_scope=PopulationScope.BENCHMARK_NAMESPACE,
    required_mechanisms=("MemberOf",),
    max_hops=1,
)

PROFILE = build_mcp_capability_profile()

POLICY = ExactRoutePolicy(kind="exact_route")

BINDING = TrackBinding(
    track=Track.MCP,
    capability_profile_id=PROFILE.profile_id,
    semantics=RelationshipSemantics.DIRECT,
    bounds=ExecutionBounds(
        max_hops=1,
        max_result_cardinality=1,
        page_size=1,
        max_pages=1,
        require_total_count=False,
        require_stable_ordering=True,
        max_output_bytes=65_536,
        max_transcript_bytes=262_144,
        max_tool_calls=12,
        timeout_seconds=120.0,
    ),
    mcp_tool_loop=MCPToolLoop.NATIVE_OPENAI_COMPATIBLE.value,
    mcp_resource_mode="off",
    mcp_binding_mode=MCPBindingMode.CYPHER_ENABLED,
    mcp_evidence_contract=MCPClaimEvidenceContract(
        result_kind="path",
        required_input_roles=("source", "target"),
    ),
)

TASK = TaskBundle(
    task_id="simple.mcp.route@2",
    revision=2,
    product="simple",
    claim_kind="route",
    answer_policy=POLICY,
    acceptance_spec=compile_acceptance_spec(CLAIM, POLICY, BINDING),
    binding=BINDING,
    input_entities=(ALICE, TARGET),
    question="Find the exact edge route.",
    answer_schema={"type": "object"},
    claim_fingerprint=FP,
    prompt_fingerprint=FP,
    task_fingerprint=FP,
)

ORACLE = OracleBundle(
    oracle_id="oracle:route",
    task_id=TASK.task_id,
    claim=CLAIM,
    claim_fingerprint=FP,
    task_fingerprint=FP,
    graph_fingerprint=FP,
    resolved_roles=(ALICE, TARGET),
    route_variants=(RouteVariant(variant_id="canonical", edges=(EDGE,)),),
    graph_edge_registry=(EDGE,),
    graph_fact_registry_fingerprint=FP,
    required_mechanisms=("MemberOf",),
    source_id=ALICE.object_id,
    target_id=TARGET.object_id,
    oracle_fingerprint=FP,
)

RESOLVER = IdentityResolver((ALICE, TARGET))

def _answer(*, relationship: str = "MemberOf"):
    return {
        "task_id": TASK.task_id,
        "path_status": "found",
        "edges": [
            {
                "source_id": "EXAMPLE\\ALICE",
                "relationship": relationship,
                "target_id": "DOMAIN ADMINS@EXAMPLE.LOCAL",
            }
        ],
    }
def native_profile(name):
    """Expected native descriptors for offline compilation, never live admission."""
    from ori.eval.v2.native_capability import build_native_capability_profile
    from ori.eval.v2.native_mcp_profiles import get_native_implementation

    source = get_native_implementation(name)
    return build_native_capability_profile(
        name, runtime_fingerprint="a" * 64, dependency_lock_fingerprint="b" * 64,
        backend_binding_fingerprint="c" * 64,
        tools=[{"name": tool, "inputSchema": {"type": "object"}}
               for tool in source.native_tool_names],
        prompts=[{"name": prompt} for prompt in source.prompt_names],
        resources=[{"uri": uri, "name": uri} for uri in source.resource_uris],
        resource_templates=[],
        surface_availability={"tools": True, "prompts": True, "resources": True},
    )


def anthropic_fixture_stream(
    message, *, omit_stop=False, cancel_partial=False, malformed_input=False, unclosed_block=False,
    preloaded=False, post_delta=False,
):
    """Exercise the real pinned SDK accumulator over synthetic native events."""
    import asyncio
    import json
    from copy import deepcopy

    from anthropic import NOT_GIVEN
    from anthropic._models import construct_type_unchecked
    from anthropic.lib.streaming import AsyncMessageStream
    from anthropic.types import RawMessageStreamEvent

    usage = deepcopy(message["usage"])
    usage["output_tokens"] = 0
    events = [{"type": "message_start", "message": {
        "id": "fixture-message", "type": "message", "role": "assistant", "model": "fixture",
        "content": [], "stop_reason": None, "stop_sequence": None, "usage": usage,
    }}]
    for index, block in enumerate(message["content"]):
        initial = deepcopy(block)
        deltas = []
        if block["type"] == "text":
            initial["text"] = ""
            deltas.append({"type": "text_delta", "text": block["text"]})
        elif block["type"] == "thinking":
            initial.update(thinking="", signature="")
            deltas.extend([{"type": "thinking_delta", "thinking": block["thinking"]},
                           {"type": "signature_delta", "signature": block["signature"]}])
        elif block["type"] == "tool_use":
            initial["input"] = {}
            encoded = json.dumps(block["input"])
            deltas.append({"type": "input_json_delta",
                           "partial_json": encoded[:-1] if malformed_input else encoded})
        events.append({"type": "content_block_start", "index": index, "content_block": initial})
        events.extend({"type": "content_block_delta", "index": index, "delta": delta}
                      for delta in deltas)
        if not unclosed_block or index != len(message["content"]) - 1:
            events.append({"type": "content_block_stop", "index": index})
    events.append({"type": "message_delta", "delta": {
        "stop_reason": message["stop_reason"], "stop_sequence": None,
    }, "usage": message["usage"]})
    if preloaded:
        events[0]["message"]["content"] = deepcopy(message["content"])
        events = [events[0], events[-1]]
    if post_delta:
        events.insert(1, events.pop())
    if not omit_stop:
        events.append({"type": "message_stop"})

    class RawStream:
        closed = False

        async def __aiter__(self):
            for event in events:
                yield construct_type_unchecked(type_=RawMessageStreamEvent, value=event)
                if cancel_partial and event["type"] == "content_block_delta":
                    raise asyncio.CancelledError

        async def close(self):
            self.closed = True

    return AsyncMessageStream(RawStream(), output_format=NOT_GIVEN)
