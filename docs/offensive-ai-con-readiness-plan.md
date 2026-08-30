# ORI Offensive AI Con Readiness Plan

Date: 2026-08-30
Scope: signed Release 1 branch plus the stacked campaign-operations and conference
work, current V29 benchmark boundary, and the work needed to present ORI
credibly by 2026-10-05.

## Current state

### Release 1 provider hardening

- Branch: `fix/openai-provider-runtime`
- Published at signed candidate commit `d6ac198`; ahead of `master` by seven
  signed commits:
  - `cd240d0` `feat: support Nous Portal API keys`
  - `cf458fb` `docs: add Nous Portal Ox Alpha run profiles`
  - `fa1e6c6` `fix: harden OpenAI-compatible provider runtime`
  - `5e3f7c7` `fix: construct typed V2 MCP launcher`
  - `ede0f49` `fix: normalize MCP provider tool schemas`
  - `e35da9f` `fix: close V2 provider hardening gaps`
  - `d6ac198` `fix: harden compatible provider runtime`
- Release 1 passed the original gates before the final benchmark-operations review:
  - `uv run pytest`: 872 passed
  - `uv run ruff check src scripts tests`: passed
  - `git diff --check`: passed
  - secret scans: passed
- PR is not open yet because GitHub SSO blocked `gh pr create`.

The final operations review also found the still-open MCP circuit-amplification
defect from the August autonomous campaign. A bounded follow-up change now gives
MCP the same pre-provider open-circuit gate as Direct. Its focused test proves
that a failed BloodHound recovery produces a zero-token `CIRCUIT_OPEN` result
without invoking the provider or a tool. The staged follow-up also makes MCP
configuration conditional on selecting the MCP track, so Direct-only campaigns
no longer need a placeholder MCP checkout. The exact updated Release 1 batch
passed its focused provider/runtime tests, Ruff, `git diff --check`, and a
repository-wide secret scan before final review and live re-certification. The
circuit/configuration follow-up is signed as `e35da9f`; the later fail-closed
provider correction advances the frozen Release 1 candidate to `d6ac198`. No
performance or conference files were folded into that release candidate.

The controlled-host acceptance pass also found that a non-interactive operator shell
could start ORI with an absolute `uv` path while the nested MCP launcher later
looked up bare `uv`/`uvx` names from a narrower `PATH`. The final fix does not
search implicit Homebrew locations. It accepts only executables resolved from
the operator's `PATH` or absolute, executable paths supplied through
`ORI_UV_EXECUTABLE` and `ORI_UVX_EXECUTABLE`. The resolved binaries, UV version,
and MCP runtime executable are recorded in private launcher provenance and
resume fingerprints. Readiness and execution reuse the same resolved launcher
object, so the binary that was qualified is the binary that is launched.

An independent code-review pass initially rejected the implicit-directory
fallback and incomplete binary provenance. After the trust-boundary and
fingerprint fixes, the second review reported no findings. A restricted-`PATH`
controlled-host test pass with explicit executable overrides completed 151 focused
tests. The exact combined Release 1, campaign-operations, compiler-index, and
conference-evidence tree then completed 919 tests in 86.48 seconds, Ruff,
staged and unstaged `git diff --check`, and a 399 MB repository-wide secret scan.

### What Release 1 now proves

- Laguna-style `content: null` Chat Completions responses no longer crash the harness through `None.strip()`.
- Direct and MCP now share the same typed Chat Completions normalization boundary.
- Provider-key routing is endpoint-family specific:
  - OpenRouter uses only `OPENROUTER_API_KEY`
  - Nous uses only `NOUS_API_KEY` or `NOUS_PORTAL_API_KEY`
  - OpenAI uses only `OPENAI_API_KEY`
  - generic compatible hosts use only `OPENAI_COMPAT_API_KEY`
- Requested and resolved `api_surface` values are fingerprinted into V2 provenance and resume compatibility.
- MCP tool schemas now omit explicit JSON `null` keywords that Nous rejected.
- Direct-only V2 configs do not require MCP tools, configuration, or a checkout;
  MCP selection still fails readiness when its explicit configuration is absent.

### Pre-final-review V13 live acceptance (now historical)

- Fresh V13 compile and full V29 live certification passed on the controlled benchmark host against graph
  `fb0b6785e524d40abcc9033ea2c7887eaa88e13bb6cb1f329636a4ce4b74b1c4`.
- The certified inventory is 46 Direct / 70 MCP tasks, with the semantic release
  scheduling 42 Direct / 55 MCP tasks.
- Fresh no-model readiness passed for:
  - the one-task Nous Direct canary;
  - the one-task Nous MCP canary;
  - the full 42 Direct / 55 MCP V29 release.
- The V13 Laguna Direct canary reached Nous once and completed as typed
  `OUTPUT_INVALID`: 1,096 input tokens, 2,048 output tokens, zero infrastructure failures, zero
  harness failures, and a valid campaign.
