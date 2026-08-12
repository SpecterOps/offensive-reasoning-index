# Secret scan report — offensive-reasoning-index

## Action

`local /Users/turbo/.codex/worktrees/ef8f/offensive-reasoning-index execute`

## Executive summary

No secret findings were detected. The original TruffleHog scan covered the
complete local checkout and found zero verified or unverified secrets. Gitleaks
independently scanned Git history with redaction enabled and found zero leaks.
Follow-up scans after the direct-runtime and adapter-certifier changes also
reported zero findings.

## Original commands

```bash
which trufflehog
which gitleaks
which gh
trufflehog filesystem . --only-verified --json
gitleaks detect --source . --report-format json \
  --report-path /tmp/ori-v2-gitleaks-20260726.json \
  --redact --no-banner --exit-code 1
jq '{finding_count:length, files: ([.[].File] | unique), rules: ([.[].RuleID] | unique)}' \
  /tmp/ori-v2-gitleaks-20260726.json
```

All three tools were available. TruffleHog 3.96.0 scanned 41,212 chunks
(402,351,474 bytes) in 9.3 seconds and reported zero verified or unverified
secrets. It logged non-fatal Brotli decode errors for three generated `.venv`
binary/cache files; Gitleaks completed independently with zero findings.

## Findings

None.

## Direct runtime follow-up

After the direct-result contract and comparator-v3 correction, Gitleaks 8.30.1
was run again in both Git-history and `--no-git` worktree modes with full
redaction. It scanned 109 commits and approximately 16.49 MB of worktree
content. Both scans exited 0 with zero findings.

## Adapter-certifier-v3 follow-up

The current tracked working tree plus non-ignored untracked source files were
copied to a temporary scan root. This deliberately excluded the ignored
`results/` tree and the external BloodHound `.env` from the source-control gate.

```bash
git ls-files -co --exclude-standard -z |
  rsync -a --from0 --files-from=- ./ <temporary-source>/
trufflehog filesystem <temporary-source> --json
gitleaks dir <temporary-source> --redact \
  --report-format json --exit-code 0
```

Both commands exited successfully on 2026-07-26. TruffleHog reported zero
findings and zero verified secrets. Gitleaks reported zero findings.

## MCP runtime-contract-v7 follow-up

After the pagination, finalization, timeout-accounting, response-proof, and
runtime-fingerprint changes, including the native watchdog classification and
health-gated live-certification retry, Gitleaks scanned all 114 commits with
redaction enabled:

```bash
gitleaks git --redact --no-banner --report-format json \
  --report-path <temporary-directory>/gitleaks.json .
```

The scan completed successfully on 2026-07-27, examined approximately 3.03 MB
of Git history, and reported zero findings. A second scan copied every tracked
and non-ignored untracked worktree file to an isolated temporary directory;
Gitleaks examined approximately 2.69 MB there and also reported zero findings.

## MCP failure-hardening V8 follow-up

After the provider/tool deadline, cancellation durability, lifetime retry,
claim-relevance, strict output, schema-retry, and campaign-accounting changes,
TruffleHog 3.96.0 scanned the changed source and documentation surfaces:

```bash
trufflehog filesystem README.md docs models.v2.example.yaml src tests \
  --results=verified --json --fail --no-update
```

The command exited 0 on 2026-07-28. It scanned 692 chunks (7,217,130 bytes)
and reported zero verified secrets and zero findings.

## MCP literal-proof V9 follow-up

After the flattened literal-row projection, stable ordering, public-selector,
abstract-principal, compiler-bound, and stale-artifact invalidation changes,
TruffleHog 3.96.0 rescanned the publishable source surfaces:

```bash
trufflehog filesystem README.md docs models.v2.example.yaml src tests \
  --results=verified --json --fail --no-update
```

The final command exited 0 on 2026-07-28. It scanned 707 chunks (7,399,437 bytes)
and reported zero verified or unverified secrets.

## MCP proof-binding V10 follow-up

After the mixed graph-plus-scalar route projection, strict selector/projection
binding, stable-order provenance, companion count/page population binding, and
distinct certification-count changes, TruffleHog 3.96.0 rescanned the
publishable source surfaces:

```bash
trufflehog filesystem README.md docs models.v2.example.yaml src tests \
  --results=verified --json --fail --no-update
```

The command exited 0 on 2026-07-28. It scanned 786 chunks (8,178,993 bytes) and
reported zero verified or unverified secrets.

## Benchmark-correctness V11 follow-up

After the AcceptanceSpec, fixed public-capacity, route-policy, bounded-negative,
CE local-group identity, MCP proof/finalization, and prompt-surface changes,
TruffleHog 3.96.0 rescanned the publishable source surfaces:

```bash
trufflehog filesystem README.md docs models.v2.example.yaml src tests \
  --results=verified --json --fail --no-update
```

The final command exited 0 on 2026-07-29 after the runner-v8 no-model readiness
change. It scanned 825 chunks (8,606,745 bytes) and
reported zero verified or unverified secrets. It logged one non-fatal Brotli
decode error for a generated pytest bytecode cache.

As an independent worktree-content gate, all tracked and non-ignored untracked
files were copied into an isolated temporary directory and scanned with
Gitleaks 8.30.1 using redaction:

```bash
git ls-files -co --exclude-standard -z |
  rsync -a --from0 --files-from=- ./ <temporary-source>/
gitleaks dir <temporary-source> --redact --no-banner \
  --report-format json --report-path <temporary-source>/gitleaks.json \
  --exit-code 1
```

Gitleaks scanned 2,918,908 bytes, exited 0, and reported no leaks.

## Benchmark-correctness V15 follow-up

After the endpoint-bound path, scalar identity, graph-attested extra evidence,
stable property registry, and archive/live normalization changes, both scanners
were run against an isolated copy of every tracked and non-ignored untracked
source file. This source-control gate excluded ignored runtime artifacts,
`.venv`, and the external BloodHound `.env`.

```bash
git ls-files -co --exclude-standard -z |
  tar --null -T - -cf <temporary-source>.tar
tar -xf <temporary-source>.tar -C <temporary-source>
trufflehog filesystem <temporary-source> \
  --force-skip-binaries --json
gitleaks detect --source <temporary-source> --no-git --redact \
  --report-format json --report-path <temporary-gitleaks-report>
```

On 2026-07-30, TruffleHog 3.96.0 scanned 390 chunks and 3,713,283
bytes across 159 source files, reporting zero verified or unverified secrets.
Gitleaks 8.30.1 scanned approximately 3.03 MB, exited 0, and reported zero
leaks. A preliminary whole-checkout scan is not used as acceptance evidence:
it included `.venv` and generated caches and therefore produced dependency
noise, while the isolated source scan was clean.

## Benchmark-correctness V16 follow-up

After the independent-review corrections for broader negative proofs, MCP path
lineage, graph-path cardinality, page-window normalization, and finalization
fingerprinting, the isolated tracked and non-ignored source scan was repeated
with the same procedure.

On 2026-07-30, TruffleHog 3.96.0 scanned 390 chunks and 3,734,328 bytes,
reporting zero verified or unverified secrets. Gitleaks 8.30.1 exited 0 and
reported zero findings. Ignored private run artifacts, `.venv`, generated
caches, and the external BloodHound `.env` were not part of the publishable
source-control surface.

## Benchmark-correctness V17 follow-up

After the path-selector scope, alpha-equivalent page population, and
relationship-contract fingerprint corrections, the isolated publishable source
scan was repeated. On 2026-07-30, TruffleHog 3.96.0 scanned 391 chunks and
3,746,723 bytes across 159 files, reporting zero verified or unverified
secrets. Gitleaks 8.30.1 exited 0 and reported zero findings.

## Benchmark-correctness V19 follow-up

After the final multiline projection, count/page distinctness, circuit-open
attribution, bounded-negative hop-ceiling, prompt-contract, and V19
certification changes, the isolated tracked and non-ignored source scan was
repeated with the same archive procedure.

On 2026-07-31, TruffleHog 3.96.0 scanned 395 chunks and 3,788,730 bytes,
reporting zero verified or unverified secrets. Gitleaks 8.30.1 exited 0 and
reported zero findings. Ignored private V19 run/certification artifacts, the
virtual environment, generated caches, and the external BloodHound `.env` were
not part of the publishable source-control surface.

## Benchmark-correctness V28 follow-up

After the final prompt-authority, query-proof, identity, grouped-selector, and
failed/truncated receipt-state corrections, the isolated publishable-source
scan was repeated against every tracked and non-ignored untracked file.

On 2026-07-31, TruffleHog 3.96.0 scanned 399 chunks and 3,866,405 bytes
across 159 files, reporting zero verified or unverified secrets. Gitleaks
8.30.1 scanned approximately 3.15 MB, exited 0, and reported zero findings.
Ignored private V28 run/certification artifacts, the virtual environment,
generated caches, and the external BloodHound `.env` were not part of the
publishable source-control surface.

## Next steps

Rerun this scan after any later merge/rebase and before publishing the v2 pull
request. Keep scorer-only artifacts and model configs ignored, and continue
loading BloodHound credentials only from the external `.env`.
