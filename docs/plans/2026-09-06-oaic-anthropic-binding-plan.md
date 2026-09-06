# S1-05I — Anthropic configuration, destination and credential binding

September 6, revision 2. Implemented and independently accepted for bounded S1-05I
closure. This is not complete Stage 1 or campaign readiness. The accepted
S1-05H characterization is the input,
not proof that this design is already correct or that the runtime is ready.

## Dependency and scope

Start from the accepted 165-file inventory
`c669a68e93707db6a88e2e446b1ca163bc37899392e544cc8f55a375cb4307eb`, with 1,150
passing tests. Reverify it and snapshot every affected file before implementation.
The installed Anthropic SDK is 0.116.0; its seven source hashes are retained in
S1-05H's private completion receipt. No dependency upgrade is in scope.

Correct Anthropic Direct initial destination/source admission, configuration-only
readiness and provenance. Preserve native official API-key, bearer, custom-header,
OAuth-profile and federation capabilities. Custom inference destinations remain
available using a dedicated explicit key. Keep Anthropic MCP unsupported. Do not
change response-content selection, terminal-state handling, token accounting,
SDK retry policy, redirects, other providers, scoring, graph or task contracts.

Two deliberate migrations are required: built-in credentials may no longer be
implicitly sent to custom inference destinations; and a running campaign freezes
profile configuration rather than accepting profile-routing changes mid-run.
Token-file rotation and native refresh behavior remain supported. Describe these
as security/reproducibility changes, not behavior-identical cleanup.

## Ownership and integration boundary

Root owns `adapter.py`, `v2/campaign_config.py`, `v2/campaign_runner.py`, documentation
and reintegration. The SDK specialist owns new `src/ori/eval/anthropic_binding.py`.
Root also owns the independently reviewed `.ori-private/` ignore rule in `.gitignore`.
The runtime-test owner owns new `tests/test_anthropic_binding.py` and the named
existing-test migrations after review. The independent reviewer owns read-only
plan, source/test and final evidence challenge. Shared source/test edits must not
overlap. Preserve all earlier changes in the consolidated branch.

No remote Git, signing, PR/merge, real SDK credential discovery, credential files,
provider, graph, GPU or host operations during implementation/validation. All
SDK/config/token fixtures are synthetic and local. No Hermes, private-machine or
reservation dependency may be introduced.

## Three interfaces, one prepared configuration

Use frozen/slotted internal dataclasses in `anthropic_binding.py`:

- `AnthropicConfigSnapshot`: immutable JSON bytes/string for a selected profile or
  federation configuration; immutable exact-key header entries; static key/bearer
  values or selected token-source references. All fields are private, repr-excluded
  and never serialized into campaign artifacts. Use JSON round-trip copying when
  giving a mutable config dictionary to the SDK.
- `AnthropicBinding`: normalized `base_url`, `endpoint_family`, constant-name
  `credential_source`, `model_slug`, `config_fingerprint`, and repr-excluded snapshot.

`prepare_anthropic_binding(model, base_url=None)` resolves/adopts configuration and
validates admission. It may read only the selected profile pointer/configuration,
never access/refresh/identity-token files. It must not construct any HTTP client,
call SDK `default_credentials`, invoke a token provider/cache, exchange or refresh
tokens, or mutate process environment.

`anthropic_binding_identity(binding)` returns only the safe immutable metadata.
It is pure: no environment, filesystem, SDK constructor or credential operation.
`materialize_anthropic_client(binding)` is execution-only and creates the exact
native `AsyncAnthropic` class inside a private attempt-owned handle, with an explicit bound URL and explicit selected
credentials/configuration. Do not subclass the client: that disables SDK discovery
semantics. Do not temporarily modify environment variables around construction.