- The V13 Qwen MCP canary reached Nous, launched the exact BloodHound MCP
  checkout through the configured absolute UV executable, made one tool call, and completed as
  typed `OUTPUT_INVALID`: 10,984 input tokens, 721 output tokens, zero
  infrastructure failures, zero harness failures, and a valid campaign.
- Both canaries published track-completion receipts after matching pre/post
  graph gates. Incorrect model outputs therefore remained model outcomes and
  did not become provider, infrastructure, or harness failures.
- The full V29 V9 readiness receipt records `ori-v2-model-campaign-v13`, the
  exact UV/MCP runtime executable, requested/resolved Chat Completions surface,
  Nous endpoint family, and credential source name. It launched zero model calls.
- A redacted machine-readable receipt is preserved on the controlled benchmark host beside the
  fresh canary artifacts. It contains no prompts, provider responses, API keys,
  or BloodHound credentials.

The launcher provenance change intentionally advances the V2 model-run schema
to V13 and the readiness schema to V9. Earlier certification, readiness, and
canary artifacts are therefore retained as diagnostics but are stale for the
final exact runtime. The fresh V13 compile, live certification, Direct and MCP
Nous canaries, and full no-model readiness all passed from new campaign roots;
no old campaign root was resumed.

### Current Release 1 candidate and remaining blockers

- A final independent provider review found two additional fail-closed gaps:
  `openai-compat` could inherit the OpenAI SDK's official default endpoint when
  no compatible base URL was supplied, and an environment-sourced compatible
  endpoint was not included in resume identity. The correction now requires an
  explicit compatible endpoint, hashes the exact effective endpoint into the
  runtime fingerprint, and routes Direct and MCP request construction through
  the shared `ProviderRequest` projection.
- The post-review correction passed 215 focused tests, 884 tracked Release 1
  tests, Ruff, `git diff --check`, a 135-commit Gitleaks scan, fresh Direct/MCP
  compilation, fresh read-only V29 live certification, and fresh 42 Direct / 55
  MCP no-model readiness with zero provider calls. An independent re-review
  reported no findings.
- The correction is now signed as commit `d6ac198` and pushed to
  `origin/fix/openai-provider-runtime`. The signature uses the configured GitHub
  RSA identity. This is the exact candidate head for all remaining canaries and
  review; do not move the evidence boundary to a different tree implicitly.
- Fresh exact-head Nous Direct and MCP canaries now pass the interoperability
  gate from new roots. Direct Laguna produced typed `OUTPUT_INVALID` with zero
  infrastructure and harness failures. MCP Qwen made seven tool calls and six
  Cypher calls, then produced typed `OUTPUT_INVALID` with zero infrastructure
  and harness failures. Both campaigns were valid and matched the graph before
  and after execution.
- The exact-head public-safe receipt is
  `docs/evidence/provider-hardening-d6ac198-nous-acceptance.json`. The older
  `provider-hardening-v13-acceptance.json` remains historical pre-correction
  evidence and must not be substituted for the `d6ac198` receipt.
- Fresh OpenRouter Direct/MCP canaries remain required. The controlled host
  still needs an `OPENROUTER_API_KEY`, and GitHub organization SSO authorization
  is still required before opening or merging the PR.
- OpenRouter live qualification is still blocked by the missing `OPENROUTER_API_KEY` on the controlled benchmark host.
- Because the revised plan requires both providers, Release 1 is not merge-ready
  yet even though the code, local gates, and Nous acceptance are ready.
- Opening the PR is blocked on GitHub SSO authorization.

### Autonomous campaign operations

A separate signed stacked commit, `317ed9c`, now adds
`ori campaign-status --config <exact-config> [--json]`. It projects the current
V2 lifecycle, lock liveness, readiness, checkpoints, reports, and track
completion without preparing a campaign, calling a provider, starting MCP,
querying BloodHound, reading the live graph, or writing state. It fails closed
on corrupt, incompatible, orphaned, or incomplete evidence and distinguishes a
truly active process from a stale `running` lifecycle.

The repository-owned supervisor contract keeps process persistence, bounded
restart policy, notifications, secret injection, and archive transfer outside
ORI. The outer supervisor consumes only the redacted status JSON and may resume
only by following ORI's typed `next_action` when resume is allowed. Interrupted
readiness is rerun without `--execute`; interrupted paid execution uses the
exact original `run-v2 --config ... --execute` command. This closes the
monitoring and reattachment design gap without inventing a second checkpoint
state machine.

### Separate offline compiler-performance slice

Signed stacked commit `56cb346` indexes immutable graph objects,
relationships, properties, and identities by graph fingerprint instead of
rescanning the 17,088-object / 60,342-relationship V29 snapshot for every
fixture and route position. Process-wide derived indexes are limited to the two
most recent graph fingerprints so a long-lived worker cannot retain an
unbounded sequence of benchmark graphs.

This is intentionally separate from Release 1 because it changes compiler and
certifier implementation fingerprints and therefore requires fresh compilation,
certification, readiness, and campaign roots. It must not invalidate the
candidate provider acceptance boundary by being folded into signed Release 1
commit `d6ac198`.

