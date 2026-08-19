# Offensive Reasoning Index

Offensive Reasoning Index (ORI) is a benchmark harness for evaluating how well
models reason over BloodHound CE Active Directory attack-path data. It generates
synthetic AD graph scenarios, produces SharpHound-compatible ingest artifacts,
runs direct Cypher and BloodHound MCP evaluations, and writes scored result
artifacts that separate reasoning quality from tool, infrastructure, and timeout
failures.

The current public benchmark products are:

```text
simple   small, fast, seeded AD smoke/triage benchmark
complex  large, enterprise-style seeded AD benchmark with multihop reasoning tasks
```

Use the product commands for normal benchmark work:

```bash
uv run ori generate simple --seed 1234 --output datasets/benchmarks
uv run ori generate complex --seed 4401 --output datasets/benchmarks
uv run ori run --config models.local.yaml --manifest datasets/benchmarks/complex-v1-seed-4401_manifest.json
```

Older Phase 3 / Phase 4 run configs remain in the repo for historical comparison
and focused diagnostics. Prefer the `simple` and `complex` product workflow unless
you are deliberately reproducing one of those older phase runs.

## Quick Start

Install dependencies:

```bash
uv sync
```

Run local validation:

```bash
uv run pytest
uv run ruff check src scripts tests
```

Generate a complex benchmark:

```bash
uv run ori generate complex --seed 4401 --output datasets/benchmarks
```

This writes:

```text
datasets/benchmarks/complex-v1-seed-4401.zip
datasets/benchmarks/complex-v1-seed-4401_manifest.json
```

Do not manually specify the domain for public benchmark products. The generator
chooses a deterministic benchmark identity from the benchmark name and seed. For
example, a seed may produce a generated identity like:

```text
Company: Granite Manufacturing
Domain: GRANITEMANUFACTURING.LOCAL
```

That identity is part of the reproducible benchmark. The same benchmark name and
seed should produce the same graph, domain, domain SID, zip, and manifest.

Preflight the task/scorer contracts before spending model tokens:

```bash
uv run ori preflight-tasks \
  --manifest datasets/benchmarks/complex-v1-seed-4401_manifest.json \
  --track mcp \
  --output results/benchmark-runs/complex-v1-seed-4401/preflight-mcp.json
```

Upload the generated zip into a controlled BloodHound CE instance, then verify the
live graph matches the manifest:

```bash
uv run ori verify-bh-health
uv run ori verify-ingest \
  --manifest datasets/benchmarks/complex-v1-seed-4401_manifest.json
```

Only after `verify-ingest` passes should you run live MCP grading:

```bash
uv run ori run \
  --config models.local.yaml \
  --manifest datasets/benchmarks/complex-v1-seed-4401_manifest.json
```

If ingest verification fails, stop and fix the BloodHound graph first. Grading a
model against the wrong graph produces noise, not benchmark signal.

## First Full Campaign: GPT-5.6 Sol vs GPT-5.5

This worked example starts from an existing checkout and runs both ORI tracks
against the complex seed-4401 dataset:

```text
direct   model produces a direct/Cypher answer that ORI executes and grades
mcp      model uses read-only BloodHound MCP tools, then ORI grades its evidence
```

Run the commands from the repository root. MCP benchmark configs use an
immutable `uvx_git` source pin. This workstation keeps the protected credential
file outside the ORI repository at:

```text
../Bloodhound-MCP/.env
```

The `.env` file supplies the BloodHound host and API credentials. Do not print
its token values, copy it into the repository, or commit it.

### 1. Confirm ORI and install dependencies

The current SharpHound/MCP grading fix is commit `7e9ed5d`. Confirm that the
active branch contains it:

```bash
git status --short --branch
git branch --contains 7e9ed5d
uv sync
```

The branch check must print the active branch. If it prints nothing, switch to a
branch that contains the fix or update your branch before benchmarking.

Run the local validation gates:

```bash
uv run pytest
uv run ruff check src scripts tests
```

Do not continue if either command fails.

### 2. Confirm Codex login and model access

ORI's `codex` provider reuses the Codex CLI OAuth login. Confirm the login
without printing `~/.codex/auth.json`:

```bash
codex --version
codex login status
```

If the status says you are not logged in:

```bash
codex login
```

On this workstation, the exact model slugs can be checked in the non-secret
model cache:

```bash
jq -r '
  .models[]
  | select(.slug == "gpt-5.6-sol" or .slug == "gpt-5.5")
  | [.slug, .display_name]
  | @tsv
' ~/.codex/models_cache.json
```

Require both `gpt-5.6-sol` and `gpt-5.5` in the output. Model availability is an
account/runtime capability, so verify it again before a later campaign rather
than assuming the cache never changes.

### 3. Confirm the dedicated BloodHound environment

Check that the protected credential file exists and is not group/world readable:

```bash
test -f ../Bloodhound-MCP/.env
test "$(stat -f '%Lp' ../Bloodhound-MCP/.env)" = "600"
```

Then run the independent health gate:

```bash
uv run --env-file ../Bloodhound-MCP/.env \
  ori verify-bh-health
```

A health pass proves reachability only. It does not prove that the correct
benchmark graph is loaded.

### 4. Generate a matched ZIP and manifest

Generate the complex seed-4401 product without manually setting a domain:

```bash
uv run ori generate complex \
  --seed 4401 \
  --output datasets/benchmarks
```

This must produce the matched pair:

```text
datasets/benchmarks/complex-v1-seed-4401.zip
datasets/benchmarks/complex-v1-seed-4401_manifest.json
```

Create the campaign directory and record artifact hashes for provenance:

```bash
mkdir -p results/benchmark-runs/complex-v1-seed-4401-gpt56sol-vs-gpt55
shasum -a 256 \
  datasets/benchmarks/complex-v1-seed-4401.zip \
  datasets/benchmarks/complex-v1-seed-4401_manifest.json \
  | tee results/benchmark-runs/complex-v1-seed-4401-gpt56sol-vs-gpt55/artifact-sha256.txt
```

Keep the ZIP and manifest together. A manifest from one generation must never
be used to validate or grade a different archive.

### 5. Preflight both task/scorer contracts

Run the direct and MCP preflights separately:

```bash
uv run ori preflight-tasks \
  --manifest datasets/benchmarks/complex-v1-seed-4401_manifest.json \
  --track direct \
  --output results/benchmark-runs/complex-v1-seed-4401-gpt56sol-vs-gpt55/preflight-direct.json

uv run ori preflight-tasks \
  --manifest datasets/benchmarks/complex-v1-seed-4401_manifest.json \
  --track mcp \
  --output results/benchmark-runs/complex-v1-seed-4401-gpt56sol-vs-gpt55/preflight-mcp.json
```

For the current seed-4401 corpus, expect 42 direct tasks and 62 MCP tasks with
zero errors. Warnings are reported separately; inspect them, but the blocking
condition is a preflight error or non-zero command exit.

### 6. Verify or upload the graph

First check whether the dedicated BloodHound instance already matches the
manifest:

```bash
uv run --env-file ../Bloodhound-MCP/.env \
  ori verify-ingest \
  --manifest datasets/benchmarks/complex-v1-seed-4401_manifest.json
```

If this ends with `INGEST CHECK: PASS` and all 30 planted paths pass, do not
upload the archive again.

If the graph is absent or mismatched, stop the terminal workflow and use the
`ori-ingest` Codex skill to upload the exact ZIP through BloodHound MCP:

```text
$ori-ingest Upload datasets/benchmarks/complex-v1-seed-4401.zip to the
configured dedicated ORI BloodHound server, then verify it against
datasets/benchmarks/complex-v1-seed-4401_manifest.json. Show me the exact target
and archive and wait for my confirmation before file_upload.
```

