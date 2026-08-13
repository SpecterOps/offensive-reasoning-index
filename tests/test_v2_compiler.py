from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest
from click.testing import CliRunner
from jsonschema import validate as validate_json_schema

from ori.cli import _build_manifest, main
from ori.eval.v2.campaign import (
    CampaignArtifactError,
    RunIdentity,
    build_checkpoint,
    build_public_report,
    build_run_provenance,
    guard_v2_output_directory,
    validate_checkpoint,
)
from ori.eval.v2.certification import (
    CertificationError,
    LiveCertificationCatalog,
    OfflineCertificationCatalog,
    build_catalog_release,
    build_fixture_parity_cases,
    build_live_certification_proof,
    build_offline_certification_catalog,
    build_projection_parity_cases,
    live_certify_corpus,
    live_certify_task,
    promote_candidate,
)
from ori.eval.v2.comparator import COMPARATOR_FINGERPRINT
from ori.eval.v2.compiler import (
    CompiledTask,
    V2CompileError,
    _extra_evidence_policy,
    _validate_binding_matches_claim,
    _validate_no_contradictory_oracles,
    compile_acceptance_spec,
    compile_legacy_product,
)
from ori.eval.v2.determinism import corpus_contract_shape_fingerprint
from ori.eval.v2.evidence import _KNOWN_TOP_LEVEL_FIELDS
from ori.eval.v2.fingerprint import canonical_sha256, certifier_fingerprint
from ori.eval.v2.fixtures import REQUIRED_FIXTURES, offline_certify
from ori.eval.v2.graph import LiveGraphVerification, build_archive_snapshot
from ori.eval.v2.mcp import build_mcp_capability_profile, classify_mcp_binding
from ori.eval.v2.profiles import capability_profile_for_track
from ori.eval.v2.protocol import (
    OracleRegistry,
    OracleV2Artifact,
    ProtocolDispatchError,
    V2ArtifactPair,
    build_artifacts,
    detect_protocol,
    load_oracles_v2,
    load_public_v2,
    load_v2_pair,
    write_artifacts,
)
from ori.eval.v2.public_surfaces import (
    PublicSurface,
    assert_solver_visible,
    build_all_solver_visible_envelopes,
    public_semantic_fingerprint,
)
from ori.eval.v2.schema import (
    AbsenceClaim,
    CertificationState,
    DecisionClaim,
    DecisionPolicy,
    ExecutionClass,
    ExtraEvidenceRule,
    MCPBindingMode,
    NegativeReasonCode,
    RouteAcceptanceKind,
    RouteClaim,
    SetClaim,
    TaskCertification,
    Track,
    VerdictStatus,
)
from ori.eval.v2.scoring import (
    AnswerSubmission,
    V2ScoringError,
    build_answers_artifact,
    score_answers_v2,
    summarize_results,
)
from ori.generator.attack_paths import plant_all_paths
from ori.generator.benchmark_profiles import build_benchmark_generation_profile
from ori.generator.graph import ADGraph
from ori.generator.org import build_org
from ori.generator.phase4 import build_phase4_complex_graph
from ori.generator.security import apply_baseline_security
from ori.generator.serializer import _build_zip


def _generated_product(product: str, seed: int):
    profile = build_benchmark_generation_profile(product, seed=seed)
    if product == "simple":
        graph = ADGraph(domain=profile.domain, seed=seed)
        build_org(
            graph,
            num_users=profile.users,
            num_workstations=profile.workstations,
            num_servers=profile.servers,
        )
        apply_baseline_security(graph)
        plant_all_paths(graph)
    else:
        graph = build_phase4_complex_graph(
            domain=profile.domain,
            seed=seed,
            users=profile.users,
            workstations=profile.workstations,
            servers=profile.servers,
        )
    archive = _build_zip(graph)
    manifest = _build_manifest(graph, seed, archive=archive)
    manifest["metadata"]["benchmark_name"] = product
    snapshot = build_archive_snapshot(archive, manifest, product=product)
    return manifest, snapshot


@pytest.fixture(scope="module")
def simple_compiled():
    manifest, snapshot = _generated_product("simple", 1234)
    return (
        manifest,
        snapshot,
        compile_legacy_product(
            manifest,
            snapshot,
            product="simple",
            track=Track.DIRECT,
        ),
        compile_legacy_product(
            manifest,
            snapshot,
            product="simple",
            track=Track.MCP,
        ),
    )


@pytest.fixture(scope="module")
def complex_compiled():
    manifest, snapshot = _generated_product("complex", 4401)
    return (
        manifest,
        snapshot,
        compile_legacy_product(
            manifest,
            snapshot,
            product="complex",
            track=Track.DIRECT,
        ),
        compile_legacy_product(
            manifest,
            snapshot,
            product="complex",
            track=Track.MCP,
        ),
    )


@pytest.fixture(scope="module")
def simple_certified(simple_compiled):
    _, snapshot, direct, mcp = simple_compiled
    return tuple(offline_certify(task, snapshot) for task in (*direct.tasks, *mcp.tasks))


@pytest.fixture(scope="module")
def complex_certified(complex_compiled):
    _, snapshot, direct, mcp = complex_compiled
    return tuple(offline_certify(task, snapshot) for task in (*direct.tasks, *mcp.tasks))


def _by_legacy(corpus, legacy_task_id: str):
    return [task for task in corpus.tasks if task.migration.legacy_task_id == legacy_task_id]


def _candidate_certifications(corpus, profile):
    candidates = {}
    for task in corpus.tasks:
        payload = {
            "task_id": task.public.task_id,
            "state": CertificationState.CANDIDATE,
            "task_fingerprint": task.public.task_fingerprint,
            "oracle_fingerprint": task.oracle.oracle_fingerprint,
            "graph_fingerprint": corpus.graph_fingerprint,
            "compiler_fingerprint": corpus.compiler_fingerprint,
            "comparator_fingerprint": COMPARATOR_FINGERPRINT,
            "certifier_fingerprint": certifier_fingerprint(),
            "capability_profile_fingerprint": profile.profile_fingerprint,
            "bounds_fingerprint": canonical_sha256(task.public.binding.bounds),
            "certified_profile_id": profile.profile_id,
            "live_proof_fingerprint": "a" * 64,
            "failures": (),
            "certification_fingerprint": "0" * 64,
        }
        payload["certification_fingerprint"] = canonical_sha256(
            payload,
            exclude_fields=("certification_fingerprint",),
        )
        candidates[task.public.task_id] = TaskCertification.model_validate(payload)
    return candidates


