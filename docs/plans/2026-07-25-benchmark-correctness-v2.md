# ORI Benchmark Correctness and Architecture V2

Status: complete
Goal: implement the v2 correctness architecture without paid model campaigns
Branch: `feat/benchmark-correctness-v2`
Upstream boundary: `0a56029471c5426be348c61c0969724eaee38599`
Protocol: `ori-eval-protocol-v2`

This checklist is the source of truth for the goal-mode implementation. The
direct-query containment policy, deny cache, timeout handling, circuit breaker,
and failure classifier are upstream dependencies and must not be reimplemented.

## Locked boundaries

- [x] Base the branch on `origin/fix/direct-query-containment` at `0a560294`.
- [x] Create `feat/benchmark-correctness-v2` without modifying the containment branch.
- [x] Confirm the branch already contains the Phase 0 freeze as `68f4cde`.
- [x] Do not merge or cherry-pick the equivalent Phase 0 commit `2946c73`.
- [x] Preserve all v1 generation and grading behind explicit legacy dispatch.
- [x] Build v2 primarily under `src/ori/eval/v2/`.
- [x] Migrate the complete current `simple` and `complex` task capabilities.
- [x] Keep Phase 3/4 profiles legacy-only.
- [x] Keep v2 opt-in until the later selector and release phase.
- [x] Do not run GPT-5.6 Sol, GPT-5.5, or any other paid/local model campaign.

Branch reconciliation is a later integration task and is explicitly outside
this benchmark-correctness goal's completion contract.

## Baseline and post-goal integration

- [x] Fetch the latest remote refs.
- [x] Confirm `origin/fix/direct-query-containment` resolves to the pinned commit.
- [x] Run the containment baseline: 393 tests passed.
- [x] Run Ruff across `src`, `scripts`, and `tests`.
- [x] Run `git diff --check`.
- [x] Verify the Phase 0 file hashes:
  - `docs/complex-v1-development-freeze.md`:
    `0c6c0cf547b18340fa6bdba7fcc9be92fae1064f45417029b491ffb949f8d13d`
  - `tests/fixtures/complex_v1_development/regressions.json`:
    `8e4789d2f4b78b1cb4df7d4cc7653b8ab752f7d74223313074f5d117e2234dcd`
  - `tests/test_complex_v1_freeze.py`:
    `25cc12d5f62d018d0ddeb878424aac17d01a537aba8f59db5fc4237392630d9a`
- [x] Keep all new commits after the pinned containment boundary.
- [x] Leave containment-owned modules unchanged unless a narrow adapter test requires
  an extension.
Post-goal procedure: after the containment PR merges, reconcile through exactly
one path:

  - rebase normally if the pinned tip is an ancestor of `origin/master`; or
  - if containment is squash-merged, replay only commits after `0a560294` onto
    `origin/master`.
- [x] Verify the current `0a560294...HEAD` diff contains only intentional v2
  work; repeat this check after the future reconciliation.

## Phase 1: protocol boundary and inventory

- [x] Freeze inventories for simple/direct, simple/MCP, complex/direct, and complex/MCP.
- [x] Record task ID, template, claim kind, track, prompt, legacy grade mode,
  reference source, semantics, bounds, and migration status.
- [x] Add strict v2 schema models and canonical SHA-256 fingerprints.
- [x] Introduce `ori-eval-protocol-v2` and a new generated-manifest revision.
- [x] Add explicit v1/v2 dispatch with fail-closed unknown-version handling.
- [x] Prove v1 artifacts cannot load as v2 and v2 artifacts cannot load as v1.
- [x] Preserve all containment regressions.

## Phase 2: typed claims and sealed oracle

- [x] Implement strict `EntityRef` and ordered `EdgeWitness`.
- [x] Implement discriminated route, set, count, decision, and absence claims.
- [x] Implement explicit answer policies:
  - exact set;
  - exact count;
  - exact route;
  - mechanism-valid route;
  - closed route variants;
  - bounded negative;
  - decision.
- [x] Implement independent direct and MCP `TrackBinding` models.
- [x] Compile solver-visible `TaskBundle` and scorer-only `OracleBundle` from one claim.
- [x] Resolve logical roles to seed-specific typed identities during compilation.
- [x] Validate every prompt role and requested output field against the claim.
- [x] Load oracles through a scorer-side registry keyed by opaque ID and fingerprint.
- [x] Remove oracle material from solver-visible Inspect metadata and logs.
- [x] Reject caller-supplied `reference_results`, `reference_nodes`,
  `valid_node_names`, and `correct` fields in v2 answers.
- [x] Add oracle-sentinel leakage tests across provider requests, logs, transcripts,
  CSVs, telemetry, and public exports.

## Phase 3: identity, Evidence IR, and comparator

- [x] Build one typed identity and alias resolver.
- [x] Support object IDs, SIDs, canonical names, domain-qualified names, and aliases.
- [x] Reject ambiguous aliases.
- [x] Normalize direct, MCP, and offline answers into one `EvidenceIR`.
- [x] Implement exact normalized set equality.
- [x] Implement exact count comparison.
- [x] Implement ordered edge-witness route validation.
- [x] Accept alternate routes only when objective, mechanisms, order, context, and
  exclusions are satisfied.
- [x] Reject wrong relationships, reversed edges, disconnected extras, cycles,
  wrong endpoints, decoys, invalidated edges, and missing supporting evidence.
- [x] Implement bounded-negative proof.
- [x] Keep precision, recall, overlap, missing values, and extras diagnostic-only.
- [x] Add independently authored golden outcomes.
- [x] Prove direct, MCP, and offline projections produce the same normalized verdict.
- [x] Structurally prevent comparator access to `template_id` or arbitrary task metadata.

## Phase 4: representative vertical slice

- [x] Migrate `t1_group_membership-01`.
- [x] Migrate `t1_has_session-01`.
- [x] Migrate `t2_nested_groups-01`.
- [x] Migrate `global-da-members`.
- [x] Migrate `mcp-global-da-direct-members`.
- [x] Migrate `mcp-global-da-direct-member-count`.
- [x] Migrate `mcp-user-privileged-group-memberships`.
- [x] Migrate `t4_adcs_esc1-01`.
- [x] Migrate `t6_adcs_identity_transition_tier0-01`.
- [x] Migrate `t6_path_selection_decoy_routes-01`.
- [x] Migrate `t6_stale_session_contingency-01`.
- [x] Migrate `t6_negative_control_invalid_cert-01`.
- [x] For every slice task, pass perfect, wrong, empty, alias, extra-entity, decoy,
  and alternate-route fixtures.
- [x] Pass reversed-edge, disconnected-path, cycle, malformed-answer, and
  source/target mismatch fixtures where relevant.
- [x] Record compiler, oracle, comparator, and task-bundle fingerprints.
- [x] Complete a read-only code-review gate.

## Phase 5: direct execution adapter

- [x] Execute every model-produced direct query only through
  `DirectQueryCoordinator.execute()`.
- [x] Preserve the authoritative containment provenance in
  `DirectExecutionReceipt`.
- [x] Normalize successful `CypherResult.raw` responses into ordered `EvidenceIR`.
- [x] Reject path results without an ordered edge witness.
- [x] Do not inspect query text to establish correctness.
- [x] Do not call legacy `grade()` or `grade_mcp_diagnostic()` from v2.
- [x] Map policy rejection, query timeout, and query error as model-attributable.
- [x] Map auth, transport, server, rate-limit, response, and circuit-open failures
  as infrastructure with no reasoning verdict.
- [x] Require direct-query policy v3 for certified runs.
- [x] Prove v2 does not alter policy-v3 admission, cache, timeout, or circuit behavior.

## Phase 6: MCP capabilities and finalization

- [x] Add a versioned BloodHound CE/MCP capability profile.
- [x] Declare direct, transitive, and effective semantics for every exposed tool.
- [x] Record pagination, ordering, truncation, proof strength, and output limits.
- [x] Classify each MCP binding as tool-only, explicitly Cypher-enabled, or blocked.
- [x] Replace `successful_tool_results` with typed evidence events.
- [x] Ensure irrelevant tools and resource reads do not unlock finalization.
- [x] Use one evidence-state machine for native Ollama, native OpenAI-compatible,
  and Inspect loops.
- [x] Forbid `mcp_tool_loop: auto` in certified runs.
- [x] Permit only one generic schema-only finalization retry.
- [x] Report unresolved malformed output as `OUTPUT_INVALID`.
- [x] Pass a common loop conformance matrix.

## Phase 7: bounds, graph fingerprints, and certification

- [x] Certify traversal, cardinality, pagination, output bytes, transcript bytes,
  tool calls, and time budgets.
- [x] Rewrite unsafe capabilities into bounded equivalents.
- [x] Block results whose completeness cannot be distinguished from truncation.
- [x] Generate an archive-derived graph digest for benchmark-owned objects,
  scorer-relevant properties, and canonical relationships.
- [x] Compute the same scoped digest from the live graph with bounded pagination.
- [x] Verify the graph before and after every executable track; no-model
  readiness uses one exact shared graph receipt because it cannot execute work
  between tracks.
- [x] Invalidate certification and runs when the graph changes.
- [x] Bind certification to task, prompt, oracle, graph, compiler, comparator,
  capability profile, and execution bounds.
- [x] Prove any semantic byte change invalidates certification.

## Phase 8: complete corpus migration

- [x] Migrate every inventoried simple and complex capability.
- [x] Preserve separate direct and MCP bindings.
- [x] Do not derive MCP tasks by copying direct tasks and appending exceptions.
- [x] Add stable candidate IDs, revisions, family, tier, claim kind, semantics,
  cost band, concentration key, and selector-facing metadata.
- [x] Replace or block every uncertifiable legacy capability.
- [x] Prohibit silent deprecation and legacy-scoring fallback.
- [x] Produce a complete per-task fixture manifest.
- [x] Produce per-track candidate-catalog digests.
- [x] Finish with zero blocked candidates.

