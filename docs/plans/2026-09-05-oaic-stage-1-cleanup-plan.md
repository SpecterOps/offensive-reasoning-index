# Stage 1 / P2 — cleanup, tests and optimization execution plan

Revision 2, September 5, 2026. Status: S1-02 implemented and independently reviewed; stage remains open.
Implementation window: September 7–10; preparation may occur earlier. This plan
implements the stage-review amendment to the [execution contract](2026-09-05-oaic-execution-plan.md).
It is not permission to skip P2 exit gates or to run providers.

## 1. Source boundary and current evidence

Accountable owner: root acting as engineering/integration lead. All source changes
stay on `codex/oaic-codebase-consolidation`; base commit is
`afa95428c966cb02972fc2a7998b0dd0b6e052a3`. Preserve every existing uncommitted
change and original checkout. Do not fetch, push, create a PR, attempt signing
workarounds or merge while the operator's deferral remains in force.

The [cleanup ledger](2026-09-05-oaic-cleanup-review.md) records prior work:
seven unreachable private helpers removed; shared campaign/compiler/Direct test
support extracted; generated-graph scenarios share setup; repeated compiler edge
index construction removed; three duplicate test executions removed across the
earlier batches. None of those changes means the whole repository is audited.
The preceding full run passed 1,064 tests and eleven subtests; the latest support
follow-up passed 340 focused tests and collected 1,062 tests. A fresh full run
completed with 1,062 tests and eleven subtests passing in 292.78s, recorded in
the progress ledger. That result predates S1-02. A later source/test edit requires
another appropriate run.

Old V30 live/offline certificates are stale after runtime/compiler source changes.
Do not weaken fingerprints or reuse them. Earlier independent-machine evidence
belongs to its recorded commit, not this dirty branch. No full Linux acceptance
has been established for the current cleanup.

## 2. Ownership and ordered work

Each row has one accountable owner. Root may delegate a narrow implementation
slice only with explicit files and a non-overlap claim. Record the actual worker
and reviewer before that slice starts. The adversarial reviewer is separate from
root; configured specialist startup failures must be disclosed. A later independent
implementation reviewer must not be the author of the slice it approves.

| ID | Accountable owner | Scope and deliverable | Independent review responsibility |
| --- | --- | --- | --- |
| S1-01 | Integration lead | Reconcile existing dirty diff, baseline evidence, public CLI/config and supported legacy behavior inventory | Verify no unsupported completion claims or lost work |
| S1-02 | Test engineering owner (root) | Remaining MCP fixture dependency extraction described below | Definition equivalence, isolation and import boundary |
| S1-03 | Test engineering owner (root) | Scenario/duplicate audit across complete test inventory, obligation mapping and justified reductions | Case coverage, fixture lifetime, diagnostics and count honesty |
| S1-04 | Engineering lead (root) | Package/runtime reachability and responsibility audit | Dynamic imports, dispatch, public compatibility and removal evidence |
| S1-05 | Performance owner (root) | Profiles and controlled before/after comparisons | Measurement reproducibility and semantic equivalence |
| S1-06 | QA owner (root) | Full regression, packaging, clean-machine portable CLI acceptance | Independent receipt/source-boundary reconciliation |
| S1-07 | Integration lead | Documentation, findings disposition and stage exit ledger | Final independent stage review; unresolved gates prevent closure |

Assigned plan and S1-02 implementation reviewer: `oaic_stage1_adversarial_fallback`,
a separate agent from root, performing the Devil's Advocate role after specialist
startup failed. S1-01 and S1-03–S1-07 implementation reviewers are currently
**unassigned—implementation blocked** until named in this table/ledger. Read-only
audits and already-running validation may continue. Reviewer unavailability does
not transfer approval to root; record a new independent assignment and re-review.

Order: S1-01 → reviewed S1-02; S1-03/S1-04 audit can run read-only in parallel;
their proposed implementation slices require this plan's adversarial gate plus
an exact change/obligation ledger before edits. S1-05 measurements precede any
optimization. S1-06 follows the last edit; S1-07 reconciles all results. Do not
implement a vague cleanup recommendation without a reviewed concrete diff scope.

## 3. Immediate deterministic change: MCP test support