def test_simple_corpus_is_completely_migrated(simple_compiled) -> None:
    _, _, direct, mcp = simple_compiled

    assert len(direct.tasks) == 20
    assert len(mcp.tasks) == 40
    assert len({task.migration.legacy_task_id for task in direct.tasks}) == 20
    assert len({task.migration.legacy_task_id for task in mcp.tasks}) == 40
    assert all(task.public.binding.track is Track.DIRECT for task in direct.tasks)
    assert all(task.public.binding.track is Track.MCP for task in mcp.tasks)


def test_complex_corpus_replaces_oversized_enumerations(complex_compiled) -> None:
    _, _, direct, mcp = complex_compiled

    assert len({task.migration.legacy_task_id for task in direct.tasks}) == 42
    assert len({task.migration.legacy_task_id for task in mcp.tasks}) == 62
    assert len(direct.tasks) == 46
    assert len(mcp.tasks) == 70

    direct_pages = _by_legacy(direct, "global-admin-to")
    native_mcp_pages = _by_legacy(mcp, "mcp-global-admin-to")
    assert len(direct_pages) == 5
    assert len(native_mcp_pages) == 5
    assert all(task.migration.status == "replaced" for task in direct_pages)
    assert [
        task.oracle.claim.selection.offset  # type: ignore[union-attr]
        for task in direct_pages
    ] == [0, 500, 1000, 1500, 2000]
    for pages in (direct_pages, native_mcp_pages):
        assert [task.public.binding.bounds.page_size for task in pages] == [500] * 5
        assert [task.public.binding.bounds.result_offset for task in pages] == [
            0,
            500,
            1000,
            1500,
            2000,
        ]
        assert all(task.public.binding.bounds.max_pages == 1 for task in pages)
        assert all(
            f"offset {task.public.binding.bounds.result_offset} and limit 500"
            in task.public.question
            for task in pages
        )
    assert all(not task.public.binding.bounds.require_total_count for task in native_mcp_pages)
    assert all(task.public.binding.bounds.timeout_seconds == 555.0 for task in native_mcp_pages)

    page = native_mcp_pages[1]
    mismatched_binding = page.public.binding.model_copy(
        update={"bounds": page.public.binding.bounds.model_copy(update={"page_size": 100})}
    )
    with pytest.raises(V2CompileError, match="one matching execution page"):
        _validate_binding_matches_claim(page.oracle.claim, mismatched_binding)


@pytest.mark.parametrize(
    ("track", "expected_candidates"),
    ((Track.DIRECT, 42), (Track.MCP, 55)),
)
def test_complex_candidate_release_groups_exact_public_semantic_duplicates(
    complex_compiled,
    track: Track,
    expected_candidates: int,
) -> None:
    corpus = complex_compiled[2 if track is Track.DIRECT else 3]
    profile = capability_profile_for_track(track)
    candidates = _candidate_certifications(corpus, profile)
    release = build_catalog_release(
        corpus,
        candidates,
        profile,
    )

    assert len(corpus.tasks) == (46 if track is Track.DIRECT else 70)
    assert len(release.entries) == expected_candidates
    assert len({entry.public_semantic_fingerprint for entry in release.entries}) == len(
        release.entries
    )
    candidate_schema = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "docs"
            / "schemas"
            / "ori-v2-candidate-catalog.schema.json"
        ).read_text()
    )
    validate_json_schema(
        instance=release.model_dump(mode="json"),
        schema=candidate_schema,
    )

    tasks_by_id = {task.public.task_id: task for task in corpus.tasks}
    released_ids = []
    for entry in release.entries:
        assert entry.task_id == min(entry.equivalent_task_ids)
        assert entry.public_semantic_fingerprint == public_semantic_fingerprint(
            tasks_by_id[entry.task_id].public
        )
        assert {
            public_semantic_fingerprint(tasks_by_id[task_id].public)
            for task_id in entry.equivalent_task_ids
        } == {entry.public_semantic_fingerprint}
        released_ids.extend(entry.equivalent_task_ids)

    assert sorted(released_ids) == sorted(tasks_by_id)
    assert len(released_ids) == len(set(released_ids))

    duplicate_entry = next(
        entry for entry in release.entries if len(entry.equivalent_task_ids) > 1
    )
    alias_id = duplicate_entry.equivalent_task_ids[1]
    stale_candidates = {
        **candidates,
        alias_id: candidates[alias_id].model_copy(
            update={"task_fingerprint": "f" * 64}
        ),
    }
    with pytest.raises(CertificationError, match=f"task {alias_id} certification is stale"):
        build_catalog_release(corpus, stale_candidates, profile)

    contradictory_tasks = tuple(
        task.model_copy(
            update={
                "oracle": task.oracle.model_copy(
                    update={
                        "forbidden_entity_ids": (
                            *task.oracle.forbidden_entity_ids,
                            "contradictory-identity",
                        )
                    }
                )
            }
        )
        if task.public.task_id == alias_id
        else task
        for task in corpus.tasks
    )
    contradictory_corpus = corpus.model_copy(update={"tasks": contradictory_tasks})
    with pytest.raises(
        CertificationError,
        match="identical public semantics bind contradictory candidate oracles",
    ):
        build_catalog_release(contradictory_corpus, candidates, profile)


def test_changed_seed_preserves_task_contract_shape_and_resolves_new_identity(
    simple_compiled,
) -> None:
    _, first_snapshot, first_direct, first_mcp = simple_compiled
    manifest, changed_snapshot = _generated_product("simple", 5678)
    changed_direct = compile_legacy_product(
        manifest,
        changed_snapshot,
        product="simple",
        track=Track.DIRECT,
    )
    changed_mcp = compile_legacy_product(
        manifest,
        changed_snapshot,
        product="simple",
        track=Track.MCP,
    )

    assert first_snapshot.graph_fingerprint != changed_snapshot.graph_fingerprint
    for first, changed in (
        (first_direct, changed_direct),
        (first_mcp, changed_mcp),
    ):
        assert [task.public.task_id for task in first.tasks] == [
            task.public.task_id for task in changed.tasks
        ]
        assert corpus_contract_shape_fingerprint(first) == corpus_contract_shape_fingerprint(
            changed
        )
        assert first.catalog_fingerprint != changed.catalog_fingerprint


