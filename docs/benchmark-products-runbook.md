# ORI Benchmark Products Runbook

ORI now exposes public benchmark products instead of asking operators to know the older phase names.

The two product names are:

```text
simple
complex
```

Use these names in generated artifacts, scripts, and result folders. Older Phase 3 / Phase 4 run configs still exist for historical comparison and focused diagnostics, but the normal public benchmark path should start with `ori generate simple` or `ori generate complex`.

## Mental Model

ORI has three separate surfaces. Keep them separate when debugging:

1. Dataset generation
   - Produces a SharpHound-compatible zip and a manifest.
   - This is deterministic for a given benchmark name and seed.
   - The generator chooses the benchmark identity, company name, domain, size band, users, hosts, paths, and decoys.

2. BloodHound CE ingest
   - The generated zip must be uploaded into a controlled BloodHound CE instance.
   - ORI does not assume upload happened just because the zip exists.
   - `verify-ingest` checks whether the live BH graph matches the manifest.

3. Model grading
   - Direct/Cypher runs test whether the model can write the right Cypher or structured answer from the task.
   - MCP runs test whether the model can use BloodHound MCP tools against the live ingested graph.
   - MCP grading is only meaningful after ingest verification passes.

If a run fails, first decide which surface failed: generation, ingest, or model execution. Most confusing failures come from using a manifest that does not match the graph currently loaded in BloodHound CE.

## Setup

Install dependencies:

```bash
uv sync
```

Run local quality checks:

```bash
uv run pytest
uv run ruff check src scripts tests
```

For live MCP runs, configure BloodHound CE credentials in `.env` or the shell:

```bash
BLOODHOUND_DOMAIN=<host>
BLOODHOUND_TOKEN_ID=<token id>
BLOODHOUND_TOKEN_KEY=<token key>
```

Do not commit `.env`, tokens, auth files, generated private results, or local operator logs.

Check BloodHound CE before grading:

```bash
uv run ori verify-bh-health
```

A health pass only means BH CE is reachable and can execute Cypher. It does not mean the benchmark dataset has been uploaded.

## Generate a Benchmark

Generate the small benchmark:

```bash
uv run ori generate simple --seed 1234 --output datasets/benchmarks
```

Generate the complex benchmark:

```bash
uv run ori generate complex --seed 4401 --output datasets/benchmarks
```

Do not pass a domain for the public benchmark products. The generator intentionally chooses a seeded benchmark identity. For example, a complex seed may produce a domain such as:

```text
GRANITEMANUFACTURING.LOCAL
```

That generated domain is part of the reproducible benchmark identity. The manifest records it alongside the generated domain SID, size band, graph profile, and task-set metadata.

Expected output shape:

```text
datasets/benchmarks/complex-v1-seed-4401.zip
datasets/benchmarks/complex-v1-seed-4401_manifest.json
```

The zip is the BloodHound handoff artifact. The manifest is ORI's source of truth for task generation, scoring, and ingest verification.

## Preflight Tasks

Run preflight before spending model tokens:

```bash
uv run ori preflight-tasks \
  --manifest datasets/benchmarks/complex-v1-seed-4401_manifest.json \
  --track mcp \
  --output results/benchmarks/complex-v1-seed-4401/preflight-mcp.json
```

Preflight validates task/scorer consistency. It does not contact the model and it does not prove the zip has been uploaded to BloodHound CE.

Interpretation:

- `0 errors` means the task contracts are structurally usable.
- Warnings usually flag known scorer caveats or cases worth reviewing before publication.
- A preflight pass is required but not sufficient for a live MCP run.

## Upload and Verify Ingest

Upload the generated zip into the controlled BloodHound CE environment:

```text
datasets/benchmarks/complex-v1-seed-4401.zip
```

Then verify the live graph against the exact matching manifest:

```bash
uv run ori verify-ingest \
  --manifest datasets/benchmarks/complex-v1-seed-4401_manifest.json
```

If using a non-default BH CE endpoint:

```bash
uv run ori verify-ingest \
  --manifest datasets/benchmarks/complex-v1-seed-4401_manifest.json \
  --bhce-url https://bloodhound.example.com
```

