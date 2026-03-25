"""Org structure layer: domain, OUs, users, computers, groups."""

from __future__ import annotations

import time
import uuid

from faker import Faker

from .graph import ACE, ADGraph, ADNode, TypedPrincipal


# Departments used for OU hierarchy and group generation
DEPARTMENTS = ["IT", "HR", "Finance", "Engineering", "Operations"]

# Well-known built-in group names (used for SID allocation + node creation)
BUILTIN_GROUPS = [
    "Domain Admins",
    "Domain Users",
    "Domain Computers",
    "Domain Controllers",
    "Enterprise Admins",
    "Schema Admins",
    "Administrators",
    "Backup Operators",
    "Remote Desktop Users",
    "Group Policy Creator Owners",
]


def build_org(
    graph: ADGraph,
    num_users: int = 20,
    num_workstations: int = 8,
    num_servers: int = 4,
    num_dcs: int = 1,
) -> None:
    """Populate the graph with an org structure: domain, OUs, users, computers, groups."""
    fake = Faker()
    fake.seed_instance(graph.seed)

    domain_node = _create_domain(graph)
    ou_map = _create_ous(graph, domain_node)
    _create_builtin_groups(graph, domain_node, ou_map)
    _create_departmental_groups(graph, domain_node, ou_map)
    _create_users(graph, fake, domain_node, ou_map, num_users)
    _create_computers(graph, domain_node, ou_map, num_workstations, num_servers, num_dcs)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _create_domain(graph: ADGraph) -> ADNode:
    domain_dn = graph.dn.domain_root()
    node = ADNode(
        object_id=graph.domain_sid,
        node_type="Domain",
        properties={
            "domain": graph.domain,
            "name": graph.domain,
            "distinguishedname": domain_dn,
            "domainsid": graph.domain_sid,
            "functionallevel": 7,  # Windows Server 2016
            "highvalue": True,
        },
        extra={
            "Trusts": [],
            "ChildObjects": [],
            "Links": [],
        },
    )
    return graph.add_node(node)


def _create_ous(graph: ADGraph, domain_node: ADNode) -> dict[str, str]:
    """Create OU hierarchy. Returns name → OU DN mapping."""
    ou_map: dict[str, str] = {}

    top_level = ["IT", "HR", "Finance", "Engineering", "Operations", "Servers", "ServiceAccounts"]
    for ou_name in top_level:
        ou_dn = graph.dn.ou(ou_name)
        ou_sid = graph.sid_alloc.alloc(f"OU-{ou_name}")
        ou_node = ADNode(
            object_id=ou_sid,
            node_type="OU",
            properties={
                "name": f"{ou_name.upper()}@{graph.domain}",
                "distinguishedname": ou_dn,
                "domain": graph.domain,
                "domainsid": graph.domain_sid,
                "blocksinheritance": False,
                "highvalue": False,
                "isaclprotected": False,
            },
            contained_by=TypedPrincipal(
                object_id=graph.domain_sid, object_type="Domain"
            ),
            extra={"ChildObjects": [], "Links": []},
        )
        graph.add_node(ou_node)
        ou_map[ou_name] = ou_dn

        # Register as child of domain
        domain_node.extra["ChildObjects"].append(
            {"ObjectIdentifier": ou_sid, "ObjectType": "OU"}
        )

    # Sub-OUs under IT
    for sub in ["Workstations", "Admins"]:
        parent_dn = ou_map["IT"]
        sub_dn = f"OU={sub},{parent_dn}"
        sub_sid = graph.sid_alloc.alloc(f"OU-IT-{sub}")
        sub_node = ADNode(
            object_id=sub_sid,
            node_type="OU",
            properties={
                "name": f"{sub.upper()}@{graph.domain}",
                "distinguishedname": sub_dn,
                "domain": graph.domain,
                "domainsid": graph.domain_sid,
                "blocksinheritance": False,
                "highvalue": False,
                "isaclprotected": False,
            },
            contained_by=TypedPrincipal(
                object_id=graph.sid_alloc.get_or_alloc(f"OU-IT"), object_type="OU"
            ),
            extra={"ChildObjects": [], "Links": []},
        )
        graph.add_node(sub_node)
        ou_map[f"IT/{sub}"] = sub_dn

    return ou_map


