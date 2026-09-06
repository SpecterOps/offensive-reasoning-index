"""Phase 4 advanced AD benchmark templates."""

from __future__ import annotations

from ..graph import ACE, ADGraph, ADNode, PlantedPath

PHASE4_TEMPLATE_VERSION = "phase4_v1.0"
_FIXED_EPOCH = 1_725_000_000


def plant_phase4_v1_paths(graph: ADGraph) -> list[PlantedPath]:
    """Plant the Phase 4 v1 advanced ADCS and delegation scenarios."""

    paths = [
        plant_adcs_esc1_path(graph),
        plant_rbcd_delegation_path(graph),
    ]
    paths.append(plant_adcs_to_delegation_composite(graph, paths[0], paths[1]))
    return paths


def stabilize_phase4_timestamps(graph: ADGraph) -> None:
    """Make Phase 4 serialized node timestamps deterministic for a seed."""

    for index, node in enumerate(sorted(graph._nodes.values(), key=lambda n: n.object_id)):
        if node.node_type not in {"User", "Computer"}:
            continue
        base = _FIXED_EPOCH - (index * 137)
        node.properties["pwdlastset"] = base
        node.properties["lastlogon"] = base - 3600
        node.properties["lastlogontimestamp"] = base - 7200


def _node_name(graph: ADGraph, object_id: str) -> str:
    node = graph.require_node(object_id)
    return str(node.properties.get("name", object_id))


def _domain_admins(graph: ADGraph) -> ADNode:
    sid = graph.sid_alloc.get("Domain Admins")
    if not sid:
        raise RuntimeError("Domain Admins group is required for Phase 4 templates")
    return graph.require_node(sid)


def _pick_user(graph: ADGraph, *, exclude: set[str] | None = None) -> ADNode:
    exclude = exclude or set()
    users = [
        u
        for u in graph.nodes_by_type("User")
        if not u.properties.get("highvalue", False)
        and not u.properties.get("trustedtoauth", False)
        and u.object_id not in exclude
    ]
    if not users:
        raise RuntimeError("Phase 4 templates require at least one regular user")
    return graph.rng.choice(sorted(users, key=lambda n: n.properties["name"]))


def _pick_computer(graph: ADGraph, *, exclude: set[str] | None = None) -> ADNode:
    exclude = exclude or set()
    computers = [
        c
        for c in graph.nodes_by_type("Computer")
        if not c.properties.get("isdc", False) and c.object_id not in exclude
    ]
    if not computers:
        raise RuntimeError("Phase 4 templates require at least one non-DC computer")
    return graph.rng.choice(sorted(computers, key=lambda n: n.properties["name"]))


def _add_group_member(graph: ADGraph, member: ADNode, group: ADNode) -> None:
    if not any(m["ObjectIdentifier"] == member.object_id for m in group.extra.get("Members", [])):
        group.extra.setdefault("Members", []).append(
            {"ObjectIdentifier": member.object_id, "ObjectType": member.node_type}
        )
    if not any(e.target == group.object_id for e in graph.edges_from(member.object_id, "MemberOf")):
        graph.add_edge(member.object_id, "MemberOf", group.object_id)


