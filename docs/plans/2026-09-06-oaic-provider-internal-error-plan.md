# S1-05F — internal adapter faults versus provider failures

Revision 2, September 6. Status: complete, independently reviewed and validated.
The pre-edit 161-file snapshot matched S1-05E. Final validation covers 163 files,
1,141 full-suite tests and 65 focused tests plus 134 subtests. All eight fault
controls and eight fresh baselines passed independent final evidence review.

## Evidence and dependency

S1-05E is accepted at a 161-file inventory with 1,126 tests and 419 subtests.
A new root reproduction crosses actual call_provider_text and Direct runtime
with only the SDK replaced: ValueError becomes retryable INFRA_ERROR, not harness
failure. A second reproduction shows missing synthetic Codex credentials also
become retryable PROVIDER_ERROR. Both have zero network/query activity; no real
auth file was accessed. Private receipts bind the three involved source hashes.

Typing known Codex credential-acquisition failures is a prerequisite within this
slice. Blanket wrapping of unknown exceptions before that step would incorrectly
label a known configuration failure as an internal defect. Credential selection,
destination admission and the separate Codex/OpenAI fallback issue are unchanged.

The installed Anthropic SDK also raises bare TypeError when authentication is
unavailable. Its acquisition includes API key, bearer token, custom headers and
SDK credential providers/discovery. Do not substitute an API-key-only guard or
exception-message matching. Before implementation, trace this supported acquisition
surface and define a typed boundary that preserves it. Excluding Anthropic from
the fix is not the chosen resolution: complete the prerequisite instead. This is
resolved by the explicit Anthropic prerequisite below, not an external-access gate.

## Ownership and interfaces

Root owns production `src/ori/eval/adapter.py` and `src/ori/eval/codex_oauth.py`,
this plan, progress/review ledgers and `examples/inference/README.md`, plus
`tests/test_v2_model_runtime.py`, new `tests/test_provider_runtime_acceptance.py`
and new `tests/test_anthropic_auth_boundary.py`.
The existing s1d_runtime_tests worker owns only `tests/test_provider_adapter.py`
and `tests/test_codex_oauth.py`; it must preserve all earlier shared-tree edits.
No collected-test imports. Existing support fixtures may be imported, not changed.
Independent reviewer owns read-only challenge and final source/evidence review.
Preserve all previous dirty edits. No V1/V2 schema, runner or scorer production
changes are authorized; return to review if a downstream change is necessary.

Add `ProviderAdapterInternalError(RuntimeError)` in adapter.py. It is not a
ProviderContractError. Known provider errors still return ModelResponse; unknown
exceptions raised inside the protected provider call become this internal error,
with the original cause and original message. Cancellation remains unwrapped.
Document this Python exception contract. V1 call_model already catches it and
retains MODEL_ERROR and existing serialization. V2's real campaign containment
records HARNESS_ERROR, invalidates the run and does not schedule infrastructure
recovery. No free-form metrics flag substitutes for execution-class correctness.

Wrap bare internal TimeoutError too, so MCP schema retry cannot mistake it for
whole-task deadline expiry. Exceptions before the current adapter try block
(invalid public API-surface enum, for example) retain their existing behavior;
configuration validation is not expanded by this change.

## Codex credential-acquisition prerequisite

Use existing ProviderAuthenticationError from provider_contract, without a cycle.
Preserve nonempty CODEX_API_KEY, then OPENAI_API_KEY, then configured/default auth
file; preserve nested token before top-level. No login, refresh, discovery or new
endpoint checks. Existing fallback is compatibility retained, not approved policy.

## Anthropic credential-admission prerequisite

Keep the exact normal AsyncAnthropic constructor and its existing discovery. Do
not subclass: the locked SDK enables default credential discovery only for its
exact base client types. Do not substitute an API-key-only guard, normalize header
casing, intercept all TypeError exceptions, or match exception messages.

Before messages.create, compute client.default_headers outside the narrow catch;
remove values that are instances of anthropic.Omit. Invoke the existing native
client._validate_headers(headers_dict, {}) inside a catch for TypeError only.
Empty request overrides match ORI's actual messages.create call. Passing the
client's custom default headers as request overrides would change Omit behavior.
On that narrow failure, close the allocated client, suppressing only ordinary
Exception from cleanup, then raise ProviderAuthenticationError with constant
`Anthropic authentication configuration is unavailable` from None. Cancellation
and other BaseException during cleanup propagate. Constructor, header preparation,
and request TypeError remain internal errors. No other client lifetime changes
belong to this slice.

