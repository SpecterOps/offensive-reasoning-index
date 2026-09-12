"""Deterministic, non-ranking OAIC diagnostic-canary selection.

The official selected release deliberately excludes the ten diagnostic-only
recipes.  This module keeps that boundary explicit: a five-task canary is
derived from the *complete certified MCP catalog* and is bound to the original
paired 50/50 release, but it can never validate as an official release.
"""

from __future__ import annotations

from collections import Counter
from typing import Literal

from pydantic import Field, model_validator

from .fingerprint import canonical_sha256
from .oaic_recipes import OAICRecipeMetadata, validate_oaic_recipe_metadata
from .protocol import PublicV2Artifact
from .release_selection import (
    PRODUCT,
    PairedSelectedRelease,
    SelectedReleaseReceipt,
    select_candidate_track,
)
from .schema import CatalogRelease, Fingerprint, StrictModel, Track

CANARY_SUITE = "native-five-kind-v1"
CANARY_SELECTOR_VERSION = "ori-oaic-diagnostic-selector-v1"
ClaimKind = Literal["set", "count", "route", "decision", "absence"]
CANARY_KINDS: tuple[ClaimKind, ...] = ("set", "count", "route", "decision", "absence")


class DiagnosticTaskIdentity(StrictModel):
    """One fixed diagnostic source contract, including its public identity."""

    task_id: str
    recipe_id: str
    variant_id: str
    claim_kind: ClaimKind
    task_fingerprint: Fingerprint
    public_semantic_fingerprint: Fingerprint


class DiagnosticSelectionReceiptV1(StrictModel):
    """A five-task diagnostic-only selection receipt.

    This intentionally has a different schema from ``SelectedReleaseReceipt``.
    Treating it as a selected release must fail before a campaign starts.
    """

    schema_version: Literal["ori-diagnostic-selection-v1"] = "ori-diagnostic-selection-v1"
    purpose: Literal["diagnostic_canary"] = "diagnostic_canary"
    ranking_eligible: Literal[False] = False
    suite: Literal["native-five-kind-v1"] = CANARY_SUITE
    selector_version: Literal["ori-oaic-diagnostic-selector-v1"] = CANARY_SELECTOR_VERSION
    product: Literal["oaic-2026-v1"] = PRODUCT
    seed: int = Field(strict=True)
    track: Literal[Track.MCP] = Track.MCP
    source_manifest_fingerprint: Fingerprint
    source_archive_sha256: Fingerprint
    graph_fingerprint: Fingerprint
    compiler_fingerprint: Fingerprint
    public_artifact_fingerprint: Fingerprint
    candidate_release_fingerprint: Fingerprint
    metadata_fingerprint: Fingerprint
    paired_release_fingerprint: Fingerprint
    original_mcp_selection_fingerprint: Fingerprint
    entries: tuple[DiagnosticTaskIdentity, ...]
    selection_fingerprint: Fingerprint

    @property
    def selected_task_ids(self) -> tuple[str, ...]:
        return tuple(entry.task_id for entry in self.entries)

    @model_validator(mode="after")
    def exact_canary_shape(self) -> DiagnosticSelectionReceiptV1:
        if self.ranking_eligible:
            raise ValueError("diagnostic canary must never be ranking eligible")
        if Counter(entry.claim_kind for entry in self.entries) != {
            kind: 1 for kind in CANARY_KINDS
        }:
            raise ValueError("diagnostic canary must contain exactly one task of each kind")
        for values in (
            self.selected_task_ids,
            tuple(entry.public_semantic_fingerprint for entry in self.entries),
            tuple((entry.recipe_id, entry.variant_id) for entry in self.entries),
        ):
            if len(values) != len(set(values)):
                raise ValueError("diagnostic canary contains duplicate task contracts")
        if self.selection_fingerprint != canonical_sha256(
            self, exclude_fields=("selection_fingerprint",)
        ):
            raise ValueError("diagnostic selection fingerprint mismatch")
        return self


