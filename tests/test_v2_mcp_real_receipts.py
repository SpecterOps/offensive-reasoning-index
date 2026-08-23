from __future__ import annotations

import json
from collections.abc import Iterable

import pytest

from ori.eval.v2.compiler import compile_acceptance_spec
from ori.eval.v2.mcp import (
    MCP_CAPABILITY_PROFILE_ID,
    EvidenceEventKind,
    MCPToolLoop,
    build_mcp_capability_profile,
)
from ori.eval.v2.model_runtime import MCPTranscriptProjector
from ori.eval.v2.schema import (
    EntityRef,
    EntitySelector,
    ExactSetPolicy,
    ExecutionBounds,
    MCPBindingMode,
    MCPClaimEvidenceContract,
    PopulationScope,
    PredicateOperator,
    PropertyPredicate,
    RelationshipPattern,
    RelationshipSemantics,
    SelectionExpression,
    SetClaim,
    TaskBundle,
    Track,
    TrackBinding,
)

FP = "a" * 64
PROFILE = build_mcp_capability_profile()
SUBJECT_NAME = "HOST-A.TEST.LOCAL"
SUBJECT_ID = "S-1-5-21-100-200-300-1001"


def _set_task(
    *,
    projection_type: str,
    page_size: int = 100,
    result_offset: int = 0,
    max_pages: int = 1,
    max_result_cardinality: int | None = None,
    require_total_count: bool = False,
    require_stable_ordering: bool = True,
    required_input_roles: tuple[str, ...] = (),
    input_entities: tuple[EntityRef, ...] = (),
    selection_predicates: tuple[PropertyPredicate, ...] = (),
) -> TaskBundle:
    cardinality = max_result_cardinality or page_size * max_pages
    policy = ExactSetPolicy(kind="exact_set")
    binding = TrackBinding(
        track=Track.MCP,
        capability_profile_id=MCP_CAPABILITY_PROFILE_ID,
        semantics=RelationshipSemantics.DIRECT,
        bounds=ExecutionBounds(
            max_hops=1,
            max_result_cardinality=cardinality,
            page_size=page_size,
            result_offset=result_offset,
            max_pages=max_pages,
            require_total_count=require_total_count,
            require_stable_ordering=require_stable_ordering,
            max_output_bytes=524_288,
            max_transcript_bytes=2_097_152,
            max_tool_calls=12,
            timeout_seconds=300.0,
        ),
        mcp_tool_loop=MCPToolLoop.NATIVE_OPENAI_COMPATIBLE.value,
        mcp_resource_mode="off",
        mcp_binding_mode=MCPBindingMode.CYPHER_ENABLED,
        mcp_evidence_contract=MCPClaimEvidenceContract(
            result_kind="entities",
            required_input_roles=required_input_roles,
            projection_types=(projection_type,),
        ),
    )
    entities_by_role = {entity.role: entity for entity in input_entities}
    anchors = tuple(
        EntitySelector(
            role=role,
            object_type=(
                entities_by_role[role].object_type
                if role in entities_by_role
                else None
            ),
        )
        for role in required_input_roles
    )
    relationships: tuple[RelationshipPattern, ...] = ()
    if required_input_roles == ("subject_computer",):
        if projection_type.casefold() == "principal":
            relationships = (
                RelationshipPattern(
                    source_role="result",
                    relationship="AdminTo",
                    target_role="subject_computer",
                    source_type="Principal",
                    target_type="Computer",
                ),
            )
        else:
            relationships = (
                RelationshipPattern(
                    source_role="subject_computer",
                    relationship="HasSession",
                    target_role="result",
                    source_type="Computer",
                    target_type=projection_type,
                ),
            )
    claim = SetClaim(
        kind="set",
        claim_id="claim:captured-set",
        selection=SelectionExpression(
            anchors=anchors,
            relationships=relationships,
            predicates=selection_predicates,
            projection_role="result",
            projection_type=projection_type,
            offset=result_offset,
            limit=(page_size if max_pages == 1 and not require_total_count else None),
        ),
        semantics=RelationshipSemantics.DIRECT,
        population_scope=PopulationScope.BENCHMARK_NAMESPACE,
    )
    return TaskBundle(
        task_id=(
            f"complex.mcp.captured-{projection_type.casefold()}-{result_offset}-{page_size}@2"
        ),
        revision=2,
        product="complex",
        claim_kind="set",
        answer_policy=policy,
        acceptance_spec=compile_acceptance_spec(claim, policy, binding),
        binding=binding,
        input_entities=input_entities,
        question="Return the complete exact synthetic entity set.",
        answer_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "entities": {
                    "type": "array",
                    "items": {"type": "string"},
                }
            },
            "required": ["entities"],
        },
        claim_fingerprint=FP,
        prompt_fingerprint=FP,
        task_fingerprint=FP,
    )