This duplicates SDK-version-coupled header admission, not credential acquisition
or deferred token exchange. The locked SDK is anthropic 0.116.0, client source
SHA256 240329ad19a78f1b990b1856a78c8e42691c54244cf60f8f55da96074e53c944.
Actual unmatched AnthropicError roots represent SDK-originated failures, including
profile/configuration and workload-identity failures: classify them as nonretryable
PROVIDER_ERROR after specific API subclasses. Do not invent an authentication or
transport classification from their text. OpenAI root errors retain the original
proposed internal classification; the Anthropic exception is explicit.

Root's private nine-case experiment compares the proposed native validator call
against actual SDK _build_headers: missing credentials, static key, bearer,
canonical custom key/bearer, lowercase custom key/bearer, discovered dynamic
token-cache, and default Omit. All nine match, including the SDK's rejection of
lowercase-only custom credentials and default Omit. Every client is closed.
Discovery is replaced with synthetic results; token acquisition and network are
trapped and never run. This proves header-admission parity only, not successful
authentication, credential discovery, or provider execution. Independent challenge
accepted the direction subject to cleanup and complete test/control specification.

## Codex credential parsing details

Catch OSError and UnicodeError only around file reading, JSONDecodeError only
around decoding. Validate top-level dictionary explicitly. Evaluate nested tokens
as `data.get('tokens') or {}` to preserve falsey fallback; reject truthy non-dict
containers. Keep selected-token truthiness/precedence, then require a nonempty
string without stripping/reinterpreting it. A truthy non-string nested token
must reject even if the top-level token is valid.

Raise constant path/value-free messages from None:
read failure → `Codex OAuth credential file could not be read`;
decode/shape failure → `Codex OAuth credential file is invalid`;
missing/invalid selected token → `Codex OAuth access token is unavailable`.
Do not catch arbitrary Exception/AttributeError/TypeError/RuntimeError. Unresolvable
tilde-user expansion is outside this bounded file-acquisition policy; do not hide
an arbitrary RuntimeError behind an auth classification. Existing successful
environment/file behavior remains unchanged; error wording intentionally becomes
path-free and auth failures become nonretryable.

## Supported provider exception policy

Change `_provider_exception_metrics` to return metadata or None. Import installed
mandatory OpenAI/Anthropic exception classes locally in the classifier; importing
them must not construct a client or discover credentials. No dependency changes.
Use isinstance, never class-name/module-string/status-attribute trust by itself.

Ordered policy:

1. Existing ProviderContractError branch stays before the general catch.
2. Actual HTTPX TimeoutException and both SDK APITimeoutError: timeout, retryable.
3. Actual HTTPX RequestError and SDK APIConnectionError: transport, retryable.
4. Actual SDK APIResponseValidationError: protocol, nonretryable.
5. Actual HTTPX HTTPStatusError (status from response) or SDK APIStatusError:
   401/403 auth false; 408 timeout true; 429 rate-limit true; >=500 server true;
   other 400–499 request false; other status protocol false.
6. Actual CodexResponseStreamError: retain legacy PROVIDER_ERROR/true explicitly.
   F04 owns splitting provider failure, truncation, missing terminal and empty
   completion. This is a documented temporary compatibility exception.
7. Other actual SDK APIError and unmatched actual AnthropicError roots:
   PROVIDER_ERROR, nonretryable. Unknown SDK error semantics do not authorize
   retries. Other bare SDK roots, spoofed SDK class names/status attributes and
   other exceptions return None.

Returned known failures preserve the current ModelResponse fields and public
schema. Raw parser errors without an existing ProviderProtocolError translation
become internal failures; parser-contract repairs remain separate. Anthropic
credential discovery/destination defects remain F02 and must not be inferred fixed.

## Finite adapter and credential test map

All cases use fresh per-cell monkeypatch scopes, synthetic environment sentinels,
fake SDK/HTTPX constructors and socket/DNS/subprocess traps. No real auth paths.
Use subtests with explicit IDs; preserve existing assertions and test functions.
Append visited IDs before each subtest and assert the exact ordered list after
each matrix. Allocate fresh exception/request/response objects per case.

Core additions are exactly 12 collected functions and 79 cases; the separate
Anthropic additions are three functions and 17 cases, totaling 15 and 96.

