# ORI benchmark hardening runbook

## Protocol boundary

ORI has two explicit correctness protocols:

- v1 preserves historical generation, campaign, and scoring behavior;
- `ori-eval-protocol-v2` is an opt-in, typed correctness architecture for the
  current `simple` and `complex` products.

Artifacts are not interchangeable. V2 public artifacts use generated-manifest
revision `ori-generated-manifest-v3`; sealed oracles use
`ori-eval-oracle-v2`. Unknown versions and mixed public/oracle pairs fail
closed. Phase 3/4 profiles remain v1-only.

## V2 correctness model

### One typed claim

The claim compiler is the only supported path to a v2 task. It resolves logical
scenario roles to seed-specific `EntityRef` identities and compiles:

- a solver-visible `TaskBundle`;
- one explicit `AnswerPolicy`;
- an independent direct or MCP `TrackBinding`;
- a scorer-only `OracleBundle`;
- certified execution bounds.

Route, set, count, decision, and absence claims declare their direct,
transitive, or effective semantics. Human-authored question text is a validated
template over claim roles and requested answer fields; it cannot add hidden
grading requirements.

The comparator accepts exactly `AnswerPolicy + OracleBundle + EvidenceIR`.
It cannot receive a legacy task, template ID, or arbitrary metadata. Exact set
and count policies reject extras. Route policies validate ordered edge
witnesses, direction, endpoints, mechanisms, context, exclusions, and
connectivity. Alternative routes pass only under the declared route policy.

### Sealed oracle boundary

Only `TaskBundle` may cross a solver-visible boundary. The common public
projection is used for provider requests, Inspect metadata, transcripts, CSV
metadata, telemetry, and public exports. It recursively rejects oracle field
names. Sentinel tests cover every surface.

Keep scorer-only files private:

```text
*-oracles-v2.private.json
*-offline-certification-v3.private.json
*-live-certification-v3.private.json
*-live-{pre,middle,post}.private.json
*scoring.private.json
```

V2 answers may contain only the declared answer shape. Caller-provided
`correct`, reference result, reference node, valid-node inventory, or oracle
fields are rejected rather than trusted.

### Evidence IR and scoring parity

Direct query results, MCP structured answers, Inspect answers, and offline
replays normalize into one `EvidenceIR`. Identity resolution supports object
IDs, SIDs, canonical names, domain-qualified names, and declared aliases.
Ambiguous aliases fail instead of selecting an arbitrary object.

Only the shared comparator emits a `Verdict`. Precision, recall, overlap,
missing elements, and extras are diagnostics; they never weaken binary policy.
Fixture certification fingerprints both Evidence IR and verdicts so a
live/offline disagreement blocks candidate promotion.

Certification uses two distinct parity layers:

- normalized fixture parity proves the sealed policy and comparator produce the
  same `EvidenceIR` and `Verdict`;
- adapter parity replays the exact fixture payload through the declared direct
  projector or MCP transcript projector/finalizer against both the archive and
  live graph snapshots.

Adapter parity labels each replay as graph-backed, adversarial, or malformed.
Malformed fixtures must produce typed `OUTPUT_INVALID` accounting instead of
being silently marked inapplicable. A perfect fixture must be graph-backed and
gradeable through the real track adapter.

The private certification fingerprint covers the compiler, comparator,
identity resolver, evidence normalizer, direct and MCP adapters, projector,
fixture generator, certification code, and schema. Changing any of those
surfaces invalidates prior offline and live certification.

### Direct execution

V2 does not duplicate containment. Every model-produced query goes through the
authoritative `DirectQueryCoordinator.execute()` path from direct-query policy
v3. The adapter preserves failure type/subtype, HTTP status, execution flag,
attempts, query and policy fingerprints, health result, and circuit state.

Correctness is derived from returned graph evidence, never query text. A path
with nodes but no ordered edges is invalid. Model-attributable policy,
query-timeout, and query errors are incorrect after readiness. Authentication,
transport, server, rate-limit, response, and circuit-open failures receive no
reasoning verdict.

Claim property constraints and graph facts are intentionally different types.
`PropertyPredicate.property_name` describes the unresolved logical claim;
`EntityPropertyFact.key` describes a seed-resolved fact bound to an object ID.
The direct projector accepts only resolved facts at its graph-evidence boundary
and rejects a stale or incorrectly paired predicate object as a typed adapter
error.

