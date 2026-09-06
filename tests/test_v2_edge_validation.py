"""Exact compiler edge membership and per-call index reuse."""

import pytest

from ori.eval.v2.compiler import V2CompileError, _assert_edges_exist
from ori.eval.v2.schema import EdgeWitness


class _SnapshotProbe:
    def __init__(self):
        self.reads = 0
        self.keys = frozenset({("A", "MemberOf", "B")})

    @property
    def edge_keys(self):
        self.reads += 1
        return self.keys


def _edge(source="A", relationship="MemberOf", target="B"):
    return EdgeWitness(source_id=source, relationship=relationship, target_id=target)


def test_edge_index_is_built_once_per_nonempty_call_and_never_reused_across_calls():
    snapshot = _SnapshotProbe()
    _assert_edges_exist(snapshot, (), purpose="empty")
    assert snapshot.reads == 0
    _assert_edges_exist(snapshot, (_edge(), _edge()), purpose="duplicate")
    assert snapshot.reads == 1

    snapshot.keys = frozenset()
    with pytest.raises(V2CompileError) as caught:
        _assert_edges_exist(snapshot, (_edge(), _edge()), purpose="changed")
    assert str(caught.value) == (
        "changed contains graph-absent edges: ['A-[MemberOf]->B', 'A-[MemberOf]->B']"
    )
    assert snapshot.reads == 2


@pytest.mark.parametrize(
    "edge",
    [_edge("B", target="A"), _edge(relationship="memberof"), _edge(target="C")],
    ids=["direction", "relationship-case", "unknown-target"],
)
def test_edge_membership_remains_exact(edge):
    snapshot = _SnapshotProbe()
    with pytest.raises(V2CompileError) as caught:
        _assert_edges_exist(snapshot, (_edge(), edge, edge), purpose="route")
    rendered = f"{edge.source_id}-[{edge.relationship}]->{edge.target_id}"
    assert str(caught.value) == f"route contains graph-absent edges: {[rendered, rendered]}"


def test_invalid_edge_input_is_not_treated_as_an_empty_sequence():
    snapshot = _SnapshotProbe()
    with pytest.raises(TypeError):
        _assert_edges_exist(snapshot, None, purpose="invalid")
    assert snapshot.reads == 0


def test_iterable_edges_remain_lazy_without_truthiness_or_length_checks():
    snapshot = _SnapshotProbe()
    _assert_edges_exist(snapshot, iter(()), purpose="empty iterator")
    assert snapshot.reads == 0

    class Edges(list):
        def __bool__(self):
            raise AssertionError("edge iteration must not consult truthiness")

        def __len__(self):
            raise AssertionError("edge iteration must not consult length")

    _assert_edges_exist(snapshot, Edges([_edge(), _edge()]), purpose="iterable")
    assert snapshot.reads == 1
