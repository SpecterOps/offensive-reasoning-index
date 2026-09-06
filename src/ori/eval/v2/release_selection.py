"""Deterministic OAIC task selection; certification admission remains mandatory."""

from __future__ import annotations

from collections import Counter
from typing import Literal

from pydantic import Field, model_validator

from . import compiler
from .comparator import COMPARATOR_FINGERPRINT
from .fingerprint import canonical_sha256
from .oaic_recipes import OAICRecipeMetadata, validate_oaic_recipe_metadata
from .protocol import PublicV2Artifact
from .schema import CatalogRelease, Fingerprint, StrictModel, Track

PRODUCT = "oaic-2026-v1"
SELECTOR_VERSION = "ori-oaic-selector-v1"
QUOTAS = (
    ("set", 32, 10),
    ("count", 30, 8),
    ("route", 24, 20),
    ("decision", 7, 6),
    ("absence", 7, 6),
)
ClaimKind = Literal["set", "count", "route", "decision", "absence"]


class SelectionQuota(StrictModel):
    claim_kind: ClaimKind
    minimum_main: int = Field(strict=True, gt=0)
    selected: int = Field(strict=True, gt=0)
    diagnostics: Literal[2] = 2


class ReleaseSelectionSpec(StrictModel):
    selector_version: Literal["ori-oaic-selector-v1"] = SELECTOR_VERSION
    product: Literal["oaic-2026-v1"] = PRODUCT
    quotas: tuple[SelectionQuota, ...]

    @model_validator(mode="after")
    def fixed_policy(self) -> ReleaseSelectionSpec:
        if tuple((q.claim_kind, q.minimum_main, q.selected) for q in self.quotas) != QUOTAS:
            raise ValueError("OAIC selection quotas do not match the approved policy")
        return self

    @property
    def spec_fingerprint(self) -> str:
        return canonical_sha256(self)


OAIC_SELECTION_SPEC = ReleaseSelectionSpec(
    quotas=tuple(
        SelectionQuota(claim_kind=kind, minimum_main=minimum, selected=selected)
        for kind, minimum, selected in QUOTAS
    )
)


class SelectedTaskIdentity(StrictModel):
    task_id: str
    recipe_id: str
    variant_id: str
    claim_kind: ClaimKind
    task_fingerprint: Fingerprint
    public_semantic_fingerprint: Fingerprint


class SelectedReleaseReceipt(StrictModel):
    schema_version: Literal["ori-selected-release-v1"] = "ori-selected-release-v1"
    product: Literal["oaic-2026-v1"] = PRODUCT
    selector_version: Literal["ori-oaic-selector-v1"] = SELECTOR_VERSION
    spec_fingerprint: Fingerprint
    seed: int = Field(strict=True)
    track: Track
    source_manifest_fingerprint: Fingerprint
    source_archive_sha256: Fingerprint
    graph_fingerprint: Fingerprint
    compiler_fingerprint: Fingerprint
    public_artifact_fingerprint: Fingerprint
    candidate_release_fingerprint: Fingerprint
    metadata_fingerprint: Fingerprint
    entries: tuple[SelectedTaskIdentity, ...]
    selection_fingerprint: Fingerprint

    @property
    def selected_task_ids(self) -> tuple[str, ...]:
        return tuple(entry.task_id for entry in self.entries)

    @model_validator(mode="after")
    def exact_selection_shape(self) -> SelectedReleaseReceipt:
        if self.spec_fingerprint != OAIC_SELECTION_SPEC.spec_fingerprint:
            raise ValueError("unknown OAIC selection policy")
        if Counter(entry.claim_kind for entry in self.entries) != {
            kind: selected for kind, _, selected in QUOTAS
        }:
            raise ValueError("selected release must contain the exact 50-task quotas")
        for values in (
            self.selected_task_ids,
            tuple(entry.public_semantic_fingerprint for entry in self.entries),
            tuple((entry.recipe_id, entry.variant_id) for entry in self.entries),
        ):
            if len(values) != len(set(values)):
                raise ValueError("selected release contains duplicate task contracts")
        if self.selection_fingerprint != canonical_sha256(
            self, exclude_fields=("selection_fingerprint",)
        ):
            raise ValueError("selected release fingerprint mismatch")
        return self


class PairedSelectedRelease(StrictModel):
    schema_version: Literal["ori-paired-selected-release-v1"] = "ori-paired-selected-release-v1"
    product: Literal["oaic-2026-v1"] = PRODUCT
    seed: int = Field(strict=True)
    source_manifest_fingerprint: Fingerprint
    source_archive_sha256: Fingerprint
    graph_fingerprint: Fingerprint
    compiler_fingerprint: Fingerprint
    spec_fingerprint: Fingerprint
    direct_selection_fingerprint: Fingerprint
    mcp_selection_fingerprint: Fingerprint
    total_tasks: Literal[100] = 100
    release_fingerprint: Fingerprint

    @model_validator(mode="after")
    def exact_pair(self) -> PairedSelectedRelease:
        if self.spec_fingerprint != OAIC_SELECTION_SPEC.spec_fingerprint:
            raise ValueError("unknown paired release selection policy")
        if self.release_fingerprint != canonical_sha256(
            self, exclude_fields=("release_fingerprint",)
        ):
            raise ValueError("paired selected release fingerprint mismatch")
        return self


