# S1-05H — Anthropic endpoint and credential characterization

September 6, revision 2. Complete; independent final evidence review approved
this bounded characterization. This is a bounded
Stage 1 review/evidence step, not approval to change the provider implementation.
The subsequent endpoint-binding implementation still needs its own complete plan.

## Why this review is on the critical path

S1-05G is accepted at the 165-file inventory
`c669a68e93707db6a88e2e446b1ca163bc37899392e544cc8f55a375cb4307eb` with 1,150
passing tests. Remaining F01 records an Anthropic endpoint mismatch. A source
audit shows that fixing the constructor argument alone would not address native
profile token exchanges or inherited authentication headers. Characterize those
interfaces before deciding the implementation's compatibility boundary.

The locked SDK is Anthropic 0.116.0. No new package or dependency is permitted.
Treat its exact installed source, not recollection of older releases, as evidence.
This review does not change the broader schedule, native MCP breadth, test-count
target, hosted-model matrix or publication gates.

## Ownership and writes

Root owns this plan, the progress/preparation documents and private probes under
`results/p2-slices/s1-05h/`. The source auditor reads the locked SDK and existing
ORI tests. The independent reviewer challenges the plan and final evidence.
No production, collected test, dependency or schema edit is in scope. No commit,
signing, remote Git, service, model, credential-file or real network operation.
All profile/config/token-like values used by probes are newly generated synthetic
fixtures. No real operator configuration is read.

## Existing discovery evidence to retain and revalidate

Use the actual ORI Direct adapter, typed V2 configuration and locked SDK with an
in-memory HTTP transport. Four distinct cases must each make exactly one synthetic
request and produce a successful typed SDK message:

| Case | Recorded V2 root | SDK root |
| --- | --- | --- |
| Explicit model URL versus environment | configured synthetic root | environment synthetic root |
| Defaults URL versus environment | defaults synthetic root | environment synthetic root |
| Environment only | absent | environment synthetic root |
| No configured/environment URL | absent | built-in official root |

Assert both request URL and synthetic API-key presence. Record actual constructor
arguments: currently empty. Record `_model_base_url`, `_provider_identity` and
`_provider_endpoint_fingerprint` from the actual typed configuration. Do not infer
an on-wire response from constructor arguments alone.

Two additional configuration-only cases use the real SDK and `CredentialsFile`
with an explicit synthetic profile containing a custom `base_url`, `user_oauth`
authentication type and synthetic `client_id`. Without an explicit inference URL, both inferred destinations
are the profile root. With the official inference URL explicitly supplied, the
inference URL changes but the credential provider's configured potential refresh root remains
the synthetic profile root. Never invoke the credential provider or token cache.
Assert that only the synthetic config file exists and no credentials file or
token cache was read or written. These cases prove resolved configuration, not
an actual OAuth refresh, federation exchange, remote authentication or leakage.

## Additional header characterization

Use the exact SDK class with a dedicated synthetic explicit API key and a custom
synthetic HTTPS root, with native API-key and bearer environment sentinels also
present. The explicit SDK credential should suppress those two environment fields.
Then cross actual request construction and in-memory transport for three cases:

1. H0: no custom-header environment. Only the explicit API key appears; no bearer.
2. H1: `ANTHROPIC_CUSTOM_HEADERS` contains canonical `Authorization: Bearer` with a
   distinct synthetic header sentinel. It appears on the built request despite
   the explicit API key. This is current SDK behavior, not desired admission.
3. H2: the environment contains lowercase `authorization: Bearer` with that
   sentinel, while constructor `default_headers` omits only canonical
   `Authorization` using `anthropic.Omit()`. Verify the lowercase bearer survives
   case-sensitive dictionary merging and reaches HTTPX's built request.

Require one constructor, one request and explicit cleanup in each case. Inspect
all values of both authentication header names case-insensitively, not only
`client.api_key`. Assert native environment-key/token sentinels never appear and
the dedicated explicit key remains present. Do not remove SDK logic or replace
its header builder with a fake. Any unexpected count or outcome is evidence that
the proposed characterization needs revision; do not weaken it to pass.

## Containment and evidence rules

Clear the process environment inside each probe scope; inject only synthetic
values. Use HTTPX MockTransport with `trust_env=False`, trap socket/DNS and
subprocess entry points, and forbid credential-provider invocation/credential
file reading. Credential discovery is prohibited except explicit synthetic
profile configuration construction in the two profile cases. Patch the SDK's
platform-label helper to a constant so platform identification cannot spawn
local commands. Patch incidental SDK shadow-warning helpers, which otherwise
probe local profile pointers; these warnings are not part of routing/auth proof.
Document these substitutions precisely rather than claiming the entire native
constructor is filesystem-free.

An initial exploratory run hit the subprocess trap through platform identification;
it is not acceptance evidence. Final runs must show zero trapped operations.
The in-memory transport must be the only HTTP receiver. Do not claim redirect
containment or run redirect probes in this step.

Hash the production/provider source inputs used in the proof where applicable,
all inspected SDK files and each final script/log/receipt. Verify the complete
accepted 165-file inventory before and after the final characterization. Retain
all seven HTTP cases and both profile cases in one auditable receipt set; no
real provider usage or new generated benchmark release is involved.

## Acceptance and handoff

Independent review must confirm the four V2-to-request mismatches, the two
configuration-only profile observations, the three actual-header observations,
the substitutions/traps, source hashes and unchanged accepted inventory.
Documentation must distinguish demonstrated runtime behavior, configuration-only
evidence and source-derived implications.

The resulting implementation handoff must explicitly require decisions for both
inference and token-exchange destinations, native official key/bearer/header/profile
compatibility, custom-key isolation, configuration-only readiness and pure
provenance. It must preserve the accepted F03 error/cleanup tests, keep Anthropic
MCP unsupported until its separate integration stage, and require fresh campaign
outputs/certification after any subsequent runtime migration.

This characterization closes only the bounded review step. It does not close F01,
F02, native credential isolation, redirect containment, remote capability checks
or Stage 1. Stop on an actual external attempt, unexpected file access, mismatched
inventory, unreviewed SDK version, or need for new operator authority.

## Independent challenge disposition

Revision 1's containment and header cases were accepted in principle. The reviewer
identified that a profile without `client_id` uses externally rotated tokens, so
describing it as refresh-capable was too strong. Revision 2 adds a synthetic client
ID and limits the claim to configured potential refresh routing, never an actual
exchange. Final probes explicitly assert each expected SDK/request URL and key
presence rather than only recording them. Re-review is required before the three
new header cases are implemented.

Revision 2 was independently approved. All seven in-memory HTTP and two synthetic
profile cases passed, with the exact source boundary unchanged. See the
[characterization evidence](2026-09-06-oaic-anthropic-characterization.md) for
results, limitations, evidence hashes and the subsequent implementation handoff.
Independent final review verified the seven HTTP cases, two profile constructions,
all SDK/evidence hashes, unchanged inventory, containment and qualified claims.
It did not approve a production fix or broader F01/F02 closure.
