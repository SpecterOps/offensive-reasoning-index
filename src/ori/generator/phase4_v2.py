"""Phase 4B/v2 realistic forest benchmark generator.

The v2 generator builds a deterministic parent/child forest envelope around the
existing Phase 4 graph primitives, then exposes task-generation inputs used by
the official medium corpus.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .phase4 import build_phase4_v1_graph

PHASE4_V2_TEMPLATE_VERSION = "phase4b_v2.0"

_PROFILE_SIZES = {
    "small": {"parent": (20, 8, 4), "child": (14, 6, 3)},
    "medium": {"parent": (80, 32, 16), "child": (56, 24, 12)},
}


@dataclass(frozen=True)
class Phase4V2Forest:
    """Deterministic parent/child forest plus generation metadata."""

    profile: str
    seed: int
    parent_domain: str
    child_domain: str
    parent_graph: Any
    child_graph: Any
    template_instances: list[dict[str, Any]]
    generation_summary: dict[str, Any]

    @property
    def domain(self) -> str:
        return self.parent_domain


def build_phase4_v2_forest(
    *,
    profile: str = "medium",
    seed: int = 4402,
    parent_domain: str = "FOREST.EXAMPLE",
    child_domain: str | None = None,
) -> Phase4V2Forest:
    """Build a deterministic realistic parent/child forest for Phase 4B/v2."""

    normalized_profile = profile.removeprefix("phase4_v2_")
    if normalized_profile not in _PROFILE_SIZES:
        raise ValueError(
            f"Unsupported Phase 4B/v2 profile {profile!r}; expected one of "
            f"{sorted(_PROFILE_SIZES)}"
        )
    child_domain = child_domain or f"CHILD.{parent_domain}"
    parent_users, parent_workstations, parent_servers = _PROFILE_SIZES[normalized_profile]["parent"]
    child_users, child_workstations, child_servers = _PROFILE_SIZES[normalized_profile]["child"]

    parent_graph = build_phase4_v1_graph(
        domain=parent_domain,
        seed=seed,
        users=parent_users,
        workstations=parent_workstations,
        servers=parent_servers,
    )
    child_graph = build_phase4_v1_graph(
        domain=child_domain,
        seed=seed + 1,
        users=child_users,
        workstations=child_workstations,
        servers=child_servers,
    )

    template_instances = _template_instances(parent_graph, child_graph)
    generation_summary = {
        "generator_profile": f"phase4_v2_{normalized_profile}",
        "template_version": PHASE4_V2_TEMPLATE_VERSION,
        "seed": seed,
        "profile": normalized_profile,
        "forest": {
            "type": "parent_child",
            "domains": [parent_domain, child_domain],
            "trusts": [
                {
                    "source_domain": child_domain,
                    "target_domain": parent_domain,
                    "trust_type": "parent_child",
                    "direction": "bidirectional",
                    "seeded": True,
                }
            ],
        },
        "stats": {
            "domains": 2,
            "total_nodes": parent_graph.node_count() + child_graph.node_count(),
            "total_edges": parent_graph.edge_count() + child_graph.edge_count(),
            "template_instances": len(template_instances),
        },
    }
    return Phase4V2Forest(
        profile=normalized_profile,
        seed=seed,
        parent_domain=parent_domain,
        child_domain=child_domain,
        parent_graph=parent_graph,
        child_graph=child_graph,
        template_instances=template_instances,
        generation_summary=generation_summary,
    )


def _template_instances(parent_graph: Any, child_graph: Any) -> list[dict[str, Any]]:
    instances: list[dict[str, Any]] = []
    for graph_role, graph in (("parent", parent_graph), ("child", child_graph)):
        for index, planted in enumerate(graph.planted_paths, start=1):
            metadata = dict(planted.metadata)
            instances.append(
                {
                    "instance_id": f"{graph_role}-{planted.template_id}-{index:02d}",
                    "template_id": planted.template_id,
                    "template_version": PHASE4_V2_TEMPLATE_VERSION,
                    "scenario_family": metadata.get("scenario_family", "unknown"),
                    "domain_role": graph_role,
                    "domain": graph.domain,
                    "source_node": planted.source_node,
                    "target_node": planted.target_node,
                    "technical_difficulty": _template_difficulty(planted.template_id)[0],
                    "reasoning_difficulty": _template_difficulty(planted.template_id)[1],
                    "graph_diagnostics": {
                        "path_edge_count": len(planted.path_edges),
                        "critical_node_count": len(metadata.get("critical_nodes", [])),
                    },
                    "verification_cypher": planted.verification_cypher,
                    "mitre": list(planted.mitre),
                    "metadata": metadata,
                }
            )
    return instances


def _template_difficulty(template_id: str) -> tuple[int, int]:
    """Return template-defined v1 difficulty; graph features are diagnostics only."""

    if template_id == "t4_adcs_esc1":
        return 4, 3
    if template_id == "t4_rbcd_delegation":
        return 4, 4
    if template_id == "t5_adcs_to_delegation_composite":
        return 5, 5
    return 3, 3
