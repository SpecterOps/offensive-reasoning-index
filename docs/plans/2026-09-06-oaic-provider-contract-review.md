# S1-05A provider contract review

September 6. Status: source audit, root reproductions and independent final
ledger/evidence review complete. This is not implementation approval.

## Scope and evidence

Complete reads: `provider_auth.py`, `provider_contract.py`, `codex_oauth.py` and
`adapter.py` under `src/ori/eval/`; all five corresponding primary test modules
named in the [approved plan](2026-09-06-oaic-provider-contract-review-plan.md).
The read-only auditor inspected every provider branch, not only compatible APIs.
Root independently read all four implementation files and reproduced the cases
below. The auditor did not run tests or edit files.

Root's focused baseline passed **92 tests in 1.52 seconds**, after the auditor
confirmed synthetic keys/auth files and fake SDK/transport boundaries. Pure config
tests do not enter supported Codex login/cache readiness. No real credentials,
login/refresh, provider request, live graph or remote Git operation was used.
Private probe network traps observed zero connection/resolution attempts.

The 160-file entry inventory digest is
`43af75a2887a246cd3c5c9402f71cf768914744f7ecd8feef117611eaa04a876`.
Nine focused-file hashes, supporting source hashes and test/probe receipts are
retained under ignored S1-05A evidence. Final independent reconciliation passed.

```text
campaign readiness / endpoint fingerprint
  → campaign-runner model/base-URL forwarding
  → V2 Direct transport → adapter.call_provider_text → provider branch
      → auth + request/turn contract
      → Codex translation where selected
native MCP single-turn helper → shared auth/contract/Codex translation
```

Only supporting callable boundaries were reviewed in these files:

- `campaign_runner.py:1091–1281,2110–2183`: endpoint identity/readiness and forwarding.
- `model_runtime.py:236–307,480–504,578–604,921–1022`: typed errors, public request and Direct handoff.
- `mcp_runtime.py:1315–1351,1603–1767`: endpoint resolution and one compatible/Codex turn, not loop sequencing.
- `campaign_config.py:1–169`: typed provider/surface/options and model prefix.
- `run_config.py:145–155,245–280`: legacy override/base-URL handling.
- `inspect_runtime.py:219–247`: model resolution and legacy routing.
- `telemetry.py:405–432,560–630`: private provider metrics/config-key sanitization.

Full native loops, V2 orchestration/projectors, cancellation durability, all V1
behavior and public exporter closure remain separate work. A supporting read does
not close those audits.

## Findings and precise reproduction limits

### F01 — endpoint identity can differ from the actual destination (P1)

`campaign_runner._model_base_url` prefers configured model/default URL over inline
URL. Compatible Direct `adapter._call_provider` unconditionally replaces it with
the inline URL. Native MCP's helper prefers the configured URL.

Root passed a real typed V2 model with configured OpenRouter and inline Nous URLs
through the actual readiness identity helper and Direct adapter with a fake SDK.
Metadata selected OpenRouter; the constructed fake request selected Nous and its
scoped credential. This proves configuration-to-request disagreement, not a live
readiness pass, provider incident or incorrect published campaign score.

Additional source-established binding gaps require explicit fix-plan decisions:
Anthropic ignores the passed URL and inherits its SDK environment/default; Ollama
and Codex environment/default URLs are absent from the current endpoint resolver's
fingerprint; Gemini uses a fixed endpoint even when a configured URL is fingerprinted.
Owner: provider endpoint resolution plus narrow readiness/provenance callers.

### F02 — credential admission is inconsistent (P1)

Codex chooses `CODEX_API_KEY`, then `OPENAI_API_KEY`, then its auth file, while its
destination accepts explicit/inline/environment overrides. Root's fake-client probe
confirmed a synthetic official OpenAI key becomes both SDK key and Authorization
for an arbitrary synthetic Codex destination, without reading an auth file.

OpenRouter/Nous resolution uses hostnames, not the TLS/port/userinfo rule applied
to official OpenAI. Pure resolver probes selected scoped keys for HTTP OpenRouter,
HTTP Nous and nonstandard-port OpenRouter; HTTP official OpenAI correctly returned
no key. No keys were transmitted. OpenRouter subdomains are also admitted by source.

