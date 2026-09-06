# S1-05K — Gemini fixed-destination provenance

Status: implemented and independently accepted. September 6, 2026.

## Entry, objective and scope

S1-05J is independently accepted: 169 source/test/script/lock files at
`b916a64d49e2ce5abf5b991f2da7af9307f280f510e0b71dd7c31b8ea1f3e3fc`,
with 1,177 tests/1,062 subtests passing. This is bounded Stage 1 correctness work,
not a substitute for the remaining cleanup, task library, three-MCP integration,
local qualification, scorecard, campaign or conference requirements.

The actual Gemini Direct adapter always supplies the SDK base URL
`https://generativelanguage.googleapis.com/v1beta/openai/`, but V2 records a
model/default/inline URL or nothing. Reconcile metadata with existing execution
without changing routing, credentials, model spelling or inference semantics.

Preserve the consolidated dirty branch and all other changes. No commits,
signing, remote Git, PR/merge, real provider/HTTP socket/graph calls, credential
file operations, local-model loads or host/service operations. Only synthetic
configuration and real SDK requests routed into in-memory HTTPX transport.
No agent/reservation/private-machine dependency is permitted.

## Exact design and compatibility

Root adds `GEMINI_OPENAI_BASE_URL` to existing `provider_contract.py` with the exact
string above, including the trailing slash. This is a wire endpoint constant,
not a new provider class or binding subsystem. Import it in `adapter.py`, replacing
only the Gemini URL literal. Import it in V2 `campaign_runner.py`; return it from
the Gemini branch of `_model_base_url` before generic inline extraction.

The existing endpoint fingerprint hashes that SDK base URL. It does not claim
to hash the final HTTP request URL, which appends `chat/completions`. Direct must
still receive the same model prefix handling, messages, output limit, structured
schema, timeout and scoped `GEMINI_API_KEY`. No environment-based Gemini endpoint,
custom credentials, model-name normalization or new credential lookup is added.

Preserve ignored explicit/default URLs: a global defaults URL in a mixed-provider
matrix must not newly reject Gemini or redirect its credential. Preserve the
existing literal `@URL` model-name payload behavior; document that inline Gemini
routing is unsupported and operators should provide the exact model ID. Do not
introduce validation/migration changes under the guise of constant extraction.
Existing unsupported Gemini MCP and explicit Responses selections still fail.

No serialized schema changes: the existing runtime source fingerprint already
includes provider_contract.py, adapter.py and campaign_runner.py. Changed runtime
provenance invalidates old readiness/resume evidence; later campaigns need fresh
certification/readiness and a fresh output directory. Endpoint-hash equality does
not make distinct source configurations equivalent overall.

Do not change SDK ownership/cleanup, response parsing, retries, redirects or
token accounting. Test fixtures explicitly close the SDK clients they allocate;
that cleanup is not evidence that production currently closes those clients.

## Ownership and sequence

1. Root writes this plan; independent reviewer challenges decisions and controls.
2. On approval, root captures all 169 pre-edit files and the approved plan.
3. Root owns the three production files and public documentation. Test specialist
   owns only new `tests/test_gemini_destination_acceptance.py` and private control
   drivers. No existing test is removed or weakened. Owners share the worktree
   and must preserve others' edits.
4. Independent source and test review; resolve findings, then freeze full source.
5. Full regression, focused tests, Ruff, offline lock, whitespace and controls on
   the same frozen inventory; independently reconcile evidence and update ledger.

## Finite tests: four scenario functions, 20 explicit cells

All cases have stable IDs and fresh isolated environment/config/client state.
Trap socket connect/connect_ex/getaddrinfo and subprocess creation. Use the real
OpenAI SDK with a constructor wrapper supplying HTTPX MockTransport, explicit
synthetic Gemini key and a nonstreaming successful chat-completion JSON receipt.
Record actual request URL, header credential and JSON body; never contact Google.
Each case closes its fixture-owned clients in finally. Mark that ownership in
counters to avoid claiming production cleanup. No private operator files read.

1. `test_gemini_destination_parity`, D00–D06 (7): absent URL; model URL; defaults
   URL; model+defaults precedence inputs; inline URL only; inline+explicit URL;
   Gemini/OpenAI-compatible/OpenAI environment URLs set. Build actual typed V2
   config and run `_model_readiness`, `_model_base_url`, endpoint fingerprint,
   then actual Direct adapter request. Exactly one explicit `_model_base_url`
   call plus its one internal fingerprint call per cell. Assert independently
   literal expected SDK base and final HTTP URL, not only equality of values
   derived from the shared constant. Assert exact submitted model (including
   literal inline suffix where present), fixed credential destination, source
   family, token counts and one inference request. Configured and environment
   distractors must never receive a request.
2. `test_gemini_scoped_credentials`, C00–C03 (4): Gemini key plus all competing
   provider keys; missing all keys; empty Gemini key plus competing keys; a fresh
   invocation after synthetic Gemini key rotation. C00/C03 assert actual wire
   Authorization header uses only Gemini key, never competitors. C01/C02 assert
   zero SDK constructors/requests, typed authentication failure and constant safe
   error. Identity/readiness must report the expected key source or reject before
   any client. C03 makes two requests and checks both distinct synthetic keys.