Uploading changes the live benchmark graph. Confirm the target and archive when
prompted, preserve the returned ingest job/status, wait for materialization, and
then rerun `ori verify-ingest`. Never continue to model grading on a wrong target,
count mismatch, partial ingest, or missing planted edge/path.

### 7. Create the two-model campaign config

Store the local config under `results/`, which is gitignored. Create
`results/models-gpt56sol-vs-gpt55.yaml` with:

```yaml
modes: [direct, mcp]
output_dir: benchmark-runs/complex-v1-seed-4401-gpt56sol-vs-gpt55

defaults:
  concurrency: 1
  runs_per_model: 1
  bhce_url: null
  model_base_url: null
  max_model_reruns_on_infra: 1
  health:
    timeout_seconds: 60
    poll_interval: 5
  mcp:
    launcher: uvx_git
    source: git+https://github.com/mwnickerson/bloodhound_mcp@cdb17097e761c8a8622cb93bc3ba49a9e150bb6e
    executable: bloodhound-mcp
    max_steps: 16
    resource_mode: "off"
    tool_loop: native-openai-compatible
  telemetry:
    enabled: true

models:
  - name: gpt-5-6-sol
    provider: codex
    model: gpt-5.6-sol
    mcp_tool_loop: native-openai-compatible

  - name: gpt-5-5
    provider: codex
    model: gpt-5.5
    mcp_tool_loop: native-openai-compatible
```

Start with `runs_per_model: 1`. With the current corpus, this is already 208
graded samples across two models and two tracks. After one complete campaign
succeeds, use a new output directory and raise `runs_per_model` to `3` or more
for variance analysis. Do not confuse repeated model passes with
`max_model_reruns_on_infra`, which only retries infrastructure failures.

Validate that exact launcher without spending model usage. The command starts
MCP, performs its credential preflight, discovers prompts/resources/tools, and
runs representative read-only calls. Use a new readiness path for a new MCP
revision:

```bash
uv run --env-file ../Bloodhound-MCP/.env \
  ori verify-mcp \
  --config results/models-gpt56sol-vs-gpt55.yaml \
  --manifest datasets/benchmarks/complex-v1-seed-4401_manifest.json \
  --output results/benchmark-runs/complex-v1-seed-4401-gpt56sol-vs-gpt55/readiness/cdb17097/mcp-readiness.json
```

Require `MCP READINESS: PASS`. The receipt and telemetry record only safe
launcher/discovery provenance and hashes, never BloodHound credential values or
tool response bodies.

### 8. Run the campaign

Re-run the health and ingest gates immediately before spending model usage:

```bash
uv run --env-file ../Bloodhound-MCP/.env \
  ori verify-bh-health

uv run --env-file ../Bloodhound-MCP/.env \
  ori verify-ingest \
  --manifest datasets/benchmarks/complex-v1-seed-4401_manifest.json
```

Only after `INGEST CHECK: PASS`, launch both tracks:

```bash
uv run --env-file ../Bloodhound-MCP/.env \
  ori run \
  --config results/models-gpt56sol-vs-gpt55.yaml \
  --manifest datasets/benchmarks/complex-v1-seed-4401_manifest.json
```

Keep the terminal open. ORI prints the active model/run as it progresses and
writes separate direct and MCP artifacts beneath:

```text
results/benchmark-runs/complex-v1-seed-4401-gpt56sol-vs-gpt55/
├── direct/
│   ├── baseline_combined.csv
│   └── baseline_summary.csv
└── mcp/
    ├── baseline_combined.csv
    └── baseline_summary.csv
```

Per-model CSVs, telemetry, and inspect logs are written alongside those summary
files. Preserve them with the manifest, artifact hashes, preflight reports, and
a sanitized copy of the model config.

### 9. Read the results

List the complete result bundle and inspect both track summaries:

```bash
find results/benchmark-runs/complex-v1-seed-4401-gpt56sol-vs-gpt55 \
  -maxdepth 4 \
  -type f \
  | sort

sed -n '1,5p' \
  results/benchmark-runs/complex-v1-seed-4401-gpt56sol-vs-gpt55/direct/baseline_summary.csv

sed -n '1,5p' \
  results/benchmark-runs/complex-v1-seed-4401-gpt56sol-vs-gpt55/mcp/baseline_summary.csv
```

Report direct and MCP scores separately. For MCP, distinguish
`reasoning_accuracy` (correct among completed samples) from
`effective_accuracy` (correct among all samples), then inspect
`infra_failure_rate`, `tool_error_rate`, and `timeout_rate`. A model with high
reasoning accuracy and high infrastructure failure rate is not operationally
equivalent to a reliable model with the same completed-sample score.

This seed currently exercises the generated 42-task direct and 62-task MCP
development corpus. It is not yet the final selected 100-task official suite per
track, so label the resulting comparison accordingly.

### First-run stop conditions

Stop and fix the relevant layer before continuing when:

- `codex login status` fails or either model slug is absent;
- the BloodHound health check fails or the configured host is not the controlled
  ORI target;
- either task preflight reports errors;
- `verify-ingest` does not report 30/30 planted paths and `INGEST CHECK: PASS`;
- the pinned MCP source/executable is invalid or readiness does not pass;
- a run reports `INFRA_ERROR`, authentication failures, systematic timeouts, or
  tool-loop failures that would make the two model scores incomparable.

## Repository Layout

```text
src/ori/                         Python package and CLI
docs/benchmark-products-runbook.md
                                 Current simple/complex setup and run workflow
docs/benchmark-hardening-runbook.md
                                 Scoring, preflight, outcome taxonomy, reporting notes
docs/phase4-v1-runbook.md        Historical/focused Phase 4 v1 operator runbook
docs/openai-compatible-model-examples.yaml
                                 Disabled example profiles for API providers
models.example.yaml              Public-safe model matrix example
run-config-phase4-v1.yaml        Older Phase 4 v1 config for focused diagnostics
results/                         Local result artifacts; do not assume complete
```

## Setup Requirements

Required for local generation and tests:

- Python 3.11+
- `uv`

Required for live MCP benchmark campaigns:

- BloodHound CE instance dedicated to controlled benchmark data
- BloodHound CE API credentials
- `uvx` (provided by `uv`) for pinned MCP benchmark launch; a local checkout is optional for development
- provider credentials or local model server for real model runs

BloodHound CE credentials are read from environment variables or a protected
environment file outside the repository:

```bash
BLOODHOUND_DOMAIN=<host>
BLOODHOUND_TOKEN_ID=<token id>
BLOODHOUND_TOKEN_KEY=<token key>
```

Do not commit `.env`, API keys, Codex auth files, BloodHound tokens, private model
configs, or generated private result dumps.

ORI passes only the documented `BLOODHOUND_*` connection fields to the MCP
child process. It does not forward the full parent environment.

## Model Configuration

Start from the public-safe example:

```bash
cp models.example.yaml models.local.yaml
```

Edit `models.local.yaml` with your local providers. A typical model matrix can run
both direct and MCP modes:

```yaml
modes: [direct, mcp]
output_dir: results/benchmark-runs/complex-v1-seed-4401

defaults:
  concurrency: 1
  runs_per_model: 3
  mcp:
    launcher: uvx_git
    source: git+https://github.com/mwnickerson/bloodhound_mcp@cdb17097e761c8a8622cb93bc3ba49a9e150bb6e
    executable: bloodhound-mcp
    max_steps: 16
    resource_mode: "off"
    tool_loop: auto

models:
  - name: local-qwen
    provider: openai-compat
    model: qwen-fast
    model_base_url: http://127.0.0.1:8080/v1
    mcp_tool_loop: native-openai-compatible

  - name: codex-gpt
    provider: codex
    model: gpt-5.5
    runs_per_model: 5
    mcp_tool_loop: native-openai-compatible
```