On the same workstation and complex seed-4401 inputs, clean `ede0f49` versus
signed commit `56cb346` measured:

| Track | Clean `ede0f49` | Indexed working tree | Wall-time reduction | Maximum RSS |
|---|---:|---:|---:|---:|
| Direct compile/offline certification | 23.52 s | 13.87 s | 41.0% | 506 MB versus 507 MB |
| MCP compile/offline certification | 33.22 s | 17.80 s | 46.4% | 530 MB versus 576 MB |

All eight Direct/MCP public, oracle, inventory, and offline-certification JSON
artifacts matched byte-for-byte after removing only fields whose names end in
`fingerprint`; those fields are expected to change because the implementation
fingerprint changed. The graph/compiler regression gate passed 51 tests and
Ruff. Treat the timing comparison as working evidence until it is preserved in
a repeatable host-qualified receipt.

## Project history and engineering narrative

ORI began in March 2026 as a deterministic synthetic-AD generator plus a direct
Cypher grading harness. The project then accumulated the boundaries needed to
turn a useful experiment into a defensible benchmark:

1. **March-April — generation and Direct/MCP execution.** ORI added seeded AD
   scenarios, BloodHound CE ingest compatibility, direct Cypher grading, model
   matrices, MCP tools/resources, telemetry, and timeout handling.
2. **May-June — failure attribution and realistic corpora.** Scoring, watchdogs,
   partial-run behavior, Phase 4 failure taxonomy, and richer forest scenarios
   were hardened. The older Phase 4B 100-question product is a separate legacy
   corpus and must not be confused with the unfinished V2 official selector.
3. **July — complex benchmark and containment.** Tier 6 attack paths, exact
   SharpHound relationship validation, repeat runs, and model-blind Direct query
   containment were added. The first complex campaign exposed prompt/scorer
   drift, alternate-path rejection, recall-only set behavior, and tool-loop
   finalization defects.
4. **Late July — protocol V2.** ORI moved to one typed claim, public
   `AcceptanceSpec`, sealed oracle, shared `EvidenceIR`, and shared comparator.
   Repeated real-run incidents drove strict identity, route, set, count,
   bounded-negative, pagination, timeout, durability, and campaign-accounting
   contracts.
5. **August — V29 and durable operations.** V29 separated the larger certified
   inventory from the semantic-unique release, bound graph/runtime/capability
   fingerprints, and added durable checkpoints and per-track publication.
   An autonomous supervisor then ran a six-model, five-pass campaign. Its Direct track is valid;
   Neo4j failed during MCP, revealing a costly circuit-amplification defect.
6. **Late August — provider production hardening.** OpenRouter and Nous became
   endpoint-isolated compatibility paths. Laguna's nullable response exposed the
   need for typed provider turns; pre-correction Nous interoperability testing
   then exposed null JSON-Schema keywords rejected by the portal. Both providers
   are fixture/regression-covered; four post-correction live canaries remain the
   Release 1 qualification gate.

The presentation's strongest engineering story is not that ORI was correct on
the first attempt. It is that the incidents described here drove typed,
fingerprinted, reproducible boundaries rather than being hidden inside a score.

## Codebase map and reviewed boundaries

| Surface | Authoritative implementation | Current role | Completion evidence |
|---|---|---|---|
| Product generation and SharpHound serialization | `src/ori/generator/` | Builds deterministic simple/complex graph products and validates planted relationships before archive publication | deterministic generation tests plus archive validation |
| Legacy V1 runner and reports | `src/ori/eval/runner.py`, `grader.py`, `report.py` | Preserves reproducibility for older Phase 3/4 and complex-v1 development campaigns | frozen V1 evidence; not the current scoring boundary |
| V2 compiler, typed claims, and sealed artifacts | `src/ori/eval/v2/compiler.py`, `schema.py`, `campaign.py` | Compiles solver-visible tasks and private oracles from one claim contract | offline certification and artifact fingerprints |
| Offline/live graph parity | `src/ori/eval/v2/graph.py`, `live_projection.py`, `live_certification.py` | Proves archive and controlled BloodHound produce equivalent typed evidence | three-gate live certification receipt |
| Direct containment | `src/ori/eval/direct_query_safety.py`, `src/ori/eval/v2/direct_adapter.py` | Admits bounded read-only CySQL and prevents dangerous or repeated queries | policy-v3 tests, deny cache, shared circuit, live controls |
| MCP evidence and finalization | `src/ori/eval/v2/mcp.py`, `model_runtime.py` | Binds model tool use to claim-relevant typed evidence and schema-valid final answers | fixture differential tests and live MCP certification |
| Provider transport and credentials | `src/ori/eval/provider_contract.py`, `provider_auth.py`, `src/ori/eval/v2/model_runtime.py` | Normalizes Chat Completions/Codex Responses and isolates endpoint-family credentials | Release 1 fixtures plus exact-head Nous receipt; OpenRouter live gate pending |
| Campaign durability and publication | `src/ori/eval/v2/campaign_runner.py` | Owns lock, lifecycle, attempts, checkpoints, graph gates, reports, and track completion | durability tests and completed V29/Nous receipts |
| Read-only operations projection | `src/ori/eval/v2/campaign_status.py` | Gives an external supervisor a redacted typed action without creating state or contacting services | focused tests; full fault-injection matrix still pending |
| Reporting and conference assets | `src/ori/eval/v2/report.py`, `scripts/build_v2_model_card.py`, `scripts/build_offensive_ai_con_assets.py`, `docs/assets/offensive-ai-con/` | Produces public-safe reports, graph-gated model cards, and deterministic conference visuals | fail-closed model-card tests, hashes, build tests, offline browser QA |
| Official suite selector | not yet implemented | Must select deterministic 100 Direct / 100 MCP public releases with documented quotas | post-conference product gate |

