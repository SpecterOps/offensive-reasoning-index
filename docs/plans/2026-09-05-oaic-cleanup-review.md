# P2 cleanup review and behavior ledger

Started September 5, 2026, from `afa9542`. This is an incremental review ledger,
not a claim that the entire codebase has been audited or P2 is complete. The
operator approved continuing development while deferring PR/merge. Publication
and final campaign admission still require reviewed, merged, certified code.

## Initial ownership and compatibility inventory

| Surface | Owner / entry points | Behavior that must remain |
| --- | --- | --- |
| Public CLI/config | `ori.cli:main`, `pyproject.toml`, model examples | Registered commands, aliases, options/defaults, strict config keys, path resolution and meaningful exit codes |
| Seeded products | `benchmarks.py`, generator profiles/templates/serializer | Named simple/complex identities, seeded graph/artifact bytes and planted relationships |
| V1 execution | `eval/runner.py`, adapters, grader, tasks, report | Direct/MCP operation, historical Phase3/4 reproduction, distinct failure attribution |
| Provider/MCP transport | `provider_auth.py`, `provider_contract.py`, `mcp_runtime.py`, launcher | Endpoint-scoped credentials, native loops, prompt discovery, read-only admission and provenance |
| V2 compiler/proofs | compiler, recipes, fixtures, identity, evidence, comparator | Public claim/oracle agreement, exact semantic contracts and failure-closed certification |
| V2 campaign | config, runner, runtime, status, supervisor | Zero-provider readiness, explicit execution, locks, durable attempts, bounded retries, exact resume and graph-gated publication |
| Discovery | registered discovery CLI/compiler/grader/preflight | Existing discovery contracts and compiler provenance |
| Reporting/scripts | V1 reports, V2 model cards, supervisor and historical helpers | Public redaction, separate tracks, stable wrappers and supported historical analyses |
| Tests | test owners and regression coverage ledger | Adversarial case isolation, named failures, immutable shared setup and documented ownership of historical defects |

Inspection found real imports for all twelve declared runtime dependencies,
including lazy provider imports. No dependency removal is justified by this pass.
Phase4V1/V2 dispatch, V1 runner modules, discovery commands and historical reporting
helpers remain reachable. Age and naming are not evidence of dead code.

An additional independent read-only review covered `eval/provider_auth.py`,
`eval/provider_contract.py`, `mcp_launcher.py` and their targeted provider/launcher
tests. It found no actionable issues in endpoint-scoped credential selection,
the MCP child-environment allowlist, strict malformed/unsupported configuration
handling or the inspected portable launcher paths. No dependency or private-host
coupling removal was justified in that scope. This is code-review evidence, not
live provider compatibility, all-three-MCP qualification or clean-machine proof.

## Batch 1: seven unreachable private helpers

The source review combined tracked-file identifier searches, Python definition
inspection, caller-closure checks, dynamic-dispatch searches, exports and command
registration checks. Six helper identifiers occurred only at their definitions;
the seventh was called only by another dead helper in this batch.

| File | Removed private helper | Retained active behavior |
| --- | --- | --- |
| `generator/templates/phase4.py` | `_first_dc` | Existing template-specific computer selectors |
| `eval/mcp_runtime.py` | `_load_bloodhound_mcp_prompt` | `_discover_bloodhound_mcp_prompt` and prompt rendering |
| `eval/v2/model_runtime.py` | `_contains_object_candidate` | Strict JSON parser/extractor and output-compliance handling |
| `eval/v2/model_runtime.py` | `_object_id_order_aliases`, `_identity_projection_aliases` | Scope-aware identity origins and ordering proof |
| `eval/v2/model_runtime.py` | `_query_covers_public_negative_scope` | Active shared negative-scope classification |
| `eval/v2/model_runtime.py` | `_query_projects_identity` | Returned identity-variable projection |

This removes 107 production lines. It removes no supported command, public
adapter, dependency or historical reproducer. External imports of undocumented
private helpers are not a supported compatibility surface; repository-internal
reachability evidence does not claim knowledge of arbitrary external callers.

A structural comparison parsed each affected module before and after cleanup,
removed only the approved definitions from the baseline tree, and asserted that
every retained syntax-tree node was identical. Independent manual review of the
diff and call boundaries found no actionable issues.

