# ORI Eval Outcome Taxonomy

## Top-level outcomes

### CORRECT
The final answer matched the reference/contract for the task.

### INCORRECT
The model returned a structured answer, but it did not satisfy the reference/contract. For MCP runs, inspect `failure_subtype` for a more precise reason.

### PARSE_FAIL
The runner could not extract the required answer shape from the model response.

### CYPHER_ERROR
The model-generated direct Cypher failed to execute because of query syntax or semantic problems.

### QUERY_TOO_EXPENSIVE
The model-generated direct Cypher could not be executed by BloodHound CE because it was likely too expensive or otherwise non-viable.

### HALLUCINATION
The answer referenced graph node names that are not present in the valid node universe.

### MODEL_ERROR
The upstream model/API call failed before a usable response was available.

Examples:
- provider timeout
- model endpoint unavailable
- Ollama/model call failure

### LOOP_EXHAUSTED
The MCP/tool-use controller reached its turn budget without a final structured JSON answer.

This is not a model/API failure and should not be counted as `MODEL_ERROR`. It usually means the model kept searching, repeated queries, or failed to synthesize an answer from evidence already gathered.

### INFRA_ERROR
Benchmark/reference infrastructure prevented fair grading.

Examples:
- reference Cypher could not be evaluated because BloodHound was unavailable
- Inspect sample was interrupted before producing required scoring metadata
- MCP/BloodHound infrastructure errors prevented a structured final answer

## MCP `failure_subtype`

MCP runs may keep `outcome=INCORRECT` while adding a more specific `failure_subtype`.

### NO_PATH_REPORTED
The reference path exists, but the model explicitly returned `path_found=false`.

Typical cause: premature negative conclusion from too little evidence.

### WRONG_PATH
The model returned graph-valid nodes, but there is no overlap with the required reference/contract nodes.

Typical cause: broad query found a plausible but irrelevant privileged path.

### INCOMPLETE_ANSWER
The model found some required nodes but missed other required critical nodes.

Typical cause: partial evidence gathering or final answer omitted required context.

## MCP evidence-depth fields

### successful_tool_results
Number of tool responses that were non-empty and not classified as tool/policy/infra errors.

### finalization_guard_used
Whether the MCP runtime injected the near-limit final-answer instruction and disabled further tools for that turn.

### loop_exhaustion_with_evidence
Whether a run still exhausted the loop despite having at least one successful tool result.

### minimum_evidence_satisfied
Simple first-pass evidence-depth signal. Currently true when an MCP trajectory used at least three total tools and at least two Cypher queries. This is diagnostic only, not a grading rule.