## Experiment and result ledger

This ledger prevents exploratory, diagnostic, invalid, and current evidence
from collapsing into one misleading score series.

| Period | Boundary and models | Track / denominator | Outcome | Validity and evidence |
|---|---|---|---|---|
| March-June | Phase 3 local-model iterations | Direct and MCP; changing diagnostic corpora | Established baseline execution, tool traces, and early scorer behavior | exploratory only; detailed notes and controlled archives are not a current ranking |
| May-June | Phase 4 / Phase 4B diagnostics | legacy registry and 100-question product experiments | Added failure taxonomy, watchdogs, and product split | historical protocol; do not label as V29 or future official 100/100 |
| July 23 | complex-v1, GPT-5.6 Sol vs GPT-5.5 | Direct 42 scheduled; MCP 62 per model | Direct stopped after 26/42; MCP preserved 44/62 and 46/62 | Direct invalid from BloodHound failure; MCP development-only because scorer/prompt defects were later proven; see `complex-v1-development-freeze.md` |
| Late July | V10 GPT-5.6 Sol and GPT-5.5 | Direct 46; MCP 70 | stored-answer diagnostics later produced Direct 23/46 and 21/46, MCP 45/70 and 40/70 | diagnostic replay only because models saw older prompts; see `benchmark-v2-certification-evidence.md` |
| August 13-22 | V29 six-model, five-pass campaign | Direct 42 x 5 per model; MCP 55 x 5 planned | Direct completed 1,260 valid samples; MCP failed after Neo4j loss and circuit amplification | Direct historical development evidence is valid; MCP has no publishable ranking or completion receipt |
| August 22-29 | Nous Ox Alpha / Laguna investigations | partial Direct/MCP attempts | Provider availability, nullable content, tool-schema, launcher, and recovery defects reproduced | diagnostic only; every pre-`d6ac198` root is stale and non-resumable |
| August 30 | Release 1 exact-head Nous canaries | one Direct and one MCP task | both campaign-valid `OUTPUT_INVALID`; MCP completed 7 tools / 6 Cypher calls | current interoperability evidence for Nous at `d6ac198`; zero infra/harness failures; public-safe receipt in `docs/evidence/` |
| Pending | OpenRouter exact-head canaries | one Direct and one MCP task | not run | fixture-covered only until live credential and canaries exist |
| Authorized next | Laguna S 2.1 full V29 campaign | 42 Direct + 55 MCP, one pass | exact no-model readiness passed with zero provider calls; paid run waits for credential rotation | readiness `67a0632d59e6...`; success requires valid track receipts, full report, and generated model-card image |

## Known gaps and debt register

| Gap | Why it matters | Exit gate |
|---|---|---|
| Release 1 is not on `master` | provider fixes are not yet the default product | OpenRouter canaries, SSO, PR review, signed merge |
| The operations/conference branch does not contain final `d6ac198` | no single current integrated tree is qualified | restack after Release 1; full tests, compile, live certification, readiness, canaries, and supervisor smoke on one exact head |
| Outer supervisor recovery is not fully qualified | lock ownership alone does not prove process progress | complete the contract's fault-injection matrix before unattended paid execution |
| Compiler index is a separate fingerprint-changing slice | performance benefit cannot inherit older certification | integrate intentionally, then regenerate every V2 artifact and receipt |
| Full current-runtime MCP result is absent | historical MCP campaign is invalid | complete the authorized Laguna Direct+MCP campaign or use an explicitly reduced fallback claim |
| Official 100/100 selector is absent | V29 is a certified development release, not the promised official public suite | implement policy/quotas, certify seed 67, run post-selector evidence campaign |
| Official OpenAI Responses is offline-only | there is no live OpenAI key/canary | Release 2 fixtures first; live qualification only when a key is intentionally available |
| Conference video and rehearsal receipts are absent | HTML/PNG fallback alone does not prove delivery readiness | record and hash video; second-machine QA; timed 25/35/45-minute rehearsals |

## Current benchmark truth

The current benchmark that can be described as the working V29 boundary is:

- product: `complex`
- live graph fingerprint: `fb0b6785e524d40abcc9033ea2c7887eaa88e13bb6cb1f329636a4ce4b74b1c4`
- MCP capability: `ori-mcp-92a37dd-bhce-9.1-cypher-v7`
- certification inventory: `46` direct / `70` MCP
- scheduled semantic release: `42` direct / `55` MCP

This is the key honesty boundary:

- ORI does have a certified V29 development boundary.
- ORI does not yet have the promised official `100` direct / `100` MCP selector implemented.
- seed `4401` is the certified development graph; the previously locked official
  suite seed is `67`, so an eventual public 100/100 release requires new
  selection, certification, and model evidence rather than relabeling V29.
- Historical autonomous campaign artifacts are useful diagnostic evidence, not a release gate.
- Direct and MCP remain separate scores. They should not be collapsed into one headline score.

### Current result evidence

The August six-model V29 Direct track completed 30 valid runs and 1,260 samples,
with zero infrastructure failures, zero harness failures, and zero unexecuted
tasks. Pooled five-run Direct results were:

| Model | Correct | Effective accuracy | Run range |
|---|---:|---:|---:|
| GPT-5.5 | 207/210 | 98.57% | 95.24%-100.00% |
| Daybreak Red | 206/210 | 98.10% | 95.24%-100.00% |
| Daybreak Blue | 205/210 | 97.62% | 95.24%-100.00% |
| GPT-5.6 Sol | 204/210 | 97.14% | 95.24%-100.00% |
| GPT-5.6 Luna | 193/210 | 91.90% | 88.10%-95.24% |
| GPT-5.6 Terra | 180/210 | 85.71% | 78.57%-95.24% |

The top four models form a tight cluster; differences of one to three answers
out of 210 are comparable to repeat variation, so this evidence does not
support a strong ordering among them. Tier 6 concentrated most of the remaining
Direct difficulty.

The MCP track is **not publishable as a ranking**. Neo4j was OOM-killed during
Luna MCP run 3. The circuit opened, but the old runner continued later provider
calls. From the first affected task onward it made 1,215 provider attempts,
consumed roughly 79.5 million tokens, and attempted 15,124 MCP tool calls. The
mandatory post-track graph gate then failed, so no MCP track-completion receipt
or public reports were produced. Twelve of 30 MCP runs are useful private
diagnostics, but none is an official V29 MCP result.

Those attempt, token, and tool totals come from the controlled private forensic
bundle. They are incident-analysis figures, not yet frozen public conference
evidence. Keep them out of the deck unless they are independently regenerated,
redacted, hashed, and bound to a public-safe forensic receipt.

## Historical run status that should not be confused with current readiness

The imported autonomous six-model campaign bundle remains historically valuable, but its final status is `execution-failed`. Its no-model readiness and certification receipts are valid evidence for the older runtime and graph boundary; they are not a substitute for the newer Release 1 provider acceptance gate.

The old failed Laguna campaign must not be resumed after the provider-runtime fingerprint change.

## Definition of finished

### 1. Release finished

Release 1 is finished only when all of the following are true:

- PR from `fix/openai-provider-runtime` to `master` exists
- local gates still pass on the exact PR head
- OpenRouter direct and MCP canaries pass on controlled benchmark host
- Nous direct and MCP canaries pass on controlled benchmark host
- receipts confirm typed provider/model/infrastructure classification with zero unexpected `HARNESS_ERROR`
- `master` contains the signed Release 1 commits

Release 2 is finished only after Release 1 is merged, then a separate `feat/openai-responses-runtime` branch adds the shared Responses adapter and offline contract coverage without changing existing `auto` behavior for OpenAI-compatible providers.

### 2. Benchmark finished

Two finish lines are intentionally separate:

- **Conference-current V29** is finished when one integrated post-Release-1
  head is compiled, live-certified, readied, fault-smoked, and used for a valid
  Direct+MCP evidence campaign with a report and model card.
- **Official public suite** is finished only when:

- the official selector exists and deterministically emits exactly `100` direct and `100` MCP tasks
- selector policy and tier distribution are documented
- compile, certification, readiness, and resume provenance all fingerprint the selector output
- the selected suites are live-certified against the controlled graph
- at least one fresh post-selector paid campaign completes on the current runtime

### 3. Scientific-validity finished

ORI is scientifically finished enough for public claims only when:

- the public denominator is explicit for every chart and table
- V1, V28/V29 diagnostic, and future official-suite results are never compared as one ranking series
- every published run names the graph fingerprint, capability pin, runtime fingerprints, and scheduled task counts
- model failures, proof failures, infrastructure failures, and harness defects remain separate in reporting

### 4. Operability finished

ORI is operationally finished only when:

- controlled benchmark host has a stable operator runbook for direct and MCP launches
- autonomous campaign supervisor can start, monitor, and archive a campaign without hidden machine-local edits
- provider credentials live outside the repo and are documented by env var name only
- BloodHound target verification always points at the controlled ORI host before spend
- controlled archive storage export paths and report-bundle generation are part of the documented workflow
- an open BloodHound circuit prevents both Direct and MCP provider spend
- the outer campaign supervisor can reattach to an existing valid campaign root
  and mirror ORI lifecycle state without inventing a second state machine
