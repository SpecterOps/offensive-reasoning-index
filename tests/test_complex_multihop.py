from __future__ import annotations

from ori.cli import _build_manifest
from ori.eval.tasks import generate_mcp_tasks, generate_tasks
from ori.generator.phase4 import build_phase4_complex_graph
from ori.generator.templates.complex_multihop import complex_path_templates


def _complex_paths():
    graph = build_phase4_complex_graph(
        domain="COMPLEX.TEST",
        seed=4401,
        users=80,
        workstations=30,
        servers=12,
    )
    return graph, {path.template_id: path for path in graph.planted_paths}


def test_complex_path_registry_starts_with_non_adcs_families() -> None:
    templates = complex_path_templates()

    assert len(templates) == 19
    assert {template.family for template in templates} == {
        "host_session_pivot",
        "constrained_delegation",
        "rbcd",
        "unconstrained_delegation",
        "acl_group_nesting",
        "gpo_ou_control",
        "laps_session_pivot",
        "trust_hopping",
        "kerberoast_privilege_chain",
        "adcs_identity_transition",
        "path_selection_decoy",
        "negative_control",
        "stale_session_contingency",
    }
    assert all(template.template_id.startswith("t6_") for template in templates)
    assert any(not template.positive for template in templates)


def test_phase4_complex_plants_initial_tier6_paths() -> None:
    _graph, paths = _complex_paths()

    assert {
        "t6_host_session_pivot_tier0",
        "t6_host_session_pivot_rbcd_tier0",
        "t6_host_session_pivot_three_host_tier0",
        "t6_constrained_delegation_bridge_tier0",
        "t6_constrained_delegation_session_bridge_tier0",
        "t6_rbcd_computer_takeover_tier0",
        "t6_rbcd_session_pivot_tier0",
        "t6_unconstrained_delegation_tgt_capture_tier0",
        "t6_unconstrained_delegation_bridge_admin_tier0",
        "t6_acl_group_nesting_tier0",
        "t6_acl_forcechange_group_pivot_tier0",
    } <= set(paths)
    assert all(path.tier == 6 for tid, path in paths.items() if tid.startswith("t6_"))


def test_complex_paths_have_tool_effort_and_mechanism_contracts() -> None:
    _graph, paths = _complex_paths()
    tier6_paths = [path for path in paths.values() if path.template_id.startswith("t6_")]

    assert tier6_paths
    for path in tier6_paths:
        if path.metadata["negative_control"]:
            continue
        assert len(path.path_edges) >= 4
        assert len(path.metadata["critical_nodes"]) >= 4
        assert path.metadata["required_mechanisms"]
        assert path.metadata["required_sequence"] == [edge[1] for edge in path.path_edges]
        assert path.metadata["template_version"] == "phase4c_tier6.0"
        assert path.metadata["tool_effort"]["requires_multi_query_synthesis"] is True
        assert path.metadata["tool_effort"]["minimum_expected_tool_calls"] >= 4


def test_host_session_pivot_requires_multiple_sessions_and_terminal_escalation() -> None:
    _graph, paths = _complex_paths()
    path = paths["t6_host_session_pivot_tier0"]
    edge_kinds = [edge[1] for edge in path.path_edges]

    assert edge_kinds.count("HasSession") >= 2
    assert any(kind in {"AdminTo", "CanPSRemote", "CanRDP"} for kind in edge_kinds)
    assert path.metadata["terminal_escalation_type"] in {
        "da_session",
        "rbcd",
        "gpo",
        "laps",
        "delegation",
    }


def test_initial_complex_pack_is_non_adcs_heavy() -> None:
    _graph, paths = _complex_paths()
    tier6 = [path for path in paths.values() if path.template_id.startswith("t6_")]

    assert len(tier6) == 19
    assert any("adcs" in path.metadata["family"] for path in tier6)
    assert any(path.metadata["negative_control"] for path in tier6)


def test_phase2_pack_has_multiple_variants_per_initial_family() -> None:
    templates = complex_path_templates()

    for family in {
        "host_session_pivot",
        "constrained_delegation",
        "rbcd",
        "unconstrained_delegation",
        "acl_group_nesting",
    }:
        assert sum(1 for template in templates if template.family == family) >= 2


def test_host_session_phase2_variants_cover_long_chain_and_terminal_rbcd() -> None:
    _graph, paths = _complex_paths()
    rbcd = paths["t6_host_session_pivot_rbcd_tier0"]
    three_host = paths["t6_host_session_pivot_three_host_tier0"]

    assert rbcd.metadata["terminal_escalation_type"] == "rbcd"
    assert "AllowedToAct" in [edge[1] for edge in rbcd.path_edges]
    assert [edge[1] for edge in three_host.path_edges].count("HasSession") >= 3


def test_tier6_tasks_are_generated_with_operator_questions() -> None:
    graph, _paths = _complex_paths()
    manifest = _build_manifest(graph, seed=4401)
    tasks = [task for task in generate_tasks(manifest) if task.tier == 6]

    assert len(tasks) == 19
    assert all(
        "multiple graph lookups" in task.question or "actually viable" in task.question
        for task in tasks
    )
    assert all(task.metadata["required_mechanisms"] for task in tasks)
    assert all(task.metadata["tool_effort"] for task in tasks)
    assert any(task.metadata["negative_control"] for task in tasks)


def test_tier6_mcp_tasks_include_complex_direct_tasks() -> None:
    graph, _paths = _complex_paths()
    manifest = _build_manifest(graph, seed=4401)
    mcp_tasks = [task for task in generate_mcp_tasks(manifest) if task.tier == 6]

    assert len(mcp_tasks) >= 19
    assert all(task.metadata["mcp_track"] for task in mcp_tasks)
