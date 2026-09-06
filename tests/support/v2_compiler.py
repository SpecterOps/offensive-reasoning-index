"""Deterministic product and compiled-corpus fixtures shared by V2 tests."""

from __future__ import annotations

import pytest

from ori.cli import _build_manifest
from ori.eval.v2.compiler import compile_legacy_product
from ori.eval.v2.graph import build_archive_snapshot
from ori.eval.v2.schema import Track
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
