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
from ori.eval.v2.campaign_config import ResolvedV2TrackPaths, load_v2_campaign_config
from ori.eval.v2.campaign_runner import (
    V2CampaignRunError,
    prepare_diagnostic_canary_track,
    prepare_selected_tracks,
    prepare_track_artifacts,
)
from ori.eval.v2.certification import build_offline_certification_catalog, live_certify_corpus
from ori.eval.v2.cli_support import prepare_canary_v2_files, select_v2_files
from ori.eval.v2.compiler import compile_legacy_product
from ori.eval.v2.diagnostic_selection import (
    CANARY_KINDS,
    DiagnosticSelectionReceiptV1,
    select_diagnostic_canary,
)
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
from ori.eval.v2.schema import CertificationState, Track
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


def test_native_feasibility_preserves_selected_cells(pools, monkeypatch, subtests, tmp_path):
    from click.testing import CliRunner

    from ori.cli import main
    from ori.eval.v2 import native_feasibility as feasibility
    from tests.support.v2_mcp import native_profile as _profile

    pool = pools(67)
    selection = _select(pool, Track.MCP)
    manifest_path = tmp_path / "manifest.json"
    archive_path = tmp_path / "archive.zip"
    selection_path = tmp_path / "selection.json"
    profile_path = tmp_path / "profile.private.json"
    manifest_path.write_text(json.dumps(pool.manifest))
    archive_path.write_bytes(pool.archive)
    selection_path.write_text(selection.model_dump_json())
    args = [
        "check-native-feasibility",
        "--manifest",
        str(manifest_path),
        "--archive",
        str(archive_path),
        "--selection",
        str(selection_path),
        "--native-profile",
        str(profile_path),
        "--json",
    ]
    for implementation in ("mwnickerson", "mordavid", "armadin"):
        with subtests.test(implementation=implementation):
            native_profile = _profile(implementation)
            result = feasibility.assess_native_selected_cell(
                manifest=pool.manifest,
                archive=pool.archive,
                selection=selection,
                profile=native_profile,
            )
            assert tuple(t.task_id for t in result.tasks) == selection.selected_task_ids
            assert len(result.tasks) == 50
            if implementation == "mwnickerson":
                assert result.offline_feasible, [
                    (t.task_id, t.reason) for t in result.tasks if t.status != "offline_supported"
                ]
                prepared = result.development
                assert prepared is not None
                assert prepared.task_ids == selection.selected_task_ids
                assert prepared.profile == native_profile
                assert prepared.selection_fingerprint == selection.selection_fingerprint
                assert len(prepared.certifications) == 50
                assert not hasattr(prepared, "release")
                assert not hasattr(prepared, "live")
                for task, oracle, certified in zip(
                    prepared.pair.public.tasks,
                    prepared.pair.private.oracles,
                    prepared.certifications,
                    strict=True,
                ):
                    assert task.task_fingerprint == oracle.task_fingerprint
                    assert task.task_fingerprint == certified.certification.task_fingerprint
                    assert task.binding.capability_profile_id == native_profile.profile_id
                    assert certified.certification.state is CertificationState.OFFLINE_CERTIFIED
                    assert certified.certification.live_proof_fingerprint is None
                from ori.eval.v2.campaign_runner import _run_model

                with pytest.raises(V2CampaignRunError, match="NATIVE_DEVELOPMENT_NOT_QUALIFIED"):
                    asyncio.run(
                        _run_model(
                            resolved=None,
                            prepared=prepared,
                            model=None,
                            run_index=1,
                            bhce=None,
                            coordinator=None,
                            loop=None,
                            runs_total=1,
                        )
                    )
                from dataclasses import replace
                from unittest.mock import AsyncMock

                assert (
                    tuple(task.public.task_id for task in prepared.corpus.tasks)
                    == prepared.task_ids
                )
                validated = feasibility.validate_native_development_track(prepared)
                assert validated.corpus == prepared.corpus
                reordered = prepared.corpus.model_dump(mode="python")
                reordered["tasks"] = tuple(reversed(prepared.corpus.tasks))
                reordered["catalog_fingerprint"] = canonical_sha256(
                    reordered,
                    exclude_fields=("catalog_fingerprint",),
                )
                with pytest.raises(
                    feasibility.NativeFeasibilityInputError,
                    match="compiled corpus",
                ):
                    feasibility.validate_native_development_track(
                        replace(
                            prepared,
                            corpus=type(prepared.corpus).model_validate(reordered),
                        )
                    )

                from ori.eval.v2 import campaign_runner

                dispatch = AsyncMock(return_value=("private-outcome", "private-provider"))
                bridge = object()
                arguments = dict(
                    prepared=prepared,
                    task_id=prepared.task_ids[0],
                    native_bridge=bridge,
                    model="openai-compat/local-fixture",
                    model_base_url=None,
                    max_steps=16,
                )
                with monkeypatch.context() as patch:
                    patch.setattr(campaign_runner, "run_mcp_model_task_v2", dispatch)
                    value = asyncio.run(campaign_runner.run_native_development_task(**arguments))
                    assert value == ("private-outcome", "private-provider")
                    assert dispatch.await_count == 1
                    forwarded = dispatch.call_args.kwargs
                    assert forwarded["certified"] is False
                    assert forwarded["bundle"] is None
                    assert forwarded["native_bridge"] is bridge
                    assert forwarded["oracle"] == prepared.pair.private.oracles[0]
                    assert forwarded["task"] == prepared.pair.public.tasks[0]
                    for bad in (
                        {**arguments, "task_id": "not-selected"},
                        {**arguments, "prepared": replace(prepared, certifications=())},
                        {
                            **arguments,
                            "prepared": replace(
                                prepared,
                                certifications=prepared.certifications[::-1],
                            ),
                        },
                    ):
                        with pytest.raises(feasibility.NativeFeasibilityInputError):
                            asyncio.run(campaign_runner.run_native_development_task(**bad))
                    assert dispatch.await_count == 1
            else:
                assert not result.offline_feasible
                assert result.development is None
                assert any(t.status == "unsupported" for t in result.tasks)
                assert not any(t.status == "harness_error" for t in result.tasks)
            for outcome in result.tasks:
                if outcome.status == "offline_supported":
                    assert outcome.certification.certification.live_proof_fingerprint is None
                else:
                    assert outcome.certification is None

            # Reuse the real expensive assessment above for the CLI projection;
            # still require exact parsed file arguments and bounded operator references.
            def assessed(**kwargs):
                assert canonical_sha256(kwargs["manifest"]) == canonical_sha256(pool.manifest)
                assert kwargs["archive"] == pool.archive
                assert kwargs["selection"] == selection
                assert kwargs["profile"] == native_profile
                return result

            profile_path.write_text(native_profile.model_dump_json())
            with monkeypatch.context() as patch:
                patch.setattr(feasibility, "assess_native_selected_cell", assessed)
                invocation = CliRunner().invoke(main, args)
                export_dir = tmp_path / f"export-{implementation}"
                exported = CliRunner().invoke(main, [*args, "--output-dir", str(export_dir)])
                assert exported.exit_code == (0 if result.offline_feasible else 2), exported.output
                assert json.loads(exported.output)["artifacts_written"] is result.offline_feasible
                if not result.offline_feasible:
                    assert str(export_dir) not in exported.output
                else:
                    references = json.loads(exported.output)["artifacts"]
                    assert references["public"]["path"] == str(export_dir / "native-public-v2.json")
                if result.offline_feasible:
                    from ori.eval.v2.protocol import load_v2_pair

                    exported_public = export_dir / "native-public-v2.json"
                    exported_oracles = export_dir / "native-oracles-v2.private.json"
                    assert (
                        load_v2_pair(exported_public, exported_oracles) == result.development.pair
                    )
                    assert export_dir.stat().st_mode & 0o777 == 0o700
                    assert exported_public.stat().st_mode & 0o777 == 0o600
                    assert exported_oracles.stat().st_mode & 0o777 == 0o600
                    repeated = CliRunner().invoke(main, [*args, "--output-dir", str(export_dir)])
                    assert repeated.exit_code == 1
                    assert "NATIVE_ARTIFACT_EXPORT_FAILED" in repeated.output
                    assert (
                        load_v2_pair(exported_public, exported_oracles) == result.development.pair
                    )
                else:
                    assert not export_dir.exists()
                from ori.eval.v2 import campaign_runner

                public_path = tmp_path / "native-public.json"
                oracles_path = tmp_path / "native-oracles.private.json"
                if result.development is not None:
                    public_path.write_text(result.development.pair.public.model_dump_json())
                    oracles_path.write_text(result.development.pair.private.model_dump_json())
                resolved = SimpleNamespace(
                    source_manifest=manifest_path,
                    archive=archive_path,
                    tracks={
                        Track.MCP: SimpleNamespace(
                            selection=selection_path,
                            public=public_path,
                            oracles=oracles_path,
                        )
                    },
                    native_mcp_paths=SimpleNamespace(
                        capability_profile=profile_path,
                        qualification=None,
                        qualification_work=None,
                    ),
                    config=SimpleNamespace(
                        purpose="official",
                        track_modes=(Track.MCP,),
                        defaults=SimpleNamespace(
                            mcp=SimpleNamespace(
                                implementation_id=implementation,
                                backend=native_profile.backend,
                                runtime_fingerprint=native_profile.runtime_fingerprint,
                                dependency_lock_fingerprint=native_profile.dependency_lock_fingerprint,
                            ),
                        ),
                    ),
                )
                patch.setattr(campaign_runner, "load_v2_campaign_config", lambda _: resolved)
                patch.setattr(
                    "ori.eval.v2.campaign_config.load_v2_native_qualification_config",
                    lambda _: resolved,
                )
                if result.offline_feasible:
                    checked = campaign_runner.prepare_native_campaign_artifacts(
                        resolved,
                        native_profile,
                    )
                    assert checked == result.development
                    qualification = campaign_runner.prepare_native_qualification(
                        tmp_path / "unused-config.yaml",
                    )
                    assert qualification[1] == checked
                    assert qualification[2].graph_fingerprint == checked.corpus.graph_fingerprint
                    assert qualification[3].certifications == checked.certifications
                    assert callable(qualification[4]) and qualification[5] >= 50
                    with pytest.raises(
                        V2CampaignRunError, match="NATIVE_CERTIFICATION_ADMISSION_UNAVAILABLE"
                    ):
                        campaign_runner.prepare_v2_campaign(tmp_path / "unused-config.yaml")
                    public_path.write_text("{}")
                    with pytest.raises(
                        V2CampaignRunError, match="NATIVE_ARTIFACT_BINDING_MISMATCH"
                    ):
                        campaign_runner.prepare_native_campaign_artifacts(resolved, native_profile)
                else:
                    with pytest.raises(
                        V2CampaignRunError, match="NATIVE_SELECTED_CELL_UNSUPPORTED"
                    ):
                        campaign_runner.prepare_native_qualification(
                            tmp_path / "unused-config.yaml",
                        )
                    with pytest.raises(
                        V2CampaignRunError, match="NATIVE_SELECTED_CELL_UNSUPPORTED"
                    ):
                        campaign_runner.prepare_v2_campaign(tmp_path / "unused-config.yaml")
            assert invocation.exit_code == (0 if result.offline_feasible else 2), invocation.output
            summary = json.loads(invocation.output)
            assert summary["selected_tasks"] == 50
            assert summary["campaign_admitted"] is False
            assert summary["provider_calls"] == 0
            assert "certification" not in invocation.output
            assert str(tmp_path) not in invocation.output

    profile = _profile("mwnickerson")
    with pytest.raises(ValueError, match="archive/manifest mismatch"):
        feasibility.assess_native_selected_cell(
            manifest=pool.manifest,
            archive=pool.archive + b"changed",
            selection=selection,
            profile=profile,
        )
    reordered = _rehash(
        selection, "selection_fingerprint", entries=tuple(reversed(selection.entries))
    )
    with pytest.raises(ValueError, match="exact seed-selected"):
        feasibility.assess_native_selected_cell(
            manifest=pool.manifest,
            archive=pool.archive,
            selection=reordered,
            profile=profile,
        )

    def broken(*args, **kwargs):
        raise RuntimeError("synthetic private defect")

    monkeypatch.setattr(feasibility, "offline_certify", broken)
    failed = feasibility.assess_native_selected_cell(
        manifest=pool.manifest,
        archive=pool.archive,
        selection=selection,
        profile=profile,
    )
    assert not failed.offline_feasible
    assert failed.development is None
    assert len(failed.tasks) == 50
    assert all(t.status == "harness_error" for t in failed.tasks)
    assert "synthetic private defect" not in repr(failed)
    with monkeypatch.context() as patch:
        patch.setattr(feasibility, "assess_native_selected_cell", lambda **_: failed)
        invocation = CliRunner().invoke(main, args)
    assert invocation.exit_code == 3
    assert json.loads(invocation.output)["harness_errors"] == 50

    def unexpected(**kwargs):
        raise TypeError("private implementation defect")

    with monkeypatch.context() as patch:
        patch.setattr(feasibility, "assess_native_selected_cell", unexpected)
        invocation = CliRunner().invoke(main, args)
    assert invocation.exit_code == 3
    assert "NATIVE_FEASIBILITY_HARNESS_ERROR" in invocation.output
    assert "private implementation defect" not in invocation.output

    selection_path.write_text('{"private_input": "SECRET-FIXTURE"}')
    invocation = CliRunner().invoke(main, args)
    assert invocation.exit_code == 1
    assert "NATIVE_FEASIBILITY_INPUT_INVALID" in invocation.output
    assert "SECRET-FIXTURE" not in invocation.output and str(tmp_path) not in invocation.output
    missing_args = list(args)
    missing_args[missing_args.index("--selection") + 1] = str(tmp_path / "private-missing.json")
    invocation = CliRunner().invoke(main, missing_args)
    assert invocation.exit_code == 1
    assert str(tmp_path) not in invocation.output and "private-missing" not in invocation.output


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


