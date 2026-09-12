"""File-only native setup adapters; no runtime installation, credentials, or model work."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Literal

import yaml

from .campaign_config import load_v2_campaign_config, load_v2_native_qualification_config
from .fingerprint import canonical_sha256
from .graph import build_archive_snapshot
from .native_capability import NativeCapabilityProfile, validate_native_capability_profile
from .native_feasibility import assess_native_selected_cell, write_native_development_artifacts
from .native_mcp_profiles import get_native_implementation
from .release_selection import SelectedReleaseReceipt
from .schema import Fingerprint, GitCommit, NonEmptyStr, StrictModel, Track


class NativeRuntimeInputV1(StrictModel):
    schema_version: Literal["hermes-ori-native-runtime-spec-v1"]
    implementation_id: Literal["mwnickerson", "mordavid", "armadin"]
    source_revision: GitCommit
    mcp_dir: NonEmptyStr
    python_executable: NonEmptyStr
    runtime_roots: tuple[NonEmptyStr, ...]
    dependency_lock: NonEmptyStr
    expected_runtime_hash: Fingerprint | None = None
    backend: Literal["bhce", "neo4j"]
    databases: tuple[NonEmptyStr, ...]
    credential_environment_names: tuple[NonEmptyStr, ...]
    backend_connection_reference: NonEmptyStr


def file_reference(path: Path, *, schema_version: str | None = None) -> dict:
    return {
        "path": str(path.absolute()),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "schema_version": schema_version,
    }


def absolute_config_document(resolved) -> dict:
    """Resolve every path before emitting a differently located private config."""
    document = resolved.config.model_dump(mode="json")
    document["source"] = {
        "manifest": str(resolved.source_manifest),
        "archive": str(resolved.archive),
    }
    document["tracks"] = {
        track.value: {
            name: str(value) if value is not None else None
            for name, value in paths.model_dump().items()
        }
        for track, paths in resolved.tracks.items()
    }
    document["output_dir"] = str(resolved.output_dir)
    document["selected_release"] = (
        str(resolved.selected_release) if resolved.selected_release else None
    )
    document["canary_selection"] = (
        str(resolved.canary_selection) if resolved.canary_selection else None
    )
    if resolved.mcp_dir:
        document["defaults"]["mcp"]["mcp_dir"] = str(resolved.mcp_dir)
    if resolved.native_mcp_paths:
        for name, value in resolved.native_mcp_paths.model_dump().items():
            document["defaults"]["mcp"][name] = (
                [str(path) for path in value]
                if isinstance(value, tuple)
                else str(value)
                if value is not None
                else None
            )
    return document


def inspect_artifact_index(config_path: Path) -> dict:
    """Validate an existing original paired release and return private references."""
    from .campaign_runner import _prepare_track, prepare_selected_tracks

    resolved = load_v2_campaign_config(config_path)
    if resolved.native_mcp_paths or resolved.config.purpose != "official":
        raise ValueError("ORIGINAL_OFFICIAL_ARTIFACT_CONFIG_REQUIRED")
    manifest = json.loads(resolved.source_manifest.read_text())
    snapshot = build_archive_snapshot(resolved.archive, manifest, product="oaic-2026-v1")
    prepared = prepare_selected_tracks(
        resolved,
        snapshot,
        {track: _prepare_track(resolved, track, snapshot) for track in resolved.tracks},
    )
    selection = SelectedReleaseReceipt.model_validate_json(
        resolved.tracks[Track.MCP].selection.read_text(),
    )
    refs = {
        "config": file_reference(config_path),
        "source_manifest": file_reference(resolved.source_manifest),
        "source_archive": file_reference(resolved.archive),
        "selected_release": file_reference(resolved.selected_release),
    }
    for track, paths in resolved.tracks.items():
        for name, value in paths.model_dump().items():
            if value is not None:
                refs[f"{track.value}.{name}"] = file_reference(value)
    return {
        "schema_version": "ori-v2-artifact-index-v1",
        "outcome": "validated",
        "product": snapshot.product,
        "seed": snapshot.seed,
        "source_config_fingerprint": resolved.source_config_fingerprint,
        "graph_fingerprint": snapshot.graph_fingerprint,
        "paired_release_fingerprint": prepared[Track.MCP].paired_release_fingerprint,
        "mcp_selection_fingerprint": selection.selection_fingerprint,
        "mcp_tasks": len(selection.entries),
        "mcp_kind_counts": dict(Counter(entry.claim_kind for entry in selection.entries)),
        "provider_calls": 0,
        "service_contacts": 0,
        "artifacts": refs,
    }


def prepare_native_config(
    *,
    config_path: Path,
    native_profile: Path,
    runtime_spec: Path,
    output_dir: Path,
    model: str,
    model_name: str,
    model_base_url: str,
    max_infra_retries: int = 1,
) -> dict:
    """Project an admitted original release to one pinned native Main/model cell."""
    from ori.native_runtime import fingerprint_native_runtime_roots

    from .cli_support import _atomic_write_text

    index = inspect_artifact_index(config_path)
    if index["seed"] != 67:
        raise ValueError("NATIVE_FIRST_RELEASE_SEED_MISMATCH")
    resolved = load_v2_campaign_config(config_path)
    profile = validate_native_capability_profile(
        NativeCapabilityProfile.model_validate_json(native_profile.read_text()),
    )
    spec = NativeRuntimeInputV1.model_validate_json(runtime_spec.read_text())
    implementation = get_native_implementation(spec.implementation_id)

    def resolve(value):
        path = Path(value).expanduser()
        return path.absolute() if path.is_absolute() else (runtime_spec.parent / path).absolute()

    roots = tuple(resolve(root) for root in spec.runtime_roots)
    lock = resolve(spec.dependency_lock)
    observed_runtime = fingerprint_native_runtime_roots(roots)
    lock_hash = hashlib.sha256(lock.read_bytes()).hexdigest()
    if (
        spec.source_revision != implementation.revision
        or spec.implementation_id != profile.implementation_id
        or spec.backend != profile.backend
        or implementation.backend != spec.backend
        or observed_runtime != profile.runtime_fingerprint
        or lock_hash != profile.dependency_lock_fingerprint
        or spec.expected_runtime_hash not in {None, observed_runtime}
    ):
        raise ValueError("NATIVE_SETUP_RUNTIME_PROFILE_MISMATCH")
    selection = SelectedReleaseReceipt.model_validate_json(
        resolved.tracks[Track.MCP].selection.read_text(),
    )
    result = assess_native_selected_cell(
        manifest=json.loads(resolved.source_manifest.read_text()),
        archive=resolved.archive.read_bytes(),
        selection=selection,
        profile=profile,
    )
    if not result.offline_feasible:
        raise ValueError("NATIVE_SELECTED_CELL_UNSUPPORTED")
    document = absolute_config_document(resolved)
    document["modes"] = ["mcp"]
    document["output_dir"] = str((output_dir / "campaign").absolute())
    document["models"] = [
        {
            "name": model_name,
            "provider": "openai-compat",
            "model": model,
            "api_surface": "chat_completions",
            "model_base_url": model_base_url,
            "mcp_tool_loop": "native-openai-compatible",
        }
    ]
    document["defaults"].update(
        concurrency=1, runs_per_model=1, reasoning_effort=None, max_infra_retries=max_infra_retries
    )
    document["defaults"]["infra_retry"]["immediate_retries"] = min(1, max_infra_retries)
    qualification = (output_dir / "qualification").absolute()
    document["defaults"]["mcp"] = {
        "mode": "native",
        "implementation_id": spec.implementation_id,
        "mcp_dir": str(resolve(spec.mcp_dir)),
        "python_executable": str(resolve(spec.python_executable)),
        "runtime_roots": [str(root) for root in roots],
        "runtime_fingerprint": observed_runtime,
        "dependency_lock": str(lock),
        "dependency_lock_fingerprint": lock_hash,
        "capability_profile": str(native_profile.absolute()),
        "backend": spec.backend,
        "databases": list(spec.databases),
        "original_config": str(config_path.absolute()),
        "qualification": str(qualification / "native-qualification-v2.private.json"),
        "qualification_work": str(qualification / "native-work-v2.private.json"),
        "max_steps": max(
            task.binding.bounds.max_tool_calls for task in result.development.pair.public.tasks
        ),
        "tool_loop": "native-openai-compatible",
    }
    native_artifacts = (output_dir / "artifacts").absolute()
    document["tracks"]["mcp"].update(
        public=str(native_artifacts / "native-public-v2.json"),
        oracles=str(native_artifacts / "native-oracles-v2.private.json"),
        candidates=str(qualification / "native-candidates-v2.private.json"),
        live_certification=str(qualification / "native-live-certification-v4.private.json"),
    )
    from .campaign_config import V2CampaignConfig

    V2CampaignConfig.model_validate(yaml.safe_load(yaml.safe_dump(document)))
    output_dir.mkdir(parents=True, mode=0o700)
    write_native_development_artifacts(result.development, native_artifacts)
    destination = output_dir / "native-config-v2.private.yaml"
    _atomic_write_text(destination, yaml.safe_dump(document, sort_keys=True))
    load_v2_native_qualification_config(destination)
    return {
        "schema_version": "ori-v2-native-config-result-v1",
        "outcome": "prepared",
        "implementation": profile.implementation_id,
        "provider_calls": 0,
        "source_index_fingerprint": canonical_sha256(index),
        "qualification_output_dir": str(qualification),
        "artifacts": {
            "config": file_reference(destination),
            "public": file_reference(native_artifacts / "native-public-v2.json"),
            "oracles": file_reference(native_artifacts / "native-oracles-v2.private.json"),
        },
    }
