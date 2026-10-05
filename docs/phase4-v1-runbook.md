# Phase 4 v1 Runbook

This runbook describes the standalone operator workflow for Phase 4 v1.
It covers dataset generation, generated artifacts, BloodHound CE handoff points,
and the MCP resources run profile.

## Scope

Phase 4 v1 adds an advanced AD benchmark profile without replacing the Phase 3
baseline. Phase 4 artifacts are written under `datasets/phase4-v1*` and
`results/phase4-v1/`.

Implemented scenario families:

- Tier 4 ADCS ESC1-style abuse path.
- Tier 4 delegation/RBCD-style abuse path.
- Tier 5 ADCS-to-delegation composite compromise path.

The BloodHound CE upload and ingest verification step is intentionally deferred
until the operator confirms a live BH CE ingest window.

## Config File

Canonical config:

```text
run-config-phase4-v1.yaml
```

Profiles:

- `phase4_v1`: generates the deterministic Phase 4 dataset.
- `phase4_v1_direct_best_stage3`: runs the no-MCP direct Cypher eval against the
  generated manifest.
- `phase4_v1_full_mcp_best_stage3`: runs the full MCP eval against the generated
  manifest with tools, on-demand resources, and MCP prompt discovery.

The eval profiles are marked `enabled: false` so `--run-all-profiles` will skip
them, but explicit `--profile` invocations still run them.

## Generate Dataset

Generate the dataset before any Phase 4 eval run:

```bash
uv run ori run --config run-config-phase4-v1.yaml --profile phase4_v1
```

Expected generated outputs:

```text
datasets/phase4-v1.zip
datasets/phase4-v1_manifest.json
```

Current canonical generation inputs:

```yaml
domain: CORP.LOCAL
seed: 4401
generator:
  profile: phase4_v1
sizing:
  users: 32
  workstations: 12
  servers: 6
```

The generated graph is seed-reproducible. The same profile inputs and seed should
produce the same manifest and SharpHound-compatible zip. To create a variant,
copy the profile or edit the `seed`, `domain`, or `sizing` fields in
`run-config-phase4-v1.yaml`, then regenerate.

Current generated manifest summary for seed `4401`:

```text
domain: CORP.LOCAL
users: 35
computers: 19
groups: 20
ous: 10
total_nodes: 89
total_edges: 191
```

The user count is higher than the sizing input because Phase 4 adds service and
scenario principals needed by the advanced paths.

## BloodHound CE Handoff

Do not assume ingest has happened after generation. The zip must be
uploaded into BloodHound CE before live MCP grading is meaningful.

Operator handoff artifact:

```text
datasets/phase4-v1.zip
```

After upload, verify the live graph against the generated manifest:

```bash
uv run ori verify-ingest --manifest datasets/phase4-v1_manifest.json
```

If a non-default BH CE URL is required:

```bash
uv run ori verify-ingest \
  --manifest datasets/phase4-v1_manifest.json \
  --bhce-url https://bloodhound.example.com
```

Do not mark Phase 4 ingest accepted until this verification passes.

## Required Eval Surfaces

After BloodHound CE contains the Phase 4 graph, run both eval surfaces.

No-MCP direct Cypher eval:

```bash
uv run ori run \
  --config run-config-phase4-v1.yaml \
  --profile phase4_v1_direct_best_stage3
```

Expected output:

```text
results/phase4-v1/direct-best-stage3.csv
```

Full MCP eval with tools, resources, and MCP prompt discovery:

```bash
uv run ori run \
  --config run-config-phase4-v1.yaml \
  --profile phase4_v1_full_mcp_best_stage3
```

Expected output:

```text
results/phase4-v1/full-mcp-best-stage3.csv
```

The direct profile currently uses:

```yaml
model:
  name: ori-qwen35-9b-64k-direct
  model: ollama/ori-qwen35-9b-64k
  options:
    temperature: 0
```

The full MCP profile currently uses:

```yaml
model:
  name: ori-qwen35-9b-64k-full-mcp
  model: ollama/ori-qwen35-9b-64k
  options:
    temperature: 0
mcp:
  max_steps: 16
  resource_mode: on-demand
  tool_loop: auto
  openai_compat_telemetry_adapter: auto
  ollama_read_timeout_seconds: 1800
```

