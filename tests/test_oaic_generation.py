"""Offline OAIC graph extension and archive-contract checks."""

from ori.benchmarks import get_benchmark
from ori.cli import _build_manifest
from ori.eval.v2.graph import build_archive_snapshot
from ori.generator.benchmark_profiles import build_benchmark_generation_profile
from ori.generator.oaic import OAIC_NEGATIVE_FIXTURES, build_oaic_graph
from ori.generator.phase4 import build_phase4_complex_graph
from ori.generator.serializer import _build_zip


def test_oaic_generation_preserves_base_and_certifies_near_misses(subtests):
    fingerprints = []
    for seed in (67, 4401, 4402):
        with subtests.test(seed=seed):
            profile = build_benchmark_generation_profile("oaic-2026-v1", seed=seed)
            args = dict(
                domain=profile.domain,
                seed=seed,
                users=profile.users,
                workstations=profile.workstations,
                servers=profile.servers,
            )
            base = build_phase4_complex_graph(**args)
            graph = build_oaic_graph(**args)
            assert {key: graph._nodes[key] for key in base._nodes} == base._nodes
            assert graph.get_edges()[: len(base.get_edges())] == base.get_edges()
            assert graph.planted_paths[: len(base.planted_paths)] == base.planted_paths
            assert graph.rng.getstate() == base.rng.getstate()
            archive = _build_zip(graph)
            assert archive == _build_zip(build_oaic_graph(**args))
            manifest = _build_manifest(graph, seed, archive=archive)
            snapshot = build_archive_snapshot(archive, manifest, product="oaic-2026-v1")
            fingerprints.append(snapshot.graph_fingerprint)
            fixtures = graph.planted_paths[len(base.planted_paths) :]
            assert [p.template_id for p in fixtures] == [x[0] for x in OAIC_NEGATIVE_FIXTURES]
            assert [p.metadata["fixture_role"] for p in fixtures] == ["main"] * 6 + [
                "diagnostic"
            ] * 2
            for path in fixtures:
                adjacency = {}
                for edge in snapshot.relationships:
                    if edge.relationship == path.metadata["relationship"]:
                        adjacency.setdefault(edge.source_id, set()).add(edge.target_id)
                frontier = {path.source_node}
                reached = set()
                for _ in range(path.metadata["max_hops"]):
                    frontier = set().union(*(adjacency.get(node, set()) for node in frontier))
                    reached.update(frontier)
                assert path.target_node == path.metadata["negative_target"]
                assert path.target_node not in reached
                assert path.metadata["near_miss"] in reached
                assert path.metadata["negative_target"] in path.verification_cypher
                assert snapshot.entity(path.metadata["negative_target"])
    assert len(set(fingerprints)) == 3
    product = get_benchmark("oaic-2026-v1")
    assert [track.task_count for track in product.tracks] == [50, 50]
    assert [track.diagnostic_task_count for track in product.tracks] == [10, 10]
