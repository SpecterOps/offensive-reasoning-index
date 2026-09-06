# S1-05J — Ollama request destination reconciliation

Status: revision 2 plus J-A1 implemented and independently accepted. September 6,
2026; bounded Stage 1
provider correctness work, not local-model qualification or campaign readiness.

## Entry and purpose

The accepted S1-05I boundary contains 167 source/test/script/lock files with
inventory SHA-256 `d94449bb649e873af64921abcc0d77b9448309cd75ea20635ad11c1a0c193d25`.
Full validation passed 1,167 tests/994 subtests. Preserve all existing edits and
the consolidated branch. Fetch, signing, commits, push, PR and merge remain
deferred. No actual provider, HTTP socket, BloodHound, model server, credential
file, host operation or inference load is permitted. Use in-memory HTTP transport
and synthetic configuration only. No reservations or external-agent dependencies.

Read-only audit established that Direct and native MCP use identical Ollama rules:
nonempty explicit URL, otherwise `OLLAMA_BASE_URL`, otherwise localhost port11434;
remove trailing slashes and one terminal `/v1`, then append `/api/chat`. They do
not strip `/api` or an existing `/api/chat`. An empty environment variable is
distinct from an unset one. V2 instead records model/default/inline configuration
and misses environment/default resolution. Inline `@URL` remains in the actual
model payload, so it is not a consistent supported inline-model feature.

Goal: one shared destination calculation, with an immutable prepared V2 selection
that gives provenance and both execution tracks the same destination. Preserve
normal routing, streaming, options, timeouts, telemetry and scoring behavior.

## Decisions and interfaces

Add dependency-light `src/ori/eval/ollama_binding.py`, importing only standard
library modules and the existing typed provider contract errors.

`OllamaEndpoint` is a frozen/slotted dataclass with `selected_base_url` and
`chat_url`, both excluded from repr. The selected URL is the original nonempty
input before normalization; retain it because passing an already-normalized root
through the adapter could strip a second `/v1` from `/v1/v1`.

`prepare_ollama_endpoint(base_url: str | None = None) -> OllamaEndpoint` selects
nonempty explicit input, else the environment value (including empty), else
`http://localhost:11434`. It uses private pure `_chat_url(selected: str) -> str` to remove trailing
slashes and exactly one terminal `/v1`, then append `/api/chat`. Empty or slash-only
selected inputs raise constant `ProviderCapabilityError` before any client is
constructed; they never fall back silently to another destination. Do not add
new hostname, scheme, port, query, credentials or path validation in this slice;
existing HTTP client behavior remains authoritative for all other strings.

`ollama_model_name(model: str) -> str` removes exactly the `ollama/` prefix and
requires a nonblank remainder with no `@`. Preserve spelling and whitespace of
otherwise nonblank slugs. `@` syntax fails with constant capability error and
instructions to use `model_base_url`, without echoing the supplied model or URL.
This intentionally converts a broken/ambiguous inline request into an early typed
failure. It does not add a new inline feature. Document this migration separately
from behavior-preserving normal URL extraction.

Direct calls prepare the endpoint once and use its `chat_url`; model payload uses
`ollama_model_name`. Native `_native_ollama_chat_url` becomes a thin delegation to
the same preparation function; `_ollama_chat_turn` uses the same model parser
before constructing a client. Keep both existing public/private callable
signatures. Do not change native stream processing or model-response metrics.
The Inspect V1 URL helper and telemetry's broader `/api` normalization remain
unchanged; they are not native Direct/MCP inference resolvers.

V2 adds private attributes `_ollama_endpoints: Mapping[str, OllamaEndpoint] | None`
and `_ollama_mutation_fingerprint: str | None` to the resolved config. They are not
serialized. `_prepare_ollama_endpoints` atomically prepares all Ollama models into
a `MappingProxyType` only after all succeed, including an explicit empty map when
none are selected. Input URL is model URL or defaults URL, otherwise environment
or localhost. Inline syntax is never considered a URL in the Ollama branch.

Preparation starts in `_model_readiness` before Anthropic credential preparation.
First validate all model API surfaces and Ollama slugs; then prepare Ollama-only
configuration. Existing Anthropic/Codex validation remains intact. A later other-
provider readiness failure does not discard already prepared Ollama endpoints.

