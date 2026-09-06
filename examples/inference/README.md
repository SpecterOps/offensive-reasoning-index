# ORI inference configuration examples

These templates show common ways to connect ORI to local or hosted inference
providers. They are intentionally sanitized and disabled by default.

ORI connects to an already running inference API. It does not require a personal
agent, GPU reservation helper, particular hostname or model-service manager.
Replace the endpoint with your local or remote service address. Provisioning and
service lifecycle are operator responsibilities outside the benchmark; the
chosen deployment tool does not change ORI's configuration contract.

Use them as copy-and-edit starting points:

1. Copy one template to a local run config, for example:

   ```bash
   cp examples/inference/openai-compatible.yaml run-config.local.yaml
   ```

2. Replace placeholder model names, base URLs, manifest paths, output paths, and
   MCP checkout paths for your environment.
3. Put secrets in environment variables or your provider gateway. Do not commit
   API keys, private hostnames, local workstation paths, or production
   BloodHound credentials.
4. Run a preflight check before launching an evaluation:

   ```bash
   uv run ori run --config run-config.local.yaml --profile preflight_local
   ```

5. Launch an evaluation profile only after BloodHound health, ingest, and
   preflight mock gates pass.

## Templates

- `openai-compatible.yaml` — generic OpenAI-compatible endpoint; works for many
  hosted gateways and local servers that expose `/v1/chat/completions`.
- `ollama.yaml` — local Ollama using ORI's native Ollama/MCP path.
- `llama-cpp.yaml` — llama.cpp server with OpenAI-compatible API.
- `vllm.yaml` — vLLM OpenAI-compatible server.
- `lm-studio.yaml` — LM Studio local OpenAI-compatible server.
- `openrouter.yaml` — OpenRouter via OpenAI-compatible API.
- `nous.yaml` — Nous Portal via its OpenAI-compatible inference API.
- `nvidia-nim.yaml` — NVIDIA NIM via OpenAI-compatible API.
- `bloodhound-mcp.yaml` — BloodHound MCP and BloodHound CE environment shape.

## Safety boundary

These examples are public-safe templates. Real inference routing files should
remain local-only when they contain private hostnames, private model aliases,
local output directories or internal network details.

The Nous template includes disabled direct and MCP profiles for the current Ox
Alpha catalog entry, `openai-compat/stealth/ox-alpha`. Set `NOUS_API_KEY` (or
`NOUS_PORTAL_API_KEY`) in the environment and enable only the profile you have
validated against your controlled BloodHound target. Start with the direct
profile; tool-enabled compatibility may vary for this newly released model.

## Protocol V2 API surfaces

V2 model entries accept `api_surface: auto | chat_completions | responses`.
Release 1 preserves existing behavior: Codex OAuth resolves `auto` to Responses,
while official OpenAI and OpenAI-compatible endpoints resolve it to Chat
Completions. OpenRouter and Nous therefore use `auto` or explicit
`chat_completions`. Other explicit Responses selections fail readiness before
model usage.

Credentials are endpoint-isolated. OpenRouter uses `OPENROUTER_API_KEY`, Nous
uses `NOUS_API_KEY` or `NOUS_PORTAL_API_KEY`, official OpenAI uses only
`OPENAI_API_KEY`, and an unrecognized compatible endpoint uses only the explicit
`OPENAI_COMPAT_API_KEY` override. Compatible endpoint selection does not borrow
another provider family's key.

OpenRouter and Nous scoped keys require a parsed HTTPS origin with no URL
username/password and no explicit port other than 443. HTTP, other ports and
userinfo-bearing recognized-provider URLs fail credential admission; a generic
key does not bypass that rejection. Existing OpenRouter apex/subdomain and Nous
host classification is preserved, not an endorsement of every classified host
as an inference service. Use the provider's supported secure endpoint.

Generic custom servers still support explicit generic keys, custom ports and
local HTTP. These checks concern the parsed origin, not complete URL grammar,
redirect containment or provider capability. Keep paths and model IDs consistent
with the API you intend to evaluate.

Provider-auth/runtime changes invalidate older V2 resume fingerprints. Use a
fresh output directory and rerun readiness, renewing any stale certification
before execution. A readiness pass never authorizes provider spending.

## Gemini's fixed endpoint

The `gemini` provider uses Google's fixed OpenAI-compatible endpoint at
`https://generativelanguage.googleapis.com/v1beta/openai/` and only
`GEMINI_API_KEY`. V2 records that actual SDK base URL in endpoint provenance.
Model/default URL settings do not redirect Gemini; they remain usable for other
providers in a mixed matrix. No Gemini endpoint environment override is supported.

