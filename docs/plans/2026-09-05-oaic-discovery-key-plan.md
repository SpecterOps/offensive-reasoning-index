# S1-04A — one discovery edge-comparison implementation

Revision 2, September 5, 2026. Status: implemented; independent plan, snapshot,
implementation and evidence review passed; full regression and reconciliation
passed. This bounded slice is complete.
This is a bounded Stage 1 cleanup, not a new
discovery protocol, scorer policy, task release or performance optimization.

## Scope, dependencies and ownership

Root owns the plan, snapshots, implementation and private differential checks.
`oaic_stage1_adversarial_fallback` owns independent plan and implementation
review. The read-only audit by `p2_dead_code_inventory` identified this exact
duplicate and its caller closure. Finish S1-03D full validation before editing
this slice. Preserve all existing dirty work on the consolidated branch at base
`afa95428c966cb02972fc2a7998b0dd0b6e052a3`.

Owned implementation files: `src/ori/discovery/grader.py` and
`tests/test_discovery_v2.py` only. No other source file, dependency, public export,
model config, task catalog, oracle or capability profile may change. No provider,
model, graph, host, signing, remote, PR or merge operation is part of this slice.

## Exact change and compatibility contract

Extend the grader's existing import from `.compiler` to import `_edge_key` with
`iter_truth_variants`. Delete only the grader's local duplicate `_edge_key`
definition. Keep every caller unchanged. The compiler's existing helper becomes
the sole implementation; do not add a utility module or alter compiler bytes.
Both current helper definitions must be AST-identical in the pre-edit snapshot.

The key remains exactly `(source_id.casefold(), relationship.casefold(),
target_id.casefold(), direction.value)` in that evaluation order. It does not
trim whitespace, resolve aliases, normalize relationships, swap inbound endpoints,
include properties, coerce invalid objects, cache inputs or catch exceptions.
Existing grader normalization and separate graph attestation remain untouched.

The grader already imports the compiler, so this adds no module dependency or
cycle. Its private `_edge_key` binding stays importable. The callable's private
module/source metadata changes to the compiler's; repository search found no
consumer of that metadata. No supported public metadata contract is introduced.
Do not unify comparator, Direct adapter or graph edge keys: their contracts differ.

Compiler/schema source bytes and the discovery compiler fingerprint stay exactly
unchanged. That fingerprint already includes the canonical helper's source file.
The current discovery report uses its fixed scorer version and content-derived
fingerprint, not a source-hashed grader fingerprint. Preserve that existing policy;
do not claim a new certification boundary. Exact same discovery inputs must yield
byte-identical serialized reports, including their existing fingerprints.
Prior V2 campaign certificates remain stale from earlier production changes.

## Snapshot and baseline

Use the established `ori-cleanup-snapshot-v1` protocol under ignored
`results/p2-slices/s1-04a/`: complete byte-verified pre-edit copies of both owned
files; base commit; records and canonical record digest; full current nonignored
source inventory (`src`, `tests`, `scripts`, lock and project metadata). Do not
use HEAD as the before-state. Retain original compiler bytes/hash and actual
discovery compiler fingerprint privately. Collect and run the nine original
discovery tests before editing. Preserve all nine complete function bodies.

## Exact regression additions

Append three independently collected tests; do not combine or rename existing
tests. Use module imports for compiler/grader to assert the shared callable.
Use one local edge-construction expression per test, not imported collected-test
helpers. Existing fixture helpers inside this same module may be used.

1. `test_discovery_edge_key_preserves_comparison_contract`: shared callable
   identity; exact tuple for source `Straße`, relationship `MemberOf`, target
   `TARGET`, outbound direction; case variant equality including `STRASSE`;
   whitespace remains significant; reversed endpoints differ; inbound differs;
   property-only variation has the same key. Assert tuple contents directly, not
   merely equality between the two bindings. Use the current schema's
   `PropertyFact(key="enabled", value=True)` for the property variation and
   `EdgeDirection.INBOUND` for direction; instantiate valid EdgeWitness values.