The Ollama mutation projection is a sorted list of exact name, model, api_surface,
structured_output_mode and effective configured URL (model URL or defaults URL,
including null). Repeated preparation verifies this projection and reuses the
map without reading the environment. Reordering and irrelevant defaults change
nothing. Adding/removing/renaming/changing an Ollama model, including zero-to-one
  or one-to-zero transitions, requires a fresh resolved config. Environment changes
after preparation do not change that config's endpoint. A fresh load selects the
new environment and must produce different endpoint/runtime provenance.

`_ollama_endpoint(model, resolved)` is a pure accessor validating preparation and
the mutation projection; missing entries fail closed with constant campaign error.
`_model_base_url` returns the bound original `selected_base_url` for Ollama.
Both tracks pass this explicit nonempty value through their existing signatures,
so they cannot reselect an environment/default. `_provider_endpoint_fingerprint`
hashes `{'endpoint': bound.chat_url}` for Ollama, not the raw configured root.
This binds the final request path without adding raw URLs to public artifacts.
`_provider_identity` remains Ollama/no-key and must not perform discovery.

Add the new module to `_RUNNER_IMPLEMENTATION_SOURCES`. No new serialized field
or schema version is required: the existing implementation and runtime endpoint
fingerprints invalidate old readiness/resume/certification. Require fresh compiled
and certified artifacts and a fresh output directory for later real campaigns;
do not migrate or relabel old results. Status stays read-only and provider-free.

## Ownership and scope

- Runtime specialist: new binding module and only Ollama-specific adapter/native
  MCP edits and imports. Preserve the accepted Anthropic/Codex implementation.
- Root: V2 config/runner integration, documentation and evidence reconciliation.
- Test specialist: new `tests/test_ollama_destination_acceptance.py` only, using
  stable scenario IDs and existing test support. Do not weaken/remove old tests.
- Independent reviewer: adversarial plan review, full changed-function context,
  test-obligation review, negative controls and final frozen evidence acceptance.

All owners share a dirty checkout; no owner reverts another owner's changes.
No dependency changes, generic provider framework, broad formatting or unrelated
dead-code removal. Gemini, completion/content validation, redirect policy, retries
and token accounting remain separate slices.

## Finite acceptance tests

Use ten scenario functions with explicitly enumerated subtest IDs, not generated
Cartesian counts. Keep original regression tests unchanged.

1. `test_ollama_endpoint_selection`: R00 explicit beats env; R01 empty explicit
   uses env; R02 unset env localhost; R03 env remote HTTP; R04 custom port; R05
   IPv6; R06 trailing slash; R07 `/v1`; R08 `/v1/`; R09 `/api` preserved; R10
   `/api/chat` preserved; R11 `/v1/v1` removes only one; R12 empty env rejected;
   R13 slash-only env rejected; R14 slash-only explicit rejected. Assert exact
   selected input and final URL, typed failure and no client/network allocation.
2. `test_ollama_model_contract`: M00 ordinary; M01 namespaced/tagged slug; M02
   spelling preserved; M03 blank; M04 empty; M05 inline suffix; M06 `@` with an
   explicit destination still rejected. Invalid rows fail before client creation.
3. `test_ollama_direct_native_request_parity`: P00 explicit; P01 env; P02 default;
   P03 `/v1/v1`; P04 IPv6. Each row drives the real Direct adapter and native
   Ollama turn with in-memory HTTP transport, captures actual HTTP URLs/model
   payload/options and synthetic terminal token counters, and verifies closure.
   Use `_native_ollama_chat_url` for the native URL, not a handcrafted final URL.
4. `test_ollama_v2_preparation`: V00 model beats defaults/env; V01 defaults beats
   env; V02 env; V03 localhost; V04 bad second model leaves no map; V05 none is
   prepared empty map; V06 subsequent provider readiness failure preserves map.
5. `test_ollama_v2_immutable_selection`: I00 env rotates after preparation;
   I01 repeated readiness does not read env; I02 pure accessor does not read env;
   I03 fresh config uses changed env; I04 unprepared access rejected. Trap only
   Ollama environment lookups so unrelated provider code is not artificially
   forbidden. Assert no provider/client/graph operations during preparation.
