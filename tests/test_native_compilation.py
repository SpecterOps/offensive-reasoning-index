"""Native public contracts are explicitly compiled, not certified by configuration."""

import pytest

from ori.eval.v2.compiler import V2CompileError, _binding, compile_legacy_product
from ori.eval.v2.schema import (
    MCPBindingMode,
    MCPClaimEvidenceContract,
    NativeClaimEvidenceContract,
    NativeProofAlternative,
    SelectionExpression,
    SetClaim,
    Track,
    TrackBinding,
)
from tests.support.v2_compiler import simple_compiled as _simple_compiled
from tests.support.v2_mcp import native_profile as _profile

simple_compiled = _simple_compiled


def test_native_corpus_interoperability_is_complete_and_budgeted(
    simple_compiled, subtests, monkeypatch,
):
    import asyncio
    import json

    from mcp import types

    from ori.eval.v2.certification import build_offline_certification_catalog
    from ori.eval.v2.compiler import CompiledCorpus
    from ori.eval.v2.fingerprint import canonical_sha256
    from ori.eval.v2.live_projection import (
        native_corpus_qualification_inputs,
        native_qualification_guard,
        verify_native_corpus_interoperability,
    )
    from ori.eval.v2.native_mcp_profiles import get_native_implementation
    from ori.eval.v2.native_mcp_runtime import NativeMCPSession

    manifest, snapshot, _, _ = simple_compiled
    profile = _profile("mordavid")
    source = get_native_implementation("mordavid")
    compiled = compile_legacy_product(
        manifest, snapshot, product="simple", track=Track.MCP, native_profile=profile,
    )
    tasks = (
        next(task for task in compiled.tasks if task.public.claim_kind == "count"),
        next(task for task in compiled.tasks if task.public.claim_kind == "set"
             and task.public.binding.bounds.require_total_count),
    )
    assert len(tasks) == 2
    payload = compiled.model_dump(mode="python")
    payload["tasks"] = tasks
    payload["catalog_fingerprint"] = canonical_sha256(
        payload, exclude_fields=("catalog_fingerprint",),
    )
    corpus = CompiledCorpus.model_validate(payload)
    offline = build_offline_certification_catalog(corpus, snapshot, profile)
    _, _, budget = native_corpus_qualification_inputs(
        corpus=corpus, offline=offline, profile=profile, snapshot=snapshot,
    )
    assert budget >= 3
    guard, guard_budget = native_qualification_guard(
        corpus=corpus, offline=offline, profile=profile, snapshot=snapshot,
    )
    assert guard_budget == budget
    assert not guard("mordavid", "unknown", {}).allowed
    assert not guard("mordavid", "query_bloodhound", {"query": "MATCH (n) RETURN n"}).allowed
    assert not guard("armadin", "query_bloodhound", {}).allowed
    assert not guard("mordavid", "query_bloodhound", {"query": float("nan")}).allowed
    from ori.eval.v2.live_projection import _raw_node

    entities = next(case.evidence.entities for case in offline.certifications[1].fixtures.cases
                    if case.name == "perfect")
    assert len(entities) <= tasks[1].public.binding.bounds.page_size
    for case in ("valid", "short_budget", "second_wrong", "cancel"):
        with subtests.test(case=case):
            calls = []

            class Client:
                async def call_tool(self, name, args):
                    index = len(calls)
                    calls.append((name, args))
                    if case == "cancel" and index == 1:
                        raise asyncio.CancelledError
                    if index == 0:
                        rows = [{"certified_count": tasks[0].oracle.expected_count}]
                    elif index == 1:
                        rows = [{"total": len(entities) + (1 if case == "second_wrong" else 0)}]
                    else:
                        nodes = [
                            _raw_node(snapshot, entity.object_id, {})
                            for entity in sorted(entities, key=lambda entity: entity.object_id)
                        ]
                        rows = [{"entity": {"objectid": node["objectId"],
                                            **node.get("properties", {})},
                                 "labels": [node["kind"]]} for node in nodes]
                    return types.CallToolResult(content=[types.TextContent(
                        type="text", text=json.dumps({
                            "success": True, "data": rows,
                        }),
                    )])

            session = NativeMCPSession(
                Client(), "mordavid",
                [{"name": name, "inputSchema": {"type": "object"}}
                 for name in source.native_tool_names], [], [], [],
                guard=guard,
                capability_profile=profile, max_calls=1 if case == "short_budget" else budget,
            )
            operation = verify_native_corpus_interoperability(
                corpus=corpus, offline=offline, profile=profile, snapshot=snapshot, session=session,
            )
            if case == "valid":
                result = asyncio.run(operation)
                assert [row["task_id"] for row in result["tasks"]] == [
                    task.public.task_id for task in tasks
                ]
                assert len({row["attempt_id"] for row in result["tasks"]}) == 2
                from ori.eval.v2.certification import (
                    CertificationError,
                    LiveCertificationCatalog,
                    NativeLiveCertificationProof,
                    live_certify_native_corpus,
                )

                qualification = {"graph_before": {"fixture": "before"},
                                 "graph_after": {"fixture": "after"}}

                def interval(value, **kwargs):
                    assert value == qualification
                    assert kwargs["work_result"] == result
                    assert kwargs["expected"] == snapshot
                    assert kwargs["profile"] == profile
                    return canonical_sha256(value)

                with monkeypatch.context() as patch:
                    # Actual interval observations are covered by backend tests;
                    # this checks complete-corpus replay and catalog integration.
                    patch.setattr(
                        "ori.eval.v2.native_bolt_runtime."
                        "validate_completed_native_bolt_qualification", interval,
                    )
                    catalog = live_certify_native_corpus(
                        corpus, offline, profile, archive_snapshot=snapshot,
                        live_snapshot_before=snapshot, live_snapshot_after=snapshot,
                        qualification=qualification, work_result=result,
                    )
                    reloaded = LiveCertificationCatalog.model_validate_json(
                        catalog.model_dump_json(),
                    )
                    assert reloaded == catalog
                    assert all(
                        type(proof) is NativeLiveCertificationProof for proof in reloaded.proofs
                    )
                    assert [item.task_id for item in catalog.certifications] == [
                        task.public.task_id for task in tasks
                    ]
                    assert all(item.state.value == "candidate" for item in catalog.certifications)
                    for records in (result["tasks"][:-1], result["tasks"][::-1],
                                    result["tasks"] + result["tasks"][:1]):
                        with pytest.raises(CertificationError, match="complete roster"):
                            live_certify_native_corpus(
                                corpus, offline, profile, archive_snapshot=snapshot,
                                live_snapshot_before=snapshot, live_snapshot_after=snapshot,
                                qualification=qualification, work_result={"tasks": records},
                            )
            else:
                with pytest.raises(asyncio.CancelledError if case == "cancel" else ValueError):
                    asyncio.run(operation)
            expected_calls = 0 if case == "short_budget" else 2 if case == "cancel" else budget
            assert len(calls) == expected_calls


