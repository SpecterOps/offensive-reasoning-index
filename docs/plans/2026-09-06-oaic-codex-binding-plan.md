# S1-05G — Codex destination, credentials and readiness binding

Revision 3, September 6. Status: complete; independent final evidence review
approved this bounded slice. Broader Stage 1 remains open.
The pre-edit gate verified all 163 files and collected exactly 1,141 tests.

## Evidence, dependency and exit condition

S1-05F is independently accepted: 1,141 full-suite tests, 65 focused tests/134
subtests and unchanged 163-file inventory
`f1df7bbf70e570a965b6256f22fc0753e940ea820aad2c66102ccef26a19fd74`.
Root reproduced two remaining defects through actual Direct and native MCP
consumers with a fake SDK: both attach a synthetic OPENAI_API_KEY to an arbitrary
custom Codex URL; two different CODEX_BASE_URL values produce different runtime
destinations but the same V2 endpoint fingerprint and false login-source label.
No provider, credential-file, network, graph or subprocess operation was used.

Close only the initial Codex destination/source portions of audit F01/F02.
Retain custom Codex-compatible endpoints with explicit credential opt-in. Do not
claim redirect containment, remote capability validation, general URL security,
Anthropic/Ollama/Gemini parity, response-terminal correctness or full Stage 1.
This is a deliberate security migration, not behavior-identical cleanup: implicit
cross-provider credentials and unsafe built-in-endpoint variants will stop working.

## Ownership and sequence

Root owns production changes in codex_oauth.py, adapter.py, mcp_runtime.py and
v2/campaign_runner.py under src/ori/eval; this plan, progress/review ledgers and
examples/inference/README.md. No source schema/model-card/compiler changes.

s1d_runtime_tests owns tests/test_codex_oauth.py, tests/test_provider_adapter.py
and new tests/support/codex_destinations.py. The test auditor owns new
tests/test_codex_destination_acceptance.py, tests/test_provider_v2_config.py,
tests/test_v2_runtime.py and tests/test_provider_runtime_acceptance.py. Existing
shared fixtures remain unchanged except the explicitly named helpers below.
Every participant preserves unrelated and earlier edits. The independent reviewer
owns read-only plan, implementation and final evidence challenge.

Sequence: complete challenge → exact pre-edit snapshot/collection → new tests and
expected red failures → shared binding API → both consumers → V2 identity/readiness
→ compatibility adjustments → focused tests/controls → independent review → full
pytest/Ruff/offline lock/diff/inventory reconciliation → documentation and closure.
No commits, signing, fetch/push, PR/merge, live service or model operations.

## Destination policy

Preserve effective URL precedence: explicit consumer argument; inline model suffix;
CODEX_BASE_URL; built-in DEFAULT_CODEX_BASE_URL. V2 first chooses model URL over
defaults URL, then delegates the remaining precedence to codex_request_base_url.
Preserve existing trailing-slash removal. Do not rewrite a custom API path.
Reject a selected nonempty URL that becomes empty after trailing-slash removal
before another resolver can mistake it for an absent value. Genuinely absent or
empty explicit arguments retain the existing fallback precedence.

Admission uses urlsplit only, with no DNS. Require nonempty hostname, http/https,
no username/password (including empty userinfo), no query/fragment, no backslash,
no ASCII whitespace/control character, a valid parsed port and no empty explicit
port. Catch parsing/port ValueError as ProviderCapabilityError with constant
`Codex endpoint configuration is invalid`; do not include the URL in the error.

The recognized built-in hostname is exact case-insensitive chatgpt.com. It requires
HTTPS, absent/443 port and the exact case-sensitive path /backend-api/codex after
existing trailing-slash removal. Any other scheme/port/path on that hostname is
rejected, not downgraded to custom admission. Subdomains and other hostnames are
custom, never eligible for built-in credentials.

Custom endpoints may use HTTP or HTTPS and valid custom ports, including remote
machines and IPv6. HTTP support requires the dedicated explicit custom key below;
it is operator-selected plaintext transport, not an automatically secure channel.
This preserves public CLI/custom deployment capability without private-host or
agent-system dependencies. No certificate, redirect, DNS or server probe occurs.

## Credential and Python interfaces

All new helpers live in codex_oauth.py, with no new dependencies:

1. Frozen/slotted CodexEndpointBinding holds base_url, endpoint_family and
   credential_source; no secret fields. `codex_endpoint_binding(base_url=None)`
   resolves/adopts the URL, validates admission, then selects source metadata.
   Built-in family is `codex_oauth`; custom family is `codex_compat`.
2. Built-in endpoint: nonempty CODEX_API_KEY wins; otherwise choose the existing
   configured/default OAuth file, source name `codex-auth-file`. OPENAI_API_KEY,
   OPENAI_COMPAT_API_KEY and CODEX_COMPAT_API_KEY are never fallback sources.
3. Custom endpoint: only nonempty CODEX_COMPAT_API_KEY is eligible. Missing key
   has source None and execution raises ProviderAuthenticationError with constant
   `Custom Codex endpoint requires CODEX_COMPAT_API_KEY`. Never inspect OAuth files
   or borrow CODEX_API_KEY/OPENAI_API_KEY, even when they are present.
4. Frozen/slotted CodexCredential holds the selected binding and token, with token
   repr=False. `resolve_codex_credential(base_url=None)` obtains exactly one binding
   and loads only its selected source. Source-selection metadata is not evidence
   of a readable file or successful authentication. Identity/provenance never call
   this secret-loading helper.
5. Move S1-05F's file parsing unchanged into a private file-token helper. Preserve
   its error categories/messages, nested/top-level precedence and falsey fallback.
   `codex_access_token(*, base_url=None)` returns the resolved token; existing
   zero-argument callers remain valid, with the documented credential migration.
6. CodexCredential.headers(thread_id=None) constructs the existing header fields
   from its already-selected token, user-agent/session/installation helpers.
   Existing `codex_headers(*, thread_id=None, base_url=None)` is a compatible wrapper
   around resolution plus this method. Never reread credentials merely to derive
   metadata or populate the SDK key and Authorization separately.

Direct and native MCP first resolve their URL, resolve one credential, derive
headers and SDK key from that same object, and record its safe family/source.
They pass the exact resolved URL to the SDK. Retain model suffix removal, options,
stream translation, timeouts, client cleanup and S1-05F exception handling.
Do not create a normalized MCP tool facade or alter native tool behavior.

## V2 identity, readiness and compatibility

_model_base_url delegates the Codex environment/default fallback to the shared
resolver, so _provider_endpoint_fingerprint hashes the same destination used by
execution. _provider_identity uses only pure binding metadata, not credentials,
login, filesystem or clients. Existing unsupported API-surface checks remain first.

For V2 Codex, reject an empty/whitespace post-prefix, pre-@ model slug before any
credential acquisition. Use the existing _codex_model_slug boundary and validate
from identity/readiness; do not allow CODEX_DEFAULT_MODEL to silently change a
certified campaign. Legacy standalone default-model behavior remains unchanged.

Readiness branches on the selected source:

| Source | Credential check | Capability check |
| --- | --- | --- |
| CODEX_API_KEY, built-in | presence only, source name | configured-not-probed; no CLI/cache/file access |
| codex-auth-file | validate actual selected file, then existing local login check | existing local model-cache/effort checks |
| CODEX_COMPAT_API_KEY | presence only, source name | configured-not-probed; no CLI/cache/file access |
| Custom source absent | typed failure before acquisition | not run |
| Invalid endpoint or empty slug | configuration failure before acquisition | not run |

OAuth credential_check is `codex-auth-file+codex-login-status`. Cache remains the
existing local cache, and only its existing local capability assertion is made;
it is not proof of remote access or that an overridden auth file matches the CLI
account. Validate the actual selected file for each applicable model, even when
login/cache checks are shared once. All branches record requested reasoning effort;
only the cache-checked OAuth branch claims advertised effort support. No branch
claims model access, remote schema support or authentication success.

Translate ProviderContractError to constant/path-free V2CampaignRunError at the
readiness boundary; raw token/file/endpoint content must not enter summaries.
The Codex branch of _provider_identity likewise translates admission errors to
V2CampaignRunError for provenance/status callers, retaining unsupported-surface
precedence. Credential loading remains outside identity; readiness separately
translates file-acquisition failures with the existing safe authentication text.
Pure identity may report candidate source metadata even if that source is missing
or unreadable. Key presence is checked by readiness/execution, not provenance.

