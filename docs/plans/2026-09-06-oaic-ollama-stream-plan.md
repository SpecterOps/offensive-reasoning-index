# S1-05M — Ollama native stream integrity

Status: independently accepted as a bounded offline slice. September 6, 2026.

Implementation and source/test review now pass, including M-A1/A2/A3 below.
Frozen offline validation passes 1,190 tests and 1,253 subtests in 224.80s;
focused provider/V2 validation passes 339 tests and 1,020 subtests in 16.02s.
Ruff, offline lock and whitespace checks pass. The complete 174-file inventory
is unchanged at `0a99af3f17e502d09f2cd3cfd425fde325e1af0e5b9cb8bd102af7aff32bee91`.
Four fresh baseline/control pairs prove 147 baseline passes, four exact intended
failures and 143 unaffected passes, with zero external operations. Root evidence
reconciliation and final independent evidence review pass. This closes only M
and its three amendments; live qualification, campaign readiness, the documented
retry-accounting discrepancy and broader Stage 1 work remain open.

## Entry and evidence

S1-05L/L-A1 is independently accepted at 172 source/test/script/lock files,
inventory `d0abc7b8e8d2b610e731671669f720b837cacf982c5cea3b309bc9cc202d732d`.
Full validation passed 1,185 tests and 1,159 subtests. The previous goal turn made
verified implementation progress. Stage 1 and the overall OAIC goal remain open;
the fewer-than-700 test target is not met.

Both adapter.py's Ollama branch and mcp_runtime._ollama_chat_turn currently return
accumulated text/tools at EOF without requiring a completion marker. Native loop
dispatch then trusts that returned turn. Invalid JSON/envelope types can escape
as internal errors, and done_reason is ignored. Fix the shared framing boundary
and truncation admission in both consumers, preserving valid capabilities.

