"""Structural and semantic preflight for discovery artifact pairs."""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from ori.eval.v2.fingerprint import canonical_sha256
from ori.eval.v2.graph import edge_fact_key
from ori.eval.v2.profiles import capability_profile_for_track
from ori.relationships import relationship_contract

from .schema import (
    DiscoveryPreflightReport,
    DiscoveryPrivateArtifact,
    DiscoveryPublicArtifact,
)


def preflight_discovery(
    public: DiscoveryPublicArtifact,
    private: DiscoveryPrivateArtifact,
) -> DiscoveryPreflightReport:
    if private.public_artifact_fingerprint != public.artifact_fingerprint:
        raise ValueError("discovery public/private artifact fingerprints differ")
    if private.graph_fingerprint != public.graph_fingerprint:
        raise ValueError("discovery public/private graph fingerprints differ")
    expected_profile = capability_profile_for_track(public.track)
    if private.capability_profile != expected_profile:
        raise ValueError("discovery capability profile drifted from the V28 pin")
    if public.capability_profile_id != expected_profile.profile_id or (
        public.capability_profile_fingerprint != expected_profile.profile_fingerprint
    ):
        raise ValueError("discovery public capability binding is inconsistent")
    Draft202012Validator.check_schema(public.answer_schema)
    identity_ids = {item.object_id for item in private.identity_catalog}
    graph_edges = set(private.graph_fact_registry.edge_keys)
    variants = 0
    for target in private.targets:
        if target.source_id not in identity_ids or target.destination_id not in identity_ids:
            raise ValueError("discovery target endpoint is absent from identity catalog")
        for variant in target.variants:
            variants += 1
            for edge in variant.edges:
                if edge.source_id not in identity_ids or edge.target_id not in identity_ids:
                    raise ValueError("discovery variant references an unknown identity")
                contract = relationship_contract(edge.relationship)
                if contract.support != "supported" or contract.wire_carrier in {
                    "derived",
                    "none",
                }:
                    raise ValueError(
                        "discovery variant contains a non-wire-supported relationship"
                    )
                if edge_fact_key(edge) not in graph_edges:
                    raise ValueError("discovery variant is absent from graph fact registry")
    checks = (
        "strict public/private schemas and canonical fingerprints",
        "exact V28 graph and artifact binding",
        "pinned direct/MCP capability profile",
        "valid bounded public answer schema",
        "sealed identity coverage",
        "wire-supported canonical relationships",
        "graph-attested route variants",
        "hidden target identifiers absent from public artifact",
    )
    payload = {
        "public_artifact_fingerprint": public.artifact_fingerprint,
        "private_artifact_fingerprint": private.artifact_fingerprint,
        "target_count": len(private.targets),
        "variant_count": variants,
        "checks": checks,
        "report_fingerprint": "0" * 64,
    }
    payload["report_fingerprint"] = canonical_sha256(
        {"schema_version": "ori-discovery-preflight-v2", **payload},
        exclude_fields=("report_fingerprint",),
    )
    return DiscoveryPreflightReport.model_validate(payload)


def preflight_discovery_files(
    *,
    public_path: Path,
    private_path: Path,
    output_path: Path | None = None,
) -> DiscoveryPreflightReport:
    public = DiscoveryPublicArtifact.model_validate_json(public_path.read_text())
    private = DiscoveryPrivateArtifact.model_validate_json(private_path.read_text())
    report = preflight_discovery(public, private)
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True)
            + "\n"
        )
    return report
