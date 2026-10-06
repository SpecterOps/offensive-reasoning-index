# ORI Benchmark Products Runbook

ORI now exposes public benchmark products instead of asking operators to know the older phase names.

The two product names are:

```text
simple
complex
```

Use these names in generated artifacts, scripts, and result folders. Legacy
Phase 3 / Phase 4 profiles remain available for historical reproduction, but
their machine-specific configs are not shipped. The normal public workflow
starts with `ori generate simple` or `ori generate complex`.

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

For live MCP runs, keep BloodHound CE credentials in a protected file outside
the repository (mode `0600`) or in the shell:

```bash
BLOODHOUND_DOMAIN=<host>
BLOODHOUND_TOKEN_ID=<token id>
BLOODHOUND_TOKEN_KEY=<token key>
```

Do not commit `.env`, tokens, auth files, generated private results, or local operator logs.

ORI forwards only `BLOODHOUND_DOMAIN`, `BLOODHOUND_PORT`,
`BLOODHOUND_SCHEME`, `BLOODHOUND_TOKEN_ID`, `BLOODHOUND_TOKEN_KEY`, and
`BLOODHOUND_VERIFY_TLS` to the MCP child. It never copies the full parent
environment. Load a protected file explicitly:

```bash
uv run --env-file /absolute/private/path/bloodhound.env \
  ori verify-bh-health
```

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
- node counts for every projected SharpHound file type, including domains, GPOs,
  containers, and ADCS objects,
- planted path nodes,
- planted path edges that BH CE should expose to the evaluator.

Common failure modes:

- Wrong dataset loaded: the live BH graph has a different generated domain, seed, or domain SID than the manifest.
- Old graph still loaded: the previous seed/domain remains in BH CE.
- Partial ingest/materialization: nodes exist, but some synthetic edges expected by the verifier are missing.
- Wrong environment: credentials point at another BloodHound CE instance.

When debugging, trust the manifest and verify the live BH domain list before grading.

Complex ZIP generation includes a relationship projection gate over all 30
planted paths. Generation fails if a declared path edge is absent from the
serialized SharpHound structures. After upload, `verify-ingest` remains mandatory:
it confirms those structures produced the expected live CE relationships and
requires negative-control paths to remain unresolved.

The manifest and verifier use the shared relationship registry. Manifest v2 records
the registry/profile versions and archive-derived relationship counts. New artifacts
emit canonical BloodHound names such as `WriteDacl` and `SameForestTrust`; legacy
`WriteDACL` and `TrustedBy` names are accepted only as input aliases. Unknown or
noncanonical ACE rights fail archive validation instead of silently disappearing at
ingest. `verify-ingest` reports both the manifest kind and the live kind queried when
an exact source/kind/target relationship is absent.

## Protocol V1 Model Config

Use `models.example.yaml` as the public-safe template:

```bash
cp models.example.yaml models.local.yaml
```

Edit `models.local.yaml` with the providers you actually want to run. Keep local/private values out of git.

The model matrix supports direct and MCP modes. Example shape:

```yaml
version: 1
manifest: datasets/benchmarks/complex-v1-seed-4401_manifest.json
modes: [direct, mcp]
output_dir: results/benchmark-runs/complex-v1-seed-4401

defaults:
  concurrency: 1
  runs_per_model: 3
  direct_query_safety:
    enabled: true
    policy_version: bloodhound-cysql-direct-v3
    server_timeout_seconds: 10
    client_timeout_seconds: 15
    max_recursive_hops: 12
    max_result_rows: 1000
    max_query_characters: 16384
    max_recursive_patterns: 2
    max_recursive_expansion_complexity: 256
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

ORI normally resolves `uv` and `uvx` from `PATH`. If an agent supervisor, cron,
or another non-interactive supervisor deliberately supplies a restricted
`PATH`, declare the operator-approved absolute executables in that supervisor's
environment:

```bash
export ORI_UV_EXECUTABLE=/opt/homebrew/bin/uv
export ORI_UVX_EXECUTABLE=/opt/homebrew/bin/uvx
```

Local-checkout runs need only `ORI_UV_EXECUTABLE`; `uvx_git` runs need both.
Readiness fails before model usage when either required executable is missing or
invalid. Private launcher provenance records the exact paths and `uv` version,
and changing them invalidates resume state. ORI does not forward these settings
to the BloodHound MCP child.

## Local V2 Campaign Config

V2 uses a separate strict campaign schema. Keep one machine-local file at
`results/models.v2.local.yaml`; paths below are relative to that file. The
artifact names match the `compile-v2` and `certify-v2-live` commands in the
[V2 compile and certification runbook](benchmark-hardening-runbook.md#v2-compile-and-certification-commands).
Replace the MCP checkout path and model slug with values available in your
environment. The candidate, oracle, and certification files are private; keep
all generated V2 artifacts under the ignored `results/` directory.

```yaml
version: 2
protocol: ori-eval-protocol-v2

