"""
Tier 2 attack path templates — multi-hop paths requiring 2-4 edges.

These require chaining multiple relationships and intermediate reasoning.
Models should solve these at ~60% expected pass rate.
"""

from __future__ import annotations

import time

from ..graph import ACE, ADGraph, ADNode, PlantedPath, TypedPrincipal


def plant_kerberoast_chain(graph: ADGraph) -> PlantedPath:
    """
    Template: t2_kerberoast_chain

    A service account with an SPN is Kerberoastable. It is a member of
    Server-Admins, so cracking the password gives local admin on all servers.

    Path: SVC_BACKUP (hasspn=True) → MemberOf → SERVER-ADMINS → AdminTo → SRV-FILE-01

    Attack: Kerberoast SVC_BACKUP → offline crack → use creds → local admin on servers.
    """
    server_admins_sid = graph.sid_alloc._allocated.get("Server-Admins")
    server_admins_node = graph.get_node(server_admins_sid) if server_admins_sid else None
    domain_users_sid = graph.sid_alloc._allocated.get("Domain Users")
    da_sid = graph.sid_alloc._allocated.get("Domain Admins")

    servers = [
        c for c in graph.nodes_by_type("Computer")
        if "SRV" in c.properties.get("name", "") and not c.properties.get("isdc", False)
    ]
    if not servers:
        raise RuntimeError("No servers found for t2_kerberoast_chain")
    target_server = servers[0]

    # Create the service account
    svc_sid = graph.sid_alloc.alloc("svc_backup")
    now = int(time.time())
    svc_node = ADNode(
        object_id=svc_sid,
        node_type="User",
        properties={
            "name": f"SVC_BACKUP@{graph.domain}",
            "displayname": "Backup Service Account",
            "samaccountname": "svc_backup",
            "userprincipalname": f"svc_backup@{graph.domain.lower()}",
            "distinguishedname": f"CN=svc_backup,CN=Users,{graph.dn.domain_root()}",
            "domain": graph.domain,
            "domainsid": graph.domain_sid,
            "enabled": True,
            "hasspn": True,
            "unconstraineddelegation": False,
            "trustedtoauth": False,
            "passwordnotreqd": False,
            "dontreqpreauth": False,
            "pwdlastset": now - (365 * 86400),  # Password never rotated — realistic
            "lastlogon": now - graph.rng.randint(0, 30 * 86400),
            "lastlogontimestamp": now - graph.rng.randint(0, 30 * 86400),
            "admincount": True,
            "highvalue": False,
            "isaclprotected": False,
            "department": "IT",
        },
        extra={
            "AllowedToDelegate": [],
            "SPNTargets": [{"ComputerSID": target_server.object_id, "Port": 1433, "Service": "MSSQLSvc"}],
            "HasSIDHistory": [],
        },
    )
    graph.add_node(svc_node)

    # Domain Users membership
    if domain_users_sid and graph.get_node(domain_users_sid):
        graph.add_edge(svc_sid, "MemberOf", domain_users_sid)
        graph.get_node(domain_users_sid).extra["Members"].append(
            {"ObjectIdentifier": svc_sid, "ObjectType": "User"}
        )

    # Server-Admins membership — grants AdminTo on servers transitively via GPOChanges
    if server_admins_node:
        graph.add_edge(svc_sid, "MemberOf", server_admins_sid)
        server_admins_node.extra["Members"].append(
            {"ObjectIdentifier": svc_sid, "ObjectType": "User"}
        )

    # DA has GenericAll on this account (default ACL)
    if da_sid:
        svc_node.aces.append(ACE(
            principal_sid=da_sid, principal_type="Group",
            right_name="GenericAll", is_inherited=True,
        ))

    cypher = (
        f"MATCH p=shortestPath( "
        f"(u:User {{name: 'SVC_BACKUP@{graph.domain}'}}) "
        f"-[*1..]->(c:Computer {{name: '{target_server.properties['name']}'}}) "
        f") RETURN p"
    )

    planted = PlantedPath(
        template_id="t2_kerberoast_chain",
        tier=2,
        category="path_finding",
        description=(
            f"SVC_BACKUP@{graph.domain} is a service account with an SPN (Kerberoastable, "
            f"password never rotated). It is a member of Server-Admins, which has AdminTo "
            f"access on all servers including {target_server.properties['name']}."
        ),
        source_node=svc_sid,
        target_node=target_server.object_id,
        path_edges=[
            (svc_sid, "MemberOf", server_admins_sid),
            (server_admins_sid, "AdminTo", target_server.object_id),
        ],
        verification_cypher=cypher,
        mitre=["T1558.003", "T1078.002"],
    )
    graph.plant(planted)
    return planted


