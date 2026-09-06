# Remaining provider destination mismatches — preparation only

September 6. Read-only repository audit while S1-05G validates. This is not an
implementation plan or authorization to change another provider's behavior.

## Later checkpoint: Anthropic accepted, Ollama validating

The original table and prioritization below are historical. S1-05I has since
closed the bounded Anthropic destination/authentication work. S1-05J implements
Ollama reconciliation and is undergoing frozen final validation. A fresh
read-only Gemini audit confirms the remaining smallest F01 correction:

- Direct always uses `https://generativelanguage.googleapis.com/v1beta/openai/`.
  Supplied model/default URLs are ignored; Gemini does not use the compatible
  provider's inline-suffix model parsing.
- V2 still records model/default/inline URL or nothing, so its endpoint identity
  does not necessarily describe that fixed runtime destination.
- Only `GEMINI_API_KEY` is admitted, and a missing key fails before SDK creation.
  Gemini remains unsupported by native V2 MCP.
- Smallest behavior-preserving correction: share the exact endpoint constant
  between the adapter and V2 resolution, before generic inline extraction. Keep
  actual model payload spelling and ignored-URL behavior unchanged. Rejecting
  configured URLs or inline syntax would be a separate compatibility migration,
  especially in mixed-provider matrices with a global default URL.
- Acceptance must cross typed configuration, effective endpoint fingerprint and
  actual adapter request capture. Cover absent/model/default/inline URLs, scoped
  credentials, model spelling, structured output, timeouts and MCP rejection.
  Distinguish SDK base URL from final `/chat/completions` request URL; equal
  effective endpoint hashes do not imply equal overall source-config provenance.

This is a factual handoff, not an approved next implementation plan. Do not add
Gemini environment routing, custom credentials, cleanup or response-parser changes
to a constant/provenance reconciliation slice.

## Next correctness triage after endpoint reconciliation

Fresh read-only source review prioritizes F06 redirect containment, then F04
terminal-response integrity and F05 duplicate tool-call identity. The accepted
Anthropic materializer still constructs `DefaultAsyncHttpxClient()` without a
redirect override; the prior finite synthetic redirect reproduction must be
renewed through this current bound path before accepting a fix. Inference and
token-exchange requests are different transport surfaces: the pinned SDK's
WorkloadIdentityCredentials and config-provider token clients use ordinary
`httpx.Client(timeout=TOKEN_EXCHANGE_TIMEOUT)`, not the async inference wrapper.
Do not infer token-exchange redirect behavior from the inference result.

Other confirmed source findings remain: Ollama Direct can return unterminated
partial streams as normal text; Anthropic takes its first content block without
terminal normalization; Codex translator accepts duplicate/missing call IDs and
conflates incomplete/failed/missing-terminal outcomes. Separate these execution-
integrity decisions from later metric-policy expansion (cached/reasoning tokens,
TTFT, cost, unknown usage and internal SDK retries). All require fresh reviewed
plans and offline fixtures, not live provider probes at this stage.

Stable compiler/schema/comparator test-consolidation audits are independent of
these provider fixes. Preserve lifecycle/cancellation and negative-boundary
obligations; reduce genuinely repeated setup, not counts through wrapper-only
renaming. The fewer-than-700 target remains unmet.

## Original preparation record

The remaining F01 findings are outside the Codex binding slice:

| Provider | Execution | V2 provenance gap |
| --- | --- | --- |
| Anthropic | Direct constructs the native SDK client without the configured URL; SDK configuration selects its destination. | `_model_base_url` records a configured/default/inline URL or nothing, not necessarily that destination. Readiness checks only the API-key source while native admission supports additional sources. |
| Ollama | Direct and native MCP resolve explicit URL, environment and localhost; normalize trailing slash and `/v1` before `/api/chat`. | V2 misses environment/default fallback and does not share request normalization. |
| Gemini | Direct always uses the fixed Google OpenAI-compatible endpoint. | V2 can record an unrelated configured URL or no URL. |

The next detailed plan should prioritize Anthropic's destination/credential
boundary. Merely passing the configured URL into the SDK would newly transmit
native credentials to previously ignored custom destinations. First decide the
official/custom admission and credential-source policies, configuration precedence,
native discovery compatibility, and pure readiness/provenance interfaces. Reproduce
the current mismatch with a fake SDK and real typed configuration before editing.
Keep Anthropic MCP unsupported in this slice; do not conflate Direct admission
with adding a native tool loop.

Retain the existing native header-admission, early cleanup, non-authentication
failure and Direct-only provider-gating tests. No real credential discovery, SDK
transport, login, model, graph, host or remote Git operation is needed to prepare
or validate the bounded change.

Ollama should subsequently share destination resolution while retaining remote
HTTP, custom ports and `/v1` compatibility. Distinguish the configured server root
from the final `/api/chat` URL and explicitly settle inline-model suffix behavior.
Gemini should retain its fixed endpoint and scoped key; rejecting conflicting URL
configuration would be a documented migration, not behavior-preserving cleanup.

Redirect containment, response termination/content handling and SDK retry/token
accounting remain separate findings. None is closed by this source audit. Each
implementation slice still requires a fresh detailed plan, independent challenge,
resolved findings, bounded implementation and independent final evidence review.
