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
from io import BytesIO
from pathlib import Path

from .graph import ADEdge, ADGraph, ADNode
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
    output_path.parent.mkdir(parents=True, exist_ok=True)
    zip_data = _build_zip(graph)
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
                edge_kind="TrustedBy",
                properties={"trust_type": "parent_child"},
            ),
            ADEdge(
                source=parent_domain_node.object_id,
                target=child_domain_node.object_id,
                edge_kind="TrustedBy",
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


def _trust_entry(domain_node: ADNode) -> dict:
    return {
        "TargetDomainSid": domain_node.properties["domainsid"],
        "TargetDomainName": domain_node.properties["domain"],
        "IsTransitive": True,
        "TrustDirection": "Bidirectional",
        "TrustType": "ParentChild",
    }


def serialize_to_dir(graph: ADGraph, output_dir: Path) -> list[Path]:
    """Serialize the graph to individual JSON files in a directory."""
    output_dir.mkdir(parents=True, exist_ok=True)
    files: list[Path] = []
    for file_type, nodes in _group_nodes(graph).items():
        data = _build_file_data(nodes, file_type)
        path = output_dir / f"{file_type}.json"
        path.write_text(json.dumps(data, indent=2))
        files.append(path)
    return files


def _build_zip(graph: ADGraph) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file_type, nodes in _group_nodes(graph).items():
            data = _build_file_data(nodes, file_type)
            info = zipfile.ZipInfo(f"{file_type}.json", date_time=(2024, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, json.dumps(data))
    return buf.getvalue()


def _group_nodes(graph: ADGraph) -> dict[str, list[ADNode]]:
    """Group nodes by their output file type."""
    groups: dict[str, list[ADNode]] = {ft: [] for ft in _VERSIONS}
    for node in graph._nodes.values():
        file_type = _NODE_TYPE_TO_FILE.get(node.node_type)
        if file_type:
            groups[file_type].append(node)
    return {k: v for k, v in groups.items() if v}


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
            "RightName": ace.right_name,
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
