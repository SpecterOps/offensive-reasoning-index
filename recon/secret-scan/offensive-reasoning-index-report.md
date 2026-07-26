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

## Next steps

Rerun this scan after any later merge/rebase and before publishing the v2 pull
request. Keep scorer-only artifacts and model configs ignored, and continue
loading BloodHound credentials only from the external `.env`.
