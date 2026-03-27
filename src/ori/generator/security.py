"""Baseline security layer: default MemberOf edges, ACLs, sessions, local admins."""

from __future__ import annotations

from .graph import ACE, ADGraph, TypedPrincipal


def apply_baseline_security(graph: ADGraph) -> None:
    """
    Apply realistic default security configuration to the graph.

    This layer creates the 'noise' that makes the graph realistic.
    Without it, planted attack paths would be trivially identifiable
    as the only non-trivial edges in the graph.

    Applies:
    - MemberOf edges: users→Domain Users, computers→Domain Computers, DCs→Domain Controllers
    - Group memberships: IT-Admins→Domain Admins
    - AdminTo edges: Domain Admins→all computers, IT-Admins→workstations
    - HasSession edges: users with sessions on their department workstations
    - Default ACEs on groups and OUs
    """
    _apply_default_memberships(graph)
    _apply_admin_edges(graph)
    _apply_has_sessions(graph)
    _apply_default_aces(graph)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _sid(graph: ADGraph, name: str) -> str | None:
    """Look up SID by name from the allocator's registry."""
    return graph.sid_alloc.get(name)


def _apply_default_memberships(graph: ADGraph) -> None:
    """
    Users → Domain Users, Computers → Domain Computers, DCs → Domain Controllers.
    Also: IT-Admins → Domain Admins (a common real-world pattern).
    """
    domain_users_sid = _sid(graph, "Domain Users")
    domain_computers_sid = _sid(graph, "Domain Computers")
    domain_controllers_sid = _sid(graph, "Domain Controllers")
    domain_admins_sid = _sid(graph, "Domain Admins")
    it_admins_sid = _sid(graph, "IT-Admins")
    server_admins_sid = _sid(graph, "Server-Admins")
    enterprise_admins_sid = _sid(graph, "Enterprise Admins")

    if domain_users_sid is None or domain_computers_sid is None:
        return

    # All users → Domain Users
    for node in graph.nodes_by_type("User"):
        if graph.get_node(domain_users_sid):
            graph.add_edge(node.object_id, "MemberOf", domain_users_sid)
            # Register member on the group
            group = graph.get_node(domain_users_sid)
            if group:
                group.extra["Members"].append(
                    {"ObjectIdentifier": node.object_id, "ObjectType": "User"}
                )

    # All computers → Domain Computers (non-DC)
    for node in graph.nodes_by_type("Computer"):
        is_dc = node.properties.get("isdc", False)
        if not is_dc and graph.get_node(domain_computers_sid):
            graph.add_edge(node.object_id, "MemberOf", domain_computers_sid)
            group = graph.get_node(domain_computers_sid)
            if group:
                group.extra["Members"].append(
                    {"ObjectIdentifier": node.object_id, "ObjectType": "Computer"}
                )

    # DCs → Domain Controllers
    if domain_controllers_sid:
        for node in graph.nodes_by_type("Computer"):
            if node.properties.get("isdc", False) and graph.get_node(domain_controllers_sid):
                graph.add_edge(node.object_id, "MemberOf", domain_controllers_sid)
                group = graph.get_node(domain_controllers_sid)
                if group:
                    group.extra["Members"].append(
                        {"ObjectIdentifier": node.object_id, "ObjectType": "Computer"}
                    )

    # IT-Admins → Domain Admins (common real-world delegation pattern)
    if it_admins_sid and domain_admins_sid:
        if graph.get_node(it_admins_sid) and graph.get_node(domain_admins_sid):
            graph.add_edge(it_admins_sid, "MemberOf", domain_admins_sid)
            da_group = graph.get_node(domain_admins_sid)
            if da_group:
                da_group.extra["Members"].append(
                    {"ObjectIdentifier": it_admins_sid, "ObjectType": "Group"}
                )

    # Domain Admins → Enterprise Admins (typical forest root config)
    if domain_admins_sid and enterprise_admins_sid:
        if graph.get_node(domain_admins_sid) and graph.get_node(enterprise_admins_sid):
            graph.add_edge(domain_admins_sid, "MemberOf", enterprise_admins_sid)
            ea_group = graph.get_node(enterprise_admins_sid)
            if ea_group:
                ea_group.extra["Members"].append(
                    {"ObjectIdentifier": domain_admins_sid, "ObjectType": "Group"}
                )

    # Wire a few IT users into IT-Admins and IT-Users
    it_users = [
        n for n in graph.nodes_by_type("User")
        if n.properties.get("department") == "IT"
    ]
    it_users_sid = _sid(graph, "IT-Users")
    for idx, user in enumerate(it_users):
        if it_users_sid and graph.get_node(it_users_sid):
            graph.add_edge(user.object_id, "MemberOf", it_users_sid)
            g = graph.get_node(it_users_sid)
            if g:
                g.extra["Members"].append(
                    {"ObjectIdentifier": user.object_id, "ObjectType": "User"}
                )
        # First half of IT users are also in IT-Admins
        if it_admins_sid and graph.get_node(it_admins_sid) and idx < len(it_users) // 2:
            graph.add_edge(user.object_id, "MemberOf", it_admins_sid)
            g = graph.get_node(it_admins_sid)
            if g:
                g.extra["Members"].append(
                    {"ObjectIdentifier": user.object_id, "ObjectType": "User"}
                )