3. `test_gemini_request_compatibility`, P00–P03 (4): exact namespaced/case-sensitive
   model plus system/user messages and explicit 8192 output limit; public JSON
   response schema plus explicit 7.5-second SDK timeout; explicit Responses
   rejected by V2 readiness; MCP selection rejected by actual V2 config parser.
   P00/P01 cross real SDK payload capture; P02/P03 make zero clients/requests.
   Keep tool use unsupported; no fabricated capability canary.
4. `test_gemini_provenance_compatibility`, F00–F04 (5): different ignored configured
   URLs produce equal fixed-endpoint hashes; actual `_provenance` with distinct
   source fingerprints remains distinct despite that endpoint equality; all three
   changed modules participate in runtime source fingerprint; old/incompatible
   run provenance rejected without overwriting guard; actual public model-card
   builder on valid synthetic two-track evidence excludes private endpoint/config
   files and outputs only its two public assets. F04 proves export isolation, not
   Gemini MCP support. Use literal synthetic private strings as leak sentinels.

## Three precise negative controls

Fixture clarification: P01 uses a strict `synthetic_submission` schema envelope
requiring one string `answer`, with additionalProperties false. Assert the exact
JSON-schema response_format, constructor timeout 7.5 and actual HTTP timeout
extensions, not elapsed time. P03 first supplies otherwise valid MCP paths/config
and matches the unsupported-provider error. C01/C02 assert the adapter's constant
`Gemini requires GEMINI_API_KEY` separately from model-qualified readiness errors.
SDK fixture closure runs in the same event loop's finally; count `fixture_close`,
not production cleanup. D06 sets GEMINI_BASE_URL, OPENAI_COMPAT_BASE_URL and
OPENAI_BASE_URL to distractors.

Instrument the actual pytest-collected test module using `CURRENT_CASE` and its
case context, not a duplicate import. Restore production symbols in finally.
Every baseline/control pair starts a fresh interpreter and uses unique files.

- FC1: D01 only, patch `campaign_runner._model_base_url(model,resolved)` to return
  the explicit distractor URL, exactly twice (direct lookup and fingerprint).
  The SDK still sends one request to Google-shaped in-memory URL. Run the request
  before the literal expected-base assertion; that assertion must fail. D00 and
  D02–D06 pass and all seven IDs are visited.
- FC2: D02 only, patch `adapter.GEMINI_OPENAI_BASE_URL` to the synthetic distractor
  URL for one case. Its single SDK request goes to that wrong mock endpoint; the
  literal expected-final-HTTP-URL assertion must fail. V2 metadata remains fixed.
  All other D cells pass, including D03–D06. This proves request and metadata are
  not both asserted only against the same accidentally modified constant.
- FC3: F00 only, patch `campaign_runner._provider_endpoint_fingerprint(model,resolved)`
  to hash the configured model/default URL, exactly twice. Independently valid
  configs use distinct distractors, so the expected equality assertion fails;
  F01–F04 pass. Do not replace whole provenance or claim source-config equality.

Each control has exactly one intended failed subtest and the corresponding parent
failure; no unrelated failures. Baselines pass all 19 cells (7+7+5); controls catch
three defects while the other 16 cells pass. Record visited/later IDs, exact
assertion, substitution count (FC2 one constant replacement), actual synthetic
requests, zero outer/per-case external calls, full discovered source path set and
hashes before/after, driver/log/receipt hashes and exit codes. Preserve diagnostics.

## Acceptance and stop conditions

Focused: new four scenarios plus provider adapter/auth/runtime acceptance, V2
provider config/runtime, MCP, Anthropic and Ollama destination tests. Full pytest;
Ruff src/scripts/tests; `uv lock --check --offline`; `git diff --check`. Do not
claim passing baseline results as evidence for changed source. A changed inventory
invalidates the gate; rerun on the new frozen boundary. Reviewer verifies real
calls, exception classes, counters and assertion coverage, not counts alone.

Stop the affected work for unreviewed design changes, unowned edits, weaker
credential isolation, request/provenance mismatch, public leakage, unknown failure,
moving-source gates or accidental external operation. Investigate locally and
preserve evidence. No final campaign or publication claims; merged code and fresh
live qualification remain deferred gates. After acceptance, return to remaining
provider review findings and the broader Stage 1 test-reduction/cleanup ledger.

## Accepted completion evidence

Final independent review accepted the bounded slice on 170 unchanged source/
test/script/lock files, inventory
`83851473ebb2c4e154089026851a44df43360b64d3f48762d5fc74ef171d9a5e`.
Full suite: 1,181 tests/1,082 subtests in 222.42s. Focused: 167 tests/464 subtests
in 3.24s. Ruff, offline lock and whitespace gates passed. The three fresh control
pairs passed 19 baseline cells and caught exactly three intended failures while
16 unaffected cells passed; recorded external-call counters were zero.

Full-suite log SHA-256:
`c16738e7a46da8d582da903ac73fdffb5160792cd047852c2bdf20e2602cb4f9`.
Private evidence under `results/p2-slices/s1-05k/` retains pre-edit source, plan,
frozen inventory, gate receipts/logs, authoritative `ed1225690bdb` controls, root
reconciliation and subsequent independent-review receipt. This does not establish
provider availability, production SDK cleanup, Stage 1 completion or campaign
readiness. Redirect containment, terminal integrity, tool-call identity and
meaningful test consolidation remain open next work.
