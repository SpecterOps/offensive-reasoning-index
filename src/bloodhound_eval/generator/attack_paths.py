"""Attack path coordinator: runs all templates and collects planted paths."""

from __future__ import annotations

from .graph import ADGraph, PlantedPath
from .templates.tier1 import plant_admin_to, plant_has_session


def plant_all_paths(graph: ADGraph) -> list[PlantedPath]:
    """Plant all available attack path templates and return the planted paths."""
    planted: list[PlantedPath] = []

    # Tier 1
    planted.append(plant_admin_to(graph))
    planted.append(plant_has_session(graph))

    return planted
