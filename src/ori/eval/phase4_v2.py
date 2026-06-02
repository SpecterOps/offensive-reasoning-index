"""Phase 4B/v2 official task corpus and matrix validation."""

from __future__ import annotations

from collections import Counter
from typing import Any

from ori.generator.phase4_v2 import PHASE4_V2_TEMPLATE_VERSION, Phase4V2Forest

_TRACKS = ("cypher", "mcp")
_FAMILIES = ("adcs_esc1", "delegation_rbcd", "adcs_delegation_composite")
_TASK_TYPES = ("path_finding", "enumeration", "cypher_generation", "mcp_analysis")

_SMOKE_TEMPLATES = [
    (
        "startup-domain-count",
        "Confirm that the generated forest contains the expected parent and child domains.",
    ),
    ("startup-parent-da", "Identify the Domain Admins group in the parent domain."),
    ("startup-child-da", "Identify the Domain Admins group in the child domain."),
    ("startup-ca-presence", "Confirm that ADCS objects are present for certificate-abuse tasks."),
]


def generate_phase4_v2_official_tasks(forest: Phase4V2Forest) -> dict[str, Any]:
    """Generate the Phase 4B/v2 official corpus: 4 smoke + 96 matrix tasks."""

    smoke_tasks = [
        _smoke_task(forest, index, suffix, question)
        for index, (suffix, question) in enumerate(_SMOKE_TEMPLATES, 1)
    ]
    matrix_tasks = [_matrix_task(forest, index) for index in range(1, 97)]
    tasks = smoke_tasks + matrix_tasks
    validation_input = {
        "profile": forest.profile,
        "official_count": len(tasks),
        "tasks_official": tasks,
    }
    matrix_validation = validate_phase4_v2_matrix(validation_input)
    generation_summary = dict(forest.generation_summary)
    generation_summary.update(
        {
            "official_count": len(tasks),
            "startup_smoke_count": len(smoke_tasks),
            "benchmark_matrix_count": len(matrix_tasks),
        }
    )
    return {
        "schema_version": "phase4b_v2.0",
        "profile": forest.profile,
        "official_count": len(tasks),
        "raw_score_denominator": len(tasks),
        "stats": _forest_stats(forest),
        "planted_paths": _planted_paths(forest),
        "generation_summary": generation_summary,
        "template_instances": forest.template_instances,
        "tasks_official": tasks,
        "tasks_candidates": _candidate_tasks(forest),
        "answers": _answer_key(tasks),
        "matrix_validation": matrix_validation,
        "run_summary": {
            "profile": forest.profile,
            "official_count": len(tasks),
            "startup_smoke_count": len(smoke_tasks),
            "benchmark_matrix_count": len(matrix_tasks),
            "scoring": "mechanical_binary_no_partial_credit",
        },
    }


def validate_phase4_v2_matrix(corpus: dict[str, Any]) -> dict[str, Any]:
    """Validate task counts and matrix balance using only benchmark-matrix tasks."""

    tasks = list(corpus.get("tasks_official", []))
    smoke = [task for task in tasks if task.get("phase") == "startup_smoke"]
    matrix = [task for task in tasks if task.get("phase") == "benchmark_matrix"]
    by_family = Counter(str(task.get("scenario_family", "unknown")) for task in matrix)
    by_track = Counter(str(task.get("track", "unknown")) for task in matrix)
    by_task_type = Counter(str(task.get("category", "unknown")) for task in matrix)
    errors: list[str] = []
    if len(tasks) != 100:
        errors.append(f"official task count must be 100, got {len(tasks)}")
    if len(smoke) != 4:
        errors.append(f"startup smoke task count must be 4, got {len(smoke)}")
    if len(matrix) != 96:
        errors.append(f"benchmark matrix task count must be 96, got {len(matrix)}")
    if any(task.get("benchmark_weight") != "official_score" for task in smoke):
        errors.append("all startup smoke tasks must count toward official_score")
    expected_family = {family: 32 for family in _FAMILIES}
    expected_track = {"cypher": 48, "mcp": 48}
    expected_task_type = {task_type: 24 for task_type in _TASK_TYPES}
    if dict(by_family) != expected_family:
        errors.append(
            "scenario family set/counts must be "
            f"{expected_family}, got {dict(sorted(by_family.items()))}"
        )
    if dict(by_track) != expected_track:
        errors.append(
            f"track balance must be {expected_track}, got {dict(sorted(by_track.items()))}"
        )
    if dict(by_task_type) != expected_task_type:
        errors.append(
            "task type balance must be "
            f"{expected_task_type}, got {dict(sorted(by_task_type.items()))}"
        )
    return {
        "ok": not errors,
        "errors": errors,
        "official_count": len(tasks),
        "startup_smoke_count": len(smoke),
        "benchmark_matrix_count": len(matrix),
        "balance_scope": "benchmark_matrix_only",
        "raw_score_scope": "startup_smoke_plus_benchmark_matrix",
        "matrix_balance": {
            "by_family": dict(sorted(by_family.items())),
            "by_track": dict(sorted(by_track.items())),
            "by_task_type": dict(sorted(by_task_type.items())),
        },
    }