def _apply_admin_edges(graph: ADGraph) -> None:
    """
    Apply local admin, RDP, DCOM, and PSRemote edges via GPOChanges on Domain/OU objects.

    BH CE file upload derives these edges from GPOChanges.LocalAdmins/RemoteDesktopUsers/
    DcomUsers/PSRemoteUsers + GPOChanges.AffectedComputers on Domain and OU objects.
    LocalAdmins.Results/RemoteDesktopUsers.Results etc. on Computer objects are also
    populated for consistency but are not read by BH CE during file upload ingest.

    - Domain Admins → AdminTo → all computers          (Domain-level GPOChanges)
    - IT-Admins → CanRDP/ExecuteDCOM/CanPSRemote → all computers  (Domain-level GPOChanges)
    - Server-Admins → AdminTo/CanRDP/CanPSRemote → servers only   (Servers OU GPOChanges)
    """
    domain_admins_sid = _sid(graph, "Domain Admins")
    server_admins_sid = _sid(graph, "Server-Admins")
    it_admins_sid = _sid(graph, "IT-Admins")

    all_computers = graph.nodes_by_type("Computer")
    server_comps = [
        c for c in all_computers
        if "SRV" in c.properties.get("name", "") and not c.properties.get("isdc", False)
    ]

    # --- Graph edges + computer-level Results (for raw JSON consumers) ---

    def _add_admin_to(group_sid: str, comp) -> None:
        graph.add_edge(group_sid, "AdminTo", comp.object_id)
        comp.extra["LocalAdmins"]["Results"].append({"ObjectIdentifier": group_sid, "ObjectType": "Group"})

    def _add_rdp(group_sid: str, comp) -> None:
        graph.add_edge(group_sid, "CanRDP", comp.object_id)
        comp.extra["RemoteDesktopUsers"]["Results"].append({"ObjectIdentifier": group_sid, "ObjectType": "Group"})
        # UserRights URA entry — BH CE's PostCanRDP requires SeRemoteInteractiveLogonRight
        # on the Computer object to create CanRDP edges (FetchComputersWithURA path).
        ur = next(
            (u for u in comp.extra["UserRights"] if u["Privilege"] == "SeRemoteInteractiveLogonRight"),
            None,
        )
        if ur is None:
            ur = {"Privilege": "SeRemoteInteractiveLogonRight", "Results": [], "Collected": True, "FailureReason": None, "LocalNames": []}
            comp.extra["UserRights"].append(ur)
        ur["Results"].append({"ObjectIdentifier": group_sid, "ObjectType": "Group"})

    def _add_dcom(group_sid: str, comp) -> None:
        graph.add_edge(group_sid, "ExecuteDCOM", comp.object_id)
        comp.extra["DcomUsers"]["Results"].append({"ObjectIdentifier": group_sid, "ObjectType": "Group"})

    def _add_psremote(group_sid: str, comp) -> None:
        graph.add_edge(group_sid, "CanPSRemote", comp.object_id)
        comp.extra["PSRemoteUsers"]["Results"].append({"ObjectIdentifier": group_sid, "ObjectType": "Group"})

    if domain_admins_sid and graph.get_node(domain_admins_sid):
        for comp in all_computers:
            _add_admin_to(domain_admins_sid, comp)

    if it_admins_sid and graph.get_node(it_admins_sid):
        for comp in all_computers:
            _add_rdp(it_admins_sid, comp)
            _add_dcom(it_admins_sid, comp)
            _add_psremote(it_admins_sid, comp)

    if server_admins_sid and graph.get_node(server_admins_sid):
        for comp in server_comps:
            _add_admin_to(server_admins_sid, comp)
            _add_rdp(server_admins_sid, comp)
            _add_psremote(server_admins_sid, comp)

    # --- Domain-level GPOChanges (BH CE ingest source of truth) ---
    # Covers Domain Admins AdminTo + IT-Admins RDP/DCOM/PSRemote on all computers.
    domain_nodes = graph.nodes_by_type("Domain")
    if domain_nodes and domain_admins_sid:
        it_admins_entry = (
            [{"ObjectIdentifier": it_admins_sid, "ObjectType": "Group"}]
            if it_admins_sid else []
        )
        domain_nodes[0].extra["GPOChanges"] = {
            "LocalAdmins": [{"ObjectIdentifier": domain_admins_sid, "ObjectType": "Group"}],
            "RemoteDesktopUsers": it_admins_entry,
            "DcomUsers": it_admins_entry,
            "PSRemoteUsers": it_admins_entry,
            "AffectedComputers": [
                {"ObjectIdentifier": c.object_id, "ObjectType": "Computer"}
                for c in all_computers
            ],
        }

    # --- Servers OU GPOChanges (Server-Admins scoped to servers only) ---
    servers_ou_sid = _sid(graph, "OU-Servers")
    servers_ou_node = graph.get_node(servers_ou_sid) if servers_ou_sid else None
    if servers_ou_node and server_admins_sid and server_comps:
        sa_entry = [{"ObjectIdentifier": server_admins_sid, "ObjectType": "Group"}]
        servers_ou_node.extra["GPOChanges"] = {
            "LocalAdmins": sa_entry,
            "RemoteDesktopUsers": sa_entry,
            "DcomUsers": [],
            "PSRemoteUsers": sa_entry,
            "AffectedComputers": [
                {"ObjectIdentifier": c.object_id, "ObjectType": "Computer"}
                for c in server_comps
            ],
        }


