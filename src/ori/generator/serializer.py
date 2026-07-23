"""
SharpHound JSON serializer.

Produces BH CE-ingestible files in the exact format that BloodHound CE
expects from SharpHound collectors. Each file has:

  { "data": [...], "meta": { "type": ..., "count": N, "methods": 0, "version": 6 } }

Reference: BloodHound CE incoming_models.go and ingest API docs.
"""

from __future__ import annotations

import json
import zipfile
from copy import deepcopy
from io import BytesIO
from pathlib import Path

from ori.relationships import RELATIONSHIP_CONTRACTS, ace_wire_right, relationship_contract

from .graph import ACE, ADEdge, ADGraph, ADNode, TypedPrincipal
from .phase4_v2 import Phase4V2Forest

# SharpHound ingest version numbers per object type
_VERSIONS: dict[str, int] = {
    "users": 6,
    "computers": 6,
    "groups": 6,
    "ous": 6,
    "domains": 5,
    "gpos": 5,
    "containers": 6,
    "enterprisecas": 6,
    "rootcas": 6,
    "aiacas": 6,
    "ntauthstores": 6,
    "certtemplates": 6,
}

_NODE_TYPE_TO_FILE: dict[str, str] = {
    "User": "users",
    "Computer": "computers",
    "Group": "groups",
    "OU": "ous",
    "Domain": "domains",
    "GPO": "gpos",
    "Container": "containers",
    "EnterpriseCA": "enterprisecas",
    "RootCA": "rootcas",
    "AIACA": "aiacas",
    "NTAuthStore": "ntauthstores",
    "CertTemplate": "certtemplates",
}


def serialize_to_zip(graph: ADGraph, output_path: Path) -> Path:
    """Serialize the graph to a SharpHound-compatible zip file."""
    from .archive_validation import validate_sharphound_zip

    output_path.parent.mkdir(parents=True, exist_ok=True)
    zip_data = _build_zip(graph)
    validate_sharphound_zip(graph, zip_data).require_valid()
    output_path.write_bytes(zip_data)
    return output_path


def serialize_forest_to_zip(forest: Phase4V2Forest, output_path: Path) -> Path:
    """Serialize a Phase 4B/v2 parent/child forest to one SharpHound-compatible zip."""

    combined = _combined_forest_graph(forest)
    return serialize_to_zip(combined, output_path)


def _combined_forest_graph(forest: Phase4V2Forest) -> ADGraph:
    combined = ADGraph(forest.parent_domain, seed=forest.seed)
    combined._nodes = {
        **forest.parent_graph._nodes,
        **forest.child_graph._nodes,
    }
    combined._edges = [*forest.parent_graph.get_edges(), *forest.child_graph.get_edges()]
    combined.planted_paths = [*forest.parent_graph.planted_paths, *forest.child_graph.planted_paths]
    parent_domain_node = forest.parent_graph.nodes_by_type("Domain")[0]
    child_domain_node = forest.child_graph.nodes_by_type("Domain")[0]
    _add_bidirectional_domain_trusts(parent_domain_node, child_domain_node)
    combined._edges.extend(
        [
            ADEdge(
                source=child_domain_node.object_id,
                target=parent_domain_node.object_id,
                edge_kind="SameForestTrust",
                properties={"trust_type": "parent_child"},
            ),
            ADEdge(
                source=parent_domain_node.object_id,
                target=child_domain_node.object_id,
                edge_kind="SameForestTrust",
                properties={"trust_type": "parent_child"},
            ),
        ]
    )
    return combined


def _add_bidirectional_domain_trusts(parent_domain_node: ADNode, child_domain_node: ADNode) -> None:
    parent_domain_node.extra.setdefault("Trusts", [])
    child_domain_node.extra.setdefault("Trusts", [])
    trust_to_child = _trust_entry(child_domain_node)
    trust_to_parent = _trust_entry(parent_domain_node)
    if trust_to_child not in parent_domain_node.extra["Trusts"]:
        parent_domain_node.extra["Trusts"].append(trust_to_child)
    if trust_to_parent not in child_domain_node.extra["Trusts"]:
        child_domain_node.extra["Trusts"].append(trust_to_parent)


def _trust_entry(domain_node: ADNode, direction: str = "Bidirectional") -> dict:
    return {
        "TargetDomainSid": domain_node.properties["domainsid"],
        "TargetDomainName": domain_node.properties["domain"],
        "IsTransitive": True,
        "TrustDirection": direction,
        "TrustType": "ParentChild",
    }


