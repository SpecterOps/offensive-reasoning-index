"""File-oriented helpers for explicit protocol-v2 CLI commands."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import yaml

from ori.eval.bhce import BHCEClient, parse_bhce_url

from .certification import (
    build_offline_certification_catalog,
    live_certify_corpus,
)
from .compiler import (
    build_migration_inventory,
    compile_legacy_product,
)
from .graph import (
    build_archive_snapshot,
    collect_live_snapshot,
    require_live_graph_match,
)
from .profiles import capability_profile_for_track
from .protocol import load_v2_pair, write_artifacts
from .schema import Track
from .scoring import (
    AnswersV2Artifact,
    ScoringV2Artifact,
    build_answers_artifact,
    score_answers_v2,
)


async def run_native_qualification_owned(*, pending_status, **kwargs):
    """Report pending cleanup before runner shutdown, retaining worker ownership.

    Status emission is bounded by the native cleanup deadline. Process exit is
    deliberately deferred until cleanup is terminal; abandoning the event loop
    cannot establish process cleanup. Pending cleanup is always a failed run.
    """
    from .native_mcp_runtime import NativeSessionCleanupPending

    try:
        return await qualify_native_mcp_files(**kwargs)
    except NativeSessionCleanupPending as pending:
        try:
            pending_status(
                "NATIVE_SESSION_CLEANUP_PENDING: waiting for owned cleanup; no publication",
            )
        except Exception:
            # A closed diagnostic stream must not bypass ownership or replace
            # the original qualification failure after the worker is drained.
            pass
        finally:
            # Even failed status output cannot release ownership. Repeated
            # cancellation does not turn pending cleanup into detached work.
            await pending.wait_for_cleanup()
        raise ValueError("NATIVE_SESSION_CLEANUP_PENDING") from None


async def qualify_native_mcp_files(*, config_path: Path, output_dir: Path | None, execute: bool):
    """Offline by default; explicit execution qualifies native services, never models."""
    import os

    from ori.mcp_launcher import NativeMCPLauncherConfig

    from .campaign_runner import (
        _atomic_write,
        _exclusive_output_dir_lock,
        prepare_native_qualification,
    )
    from .certification import live_certify_native_corpus
    from .native_bolt_runtime import qualify_native_bolt_corpus
    from .native_ce_runtime import native_ce_connection, qualify_native_ce_corpus

    resolved, prepared, snapshot, offline, guard, budget = prepare_native_qualification(config_path)
    profile = prepared.profile
    summary = {
        "schema_version": "ori-native-qualification-v1",
        "artifacts": {},
        "implementation": profile.implementation_id,
        "selected_tasks": len(prepared.task_ids),
        "qualification_call_budget": budget,
        "provider_calls": 0,
        "campaign_admitted": False,
        "status": "offline_prepared",
    }
    if not execute:
        return summary
    if output_dir is None:
        raise ValueError("NATIVE_QUALIFICATION_OUTPUT_REQUIRED")
    paths = resolved.native_mcp_paths
    config = resolved.config.defaults.mcp
    # A config prepared for one future immutable receipt root must not qualify
    # another directory and leave a misleading runnable config behind.
    declared = (
        (paths.qualification, "native-qualification-v2.private.json"),
        (paths.qualification_work, "native-work-v2.private.json"),
        (resolved.tracks[Track.MCP].candidates, "native-candidates-v2.private.json"),
        (
            resolved.tracks[Track.MCP].live_certification,
            "native-live-certification-v4.private.json",
        ),
    )
    if any(path is not None for path, _ in declared) and any(
        path is None or path.resolve() != (output_dir / filename).resolve()
        for path, filename in declared
    ):
        raise ValueError("NATIVE_QUALIFICATION_OUTPUT_BINDING_MISMATCH")
    launcher = NativeMCPLauncherConfig(
        implementation_id=profile.implementation_id,
        checkout=resolved.mcp_dir,
        python_executable=paths.python_executable,
        runtime_roots=paths.runtime_roots,
        runtime_fingerprint=profile.runtime_fingerprint,
        dependency_lock=paths.dependency_lock,
        dependency_lock_fingerprint=profile.dependency_lock_fingerprint,
    )
    if profile.backend == "bhce":
        connection = {
            f"BLOODHOUND_{suffix}": os.environ.get(f"BLOODHOUND_{suffix}", "")
            for suffix in ("DOMAIN", "TOKEN_ID", "TOKEN_KEY")
        }
        connection.update(
            {
                f"BLOODHOUND_{suffix}": os.environ[f"BLOODHOUND_{suffix}"]
                for suffix in ("SCHEME", "PORT", "VERIFY_TLS")
                if f"BLOODHOUND_{suffix}" in os.environ
            }
        )
        connection, client_options, _ = native_ce_connection(connection)
        from ori.eval.bhce import resolve_bhce_target

        if resolve_bhce_target(resolved.config.defaults.bhce_url) != {
            key: client_options[key] for key in ("scheme", "domain", "port")
        }:
            raise ValueError("NATIVE_CE_TARGET_MISMATCH")
    else:
        prefix = "BLOODHOUND" if profile.implementation_id == "mordavid" else "NEO4J"
        connection = {
            f"{prefix}_{suffix}": os.environ.get(f"{prefix}_{suffix}", "")
            for suffix in ("URI", "USERNAME", "PASSWORD")
        }
    if any(not value.strip() for value in connection.values()):
        raise ValueError("NATIVE_CONNECTION_ENVIRONMENT_INVALID")
    # Reserve a fresh private destination before contacting any service.
    # Failed/interrupted directories remain diagnostic-only and are never reused.
    output_dir.mkdir(parents=True, mode=0o700)
    if output_dir.stat().st_mode & 0o077 or output_dir.stat().st_uid != os.getuid():
        raise ValueError("NATIVE_QUALIFICATION_OUTPUT_PERMISSIONS")
    with _exclusive_output_dir_lock(output_dir):
        descriptor = os.open(
            output_dir / "native-stderr.private.log",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as private_log:
            options = dict(
                corpus=prepared.corpus,
                offline=offline,
                config=launcher,
                profile=profile,
                connection=connection,
                private_stderr=private_log,
                expected=snapshot,
            )
            if profile.backend == "bhce":
                work, qualification = await qualify_native_ce_corpus(**options)
            else:
                work, qualification = await qualify_native_bolt_corpus(
                    **options,
                    guard=guard,
                    databases=config.databases,
                )
        # The completed interval verifies exact equality with this archive
        # snapshot at every destination before and after native work.
        live = live_certify_native_corpus(
            prepared.corpus,
            offline,
            profile,
            archive_snapshot=snapshot,
            live_snapshot_before=snapshot,
            live_snapshot_after=snapshot,
            qualification=qualification,
            work_result=work,
        )
        for name, payload in (
            ("native-qualification-v2.private.json", qualification),
            ("native-work-v2.private.json", work),
            ("native-offline-certification-v4.private.json", offline.model_dump(mode="json")),
            ("native-candidates-v2.private.json", live.candidate_catalog.model_dump(mode="json")),
            ("native-live-certification-v4.private.json", live.model_dump(mode="json")),
        ):
            _atomic_write(output_dir / name, payload)
    from .native_setup import file_reference

    return {
        **summary,
        "status": "native_candidate_certified",
        "artifacts": {
            label: file_reference(output_dir / filename)
            for label, filename in (
                ("qualification", "native-qualification-v2.private.json"),
                ("work", "native-work-v2.private.json"),
                ("offline_certification", "native-offline-certification-v4.private.json"),
                ("candidates", "native-candidates-v2.private.json"),
                ("live_certification", "native-live-certification-v4.private.json"),
            )
        },
    }


def _write_model(path: Path, model: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(model.model_dump(mode="json"), indent=2, sort_keys=True) + "\n")


def _atomic_write_text(path: Path, value: str) -> None:
    """Persist a private YAML record without a partial-file success state."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def compile_v2_files(
    *,
    source_manifest_path: Path,
    archive_path: Path,
    product: str,
    track: Track,
    output_dir: Path,
) -> dict[str, Path]:
    """Compile and offline-certify one current product/track without a model."""

    manifest = json.loads(source_manifest_path.read_text())
    archive = archive_path.read_bytes()
    snapshot = build_archive_snapshot(archive, manifest, product=product)
    corpus = compile_legacy_product(
        manifest,
        snapshot,
        product=product,
        track=track,
    )
    profile = capability_profile_for_track(track)
    inventory = build_migration_inventory(corpus)
    offline = build_offline_certification_catalog(
        corpus,
        snapshot,
        profile,
    )

    stem = f"{product}-{track.value}-seed-{corpus.seed}"
    public_path = output_dir / f"{stem}-public-v2.json"
    oracle_path = output_dir / f"{stem}-oracles-v2.private.json"
    inventory_path = output_dir / f"{stem}-inventory-v2.json"
    certification_path = output_dir / f"{stem}-offline-certification-v4.private.json"
    write_artifacts(
        corpus,
        public_path=public_path,
        oracle_path=oracle_path,
        identity_catalog=snapshot.entities,
    )
    _write_model(inventory_path, inventory)
    _write_model(certification_path, offline)
    paths = {
        "public": public_path,
        "oracles": oracle_path,
        "inventory": inventory_path,
        "offline_certification": certification_path,
    }
    if product == "oaic-2026-v1":
        from .oaic_recipes import build_oaic_recipe_metadata

        paths["release_metadata"] = output_dir / f"{stem}-release-metadata-v1.json"
        _write_model(paths["release_metadata"], build_oaic_recipe_metadata(corpus))
    return paths