def test_diagnostic_canary_is_fixed_non_ranking_and_bound_to_paired_release(pools):
    pool = pools(67)
    direct = _select(pool, Track.DIRECT)
    mcp = _select(pool, Track.MCP)
    paired = pair_selected_tracks(direct, mcp)
    data = pool.tracks[Track.MCP]

    receipt = select_diagnostic_canary(
        public=data.public,
        candidates=data.candidates,
        metadata=data.metadata,
        source_archive_sha256=pool.archive_hash,
        paired_release=paired,
        mcp_selection=mcp,
    )

    assert receipt.ranking_eligible is False
    assert receipt.selected_task_ids
    assert len(receipt.selected_task_ids) == len(CANARY_KINDS)
    assert {entry.claim_kind for entry in receipt.entries} == set(CANARY_KINDS)
    assert all(
        entry.eligibility == "diagnostic"
        for entry in data.metadata.entries
        if entry.task_id in receipt.selected_task_ids
    )
    assert receipt == select_diagnostic_canary(
        public=data.public,
        candidates=data.candidates,
        metadata=data.metadata,
        source_archive_sha256=pool.archive_hash,
        paired_release=paired,
        mcp_selection=mcp,
    )
    with pytest.raises(ValueError, match="MCP selected release"):
        select_diagnostic_canary(
            public=data.public,
            candidates=data.candidates,
            metadata=data.metadata,
            source_archive_sha256=pool.archive_hash,
            paired_release=paired,
            mcp_selection=direct,
        )