def plant_acl_chain(graph: ADGraph) -> PlantedPath:
    """
    Template: t2_acl_chain

    A non-IT user has GenericAll on the Server-Admins group. With GenericAll,
    the attacker can add themselves (or any account) to the group, gaining
    AdminTo access on all servers.

    Path: USER → GenericAll → SERVER-ADMINS → AdminTo → SRV-FILE-01

    Attack: exploit GenericAll → AddMember self to Server-Admins → local admin on servers.
    """
    server_admins_sid = graph.sid_alloc._allocated.get("Server-Admins")
    server_admins_node = graph.get_node(server_admins_sid) if server_admins_sid else None
    if not server_admins_node:
        raise RuntimeError("Server-Admins not found for t2_acl_chain")

    # Pick a non-IT, non-privileged user as the attacker
    it_admins_sid = graph.sid_alloc._allocated.get("IT-Admins")
    it_admins_node = graph.get_node(it_admins_sid) if it_admins_sid else None
    privileged_sids = {
        m["ObjectIdentifier"]
        for m in (it_admins_node.extra.get("Members", []) if it_admins_node else [])
    }

    candidates = [
        u for u in graph.nodes_by_type("User")
        if u.object_id not in privileged_sids
        and u.properties.get("department") not in ("IT", None)
    ]
    if not candidates:
        raise RuntimeError("No eligible users for t2_acl_chain")

    attacker = graph.rng.choice(candidates)

    # Plant: GenericAll ACE from attacker onto Server-Admins
    server_admins_node.aces.append(ACE(
        principal_sid=attacker.object_id,
        principal_type="User",
        right_name="GenericAll",
        is_inherited=False,
    ))
    graph.add_edge(attacker.object_id, "GenericAll", server_admins_sid)

    servers = [
        c for c in graph.nodes_by_type("Computer")
        if "SRV" in c.properties.get("name", "") and not c.properties.get("isdc", False)
    ]
    target_server = servers[0] if servers else graph.nodes_by_type("Computer")[0]

    cypher = (
        f"MATCH p=shortestPath( "
        f"(u:User {{name: '{attacker.properties['name']}'}}) "
        f"-[*1..]->(c:Computer) "
        f") WHERE c.name STARTS WITH 'SRV-' RETURN p"
    )

    planted = PlantedPath(
        template_id="t2_acl_chain",
        tier=2,
        category="path_finding",
        description=(
            f"{attacker.properties['name']} ({attacker.properties.get('department')} dept) "
            f"has GenericAll on Server-Admins. By adding themselves to the group, they gain "
            f"local admin on all servers including {target_server.properties['name']}."
        ),
        source_node=attacker.object_id,
        target_node=target_server.object_id,
        path_edges=[
            (attacker.object_id, "GenericAll", server_admins_sid),
            (server_admins_sid, "AdminTo", target_server.object_id),
        ],
        verification_cypher=cypher,
        mitre=["T1222.001", "T1078.002"],
    )
    graph.plant(planted)
    return planted


