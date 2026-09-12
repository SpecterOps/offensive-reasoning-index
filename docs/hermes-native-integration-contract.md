# Hermes native integration candidate

All commands below use the installed `ori` executable. They never install a
native implementation, upload graphs, change services, or manufacture execution
authority. Private JSON stdout is an operator/machine interface; only the
allowlisted diagnostic projection is suitable for direct controller consumption.

| Command | JSON schema | Behavior |
| --- | --- | --- |
| `generate PRODUCT --seed N --output DIR --json` | `ori-generation-result-v1` | Offline matched source archive/manifest references |
| `inspect-artifacts-v2 --config FILE --json` | `ori-v2-artifact-index-v1` | Offline validation of original complete certified paired release; returns exact private artifact references |
| `fingerprint-native-runtime --runtime-root DIR --dependency-lock FILE --json` | `ori-native-runtime-fingerprint-v1` | Local byte hashes only; repeat ordered disjoint roots |
| `discover-native-profile ... --json` | `ori-native-profile-discovery-v1` | Offline inputs only; `--execute` additionally launches controlled discovery and graph reads |
| `check-native-feasibility ... --json` | `ori-native-feasibility-v1` | All original 50 selected contracts; unsupported cells remain unranked |
| `prepare-native-config-v2 ... --json` | `ori-v2-native-config-result-v1` | Offline original-source/native/runtime binding and one fresh MCP-only configuration |
| `qualify-native-mcp --config FILE --json` | `ori-native-qualification-v1` | Offline fixture preparation; `--execute --output-dir DIR` authorizes controlled native services, never providers |
| `prepare-canary-v2 --config FILE --suite native-five-kind-v1 --output-dir DIR --json` | `ori-v2-canary-preparation-result-v1` | Source-derived exact five diagnostic recipes, separate native artifacts/config/qualification output |
| `run-canary-v2 --config FILE --json` | `ori-v2-canary-run-result-v1` | Diagnostic fixture qualification if absent, then readiness; `--execute` separately launches models |
| `inspect-canary-v2 --config FILE --json` | `ori-v2-diagnostic-result-v1` | Read-only exact completed roster/report/graph validation, then five kind/outcome slots without task IDs |

The native config preparation command requires `--config`, `--native-profile`,
`--runtime-spec`, `--output-dir`, `--model`, `--model-name`, `--model-base-url`,
and optional `--max-infra-retries` (default 1). The runtime spec uses the strict
`hermes-ori-native-runtime-spec-v1` schema. Relative runtime paths resolve from
that file. The exact source revision, observed runtime bytes, dependency lock,
native capability profile, backend, and database scope must agree. The emitted
config preserves both source tracks and the paired selection, chooses one
OpenAI-compatible Chat Completions target, serial execution, and one repetition.
Its `qualification_output_dir` is a reserved future path returned by ORI, not a
completed receipt. `qualify-native-mcp --execute` must populate it before readiness.

Native canaries retain an explicit pointer to the original complete source
configuration. Their deterministic selection is rederived from that independently
certified source, then compiled and fixture-replayed through the native projector.
Their five-item selection type cannot satisfy the official 50-item validator.
Qualification, live graph gates, checkpoints, status, supervisor, and publication
use the real common campaign runtime. No private development-task helper substitutes
for this execution path.

Native profile observations preserve the complete discovered inventory, including
descriptors that are not model-callable. Provider tool lists remain separately
read-only and omit Main's `file_upload`; every actual native call still crosses
the operation guard. Complete discovery is provenance, not execution authority.

Diagnostic inspection accepts one model, one repetition, five unique kinds and
valid completed evidence. It returns exact config, selection, schedule, completion,
report and graph fingerprints, explicit terminal outcomes/correctness, and bounded
usage counters. It omits task IDs, prompts, answers, queries, credentials, endpoints,
and paths. Unsupported, partial, stale, invalid or missing evidence is a nonzero
typed failure, never a synthetic five-result success. Aggregate public export
remains `ori-v2-campaign-public-export-v2`; its private stdout envelope remains
`ori-v2-campaign-export-result-v1`.

Parser failures that precede a callback can remain non-JSON Click errors. An
external controller must bound and sanitize subprocess output and fail closed;
it must not expose raw errors or infer success from an empty response.

Native child stderr and parent SDK parser diagnostics share a 1 MiB private-log
cap. An ORI-owned pipe collector continuously drains excess bytes without retaining
them or blocking the child on an unserviced pipe. A reserved final marker records
observed/retained byte counts, truncation and drain completion. Truncation prevents
successful native session completion. After SDK cleanup, a descendant retaining
stderr has only a bounded final drain interval; incomplete drain also fails closed.
This does not impose a file-size limit on benchmark artifacts or checkpoints.

The installed supervisor retains exact ownership of its launched ORI child and
forwards cancellation with bounded cleanup. Its pre-launch sibling
`<state-filename>.owned-child.private.json` marker is removed only after that
original child is confirmed exited. Unknown spawn or cleanup leaves a quarantine
that blocks relaunch; it never authorizes PID adoption. Do not remove the marker
or change state paths to bypass it. Preserve the evidence and request an operator
containment decision; see [supervisor recovery](v2-campaign-supervisor-contract.md#owned-child-cancellation-and-recovery).

## Offline producer/consumer fixture

The test fixture below materializes real ORI source artifacts, paired selection,
native 50-contract configuration and common-runtime diagnostic results. Its graph
observations and HTTP responses are synthetic. The five replies are deliberately
malformed; no successful model outcomes are manufactured. These files must never
be used as deployment evidence, native qualification, or model scores.

From an isolated environment containing the exact candidate ORI wheel, pytest and
its dependencies, run this test from the integration checkout with a new absolute
destination (do not reuse an earlier source-fingerprint fixture):

```bash
ORI_SYNTHETIC_ACCEPTANCE_DIR=/absolute/new/synthetic-acceptance \
  python -m pytest -q \
  tests/test_v2_release_selection.py::test_prepare_canary_writes_isolated_mcp_config_and_rederives_five_tasks
```

The output root contains `official.yaml`, `native-profile.private.json`,
`native-runtime.private.json`, `native-setup/native-config-v2.private.yaml`, and
`canary/canary-config-v2.yaml`. The latter points to the completed synthetic
diagnostic campaign and can be consumed by the installed `campaign-status`,
`inspect-canary-v2`, and `export-campaign-v2` subprocesses without test patches.
`canary/synthetic-acceptance.json` records the exact expected non-success outcomes.
The original config is the input to `inspect-artifacts-v2`; the profile/runtime
files can exercise fresh `prepare-native-config-v2` output. This fixture does not
claim an installed native MCP process or real-provider acceptance.