Store bindings by unique model name in a Pydantic `PrivateAttr` mapping on
`ResolvedV2CampaignConfig`; normal config/provenance serialization excludes it.
At the start of `_model_readiness`, prevalidate the requested API surface of every
model and the nonblank Anthropic/Codex slugs before any provider configuration-file
access. Reuse `provider_contract.validate_release1_api_surface` behind a pure
campaign error-translation helper used by both prevalidation and identity;
preserve existing campaign messages and do not duplicate divergent policy. Then
prepare all Anthropic bindings in a local temporary dictionary. Install a read-only
mapping only after every Anthropic entry succeeds. A failed preparation installs
nothing, and a retry rebuilds the entire temporary dictionary. Once installed,
repeated readiness on the same resolved object reuses it without environment or
profile discovery. The mutation digest is exactly a list sorted by unique model
name, containing only Anthropic entries and these fields: `name`, `model`,
`api_surface`, `structured_output_mode`, and `configured_base_url` (model URL or
defaults URL or null, using current precedence). It contains no environment values,
other model entries, unrelated defaults or reasoning effort. Recompute this pure
projection before reuse; inequality rejects mutation and never triggers replacement.
An empty selection hashes the empty list. Adding, removing, renaming or changing
the provider of an Anthropic entry changes the projection, including transitions
to/from the empty map, and requires fresh configuration load. Reordering otherwise
identical entries does not. A changed default URL is relevant only to Anthropic
entries that inherit it. A fresh configuration load is the only rebinding boundary.
A later readiness failure in another provider does not discard or partially replace
the already complete Anthropic mapping. Empty Anthropic selections install an empty
mapping rather than an ambiguous unprepared state.
Non-Anthropic configuration changes retain existing behavior; do not freeze unrelated
provider settings merely because an empty Anthropic map was installed.

Other providers retain their existing paths. Identity, endpoint fingerprint
and provenance require the prepared Anthropic binding and fail closed if it is
missing; they may not silently rediscover it. Unsupported API-surface errors must
still precede binding access. The ordinary campaign preparation path performs this
stage before any provider execution.

Pass the same binding to actual Direct execution using a bound transport callable
provided to the existing `run_direct_model_task_v2(transport=...)` seam. Add an
optional `anthropic_binding` keyword to `call_provider_text` and its internal
dispatcher, but only supply it for Anthropic. No V2 model-runtime/scorer signature
change is needed. Without a supplied binding, standalone/V1 Anthropic calls prepare
an ephemeral binding before materialization. Other providers ignore no new state
and receive no new kwargs. Bound model slug/URL must match the attempted request.

## Destination policy

Inference precedence is model URL, defaults URL, inline suffix, ANTHROPIC_BASE_URL,
selected native profile URL, then `https://api.anthropic.com`. Inline suffix is
removed from the SDK model name. Empty/whitespace V2 model slugs fail before config
file access. Explicit empty URL arguments retain existing absent-value fallback;
a nonempty slash-only URL fails before normalization can create a second fallback.

Admit HTTP/HTTPS with a nonempty hostname, valid nonempty explicit port, no userinfo
(including empty userinfo), query, fragment, backslash or ASCII codepoint 0–32/127.
Normalize only trailing slashes. Validate raw text before URL parsing can discard
characters. Catch URL parsing failures using constant path/URL-free capability
errors. The exact case-insensitive built-in hostname `api.anthropic.com` requires
HTTPS, no port or 443, and an empty normalized path. Invalid built-in variants are
rejected, never downgraded to custom. Other hosts, IPv6, remote HTTP and custom
ports/paths remain valid custom deployments with the dedicated key. HTTP is an
explicit plaintext choice, not a secure-transport claim.

Separately resolve a selected profile's potential refresh/federation base URL using
the SDK's actual profile-over-bound precedence. Native credential acquisition is
admitted only when both inference and credential-provider destinations are the
official origin above. Reject conflicting/prohibited routing before materialization;
never silently rewrite a profile's exchange URL. This restriction does not claim
redirect, DNS, TLS or actual token-exchange containment.

For custom inference, require nonempty `ANTHROPIC_COMPAT_API_KEY`. Do not use native
API keys, bearer tokens, OAuth profiles or workload identity for that request. A
profile may have been read solely to resolve an otherwise implicit destination;
discard its credential configuration after selecting the dedicated custom key.
Missing custom key is a constant nonretryable authentication failure.

## Native configuration preparation and compatibility

Encapsulate all pinned-SDK configuration helpers in the new module. Before using
private helpers, require installed version `0.116.0` and the seven exact source-file
SHA-256 values in the accepted S1-05H receipt. Put the expected values in the module,
using paths relative to the installed `anthropic` package rather than machine paths.
Missing/unreadable source, mismatched version/hash, or missing required symbols
raises constant `ProviderCapabilityError("Unsupported Anthropic SDK credential interface")`.
This is an intentional fail-closed compatibility boundary: dependency upgrades,
vendor patches and source-less packaging require new review and certification.
Do not automatically accept a version range or fall back to discovery. Cache a
successful compatibility check for the process; tests clear the cache explicitly.
Reuse configuration parsing through `CredentialsFile`, not `default_credentials`:
the latter can construct an HTTP client in its federation branch.

Preserve SDK selection order and its distinction between absent and empty values:

1. Native API-key and/or bearer environment fields suppress discovery when either
   is present, including an empty value. Preserve simultaneous key and bearer.