def plant_nested_groups(graph: ADGraph) -> PlantedPath:
    """
    Template: t2_nested_groups

    A user is in a nested group chain that eventually reaches Server-Admins.
    The nesting obscures the privilege escalation path — a common real-world pattern
    where groups accumulate membership over time without auditing.

    Path: USER → MemberOf → INFRA-TEAM → MemberOf → INFRA-LEADS → MemberOf → SERVER-ADMINS → AdminTo → SRV

    Attack: the nested membership grants transitive AdminTo on servers.
    """
    server_admins_sid = graph.sid_alloc._allocated.get("Server-Admins")
    server_admins_node = graph.get_node(server_admins_sid) if server_admins_sid else None
    if not server_admins_node:
        raise RuntimeError("Server-Admins not found for t2_nested_groups")

    domain_users_sid = graph.sid_alloc._allocated.get("Domain Users")
    da_sid = graph.sid_alloc._allocated.get("Domain Admins")
    cn_users_dn = f"CN=Users,{graph.dn.domain_root()}"

    # Create two nested groups
    infra_leads_sid = graph.sid_alloc.alloc("INFRA-LEADS")
    infra_team_sid = graph.sid_alloc.alloc("INFRA-TEAM")

    infra_leads_node = ADNode(
        object_id=infra_leads_sid,
        node_type="Group",
        properties={
            "name": f"INFRA-LEADS@{graph.domain}",
            "samaccountname": "INFRA-LEADS",
            "distinguishedname": f"CN=INFRA-LEADS,{cn_users_dn}",
            "domain": graph.domain,
            "domainsid": graph.domain_sid,
            "highvalue": False,
            "admincount": True,
            "isaclprotected": False,
        },
        extra={"Members": []},
    )
    infra_team_node = ADNode(
        object_id=infra_team_sid,
        node_type="Group",
        properties={
            "name": f"INFRA-TEAM@{graph.domain}",
            "samaccountname": "INFRA-TEAM",
            "distinguishedname": f"CN=INFRA-TEAM,{cn_users_dn}",
            "domain": graph.domain,
            "domainsid": graph.domain_sid,
            "highvalue": False,
            "admincount": False,
            "isaclprotected": False,
        },
        extra={"Members": []},
    )
    graph.add_node(infra_leads_node)
    graph.add_node(infra_team_node)

    # Wire the nesting: INFRA-TEAM → MemberOf → INFRA-LEADS → MemberOf → Server-Admins
    graph.add_edge(infra_team_sid, "MemberOf", infra_leads_sid)
    infra_leads_node.extra["Members"].append({"ObjectIdentifier": infra_team_sid, "ObjectType": "Group"})

    graph.add_edge(infra_leads_sid, "MemberOf", server_admins_sid)
    server_admins_node.extra["Members"].append({"ObjectIdentifier": infra_leads_sid, "ObjectType": "Group"})

    # Pick a non-IT, non-privileged user and add them to INFRA-TEAM
    it_admins_sid = graph.sid_alloc._allocated.get("IT-Admins")
    it_admins_node = graph.get_node(it_admins_sid) if it_admins_sid else None
    privileged_sids = {
        m["ObjectIdentifier"]
        for m in (it_admins_node.extra.get("Members", []) if it_admins_node else [])
    }

    candidates = [
        u for u in graph.nodes_by_type("User")
        if u.object_id not in privileged_sids
        and u.properties.get("department") == "Engineering"
    ] or [
        u for u in graph.nodes_by_type("User")
        if u.object_id not in privileged_sids
        and u.properties.get("department") not in ("IT", None)
    ]
    if not candidates:
        raise RuntimeError("No eligible users for t2_nested_groups")

    source_user = graph.rng.choice(candidates)
    graph.add_edge(source_user.object_id, "MemberOf", infra_team_sid)
    infra_team_node.extra["Members"].append(
        {"ObjectIdentifier": source_user.object_id, "ObjectType": "User"}
    )
    # Also add to Domain Users
    if domain_users_sid and graph.get_node(domain_users_sid):
        graph.add_edge(source_user.object_id, "MemberOf", domain_users_sid)
        graph.get_node(domain_users_sid).extra["Members"].append(
            {"ObjectIdentifier": source_user.object_id, "ObjectType": "User"}
        )

    # DA has GenericAll on both groups
    for node in (infra_leads_node, infra_team_node):
        if da_sid:
            node.aces.append(ACE(
                principal_sid=da_sid, principal_type="Group",
                right_name="GenericAll", is_inherited=True,
            ))

    servers = [
        c for c in graph.nodes_by_type("Computer")
        if "SRV" in c.properties.get("name", "") and not c.properties.get("isdc", False)
    ]
    target_server = servers[0] if servers else graph.nodes_by_type("Computer")[0]

    cypher = (
        f"MATCH p=shortestPath( "
        f"(u:User {{name: '{source_user.properties['name']}'}}) "
        f"-[*1..]->(c:Computer) "
        f") WHERE c.name STARTS WITH 'SRV-' RETURN p"
    )

    planted = PlantedPath(
        template_id="t2_nested_groups",
        tier=2,
        category="path_finding",
        description=(
            f"{source_user.properties['name']} is in INFRA-TEAM, which is nested inside "
            f"INFRA-LEADS, which is a member of Server-Admins. Transitive membership grants "
            f"local admin on all servers including {target_server.properties['name']}."
        ),
        source_node=source_user.object_id,
        target_node=target_server.object_id,
        path_edges=[
            (source_user.object_id, "MemberOf", infra_team_sid),
            (infra_team_sid, "MemberOf", infra_leads_sid),
            (infra_leads_sid, "MemberOf", server_admins_sid),
            (server_admins_sid, "AdminTo", target_server.object_id),
        ],
        verification_cypher=cypher,
        mitre=["T1078.002"],
    )
    graph.plant(planted)
    return planted