### MCP capabilities and finalization

Every MCP binding is checked against a versioned BloodHound CE/MCP capability
profile. The profile records operation semantics, pagination, ordering,
truncation reporting, total-count behavior, proof strength, and output limits.
A binding is tool-only, explicitly Cypher-enabled, or blocked.

Provider loops submit mechanical `ToolObservation` facts. The harness classifies
them against the public claim as useful positive evidence, valid negative proof,
conclusive or inconclusive empty, truncated, invalid arguments, policy
rejection, infrastructure failure, harness failure, irrelevant activity, or
resource read. Models and provider adapters cannot self-declare evidence useful.

Native MCP tool executors return structured text-content blocks. The shared
runtime unwraps those blocks to their JSON text before the V2 projector reads
cardinality, total count, path nodes, or ordered edges. A known complete
operation without a mechanically extractable result count is downgraded to
inconclusive evidence. It must not violate the strict `ToolObservation` schema
or terminate the provider loop.

Only useful positive, valid negative, or conclusive empty evidence unlocks
finalization. Resource reads, irrelevant calls, incomplete empties, truncation,
and errors do not. Native Ollama, native OpenAI-compatible, and Inspect use one
state machine. Certified runs forbid `auto`; one generic schema-only retry is
allowed and unresolved malformed output becomes `OUTPUT_INVALID`.

### Bounds and graph identity

Every task binds maximum traversal depth, result cardinality, page size/pages,
output bytes, transcript bytes, tool calls, and timeout. Oversized enumerations
are compiled as deterministic 500-object pages. Completeness that cannot be
distinguished from truncation blocks certification.

The archive graph digest covers benchmark-owned typed objects,
scorer-relevant properties, canonical ordered relationships, and unexpected
incident attack-path relationships. The live digest uses bounded, stable,
paginated harness queries. Only explicitly declared BloodHound-generated
artifacts are normalized. The graph must match before, between, and after the
two track certifications; any change invalidates the run.

Certification and checkpoints bind task, prompt, oracle, graph, compiler,
comparator, capability profile, execution bounds, containment policy, MCP
finalization policy, and catalog fingerprints. A semantic byte change makes
prior certification stale.

### Model-backed V2 campaigns

`ori run-v2` is the only model-backed V2 entry point. It accepts the strict
`models.v2.example.yaml` shape and rejects unknown fields, V1 protocol values,
mixed track sets, `resource_mode` other than `off`, implicit/automatic tool
loops, concurrency above one, and provider/loop combinations unsupported by
the native runtime.

The solver request contains only the common public envelope: task ID and
fingerprint, track, relationship semantics, execution bounds, question, answer
schema, and generic instructions. It never contains an oracle, reference query,
expected identities, route variants, or valid-node inventory.

Direct submissions use a runtime-derived outer schema:

```json
{"query": "one bounded read-only Cypher query", "assertion": {}}
```

Decision and absence claims may declare only the specific assertion fields
allowed by that public claim kind. The query executes exactly once through
`DirectQueryCoordinator.execute()`. Correctness comes from the returned graph
evidence plus the restricted assertion, not the query text.

The public request also carries `ori-direct-result-contract-v1`. It preserves
the working v1 BloodHound boundary without restoring the legacy grader:

- set queries return only answer nodes as rows, or one
  `collect(node) AS entities` collection;
- count queries return one non-negative scalar;
- route queries return an actual path variable with `RETURN p`, plus any
  required supporting relationship variables;
- Cypher must not construct the task's JSON answer schema with maps, list
  literals, or list comprehensions;
- `CALL`, `UNION`, `UNWIND`, `CASE`, `labels()`, and `XOR` are outside the
  certified direct result grammar;
- recursive route ordering and `LIMIT` belong after the final `RETURN`.

These are model-neutral API/result constraints, not graph or answer hints. The
policy still evaluates the submitted query after generation and never rewrites
or retries it. Direct set completeness uses one certified, bounded BloodHound
response and exact sealed-set comparison; MCP set completeness still requires
mechanical count/pagination evidence because MCP is a paginated tool surface.

