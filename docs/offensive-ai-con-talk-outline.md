# Offensive AI Con Talk Outline

Status: working conference outline

Target date: 2026-10-05

Working thesis: Building an attack-path benchmark for AI is not mainly a
matter of generating harder questions. It is the work of making graph truth,
execution constraints, public task contracts, evidence, and scoring agree—and
then using real model and infrastructure failures to find the remaining gaps.

The accepted title, submitted abstract, exact session duration, slide template,
and organizer AV constraints are not present in the repository. Insert those
unchanged when they are available. Until then, this outline uses a 35-minute
talk plus questions and includes 25- and 45-minute variants.

## Audience promise

By the end of the talk, an offensive-security or AI-evaluation practitioner
should understand:

1. why plausible Cypher or tool output is not the same as graph-grounded
   attack-path reasoning;
2. why Direct and MCP are separate benchmark surfaces;
3. how ORI binds generated graph truth, live BloodHound state, public task
   contracts, sealed oracles, evidence, and scoring;
4. how benchmark, provider, and infrastructure incidents became typed runtime
   boundaries instead of contaminated scores;
5. what the current V29 development boundary proves—and what it does not.

## Public claim boundary

The main conference claim is:

> ORI currently provides a certified V29 task and certification boundary for
> separate Direct and MCP attack-path reasoning surfaces. Its most important
> result is the failure-driven architecture that makes graph truth, model
> evidence, infrastructure state, and scoring distinguishable. The historical
> Direct campaign is valid evidence; fresh current-runtime Direct and MCP model
> evidence is still pending.

Do not call V29 the official public `100/100` suite. Do not present the failed
historical MCP campaign as a ranking. Do not combine Direct and MCP into one
headline score. Do not describe Release 1 as shipped until it is merged and all
four post-correction Nous/OpenRouter Direct and MCP interoperability canaries
complete on signed commit `d6ac198` with matching graph gates and zero unexpected
infrastructure or harness failures. A typed `OUTPUT_INVALID` can satisfy this
interoperability gate; it is not a claim that the model answered correctly.

## Three-act structure

- **Act I — Plausible is not proven.** Start with the analyst problem and show
  why a fluent answer can still be wrong, unsafe, or grounded in the wrong
  graph.
- **Act II — The benchmark had to learn.** Use failures to motivate graph
  gates, typed claims, Evidence IR, containment, provider normalization, and
  durable campaign state.
- **Act III — The honest boundary.** Present the certified V29 development
  release, the valid Direct evidence, the invalid MCP ranking, and the roadmap.

## Default 35-minute slide plan

| # | Time | Slide | Narrative beat | Visual or evidence | Required takeaway |
|---:|---:|---|---|---|---|
| 1 | 2:00 | A plausible answer is not proof | Open with a model answer that sounds like an analyst but is not yet tied to the loaded graph or a complete path. | One simplified BloodHound path and one plausible-but-incomplete answer. | Fluency is not graph-grounded reasoning. |
| 2 | 2:30 | Why BloodHound is a useful reasoning laboratory | Explain identity paths, decoys, negative controls, alternate routes, and bounded decisions. | Small synthetic graph with source, target, decoy, and two possible mechanisms. | Attack paths expose both reasoning and evidence discipline. |
| 3 | 3:00 | What ORI measures | Separate Direct query generation from MCP tool-driven reasoning. Mention resources only as historical context, not a current V29 score. | Side-by-side Direct/MCP inputs, actions, and outputs. | Different surfaces test different capabilities. |
| 4 | 3:30 | The first benchmark told us where the benchmark was wrong | Show the progression from exact-answer scoring toward typed public contracts and sealed evidence. | V1 → V28 → V29 incident timeline. | Benchmark defects can masquerade as model failures. |
| 5 | 3:30 | Graph truth before model spend | Walk through generate → archive validation → ingest verification → compile/certify → readiness → execute → post-track graph gate. | One pipeline diagram with stop gates. | Health is not ingest; readiness is not execution. |
| 6 | 4:00 | One typed claim, two visibility boundaries | Explain public `AcceptanceSpec`, sealed oracle, solver-visible request, `EvidenceIR`, and comparator. | Claim → public task / sealed oracle → evidence → comparator diagram. | The scorer may enforce only what the public contract supports. |
| 7 | 3:00 | Difficulty without ambiguity | Use one Tier 6 route or decision task to show ordered endpoints, supporting evidence, decoys, and bounded negatives. | Sanitized task card and expected evidence shape, never the sealed answer. | Hard tasks should be difficult because of reasoning, not hidden grading rules. |
| 8 | 4:00 | Incidents drove typed boundaries | Cover nullable provider content, Nous tool-schema rejection, query containment, launcher provenance, graph drift, and the MCP circuit-spend defect. Clearly label the unmerged current-branch changes. | Before/after failure taxonomy and fingerprint map. | Selected incidents drove typed, regression-covered boundaries; staged behavior is not shipped behavior. |
| 9 | 3:00 | What V29 actually is | State the certified inventory, scheduled semantic release, graph fingerprint, and MCP capability pin. | Compact boundary card: 46/70 certified, 42/55 scheduled. | This is a certified development boundary, not the future official suite. |
| 10 | 3:30 | The Direct evidence worth showing | Present six-model, five-pass Direct results, denominators, repeat ranges, and the tight top-model cluster. | Repeat-aware Direct chart with exact `n=210` per model. | Small score gaps must be interpreted against repeat variation. |
| 11 | 2:30 | The MCP result we refused to publish | Explain the Neo4j failure, continued provider spend in the older runtime, failed post-track graph gate, and why ORI withheld the ranking. | Incident sequence, not a leaderboard. | Invalid infrastructure state is not model performance. |
| 12 | 1:30 | What we are not claiming | Explicit anti-claims: no official 100/100 selector, no SOTA ranking, no unified Direct+MCP score, no valid historical MCP leaderboard. | Four large anti-claim statements. | Honest boundaries strengthen the result. |
| 13 | 1:30 | What comes next | Release 1, fresh current-runtime evidence, supervisor acceptance, and the deterministic official selector. | Near-term roadmap ending with the public suite. | The next step is new evidence, not relabeling old evidence. |

