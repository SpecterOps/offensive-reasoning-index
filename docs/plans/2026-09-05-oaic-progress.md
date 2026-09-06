# OAIC progress and integration ledger

Updated: 2026-09-06. Contract: [execution plan](2026-09-05-oaic-execution-plan.md).
This ledger is public-safe; machine-specific paths and operational receipts remain
in private results. No provider calls have been authorized by a readiness result.

## Current state

### Active stop boundary: Stage 4

Local checkpoint commits are now authorized so the operator has recoverable
development points. No remote pushes, PRs or merges are authorized. A WIP
checkpoint preserves work; it is not a release or certification claim.

The operator corrected the stop point to Stage 4 and explicitly requested
MorDavid and Armadin native compatibility. Continue that integration, including
the unfinished graph/path work, then pause before Stage 5. The earlier Stage 3
handoff remains usable for the historical MCP lane, not native qualification.
Live graph certification and local-model configuration remain explicit
operator-side gates; no local or hosted model calls are authorized by this
handoff. A full regression started before the correction is still running;
preserve its result and scope rather than restart it.

### Capability-first reassessment (September 6 operator override)

The canonical execution strategy now supersedes the earlier micro-slice cadence.
The repair-accounting correction now passes focused regression, independent
source/test review, and batch-end full regression (1,198 tests and 1,371 subtests,
254.49 seconds). It records actual
repair transport activity even when scoring rejects the answer, without changing
scorer counters or public totals. Cleanup expansion is stopped. Do not add
unrelated cleanup gates before task-library implementation. Deferred external
operations do not block local development.

| Stage | Completed capability | Missing / actual blocker | Next deliverable |
| --- | --- | --- | --- |
| 0 Consolidation/V30 | Consolidated development branch; prior V30 qualification | Commits/PR/merge explicitly deferred; renewed release certification later | Preserve branch; integrate locally, publish only after authorization |
| 1 Cleanup | Provider boundaries, stream integrity, isolated support, portability, validated repair accounting | Nonblocking debt is backlog; no current batch blocker | Batch closed; task-library implementation next |
| 2 Local baseline | Portable explicit endpoint configuration | Operator artifact/runtime profile and authorized live qualification unavailable | Qualify externally provisioned local model when authorized |
| 3 Task library | Offline implementation accepted: OAIC graph/CLI, 100-main-candidate pools per track plus 10 diagnostics, deterministic 50/50 selection, paired admission, selected execution/resume/reporting | Real graph certification and local-model qualification remain unauthorized; merged publication deferred | Authorized live qualification later; next independent implementation is native MCP integration |
| 4 Native MCP/providers | Historical MCP/provider adapters plus native source registry, preserved discovery/session dispatch, initial evidence extraction and independent Bolt verification CLI | Native campaign/compiler/certifier integration, model-query containment and full task proof coverage still missing; runtime/dependency and live qualification deferred | Bind the native session core to backend verification and task-level certification, then runner admission |
| 5 Scorecard/budget | Separate track scores and basic durable attempt/token/tool metrics | Full dimensions, usage-knownness and campaign-wide dollar ledger absent | Implement auditable metrics and enforced budget admission/settlement |
| 6 Qualification/model lock | Existing no-provider readiness and resume boundaries | Depends on task/MCP/reporting integration; merge/live authorization deferred | Integrated offline qualification, then authorized local/model canaries |
| 7 Campaign | Existing durable runner | Frozen qualified merged release, model lock and spend approval required | Execute final matrix only after admission gates |
| 8 Evidence freeze | Existing redacted artifact/report machinery | Final valid campaign and reconciled cost/evidence missing | Freeze public-safe claim/evidence package by September 26 |
| 9 Demo/deck | Historical offline demo assets/runbooks | Updated final results/deck and two-machine rehearsal missing | Refresh deterministic offline package after report interface stabilizes |
| 10 Delivery | Schedule and talk outline | Final rehearsal/package and conference date ahead | Deliver unchanged technical package October 5 |
| OpenGraph design | Reviewed vendor-neutral framework design | Conference design freeze/documentation remains | Finalize design with talk; implementation stays post-conference |

The highest-impact task-expansion implementation batch is now delivered locally,
using P4's revised candidate quotas and unchanged 50/50 selection policy. Native
MCP integration is the next independent implementation capability. Local-model
qualification, merge and live certification remain later acceptance dependencies,
not reasons to postpone approved local development.

Task expansion now follows the operator-confirmed **100 Direct candidates and
100 MCP candidates**, with each release selecting **50 Direct + 50 MCP**.
Ten diagnostic contracts per track remain separate. Three-seed roster tests pass
for seed 67, 4401 and 4402 with unique semantics, bounded full populations and
public decision-subject identities. The expanded populations uncovered an
ambiguous-name positive-fixture defect; the reviewed correction uses object IDs
for ambiguous aliases without weakening actual answer resolution. OAIC admission
also checks current compiler freshness, and the public product list now exposes
the OAIC product alongside historical products.

Offline acceptance evidence: all 15 authoring tests passed across three seeds,
including the existing offline-certification gates for 110 contracts per track.
The real-artifact integration test passed selection, Direct-only paired admission,
omission/stale-artifact rejection, exactly 50 executed tasks, zero additional
dispatch on resume, a 50-task report denominator and 110 retained checkpoint
bindings. Full regression completed in 680.77 seconds: 1,223 tests and 1,376
subtests passed; one older accounting integration fixture failed in three
subcases because its test double omitted the new optional `selected_task_ids`
field. The fixture now explicitly uses the real default, `None`; no production
code or assertions changed after that full run. The affected accounting and
selection-admission modules then passed five tests and 37 subtests in 1.57
seconds. Ruff and whitespace checks pass. The full run was not repeated after
that test-only correction; this record preserves the actual results rather than
claiming a single all-green full run. Independent batch and integration reviews
have no remaining blocking findings.

These are offline implementation/fixture-replay results, not real live graph
certification, model qualification, merged-code publication or conference
completion. No real provider/model calls, graph changes, remote host operations, commits,
pushes or PR submissions occurred in this batch.
Operator workflow: [OAIC selected releases](../oaic-selected-release.md).

Native MCP source assessment resolved a material integration decision: neither
alternate uses the existing CE backend, and the historical loader cannot be
relabeled native because it substitutes CE-coordinator results. The execution
plan's P5 section now records exact inspected revisions, native surface counts,
backend isolation, missing resources, evidence limits and compatibility behavior.
Independent adversarial review identified hidden MorDavid database fallback,
Bolt-specific containment and per-task native proof feasibility as admission
requirements. These are incorporated into the batch boundary. Source inspection
does not prove runtime discovery, dependency reproducibility or live graph parity.

The source-assessment continuation resolved those architecture constraints and
updated the existing plan without changing production code. The subsequent
implementation now adds the native session core:

- Immutable source metadata for all three pins and their complete native
  tool/prompt/resource inventories. Discovery rejects missing, duplicate or
  unknown capabilities and unsafe schema references, without rewriting native
  descriptions or schemas. This is not runtime qualification.
- An initialized-session driver using the actual MCP SDK protocol, with bounded
  discovery/calls/results, required backend-specific admission, unchanged native
  invocation/results, mutator filtering, and typed argument/policy/transport/tool
  failures. Missing resource/prompt capabilities are recorded as unavailable.
  Read-only operations of the main server's mixed administration tools and its
  native default operations remain usable; the driver does not inject defaults
  into submitted arguments or permit their mutation operations.
  Native prompt retrieval does not inject content into the benchmark system
  prompt. There is no launcher or automatically admitted campaign path.
- Implementation-specific mechanical observations: directed Armadin path/domain
  data, unambiguous MorDavid integers, and native main-server scalar literals.
  Lossy, ambiguous, truncated and unsupported shapes remain inconclusive or
  unsupported. Zero observations do not become absence proofs, and no oracle or
  graph snapshot supplies missing facts. Observation is not proof completion.

Final focused validation passed 67 tests and 92 subtests in 1.96 seconds, including real
in-memory MCP client/server protocol round trips for all three source inventories.
These servers are local test doubles, not the upstream implementations or a live
backend. Independent review found and verified fixes for malformed arguments
escaping validation and native error envelopes being treated as successful
unprojected calls; its two targeted tests and nine subtests passed. Ruff and
whitespace checks pass. Batch-end full regression passed 1,290 tests and 1,460
subtests in 682.17 seconds. The final native mixed-tool/default-operation
adjustment was made after that process collected its tests and was separately
covered by the 67-test/92-subtest focused result above. No historical runner or
compiler code changed in this native-core batch; the full suite was not repeated
for that isolated adjustment. The native session library is accepted offline,
not the unfinished native campaign integration.

