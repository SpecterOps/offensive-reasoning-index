# Stage 1 next boundary — native stream integrity

Status: read-only preparation, not an approved implementation plan. September 6,
2026. Prepared while S1-05L final offline validation was running. Do not implement
this proposal before a fresh detailed plan and independent challenge.

## Recommended narrow next slice

Ollama Direct and native MCP both accumulate streamed content and accept EOF
without requiring a terminal completion receipt. The relevant loops are in
adapter.py's Ollama branch and mcp_runtime.py's _ollama_chat_turn. The latter's
native consumer accepts returned tool calls, so stream admission must finish
before any tool dispatch. This source inspection is not a claim that a partial
tool call was executed in a live campaign.

Both loops also parse JSON and access object fields without a narrow wire-format
validation boundary. Malformed JSON/non-object chunks can escape as internal
defects. Explicit provider error chunks need a deliberate typed outcome.
Existing ProviderProtocolError already maps to nonretryable provider protocol
failure in Direct and native V2; no new serialized failure schema is required
merely for missing or malformed framing.

## Decisions the next plan must settle

- Require an actual boolean terminal completion rather than arbitrary truthiness;
  decide compatibility for ordinary chunks that omit done.
- Decide duplicate-terminal and post-terminal payload handling, including whether
  to keep reading until EOF to detect either. Preserve watchdog and cancellation
  semantics; do not turn a provider that never closes its stream into a hang.
- Preserve blank lines, valid content/thinking/tool concatenation, metrics-only
  terminal chunks, request options, timeouts and destination admission.
- Distinguish malformed framing, explicit provider errors and legitimate
  truncation such as done_reason:length. Truncation must not silently become a
  provider infrastructure error.
- Prove rejected native streams cannot reach tool dispatch; preserve evidence
  from earlier completed turns. Keep error messages public-safe and raw payloads
  private. Do not broadly catch unrelated programmer exceptions.

Useful current tests include test_ollama_chat_turn_streams_payload_options_and_tool_calls,
Ollama destination request-parity/failure scenarios and L's successful-stream
cases. Their valid completions do not prove missing-terminal rejection. Use
actual transport/consumer tests, deterministic negative controls and a frozen
full-suite boundary rather than inferring correctness from source alone.

## Separate subsequent work

Codex translator ordering, duplicate call IDs and reconciliation of streamed
versus completed output require a distinct plan. Current source rejects missing
completion but does not establish all ordering/tool-identity invariants. Existing
failed/incomplete/missing/empty terminal tests are not comprehensive reconciliation
proof. The currently retryable CodexResponseStreamError is another explicit
compatibility decision, not a reason to alter every failure type in this slice.

Subsequent independent read-only source inspection found that repeated completed
events overwrite the retained response; post-completion deltas are still accepted;
repeated function-call completion events append duplicate IDs; and missing IDs or
arguments receive invented defaults. Streamed text/calls also suppress completed
output fallback without reconciling partial inventories or disagreement. These
are translator observations, not proof of duplicate live tool execution. Both
consumers buffer events before translation, so a future shared admission check
can reject an invalid turn before native dispatch. Its separate plan must settle
terminal uniqueness, identity requirements, repeated-identical-call policy,
completed-output ordering and absent-field compatibility, then prove zero tool
dispatch plus preservation of evidence from prior valid turns. Retain the current
translator signature and separately review existing failed/incomplete attribution;
do not silently fold usage or refusal semantics into this cleanup.

Usage knownness, cached/reasoning accounting, SDK billable retries and scorecard
metrics remain in their coherent provider/accounting phases. Token-exchange
redirect acceptance also remains separate from L's native inference boundary.
M's C10 development scenario additionally exposed an accounting discrepancy:
the actual three synthetic requests and cumulative tokens include a schema
repair, but finalization reports schema_retry_count=0 after the output-byte
bound revokes evidence. Follow-through now adds separate typed private
schema_repair_execution transport-invocation/response accounting. The reducer
counter retains its semantic meaning rather than being overwritten. C10 retains
three requests, 33/21 returned tokens, reducer zero and OUTPUT_INVALID, and now
records one repair invocation and one returned response. Raised/cancelled usage
is not invented, historical missing receipts remain unknown, and no new public
metric or ranking permission is inferred. Full scorecard accounting remains its
own roadmap capability.
No live providers, graph operations, hardware changes or remote Git actions were
performed during this preparation.