Existing receipt fields are strings; retain their schemas. Source-name and
effective-endpoint changes alter runtime/provenance fingerprints. Token values
and auth-file paths never enter hashes or public exports; rotation within one
source does not redefine campaign identity. This slice assumes fixed process
configuration and does not freeze arbitrary in-process environment mutation.
Use fresh output/readiness and renewed stale certification; never migrate or
resume historical campaigns past the changed runtime fingerprint.

## Finite test catalog

Immutable named cases in tests/support/codex_destinations.py contain only synthetic
URLs and expected family/admission; no executable helpers or secrets.

O0 built-in exact; O1 uppercase hostname with :443 and trailing /; O2 built-in
with multiple trailing slashes. All normalize only through established resolution.
C0 https://proxy.invalid/codex; C1 http://127.0.0.1:8080/codex;
C2 http://gpu.invalid:8080/codex; C3 http://[::1]:8080/codex;
C4 https://proxy.invalid:8443/custom/path; C5 https://api.chatgpt.com/codex.
Raw rejection vectors are literal strings; Python escape notation below denotes
actual characters, not a backslash followed by a letter. Check raw input before
urlsplit, rejecting codepoints 0–32 inclusive and 127 anywhere, plus backslash.

| ID | Literal URL |
| --- | --- |
| X0 | `http://chatgpt.com/backend-api/codex` |
| X1 | `https://chatgpt.com:8443/backend-api/codex` |
| X2 | `https://chatgpt.com/not-codex` |
| X3 | `https://user:pass@proxy.invalid/codex` |
| X4 | `https://proxy.invalid/codex?mode=test` |
| X5 | `https://proxy.invalid/codex#fragment` |
| X6 | `https://proxy.invalid:65536/codex` |
| X7 | `https://proxy.invalid:/codex` |
| X8 | Python `"https://proxy.invalid/codex\\path"` |
| X9 | `http://[::1/codex` |
| X10 | `https:///codex` |
| X11 | `ftp://proxy.invalid/codex` |
| X12 | Python `"\thttps://proxy.invalid/codex"` |
| X13 | `https://@proxy.invalid/codex` |
| X14 | `https://proxy.invalid:invalid/codex` |
| X15 | Python `"https://pro\nxy.invalid/codex"` |
| X16 | Python `"\x00https://proxy.invalid/codex"` |
| X17 | Python `"https://proxy.invalid/co\x7fdex"` |
| X18 | `/` |
| X19 | `///` |

There are nine valid and 20 rejected destinations: 29 total.

Source environments K0 none; K1 CODEX_API_KEY only; K2 OPENAI_API_KEY only;
K3 CODEX_COMPAT_API_KEY only; K4 all those plus OPENAI_COMPAT_API_KEY.
Each token is a distinct synthetic sentinel. For all 145 cells, pure binding must
be token/file/client free and actual credential loading must use only the selected
source. Official file cases use a synthetic nested-token fixture; missing/custom
and invalid cases have file-access traps. Credential repr must exclude sentinels.

New collected functions:

- test_codex_endpoint_source_binding_matrix: 145 pure cells in test_codex_oauth.py.
- test_codex_credential_acquisition_matrix: 145 acquisition cells in that file.
- test_codex_direct_and_native_binding_parity: eleven cases crossed with both
  consumers in new test_codex_destination_acceptance.py: official key; official
  file; official ignores OpenAI key; HTTPS custom key; remote HTTP custom key;
  IPv6 custom key; custom missing key with unrelated keys; invalid built-in HTTP;
  invalid userinfo; both slash-only variants. Exactly 22 cells, real adapter/native
  helpers and fake SDK; invalid URLs cannot load credentials.
- test_codex_configured_destination_precedence: five real typed-config cases
  (model/default/inline/environment/built-in), both consumers, 10 cells. Compare
  effective destination, source metadata and endpoint hash to actual fake request.
- test_codex_readiness_source_branches: ten cases R0–R9 (official key/file/
  OpenAI-only fallback; custom key/missing; invalid endpoint; missing selected
  OAuth file; malformed selected OAuth file; both slash-only variants), with explicit file/login/cache/
  constructor counters and synthetic file/cache fixtures only.
- test_codex_readiness_capability_scope: three cases (OAuth supported effort,
  OAuth unsupported effort, custom unknown model and requested effort).