## Phase 9: shared runtime and exact accounting

- [x] Dispatch v2 through direct, MCP, Inspect, and offline scoring surfaces.
- [x] Keep legacy scoring callable only through v1 dispatch.
- [x] Bind checkpoints to all v2 compatibility fingerprints.
- [x] Reject mixed-protocol checkpoints and output directories.
- [x] Derive denominators from the certified catalog.
- [x] Require each scheduled task exactly once after retry reconciliation.
- [x] Reject missing, duplicate, unknown, and fingerprint-mismatched results.
- [x] Separate execution status, reasoning verdict, and campaign validity.
- [x] Give unresolved infrastructure no reasoning verdict.
- [x] Keep direct and MCP summaries separate.
- [x] Add an explicit `ori run-v2` model-campaign entry point while keeping
  normal `ori run` on v1.
- [x] Make `ori run-v2` a no-model readiness gate unless the operator supplies
  `--execute`.
- [x] Derive every V2 model schedule from the candidate-certified catalog.
- [x] Send solver requests only through the common redacted public envelope,
  including public semantics and execution bounds.
- [x] Validate typed direct `{query, assertion}` submissions before any
  BloodHound execution.
- [x] Route direct and MCP-issued Cypher through the authoritative policy-v3
  coordinator.
- [x] Project native MCP tool results into typed evidence events and perform at
  most one real schema-only retry after useful evidence.
- [x] Bind atomic private resume state to run, artifacts, graph, capability,
  containment, runtime, provider attempts, and exact task accounting.
- [x] Withhold redacted public reports until the post-track graph gate passes.

## Phase 10: controlled validation

- [x] Generate simple seeds 1234 and 5678.
- [x] Generate complex seeds 4401 and 4402.
- [x] Prove same-seed fingerprints are deterministic.
- [x] Prove changed seeds resolve identities without prompt/contract drift.
- [x] Verify the controlled BloodHound target and current ingest before live checks.
- [x] Request confirmation before uploading or replacing graph data.
- [x] Live-certify the vertical slice with deterministic fixtures.
- [x] Live-certify the simple catalog after operator-approved graph replacement.
- [x] Live-certify the complete simple and complex catalogs under their original
  implementation fingerprints.
- [x] Verify pre/post graph fingerprints.
- [x] Re-live-certify the complete simple catalog under adapter-certifier v3.
- [x] Re-live-certify the complete complex catalog under adapter-certifier v3
  after the exact complex graph is loaded with operator approval.
- [x] Confirm current adapter-certifier-v3 complex live/offline Evidence IR and
  verdict parity.
- [x] Run no paid or local model campaign.

## Phase 11: documentation and handoff

- [x] Update the README with the opt-in v2 development workflow.
- [x] Update the hardening runbook.
- [x] Document task authoring and certification.
- [x] Publish a layered V2 design rationale covering the plain-language mental
  model, complete incident/decision history, implementation tradeoffs, scoring
  mathematics, worked campaign examples, presentation talk track, glossary,
  and current limitations.
- [x] Document model-blind execution constraints.
- [x] Publish the candidate-catalog contract for the later selector.
- [x] Preserve v1 reproduction instructions.
- [x] Run full pytest, Ruff, diff check, and secret scan.
- [x] Complete final independent review.
- [x] Complete focused oracle-leakage and scoring-parity reviews.
- [x] Add AgentVault project, decision, and session continuity.
- [x] Route the matching Personal Vault write through librarian and require
  `LIBRARIAN WRITE COMPLETE`.
- [x] Record branch reconciliation as a post-goal integration task rather than
  a benchmark-correctness completion gate.

This deferred procedure is a PR-preparation checkpoint, not a v2 implementation,
certification, or goal-completion gate. Reconcile after the containment PR is
merged and before opening the v2 PR.

## Post-implementation MCP runtime incident

An operator-authorized model campaign after the original no-paid implementation
gate exposed a V2 MCP adapter regression. Successful BloodHound MCP responses
arrived as structured text-content blocks, were stringified as Python
representations, failed JSON projection, and were then mislabeled as
infrastructure failures.

- [x] Preserve the failed campaign under its original output directory.
- [x] Reproduce the real in-process `domain_info`, `graph_analysis`, and
  `cypher_query` response boundaries against the controlled BloodHound target.
- [x] Unwrap MCP text-content blocks before model delivery and V2 projection.
- [x] Extract explicit cardinality for the pinned keyed graph-search response.
- [x] Downgrade unknown response shapes to inconclusive evidence instead of
  constructing an incoherent `ToolObservation`.
- [x] Add a distinct MCP harness-failure event and terminal phase.
- [x] Map unknown internal runtime exceptions to `HARNESS_ERROR`; retain
  timeout and HTTP failures as `INFRA_ERROR`.
- [x] Resume compatible checkpoints by reopening infrastructure and unexecuted
  samples while preserving terminal model/success results.
- [x] Continue provider attempt numbers monotonically across resume.
- [x] Bind MCP certification to the state machine, projector, adapter, and
  native provider-loop source fingerprints.
- [x] Bind run provenance to the complete runtime implementation fingerprint
  and bump the campaign runner to `ori-v2-model-campaign-v2`.
- [x] Add regressions for real content blocks, keyed graph search, unknown
  shapes, harness classification, graph-progress receipts, and resume
  reconciliation.
- [x] Recompile and live-certify the corrected simple seed-1234 catalog in a
  fresh artifact namespace without model calls.
- [x] Pass no-model readiness for both models and both tracks against the
  unchanged live graph.
- [x] Complete independent code review and final repository validation.

## Post-implementation direct runtime incident

The first operator-authorized V2 direct campaign exposed a boundary regression
that deterministic fixture certification did not cover. V2 had dropped the
proven v1 CySQL instructions, encouraged models to mirror the answer JSON inside
Cypher, ignored CE collected-node literals, treated auxiliary returned nodes as
set answers, and required exact equality with unrequested CE edge properties.

- [x] Preserve the interrupted seven-sample checkpoint in its original output
  directory and stop further provider calls.
- [x] Classify the observed failures as projection, CySQL-contract, policy-shape,
  or comparator defects rather than infrastructure.
- [x] Reuse the v1 `RETURN p`/node-row CySQL contract without restoring legacy
  grading, reference-result comparison, or template branches.
- [x] Add the public, model-neutral `ori-direct-result-contract-v1`.
- [x] Bind that result contract into the direct capability profile.
- [x] Parse real CE collected-node literals and keep auxiliary nodes out of
  exact-set evidence.
- [x] Validate an optional declared total against the returned entity count.
- [x] Treat CE edge properties as a superset while enforcing every
  oracle-required property.
- [x] Add regressions for v1 node rows, collected entities, auxiliary nodes,
  inconsistent totals, direct prompt grammar, and CE edge-property supersets.
- [x] Prove the old certification is rejected before model execution.
- [x] Recompile and live-certify both simple tracks in a fresh artifact
  namespace without model calls.
- [x] Pass live direct conformance for the 2-account kerberoastable set, the
  58-computer AdminTo set, and the one-edge exact membership route.
- [x] Pass final full tests, lint, diff check, secret scan, and independent
  review.
- [x] Pass fresh no-model readiness and prepare the exact operator rerun command.
- [x] Record the incident and resolution in AgentVault and Personal Vault.

### Live campaign property-predicate crash

The corrected operator campaign then exposed an unhandled direct-projection
defect on GPT-5.5 task `simple.direct.t2_kerberoast_chain-01@2`. A successful
route required the source entity's `hasspn` property, but the adapter accessed
`PropertyPredicate.key` instead of the schema field `property_name`. The
resulting `AttributeError` terminated the campaign rather than producing typed
failure accounting.

- [x] Preserve the complete 20-task GPT-5.6 direct run and the first 12
  checkpointed GPT-5.5 direct results under the original runtime fingerprint.
- [x] Read required predicate names from `PropertyPredicate.property_name`.
- [x] Add a regression that projects a property-constrained route and proves
  the expected entity property fact reaches `EvidenceIR`.
- [x] Contain unexpected direct projector/comparator exceptions as
  `HARNESS_ERROR` with no reasoning verdict.
- [x] Prove live projection of the two-edge `MemberOf → AdminTo` route retains
  `hasspn=true` and produces `ROUTE_VALID`.
- [x] Pass 558 repository tests, Ruff, diff validation, and independent review.
- [x] Preserve fail-closed runtime provenance; do not adopt the old checkpoint
  implicitly under the changed implementation fingerprint.
- [x] Move the operator config to the fresh
  `campaign-comparator-v3-property-predicate-fix` output namespace.
- [x] Pass two-model/two-track no-model readiness against the unchanged graph.

### Pre-run analogous boundary audit

Before another operator-authorized campaign, the schema and runtime boundaries
were audited for defects analogous to the property-predicate crash.

- [x] Make property-bearing route schemas require the property evidence used by
  the comparator.
- [x] Define strict nested entity, edge-property, entity-property, and
  bounded-negative reason schemas.
- [x] Give the negative certificate task distinct `certificate_template` and
  `objective` roles, and reject duplicate public logical roles.
- [x] Use one JSON-Schema-plus-`EvidenceIR` validation boundary for MCP
  finalization, retry eligibility, fixtures, and offline scoring.
- [x] Make perfect and empty fixtures schema-valid and gradeable so live parity
  cannot silently mark malformed fixtures inapplicable.
- [x] Reject non-finite protocol numbers and safely digest malformed output
  without re-crashing the error handler.
- [x] Accept any count alias only for a count-only projection returning exactly
  one unambiguous non-negative scalar literal.
- [x] Project direct entity property names case-insensitively while preserving
  the claim's canonical property key.