2. `test_discovery_edge_key_preserves_invalid_input_errors`: for both bindings,
   `None` raises AttributeError; a private plain object whose source property
   raises a unique RuntimeError propagates that exact instance without touching
   its relationship/target/direction properties. Restore no shared object because
   each probe creates a fresh object. These are invocation compatibility checks,
   not new supported schema inputs.
3. `test_discovery_strict_subpath_preserves_order`: using three consecutive
   directed edges A→B→C→D, both compiler `_strict_contiguous_subpath` and grader
   `_is_strict_subpath` return true for the prefix and interior single edge,
   false for the complete route, reversed sequence and disconnected selection.
   Explicitly preserve existing empty-candidate behavior against a nonempty
   container; both return true. Do not silently fix this behavior in cleanup.

The added cases increase collection by three (1,099→1,102 if the prerequisite
boundary is unchanged). They document extracted semantics; no reduction claim.

## Differential and fault evidence

Before edits, run the complete nine-test module through a private instrumentation
plugin wrapping its imported `grade_discovery`. Record every call's complete
public/private/submission serialized inputs and full returned serialized report,
including fingerprints. Canonicalize JSON with sorted keys and compact separators;
keep all captured private artifacts in ignored results. Repeat after edits, require
identical ordered call records and exactly four successful grading invocations
(alias, alternate/overlap, invalid-evidence pair, redaction). Tests not calling the
grader still run unchanged. A count mismatch requires investigation, not relaxing
the check. Never expose these fixtures as certified campaign results.

Run the new key-contract test once with both compiler and grader bindings privately
replaced by the same direction-collapsing callable: retain the original first three
tuple fields but always return `"outbound"` in the fourth. Shared identity and the
outbound tuple assertion must pass; require failure at the explicit inbound-key
inequality assertion. Replacing only one binding is not an adequate control,
because it fails identity before exercising direction semantics. This correction
resolves the independent review finding. Run the subpath test once with each
module's relevant comparison privately replaced by an always-false callable:
it must fail for each module separately. Use separate test processes and restore
bindings in each plugin's finalizer. These expected failures are private controls,
not passing production tests.

## Exit and stop conditions

- Independent plan approval and byte-verified baseline precede edits.
- Production AST equals baseline after only the specified import and deletion;
  all original test function bytes and all unowned source files are unchanged.
- Nine original plus three new discovery tests pass; differential calls/reports
  and compiler fingerprint are identical; all three fault controls fail as expected.
- Independent implementation/evidence review passes before final full-suite run.
- Full regression, Ruff, frozen-lock and diff checks pass, with source inventory
  identical before/after validation. Record exact counts, not estimates.
- Update progress and cleanup ledgers; no speedup, whole-stage completion or
  refreshed live-certification claim. No public raw evidence.

Stop on nonidentical helpers, unexpected callers/metadata dependence, altered
exception or ordering behavior, report drift, unexpected fingerprint changes,
lost original tests, source changes outside ownership, failed controls or review.
Any substantive redesign returns to the independent plan review gate.

## Implementation evidence

The grader now imports the existing compiler helper and no longer defines its own
copy. No caller or other production file changed. The exact allowed AST transform
matches the snapshot. All original test/helper definitions remain byte-identical;
three regression tests were appended. Independent snapshot review required adding
the explicit full-inventory digest to the receipt; that was corrected before edits.

Baseline: nine tests pass and record four complete scoring inputs/results. After
implementation, twelve tests pass and all four records match exactly, including
report fingerprints. Discovery compiler fingerprint is unchanged. All three fault
controls fail at their intended assertions; the direction control reaches the
inbound comparison after identity and outbound checks pass. Independent review
found no remaining issue. Full regression passed 1,102 tests and 76 subtests in
251.25s. Ruff, lock and diff checks pass; all 160 implementation files match the
validation-start inventory. Canonical serialized comparison also passes, avoiding
Python's permissive numeric/boolean equality. This removes nine production lines;
it adds three regression cases and does not reduce collection. No speedup is claimed.