| File | Collected function | Cases |
| --- | --- | --- |
| test_provider_adapter.py | test_adapter_internal_fault_boundary | U0–U6: 7 |
| test_provider_adapter.py | test_adapter_known_failure_boundary | K00–K40: 41 |
| test_provider_adapter.py | test_adapter_cancellation_is_not_wrapped | 1 |
| test_provider_adapter.py | test_codex_auth_failure_stops_before_sdk | 1 |
| test_provider_adapter.py | test_unknown_provider_is_typed_capability_failure | 1 |
| test_codex_oauth.py | test_codex_auth_file_rejection_boundary | A0–A13: 14 |
| test_codex_oauth.py | test_codex_auth_file_compatibility | C0–C5: 6 |
| test_codex_oauth.py | test_codex_auth_environment_precedence | E0–E2: 3 |
| test_provider_runtime_acceptance.py | test_v1_adapter_internal_fault_retains_legacy_artifact_contract | 1 |
| test_provider_runtime_acceptance.py | test_codex_auth_failure_is_nonretryable_direct_infrastructure | 1 |
| test_provider_runtime_acceptance.py | test_adapter_fault_campaign_persists_continues_and_resumes_terminal | 1 |
| test_v2_model_runtime.py | test_schema_retry_adapter_internal_fault_is_harness_failure | S0–S1: 2 |

Unsupported provider dispatch raises existing ProviderCapabilityError instead of
ValueError, becoming nonretryable capability failure with no SDK or auth access.
V1 continues to serialize and grade MODEL_ERROR.

Adapter internal cases U0–U6: ValueError, AttributeError, TypeError, bare
TimeoutError, RuntimeError, synthetic class named APIConnectionError, and a plain
Exception carrying status_code503. Fake SDK create raises each; real adapter must
raise ProviderAdapterInternalError with identical cause/message. SDK create count
one. A separate CancelledError case must propagate unchanged. No callback replacement
that bypasses call_provider_text is sufficient.

Known-error cases: three libraries HTTPX/OpenAI/Anthropic crossed with timeout,
connection and statuses401,403,408,429,400,404,500,503,302 (33 cases). Use actual
synthetic exception objects, request/response objects without transports. Add
two SDK response-validation cases, two SDK base APIError cases, one actual Codex
stream-error case and three existing contract errors (auth/capability/protocol).
Total 41 known-error cells; assert exact subtype/retryability and returned error.
Test cancellation separately, not as an Exception case.
K00–K10 HTTPX, K11–K21 OpenAI, K22–K32 Anthropic use the order above.
K33/K34 are OpenAI/Anthropic validation; K35/K36 their base APIError;
K37 Codex stream; K38/K39/K40 auth/capability/protocol contract exceptions.

### Frozen Anthropic additions (root ownership)

In new test_anthropic_auth_boundary.py, collect exactly these three functions:

- `test_anthropic_native_auth_header_parity`: H0–H8 in the exact order of the
  nine-case private experiment above. Real base SDK with MockTransport and
  synthetic discovery; compare native _build_headers with the admission path
  used by the real adapter. For accepted cases return one synthetic message;
  assert exactly one fake message request. Rejected cases return PROVIDER_AUTH,
  retryable false, zero requests and closed client. Dynamic token-cache provider
  is a trap because the fake message request does not acquire a token. Do not
  claim real authenticated HTTP execution.
- `test_anthropic_early_auth_cleanup`: L0 successful close; L1 close raises
  RuntimeError; L2 close raises CancelledError. Missing synthetic credentials
  drive actual adapter admission. L0/L1 retain path-free PROVIDER_AUTH and one
  close call; L2 propagates cancellation. All have zero message requests.
- `test_anthropic_non_auth_fault_boundaries`: B0 constructor TypeError;
  B1 default_headers TypeError; B2 messages.create TypeError; B3 constructor
  actual AnthropicError; B4 request actual AnthropicError. B0–B2 become internal
  error with original cause; B3/B4 return nonretryable PROVIDER_ERROR. No substring
  matching. Successful synthetic credentials for B1/B2/B4. Assert constructor,
  message and cleanup counts appropriate to each injection point.

These add 17 cases. For B1, inject the property fault only after SDK construction,
so it targets adapter header preparation. No collected-test imports or changes
to existing shared support fixtures.