- [x] Contain coordinator/receipt and all comparator exceptions as
  `HARNESS_ERROR`, preserve the authoritative query fingerprint, and represent
  unknown execution/attempt provenance as null.
- [x] Expand runtime and MCP-finalization fingerprints over every shared schema,
  identity, evidence, adapter, state-machine, and accounting source.
- [x] Recompile both simple tracks and both complete complex tracks in the fresh
  `schema-runtime-hardening-v2` namespace without model calls.
- [x] Live-certify simple seed 1234 against the unchanged controlled graph.
- [x] Verify the live MCP `member_count` literal shape and the direct
  `MemberOf → AdminTo` route with `hasspn=true`.
- [x] Pass fresh two-model/two-track no-model readiness from a new campaign
  output directory.
- [x] Pass final repository tests, Ruff, diff validation, secret scan, and
  independent review.

### Adapter-level parity and campaign-containment audit

A second pre-run audit found that the prior live fixture proof checked graph
membership but then compared an offline `EvidenceIR` object with itself. It did
not cross the direct projector, MCP transcript projector, or shared MCP
finalizer. The audit also identified a campaign-level exception gap: a future
unexpected task-runtime defect could still terminate the process before a
durable checkpoint was written.

- [x] Preserve each applicable fixture's exact structured answer payload in the
  private certification artifact.
- [x] Add canonical direct graph-response replay through
  `project_direct_evidence()`.
- [x] Add canonical MCP tool-response replay through
  `MCPTranscriptProjector` and `score_mcp_transcript_v2()`.
- [x] Distinguish graph-backed, adversarial, and malformed replay evidence.
- [x] Require malformed applicable fixtures to produce typed
  `MODEL_FAILURE/OUTPUT_INVALID` parity instead of an inapplicable marker.
- [x] Bind certification to a source-derived certifier fingerprint covering the
  schema, compiler, comparator, graph, identity, evidence, fixtures,
  certification, direct/MCP adapters, and model runtime.
- [x] Reject stale offline, live, candidate, and campaign certification after a
  certifier change.
- [x] Add an explicit direct boundary between unresolved
  `PropertyPredicate.property_name` constraints and resolved
  `EntityPropertyFact.key` facts.
- [x] Add a regression that simulates a stale predicate object at the resolved
  fact boundary and fails with `DirectAdapterError`, not `AttributeError`.
- [x] Scope direct property projection to the resolved `(entity_id, key)` pair
  so an unrelated returned node cannot contaminate route, decision, or negative
  proof evidence.
- [x] Reject nodes outside the sealed decision/absence context and cover the
  decision boundary explicitly.
- [x] Replay every simple fixture across all 20 direct and 40 MCP candidates.
- [x] Replay every complex fixture across all 46 direct and 70 MCP candidates.
- [x] Add a per-task campaign containment boundary that records unexpected
  runtime defects as durable `HARNESS_ERROR`, continues/checkpoints, and makes
  the campaign invalid.
- [x] Compile both tracks for simple seed 1234 and complex seed 4401 into the
  fresh `adapter-parity-v3-entity-scoped` artifact namespace without model
  calls.
- [x] Live-certify both simple seed-1234 tracks against the unchanged controlled
  graph under certifier v3.
- [x] Pass a fresh two-model/two-track no-model readiness gate from
  `models-v2-adapter-parity-v3.yaml`.
- [x] Live-certify both complex seed-4401 tracks under certifier v3 after an
  operator-approved graph replacement.
- [x] Pass a fresh two-model/two-track no-model readiness gate for the 46 direct
  and 70 MCP complex candidates without provider calls.
- [x] Run final full repository tests (581 passed), Ruff, diff validation,
  source-control secret scan, and independent review for the
  adapter-certifier-v3 change.

### MCP pagination, finalization, and accounting incident

The first complex V2 MCP campaign exposed a second runtime-contract failure.
BloodHound and the pinned MCP server remained healthy, but five public tasks
requested deterministic 500-row windows while their hidden bindings and
projector required 100-row subpages. Whole-loop 120-second budget exhaustion
was mislabeled as infrastructure, partial usage was discarded, and valid final
answers without certified tool evidence were mislabeled as malformed output.

- [x] Preserve the incomplete campaign as incident evidence and do not resume it.
- [x] Derive the execution page size and starting offset from the typed selection
  window, and reject compiler output whose binding contradicts that claim.
- [x] Make bounded deterministic windows one explicit page with no hidden global
  total-count requirement.
- [x] Publish a model-neutral MCP evidence result contract derived from public
  bounds, including the exact count/page mechanics and property projection rule.
- [x] Certify pinned list operations as complete single-page evidence only when
  their response mechanically reports count, limit, skip, and all returned items.
- [x] Accept the live `computer_info.sessions` response as complete exact-set
  evidence when its reported population fits in one page.
- [x] Pass `include_properties=false` through the authoritative direct coordinator
  without changing policy-v3 admission, timeout, deny-cache, or circuit defaults.
- [x] Prove a live 500-node page is policy-admitted and renders below the certified
  524,288-byte output bound with properties omitted.
- [x] Separate valid-but-unproven public `PROOF_INSUFFICIENT` from malformed
  `OUTPUT_INVALID`, and keep its reasoning verdict null.
- [x] Classify whole-task budget exhaustion as model-attributable `TASK_TIMEOUT`,
  not infrastructure, so it is not retried after a BloodHound-only health check.
- [x] Preserve native `MCP_TURN_TIMEOUT` and `NO_PROGRESS_TIMEOUT` watchdog
  subtypes as retryable provider infrastructure instead of collapsing them into
  the whole-task execution budget.
- [x] Route harness-owned live graph certification reads through one health-gated
  infrastructure retry without retrying model-authored queries.
- [x] Preserve partial cumulative tokens, transcript messages, tool arguments,
  raw tool results, derived observations, evidence events, and policy receipts in
  the private run state after cancellation or failure.
- [x] Prevent schema-only finalization retries after task, infrastructure, or
  harness terminal failures.
- [x] Increase compiled MCP task budgets deterministically from the tool-call
  bound while retaining a finite 600-second ceiling.
- [x] Bump the MCP capability, evidence-state, campaign-runner, private-state,
  and model-report contract versions; source fingerprints invalidate all prior
  compilation, certification, readiness, and resume artifacts.
- [x] Add focused regressions for page offsets, high-level exact-set completeness,
  proof-insufficient accounting, task-timeout accounting, cancellation-safe
  audit receipts, result-contract prompts, and property-projection pass-through.
- [x] Replay every simple and complex fixture through the revised adapters.
- [x] Pass full pytest (591 passed), Ruff, diff validation, source-control secret
  scan, and independent security/correctness review.
- [x] Recompile and live-certify both complex tracks in the fresh
  `mcp-runtime-contract-v7` artifact namespace.
- [x] Pass fresh no-model two-model/two-track readiness from the new V7 campaign output
  directory without launching provider calls.
- [x] Update the README, hardening runbook, task-authoring guidance, and durable
  vault continuity.

Validation evidence:

- V5 and V6 are stale intermediate artifacts. V6 live certification stopped on
  a transient harness-owned BloodHound relationship-read timeout; V7 includes
  the health-gated retry and is the only runnable release.
- V7 graph fingerprint:
  `78ae0686047a372c273d2045abfac0f76fce03b3bb4029cb178727dd3ade65a5`.
- Direct candidate release:
  `2939c6e0f9c352e8581087f10a2ff1532a6156e9a54c6544fd970135a4a1d1f9`
  (46 tasks).
- MCP candidate release:
  `415e1f7b3b8440e97d32bfb08f54bc4b9a71d47f1898e1977a0650c20b5eb6ff`
  (70 tasks).
- No-model readiness receipt:
  `results/v2/complex-seed-4401/mcp-runtime-contract-v7/campaign/v2-run-readiness.private.json`.
- Executable local config:
  `results/v2/complex-seed-4401/models-v2-mcp-runtime-contract-v7.yaml`.

### Pre-run MCP failure-hardening audit (V8)

A proactive audit of the failed complex MCP campaign found additional
cross-layer risks that could otherwise distort or terminate the next paid run.
V7 is now stale by design and must never be resumed.

- [x] Give provider reads and MCP tool calls explicit sub-deadlines below every
  whole-task deadline, and reject a configuration whose hidden caps contradict
  certified task bounds.
- [x] Distinguish provider, MCP-tool, BloodHound, operator interruption, and
  harness failure scopes in private receipts.
- [x] Preserve partial streaming state and typed tool receipts before re-raising
  provider/tool infrastructure failures.
- [x] Treat only the outer certified deadline as model-attributable
  `TASK_TIMEOUT`; an untyped inner timeout is a harness defect.
- [x] Preserve interrupted direct-provider, direct-query, MCP-loop, and
  schema-retry attempts for atomic campaign checkpointing.
- [x] Enforce one lifetime per-task infrastructure retry budget across resumes
  and persist every attempt before deciding whether to retry.
- [x] Retry provider and MCP-tool availability without probing or changing the
  BloodHound circuit; health-gate only BloodHound-scoped failures.
- [x] Avoid provider calls while the direct circuit remains open.
- [x] Quarantine only explicit query timeout/complexity failures; generic
  transport or server failures cannot poison the shared deny cache.
- [x] Recognize BloodHound count queries ending in `LIMIT 1` and the real
  `collect(node) AS entities` literal response without auxiliary-node
  contamination.
- [x] Require exact reported skip/limit mechanics before a high-level window can
  be considered complete.
- [x] Revoke prior finalization readiness after any later truncation in both the
  reducer and native provider loops.
- [x] Map policy rejections, model CySQL errors, and invalid tool arguments to
  model-attributable outcomes instead of public proof failure.
- [x] Forbid loop exhaustion, runtime failure, or missing output from receiving
  a fresh schema retry.
- [x] Keep the one schema-only retry inside the original task deadline, size it
  from the public output bound, and forbid it from introducing absent answer
  facts.