The direct projector accepts both standard `data.nodes` and CE's collected-node
literal representation (`Props`, `Labels`, and `ElementId`). If exactly one
node collection is present, it is the answer population and auxiliary graph
nodes are ignored. A declared total must equal the projected entity count.
Exact route identity is structural—ordered endpoints, direction, and
relationship—while oracle-declared edge properties are required predicates.
Extra CE-generated properties such as `lastseen` do not make an otherwise exact
route incorrect.

MCP loops project each actual tool result into a mechanical `ToolObservation`.
High-level MCP response wrappers and coordinator-backed Cypher responses share
the same projector. Successful activity alone cannot unlock finalization.
Cypher enumeration requires a companion scalar total and a bounded,
stably-ordered page; route witnesses still must pass the shared Evidence IR
comparator. Any MCP-issued Cypher runs through the same policy-v3 coordinator
as direct mode.

The compiler publishes strict nested answer schemas for entities, edges,
entity-property facts, and the generic bounded-negative reason vocabulary.
MCP output, schema-only retry eligibility, deterministic fixtures, and offline
replay all use one shared JSON-Schema-plus-`EvidenceIR` validator. This prevents
one scoring surface from accepting an answer another surface rejects. Malformed
and non-finite values are model-attributable `OUTPUT_INVALID`; diagnostic
fingerprinting is deliberately non-canonical so even invalid JSON numbers
cannot crash the error path. Count aliases are unrestricted only for a
count-only projection whose tool response contains exactly one unambiguous
non-negative scalar literal.

Run state is private and atomic. It binds the model/run identity, source
manifest and archive, task/oracle/catalog/live-certification fingerprints,
graph, capability profile, containment configuration, runtime configuration,
runtime implementation, and every provider attempt. Resume rejects incompatible
provenance. Within compatible provenance, infrastructure and unexecuted samples
are rescheduled, their prior terminal row is replaced, and provider attempt
numbers remain contiguous. Successful and model-attributable samples are not
replayed. Public reports are emitted only after all scheduled tasks are
reconciled exactly once and the post-track graph gate passes.

Only external availability failures—HTTP, transport, authentication, server,
rate-limit, and timeout conditions—are retryable infrastructure. An internal
projector, schema, adapter, or runner exception is `HARNESS_ERROR`; it receives
no reasoning verdict and is not retried as infrastructure. The campaign writes
that terminal task result and its private provider receipt, preserves its
checkpoint, continues to later tasks, and marks the campaign invalid. The MCP finalization
fingerprint hashes the state machine, projector, MCP adapter, and provider-loop
implementations, while model-run provenance binds the complete runtime
implementation fingerprint. Runtime changes therefore require new compiled and
live-certified artifacts plus a fresh campaign output directory.

The direct-query coordinator, circuit state, and deny cache are campaign-scoped,
not model/run-scoped. The cache lives at the V2 output root and is bound to the
source-manifest digest plus policy version, so a quarantined query cannot be
re-executed by a later model, repetition, or MCP-issued Cypher call in the same
campaign.

The live terminal stream is also model-blind. It shows artifact and graph-gate
stages, model/run and public task identifiers, claim/policy type, retry state,
checkpointed outcome, elapsed time, token counts, running correctness, and MCP
tool/Cypher counts. It does not expose the submitted query, raw model response,
oracle, expected identities, or graph evidence. Progress-output failures are
non-fatal and cannot change execution or scoring.

## V2 compile and certification commands

Compile and offline-certify one product track:

```bash
uv run ori compile-v2 \
  --manifest datasets/benchmarks/complex-v1-seed-4401_manifest.json \
  --archive datasets/benchmarks/complex-v1-seed-4401.zip \
  --product complex \
  --track mcp \
  --output-dir results/v2/complex-seed-4401
```

Live-certify both tracks without invoking a model:

```bash
uv run --env-file ../Bloodhound-MCP/.env \
  ori certify-v2-live \
  --manifest datasets/benchmarks/complex-v1-seed-4401_manifest.json \
  --archive datasets/benchmarks/complex-v1-seed-4401.zip \
  --product complex \
  --output-dir results/v2/complex-seed-4401/live
```

This command is read-only. A graph mismatch is a stop condition, not permission
to upload or replace data.

Create a machine-local campaign config, then run readiness without providers:

```bash
cp models.v2.example.yaml models.v2.local.yaml

uv run --env-file ../Bloodhound-MCP/.env \
  ori run-v2 \
  --config models.v2.local.yaml
```

