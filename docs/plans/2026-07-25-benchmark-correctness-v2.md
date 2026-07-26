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
- [x] Live-certify the complete simple and complex catalogs.
- [x] Verify pre/post graph fingerprints.
- [x] Confirm simple and complex live and offline Evidence IR and verdict parity.
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
- [x] Live and offline verdicts agree.
- [x] No v2 task depends on hidden template-specific grader behavior.
- [x] Every candidate is safe under its direct or MCP profile.
- [x] The original implementation goal launched no paid model run; the later
  operator-authorized validation campaign is recorded in the incident section.
- [x] The branch is ready to rebase cleanly after the containment PR merges.
