# OAIC selected-release workflow

`oaic-2026-v1` is an explicit product alongside historical `simple` and
`complex`. It does not silently change their datasets or task schedules.
An OAIC release contains exactly **100 tasks: 50 Direct and 50 MCP** over one
seeded graph. Scores remain separate by track.

## Contract pool and selection

Each track compiles 110 unique solver-visible contracts. One hundred belong to
the main candidate pool and ten are separate diagnostic-only contracts. Every contract crosses the existing
typed compiler, sealed oracle and fixture-certification boundaries.

| Contract kind | Main pool | Diagnostic pool | Selected |
| --- | ---: | ---: | ---: |
| Set | 32 | 2 | 10 |
| Count | 30 | 2 | 8 |
| Route | 24 | 2 | 20 |
| Decision | 7 | 2 | 6 |
| Absence | 7 | 2 | 6 |

The expanded populations include privileged users/groups, domain controllers,
certificate-template settings, GPOs/OUs, enterprise certificate authorities, and anchored
session, administrative, delegation, ACL, GPO, LAPS and enrollment relationships.
Set/count pairs ask different answer contracts over the same declared population;
renaming tasks does not create another unique contract. Decision questions expose
every subject identity required by their exact evidence contract without revealing
whether the requested relationships exist.

The selector orders candidates within each kind by the canonical SHA-256 of
`(selector_version, product, seed, track, recipe_id, variant_id)`, followed by
stable recipe, variant and task IDs for ties. It selects without replacement.
It does not use model scores. Reordering an input catalog does not change the
selected IDs. The seed affects both graph generation and the selected subset.
Diagnostic recipes remain excluded across seeds, not merely within one graph.

Eight additional synthetic near-miss fixtures contain a real present path and
a separate absent target. Absence claims are checked against the archive rather
than inferred from labels such as “decoy.” The large set of Domain Admins'
administered computers explicitly asks for the first 500 identities in object-ID
order; its companion count asks for the full population. The window is public,
not hidden pagination or an answer-size hint.

## Generate and compile without services

Run from any supported checkout with Python and `uv`. No external agent,
private host, scheduler or reservation service is required. Seed 67 is the
conference selection; 4401 and 4402 are robustness checks.

```bash
uv run ori generate oaic-2026-v1 --seed 67 --output datasets/benchmarks

uv run ori compile-v2 --product oaic-2026-v1 --track direct \
  --manifest datasets/benchmarks/oaic-2026-v1-seed-67_manifest.json \
  --archive datasets/benchmarks/oaic-2026-v1-seed-67.zip \
  --output-dir results/oaic-67/compiled

uv run ori compile-v2 --product oaic-2026-v1 --track mcp \
  --manifest datasets/benchmarks/oaic-2026-v1-seed-67_manifest.json \
  --archive datasets/benchmarks/oaic-2026-v1-seed-67.zip \
  --output-dir results/oaic-67/compiled
```

Compilation writes public solver artifacts, private oracles, the existing
offline-certification catalog, and a `*-release-metadata-v1.json` sidecar.
Recipe eligibility and identity are rederived from the known registry and
public task bindings whenever admitted; a self-consistent hash is insufficient.
Offline certification is not live qualification or publication approval.

## Certify the exact controlled graph

Graph upload/replacement is a separate, explicitly authorized operation.
Do not run the following until the exact archive is loaded on the controlled
benchmark instance and the operator has authorized the read-only live checks.
Credentials remain in the environment or an external protected file.

```bash
uv run ori certify-v2-live --product oaic-2026-v1 \
  --manifest datasets/benchmarks/oaic-2026-v1-seed-67_manifest.json \
  --archive datasets/benchmarks/oaic-2026-v1-seed-67.zip \
  --output-dir results/oaic-67/certified
```

This retains the existing exact graph gates before, between and after track
certification. It makes no model calls. Compiler, projector, certifier or
capability changes require fresh compatible artifacts; do not reuse stale
certification or resume a differently fingerprinted campaign.

## Select the paired release without services

```bash
uv run ori select-v2 \
  --manifest datasets/benchmarks/oaic-2026-v1-seed-67_manifest.json \
  --archive datasets/benchmarks/oaic-2026-v1-seed-67.zip \
  --compiled-dir results/oaic-67/compiled \
  --certification-dir results/oaic-67/certified \
  --output-dir results/oaic-67/selected
```

This command validates both full public/oracle/candidate/live-certification
pairs before writing either selection. It performs no provider, MCP or graph
calls. It writes two 50-task selection receipts and one common manifest:

```text
oaic-2026-v1-seed-67-direct-selection-v1.json
oaic-2026-v1-seed-67-mcp-selection-v1.json
oaic-2026-v1-seed-67-selected-release-v1.json
```

The common manifest binds product, seed, source manifest, archive, graph,
compiler, selection policy and both exact selections. Exact reruns are
idempotent. Different existing output content is rejected; use a new directory.

## Campaign integration and compatibility

### Stage 3 local-testing handoff

This Stage 3 workflow remains available while native Stage 4 compatibility is
completed. Native integration is not required for this historical-lane campaign.
Use the existing main-server MCP path with its exact pinned checkout revision
`92a37dd481ce675fe552f14c9957a31dbbcd212e` and a controlled synthetic graph.

After the generation, compilation, authorized live certification and paired
selection steps above, copy the complete sanitized template:

```bash
cp examples/inference/oaic-stage3-local.v2.yaml results/oaic-67/models.local.yaml
```

Edit that private copy with your actual local model ID, inference endpoint and
pinned MCP checkout. Keep credentials outside YAML. The template uses one
complete pass, 50 Direct and 50 MCP tasks, and a fresh campaign directory. It
requires no external agent or private-host integration. Match the externally
configured model's context, output capacity and inference settings; preserve
that runtime configuration with your private testing evidence.

Run readiness with the controlled BloodHound credentials in the environment:

```bash
uv run ori run-v2 --config results/oaic-67/models.local.yaml
```

Readiness makes zero provider calls. Stop on any stale artifact, wrong MCP pin,
missing capability or graph mismatch. A passing offline test suite is not a
substitute for this gate. After readiness passes and you intentionally authorize
your local-model run:

```bash
uv run ori run-v2 --config results/oaic-67/models.local.yaml --execute
uv run ori campaign-status --config results/oaic-67/models.local.yaml
```

Do not reuse an older campaign directory after any source, graph, model or
runtime change. Results from this unmerged development checkout are local test
evidence, not publishable benchmark results. Local checkpoints are authorized;
pushes, PRs and merge remain deferred.

### Artifact fields

Use the existing V2 campaign configuration and add:

```yaml
selected_release: selected/oaic-2026-v1-seed-67-selected-release-v1.json
tracks:
  direct:
    public: compiled/oaic-2026-v1-direct-seed-67-public-v2.json
    oracles: compiled/oaic-2026-v1-direct-seed-67-oracles-v2.private.json
    candidates: certified/oaic-2026-v1-seed-67-direct-candidates-v2.json
    live_certification: certified/oaic-2026-v1-seed-67-direct-live-certification-v4.private.json
    release_metadata: compiled/oaic-2026-v1-direct-seed-67-release-metadata-v1.json
    selection: selected/oaic-2026-v1-seed-67-direct-selection-v1.json
  mcp:
    public: compiled/oaic-2026-v1-mcp-seed-67-public-v2.json
    oracles: compiled/oaic-2026-v1-mcp-seed-67-oracles-v2.private.json
    candidates: certified/oaic-2026-v1-seed-67-mcp-candidates-v2.json
    live_certification: certified/oaic-2026-v1-seed-67-mcp-live-certification-v4.private.json
    release_metadata: compiled/oaic-2026-v1-mcp-seed-67-release-metadata-v1.json
    selection: selected/oaic-2026-v1-seed-67-mcp-selection-v1.json
```

This is an artifact fragment, not a complete runnable config. Supply `source`,
`modes`, `output_dir`, `defaults` and `models` as documented for V2. All relative
paths resolve from the configuration file's directory. Both track artifact sets
are mandatory even with `modes: [direct]`; the unused sibling is checked from
files only, not launched. Readiness rederives each selection from the certified
pool. Omitting the selection cannot downgrade an OAIC artifact to an unselected
historical run. Historical products still use their existing full schedules.

`run-v2 --config ...` remains no-model readiness. Only explicitly authorized
`run-v2 --config ... --execute` spends model usage. The full compiled inventory
remains bound to checkpoints, while execution and score denominators use only
the selected tasks. Selection changes invalidate resume provenance; off-selection
attempts or results are rejected rather than silently included.

Stop for unknown templates, recipes, fixture versions, altered metadata,
insufficient quotas, stale certification, mismatched seeds/graphs/sources,
changed selections or invalid resume evidence. Publication still requires
merged code, final certification, graph-gated execution and the applicable
budget/privacy gates. Public reports must not expose prompts, answers, queries,
tool bodies, private paths, credentials or sealed oracle data.