Before applying this slice, retain complete pre-edit bytes under ignored
`results/p2-slices/s1-02/pre-edit/`, mirroring each owned relative path. Write a
private `snapshot.private.json` with schema `ori-cleanup-snapshot-v1`, base commit,
slice ID, sorted records `{path, exists, sha256}`, and SHA-256 of that canonical
record list (UTF-8 compact JSON with sorted keys). Include the three existing
test modules and explicit absence of the new support module. Also inventory all
tracked and nonignored untracked files under `src`, `tests`, `scripts`, plus
`pyproject.toml` and `uv.lock`, so the dirty implementation is identified rather
than inferred from HEAD. Capture complete files through nontruncated tool results
and use `apply_patch` for snapshot writes; verify every copied byte hash.

Compare the immutable pre-edit snapshot for extraction equivalence, not HEAD.
Record a post-edit source inventory before tests and compare it after tests;
any unexpected source change invalidates that run. Snapshot metadata and raw
source copies remain private. Apply the same protocol to later slices. The
reviewer must confirm the snapshot inventory before S1-02 implementation.

Files owned by S1-02:

- New `tests/support/v2_mcp.py`.
- `tests/test_v2_mcp_adapter.py`.
- Import lines only in `tests/test_v2_model_runtime.py` and `tests/test_v2_runtime.py`.

Move exactly twelve current definitions: `FP`, `ALICE`, `TARGET`, `EDGE`, `CLAIM`,
`PROFILE`, `POLICY`, `BINDING`, `TASK`, `ORACLE`, `RESOLVER`, `_answer`.
Copy the exact definition bodies; resolve only required production imports into
the support module. Do not merge these with Direct fixtures: their entity roles,
bindings and evidence contracts differ. `_useful_event` stays in the MCP test
module because no external test currently imports it. No new production module,
test dependency, model call, fixture scope, global cache or runtime fingerprint
change is required. Existing module-level sharing remains unchanged.

Redirect the five existing relative import statements in model-runtime/runtime
tests to the neutral support module. Remove only imports made unused by extraction.
Keep all tests, parameter cases, fixtures and assertions unchanged. Do not apply
whole-file formatting to unrelated code. Use programmatic complete-source patches
with explicit truncation checks; never reconstruct source from clipped tool text.

Acceptance:

1. Baseline/current AST equality for all twelve moved definitions, all retained
   functions/classes/assignments, and every caller test body.
2. Exact retained function/class source comparison; no incidental body formatting.
3. `uv run pytest tests/test_v2_mcp_adapter.py tests/test_v2_model_runtime.py tests/test_v2_runtime.py -q` passes.
4. Inventory every Python import under `tests/`, including relative imports and
   subprocess source strings. No remaining import of a collected `test_` module
   may be silently missed; retain unrelated cases only with a recorded reason.
5. `uv run ruff check src scripts tests` and `git diff --check` pass.
6. Full test collection remains 1,062; extraction does not count as test reduction.

If a moved object is mutated by a test, investigate its previous sharing lifetime
before proceeding. Do not broaden caching or change object identity to make tests
pass. An import or assertion mismatch fails the slice; restore only that slice's
changes from its verified pre-edit content and redesign before continuing.

## 4. Whole-suite consolidation policy and deliverable

Produce an inventory of every collected test and named logical subcase. Distinguish
function count, collected parameter count, and logical obligation count. Include
fixture scope, production surface, historical defect owner and setup cost. The
eight historical defect mappings in `tests/test_v2_regression_coverage.py` are
mandatory; any rename updates the mapping in the same reviewed slice.

Target fewer than 700 collected baseline tests, not fewer assertions. Combine
only coherent scenarios that exercise a shared immutable setup or a real ordered
lifecycle. Use named pytest subtests when independent checks should continue after
one failure; keep destructive/corrupting scenarios isolated. A parameter matrix
covering different acceptance/rejection boundaries is not duplicate coverage.
Do not mechanically turn matrices into loops to satisfy a number.

For every proposed reduction, write a before/after obligation map and exact setup
ownership. Prove that a failed early subcase still exposes later independent
failures, and that a mutating case cannot contaminate later cases. Preserve strict
negative tests for invalid configuration, schema, evidence, query containment,
retry limits, budget accounting and public redaction. Validate both module-only
and suite execution to expose collection-order coupling. No new pytest plugin
is needed for subtests with the currently locked pytest version.

If reaching 700 would weaken isolation or capability, document the exact remaining
cases and tradeoff for the operator; do not claim the target achieved or silently
replace it. The exception in the canonical plan permits preserving essential
coverage, not declaring an unaudited large suite satisfactory.

## 5. Production audit and compatibility decisions

