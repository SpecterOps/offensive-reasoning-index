"""Native five-kind compilation bound to the independently certified source release."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .diagnostic_selection import DiagnosticSelectionReceiptV1, select_diagnostic_canary
from .fingerprint import canonical_sha256
from .fixtures import offline_certify
from .graph import build_archive_snapshot
from .native_feasibility import NativeDevelopmentTrack, validate_native_development_track
from .oaic_recipes import OAIC_RECIPE_REGISTRY, OAICRecipeMetadata, _compile_oaic_recipe
from .protocol import V2ArtifactPair, build_artifacts
from .release_selection import PairedSelectedRelease, SelectedReleaseReceipt
from .schema import Track


def original_diagnostic_inputs(resolved):
    """Revalidate the original complete paired source, never trust a five-item hash alone."""
    from .campaign_config import load_v2_campaign_config
    from .campaign_runner import _prepare_track, prepare_selected_tracks

    paths = resolved.native_mcp_paths
    if paths is None or paths.original_config is None:
        raise ValueError("NATIVE_DIAGNOSTIC_ORIGINAL_CONFIG_REQUIRED")
    source = load_v2_campaign_config(paths.original_config)
    if (
        source.native_mcp_paths is not None
        or source.config.purpose != "official"
        or source.selected_release is None
        or resolved.selected_release is None
    ):
        raise ValueError("NATIVE_DIAGNOSTIC_ORIGINAL_CONFIG_INVALID")
    if (
        source.source_manifest.read_bytes() != resolved.source_manifest.read_bytes()
        or source.archive.read_bytes() != resolved.archive.read_bytes()
        or source.selected_release.read_bytes() != resolved.selected_release.read_bytes()
    ):
        raise ValueError("NATIVE_DIAGNOSTIC_SOURCE_MISMATCH")
    manifest = json.loads(source.source_manifest.read_text())
    snapshot = build_archive_snapshot(source.archive, manifest, product="oaic-2026-v1")
    tracks = prepare_selected_tracks(
        source,
        snapshot,
        {track: _prepare_track(source, track, snapshot) for track in source.tracks},
    )
    mcp_paths = source.tracks[Track.MCP]
    paired = PairedSelectedRelease.model_validate_json(source.selected_release.read_text())
    selection = select_diagnostic_canary(
        public=tracks[Track.MCP].pair.public,
        candidates=tracks[Track.MCP].release,
        metadata=OAICRecipeMetadata.model_validate_json(mcp_paths.release_metadata.read_text()),
        source_archive_sha256=hashlib.sha256(source.archive.read_bytes()).hexdigest(),
        paired_release=paired,
        mcp_selection=SelectedReleaseReceipt.model_validate_json(mcp_paths.selection.read_text()),
    )
    if resolved.canary_selection is not None and selection != (
        DiagnosticSelectionReceiptV1.model_validate_json(resolved.canary_selection.read_text())
    ):
        raise ValueError("NATIVE_DIAGNOSTIC_SELECTION_MISMATCH")
    return source, snapshot, selection, paired


def compile_native_diagnostic(resolved, profile) -> NativeDevelopmentTrack:
    """Compile and replay the exact source roster through native proof, offline."""
    from . import compiler
    from .native_capability import validate_native_capability_profile
    from .oaic_recipes import compile_oaic_product

    source, snapshot, selection, _ = original_diagnostic_inputs(resolved)
    profile = validate_native_capability_profile(profile)
    manifest = json.loads(source.source_manifest.read_text())
    base = compile_oaic_product(manifest, snapshot, track=Track.MCP)
    recipes = {recipe.recipe_id: recipe for recipe in OAIC_RECIPE_REGISTRY}
    planted = compiler._paths_by_template(manifest)
    tasks = tuple(
        _compile_oaic_recipe(
            recipes[entry.recipe_id],
            snapshot,
            planted,
            base.graph_fact_registry,
            track=Track.MCP,
            native_profile=profile,
        )
        for entry in selection.entries
    )
    if tuple(task.public.task_id for task in tasks) != selection.selected_task_ids:
        raise ValueError("NATIVE_DIAGNOSTIC_ROSTER_MISMATCH")
    payload = base.model_dump(mode="python")
    payload["tasks"] = tasks
    payload["catalog_fingerprint"] = canonical_sha256(
        payload, exclude_fields=("catalog_fingerprint",)
    )
    corpus = compiler.CompiledCorpus.model_validate(payload)
    public, private = build_artifacts(corpus, identity_catalog=snapshot.entities)
    result = NativeDevelopmentTrack(
        pair=V2ArtifactPair(public=public, private=private),
        profile=profile,
        certifications=tuple(
            offline_certify(task, snapshot, native_profile=profile) for task in tasks
        ),
        selection=selection,
        corpus=corpus,
    )
    return validate_native_development_track(result)


def prepare_native_canary_files(*, config_path: Path, suite: str, output_dir: Path):
    """Prepare native diagnostic artifacts without invoking services or providers."""
    import yaml

    from .campaign_config import load_v2_campaign_config, load_v2_native_qualification_config
    from .campaign_runner import prepare_v2_campaign
    from .cli_support import _atomic_write_text, prepare_canary_v2_files
    from .native_feasibility import write_native_development_artifacts
    from .native_setup import absolute_config_document

    resolved, _, full, _, _ = prepare_v2_campaign(config_path)
    # Full whole-roster admission must succeed first; diagnostics never bypass it.
    if resolved.native_mcp_paths is None or resolved.native_mcp_paths.original_config is None:
        raise ValueError("NATIVE_DIAGNOSTIC_ORIGINAL_CONFIG_REQUIRED")
    if resolved.config.purpose != "official":
        raise ValueError("NATIVE_DIAGNOSTIC_OFFICIAL_PARENT_REQUIRED")
    source = prepare_canary_v2_files(
        config_path=resolved.native_mcp_paths.original_config,
        suite=suite,
        output_dir=output_dir / "source-diagnostic",
    )
    generic = load_v2_campaign_config(source["config"])
    document = absolute_config_document(generic)
    document["defaults"] = absolute_config_document(resolved)["defaults"]
    document["models"] = resolved.config.model_dump(mode="json")["models"]
    document["output_dir"] = str((output_dir / "campaign").absolute())
    native = document["defaults"]["mcp"]
    native["qualification"] = None
    native["qualification_work"] = None
    temporary = output_dir / "native-input.private.yaml"
    _atomic_write_text(temporary, yaml.safe_dump(document, sort_keys=True))
    unqualified = load_v2_native_qualification_config(temporary)
    prepared = compile_native_diagnostic(unqualified, full[Track.MCP].profile)
    artifact_dir = output_dir / "native-artifacts"
    write_native_development_artifacts(prepared, artifact_dir)
    qualification_dir = (output_dir / "qualification").absolute()
    document["tracks"]["mcp"].update(
        public=str((artifact_dir / "native-public-v2.json").absolute()),
        oracles=str((artifact_dir / "native-oracles-v2.private.json").absolute()),
        candidates=str(qualification_dir / "native-candidates-v2.private.json"),
        live_certification=str(qualification_dir / "native-live-certification-v4.private.json"),
    )
    native["qualification"] = str(qualification_dir / "native-qualification-v2.private.json")
    native["qualification_work"] = str(qualification_dir / "native-work-v2.private.json")
    destination = output_dir / "canary-config-v2.yaml"
    _atomic_write_text(destination, yaml.safe_dump(document, sort_keys=True))
    load_v2_native_qualification_config(destination)
    return {
        "selection": source["selection"],
        "config": destination,
        "qualification_output_dir": qualification_dir,
    }
