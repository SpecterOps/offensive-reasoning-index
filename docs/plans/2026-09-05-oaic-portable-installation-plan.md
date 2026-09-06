# S1-06A — portable installed-package acceptance

Revision 3, September 5, 2026. Status: independently approved for this bounded
slice after the two clarifications below. This is a QA-enablement slice, not closure of Stage 1 or a
claim that the four-machine/version qualification matrix has passed.

**Completion:** implemented, independently reviewed and locally validated. All
ten installed-wheel probes passed; 61 focused tests/two subtests and the full
1,099 tests/76 subtests passed (237.35s). All 160 implementation-file hashes match
the final validation boundary; the 158 pre-existing files are unchanged. This
adds new QA coverage, not a reduction in logical obligations or collected tests.
The broader consolidation target remains open.

## Ownership, boundary and dependencies

Root is accountable for QA, acceptance fixtures and integration. Implementation
of the standalone driver belongs to `s1_portable_driver`; root owns its tests and
documentation. `oaic_stage1_adversarial_fallback` independently reviews the plan
and the driver/test implementation; neither author approves their own work.
Assign the worker only after review. All writes stay on the consolidated branch;
preserve existing changes and leave signing, fetch/push, PR and merge deferred.
No model, provider, live graph, host or inference-service operation is authorized.

Current packaging uses Hatchling, Python >=3.11, package `src/ori`, distribution
`offensive-reasoning-index`, and console entry point `ori = ori.cli:main`.
Existing lifecycle/transport tests are checkout-based. `campaign-status` resolves
six artifact paths relative to its config, checks existence, and reports
`not_started` without parsing those artifacts when the output is absent.
Consequently this probe cannot certify the artifacts, readiness or execution.

Owned files: new `scripts/verify_portable_installation.py`, new
`tests/test_portable_installation_acceptance.py`, this plan and the progress
ledger. No production package, configuration schema, dependency or lock changes.
Before implementation snapshot absence of the two new files and inventory
`src`, `scripts`, `tests`, `pyproject.toml`, `uv.lock` using the Stage 1 snapshot
protocol. Reconcile the inventory after validation; documentation is accounted
for separately. Existing 1,038-test/74-subtest evidence is historical after edits.

## Interface and installation contract

The driver is standalone standard-library Python and exposes:

```
python scripts/verify_portable_installation.py \
  --python <absolute-wheel-environment-python> \
  --wheel <absolute-built-wheel> \
  --output-dir <new-private-qualification-directory>
```

All three options are mandatory. Do not install, provision, download, launch uv,
discover personal tools or locate credentials inside the driver. Require existing
absolute executable Python and a regular wheel. Resolve symlinks for wheel/output
and package locations, but preserve the venv interpreter symlink when launching:
resolving it can select the base interpreter instead. Reject an existing output
path entry, including a regular file or dangling symlink, rather than overwrite
old evidence. Validate the lexical output entry before resolving its parent;
never follow a dangling output symlink. Create the output
with mode 0700, then use its `outside checkout`, `config files`, `generated
artifacts` and `empty home` children. Do not delete anything automatically.
Reject an output beneath an ancestor `.git` marker (file or directory), or beneath
the target venv. This is Git-worktree/venv exclusion, not detection of arbitrary
unpacked source archives. Actual acceptance uses a temporary root outside both.

The operator creates a separate venv without system site packages, installs locked
runtime dependencies and the exact built wheel, and supplies that interpreter.
Use `uv export --frozen --no-dev --no-emit-project` to obtain requirements for
installation; retain the export and build/install logs privately. A wheel-only
venv must not install the project editable or import it from a checkout. A local
cached/offline installation is sufficient for developing this driver; it does
not establish a clean OS matrix cell. Do not add the source tree to PYTHONPATH.

The driver first probes the target with `-I`. Require a venv (`sys.prefix !=
sys.base_prefix`), its pyvenv.cfg `include-system-site-packages = false`, and the
installed console script inside that venv. Inspect distribution metadata using
`importlib.metadata`; require the expected distribution and `ori.cli:main` entry
point. Require the imported `ori` package inside the venv's site-packages, not
the checkout, and require loaded `ori.cli.__file__` beneath that verified package.
Compare every non-directory wheel member under `ori/` byte-for-byte
with that installed package, reject extra package files other than bytecode
caches, and require the wheel's METADATA/version and entry_points.txt to equal
the installed distribution's corresponding files. Reject traversal/absolute
wheel member names and duplicate members. Bind the receipt to the wheel SHA-256,
installed module inventory, Python/OS/architecture and observed package location.
This proves the installed ORI bytes match that wheel, not dependency integrity;
locked dependency provisioning and source-to-wheel binding remain outer gates.

