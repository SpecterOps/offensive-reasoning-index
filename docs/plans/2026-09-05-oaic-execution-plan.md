# ORI Offensive AI Con execution contract

Approved for implementation on 2026-09-05. This is the canonical engineering
contract for the pre-conference program. The progress ledger records evidence;
this plan defines requirements, not completed work.

## Objective and dates

Deliver reviewed/consolidated code, a deterministic certified 100-task release,
three native BloodHound MCP implementations, six hosted models plus local Qwen,
independent score dimensions, bounded campaign operations, public-safe evidence,
an offline conference package, and a vendor-neutral OpenGraph framework design.

- Implementation and evidence freeze: 2026-09-26.
- Technical/package freeze: 2026-09-29.
- Conference: 2026-10-05.
- Canonical progress branch: `codex/oaic-codebase-consolidation`.
- Publication baseline: reviewed code merged into `master`.
- Hosted ceiling: USD 2,000 including canaries.
- Main release: 50 Direct plus 50 MCP tasks; never combine track scores.
- Local development: frozen Qwen3.8-27B inference endpoint provisioned externally.

V30 qualification is a hard prerequisite for dependent implementation. Historical
V29 evidence does not certify new code. Deadline arrival, passing tests, an open
PR, or partial results do not mean the complete program is finished.

## Execution and ownership

ORI is a portable benchmark, not a machine or model-service manager. It must
run without a personal agent installation, private hostname, workstation layout,
GPU reservation command or service-control integration. Operators provision
inference and BloodHound independently; ORI consumes explicit endpoint/model,
credential-source and artifact configuration. Local and remote inference use
the same supported provider interfaces. No machine-specific provisioning hook
is an ORI readiness, installation, execution or reporting prerequisite.
Controlled-host acceptance may run on any declared machine satisfying the
documented requirements; no historical operator machine is required.
Both an interactive operator and an external automation agent use the same
documented CLI. External automation may invoke ORI locally or on a configured
execution host; transport and process supervision remain outside the benchmark.
Preserve config-file inputs, meaningful exit codes, no-provider readiness,
redacted `campaign-status --json`, durable results, exclusive campaign locks,
and fingerprint-checked resume. No agent-specific API or alternate scoring path.

Root owns integration, shared schemas, gates and evidence reconciliation. Assign
non-overlapping compiler/scorer, runtime/MCP, and operations/reporting work to
specialists; independent reviewers cannot approve their own implementation.
Use one primary execution task and repository checkpoints, not competing loops.

At every resume: read this plan and the ledger, check branch/dirty state and
running processes, recover existing work, then execute the first ready package.
Record source commit, dependencies, owner, validation, review, blocker and next
action. Continue independent authorized work while another lane needs input.

Routine edits, diagnostics, tests, signed branch commits, branch pushes and PR
preparation are within the approved implementation. Honor protected-branch rules
and exact-target graph-replacement approval. Present any required paid approval
against a frozen manifest and estimate. Never repeatedly request the same approval.

## P0: progress and mobile input (September 5-6)

- P0-N01: pair the existing desktop task with ChatGPT Remote on the phone. Use
  the same account/workspace, native Connections settings, OS notification
  permission and an awake/online host. The user owns QR/MFA/passkey interaction.
- P0-N02: issue one harmless native input request with the phone backgrounded;
  record confirmation that the alert arrived and a phone reply reached this task.
  Do not infer phone delivery from a desktop event.
- P0-W01: maintain this plan, the progress/findings ledger and private receipts.
- P0-W02: notify only for a material decision/action or finished goal. Include
  package ID, evidence, recommended action, alternatives and unaffected work.
  Do not send routine test/commit/retry notifications or invent a push service.

If mobile delivery is unavailable, keep engineering moving and mark that gate
blocked. Email, SMS or another service is not an automatic substitute.

## P1: consolidation and V30 qualification (September 5-7)

- P1-W01: refresh local/remote/worktree/PR inventories; record exact commits and
  uncommitted artifacts. Preserve originals and keep private output out of Git.
- P1-W02: create a clean worktree from remote master, integrate current V30
  lineage, compare every remaining branch by ancestry and patch equivalence,
  incorporate missing applicable changes once, and record exclusions/supersession.
  Never replay obsolete dependency upgrades or delete source branches.
