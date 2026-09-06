# S1-05E — scoped compatible credential origin admission

Revision 2, September 6. Status: complete after independent plan, implementation
and final evidence review. Pre-edit 160-file inventory matched S1-05D and baseline
collection contained 1,120 tests.
This follows accepted S1-05D and resolves the OpenRouter/Nous origin-admission
portion of S1-05A F02. It does not close Codex/Anthropic credential findings.

## Contract and compatibility decisions

Preserve endpoint family classification exactly. Before releasing a scoped
OpenRouter or Nous key, require the parsed origin to use HTTPS, no username or
password, and absent/443 port. Invalid/out-of-range ports deny release. Return
the existing frozen credential object with the original family and absent
key/source on denial; never reclassify it as generic or try another family's key.

Preserve existing OpenRouter apex/subdomain classification and both exact Nous
hostnames. This avoids an unreviewed endpoint removal; it does not endorse every
subdomain as an inference endpoint. Keep Nous primary/alias key priority. Generic
endpoints retain explicit generic-key support on custom ports and local HTTP.
Official OpenAI policy and helper remain unchanged. No normalized tool facade,
new provider API, local-host requirement, agent dependency or new package.

This is parsed-origin admission, not a new URL grammar. Paths/query/fragments,
raw whitespace/control normalization, IDNA/trailing-dot aliases and multi-`@`
syntax are not changed. Existing parser exceptions outside invalid ports are not
newly normalized or claimed fixed. The native helper already normalizes URL
suffixes before credential resolution; tests exercise its resulting origin. No
claim that arbitrary invalid raw URLs now receive uniform typed errors.

Valid requests retain behavior. HTTP/alternate-port/userinfo recognized-provider
configurations intentionally stop receiving scoped keys. Users must use their
provider's secure supported origin; generic keys cannot bypass recognized-family
denial. No insecure opt-in is introduced. Generic custom servers remain supported.

Changing `provider_auth.py` changes the shared V2 runtime fingerprint. All
old-runtime campaigns need fresh output/readiness and any additionally stale
certification must be renewed; no resume or evidence migration bypass.

## Ownership and implementation boundary

Root owns `src/ori/eval/provider_auth.py`, this plan, progress/review ledgers and
the operator credential notes in `examples/inference/README.md`.
Test owner may edit only `tests/test_provider_auth.py`, `tests/test_provider_adapter.py`,
`tests/test_provider_v2_config.py`, `tests/test_mcp.py`, and a plain immutable
case catalog `tests/support/provider_origins.py`. Independent reviewer owns
read-only plan/diff/evidence acceptance. Preserve all unrelated dirty work and
S1-05D changes. No other production module is writable in this slice.

Add a private pure predicate for HTTPS/port/userinfo, catching invalid parsed
ports and returning false. Invoke it only for already-classified OpenRouter/Nous
before any environment-key lookup. Reuse the existing absent-credential return
shape. Do not refactor official OpenAI or change public helper signatures. No
consumer edits should be necessary: existing Direct and native gates reject
missing credentials before client construction; readiness rejects absent source.
If a consumer fails that contract, stop and revise the plan rather than expanding
implementation implicitly.

## Tests and isolation

All new tests use synthetic credentials and fake SDK/HTTPX clients. Clear scoped
and generic keys, relevant base-URL env vars, then set only test sentinels. Do not
read auth files, run login or full graph readiness. Keep existing test bodies and
assertions; add genuine multi-obligation scenarios, not count-only wrappers.

1. Resolver scenario: for each of OpenRouter apex and existing subdomain, Nous
   inference and portal hosts, assert HTTPS default/443 and case-insensitive
   scheme/host release the expected scoped key/source. Remove primary Nous key
   and assert alias fallback. Check secret-free repr.
2. Rejection scenario: each host crossed with HTTP, FTP, port8443, malformed port,
   port65536, username-only, username/password and empty userinfo. Assert original
   family, no key/source, wrapper returns None despite all keys being populated.
3. Compatibility scenario: generic custom HTTP/HTTPS ports and local endpoints
   retain explicit generic key; lookalikes do not borrow scoped keys; existing
   official OpenAI TLS policy unchanged. Existing S1-05D cases remain green.
4. Direct consumer scenario: reject each invalid origin before fake SDK constructor
   or request; assert `PROVIDER_AUTH`, false retryability and empty text. Valid
   origins use the exact scoped key and selected destination in a fake response.
5. Native MCP single-turn scenario: same rejection matrix before fake HTTPX
   constructor/post, raises `ProviderAuthenticationError`; valid origins pass
   exact scoped bearer and retain normalized response. Does not claim full-loop
   persistence or redirect containment.
6. V2 no-model readiness scenario: real typed config and `_model_readiness`, only
   compatible models; denied origins raise `V2CampaignRunError`, valid origins
   record family/source without secret values. Guard provider constructors to
   prove zero client/request activity. Do not call graph-aware `run-v2`.

Use pytest subtests for matrix cells so one failed case does not hide the rest.
Inputs can be local small constants; no imports from another collected test file.
The owned support catalog contains only immutable host/URL/expected-family and
synthetic-key tuples; no fixture execution, assertions, imports of collected
tests or provider activity. Each consumer keeps its own interception and assertions.

## Evidence, red/green and exit

### Frozen case map and isolation

Hosts in this exact order (IDs H0–H3): `openrouter.ai`,
`api.openrouter.ai`, `inference-api.nousresearch.com`, `portal.nousresearch.com`.
H0/H1 expect openrouter/OPENROUTER_API_KEY/test-router; H2/H3 expect
nous/NOUS_API_KEY/test-nous. All suffixes below are literal `/v1`.

