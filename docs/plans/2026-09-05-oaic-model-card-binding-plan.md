# S1-04C — bind model cards to existing private run provenance

Revision 3, September 5, 2026. Status (September 6): complete; independent final
implementation, regression and evidence review passed. Bounded F02 closure only.
Resolves S1-04B-F02, not
the separate label-privacy finding or all publication-review coverage gaps.

## Ownership and source boundary

Root owns production implementation, private equivalence/rejection probes and
documentation. `s1d_runtime_tests` owns only `tests/test_v2_model_card.py`: first
the fixture-only upgrade, then (after root captures the unchanged-production
baseline) the new tests. It must preserve other workers' changes; root does not
edit that test file concurrently. The test worker reports completion at each
phase boundary and does not start phase two before root's explicit handoff.
`oaic_stage1_adversarial_fallback` owns independent plan, snapshot and implementation
review. Only `src/ori/eval/v2/model_card.py`, `tests/test_v2_model_card.py` and
`docs/v2-campaign-supervisor-contract.md` are owned. Planning/progress documents
and ignored evidence are root-owned. Do not modify runner, status, shared schemas,
scoring, graph, provider, compiler or certification implementation.

Start from the current consolidated dirty tree at
`afa95428c966cb02972fc2a7998b0dd0b6e052a3`. Preserve every existing change. S1-04A
passed 1,102 tests and 76 subtests; S1-04B source audit, reproductions and 63 focused
tests are independently reviewed. No provider/model/graph/service operation,
dependency installation, remote Git, signing, PR or merge is authorized here.

## Correctness requirement and non-goals

A model card must not combine internally valid files that disagree about their
run or benchmark. Require the runner's already-existing per-run
`campaign-provenance-v2.json` beside each public report/private state, validate it
with `ModelRunProvenanceV2`, and reconcile the existing common fields below.
Unkeyed fingerprints provide consistency, not authenticity or protection against
coherent rewriting of the complete bundle.

Do not require current local runner/compiler/certifier source fingerprints when
reading otherwise supported historical evidence. Compare recorded values with
one another. Do not reconstruct missing provenance, invent defaults, update hashes
in operator input, call readiness, contact a provider or rerun a campaign.

Out of scope: label privacy, actual graph-receipt file admission, full configured
model/repetition completeness, new source-signing guarantees, launcher-path
normalization, provider attempt semantics, atomic two-file card writes and future
OAIC scorecard fields. Existing checks at those boundaries remain intact; the
review ledger keeps unresolved obligations open.

## Exact implementation

Import `ModelRunProvenanceV2` from the existing campaign_runner import group.
No new module dependency is introduced. Load provenance in `_load_track_reports`
after all existing per-run report/state/readiness validation and before the run is
admitted. Do not reuse `_load_model` for provenance: its error text exposes input
paths and validation values. Add `_load_run_provenance(path)` which reads bytes,
catches OSError as `ModelCardBuildError("cannot read model-run provenance") from
None`, and catches Pydantic ValidationError from `model_validate_json` as
`ModelCardBuildError("invalid model-run provenance") from None`. Never interpolate
the input path, payload or original exception. Missing, malformed, wrong-schema
and bad-self-fingerprint files fail before any output assets are written.
Existing report/state loader behavior is unchanged, not claimed newly sanitized.

Add one private helper `_assert_run_provenance_matches_evidence` receiving
provenance, report, state, readiness and the already-resolved track readiness.
It performs only the comparisons in this table, including the new local
checkpoint/row/scheduler checks. Keep both signature and body of existing
`_assert_run_state_matches_report(report, state)` unchanged.
Do not extract or change status-reader checks in this slice.