def serialize_to_dir(graph: ADGraph, output_dir: Path) -> list[Path]:
    """Serialize the graph to individual JSON files in a directory."""
    output_dir.mkdir(parents=True, exist_ok=True)
    files: list[Path] = []
    for file_type, nodes in _group_nodes(project_nodes_for_sharphound(graph)).items():
        data = _build_file_data(nodes, file_type)
        path = output_dir / f"{file_type}.json"
        path.write_text(json.dumps(data, indent=2))
        files.append(path)
    return files


def _build_zip(graph: ADGraph) -> bytes:
    projected_nodes = project_nodes_for_sharphound(graph)
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file_type, nodes in _group_nodes(projected_nodes).items():
            data = _build_file_data(nodes, file_type)
            info = zipfile.ZipInfo(f"{file_type}.json", date_time=(2024, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, json.dumps(data))
    return buf.getvalue()


def _group_nodes(nodes: dict[str, ADNode]) -> dict[str, list[ADNode]]:
    """Group nodes by their output file type."""
    groups: dict[str, list[ADNode]] = {ft: [] for ft in _VERSIONS}
    for node in nodes.values():
        file_type = _NODE_TYPE_TO_FILE.get(node.node_type)
        if file_type:
            groups[file_type].append(node)
    return {k: v for k, v in groups.items() if v}


_ACL_EDGE_KINDS = {
    kind for kind, contract in RELATIONSHIP_CONTRACTS.items() if contract.wire_carrier == "ace"
}

_GPO_CHANGE_FIELDS = {
    "AdminTo": "LocalAdmins",
    "CanRDP": "RemoteDesktopUsers",
    "CanPSRemote": "PSRemoteUsers",
    "ExecuteDCOM": "DcomUsers",
}


def project_nodes_for_sharphound(graph: ADGraph) -> dict[str, ADNode]:
    """Materialize ADEdges into SharpHound records without mutating the graph."""
    nodes = deepcopy(graph._nodes)
    gpo_relationships = _existing_gpo_change_relationships(nodes)
    carrier_index = 0

    for edge in graph.get_edges():
        source = nodes.get(edge.source)
        target = nodes.get(edge.target)
        if source is None or target is None:
            continue

        handled = False
        contract = relationship_contract(edge.edge_kind)
        if contract.support == "supported" and not contract.accepts_endpoints(
            source.node_type, target.node_type
        ):
            raise ValueError(
                f"Invalid endpoints for {edge.edge_kind}: {source.node_type} -> {target.node_type}"
            )
        if edge.edge_kind == "MemberOf" and target.node_type == "Group":
            handled = True
            _append_unique(
                target.extra.setdefault("Members", []),
                {"ObjectIdentifier": source.object_id, "ObjectType": source.node_type},
            )
        elif edge.edge_kind in _ACL_EDGE_KINDS:
            handled = True
            ace = ACE(source.object_id, source.node_type, ace_wire_right(edge.edge_kind))
            if ace not in target.aces:
                target.aces.append(ace)
        elif edge.edge_kind == "AllowedToDelegate" and target.node_type == "Computer":
            handled = True
            _append_unique(
                source.extra.setdefault("AllowedToDelegate", []),
                {"ObjectIdentifier": target.object_id, "ObjectType": "Computer"},
            )
        elif edge.edge_kind == "AllowedToAct" and target.node_type == "Computer":
            handled = True
            _append_unique(
                target.extra.setdefault("AllowedToAct", []),
                {"ObjectIdentifier": source.object_id, "ObjectType": source.node_type},
            )
        elif (
            edge.edge_kind == "HasSession"
            and source.node_type == "Computer"
            and target.node_type == "User"
        ):
            handled = True
            sessions = source.extra.setdefault("Sessions", {"Results": [], "Collected": True})
            _append_unique(
                sessions["Results"],
                {"UserSID": target.object_id, "ComputerSID": source.object_id},
            )
        elif edge.edge_kind in _GPO_CHANGE_FIELDS:
            handled = True
            if source.node_type not in {"User", "Group"} or target.node_type != "Computer":
                _raise_invalid_planted_edge(edge, source, target)
                continue
            _project_computer_result(source, target, edge.edge_kind)
            relationship = (source.object_id, edge.edge_kind, target.object_id)
            if relationship not in gpo_relationships:
                carrier_index += 1
                carrier = _gpo_change_carrier(graph, source, target, edge.edge_kind, carrier_index)
                nodes[carrier.object_id] = carrier
                _register_domain_child(nodes, graph.domain_sid, carrier)
                gpo_relationships.add(relationship)
        elif edge.edge_kind == "GPLink":
            handled = True
            if source.node_type != "GPO" or target.node_type not in {"Domain", "OU"}:
                _raise_invalid_planted_edge(edge, source, target)
                continue
            _append_unique(
                target.extra.setdefault("Links", []),
                {"GUID": source.object_id, "IsEnforced": bool(edge.properties.get("enforced"))},
            )
        elif edge.edge_kind == "Contains" and source.node_type in {"Domain", "OU"}:
            handled = True
            target.contained_by = TypedPrincipal(source.object_id, source.node_type)
            _append_unique(
                source.extra.setdefault("ChildObjects", []),
                {"ObjectIdentifier": target.object_id, "ObjectType": target.node_type},
            )
        elif edge.edge_kind == "SameForestTrust":
            handled = True
            if source.node_type != "Domain" or target.node_type != "Domain":
                _raise_invalid_planted_edge(edge, source, target)
                continue
            _append_unique(source.extra.setdefault("Trusts", []), _trust_entry(target, "Outbound"))
        elif edge.edge_kind == "PublishedTo" and target.node_type == "EnterpriseCA":
            handled = True
            template_ref = {"ObjectIdentifier": source.object_id}
            _append_unique(target.extra.setdefault("CertTemplates", []), template_ref)
            _append_unique(target.extra.setdefault("EnabledCertTemplates", []), template_ref)
        elif edge.edge_kind == "HasSIDHistory":
            handled = True
            _append_unique(
                source.extra.setdefault("HasSIDHistory", []),
                {"ObjectIdentifier": target.object_id, "ObjectType": target.node_type},
            )

        is_public = any(edge.properties.get(flag) for flag in ("planted", "supporting", "official"))
        allowed_internal = contract.support in {"derived", "internal_only"} and not is_public
        if not handled and not allowed_internal:
            raise ValueError(
                f"{contract.support} edge has no SharpHound projection: "
                f"{source.node_type}({source.object_id}) -[{edge.edge_kind}]-> "
                f"{target.node_type}({target.object_id})"
            )

    return nodes


def _raise_invalid_planted_edge(edge: ADEdge, source: ADNode, target: ADNode) -> None:
    if edge.properties.get("planted"):
        raise ValueError(
            "Planted edge cannot be represented by SharpHound: "
            f"{source.node_type}({source.object_id}) -[{edge.edge_kind}]-> "
            f"{target.node_type}({target.object_id})"
        )


def _append_unique(items: list[dict], item: dict) -> None:
    if item not in items:
        items.append(item)


def _project_computer_result(source: ADNode, target: ADNode, edge_kind: str) -> None:
    field = _GPO_CHANGE_FIELDS[edge_kind]
    result = {"ObjectIdentifier": source.object_id, "ObjectType": source.node_type}
    collection = target.extra.setdefault(field, {"Results": [], "Collected": True})
    _append_unique(collection["Results"], result)
    if edge_kind != "CanRDP":
        return
    user_rights = target.extra.setdefault("UserRights", [])
    right = next(
        (
            entry
            for entry in user_rights
            if entry.get("Privilege") == "SeRemoteInteractiveLogonRight"
        ),
        None,
    )
    if right is None:
        right = {
            "Privilege": "SeRemoteInteractiveLogonRight",
            "Results": [],
            "Collected": True,
            "FailureReason": None,
            "LocalNames": [],
        }
        user_rights.append(right)
    _append_unique(right["Results"], result)


def _existing_gpo_change_relationships(
    nodes: dict[str, ADNode],
) -> set[tuple[str, str, str]]:
    relationships: set[tuple[str, str, str]] = set()
    for node in nodes.values():
        if node.node_type not in {"Domain", "OU"}:
            continue
        changes = node.extra.get("GPOChanges") or {}
        affected = [item.get("ObjectIdentifier") for item in changes.get("AffectedComputers", [])]
        for edge_kind, field in _GPO_CHANGE_FIELDS.items():
            for principal in changes.get(field, []):
                for target_id in affected:
                    relationships.add((principal.get("ObjectIdentifier"), edge_kind, target_id))
    return relationships


def _gpo_change_carrier(
    graph: ADGraph,
    source: ADNode,
    target: ADNode,
    edge_kind: str,
    index: int,
) -> ADNode:
    object_id = f"{graph.domain_sid}-9100{index:04d}"
    name = f"ORI-EDGE-{edge_kind.upper()}-{index:04d}"
    changes = {field: [] for field in _GPO_CHANGE_FIELDS.values()}
    changes[_GPO_CHANGE_FIELDS[edge_kind]] = [
        {"ObjectIdentifier": source.object_id, "ObjectType": source.node_type}
    ]
    changes["AffectedComputers"] = [
        {"ObjectIdentifier": target.object_id, "ObjectType": "Computer"}
    ]
    return ADNode(
        object_id=object_id,
        node_type="OU",
        properties={
            "name": f"{name}@{graph.domain}",
            "distinguishedname": f"OU={name},{graph.dn.domain_root()}",
            "domain": graph.domain,
            "domainsid": graph.domain_sid,
            "blocksinheritance": False,
            "highvalue": False,
            "isaclprotected": False,
        },
        contained_by=TypedPrincipal(graph.domain_sid, "Domain"),
        extra={"ChildObjects": [], "Links": [], "GPOChanges": changes},
    )


def _register_domain_child(nodes: dict[str, ADNode], domain_id: str, child: ADNode) -> None:
    domain = nodes.get(domain_id)
    if domain is None:
        return
    _append_unique(
        domain.extra.setdefault("ChildObjects", []),
        {"ObjectIdentifier": child.object_id, "ObjectType": child.node_type},
    )


def _build_file_data(nodes: list[ADNode], file_type: str) -> dict:
    return {
        "data": [_serialize_node(n) for n in nodes],
        "meta": {
            "type": file_type,
            "count": len(nodes),
            "methods": 0,
            "version": _VERSIONS.get(file_type, 6),
        },
    }


def _serialize_node(node: ADNode) -> dict:
    """Dispatch to the appropriate serializer based on node type."""
    serializers = {
        "User": _serialize_user,
        "Computer": _serialize_computer,
        "Group": _serialize_group,
        "OU": _serialize_ou,
        "Domain": _serialize_domain,
        "GPO": _serialize_gpo,
        "EnterpriseCA": _serialize_enterprise_ca,
        "RootCA": _serialize_root_ca,
        "NTAuthStore": _serialize_ntauth_store,
        "CertTemplate": _serialize_cert_template,
    }
    fn = serializers.get(node.node_type)
    if fn is None:
        raise ValueError(f"No serializer for node type: {node.node_type}")
    return fn(node)


def _aces(node: ADNode) -> list[dict]:
    return [
        {
            "PrincipalSID": ace.principal_sid,
            "PrincipalType": ace.principal_type,
            "RightName": ace_wire_right(ace.right_name),
            "IsInherited": ace.is_inherited,
        }
        for ace in node.aces
    ]


def _contained_by(node: ADNode) -> dict | None:
    if node.contained_by is None:
        return None
    return {
        "ObjectIdentifier": node.contained_by.object_id,
        "ObjectType": node.contained_by.object_type,
    }


def _serialize_user(node: ADNode) -> dict:
    p = node.properties
    return {
        "ObjectIdentifier": node.object_id,
        "Properties": {
            "domain": p["domain"],
            "name": p["name"],
            "distinguishedname": p["distinguishedname"],
            "domainsid": p["domainsid"],
            "samaccountname": p.get("samaccountname", ""),
            "userprincipalname": p.get("userprincipalname", ""),
            "displayname": p.get("displayname", ""),
            "enabled": p.get("enabled", True),
            "hasspn": p.get("hasspn", False),
            "unconstraineddelegation": p.get("unconstraineddelegation", False),
            "trustedtoauth": p.get("trustedtoauth", False),
            "passwordnotreqd": p.get("passwordnotreqd", False),
            "dontreqpreauth": p.get("dontreqpreauth", False),
            "pwdlastset": p.get("pwdlastset", 0),
            "lastlogon": p.get("lastlogon", 0),
            "lastlogontimestamp": p.get("lastlogontimestamp", 0),
            "admincount": p.get("admincount", False),
            "isaclprotected": p.get("isaclprotected", False),
            "highvalue": p.get("highvalue", False),
        },
        "PrimaryGroupSID": p.get("domainsid", "") + "-513",  # Domain Users
        "AllowedToDelegate": node.extra.get("AllowedToDelegate", []),
        "SPNTargets": node.extra.get("SPNTargets", []),
        "HasSIDHistory": node.extra.get("HasSIDHistory", []),
        "Aces": _aces(node),
        "IsACLProtected": p.get("isaclprotected", False),
        "IsDeleted": False,
        "ContainedBy": _contained_by(node),
    }


def _serialize_computer(node: ADNode) -> dict:
    p = node.properties
    return {
        "ObjectIdentifier": node.object_id,
        "Properties": {
            "domain": p["domain"],
            "name": p["name"],
            "distinguishedname": p["distinguishedname"],
            "domainsid": p["domainsid"],
            "samaccountname": p.get("samaccountname", ""),
            "enabled": p.get("enabled", True),
            "haslaps": p.get("haslaps", False),
            "unconstraineddelegation": p.get("unconstraineddelegation", False),
            "trustedtoauth": p.get("trustedtoauth", False),
            "isdc": p.get("isdc", False),
            "isaclprotected": p.get("isaclprotected", False),
            "highvalue": p.get("highvalue", False),
            "operatingsystem": p.get("operatingsystem", ""),
            "pwdlastset": p.get("pwdlastset", 0),
            "lastlogon": p.get("lastlogon", 0),
            "lastlogontimestamp": p.get("lastlogontimestamp", 0),
        },
        "PrimaryGroupSID": p.get("domainsid", "") + ("-516" if p.get("isdc") else "-515"),
        "AllowedToDelegate": node.extra.get("AllowedToDelegate", []),
        "AllowedToAct": node.extra.get("AllowedToAct", []),
        "HasSIDHistory": node.extra.get("HasSIDHistory", []),
        "Sessions": node.extra.get("Sessions", {"Results": [], "Collected": True}),
        "PrivilegedSessions": {"Results": [], "Collected": True},
        "RegistrySessions": {"Results": [], "Collected": True},
        "LocalAdmins": node.extra.get("LocalAdmins", {"Results": [], "Collected": True}),
        "RemoteDesktopUsers": node.extra.get(
            "RemoteDesktopUsers", {"Results": [], "Collected": True}
        ),
        "DcomUsers": node.extra.get("DcomUsers", {"Results": [], "Collected": True}),
        "PSRemoteUsers": node.extra.get("PSRemoteUsers", {"Results": [], "Collected": True}),
        "UserRights": node.extra.get("UserRights", []),
        "Aces": _aces(node),
        "IsACLProtected": p.get("isaclprotected", False),
        "IsDeleted": False,
        "ContainedBy": _contained_by(node),
    }


def _serialize_group(node: ADNode) -> dict:
    p = node.properties
    return {
        "ObjectIdentifier": node.object_id,
        "Properties": {
            "domain": p["domain"],
            "name": p["name"],
            "distinguishedname": p["distinguishedname"],
            "domainsid": p["domainsid"],
            "samaccountname": p.get("samaccountname", ""),
            "admincount": p.get("admincount", False),
            "highvalue": p.get("highvalue", False),
            "isaclprotected": p.get("isaclprotected", False),
        },
        "Members": node.extra.get("Members", []),
        "Aces": _aces(node),
        "IsACLProtected": p.get("isaclprotected", False),
        "IsDeleted": False,
        "ContainedBy": _contained_by(node),
    }


def _serialize_ou(node: ADNode) -> dict:
    p = node.properties
    return {
        "ObjectIdentifier": node.object_id,
        "Properties": {
            "domain": p["domain"],
            "name": p["name"],
            "distinguishedname": p["distinguishedname"],
            "domainsid": p["domainsid"],
            "blocksinheritance": p.get("blocksinheritance", False),
            "highvalue": p.get("highvalue", False),
            "isaclprotected": p.get("isaclprotected", False),
        },
        "ChildObjects": node.extra.get("ChildObjects", []),
        "Links": node.extra.get("Links", []),
        "GPOChanges": node.extra.get(
            "GPOChanges",
            {
                "LocalAdmins": [],
                "RemoteDesktopUsers": [],
                "DcomUsers": [],
                "PSRemoteUsers": [],
                "AffectedComputers": [],
            },
        ),
        "Aces": _aces(node),
        "IsACLProtected": p.get("isaclprotected", False),
    }


def _serialize_domain(node: ADNode) -> dict:
    p = node.properties
    return {
        "ObjectIdentifier": node.object_id,
        "Properties": {
            "domain": p["domain"],
            "name": p["name"],
            "distinguishedname": p["distinguishedname"],
            "domainsid": p["domainsid"],
            "functionallevel": p.get("functionallevel", "Windows Server 2016"),
            "highvalue": p.get("highvalue", True),
            "isaclprotected": p.get("isaclprotected", False),
        },
        "Trusts": node.extra.get("Trusts", []),
        "ChildObjects": node.extra.get("ChildObjects", []),
        "Links": node.extra.get("Links", []),
        "GPOChanges": node.extra.get(
            "GPOChanges",
            {
                "LocalAdmins": [],
                "RemoteDesktopUsers": [],
                "DcomUsers": [],
                "PSRemoteUsers": [],
                "AffectedComputers": [],
            },
        ),
        "Aces": _aces(node),
        "IsACLProtected": p.get("isaclprotected", False),
    }


def _serialize_gpo(node: ADNode) -> dict:
    p = node.properties
    return {
        "ObjectIdentifier": node.object_id,
        "Properties": {
            "domain": p["domain"],
            "name": p["name"],
            "distinguishedname": p["distinguishedname"],
            "domainsid": p["domainsid"],
            "highvalue": p.get("highvalue", False),
            "isaclprotected": p.get("isaclprotected", False),
        },
        "Aces": _aces(node),
        "IsACLProtected": p.get("isaclprotected", False),
    }


def _serialize_enterprise_ca(node: ADNode) -> dict:
    p = node.properties
    return {
        "ObjectIdentifier": node.object_id,
        "Properties": {
            "domain": p["domain"],
            "name": p["name"],
            "distinguishedname": p.get("distinguishedname", ""),
            "domainsid": p["domainsid"],
            "caname": p.get("caname", ""),
            "dnshostname": p.get("dnshostname", ""),
            "highvalue": p.get("highvalue", True),
        },
        "CertTemplates": node.extra.get("CertTemplates", []),
        "EnabledCertTemplates": node.extra.get("EnabledCertTemplates", []),
        "Aces": _aces(node),
        "IsACLProtected": p.get("isaclprotected", False),
    }


def _serialize_root_ca(node: ADNode) -> dict:
    p = node.properties
    return {
        "ObjectIdentifier": node.object_id,
        "Properties": {
            "domain": p["domain"],
            "name": p["name"],
            "domainsid": p["domainsid"],
            "caname": p.get("caname", ""),
            "highvalue": p.get("highvalue", True),
        },
        "Aces": _aces(node),
        "IsACLProtected": p.get("isaclprotected", False),
    }


def _serialize_ntauth_store(node: ADNode) -> dict:
    p = node.properties
    return {
        "ObjectIdentifier": node.object_id,
        "Properties": {
            "domain": p["domain"],
            "name": p["name"],
            "domainsid": p["domainsid"],
        },
        "Aces": _aces(node),
        "IsACLProtected": False,
    }


def _serialize_cert_template(node: ADNode) -> dict:
    p = node.properties
    return {
        "ObjectIdentifier": node.object_id,
        "Properties": {
            "domain": p["domain"],
            "name": p["name"],
            "domainsid": p["domainsid"],
            "displayname": p.get("displayname", ""),
            "validityperiod": p.get("validityperiod", "1 year"),
            "renewalperiod": p.get("renewalperiod", "6 weeks"),
            "schemaversion": str(p.get("schemaversion", 2)),
            "enrollmentflag": str(p.get("enrollmentflag", 0)),
            "requiresmanagerapproval": p.get("requiresmanagerapproval", False),
            "authenticationenabled": p.get("authenticationenabled", False),
            "nosecurityextension": p.get("nosecurityextension", False),
            "enrolleesuppliessubject": p.get("enrolleesuppliessubject", False),
            "subjectaltrequireupn": p.get("subjectaltrequireupn", False),
            "subjectaltrequiredns": p.get("subjectaltrequiredns", False),
            "subjectaltrequiredomaindns": p.get("subjectaltrequiredomaindns", False),
            "subjectaltrequireemail": p.get("subjectaltrequireemail", False),
            "ekus": p.get("ekus", []),
            "certificateapplicationpolicy": p.get("certificateapplicationpolicy", []),
            "highvalue": p.get("highvalue", False),
        },
        "Aces": _aces(node),
        "IsACLProtected": p.get("isaclprotected", False),
    }
