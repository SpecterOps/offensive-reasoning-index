# S1-02B: restore isolated test-support imports

Status: implemented and independently accepted on the combined offline boundary.

Completion: 179 source files at
`4478262cc59b1514e706158e99ebbe4f20731234bd75bb240f05821c4653bdc1`;
1,195 tests and 1,339 subtests passed in 262.09s. All nine final gates and 32
fault-control processes passed their expected checks. The controls reconcile
231 baseline passes, 16 intentional failures and 215 unaffected passes. Exact
helper/test/module parity and six fixture byte comparisons pass. Independent
final evidence review accepts this restoration and N/N-A1 on the combined source.
No production files changed in this slice; remote and live work remain deferred.

## Boundary and purpose

The N source inventory has 175 files and SHA-256
`2d56c64e37e063d3ebb3e574ec3515566493ec67b910751341e7c8fb0c5f5b55`.
Its 1,194 tests and 1,329 subtests passed, but the architecture gate correctly
failed on seven indented imports from collected test modules. All seven were
present before N. N remains unaccepted; its failed receipt must be preserved.
This slice restores that gate without changing production behavior, fixtures,
assertions, test identifiers, provider policy, or historical evidence.

No remote Git operations, commits, model calls, live services, credential reads,
or graph operations are authorized. Use synthetic transports and fresh temporary
directories only. Preserve all unrelated dirty work. This is not a claim that the
test-count target or the wider OAIC release is complete.

## Exact implementation

1. Move the complete model-card fixture closure into
   `tests/support/v2_model_card.py`: constants `_CONFIG`, `_GRAPH`, `_TARGET`,
   `_PUBLIC`, `_CAPABILITY`, `_MODEL`, `_PROVIDER` and helpers `_catalog`, `_dump`,
   `_summary`, `_samples`, `_public_rows`, `_provider`, `_provenance`, `_state`,
   `_model_report`, `_track_receipt`, `_readiness`, `_lifecycle`, `_campaign`.
   Preserve function bodies and defaults verbatim. Move their required imports;
   retain negative/mutation helpers and test functions in the collected module.
   Update imports in model-card, Anthropic binding, Gemini destination, and Ollama
   destination tests. Support must never import back from collected tests.
2. Move `_binding_module`, `_denied`, `_native_attempt_receiver`,
   `_anthropic_case_context`, `_synthetic_profile`, and required `OFFICIAL` constant
   and imports into `tests/support/anthropic_binding.py`. Keep a thin contextmanager
   wrapper of the original name in `test_anthropic_binding.py`, setting its local
   `CURRENT_CASE` before entering the support context and resetting it after normal
   exit. Do not introduce a finally reset or otherwise redesign exception cleanup.
   The shared implementation has no dependency on the collected module's globals.
   Point all three auth-boundary imports to support. Keep existing SDK, HTTPX,
   filesystem, socket, and process traps and historical control hook names intact.
3. Move `MODEL`, `_config_payload`, `_resolved`, `_provenance` and required imports
   into `tests/support/gemini_destination.py`. Update Gemini's own references and
   the redirect test's import. Keep Gemini case contexts, receivers, and control
   hook globals in their original collected module.
4. Add `tests/test_support_imports.py`, one grouped architecture test. Its AST
   walker inspects imports at every nesting depth in every Python file below
   `tests/`, resolves absolute and relative module names, and rejects imports of
   any collected `test_*.py` module. Include synthetic nested, relative, aliased,
   allowed-support, and prefix-lookalike cases in the same grouped test using
   subtests. This gate covers static Python imports, not arbitrary computed dynamic
   imports; separately search and review `import_module`, `__import__`, and embedded
   subprocess scripts. Do not falsely claim comprehensive dynamic analysis.

The Anthropic move permits exactly three removed statements from the shared
context body: `global CURRENT_CASE`, entry `CURRENT_CASE = case`, and trailing
`CURRENT_CASE = None`. Preserve their ordering/normal-exit semantics in the
collected wrapper. All other moved helper statements remain AST-equivalent.