- [x] Require the final output to be exactly one JSON object; fenced JSON and
  surrounding commentary are model-attributable `OUTPUT_INVALID`.
- [x] Replace activity-based MCP finalization with one strict public evidence
  contract: claim-bound `cypher_query.run` returning entities, a scalar count,
  or a bounded path.
- [x] Require only solver-visible input selectors, projection type, and result
  shape for claim relevance; sealed mechanisms, properties, expected facts,
  and comment-only markers cannot influence finalization.
- [x] Make certification replay use the same claim-bound Cypher proof surface as
  live execution.
- [x] Enforce that every MCP evidence selector role exists in the public task.
- [x] Bump the compiler, certifier, MCP result/finalization, campaign runner,
  readiness, and private run-state boundaries so every older artifact is stale.
- [x] Add focused regressions and pass the current 110-test MCP
  runtime/finalization/schema set.
- [x] Replay the complete simple and complex fixture catalogs after the final
  public evidence-contract change.
- [x] Pass the complete repository test suite, Ruff, diff validation, secret
  scan, and independent review.
- [x] Recompile and live-certify both complex tracks in a fresh
  `mcp-runtime-contract-v8` namespace without provider calls.
- [x] Pass a fresh two-model/two-track no-model readiness gate from a new V8
  output directory.
- [x] Update AgentVault and the Personal Vault through librarian with the V8
  outcome and exact safe-run command.

V8 stop condition: do not run `--execute` until all unchecked items above are
complete and the fresh no-model readiness receipt matches the current graph,
runtime, candidate catalogs, and MCP server revision.

V8 validation evidence:

- Full repository suite: 615 passed in 526.85 seconds.
- Ruff and `git diff --check`: clean.
- TruffleHog source scan: 692 chunks, 7,217,130 bytes, zero verified
  secrets/findings.
- Independent review: no actionable findings after verifying that fixture
  entity slices are relative to the already selected oracle window while query
  offsets remain absolute.
- BloodHound health: PASS.
- Complex ingest: 30/30 planted paths, `INGEST CHECK: PASS`.
- Graph fingerprint before/middle/after:
  `78ae0686047a372c273d2045abfac0f76fce03b3bb4029cb178727dd3ade65a5`.
- Certifier fingerprint:
  `9ac31610d570b61116db48b06ad29247e943cb88d0780597cb37b2b67360ea31`.
- Direct: 46 candidates, release
  `3ae280bc1e041fda950983262df49b69fb6bbfec025640f8449d8ac9cb1af900`.
- MCP: 70 candidates, release
  `4438b2b1bf4f1b5da791138dccc9fc843f200c8accccdc8bce5bd3c7ed482a67`.
- Readiness schema/runner:
  `ori-v2-run-readiness-v2` / `ori-v2-model-campaign-v4`.
- Readiness fingerprint:
  `19e80a47cf1cb792326e83745eee3855015a2888487ddd9a0618e2724df21bd3`.
- Pinned MCP revision:
  `009c88f41fae302becad4b00777a3749a0f6f0fa`.
- No-model readiness explicitly reported: `No model calls were launched`.
- Runnable config:
  `results/v2/complex-seed-4401/models-v2-mcp-runtime-contract-v8.yaml`.
- Readiness receipt:
  `results/v2/complex-seed-4401/mcp-runtime-contract-v8/campaign/v2-run-readiness.private.json`.

### Post-run MCP literal-proof correction (V9)

The V8 direct track was valid, but the stopped V8 MCP track exposed a systematic
proof-accounting defect. V8 MCP artifacts and checkpoints are invalid and must
never be resumed. Thirteen schema-valid, exact-correct final answers were
reported as `PROOF_INSUFFICIENT`; the first page passed only because a non-empty
literal result was incorrectly treated as an empty set.

- [x] Capture real BloodHound flattened `data.literals` receipts as regression
  fixtures.
- [x] Derive entity-row cardinality from consistent repeated identity columns
  without double-counting optional `name` columns.
- [x] Treat unknown non-empty literal shapes as inconclusive instead of
  inheriting zero graph-node counts.
- [x] Accept direct `objectid`, `RETURN`-bound aliases, and safe `WITH`
  passthrough aliases for stable page ordering.
- [x] Reject unbound, non-identity, and identity-then-property-rebound ordering
  aliases.
- [x] Recognize exact case-normalized public selectors such as
  `TOUPPER(c.name) = ...`.
- [x] Treat `Principal` as an abstract projection and verify the returned
  identity/count variable rather than requiring `:Principal`.
- [x] Make certification replay use the real flattened literal-row shape
  instead of fabricated graph nodes.
- [x] Increase complete-set pages from 100 to 500 rows and compile
  cardinality-derived serialization time into the public task deadline.
- [x] Bump compiler, certifier, MCP capability/result/finalization, campaign
  runner, readiness, and private run-state boundaries.
- [x] Prove V8 provenance and `run-state-v3.private.json` cannot load in V9.
- [x] Pass focused captured-receipt, MCP runtime, compiler, and campaign schema
  regressions.
- [x] Pass the complete repository suite, Ruff, diff validation, secret scan,
  and independent review.
- [x] Recompile and live-certify both complex tracks in a fresh
  `mcp-runtime-contract-v9` namespace without provider calls.
- [x] Pass a fresh two-model/two-track no-model readiness gate.

V9 validation evidence:

- Captured-receipt and runtime regressions: 79 passed after the review repair.
- Complete final repository suite: 631 passed in 327.66 seconds.
- Ruff and `git diff --check`: clean.
- TruffleHog source scan: 707 chunks, 7,399,437 bytes, zero verified or
  unverified secrets.
- Independent review: one medium `WITH` alias gap found, fixed, and re-reviewed
  with no remaining actionable finding.
- BloodHound health: PASS.
- Complex ingest: 30/30 planted paths, `INGEST CHECK: PASS`.
- Graph fingerprint before/middle/after and readiness:
  `78ae0686047a372c273d2045abfac0f76fce03b3bb4029cb178727dd3ade65a5`.
- Certifier fingerprint:
  `edb647993949118d082dc56ecf9643a795e6d67a759426ebc964384177a5e342`.
- MCP capability profile:
  `ori-mcp-009c88f-bhce-9.1-cypher-v3`.
- Direct: 46 candidates, release
  `8fef047a0788f86a0195968875a3e3178e162ec74d330a5970f7a31adec76606`.
- MCP: 70 candidates, release
  `63d05fe45e2cc3979ff786f96083eb8689cce59e1f28d8fa7d75c3c6a070a96b`.
- Readiness schema/runner:
  `ori-v2-run-readiness-v3` / `ori-v2-model-campaign-v5`.
- Readiness fingerprint:
  `dc38a3a7ad9ebd419dd461b0a2510c0e4f2a6d68b849193f0dc5271d0b22a122`.
- No-model readiness explicitly reported: `No model calls were launched`.
- Runnable config:
  `results/v2/complex-seed-4401/models-v2-mcp-runtime-contract-v9.yaml`.
- Readiness receipt:
  `results/v2/complex-seed-4401/mcp-runtime-contract-v9/campaign/v2-run-readiness.private.json`.

V9 is retained as historical campaign evidence. Its completed MCP results
exposed the V10 defects below, so neither its artifacts nor checkpoints are a
valid resume point.

### Post-campaign MCP proof-binding correction (V10)

The completed V9 campaign proved that the literal-page repair worked, but six
route/decision receipts with positive graph witnesses and auxiliary endpoint
scalars were still reported as `PROOF_INSUFFICIENT`. Replaying their stored
schema-valid answers through the corrected comparator recovers two correct
answers and preserves four incorrect answers. The audit also found
over-permissive claim-relevance cases that could let unrelated selectors,
labels, ordering, or companion counts unlock finalization.

- [x] Prefer positive graph cardinality for route/decision receipts even when
  the same response contains auxiliary scalar literals.
- [x] Keep a response with zero/unknown graph cardinality and unknown non-empty
  literals inconclusive.
- [x] Parse exact public `name`/`objectid` selectors and reject lookalike
  properties, altered normalized values, and concatenated suffixes.
- [x] Use the same selector matcher to identify bound input variables and reject
  answers that return only a required input selector.
- [x] Bind identity/count projections to their actual variables and labels;
  reject explicitly wrong labels and disconnected decoy labels.
- [x] Track `objectid` origin through `WITH` and `RETURN`; reject unrelated
  ordering variables and aliases rebound to another property.
- [x] Bind companion counts and pages to the same normalized population.
- [x] Make certification use `count(DISTINCT result)` when the page uses
  `WITH DISTINCT result`.
- [x] Replay mixed graph-plus-scalar route receipts in certification.
- [x] Bump compiler, certifier, MCP capability/result/finalization, and campaign
  runner boundaries so V9 artifacts and checkpoints cannot resume as V10.
- [x] Pass the complete repository suite, Ruff, diff validation, and source
  secret scan.
- [x] Recompile and live-certify both complex tracks in a fresh
  `mcp-runtime-contract-v10` namespace without provider calls.
- [x] Pass a fresh two-model/two-track no-model readiness gate.
- [x] Complete independent re-review of the distinct-count repair.

V10 validation evidence:

- Captured-receipt and runtime regressions: 93 passed.
- Complete final repository suite: 656 passed in 319.55 seconds.
- Ruff and `git diff --check`: clean.
- TruffleHog source scan: 786 chunks, 8,178,993 bytes, zero verified or
  unverified secrets.
- Independent review: one medium distinct-count replay gap found and fixed;
  focused re-review found no remaining actionable issue.
- BloodHound MCP health query: PASS for
  `GRANITEMANUFACTURING.LOCAL`.
- Graph fingerprint before/middle/after and readiness:
  `78ae0686047a372c273d2045abfac0f76fce03b3bb4029cb178727dd3ade65a5`.