@pytest.mark.parametrize("implementation", ["mwnickerson", "mordavid"])
def test_native_interoperability_uses_actual_guarded_results(
    simple_compiled, implementation, subtests, monkeypatch,
):
    import asyncio
    import json
    from copy import deepcopy

    from mcp import types

    from ori.eval.v2.fixtures import offline_certify
    from ori.eval.v2.live_projection import (
        validate_native_interoperability,
        verify_native_task_interoperability,
    )
    from ori.eval.v2.native_mcp_profiles import get_native_implementation
    from ori.eval.v2.native_mcp_runtime import NativeCallDecision, NativeMCPSession

    manifest, snapshot, _, _ = simple_compiled
    profile = _profile(implementation)
    source = get_native_implementation(implementation)
    corpus = compile_legacy_product(
        manifest, snapshot, product="simple", track=Track.MCP, native_profile=profile,
    )
    task = next(task for task in corpus.tasks if task.public.claim_kind == "count")
    offline = offline_certify(task, snapshot, native_profile=profile)
    for case in ("valid", "wrong", "malformed", "policy", "cancel"):
        with subtests.test(case=case):
            calls = []

            class Client:
                async def call_tool(self, name, arguments):
                    calls.append((name, arguments))
                    if case == "cancel":
                        raise asyncio.CancelledError
                    count = task.oracle.expected_count + (1 if case == "wrong" else 0)
                    payload = ({"success": True, "data": [{"certified_count": count}]}
                               if implementation == "mordavid" else {
                                   "success": True, "info_type": "run", "has_results": True,
                                   "node_count": 0, "edge_count": 0,
                                   "data": {"nodes": {}, "edges": [], "literals": [
                                       {"key": "certified_count", "value": count},
                                   ]},
                               })
                    return types.CallToolResult(content=[types.TextContent(
                        type="text",
                        text="malformed" if case == "malformed" else json.dumps(payload),
                    )])

            session = NativeMCPSession(
                Client(), implementation,
                [{"name": name, "inputSchema": {"type": "object"}}
                 for name in source.native_tool_names],
                [{"name": name} for name in source.prompt_names],
                [{"uri": uri, "name": uri} for uri in source.resource_uris], [],
                guard=lambda *_: NativeCallDecision(case != "policy", "fixture"),
                capability_profile=profile,
            )
            operation = verify_native_task_interoperability(
                compiled=task, offline=offline, profile=profile, snapshot=snapshot, session=session,
            )
            if case == "valid":
                report = asyncio.run(operation)
                assert report["evidence"]["count"] == task.oracle.expected_count
                assert len(report["calls"]) == 1
                assert report["calls"][0]["raw_result"]["content"]
                assert validate_native_interoperability(
                    json.loads(json.dumps(report)), compiled=task, offline=offline,
                    profile=profile, snapshot=snapshot,
                )
                if implementation == "mordavid":
                    from ori.eval.v2.certification import (
                        CertificationError,
                        LiveCertificationProof,
                        NativeLiveCertificationProof,
                        build_native_live_certification_proof,
                        promote_candidate,
                        promote_native_candidate,
                    )
                    from ori.eval.v2.fingerprint import canonical_sha256

                    work = {"tasks": [report]}
                    qualification = {"unit_test_interval": True}

                    def checked_interval(value, **kwargs):
                        # Interval observation replay has its own driver tests;
                        # this test proves the builder passes the exact work.
                        assert value is qualification
                        assert kwargs == {
                            "profile": profile, "expected": snapshot, "work_result": work,
                        }
                        return canonical_sha256(value)

                    with monkeypatch.context() as patch:
                        patch.setattr(
                            "ori.eval.v2.native_bolt_runtime."
                            "validate_completed_native_bolt_qualification", checked_interval,
                        )
                        proof = build_native_live_certification_proof(
                            task, offline, profile, archive_snapshot=snapshot,
                            live_snapshot_before=snapshot, live_snapshot_after=snapshot,
                            qualification=qualification, work_result=work,
                        )
                        admission = dict(
                            archive_snapshot=snapshot, live_snapshot_before=snapshot,
                            live_snapshot_after=snapshot, qualification=qualification,
                            work_result=work,
                        )
                        promoted = promote_native_candidate(
                            task, offline, profile, proof, **admission,
                        )
                        assert promoted.state.value == "candidate"
                        assert promoted.live_proof_fingerprint == proof.proof_fingerprint
                        retained_failure = offline.model_copy(update={
                            "certification": offline.certification.model_copy(update={
                                "failures": ("fixture failed",),
                            }),
                        })
                        with pytest.raises(CertificationError, match="retained offline failures"):
                            promote_native_candidate(
                                task, retained_failure, profile, proof, **admission,
                            )
                        with pytest.raises(CertificationError, match="proof mismatch"):
                            promote_native_candidate(
                                task, offline, profile,
                                proof.model_copy(update={"qualification_fingerprint": "0" * 64}),
                                **admission,
                            )
                        patch.setattr(
                            "ori.eval.v2.native_bolt_runtime."
                            "validate_completed_native_bolt_qualification",
                            lambda *args, **kwargs: (_ for _ in ()).throw(
                                ValueError("interval invalid"),
                            ),
                        )
                        with pytest.raises(ValueError, match="interval invalid"):
                            promote_native_candidate(task, offline, profile, proof, **admission)
                    assert NativeLiveCertificationProof.model_validate_json(
                        proof.model_dump_json(),
                    ) == proof
                    assert proof.interoperability_fingerprint == canonical_sha256(report)
                    assert proof.qualification_work_fingerprint == canonical_sha256(work)
                    with pytest.raises(ValueError):
                        LiveCertificationProof.model_validate_json(proof.model_dump_json())
                    with pytest.raises(CertificationError, match="native qualification"):
                        promote_candidate(task, offline, profile, proof)
                    missing_marker = proof.model_dump(mode="json")
                    del missing_marker["proof_kind"]
                    with pytest.raises(ValueError):
                        NativeLiveCertificationProof.model_validate(missing_marker)
                for mutation in ("raw", "evidence", "verdict", "task", "calls", "args",
                                 "duration", "tool_error"):
                    with subtests.test(mutation=mutation):
                        changed = deepcopy(report)
                        if mutation == "raw":
                            changed["calls"][0]["raw_result"]["content"] = []
                        elif mutation == "evidence":
                            changed["evidence"]["count"] += 1
                        elif mutation == "verdict":
                            changed["verdict"] = {}
                        elif mutation == "task":
                            changed["task_fingerprint"] = "0" * 64
                        elif mutation == "calls":
                            changed["calls"] = []
                        elif mutation == "args":
                            changed["calls"][0]["arguments"] = {}
                        elif mutation == "duration":
                            changed["calls"][0]["duration_seconds"] = float("nan")
                        else:
                            changed["calls"][0]["raw_result"]["isError"] = True
                        with pytest.raises(
                            ValueError, match="NATIVE_INTEROPERABILITY_REPLAY_INVALID",
                        ):
                            validate_native_interoperability(
                                changed, compiled=task, offline=offline,
                                profile=profile, snapshot=snapshot,
                            )
            else:
                with pytest.raises(asyncio.CancelledError if case == "cancel" else ValueError):
                    asyncio.run(operation)
            assert len(calls) == (0 if case == "policy" else 1)


