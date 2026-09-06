# S1-03C — certified fixture reuse and reporting coverage

Revision 4, September 5, 2026. Status: bounded slice complete; plan, implementation,
fault controls, measurement, full regression and source reconciliation passed.
Stage 1 remains open.
Owner: root/test engineering. Independent reviewer:
`oaic_stage1_adversarial_fallback`. Parent:
[Stage 1](2026-09-05-oaic-stage-1-cleanup-plan.md). Implementation is blocked
until review findings are resolved and exact pre-edit snapshots are verified.

## Scope, baseline and compatibility

Own `tests/test_v2_compiler.py`, `tests/test_report.py`, and the single CV1-005
owner-name update in `tests/test_v2_regression_coverage.py`. Root owns this
plan, validation tooling under ignored results, and ledger updates. No production,
shared schema, public documentation contract, dependency or provider change.
Keep existing support modules and module-scoped compiler/certifier fixtures.
`tests/test_v2_schema.py` was audited but is deliberately unchanged: its independent
typed-boundary negatives are not duplicates. No remote Git, signing, provider,
model-service or graph operation is authorized by this slice.

Starting source is the completed S1-03B inventory:
`a5efc34f10e9ed20f9ab988f569d393e5d40f2782a952cb7f45ad929f841ab8e`.
Full regression passed 1,039 tests and 40 subtests. Fresh collection verified
49 compiler and 20 report cases (69 total, 1.26 seconds). Record the full-suite
baseline again if source changes before snapshot verification.

This slice removes redundant fixture setup and repairs test coverage. It does
not claim production optimization, certification of the OAIC release or completion
of the fewer-than-700 target. Test changes must leave all production fingerprints
unchanged. Existing campaign evidence remains subject to the earlier production
changes' recertification requirement.

## 1. Reuse certification, never mutable answers

Retain `simple_certified` and `complex_certified` module fixtures unchanged. They
execute the real `offline_certify` once for every task: 60 and 116 respectively.
Do not replace them with fabricated statuses, lazy success records, or golden
answers. Preserve all separate real adapter-replay/live-catalog tests.

Add module-local `_certifications_for_corpus(corpus, certifications)`. It returns
an ordered tuple corresponding exactly to `corpus.tasks`. Build an index keyed
by `(task_id, task_fingerprint, oracle_fingerprint)` from the supplied immutable
certification results; reject duplicate full keys before dictionary insertion.
Additional entries for the other track are allowed because the existing fixture
contains both tracks. Every requested task must have exactly one matching result.
For each selected result assert, with explicit invariant messages:

- task ID, public fingerprint and oracle fingerprint match both certification
  and fixture manifest;
- graph/compiler fingerprints match the corpus;
- comparator and certifier fingerprints match current implementations;
- fixture comparator fingerprint also matches the current comparator, and both
  `certification_fingerprint` and `fixture_fingerprint` equal fresh
  `canonical_sha256` of their complete respective models, excluding only their
  own fingerprint field. Check these self-hashes before trusting case evidence;
  matching IDs/statuses alone do not prove body integrity;
- capability-profile fingerprint and bounds fingerprint match that task's
  actual track profile and public bounds;
- state is `OFFLINE_CERTIFIED`, failures are empty, and fixture coverage registry
  fingerprint matches the existing constant;
- exactly one `perfect` case exists, is applicable, has evidence, and has expected
  and actual status `CORRECT`.

Do not recompute certifications or mutate their models. Do not index solely by
task ID across products. Missing/duplicate/stale data fails an assertion; never
fall back to fresh certification or silently skip a task.

Change `_perfect_answers(corpus, snapshot)` to
`_perfect_answers(corpus, certifications)`. It uses the ordered selected results,
retains the existing evidence-to-public-answer conversion and schema-property
filter without changing any field or normalization, and deep-copies the returned
answer mapping. This copy covers nested answer containers, not only outer dicts.
Each call returns independently mutable JSON-compatible dictionaries.

Update every existing caller to request `simple_certified` or `complex_certified`
explicitly and pass the fixture, keeping its graph snapshot for artifact creation
where still used. Preserve every scoring invocation except the one explicitly
shared in section 3. Keep all three independently collected nonfinite cases and
the comparator monkeypatch isolated and unchanged.