- Compiler/comparator fingerprints:
  `6fd4c0caa1347d504d171cb3dace8201dfbf5cc64142eac2d62664576634ec8a` /
  `79aefda1e849cade32d2a77f108af715f563cc02cbab39b6af6f79e1c1b9437b`.
- Certifier fingerprint:
  `83bf2d4198fdb9254fccdbf7b3d441306ebf27e789d70e646960cf6bc09fd8f6`.
- MCP capability profile:
  `ori-mcp-009c88f-bhce-9.1-cypher-v4`.
- Direct: 46 candidates, release
  `b346e41794da3d5f3642396a2f44890003db76e4e4eb4f5d2dce7b511c8cda32`.
- MCP: 70 candidates, release
  `359b5567c605290574fdbae452637333154bfe0d3143c915f09c3cda6490637a`.
- Readiness schema/runner:
  `ori-v2-run-readiness-v3` / `ori-v2-model-campaign-v6`.
- Readiness fingerprint:
  `4a895c572bf36dfe5e6835aa6d9a683b346375166a66e2d102608c128a42f02c`.
- No-model readiness explicitly reported: `No model calls were launched`.
- Runnable config:
  `results/v2/complex-seed-4401/models-v2-mcp-runtime-contract-v10.yaml`.
- Readiness receipt:
  `results/v2/complex-seed-4401/mcp-runtime-contract-v10/campaign/v2-run-readiness.private.json`.

The operator-authorized V10 campaign used this historical command:

```bash
uv run --env-file /Users/turbo/Projects/Bloodhound-MCP/.env \
  ori run-v2 \
  --config results/v2/complex-seed-4401/models-v2-mcp-runtime-contract-v10.yaml \
  --execute
```

Its stored results exposed a further correctness defect set. V10 artifacts and
checkpoints are retained as incident evidence but are not valid certification
or resume inputs after the V11 boundary below.

### Post-campaign evaluation-contract correction (V11)

The V10 score is not comparable to v1 as a pure model-quality change. V1 used
permissive route node coverage, recall-only sets, and count tolerances, while
V10 correctly tightened those policies but also introduced hidden or
contradictory requirements and incomplete BloodHound identity coverage. V11
keeps strict correctness and removes the harness-created false negatives.

- [x] Freeze the V10 direct/MCP outcome and prompt-contract defect inventory.
- [x] Add one strict solver-visible `AcceptanceSpec` compiled from the typed
  claim, answer policy, and execution binding.
- [x] Reject compilation when sealed scorer requirements have no public origin.
- [x] Distinguish direct User membership from direct Principal membership so
  identical public contracts cannot bind contradictory oracles.
- [x] Use exact routes only for a single public source-to-target edge; publish
  mechanism requirements for longer mechanism-valid routes.
- [x] Allow truthful additional supporting edges and properties unless the
  public policy explicitly closes them.
- [x] Keep exact entity sets and exact scalar counts strict.
- [x] Require evidence closure for decisions without rejecting graph-connected
  truthful entities.
- [x] Redesign bounded-negative tasks as one public, mechanically provable
  count-zero route claim with compatible answer fields.
- [x] Bind negative counts to one public source-to-objective path variable;
  accept broader searches only as zero proofs and grade an exact-scope non-zero
  count as contradictory evidence.
- [x] Alpha-normalize MCP count/page population signatures.
- [x] Let a later complete proof supersede earlier truncation; revoke readiness
  when truncation occurs after the latest complete proof.
- [x] Send one concise provider contract and suppress a contradictory discovered
  MCP resource-first prompt in certified `resource_mode: off` runs.
- [x] Remove unnecessary global stable ordering from full exact sets and route
  witnesses; retain it for deterministic set windows.
- [x] Replace sealed expected-cardinality execution bounds with fixed public
  capacities so bounds do not leak answer counts and in-capacity extras reach
  the exact-set comparator.
- [x] Publish the supported direct CySQL boundary, including no
  `toString(Path)` or `reduce()`, without task-specific query hints.
- [x] Include deterministic CE-derived `ADLocalGroup`, `LocalToComputer`, and
  `MemberOfLocalGroup` identities in archive/live graph and oracle catalogs.
- [x] Classify a tool-proven missing identity as `HARNESS_ERROR` while preserving
  an unproven/hallucinated identity as model-attributable `OUTPUT_INVALID`.
- [x] Bump compiler, comparator, graph, direct result, MCP capability/result,
  certifier, campaign runner, readiness, private run-state, and certification
  artifact boundaries.
- [x] Pass every simple and complex fixture through the real direct and MCP
  adapters under the V11 boundary.
- [x] Regenerate both complex seed-4401 track artifacts in a fresh V11
  namespace.
- [x] Replay stored V10 answers and receipts as counterfactual diagnostics,
  without treating changed prompts as an official rescore.
- [x] Live-certify both tracks against the unchanged controlled BloodHound
  graph, with archive/live Evidence IR and verdict parity.
- [x] Pass two-model/two-track no-model readiness from a fresh V11 output
  directory.
- [x] Pass the complete repository suite, Ruff, diff validation, source secret
  scan, and independent code review.
- [x] Update README/runbooks with final V15 scoring and certification evidence.
- [ ] Update both vaults with final V15 evidence.
- [x] Launch no provider campaign as part of V11 implementation.

#### V11 acceptance evidence

- Artifact namespace:
  `results/v2/complex-seed-4401/benchmark-correctness-v11/`.
- Graph fingerprint:
  `58ab1253a3961b36c59b540e94822726ca8a750ee2cea8497256dc3b7e1dfbd5`.
- Compiler fingerprint:
  `1a2a00bd25504cff6aa1b66030dc5822e9ae2ef40b19f39797cfa6ea9dc2c61d`.
- Comparator fingerprint:
  `f66717c62657c21c8707b70a0a4fe9dc02df78cfde29dc8fb67e73e2c88c3b5b`.
- Direct: 46/46 candidates, catalog
  `8b23c4d21cf5ba60e93f5a7a19bf605d1166124c74a6e13466022d3b1e518806`.
- MCP: 70/70 candidates, catalog
  `738721baf6db4f63cb85de6be7776ab1c0c96bd0b272b9531e009b0f1006687b`.
- Live certification schema/certifier:
  `ori-eval-live-certification-v4` /
  `accffe6aa005a228f175b72240c2095c367190f39e1b37630d5fe624e677f3b8`.
- Live graph gates: 17,088 objects and 60,342 relationships before, between,
  and after the track certifications; all observed graph fingerprints matched.
- Controlled ingest verification: 30/30 planted paths and exact projected node
  counts passed.
- Counterfactual V10 MCP transcript replay under V11 recovered seven correct
  GPT-5.6 Sol answers (45/70 total) and five correct GPT-5.5 answers (40/70
  total), with no previously correct answer regressing. This is diagnostic
  evidence only because the original models saw the V10 prompts.
- Counterfactual direct Evidence IR replay produced no score changes:
  GPT-5.6 Sol remained 23/46 and GPT-5.5 remained 21/46.
- Complete validation:
  `685 passed`, Ruff passed, `git diff --check` passed, TruffleHog found zero
  verified or unverified secrets, and Gitleaks found zero leaks.
- Two-model/two-track no-model readiness passed for GPT-5.6 Sol and GPT-5.5,
  explicitly reporting that no model calls were launched. Runner v8/readiness
  v5 performs one exact shared no-model graph gate; executable campaigns keep
  independent pre/post graph gates.

### Post-campaign scoring and prompt audit (V15)

The V10 score drop was partly intentional and partly harness-created. V1 is not
a valid score baseline because it used recall-only sets, tolerated count
differences, and accepted route node coverage without proving ordered
relationships. The V10 audit nevertheless found false negatives that strict
scoring does not justify.

- [x] Prove every provider receives one authoritative V2 prompt and one copy of
  the public question; suppress the resource-first MCP server prompt when
  `resource_mode: off`.
- [x] Require returned graph paths to bind the public source and target at the
  ordered witness endpoints.
- [x] Accept scalar `object_id` literal rows as tool-observed identities.
- [x] Accept additional route, decision, and bounded-negative evidence only
  when it is graph-attested and connected to the returned witness.
- [x] Expand the corpus-wide graph fact registry to stable solver-assertable
  scalar and list-valued properties rather than oracle-required facts alone.
- [x] Normalize declared CE collection, ACL, ownership, and tier metadata out
  of evidence without discarding unknown semantic properties.
- [x] Canonicalize known case-only ingest transformations consistently across
  archive snapshots, live snapshots, registry keys, and comparisons.
- [x] Replay every stored V10 completed answer under the corrected scorer
  boundary and prove no previously correct answer regresses.
- [x] Regenerate and offline-certify the 46 direct and 70 MCP candidates under
  `benchmark-correctness-v15`.
- [x] Pass three read-only live graph gates and full live/offline certification.
- [x] Pass two-model/two-track no-model readiness with no provider calls.
- [x] Pass 713 repository tests, Ruff, and `git diff --check`.
- [x] Complete the first final source secret scan and independent review.
- [x] Address every independent-review finding in a fresh V16 artifact
  namespace.
- [x] Complete the post-V16 source secret scan and independent review.
- [x] Address the second review's path-scope, alpha-equivalent page, and
  relationship-fingerprint findings in a fresh V17 artifact namespace.
- [x] Address the V17 re-review's live post-assignment selector and anonymous
  count-population findings in a fresh V18 artifact namespace.
- [x] Address the final multiline projection, count/page distinctness,
  `circuit_open`, and over-wide negative-proof findings in a fresh V19 artifact
  namespace.
- [x] Address the V19 re-review's contradictory-selector false proof and
  equivalent one-hop proof false negative in a fresh V20 artifact namespace.
- [ ] Record the final result in AgentVault and Personal Vault.

