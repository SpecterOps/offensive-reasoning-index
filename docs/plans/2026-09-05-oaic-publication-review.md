# S1-04B — publication and model-card source review

September 5, 2026. Status: bounded source-review pass complete; root reproductions,
focused validation and independent full-ledger review passed. Unresolved rows
remain open and this is not complete subsystem assurance. The
[approved plan](2026-09-05-oaic-publication-review-plan.md) authorizes review, not
fixes. Root owns integration; `p2_dead_code_inventory` performed the source audit;
`oaic_stage1_adversarial_fallback` independently validated the two findings and
will review the complete ledger. Both findings are P2 correctness/privacy issues.

## Evidence scope

Base commit: `afa95428c966cb02972fc2a7998b0dd0b6e052a3`, with the existing consolidated
dirty changes. The complete 160-file implementation inventory matches the S1-04A
validation boundary, whose digest is
`54eff85d79e37a23006ebaa932186bc75ac25c9c7811681c61980f7a2cc0fe0e`.
No implementation file changed during this audit. Complete raw synthetic fixture
and reproduction output stays in ignored `results/p2-slices/s1-04b/`.

Complete source reads: `campaign_runner.py`, `scoring.py`, `model_card.py`,
`campaign.py` and `fingerprint.py` under `src/ori/eval/v2/`, plus
`scripts/build_v2_model_card.py`. Complete test reads: publication acceptance,
scoring dimensions, campaign durability, model card, campaign status and their
shared `tests/support/v2_campaign.py` support. The current review plan and campaign
supervisor contract were read completely.

Additional bounded closures: `campaign_status.py` artifact loading, schedule,
provenance/state/report and lifecycle/completion reconciliation; `graph.py`
LiveGraphVerification and exact-match gate; `model_runtime.py` private provider/tool
receipts, cancellation carrier and record construction; comparator verdict/reason
construction. These reads do not establish a full audit of provider loops, graph
canonicalization, certification/compiler, config validation, supervisor implementation,
comparator semantics or the entire repository.

The audit's initial inventory command mistakenly shadowed a shell system variable,
producing command-not-found pseudo-mismatches. That check was discarded and rerun
with a task-specific variable: zero mismatches. No environment or source changes
persisted. This is not an unexplained source discrepancy.

## Publication flow

```text
compiled pair + candidate/live certificate
  → prepared track + private provenance + resumable state
  → pre-track exact graph gate
  → durable per-model/repetition attempts and checkpoints
  → post-track exact graph gate
  → public run reports → track completion → completed lifecycle
  → model-card evidence reader → separate Direct/MCP summaries → JSON/SVG
```

## Source decisions

| Reviewed closure | Decision and limit |
| --- | --- |
| `_prepare_track` | Retain artifact/profile/certifier/candidate/live compatibility and selected-task membership checks. Certificate implementation remains another audit. |
| `_provenance`, `_guard_run_dir`, `_load_state` | Retain runtime/config/identity/archive binding and incompatible-resume rejection. |
| Attempt/state schemas | Retain contiguous attempts, last-attempt/checkpoint agreement, task-set reconciliation and self-fingerprints. |
| `_run_model` | Retain checkpoint-before-retry, lifetime attempt numbering, terminal outcomes and cancellation persistence. Integrated resumed-publication coverage remains incomplete. |
| `_run_operational_metrics` | Retain durable-attempt aggregation. `exhausted_infrastructure_tasks` means final infrastructure outcome, including nonretryable failures, not exclusively retry-budget exhaustion. |
| `_model_report` | Retain complete-scheduler requirement, exact selected result set and derived metrics. |
| `_track_completion`, `_publish_track_completion` | Retain report-before-receipt ordering and run/task count and identity checks. Caller supplies graph-gated receipts. |
| `_run_prepared_v2_campaign` | Retain independent executable pre/post gates per track. Earlier completed track survives later-track failure. |
| `_atomic_write`, lifecycle and lock machinery | Retain per-file flush/replace/directory-flush semantics and explicit lifecycle. Not a multi-file transaction. |
| `summarize_results` | Retain exact duplicate/missing/foreign result rejection and explicit denominators. |
| Campaign report/checkpoint builders | Retain exclusion of private detail, summary recomputation and full-compiled-binding compatibility with selected subsets. |
| `_assert_run_state_matches_report` | F02: identity/outcome/compliance/metrics checks omit common checkpoint metadata and provenance binding. Task-fingerprint cross-check also requires coverage. |
| `_load_track_reports` | Retain exact report/receipt set, graph/public/profile/candidate/live checks. Strengthen F02; provenance and actual graph-receipt files are not loaded. |
| `_select_model` | Retain common provider/model and matching run-index sets. Full configured repetition/model completeness is unproved without further config/provenance analysis. |
| `_aggregate` | Retain weighted counts and separate track catalogs. Combined operational workload is not combined accuracy. |
| `_public_label`, `build_model_card` | F01: label admission is incomplete; every emitted variable string needs explicit review. |
| `_svg_bytes` | Retain markup escaping; it prevents demonstrated injection, not disclosure. |
| CLI wrapper/main | Retain reachable entry point. No dead production entry point found. |