The fixture-exemption test uses `_certifications_for_corpus(direct,
complex_certified)` for its coverage check and first fixture manifest. Preserve
the stale registry mutation and all current assertions. Do not add a second
certification for its first task.

The instrumented baseline confirms 443 certifications through these paths: 116 complex
schema answers, 120 across six simple-Direct callers, 160 simple-MCP strict-schema
and nonfinite inputs, and 47 fixture-exemption calls. All 12 selected cases passed
in 36.06 seconds under the call counter; the 158-file source inventory is unchanged.
Preserve this private receipt and repeat the counter after implementation. A standalone test may now
initialize a larger shared fixture, so do not promise every isolated test is faster.

### One new helper-lifecycle regression

Add `test_certified_answer_reuse_is_bound_and_isolated(simple_compiled,
simple_certified, subtests)`. Use the real simple corpora and fixture once. Explicit
named subtests (not an opaque parameter loop) exercise:

1. Two Direct answer calls are deeply equal but share no mutable dict/list nodes
   with each other or the actual source certification structures. Traverse the
   source models' real field values, not their detached `model_dump` output, when
   comparing object identities. Mutate the first answer's existing entities and
   existing edge-property dict containers;
   the second answer, original certification model dumps and a third fresh call
   remain unchanged. Also verify MCP fresh answers using the same fixture.
   Property values are `JsonScalar`; do not fabricate schema-invalid nested
   property leaves. Use real schema-valid evidence and its existing containers.
2. Missing selected certificate is rejected.
3. Duplicate full certificate key is rejected rather than overwritten.
4. Changed task fingerprint and changed oracle fingerprint are separately rejected.
5. Changed graph, compiler, comparator and certifier fingerprints are separately
   rejected even when task identity is unchanged.
6. Changed profile and bounds fingerprints are separately rejected.
7. Changed fixture binding/coverage registry, failed state/failures, absent perfect
   case and non-correct perfect case are separately rejected.
   Separately reject a stale certification self-hash, a stale fixture self-hash,
   an altered perfect-evidence body retaining its original hash, and a changed
   fixture comparator fingerprint. For semantic-field rejection probes recompute
   the relevant self-hash so the intended binding check is exercised; self-hash
   probes deliberately retain stale hashes. Assert invariant-specific messages.
8. A final fresh unmodified call equals the original answer mapping, proving the
   invalid-input probes did not poison shared state.

Construct each invalid variant with fresh `model_copy` objects, not mutation of
fixture internals; such intentionally bypassed model validation tests the helper's
boundary. Keep each rejection in its own named subtest so one accepted invalid
record cannot suppress another check. All checks reuse the same immutable inputs,
not answers modified by an earlier check. Deep-state comparison runs afterward.

## 2. Repair compiler coverage gaps

### Every MCP task occurrence

In `test_every_mcp_candidate_is_supported_by_the_pinned_capability_profile`, use
keys `(corpus.product, corpus.track, task.public.task_id)`, explicitly iterating
the simple and complex MCP corpora. Assert uniqueness before insertion and total
classified entries equal `len(simple_mcp.tasks) + len(complex_mcp.tasks)` (currently
110). Preserve the exact all-`CYPHER_ENABLED` assertion. Live materialization of
the local synthetic fixtures confirms zero current shared task IDs: compound
keys prevent future collisions; do not claim existing classifications were lost.

The isolated collision negative control deliberately copies the complex corpus
and its first public task, setting only that task's ID to the first simple MCP
task's ID. Supply the copied corpus only through that test item's fixture arguments,
without changing the real cached fixture. Stub the classifier for this aggregation
control: return `BLOCKED` for the original first simple task object and
`CYPHER_ENABLED` for every other object. The old task-ID-only dictionary must
silently overwrite the blocked result and pass; the new compound-key test must
retain 110 occurrences and fail its all-enabled assertion. This is a test of
classification accounting, not classifier semantics or real certification. The
ordinary unmodified test still exercises the real classifier across all 110 tasks.
Keep all synthetic copies/stubs isolated to a private pytest subprocess and never
reuse them in certification or campaign evidence.

