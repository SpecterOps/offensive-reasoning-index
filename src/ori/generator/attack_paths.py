"""Attack path coordinator: runs all templates and collects planted paths."""

from __future__ import annotations

from .graph import ADGraph, PlantedPath
from .templates.tier1 import plant_admin_to, plant_group_membership, plant_has_session
from .templates.tier2 import plant_acl_chain, plant_kerberoast_chain, plant_nested_groups
from .templates.tier3 import plant_constrained_delegation, plant_unconstrained_delegation


def plant_all_paths(graph: ADGraph) -> list[PlantedPath]:
    """Plant all available attack path templates and return the planted paths."""
    planted: list[PlantedPath] = []

    # Tier 1 — single-hop, ~90% expected pass rate
    planted.append(plant_admin_to(graph))
    planted.append(plant_has_session(graph))
    planted.append(plant_group_membership(graph))

    # Tier 2 — multi-hop, ~60% expected pass rate
    planted.append(plant_kerberoast_chain(graph))
    planted.append(plant_acl_chain(graph))
    planted.append(plant_nested_groups(graph))

    # Tier 3 — delegation abuse, ~30% expected pass rate
    planted.append(plant_unconstrained_delegation(graph))
    planted.append(plant_constrained_delegation(graph))

    return planted