def test_native_anthropic_shared_runner_and_exact_repair_history(
    simple_compiled, monkeypatch, subtests,
):
    import asyncio
    import json
    from contextlib import asynccontextmanager
    from copy import deepcopy
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from mcp import types

    from ori.eval.adapter import ModelResponse
    from ori.eval.provider_contract import ProviderCapabilityError
    from ori.eval.v2.identity import IdentityResolver
    from ori.eval.v2.live_projection import _mcp_fixture_query
    from ori.eval.v2.model_runtime import V2ModelTaskCancelled, run_mcp_model_task_v2
    from ori.eval.v2.native_capability import build_native_capability_profile
    from ori.eval.v2.native_mcp_profiles import get_native_implementation
    from ori.eval.v2.native_mcp_runtime import (
        NativeCallDecision,
        NativeMCPSession,
        NativeModelToolBridge,
    )
    from tests.support.v2_mcp import anthropic_fixture_stream

    manifest, snapshot, _, _ = simple_compiled
    source = get_native_implementation("mordavid")
    descriptors = [{"name": name, "description": "native description",
                    "inputSchema": {"type": "object"}} for name in source.native_tool_names]
    profile = build_native_capability_profile(
        "mordavid", runtime_fingerprint="a" * 64, dependency_lock_fingerprint="b" * 64,
        backend_binding_fingerprint="c" * 64, tools=descriptors,
        prompts=[], resources=[], resource_templates=[],
        surface_availability={"tools": True, "prompts": True, "resources": True},
    )
    corpus = compile_legacy_product(
        manifest, snapshot, product="simple", track=Track.MCP, native_profile=profile,
        native_tool_loop="native-anthropic",
    )
    compiled = next(t for t in corpus.tasks if t.public.claim_kind == "count")
    count = compiled.oracle.expected_count
    query = _mcp_fixture_query(compiled.public, count=True)
    binding = SimpleNamespace(model_slug="fixture", base_url="https://fixture.invalid")
    for case in ("complete", "repair", "cancel", "duplicate", "refusal", "pause", "truncated",
                 "tool_error", "policy", "usage_unknown", "cancel_read", "multi_cancel",
                 "structured", "structured_repair", "schema_rejected",
                 "stream_eof", "stream_cancel", "read_timeout", "partial_json", "unclosed_block",
                 "preloaded", "post_delta"):
        with subtests.test(case=case):
            calls, turns, repairs = [], [], []

            class Session:
                async def call_tool(self, name, arguments):
                    calls.append((name, arguments))
                    if case == "cancel" or case == "multi_cancel" and len(calls) == 2:
                        raise asyncio.CancelledError
                    return types.CallToolResult(content=[types.TextContent(
                        type="text", text=json.dumps({"success": True, "data": [{"total": count}]}),
                    )], isError=case == "tool_error")

            session = NativeMCPSession(
                Session(), "mordavid", tools=descriptors, prompts=[], resources=[],
                resource_templates=[],
                guard=lambda *_: NativeCallDecision(case != "policy", "fixture"),
                capability_profile=profile,
            )
            bridge = NativeModelToolBridge(session, compiled.public, attempt_id=case)
            signature = {"type": "thinking", "thinking": "private thought", "signature": "sig"}

            async def create(**kwargs):
                turns.append(deepcopy(kwargs))
                if case == "read_timeout":
                    await asyncio.sleep(0.1)
                if case == "cancel_read" and len(turns) == 2:
                    raise asyncio.CancelledError
                if kwargs["tools"]:
                    assert kwargs["tools"][0]["description"] == "native description"
                    assert kwargs["tools"][0]["input_schema"] == {"type": "object"}
                    assert "output_config" not in kwargs
                else:
                    assert (kwargs["output_config"]["format"]["schema"]
                            == compiled.public.answer_schema)
                    if case == "schema_rejected":
                        import anthropic
                        import httpx

                        raise anthropic.BadRequestError("unsupported schema", body={}, response=
                            httpx.Response(400, request=httpx.Request("POST", binding.base_url)))
                if len(turns) == 1 or case == "duplicate":
                    blocks = [signature, {"type": "tool_use", "id": "call-1",
                              "name": source.generic_query_tool, "input": {"query": query}}]
                    if case == "multi_cancel":
                        blocks.append({**deepcopy(blocks[-1]), "id": "call-2"})
                    reason = {"refusal": "refusal", "pause": "pause_turn",
                              "truncated": "max_tokens"}.get(case, "tool_use")
                    if case == "pause":
                        blocks = [signature]
                else:
                    blocks = [{"type": "text", "text": ("commentary " if "repair" in case else "")
                               + json.dumps({"count": count})}]
                    reason = "end_turn"
                usage = {"input_tokens": 2, "output_tokens": 1,
                         "cache_read_input_tokens": 3, "cache_creation_input_tokens": 4}
                if case == "usage_unknown" and len(turns) == 1:
                    usage.pop("input_tokens")
                    usage.pop("cache_read_input_tokens")
                return {"content": deepcopy(blocks), "stop_reason": reason, "usage": usage}

            streams = []

            @asynccontextmanager
            async def stream(**kwargs):
                message = await create(**kwargs)
                sdk_stream = anthropic_fixture_stream(
                    message, omit_stop=case == "stream_eof", cancel_partial=case == "stream_cancel",
                    malformed_input=case == "partial_json", unclosed_block=case == "unclosed_block",
                    preloaded=case == "preloaded", post_delta=case == "post_delta",
                )
                streams.append(sdk_stream)
                async with sdk_stream:
                    yield sdk_stream

            attempt = SimpleNamespace(client=SimpleNamespace(
                default_headers={}, _validate_headers=lambda *args: None,
                messages=SimpleNamespace(create=create, stream=stream),
            ), aclose=AsyncMock())
            materialize = AsyncMock(return_value=attempt)
            monkeypatch.setattr("ori.eval.anthropic_mcp.materialize_anthropic_client", materialize)

            async def repair(**kwargs):
                repairs.append(kwargs)
                assert kwargs["anthropic_binding"] is binding
                assert kwargs["messages"][1]["content"][0] == signature
                assert kwargs["messages"][2]["content"][0]["type"] == "tool_result"
                assert all(m["role"] != "tool" for m in kwargs["messages"])
                if case == "structured_repair":
                    assert (kwargs["structured_output_schema"]["schema"]
                            == compiled.public.answer_schema)
                return ModelResponse(raw_text=json.dumps({"count": count}), cypher=None,
                                     parse_stage="fixture", tokens_input=2, tokens_output=1,
                                     elapsed_seconds=0.01, model="fixture")

            options = dict(
                task=compiled.public, oracle=compiled.oracle, profile=profile, bundle=None,
                native_bridge=bridge, certified=False,
                resolver=IdentityResolver(obj.entity for obj in snapshot.objects),
                model="anthropic/fixture", model_base_url=None, anthropic_binding=binding,
                tool_loop="native-anthropic", transport=repair,
                read_timeout_seconds=0.01 if case == "read_timeout" else 240.0,
                max_steps=2 if case in {"structured", "schema_rejected"} else 3,
                structured_output_mode=(
                    "json_schema" if case.startswith("struct") or case == "schema_rejected"
                    else "prompt_local_validation"
                ),
            )
            if case == "complete":
                with pytest.raises(ProviderCapabilityError):
                    asyncio.run(run_mcp_model_task_v2(**{
                        **options, "structured_output_mode": "unknown",
                    }))
                materialize.assert_not_awaited()
            if case in {"cancel", "cancel_read", "multi_cancel", "stream_cancel"}:
                with pytest.raises(V2ModelTaskCancelled) as cancelled:
                    asyncio.run(run_mcp_model_task_v2(**options))
                record = cancelled.value.provider
                if case == "stream_cancel":
                    assert record.provider_metrics["stream_partial"] is not None
                    assert not record.provider_metrics["usage_complete"] and not calls
                elif case != "cancel_read":
                    assert record.provider_metrics["pending_tool_calls"][0]["id"] == (
                        "call-2" if case == "multi_cancel" else "call-1"
                    )
                else:
                    assert not record.provider_metrics["usage_complete"]
                    assert record.provider_metrics["completed_provider_turns"] == 1
                if case == "multi_cancel":
                    assert len(record.provider_metrics["native_history"][-1]["content"]) == 1
            else:
                outcome, record = asyncio.run(run_mcp_model_task_v2(**options))
                if case in {
                    "complete", "repair", "usage_unknown", "structured", "structured_repair",
                }:
                    assert outcome.sample.verdict is not None, (outcome.sample, repairs)
                    assert outcome.sample.verdict.status.value == "correct"
                    assert len(calls) == 1
                    assert turns[1]["messages"][1]["content"][0] == signature
                    if case == "complete":
                        assert record.provider_metrics["usage"]["input_tokens"] == 4
                        assert record.provider_metrics["cache_read_input_tokens"] == 6
                        assert record.provider_metrics["usage_complete"]
                        assert record.provider_metrics["time_to_first_token_seconds"] is not None
                    elif case == "usage_unknown":
                        assert record.provider_metrics["usage"]["input_tokens"] is None
                        assert record.provider_metrics["cache_read_input_tokens"] is None
                        assert not record.provider_metrics["usage_complete"]
                elif case == "duplicate":
                    assert len(calls) == 1
                elif case == "schema_rejected":
                    assert len(calls) == 1 and not repairs
                    assert record.provider_metrics["infra_error_subtype"] == "PROVIDER_REQUEST"
                elif case in {"stream_eof", "read_timeout", "partial_json", "unclosed_block",
                              "preloaded", "post_delta"}:
                    assert not calls and not repairs
                    assert record.provider_metrics["infra_error_subtype"] == (
                        "PROVIDER_TIMEOUT" if case == "read_timeout" else "PROVIDER_PROTOCOL"
                    )
                elif case in {"tool_error", "policy"}:
                    assert outcome.sample.verdict is None
                    assert turns[1]["messages"][2]["content"][0]["is_error"] is True
                    assert len(calls) == (1 if case == "tool_error" else 0)
                else:
                    assert not calls and outcome.sample.verdict is None
                assert bool(repairs) == ("repair" in case)
            attempt.aclose.assert_awaited_once()
            assert all(stream._raw_stream.closed for stream in streams)
            assert record.surface == "mcp-native-anthropic"
            assert record.tokens_input >= (
                0 if case in {"read_timeout", "preloaded"} else 2
            )


