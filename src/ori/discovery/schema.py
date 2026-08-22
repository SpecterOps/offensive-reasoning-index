"""Strict public, private, submission, and report models for discovery v2."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from ori.eval.v2.fingerprint import canonical_sha256
from ori.eval.v2.schema import (
    CapabilityProfile,
    EdgeWitness,
    EntityRef,
    ExecutionBounds,
    Fingerprint,
    GraphFactRegistry,
    NonEmptyStr,
    StrictModel,
    Track,
)

DISCOVERY_PROTOCOL_VERSION = "ori-open-world-discovery-v2"
DISCOVERY_PUBLIC_SCHEMA = "ori-discovery-public-v2"
DISCOVERY_PRIVATE_SCHEMA = "ori-discovery-oracle-v2"
DISCOVERY_SUBMISSION_SCHEMA = "ori-discovery-submission-v2"
DISCOVERY_REPORT_SCHEMA = "ori-discovery-report-v2"
DISCOVERY_PREFLIGHT_SCHEMA = "ori-discovery-preflight-v2"
DISCOVERY_SCORER_VERSION = "ori-discovery-scorer-v2.1"


class DiscoveryScope(StrictModel):
    """Solver-visible campaign scope without scorer-only target identities."""

    kind: Literal["campaign_union"] = "campaign_union"
    population_scope: Literal["benchmark_namespace"] = "benchmark_namespace"
    objective: Literal["attack_routes"] = "attack_routes"
    truth_source: Literal["closed_v28_route_oracles"] = "closed_v28_route_oracles"
    alternate_policy: Literal["one_objective_many_variants"] = (
        "one_objective_many_variants"
    )
    overlap_policy: Literal["strict_subpaths_are_redundant"] = (
        "strict_subpaths_are_redundant"
    )


class DiscoveryPublicArtifact(StrictModel):
    schema_version: Literal["ori-discovery-public-v2"] = DISCOVERY_PUBLIC_SCHEMA
    protocol_version: Literal["ori-open-world-discovery-v2"] = (
        DISCOVERY_PROTOCOL_VERSION
    )
    product: NonEmptyStr
    track: Track
    seed: int = Field(strict=True)
    source_public_artifact_fingerprint: Fingerprint
    source_manifest_fingerprint: Fingerprint
    graph_fingerprint: Fingerprint
    capability_profile_id: NonEmptyStr
    capability_profile_fingerprint: Fingerprint
    bounds: ExecutionBounds
    scope: DiscoveryScope
    instructions: tuple[NonEmptyStr, ...]
    answer_schema: dict[str, Any]
    compiler_fingerprint: Fingerprint
    artifact_fingerprint: Fingerprint

    @model_validator(mode="after")
    def fingerprint_matches(self) -> DiscoveryPublicArtifact:
        expected = canonical_sha256(self, exclude_fields=("artifact_fingerprint",))
        if self.artifact_fingerprint != expected:
            raise ValueError("discovery public artifact fingerprint mismatch")
        forbidden = {
            "expected",
            "oracle",
            "truth",
            "variant_id",
        }

        def visit(value: Any) -> None:
            if isinstance(value, dict):
                for key, nested in value.items():
                    if key.casefold() in forbidden:
                        raise ValueError(
                            f"scorer-only field reached discovery public artifact: {key}"
                        )
                    visit(nested)
            elif isinstance(value, (list, tuple)):
                for nested in value:
                    visit(nested)

        visit(self.model_dump(mode="json"))
        return self


class DiscoveryVariant(StrictModel):
    variant_id: Fingerprint
    edges: tuple[EdgeWitness, ...]

    @model_validator(mode="after")
    def is_non_empty_contiguous_path(self) -> DiscoveryVariant:
        if not self.edges:
            raise ValueError("discovery variants require at least one edge")
        if any(
            previous.target_id.casefold() != current.source_id.casefold()
            for previous, current in zip(self.edges, self.edges[1:], strict=False)
        ):
            raise ValueError("discovery variants must be contiguous")
        expected = canonical_sha256(self.edges)
        if self.variant_id != expected:
            raise ValueError("discovery variant fingerprint mismatch")
        return self


class DiscoveryTarget(StrictModel):
    target_id: Fingerprint
    source_id: NonEmptyStr
    destination_id: NonEmptyStr
    variants: tuple[DiscoveryVariant, ...]
    source_task_fingerprints: tuple[Fingerprint, ...]

    @model_validator(mode="after")
    def variants_share_endpoints(self) -> DiscoveryTarget:
        if not self.variants:
            raise ValueError("discovery targets require at least one variant")
        for variant in self.variants:
            if (
                variant.edges[0].source_id.casefold() != self.source_id.casefold()
                or variant.edges[-1].target_id.casefold()
                != self.destination_id.casefold()
            ):
                raise ValueError("discovery target variant endpoints differ")
        variant_ids = tuple(item.variant_id for item in self.variants)
        if variant_ids != tuple(sorted(set(variant_ids))):
            raise ValueError("discovery target variants must be sorted and unique")
        if self.source_task_fingerprints != tuple(
            sorted(set(self.source_task_fingerprints))
        ):
            raise ValueError("source task fingerprints must be sorted and unique")
        expected = canonical_sha256(
            {
                "source_id": self.source_id,
                "destination_id": self.destination_id,
                "variant_ids": variant_ids,
            }
        )
        if self.target_id != expected:
            raise ValueError("discovery target fingerprint mismatch")
        return self


class DiscoveryPrivateArtifact(StrictModel):
    schema_version: Literal["ori-discovery-oracle-v2"] = DISCOVERY_PRIVATE_SCHEMA
    protocol_version: Literal["ori-open-world-discovery-v2"] = (
        DISCOVERY_PROTOCOL_VERSION
    )
    public_artifact_fingerprint: Fingerprint
    source_oracle_artifact_fingerprint: Fingerprint
    graph_fingerprint: Fingerprint
    capability_profile: CapabilityProfile
    identity_catalog: tuple[EntityRef, ...]
    identity_catalog_fingerprint: Fingerprint
    graph_fact_registry: GraphFactRegistry
    targets: tuple[DiscoveryTarget, ...]
    target_catalog_fingerprint: Fingerprint
    artifact_fingerprint: Fingerprint

    @model_validator(mode="after")
    def fingerprints_match(self) -> DiscoveryPrivateArtifact:
        object_ids = tuple(item.object_id for item in self.identity_catalog)
        if len(object_ids) != len(set(object_ids)):
            raise ValueError("discovery identity catalog has duplicate object IDs")
        if self.identity_catalog_fingerprint != canonical_sha256(
            self.identity_catalog
        ):
            raise ValueError("discovery identity catalog fingerprint mismatch")
        target_ids = tuple(item.target_id for item in self.targets)
        if target_ids != tuple(sorted(set(target_ids))):
            raise ValueError("discovery targets must be sorted and unique")
        if self.target_catalog_fingerprint != canonical_sha256(self.targets):
            raise ValueError("discovery target catalog fingerprint mismatch")
        expected = canonical_sha256(self, exclude_fields=("artifact_fingerprint",))
        if self.artifact_fingerprint != expected:
            raise ValueError("discovery private artifact fingerprint mismatch")
        return self


class DiscoveryFinding(StrictModel):
    finding_id: NonEmptyStr
    route: tuple[EdgeWitness, ...]
    evidence_edges: tuple[EdgeWitness, ...]
    confidence: float = Field(strict=True, ge=0, le=1)

    @field_validator("route", "evidence_edges")
    @classmethod
    def edges_are_unique(cls, value: tuple[EdgeWitness, ...]) -> tuple[EdgeWitness, ...]:
        keys = [
            (
                item.source_id.casefold(),
                item.relationship.casefold(),
                item.target_id.casefold(),
                item.direction.value,
            )
            for item in value
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("finding edge collections must not contain duplicates")
        return value


class DiscoverySubmission(StrictModel):
    schema_version: Literal["ori-discovery-submission-v2"] = (
        DISCOVERY_SUBMISSION_SCHEMA
    )
    protocol_version: Literal["ori-open-world-discovery-v2"] = (
        DISCOVERY_PROTOCOL_VERSION
    )
    public_artifact_fingerprint: Fingerprint
    findings: tuple[DiscoveryFinding, ...]

    @field_validator("findings")
    @classmethod
    def finding_ids_are_unique(
        cls, value: tuple[DiscoveryFinding, ...]
    ) -> tuple[DiscoveryFinding, ...]:
        ids = [item.finding_id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("discovery finding IDs must be unique")
        return value


class DiscoveryFindingResult(StrictModel):
    finding_digest: Fingerprint
    classification: Literal[
        "matched",
        "alternate_redundant",
        "overlap_redundant",
        "false_positive",
    ]
    evidence_valid: bool
    reason: NonEmptyStr


class DiscoveryScoreReport(StrictModel):
    schema_version: Literal["ori-discovery-report-v2"] = DISCOVERY_REPORT_SCHEMA
    protocol_version: Literal["ori-open-world-discovery-v2"] = (
        DISCOVERY_PROTOCOL_VERSION
    )
    public_artifact_fingerprint: Fingerprint
    submission_fingerprint: Fingerprint
    finding_count: int = Field(strict=True, ge=0)
    matched_objectives: int = Field(strict=True, ge=0)
    objective_count: int = Field(strict=True, ge=0)
    false_positive_count: int = Field(strict=True, ge=0)
    redundant_count: int = Field(strict=True, ge=0)
    precision: float = Field(strict=True, ge=0, le=1)
    recall: float = Field(strict=True, ge=0, le=1)
    f1: float = Field(strict=True, ge=0, le=1)
    finding_results: tuple[DiscoveryFindingResult, ...]
    scorer_version: Literal["ori-discovery-scorer-v2.1"] = DISCOVERY_SCORER_VERSION
    report_fingerprint: Fingerprint

    @model_validator(mode="after")
    def fingerprint_matches(self) -> DiscoveryScoreReport:
        expected = canonical_sha256(self, exclude_fields=("report_fingerprint",))
        if self.report_fingerprint != expected:
            raise ValueError("discovery score report fingerprint mismatch")
        return self


class DiscoveryPreflightReport(StrictModel):
    schema_version: Literal["ori-discovery-preflight-v2"] = (
        DISCOVERY_PREFLIGHT_SCHEMA
    )
    public_artifact_fingerprint: Fingerprint
    private_artifact_fingerprint: Fingerprint
    target_count: int = Field(strict=True, gt=0)
    variant_count: int = Field(strict=True, gt=0)
    checks: tuple[NonEmptyStr, ...]
    report_fingerprint: Fingerprint

    @model_validator(mode="after")
    def fingerprint_matches(self) -> DiscoveryPreflightReport:
        expected = canonical_sha256(self, exclude_fields=("report_fingerprint",))
        if self.report_fingerprint != expected:
            raise ValueError("discovery preflight report fingerprint mismatch")
        return self