def test_vertical_slice_claims_are_typed_and_correct(complex_compiled) -> None:
    _, _, direct, mcp = complex_compiled

    membership = _by_legacy(direct, "t1_group_membership-01")[0]
    assert isinstance(membership.oracle.claim, RouteClaim)
    assert membership.public.answer_policy.kind == "exact_route"
    assert membership.oracle.required_mechanisms == ("MemberOf",)
    assert membership.public.binding.bounds.require_stable_ordering is False

    nested_route = _by_legacy(direct, "t2_nested_groups-02")[0]
    assert nested_route.public.answer_policy.kind == "mechanism_valid_route"
    assert nested_route.public.acceptance_spec.required_mechanisms == (
        "MemberOf",
        "MemberOf",
    )
    assert "MemberOf -> MemberOf" in nested_route.public.question

    sessions = _by_legacy(direct, "t1_has_session-01")[0]
    assert isinstance(sessions.oracle.claim, SetClaim)
    assert len(sessions.oracle.expected_entities) == 391
    assert [step.semantics.value for step in sessions.oracle.claim.selection.relationships] == [
        "direct",
        "transitive",
    ]
    session_membership = sessions.oracle.claim.selection.relationships[1]
    assert (
        f"within {session_membership.max_hops} MemberOf hops"
        in sessions.public.question
    )

    da_members = _by_legacy(direct, "global-da-members")[0]
    assert len(da_members.oracle.expected_entities) == 561
    assert da_members.public.binding.bounds.require_stable_ordering is False
    assert da_members.public.binding.bounds.max_result_cardinality == 1000

    direct_page = _by_legacy(direct, "global-admin-to")[0]
    assert direct_page.public.binding.bounds.result_offset == 0
    assert direct_page.public.binding.bounds.max_result_cardinality == 500
    assert direct_page.public.binding.bounds.require_stable_ordering is True

    mcp_da_members = _by_legacy(mcp, "global-da-members")[0]
    assert len(mcp_da_members.oracle.expected_entities) == 561
    assert mcp_da_members.public.binding.bounds.page_size == 500
    assert mcp_da_members.public.binding.bounds.max_pages == 2
    assert mcp_da_members.public.binding.bounds.max_result_cardinality == 1000
    assert mcp_da_members.public.binding.bounds.require_total_count is True
    assert mcp_da_members.public.binding.bounds.require_stable_ordering is True
    assert mcp_da_members.public.binding.bounds.timeout_seconds == 600.0

    direct_members = _by_legacy(mcp, "mcp-global-da-direct-members")[0]
    direct_count = _by_legacy(mcp, "mcp-global-da-direct-member-count")[0]
    privileged_groups = _by_legacy(mcp, "mcp-user-privileged-group-memberships")[0]
    active_sessions = _by_legacy(
        mcp,
        "mcp-computer-active-sessions-unconstrained",
    )[0]
    assert len(direct_members.oracle.expected_entities) == 21
    assert direct_members.public.binding.bounds.max_result_cardinality == 1000
    assert direct_members.public.binding.bounds.max_pages == 2
    assert direct_count.oracle.expected_count == 21
    assert direct_count.public.binding.bounds.max_result_cardinality == 1
    assert len(privileged_groups.oracle.expected_entities) == 2
    privileged_membership = privileged_groups.oracle.claim.selection.relationships[0]
    assert (
        f"within {privileged_membership.max_hops} MemberOf hops"
        in privileged_groups.public.question
    )
    assert "declared subject computer" in active_sessions.public.question
    assert "unconstrained-delegation computer" not in active_sessions.public.question

    adcs = _by_legacy(direct, "t4_adcs_esc1-01")[0]
    assert isinstance(adcs.oracle.claim, DecisionClaim)
    assert adcs.oracle.expected_decision is True
    assert len(adcs.oracle.required_context) == 3
    assert len(adcs.oracle.required_properties) == 3

    negative = _by_legacy(direct, "t6_negative_control_invalid_cert-01")[0]
    assert isinstance(negative.oracle.claim, AbsenceClaim)
    input_roles = [entity.role for entity in negative.public.input_entities]
    assert set(input_roles) == {"source", "objective"}
    assert len(input_roles) == len(set(input_roles))
    assert negative.public.question.count("objective=") == 1
    assert {witness.reason_code for witness in negative.oracle.negative_witnesses} == {
        NegativeReasonCode.OBJECTIVE_UNREACHABLE,
    }
    assert negative.oracle.required_context == ()
    assert negative.oracle.required_properties == ()


def test_route_repairs_are_generic_context_not_comparator_exceptions(
    complex_compiled,
) -> None:
    _, _, direct, _ = complex_compiled
    rbcd = _by_legacy(direct, "t6_host_session_pivot_rbcd_tier0-01")[0]
    acl = _by_legacy(direct, "t6_acl_group_nesting_tier0-01")[0]

    assert rbcd.migration.status == "replaced"
    assert acl.migration.status == "replaced"
    assert len(rbcd.oracle.required_context) == 1
    assert len(acl.oracle.required_context) == 1
    assert all(
        edge in task.oracle.graph_edge_registry
        for task in (rbcd, acl)
        for edge in task.oracle.required_context
    )


def test_public_bundles_are_sealed_and_fingerprints_bind_claims(
    complex_compiled,
) -> None:
    _, _, direct, mcp = complex_compiled
    forbidden = {
        "reference_cypher",
        "ref_result",
        "valid_node_names",
        "expected_entities",
        "route_variants",
        "oracle_fingerprint",
    }

    for task in (*direct.tasks, *mcp.tasks):
        serialized = json.dumps(task.public.model_dump(mode="json"), sort_keys=True)
        assert all(field not in serialized for field in forbidden)
        assert task.public.claim_fingerprint == canonical_sha256(task.oracle.claim)
        assert task.public.task_fingerprint == task.oracle.task_fingerprint
        assert task.oracle.graph_fingerprint == direct.graph_fingerprint


def test_oracle_sentinel_never_reaches_any_solver_visible_surface(
    complex_compiled,
) -> None:
    _, _, direct, _ = complex_compiled
    compiled = direct.tasks[0]
    sentinel = "ORI_ORACLE_SENTINEL_4d624ea1"
    sealed = compiled.oracle.model_copy(
        update={
            "oracle_id": sentinel,
            "oracle_fingerprint": canonical_sha256({"sentinel": sentinel}),
        }
    )

    assert sentinel in sealed.model_dump_json()
    envelopes = build_all_solver_visible_envelopes(compiled.public)
    assert {envelope.surface for envelope in envelopes} == set(PublicSurface)
    assert {envelope.surface.value for envelope in envelopes} == {
        "provider_request",
        "inspect_metadata",
        "transcript",
        "csv",
        "telemetry",
        "public_export",
    }
    for envelope in envelopes:
        payload = envelope.model_dump(mode="json")
        assert_solver_visible(payload, sentinels=(sentinel,))
        assert sentinel not in json.dumps(payload, sort_keys=True)


