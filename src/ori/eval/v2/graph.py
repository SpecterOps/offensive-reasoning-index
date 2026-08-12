"""Canonical graph snapshots for ORI evaluation protocol v2.

The archive snapshot is the scorer's immutable graph identity.  It is derived
from the exact SharpHound archive that produced the benchmark manifest, not
from task answers or model-visible metadata.
"""

from __future__ import annotations

import json
import zipfile
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from io import BytesIO
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from ori.relationships import canonical_ace_kind, canonical_relationship_kind

from .fingerprint import canonical_sha256
from .schema import (
    EdgeWitness,
    EntityRef,
    GraphFactRegistry,
    PropertyFact,
    StrictModel,
)

GRAPH_SNAPSHOT_VERSION = "ori-graph-snapshot-v2"
LIVE_GRAPH_VERIFICATION_VERSION = "ori-live-graph-verification-v2"

# The digest binds stable, solver-assertable graph properties. BloodHound adds
# collection timestamps, tier tags, ownership flags, and relationship
# bookkeeping during ingest; those are deliberately excluded from both archive
# and live projections. Stable properties remain in the corpus-wide fact
# registry even when they are not required by a current oracle so truthful
# additional context can be attested without teaching the scorer a task-specific
# exception.
SCORER_RELEVANT_PROPERTY_KEYS = frozenset(
    {
        "admincount",
        "authenticationenabled",
        "certificateapplicationpolicy",
        "displayname",
        "ekus",
        "enabled",
        "enrolleesuppliessubject",
        "hasspn",
        "highvalue",
        "isdc",
        "name",
        "nosecurityextension",
        "operatingsystem",
        "requiresmanagerapproval",
        "schemaversion",
        "trustedtoauth",
        "unconstraineddelegation",
    }
)
NORMALIZED_ENTITY_METADATA_KEYS = frozenset(
    {
        "istierzero",
        "published-to-cas",
        "system_tags",
    }
)
NORMALIZED_EDGE_METADATA_KEYS = frozenset(
    {
        "inheritancehash",
        "isacl",
        "isinherited",
        "isprimarygroup",
        "lastseen",
    }
)
CASE_INSENSITIVE_PROPERTY_VALUE_KEYS = frozenset(
    {
        "certificateapplicationpolicy",
        "displayname",
        "ekus",
        "kind",
        "name",
        "operatingsystem",
    }
)
CE_NORMALIZED_ARTIFACTS = (
    "CE local-group nodes and their deterministic incident edges included",
    "node collection timestamps excluded",
    "node ownership and tier tags excluded",
    "non-semantic node metadata excluded by allowlist",
    "stable list-valued properties projected as scalar membership facts",
    "relationship collection and ACL bookkeeping excluded",
)
_CE_LOCAL_GROUP_RIDS = frozenset({"544", "555", "562", "580"})
_CE_LOCAL_GROUP_FIELDS = {
    "LocalAdmins": "544",
    "RemoteDesktopUsers": "555",
    "DcomUsers": "562",
    "PSRemoteUsers": "580",
}
_V2_CE_RELATIONSHIPS = frozenset(
    {
        "CoerceToTGT",
        "LocalToComputer",
        "MemberOfLocalGroup",
        "RemoteInteractiveLogonRight",
    }
)

_FILE_OBJECT_TYPES = {
    "users": "User",
    "computers": "Computer",
    "groups": "Group",
    "ous": "OU",
    "domains": "Domain",
    "gpos": "GPO",
    "containers": "Container",
    "enterprisecas": "EnterpriseCA",
    "rootcas": "RootCA",
    "aiacas": "AIACA",
    "ntauthstores": "NTAuthStore",
    "certtemplates": "CertTemplate",
}
_OBJECT_TYPE_FILE = {value: key for key, value in _FILE_OBJECT_TYPES.items()}
_LIVE_OBJECT_TYPES = frozenset({*_OBJECT_TYPE_FILE, "ADLocalGroup"})

_GPO_CHANGE_RELATIONSHIPS = {
    "LocalAdmins": "AdminTo",
    "RemoteDesktopUsers": "CanRDP",
    "DcomUsers": "ExecuteDCOM",
    "PSRemoteUsers": "CanPSRemote",
}


class GraphObject(StrictModel):
    """One benchmark-owned object and the scalar properties bound to its digest."""

    entity: EntityRef
    properties: tuple[PropertyFact, ...] = ()