def select_v2_files(
    *,
    source_manifest_path: Path,
    archive_path: Path,
    compiled_dir: Path,
    certification_dir: Path,
    output_dir: Path,
) -> dict[str, Path]:
    """Admit both complete certified pools and publish one deterministic release.

    This is a file-only operation: existing live certification is required, but
    no provider, MCP server, or graph endpoint is contacted.
    """
    from .campaign_config import ResolvedV2TrackPaths
    from .campaign_runner import _atomic_write, prepare_track_artifacts
    from .fingerprint import canonical_sha256
    from .oaic_recipes import OAICRecipeMetadata
    from .release_selection import PRODUCT, pair_selected_tracks, select_candidate_track

    manifest = json.loads(source_manifest_path.read_text())
    archive = archive_path.read_bytes()
    snapshot = build_archive_snapshot(archive, manifest, product=PRODUCT)
    if manifest.get("metadata", {}).get("benchmark") != PRODUCT:
        raise ValueError("selection requires an explicit OAIC source manifest")
    source_fingerprint = canonical_sha256(manifest)
    archive_sha256 = hashlib.sha256(archive).hexdigest()
    selections = {}
    for track in (Track.DIRECT, Track.MCP):
        stem = f"{PRODUCT}-{track.value}-seed-{snapshot.seed}"
        live_stem = f"{PRODUCT}-seed-{snapshot.seed}-{track.value}"
        paths = ResolvedV2TrackPaths(
            public=compiled_dir / f"{stem}-public-v2.json",
            oracles=compiled_dir / f"{stem}-oracles-v2.private.json",
            candidates=certification_dir / f"{live_stem}-candidates-v2.json",
            live_certification=(
                certification_dir / f"{live_stem}-live-certification-v4.private.json"
            ),
        )
        prepared = prepare_track_artifacts(paths, track, snapshot)
        public = prepared.pair.public
        if public.seed != snapshot.seed or public.source_manifest_fingerprint != source_fingerprint:
            raise ValueError("selection source manifest or seed mismatch")
        metadata = OAICRecipeMetadata.model_validate_json(
            (compiled_dir / f"{stem}-release-metadata-v1.json").read_text()
        )
        selections[track] = select_candidate_track(
            public=public,
            candidates=prepared.release,
            metadata=metadata,
            source_archive_sha256=archive_sha256,
        )
    paired = pair_selected_tracks(selections[Track.DIRECT], selections[Track.MCP])
    stem = f"{PRODUCT}-seed-{snapshot.seed}"
    models = {
        "direct_selection": selections[Track.DIRECT],
        "mcp_selection": selections[Track.MCP],
        "selected_release": paired,
    }
    paths = {name: output_dir / f"{stem}-{name.replace('_', '-')}-v1.json" for name in models}
    # Check every output before replacing any file. A release directory cannot
    # silently change identity; exact reruns are safe and reproducible.
    for name, path in paths.items():
        if path.exists() and json.loads(path.read_text()) != models[name].model_dump(mode="json"):
            raise ValueError("selection output already contains a different release")
    for name, model in models.items():
        _atomic_write(paths[name], model.model_dump(mode="json"))
    return paths