def _apply_has_sessions(graph: ADGraph) -> None:
    """
    Add HasSession edges: users have sessions on computers in their area.
    A few DA/privileged users have sessions on workstations (common misconfiguration).
    """
    workstations = [
        c for c in graph.nodes_by_type("Computer")
        if "WS-" in c.properties.get("name", "")
    ]
    users = graph.nodes_by_type("User")

    if not workstations:
        return

    def _add_session(ws, user) -> None:
        graph.add_edge(ws.object_id, "HasSession", user.object_id)
        ws.extra["Sessions"]["Results"].append({
            "UserSID": user.object_id,
            "ComputerSID": ws.object_id,
        })

    # Regular users get sessions on workstations
    for i, user in enumerate(users[:min(len(users), len(workstations))]):
        ws = workstations[i % len(workstations)]
        _add_session(ws, user)

    # Privileged session on a workstation — common real-world finding
    # DA group contains IT-Admins (a group), so look for IT-Admins user members
    it_admins_sid = _sid(graph, "IT-Admins")
    it_admins_node = graph.get_node(it_admins_sid) if it_admins_sid else None
    if it_admins_node and workstations:
        target_ws = graph.rng.choice(workstations)
        da_user_members = [
            graph.get_node(m["ObjectIdentifier"])
            for m in it_admins_node.extra.get("Members", [])
            if m["ObjectType"] == "User"
        ]
        da_users = [u for u in da_user_members if u is not None]
        if da_users:
            da_user = graph.rng.choice(da_users)
            _add_session(target_ws, da_user)


def _apply_default_aces(graph: ADGraph) -> None:
    """
    Apply default ACEs: Domain Admins has GenericAll on most objects.
    This reflects the default ACL structure in a real AD environment.
    """
    da_sid = _sid(graph, "Domain Admins")
    if not da_sid:
        return

    for node in list(graph.nodes_by_type("User")) + list(graph.nodes_by_type("Computer")):
        node.aces.append(ACE(
            principal_sid=da_sid,
            principal_type="Group",
            right_name="GenericAll",
            is_inherited=True,
        ))