No deletion, helper consolidation or performance claim follows from this audit.
Status and model-card checks are not equivalent merely because their purposes
overlap: status already validates provenance relationships the card omits.

## Twelve contract-to-test decisions

| # | Evidence and decision | Missing coverage or boundary |
| --- | --- | --- |
| 1 Graph-gated publication | Normal execution covered by `test_campaign_publication_requires_each_independent_graph_gate` with no drift and drift at each of four gates. No source bypass found. | Resumed completed tasks, deferred retries and cancellation/resume need integrated graph-publication assertions. |
| 2 Evidence binding | Candidate-subset checkpoint test and `test_completed_campaign_validates_full_accounting` cover producer/status behavior. Reader behavior is contradictory: F02 reproduces three checkpoint/report mismatches. | Hash-corruption rejection is not proof of semantic cross-file checks. Provenance probe demonstrates an unchecked binding; the fixture has no provenance file. |
| 3 Exact task accounting | `summarize_results` rejects duplicate/missing/foreign results; extra-public-report and summary-disagreement card tests cover portions. | Add coherent rehashed missing/foreign result and task-fingerprint mismatch cases at the real JSON reader. |
| 4 Outcome dimensions | `test_summary_separates_reasoning_delivery_and_end_to_end_success`, ungradeable-model-failure and public-row-summary tests cover core dimensions. | Full taxonomy matrix is broader than these tests; do not claim complete outcome coverage. |
| 5 Attempt accounting | `test_status_usage_counts_every_durable_provider_attempt`, audited interruption and normalization tests cover portions. | No reviewed accepted-card scenario spans canceled attempts, immediate/deferred recovery and final nonretryable infrastructure. Provider cumulative usage is separate scope. |
| 6 Partial completion | Running/interrupted atomic-report-before-receipt tests, missing-completion and card lifecycle/track tests cover common partial states. | Every report→receipt→lifecycle fault point and two-track recovery need integrated coverage. |
| 7 Actual supporting evidence | Card validates models, selected private fields, summary and metrics, not just filenames. F02 remains confirmed. | Actual graph-receipt bodies are not loaded; positive fixture omits them. Decide a stronger requirement in a separate plan, without suggesting self-hashes authenticate execution. |
| 8 Model/repetition identity | Model mismatch/extra-report tests and source matching of run-index sets provide partial coverage. | Same slug across providers/configured variants, noncontiguous equal index sets and omitted configured runs require explicit decisions/tests. No mixture defect confirmed here. |
| 9 Public privacy | Absolute-path label and no-private-body tests cover limited cases. F01 confirms URL/relative/embedded path disclosure. | Inspect provider/model/product labels and raw MCP revision, plus public-report model identity. SVG escaping is not redaction. |
| 10 Determinism/recovery | Deterministic public-card and runner fsync/lock tests cover happy path and per-file writes. | Card writes SVG then JSON nonatomically. Current contract does not promise atomic pair publication; retain as documented limitation unless separately changed. |
| 11 Separate honest metrics | Separate-accounting and deterministic-card tests cover current Direct/MCP split and unavailable rates. | Future OAIC cost/TTFT/percentiles/partial accuracy/resources/local separation are future features, not regressions. Direct tool zeros need future N/A semantics. |
| 12 Test relevance | Mapping distinguishes producer, status and card tests. | Passing status tests do not prove card admission; mocked publication tests do not prove graph gates; synthetic fixtures are not certified campaigns. |

Exact current denominators: reasoning accuracy is correct/(correct+incorrect), or
unavailable when no reasoning verdicts; effective accuracy is correct/scheduled;
output compliance excludes unobserved outputs. Infrastructure, harness and
unexecuted samples invalidate eligibility. Proof/model failures reduce effective
accuracy without invented reasoning verdicts. Operational elapsed sums provider
attempt time, not total campaign wall-clock time.

Exact test selectors behind the abbreviated table descriptions (all under `tests/`):

