# Complex v1 Development Campaign Freeze

Status: frozen development evidence
Campaign: `complex-v1-seed-4401-gpt56sol-vs-gpt55`
Freeze date: 2026-07-23
Source commit: `a296b81c95149ee366486abad0af2807ff7490a9`

## Decision

The first GPT-5.6 Sol versus GPT-5.5 campaign is preserved as
`complex-v1 development`. It is a regression and provenance artifact, not an
official model ranking.

Do not:

- retroactively rescore these outputs under a later scorer;
- combine the direct and MCP tracks;
- report a winner from this campaign;
- treat the direct artifacts as a completed two-model comparison;
- compare these results with a later protocol unless the compatibility
  fingerprint is identical.

Later benchmark fixes must create a new product revision and a new result
directory. Historical artifacts remain immutable.

## Frozen Inputs

| Artifact | SHA-256 |
|---|---|
| `complex-v1-seed-4401.zip` | `a00e60e0f7ba8a02bcfee74e0ef1b99d7e46107d25b5172ffb1231e51f8f5edb` |
| `complex-v1-seed-4401_manifest.json` | `dd79ececd086485b7c25f64e1795830b55248aa02a4a2cb0c4691ba037d3ec4c` |
| `models-gpt56sol-vs-gpt55.yaml` | `51537149fc27572f865479fd14c3d1076e11296c1405f585c6877634ab37a14b` |

The frozen NAS bundle contains the dataset ZIP, matching manifest, sanitized
model configuration, task preflight reports, raw Inspect archives, telemetry,
per-model CSVs, summaries, an inventory, and bundle-level checksums.

## Artifact Characterization

### Task preflight

- Direct: 42 tasks, zero errors, 25 warnings.
- MCP: 62 tasks, zero errors, 29 warnings.

These checks established structural usability only. They did not prove that
the runtime, task wording, answer contracts, or scorer semantics were suitable
for an official comparison.

### Direct track

The preserved direct evidence is partial and infrastructure-affected:

- only a GPT-5.6 Sol Inspect archive is present;
- the archive contains 26 of 42 task samples;
- 25 samples were scored `QUERY_TOO_EXPENSIVE`;
- one sample is unscored;
- no GPT-5.5 direct artifact is present;
- no direct per-model or summary CSV was produced.

BloodHound became unhealthy during the run, so these outcome labels cannot be
treated as reliable model-query classifications. The direct track is invalid
for ranking and exists only as failure evidence for query containment, circuit
breaking, and typed error attribution.

### MCP track

Both MCP runs reached all 62 expected tasks and their outcome accounting
balanced:

| Model | Correct | Other outcomes | Development score |
|---|---:|---:|---:|
| GPT-5.6 Sol | 44 | 17 incorrect, 1 hallucination | 70% |
| GPT-5.5 | 46 | 15 incorrect, 1 loop exhausted | 74% |

These percentages are preserved exactly as produced, but they are not an
official ranking. Review of the campaign found benchmark-contract defects that
can produce both false positives and false negatives.

## Regression Characterization

The characterization cases in
`tests/fixtures/complex_v1_development/regressions.json` freeze the defects that
complex v2 must address:

1. BloodHound or transport failure misattributed as query expense.
2. Prompt wording not aligned with the scored answer contract.
3. A single reference witness treated as the only acceptable route.
4. Recall-only set scoring accepting extra valid-but-wrong entities.
5. Live and offline scoring deriving evidence differently.
6. Direct, transitive, and effective MCP semantics left implicit.
7. Unbounded enumeration creating unsafe queries or unusable answers.
8. MCP finalization driven by the wrong progress counter.

The fixture pack is intentionally a characterization contract. Corrected v2
behavior must be implemented in new tests without changing this historical
record.

## Phase 0 Exit Criteria

Phase 0 is complete when:

- this decision and regression ledger are version controlled;
- the original result artifacts and matching inputs are stored together;
- the archive passes a secret scan;
- a complete file inventory and SHA-256 manifest are present;
- the NAS copy independently verifies against that manifest;
- the local result copies are removed only after NAS verification;
- matching operational notes exist in AgentVault and the Personal Vault.