- P1-W03: independently review scoring/output compliance, non-finite JSON,
  schema retry, task deadlines/cancellation, retry classification and persistent
  budgets, recovery accounting, public/private reconciliation, locks and durability.
- P1-W04: controlled-host, zero-provider acceptance for duplicate launch,
  stale/active state, interrupted readiness/execution with fake transports,
  exact resume, monotonic attempts, cooldown persistence, circuit containment,
  corrupt evidence, graph drift, publication refusal, reattachment and archival.
- P1-W05: submit the baseline PR with integration ledger and validation; obtain
  required review/merge and bind acceptance to the merged source boundary.

Gate: full validation, independent review and controlled-host acceptance pass;
the V30 baseline is merged. Downstream cleanup/features cannot bypass this gate.

## P2: review, cleanup and optimization (September 7-10)

- P2-W01: inventory package/CLI/provider/MCP/generator/compiler/scorer/certifier/
  runner/status/supervisor/reporting/script/test/dependency/config surfaces and callers.
- P2-W02: capture behavior contracts: CLI arguments/defaults/exits, config
  acceptance, imports, artifacts, semantic identities, scores/failures, reports,
  resume rules, credential routing and historical reproduction.
- P2-W03: remove only proven unreachable code or equivalent duplicated behavior.
  Check dynamic imports, configuration dispatch, exports and external helpers.
  Legacy V1 reporting and shared provider/MCP runtime remain supported.
- P2-W04: separate transport/execution/projection/reporting responsibilities,
  consolidate identical validation/accounting, split oversized modules coherently,
  preserve import compatibility and remove dependencies only after caller checks.
- P2-W05: target fewer than 700 collected baseline tests through shared immutable
  setup and coherent scenarios, preserving every regression obligation and named
  subcase failures. Record logical scenario count too; do not hide cases in loops
  merely to game count. Preserve essential coverage if the target conflicts.
- P2-W06: profile generation, archive reconstruction, certification, projection,
  reports, tests and peak memory. Compare one warmup/five measurements on the same
  environment. Retain only repeatable improvements with equivalent behavior.
- P2-W07: independent review, differential checks, full pytest/Ruff/lock/diff and
  secret checks; merge cleanup before dependent implementation.
- P2-W08: qualify public installation on clean Linux and macOS machines using
  documented dependencies only, with no private agent files or operator tools.
  Exercise arbitrary checkout/config/output paths (including spaces), explicit
  local and remote inference URLs, and a controlled BloodHound endpoint. Run
  model-free config/credential-routing tests plus no-provider readiness, using
  offline doubles for deterministic tests. Verify missing credentials/endpoints
  produce actionable typed failures, never discovery of private installations.
  Test executable campaign behavior with fake transports before model admission.
  Qualify both direct terminal invocation and a generic external CLI driver:
  readiness without `--execute`, explicit execution admission, status polling,
  duplicate-launch rejection, interruption and exact-config resume. Driver tests
  must not import or install a particular personal agent. Preserve the existing
  supervisor CLI as optional tooling, not a prerequisite for standalone use.
  Record exact OS/Python/dependency versions and receipts. Do not claim Windows
  support without separate qualification of POSIX lock/signal dependencies.
  Public examples use loopback or reserved placeholder domains; preserve legacy
  config parsing and privately held operator configs when sanitizing examples.

Source refactors may invalidate fingerprints despite equivalent behavior. Update
coverage for moved modules; regenerate evidence instead of weakening fingerprints.

## P3: local Qwen qualification (September 7-9)

- P3-W01: obtain an operator-provisioned endpoint, exact served model ID and
  deployment metadata. Provisioning and any scheduling remain outside ORI.
  Do not add reservation, SSH, agent, model-loading or service-management code.
- P3-W02: validate explicit endpoint configuration and protocol compatibility;
  missing or unavailable inference is an endpoint prerequisite, not a requirement
  to install an orchestration service. No provider calls during readiness.
- P3-W03: record operator-supplied model repository/revision/files/sizes/SHA-256, quantization,
  tokenizer/template, llama.cpp and llama-swap revisions, context/output reserve,
  KV/GPU placement, slots/batches/cache, sampler/seed/thinking and launch controls.
