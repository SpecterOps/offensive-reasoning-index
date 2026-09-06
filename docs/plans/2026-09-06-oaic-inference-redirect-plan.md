# S1-05L — Inference redirect containment

Status: implemented and independently accepted. September 6, 2026.

## Entry and objective

Accepted K boundary: 170 source/test/script/lock files, inventory
`83851473ebb2c4e154089026851a44df43360b64d3f48762d5fc74ef171d9a5e`;
1,181 tests and 1,082 subtests passed. Preserve the entire dirty consolidated
branch. This slice does not close Stage 1, satisfy the under-700 test target,
qualify a live provider, or authorize a campaign.

SDK-backed inference currently follows redirects by default; raw HTTPX paths
do not. A prepared destination must not silently become another destination
after a server response. Disable following for every native inference transport,
including same-origin redirects. Preserve successful requests, credentials,
request payloads, streaming, timeouts, native connection limits and SDK retries.
Also close the presently unowned Direct Chat Completions transport.

No real provider, socket, graph, credential-file, service, local-model or remote
Git operations. No commits, signing, push, PR or merge. Only synthetic credentials
and real SDK requests through in-memory HTTPX transport. ORI stays standalone;
no Hermes, reservation or private-machine dependency.

## Fixed implementation decisions

Add `src/ori/eval/provider_transport.py` with async context manager
`openai_inference_client(**kwargs)`. Import OpenAI lazily. Reject caller-supplied
`http_client` with constant ValueError before allocation. Create the SDK's
`DefaultAsyncHttpxClient(follow_redirects=False)` (not a bare replacement with
different SDK defaults), then create `AsyncOpenAI(http_client=transport, **kwargs)`.
Yield the client. Catch only real OpenAI APIStatusError with status 300–399 and
translate to ProviderProtocolError with constant message
`Provider redirects are not supported; configure the final endpoint`.
All other exceptions retain their current classification and traceback.

Own the transport in a finally covering both SDK construction and request use.
If its is_closed is false, attempt transport.aclose once in this finally.
Suppress ordinary Exception from that fallback cleanup so it cannot replace a
successful result or primary failure; propagate cancellation/BaseException.
Do not claim actual closure after a failed close. Existing Codex client.close
finally blocks remain unchanged inside the context; therefore an unsuccessful
SDK close can be followed by an independent transport fallback attempt. There
is no exactly-once transport-attempt claim across those two owners. Direct Chat
Completions gains transport cleanup, without requiring fake SDK clients to grow
a close method or changing normalization behavior.

Use the helper at all three OpenAI SDK sites: Direct OpenAI/compatible/Gemini,
Direct Codex and native MCP Codex. Existing credential and destination admission
must precede transport creation. Keep complete request/stream consumption inside
the context. Do not translate exceptions in a send hook: SDKs can reclassify such
exceptions and retry them as connection failures.

Set follow_redirects=False explicitly at all three raw HTTPX sites: Direct
Ollama, native MCP Ollama and native MCP compatible. Set the same option on the
existing Anthropic-owned DefaultAsyncHttpxClient and retain its accepted cleanup
handle unchanged. No request-level follow_redirects override is introduced.

Native SDK retries remain unchanged, including an x-should-retry:true response
which may repeat the original request without following its Location. Exactly
one request is asserted only for ordinary redirect responses without that
header. Do not set max_retries=0 in production or infer billable attempt counts.
Redirect statuses are nonretryable provider protocol failures at the harness
boundary; SDK-internal retry accounting remains separate deferred work.

Anthropic OAuth refresh/federation exchanges use separate native synchronous
HTTPX clients which currently default to no following. Do not change or claim
dynamic qualification of those credential-exchange paths in this inference
slice. SDK source inspection is evidence only of their current default. A later
auth-exchange acceptance slice must exercise both exact native flows before
claiming end-to-end credential-exchange containment.

## Migration and fingerprint contract

Legitimate same-origin/gateway redirects now fail closed. Operators must configure
the final supported inference URL; ORI neither rewrites Location nor resends
credentials to it. No serialized schema changes. Add the new module to both
V2 runner implementation sources and MCP finalization sources; existing changed
adapter, binding and runtime sources are already fingerprinted. This invalidates
old runtime/readiness/resume and MCP certification. Require recompilation,
certification, readiness and a fresh output directory for later execution.

Public docs describe only inference policy, unchanged SDK retries, the migration
and separate unqualified token-exchange boundary. OpenAI SDK redirect errors use
the constant message. Existing raw HTTPX and Anthropic private diagnostics can
contain Location or response body and remain private; do not change these strings
in this slice. Actual public projections must exclude them and credential/URL
sentinels. Private chained exceptions retain the existing diagnostic boundary.
No new public transport telemetry is introduced.

## Ownership and sequence

