# S1-03A — generated-catalog scenario and continuation coverage

Revision 2, September 5, 2026. Status: implemented, independently reviewed and focused validation passed.
Parent: [Stage 1 plan](2026-09-05-oaic-stage-1-cleanup-plan.md).
Owner: root/test engineering. Independent plan and implementation reviewer:
`oaic_stage1_adversarial_fallback` (assignment requested; implementation waits).
No production, schema, runtime fingerprint, provider, model, remote Git or live
graph changes. This is a bounded step toward the whole-suite reduction target,
not a replacement for the remaining audit or fewer-than-700 objective.

## Evidence and limits

Pre-slice collection has 1,062 items, 862 functions and 59 modules. The private
`results/p2-slices/s1-03/collection.private.json` records every node ID, module,
original function, parameterized status and resolved fixture scopes. Node IDs
may contain synthetic query parameters, so do not publish that raw inventory.
Eleven explicitly named subtests already exist; do not sum collected items and
subtests as though all were independent obligations. Other multi-assertion tests
need semantic review before a whole-suite logical-obligation count is claimed.

Read-only reviewers inspected V2 runtime/Direct/real-receipt modules (213 cases)
and legacy eval/ops/MCP modules (155 cases). Most input matrices and cross-layer
checks represent distinct behavior. These findings do not establish a defensible
363-case reduction. Retain those cases while the remaining modules are audited.

## Exact change A: one generated legacy catalog

Only `tests/test_eval.py` changes. Replace these ten collected functions with
private assertion helpers named by replacing `test_` with `_assert_`:

| Original test / retained subtest label | Preserved obligation |
| --- | --- |
| test_generate_tasks_count | Exactly eleven generated tasks |
| test_generate_tasks_source_name_in_question | Source and target in AdminTo question |
| test_enumeration_task_has_own_reference_cypher | Enumeration not anchored to planted user; correct group |
| test_path_finding_task_uses_verification_cypher | Planted path reference preserved |
| test_global_tasks_present | All three existing global IDs |
| test_global_admin_to_returns_computers_not_paths | Computer projection, no path projection |
| test_global_privileged_sessions_returns_computers_not_paths | Session computer projection, no path projection |
| test_acl_chain_02_names_source_and_target | Both endpoints and abused-group wording |
| test_all_anchored_generated_questions_name_their_graded_endpoints | Every anchored task's source and target, unchanged loop |
| test_phase4_composite_names_bridge_and_delegation_target | Source, bridge and delegation target |

Each helper takes `tasks`. Remove only its identical
`tasks = generate_tasks(_make_manifest())` setup statement; keep docstrings,
lookups, loops, comments and all 21 static assertions unchanged. In particular,
retain `next(...)`, not a dictionary lookup that changes duplicate behavior.

Add `test_generated_legacy_catalog_contracts(subtests)`: construct one fresh
catalog inside that function, then use ten explicit `with subtests.test(msg=...)`
blocks in the table order. Each block calls its corresponding helper; the whole
lookup and assertion body executes inside the named block. No opaque loop and
no module/session fixture. Supporting-edge modification tests remain untouched
and independently regenerate their own manifest. No helper writes any task,
metadata or container. A deep-value before/after audit must confirm this.

Expected collection: test_eval 71 → 62; suite 1,062 → 1,053. Ten logical
obligations become ten named subtests; none is deleted. Actual catalog-generation
calls for these obligations go 10 → 1. This is setup-count evidence, not a measured
wall-clock speedup. No other test reduction is authorized by this slice.

## Exact change B: prove continuation after a failure

Only the body of
`tests/test_ops.py::test_cli_run_config_run_all_profiles_keep_going_continues_after_failure`
changes. Preserve existing prep(success), smoke(failure), error aggregation,
nonzero exit and all three diagnostic-string assertions. Append a third profile
`after` with kind `preflight`, the same manifest and distinct output `out-after`.
The existing fake preflight returns success; expected call order becomes
`["preflight", "smoke_eval", "preflight"]`.
Assert CLI output reaches `[3/3] after (preflight)`. Do not alter the neighboring
stop-on-first-failure test or any production exception/control-flow logic.
This strengthens coverage; it is not a duplicate removal or behavior change.

## Pre-edit gate and safe ownership