def _subject_entity() -> EntityRef:
    return EntityRef(
        object_id=SUBJECT_ID,
        object_type="Computer",
        domain="TEST.LOCAL",
        role="subject_computer",
        canonical_name=SUBJECT_NAME,
        aliases=("HOST-A",),
    )


def _literal_payload(literals: Iterable[dict[str, object]]) -> str:
    materialized = list(literals)
    return json.dumps(
        {
            "info_type": "run",
            "success": True,
            "has_results": bool(materialized),
            "data": {
                "nodes": {},
                "edges": [],
                "literals": materialized,
            },
            "node_count": 0,
            "edge_count": 0,
            "query_executed": True,
        }
    )


def _identity_literals(
    count: int,
    *,
    include_name: bool = False,
    start: int = 0,
) -> list[dict[str, object]]:
    literals: list[dict[str, object]] = []
    for index in range(start, start + count):
        literals.append(
            {
                "key": "object_id",
                "value": f"S-1-5-21-100-200-300-{2000 + index}",
            }
        )
        if include_name:
            literals.append(
                {
                    "key": "name",
                    "value": f"USER-{index:04d}@TEST.LOCAL",
                }
            )
    return literals


def _observe(
    projector: MCPTranscriptProjector,
    *,
    query: str,
    literals: Iterable[dict[str, object]],
) -> bool:
    return projector.observe(
        "cypher_query",
        {
            "info_type": "run",
            "include_properties": False,
            "query": query,
        },
        _literal_payload(literals),
        None,
    )


def test_flat_object_id_literals_are_counted_as_entity_rows() -> None:
    projector = MCPTranscriptProjector(
        _set_task(projection_type="User", page_size=3),
        PROFILE,
    )

    ready = _observe(
        projector,
        query=("MATCH (u:User) RETURN u.objectid AS object_id ORDER BY u.objectid SKIP 0 LIMIT 3"),
        literals=_identity_literals(3),
    )

    receipt = projector.receipts[-1]
    assert ready is True
    assert receipt.observation.result_count == 3
    assert receipt.event.kind is EvidenceEventKind.USEFUL_POSITIVE


def test_object_id_and_name_literals_count_rows_without_double_counting() -> None:
    projector = MCPTranscriptProjector(
        _set_task(projection_type="User", page_size=2),
        PROFILE,
    )

    ready = _observe(
        projector,
        query=(
            "MATCH (u:User) RETURN u.objectid AS object_id, u.name AS name "
            "ORDER BY u.objectid SKIP 0 LIMIT 2"
        ),
        literals=_identity_literals(2, include_name=True),
    )

    receipt = projector.receipts[-1]
    assert ready is True
    assert receipt.observation.result_count == 2
    assert receipt.event.kind is EvidenceEventKind.USEFUL_POSITIVE


@pytest.mark.parametrize(
    ("offset", "row_count"),
    [
        (500, 500),
        (2000, 315),
    ],
)
def test_captured_large_literal_pages_preserve_real_row_cardinality(
    offset: int,
    row_count: int,
) -> None:
    projector = MCPTranscriptProjector(
        _set_task(
            projection_type="Computer",
            page_size=500,
            result_offset=offset,
            max_result_cardinality=500,
        ),
        PROFILE,
    )

    ready = _observe(
        projector,
        query=(
            "MATCH (c:Computer) RETURN c.objectid AS object_id "
            f"ORDER BY c.objectid SKIP {offset} LIMIT 500"
        ),
        literals=_identity_literals(row_count, start=offset),
    )

    receipt = projector.receipts[-1]
    assert ready is True
    assert receipt.observation.result_count == row_count
    assert receipt.observation.complete is True
    assert receipt.event.kind is EvidenceEventKind.USEFUL_POSITIVE


