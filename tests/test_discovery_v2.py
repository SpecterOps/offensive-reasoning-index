"""Unit and adversarial coverage for V28-native discovery."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner
from pydantic import ValidationError

from ori.cli import main
from ori.discovery.compiler import _normalize_variant
from ori.discovery.grader import (
    DiscoverySubmissionError,
    _load_json_bytes,
    grade_discovery,
)
from ori.discovery.schema import (
    DiscoveryFinding,
    DiscoveryPrivateArtifact,
    DiscoveryPublicArtifact,
    DiscoveryScope,
    DiscoverySubmission,
    DiscoveryTarget,
    DiscoveryVariant,
)
from ori.eval.v2.fingerprint import canonical_sha256
from ori.eval.v2.graph import (
    GraphObject,
    GraphSnapshot,
    build_graph_fact_registry,
)
from ori.eval.v2.profiles import PINNED_DIRECT_CAPABILITY_PROFILE
from ori.eval.v2.schema import EdgeWitness, EntityRef, ExecutionBounds, Track

ZERO = "0" * 64


def _snapshot() -> GraphSnapshot:
    entities = (
        EntityRef(
            object_id="U-1",
            object_type="User",
            domain="LAB.LOCAL",
            role="sealed_identity",
            canonical_name="alice@LAB.LOCAL",
            aliases=("Alice",),
        ),
        EntityRef(
            object_id="G-1",
            object_type="Group",
            domain="LAB.LOCAL",
            role="sealed_identity",
            canonical_name="OPS@LAB.LOCAL",
            aliases=("Ops",),
        ),
        EntityRef(
            object_id="D-1",
            object_type="Domain",
            domain="LAB.LOCAL",
            role="sealed_identity",
            canonical_name="LAB.LOCAL",
        ),
    )
    relationships = (
        EdgeWitness(source_id="U-1", relationship="MemberOf", target_id="G-1"),
        EdgeWitness(source_id="G-1", relationship="GenericAll", target_id="D-1"),
        EdgeWitness(source_id="U-1", relationship="GenericAll", target_id="D-1"),
    )
    payload = {
        "schema_version": "ori-graph-snapshot-v2",
        "manifest_schema_version": "ori-generated-manifest-v2",
        "product": "complex",
        "seed": 4401,
        "domain": "LAB.LOCAL",
        "domain_sid": "S-1-5-21-1",
        "objects": tuple(GraphObject(entity=entity) for entity in entities),
        "relationships": relationships,
        "relationship_counts": (),
        "normalized_artifacts": (),
        "graph_fingerprint": ZERO,
    }
    payload["graph_fingerprint"] = canonical_sha256(
        payload, exclude_fields=("graph_fingerprint",)
    )
    return GraphSnapshot.model_validate(payload)


def _variant(edges: tuple[EdgeWitness, ...]) -> DiscoveryVariant:
    return DiscoveryVariant(variant_id=canonical_sha256(edges), edges=edges)


def _artifacts(
    *, seed: int = 4401
) -> tuple[DiscoveryPublicArtifact, DiscoveryPrivateArtifact]:
    snapshot = _snapshot()
    bounds = ExecutionBounds(
        max_hops=4,
        max_result_cardinality=8,
        page_size=8,
        result_offset=0,
        max_pages=2,
        require_total_count=False,
        require_stable_ordering=True,
        max_output_bytes=4096,
        max_transcript_bytes=8192,
        max_tool_calls=0,
        timeout_seconds=30.0,
    )
    public_payload = {
        "product": "complex",
        "track": Track.DIRECT,
        "seed": seed,
        "source_public_artifact_fingerprint": "1" * 64,
        "source_manifest_fingerprint": "2" * 64,
        "graph_fingerprint": snapshot.graph_fingerprint,
        "capability_profile_id": PINNED_DIRECT_CAPABILITY_PROFILE.profile_id,
        "capability_profile_fingerprint": (
            PINNED_DIRECT_CAPABILITY_PROFILE.profile_fingerprint
        ),
        "bounds": bounds,
        "scope": DiscoveryScope(),
        "instructions": ("Find every objective.",),
        "answer_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"findings": {"type": "array", "maxItems": 8}},
        },
        "compiler_fingerprint": "3" * 64,
        "artifact_fingerprint": ZERO,
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

    full = _variant(snapshot.relationships[:2])
    alternate = _variant((snapshot.relationships[2],))
    variant_ids = tuple(sorted((full.variant_id, alternate.variant_id)))
    variant_by_id = {item.variant_id: item for item in (full, alternate)}
    target_id = canonical_sha256(
        {
            "source_id": "U-1",
            "destination_id": "D-1",
            "variant_ids": variant_ids,
        }
    )
    target = DiscoveryTarget(
        target_id=target_id,
        source_id="U-1",
        destination_id="D-1",
        variants=tuple(variant_by_id[item] for item in variant_ids),
        source_task_fingerprints=("4" * 64,),
    )
    registry = build_graph_fact_registry(snapshot)
    private_payload = {
        "public_artifact_fingerprint": public.artifact_fingerprint,
        "source_oracle_artifact_fingerprint": "5" * 64,
        "graph_fingerprint": snapshot.graph_fingerprint,
        "capability_profile": PINNED_DIRECT_CAPABILITY_PROFILE,
        "identity_catalog": snapshot.entities,
        "identity_catalog_fingerprint": canonical_sha256(snapshot.entities),
        "graph_fact_registry": registry,
        "targets": (target,),
        "target_catalog_fingerprint": canonical_sha256((target,)),
        "artifact_fingerprint": ZERO,
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


def _finding(
    finding_id: str,
    route: tuple[EdgeWitness, ...],
    *,
    evidence: tuple[EdgeWitness, ...] | None = None,
) -> DiscoveryFinding:
    return DiscoveryFinding(
        finding_id=finding_id,
        route=route,
        evidence_edges=evidence if evidence is not None else route,
        confidence=0.9,
    )


def _submission(
    public: DiscoveryPublicArtifact,
    findings: tuple[DiscoveryFinding, ...],
) -> DiscoverySubmission:
    return DiscoverySubmission(
        public_artifact_fingerprint=public.artifact_fingerprint,
        findings=findings,
    )


def test_seed_changes_public_artifact_identity() -> None:
    first, _ = _artifacts(seed=4401)
    second, _ = _artifacts(seed=4402)
    assert first.artifact_fingerprint != second.artifact_fingerprint


def test_aliases_and_relationship_case_normalize() -> None:
    public, private = _artifacts()
    route = (
        EdgeWitness(source_id="Alice", relationship="memberOF", target_id="Ops"),
        EdgeWitness(source_id="Ops", relationship="genericall", target_id="LAB.LOCAL"),
    )
    report = grade_discovery(public, private, _submission(public, (_finding("a", route),)))
    assert report.matched_objectives == 1
    assert report.f1 == 1.0


def test_alternate_and_overlap_are_redundant_not_required() -> None:
    public, private = _artifacts()
    full = (
        EdgeWitness(source_id="U-1", relationship="MemberOf", target_id="G-1"),
        EdgeWitness(source_id="G-1", relationship="GenericAll", target_id="D-1"),
    )
    alternate = (
        EdgeWitness(source_id="U-1", relationship="GenericAll", target_id="D-1"),
    )
    overlap = (full[0],)
    report = grade_discovery(
        public,
        private,
        _submission(
            public,
            (
                _finding("full", full),
                _finding("alternate", alternate),
                _finding("overlap", overlap),
            ),
        ),
    )
    assert report.matched_objectives == 1
    assert report.objective_count == 1
    assert report.redundant_count == 2
    assert report.false_positive_count == 0
    assert report.precision == 1.0


def test_missing_evidence_and_unsupported_relationship_are_false_positives() -> None:
    public, private = _artifacts()
    truth = (
        EdgeWitness(source_id="U-1", relationship="GenericAll", target_id="D-1"),
    )
    unsupported = (
        EdgeWitness(source_id="U-1", relationship="EnterpriseCAFor", target_id="D-1"),
    )
    report = grade_discovery(
        public,
        private,
        _submission(
            public,
            (
                _finding("no-evidence", truth, evidence=()),
                _finding("unsupported", unsupported),
            ),
        ),
    )
    assert report.matched_objectives == 0
    assert report.false_positive_count == 2
    assert report.f1 == 0.0


def test_compiler_rejects_internal_only_relationship() -> None:
    snapshot = _snapshot()
    with pytest.raises(ValueError, match="wire-supported"):
        _normalize_variant(
            (
                EdgeWitness(
                    source_id="U-1",
                    relationship="EnterpriseCAFor",
                    target_id="D-1",
                ),
            ),
            snapshot,
        )


def test_malformed_duplicate_and_oversized_output_are_rejected() -> None:
    with pytest.raises(DiscoverySubmissionError, match="valid JSON"):
        _load_json_bytes(b"{", max_bytes=100)
    with pytest.raises(DiscoverySubmissionError, match="duplicate JSON"):
        _load_json_bytes(b'{"a":1,"a":2}', max_bytes=100)
    with pytest.raises(DiscoverySubmissionError, match="maximum"):
        _load_json_bytes(b"{}" * 60, max_bytes=100)


def test_strict_models_reject_extra_fields_and_public_leakage() -> None:
    public, _ = _artifacts()
    payload = {
        "public_artifact_fingerprint": public.artifact_fingerprint,
        "findings": [],
        "unexpected": True,
    }
    with pytest.raises(ValidationError):
        DiscoverySubmission.model_validate(payload)

    leaked = public.model_dump(mode="python")
    leaked["answer_schema"] = {"properties": {"variant_id": {"type": "string"}}}
    leaked["artifact_fingerprint"] = canonical_sha256(
        leaked, exclude_fields=("artifact_fingerprint",)
    )
    with pytest.raises(ValidationError, match="scorer-only field"):
        DiscoveryPublicArtifact.model_validate(leaked)


def test_public_score_report_does_not_leak_private_target_ids() -> None:
    public, private = _artifacts()
    truth = (
        EdgeWitness(source_id="U-1", relationship="GenericAll", target_id="D-1"),
    )
    report = grade_discovery(public, private, _submission(public, (_finding("x", truth),)))
    rendered = json.dumps(report.model_dump(mode="json"), sort_keys=True)
    assert private.targets[0].target_id not in rendered
    assert "variant_id" not in rendered


def test_discovery_cli_exposes_offline_commands_only() -> None:
    result = CliRunner().invoke(main, ["discovery", "--help"])
    assert result.exit_code == 0
    assert "compile" in result.output
    assert "preflight" in result.output
    assert "grade" in result.output
    assert "run" not in result.output