## Child-process containment and honest scope

Launch every probe with an argument list, no shell, a finite timeout (30 seconds
for import/help/status/errors; 180 seconds per generation command) and a child
environment constructed from scratch. Allowed values are PATH set only to the
target interpreter directory, HOME and XDG_CONFIG_HOME under empty home,
TMPDIR/TMP/TEMP under the private output, UTF-8 encoding controls, NO_COLOR,
TERM=dumb and PYTHON_DOTENV_DISABLED=1. The locked python-dotenv supports that
switch; it prevents CLI startup from discovering an ancestor or package-adjacent
.env despite the empty home/cwd. On Windows, reject as unsupported in this slice rather than imply
matrix support. Never inherit provider tokens, BLOODHOUND fields, proxy fields,
PYTHONPATH, PYTHONHOME, agent paths or the operator's home.

Use `python -I -c <standard-library bootstrap> <installed-console-script> <args>`.
The bootstrap installs an audit hook before importing ORI, then uses runpy to run
the actual installed console script as __main__ with its normal argv. Deny and
record Python socket connect/bind/address-resolution/send operations and process
launch events (subprocess.Popen, os.system, os.posix_spawn, os.exec). Record event
names only, never their arguments. Emit a uniquely prefixed guard summary on
stderr in finally, even for expected Click SystemExit. The driver requires exactly
one valid summary. All attempted forbidden operations fail acceptance except the
narrow blocked capability observation below; catching an exception does not erase
an attempt. A separate
guard self-test intentionally attempts a socket connection and a subprocess and
requires both be rejected before execution. This is an audit of ordinary Python
operations, not an OS network sandbox or a defense against malicious native code.
Never describe it as proof against all possible network access.

Revision 3's independently reviewed exception changes accounting, not permissions:
the locked urllib3 imports `HAS_IPV6 = _has_ipv6("::1")` and attempts a local bind
while importing status dependencies. Continue raising before every bind. Classify
only `socket.bind` on an IPv6 socket with exact address `("::1", 0)` and immediate
caller module/function `urllib3.util.connection._has_ipv6`. Accept zero or one such
blocked observation only for `status-relative` and `status-poison`. Retain the
original event and classification/count in the guard receipt. Other families,
addresses, callers, repeats, other probes and additional outbound events fail.
Describe a matching receipt as one blocked local capability probe, never zero
attempted network operations. The first failed status receipt remains diagnostic
evidence; no production code or dependency is changed to suppress the observation.

Store private stdout/stderr logs and command argument arrays with exit codes,
duration and SHA-256. On timeout or failed assertion write a failed receipt and
return 1. Invalid driver arguments return 2 before creating output. Never emit a
passing receipt for a partial run. The receipt schema is
`ori-portable-installation-v1`, with state pass/failed, wheel digest, environment,
package inventory, per-probe expected/actual exit and guard summary, artifact
digests and typed failure. Console output is only pass/fail, probe IDs and receipt
path; full logs remain private. This subordinate receipt does not replace the
outer `ori-cleanup-qualification-v1` source/lock/build/matrix receipt.

## Exact ordered acceptance probes

1. Guard self-test and installed-byte/entry-point verification above.
2. Installed `ori --help`: exit 0, commands `generate`, `run-v2`,
   `campaign-status` present. `ori benchmark list`: exit 0, distinct line-leading
   product identifiers `simple` and `complex`, not incidental prose matches.
3. `ori generate simple --seed 1234 --output <spaced-simple-output>` and
   `ori generate complex --seed 4401 --output <spaced-complex-output>`: exit 0;
   require exactly the expected versioned ZIP/manifest pair in each root,
   parse manifest seed and benchmark identity, validate ZIP CRC, and hash both.
   These are generation/package checks, not ingest/certification. Comparison to
   the historical artifact baseline remains the outer gate, not fabricated here.
4. Create a strict V2 Direct-only config under `config files` with relative
   manifest/archive/public/oracles/candidates/live_certification paths pointing
   to six empty inert files in that directory, and relative output `campaign
   output`. Use one generic openai-compatible placeholder model and explicit
   `http://127.0.0.1:9/v1`; no credentials, no MCP service. Label fixtures in the
   receipt as inert/non-certified, never reuse them for run-v2 readiness.