Root owns this plan, snapshot, new transport module, adapter.py, mcp_runtime.py,
anthropic_binding.py, V2 campaign_runner.py and mcp.py plus public documentation.
Test specialist owns new tests/test_provider_redirect_acceptance.py and only these
existing compatibility edits: Codex reasoning-effort test replaces whole-module
fake with a patch of real AsyncOpenAI; Gemini _case injects MockTransport into the
new owned HTTP client while preserving fixture closure and all 20 cells; two
provider-auth exact kwargs tests additionally assert owned HTTP client's no-follow
and closed properties without dropping existing key/base assertions.
All owners share the worktree; preserve each other's changes.

Independent reviewer challenges this plan before production/test edits. Root
snapshots all 170 entry files and approved plan, then implementation/test work
proceeds in disjoint scopes. Reviewer checks source/tests before frozen gates.
Private drivers are append-only evidence, never overwrite diagnostic runs.

## Finite acceptance tests

Use grouped scenarios with stable case IDs and fresh isolated case state. Patch
HTTPX construction to attach MockTransport while leaving the SDK's actual
transport class, follow_redirects option, and request machinery intact. Trap
socket connection/name resolution and subprocess creation; sanitize environment
and home paths. Never read operator credentials. Count actual received requests,
their URL, credential header, payload and transport closure separately.

`test_inference_cross_origin_redirects`, R00–R39: eight entry paths times five
statuses 301/302/303/307/308: Direct OpenAI,
Direct compatible, Direct Gemini, Direct Codex, Direct Anthropic, Direct Ollama,
native MCP Codex and native MCP compatible. R40–R44 cover native MCP Ollama.
For each path use cross-origin Location carrying a synthetic secret sentinel;
assert exactly one original request, zero Location requests, typed nonretryable
protocol outcome, no Location/credential/body sentinel in actual public projection,
the constant private error for translated OpenAI paths, and owned
transport closure. Cross actual adapter/native request entry points, not merely
the context helper. Native errors cross the V2 infrastructure classifier to
prove protocol rather than HARNESS attribution. SDK retries remain at defaults.

`test_inference_same_origin_and_success`, S00–S08: same nine paths with a
same-origin 307; assert the same rejection and
no second request. S09–S17: successful 200 per path, including valid Codex SSE
and Ollama done receipt; preserve representative model/messages, token counts
and result text and verify transport closure. Do not claim terminal-parser
coverage beyond those valid receipts. S18: OpenAI response 307 with retry header
true and second identical 307 without header: two original requests, no redirect
destination, protocol failure. Patch SDK retry sleep only, not retry decisions.

`test_inference_transport_lifecycle`, L00–L06: helper ownership and
classification: successful fake SDK construction;
constructor raises; request raises ordinary ValueError; actual 3xx translation;
actual non-3xx SDK error preserved by identity; ordinary cleanup failure preserves
primary error; cancellation propagates while finally attempts cleanup. Each
asserts exact constructor/cleanup counts and final closed status where successful.
L07: caller http_client rejected before allocation. L08: Codex SDK close failure
leaves transport open and fallback closes it, primary close failure preserved.
L09: denied credentials at the actual Direct adapter produce zero owned transport
allocations, not merely zero fake SDK calls.

`test_inference_transport_provenance`, F00: new source participates in runner
fingerprint; F01: participates in MCP
finalization fingerprint; F02: stale actual provenance guard rejects without
overwriting. The actual raw/Anthropic diagnostics captured in R/S must be supplied
to the real public projection, not merely copied into an ignored private file.
There are four scenario functions and exactly 77 cells (45+19+10+3).

Concrete entry points: all Direct paths call adapter.call_provider_text; native
Codex/compatible call mcp_runtime._openai_compat_chat_turn with their respective
base/full-chat URLs; native Ollama calls _ollama_chat_turn with its normalized
chat URL. L08 uses native Codex so the SDK close exception identity remains
observable rather than being wrapped by the Direct public adapter.
For R/S public filtering, feed each captured response.error/exception string
through model_runtime._model_infrastructure_sample, summarize_results and actual
campaign.build_public_report with a valid synthetic task/oracle/profile pair.
Assert that the private sample retained that actual diagnostic before asserting
that serialized public output omits all sentinels. Use the existing valid
tests.support.v2_mcp fixture contracts; do not claim this is an executed campaign.

If SDK shapes or existing APIs make any cell impossible, stop before substituting
a fake equivalent: amend this plan with the concrete contract and obtain review.
No live model is required for any cell.

## Negative controls and validation