Readiness verifies:

- all source, public, oracle, candidate, and live-certification fingerprints;
- exact candidate/public/oracle/certification task sets;
- candidate state for every scheduled task;
- the pinned clean MCP checkout revision;
- explicit model/loop compatibility;
- local provider credential configuration and, for Codex, exact model slugs in
  the Codex capability cache;
- BloodHound health and the exact archive-derived live graph before and after
  each configured track.

No model call occurs unless `--execute` is supplied:

```bash
uv run --env-file ../Bloodhound-MCP/.env \
  ori run-v2 \
  --config models.v2.local.yaml \
  --execute
```

Treat `--execute` as the paid/external side-effect boundary. Use a new output
directory for a changed config, model set, or repetition count.

Score structured answers offline:

```bash
uv run ori score-answers \
  --protocol v2 \
  --track mcp \
  --manifest results/v2/complex-seed-4401/complex-mcp-seed-4401-public-v2.json \
  --oracles results/v2/complex-seed-4401/complex-mcp-seed-4401-oracles-v2.private.json \
  --answers answers.json \
  --output results/v2/complex-seed-4401/scoring.private.json
```

## V1 offline answer scoring

Use `ori score-answers` to grade structured answers without launching a model campaign:

```bash
ori score-answers \
  --manifest datasets/phase4-v1_manifest.json \
  --answers answers.json \
  --track mcp \
  --output scorer_projection.json
```

For legacy v1, the answers file may be either a list of answer objects or an
object with an `answers` list. Each answer object should include:

- `task_id`
- `final_answer` (or `answer`), using the MCP final JSON contract
- optional `reference_nodes` / `reference_node_names`
- optional `ref_error`

The output projection includes per-task `reference_nodes`, `answer_nodes`, `missing_reference_nodes`, `missing_required_nodes`, `extra_valid_nodes`, `hallucinated_nodes`, `metrics`, `details`, and `ref_error`.

## Preflight consistency checks

Run a lightweight scorer/task consistency check before campaigns:

```bash
ori preflight-tasks --manifest datasets/phase4-v1_manifest.json --track mcp --output preflight.json
```

Pass `--valid-nodes valid_nodes.json` when you have a graph inventory dump; the checker will flag contract nodes missing from that inventory.

- `ORI_MCP_NO_PROGRESS_TIMEOUT_SECONDS` configures the native Ollama MCP activity watchdog (default 300s).

## Failure taxonomy

- `MODEL_ERROR`: the model/provider call failed, including an OpenAI Responses
  `error`, `response.failed`, `response.incomplete`, a stream without a terminal
  completion, or a completed response with neither text nor tool calls.
- `PARSE_FAIL`: the model call completed with non-empty answer text, but ORI
  could not extract the required Cypher or structured answer from it.
- `CYPHER_ERROR`: model/tool query error, including structured BloodHound syntax/query errors.
- `QUERY_TOO_EXPENSIVE`: the model query was rejected by the versioned direct
  safety policy or BloodHound explicitly terminated that admitted query at the
  server timeout.
- `INFRA_ERROR`: external BloodHound, provider, HTTP, transport, authentication,
  rate-limit, server, or timeout availability issue.
- `HARNESS_ERROR`: internal ORI projector, schema, adapter, or runner failure;
  never a model reasoning verdict and never an infrastructure retry.
- `MCP_TURN_TIMEOUT`: native MCP model turn produced no token/tool/log progress before the watchdog expired.
- `NO_PROGRESS_TIMEOUT`: generic activity watchdog timeout.
- `SAMPLE_TIMEOUT`: sample-level timeout placeholder/future subtype.
- `OLLAMA_STREAM_TIMEOUT`: Ollama stream timeout placeholder/future subtype.
- `HALLUCINATION`: final answer named a node that is absent from the valid graph inventory. Real graph nodes outside the reference are reported as `extra_valid_nodes`, not hallucinations.

## Reporting metrics

MCP summaries separate reasoning quality from reliability:

- `completed_samples`
- `correct_completed`
- `reasoning_accuracy = correct_completed / completed_samples`
- `effective_accuracy = correct / total_samples`
- `infra_failure_rate`
- `tool_error_rate`
- `timeout_rate`

