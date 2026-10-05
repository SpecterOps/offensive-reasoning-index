from __future__ import annotations

import pytest

from ori.eval.v2.graph import GraphObject, GraphSnapshot
from ori.eval.v2.schema import (
    EdgeWitness,
    EntityRef,
    EntitySelector,
    RelationshipPattern,
    RelationshipSemantics,
    SelectionExpression,
)
from ori.eval.v2.selection import SelectionEvaluationError, evaluate_selection


def _entity(object_id: str, object_type: str, role: str) -> EntityRef:
    return EntityRef(
        object_id=object_id,
        object_type=object_type,
        role=role,
        canonical_name=f"{object_id}@EXAMPLE.LOCAL",
        domain="EXAMPLE.LOCAL",
    )


def _snapshot() -> GraphSnapshot:
    users = tuple(_entity(f"U-{index}", "User", "fixture") for index in range(4))
    group = _entity("G-1", "Group", "fixture")
    return GraphSnapshot.model_construct(
        schema_version="ori-graph-snapshot-v2",
        manifest_schema_version="ori-generated-manifest-v2",
        product="fixture",
        seed=1,
        domain="EXAMPLE.LOCAL",
        domain_sid="S-1-5-21-1",
        objects=tuple(GraphObject(entity=entity) for entity in (*users, group)),
        relationships=(
            EdgeWitness(source_id="U-0", relationship="MemberOf", target_id="U-1"),
            EdgeWitness(source_id="U-1", relationship="MemberOf", target_id="U-2"),
            EdgeWitness(source_id="U-2", relationship="MemberOf", target_id="G-1"),
            EdgeWitness(source_id="U-3", relationship="MemberOf", target_id="U-0"),
        ),
        relationship_counts=(),
        graph_fingerprint="fixture",
    )


def test_tree_selection_includes_max_hop_and_excludes_max_plus_one() -> None:
    selection = SelectionExpression(
        anchors=(EntitySelector(role="group", object_type="Group"),),
        relationships=(
            RelationshipPattern(
                source_role="result",
                relationship="MemberOf",
                target_role="group",
                semantics=RelationshipSemantics.TRANSITIVE,
                min_hops=1,
                max_hops=3,
                source_type="User",
                target_type="Group",
            ),
        ),
        projection_role="result",
        projection_type="User",
    )
    result = evaluate_selection(_snapshot(), selection, role_bindings={"group": "G-1"})
    assert tuple(entity.object_id for entity in result.entities) == ("U-0", "U-1", "U-2")
    assert result.unpaged_count == 3


def test_bound_role_identity_must_match_its_declared_type() -> None:
    selection = SelectionExpression(
        anchors=(EntitySelector(role="group", object_type="Group"),),
        relationships=(
            RelationshipPattern(
                source_role="result",
                relationship="MemberOf",
                target_role="group",
                source_type="User",
                target_type="Group",
            ),
        ),
        projection_role="result",
        projection_type="User",
    )
    with pytest.raises(SelectionEvaluationError, match="incompatible type"):
        evaluate_selection(_snapshot(), selection, role_bindings={"group": "U-0"})