The next backend batch adds `ori verify-native-graph`: explicit database and
credential scope, optional pinned Neo4j driver, bounded full-inventory collection,
scoring-graph comparison, native-property observation fingerprint, transaction
rollback, typed failures and private atomic output. It rejects extra objects,
ambiguous labels, duplicate physical edges, unsupported property values and
incomplete pages. Real generated-archive integration includes CE-derived local
groups; nameless objects use an observed-ID display fallback without inventing a
native name property. An independent negative assertion rejects that invented
property. The existing CE verifier and historical campaigns are unchanged.

Focused graph/session tests passed 13 tests and 67 subtests in 1.51 seconds.
Independent bounded review passed six tests and 36 subtests with no remaining
blocker. The final graph-only rerun, including the added negative assertion,
passed six tests and 36 subtests in 1.08 seconds. Ruff and whitespace checks pass.
Batch-end full regression passed **1,297 tests and 1,507 subtests in 653.81
seconds**. The backend-verification CLI batch is accepted offline. Dependency
lock consistency, installed CLI help and private-file ignore rules also passed.
See [native backend verification](../native-mcp-backends.md) for the operator
workflow and limits. No actual backend, upstream server or model was contacted.

Native compiler/certifier/runner integration, source/runtime qualification,
backend-specific model-query containment, live Bolt graph parity, complete claim-specific
projection and local-model qualification remain unfinished. Existing campaigns
are not silently switched to this library path. No upstream server, real model,
graph mutation, remote host operation or Git publication was launched. The task
expansion baseline remains accepted offline; no unrelated cleanup is reopened.

The next native integration deliverable follows existing extension points:
explicit capability selection in `profiles.py` and `mcp.py`; implementation-bound
task compilation; actual native-envelope fixture replay in `certification.py`;
backend-bound readiness in `prepare_v2_campaign`; and explicit native dispatch
from `_run_model`. Historical defaults remain unchanged. Existing provenance and
checkpoint infrastructure must carry the new bindings rather than introducing a
parallel evidence system. Current native extractors supply observations, not
complete claim proofs; selected-task feasibility is required before provider
admission. In particular, empty Armadin shortest paths cannot prove bounded
absence, limited lists cannot prove full sets, and lost MorDavid path facts cannot
be supplied by an oracle. An unsupported implementation receives an unranked typed
cell, not an altered selection. This is remaining implementation work, not a
new planning prerequisite or live-operations authorization.

The September 6 continuation resolved a material native schema migration before
implementation: the current evidence contract literally permits only
`cypher_query.run`, and the historical capability profile requires CE version
metadata. Independent adversarial review selected a separate native capability
type, an explicit native evidence-contract union and binding mode, fresh native
compilation/certification, and claim-bound adjudication before cell admission.
The exact interface and coherent batch acceptance are now recorded in P5. No
production code changed in that decision turn; no new baseline regression was
run. This is an architecture decision resolved, not native campaign completion.
The reviewed batch is now in implementation; ordinary details do not require
another planning/review cycle.

Implemented native contract boundary: separate immutable native capability
profiles; explicit native binding mode and evidence-contract variant; optional
native compilation through both product compilers; preserved recipe/claim IDs
with new task/oracle/catalog hashes; and pre-dispatch session/profile/discovery
checks. The main and MorDavid native count paths classify actual native scalar
responses against public population scope. Independent implementation review
found intermediate-population and pre-dispatch-binding gaps. Fixes reject
limiting/filtering/rebinding stages, arithmetic/property counts and duplicate-row
counts, and reject mismatched tasks before any backend call. These native proof
requirements are public. Historical certifiers explicitly refuse native tasks
until native adapter replay is implemented; no native candidate or campaign is
claimed qualified by this work.

The native compilation test verifies all 110 OAIC MCP contracts retain their
recipe roster under explicit MorDavid compilation. Compilation is not evidence
that MorDavid can prove all those contracts: source-specific projectors,
certification and selected-cell feasibility remain required. The broader
compilation-to-runner batch remains open. Native fixture replay, route/set/absence
adjudication, candidate admission and shared-runner dispatch are its remaining
implementation work; live qualification and publication stay deferred.

Focused native/compiler/schema/runtime validation passed **117 tests and 156
subtests in 75.64 seconds**. Independent verification of both review fixes passed
two tests and 24 subtests. Ruff and whitespace checks pass. The previous full
suite remains evidence for its earlier backend-verifier boundary, not these
subsequent source changes; the next full run is reserved for completion of this
coherent native compilation-to-runner batch. No model, live backend, graph
mutation, host operation or Git publication occurred.

Local recovery checkpoint `cefe2dd` preserves the accumulated Stage 3 and Stage 4
work on `codex/oaic-codebase-consolidation`. It is signed and explicitly WIP, not
release acceptance. The full regression started before that checkpoint completed
with **1,305 tests and 1,541 subtests passing in 701.66 seconds**. Subsequent native
proof changes below are covered separately by focused tests, not by that full run.
No remote Git operation occurred.

Native path/domain continuation: main CE graph envelopes now retain actual stable
IDs, directed endpoint references and observed properties without turning scalar
columns into completeness counts. Main and Armadin positive route observations
must connect the exact public endpoint identities within the declared hop bound.
Armadin `find_domains` now proves only the complete unfiltered Domain population;
its pinned source has no hidden query limit and its client iterates all records.
Malformed counts, missing/duplicate identities, truncation, filtered populations
and selected windows cannot unlock this proof. No oracle supplements native data.
Independent review found no implementation blocker and requested independent
Domain scope-negative tests; those are included with the actual-session route
and malformed-main-graph regressions. The compilation-to-runner batch remains
open: native certification, whole-selected-cell feasibility, readiness and shared
campaign dispatch remain required before Stage 4 is ready for operator testing.
Focused session/projection validation passed **68 tests and 94 subtests in 3.27
seconds**; Ruff and whitespace checks passed. No model or live graph calls were
made. Public pinned source was read without a Git fetch, push, PR or merge.

Native offline certification now uses the existing fixture/catalog interfaces,
with an explicit native profile dispatch. Main/MorDavid count fixtures and
Armadin complete Domain-set fixtures cross source-shaped envelopes, production
native projection/adjudication, and the same MCP schema finalizer/comparator as
historical runs. Every applicable scorer fixture is replayed, and independent
unknown/truncated/failed/unrelated-scope responses must remain non-unlocking.
Unsupported native shapes abort the whole catalog; no smaller replacement
selection is created. Native finalizer state is explicitly uncertified for
offline replay, and the default certified path remains closed. The existing
offline certificate structure is reused without a parallel receipt system.
Independent review found no blocking regression; its historical adapter check
passed 13 tests. Focused native/compiler/finalizer validation passed 131 tests
and 100 subtests in 82.06 seconds, before the separate Armadin replay assertion
was added. This is further implementation of the open Stage 4 batch, not live
qualification or whole-campaign completion. Native route/set/absence replay,
whole-selected-cell feasibility and shared campaign admission/dispatch remain.
The final dedicated main/MorDavid/Armadin offline replay checks passed three
tests in 2.78 seconds, including proof rejection and whole-catalog refusal.
Ruff and whitespace checks passed; no live model, graph or remote Git calls.

Native route replay is now integrated into offline certification for main and
Armadin's supported unconstrained positive route surface. Main fixture envelopes
use stable object IDs plus actual CE map-key edge endpoints; Armadin fixtures
serialize only an ordered simple directed path, not unavailable edge properties
or supporting edges. Certification independently rejects node-only responses,
missing stable identities and native no-path results. The existing adversarial
answer fixtures still cross the shared finalizer/scorer. The dedicated Armadin
fixture authors an unconstrained route with the corresponding public policy;
it does not weaken or replace the selected benchmark roster. MorDavid's lossy
native paths remain unsupported rather than reconstructed from sealed data.
Independent route review found no blocker and passed two route certification
tests. Focused native/compiler/projector/finalizer validation passed **83 tests
and 70 subtests in 29.12 seconds**. Ruff and whitespace checks passed. This is
offline Stage 4 progress, not native live admission. General set/absence proof,
complete selected-cell feasibility and shared runner admission/dispatch remain.

