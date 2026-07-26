# Secret scan report — offensive-reasoning-index

## Action

`local /Users/turbo/.codex/worktrees/ef8f/offensive-reasoning-index execute`

## Executive summary

No secret findings were detected. TruffleHog scanned the complete local
filesystem checkout, including current uncommitted V2 changes, and found zero
verified or unverified secrets. Gitleaks independently scanned 106 commits and
approximately 2.75 MB with redaction enabled and found zero leaks.

## Commands

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
secrets. It logged non-fatal Brotli decode errors for three generated
`.venv` binary/cache files; Gitleaks completed independently with zero findings.

## Findings

None.

## Next steps

Rerun this scan after any later merge/rebase and before publishing the v2 pull
request.