source:
  manifest: ../datasets/benchmarks/complex-v1-seed-4401_manifest.json
  archive: ../datasets/benchmarks/complex-v1-seed-4401.zip

tracks:
  direct:
    public: v2/complex-seed-4401/complex-direct-seed-4401-public-v2.json
    oracles: v2/complex-seed-4401/complex-direct-seed-4401-oracles-v2.private.json
    candidates: v2/complex-seed-4401/live/complex-seed-4401-direct-candidates-v2.json
    live_certification: v2/complex-seed-4401/live/complex-seed-4401-direct-live-certification-v4.private.json
  mcp:
    public: v2/complex-seed-4401/complex-mcp-seed-4401-public-v2.json
    oracles: v2/complex-seed-4401/complex-mcp-seed-4401-oracles-v2.private.json
    candidates: v2/complex-seed-4401/live/complex-seed-4401-mcp-candidates-v2.json
    live_certification: v2/complex-seed-4401/live/complex-seed-4401-mcp-live-certification-v4.private.json

modes: [direct, mcp]
output_dir: benchmark-runs/complex-seed-4401-v2

defaults:
  concurrency: 1
  runs_per_model: 1
  max_infra_retries: 2
  infra_retry:
    immediate_retries: 1
    deferred_cooldown_seconds: 300
  mcp:
    mcp_dir: /path/to/pinned/BloodHound-MCP
    max_steps: 16
    resource_mode: "off"
    tool_loop: native-openai-compatible

models:
  - name: codex-model
    provider: codex
    model: <available-codex-model-slug>
    api_surface: auto
    mcp_tool_loop: native-openai-compatible
    options: {}
```

For OpenAI-compatible V2 providers such as Nous Portal, use
`provider: openai-compat`, the provider's model ID and `model_base_url`, and
`api_surface: auto`. Store its key in the matching environment variable, not
in this file. Run V2 readiness first; add `--execute` only after readiness
passes and you intend to spend provider usage.

For unattended V2 operation, use the read-only `ori campaign-status --json`
projection and follow the
[V2 Campaign Supervisor Contract](v2-campaign-supervisor-contract.md). External
process managers own persistence, backoff, notifications, credential injection,
and archival; ORI remains authoritative for locking, readiness, checkpoints,
resume eligibility, graph gates, and completion.
The V2 status projection reports `terminal_results` separately from durable
`checkpointed_results`; monitors must use terminal results for completion and
display deferred cooldown/retry phases rather than treating a full checkpoint
count as 100 percent complete.

For OpenRouter, set `OPENROUTER_API_KEY` in the environment and use the
OpenAI-compatible provider. ORI uses `OPENAI_COMPAT_API_KEY` as the explicit
override and selects `OPENROUTER_API_KEY` only for OpenRouter endpoints. It
never falls back to a key belonging to another provider hostname. The key
works for both direct and MCP inference; keep it out of YAML and other tracked
files.

```bash
export OPENROUTER_API_KEY="<your OpenRouter key>"
```

```yaml
models:
  - name: openrouter-model
    model: openai-compat/<openrouter-model-id>
    model_base_url: https://openrouter.ai/api/v1
    mcp_tool_loop: native-openai-compatible
    openai_compat_telemetry_adapter: generic
```

Replace `<openrouter-model-id>` with the exact model ID available from OpenRouter.

For Nous Portal, set `NOUS_API_KEY` (or `NOUS_PORTAL_API_KEY`) and use the
OpenAI-compatible inference endpoint. ORI selects the Nous-specific key when
`model_base_url` points at `inference-api.nousresearch.com`:

```bash
export NOUS_API_KEY="<your Nous Portal key>"
```

```yaml
models:
  - name: nous-portal-model
    model: openai-compat/<nous-model-id>
    model_base_url: https://inference-api.nousresearch.com/v1
    mcp_tool_loop: native-openai-compatible
    openai_compat_telemetry_adapter: generic