## BloodHound Cypher runtime guidance

### Direct Cypher containment

Direct Cypher is checked only after generation, so the benchmark prompt receives
no safety hints. ORI does not rewrite or repair the answer. The
`bloodhound-cysql-direct-v3` policy rejects non-read-only statements,
unselective recursive expansions, raw open-ended wildcard path enumeration,
open-ended `allShortestPaths`, excessive recursive bounds/patterns, oversized
queries/results, recursive relationship-alternation complexity, broad
relationship enumeration, and broad labeled or unlabeled node enumeration.
Recursive selectors must bind an endpoint of the path; a
disconnected selector cannot make a separate expansion safe. For standalone
node sets, inline property maps and equivalent scalar `WHERE` predicates are
both valid filters. Recursive traversals always need an exact endpoint `name`
or `objectid`; aggregate output and `LIMIT` do not make an unanchored expansion
safe. Non-recursive relationship enumeration may instead use aggregate-only
output, including a pure `WITH count(...)` scalar projection, or an explicit
result-stage `LIMIT`. Mixed aggregate projections that retain graph entities do
not qualify. Ordinary property filters are not treated as exact traversal
selectors. A `WITH` clause starts a new containment stage. Exact selectors
propagate only when the selected variable is explicitly projected by name,
alias, or `WITH *`; discarded bindings cannot authorize later enumeration.
`UNION` is rejected because it is outside the documented BloodHound direct-query
subset and would create an independent branch with separate selectivity.
BloodHound responses that explicitly reject a query as too complex or likely to
cause poor or unstable database performance are quarantined and scored as
`QUERY_TOO_EXPENSIVE`, not syntax errors.

The policy applies only to untrusted model output. Trusted benchmark reference
queries are preflighted as task contracts and execute with the same BloodHound
server and client deadlines, but they are not assigned model-attributable policy
penalties.

Admitted model queries are serialized and executed exactly once. ORI sends
BloodHound's official `Prefer: wait=N` header, uses a slightly longer client
deadline, and records whether the query executed. A server query timeout
quarantines its fingerprint for the manifest/policy pair. A transport, server,
rate-limit, client-timeout, or authentication failure triggers a cheap health
check; an unhealthy result opens the campaign circuit and later samples are
recorded as unexecuted `INFRA_ERROR` placeholders.

Fingerprint canonicalization removes comments and formatting and normalizes
known Cypher keyword case. It deliberately preserves identifiers and literals,
including their case. A policy-version, manifest, model, or run-name change
requires a new output directory and new checkpoint/deny-cache artifacts.

Direct CSV, summary, telemetry, and checkpoint artifacts record:

- query execution and attempt count;
- query fingerprint and safety-policy version/rule;
- post-failure BloodHound health and circuit state;
- policy rejection, server timeout, circuit skip, and greedy-query counts.

The current policy accepts all 42 reference queries generated by
`complex-v1-seed-4401`, including the selective Tier 6
`shortestPath(...[*1..12]...)` contracts. The exact Phase 0 raw wildcard
regression is rejected before any BloodHound request.

Use BloodHound's current documentation as the syntax and API authority:

- [Supported Cypher Syntax](https://bloodhound.specterops.io/analyze-data/explore/cypher-supported)
- [Search with Cypher](https://bloodhound.specterops.io/analyze-data/explore/cypher-search)
- [Run a Cypher query API](https://bloodhound.specterops.io/reference/cypher/run-a-cypher-query)

### MCP guidance

The MCP system prompt now reminds models to avoid known BloodHound CE pitfalls:

- never duplicate `RETURN` columns; alias repeated/derived expressions
- prefer scalar columns (`u.name`, `g.name`, `c.name`) over `nodes(p)` / list projections
- avoid unsupported `UNION` in the CE API path
- use `COALESCE(list_prop, [])` for nullable list properties
- revise structured `syntax_error` / `query_error` responses instead of retrying the same query
- keep final `node_names` limited to task-required graph-valid nodes unless the contract allows optional nodes

## Contracts

Phase 4 ADCS/composite tasks can derive answer contracts from manifest `critical_nodes`; explicit per-task `metadata.answer_contract` is also supported with `required_nodes`, `optional_nodes`, `forbidden_nodes`, `required_edges`, and `grade_mode`.