@pytest.mark.parametrize("implementation", ["mwnickerson", "mordavid"])
def test_native_fixture_parity_uses_native_projector_without_promoting(
    simple_compiled, implementation, monkeypatch,
):
    from ori.eval.v2.certification import (
        CertificationError,
        build_projection_parity_cases,
        live_certify_task,
    )
    from ori.eval.v2.fixtures import offline_certify
    from ori.eval.v2.profiles import capability_profile_for_track

    manifest, snapshot, _, _ = simple_compiled
    profile = _profile(implementation)
    corpus = compile_legacy_product(
        manifest, snapshot, product="simple", track=Track.MCP, native_profile=profile,
    )
    task = next(t for t in corpus.tasks if t.public.claim_kind == "count")
    offline = offline_certify(task, snapshot, native_profile=profile)

    def historical_projector_forbidden(**kwargs):
        raise AssertionError("native parity must not use historical CE receipts")

    monkeypatch.setattr(
        "ori.eval.v2.certification.project_mcp_fixture", historical_projector_forbidden,
    )
    cases = build_projection_parity_cases(
        task, offline, profile, archive_snapshot=snapshot, live_snapshot=snapshot,
    )
    assert tuple(case.name for case in cases) == tuple(case.name for case in offline.fixtures.cases)
    assert {"perfect", "wrong", "empty"} <= {case.name for case in cases if case.applicable}
    for case in cases:
        if case.applicable:
            assert case.archive_raw_source_digest == case.live_raw_source_digest
            assert case.archive_outcome == case.live_outcome
    with pytest.raises(CertificationError, match="graph fingerprint mismatch"):
        build_projection_parity_cases(
            task, offline, profile, archive_snapshot=snapshot,
            live_snapshot=snapshot.model_copy(update={"graph_fingerprint": "0" * 64}),
        )
    with pytest.raises(CertificationError, match="native capability profile"):
        build_projection_parity_cases(
            task, offline, capability_profile_for_track(Track.MCP),
            archive_snapshot=snapshot, live_snapshot=snapshot,
        )
    mismatched = offline.model_copy(update={"certification": offline.certification.model_copy(
        update={"task_fingerprint": "0" * 64},
    )})
    with pytest.raises(CertificationError, match="certification binding mismatch"):
        build_projection_parity_cases(
            task, mismatched, profile, archive_snapshot=snapshot, live_snapshot=snapshot,
        )
    # Snapshot fixture parity does not qualify an actual native runtime/backend.
    with pytest.raises((TypeError, ValueError)):
        live_certify_task(
            task, offline, profile, archive_snapshot=snapshot,
            live_snapshot_before=snapshot, live_snapshot_after=snapshot,
        )