def prepare_canary_v2_files(
    *,
    config_path: Path,
    suite: str,
    output_dir: Path,
) -> dict[str, Path]:
    """Derive a separate MCP-only diagnostic config from an official OAIC config.

    This is an offline operation.  It revalidates the complete official
    paired-release binding, then selects one *diagnostic-only* source contract
    for each public claim kind.  The resulting configuration has a new output
    root and an explicit campaign purpose, so it cannot resume an official
    campaign or contribute to its denominator.
    """

    from .campaign_config import load_v2_campaign_config
    from .campaign_runner import _atomic_write, prepare_selected_tracks, prepare_track_artifacts
    from .diagnostic_selection import CANARY_SUITE, select_diagnostic_canary
    from .graph import build_archive_snapshot
    from .oaic_recipes import OAICRecipeMetadata
    from .release_selection import PairedSelectedRelease, SelectedReleaseReceipt

    if suite != CANARY_SUITE:
        raise ValueError(f"unsupported diagnostic canary suite: {suite}")
    resolved = load_v2_campaign_config(config_path)
    if resolved.native_mcp_paths is not None:
        from .native_diagnostic import prepare_native_canary_files

        return prepare_native_canary_files(
            config_path=config_path, suite=suite, output_dir=output_dir
        )
    if resolved.config.purpose != "official" or resolved.selected_release is None:
        raise ValueError("canary preparation requires an official paired-release configuration")
    if set(resolved.tracks) != {Track.DIRECT, Track.MCP}:
        raise ValueError("canary preparation requires both official certified tracks")
    manifest = json.loads(resolved.source_manifest.read_text())
    snapshot = build_archive_snapshot(resolved.archive, manifest, product="oaic-2026-v1")
    prepared = {
        track: prepare_track_artifacts(resolved.tracks[track], track, snapshot)
        for track in resolved.tracks
    }
    prepared = prepare_selected_tracks(resolved, snapshot, prepared)
    mcp_paths = resolved.tracks[Track.MCP]
    if mcp_paths.release_metadata is None or mcp_paths.selection is None:
        raise ValueError("official configuration is missing MCP selection artifacts")
    receipt = select_diagnostic_canary(
        public=prepared[Track.MCP].pair.public,
        candidates=prepared[Track.MCP].release,
        metadata=OAICRecipeMetadata.model_validate_json(mcp_paths.release_metadata.read_text()),
        source_archive_sha256=hashlib.sha256(resolved.archive.read_bytes()).hexdigest(),
        paired_release=PairedSelectedRelease.model_validate_json(
            resolved.selected_release.read_text()
        ),
        mcp_selection=SelectedReleaseReceipt.model_validate_json(mcp_paths.selection.read_text()),
    )
    output_dir = output_dir.resolve()
    selection_path = output_dir / "diagnostic-selection-v1.json"
    config_output_path = output_dir / "canary-config-v2.yaml"
    selection_payload = receipt.model_dump(mode="json")
    # Resolve every retained artifact to an absolute path.  This config is a
    # private operator record, and portability must not depend on the caller's
    # current directory or an arbitrary sibling layout.
    from .native_setup import absolute_config_document

    config_document = absolute_config_document(resolved)
    config_document.update(
        {
            "purpose": "diagnostic_canary",
            "modes": ["mcp"],
            "tracks": {
                "mcp": {
                    "public": str(mcp_paths.public),
                    "oracles": str(mcp_paths.oracles),
                    "candidates": str(mcp_paths.candidates),
                    "live_certification": str(mcp_paths.live_certification),
                    "release_metadata": str(mcp_paths.release_metadata),
                    "selection": str(mcp_paths.selection),
                }
            },
            "source": {
                "manifest": str(resolved.source_manifest),
                "archive": str(resolved.archive),
            },
            "selected_release": str(resolved.selected_release),
            "canary_selection": str(selection_path),
            "output_dir": str(output_dir / "campaign"),
        }
    )
    rendered = yaml.safe_dump(config_document, sort_keys=True)
    # Verify the emitted document before making it durable.  A typed validation
    # failure is a bug or incompatible input, not a partial canary artifact.
    from .campaign_config import V2CampaignConfig

    V2CampaignConfig.model_validate(yaml.safe_load(rendered))
    if config_output_path.exists() and config_output_path.read_text() != rendered:
        raise ValueError("canary output already contains a different campaign config")
    if selection_path.exists() and json.loads(selection_path.read_text()) != selection_payload:
        raise ValueError("canary output already contains a different diagnostic selection")
    _atomic_write(selection_path, selection_payload)
    _atomic_write_text(config_output_path, rendered)
    return {
        "selection": selection_path,
        "config": config_output_path,
    }