Valid forms in order per host (V0–V2): `https://HOST/v1`,
`https://HOST:443/v1`, `HTTPS://UPPERCASE_HOST/v1`. Twelve positive cases total.
Denied forms in order per host (D0–D7): `http://HOST/v1`, `ftp://HOST/v1`,
`https://HOST:8443/v1`, `https://HOST:bad/v1`, `https://HOST:65536/v1`,
`https://user@HOST/v1`, `https://user:pass@HOST/v1`, `https://@HOST/v1`.
Thirty-two negative cases total. Order is host-major; IDs combine host/form.
Resolver test one runs V cases then two separate Nous-host alias-fallback cases
(primary deleted, NOUS_PORTAL_API_KEY/test-nous-alias remains): 14 subtests.
Resolver test two runs all 32 D cases. Each of three consumer tests runs all 32
D cases then all 12 V cases: 44 subtests each, 132 consumer subtests total.
Direct expected denial: empty text, PROVIDER_AUTH, retryable false. Native:
ProviderAuthenticationError. Readiness: V2CampaignRunError. Every denied consumer
cell asserts separate constructor and request counters both zero, including
readiness; fake clients contain request methods so these are independent checks.

Compatibility controls, in order, eight subtests:
`http://custom.example:8080/v1`, `https://custom.example:8443/v1`,
`http://127.0.0.1:8080/v1`, `https://openrouter.ai.example.test/v1`,
`https://inference-api.nousresearch.com.example.test/v1`,
`https://api.openai.com/v1`, `http://api.openai.com/v1`,
`https://api.openai.com:8443/v1`. First five expect generic/test-generic with the
explicit generic key and None after removing only that key; last three expect
openai with test-openai only for HTTPS default-port. All other family keys remain
populated to prove no borrowing. Total new map: six tests, 186 subtests.

Every cell uses a new `monkeypatch.context()` inside its subtest block and fresh
capture counters. Clear OPENAI_API_KEY, OPENAI_COMPAT_API_KEY, OPENROUTER_API_KEY,
NOUS_API_KEY, NOUS_PORTAL_API_KEY, OPENAI_BASE_URL and OPENAI_COMPAT_BASE_URL;
set only test-openai/test-generic/test-router/test-nous/test-nous-alias sentinels.
Replace SDK and HTTPX constructors before any tested function is called. No
constructor/request fake ever delegates to the real implementation.

Private fault control: independently for each consumer, inject RuntimeError on
its imported resolver's first D0 call only; forward subsequent calls to the real
resolver. Run the exact test with the same fake transport fixtures and record
subtest reports. Require exactly one failed subtest and 43 passing subtests,
including the final H3-V2 positive case, plus the expected failed parent. Restore
through the private pytest plugin teardown, never edit production for this control.
Then rerun the unmodified test and require all 44 pass. This proves later-case
execution after an unexpected failure, not merely continuation after an assertion.

Capture pre-edit source inventory and owned-file snapshots; match S1-05D's final
160-file boundary. Record baseline collection before test edits. Add tests first;
run the new scenarios against unchanged source. Origin rejection must fail and
positive controls pass. Inspect failures to exclude a broken fake/config fixture.
Implement only after the planned failures are established.

Run the five S1-05A modules plus `test_mcp.py`. Independent reviewer inspects exact
consumer gates and interception boundaries. Then full pytest, Ruff across src,
scripts and tests, offline lock check, diff check. Record actual counts, log
hashes and unchanged final validation inventory; no fabricated combined results.
Update operator-facing credential notes with parsed-origin policy and migration.
Independent final evidence review must approve before marking this portion closed.

## Stop conditions and residual work

Stop for unintended source drift, fake escape, credential discovery, other-provider
regressions, unknown compatibility impact or production scope expansion. No
provider/network requests, remote Git/signing/merge, live graph or host/service
operations. Readiness never authorizes usage. F01 remaining destinations, F02
Codex/Anthropic, F03–F06, full native-loop audit, local qualification, task-library
expansion and full Stage 1 remain open.

## Accepted completion evidence

Root's unchanged-source red run produced 128 failed denial subtests and four
failed parents, with all 58 positive/compatibility subtests passing. The production
change is confined to a parsed-origin predicate and pre-key-lookup denial in
provider_auth.py; no consumer production edits were needed. All 84 original test
functions remain unchanged. Six new scenarios add 186 explicit obligations, not
186 separately collected tests.

Focused validation: 144 tests and 186 subtests passed in 1.88s. Three independent
first-case fault controls each produced exactly one failed H0-D0, 43 later passes
in the approved order, one failed parent and zero network attempts. Each restored
baseline then passed all 44 cases with a passed parent and zero network attempts.
Full validation: 1,126 tests and 419 subtests passed in 249.23s. Ruff, offline lock
and diff checks passed. Independent reviewer reconciled all 161 final source/test/
script/lock files against the validation-start inventory and approved narrow closure.

Inventory SHA-256:
`a45ebade26356ef402e260afb7db0ee268287a155c6afce697fd47b979fd2ea3`.
Full-suite log SHA-256:
`2c6de66b2650140596a92268757419d50e6e81c21fbb1a7820de750a03364f66`.
Private logs/snapshots stay outside public artifacts. This closes only the parsed
OpenRouter/Nous origin-admission portion of F02, not the remaining provider audit
or Stage 1. Operator documentation is updated; no external operations were performed.
