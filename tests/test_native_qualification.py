"""Offline native artifact loading; backend interval and 50-task gates tested elsewhere."""

import asyncio
import json
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace

import pytest
from mcp import types

from ori.eval.v2.certification import (
    build_offline_certification_catalog,
    live_certify_native_corpus,
)
from ori.eval.v2.compiler import CompiledCorpus, compile_legacy_product
from ori.eval.v2.fingerprint import canonical_sha256
from ori.eval.v2.live_projection import (
    native_qualification_guard,
    verify_native_corpus_interoperability,
)
from ori.eval.v2.native_mcp_profiles import get_native_implementation
from ori.eval.v2.native_mcp_runtime import NativeMCPSession
from ori.eval.v2.native_qualification import (
    NativeQualificationArtifactError,
    load_native_qualification_artifacts,
    native_launcher_provenance,
)
from ori.eval.v2.protocol import V2ArtifactPair, build_artifacts
from ori.eval.v2.schema import Track
from tests.support.v2_compiler import simple_compiled as _simple_compiled
from tests.support.v2_mcp import native_profile

simple_compiled = _simple_compiled


@pytest.mark.parametrize("implementation", ["mordavid", "mwnickerson"])
def test_native_qualification_loader_replays_and_rejects_mixed_artifacts(
    simple_compiled,
    tmp_path,
    monkeypatch,
    subtests,
    implementation,
):
    manifest, snapshot, _, _ = simple_compiled
    profile = native_profile(implementation)
    corpus = compile_legacy_product(
        manifest,
        snapshot,
        product="simple",
        track=Track.MCP,
        native_profile=profile,
    )
    task = next(task for task in corpus.tasks if task.public.claim_kind == "count")
    payload = corpus.model_dump(mode="python")
    payload["tasks"] = (task,)
    payload["catalog_fingerprint"] = canonical_sha256(
        payload,
        exclude_fields=("catalog_fingerprint",),
    )
    corpus = CompiledCorpus.model_validate(payload)
    offline = build_offline_certification_catalog(corpus, snapshot, profile)
    guard, budget = native_qualification_guard(
        corpus=corpus,
        offline=offline,
        profile=profile,
        snapshot=snapshot,
    )

    class Client:
        async def call_tool(self, name, args):
            result = {"success": True, "data": [{"certified_count": task.oracle.expected_count}]}
            if implementation == "mwnickerson":
                result = {
                    "success": True,
                    "info_type": "run",
                    "has_results": True,
                    "node_count": 0,
                    "edge_count": 0,
                    "data": {
                        "nodes": {},
                        "edges": [],
                        "literals": [
                            {"key": "certified_count", "value": task.oracle.expected_count}
                        ],
                    },
                }
            return types.CallToolResult(
                content=[
                    types.TextContent(
                        type="text",
                        text=json.dumps(result),
                    )
                ]
            )

    source = get_native_implementation(implementation)
    session = NativeMCPSession(
        Client(),
        implementation,
        [{"name": name, "inputSchema": {"type": "object"}} for name in source.native_tool_names],
        [{"name": name} for name in source.prompt_names],
        [{"uri": uri, "name": uri} for uri in source.resource_uris],
        [],
        guard=guard,
        capability_profile=profile,
        max_calls=budget,
    )
    work = asyncio.run(
        verify_native_corpus_interoperability(
            corpus=corpus,
            offline=offline,
            profile=profile,
            snapshot=snapshot,
            session=session,
        )
    )
    qualification = {"graph_before": {"fixture": "before"}, "graph_after": {"fixture": "after"}}
    if implementation == "mwnickerson":
        qualification["qualification_kind"] = "native-ce-fixture-session-v1"

    def interval(value, **kwargs):
        if value != qualification or kwargs["expected"] != snapshot:
            raise ValueError("fixture interval mismatch")
        return canonical_sha256(value)

    # Backend observations and original selected-50 accounting are separate
    # integration gates. Actual response projection, evidence replay, native
    # proof construction and catalog comparison execute without mocks here.
    monkeypatch.setattr(
        "ori.eval.v2.native_bolt_runtime.validate_completed_native_bolt_qualification", interval
    )
    monkeypatch.setattr(
        "ori.eval.v2.native_ce_runtime.validate_completed_native_ce_qualification", interval
    )
    public, private = build_artifacts(corpus, identity_catalog=snapshot.entities)
    ids = tuple(task.task_id for task in public.tasks)
    prepared = SimpleNamespace(
        profile=profile,
        pair=V2ArtifactPair(public=public, private=private),
        task_ids=ids,
        selection=SimpleNamespace(selected_task_ids=ids),
        selection_fingerprint="a" * 64,
    )

    def artifacts(value):
        assert value is prepared
        return corpus, offline

    monkeypatch.setattr(
        "ori.eval.v2.native_qualification.native_qualification_artifacts",
        artifacts,
    )
    live = live_certify_native_corpus(
        corpus,
        offline,
        profile,
        archive_snapshot=snapshot,
        live_snapshot_before=snapshot,
        live_snapshot_after=snapshot,
        qualification=qualification,
        work_result=work,
    )
    originals = {
        "qualification": qualification,
        "work": work,
        "candidate": live.candidate_catalog.model_dump(mode="json"),
        "live": live.model_dump(mode="json"),
    }
    paths = {f"{name}_path": tmp_path / f"{name}.private.json" for name in originals}

    def write(values):
        for name, value in values.items():
            paths[f"{name}_path"].write_text(json.dumps(value), encoding="utf-8")

    write(originals)
    result = load_native_qualification_artifacts(prepared, snapshot, **paths)
    assert result.catalog == live
    assert result.prepared is prepared
    assert result.qualification_fingerprint == canonical_sha256(qualification)
    assert result.work_fingerprint == canonical_sha256(work)
    assert "catalog=" not in repr(result) and "prepared=" not in repr(result)
    assert not hasattr(result, "campaign_admitted")
    assert result.task_ids == ids
    assert result.track is Track.MCP
    assert result.profile == profile and result.pair == prepared.pair
    assert result.release == live.candidate_catalog and result.live == live
    assert result.selection_fingerprint == prepared.selection_fingerprint
    assert tuple(result.certifications) == ids
    _assert_native_qualified_dispatch(result, Client, work, guard, budget, artifacts, monkeypatch)
    observation = {"runtime": {"private_path": "/private/fixture"}, "native_discovery": {}}
    seen = []

    def check_observation(value, **kwargs):
        assert kwargs == {
            "profile": profile,
            "backend_binding_fingerprint": profile.backend_binding_fingerprint,
        }
        if value.get("runtime") != observation["runtime"]:
            raise ValueError("private runtime mismatch")
        seen.append(value)

    # Source/runtime/discovery validation is independently exercised by native
    # session tests. Here verify the private mapping uses that validator, not config.
    with monkeypatch.context() as context:
        context.setattr(
            "ori.eval.v2.native_mcp_runtime.validate_native_session_observations", check_observation
        )
        mapping = native_launcher_provenance(result, observation)
        assert seen == [observation]
        assert mapping == {
            "mode": "native",
            "implementation_id": profile.implementation_id,
            "backend": profile.backend,
            "source_revision": profile.source_revision,
            "runtime_fingerprint": profile.runtime_fingerprint,
            "dependency_lock_fingerprint": profile.dependency_lock_fingerprint,
            "capability_profile_fingerprint": profile.profile_fingerprint,
            "backend_binding_fingerprint": profile.backend_binding_fingerprint,
            "qualification_fingerprint": result.qualification_fingerprint,
            "qualification_work_fingerprint": result.work_fingerprint,
            "session_observation_fingerprint": canonical_sha256(observation),
        }
        assert "/private/fixture" not in json.dumps(mapping)
        changed = native_launcher_provenance(result, {**observation, "session": "different"})
        assert (
            changed["session_observation_fingerprint"] == mapping["session_observation_fingerprint"]
        )
        for invalid in ({}, {"runtime": None}, {"runtime": float("nan")}):
            with subtests.test(observation=invalid):
                with pytest.raises(
                    NativeQualificationArtifactError, match="^NATIVE_LAUNCHER_PROVENANCE_INVALID$"
                ):
                    native_launcher_provenance(result, invalid)
        with pytest.raises(NativeQualificationArtifactError):
            native_launcher_provenance(prepared, observation)
        _assert_campaign_native_provenance(result, observation, paths, tmp_path, snapshot)
    with pytest.raises(TypeError):
        result.certifications[ids[0]] = None
    with pytest.raises(FrozenInstanceError):
        result.work_fingerprint = "altered"

    from ori.eval.v2.campaign_runner import V2CampaignRunError, _run_model

    # Scheduler-compatible metadata must not unlock the historical executor.
    with pytest.raises(V2CampaignRunError, match="^NATIVE_ATTEMPT_SCOPE_REQUIRED$"):
        asyncio.run(
            _run_model(
                resolved=None,
                prepared=result,
                model=None,
                run_index=1,
                bhce=None,
                coordinator=None,
                loop=None,
                runs_total=1,
            )
        )
    for changed in ((), ids + ids, ("not-selected",)):
        with subtests.test(roster=changed):
            altered = SimpleNamespace(**vars(prepared))
            altered.selection = SimpleNamespace(selected_task_ids=changed)
            with pytest.raises(NativeQualificationArtifactError):
                replace(result, prepared=altered)
    cert = live.certifications[0]
    for changes in (
        {"task_fingerprint": "0" * 64},
        {"oracle_fingerprint": "0" * 64},
        {"capability_profile_fingerprint": "0" * 64},
        {"certification_fingerprint": "0" * 64},
    ):
        with subtests.test(certification=changes):
            altered_live = live.model_copy(
                update={
                    "certifications": (cert.model_copy(update=changes),),
                }
            )
            with pytest.raises(NativeQualificationArtifactError):
                replace(result, catalog=altered_live)

    mutations = []
    for name in ("candidate", "live"):
        values = deepcopy(originals)
        values[name]["graph_fingerprint"] = "0" * 64
        mutations.append((f"mixed_{name}", values))
        values = deepcopy(originals)
        values[name]["undeclared"] = True
        mutations.append((f"extra_{name}", values))
    values = deepcopy(originals)
    values["work"]["tasks"] = []
    mutations.append(("missing_work", values))
    values = deepcopy(originals)
    values["work"]["tasks"][0]["calls"][0]["raw_result"] = {"invented": True}
    mutations.append(("invented_response", values))
    values = deepcopy(originals)
    values["qualification"]["graph_after"] = {"fixture": "different"}
    mutations.append(("mixed_interval", values))
    for name, values in mutations:
        with subtests.test(case=name):
            write(values)
            with pytest.raises(
                NativeQualificationArtifactError, match="^NATIVE_QUALIFICATION_ARTIFACT_INVALID$"
            ):
                load_native_qualification_artifacts(prepared, snapshot, **paths)
    for name in originals:
        for malformed in (
            '{"duplicate":1,"duplicate":2}',
            '{"value":NaN}',
            '{"value":1e999}',
            "[]",
            "{} trailing",
        ):
            with subtests.test(file=name, malformed=malformed):
                write(originals)
                paths[f"{name}_path"].write_text(malformed, encoding="utf-8")
                with pytest.raises(NativeQualificationArtifactError):
                    load_native_qualification_artifacts(prepared, snapshot, **paths)
    write(originals)
    paths["work_path"].unlink()
    with pytest.raises(NativeQualificationArtifactError):
        load_native_qualification_artifacts(prepared, snapshot, **paths)


