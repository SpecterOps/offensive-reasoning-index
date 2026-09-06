# S1-03D — deduplicate runtime task preparation without hiding tests

Revision 2, September 5, 2026. Status: bounded slice complete; independent plan,
implementation and evidence review and final full-suite validation passed.
Root is accountable for engineering/integration.
The broader OAIC goal, fewer-than-700 collection target and deferred remote/live
gates are unchanged.

## Review finding and changed approach

The independent reviewer rejected revision 1: renaming 46 complete tests to
private checks behind twelve tuple loops would lower collection without removing
duplicated setup or creating a new lifecycle. Do not implement that proposal.
Its private scenario map and baseline inventory remain discovery evidence only.

A fresh AST audit found genuine repeated task construction. Revision 2 removes
that duplication from twelve existing test functions using three small task
factories. All tests, negative boundaries, parameter IDs and selectors remain
independently collected. There is no collection-reduction or speedup claim for
this slice. The concrete benefit is one authoritative construction expression
per repeated contract instead of five/four/three copies, reducing boilerplate
and future fixture drift while preserving every test's observations/assertions.

## Ownership and exact scope

`s1d_runtime_tests` owns only `tests/test_v2_model_runtime.py`. Root owns plan,
private before/after/fault/equivalence evidence and progress documentation.
`oaic_stage1_adversarial_fallback` independently reviews plan, implementation
and evidence; it is separate from both authors. Assign the worker only after
approval and snapshot verification.

The real-receipt module is now unchanged; no second worker is needed. No
production, support-module, dependency, configuration, artifact, oracle or
certification implementation changes. No provider/model/graph/host/service
operations, signing, fetch/push, PR or merge. Preserve all prior dirty changes.
Base remains `afa95428c966cb02972fc2a7998b0dd0b6e052a3`.

## Three exact factories and call-site map

Place the three factories in the existing module-local fixture section, after
`_public_selection_task` and before the first collected test. Return the exact
existing right-hand expression, with no normalization, cache, default changes,
new validation, model_copy options or additional profile/task sharing.
Add only `ExecutionBounds` to the existing schema import, as required by the
declared set-factory annotation. This is the sole permitted import change.

- `_unbound_user_count_task() -> TaskBundle`: copy the complete RHS of the
  `task = MCP_TASK.model_copy(...)` statement from
  `test_count_star_accepts_one_unambiguous_typed_population`. Keep omitted
  `required_input_roles` omitted; do not replace it with an explicit default.
- `_user_set_task(bounds: ExecutionBounds) -> TaskBundle`: copy the complete
  task RHS from `test_count_projection_accepts_one_unambiguous_scalar_alias`.
  The only local input is the supplied bounds object. Keep each caller's original
  bounds construction untouched, including differing total-count/page policies.
- `_group_membership_selection_task() -> TaskBundle`: copy the complete
  `_public_selection_task(SelectionExpression(...))` RHS from
  `test_public_selection_requires_the_declared_relationship`. Preserve every
  selector, relationship, role, direction and concrete projection type.

Replace only the matching assignment RHS in the following functions. Require
every original expression to be AST-identical to its factory's canonical source;
otherwise stop for review. Caller assignment target remains `task`.

### `_unbound_user_count_task`

- `test_count_star_accepts_one_unambiguous_typed_population`
- `test_count_star_accepts_one_anonymous_typed_population`
- `test_count_star_rejects_ambiguous_typed_populations`
- `test_count_star_rejects_fanout_row_populations`
- `test_count_star_rejects_anonymous_fanout_row_populations`

### `_user_set_task`

- `test_count_projection_accepts_one_unambiguous_scalar_alias`
- `test_count_projection_accepts_limit_one_and_collected_entity_page`
- `test_page_bound_violation_revokes_an_earlier_unlock`
- `test_later_independently_complete_proof_supersedes_earlier_truncation`

### `_group_membership_selection_task`

- `test_public_selection_requires_the_declared_relationship`
- `test_public_selection_requires_unproven_concrete_projection_label`
- `test_selection_fixture_query_realizes_the_public_contract`

## Behavioral and compatibility boundary

Each call still constructs a fresh task at the original point inside its test.
Existing sharing inherited from MCP_TASK and bound objects is unchanged; frozen
Pydantic models are not assumed deeply immutable. No task is cached or placed in
a wider-scope fixture. Every projector, receipt, response and observation sequence
remains inside its original test. Do not abstract provider execution or exception
boundaries as part of this change. No collected function is renamed or combined.

