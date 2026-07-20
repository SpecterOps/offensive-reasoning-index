"""Validate that planted graph relationships survive SharpHound serialization."""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field
from io import BytesIO

from .graph import ADGraph

Relationship = tuple[str, str, str]


@dataclass(frozen=True)
class ArchivePathCheck:
    template_id: str
    expected_edges: int
    missing_edges: tuple[Relationship, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.missing_edges


@dataclass(frozen=True)
class ArchiveValidationReport:
    path_checks: tuple[ArchivePathCheck, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.path_checks)

    @property
    def edge_references(self) -> int:
        return sum(check.expected_edges for check in self.path_checks)

    def require_valid(self) -> None:
        if self.ok:
            return
        details = []
        for check in self.path_checks:
            for source, edge, target in check.missing_edges:
                details.append(f"{check.template_id}: {source} -[{edge}]-> {target}")
        raise ValueError(
            "SharpHound archive is missing planted relationships:\n" + "\n".join(details)
        )


def validate_sharphound_zip(graph: ADGraph, archive: bytes) -> ArchiveValidationReport:
    """Validate every planted path edge against serialized SharpHound evidence."""
    relationships = _relationships_from_archive(archive)
    checks = []
    for planted in graph.planted_paths:
        expected = [
            *planted.path_edges,
            *planted.metadata.get("supporting_edges", []),
        ]
        missing = tuple(edge for edge in expected if edge not in relationships)
        checks.append(
            ArchivePathCheck(
                template_id=planted.template_id,
                expected_edges=len(expected),
                missing_edges=missing,
            )
        )
    return ArchiveValidationReport(tuple(checks))


def _relationships_from_archive(archive: bytes) -> set[Relationship]:
    records: list[dict] = []
    with zipfile.ZipFile(BytesIO(archive)) as zf:
        for name in zf.namelist():
            if name.endswith(".json"):
                records.extend(json.loads(zf.read(name)).get("data", []))

    relationships: set[Relationship] = set()
    object_ids = {record.get("ObjectIdentifier") for record in records}

    for record in records:
        target_id = record.get("ObjectIdentifier")
        if not target_id:
            continue

        for member in record.get("Members", []):
            relationships.add((member["ObjectIdentifier"], "MemberOf", target_id))
        for ace in record.get("Aces", []):
            relationships.add((ace["PrincipalSID"], ace["RightName"], target_id))
        for session in (record.get("Sessions") or {}).get("Results", []):
            relationships.add((target_id, "HasSession", session["UserSID"]))
        for delegated in record.get("AllowedToDelegate", []):
            relationships.add((target_id, "AllowedToDelegate", delegated["ObjectIdentifier"]))
        for principal in record.get("AllowedToAct", []):
            relationships.add((principal["ObjectIdentifier"], "AllowedToAct", target_id))
        contained_by = record.get("ContainedBy")
        if contained_by:
            relationships.add((contained_by["ObjectIdentifier"], "Contains", target_id))
        for link in record.get("Links", []):
            relationships.add((link["GUID"], "GPLink", target_id))
        for template in record.get("CertTemplates", []):
            relationships.add((template["ObjectIdentifier"], "PublishedTo", target_id))
        for trust in record.get("Trusts", []):
            trusted_id = trust.get("TargetDomainSid")
            if trusted_id in object_ids:
                relationships.add((target_id, "TrustedBy", trusted_id))

        changes = record.get("GPOChanges") or {}
        affected = [item["ObjectIdentifier"] for item in changes.get("AffectedComputers", [])]
        for change_field, edge_kind in {
            "LocalAdmins": "AdminTo",
            "RemoteDesktopUsers": "CanRDP",
            "DcomUsers": "ExecuteDCOM",
            "PSRemoteUsers": "CanPSRemote",
        }.items():
            for principal in changes.get(change_field, []):
                for computer_id in affected:
                    relationships.add((principal["ObjectIdentifier"], edge_kind, computer_id))

    return relationships
