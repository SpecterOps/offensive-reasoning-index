"""Native public contracts are explicitly compiled, not certified by configuration."""

import pytest

from ori.eval.v2.compiler import V2CompileError, _binding, compile_legacy_product
from ori.eval.v2.native_capability import build_native_capability_profile
from ori.eval.v2.native_mcp_profiles import get_native_implementation
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

simple_compiled = _simple_compiled


def _profile(name):
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


def test_explicit_native_compilation_preserves_claims_but_rebinds_contracts(
    simple_compiled, subtests,
):
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
    with pytest.raises(ValueError, match="NATIVE_PROOF_UNSUPPORTED"):
        kind = "decision" if implementation == "mwnickerson" else "set"
        unsupported = next(task for task in corpus.tasks if task.public.claim_kind == kind)
        offline_certify(unsupported, snapshot, native_profile=profile)
    from ori.eval.v2.certification import build_offline_certification_catalog

    with pytest.raises(ValueError, match="NATIVE_PROOF_UNSUPPORTED"):
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


def test_armadin_domain_offline_replay_is_not_live_admission(simple_compiled):
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


def test_main_native_routes_cross_offline_fixture_replay(simple_compiled):
    from ori.eval.v2.fixtures import offline_certify
    from ori.eval.v2.schema import CertificationState

    manifest, snapshot, _, _ = simple_compiled
    profile = _profile("mwnickerson")
    corpus = compile_legacy_product(
        manifest, snapshot, product="simple", track=Track.MCP, native_profile=profile,
    )
    routes = [task for task in corpus.tasks if task.public.claim_kind == "route"]
    assert routes
    for task in routes:
        certified = offline_certify(task, snapshot, native_profile=profile)
        assert certified.certification.state is CertificationState.OFFLINE_CERTIFIED
        assert certified.certification.live_proof_fingerprint is None
        names = {case.name for case in certified.fixtures.cases if case.applicable}
        assert {"perfect", "wrong", "empty", "reversed_edge", "disconnected_path"} <= names


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


def test_main_native_sets_cross_offline_fixture_replay(simple_compiled):
    from ori.eval.v2.fixtures import offline_certify
    from ori.eval.v2.schema import CertificationState

    manifest, snapshot, _, _ = simple_compiled
    profile = _profile("mwnickerson")
    corpus = compile_legacy_product(
        manifest, snapshot, product="simple", track=Track.MCP, native_profile=profile,
    )
    tasks = [task for task in corpus.tasks if task.public.claim_kind == "set"]
    assert tasks
    for task in tasks:
        certified = offline_certify(task, snapshot, native_profile=profile)
        assert certified.certification.state is CertificationState.OFFLINE_CERTIFIED
        assert certified.certification.live_proof_fingerprint is None
