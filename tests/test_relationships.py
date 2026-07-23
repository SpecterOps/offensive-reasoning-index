from typing import get_args

import pytest

from ori.generator.archive_validation import _trust_relationships
from ori.generator.graph import ACE, ADGraph, ADNode, EdgeKind
from ori.generator.serializer import _aces
from ori.relationships import (
    LEGACY_SEMANTIC_ALIASES,
    LEGACY_SPELLING_ALIASES,
    RELATIONSHIP_CONTRACTS,
    canonical_ace_kind,
    canonical_relationship_kind,
    relationship_contract,
)


def test_every_edge_kind_has_exactly_one_contract() -> None:
    assert set(get_args(EdgeKind)) == set(RELATIONSHIP_CONTRACTS)
    assert {contract.canonical_kind for contract in RELATIONSHIP_CONTRACTS.values()} == set(
        RELATIONSHIP_CONTRACTS
    )


def test_legacy_spelling_and_semantic_aliases_are_distinct() -> None:
    assert LEGACY_SPELLING_ALIASES == {"WriteDACL": "WriteDacl"}
    assert LEGACY_SEMANTIC_ALIASES == {"TrustedBy": "SameForestTrust"}
    assert canonical_relationship_kind("WriteDACL") == "WriteDacl"
    assert canonical_relationship_kind("TrustedBy") == "SameForestTrust"


def test_unknown_relationship_kind_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown relationship kind"):
        canonical_relationship_kind("InventedRelationship")


def test_graph_normalizes_explicit_legacy_alias_at_insertion() -> None:
    graph = ADGraph("TEST.LOCAL", seed=1)
    source = graph.add_node(ADNode("S-1-1", "User", {}))
    target = graph.add_node(ADNode("S-1-2", "Group", {}))

    edge = graph.add_edge(source.object_id, "WriteDACL", target.object_id)

    assert edge.edge_kind == "WriteDacl"


def test_ace_serialization_emits_canonical_wire_right() -> None:
    node = ADNode(
        "S-1-2",
        "Group",
        {},
        aces=[ACE("S-1-1", "User", "WriteDACL")],
    )

    assert _aces(node)[0]["RightName"] == "WriteDacl"


def test_archive_ace_reconstruction_requires_canonical_wire_right() -> None:
    assert canonical_ace_kind("WriteDacl") == "WriteDacl"
    with pytest.raises(ValueError, match="Non-canonical"):
        canonical_ace_kind("WriteDACL")


@pytest.mark.parametrize(
    ("direction", "expected"),
    [
        ("Outbound", {("A", "SameForestTrust", "B")}),
        ("Inbound", {("B", "SameForestTrust", "A")}),
        (
            "Bidirectional",
            {
                ("A", "SameForestTrust", "B"),
                ("B", "SameForestTrust", "A"),
            },
        ),
    ],
)
def test_parent_child_trust_reconstruction_is_oriented(direction, expected) -> None:
    trust = {"TrustType": "ParentChild", "TrustDirection": direction}
    assert _trust_relationships("A", "B", trust) == expected


def test_contracts_expose_endpoint_and_support_classification() -> None:
    trust = relationship_contract("SameForestTrust")
    assert trust.accepts_endpoints("Domain", "Domain")
    assert not trust.accepts_endpoints("User", "Domain")
    assert trust.support == "supported"
    assert relationship_contract("IssuedSignedBy").support == "internal_only"
    assert relationship_contract("DCSync").support == "derived"
