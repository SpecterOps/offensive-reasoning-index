"""Phase 4 dataset generation profiles."""

from __future__ import annotations

from .attack_paths import plant_all_paths
from .graph import ADGraph
from .org import build_org
from .security import apply_baseline_security
from .templates.phase4 import plant_phase4_v1_paths, stabilize_phase4_timestamps


def build_phase4_v1_graph(
    *,
    domain: str,
    seed: int,
    users: int = 32,
    workstations: int = 12,
    servers: int = 6,
) -> ADGraph:
    """Build a deterministic Phase 4 v1 graph without changing Phase 3 generation."""

    graph = ADGraph(domain=domain, seed=seed)
    build_org(
        graph,
        num_users=users,
        num_workstations=workstations,
        num_servers=servers,
    )
    apply_baseline_security(graph)
    plant_all_paths(graph)
    plant_phase4_v1_paths(graph)
    stabilize_phase4_timestamps(graph)
    return graph
