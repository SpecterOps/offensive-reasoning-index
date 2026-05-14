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
- `QUERY_TOO_EXPENSIVE`: model-generated Cypher classified as expensive/non-viable.
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

The MCP system prompt now reminds models to avoid known BloodHound CE pitfalls:

- never duplicate `RETURN` columns; alias repeated/derived expressions
- prefer scalar columns (`u.name`, `g.name`, `c.name`) over `nodes(p)` / list projections
- avoid unsupported `UNION` in the CE API path
- use `COALESCE(list_prop, [])` for nullable list properties
- revise structured `syntax_error` / `query_error` responses instead of retrying the same query
- keep final `node_names` limited to task-required graph-valid nodes unless the contract allows optional nodes

## Contracts

Phase 4 ADCS/composite tasks can derive answer contracts from manifest `critical_nodes`; explicit per-task `metadata.answer_contract` is also supported with `required_nodes`, `optional_nodes`, `forbidden_nodes`, `required_edges`, and `grade_mode`.