Primary references consulted September 6 (not live provider qualification):
[Chat response](https://docs.ollama.com/api/chat) defines done as boolean and
done_reason as string; [streaming](https://docs.ollama.com/api/streaming) describes
NDJSON with a terminal true marker; [errors](https://docs.ollama.com/api/errors)
documents in-stream error objects without a changed HTTP status. The upstream
[API types](https://github.com/ollama/ollama/blob/main/api/types.go) also distinguish
message content, thinking, tool-call containers and completion fields. These are
current source references, not a pinned serving runtime or a canary receipt.
Upstream [completion reasons](https://github.com/ollama/ollama/blob/main/llm/server.go)
explicitly distinguishes DoneReasonLength from DoneReasonStop and serializes the
former as 'length'; ORI's model-truncation treatment follows that distinction.

Preserve the consolidated dirty branch. No real provider/model, graph, socket,
credential-file, hardware/service, remote Git, signing, commit, PR or merge work.
Public documentation reads are allowed; tests use synthetic credentials and
HTTPX MockTransport only. No agent-framework or machine-specific dependency.

## Fixed design

Add a network-free src/ori/eval/ollama_stream.py with OllamaStreamState:
feed_line(line: str) -> dict[str, Any] | None and finish() -> str. State holds only
terminal_seen and done_reason; the current callers retain their accumulators.
No streaming tool executes before finish succeeds. The reducer is instance-local
per request and reusable by Direct and native MCP without a normalized tool facade.

feed_line ignores whitespace-only lines, including after the terminal. Every
other line must decode as one JSON object. Reject invalid JSON and non-finite
JSON constants with a constant ProviderProtocolError; do not catch unrelated
exceptions. Preserve ordinary JSON duplicate-key decoding in this slice rather
than silently adding a new duplicate-key policy. Unknown fields remain ignored.
If terminal_seen is already true, any further nonblank object/data is a protocol
failure (duplicate terminal and post-terminal payload are equally inadmissible).

If present, done must have exact bool type, not truthiness. Missing done means an
intermediate chunk, retaining compatibility with existing fixtures. A true marker
sets terminal_seen once. Continue through response-body EOF; this is not waiting
for the TCP connection to close. finish requires that true marker. Existing
read/no-progress/whole-task deadlines and cancellation remain active after it;
a broken or stalled HTTP body after done is not a successful completed response.

Validate only consumed wire structure: message may be omitted/null (metrics-only
completion) or an object; present content/thinking may be null or strings; model
may be missing/null/empty or a string. Present tool_calls may be null or a list;
every listed call must be an object with an object function. Do not change tool
name/ID normalization or argument legality in this framing slice. Those existing
model/tool-call semantics remain a separate integrity review; this plan does not
claim to fix every completed-turn tool-argument defect.

done_reason may be missing/null or a string; missing/null maps to empty string.
Only the terminal chunk determines the retained reason. Other strings remain
forward-compatible; exactly 'length' means model truncation, not a provider error.
Do not infer truncation from content length, empty output or prose. A terminal
reason does not excuse malformed framing elsewhere in the response.

For the six consumed terminal metrics (prompt_eval_count, eval_count,
total_duration, load_duration, prompt_eval_duration, eval_duration), preserve the
existing int(value or 0) conversion semantics. Catch only conversion TypeError,
ValueError or OverflowError and raise a constant protocol error. Return those
normalized numbers in the decoded terminal object. Do not introduce new token
knownness, rounding, nonnegative or accounting policies in this slice. Nonterminal
metric fields remain ignored as before.

An explicit error member is a provider failure, even if done is also true; never
publish its raw message or treat it as a completed answer. Add
ProviderGenerationError(ProviderContractError), code PROVIDER_GENERATION_ERROR,
retryable=False, with the constant 'Ollama reported a generation error'. Because
the stream error carries no trustworthy structured cause/status, do not guess
authentication, capacity or transient retryability from prose. Add its explicit
native V2 classification before the generic contract case. Direct already uses
the typed error's code/retryability. Both produce provider infrastructure failure,
not model incorrectness or HARNESS_ERROR. No public outcome schema changes.

## Integration and legitimate truncation

Direct replaces only line decoding with feed_line and calls finish after consuming
the response, before returning accumulated output. It retains current destination,
payload, text/thinking concatenation, options, timeouts and metrics for valid
responses. For reason length, return empty final text with retained thinking/token
metrics, model_output_error=True and model_output_subtype=TRUNCATED, and no provider
error. The existing V2 Direct answer boundary then yields OUTPUT_INVALID without
executing a query. Record finish_reason and provider_turn_status privately.

Native _ollama_chat_turn does the same framing validation; add finish_reason and
provider_metrics to the returned internal dictionary. On length, suppress returned
executable tool_calls and final content, retain thinking and completed terminal
usage, and record TRUNCATED metadata. Do not dispatch partial tool calls or accept
a syntactically valid-looking truncated final answer.

_run_ollama_mcp_loop accumulates per-turn provider_metrics and finish reasons using
optional .get defaults, preserving the existing normalized test doubles and valid
request/response behavior. A truncated terminal response ends that loop with empty
final content, no provider error and explicit model-output metadata. Do not label
it as 'loop exhausted' or claim loop_exhaustion_with_evidence for this condition.
Prior completed tool calls, progress messages, thinking and token counts remain.

V2's existing single schema-only retry policy is preserved: with no qualified
evidence, truncation cannot cause a retry or tool dispatch; with prior qualifying
evidence it may receive exactly one tools-disabled schema retry within the original
task deadline, using the preserved transcript. A valid retry can complete the task;
an invalid retry remains an output/proof failure. A malformed/missing/error stream
is a terminal provider failure and must never trigger the schema retry. No generic
extra reruns or hidden prompt changes are introduced.

Add ollama_stream.py to both runner implementation and MCP finalization source
fingerprints. Existing modified adapter, runtime, provider contract and V2 runtime
sources are already bound. No serialized version bump; old fingerprints become
stale. Recompile, renew certification/readiness and use a fresh output directory
before later campaigns. No live certification is performed in this slice.

## Ownership and execution order

Root owns plan, new reducer, adapter.py, mcp_runtime.py, provider_contract.py,
v2/model_runtime.py, v2/campaign_runner.py, v2/mcp.py and public docs. Test specialist
owns only new tests/test_ollama_stream_acceptance.py and private control drivers.
No existing test migration is expected: every real-wire fixture already includes
boolean done:true; normalized internal mocks retain optional metadata compatibility.
Any additional change requires an independently reviewed explicit amendment.
Owners share the tree and must not revert each other's or unrelated edits.

Independent Devil's Advocate review precedes edits. After approval snapshot all
172 files and approved plan. Implement in separate scopes; review complete changed
source/tests; freeze all source/test/script/lock paths before final gates/controls.
Use unique append-only private logs; preserve failed and superseded runs.

## Exact finite acceptance map

Four grouped scenario functions. Each cell has a stable ID, fresh state and network
and subprocess traps. Real HTTPX clients use MockTransport; no real inference.
Assert request endpoint, model/messages/options, no redirects and transport closure.

1. test_ollama_stream_wire_contract: W00–W31, each with Direct/native suffix D/N,
64 cells. Cases in order: metrics-only terminal; blank lines plus split content;
thinking/tool accumulation; explicit intermediate done:false; omitted intermediate
done; terminal content; explicit stop; length with tempting tool; empty EOF;
content without terminal; malformed JSON; array frame; null frame; done:'true';
done:1; done:null; duplicate terminal; post-terminal content; error object;
error plus done; message array; tool_calls object; non-object tool call;
non-string done_reason; non-object function; non-string content; non-string thinking;
non-string model; unconvertible terminal metric; unknown string reason preserved;
null terminal reason; non-finite JSON constant. Valid cases preserve literal
text/thinking/usage/tools where applicable; Direct still does not execute tools.
Invalid cases assert exact typed subtype, nonretryability, zero accepted final
output and constant secret-free diagnostics. Native exceptions cross the actual
V2 provider classifier. Public projection receives the captured real diagnostic
and excludes body/answer/endpoint sentinels, not an unrelated ignored file.

2. test_ollama_stream_consumer_admission: C00–C09, 10 cells. C00–C03 run the real
native loop with tempting tool calls followed by missing completion, malformed
JSON, error and length respectively; zero tool executions. C04–C07 repeat through
actual V2 MCP after one complete qualifying evidence turn: preserve that first
tool's transcript, receipt and tokens, never dispatch the second turn's tool.
C04–C06 are provider failures with no schema retry. C07 permits exactly one
successful schema-only retry, carrying the previous transcript and no tools.
C08 is the same prior-proof length with invalid schema retry: no additional
retry or tool execution. C09 actual V2 Direct receives length with valid-looking
query text: OUTPUT_INVALID, zero coordinator calls, retained terminal usage.
Use tests.support.v2_mcp's valid task/oracle/profile contracts and real native
transport; do not patch _ollama_chat_turn or substitute a fake consumer.
For C04–C08 and T04, compare independently frozen full prior tool arguments,
result text, transcript, receipt and token values; counts/nonempty checks alone
are insufficient preservation evidence.

3. test_ollama_stream_runtime_boundaries: T00–T07, 8 cells. Native watchdog before
done; watchdog after done; HTTP read timeout; cancellation before completion;
cancellation after one completed tool/evidence turn; injected unrelated reducer
RuntimeError remains HARNESS_ERROR; two sequential requests prove fresh reducer
state; blank lines after terminal still allow success. Use bounded synthetic
AsyncByteStream/controlled clock or deadline hooks, not a real sleeping provider.
Preserve exact timeout/cancellation classifications and prior receipts. T05 injects
only the parser call in the real V2 path, never the classifier itself.

4. test_ollama_stream_provenance: F00–F02, 3 cells. Reducer included in runner
fingerprint and MCP finalization fingerprint; actual stale provenance guard rejects
without overwriting. Total four functions, 85 cells (64+10+8+3).

## Three negative controls

Each baseline/control pair is a fresh interpreter running the actual collected
test module; restore patched symbols in finally and verify every later ID. FC1:
W09-D only, replace OllamaStreamState.finish with one return of empty reason instead
of missing-completion rejection; exactly one invocation, expected provider-error
assertion fails, remaining 63 cells pass. FC2: W09-N same single finish substitution
for native path; classifier assertion fails, remaining 63 pass. FC3: C00 only,
finish returns once without terminal validation, admitting a tempting tool call;
the fake tool returns harmless synthetic data and next request has a valid completed
answer. Assert tool count before failure classification so the precise zero-dispatch
assertion fails. Later nine consumer cells pass. Do not patch production files,
test assertions, classifier or public report builder.
FC3 restores finish before the second request; the override affects exactly the
first incomplete response, not its subsequent valid completion.

Baselines: 138 cells (64+64+10); controls: three intended failures, 135 unaffected
passes. Record exact patched-call count, original/destination request and tool
counts, failed assertion, visited/later IDs, outer/per-case external calls, full
discovered path set and hashes before/after, driver/log/receipt hashes and exits.
An unexpected secondary error invalidates that control until fixed and rerun with
new filenames; retain the diagnostic evidence.

## Gates and stop conditions

Focused tests include new scenarios and all current provider, Ollama, redirect,
V2 runtime/model-runtime and native MCP tests. Run full pytest, Ruff src/scripts/tests,
offline uv lock --check and git diff --check against the same frozen inventory.
Independently reconcile gates/controls and exact totals/timings/hashes before
bounded closure. Public documentation explains the EOF/truncation migration and
fresh-evidence requirement; progress ledger keeps broader Stage 1 incomplete.

Stop for real external activity, unexpected source drift, a needed unapproved file
change, fabricated provider evidence, hidden scorer/prompt changes, downgraded
assertions, any failed gate/control or unresolved review hole. Do not silently
weaken a fixture or reinterpret truncation to get a passing test. SDK accounting,
current-turn token unknownness, complete tool-argument/identity validation, Codex
terminal ordering and credential-exchange qualification remain explicit follow-ups.

## M-A1 — Preserve the original fact source for schema repair

Independently approved amendment before production integration; approved wire-test
fixtures are being drafted while the affected consumer expectations remain paused.
The feasibility audit found that
_retry_adds_answer_facts uses the original response.raw_text. Clearing truncated
output would make C07's repair fail even when it introduced no new facts. Do not
bypass or weaken that guard, and do not change C07 to an easier failure expectation.

Only for a length-truncated native Ollama turn, retain its original concatenated
content in private provider_metrics.truncated_output_text. Keep returned executable
content empty and tool calls suppressed. The native loop places the original
truncated text in the private assistant transcript (without executable tool calls),
and carries that terminal turn's truncated_output_text plus TRUNCATED markers to
the final private ModelResponse metrics. Its raw_text remains empty and error None.
The original text is neither accepted as an answer nor used to unlock evidence.

Immediately before MCP final parsing, derive a local schema_retry_source from
response.raw_text. Use the private retained text instead only when the configured
model prefix is ollama/, top-level model_output_error is exactly True,
model_output_subtype is exactly TRUNCATED and truncated_output_text is a string.
Otherwise preserve the original behavior. Pass that same source to the existing
schema-only prompt and _retry_adds_answer_facts. Never change the guard itself,
allow new oracle/proof facts, or source text from a tool result. The exact source
must be the model's own emitted truncated output.

Count this retained source once in the existing final-output byte budget instead
of empty response.raw_text; it already occurs in the preserved private assistant
transcript for transcript-byte accounting. The retry output is counted as before.
Do not infer unlimited output because the grading-visible field is empty. No
new public field or metric is introduced. Tests must pass the resulting private
record/diagnostics through the actual public projection and reject raw-text
sentinel leakage. Preserve all other providers and nontruncated paths unchanged.

C07's initial length-marked output contains the same literal answer facts as its
completed schema-only repair, so the unchanged fact guard can legitimately pass.
C08's retry includes a new answer fact and must be rejected explicitly as
SCHEMA_RETRY_ADDED_NEW_FACTS with no further call. Add C10 for retained truncated
source exceeding max_output_bytes: the task must not become rankable through a
short repaired answer. Consumer count becomes 11, total 86 cells, control baselines
139 and unaffected control passes 136. Existing C04–C08/T04 exact prior-evidence
preservation obligations remain unchanged. This amendment adds no new production
file to the seven-file ownership set, only the specified V2 integration behavior.
Initial answer parsing continues to use empty raw_text; retained text is only a
repair source and byte-budget input. This preserves the existing lexical guard,
not a stronger semantic no-fact-expansion guarantee. All framing failures use the
constant 'Ollama stream is incomplete or malformed'.

## M-A2 — Native schema-repair tool-argument encoding

Independently approved amendment during implementation review. _schema_only_retry currently
encodes assistant tool arguments as JSON strings for every provider. That is the
Chat Completions representation, but native Ollama chat requires an object. The
upstream ToolCallFunctionArguments decoder and chat schema establish that boundary;
a permissive MockTransport success is not proof of a valid native request.

In _schema_only_retry.provider_message, branch only for model.startswith('ollama/'):
preserve dictionary arguments; parse string arguments as JSON and require a
dictionary result. Reject malformed JSON or non-object arguments before invoking
the transport, raising V2ModelRuntimeError with constant message
'Ollama repair transcript requires object tool arguments'. Do not invent an empty
dictionary, discard a prior call or reconstruct arguments from prose. Preserve
all non-Ollama argument serialization, tool names, call IDs, message order, prior
tool results, schema prompt and fact guard. This is wire projection of an existing
private transcript, not tool execution or a new tool facade. Broader argument
legality/identity and accounting audits remain separate.

C07/C08/C10 must now assert that the actual third native request contains object
tool arguments equal to the independently frozen prior arguments, with no tools
offered and the complete preserved call/result transcript. Add fifth function
test_ollama_schema_retry_wire_projection with P00–P04: native dictionary retained;
native JSON-object string decoded; native malformed JSON string rejected with zero
transport calls; native JSON array string rejected with zero transport calls;
compatible provider dictionary still emitted as JSON string. P00/P01/P04 cross
the actual schema-retry function, adapter and MockTransport wire; keep literal
expected arguments independent of the projection implementation. P02/P03 assert
the exact internal-error type/message, never a provider or model verdict.

Add FC4: fresh-process baseline/control pair for all five P cells. P00 only,
replace the fixture's thin _schema_transport wrapper with a one-shot wrapper that
JSON-encodes the already projected assistant arguments immediately before invoking
the real adapter. Exactly one substitution/call; the mock returns an ordinary
successful response, then the explicit actual-wire object-argument assertion must
fail. Do not patch assertions, the production projection, or provider classifier.
The remaining four P cells pass. This proves native payload regression detection
without changing production files. Apply all existing source/hash/network and
append-only evidence requirements to FC4.

Final totals: five functions, 91 cells (64+11+8+3+5). Four baseline/control pairs
cover 144 baseline cells; controls detect four intended failures and preserve
140 unaffected passing cells. No production file or existing-test migration is
added to the approved ownership set. This amendment must be independently approved
before changing the projection or claiming native-valid C07 repair acceptance.

M-A2 review correction: native argument extraction must explicitly check field
presence in both nested-function and flat-function transcript shapes. A missing
field raises the same constant V2ModelRuntimeError before transport; it must not
inherit the existing flat-form default {}. Preserve those defaults only for
non-Ollama providers. An explicitly supplied empty dictionary remains valid.
Add P05 missing nested arguments, P06 missing flat arguments (both zero transport),
and P07 explicit empty native dictionary (actual wire remains {}). This supersedes
the preceding five-cell P count: final P count is eight, overall five functions
with 94 cells, four control pairs with 147 baseline cells, four intended failures
and 143 unaffected passes. FC4 runs all eight P cases, with seven later passes.

## M-A3 — Acceptance-fixture isolation

Independently approved during implementation review. The new test module imported another collected test module's context
fixture, contradicting S1-02's no-test-to-test-import boundary. Replace that import
with a local synthetic transport context in the new M test file only. Preserve
fresh synthetic environment/home, no real credential-file reads, socket and
subprocess traps, actual HTTPX/SDK paths with MockTransport, request/allocation/
closure counters, per-case state and cleanup. Retain needed deterministic platform
stubs and no process-global leakage. Do not edit L's accepted tests, introduce a
new support file, or broaden production ownership. The existing five functions,
94 cases and four fault controls remain unchanged. Final full and focused gates
must include both L and M. This avoids coupling collected modules without turning
M into an unrelated shared-fixture refactor.

Review also requires C07/C08/C10 to feed the actual retained private provider
record through typed attempt/state and the real public operational aggregation;
assert private text/transcript sentinels are present before proving their absence
from the complete serialized public report. C10 must explicitly report no
reasoning verdict and the exact OUTPUT_INVALID/model_failure outcome after the
output bound revokes evidence, not simply a non-true
accuracy result. These strengthen existing acceptance obligations, not production
semantics or test counts.