Anthropic constructs a bare SDK client, delegating destination, auth-token/key and
credential-discovery behavior rather than binding them to recorded ORI identity.
Endpoints/environment are operator-controlled inputs; this is not proof of remote
compromise. It is a credential-boundary and configuration-safety defect relative
to exact-destination/no-cross-provider-fallback requirements. Preserve supported
custom endpoints through an explicit policy, not an unreviewed blanket removal.
Owner: auth/provider adapter.

### F03 — unexpected defects gain provider retryability (P2)

`adapter.call_provider_text` catches arbitrary exceptions, and its generic classifier
defaults to retryable `PROVIDER_ERROR`. Root confirmed this classifier result for
a synthetic `ValueError`. V2 trusts returned adapter metadata when constructing
infrastructure outcomes, so thrown-runtime exception containment does not correct
an already converted `ModelResponse`.

This probe proves the classifier boundary, not a full paid retry or persisted
campaign consequence. Cancellation is not caught by these `except Exception`
blocks on supported Python versions. Owner: adapter taxonomy with narrow V2 tests.

### F04 — final-answer/status handling differs across native branches (P2)

Root's fake HTTPX Ollama stream emitted text with `done=false`, then EOF. The actual
Direct adapter returned that partial text with no error and zero token counts.
No completed stream was required. Source additionally shows Anthropic selects
only the first block without stop-reason handling; Codex Direct hardcodes completed
status and flattens completed refusal text. Codex incomplete events raise generic
stream errors, then become retryable infrastructure, unlike compatible truncation.

These are bounded adapter observations; do not infer model capability or actual
provider incident. Owner: provider translation/status contract. Missing supported
features and malformed response behavior require separate, explicit decisions.

### F05 — Codex stream/usage projection loses information (P2)

Root's pure synthetic translator probes confirm missing input usage becomes zero
and duplicate function-call done events produce two calls with the same ID.
Source also lacks post-terminal/multiple-terminal reconciliation, partial-versus-
completed output reconciliation, and may invent missing IDs/empty arguments.

The duplicated output probe does not prove two tools were executed. Missing usage
knownness is a concrete translator loss; durable accounting remains S1-05B/C.
Cached/reasoning dimensions are not yet represented by the shared contract and
belong to the planned scorecard expansion. Owner: Codex translator and later runtime.

### F06 — SDK redirect containment (P2) and open retry accounting

Installed OpenAI 2.30.0 and Anthropic 0.116.0 default to two retries and redirect-
following SDK clients. HTTPX 0.28.1 defaults to environment trust; raw clients do
not follow redirects by default. HTTPX strips Authorization on different origins
except direct same-host HTTP-to-HTTPS upgrades, but does not strip arbitrary
credential headers; Anthropic emits `X-Api-Key`.

Root crossed the actual ORI Anthropic adapter and installed SDK with its default
redirect-following HTTP client, replacing all network transport with a finite
mock. A synthetic 307 redirect between two distinct synthetic hosts retained the
synthetic `X-Api-Key` on both requests. The client had an explicit synthetic key
and base URL, environment trust disabled, empty environment, retries disabled
and network traps; zero connection/resolution attempts occurred. This verifies
default header forwarding under those conditions, not real key exposure or a
provider incident. No bare SDK credential discovery was allowed.

SDK retries occur inside ORI's logical attempt; actual budget/usage impact remains
unestablished. Do not claim all bearer credentials cross redirects. Owner:
transport containment and S1-05B/C accounting.

## Exact test evidence key

All references denote assertions read by the source auditor, not name searches.
Fake client assertions prove ORI request construction, not remote behavior.