Do not run live MCP grading until this passes.

`verify-ingest` checks:

- the domain and domain SID expected by the manifest,
- node counts by key type,
- planted path nodes,
- planted path edges that BH CE should expose to the evaluator.

Common failure modes:

- Wrong dataset loaded: the live BH graph has a different generated domain, seed, or domain SID than the manifest.
- Old graph still loaded: the previous seed/domain remains in BH CE.
- Partial ingest/materialization: nodes exist, but some synthetic edges expected by the verifier are missing.
- Wrong environment: credentials point at another BloodHound CE instance.

When debugging, trust the manifest and verify the live BH domain list before grading.

## Model Configs

Use `models.example.yaml` as the public-safe template:

```bash
cp models.example.yaml models.local.yaml
```

Edit `models.local.yaml` with the providers you actually want to run. Keep local/private values out of git.

The model matrix supports direct and MCP modes. Example shape:

```yaml
modes: [direct, mcp]
output_dir: results/benchmark-runs/complex-v1-seed-4401

defaults:
  concurrency: 1
  mcp:
    mcp_dir: ../bloodhound-mcp
    max_steps: 16
    resource_mode: off
    tool_loop: auto

models:
  - name: local-qwen
    provider: openai-compat
    model: qwen-fast
    model_base_url: http://127.0.0.1:8080/v1
    mcp_tool_loop: native-openai-compatible

  - name: codex-gpt
    provider: codex
    model: gpt-5.5-codex
    mcp_tool_loop: native-openai-compatible
```

For Codex OAuth, log in with Codex CLI first:

```bash
codex login
```

ORI can use the Codex auth file at:

```text
~/.codex/auth.json
```

Never print or commit the auth file. A safe readiness check is to report only whether a token exists and its length, not the token value.

## Run a Benchmark Campaign

After generation, preflight, upload, and ingest verification, run the model matrix:

```bash
uv run ori run \
  --config models.local.yaml \
  --manifest datasets/benchmarks/complex-v1-seed-4401_manifest.json
```

If you want to keep artifacts isolated, set `output_dir` in the model config to a seed-specific folder:

```text
results/benchmark-runs/complex-v1-seed-4401
```

Preserve these outputs:

- per-model CSVs,
- combined CSV,
- summary CSV,
- telemetry summaries,
- inspect logs for MCP runs,
- the exact manifest used for the run,
- the model config with secrets removed.

## Current Task Counts

The product metadata still declares the intended official complex benchmark size:

```text
complex official: 100 grading tasks
complex diagnostic: 24 grading tasks
```

The current generated complex corpus is not yet enforcing the official 100-task selector. It currently exposes the generated task corpus from the planted paths. At the time of this runbook, a complex seed with the current Tier 6 implementation produces roughly:

```text
planted paths: 30
MCP grading tasks: 62
Tier 6 planted paths/tasks: 19
```

That is good enough for development sweeps, but not the final published official distribution. The next product-hardening step is to add a suite selector that chooses exactly 100 official tasks with an intentional tier distribution.

## Recommended Operator Sequence

For a clean complex benchmark sweep:

```bash
# 1. Generate the benchmark artifacts.
uv run ori generate complex --seed 4401 --output datasets/benchmarks

# 2. Preflight task/scorer contracts.
uv run ori preflight-tasks \
  --manifest datasets/benchmarks/complex-v1-seed-4401_manifest.json \
  --track mcp \
  --output results/benchmark-runs/complex-v1-seed-4401/preflight-mcp.json

# 3. Upload this zip into BloodHound CE.
#    datasets/benchmarks/complex-v1-seed-4401.zip

# 4. Verify the live BloodHound graph matches the manifest.
uv run ori verify-ingest \
  --manifest datasets/benchmarks/complex-v1-seed-4401_manifest.json

# 5. Run the configured model matrix.
uv run ori run \
  --config models.local.yaml \
  --manifest datasets/benchmarks/complex-v1-seed-4401_manifest.json
```

If step 4 fails, stop. Do not grade models against a mismatched graph; the scores will describe ingest drift, not reasoning quality.
