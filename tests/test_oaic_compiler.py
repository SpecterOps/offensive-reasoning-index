from __future__ import annotations

from collections import Counter
from copy import deepcopy

import pytest

from ori.cli import _build_manifest
from ori.eval.v2.certification import build_offline_certification_catalog
from ori.eval.v2.compiler import V2CompileError, compile_legacy_product
from ori.eval.v2.fingerprint import canonical_sha256
from ori.eval.v2.graph import build_archive_snapshot
from ori.eval.v2.oaic_recipes import (
    OAIC_RECIPE_REGISTRY,
    PRODUCT,
    OAICRecipeMetadata,
    build_oaic_recipe_metadata,
    validate_oaic_recipe_metadata,
)
from ori.eval.v2.profiles import capability_profile_for_track
from ori.eval.v2.protocol import build_artifacts
from ori.eval.v2.public_surfaces import public_semantic_fingerprint
from ori.eval.v2.schema import Track
from ori.generator.benchmark_profiles import build_benchmark_generation_profile
from ori.generator.oaic import build_oaic_graph
from ori.generator.serializer import _build_zip


@pytest.fixture(scope="module", params=(67, 4401, 4402))
def oaic_compiled(request):
    seed = request.param
    profile = build_benchmark_generation_profile(PRODUCT, seed=seed)
    graph = build_oaic_graph(
        domain=profile.domain,
        seed=seed,
        users=profile.users,
        workstations=profile.workstations,
        servers=profile.servers,
    )
    archive = _build_zip(graph)
    manifest = _build_manifest(graph, seed, archive=archive)
    manifest["metadata"].update(profile.to_metadata())
    snapshot = build_archive_snapshot(archive, manifest, product=PRODUCT)
    corpora = {
        track: compile_legacy_product(manifest, snapshot, product=PRODUCT, track=track)
        for track in Track
    }
    return manifest, snapshot, corpora


def test_oaic_roster_has_one_hundred_main_contracts_per_track(oaic_compiled):
    _, _, corpora = oaic_compiled
    assert len(OAIC_RECIPE_REGISTRY) == 110
    for track, corpus in corpora.items():
        assert len(corpus.tasks) == 110
        assert len({public_semantic_fingerprint(t.public) for t in corpus.tasks}) == 110
        assert Counter(t.public.claim_kind for t in corpus.tasks) == {
            "set": 34,
            "count": 32,
            "route": 26,
            "decision": 9,
            "absence": 9,
        }
        metadata = build_oaic_recipe_metadata(corpus)
        assert Counter(e.claim_kind for e in metadata.entries if e.eligibility == "main") == {
            "set": 32,
            "count": 30,
            "route": 24,
            "decision": 7,
            "absence": 7,
        }
        assert Counter(e.claim_kind for e in metadata.entries if e.eligibility == "diagnostic") == {
            "set": 2,
            "count": 2,
            "route": 2,
            "decision": 2,
            "absence": 2,
        }
        assert all(e.track is track for e in metadata.entries)
        by_recipe = {t.migration.legacy_task_id: t for t in corpus.tasks}
        window = by_recipe["set.da-administered-computers"]
        full_count = by_recipe["count.da-administered-computers"]
        assert window.oracle.claim.selection.offset == 0
        assert window.oracle.claim.selection.limit == 500
        assert len(window.oracle.expected_entities) == 500
        assert "offset 0, limit 500" in window.public.question
        assert full_count.oracle.claim.selection.limit is None
        assert full_count.oracle.expected_count > 1000
        for recipe_id, task in by_recipe.items():
            if task.public.claim_kind != "set" or task is window:
                continue
            assert task.oracle.claim.selection.limit is None
            assert 0 < len(task.oracle.expected_entities) <= 1000, recipe_id
            paired_count = by_recipe.get(recipe_id.replace("set.", "count.", 1))
            if paired_count is not None:
                assert paired_count.oracle.expected_count == len(task.oracle.expected_entities)
        for task in corpus.tasks:
            if task.public.claim_kind == "decision":
                assert {subject.role for subject in task.oracle.claim.subjects} == {
                    entity.role for entity in task.public.input_entities
                }
                assert {
                    (entity.role, entity.object_id) for entity in task.oracle.resolved_roles
                } == {(entity.role, entity.object_id) for entity in task.public.input_entities}


@pytest.mark.parametrize("track", tuple(Track))
def test_oaic_contracts_pass_existing_offline_certification(oaic_compiled, track):
    _, snapshot, corpora = oaic_compiled
    corpus = corpora[track]
    certification = build_offline_certification_catalog(
        corpus, snapshot, capability_profile_for_track(track)
    )
    assert len(certification.certifications) == 110


def test_oaic_metadata_is_rederived_from_known_public_bindings(oaic_compiled):
    _, snapshot, corpora = oaic_compiled
    for corpus in corpora.values():
        public, _ = build_artifacts(corpus, identity_catalog=snapshot.entities)
        metadata = build_oaic_recipe_metadata(corpus)
        assert metadata.catalog_fingerprint == public.catalog_fingerprint
        assert validate_oaic_recipe_metadata(metadata, public) == metadata
        for field, value in (
            ("eligibility", "diagnostic"),
            ("recipe_id", "unknown"),
            ("variant_id", "unknown"),
            ("template_id", "unknown"),
            ("public_semantic_fingerprint", "0" * 64),
        ):
            payload = metadata.model_dump()
            payload["entries"][0][field] = value
            payload["metadata_fingerprint"] = canonical_sha256(
                payload, exclude_fields=("metadata_fingerprint",)
            )
            altered = OAICRecipeMetadata.model_validate(payload)
            with pytest.raises(V2CompileError, match="known compiled bindings"):
                validate_oaic_recipe_metadata(altered, public)


def test_oaic_compilation_rejects_unknown_or_missing_templates(oaic_compiled):
    manifest, snapshot, _ = oaic_compiled
    changes = ("unknown", "missing")
    if snapshot.seed == 67:
        changes += ("duplicate", "template_version", "fixture_role")
    for change in changes:
        modified = deepcopy(manifest)
        key = "planted_paths" if "planted_paths" in modified else "paths"
        message = "templates mismatch"
        if change == "unknown":
            modified[key][0]["template_id"] = "unknown-template"
        elif change == "missing":
            modified[key].pop()
        elif change == "duplicate":
            modified[key].append(deepcopy(modified[key][0]))
            message = "duplicate planted template"
        else:
            negative = next(
                path
                for path in modified[key]
                if path["template_id"] == "oaic-negative-admin-target"
            )
            negative["metadata"][change] = "unknown"
            message = "negative fixture metadata mismatch"
        with pytest.raises(V2CompileError, match=message):
            compile_legacy_product(modified, snapshot, product=PRODUCT, track=Track.DIRECT)