Use the exact Gemini model ID. Inline `model@URL` routing is unsupported: its
suffix remains part of the submitted model name, not a destination override.
Gemini remains Direct-only in certified V2 campaigns. Readiness checks local
configuration and credential presence without contacting Google. Older runtime
evidence needs fresh certification/readiness and a fresh campaign output root.

## Native Ollama destinations

Use `model: ollama/<model-name>` and a separate `model_base_url` for native
Ollama. In V2, a model's URL overrides the defaults URL; otherwise ORI selects
`OLLAMA_BASE_URL`, or `http://localhost:11434` when that variable is unset.
The native Direct and MCP paths remove trailing slashes and one terminal `/v1`,
then append `/api/chat`. Supply the server root, not `/api` or `/api/chat`.
Remote HTTP servers, custom ports and IPv6 addresses remain supported.

V2 prepares that selection once for readiness, provenance and both execution
tracks. Changing the environment afterward does not redirect the prepared
campaign; reload the configuration to select a different server. Changed prepared
Ollama model/URL settings also require reloading. A newly selected destination or
changed runtime requires fresh certification/readiness and a fresh output root.

Ambiguous `ollama/<model>@<URL>` input now fails early: previously its suffix
could affect V2 routing while remaining embedded in the model name sent to
Ollama. Move the URL to `model_base_url`. Empty or slash-only selected destinations
also fail instead of falling back to another server. Readiness does not contact
Ollama or establish that a model is installed or available.

## Provider failures versus harness defects

Known authentication, transport and provider-protocol failures retain explicit
provider classifications. An unexpected adapter/SDK programming error is a V2
`HARNESS_ERROR`, invalidates campaign results and is not retried as infrastructure.
At the Python transport boundary it raises `ProviderAdapterInternalError` with
the original cause; legacy `call_model` still returns its `MODEL_ERROR` artifact.
Cancellation and whole-task deadlines retain their separate handling.

Missing or malformed Codex credential files now produce nonretryable
authentication failures with path-free messages. File-token parsing precedence
is unchanged; endpoint-specific credential selection is described below.
Anthropic preserves native configuration selection through a private prepared
binding and uses its locked-version header-admission check to identify absent
authentication; this does not validate a deferred token exchange. Other unmatched
Anthropic SDK errors remain nonretryable provider failures, not inferred auth
failures. No readiness or offline test proves provider access.

This runtime change requires fresh V2 output directories and readiness, plus
renewal of stale certification. Do not resume an older campaign or reinterpret
historical infrastructure failures as newly measured harness results.

## Codex destinations and credentials

The built-in `https://chatgpt.com/backend-api/codex` destination uses
`CODEX_API_KEY` when nonempty, otherwise the selected Codex OAuth file.
`OPENAI_API_KEY` is no longer a Codex fallback. Custom Codex-compatible servers
require their own explicit `CODEX_COMPAT_API_KEY`; ORI never sends the built-in
key or OAuth-file token to a custom destination. Keep all keys in the environment,
not checked-in configuration. An explicit model URL overrides defaults, followed
by the inline model URL, `CODEX_BASE_URL`, and the built-in URL.

The built-in hostname requires HTTPS, port 443 or no explicit port, and the exact
API path above. Invalid built-in variants fail rather than becoming custom
destinations. Custom endpoints retain HTTP/HTTPS, custom paths and ports, and IPv6
support for standalone CLI deployments on other machines. HTTP is an explicit
plaintext transport choice. Userinfo, queries, fragments, malformed ports,
backslashes and ASCII whitespace/control characters are rejected. These checks
do not validate redirects, DNS, TLS certificates or remote model capabilities.

No-model readiness validates the actual selected OAuth file before shared local
login/cache checks. Those checks do not prove that an overridden auth file belongs
to the local CLI account. Explicit-key destinations only record configured
capabilities and requested effort; they do not contact a provider or claim model
access. Endpoint and credential-source changes affect campaign fingerprints;
secret values and credential-file paths are excluded. Use new campaign output
directories, fresh readiness and renewed stale certification after this migration.

## Anthropic destinations and prepared credentials

Anthropic Direct requests resolve the model URL, defaults URL, inline URL,
`ANTHROPIC_BASE_URL`, selected native profile URL, then `https://api.anthropic.com`.
The inline URL is not part of the model identifier sent to the provider. The
official hostname requires HTTPS, no explicit port other than 443, and no path.
Invalid official variants fail closed. Custom HTTP/HTTPS servers, ports, paths
and IPv6 remain supported with a dedicated `ANTHROPIC_COMPAT_API_KEY`.

Custom destinations do not inherit native API keys, bearer tokens, OAuth profiles
or workload identity. Inherited Authorization, X-Api-Key, Proxy-Authorization and
Cookie headers are removed case-insensitively before adding the dedicated key.
Keep keys and custom-header values outside checked-in configuration. This is
initial-destination admission, not redirect, DNS, TLS or remote capability proof.