class GraphSnapshot(StrictModel):
    """Canonical scorer-side view of a generated SharpHound archive."""

    schema_version: Literal["ori-graph-snapshot-v2"] = GRAPH_SNAPSHOT_VERSION
    manifest_schema_version: str
    product: str
    seed: int = Field(strict=True)
    domain: str
    domain_sid: str
    objects: tuple[GraphObject, ...]
    relationships: tuple[EdgeWitness, ...]
    relationship_counts: tuple[PropertyFact, ...]
    normalized_artifacts: tuple[str, ...] = ()
    graph_fingerprint: str

    @model_validator(mode="after")
    def fingerprint_matches_content(self) -> GraphSnapshot:
        expected = graph_snapshot_fingerprint(self)
        if self.graph_fingerprint != expected:
            raise ValueError(
                f"graph fingerprint mismatch: declared={self.graph_fingerprint} computed={expected}"
            )
        return self

    @property
    def entities(self) -> tuple[EntityRef, ...]:
        return tuple(item.entity for item in self.objects)

    @property
    def edge_keys(self) -> frozenset[tuple[str, str, str]]:
        return frozenset(
            (edge.source_id, edge.relationship, edge.target_id) for edge in self.relationships
        )

    def entity(self, object_id: str) -> EntityRef:
        for item in self.objects:
            if item.entity.object_id == object_id:
                return item.entity
        raise KeyError(f"graph object not found: {object_id}")


_GRAPH_FACT_REGISTRY_CACHE: dict[str, GraphFactRegistry] = {}


