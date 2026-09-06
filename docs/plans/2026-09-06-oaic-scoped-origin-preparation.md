# Next provider fix: scoped origin admission preparation

September 6. Read-only preparation, not an approved implementation plan. Follow
S1-05D final acceptance with a fresh detailed plan and independent challenge.

The S1-05A auditor traced all repository callers of
`resolve_openai_compat_credential`: Direct adapter, native compatible single-turn
helper and V2 readiness identity. The compatibility `openai_compat_api_key` wrapper
remains public; absence of a current production caller does not authorize removal.

The proposed next bounded fix targets OpenRouter/Nous credential admission only.
Preserve existing family classification and return the recognized family with no
key/source for insecure recognized origins. Do not reclassify rejection as generic:
that could allow a generic key to bypass the scoped-origin decision.

Proposed policy to challenge: HTTPS, absent/443 port, no userinfo; malformed or
out-of-range ports deny admission. Preserve existing OpenRouter apex/subdomain
classification and both existing Nous hosts rather than silently withdrawing a
supported configuration. This is compatibility preservation, not a claim that
every subdomain is an endorsed provider endpoint. Preserve Nous key priority and
all generic explicit-key/custom-port/local-HTTP behavior. No additional IDNA,
trailing-dot or escaped-host aliases become trusted. Decide raw control-character
handling explicitly before implementation; do not accidentally inherit URL parser
cleanup as an admission policy. Path/query/fragment and redirects are separate.

Current consumers already deny absent scoped credentials before transport:
Direct yields nonretryable `PROVIDER_AUTH`; native single-turn raises
`ProviderAuthenticationError`; V2 readiness rejects absent credential source.
The next plan must prove these actual consumer boundaries with fake SDK/HTTPX
constructors, including no-client/no-request assertions, not just resolver tests.

Test ownership is expected in `test_provider_auth.py`, `test_provider_adapter.py`,
`test_mcp.py` and `test_provider_v2_config.py`. Preserve existing key-isolation and
S1-05D precedence assertions. Cover HTTPS/443/case, existing hosts, Nous fallback,
HTTP/other scheme/alternate-invalid-out-of-range ports, all userinfo forms, no
generic-key rescue, local/custom generic positive controls, and lookalike hosts.
Read the exact native helper/test closure before finalizing the next plan.

Changing `provider_auth.py` invalidates the shared V2 runtime fingerprint for all
older campaigns. No resume bypass. Codex/Anthropic credential behavior, redirects,
retry accounting, full native loops and full F01 resolution remain separate work.
No source/test edits, provider calls, credential-file reads or external changes
were performed by this preparation.