def _add_phase4_adcs(graph: ADGraph) -> tuple[ADNode, ADNode, ADNode, ADNode, ADNode]:
    ca_host = _pick_computer(graph)
    root_id = f"{graph.domain_sid}-ROOTCA-PHASE4"
    enterprise_id = f"{graph.domain_sid}-ENTERPRISECA-PHASE4"
    ntauth_id = f"{graph.domain_sid}-NTAUTH-PHASE4"
    template_id = f"{graph.domain_sid}-CERTTEMPLATE-PHASE4-ESC1"

    root = ADNode(
        object_id=root_id,
        node_type="RootCA",
        properties={
            "domain": graph.domain,
            "name": f"ORI-ROOT-CA@{graph.domain}",
            "domainsid": graph.domain_sid,
            "caname": "ORI Root CA",
            "highvalue": True,
        },
    )
    enterprise = ADNode(
        object_id=enterprise_id,
        node_type="EnterpriseCA",
        properties={
            "domain": graph.domain,
            "name": f"ORI-ENTERPRISE-CA@{graph.domain}",
            "distinguishedname": f"CN=ORI-Enterprise-CA,CN=Enrollment Services,CN=Public Key Services,CN=Services,CN=Configuration,{graph.dn.domain_root()}",  # noqa: E501
            "domainsid": graph.domain_sid,
            "caname": "ORI Enterprise CA",
            "dnshostname": ca_host.properties["name"].lower(),
            "highvalue": True,
        },
        extra={"CertTemplates": [], "EnabledCertTemplates": []},
    )
    ntauth = ADNode(
        object_id=ntauth_id,
        node_type="NTAuthStore",
        properties={
            "domain": graph.domain,
            "name": f"NTAUTH@{graph.domain}",
            "domainsid": graph.domain_sid,
        },
    )
    template = ADNode(
        object_id=template_id,
        node_type="CertTemplate",
        properties={
            "domain": graph.domain,
            "name": f"ORI-ESC1-USER@{graph.domain}",
            "domainsid": graph.domain_sid,
            "displayname": "ORI ESC1 User Authentication",
            "validityperiod": "1 year",
            "renewalperiod": "6 weeks",
            "schemaversion": 2,
            "enrollmentflag": 0,
            "requiresmanagerapproval": False,
            "authenticationenabled": True,
            "nosecurityextension": False,
            "enrolleesuppliessubject": True,
            "subjectaltrequireupn": False,
            "ekus": ["Client Authentication"],
            "certificateapplicationpolicy": ["Client Authentication"],
            "highvalue": True,
        },
    )
    enroll_group = ADNode(
        object_id=graph.sid_alloc.alloc("ORI-Phase4-Cert-Enrollers"),
        node_type="Group",
        properties={
            "name": f"ORI-PHASE4-CERT-ENROLLERS@{graph.domain}",
            "samaccountname": "ORI-Phase4-Cert-Enrollers",
            "distinguishedname": f"CN=ORI-Phase4-Cert-Enrollers,CN=Users,{graph.dn.domain_root()}",
            "domain": graph.domain,
            "domainsid": graph.domain_sid,
            "highvalue": False,
            "admincount": False,
            "isaclprotected": False,
        },
        extra={"Members": []},
    )

    for node in (root, enterprise, ntauth, template, enroll_group):
        graph.add_node(node)

    enterprise.extra["CertTemplates"].append({"ObjectIdentifier": template.object_id})
    enterprise.extra["EnabledCertTemplates"].append({"ObjectIdentifier": template.object_id})
    template.aces.append(
        ACE(
            principal_sid=enroll_group.object_id,
            principal_type="Group",
            right_name="Enroll",
        )
    )

    graph.add_edge(root.object_id, "RootCAFor", graph.domain_sid)
    graph.add_edge(enterprise.object_id, "EnterpriseCAFor", graph.domain_sid)
    graph.add_edge(enterprise.object_id, "IssuedSignedBy", root.object_id)
    graph.add_edge(ntauth.object_id, "NTAuthStoreFor", graph.domain_sid)
    graph.add_edge(root.object_id, "TrustedForNTAuth", ntauth.object_id)
    graph.add_edge(template.object_id, "PublishedTo", enterprise.object_id)
    graph.add_edge(enroll_group.object_id, "Enroll", template.object_id)
    return root, enterprise, ntauth, template, enroll_group


