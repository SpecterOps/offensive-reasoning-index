# V2 Campaign Supervisor Contract

This contract defines how an external process supervisor may operate a protocol
V2 campaign without replacing ORI's lifecycle, checkpoint, retry, or completion
state machines. It applies to terminal multiplexers, service managers, and
agent-based operators.

The supervisor consumes one redacted, read-only command:

```bash
uv run ori campaign-status --config <exact-v2-config> --json
```

The command does not prepare a campaign, contact a provider, start MCP, query
BloodHound, inspect the live graph, or write campaign state. A non-zero exit is
a stop condition, not an invitation to repair receipts.

## Authority boundary

ORI owns:

- config, runtime, graph, capability, and release fingerprints;
- the exclusive campaign lock and durable lifecycle receipt;
- model and tool execution;
- atomic provider-attempt and result checkpoints;
- lifetime infrastructure-retry accounting;
- graph-gated public reports and track-completion receipts;
- the decision about whether a campaign is resumable or complete.

The external supervisor owns:

- process persistence and log capture;
- bounded restart count and backoff;
- environment and credential injection;
- operator-approved model-spend boundaries;
- notifications;
- host reboot policy;
- completed-bundle transfer and retention.

The tracked reference implementation is `scripts/supervise_v2_campaign.py`.
Its fingerprinted state and exclusive lock must live outside the ORI campaign
root. This keeps external restart accounting durable without adding another
file to ORI's own lifecycle namespace.

The supervisor must not create a second campaign status file or infer success
from a process exit alone. ORI's status projection is authoritative.

## Required immutable inputs

Before readiness, pin these operator inputs:

- one absolute config path;
- one output directory declared by that config;
- one source manifest and archive pair;
- the selected Direct and MCP candidate/live-certification artifacts;
- the exact model matrix, API surfaces, provider endpoints, reasoning effort,
  MCP checkout, and runtime executable overrides;
- the provider-spend limit and external restart limit.

Never change those values while resuming a campaign. A different model,
provider, graph, MCP revision, task release, runtime behavior, or output root is
a new campaign with a new config and directory.

## State and action table

| `observed_state` | Required supervisor action |
|---|---|
| `not_started` | Run no-model readiness only. Do not add `--execute`. |
| `readiness_complete` | Stop for the operator's paid-run approval, then launch the exact execute command once. |
| `running` | Monitor only. Never launch a second process. |
| `stale_running` | If `resume_allowed` is true, follow `next_action`: `run_readiness` reruns the exact no-model command, while `resume_campaign` reruns the exact execute command after bounded backoff. Otherwise stop. |
| `interrupted` | If `resume_allowed` is true, follow `next_action`: resume readiness without `--execute`, or resume execution with the exact execute command after operator policy permits. |
| `failed` | Preserve evidence and alert. Never auto-resume. |
| `completed` with `campaign_complete` | Verify and archive the immutable campaign root, then notify. |
| `completed` with `campaign_complete_invalid` | Preserve and alert. Never rerun merely to fill the denominator. |

Recovery always uses the same command shape that created the interrupted phase.
For readiness, do not add `--execute`:

```bash
uv run --env-file <protected-env-file> \
  ori run-v2 --config <exact-v2-config>
```

For paid execution, use the exact execute command:

```bash
uv run --env-file <protected-env-file> \
  ori run-v2 --config <exact-v2-config> --execute
```

ORI reopens only infrastructure and unexecuted work that remains eligible under
the original lifetime retry budget. Successful and model-attributable results
remain terminal. The supervisor must not edit checkpoints to change that
decision.

## One-shot polling algorithm

For every poll:

1. Run `campaign-status --json` against the exact config.
2. If the command exits non-zero, stop and alert.
3. Require the expected `ori-v2-campaign-status-v2` schema and source-config
   fingerprint.
4. Read only `observed_state`, `resume_allowed`, `next_action`, progress,
   per-track completion/validity, and safe outcome counts.
5. Apply the state table exactly once.
6. Sleep using the supervisor's bounded polling interval.

Do not parse private provider responses, prompts, MCP transcripts, tool
arguments, credentials, or BloodHound results. Do not read a private receipt to
invent a more permissive action than `campaign-status` reports.

Progress percentages use `terminal_results`, never `checkpointed_results`.
During `deferred_cooldown` or `deferred_retry`, surface the active recovery
round and pending-infrastructure count; a full checkpoint count is not campaign
completion while those tasks remain eligible.

