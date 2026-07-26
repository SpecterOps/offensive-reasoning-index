# ORI V2 task authoring and certification

Protocol v2 treats a benchmark task as a compiled semantic contract, not a
prompt plus a template-specific grader. New task families must follow this
workflow before they can enter a later selector suite.

## Authoring boundary

Add a declarative claim recipe in `src/ori/eval/v2/compiler.py`. A recipe must
define:

1. a stable logical task and claim ID;
2. one claim kind: route, set, count, decision, or absence;
3. logical input/output roles and seed-resolved selectors;
4. direct, transitive, or effective semantics;
5. population scope, mechanisms, ordering, context, and exclusions;
6. one explicit answer policy;
7. independent direct and MCP bindings where supported;
8. execution bounds and selector-facing metadata.

Do not add a comparator branch keyed by task ID, template ID, family, tier, or
prompt text. Do not parse reference Cypher to infer semantics. If a legacy
capability cannot be expressed and certified, compilation must block it or
replace it with an explicitly bounded equivalent.

## Claim and policy selection

Use the narrowest correct policy:

- `ExactSet` for complete enumeration; extras fail.
- `ExactCount` for a complete scalar count; no tolerance.
- `ExactRoute` for one exact ordered witness.
- `MechanismValidRoute` when any sealed graph-valid route satisfying the
  declared objective, mechanism order, context, and exclusions is valid.
- `ClosedRouteVariants` for a finite, explicitly sealed set of acceptable
  routes.
- `BoundedNegative` for absence with a bounded proof and a mutation that would
  flip the oracle.
- `Decision` for a boolean conclusion backed by the declared entities, edges,
  and properties.

Display names are not identities. Resolve every role to an `EntityRef` and use
object ID as primary identity. Declare aliases only when they identify exactly
one object.

## Track bindings

Author direct and MCP bindings separately.

For direct:

- require direct-query policy v3;
- keep `max_tool_calls` at zero;
- ensure a returned path can include ordered edge witnesses;
- never rely on query text to prove an answer.

For MCP:

- choose an explicit certified loop;
- keep resource mode explicit;
- select tool-only or explicitly Cypher-enabled support from the pinned
  capability profile;
- require stable ordering, pagination, total count, or truncation reporting
  whenever completeness depends on it;
- block the binding if the capability cannot prove the claim.

Safety admission controls are model-blind and applied by the harness after the
model produces a query or tool call. The public task envelope discloses only
the generic execution budgets needed to form a bounded answer. It never
discloses policy rules, reference queries, expected facts, or task-specific
grader hints.

## Required bounds

Every binding must set:

- maximum hops;
- maximum result cardinality;
- page size and maximum pages;
- total-count and stable-order requirements;
- maximum output and transcript bytes;
- maximum tool calls;
- timeout.

Use a deterministic bounded page, subset, count, or route claim when a complete
unbounded enumeration would exceed those limits. Do not certify a task when a
truncated answer is indistinguishable from a complete answer.

## Fixture contract

Every compiled task receives:

- perfect;
- wrong;
- empty;
- alias;
- extra entity;
- decoy;
- alternate route.

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
task_fingerprint
oracle_fingerprint
certification_fingerprint
```

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