- test_codex_mixed_readiness_shares_only_login_and_cache: two ordered campaigns,
  `[custom, oauth-a, oauth-b]` and `[oauth-a, custom, oauth-b]`. Both OAuth models
  use the actual selected synthetic file and advertise high effort in the same
  synthetic cache. The custom model has only CODEX_COMPAT_API_KEY and an unknown
  remote slug. Set defaults.reasoning_effort=high and leave CODEX_API_KEY absent.
  Require exactly two file-token validations, one exact `codex login status`
  invocation and one cache read; no credential acquisition for custom. Check
  ordered receipts for each model's source, credential check, capability check
  and high effort. Reordering must not change any per-model admission assertion.
- test_codex_v2_model_identity_is_explicit: ordinary slug, prefixed inline slug,
  empty slug, whitespace slug; four cases, invalid before acquisition.
- test_codex_provenance_is_secret_free_and_endpoint_bound: two source cases,
  official file and custom key, actual _provenance with existing compiler support
  artifacts and synthetic release metadata. Trap credential loading/login/clients;
  varying CODEX_BASE_URL changes runtime fingerprint; same-source token rotation
  does not. No token or OAuth-file path appears in serialized provenance.

Total nine new functions, 343 cells. The last seven functions live in new
test_codex_destination_acceptance.py with local typed-config construction helpers;
no imports from collected test modules. Every cell gets fresh monkeypatch context,
fresh exceptions/requests/counters, socket/DNS/subprocess traps, and independently
frozen expected values. Named subtests preserve exact ordered visited-ID assertions
after each loop. SDK events are synthetic Responses delta/completed envelopes;
capture both SDK key and extra-header Authorization, exactly one create, and close.
Rejected consumer cells have zero constructor/create calls; native exceptions
remain typed and Direct returns existing typed provider-error responses.

## Explicit existing-test migrations

Keep all S1-05F file rejection/compatibility assertions. Only E1/E2 in
test_codex_auth_environment_precedence change from OpenAI fallback to synthetic
OAuth-file fallback; E0 remains CODEX_API_KEY precedence. Extend new-test environment
clear lists to CODEX_BASE_URL and CODEX_COMPAT_API_KEY. Keep unrelated legacy tests
unchanged, except endpoint isolation in the successful Codex header/effort tests.

In test_provider_adapter.py, give _call_fault_adapter an optional base_url retaining
its current generic default, and explicitly use the built-in endpoint for
test_codex_auth_failure_stops_before_sdk. Its original missing-file obligation
remains. Similarly fix the explicit URL in
test_codex_auth_failure_is_nonretryable_direct_infrastructure.

For test_codex_readiness_requires_and_records_requested_effort, add
defaults.model_base_url=None, clear endpoint/key variables, supply synthetic OAuth
file, retain the fake login/cache, and assert exact new source/check fields. Keep
both existing success and unsupported-effort assertions. Existing API-surface and
json_schema-provider rejection tests stay unchanged; new credentials do not unlock
unsupported surfaces. Record every intentionally changed existing test AST; any
other existing-test change requires independent review rather than weakening it.

## Fault controls, final gates and stops

Before edits snapshot all 163 files and exact collection. Establish expected red
failures at actual consumers and effective fingerprint, not missing-symbol imports.
The already-reproduced consumers are the primary pre-implementation red boundary;
missing new helper symbols are not counted as authentication-regression evidence.
Private process-local controls with fresh baselines:

1. Pure control wraps codex_endpoint_binding, delegates then replaces only its
   first return's endpoint_family with codex_compat. Select only the pure matrix:
   O0/K0 fails the family assertion; remaining 144 cells pass in order.
2. Acquisition control wraps resolve_codex_credential, delegates then replaces
   only its first return's token with a distinct synthetic sentinel. Select only
   acquisition matrix: O0/K0 fails token equality; remaining 144 cells pass.
3. Direct-only control AST-replaces the single
   resolve_codex_credential(resolved_base) call in adapter._call_provider with a
   private shim. The shim delegates unless the pure binding is custom with source
   None, in which case it returns a synthetic CodexCredential without file access.
   Native-only control replaces the single resolve_codex_credential(url) call in
   mcp_runtime._openai_compat_chat_turn instead. Original globals are copied with
   only the shim added; all other AST nodes must match before/after restoration.
   Each control runs the 22-cell consumer matrix: only the targeted consumer's
   custom-missing cell fails (observed constructor/create each one); other 21 pass.