For local MCP development, `launcher: local_checkout` with `mcp_dir` remains
supported and preserves `uv --directory <mcp_dir> run main.py`. Use the pinned
`uvx_git` launcher for benchmark profiles and a new output root whenever the
revision changes.

`runs_per_model` performs independent complete passes against the same manifest.
The default applies to every model and a model entry can override it. Repeated
outputs are isolated under `direct/<model>/run-001.csv` and
`mcp/<model>/run-001.csv`, with `run_index` and `runs_per_model` retained in the
CSV metadata. This is separate from `max_model_reruns_on_infra`, which only
retries infrastructure failures.

For Codex OAuth, log in with the Codex CLI:

```bash
codex login
```

ORI can use the Codex auth file at:

```text
~/.codex/auth.json
```

Never print or commit the contents of that file.

## What Each Step Proves

Generation proves that ORI can create a deterministic benchmark artifact:

```bash
uv run ori generate complex --seed 4401 --output datasets/benchmarks
```

Preflight proves that the task contracts and scorer expectations are structurally
usable:

```bash
uv run ori preflight-tasks --manifest <manifest> --track mcp
```

BloodHound health proves the API is reachable:

```bash
uv run ori verify-bh-health
```

Ingest verification proves the live BloodHound graph matches the exact manifest:

```bash
uv run ori verify-ingest --manifest <manifest>
```

Model grading proves model performance only if the earlier steps passed:

```bash
uv run ori run --config <models.yaml> --manifest <manifest>
```

Keep this separation in mind. A health pass is not an ingest pass, and a preflight
pass does not mean the graph has been uploaded.

## Current Product State

`simple` is the small smoke/triage benchmark.

`complex` is the large enterprise benchmark. It now includes Tier 6 multihop AD
attack-path reasoning families such as host/session pivots, delegation, RBCD, ACL
nesting, GPO/OU control, LAPS/session pivots, trust hopping, Kerberoast chains,
ADCS identity transition, decoy rejection, stale-session contingency, and negative
controls.

The product metadata declares two official tracks over the same generated dataset:

```text
complex-direct official: 100 grading tasks
complex-mcp official:    100 grading tasks

complex-direct diagnostic: 24 grading tasks
complex-mcp diagnostic:    24 grading tasks
```

The tracks should not be mixed into one 50/50 score. Direct Cypher and MCP measure
different capabilities, so they get separate scores and can optionally be combined
later as a derived composite. Both tracks use the same generated zip and manifest
so model comparisons stay tied to one graph, seed, domain, and path corpus.

The current generated complex corpus does not yet enforce the final 100-task
selector per track. It currently exposes the generated tasks from the planted path
corpus. At the time of this README update, a current complex seed produces roughly:

```text
planted paths: 30
Direct/Cypher grading tasks: 42
MCP grading tasks: 62
Tier 6 paths/tasks: 19
```

Complex generation now validates the completed SharpHound ZIP against every
declared planted relationship before writing it. The current corpus must pass all
30 planted paths at this archive boundary. This proves that the relationships are
encoded in CE-ingestable SharpHound structures; operators must still upload the
ZIP and require `ori verify-ingest` to report 30/30 before running models.

Generated manifest v2 records the relationship-contract version, SharpHound file
versions, tested BloodHound CE baseline, complete projected node counts, and an
archive-derived relationship summary. Relationship names are canonical end to end:
for example, `WriteDacl` is the SharpHound ACE right and `SameForestTrust` is the
live CE relationship. Legacy `WriteDACL` and `TrustedBy` inputs are normalized only
at compatibility boundaries and are never emitted by new datasets.

The next hardening step is to add an official suite selector that chooses exactly
100 direct tasks and 100 MCP tasks with intentional tier distribution from the same
dataset.