## Test obligations and count accounting

### Compiler/schema/report discovery (subsequently addressed by S1-03C)

An independent explorer audited `test_v2_compiler.py`, `test_v2_schema.py` and
`test_report.py` after S1-03B. Static expansion estimates are 49/29/20 cases
respectively, not a fresh per-module collection receipt. The highest-value
candidate is fixture reuse: `_perfect_answers` repeats certification despite
existing module-certified corpora. The audit identifies 443 repeated
`offline_certify` calls across complex schema, simple Direct/MCP answer and
fixture-exemption checks. A future plan must verify exact task/certification
binding and return fresh mutable answer dictionaries for every caller; it must
not cache mutated answer payloads or remove scoring executions.

Three possible lifecycle merges offer only three fewer cases: compiler scoring
through publication, report tier coverage, and successful-response thinking plus
telemetry export. The schema negatives are distinct and should remain independent.
These candidates require a new bounded plan and adversarial review before edits.

Four coverage findings must be resolved in that plan rather than concealed by
consolidation: MCP classifications keyed only by task ID can overwrite occurrences
across products; the oracle-sentinel test never sends its modified sealed object
through the tested export boundary; comparison-table tests do not bind numeric
counts to the intended row/column; and a test named for V2 or unknown manifests
tests only the V3 case. Preserve existing obligations while adding the missing
failure controls. These are test-coverage findings, not established production
defects or permission to alter benchmark behavior.

The preceding paragraphs retain the discovery-time proposal and qualifications.
S1-03C subsequently planned, reviewed, implemented and validated those test-only
changes; its accepted evidence follows. No schema-negative cases were removed.

### MCP validation optimization investigation — rejected for this cleanup

An independent audit traced repeated MCP profile validation within `mcp.py`:
public binding classification invokes three full validations through nested
lookup/support calls; initial finalization invokes four; observation classification
can invoke five; conformance construction repeats initialization four times per
loop. This explains the profiled hotspot structurally but is not a new timing
measurement or an approved optimization.

A future S1-05 plan may retain full validation at every public entry and use
private synchronous helpers internally. It must define exact built-in-model
eligibility and preserve fallback behavior for custom inputs or overridable
callbacks, exception ordering, foreign ToolCapability support behavior, stale and
forged profiles, and independently validated successive calls. No global cache,
identity-based trust, skip-validation flag, or validation carried across await/
provider boundaries is approved. Frozen Pydantic models are not proof of deep
immutability. Any change to `mcp.py` changes finalization/certifier fingerprints
and requires later recertification; historical receipts must not be reused.

Disposition, September 5: independent adversarial reviewer
`oaic_stage1_adversarial_fallback` recommends retaining the current checks. Root
reproduced the concrete compatibility counterexample with the unchanged runtime:
an accepted `EvidenceEvent` subclass overrides `model_copy` and changes a private
deep copy of the capability profile during ignored-event processing. The next
initializer correctly raises `MCP capability profile fingerprint mismatch`.
Reusing the earlier initial state would omit that rejection. The shared pinned
profile remained valid after the experiment. No production or test file changed.

Do not implement validation elimination or initial-state reuse as part of this
behavior-preserving slice. The earlier helper proposal above is discovery history,
not approval. Establishing a new transitively immutable input boundary would be a
separate compatibility/API change, not routine cleanup. Root owns this disposition;
the next performance candidate needs its own detailed plan, independent review,
controlled measurement and equivalence evidence. Stage 1 remains open.

### Accepted and in-validation reductions

Historical accepted collection was 1,038 after
[S1-03C](2026-09-05-oaic-compiler-report-tests-plan.md). Full regression passed
1,038 tests plus 74 subtests in 249.06s. All 223 original compiler assertion ASTs
and 17 merged report assertions remain. Real marked-oracle export, product-aware
classification accounting, exact error-column cells and certified-answer isolation
now have executable coverage. Independent implementation/evidence reviews and
all fault controls passed; all 158 source files matched the validation boundary.
Current corpora have no intersecting MCP task IDs: the collision test is explicitly
synthetic future-regression protection, not evidence of already lost classifications.