4. Precedence control wraps _model_base_url: return None only for the uniquely
   marked environment case whose model/default/inline URLs are absent and whose
   CODEX_BASE_URL equals that case's synthetic URL; delegate otherwise. Each of
   the 10 cases executes its real fake-SDK consumer before comparing recorded URL
   and hash. Both environment cells fail binding assertions, other eight pass.
5. File-readiness control wraps the private file-token helper and returns a
   synthetic token only for the R6 missing and R7 malformed fixture basenames;
   delegate otherwise. Synthetic login/cache are counted valid stubs, not traps,
   for these two cases, so bypass demonstrably reaches them. R6/R7 fail the expected
   authentication rejection and zero-login assertions; other eight cases pass.
6. Purity control wraps _provider_identity and first calls
   resolve_codex_credential on the selected URL. Each provenance test installs an
   unconditional trap at that secret-loading helper. Both cases fail at the trap,
   both are visited, no actual file/client operation occurs. Baseline calls never
   reach the trap and prove actual _provenance can execute without acquisition.

These are seven distinct control processes (item 3 has two), each followed by a
fresh unmodified process for its exact selector. No control edits production
files. Verify exact full inventory before/after, ordered IDs, expected assertion
location, failure/pass counts, SDK/credential counters and baseline success.
On unexpected count/location, repair the control or implementation; never accept
an import/fake failure as evidence of the intended admission boundary.

Run all touched provider/native/readiness modules, independent source/test review,
full pytest, Ruff, offline lock/diff checks and fingerprinted final receipts.
Stop on credential/parser/fake escape, changed source inventory, unplanned helper
caller, capability regression outside the declared migration, or any need for
provider/graph/host/remote Git operations. Do not silently narrow custom endpoint
support, borrow a credential to make a test pass, or infer publication readiness.

## Independent challenge disposition

Revision 1's policy and custom-credential migration were accepted in principle.
Approval was withheld for missing mixed-model readiness-state coverage and
underspecified raw URL vectors. Revision 2 adds both campaign orders and freezes
all raw vectors, including empty userinfo, invalid/range ports, leading tab/NUL,
embedded newline and DEL. Totals are now nine functions/317 cells, with 135 cells
per binding/acquisition matrix and correspondingly updated continuation controls.
Independent re-review approved revision 2 for bounded implementation after the
pre-edit snapshot gate. Approval does not cover redirects, remote capability
validation, broader F01/F02 closure or campaign readiness.

Implementation review found that slash-only URLs could normalize to empty and
trigger a second resolver's default fallback. Revision 3 adds early rejection and
the two literal vectors to pure, acquisition, consumer and readiness tests. This
adds 26 cells (343 total) without changing the nine-function architecture.
Independent review approved revision 3 and verified the source/test migrations,
seven fault controls, seven fresh baselines and the unchanged 165-file inventory
`c669a68e93707db6a88e2e446b1ca163bc37899392e544cc8f55a375cb4307eb`.

## Final validation

- Full regression: 1,150 tests passed in 222.19 seconds. The default-verbosity
  full-suite summary does not report a separate subtest total.
- Focused regression: 154 tests and 546 subtests passed in 2.97 seconds.
- New acceptance surface: nine tests and 343 subtests passed in 2.18 seconds.
- Seven intended fault injections and seven fresh unmodified baselines verified;
  exact ordered cases, failure locations, SDK/file/login/cache counters and all
  165 source hashes were checked before and after every process.
- Ruff, offline dependency lock and diff checks passed.
- Full-suite log SHA-256:
  `bad9b3a27b30c254670507dda81bf7fad4d5f69f59e149d3126a119e14371049`.

An earlier full-suite run began before the final test-strengthening boundary and
is explicitly excluded from acceptance. Its superseded inventory is retained.
The final run and all controls bind to the 165-file fingerprint above. Runtime
results remain unmerged and require fresh readiness/certification before any
publishable campaign. No live provider, graph, host or remote Git operation ran.
Independent final review verified the full-suite log hash, unchanged inventory,
all fourteen control receipts, focused/new-case results and migration documentation.
Redirect containment, remote capability validation and remaining audit findings
remain outside this closure.