All current 131 runtime and 42 real-receipt collected IDs, including parameter
suffixes and relative order, must be byte-for-byte identical before/after.
Expected full collection remains 1,099 tests plus the existing 76 subtests. No
new public API, fixture configuration or migration behavior is introduced.
Do not alter any assertion or query/result literal. Every other function's
signature, decorators and body remain byte-identical.

## Snapshot and baseline evidence

The established v1 snapshot under `results/p2-slices/s1-03d` already retains
complete pre-edit bytes of both modules and the 160-file source inventory.
Independently verify it before edits; it includes earlier accepted dirty changes
and cannot be replaced by HEAD. The revision-1 46-case obligation map is obsolete
for implementation. `preparation-map.private.json` and this twelve-call-site map
are the revision-2 scope.

Baseline: both complete modules pass 173 cases in 15.98s under line/branch coverage
of `ori.eval.v2.model_runtime`, `ori.eval.v2.mcp` and
`ori.eval.v2.mcp_adapter`; the JSON is private. Capture the exact collected IDs
and canonical RHS ASTs before edits. Require all twelve assignments to match
the three declared expressions and each helper name to be absent before editing.

## Exact verification and stop gates

1. Expand each new helper call syntactically back into its original RHS (substitute
   only the explicit bounds argument). The complete original test function AST,
   including signature, decorators, statements, assertions and literals, must then
   equal the snapshot. Compare each helper's return AST and signature to the plan.
   Verify exactly twelve replacements and exactly three new definitions.
2. Every unselected definition and all module-level statements remain byte-identical
   except the explicitly required `ExecutionBounds` import addition.
   Require a net source-line reduction; adding wrappers around otherwise unchanged
   complete tests is not acceptable. Full node-ID collection remains identical.
3. Private runtime equivalence instrumentation wraps the three factories while
   running all twelve selected tests and their original parameter variants.
   Compare each returned task against evaluation of the original expression using
   the same module globals and bounds argument. Compare full model dumps, nested
   Pydantic field-set information and types, not just task fingerprints. Keep
   strong references; each call returns a distinct task object. Verify caller
   bounds object identity is preserved in the set task. Check module task/profile
   dumps before and after; never mutate shared module fixtures for the probe.
4. Separate fault controls replace each factory with a setup RuntimeError in a
   private test process. Require every mapped collected case to fail, no skip/
   xfail, and all unrelated selected cases to continue passing. These controls
   prove coverage of the shared preparation and pytest's unchanged isolation;
   restoring original module globals is mandatory if any process is reused.
5. Rerun both complete modules with the same coverage settings. Require every
   baseline production line and branch arc still covered; any loss blocks closure.
   No performance improvement is asserted from single-run elapsed time.
6. Independent implementation review reads the complete changed module and its
   snapshot diff, canonical expressions, runtime equivalence and fault evidence.
   Resolve findings before full pytest, Ruff, frozen-lock and diff checks. Capture
   the exact post-edit source inventory and compare it after validation; production
   and all unowned file bytes must remain unchanged.
7. Public notes distinguish completed boilerplate cleanup from the still-open
   collected-test reduction target. Preserve raw snapshots, coverage and receipt
   bodies privately. Do not change production fingerprints or claim renewed
   certification from test-only evidence.

Stop on expression mismatch, unexpected call sites, shared-state mutation, field-set
or type differences, bounds identity changes, lost test IDs/coverage/assertions,
unexpected source changes, or failed review/validation. No date or count target
waives these conditions. The rejected collection-only proposal remains rejected.

## Implementation and focused evidence

The separate reviewer approved revision 2 before implementation and found no
actionable implementation issues afterward. Exactly three factories replace
twelve construction expressions; expanding those calls restores the complete
original test ASTs. All 103 other definitions remain byte-identical. The only
import addition is the planned bounds annotation. Net reduction is 96 source
lines, with no removed tests or changed production code.

Both complete modules retain exactly 173 collected IDs in the same order and
pass under production line/branch coverage (16.20s). Every baseline covered line
and branch arc in the three measured production modules is identical afterward.
The thirteen selected parameter cases pass runtime equivalence and fresh-object
checks. Factory fault controls fail exactly six, four and three mapped cases,
respectively; the other selected cases pass in each process. Fixture state and
caller bounds identity remain unchanged. These are fault-injection successes,
not ordinary passing test runs. Raw records stay in ignored private evidence.

Full regression passed: 1,099 tests and 76 subtests in 241.00s. Ruff, frozen-lock
and diff checks passed. All 160 implementation files match the validation-start
inventory; all unowned source bytes match the pre-edit snapshot. The earlier
unrecoverable session result is not counted as evidence; the accepted rerun has a
retained complete log. This closes only this bounded slice. The broader
fewer-than-700 target remains open; no elapsed-time improvement is claimed.