async def ensure_native_canary_qualification(config_path: Path) -> None:
    """Qualify missing diagnostics on their configured fresh owned output surface.

    This is a controlled-service gate, never inference. Partial evidence is not
    reused or repaired. Existing qualification is replayed by normal readiness.
    """
    from .campaign_config import load_v2_native_qualification_config

    # Ordinary historical canaries have no native declaration and are handled
    # by their existing certified-readiness branch.
    document = yaml.safe_load(config_path.read_text())
    if not isinstance(document, dict):
        raise ValueError("DIAGNOSTIC_CONFIG_MAPPING_REQUIRED")
    native = document.get("defaults", {}).get("mcp", {})
    if not isinstance(native, dict) or native.get("mode") != "native":
        return
    resolved = load_v2_native_qualification_config(config_path)
    if resolved.config.purpose != "diagnostic_canary":
        raise ValueError("NATIVE_DIAGNOSTIC_PURPOSE_REQUIRED")
    paths = resolved.native_mcp_paths
    evidence = (
        paths.qualification,
        paths.qualification_work,
        resolved.tracks[Track.MCP].candidates,
        resolved.tracks[Track.MCP].live_certification,
    )
    if any(path is None for path in evidence):
        raise ValueError("NATIVE_DIAGNOSTIC_QUALIFICATION_OUTPUT_REQUIRED")
    if all(path.is_file() for path in evidence):
        return
    if any(path.exists() for path in evidence) or len({path.parent for path in evidence}) != 1:
        raise ValueError("NATIVE_DIAGNOSTIC_QUALIFICATION_PARTIAL")
    await run_native_qualification_owned(
        config_path=config_path,
        output_dir=paths.qualification.parent,
        execute=True,
        pending_status=lambda _message: None,
    )