- the complete token-capped supervisor acceptance matrix passes: duplicate lock,
  active/stale state, graceful interruption, exact resume without terminal
  replay, Direct/MCP circuit containment, corrupt-evidence rejection, graph
  drift, invalid completion, and verified archival

### 5. Conference finished

ORI is conference-finished by 2026-10-05 only when:

- Release 1 is merged
- the talk/demo uses one exact benchmark story that is true today
- at least one current-runtime benchmark run is available for screenshots/tables
- the talk explicitly distinguishes certified development boundary from future official public suite
- fallback slides exist in case live BloodHound or provider access is unavailable
- organizer title, abstract, duration, template, AV, and delivery constraints are
  confirmed or a dated fallback format is recorded
- final deck, PDF, offline HTML, static images, and recorded video fallback are
  generated, hashed, and secret-scanned
- the offline package passes on a second machine/profile
- written Q&A answers, a cut list, and timed 25/35/45-minute rehearsal receipts
  exist, including one full-deck and one offline-fallback rehearsal

## Must / should / could

### Must

- Open the Release 1 PR after SSO access is available.
- Preserve signed candidate commit `d6ac198` as the exact canary and PR head.
- Preserve the completed fresh Nous Direct and MCP canaries from new roots on
  that exact head; rotate the credential before future Nous calls.
- Run fresh OpenRouter Direct and MCP canaries on the controlled benchmark host
  with a new `OPENROUTER_API_KEY`.
- Merge Release 1 only after all four post-correction canaries pass.
- Preserve the new full V29 live-certification and no-model-readiness receipts;
  they now cover the circuit-safe runtime fingerprint.
- Start a fresh post-merge campaign root for any further provider runs.
- Freeze the conference story as "V29 certified development benchmark plus
  provider and operations hardening." The official public 100/100 suite is the
  dated post-conference product lane and must not be implied on October 5.

### Should

- Implement the official `100/100` selector as the next major benchmark feature after Release 1.
- Produce one fresh current-runtime benchmark run on the post-Release-1 code path for conference tables and screenshots.
- Create one canonical operator config for the controlled benchmark host that points at:
  - the current ORI checkout
  - the dedicated ORI BloodHound target
  - the pinned `92a37dd` MCP revision or its intentional successor
- Add a short conference-facing report that translates V29 correctness into plain language.
- Validate the repository-owned `campaign-status` projection and supervisor
  contract with an external fault-injection smoke before another large
  unattended MCP campaign.

### Could

- Release 2 Responses support after Release 1 lands.
- A reduced direct-only public demo profile for live presentation safety.
- A small conference appendix that shows why ORI separates direct, MCP, proof, and infra outcomes.

## Risks

- The biggest immediate schedule risk is acceptance drift: signed candidate
  `d6ac198` is pushed, but all four provider canaries and the PR must remain
  bound to that exact head.
- The biggest product risk is over-claiming the benchmark as "official" before the `100/100` selector exists.
- The biggest demo risk is depending on live infrastructure during the talk. BloodHound host state, provider availability, or MCP reachability can all fail independently.
- The biggest interpretation risk is presenting historical autonomous supervisor artifacts as if they were current-runtime final rankings.

## Recommended execution sequence

### 2026-08-30 to 2026-09-02 — close Release 1

- Signed candidate `d6ac198` is pushed; keep it frozen while completing release
  acceptance.
- Preserve the completed exact-head Nous Direct/MCP receipt; do not rerun it
  merely to replace a model-attributable `OUTPUT_INVALID` result.
- Add `OPENROUTER_API_KEY` on the controlled benchmark host.
- Run fresh OpenRouter Direct/MCP canaries from two new campaign roots.
- Open the Release 1 PR.
- Merge Release 1 after review and canary confirmation.
- Preserve the already-passing circuit-safe V13 certification/readiness
  receipts. If the separate compiler-index commit is selected for the next
  campaign, compile, certify, and run readiness again because its fingerprint
  intentionally differs.

### 2026-09-03 to 2026-09-08 — freeze the conference claim

- Choose the talk claim: certified V29 development benchmark and the engineering
  failures that hardened it. Do not make the official 100/100 suite a talk gate.
- Select the small conference model matrix and token budget.
- Primary evidence matrix: Poolside Laguna S 2.1, one 42-task Direct pass and one
  55-task MCP pass. Reduced fallback: Direct-only 42 tasks if MCP operability has
  not passed by September 15. Do not add repetitions until one valid pair exists.
- Freeze a hard operator ceiling before execution: maximum one external restart,
  no replay of terminal tasks, and a cumulative token ceiling derived from the
  one-task MCP canary plus provider pricing confirmed immediately before launch.
  Stop rather than silently shrink the denominator if the ceiling is reached.
- Freeze the exact graph, MCP pin, runtime commit, task releases, and result schema.

### 2026-09-09 to 2026-09-15 — operations hardening