def _assert_campaign_native_provenance(qualified, observations, paths, tmp_path, snapshot):
    from ori.eval.v2 import campaign_runner
    from ori.eval.v2.campaign_config import V2Defaults, V2ModelEntry
    from ori.eval.v2.mcp import MCPToolLoop

    model = V2ModelEntry(
        name="local-fixture",
        provider="openai-compat",
        model="local-fixture",
        model_base_url="http://127.0.0.1:8080/v1",
    )
    resolved = SimpleNamespace(
        source_config_fingerprint="a" * 64,
        source_manifest=paths["qualification_path"],
        archive=paths["work_path"],
        config=SimpleNamespace(defaults=V2Defaults(), models=(model,), purpose="official"),
    )
    arguments = dict(
        resolved=resolved,
        prepared=qualified,
        model=model,
        run_index=1,
        loop=MCPToolLoop.NATIVE_OPENAI_COMPATIBLE,
        native_session_observations=observations,
    )
    provenance = campaign_runner._provenance(**arguments)
    assert (
        provenance.run_identity.target_fingerprint == qualified.profile.backend_binding_fingerprint
    )
    assert provenance.mcp_launcher_provenance["mode"] == "native"
    assert (
        campaign_runner.ModelRunProvenanceV2.model_validate_json(
            provenance.model_dump_json(),
        )
        == provenance
    )
    destination = tmp_path / "native-resume"
    campaign_runner._guard_run_dir(destination, provenance)
    campaign_runner._guard_run_dir(destination, campaign_runner._provenance(**arguments))
    another_interval = campaign_runner._provenance(
        **{
            **arguments,
            "native_session_observations": {**observations, "session": "changed"},
        }
    )
    assert another_interval.provenance_fingerprint == provenance.provenance_fingerprint
    changed = campaign_runner._provenance(
        **{
            **arguments,
            "native_session_observations": {
                **observations,
                "native_discovery": {"changed": True},
            },
        }
    )
    assert changed.provenance_fingerprint != provenance.provenance_fingerprint
    with pytest.raises(campaign_runner.V2CampaignRunError, match="incompatible"):
        campaign_runner._guard_run_dir(destination, changed)
    for invalid in (
        {"native_session_observations": None},
        {"mcp_launcher_provenance": {"mode": "legacy"}},
    ):
        with pytest.raises(
            campaign_runner.V2CampaignRunError, match="^NATIVE_SESSION_OBSERVATIONS_REQUIRED$"
        ):
            campaign_runner._provenance(**{**arguments, **invalid})

    from ori.eval.v2.graph import GraphSnapshot, LiveGraphVerification, graph_snapshot_fingerprint

    graph = dict(
        schema_version="ori-live-graph-verification-v2",
        expected_graph_fingerprint=snapshot.graph_fingerprint,
        observed_graph_fingerprint=snapshot.graph_fingerprint,
        page_size=500,
        object_queries=1,
        relationship_queries=1,
        object_count=len(snapshot.entities),
        relationship_count=len(snapshot.relationships),
        normalized_artifacts=(),
    )
    graph["verification_fingerprint"] = canonical_sha256(graph)
    receipt = LiveGraphVerification.model_validate(graph)
    readiness_args = dict(
        resolved=resolved,
        snapshot=snapshot,
        prepared={Track.MCP: qualified},
        receipts={Track.MCP: receipt},
        mcp_revision=qualified.profile.source_revision,
        mcp_launcher_provenance=None,
        model_readiness=campaign_runner._model_readiness(resolved),
        native_session_observations=observations,
    )
    ready = campaign_runner._readiness(**readiness_args)
    assert ready.tracks[0].target_fingerprint == qualified.profile.backend_binding_fingerprint
    assert ready.target_fingerprint == canonical_sha256(
        {
            "mcp": qualified.profile.backend_binding_fingerprint,
        }
    )
    assert ready.mcp_launcher_provenance == provenance.mcp_launcher_provenance
    assert campaign_runner.CampaignReadinessV2.model_validate_json(ready.model_dump_json()) == ready
    # Isolate readiness target routing; this metadata double does not certify a Direct track.
    direct_metadata = SimpleNamespace(
        track=Track.DIRECT,
        pair=qualified.pair,
        release=qualified.release,
        live=qualified.live,
        profile=qualified.profile,
        task_ids=qualified.task_ids,
    )
    mixed = campaign_runner._readiness(
        **{
            **readiness_args,
            "prepared": {Track.DIRECT: direct_metadata, Track.MCP: qualified},
            "receipts": {Track.DIRECT: receipt, Track.MCP: receipt},
        }
    )
    targets = {item.track.value: item.target_fingerprint for item in mixed.tracks}
    assert targets == {
        "direct": canonical_sha256(
            campaign_runner.resolve_bhce_target(
                resolved.config.defaults.bhce_url,
            )
        ),
        "mcp": qualified.profile.backend_binding_fingerprint,
    }
    assert targets["direct"] != targets["mcp"]
    assert mixed.target_fingerprint == canonical_sha256(targets)
    forged = mixed.model_dump(mode="json")
    forged["tracks"][0]["target_fingerprint"] = targets["mcp"]
    forged["readiness_fingerprint"] = canonical_sha256(
        forged,
        exclude_fields=("readiness_fingerprint",),
    )
    with pytest.raises(ValueError, match="readiness track target binding mismatch"):
        campaign_runner.CampaignReadinessV2.model_validate_json(json.dumps(forged))
    for invalid in (
        {"mcp_revision": "0" * 40},
        {"receipts": {}},
        {"receipts": {Track.MCP: receipt.model_copy(update={"object_count": 0})}},
    ):
        with pytest.raises(
            campaign_runner.V2CampaignRunError, match="^NATIVE_READINESS_BINDING_MISMATCH$"
        ):
            campaign_runner._readiness(**{**readiness_args, **invalid})
    other_graph = snapshot.model_dump(mode="python")
    other_graph["seed"] += 1
    other_graph["graph_fingerprint"] = graph_snapshot_fingerprint(other_graph)
    other_snapshot = GraphSnapshot.model_validate(other_graph)
    other_receipt = {
        **graph,
        "expected_graph_fingerprint": other_snapshot.graph_fingerprint,
        "observed_graph_fingerprint": other_snapshot.graph_fingerprint,
    }
    other_receipt["verification_fingerprint"] = canonical_sha256(
        other_receipt,
        exclude_fields=("verification_fingerprint",),
    )
    with pytest.raises(
        campaign_runner.V2CampaignRunError, match="^NATIVE_READINESS_BINDING_MISMATCH$"
    ):
        campaign_runner._readiness(
            **{
                **readiness_args,
                "snapshot": other_snapshot,
                "receipts": {Track.MCP: LiveGraphVerification.model_validate(other_receipt)},
            }
        )