@pytest.mark.parametrize("implementation", ["mwnickerson", "mordavid"])
def test_shared_native_task_runner_is_explicit_and_preserves_attempts(
    simple_compiled, implementation, monkeypatch, subtests,
):
    import asyncio
    import copy
    import json

    from mcp import types
    from pydantic import ValidationError

    from ori.eval.adapter import ModelResponse
    from ori.eval.v2.fingerprint import canonical_sha256
    from ori.eval.v2.identity import IdentityResolver
    from ori.eval.v2.live_projection import _mcp_fixture_query
    from ori.eval.v2.model_runtime import (
        ProviderRunRecord,
        V2ModelRuntimeError,
        V2ModelTaskCancelled,
        run_mcp_model_task_v2,
    )
    from ori.eval.v2.native_mcp_profiles import get_native_implementation
    from ori.eval.v2.native_mcp_runtime import (
        NativeCallDecision,
        NativeMCPSession,
        NativeModelToolBridge,
    )
    from ori.eval.v2.scoring import SampleOutcomeCode

    manifest, snapshot, _, _ = simple_compiled
    profile = _profile(implementation)
    corpus = compile_legacy_product(
        manifest, snapshot, product="simple", track=Track.MCP, native_profile=profile,
    )
    compiled = next(t for t in corpus.tasks if t.public.claim_kind == "count")
    count = compiled.oracle.expected_count
    query = _mcp_fixture_query(compiled.public, count=True)
    source = get_native_implementation(implementation)
    for case in ("complete", "infra", "cancel", "repair", "repair_cancel"):
        with subtests.test(case=case):
            calls = []
            turns = []
            repairs = []

            class Session:
                async def call_tool(self, name, arguments):
                    calls.append((name, arguments))
                    if case == "cancel":
                        raise asyncio.CancelledError
                    if case == "infra":
                        raise TimeoutError("private transport detail")
                    payload = ({"success": True, "info_type": "run", "has_results": True,
                                "node_count": 0, "edge_count": 0,
                                "data": {"nodes": {}, "edges": [],
                                         "literals": [{"key": "total", "value": count}]}}
                               if implementation == "mwnickerson" else
                               {"success": True, "data": [{"total": count}]})
                    return types.CallToolResult(content=[
                        types.TextContent(type="text", text=json.dumps(payload)),
                    ])

            session = NativeMCPSession(
                Session(), implementation,
                tools=[{"name": name, "inputSchema": {"type": "object"}}
                       for name in source.native_tool_names],
                prompts=[{"name": name} for name in source.prompt_names],
                resources=[{"uri": uri, "name": uri} for uri in source.resource_uris],
                resource_templates=[], guard=lambda *_: NativeCallDecision(True, "offline fixture"),
                capability_profile=profile,
            )
            bridge = NativeModelToolBridge(session, compiled.public, attempt_id=case)
            arguments = {"query": query}
            if implementation == "mwnickerson":
                arguments["info_type"] = "run"

            bypass = asyncio.run(session.call_tool(
                source.generic_query_tool, arguments, compiled.public, attempt_id=case,
            ))
            assert bypass.failure == "HARNESS_ERROR" and not bypass.executed
            assert calls == [] and session.calls == 0
            assert not session._set_states

            async def turn(**kwargs):
                turns.append(kwargs)
                final = json.dumps({"count": count})
                if case.startswith("repair"):
                    final = "commentary " + final
                return {"model": "offline-stub", "prompt_tokens": 2, "completion_tokens": 1,
                        "content": final if len(turns) > 1 else "",
                        "tool_calls": [] if len(turns) > 1 else [{"id": "native-call",
                            "type": "function", "function": {"name": source.generic_query_tool,
                                "arguments": json.dumps(arguments)}}]}

            async def repair(**kwargs):
                repairs.append(kwargs)
                if case == "repair_cancel":
                    raise asyncio.CancelledError
                return ModelResponse(
                    raw_text=json.dumps({"count": count}), model="offline-stub",
                    cypher=None, parse_stage="offline-repair",
                    tokens_input=3, tokens_output=2, elapsed_seconds=0.01,
                )

            monkeypatch.setattr("ori.eval.mcp_runtime._openai_compat_chat_turn", turn)
            kwargs = dict(task=compiled.public, oracle=compiled.oracle,
                          resolver=IdentityResolver(obj.entity for obj in snapshot.objects),
                          profile=profile, bundle=None, native_bridge=bridge,
                          model="openai-compat/offline-stub", model_base_url="http://localhost:8080/v1",
                          tool_loop="native-openai-compatible", max_steps=3, transport=repair)
            with pytest.raises(V2ModelRuntimeError, match="certified=False"):
                asyncio.run(run_mcp_model_task_v2(**kwargs))
            assert calls == turns == []
            if case in {"cancel", "repair_cancel"}:
                with pytest.raises(V2ModelTaskCancelled) as cancelled:
                    asyncio.run(run_mcp_model_task_v2(**kwargs, certified=False))
                record = cancelled.value.provider
                native_call = record.provider_metrics["native_execution"]["tool_calls"][0]
                if case == "cancel":
                    assert record.provider_metrics["native_execution"]["interrupted"]
                    assert native_call["outcome"] is None
                else:
                    assert native_call["consumed"] and native_call["outcome"] is not None
                    assert record.provider_metrics["schema_repair_execution"][
                        "status"
                    ] == "interrupted"
            else:
                outcome, record = asyncio.run(run_mcp_model_task_v2(**kwargs, certified=False))
                assert outcome.sample.outcome is (
                    SampleOutcomeCode.COMPLETED if case in {"complete", "repair"}
                    else SampleOutcomeCode.INFRA_ERROR
                )
                assert outcome.finalization.certified is False
                if case in {"complete", "repair"}:
                    assert outcome.sample.reasoning_correct is True
                if case == "repair":
                    repair_status = record.provider_metrics["schema_repair_execution"]["status"]
                    assert repair_status == "returned"
            assert len(repairs) == int(case.startswith("repair"))
            trace = record.provider_metrics["native_execution"]
            assert trace["attempt_id"] == case and trace["certified"] is False
            assert trace["tool_calls"][0]["arguments"] == arguments
            assert record.mcp_tool_receipts == ()
            assert ProviderRunRecord.model_validate_json(record.model_dump_json()) == record
            if case == "complete":
                for field, value in (("certified", True), ("task_fingerprint", "0" * 64),
                                     ("schema_version", "unknown"), ("extra_field", "invalid")):
                    with subtests.test(invalid_trace_field=field):
                        payload = copy.deepcopy(record.model_dump(mode="json"))
                        payload["provider_metrics"]["native_execution"][field] = value
                        payload["record_fingerprint"] = canonical_sha256(
                            payload, exclude_fields=("record_fingerprint",),
                        )
                        with pytest.raises(ValidationError):
                            ProviderRunRecord.model_validate_json(json.dumps(payload))
            with pytest.raises(V2ModelRuntimeError, match="fresh matching attempt"):
                asyncio.run(run_mcp_model_task_v2(**kwargs, certified=False))