def _base_task(*, task_id: str, phase: str, question: str, category: str) -> dict[str, Any]:
    return {
        "id": task_id,
        "phase": phase,
        "question": question,
        "category": category,
        "grade_mode": "mechanical_binary",
        "benchmark_weight": "official_score",
        "scoring": {"type": "binary", "partial_credit": False, "correct": 1, "incorrect": 0},
    }


def _smoke_task(forest: Phase4V2Forest, index: int, suffix: str, question: str) -> dict[str, Any]:
    task = _base_task(
        task_id=f"p4v2-smoke-{index:02d}-{suffix}",
        phase="startup_smoke",
        question=question,
        category="startup_smoke",
    )
    task.update(
        {
            "smoke_task": True,
            "track": "startup",
            "scenario_family": "startup_smoke",
            "technical_difficulty": 1,
            "reasoning_difficulty": 1,
            "template_id": "startup_smoke",
            "template_version": PHASE4_V2_TEMPLATE_VERSION,
            "reference": {"domains": [forest.parent_domain, forest.child_domain]},
            "graph_diagnostics": {"domain_count": 2},
        }
    )
    return task


def _matrix_task(forest: Phase4V2Forest, index: int) -> dict[str, Any]:
    instance = forest.template_instances[(index - 1) % len(forest.template_instances)]
    family = _FAMILIES[(index - 1) % len(_FAMILIES)]
    track = _TRACKS[(index - 1) % len(_TRACKS)]
    category = _TASK_TYPES[(index - 1) % len(_TASK_TYPES)]
    task = _base_task(
        task_id=f"p4v2-matrix-{index:03d}",
        phase="benchmark_matrix",
        question=(
            f"Solve Phase 4B/v2 {family} task {index:03d} in the {instance['domain_role']} "
            f"domain using {track} evidence."
        ),
        category=category,
    )
    task.update(
        {
            "smoke_task": False,
            "track": track,
            "scenario_family": family,
            "template_id": instance["template_id"],
            "template_instance_id": instance["instance_id"],
            "template_version": PHASE4_V2_TEMPLATE_VERSION,
            "technical_difficulty": instance["technical_difficulty"],
            "reasoning_difficulty": instance["reasoning_difficulty"],
            "reference_cypher": instance["verification_cypher"],
            "mitre": instance["mitre"],
            "graph_diagnostics": instance["graph_diagnostics"],
        }
    )
    return task


def _forest_stats(forest: Phase4V2Forest) -> dict[str, int]:
    graphs = (forest.parent_graph, forest.child_graph)
    return {
        "users": sum(len(graph.nodes_by_type("User")) for graph in graphs),
        "computers": sum(len(graph.nodes_by_type("Computer")) for graph in graphs),
        "groups": sum(len(graph.nodes_by_type("Group")) for graph in graphs),
        "ous": sum(len(graph.nodes_by_type("OU")) for graph in graphs),
        "domains": sum(len(graph.nodes_by_type("Domain")) for graph in graphs),
        "total_nodes": sum(graph.node_count() for graph in graphs),
        "total_edges": sum(graph.edge_count() for graph in graphs) + 2,
        "planted_paths": len(forest.template_instances),
    }


def _planted_paths(forest: Phase4V2Forest) -> list[dict[str, Any]]:
    paths: list[dict[str, Any]] = []
    for domain_role, graph in (("parent", forest.parent_graph), ("child", forest.child_graph)):
        for planted in graph.planted_paths:
            source = graph.require_node(planted.source_node)
            target = graph.require_node(planted.target_node)
            paths.append(
                {
                    "template_id": planted.template_id,
                    "domain_role": domain_role,
                    "domain": graph.domain,
                    "tier": planted.tier,
                    "category": planted.category,
                    "description": planted.description,
                    "source": planted.source_node,
                    "target": planted.target_node,
                    "source_name": source.properties.get("name", ""),
                    "target_name": target.properties.get("name", ""),
                    "verification_cypher": planted.verification_cypher,
                    "path_edges": [
                        {"source": src, "edge": edge_kind, "target": dst}
                        for src, edge_kind, dst in planted.path_edges
                    ],
                    "mitre": list(planted.mitre),
                    "metadata": dict(planted.metadata),
                }
            )
    return paths


def _candidate_tasks(forest: Phase4V2Forest) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for index, instance in enumerate(forest.template_instances, start=1):
        candidates.append(
            {
                "id": f"p4v2-candidate-{index:03d}",
                "template_instance_id": instance["instance_id"],
                "scenario_family": instance["scenario_family"],
                "eligible_for_official": True,
            }
        )
    return candidates


def _answer_key(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "task_id": task["id"],
            "score_if_correct": 1,
            "score_if_incorrect": 0,
            "partial_credit": False,
            "benchmark_weight": task["benchmark_weight"],
        }
        for task in tasks
    ]