def _create_builtin_groups(
    graph: ADGraph, domain_node: ADNode, ou_map: dict[str, str]
) -> None:
    """Create well-known AD groups with canonical SIDs."""
    now = int(time.time())
    cn_users_dn = f"CN=Users,{graph.dn.domain_root()}"
    builtin_dn = f"CN=Builtin,{graph.dn.domain_root()}"

    for group_name in BUILTIN_GROUPS:
        sid = graph.sid_alloc.alloc(group_name)
        is_admin = group_name in ("Domain Admins", "Enterprise Admins", "Administrators")
        node = ADNode(
            object_id=sid,
            node_type="Group",
            properties={
                "name": f"{group_name.upper()}@{graph.domain}",
                "samaccountname": group_name,
                "distinguishedname": f"CN={group_name},{cn_users_dn}",
                "domain": graph.domain,
                "domainsid": graph.domain_sid,
                "highvalue": is_admin,
                "admincount": is_admin,
                "isaclprotected": False,
            },
            extra={"Members": []},
        )
        graph.add_node(node)


def _create_departmental_groups(
    graph: ADGraph, domain_node: ADNode, ou_map: dict[str, str]
) -> None:
    """Create departmental groups (IT-Admins, IT-Users, etc.)."""
    cn_users_dn = f"CN=Users,{graph.dn.domain_root()}"
    dept_groups = [
        ("IT-Admins", True),
        ("IT-Users", False),
        ("Server-Admins", True),
        ("Workstation-Admins", False),
        ("HR-Users", False),
        ("Finance-Users", False),
        ("Engineering-Users", False),
    ]
    for group_name, is_admin in dept_groups:
        sid = graph.sid_alloc.alloc(group_name)
        node = ADNode(
            object_id=sid,
            node_type="Group",
            properties={
                "name": f"{group_name.upper()}@{graph.domain}",
                "samaccountname": group_name,
                "distinguishedname": f"CN={group_name},{cn_users_dn}",
                "domain": graph.domain,
                "domainsid": graph.domain_sid,
                "highvalue": False,
                "admincount": is_admin,
                "isaclprotected": False,
            },
            extra={"Members": []},
        )
        graph.add_node(node)


def _create_users(
    graph: ADGraph,
    fake: Faker,
    domain_node: ADNode,
    ou_map: dict[str, str],
    num_users: int,
) -> None:
    """Create regular users distributed across departments."""
    dept_ou_keys = ["IT", "HR", "Finance", "Engineering", "Operations"]
    now = int(time.time())
    # Keep track of samaccountnames to avoid duplicates
    used_sams: set[str] = set()

    for i in range(num_users):
        dept = dept_ou_keys[i % len(dept_ou_keys)]
        ou_dn = ou_map[dept]

        # Generate unique samaccountname
        for _ in range(10):
            first = fake.first_name()
            last = fake.last_name()
            sam = f"{first[0].lower()}{last.lower()}"[:20]
            if sam not in used_sams:
                used_sams.add(sam)
                break
        else:
            sam = f"user{i}"
            used_sams.add(sam)

        display_name = f"{first} {last}"
        upn = f"{sam}@{graph.domain.lower()}"
        dn = graph.dn.user(display_name, f"OU={dept}")
        sid = graph.sid_alloc.alloc(sam)

        node = ADNode(
            object_id=sid,
            node_type="User",
            properties={
                "name": f"{sam.upper()}@{graph.domain}",
                "displayname": display_name,
                "samaccountname": sam,
                "userprincipalname": upn,
                "distinguishedname": dn,
                "domain": graph.domain,
                "domainsid": graph.domain_sid,
                "enabled": True,
                "hasspn": False,
                "unconstraineddelegation": False,
                "trustedtoauth": False,
                "passwordnotreqd": False,
                "dontreqpreauth": False,
                "pwdlastset": now - graph.rng.randint(0, 90 * 86400),
                "lastlogon": now - graph.rng.randint(0, 30 * 86400),
                "lastlogontimestamp": now - graph.rng.randint(0, 30 * 86400),
                "admincount": False,
                "highvalue": False,
                "isaclprotected": False,
                "department": dept,
            },
            contained_by=TypedPrincipal(
                object_id=graph.sid_alloc.get_or_alloc(f"OU-{dept}"), object_type="OU"
            ),
            extra={"AllowedToDelegate": [], "SPNTargets": [], "HasSIDHistory": []},
        )
        graph.add_node(node)