2. Otherwise, explicit profile/config-directory selection or an active pointer wins.
3. Otherwise, complete environment federation wins over an unselected fallback
   profile. Snapshot its selected identity source, not the identity-token value.
4. Otherwise, attempt the native fallback profile. Preserve swallowed native
   fallback `AnthropicError` behavior; explicitly selected broken profiles fail.
5. No selected native credential still permits the SDK's established canonical
   custom-header authentication. Preserve its case-sensitive admission behavior.

Profile parsing may fill native environment defaults into configuration. Freeze
the resulting selected configuration and the resolved credential/identity file
paths without opening those token files. Preserve a profile's default token-cache
path when converting to `InMemoryConfig`: explicitly populate credentials_path for
both OAuth and profile federation. Resolve identity-token path selection once;
token content remains execution-only. Keep environment identity-token readers
dynamic at invocation, matching native token rotation, while the source choice,
rule, organization, scope, workspace and routing are frozen.

Use native `InMemoryConfig` at execution for profile credentials, retaining native
OAuth external rotation, refresh grants and federation cache behavior. Environment
federation materializes native `WorkloadIdentityCredentials` only during execution.
Never invoke either provider during preparation or readiness. Arbitrary callable
providers are not added to ORI's interface by this change.

Configuration-only auth admission mirrors the locked SDK's header validation using
prepared static headers and a provider-present marker; it is not authentication.
Readiness labels all admitted Anthropic entries `configuration-validated-not-probed`
and explicitly does not claim remote model, schema or credential validity. Missing
token files can therefore pass configuration readiness and fail typed execution.

## Header isolation and source metadata

Constructor arguments are total, not discovery hints. In every branch supply the
bound `base_url`, prepared `default_headers`, and owned async `http_client`.
Never pass `profile=` or `config=` to the SDK constructor; materialize a selected
native provider separately and pass it through `credentials=`. The exact branch
arguments are:

| Branch | `api_key` | `auth_token` | `credentials` | Additional header rule |
| --- | --- | --- | --- | --- |
| Native key/bearer present | selected value or None, including empty | selected value or None, including empty | None | Preserve original native empty fields and both credentials |
| Profile OAuth/federation | None | None | selected `InMemoryConfig` | Merge its prepared extra headers below exact-key custom headers |
| Environment federation | None | None | selected `WorkloadIdentityCredentials` | Preserve prepared native header map |
| Header-only | empty string discovery fence | None | None | Add canonical `X-Api-Key: Omit()` only when that exact key was absent from the original prepared map |
| Custom destination | dedicated nonempty key | None | None | Exact-key tombstones for every inherited prohibited variant, then canonical dedicated key |

The synthetic empty key in header-only mode is not native-empty-key mode. Do not
add an empty wire header, promote lowercase auth headers, or erase a canonical
header that was actually configured. Every branch suppresses SDK credential
discovery while preserving its admitted wire headers. Configuration-only admission
uses the locked SDK's unbound `_validate_headers` with a configuration-only marker
holding `_token_cache` presence, after filtering Omit as F03 does; no client or
token cache is constructed. Actual materialized admission is still checked before
the request, preserving F03's independent post-allocation safety boundary.

For official inference, preserve exact native header parsing, precedence and case
behavior, including static custom-header overrides and simultaneous key/bearer.
Record effective source names, not values; distinguish a static header shadowing
a configured provider from an actually invoked provider. Profile configuration is
not remotely validated merely because an overriding static header exists.

For custom inference, exclude every exact spelling of inherited `Authorization`,
`X-Api-Key`, `Proxy-Authorization` and `Cookie`, matched case-insensitively. Build
exact-key Omit entries for all excluded environment variants before adding the
dedicated canonical `X-Api-Key`. Preserve other configured headers. The SDK rereads
its custom-header environment during construction; supplying an empty mapping or
omitting only canonical Authorization is insufficient. Freeze the header map and
assume fixed process configuration, as S1-05G does; do not make concurrency-unsafe
environment mutations.

The unchanged SDK can still perform incidental profile-pointer checks for shadow
warnings even when an explicit key disables credential discovery. Do not claim
custom construction has zero configuration-file reads. It must have zero native
token/provider acquisition and must not transmit excluded credentials.

## Fingerprints, redaction and errors