| Key | Test / actual boundary |
| --- | --- |
| A1 | `test_openai_compat_api_key_selects_scoped_keys_and_generic_override_only`: real resolver, synthetic expected keys/source and fallback deletion. |
| A2 | `test_official_openai_key_requires_the_exact_tls_origin`: HTTP/8443/userinfo rejection, no official key. |
| A3 | `test_no_provider_key_crosses_endpoint_families`: one synthetic key at a time, expected endpoint matrix. |
| A4 | `test_credential_metadata_is_safe_and_endpoint_scoped`: source/family and secret omitted from repr. |
| C1 | `test_chat_completions_payload_projects_shared_request_contract`: exact messages/tools/schema/limit/reasoning payload. |
| C2 | `test_nullable_content_with_valid_tool_call_is_lossless`: exact ID/name/raw/parsed arguments and status. |
| C3 | Refusal/reasoning/empty tests and `test_finish_reason_controls_typed_terminal_status`: exact normalized output/status. |
| C4 | `test_missing_usage_stays_unknown_instead_of_becoming_zero`: None counts, false knownness flags. |
| C5 | Malformed tool arguments/missing choices: raw retained and nonretryable protocol/parse classification. |
| D1 | `test_chat_adapter_normalizes_text_usage_and_surface`: fake SDK model/count/status/surface/knownness. |
| D2 | Scoped-key/local-placeholder/missing-remote-key adapter tests: fake SDK key and no-request assertions. |
| D3 | `test_official_openai_rejects_an_untrusted_origin_before_client_creation`: fake constructor unused, nonretryable capability. |
| D4 | `test_official_openai_and_gemini_pass_only_their_scoped_keys`: fake SDK captured keys/default/source. |
| D5 | `test_nonfinal_direct_turns_are_typed_model_output_failures`: compatible fake responses, empty output/exact subtype. |
| D6 | `test_missing_usage_is_unknown_in_metrics_and_zero_only_at_legacy_boundary`: fake SDK, top-level zero and nested None/false flags. |
| D7 | `test_provider_transport_errors_use_typed_metadata`: real classifier, synthetic HTTPX timeout/connect errors. |
| D8 | `test_provider_endpoint_telemetry_strips_userinfo_path_and_query`: fake SDK, scheme/host projection. |
| D9 | `test_openai_compatible_client_receives_explicit_request_timeout`: fake client captures 37.5 seconds. |
| V1 | `test_release1_unsupported_surfaces_fail_before_provider_probe`: real readiness rejection before Codex login. |
| V2 | Structured-output/option tests: real config rejects unsupported JSON-schema modes and hidden typed-option shadowing. |
| V3 | Readiness identity/missing-key/official-origin tests: synthetic environment, no provider probe. |
| V4 | `test_environment_compat_endpoint_is_resolved_for_runtime_fingerprinting`: environment URL changes hash. |
| X1 | `test_codex_chat_translation_preserves_tool_conversation`: exact instruction/input/call/result/schema metadata. |
| X2 | Optional-parameter/reasoning tests: omitted/enabled request fields and invalid effort rejection. |
| X3 | `test_codex_provider_sends_reasoning_effort_to_responses`: fake SDK/module/event stream, synthetic key. |
| X4 | `test_codex_headers_load_oauth_token_from_codex_auth_file`: temporary synthetic auth file and exact headers. |
| X5 | Pure Codex synthetic stream success/error/failed/incomplete/missing-terminal/empty/fallback cases: exact values or exceptions. |

### Expanded executable selector index

Function selectors include every current parameterized case. Shared-normalizer
tests do not prove each provider invokes that normalizer. Config tests do not
prove remote capability; fake client tests do not prove actual destinations.