Total planned delivery: 35 minutes. Reserve the remaining session time for
questions according to the organizer's confirmed slot.

## Timing variants

### 25-minute version

- Combine slides 2 and 3.
- Reduce the version history on slide 4 to one failure example.
- Use one architecture slide combining slides 5 and 6.
- Remove the detailed Tier 6 example from the main deck and keep it in the
  appendix.
- Keep both the Direct evidence and invalid-MCP boundary; never cut the caveat.

### 45-minute version

- Add a five-minute offline demo after slide 8.
- Expand the Tier 6 example into a step-by-step evidence projection.
- Add one slide on campaign durability and `campaign-status`.
- Add one slide showing how the same failure taxonomy applies across provider,
  MCP tool, BloodHound, and harness boundaries. Visually separate Nous live
  interoperability canaries, OpenRouter fixture coverage pending live canaries,
  and the existing Codex OAuth compatibility path.

## Evidence register

Every quantitative slide must link to an immutable, redacted artifact bundle
before technical freeze. The current Release 1 acceptance JSON describes the
pre-final-review V13 runtime and is historical diagnostic evidence. Signed
commit `d6ac198` is the candidate runtime identity, but it needs four fresh
post-correction canaries and a new redacted acceptance receipt before it becomes
immutable live-release evidence.

