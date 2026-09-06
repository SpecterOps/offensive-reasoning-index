# S1-05N — Codex successful-stream reconciliation

Status: accepted on the combined S1-02B offline boundary after independent review.

S1-02B restored the seven pre-existing static collected-test imports and reran
the full suite and all affected I/K/L/N controls. Combined inventory: 179 files at
`4478262cc59b1514e706158e99ebbe4f20731234bd75bb240f05821c4653bdc1`.
All 1,195 tests and 1,339 subtests pass; 32 control processes reconcile 231 baseline
passes, 16 intended failures and 215 unaffected passes. Independent final review
accepts N/N-A1 on this new source. No live readiness or publication is implied.
The earlier evidence and failed gate described below remain historical and unchanged.

Implementation and bounded source/test/control-design review now pass, including
N-A1. Initial frozen acceptance was withheld: the full-indentation import gate detected
seven pre-existing collected-test helper imports that earlier anchored searches
missed. This is a real architecture-gate failure, not a new N source regression.
Retain the failed receipt and passing runtime evidence separately. A fresh scoped
helper-isolation plan must restore the invariant and run combined validation;
do not silently drop this gate or expand N's edit scope without review.

Frozen runtime evidence: 175 files at
`2d56c64e37e063d3ebb3e574ec3515566493ec67b910751341e7c8fb0c5f5b55`;
full suite 1,194 tests and 1,329 subtests in 227.59s; focused suite 343 tests and
1,096 subtests in 16.86s. Ruff, offline lock and whitespace pass. All three
controls reproduce their exact intended failures with 73 baseline passes and
70 unaffected control passes; no external operations were recorded. Independent
review verified these receipts and the same-source failed import gate. These
results are runtime evidence only, not a waiver or final N acceptance.
September 6, 2026. Root owns integration and production; the test specialist owns
the named acceptance and fixture migrations; a separate reviewer owns approval.

## Entry, scope and dependencies

M/M-A1/A2/A3 is independently accepted at 174 files, inventory
`0a99af3f17e502d09f2cd3cfd425fde325e1af0e5b9cb8bd102af7aff32bee91`.
Full regression: 1,190 tests and 1,253 subtests in 224.80s. Reverified at entry.
The prior goal turn made verified progress. Stage 1 and the overall goal remain
open. Preserve the consolidated dirty branch and all earlier work. No provider,
model, graph, credential-file, socket, host/service, signing, commit, remote Git,
PR or merge operations; synthetic in-memory HTTPX/SDK tests only. Public official
documentation reads are allowed. No agent-framework or machine-specific dependency.

Source inspection shows that the shared Codex translator currently accepts
duplicate/post-terminal output, invents missing tool-call IDs/arguments, and uses
streamed output instead of reconciling it with completed output. Both consumers
buffer events before translating. These are source defects, not proof of duplicate
live tool execution. This slice repairs the entire successful-output admission
boundary described below, not just a duplicate-event example.