For the official destination, native key/bearer, canonical custom-header,
OAuth-profile and federation configurations remain available. Preparation freezes
configuration and selected token-source paths without opening token files or
contacting providers. Readiness reports `configuration-validated-not-probed`.
Credentials may consequently fail at execution even after configuration readiness
passes. Anthropic MCP remains unsupported by this release.

A prepared campaign cannot silently pick up edited profile routing. Native token
rotation and refresh remain execution-time behavior; a changed configuration needs
a fresh campaign. ORI checks the pinned Anthropic SDK version and credential-source
interfaces; upgrades and patched SDK packages require renewed qualification.

Arbitrary header values are not included in public fingerprints. Instead, a
mode0600 `.ori-private/anthropic-headers-v1.private.json` guard inside a mode0700
directory checks same-campaign resume. Authentication values are omitted to allow
rotation; other header values remain private because they may also be sensitive.
Never publish this directory or treat a structural public fingerprint as proof that
arbitrary header values were identical across separate campaigns. A missing,
malformed, insecure or mismatched guard on an existing Anthropic campaign blocks
resume; do not delete it to bypass the check.
The repository ignores these private directories at any depth to prevent ordinary
staging of untracked guards. Forced staging, already tracked files and external
archive tools still require explicit operator care.

This migration advances readiness to v12. Older v11 readiness is historical
diagnostic evidence, not a current resume or model-card input. Use a fresh output
directory, fresh readiness and renewed stale certification before execution.

## Inference redirects

ORI's native inference transports do not follow HTTP redirects, including redirects
to another path on the same origin. Configure the final supported inference URL
instead of a gateway URL that redirects. Redirect responses are provider protocol
failures, not model reasoning failures. Destination admission, DNS/TLS behavior and
remote model capability remain separate checks.

SDK retry defaults are unchanged: a provider retry header may cause another request
to the original URL, but never authorizes following its Location. This policy does
not establish billable HTTP attempt counts. Raw provider diagnostics can include
sensitive response or redirect details; keep private artifacts private and publish
only ORI's redacted reports.

Anthropic's native credential refresh and federation exchanges use separate SDK
transports. This inference policy does not claim live qualification or exhaustive
redirect testing of those exchange flows. No-model readiness remains provider-free.

The transport change invalidates prior runtime/readiness/resume fingerprints and
MCP certification. Recompile, renew certification and readiness, and choose a fresh
campaign output directory before later execution. Standalone CLI operation needs
no agent framework or machine-specific orchestration service.

## Native Ollama stream completion

Direct and native MCP inference require one boolean `done: true` marker followed
by the end of the HTTP response body. Intermediate chunks may omit `done`;
metrics-only terminal chunks and blank lines remain supported. Missing completion,
malformed consumed fields, duplicate completion and post-terminal payload fail as
provider protocol errors. Existing HTTP, read, no-progress and whole-task deadlines
still apply. An explicit in-stream provider error is separately recorded as
`PROVIDER_GENERATION_ERROR`, without guessing a retry cause from its message.

`done_reason: length` is a truncated model output, not a provider failure. It cannot
be accepted as a Direct answer or dispatch native MCP tool calls. Native MCP may
still use the existing single, tools-disabled schema repair after qualifying
evidence; the existing lexical fact guard rejects new answer facts not present in
the model's original output. The original truncated text is retained only in private diagnostics and
the repair transcript, and remains subject to output/transcript byte limits.
Only redacted public reports may be published; do not export private records.
Native repair requests preserve prior tool arguments as objects. Missing,
malformed or non-object arguments in that private transcript stop repair before
transport; ORI does not invent replacement arguments.

This stream-contract change requires fresh compilation, certification, readiness
and a new campaign output directory. Passing offline fixtures does not certify a
particular live Ollama version, model artifact or serving configuration.

## Codex completed-stream integrity

Codex Direct and native MCP use the completed response's output list as the
authoritative text and tool-call inventory. Streamed text and completed tool
events must agree with that inventory. Duplicate completions, post-completion
events, duplicate call IDs and conflicting output are provider protocol failures;
they cannot admit partial tool calls. Explicit provider error/failed/incomplete
events retain their existing failure classification.

Terminal-only output and optional function-item IDs remain supported. Tool-call
IDs and argument strings are not invented. Empty argument strings retain the
existing downstream empty-argument behavior; this change does not introduce a new
tool-argument policy. Refusal fallback and existing request settings are preserved.

Changing this contract invalidates runner and MCP finalization fingerprints.
Recompile, recertify, rerun no-model readiness and choose a fresh output directory;
do not resume older campaigns. Offline fixtures do not qualify a live provider.
