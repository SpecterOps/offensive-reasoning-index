# Next-slice preparation: Codex destination and credential binding

September 6. Read-only triage, not an approved implementation plan.

S1-05F deliberately preserves Codex credential precedence and destination
selection while correcting internal-error attribution. The next recommended
bounded slice addresses the remaining P1 portions of provider audit F01/F02:
credential acquisition and effective destination are independent, so an implicit
OpenAI key or OAuth file can be selected for an arbitrary configured destination.
The existing audit reproduction uses synthetic credentials and a fake SDK; it
does not establish a live compromise or authorize provider access.

## Interfaces to inspect together

- `src/ori/eval/codex_oauth.py`: token selection, effective base URL, headers.
- `src/ori/eval/adapter.py`: actual Direct destination, SDK key and bearer header.
- `src/ori/eval/mcp_runtime.py`: native Codex single-turn destination/auth pairing.
- `src/ori/eval/v2/campaign_runner.py`: effective endpoint and credential-source
  provenance, login/model-cache readiness, and resume fingerprints.
- Existing provider, Codex, native MCP, configuration and readiness tests.

## Decisions the fresh detailed plan must settle

Define the exact official origin and whether admission also binds the Codex API
base path. Define migration away from implicit OPENAI_API_KEY fallback without
changing S1-05F's typed file parsing. Preserve explicitly configured compatible
endpoints only through an explicit credential policy that cannot borrow an OAuth
file or another provider's key. State separate local HTTP/custom-port and remote
security rules. Preserve or explicitly migrate the public codex_headers helper
signature; internal callers must supply their resolved destination.

Readiness and execution must use the same destination and credential-source
decision. Local Codex login/cache evidence must not be presented as proof for an
unrelated custom endpoint. Effective environment-based endpoint changes must
invalidate resume identity. Require fresh outputs/readiness and stale-certificate
renewal; do not retrofit old evidence.

## Offline acceptance direction

Freeze a finite pure admission matrix, then cross actual Direct/native consumers
with fake SDKs and real typed configuration/provenance. Assert both SDK key and
bearer surfaces, endpoint parity and rejection before credential-file or SDK
access. Readiness tests use synthetic login/cache boundaries only. Preserve
existing credential parsing cases except explicitly documented migration cases.

No provider calls, operator credential reads, graph/service operations or remote
Git work are needed to prepare this plan. Require a fresh independent challenge
and explicit resolution of compatibility decisions before implementation. This
slice must not claim to fix Anthropic discovery, redirect containment, Ollama or
Gemini endpoint mismatches, response-terminal semantics or usage accounting.
