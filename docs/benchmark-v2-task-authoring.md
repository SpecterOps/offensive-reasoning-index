# ORI V2 task authoring and certification

Protocol v2 treats a benchmark task as a compiled semantic contract, not a
prompt plus a template-specific grader. New task families must follow this
workflow before they can enter a later selector suite.

Read [ORI V2 Design Rationale](benchmark-v2-design-rationale.md) first when you
need the plain-language model, incident history, decision tradeoffs, or scoring
mathematics behind these authoring rules.

## Authoring boundary

Add a typed logical recipe through the task recipe registry exposed by
`src/ori/eval/v2/task_recipes.py`, then add its declarative claim compiler. A recipe must
define:

1. a stable logical task and claim ID;
2. one claim kind: route, set, count, decision, or absence;
3. logical input/output roles and seed-resolved selectors;
4. direct or explicitly bounded transitive semantics;
5. population scope, mechanisms, ordering, context, and exclusions;
6. one explicit answer policy;
7. one solver-visible `AcceptanceSpec` compiled from that claim and policy;
8. independent direct and MCP bindings where supported;
9. execution bounds and selector-facing metadata.

Do not add a comparator branch keyed by task ID, template ID, family, tier, or
prompt text. Do not parse reference Cypher to infer semantics. If a legacy
capability cannot be expressed and certified, compilation must block it or
replace it with an explicitly bounded equivalent.

Selection relationships must use exact canonical BloodHound identifiers. Their
role graph must be a connected, acyclic tree rooted at the projection role, and
may contain at most 16 edges. The product of every declared relationship's
`max_hops` cannot exceed 256. `effective`
relationship semantics require a separately certified derivation and are not an
authorable selection shape. `ClosedRouteVariants` remains a compatibility schema
for previously sealed artifacts; new recipes must use `ExactRoute` or
`MechanismValidRoute` until a dedicated closed-variant authoring and certification
workflow is approved.

The `AcceptanceSpec` must expose every semantic fact that can change the
verdict without disclosing seed-resolved answer identities. This includes route
mechanisms and ordering, context, properties, exclusions, additional truthful
evidence behavior, completeness, and bounds. The structural compiler lint must
be able to derive every sealed scorer requirement from it.

## Claim and policy selection

Use the narrowest correct policy:

- `ExactSet` for complete enumeration; extras fail.
- `ExactCount` for a complete scalar count; no tolerance.
- `ExactRoute` for one exact ordered witness.
- `MechanismValidRoute` when any sealed graph-valid route satisfying the
  declared objective, mechanism order, context, and exclusions is valid.
- `ClosedRouteVariants` is compatibility-only for previously sealed artifacts;
  it is not available to new task recipes.
- `BoundedNegative` for absence with a bounded proof and a mutation that would
  flip the oracle. Its declared proof operation and submitted answer must be
  compatible; for a count-zero route proof, do not additionally require hidden
  partial edges or properties.
- `Decision` for a boolean conclusion backed by the declared entities, edges,
  and properties.

Display names are not identities. Resolve every role to an `EntityRef` and use
object ID as primary identity. Declare aliases only when they identify exactly
one object. Keep query selection separate from answer normalization: query
predicates use the exact canonical `name` or exact `objectid`; aliases may
normalize a final answer but are not alternate live graph property values.
State every semantic traversal ceiling in the public question, and do not
describe an anchor with a property unless that property is part of the typed
selection.

## Track bindings

Author direct and MCP bindings separately.

For direct:

- require direct-query policy v3;
- bind the versioned direct result contract;
- keep `max_tool_calls` at zero;
- return answer nodes only for set claims, a scalar for count claims, and an
  actual `RETURN p` path for route claims;
- ensure a returned path can include ordered edge witnesses;
- when returning a supporting relationship separately from `p`, also return
  both endpoint node variables so BloodHound cannot omit a non-path endpoint;
- make cycle rejection a comparator responsibility over the returned witness;
  do not require every pairwise node-inequality predicate in the query;
- do not impose a global ordering on paths or complete exact sets; require
  stable ordering only for a deterministic set window;
- use one scalar route count for a bounded-negative direct claim; exact
  singleton endpoint bindings followed by `OPTIONAL MATCH p=...` are the
  preferred zero-preserving form;
- keep JSON maps/list construction out of Cypher;
- never rely on query text to prove an answer.

For MCP:

- choose an explicit certified loop;
- keep resource mode explicit;
- select explicitly Cypher-enabled support from the pinned capability profile
  for the current certified runtime;
- treat high-level tools as exploratory until a future tool-only binding
  publishes and certifies its own solver-visible proof contract;