Add the new module to the runner implementation fingerprint inputs. Bind the
Anthropic configuration fingerprint into per-model runtime provenance and readiness.
Add `ModelReadinessV2.anthropic_binding_fingerprint: str | None = None`, constrained
to a lowercase 64-character hexadecimal digest when non-null. A model validator
requires it for Anthropic and requires null for other providers. Advance
`READINESS_SCHEMA_VERSION` and the matching `CampaignReadinessV2.schema_version`
literal from `ori-v2-run-readiness-v11` to `ori-v2-run-readiness-v12`. The field is
part of each model receipt and therefore the existing canonical readiness hash;
it is not embedded in check-description strings. Keep the runner schema v15:
its implementation fingerprint already changes and prevents old runtime resume.
Historical v11 readiness remains preserved as diagnostic evidence but is rejected
by the current v12 parser, including status/model-card readers; no hash-repair or
silent default migration is permitted. Non-Anthropic v12 receipts include null
and are freshly fingerprinted. New tests cover required/missing/wrong-length
Anthropic digests, non-Anthropic null, and old-version rejection.
Its canonical payload has version `anthropic-binding-v1` and exactly these fields:

| Field | Canonical value / rule |
| --- | --- |
| `model_slug`, `inference_url`, `exchange_url`, `endpoint_family` | Resolved slug and admitted routes; absent exchange is JSON null |
| `selection` | One constant for native key/bearer/both, header-only, explicit/fallback OAuth, explicit/fallback profile federation, environment federation, or custom key |
| `credential_sources` | Ordered source-name list; distinguish absence, empty, and nonempty static fields without storing values |
| `effective_header_sources` | Exact header names sorted lexically, origin (profile/custom/key/bearer/dedicated), and presence/nonempty flags for canonical auth admission |
| `profile_semantics` | Only base_url, organization_id, workspace_id and authentication.type/client_id/scope/federation_rule_id/service_account_id; retain JSON types and normalize absent fields to null |
| `token_source` | Source kind plus selected absolute expanded credential-file/identity-file paths, or fixed environment variable name; no token content or file metadata |

Resolve paths without requiring token files to exist and without following token
file symlinks. Changing a selected path is a configuration change; rotating content
at the same path is not. Profile names/config-file paths are selection mechanisms,
not semantic inputs once the expanded snapshot is fixed. Unknown SDK-ignored
profile fields are dropped from both the materialized snapshot and fingerprint;
unknown auth discriminators or identity source kinds remain errors. Retain only
the SDK-recognized fields above plus selected credentials_path and identity_token
source/path in the private snapshot. This prevents arbitrary ignored fields from
being treated as nonsecret fingerprint material.

No header values are hashed: arbitrary custom headers can contain secrets even
when their names do not identify authentication. No API keys, bearer values,
identity/access/refresh tokens or token-file metadata are hashed. Public reports
receive only structural aggregate fingerprints, never paths or profile/header
values. This structural fingerprint does not establish arbitrary header-value
equivalence across separate campaigns. Same-campaign header configuration is
enforced by the following private guard, not a misleading public hash. The complete
binding snapshot remains in memory and is never serialized.

### Private header resume guard

Root owns `_guard_anthropic_headers` in `campaign_runner.py`. Invoke it immediately
inside `_exclusive_output_dir_lock`, before `_CampaignLifecycleController.start`.
Preparation remains configuration-only; `campaign-status` neither reads nor writes
this guard. Campaigns without Anthropic entries do not create or require it.

Use `anthropic-headers-v1.private.json` with an exact versioned schema:
`schema_version: 1`, `source_config_fingerprint`, and `models` keyed by unique model
name. Each model contains `model_slug`, `binding_fingerprint`, and lexically sorted
`headers` entries `{name, source, value}`. For Authorization, X-Api-Key,
Proxy-Authorization and Cookie (case-insensitive), omit `value` entirely; the
structural source/presence rules already preserve auth selection while allowing
rotation. Retain all other header values exactly and privately. Reject unknown
schema fields, duplicate entries, missing model entries and wrong JSON types.

Compare exact documents before any readiness/provenance/checkpoint write or provider
execution. A fresh output root containing only the campaign lock may create the
guard through existing durable atomic replacement (temporary file mode0600).
An existing populated output without a guard, or a malformed/mismatched guard,
fails closed with a constant value/path-free error. Never bootstrap a guard from
current configuration for an existing campaign. A matching guard-only interrupted
preparation may continue. Failed preparation creates no guard.

Reject a symlink/nonregular guard or group/world-readable guard. Store it in a
new mode0700 `.ori-private` child directory under the campaign root, rejecting a
preexisting symlink or group/world-accessible directory instead of changing user
permissions. Freshness excludes that directory only when it contains no entries.
Do not embed its contents or digest in status, reports, telemetry or error text.
This boundary protects same-campaign resume; it does not attest cross-campaign
equality of arbitrary secret-bearing headers. Public export allowlists must exclude
the entire `.ori-private` subtree. Private campaign backups must protect it.
Ignore `.ori-private/` at every repository depth to prevent ordinary staging of
untracked guards outside the default results directory. Verify root-level and nested
synthetic paths with `git check-ignore`; preserve all existing ignore rules. This
does not prevent forced staging, protect already tracked files, or replace export
allowlists and private-backup handling. This narrow addition was independently
approved during implementation review.