@pytest.mark.parametrize(
    "projection_and_order",
    [
        "RETURN u.objectid AS object_id ORDER BY u.objectid",
        "RETURN u.objectid AS object_id ORDER BY object_id",
        "WITH u.objectid AS object_id RETURN object_id ORDER BY object_id",
        "WITH DISTINCT u ORDER BY u.objectid RETURN u.objectid AS object_id",
    ],
)
def test_stable_object_id_order_accepts_property_and_bound_alias(
    projection_and_order: str,
) -> None:
    projector = MCPTranscriptProjector(
        _set_task(projection_type="User", page_size=2),
        PROFILE,
    )

    ready = _observe(
        projector,
        query=f"MATCH (u:User) {projection_and_order} SKIP 0 LIMIT 2",
        literals=_identity_literals(2),
    )

    assert ready is True
    assert projector.receipts[-1].event.kind is EvidenceEventKind.USEFUL_POSITIVE


@pytest.mark.parametrize(
    "query",
    [
        ("MATCH (u:User) RETURN u.objectid AS object_id ORDER BY invented_alias SKIP 0 LIMIT 2"),
        (
            "MATCH (u:User) RETURN u.objectid AS object_id, u.name AS display_name "
            "ORDER BY display_name SKIP 0 LIMIT 2"
        ),
        (
            "MATCH (u:User) WITH u.name AS object_id RETURN object_id "
            "ORDER BY object_id SKIP 0 LIMIT 2"
        ),
        (
            "MATCH (u:User) WITH u.objectid AS object_id "
            "WITH u.name AS object_id RETURN object_id "
            "ORDER BY object_id SKIP 0 LIMIT 2"
        ),
    ],
)
def test_unbound_or_nonidentity_order_alias_cannot_unlock_finalization(
    query: str,
) -> None:
    projector = MCPTranscriptProjector(
        _set_task(projection_type="User", page_size=2),
        PROFILE,
    )

    ready = _observe(
        projector,
        query=query,
        literals=_identity_literals(2, include_name=True),
    )

    assert ready is False
    assert projector.receipts[-1].observation.complete is False
    assert projector.receipts[-1].event.kind is EvidenceEventKind.TRUNCATED


def test_toupper_exact_public_selector_is_claim_relevant_but_wrong_selector_is_not() -> None:
    task = _set_task(
        projection_type="User",
        page_size=2,
        required_input_roles=("subject_computer",),
        input_entities=(_subject_entity(),),
    )
    accepted = MCPTranscriptProjector(task, PROFILE)
    rejected = MCPTranscriptProjector(task, PROFILE)
    query = (
        "MATCH (c:Computer)-[:HasSession]->(u:User) "
        "WHERE TOUPPER(c.name) = '{selector}' "
        "RETURN u.objectid AS object_id ORDER BY object_id SKIP 0 LIMIT 2"
    )

    accepted_ready = _observe(
        accepted,
        query=query.format(selector=SUBJECT_NAME),
        literals=_identity_literals(2),
    )
    rejected_ready = _observe(
        rejected,
        query=query.format(selector="HOST-B.TEST.LOCAL"),
        literals=_identity_literals(2),
    )

    assert accepted_ready is True
    assert accepted.receipts[-1].observation.claim_relevant is True
    assert accepted.receipts[-1].event.kind is EvidenceEventKind.USEFUL_POSITIVE
    assert rejected_ready is False
    assert rejected.receipts[-1].observation.claim_relevant is False
    assert rejected.receipts[-1].event.kind is EvidenceEventKind.IRRELEVANT


