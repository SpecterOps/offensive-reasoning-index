"""Tests for the core graph model."""

import pytest

from ori.generator.graph import ACE, ADEdge, ADGraph, ADNode, SIDAllocator


def test_sid_allocator_well_known():
    alloc = SIDAllocator("S-1-5-21-1-2-3")
    sid = alloc.alloc("Domain Admins")
    assert sid == "S-1-5-21-1-2-3-512"


def test_sid_allocator_sequential():
    alloc = SIDAllocator("S-1-5-21-1-2-3")
    sid1 = alloc.alloc("user1")
    sid2 = alloc.alloc("user2")
    assert sid1 == "S-1-5-21-1-2-3-1100"
    assert sid2 == "S-1-5-21-1-2-3-1101"


def test_sid_allocator_get_or_alloc():
    alloc = SIDAllocator("S-1-5-21-1-2-3")
    sid1 = alloc.alloc("user1")
    sid2 = alloc.get_or_alloc("user1")
    assert sid1 == sid2


def test_graph_add_and_get_node():
    graph = ADGraph("CORP.LOCAL", seed=1)
    node = ADNode(
        object_id="S-1-5-21-1-2-3-512",
        node_type="Group",
        properties={"name": "DOMAIN ADMINS@CORP.LOCAL", "domain": "CORP.LOCAL", "domainsid": "S-1-5-21-1-2-3"},
    )
    graph.add_node(node)
    assert graph.get_node("S-1-5-21-1-2-3-512") is node


def test_graph_duplicate_node_raises():
    graph = ADGraph("CORP.LOCAL", seed=1)
    node = ADNode(object_id="S-1-2-3", node_type="Group", properties={})
    graph.add_node(node)
    with pytest.raises(ValueError, match="Duplicate node"):
        graph.add_node(ADNode(object_id="S-1-2-3", node_type="User", properties={}))


def test_graph_add_edge():
    graph = ADGraph("CORP.LOCAL", seed=1)
    user = ADNode(object_id="S-1-1", node_type="User", properties={})
    group = ADNode(object_id="S-1-2", node_type="Group", properties={})
    graph.add_node(user)
    graph.add_node(group)
    edge = graph.add_edge("S-1-1", "MemberOf", "S-1-2")
    assert edge.source == "S-1-1"
    assert edge.edge_kind == "MemberOf"
    assert edge.target == "S-1-2"


def test_graph_edge_missing_source_raises():
    graph = ADGraph("CORP.LOCAL", seed=1)
    group = ADNode(object_id="S-1-2", node_type="Group", properties={})
    graph.add_node(group)
    with pytest.raises(KeyError):
        graph.add_edge("S-1-MISSING", "MemberOf", "S-1-2")


def test_graph_nodes_by_type():
    graph = ADGraph("CORP.LOCAL", seed=1)
    graph.add_node(ADNode(object_id="S-1", node_type="User", properties={}))
    graph.add_node(ADNode(object_id="S-2", node_type="User", properties={}))
    graph.add_node(ADNode(object_id="S-3", node_type="Group", properties={}))
    assert len(graph.nodes_by_type("User")) == 2
    assert len(graph.nodes_by_type("Group")) == 1


def test_graph_seed_determinism():
    """Same seed should produce same domain SID."""
    g1 = ADGraph("CORP.LOCAL", seed=42)
    g2 = ADGraph("CORP.LOCAL", seed=42)
    assert g1.domain_sid == g2.domain_sid


def test_graph_different_seeds_differ():
    g1 = ADGraph("CORP.LOCAL", seed=1)
    g2 = ADGraph("CORP.LOCAL", seed=2)
    assert g1.domain_sid != g2.domain_sid