New process/configuration preparation may produce a new fingerprint; use a fresh
campaign output directory when it differs. Within a prepared campaign, profile
config edits do not redirect execution: its selected snapshot is authoritative.
Token-file content is not frozen. Existing campaigns require new output/readiness
and renewed stale certification after this runtime change; no automatic migration.

Translate known malformed/missing selected profile configuration and SDK auth
configuration errors into constant safe ProviderAuthenticationError messages.
Unsupported SDK/interface, route and explicit slug errors use ProviderCapabilityError.
Unexpected internal errors remain F03 harness errors; never relabel them as retryable
infrastructure. Preserve cancellation, public prompts, response-content selection,
token usage and provider request options. SDK error text containing private paths
or config values must not reach public readiness summaries.

Materialized client lifetime is bounded by one attempt-owned handle. It owns the
explicit native default async HTTP transport and separately constructed native
credential provider, registering each immediately after allocation. Do not call
`AsyncAnthropic.close()` and then separately close its provider: the SDK already
closes credentials after HTTP cleanup, and partial failure makes that ambiguous.
The handle performs the same two native operations independently: async transport
`aclose()`, then selected credential provider `close()`. Set a per-resource
close-attempt flag before calling each operation. Attempt provider cleanup even if
HTTP cleanup raises or is cancelled; never retry a failed close and never directly
close a profile's borrowed workload delegate.

Constructor failure, post-allocation auth rejection, request failure, cancellation
and success all use this one cleanup path. Retain the original request exception
or established response when cleanup raises an ordinary exception; do not expose
cleanup exception text. A newly raised cleanup cancellation propagates after the
other resource's cleanup attempt, even if a request error already exists. Use a
captured cancellation rather than allowing it to skip the second operation.
No external clients/providers are accepted by this internal public-facing API.
The async transport must use the SDK's native default transport class/options,
not HTTPX defaults that would change retries, timeouts or limits.

This claims exactly-once cleanup attempts, not unconditional closure. Allocation
that fails inside a third-party constructor before returning cannot be recovered
through a missing reference. Native token acquisition running in a worker thread
may outlive request cancellation; do not claim worker quiescence or containment.
Do not add a new cancellation wait/deadline or alter SDK token refresh behavior.
Tests must exercise these limits using finite synthetic operations only.

## Finite verification map

The revised inventory is **17 scenario functions / 142 explicit ordered cells**,
not the superseded 13/85 proposal. These are planned cases, not passing evidence.
All new functions belong to the test specialist in `tests/test_anthropic_binding.py`.
Do not collapse cases into an unobservable assertion or weaken older obligations.

| Scenario | Cells | Required cases |
| --- | ---: | --- |
| `test_anthropic_native_selection` | 16 | N00–N15 in the order below |
| `test_anthropic_inference_url_admission` | 29 | O0–O2, C0–C5, X0–X19 |
| `test_anthropic_destination_precedence` | 6 | P0–P5: model/defaults/inline/environment/profile/built-in |
| `test_anthropic_actual_request_headers` | 8 | A0–A7 below |
| `test_anthropic_native_token_compatibility` | 4 | T0 external OAuth rotation; T1 OAuth refresh; T2 profile federation cache/expiry/exchange; T3 environment identity rotation |
| `test_anthropic_binding_identity_is_pure` | 2 | I0 environment, I1 profile; identity under unconditional environment/file/SDK/token traps |
| `test_anthropic_readiness_is_configuration_only` | 4 | R0 missing token file; R1 static key; R2 missing custom key; R3 malformed explicit profile |
| `test_anthropic_direct_context_propagation` | 2 | D0 official plus slug mismatch; D1 custom plus URL mismatch |
| `test_anthropic_provenance_binding` | 3 | F0 changed route; F1 changed organization/scope then header name; F2 token/auth-header/non-auth-header-value rotation leaves structural hash unchanged |
| `test_anthropic_snapshot_and_resume` | 2 | S0 profile edit cannot redirect prepared execution; S1 fresh changed preparation rejected by actual existing-output provenance guard |
| `test_anthropic_binding_error_taxonomy` | 8 | E0 missing explicit config; E1 unsupported SDK version; E2 unknown auth discriminator; E3 unexpected internal defect; E4 missing symbol; E5 hash mismatch; E6 missing SDK source; E7 unreadable SDK source |
| `test_anthropic_attempt_cleanup` | 10 | L00–L09 below |
| `test_anthropic_legacy_ephemeral_binding` | 2 | V0 standalone preparation; V1 unchanged legacy success/error schema, tokens and request options |
| `test_anthropic_private_header_resume_guard` | 23 | G00–G22 below |
| `test_anthropic_binding_preparation_is_atomic` | 10 | B00–B09 below |
| `test_anthropic_credential_route_admission` | 6 | Q0 inherited official; Q1 explicit official normalized; Q2 custom exchange conflict; Q3 HTTP official; Q4 wrong official path; Q5 malformed port |
| `test_anthropic_readiness_binding_schema` | 7 | J0 valid Anthropic digest; J1 missing; J2 wrong length; J3 uppercase/nonhex; J4 non-Anthropic null; J5 non-Anthropic nonnull rejected; J6 historical v11 rejected |