def test_solver_visible_redaction_recursively_rejects_keys_and_sentinels() -> None:
    assert_solver_visible(
        {
            "acceptance_spec": {
                "required_mechanisms": ["MemberOf"],
                "required_context": [],
                "required_properties": [],
            }
        }
    )
    with pytest.raises(ValueError, match="scorer-only field"):
        assert_solver_visible({"nested": [{"reference_results": ["secret"]}]})
    with pytest.raises(ValueError, match="oracle sentinel"):
        assert_solver_visible(
            {"events": [{"text": "prefix ORACLE_SECRET suffix"}]},
            sentinels=("ORACLE_SECRET",),
        )


def test_public_questions_name_every_required_input_without_exposing_outputs(
    complex_compiled,
) -> None:
    _, _, direct, mcp = complex_compiled

    for task in (*direct.tasks, *mcp.tasks):
        for entity in task.public.input_entities:
            display = entity.canonical_name or entity.object_id
            assert display in task.public.question
        assert {entity.object_id for entity in task.public.input_entities}.issubset(
            {entity.object_id for entity in task.oracle.resolved_roles}
        )

    privileged_groups = _by_legacy(mcp, "mcp-user-privileged-group-memberships")[0]
    assert "WTORRES@" in privileged_groups.public.question
    assert {entity.object_id for entity in privileged_groups.public.input_entities}.isdisjoint(
        {entity.object_id for entity in privileged_groups.oracle.expected_entities}
    )

    adcs = _by_legacy(direct, "t4_adcs_esc1-01")[0]
    assert len(adcs.public.input_entities) == 2
    assert len(adcs.public.input_entities) < len(adcs.oracle.expected_entities)


def test_acceptance_spec_is_the_complete_public_semantic_contract(
    complex_compiled,
) -> None:
    _, _, direct, mcp = complex_compiled

    for task in (*direct.tasks, *mcp.tasks):
        expected = compile_acceptance_spec(
            task.oracle.claim,
            task.public.answer_policy,
            task.public.binding,
        )
        assert task.public.acceptance_spec == expected
        assert task.public.acceptance_spec.bounds == task.public.binding.bounds
        serialized = task.public.acceptance_spec.model_dump_json()
        assert all(entity.object_id not in serialized for entity in task.oracle.resolved_roles)
        for envelope in build_all_solver_visible_envelopes(task.public):
            assert envelope.acceptance_spec == expected

    routes = [
        task for task in (*direct.tasks, *mcp.tasks) if isinstance(task.oracle.claim, RouteClaim)
    ]
    assert routes
    for task in routes:
        acceptance = task.public.acceptance_spec
        if acceptance.route_acceptance is RouteAcceptanceKind.ANY_GRAPH_VALID:
            assert not acceptance.required_mechanisms
            assert "any graph-valid route" in task.public.question
        else:
            assert acceptance.required_mechanisms
            assert all(
                mechanism in task.public.question for mechanism in acceptance.required_mechanisms
            )


def test_decision_entity_closure_policy_is_solver_visible() -> None:
    closed = DecisionPolicy(
        kind="decision",
        require_evidence_entities=False,
        forbid_unrelated_entities=True,
    )
    open_policy = closed.model_copy(
        update={"forbid_unrelated_entities": False}
    )

    assert (
        _extra_evidence_policy(closed).entities
        is ExtraEvidenceRule.REQUIRE_EVIDENCE_CLOSURE
    )
    assert (
        _extra_evidence_policy(open_policy).entities
        is ExtraEvidenceRule.ALLOW_TRUTHFUL
    )


def test_scorer_constraints_cannot_diverge_from_public_acceptance(
    complex_compiled,
) -> None:
    _, _, direct, _ = complex_compiled
    route = next(
        task
        for task in direct.tasks
        if isinstance(task.oracle.claim, RouteClaim) and task.oracle.required_mechanisms
    )
    contradictory = route.oracle.model_copy(
        update={"required_mechanisms": ("ContradictoryMechanism",)}
    )

    with pytest.raises(
        ValueError,
        match="oracle mechanisms are not declared by public acceptance",
    ):
        CompiledTask(
            public=route.public,
            oracle=contradictory,
            migration=route.migration,
        )


def test_identical_solver_visible_contracts_cannot_bind_contradictory_oracles(
    complex_compiled,
) -> None:
    _, _, direct, _ = complex_compiled
    original = next(
        task
        for task in direct.tasks
        if isinstance(task.oracle.claim, SetClaim) and len(task.oracle.expected_entities) > 1
    )
    contradictory = CompiledTask(
        public=original.public,
        oracle=original.oracle.model_copy(
            update={"expected_entities": original.oracle.expected_entities[:-1]}
        ),
        migration=original.migration,
    )

    with pytest.raises(
        ValueError,
        match="identical solver-visible contracts bind contradictory oracles",
    ):
        _validate_no_contradictory_oracles((original, contradictory))


def test_direct_user_and_principal_membership_contracts_are_distinguishable(
    complex_compiled,
) -> None:
    _, _, _, mcp = complex_compiled
    users = _by_legacy(mcp, "t1_group_membership-02")[0]
    principals = _by_legacy(mcp, "mcp-global-da-direct-members")[0]

    assert users.public.acceptance_spec.selection is not None
    assert principals.public.acceptance_spec.selection is not None
    assert users.public.acceptance_spec.selection.projection_type == "User"
    assert principals.public.acceptance_spec.selection.projection_type == "Principal"
    assert "direct User members" in users.public.question
    assert "direct Principal members" in principals.public.question
    assert users.public.prompt_fingerprint != principals.public.prompt_fingerprint


def test_claim_kinds_cover_full_complex_catalog(complex_compiled) -> None:
    _, _, direct, mcp = complex_compiled

    direct_kinds = Counter(task.public.claim_kind for task in direct.tasks)
    mcp_kinds = Counter(task.public.claim_kind for task in mcp.tasks)
    assert direct_kinds == {
        "route": 28,
        "set": 14,
        "decision": 3,
        "absence": 1,
    }
    assert mcp_kinds == {
        "route": 33,
        "set": 32,
        "count": 1,
        "decision": 3,
        "absence": 1,
    }