## Fail-closed rules

Stop and notify the operator when any of these is true:

- status exits non-zero or reports corrupt/incompatible evidence;
- `resume_allowed` is false for a proposed recovery;
- `next_action` is `investigate_failure`;
- the config, runtime, graph, candidate, capability, or completion fingerprints
  disagree;
- lifecycle evidence is missing while a lock or other campaign artifact exists;
- lifecycle evidence exists but its persistent campaign lock file is missing;
- provider-run, checkpoint, report, or completion evidence exists without the
  readiness receipt that binds it to the graph and release;
- authentication or another non-retryable provider failure occurs;
- the external restart or spend limit is reached;
- a track is complete but invalid;
- the graph changes or a required post-track receipt is absent.

Never delete, unlock, truncate, or rewrite ORI's lock, lifecycle, checkpoint,
readiness, report, or completion files. Lock freedom is observed, not repaired.
Use SIGINT or SIGTERM for an operator or budget stop so ORI can record the
interruption. Reserve an uncatchable kill for a host emergency.

## Process and environment example

The external supervisor may run readiness and execution in a persistent terminal
or service, but it must keep credentials outside the repository. A restricted
non-interactive `PATH` must declare approved absolute executables:

```bash
export ORI_UV_EXECUTABLE=/absolute/path/to/uv
export ORI_UVX_EXECUTABLE=/absolute/path/to/uvx
```

Provider credentials remain endpoint-specific environment variables. The
supervisor may verify that a required variable is present, but it must never log
its value. The same rule applies to BloodHound credentials and protected env
files.

Run the reference supervisor from the repository root, inject the protected
environment outside the script, and supply the absolute approved ORI console
executable:

```bash
uv run --env-file <protected-env-file> \
  python scripts/supervise_v2_campaign.py \
  --config <exact-v2-config> \
  --state <path-outside-campaign-root>/supervisor-state.private.json \
  --ori-executable <absolute-path-to-repo-venv>/bin/ori \
  --token-ceiling 24000000 \
  --max-restarts 1 \
  --poll-interval-seconds 5 \
  --execute-approved \
  --model example-provider/example-model \
  --display-name "Example Model" \
  --model-card-output <public-evidence-output>
```

`--execute-approved` is the explicit paid-run authority boundary. Without it,
the supervisor may run no-model readiness but stops before execution. The
state fingerprint binds the exact config bytes, source-config fingerprint,
token threshold, and restart limit so a later process cannot silently reset or
change those policies.

The token threshold is based only on durable provider attempts projected by
`campaign-status`; it never reads raw responses. Polling cannot cancel usage
already in flight, so leave explicit headroom below the operator's absolute
provider budget. For a 25-million-token operator budget, the example stops at
24 million observed tokens and permits no restart after a budget stop. A
supervisor may signal only the child it launched itself; an adopted lock-held
campaign is monitor-only.

## Completion and archival

Archive only after status reports a valid completed campaign. Copy the entire
campaign root to controlled storage, generate hashes at the destination, and
verify the copied bytes before recording archival success. A live or mutable
campaign root may be copied for diagnostics, but it must not be labeled final
evidence.

Report Direct and MCP separately. Preserve the exact config, readiness,
lifecycle, run states, public reports, track-completion receipts, source
manifest/archive hashes, and a redacted operator log. Never publish private
provider bodies, prompts, tool transcripts, oracle files, credentials, or local
machine paths.

## Minimal acceptance test

Before allowing an unattended paid campaign, demonstrate all of the following
with a token-capped smoke root:

- readiness completes with zero provider calls;
- a second launcher cannot acquire the campaign lock;
- status distinguishes active `running` from `stale_running`;
- SIGTERM produces an interruptible, resumable state;
- the exact same execute command resumes without replaying terminal results;
- an open BloodHound circuit prevents later Direct and MCP provider spend;
- failed, corrupt, incompatible, or invalid-completion evidence stops the
  supervisor;
- valid completion produces graph-gated track receipts before archival.

`running` proves exclusive ownership of the campaign root; it does not prove
that the owning process is making progress. A supervisor must never start a
second process while the lock is held. It may alert on an operator-defined
no-progress deadline and request graceful termination, but restart eligibility
begins only after the lock is free and `campaign-status` reports an allowed
typed recovery action. The deadline must be longer than the configured graph,
provider-read, tool-turn, and whole-task bounds so a valid long task is not
mistaken for a wedge.