N00 key; N01 bearer; N02 both; N03 present-empty key suppresses discovery but
denies; N04 canonical key header; N05 canonical bearer header; N06 lowercase-only
denial; N07 explicit OAuth; N08 fallback OAuth; N09 environment WIF over unselected
fallback; N10 explicit profile over WIF; N11 malformed explicit profile fails;
N12 malformed fallback swallowed while canonical-header auth succeeds; N13 static
key suppresses profile discovery; N14 canonical bearer shadows selected OAuth
without invoking it; N15 profile federation. Check exact branch kwargs, selection,
source and allowed config reads; every preparation has zero token/client/network work.

Literal URL fixtures (never derived from runtime expectations):

| ID | URL |
| --- | --- |
| O0 | `https://api.anthropic.com` |
| O1 | `https://API.ANTHROPIC.COM:443/` |
| O2 | `https://api.anthropic.com///` |
| C0 | `https://proxy.invalid/custom` |
| C1 | `http://127.0.0.1:8080/custom` |
| C2 | `http://gpu.invalid:8080/custom` |
| C3 | `http://[::1]:8080/custom` |
| C4 | `https://proxy.invalid:8443/custom/path` |
| C5 | `https://edge.api.anthropic.com/custom` |
| X0 | `http://api.anthropic.com` |
| X1 | `https://api.anthropic.com:8443` |
| X2 | `https://api.anthropic.com/v1` |
| X3 | `https://user:pass@proxy.invalid/custom` |
| X4 | `https://proxy.invalid/custom?q=1` |
| X5 | `https://proxy.invalid/custom#fragment` |
| X6 | `https://proxy.invalid:65536/custom` |
| X7 | `https://proxy.invalid:/custom` |
| X8 | `https://proxy.invalid` + backslash + `custom` |
| X9 | `http://[::1/custom` |
| X10 | `https:///custom` |
| X11 | `ftp://proxy.invalid/custom` |
| X12 | TAB + `https://proxy.invalid/custom` |
| X13 | `https://@proxy.invalid/custom` |
| X14 | `https://proxy.invalid:bad/custom` |
| X15 | `https://pro` + LF + `xy.invalid/custom` |
| X16 | NUL + `https://proxy.invalid/custom` |
| X17 | `https://proxy.invalid/cu` + DEL + `stom` |
| X18 | `/` |
| X19 | `///` |

O uses native official credentials; C requires the dedicated custom key; X raises
constant capability errors before SDK construction. Q's six exchange cases are
distinct scope checks, not a claim that all 29 vectors were replayed for exchange.
P0–P4 use distinct competing lower-priority custom destinations and a dedicated
key; P5 uses official native auth. P4 proves profile credentials are discarded.
Every admitted P cell reaches a native SDK MockTransport receiver whose URL must
match recorded identity and whose model has no inline suffix.

A0 missing custom key denies before construction; A1 canonical/lower/mixed-case
variants of all four prohibited header families are isolated, preserving a benign
header; A2 official key; A3 bearer; A4 both; A5 canonical key override; A6 canonical
bearer override; A7 lowercase bearer plus valid key. Inspect raw HTTP header
multi-items to catch duplicates; check source metadata and response usage 3/2.
T cells use real native provider/cache logic with synthetic token files and HTTP
receivers, asserting inference/exchange counts, destinations, bodies, cache paths
and cleanup. No preparation may acquire a token.

D uses the actual V2 Direct runner/adapter with synthetic graph evidence, checks
object identity of the supplied binding and exact options, and rejects mismatches
before extra materialization. F uses actual runner provenance, not just isolated
binding digests; F1 compares organization-only change before its header-name check.
F2's header-value change must separately fail G09's private guard comparison.
J0 must prove digest changes alter `_readiness`'s computed hash, not merely validate
a standalone model field. J1/J2/J3/J5/J6 assert parser failures without repair.