Audit every package entry point and caller closure in the initial ledger:
CLI/config, generator/serializer, V1 runner/reporting, providers/MCP launcher,
V2 compiler/evidence/comparator/certifier, campaign/status/supervisor, discovery
and scripts. Classify each candidate as reachable/supported, dead with evidence,
duplicated with equivalent obligations, or unresolved. Record exact searches,
exports, dynamic lookup, configuration selection and external wrapper considerations.

Retain V1/Phase3/Phase4 reproduction, aliases, strict config semantics, endpoint
credential isolation, portable executable resolution, signal/lock/resume behavior,
and all public/private evidence boundaries. No dependency is removed merely
because its import is lazy. No relationship/type/schema rename is a cleanup.
Shared schema ownership stays with integration. Source module splits must preserve
supported import paths and expand fingerprint source closures; old certificates
then become stale, never silently compatible.

Audits may conclude no justified deletion. Optimization and cleanup must preserve
supported behavior, not only the most recent V2 happy path. Any proposed observable
change needs a separate explicit decision and is outside this behavior-preserving
stage's default authority.

## 6. Performance work and acceptance

Profile generation, archive reconstruction, compilation/certification, projection,
reporting and test setup separately. Use synthetic deterministic inputs, no model
or remote graph calls. Record exact source hashes, Python/dependency versions,
seed/product, graph size, warmup, timings and memory measurement method privately;
publish only sanitized summary evidence.

For each candidate: one warmup, five alternating baseline/candidate measurements
on the same machine and input; report all values, medians and spread. Measure
peak memory with an explicitly named method and never call traced allocations
RSS. CPU profiles locate work but instrumented timing is not the final comparison.
Keep repeatable improvements only after artifact bytes/semantic outputs and error
behavior match. Caches must define immutability, size, invalidation, thread/process
scope and empty/error behavior. The prior edge-index optimization remains a
specific measured result, not an end-to-end speedup claim.

## 7. QA, portability and final exit

After final stage edits run full `uv run pytest`, repository-wide Ruff,
`uv lock --check`, `git diff --check`, package build and public-tree secret checks.
Verify wheel/source content, CLI startup and the absence of packaged test/private
artifacts. Regenerate simple/complex artifacts in fresh output roots and compare
seeded identity, manifests, archive bytes and task contracts to the selected
compatible baseline. Any intentional provenance-only difference must be explained
separately from semantic equality.

Qualify exact source on clean Linux and macOS installations using documented Python
and uv dependencies only. Include checkout/config/output paths containing spaces,
no personal-agent installation, explicit inference URL configuration, missing-key
typed errors, fake-transport readiness/execution/status, duplicate launch,
interruption and resume. Preserve meaningful exits and local config-relative paths.
No actual inference or graph write is authorized by these offline checks. Live
readiness/certification is a separately scoped gate when the controlled endpoint
is available; never invent that receipt from offline doubles.