Freeze ordering: keep the initial report/state load loop and every existing
per-run validation in their current order. Record each run's sibling provenance
path in a separate local `provenance_paths` map keyed by the existing run key;
do not change `LoadedRun`. At the end of the per-run validation loop, after
existing target/Direct/MCP loop checks, load that provenance and call the new
helper. Thus `mcp graph fingerprint mismatch` and other existing failures still
precede the new checks. No later model selection, aggregation or output write can
occur before every admitted run crosses this new boundary.

| Comparison | Exact fields / rule |
| --- | --- |
| State→provenance | `state.provenance_fingerprint == provenance.provenance_fingerprint` |
| Run identity | Provenance run identity equals both checkpoint and model-report run identity, as complete typed objects |
| Checkpoint→provenance.base | `product`, `track`, `public_artifact_fingerprint`, `oracle_artifact_fingerprint`, `catalog_fingerprint`, `graph_fingerprint`, `compiler_fingerprint`, `comparator_fingerprint`, `capability_profile_fingerprint` all equal |
| Report body→checkpoint | `product`, `track`, `public_artifact_fingerprint`, `catalog_fingerprint`, `graph_fingerprint`, `capability_profile_fingerprint` all equal |
| Report body→provenance.base | Same six common fields all equal; explicit comparison or transitive equality through the checked checkpoint is acceptable |
| Release→provenance | Model report's candidate-release and live-certification fingerprints equal provenance; retain existing report→receipt→readiness comparisons |
| Provenance→readiness | Source manifest SHA-256 and archive SHA-256 equal global readiness; base graph equals readiness graph; run target equals readiness target |
| Provenance→track readiness | Base track/public/oracle/capability match; candidate and live fingerprints match; report schedule already matches receipt, and also must equal track readiness task_count |
| Provider request metadata→readiness | At least one readiness model record matches provenance provider/model plus requested/resolved API surface, structured-output mode, endpoint family and credential-source name. No new uniqueness/configured-name requirement; repetition completeness remains out of scope |
| Public rows→private results | Retain exact task-ID set and existing outcome/verdict/compliance/metrics checks; additionally require row task fingerprint equals matching private result task fingerprint and row product/track equals report body product/track |
| Completed private state | Require scheduler phase `complete`, mirroring the actual producer's `_model_report` admission rule |

Use field-name/category errors without embedding private values. Preserve existing
error messages for existing checks. New categories are: missing/invalid provenance,
state/provenance binding, run identity, checkpoint/provenance metadata,
report/checkpoint metadata, release provenance, readiness provenance,
provider-readiness metadata, row task binding and incomplete scheduler.
Tests match stable category phrases, not full private exception text.

No need to retain the full provenance in `LoadedRun` or emit a new public field:
the existing public state-file hash binds a state containing the verified
provenance fingerprint. Keep schema `ori-v30-model-card-v3`, output filenames,
JSON keys, metric equations and SVG rendering unchanged. Do not add private
provenance/credential/path data to output. Valid consistent frozen input files must
produce byte-identical JSON and SVG before and after this admission change.

## Compatibility and operator behavior

Consistent current-schema historical campaigns already have the required file
because `_guard_run_dir` writes it before execution. They remain readable without
current live services or certification. Previously copied/incomplete bundles
missing that file now fail closed; recover the original matching file from the
original campaign, not a fabricated reconstruction. Contradictory bundles require
investigation. This is intentional correction of an insufficient evidence check,
not removal of a supported scoring or campaign capability.

Preserve the distinction between full compiled checkpoint bindings and selected
release results: extra unused compiled bindings are allowed; the exact selected
results must match public rows and declared scheduled count. Do not require the
number of checkpoint bindings to equal the release size.

Document stricter card admission, missing-file recovery, unchanged schemas and
unkeyed-consistency limits in the supervisor contract. Keep historical stored
cards untouched. A passing card remains insufficient to claim a signed release
or merged/currently certified final conference campaign.

## Snapshot and staged baseline

