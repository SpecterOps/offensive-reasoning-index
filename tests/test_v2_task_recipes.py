from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from ori.eval.tasks import TASK_RECIPE_REGISTRY, generate_mcp_tasks, generate_tasks
from ori.eval.v2.task_recipes import (
    TaskRecipeRegistry,
    TaskRecipeRegistryError,
    TaskVariantRecipe,
    TemplateCoverageExclusion,
    TrackExclusion,
    validate_generated_recipe_coverage,
    validate_manifest_recipe_coverage,
)

MANIFEST_PATH = Path("datasets/benchmarks/complex-v1-seed-4401_manifest.json")


def _recipe(**overrides: object) -> TaskVariantRecipe:
    values: dict[str, object] = {
        "recipe_id": "recipe-1",
        "template_id": "template-1",
        "legacy_task_id": "task-1",
        "supported_tracks": ("direct", "mcp"),
        "claim_kind": "route",
        "family": "family-1",
        "tier": 1,
        "semantics": "direct",
        "concentration_key": "family:1",
    }
    values.update(overrides)
    return TaskVariantRecipe(**values)  # type: ignore[arg-type]


def test_registry_rejects_duplicate_recipe_and_task_emission_ids() -> None:
    recipe = _recipe()
    with pytest.raises(TaskRecipeRegistryError, match="duplicate recipe IDs"):
        TaskRecipeRegistry((recipe, recipe))

    conflicting = replace(recipe, recipe_id="recipe-2")
    with pytest.raises(TaskRecipeRegistryError, match="conflicting recipe emissions"):
        TaskRecipeRegistry((recipe, conflicting))


def test_registry_rejects_zero_emission_and_missing_track_decisions() -> None:
    with pytest.raises(TaskRecipeRegistryError, match="zero task variants"):
        TaskRecipeRegistry((_recipe(emitted_variants=()),))

    with pytest.raises(TaskRecipeRegistryError, match="support or explicitly exclude mcp"):
        TaskRecipeRegistry((_recipe(supported_tracks=("direct",)),))

    exclusion = TrackExclusion(
        exclusion_id="exclude-mcp:recipe-1",
        track="mcp",
        reason="",
    )
    with pytest.raises(TaskRecipeRegistryError, match="track exclusion reason"):
        TaskRecipeRegistry(
            (_recipe(supported_tracks=("direct",), track_exclusions=(exclusion,)),)
        )


@pytest.mark.parametrize("template_id", ["custom_path", "t6_unregistered_path"])
def test_unknown_planted_template_fails_closed(template_id: str) -> None:
    with pytest.raises(TaskRecipeRegistryError, match="lack an authoring decision"):
        validate_manifest_recipe_coverage(
            {"planted_paths": [{"template_id": template_id}]},
            TASK_RECIPE_REGISTRY,
        )


def test_support_only_template_requires_stable_exclusion_and_emits_no_task() -> None:
    registry = TaskRecipeRegistry(
        (),
        (
            TemplateCoverageExclusion(
                exclusion_id="support-only:template-1",
                template_id="template-1",
                reason="graph support fixture has no independently gradable claim",
            ),
        ),
    )
    manifest = {"planted_paths": [{"template_id": "template-1"}]}
    validate_manifest_recipe_coverage(manifest, registry)
    assert registry.recipes_for_track(manifest, "direct") == ()

    with pytest.raises(TaskRecipeRegistryError, match="do not match planted templates"):
        validate_manifest_recipe_coverage({"planted_paths": []}, registry)

    with pytest.raises(TaskRecipeRegistryError, match="both registered and excluded"):
        TaskRecipeRegistry(
            (_recipe(),),
            (
                TemplateCoverageExclusion(
                    exclusion_id="support-only:template-1",
                    template_id="template-1",
                    reason="conflicts with a gradable recipe",
                ),
            ),
        )


def test_current_complex_manifest_has_deterministic_complete_recipe_coverage() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text())
    planted_templates = {
        str(path["template_id"]) for path in manifest["planted_paths"]
    }
    validate_manifest_recipe_coverage(manifest, TASK_RECIPE_REGISTRY)

    registered_templates = {
        recipe.template_id
        for recipe in TASK_RECIPE_REGISTRY.recipes
        if recipe.requires_planted_template
    }
    assert planted_templates == registered_templates
    assert len(planted_templates) == 30

    direct = generate_tasks(manifest)
    mcp = generate_mcp_tasks(manifest)
    validate_generated_recipe_coverage(
        manifest, TASK_RECIPE_REGISTRY, "direct", direct
    )
    validate_generated_recipe_coverage(manifest, TASK_RECIPE_REGISTRY, "mcp", mcp)
    assert len(direct) == 42
    assert len(mcp) == 62
    assert len({task.metadata["logical_recipe_id"] for task in direct}) == 42
    assert len({task.metadata["logical_recipe_id"] for task in mcp}) == 62
