# S1-05O / scorecard prerequisite: schema-repair execution evidence

Status: implemented and reviewed; focused and batch-end full regression pass.

Operator strategy override: the September 6 capability-first cadence supersedes
the bespoke controls, snapshot/reconciliation and exact test-matrix expansion
requirements later in this historical plan. No new control drivers were created
or executed. The implemented tests cover the demonstrated lifecycle and strictness
gaps using existing pytest, synthetic HTTPX and real state/report interfaces.
Focused new/C10 validation passes four tests and 43 subtests; existing runtime/
Ollama validation passed 137 tests and 96 subtests. Batch-end full regression
passed 1,198 tests and 1,371 subtests in 254.49 seconds. The reviewed receipt
contract and preservation requirements remain.
After this correction, advance to the task-library/selection deliverable.

## Entry and verified cause

Accepted entry: 179 files at
`4478262cc59b1514e706158e99ebbe4f20731234bd75bb240f05821c4653bdc1`;
1,195 tests and 1,339 subtests passed. Preserve all dirty work and historical
receipts. Remote Git, live models, real credential files and services are deferred.
Tests use synthetic transports and controlled temporary files only.

The existing `FinalizationState.schema_retry_count` counts a reducer transition,
not transport execution. Runtime may invoke repair, then revoke qualifying
evidence for output/transcript bounds before replaying finalization. C10 therefore
has three synthetic wire requests and 33/21 returned tokens but reducer count zero.
Timeout and terminal failures similarly preempt finalization. Model-free replay
can report one reducer retry without invoking any provider. Campaign operational
attempts/retries count whole-task executions/infrastructure retries, not model turns.

Do not overwrite any of those counters or change grading, retry eligibility,
deadlines, request bodies, tool availability, failure classification or token sums.
Add independently observed private transport-invocation evidence instead. This is
an accounting prerequisite, not completion of the full public scorecard/cost phase.

## Interface and lifecycle

Define strict `SchemaRepairExecutionV1` in `src/ori/eval/v2/model_runtime.py` with
`schema_version: Literal['ori-schema-repair-execution-v1']`,
`transport_invocations: int` (strict, zero or one), and
`responses_received: int` (strict, zero or one).
`status` is exactly `not_started | preparation_failed | returned | raised |
timed_out | interrupted`.

Coherent tuples are: not_started=(0,0); preparation_failed=(0,0);
returned=(1,1); raised=(1,0); timed_out and interrupted=(0,0) or (1,0).
No other combinations are valid. A returned ModelResponse with an error is still
a returned response; existing typed failure metrics retain its attribution.
The receipt contains no payload, provider identity, endpoint, timestamp or tokens.
It does not claim an HTTP request, billable request, or complete usage observation.

Store its JSON under reserved private
`ProviderRunRecord.provider_metrics['schema_repair_execution']`. `_record` accepts
an optional typed receipt and inserts it after adapter metrics, so adapters cannot
override the harness receipt. `ProviderRunRecord` validates this reserved mapping
when present, rejects it for Direct, and includes it in the existing record hash.
Missing receipt in historical records means unknown, never zero; do not add a
serialized default field or rewrite historical hashes. An explicit null mapping
is invalid. The reserved value must be an actual mapping, containing exactly all
four schema fields; a receipt-model object is not a serialized mapping. In particular,
reject empty or partially populated mappings rather than applying constructor
defaults to historical input. Internal typed constructors may retain defaults.
Normal new MCP records include a zero receipt even if repair is never
eligible. Interrupted MCP records include the observed receipt as well.

Create a task-local mutable holder of this immutable receipt before entering the
MCP loop. Pass a wrapper of the supplied TextTransport to `_schema_only_retry`.
Increment only when the wrapper is actually entered, after transcript projection
and immediately before calling the transport. The wrapper forwards every keyword
unchanged, awaits once, records a returned response before returning it, and
records raised/interrupted status before re-raising exceptions/cancellation.
The existing outer repair handlers distinguish deadline timeout from interruption
and replace status accordingly without changing counts. A preparation exception
before wrapper entry sets preparation_failed; zero remaining deadline sets
timed_out with zero invocations. Preserve the holder on all MCP cancellation exits
and the final `_record` call, including bounds revocation and fact rejection.
The normal no-repair path remains not_started. No second invocation is permitted;
an unexpected second wrapper entry raises an internal invariant error before
calling the transport rather than silently inflating a bounded receipt.

The reducer and model-free scoring APIs are unchanged. No new public report or
model-card field is introduced in this slice: existing public aggregate attempts,
retry counts and token totals must remain identical. Follow-on scorecard work may
aggregate this receipt using separately named metrics, with unknown historical
coverage explicit. Partial usage from a transport that raises remains unknown;
this slice does not manufacture tokens or claim zero cost.

## Ownership, compatibility and allowed files

Root owns production interface/integration in `src/ori/eval/v2/model_runtime.py`,
plan, documentation and private validation drivers. A test specialist owns the
new `tests/test_schema_repair_accounting.py` and additions only to the existing
C10 assertions in `tests/test_ollama_stream_acceptance.py`. Reuse non-collected
support modules; never import collected tests or weaken existing assertions.
An independent reviewer owns plan/implementation/evidence review, read-only except
an explicitly assigned private final receipt.

No other production files change. `model_runtime.py` is already included in runner
and MCP-finalization fingerprints: verify both bindings and stale resume rejection
without overwriting old campaign files. Recompile/recertify/readiness and a fresh
campaign root are required for later execution. Historical records without the
reserved key remain readable with their original hashes, but are not evidence of
known-zero repair usage. New private metrics remain excluded from public artifacts.