Use `ori-cleanup-snapshot-v1` under ignored `results/p2-slices/s1-04c/` with complete
byte-verified snapshots of all three owned files, base commit, canonical record
and full 160-file implementation digests. Record full original test IDs and bodies.
The independent reviewer verifies the snapshot before any owned edit.

First upgrade only test fixtures to represent complete existing evidence:

- Add `_provenance(checkpoint)` returning a real validated ModelRunProvenanceV2.
  Its base is constructed from the nine checkpoint/base fields in the table,
  with current schema/protocol defaults and correct canonical fingerprints.
- Use source-manifest `3`×64 and archive `4`×64 to match existing readiness;
  request/resolved surface Chat Completions, prompt-local validation, endpoint
  family `nous`, credential source `NOUS_API_KEY`, and no launcher provenance,
  matching existing fixture metadata. Use non-secret deterministic fingerprints
  for containment/runtime implementation/runtime config; do not inspect credentials.
- `_state` binds its provenance fingerprint to that helper's returned artifact.
  `_campaign` writes the helper's provenance beside each written private state.
- Correct `_readiness`'s oracle fingerprint from `5`×64 to `b`×64, matching the
  existing checkpoint. This repairs a previously unchecked fixture contradiction;
  no assertion or expected score is removed.

Run all original model-card tests against unchanged production first. Retain a
complete frozen synthetic two-track bundle made with upgraded fixtures and build
baseline card outputs from it. Save input/output hashes privately. Subsequent
equivalence builds use those exact same input files, never freshly regenerated
timestamps or a changed fixture. They are synthetic, not certified evidence.

Then implement production comparisons and add the new tests. Existing test IDs
and assertion bodies remain, except fixture construction/import additions above.

## Exact regression scenarios

Add these tests without renaming existing tests:

1. `test_model_card_rejects_missing_or_invalid_provenance`: parameterize Direct/MCP;
   within each track use named subtests for missing file, malformed JSON,
   invalid root type, unsupported schema and incorrect self-hash. Each subtest gets a fresh campaign
   directory copied from immutable baseline fixture bytes and a fresh output path.
   Use unique synthetic input-path and invalid-payload sentinels. Assert both are
   absent from `str(error)` and `traceback.format_exception(error)`, and verify the
   same behavior through `python -m ori.eval.v2.model_card` on the malformed-JSON
   and invalid-root cases
   with captured stderr/nonzero exit. Exception chaining must not restore payload
   values. This promises removal of input-path/payload sentinels, not redaction of
   Python's own implementation-file traceback locations. Assert no output assets.
2. `test_model_card_rejects_mixed_run_evidence`: parameterize Direct/MCP; named
   subtests for every checkpoint/base field in the table, state provenance pointer,
   each complete run-identity field, release candidate/live fields, manifest/archive
   SHA, provider metadata fields, row task fingerprint/product/track, schedule count
   and scheduler phase. For checkpoint-only cases change checkpoint and state
   hashes; for provenance cases correctly update base/provenance/state pointers
   and hashes so failure reaches the semantic cross-file comparison. For row cases
   rehash the public report, model report, matching track receipt and lifecycle so
   no unrelated stale wrapper hash masks the mismatch. Cross the real file reader.
3. `test_model_card_accepts_full_compiled_bindings_for_selected_subset`: add one
   valid unused checkpoint binding, rehash state and preserve selected results;
   actual card build succeeds. It must still reject an added/foreign result without
   a corresponding public row and matching schedule (a named negative subtest).
   Parameterize both tracks and use named positive/negative subtests.

### Frozen mutation map

Run the following groups in table order for each track. Case IDs are the group
prefix plus field name, or the single name stated. Preserve this finite map in a
private JSON receipt before controls; independent review approves the map as part
of this plan and verifies its expanded receipt. `different_fp` is `7` repeated
64 times; assert it differs from the fixture's original value before each mutation.
`other_track` is the opposite valid Track value. All schema-only/default fields
not named remain untouched. Every semantic case requires each individually loaded
artifact to validate before calling the real model-card reader.

