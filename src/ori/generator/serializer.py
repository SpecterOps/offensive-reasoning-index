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
from dataclasses import asdict
from io import BytesIO
from pathlib import Path

from .graph import ADGraph, ADNode


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
            zf.writestr(f"{file_type}.json", json.dumps(data))
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
        "RemoteDesktopUsers": node.extra.get("RemoteDesktopUsers", {"Results": [], "Collected": True}),
        "DcomUsers": node.extra.get("DcomUsers", {"Results": [], "Collected": True}),
        "PSRemoteUsers": node.extra.get("PSRemoteUsers", {"Results": [], "Collected": True}),
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
        "GPOChanges": node.extra.get("GPOChanges", {
            "LocalAdmins": [],
            "RemoteDesktopUsers": [],
            "DcomUsers": [],
            "PSRemoteUsers": [],
            "AffectedComputers": [],
        }),
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
