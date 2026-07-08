# ORI Benchmark Product Split Plan

Date: 2026-07-07
Status: planning draft
Owner lane: Dogwalker / ORI evaluation

## Core decision

ORI should not keep accreting phases as if every phase is a separate product. The public-facing benchmark shape should become simpler:

1. Simple benchmark
   - Easier, stable, fast to run.
   - Based around the Phase 3 family of tasks and benchmark surfaces.
   - Useful for smoke testing, model triage, local runs, and broad user adoption.

2. Complex benchmark
   - The end result of the Phase 4 work.
   - A single new Phase 4 profile that includes the realistic forest, richer attack-path families, decoys, negative controls, mechanism evidence, and what would otherwise have been Phase 4C/Tier 6.
   - Useful for serious model comparison and frontier-capability measurement.

The important move: do not expose Phase 4B and Phase 4C as confusing separate end-user benchmark products. Internally, they can remain implementation milestones. Externally and operationally, they should converge into one coherent complex benchmark profile.

## Recommended public/operator model

### Simple benchmark

Working name:

- `simple`
- `phase3_simple`
- `ori-simple`

Purpose:

- Fast baseline.
- Lower operational friction.
- Easier to explain.
- Suitable for new users and quick model smoke tests.
- Should fit local or modest model workflows.

Likely source material:

- Phase 3A direct-Cypher style tasks.
- Phase 3B tools-only MCP tasks.
- Possibly a small resources-enabled subset if it does not make setup confusing.

Characteristics:

- Smaller graph.
- Fewer domains.
- Less decoy density.
- Shorter chains.
- Fewer negative controls.
- Clear source-to-target pathfinding.
- Minimal scoring contract complexity.
- Short runtime target.

Suggested shape:

- 20–40 tasks.
- Direct and MCP variants where practical.
- One known-good dataset/profile family.
- One command to generate/provision/check/run.
- Small generated domain target: roughly 100 users, 40 workstations, and 15 servers.
- The seed should control full benchmark identity: exact size within the simple range, company/domain names, usernames, hostnames, group names, planted attack paths, and evaluated questions.

### Complex benchmark

Working name:

- `complex`
- `phase4_complex`
- `ori-complex`

Purpose:

- Serious benchmark for advanced model/tool reasoning.
- The main end-state of the Phase 4 work.
- Preserves Phase 4B/v2 realism and adds the Tier 6/Phase 4C ceiling layer inside one coherent profile.

Likely source material:

- Phase 4B/v2 realistic parent/child forest.
- Phase 4B alias-equivalence scoring and diagnostic hardening.
- Phase 4C/Tier 6 attacker-decision complexity.

Characteristics:

- Parent/child forest, e.g. `CORP.LOCAL` + `NA.CORP.LOCAL`.
- Realistic enterprise noise.
- Cross-domain paths.
- ADCS, delegation, sessions, ACLs, group nesting, and composite chains.
- Decoys and negative controls.
- Mechanism-aware answers.
- Adaptive/contingency tasks.
- Failure subtype diagnostics.

Suggested shape:

- Preview/diagnostic mode: 24–30 tasks.
- Official mode: 100 tasks.
- Same generated profile family, different task-set selectors.
- One canonical profile name for the complex benchmark rather than exposing Phase 4B/4C as separate user concepts.
- Large generated domain target: roughly 5,000 users, 2,000 workstations, and 500 servers.
- The seed should control full benchmark identity: exact size within the complex range, company/domain names, usernames, hostnames, group names, OU/business-unit labels, service-account names, synthetic enterprise flavor, planted attack paths, decoys, negative controls, and evaluated questions.

## Phase naming recommendation

Use phases internally, but collapse user-facing benchmark names.

Internal implementation language:

- Phase 3: simple benchmark source / baseline surfaces.
- Phase 4A: advanced scenario prototype / earlier v1 work.
- Phase 4B: realistic forest and 100-task matrix.
- Phase 4C: highest-tier decision complexity and ceiling tasks.