| Case group | Ordered fields and replacement | Hash/pointer handling |
| --- | --- | --- |
| `checkpoint.*` (9) | product=`other-product`; track=other_track; public_artifact_fingerprint, oracle_artifact_fingerprint, catalog_fingerprint, graph_fingerprint, compiler_fingerprint, comparator_fingerprint, capability_profile_fingerprint=different_fp | Rehash checkpoint and state only; original valid provenance remains |
| `provenance.base.*` (9) | Same ordered fields/values as preceding row | Rehash base, provenance; update state provenance pointer and rehash state; checkpoint values remain original |
| `state.provenance_fingerprint` | different_fp | Rehash state only; valid original provenance file remains |
| `provenance.run_identity.*` (5) | provider=`other-provider`; model=`other-model`; run_index=2; target_fingerprint=different_fp; tool_loop=`native-openai-compatible` for Direct or null for MCP | Rehash provenance, update state pointer and rehash state; checkpoint/report identities remain original |
| `provenance.release.*` (2) | candidate_release_fingerprint=`other-candidate`; live_certification_fingerprint=`other-live` | Rehash provenance and update state pointer/hash |
| `provenance.archive.*` (2) | source_manifest_sha256, archive_sha256=different_fp | Rehash provenance and update state pointer/hash |
| `provenance.provider.*` (5) | requested_api_surface=`responses`; resolved_api_surface=`responses`; structured_output_mode=`json_schema`; endpoint_family=`generic`; credential_source=null | Rehash provenance and update state pointer/hash; original readiness remains |
| `public_row.*` (3) | First row task_fingerprint=different_fp; product=`other-product`; track=other_track | Rehash nested public report, model report, matching receipt run reference, track receipt and matching lifecycle reference/hash |
| `readiness.task_count` | Selected track task_count=3 | Rehash readiness; receipt still schedules two |
| `state.scheduler_phase` | phase=`primary` with existing round zero/pending-empty fields | Rehash state; preserve terminal results |
| `provenance.unmatched_file` | Provenance source_manifest_sha256=different_fp | Rehash provenance only; state keeps original provenance pointer, proving an actual file mismatch |
| `readiness.split_metadata` | Replace matching readiness model with two records of same provider/model, names `split-one` and `split-two`; first differs only in requested_api_surface=`responses`, second only in endpoint_family=`generic` | Set model_count consistently and rehash readiness. Original provenance cannot match either complete record even though every individual field has some matching record |
| `report_body.catalog` | Report-body catalog_fingerprint=different_fp | Rehash nested public report/model report and receipt/lifecycle references; unchanged checkpoint/provenance agree with each other |
| `report_body.product` | Report body and every public row product=`other-product` | Rehash report wrappers/receipt/lifecycle; unchanged checkpoint/provenance agree with each other |
| `coherent_private.oracle` | Checkpoint and provenance.base oracle_artifact_fingerprint=different_fp | Rehash checkpoint/base/provenance/state and update state pointer; leave readiness oracle unchanged |

This is forty-three semantic mutations per track. The last three resolve the
independent review's masking finding: earlier pairwise agreement must succeed so
report/checkpoint catalog/product and private/readiness oracle comparisons are
actually exercised. The malformed-provenance scenario
adds five cases per track, in this order: remove the file; replace it with an
unterminated JSON object containing the unique payload sentinel; replace it with
a valid JSON string containing that sentinel; change schema_version to
`unsupported`; change only its provenance_fingerprint to different_fp. The first
two content cases exercise parser and structural-validation error disclosure;
run CLI checks for both. Do not silently change totals.

The compiled-subset positive adds a fresh binding `unused-compiled-task` with
task/oracle/bounds fingerprints `6`×64 and no result or attempt. The negative
reuses that valid binding and adds a copied result/attempt for its task with
matching new fingerprints, contiguous attempt number one, correct nested provider/
attempt/checkpoint/state hashes, but leaves public rows and schedule unchanged.
It must fail exact public/private accounting, not an invalid individual state.
Both scenarios start from pristine baseline bytes, not the preceding mutated case.