## Exact acceptance matrix

One grouped runtime test has A00–A17, preserving score/classification as follows:

| ID | Scenario | Invocation/response | Status |
| --- | --- | --- | --- |
| A00 | Valid first answer | 0/0 | not_started |
| A01 | No qualifying evidence | 0/0 | not_started |
| A02 | Initial terminal infrastructure failure | 0/0 | not_started |
| A03 | Deadline exhausted before repair dispatch | 0/0 | timed_out |
| A04 | Native transcript projection rejects arguments | 0/0 | preparation_failed |
| A05 | Valid repair | 1/1 | returned |
| A06 | Malformed repair | 1/1 | returned |
| A07 | Nonfinite repair | 1/1 | returned |
| A08 | Repair adds forbidden facts | 1/1 | returned |
| A09 | Output overflow after repair, C10 equivalent | 1/1 | returned |
| A10 | Transcript overflow after repair | 1/1 | returned |
| A11 | Returned typed provider error | 1/1 | returned |
| A12 | Raised typed provider exception | 1/0 | raised |
| A13 | Raised internal harness exception | 1/0 | raised |
| A14 | Deadline timeout after dispatch; cleanup awaited | 1/0 | timed_out |
| A15 | Operator cancellation after dispatch | 1/0 | interrupted |
| A16 | Cancellation during initial MCP loop | 0/0 | not_started |
| A17 | Cancellation after repair eligibility, before wrapper entry | 0/0 | interrupted |

A05/A09/A14/A15 use the actual native Ollama loop and HTTPX synthetic receiver,
not a mocked lower-level loop. A09 proves three observed requests, 33/21 returned
tokens, reducer zero, OUTPUT_INVALID, no reasoning verdict and no ranking. A14
retains existing TASK_TIMEOUT/reducer-zero behavior. A15 catches the real typed
cancellation exception and verifies its private record, prior evidence/transcript,
no extra tools, and cleanup. Every case has socket/DNS/process/credential traps;
requests remain synthetic. Remaining cases may use narrow synthetic TextTransport
and projector observations to isolate the specified lifecycle boundary.
A16 and A17 traverse the actual typed cancellation-record exits. A17 uses a
controlled coroutine scheduling hook at repair entry before transport invocation;
it must not replace the cancellation handler or fabricate the resulting record.

One grouped contract test has B00–B14: valid receipt/record JSON roundtrip and
hash binding; reject boolean counter; reject count above one; reject response
greater than invocation; reject status/count mismatch; reject unknown key;
reject explicit null; reject reserved receipt on Direct; old missing-key record
keeps its original hash; model-free reducer retry one supplies no operational
receipt; B10–B13 omit schema_version, transport_invocations, responses_received,
and status respectively; B14 supplies an empty mapping. All five latter cases
must fail reserved-field validation, not a stale fingerprint. Existing C10 gains
explicit reducer-zero and receipt1/1 assertions only.

One grouped integration test has C00–C03: real private state/report roundtrip
preserves receipt without public leakage and keeps attempts/retries/tokens equal;
interrupted-attempt checkpoint preserves receipt; runner and MCP fingerprint
bindings include the changed source; stale provenance rejects resume and leaves
existing files unchanged. Use real serializers and report builders, not fabricated
public summaries. Exactly three new collected tests and 37 subtests; expected
combined suite is 1,198 tests and 1,376 subtests with no old case removals.

## Independent controls and final gates

Snapshot approved plan and the exact entry source before implementation. Private
evidence belongs in new `results/p2-slices/s1-05o/`; exclusively create receipts,
retain failed diagnostics, and never overwrite earlier phase evidence.

Three private one-shot controls use actual collected module identity: FC1 drops
the final runtime receipt only for A09 (expected failure on missing receipt while
later cases still pass); FC2 changes the cancellation receipt to zero/not_started
only for A15 (failure on its invocation count); FC3 wraps the real `_record` once
only during C00. It receives the real completed ProviderRunRecord, removes only
the reserved metric from its dumped payload, recomputes `record_fingerprint` via
the real canonical hash excluding that field, validates the coherent missing-key
record and returns it. All subsequent attempt/state fingerprint construction and
private serialization remain real and unchanged. This simulates a legacy-compatible
receipt omission and must fail the explicit post-roundtrip preservation assertion,
not a checksum exception or earlier setup assertion. Each control has a fresh-process unmodified
baseline and verifies intended assertion, later-case reachability, no extra
injection, zero external operations, and unchanged complete discovered inventory.
FC1 and FC2 each run the 18-cell runtime group; FC3 runs the four-cell integration
group. Expected totals are 40 baseline passes, three intended failures and 37
unaffected passes across six fresh processes. All three controls must reach later
cases after their target (A10–A17, A16–A17, and C01–C03 respectively).
Independent reviewer must approve exact driver implementation before execution.

After implementation/source review: freeze source; run focused new/C10/runtime/
campaign/model-card tests, full pytest, Ruff, offline lock check, whitespace,
and the static import gate. Require exact counters, old assertion retention,
request parity, unchanged public scoring/operational semantics, all three controls,
source-bound log hashes and independent final evidence review. A passing private
test is not live qualification, merged-code acceptance or conference completion.

Stop for unplanned changes to score, failure taxonomy, prompts, wire requests,
token totals, old hashes, public metrics, cleanup, timeout behavior, or evidence
admission; resolve with a reviewed amendment. Stop for unknown lifecycle states,
source drift, malformed receipts, incorrect controls, or any real external call.