The 443 repeated certification calls were removed from answer/exemption setup;
the 176 real module certifications remain. On prebuilt complex Direct/MCP inputs,
one warmup and five alternating measurement pairs gave old median 7.242s
(7.216–7.453s) and new median 0.329s (0.327–0.560s), with exact answer equality.
Environment and complete installed versions are attached privately. This measures
test answer construction only, excludes initial certification equally, and does
not establish a campaign or end-to-end suite speedup.

S1-03A reduced collection to 1,053 items. Its
[reviewed plan and complete baseline module inventory](2026-09-05-oaic-test-consolidation-plan.md)
maps ten former legacy-catalog test names to explicit subtests over one fresh
catalog. All 21 original static assertions, including the anchored-task loop,
remain unchanged. Repeated setup calls drop ten-to-one; nine collected items
are removed, not nine logical obligations. Supporting-edge mutation cases stay
isolated. The CLI keep-going test now places a successful profile after a failure
and asserts that it runs; this fixes a coverage gap, not production behavior.

Both independent plan and implementation reviews passed. Focused validation:
110 tests plus ten subtests in 1.34s. Early assertion and middle lookup exception
probes each preserve nine other subtest passes; a no-keep-going negative control
fails the strengthened continuation assertion. Deep-value, source hash, Ruff,
lock, diff and secret checks pass. The subsequent full suite (1,039 tests plus 40
subtests, 253.60s) includes both S1-03A and S1-03B.

S1-03B subsequently reduces collection to 1,039. Its
[reviewed serializer/generator plan](2026-09-05-oaic-generator-scenarios-plan.md)
consolidates 19 obligations into five scenarios with independent subtests; four
tests remain separate. All 73 original assertions and 17 loops/comprehensions
are preserved. Independent implementation review, deep graph/manifest/profile
immutability checks, nine isolated fault controls, 34 focused tests plus 19
subtests, collection, lint, lock, diff and secret checks passed. Full-suite
validation of both new slices passed: 1,039 tests plus 40 subtests in 253.60s.
All 158 implementation files matched the validation-start inventory afterward.

The same full-run timing inventory identified the three-seed expansion matrix
as the largest item (149.84s), followed by complex adapter fixture replay
(18.19s) and complex compiler setup (15.19s). These are observed durations, not
controlled speedup measurements. Preserve cross-seed coverage and investigate
its production work before proposing optimization. The wider audit currently
covers six high-volume modules in depth, not the whole repository.

Baseline collection: 1,067 items across approximately 867 test functions. First
batch removes only `test_generate_simple_alias_writes_seeded_artifacts`:

- Its obligations were successful named-simple alias execution and creation of
  both ZIP and manifest.
- Retained `test_simple_artifacts_ignore_wall_clock_but_vary_by_seed` exercises
  both CLI aliases, requires successful exit, reads both files, checks exact byte
  equality under different clocks and checks changed-seed variation.
- The detailed seed-1234 named-product artifact test remains.

Verified collection after this batch: 1,066 passing items. This is genuine duplicate
removal, not achievement of the fewer-than-700 target. No parametrized matrix was
converted into an opaque loop, and no adversarial case was removed.

The next test-review candidates are coherent generated-graph/paired-track
scenarios in `test_complex_multihop.py` and neutral support modules for fixtures
currently imported from other test modules. Preserve every assertion and named
subcase; keep deliberately corrupted archives independently isolated. The eight
historical defect mappings in `test_v2_regression_coverage.py` bind exact test
names and must be updated explicitly if any of those owners change.

Do not delete the three-seed expansion matrix as duplicated compiler coverage:
it additionally proves cross-seed semantics, determinism, recipes and projection
accounting. Approximately 200 parametrized additions are not a legitimate shortcut
to the target; collapsing them would weaken isolation without reducing obligations.

## Batch 2: shared test support and generated-graph scenarios

Follow-up support extraction moved `_generated_product` and `simple_compiled`
verbatim into `tests/support/v2_compiler.py`. Publication acceptance no longer
imports a collected compiler test module. All 52 original definitions retain
identical syntax trees, and fixture-manager inspection confirms independent
module-scoped fixture instances for compiler and publication tests. The focused
pair passed 54 tests in 71.81s; Ruff passed. Fixture scope was not widened.