`tool_loop: auto` keeps Ollama models on the native Ollama MCP loop and uses
Inspect for supported non-Ollama providers.

For Phase 4 reporting, compare the direct result CSV against the full MCP result
CSV. The direct run measures whether the model can synthesize correct Cypher from
the task alone. The full MCP run measures whether the model can use BloodHound MCP
tools, reference resources, and the discovered MCP system prompt to reason over
the live graph.

## API Model Profiles

For OpenAI-compatible APIs, set the model to `openai-compat/...`, provide the
base URL, and force the native OpenAI-compatible MCP tool loop.

ORI uses `OPENAI_COMPAT_API_KEY` only as the explicit override for generic
OpenAI-compatible endpoints. OpenRouter selects `OPENROUTER_API_KEY`, Nous
selects `NOUS_API_KEY` (with `NOUS_PORTAL_API_KEY` as an alias), and official
OpenAI selects `OPENAI_API_KEY`. Keep keys out of run-config YAML and other
tracked files. The selected key works for both direct and MCP inference.

For Nous Portal, use `https://inference-api.nousresearch.com/v1` as the base URL
and set `NOUS_API_KEY` before running ORI.

Reusable examples live in:

```text
docs/openai-compatible-model-examples.yaml
```

Copy one disabled profile from that file into a generated run config or into
`run-config-phase4-v1.yaml`, then update the provider URL, model name,
and output path.

Local llama.cpp example:

```yaml
model:
  name: llama-cpp-qwen
  model: openai-compat/qwen
  model_base_url: http://127.0.0.1:8080/v1
  mcp_tool_loop: native-openai-compatible
  openai_compat_telemetry_adapter: llama-cpp
  options:
    temperature: 0
```

MLX example:

```yaml
model:
  name: mlx-qwen
  model: openai-compat/qwen
  model_base_url: http://127.0.0.1:8080/v1
  mcp_tool_loop: native-openai-compatible
  openai_compat_telemetry_adapter: mlx-lm
  options:
    temperature: 0
```

OpenRouter-style example:

```yaml
model:
  name: openrouter-qwen
  model: openai-compat/qwen/qwen3-32b
  model_base_url: https://openrouter.ai/api/v1
  mcp_tool_loop: native-openai-compatible
  openai_compat_telemetry_adapter: generic
  options:
    temperature: 0
```

Replace `qwen/qwen3-32b` with the exact OpenRouter model ID you want to run.

Supported OpenAI-compatible telemetry adapters:

```text
auto
generic
llama-cpp
mlx-lm
vllm
lm-studio
```

The native OpenAI-compatible loop captures standard usage fields, provider
metadata when present, structured reasoning fields, and `<think>...</think>`
blocks. Provider-specific telemetry is best-effort because OpenAI compatibility
does not standardize runtime metrics.

## Prompt Discovery

MCP prompt loading is automatic. ORI asks the BloodHound MCP server for available
prompts, ranks BloodHound/assistant-like prompts, and falls back to ORI's built-in
MCP system prompt when the server has no usable prompt.

Report metadata records:

```text
server_prompt_name
available_prompt_names
prompt_discovery_status
resource_mode
mcp_tool_loop
```

Preserve these fields in run summaries because they explain which prompt surface
the model actually saw.

## Acceptance Checklist

Use this checklist for run acceptance:

- Dataset generated with `phase4_v1`.
- `datasets/phase4-v1.zip` exists.
- `datasets/phase4-v1_manifest.json` exists.
- BloodHound CE upload completed by operator.
- `ori verify-ingest` passed for the Phase 4 manifest.
- No-MCP direct eval completed.
- Full MCP eval completed.
- `results/phase4-v1/direct-best-stage3.csv` exists.
- `results/phase4-v1/full-mcp-best-stage3.csv` exists.
- Prompt discovery metadata is present in the CSV.
- Tests pass with `uv run pytest`.
- Lint passes with `uv run ruff check src tests`.

Current deferred item:

```text
BloodHound CE upload and ingest verification are pending operator availability.
```