5. Place a non-directory poison file at the same relative `campaign output` under
   `outside checkout`. Run installed `campaign-status --config <absolute-config>
   --json` there: exit 0, parse exact not_started lifecycle/observed state,
   next_action=run_readiness, resume_allowed=false, one expected run and zero
   provider attempts/tokens/checkpoints. Correct config-relative output remains
   nonexistent; cwd poison remains byte-identical.
6. Create the config-relative `campaign output` as a poison regular file. Repeat
   status: exit 1 with `campaign output path is not a directory`. This contrasts
   the correct and wrong path rather than merely asserting a missing directory.
   Keep this failed-state fixture; do not delete it for later probes.
7. `run-v2` without --config and `run-v2 --config <nonexistent-file>`: exit 2,
   actionable missing-option/path error, zero forbidden attempts. Do not pass
   --execute or invoke real readiness as an installed-package smoke test.

Each probe checks outputs, not just process success. Environment/package failures
stop before CLI probes; subsequent unexpected failure stops and preserves logs.
Successful earlier generation is not an overall pass if a later probe fails.

## Tests, review and acceptance

Normal `uv run pytest` must not require an externally provisioned wheel, install
packages, call a provider or silently skip these new driver tests. Tests exercise
driver parsing, environment allowlisting, wheel-member/path validation, package
inventory mismatch detection, probe-output validation and failure receipts with
temporary fixtures and bounded fake subprocess results. At least one actual
stdlib child executes the audit bootstrap's self-test. Explicit cases cover:
relative Python/wheel paths; existing output; malformed/duplicate/traversal wheel;
missing and extra installed file; altered file hash/entry point; external package
path; unexpected exit; missing/duplicate guard summary; caught forbidden attempt;
timeout; malformed status JSON; wrong status/action/counters; and output poison.
Do not collapse these cases into one opaque assertion. Parameterization is fine.

Root runs the real driver against a newly built wheel in a separate local venv
with frozen runtime dependencies, spaced paths and no editable project. Retain
build/install/driver receipts. Deliberately alter a private installed ORI file and
rerun with a new output: require installed-byte failure before CLI probes. This
copy-only negative control must not change the source or accepted installation.

Independent review checks implementation against this plan, snapshot integrity,
audit scope wording, path handling, inert fixture non-certification and exact
wheel provenance. Resolve findings before final full pytest, Ruff, lock and diff
checks. Record new collection count honestly; QA coverage may increase it even
while the broader test-consolidation target remains open. If this local driver
passes, mark only S1-06A complete. The fixed four-cell OS/Python matrix, real
transport/lifecycle checks, current live certification and merged-source gates
remain separate and pending until actually exercised.

## Adversarial review disposition

`oaic_stage1_adversarial_fallback` approved the bounded plan with two required
clarifications: reject every existing output-path entry including dangling
symlinks, and verify the loaded CLI module origin as well as package origin.
Revision 2 incorporates both. Snapshot verification and implementation review
remain separate gates; this review is not runtime acceptance.

Implementation review additionally required exact status schema/protocol and
run/model/index/start/report/completion consistency. These checks and regression
cases are implemented. Revision 3's blocked IPv6 capability accounting was
independently approved after the locked dependency and actual audit stack matched;
its implementation must cover wrong family/origin/address, repeats and an extra
outbound event independently before another acceptance run.

## Accepted local evidence and remaining gates

The exact built wheel and 94 locked runtime dependencies were installed offline
in a separate, non-editable CPython 3.12.13 environment. Its 81 ORI package files
match the source. The driver ran outside the Git worktree and venv with spaced
paths, an empty credential environment and dotenv discovery disabled. All ten
probes passed. Each status probe recorded one blocked local IPv6 capability
check; ordinary probes recorded no outbound attempts. No bind was permitted.
The intentional audit self-test is accounted for separately.

A second isolated installation with one deliberately altered package file was
rejected before CLI probes; the accepted installation remained unchanged. Earlier
failed checks (platform discovery and unclassified dependency capability check)
remain diagnostic evidence, not acceptance passes. Independent source, installed
file, log, receipt and archive-hash reconciliation passed. The private archive
contains 71 verified files and remains under ignored results.

Ruff, frozen lock and diff checks pass. Manual secret/private-path pattern review
of the new public files found no matches; automated container scanning was not
available because its Docker daemon was stopped. No service was started. Keep
automated scanning pending in the broader release gate, not silently passed.

Only S1-06A's local driver slice is complete. Clean OS/Python matrix acceptance,
live graph certification, real provider/MCP qualification, broad Stage 1 cleanup,
and deferred signing/publication remain outstanding. This driver does not create
provider readiness or certified benchmark results from its inert status fixtures.