def _fact_key(*values: Any) -> str:
    return json.dumps(
        values,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def normalized_property_value(key: str, value: Any) -> Any:
    if (
        isinstance(value, str)
        and key.casefold() in CASE_INSENSITIVE_PROPERTY_VALUE_KEYS
    ):
        return " ".join(value.split()).casefold()
    return value


def edge_fact_key(edge: EdgeWitness) -> str:
    return _fact_key(
        edge.source_id.casefold(),
        edge.relationship.casefold(),
        edge.target_id.casefold(),
    )


def edge_property_fact_key(edge: EdgeWitness, fact: PropertyFact) -> str:
    return _fact_key(
        edge.source_id.casefold(),
        edge.relationship.casefold(),
        edge.target_id.casefold(),
        fact.key.casefold(),
        normalized_property_value(fact.key, fact.value),
    )


def entity_property_fact_key(
    entity_id: str,
    key: str,
    value: Any,
) -> str:
    return _fact_key(
        entity_id.casefold(),
        key.casefold(),
        normalized_property_value(key, value),
    )


def build_graph_fact_registry(snapshot: GraphSnapshot) -> GraphFactRegistry:
    """Build one corpus-wide sealed fact registry without per-task duplication."""

    cached = _GRAPH_FACT_REGISTRY_CACHE.get(snapshot.graph_fingerprint)
    if cached is not None:
        return cached
    edge_keys = tuple(
        sorted({edge_fact_key(edge) for edge in snapshot.relationships})
    )
    edge_property_facts = tuple(
        sorted(
            {
                edge_property_fact_key(edge, property_fact)
                for edge in snapshot.relationships
                for property_fact in edge.properties
            }
        )
    )
    entity_property_facts = tuple(
        sorted(
            {
                entity_property_fact_key(
                    item.entity.object_id,
                    property_fact.key,
                    property_fact.value,
                )
                for item in snapshot.objects
                for property_fact in item.properties
            }
        )
    )
    payload = {
        "edge_keys": edge_keys,
        "edge_property_facts": edge_property_facts,
        "entity_property_facts": entity_property_facts,
        "registry_fingerprint": "0" * 64,
    }
    payload["registry_fingerprint"] = canonical_sha256(
        payload,
        exclude_fields=("registry_fingerprint",),
    )
    registry = GraphFactRegistry.model_validate(payload)
    _GRAPH_FACT_REGISTRY_CACHE[snapshot.graph_fingerprint] = registry
    return registry


class LiveGraphVerification(StrictModel):
    """Immutable receipt for one bounded live-graph projection."""

    schema_version: Literal["ori-live-graph-verification-v2"] = (
        LIVE_GRAPH_VERIFICATION_VERSION
    )
    expected_graph_fingerprint: str
    observed_graph_fingerprint: str
    page_size: int = Field(strict=True, gt=0)
    object_queries: int = Field(strict=True, ge=0)
    relationship_queries: int = Field(strict=True, ge=0)
    object_count: int = Field(strict=True, ge=0)
    relationship_count: int = Field(strict=True, ge=0)
    normalized_artifacts: tuple[str, ...]
    verification_fingerprint: str

    @model_validator(mode="after")
    def receipt_is_valid(self) -> LiveGraphVerification:
        expected = canonical_sha256(
            self,
            exclude_fields=("verification_fingerprint",),
        )
        if self.verification_fingerprint != expected:
            raise ValueError("live graph verification fingerprint mismatch")
        return self


def graph_snapshot_fingerprint(snapshot: GraphSnapshot | Mapping[str, Any]) -> str:
    """Fingerprint every semantic graph field while excluding the digest itself."""

    return canonical_sha256(snapshot, exclude_fields=("graph_fingerprint",))


def _property_sort_key(fact: PropertyFact) -> tuple[str, str]:
    return (
        fact.key.casefold(),
        json.dumps(
            fact.value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
    )


def _scalar_properties(properties: Mapping[str, Any]) -> tuple[PropertyFact, ...]:
    facts: list[PropertyFact] = []
    for raw_key, value in properties.items():
        key = str(raw_key)
        if key.casefold() not in SCORER_RELEVANT_PROPERTY_KEYS:
            continue
        if isinstance(value, (str, int, float, bool)):
            facts.append(
                PropertyFact(
                    key=key,
                    value=normalized_property_value(key, value),
                )
            )
            continue
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            facts.extend(
                PropertyFact(
                    key=key,
                    value=normalized_property_value(key, item),
                )
                for item in value
                if isinstance(item, (str, int, float, bool))
            )
    return tuple(sorted(set(facts), key=_property_sort_key))


def _object_properties(
    properties: Mapping[str, Any],
    *,
    object_type: str,
) -> tuple[PropertyFact, ...]:
    return tuple(
        sorted(
            {
                *_scalar_properties(properties),
                PropertyFact(key="kind", value=object_type),
            },
            key=_property_sort_key,
        )
    )


def _aliases(properties: Mapping[str, Any], object_id: str) -> tuple[str, ...]:
    candidates: set[str] = set()
    for key in (
        "name",
        "samaccountname",
        "userprincipalname",
        "displayname",
        "distinguishedname",
    ):
        value = properties.get(key)
        if isinstance(value, str) and value.strip() and value.strip() != object_id:
            candidates.add(value.strip())

    canonical_name = properties.get("name")
    domain = properties.get("domain")
    if (
        isinstance(canonical_name, str)
        and isinstance(domain, str)
        and canonical_name.upper().endswith(f"@{domain.upper()}")
    ):
        candidates.add(canonical_name[: -(len(domain) + 1)])

    return tuple(sorted(candidates, key=lambda value: (value.casefold(), value)))


def _entity_ref(
    record: Mapping[str, Any],
    *,
    object_type: str,
) -> EntityRef:
    object_id = str(record.get("ObjectIdentifier") or "").strip()
    if not object_id:
        raise ValueError(f"{object_type} record is missing ObjectIdentifier")
    properties = record.get("Properties")
    if not isinstance(properties, Mapping):
        raise ValueError(f"{object_type} {object_id} is missing Properties")
    canonical_name = properties.get("name")
    domain = properties.get("domain")
    return EntityRef(
        object_id=object_id,
        object_type=object_type,
        domain=str(domain).strip() if isinstance(domain, str) and domain.strip() else None,
        role="benchmark_object",
        canonical_name=(
            str(canonical_name).strip()
            if isinstance(canonical_name, str) and canonical_name.strip()
            else None
        ),
        aliases=_aliases(properties, object_id),
    )


def _edge(source: Any, relationship: Any, target: Any) -> EdgeWitness:
    source_id = str(source or "").strip()
    target_id = str(target or "").strip()
    if not source_id or not target_id:
        raise ValueError(
            f"relationship {relationship!r} has an empty endpoint: {source_id!r} -> {target_id!r}"
        )
    relationship_name = str(relationship)
    try:
        canonical = canonical_relationship_kind(relationship_name)
    except ValueError:
        if relationship_name not in _V2_CE_RELATIONSHIPS:
            raise
        canonical = relationship_name
    return EdgeWitness(
        source_id=source_id,
        relationship=canonical,
        target_id=target_id,
    )


def _trust_edges(
    source_domain_id: str,
    trust: Mapping[str, Any],
    known_object_ids: set[str],
) -> Iterable[EdgeWitness]:
    target_domain_id = str(trust.get("TargetDomainSid") or "").strip()
    if not target_domain_id or target_domain_id not in known_object_ids:
        return ()
    if str(trust.get("TrustType") or "").casefold() != "parentchild":
        raise ValueError(f"unsupported SharpHound trust type: {trust.get('TrustType')!r}")

    direction = str(trust.get("TrustDirection") or "").casefold()
    outbound = _edge(source_domain_id, "SameForestTrust", target_domain_id)
    inbound = _edge(target_domain_id, "SameForestTrust", source_domain_id)
    if direction == "bidirectional":
        return (outbound, inbound)
    if direction == "outbound":
        return (outbound,)
    if direction == "inbound":
        return (inbound,)
    raise ValueError(f"unsupported SharpHound trust direction: {trust.get('TrustDirection')!r}")


def _archive_records(archive: bytes) -> list[tuple[str, Mapping[str, Any]]]:
    records: list[tuple[str, Mapping[str, Any]]] = []
    with zipfile.ZipFile(BytesIO(archive)) as bundle:
        for filename in sorted(bundle.namelist()):
            if not filename.endswith(".json"):
                continue
            payload = json.loads(bundle.read(filename))
            meta = payload.get("meta") or {}
            file_type = str(meta.get("type") or Path(filename).stem).casefold()
            object_type = _FILE_OBJECT_TYPES.get(file_type)
            if object_type is None:
                raise ValueError(f"unsupported SharpHound file type {file_type!r} in {filename}")
            data = payload.get("data")
            if not isinstance(data, list):
                raise ValueError(f"SharpHound file {filename} has no data list")
            for record in data:
                if not isinstance(record, Mapping):
                    raise ValueError(f"SharpHound file {filename} contains a non-object record")
                records.append((object_type, record))
    if not records:
        raise ValueError("SharpHound archive contains no graph records")
    return records


def _relationships(
    records: Iterable[tuple[str, Mapping[str, Any]]],
    *,
    include_ce_projection: bool = True,
) -> tuple[EdgeWitness, ...]:
    materialized = tuple(records)
    known_object_ids = {
        str(record.get("ObjectIdentifier"))
        for _, record in materialized
        if record.get("ObjectIdentifier")
    }
    if include_ce_projection:
        known_object_ids.update(
            f"{record.get('ObjectIdentifier')}-{rid}"
            for object_type, record in materialized
            if object_type == "Computer" and record.get("ObjectIdentifier")
            for rid in _CE_LOCAL_GROUP_RIDS
        )
    edges: dict[tuple[str, str, str], EdgeWitness] = {}
    domain_ids = {
        str(record.get("ObjectIdentifier"))
        for object_type, record in materialized
        if object_type == "Domain" and record.get("ObjectIdentifier")
    }
    domains_by_dn = {
        str((record.get("Properties") or {}).get("distinguishedname") or "").casefold(): str(
            record.get("ObjectIdentifier")
        )
        for object_type, record in materialized
        if object_type == "Domain"
        and record.get("ObjectIdentifier")
        and (record.get("Properties") or {}).get("distinguishedname")
    }

    def add(edge: EdgeWitness) -> None:
        edges[(edge.source_id, edge.relationship, edge.target_id)] = edge

    for object_type, record in materialized:
        target_id = str(record.get("ObjectIdentifier") or "").strip()
        if not target_id:
            continue

        for member in record.get("Members") or ():
            add(_edge(member.get("ObjectIdentifier"), "MemberOf", target_id))
        for ace in record.get("Aces") or ():
            add(
                _edge(
                    ace.get("PrincipalSID"),
                    canonical_ace_kind(ace.get("RightName")),
                    target_id,
                )
            )
        for session in (record.get("Sessions") or {}).get("Results") or ():
            add(_edge(target_id, "HasSession", session.get("UserSID")))
        for delegated in record.get("AllowedToDelegate") or ():
            add(_edge(target_id, "AllowedToDelegate", delegated.get("ObjectIdentifier")))
        for principal in record.get("AllowedToAct") or ():
            add(_edge(principal.get("ObjectIdentifier"), "AllowedToAct", target_id))
        contained_by = record.get("ContainedBy")
        if contained_by:
            add(_edge(contained_by.get("ObjectIdentifier"), "Contains", target_id))
        for link in record.get("Links") or ():
            add(_edge(link.get("GUID"), "GPLink", target_id))
        for template in record.get("CertTemplates") or ():
            add(_edge(template.get("ObjectIdentifier"), "PublishedTo", target_id))
        for trust in record.get("Trusts") or ():
            for edge in _trust_edges(target_id, trust, known_object_ids):
                add(edge)
        for principal in record.get("HasSIDHistory") or ():
            add(_edge(target_id, "HasSIDHistory", principal.get("ObjectIdentifier")))

        changes = record.get("GPOChanges") or {}
        affected = [item.get("ObjectIdentifier") for item in changes.get("AffectedComputers") or ()]
        for field, relationship in _GPO_CHANGE_RELATIONSHIPS.items():
            for principal in changes.get(field) or ():
                for computer_id in affected:
                    add(
                        _edge(
                            principal.get("ObjectIdentifier"),
                            relationship,
                            computer_id,
                        )
                    )

        if include_ce_projection:
            primary_group = str(record.get("PrimaryGroupSID") or "").strip()
            if primary_group and primary_group in known_object_ids:
                add(_edge(target_id, "MemberOf", primary_group))

            properties = record.get("Properties")
            props = properties if isinstance(properties, Mapping) else {}
            domain_id = str(props.get("domainsid") or "").strip()
            if (
                object_type == "OU"
                and not record.get("ContainedBy")
                and domain_id in domain_ids
            ):
                distinguished_name = str(
                    props.get("distinguishedname") or ""
                ).strip()
                parent_dn = (
                    distinguished_name.split(",", 1)[1].casefold()
                    if "," in distinguished_name
                    else ""
                )
                parent_id = domains_by_dn.get(parent_dn)
                if parent_id is not None:
                    add(_edge(parent_id, "Contains", target_id))

            if (
                object_type == "Computer"
                and props.get("unconstraineddelegation") is True
                and domain_id in domain_ids
            ):
                add(_edge(target_id, "CoerceToTGT", domain_id))

            if object_type == "Computer":
                for field, rid in _CE_LOCAL_GROUP_FIELDS.items():
                    local_group_id = f"{target_id}-{rid}"
                    add(_edge(local_group_id, "LocalToComputer", target_id))
                    collection = record.get(field)
                    local_group = (
                        collection if isinstance(collection, Mapping) else {}
                    )
                    for principal in local_group.get("Results") or ():
                        principal_id = principal.get("ObjectIdentifier")
                        if principal_id in known_object_ids:
                            add(
                                _edge(
                                    principal_id,
                                    "MemberOfLocalGroup",
                                    local_group_id,
                                )
                            )

                for assignment in record.get("UserRights") or ():
                    if (
                        str(assignment.get("Privilege") or "")
                        != "SeRemoteInteractiveLogonRight"
                    ):
                        continue
                    for principal in assignment.get("Results") or ():
                        principal_id = principal.get("ObjectIdentifier")
                        if principal_id in known_object_ids:
                            add(
                                _edge(
                                    principal_id,
                                    "RemoteInteractiveLogonRight",
                                    target_id,
                                )
                            )

    return tuple(
        edges[key]
        for key in sorted(
            edges,
            key=lambda item: (item[0].casefold(), item[1].casefold(), item[2].casefold()),
        )
    )


def _ce_local_group_objects(
    records: Iterable[tuple[str, Mapping[str, Any]]],
) -> tuple[GraphObject, ...]:
    """Project the four deterministic CE local-group nodes for every computer.

    BloodHound CE materializes these nodes during SharpHound ingest and exposes
    them to both direct Cypher and MCP path queries. They therefore belong in
    the scorer identity and edge registries even though they are not standalone
    records in the source archive.
    """

    objects: list[GraphObject] = []
    for object_type, record in records:
        if object_type != "Computer":
            continue
        computer_id = str(record.get("ObjectIdentifier") or "").strip()
        if not computer_id:
            continue
        for rid in sorted(_CE_LOCAL_GROUP_RIDS):
            object_id = f"{computer_id}-{rid}"
            objects.append(
                GraphObject(
                    entity=EntityRef(
                        object_id=object_id,
                        object_type="ADLocalGroup",
                        role="ce_derived_local_group",
                        canonical_name=object_id,
                    ),
                    properties=(
                        PropertyFact(key="kind", value="ADLocalGroup"),
                    ),
                )
            )
    return tuple(
        sorted(objects, key=lambda item: item.entity.object_id.casefold())
    )


def build_archive_snapshot(
    archive: bytes | Path,
    manifest: Mapping[str, Any],
    *,
    product: str | None = None,
) -> GraphSnapshot:
    """Build and validate the immutable scorer-side graph snapshot."""

    archive_bytes = archive.read_bytes() if isinstance(archive, Path) else archive
    records = _archive_records(archive_bytes)
    archive_objects = tuple(
        sorted(
            (
                GraphObject(
                    entity=_entity_ref(record, object_type=object_type),
                    properties=_object_properties(
                        record.get("Properties") or {},
                        object_type=object_type,
                    ),
                )
                for object_type, record in records
            ),
            key=lambda item: item.entity.object_id.casefold(),
        )
    )
    objects = tuple(
        sorted(
            (*archive_objects, *_ce_local_group_objects(records)),
            key=lambda item: item.entity.object_id.casefold(),
        )
    )
    object_ids = [item.entity.object_id for item in objects]
    duplicates = sorted(object_id for object_id, count in Counter(object_ids).items() if count > 1)
    if duplicates:
        raise ValueError(f"duplicate graph object identifiers: {duplicates}")

    raw_relationships = _relationships(records, include_ce_projection=False)
    relationships = _relationships(records)
    dangling = sorted(
        {
            endpoint
            for edge in relationships
            for endpoint in (edge.source_id, edge.target_id)
            if endpoint not in set(object_ids)
        }
    )
    if dangling:
        raise ValueError(f"archive relationships reference unknown objects: {dangling}")

    counts = Counter(edge.relationship for edge in relationships)
    count_facts = tuple(
        PropertyFact(key=relationship, value=count)
        for relationship, count in sorted(counts.items())
    )
    metadata = manifest.get("metadata") or {}
    resolved_product = (
        product or metadata.get("benchmark_name") or metadata.get("generator_profile") or "unknown"
    )
    payload = {
        "schema_version": GRAPH_SNAPSHOT_VERSION,
        "manifest_schema_version": str(manifest.get("schema_version") or ""),
        "product": str(resolved_product),
        "seed": manifest.get("seed"),
        "domain": str(manifest.get("domain") or ""),
        "domain_sid": str(manifest.get("domain_sid") or ""),
        "objects": objects,
        "relationships": relationships,
        "relationship_counts": count_facts,
        "normalized_artifacts": CE_NORMALIZED_ARTIFACTS,
        "graph_fingerprint": "0" * 64,
    }
    payload["graph_fingerprint"] = canonical_sha256(
        payload,
        exclude_fields=("graph_fingerprint",),
    )
    snapshot = GraphSnapshot.model_validate(payload)

    expected_nodes = (manifest.get("stats") or {}).get("total_nodes")
    if isinstance(expected_nodes, int) and expected_nodes != len(archive_objects):
        raise ValueError(
            f"archive node count {len(archive_objects)} does not match manifest "
            f"count {expected_nodes}"
        )
    expected_relationships = (manifest.get("relationship_summary") or {}).get("total_relationships")
    if isinstance(expected_relationships, int) and expected_relationships != len(
        raw_relationships
    ):
        raise ValueError(
            f"archive relationship count {len(raw_relationships)} does not match "
            f"manifest count {expected_relationships}"
        )
    return snapshot


def _live_data(result: Any) -> Mapping[str, Any]:
    raw = result.raw if isinstance(result.raw, Mapping) else {}
    data = raw.get("data", raw)
    return data if isinstance(data, Mapping) else {}


def _live_count(result: Any) -> int:
    if not result.success:
        raise ValueError(f"live graph count query failed: {result.error}")
    literals = _live_data(result).get("literals")
    if not isinstance(literals, Sequence) or isinstance(literals, (str, bytes)):
        raise ValueError("live graph count query returned no literals")
    counts = {
        literal.get("value")
        for literal in literals
        if isinstance(literal, Mapping)
        and literal.get("key") == "count"
        and isinstance(literal.get("value"), int)
        and not isinstance(literal.get("value"), bool)
        and literal.get("value") >= 0
    }
    if len(counts) != 1:
        raise ValueError("live graph count query returned an ambiguous count")
    return int(counts.pop())


def _live_nodes(result: Any) -> tuple[Mapping[str, Any], ...]:
    if not result.success:
        raise ValueError(f"live graph object query failed: {result.error}")
    nodes = _live_data(result).get("nodes") or {}
    if not isinstance(nodes, Mapping):
        raise ValueError("live graph object query returned invalid nodes")
    if any(not isinstance(node, Mapping) for node in nodes.values()):
        raise ValueError("live graph object query returned a non-object node")
    return tuple(nodes.values())


def _live_edges(result: Any) -> tuple[EdgeWitness, ...]:
    if not result.success:
        raise ValueError(f"live graph relationship query failed: {result.error}")
    data = _live_data(result)
    nodes = data.get("nodes") or {}
    edges = data.get("edges") or ()
    if not isinstance(nodes, Mapping):
        raise ValueError("live graph relationship query returned invalid nodes")
    if not isinstance(edges, Sequence) or isinstance(edges, (str, bytes)):
        raise ValueError("live graph relationship query returned invalid edges")
    node_ids: dict[str, str] = {}
    for key, node in nodes.items():
        if not isinstance(node, Mapping):
            raise ValueError("live graph relationship query returned a non-object node")
        object_id = str(
            node.get("objectId")
            or (node.get("properties") or {}).get("objectid")
            or ""
        ).strip()
        if not object_id:
            raise ValueError("live graph relationship endpoint has no object ID")
        node_ids[str(key)] = object_id

    projected: list[EdgeWitness] = []
    for edge in edges:
        if not isinstance(edge, Mapping):
            raise ValueError("live graph relationship query returned a non-object edge")
        source = node_ids.get(str(edge.get("source")))
        target = node_ids.get(str(edge.get("target")))
        relationship = edge.get("kind") or edge.get("label")
        if not source or not target or not relationship:
            raise ValueError("live graph relationship has incomplete endpoints or kind")
        try:
            canonical = canonical_relationship_kind(str(relationship))
        except ValueError:
            # Unknown live kinds remain in the digest so a CE upgrade or an
            # unexpected incident attack-path relationship invalidates the run
            # instead of being silently normalized away.
            canonical = str(relationship).strip()
        projected.append(
            EdgeWitness(
                source_id=source,
                relationship=canonical,
                target_id=target,
            )
        )
    return tuple(projected)


async def _run_live_query(bhce: Any, query: str) -> Any:
    """Use bounded infrastructure recovery for harness-owned graph reads."""

    resilient = getattr(bhce, "run_cypher_resilient", None)
    if callable(resilient):
        return await resilient(query)
    return await bhce.run_cypher(query)


def _assert_live_identity(node: Mapping[str, Any], expected: EntityRef) -> None:
    properties = node.get("properties")
    props = properties if isinstance(properties, Mapping) else {}
    object_id = str(node.get("objectId") or props.get("objectid") or "").strip()
    object_type = str(node.get("kind") or "").strip()
    canonical_name = str(node.get("label") or props.get("name") or "").strip()
    domain = str(props.get("domain") or "").strip()
    mismatches: list[str] = []
    if object_id != expected.object_id:
        mismatches.append("object ID")
    if object_type.casefold() != expected.object_type.casefold():
        mismatches.append("object type")
    if expected.canonical_name and canonical_name.casefold() != expected.canonical_name.casefold():
        mismatches.append("canonical name")
    if expected.domain and domain.casefold() != expected.domain.casefold():
        mismatches.append("domain")
    if mismatches:
        raise ValueError(
            f"live graph identity mismatch for {expected.object_id}: "
            + ", ".join(mismatches)
        )


async def collect_live_snapshot(
    bhce: Any,
    expected: GraphSnapshot,
    *,
    page_size: int = 500,
) -> tuple[GraphSnapshot, LiveGraphVerification]:
    """Project the controlled graph with bounded, stable, paginated queries."""

    if page_size <= 0:
        raise ValueError("live graph page size must be positive")
    expected_by_type: dict[str, dict[str, GraphObject]] = {}
    for item in expected.objects:
        expected_by_type.setdefault(item.entity.object_type, {})[
            item.entity.object_id
        ] = item

    observed_objects: dict[str, GraphObject] = {}
    observed_edges: dict[tuple[str, str, str], EdgeWitness] = {}
    object_queries = 0
    relationship_queries = 0

    for object_type in sorted(_LIVE_OBJECT_TYPES):
        count_result = await _run_live_query(
            bhce,
            f"MATCH (n:{object_type}) RETURN count(n) AS count LIMIT 1"
        )
        object_queries += 1
        count = _live_count(count_result)
        expected_items = expected_by_type.get(object_type, {})
        if count != len(expected_items):
            raise ValueError(
                f"live {object_type} count {count} does not match archive "
                f"count {len(expected_items)}"
            )
        for offset in range(0, count, page_size):
            query = (
                f"MATCH (n:{object_type}) RETURN n ORDER BY n.objectid "
                f"SKIP {offset} LIMIT {page_size}"
            )
            result = await _run_live_query(bhce, query)
            object_queries += 1
            for node in _live_nodes(result):
                object_id = str(
                    node.get("objectId")
                    or (node.get("properties") or {}).get("objectid")
                    or ""
                ).strip()
                expected_item = expected_items.get(object_id)
                if expected_item is None:
                    raise ValueError(
                        f"live graph contains unexpected {object_type} {object_id!r}"
                    )
                _assert_live_identity(node, expected_item.entity)
                properties = node.get("properties")
                props = properties if isinstance(properties, Mapping) else {}
                observed_objects[object_id] = GraphObject(
                    entity=expected_item.entity,
                    properties=_object_properties(
                        props,
                        object_type=expected_item.entity.object_type,
                    ),
                )

        for direction in ("outgoing", "incoming"):
            if direction == "outgoing":
                pattern = f"(a:{object_type})-[r]->(b)"
                order = "a.objectid, type(r), b.objectid"
            else:
                pattern = f"(a)-[r]->(b:{object_type})"
                order = "a.objectid, type(r), b.objectid"
            count_result = await _run_live_query(
                bhce,
                f"MATCH {pattern} RETURN count(r) AS count LIMIT 1"
            )
            relationship_queries += 1
            edge_count = _live_count(count_result)
            extracted = 0
            for offset in range(0, edge_count, page_size):
                result = await _run_live_query(
                    bhce,
                    f"MATCH {pattern} RETURN a, r, b ORDER BY {order} "
                    f"SKIP {offset} LIMIT {page_size}"
                )
                relationship_queries += 1
                page_edges = _live_edges(result)
                extracted += len(page_edges)
                for edge in page_edges:
                    observed_edges[
                        (edge.source_id, edge.relationship, edge.target_id)
                    ] = edge
            if extracted != edge_count:
                raise ValueError(
                    f"live {object_type} {direction} relationship pagination "
                    f"returned {extracted} edges for count {edge_count}"
                )

    if set(observed_objects) != {
        item.entity.object_id for item in expected.objects
    }:
        raise ValueError("live graph object pagination did not cover the archive")
    expected_ids = set(observed_objects)
    incident_edges = tuple(
        edge
        for edge in observed_edges.values()
        if edge.source_id in expected_ids or edge.target_id in expected_ids
    )
    unexpected_endpoints = sorted(
        {
            endpoint
            for edge in incident_edges
            for endpoint in (edge.source_id, edge.target_id)
            if endpoint not in expected_ids
        }
    )
    if unexpected_endpoints:
        raise ValueError(
            "live graph has relationships incident to non-benchmark objects: "
            + ", ".join(unexpected_endpoints[:10])
        )
    relationships = tuple(
        sorted(
            (
                edge
                for edge in incident_edges
                if edge.source_id in expected_ids and edge.target_id in expected_ids
            ),
            key=lambda edge: (
                edge.source_id.casefold(),
                edge.relationship.casefold(),
                edge.target_id.casefold(),
            ),
        )
    )
    counts = Counter(edge.relationship for edge in relationships)
    payload = {
        "schema_version": GRAPH_SNAPSHOT_VERSION,
        "manifest_schema_version": expected.manifest_schema_version,
        "product": expected.product,
        "seed": expected.seed,
        "domain": expected.domain,
        "domain_sid": expected.domain_sid,
        "objects": tuple(
            observed_objects[key]
            for key in sorted(observed_objects, key=str.casefold)
        ),
        "relationships": relationships,
        "relationship_counts": tuple(
            PropertyFact(key=relationship, value=count)
            for relationship, count in sorted(counts.items())
        ),
        "normalized_artifacts": CE_NORMALIZED_ARTIFACTS,
        "graph_fingerprint": "0" * 64,
    }
    payload["graph_fingerprint"] = canonical_sha256(
        payload,
        exclude_fields=("graph_fingerprint",),
    )
    observed = GraphSnapshot.model_validate(payload)
    receipt_payload = {
        "expected_graph_fingerprint": expected.graph_fingerprint,
        "observed_graph_fingerprint": observed.graph_fingerprint,
        "page_size": page_size,
        "object_queries": object_queries,
        "relationship_queries": relationship_queries,
        "object_count": len(observed.objects),
        "relationship_count": len(observed.relationships),
        "normalized_artifacts": CE_NORMALIZED_ARTIFACTS,
        "verification_fingerprint": "0" * 64,
    }
    receipt_payload["verification_fingerprint"] = canonical_sha256(
        {
            "schema_version": LIVE_GRAPH_VERIFICATION_VERSION,
            **receipt_payload,
        },
        exclude_fields=("verification_fingerprint",),
    )
    return observed, LiveGraphVerification.model_validate(receipt_payload)


def require_live_graph_match(
    expected: GraphSnapshot,
    observed: GraphSnapshot,
) -> None:
    """Fail closed when any scorer-relevant live graph byte has drifted."""

    if expected.graph_fingerprint == observed.graph_fingerprint:
        return
    expected_edges = expected.edge_keys
    observed_edges = observed.edge_keys
    raise ValueError(
        "live graph fingerprint mismatch: "
        f"expected={expected.graph_fingerprint} "
        f"observed={observed.graph_fingerprint} "
        f"missing_edges={len(expected_edges - observed_edges)} "
        f"extra_edges={len(observed_edges - expected_edges)}"
    )