- Merge or cleanly restack signed `campaign-status` commit `317ed9c` after
  Release 1 lands.
- Run the complete token-capped supervisor matrix: duplicate lock rejection,
  active/stale distinction, SIGTERM interruption, exact resume with no terminal
  replay, BloodHound outage and Direct/MCP circuit containment, provider auth and
  rate-limit behavior, corrupt/missing evidence rejection, graph drift,
  interrupted post-track verification, invalid completion, and verified archive
  transfer.
- September 15 go/no-go: if Release 1, the integrated head, or the supervisor
  matrix is incomplete, freeze the conference result claim to the historical
  Direct evidence plus current provider-hardening canaries. Do not launch a
  large MCP run to recover the schedule.
- Validate the completed offline demo bundle on a second machine/profile so the
  talk does not depend on live services. The repository package contains six
  keyboard-navigable beats, six 1280-by-720 static fallbacks, source and render
  receipts, and explicit abort rules.

### 2026-09-16 to 2026-09-22 — fresh benchmark evidence

- Run one fresh current-runtime Direct+MCP campaign on the frozen V29 release.
- Use the explicitly authorized Laguna S 2.1 one-pass matrix. Enforce the frozen
  token/restart ceiling and monitor only through redacted status/progress.
- Start with one pass per model. Expand repetitions only after the first complete
  valid track pair publishes successfully.
- Generate redacted reports, repeat-aware tables, cost/latency summaries, and
  immutable hashes. Generate the public-safe Laguna model-card JSON and image.
  Freeze the evidence bundle by September 22.
- Record, hash, secret-scan, and second-machine-test a short video fallback by
  September 22.

### 2026-09-23 to 2026-09-28 — talk construction

- Build the talk around the problem, failure incidents, typed V2 architecture,
  Direct evidence, honest MCP boundary, and the fresh current-runtime result.
- Prepare screenshots and a short recorded or offline demo.
- Write the Q&A answer sheet and cut list. Record 25-, 35-, and 45-minute timing
  results, including one full-deck and one offline-fallback rehearsal, and remove
  material that cannot fit.

### 2026-09-29 to 2026-10-02 — technical freeze

- Accept only correctness, security, documentation, and demo-blocking fixes.
- Re-run links, hashes, chart labels, provenance, and secret scans.
- Rehearse from the offline fallback on a different machine/profile.

### 2026-10-03 to 2026-10-05 — delivery buffer

- Final wording and rehearsal only.
- Verify the laptop, adapters, local artifacts, video fallback, and PDF export.
- Do not change benchmark semantics or launch a new large campaign.

### 2026-10-06 to 2026-10-19 — official selector implementation

- Implement the approved deterministic seed-67 selector policy and tier quotas.
- Require exactly 100 Direct and 100 MCP tasks, semantic-equivalence accounting,
  selector fingerprints, and reviewable selection receipts.
- Add fixture, determinism, quota, leakage, and denominator tests.

### 2026-10-20 to 2026-10-26 — official-suite certification

- Generate the seed-67 archive and selected suites from one signed head.
- Run offline certification, controlled ingest verification, full live
  certification, and no-model readiness.
- Fail closed on any graph, capability, or denominator ambiguity.

### 2026-10-27 to 2026-11-09 — official evidence and release

- Run one token-capped Direct+MCP pass before considering repetitions.
- Publish separate Direct/MCP reports, model cards, methodology, hashes, and a
  signed release only if both tracks are valid.
- If a track is invalid, publish the incident and retain the suite as a release
  candidate rather than manufacturing a ranking.

Release 2 Responses support also remains a post-Release-1 lane. It should not
compete with current-runtime benchmark evidence or presentation preparation.

## Owner and automation split

The user's evening time should be reserved for decisions, credentials, review,
and rehearsal. Codex/automation can own long-running deterministic work.

| Work | User | Codex/automation |
|---|---|---|
| Credentials and GitHub SSO | Add/authorize | Verify presence and result without exposing secrets |
| Release review | Approve PR and merge | Run gates, review, push, collect evidence |
| Certification/readiness | Approve target and paid boundary | Run detached, monitor, checkpoint, summarize |
| Paid campaign | Approve exact matrix/budget | Execute, stop on invalidity, archive to controlled archive storage |
| Talk narrative | Choose emphasis and voice | Draft charts, source map, notes, and fallback demo |
| Rehearsal | Present and edit | Time, capture questions, revise supporting material |

Autonomous runs must stop on graph drift, stale fingerprints, provider auth,
unexpected harness failures, an open circuit that cannot recover, missing
track-completion receipts, or a budget limit. They must not continue merely to
fill a denominator.

## Immediate next actions

1. Rotate the controlled-host Nous token before any further Nous model call;
   retain the exact-head completed canary receipt.
2. Add `OPENROUTER_API_KEY` on the controlled benchmark host and run the two
   OpenRouter Direct/MCP canaries from fresh roots.
3. Authorize GitHub SSO and open the already-prepared Release 1 PR from
   `fix/openai-provider-runtime`.
