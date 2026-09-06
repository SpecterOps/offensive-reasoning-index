# S1-03B — serializer and seeded-generator scenarios

Revision 2, September 5, 2026. Status: bounded slice complete; independent review,
full regression and final hash reconciliation passed. Stage 1 remains open.
Parent: [Stage 1](2026-09-05-oaic-stage-1-cleanup-plan.md). Owner: root/test
engineering. Requested independent reviewer: `oaic_stage1_adversarial_fallback`.
Do not implement until reviewer assignment, findings disposition and pre-edit
snapshot verification are complete. No production or shared-schema change.

## Baseline and non-goals

Current suite collection is 1,053 after accepted S1-03A. This slice owns only
`tests/test_serializer.py`, `tests/test_phase4.py` and
`tests/test_benchmark_profiles.py`. Their current 23 cases become nine cases
through five explicit scenarios, retaining three existing independent tests and
one additional retained serializer planting test (detailed counts below).
The complete module counts, not prose arithmetic, are authoritative: 9→3,
8→4 and 6→2; total 23→9, whole suite 1,053→1,039. No assertion is deleted.

The read-only audit also examined graph, benchmark catalog and CLI modules. Keep
their existing independent cases: mutable graph rejection boundaries, separate
CLI surfaces/defaults/overrides, and cheap catalog calls are not duplicates.
Benchmark CLI has seven functions and eight collected cases; do not confuse the
two counts. Other proposed V2 scenarios remain separate unimplemented slices.

## A. Serializer artifact contracts (nine cases to three)

Keep `sample_graph` function-scoped and unchanged, including its existing fixture
dependencies. No graph escapes one scenario. Keep
`test_planted_paths_are_in_manifest` unchanged and independently collected.

Create `test_serializer_zip_contract(sample_graph, tmp_path, subtests)`:
serialize once to `tmp_path / "test.zip"`, retaining both returned path and
requested path. Two explicit named subtests preserve:

| Old test / subtest label | Retained check |
| --- | --- |
| test_serialize_to_zip_creates_file | Returned path exists and size is positive |
| test_zip_contains_expected_files | Requested ZIP opens and contains all five named files |

The ZIP open/read belongs inside its subtest so a failure in the existence check
does not prevent an independent attempted member check. Do not replace either
assertion with a generic nonempty archive check.

Create `test_serializer_directory_contract(sample_graph, tmp_path, subtests)`:
serialize once to `tmp_path / "output"`, then call six read-only helpers under
explicit named subtests in this order:

| Old test / subtest label | Retained inputs and assertions |
| --- | --- |
| test_json_meta_envelope | users file existence, data/meta, type/count/method/version |
| test_user_objects_have_required_fields | Per-user IDs/fields and all required properties |
| test_computer_objects_have_required_fields | Per-computer IDs/fields/name/isdc |
| test_group_members_reference_real_sids | Every member SID against actual graph, original diagnostics |
| test_user_names_are_uppercase_domain | Every user name and both uppercase components, original messages |
| test_domain_count | Exact count and exact graph domain SID |

Move bodies into `_assert_` helpers, removing only repeated path assignment and
serialization. Helpers receive the original `sample_graph` and shared `out_dir`
as needed. Each helper opens/parses its own files inside its subtest; do not
preparse all files in common setup, which would suppress independent failures.
All per-object loops and diagnostic messages remain identical. Generated graphs
drop nine→three, ZIP serialization two→one, directory serialization six→one.

## B. Phase4 generated products (eight cases to four)

All graph builds retain domain `PHASE4.TEST`, 18 users, seven workstations and
four servers. Introduce a local `_graph_for_seed(seed)` helper holding those
exact existing arguments. Retain `_manifest_for_seed` only if still called;
remove it only after a complete caller search proves the extraction made it dead.
Neither helper is a production API.

Create `test_phase4_seeded_artifact_contract(tmp_path, subtests)` with three
independent graph builds: seed 4401 twice and seed 4402 once. Assert the two
same-seed graph objects are distinct. Only graph construction belongs in shared
setup. Build each independently generated manifest inside the consuming subtest;
do not share manifest construction across the manifest-equality and changed-seed
checks. A manifest-construction failure must not prevent the ZIP check.
Named subtests preserve:

