# Direct Query Containment Completion Checklist

This checklist records the completed direct-query containment work for the
complex-v1 campaign. It does not convert the 42-task development corpus into the
future official 100-task suite, and it does not retroactively rescore or modify
frozen policy-v1 result artifacts.

Authoritative BloodHound references:

- [Supported Cypher Syntax](https://bloodhound.specterops.io/analyze-data/explore/cypher-supported)
- [Search with Cypher](https://bloodhound.specterops.io/analyze-data/explore/cypher-search)
- [Run a Cypher query API](https://bloodhound.specterops.io/reference/cypher/run-a-cypher-query)

## Policy and runtime

- [x] Preserve the policy-v1 campaign as immutable development evidence.
- [x] Confirm that rejected queries were not executed, admitted queries executed
  once, and BloodHound remained healthy in the contained run.
- [x] Re-check the policy assumptions against the current official BloodHound
  CySQL and query-timeout documentation.
- [x] Introduce a new policy version so policy-v1 deny caches and checkpoints
  cannot be reused under changed semantics.
- [x] Treat inline property maps and equivalent `WHERE` property filters
  consistently for standalone node enumeration.
- [x] Require exact endpoint selectors for recursive traversals; permit
  aggregate-only output or a bounded result only for non-recursive relationship
  enumeration. Ordinary boolean/property filters do not make a traversal
  selective.
- [x] Canonicalize query fingerprints across comments, keyword case, and
  formatting while preserving case-sensitive identifiers and literal values.
- [x] Prove that policy rejection and deny-cache quarantine never execute a
  BloodHound query.
- [x] Prove that admitted queries execute once with both the documented server
  timeout and the longer client deadline.
- [x] Prove that an unhealthy post-error health check opens the campaign circuit
  and records later tasks as unexecuted infrastructure outcomes.

## Task and scorer contracts

- [x] Rewrite every planted `path_exists` prompt whose wording claims global
  enumeration so that it names the exact planted source and target being graded.
- [x] Add structural preflight checks for anchored prompt scope instead of using
  generic words such as “privileged” as a proxy for a missing answer contract.
- [x] Keep genuinely global `node_set` tasks global and reference-defined.
- [x] Generate the complex seed-4401 development corpus and require direct and
  MCP task preflights to report zero errors and zero warnings.
- [x] Run perfect, wrong-answer, empty-answer, and containment regression
  controls so contract cleanup cannot silently weaken grading.

## Documentation and compatibility

- [x] Update public-safe configuration examples and runbooks to the new policy
  version.
- [x] Document the standalone-node versus traversal selectivity rule and the
  fingerprint canonicalization boundary.
- [x] Document that a policy-version, manifest, model, or run-name change
  requires a new output directory and fresh artifacts.

## Acceptance gates

- [x] Focused containment, task-generation, preflight, and scoring tests pass.
- [x] Full `pytest` suite passes.
- [x] Ruff passes for `src`, `scripts`, and `tests`.
- [x] Fresh complex generation succeeds and its SharpHound archive validates all
  30 planted paths.
- [x] Live BloodHound health and exact-manifest ingest verification pass.
- [x] A fresh 42-task direct mock/perfect campaign completes without
  `QUERY_TOO_EXPENSIVE`, infrastructure, timeout, or circuit-open outcomes.
- [x] Independent code review has no unresolved correctness or fairness findings.
- [x] The final diff is secret-safe, limited to the containment/task-contract
  scope, and committed with a verified signature.

Completed state:

- Policy version: `bloodhound-cysql-direct-v2`
- Direct preflight: 42 tasks, 0 errors, 0 warnings
- MCP preflight: 62 tasks, 0 errors, 0 warnings
- Fresh generation: `complex-v1-seed-4401` succeeded
- BloodHound health: pass
- Ingest verification: pass, 30/30 planted paths