- `test_v2_publication_acceptance.py::test_campaign_publication_requires_each_independent_graph_gate`
- `test_v2_scoring_dimensions.py::test_summary_separates_reasoning_delivery_and_end_to_end_success`
- `test_v2_scoring_dimensions.py::test_ungradeable_model_failure_cannot_forge_reasoning_incorrect`
- `test_v2_scoring_dimensions.py::test_normalization_is_only_valid_for_compliant_output`
- `test_v2_scoring_dimensions.py::test_public_rows_recompute_the_same_multidimensional_summary`
- `test_v2_campaign_status.py::test_candidate_subset_checkpoint_may_bind_full_compiled_inventory`
- `test_v2_campaign_status.py::test_completed_campaign_validates_full_accounting`
- `test_v2_campaign_status.py::test_status_usage_counts_every_durable_provider_attempt`
- `test_v2_campaign_status.py::test_running_projection_allows_atomic_report_before_track_receipt`
- `test_v2_campaign_status.py::test_interrupted_projection_allows_atomic_report_before_track_receipt`
- `test_v2_campaign_status.py::test_missing_completion_evidence_fails_closed`
- `test_v2_campaign_status.py::test_direct_and_mcp_accounting_is_separate_and_complete`
- `test_v2_campaign_durability.py::test_operator_interruption_is_audited_without_consuming_retry_budget`
- `test_v2_campaign_durability.py::test_atomic_write_fsyncs_file_and_parent_directory`
- `test_v2_campaign_durability.py::test_output_directory_lock_rejects_concurrent_resume`
- `test_v2_model_card.py::test_build_model_card_is_deterministic_separate_and_public_safe`
- `test_v2_model_card.py::test_build_model_card_rejects_extra_public_report`
- `test_v2_model_card.py::test_build_model_card_rejects_summary_that_disagrees_with_rows`
- `test_v2_model_card.py::test_build_model_card_rejects_incomplete_lifecycle`
- `test_v2_model_card.py::test_build_model_card_rejects_invalid_track_completion`
- `test_v2_model_card.py::test_build_model_card_rejects_mismatched_graph`
- `test_v2_model_card.py::test_build_model_card_rejects_mismatched_direct_mcp_models`
- `test_v2_model_card.py::test_build_model_card_rejects_local_path_in_public_label`
- `test_v2_model_card.py::test_model_card_rejects_output_compliance_state_mismatch`
- `test_v2_model_card.py::test_build_model_card_json_contains_no_rows_or_private_payloads`

## Confirmed findings

### F01 — incomplete public-label privacy filter (P2)

Disposition, September 6: bounded recognized-pattern admission is closed by
independently reviewed [S1-04D](2026-09-06-oaic-public-label-plan.md). All public
card strings cross a non-mutating pre-render check; 1,111 tests and 233 subtests
pass, with both valid frozen output pairs unchanged. Namespace ambiguity,
disguised content and the remaining audit obligations stay open. The following
records the original finding, not current behavior.

At `model_card.py` `_public_label` and `build_model_card`, whole absolute paths,
selected controls and certain credential-like patterns are rejected, but synthetic
URL, relative-path and embedded-absolute-path display labels reach both JSON and
SVG. Root reproduced the complete build with fresh synthetic fixture directories;
the independent reviewer confirmed source, output and source hashes.

This is operator-supplied-label disclosure, not automatic reading of private files,
not actual secret exposure in this audit, and not demonstrated SVG injection.
Follow-up scope: explicit public string admission across all emitted variable
strings, preserving slash-qualified model IDs and private identity semantics.
Reject unsafe ambiguous input; do not silently rewrite model identities. Define
recognized patterns without claiming universal secret detection.

### F02 — incomplete private-state evidence binding (P2)

Disposition, September 6: closed by independently reviewed
[S1-04C](2026-09-05-oaic-model-card-binding-plan.md). The following records the
original audit finding. Current admission requires and reconciles existing run
provenance; full regression passes 1,108 tests and 176 subtests. Valid frozen
card outputs are unchanged. F01 and the separately listed obligations remain open.

At `_assert_run_state_matches_report` and `_load_track_reports`, three individually
changed checkpoint graph/catalog/public-artifact fingerprints are accepted with
unchanged reports after recomputing nested unkeyed self-hashes and crossing the
real strict JSON loader. The fourth probe changes the opaque state provenance
fingerprint; its fixture contains no provenance artifact, so that case proves
the binding is unchecked, not contradiction against a file already loaded.

Follow-up scope: require the existing runner-produced `campaign-provenance-v2.json`,
validate its fingerprint and common fields against checkpoint/report/readiness,
and enforce result/task binding. Preserve semantic-subset schedules: all compiled
checkpoint bindings need not be scheduled. Existing consistent bundles remain
readable; missing/contradictory evidence fails closed. Do not invent defaults,
repair/re-hash inputs automatically or require current-code certification merely
to read a historical campaign. Status `_read_run` provides a reference, not an
automatic shared-helper extraction plan.

These are consistency checks, not signing/authenticity or tamper resistance against
coherent rewriting of an entire bundle. Actual graph-receipt loading, configured
repetition completeness and arbitrary revision strings remain explicit follow-up
decisions, not silently closed issues.

## Validation and remaining action

No source was edited by this audit. Root's reproduction scripts/JSON receipts
record exact source hashes and only synthetic cases. The source reviewer executed
no tests or services. Root ran all five complete test modules: 63 tests passed in
3.67s. The final inventory has zero changes across all 160 implementation files;
18 reviewed-file hashes and the complete test log are retained privately.
Independent review reconciled all 18 reviewed-file hashes, the complete 160-file
inventory, the 63-test receipt, cited source/test closures and both finding limits.
This closes the bounded audit pass, not its remediation or unreviewed obligations.
Passing these existing tests does not negate the independently reproduced gaps.
Both findings must be
resolved before claiming public-safe, evidence-bound model-card publication.
Any implementation needs a fresh detailed plan and independent challenge.
The completed [S1-04C binding fix](2026-09-05-oaic-model-card-binding-plan.md) covers
F02 separately; it does not close F01, actual graph-receipt admission, configured
run completeness, all public strings or integrated recovery/publication coverage.
