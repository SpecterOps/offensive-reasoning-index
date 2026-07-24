# ORI benchmark hardening runbook

## Offline answer scoring

Use `ori score-answers` to grade structured answers without launching a model campaign:

```bash
ori score-answers \
  --manifest datasets/phase4-v1_manifest.json \
  --answers answers.json \
  --track mcp \
  --output scorer_projection.json
```

The answers file may be either a list of answer objects or an object with an `answers` list. Each answer object should include:

- `task_id`
- `final_answer` (or `answer`), using the MCP final JSON contract
- optional `reference_nodes` / `reference_node_names`
- optional `ref_error`

The output projection includes per-task `reference_nodes`, `answer_nodes`, `missing_reference_nodes`, `missing_required_nodes`, `extra_valid_nodes`, `hallucinated_nodes`, `metrics`, `details`, and `ref_error`.

## Preflight consistency checks

Run a lightweight scorer/task consistency check before campaigns:

```bash
ori preflight-tasks --manifest datasets/phase4-v1_manifest.json --track mcp --output preflight.json
```

Pass `--valid-nodes valid_nodes.json` when you have a graph inventory dump; the checker will flag contract nodes missing from that inventory.

- `ORI_MCP_NO_PROGRESS_TIMEOUT_SECONDS` configures the native Ollama MCP activity watchdog (default 300s).

## Failure taxonomy

- `CYPHER_ERROR`: model/tool query error, including structured BloodHound syntax/query errors.
- `QUERY_TOO_EXPENSIVE`: the model query was rejected by the versioned direct
  safety policy or BloodHound explicitly terminated that admitted query at the
  server timeout.
- `INFRA_ERROR`: BloodHound/runtime availability issue.
- `MCP_TURN_TIMEOUT`: native MCP model turn produced no token/tool/log progress before the watchdog expired.
- `NO_PROGRESS_TIMEOUT`: generic activity watchdog timeout.
- `SAMPLE_TIMEOUT`: sample-level timeout placeholder/future subtype.
- `OLLAMA_STREAM_TIMEOUT`: Ollama stream timeout placeholder/future subtype.
- `HALLUCINATION`: final answer named a node that is absent from the valid graph inventory. Real graph nodes outside the reference are reported as `extra_valid_nodes`, not hallucinations.

## Reporting metrics

MCP summaries separate reasoning quality from reliability:

- `completed_samples`
- `correct_completed`
- `reasoning_accuracy = correct_completed / completed_samples`
- `effective_accuracy = correct / total_samples`
- `infra_failure_rate`
- `tool_error_rate`
- `timeout_rate`

## BloodHound Cypher runtime guidance

### Direct Cypher containment

Direct Cypher is checked only after generation, so the benchmark prompt receives
no safety hints. ORI does not rewrite or repair the answer. The
`bloodhound-cysql-direct-v2` policy rejects non-read-only statements,
unselective recursive expansions, raw open-ended wildcard path enumeration,
open-ended `allShortestPaths`, excessive recursive bounds/patterns, oversized
queries/results, broad relationship enumeration, and broad labeled or unlabeled
node enumeration. Recursive selectors must bind an endpoint of the path; a
disconnected selector cannot make a separate expansion safe. For standalone
node sets, inline property maps and equivalent scalar `WHERE` predicates are
both valid filters. Recursive traversals always need an exact endpoint `name`
or `objectid`; aggregate output and `LIMIT` do not make an unanchored expansion
safe. Non-recursive relationship enumeration may instead use aggregate-only
output, including a pure `WITH count(...)` scalar projection, or an explicit
result-stage `LIMIT`. Mixed aggregate projections that retain graph entities do
not qualify. Ordinary property filters are not treated as exact traversal
selectors. A `WITH` clause starts a new containment stage. Exact selectors
propagate only when the selected variable is explicitly projected by name,
alias, or `WITH *`; discarded bindings cannot authorize later enumeration.
`UNION` is rejected because it is outside the documented BloodHound direct-query
subset and would create an independent branch with separate selectivity.

The policy applies only to untrusted model output. Trusted benchmark reference
queries are preflighted as task contracts and execute with the same BloodHound
server and client deadlines, but they are not assigned model-attributable policy
penalties.

Admitted model queries are serialized and executed exactly once. ORI sends
BloodHound's official `Prefer: wait=N` header, uses a slightly longer client
deadline, and records whether the query executed. A server query timeout
quarantines its fingerprint for the manifest/policy pair. A transport, server,
rate-limit, client-timeout, or authentication failure triggers a cheap health
check; an unhealthy result opens the campaign circuit and later samples are
recorded as unexecuted `INFRA_ERROR` placeholders.

Fingerprint canonicalization removes comments and formatting and normalizes
known Cypher keyword case. It deliberately preserves identifiers and literals,
including their case. A policy-version, manifest, model, or run-name change
requires a new output directory and new checkpoint/deny-cache artifacts.

Direct CSV, summary, telemetry, and checkpoint artifacts record:

- query execution and attempt count;
- query fingerprint and safety-policy version/rule;
- post-failure BloodHound health and circuit state;
- policy rejection, server timeout, circuit skip, and greedy-query counts.

The current policy accepts all 42 reference queries generated by
`complex-v1-seed-4401`, including the selective Tier 6
`shortestPath(...[*1..12]...)` contracts. The exact Phase 0 raw wildcard
regression is rejected before any BloodHound request.

Use BloodHound's current documentation as the syntax and API authority:

- [Supported Cypher Syntax](https://bloodhound.specterops.io/analyze-data/explore/cypher-supported)
- [Search with Cypher](https://bloodhound.specterops.io/analyze-data/explore/cypher-search)
- [Run a Cypher query API](https://bloodhound.specterops.io/reference/cypher/run-a-cypher-query)

### MCP guidance

The MCP system prompt now reminds models to avoid known BloodHound CE pitfalls:

- never duplicate `RETURN` columns; alias repeated/derived expressions
- prefer scalar columns (`u.name`, `g.name`, `c.name`) over `nodes(p)` / list projections
- avoid unsupported `UNION` in the CE API path
- use `COALESCE(list_prop, [])` for nullable list properties
- revise structured `syntax_error` / `query_error` responses instead of retrying the same query
- keep final `node_names` limited to task-required graph-valid nodes unless the contract allows optional nodes

## Contracts

Phase 4 ADCS/composite tasks can derive answer contracts from manifest `critical_nodes`; explicit per-task `metadata.answer_contract` is also supported with `required_nodes`, `optional_nodes`, `forbidden_nodes`, `required_edges`, and `grade_mode`.