def test_prepare_canary_writes_isolated_mcp_config_and_rederives_five_tasks(
    pools, tmp_path, monkeypatch,
):
    from tests.support.hermes_acceptance import acceptance_root, run_synthetic_diagnostic

    tmp_path = acceptance_root(tmp_path)
    pool = pools(67)
    archive_path = tmp_path / "source.zip"
    manifest_path = tmp_path / "source-manifest.json"
    archive_path.write_bytes(pool.archive)
    manifest_path.write_text(json.dumps(pool.manifest))
    compiled = tmp_path / "compiled"
    certified = tmp_path / "certified"
    selections = tmp_path / "selections"
    artifacts = {}
    for track in Track:
        data = pool.tracks[track]
        stem = f"{PRODUCT}-{track.value}"
        public = compiled / f"{stem}-public.json"
        oracle = compiled / f"{stem}-oracle.private.json"
        write_artifacts(
            data.corpus,
            public_path=public,
            oracle_path=oracle,
            identity_catalog=pool.snapshot.entities,
        )
        metadata = compiled / f"{stem}-metadata.json"
        metadata.write_text(data.metadata.model_dump_json())
        candidates = certified / f"{stem}-candidates.json"
        candidates.parent.mkdir(parents=True, exist_ok=True)
        candidates.write_text(data.candidates.model_dump_json())
        live = certified / f"{stem}-live.private.json"
        live.write_text(data.live.model_dump_json())
        artifacts[track] = {
            "public": public,
            "oracle": oracle,
            "metadata": metadata,
            "candidates": candidates,
            "live": live,
        }
    direct = _select(pool, Track.DIRECT)
    mcp = _select(pool, Track.MCP)
    pair = pair_selected_tracks(direct, mcp)
    selections.mkdir()
    direct_path = selections / "direct.json"
    mcp_path = selections / "mcp.json"
    paired_path = selections / "paired.json"
    direct_path.write_text(direct.model_dump_json())
    mcp_path.write_text(mcp.model_dump_json())
    paired_path.write_text(pair.model_dump_json())
    mcp_dir = tmp_path / "mcp"
    mcp_dir.mkdir()
    config_path = tmp_path / "official.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "version": 2,
                "protocol": "ori-eval-protocol-v2",
                "source": {"manifest": str(manifest_path), "archive": str(archive_path)},
                "tracks": {
                    track.value: {
                        "public": str(artifacts[track]["public"]),
                        "oracles": str(artifacts[track]["oracle"]),
                        "candidates": str(artifacts[track]["candidates"]),
                        "live_certification": str(artifacts[track]["live"]),
                        "release_metadata": str(artifacts[track]["metadata"]),
                        "selection": str(direct_path if track is Track.DIRECT else mcp_path),
                    }
                    for track in Track
                },
                "modes": ["direct", "mcp"],
                "selected_release": str(paired_path),
                "output_dir": str(tmp_path / "official-campaign"),
                "defaults": {
                    "concurrency": 1,
                    "runs_per_model": 1,
                    "mcp": {
                        "mcp_dir": "mcp",
                        "resource_mode": "off",
                        "tool_loop": "native-openai-compatible",
                    },
                },
                "models": [{"name": "test", "provider": "codex", "model": "test"}],
            },
            sort_keys=True,
        )
    )

    outputs = prepare_canary_v2_files(
        config_path=config_path,
        suite="native-five-kind-v1",
        output_dir=tmp_path / "canary",
    )
    canary = yaml.safe_load(outputs["config"].read_text())
    assert canary["purpose"] == "diagnostic_canary"
    assert canary["modes"] == ["mcp"]
    assert set(canary["tracks"]) == {"mcp"}
    diagnostic = DiagnosticSelectionReceiptV1.model_validate_json(outputs["selection"].read_text())
    assert len(diagnostic.entries) == 5

    resolved = load_v2_campaign_config(outputs["config"])
    assert resolved.mcp_dir == mcp_dir
    assert canary["defaults"]["mcp"]["mcp_dir"] == str(mcp_dir)
    snapshot = build_archive_snapshot(pool.archive, pool.manifest, product=PRODUCT)
    prepared = {Track.MCP: prepare_track_artifacts(resolved.tracks[Track.MCP], Track.MCP, snapshot)}
    selected = prepare_diagnostic_canary_track(resolved, snapshot, prepared)
    assert selected[Track.MCP].task_ids == diagnostic.selected_task_ids

    from ori.eval.v2.native_setup import inspect_artifact_index

    index = inspect_artifact_index(config_path)
    assert index["mcp_tasks"] == 50 and index["provider_calls"] == 0
    assert index["mcp_kind_counts"] == {
        "set": 10,
        "count": 8,
        "route": 20,
        "decision": 6,
        "absence": 6,
    }
    assert set(index["artifacts"]) >= {"config", "direct.public", "mcp.public", "selected_release"}

    # This crosses the real native compiler/projector/fixture boundary over the
    # exact five source recipes. No native transport or provider is contacted.
    from ori.eval.v2.native_diagnostic import compile_native_diagnostic
    from ori.eval.v2.native_feasibility import validate_native_development_track
    from tests.support.v2_mcp import native_profile

    native_resolved = SimpleNamespace(
        native_mcp_paths=SimpleNamespace(original_config=config_path),
        selected_release=resolved.selected_release,
        source_manifest=resolved.source_manifest,
        archive=resolved.archive,
        canary_selection=resolved.canary_selection,
    )
    native = compile_native_diagnostic(native_resolved, native_profile("mwnickerson"))
    assert len(native.task_ids) == 5
    assert native.task_ids == diagnostic.selected_task_ids
    assert all(task.binding.mcp_resource_mode == "native" for task in native.pair.public.tasks)
    assert native.task_ids != mcp.selected_task_ids
    assert validate_native_development_track(native) == native

    # Native config preparation uses the actual complete source-index producer,
    # byte fingerprinting and 50-contract feasibility, not canned path documents.
    import sys

    from ori.eval.v2.campaign_config import load_v2_native_qualification_config
    from ori.eval.v2.native_capability import NativeCapabilityProfile
    from ori.eval.v2.native_setup import prepare_native_config
    from ori.native_runtime import fingerprint_native_runtime_roots

    runtime = tmp_path / "external-runtime"
    runtime.mkdir()
    (runtime / "fixture.py").write_text("fixture = True\n")
    lock = tmp_path / "native-lock.txt"
    lock.write_text("fixture==1.0\n")
    runtime_fp = fingerprint_native_runtime_roots((runtime,))
    native_payload = native.profile.model_dump(
        mode="python", exclude={"profile_id", "profile_fingerprint"}
    )
    native_payload.update(
        runtime_fingerprint=runtime_fp,
        dependency_lock_fingerprint=hashlib.sha256(lock.read_bytes()).hexdigest(),
    )
    profile_fp = canonical_sha256(native_payload)
    profile = NativeCapabilityProfile.model_validate(
        {
            **native_payload,
            "profile_id": f"ori-native-mwnickerson-v1-{profile_fp}",
            "profile_fingerprint": profile_fp,
        }
    )
    profile_path = tmp_path / "native-profile.private.json"
    profile_path.write_text(profile.model_dump_json())
    spec_path = tmp_path / "native-runtime.private.json"
    spec_path.write_text(
        json.dumps(
            {
                "schema_version": "hermes-ori-native-runtime-spec-v1",
                "implementation_id": "mwnickerson",
                "source_revision": profile.source_revision,
                "mcp_dir": str(mcp_dir),
                "python_executable": sys.executable,
                "runtime_roots": [str(runtime)],
                "dependency_lock": str(lock),
                "expected_runtime_hash": runtime_fp,
                "backend": "bhce",
                "databases": [],
                "credential_environment_names": ["BLOODHOUND_TOKEN_KEY"],
                "backend_connection_reference": "private-backend.json",
            }
        )
    )
    native_setup = prepare_native_config(
        config_path=config_path,
        native_profile=profile_path,
        runtime_spec=spec_path,
        output_dir=tmp_path / "native-setup",
        model="local-fixture",
        model_name="fixture",
        model_base_url="http://127.0.0.1:8999/v1",
    )
    from pathlib import Path

    native_config = load_v2_native_qualification_config(
        Path(native_setup["artifacts"]["config"]["path"])
    )
    assert native_config.config.modes == ["mcp"]
    assert set(native_config.tracks) == {Track.DIRECT, Track.MCP}
    assert native_config.config.models[0].provider == "openai-compat"
    assert native_config.native_mcp_paths.original_config == config_path
    assert native_config.native_mcp_paths.qualification.parent == Path(
        native_setup["qualification_output_dir"]
    )
    assert not native_config.native_mcp_paths.qualification.exists()
    assert native_setup["provider_calls"] == 0
    run_synthetic_diagnostic(outputs["config"], snapshot, pool.verification, monkeypatch)
    altered = _rehash(
        diagnostic, "selection_fingerprint", entries=tuple(reversed(diagnostic.entries))
    )
    outputs["selection"].write_text(altered.model_dump_json())
    with pytest.raises(ValueError, match="NATIVE_DIAGNOSTIC_SELECTION_MISMATCH"):
        compile_native_diagnostic(native_resolved, native.profile)
    outputs["selection"].write_text(diagnostic.model_dump_json())


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