def _create_computers(
    graph: ADGraph,
    domain_node: ADNode,
    ou_map: dict[str, str],
    num_workstations: int,
    num_servers: int,
    num_dcs: int,
) -> None:
    """Create workstations, servers, and domain controllers."""
    now = int(time.time())

    # Workstations
    ws_ou_dn = ou_map.get("IT/Workstations", ou_map["IT"])
    for i in range(1, num_workstations + 1):
        name = f"WS-IT-{i:02d}"
        _add_computer(graph, name, ws_ou_dn, is_dc=False)

    # Servers
    server_names = ["SRV-FILE-01", "SRV-WEB-01", "SRV-SQL-01", "SRV-APP-01"]
    srv_ou_dn = ou_map["Servers"]
    for i in range(num_servers):
        name = server_names[i] if i < len(server_names) else f"SRV-{i:02d}"
        _add_computer(graph, name, srv_ou_dn, is_dc=False)

    # Domain controllers
    dc_ou_dn = f"OU=Domain Controllers,{graph.dn.domain_root()}"
    # Create the DC OU if it doesn't exist
    dc_ou_sid = graph.sid_alloc.alloc("OU-DomainControllers")
    if graph.get_node(dc_ou_sid) is None:
        dc_ou_node = ADNode(
            object_id=dc_ou_sid,
            node_type="OU",
            properties={
                "name": f"DOMAIN CONTROLLERS@{graph.domain}",
                "distinguishedname": dc_ou_dn,
                "domain": graph.domain,
                "domainsid": graph.domain_sid,
                "blocksinheritance": False,
                "highvalue": True,
                "isaclprotected": False,
            },
            contained_by=TypedPrincipal(object_id=graph.domain_sid, object_type="Domain"),
            extra={"ChildObjects": [], "Links": []},
        )
        graph.add_node(dc_ou_node)
        domain_node.extra["ChildObjects"].append(
            {"ObjectIdentifier": dc_ou_sid, "ObjectType": "OU"}
        )

    for i in range(1, num_dcs + 1):
        name = f"DC{i:02d}"
        _add_computer(graph, name, dc_ou_dn, is_dc=True)


def _add_computer(graph: ADGraph, name: str, ou_dn: str, is_dc: bool) -> ADNode:
    fqdn = f"{name}.{graph.domain.lower()}"
    sam = f"{name}$"
    sid = graph.sid_alloc.alloc(name)
    now = int(time.time())

    node = ADNode(
        object_id=sid,
        node_type="Computer",
        properties={
            "name": f"{name}.{graph.domain}",
            "samaccountname": sam,
            "distinguishedname": graph.dn.computer(name, ou_dn.split(",")[0]),
            "domain": graph.domain,
            "domainsid": graph.domain_sid,
            "enabled": True,
            "haslaps": False,
            "unconstraineddelegation": is_dc,  # DCs always have unconstrained delegation
            "trustedtoauth": False,
            "isdc": is_dc,
            "highvalue": is_dc,
            "isaclprotected": False,
            "operatingsystem": "Windows Server 2022" if is_dc else "Windows 10 Enterprise",
            "pwdlastset": now - graph.rng.randint(0, 30 * 86400),
            "lastlogon": now - graph.rng.randint(0, 7 * 86400),
            "lastlogontimestamp": now - graph.rng.randint(0, 7 * 86400),
        },
        contained_by=None,  # Could set to OU TypedPrincipal
        extra={"AllowedToDelegate": [], "AllowedToAct": [], "HasSIDHistory": [], "Sessions": {"Results": [], "Collected": True}},
    )
    return graph.add_node(node)
