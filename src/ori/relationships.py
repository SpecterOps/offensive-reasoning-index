"""Canonical relationship contracts for generated and live BloodHound edges."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, get_args

RELATIONSHIP_CONTRACT_VERSION = "1.0"
SHARPHOUND_PROFILE = "bloodhound-ce-9.1.0-sharphound-v5-v6"

RelationshipKind = Literal[
    "MemberOf",
    "HasMember",
    "AdminTo",
    "CanRDP",
    "CanPSRemote",
    "ExecuteDCOM",
    "HasSession",
    "GenericAll",
    "GenericWrite",
    "WriteOwner",
    "WriteDacl",
    "AllExtendedRights",
    "ForceChangePassword",
    "AddMember",
    "AddSelf",
    "Owns",
    "DCSync",
    "GetChanges",
    "GetChangesAll",
    "AllowedToDelegate",
    "AllowedToAct",
    "Contains",
    "SameForestTrust",
    "Enroll",
    "AutoEnroll",
    "PublishedTo",
    "IssuedSignedBy",
    "EnterpriseCAFor",
    "RootCAFor",
    "TrustedForNTAuth",
    "NTAuthStoreFor",
    "GPLink",
    "GPOAffectedByContainer",
    "HasSIDHistory",
]
SupportClassification = Literal["supported", "derived", "internal_only", "unsupported"]
WireCarrier = Literal[
    "members",
    "gpo_change",
    "sessions",
    "ace",
    "allowed_to_delegate",
    "allowed_to_act",
    "contained_by",
    "trust",
    "cert_templates",
    "sid_history",
    "derived",
    "none",
]

ALL_NODE_TYPES = frozenset(
    {
        "User",
        "Computer",
        "Group",
        "Domain",
        "OU",
        "Container",
        "GPO",
        "RootCA",
        "EnterpriseCA",
        "AIACA",
        "NTAuthStore",
        "CertTemplate",
    }
)


@dataclass(frozen=True)
class RelationshipContract:
    canonical_kind: RelationshipKind
    wire_carrier: WireCarrier
    wire_value: str | None
    live_kinds: tuple[str, ...]
    source_types: frozenset[str]
    target_types: frozenset[str]
    support: SupportClassification

    def accepts_endpoints(self, source_type: str, target_type: str) -> bool:
        return source_type in self.source_types and target_type in self.target_types


def _contract(
    kind: RelationshipKind,
    carrier: WireCarrier,
    *,
    wire_value: str | None = None,
    live_kinds: tuple[str, ...] | None = None,
    source_types: frozenset[str] = ALL_NODE_TYPES,
    target_types: frozenset[str] = ALL_NODE_TYPES,
    support: SupportClassification = "supported",
) -> RelationshipContract:
    return RelationshipContract(
        kind, carrier, wire_value, live_kinds or (kind,), source_types, target_types, support
    )


_USERS_GROUPS = frozenset({"User", "Group"})
_PRINCIPALS = frozenset({"User", "Computer", "Group"})
_CONTAINERS = frozenset({"Domain", "OU"})

RELATIONSHIP_CONTRACTS: dict[str, RelationshipContract] = {
    "MemberOf": _contract("MemberOf", "members", target_types=frozenset({"Group"})),
    "HasMember": _contract("HasMember", "derived", support="derived"),
    "AdminTo": _contract(
        "AdminTo",
        "gpo_change",
        wire_value="LocalAdmins",
        source_types=_USERS_GROUPS,
        target_types=frozenset({"Computer"}),
    ),
    "CanRDP": _contract(
        "CanRDP",
        "gpo_change",
        wire_value="RemoteDesktopUsers",
        source_types=_USERS_GROUPS,
        target_types=frozenset({"Computer"}),
    ),
    "CanPSRemote": _contract(
        "CanPSRemote",
        "gpo_change",
        wire_value="PSRemoteUsers",
        source_types=_USERS_GROUPS,
        target_types=frozenset({"Computer"}),
    ),
    "ExecuteDCOM": _contract(
        "ExecuteDCOM",
        "gpo_change",
        wire_value="DcomUsers",
        source_types=_USERS_GROUPS,
        target_types=frozenset({"Computer"}),
    ),
    "HasSession": _contract(
        "HasSession",
        "sessions",
        source_types=frozenset({"Computer"}),
        target_types=frozenset({"User"}),
    ),
    "GenericAll": _contract("GenericAll", "ace", wire_value="GenericAll"),
    "GenericWrite": _contract("GenericWrite", "ace", wire_value="GenericWrite"),
    "WriteOwner": _contract("WriteOwner", "ace", wire_value="WriteOwner"),
    "WriteDacl": _contract("WriteDacl", "ace", wire_value="WriteDacl"),
    "AllExtendedRights": _contract("AllExtendedRights", "ace", wire_value="AllExtendedRights"),
    "ForceChangePassword": _contract(
        "ForceChangePassword",
        "ace",
        wire_value="ForceChangePassword",
        source_types=_PRINCIPALS,
        target_types=frozenset({"User"}),
    ),
    "AddMember": _contract(
        "AddMember", "ace", wire_value="AddMember", target_types=frozenset({"Group"})
    ),
    "AddSelf": _contract("AddSelf", "ace", wire_value="AddSelf", target_types=frozenset({"Group"})),
    "Owns": _contract("Owns", "ace", wire_value="Owns"),
    "DCSync": _contract("DCSync", "derived", support="derived"),
    "GetChanges": _contract(
        "GetChanges", "ace", wire_value="GetChanges", target_types=frozenset({"Domain"})
    ),
    "GetChangesAll": _contract(
        "GetChangesAll", "ace", wire_value="GetChangesAll", target_types=frozenset({"Domain"})
    ),
    "AllowedToDelegate": _contract(
        "AllowedToDelegate", "allowed_to_delegate", target_types=frozenset({"Computer"})
    ),
    "AllowedToAct": _contract(
        "AllowedToAct", "allowed_to_act", target_types=frozenset({"Computer"})
    ),
    "Contains": _contract("Contains", "contained_by", source_types=_CONTAINERS),
    "SameForestTrust": _contract(
        "SameForestTrust",
        "trust",
        wire_value="ParentChild",
        source_types=frozenset({"Domain"}),
        target_types=frozenset({"Domain"}),
    ),
    "Enroll": _contract(
        "Enroll", "ace", wire_value="Enroll", target_types=frozenset({"CertTemplate"})
    ),
    "AutoEnroll": _contract(
        "AutoEnroll", "ace", wire_value="AutoEnroll", target_types=frozenset({"CertTemplate"})
    ),
    "PublishedTo": _contract(
        "PublishedTo",
        "cert_templates",
        source_types=frozenset({"CertTemplate"}),
        target_types=frozenset({"EnterpriseCA"}),
    ),
    "IssuedSignedBy": _contract("IssuedSignedBy", "none", support="internal_only"),
    "EnterpriseCAFor": _contract("EnterpriseCAFor", "none", support="internal_only"),
    "RootCAFor": _contract("RootCAFor", "none", support="internal_only"),
    "TrustedForNTAuth": _contract("TrustedForNTAuth", "none", support="internal_only"),
    "NTAuthStoreFor": _contract("NTAuthStoreFor", "none", support="internal_only"),
    "GPLink": _contract(
        "GPLink", "contained_by", source_types=frozenset({"GPO"}), target_types=_CONTAINERS
    ),
    "GPOAffectedByContainer": _contract("GPOAffectedByContainer", "derived", support="derived"),
    "HasSIDHistory": _contract(
        "HasSIDHistory",
        "sid_history",
        source_types=_PRINCIPALS,
        target_types=_PRINCIPALS,
    ),
}

# A spelling correction is distinct from a relationship whose live semantics changed.
LEGACY_SPELLING_ALIASES: dict[str, RelationshipKind] = {"WriteDACL": "WriteDacl"}
LEGACY_SEMANTIC_ALIASES: dict[str, RelationshipKind] = {"TrustedBy": "SameForestTrust"}


def canonical_relationship_kind(kind: str) -> RelationshipKind:
    """Return the canonical kind for a known canonical or explicit legacy name."""
    canonical = LEGACY_SPELLING_ALIASES.get(kind, LEGACY_SEMANTIC_ALIASES.get(kind, kind))
    if canonical not in RELATIONSHIP_CONTRACTS:
        raise ValueError(f"Unknown relationship kind: {kind}")
    return canonical  # type: ignore[return-value]


def relationship_contract(kind: str) -> RelationshipContract:
    return RELATIONSHIP_CONTRACTS[canonical_relationship_kind(kind)]


def live_relationship_kinds(kind: str) -> tuple[str, ...]:
    return relationship_contract(kind).live_kinds


def ace_wire_right(kind: str) -> str:
    """Return the canonical SharpHound ACE right for a graph relationship."""
    contract = relationship_contract(kind)
    if contract.wire_carrier != "ace" or contract.wire_value is None:
        raise ValueError(f"Relationship is not represented by a SharpHound ACE: {kind}")
    return contract.wire_value


def canonical_ace_kind(right_name: str) -> RelationshipKind:
    """Resolve a canonical ACE right, rejecting unknown and non-ACE wire values."""
    canonical = canonical_relationship_kind(right_name)
    contract = RELATIONSHIP_CONTRACTS[canonical]
    if contract.wire_carrier != "ace" or contract.wire_value != right_name:
        raise ValueError(f"Non-canonical or unsupported SharpHound ACE right: {right_name}")
    return canonical


def _assert_registry_complete() -> None:
    declared = set(get_args(RelationshipKind))
    registered = set(RELATIONSHIP_CONTRACTS)
    if declared != registered:
        raise RuntimeError(
            f"Relationship registry mismatch: missing={declared - registered}, "
            f"extra={registered - declared}"
        )


_assert_registry_complete()