- `test_phase4_same_seed_manifest_is_identical`: equal independently generated manifests.
- `test_phase4_same_seed_zip_is_identical`: serialize each seed-4401 graph to
  its own first/second ZIP and compare exact bytes, not hashes of one shared ZIP.
- `test_phase4_different_seed_keeps_templates_but_varies_names`: equal ordered
  template lists and unequal ordered source-name lists for 4401 versus 4402.

Do not derive either comparison side by copying a graph or artifact. Keep ZIP
serialization and reads within the ZIP subtest; failure there must not suppress
the changed-seed check. Original equality/inequality expressions stay equivalent.
Graph builds in these three obligations drop six→three.

Create `test_phase4_generated_contract(tmp_path, subtests)` with one fresh 4401
graph. Keep three named read-only checks; construct a fresh manifest inside each
of the task-metadata and reference-query subtests, never in shared setup. A
manifest-construction failure must not prevent the independent ADCS check:

- `test_phase4_adcs_objects_serialize_to_sharphound_files`: serialize inside this
  subtest; retain all four ADCS filenames, type and subject-supply property check.
- `test_phase4_tasks_include_tier4_and_tier5_metadata`: generate tasks inside
  this subtest; retain exact three-template set, critical nodes and version checks.
- `test_phase4_reference_cypher_covers_privileged_targets`: retain both exact
  target/query inclusion assertions and RBCD critical-node count.

The two `test_run_config_generate_profile_*` CLI tests remain unchanged and
independently collected. Graph builds in the three generated-contract checks
drop three→one. A serializer or task-generation defect may fail its subtest but
must not mutate shared input or prevent later independent checks.

## C. Seeded profile matrix (six cases to two)

Create `test_seeded_profile_contract(subtests)` from four separate calls:
simple/1234 first and second, simple/5678, complex/1234. Do not reuse the same
object for both reproducibility inputs. Five named helpers retain all assertions:

- `test_seeded_benchmark_profile_is_reproducible`: object and metadata equality.
- `test_seeded_benchmark_profile_varies_by_seed`: object and exact identity/scale tuple inequality.
- `test_simple_profile_stays_in_small_domain_band`: all three ranges and uppercase domain.
- `test_complex_profile_stays_in_large_domain_band`: all three ranges and uppercase domain.
- `test_same_seed_differs_between_simple_and_complex`: both benchmark labels,
  distinct domain and smaller simple population.

Keep `test_timestamp_version_preserves_existing_identity_and_scale` unchanged:
its seed-67 frozen values and generator-version boundary are not covered by the
new matrix. Profile builds for the first five obligations drop eight→four.

## Isolation, snapshots and evidence

Save exact pre-edit copies of the three owned files and full implementation
inventory under ignored `results/p2-slices/s1-03b/`, using S1-02's snapshot schema.
Record current source, not only HEAD `afa9542`. Reviewer verifies those hashes
and no overlapping writer before edits. The single-seed diagnostic profile has
finished (122.613 seconds, private `results/p2-expansion-seed67.private.pstats`).
It did not modify production code and is not the full three-seed acceptance test.

Retain old names as subtest messages and record mappings in the cleanup ledger.
Use helpers to preserve original assertion/loop ASTs; changed variable bindings
must have an explicit old→new equivalence map. No whole-file formatter pass.

Audit graph inputs before and after serialization/manifest/task checks using
deep value snapshots of domain/seed/domain SID, node dataclasses, edge dataclasses,
planted paths, SID allocator state, DN state and RNG getstate. Compare complete
manifest/profile values too. Do not use only graph counts as an immutability test.
If any legitimate existing operation mutates these inputs, stop/review a fresh
input design rather than silently copying around a regression. Each scenario
uses fresh objects and function-local fixture scope.

## Required validation

1. Preserve every original assertion, loop and diagnostic; compare ASTs and
   manually account for changed setup variable bindings. Retained tests stay
   byte-identical. Verify unchanged historical regression owner mappings.
2. Setup-call spies confirm graph/profile/serialization reductions stated above.
   Report call counts only; do not infer a speedup from them.