Before implementation, verify current dirty source matches the completed baseline
run and inventory. Save complete exact copies of both owned files under ignored
`results/p2-slices/s1-03a/pre-edit/tests/`, plus snapshot metadata and a sorted
implementation inventory using the same schema as S1-02. Base Git commit remains
`afa9542`; the snapshot hash, not HEAD alone, identifies this slice's source.
Reviewer confirms copies, absence of overlapping writers and reviewed plan before
edits. The current full test run may finish; do not edit these files during it.

Apply patches using absolute worktree paths and complete-source handling with
truncation detection. Preserve all other existing edits. Do not format whole
files. Capture post-edit hashes before validation and verify after tests.

## Exact validation and review

1. Compare ten helper bodies to the pre-edit tests after removing only the setup
   assignment and changing their names/arguments; every retained assertion and
   loop AST must match. Compare all other eval definitions byte-for-byte.
2. Invoke every helper over one catalog and compare deep dataclass value snapshots
   before/after; setup-call spy confirms one generation for the scenario.
3. Fault-injection probe in an isolated interpreter: replace the first helper with
   an AssertionError and run the real pytest scenario. Require one named subtest
   failure, nine passing subtests and nonzero exit. Repeat with a middle helper
   raising the missing-task `StopIteration`; require later subtests to run too.
   No working-source mutation.
4. For continuation, inspect the exact three-profile config and call order. Run a
   negative-control isolated invocation with `--keep-going` removed; the strengthened
   test must fail its post-failure call-order assertion. Retain the standalone
   production stop-on-failure acceptance test.
5. Run `uv run pytest tests/test_eval.py tests/test_ops.py tests/test_v2_regression_coverage.py -q`.
   All cases and named subtests pass; historical V2 regression names stay unchanged.
6. Full collection is 1,053. Run `uv run ruff check src scripts tests`,
   `uv lock --check`, `git diff --check` and changed-public-file secret scans.
7. Independent implementation reviewer compares actual pre-edit snapshots,
   obligation mapping, mutation controls, isolation and final hashes. A plan pass
   cannot substitute for this review. Full-suite validation after the last slice
   remains mandatory before Stage 1 closure.

Stop for lost assertions, early-failure suppression, mutable shared contamination,
changed test fixtures outside scope, an unexpected count, truncated content,
unreviewed findings or source changes during validation. Fix/re-review, never
weaken an assertion to make consolidation pass.

## Deferred candidates and rejected shortcuts

V2 reviewers found possible public-request/schema, route-state-transition,
literal-cardinality and count-encoding scenarios (net five cases), plus an optional
selector-normalization contract (net two). These require separate exact scopes,
fresh-projector ownership where needed and adversarial review; not authorized here.

Keep parser/nonfinite/fence vectors, query and failure taxonomy, interruption/
retry cases, projection negatives and historical V1/Phase3/Phase4 dispatch.
Matching assertion bodies are not duplicate inputs. Adaptation layers and
end-to-end tests do not substitute for each other. Longer tests already covering
real lifecycle transitions should not be split or deleted to manufacture progress.

## Collection inventory by module

“Parameterized items” counts all collected members of a parameterized function,
not only expansion above one. Static imports/fixture names do not prove semantic
coverage; every implementation candidate still requires an obligation map.