def plant_adcs_esc1_path(graph: ADGraph) -> PlantedPath:
    root, enterprise, ntauth, template, enroll_group = _add_phase4_adcs(graph)
    enrollee = _pick_user(graph)
    _add_group_member(graph, enrollee, enroll_group)
    domain_admins = _domain_admins(graph)

    cypher = (
        f"MATCH (da:Group {{name: '{_node_name(graph, domain_admins.object_id)}'}}) "
        "WITH da "
        f"MATCH p=(u:User {{name: '{_node_name(graph, enrollee.object_id)}'}})"
        "-[:MemberOf*1..]->(g:Group)"
        "-[:Enroll]->(t:CertTemplate)"
        "-[:PublishedTo]->(ca:EnterpriseCA) "
        "WHERE t.enrolleesuppliessubject = true "
        "AND t.authenticationenabled = true "
        "RETURN p, t, ca, g, u, da"
    )
    critical_nodes = [
        enrollee.object_id,
        enroll_group.object_id,
        template.object_id,
        enterprise.object_id,
        domain_admins.object_id,
    ]
    planted = PlantedPath(
        template_id="t4_adcs_esc1",
        tier=4,
        category="path_finding",
        description=(
            f"{_node_name(graph, enrollee.object_id)} can enroll through "
            f"{_node_name(graph, enroll_group.object_id)} against the ESC1-style template "
            f"{_node_name(graph, template.object_id)} published by "
            f"{_node_name(graph, enterprise.object_id)}."
        ),
        source_node=enrollee.object_id,
        target_node=domain_admins.object_id,
        path_edges=[
            (enrollee.object_id, "MemberOf", enroll_group.object_id),
            (enroll_group.object_id, "Enroll", template.object_id),
            (template.object_id, "PublishedTo", enterprise.object_id),
        ],
        verification_cypher=cypher,
        mitre=["T1649", "T1550.003"],
        metadata={
            "scenario_family": "adcs_esc1",
            "critical_nodes": critical_nodes,
            "required_capabilities": ["adcs_enumeration", "template_abuse"],
            "diagnostic_nodes": [root.object_id, ntauth.object_id],
            "template_version": PHASE4_TEMPLATE_VERSION,
        },
    )
    graph.plant(planted)
    return planted


def plant_rbcd_delegation_path(graph: ADGraph) -> PlantedPath:
    delegate_user = _pick_user(graph)
    target = _pick_computer(graph)
    target.extra.setdefault("AllowedToAct", []).append(
        {"ObjectIdentifier": delegate_user.object_id, "ObjectType": "User"}
    )
    graph.add_edge(delegate_user.object_id, "AllowedToAct", target.object_id, planted=True)
    target.aces.append(
        ACE(
            principal_sid=delegate_user.object_id,
            principal_type="User",
            right_name="GenericWrite",
        )
    )

    cypher = (
        f"MATCH p=(u:User {{name: '{_node_name(graph, delegate_user.object_id)}'}})"
        f"-[:AllowedToAct]->(c:Computer {{name: '{_node_name(graph, target.object_id)}'}}) "
        "RETURN p"
    )
    critical_nodes = [delegate_user.object_id, target.object_id]
    planted = PlantedPath(
        template_id="t4_rbcd_delegation",
        tier=4,
        category="path_finding",
        description=(
            f"{_node_name(graph, delegate_user.object_id)} has a planted RBCD-style "
            f"AllowedToAct path to {_node_name(graph, target.object_id)}."
        ),
        source_node=delegate_user.object_id,
        target_node=target.object_id,
        path_edges=[(delegate_user.object_id, "AllowedToAct", target.object_id)],
        verification_cypher=cypher,
        mitre=["T1558.003"],
        metadata={
            "scenario_family": "delegation_rbcd",
            "critical_nodes": critical_nodes,
            "required_capabilities": ["delegation_enumeration", "rbcd_abuse"],
            "template_version": PHASE4_TEMPLATE_VERSION,
            "affected_service": target.properties["name"],
        },
    )
    graph.plant(planted)
    return planted