One deferred backlog: unrelated dead-code/cosmetic cleanup, additional test-count
consolidation without demonstrated payoff, broader partial-usage knownness and
nonblocking provider edge cases. Accounting required by the scorecard remains in
Stage 5, not silently dropped. The original roadmap is not complete.

S1-02B's independently reviewed test-support restoration is implemented at a
179-file frozen boundary. Shared helpers now live in three support modules; the
new static AST import gate checks nested, aliased, package-child and relative
imports. Mechanical checks preserve 54 existing test bodies, 21 helper bodies,
moved constants, the context wrapper and all remaining module statements. Six
fixture variants are byte-identical with an equal synthetic clock. Three fresh
support imports pass external-operation and credential-access tripwires. The
full combined suite and independent fault-control reconciliation now pass.
Independent final review accepts S1-02B and N/N-A1 at this combined boundary.
See the
[restoration plan](2026-09-06-oaic-test-support-restoration-plan.md).

S1-05N's fresh Codex stream plan is independently approved after resolving failure
precedence and ignored-output-index coverage. Its accepted 174-file entry was
snapshotted. Production reconciliation is implemented in its three-file scope;
independent source/test review and 76 grouped acceptance cells now pass. No
renewed live campaign readiness is claimed by this offline validation.
The wider import gate found seven pre-existing indented imports from collected
test modules; earlier anchored searches missed them. The failed gate is preserved.
S1-02B restores the static import invariant on the new combined source; it does not
claim analysis of arbitrary computed dynamic imports. The historical
175-file N boundary passed 1,194 tests and 1,329 subtests in 227.59s;
focused validation passes 343 tests and 1,096 subtests. Three controls reconcile
73 baseline passes, three intended failures and 70 unaffected passes. Independent
runtime evidence review originally withheld acceptance. The new combined gates,
not retroactive alteration of those receipts, now establish offline acceptance.

Current accepted regression: **1,195 tests and 1,339 subtests pass in 262.09s**.
Inventory: 179 files,
`4478262cc59b1514e706158e99ebbe4f20731234bd75bb240f05821c4653bdc1`.
Ruff, offline lock, whitespace, architecture, parity and three fresh-import gates
pass. All 32 fresh control processes reconcile 231 baseline passes, 16 intended
failures and 215 unaffected passes, with zero observed external operations.
No production behavior changed in S1-02B. The test-count reduction target remains
open; this slice adds one regression test rather than reducing coverage.

Previous accepted M regression: **1,190 tests and 1,253 subtests pass in 224.80s**
with S1-05M/M-A1/M-A2/M-A3 included. Focused provider/V2 validation passes
339 tests and 1,020 subtests; Ruff, offline lock and whitespace gates pass.
The 174-file implementation inventory is unchanged. Four offline fault controls
detect four intended regressions while preserving 143 other passing cells;
all 147 baseline cells pass. Independent source/test review and root evidence
reconciliation and final independent evidence review pass. S1-05M and its three
amendments are accepted. This is
offline engineering acceptance only, not merged-code or live campaign readiness.