def test_toupper_public_selector_value_is_claim_relevant() -> None:
    task = _set_task(
        projection_type="User",
        page_size=1,
        required_input_roles=("subject_computer",),
        input_entities=(_subject_entity(),),
    )
    projector = MCPTranscriptProjector(task, PROFILE)

    ready = _observe(
        projector,
        query=(
            "MATCH (c:Computer "
            f"{{name: TOUPPER('{SUBJECT_NAME}')}})-[:HasSession]->(u:User) "
            "RETURN u.objectid AS object_id, u.name AS name "
            "ORDER BY object_id SKIP 0 LIMIT 1"
        ),
        literals=_identity_literals(1, include_name=True),
    )

    assert ready is True
    assert projector.receipts[-1].observation.claim_relevant is True
    assert projector.receipts[-1].event.kind is EvidenceEventKind.USEFUL_POSITIVE


def test_where_toupper_public_selector_value_is_claim_relevant() -> None:
    task = _set_task(
        projection_type="User",
        page_size=1,
        required_input_roles=("subject_computer",),
        input_entities=(_subject_entity(),),
    )
    projector = MCPTranscriptProjector(task, PROFILE)

    ready = _observe(
        projector,
        query=(
            "MATCH (c:Computer)-[:HasSession]->(u:User) "
            f"WHERE c.name = TOUPPER('{SUBJECT_NAME}') "
            "RETURN u.objectid AS object_id "
            "ORDER BY object_id SKIP 0 LIMIT 1"
        ),
        literals=_identity_literals(1),
    )

    assert ready is True
    assert projector.receipts[-1].observation.claim_relevant is True


@pytest.mark.parametrize(
    "selector_expression",
    [
        f"c.displayname = '{SUBJECT_NAME}'",
        f"c.NAME = '{SUBJECT_NAME}'",
        f"c.name = TOLOWER('{SUBJECT_NAME}')",
        f"c.name = TOUPPER('{SUBJECT_NAME}') + '.EVIL'",
    ],
)
def test_inexact_or_semantically_wrong_public_selector_cannot_unlock(
    selector_expression: str,
) -> None:
    task = _set_task(
        projection_type="User",
        page_size=1,
        required_input_roles=("subject_computer",),
        input_entities=(_subject_entity(),),
    )
    projector = MCPTranscriptProjector(task, PROFILE)

    ready = _observe(
        projector,
        query=(
            "MATCH (c:Computer)-[:HasSession]->(u:User) "
            f"WHERE {selector_expression} "
            "RETURN u.objectid AS object_id "
            "ORDER BY object_id SKIP 0 LIMIT 1"
        ),
        literals=_identity_literals(1),
    )

    assert ready is False
    assert projector.receipts[-1].observation.claim_relevant is False
    assert projector.receipts[-1].event.kind is EvidenceEventKind.IRRELEVANT


def test_wrong_case_identity_property_cannot_prove_a_stable_page_or_count() -> None:
    page_task = _set_task(projection_type="User", page_size=1)
    page_projector = MCPTranscriptProjector(page_task, PROFILE)
    count_task = _set_task(
        projection_type="User",
        page_size=1,
        require_total_count=True,
    )
    count_projector = MCPTranscriptProjector(count_task, PROFILE)

    page_ready = _observe(
        page_projector,
        query=(
            "MATCH (u:User) RETURN u.objectid AS object_id "
            "ORDER BY u.OBJECTID SKIP 0 LIMIT 1"
        ),
        literals=_identity_literals(1),
    )
    _observe(
        count_projector,
        query="MATCH (u:User) RETURN count(u.OBJECTID) AS total",
        literals=[{"key": "total", "value": 0}],
    )

    assert page_ready is False
    assert page_projector.receipts[-1].observation.complete is False
    assert page_projector.finalization_ready is False
    assert count_projector.total_count is None
    assert count_projector.receipts[-1].observation.claim_relevant is False