- compile one public evidence result kind: entities, scalar count, or path;
- require only public input selectors and public projection types for
  claim-relevance; never copy sealed mechanisms, properties, or expected facts
  into the runtime relevance gate;
- require stable ordering, pagination, total count, or truncation reporting
  whenever completeness depends on it;
- return entity rows with a stable identity column such as
  `entity.objectid AS object_id`; BloodHound may flatten these rows into
  `data.literals` while reporting zero graph-node cardinality;
- accept stable ordering only when the ordered property or current alias is
  proven to derive from `objectid`, including safe `WITH` passthroughs but not
  property rebindings;
- bind a companion count and every page to the same normalized population, and
  count distinct result identities whenever the page window is deduplicated;
  if a distinct count is paired with row-preserving pages, require the receipts
  to prove one globally unique explicit object ID per row instead of trusting
  equal scalar and row totals;
- use exact `name`/`objectid` selector predicates and return an identity-bearing
  result variable other than a required input selector;
- preserve BloodHound's case-sensitive label, relationship, and property
  identifiers; accept an anonymous node carrying the exact public selector as
  equivalent to a named anchor;
- accept `coalesce(boolean_property, false)` only when it is logically
  equivalent to the declared typed boolean predicate;
- treat `Principal` as an abstract projection type and validate the returned
  identity/count variable instead of emitting or requiring `:Principal`;
- preserve positive route/decision graph witnesses when the pinned MCP response
  also contains endpoint scalar literals;
- require an ordered route witness itself to begin and end at the public claim
  selectors; for decision claims, require both public subjects to occur on the
  same returned path but permit them to be interior nodes;
- require the returned path variable to preserve lineage through `WITH`
  projections and the BloodHound receipt to contain actual nodes and edges;
  node-only results and rebound path variables are not route proof;
- require public endpoint selectors to remain live in the returned path's
  Cypher scope; selectors dropped by `WITH` or added only by a later detached
  `MATCH` do not bind that path;
- accept additional route/decision entities, edges, and properties only when
  the corpus-wide graph registry attests them and they remain connected to the
  returned witness;
- add any newly solver-assertable stable property to the shared archive/live
  registry and declare its CE normalization explicitly; never add a
  template-specific comparator exception;
- ensure a later complete proof can supersede an earlier truncated attempt,
  while a later truncation revokes readiness;
- bind the final set to mechanically complete identity pages and bind final
  witness edges/properties to the latest complete claim-relevant receipt;
  a schema-only retry cannot erase a complete 500-identity receipt or invent
  unsupported graph facts;
- for a bounded-negative scalar, count one source-to-objective path variable
  over the complete public hop bound; a broader wildcard/undirected search can
  prove zero but its non-zero result cannot contradict the narrower claim; the
  same rule applies when the relationship vocabulary is exact but the query's
  hop ceiling is wider than the public ceiling; accept ordinary exact one-edge
  syntax as equivalent to `*1..1` for a one-hop claim, and reject conflicting
  inline/`WHERE` endpoint selectors, selector predicates on non-endpoint
  variables, and undeclared extra endpoint labels;
- make deterministic window claims use the exact public offset and limit as one
  execution page, without hidden subpages or a hidden global count; normalize an
  omitted first-page `SKIP 0` to the same population key as later contiguous
  pages, alpha-normalize bound variable names across those pages, and treat an
  equivalent single anonymous `COUNT(*)` population as the same population;
- block the binding if the capability cannot prove the claim.

Safety admission controls are model-blind and applied by the harness after the
model produces a query or tool call. The public task envelope discloses the
generic execution budgets and supported result grammar needed to form a
bounded, executable answer. Generic CySQL compatibility rules—such as using
`RETURN p` and avoiding map/list construction—are execution constraints, not
reasoning hints. The envelope never discloses task-specific policy decisions,
reference queries, expected facts, or grader behavior.

The runtime provider payload should contain the question once and one
authoritative public task contract. Do not also inject a discovered MCP server
prompt whose resource workflow conflicts with the certified track binding.

## Required bounds

Every binding must set:

- maximum hops;
- maximum result cardinality;
- page size, starting result offset, and maximum pages;
- total-count and stable-order requirements;
- maximum output and transcript bytes;
- maximum tool calls;
- timeout.

Use a deterministic bounded page, subset, count, or route claim when a complete
unbounded enumeration would exceed those limits. Its typed selection, public
question, binding, runtime projector, and certification replay must agree on
the same offset and limit. Do not certify a task when a truncated answer is
indistinguishable from a complete answer.