- P3-W04: verify fit, actual placement, no truncation, native calls/arguments,
  dependent turns, schema finalization, cancellation/accounting and restart.
- P3-W05: close ORI-owned client connections and checkpoint artifacts. Do not
  stop, unload, reserve or release externally managed model services.

All model-in-the-loop development uses this profile; material changes invalidate
affected qualification. Deterministic unit/compiler/scorer tests remain model-free.

## P4: task pool and release selection (September 10-14)

- P4-W01: version the new `oaic-2026-v1` release. Preserve simple smoke and
  historical products. Conference seed 67; robustness seeds 4401 and 4402.
- P4-W02: certify at least 60 main semantic-unique contracts per track plus ten
  reserved diagnostics per track (two per claim kind). Diagnostics are excluded
  from main selection across seeds by recipe and semantic identity.

| Claim | Main eligible minimum per track | Selected per track |
| --- | ---: | ---: |
| set | 12 | 10 |
| count | 10 | 8 |
| route | 24 | 20 |
| decision | 7 | 6 |
| bounded absence | 7 | 6 |
| total | 60 | 50 |

- P4-W03: recipes declare stable identity, graph template, tracks/exclusions,
  claim/family/tier/concentration, eligibility, public selectors/acceptance/bounds,
  oracle builder and fixtures. Extend existing supported AD semantics first.
- P4-W04: certify positive, missing/extra/disconnected evidence, wrong type/
  direction/endpoint/property, incomplete pages/counts, false negative proof,
  malformed answer, ambiguous identity, valid alternatives and native projections.
  Unknown recipes/templates/fixtures/exemptions fail closed.
- P4-W05: add ReleaseSelectionSpec, SelectedReleaseReceipt and `ori select-v2`.
  Deduplicate certified public semantics, exclude diagnostics, order within each
  quota by canonical SHA-256(selector version, product, seed, track, recipe,
  variant), break ties by identity and select without replacement. Reject deficient
  quotas. Bind IDs/source/catalog/policy to the receipt. Never select by model
  scores, hidden difficulty or oracle performance.
- P4-W06: test repeatability, catalog reorder invariance, graph and logical-subset
  seed variation, disjointness, no duplicate inflation and exact 50/50 scheduling.
  Finite selection space does not promise a unique subset for every possible seed.

## P5: native MCP and providers (September 10-16)

- P5-W01: immutable profiles for mwnickerson/bloodhound_mcp,
  MorDavid/BloodHound-MCP-AI and armadin-public/bloodhound-mcp-server. Record full
  revisions, runtimes, launchers, backend, credential-source names, native
  discovery hashes, admission policy, projector and capability fingerprint.
- P5-W02: prove equivalent canonical synthetic graph facts through each actual
  backend; reachability alone is insufficient.
- P5-W03: preserve native tool names/descriptions/schemas/results, prompts and
  resources. Standard MCP client operations are identified as protocol plumbing.
  Record missing surfaces as unavailable; no emulation or normalized facade.
- P5-W04: exclude mutators, constrain mixed operations at dispatch, restrict
  backend credentials, bound execution and classify policy rejection. Refuse
  profiles that cannot be safely constrained; do not silently patch upstream.
- P5-W05: one projector per implementation:
  project(native_receipt, public_claim, capability_profile) -> EvidenceIR,
  proof_status, diagnostics. Use only mechanical/certified native evidence;
  unknown/truncated/ambiguous results are inconclusive. No oracle repair or hidden
  queries. Native high-level tools may prove claims when their receipts suffice.
- P5-W06: add Anthropic Messages native tool loop, protocol-only schema mapping,
  tool results, usage/streaming, cancellation, typed errors and schema retry.
- P5-W07: exercise all three profiles with frozen local Qwen before hosted anchor
  admission; include failures, truncation, resources, finalization and redaction.

## P6: scorecard, telemetry and budget (September 10-16)