3. Isolated pytest fault probes inject an early assertion failure in each new
   scenario. Each yields one named failure while later independent subtests run.
   For the directory missing-file probe, delete only `computers.json`: the third
   subtest fails and the other five pass, including the three later checks.
   Separately make `_build_manifest` raise in both Phase4 scenarios: the two
   manifest-consuming checks fail but the ZIP or ADCS check respectively still
   executes and passes. These probes expect two failures and one pass, not one
   failure, because both independent consumers intentionally construct manifests.
4. Determinism negative control corrupts only the second independently generated
   archive (in an isolated test process) and must fail the ZIP equality check.
5. `uv run pytest tests/test_serializer.py tests/test_phase4.py tests/test_benchmark_profiles.py tests/test_graph.py tests/test_benchmarks.py tests/test_benchmark_cli.py tests/test_v2_regression_coverage.py -q` passes.
6. Full collection is 1,039. No logical obligation is lost. Ruff/lock/diff and
   changed-file secret checks pass; validation start/end source hashes match.
7. Independent implementation review passes against the pre-edit snapshots and
   fault-control evidence. Full-suite validation remains required for stage exit.

Stop for missing assertions, shared-state mutation, identical-object determinism
comparisons, suppressed subtests, unreviewed source changes, unexpected counts,
or any scope expansion. A blocked or unavailable reviewer is not self-approval.

## Review disposition

The independent reviewer identified two findings in revision 1: shared manifest
setup could suppress independent artifact checks (P1), and an unspecified missing
file could fail multiple directory checks rather than exactly one (P2). Revision
2 moves manifest construction into each consumer and pins the missing-file probe
to `computers.json`, with explicit expected failure counts. The same independent
reviewer re-reviewed revision 2 and approved this bounded plan with both findings
resolved. Verified pre-edit snapshots remain required before implementation,
followed by independent implementation review and the validation above. This
approval does not claim the wider fewer-than-700 target is achieved.

## Implementation evidence

Snapshot review verified all three owned files and the complete 158-file source
inventory before implementation. A scoped test-engineering worker implemented
the three modules; root performed separate validation and the original independent
reviewer inspected the completed change. Neither production code nor shared
schemas changed. Only the three approved test modules differ from the slice's
pre-edit implementation inventory.

All 73 original static assertions remain (59 consolidated, 14 in retained tests).
Every assertion expression is identical except the explicitly approved manifest
equality binding; the new distinct-object assertion adds coverage. All 17 original
loop/comprehension ASTs remain identical. The fixture and four retained tests are
byte-identical. Each of the 19 consolidated checks maps from `test_<name>` to
`_assert_<name>`, with the original name retained as its subtest message.

Root's instrumented run passed nine tests and 19 subtests. Nineteen graph,
manifest and profile values retained their complete deep state, including RNG,
SID allocator and DN internals. Observed calls confirm three serializer fixtures,
one directory serialization, four Phase4 graph builds, six independent manifest
constructions and six profile builds (four scenario plus two retained regression).
Four ZIP calls comprise one serializer ZIP and three Phase4 artifacts. These are
setup reductions, not a claimed wall-clock speedup.

All nine isolated fault probes produced exactly their specified failed/passed
subtest counts and nonzero process exits. The second-ZIP probe changes only that
readable archive's comment bytes, causing byte equality to fail. Deep-state and
fault receipts are retained privately under the slice evidence directory.

The seven-module focused gate passed 34 tests plus 19 subtests in 1.50 seconds.
Collection is 1,039; repository Ruff, lock, diff and changed-file secret checks
passed. Independent implementation review found no blocking findings. The
validation source digest is
`a5efc34f10e9ed20f9ab988f569d393e5d40f2782a952cb7f45ad929f841ab8e`.
The full suite passed 1,039 tests and 40 subtests in 253.60 seconds. All 158
implementation files still matched the validation-start inventory afterward.
The three-seed expansion acceptance test ran in full and passed; its observed
125.23-second duration is not a controlled optimization measurement. This closes
S1-03B only, not the broader cleanup/count/portable-qualification stage.
