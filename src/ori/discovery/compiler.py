"""Compile discovery truth from an exact V28 public/oracle artifact pair."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable, Sequence
from pathlib import Path

from ori.eval.v2.fingerprint import canonical_sha256
from ori.eval.v2.graph import GraphSnapshot, build_archive_snapshot
from ori.eval.v2.profiles import capability_profile_for_track
from ori.eval.v2.protocol import V2ArtifactPair, load_v2_pair
from ori.eval.v2.schema import (
    ClosedRouteVariantsPolicy,
    EdgeWitness,
    ExactRoutePolicy,
    ExecutionBounds,
)
from ori.relationships import canonical_relationship_kind, relationship_contract

from .schema import (
    DiscoveryPrivateArtifact,
    DiscoveryPublicArtifact,
    DiscoveryScope,
    DiscoveryTarget,
    DiscoveryVariant,
)

DISCOVERY_COMPILER_VERSION = "ori-discovery-compiler-v2.1"


def discovery_compiler_fingerprint() -> str:
    directory = Path(__file__).parent
    sources = {
        name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
        for name in ("compiler.py", "schema.py")
    }
    return canonical_sha256(
        {"version": DISCOVERY_COMPILER_VERSION, "sources": sources}
    )


def _edge_key(edge: EdgeWitness) -> tuple[str, str, str, str]:
    return (
        edge.source_id.casefold(),
        edge.relationship.casefold(),
        edge.target_id.casefold(),
        edge.direction.value,
    )


def _normalize_variant(
    edges: Sequence[EdgeWitness],
    snapshot: GraphSnapshot,
) -> DiscoveryVariant:
    normalized: list[EdgeWitness] = []
    for edge in edges:
        relationship = canonical_relationship_kind(edge.relationship)
        contract = relationship_contract(relationship)
        if contract.support != "supported" or contract.wire_carrier in {
            "derived",
            "none",
        }:
            raise ValueError(
                "discovery truth may contain only wire-supported relationships: "
                f"{edge.relationship} is {contract.support}/{contract.wire_carrier}"
            )
        source_type = snapshot.entity(edge.source_id).object_type
        target_type = snapshot.entity(edge.target_id).object_type
        if not contract.accepts_endpoints(source_type, target_type):
            raise ValueError(
                f"relationship {relationship} rejects endpoints "
                f"{source_type}->{target_type}"
            )
        normalized.append(edge.model_copy(update={"relationship": relationship}))
    normalized_edges = tuple(normalized)
    return DiscoveryVariant(
        variant_id=canonical_sha256(normalized_edges),
        edges=normalized_edges,
    )


def _strict_contiguous_subpath(
    candidate: Sequence[EdgeWitness],
    container: Sequence[EdgeWitness],
) -> bool:
    if len(candidate) >= len(container):
        return False
    wanted = tuple(_edge_key(edge) for edge in candidate)
    available = tuple(_edge_key(edge) for edge in container)
    return any(
        available[offset : offset + len(wanted)] == wanted
        for offset in range(len(available) - len(wanted) + 1)
    )


def _collapse_overlapping_targets(
    targets: Sequence[DiscoveryTarget],
) -> tuple[DiscoveryTarget, ...]:
    retained: list[DiscoveryTarget] = []
    for candidate in targets:
        is_only_overlap = all(
            any(
                _strict_contiguous_subpath(variant.edges, other_variant.edges)
                for other in targets
                if other.target_id != candidate.target_id
                for other_variant in other.variants
            )
            for variant in candidate.variants
        )
        if not is_only_overlap:
            retained.append(candidate)
    return tuple(sorted(retained, key=lambda item: item.target_id))


def _compile_targets(
    pair: V2ArtifactPair,
    snapshot: GraphSnapshot,
) -> tuple[DiscoveryTarget, ...]:
    tasks = {task.task_id: task for task in pair.public.tasks}
    grouped: dict[
        tuple[str, str],
        dict[str, set[object]],
    ] = defaultdict(lambda: {"variants": set(), "tasks": set()})
    variants: dict[str, DiscoveryVariant] = {}

    for oracle in pair.private.oracles:
        task = tasks[oracle.task_id]
        if not isinstance(task.answer_policy, (ExactRoutePolicy, ClosedRouteVariantsPolicy)):
            continue
        for raw_variant in oracle.route_variants:
            variant = _normalize_variant(raw_variant.edges, snapshot)
            if len(variant.edges) > task.binding.bounds.max_hops:
                raise ValueError(
                    f"discovery source task {task.task_id} exceeds its V28 hop bound"
                )
            variants[variant.variant_id] = variant
            endpoints = (
                variant.edges[0].source_id,
                variant.edges[-1].target_id,
            )
            grouped[endpoints]["variants"].add(variant.variant_id)
            grouped[endpoints]["tasks"].add(task.task_fingerprint)

    if not grouped:
        raise ValueError(
            "the V28 pair contains no closed or exact route oracles suitable for discovery"
        )

    targets: list[DiscoveryTarget] = []
    for (source_id, destination_id), raw in grouped.items():
        variant_ids = tuple(sorted(str(item) for item in raw["variants"]))
        target_id = canonical_sha256(
            {
                "source_id": source_id,
                "destination_id": destination_id,
                "variant_ids": variant_ids,
            }
        )
        targets.append(
            DiscoveryTarget(
                target_id=target_id,
                source_id=source_id,
                destination_id=destination_id,
                variants=tuple(variants[item] for item in variant_ids),
                source_task_fingerprints=tuple(
                    sorted(str(item) for item in raw["tasks"])
                ),
            )
        )
    collapsed = _collapse_overlapping_targets(targets)
    if not collapsed:
        raise ValueError("overlap collapse removed every discovery objective")
    return collapsed


def _aggregate_bounds(pair: V2ArtifactPair, targets: Sequence[DiscoveryTarget]) -> ExecutionBounds:
    task_bounds = [task.binding.bounds for task in pair.public.tasks]
    if not task_bounds:
        raise ValueError("V28 public artifact has no task execution bounds")
    largest_truth = max(len(variant.edges) for item in targets for variant in item.variants)
    return ExecutionBounds(
        max_hops=max(largest_truth, max(item.max_hops for item in task_bounds)),
        max_result_cardinality=min(
            256,
            max(len(targets) * 4, max(item.max_result_cardinality for item in task_bounds)),
        ),
        page_size=min(item.page_size for item in task_bounds),
        result_offset=0,
        max_pages=max(item.max_pages for item in task_bounds),
        require_total_count=False,
        require_stable_ordering=all(
            item.require_stable_ordering for item in task_bounds
        ),
        max_output_bytes=min(item.max_output_bytes for item in task_bounds),
        max_transcript_bytes=min(item.max_transcript_bytes for item in task_bounds),
        max_tool_calls=max(item.max_tool_calls for item in task_bounds),
        timeout_seconds=max(item.timeout_seconds for item in task_bounds),
    )


def _answer_schema(bounds: ExecutionBounds) -> dict[str, object]:
    edge = {
        "type": "object",
        "additionalProperties": False,
        "required": ["source_id", "relationship", "target_id"],
        "properties": {
            "source_id": {"type": "string", "minLength": 1},
            "relationship": {"type": "string", "minLength": 1},
            "target_id": {"type": "string", "minLength": 1},
            "direction": {"enum": ["outbound", "inbound"]},
            "properties": {"type": "array", "maxItems": 32},
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "protocol_version",
            "public_artifact_fingerprint",
            "findings",
        ],
        "properties": {
            "schema_version": {"const": "ori-discovery-submission-v2"},
            "protocol_version": {"const": "ori-open-world-discovery-v2"},
            "public_artifact_fingerprint": {
                "type": "string",
                "pattern": "^[0-9a-f]{64}$",
            },
            "findings": {
                "type": "array",
                "maxItems": bounds.max_result_cardinality,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "finding_id",
                        "route",
                        "evidence_edges",
                        "confidence",
                    ],
                    "properties": {
                        "finding_id": {"type": "string", "minLength": 1},
                        "route": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": bounds.max_hops,
                            "items": edge,
                        },
                        "evidence_edges": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": bounds.max_hops * 2,
                            "items": edge,
                        },
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                },
            },
        },
    }


def compile_discovery(
    *,
    pair: V2ArtifactPair,
    snapshot: GraphSnapshot,
) -> tuple[DiscoveryPublicArtifact, DiscoveryPrivateArtifact]:
    """Compile one exact, campaign-union discovery corpus."""

    if snapshot.graph_fingerprint != pair.public.graph_fingerprint:
        raise ValueError("SharpHound archive does not match the V28 artifact graph")
    if snapshot.seed != pair.public.seed or snapshot.product != pair.public.product:
        raise ValueError("SharpHound archive identity does not match the V28 artifact pair")
    profile = capability_profile_for_track(pair.public.track)
    if any(
        task.binding.capability_profile_id != profile.profile_id
        for task in pair.public.tasks
    ):
        raise ValueError("V28 tasks are not bound to the pinned track capability profile")
    targets = _compile_targets(pair, snapshot)
    bounds = _aggregate_bounds(pair, targets)
    compiler_fingerprint = discovery_compiler_fingerprint()
    public_payload = {
        "product": pair.public.product,
        "track": pair.public.track,
        "seed": pair.public.seed,
        "source_public_artifact_fingerprint": pair.public.artifact_fingerprint,
        "source_manifest_fingerprint": pair.public.source_manifest_fingerprint,
        "graph_fingerprint": pair.public.graph_fingerprint,
        "capability_profile_id": profile.profile_id,
        "capability_profile_fingerprint": profile.profile_fingerprint,
        "bounds": bounds,
        "scope": DiscoveryScope(),
        "instructions": (
            "Discover attack routes anywhere in the declared benchmark namespace.",
            "Submit stable object IDs or aliases and canonical BloodHound relationships.",
            "Every route must include graph-derived evidence edges; unsupported "
            "claims are false positives.",
            "Alternate routes to the same objective and strict overlapping "
            "subpaths do not create extra required objectives.",
        ),
        "answer_schema": _answer_schema(bounds),
        "compiler_fingerprint": compiler_fingerprint,
        "artifact_fingerprint": "0" * 64,
    }
    public_payload["artifact_fingerprint"] = canonical_sha256(
        {
            "schema_version": "ori-discovery-public-v2",
            "protocol_version": "ori-open-world-discovery-v2",
            **public_payload,
        },
        exclude_fields=("artifact_fingerprint",),
    )
    public = DiscoveryPublicArtifact.model_validate(public_payload)
    private_payload = {
        "public_artifact_fingerprint": public.artifact_fingerprint,
        "source_oracle_artifact_fingerprint": pair.private.artifact_fingerprint,
        "graph_fingerprint": pair.private.graph_fingerprint,
        "capability_profile": profile,
        "identity_catalog": pair.private.identity_catalog,
        "identity_catalog_fingerprint": pair.private.identity_catalog_fingerprint,
        "graph_fact_registry": pair.private.graph_fact_registry,
        "targets": targets,
        "target_catalog_fingerprint": canonical_sha256(targets),
        "artifact_fingerprint": "0" * 64,
    }
    private_payload["artifact_fingerprint"] = canonical_sha256(
        {
            "schema_version": "ori-discovery-oracle-v2",
            "protocol_version": "ori-open-world-discovery-v2",
            **private_payload,
        },
        exclude_fields=("artifact_fingerprint",),
    )
    private = DiscoveryPrivateArtifact.model_validate(private_payload)
    return public, private


def _write_model(path: Path, model: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not hasattr(model, "model_dump"):
        raise TypeError("discovery artifacts must be strict models")
    payload = model.model_dump(mode="json")  # type: ignore[attr-defined]
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def compile_discovery_files(
    *,
    manifest_path: Path,
    archive_path: Path,
    v2_public_path: Path,
    v2_oracle_path: Path,
    output_dir: Path,
) -> dict[str, Path]:
    manifest = json.loads(manifest_path.read_text())
    if not isinstance(manifest, dict):
        raise ValueError("source ORI manifest must be a JSON object")
    pair = load_v2_pair(v2_public_path, v2_oracle_path)
    snapshot = build_archive_snapshot(
        archive_path,
        manifest,
        product=pair.public.product,
    )
    public, private = compile_discovery(pair=pair, snapshot=snapshot)
    stem = f"{public.product}-{public.track.value}-seed-{public.seed}-discovery"
    paths = {
        "public": output_dir / f"{stem}-public-v2.json",
        "private": output_dir / f"{stem}-oracles-v2.private.json",
    }
    _write_model(paths["public"], public)
    _write_model(paths["private"], private)
    return paths


def iter_truth_variants(
    private: DiscoveryPrivateArtifact,
) -> Iterable[tuple[DiscoveryTarget, DiscoveryVariant]]:
    for target in private.targets:
        for variant in target.variants:
            yield target, variant
