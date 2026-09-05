# OAIC progress and integration ledger

Updated: 2026-09-05. Contract: [execution plan](2026-09-05-oaic-execution-plan.md).
This ledger is public-safe; machine-specific paths and operational receipts remain
in private results. No provider calls have been authorized by a readiness result.

## Current state

Phase P1 in progress. Later dependent implementation remains gated by reviewed,
merged V30 and controlled-host acceptance. The new progress branch preserves the
existing 29-commit development lineage above master without rewriting it.

| Package | Status | Owner | Evidence / next action |
| --- | --- | --- | --- |
| P0-W01 durable plan | complete | integration | Approved contract saved with this ledger |
| P0-N01/N02 mobile pairing/test | pending user | user/integration | Native readiness question issued; delivery not verified |
| P1-W01 inventory | complete | branch reviewer | Full ancestry/patch/lock comparison below; no missing patch identified |
| P1-W02 consolidated worktree | running | integration | Clean branch created from master and fast-forwarded to 0f0eaa8; push pending |
| P1-W03 V30 independent review | running | independent reviewer | Review scope 10a979e..0f0eaa8; no provider calls |
| P1-W04 host acceptance | running | operations | Host reachable; old checkout at 8ad6f94; real-process tests being added |
| P1-W05 PR/merge | blocked | integration/user | GitHub CLI SAML authorization missing; connector cannot access repository |
| P2 cleanup | pending dependency | engineering | Requires P1 gate |
| P3 local Qwen | blocked | operations/user | Known reservation wrappers invoke a retired inert script; governed successor unidentified |
| P4 library/selector | pending dependency | compiler | Requires stable P1/P2/P3 interfaces |
| P5 native MCP/providers | pending dependency | runtime | Requires stable P1/P2/P3 interfaces |
| P6 scorecard/budget | pending dependency | operations | Requires stable P1/P2 interfaces |
| P7 release qualification | pending dependency | QA | Requires P4/P5/P6 |
| P8 campaign | pending dependency | operator | Requires merged release and exact admission manifest |
| P9 evidence freeze | pending dependency | release | Requires valid campaign evidence |
| P10 offline package | pending dependency | presentation | Existing historical assets preserved |
| P11 delivery | pending date | user | Physical delivery is user-owned |
| P12 OpenGraph design | pending dependency | architecture | Requires stable V30 boundary |

## Baseline evidence

- Remote master: `1247c009ab1ea4817eb8b99adcea2e9ddbc7ded9`.
- Integrated development: `0f0eaa8e10f65c897c2a07f03033a31b874e5a01`.
- Previous local validation of that unchanged source: 1,042 tests passed,
  focused V30 212 passed; Ruff/lock/diff checks passed.
- SSH commit signature verified using the already configured signing public key
  and a temporary command-local allowed-signers file. Global trust configuration
  was not changed; its configured allowed-signers path is absent.

## Preservation

- Original development checkout and source branches retained.
- Untracked public-release report and earlier expansion plan retained in original
  checkout; their historical/in-progress status is not current qualification.
- Original output directory retained, excluded from this integration pending inventory.
- Historical V29 artifacts unchanged; no claim that they qualify V30.

## Input blockers

- GitHub CLI authenticated repository API access requires organization SAML
  authorization. Git fetch succeeds; the configured connector returns inaccessible
  repository errors. No authentication values are copied into this ledger.
- Phone alert delivery requires end-to-end user confirmation.
- The known GPU reservation/release wrappers invoke an intentionally retired
  implementation that returns success without acquiring a reservation. Bounded
  read-only discovery found no governed successor. Do not treat exit zero as
  reservation evidence, reactivate old installers, or load the model.

## Findings

Independent code review is pending. Lack of a finding here is not an approval or
assertion that the code contains no defects.

## Branch disposition (P1-W01)

Matching local/remote refs share the same disposition unless separately noted.

