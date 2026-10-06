# Offensive Reasoning Index

Offensive Reasoning Index (ORI) is a benchmark harness for evaluating model
reasoning over synthetic BloodHound CE Active Directory graphs. It generates
seeded datasets, runs Direct Cypher or BloodHound MCP tasks, and records scores
with model, tool, infrastructure, and timeout outcomes kept distinct.

The public benchmark products are `simple` (small smoke/triage) and `complex`
(large graph with multihop attack-path tasks). Direct and MCP are separate
tracks and must be reported separately.

## Requirements

- Python 3.11 or later
- [`uv`](https://docs.astral.sh/uv/)
- A controlled BloodHound CE instance for MCP runs
- Credentials for the selected inference provider; Codex models use the Codex
  CLI OAuth login

## Quick start: product workflow

Install the locked dependencies:

```bash
uv sync
```

Generate a benchmark. The same product and seed reproduce the same graph for a
given generator version; use a different seed for a different environment.

```bash
uv run ori generate complex --seed 4401 --output datasets/benchmarks
```

This writes a ZIP for BloodHound ingest and a matching manifest for ORI. Keep
them together. Preflight the task/scorer contracts before model usage:

```bash
uv run ori preflight-tasks \
  --manifest datasets/benchmarks/complex-v1-seed-4401_manifest.json \
  --track direct \
  --output results/preflight-direct.json

uv run ori preflight-tasks \
  --manifest datasets/benchmarks/complex-v1-seed-4401_manifest.json \
  --track mcp \
  --output results/preflight-mcp.json
```

Upload the exact ZIP to a dedicated, controlled BloodHound CE instance. Then
verify health and verify that the live graph matches the manifest:

```bash
uv run --env-file /path/to/private/bloodhound.env ori verify-bh-health

uv run --env-file /path/to/private/bloodhound.env \
  ori verify-ingest \
  --manifest datasets/benchmarks/complex-v1-seed-4401_manifest.json
```

Do not grade models until ingest verification passes.

Copy the canonical Protocol V1 model config and edit the local copy:

```bash
cp models.example.yaml models.local.yaml
```

Keep API keys and machine-specific endpoints in environment variables or a
protected file outside the repository. Then run the campaign:

```bash
uv run --env-file /path/to/private/bloodhound.env \
  ori run --config models.local.yaml
```

The full operator workflow, provider-specific environment variables, and
configuration options are in the
[Benchmark Products Runbook](docs/benchmark-products-runbook.md).

## Protocol V2

V2 is a separate, candidate-certified workflow. Compile Direct and MCP catalogs
from the same generated ZIP and manifest, certify them against the controlled
live graph, then run readiness before explicitly enabling model execution. The
[Benchmark Products Runbook](docs/benchmark-products-runbook.md#local-v2-campaign-config)
contains the V2 local-config template and links to the compile/run commands. The
[V2 Design Rationale](docs/benchmark-v2-design-rationale.md) explains its
scoring and evidence contract. Do not compare V1 and V2 scores as if they used
the same task contract.

## Documentation

- [Benchmark Products Runbook](docs/benchmark-products-runbook.md): generation,
  setup, ingest verification, provider configuration, and campaigns
- [Benchmark Hardening Runbook](docs/benchmark-hardening-runbook.md):
  preflight, containment, failure attribution, and reporting
- [Evaluation Outcome Taxonomy](docs/eval-outcome-taxonomy.md): outcome labels
  and their interpretation
- [V2 Design Rationale](docs/benchmark-v2-design-rationale.md): V2 protocol,
  scoring, and limitations
- [V2 Certification Evidence](docs/benchmark-v2-certification-evidence.md):
  certification and fixture evidence
- [V2 Task Authoring](docs/benchmark-v2-task-authoring.md): adding and
  certifying task families
- [Open-World Discovery V2](docs/open-world-discovery-v2.md): discovery
  workflow and contracts
- [V2 Campaign Supervisor Contract](docs/v2-campaign-supervisor-contract.md):
  bounded execution, status, and recovery
- [V2 candidate catalog schema](docs/schemas/ori-v2-candidate-catalog.schema.json)

## Maintainer checks

```bash
uv run pytest
uv run ruff check src scripts tests
```

Do not commit generated datasets, private results, local model/run configs,
provider credentials, Codex auth files, or operator logs. The checked-in
`models.example.yaml` is the only standalone model config file. V2's distinct
schema is shown inline in the runbook; keep the working copy under ignored
`results/`.