Private control AH0 compiles a process-local copy of adapter._call_provider with
exactly its new `client._validate_headers(headers, {})` statement replaced by
`pass`, retaining original globals. Native SDK _build_headers remains unchanged:
exactly H0/H5/H6/H8 fail the adapter auth rejection assertions and H1/H2/H3/H4/H7
pass, with all nine IDs visited. A global native-validator bypass is rejected
because it fails preliminary SDK parity instead of exercising adapter admission.
Control AL0 compiles a
process-local copy of adapter._call_provider with exactly the new
`await client.close()` statement replaced by `pass`; require one AST match and
leave every other AST node unchanged. Install only that function in the adapter
module, retaining its original globals. L0/L1 fail the one-close-call expectation
and L2 fails cancellation propagation; all three IDs must be visited. No files
are patched for controls; the process-scoped replacement restores on exit. Run
the exact three-function baseline afterward, requiring all 17 IDs pass and zero
network activity. Other boundary controls are specified in the core map below.

Credential rejection cases A0–A13: missing path; directory; injected PermissionError
for that exact test path; invalid UTF-8; malformed JSON; top-level list/string/null;
truthy non-dict tokens; missing token; empty token; top-level numeric token; truthy
numeric nested token with valid top-level; truthy list nested token with valid
top-level. Require ProviderAuthenticationError/code/retryability and messages with
no fixture path, sentinel document or token. Use typed JSON fixture construction;
never attempt real-file discovery. Explicit tests for successful acquisition:
nested; top-level; nested empty fallback; tokens null fallback; tokens empty-list
fallback; nested wins. Three environment precedence cases: both keys; empty Codex
plus OpenAI; absent Codex plus OpenAI. Auth-path helper is a trap for these cases.
Total 14 rejection +9 compatibility cells.

Actual Codex missing-file adapter case must assert no SDK construction/request,
nonretryable PROVIDER_AUTH and path-free error. Real Direct runtime with that
adapter must yield INFRA_FAILURE/INFRA_ERROR with no query or harness verdict.
V1 compatibility case calls actual call_model through fake failing SDK, round-trips
existing serialization and grades MODEL_ERROR; no Cypher/query is produced.

## Durable Direct acceptance

Use module-scoped simple_compiled support fixture and real build_artifacts,
V2ArtifactPair and direct capability profile. Select first two distinct public
tasks and their unchanged oracles. PreparedTrack uses real pair/profile but
synthetic ordered release entries and release/live fingerprints; certification
preparation is replaced, not claimed proven.

Use existing support _config to write temporary config, amend before actual load:
one compatible `adapter-fault` model/test-model at `https://provider.invalid/v1`,
synthetic BHCE URL, max_infra_retries2/immediate1/deferred300. Keep real provenance,
run-directory guards, OracleRegistry, scheduler, checkpoint builders, atomic writes
and disk reload. Only SDK requests are fake: first raises ValueError; every later
request returns content `{}`, stop, usage3/2/5. Coordinator query and health are traps.
Call actual _run_model; no runtime/adapter/containment substitution.

Require exactly two ordered SDK calls, two attempt1 initial/round0 records in
actual PrivateRunStateV2: first HARNESS_FAILURE/HARNESS_ERROR/no verdict, second
MODEL_FAILURE/OUTPUT_INVALID. Scheduler complete with no pending/cooldown;
summary scheduled2/harness1/model1/infra0 and invalid. Operational attempts2,
retries0; first infrastructure retry policy None. Repeat _run_model with SDK trap:
disk-loaded state/attempts/results remain equal and neither task reruns. This is
same-process durable reload, not fresh-interpreter or full publication acceptance.
Existing first-attempt unknown usage is not measured zero cost; do not invent
tokens or elapsed time absent from the current containment receipt.

## MCP schema-retry acceptance

Append to existing model-runtime tests to reuse their local route observation
helper. Use real MCP_TASK/ORACLE/RESOLVER/PROFILE, empty MCPServerBundle, compatible
model/test-model at the synthetic endpoint, explicit actual adapter transport,
native compatible loop and json_schema mode. Fake only initial loop: call real
observer with claim-bound USER-A MemberOf GROUP-B witness; return malformed
`commentary ` plus fixture answer, initial usage10/5/time0.01, and meaningful
user/assistant tool-call/tool-result/malformed-final transcript.