def _assert_native_qualified_dispatch(
    qualified, client_type, work, guard, budget, artifacts, monkeypatch
):
    from ori.eval.adapter import ModelResponse
    from ori.eval.v2.identity import IdentityResolver
    from ori.eval.v2.model_runtime import (
        ProviderRunRecord,
        V2ModelRuntimeError,
        V2ModelTaskCancelled,
        run_mcp_model_task_v2,
    )
    from ori.eval.v2.native_mcp_runtime import NativeModelToolBridge

    profile = qualified.profile
    source = get_native_implementation(profile.implementation_id)
    task, oracle = qualified.pair.public.tasks[0], qualified.pair.private.oracles[0]
    call = work["tasks"][0]["calls"][0]
    for case in ("complete", "repair", "cancel", "invalid_protocol"):
        session = NativeMCPSession(
            client_type(),
            profile.implementation_id,
            [
                {"name": name, "inputSchema": {"type": "object"}}
                for name in source.native_tool_names
            ],
            [{"name": name} for name in source.prompt_names],
            [{"uri": uri, "name": uri} for uri in source.resource_uris],
            [],
            guard=guard,
            capability_profile=profile,
            max_calls=budget,
        )
        certificate = qualified.certifications[task.task_id]
        bridge = NativeModelToolBridge(
            session, task, attempt_id=case, native_certification=certificate
        )
        turns = []
        repairs = []

        async def turn(**kwargs):
            turns.append(kwargs)
            if case == "cancel" and len(turns) > 1:
                raise asyncio.CancelledError
            final = json.dumps({"count": oracle.expected_count})
            if case == "repair":
                final = "commentary " + final
            if case == "invalid_protocol":
                final = '{"ori_native_protocol":{"operation":"read_resource","uri":123}}'
            return {
                "model": "offline-stub",
                "prompt_tokens": 2,
                "completion_tokens": 1,
                "content": final if len(turns) > 1 else "",
                "finish_reason": "stop",
                "tool_calls": []
                if len(turns) > 1
                else [
                    {
                        "id": "native-call",
                        "type": "function",
                        "function": {
                            "name": call["tool_name"],
                            "arguments": json.dumps(call["arguments"]),
                        },
                    }
                ],
            }

        async def repair(**kwargs):
            repairs.append(kwargs)
            return ModelResponse(
                raw_text=json.dumps({"count": oracle.expected_count}),
                model="offline-stub",
                cypher=None,
                parse_stage="fixture",
                tokens_input=3,
                tokens_output=2,
                elapsed_seconds=0.01,
            )

        with monkeypatch.context() as context:
            context.setattr("ori.eval.mcp_runtime._openai_compat_chat_turn", turn)
            # Full selected-50 validation is independently covered; this shared
            # fixture executes real candidate replay and the real model/finalizer path.
            context.setattr(
                "ori.eval.v2.native_feasibility.native_qualification_artifacts", artifacts
            )
            options = dict(
                task=task,
                oracle=oracle,
                resolver=IdentityResolver(qualified.pair.private.identity_catalog),
                profile=profile,
                bundle=None,
                native_bridge=bridge,
                native_qualification=qualified,
                certified=True,
                graph_fact_registry=qualified.pair.private.graph_fact_registry,
                model="openai-compat/offline-stub",
                model_base_url="http://localhost:8080/v1",
                tool_loop="native-openai-compatible",
                max_steps=3,
                transport=repair,
            )
            for invalid in (
                {"native_qualification": None},
                {"certified": False},
                {"graph_fact_registry": None},
            ):
                with pytest.raises(V2ModelRuntimeError):
                    asyncio.run(run_mcp_model_task_v2(**{**options, **invalid}))
                assert not turns and not bridge.runner_claimed and session.calls == 0
            if case == "cancel":
                with pytest.raises(V2ModelTaskCancelled) as cancelled:
                    asyncio.run(run_mcp_model_task_v2(**options))
                record = cancelled.value.provider
            else:
                outcome, record = asyncio.run(run_mcp_model_task_v2(**options))
                if case == "invalid_protocol":
                    assert outcome.sample.reasoning_correct is not True
                    assert bridge.finalization_ready  # Real prior native proof remains present.
                    assert len(turns) == 2 and repairs == []
                    assert bridge.protocol_calls[-1]["operation"] == "invalid_request"
                else:
                    assert outcome.sample.reasoning_correct is True
                assert outcome.finalization.certified
                assert outcome.finalization.native_certification == certificate
                assert outcome.finalization.schema_retry_count == int(case == "repair")
            trace = record.provider_metrics["native_execution"]
            assert trace["schema_version"] == "ori-native-execution-v2" and trace["certified"]
            assert (
                trace["native_certification_fingerprint"] == certificate.certification_fingerprint
            )
            assert trace["tool_calls"][0]["arguments"] == call["arguments"]
            assert ProviderRunRecord.model_validate_json(record.model_dump_json()) == record
            from ori.eval.v2.campaign import (
                CheckpointV2,
                PublicReportV2,
                RunIdentity,
                build_checkpoint,
                build_public_report,
                validate_checkpoint,
            )
            from ori.eval.v2.campaign_runner import _run_operational_metrics
            from ori.eval.v2.scoring import summarize_results

            sample = cancelled.value.sample if case == "cancel" else outcome.sample
            identity = RunIdentity(
                provider="openai-compat",
                model="fixture",
                run_index=1,
                target_fingerprint=profile.backend_binding_fingerprint,
                tool_loop="native-openai-compatible",
            )
            checkpoint = build_checkpoint(qualified.pair, profile, identity, results=(sample,))
            restored = CheckpointV2.model_validate_json(checkpoint.model_dump_json())
            assert validate_checkpoint(restored, qualified.pair, profile, identity) == checkpoint
            public = build_public_report(
                qualified.pair,
                profile,
                (sample,),
                summarize_results(qualified.task_ids, (sample,)),
                certifications=qualified.certifications,
                scheduled_task_ids=qualified.task_ids,
            )
            assert PublicReportV2.model_validate_json(public.model_dump_json()) == public
            state = SimpleNamespace(
                checkpoint=checkpoint,
                attempts=(
                    SimpleNamespace(
                        task_id=task.task_id,
                        provider=record,
                        sample=sample,
                        scheduler_phase="initial",
                        recovery_round=0,
                    ),
                ),
            )
            metrics = _run_operational_metrics(state, native=True)
            assert metrics.resource_mode == "native"
            assert metrics.mcp_tool_calls_total == 1 and metrics.cypher_query_calls_total == 1
            assert metrics.tokens_input_total == record.tokens_input
            with pytest.raises(ValueError, match="certified native campaign"):
                _run_operational_metrics(state)
            if case == "complete" and profile.implementation_id == "mwnickerson":
                # Isolate aggregation: a successful native read is present both
                # as a protocol invocation and an evidence event. It counts once.
                from dataclasses import asdict

                from ori.eval.v2.mcp import EvidenceEventKind
                from ori.eval.v2.native_mcp_runtime import NativeCallOutcome

                trace_with_read = json.loads(json.dumps(trace))
                trace_with_read["protocol_calls"] = [
                    {
                        "operation": "read_resource",
                        "target": source.resource_uris[0],
                        "arguments": {},
                        "interrupted": False,
                        "duplicate_read": False,
                        "evidence_producing": False,
                        "outcome": asdict(NativeCallOutcome(True, 0.01, {"contents": []})),
                    }
                ]
                provider = SimpleNamespace(
                    **{
                        **record.model_dump(mode="python"),
                        "provider_metrics": {"native_execution": trace_with_read},
                        "mcp_events": (SimpleNamespace(kind=EvidenceEventKind.RESOURCE_READ),),
                    },
                )
                resource_state = SimpleNamespace(
                    checkpoint=checkpoint,
                    attempts=(
                        SimpleNamespace(
                            task_id=task.task_id,
                            provider=provider,
                            sample=sample,
                            scheduler_phase="initial",
                            recovery_round=0,
                        ),
                    ),
                )
                resource_metrics = _run_operational_metrics(resource_state, native=True)
                assert resource_metrics.resource_read_calls_total == 1