The operator now requires a fresh in-depth plan, independent Devil's Advocate
review, finding remediation and implementation for each stage. The canonical
contract maps user stages 0–10 to existing P1–P11 packages; P12 stays the parallel
design lane. [Stage 1's plan](2026-09-05-oaic-stage-1-cleanup-plan.md) passed
adversarial review for the bounded S1-02 extraction. Three findings addressed actual reviewer assignment,
executable clean-machine qualification and precise dirty-source snapshots. The
configured specialist could not start; a separate available agent performs the
adversarial role. S1-02 then passed independent implementation review, 161 focused
tests, lint, collection and source-hash checks. No collected-test imports remain
in the audited Python/embedded import surface. Unreviewed later slices remain blocked from implementation,
not from read-only investigation. No whole-stage approval is claimed.

Earlier complete regression run: **1,141 tests passed in 247.36s**, including
S1-02, S1-03A/B/C/D, S1-04A/C/D, S1-05D/E/F and S1-06A. The default-verbosity
full-suite summary does not print a separate subtest count; it is not inferred.
Final focused validation passes 65 tests and 134 subtests. Ruff, offline lock and
diff checks passed; the 163-file validation inventory is unchanged.
The [S1-03A consolidation](2026-09-05-oaic-test-consolidation-plan.md)
passed independent plan/implementation review and 110 focused tests plus ten
subtests. It reduces collection to 1,053 while retaining all ten catalog
obligations, and strengthens continuation-after-failure coverage. The latest
full-suite run covers these changes.

The next [S1-03B generator scenario plan](2026-09-05-oaic-generator-scenarios-plan.md)
passed independent adversarial re-review after two findings were resolved:
manifest construction cannot suppress independent artifact checks, and the
missing-file fault probe now has an exact target and expected failure count.
Snapshot verification, implementation and independent implementation review now
pass. Its 14-case reduction brings collection to 1,039 while preserving all 73
original assertions. Nine isolated fault controls and deep-state checks passed;
34 focused tests plus 19 subtests passed before the full regression run. All 158
implementation files match the validation-start inventory after completion.

A read-only, single-seed diagnostic of the expensive expansion test completed in
122.613 seconds under profiling. Repeated answer-schema meta-validation and MCP
capability validation account for substantial cumulative work. These are
optimization candidates, not approved changes or measured speedups. The diagnostic
does not replace the complete three-seed acceptance test. Production optimization
still requires a separate reviewed compatibility plan and controlled measurements.

The repeated MCP-validation candidate is now rejected for this behavior-preserving
cleanup following independent adversarial review and a local counterexample.
An accepted custom event can change a private profile copy between initializations;
the existing later validation detects it. Retain these checks rather than silently
change accepted-input behavior. No production/test changes or new speedup claims
result from this investigation. Other model-free cleanup work remains available;
operator-deferred signing, remote publication and live-model setup remain gates.

[S1-06A installed-package acceptance](2026-09-05-oaic-portable-installation-plan.md)
is complete locally after independent plan, implementation and evidence review.
The standalone driver exercises the installed wheel outside the worktree/venv,
with spaced paths, credentials removed and dotenv discovery disabled. All ten
probes passed, including both generated products, configuration-relative status,
poisoned output and missing-config failures. An altered isolated installation was
rejected before CLI probes. Each status probe records one specifically identified
blocked IPv6 capability check; no socket bind was permitted and ordinary probes
recorded no outbound attempts. Inert status fixtures are not certified evidence.

This adds 61 regression cases/two subtests; it does not advance the collected-test
reduction target by renaming cases. All 158 pre-existing implementation files are
unchanged and all 160 current source hashes match the full-run boundary. Manual
new-file secret/private-path review passed; automated container scanning remains
unavailable with Docker stopped. The four-cell clean-machine matrix is still open.

Independent review rejected the proposed 46-check runtime/real-receipt wrapper
consolidation: it would reduce collection without removing setup or creating
genuine lifecycles. It was not implemented. The revised
[S1-03D plan](2026-09-05-oaic-runtime-receipt-scenarios-plan.md) instead removes
96 lines of duplicate task construction through three fresh-object factories.
All tests remain individually collected, with unchanged IDs and assertions.
Independent plan and implementation review, thirteen runtime equivalence cases,
three exact fault controls, and 173 focused tests pass. Production coverage is
identical; full regression and final 160-file source reconciliation pass.
This bounded slice is complete. This is maintainability work,
not progress toward the collected-count target or a measured speedup.

The next [S1-04A discovery-key plan](2026-09-05-oaic-discovery-key-plan.md) passed
independent plan, snapshot and implementation review. Revision 2 resolves its fault
control finding: both bindings must share the faulty callable so the probe reaches
direction semantics rather than failing the identity assertion first. The grader
now imports the compiler's identical helper; all other production bytes remain
unchanged. Twelve focused tests, four exact before/after scoring records and three
fault controls pass. Full regression and final 160-file source reconciliation
pass. The bounded slice is complete, with nine fewer production lines and three
new regression cases; this is not a collected-test reduction or speedup claim.

The source-review coverage audit identified seven remaining substantive areas;
incremental cleanup is not whole-repository review. The independently approved
[S1-04B publication review](2026-09-05-oaic-publication-review-plan.md) will trace
candidate/private state through graph gates, report publication and model cards.
The read-only audit is complete as a bounded review pass and authorizes no implementation.
Entry files are not assumed to cover every dependency; unresolved closures remain
open. Other gaps include provider execution, certification integrity,
graph normalization, V1 behavior, operator lifecycle and complete discovery review.

Two S1-04B findings have independently validated model-free synthetic reproductions:
URL/relative/embedded
path display labels reach both public model-card formats, and changed private
state graph/catalog/public-artifact bindings remain accepted when unkeyed
self-hashes are recomputed. The provenance probe proves an unchecked state binding;
its fixture has no provenance file. These are consistency gaps, not authenticity
claims. The [twelve-obligation ledger](2026-09-05-oaic-publication-review.md),
63 focused tests and final source hashes passed independent review. Fixes and
explicit unresolved obligations remain open; no fix is authorized by the audit plan.
Raw fixtures, mutated states and output files remain ignored/private.

[S1-04C](2026-09-05-oaic-model-card-binding-plan.md) will require and
reconciling existing per-run provenance while preserving valid historical evidence,
selected-subset behavior and output schemas. Independent adversarial plan/map
review and three-file/160-source snapshot verification passed on September 6.
The implementation and expanded 17-test/100-subtest model-card suite pass; valid
frozen JSON/SVG bytes are unchanged and all five root mismatch probes reject.
The full 160-file source inventory stayed unchanged throughout full validation.
Final independent evidence reconciliation passed; the bounded F02 finding is
closed. Label admission will
receive its own compatibility plan.

[S1-04D public-string admission](2026-09-06-oaic-public-label-plan.md) has now
passed independent plan/snapshot review and implemented the bounded F01 fix.
Review caught and resolved punctuation-path and Unicode word-boundary gaps.
Focused tests pass 20 cases and 157 subtests; both default/display frozen output
pairs remain unchanged. Pattern-isolation and recursive-guard bypass controls
pass with their expected failures. Full regression and final independent evidence
closure passed; bounded F01 is closed. Namespace/model ambiguity and disguised content remain
explicit limitations; no arbitrary identity redaction or stricter fingerprint
schema was introduced.

[S1-05A provider-contract audit](2026-09-06-oaic-provider-contract-review-plan.md)
passed independent plan and final evidence review. It covers four provider/auth/response files and
five primary test modules, with a 72-cell provider/obligation ledger and 92 passing
focused tests. Six findings remain to be fixed in separately challenged slices;
synthetic reproductions made zero network calls and the 160-file inventory is unchanged. Native loops,
V2 orchestration and projector semantics remain separate review work. The audit
authorizes no implementation or provider/auth operations.

The separately challenged [S1-05D fix](2026-09-06-oaic-compatible-destination-plan.md)
preserves the explicit configured compatible endpoint over an inline model URL.
Its nine regression cases produced five expected mismatch failures and four
control passes before implementation; all 101 focused provider tests now pass.
Independent implementation and final full-suite evidence review passed. Only
this portion of F01 is closed. Changing shared runtime code invalidates all older
V2 campaign resume fingerprints, so use fresh outputs/readiness. No provider,
graph or remote Git operations were performed.
The [next origin-admission preparation](2026-09-06-oaic-scoped-origin-preparation.md)
was followed by the separately challenged [S1-05E plan](2026-09-06-oaic-scoped-origin-plan.md).
Its scoped OpenRouter/Nous credential guard is implemented; independent review
approved production, compatibility tests, operator documentation and fault
controls. Focused validation passes 144 tests and 186 subtests. Full regression
and independent final evidence review passed; the parsed OpenRouter/Nous portion
of F02 is closed. All 84 original test functions are
unchanged, and controls prove each consumer continues through 43 later cases
after a first-case failure without network activity. Other F02 providers and
redirect/SDK behavior remain open. The next
[internal-error preparation](2026-09-06-oaic-provider-error-preparation.md) is
followed by the [S1-05F draft](2026-09-06-oaic-provider-internal-error-plan.md).
New root evidence crosses actual adapter and Direct runtime: a synthetic internal
ValueError becomes retryable infrastructure, and missing synthetic Codex auth is
also retryable. Zero network/query activity occurred. Independent challenge found
that Anthropic's supported authentication acquisition also needs a typed boundary,
and the exact fault-control map was incomplete. Revision 2 now specifies the
normal-client native header check, early-exit cleanup, SDK-root error policy,
15 collected tests/96 cases and eight isolated fault controls. Nine root offline
cases match actual locked-SDK header admission, including its existing rejections;
every client closes and no network/discovery/token acquisition runs. The 161-file
baseline was preserved before edits. S1-05F passed independent plan and
implementation review and is now complete: actual SDK-class classification,
typed Codex authentication and native Anthropic header admission retain known
provider failures, while internal exceptions become nonretryable V2 harness
failures. Legacy artifacts remain compatible. All 15 new tests/96 cases are
present; exact frozen receipt/transcript comparisons and eight fault controls
with eight fresh baselines passed independent review. Full 1,141-test regression,
final focused 65/134 validation and exact 163-file reconciliation passed.
Final inventory: `f1df7bbf70e570a965b6256f22fc0753e940ea820aad2c66102ccef26a19fd74`.
Full log: `364b5c472e352d7b4a2eb821b793ba86dccc08cee95550d0a7fc9966f430f45d`.
Independent final evidence review approved this bounded attribution correction,
not broader F02 or Stage 1. No provider/graph/remote Git operations were made.
Older V2 campaigns require fresh output/readiness and renewed stale certification.
The [Codex destination binding plan](2026-09-06-oaic-codex-binding-plan.md)
revision 3 and its implementation/tests are independently approved. The complete
163-file pre-edit snapshot and 1,141-test collection matched the prior boundary.
Codex now binds credentials to the selected endpoint, rejects unsafe built-in
variants and slash-only fallback, and records the actual source/destination in
readiness and provenance. Custom destinations require `CODEX_COMPAT_API_KEY`;
the old implicit OpenAI-key fallback is deliberately removed.
Final validation: 1,150 tests in 222.19s; focused 154 tests/546 subtests; nine new
tests/343 subtests; seven fault controls and seven fresh baselines; Ruff, offline
lock and diff checks passed. The final 165-file inventory is
`c669a68e93707db6a88e2e446b1ca163bc37899392e544cc8f55a375cb4307eb`.
Full log: `bad9b3a27b30c254670507dda81bf7fad4d5f69f59e149d3126a119e14371049`.
Independent final evidence review approved bounded S1-05G closure; broader Stage 1
remains open. Redirect containment and remote capability validation are not claimed.
The [remaining endpoint preparation](2026-09-06-oaic-remaining-destinations-preparation.md)
records Anthropic, Ollama and Gemini gaps without implementing the next slice.

[S1-05H characterization](2026-09-06-oaic-anthropic-characterization-plan.md)
revision 2 was independently challenged, corrected and approved. Seven synthetic
HTTP requests and two configuration-only profile cases confirmed Anthropic's
recorded-versus-executed destination gap, separate potential refresh routing and
inherited authentication-header behavior. All 165 accepted source files remain
unchanged. The [evidence/handoff](2026-09-06-oaic-anthropic-characterization.md)
passed independent final review. S1-05H is complete as a bounded characterization;
no Anthropic production fix or F01/F02 closure is claimed. The next step is the
full destination/credential binding implementation plan using these verified inputs.

[S1-05I binding plan](2026-09-06-oaic-anthropic-binding-plan.md) is in revision 2
after independent challenge identified preparation ordering, header-only discovery,
fingerprint/privacy, exact test coverage and resource-ownership gaps. The resumed
session reverified all 165 source hashes against the accepted boundary. Revision 2
specifies atomic immutable preparation, explicit native constructor branches,
private header resume comparison and independently attempted resource cleanup.
The finite appendix now defines 17 scenario functions/142 explicit cases, seven
fault controls and exact compatibility-test migrations. Independent review approved
revision 2 after the exact Anthropic-only mutation projection was specified.
The unchanged Anthropic baseline was rerun: 3 tests/17 subtests passed in 0.73s,
and the complete 165-file pre-edit snapshot was captured before implementation.

Implementation is now in progress: the SDK specialist owns the new binding module,
root owns adapter/config/runner integration and the private resume guard, and the
test specialist owns 17 new scenarios plus four named compatibility-test changes.
The first development regression pass has 39 tests/97 subtests passing. A separate
pass initially had 55 tests/92 subtests passing with one expected failure at the
readiness-version assertion before its planned migration. After that migration,
the same selection passed 56 tests/92 subtests in 1.37s. These are moving-source checks,
not final acceptance. Source review, complete tests, fault controls and final
evidence reconciliation remain open; no campaign readiness or full-suite pass for
S1-05I is claimed.

Independent first-pass source review found and then rechecked two P2 corrections:
conflicting inline URLs now fail before materialization when no explicit URL
overrides them, and unavailable SDK/private modules now raise the constant typed
capability error. The checks used process-local sentinels without credentials,
client construction or network access. These two findings are closed, not the
whole implementation review. Source Ruff and whitespace checks pass; the new test
suite and its frozen evidence gates remain in progress.
All 17 new scenario functions/142 cases are now written; initial integrated runs
are fixture-debugging evidence only. A Q3 route-classification discrepancy was
corrected without parsing SDK errors or rereading profiles: invalid established
exchange routes from explicit profiles get the typed capability failure, while
native fallback-profile error swallowing is preserved.

S1-05I is now implemented and independently accepted. The frozen boundary is
167 source/test/script/lock files, inventory SHA-256
`d94449bb649e873af64921abcc0d77b9448309cd75ea20635ad11c1a0c193d25`.
The full suite passed 1,167 tests and 994 subtests in 226.81 seconds; focused
validation passed 21 tests and 159 subtests. All seven deliberate fault controls
failed their intended cell while 32 continuation cells passed; the seven fresh
baselines passed all 39 cells. The 14 control receipts retain unchanged source
inventories and zero network calls. Ruff, offline lock validation and whitespace
checks passed. Independent review accepted the bounded destination/authentication
binding, private resume guard and compatibility evidence, including the explicit
limitation that overwritten early diagnostic receipts were not retained and are
excluded from acceptance. This closes S1-05I, not Stage 1 or campaign readiness.
Private completion reconciliation and the subsequent independent-review receipt
are retained together under `results/p2-slices/s1-05i/`; the former accurately
records that review was still pending when it was created.

The next bounded plan addresses the remaining Ollama destination mismatch across
Direct, native MCP and V2 provenance. Gemini's fixed-destination reconciliation
remains a separate follow-up. Real model qualification and remote Git operations
remain deferred; neither is required for this next offline engineering slice.

S1-05J now has an independently approved revision-2 plan and a complete pre-edit
snapshot. Shared Ollama destination resolution and V2 immutable selection are
implemented; first source review passed. The unchanged focused regression set
passes 154 tests/375 subtests after preserving lightweight configuration-object
compatibility. The new ten-scenario suite has 68 cells: 67 pass, while actual
native MCP dispatch exposes a pre-existing runtime integration defect before HTTP
allocation: common V2 loop kwargs pass `max_tokens` to `_run_ollama_mcp_loop`,
whose signature does not accept it. This requires a narrowly reviewed plan
amendment and correction, not a test-side workaround. No final source freeze,
fault-control acceptance or S1-05J completion is claimed. All checks remain
synthetic and offline; no safety refusal or inference admission gate caused this
integration failure.

J-A1 was independently approved and implemented: `max_tokens` is now passed only
to the OpenAI-compatible native loop, leaving Ollama's explicit options unchanged.
Real Direct and MCP dispatch both preserve a synthetic `num_predict` option.
All ten new scenarios/68 cells pass; independent source/test review passed.
Frozen final validation at 169 files, inventory
`b916a64d49e2ce5abf5b991f2da7af9307f280f510e0b71dd7c31b8ea1f3e3fc`,
passed 1,177 tests/1,062 subtests in 224.90s. Focused validation passed 164 tests/
443 subtests in 3.17s. Ruff, offline lock and whitespace gates passed. Five fresh
baseline/control pairs passed all 44 baseline cells and caught exactly five
intended failures while 39 other cells passed; external-call counters stayed zero.
The authoritative controls are `a49b475678e6`, with the earlier `48fabd38e460`
driver and receipts preserved but excluded from final acceptance. Root reconciled
all full-path inventories and log/receipt hashes in `completion.private.json`;
independent final evidence review remains pending at this checkpoint.

Independent final review subsequently accepted S1-05J and J-A1, reconciling all
169 unchanged files, five passing gates and ten authoritative control runs.
`independent-review.private.json` records that acceptance separately from the
earlier pending-review reconciliation. The next step is the fresh Gemini
fixed-endpoint reconciliation plan; broader Stage 1 and final campaign gates
remain open.

S1-05K Gemini reconciliation now has an independently approved plan and the
169-file pre-edit snapshot. The bounded production correction is implemented:
one shared fixed SDK endpoint constant feeds both Gemini execution and V2
provenance. Existing ignored-URL behavior, model payloads, scoped credentials and
Direct-only support remain unchanged. First independent source review passed;
85 existing focused tests/136 subtests pass. Four new scenario functions/20 cells
and three controls are being prepared. This moving-source development checkpoint
is not final K acceptance or complete provider-review closure.

K source/test review and frozen validation subsequently passed at 170 files,
inventory `83851473ebb2c4e154089026851a44df43360b64d3f48762d5fc74ef171d9a5e`.
The full suite passed 1,181 tests/1,082 subtests in 222.42s; focused validation
passed 167 tests/464 subtests in 3.24s. Ruff, offline lock and whitespace passed.
Three fresh baseline/control pairs passed all 19 baseline cells and detected
exactly three intended failures, with 16 unaffected cells passing and zero recorded
external calls. Root reconciled every source path/hash and gate/control log hash;
`ed1225690bdb` is the authoritative control manifest. Final independent evidence
review remains pending at this checkpoint; no complete Stage 1 claim is made.

Final independent review subsequently accepted S1-05K and its unchanged 170-file
boundary. The later `independent-review.private.json` records acceptance after
the pending-review reconciliation. Next priorities: reviewed redirect containment
and terminal/tool-call integrity, with deterministic schema-test consolidation
available independently. The fewer-than-700 suite target remains unmet.

S1-05L inference redirect containment has a fresh independently approved plan
and a verified 170-file K pre-edit snapshot. The bounded implementation disables
redirect following at every native inference transport and gives native Codex
redirects a typed protocol failure. A shared SDK transport owner also closes the
previously unowned Direct Chat Completions transport. SDK retry defaults are
unchanged; native credential-exchange flows remain a separate unqualified boundary.
Four grouped acceptance scenarios (77 cells) and three negative controls are
being implemented. This is a moving-source development checkpoint, not L final
acceptance, campaign readiness or Stage 1 completion. No real provider calls,
remote Git operations or hardware/service changes were made.

L's first frozen full/focused runs exposed only the pre-existing K21 raw OpenAI
redirect-message expectation. Independent amendment L-A1 approved changing that
one expectation to the planned constant while retaining all 41 cases and their
classification/counter assertions. The original failed logs and successful
superseded controls remain preserved. The amended 172-file boundary is
`d0abc7b8e8d2b610e731671669f720b837cacf982c5cea3b309bc9cc202d732d`;
all final gates and six fresh controls are being rerun against it.

The amended L boundary subsequently passed full regression (1,185 tests and
1,159 subtests, 224.33s), focused regression (334 tests and 926 subtests, 15.37s),
Ruff, offline lock and whitespace checks. Authoritative controls `43313b4d0e13`
passed 100 baseline cells, caught exactly three intended defects and passed
97 unaffected cells, with no external calls and unchanged source inventories.
Root reconciliation is complete; final independent evidence acceptance is pending
at this checkpoint. The next read-only preparation identifies Ollama terminal
completion integrity across Direct and native MCP as a bounded follow-up, with
Codex ordering/tool-identity changes kept separate.

Final independent evidence review subsequently accepted S1-05L/L-A1 at that
unchanged 172-file boundary. Native inference redirects and owned transport
cleanup are now closed as a bounded slice; Stage 1 remains open. Next: a reviewed
Ollama terminal-integrity plan, followed by separate Codex ordering/tool-identity
work and justified deterministic test consolidation.

S1-05M now has a detailed independently approved base plan and a verified 172-file
pre-edit snapshot. The test specialist is drafting real-wire framing scenarios.
Before production integration, the feasibility audit found that clearing truncated
text would deprive the existing schema-repair no-new-facts guard of its source.
M-A1 therefore proposes retaining that original text only in private evidence and
repair inputs, with byte-budget accounting and no executable/graded partial output.
The affected consumer expectations and production integration are paused for
independent amendment review. No full M validation or closure is claimed.

M-A1 was subsequently independently approved and captured separately. The shared
stream validator and both native consumer integrations are implemented under the
seven-file production scope. Truncated model text remains ungraded/unexecutable,
but its privately retained original is used by the unchanged lexical repair guard
and output-byte accounting. Source review and 86 grouped acceptance cells are in
progress; no M acceptance or broader Stage 1 completion is claimed yet.

M-A2 is independently approved and its bounded source review passes. Native
Ollama schema-repair requests now retain object tool arguments, convert existing
JSON-object strings, and reject missing/malformed/non-object arguments before
transport. Other providers retain their existing representation. The final test
scope is five grouped functions with 94 cells and four negative controls.
Complete validation and independent evidence review remain pending. The C10
repair-counter discrepancy is recorded for subsequent accounting work; actual
request/token receipts, not that counter, determine the scenario's usage.

M-A3 subsequently restored the no-collected-test-import invariant with a local
synthetic fixture, without editing accepted L tests. Independent review also
required actual retained provider records to cross public operational aggregation
and an explicit no-reasoning-verdict outcome for over-budget C10. All fixes are
included in the latest accepted frozen regression above. No partial Ollama stream
can dispatch a tool, and valid prior evidence remains available to the existing
bounded schema-repair path. The next scoped planning target is Codex successful
stream ordering and tool identity; its read-only findings are recorded in the
terminal-integrity preparation note. No remote Git or live-model work was done.

[S1-03C](2026-09-05-oaic-compiler-report-tests-plan.md) now has an independently
approved plan and verified pre-edit snapshots. Its instrumented baseline confirmed
443 repeated certification calls across 12 passing test cases. The plan fixes
certificate/fixture integrity and deep-copy review findings before reuse, repairs
coverage gaps, and combines three genuine setup-sharing scenarios. Implementation
is present in both ownership lanes and independent implementation reviews found
no blocking issues. Report validation: 18 tests/four subtests and five root fault
controls pass. Compiler/ownership validation: 51 tests/30 subtests pass. The real
simple/complex MCP task IDs do not currently overlap; the reviewed synthetic
collision control tests future collision protection, not lost current results.
Root compiler fault controls, controlled measurement, full regression and final
hash reconciliation now pass. The bounded slice is complete; broader Stage 1
remains open. Controlled complex answer construction on prebuilt certified inputs
has medians 7.242s before and 0.329s after (ranges 7.216–7.453s and 0.327–0.560s),
with exact answers on every sample. This is test setup reuse, not a campaign-speed
claim. Real module certification remains intact and no production code changed.

Phase P2 review has started under the operator-approved September 5 exception:
PR/merge may wait while cleanup and regression testing proceed on the consolidated
branch. V30 qualification passed; final campaigns/public results still require
merged, currently certified code. Earlier entries describe gates in force when
recorded; the execution contract's amendment now governs P2. The branch preserves the
existing 29-commit development lineage above master without rewriting it.

| Package | Status | Owner | Evidence / next action |
| --- | --- | --- | --- |
| P0-W01 durable plan | complete | integration | Approved contract saved with this ledger |
| P0-N01/N02 mobile pairing/test | pending user | user/integration | Native readiness question issued; delivery not verified |
| P1-W01 inventory | complete | branch reviewer | Full ancestry/patch/lock comparison below; no missing patch identified |
| P1-W02 consolidated worktree | complete | integration | Development lineage preserved; remote branch verified at 82e2c7a with plan and process tests |
| P1-W03 V30 independent review | manual pass | independent reviewer | Exact 10a979e..0f0eaa8 and new process tests: no confirmed findings; automated review unavailable |
| P1-W04 host acceptance | unmerged evidence passed | operations | Second-machine offline suite/CLI and three-snapshot live certification passed at 22cf478; merged-source binding remains |
| P1-W05 PR/merge | deferred by operator | integration/user | GitHub CLI SAML authorization missing; remains required before final campaign/publication |
| P2 cleanup | S1-03C/D, S1-04A/C/D, S1-05G/H/I/J/K/L and local S1-06A complete; stage open | engineering | Latest accepted full suite: 1,185 tests/1,159 subtests; next: terminal/tool-call integrity and justified test consolidation; broader provider review, test reduction, automated scan and clean-machine qualification remain open |
| P3 local Qwen | pending endpoint qualification | operations | Operator-provisioned inference; no reservation or agent dependency in ORI |
| P4 library/selector | pending dependency | compiler | Requires stable P1/P2/P3 interfaces |
| P5 native MCP/providers | pending dependency | runtime | Requires stable P1/P2/P3 interfaces |
| P6 scorecard/budget | pending dependency | operations | Requires stable P1/P2 interfaces |
| P7 release qualification | pending dependency | QA | Requires P4/P5/P6 |
| P8 campaign | pending dependency | operator | Requires merged release and exact admission manifest |
| P9 evidence freeze | pending dependency | release | Requires valid campaign evidence |
| P10 offline package | pending dependency | presentation | Existing historical assets preserved |
| P11 delivery | pending date | user | Physical delivery is user-owned |
| P12 OpenGraph design | independently reviewed; conference freeze pending | architecture/domain | Vendor-neutral future V3 specification and two contract walkthroughs; no extension implementation |

## Baseline evidence

P12-W01–W06 now have a [reviewed specification](../opengraph-extension-framework-design.md):
strict manifests and interfaces, exact identity/relationship semantics, native
evidence projection, certification, compatibility, fingerprints, migrations and
two abstract domain walkthroughs. Independent architecture and repository/domain
reviews found no remaining blocking findings after revisions. Configured
specialist startup was unavailable; separate architecture and repository
reviewers supplied these reviews. Encoding vectors and local links passed
document QA; secret scanning passed. This is design-only completion, not runtime
support, live qualification or the September conference freeze. No provider calls,
graph mutations or remote Git operations were made for this package.

P2 details and retained behavior obligations are tracked in the
[cleanup review ledger](2026-09-05-oaic-cleanup-review.md). P1 receipts below bind
to the pre-cleanup implementation, not the edited runtime. Both old Direct and
MCP offline/live catalogs are correctly rejected as stale after the source-byte
fingerprint change; no provenance guard has been relaxed.

First P2 batch: 107 production lines removed, retained module syntax trees
unchanged, and seed-4401 ZIP/manifest byte-identical to P1. Full validation passed
1,066 tests in 271.17s; independent review found no issues. Ruff, lock, packaging,
CLI startup and documentation secret scans passed. This is not completion of the
test-count target or measured optimization work. Changes remain local while the
existing SSH signer is unavailable; no signing or remote-sync guard was disabled.

Second P2 batch: shared campaign builders moved out of collected test modules,
with all twelve definitions and 29 status test functions structurally identical.
Complex multihop checks now share setup within three named scenarios; both archive
tests stay separate. All thirteen original logical obligations remain (eleven
named subtests plus two archive tests), with identical assertion bodies. Full
validation passed 1,058 tests and eleven subtests in 253.71s. Independent review,
Ruff, lock, diff and secret checks passed. This timing is not a controlled speedup
measurement. The suite-size goal, broader cleanup and release qualification remain
open; no provider calls or remote changes were made.

Third P2 batch profiles full complex-corpus adapter replay and removes repeated
relationship-index construction within each compiler edge-validation call.
One warmup and five alternating measurements over the actual captured workload
showed a 4.709s baseline median versus 1.172s after reuse, with essentially unchanged
traced peak allocation. This is an isolated workload result, not whole-campaign
performance. Six focused regressions pass, complete 46/70-task compiled corpora
match under baseline/candidate helper replay, and independent review found no
issues. Fingerprint serialization was deliberately left unchanged after its
compatibility review. Broader profiling, test reduction and installation gates
remain open; the compiler source fingerprint has advanced and old evidence must
not be reused for a new campaign.

The final lazy-index implementation additionally preserves invalid-None rejection
and empty-iterator behavior. Final full validation passed 1,064 tests plus eleven
subtests in 233.88s; source/wheel builds, Ruff, lock, diff and secret checks passed.
The isolated speed measurement does not establish an end-to-end suite speedup.
Independent provider/credential/launcher review also found no actionable issues;
live provider and clean-machine qualification remain separate pending gates.

- Remote master: `1247c009ab1ea4817eb8b99adcea2e9ddbc7ded9`.
- Integrated development: `0f0eaa8e10f65c897c2a07f03033a31b874e5a01`.
- Previous local validation of that unchanged source: 1,042 tests passed,
  focused V30 212 passed; Ruff/lock/diff checks passed.
- SSH commit signature verified using the already configured signing public key
  and a temporary command-local allowed-signers file. Global trust configuration
  was not changed; its configured allowed-signers path is absent.

## Preservation

- Original development checkout and source branches retained.
- Untracked public-release report and earlier expansion plan retained in original
  checkout; their historical/in-progress status is not current qualification.
- Original output directory retained, excluded from this integration pending inventory.
- Historical V29 artifacts unchanged; no claim that they qualify V30.

## Input blockers

- GitHub CLI authenticated repository API access requires organization SAML
  authorization. Git fetch succeeds; the configured connector returns inaccessible
  repository errors. Branch push succeeded, but draft PR creation was rejected
  by SAML enforcement. No PR exists yet. No authentication values are copied here.
  Rechecked on the first active-goal continuation: CLI SAML still blocks access;
  the available in-app browser was signed out, so it could not create the PR.
- Phone alert delivery requires end-to-end user confirmation.
- Local inference is provisioned outside ORI. The previously recorded GPU
  reservation blocker is withdrawn: no such mechanism is an ORI prerequisite.
  Qualification still requires the configured endpoint and reproducibility
  metadata, not any particular machine or agent installation.

## Portability boundary correction

The operator clarified that local model-service management is external to the
public benchmark. Both standalone terminal use and an external automation agent
driving the same CLI are required; neither gets a separate scoring/runtime path.
The execution contract now explicitly forbids ORI-owned
reservation, personal-agent, SSH or service-control dependencies. Source and
package-dependency inspection found no existing dependency on those private
systems. Public setup guidance describes endpoint configuration only. Clean
Linux/macOS qualification is a release gate, not a claim of completed testing.

Legacy example filenames/profile IDs remain compatible, but defaults now use
loopback inference and config-relative dataset paths. Operators must set their
own served model ID and endpoint; previous private configs are not rewritten.
The historical rich-report helper requires `--run-root` and accepts an optional
fresh `--output-dir`; existing outputs are preserved rather than overwritten.
Its output remains private historical diagnostics, not certified public evidence.
The archived-attribution test now runs against deterministic temporary fixtures
instead of silently passing when a private artifact directory is unavailable.

Validation of this correction: 163 focused configuration, provider, launcher,
credential-routing and helper tests passed; Ruff, lock and diff checks passed.
The complete updated suite passed: 1,053 tests in 255.14s on Python 3.12.13.
Source distribution and wheel builds passed. The installed CLI help command
also passed outside the checkout with an empty environment except system PATH.
Independent manual review of all 16 changed files found no actionable issues;
the automated review CLI remained unavailable. Clean-machine OS qualification
is still pending, so these local checks do not establish that release gate.
The package/runtime source has no personal-agent or private-host dependency.
No benchmark model calls, remote host operations or service configuration changes
were needed.

## Findings

P1 installed-wheel acceptance at `3114921` exposed a reproducibility defect in
named `simple` generation: identical seeds produced different ZIP bytes because
user/computer properties used wall-clock timestamps. Manifests were identical,
so successful preflight alone did not detect it. The private failed receipt is
retained; it is not a passing qualification artifact.

The narrow correction normalizes timestamps only for named simple generation,
advances its generator metadata to `seeded-benchmark-v2`, and freezes existing
identity/scale RNG namespaces. Complex and unnamed legacy generation are
unchanged. Regression checks cover both named CLI aliases under widely different
clocks, exact ZIP/manifest bytes, and variation across seeds. Old simple evidence
must not be resumed with regenerated archives; see the products runbook.

Additional P1 process tests now use the real CLI dispatcher in separate
interpreters to check active/interrupted/stale readiness and execution status,
correct recovery actions, corrupt-evidence refusal, redaction and read-only
inspection. Controlled preparation/execution seams remain fake, so these tests
do not prove live graph or provider behavior.
The combined process/status gate passed 47 tests; the simple generator/profile
gate passed 15 tests. Independent manual review found no actionable issues in
these changes. This is prerequisite hardening, not admission to later phases.
Full updated validation passed: 1,061 tests in 279.18s; Ruff, lock, diff and
secret checks passed.

Installed-wheel revalidation passed at `c08bd7afaced067a1c77c116f02236393203f11d`:

- Fresh non-editable installation with 94 locked runtime dependencies on
  macOS arm64/Python 3.12.13, outside the source checkout.
- Direct CLI and isolated subprocess driver generated identical seed-67 ZIP
  and manifest bytes under different clocks. The driver denied network/launcher
  operations and source-checkout access; no personal-agent environment was used.
- Direct and MCP task preflight returned successful receipts; zero model calls.
- Wheel SHA-256: `8c4181fdb1a27576b971de83e0f2eace4f530f569c1f62b27af5a65b54243fa2`.

This qualifies the named simple offline path on a fresh local environment only.
It does not establish Linux portability, live graph equivalence, V30 campaign
qualification or merged-source publication eligibility. Remaining P1 work includes
controlled-host replay of the acceptance scenarios, live graph qualification,
and the required PR review/merge. Local scheduler and publication coverage added
below closes the previously identified offline test gaps, not those host gates.

## Additional P1 recovery and publication acceptance

Two test files exercise the previously missing production boundaries without
contacting model providers or live graph services:

- `test_v2_attempt_process_acceptance.py`: five fresh interpreters cross the real
  scheduler, Direct task runtime, atomic checkpoint writes and validated reload.
  Synthetic provider failure drives two durable cooldown interruptions, recovery
  rounds, monotonic attempts 1/2/3 and lifetime retry exhaustion. Restart retains
  the persisted not-before deadline and cannot reset the retry allowance. Task,
  certification and provenance fixtures are synthetic; the campaign-wide wrapper
  is covered separately by the existing process tests.
- `test_v2_publication_acceptance.py`: matching-graph execution and drift at each
  of the four independent Direct/MCP pre/post gates cross actual orchestration,
  scheduling, graph comparison, checkpoints, lifecycle and report publication.
  Drift refuses new reports; already published Direct evidence stays byte-identical
  when a later MCP gate fails. Preparation, task runtime, graph acquisition and
  MCP launch are test doubles. These tests do not qualify candidate admission,
  real model transports, MCP behavior or a live BloodHound graph.

Fresh complex seed-4401 generation and both offline compilation commands passed
against source `0585f78fd2ef32ad4e300974a8932707135a7738`. The Direct inventory
contains 46 offline-certified tasks and 462 fixture cases; MCP contains 70 and
655 respectively. Both certification receipts contain zero failures.

- Archive SHA-256: `a00e60e0f7ba8a02bcfee74e0ef1b99d7e46107d25b5172ffb1231e51f8f5edb`.
- Manifest SHA-256: `d2dd22a037a3d8f2d4055c2792a5abee8806a43a2900519378e0b1822c314be9`.
- Archive-derived graph: `fb0b6785e524d40abcc9033ea2c7887eaa88e13bb6cb1f329636a4ce4b74b1c4`.

Private compiler/oracle/certification artifacts remain under ignored results.
This is existing V30 proof-inventory evidence, not a live certificate, the future
unique-contract pool, or the OAIC selected 50/50 release. No source production
code changed during this additional acceptance pass.

The six new acceptance scenarios passed together in 11.73s. The full suite passed:
1,067 tests in 284.79s. Independent manual review of both files found no actionable
issues. Ruff and lockfile checks passed;
the staged test/documentation secret scan was clean. These results are offline
acceptance evidence only, not permission to bypass the P1 merge/host gate.

Independent manual review of exactly `10a979e..0f0eaa8` found no confirmed
production defects. A separate review of the new process-acceptance test file
found no actionable issues. Coverage included scoring/output compliance, MCP
finalization, retry accounting, status/reporting, schema, selection, fixtures and
recipe coverage, plus process containment and interruption persistence.

The automated `codex review` invocation could not complete because the installed
CLI was incompatible with its selected model. Manual review is not a claim that
this automated gate passed, that the entire repository is defect-free, or that
controlled-host/merged-source qualification is complete. Full cleanup review is
still the gated P2 package.

## Branch disposition (P1-W01)

Matching local/remote refs share the same disposition unless separately noted.

| Ref | Disposition | Evidence |
| --- | --- | --- |
| master / origin/master / origin/HEAD | incorporated | 1247c00 ancestor |
| codex/v30-scoring-output-compliance | incorporated | 0f0eaa8 baseline |
| codex/benchmark-correctness-v29-fixes | incorporated | 9b52b44 ancestor |
| codex/integrate-v28-open-world | incorporated | 1568007 ancestor |
| codex/merge-all-changes | incorporated | 224678c ancestor |
| codex/nous-api-key / feat/nous-portal-ox-alpha | incorporated | cf458fb ancestor |
| codex/openrouter-api-key | incorporated | 661ee7c ancestor |
| codex/ori-tier6-and-repeat | incorporated | f5e6511 ancestor |
| codex/tier6-sharphound-projection | incorporated | 01c6cf9 ancestor |
| codex/uvx-git-mcp-launcher | incorporated | 7085ee2 ancestor, including local extra commit |
| origin/codex/uvx-git-mcp-launcher | incorporated | 5abb9e1 ancestor |
| feat/benchmark-correctness-v2 | incorporated | d86ce95 ancestor |
| feat/v2-campaign-operations | incorporated | 10a979e ancestor |
| fix/direct-query-containment | incorporated | 0a56029 ancestor |
| fix/openai-provider-runtime | incorporated | d6ac198 ancestor |
| fix/sharphound-contract-hardening | incorporated | a296b81 ancestor |
| origin/feat/phase4b-diagnostics-benchmark-registry | incorporated | e4bf483 ancestor |
| origin/fix/phase4-mcp-failure-attribution | incorporated | 6ca03d4 ancestor |
| origin/repair/phase4b-v2-review-findings-t_38c7fa14 | incorporated | 2d31ebe ancestor |
| hermes/v30-harddeadline | incorporated | 8ad6f94 ancestor |
| origin/chore/dependabot-uv-updates | incorporated | 0b6c6f7 ancestor |
| chore/complex-v1-phase0-freeze | equivalent | 2946c73 patch matches contained 68f4cde |
| origin/docs/sanitized-inference-config-examples | ported | 5b13f1b additions in 224678c, followed by credential/Nous improvements |
| antonetta/feature/open-world-discovery-v1 | superseded | 66db53f old standalone implementation replaced by a003b37 V28-native discovery |

All origin dependabot branch tips are represented or superseded by the lockfile:

| Package | Branch requested | Current locked |
| --- | --- | --- |
| aiohttp | 3.14.3 | 3.14.3 |
| anthropic | 0.87.0 | 0.116.0 |
| cryptography | 50.0.0 | 50.0.0 |
| idna | 3.15 | 3.18 |
| mcp | 1.28.1 | 1.28.1 |
| pydantic-settings | 2.14.2 | 2.14.2 |
| pygments | 2.20.0 | 2.20.0 |
| pyjwt | 2.13.0 | 2.13.0 |
| pytest | 9.0.3 | 9.1.1 |
| python-multipart | 0.0.31 | 0.0.32 |
| soupsieve | 2.8.4 | 2.8.4 |
| starlette | 1.3.1 | 1.3.1 |
| urllib3 | 2.7.0 | 2.7.0 |

The second linked worktree has an uncommitted local inference configuration edit
(12 added/10 deleted lines); it is preserved without importing private settings.
Original output contains two generated files totaling approximately 3.7 MB;
metadata only was inspected, and neither is staged.

## Current validation

- Fresh isolated environment installed with `uv sync --frozen` on Python 3.12.13.
- Gitleaks scanned integrated history through the branch-publication ledger:
  31 non-merge commits, no leaks. Documentation and test directory scans passed.
- Focused supervisor/status/durability/runtime/scoring gate: 92 passed in 7.82s.
- Ruff, lockfile validation and diff checks passed in the isolated worktree.
- Fresh Python 3.12 baseline full suite: 1,042 passed in 250.15s.
- New process acceptance file, run separately: 6 passed in 11.47s.
  This is additional coverage, not a claim that the preceding full-suite run
  collected the new file.
- Real-process coverage includes campaign/supervisor lock exclusion and release,
  SIGTERM durability, SIGKILL stale state, and second-interpreter recovery.
  Artifact preparation and execution bodies are replaced; these checks do not
  certify live graph parity, providers, remote deployment or task-checkpoint resume.

## Earlier controlled-host discovery (historical)

Read-only identity checks succeeded on both the benchmark host and GPU host.
At that inspection, the benchmark checkout was clean at detached 8ad6f94, not
consolidated HEAD. Its prior artifacts do not qualify the new source. An absolute uv
executable was verified on the benchmark host but was absent from its noninteractive
PATH. No provider calls, model loads, reservation actions or service changes ran.

## Independent-machine qualification at 22cf478

A second macOS 26.3.1 arm64 machine qualified an isolated source copy at
`22cf478e3d74f9a013166ec29e365db9cf1034cd`, using Python 3.12.13 and uv 0.10.9.
The source archive SHA-256 matched before extraction:
`20402b766ace92951f0e14ef1af42deafb6dafcb7f12a8fe76fa20c45c3680ad`.

Frozen dependency installation, all 1,067 tests (222.86s), Ruff and standalone
CLI startup passed. The private external driver used explicit installed-tool
paths and a minimal environment, with no personal-agent installation or model
service operation. Its durable session survived observer reconnection. The
receipt and full test log were retrieved and their checksums matched remotely
and locally:

- Qualification receipt: `918354b6c7cb2e6f1177f19450732810fe087ed28881aae03a0d300d2eb1fffc`.
- Test log: `1683bb04f0c1cba8a5a05292b0af85d65dfd6914cbd06228019993ff9b474654`.

The machine's existing checkout was not used or changed. Its committed branch
at `cf458fb` is already included in the consolidation; its uncommitted legacy
MCP-launcher compatibility patch is also represented in the current implementation
and was preserved in place. No source branches or unrelated files were removed.

Separately, controlled BloodHound health and the fresh seed-4401 ingest check
passed: all declared object counts and 30/30 planted paths matched. Both legacy
manifest structural preflights passed with zero errors/warnings (42 Direct and
62 MCP tasks). These counts are not the V30 certification or OAIC release counts.
The exact three-snapshot live-certification command also passed. Pre, middle and
post snapshots each matched 17,088 canonical objects and 60,342 relationships,
with 40 object queries and 159 relationship queries per snapshot. All three
observed graph fingerprints equal the archive-derived graph
`fb0b6785e524d40abcc9033ea2c7887eaa88e13bb6cb1f329636a4ce4b74b1c4`.
Their content-derived verification fingerprint is
`5a832aaf6d15972eaf4f75c44d442f0e839004b185777eb0947b5bb939f12f53`;
identical receipts reflect identical observations, not reuse of one acquisition.

Direct has 46 candidate-certified tasks and MCP has 70, each with zero failures.
Their semantic candidate releases contain 42 and 55 representatives respectively.
Each exported candidate catalog exactly matches its embedded certification copy.

| Evidence | Direct | MCP |
| --- | --- | --- |
| Live certification fingerprint | `c8135cba7403fb916ffd0949421cf1b9ad939635e363b1832cc442a5f36b85ed` | `cebb3c39c0f8955ac709672a07ecd6d302f240315c5238b11e3d6d203217601c` |
| Candidate release fingerprint | `d684e85b1bd491e1acb44c4031e16e15a608f5fb7bf0d7460bc2eba358433a7d` | `ef78a49a8f72350bf2345a2e5954e6903004e89bf2e7b1c4fc353c09eed4372c` |

These are V30 baseline receipts, not the future OAIC 50/50 selected release or
evidence of a real-model campaign. No provider calls or graph mutations were
requested. Private artifacts and operational paths remain outside public Git.

This is independent-machine offline acceptance, not Linux/Windows qualification,
live model/MCP transport qualification, or publication from merged code. P1 remains
open pending merged-source evidence binding and PR review/merge. GitHub CLI
repository access was rechecked and still fails organization SAML authorization. Public
documentation contains no private host paths, credentials or raw graph evidence.