def test_legacy_compiler_rejects_v2_or_unknown_manifests(simple_compiled) -> None:
    manifest, snapshot, _, _ = simple_compiled
    incompatible = dict(manifest)
    incompatible["schema_version"] = "ori-generated-manifest-v3"

    with pytest.raises(V2CompileError, match="requires ori-generated-manifest-v2"):
        compile_legacy_product(
            incompatible,
            snapshot,
            product="simple",
            track=Track.DIRECT,
        )


def test_every_simple_candidate_is_offline_certified(simple_certified) -> None:
    assert len(simple_certified) == 60
    for result in simple_certified:
        assert result.certification.state.value == "offline-certified"
        assert set(REQUIRED_FIXTURES).issubset(case.name for case in result.fixtures.cases)


def test_every_complex_candidate_is_offline_certified(complex_certified) -> None:
    assert len(complex_certified) == 116
    for result in complex_certified:
        assert result.certification.state.value == "offline-certified"
        assert set(REQUIRED_FIXTURES).issubset(case.name for case in result.fixtures.cases)
        assert all(
            not case.applicable or case.actual_status is case.expected_status
            for case in result.fixtures.cases
        )


def _assert_every_fixture_crosses_its_declared_adapter(
    snapshot,
    corpora,
    certifications,
) -> None:
    offline_by_task = {item.certification.task_id: item for item in certifications}
    for corpus in corpora:
        profile = capability_profile_for_track(corpus.track)
        for task in corpus.tasks:
            offline = offline_by_task[task.public.task_id]
            projections = build_projection_parity_cases(
                task,
                offline,
                profile,
                archive_snapshot=snapshot,
                live_snapshot=snapshot,
            )
            assert [case.name for case in projections] == [
                case.name for case in offline.fixtures.cases
            ]
            perfect = next(case for case in projections if case.name == "perfect")
            assert perfect.applicable is True
            assert perfect.archive_projection_source == "graph_snapshot_replay"
            assert perfect.live_projection_source == "graph_snapshot_replay"
            assert perfect.archive_execution_class is ExecutionClass.SUCCESS
            assert perfect.archive_outcome.value == "COMPLETED"
            assert perfect.archive_verdict_status is VerdictStatus.CORRECT
            assert perfect.live_verdict_status is VerdictStatus.CORRECT

            for fixture, projection in zip(
                offline.fixtures.cases,
                projections,
                strict=True,
            ):
                assert projection.applicable is fixture.applicable
                if fixture.applicable:
                    assert projection.expected_status is fixture.expected_status
                    assert projection.archive_raw_source_digest is not None
                    assert projection.live_raw_source_digest is not None
                else:
                    assert projection.inapplicable_reason


def test_every_simple_fixture_crosses_direct_and_mcp_adapters(
    simple_compiled,
    simple_certified,
) -> None:
    _, snapshot, direct, mcp = simple_compiled
    _assert_every_fixture_crosses_its_declared_adapter(
        snapshot,
        (direct, mcp),
        simple_certified,
    )


def test_every_complex_fixture_crosses_direct_and_mcp_adapters(
    complex_compiled,
    complex_certified,
) -> None:
    _, snapshot, direct, mcp = complex_compiled
    _assert_every_fixture_crosses_its_declared_adapter(
        snapshot,
        (direct, mcp),
        complex_certified,
    )


def test_every_mcp_candidate_is_supported_by_the_pinned_capability_profile(
    simple_compiled,
    complex_compiled,
) -> None:
    profile = build_mcp_capability_profile()
    simple_mcp = simple_compiled[3]
    complex_mcp = complex_compiled[3]

    classifications = {
        task.public.task_id: classify_mcp_binding(task.public, profile)
        for task in (*simple_mcp.tasks, *complex_mcp.tasks)
    }

    assert set(classifications.values()) == {MCPBindingMode.CYPHER_ENABLED}


def test_v1_v2_dispatch_and_sealed_oracle_registry(simple_compiled, tmp_path) -> None:
    manifest, snapshot, direct, _ = simple_compiled
    public, private = build_artifacts(
        direct,
        identity_catalog=snapshot.entities,
    )

    assert detect_protocol(manifest) == "v1"
    assert detect_protocol(public.model_dump(mode="json")) == "v2"
    assert load_public_v2(public.model_dump(mode="json")) == public
    assert load_oracles_v2(private.model_dump(mode="json")) == private

    with pytest.raises(ProtocolDispatchError):
        load_public_v2(manifest)
    with pytest.raises(ProtocolDispatchError):
        load_oracles_v2(public.model_dump(mode="json"))

    registry = OracleRegistry(private)
    oracle = private.oracles[0]
    assert registry.get(oracle.oracle_id, oracle.oracle_fingerprint) == oracle
    assert registry.for_task(oracle.task_id) == oracle
    with pytest.raises(ProtocolDispatchError, match="fingerprint mismatch"):
        registry.get(oracle.oracle_id, "0" * 64)

    public_path = tmp_path / "public.json"
    oracle_path = tmp_path / "private" / "oracles.json"
    written_public, written_private = write_artifacts(
        direct,
        public_path=public_path,
        oracle_path=oracle_path,
        identity_catalog=snapshot.entities,
    )
    assert load_public_v2(public_path) == written_public
    assert load_oracles_v2(oracle_path) == written_private
    serialized_public = public_path.read_text()
    assert "reference_cypher" not in serialized_public
    assert "expected_entities" not in serialized_public
    assert "oracle_fingerprint" not in serialized_public
    pair = load_v2_pair(public_path, oracle_path)
    assert pair.public == written_public
    assert pair.private == written_private


def test_public_semantic_fingerprint_excludes_provenance_but_binds_contract(
    simple_compiled,
) -> None:
    task = simple_compiled[2].tasks[0].public
    baseline = public_semantic_fingerprint(task)
    provenance_changed = task.model_copy(
        update={
            "task_id": "renamed@2",
            "revision": task.revision + 1,
            "product": "renamed-product",
            "input_entities": tuple(
                entity.model_copy(
                    update={
                        "object_id": f"resolved-{index}",
                        "domain": "RESOLVED.INVALID",
                        "canonical_name": f"resolved-{index}",
                        "aliases": (),
                    }
                )
                for index, entity in enumerate(task.input_entities)
            ),
            "claim_fingerprint": "a" * 64,
            "prompt_fingerprint": "b" * 64,
            "task_fingerprint": "c" * 64,
        }
    )

    assert public_semantic_fingerprint(provenance_changed) == baseline
    assert public_semantic_fingerprint(
        task.model_copy(update={"question": f"{task.question} changed"})
    ) != baseline
    assert public_semantic_fingerprint(
        task.model_copy(update={"generic_instructions": (*task.generic_instructions, "changed")})
    ) != baseline
    assert public_semantic_fingerprint(
        task.model_copy(
            update={
                "binding": task.binding.model_copy(
                    update={"capability_profile_id": "changed-profile"}
                )
            }
        )
    ) != baseline


