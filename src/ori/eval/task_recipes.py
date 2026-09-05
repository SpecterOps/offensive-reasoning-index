"""Typed, fail-closed authoring registry for generated ORI task recipes."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

RecipeTrack = Literal["direct", "mcp"]
RecipeClaimKind = Literal["route", "absence", "set", "count", "decision"]
RecipeSemantics = Literal["direct", "transitive"]


class TaskRecipeRegistryError(ValueError):
    """Raised when task authoring coverage is incomplete or contradictory."""


@dataclass(frozen=True, slots=True)
class TaskVariantRecipe:
    """One logical task recipe and its single legacy task-view emission."""

    recipe_id: str
    template_id: str
    legacy_task_id: str
    supported_tracks: tuple[RecipeTrack, ...]
    claim_kind: RecipeClaimKind
    family: str
    tier: int
    semantics: RecipeSemantics
    concentration_key: str
    gradable: bool = True
    track_exclusions: tuple[TrackExclusion, ...] = ()
    emitted_variants: tuple[str, ...] = ("legacy-v1",)
    requires_planted_template: bool = True

    def resolve_task_ids(self, manifest: Mapping[str, Any]) -> tuple[str, ...]:
        """Resolve this recipe for one seeded manifest without changing semantics."""

        if self.requires_planted_template:
            template_ids = {
                str(path.get("template_id") or "")
                for path in manifest.get("planted_paths") or ()
                if isinstance(path, Mapping)
            }
            if self.template_id not in template_ids:
                return ()
        return (self.legacy_task_id,) if self.emitted_variants else ()


@dataclass(frozen=True, slots=True)
class TrackExclusion:
    exclusion_id: str
    track: RecipeTrack
    reason: str


@dataclass(frozen=True, slots=True)
class TemplateCoverageExclusion:
    """An explicit support-only decision for a planted template."""

    exclusion_id: str
    template_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class TaskRecipeRegistry:
    recipes: tuple[TaskVariantRecipe, ...]
    template_exclusions: tuple[TemplateCoverageExclusion, ...] = ()

    def __post_init__(self) -> None:
        _validate_registry(self)

    def recipes_for_track(
        self,
        manifest: Mapping[str, Any],
        track: RecipeTrack,
    ) -> tuple[TaskVariantRecipe, ...]:
        return tuple(
            recipe
            for recipe in self.recipes
            if track in recipe.supported_tracks and recipe.resolve_task_ids(manifest)
        )

    def recipe_for_task(
        self,
        manifest: Mapping[str, Any],
        track: RecipeTrack,
        legacy_task_id: str,
    ) -> TaskVariantRecipe:
        matches = [
            recipe
            for recipe in self.recipes_for_track(manifest, track)
            if legacy_task_id in recipe.resolve_task_ids(manifest)
        ]
        if len(matches) != 1:
            raise TaskRecipeRegistryError(
                f"generated task {legacy_task_id!r} has {len(matches)} registry entries "
                f"for {track}"
            )
        return matches[0]


def _nonempty(value: str, label: str) -> None:
    if not value.strip():
        raise TaskRecipeRegistryError(f"{label} must be non-empty")


def _validate_registry(registry: TaskRecipeRegistry) -> None:
    recipe_ids = [recipe.recipe_id for recipe in registry.recipes]
    duplicates = sorted(key for key, count in Counter(recipe_ids).items() if count > 1)
    if duplicates:
        raise TaskRecipeRegistryError(f"duplicate recipe IDs: {duplicates}")

    emissions: dict[tuple[RecipeTrack, str], str] = {}
    for recipe in registry.recipes:
        for label, value in (
            ("recipe ID", recipe.recipe_id),
            ("template ID", recipe.template_id),
            ("legacy task ID", recipe.legacy_task_id),
            ("family", recipe.family),
            ("concentration key", recipe.concentration_key),
        ):
            _nonempty(value, label)
        if not recipe.emitted_variants:
            raise TaskRecipeRegistryError(
                f"recipe {recipe.recipe_id!r} emits zero task variants"
            )
        if not recipe.gradable:
            raise TaskRecipeRegistryError(
                f"recipe {recipe.recipe_id!r} must be gradable; use a template exclusion "
                "for support-only coverage"
            )
        if len(recipe.supported_tracks) != len(set(recipe.supported_tracks)):
            raise TaskRecipeRegistryError(
                f"recipe {recipe.recipe_id!r} repeats a supported track"
            )
        exclusions = {item.track: item for item in recipe.track_exclusions}
        if len(exclusions) != len(recipe.track_exclusions):
            raise TaskRecipeRegistryError(
                f"recipe {recipe.recipe_id!r} repeats a track exclusion"
            )
        for track in ("direct", "mcp"):
            supported = track in recipe.supported_tracks
            excluded = track in exclusions
            if supported == excluded:
                raise TaskRecipeRegistryError(
                    f"recipe {recipe.recipe_id!r} must support or explicitly exclude {track}"
                )
        for exclusion in recipe.track_exclusions:
            _nonempty(exclusion.exclusion_id, "track exclusion ID")
            _nonempty(exclusion.reason, "track exclusion reason")
        for track in recipe.supported_tracks:
            key = (track, recipe.legacy_task_id)
            if key in emissions:
                raise TaskRecipeRegistryError(
                    f"conflicting recipe emissions for {track} task "
                    f"{recipe.legacy_task_id!r}: {emissions[key]!r} and "
                    f"{recipe.recipe_id!r}"
                )
            emissions[key] = recipe.recipe_id

    exclusion_ids = [item.exclusion_id for item in registry.template_exclusions]
    if len(exclusion_ids) != len(set(exclusion_ids)):
        raise TaskRecipeRegistryError("duplicate template coverage exclusion IDs")
    excluded_templates: set[str] = set()
    registered_templates = {
        recipe.template_id for recipe in registry.recipes if recipe.requires_planted_template
    }
    for exclusion in registry.template_exclusions:
        _nonempty(exclusion.exclusion_id, "template coverage exclusion ID")
        _nonempty(exclusion.template_id, "excluded template ID")
        _nonempty(exclusion.reason, "template coverage exclusion reason")
        if exclusion.template_id in registered_templates:
            raise TaskRecipeRegistryError(
                f"template {exclusion.template_id!r} is both registered and excluded"
            )
        if exclusion.template_id in excluded_templates:
            raise TaskRecipeRegistryError(
                f"template {exclusion.template_id!r} has conflicting exclusions"
            )
        excluded_templates.add(exclusion.template_id)


def validate_manifest_recipe_coverage(
    manifest: Mapping[str, Any],
    registry: TaskRecipeRegistry,
) -> None:
    """Require an explicit registry recipe or support-only decision per planted path."""

    planted: list[str] = []
    for path in manifest.get("planted_paths") or ():
        if not isinstance(path, Mapping):
            raise TaskRecipeRegistryError("planted path must be a mapping")
        template_id = str(path.get("template_id") or "").strip()
        if not template_id:
            raise TaskRecipeRegistryError("planted path has no template_id")
        planted.append(template_id)
    duplicate_templates = sorted(
        template_id for template_id, count in Counter(planted).items() if count > 1
    )
    if duplicate_templates:
        raise TaskRecipeRegistryError(
            f"duplicate planted template IDs: {duplicate_templates}"
        )

    registered = {
        recipe.template_id for recipe in registry.recipes if recipe.requires_planted_template
    }
    excluded = {item.template_id for item in registry.template_exclusions}
    stale_exclusions = sorted(excluded - set(planted))
    if stale_exclusions:
        raise TaskRecipeRegistryError(
            f"template coverage exclusions do not match planted templates: {stale_exclusions}"
        )
    unknown = sorted(set(planted) - registered - excluded)
    if unknown:
        raise TaskRecipeRegistryError(
            f"planted templates lack an authoring decision: {unknown}"
        )


def validate_generated_recipe_coverage(
    manifest: Mapping[str, Any],
    registry: TaskRecipeRegistry,
    track: RecipeTrack,
    generated_tasks: Sequence[Any],
    compiled_tasks: Sequence[Any] | None = None,
) -> None:
    """Reconcile manifest decisions, registry emissions, V1 tasks, and V2 candidates."""

    validate_manifest_recipe_coverage(manifest, registry)
    expected = {
        task_id: recipe
        for recipe in registry.recipes_for_track(manifest, track)
        for task_id in recipe.resolve_task_ids(manifest)
    }
    actual = {str(task.id): task for task in generated_tasks}
    if set(actual) != set(expected):
        raise TaskRecipeRegistryError(
            "generated task coverage differs from the recipe registry: "
            f"missing={sorted(set(expected) - set(actual))} "
            f"unknown={sorted(set(actual) - set(expected))}"
        )
    for task_id, task in actual.items():
        recipe = expected[task_id]
        metadata = getattr(task, "metadata", {})
        if metadata.get("logical_recipe_id") != recipe.recipe_id:
            raise TaskRecipeRegistryError(
                f"generated task {task_id!r} is not bound to recipe {recipe.recipe_id!r}"
            )
        if str(getattr(task, "template_id", "")) != recipe.template_id:
            raise TaskRecipeRegistryError(
                f"generated task {task_id!r} disagrees with its template recipe"
            )
        if int(getattr(task, "tier", -1)) != recipe.tier:
            raise TaskRecipeRegistryError(
                f"generated task {task_id!r} disagrees with its recipe tier"
            )
        generated_claim_kind = metadata.get(
            "recipe_claim_kind",
            _claim_kind(str(getattr(task, "grade_mode", ""))),
        )
        if generated_claim_kind != recipe.claim_kind:
            raise TaskRecipeRegistryError(
                f"generated task {task_id!r} disagrees with its recipe claim kind"
            )

    if compiled_tasks is None:
        return
    compiled_by_legacy = Counter(
        str(task.migration.legacy_task_id) for task in compiled_tasks
    )
    missing_candidates = sorted(task_id for task_id in expected if not compiled_by_legacy[task_id])
    unknown_candidates = sorted(set(compiled_by_legacy) - set(expected))
    if missing_candidates or unknown_candidates:
        raise TaskRecipeRegistryError(
            "compiled candidate coverage differs from the recipe registry: "
            f"missing={missing_candidates} unknown={unknown_candidates}"
        )


def _claim_kind(grade_mode: str) -> RecipeClaimKind:
    mapping: dict[str, RecipeClaimKind] = {
        "path_exists": "route",
        "no_path": "absence",
        "node_set": "set",
        "row_count": "count",
    }
    try:
        return mapping[grade_mode]
    except KeyError as exc:
        raise TaskRecipeRegistryError(
            f"unsupported recipe grade mode: {grade_mode!r}"
        ) from exc


def claim_kind_for_grade_mode(grade_mode: str) -> RecipeClaimKind:
    """Expose the canonical V1 grade-mode mapping to registry authors."""

    return _claim_kind(grade_mode)
