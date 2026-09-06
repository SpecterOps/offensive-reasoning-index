# Next provider fix: internal-error attribution preparation

September 6. Read-only S1-05A F03 follow-up, not an approved coding plan.
Before implementation, read the exact supporting boundaries and produce a
decision-complete plan with independent challenge. No code changes here.

The adapter currently converts unexpected exceptions into retryable provider
errors. V2 Direct converts every returned `ModelResponse.error` into infrastructure;
setting only nonretryable metadata would not yield HARNESS_ERROR. Do not substitute
a retry flag for execution-class correctness.

Recommended direction to challenge: keep ModelResponse and V1 serialization/outcomes
unchanged, but make unrecognized adapter exceptions raise a distinct internal
adapter error with original cause. Known actual HTTPX/OpenAI/Anthropic exceptions
retain typed provider metadata; match actual classes, not arbitrary class names
or a forged status_code attribute. Existing ProviderContractError handling stays.

Wrapping matters: a bare TimeoutError propagated during schema retry can enter the
whole-task timeout branch. A distinct internal error should reach harness
containment instead. V1 call_model already catches exceptions and can preserve
legacy MODEL_ERROR behavior. V2 campaign owns durable harness containment; the
standalone Direct helper need not return a sample for every internal defect.

CodexResponseStreamError currently mixes failure/incomplete/missing-terminal/empty
states. Preserve that explicit legacy compatibility category for this bounded F03
change; F04 must split its meanings separately. Do not accidentally reclassify all
Codex failures as harness defects. Likewise, declared ProviderProtocolError stays
provider-attributable; an untranslated raw parser exception remains a separate
parser-boundary defect rather than being guessed from its text.

Supporting closures identified by the source auditor:

- adapter.call_provider_text and _provider_exception_metrics: conversion policy.
- adapter.call_model, V1 runner/grader and inspect_runtime serialization/adapter
  route: existing catch, no query on missing Cypher, MODEL_ERROR compatibility.
- model_runtime Direct provider call: recognized throws become infrastructure;
  unknown throws propagate; all returned errors remain infrastructure.
- campaign_runner per-task containment and contain_model_runtime_exception:
  HARNESS_ERROR construction, durable attempt checkpointing, later-task continuation.
- _schema_only_retry and its caller exception handling: internal versus provider
  versus task-deadline separation, preservation of first-turn evidence/usage.

The future plan must cover actual adapter-through-runtime paths with fake SDKs,
not substitute an already-failed transport result. Required obligations include
ValueError/AttributeError/TypeError/bare TimeoutError, spoofed SDK name/status,
real synthetic SDK/HTTPX errors, contract and Codex compatibility, cancellation,
V1 serialization and MODEL_ERROR, and V2 single durable harness attempt with no
query, verdict or infrastructure retry, invalid campaign and safe later-task
continuation/resume behavior. Schema retry must preserve the first transcript and
known usage, produce harness failure for internal defects, and retain genuine
cancellation/deadline behavior.

Existing prose-only external ModelResponse errors must remain nonretryable
infrastructure; the new internal exception should not reinterpret third-party
legacy responses. No on-disk schema migration or V1 outcome rename is proposed.
Shared runtime fingerprint changes require fresh V2 output/readiness; no resume
bypass. Full native-loop sequencing, Codex F04 semantics and partial-usage recovery
remain distinct work. No tests, external requests or source edits were performed
by this preparation.