## Current Runbooks

- [Benchmark Products Runbook](docs/benchmark-products-runbook.md): current
  simple/complex generation, setup, BloodHound handoff, preflight, ingest, model
  config, and run workflow.
- [Benchmark Hardening Runbook](docs/benchmark-hardening-runbook.md): offline
  answer scoring, task/scorer preflight checks, failure taxonomy, and reporting
  metrics.
- [Phase 4 v1 Runbook](docs/phase4-v1-runbook.md): older Phase 4 v1 workflow for
  historical comparison and focused diagnostic profiles.
- [OpenAI-Compatible Model Examples](docs/openai-compatible-model-examples.yaml):
  disabled profile examples for Ollama OpenAI compat, llama.cpp, MLX, vLLM,
  LM Studio, OpenRouter, and NVIDIA NIM.

## Common Commands

List CLI commands:

```bash
uv run ori --help
```

Generate benchmarks:

```bash
uv run ori generate simple --seed 1234 --output datasets/benchmarks
uv run ori generate complex --seed 4401 --output datasets/benchmarks
```

Preflight generated tasks:

```bash
uv run ori preflight-tasks \
  --manifest datasets/benchmarks/complex-v1-seed-4401_manifest.json \
  --track mcp \
  --output results/benchmark-runs/complex-v1-seed-4401/preflight-mcp.json
```

Verify BloodHound CE health and ingest:

```bash
uv run ori verify-bh-health
uv run ori verify-ingest \
  --manifest datasets/benchmarks/complex-v1-seed-4401_manifest.json
```

Run a configured benchmark campaign:

```bash
uv run ori run \
  --config models.local.yaml \
  --manifest datasets/benchmarks/complex-v1-seed-4401_manifest.json
```

Score structured answers offline without launching a model campaign:

```bash
uv run ori score-answers \
  --manifest datasets/benchmarks/complex-v1-seed-4401_manifest.json \
  --answers answers.json \
  --track mcp \
  --output scorer_projection.json
```

Run older phase configs when intentionally reproducing them:

```bash
uv run ori run --config run-config-phase4-v1.yaml --profile phase4_v1
uv run ori run --config run-config-phase4-v1.yaml --profile phase4_v1_full_mcp_best_stage3
```

## Operating Rules

1. Generate or select a benchmark dataset.
2. Preflight task/scorer contracts.
3. Upload the generated zip into a controlled BloodHound CE environment.
4. Verify ingest against the exact matching manifest.
5. Run direct and/or MCP model grading.
6. Preserve CSV outputs, telemetry, inspect logs, manifest, and sanitized model config.

Operational guardrails:

- Do not point ORI at production BloodHound environments or real customer data
  unless the environment owner explicitly approves the scope.
- Do not treat generated datasets as ingested until `ori verify-ingest` passes.
- Do not compare model scores from runs that used different manifests or different
  live BloodHound graphs.
- Preserve report metadata such as `server_prompt_name`, `available_prompt_names`,
  `prompt_discovery_status`, `resource_mode`, and `mcp_tool_loop`.
- Explain failures using the hardening taxonomy: `CYPHER_ERROR`,
  `QUERY_TOO_EXPENSIVE`, `INFRA_ERROR`, `MCP_TURN_TIMEOUT`,
  `NO_PROGRESS_TIMEOUT`, `SAMPLE_TIMEOUT`, `OLLAMA_STREAM_TIMEOUT`, and
  `HALLUCINATION`.

## Safety and Scope Notes

ORI is for controlled benchmark and evaluation work. Do not point evaluation runs
at production BloodHound environments or real customer data unless the environment
owner has explicitly approved the scope, credentials, and reporting destination.

Generated datasets/results may be useful evidence, but review size and sensitivity
before committing them. Never commit local `.env`, generated secrets, auth tokens,
private result dumps, or machine-specific operator logs.
