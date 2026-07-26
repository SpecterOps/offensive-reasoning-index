# ORI Benchmark Correctness and Architecture V2

Status: in progress
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

## Baseline and integration

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
- [ ] After the containment PR merges, reconcile through exactly one path:
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
- [x] Verify the graph before and after each track.
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
- [ ] Re-live-certify the complete complex catalog under adapter-certifier v3
  after the exact complex graph is loaded with operator approval.
- [ ] Confirm current adapter-certifier-v3 complex live/offline Evidence IR and
  verdict parity.
- [x] Run no paid or local model campaign.

## Phase 11: documentation and handoff

- [x] Update the README with the opt-in v2 development workflow.
- [x] Update the hardening runbook.
- [x] Document task authoring and certification.
- [x] Document model-blind execution constraints.
- [x] Publish the candidate-catalog contract for the later selector.
- [x] Preserve v1 reproduction instructions.
- [x] Run full pytest, Ruff, diff check, and secret scan.
- [x] Complete final independent review.
- [x] Complete focused oracle-leakage and scoring-parity reviews.
- [x] Add AgentVault project, decision, and session continuity.
- [x] Route the matching Personal Vault write through librarian and require
  `LIBRARIAN WRITE COMPLETE`.
- [ ] Reconcile the final branch against the containment PR merge strategy.

The two unchecked reconciliation items are a deferred integration checkpoint,
not a v2 implementation or certification gate. As of 2026-07-26,
`origin/master` does not contain `0a560294`; run them after the containment PR
is merged and before opening the v2 PR.

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
- [ ] Live-certify both complex seed-4401 tracks under certifier v3 after an
  operator-approved graph replacement.
- [x] Run final full repository tests (581 passed), Ruff, diff validation,
  source-control secret scan, and independent review for the
  adapter-certifier-v3 change.

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
- [x] Current simple live and offline adapter-projected verdicts agree.
- [ ] Current complex live and offline adapter-projected verdicts agree under
  certifier v3.
- [x] No v2 task depends on hidden template-specific grader behavior.
- [x] Every candidate is safe under its direct or MCP profile.
- [x] The original implementation goal launched no paid model run; the later
  operator-authorized validation campaign is recorded in the incident section.
- [x] The branch is ready to rebase cleanly after the containment PR merges.