### Real sealed-artifact sentinel boundary

Keep `test_oracle_sentinel_never_reaches_any_solver_visible_surface`, add
`tmp_path`, and retain all six surface-name checks and per-envelope sentinel checks.
Use the existing complex Direct task and sentinel. Create a new valid oracle with
that `oracle_id`, recompute its full oracle fingerprint with the normal exclusion,
and validate it as `OracleBundle`. Replace only that oracle in a copied compiled
task/corpus; never mutate the shared compiled fixture. Send this modified corpus
through real `write_artifacts` to distinct temporary public/private paths using
the complete snapshot identity catalog. Assert the returned private object and
written private bytes contain the sentinel, while returned public object and
written public bytes do not. Load the exact pair back with `load_v2_pair`.

Build all solver-visible envelopes from the matching loaded public task, not the
original task that bypassed the writer. Preserve the existing enum/name equality
checks and `assert_solver_visible` calls. Verify the loaded private oracle has
the sentinel ID and the source compiled corpus is unchanged. A private driver
spies on the real writer input to prove the marked oracle was consumed. A second
driver injects the sentinel into the public artifact output and must make the
test fail. Do not change production export behavior to manufacture a pass.

### Honest unsupported-version rejection

Rename `test_legacy_compiler_rejects_v2_or_unknown_manifests` to
`test_legacy_compiler_rejects_unsupported_manifest_versions` and parameterize its
existing body over `ori-generated-manifest-v3` and
`ori-generated-manifest-unknown`. Preserve the current exception type/message and
all graph/product/track inputs. The known generated-manifest-v2 input remains
covered by normal compilation. Check regression-owner mappings before renaming.

## 3. One compiler scoring-to-publication scenario

Combine `test_offline_scoring_uses_sealed_identity_catalog_and_shared_comparator`
and `test_v2_checkpoint_output_guard_and_public_report_are_exact_and_redacted`
into `test_offline_scoring_publication_lifecycle` with fresh simple Direct
artifacts, fresh perfect answers, and one real scoring run. Only artifact/answer/
scoring preparation is shared. Five explicit named subtests call helpers:

1. Original scoring and compliance assertions (all retained).
2. Build and validate checkpoint, including changed-run-identity rejection.
3. Build full public report and retain all redaction and row-count assertions.
4. Build scheduled-subset report and retain exact sorted selected IDs.
5. Build provenance/output guard, retain idempotence and incompatible-MCP rejection.

Checkpoint/report/provenance builders and their file reads belong inside their
own subtests. Failure in scoring assertions or checkpoint creation must not
suppress report or output-guard checks. Preserve the existing names as messages
for their corresponding obligations, adding unambiguous suffixes for split checks.
Retain assertion, exception and diagnostic expressions with an explicit input
binding map. One artifact/scoring preparation replaces two, without changing any
scored task or result assertion. Keep private oracle data out of public reports.
Update only CV1-005's mapped function name in the regression-ownership table to
this executable lifecycle; preserve the defect ID, module and all other mappings.
The ownership test discovers top-level test functions, so a helper/subtest name
alone cannot preserve its registration.

## 4. Reporting scenarios and exact table cells

Combine `test_write_summary_csv_includes_tier4_and_tier5` and
`test_write_summary_csv_reports_tier6_results` into
`test_write_summary_csv_tier_coverage`: one export for one model includes the
original tier4-correct, tier5-incorrect and tier6-correct inputs. Preserve tier6's
explicit `result.task.tier = 6` setup. Two named helpers retain all nine original
tier assertions; each opens/reads its own CSV inside the subtest. Other fields
whose aggregate values change are not asserted by either original test.

Combine `test_write_combined_csv_preserves_model_thinking` and
`test_write_combined_csv_includes_telemetry_columns` into
`test_write_combined_csv_response_details`: one fresh successful result carries
the exact original thinking and telemetry values and is exported once. Two named
helpers each read the CSV independently and retain row-count, exact text, numeric
rounding, token source, version, quantization and sample-reference assertions.
Keep the separate all-model test that verifies empty-thinking behavior unchanged.

