from __future__ import annotations

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

    assert len(templates) == 5
    assert {template.family for template in templates} == {
        "host_session_pivot",
        "constrained_delegation",
        "rbcd",
        "unconstrained_delegation",
        "acl_group_nesting",
    }
    assert all(template.template_id.startswith("t6_") for template in templates)
    assert all(template.positive for template in templates)


def test_phase4_complex_plants_initial_tier6_paths() -> None:
    _graph, paths = _complex_paths()

    assert {
        "t6_host_session_pivot_tier0",
        "t6_constrained_delegation_bridge_tier0",
        "t6_rbcd_computer_takeover_tier0",
        "t6_unconstrained_delegation_tgt_capture_tier0",
        "t6_acl_group_nesting_tier0",
    } <= set(paths)
    assert all(path.tier == 6 for tid, path in paths.items() if tid.startswith("t6_"))


def test_complex_paths_have_tool_effort_and_mechanism_contracts() -> None:
    _graph, paths = _complex_paths()
    tier6_paths = [path for path in paths.values() if path.template_id.startswith("t6_")]

    assert tier6_paths
    for path in tier6_paths:
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

    assert len(tier6) == 5
    assert not any("adcs" in path.metadata["family"] for path in tier6)