- P6-W01: keep Direct/MCP separate, local visually separate; accuracy gates rank.
  C/W=comparator correct/incorrect; M=model failure without verdict; P=proof
  insufficient; X/H/U=infra/harness/unexecuted; S=C+W+M+P+X+H+U.
  Effective=C/S; reasoning=C/(C+W); proof completion=(C+W)/S; output compliance
  uses observed outputs. Zero denominators are unavailable.
  Partial diagnostics: identity P/R/F1 for sets, required typed witness coverage
  for routes, required-fact coverage for properties; scalar/absence exact-only.
  Partial never upgrades exact rank.
  Speed: task/provider time, observed TTFT, tool/resource latency, p50/p95,
  throughput and timeouts; setup overhead separate.
  Efficiency: token usage for input, output, reasoning and cache; costs labeled
  as actual, estimated or unavailable;
  cost/task, tokens/correct and cost/correct. Include failed/retried attempts.
  MCP tool use: success/failure, Cypher/high-level, valid args, policy rejection,
  duplicates and evidence contribution. Resources: availability/reads/failures/
  duplicates/evidence contribution. Both dimensions N/A for Direct.
- P6-W02: versioned provider/tool/prompt/resource/retry/evidence/task/budget events
  with stable IDs, monotonic timing and private raw receipts; reconcile public data.
- P6-W03: preserve native usage, avoid reasoning/cache double count, retain partial
  cancellation usage and missing values; no authoritative character-count estimate.
- P6-W04: durable campaign-wide USD 2,000 ceiling. Reserve up to USD 200 canaries.
  Admission requires incurred plus conservative remaining estimate <= USD 1,600.
  Before every request atomically reserve its maximum bounded charge; reconcile
  afterward. Unknown usage retains reservation. Restart cannot reset accounting.
  Block unverifiable pricing/unbounded cost; distinguish subscription usage.
- P6-W05: test boundary budgets, racing reservations, crashes, duplicate settlement,
  unknown usage, retry/restart, rate mismatch, missing fields, zero success,
  accounting disagreement and tampering.

## P7: integrated qualification and model lock (September 16-18)

- P7-W01: resolve by September 17 exact IDs for Qwen/DeepSeek/Kimi/GLM flagships,
  GPT-6 Astra and Claude Opus 5. Prefer official APIs, record alternate serving
  routes. Astra fallback is GPT-5.6 Sol after qualification. Missing required
  models remain typed unavailable cells. Local Qwen is the seventh baseline.
- P7-W02: freeze reasoning/sampler/output/context/loop/schema/stream/retry controls;
  do not claim cross-vendor effort equivalence or tune during the final campaign.
- P7-W03: full deterministic tests, generation/archive, compile/certify/select,
  exact live graph and per-profile certification, redaction/reconciliation and
  supervisor faults. Assert readiness makes zero provider calls.
- P7-W04: independent release review/merge; regenerate source-bound receipts on
  merged code. No publishable run uses unmerged code.
- P7-W05: bounded one-Direct/one-MCP provider canaries on diagnostics; charge all
  attempts. Fix through deterministic/local coverage, merge and repeat affected
  canary only. Canaries establish interoperability, not model ranking.
- P7-W06: anchor canary is ten identical reserved MCP contracts for Astra/fallback
  and Opus on the main implementation. Select valid candidate by exact effective
  accuracy, proof completion, comparable cost, duration, stable ID (in that order).
  Then bounded diagnostics on alternate MCPs. Never select using final results.
- P7-W07: freeze admission manifest: merged commit/models/release/graph/MCP/local
  profiles/repetitions/sample counts/estimate/exposure/runtime/canaries/readiness/
  recovery. Any required approval binds this exact manifest.

## P8: campaign (September 18-25)

- P8-W01: per pass seven models x 50 Direct, seven x 50 main MCP, anchor x 50 x
  two alternate MCP = 800 samples (700 hosted). Three passes = 2,400 (2,100 hosted),
  plus canaries. Main MCP is mwnickerson for every model.
- P8-W02: complete one balanced pass first; admit passes two and three only when
  both fit cost/time. Partial extras are supplemental, never an unbalanced primary
  comparison. Reduce three to one before reducing breadth; otherwise report infeasible.
- P8-W03: serial graph access, frozen deterministic rotations, fresh conversations,
  one selected release, declared cache/warmup policy. Reuse main-anchor results only
  when settings, fingerprints and repetition identity exactly match.
- P8-W04: exact graph before/between/after cells and before publication. A failed
  post-gate invalidates the cell; repairing the graph cannot retroactively certify it.