Direct adapter/runtime support now lives in `tests/support/v2_direct.py` instead
of a collected Direct test module. Fourteen shared definitions were moved without
AST changes; all retained Direct and model-runtime functions/classes are also
byte-identical to the baseline. The subprocess acceptance script imports the
neutral support module. A temporary file-reconstruction error was caught during
review, repaired from verified baseline content, and excluded from acceptance;
root independently compared every top-level definition and assigned fixture
before accepting the fresh 172-test pass in 22.80s. No production code changed
in this follow-up. Relative imports from collected MCP adapter tests were retained
at this checkpoint and subsequently addressed by reviewed S1-02 below.

S1-02 used the new stage planning/challenge/revision/implementation cycle. Its
independent reviewer verified pre-edit byte copies and a complete dirty-source
inventory before admitting the extraction. Twelve MCP fixture definitions moved
unchanged into `tests/support/v2_mcp.py`; five consumer import statements now use
that neutral module. The independent implementation review found no blocking
findings. All 161 focused tests passed in 12.76s; collection remains 1,062.
Ruff, diff, secret and start/end source inventory checks passed. No collected-test
imports remain in the audited Python AST/embedded import strings. Production
source did not change. The full 1,062-test plus eleven-subtest pass in 292.78s
was completed before S1-02, so final stage validation must cover this new slice.

A whole-suite exact-body scan found matching assertions in four Direct safety
parameter matrices; their inputs cover different behaviors, so matching bodies
alone did not justify removal. Two identical `hasspn` query cases did recur in
the broad admission matrix and dedicated scalar-filter matrix. Only the broad
copies were removed. Both inputs remain byte-identical in the dedicated matrix,
with the same fresh policy setup and `allowed is True` assertion; all function
bodies are unchanged. Safety plus operations tests passed 114 cases in 1.46s
(116 before removing these two duplicate executions). This is a two-item
reduction, not achievement of the fewer-than-700 target.

Combined follow-up validation: 340 focused tests passed, full collection is
1,062 items, repository-wide Ruff and diff checks passed, and support-module
secret scanning found no leaks. The prior 1,064-test full-suite result predates
this follow-up; no new full-suite pass is claimed here.

Campaign status, process recovery, publication and attempt-recovery tests now
import synthetic campaign builders from `tests/support/v2_campaign.py`, not from
a collected test module. All twelve builder functions and six fingerprint
constants were moved verbatim. Status test bodies remain identical, including
their assertions. The child-interpreter import was updated too. The four affected
modules passed all 53 tests in 36.67s. Compiler and adapter test-module imports
outside this extraction remain follow-up work; this is not a claim of complete
test-support separation.

The complex multihop module now has five collected scenarios instead of thirteen:

- Registry scenario: two explicitly named subtests over one template registry.
- Generated-graph scenario: seven explicitly named, read-only subtests over one
  graph, replacing seven identical graph constructions.
- Paired-track scenario: two explicitly named subtests over one graph/manifest.
- Valid archive and deliberately corrupted archive: separate unchanged tests.

All thirteen original logical obligations remain: eleven named subtests and two
archive tests. Structural comparison confirms every assertion/helper body is
identical after removing repeated setup; archive tests and corruption helpers are
unchanged. Native pytest subtests are available under the existing pytest >=9.0.3
development requirement; no dependency was added. Focused validation passed five
scenarios and eleven subtests in 0.24s. An in-memory mutation probe deliberately
failed the first graph check: the failure was reported and all six later named
checks still passed. The probe modified no source file.

