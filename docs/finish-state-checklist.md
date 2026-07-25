# Direct Query Containment v3 — Finish-State Checklist

## Policy and classification

- [x] Bump the direct-query policy identity to `bloodhound-cysql-direct-v3`.
- [x] Add a configurable recursive-expansion complexity budget, defaulting to 256 units.
- [x] Calculate complexity from effective hop bounds and relationship-type alternatives.
- [x] Reject recursive lower bounds above the hop limit, including open-ended forms such as `*100..`.
- [x] Reject the observed GPT-5.6 Sol greedy query before it reaches BloodHound.
- [x] Keep ordinary BloodHound HTTP 400 syntax failures classified as `CYPHER_ERROR`.
- [x] Classify BloodHound's explicit query-complexity response as `QUERY_TOO_EXPENSIVE`.

## Containment behavior

- [x] Serialize direct-query execution so only one untrusted query reaches BloodHound at a time.
- [x] Execute admitted queries once with separate server and client deadlines.
- [x] Quarantine an exact query fingerprint after a BloodHound timeout or complexity rejection.
- [x] Reject a quarantined query without sending it to BloodHound again.
- [x] Health-check BloodHound after a potentially destabilizing query failure.
- [x] Open the circuit and record later tasks as unexecuted infrastructure failures if BloodHound is unhealthy.
- [x] Scope deny-cache reuse to the exact manifest fingerprint and policy version.

## Configuration and documentation

- [x] Expose all containment limits through shared YAML configuration.
- [x] Update the public example config and operator runbooks with the v3 policy and complexity limit.
- [x] Preserve the original run identity in generated campaign configs when CLI overrides are used.
- [x] Document that a changed policy, manifest, model, or campaign requires a new output directory.

## Regression and acceptance evidence

- [x] Add exact-query, lower-bound bypass, syntax-error, complexity-response, and quarantine regression tests.
- [x] Replay policy v3 over all 84 completed direct-campaign queries.
- [x] Confirm the replay newly rejects six risky/error-producing queries and zero previously correct queries.
- [x] Generate complex seed 4401 deterministically and match the checked-in ZIP SHA-256.
- [x] Preflight all 42 direct and 62 MCP tasks with zero errors or warnings.
- [x] Verify the live BloodHound instance is healthy and the graph passes all 30 planted-path checks.
- [x] Run direct smoke controls: perfect 42/42, and expected outcomes for hallucination, wrong, syntax-error, and empty models.
- [x] Run the complete test suite, Ruff, `git diff --check`, and a secret scan.
- [x] Complete an independent review and resolve its run-identity finding.