User-facing / run-facing language:

- Simple benchmark.
- Complex benchmark.

This makes ORI much easier to explain:

> ORI ships with a simple benchmark for quick baseline runs and a complex benchmark for realistic enterprise attack-path reasoning.

That is cleaner than asking users to understand Phase 3A vs Phase 3B vs Phase 3C vs Phase 4B/v2 vs Phase 4C before they can run anything.

## Desired run experience

The project should converge toward simple commands like:

```bash
ori benchmark run simple --mode mcp --model <model-alias>
ori benchmark run complex --mode mcp --model <model-alias>
```

or, if keeping current config-driven mechanics underneath:

```bash
ori run --benchmark simple --profile mcp --model <model-alias>
ori run --benchmark complex --profile mcp --model <model-alias>
```

The implementation can still generate configs internally, but the operator/user should not need to hand-edit multiple run-config YAML files for standard benchmark runs.

## Benchmark packaging concept

### Benchmark definition object

Introduce a benchmark definition layer that maps simple names to actual config pieces.

Example conceptual shape:

```yaml
benchmarks:
  simple:
    graph_profile: phase3_simple
    task_set: phase3_simple_official
    supported_modes: [direct, mcp]
    default_task_count: 40
    expected_runtime: short
    scoring_profile: standard
    generated_scale_target:
      users: 100
      workstations: 40
      servers: 15
    seed_controls: [exact_size, company_name, domain_name, usernames, hostnames, groups, planted_paths, evaluated_questions]

  complex:
    graph_profile: phase4_complex
    task_set: phase4_complex_official
    diagnostic_task_set: phase4_complex_diagnostic
    supported_modes: [direct, mcp]
    default_task_count: 100
    diagnostic_task_count: 24
    expected_runtime: long
    scoring_profile: mechanism_decision_complex
    generated_scale_target:
      users: 5000
      workstations: 2000
      servers: 500
    seed_controls: [exact_size, company_name, domain_name, usernames, hostnames, groups, org_units, service_accounts, enterprise_flavor, planted_paths, decoys, negative_controls, evaluated_questions]
```

### Run modes

Recommended modes:

- `direct`: direct Cypher answer generation.
- `mcp`: BloodHound MCP tool-use benchmark.
- `mock`: mock/perfect, mock/wrong, mock/empty validation gates.
- `diagnostic`: smaller task set for the complex benchmark.

Avoid exposing too many old internal variants as first-class user choices.

## Complex benchmark task architecture

The complex benchmark should include the existing Phase 4B/v2 foundations plus the new highest-tier decision tasks.

Suggested official 100-task split:

- 4 startup/usability tasks
- 24 standard realistic pathfinding tasks
- 20 ADCS/delegation/session/ACL mechanism tasks
- 20 decoy and negative-control tasks
- 24 attacker-decision complexity tasks
- 8 cross-domain / composite Tier 0 tasks

Alternative if keeping the current Phase 4B/v2 matrix intact:

- Preserve the existing 100-task Phase 4B/v2 as `complex-v1` internally.
- Build the new consolidated complex profile as `complex-v2` with the harder tasks folded in.
- Public docs call it simply `complex` once stable.

## Highest-tier task design inside complex benchmark

The former Phase 4C/Tier 6 work should become the highest-difficulty band inside the complex benchmark.

It should test all four attacker-decision pressures:

1. Path composition
   - Multiple abuse primitives chained into one route to Tier 0.

2. Path selection
   - Multiple plausible routes, only one viable or optimal.

3. Operational sequencing
   - Correct identity/privilege transitions must happen in order.

4. Adaptive contingency
   - Model must re-plan when a session/template/host/path assumption is invalidated.

These should not be a separate public benchmark unless later evidence shows they need their own product surface.

## Scoring model

Simple benchmark:

- Standard strict correctness.
- Parse fail / hallucination / infra error separation.
- Minimal diagnostic subtypes.

Complex benchmark:

- Strict headline correctness remains binary.
- Diagnostic subtype columns explain failure.
- Alias-equivalence object matching is required.
- Mechanism and sequence requirements apply only to tasks that declare them.
- Negative controls require explicit proof/reason, not just empty answer.

Complex failure subtypes should include:

- `INCOMPLETE_PATH`
- `WRONG_BRIDGE_OBJECT`
- `WRONG_MECHANISM`
- `DECOY_ACCEPTED`
- `WRONG_PATH_SELECTED`
- `SEQUENCE_ERROR`
- `MISSING_IDENTITY_TRANSITION`
- `INVALIDATED_EDGE_USED`
- `NEGATIVE_CONTROL_FALSE_POSITIVE`
- `INSUFFICIENT_EVIDENCE`
- `TARGET_MISMATCH`

## Simplification work needed

### 1. Benchmark registry

Add a registry that lets ORI understand `simple` and `complex` as named benchmark products.

Acceptance:

- CLI can list available benchmarks.
- CLI can describe each benchmark.
- CLI can map benchmark name + mode to graph profile, task set, scoring profile, and config defaults.

### 2. Standard run command

Add a simpler run path over the current config-driven internals.

Acceptance:

- User can run one command for simple benchmark.
- User can run one command for complex diagnostic.
- User can run one command for complex official.
- Generated config path is still preserved for reproducibility.

### 3. Dataset/provisioning preflight wrapper

Make BloodHound setup less bespoke.

Acceptance:

- Generate dataset.
- Verify or provision BloodHound.
- Validate ingest against manifest.
- Refuse MCP run if ingest does not match.
- Preserve exact artifact paths and hashes.

### 4. Mock validation gates

Make mock/perfect, mock/wrong, mock/empty, and mock/decoy first-class benchmark gates.

Acceptance:

- Complex benchmark cannot be considered ready if mock gates fail.
- Mock gates report task/scorer issues before model spend.

### 5. Documentation split

Public docs should explain:

- What is ORI?
- What is the simple benchmark?
- What is the complex benchmark?
- How do I run each?
- How do I interpret results?

Internal docs can preserve phase history.

## Recommended next implementation plan

1. Rename the planning target from “Phase 4C/Tier 6 diagnostic” to “complex benchmark highest-tier tasks.”
2. Draft the benchmark registry schema for `simple` and `complex`.
3. Decide whether the current Phase 4B/v2 100-task set becomes:
   - the first `complex` official benchmark, with higher-tier tasks added later, or
   - historical `complex-v1`, while the consolidated harder profile becomes `complex-v2`.
4. Design the new Phase 4 profile as `phase4_complex` rather than `phase4b`/`phase4c` split products.
5. Keep the 24-task decision-complexity set as the first diagnostic slice inside `phase4_complex`.
6. Build simple CLI affordances around the existing run-config engine before broad public release.

## Open decisions

1. Should `complex` initially point to the existing Phase 4B/v2 100-task baseline, then evolve, or should `complex` wait until the new harder Phase 4 profile is ready?
2. Should simple benchmark include both direct and MCP modes from day one, or should it start MCP-only for clarity?
3. Should complex benchmark official mode be exactly 100 tasks, or should we allow a larger end-state if the harder bands need room?
4. Should the public CLI expose phase names at all, or only benchmark names?

## Current recommendation

Use benchmark names publicly and phase names internally.

Build:

- `simple`: Phase 3-derived, quick, stable, friendly.
- `complex`: one new Phase 4 profile that absorbs Phase 4B realism and Phase 4C difficulty.

Keep the first complex-hardening step small: a 24-task diagnostic slice inside the new Phase 4 complex profile. Once it separates models cleanly and passes mock gates, expand the complex benchmark to the official task count.