def test_normalized_where_selector_cannot_return_only_the_selected_input() -> None:
    task = _set_task(
        projection_type="Any",
        page_size=1,
        required_input_roles=("subject_computer",),
        input_entities=(_subject_entity(),),
    )
    projector = MCPTranscriptProjector(task, PROFILE)

    ready = _observe(
        projector,
        query=(
            "MATCH (c:Computer)-[:HasSession]->(entity) "
            f"WHERE c.name = TOUPPER('{SUBJECT_NAME}') "
            "RETURN c.objectid AS object_id "
            "ORDER BY object_id SKIP 0 LIMIT 1"
        ),
        literals=_identity_literals(1),
    )

    assert ready is False
    assert projector.receipts[-1].observation.claim_relevant is False


@pytest.mark.parametrize("projection_type", ["Any", "User"])
def test_identity_rows_do_not_require_a_concrete_result_variable_label(
    projection_type: str,
) -> None:
    task = _set_task(
        projection_type=projection_type,
        page_size=1,
        required_input_roles=("subject_computer",),
        input_entities=(_subject_entity(),),
    )
    projector = MCPTranscriptProjector(task, PROFILE)

    ready = _observe(
        projector,
        query=(
            f"MATCH (c:Computer {{name: '{SUBJECT_NAME}'}})-[:HasSession]->(entity) "
            "RETURN entity.objectid AS object_id, entity.name AS name "
            "ORDER BY object_id SKIP 0 LIMIT 1"
        ),
        literals=_identity_literals(1, include_name=True),
    )

    assert ready is True
    assert projector.receipts[-1].observation.claim_relevant is True
    assert projector.receipts[-1].event.kind is EvidenceEventKind.USEFUL_POSITIVE


@pytest.mark.parametrize("projection_type", ["Any", "User"])
def test_unlabeled_identity_projection_cannot_return_only_the_selected_input(
    projection_type: str,
) -> None:
    task = _set_task(
        projection_type=projection_type,
        page_size=1,
        required_input_roles=("subject_computer",),
        input_entities=(_subject_entity(),),
    )
    projector = MCPTranscriptProjector(task, PROFILE)

    ready = _observe(
        projector,
        query=(
            f"MATCH (c:Computer {{name: '{SUBJECT_NAME}'}})-[:HasSession]->(entity) "
            "RETURN c.objectid AS object_id, c.name AS name "
            "ORDER BY object_id SKIP 0 LIMIT 1"
        ),
        literals=_identity_literals(1, include_name=True),
    )

    assert ready is False
    assert projector.receipts[-1].observation.claim_relevant is False
    assert projector.receipts[-1].event.kind is EvidenceEventKind.IRRELEVANT


def test_explicit_wrong_projection_label_cannot_unlock() -> None:
    projector = MCPTranscriptProjector(
        _set_task(projection_type="User", page_size=1),
        PROFILE,
    )

    ready = _observe(
        projector,
        query=(
            "MATCH (entity:Computer) RETURN entity.objectid AS object_id "
            "ORDER BY object_id SKIP 0 LIMIT 1"
        ),
        literals=_identity_literals(1),
    )

    assert ready is False
    assert projector.receipts[-1].observation.claim_relevant is False


def test_disconnected_decoy_label_cannot_validate_wrong_projection_type() -> None:
    task = _set_task(
        projection_type="User",
        page_size=1,
        required_input_roles=("subject_computer",),
        input_entities=(_subject_entity(),),
    )
    projector = MCPTranscriptProjector(task, PROFILE)

    ready = _observe(
        projector,
        query=(
            f"MATCH (c:Computer {{name: '{SUBJECT_NAME}'}}), (decoy:User) "
            "RETURN c.objectid AS object_id "
            "ORDER BY object_id SKIP 0 LIMIT 1"
        ),
        literals=_identity_literals(1),
    )

    assert ready is False
    assert projector.receipts[-1].observation.claim_relevant is False


