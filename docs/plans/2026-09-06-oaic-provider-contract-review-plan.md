# S1-05A — credential destination and provider contract audit

Revision 1, September 6. Status: audit complete; independent final review approved
after exact test indexing and redirect-evidence ledger correction. Nine-file/160-source
entry and completion inventories match; 92 focused tests pass. This is a read-only review, not authority to implement
findings or contact a model/provider. It follows the bounded publication fixes.

## Purpose and ownership

Close a substantive part of Stage 1's remaining whole-code review: credential
selection, request destination, provider request/response normalization and public
error metadata. Identify dead/redundant code only after tracing every static and
dynamic caller. Do not remove compatibility branches merely because a current
campaign does not use them.

Root owns orchestration, reproduction design, local validation and public-safe
review/progress documentation. A separate read-only auditor owns complete source
and test analysis. The independent challenge reviewer owns plan and final ledger
review; the source auditor cannot approve its own conclusions. No production or
test file is writable in this slice. Findings require separate detailed fix plans,
independent challenge and implementation approval within the existing goal.

No remote Git/signing/merge, provider/model call, credential-file access, live graph,
host/service mutation or dependency installation. Use synthetic credential values
only in local tests. Never print real environment values or auth file contents.
Readiness passing is not permission to spend usage.

## Current discovery and exact source closure

Read these four files completely, not only their entry functions:

- `src/ori/eval/provider_auth.py` (129 lines at discovery).
- `src/ori/eval/provider_contract.py` (453 lines).
- `src/ori/eval/codex_oauth.py` (387 lines).
- `src/ori/eval/adapter.py` (670 lines).

They total 1,639 lines. Treat counts as location aids, not completion evidence.
Record exact start/end hashes. Include dynamic imports inside provider branches;
module-level import searches are insufficient.

Follow supporting contracts only where called by these four files: model/base-URL
resolution and serialization in `inspect_runtime.py`; typed config fields and
validation in `run_config.py` and `v2/campaign_config.py`; provider metadata export
in `telemetry.py`; actual Direct caller in `v2/model_runtime.py`; native compatible
request helper in `mcp_runtime.py`; campaign readiness capability checks and
configuration-to-call forwarding in `v2/campaign_runner.py`. For each supporting
file record exact functions/line spans read and why. If a dependency remains
unread, mark its obligation open instead of claiming transitive coverage.

Native loop sequencing, tool admission, MCP launcher environment, full V2 execution/
cancellation and query/projector semantics are separate S1-05B/C audits. Reading a
supporting call site here does not close those audits. Native loops have V1 callers,
so future loop edits require V1 and V2 compatibility evidence.

## Required contract-to-test ledger

Produce one row per provider branch (Anthropic, Ollama, OpenAI, compatible, Gemini,
Codex) and each relevant obligation below. Record source decision, exact existing
test and assertion, whether the helper is real or mocked, uncovered cases and
retain/fix/remove/undecided disposition. Retain all 72 provider/obligation cells;
mark genuinely inapplicable cells N/A with a reason, never omit them. A test name
is not proof of coverage. When installed SDK source supports a finding, record
the installed dependency version and exact inspected source-file hashes.

1. Resolve configured, inline and override endpoints to the actual request URL;
   determine precedence and whether query/userinfo/fragment syntax is accepted.
2. Bind each credential source to that exact destination; no cross-provider
   fallback, private auth leakage or credential-bearing error output. Inspect
   behavior before client construction when credentials are missing.
3. Trace redirects, SDK base URLs, environment/proxy defaults and transport-client
   settings from local code. Distinguish locally established behavior from library
   assumptions. Inspect installed library source read-only if necessary; do not
   exercise a network destination or infer unverified SDK behavior as fact.
4. Resolve requested versus effective API surface, structured output and provider
   capabilities. Unsupported requests fail typed and do not silently fall back
   or contact a provider during no-model checks.
5. Preserve public task/schema/system/user request boundaries through provider
   translation without mixing sealed oracle fields or hidden task-specific hints.
6. Distinguish final answer, refusal, reasoning-only, tool-only, malformed, incomplete
   and provider error responses. Unknown response shapes cannot invent success.
7. Preserve tool IDs, names, arguments and streamed text without duplication or
   conflation; inspect terminal, duplicate and out-of-order event handling. Do not
   demand unsupported streaming capabilities without identifying the contract.
8. Preserve unknown input/output/reasoning/cached usage until an explicitly
   documented compatibility boundary; distinguish missing data from measured zero.
   Trace downstream projections only far enough to identify where information is
   retained or lost, and mark deeper loop/durability work open.
9. Separate provider timeout/transport failures from model-invalid output and
   harness defects. Preserve cancellation propagation and identify caller-owned
   persistence obligations without asserting this audit proves them.
10. Trace every emitted error/telemetry field: no raw tokens, endpoint userinfo,
    responses, private paths or credential values in public projections. Private
    diagnostic retention and public exports have different contracts.
11. Identify actually unreachable helpers/branches and duplicate implementations
    using callers, dynamic imports and compatibility entry points. Source similarity
    alone never authorizes removal or consolidation.
12. Record review limits and route concrete findings to the correct later owner:
    auth/provider adapter, shared native transport, V2 orchestration, or reporting.

## Test and evidence boundaries

Read all five primary test modules completely:
`test_provider_auth.py`, `test_provider_contract.py`, `test_provider_adapter.py`,
`test_provider_v2_config.py`, `test_codex_oauth.py`.
Inspect relevant supporting tests when a ledger obligation refers to them; cite
actual assertions and replacement boundaries. At discovery, these five modules
total 1,631 lines; verify current content rather than relying on that figure.

Before running the focused suite, verify its transport/SDK/auth-file fixtures use
synthetic data and intercept every provider path exercised. Do not call real login,
refresh, capability endpoints or provider SDK requests. Stop a questionable test
from execution until its scope is established. Root runs the five modules as the
baseline and records actual IDs/counts/outcomes; passing tests do not negate a
source-backed counterexample.

For each suspected bug, first document a precise input/expected/actual claim and
which layer owns the contract. Then root may reproduce with a private, finite,
model-free probe using fake clients/transports and synthetic sentinels. Preserve
raw probe inputs/results only under ignored `results/p2-slices/s1-05a/`. No new
permanent tests or code edits are authorized by this audit plan. If reproduction
would require external access, record the missing proof and defer that probe.

Capture the full current source inventory and focused-file hashes before review;
reconcile them at completion. If S1-04D is still validating, do not dispatch until
its final inventory boundary is accepted. Historical line references or discovery
notes cannot substitute for reading the audited version.

## Deliverable and exit

Write `docs/plans/2026-09-06-oaic-provider-contract-review.md` with source/hash scope,
complete dispatch map, provider-by-obligation ledger, exact test mapping, supported
findings and explicit unresolved closures. Never report a structural search as a
completed audit or infer a vulnerability solely from absent test names.

Independent final review reconciles the ledger with source, focused tests, private
reproductions and unchanged inventory. Root updates Stage 1 progress without
claiming shared native loops, graph/certification, full V1 or full V2 runtime are
reviewed. Completion means this bounded audit is evidence-backed; confirmed fixes
and broader Stage 1 remain open until separately implemented and validated.

Stop on unexpected source drift, a real credential/provider path, unbounded probe,
unsupported security claim or a required dependency whose behavior is unknown.
Resolve safely in scope, or record the exact open obligation; do not fabricate
evidence or broaden authority. Remote consolidation and final merged certification
remain deferred under the operator's current instruction.