- P8-W05: exclusive locks, atomic checkpoints, monotonic attempts, bounded retry,
  persistent cooldown, typed status/resume, circuit containment and completion receipts.
- P8-W06: rank only cells with full accounting, compatible evidence, passing graph
  gates and no unresolved X/H/U. Model errors/P lower accuracy without invalidating
  an otherwise operationally valid cell. Preserve typed invalid/unavailable cells.

## P9: evidence freeze (September 26)

- P9-W01: reconcile IDs/denominators/repeats/attempts/usage/cost/graph/runtime/labels.
- P9-W02: every public claim references source/release/track/profile/model/repeats,
  artifact and limitations. Never combine historical protocols into one ranking.
- P9-W03: allowlist exports; exclude secrets, private paths/endpoints, prompts,
  answers, queries, arguments/bodies/transcripts and sealed oracle content. Scan
  documents/JSON/images/archives/metadata/staged history.
- P9-W04: signed artifact manifest and verified private backup. After freeze no
  semantic/model/task/metric reselection. Defects withdraw claims, not replace results.

## P10: offline package (September 19-29)

- P10-W01: copy-pasteable setup/configure-endpoint/generate/certify/select/readiness/budget/
  execute/status/resume/stop/export/read-results runbook.
- P10-W02: separate hosted Direct/main MCP, native-MCP comparison and local charts;
  display validity/denominators/repeats/missing telemetry/uncertainty; no composite.
- P10-W03: extend existing network-free HTML/static demo with approved aggregates;
  no live execution buttons; immediate static and recorded fallback.
- P10-W04: deck/notes/PDF/HTML/images/video/Q&A and 25/35/45-minute cuts; organizer
  constraints, timed/full/short/fallback/second-machine rehearsals, privacy review.
- P10-W05: September 29 hash freeze and two verified device copies. Necessary
  package correction gets a revision and QA, never new benchmark evidence.

## P11: delivery (September 30-October 5)

Rehearse, verify playback/power/display/offline availability and limitations.
Present frozen package with immediate fallback, no on-stage infrastructure debugging.
The physical presentation is user-owned; agent verifies engineering readiness.

## P12: OpenGraph design (September 10-24)

- P12-W01: vendor-neutral manifest/namespace/version/dependencies/schema/identity/
  relationships/recipes/fixtures/projector/certifier/content fingerprints.
- P12-W02: define register, validate, seeded generate, claim compile, oracle build,
  evidence project and certify interfaces with typed inputs/outputs/errors/ownership.
- P12-W03: stable namespaced identity, aliases, collisions, typed properties,
  directed endpoints, direct/derived semantics and derivation provenance.
- P12-W04: public contracts, isolated oracle, claim/EvidenceIR strategy,
  completeness, fixtures, native projections, certification and selection.
- P12-W05: major/minor/patch examples; invalidate appropriate generated/claim/oracle/
  capability/certification/readiness/resume/report boundaries.
- P12-W06: two abstract domains and end-to-end worked examples, independent
  architecture/domain review. No hidden requirements or unknown namespaces.

Pre-conference: reviewed design/specification/examples. Post-conference only:
executable loading, vendor/GitHub plugins and AD migration.

## Recovery, final acceptance and user input

Stop dependent work for unqualified V30, graph drift, stale evidence, unknown
semantics, unsafe MCP, invented proof, readiness provider calls, unbounded spend,
corrupt/unsupported resume, private/public mismatch or leaks. Preserve receipts,
classify, fix narrowly, test locally, review/merge, regenerate affected artifacts,
use fresh roots where needed, reassess cost/time, then resume.

Continue unaffected work for host/PR/credential/mobile blockers. Deadline fallback
uses valid existing evidence and an explicit unmet-requirements list; it does not
complete the full goal. Never erase failed attempts.

Final handoff: remote branch/PRs/merged commit, integration/findings, cleanup and
test/performance results, local/MCP/model profiles, pool/selection/certification,
validity per cell, independent scorecards, reconciled spend, public/private
manifests/backups, OpenGraph design, offline assets/rehearsals, notification test
and remaining limitations. Complete only when every required engineering gate has
evidence; the physical conference presentation remains separately user-owned.