def select_candidate_track(
    *,
    public: PublicV2Artifact,
    candidates: CatalogRelease,
    metadata: OAICRecipeMetadata,
    source_archive_sha256: str,
) -> SelectedReleaseReceipt:
    """Select after full candidate/live/oracle admission, never in place of it.

    This pure function also rederives recipe bindings and validates the complete
    semantic pool. Its self-hash alone is not proof of certification; CLI and
    campaign callers must retain the ordinary full-catalog certification gate.
    """
    if (
        public.compiler_fingerprint != compiler.compiler_fingerprint()
        or public.comparator_fingerprint != COMPARATOR_FINGERPRINT
    ):
        raise ValueError("OAIC selection requires current compiler and comparator certification")
    validate_oaic_recipe_metadata(metadata, public)
    if public.product != PRODUCT or candidates.product != PRODUCT:
        raise ValueError("OAIC selection requires the explicit OAIC product")
    if (
        candidates.graph_fingerprint != public.graph_fingerprint
        or candidates.compiler_fingerprint != public.compiler_fingerprint
        or candidates.comparator_fingerprint != public.comparator_fingerprint
    ):
        raise ValueError("selection candidate/public source mismatch")
    by_id = {entry.task_id: entry for entry in metadata.entries}
    covered = {task_id for entry in candidates.entries for task_id in entry.equivalent_task_ids}
    if covered != set(by_id):
        raise ValueError("selection requires the complete certified recipe pool")
    main = []
    diagnostic_semantics: set[str] = set()
    diagnostic_recipes: set[str] = set()
    diagnostic_counts: Counter[str] = Counter()
    for entry in candidates.entries:
        if entry.track is not public.track:
            raise ValueError("selection candidate track mismatch")
        recipe = by_id[entry.task_id]
        if (
            entry.task_fingerprint != recipe.task_fingerprint
            or entry.public_semantic_fingerprint != recipe.public_semantic_fingerprint
            or entry.claim_kind != recipe.claim_kind
        ):
            raise ValueError("selection candidate recipe binding mismatch")
        equivalent = [by_id[task_id] for task_id in entry.equivalent_task_ids]
        if any(item.eligibility != recipe.eligibility for item in equivalent):
            raise ValueError("diagnostic and main recipes share public semantics")
        if recipe.eligibility == "diagnostic":
            diagnostic_counts[recipe.claim_kind] += 1
            diagnostic_semantics.add(recipe.public_semantic_fingerprint)
            diagnostic_recipes.update(item.recipe_id for item in equivalent)
        else:
            main.append(recipe)
    selected: list[SelectedTaskIdentity] = []
    for quota in OAIC_SELECTION_SPEC.quotas:
        pool = [entry for entry in main if entry.claim_kind == quota.claim_kind]
        if (
            len(pool) < quota.minimum_main
            or diagnostic_counts[quota.claim_kind] < quota.diagnostics
        ):
            raise ValueError(f"insufficient certified {quota.claim_kind} selection quota")
        if any(
            entry.recipe_id in diagnostic_recipes
            or entry.public_semantic_fingerprint in diagnostic_semantics
            for entry in pool
        ):
            raise ValueError("diagnostic identity leaked into main selection")
        pool.sort(
            key=lambda entry: (
                canonical_sha256(
                    (
                        SELECTOR_VERSION,
                        PRODUCT,
                        public.seed,
                        public.track.value,
                        entry.recipe_id,
                        entry.variant_id,
                    )
                ),
                entry.recipe_id,
                entry.variant_id,
                entry.task_id,
            )
        )
        selected.extend(
            SelectedTaskIdentity(
                task_id=entry.task_id,
                recipe_id=entry.recipe_id,
                variant_id=entry.variant_id,
                claim_kind=entry.claim_kind,
                task_fingerprint=entry.task_fingerprint,
                public_semantic_fingerprint=entry.public_semantic_fingerprint,
            )
            for entry in pool[: quota.selected]
        )
    payload = dict(
        schema_version="ori-selected-release-v1",
        product=PRODUCT,
        selector_version=SELECTOR_VERSION,
        spec_fingerprint=OAIC_SELECTION_SPEC.spec_fingerprint,
        seed=public.seed,
        track=public.track,
        source_manifest_fingerprint=public.source_manifest_fingerprint,
        source_archive_sha256=source_archive_sha256,
        graph_fingerprint=public.graph_fingerprint,
        compiler_fingerprint=public.compiler_fingerprint,
        public_artifact_fingerprint=public.artifact_fingerprint,
        candidate_release_fingerprint=candidates.release_fingerprint,
        metadata_fingerprint=metadata.metadata_fingerprint,
        entries=tuple(selected),
    )
    return SelectedReleaseReceipt.model_validate(
        {
            **payload,
            "selection_fingerprint": canonical_sha256(payload),
        }
    )


def pair_selected_tracks(
    direct: SelectedReleaseReceipt,
    mcp: SelectedReleaseReceipt,
) -> PairedSelectedRelease:
    """Bind two matched 50-task tracks; never combine unmatched seeds/graphs."""
    if direct.track is not Track.DIRECT or mcp.track is not Track.MCP:
        raise ValueError("paired release requires one Direct and one MCP selection")
    shared = (
        "product",
        "seed",
        "source_manifest_fingerprint",
        "source_archive_sha256",
        "graph_fingerprint",
        "compiler_fingerprint",
        "spec_fingerprint",
    )
    if any(getattr(direct, field) != getattr(mcp, field) for field in shared):
        raise ValueError("paired selection product, seed, source or graph mismatch")
    payload = {
        "schema_version": "ori-paired-selected-release-v1",
        **{field: getattr(direct, field) for field in shared},
        "direct_selection_fingerprint": direct.selection_fingerprint,
        "mcp_selection_fingerprint": mcp.selection_fingerprint,
        "total_tasks": 100,
    }
    return PairedSelectedRelease.model_validate(
        {
            **payload,
            "release_fingerprint": canonical_sha256(payload),
        }
    )