def _assert_native_selected_pair_binding(resolved, pool, prepared, original, monkeypatch):
    from dataclasses import dataclass

    from ori.eval.v2.campaign_runner import prepare_native_selected_tracks
    from ori.eval.v2.release_selection import PairedSelectedRelease

    direct_selection = SelectedReleaseReceipt.model_validate_json(original["direct_selection"])
    native_selection = SelectedReleaseReceipt.model_validate_json(original["mcp_selection"])
    stored_pair = PairedSelectedRelease.model_validate_json(original["selected_release"])

    # Only qualification ownership is substituted. Real source artifacts,
    # certified Direct selection, paired receipts and file comparisons execute.
    # Native raw-response qualification replay has its own integration coverage
    # in test_native_qualification.py; this fixture does not claim to prove it.
    @dataclass(frozen=True)
    class QualifiedFixture:
        prepared: object
        pair: object
        release: object
        live: object
        paired_release: object = None

        def __post_init__(self):
            if len(self.prepared.selection.selected_task_ids) != 50:
                raise ValueError("fixture native roster must contain 50 tasks")
            if self.paired_release is not None and (
                self.paired_release.total_tasks != 100
                or self.paired_release.mcp_selection_fingerprint
                != self.prepared.selection.selection_fingerprint
            ):
                raise ValueError("fixture native paired roster mismatch")

    native = QualifiedFixture(
        SimpleNamespace(selection=native_selection),
        prepared[Track.MCP].pair,
        prepared[Track.MCP].release,
        prepared[Track.MCP].live,
    )
    with monkeypatch.context() as patch:
        patch.setattr("ori.eval.v2.native_qualification.NativeQualifiedArtifacts", QualifiedFixture)
        result = prepare_native_selected_tracks(
            resolved, pool.snapshot, prepared[Track.DIRECT], native
        )
        assert result[Track.DIRECT].task_ids == direct_selection.selected_task_ids
        assert result[Track.MCP].prepared.selection.selected_task_ids == (
            native_selection.selected_task_ids
        )
        assert result[Track.DIRECT].paired_release_fingerprint == stored_pair.release_fingerprint
        assert result[Track.MCP].paired_release == stored_pair
        assert result[Track.DIRECT].selection_fingerprint == direct_selection.selection_fingerprint
        assert native.paired_release is None
        for changed in (
            {"selected_release": None},
            {"tracks": {Track.MCP: resolved.tracks[Track.MCP]}},
        ):
            invalid = SimpleNamespace(**{**vars(resolved), **changed})
            with pytest.raises(V2CampaignRunError, match="NATIVE_PAIRED_RELEASE_REQUIRED"):
                prepare_native_selected_tracks(
                    invalid,
                    pool.snapshot,
                    prepared[Track.DIRECT],
                    native,
                )
        forged_direct = _rehash(
            direct_selection,
            "selection_fingerprint",
            entries=tuple(reversed(direct_selection.entries)),
        )
        forged_native = _rehash(
            native_selection,
            "selection_fingerprint",
            entries=tuple(reversed(native_selection.entries)),
        )
        mutations = (
            (resolved.tracks[Track.DIRECT].selection, forged_direct, original["direct_selection"]),
            (resolved.tracks[Track.MCP].selection, forged_native, original["mcp_selection"]),
            (
                resolved.selected_release,
                pair_selected_tracks(forged_direct, native_selection),
                original["selected_release"],
            ),
        )
        for path, altered, saved in mutations:
            try:
                path.write_text(altered.model_dump_json())
                with pytest.raises(V2CampaignRunError, match="NATIVE_PAIRED_RELEASE_MISMATCH"):
                    prepare_native_selected_tracks(
                        resolved, pool.snapshot, prepared[Track.DIRECT], native
                    )
            finally:
                path.write_bytes(saved)


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
    _assert_native_selected_pair_binding(resolved, pool, prepared, original, monkeypatch)
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

        def require_confirmed_native_completion(self) -> None:
            # No native calls exist in this isolated Direct selection test.
            return None

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