def test_explicit_native_compilation_preserves_claims_but_rebinds_contracts(
    simple_compiled, subtests,
):
    import json

    from ori.eval.v2.model_runtime import mcp_system_prompt

    manifest, snapshot, _, legacy = simple_compiled
    before = legacy.model_dump_json()
    for name, tool, operation in (
        ("mwnickerson", "cypher_query", "run"),
        ("mordavid", "query_bloodhound", None),
    ):
        with subtests.test(implementation=name):
            profile = _profile(name)
            native = compile_legacy_product(
                manifest, snapshot, product="simple", track=Track.MCP, native_profile=profile,
            )
            assert native.catalog_fingerprint != legacy.catalog_fingerprint
            from ori.eval.v2.certification import build_offline_certification_catalog
            from ori.eval.v2.fixtures import offline_certify
            from ori.eval.v2.profiles import capability_profile_for_track

            with pytest.raises(ValueError, match="NATIVE_CERTIFICATION_UNAVAILABLE"):
                offline_certify(native.tasks[0], snapshot)
            with pytest.raises(ValueError):
                build_offline_certification_catalog(
                    native, snapshot, capability_profile_for_track(Track.MCP),
                )
            assert len(native.tasks) == len(legacy.tasks)
            for new, old in zip(native.tasks, legacy.tasks, strict=True):
                assert new.public.task_id == old.public.task_id
                assert new.oracle.claim == old.oracle.claim
                assert new.public.acceptance_spec == old.public.acceptance_spec
                assert new.public.task_fingerprint != old.public.task_fingerprint
                assert new.oracle.oracle_fingerprint != old.oracle.oracle_fingerprint
                contract = new.public.binding.mcp_evidence_contract
                assert isinstance(contract, NativeClaimEvidenceContract)
                assert contract.alternatives[0].tool_name == tool
                assert contract.alternatives[0].operation == operation
                assert new.public.binding.capability_profile_id == profile.profile_id
                assert new.public.binding.mcp_binding_mode is MCPBindingMode.NATIVE
                public_json = new.public.model_dump_json()
                assert "backend_binding_fingerprint" not in public_json
                assert "dependency_lock_fingerprint" not in public_json
                prompt = mcp_system_prompt(new.public)
                request = json.loads(prompt.split("\n\n", 1)[1])
                result_contract = request["evidence_result_contract"]
                assert result_contract["native_contract"] == contract.model_dump(mode="json")
                assert result_contract["execution_bounds"] == new.public.binding.bounds.model_dump(
                    mode="json"
                )
                assert "include_properties=false" not in prompt
                assert "unsupported_cysql" not in prompt
                assert "dependency_lock_fingerprint" not in prompt
                if name == "mordavid":
                    assert "cypher_query" not in prompt
                    if new.public.claim_kind == "set":
                        assert "labels(n) AS labels" in prompt
            assert legacy.model_dump_json() == before
    assert all(isinstance(task.public.binding.mcp_evidence_contract, MCPClaimEvidenceContract)
               for task in legacy.tasks)


def test_native_schema_discriminator_mode_and_implementation_are_fail_closed(
    simple_compiled, subtests,
):
    claim = simple_compiled[3].tasks[0].oracle.claim
    native = _binding(
        Track.MCP, claim=claim, expected_cardinality=1, native_profile=_profile("mordavid"),
    )
    payload = native.model_dump(mode="python")
    assert TrackBinding.model_validate_json(native.model_dump_json()) == native
    for case in ("version", "mode", "profile", "tool", "duplicate"):
        with subtests.test(case=case):
            changed = native.model_dump(mode="python")
            contract = changed["mcp_evidence_contract"]
            if case == "version":
                del contract["contract_version"]
            elif case == "mode":
                changed["mcp_binding_mode"] = MCPBindingMode.CYPHER_ENABLED
            elif case == "profile":
                changed["capability_profile_id"] = "ori-native-armadin-v1-" + "a" * 64
            elif case == "tool":
                contract["alternatives"][0]["tool_name"] = "invented"
            else:
                contract["alternatives"] *= 2
            with pytest.raises(ValueError):
                TrackBinding.model_validate(changed)
    assert native.model_dump(mode="python") == payload
    with pytest.raises(V2CompileError, match="only to MCP"):
        _binding(Track.DIRECT, claim=claim, expected_cardinality=1,
                 native_profile=_profile("mordavid"))
    with pytest.raises(V2CompileError, match="invalid native"):
        _binding(Track.MCP, claim=claim, expected_cardinality=1, native_profile=object())


def test_armadin_unsupported_claims_do_not_get_generic_query_contracts(simple_compiled):
    manifest, snapshot, _, legacy = simple_compiled
    with pytest.raises(V2CompileError, match="unsupported Armadin"):
        compile_legacy_product(
            manifest, snapshot, product="simple", track=Track.MCP,
            native_profile=_profile("armadin"),
        )
    existing = legacy.tasks[0].oracle.claim
    claim = SetClaim(
        kind="set", claim_id="native-domains",
        selection=SelectionExpression(projection_role="answer", projection_type="Domain"),
        semantics=existing.semantics, population_scope=existing.population_scope,
    )
    binding = _binding(Track.MCP, claim=claim, expected_cardinality=1,
                       native_profile=_profile("armadin"))
    assert binding.mcp_evidence_contract.alternatives[0].tool_name == "find_domains"
    with pytest.raises(ValueError):
        NativeClaimEvidenceContract(
            contract_version="ori-native-claim-evidence-v1", implementation_id="armadin",
            alternatives=(NativeProofAlternative(
                tool_name="find_shortest_path", result_kind="scalar_count",
                requirements=("bounded absence",),
            ),),
        )


def test_oaic_native_compiler_threads_profile_without_changing_recipe_roster():
    from ori.cli import _build_manifest
    from ori.eval.v2.graph import build_archive_snapshot
    from ori.eval.v2.oaic_recipes import build_oaic_recipe_metadata, compile_oaic_product
    from ori.generator.benchmark_profiles import build_benchmark_generation_profile
    from ori.generator.oaic import build_oaic_graph
    from ori.generator.serializer import _build_zip

    config = build_benchmark_generation_profile("oaic-2026-v1", seed=67)
    graph = build_oaic_graph(
        domain=config.domain, seed=67, users=config.users,
        workstations=config.workstations, servers=config.servers,
    )
    archive = _build_zip(graph)
    manifest = _build_manifest(graph, 67, archive=archive)
    manifest["metadata"]["benchmark"] = "oaic-2026-v1"
    snapshot = build_archive_snapshot(archive, manifest, product="oaic-2026-v1")
    legacy = compile_oaic_product(manifest, snapshot, track=Track.MCP)
    native = compile_legacy_product(
        manifest, snapshot, product="oaic-2026-v1", track=Track.MCP,
        native_profile=_profile("mordavid"),
    )
    assert len(native.tasks) == len(legacy.tasks) == 110
    old_metadata = build_oaic_recipe_metadata(legacy)
    new_metadata = build_oaic_recipe_metadata(native)
    assert [(e.recipe_id, e.variant_id, e.eligibility) for e in new_metadata.entries] == [
        (e.recipe_id, e.variant_id, e.eligibility) for e in old_metadata.entries
    ]
    assert all(isinstance(task.public.binding.mcp_evidence_contract, NativeClaimEvidenceContract)
               for task in native.tasks)
    assert {t.public.task_id for t in native.tasks} == {t.public.task_id for t in legacy.tasks}
    assert native.catalog_fingerprint != legacy.catalog_fingerprint