6. `test_ollama_v2_mutation_boundary`: C00 rename; C01 model; C02 API surface;
   C03 structured mode; C04 inherited defaults URL; C05 add from empty;
   C06 remove last; C07 provider switch; C08 reorder accepted; C09 shadowed
   defaults URL change accepted; C10 unrelated provider mutation accepted.
   C02/C03 start from valid prepared input, then use test-only `model_copy(update=...)`
   to create a Responses/JSON-Schema mutation without weakening production config
   validation. Call the pure accessor, expect the mutation guard's constant error,
   and verify the original map object is unchanged with no environment lookup or
   rebind. These tests prove mutation detection, not admission of those settings.
7. `test_ollama_v2_dispatch_binding`: D00 actual `_run_model` Direct dispatch;
   D01 actual `_run_model` MCP dispatch. Prepare on endpoint A, change environment
   to B, capture the passed explicit URL at runtime, then cross the real adapter
   or native request using that captured value. The final HTTP URL must equal A's
   bound `chat_url` and endpoint fingerprint. Stub graph/tool work only; don't
   turn this into an entirely mocked runner or omit the actual provider request.
8. `test_ollama_provenance_compatibility`: F00 root and root `/v1` produce same
   endpoint hash; F01 different resolved paths differ; F02 fresh environment
   change alters actual runtime provenance; F03 source fingerprint includes new
   module; F04 private attrs absent from serialization; F05 public model-card
   projection excludes raw endpoint/path; F06 mismatched provenance rejected by
   existing run-directory guard. Do not claim full runtime provenance equality
   for different source configs just because final endpoint hashes agree.
9. `test_ollama_failure_and_legacy_boundary`: B00 Direct invalid inline produces
   typed provider capability result; B01 native invalid inline typed exception;
   B02 empty env typed failure; B03 legacy Direct `call_model` success retains
   text/tokens; B04 existing native stream error behavior retained. No fabricated
   live server-readiness or inference availability claims.
10. `test_ollama_pure_identity_and_compatibility`: X00 family/no-key identity;
    X01 model-free readiness; X02 unchanged Inspect `/v1` behavior; X03 unchanged
    telemetry `/api` behavior. Verify actual helpers without modifying them.

Total: ten functions, 68 enumerated subtest cells. Confirm the arithmetic and
collection before accepting the plan; no hiding lost obligations in broad loops.

Test setup clarification after approval: B04 uses synthetic HTTP 500 and preserves
the existing `httpx.HTTPStatusError`, not new JSON error-object handling. V05/C05
use a valid config containing a non-Ollama model, not an invalid zero-model config.
Rejection applies to empty/slash-only selected input; `/v1` itself is not a new
grammar rejection, even though its final relative request may fail in HTTPX.

## Fault controls and evidence

Five fresh baseline/control process pairs, scoped process-local substitutions.
The test module exposes `CURRENT_CASE`, set on entering each cell and reset in
finally; controls import the real module under its pytest collection name before
collection and patch production symbols for that process only. Every wrapper
delegates unchanged except at its exact target ID. Restore in finally even when
pytest returns failure. Baseline applies no substitution. Exact controls:

- FC1: patch `ollama_binding.prepare_ollama_endpoint(base_url=None)` for R00 only,
  delegate original with `None` instead of the explicit input. Exactly one
  substitution; R00 fails selected-input equality; R01–R14 all execute and pass.
- FC2: patch `ollama_binding._chat_url(selected)` for R11 only, call original then
  replace terminal `/v1/api/chat` with `/api/chat`. Exactly one substitution;
  final-URL equality fails, R12–R14 execute and pass.
- FC3: patch `campaign_runner._ollama_endpoint(model, resolved)` for I00 only.
  First call original to retain preparation/mutation checks, then return
  `prepare_ollama_endpoint(None)` using changed synthetic environment B. Exactly
  one accessor substitution in I00; prepared-A equality fails. I01–I04 pass.
- FC4: patch `campaign_runner._model_base_url(model, resolved)` for D01 only,
  return `None` instead of bound A. Exactly one substitution during the actual
  runner attempt; native inference resolves changed B. Let the synthetic HTTP
  request finish before asserting captured/final destination equality, so failure
  demonstrates actual wrong routing. D00 must pass. D01 is last: claim no later
  continuation cell for this control; require both visited IDs and the intended
  failed subtest plus parent accounting.