Three fresh-process baseline/control pairs execute the actual new test module:
FC1: intercept the fixture's actual HTTPX constructor for R00 only and replace
follow_redirects=False with True exactly once. The mock redirected destination
returns a valid successful Chat Completions response, not another redirect. Assert
request count before outcome so only the extra-destination assertion fails; later
R cells still pass. FC2: in R30 only, replace provider_transport.ProviderProtocolError
with a factory returning a real OpenAI APIStatusError for the captured synthetic
307/301 response. Exactly one factory invocation replaces the translated exception
with the SDK exception; assert V2 classification before private-error content so
the expected protocol attribution fails. Other R cells use the unmodified class.
FC3: L01 fake owned transport's is_closed property reports True before any close
only under control; its actual closed flag stays False. Exactly one property read
causes the helper to skip cleanup; the explicit aclose count==1 assertion fails.
Later L cells pass. These are fixture-only substitutions, not new production hooks.
The three baseline runs cover 100 cells (45+45+10); controls must yield exactly
three intended failures and 97 other passing cells. Stable IDs and fixture
hooks must target one case only and restore in finally. Preserve all baseline,
failure, unrelated/later-case and external-call counts, exact substitution count,
source path-set/hash before and after, commands, exit codes and driver/log hashes.
Any unexpected failure invalidates the control until investigated and rerun with
new filenames. Do not modify production files to inject faults.

Freeze all source/test/script/lock files and enforce the exact ownership allowlist.
Run focused redirect/provider/auth/Codex/Anthropic/Gemini/Ollama/V2 runtime suites,
then full pytest, Ruff on src/scripts/tests, offline uv lock --check and
git diff --check on the same frozen inventory. No test deletion or weakened
assertion is permitted. Independently reconcile gate receipts and control logs.
Record exact test/subtest totals, times and hashes, not estimates. Close only L
after all pass and independent review accepts; Stage 1 remains open.

Stop for an unplanned production change, failed network trap, real secret input,
unexplained source drift, missing fingerprint coverage, weakened test, unexpected
control failure or unresolved review finding. Preserve all evidence and unrelated
work. Deferred hosted calls, Git publication and local hardware qualification
remain deferred, not silently accepted.

## L-A1 — Existing redirect-message compatibility assertion

Independently approved amendment after the first frozen focused run. That run's sole failing
subtest is existing test_adapter_known_failure_boundary K21, which constructs an
actual OpenAI 302 APIStatusError and previously required its raw message unchanged.
The new helper intentionally translates that error to the approved constant.
Keep all 41 case IDs, SDK exception fixtures, exact constructor/request counters,
empty result, subtype and retryability assertions unchanged. In only that test
function, require the literal constant for index 21 and str(fault) for every other
case. Do not broaden the conditional to every exception or relax string equality.

Add tests/test_provider_adapter.py to the snapshot validation allowlist solely
for this migration after independent amendment approval. The first full/focused
logs remain diagnostic evidence on their original frozen inventory; do not edit
source while those runs are active. Capture the approved amendment separately,
apply the single-function test migration, archive the previous freeze, then rerun
all five final gates and all six controls with new names on the new inventory.
No production change, test deletion or additional test count is authorized.

## Frozen validation checkpoint

The approved implementation and L-A1 migration passed all five gates on 172
source/test/script/lock files, inventory
`d0abc7b8e8d2b610e731671669f720b837cacf982c5cea3b309bc9cc202d732d`.
Full pytest: 1,185 tests and 1,159 subtests in 224.33 seconds. Focused regression:
334 tests and 926 subtests in 15.37 seconds. Ruff, offline dependency-lock check
and whitespace check passed. Full log SHA-256:
`465b833e0621e6dacb6be1d0ac6fee04a84f39fb819cfe56a44b5726126e4a86`.

Four new functions exercised 77 cells. The final six control processes passed
100 baseline cells, detected exactly three intended failures and passed the
other 97 cells, with zero recorded external calls and identical full discovered
source inventories before/after. Authoritative control run: `43313b4d0e13`;
manifest SHA-256:
`19359aa908d8c88066265be1d77e455321de5ed2a54840effaeb8821f2d3a9b5`.
Root reconciled all five gates and six control receipts/log hashes in the private
completion record. Independent final evidence acceptance is pending at this
checkpoint; prior source/test and L-A1/control reviews already passed.

The original failed K21 regression runs, superseded successful control run
`34142dcb6a0e`, and initial control-recorder diagnostic run `225585b0a1dc` remain
preserved and excluded from final acceptance. Initial tool-only development
diagnostics were not retroactively reconstructed as original log files; this
limitation is explicitly recorded in private development notes. No production
files were changed for fault injection. Stage 1 and campaign gates remain open.

Final independent review subsequently accepted bounded S1-05L/L-A1 closure after
reconciling all five post-amendment gate receipts/logs, unchanged 172-file source
inventory and authoritative controls. The later private independent-review record
preserves that acceptance separately from the earlier pending-review checkpoint.
This accepts native inference redirect containment only, not credential-exchange
qualification, broader cleanup completion or live campaign readiness.
