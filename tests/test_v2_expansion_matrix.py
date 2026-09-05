from __future__ import annotations

import hashlib

from ori.cli import _build_manifest
from ori.eval.task_recipes import validate_manifest_recipe_coverage
from ori.eval.tasks import (
    TASK_RECIPE_REGISTRY,
    generate_mcp_tasks,
    generate_tasks,
)
from ori.eval.v2.certification import (
    build_offline_certification_catalog,
    build_projection_parity_cases,
    validate_semantic_equivalence_classes,
)
from ori.eval.v2.compiler import (
    _validate_no_contradictory_oracles,
    compile_legacy_product,
)
from ori.eval.v2.determinism import corpus_contract_shape_fingerprint
from ori.eval.v2.fingerprint import canonical_sha256
from ori.eval.v2.graph import build_archive_snapshot
from ori.eval.v2.profiles import capability_profile_for_track
from ori.eval.v2.schema import Track
from ori.generator.archive_validation import validate_sharphound_zip
from ori.generator.benchmark_profiles import build_benchmark_generation_profile
from ori.generator.phase4 import build_phase4_complex_graph
from ori.generator.serializer import _build_zip

SEED_MATRIX = (
    (4401, "reference"),
    (67, "robustness-only"),
    (4402, "fixed-canary"),
)


def _generate(seed: int):
    profile = build_benchmark_generation_profile("complex", seed=seed)
    graph = build_phase4_complex_graph(
        domain=profile.domain,
        seed=seed,
        users=profile.users,
        workstations=profile.workstations,
        servers=profile.servers,
    )
    archive = _build_zip(graph)
    validate_sharphound_zip(graph, archive).require_valid()
    manifest = _build_manifest(graph, seed, archive=archive)
    manifest["metadata"]["benchmark_name"] = "complex"
    return archive, manifest


def test_three_seed_offline_expansion_matrix_is_deterministic_and_complete() -> None:
    assert dict(SEED_MATRIX)[67] == "robustness-only"
    shape_by_track: dict[Track, str] = {}
    recipe_ids_by_track: dict[Track, tuple[str, ...]] = {}

    for seed, _role in SEED_MATRIX:
        archive, manifest = _generate(seed)
        repeated_archive, repeated_manifest = _generate(seed)
        assert hashlib.sha256(archive).digest() == hashlib.sha256(repeated_archive).digest()
        assert canonical_sha256(manifest) == canonical_sha256(repeated_manifest)

        validate_manifest_recipe_coverage(manifest, TASK_RECIPE_REGISTRY)
        snapshot = build_archive_snapshot(archive, manifest, product="complex")
        for track in (Track.DIRECT, Track.MCP):
            legacy_tasks = (
                generate_tasks(manifest)
                if track is Track.DIRECT
                else generate_mcp_tasks(manifest)
            )
            recipe_ids = tuple(
                task.metadata["logical_recipe_id"] for task in legacy_tasks
            )
            if track in recipe_ids_by_track:
                assert recipe_ids == recipe_ids_by_track[track]
            else:
                recipe_ids_by_track[track] = recipe_ids

            corpus = compile_legacy_product(
                manifest,
                snapshot,
                product="complex",
                track=track,
            )
            shape = corpus_contract_shape_fingerprint(corpus)
            if track in shape_by_track:
                assert shape == shape_by_track[track]
            else:
                shape_by_track[track] = shape

            profile = capability_profile_for_track(track)
            offline = build_offline_certification_catalog(corpus, snapshot, profile)
            assert len(offline.certifications) == len(corpus.tasks)
            _validate_no_contradictory_oracles(corpus.tasks)
            validate_semantic_equivalence_classes(corpus)
            for task, certification in zip(
                corpus.tasks,
                offline.certifications,
                strict=True,
            ):
                projections = build_projection_parity_cases(
                    task,
                    certification,
                    profile,
                    archive_snapshot=snapshot,
                    live_snapshot=snapshot,
                )
                assert tuple(case.name for case in projections) == tuple(
                    case.name for case in certification.fixtures.cases
                )

    assert len(recipe_ids_by_track[Track.DIRECT]) == 42
    assert len(recipe_ids_by_track[Track.MCP]) == 62