#### V15 acceptance evidence

- Graph fingerprint before/middle/after and readiness:
  `fb0b6785e524d40abcc9033ea2c7887eaa88e13bb6cb1f329636a4ce4b74b1c4`.
- Live graph projection: 17,088 objects, 60,342 relationships, 31 object
  queries, and 98 relationship queries per bounded pass.
- Compiler/comparator:
  `c686787c45b9a367fe0c3a6d9895fa22c5af5b6c2c59e41a460c5d4bbc57f68e` /
  `5e6aa7829c856b65fb7020bfe420b968ecf6ede66e5fd03b53ab7766359da9a1`.
- Certifier:
  `cd44cfaf5486dc8437e20d6e1b5e4730dc73c18c621430f59308eb82c2e6439f`.
- Direct: 46/46 candidates, catalog
  `877ad272ff1595281cd49ef590cae6f816f7d269e2c3250b70562064afa7c669`,
  release
  `9c18bbd386de06f5949ec8c3066b2181ab2243137b31e20057a8b72effeff0a2`.
- MCP: 70/70 candidates, catalog
  `2cd4360777d0da7759c63942591fc7b50d4f98f6b36d27f826f8a4a8eaab794e`,
  release
  `2cbe4159d3b9df82dd5e1f20bb708df85814fdf46a398072edc4a8edb9b2b191`.
- Readiness:
  `72689d503bb4f8cd9b55acd16972442695d46453af3567f692c7d607f22ebcc2`;
  both Codex model capabilities passed, and no model call was launched.
- Diagnostic stored-answer replay: all previously correct answers stayed
  correct. Corrected MCP totals are 45/70 for GPT-5.6 Sol and 40/70 for
  GPT-5.5; these are diagnostics, not an official rescore, because V10 prompts
  were different. Direct remains 23/46 and 21/46.

#### V16 independent-review closure

- [x] Treat a broader non-zero direct absence count as
  `PROOF_INSUFFICIENT`, with no reasoning verdict.
- [x] Preserve returned-path lineage across `WITH` aliases and reject rebound
  path variables.
- [x] Require an MCP route/decision receipt to contain at least two graph nodes
  and one graph edge before it can unlock finalization.
- [x] Normalize omitted first-page `SKIP 0` and explicit later `SKIP` pages to
  one population key.
- [x] Add the shared query contract to the MCP finalization fingerprint and
  bump the state-machine/certifier versions.
- [x] Pass 154 focused scorer/runtime regressions.
- [x] Pass all 317 V2 tests.
- [x] Regenerate 46 direct and 70 MCP candidates under
  `benchmark-correctness-v16`.
- [x] Pass 276 direct and 420 MCP offline fixtures.
- [x] Pass read-only live certification on all three graph gates.
- [x] Pass two-model/two-track no-model readiness with no provider calls.

V16 fingerprints:

- Graph:
  `fb0b6785e524d40abcc9033ea2c7887eaa88e13bb6cb1f329636a4ce4b74b1c4`.
- Compiler/comparator:
  `c686787c45b9a367fe0c3a6d9895fa22c5af5b6c2c59e41a460c5d4bbc57f68e` /
  `5e6aa7829c856b65fb7020bfe420b968ecf6ede66e5fd03b53ab7766359da9a1`.
- Certifier:
  `90d6a98ee555fd6c74a17b2c3c3051d128fcdb7c8f71e89cb2959eeaa235dc58`.
- Direct catalog/release/live certification:
  `43d2c71c3f9f98cf892a6b9b7b13753ff6ed1cc9ba257e3bc8b06595d5d14f52` /
  `b9d3d42ea63ae0569aae2ad2f59674ec06d7e68660f64ae81183eb27a988f9c9` /
  `98f0df5c974be0b50cac9875a2ae0b3b781d9e8fd98bc1b828de39789340e568`.
- MCP catalog/release/live certification:
  `e92946461f37813979a0df26e679ce57c8328741da87e55eecbcb7d41fe79153` /
  `39aa51a4bec0f8fd49183b9ca3a0faac4195a2bdd295cac90af06344f735958a` /
  `066586c27159a260d66682908c351363d3d3fbcecce42f87c79bdcb1299302b0`.
- Readiness:
  `c0b01c6c331a0ec495e40ed02fe77ab505587bb3c611beae8bc838303ebb7ff7`.

#### V17 final-review closure

- [x] Scope exact endpoint selectors to variables live at the returned path,
  preserving valid `WITH` passthrough/aliases and rejecting later detached
  selectors.
- [x] Alpha-normalize page population variables so semantically identical
  pages cannot be split by a local variable rename.
- [x] Include relationship endpoint/type contracts in the MCP finalization
  fingerprint.
- [x] Bump the MCP result/state-machine and certifier boundaries.
- [x] Pass 206 focused runtime, MCP, direct-adapter, and captured-receipt tests.
- [x] Regenerate and offline-certify 46 direct and 70 MCP candidates under
  `benchmark-correctness-v17`.
- [x] Pass 276 direct and 420 MCP offline fixtures.
- [x] Pass read-only live certification on all three graph gates.
- [x] Pass two-model/two-track no-model readiness with no provider calls.
- [ ] Pass the final full repository/static/secret/re-review gates.
- [ ] Complete both vault updates.

V17 fingerprints:

- Graph:
  `fb0b6785e524d40abcc9033ea2c7887eaa88e13bb6cb1f329636a4ce4b74b1c4`.
- Live graph verification:
  `5a832aaf6d15972eaf4f75c44d442f0e839004b185777eb0947b5bb939f12f53`.
- Compiler/comparator:
  `c686787c45b9a367fe0c3a6d9895fa22c5af5b6c2c59e41a460c5d4bbc57f68e` /
  `5e6aa7829c856b65fb7020bfe420b968ecf6ede66e5fd03b53ab7766359da9a1`.
- Certifier:
  `55170d1aa62df366cf20f8152a9c89035e15ed33fcaf36bec4e03676be1d62f0`.
- Direct catalog/release/live certification:
  `f46a56d1e24c6f5fb257a68d0e47d82113c2de49cee141a84697e146401536ce` /
  `be6abca0dc99ed2881611a698d5ce3dabf1eaaecf81cd0052e36688222875b19` /
  `669d8e34d4a75d89055ddee35fdfe4accbfd6d83fe7696f9f53afaa8851d275c`.
- MCP catalog/release/live certification:
  `6973f469b609200674580b9370eed811117318f23a711c20caaf7b8bce68b229` /
  `b665d9fa200def86a3df76ff71bc679c04d8528f08809a9a2ce0fbf7505a75d4` /
  `9b70f4a7312a3099c67566e79af8d0ff4d8593d2d7dcc079e06a13498a23aaf9`.
- Readiness:
  `53dff3f02350516faba5cbc80f5e9a566c57532a936ab6f3eb2e730bdef08ec6`.

#### V18 final false-negative closure

- [x] Accept endpoint selectors applied after path assignment only while those
  variables remain in the returned path's live lineage.
- [x] Preserve selector lineage through valid `WITH` passthrough/aliases and
  continue rejecting reintroduced detached variables.
- [x] Canonicalize one anonymous `COUNT(*)` population with the equivalent
  named enumeration population.
- [x] Bump the MCP result/state-machine and certifier boundaries again.
- [x] Pass 158 focused MCP runtime, state-machine, and captured-receipt tests.
- [x] Regenerate and offline-certify 46 direct and 70 MCP candidates under
  `benchmark-correctness-v18`.
- [x] Pass 276 direct and 420 MCP offline fixtures.
- [x] Pass read-only live certification on all three graph gates.
- [x] Pass two-model/two-track no-model readiness with no provider calls.
- [ ] Pass the final full repository/static/secret/re-review gates.
- [ ] Complete both vault updates.

V18 fingerprints:

- Graph/live verification:
  `fb0b6785e524d40abcc9033ea2c7887eaa88e13bb6cb1f329636a4ce4b74b1c4` /
  `5a832aaf6d15972eaf4f75c44d442f0e839004b185777eb0947b5bb939f12f53`.
- Compiler/comparator:
  `c686787c45b9a367fe0c3a6d9895fa22c5af5b6c2c59e41a460c5d4bbc57f68e` /
  `5e6aa7829c856b65fb7020bfe420b968ecf6ede66e5fd03b53ab7766359da9a1`.
- Certifier:
  `1c9d8d9801066503a96644edbcd1fdf514bc8a3ecd757ae0e66e077b15b0b5b0`.
- Direct catalog/release/live certification:
  `2c21803a71d3b01125f25ee58144b2e39b9a30ed8836e4fd85cc5acab023abce` /
  `c388e3981a0cb14bd8d37e61e4083a6ac746271b1230f97903e58618f94db77f` /
  `6c8a7b424e7777e5041125709570b302701e2f97bbb4fc7a430d079891984bfa`.
- MCP catalog/release/live certification:
  `ad42aac383c580333d6485a6b03abcf7ed27ffe219fc84e78d343bcb34a976e8` /
  `2d4bb2c4feccb4d518ba9509fd3ca740e84403d23b529d68bab5cca6fb77c298` /
  `d19468008e21a6e0dedff34223c96fedbaa4adc856d05ce10bd11ecf5f576ca2`.
- Readiness:
  `028c6b4c21e9d77e055867d742a2d8daf9dbac8bfd8e9b94ce7a310aa912f6c6`.

#### V19 prompt and proof-equivalence closure

- [x] Parse multiline `RETURN` and `WITH` projections without losing identity
  lineage.
- [x] Require count/page population and identity distinctness to match.
- [x] Permit distinct-count/row-page equivalence only when receipts prove one
  globally unique object ID per row.
- [x] Reject duplicate or missing row identities as incomplete proof.
- [x] Classify MCP `circuit_open` receipts as infrastructure.
- [x] Treat an otherwise exact bounded-negative query with a wider hop ceiling
  as broader.
