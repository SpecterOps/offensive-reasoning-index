# Portable native MCP setup

This operator workflow uses ORI's public CLI and operator-owned local paths.
It does not require an agent supervisor, a particular workstation, or a private
model host. Use only a controlled synthetic graph. Nothing below authorizes
uploading a graph, changing database privileges, or spending hosted-model usage.

**Integration candidate:** native runtime, discovery, feasibility, qualification,
and campaign interfaces have been reconciled from the native development work.
The Hermes integration additionally provides typed source indexing, config
preparation, native diagnostic schedules, and bounded diagnostic inspection.
Tests and build identities belong to the integration acceptance record; historical
development totals do not certify this candidate. No live qualification or model
campaign is claimed by this runbook.

## 1. Preserve the original benchmark release

Start with the generated source manifest/archive, the complete certified Direct
pool, and the original paired selected release. The candidate pools contain at
least 100 unique contracts per track; the benchmark selects exactly 50 Direct
and 50 MCP tasks. Keep both track selection receipts and their paired receipt.
Do not choose a new seed or substitute tasks to make an implementation feasible.

Generation, compilation, live certification and `select-v2` remain the existing
release workflow. Consult [task authoring](benchmark-v2-task-authoring.md) and
[native backend contracts](native-mcp-backends.md). Use actual paths printed by
those commands, not filenames inferred from another seed or protocol version.
The Direct artifacts and paired selection are required even for an MCP-only
campaign; an MCP-only execution does not run Direct or contact its CE backend.

## 2. Prepare pinned source and an external Python environment

Have the operator obtain the exact source revision and install its dependencies
into a dedicated environment outside the source checkout. ORI does not install,
upgrade, clone, repair or vendor these implementations during benchmarking.

| Implementation | Repository | Exact revision |
| --- | --- | --- |
| Main | `https://github.com/mwnickerson/bloodhound_mcp` | `92a37dd481ce675fe552f14c9957a31dbbcd212e` |
| MorDavid | `https://github.com/MorDavid/BloodHound-MCP-AI` | `1eb21b01da14fd2eda941234e3a545e876bef296` |
| Armadin | `https://github.com/armadin-public/bloodhound-mcp-server` | `6ad4a4703d1117c3019400539911ca689a537197` |

The implementation registry in `src/ori/eval/v2/native_mcp_profiles.py` is
authoritative. Stop if a copied pin disagrees with that checkout's registry.
MorDavid's redistribution licensing remains unresolved; this workflow does not
authorize redistribution of its source.

Use an external venv with system site packages disabled. Supply an exact flat
dependency lock containing every installed distribution, including bootstrap
packages, as `name==version` lines. ORI rejects missing/extra distributions,
editable packages, startup hooks, unsupported lock syntax and runtime paths
outside the frozen roots. Include the interpreter's actual standard-library and
environment roots needed by the existing startup verifier, in a stable order;
do not use `/` or overlapping roots. Freeze only after installation is finished.
See the runtime restrictions in [native backends](native-mcp-backends.md).

Hash the frozen runtime inputs without launching a process:

```bash
uv run ori fingerprint-native-runtime \
  --runtime-root <absolute-runtime-root> \
  --runtime-root <absolute-additional-root> \
  --dependency-lock <complete-flat-lock-file> --json
```

Retain its runtime and dependency-lock hashes. Do not replace them with guessed
values or silently recalculate them after a failed qualification. This operation
hashes local bytes only, without launching Python, MCP or a provider.

## 3. Observe the native profile

Keep credentials outside the repository and YAML. Supply these environment fields:

| Implementation | Required environment | Backend scope |
| --- | --- | --- |
| Main | `BLOODHOUND_DOMAIN`, `BLOODHOUND_TOKEN_ID`, `BLOODHOUND_TOKEN_KEY` | Controlled CE API |
| MorDavid | `BLOODHOUND_URI`, `BLOODHOUND_USERNAME`, `BLOODHOUND_PASSWORD` | Both `neo4j` and `bloodhound` |
| Armadin | `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD` | One explicit, observed home database |

Main optionally accepts `BLOODHOUND_SCHEME`, `BLOODHOUND_PORT` and
`BLOODHOUND_VERIFY_TLS`. Its configured CE URL must identify the same target.
For Bolt, READ routing alone is insufficient: admission requires the implemented
read-only privilege, database access, exact graph, topology and bounded server
transaction-timeout observations. Provisioning those conditions is a separate
operator action. A failed connection to MorDavid's alternate database is not
proof that the alternate is confined. Both destinations must pass.

First check offline inputs:

```bash
uv run ori discover-native-profile \
  --implementation <mwnickerson-or-mordavid-or-armadin> \
  --mcp-dir <pinned-checkout> --python-executable <external-venv-python> \
  --runtime-root <absolute-runtime-root> \
  --runtime-fingerprint <runtime-sha256> \
  --dependency-lock <complete-flat-lock-file> \
  --dependency-lock-fingerprint <lock-sha256> \
  --manifest <original-manifest> --archive <original-archive> --product <source-product> \
  --output-dir <fresh-private-discovery-directory> --json
```

Repeat `--runtime-root` for exactly the frozen ordered root list. Set `--product`
to the exact source product (`oaic-2026-v1` for the OAIC release, or `simple`/`complex`
when intentionally using those products); do not relabel an existing archive.
Add `--database neo4j --database bloodhound` for MorDavid, or
`--database <actual-home-database>` for Armadin. Main takes no database options.
Without `--execute`, this command reports `offline_inputs_checked`
and produce no profile. It checks local runtime bytes, lock hash, archive and
input paths only: no subprocesses, Git source verification, interpreter startup
or discovery. `source_bytes_verified` remains false. After explicit authorization
to contact these controlled services,
repeat with `--execute`.

Discovery uses the existing unbound session discovery path, then builds the
profile from actual descriptors and backend/runtime bindings. It must call no
provider, model tool, prompt body or resource body. A profile is published only
after confirmed cleanup and postchecks. Successful discovery writes
`native-capability-profile-v1.private.json` and `native-discovery-v1.private.json`
inside the requested directory; native diagnostics stay in
`native-stderr.private.log`. Console output contains redacted status only.
The profile is not a qualification receipt. Failed directories remain private
diagnostics and must not be reused as successful discovery.

## 4. Check the unchanged selected MCP roster

This existing command is offline:

```bash
uv run ori check-native-feasibility \
  --manifest <original-manifest> --archive <original-archive> \
  --selection <original-selected-mcp-receipt> \
  --native-profile <observed-native-profile-file> \
  --output-dir <fresh-native-task-directory> --json
```

On a feasible cell it exports `native-public-v2.json` and
`native-oracles-v2.private.json`. Exit 2 means an unsupported cell: retain its
typed result, do not qualify/run/rank it, and do not replace unsupported tasks.
Exit 1 means invalid input; exit 3 means a harness defect. A profile existing or
an MCP exposing many tools does not imply that all selected contracts are
supported. MorDavid and Armadin can legitimately fail this whole-cell gate
because their native evidence cannot prove a selected contract.

## 5. Assemble one private configuration per implementation

Use a separate file and fresh output root per implementation. All placeholders
below require actual operator values; hash placeholders are not valid hashes.
Paths resolve relative to the configuration file. Keep public tasks separate
from sealed oracles and private qualification outputs.

Shared configuration skeleton:

```yaml
version: 2
protocol: ori-eval-protocol-v2
source:
  manifest: <original-manifest-path>
  archive: <original-archive-path>
selected_release: <original-paired-selection-path>
tracks:
  direct:
    public: <full-direct-public-path>
    oracles: <full-direct-oracles-path>
    candidates: <full-direct-candidates-path>
    live_certification: <full-direct-live-certification-path>
    release_metadata: <original-direct-release-metadata-path>
    selection: <original-direct-selection-path>
  mcp:
    public: <native-task-directory>/native-public-v2.json
    oracles: <native-task-directory>/native-oracles-v2.private.json
    candidates: <qualification-directory>/native-candidates-v2.private.json
    live_certification: <qualification-directory>/native-live-certification-v4.private.json
    release_metadata: <original-mcp-release-metadata-path>
    selection: <original-mcp-selection-path>
modes: [direct, mcp]
output_dir: <fresh-campaign-output-directory>
defaults:
  concurrency: 1
  runs_per_model: 1
  bhce_url: <controlled-direct-ce-url>
  max_infra_retries: 1
  # Insert exactly one mcp mapping below here.
models:
  - name: local-qwen
    provider: openai-compat
    model: <local-qwen-served-model-id>
    model_base_url: http://127.0.0.1:8080/v1
    api_surface: chat_completions
    mcp_tool_loop: native-openai-compatible
    options: {}
```

The endpoint is a placeholder for an operator-managed compatible Qwen service,
not a service ORI provisions. Its model artifact and serving configuration must
be frozen separately. Change `modes` to `[mcp]` for MCP-only testing, retaining
the paired artifacts. Local-model execution still requires explicit approval.

Main CE, inserted under `defaults`:

```yaml
mcp:
  mode: native
  implementation_id: mwnickerson
  backend: bhce
  databases: []
  mcp_dir: <main-pinned-checkout>
  capability_profile: <main-observed-profile-file>
  python_executable: <main-external-venv-python>
  runtime_roots: [<main-runtime-root>, <main-additional-root>]
  runtime_fingerprint: <main-runtime-sha256>
  dependency_lock: <main-complete-flat-lock>
  dependency_lock_fingerprint: <main-lock-sha256>
  qualification: <qualification-directory>/native-qualification-v2.private.json
  qualification_work: <qualification-directory>/native-work-v2.private.json
  tool_loop: native-openai-compatible
  max_steps: 16
  read_timeout_seconds: 240.0
  tool_timeout_seconds: 60.0
```

MorDavid Bolt, inserted under `defaults`:

```yaml
mcp:
  mode: native
  implementation_id: mordavid
  backend: neo4j
  databases: [neo4j, bloodhound]
  mcp_dir: <mordavid-pinned-checkout>
  capability_profile: <mordavid-observed-profile-file>
  python_executable: <mordavid-external-venv-python>
  runtime_roots: [<mordavid-runtime-root>, <mordavid-additional-root>]
  runtime_fingerprint: <mordavid-runtime-sha256>
  dependency_lock: <mordavid-complete-flat-lock>
  dependency_lock_fingerprint: <mordavid-lock-sha256>
  qualification: <qualification-directory>/native-qualification-v2.private.json
  qualification_work: <qualification-directory>/native-work-v2.private.json
  tool_loop: native-openai-compatible
  max_steps: 16
  read_timeout_seconds: 240.0
  tool_timeout_seconds: 60.0
```

Armadin Bolt, inserted under `defaults`:

```yaml
mcp:
  mode: native
  implementation_id: armadin
  backend: neo4j
  databases: [<actual-observed-home-database>]
  mcp_dir: <armadin-pinned-checkout>
  capability_profile: <armadin-observed-profile-file>
  python_executable: <armadin-external-venv-python>
  runtime_roots: [<armadin-runtime-root>, <armadin-additional-root>]
  runtime_fingerprint: <armadin-runtime-sha256>
  dependency_lock: <armadin-complete-flat-lock>
  dependency_lock_fingerprint: <armadin-lock-sha256>
  qualification: <qualification-directory>/native-qualification-v2.private.json
  qualification_work: <qualification-directory>/native-work-v2.private.json
  tool_loop: native-openai-compatible
  max_steps: 16
  read_timeout_seconds: 240.0
  tool_timeout_seconds: 60.0
```

Runtime bounds must satisfy the actual selected tasks. A deadline/configuration
rejection is a stop condition, not permission to alter a task or hidden limit.

## 6. Qualify without a model

First use the existing offline preparation command; candidate/live and paired
qualification paths may name the future outputs above:

```bash
uv run ori qualify-native-mcp --config <private-config.yaml> --json
```

After explicit service authorization, qualify into the fresh directory named in
the configuration:

```bash
uv run ori qualify-native-mcp --config <private-config.yaml> \
  --output-dir <qualification-directory> --execute --json
```

This runs harness fixture calls against the native server, not a model. Successful
output contains `native-qualification-v2.private.json`, `native-work-v2.private.json`,
`native-offline-certification-v4.private.json`, `native-candidates-v2.private.json`
and `native-live-certification-v4.private.json`. Existing directories are rejected.
Failed/interrupted directories are diagnostic-only. Never infer completion from
one file or reuse a pending-cleanup process.

## 7. Readiness, authorized local execution, and inspection

```bash
uv run ori run-v2 --config <private-config.yaml>
```

Readiness makes zero provider calls, but contacts the controlled graph and native
server for current graph/runtime/discovery checks. A successful qualification is
not a substitute for this gate. Stop on changed bindings, stale artifacts,
unsupported contracts, wrong databases, graph drift or cleanup uncertainty.

Only after readiness passes and local-model execution is authorized:

```bash
uv run ori run-v2 --config <private-config.yaml> --execute
uv run ori campaign-status --config <private-config.yaml> --json
```

The status command is offline. Execution preserves Direct/MCP separation and
records native failures without inventing missing evidence. A pending cleanup
message means wait for confirmed cleanup; do not kill the owner to force reuse.
After transport uncertainty, investigate the documented recovery condition rather
than clearing quarantine or resuming automatically.

Keep all private artifacts: they may contain endpoints, paths, queries, answers,
tool bodies, prompt/resource data and sealed evidence. Publish only the existing
redacted reports after their graph and publication gates; local testing does not
waive merged-code or final-campaign requirements.
