# S1-04B — completed-campaign publication and model-card review

Revision 1, September 5, 2026. Status: bounded audit pass complete; source review,
root reproductions, 63 focused tests and independent full-ledger review passed.
Findings and unresolved coverage obligations remain open. This targets a
substantive source-review gap in Stage 1. It is
not a future scorecard implementation or a whole-repository completion claim.

## Purpose and ownership

The coverage reconciliation identified seven remaining review areas: publication;
provider/native execution; claim/oracle/certificate integrity; archive/live graph
canonicalization; V1 behavior; portable operator lifecycle; and full discovery plus
historical scripts. Existing incremental cleanup, passing tests and historical
diff reviews do not establish complete review of these areas.

Start with publication because it governs whether the eventual conference output
is valid, correctly aggregated and public-safe. Root is accountable for scope and
findings disposition. `p2_dead_code_inventory` owns the read-only source review.
`oaic_stage1_adversarial_fallback` independently reviews this plan and the returned
source/contract/test ledger. Root owns bounded model-free reproductions and docs.
No implementation is authorized by this review plan. Any confirmed fix requires
its own decision-complete plan, adversarial approval and compatibility analysis.

## Source and operation boundary

Use the actual consolidated dirty tree, not HEAD or historical certificates.
Preserve all existing changes, including S1-04A. At audit start capture exact hashes
of all files examined, base commit and the current full implementation inventory.
Record the same hashes at review completion. A changed file requires re-review of
its affected closure; do not silently attach the result to a different source.

Read complete current contents of:

- `src/ori/eval/v2/campaign_runner.py`
- `src/ori/eval/v2/scoring.py`
- `src/ori/eval/v2/model_card.py`
- `scripts/build_v2_model_card.py`

Deep review focuses on report schemas/validators, `_model_report`,
`_track_completion`, `_publish_track_completion`, `_run_prepared_v2_campaign`,
`summarize_results`, `_run_operational_summary`,
`_assert_run_state_matches_report`, `_assert_summary_matches_rows`,
`_load_track_reports`, `_select_model`, `_aggregate`, `_svg_bytes` and
`build_model_card`. Resolve current symbol names rather than trusting old line
numbers. Follow local helper/type/serializer/fingerprint callers and callees only
as required to decide the twelve questions below; list each extra file and the
exact reviewed closure. Reading an imported file does not automatically mean its
entire subsystem is audited. Record unexplored boundaries explicitly.

Read relevant current contract documentation and complete relevant tests for
publication acceptance, scoring dimensions, model cards, status and durability.
Discover exact test paths through repository search; record test IDs and assertions
that prove each obligation rather than inventing names. Existing tests are not
assumed adequate merely because their names resemble a requirement.

No provider/model/MCP/BloodHound calls, graph certification, uploads, private host
changes, signing, remote Git, PR or merge. Reproductions use synthetic local
fixtures and temporary paths under ignored results. Do not execute untrusted
external worktrees or use actual campaign secrets. No dependency installation.

## Twelve required review decisions

1. Verify publication follows a successful exact post-track graph gate on every
   reachable execution, retry, interruption and resumed-completion path.
2. Trace schedule, task, run index, model identity, graph and runtime/source binding
   from candidate/private state through reports and track-completion receipts.
3. Determine whether duplicate, missing or foreign results can influence report
   denominators or be accepted by the model-card reader.
4. Check distinct accounting for infrastructure, harness, proof-insufficient,
   timeout, invalid-output and reasoning outcomes; state exact denominators.
5. Trace durable attempt usage and retry counters, including canceled, recovered
   and exhausted attempts; identify omission and double-count opportunities.
6. Test whether partial writes or one completed track can be mistaken for a
   complete requested two-track campaign. Distinguish legitimate per-track output
   from a complete campaign/model-card claim.
7. Verify model cards cross-check private/public evidence rather than trusting a
   filename, existence check or unbound completion flag.
8. Check exact model/repetition selection and rejection of incompatible mixtures.
9. Trace every JSON/SVG output field, including labels and error strings, for
   private paths, URLs, arbitrary metadata, prompts, answers, queries, tool bodies,
   oracle material and injection/escaping hazards.
10. Verify deterministic output for identical accepted inputs and documented
    atomicity/failure/recovery behavior of writes. Do not require stronger write
    semantics than the current supported contract without classifying the gap.
11. Verify Direct/MCP separation and honest missing/unavailable metrics. Distinguish
    current contract bugs from future OAIC scorecard requirements.
12. Map all preceding decisions to exact positive/negative executable tests.
    Report missing coverage independently from confirmed production defects.

## Evidence and disposition

Deliver one public-safe review document under `docs/plans/` with:

- exact source scope and hashed private evidence reference;
- a compact data-flow map from candidate/state through graph gate, report,
  completion receipt and model card;
- per-function decisions, boundary assumptions and caller/callee notes;
- twelve contract-to-test rows with covered, missing, contradictory or unresolved
  status, exact existing test IDs and reasons;
- ranked findings with trigger, consequence, source location, reproduction status
  and recommended narrow ownership; no raw private payload in the document;
- explicit retain/dead/duplicate/unresolved classification for any cleanup lead;
- follow-up implementation scopes separated from future protocol/scorecard work.

Root validates high-impact findings with a bounded model-free reproduction where
possible and challenges unsupported claims. Reproduction scripts and complete
outputs remain ignored/private; use apply_patch to author them. Run only relevant
existing tests needed to establish the reviewed contract, record exact commands,
counts and final source hashes. A passing test does not invalidate a counterexample
outside its assertions. No-finding outcomes are acceptable with complete evidence.

The separate reviewer reads the returned ledger, all cited source closures and
reproductions before approving it. If scope is too large for a reliable pass,
split by one of the twelve obligations without dropping any from the parent plan.
Keep unreviewed rows open. Root updates the cleanup/progress ledgers only after
reconciling findings and independent review; no whole-P2 completion declaration.

## Stop conditions

Stop the affected probe on any required real service/provider access, mixed source
inventory, private evidence exposure, unsupported dependency or state mutation.
Continue other read-only questions where independent. Unresolved source semantics
remain findings for investigation, not permission to change behavior. Remote and
local-model setup deferrals do not block this model-free review.