@pytest.mark.parametrize("implementation", ["mwnickerson", "mordavid"])
def test_native_count_compilation_replays_shared_finalizer_before_offline_certification(
    simple_compiled, implementation, monkeypatch,
):
    from ori.eval.v2.fixtures import offline_certify
    from ori.eval.v2.mcp import initial_finalization_state
    from ori.eval.v2.schema import CertificationState

    manifest, snapshot, _, _ = simple_compiled
    profile = _profile(implementation)
    corpus = compile_legacy_product(
        manifest, snapshot, product="simple", track=Track.MCP, native_profile=profile,
    )
    task = next(task for task in corpus.tasks if task.public.claim_kind == "count")
    certified = offline_certify(task, snapshot, native_profile=profile)
    assert certified.certification.state is CertificationState.OFFLINE_CERTIFIED
    assert certified.certification.capability_profile_fingerprint == profile.profile_fingerprint
    assert certified.certification.live_proof_fingerprint is None
    assert certified.certification.certified_profile_id is None
    assert all(case.actual_status == case.expected_status
               for case in certified.fixtures.cases if case.applicable)
    with pytest.raises(ValueError, match="NATIVE_CERTIFICATION_UNAVAILABLE"):
        initial_finalization_state(task.public, profile, tool_loop="native-openai-compatible")
    with pytest.raises(ValueError, match="forbidden"):
        initial_finalization_state(task.public, profile, tool_loop="auto", certified=False)
    if implementation == "mordavid":
        with pytest.raises(ValueError, match="NATIVE_PROOF_UNSUPPORTED"):
            unsupported = next(task for task in corpus.tasks if task.public.claim_kind == "route")
            offline_certify(unsupported, snapshot, native_profile=profile)
    from ori.eval.v2.certification import build_offline_certification_catalog

    if implementation == "mordavid":
        with pytest.raises(ValueError, match="NATIVE_PROOF_UNSUPPORTED"):
            build_offline_certification_catalog(corpus, snapshot, profile)
    else:
        build_offline_certification_catalog(corpus, snapshot, profile)

    # A broken projector or an unproven perfect response must block certification,
    # even though all deterministic scorer-only fixture expectations still pass.
    import ori.eval.v2.native_proof as proof
    original = proof.classify_native_result

    def irrelevant(*args, **kwargs):
        from ori.eval.v2.mcp import EvidenceEventKind
        return original(*args, **kwargs).model_copy(update={"kind": EvidenceEventKind.IRRELEVANT})

    monkeypatch.setattr(proof, "classify_native_result", irrelevant)
    with pytest.raises(ValueError, match="perfect native fixture cannot establish proof"):
        offline_certify(task, snapshot, native_profile=profile)
    def always_positive(*args, **kwargs):
        from ori.eval.v2.mcp import EvidenceEventKind
        return original(*args, **kwargs).model_copy(
            update={"kind": EvidenceEventKind.USEFUL_POSITIVE},
        )

    monkeypatch.setattr(proof, "classify_native_result", always_positive)
    with pytest.raises(ValueError, match="adversarial fixture incorrectly establishes proof"):
        offline_certify(task, snapshot, native_profile=profile)


def test_armadin_domain_offline_replay_is_not_live_admission(simple_compiled, monkeypatch):
    from ori.eval.v2.compiler import CompiledTask, _fingerprinted_task_bundle
    from ori.eval.v2.fingerprint import canonical_sha256
    from ori.eval.v2.fixtures import offline_certify
    from ori.eval.v2.schema import CertificationState, ExactSetPolicy, OracleBundle

    _, snapshot, _, corpus = simple_compiled
    original = next(task for task in corpus.tasks if task.public.claim_kind == "set")
    profile = _profile("armadin")
    claim = SetClaim(
        kind="set", claim_id="native-all-domains",
        selection=SelectionExpression(projection_role="item", projection_type="Domain"),
        semantics=original.oracle.claim.semantics,
        population_scope=original.oracle.claim.population_scope,
    )
    entities = tuple(obj.entity for obj in snapshot.objects if obj.entity.object_type == "Domain")
    assert entities
    public = _fingerprinted_task_bundle(
        task_id="native-all-domains", product="simple", claim=claim,
        policy=ExactSetPolicy(kind="exact_set"),
        binding=_binding(Track.MCP, claim=claim, expected_cardinality=len(entities),
                         native_profile=profile), input_entities=(),
        question="Return the complete set of Domain objects.",
    )
    from ori.eval.v2.model_runtime import mcp_system_prompt

    prompt = mcp_system_prompt(public)
    assert "find_domains" in prompt
    assert "cypher_query" not in prompt and "include_properties" not in prompt
    oracle_data = original.oracle.model_dump(mode="python")
    oracle_data.update(
        oracle_id="oracle:native-all-domains", task_id=public.task_id, claim=claim,
        task_fingerprint=public.task_fingerprint, claim_fingerprint=public.claim_fingerprint,
        expected_entities=entities, resolved_roles=(), expected_count=None,
    )
    oracle_data["oracle_fingerprint"] = canonical_sha256(
        oracle_data, exclude_fields=("oracle_fingerprint",),
    )
    task = CompiledTask(
        public=public, oracle=OracleBundle.model_validate(oracle_data),
        migration=original.migration.model_copy(update={"candidate_task_ids": (public.task_id,)}),
    )
    certification = offline_certify(task, snapshot, native_profile=profile)
    assert certification.certification.state is CertificationState.OFFLINE_CERTIFIED
    assert certification.certification.capability_profile_fingerprint == profile.profile_fingerprint
    assert certification.certification.live_proof_fingerprint is None

    from ori.eval.v2.certification import build_projection_parity_cases

    parity = build_projection_parity_cases(
        task, certification, profile, archive_snapshot=snapshot, live_snapshot=snapshot,
    )
    assert len(parity) == len(certification.fixtures.cases)
    assert next(case for case in parity if case.name == "perfect").applicable

    import asyncio
    import json

    from mcp import types

    from ori.eval.v2.identity import IdentityResolver
    from ori.eval.v2.model_runtime import run_mcp_model_task_v2
    from ori.eval.v2.native_mcp_profiles import get_native_implementation
    from ori.eval.v2.native_mcp_runtime import (
        NativeCallDecision,
        NativeMCPSession,
        NativeModelToolBridge,
    )
    from ori.eval.v2.scoring import SampleOutcomeCode

    calls = []
    turns = []
    source = get_native_implementation("armadin")

    class Session:
        async def call_tool(self, name, arguments):
            calls.append((name, arguments))
            payload = {"success": True,
                       "domains": [{"objectid": e.object_id, "name": e.canonical_name,
                                    "domain": e.domain} for e in entities],
                       "count": len(entities)}
            return types.CallToolResult(content=[
                types.TextContent(type="text", text=json.dumps(payload)),
            ])

    session = NativeMCPSession(
        Session(), "armadin",
        tools=[{"name": name, "inputSchema": {"type": "object"}}
               for name in source.native_tool_names],
        prompts=[{"name": name} for name in source.prompt_names],
        resources=[], resource_templates=[],
        guard=lambda *_: NativeCallDecision(True, "offline fixture"), capability_profile=profile,
    )
    bridge = NativeModelToolBridge(session, public, attempt_id="domain-runner")

    async def turn(**kwargs):
        turns.append(kwargs)
        return {"model": "offline-stub", "prompt_tokens": 2, "completion_tokens": 1,
                "content": '{"entities":[]}' if len(turns) > 1 else "",
                "tool_calls": [] if len(turns) > 1 else [{"id": "native-call",
                    "type": "function", "function": {"name": "find_domains", "arguments": "{}"}}]}

    async def no_repair(**kwargs):
        raise AssertionError("complete native set receipts should materialize the answer")

    monkeypatch.setattr("ori.eval.mcp_runtime._openai_compat_chat_turn", turn)
    outcome, record = asyncio.run(run_mcp_model_task_v2(
        task=public, oracle=task.oracle,
        resolver=IdentityResolver(obj.entity for obj in snapshot.objects),
        profile=profile, bundle=None, native_bridge=bridge, certified=False,
        model="openai-compat/offline-stub", model_base_url="http://localhost:8080/v1",
        tool_loop="native-openai-compatible", max_steps=3, transport=no_repair,
    ))
    assert calls == [("find_domains", {})]
    assert outcome.sample.outcome is SampleOutcomeCode.COMPLETED
    assert outcome.sample.reasoning_correct is True and not outcome.finalization.certified
    assert record.provider_metrics["native_execution"]["implementation_id"] == "armadin"
    assert record.mcp_tool_receipts == ()