The architecture helper returns ordered `(line_number, resolved_module)` tuples.
Construct the set of collected module names from repository-relative `test_*.py`
files. Resolve relative imports against the importing file's package, treating
`__init__.py` as its package rather than an ordinary module. Check both the
ImportFrom base and its named children; resolve aliases by original name. Reject
exact collected module names or their dotted descendants, not string prefixes.
Sort/deduplicate diagnostics. Its fixed nine synthetic cases use collected module
`tests.test_x`: nested from-import, aliased import, package child import, relative
module import, relative child import, and parent-relative child import each fail
with the exact line/module tuple; support import and `tests.test_x_extra` pass;
an import inside a function in a support package fails. Case IDs A01–A09 follow
that order. A10 scans the real tree and must return no diagnostics. One collected
test function and ten subtests are added; expected combined total is 1,195 tests
and 1,339 subtests. No dynamic import enforcement is implied.

Allowed implementation writes: the three new support files, the new architecture
test, and six existing files: `test_v2_model_card.py`, `test_anthropic_binding.py`,
`test_gemini_destination_acceptance.py`, `test_ollama_destination_acceptance.py`,
`test_anthropic_auth_boundary.py`, `test_provider_redirect_acceptance.py` below
`tests/`. No production writes. Root owns plan, private validation scripts, and
progress documentation; delegated implementation ownership will partition files.

## Evidence and acceptance

Before edits, snapshot the exact source and approved plan to a fresh
`results/p2-slices/s1-02b/` directory, with exclusive-create receipts. Never replace
N's failed gate, previous frozen source, logs, or fault-control manifests. Record
the before and after inventory and require all changes to be in the allowlist.

Verify mechanically that existing test function identifiers, assertion ASTs,
scenario identifiers, and moved helper bodies are unchanged, except the explicit
context wrapper and import relocation. Document any mismatch and stop for plan
amendment rather than silently adjusting expected behavior.

Generate model-card fixtures from the trusted frozen old helper closure and the
new support module in separate fresh directories. Compare relative filenames and
exact bytes for default, alternate MCP model, alternate graph, invalid campaign,
running status, and contradictory summary variants. Loading the frozen closure
must not collect or execute tests; use exact-hash-verified definitions only.

Reviewed validation amendment: the real attempt builder obtains timestamps from
`campaign_runner._utc_now`. In the private differential check only, provide the
same literal `2026-08-30T12:00:00+00:00` clock to both builders, require equal
positive call counts per variant, and restore the clock in finally. Compare
unmodified artifact bytes, including fingerprints. The initial timestamp-only
failures remain diagnostics; production and fixture clocks are not changed.

Require fresh-process imports of the support modules with credential-file access,
socket/DNS, and process-spawn tripwires and zero observed external operations.
Run affected test modules, then freeze the combined source and run the complete
pytest suite, Ruff on src/scripts/tests, offline lock check, whitespace check,
and new AST architecture gate. All receipts bind unchanged before/after inventories.

Rerun the I, K, L, and N fault-control families on this combined source: expected
231 baseline passes, 16 intended failures, and 215 unaffected passes across 32
fresh processes. Private driver adaptations may change only evidence paths,
inventory bindings, and source-loader plumbing required by the approved extraction;
fault semantics and selected cases stay unchanged. Independently review adapted
drivers before execution. N's old-translator control remains bound to its original
pre-N translator hash, not the new restoration snapshot. Retain all failed attempts.

Independent review must inspect helper global closure, fixture byte parity,
CURRENT_CASE/monkeypatch compatibility, architecture coverage, assertion retention,
source binding, control outcomes, and zero external activity. Only after all gates
pass may root record combined acceptance and close N's pending architecture item.
Historic N evidence remains conditional and unchanged; the new receipt supersedes
it for acceptance without retroactively changing its result.

## Stop conditions and follow-on

Stop implementation for unexplained fixture differences, changed assertions or
scenario coverage, changed cleanup semantics, production edits, import cycles,
control failures inconsistent with the frozen baseline, or source drift during
validation. Resolve through a reviewed amendment. Missing external authority is
not needed for this slice. After acceptance, resume remaining offline cleanup and
accounting defects; merge, live-model qualification, and publication remain deferred.
