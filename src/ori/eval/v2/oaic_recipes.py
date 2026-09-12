"""Explicit OAIC contracts and separately fingerprinted release metadata."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import Field, model_validator

from ori.eval.tasks import _TIER6_COMPLEX_TEMPLATE_IDS, Task

from . import compiler as c
from .fingerprint import canonical_sha256
from .graph import GraphSnapshot
from .public_surfaces import public_semantic_fingerprint
from .schema import (
    AbsenceClaim,
    BoundedNegativePolicy,
    CountClaim,
    DecisionClaim,
    DecisionPolicy,
    EntitySelector,
    ExactCountPolicy,
    ExactSetPolicy,
    NegativeReasonCode,
    NegativeWitness,
    PopulationScope,
    RelationshipSemantics,
    SetClaim,
    StrictModel,
    Track,
)
from .selection import evaluate_selection

PRODUCT = "oaic-2026-v1"
Kind = Literal["set", "count", "route", "decision", "absence"]


@dataclass(frozen=True)
class OAICRecipe:
    recipe_id: str
    variant_id: str
    eligibility: Literal["main", "diagnostic"]
    template_id: str
    claim_kind: Kind
    family: str
    tier: int
    concentration_key: str
    supported_tracks: tuple[Track, ...]
    legacy_id: str


_POPULATIONS = (
    ("spn-users", "t2_kerberoast_chain-02", "t2_kerberoast_chain"),
    ("unconstrained-computers", "t3_unconstrained_delegation-02", "t3_unconstrained_delegation"),
    ("trusted-to-auth-users", "t3_constrained_delegation-02", "t3_constrained_delegation"),
    ("privileged-session-computers", "t1_has_session-01", "t1_has_session"),
    ("direct-da-users", "t1_group_membership-02", "t1_group_membership"),
    ("direct-da-principals", "mcp-global-da-direct-members", "t1_group_membership"),
    ("transitive-da-principals", "global-da-members", "t1_group_membership"),
    ("da-administered-computers", "global-admin-to", "t1_admin_to"),
    ("subject-user-groups", "mcp-user-group-memberships-mmoore", "t1_group_membership"),
    (
        "subject-user-privileged-groups",
        "mcp-user-privileged-group-memberships",
        "t1_group_membership",
    ),
    (
        "privileged-user-session-locations",
        "mcp-user-session-locations-privileged-user",
        "t1_has_session",
    ),
    ("direct-dc-admins", "mcp-computer-admin-users-dc01", "t1_admin_to"),
    ("server-admins-members", "mcp-group-members-server-admins", "t2_nested_groups"),
    ("infra-team-parent-groups", "mcp-group-memberships-infra-team", "t2_nested_groups"),
)
# Full populations, not additional windows or alternate names for old contracts.
_EXTRA_LEGACY_POPULATIONS = (
    ("subject-computer-session-users", "mcp-computer-active-sessions", "t1_has_session"),
    ("server-admins-computers", "mcp-group-admin-rights-server-admins", "t2_nested_groups"),
    ("file-server-admin-principals", "mcp-computer-admin-users-srv-file-01", "t1_admin_to"),
    (
        "unconstrained-computer-session-users",
        "mcp-computer-active-sessions-unconstrained",
        "t3_unconstrained_delegation",
    ),
    (
        "constrained-user-delegation-targets",
        "mcp-user-constrained-delegation-targets",
        "t3_constrained_delegation",
    ),
)
_PROPERTY_POPULATIONS = (
    ("admincount-users", "User", "admincount", True, "t1_group_membership"),
    ("highvalue-groups", "Group", "highvalue", True, "t2_nested_groups"),
    ("domain-controller-computers", "Computer", "isdc", True, "t1_admin_to"),
    (
        "subject-supplied-certificate-templates",
        "CertTemplate",
        "enrolleesuppliessubject",
        True,
        "t4_adcs_esc1",
    ),
    (
        "authentication-certificate-templates",
        "CertTemplate",
        "authenticationenabled",
        True,
        "t4_adcs_esc1",
    ),
    (
        "non-authentication-certificate-templates",
        "CertTemplate",
        "authenticationenabled",
        False,
        "t6_negative_control_invalid_cert",
    ),
    ("group-policy-objects", "GPO", None, None, "t6_gpo_ou_control_tier0"),
    ("organizational-units", "OU", None, None, "t6_gpo_ou_control_tier0"),
    ("admincount-groups", "Group", "admincount", True, "t2_nested_groups"),
    (
        "enterprise-certificate-authorities",
        "EnterpriseCA",
        None,
        None,
        "t4_adcs_esc1",
    ),
)
_RELATIONSHIP_POPULATIONS = (
    (
        "rbcd-source-target-computers",
        "t6_rbcd_computer_takeover_tier0",
        "AllowedToAct",
        "Computer",
        "Computer",
    ),
    ("acl-source-controlled-groups", "t2_acl_chain", "GenericAll", "User", "Group"),
    ("gpo-controller-policy-objects", "t6_gpo_ou_control_tier0", "GenericWrite", "Group", "GPO"),
    (
        "laps-pivot-extended-rights-groups",
        "t6_laps_session_pivot_tier0",
        "AllExtendedRights",
        "User",
        "Group",
    ),
    ("esc1-enroller-certificate-templates", "t4_adcs_esc1", "Enroll", "Group", "CertTemplate"),
)
_ROUTES = (
    "t1_admin_to",
    "t1_has_session",
    "t1_group_membership",
    "t2_kerberoast_chain",
    "t2_acl_chain",
    "t2_nested_groups",
    "t3_constrained_delegation",
    "t4_rbcd_delegation",
    *sorted(_TIER6_COMPLEX_TEMPLATE_IDS),
)
_DECISIONS = (
    "t3_unconstrained_delegation",
    "t4_adcs_esc1",
    "t5_adcs_to_delegation_composite",
    "t6_rbcd_computer_takeover_tier0",
    "t6_gpo_ou_control_tier0",
    "t6_laps_session_pivot_tier0",
    "t6_trust_hopping_tier0",
    "t6_host_session_pivot_tier0",
    "t6_adcs_identity_transition_tier0",
)
_NEGATIVES = (
    ("oaic-negative-admin-target", "AdminTo", 1, "User", "Computer"),
    ("oaic-negative-session-target", "HasSession", 1, "Computer", "User"),
    ("oaic-negative-membership-bridge", "MemberOf", 3, "Group", "Group"),
    ("oaic-negative-delegation-target", "AllowedToDelegate", 1, "User", "Computer"),
    ("oaic-negative-rbcd-target", "AllowedToAct", 1, "Computer", "Computer"),
    ("oaic-negative-acl-target", "GenericAll", 1, "User", "Group"),
    ("oaic-negative-gpo-control", "GenericWrite", 1, "Group", "GPO"),
    ("oaic-negative-identity-transition", "GenericWrite", 1, "Group", "User"),
)


def _registry() -> tuple[OAICRecipe, ...]:
    recipes = []

    def add(kind, name, template, legacy, *, diagnostic=False):
        tier = int(template[1]) if template.startswith("t") else 6
        recipes.append(
            OAICRecipe(
                recipe_id=f"{kind}.{name}",
                variant_id="v1",
                eligibility="diagnostic" if diagnostic else "main",
                template_id=template,
                claim_kind=kind,
                family=name,
                tier=tier,
                concentration_key=template,
                supported_tracks=(Track.DIRECT, Track.MCP),
                legacy_id=legacy,
            )
        )

    for index, (name, legacy, template) in enumerate(_POPULATIONS):
        add("set", name, template, legacy, diagnostic=index >= 12)
        if index not in {10, 11}:
            add("count", name, template, legacy, diagnostic=index >= 12)
    for name, legacy, template in _EXTRA_LEGACY_POPULATIONS:
        for kind in ("set", "count"):
            add(kind, name, template, legacy)
    for name, _type, _property, _value, template in _PROPERTY_POPULATIONS:
        for kind in ("set", "count"):
            add(kind, name, template, name)
    for name, template, _relationship, _source, _target in _RELATIONSHIP_POPULATIONS:
        for kind in ("set", "count"):
            add(kind, name, template, name)
    for template in _ROUTES:
        add(
            "route",
            template,
            template,
            template + ("-02" if template == "t1_has_session" else "-01"),
            diagnostic=template
            in {"t6_path_selection_decoy_routes", "t6_stale_session_contingency"},
        )
    for index, template in enumerate(_DECISIONS):
        add("decision", template, template, template + "-01", diagnostic=index >= 7)
    add(
        "absence",
        "invalid-certificate",
        "t6_negative_control_invalid_cert",
        "t6_negative_control_invalid_cert-01",
    )
    for index, (template, *_rest) in enumerate(_NEGATIVES):
        add("absence", template, template, template, diagnostic=index >= 6)
    return tuple(recipes)


OAIC_RECIPE_REGISTRY = _registry()


class OAICRecipeMetadataEntry(StrictModel):
    task_id: str
    task_fingerprint: str
    public_semantic_fingerprint: str
    recipe_id: str
    variant_id: str
    eligibility: Literal["main", "diagnostic"]
    template_id: str
    claim_kind: Kind
    track: Track
    family: str
    tier: int = Field(strict=True, ge=1)
    concentration_key: str


class OAICRecipeMetadata(StrictModel):
    schema_version: Literal["ori-oaic-recipe-metadata-v1"] = "ori-oaic-recipe-metadata-v1"
    product: Literal["oaic-2026-v1"] = PRODUCT
    seed: int = Field(strict=True)
    track: Track
    source_manifest_fingerprint: str
    graph_fingerprint: str
    compiler_fingerprint: str
    catalog_fingerprint: str
    entries: tuple[OAICRecipeMetadataEntry, ...]
    metadata_fingerprint: str

    @model_validator(mode="after")
    def fingerprint_matches(self):
        if (
            canonical_sha256(self, exclude_fields=("metadata_fingerprint",))
            != self.metadata_fingerprint
        ):
            raise ValueError("OAIC recipe metadata fingerprint mismatch")
        return self


def _task_id(recipe: OAICRecipe, track: Track) -> str:
    return f"{PRODUCT}.{track.value}.{recipe.recipe_id}@1"


def _metadata(public_tasks, *, seed, track, source, graph, compiler, catalog):
    recipes = {_task_id(recipe, track): recipe for recipe in OAIC_RECIPE_REGISTRY}
    if len(public_tasks) != len(recipes) or {task.task_id for task in public_tasks} != set(recipes):
        raise c.V2CompileError("OAIC public catalog does not contain the exact known recipe roster")
    entries = []
    semantics = set()
    for task in public_tasks:
        recipe = recipes[task.task_id]
        if (
            task.product != PRODUCT
            or task.binding.track is not track
            or task.claim_kind != recipe.claim_kind
        ):
            raise c.V2CompileError("OAIC task recipe binding mismatch")
        semantic = public_semantic_fingerprint(task)
        if semantic in semantics:
            raise c.V2CompileError("OAIC roster contains duplicate public semantics")
        semantics.add(semantic)
        entries.append(
            OAICRecipeMetadataEntry(
                task_id=task.task_id,
                task_fingerprint=task.task_fingerprint,
                public_semantic_fingerprint=semantic,
                recipe_id=recipe.recipe_id,
                variant_id=recipe.variant_id,
                eligibility=recipe.eligibility,
                template_id=recipe.template_id,
                claim_kind=recipe.claim_kind,
                track=track,
                family=recipe.family,
                tier=recipe.tier,
                concentration_key=recipe.concentration_key,
            )
        )
    payload = dict(
        schema_version="ori-oaic-recipe-metadata-v1",
        product=PRODUCT,
        seed=seed,
        track=track,
        source_manifest_fingerprint=source,
        graph_fingerprint=graph,
        compiler_fingerprint=compiler,
        catalog_fingerprint=catalog,
        entries=tuple(entries),
        metadata_fingerprint="0" * 64,
    )
    payload["metadata_fingerprint"] = canonical_sha256(
        payload, exclude_fields=("metadata_fingerprint",)
    )
    return OAICRecipeMetadata.model_validate(payload)


def build_oaic_recipe_metadata(corpus: c.CompiledCorpus) -> OAICRecipeMetadata:
    if corpus.product != PRODUCT:
        raise c.V2CompileError("OAIC metadata requires an OAIC corpus")
    tasks = tuple(task.public for task in corpus.tasks)
    return _metadata(
        tasks,
        seed=corpus.seed,
        track=corpus.track,
        source=corpus.source_manifest_fingerprint,
        graph=corpus.graph_fingerprint,
        compiler=corpus.compiler_fingerprint,
        catalog=canonical_sha256(tasks),
    )


def validate_oaic_recipe_metadata(
    metadata: OAICRecipeMetadata, public_artifact
) -> OAICRecipeMetadata:
    if public_artifact.product != PRODUCT:
        raise c.V2CompileError("OAIC metadata requires an OAIC public artifact")
    expected = _metadata(
        public_artifact.tasks,
        seed=public_artifact.seed,
        track=public_artifact.track,
        source=public_artifact.source_manifest_fingerprint,
        graph=public_artifact.graph_fingerprint,
        compiler=public_artifact.compiler_fingerprint,
        catalog=public_artifact.catalog_fingerprint,
    )
    if metadata != expected:
        raise c.V2CompileError("OAIC recipe metadata does not match known compiled bindings")
    return metadata


def _legacy(recipe):
    mode = {
        "set": "node_set",
        "count": "row_count",
        "route": "path_exists",
        "decision": "path_exists",
        "absence": "no_path",
    }[recipe.claim_kind]
    return Task(
        id=recipe.legacy_id,
        template_id=recipe.template_id,
        tier=recipe.tier,
        category="path_finding",
        question="",
        reference_cypher="",
        grade_mode=mode,
    )


def _selection_recipe(recipe, snapshot, paths):
    name = recipe.recipe_id.split(".", 1)[1]
    for population, object_type, property_name, value, _template in _PROPERTY_POPULATIONS:
        if name != population:
            continue
        predicates = (
            ()
            if property_name is None
            else (
                c.PropertyPredicate(
                    role="result",
                    property_name=property_name,
                    operator=c.PredicateOperator.EQUALS,
                    value=value,
                ),
            )
        )
        description = f"{object_type} objects"
        if property_name is not None:
            description += f" whose {property_name} property equals {json.dumps(value)}"
        return (
            c.SelectionExpression(
                projection_role="result", projection_type=object_type, predicates=predicates
            ),
            (),
            description,
            RelationshipSemantics.DIRECT,
        )
    for population, template, relationship, source_type, target_type in _RELATIONSHIP_POPULATIONS:
        if name != population:
            continue
        path = paths[template]
        matching = tuple(
            edge
            for raw in (*path.get("path_edges", ()), *path.get("supporting_edges", ()))
            if (edge := c._edge_from_raw(raw)).relationship == relationship
            and snapshot.entity(edge.source_id).object_type == source_type
            and snapshot.entity(edge.target_id).object_type == target_type
        )
        if len(matching) != 1:
            raise c.V2CompileError(f"OAIC population requires one typed anchor edge: {name}")
        c._assert_edges_exist(snapshot, matching, purpose=name)
        anchor = c._entity_with_role(snapshot, matching[0].source_id, "subject")
        return (
            c.SelectionExpression(
                anchors=(EntitySelector(role="subject", object_type=source_type),),
                relationships=(
                    c._direct_pattern(
                        "subject",
                        relationship,
                        "result",
                        source_type=source_type,
                        target_type=target_type,
                    ),
                ),
                projection_role="result",
                projection_type=target_type,
            ),
            (anchor,),
            f"{target_type} objects reached by one outbound {relationship} relationship "
            f"from the declared {source_type} subject",
            RelationshipSemantics.DIRECT,
        )
    return c._selection_recipe(_legacy(recipe), snapshot, paths)


def _selection_draft(recipe, snapshot, paths):
    legacy = _legacy(recipe)
    selection, roles, description, semantics = _selection_recipe(recipe, snapshot, paths)
    if recipe.recipe_id.endswith(".transitive-da-principals"):
        selection = selection.model_copy(
            update={
                "projection_type": "Principal",
                "relationships": tuple(
                    pattern.model_copy(update={"source_type": "Principal"})
                    for pattern in selection.relationships
                ),
            }
        )
        description = description.replace("users", "principals")
    if recipe.recipe_id == "set.da-administered-computers":
        selection = selection.model_copy(update={"offset": 0, "limit": 500})
        description = (
            f"{description}, in the deterministic first 500 object-ID-ordered rows "
            "(offset 0, limit 500)"
        )
    result = evaluate_selection(
        snapshot, selection, role_bindings={r.role: r.object_id for r in roles}
    )
    if recipe.claim_kind == "set" and len(result.entities) > 1000:
        raise c.V2CompileError(
            f"OAIC full-set recipe exceeds certified capacity: {recipe.recipe_id}"
        )
    is_count = recipe.claim_kind == "count"
    claim_class = CountClaim if is_count else SetClaim
    claim = claim_class(
        kind=recipe.claim_kind,
        claim_id=f"claim:{recipe.recipe_id}",
        selection=selection,
        semantics=semantics,
        population_scope=PopulationScope.BENCHMARK_NAMESPACE,
    )
    return c._ClaimDraft(
        legacy=legacy,
        claim=claim,
        policy=ExactCountPolicy(kind="exact_count")
        if is_count
        else ExactSetPolicy(kind="exact_set"),
        question_template=(
            f"Return the {'exact count' if is_count else 'complete exact set'} of {description}."
        ),
        resolved_roles=roles,
        expected_entities=() if is_count else result.entities,
        expected_count=result.unpaged_count if is_count else None,
        reference_source="declarative_selection",
    )


def _decision_draft(recipe, snapshot, paths):
    legacy = _legacy(recipe)
    if recipe.template_id in _DECISIONS[:3]:
        return c._decision_draft(legacy, snapshot, paths)
    path = c._path_for_task(legacy, paths)
    edges = tuple(
        c._edge_from_raw(edge)
        for edge in (*path.get("path_edges", ()), *path.get("supporting_edges", ()))
    )
    if not edges:
        raise c.V2CompileError("OAIC decision recipe requires planted evidence")
    c._assert_edges_exist(snapshot, edges, purpose=recipe.recipe_id)
    roles, by_id = c._route_roles(snapshot, edges[:1], edges[1:])
    claim = DecisionClaim(
        kind="decision",
        claim_id=f"claim:{recipe.recipe_id}",
        subjects=tuple(EntitySelector(role=r.role, object_type=r.object_type) for r in roles),
        required_relationships=tuple(c._relationship_pattern(e, by_id, snapshot) for e in edges),
        semantics=RelationshipSemantics.DIRECT,
        population_scope=PopulationScope.BENCHMARK_NAMESPACE,
    )
    return c._ClaimDraft(
        legacy=legacy,
        claim=claim,
        policy=DecisionPolicy(kind="decision", require_supporting_evidence=True),
        question_template=(
            "Decide whether all declared objects satisfy the complete specified "
            "relationship chain. Return each supporting relationship."
        ),
        resolved_roles=roles,
        expected_entities=tuple(snapshot.entity(r.object_id) for r in roles),
        expected_decision=True,
        graph_edge_registry=edges,
        required_context=edges,
        reference_source="planted_path",
    )


def _negative_draft(recipe, snapshot, paths):
    if recipe.template_id == "t6_negative_control_invalid_cert":
        return c._negative_draft(_legacy(recipe), snapshot, paths)
    spec = next((item for item in _NEGATIVES if item[0] == recipe.template_id), None)
    if spec is None:
        raise c.V2CompileError("unknown OAIC negative recipe")
    _, relationship, bound, source_type, target_type = spec
    path = c._path_for_task(_legacy(recipe), paths)
    metadata = path.get("metadata", {})
    expected = dict(
        negative_control=True,
        relationship=relationship,
        min_hops=1,
        max_hops=bound,
        direction="outbound",
        fixture_role=recipe.eligibility,
        template_version="oaic-negative-v1",
    )
    if not isinstance(metadata, Mapping) or any(
        metadata.get(key) != value for key, value in expected.items()
    ):
        raise c.V2CompileError(f"OAIC negative fixture metadata mismatch: {recipe.template_id}")
    source_id, target_id, near = (
        path.get("source_node"),
        metadata.get("negative_target"),
        metadata.get("near_miss"),
    )
    if not all(isinstance(value, str) and value for value in (source_id, target_id, near)):
        raise c.V2CompileError("OAIC negative fixture requires exact source/target/near-miss IDs")
    try:
        source = c._entity_with_role(snapshot, source_id, "source")
        target = c._entity_with_role(snapshot, target_id, "target")
        near_entity = snapshot.entity(near)
    except KeyError as exc:
        raise c.V2CompileError(
            "OAIC negative fixture references an unknown graph identity"
        ) from exc
    if (
        source.object_type != source_type
        or target.object_type != target_type
        or near == target.object_id
        or near_entity.object_type != target_type
    ):
        raise c.V2CompileError("OAIC negative fixture identity mismatch")
    planted = tuple(c._edge_from_raw(edge) for edge in path.get("path_edges", ()))
    c._assert_edges_exist(snapshot, planted, purpose=recipe.recipe_id)
    frontier = {source.object_id}
    near_reachable = False
    for _ in range(bound):
        frontier = {
            edge.target_id
            for edge in snapshot.relationships
            if edge.relationship == relationship and edge.source_id in frontier
        }
        if target.object_id in frontier:
            raise c.V2CompileError("OAIC declared negative transition is graph-reachable")
        near_reachable |= near in frontier
    if not near_reachable:
        raise c.V2CompileError("OAIC near-miss positive path is absent")
    reasons = (NegativeReasonCode.OBJECTIVE_UNREACHABLE,)
    claim = AbsenceClaim(
        kind="absence",
        claim_id=f"claim:{recipe.recipe_id}",
        source=EntitySelector(role="source", object_type=source_type),
        target=EntitySelector(role="target", object_type=target_type),
        relationships=(relationship,),
        reason_codes=reasons,
        max_hops=bound,
        semantics=RelationshipSemantics.DIRECT,
        population_scope=PopulationScope.BENCHMARK_NAMESPACE,
    )
    return c._ClaimDraft(
        legacy=_legacy(recipe),
        claim=claim,
        policy=BoundedNegativePolicy(kind="bounded_negative"),
        question_template=(
            "Determine whether the exact source can reach the exact target through "
            f"outbound {relationship} within {bound} hops. If absent, return no_path "
            "with objective_unreachable."
        ),
        resolved_roles=(source, target),
        source_id=source.object_id,
        target_id=target.object_id,
        negative_witnesses=(
            NegativeWitness(
                reason_code=reasons[0],
                checked_entity_ids=(source.object_id, target.object_id),
                max_hops=bound,
                witness_absent=True,
            ),
        ),
        reference_source="archive_graph",
    )


def _compile_oaic_recipe(recipe, snapshot, paths, facts, *, track, native_profile=None,
                         native_tool_loop="native-openai-compatible"):
    if recipe not in OAIC_RECIPE_REGISTRY:
        raise c.V2CompileError("unknown or altered OAIC recipe")
    if track not in recipe.supported_tracks:
        raise c.V2CompileError("unsupported OAIC recipe track")
    draft = (
        _selection_draft(recipe, snapshot, paths)
        if recipe.claim_kind in {"set", "count"}
        else c._route_draft(_legacy(recipe), snapshot, paths)
        if recipe.claim_kind == "route"
        else _decision_draft(recipe, snapshot, paths)
        if recipe.claim_kind == "decision"
        else _negative_draft(recipe, snapshot, paths)
    )
    cardinality = (
        draft.expected_count
        if draft.expected_count is not None
        else len(draft.expected_entities) or len(draft.route_variants) or 1
    )
    binding = c._binding(
        track, claim=draft.claim, expected_cardinality=cardinality,
        native_profile=native_profile,
        native_tool_loop=native_tool_loop,
    )
    # Every decision subject is a declared input, not an inferred answer.
    # Keep this OAIC correction separate from historical product contracts.
    inputs = (
        draft.resolved_roles
        if isinstance(draft.claim, DecisionClaim)
        else c._public_input_entities(draft)
    )
    public = c._fingerprinted_task_bundle(
        task_id=_task_id(recipe, track),
        product=PRODUCT,
        claim=draft.claim,
        policy=draft.policy,
        binding=binding,
        input_entities=inputs,
        question=c._question_with_public_inputs(draft.question_template, inputs),
    )
    oracle = c._fingerprinted_oracle(
        draft=draft,
        public=public,
        snapshot=snapshot,
        graph_fact_registry_fingerprint=facts.registry_fingerprint,
    )
    migration = c.MigrationRecord(
        product=PRODUCT,
        track=track,
        legacy_task_id=recipe.recipe_id,
        legacy_template_id=recipe.template_id,
        legacy_grade_mode=draft.legacy.grade_mode,
        family=recipe.family,
        tier=recipe.tier,
        cost_band=c._cost_band(binding),
        path_concentration_key=recipe.concentration_key,
        candidate_task_ids=(public.task_id,),
        status=draft.status,
        claim_kind=recipe.claim_kind,
        semantics=draft.claim.semantics,
        reference_source=draft.reference_source,
        notes=draft.notes,
    )
    return c.CompiledTask(public=public, oracle=oracle, migration=migration)


def compile_oaic_product(
    manifest: Mapping[str, Any], snapshot: GraphSnapshot, *, track: Track,
    native_profile: Any | None = None,
    native_tool_loop: str = "native-openai-compatible",
) -> c.CompiledCorpus:
    if (
        manifest.get("schema_version") != "ori-generated-manifest-v2"
        or manifest.get("metadata", {}).get("benchmark") != PRODUCT
        or snapshot.product != PRODUCT
        or manifest.get("seed") != snapshot.seed
        or manifest.get("domain") != snapshot.domain
    ):
        raise c.V2CompileError("OAIC compilation requires its exact generated product and snapshot")
    paths = c._paths_by_template(manifest)
    known = {recipe.template_id for recipe in OAIC_RECIPE_REGISTRY}
    if set(paths) != known:
        raise c.V2CompileError(
            f"OAIC templates mismatch: missing={sorted(known - set(paths))}, "
            f"unknown={sorted(set(paths) - known)}"
        )
    facts = c.build_graph_fact_registry(snapshot)
    compiled = [
        _compile_oaic_recipe(recipe, snapshot, paths, facts, track=track,
                             native_profile=native_profile, native_tool_loop=native_tool_loop)
        for recipe in OAIC_RECIPE_REGISTRY
    ]
    if Counter(t.public.claim_kind for t in compiled) != {
        "set": 34,
        "count": 32,
        "route": 26,
        "decision": 9,
        "absence": 9,
    }:
        raise c.V2CompileError("OAIC registry quotas changed")
    payload = dict(
        protocol_version=c.PROTOCOL_VERSION,
        manifest_schema_version=c.MANIFEST_SCHEMA_VERSION,
        product=PRODUCT,
        track=track,
        seed=snapshot.seed,
        source_manifest_fingerprint=canonical_sha256(manifest),
        graph_fingerprint=snapshot.graph_fingerprint,
        graph_object_count=len(snapshot.objects),
        graph_fact_registry=facts,
        compiler_fingerprint=c.compiler_fingerprint(),
        tasks=tuple(compiled),
        catalog_fingerprint="0" * 64,
    )
    payload["catalog_fingerprint"] = canonical_sha256(
        payload, exclude_fields=("catalog_fingerprint",)
    )
    corpus = c.CompiledCorpus.model_validate(payload)
    build_oaic_recipe_metadata(corpus)
    return corpus
