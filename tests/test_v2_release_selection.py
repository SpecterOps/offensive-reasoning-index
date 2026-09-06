"""OAIC selection over real contracts; synthetic replay is not live qualification."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import Counter
from types import SimpleNamespace

import pytest
import yaml

from ori.cli import _build_manifest
from ori.eval.v2.campaign_config import ResolvedV2TrackPaths
from ori.eval.v2.campaign_runner import (
    V2CampaignRunError,
    prepare_selected_tracks,
    prepare_track_artifacts,
)
from ori.eval.v2.certification import build_offline_certification_catalog, live_certify_corpus
from ori.eval.v2.cli_support import select_v2_files
from ori.eval.v2.compiler import compile_legacy_product
from ori.eval.v2.fingerprint import canonical_sha256
from ori.eval.v2.graph import LiveGraphVerification, build_archive_snapshot
from ori.eval.v2.oaic_recipes import OAIC_RECIPE_REGISTRY, build_oaic_recipe_metadata
from ori.eval.v2.profiles import capability_profile_for_track
from ori.eval.v2.protocol import build_artifacts, write_artifacts
from ori.eval.v2.release_selection import (
    PRODUCT,
    SelectedReleaseReceipt,
    pair_selected_tracks,
    select_candidate_track,
)
from ori.eval.v2.schema import Track
from ori.generator.benchmark_profiles import build_benchmark_generation_profile
from ori.generator.oaic import build_oaic_graph
from ori.generator.serializer import _build_zip


def _rehash(model, field, **updates):
    payload = model.model_dump(mode="python")
    payload.update(updates)
    payload[field] = canonical_sha256(payload, exclude_fields=(field,))
    return type(model).model_validate(payload)


@pytest.fixture(scope="module")
def pools():
    cache = {}

    def generate(seed):
        if seed in cache:
            return cache[seed]
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
        manifest["metadata"]["benchmark"] = PRODUCT
        snapshot = build_archive_snapshot(archive, manifest, product=PRODUCT)
        payload = dict(
            schema_version="ori-live-graph-verification-v2",
            expected_graph_fingerprint=snapshot.graph_fingerprint,
            observed_graph_fingerprint=snapshot.graph_fingerprint,
            page_size=1000,
            object_queries=1,
            relationship_queries=1,
            object_count=len(snapshot.objects),
            relationship_count=len(snapshot.relationships),
            normalized_artifacts=(),
            verification_fingerprint="0" * 64,
        )
        payload["verification_fingerprint"] = canonical_sha256(
            payload,
            exclude_fields=("verification_fingerprint",),
        )
        verification = LiveGraphVerification.model_validate(payload)
        tracks = {}
        for track in Track:
            corpus = compile_legacy_product(manifest, snapshot, product=PRODUCT, track=track)
            capability = capability_profile_for_track(track)
            offline = build_offline_certification_catalog(corpus, snapshot, capability)
            live = live_certify_corpus(
                corpus,
                offline,
                capability,
                archive_snapshot=snapshot,
                live_snapshot_before=snapshot,
                live_snapshot_after=snapshot,
                verification_before=verification,
                verification_after=verification,
            )
            public, _ = build_artifacts(corpus, identity_catalog=snapshot.entities)
            metadata = build_oaic_recipe_metadata(corpus)
            tracks[track] = SimpleNamespace(
                corpus=corpus,
                public=public,
                metadata=metadata,
                live=live,
                candidates=live.candidate_catalog,
            )
        result = SimpleNamespace(
            manifest=manifest,
            archive=archive,
            snapshot=snapshot,
            tracks=tracks,
            archive_hash=hashlib.sha256(archive).hexdigest(),
            verification=verification,
        )
        cache[seed] = result
        return result

    return generate


def _select(pool, track, **overrides):
    data = pool.tracks[track]
    args = dict(
        public=data.public,
        candidates=data.candidates,
        metadata=data.metadata,
        source_archive_sha256=pool.archive_hash,
    )
    args.update(overrides)
    return select_candidate_track(**args)


def test_registry_and_certified_selection_across_seeds(pools):
    assert len(OAIC_RECIPE_REGISTRY) == 110
    assert Counter(r.eligibility for r in OAIC_RECIPE_REGISTRY) == {"main": 100, "diagnostic": 10}
    assert Counter(r.claim_kind for r in OAIC_RECIPE_REGISTRY if r.eligibility == "main") == {
        "set": 32,
        "count": 30,
        "route": 24,
        "decision": 7,
        "absence": 7,
    }
    assert Counter(r.claim_kind for r in OAIC_RECIPE_REGISTRY if r.eligibility == "diagnostic") == {
        "set": 2,
        "count": 2,
        "route": 2,
        "decision": 2,
        "absence": 2,
    }
    expected = {"set": 10, "count": 8, "route": 20, "decision": 6, "absence": 6}
    selected = {track: [] for track in Track}
    graphs = set()
    for seed in (67, 4401, 4402):
        pool = pools(seed)
        graphs.add(pool.snapshot.graph_fingerprint)
        receipts = {}
        for track in Track:
            data = pool.tracks[track]
            assert len(data.corpus.tasks) == len(data.live.certifications) == 110
            assert all(c.state.value == "candidate" for c in data.live.certifications)
            receipt = receipts[track] = _select(pool, track)
            assert len(receipt.selected_task_ids) == len(set(receipt.selected_task_ids)) == 50
            assert Counter(e.claim_kind for e in receipt.entries) == expected
            diagnostic = {
                e.recipe_id for e in data.metadata.entries if e.eligibility == "diagnostic"
            }
            chosen = frozenset(e.recipe_id for e in receipt.entries)
            assert chosen.isdisjoint(diagnostic)
            selected[track].append(chosen)
            assert _select(pool, track) == receipt
        pair = pair_selected_tracks(receipts[Track.DIRECT], receipts[Track.MCP])
        assert pair.total_tasks == 100
        assert pair.direct_selection_fingerprint == receipts[Track.DIRECT].selection_fingerprint
        assert pair.mcp_selection_fingerprint == receipts[Track.MCP].selection_fingerprint
    assert len(graphs) == 3
    assert all(len(set(values)) == 3 for values in selected.values())


def test_selection_reordering_and_metadata_rejection(pools):
    pool = pools(67)
    for track in Track:
        data = pool.tracks[track]
        tasks = tuple(reversed(data.public.tasks))
        reordered = _rehash(
            data.public,
            "artifact_fingerprint",
            tasks=tasks,
            catalog_fingerprint=canonical_sha256(tasks),
        )
        metadata = _rehash(
            data.metadata,
            "metadata_fingerprint",
            entries=tuple(reversed(data.metadata.entries)),
            catalog_fingerprint=reordered.catalog_fingerprint,
        )
        actual = _select(pool, track, public=reordered, metadata=metadata)
        assert actual.entries == _select(pool, track).entries
        assert actual.candidate_release_fingerprint == data.candidates.release_fingerprint
        assert actual.public_artifact_fingerprint == reordered.artifact_fingerprint
        for field, value in (
            ("recipe_id", "unknown"),
            ("template_id", "unknown"),
            ("eligibility", "diagnostic"),
        ):
            changed = data.metadata.entries[0].model_copy(update={field: value})
            metadata = _rehash(
                data.metadata, "metadata_fingerprint", entries=(changed, *data.metadata.entries[1:])
            )
            with pytest.raises(ValueError):
                _select(pool, track, metadata=metadata)
        deficient = _rehash(
            data.candidates,
            "release_fingerprint",
            entries=data.candidates.entries[1:],
            catalog_fingerprint=canonical_sha256(data.candidates.entries[1:]),
        )
        with pytest.raises(ValueError):
            _select(pool, track, candidates=deficient)


@pytest.mark.parametrize(
    "field,value",
    [("seed", 999), ("source_archive_sha256", "b" * 64), ("graph_fingerprint", "c" * 64)],
)
def test_pair_rejects_individually_rehashed_mismatched_tracks(pools, field, value):
    pool = pools(67)
    direct, mcp = (_select(pool, track) for track in Track)
    changed = _rehash(mcp, "selection_fingerprint", **{field: value})
    with pytest.raises(ValueError, match="mismatch"):
        pair_selected_tracks(direct, changed)


def test_file_selection_admits_complete_pair_before_any_output(pools, tmp_path, monkeypatch):
    pool = pools(67)
    compiled, certified = tmp_path / "compiled", tmp_path / "certified"
    compiled.mkdir()
    certified.mkdir()
    manifest, archive = tmp_path / "manifest.json", tmp_path / "graph.zip"
    manifest.write_text(json.dumps(pool.manifest))
    archive.write_bytes(pool.archive)
    live_paths = []
    for track, data in pool.tracks.items():
        stem = f"{PRODUCT}-{track.value}-seed-67"
        write_artifacts(
            data.corpus,
            public_path=compiled / f"{stem}-public-v2.json",
            oracle_path=compiled / f"{stem}-oracles-v2.private.json",
            identity_catalog=pool.snapshot.entities,
        )
        (compiled / f"{stem}-release-metadata-v1.json").write_text(data.metadata.model_dump_json())
        stem = f"{PRODUCT}-seed-67-{track.value}"
        (certified / f"{stem}-candidates-v2.json").write_text(data.candidates.model_dump_json())
        path = certified / f"{stem}-live-certification-v4.private.json"
        path.write_text(data.live.model_dump_json())
        live_paths.append(path)
    args = dict(
        source_manifest_path=manifest,
        archive_path=archive,
        compiled_dir=compiled,
        certification_dir=certified,
        output_dir=tmp_path / "selected",
    )
    paths = select_v2_files(**args)
    original = {key: path.read_bytes() for key, path in paths.items()}
    assert json.loads(original["selected_release"])["total_tasks"] == 100
    for track in Track:
        assert len(json.loads(original[f"{track.value}_selection"])["entries"]) == 50
    assert {key: path.read_bytes() for key, path in select_v2_files(**args).items()} == original
    saved = live_paths[-1].read_bytes()
    live_paths[-1].unlink()
    with pytest.raises((ValueError, OSError)):
        select_v2_files(**{**args, "output_dir": tmp_path / "missing"})
    assert not (tmp_path / "missing").exists()
    live_paths[-1].write_bytes(live_paths[0].read_bytes())
    with pytest.raises(ValueError):
        select_v2_files(**{**args, "output_dir": tmp_path / "mixed"})
    assert not (tmp_path / "mixed").exists()
    live_paths[-1].write_bytes(saved)
    track_paths = {}
    prepared = {}
    for track in Track:
        stem = f"{PRODUCT}-{track.value}-seed-67"
        live_stem = f"{PRODUCT}-seed-67-{track.value}"
        track_paths[track] = ResolvedV2TrackPaths(
            public=compiled / f"{stem}-public-v2.json",
            oracles=compiled / f"{stem}-oracles-v2.private.json",
            candidates=certified / f"{live_stem}-candidates-v2.json",
            live_certification=certified / f"{live_stem}-live-certification-v4.private.json",
            release_metadata=compiled / f"{stem}-release-metadata-v1.json",
            selection=paths[f"{track.value}_selection"],
        )
        prepared[track] = prepare_track_artifacts(track_paths[track], track, pool.snapshot)
    resolved = SimpleNamespace(
        tracks=track_paths,
        source_manifest=manifest,
        archive=archive,
        selected_release=paths["selected_release"],
    )
    admitted = prepare_selected_tracks(resolved, pool.snapshot, prepared)
    assert all(len(item.selected_task_ids) == 50 for item in admitted.values())
    with pytest.raises(V2CampaignRunError, match="both certified tracks"):
        prepare_selected_tracks(resolved, pool.snapshot, {Track.DIRECT: prepared[Track.DIRECT]})
    path = paths["direct_selection"]
    receipt = SelectedReleaseReceipt.model_validate_json(path.read_text())
    # A valid self-hash is not proof of the prescribed selection order.
    forged = _rehash(receipt, "selection_fingerprint", entries=tuple(reversed(receipt.entries)))
    path.write_text(forged.model_dump_json())
    with pytest.raises(V2CampaignRunError, match="differs from certified selection"):
        prepare_selected_tracks(resolved, pool.snapshot, prepared)
    path.write_bytes(original["direct_selection"])
    # Exercise the public campaign admission path, not only its file helper.
    from ori.eval.v2 import campaign_runner

    config_payload = {
        "version": 2,
        "source": {"manifest": str(manifest), "archive": str(archive)},
        "tracks": {
            track.value: value.model_dump(mode="json") for track, value in track_paths.items()
        },
        "selected_release": str(paths["selected_release"]),
        "modes": ["direct"],
        "output_dir": str(tmp_path / "campaign"),
        "defaults": {"bhce_url": "https://graph.invalid"},
        "models": [
            {
                "name": "offline",
                "provider": "ollama",
                "model": "offline",
                "model_base_url": "http://127.0.0.1:1",
            }
        ],
    }
    config_path = tmp_path / "campaign.yaml"
    config_path.write_text(yaml.safe_dump(config_payload))

    def forbidden(*args, **kwargs):
        raise AssertionError("file-only Direct admission must not launch external services")

    readiness_calls = []
    original_readiness = campaign_runner._model_readiness

    def readiness(config):
        readiness_calls.append(config.config.track_modes)
        return original_readiness(config)

    with monkeypatch.context() as patch:
        patch.setattr(campaign_runner, "_git_revision", forbidden)
        patch.setattr(campaign_runner, "BHCEClient", forbidden)
        patch.setattr(campaign_runner, "_model_readiness", readiness)
        actual_config, _, actual_tracks, revision, _ = campaign_runner.prepare_v2_campaign(
            config_path
        )
        assert set(actual_config.tracks) == {Track.DIRECT, Track.MCP}
        assert set(actual_tracks) == {Track.DIRECT}
        assert actual_tracks[Track.DIRECT].task_ids == receipt.selected_task_ids
        assert revision == "not-applicable"
        assert readiness_calls == [(Track.DIRECT,)]
        live_paths[-1].unlink()
        with pytest.raises(ValueError):
            campaign_runner.prepare_v2_campaign(config_path)
        live_paths[-1].write_bytes(saved)
        assert readiness_calls == [(Track.DIRECT,)]
        del config_payload["selected_release"]
        del config_payload["tracks"]["mcp"]
        for key in ("selection", "release_metadata"):
            del config_payload["tracks"]["direct"][key]
        config_path.write_text(yaml.safe_dump(config_payload))
        with pytest.raises(V2CampaignRunError, match="both certified tracks"):
            campaign_runner.prepare_v2_campaign(config_path)
        assert readiness_calls == [(Track.DIRECT,)]
    # Use the real scheduler/runtime with a deterministic invalid response.
    # There is no model server, graph call or synthetic scoring bypass.
    from ori.eval.adapter import ModelResponse
    from ori.eval.v2.model_runtime import run_direct_model_task_v2

    calls = []

    async def transport(**kwargs):
        return ModelResponse(
            raw_text="{}",
            cypher=None,
            parse_stage="none",
            tokens_input=1,
            tokens_output=1,
            elapsed_seconds=0.01,
            model=kwargs["model"],
        )

    async def run_task(**kwargs):
        calls.append(kwargs["task"].task_id)
        return await run_direct_model_task_v2(**kwargs, transport=transport)

    class NoGraph:
        circuit_open = False

        def __getattr__(self, name):
            raise AssertionError(f"unexpected graph operation: {name}")

    prepared_direct = actual_tracks[Track.DIRECT]
    arguments = dict(
        resolved=actual_config,
        prepared=prepared_direct,
        model=actual_config.config.models[0],
        run_index=1,
        bhce=NoGraph(),
        coordinator=NoGraph(),
        loop=None,
        runs_total=1,
    )
    with monkeypatch.context() as patch:
        patch.setattr(campaign_runner, "run_direct_model_task_v2", run_task)
        provenance, samples = asyncio.run(campaign_runner._run_model(**arguments))
        assert tuple(calls) == receipt.selected_task_ids
        assert len(samples) == 50
        assert all(sample.outcome.value == "OUTPUT_INVALID" for sample in samples)
        calls.clear()
        _, resumed = asyncio.run(campaign_runner._run_model(**arguments))
        assert not calls
        assert resumed == samples
    run_dir = actual_config.output_dir / "direct/offline/run-001"
    report = campaign_runner._model_report(
        provenance=provenance,
        prepared=prepared_direct,
        results=samples,
        run_dir=run_dir,
        before=pool.verification,
        after=pool.verification,
    )
    assert report.report.summary.scheduled == 50
    state = campaign_runner._load_state(
        run_dir / campaign_runner.RUN_STATE_NAME,
        provenance=provenance,
        prepared=prepared_direct,
    )
    assert len(state.checkpoint.task_bindings) == 110
    assert tuple(attempt.task_id for attempt in state.attempts) == receipt.selected_task_ids
    # Source changes invalidate otherwise self-consistent old candidate files.
    from ori.eval.v2 import release_selection

    with monkeypatch.context() as patch:
        patch.setattr(release_selection.compiler, "compiler_fingerprint", lambda: "0" * 64)
        with pytest.raises(ValueError, match="current compiler"):
            _select(pool, Track.DIRECT)
        with pytest.raises(ValueError, match="current compiler"):
            select_v2_files(**{**args, "output_dir": tmp_path / "stale"})
        assert not (tmp_path / "stale").exists()
    with monkeypatch.context() as patch:
        patch.setattr(release_selection, "COMPARATOR_FINGERPRINT", "0" * 64)
        with pytest.raises(ValueError, match="current compiler and comparator"):
            _select(pool, Track.MCP)