def test_v2_pair_rejects_wrong_track_and_incomplete_identity_catalog(
    simple_compiled,
) -> None:
    _, snapshot, direct, mcp = simple_compiled
    public_direct, private_direct = build_artifacts(
        direct,
        identity_catalog=snapshot.entities,
    )
    _, private_mcp = build_artifacts(
        mcp,
        identity_catalog=snapshot.entities,
    )

    with pytest.raises(ProtocolDispatchError, match="incomplete"):
        build_artifacts(
            direct,
            identity_catalog=snapshot.entities[:-1],
        )
    with pytest.raises(ValueError, match="pair mismatch"):
        load_v2_pair(
            public_direct.model_dump(mode="json"),
            private_mcp.model_dump(mode="json"),
        )

    truncated_payload = private_direct.model_dump(mode="python")
    truncated_payload["oracles"] = private_direct.oracles[:-1]
    truncated_payload["oracle_catalog_fingerprint"] = canonical_sha256(truncated_payload["oracles"])
    truncated_payload["artifact_fingerprint"] = "0" * 64
    truncated_payload["artifact_fingerprint"] = canonical_sha256(
        truncated_payload,
        exclude_fields=("artifact_fingerprint",),
    )
    truncated_private = OracleV2Artifact.model_validate(truncated_payload)
    with pytest.raises(ValueError, match="task sets differ"):
        V2ArtifactPair(
            public=public_direct,
            private=truncated_private,
        )


def _perfect_answers(corpus, snapshot):
    answers = {}
    for task in corpus.tasks:
        certification = offline_certify(task, snapshot)
        perfect = next(case for case in certification.fixtures.cases if case.name == "perfect")
        assert perfect.evidence is not None
        evidence = perfect.evidence
        evidence_payload = {
            "entities": [entity.object_id for entity in evidence.entities],
            "edges": [
                {
                    "source_id": edge.source_id,
                    "relationship": edge.relationship,
                    "target_id": edge.target_id,
                    "direction": edge.direction.value,
                    "properties": {fact.key: fact.value for fact in edge.properties},
                }
                for edge in evidence.edges
            ],
            "count": evidence.count,
            "decision": evidence.decision,
            "path_status": evidence.path_status.value,
            "supporting_edges": [
                {
                    "source_id": edge.source_id,
                    "relationship": edge.relationship,
                    "target_id": edge.target_id,
                    "direction": edge.direction.value,
                    "properties": {fact.key: fact.value for fact in edge.properties},
                }
                for edge in evidence.supporting_edges
            ],
            "observed_properties": [
                {
                    "entity_id": fact.entity_id,
                    "key": fact.key,
                    "value": fact.value,
                }
                for fact in evidence.observed_properties
            ],
            "negative_reason_codes": [reason.value for reason in evidence.negative_reason_codes],
        }
        answers[task.public.task_id] = {
            key: evidence_payload[key]
            for key in task.public.answer_schema["properties"]
            if evidence_payload.get(key) is not None
        }
    return answers


def test_every_compiled_perfect_answer_matches_its_public_schema(
    complex_compiled,
) -> None:
    _, snapshot, direct, mcp = complex_compiled

    for corpus in (direct, mcp):
        answers = _perfect_answers(corpus, snapshot)
        for task in corpus.tasks:
            validate_json_schema(
                answers[task.public.task_id],
                task.public.answer_schema,
            )


def test_absence_prompt_does_not_offer_fields_forbidden_by_its_schema(
    complex_compiled,
) -> None:
    _, _, direct, mcp = complex_compiled

    for corpus in (direct, mcp):
        task = next(
            item.public
            for item in corpus.tasks
            if item.public.claim_kind == "absence"
        )
        assert "supporting_edges" not in task.answer_schema["properties"]
        assert "observed_properties" not in task.answer_schema["properties"]
        assert "additional supporting edges" not in task.question
        assert "additional observed properties" not in task.question


def test_every_public_answer_field_has_a_shared_evidence_ir_consumer(
    complex_compiled,
) -> None:
    _, _, direct, mcp = complex_compiled

    for task in (*direct.tasks, *mcp.tasks):
        assert set(task.public.answer_schema["properties"]).issubset(_KNOWN_TOP_LEVEL_FIELDS)


def test_offline_scoring_uses_sealed_identity_catalog_and_shared_comparator(
    simple_compiled,
) -> None:
    _, snapshot, direct, _ = simple_compiled
    public, private = build_artifacts(
        direct,
        identity_catalog=snapshot.entities,
    )
    answers = build_answers_artifact(
        public,
        _perfect_answers(direct, snapshot),
    )

    scoring = score_answers_v2(public, private, answers)

    assert len(private.identity_catalog) == len(snapshot.entities)
    assert scoring.summary.scheduled == len(public.tasks)
    assert scoring.summary.correct == len(public.tasks)
    assert scoring.summary.incorrect == 0
    assert scoring.summary.campaign_valid is True
    assert all(result.verdict is not None for result in scoring.results)


def test_forged_answer_reference_data_cannot_affect_a_v2_verdict(
    simple_compiled,
) -> None:
    _, snapshot, direct, _ = simple_compiled
    public, private = build_artifacts(
        direct,
        identity_catalog=snapshot.entities,
    )
    raw_answers = _perfect_answers(direct, snapshot)
    first_task_id = public.tasks[0].task_id
    raw_answers[first_task_id]["correct"] = True
    answers = build_answers_artifact(public, raw_answers)

    scoring = score_answers_v2(public, private, answers)
    forged = next(result for result in scoring.results if result.task_id == first_task_id)

    assert forged.outcome.value == "OUTPUT_INVALID"
    assert forged.reasoning_correct is False
    assert forged.verdict is None
    assert scoring.summary.incorrect == 1