| Key | Exact selector | Assertion boundary |
| --- | --- | --- |
| C3 | `tests/test_provider_contract.py::test_refusal_only_response_remains_distinct_from_empty_output` | Empty text, retained refusal, REFUSED and exact item. |
| C3 | `tests/test_provider_contract.py::test_reasoning_only_response_remains_distinct_from_empty_output` | Retained reasoning, REASONING_ONLY and exact item. |
| C3 | `tests/test_provider_contract.py::test_genuinely_empty_response_has_explicit_empty_status` | Empty text/items, EMPTY. |
| C3 | `tests/test_provider_contract.py::test_finish_reason_controls_typed_terminal_status` | stop/length/content_filter and corresponding status. |
| C5 | `tests/test_provider_contract.py::test_malformed_tool_arguments_are_retained_without_becoming_empty_object` | Raw retained, parsed None, MALFORMED/error. |
| C5 | `tests/test_provider_contract.py::test_missing_choices_or_message_is_nonretryable_protocol_error` | Invalid envelope, nonretryable PROVIDER_PROTOCOL. |
| D2 | `tests/test_provider_adapter.py::test_direct_adapter_uses_only_endpoint_scoped_credential` | Fake client OpenRouter key/family/source. |
| D2 | `tests/test_provider_adapter.py::test_local_generic_compat_endpoint_never_borrows_official_openai_key` | Fake local key placeholder, no credential source. |
| D2 | `tests/test_provider_adapter.py::test_remote_generic_compat_endpoint_requires_explicit_compat_key` | No fake request, nonretryable PROVIDER_AUTH. |
| D2 | `tests/test_provider_adapter.py::test_compat_provider_without_endpoint_fails_closed` | Neither client nor request, nonretryable PROVIDER_CAPABILITY. |
| D2 | `tests/test_provider_adapter.py::test_compat_override_does_not_replace_scoped_provider_keys` | Missing scoped key, no request, PROVIDER_AUTH. |
| D2 | `tests/test_provider_adapter.py::test_missing_nous_credential_is_nonretryable_auth_failure` | No request, nonretryable PROVIDER_AUTH. |
| V2 | `tests/test_provider_v2_config.py::test_json_schema_mode_is_explicit_and_fingerprinted_for_chat_providers` | Identity/serialized mode equals json_schema; does not directly compare two runtime hashes despite its name. |
| V2 | `tests/test_provider_v2_config.py::test_json_schema_mode_rejects_uncertified_provider_surfaces` | Real validation rejects Codex/Ollama/Anthropic mode. |
| V2 | `tests/test_provider_v2_config.py::test_structured_output_mode_cannot_hide_in_options` | Real validation rejects option shadowing. |
| V2 | `tests/test_provider_v2_config.py::test_api_surface_is_typed_and_cannot_hide_in_options` | Unknown surface and options.api_surface rejected. |
| V3 | `tests/test_provider_v2_config.py::test_readiness_records_safe_endpoint_and_credential_identity` | Local surface/mode/family/source and synthetic secrets omitted. |
| V3 | `tests/test_provider_v2_config.py::test_official_openai_never_accepts_compat_override` | Missing official key rejected despite compatible key. |
| V3 | `tests/test_provider_v2_config.py::test_official_openai_readiness_rejects_untrusted_origins` | HTTP/alternate official port rejected. |
| V3 | `tests/test_provider_v2_config.py::test_remote_generic_compat_requires_explicit_compat_key` | Remote generic missing explicit key rejected. |
| X2 | `tests/test_codex_oauth.py::test_codex_chat_translation_omits_unsupported_optional_params_by_default` | Pure translator omits limit/temperature/reasoning. |
| X2 | `tests/test_codex_oauth.py::test_codex_chat_translation_sets_explicit_reasoning_effort` | Pure translator exact high effort. |
| X2 | `tests/test_codex_oauth.py::test_codex_chat_translation_rejects_unknown_reasoning_effort` | Pure translator rejects extreme. |
| X2 | `tests/test_codex_oauth.py::test_codex_chat_translation_can_opt_into_optional_params` | Synthetic flags produce limit123/temperature0. |
| X5 | `tests/test_codex_oauth.py::test_codex_responses_events_collect_chat_completion` | Pure events: text, one tool call, exact complete usage. |
| X5 | `tests/test_codex_oauth.py::test_codex_responses_events_raise_on_stream_error` | Stream error with supplied code/message. |
| X5 | `tests/test_codex_oauth.py::test_codex_responses_events_raise_on_failed_response` | Failed terminal event raises. |
| X5 | `tests/test_codex_oauth.py::test_codex_responses_events_raise_on_incomplete_response` | Incomplete max-output terminal raises. |
| X5 | `tests/test_codex_oauth.py::test_codex_responses_events_raise_without_terminal_event` | Text-only stream raises missing completion. |
| X5 | `tests/test_codex_oauth.py::test_codex_responses_events_raise_on_empty_completed_response` | Empty completed stream raises. |
| X5 | `tests/test_codex_oauth.py::test_codex_responses_events_fall_back_to_completed_response_output` | Completed output_text fallback equals expected text. |

Missing-key D2 cases assert no request; do not upgrade that to no constructor
unless explicitly asserted. X2's default-omission test does not clear external
opt-in flags, so its deterministic outcome assumes those flags absent; this is
test-isolation debt, not an unmocked-provider path. X5 does not prove duplicate/
out-of-order handling, partial knownness, refusal classification or downstream
retryability. Root probes supplement only their explicitly demonstrated boundaries.

## Complete 72-cell provider/obligation ledger