Qualification matrix is fixed: Ubuntu 24.04 x86_64 and macOS 26.6.2 arm64,
each with CPython 3.12.13 and CPython 3.11.16, uv 0.11.21 and the unchanged
`uv.lock`. Python 3.11 is the declared minimum minor version; its 3.11.16 patch
is an official [security release](https://www.python.org/downloads/release/python-31116/).
Record OS build/image digest and interpreter/build digest before installing
ORI. These are test pins, not a claim to support every future Python version.
If a pinned environment cannot be provisioned, leave that cell blocked; a
replacement requires an explicit reviewed plan revision, not silent substitution.

Run each cell from an isolated `ori qualification/source checkout` path:
`uv sync --frozen --python <pinned-version>`, `uv run pytest`,
`uv run ruff check src scripts tests`, `uv lock --check`, and
`uv build --out-dir "qualification artifacts/dist"`; require exit 0 for each.
Create a separate wheel-only environment, install the exact built wheel and
locked runtime dependencies, and invoke its installed `ori --help`,
`ori benchmark list`, and both named-product generation commands from outside
the checkout. Require exit 0, both product names, and matched ZIP/manifest pairs.
The exact generic CLI-driver acceptance is a new
`tests/test_portable_installation_acceptance.py` slice owned by QA: launch installed
CLI through `subprocess` with an allowlisted environment and spaced config/output
paths; assert config-relative resolution and no personal-path discovery. It must
be independently planned/reviewed before S1-06 implementation.

The [S1-06A concrete driver plan](2026-09-05-oaic-portable-installation-plan.md)
subsequently passed independent review and local installed-wheel acceptance.
Root owns QA/tests; `s1_portable_driver` implemented the standalone driver;
`oaic_stage1_adversarial_fallback` independently reviewed plan/code/evidence.
This assignment authorizes only that completed local slice, not the entire S1-06
matrix. Its blocked dependency-capability observation is explicitly accounted
for without permitting a socket operation. See its plan for current results and
remaining machine/scan/live gates.

Lifecycle coverage must run the existing `test_v2_process_acceptance.py`,
`test_v2_attempt_process_acceptance.py`, `test_v2_publication_acceptance.py`,
`test_v2_campaign_status.py`, `test_mcp_launcher.py`, and `test_provider_auth.py`.
These bind lock exclusion, SIGTERM/SIGKILL recovery, status/redaction/corruption,
monotonic retry budget/cooldown, graph-gated publication, explicit executable
paths, and scoped credentials. The new installed-CLI acceptance fills the gap
between these checkout-based tests and wheel-only invocation; neither alone
proves the other. Missing config CLI invocation must exit 2 with an actionable
error, and must not make a provider/network call.

Private `qualification.private.json` is schema `ori-cleanup-qualification-v1`:
cell ID, OS/build/architecture, Python/build/uv versions, source inventory digest,
lock digest, wheel/sdist SHA-256, commands with argument arrays and exit codes,
test/subtest counts, log hashes, start/end source digests, and pass/fail/blocked
state with typed reasons. Command paths/logs stay private. Every receipt is bound
to the exact snapshot and wheel, not merely the Git branch name.

Generation comparison baseline is `afa9542`, reconstructed independently without
resetting any existing checkout. Compare simple seeds 1234 and 1235 and complex
seeds 4401 and 4402: archive bytes, manifest identity/graph data and public task
semantics. Compare current-code same-seed reruns too. Compiler/certifier source
fingerprints may differ as documented; graph/task semantics may not. Use fresh
per-cell/per-seed output directories and retain paired artifact hashes.

Exit requires an independent final review, reviewed obligation inventory, justified
test-count disposition, retained behavior evidence, controlled performance results,
and clean-machine receipts for the exact source. Missing machine access remains
an explicit open gate; do not mark Stage 1 complete. Merge may wait under the
operator amendment, but final hosted campaign/publication may not use unmerged
or stale-certified code. New code cannot inherit an earlier machine's acceptance.

## 8. Review and stop ledger

Configured Devil's Advocate startup failed because its fixed model is unavailable
in this account. A separate available adversarial reviewer reviewed this plan's
revision 2 and admitted S1-02 only. Record finding ID, severity, required decision,
revision, reviewer disposition and proof before implementation.

| Finding | Required fix in revision 2 | Disposition |
| --- | --- | --- |
| DA-1 / P1 unnamed reviewers | Assigned actual plan/S1-02 reviewer; later implementation explicitly blocked until assignment | Confirmed resolved by independent reviewer |
| DA-2 / P1 underspecified qualification | Fixed four-cell OS/Python matrix including minimum minor, source/wheel receipt schema, commands, differential seeds/baseline and installed-CLI gap | Stage-level gap resolved; S1-06 remains blocked pending its concrete driver slice review |
| DA-3 / P2 dirty-source ambiguity | Per-slice byte snapshots, full source inventory, start/end validation hash comparison and reviewer snapshot gate | Confirmed resolved; reviewer independently verified 157-file inventory and all pre-edit copies |

The reviewer found the immediate twelve-definition extraction technically sound,
and permitted implementation after DA-1 and DA-3 were independently verified.
This is not a whole-stage implementation or qualification pass.

S1-02 closeout: independent implementation review found no blocking findings
against the verified pre-edit copies. All twelve moved definitions and retained
test bodies are unchanged; only the four approved implementation files changed.
Focused validation passed 161 tests in 12.76s, full collection remains 1,062,
repository-wide Ruff and diff checks passed, and support-module secret scanning
found no leaks. All 158 implementation inventory records matched before/after
validation. AST and embedded-source scans found no remaining collected-test
imports. No production source changed. Full-suite revalidation after this slice
remains part of stage closure; the 292.78s full pass predates this extraction.

Stop the affected slice for: changed source during validation, truncated source
capture, unexplained semantic/output difference, lost regression ownership,
state-sharing change, unreviewed interface expansion, unresolved adversarial
blocker, unavailable independent reviewer, failing tests, secret finding, or a
need for live/paid authority. Continue only independent authorized work. Dates,
test count and easy local passes do not override these conditions.