| Module | Collected items | Functions | Parameterized items |
| --- | ---: | ---: | ---: |
| `tests/test_v2_model_runtime.py` | 131 | 110 | 30 |
| `tests/test_eval.py` | 71 | 71 | 0 |
| `tests/test_direct_query_safety.py` | 67 | 26 | 47 |
| `tests/test_v2_compiler.py` | 49 | 42 | 10 |
| `tests/test_ops.py` | 47 | 47 | 0 |
| `tests/test_v2_mcp_real_receipts.py` | 42 | 28 | 22 |
| `tests/test_v2_direct_adapter.py` | 40 | 28 | 15 |
| `tests/test_mcp.py` | 37 | 35 | 4 |
| `tests/test_v2_campaign_status.py` | 36 | 29 | 12 |
| `tests/test_v2_mcp.py` | 32 | 19 | 15 |
| `tests/test_v2_schema.py` | 29 | 25 | 6 |
| `tests/test_v2_campaign_supervisor.py` | 28 | 20 | 9 |
| `tests/test_provider_adapter.py` | 27 | 18 | 13 |
| `tests/test_mcp_launcher.py` | 26 | 19 | 10 |
| `tests/test_provider_v2_config.py` | 25 | 15 | 15 |
| `tests/test_v2_comparator.py` | 23 | 18 | 6 |
| `tests/test_report.py` | 20 | 20 | 0 |
| `tests/test_scoring_hardening.py` | 18 | 18 | 0 |
| `tests/test_provider_contract.py` | 17 | 12 | 7 |
| `tests/test_v2_runtime.py` | 17 | 12 | 7 |
| `tests/test_codex_oauth.py` | 16 | 16 | 0 |
| `tests/test_bhce_health.py` | 15 | 15 | 0 |
| `tests/test_run_config.py` | 15 | 15 | 0 |
| `tests/test_v2_mcp_adapter.py` | 13 | 10 | 4 |
| `tests/test_v2_campaign_config.py` | 12 | 8 | 6 |
| `tests/test_v2_graph.py` | 11 | 11 | 0 |
| `tests/test_v2_model_card.py` | 11 | 10 | 2 |
| `tests/test_v2_process_acceptance.py` | 11 | 4 | 10 |
| `tests/test_graph.py` | 10 | 10 | 0 |
| `tests/test_relationships.py` | 10 | 8 | 3 |
| `tests/test_v2_task_recipes.py` | 10 | 7 | 5 |
| `tests/test_discovery_v2.py` | 9 | 9 | 0 |
| `tests/test_phase4_v2.py` | 9 | 9 | 0 |
| `tests/test_runner_infra.py` | 9 | 9 | 0 |
| `tests/test_serializer.py` | 9 | 9 | 0 |
| `tests/test_benchmark_cli.py` | 8 | 7 | 2 |
| `tests/test_phase4.py` | 8 | 8 | 0 |
| `tests/test_inspect_runtime.py` | 7 | 7 | 0 |
| `tests/test_offensive_ai_con_assets.py` | 7 | 4 | 4 |
| `tests/test_phase4b_diagnostics.py` | 7 | 7 | 0 |
| `tests/test_provider_auth.py` | 7 | 7 | 0 |
| `tests/test_v2_campaign_durability.py` | 7 | 7 | 0 |
| `tests/test_benchmark_profiles.py` | 6 | 6 | 0 |
| `tests/test_benchmarks.py` | 6 | 6 | 0 |
| `tests/test_v2_edge_validation.py` | 6 | 4 | 3 |
| `tests/test_complex_multihop.py` | 5 | 5 | 0 |
| `tests/test_preflight.py` | 5 | 5 | 0 |
| `tests/test_v2_identity.py` | 5 | 5 | 0 |
| `tests/test_v2_publication_acceptance.py` | 5 | 1 | 5 |
| `tests/test_telemetry.py` | 4 | 4 | 0 |
| `tests/test_v2_scoring_dimensions.py` | 4 | 4 | 0 |
| `tests/test_aliasfix_report_config.py` | 3 | 3 | 0 |
| `tests/test_offensive_ai_con_demo.py` | 3 | 3 | 0 |
| `tests/test_v2_selection.py` | 2 | 2 | 0 |
| `tests/test_complex_v1_freeze.py` | 1 | 1 | 0 |
| `tests/test_phase4b_failure_attribution.py` | 1 | 1 | 0 |
| `tests/test_v2_attempt_process_acceptance.py` | 1 | 1 | 0 |
| `tests/test_v2_expansion_matrix.py` | 1 | 1 | 0 |
| `tests/test_v2_regression_coverage.py` | 1 | 1 | 0 |

## Review findings and disposition

Independent reviewer `oaic_stage1_adversarial_fallback` found no blocking design
findings and verified both pre-edit copies and the implementation inventory.
Implementation is admitted only after the current baseline full run passes with
unchanged source. The review suggested the additional missing-task exception
probe above; it is accepted. This is not implementation approval of an untested
diff, nor closure of the whole-suite reduction or Stage 1.

The baseline full run passed 1,062 tests and eleven subtests in 293.91s with
unchanged source. Implementation then completed: independent review found no
blocking findings; 110 focused tests plus ten subtests passed in 1.34s. All 21
original assertions and lookups/loops were retained. Deep catalog values stayed
unchanged and the scenario generated its catalog exactly once. The first-helper
AssertionError and middle-helper StopIteration probes each reported one failed
and nine passing subtests. Removing keep-going in the negative control made the
strengthened continuation assertion fail as required. Collection is now 1,053.
Ruff, lock, diff and changed-file secret scans passed; validation start/end hashes
matched. Only the two approved test files changed in this slice. Full-suite
validation after this change and the wider reduction target remain open.