- FC5: patch `campaign_runner._provider_endpoint_fingerprint(model, resolved)`
  for F02 only, return `"0" * 64` on exactly two calls. Build actual `_provenance`
  twice from the same source-config fingerprint/model/prepared-track fixtures and
  freshly prepared resolved configs differing only in synthetic environment A/B.
  Assert differing `runtime_config_fingerprint` before overall provenance
  inequality. That runtime assertion must fail, not a shape or earlier endpoint
  assertion. F03–F06 execute and pass.

Record exact visited IDs, intended assertion location, substitution counts and
zero external-network counter. Each control has exactly one intended failed
subtest, no unrelated failure/error; all other cells of its parent pass. Parent
failure due solely to that subtest is expected, not an extra failed obligation.
Do not edit production files for controls or overwrite earlier diagnostic logs.

After plan approval, snapshot all 167 source files and the approved plan. After
implementation/review corrections, freeze the complete source inventory and
record before/after hashes for each acceptance run. Source changes invalidate
that run. Run the ten new scenarios plus existing provider, MCP, Inspect, V2
runtime/config, Anthropic and Codex regressions; then full pytest, Ruff over
src/scripts/tests, `uv lock --check --offline`, and `git diff --check`. Retain
command exits, complete logs and hashes. Run the five final fresh control pairs
against the same frozen inventory. Independently reconcile all results before
calling S1-05J accepted. No test-count reduction or Stage 1 completion claim.

## Stop conditions and handoff

### Approved amendment J-A1: native-loop keyword compatibility

The real D01 integration test found that V2 puts `max_tokens` in shared loop
kwargs, but native Ollama does not accept that keyword. This raises a harness
failure before any HTTP request. The correction belongs to root in
`src/ori/eval/v2/model_runtime.py`: remove only that key from the shared kwargs
and pass `max_tokens=max_tokens` explicitly to the OpenAI-compatible loop.
Keep Ollama's existing `ollama_options` handling unchanged; do not introduce a
new `num_predict` default or alter its sampling/output policy in this slice.
No loop signature change or ignored catch-all kwargs is permitted.

Retain the exact 68 cells. Strengthen D00/D01 to set explicit synthetic
`ollama_options.num_predict` and assert that the actual HTTP body preserves it;
retain the real native loop rather than filtering keywords in a test wrapper.
Existing OpenAI-compatible output-limit tests must pass, and independently
review the changed production call sites before final acceptance. Add
model_runtime.py to this slice's source ownership and freeze allowlist only after
independent approval. Original pre-edit snapshot already includes that file.
This amendment passed independent review and does not claim a full
cross-provider output-budget policy fix.

Stop the affected slice for any unresolved review finding, unowned source change,
moving-source validation, native compatibility regression, untyped preparation
failure, environment/provenance divergence, missed negative control, leaked raw
endpoint in public output, or accidental external call. Preserve diagnostics and
resolve locally where possible. Do not touch deferred operations to bypass a gate.
After bounded acceptance, update the progress ledger and prepare the separate
Gemini fixed-endpoint plan; do not combine either with hosted admission or MCP
implementation expansion.

## Accepted completion evidence

Independent final review accepted S1-05J and J-A1 on the unchanged 169-file
inventory `b916a64d49e2ce5abf5b991f2da7af9307f280f510e0b71dd7c31b8ea1f3e3fc`.
The full suite passed 1,177 tests/1,062 subtests in 224.90s; focused validation
passed 164 tests/443 subtests. Ruff, offline lock and whitespace checks passed.
The five final fresh control pairs passed 44 baseline cells and caught exactly
five intended failed cells with 39 other cells passing. Recorded external-network
counters remained zero. Full-suite log SHA-256:
`f17f5ed72305d7ce68908f3e281fab476317fa11362234ab496053c71e10154e`.

Private evidence under `results/p2-slices/s1-05j/` retains pre-edit source,
approved plan, frozen inventory, five gate receipts/logs, authoritative
`a49b475678e6` controls, root reconciliation and subsequent independent-review
receipt. Earlier `48fabd38e460` evidence and driver remain preserved but excluded.
The controls instrument the actual pytest-collected module instead of importing
a duplicate module before collection; target IDs and production substitutions
are unchanged. This accepts the bounded slice, not Stage 1, live qualification
or campaign readiness. Gemini reconciliation is the next separately planned slice.