def test_abstract_principal_accepts_unlabeled_identity_projection_only() -> None:
    task = _set_task(
        projection_type="Principal",
        page_size=2,
        required_input_roles=("subject_computer",),
        input_entities=(_subject_entity(),),
    )
    accepted = MCPTranscriptProjector(task, PROFILE)
    wrong_endpoint = MCPTranscriptProjector(task, PROFILE)
    match = f"MATCH (p)-[:AdminTo]->(c:Computer {{name: '{SUBJECT_NAME}'}}) "

    accepted_ready = _observe(
        accepted,
        query=(
            match + "RETURN p.objectid AS object_id, p.name AS name "
            "ORDER BY object_id SKIP 0 LIMIT 2"
        ),
        literals=_identity_literals(2, include_name=True),
    )
    rejected_ready = _observe(
        wrong_endpoint,
        query=(match + "RETURN c.objectid AS object_id ORDER BY object_id SKIP 0 LIMIT 2"),
        literals=_identity_literals(2),
    )

    assert accepted_ready is True
    assert accepted.receipts[-1].observation.claim_relevant is True
    assert accepted.receipts[-1].event.kind is EvidenceEventKind.USEFUL_POSITIVE
    assert rejected_ready is False
    assert wrong_endpoint.receipts[-1].observation.claim_relevant is False
    assert wrong_endpoint.receipts[-1].event.kind is EvidenceEventKind.IRRELEVANT


def test_unknown_nonempty_literals_remain_inconclusive() -> None:
    projector = MCPTranscriptProjector(
        _set_task(projection_type="User", page_size=1),
        PROFILE,
    )

    ready = _observe(
        projector,
        query=("MATCH (u:User) RETURN u.objectid AS object_id ORDER BY u.objectid SKIP 0 LIMIT 1"),
        literals=[{"key": "department", "value": "Engineering"}],
    )

    receipt = projector.receipts[-1]
    assert ready is False
    assert receipt.observation.result_count is None
    assert receipt.observation.complete is False
    assert receipt.event.kind is EvidenceEventKind.INCONCLUSIVE_EMPTY


@pytest.mark.parametrize("projection_type", ["Any", "Principal"])
def test_abstract_companion_count_accepts_unlabeled_counted_variable(
    projection_type: str,
) -> None:
    task = _set_task(
        projection_type=projection_type,
        page_size=1,
        require_total_count=True,
    )
    projector = MCPTranscriptProjector(task, PROFILE)

    _observe(
        projector,
        query="MATCH (entity) RETURN count(DISTINCT entity) AS total",
        literals=[{"key": "total", "value": 1}],
    )

    assert projector.total_count == 1


def test_concrete_companion_count_requires_explicit_counted_variable_label() -> None:
    task = _set_task(
        projection_type="User",
        page_size=1,
        require_total_count=True,
    )
    projector = MCPTranscriptProjector(task, PROFILE)

    _observe(
        projector,
        query="MATCH (entity) RETURN count(DISTINCT entity) AS total",
        literals=[{"key": "total", "value": 1}],
    )

    assert projector.total_count is None
    assert projector.receipts[-1].event.kind is EvidenceEventKind.IRRELEVANT


def test_companion_total_and_six_literal_pages_complete_exact_set_proof() -> None:
    task = _set_task(
        projection_type="User",
        page_size=100,
        max_pages=6,
        max_result_cardinality=561,
        require_total_count=True,
    )
    projector = MCPTranscriptProjector(task, PROFILE)

    count_ready = _observe(
        projector,
        query="MATCH (u:User) RETURN count(DISTINCT u) AS total",
        literals=[{"key": "total", "value": 561}],
    )
    assert count_ready is False
    assert projector.total_count == 561

    for page_index, row_count in enumerate((100, 100, 100, 100, 100, 61)):
        offset = page_index * 100
        ready = _observe(
            projector,
            query=(
                "MATCH (u:User) RETURN u.objectid AS object_id "
                f"ORDER BY u.objectid SKIP {offset} LIMIT 100"
            ),
            literals=_identity_literals(row_count, start=offset),
        )
        assert ready is (page_index == 5)

    receipt = projector.receipts[-1]
    assert receipt.observation.result_count == 561
    assert receipt.observation.total_count == 561
    assert receipt.observation.pages_received == 6
    assert receipt.observation.complete is True
    assert receipt.event.kind is EvidenceEventKind.USEFUL_POSITIVE