The installed, locked SDK types establish: Response.output is a list;
function call_id/name/arguments are required strings, while function item.id and
item.status are optional; text delta events carry output/content indices and
item_id. Response.output_text is computed from output, not independent wire data.
The fetched official [function-calling streaming guide](https://developers.openai.com/api/docs/guides/function-calling#streaming)
also binds events to individual output indices. These references do not certify
any live Codex backend. Preserve ORI's existing refusal fallback explicitly.

## Fixed production ownership and interfaces

Only three production files may change:

- src/ori/eval/codex_oauth.py: retain the public translator signature
  codex_responses_events_to_chat_completion(events, model) -> dict; replace its
  successful-output construction with validation and reconciliation. Remove or
  replace its private fallback helpers when no callers remain.
- src/ori/eval/mcp_runtime.py: in the Codex buffering/progress path only, append
  a text delta to progress when it is a string. Retain the original event in the
  buffer so malformed deltas reach the translator and become protocol errors,
  not a join TypeError. Preserve callbacks, timeouts and cleanup otherwise.
- src/ori/eval/v2/mcp.py: add codex_oauth.py to finalization source fingerprints.
  Runner fingerprint already contains that source; verify rather than duplicate.

Use existing ProviderProtocolError with constant message
'Codex successful stream is inconsistent or malformed'. It is nonretryable
provider infrastructure failure in both consumers, not model incorrectness or
HARNESS_ERROR. Do not catch unrelated internal exceptions. Preserve request
translation, credentials, endpoints, usage conversion, IDs/timestamps generated
for the outer Chat Completion, transport ownership and timeouts.

## Terminal and failure precedence

Consume the complete event iterable. Retain existing immediate
CodexResponseStreamError handling for error, response.failed and
response.incomplete, including after a prior completion. This explicitly preserves
their current attribution/retry compatibility for separate review.

For all other events, record a pending protocol violation for any event after the first response.completed,
including another completion, text, tool, or unknown event. Continue consuming
instead of immediately raising: a later explicit error/failed/incomplete event
retains the legacy error precedence. Keep the first completion, never overwrite
it. After exhaustion and the missing/null completion gate, reject the pending
violation before successful-output validation. Before completion,
unknown event types remain ignored. Do not introduce sequence-number monotonicity,
response.created requirements or incremental argument-delta reconstruction.
Gather consumed text and function-item-done events without grading/dispatching
or eagerly validating their fields; a later explicit failure retains precedence.
After the iterable ends, absent or null completed response retains the existing
missing-completion CodexResponseStreamError and its existing message. This gate
precedes successful-output validation. Completed response status may be absent/
null; if present it must be 'completed'. Its output must be an actual list.

## Canonical completed output

The completed output list is the authoritative complete inventory and ordering.
Never invent call IDs or arguments and never drop a final call merely because
another call was streamed. Accept terminal-only output and a partial inventory of
streamed done events when every observed done event agrees with the final list.

Require every output item to have a nonempty string type. Unknown types remain
ignored for projection while retaining their list positions. For consumed message
and function items, present status must be null or 'completed'. Message IDs are
nonempty strings. Function item IDs remain optional/null; a present ID must be a
nonempty string. All present IDs across consumed items must be unique. A message's
content must be a list; each content block must have a nonempty string type.
Known output_text.text and refusal.refusal must be strings, including empty strings.
Unknown content types remain ignored. Message role/phase and other fields retain
their current projection behavior; this is not a complete Responses schema validator.

Function call_id and name must be nonblank strings, preserved verbatim without
trimming; arguments must be a present string, preserved byte-for-byte. Empty,
malformed-JSON and non-object-JSON strings remain strings for the existing
downstream argument validator; the translator must never turn them into '{}'.
The existing native EMPTY-argument policy may still invoke a zero-argument tool
with {}; preserving that policy is intentional, not a claim of new argument
legality enforcement. Duplicate call_id
values in final output are protocol errors, even if otherwise identical.
Output calls use final-list order. They retain the existing Chat Completions shape
with id=call_id, type=function and function{name, arguments}.

Final text uses the concatenation of output_text blocks in final item/content
order. If that concatenation is nonempty, preserve the existing SDK-backed rule
that refusal blocks are not appended. Otherwise concatenate refusal blocks. Do
not read an independently supplied output_text convenience attribute or introduce
a new refusal classification. Empty/whitespace final text without any calls retains
the existing empty-completion CodexResponseStreamError/message.

## Reconcile observed events

For each consumed text delta: require exact nonnegative int output_index and
content_index (not bool), nonempty item_id string, and delta string. The indexed
final item/content must be output_text with the exact item_id. Group deltas by
that indexed content slot; concatenate in event arrival order within the slot.
Every observed slot must equal its complete final text exactly. Missing streamed
slots are allowed because the completed response supplies them. Interleaving
different slots is allowed; final projection order remains final-list order.
Conflicting, partial, orphan or rebound text is rejected, not silently preferred.

For every function output_item.done: require exact nonnegative int output_index;
validate the same call fields/status/optional ID as final calls. The final item at
that index must be a function call with identical call_id/name/argument string.
When both item IDs are present they must match; absence of optional IDs is not a
failure. Each streamed call_id and streamed function output_index may appear only
once, including identical repeats. Event order may differ from final order.
This supplies one unique final call inventory without executing partial events.
Other output_item.done kinds retain their previously ignored behavior.

## Migration and compatibility

No old artifacts or checkpoints silently resume: changed runner/finalization
fingerprints require new compilation, certification, readiness and output root.
Readiness still makes zero provider calls. No public schema change.

Allowed fixture-only edits, preserving every existing assertion:

1. tests/test_codex_oauth.py: reasoning-effort provider success, combined text/tool
   translator success, and terminal fallback success now contain matching final
   message/tool output and identified/indexed text events. The impossible
   output_text-nonempty/output-empty mock becomes an actual message content shape.
   Missing-terminal and empty-completion negative fixtures may receive structurally
   valid fields but retain their exact existing exception assertions. Do not edit
   explicit error/failed/incomplete expectations.
2. tests/test_codex_destination_acceptance.py::_sdk: matching completed message for
   existing destination/binding/precedence scenarios; no endpoint/credential changes.
3. tests/test_provider_redirect_acceptance.py::_success_response: matching completed
   message for existing Codex/native-Codex success; no redirect assertion changes.

No support-module, other test, provider contract or task/scorer changes are allowed.
Any additional migration requires a reviewed amendment before edits.

## Exact acceptance matrix

Add tests/test_codex_stream_acceptance.py with four grouped functions, 74 cells.
Use fresh state, stable IDs, synthetic environment/home, socket/DNS/process traps,
real SDK SSE decoding and HTTPX MockTransport. No collected-test imports. Positive
wire fixtures validate with installed SDK models; T37's future event and T45's
future content block alone are explicit permissive-extension exceptions, while
their known surrounding items still validate. Malformed cases deliberately
exercise its permissive wire construction. Freeze literal expected text/calls,
arguments, prior receipts/transcripts, outcomes and token counts independently.

test_codex_stream_reconciliation: T00–T45 (46 cells):
00 text; 01 function; 02 mixed; 03 terminal-only tools; 04 partial streamed call
inventory includes all final calls; 05 terminal-only text; 06 interleaved text
slots project in final order; 07 missing optional function item ID; 08 malformed
JSON arguments preserved; 09 empty argument string preserved; 10 duplicate
completion; 11 post-terminal text; 12 post-terminal tool; 13 post-terminal unknown;
14 duplicate identical streamed call; 15 duplicate final call ID; 16 missing call
ID; 17 blank call ID; 18 missing arguments; 19 nonstring arguments; 20 missing
name; 21 streamed call absent from final list; 22 name conflict; 23 argument
conflict; 24 function index mismatch; 25 text conflict; 26 unknown text item ID;
27 missing final output; 28 nonlist final output; 29 nonstring delta; 30 bool
index; 31 contradictory response status; 32 missing terminal legacy failure;
33 explicit failed legacy failure; 34 explicit incomplete legacy failure;
35 explicit error legacy failure; 36 empty final legacy failure; 37 unknown
pre-terminal event allowed; 38 refusal-only fallback; 39 mixed text/refusal
preserves text priority; 40 conflicting optional item IDs; 41 negative index;
42 duplicate final message ID; 43 incomplete consumed item status; 44 completion
then unknown/text then explicit failed event retains the legacy failure rather
than stopping early with a protocol error; 45 leading SDK-valid reasoning output
item and ignored future content block before output_text retain their original
output/content positions, with an exactly indexed delta and final text. Construct
the unknown content block through the permissive-wire/object path; do not claim
that unknown content validates against the current SDK union.
Use the actual translator, with exact successful output and constant error/type
assertions. Keep generated outer IDs/time nondeterminism out of semantic equality.

test_codex_stream_wire_admission: W00–W07 each Direct/native suffix D/N (16 cells):
00 valid text; 01 valid tool; 02 duplicate terminal; 03 post-terminal tool;
04 duplicate streamed call; 05 conflicting text; 06 missing final output;
07 nonstring delta. Cross actual adapter/native turn and SDK wire decoding.
Native supplies a real progress observer, proving malformed deltas are not join
defects. Invalid native turns raise exact protocol type/subtype; Direct returns
its actual nonretryable provider failure. Assert request destinations/payloads,
transport closure, no real credential reads, and no partial accepted output.

test_codex_stream_consumer_admission: C00–C08 (9 cells):
00 duplicate-terminal first turn, zero tools; 01 duplicate-streamed-call first
turn, zero tools; 02 text-conflict first turn, zero tools; 03 partial streamed
inventory correctly executes the two final calls once each in final order then
receives a valid final answer; 04 one completed claim-relevant Cypher receipt then
invalid duplicate-call turn retains exact prior receipt/transcript/tokens and
executes no additional tools; 05 unrelated injected translator RuntimeError maps
to HARNESS_ERROR, never infrastructure retry; 06 actual V2 Direct invalid stream
does not invoke query coordinator; 07 cancellation retains current cleanup and
prior completed evidence; 08 HTTP read failure is still infrastructure with no
current-turn tool dispatch. Invalid stream cases get no schema repair. C04 uses
actual V2 consumer, typed record/attempt/state and actual public aggregation to
prove private query/argument/result sentinels remain absent from public output.
Use literal independently expected prior evidence, not self-equality alone.

test_codex_stream_provenance: F00–F02 (3 cells): existing runner binding, new MCP
finalization binding, actual stale-resume rejection with no overwrite/provider call.

## Fault controls, validation and acceptance

Three fresh baseline/control pairs run whole current scenario functions:
FC1 T10 in all46 T cells; FC2 W04-N in all16 W cells; FC3 C01 in all9 C cells.
Only the target call uses the frozen pre-edit translator once; restore the current
function before any subsequent call. Load pre-edit source into a separate private
module with the proper package context; never mutate tracked production or import
a collected module twice. This is a regression counterfactual, not a live attack.
FC1 must fail its expected exception assertion; FC2 its native protocol assertion;
FC3 its zero-dispatch assertion after exactly two harmless duplicate synthetic
tool calls and two requests; the second request uses the restored current
translator and a valid final response. No assertion,
classifier, report builder, provider result or tool result may be patched.
Require exactly one substitution per control, 71 baseline passes, three intended
failures and 68 unaffected passes. Verify later IDs, exact tool/request counts,
source path-set and hashes, process exits, log hashes and all external traps.
Unexpected secondary failure invalidates a control; preserve it as diagnostic
evidence and use fresh filenames on any rerun.

Snapshot the accepted 174-file entry before edits. Final expected inventory is175
with one new test file. Reconcile the seven allowed files (three production,
three existing tests, one new test) against the full inventory. Run full pytest,
focused Codex/provider/Direct/MCP/V2 suites including L and M, Ruff, offline lock,
whitespace and collected-test-import checks on one frozen boundary. Require an
independent source/test review followed by independent final evidence review.
No whole-stage, test-count-reduction, live capability or publishability claim.

Stop before implementation if review finds an unresolved compatibility decision;
stop acceptance on wrong scope, moving source, malformed evidence, unplanned test
migrations or failed gates. Preserve the explicit user deferrals rather than
calling a real provider to settle an offline uncertainty. Subsequent accounting,
refusal classification, request transcript reconstruction, sequence-number policy
and model-specific live qualification stay separately owned roadmap work.

## N-A1 — Unknown-event hashability

Independently approved after source/test review, before freeze. The terminal
classification currently uses set membership on event.type. A list or dictionary
therefore raises TypeError even though no hashing is required for the planned
unknown-event policy. Replace that one membership test with tuple membership,
retaining the same equality comparisons. Add T46: a translator-only
SimpleNamespace(type=[]) before a coherent valid completion is ignored. Add T47:
the same object after completion produces the constant protocol failure after
exhaustion. These explicitly exercise translator objects, not successful SDK
decoding of malformed event envelopes. No other production behavior/file changes.
Final totals supersede the base matrix: four functions, 76 cells (48+16+9+3);
three control pairs cover 73 baseline passes, three intended failures and 70
unaffected passes. FC1 runs all 48 translator cases. Fresh source/test review and
the complete frozen gates remain required.