L00 success; L01 SDK constructor failure after owned resources return; L02 request
failure; L03 HTTP cleanup ordinary failure after success; L04 provider cleanup
ordinary failure after success; L05 both cleanup failures after request failure;
L06 request cancellation; L07 HTTP cleanup cancellation; L08 provider cleanup
cancellation; L09 profile's borrowed workload delegate. Count exactly-once owned
resource cleanup attempts and zero direct `AsyncAnthropic.close()` calls. Retain
outcomes for ordinary cleanup faults, propagate cancellation after both attempts,
and never claim unconditional closure or worker quiescence.

G00 fresh locked root/schema/0600 file/0700 directory; G01 exact guard-only restart;
G02 populated root missing guard; G03 malformed JSON; G04 wrong version; G05 unknown
field; G06 duplicate header; G07 missing model; G08 wrong JSON type; G09 non-auth
value mismatch; G10 four-family auth rotation with no saved auth values; G11 absent
versus empty non-auth value; G12 exact-case name change; G13 guard symlink; G14
nonregular guard; G15 unsafe file mode; G16 directory symlink; G17 unsafe directory
mode; G18 status with guard access trapped; G19 failed atomic replacement; G20 no
Anthropic; G21 failed preparation; G22 lock/guard-before-lifecycle/write/execution
ordering. Rejections preserve original bytes, have safe errors and zero downstream
writes/provider calls. G00 also verifies public projection excludes the private
subtree and never emits header values or their digest.

B00 complete immutable mapping installed atomically; B01 second entry fails then
retry rebuilds all; B02 first entry fails without preparing later ones; B03 repeated
readiness reuses identical bindings under discovery traps; B04 Anthropic model or
effective-default-URL mutation rejects without rebind; B05 any unsupported surface
preempts provider config access; B06 blank Anthropic slug; B07 whitespace Codex slug;
B08 distinguishable empty prepared map; B09 another provider's later readiness
failure preserves the complete Anthropic map. Use two entries where order matters;
assert mutation of the installed map fails. Do not freeze unrelated providers.

Every cell has independent counters and expected values, scoped environment,
synthetic config/token files and HTTP receivers, socket/DNS/subprocess traps,
ordered visited-ID checks and exact request assertions. No real model or BloodHound
operation is allowed. Fixture or missing-symbol errors are not regression evidence.
Helpers are `_anthropic_case_context`, `_synthetic_profile`, `_synthetic_token_file`,
`_native_attempt_receiver`, `_assert_attempt_counters`, `_prepared_campaign` and
`_guard_document`. Each context clears environment into synthetic values, redirects
SDK homes/paths into its temporary directory and traps unexpected file access,
socket connect/connect_ex, DNS and subprocess creation. Maintain separate config,
token, constructor, request and cleanup counters. Install MockTransport beneath
real SDK/provider clients; do not replace message creation in new wire tests.
Reset compatibility-check caches per cell. Use full native Messages JSON responses.

Existing F03 and G suites remain mandatory. The test specialist owns precisely
three F03 function migrations in `tests/test_anthropic_auth_boundary.py`:
`test_anthropic_native_auth_header_parity`, `test_anthropic_early_auth_cleanup`,
`test_anthropic_non_auth_fault_boundaries`, plus `_client`/`_call` helpers. Preserve
all H0–H8/L0–L2/B0–B4 IDs, outcomes, request counts and native admission comparison.
Constructor fakes assert full bound kwargs, not simply accept arbitrary kwargs.
Move literal SDK-close assertions to owned transport/provider attempt counts,
separately tracking fixture reference-client finalizers. H0/H5/H6/H8 now deny before
allocation; admitted H cases close allocated resources once. L0–L2 deliberately
change trigger from missing initial credentials to valid preparation followed by
post-allocation `_validate_headers` TypeError; preserve AUTH/no-request, ordinary
cleanup suppression and cancellation propagation. B constructor/header/request
fault categories remain unchanged and every already-returned resource is cleaned.

The same owner changes only the readiness-version literal assertion in
`tests/test_v2_runtime.py::test_v12_campaign_schemas_cannot_accept_prior_run_state`
to v12. No raw Anthropic identity tests exist to migrate. Keep existing provider
schema/MCP rejection tests and unrelated functions unchanged. Review each changed
preexisting test AST against its snapshot before acceptance.

## Challenge, controls and exit gates

