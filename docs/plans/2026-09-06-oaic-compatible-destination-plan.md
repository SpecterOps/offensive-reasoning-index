# S1-05D — explicit compatible destination precedence

Revision 2, September 6. Status: complete, with independent plan, implementation
and final evidence approval. This narrow
implementation slice follows the completed S1-05A audit; it closes only the
explicit-versus-inline compatible Direct destination mismatch in F01.

## Decision and scope

The caller's nonempty `base_url` wins over the endpoint embedded in
`openai-compat/model@URL`. Strip the inline suffix from the requested model in
either case. With no nonempty explicit URL, retain the inline URL. With neither,
retain the existing typed capability failure. Do not add environment fallback to
the standalone adapter: campaign resolution already supplies it, and changing
standalone behavior is outside this defect. Preserve empty-string-as-absent and
the existing first-`@` model split. URL grammar validation is separate work.

This matches established V2 `_model_base_url` and native MCP configured-first
precedence for ordinary endpoints. Do not rewrite URL paths, normalize hostnames,
change credential selection, SDK settings, model response handling or public
schemas. Conflicting old Direct configurations intentionally change destination
to the caller's explicit URL. All non-conflicting configurations retain request behavior.

The adapter file is already included in the V2 runner implementation fingerprint.
Verify that binding in tests; no manual version bump or compatibility bypass.
Changing this shared runtime fingerprint invalidates resume for every old-runtime
V2 campaign, not only conflicting Direct configurations. Require fresh output
directories and readiness for all such campaigns, plus any certification refresh
required by their artifact fingerprints. Never resume or relabel old evidence.
Multi-`@` parsing and native URL-suffix normalization remain outside this ordinary-
endpoint precedence claim.
This slice does not close other F01 endpoint/environment discrepancies (Anthropic,
Gemini, Codex, Ollama), F02 credential policy, F03–F06 or the full native-loop audit.

## Ownership and edit boundary

Root owns the production change in `src/ori/eval/adapter.py`, tests in
`tests/test_provider_adapter.py` and `tests/test_provider_v2_config.py`, and this
plan/progress/audit disposition documentation. Independent reviewer owns plan
challenge and final diff/evidence acceptance, read-only. Preserve all previous
dirty changes. No other production module changes are allowed in this slice.

## Implementation

In the compatible branch, split into `name, inline_base`, then assign
`resolved_base = resolved_base or inline_base`. Keep credential resolution after
that assignment. Keep all other provider branches and request bodies unchanged.
Extend the local fake-SDK test helper with an optional model argument defaulting
to its exact existing value; no existing caller or assertion is removed.

Add one parameterized adapter regression covering:

1. Explicit OpenRouter plus inline Nous: OpenRouter destination/key wins.
2. Explicit Nous plus inline OpenRouter: Nous destination/key wins.
3. Explicit localhost plus inline OpenRouter: localhost and `not-needed`, never
   either hosted key.
4. Absent explicit plus inline Nous: existing inline behavior and Nous key.
5. Empty explicit plus inline Nous: same fallback.
6. Identical explicit/inline OpenRouter: unchanged destination/key.

Every row must assert client base URL and API key, model suffix removal, exact
user/system payload preservation, successful response text, and family/source
metrics. Clear all relevant key and endpoint environment names, then set only
synthetic family sentinels. SDK construction is replaced before calls.

Add a V2 config-to-adapter regression with real typed config/default resolution,
`_model_base_url`, `_provider_identity` and `_provider_endpoint_fingerprint`, then
the real adapter behind the fake SDK. Exercise model-explicit, defaults-explicit
and inline-only URL sources. Assert the URL used by the fake client equals the
readiness helper's URL, credential family/source agree, actual requested model is
suffix-free, and changing the effective endpoint changes the endpoint fingerprint.
Do not call full readiness, graph, login or provider discovery. Do not import
another collected test module; keep the small fake local or in existing support.

## Validation and acceptance

Capture exact source/test inventory before edits and test collection. First add
the regression tests and run them against the unchanged adapter: conflicting
explicit cases must fail for the destination mismatch. Existing fallback/equal
cases must pass. Record the failing assertions privately. Then implement and
rerun both modules plus all five S1-05A primary modules. No test deletion, fewer
assertions or relabeling a failure as success is permitted.

Run complete pytest and Ruff across `src scripts tests`, frozen-lock validation
and `git diff --check`. Capture full log and source hashes before/after validation;
source cannot change during the final run. Independently inspect the exact diff,
test intercepts and receipts. Acceptance requires all new/existing tests pass and
the reviewer approves the narrow F01 disposition. Full-suite counts are reported
as observed, not forecast. Tests are model-free; no live credential or network use.

## Stop and carry-forward conditions

Stop on source drift outside ownership, a test reaching real SDK transport or
credential discovery, unexpected changes to other providers, or a need to change
shared endpoint grammar. Return to plan review if implementation expands beyond
the two-assignment correction. Remote Git/signing/PR/merge, model calls, live graph
checks and host/service changes remain deferred. This fix does not certify a
campaign, authorize spending or establish public-release readiness.

## Completion evidence

Pre-edit inventory matched S1-05A's 160-file boundary. Before implementation, the
nine new cases produced five expected destination failures and four control
passes. After implementation, 101 focused tests passed in 1.30s. Full pytest:
1,120 tests and 233 subtests passed in 251.04s. Ruff, offline lock validation and
diff checks pass. All original test bodies remain unchanged except the approved
additional fingerprint-source assertion. All 160 validation-start/end file hashes
and inventory membership match. Final independent reconciliation approved narrow
closure; no other F01 case or F02–F06 finding is closed.

Validation inventory SHA-256:
`3f8e5543a9b400dc073e77297cd88e773828b58392e008ce59fdb92c2caf8008`.
Full-suite log SHA-256:
`923c60f7cc9f5cfb96791869e2312caf64e0d3f1294164f93d30f982fbcf4cd0`.
Logs and source snapshots remain private. No external operations were performed.