R = retain bounded behavior; F = fix candidate above; U = unresolved or requires
targeted evidence. No primary assertion means none in these five modules, not
globally absent coverage. N/A always states why. Obligation numbers match the plan.

### Anthropic

| # | Disposition and evidence |
| --- | --- |
| 1 | F01: passed URL ignored; SDK environment/default used. No primary request assertion. |
| 2 | F02: bare SDK credential selection versus readiness key-only check. No auth/request parity assertion. |
| 3 | U/F06: actual SDK MockTransport proves synthetic X-Api-Key cross-origin forwarding; real transport, retry accounting, proxy and credential discovery remain untested. |
| 4 | R/U: nominal Chat surface uses Messages; V2 rejects JSON schema; generic Responses rejection, no branch request assertion. |
| 5 | R/U: system separate, messages forwarded, no oracle object passed; full public envelope open, no primary payload assertion. |
| 6 | F04: first text block, stop reason ignored; no response matrix. |
| 7 | N/A/R: Direct branch exposes neither streaming nor native tool output; multiple text blocks covered by6. |
| 8 | U: assumes usage present; no knownness/cached/reasoning assertion. |
| 9 | F03: arbitrary SDK/shape exceptions retryable; D7 proves only shared HTTPX classifier, cancellation still propagates. |
| 10 | U: raw errors private, no success metrics; complete downstream public export not reviewed. |
| 11 | R: active dispatch; no branch test does not mean dead code. |
| 12 | U: auth/adapter owner; credential discovery internals and full runtime/export remain open. |

### Ollama

| # | Disposition and evidence |
| --- | --- |
| 1 | F01: explicit/environment/default and `/v1` stripping, fingerprint misses environment/default; no primary transport assertion. |
| 2 | N/A/R: branch sends no API key/header; URL credentials and proxies remain1/3. |
| 3 | U: fixed HTTPX timeout, redirects off/environment trust on; no primary fake transport test. |
| 4 | R/U: V2 rejects JSON schema; nominal surface differs from native API; passed timeout/output limit not honored here. |
| 5 | R/U: provided system/messages and copied options; no primary payload assertion. |
| 6 | F04: EOF without done accepted; malformed/error chunks not normalized; root probe supplements absent primary assertion. |
| 7 | U: append-only text/thinking, terminal reconciliation open; tool preservation N/A for this Direct branch. |
| 8 | U: missing usage coerced zero, no knownness flags or primary usage test. |
| 9 | F03: malformed JSON becomes provider retryability; D7 covers helper only. |
| 10 | R/U: fixed numeric success metrics, raw errors private; full export open. |
| 11 | R: active dispatch/compatibility route, no removal. |
| 12 | U: native transport and S1-05B/C loop/durability owners. |

### Official OpenAI

| # | Disposition and evidence |
| --- | --- |
| 1 | R/U: official explicit/default, other origins rejected D3/D4; full path/query/fragment cases open. |
| 2 | R: only official key, pre-client check D3/D4/A2. |
| 3 | U/F06: explicit key/base, inherited redirect/retry/proxy defaults; no transport assertion. |
| 4 | R: shared surface gate V1/schema payload C1; remote capability not probed. |
| 5 | R/U: supplied system/messages/schema C1/D4; full public-surface implementation open. |
| 6 | R/U: shared C3/D5 normalization, not independent official response fixtures. |
| 7 | R/U: shared C2/C5 arguments; duplicate IDs unresolved, streaming N/A for this nonstreaming branch. |
| 8 | R/U: C4/D6 preserve unknown until legacy zero projection; cached/reasoning absent. |
| 9 | F03: D7 known errors, unexpected locals still retryable. |
| 10 | R/U: D8 endpoint scrub, arbitrary private model/ID/refusal/reasoning not intrinsically secret-free. |
| 11 | R: distinct credential branch, not redundant with compatible provider. |
| 12 | U: adapter/transport owner; SDK retry accounting/public exporter open. |

### OpenAI-compatible