async def certify_v2_live_files(
    *,
    source_manifest_path: Path,
    archive_path: Path,
    product: str,
    output_dir: Path,
    bhce_url: str | None = None,
    page_size: int = 1000,
) -> dict[str, Path]:
    """Live-certify both tracks with three exact graph identity gates."""

    manifest = json.loads(source_manifest_path.read_text())
    snapshot = build_archive_snapshot(
        archive_path,
        manifest,
        product=product,
    )
    corpora = {
        track: compile_legacy_product(
            manifest,
            snapshot,
            product=product,
            track=track,
        )
        for track in (Track.DIRECT, Track.MCP)
    }
    profiles = {track: capability_profile_for_track(track) for track in (Track.DIRECT, Track.MCP)}
    offline = {
        track: build_offline_certification_catalog(
            corpora[track],
            snapshot,
            profiles[track],
        )
        for track in (Track.DIRECT, Track.MCP)
    }

    async with BHCEClient(**parse_bhce_url(bhce_url)) as bhce:
        health = await bhce.check_health()
        if not health.ok:
            raise ValueError(
                f"BloodHound health check failed before v2 certification: {health.detail}"
            )
        live_pre, receipt_pre = await collect_live_snapshot(
            bhce,
            snapshot,
            page_size=page_size,
        )
        require_live_graph_match(snapshot, live_pre)
        live_middle, receipt_middle = await collect_live_snapshot(
            bhce,
            snapshot,
            page_size=page_size,
        )
        require_live_graph_match(snapshot, live_middle)
        direct = live_certify_corpus(
            corpora[Track.DIRECT],
            offline[Track.DIRECT],
            profiles[Track.DIRECT],
            archive_snapshot=snapshot,
            live_snapshot_before=live_pre,
            live_snapshot_after=live_middle,
            verification_before=receipt_pre,
            verification_after=receipt_middle,
        )
        live_post, receipt_post = await collect_live_snapshot(
            bhce,
            snapshot,
            page_size=page_size,
        )
        require_live_graph_match(snapshot, live_post)
        mcp = live_certify_corpus(
            corpora[Track.MCP],
            offline[Track.MCP],
            profiles[Track.MCP],
            archive_snapshot=snapshot,
            live_snapshot_before=live_middle,
            live_snapshot_after=live_post,
            verification_before=receipt_middle,
            verification_after=receipt_post,
        )

    stem = f"{product}-seed-{snapshot.seed}"
    paths = {
        "verification_pre": output_dir / f"{stem}-live-pre.private.json",
        "verification_middle": output_dir / f"{stem}-live-middle.private.json",
        "verification_post": output_dir / f"{stem}-live-post.private.json",
        "direct_certification": (output_dir / f"{stem}-direct-live-certification-v4.private.json"),
        "mcp_certification": (output_dir / f"{stem}-mcp-live-certification-v4.private.json"),
        "direct_candidates": output_dir / f"{stem}-direct-candidates-v2.json",
        "mcp_candidates": output_dir / f"{stem}-mcp-candidates-v2.json",
    }
    for label, model in (
        ("verification_pre", receipt_pre),
        ("verification_middle", receipt_middle),
        ("verification_post", receipt_post),
        ("direct_certification", direct),
        ("mcp_certification", mcp),
        ("direct_candidates", direct.candidate_catalog),
        ("mcp_candidates", mcp.candidate_catalog),
    ):
        _write_model(paths[label], model)
    return paths


