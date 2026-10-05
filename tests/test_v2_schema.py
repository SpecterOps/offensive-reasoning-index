from __future__ import annotations

import json

import pytest
from pydantic import TypeAdapter, ValidationError

from ori.eval.v2 import (
    MANIFEST_SCHEMA_VERSION,
    PROTOCOL_VERSION,
    AuthorableAnswerPolicy,
    ClaimSpec,
    EdgeWitness,
    EntityRef,
    EvidenceIR,
    ExactSetPolicy,
    ExecutionBounds,
    MechanismValidRoutePolicy,
    PopulationScope,
    PropertyFact,
    RelationshipPattern,
    RelationshipSemantics,
    RouteClaim,
    SelectionExpression,
    TaskBundle,
    Track,
    TrackBinding,
    canonical_json_bytes,
    canonical_sha256,
)
from ori.eval.v2.compiler import compile_acceptance_spec

FP_A = "a" * 64
FP_B = "b" * 64
FP_C = "c" * 64


def _entity(*, object_id: str = "S-1-5-21-1-1001") -> EntityRef:
    return EntityRef(
        object_id=object_id,
        object_type="User",
        domain="EXAMPLE.LOCAL",
        role="source_user",
        canonical_name="ALICE@EXAMPLE.LOCAL",
        aliases=("ALICE",),
    )


def _route_claim() -> RouteClaim:
    return RouteClaim(
        kind="route",
        claim_id="claim-1",
        source={"role": "source_user", "object_type": "User"},
        target={"role": "domain_admins", "object_type": "Group"},
        semantics=RelationshipSemantics.TRANSITIVE,
        population_scope=PopulationScope.BENCHMARK_NAMESPACE,
        required_mechanisms=("MemberOf", "AdminTo"),
        max_hops=2,
    )


def _bounds() -> ExecutionBounds:
    return ExecutionBounds(
        max_hops=6,
        max_result_cardinality=100,
        page_size=50,
        max_pages=2,
        require_total_count=True,
        require_stable_ordering=True,
        max_output_bytes=65536,
        max_transcript_bytes=262144,
        max_tool_calls=0,
        timeout_seconds=15.0,
    )


def _task_bundle() -> TaskBundle:
    policy = MechanismValidRoutePolicy(kind="mechanism_valid_route")
    binding = TrackBinding(
        track=Track.DIRECT,
        capability_profile_id="direct-policy-v3",
        semantics=RelationshipSemantics.TRANSITIVE,
        bounds=_bounds(),
        direct_query_policy_version="bloodhound-cysql-direct-v3",
    )
    return TaskBundle(
        task_id="task-1",
        revision=1,
        product="complex",
        claim_kind="route",
        answer_policy=policy,
        acceptance_spec=compile_acceptance_spec(_route_claim(), policy, binding),
        binding=binding,
        question="Return the effective principals.",
        answer_schema={
            "type": "object",
            "properties": {"entities": {"type": "array"}},
            "required": ["entities"],
        },
        claim_fingerprint=FP_A,
        prompt_fingerprint=FP_B,
        task_fingerprint=FP_C,
    )


def test_protocol_constants_are_pinned() -> None:
    assert PROTOCOL_VERSION == "ori-eval-protocol-v2"
    assert MANIFEST_SCHEMA_VERSION == "ori-generated-manifest-v3"


def test_models_reject_unknown_fields_and_are_frozen() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        EntityRef(
            object_id="S-1",
            object_type="User",
            role="user",
            aliases=(),
            expected_answer=True,
        )

    entity = _entity()
    with pytest.raises(ValidationError, match="Instance is frozen"):
        entity.object_id = "S-2"  # type: ignore[misc]


def test_selection_requires_every_role_to_connect_to_projection() -> None:
    with pytest.raises(ValidationError, match="disconnected roles"):
        SelectionExpression(
            anchors=(
                {"role": "result", "object_type": "User"},
                {"role": "island", "object_type": "Group"},
            ),
            projection_role="result",
            projection_type="User",
        )


def test_selection_relationship_roles_must_form_a_simple_tree() -> None:
    relationships = (
        RelationshipPattern(
            source_role="result", relationship="MemberOf", target_role="group"
        ),
        RelationshipPattern(
            source_role="group", relationship="AdminTo", target_role="computer"
        ),
        RelationshipPattern(
            source_role="computer", relationship="HasSession", target_role="result"
        ),
    )
    with pytest.raises(ValidationError, match="acyclic and tree-shaped"):
        SelectionExpression(
            relationships=relationships,
            projection_role="result",
            projection_type="User",
        )