The same five scenarios and eleven subtests also passed with third-party plugin
autoload disabled. The independent review's initial dependency concern was
checked against the existing requirement and pytest's
[core subtest documentation](https://docs.pytest.org/en/stable/how-to/subtests.html):
the fixture has been built in since pytest 9.0, so no separate plugin is required.
Subtests cannot be selected individually by CLI node ID; rerun their owning
scenario instead. Independent adversarial matrices retain their individual IDs.
After checking that evidence, independent review withdrew the dependency finding
and returned no actionable findings for this batch.

This reduces repeated graph construction from eleven calls to four in this
module. It reduces collected scenario count by eight, not logical coverage by
eight; it is not achievement of the suite-size goal or a measured speedup.

Full integrated validation: 1,058 tests and eleven subtests passed in 253.71s.
Ruff, lock validation, whitespace checks and secret scans of the test diff,
support module and plan documents passed. These are offline regression results,
not fresh live certification or final campaign admission.

## Batch 3: measured compiler edge-index reuse

A CPU profile of the complete complex Direct/MCP fixture adapter replay passed
its test and identified 308 `GraphSnapshot.edge_keys` constructions inside 130
compiler validation calls. The profiled run took 103.25s; instrumentation overhead
makes this unsuitable as a normal-suite baseline. Canonical serialization was a
larger hotspot, but removing its deep copy was rejected after read-only review:
mutable Enum values, opaque model serializers and exclusion-generator ordering
make a blanket removal incompatible. No fingerprint serialization was changed.

The optimization computes the relationship-key set once per nonempty
`_assert_edges_exist` call, instead of once per edge. Empty input still performs
no index construction. No cross-call cache, weaker membership rule, schema change
or new dependency was introduced. Existing graph-fingerprint verification and
case-sensitive source/type/target membership remain unchanged.

The measurement replays the actual calls captured while compiling both complex
seed-4401 tracks over the 60,342-relationship graph. It includes one warmup per
variant, five alternating-order measurements per variant, and a separate traced
allocation measurement. There are 130 calls, 308 edge checks and 77 nonempty calls.

| Measurement | Baseline | Once-per-call candidate |
| --- | --- | --- |
| Five elapsed samples (seconds) | 4.729, 4.816, 4.709, 4.695, 4.675 | 1.317, 1.172, 1.171, 1.172, 1.264 |
| Median elapsed (seconds) | 4.709 | 1.172 |
| Traced peak allocation (bytes), separate run | 5,959,832 | 5,959,768 |

Every candidate sample was faster than every baseline sample in this isolated
workload. The roughly 75% median reduction applies only to these edge checks,
not full compilation, certification or campaigns. Traced allocation is not total
process RSS. Broader runtime and memory qualification remains open under P2-W06.
The private reproduction script and CPU profile remain in ignored results.

The first four regression cases protect empty input, one index per call, fresh lookup
on the next call, exact direction/type case/target matching, and duplicate missing
edge ordering/text. The reuse assertion failed on the baseline and passed after
the change; all three negative-membership cases passed before and after. Complete
compiled corpora for all 46 Direct and 70 MCP tasks are identical when replaying
the baseline and candidate helper under the same current source provenance.
Independent review found no actionable issues.

The first candidate passed 1,062 tests plus eleven subtests in 234.02s, but root
review then found that its `if not edges` shortcut newly accepted `None`. That
candidate is superseded. The final implementation builds the index lazily on the
first iterated edge, without consulting input truthiness or length. Two additional
regressions protect invalid-None rejection, empty-iterator laziness and iterable
inputs whose truthiness/length cannot be queried. All six focused tests pass.
The measurement table above records the final lazy-index variant, remeasured
with the same warmup/five-sample procedure. Independent re-review found no
actionable issues. Final complete-corpus differential replay again matched all
46 Direct and 70 MCP tasks. The final full suite passed 1,064 tests plus eleven
subtests in 233.88s; Ruff, lock, whitespace and secret checks passed. Source/wheel
builds passed, and the wheel contains the exact updated compiler without test
support modules. The full-suite time is validation, not a five-run end-to-end
performance comparison.

## Evidence migration

### S1-03D runtime task preparation — complete

Independent review rejected the 46-test wrapper consolidation as collection-only
repackaging. It was not implemented. The revised
[plan](2026-09-05-oaic-runtime-receipt-scenarios-plan.md) removes twelve repeated
task-construction expressions in favor of three fresh-task factories, reducing
96 lines without changing any collected test, assertion, query or production code.
The twelve expanded function ASTs are identical to the baseline; all other 103
definitions remain byte-identical. All 173 focused IDs and their order remain.

Thirteen runtime equivalence cases verify complete task values, types, field sets,
fresh objects and bounds identity. Three fault controls fail exactly their six,
four and three mapped cases while unrelated selected tests continue. All baseline
production lines/branches remain covered. Independent plan and implementation
review found no remaining issues. Full regression passes 1,099 tests and 76
subtests in 241.00s, with Ruff, lock, diff and 160-file final source reconciliation.
No speedup, test-count reduction or renewed certification is claimed. The total
includes the earlier 61-case installed-package acceptance addition.

The discovery compiler/grader edge-key duplicate has a separately completed
[S1-04A plan](2026-09-05-oaic-discovery-key-plan.md). The grader imports the existing
compiler helper; the exact import/deletion AST change removes nine production
lines. All original tests remain, with three new regressions. Four complete
serialized scoring inputs/reports are identical before/after; the compiler
fingerprint is unchanged. Independent plan/snapshot/implementation/evidence review,
three semantic fault controls, twelve focused tests, Ruff, lock and diff checks
pass. Full regression passes 1,102 tests and 76 subtests in 251.25s, with all
160 source files unchanged during validation. No speedup or count reduction claim.
Comparator, Direct adapter and graph keys have different contracts and must not
be unified merely because their names are similar.

### S1-04B substantive publication review — audit pass complete

The [full ledger](2026-09-05-oaic-publication-review.md) traces production graph gates,
durable attempts, exact accounting, reports, completion receipts and model cards.
Independent review reconciled all 18 reviewed-file hashes, 160 implementation
files and 63 focused passing tests. Two P2 findings are independently reproduced:
operator-supplied unsafe labels reach public JSON/SVG, and model-card state binding
omits common metadata/provenance checks. No real private data was exposed and no
provider or graph was contacted. No new dead-code removal follows from this audit.

The completed [S1-04C fix](2026-09-05-oaic-model-card-binding-plan.md) addresses the
binding finding only. Independent plan, snapshot, implementation and final evidence
review passed on September 6. Full regression passes 1,108 tests and 176 subtests
in 252.19 seconds with the 160-file validation inventory unchanged. Original test
bodies remain intact; valid frozen card JSON/SVG bytes are identical and all five
root mismatch probes now reject. Label privacy and unresolved
graph-receipt/configured-run/public-string/recovery coverage remain open. Finishing
this source-review pass does not make the whole subsystem or Stage 1 complete.

### S1-04D public-string admission — bounded F01 complete

The [reviewed fix](2026-09-06-oaic-public-label-plan.md) checks all card strings
before rendering, preserving supported namespace/tag/Unicode names and existing
reference schemas. Explicit URL/path/control/credential patterns fail without
rewriting identities or input evidence. Independent final review passed the
57 new obligations, both fault controls, unchanged frozen default/display output
pairs and full 1,111-test/233-subtest regression (250.65 seconds). Source inventory
remained unchanged during validation. This is bounded syntax admission, not
universal privacy detection. Broader publication obligations remain open.

### Existing fingerprint migration

Source-byte fingerprints intentionally change even for unreachable-code removal.
No fingerprint exclusion, schema relaxation or stale-receipt reuse was added.

| Fingerprint | Qualified pre-cleanup value | First cleanup value |
| --- | --- | --- |
| Certifier | `fa6bf42b336cbebbb92d546cbf53a7e6efd3b93cceadba6f97d7228dab0962f4` | `0274e4af26e77ab00d0a097f9f45162c964d3a784664f68ba9b9bedd94d5b90b` |
| MCP finalization | `ad0aa8d848202bb69934eabc368b2816f5a751589ff6a99e33f769d9721a3d5d` | `1ca20a6e22f5686409ea25b6f6ef8ac893eff158a5f4681877788951e9076783` |

Keep P1 receipts as historical qualified-source evidence. Fresh compilation,
live certification, readiness and a fresh output root are required before a new
campaign. Final publishable evidence must bind to merged code.
The current validators were run against the actual P1 Direct and MCP offline/live
catalogs: all four were rejected explicitly because the certifier is stale.

Batch 3 also advances the source-bound compiler fingerprint from
`49d50f030db7cf32c61f54a4e2fc9288cfdd46f2595cf9a110d0ce740f3f4a4f` to
`06e5a098923ca3f3206bf53e797b74648250a614416ed78287dfdb637101f8c3`.
The differential corpus comparison deliberately holds source provenance constant
to isolate behavior; it does not authorize reuse of older compiled artifacts.
Fresh compilation/certification/readiness remain required for campaign use.

## Validation and remaining work

- Retained-module structural equivalence: passed for all three production files.
- Complex seed-4401 ZIP and manifest: byte-identical to the P1 artifacts.
- Ruff, lock validation, CLI startup and source/wheel builds: passed.
- Full updated suite: 1,066 passed in 271.17s. This single run is validation,
  not a controlled performance comparison against earlier runs.
- Independent manual review: no actionable findings.
- No model/provider calls, remote changes or graph mutations in this batch.
- Remote publication is deferred: the existing SSH signer failed during two fetch
  attempts. Work remains local on the consolidated branch; do not overwrite remote
  changes or weaken signing. Recheck remote state before the eventual signed push.

The compiler inventory probe measured 49 tests in 80.51s; complex fixture setup
and complete adapter replay were major contributors. This is a diagnostic lead,
not a before/after performance result. P2-W06 still requires one warmup and five
matched measurements, including memory, before claiming an optimization.

Remaining P2 work includes broader subsystem review, test-support separation,
coherent scenario consolidation, measured optimization, differential validation,
fresh certification and clean Linux/macOS installation qualification. Small
intentional public evidence-normalization wrappers remain: identical bodies do
not make their public interfaces redundant. The duplicated live discovery
`_edge_key` helpers are a later extraction candidate, not unreachable code.

## S1-05A provider audit and S1-05D destination correction

The bounded [provider audit](2026-09-06-oaic-provider-contract-review.md) passed
independent evidence review: four complete source files, five primary test modules,
72 obligation cells, 92 focused passes and an unchanged 160-file inventory.
Synthetic reproductions establish six findings without provider calls. No dead
provider branch has been proven removable. Full native-loop and V2 orchestration
reviews remain open.

The separately challenged [destination plan](2026-09-06-oaic-compatible-destination-plan.md)
implements configured-first compatible Direct precedence. Five new regression
cases failed at the expected mismatch before the fix; four control cases passed.
All 101 focused provider tests now pass. Full validation passes 1,120 tests and
233 subtests in 251.04s; the 160-file inventory is unchanged. Independent
implementation and final evidence review passed; only the explicit-versus-inline
portion of audit F01 is closed. All old-runtime V2 campaigns require fresh output/readiness,
even when their request behavior is unaffected by this correction.

## S1-05E scoped credential origin admission

The [approved plan](2026-09-06-oaic-scoped-origin-plan.md) is complete. Recognized
OpenRouter/Nous origins now require parsed HTTPS, absent/443 port and no userinfo
before receiving scoped keys; denial retains family and cannot borrow a generic
key. Existing generic endpoints and recognized host classification are preserved.
The production change remains in provider_auth.py; existing consumers reject
before client construction. No runtime API or outcome schema changed.

All 84 original test functions remain unchanged. The six new scenarios cover 186
obligations, with correct red failures and positive controls. Focused 144/186 and
full 1,126/419 test/subtest runs passed; Ruff, lock, diff and 161-file reconciliation
passed. Three first-case failure controls and three restored baselines prove exact
case continuation with no network attempts. Independent final review approved the
bounded F02 disposition. Codex/Anthropic, redirects, raw URL grammar, remaining
audit findings and broader Stage 1 remain open. Updated operator notes require
fresh V2 outputs/readiness and preserve the standalone/custom-server boundary.

## S1-05F internal adapter error attribution

The separately reviewed [plan](2026-09-06-oaic-provider-internal-error-plan.md)
is implemented. Actual SDK/HTTP exception classes preserve known provider failure
categories; unexpected internal exceptions propagate through a dedicated boundary
and become nonretryable harness failures in V2. Known Codex credential acquisition
and locked-SDK Anthropic header admission remain provider authentication failures.
Legacy serialization and MODEL_ERROR behavior are preserved. No public schema,
scorer or runner implementation changes were made in this slice.

Fifteen new tests cover 96 cases, including actual adapter-to-campaign persistence,
continuation and same-process resume, and MCP schema-retry transcript/evidence
preservation against independent frozen originals. Final focused validation passed
65 tests and 134 subtests after the copy-isolation refinement. All old top-level test functions
remain AST-identical. Independent implementation review found no production
blocker; its two acceptance findings were resolved.

Independent review also accepted eight process-local fault controls and eight
fresh-process baselines against the complete unchanged 163-file source inventory.
Controls prove exact failure locations, continuation, adapter-only admission,
cleanup, durable retry sensitivity and timeout ordering without network activity.
Earlier diagnostic controls with partial inventories are not accepted evidence.
Replacement full-suite validation passed 1,141 tests in 247.36s; its summary does
not report a separate subtest count. Source inventory and all control receipts
reconcile; Ruff, offline lock and diff checks pass. Independent final review
approved bounded S1-05F closure. The
superseded run was deliberately interrupted after a test-expectation refinement;
its 806 passes are diagnostic only. This does not close broader Stage 1 or F02.

## September 6 follow-up: deterministic consolidation candidate

Read-only audit of `test_v2_schema.py` and `test_v2_comparator.py` identified one
small justified setup-sharing candidate, not a whole-suite reduction plan.
Source-derived inventory: schema 25 named functions/29 expected collected items;
comparator 18 functions/23 expected items. These are not a fresh collection report.

Six schema functions build the same valid `_task_bundle()` eight times:
`test_task_bundle_rejects_duplicate_public_logical_roles`,
`test_task_bundle_requires_and_binds_solver_visible_acceptance`,
`test_task_bundle_rejects_policy_claim_mismatch`,
`test_public_task_bundle_has_no_oracle_fields`,
`test_public_task_bundle_rejects_oracle_fields_hidden_in_answer_schema`, and
`test_task_bundle_rejects_wrong_protocol_and_manifest_versions`.
One eight-subtest admission scenario could reuse one valid baseline while deep-
copying nested payloads before each mutation. Preserve seven rejection obligations
and the positive public-shape checks, exact error matches, both role entities,
all serialization assertions, and baseline deep equality. An early failure must
not prevent the remaining cells, and nested mutation must not affect later cases.
This removes seven redundant baseline/acceptance-spec constructions and reduces
six collected functions to one: five fewer items, not a measured speedup.

Comparator oracles differ materially in registry, context and policy. Retain the
existing organization; wrapper-only grouping would obscure contract boundaries
without meaningful setup reuse. This pair of modules cannot deliver the under-700
target. Broader deterministic setup audits remain necessary and are not blocked
by local-model availability or provider response-contract work.

No test edits are authorized by this candidate note. A fresh plan, independent
challenge, setup measurement, assertion map, isolation/continuation controls and
frozen validation are still required before implementation.

## September 6: indented collected-test imports must be restored

N's full-indentation import gate found seven pre-existing statements missed by
the earlier start-of-line-only search. Independent comparison with the N entry
snapshot confirms all seven predate N. The earlier broad no-collected-import
claim is not current truth. Preserve the failing gate, not an exception list.

Read-only dependency inventory identifies three extraction groups:

- Move the complete model-card campaign fixture closure (constants and builders
  through `_campaign`) into tests/support/v2_model_card.py.
  Update the model-card tests and the Anthropic/Gemini/Ollama consumers to import
  support directly; retain assertion-specific mutation helpers in the test file.
- Move the shared Anthropic synthetic SDK context and its binding/receiver/profile
  helpers into tests/support/anthropic_binding.py. Keep a thin local context wrapper
  and CURRENT_CASE ownership in the original collected module for its historical
  control hooks. Preserve existing reset semantics; do not redesign cleanup here.
  The three authentication-boundary imports then target support directly.
- Move Gemini's MODEL/config/resolved/provenance helper closure into
  tests/support/gemini_destination.py. Redirect tests import those helpers there.
  Keep Gemini and redirect per-case contexts/CURRENT_CASE local.

No production behavior, test assertion or test selector should change. The next
fresh plan must specify helper dependency closure, wrapper/control compatibility,
deep fixture/serialized-artifact equivalence, fresh support-import side effects,
an AST check including nested imports, and reruns of affected controls plus N on
one new frozen boundary. Do not relabel N's old receipts as evidence for changed
sources. This is restoration of test isolation, not a test-count reduction or an
excuse to remove independent negative cases.

S1-02B follow-through: extraction is now implemented and independently accepted.
The combined 179-file source passes 1,195 tests and 1,339 subtests, all 32 affected
fault-control processes, static nested-import isolation, and fresh-import traps.
All 54 existing test bodies and 21 moved helpers remain equivalent; six campaign
fixture variants are byte-identical under equal synthetic clock inputs. The old
failed N import receipt is retained. This closes this isolation finding only,
not the broader code-cleanup or test-count target.
