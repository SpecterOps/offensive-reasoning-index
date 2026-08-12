"""Strict public and scorer-only schema for ORI evaluation protocol v2."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from .fingerprint import canonical_sha256

PROTOCOL_VERSION = "ori-eval-protocol-v2"
MANIFEST_SCHEMA_VERSION = "ori-generated-manifest-v3"
CONTAINMENT_BASE_COMMIT = "0a56029471c5426be348c61c0969724eaee38599"
DIRECT_QUERY_POLICY_VERSION = "bloodhound-cysql-direct-v3"

NonEmptyStr = Annotated[str, StringConstraints(min_length=1, strip_whitespace=False)]
Fingerprint = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
GitCommit = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
JsonScalar = str | int | float | bool | None


class StrictModel(BaseModel):
    """Protocol model that rejects coercion, mutation, and undeclared fields."""

    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        strict=True,
        validate_default=True,
    )


class RelationshipSemantics(StrEnum):
    DIRECT = "direct"
    TRANSITIVE = "transitive"
    EFFECTIVE = "effective"


class PopulationScope(StrEnum):
    DECLARED = "declared"
    SOURCE_NEIGHBORS = "source_neighbors"
    TARGET_NEIGHBORS = "target_neighbors"
    DOMAIN = "domain"
    FOREST = "forest"
    BENCHMARK_NAMESPACE = "benchmark_namespace"


class Track(StrEnum):
    DIRECT = "direct"
    MCP = "mcp"


class EdgeDirection(StrEnum):
    OUTBOUND = "outbound"
    INBOUND = "inbound"


class PathStatus(StrEnum):
    FOUND = "found"
    NO_PATH = "no_path"
    UNKNOWN = "unknown"


class VerdictStatus(StrEnum):
    CORRECT = "correct"
    INCORRECT = "incorrect"


class CertificationState(StrEnum):
    DRAFT = "draft"
    COMPILED = "compiled"
    OFFLINE_CERTIFIED = "offline-certified"
    LIVE_CERTIFIED = "live-certified"
    CANDIDATE = "candidate"


class ProofStrength(StrEnum):
    POSITIVE_ONLY = "positive_only"
    COMPLETE_ENUMERATION = "complete_enumeration"
    BOUNDED_NEGATIVE = "bounded_negative"


class PredicateOperator(StrEnum):
    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    IN = "in"
    NOT_IN = "not_in"
    EXISTS = "exists"
    NOT_EXISTS = "not_exists"


class MCPBindingMode(StrEnum):
    TOOL_ONLY = "tool_only"
    CYPHER_ENABLED = "cypher_enabled"
    BLOCKED = "blocked"


class NegativeReasonCode(StrEnum):
    TEMPLATE_AUTHENTICATION_DISABLED = "template_authentication_disabled"
    MISSING_PUBLISHED_TO = "missing_published_to"
    MISSING_PRIVILEGED_IDENTITY_TRANSITION = "missing_privileged_identity_transition"
    MISSING_REQUIRED_RELATIONSHIP = "missing_required_relationship"
    PROPERTY_CONSTRAINT_FAILED = "property_constraint_failed"
    OBJECTIVE_UNREACHABLE = "objective_unreachable"


class ExecutionClass(StrEnum):
    SUCCESS = "success"
    MODEL_FAILURE = "model_failure"
    PROOF_FAILURE = "proof_failure"
    INFRA_FAILURE = "infra_failure"
    HARNESS_FAILURE = "harness_failure"
    UNEXECUTED = "unexecuted"


class HealthState(StrEnum):
    NOT_CHECKED = "not_checked"
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"


class EntityRef(StrictModel):
    object_id: NonEmptyStr
    object_type: NonEmptyStr
    domain: NonEmptyStr | None = None
    role: NonEmptyStr
    canonical_name: NonEmptyStr | None = None
    aliases: tuple[NonEmptyStr, ...] = ()

    @field_validator("aliases")
    @classmethod
    def aliases_are_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("aliases must be unique")
        return value


class PropertyFact(StrictModel):
    key: NonEmptyStr
    value: JsonScalar


class EdgeWitness(StrictModel):
    source_id: NonEmptyStr
    relationship: NonEmptyStr
    target_id: NonEmptyStr
    direction: EdgeDirection = EdgeDirection.OUTBOUND
    properties: tuple[PropertyFact, ...] = ()

    @field_validator("properties")
    @classmethod
    def property_keys_are_unique(
        cls,
        value: tuple[PropertyFact, ...],
    ) -> tuple[PropertyFact, ...]:
        keys = [item.key for item in value]
        if len(keys) != len(set(keys)):
            raise ValueError("edge property keys must be unique")
        return value


class EntitySelector(StrictModel):
    role: NonEmptyStr
    object_type: NonEmptyStr | None = None


class PropertyPredicate(StrictModel):
    """Typed property constraint over one logical role."""

    role: NonEmptyStr
    property_name: NonEmptyStr
    operator: PredicateOperator
    value: JsonScalar | tuple[JsonScalar, ...] = None

    @model_validator(mode="after")
    def operator_matches_value(self) -> PropertyPredicate:
        if self.operator in {PredicateOperator.EXISTS, PredicateOperator.NOT_EXISTS}:
            if self.value is not None:
                raise ValueError("existence predicates cannot declare a value")
        elif self.operator in {PredicateOperator.IN, PredicateOperator.NOT_IN}:
            if not isinstance(self.value, tuple) or not self.value:
                raise ValueError("set predicates require a non-empty tuple value")
        elif isinstance(self.value, tuple):
            raise ValueError("scalar predicates cannot declare a tuple value")
        return self


class RelationshipPattern(StrictModel):
    """One typed graph relationship between logical roles."""

    source_role: NonEmptyStr
    relationship: NonEmptyStr
    target_role: NonEmptyStr
    direction: EdgeDirection = EdgeDirection.OUTBOUND
    semantics: RelationshipSemantics = RelationshipSemantics.DIRECT
    min_hops: int = Field(default=1, strict=True, ge=0)
    max_hops: int = Field(default=1, strict=True, ge=1)
    source_type: NonEmptyStr | None = None
    target_type: NonEmptyStr | None = None

    @model_validator(mode="after")
    def hop_bounds_match_semantics(self) -> RelationshipPattern:
        if self.min_hops > self.max_hops:
            raise ValueError("relationship min_hops cannot exceed max_hops")
        if self.semantics is RelationshipSemantics.DIRECT and (
            self.min_hops != 1 or self.max_hops != 1
        ):
            raise ValueError("direct relationships must use exactly one hop")
        if self.semantics is not RelationshipSemantics.DIRECT and self.max_hops <= 1:
            raise ValueError("transitive/effective relationships require max_hops > 1")
        return self


class SelectionExpression(StrictModel):
    """Declarative, bounded graph selection used by set and count claims."""

    anchors: tuple[EntitySelector, ...] = ()
    relationships: tuple[RelationshipPattern, ...] = ()
    predicates: tuple[PropertyPredicate, ...] = ()
    projection_role: NonEmptyStr
    projection_type: NonEmptyStr
    order_by: Literal["object_id"] = "object_id"
    offset: int = Field(default=0, strict=True, ge=0)
    limit: int | None = Field(default=None, strict=True, gt=0)
    require_complete: bool = True

    @model_validator(mode="after")
    def declared_roles_are_connected(self) -> SelectionExpression:
        roles = {selector.role for selector in self.anchors}
        for relationship in self.relationships:
            roles.add(relationship.source_role)
            roles.add(relationship.target_role)
        if self.projection_role not in roles and (self.relationships or self.anchors):
            raise ValueError("projection_role is not declared by the selection")
        unknown_predicates = sorted(
            {
                predicate.role
                for predicate in self.predicates
                if predicate.role not in roles and predicate.role != self.projection_role
            }
        )
        if unknown_predicates:
            raise ValueError(
                f"property predicates reference undeclared roles: {unknown_predicates}"
            )
        return self


class RouteClaim(StrictModel):
    kind: Literal["route"]
    claim_id: NonEmptyStr
    source: EntitySelector
    target: EntitySelector
    semantics: RelationshipSemantics
    population_scope: PopulationScope
    required_mechanisms: tuple[NonEmptyStr, ...] = ()
    required_context: tuple[RelationshipPattern, ...] = ()
    required_properties: tuple[PropertyPredicate, ...] = ()
    excluded_relationships: tuple[RelationshipPattern, ...] = ()
    excluded_mechanisms: tuple[NonEmptyStr, ...] = ()
    mechanisms_are_ordered: bool = True
    max_hops: int = Field(strict=True, gt=0)


class SetClaim(StrictModel):
    kind: Literal["set"]
    claim_id: NonEmptyStr
    selection: SelectionExpression
    semantics: RelationshipSemantics
    population_scope: PopulationScope


class CountClaim(StrictModel):
    kind: Literal["count"]
    claim_id: NonEmptyStr
    selection: SelectionExpression
    semantics: RelationshipSemantics
    population_scope: PopulationScope


class DecisionClaim(StrictModel):
    kind: Literal["decision"]
    claim_id: NonEmptyStr
    subjects: tuple[EntitySelector, ...]
    required_relationships: tuple[RelationshipPattern, ...] = ()
    required_properties: tuple[PropertyPredicate, ...] = ()
    required_route: tuple[RelationshipPattern, ...] = ()
    semantics: RelationshipSemantics
    population_scope: PopulationScope

    @model_validator(mode="after")
    def has_typed_evidence_requirement(self) -> DecisionClaim:
        if not (self.required_relationships or self.required_properties or self.required_route):
            raise ValueError("decision claims require typed evidence")
        return self


class AbsenceClaim(StrictModel):
    kind: Literal["absence"]
    claim_id: NonEmptyStr
    source: EntitySelector
    target: EntitySelector
    relationships: tuple[NonEmptyStr, ...]
    required_context: tuple[RelationshipPattern, ...] = ()
    blocking_properties: tuple[PropertyPredicate, ...] = ()
    reason_codes: tuple[NegativeReasonCode, ...]
    max_hops: int = Field(strict=True, gt=0)
    semantics: RelationshipSemantics
    population_scope: PopulationScope


ClaimSpec = Annotated[
    RouteClaim | SetClaim | CountClaim | DecisionClaim | AbsenceClaim,
    Field(discriminator="kind"),
]


class ExactSetPolicy(StrictModel):
    kind: Literal["exact_set"]


class ExactCountPolicy(StrictModel):
    kind: Literal["exact_count"]


class ExactRoutePolicy(StrictModel):
    kind: Literal["exact_route"]
    require_ordered_edges: bool = True
    forbid_cycles: bool = True
    forbid_extra_supporting_edges: bool = False
    forbid_extra_properties: bool = False


class MechanismValidRoutePolicy(StrictModel):
    kind: Literal["mechanism_valid_route"]
    require_ordered_edges: bool = True
    forbid_cycles: bool = True
    forbid_extra_edges: bool = True
    forbid_extra_supporting_edges: bool = False
    forbid_extra_properties: bool = False


class ClosedRouteVariantsPolicy(StrictModel):
    kind: Literal["closed_route_variants"]
    require_ordered_edges: bool = True
    forbid_cycles: bool = True
    forbid_extra_supporting_edges: bool = False
    forbid_extra_properties: bool = False


class BoundedNegativePolicy(StrictModel):
    kind: Literal["bounded_negative"]
    require_complete_proof: bool = True
    require_reason_codes: bool = True
    forbid_extra_supporting_edges: bool = False
    forbid_extra_properties: bool = False


class DecisionPolicy(StrictModel):
    kind: Literal["decision"]
    require_supporting_evidence: bool = True
    require_evidence_entities: bool = True
    forbid_unrelated_entities: bool = True
    forbid_extra_supporting_edges: bool = False
    forbid_extra_properties: bool = False


AnswerPolicy = Annotated[
    ExactSetPolicy
    | ExactCountPolicy
    | ExactRoutePolicy
    | MechanismValidRoutePolicy
    | ClosedRouteVariantsPolicy
    | BoundedNegativePolicy
    | DecisionPolicy,
    Field(discriminator="kind"),
]


class ExecutionBounds(StrictModel):
    max_hops: int = Field(strict=True, ge=0)
    max_result_cardinality: int = Field(strict=True, gt=0)
    page_size: int = Field(strict=True, gt=0)
    result_offset: int = Field(default=0, strict=True, ge=0)
    max_pages: int = Field(strict=True, gt=0)
    require_total_count: bool
    require_stable_ordering: bool
    max_output_bytes: int = Field(strict=True, gt=0)
    max_transcript_bytes: int = Field(strict=True, gt=0)
    max_tool_calls: int = Field(strict=True, ge=0)
    timeout_seconds: float = Field(strict=True, gt=0)


class RouteAcceptanceKind(StrEnum):
    """Public route language; never identifies a reference witness."""

    NOT_APPLICABLE = "not_applicable"
    ANY_GRAPH_VALID = "any_graph_valid"
    EXACT_MECHANISM_SEQUENCE = "exact_mechanism_sequence"
    MECHANISM_CONSTRAINED = "mechanism_constrained"
    CLOSED_MECHANISM_VARIANTS = "closed_mechanism_variants"


class ExtraEvidenceRule(StrEnum):
    """How truthful evidence outside the minimum answer is treated."""

    NOT_APPLICABLE = "not_applicable"
    FORBID = "forbid"
    REQUIRE_EXACT_SET = "require_exact_set"
    REQUIRE_EVIDENCE_CLOSURE = "require_evidence_closure"
    ALLOW_TRUTHFUL = "allow_truthful"
    ALLOW_IF_GRAPH_VALID = "allow_if_graph_valid"


class ExtraEvidencePolicy(StrictModel):
    """Public rules for additional identities, edges, and properties."""

    entities: ExtraEvidenceRule = ExtraEvidenceRule.NOT_APPLICABLE
    route_edges: ExtraEvidenceRule = ExtraEvidenceRule.NOT_APPLICABLE
    supporting_edges: ExtraEvidenceRule = ExtraEvidenceRule.ALLOW_IF_GRAPH_VALID
    properties: ExtraEvidenceRule = ExtraEvidenceRule.ALLOW_IF_GRAPH_VALID


class CompletenessContract(StrictModel):
    """Public completeness proof required for the declared answer scope."""

    scope: Literal[
        "entire_population",
        "declared_window",
        "single_witness",
        "bounded_negative",
    ]
    require_complete_answer: bool
    require_complete_proof: bool
    require_total_count: bool
    require_stable_ordering: bool


class AcceptanceSpec(StrictModel):
    """Solver-visible semantic contract compiled from the same typed claim as scoring."""

    claim_kind: Literal["route", "set", "count", "decision", "absence"]
    answer_policy: AnswerPolicy
    semantics: RelationshipSemantics
    population_scope: PopulationScope
    source_role: NonEmptyStr | None = None
    target_role: NonEmptyStr | None = None
    selection: SelectionExpression | None = None
    route_acceptance: RouteAcceptanceKind = RouteAcceptanceKind.NOT_APPLICABLE
    required_mechanisms: tuple[NonEmptyStr, ...] = ()
    required_mechanism_categories: tuple[NonEmptyStr, ...] = ()
    allowed_mechanisms: tuple[NonEmptyStr, ...] = ()
    mechanisms_are_ordered: bool = False
    required_context: tuple[RelationshipPattern, ...] = ()
    required_properties: tuple[PropertyPredicate, ...] = ()
    required_relationships: tuple[RelationshipPattern, ...] = ()
    required_route: tuple[RelationshipPattern, ...] = ()
    excluded_relationships: tuple[RelationshipPattern, ...] = ()
    excluded_mechanisms: tuple[NonEmptyStr, ...] = ()
    negative_reason_codes: tuple[NegativeReasonCode, ...] = ()
    extra_evidence: ExtraEvidencePolicy
    completeness: CompletenessContract
    bounds: ExecutionBounds

    @model_validator(mode="after")
    def fields_match_claim_and_policy(self) -> AcceptanceSpec:
        allowed_policies = {
            "route": {"exact_route", "mechanism_valid_route", "closed_route_variants"},
            "set": {"exact_set"},
            "count": {"exact_count"},
            "decision": {"decision"},
            "absence": {"bounded_negative", "decision"},
        }
        if self.answer_policy.kind not in allowed_policies[self.claim_kind]:
            raise ValueError(
                f"{self.answer_policy.kind} is not valid for {self.claim_kind} acceptance"
            )
        if self.claim_kind in {"route", "absence"}:
            if self.source_role is None or self.target_role is None:
                raise ValueError("route/absence acceptance requires source and target roles")
            if self.source_role == self.target_role:
                raise ValueError("source and target roles must differ")
        elif self.source_role is not None or self.target_role is not None:
            raise ValueError("only route/absence acceptance may declare endpoint roles")
        if self.claim_kind == "route":
            if self.route_acceptance is RouteAcceptanceKind.NOT_APPLICABLE:
                raise ValueError("route acceptance must declare its accepted route language")
            if self.selection is not None:
                raise ValueError("route acceptance cannot declare a set selection")
        elif self.route_acceptance is not RouteAcceptanceKind.NOT_APPLICABLE:
            raise ValueError("only route claims may declare route_acceptance")
        if self.claim_kind in {"set", "count"}:
            if self.selection is None:
                raise ValueError("set/count acceptance requires its public selection")
        elif self.selection is not None:
            raise ValueError("only set/count acceptance may declare a selection")
        route_only = (
            self.required_mechanisms,
            self.required_mechanism_categories,
            self.mechanisms_are_ordered,
            self.excluded_relationships,
            self.excluded_mechanisms,
        )
        if self.claim_kind != "route" and any(route_only):
            raise ValueError("route-only acceptance fields reached a non-route claim")
        if self.claim_kind != "absence" and (self.allowed_mechanisms or self.negative_reason_codes):
            raise ValueError("absence-only acceptance fields reached another claim kind")
        for field_name in (
            "required_mechanism_categories",
            "allowed_mechanisms",
            "excluded_mechanisms",
            "negative_reason_codes",
        ):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must be unique")
        if self.route_acceptance is RouteAcceptanceKind.ANY_GRAPH_VALID and (
            self.required_mechanisms
            or self.required_mechanism_categories
            or self.required_context
            or self.required_properties
            or self.excluded_relationships
            or self.excluded_mechanisms
        ):
            raise ValueError("any_graph_valid cannot carry hidden route constraints")
        return self


class MCPClaimEvidenceContract(StrictModel):
    """Public, model-blind contract for evidence that may unlock MCP grading."""

    tool_name: Literal["cypher_query"] = "cypher_query"
    operation: Literal["run"] = "run"
    result_kind: Literal["entities", "scalar_count", "path"]
    required_input_roles: tuple[NonEmptyStr, ...] = ()
    projection_types: tuple[NonEmptyStr, ...] = ()

    @model_validator(mode="after")
    def fields_are_unique(self) -> MCPClaimEvidenceContract:
        for field_name in (
            "required_input_roles",
            "projection_types",
        ):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must be unique")
        return self


class TrackBinding(StrictModel):
    track: Track
    capability_profile_id: NonEmptyStr
    semantics: RelationshipSemantics
    bounds: ExecutionBounds
    direct_query_policy_version: NonEmptyStr | None = None
    mcp_tool_loop: NonEmptyStr | None = None
    mcp_resource_mode: NonEmptyStr | None = None
    mcp_binding_mode: MCPBindingMode | None = None
    mcp_evidence_contract: MCPClaimEvidenceContract | None = None

    @model_validator(mode="after")
    def track_fields_match(self) -> TrackBinding:
        if self.track is Track.DIRECT:
            if self.direct_query_policy_version is None:
                raise ValueError("direct bindings require a direct query policy version")
            if self.mcp_tool_loop is not None or self.mcp_resource_mode is not None:
                raise ValueError("direct bindings cannot declare MCP loop or resource settings")
            if self.mcp_binding_mode is not None:
                raise ValueError("direct bindings cannot declare an MCP binding mode")
            if self.mcp_evidence_contract is not None:
                raise ValueError("direct bindings cannot declare an MCP evidence contract")
            if self.bounds.max_tool_calls != 0:
                raise ValueError("direct bindings must use a zero tool-call budget")
        else:
            if self.direct_query_policy_version is not None:
                raise ValueError("MCP bindings cannot declare a direct query policy version")
            if self.mcp_tool_loop is None or self.mcp_resource_mode is None:
                raise ValueError("MCP bindings require explicit loop and resource settings")
            if self.mcp_binding_mode is None:
                raise ValueError("MCP bindings require an explicit binding mode")
            if self.mcp_evidence_contract is None:
                raise ValueError("MCP bindings require a public claim evidence contract")
            if self.mcp_binding_mode is MCPBindingMode.BLOCKED:
                raise ValueError("blocked MCP bindings cannot produce TaskBundles")
            if self.bounds.max_tool_calls == 0:
                raise ValueError("MCP bindings require a positive tool-call budget")
        return self


class TaskBundle(StrictModel):
    protocol_version: Literal["ori-eval-protocol-v2"] = PROTOCOL_VERSION
    manifest_schema_version: Literal["ori-generated-manifest-v3"] = MANIFEST_SCHEMA_VERSION
    task_id: NonEmptyStr
    revision: int = Field(strict=True, gt=0)
    product: NonEmptyStr
    claim_kind: Literal["route", "set", "count", "decision", "absence"]
    answer_policy: AnswerPolicy
    acceptance_spec: AcceptanceSpec
    binding: TrackBinding
    input_entities: tuple[EntityRef, ...] = ()
    question: NonEmptyStr
    answer_schema: dict[str, Any]
    generic_instructions: tuple[NonEmptyStr, ...] = ()
    claim_fingerprint: Fingerprint
    prompt_fingerprint: Fingerprint
    task_fingerprint: Fingerprint

    @field_validator("answer_schema")
    @classmethod
    def answer_schema_is_public_json(cls, value: dict[str, Any]) -> dict[str, Any]:
        forbidden = {
            "correct",
            "expected_answer",
            "expected_count",
            "expected_decision",
            "expected_entities",
            "reference_cypher",
            "reference_nodes",
            "reference_results",
            "ref_result",
            "route_variants",
            "valid_node_names",
        }

        def visit(item: Any) -> None:
            if isinstance(item, dict):
                for key, nested in item.items():
                    if not isinstance(key, str):
                        raise ValueError("answer schema keys must be strings")
                    if key.casefold() in forbidden:
                        raise ValueError(f"answer schema contains oracle field: {key}")
                    visit(nested)
            elif isinstance(item, (list, tuple)):
                for nested in item:
                    visit(nested)
            elif item is not None and not isinstance(item, (str, int, float, bool)):
                raise ValueError(f"answer schema contains non-JSON value: {type(item).__name__}")

        visit(value)
        return value

    @model_validator(mode="after")
    def policy_matches_claim_kind(self) -> TaskBundle:
        allowed = {
            "route": {"exact_route", "mechanism_valid_route", "closed_route_variants"},
            "set": {"exact_set"},
            "count": {"exact_count"},
            "decision": {"decision"},
            "absence": {"bounded_negative", "decision"},
        }
        if self.answer_policy.kind not in allowed[self.claim_kind]:
            raise ValueError(f"{self.answer_policy.kind} is not valid for {self.claim_kind} claims")
        if self.acceptance_spec.claim_kind != self.claim_kind:
            raise ValueError("acceptance claim kind does not match task claim kind")
        if self.acceptance_spec.answer_policy != self.answer_policy:
            raise ValueError("acceptance policy does not match task answer policy")
        if self.acceptance_spec.semantics is not self.binding.semantics:
            raise ValueError("acceptance semantics do not match track binding semantics")
        if self.acceptance_spec.bounds != self.binding.bounds:
            raise ValueError("acceptance bounds do not match track binding bounds")
        input_ids = [entity.object_id for entity in self.input_entities]
        if len(input_ids) != len(set(input_ids)):
            raise ValueError("public input entities must have unique object IDs")
        input_roles = [entity.role for entity in self.input_entities]
        if len(input_roles) != len(set(input_roles)):
            raise ValueError("public input entities must have unique logical roles")
        evidence_contract = self.binding.mcp_evidence_contract
        if evidence_contract is not None:
            unknown_roles = sorted(set(evidence_contract.required_input_roles) - set(input_roles))
            if unknown_roles:
                raise ValueError(
                    f"MCP evidence contract references non-public input roles: {unknown_roles}"
                )
        return self


class RouteVariant(StrictModel):
    variant_id: NonEmptyStr
    edges: tuple[EdgeWitness, ...]


class EntityPropertyFact(StrictModel):
    entity_id: NonEmptyStr
    key: NonEmptyStr
    value: JsonScalar


class GraphFactRegistry(StrictModel):
    """One sealed, corpus-wide truth registry shared by every task oracle."""

    edge_keys: tuple[NonEmptyStr, ...]
    edge_property_facts: tuple[NonEmptyStr, ...]
    entity_property_facts: tuple[NonEmptyStr, ...]
    registry_fingerprint: Fingerprint

    @model_validator(mode="after")
    def fingerprint_matches(self) -> GraphFactRegistry:
        for field_name in (
            "edge_keys",
            "edge_property_facts",
            "entity_property_facts",
        ):
            values = getattr(self, field_name)
            if values != tuple(sorted(set(values))):
                raise ValueError(
                    f"graph fact registry {field_name} must be sorted and unique"
                )
        expected = canonical_sha256(
            self,
            exclude_fields=("registry_fingerprint",),
        )
        if self.registry_fingerprint != expected:
            raise ValueError("graph fact registry fingerprint mismatch")
        return self


class NegativeWitness(StrictModel):
    reason_code: NegativeReasonCode
    checked_entity_ids: tuple[NonEmptyStr, ...] = ()
    checked_edges: tuple[EdgeWitness, ...] = ()
    checked_properties: tuple[EntityPropertyFact, ...] = ()
    max_hops: int = Field(strict=True, ge=0)
    witness_absent: bool


class OracleBundle(StrictModel):
    protocol_version: Literal["ori-eval-protocol-v2"] = PROTOCOL_VERSION
    oracle_id: NonEmptyStr
    task_id: NonEmptyStr
    claim: ClaimSpec
    claim_fingerprint: Fingerprint
    task_fingerprint: Fingerprint
    graph_fingerprint: Fingerprint
    resolved_roles: tuple[EntityRef, ...] = ()
    expected_entities: tuple[EntityRef, ...] = ()
    expected_count: int | None = Field(default=None, strict=True, ge=0)
    expected_decision: bool | None = Field(default=None, strict=True)
    route_variants: tuple[RouteVariant, ...] = ()
    graph_edge_registry: tuple[EdgeWitness, ...] = ()
    graph_fact_registry_fingerprint: Fingerprint
    required_mechanisms: tuple[NonEmptyStr, ...] = ()
    required_context: tuple[EdgeWitness, ...] = ()
    required_properties: tuple[EntityPropertyFact, ...] = ()
    source_id: NonEmptyStr | None = None
    target_id: NonEmptyStr | None = None
    forbidden_entity_ids: tuple[NonEmptyStr, ...] = ()
    forbidden_edges: tuple[EdgeWitness, ...] = ()
    negative_witnesses: tuple[NegativeWitness, ...] = ()
    oracle_fingerprint: Fingerprint


class EvidenceIR(StrictModel):
    task_id: NonEmptyStr
    entities: tuple[EntityRef, ...] = ()
    edges: tuple[EdgeWitness, ...] = ()
    count: int | None = Field(default=None, strict=True, ge=0)
    decision: bool | None = Field(default=None, strict=True)
    path_status: PathStatus = PathStatus.UNKNOWN
    supporting_edges: tuple[EdgeWitness, ...] = ()
    observed_properties: tuple[EntityPropertyFact, ...] = ()
    negative_reason_codes: tuple[NegativeReasonCode, ...] = ()
    rejected_decoy_ids: tuple[NonEmptyStr, ...] = ()
    truncated: bool = False
    graph_fact_attestation: Fingerprint | None = None
    raw_digest: Fingerprint
    normalization_warnings: tuple[NonEmptyStr, ...] = ()


class VerdictDiagnostics(StrictModel):
    missing_entity_ids: tuple[NonEmptyStr, ...] = ()
    extra_entity_ids: tuple[NonEmptyStr, ...] = ()
    missing_edges: tuple[EdgeWitness, ...] = ()
    extra_edges: tuple[EdgeWitness, ...] = ()
    precision: float | None = Field(default=None, strict=True, ge=0, le=1)
    recall: float | None = Field(default=None, strict=True, ge=0, le=1)
    route_overlap: float | None = Field(default=None, strict=True, ge=0, le=1)


class Verdict(StrictModel):
    task_id: NonEmptyStr
    status: VerdictStatus
    reason: NonEmptyStr
    task_fingerprint: Fingerprint
    oracle_fingerprint: Fingerprint
    evidence_fingerprint: Fingerprint
    comparator_fingerprint: Fingerprint
    diagnostics: VerdictDiagnostics = VerdictDiagnostics()


class ToolCapability(StrictModel):
    name: NonEmptyStr
    operation: NonEmptyStr
    semantics: tuple[RelationshipSemantics, ...]
    supports_pagination: bool
    stable_ordering: bool
    reports_truncation: bool
    reports_total_count: bool
    proof_strength: ProofStrength
    max_output_bytes: int = Field(strict=True, gt=0)


class CapabilityProfile(StrictModel):
    profile_id: NonEmptyStr
    track: Track
    bloodhound_ce_version: NonEmptyStr
    mcp_server_revision: NonEmptyStr | None = None
    finalization_policy_fingerprint: Fingerprint | None = None
    direct_query_policy_version: NonEmptyStr | None = None
    direct_result_contract_version: NonEmptyStr | None = None
    containment_base_commit: GitCommit | None = None
    tools: tuple[ToolCapability, ...] = ()
    resources: tuple[NonEmptyStr, ...] = ()
    cypher_enabled: bool = False
    profile_fingerprint: Fingerprint

    @model_validator(mode="after")
    def profile_matches_track(self) -> CapabilityProfile:
        if self.track is Track.DIRECT:
            if self.direct_query_policy_version is None:
                raise ValueError("direct profiles require a direct query policy version")
            if self.direct_result_contract_version is None:
                raise ValueError("direct profiles require a result contract version")
            if self.containment_base_commit is None:
                raise ValueError("direct profiles require the pinned containment commit")
            if self.mcp_server_revision is not None or self.tools or self.resources:
                raise ValueError("direct profiles cannot describe MCP surfaces")
            if self.finalization_policy_fingerprint is not None:
                raise ValueError("direct profiles cannot declare MCP finalization policy")
            if self.cypher_enabled:
                raise ValueError("cypher_enabled is an MCP capability flag")
        else:
            if self.mcp_server_revision is None:
                raise ValueError("MCP profiles require a server revision")
            if self.finalization_policy_fingerprint is None:
                raise ValueError("MCP profiles require a finalization policy fingerprint")
            if self.direct_query_policy_version is not None:
                raise ValueError("MCP profiles cannot declare a direct query policy")
            if self.direct_result_contract_version is not None:
                raise ValueError("MCP profiles cannot declare a direct result contract")
            if self.containment_base_commit is not None:
                raise ValueError("MCP profiles cannot declare a containment commit")
        return self


class DirectExecutionReceipt(StrictModel):
    execution_class: ExecutionClass
    failure_type: NonEmptyStr | None = None
    failure_subtype: NonEmptyStr | None = None
    status_code: int | None = Field(default=None, strict=True, ge=100, le=599)
    query_executed: bool | None
    attempts: int | None = Field(default=None, strict=True, ge=0)
    query_fingerprint: Fingerprint | None = None
    policy_version: NonEmptyStr
    policy_rule: NonEmptyStr | None = None
    elapsed_seconds: float = Field(strict=True, ge=0)
    post_query_health: HealthState
    circuit_state: CircuitState
    response_digest: Fingerprint | None = None


class TaskCertification(StrictModel):
    task_id: NonEmptyStr
    state: CertificationState
    task_fingerprint: Fingerprint
    oracle_fingerprint: Fingerprint
    graph_fingerprint: Fingerprint
    compiler_fingerprint: Fingerprint
    comparator_fingerprint: Fingerprint
    certifier_fingerprint: Fingerprint
    capability_profile_fingerprint: Fingerprint
    bounds_fingerprint: Fingerprint
    certified_profile_id: NonEmptyStr | None = None
    live_proof_fingerprint: Fingerprint | None = None
    failures: tuple[NonEmptyStr, ...] = ()
    certification_fingerprint: Fingerprint

    @model_validator(mode="after")
    def lifecycle_requirements_hold(self) -> TaskCertification:
        if self.state in {CertificationState.LIVE_CERTIFIED, CertificationState.CANDIDATE}:
            if self.certified_profile_id is None:
                raise ValueError("live-certified and candidate tasks require a profile")
            if self.live_proof_fingerprint is None:
                raise ValueError("live-certified and candidate tasks require a live proof")
        elif self.live_proof_fingerprint is not None:
            raise ValueError("pre-live certification cannot bind a live proof")
        if self.state is CertificationState.CANDIDATE and self.failures:
            raise ValueError("candidate tasks cannot retain certification failures")
        expected = canonical_sha256(
            self,
            exclude_fields=("certification_fingerprint",),
        )
        if self.certification_fingerprint != expected:
            raise ValueError("task certification fingerprint mismatch")
        return self


class CatalogEntry(StrictModel):
    task_id: NonEmptyStr
    revision: int = Field(strict=True, gt=0)
    product: NonEmptyStr
    track: Track
    family: NonEmptyStr
    tier: int = Field(strict=True, ge=1)
    claim_kind: Literal["route", "set", "count", "decision", "absence"]
    semantics: RelationshipSemantics
    cost_band: NonEmptyStr
    path_concentration_key: NonEmptyStr
    task_fingerprint: Fingerprint
    oracle_fingerprint: Fingerprint
    certification_fingerprint: Fingerprint


class CatalogRelease(StrictModel):
    protocol_version: Literal["ori-eval-protocol-v2"] = PROTOCOL_VERSION
    manifest_schema_version: Literal["ori-generated-manifest-v3"] = MANIFEST_SCHEMA_VERSION
    release_id: NonEmptyStr
    product: NonEmptyStr
    entries: tuple[CatalogEntry, ...]
    graph_fingerprint: Fingerprint
    compiler_fingerprint: Fingerprint
    comparator_fingerprint: Fingerprint
    capability_profile_fingerprint: Fingerprint
    catalog_fingerprint: Fingerprint
    release_fingerprint: Fingerprint

    @model_validator(mode="after")
    def fingerprints_match(self) -> CatalogRelease:
        if self.catalog_fingerprint != canonical_sha256(self.entries):
            raise ValueError("candidate catalog fingerprint mismatch")
        expected = canonical_sha256(
            self,
            exclude_fields=("release_fingerprint",),
        )
        if self.release_fingerprint != expected:
            raise ValueError("candidate release fingerprint mismatch")
        return self