@pytest.mark.parametrize("relationship", ["memberOF", "WriteDACL"])
def test_relationship_patterns_require_exact_canonical_identifiers(
    relationship: str,
) -> None:
    with pytest.raises(ValidationError, match="canonical"):
        RelationshipPattern(
            source_role="source",
            relationship=relationship,
            target_role="target",
        )


def test_effective_selection_relationships_are_not_authorable() -> None:
    with pytest.raises(ValidationError, match="DIRECT.*TRANSITIVE"):
        RelationshipPattern(
            source_role="source",
            relationship="MemberOf",
            target_role="target",
            semantics=RelationshipSemantics.EFFECTIVE,
            max_hops=2,
        )


def test_selection_transitive_complexity_is_deterministically_bounded() -> None:
    with pytest.raises(ValidationError, match="complexity exceeds 256"):
        SelectionExpression(
            relationships=(
                RelationshipPattern(
                    source_role="result",
                    relationship="MemberOf",
                    target_role="group",
                    semantics=RelationshipSemantics.TRANSITIVE,
                    max_hops=17,
                ),
                RelationshipPattern(
                    source_role="group",
                    relationship="AdminTo",
                    target_role="computer",
                    semantics=RelationshipSemantics.TRANSITIVE,
                    max_hops=17,
                ),
            ),
            projection_role="result",
            projection_type="User",
        )


def test_connected_tree_selection_retains_declared_bounds() -> None:
    selection = SelectionExpression(
        relationships=(
            RelationshipPattern(
                source_role="result",
                relationship="MemberOf",
                target_role="group",
                semantics=RelationshipSemantics.TRANSITIVE,
                min_hops=1,
                max_hops=4,
            ),
            RelationshipPattern(
                source_role="group",
                relationship="AdminTo",
                target_role="computer",
            ),
        ),
        projection_role="result",
        projection_type="User",
    )
    assert selection.relationships[0].min_hops == 1
    assert selection.relationships[0].max_hops == 4


def test_selection_rejects_zero_hop_and_conflicting_role_types() -> None:
    with pytest.raises(ValidationError, match="greater than or equal to 1"):
        RelationshipPattern(
            source_role="source",
            relationship="MemberOf",
            target_role="target",
            semantics=RelationshipSemantics.TRANSITIVE,
            min_hops=0,
            max_hops=2,
        )

    with pytest.raises(ValidationError, match="conflicting object types"):
        SelectionExpression(
            anchors=({"role": "result", "object_type": "Group"},),
            projection_role="result",
            projection_type="User",
        )


def test_claim_relationship_vocabulary_is_canonical() -> None:
    with pytest.raises(ValidationError, match="canonical"):
        RouteClaim(
            kind="route",
            claim_id="claim-case",
            source={"role": "source", "object_type": "User"},
            target={"role": "target", "object_type": "Group"},
            semantics=RelationshipSemantics.DIRECT,
            population_scope=PopulationScope.BENCHMARK_NAMESPACE,
            required_mechanisms=("memberOF",),
            max_hops=1,
        )


def test_closed_route_variants_are_not_an_authorable_policy() -> None:
    adapter = TypeAdapter(AuthorableAnswerPolicy)
    with pytest.raises(ValidationError, match="does not match any of the expected tags"):
        adapter.validate_python({"kind": "closed_route_variants"})


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("count", True),
        ("count", "1"),
        ("truncated", 0),
        ("decision", 1),
    ],
)
def test_evidence_rejects_coerced_counts_and_booleans(field: str, invalid: object) -> None:
    payload: dict[str, object] = {
        "task_id": "task-1",
        "raw_digest": FP_A,
    }
    payload[field] = invalid
    with pytest.raises(ValidationError):
        EvidenceIR.model_validate(payload)


def test_protocol_models_reject_nonfinite_json_numbers() -> None:
    with pytest.raises(ValidationError):
        PropertyFact(key="risk", value=float("nan"))


def test_task_bundle_rejects_duplicate_public_logical_roles() -> None:
    payload = _task_bundle().model_dump()
    payload["input_entities"] = (
        _entity().model_dump(),
        _entity(object_id="S-1-5-21-1-1002")
        .model_copy(update={"canonical_name": "BOB@EXAMPLE.LOCAL", "aliases": ("BOB",)})
        .model_dump(),
    )

    with pytest.raises(
        ValidationError,
        match="public input entities must have unique logical roles",
    ):
        TaskBundle.model_validate(payload)


def test_task_bundle_requires_and_binds_solver_visible_acceptance() -> None:
    payload = _task_bundle().model_dump()
    payload.pop("acceptance_spec")
    with pytest.raises(ValidationError, match="Field required"):
        TaskBundle.model_validate(payload)

    payload = _task_bundle().model_dump()
    payload["acceptance_spec"]["semantics"] = RelationshipSemantics.DIRECT
    with pytest.raises(
        ValidationError,
        match="acceptance semantics do not match track binding semantics",
    ):
        TaskBundle.model_validate(payload)