Independent challenge must resolve configuration selection parity, immutable/private
storage, SDK private-interface containment, header isolation, route semantics,
fingerprint completeness/privacy and native resource ownership before edits.

After baseline snapshot and tests, implement configuration preparation, pure metadata,
execution materialization, V2 context integration, then compatibility/docs. Fault
controls run in separate processes, never editing production sources. These private
helper names are frozen implementation contracts: `_custom_api_key(snapshot)`,
`_custom_headers(snapshot, key)`, `_client_kwargs(binding, transport, credentials)`,
`_snapshot_config(binding)`, and attempt handle `aclose()`. Each test exposes its
current case ID to the process-local control plugin; restore patches before the
next cell. Missing symbols, bad signatures or fixture failures do not qualify.

| Control | Exact selector and substitution | Required failure and continuation |
| --- | --- | --- |
| FC1 | `test_anthropic_actual_request_headers`, A0 only: `_custom_api_key` returns a synthetic dedicated key despite absence | A0 auth/zero-constructor assertion fails after intercepted request; 7 other cells pass and all 7 later IDs visited; baseline 8 pass |
| FC2 | Same function, A1 only: call original `_custom_headers`, remove exactly lowercase `authorization` Omit tombstone | Raw receiver assertion detects inherited bearer; 7 pass, 6 later IDs; baseline 8 pass; exactly one tombstone removed |
| FC3 | `test_anthropic_destination_precedence`, P0 only: `_client_kwargs` replaces only base_url with `https://wrong-route.invalid` | Captured actual URL assertion fails; 5 pass/5 later; baseline 6 pass |
| FC4 | `test_anthropic_binding_identity_is_pure`, I0 only: identity wrapper attempts valid-signature re-preparation before original identity | Explicit environment/config purity sentinel fires; I1 passes/later; baseline 2 pass |
| FC5 | `test_anthropic_snapshot_and_resume`, S0 only: `_snapshot_config` reopens the exact synthetic original profile with CredentialsFile | Post-preparation profile-read trap fires; S1 passes/later; baseline 2 pass |
| FC6 | `test_anthropic_provenance_binding`, F1 only: imported identity wrapper calls original with `dataclasses.replace(binding, config_fingerprint='0'*64)` | Same-route changed-organization provenance inequality fails; F0/F2 pass, F2 later; baseline 3 pass; exactly 2 wrapped identities for this comparison |
| FC7 | `test_anthropic_attempt_cleanup`, L00 only: handle `aclose` becomes no-op | Successful native request followed by zero actual cleanup fails exactly-once counters; 9 pass/9 later; baseline 10 pass |

Each mutated cell records exact substitution count, intended assertion location,
ordered visited IDs and downstream trap counters. Parent visited-list assertions
must pass. Record all frozen source/test hashes before and after every control and
fresh unmodified baseline. Totals are 39 baseline cells; mutated runs must have
exactly 7 failed and 32 passing cells. These controls do not claim mutation coverage
of every guard/lifecycle obligation. If an independently intended second path still
binds F1 semantics, FC6 is invalid and requires an amended reviewed control rather
than counting an unrelated failure.

Run focused modules, independent source/test review, complete pytest, Ruff, offline
lock and diff checks. Reconcile exact final inventory and all control receipts.
Publish only bounded claims: initial Anthropic destination/source correction, not
redirect containment or remote readiness. Broader audit/Stage 1 and the remaining
provider, task, MCP, scorecard, budget and conference work remain open.

## Accepted completion evidence

The final 167-file source/test inventory is
`d94449bb649e873af64921abcc0d77b9448309cd75ea20635ad11c1a0c193d25`.
Full validation passed 1,167 tests and 994 subtests in 226.81s. The focused new and
migrated selection passed 21 tests and 159 subtests in 2.34s. Ruff, offline lock
and whitespace checks passed against the same frozen inventory.

Seven fresh fault controls produced exactly seven intended failed cells and 32
passing continuation cells; seven fresh baselines passed all 39 cells. Independent
review reconciled all fourteen control receipts, five gate receipts/logs and every
source hash. The full-suite log SHA-256 is
`f0e409ca8f6b0464d2e6b3954099f2a8bd0bda8c01d261951470876335089e8d`.

Early control-report parsing diagnostics are excluded. The first FC1 development
receipts were overwritten before preservation was requested; their original files
are not claimed as retained. All authoritative pairs were rerun in fresh processes,
and the limitation is explicit in the private evidence manifest. No acceptance
claim depends on those early diagnostics.

The current readiness schema is v12. Keep the new private guard protected, renew
stale certification and use fresh campaign output directories. No live provider,
graph, GPU, remote Git, signing, PR or merge work occurred in this slice.