def test_offline_scoring_treats_strict_schema_failure_as_output_invalid(
    simple_compiled,
) -> None:
    _, snapshot, _, mcp = simple_compiled
    public, private = build_artifacts(
        mcp,
        identity_catalog=snapshot.entities,
    )
    raw_answers = _perfect_answers(mcp, snapshot)
    count_task = next(task for task in public.tasks if task.claim_kind == "count")
    raw_answers[count_task.task_id]["count"] = "not-an-integer"
    answers = build_answers_artifact(public, raw_answers)

    scoring = score_answers_v2(public, private, answers)
    result = next(item for item in scoring.results if item.task_id == count_task.task_id)

    assert result.execution_class is ExecutionClass.MODEL_FAILURE
    assert result.outcome.value == "OUTPUT_INVALID"
    assert result.reasoning_correct is False
    assert result.verdict is None


def test_offline_scoring_contains_unexpected_comparator_failure(
    simple_compiled,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, snapshot, direct, _ = simple_compiled
    public, private = build_artifacts(
        direct,
        identity_catalog=snapshot.entities,
    )
    answers = build_answers_artifact(
        public,
        _perfect_answers(direct, snapshot),
    )

    def raise_internal_error(*args: object, **kwargs: object):
        raise AttributeError("internal comparator defect")

    monkeypatch.setattr("ori.eval.v2.scoring.compare", raise_internal_error)

    scoring = score_answers_v2(public, private, answers)

    assert scoring.summary.harness_failures == len(public.tasks)
    assert scoring.summary.campaign_valid is False
    assert scoring.summary.invalid_reasons == ("HARNESS_FAILURE",)
    assert all(
        result.execution_class is ExecutionClass.HARNESS_FAILURE
        and result.reasoning_correct is None
        and result.verdict is None
        for result in scoring.results
    )


def test_v2_scoring_rejects_missing_duplicate_unknown_and_stale_tasks(
    simple_compiled,
) -> None:
    _, snapshot, direct, _ = simple_compiled
    public, private = build_artifacts(
        direct,
        identity_catalog=snapshot.entities,
    )
    raw_answers = _perfect_answers(direct, snapshot)
    valid = build_answers_artifact(public, raw_answers)
    submissions = list(valid.answers)

    duplicate = build_answers_artifact(
        public,
        (*submissions, submissions[0]),
    )
    with pytest.raises(V2ScoringError, match="duplicates"):
        score_answers_v2(public, private, duplicate)

    missing = build_answers_artifact(public, submissions[1:])
    with pytest.raises(V2ScoringError, match="missing"):
        score_answers_v2(public, private, missing)

    unknown_submission = AnswerSubmission(
        task_id="unknown.task@2",
        task_fingerprint="f" * 64,
        answer={},
    )
    unknown = build_answers_artifact(
        public,
        (*submissions[:-1], unknown_submission),
    )
    with pytest.raises(V2ScoringError, match="unknown"):
        score_answers_v2(public, private, unknown)

    stale_submission = submissions[0].model_copy(update={"task_fingerprint": "f" * 64})
    stale = build_answers_artifact(
        public,
        (stale_submission, *submissions[1:]),
    )
    with pytest.raises(V2ScoringError, match="fingerprint mismatch"):
        score_answers_v2(public, private, stale)


def test_candidate_promotion_and_catalog_bind_every_certification_dimension(
    simple_compiled,
    simple_certified,
) -> None:
    _, snapshot, direct, _ = simple_compiled
    offline_by_task = {item.certification.task_id: item for item in simple_certified}
    profile = capability_profile_for_track(Track.DIRECT)
    candidates = {}

    for task in direct.tasks:
        offline = offline_by_task[task.public.task_id]
        proof = build_live_certification_proof(
            task,
            offline,
            profile,
            graph_fingerprint_before=direct.graph_fingerprint,
            graph_fingerprint_after=direct.graph_fingerprint,
            parity_cases=build_fixture_parity_cases(
                task,
                offline,
                snapshot,
            ),
            projection_cases=build_projection_parity_cases(
                task,
                offline,
                profile,
                archive_snapshot=snapshot,
                live_snapshot=snapshot,
            ),
        )
        candidates[task.public.task_id] = promote_candidate(
            task,
            offline,
            profile,
            proof,
        )

    release = build_catalog_release(direct, candidates, profile)
    assert len(release.entries) == len(
        {public_semantic_fingerprint(task.public) for task in direct.tasks}
    )
    assert {entry.cost_band for entry in release.entries} <= {
        "low",
        "medium",
        "high",
    }
    assert all(entry.path_concentration_key for entry in release.entries)
    assert all(entry.certification_fingerprint for entry in release.entries)

    stale = direct.tasks[0].public.model_copy(update={"task_fingerprint": "f" * 64})
    stale_task = direct.tasks[0].model_copy(update={"public": stale})
    with pytest.raises(CertificationError, match="cannot be promoted"):
        promote_candidate(
            stale_task,
            offline_by_task[direct.tasks[0].public.task_id],
            profile,
            build_live_certification_proof(
                direct.tasks[0],
                offline_by_task[direct.tasks[0].public.task_id],
                profile,
                graph_fingerprint_before=direct.graph_fingerprint,
                graph_fingerprint_after=direct.graph_fingerprint,
                parity_cases=build_fixture_parity_cases(
                    direct.tasks[0],
                    offline_by_task[direct.tasks[0].public.task_id],
                    snapshot,
                ),
                projection_cases=build_projection_parity_cases(
                    direct.tasks[0],
                    offline_by_task[direct.tasks[0].public.task_id],
                    profile,
                    archive_snapshot=snapshot,
                    live_snapshot=snapshot,
                ),
            ),
        )


def test_live_fixture_parity_promotes_without_a_model_campaign(
    simple_compiled,
    simple_certified,
) -> None:
    _, snapshot, direct, _ = simple_compiled
    task = direct.tasks[0]
    offline = next(
        item for item in simple_certified if item.certification.task_id == task.public.task_id
    )
    profile = capability_profile_for_track(Track.DIRECT)

    parity = build_fixture_parity_cases(task, offline, snapshot)
    proof, certification = live_certify_task(
        task,
        offline,
        profile,
        archive_snapshot=snapshot,
        live_snapshot_before=snapshot,
        live_snapshot_after=snapshot,
    )

    assert {"perfect", "wrong", "empty"}.issubset({case.name for case in parity if case.applicable})
    assert proof.graph_fingerprint_before == snapshot.graph_fingerprint
    assert certification.state.value == "candidate"


def test_live_catalog_promotion_requires_exact_receipts_and_task_accounting(
    simple_compiled,
) -> None:
    _, snapshot, direct, _ = simple_compiled
    profile = capability_profile_for_track(Track.DIRECT)
    offline = build_offline_certification_catalog(direct, snapshot, profile)
    receipt_payload = {
        "expected_graph_fingerprint": snapshot.graph_fingerprint,
        "observed_graph_fingerprint": snapshot.graph_fingerprint,
        "page_size": 1000,
        "object_queries": 1,
        "relationship_queries": 1,
        "object_count": len(snapshot.objects),
        "relationship_count": len(snapshot.relationships),
        "normalized_artifacts": (),
        "verification_fingerprint": "0" * 64,
    }
    receipt_payload["verification_fingerprint"] = canonical_sha256(
        {
            "schema_version": "ori-live-graph-verification-v2",
            **receipt_payload,
        },
        exclude_fields=("verification_fingerprint",),
    )
    receipt = LiveGraphVerification.model_validate(receipt_payload)

    catalog = live_certify_corpus(
        direct,
        offline,
        profile,
        archive_snapshot=snapshot,
        live_snapshot_before=snapshot,
        live_snapshot_after=snapshot,
        verification_before=receipt,
        verification_after=receipt,
    )

    assert len(catalog.proofs) == len(direct.tasks)
    assert len(catalog.certifications) == len(direct.tasks)
    assert len(catalog.candidate_catalog.entries) == len(
        {public_semantic_fingerprint(task.public) for task in direct.tasks}
    )
    assert all(certification.state.value == "candidate" for certification in catalog.certifications)
    legacy_offline = offline.model_dump(mode="python")
    legacy_offline["schema_version"] = "ori-eval-offline-certification-v3"
    with pytest.raises(ValueError, match="schema_version"):
        OfflineCertificationCatalog.model_validate(legacy_offline)
    legacy_live = catalog.model_dump(mode="python")
    legacy_live["schema_version"] = "ori-eval-live-certification-v3"
    with pytest.raises(ValueError, match="schema_version"):
        LiveCertificationCatalog.model_validate(legacy_live)
    candidate_schema = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "docs"
            / "schemas"
            / "ori-v2-candidate-catalog.schema.json"
        ).read_text()
    )
    validate_json_schema(
        instance=catalog.candidate_catalog.model_dump(mode="json"),
        schema=candidate_schema,
    )