Two cases S0 ValueError and S1 bare TimeoutError from actual adapter's fake SDK
schema-retry create. Assert one initial loop and one retry SDK request; no tools
in retry request; final schema-only instruction and ori_mcp_submission format;
original call ID/tool result retained; no private message metadata; client timeout
positive/bounded. Outcome/finalization HARNESS_FAILURE/HARNESS_ERROR, no verdict,
no infrastructure/task-timeout event, original useful receipt/transcript/digest
and known10/5 usage retained. Infrastructure retry policy None. Do not assert
schema_retry_count1: the existing finalizer can retain0 when retry throws before
event replay. Captured SDK count proves actual retry execution. provider_error
may retain None with the initial response; evidence lives in sample/events.
Existing cancellation/genuine-deadline tests must remain green.

## Validation, migration and stop conditions

Snapshot current161-file inventory and baseline collection before edits. Add tests
first and establish expected red categories, not collection/import failures for
the not-yet-defined internal exception. Then implement auth prerequisite and
adapter classification. Run focused modules plus actual durable/schema scenarios,
independent implementation review, full pytest, Ruff, offline lock and diff checks.
Bind final logs/hashes and verify no source drift during validation. Each private
control uses its own pytest process/plugin with exact sentinel matching, followed
by a fresh unmodified-process baseline. Preserve verbose subtest IDs/counts and
verify unchanged production/test hashes after each control:

| Control | Process-local substitution | Expected result |
| --- | --- | --- |
| U continuation | Classifier returns retryable PROVIDER_ERROR only for U0 sentinel; delegates others | U0 fails, U1–U6 pass, all seven visited |
| K continuation | Classifier returns PROVIDER_ERROR only for uniquely marked K00 HTTPX timeout | K00 fails, remaining 40 pass, all 41 visited |
| A continuation | codex_access_token returns synthetic token only for A0 exact fixture path | A0 fails, remaining 13 pass, all 14 visited |
| Durable boundary | Classifier returns retryable provider error only for first durable sentinel ValueError | Acceptance fails, three SDK calls after immediate retry; no deferred cooldown because every later response is valid envelope with `{}` |
| Schema boundary | Classifier returns retryable provider error only for S0/S1 sentinels | Both harness assertions fail; both cases visited |
| Timeout ordering | Replace adapter.ProviderAdapterInternalError with built-in TimeoutError inside process | Both schema cases hit earlier timeout handling and fail harness assertions |

AH0/AL0 controls are frozen in the Anthropic section. Restore by process exit,
not by assuming a failed test undid its changes. No source-file mutation is used.

Both production files enter shared runtime/finalization fingerprints. All older
V2 campaigns need fresh output/readiness and stale certification renewed. No
schema migration, retroactive rescore or resume bypass. V1 MODEL_ERROR stays.
No real credentials, providers, graph/service changes, remote Git/signing/merge.
Stop on unexpected ownership drift, fake escape, unknown SDK/config compatibility,
downstream production need or evidence that the accepted policy misattributes a
known failure. Full F01/F02/F04–F06 and broader Stage1 stay open.

## Independent challenge disposition and next actions

The reviewer accepted the reproduced defect and the need for actual SDK-class
matching, cancellation/bare-timeout distinction, real disk reload, and retained
Codex stream compatibility. Review is not implementation approval.

Revision 2 resolves the missing specifications: locked-SDK inspection and nine
offline parity cases establish the narrow Anthropic admission boundary; cleanup
and unmatched SDK-root policy are explicit; unsupported provider dispatch is
typed; all 15 test names, 96 cases, file owners, operator documentation and eight
fault controls are frozen. Return this complete revision for independent challenge
before snapshots, test implementation or production edits. Do not carry approval
from S1-05E. Independent implementation review found no production blocker and
required two acceptance refinements: AH0 must bypass only adapter admission, and
schema preservation must compare original receipt fields/transcript contents and
canonical hashes rather than mere nonempty values. Both refinements are applied;
controls, full regression and final independent evidence review subsequently
passed. Final inventory is f1df7bbf70e570a965b6256f22fc0753e940ea820aad2c66102ccef26a19fd74;
full-suite log hash is 364b5c472e352d7b4a2eb821b793ba86dccc08cee95550d0a7fc9966f430f45d.
The full-suite summary reports 1,141 passes in 247.36s without a separate subtest
count. Earlier interrupted/partial-inventory diagnostic runs are not acceptance
evidence. Broad F02 and Stage 1 remain open.

Root's new private reproduction confirms the actual adapter-to-Direct result,
not merely the standalone classifier. It also confirms missing synthetic Codex
auth is currently retryable. No network or graph queries ran; all involved
production sources still match the accepted S1-05E inventory.
