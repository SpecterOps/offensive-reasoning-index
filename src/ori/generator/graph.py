"""Core graph model for synthetic AD graph generation."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Literal


NodeType = Literal[
    "User", "Computer", "Group", "Domain", "OU", "Container", "GPO",
    "RootCA", "EnterpriseCA", "AIACA", "NTAuthStore", "CertTemplate",
]

EdgeKind = Literal[
    # Group/membership
    "MemberOf", "HasMember",
    # Admin / lateral movement
    "AdminTo", "CanRDP", "CanPSRemote", "ExecuteDCOM",
    # Sessions
    "HasSession",
    # ACL edges
    "GenericAll", "GenericWrite", "WriteOwner", "WriteDACL",
    "AllExtendedRights", "ForceChangePassword",
    "AddMember", "AddSelf",
    "Owns",
    "DCSync", "GetChanges", "GetChangesAll",
    # Delegation
    "AllowedToDelegate", "AllowedToAct",
    # Container / hierarchy
    "Contains",
    # Trust
    "TrustedBy",
    # ADCS
    "Enroll", "AutoEnroll",
    "PublishedTo", "IssuedSignedBy",
    "EnterpriseCAFor", "RootCAFor",
    "TrustedForNTAuth", "NTAuthStoreFor",
    # GPO
    "GPLink", "GPOAffectedByContainer",
    # SID History
    "HasSIDHistory",
]


@dataclass
class TypedPrincipal:
    object_id: str
    object_type: NodeType


@dataclass
class ACE:
    principal_sid: str
    principal_type: NodeType
    right_name: str
    is_inherited: bool = False


@dataclass
class ADNode:
    object_id: str          # SID or DN-based identifier
    node_type: NodeType
    properties: dict        # SharpHound Properties dict
    aces: list[ACE] = field(default_factory=list)
    contained_by: TypedPrincipal | None = None
    # Type-specific extras (Members for Group, Trusts for Domain, etc.)
    extra: dict = field(default_factory=dict)


@dataclass
class ADEdge:
    source: str             # ObjectIdentifier
    target: str             # ObjectIdentifier
    edge_kind: EdgeKind
    properties: dict = field(default_factory=dict)


@dataclass
class PlantedPath:
    """Documents a planted attack path for the ground truth manifest."""
    template_id: str
    tier: int
    category: str           # path_finding, enumeration, analysis, etc.
    description: str
    source_node: str        # ObjectIdentifier
    target_node: str        # ObjectIdentifier
    path_edges: list[tuple[str, str, str]]  # [(source, edge_kind, target), ...]
    verification_cypher: str
    mitre: list[str] = field(default_factory=list)


class SIDAllocator:
    """Allocates SIDs within a domain's SID space."""

    # Well-known RIDs for built-in groups
    WELL_KNOWN = {
        "Domain Admins": 512,
        "Domain Users": 513,
        "Domain Computers": 515,
        "Domain Controllers": 516,
        "Schema Admins": 518,
        "Enterprise Admins": 519,
        "Group Policy Creator Owners": 520,
        "Administrators": 544,
        "Backup Operators": 551,
        "Remote Desktop Users": 555,
        "Distributed COM Users": 562,
    }

    def __init__(self, domain_sid: str) -> None:
        self.domain_sid = domain_sid
        self._next_rid = 1100
        self._allocated: dict[str, str] = {}  # name → SID

    def get(self, name: str) -> str | None:
        """Return existing SID for name, or None if not yet allocated."""
        return self._allocated.get(name)

    def get_or_alloc(self, name: str) -> str:
        """Get existing SID for name or allocate a new one."""
        if name in self._allocated:
            return self._allocated[name]
        sid = self.alloc(name)
        return sid

    def alloc(self, name: str) -> str:
        """Allocate a new SID for a named object. Raises if already allocated — use get_or_alloc() for idempotent access."""
        if name in self._allocated:
            raise ValueError(f"SID already allocated for {name!r} — use get_or_alloc() for idempotent access")
        if name in self.WELL_KNOWN:
            rid = self.WELL_KNOWN[name]
        else:
            rid = self._next_rid
            self._next_rid += 1
        sid = f"{self.domain_sid}-{rid}"
        self._allocated[name] = sid
        return sid

    def alloc_anon(self) -> str:
        """Allocate a SID without associating it to a name."""
        sid = f"{self.domain_sid}-{self._next_rid}"
        self._next_rid += 1
        return sid