Keep three comparison-column tests independently collected. Strengthen them with
a local `_comparison_row(out, model)` reader: locate the unique header beginning
`Model`, split its whitespace-delimited column names, locate the unique main row
whose first token equals the displayed short model name, and require equal field
counts before zipping. For existing `test:latest` input, assert exact target cell
`ModelErr`, `InfraErr` or `QExp` is `"1"`, the other two are `"0"`, and `Fails` is
`"1"`. Preserve heading assertions; replace the unbound `"1" in out` assertion
with stronger named-cell checks. Do not parse subordinate status/tool rows as
models or hardcode column offsets. A fault probe changing each target numeric
cell only (headings and unrelated `1` values unchanged) must fail its test.

No summary-health, provenance fallback, policy-rejection, executed-timeout, or
schema-negative test is removed or combined in this slice.

## 5. Validation, measurement and count accounting

Before edits, save current owned-file bytes and all 158 implementation hashes to
ignored `results/p2-slices/s1-03c/`, using the established snapshot schema; the
independent reviewer verifies them. Root ensures no overlapping writer. Record
post-edit and post-validation hashes and permit changes only to the two main
test modules and the one explicitly owned regression-table value. Do not copy
HEAD over prior approved edits.

Required checks:

1. Original assertion/loop/exception AST ledger with explicit allowed changes:
   lifecycle bindings, true artifact sentinel path, compound MCP keys and exact
   table-cell strengthening. Unrelated functions remain byte-identical. Existing
   public regression ownership stays covered after the one version-test rename.
2. Instrument original and new fixture paths to confirm removed certification
   calls; preserve all real module certification and adapter-replay work. Compare
   old/new perfect answers for simple and complex, both tracks, exact deep values.
3. Run the new reuse lifecycle and deep-state audit. Invalid variants do not
   mutate certified/compiled inputs or influence later callers.
4. Fault probes: early assertion and middle checkpoint exception in compiler
   lifecycle (later checks run), early helper failures in each report scenario,
   duplicate-ID classification failure, real marked-oracle writer input, injected
   public sentinel, and three incorrect numeric cells. Every negative control
   must fail for the intended condition, not fixture initialization or imports.
5. Measure old versus new answer construction on the same prebuilt complex Direct
   and MCP corpora and existing certificates: one warmup, then five alternating
   old/new pairs, with exact answer equality on every pair. Report medians and
   call counts; do not label focused/full-suite timing differences as controlled
   speedups. Do not reduce seeds or bypass schema/scoring to improve measurements.
6. `uv run pytest tests/test_v2_compiler.py tests/test_v2_schema.py tests/test_report.py tests/test_v2_regression_coverage.py tests/test_v2_publication_acceptance.py -q`.
7. Full collection should be 1,038: three two-to-one merges remove three cases;
   one reuse lifecycle and one extra unsupported-version case add two. Verify
   exact collection and retained logical obligations, not only arithmetic.
8. Repository Ruff, lock, diff and changed-file secret checks; full model-free
   regression; independent implementation review and final source reconciliation.

No success claim or stage closure before these gates. Stop the affected change
for wrong certificate binding, source drift, shared mutable answers, a lost
failure boundary, changed production fingerprint, unsupported sentinel setup,
misleading timing/count accounting or an unresolved independent review finding.
Read-only discovery of future production optimization remains independent.

## Review ledger

DA-C1 / P1: the reviewer identified missing self-hash and fixture-comparator checks.
Revision 2 specifies full canonical self-hash recomputation and separate forged
body/stale-hash/semantic-binding negative controls; the reviewer confirms resolution.

DA-C2 / P2: mutating newly injected returned data alone would not prove detachment
from source evidence. Revision 3 requires recursive mutable-container identity
checks against the actual source models and mutations of existing output
containers. The reviewer verified that property leaves are `JsonScalar` and
withdrew the initial suggestion to fabricate nested property values: the test
must use schema-valid evidence. The reviewer approved revision 3 with both findings
resolved. Pre-edit snapshot verification remains a separate gate before implementation.

