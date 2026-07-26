"""File-oriented helpers for explicit protocol-v2 CLI commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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


def _write_model(path: Path, model: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(model.model_dump(mode="json"), indent=2, sort_keys=True)
        + "\n"
    )


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
    certification_path = (
        output_dir / f"{stem}-offline-certification-v2.private.json"
    )
    write_artifacts(
        corpus,
        public_path=public_path,
        oracle_path=oracle_path,
        identity_catalog=snapshot.entities,
    )
    _write_model(inventory_path, inventory)
    _write_model(certification_path, offline)
    return {
        "public": public_path,
        "oracles": oracle_path,
        "inventory": inventory_path,
        "offline_certification": certification_path,
    }


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
    profiles = {
        track: capability_profile_for_track(track)
        for track in (Track.DIRECT, Track.MCP)
    }
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
                f"BloodHound health check failed before v2 certification: "
                f"{health.detail}"
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
        "direct_certification": (
            output_dir / f"{stem}-direct-live-certification.private.json"
        ),
        "mcp_certification": (
            output_dir / f"{stem}-mcp-live-certification.private.json"
        ),
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
    if (
        isinstance(payload, dict)
        and payload.get("schema_version") == "ori-eval-answers-v2"
    ):
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
                raise ValueError(
                    "v2 answer rows require task_id and structured answer"
                )
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