```

Replace `<nous-model-id>` with the exact model ID shown in the [Nous Portal API
Docs](https://portal.nousresearch.com/api-docs). Keep the key out of YAML and
use a model that supports the OpenAI chat-completions/tool-calling surface for
MCP runs.

The top-level `manifest` and `output_dir` make the matrix self-contained.
Relative paths are resolved from the config file's directory. Treat
`output_dir` as the campaign name: copy the config and select a new value for
each fresh campaign so checkpoints and policy-scoped deny-cache state are never
silently reused. `--manifest` and `--output-dir` remain explicit CLI overrides.
See [`models.example.yaml`](../models.example.yaml) for the canonical Protocol V1
baseline config. This runbook shows provider-specific entries and the available
campaign overrides.

Unknown model-matrix settings are errors. ORI writes a directly runnable,
absolute-path `campaign-config.yaml`, preserves the untouched input as
`campaign-config.source.yaml`, and records hashes plus CLI overrides in
`campaign-provenance.yaml`. The generated config can resume that exact campaign.
If any provenance record differs on a later invocation, ORI requires a new
`output_dir`.

Protocol V2 model entries also record `api_surface`. `auto` preserves current
behavior: Codex OAuth uses Responses and OpenAI-compatible providers use Chat
Completions. OpenRouter and Nous may set `api_surface: chat_completions`
explicitly. Release 1 rejects official OpenAI Responses and other unsupported
surface selections during no-model readiness. Requested/resolved surfaces,
structured-output mode, endpoint family, and credential-source name are private
fingerprinted provenance; secret values are never recorded.

Do not resume a campaign produced by an incompatible provider runtime. Create
a fresh output directory after a runtime change.
`runs_per_model` controls independent full benchmark passes against the same
dataset and manifest. A model entry overrides the default. Each repetition has
its own CSV and run metadata. `max_model_reruns_on_infra` remains reserved for
recovery retries and does not increase the requested sample count.

Direct mode applies the versioned safety policy after model generation. It does
not alter the prompt or repair the answer. Policy-rejected queries are scored as
`QUERY_TOO_EXPENSIVE` without reaching BloodHound; admitted queries run once
with BloodHound's documented server timeout. See
[Benchmark Hardening Runbook](benchmark-hardening-runbook.md#direct-cypher-containment)
for circuit-breaker, deny-cache, checkpoint, and reporting details.

For Codex OAuth, log in with Codex CLI first:

```bash
codex login
```

ORI can use the Codex auth file at:

```text
~/.codex/auth.json
```

Never print or commit the auth file. Check login status with `codex login status`.

Before model grading, start the exact pinned MCP package and exercise prompt,
resource, tool, and credential startup paths without calling a model:

```bash
uv run --env-file /absolute/private/path/bloodhound.env \
  ori verify-mcp \
  --config models.local.yaml \
  --manifest datasets/benchmarks/complex-v1-seed-4401_manifest.json \
  --output results/readiness/cdb17097/mcp-readiness.json
```

The output is an immutable readiness receipt. Use a new output root when the
MCP revision changes. It records the launcher, canonical source, full revision,
executable, `uv` version, prompt/resource discovery, read-only tool inventory,
and hashed response evidence; it never records credential values or tool
response bodies.

For local MCP development only, retain the legacy launcher:

```yaml
mcp:
  launcher: local_checkout
  mcp_dir: ../bloodhound-mcp
```

That mode preserves `uv --directory <mcp_dir> run main.py`. Benchmark configs
should use the pinned `uvx_git` form above.

## Run a Benchmark Campaign

Run the direct track:

```bash
uv run ori run --config models.local.yaml
```

Run the MCP track from the same manifest by setting `modes: [mcp]` in the model
config, or run both tracks together with:

```yaml
modes: [direct, mcp]
```

The direct and MCP tracks should be reported separately. They use the same
dataset, seed, generated domain, domain SID, and planted path corpus, but they
measure different skills.

If you want to keep artifacts isolated, set `output_dir` in the model config to a seed-specific folder:

```text
results/benchmark-runs/complex-v1-seed-4401
```

Preserve these outputs:

- exact and resolved campaign-config YAML,
- per-model CSVs,
- combined CSV,
- summary CSV,
- telemetry summaries,
- inspect logs for MCP runs,
- the exact manifest used for the run,
- the model config with secrets removed.

## V1 Development Task Counts

The generated complex V1 development corpus currently exposes 42 Direct tasks
and 62 MCP tasks. These are not final 100-task official selectors. Keep Direct
and MCP scores separate; they use the same graph but measure different
capabilities.

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
uv run ori run --config models.local.yaml
```

If step 4 fails, stop. Do not grade models against a mismatched graph; the scores will describe ingest drift, not reasoning quality.

## OpenAI-Compatible Providers

The single checked-in `models.example.yaml` includes generic examples for
OpenAI-compatible providers. Copy it to `models.local.yaml`, edit the relevant
model entry, and set credentials through environment variables. For Nous
Portal, set `NOUS_API_KEY` or `NOUS_PORTAL_API_KEY` outside the repository:

```bash
cp models.example.yaml models.local.yaml
export NOUS_API_KEY="<your Nous Portal key>"
uv run ori run --config models.local.yaml
```
