# ORI Offensive AI Con Readiness Plan

Date: 2026-08-30
Scope: signed Release 1 branch plus the stacked campaign-operations and conference
work, current V29 benchmark boundary, and the work needed to present ORI
credibly by 2026-10-05.

## Current state

### Release 1 provider hardening

- Branch: `fix/openai-provider-runtime`
- Published at signed commit `e35da9f`; ahead of `master` by six signed commits:
  - `cd240d0` `feat: support Nous Portal API keys`
  - `cf458fb` `docs: add Nous Portal Ox Alpha run profiles`
  - `fa1e6c6` `fix: harden OpenAI-compatible provider runtime`
  - `5e3f7c7` `fix: construct typed V2 MCP launcher`
  - `ede0f49` `fix: normalize MCP provider tool schemas`
  - `e35da9f` `fix: close V2 provider hardening gaps`
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
follow-up is now signed and published as `e35da9f`; no later operations,
performance, or conference files were folded into that release commit.

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
- The new runtime fingerprint makes the earlier Nous Direct and MCP canaries
  pre-correction evidence. Rerun both from fresh roots after the signed fix is
  pushed; never resume the old roots.
- Fresh Nous Direct/MCP and OpenRouter Direct/MCP canaries are still required.
  The controlled host still needs an `OPENROUTER_API_KEY`, and GitHub organization
  SSO authorization is still required before opening or merging the PR.
- OpenRouter live qualification is still blocked by the missing `OPENROUTER_API_KEY` on the controlled benchmark host.
- Because the revised plan requires both OpenRouter and Nous live canaries, Release 1 is not merge-ready yet even though the code and local gates are ready.
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
already-completed provider acceptance evidence by being folded into signed
Release 1 commit `e35da9f`.

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
   endpoint-isolated live providers. Laguna's nullable response exposed the need
   for typed provider turns; live Nous testing then exposed null JSON-Schema
   keywords rejected by the portal. Both are now regression-covered.

The presentation's strongest engineering story is not that ORI was correct on
the first attempt. It is that each failure became a typed, fingerprinted,
reproducible boundary rather than being hidden inside a score.

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

ORI is benchmark-finished for a publishable benchmark claim only when:

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

### 5. Conference finished

ORI is conference-finished by 2026-10-05 only when:

- Release 1 is merged
- the talk/demo uses one exact benchmark story that is true today
- at least one current-runtime benchmark run is available for screenshots/tables
- the talk explicitly distinguishes certified development boundary from future official public suite
- fallback slides exist in case live BloodHound or provider access is unavailable

## Must / should / could

### Must

- Open the Release 1 PR after SSO access is available.
- Preserve signed candidate commit `d6ac198` as the exact canary and PR head.
- Run fresh Nous Direct and MCP canaries from new roots on that exact head.
- Run fresh OpenRouter Direct and MCP canaries on the controlled benchmark host
  with a new `OPENROUTER_API_KEY`.
- Merge Release 1 only after all four post-correction canaries pass.
- Preserve the new full V29 live-certification and no-model-readiness receipts;
  they now cover the circuit-safe runtime fingerprint.
- Start a fresh post-merge campaign root for any further provider runs.
- Decide whether the conference story is:
  - "V29 certified development benchmark plus provider hardening"
  - or "official public benchmark release"

Those are different claims. Only the first is currently plausible for 2026-10-05 without rushing the selector.

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
- Add `OPENROUTER_API_KEY` on the controlled benchmark host.
- Run fresh Nous and OpenRouter Direct/MCP canaries from four new campaign roots.
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
- Freeze the exact graph, MCP pin, runtime commit, task releases, and result schema.

### 2026-09-09 to 2026-09-15 — operations hardening

- Merge or cleanly restack signed `campaign-status` commit `317ed9c` after
  Release 1 lands.
- Run a representative fault-injection smoke: BloodHound unavailable, circuit
  open, zero later provider calls, clean resume only after health recovery.
- Validate the completed offline demo bundle on a second machine/profile so the
  talk does not depend on live services. The repository package contains six
  keyboard-navigable beats, six 1280-by-720 static fallbacks, source and render
  receipts, and explicit abort rules.

### 2026-09-16 to 2026-09-22 — fresh benchmark evidence

- Run one fresh current-runtime Direct+MCP campaign on the frozen V29 release.
- Start with one pass per model. Expand repetitions only after the first complete
  valid track pair publishes successfully.
- Generate redacted reports, repeat-aware tables, cost/latency summaries, and
  immutable hashes. Freeze the evidence bundle by September 22.

### 2026-09-23 to 2026-09-28 — talk construction

- Build the talk around the problem, failure incidents, typed V2 architecture,
  Direct evidence, honest MCP boundary, and the fresh current-runtime result.
- Prepare screenshots and a short recorded or offline demo.
- Perform two timed rehearsals and remove material that cannot fit.

### 2026-09-29 to 2026-10-02 — technical freeze

- Accept only correctness, security, documentation, and demo-blocking fixes.
- Re-run links, hashes, chart labels, provenance, and secret scans.
- Rehearse from the offline fallback on a different machine/profile.

### 2026-10-03 to 2026-10-05 — delivery buffer

- Final wording and rehearsal only.
- Verify the laptop, adapters, local artifacts, video fallback, and PDF export.
- Do not change benchmark semantics or launch a new large campaign.

### Deferred product lane after the conference

- Create and review the official `100/100` selector implementation.
- Re-run compile, certification, and no-model readiness on the selector-enabled boundary.
- Fail closed on any denominator ambiguity.

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

1. Unlock the SSH signing agent, restore the named post-review provider stash,
   create the signed commit, and push it.
2. Add `OPENROUTER_API_KEY` on the controlled benchmark host and rerun all four
   Nous/OpenRouter Direct/MCP canaries from fresh roots.
3. Authorize GitHub SSO and open the Release 1 PR from
   `fix/openai-provider-runtime`.
4. Merge Release 1 to `master` after review.
5. Restack and review `feat/v2-campaign-operations` after Release 1 reaches
   `master`; recertify if the compiler-index commit is included.
6. Run one fresh V29 current-runtime campaign for conference evidence.
7. Treat the official `100/100` selector as the next major product milestone,
   not as a prerequisite for the October 5 talk.

## Current working command boundary

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
  canaries pass on signed candidate `d6ac198` and the PR review is complete.
- The conference story must not drift into "official public benchmark" language. The honest current claim is the certified V29 development boundary plus the provider-hardening work that made it operable.
- Historical autonomous campaign artifacts are useful diagnostics, but they are not current-runtime proof. Any table or screenshot for the talk needs a fresh post-hardening run.
- The new read-only `ori campaign-status` slice closes the visibility gap for monitoring and reattachment checks, but it does not by itself prove that the outer supervisor can safely restart long unattended campaigns.
- The old Laguna campaign and any pre-fingerprint-change checkpoints stay invalid for resume purposes even if they still exist on disk.
- Direct and MCP must stay separated in every chart and conclusion; the failed MCP ranking is a boundary condition, not a benchmark result to average away.

## Source map

- `scripts/build_offensive_ai_con_assets.py` and
  `docs/assets/offensive-ai-con/` — deterministic historical Direct chart, CSV,
  and report-hash evidence manifest.
- `docs/offensive-ai-con-talk-outline.md` — timed narrative, claim register,
  offline demo storyboard, visual inventory, and evidence-freeze checklist.
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