def test_discriminated_claim_union_rejects_unknown_kind() -> None:
    adapter = TypeAdapter(ClaimSpec)
    with pytest.raises(ValidationError, match="Input tag 'template-special'"):
        adapter.validate_python(
            {
                "kind": "template-special",
                "claim_id": "claim-1",
            }
        )

    claim = adapter.validate_python(_route_claim().model_dump())
    assert isinstance(claim, RouteClaim)


def test_track_binding_requires_explicit_track_specific_protocol() -> None:
    with pytest.raises(ValidationError, match="direct bindings require"):
        TrackBinding(
            track=Track.DIRECT,
            capability_profile_id="direct",
            semantics=RelationshipSemantics.DIRECT,
            bounds=_bounds(),
        )

    with pytest.raises(ValidationError, match="MCP bindings require"):
        TrackBinding(
            track=Track.MCP,
            capability_profile_id="mcp",
            semantics=RelationshipSemantics.DIRECT,
            bounds=_bounds(),
        )


def test_task_bundle_rejects_policy_claim_mismatch() -> None:
    payload = _task_bundle().model_dump()
    payload["answer_policy"] = ExactSetPolicy(kind="exact_set")
    with pytest.raises(ValidationError, match="exact_set is not valid for route claims"):
        TaskBundle.model_validate(payload)


def test_canonical_fingerprint_is_mapping_order_independent_and_compact() -> None:
    first = {"z": [3, 2, 1], "a": {"right": True, "left": None}}
    second = {"a": {"left": None, "right": True}, "z": [3, 2, 1]}

    encoded = canonical_json_bytes(first)
    assert encoded == b'{"a":{"left":null,"right":true},"z":[3,2,1]}'
    assert canonical_sha256(first) == canonical_sha256(second)


def test_canonical_fingerprint_changes_on_semantic_byte_change() -> None:
    original = EdgeWitness(
        source_id="A",
        relationship="MemberOf",
        target_id="B",
    )
    changed = EdgeWitness(
        source_id="A",
        relationship="AdminTo",
        target_id="B",
    )
    assert canonical_sha256(original) != canonical_sha256(changed)


def test_canonical_fingerprint_supports_exact_explicit_exclusions() -> None:
    payload = {"public": {"value": 1}, "private": {"oracle": "sentinel"}}
    without_oracle = canonical_json_bytes(payload, exclude_fields=("private.oracle",))
    assert json.loads(without_oracle) == {"private": {}, "public": {"value": 1}}

    with pytest.raises(ValueError, match="does not exist"):
        canonical_sha256(payload, exclude_fields=("private.missing",))


def test_canonical_fingerprint_rejects_non_json_inputs() -> None:
    with pytest.raises(TypeError, match="unsupported canonical JSON value"):
        canonical_sha256({"not_canonical": {"unordered"}})


def test_public_task_bundle_has_no_oracle_fields() -> None:
    forbidden = {
        "expected_entities",
        "expected_count",
        "expected_decision",
        "reference_cypher",
        "ref_result",
        "route_variants",
        "valid_node_names",
        "oracle",
        "oracle_fingerprint",
        "claim",
    }
    assert forbidden.isdisjoint(TaskBundle.model_fields)

    dumped = _task_bundle().model_dump(mode="json")
    serialized = json.dumps(dumped, sort_keys=True)
    assert dumped["protocol_version"] == PROTOCOL_VERSION
    assert dumped["manifest_schema_version"] == MANIFEST_SCHEMA_VERSION
    assert all(name not in serialized for name in forbidden - {"claim"})


def test_public_task_bundle_rejects_oracle_fields_hidden_in_answer_schema() -> None:
    payload = _task_bundle().model_dump()
    payload["answer_schema"] = {
        "type": "object",
        "properties": {"expected_entities": {"type": "array"}},
    }
    with pytest.raises(ValidationError, match="answer schema contains oracle field"):
        TaskBundle.model_validate(payload)


def test_task_bundle_rejects_wrong_protocol_and_manifest_versions() -> None:
    payload = _task_bundle().model_dump()
    payload["protocol_version"] = "ori-eval-protocol-v1"
    with pytest.raises(ValidationError):
        TaskBundle.model_validate(payload)

    payload = _task_bundle().model_dump()
    payload["manifest_schema_version"] = "ori-generated-manifest-v2"
    with pytest.raises(ValidationError):
        TaskBundle.model_validate(payload)