4. Merge Release 1 to `master` after review.
5. Finish the campaign-status fail-closed corrections and the full external
   supervisor fault matrix, then restack `feat/v2-campaign-operations` after
   Release 1 reaches `master`.
6. Complete no-model readiness for the authorized Laguna S 2.1 42-Direct / 55-MCP
   configuration, freeze the token/restart ceiling, then execute after token
   rotation.
7. Generate the full redacted Laguna report and deterministic model-card image.
8. Qualify one integrated head containing Release 1, operations, selected
   compiler performance, reporting, and conference changes.
9. Treat the dated October-November official `100/100` selector lane as the next
   product release, not as a prerequisite for the October 5 talk.

## Current working command boundary

After a full Direct+MCP campaign reaches valid graph-gated completion, generate
its public-safe model card from the campaign evidence root:

```bash
uv run python scripts/build_v2_model_card.py \
  --campaign-root <completed-campaign-root> \
  --output-dir <public-evidence-output> \
  --model poolside/laguna-s-2.1 \
  --display-name "Poolside: Laguna S 2.1"
```

The builder emits `v29-model-card.json` and a deterministic 1280-by-720
`v29-model-card.svg`. It fails closed unless the lifecycle, readiness, Direct
and MCP track-completion receipts, graph gates, candidate releases, run
identities, public reports, and row-derived summaries all agree. It does not
accept a partial or invalid track as presentation evidence.

The current honest operator story is:

- for the public product workflow, use `ori generate`, `preflight-tasks`, `verify-ingest`, then `ori run`
- for the certified V29 boundary, use `ori run-v2 --config ...` for no-model readiness and add `--execute` only for intentional paid runs
- never resume pre-hardening Laguna or other stale V2 campaigns after the provider-runtime fingerprint change

A controlled archive retains the historical V29 bundle. It is diagnostic evidence, not the current release gate or a public source-tree artifact.

The prepared controlled benchmark host handoff uses the exact complex seed-4401 source,
the dedicated ORI BloodHound target, pinned MCP revision `92a37dd`, the 42/55 V29
release, and a fresh output root. No-model readiness must pass again after every
runtime fingerprint change; only the same config with explicit `--execute`
spends provider usage.

## Adversarial plan audit

These are the main ways the current plan could still fail through wording or
gate drift:

- Release 1 is not merge-ready until fresh Nous/OpenRouter Direct and MCP
  qualification is complete on signed candidate `d6ac198`, including the
  already-passing Nous pair, pending OpenRouter pair, and PR review.
- The conference story must not drift into "official public benchmark" language. The honest current claim is the certified V29 development boundary plus the provider-hardening work that made it operable.
- Historical autonomous campaign artifacts are useful diagnostics, but they are not current-runtime proof. Any table or screenshot for the talk needs a fresh post-hardening run.
- The new read-only `ori campaign-status` slice closes the visibility gap for monitoring and reattachment checks, but it does not by itself prove that the outer supervisor can safely restart long unattended campaigns.
- `running` means the campaign lock is owned; it is not a heartbeat. The outer
  supervisor must never start a second process while that lock is held and must
  prove its no-progress/SIGTERM policy in the acceptance matrix.
- The old Laguna campaign and any pre-fingerprint-change checkpoints stay invalid for resume purposes even if they still exist on disk.
- Direct and MCP must stay separated in every chart and conclusion; the failed MCP ranking is a boundary condition, not a benchmark result to average away.

## Source map

- `scripts/build_offensive_ai_con_assets.py` and
  `docs/assets/offensive-ai-con/` — deterministic historical Direct chart, CSV,
  and report-hash evidence manifest.
- `docs/offensive-ai-con-talk-outline.md` — timed narrative, claim register,
  offline demo storyboard, visual inventory, and evidence-freeze checklist.
- `docs/offensive-ai-con-offline-demo.md` and
  `docs/assets/offensive-ai-con/offline-demo/` — keyboard-navigable offline demo,
  six static stage fallbacks, and render receipts.
- `docs/assets/offensive-ai-con/ori-offensive-ai-con-working-deck.pptx` — current
  working deck; not final until organizer inputs and frozen evidence are bound.
- `docs/benchmark-v2-design-rationale.md` — canonical architecture, incidents,
  metrics, and V29 boundary.
- `docs/benchmark-v2-certification-evidence.md` — offline/live certification
  evidence and fingerprints.
- `docs/benchmark-hardening-runbook.md` — safety, failure taxonomy, scoring,
  and operational gates.
- `docs/benchmark-v2-task-authoring.md` — candidate certification and future
  selector-facing contract.
- `docs/v2-campaign-supervisor-contract.md` — bounded external monitoring,
  restart, stop, and archival contract for autonomous campaigns.
- `docs/evidence/provider-hardening-v13-acceptance.json` — public-safe V13 validation and canary evidence index.
- `docs/evidence/provider-hardening-d6ac198-nous-acceptance.json` — exact-head
  Nous Direct/MCP acceptance and pending OpenRouter/release gates.
