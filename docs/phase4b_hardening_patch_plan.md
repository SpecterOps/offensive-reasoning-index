# ORI Phase 4B/v2 Harness Hardening Patch Plan

Goal: keep strict ORI scoring unchanged while adding attribution, guardrails, and diagnostic-only repair/finalization infrastructure.

## Implemented in this patch set

1. Diagnostic CSV fields
- `failure_stage`
- `evidence_found`
- `evidence_depth_score`
- `reference_entities_seen_count`
- `reference_path_nodes_seen_count`
- `final_answer_contract_valid`
- `invalid_entities`
- `missing_required_entities`
- `finalization_guard_used`
- `repair_turn_used`
- `minimum_evidence_satisfied`
- `successful_tool_results`
- `reasoning_capture_mode`
- `reasoning_token_count`

These are diagnostic-only. `score`, `outcome`, and strict grading behavior are not relaxed.

2. Entity/path pre-grade validation
- Structured answer contracts are validated separately from strict score.
- Invalid entities and missing required entities are recorded.
- Path answer contract validity is recorded, including `path_found` presence and answer type sanity.

3. Optional one-turn repair/finalization plumbing
- MCP finalization guard remains no-tools synthesis near loop budget.
- It is togglable with `ORI_MCP_FINALIZATION_GUARD=0/1` and defaults enabled because it does not leak references or change strict scoring.
- `repair_turn_used` is now represented in metadata/CSV. Actual repair turns must stay config-gated and off by default until a no-reference repair prompt is reviewed.

4. Prompt/reference reconciliation
- Do not change task wording from score artifacts.
- Use `phase4b_failure_attribution.py` common-miss output to identify true wording/reference mismatches.
- Any wording changes must be versioned and rerun as a new task-set version.

5. Query strategy resources/templates
- Add non-answer-leaking strategy notes only. They should describe query patterns, not reference answers or planted nodes.
- Priority families: ADCS/ESC1, unconstrained delegation + sessions, active sessions, source-to-target pathfinding, nested group membership.

6. Diagnostic slice
- The extractor emits `diagnostic-slice-candidates.json` with 20 candidates: ADCS/ESC1, session/delegation, GPT-5.4 MCP regressions, and incomplete/no-path cases.

## Next implementation step

Before running a full benchmark, run a small diagnostic slice with strict scoring unchanged and inspect whether the new fields separate:
- no evidence found
- evidence found but no synthesis
- invalid entity/final-answer contract errors
- direct Cypher syntax/query-cost failures
- MCP over-search/tool churn