For complete MCP set claims, the compiler currently uses a fixed public
1,000-identity capacity over 500-row pages and requires a companion total. The
capacity must not be derived from the sealed expected set size. This lets the
exact-set comparator grade extras while avoiding answer-count leakage. Direct
tasks receive a 180-second whole-task deadline. MCP tasks receive a 600-second
floor plus a capacity-derived set-serialization allowance, capped at 1,200
seconds. The default MCP provider read sub-deadline is 240 seconds. Do not
override these values with smaller hidden model-loop, tool, or read deadlines;
campaign readiness rejects runtime caps that contradict certified bounds.

## Fixture contract

Every compiled task receives:

- perfect;
- wrong;
- empty;
- alias;
- extra entity;
- decoy;
- alternate route.

The perfect and empty fixtures must both satisfy the public answer schema.
“Empty” means schema-valid empty evidence that the comparator rejects; it must
not be implemented as malformed output. Schema failures are covered separately
by the malformed-answer fixture so live parity cannot silently mark a required
empty case inapplicable.

Route tasks also exercise reversed edge, disconnected path, cycle, malformed
answer, and source/target mismatch. If a named fixture is structurally
inapplicable, the compiler must record why and identify the registered
adversarial micrograph that covers the underlying policy. Silent omission is
not allowed.

Golden results should be independently authored when they protect against the
compiler and comparator sharing the same mistake.

## Certification lifecycle

Tasks advance only through:

```text
draft -> compiled -> offline-certified -> live-certified(profile) -> candidate
```

Offline certification:

```bash
uv run ori compile-v2 \
  --manifest <generated-manifest-v2.json> \
  --archive <matching.zip> \
  --product <simple|complex> \
  --track <direct|mcp> \
  --output-dir <directory>
```

Live certification:

```bash
uv run --env-file <bloodhound-env> \
  ori certify-v2-live \
  --manifest <generated-manifest-v2.json> \
  --archive <matching.zip> \
  --product <simple|complex> \
  --output-dir <directory>
```

The live graph must already match. The command performs read-only graph
projection and deterministic fixture replay; it neither uploads data nor calls
a model.

Promotion is invalidated by changes to the task, prompt, oracle, graph,
compiler, comparator, capability profile, execution bounds, containment
policy, or MCP finalization policy.

Before a candidate enters a model campaign, validate the exact local bundle:

```bash
uv run --env-file <bloodhound-env> \
  ori run-v2 \
  --config <models-v2.local.yaml>
```

This readiness form never calls a model. It proves the candidate release,
sealed oracle, capability profile, pinned MCP revision, and current live graph
still match. `--execute` is a separate, explicit boundary and must not be used
as part of task authoring or certification.

## Candidate catalog contract

Each track publishes a `CatalogRelease` for the future Task Selector Suite. It
contains:

```text
schema_version
protocol_version
release_id
product
graph_fingerprint
compiler_fingerprint
comparator_fingerprint
capability_profile_fingerprint
catalog_fingerprint
release_fingerprint
entries[]
```

Each entry includes:

```text
task_id
revision
product
track
family
tier
claim_kind
semantics
cost_band
path_concentration_key
public_semantic_fingerprint
equivalent_task_ids
task_fingerprint
oracle_fingerprint
certification_fingerprint
```

The release contains one deterministic representative per unique public
semantic fingerprint. Every equivalent task ID remains compiled,
oracle-bound, and certified and is recorded in `equivalent_task_ids`. Promotion
fails if two public-equivalent tasks have different sealed scorer outcomes.
The selector may consume only candidate-certified entries and must preserve all
bound fingerprints. Selection policy, quotas, new attack paths, and official
100/100 suites are intentionally separate from task correctness.

The machine-readable contract is
[`schemas/ori-v2-candidate-catalog.schema.json`](schemas/ori-v2-candidate-catalog.schema.json).

## Author checklist

- [ ] Claim, question, policy, oracle, and bounds compile from one recipe.
- [ ] Public input roles are present in the question; expected outputs remain sealed.
- [ ] No solver-visible artifact contains oracle data.
- [ ] Direct and MCP semantics are explicit and independently supported.
- [ ] The claim is bounded and completeness is observable.
- [ ] All required fixtures pass or carry registered micrograph coverage.
- [ ] Direct, MCP, Inspect, and offline projections use the shared Evidence IR.
- [ ] No comparator or runtime branch uses task/template identity.
- [ ] Same-seed fingerprints are deterministic.
- [ ] Changed seeds preserve the semantic contract shape while resolving new identities.
- [ ] Offline and live Evidence IR and verdict fingerprints agree.
- [ ] Pre/mid/post live graph fingerprints match.
- [ ] The task reaches candidate state under its pinned capability profile.
- [ ] `ori run-v2 --config ...` readiness passes without launching a model.