| Ref | Disposition | Evidence |
| --- | --- | --- |
| master / origin/master / origin/HEAD | incorporated | 1247c00 ancestor |
| codex/v30-scoring-output-compliance | incorporated | 0f0eaa8 baseline |
| codex/benchmark-correctness-v29-fixes | incorporated | 9b52b44 ancestor |
| codex/integrate-v28-open-world | incorporated | 1568007 ancestor |
| codex/merge-all-changes | incorporated | 224678c ancestor |
| codex/nous-api-key / feat/nous-portal-ox-alpha | incorporated | cf458fb ancestor |
| codex/openrouter-api-key | incorporated | 661ee7c ancestor |
| codex/ori-tier6-and-repeat | incorporated | f5e6511 ancestor |
| codex/tier6-sharphound-projection | incorporated | 01c6cf9 ancestor |
| codex/uvx-git-mcp-launcher | incorporated | 7085ee2 ancestor, including local extra commit |
| origin/codex/uvx-git-mcp-launcher | incorporated | 5abb9e1 ancestor |
| feat/benchmark-correctness-v2 | incorporated | d86ce95 ancestor |
| feat/v2-campaign-operations | incorporated | 10a979e ancestor |
| fix/direct-query-containment | incorporated | 0a56029 ancestor |
| fix/openai-provider-runtime | incorporated | d6ac198 ancestor |
| fix/sharphound-contract-hardening | incorporated | a296b81 ancestor |
| origin/feat/phase4b-diagnostics-benchmark-registry | incorporated | e4bf483 ancestor |
| origin/fix/phase4-mcp-failure-attribution | incorporated | 6ca03d4 ancestor |
| origin/repair/phase4b-v2-review-findings-t_38c7fa14 | incorporated | 2d31ebe ancestor |
| hermes/v30-harddeadline | incorporated | 8ad6f94 ancestor |
| origin/chore/dependabot-uv-updates | incorporated | 0b6c6f7 ancestor |
| chore/complex-v1-phase0-freeze | equivalent | 2946c73 patch matches contained 68f4cde |
| origin/docs/sanitized-inference-config-examples | ported | 5b13f1b additions in 224678c, followed by credential/Nous improvements |
| antonetta/feature/open-world-discovery-v1 | superseded | 66db53f old standalone implementation replaced by a003b37 V28-native discovery |

All origin dependabot branch tips are represented or superseded by the lockfile:

| Package | Branch requested | Current locked |
| --- | --- | --- |
| aiohttp | 3.14.3 | 3.14.3 |
| anthropic | 0.87.0 | 0.116.0 |
| cryptography | 50.0.0 | 50.0.0 |
| idna | 3.15 | 3.18 |
| mcp | 1.28.1 | 1.28.1 |
| pydantic-settings | 2.14.2 | 2.14.2 |
| pygments | 2.20.0 | 2.20.0 |
| pyjwt | 2.13.0 | 2.13.0 |
| pytest | 9.0.3 | 9.1.1 |
| python-multipart | 0.0.31 | 0.0.32 |
| soupsieve | 2.8.4 | 2.8.4 |
| starlette | 1.3.1 | 1.3.1 |
| urllib3 | 2.7.0 | 2.7.0 |

The second linked worktree has an uncommitted local inference configuration edit
(12 added/10 deleted lines); it is preserved without importing private settings.
Original output contains two generated files totaling approximately 3.7 MB;
metadata only was inspected, and neither is staged.

## Current validation

- Fresh isolated environment installed with `uv sync --frozen` on Python 3.12.13.
- Gitleaks scanned `origin/master..HEAD`: 28 non-merge commits, no leaks.
- Focused supervisor/status/durability/runtime/scoring gate: 92 passed in 7.82s.
- Ruff, lockfile validation and diff checks passed in the isolated worktree.
- Full pytest running in the fresh Python 3.12 environment.

## Controlled-host discovery

Read-only identity checks succeeded on both the benchmark host and GPU host.
The existing benchmark checkout is clean at detached 8ad6f94, not consolidated
HEAD. Its prior artifacts do not qualify the new source. A verified absolute uv
executable exists on the benchmark host but is absent from its noninteractive
PATH. No provider calls, model loads, reservation actions or service changes ran.
