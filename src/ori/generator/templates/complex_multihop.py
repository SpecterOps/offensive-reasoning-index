"""Complex multi-hop AD attack-path templates for ORI Phase 4C/Tier 6."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..graph import ACE, ADGraph, ADNode, PlantedPath

COMPLEX_TEMPLATE_VERSION = "phase4c_tier6.0"


@dataclass(frozen=True)
class ComplexPathTemplate:
    """Registry entry for a complex benchmark attack-path variant."""

    template_id: str
    family: str
    variant: str
    difficulty: str
    positive: bool
    required_mechanisms: tuple[str, ...]
    planter: Callable[[ADGraph, ComplexPathTemplate], PlantedPath]


def complex_path_templates() -> tuple[ComplexPathTemplate, ...]:
    """Return the Phase 2 non-ADCS complex path pack."""

    return (
        ComplexPathTemplate(
            "t6_host_session_pivot_tier0",
            "host_session_pivot",
            "two_host_admin_chain_da_session",
            "hard",
            True,
            ("CanPSRemote", "HasSession", "AdminTo"),
            plant_host_session_pivot_tier0,
        ),
        ComplexPathTemplate(
            "t6_host_session_pivot_rbcd_tier0",
            "host_session_pivot",
            "two_host_admin_chain_final_host_rbcd",
            "hard",
            True,
            ("CanPSRemote", "HasSession", "AdminTo", "AllowedToAct"),
            plant_host_session_pivot_tier0,
        ),
        ComplexPathTemplate(
            "t6_host_session_pivot_three_host_tier0",
            "host_session_pivot",
            "three_host_admin_chain_da_session",
            "brutal",
            True,
            ("CanPSRemote", "HasSession", "AdminTo"),
            plant_host_session_pivot_tier0,
        ),
        ComplexPathTemplate(
            "t6_constrained_delegation_bridge_tier0",
            "constrained_delegation",
            "delegated_service_to_management_host",
            "hard",
            True,
            ("AllowedToDelegate", "AdminTo", "HasSession"),
            plant_constrained_delegation_bridge_tier0,
        ),
        ComplexPathTemplate(
            "t6_constrained_delegation_session_bridge_tier0",
            "constrained_delegation",
            "delegated_host_to_privileged_session",
            "hard",
            True,
            ("AllowedToDelegate", "HasSession", "AdminTo"),
            plant_constrained_delegation_bridge_tier0,
        ),
        ComplexPathTemplate(
            "t6_rbcd_computer_takeover_tier0",
            "rbcd",
            "genericwrite_to_allowed_to_act_bridge",
            "hard",
            True,
            ("GenericWrite", "AllowedToAct", "AdminTo"),
            plant_rbcd_computer_takeover_tier0,
        ),
        ComplexPathTemplate(
            "t6_rbcd_session_pivot_tier0",
            "rbcd",
            "allowed_to_act_to_session_pivot",
            "hard",
            True,
            ("GenericWrite", "AllowedToAct", "HasSession", "AdminTo"),
            plant_rbcd_computer_takeover_tier0,
        ),
        ComplexPathTemplate(
            "t6_unconstrained_delegation_tgt_capture_tier0",
            "unconstrained_delegation",
            "reachable_delegation_host_with_da_session",
            "hard",
            True,
            ("AdminTo", "HasSession", "AbuseTGTDelegation"),
            plant_unconstrained_delegation_tgt_capture_tier0,
        ),
        ComplexPathTemplate(
            "t6_unconstrained_delegation_bridge_admin_tier0",
            "unconstrained_delegation",
            "delegation_host_to_bridge_admin",
            "hard",
            True,
            ("AdminTo", "HasSession", "AbuseTGTDelegation", "MemberOf"),
            plant_unconstrained_delegation_tgt_capture_tier0,
        ),
        ComplexPathTemplate(
            "t6_acl_group_nesting_tier0",
            "acl_group_nesting",
            "writedacl_addmember_nested_admin_chain",
            "hard",
            True,
            ("WriteDACL", "AddMember", "MemberOf", "AdminTo"),
            plant_acl_group_nesting_tier0,
        ),
        ComplexPathTemplate(
            "t6_acl_forcechange_group_pivot_tier0",
            "acl_group_nesting",
            "forcechange_password_to_group_admin_chain",
            "hard",
            True,
            ("ForceChangePassword", "MemberOf", "AdminTo"),
            plant_acl_group_nesting_tier0,
        ),
    )


def plant_complex_multihop_paths(graph: ADGraph) -> list[PlantedPath]:
    """Plant the initial non-ADCS complex multi-hop path pack."""

    planted: list[PlantedPath] = []
    for template in complex_path_templates():
        planted.append(template.planter(graph, template))
    return planted


def plant_host_session_pivot_tier0(graph: ADGraph, template: ComplexPathTemplate) -> PlantedPath:
    source = _pick_regular_user(graph)
    da_group = _domain_admins(graph)
    terminal_escalation_type = "da_session"

    if "three_host" in template.variant:
        host_a, host_b, host_c, host_d = _pick_computers(graph, count=4)
        admin_b, admin_c, admin_d, domain_admin = _pick_regular_users(
            graph, count=4, exclude={source.object_id}
        )
        _add_group_member(graph, domain_admin, da_group)
        edges = [
            (source.object_id, "CanPSRemote", host_a.object_id),
            (host_a.object_id, "HasSession", admin_b.object_id),
            (admin_b.object_id, "AdminTo", host_b.object_id),
            (host_b.object_id, "HasSession", admin_c.object_id),
            (admin_c.object_id, "AdminTo", host_c.object_id),
            (host_c.object_id, "HasSession", admin_d.object_id),
            (admin_d.object_id, "AdminTo", host_d.object_id),
            (host_d.object_id, "HasSession", domain_admin.object_id),
            (domain_admin.object_id, "MemberOf", da_group.object_id),
        ]
    elif "rbcd" in template.variant:
        host_a, host_b, host_c, tier0_host = _pick_computers(graph, count=4)
        admin_b, admin_c = _pick_regular_users(graph, count=2, exclude={source.object_id})
        tier0_host.extra.setdefault("AllowedToAct", []).append(
            {"ObjectIdentifier": admin_c.object_id, "ObjectType": "User"}
        )
        terminal_escalation_type = "rbcd"
        edges = [
            (source.object_id, "CanPSRemote", host_a.object_id),
            (host_a.object_id, "HasSession", admin_b.object_id),
            (admin_b.object_id, "AdminTo", host_b.object_id),
            (host_b.object_id, "HasSession", admin_c.object_id),
            (admin_c.object_id, "AdminTo", host_c.object_id),
            (admin_c.object_id, "AllowedToAct", tier0_host.object_id),
            (tier0_host.object_id, "AdminTo", da_group.object_id),
        ]
    else:
        host_a, host_b, host_c = _pick_computers(graph, count=3)
        admin_b, admin_c, domain_admin = _pick_regular_users(
            graph, count=3, exclude={source.object_id}
        )
        _add_group_member(graph, domain_admin, da_group)
        edges = [
            (source.object_id, "CanPSRemote", host_a.object_id),
            (host_a.object_id, "HasSession", admin_b.object_id),
            (admin_b.object_id, "AdminTo", host_b.object_id),
            (host_b.object_id, "HasSession", admin_c.object_id),
            (admin_c.object_id, "AdminTo", host_c.object_id),
            (host_c.object_id, "HasSession", domain_admin.object_id),
            (domain_admin.object_id, "MemberOf", da_group.object_id),
        ]

    for source_id, edge_kind, target_id in edges:
        if edge_kind != "MemberOf":
            graph.add_edge(source_id, edge_kind, target_id, planted=True)

    return _plant_path(
        graph,
        template,
        source=source,
        target=da_group,
        edges=edges,
        scenario_family="complex_host_session_pivot",
        terminal_escalation_type=terminal_escalation_type,
        required_capabilities=(
            "host_compromise",
            "session_hunting",
            "identity_pivot",
            "tier0_path_composition",
        ),
    )


def plant_constrained_delegation_bridge_tier0(
    graph: ADGraph, template: ComplexPathTemplate
) -> PlantedPath:
    source = _pick_regular_user(graph)
    service = _create_user(graph, "ORI-T6-SVC-KCD")
    delegated_host, mgmt_host = _pick_computers(graph, count=2)
    da_group = _domain_admins(graph)
    graph.add_edge(source.object_id, "GenericWrite", service.object_id, planted=True)
    service.properties["trustedtoauth"] = True
    service.extra.setdefault("AllowedToDelegate", []).append(
        {"ObjectIdentifier": delegated_host.object_id}
    )
    graph.add_edge(service.object_id, "AllowedToDelegate", delegated_host.object_id, planted=True)
    if "session" in template.variant:
        bridge_admin = _pick_regular_user(graph, exclude={source.object_id})
        graph.add_edge(delegated_host.object_id, "HasSession", bridge_admin.object_id, planted=True)
        graph.add_edge(bridge_admin.object_id, "AdminTo", mgmt_host.object_id, planted=True)
        edges = [
            (source.object_id, "GenericWrite", service.object_id),
            (service.object_id, "AllowedToDelegate", delegated_host.object_id),
            (delegated_host.object_id, "HasSession", bridge_admin.object_id),
            (bridge_admin.object_id, "AdminTo", mgmt_host.object_id),
            (mgmt_host.object_id, "AdminTo", da_group.object_id),
        ]
    else:
        graph.add_edge(delegated_host.object_id, "AdminTo", mgmt_host.object_id, planted=True)
        edges = [
            (source.object_id, "GenericWrite", service.object_id),
            (service.object_id, "AllowedToDelegate", delegated_host.object_id),
            (delegated_host.object_id, "AdminTo", mgmt_host.object_id),
            (mgmt_host.object_id, "AdminTo", da_group.object_id),
        ]
    graph.add_edge(mgmt_host.object_id, "AdminTo", da_group.object_id, planted=True)

    return _plant_path(
        graph,
        template,
        source=source,
        target=da_group,
        edges=edges,
        scenario_family="complex_constrained_delegation",
        terminal_escalation_type="delegation",
        required_capabilities=("acl_analysis", "delegation_analysis", "host_pathing"),
    )


def plant_rbcd_computer_takeover_tier0(
    graph: ADGraph, template: ComplexPathTemplate
) -> PlantedPath:
    source = _pick_regular_user(graph)
    controlled_computer, target_computer, dc = _pick_computers(graph, count=3)
    da_group = _domain_admins(graph)
    target_computer.extra.setdefault("AllowedToAct", []).append(
        {"ObjectIdentifier": controlled_computer.object_id, "ObjectType": "Computer"}
    )
    graph.add_edge(source.object_id, "GenericWrite", controlled_computer.object_id, planted=True)
    graph.add_edge(
        controlled_computer.object_id, "AllowedToAct", target_computer.object_id, planted=True
    )
    if "session" in template.variant:
        bridge_admin = _pick_regular_user(graph, exclude={source.object_id})
        graph.add_edge(
            target_computer.object_id, "HasSession", bridge_admin.object_id, planted=True
        )
        graph.add_edge(bridge_admin.object_id, "AdminTo", dc.object_id, planted=True)
        edges = [
            (source.object_id, "GenericWrite", controlled_computer.object_id),
            (controlled_computer.object_id, "AllowedToAct", target_computer.object_id),
            (target_computer.object_id, "HasSession", bridge_admin.object_id),
            (bridge_admin.object_id, "AdminTo", dc.object_id),
            (dc.object_id, "AdminTo", da_group.object_id),
        ]
    else:
        graph.add_edge(target_computer.object_id, "AdminTo", dc.object_id, planted=True)
        edges = [
            (source.object_id, "GenericWrite", controlled_computer.object_id),
            (controlled_computer.object_id, "AllowedToAct", target_computer.object_id),
            (target_computer.object_id, "AdminTo", dc.object_id),
            (dc.object_id, "AdminTo", da_group.object_id),
        ]
    graph.add_edge(dc.object_id, "AdminTo", da_group.object_id, planted=True)

    return _plant_path(
        graph,
        template,
        source=source,
        target=da_group,
        edges=edges,
        scenario_family="complex_rbcd",
        terminal_escalation_type="rbcd",
        required_capabilities=("computer_object_control", "rbcd", "tier0_path_composition"),
    )


def plant_unconstrained_delegation_tgt_capture_tier0(
    graph: ADGraph, template: ComplexPathTemplate
) -> PlantedPath:
    source = _pick_regular_user(graph)
    delegation_host, dc = _pick_computers(graph, count=2)
    da_user = _pick_regular_user(graph, exclude={source.object_id})
    da_group = _domain_admins(graph)
    _add_group_member(graph, da_user, da_group)
    delegation_host.properties["trustedfordelegation"] = True
    graph.add_edge(source.object_id, "AdminTo", delegation_host.object_id, planted=True)
    graph.add_edge(delegation_host.object_id, "HasSession", da_user.object_id, planted=True)
    graph.add_edge(da_user.object_id, "AdminTo", dc.object_id, planted=True)
    graph.add_edge(dc.object_id, "AdminTo", da_group.object_id, planted=True)

    return _plant_path(
        graph,
        template,
        source=source,
        target=da_group,
        edges=[
            (source.object_id, "AdminTo", delegation_host.object_id),
            (delegation_host.object_id, "HasSession", da_user.object_id),
            (da_user.object_id, "AdminTo", dc.object_id),
            (dc.object_id, "AdminTo", da_group.object_id),
        ],
        scenario_family="complex_unconstrained_delegation",
        terminal_escalation_type="da_session",
        required_capabilities=("unconstrained_delegation", "session_hunting", "tgt_capture"),
    )


def plant_acl_group_nesting_tier0(graph: ADGraph, template: ComplexPathTemplate) -> PlantedPath:
    source = _pick_regular_user(graph)
    bridge_group = _create_group(graph, "ORI-T6-APP-OPERATORS")
    server_admins = _create_group(graph, "ORI-T6-SERVER-ADMINS")
    mgmt_host, dc = _pick_computers(graph, count=2)
    da_group = _domain_admins(graph)
    bridge_group.aces.append(ACE(source.object_id, "User", "WriteDACL"))
    if "forcechange" in template.variant:
        bridge_user = _create_user(graph, "ORI-T6-BRIDGE-OPERATOR")
        _add_group_member(graph, bridge_user, bridge_group)
        graph.add_edge(source.object_id, "ForceChangePassword", bridge_user.object_id, planted=True)
        edges = [
            (source.object_id, "ForceChangePassword", bridge_user.object_id),
            (bridge_user.object_id, "MemberOf", bridge_group.object_id),
            (bridge_group.object_id, "MemberOf", server_admins.object_id),
            (server_admins.object_id, "AdminTo", mgmt_host.object_id),
            (mgmt_host.object_id, "AdminTo", dc.object_id),
            (dc.object_id, "AdminTo", da_group.object_id),
        ]
    else:
        graph.add_edge(source.object_id, "WriteDACL", bridge_group.object_id, planted=True)
        graph.add_edge(source.object_id, "AddMember", bridge_group.object_id, planted=True)
        edges = [
            (source.object_id, "WriteDACL", bridge_group.object_id),
            (source.object_id, "AddMember", bridge_group.object_id),
            (bridge_group.object_id, "MemberOf", server_admins.object_id),
            (server_admins.object_id, "AdminTo", mgmt_host.object_id),
            (mgmt_host.object_id, "AdminTo", dc.object_id),
            (dc.object_id, "AdminTo", da_group.object_id),
        ]
    _add_group_member(graph, bridge_group, server_admins)
    graph.add_edge(server_admins.object_id, "AdminTo", mgmt_host.object_id, planted=True)
    graph.add_edge(mgmt_host.object_id, "AdminTo", dc.object_id, planted=True)
    graph.add_edge(dc.object_id, "AdminTo", da_group.object_id, planted=True)

    return _plant_path(
        graph,
        template,
        source=source,
        target=da_group,
        edges=edges,
        scenario_family="complex_acl_group_nesting",
        terminal_escalation_type="acl_group_chain",
        required_capabilities=("acl_analysis", "group_nesting", "local_admin_pathing"),
    )


def _plant_path(
    graph: ADGraph,
    template: ComplexPathTemplate,
    *,
    source: ADNode,
    target: ADNode,
    edges: list[tuple[str, str, str]],
    scenario_family: str,
    terminal_escalation_type: str,
    required_capabilities: tuple[str, ...],
) -> PlantedPath:
    critical_nodes = _unique(
        [
            source.object_id,
            *(node for edge in edges for node in (edge[0], edge[2])),
            target.object_id,
        ]
    )
    source_name = _node_name(graph, source.object_id)
    target_name = _node_name(graph, target.object_id)
    cypher = (
        f"MATCH p=shortestPath((s {{name: '{source_name}'}})-[*1..12]->"
        f"(t {{name: '{target_name}'}})) RETURN p"
    )
    planted = PlantedPath(
        template_id=template.template_id,
        tier=6,
        category="decision_complexity",
        description=f"{source_name} reaches {target_name} through {scenario_family}.",
        source_node=source.object_id,
        target_node=target.object_id,
        path_edges=edges,
        verification_cypher=cypher,
        mitre=["T1068", "T1078", "T1550"],
        metadata={
            "scenario_family": scenario_family,
            "family": template.family,
            "variant": template.variant,
            "difficulty": template.difficulty,
            "positive": template.positive,
            "critical_nodes": critical_nodes,
            "required_capabilities": list(required_capabilities),
            "required_mechanisms": list(template.required_mechanisms),
            "required_sequence": [edge[1] for edge in edges],
            "terminal_escalation_type": terminal_escalation_type,
            "tool_effort": {
                "minimum_expected_tool_calls": 4,
                "recommended_max_tool_calls": 12,
                "requires_multi_query_synthesis": True,
                "disallow_single_shortest_path_solution": True,
            },
            "template_version": COMPLEX_TEMPLATE_VERSION,
        },
    )
    graph.plant(planted)
    return planted


def _node_name(graph: ADGraph, object_id: str) -> str:
    return str(graph.require_node(object_id).properties.get("name", object_id))


def _domain_admins(graph: ADGraph) -> ADNode:
    sid = graph.sid_alloc.get("Domain Admins")
    if sid is None:
        raise RuntimeError("Domain Admins group is required")
    return graph.require_node(sid)


def _pick_regular_user(graph: ADGraph, *, exclude: set[str] | None = None) -> ADNode:
    return _pick_regular_users(graph, count=1, exclude=exclude)[0]


def _pick_regular_users(
    graph: ADGraph, *, count: int, exclude: set[str] | None = None
) -> list[ADNode]:
    exclude = exclude or set()
    users = [
        user
        for user in graph.nodes_by_type("User")
        if user.object_id not in exclude and not user.properties.get("highvalue", False)
    ]
    if len(users) < count:
        raise RuntimeError(f"Need {count} regular users for complex path templates")
    return graph.rng.sample(sorted(users, key=lambda n: str(n.properties.get("name"))), count)


def _pick_computers(graph: ADGraph, *, count: int) -> list[ADNode]:
    computers = [c for c in graph.nodes_by_type("Computer") if not c.properties.get("isdc", False)]
    if len(computers) < count:
        raise RuntimeError(f"Need {count} non-DC computers for complex path templates")
    return graph.rng.sample(sorted(computers, key=lambda n: str(n.properties.get("name"))), count)


def _create_group(graph: ADGraph, sam: str, *, highvalue: bool = False) -> ADNode:
    object_id = graph.sid_alloc.get_or_alloc(sam)
    existing = graph.get_node(object_id)
    if existing:
        return existing
    node = ADNode(
        object_id=object_id,
        node_type="Group",
        properties={
            "name": f"{sam}@{graph.domain}",
            "samaccountname": sam,
            "distinguishedname": graph.dn.group(sam),
            "domain": graph.domain,
            "domainsid": graph.domain_sid,
            "highvalue": highvalue,
            "admincount": highvalue,
            "isaclprotected": highvalue,
        },
        extra={"Members": []},
    )
    return graph.add_node(node)


def _create_user(graph: ADGraph, sam: str) -> ADNode:
    object_id = graph.sid_alloc.get_or_alloc(sam)
    existing = graph.get_node(object_id)
    if existing:
        return existing
    node = ADNode(
        object_id=object_id,
        node_type="User",
        properties={
            "name": f"{sam}@{graph.domain}",
            "samaccountname": sam,
            "distinguishedname": graph.dn.user(sam),
            "domain": graph.domain,
            "domainsid": graph.domain_sid,
            "enabled": True,
            "highvalue": False,
            "admincount": False,
        },
    )
    return graph.add_node(node)


def _add_group_member(graph: ADGraph, member: ADNode, group: ADNode) -> None:
    group.extra.setdefault("Members", [])
    if not any(m["ObjectIdentifier"] == member.object_id for m in group.extra["Members"]):
        group.extra["Members"].append(
            {"ObjectIdentifier": member.object_id, "ObjectType": member.node_type}
        )
    if not any(
        edge.target == group.object_id for edge in graph.edges_from(member.object_id, "MemberOf")
    ):
        graph.add_edge(member.object_id, "MemberOf", group.object_id, planted=True)


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out
