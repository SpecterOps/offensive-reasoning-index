# Offensive Reasoning Index

Offensive Reasoning Index (ORI) is a benchmark harness for evaluating how well
models reason over BloodHound CE Active Directory attack-path data. It generates
synthetic graph scenarios, runs direct Cypher and BloodHound MCP evaluations, and
produces scored result artifacts that separate reasoning quality from tool,
infrastructure, and timeout failures.

The current repository workflow is config-driven through:

```bash
uv run ori run --config <run-config.yaml> --profile <profile-name>
```

## Current Status

The active project state is Phase 4 validation / diagnostic follow-up:

- Phase 4 v1 advanced AD scenarios exist.
- The current canonical config is `run-config-phase4-v1.yaml`.
- The current diagnostic focus is the ADCS/control-loop slice:
  - `t4_adcs_esc1-01`
  - `t5_adcs_to_delegation_composite-01`
  - `t2_acl_chain-02`
- The next broader roadmap step is a Phase 4B/v2 advanced synthetic generation
  pass before Phase 5 BloodHound MCP comparison design.

The older phase list in GitHub issues is historical roadmap context; prefer the
current run configs and runbooks when they disagree.

## Repository Layout

```text
src/ori/                         Python package and CLI
docs/phase4-v1-runbook.md        Phase 4 v1 operator runbook
docs/benchmark-hardening-runbook.md
                                 Scoring/preflight/failure-taxonomy notes
examples/inference/              Sanitized inference-provider config templates
docs/openai-compatible-model-examples.yaml
                                 Legacy disabled provider-profile examples
run-config-phase4-v1.yaml        Current Phase 4 v1 config
run-config-phase3-m4.yaml        Earlier M4 Phase 3 config
models.yaml / models-m4.yaml     Local model lineups
results/                         Local result artifacts; do not assume complete
```

## Setup

Requirements:

- Python 3.11+
- `uv`
- BloodHound CE for live ingest/eval runs
- local `bloodhound-mcp` checkout for MCP-mode evals
- provider credentials or local models for non-smoke benchmark campaigns

Install/sync dependencies:

```bash
uv sync
```

Run basic validation:

```bash
uv run pytest
uv run ruff check src tests
```

## Current Runbooks

- [Phase 4 v1 Runbook](docs/phase4-v1-runbook.md): generate the
  Phase 4 benchmark dataset, hand it to BloodHound CE ingest, verify ingest, and
  run direct Cypher plus full MCP evaluations from `run-config-phase4-v1.yaml`.
- [Benchmark Hardening Runbook](docs/benchmark-hardening-runbook.md): offline
  answer scoring, task/scorer preflight checks, failure taxonomy, and reporting
  metrics.
- [Inference Config Examples](examples/inference/): sanitized templates for
  Ollama, generic OpenAI-compatible endpoints, llama.cpp, vLLM, LM Studio,
  OpenRouter, NVIDIA NIM, and BloodHound MCP environment wiring. Copy these to
  local run configs and replace placeholders; keep real inference endpoints,
  private model aliases, API keys, and local paths out of public commits.
- [OpenAI-Compatible Model Examples](docs/openai-compatible-model-examples.yaml):
  legacy disabled profile examples for Ollama OpenAI compat, llama.cpp, MLX,
  vLLM, LM Studio, OpenRouter, and NVIDIA NIM.

## Common Commands

List CLI commands:

```bash
uv run ori --help
```

Run tests and lint:

```bash
uv run pytest
uv run ruff check src tests
```

Generate the Phase 4 v1 dataset:

```bash
uv run ori run --config run-config-phase4-v1.yaml --profile phase4_v1
```

Verify BloodHound CE is reachable before a live run:

```bash
uv run ori verify-bh-health
```

After uploading the generated BloodHound zip, verify ingest:

```bash
uv run ori verify-ingest --manifest datasets/phase4-v1_manifest.json
```

Run the direct Cypher Phase 4 profile:

```bash
uv run ori run --config run-config-phase4-v1.yaml --profile phase4_v1_direct_best_stage3
```

Run the full BloodHound MCP Phase 4 profile:

```bash
uv run ori run --config run-config-phase4-v1.yaml --profile phase4_v1_full_mcp_best_stage3
```

Run scorer/task preflight checks:

```bash
uv run ori preflight-tasks \
  --manifest datasets/phase4-v1_manifest.json \
  --track mcp \
  --output results/phase4-v1/preflight-mcp.json
```

Score structured answers offline without launching a model campaign:

```bash
uv run ori score-answers \
  --manifest datasets/phase4-v1_manifest.json \
  --answers answers.json \
  --track mcp \
  --output scorer_projection.json
```

Run all enabled profiles in a config:

```bash
uv run ori run --config run-config-phase3-m4.yaml --run-all-profiles --keep-going
```

## Operating Model

Run ORI as a standalone benchmark harness. A typical workflow is:

1. Generate or select a benchmark dataset.
2. Upload the generated SharpHound-compatible zip into a controlled BloodHound CE
   environment.
3. Verify ingest with `ori verify-ingest` before grading live MCP runs.
4. Run the direct Cypher and/or BloodHound MCP profiles from a checked-in config.
5. Preserve CSV outputs and report metadata so model quality can be separated
   from tool, timeout, and infrastructure failures.

Operational rules:

1. Do not run destructive BloodHound or infrastructure changes without explicit
   approval from the environment owner.
2. Do not treat generated datasets as ingested until `ori verify-ingest` passes.
3. Keep benchmark reports and CSV artifacts with enough metadata to reproduce the
   run surface.
4. Preserve report metadata that explains the run surface, especially:
   `server_prompt_name`, `available_prompt_names`, `prompt_discovery_status`,
   `resource_mode`, and `mcp_tool_loop`.

## Output and Reporting Expectations

Important generated Phase 4 paths:

```text
datasets/phase4-v1.zip
datasets/phase4-v1_manifest.json
results/phase4-v1/direct-best-stage3.csv
results/phase4-v1/full-mcp-best-stage3.csv
```

MCP summaries should separate:

- completed samples
- correct completed samples
- reasoning accuracy
- effective accuracy
- infrastructure failure rate
- tool error rate
- timeout rate

Use the hardening runbook's failure taxonomy when explaining failures:
`CYPHER_ERROR`, `QUERY_TOO_EXPENSIVE`, `INFRA_ERROR`, `MCP_TURN_TIMEOUT`,
`NO_PROGRESS_TIMEOUT`, `SAMPLE_TIMEOUT`, `OLLAMA_STREAM_TIMEOUT`, and
`HALLUCINATION`.

## Safety and Scope Notes

ORI is for controlled benchmark and evaluation work. Do not point evaluation runs
at production BloodHound environments or real customer data unless the
environment owner has explicitly approved the scope, credentials, and reporting
destination.

Do not commit local `.env`, generated secrets, private result dumps, inference
routing configs, or machine-specific operator logs. Keep real provider endpoints,
private model aliases, and local output paths in untracked local configs. The
sanitized templates under `examples/inference/` are safe starting points for
public documentation and user setup. Generated datasets/results may be useful
evidence, but review size and sensitivity before committing them.