- [x] Publish the population, distinctness, and unique-row requirements in the
  solver-visible result contract without leaking expected facts.
- [x] Pass 199 focused runtime/adapter/captured-receipt tests.
- [x] Pass the complete repository suite: 721 tests in 627.20 seconds.
- [x] Regenerate and offline-certify 46 direct and 70 MCP candidates under
  `benchmark-correctness-v19`.
- [x] Pass 276 direct and 420 MCP offline fixtures.
- [x] Pass read-only live certification on all three graph gates.
- [x] Pass two-model/two-track no-model readiness with no provider calls.
- [ ] Pass the final static/secret/re-review gates.
- [ ] Complete both vault updates.

V19 fingerprints:

- Graph/live verification:
  `fb0b6785e524d40abcc9033ea2c7887eaa88e13bb6cb1f329636a4ce4b74b1c4` /
  `5a832aaf6d15972eaf4f75c44d442f0e839004b185777eb0947b5bb939f12f53`.
- Compiler/comparator:
  `c686787c45b9a367fe0c3a6d9895fa22c5af5b6c2c59e41a460c5d4bbc57f68e` /
  `5e6aa7829c856b65fb7020bfe420b968ecf6ede66e5fd03b53ab7766359da9a1`.
- Certifier:
  `ce74acc27190a91362327599dcb5cb6e4ceadf3b59e1ef60783de63f5c0742eb`.
- Direct catalog/release/live certification:
  `3734c3d15cb629758e4f77d006cef17e096d6c57caaf3bf9fb9943c78bdef74f` /
  `9ac6bccc07b032186df0ee30b2e477c61c8e99f3f60f1ddec2a296da633aee57` /
  `cc2b7ca17ab7646fff6715f689281b64dda6c0aba09d9d7c4441258f15e6e583`.
- MCP catalog/release/live certification:
  `51788c8f9aeb1f052b82e76619dd2888794e78dd64690cdacce763dae46a1e84` /
  `c7c075d2e0a420309acf9e7dd31fbd57df71403c345ee4a7458a1bc478cd0721` /
  `07465856be2f55d6d55b07a734a1dd57d103fcdcd1233eea481a65ff9fc133d6`.
- Readiness:
  `c492c28eb584868b15764040c0cf1b7766f067418c184581620c1fa9cdd941a6`.

#### V20 final negative-proof closure

- [x] Reject a bounded-negative query when inline and `WHERE` selectors bind
  the same endpoint variable to conflicting public roles.
- [x] Accept ordinary exact one-hop relationship syntax as equivalent to
  `*1..1` when the complete public hop ceiling is one.
- [x] Accept the corresponding broader untyped one-hop zero proof while
  keeping its non-zero result inconclusive.
- [x] Cross both corrections through the real MCP transcript projector.
- [x] Bump direct result contract, MCP result contract, MCP evidence state
  machine, and certifier boundaries.
- [x] Pass 199 focused runtime/adapter/captured-receipt tests.
- [x] Regenerate and offline-certify 46 direct and 70 MCP candidates under
  `benchmark-correctness-v20`.
- [x] Pass the complete direct and MCP offline fixture manifests.
- [x] Pass read-only live certification on all three graph gates.
- [x] Pass two-model/two-track no-model readiness with no provider calls.
- [ ] Pass the final repository/static/secret/re-review gates.
- [ ] Complete both vault updates.

V20 fingerprints:

- Graph/live verification:
  `fb0b6785e524d40abcc9033ea2c7887eaa88e13bb6cb1f329636a4ce4b74b1c4` /
  `5a832aaf6d15972eaf4f75c44d442f0e839004b185777eb0947b5bb939f12f53`.
- Compiler/comparator:
  `e037c580616028daf82e44771ecc59d6353b30bb294ffc2816b7df062800d04d` /
  `5e6aa7829c856b65fb7020bfe420b968ecf6ede66e5fd03b53ab7766359da9a1`.
- Certifier:
  `7a58ed05ecba8ad92f891c0e007039dbd50620781b8981044be5eea919b65c06`.
- Direct catalog/release/live certification:
  `a3fff5385721f1d89ac70b9e5855199da8159d1b285529b9bb05768c91d866ad` /
  `dfad392d4e7744f88189aafac97b36a3fac1b6d4f8e95817069a1c89dd096c20` /
  `57226e60e2a5622f3bc31c3ed912c51aeaa8bf64efbe8c8cf4a2ddfe17aaa00e`.
- MCP catalog/release/live certification:
  `8344a0cdbde1d8f60f3cc5bfb9632aff56a977e470346d9c9619f400c1980be9` /
  `2fa21757dc780cb338755dfb0ab99ef350f98957689de3b189e371044598e509` /
  `d562de4109d1f6c0adbd1159e2ca2067eeb17e016c9c1dc559c64678f795eb8f`.
- Readiness:
  `df1a097c7f866fb33f15443006413c03fd94d9b784e4742bbb1155a69bbb4f1e`.

#### V21 undeclared-filter closure

- [x] Reject bounded-negative selector predicates on relationship, path, or
  otherwise non-endpoint variables.
- [x] Reject additional endpoint labels beyond the public object type.
- [x] Cross a forced-zero non-endpoint selector through the real MCP transcript
  projector and prove it cannot unlock finalization.
- [x] Bump direct result contract, MCP result contract, MCP evidence state
  machine, and certifier boundaries.
- [x] Pass 199 focused runtime/adapter/captured-receipt tests.
- [ ] Regenerate and offline-certify both complex tracks under
  `benchmark-correctness-v21`.
- [ ] Pass read-only live certification on all three graph gates.
- [ ] Pass two-model/two-track no-model readiness with no provider calls.
- [ ] Pass the final repository/static/secret/re-review gates.
- [ ] Complete both vault updates.

V21 was superseded during independent review. V21 through V27 are stale
development boundaries and must not be executed or resumed.

#### V28 scorer and task-communication closure

- [x] Send exactly one authoritative V2 system prompt and one compiled user
  question; retain the discovered BloodHound server prompt only as provenance
  when `resource_mode: off`.
- [x] Make every scorer-relevant acceptance clause solver-visible through the
  typed `AcceptanceSpec` and concise question without exposing oracle facts.
- [x] State exact hop ceilings and case-sensitive BloodHound identifiers in the
  public task contract.
- [x] Remove misleading property wording from claims that do not require that
  property.
- [x] Align bounded-negative question text, answer schema, execution proof, and
  comparator policy.
- [x] Accept legitimate direct and MCP evidence shapes including flattened
  scalar identities, graph-attested connected route context, benign CE
  properties, case-normalized ingest identities, path lineage, alpha-equivalent
  count/page populations, and exact one-hop syntax.
- [x] Reject contradictory or non-endpoint selectors, wrong-case identifiers,
  display aliases used as live Cypher values, extra endpoint labels, forced-zero
  filters, arbitrary relationship bodies, and lower-bound-zero traversals.
- [x] Prevent failed or truncated MCP receipts from seeding later completeness
  state; allow a later independent complete proof to supersede a prior
  payload-truncated receipt.
- [x] Pass the independent reviewer counterexamples for grouped endpoint
  predicates and repeated exact selectors.
- [x] Regenerate and offline-certify complex direct (46), complex MCP (70),
  simple direct (20), and simple MCP (40) under
  `benchmark-correctness-v28`.
- [x] Pass read-only complex seed-4401 live certification for all 46 direct and
  70 MCP candidates across all three graph gates.
- [x] Pass two-model/two-track no-model readiness with no provider calls.
- [x] Pass the complete repository suite: 730 tests in 575.58 seconds.
- [x] Pass Ruff, lock validation, diff validation, and an isolated
  publishable-source secret scan.
- [x] Switch the controlled graph from complex seed 4401 to simple seed 1234
  with explicit operator approval and repeat simple live certification.
- [x] Restore the exact complex seed-4401 archive after the temporary simple
  certification and re-prove health, exact node counts, 30/30 planted paths,
  the V28 graph fingerprint, and no-model readiness.
- [x] Exclude final branch reconciliation from this goal and retain it as a
  later PR-preparation task.
- [x] Record the final V28 result in both vaults.

At the V28 completion point, that was the only executable complex boundary. It
bound:

- compiler `ori-claim-compiler-v2.10.0`;
- direct result contract `ori-direct-result-contract-v13`;
- MCP result/finalization contract `ori-mcp-result-contract-v21` /
  `ori-mcp-evidence-v21`;
- certifier `ori-live-certifier-v23`;
- graph
  `fb0b6785e524d40abcc9033ea2c7887eaa88e13bb6cb1f329636a4ce4b74b1c4`;
- compiler fingerprint
  `e62dc48d908d99f03cbb7e75c731411d9eb65b5a61fdc79817b75755eba25058`;
- comparator fingerprint
  `5e6aa7829c856b65fb7020bfe420b968ecf6ede66e5fd03b53ab7766359da9a1`;
- direct release/live certificate
  `e1be6a859b5212c13acf87de7b3368263a82fd9314b91acbf11e998b08b6408a` /
  `177f8ff0db9c37d20b2b894dfd7de8940d5f5dae72b53ad10242b0e9a55e910d`;
- MCP release/live certificate
  `40766c5c2d7f291a443b75b4ab01a8e17e35e6aee10321ff37f965b6daf1651f` /
  `79ee3e027c0fd915db1531dd6daac512323ef80c26bb96a49e7faf55c55acc48`.

The 2026-07-31 read-only completion audit reconfirmed that the controlled
target is `bloodhound-ori.mwnickerson.com`, complex seed 4401 passes exact node
counts and all 30/30 planted paths, and simple live certification stops safely
on the expected graph mismatch (`ADLocalGroup` 9,260 live versus 232 in the
simple archive). The reversible certification handoff is pinned to:

- simple ZIP
  `c3e939f063fcf9501551a80f07fcddd84966838a5f5cc9bff9ba3b29cdc6f3e4`
  and manifest
  `3a20e9a2f9199130a3fa2b29f06433f7c949627126398c4bb1eba549fc5f9c1b`;
- complex restore ZIP
  `a00e60e0f7ba8a02bcfee74e0ef1b99d7e46107d25b5172ffb1231e51f8f5edb`
  and manifest
  `d2dd22a037a3d8f2d4055c2792a5abee8806a43a2900519378e0b1822c314be9`.

Both ZIPs pass archive integrity. On 2026-07-31 the operator approved the two
destructive transitions. Each clear was pinned to
`https://bloodhound-ori.mwnickerson.com:443`, asserted the complete endpoint,
and required the clear API to return exactly HTTP 204. BloodHound MCP upload
job 12 installed the pinned simple archive. Simple health, exact node counts,
and all 8/8 planted paths passed before V28 live certification certified all 20
direct and 40 MCP candidates across three graph gates. No-model readiness then
passed on graph
`e964390638310fc94f4b4b3fe4a50a2f3e0006510626af6b12f829f8631549ae`.

The temporary graph was then cleared under the same target assertion and
BloodHound MCP upload job 13 restored the pinned complex archive. The restored
graph passed health, exact node counts, all 30/30 planted paths, and V28
no-model readiness. Its graph fingerprint returned to
`fb0b6785e524d40abcc9033ea2c7887eaa88e13bb6cb1f329636a4ce4b74b1c4`,
with the existing 46-direct and 70-MCP releases accepted. No provider calls
were launched during either readiness gate. `origin/master` still does not
contain containment commit `0a560294`; final branch reconciliation remains
externally pending.

## Definition of done

- [x] All eight CV1 regression cases pass through v2.
- [x] Direct containment behavior remains unchanged.
- [x] Prompt, oracle, bounds, and policy derive from one claim.
- [x] Every v2 scoring surface uses one Evidence IR and comparator.
- [x] Mechanism-valid alternatives pass and decoy/wrong-mechanism routes fail.
- [x] Exact sets and counts reject extras and incorrect counts.
- [x] Direct, transitive, and effective semantics are explicit and certified.
- [x] No oracle material reaches a model-visible surface.
- [x] MCP finalization uses claim-relevant evidence state.
- [x] Graph, oracle, protocol, compiler, comparator, and catalog fingerprints are
  immutable and enforced.
- [x] Every current simple/complex capability is migrated or replaced.
- [x] Every migrated task has complete fixture evidence.
- [x] Current simple live and offline adapter-projected verdicts agree under
  V28 across all 20 direct and 40 MCP candidates on graph
  `e964390638310fc94f4b4b3fe4a50a2f3e0006510626af6b12f829f8631549ae`.
- [x] Current complex live and offline adapter-projected verdicts agree under
  the final V28 live-certifier fingerprint.
- [x] No v2 task depends on hidden template-specific grader behavior.
- [x] Every candidate is safe under its direct or MCP profile.
- [x] The original implementation goal launched no paid model run; the later
  operator-authorized validation campaign is recorded in the incident section.
- [x] Treat reconciliation and final PR-diff verification as post-goal
  integration work after the containment PR merges.

## Seven-model high-effort campaign audit and V29 closure

The operator-authorized V28 campaign on 2026-08-11 was the first broad run
across GPT-5.6 Sol, Terra, Luna, Daybreak Blue, Daybreak Red, Waluigi, and
GPT-5.5 at one explicit `high` reasoning effort. It produced useful diagnostic
evidence, but it is not publishable and must not be resumed under V29. The run
checkpointed 407 of 812 scheduled samples before external termination:

```text
completion = 407 / 812 = 50.12%
old schedule = 7 models * (46 direct + 70 MCP) = 812 samples
```

The direct track completed all 322 samples and preserved the graph exactly.
Its observed 246/322 correct result (76.40%) contained both genuine model
errors and benchmark-induced false negatives. The audit separated them before
changing code:

- 14 contextual-path answers returned the requested path and supporting
  relationship, but BloodHound omitted a separately projected relationship's
  non-path endpoint unless that node was also returned. The public contract had
  never required that endpoint projection, so these were benchmark false
  negatives.
- two bounded-negative answers used the standard zero-preserving
  `OPTIONAL MATCH p=... RETURN count(p)` form. BloodHound returned zero, but the
  proof parser recognized only `MATCH p=`. These were benchmark false
  negatives.
- one Terra response returned relationships without the required path and with
  unresolved internal endpoints. That is model-attributable invalid output,
  not a harness crash.
- one stale-session answer returned explicitly forbidden decoy edges. It is a
  gradeable `ROUTE_FORBIDDEN_EDGE`, not generic invalid output.
- 40 first queries timed out as too complex and 14 token-equivalent retries
  were then stopped correctly by the deny cache. Every one contained 21 to 45
  pairwise node-inequality clauses, averaging 27.7, because the public cycle
  wording encouraged an O(n-squared) query construction. The same bounded
  representative route executed immediately when those inequalities were
  removed; the returned witness still remained subject to comparator cycle
  rejection.

The maximum diagnostic correction from the 14 contextual and two negative
false negatives is:

```text
observed direct correctness = 246 / 322 = 76.40%
diagnostic ceiling          = 262 / 322 = 81.37%
maximum false-negative gap  = 16 / 322  = 4.97 percentage points
cycle-induced failures      = 54 / 322  = 16.77% of direct samples
```

The 262/322 value is not a published rescore. The models saw V28 wording, and
several failures occurred before a valid graph witness existed. Only a fresh
V29-versus-V29 campaign can produce official scores.

The partial MCP run exposed three additional correctness boundaries:

- a complete 500-identity receipt could be discarded when a schema-only retry
  returned an empty list;
- decision claims could legitimately bind their public subjects to interior
  nodes on one returned path, while route claims still require directional
  endpoints; and
- final edge/property assertions needed mechanical binding to the latest
  complete claim-relevant BloodHound receipt.

The run also scheduled multiple task IDs with identical solver-visible
semantics. V29 keeps every compiled task and certification proof but releases
one deterministic representative per public-semantic equivalence class. It
fails closed when equivalent public tasks have different sealed scorer
outcomes. The resulting schedule math is:

```text
direct release: 46 compiled -> 42 scheduled (4 fewer, 8.70%)
MCP release:    70 compiled -> 55 scheduled (15 fewer, 21.43%)
seven models:   812 old -> 679 new samples (133 fewer, 16.38%)
```

This is deduplication, not task deletion: every alias remains compiled,
oracle-bound, fixture-certified, and listed in `equivalent_task_ids`.

### V29 implementation checklist

- [x] Require both endpoint node variables for separately returned supporting
  relationships and classify unresolved BloodHound internal endpoints as
  model-attributable invalid output.
- [x] Partition one unique acyclic public source-to-target route structurally,
  pass connected supplemental evidence to the comparator, and preserve
  gradeable forbidden-edge verdicts.
- [x] Accept an exact zero-preserving `OPTIONAL MATCH` absence proof only after
  both public singleton endpoints have been bound; reject unbound,
  contradictory, or extra-filtered variants.
- [x] Make cycle simplicity a comparator responsibility and tell models not to
  generate quadratic pairwise inequality filters; continue rejecting an
  actually cyclic returned witness.
- [x] Materialize complete MCP set answers from mechanically proven receipt
  pages, including a complete 500-identity page followed by an empty
  schema-only retry.
- [x] Permit decision-role selectors anywhere on the one returned path while
  preserving route endpoint direction and rejecting detached selectors.
- [x] Bind MCP final edges and public properties to the latest complete
  claim-relevant receipt and reject unsupported assertions as `OUTPUT_INVALID`.
- [x] Retain all 46 direct and 70 MCP compiled/certified tasks while releasing
  42 and 55 unique public-semantic representatives.
- [x] Reject public-semantic equivalence classes whose sealed scorer outcomes
  disagree.
- [x] Derive campaign reports and denominators from the candidate release, not
  the larger certification inventory.
- [x] Add an exclusive campaign-output lock, unique atomic temporary files,
  file and directory `fsync`, a fingerprinted lifecycle receipt, durable signal
  and cancellation state, and per-track report/completion publication.
- [x] Preserve interrupted provider attempts without charging the original
  infrastructure-retry budget on resume.
- [x] Fix archive graph construction from repeated identity-set rebuilding to
  one linear-time identity set per graph.
- [x] Fail readiness on the stale Bloodhound-MCP revision, audit the local
  `009c88f` to `92a37dd` delta, advance the capability profile to
  `ori-mcp-92a37dd-bhce-9.1-cypher-v7`, and recertify instead of bypassing the
  pin. The delta adds bounded credential preflight and byte-upload tools while
  preserving the certified read-only Cypher callable.
- [x] Pass the combined direct/MCP/campaign regression suite.
- [x] Pass the complete repository suite (759 tests), Ruff, diff validation,
  and independent review on the final V29 diff.
- [x] Recompile and offline-certify complex seed 4401 under V29.
- [x] Re-run read-only live certification against the unchanged controlled
  complex graph and prove all three graph gates.
- [x] Create a fresh seven-model `high`-effort V29 config and pass no-model
  readiness without provider calls.
- [x] Pass the final publishable-source secret scan: Gitleaks scanned 3.31 MB
  with zero findings; TruffleHog 3.96.0 scanned 417 chunks / 4,067,728 bytes
  with zero verified or unverified secrets.
- [x] Record final V29 fingerprints and evidence in the README, design
  rationale, certification evidence, AgentVault, and Personal Vault.

V28 artifacts and checkpoints remain immutable incident evidence. Every
compiler, result-contract, finalization, certifier, runner, catalog, and
readiness fingerprint changed, so V29 uses a new artifact root and a new
campaign output directory.