def test_distinct_companion_total_rejects_duplicate_identity_rows() -> None:
    task = _set_task(
        projection_type="User",
        page_size=2,
        max_result_cardinality=2,
        require_total_count=True,
    )
    projector = MCPTranscriptProjector(task, PROFILE)

    _observe(
        projector,
        query="MATCH (u:User) RETURN count(DISTINCT u) AS total",
        literals=[{"key": "total", "value": 2}],
    )
    duplicate = {
        "key": "object_id",
        "value": "S-1-5-21-100-200-300-2000",
    }
    ready = _observe(
        projector,
        query=(
            "MATCH (u:User) RETURN u.objectid AS object_id "
            "ORDER BY u.objectid SKIP 0 LIMIT 2"
        ),
        literals=[duplicate, duplicate],
    )

    assert ready is False
    assert projector.receipts[-1].observation.result_count == 2
    assert projector.receipts[-1].observation.total_count is None
    assert projector.receipts[-1].observation.complete is False


def test_companion_total_accepts_contiguous_pages_ordered_in_with_clause() -> None:
    task = _set_task(
        projection_type="User",
        page_size=500,
        max_pages=2,
        max_result_cardinality=561,
        require_total_count=True,
    )
    projector = MCPTranscriptProjector(task, PROFILE)

    _observe(
        projector,
        query="MATCH (u:User) RETURN count(DISTINCT u.objectid) AS total",
        literals=[{"key": "total", "value": 561}],
    )

    first_ready = _observe(
        projector,
        query=(
            "MATCH (u:User) WITH DISTINCT u ORDER BY u.objectid "
            "RETURN u.objectid AS object_id SKIP 0 LIMIT 500"
        ),
        literals=_identity_literals(500),
    )
    second_ready = _observe(
        projector,
        query=(
            "MATCH (u:User) WITH DISTINCT u ORDER BY u.objectid "
            "RETURN u.objectid AS object_id SKIP 500 LIMIT 500"
        ),
        literals=_identity_literals(61, start=500),
    )

    assert first_ready is False
    assert second_ready is True
    assert projector.page_counts == {0: 500, 500: 61}
    assert projector.receipts[-1].observation.result_count == 561
    assert projector.receipts[-1].observation.complete is True
    assert projector.receipts[-1].event.kind is EvidenceEventKind.USEFUL_POSITIVE


@pytest.mark.parametrize(
    ("projection_type", "count_query", "page_query", "total"),
    [
        (
            "Computer",
            ("MATCH (c:Computer) WHERE c.unconstraineddelegation = true RETURN count(c) AS total"),
            (
                "MATCH (entity:Computer) "
                "WHERE entity.unconstraineddelegation = true "
                "RETURN entity.objectid AS object_id "
                "ORDER BY entity.objectid SKIP 0 LIMIT 500"
            ),
            1,
        ),
        (
            "User",
            ("MATCH (u:User) WHERE u.hasspn = true RETURN count(u) AS total"),
            (
                "MATCH (entity:User) WHERE entity.hasspn = true "
                "RETURN entity.objectid AS object_id "
                "ORDER BY entity.objectid SKIP 0 LIMIT 500"
            ),
            4,
        ),
    ],
)
def test_v10_count_and_page_receipts_bind_under_alpha_renaming(
    projection_type: str,
    count_query: str,
    page_query: str,
    total: int,
) -> None:
    property_name = (
        "unconstraineddelegation"
        if projection_type == "Computer"
        else "hasspn"
    )
    task = _set_task(
        projection_type=projection_type,
        page_size=500,
        max_result_cardinality=500,
        require_total_count=True,
        selection_predicates=(
            PropertyPredicate(
                role="result",
                property_name=property_name,
                operator=PredicateOperator.EQUALS,
                value=True,
            ),
        ),
    )
    projector = MCPTranscriptProjector(task, PROFILE)

    _observe(
        projector,
        query=count_query,
        literals=[{"key": "total", "value": total}],
    )
    ready = _observe(
        projector,
        query=page_query,
        literals=_identity_literals(total),
    )

    assert ready is True
    assert projector.receipts[-1].observation.total_count == total
    assert projector.receipts[-1].observation.complete is True
    assert projector.receipts[-1].event.kind is EvidenceEventKind.USEFUL_POSITIVE