Expected new collection is six cases (three functions × two tracks), with 100
named obligations (86 semantic, ten invalid-provenance, four subset checks).
Verify actual collection instead of assuming these counts. Do not count the CLI
assertions as additional collected tests or hide any pre-existing case.

Every mutation must remain valid under its individual schema unless the subtest
explicitly targets malformed/schema/hash rejection. Use valid alternative enum
members, not arbitrary strings. Freeze the full case map in private evidence and
have the reviewer approve it before running mutations. No assertion of a specific
overall count until actual collection; report collected cases and named obligations
separately. Genuine baseline-fixture reuse does not remove any existing test.

Each subtest restores nothing into the shared baseline: deserialize/copy pristine
fixture bytes into a new directory. Validate all baseline hashes afterward. Add
a private early-subtest fault control proving later independent cases still run;
inject an unexpected setup error only in the first case and require exactly that
failure with the final named case observed. This prevents shared preparation from
silently masking later coverage.

Rerun the four original root state-binding reproductions against complete coherent
fixtures: the three checkpoint contradictions and unmatched state provenance
pointer must now raise the intended ModelCardBuildError without output assets.
Add an actual mismatched provenance-file case, not merely the earlier fixture with
no file. Keep every rejected synthetic input and receipt private.

## Exit gates

- Reviewed plan/snapshots; upgraded-fixture baseline passes original tests.
- Frozen complete valid bundle produces identical JSON/SVG bytes before/after.
- All negative cases reach intended semantic boundaries, including proper rehashing;
  named-case isolation control passes; immutable baseline remains unchanged.
- Existing model-card, publication, status, scoring-dimension and durability modules
  pass; independent implementation/evidence review passes.
- Full suite, Ruff, frozen-lock and diff checks pass; exact start/end source inventory
  matches. All unowned implementation bytes remain unchanged.
- Docs explain compatibility and limits; F02 closes only after verified evidence.
  F01 and remaining S1-04B obligations stay open. No whole-stage completion claim.

Stop on report drift for valid frozen input, rejection of legitimate selected
subsets, newly required live/current-source validation, private output, mutation
of input bundles, unrelated source changes or failed independent review. A design
change returns to plan review instead of being improvised during implementation.

## Execution evidence (September 6)

Independent review approved revision 3 and verified all three pre-edit copies,
the full 160-file inventory and the original 11 collected IDs. The upgraded
fixtures passed all 11 original cases against unchanged production (1.54 seconds).
Ten complete synthetic input files were frozen before production edits. Both
resulting JSON/SVG files remain byte-identical after the change, with all input
hashes unchanged. These fixtures are not certified campaign evidence.

Focused validation passed 17 collected tests and 100 named subtests (6.85 seconds).
The original ten function bodies and all 11 collected cases remain unchanged.
Independent production, documentation, test and expanded mutation-map review
passed. Five root reproductions now reject inconsistent evidence without writing
assets. The private first-case setup-fault control reaches all 43 semantic cases:
one intentional failed subtest, 42 passing later subtests and a final successful
immutable-baseline check. Pytest also marks the containing test failed, as expected;
that parent result is not a second independent subtest failure.

Full-suite validation passed 1,108 tests and 176 subtests in 252.19 seconds. All
160 source inventory entries match their validation-start hashes; all unowned
implementation entries match the pre-edit snapshot. Ruff, offline frozen-lock
and diff checks passed. Independent final review reconciled the full log/hash,
inventory, all 100 named obligations, fault control and frozen equivalence;
bounded F02 closure is approved. F01 label
privacy, actual graph-receipt admission and the remaining audit obligations stay
open. No model, provider, live graph or remote Git operations were performed.
