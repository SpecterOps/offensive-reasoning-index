"""OAIC-only near-miss fixtures appended to the unchanged complex generator."""

from __future__ import annotations

from .graph import ADGraph, ADNode, PlantedPath
from .phase4 import build_phase4_complex_graph
from .templates.complex_multihop import _create_gpo, _create_group, _create_user

OAIC_NEGATIVE_FIXTURES = (
    ("oaic-negative-admin-target", "User", "Computer", "AdminTo", 1, "main"),
    ("oaic-negative-session-target", "Computer", "User", "HasSession", 1, "main"),
    ("oaic-negative-membership-bridge", "Group", "Group", "MemberOf", 3, "main"),
    ("oaic-negative-delegation-target", "User", "Computer", "AllowedToDelegate", 1, "main"),
    ("oaic-negative-rbcd-target", "Computer", "Computer", "AllowedToAct", 1, "main"),
    ("oaic-negative-acl-target", "User", "Group", "GenericAll", 1, "main"),
    ("oaic-negative-gpo-control", "Group", "GPO", "GenericWrite", 1, "diagnostic"),
    ("oaic-negative-identity-transition", "Group", "User", "GenericWrite", 1, "diagnostic"),
)


def _node(graph: ADGraph, kind: str, name: str) -> ADNode:
    if kind == "User":
        return _create_user(graph, name)
    if kind == "Group":
        return _create_group(graph, name)
    if kind == "GPO":
        return _create_gpo(graph, name)
    if kind != "Computer":
        raise ValueError(f"Unsupported OAIC fixture object type: {kind}")
    return graph.add_node(
        ADNode(
            object_id=graph.sid_alloc.get_or_alloc(name),
            node_type="Computer",
            properties={
                "name": f"{name}.{graph.domain}",
                "samaccountname": name + "$",
                "distinguishedname": graph.dn.computer(name),
                "domain": graph.domain,
                "domainsid": graph.domain_sid,
                "enabled": True,
                "isdc": False,
                "highvalue": False,
            },
        )
    )


def build_oaic_graph(
    domain: str, seed: int, users: int, workstations: int, servers: int
) -> ADGraph:
    """Build complex first, then dedicated absent-target/real-near-miss pairs.

    ``target_node`` remains the real planted path endpoint. Absence recipes use
    metadata ``negative_target``; no nonexistent edge enters ``path_edges``.
    """
    graph = build_phase4_complex_graph(
        domain=domain, seed=seed, users=users, workstations=workstations, servers=servers
    )
    for (
        template,
        source_type,
        target_type,
        relationship,
        hops,
        eligibility,
    ) in OAIC_NEGATIVE_FIXTURES:
        prefix = template.upper()
        source = _node(graph, source_type, prefix + "-SOURCE")
        target = _node(graph, target_type, prefix + "-ABSENT")
        near = _node(graph, target_type, prefix + "-NEAR")
        edges = [(source.object_id, relationship, near.object_id)]
        if relationship == "MemberOf":
            bridge = _node(graph, "Group", prefix + "-BRIDGE")
            edges = [
                (source.object_id, relationship, bridge.object_id),
                (bridge.object_id, relationship, near.object_id),
            ]
        for left, kind, right in edges:
            graph.add_edge(left, kind, right, planted=True)
        graph.plant(
            PlantedPath(
                template_id=template,
                tier=6,
                category="bounded_absence",
                description="A present near-miss branch does not establish the target link.",
                source_node=source.object_id,
                target_node=near.object_id,
                path_edges=edges,
                verification_cypher=(
                    f"MATCH p=(s {{objectid: '{source.object_id}'}})"
                    f"-[:{relationship}*1..{hops}]->(t {{objectid: '{near.object_id}'}}) RETURN p"
                ),
                metadata={
                    "negative_control": True,
                    "negative_target": target.object_id,
                    "near_miss": near.object_id,
                    "relationship": relationship,
                    "direction": "outbound",
                    "min_hops": 1,
                    "max_hops": hops,
                    "fixture_role": eligibility,
                    "template_version": "oaic-negative-v1",
                },
            )
        )
    return graph