def test_alpha_renaming_does_not_merge_different_predicate_populations() -> None:
    task = _set_task(
        projection_type="User",
        page_size=1,
        max_result_cardinality=1,
        require_total_count=True,
    )
    projector = MCPTranscriptProjector(task, PROFILE)

    _observe(
        projector,
        query=("MATCH (u:User) WHERE u.hasspn = true RETURN count(u) AS total"),
        literals=[{"key": "total", "value": 1}],
    )
    ready = _observe(
        projector,
        query=(
            "MATCH (entity:User) WHERE entity.hasspn = false "
            "RETURN entity.objectid AS object_id "
            "ORDER BY entity.objectid SKIP 0 LIMIT 1"
        ),
        literals=_identity_literals(1),
    )

    assert ready is False
    assert projector.receipts[-1].observation.total_count is None
    assert projector.receipts[-1].observation.complete is False


def test_wrong_selector_count_cannot_supply_a_set_completeness_total() -> None:
    task = _set_task(
        projection_type="User",
        page_size=1,
        max_result_cardinality=1,
        require_total_count=True,
        required_input_roles=("subject_computer",),
        input_entities=(_subject_entity(),),
    )
    projector = MCPTranscriptProjector(task, PROFILE)

    _observe(
        projector,
        query=(
            "MATCH (c:Computer {name: 'HOST-B.TEST.LOCAL'})-[:HasSession]->(u:User) "
            "RETURN count(DISTINCT u) AS total"
        ),
        literals=[{"key": "total", "value": 1}],
    )

    assert projector.total_count is None
    assert projector.receipts[-1].observation.claim_relevant is False
    assert projector.receipts[-1].event.kind is EvidenceEventKind.IRRELEVANT


def test_companion_total_cannot_complete_a_different_population() -> None:
    task = _set_task(
        projection_type="User",
        page_size=1,
        max_result_cardinality=1,
        require_total_count=True,
        required_input_roles=("subject_computer",),
        input_entities=(_subject_entity(),),
    )
    projector = MCPTranscriptProjector(task, PROFILE)

    _observe(
        projector,
        query=(
            f"MATCH (c:Computer {{name: '{SUBJECT_NAME}'}}), (u:User) "
            "RETURN count(DISTINCT u) AS total"
        ),
        literals=[{"key": "total", "value": 1}],
    )
    ready = _observe(
        projector,
        query=(
            f"MATCH (c:Computer {{name: '{SUBJECT_NAME}'}})"
            "-[:HasSession]->(u:User) "
            "RETURN u.objectid AS object_id "
            "ORDER BY object_id SKIP 0 LIMIT 1"
        ),
        literals=_identity_literals(1),
    )

    assert projector.total_count is None
    assert ready is False
    assert projector.receipts[-1].observation.total_count is None
    assert projector.receipts[-1].observation.complete is False


def test_with_alias_ordering_is_bound_to_returned_object_id() -> None:
    projector = MCPTranscriptProjector(
        _set_task(projection_type="User", page_size=1),
        PROFILE,
    )

    ready = _observe(
        projector,
        query=(
            "MATCH (u:User) WITH u, u.objectid AS oid ORDER BY oid "
            "RETURN u.objectid AS object_id SKIP 0 LIMIT 1"
        ),
        literals=_identity_literals(1),
    )

    assert ready is True


def test_unrelated_variable_ordering_cannot_unlock() -> None:
    projector = MCPTranscriptProjector(
        _set_task(projection_type="User", page_size=1),
        PROFILE,
    )

    ready = _observe(
        projector,
        query=(
            "MATCH (u:User), (d:Computer) "
            "RETURN u.objectid AS object_id "
            "ORDER BY d.objectid SKIP 0 LIMIT 1"
        ),
        literals=_identity_literals(1),
    )

    assert ready is False
    assert projector.receipts[-1].observation.complete is False