@pytest.mark.parametrize("claim_kind", ["route", "decision"])
def test_main_native_routes_cross_offline_fixture_replay(simple_compiled, claim_kind):
    from ori.eval.v2.fixtures import offline_certify
    from ori.eval.v2.schema import CertificationState

    manifest, snapshot, _, _ = simple_compiled
    profile = _profile("mwnickerson")
    corpus = compile_legacy_product(
        manifest, snapshot, product="simple", track=Track.MCP, native_profile=profile,
    )
    routes = [task for task in corpus.tasks if task.public.claim_kind == claim_kind]
    assert routes
    for task in routes:
        certified = offline_certify(task, snapshot, native_profile=profile)
        assert certified.certification.state is CertificationState.OFFLINE_CERTIFIED
        assert certified.certification.live_proof_fingerprint is None
        names = {case.name for case in certified.fixtures.cases if case.applicable}
        assert {"perfect", "wrong", "empty"} <= names
        if claim_kind == "route":
            assert {"reversed_edge", "disconnected_path"} <= names


def test_armadin_native_route_replay_preserves_directed_witness(simple_compiled):
    from ori.eval.v2.compiler import CompiledTask, _fingerprinted_task_bundle
    from ori.eval.v2.fingerprint import canonical_sha256
    from ori.eval.v2.fixtures import offline_certify
    from ori.eval.v2.schema import CertificationState, MechanismValidRoutePolicy, OracleBundle

    _, snapshot, _, corpus = simple_compiled
    original = next(task for task in corpus.tasks if task.public.claim_kind == "route"
                    and not task.oracle.claim.required_context
                    and not task.oracle.claim.required_properties
                    and not task.oracle.claim.excluded_relationships
                    and not task.oracle.claim.excluded_mechanisms)
    # This is a dedicated unconstrained route fixture, not an easier replacement
    # for any selected benchmark task. Armadin's compiler rejects constrained
    # route contracts and that whole-cell admission boundary remains unchanged.
    claim = original.oracle.claim.model_copy(update={"required_mechanisms": ()})
    profile = _profile("armadin")
    public = _fingerprinted_task_bundle(
        task_id="native-armadin-route", product="simple", claim=claim,
        policy=MechanismValidRoutePolicy(kind="mechanism_valid_route", forbid_extra_edges=False),
        binding=_binding(Track.MCP, claim=claim, expected_cardinality=1,
                         native_profile=profile), input_entities=original.public.input_entities,
        question="Return a directed path with edges between the public source and target.",
    )
    oracle_data = original.oracle.model_dump(mode="python")
    oracle_data.update(oracle_id="oracle:native-armadin-route", task_id=public.task_id,
                       claim=claim, required_mechanisms=(),
                       task_fingerprint=public.task_fingerprint,
                       claim_fingerprint=public.claim_fingerprint)
    oracle_data["oracle_fingerprint"] = canonical_sha256(
        oracle_data, exclude_fields=("oracle_fingerprint",),
    )
    task = CompiledTask(public=public, oracle=OracleBundle.model_validate(oracle_data),
                        migration=original.migration.model_copy(
                            update={"candidate_task_ids": (public.task_id,)}))
    certified = offline_certify(task, snapshot, native_profile=profile)
    assert certified.certification.state is CertificationState.OFFLINE_CERTIFIED
    assert certified.certification.live_proof_fingerprint is None


def test_native_absence_offline_certification_replays_generated_contracts(subtests):
    from ori.eval.v2.fixtures import offline_certify
    from ori.eval.v2.schema import CertificationState
    from tests.support.v2_compiler import _generated_product

    manifest, snapshot = _generated_product("complex", 4401)
    for implementation in ("mwnickerson", "mordavid"):
        with subtests.test(implementation=implementation):
            profile = _profile(implementation)
            corpus = compile_legacy_product(
                manifest, snapshot, product="complex", track=Track.MCP, native_profile=profile,
            )
            tasks = [task for task in corpus.tasks if task.public.claim_kind == "absence"]
            assert tasks
            for task in tasks:
                certified = offline_certify(task, snapshot, native_profile=profile)
                assert certified.certification.state is CertificationState.OFFLINE_CERTIFIED
                assert certified.certification.live_proof_fingerprint is None
                assert certified.certification.capability_profile_fingerprint == (
                    profile.profile_fingerprint
                )


@pytest.mark.parametrize("implementation", ["mwnickerson", "mordavid"])
def test_main_native_sets_cross_offline_fixture_replay(simple_compiled, implementation):
    from ori.eval.v2.fixtures import offline_certify
    from ori.eval.v2.schema import CertificationState

    manifest, snapshot, _, _ = simple_compiled
    profile = _profile(implementation)
    corpus = compile_legacy_product(
        manifest, snapshot, product="simple", track=Track.MCP, native_profile=profile,
    )
    tasks = [task for task in corpus.tasks if task.public.claim_kind == "set"]
    assert tasks
    for task in tasks:
        certified = offline_certify(task, snapshot, native_profile=profile)
        assert certified.certification.state is CertificationState.OFFLINE_CERTIFIED
        assert certified.certification.live_proof_fingerprint is None