| Proposed public statement | Current status | Evidence source | Required qualifier |
|---|---|---|---|
| V29 certifies 46 Direct and 70 MCP tasks and schedules 42 Direct and 55 MCP semantic representatives. | Supported | `docs/benchmark-v2-design-rationale.md`; `docs/offensive-ai-con-readiness-plan.md` | Call it the V29 development boundary. |
| The controlled V29 graph fingerprint is `fb0b6785e524d40abcc9033ea2c7887eaa88e13bb6cb1f329636a4ce4b74b1c4`. | Supported | live-certification evidence summarized in the readiness plan | State that the fingerprint belongs to the certified development graph. |
| The MCP capability is `ori-mcp-92a37dd-bhce-9.1-cypher-v7`. | Supported | V29 readiness/certification evidence | Do not imply all BloodHound MCP revisions are equivalent. |
| The historical V29 Direct track contains 30 valid runs and 1,260 samples with zero infrastructure, harness, or unexecuted outcomes. | Supported historical evidence | `docs/benchmark-v2-design-rationale.md`; readiness report | Label it historical and bind the final chart to the frozen bundle hash plus exact commit/tree and runtime fingerprint. Replace it with fresh evidence only if the post-merge campaign completes validly. |
| GPT-5.5 scored 207/210; Daybreak Red 206/210; Daybreak Blue 205/210; GPT-5.6 Sol 204/210; Luna 193/210; Terra 180/210 on that Direct campaign. | Supported historical evidence | historical campaign bundle and design rationale | Show repeat ranges and avoid strong ordering among the top cluster. |
| The historical MCP campaign is not publishable as a model ranking. | Supported | missing post-track completion/public reports; incident analysis | Explain the failed graph gate and infrastructure incident. |
| The older runner made 1,215 later provider attempts, used about 79.5 million tokens, and issued 15,124 MCP tool calls after the first affected task. | Private incident-analysis figures; not yet conference-ready | historical forensic bundle summarized in the design rationale | Keep the totals out of the deck until they are reverified directly and bound to a frozen forensic receipt/hash. If later used, say “the older runtime recorded” and “approximately” for tokens. |
| Laguna-style nullable Chat Completions content no longer produces a harness `None.strip()` exception. | Supported on signed candidate commit `d6ac198` | provider fixtures and regression tests; pre-correction V13 canary classification is diagnostic only | Do not call Release 1 shipped until merge and fresh canaries. |
| Nous Direct and MCP canaries completed with zero infrastructure and harness failures. | Historical pre-correction interoperability evidence | V13 acceptance evidence | Both outputs were typed `OUTPUT_INVALID`; rerun both on `d6ac198` before using them as release evidence. |
| Release 1 supports production-qualified OpenRouter and Nous. | Not yet supported | Four post-correction canaries are pending | Describe both providers as fixture/regression-covered. Call either live-qualified only after its Direct and MCP canaries pass on `d6ac198`; call the release shipped only after merge. |
| ORI has an official deterministic 100 Direct / 100 MCP suite. | Unsupported | selector is not implemented | Future work only. |

## Direct results slide requirements

The chart must:

- title itself as the historical V29 **Direct** development campaign;
- show `n=210` for each model and `n=1,260` overall;
- include the five-run minimum-to-maximum range for each model;
- label effective accuracy over all scheduled samples;
- include zero infrastructure, harness, and unexecuted outcomes;
- state that the top four are a tight cluster and that the data do not support
  a strong total ordering among them;
- never place MCP results in the same score series.

Prefer a dot-and-range plot over a sorted leaderboard bar chart. It makes repeat
variation visible and reduces the temptation to over-read one- or two-answer
differences.

The reproducible historical asset set is:

- [`historical-v29-direct-results.svg`](assets/offensive-ai-con/historical-v29-direct-results.svg)
  — slide-ready dot-and-range chart;
- [`historical-v29-direct-results.csv`](assets/offensive-ai-con/historical-v29-direct-results.csv)
  — pooled counts and all five run values;
- [`historical-v29-direct-evidence.json`](assets/offensive-ai-con/historical-v29-direct-evidence.json)
  — exact runner commit, 30 report hashes and artifact fingerprints, aggregate
  values, generated-asset hashes, and claim warnings;
- [`build_offensive_ai_con_assets.py`](../scripts/build_offensive_ai_con_assets.py)
  — fail-closed deterministic builder.

The current editable working deck is
[`ori-offensive-ai-con-working-deck.pptx`](assets/offensive-ai-con/ori-offensive-ai-con-working-deck.pptx).
It is generated by
[`build_offensive_ai_con_deck.mjs`](../scripts/build_offensive_ai_con_deck.mjs),
contains repository-backed source blocks in every slide's speaker notes, and
must remain labeled as a working deck until the organizer title, speaker line,
session duration, and presentation template are confirmed.

Regenerate only from the frozen public-report bundle:

```bash
uv run python scripts/build_offensive_ai_con_assets.py \
  --bundle-root <frozen-campaign-bundle-root> \
  --output-dir docs/assets/offensive-ai-con
```

The builder rejects missing or extra runs, duplicated report fingerprints,
invalid campaign accounting, non-zero infrastructure/harness/unexecuted
outcomes, dirty or mismatched runner provenance, and effective-accuracy drift.

## Offline demo storyboard

The implemented conference demo is documented in
[`offensive-ai-con-offline-demo.md`](offensive-ai-con-offline-demo.md) and opens
from
[`offline-demo/index.html`](assets/offensive-ai-con/offline-demo/index.html).
It is rendered from a frozen local bundle, makes zero provider calls, and has a
static 1280-by-720 PNG fallback for every beat. A live run may be optional, but
it must not be required for the talk.

1. **Graph gate:** show the exact manifest/archive identity and a passing ingest
   verification. Explain that a healthy API is insufficient.