def _raw_answers_artifact(
    path: Path,
    *,
    public,
) -> AnswersV2Artifact:
    text = path.read_text()
    payload = json.loads(text)
    if isinstance(payload, dict) and payload.get("schema_version") == "ori-eval-answers-v2":
        return AnswersV2Artifact.model_validate_json(text)
    if not isinstance(payload, dict):
        raise ValueError("v2 answers must be a JSON object")
    raw_answers = payload.get("answers")
    if isinstance(raw_answers, dict):
        return build_answers_artifact(public, raw_answers)
    if isinstance(raw_answers, list):
        converted: dict[str, dict[str, Any]] = {}
        for item in raw_answers:
            if not isinstance(item, dict):
                raise ValueError("v2 answer rows must be JSON objects")
            task_id = item.get("task_id")
            answer = item.get("answer")
            if not isinstance(task_id, str) or not isinstance(answer, dict):
                raise ValueError("v2 answer rows require task_id and structured answer")
            if task_id in converted:
                raise ValueError(f"duplicate v2 answer task ID: {task_id}")
            converted[task_id] = answer
        return build_answers_artifact(public, converted)
    raise ValueError("v2 answers object requires an answers mapping or list")


def score_v2_files(
    *,
    public_path: Path,
    oracle_path: Path,
    answers_path: Path,
    output_path: Path,
    expected_track: Track | None = None,
) -> ScoringV2Artifact:
    """Load an exact public/private pair and score a complete offline replay."""

    pair = load_v2_pair(public_path, oracle_path)
    if expected_track is not None and pair.public.track is not expected_track:
        raise ValueError(
            f"v2 artifact track is {pair.public.track.value!r}, "
            f"not requested {expected_track.value!r}"
        )
    answers = _raw_answers_artifact(answers_path, public=pair.public)
    scoring = score_answers_v2(pair.public, pair.private, answers)
    _write_model(output_path, scoring)
    return scoring
