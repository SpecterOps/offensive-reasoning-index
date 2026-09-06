# Local OAIC checkpoint secret scan

Scope: staged ORI source, tests, documentation, examples and dependency metadata.
Ignored credentials, private results and generated datasets were not staged.
No remote operations or credential verification were performed.

Command: `gitleaks git --pre-commit --staged --redact=100 --no-banner --no-color --verbose`

The scanner reported two `generic-api-key` findings. Both were inspected:

- `src/ori/eval/anthropic_binding.py:34`: pinned SDK source-file SHA-256 checksum, not a credential.
- `src/ori/eval/v2/oaic_recipes.py:59`: synthetic delegation task identifier, not a credential.

No actual secrets were identified. Scanner exit status was 1 for those findings;
no rules were disabled or findings silently suppressed. This scan does not certify
release readiness or replace the final publication review.