def test_v2_checkpoint_output_guard_and_public_report_are_exact_and_redacted(
    simple_compiled,
    tmp_path,
) -> None:
    _, snapshot, direct, mcp = simple_compiled
    public, private = build_artifacts(
        direct,
        identity_catalog=snapshot.entities,
    )
    pair = V2ArtifactPair(public=public, private=private)
    profile = capability_profile_for_track(Track.DIRECT)
    answers = build_answers_artifact(
        public,
        _perfect_answers(direct, snapshot),
    )
    scoring = score_answers_v2(public, private, answers)
    identity = RunIdentity(
        provider="fixture",
        model="deterministic",
        run_index=1,
        target_fingerprint="a" * 64,
    )

    checkpoint = build_checkpoint(
        pair,
        profile,
        identity,
        results=scoring.results,
    )
    assert validate_checkpoint(checkpoint, pair, profile, identity) == checkpoint
    with pytest.raises(CampaignArtifactError, match="run_identity"):
        validate_checkpoint(
            checkpoint,
            pair,
            profile,
            identity.model_copy(update={"run_index": 2}),
        )

    report = build_public_report(
        pair,
        profile,
        scoring.results,
        scoring.summary,
    )
    serialized = json.dumps(report.model_dump(mode="json"), sort_keys=True)
    assert "oracle_fingerprint" not in serialized
    assert "oracle_artifact_fingerprint" not in serialized
    assert "evidence" not in serialized
    assert len(report.rows) == len(public.tasks)

    subset_ids = tuple(result.task_id for result in scoring.results[:2])
    subset_results = scoring.results[:2]
    subset_report = build_public_report(
        pair,
        profile,
        subset_results,
        summarize_results(subset_ids, subset_results),
        scheduled_task_ids=subset_ids,
    )
    assert tuple(row.task_id for row in subset_report.rows) == tuple(sorted(subset_ids))

    provenance = build_run_provenance(pair, profile)
    output_dir = tmp_path / "campaign"
    guard = guard_v2_output_directory(output_dir, provenance)
    assert guard_v2_output_directory(output_dir, provenance) == guard

    public_mcp, private_mcp = build_artifacts(
        mcp,
        identity_catalog=snapshot.entities,
    )
    other = build_run_provenance(
        V2ArtifactPair(public=public_mcp, private=private_mcp),
        capability_profile_for_track(Track.MCP),
    )
    with pytest.raises(CampaignArtifactError, match="different"):
        guard_v2_output_directory(output_dir, other)


def test_score_answers_cli_requires_explicit_v2_and_separate_oracles(
    simple_compiled,
    tmp_path,
) -> None:
    _, snapshot, direct, _ = simple_compiled
    public_path = tmp_path / "public.json"
    oracle_path = tmp_path / "private" / "oracles.json"
    public, _ = write_artifacts(
        direct,
        public_path=public_path,
        oracle_path=oracle_path,
        identity_catalog=snapshot.entities,
    )
    answers_path = tmp_path / "answers.json"
    answers_path.write_text(
        json.dumps(
            {"answers": _perfect_answers(direct, snapshot)},
            indent=2,
            sort_keys=True,
        )
    )
    output_path = tmp_path / "scoring.private.json"
    runner = CliRunner()

    implicit = runner.invoke(
        main,
        [
            "score-answers",
            "--manifest",
            str(public_path),
            "--answers",
            str(answers_path),
            "--track",
            "direct",
            "--output",
            str(output_path),
        ],
    )
    assert implicit.exit_code != 0
    assert "explicit --protocol v2" in implicit.output
    assert not output_path.exists()

    explicit = runner.invoke(
        main,
        [
            "score-answers",
            "--protocol",
            "v2",
            "--manifest",
            str(public_path),
            "--oracles",
            str(oracle_path),
            "--answers",
            str(answers_path),
            "--track",
            "direct",
            "--output",
            str(output_path),
        ],
    )
    assert explicit.exit_code == 0, explicit.output
    assert f"Scored {len(public.tasks)}/{len(public.tasks)} v2 samples" in (explicit.output)