class DNBuilder:
    """Builds consistent Active Directory distinguished names."""

    def __init__(self, domain: str) -> None:
        # domain = "CORP.LOCAL" → dc_suffix = "DC=corp,DC=local"
        parts = domain.lower().split(".")
        self.dc_suffix = ",".join(f"DC={p}" for p in parts)
        self.domain = domain.upper()

    def user(self, name: str, ou_path: str = "CN=Users") -> str:
        return f"CN={name},{ou_path},{self.dc_suffix}"

    def computer(self, name: str, ou_path: str = "CN=Computers") -> str:
        return f"CN={name},{ou_path},{self.dc_suffix}"

    def group(self, name: str, ou_path: str = "CN=Users") -> str:
        return f"CN={name},{ou_path},{self.dc_suffix}"

    def ou(self, name: str, parent: str | None = None) -> str:
        if parent:
            return f"OU={name},{parent},{self.dc_suffix}"
        return f"OU={name},{self.dc_suffix}"

    def domain_root(self) -> str:
        return self.dc_suffix

    def gpo(self, guid: str) -> str:
        return f"CN={{{guid}}},CN=Policies,CN=System,{self.dc_suffix}"


class ADGraph:
    """Central graph model. All nodes and edges registered here."""

    def __init__(self, domain: str, seed: int | None = None) -> None:
        self.domain = domain.upper()
        self.seed = seed
        self.rng = random.Random(seed)
        self._nodes: dict[str, ADNode] = {}
        self._edges: list[ADEdge] = []
        self.planted_paths: list[PlantedPath] = []

        # Generate a stable domain SID
        a = self.rng.randint(1000000000, 9999999999)
        b = self.rng.randint(1000000000, 9999999999)
        c = self.rng.randint(100000000, 999999999)
        self.domain_sid = f"S-1-5-21-{a}-{b}-{c}"

        self.sid_alloc = SIDAllocator(self.domain_sid)
        self.dn = DNBuilder(domain)

    # --- Node management ---

    def add_node(self, node: ADNode) -> ADNode:
        if node.object_id in self._nodes:
            raise ValueError(f"Duplicate node: {node.object_id}")
        self._nodes[node.object_id] = node
        return node

    def get_node(self, object_id: str) -> ADNode | None:
        return self._nodes.get(object_id)

    def require_node(self, object_id: str) -> ADNode:
        node = self._nodes.get(object_id)
        if node is None:
            raise KeyError(f"Node not found: {object_id}")
        return node

    def nodes_by_type(self, node_type: NodeType) -> list[ADNode]:
        return [n for n in self._nodes.values() if n.node_type == node_type]

    # --- Edge management ---

    def add_edge(self, source: str, edge_kind: EdgeKind, target: str, **props) -> ADEdge:
        # Validate source and target exist
        if source not in self._nodes:
            raise KeyError(f"Edge source not found: {source}")
        if target not in self._nodes:
            raise KeyError(f"Edge target not found: {target}")
        edge = ADEdge(source=source, target=target, edge_kind=edge_kind, properties=props)
        self._edges.append(edge)
        return edge

    def get_edges(self) -> list[ADEdge]:
        return list(self._edges)

    def edges_from(self, source: str, edge_kind: EdgeKind | None = None) -> list[ADEdge]:
        return [
            e for e in self._edges
            if e.source == source and (edge_kind is None or e.edge_kind == edge_kind)
        ]

    def edges_to(self, target: str, edge_kind: EdgeKind | None = None) -> list[ADEdge]:
        return [
            e for e in self._edges
            if e.target == target and (edge_kind is None or e.edge_kind == edge_kind)
        ]

    # --- Planted path tracking ---

    def plant(self, path: PlantedPath) -> None:
        self.planted_paths.append(path)

    # --- Convenience ---

    def node_count(self) -> int:
        return len(self._nodes)

    def edge_count(self) -> int:
        return len(self._edges)

    def all_sids(self) -> set[str]:
        return set(self._nodes.keys())