def plant_adcs_to_delegation_composite(
    graph: ADGraph,
    adcs_path: PlantedPath,
    delegation_path: PlantedPath,
) -> PlantedPath:
    domain_admins = _domain_admins(graph)
    source = adcs_path.source_node
    template = adcs_path.metadata["critical_nodes"][2]
    ca = adcs_path.metadata["critical_nodes"][3]
    delegation_target = delegation_path.target_node
    now = _FIXED_EPOCH - graph.rng.randint(0, 86_400)
    bridge_sid = graph.sid_alloc.alloc("svc_phase4_bridge")
    bridge = ADNode(
        object_id=bridge_sid,
        node_type="User",
        properties={
            "name": f"SVC_PHASE4_BRIDGE@{graph.domain}",
            "displayname": "Phase 4 Bridge Service",
            "samaccountname": "svc_phase4_bridge",
            "userprincipalname": f"svc_phase4_bridge@{graph.domain.lower()}",
            "distinguishedname": f"CN=svc_phase4_bridge,CN=Users,{graph.dn.domain_root()}",
            "domain": graph.domain,
            "domainsid": graph.domain_sid,
            "enabled": True,
            "hasspn": True,
            "unconstraineddelegation": False,
            "trustedtoauth": True,
            "passwordnotreqd": False,
            "dontreqpreauth": False,
            "pwdlastset": now,
            "lastlogon": now,
            "lastlogontimestamp": now,
            "admincount": False,
            "highvalue": False,
            "isaclprotected": False,
            "department": "IT",
        },
        extra={
            "AllowedToDelegate": [
                {"ObjectIdentifier": delegation_target, "ObjectType": "Computer"}
            ],
            "SPNTargets": [
                {"ComputerSID": delegation_target, "Port": 445, "Service": "cifs"}
            ],
            "HasSIDHistory": [],
        },
    )
    graph.add_node(bridge)
    bridge.aces.append(
        ACE(
            principal_sid=source,
            principal_type="User",
            right_name="GenericWrite",
        )
    )
    graph.add_edge(bridge.object_id, "AllowedToDelegate", delegation_target)
    graph.add_edge(source, "GenericWrite", bridge.object_id, planted=True)

    cypher = (
        f"MATCH (da:Group {{name: '{_node_name(graph, domain_admins.object_id)}'}}) "
        "WITH da "
        f"MATCH p=(u:User {{name: '{_node_name(graph, source)}'}})"
        "-[:MemberOf*1..]->(:Group)-[:Enroll]->(t:CertTemplate)"
        "-[:PublishedTo]->(ca:EnterpriseCA), "
        f"q=(u)-[:GenericWrite]->(svc:User {{name: '{_node_name(graph, bridge.object_id)}'}})"
        "-[:AllowedToDelegate]->"
        f"(c:Computer {{name: '{_node_name(graph, delegation_target)}'}}) "
        "RETURN p, q, t, ca, svc, c, da"
    )
    critical_nodes = [
        source,
        template,
        ca,
        bridge.object_id,
        delegation_target,
        domain_admins.object_id,
    ]
    planted = PlantedPath(
        template_id="t5_adcs_to_delegation_composite",
        tier=5,
        category="path_finding",
        description=(
            f"{_node_name(graph, source)} can combine ESC1 certificate abuse with control of "
            f"{_node_name(graph, bridge.object_id)}, which can delegate to "
            f"{_node_name(graph, delegation_target)}."
        ),
        source_node=source,
        target_node=domain_admins.object_id,
        path_edges=[
            *adcs_path.path_edges,
            (source, "GenericWrite", bridge.object_id),
            (bridge.object_id, "AllowedToDelegate", delegation_target),
        ],
        verification_cypher=cypher,
        mitre=["T1649", "T1558.003", "T1550.003"],
        metadata={
            "scenario_family": "adcs_delegation_composite",
            "critical_nodes": critical_nodes,
            "required_capabilities": [
                "adcs_enumeration",
                "delegation_enumeration",
                "composite_path_reasoning",
            ],
            "template_version": PHASE4_TEMPLATE_VERSION,
            "component_templates": [adcs_path.template_id, delegation_path.template_id],
        },
    )
    graph.plant(planted)
    return planted