def select_diagnostic_canary(
    *,
    public: PublicV2Artifact,
    candidates: CatalogRelease,
    metadata: OAICRecipeMetadata,
    source_archive_sha256: str,
    paired_release: PairedSelectedRelease,
    mcp_selection: SelectedReleaseReceipt,
) -> DiagnosticSelectionReceiptV1:
    """Select one deterministic diagnostic recipe per public claim kind.

    The ordinary selected-release validator is deliberately run first.  This
    proves the source catalog still has the whole certified main/diagnostic
    pool and binds the canary to the same paired release; it does not reuse the
    official receipt for a five-task schedule.
    """

    if public.track is not Track.MCP or any(
        entry.track is not Track.MCP for entry in candidates.entries
    ):
        raise ValueError("native diagnostic canary supports the MCP track only")
    official = select_candidate_track(
        public=public,
        candidates=candidates,
        metadata=metadata,
        source_archive_sha256=source_archive_sha256,
    )
    if official != mcp_selection:
        raise ValueError("diagnostic canary requires the exact certified MCP selected release")
    if (
        paired_release.mcp_selection_fingerprint != mcp_selection.selection_fingerprint
        or paired_release.product != PRODUCT
        or paired_release.seed != public.seed
        or paired_release.graph_fingerprint != public.graph_fingerprint
        or paired_release.source_manifest_fingerprint != public.source_manifest_fingerprint
        or paired_release.compiler_fingerprint != public.compiler_fingerprint
    ):
        raise ValueError("diagnostic canary paired release does not match certified MCP source")
    validate_oaic_recipe_metadata(metadata, public)
    if (
        metadata.seed != public.seed
        or metadata.track is not Track.MCP
        or metadata.graph_fingerprint != public.graph_fingerprint
        or metadata.source_manifest_fingerprint != public.source_manifest_fingerprint
        or metadata.compiler_fingerprint != public.compiler_fingerprint
    ):
        raise ValueError("diagnostic canary metadata does not match certified MCP source")

    by_id = {entry.task_id: entry for entry in metadata.entries}
    candidate_by_id = {entry.task_id: entry for entry in candidates.entries}
    covered = {task_id for entry in candidates.entries for task_id in entry.equivalent_task_ids}
    if covered != set(by_id) or set(candidate_by_id) != set(by_id):
        raise ValueError("diagnostic canary requires the complete certified recipe pool")
    diagnostics = [entry for entry in metadata.entries if entry.eligibility == "diagnostic"]
    if Counter(entry.claim_kind for entry in diagnostics) != {kind: 2 for kind in CANARY_KINDS}:
        raise ValueError("diagnostic recipe pool must contain two tasks of each kind")

    selected: list[DiagnosticTaskIdentity] = []
    for kind in CANARY_KINDS:
        pool = [entry for entry in diagnostics if entry.claim_kind == kind]
        pool.sort(
            key=lambda entry: (
                canonical_sha256(
                    (
                        CANARY_SELECTOR_VERSION,
                        PRODUCT,
                        public.seed,
                        Track.MCP.value,
                        entry.recipe_id,
                        entry.variant_id,
                    )
                ),
                entry.recipe_id,
                entry.variant_id,
                entry.task_id,
            )
        )
        selected_metadata = pool[0]
        candidate = candidate_by_id[selected_metadata.task_id]
        if (
            candidate.track is not Track.MCP
            or candidate.task_fingerprint != selected_metadata.task_fingerprint
            or candidate.public_semantic_fingerprint
            != selected_metadata.public_semantic_fingerprint
            or candidate.claim_kind != selected_metadata.claim_kind
        ):
            raise ValueError("diagnostic candidate recipe binding mismatch")
        selected.append(
            DiagnosticTaskIdentity(
                task_id=selected_metadata.task_id,
                recipe_id=selected_metadata.recipe_id,
                variant_id=selected_metadata.variant_id,
                claim_kind=selected_metadata.claim_kind,
                task_fingerprint=selected_metadata.task_fingerprint,
                public_semantic_fingerprint=selected_metadata.public_semantic_fingerprint,
            )
        )

    payload = {
        "schema_version": "ori-diagnostic-selection-v1",
        "purpose": "diagnostic_canary",
        "ranking_eligible": False,
        "suite": CANARY_SUITE,
        "selector_version": CANARY_SELECTOR_VERSION,
        "product": PRODUCT,
        "seed": public.seed,
        "track": Track.MCP,
        "source_manifest_fingerprint": public.source_manifest_fingerprint,
        "source_archive_sha256": source_archive_sha256,
        "graph_fingerprint": public.graph_fingerprint,
        "compiler_fingerprint": public.compiler_fingerprint,
        "public_artifact_fingerprint": public.artifact_fingerprint,
        "candidate_release_fingerprint": candidates.release_fingerprint,
        "metadata_fingerprint": metadata.metadata_fingerprint,
        "paired_release_fingerprint": paired_release.release_fingerprint,
        "original_mcp_selection_fingerprint": mcp_selection.selection_fingerprint,
        "entries": tuple(selected),
    }
    return DiagnosticSelectionReceiptV1.model_validate(
        {
            **payload,
            "selection_fingerprint": canonical_sha256(payload),
        }
    )