2. **No-model readiness:** show artifact, model capability, MCP revision,
   launcher, and exact graph checks completing with zero provider calls.
3. **Safe monitoring:** show `ori campaign-status --config ... --json`
   projecting `readiness_complete`, `running`, or `completed` without contacting
   the provider or graph.
4. **One evidence path:** show a sanitized public task, one Direct query or MCP
   tool receipt, the resulting `EvidenceIR`, and the public outcome. Never show
   the sealed oracle or private provider transcript.
5. **Failure boundary:** show an infrastructure or malformed-provider fixture
   becoming a typed outcome instead of a score or traceback.
6. **Result:** finish on the Direct chart and the explicit MCP non-ranking.

### Demo abort rules

- If BloodHound, MCP, or provider access is unavailable, do not troubleshoot on
  stage; switch to the frozen recording.
- If the live graph fingerprint differs, stop immediately.
- If readiness or status exits non-zero, stop immediately.
- Never expose an env file, credential source path, provider body, prompt,
  private transcript, oracle, or machine-local path.
- Never launch a full benchmark during the talk.

## Required visual assets

- simplified ORI truth pipeline;
- Direct-versus-MCP capability split;
- typed-claim and Evidence IR architecture;
- one sanitized Tier 6 task/evidence example;
- failure taxonomy before/after;
- V29 boundary card;
- repeat-aware Direct results plot;
- MCP incident sequence;
- anti-claims slide;
- roadmap;
- recorded offline demo plus static screenshot fallback for each demo beat.

## Appendix slides

- exact Direct model table and run ranges;
- failure taxonomy definitions;
- query containment policy;
- graph/live-certification fingerprint chain;
- provider credential routing and API surfaces;
- campaign lifecycle and supervisor boundary;
- why V1, V28/V29, and the future official suite cannot be one ranking series;
- official selector acceptance criteria;
- reproducibility commands and public artifact map.

## Questions to prepare for

1. Why use synthetic AD data rather than a real enterprise graph?
2. How do you know the generated archive and live BloodHound graph agree?
3. Can a hidden oracle impose requirements the model never saw?
4. Why are Direct and MCP not directly comparable?
5. How do you distinguish model failure from BloodHound, MCP, or provider
   failure?
6. Why not publish the MCP scores that completed before the outage?
7. Is the top Direct model statistically better than the next model?
8. Does the benchmark measure offensive ability, Cypher skill, tool use, or all
   three?
9. How do you prevent expensive or unsafe model-generated Cypher?
10. What would make the benchmark an official public release?

## Asset and evidence freeze checklist

Before freezing the deck:

- insert the accepted title, abstract, duration, and organizer requirements;
- replace historical Direct evidence with fresh post-merge evidence if—and only
  if—the new campaign publishes valid track receipts;
- verify every number directly against the frozen evidence bundle;
- ensure every chart names protocol, track, graph fingerprint, model, run count,
  task count, denominator, and runtime boundary;
- render the offline demo and capture static fallbacks;
- scan slides, notes, recordings, terminal frames, and metadata for secrets and
  private paths;
- export PDF and video to two independent devices;
- rehearse the 25-, 35-, and 45-minute cut points;
- accept no benchmark-semantic changes after the technical freeze.

## Source map

- `docs/offensive-ai-con-readiness-plan.md` — current project truth, schedule,
  release gates, results, and conference definition of finished.
- `docs/benchmark-v2-design-rationale.md` — architecture, scoring, incident
  history, V29 boundary, and historical campaign evidence.
- `docs/benchmark-v2-certification-evidence.md` — archive/live certification
  and fingerprint evidence.
- `docs/benchmark-hardening-runbook.md` — failure taxonomy, containment,
  scoring, durability, and interpretation.
- `docs/v2-campaign-supervisor-contract.md` — safe autonomous monitoring,
  recovery, and archival boundary.
- `docs/evidence/provider-hardening-v13-acceptance.json` — public-safe provider
  hardening and Nous canary evidence.
- `docs/offensive-ai-con-offline-demo.md` — offline stage operation, claim
  boundary, rebuild steps, and abort/fallback rules.
- `docs/assets/offensive-ai-con/offline-demo/` — self-contained six-beat demo,
  public evidence receipt, render receipt, and six static fallbacks.
- `docs/assets/offensive-ai-con/` — reproducible historical Direct chart, CSV,
  and evidence manifest bound to all 30 public reports.
