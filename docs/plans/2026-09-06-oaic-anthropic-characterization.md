# Anthropic destination and credential review evidence

September 6. S1-05H characterization completed locally and independently accepted.
This is review evidence, not an implemented provider correction.

## Demonstrated behavior

The locked Anthropic 0.116.0 SDK was exercised through in-memory HTTP transport.
Four cases crossed ORI's actual Direct adapter and typed V2 configuration. All four
made one synthetic request with the synthetic key, and showed that the recorded
V2 destination was not the actual SDK destination:

- A model URL and a defaults URL were recorded but ignored by the SDK constructor.
- An environment URL controlled execution but was absent from V2 endpoint identity.
- With no URL configured, the SDK used its official default while V2 recorded none.

Two real SDK constructions used a temporary synthetic OAuth profile with a client
ID. Pinning inference to the official endpoint did not replace the credential
provider's configured potential refresh base URL. These are configuration-only
observations: no credential token file was read, no token provider was invoked,
and no refresh/exchange request was made.

Three additional actual SDK request-build cases established the custom-header
boundary. An explicit API key suppressed the native API-key and bearer environment
variables. However, an inherited `Authorization` custom header still reached the
request. A lowercase `authorization` header also survived an explicit omission of
only canonical `Authorization`. Therefore, a dedicated custom key alone is not
sufficient credential isolation; all inherited authentication-header spellings
must be handled deliberately by the implementation design.

## What the SDK source additionally establishes

Native API-key and bearer fields can both be present. Profile selection and config
parsing can occur during construction, while token acquisition/refresh is deferred
to request authentication. A profile's configured base URL takes precedence over
the client's bound fallback for credential-provider routing. Native environment
workload federation and file-backed profile credentials do not have identical
binding rules. Arbitrary credential callables need separate consideration.

Incidental SDK shadow-warning logic can inspect local profile pointers even with
an explicit static key. The probes substituted those warning helpers and the
platform-label helper, trapped external/discovery operations, and used only
synthetic profile configuration. They do not prove that an unmodified native SDK
constructor never accesses local configuration. The current SDK behavior is not
being called an observed real credential leak.

## Verified local evidence boundary

- Seven synthetic HTTP requests and two configuration-only profile constructions.
- Zero trapped operations in the final run; no real provider/network/token access.
- Exact expected request URLs, header presence/absence and cleanup assertions.
- Unchanged 165-file inventory:
  `c669a68e93707db6a88e2e446b1ca163bc37899392e544cc8f55a375cb4307eb`.
- Final receipt/log SHA-256:
  `f78ad4ae7c5c2fbfb23624fc2f6f1875a64c3a93a6a2b4b94f7621365c4bb1d2`.
- Locked SDK client SHA-256:
  `240329ad19a78f1b990b1856a78c8e42691c54244cf60f8f55da96074e53c944`.

The existing 1,150-test accepted baseline is unchanged; no new full-suite result
is claimed for this documentation/private-probe-only step. The first exploratory
platform probe hit a prohibited subprocess trap and was excluded from acceptance.
The refreshed profile fixture includes a client ID and is not conflated with the
SDK's externally rotated, non-refreshing token mode.

## Required next implementation design

The next detailed binding plan must settle both inference and credential-provider
destinations before changing the constructor. It must specify native key/bearer,
custom-header and profile compatibility; dedicated custom-key isolation across
all header spellings; configuration-only readiness versus token acquisition;
pure provenance; error redaction; and source/endpoint fingerprint migration.
Do not silently disable native credentials or claim an inference URL alone binds
all credential exchange. Preserve F03 failure/cleanup obligations and the existing
Anthropic Direct-only boundary. Any runtime migration requires new campaign output,
fresh readiness and renewed stale certification before publication.

F01/F02, redirects, remote capability validation and Stage 1 remain open. No
production fix, provider access, graph operation or remote Git operation was made.