| # | Disposition and evidence |
| --- | --- |
| 1 | F01: Direct inline precedence differs; D1 model projection and V4 env hash do not cover conflict. |
| 2 | F02: A1/A3/D2 family isolation works, OpenRouter/Nous TLS/port admission gap remains. |
| 3 | U/F06: SDK Direct differs from raw native helper; D9 timeout forwarding only. |
| 4 | R: unsupported Responses rejects before request, V1/adapter test; C1 schema forwarding. |
| 5 | R/U: C1/D1 supplied envelope; helper reasoning mapping can overwrite payload keys, though production adapter does not populate it. |
| 6 | R/U: known C3/D5 statuses; unknown finish reason with text becomes completed, explicit contract decision open. |
| 7 | R/U: C2/C5 valid/malformed args; missing/duplicate IDs and nonfinite JSON admission unresolved. |
| 8 | R/U: C4/D6 unknown counts covered, cached/reasoning not modeled. |
| 9 | F03: known contract failures typed, arbitrary local exceptions gain retryability. |
| 10 | R/U: D8 endpoint only; arbitrary private metadata/raw errors and full public exporter open. |
| 11 | R: public/tested compatibility key wrapper cannot be removed for lacking a current production caller. |
| 12 | U: endpoint/auth/adapter owner; native sequencing/persistence later. |

### Gemini

| # | Disposition and evidence |
| --- | --- |
| 1 | F01: fixed endpoint ignores configured URL fingerprint; D4 key test not mismatch proof. |
| 2 | R: explicit Gemini key/check; D4 positive, dedicated missing-key assertion absent. |
| 3 | U/F06: shared SDK defaults; no transport test. |
| 4 | R/U: shared Chat/schema/config path, no live capability probe. |
| 5 | R/U: C1 shared/D4 branch request; full envelope open. |
| 6 | R/U: shared C3/D5, no Gemini response matrix. |
| 7 | R/U: C2/C5 shared nonstreaming tools; duplicates unresolved, streaming N/A. |
| 8 | R/U: shared unknown counts, no Gemini usage fixture/cached/reasoning dimensions. |
| 9 | F03: generic fallback; D7 helper only. |
| 10 | R/U: shared endpoint/private metrics, no complete public export proof. |
| 11 | R: active fixed-origin/scoped-key branch, no removal. |
| 12 | U: adapter owner; later bounded canary establishes actual remote schema/usage, not this audit. |

### Codex

| # | Disposition and evidence |
| --- | --- |
| 1 | F01/F02: explicit/inline/env/default, arbitrary destination and unbound environment; X1/X3 omit destination. |
| 2 | F02: official-key fallback/unrestricted destination; X4 synthetic-file positive only; root fake SDK supplements. |
| 3 | U/F06: explicit SDK key/base, inherited defaults, client closes finally; no transport test. |
| 4 | R/U: Responses V1, optional params X2, unsupported schema V2; documented token-limit omission is not supported enforcement. |
| 5 | R/U: X1 exact conversation; invented IDs/empty args; full public-envelope guard open. |
| 6 | F04: X5 error raising followed by generic infrastructure; refusal flattened/status hardcoded. |
| 7 | F/U/F05: X5 single call; root duplicate output proof, no tool execution consequence or complete ordering proof. |
| 8 | F05: partial unknown becomes zero (root); X5 complete/absent-whole usage only. |
| 9 | F03/F04: incomplete/empty retryable; cancellation/client close retained, persistence S1-05C. |
| 10 | U: raw provider errors/auth paths private; generated response ID; no new public leakage proved. |
| 11 | R: Direct/native dynamic imports and helper callers; no removal. |
| 12 | U: auth/translator owner; real login, token validity/refresh and remote behavior not tested. |

## Dependency evidence and remaining action

The source auditor inspected installed source without constructors or network calls: OpenAI
`_client.py:510–559`, `_base_client.py:463–477,1386–1444`; Anthropic
`_client.py:620–705,750–776`, `_base_client.py:1625–1667`; HTTPX
`_client.py:546–565,1353–1410`; their version/constants files. Exact source hashes
and versions are retained privately for independent verification. SDK source
observations are not inferred actual provider behavior. Root's separately scoped
redirect probe constructs an explicit synthetic SDK client with only a mock
transport, as described in F06; it does not use SDK credential discovery.

No dead-code removal is justified by this audit. Similar provider code does not
have equivalent credential, response or V1/V2 contracts. Proposed fix order:
effective destination/credential binding, typed native response/error contract,
then stream/usage and SDK containment with native-loop/runtime owners. Each needs
a fresh decision-complete plan and independent challenge; this ledger implements
none of them. Broader Stage 1, local qualification, merged certification and final
conference readiness remain open.