The reviewer subsequently verified all three owned pre-edit copies, the complete
158-file inventory and both snapshot digests. The approved implementation began
with non-overlapping compiler/regression-table and report-test ownership. Root
owns separate measurement and fault controls. No production files are in scope.

Section 4 implementation passed its 18 report tests plus four subtests. Root
verified all 17 merged assertions and 16 unrelated function bodies against the
snapshot. Five isolated controls passed: first-helper failures preserve the other
scenario check, and changing each target comparison cell from 1 to 7 fails its
specific numeric assertion while headings and unrelated 1 values remain present.
Section 4 independent implementation review found no blocking findings and
verified that its fault receipt matches the current report-test source. Compiler
review and whole-slice validation remain pending; reporting approval is not
approval of partially validated compiler changes.

Revision-4 evidence correction: ordinary fixture materialization found 40 simple
and 70 complex MCP tasks with no intersecting IDs. The original collision-control
assumption was therefore inapplicable. The amendment above supplies a deliberately
synthetic collision, distinguishes the aggregation stub from real classification,
and requires an old-pass/new-fail counterexample. The reviewer approved this
amendment before control execution; no production or implementation scope expands.

Compiler implementation review found no blocking findings. The worker's focused
compiler/ownership gate passed 51 tests plus 30 subtests in 59.09 seconds; this
includes 25 explicit reuse checks and five publication lifecycle checks. Review
confirmed that only the three approved files changed and that CV1-005 alone
changes its registered owner. Validation-start source digest:
`d05298772626719c9f35a5097f98fd3f7b18648eee7ef80b78b61ad8279cd198`.
Root compiler fault controls, controlled measurements, full regression and final
source reconciliation remain open. The first measurement-driver invocation
failed before measurement because its private script directory omitted the
repository import root; the corrected invocation is running. That failed startup
is not performance evidence and changed no production or test source.

### Root acceptance evidence

- All 223 original compiler assertion ASTs remain, and CV1-005 has exactly the
  approved owner-name replacement. Report preservation evidence is recorded above.
- The five-module gate passed 103 tests and 34 subtests in 67.33 seconds.
- Six compiler controls passed: first assertion and checkpoint faults each leave
  four later/independent checks passing; the real writer receives the marked
  oracle; an injected public sentinel fails at the redaction assertion; the
  isolated collision passes against the old aggregation and fails against the
  new one while preserving the blocked occurrence. An initial old-snapshot
  import failure was excluded, corrected, and the actual control then executed.
- Instrumentation now observes only 176 retained module certifications (60 simple,
  116 complex), with zero per-answer/exemption recertifications. The targeted
  run passed 12 tests and 30 subtests. The old targeted run did not initialize
  these module fixtures, but the old full suite already did; this is call-accounting
  evidence, not a wall-clock comparison of unlike selected runs.
- Simple answer equality passed for 20 Direct and 40 MCP answers with certified
  inputs unchanged. Complex equality passed for all 46 Direct and 70 MCP answers
  on every controlled measurement sample.
- One warmup and five alternating pairs measured the complete complex answer
  construction helper on prebuilt/certified inputs. Old median 7.242s (range
  7.216–7.453s); new median 0.329s (range 0.327–0.560s). Real certification setup
  is excluded equally from these timings. The old helper performed 696 repeated
  certifications including warmup; the new helper performed zero. This is test
  answer-construction reuse, not campaign, production or full-suite speedup.
- Environment: macOS 26.6.2 arm64, CPython 3.12.13, uv 0.11.21. The measurement
  receipt links a hash-verified private environment receipt with the complete
  installed distribution versions and exact lock hash, captured afterward on the
  same checkout/runtime. All 158 source hashes match measurement/validation start.
- Independent evidence review found no new correctness blocker and requested the
  environment and spread qualifications now recorded here.

Final full regression passed 1,038 tests and 74 subtests in 249.06 seconds,
including the complete three-seed expansion test. All 158 implementation files
matched the validation-start inventory after completion. Repository Ruff, lock,
diff and changed-file secret checks passed. The net one-case reduction reflects
three scenario merges and two added cases, without deleting logical obligations.
This closes S1-03C, not the full audit, fewer-than-700 target, portable-machine
qualification or Stage 1.
