"""Model-backed protocol-v2 execution without legacy task or scorer dispatch."""

from __future__ import annotations

import asyncio
import json
import re
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from itertools import permutations
from typing import Any, Literal

import httpx
from inspect_ai.tool import ToolCallError
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from pydantic import Field, model_validator

from ori.eval.adapter import ModelResponse, call_provider_text
from ori.eval.direct_query_safety import normalize_query_for_fingerprint
from ori.eval.mcp_runtime import (
    DEFAULT_MCP_OLLAMA_READ_TIMEOUT_SECONDS,
    OPENAI_COMPAT_TELEMETRY_AUTO,
    MCPNoProgressTimeout,
    MCPServerBundle,
    MCPToolInfrastructureError,
    _run_ollama_mcp_loop,
    _run_openai_compat_mcp_loop,
)
from ori.eval.provider_contract import (
    ProviderApiSurface,
    ProviderAuthenticationError,
    ProviderCapabilityError,
    ProviderContractError,
    ProviderProtocolError,
    resolve_api_surface,
    validate_release1_api_surface,
)
from ori.relationships import canonical_relationship_kind, relationship_contract

from .compiler import DIRECT_RESULT_CONTRACT_VERSION
from .direct_adapter import DirectV2Outcome, _literal_node_collections
from .evidence import (
    EvidenceIdentityCatalogError,
    EvidenceNormalizationError,
    validate_and_normalize_evidence,
)
from .fingerprint import canonical_sha256
from .graph import entity_property_fact_key
from .identity import IdentityResolver
from .mcp import (
    SCHEMA_ONLY_RETRY_INSTRUCTION,
    EvidenceEvent,
    EvidenceEventKind,
    MCPToolLoop,
    ToolObservation,
    classify_evidence_event,
    classify_tool_observation,
    validate_certified_mcp_loop,
)
from .mcp_adapter import MCPV2Outcome
from .public_surfaces import (
    PublicSurface,
    assert_solver_visible,
    build_solver_visible_envelope,
)
from .query_contract import negative_query_scope_mode
from .runtime import V2RuntimeSurface, run_direct_task_v2, run_mcp_task_v2
from .schema import (
    CapabilityProfile,
    DirectExecutionReceipt,
    EntityRef,
    ExecutionClass,
    GraphFactRegistry,
    NegativeReasonCode,
    OracleBundle,
    StrictModel,
    TaskBundle,
)
from .scoring import SampleOutcomeCode, SampleResult


class V2ModelRuntimeError(ValueError):
    """Raised when a model-facing v2 contract is mixed, stale, or unsupported."""


MCP_RESULT_CONTRACT_VERSION = "ori-mcp-result-contract-v22"
QuerySelector = tuple[Literal["objectid", "name"], str]


class DirectSubmissionV2(StrictModel):
    """The model's query plus claim-only assertions not derivable from graph rows."""

    query: str = Field(strict=True, min_length=1)
    assertion: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def query_is_nonempty(self) -> DirectSubmissionV2:
        if not self.query.strip():
            raise ValueError("direct submission query cannot be blank")
        return self


class MCPToolAuditReceipt(StrictModel):
    """Private, replayable record of one MCP operation and its classification."""

    sequence: int = Field(strict=True, ge=1)
    tool_name: str
    operation: str
    arguments: dict[str, Any]
    result_text: str
    tool_error: str | None = None
    observation: ToolObservation
    event: EvidenceEvent
    receipt_fingerprint: str

    @model_validator(mode="after")
    def fingerprint_matches(self) -> MCPToolAuditReceipt:
        expected = canonical_sha256(
            self,
            exclude_fields=("receipt_fingerprint",),
        )
        if self.receipt_fingerprint != expected:
            raise ValueError("MCP tool audit receipt fingerprint mismatch")
        return self


class ProviderRunRecord(StrictModel):
    """Private model-call evidence retained beside the exact v2 checkpoint."""

    task_id: str
    task_fingerprint: str
    provider_model: str
    surface: str
    raw_response: str
    response_digest: str
    tokens_input: int = Field(strict=True, ge=0)
    tokens_output: int = Field(strict=True, ge=0)
    elapsed_seconds: float = Field(strict=True, ge=0)
    provider_error: str | None = None
    provider_metrics: dict[str, Any] = Field(default_factory=dict)
    direct_query_digest: str | None = None
    direct_receipt: DirectExecutionReceipt | None = None
    mcp_events: tuple[EvidenceEvent, ...] = ()
    mcp_tool_receipts: tuple[MCPToolAuditReceipt, ...] = ()
    mcp_finalization: dict[str, Any] | None = None
    mcp_transcript: tuple[dict[str, Any], ...] = ()
    transcript_digest: str | None = None
    record_fingerprint: str

    @model_validator(mode="after")
    def fingerprint_matches(self) -> ProviderRunRecord:
        expected = canonical_sha256(
            self,
            exclude_fields=("record_fingerprint",),
        )
        if self.record_fingerprint != expected:
            raise ValueError("provider run record fingerprint mismatch")
        return self


class V2ModelTaskCancelled(asyncio.CancelledError):
    """Cancellation carrying the private in-flight attempt for durable checkpointing."""

    def __init__(
        self,
        sample: SampleResult,
        provider: ProviderRunRecord,
    ) -> None:
        self.sample = sample
        self.provider = provider
        super().__init__("v2 model task cancelled after preserving partial state")


def _record(
    *,
    task: TaskBundle,
    model: str,
    surface: str,
    response: ModelResponse,
    direct_query: str | None = None,
    direct_receipt: DirectExecutionReceipt | None = None,
    mcp_events: tuple[EvidenceEvent, ...] = (),
    mcp_tool_receipts: tuple[MCPToolAuditReceipt, ...] = (),
    mcp_finalization: Mapping[str, Any] | None = None,
    mcp_transcript: tuple[dict[str, Any], ...] = (),
    transcript_digest: str | None = None,
) -> ProviderRunRecord:
    payload = {
        "task_id": task.task_id,
        "task_fingerprint": task.task_fingerprint,
        "provider_model": model,
        "surface": surface,
        "raw_response": response.raw_text,
        "response_digest": canonical_sha256(response.raw_text),
        "tokens_input": response.tokens_input,
        "tokens_output": response.tokens_output,
        "elapsed_seconds": response.elapsed_seconds,
        "provider_error": response.error,
        "provider_metrics": dict(response.provider_metrics),
        "direct_query_digest": (
            canonical_sha256(direct_query) if direct_query is not None else None
        ),
        "direct_receipt": direct_receipt,
        "mcp_events": mcp_events,
        "mcp_tool_receipts": mcp_tool_receipts,
        "mcp_finalization": (dict(mcp_finalization) if mcp_finalization is not None else None),
        "mcp_transcript": mcp_transcript,
        "transcript_digest": transcript_digest,
        "record_fingerprint": "0" * 64,
    }
    payload["record_fingerprint"] = canonical_sha256(
        payload,
        exclude_fields=("record_fingerprint",),
    )
    return ProviderRunRecord.model_validate(payload)


def _transcript_payload(messages: list[Any]) -> tuple[dict[str, Any], ...]:
    payload: list[dict[str, Any]] = []
    for message in messages:
        if isinstance(message, Mapping):
            payload.append(dict(message))
            continue
        dumped = getattr(message, "model_dump", lambda **_: None)(mode="json")
        payload.append(dict(dumped) if isinstance(dumped, Mapping) else {"rendered": str(message)})
    return tuple(payload)


def _provider_infrastructure_details(
    exc: Exception,
) -> tuple[str, bool] | None:
    """Return a stable provider subtype and retryability for SDK/HTTP failures."""

    if isinstance(exc, ProviderAuthenticationError):
        return "PROVIDER_AUTH", False
    if isinstance(exc, ProviderCapabilityError):
        return "PROVIDER_CAPABILITY", False
    if isinstance(exc, ProviderProtocolError):
        return "PROVIDER_PROTOCOL", False
    if isinstance(exc, ProviderContractError):
        return "PROVIDER_CONTRACT", False

    if isinstance(exc, httpx.TimeoutException):
        return "PROVIDER_TIMEOUT", True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in {401, 403}:
            return "PROVIDER_AUTH", False
        if status == 408:
            return "PROVIDER_TIMEOUT", True
        if status == 429:
            return "PROVIDER_RATE_LIMIT", True
        if status >= 500:
            return "PROVIDER_SERVER", True
        if status in {404, 405, 406, 415}:
            return "PROVIDER_CAPABILITY", False
        return "PROVIDER_PROTOCOL", False
    if isinstance(exc, httpx.RequestError):
        return "PROVIDER_TRANSPORT", True

    module = type(exc).__module__.split(".", 1)[0]
    name = type(exc).__name__
    if module != "openai":
        return None
    if name == "APITimeoutError":
        return "PROVIDER_TIMEOUT", True
    if name == "APIConnectionError":
        return "PROVIDER_TRANSPORT", True
    status = getattr(exc, "status_code", None)
    if name in {"AuthenticationError", "PermissionDeniedError"} or status in {401, 403}:
        return "PROVIDER_AUTH", False
    if name == "RateLimitError" or status == 429:
        return "PROVIDER_RATE_LIMIT", True
    if name == "InternalServerError" or (isinstance(status, int) and status >= 500):
        return "PROVIDER_SERVER", True
    if status == 408:
        return "PROVIDER_TIMEOUT", True
    return None


def _provider_response_infrastructure_details(
    response: ModelResponse,
) -> tuple[str, str, bool]:
    """Recover typed provider failure metadata without guessing from prose."""

    metrics = response.provider_metrics
    scope_value = metrics.get("infra_scope")
    subtype_value = metrics.get("infra_error_subtype")
    retryable_value = metrics.get("infra_retryable")
    scope = scope_value if isinstance(scope_value, str) else "provider"
    subtype = subtype_value if isinstance(subtype_value, str) else None
    retryable = retryable_value if isinstance(retryable_value, bool) else None
    if subtype is not None and retryable is not None:
        return scope, subtype, retryable
    # A legacy or third-party adapter that returns only prose must not gain a
    # retry policy by matching words in an error message. Fail closed until the
    # adapter supplies the typed metadata above.
    return scope, subtype or "PROVIDER_UNTYPED", False


def _failure_response(
    *,
    partial: ModelResponse | None,
    model: str,
    parse_stage: str,
    error: str,
    elapsed_seconds: float,
    metrics: Mapping[str, Any] | None = None,
) -> ModelResponse:
    merged_metrics = dict(partial.provider_metrics) if partial is not None else {}
    if metrics is not None:
        merged_metrics.update(dict(metrics))
    if partial is None:
        return ModelResponse(
            raw_text="",
            cypher=None,
            parse_stage=parse_stage,
            tokens_input=0,
            tokens_output=0,
            elapsed_seconds=elapsed_seconds,
            model=model,
            error=error,
            provider_metrics=merged_metrics,
        )
    return replace(
        partial,
        parse_stage=parse_stage,
        elapsed_seconds=max(partial.elapsed_seconds, elapsed_seconds),
        error=error,
        provider_metrics=merged_metrics,
    )


@dataclass(frozen=True, slots=True)
class ParsedJsonObject:
    payload: Mapping[str, Any]
    raw_protocol_compliant: bool
    format_normalized: bool


_SINGLE_JSON_FENCE = re.compile(
    r"\A\s*```json\s*\n(?P<payload>\{.*\})\s*\n```\s*\Z",
    re.IGNORECASE | re.DOTALL,
)


def _parse_json_object(text: str) -> ParsedJsonObject:
    stripped = text.strip()
    if not stripped:
        raise V2ModelRuntimeError("model output did not contain one JSON object")
    candidate = stripped
    normalized = False
    fence = _SINGLE_JSON_FENCE.fullmatch(text)
    if fence is not None:
        candidate = fence.group("payload").strip()
        normalized = True
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise V2ModelRuntimeError(
            "model output must be one JSON object or exactly one outer ```json fence"
        ) from exc
    if not isinstance(parsed, Mapping):
        raise V2ModelRuntimeError("model output must be exactly one JSON object")
    return ParsedJsonObject(
        payload=parsed,
        raw_protocol_compliant=not normalized,
        format_normalized=normalized,
    )


def _extract_json_object(text: str) -> Mapping[str, Any]:
    return _parse_json_object(text).payload


def _contains_object_candidate(text: str) -> bool:
    stripped = text.strip()
    return bool(stripped) and ("{" in stripped or "}" in stripped)


def _answer_scalar_values(value: Any) -> tuple[str, ...]:
    values: list[str] = []

    def visit(item: Any) -> None:
        if isinstance(item, Mapping):
            for nested in item.values():
                visit(nested)
        elif isinstance(item, (list, tuple)):
            for nested in item:
                visit(nested)
        elif isinstance(item, bool):
            values.append("true" if item else "false")
        elif isinstance(item, (str, int, float)):
            values.append(str(item).casefold())

    visit(value)
    return tuple(values)


def _retry_adds_answer_facts(
    malformed_output: str,
    retry_answer: Mapping[str, Any],
) -> bool:
    """Forbid a schema retry from introducing facts absent from the first output."""

    source = malformed_output.casefold()
    source_variants = (source, source.replace("\\\\", "\\"))
    return any(
        not any(value in candidate for candidate in source_variants)
        for value in _answer_scalar_values(retry_answer)
    )


def direct_submission_schema(task: TaskBundle) -> dict[str, Any]:
    """Derive the direct model contract solely from the public task."""

    assertion_properties: dict[str, Any] = {}
    assertion_required: list[str] = []
    if task.claim_kind == "decision":
        assertion_properties["decision"] = {"type": "boolean"}
        assertion_required.append("decision")
    elif task.claim_kind == "absence":
        assertion_properties.update(
            {
                "path_status": {"const": "no_path"},
                "negative_reason_codes": {
                    "type": "array",
                    "items": {
                        "enum": [reason.value for reason in NegativeReasonCode],
                    },
                    "uniqueItems": True,
                },
            }
        )
        assertion_required.extend(("path_status", "negative_reason_codes"))

    assertion_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": assertion_properties,
        "required": assertion_required,
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "query": {"type": "string", "minLength": 1},
            "assertion": assertion_schema,
        },
        "required": ["query", "assertion"],
    }


def _parse_direct_submission(
    text: str,
    task: TaskBundle,
) -> tuple[DirectSubmissionV2, ParsedJsonObject]:
    parsed = _parse_json_object(text)
    payload = parsed.payload
    schema = direct_submission_schema(task)
    try:
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(dict(payload))
    except (SchemaError, ValidationError) as exc:
        raise V2ModelRuntimeError(f"direct submission schema mismatch: {exc.message}") from exc
    assert_solver_visible(payload)
    return DirectSubmissionV2.model_validate(payload), parsed


def parse_direct_submission(text: str, task: TaskBundle) -> DirectSubmissionV2:
    return _parse_direct_submission(text, task)[0]


def _provider_request(task: TaskBundle, *, direct: bool) -> dict[str, Any]:
    envelope = build_solver_visible_envelope(
        task,
        surface=PublicSurface.PROVIDER_REQUEST,
    )
    task_contract: dict[str, Any] = {
        "track": envelope.track,
        "acceptance_spec": envelope.acceptance_spec.model_dump(mode="json"),
        "generic_instructions": envelope.generic_instructions,
    }
    payload = {
        # The question is sent once as the user message. Bounds live once in
        # the result contract and the answer shape lives once here. Public
        # artifact fingerprints are useful for provenance, not for solving.
        "task_contract": task_contract,
        "submission_schema": (direct_submission_schema(task) if direct else task.answer_schema),
    }
    if direct:
        payload["query_result_contract"] = _direct_query_result_contract(task)
    else:
        payload["evidence_result_contract"] = _mcp_evidence_result_contract(task)
    assert_solver_visible(payload)
    return payload


def _direct_query_result_contract(task: TaskBundle) -> dict[str, Any]:
    """Describe supported CySQL output without exposing scorer-only material."""

    common = {
        "one_statement": True,
        "return_only_answer_evidence": True,
        "identity_selector_rule": (
            "Use exact case-sensitive BloodHound label, relationship, and property "
            "identifiers. Query predicates must use the canonical name or objectid "
            "shown in the public task contract; display aliases are not alternate "
            "live graph property values."
        ),
        "unsupported_cysql": [
            "CALL",
            "UNION",
            "UNWIND",
            "CASE",
            "reduce()",
            "map literals",
            "list comprehensions",
            "labels()",
            "toString(Path)",
            "XOR",
        ],
    }
    if task.claim_kind == "set":
        claim = {
            "result_shape": (
                "Return only answer nodes as individual rows, for example "
                "RETURN entity ORDER BY entity.objectid. A single "
                "collect(entity) AS entities literal is also accepted."
            ),
            "auxiliary_nodes_forbidden": True,
        }
    elif task.claim_kind in {"count", "absence"}:
        claim = {
            "result_shape": (
                "Count exactly one named path from acceptance_spec.source_role to "
                "acceptance_spec.target_role over the complete public hop bound, "
                "using either the exact allowed outbound mechanisms or a wildcard/"
                "undirected stronger zero proof. Use only exact public endpoint "
                "selectors and no other filters. Bind those exact endpoints first, "
                "then use OPTIONAL MATCH for the named path so an absent path still "
                "returns a scalar row. Return exactly one non-negative scalar AS "
                "count; the bounded-negative route count must be zero."
                if task.claim_kind == "absence"
                else "Return exactly one non-negative scalar AS count."
            ),
        }
    else:
        claim = {
            "result_shape": (
                "Return an actual BloodHound path variable, for example "
                "RETURN p LIMIT 1. The path value itself preserves ordered edges; "
                "route tasks do not require globally sorting Path values. Return "
                "required supporting relationship variables separately and also "
                "return both endpoint node variables for every separately returned "
                "relationship; BloodHound otherwise omits endpoints needed to "
                "interpret that edge. Return a bounded path witness and let ORI "
                "validate that it is acyclic; do not add quadratic pairwise node-"
                "inequality predicates merely to prove path simplicity. Never "
                "construct JSON in Cypher."
            ),
            "recursive_limit_position": "after the final RETURN projection",
        }
    return {
        "version": DIRECT_RESULT_CONTRACT_VERSION,
        "claim_kind": task.claim_kind,
        "common": common,
        "claim": claim,
    }


def direct_system_prompt(task: TaskBundle) -> str:
    request = _provider_request(task, direct=True)
    return (
        "You are being evaluated on a controlled synthetic BloodHound CE graph. "
        "Write exactly one bounded read-only Cypher query that answers the public "
        "question. Return only one JSON object matching submission_schema. The query "
        "result must follow query_result_contract because ORI projects returned "
        "graph evidence itself. "
        "Use RETURN p for path queries so BloodHound returns ordered nodes and edges. "
        "That path value already preserves edge order; do not globally sort Path "
        "values or call toString() on a Path. Return both endpoint node variables "
        "with every supporting relationship projected separately. ORI validates "
        "whether the returned path is acyclic, so do not add quadratic pairwise "
        "node-inequality filters merely to prove simplicity. BloodHound CySQL also "
        "does not support reduce(). "
        "For set queries return only the answer nodes, not source or context nodes. "
        "Use exact case-sensitive BloodHound label, relationship, and property "
        "identifiers, and use the public canonical name or objectid in query "
        "predicates rather than a display alias. "
        "Do not use CALL, UNION, UNWIND, CASE expressions, map literals, list "
        "comprehensions, labels(), or XOR. Put a recursive route's ORDER BY and LIMIT "
        "after its final RETURN projection. shortestPath requires one variable-length "
        "pattern. The assertion object may contain only fields declared by "
        "submission_schema. Do not use write clauses, unbounded traversal, or hidden "
        "assumptions.\n\n" + json.dumps(request, sort_keys=True)
    )


def _mcp_evidence_result_contract(task: TaskBundle) -> dict[str, Any]:
    """Describe model-neutral MCP completeness evidence from public bounds."""

    bounds = task.binding.bounds
    common = {
        "read_only": True,
        "accepted_proof_tool": "cypher_query",
        "accepted_proof_operation": "run",
        "other_tools_are_exploratory_only": True,
        "truncation_rule": (
            "A truncated or incomplete result is not a proof. A later independently "
            "complete claim-bound result may supersede it, while any truncation after "
            "the latest complete proof revokes readiness."
        ),
        "identity_selector_rule": (
            "Query predicates must use the exact canonical BloodHound name or "
            "objectid shown in the public task contract, paired with that exact "
            "case-sensitive property key. Display aliases are accepted in the final "
            "answer only; they are not alternate live graph property values."
        ),
        "unsupported_cysql": [
            "CALL",
            "UNION",
            "UNWIND",
            "CASE",
            "reduce()",
            "map literals",
            "list comprehensions",
            "labels()",
            "toString(Path)",
            "XOR",
        ],
    }
    if task.claim_kind == "set":
        if bounds.require_total_count:
            cypher = {
                "count": (
                    "Run one count-only query returning exactly one non-negative scalar "
                    "literal over the same identity population and distinctness as the "
                    "entity pages."
                ),
                "pages": (
                    "Return one row per unique answer identity ordered by objectid using "
                    "the same population as the count and contiguous "
                    f"SKIP/LIMIT pages of {bounds.page_size}, starting at "
                    f"offset {bounds.result_offset}."
                ),
            }
        else:
            cypher = {
                "window": (
                    "Return exactly the declared deterministic answer window "
                    f"ordered by objectid with SKIP {bounds.result_offset} and "
                    f"LIMIT {bounds.page_size}."
                )
            }
        claim = {
            "cypher": cypher,
            "cypher_include_properties": False,
            "entity_rows": (
                "Return one row per answer entity using entity.objectid AS object_id "
                "and optionally entity.name AS name. ORDER BY entity.objectid or an "
                "alias directly bound to entity.objectid is equivalent. BloodHound "
                "label, relationship, and property identifiers are case-sensitive. "
                "Once this complete claim-bound window is proven, ORI mechanically "
                "materializes the final entities from the tool receipt instead of "
                "depending on a second model-authored copy of every identity."
            ),
        }
    elif task.claim_kind in {"count", "absence"}:
        claim = {
            "result_shape": (
                "Count exactly one named path from acceptance_spec.source_role to "
                "acceptance_spec.target_role over the complete public hop bound, "
                "using either the exact allowed outbound mechanisms or a wildcard/"
                "undirected stronger zero proof. Use only exact public endpoint "
                "selectors and no other filters. Bind those exact endpoints first, "
                "then use OPTIONAL MATCH for the named path so an absent path still "
                "returns a scalar row. Return exactly one non-negative scalar "
                "literal; zero is the bounded negative proof."
                if task.claim_kind == "absence"
                else "Use one count-only Cypher query returning exactly one "
                "non-negative scalar literal."
            )
        }
    else:
        claim = {
            "result_shape": (
                "Use one bounded claim-relevant Cypher query returning an actual "
                "ordered BloodHound path with the requested relationship witnesses. "
                "The Path value preserves edge order; do not sort Path values or call "
                "toString() on a Path, and do not use reduce(). ORI validates whether "
                "the returned witness is acyclic, so do not add quadratic pairwise "
                "node-inequality predicates merely to prove path simplicity."
            )
        }
    return {
        "version": MCP_RESULT_CONTRACT_VERSION,
        "claim_kind": task.claim_kind,
        "common": common,
        "claim": claim,
    }


def mcp_system_prompt(task: TaskBundle) -> str:
    request = _provider_request(task, direct=False)
    return (
        "You are being evaluated on a controlled synthetic BloodHound CE graph. "
        "Use only the provided read-only BloodHound tools. High-level tools may be "
        "used for exploration, but only the claim-bound cypher_query operation "
        "declared by evidence_result_contract establishes completion. Follow that "
        "contract exactly and set include_properties=false for set/count enumerations "
        "unless the public answer requires properties. A truncated or incomplete "
        "result is not proof; obtain a later independently complete proof before "
        "finalizing. Use exact case-sensitive BloodHound label, relationship, and "
        "property identifiers. Use only the public canonical name or objectid in "
        "query predicates; display aliases are only for the final answer. "
        "Do not use CALL, UNION, UNWIND, CASE expressions, reduce(), map literals, "
        "list comprehensions, labels(), toString(Path), or XOR. "
        "Finish with only one JSON object matching submission_schema. "
        "Use stable object IDs when available and include ordered edge witnesses for "
        "routes. Do not include commentary or markdown.\n\n" + json.dumps(request, sort_keys=True)
    )


def _model_infrastructure_sample(
    task: TaskBundle,
    oracle: OracleBundle,
    detail: str,
) -> SampleResult:
    return SampleResult(
        task_id=task.task_id,
        task_fingerprint=task.task_fingerprint,
        oracle_fingerprint=oracle.oracle_fingerprint,
        execution_class=ExecutionClass.INFRA_FAILURE,
        outcome=SampleOutcomeCode.INFRA_ERROR,
        detail=detail,
    )


def _model_output_invalid_sample(
    task: TaskBundle,
    oracle: OracleBundle,
    detail: str,
) -> SampleResult:
    return SampleResult(
        task_id=task.task_id,
        task_fingerprint=task.task_fingerprint,
        oracle_fingerprint=oracle.oracle_fingerprint,
        execution_class=ExecutionClass.MODEL_FAILURE,
        outcome=SampleOutcomeCode.OUTPUT_INVALID,
        output_compliant=False,
        detail=detail,
    )


def _interrupted_sample(
    task: TaskBundle,
    oracle: OracleBundle,
    detail: str,
) -> SampleResult:
    return SampleResult(
        task_id=task.task_id,
        task_fingerprint=task.task_fingerprint,
        oracle_fingerprint=oracle.oracle_fingerprint,
        execution_class=ExecutionClass.UNEXECUTED,
        outcome=SampleOutcomeCode.INTERRUPTED,
        detail=detail,
    )


def _task_timeout_sample(
    task: TaskBundle,
    oracle: OracleBundle,
    detail: str,
) -> SampleResult:
    return SampleResult(
        task_id=task.task_id,
        task_fingerprint=task.task_fingerprint,
        oracle_fingerprint=oracle.oracle_fingerprint,
        execution_class=ExecutionClass.MODEL_FAILURE,
        outcome=SampleOutcomeCode.TASK_TIMEOUT,
        detail=detail,
    )


def _task_timeout_provider_record(
    provider: ProviderRunRecord,
    *,
    detail: str,
    timeout_seconds: float,
) -> ProviderRunRecord:
    payload = provider.model_dump(mode="python")
    metrics = {
        key: value
        for key, value in provider.provider_metrics.items()
        if not key.startswith("infra_")
    }
    metrics.update(
        {
            "timeout_scope": "whole_task",
            "task_timeout_seconds": timeout_seconds,
        }
    )
    payload.update(
        {
            "elapsed_seconds": timeout_seconds,
            "provider_error": detail,
            "provider_metrics": metrics,
            "record_fingerprint": "0" * 64,
        }
    )
    payload["record_fingerprint"] = canonical_sha256(
        payload,
        exclude_fields=("record_fingerprint",),
    )
    return ProviderRunRecord.model_validate(payload)


def contain_model_runtime_exception(
    *,
    task: TaskBundle,
    oracle: OracleBundle,
    model: str,
    surface: str,
    error: Exception,
) -> tuple[SampleResult, ProviderRunRecord]:
    """Turn an unexpected task-runtime defect into durable campaign evidence."""

    detail = f"{type(error).__name__}: {error}"
    response = ModelResponse(
        raw_text="",
        cypher=None,
        parse_stage="harness_failure",
        tokens_input=0,
        tokens_output=0,
        elapsed_seconds=0.0,
        model=model,
        error=detail,
    )
    sample = SampleResult(
        task_id=task.task_id,
        task_fingerprint=task.task_fingerprint,
        oracle_fingerprint=oracle.oracle_fingerprint,
        execution_class=ExecutionClass.HARNESS_FAILURE,
        outcome=SampleOutcomeCode.HARNESS_ERROR,
        detail=detail,
    )
    return sample, _record(
        task=task,
        model=model,
        surface=surface,
        response=response,
    )


def unexecuted_model_record(
    *,
    task: TaskBundle,
    oracle: OracleBundle,
    model: str,
    surface: str,
    detail: str,
) -> tuple[SampleResult, ProviderRunRecord]:
    """Record a task that was intentionally stopped before any provider call."""

    response = ModelResponse(
        raw_text="",
        cypher=None,
        parse_stage="unexecuted",
        tokens_input=0,
        tokens_output=0,
        elapsed_seconds=0.0,
        model=model,
        error=detail,
        provider_metrics={
            "infra_scope": "bloodhound",
            "infra_error_subtype": "CIRCUIT_OPEN",
            "infra_retryable": True,
            "provider_called": False,
        },
    )
    sample = SampleResult(
        task_id=task.task_id,
        task_fingerprint=task.task_fingerprint,
        oracle_fingerprint=oracle.oracle_fingerprint,
        execution_class=ExecutionClass.UNEXECUTED,
        outcome=SampleOutcomeCode.CIRCUIT_OPEN,
        detail=detail,
    )
    return sample, _record(
        task=task,
        model=model,
        surface=surface,
        response=response,
    )


TextTransport = Callable[..., Awaitable[ModelResponse]]


def _json_schema_response_format(
    schema: Mapping[str, Any],
    *,
    name: str,
) -> dict[str, Any]:
    """Return the provider-neutral strict JSON-Schema response descriptor."""

    return {
        "name": name,
        "strict": True,
        "schema": dict(schema),
    }


async def _run_direct_model_task_v2_unbounded(
    *,
    coordinator: Any,
    task: TaskBundle,
    oracle: OracleBundle,
    resolver: IdentityResolver,
    model: str,
    model_base_url: str | None = None,
    ollama_options: dict[str, Any] | None = None,
    max_tokens: int = 2048,
    api_surface: ProviderApiSurface | str = ProviderApiSurface.AUTO,
    structured_output_mode: str = "prompt_local_validation",
    transport: TextTransport = call_provider_text,
) -> tuple[DirectV2Outcome | None, SampleResult, ProviderRunRecord]:
    """Call one model with a public direct contract, then execute via policy v3."""

    try:
        response = await transport(
            model=model,
            messages=[{"role": "user", "content": task.question}],
            system=direct_system_prompt(task),
            base_url=model_base_url,
            max_tokens=max_tokens,
            ollama_options=ollama_options,
            api_surface=api_surface,
            request_timeout_seconds=task.binding.bounds.timeout_seconds,
            structured_output_schema=(
                _json_schema_response_format(
                    direct_submission_schema(task),
                    name="ori_direct_submission",
                )
                if structured_output_mode == "json_schema"
                else None
            ),
        )
    except asyncio.CancelledError as exc:
        detail = "direct model request interrupted before completion"
        response = _failure_response(
            partial=None,
            model=model,
            parse_stage="direct_interrupted",
            error=detail,
            elapsed_seconds=0.0,
            metrics={
                "infra_scope": "operator",
                "infra_error_subtype": "INTERRUPTED",
                "infra_retryable": False,
            },
        )
        raise V2ModelTaskCancelled(
            _interrupted_sample(task, oracle, detail),
            _record(
                task=task,
                model=model,
                surface=V2RuntimeSurface.DIRECT.value,
                response=response,
            ),
        ) from exc
    except Exception as exc:
        infrastructure = _provider_infrastructure_details(exc)
        if infrastructure is None:
            raise
        subtype, retryable = infrastructure
        response = _failure_response(
            partial=None,
            model=model,
            parse_stage="direct_provider_infrastructure_failure",
            error=str(exc),
            elapsed_seconds=0.0,
            metrics={
                "infra_scope": "provider",
                "infra_error_subtype": subtype,
                "infra_retryable": retryable,
            },
        )
    if response.error:
        scope, subtype, retryable = _provider_response_infrastructure_details(
            response
        )
        response = replace(
            response,
            provider_metrics={
                **dict(response.provider_metrics),
                "infra_scope": scope,
                "infra_error_subtype": subtype,
                "infra_retryable": retryable,
            },
        )
        sample = _model_infrastructure_sample(task, oracle, response.error)
        return (
            None,
            sample,
            _record(
                task=task,
                model=model,
                surface=V2RuntimeSurface.DIRECT.value,
                response=response,
            ),
        )
    try:
        submission, parsed_submission = _parse_direct_submission(response.raw_text, task)
    except V2ModelRuntimeError as exc:
        sample = _model_output_invalid_sample(task, oracle, str(exc))
        return (
            None,
            sample,
            _record(
                task=task,
                model=model,
                surface=V2RuntimeSurface.DIRECT.value,
                response=response,
            ),
        )

    try:
        outcome, sample = await run_direct_task_v2(
            coordinator,
            query=submission.query,
            task=task,
            oracle=oracle,
            resolver=resolver,
            answer_payload=submission.assertion,
        )
        sample = sample.model_copy(
            update={
                "output_compliant": True,
                "output_normalized": parsed_submission.format_normalized,
            }
        )
    except asyncio.CancelledError as exc:
        detail = "direct query execution interrupted before completion"
        interrupted_response = _failure_response(
            partial=response,
            model=model,
            parse_stage="direct_interrupted",
            error=detail,
            elapsed_seconds=response.elapsed_seconds,
            metrics={
                "infra_scope": "operator",
                "infra_error_subtype": "INTERRUPTED",
                "infra_retryable": False,
            },
        )
        raise V2ModelTaskCancelled(
            _interrupted_sample(task, oracle, detail),
            _record(
                task=task,
                model=model,
                surface=V2RuntimeSurface.DIRECT.value,
                response=interrupted_response,
                direct_query=submission.query,
            ),
        ) from exc
    return (
        outcome,
        sample,
        _record(
            task=task,
            model=model,
            surface=V2RuntimeSurface.DIRECT.value,
            response=response,
            direct_query=submission.query,
            direct_receipt=outcome.receipt,
        ),
    )


async def run_direct_model_task_v2(
    *,
    coordinator: Any,
    task: TaskBundle,
    oracle: OracleBundle,
    resolver: IdentityResolver,
    model: str,
    model_base_url: str | None = None,
    ollama_options: dict[str, Any] | None = None,
    max_tokens: int = 2048,
    api_surface: ProviderApiSurface | str = ProviderApiSurface.AUTO,
    structured_output_mode: str = "prompt_local_validation",
    transport: TextTransport = call_provider_text,
) -> tuple[DirectV2Outcome | None, SampleResult, ProviderRunRecord]:
    """Run one Direct task inside its solver-visible whole-task deadline."""

    timeout_seconds = task.binding.bounds.timeout_seconds
    deadline = asyncio.timeout(timeout_seconds)
    try:
        async with deadline:
            return await _run_direct_model_task_v2_unbounded(
                coordinator=coordinator,
                task=task,
                oracle=oracle,
                resolver=resolver,
                model=model,
                model_base_url=model_base_url,
                ollama_options=ollama_options,
                max_tokens=max_tokens,
                api_surface=api_surface,
                structured_output_mode=structured_output_mode,
                transport=transport,
            )
    except V2ModelTaskCancelled as exc:
        if not deadline.expired():
            raise
        detail = "direct task execution budget exhausted"
        return (
            None,
            _task_timeout_sample(task, oracle, detail),
            _task_timeout_provider_record(
                exc.provider,
                detail=detail,
                timeout_seconds=timeout_seconds,
            ),
        )
    except TimeoutError:
        if not deadline.expired():
            raise
        detail = "direct task execution budget exhausted"
        response = _failure_response(
            partial=None,
            model=model,
            parse_stage="direct_task_timeout",
            error=detail,
            elapsed_seconds=timeout_seconds,
            metrics={
                "timeout_scope": "whole_task",
                "task_timeout_seconds": timeout_seconds,
            },
        )
        return (
            None,
            _task_timeout_sample(task, oracle, detail),
            _record(
                task=task,
                model=model,
                surface=V2RuntimeSurface.DIRECT.value,
                response=response,
            ),
        )


def _tool_payload(text: str) -> Mapping[str, Any]:
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {}
    return payload if isinstance(payload, Mapping) else {}


def _scalar_count(payload: Mapping[str, Any]) -> int | None:
    data = payload.get("data")
    inner = data if isinstance(data, Mapping) else payload
    literals = inner.get("literals") if isinstance(inner, Mapping) else None
    if not isinstance(literals, list):
        return None
    values = [
        item.get("value")
        for item in literals
        if isinstance(item, Mapping)
        and isinstance(item.get("value"), int)
        and not isinstance(item.get("value"), bool)
        and item.get("value") >= 0
    ]
    return int(values[0]) if len(values) == 1 else None


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _declared_total(payload: Mapping[str, Any]) -> int | None:
    """Read a mechanical total without inferring one from result contents."""

    candidates: list[int] = []
    current: Any = payload
    for _depth in range(3):
        if not isinstance(current, Mapping):
            break
        for key in ("total_count", "total", "count"):
            value = _nonnegative_int(current.get(key))
            if value is not None:
                candidates.append(value)
        current = current.get("data")
    return candidates[0] if candidates and len(set(candidates)) == 1 else None


def _declared_tool_window(
    arguments: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> tuple[int, int] | None:
    """Resolve response-reported list-tool bounds and reject argument drift."""

    candidates: list[Mapping[str, Any]] = [payload]
    current: Any = payload.get("data")
    for _depth in range(3):
        if not isinstance(current, Mapping):
            break
        candidates.append(current)
        current = current.get("data")
    response_window: tuple[int, int] | None = None
    for candidate in candidates:
        skip = _nonnegative_int(candidate.get("skip"))
        limit = _nonnegative_int(candidate.get("limit"))
        if skip is not None and limit is not None and limit > 0:
            response_window = (skip, limit)
            break
    if response_window is None:
        return None

    if "skip" not in arguments or "limit" not in arguments:
        return None
    argument_skip = _nonnegative_int(arguments.get("skip"))
    argument_limit = _nonnegative_int(arguments.get("limit"))
    if argument_skip is None or argument_limit is None or argument_limit <= 0:
        return None
    if (argument_skip, argument_limit) != response_window:
        return None
    return response_window


def _graph_result_cardinality(payload: Mapping[str, Any]) -> int | None:
    """Count graph results without inheriting unrelated scalar columns."""

    top_node_count = _nonnegative_int(payload.get("node_count"))
    top_edge_count = _nonnegative_int(payload.get("edge_count"))
    top_counts = tuple(
        count for count in (top_node_count, top_edge_count) if count is not None
    )
    if top_counts:
        return max(top_counts)

    current: Any = payload.get("data")
    for _depth in range(3):
        if isinstance(current, list):
            return len(current)
        if not isinstance(current, Mapping):
            break
        nodes = current.get("nodes")
        edges = current.get("edges")
        path = current.get("path")
        counts: list[int] = []
        if isinstance(nodes, (Mapping, list)):
            counts.append(len(nodes))
        if isinstance(edges, list):
            counts.append(len(edges))
        if isinstance(path, list):
            counts.append(len(path))
        if counts and max(counts) > 0:
            return max(counts)
        nested = current.get("data")
        if nested is current:
            break
        current = nested
    return None


def _has_positive_graph_path(payload: Mapping[str, Any]) -> bool:
    """Require mechanical node-and-edge evidence for a returned path receipt."""

    node_counts: list[int] = []
    edge_counts: list[int] = []
    top_node_count = _nonnegative_int(payload.get("node_count"))
    top_edge_count = _nonnegative_int(payload.get("edge_count"))
    if top_node_count is not None:
        node_counts.append(top_node_count)
    if top_edge_count is not None:
        edge_counts.append(top_edge_count)

    current: Any = payload.get("data")
    for _depth in range(3):
        if not isinstance(current, Mapping):
            break
        nodes = current.get("nodes")
        edges = current.get("edges")
        if isinstance(nodes, (Mapping, list)):
            node_counts.append(len(nodes))
        if isinstance(edges, list):
            edge_counts.append(len(edges))
        nested = current.get("data")
        if nested is current:
            break
        current = nested
    return bool(
        node_counts
        and edge_counts
        and max(node_counts) >= 2
        and max(edge_counts) >= 1
    )


def _result_cardinality(
    payload: Mapping[str, Any],
    *,
    prefer_graph_counts: bool = False,
) -> int | None:
    """Count only explicit result containers from pinned MCP response shapes."""

    graph_cardinality = _graph_result_cardinality(payload)
    if prefer_graph_counts and graph_cardinality is not None and graph_cardinality > 0:
        return graph_cardinality

    literal_collections = _literal_node_collections(payload)
    named = tuple(
        nodes for key, nodes in literal_collections if key.casefold() in {"entities", "entity"}
    )
    candidates = named or tuple(nodes for _key, nodes in literal_collections)
    if len(candidates) == 1:
        return len(candidates[0])
    if len(candidates) > 1:
        return None

    literal_row_count = _entity_literal_row_count(payload)
    if literal_row_count is not None:
        return literal_row_count
    if _nonempty_scalar_literals(payload):
        # BloodHound reports scalar projections separately from graph node and
        # edge counts. A non-empty, unrecognized literal shape must not inherit
        # the wrapper's zero graph counts and masquerade as conclusive empty
        # evidence.
        return None

    return graph_cardinality


def _scalar_literals(payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    """Return the pinned BloodHound MCP scalar literal sequence."""

    data = payload.get("data")
    if not isinstance(data, Mapping):
        return ()
    literals = data.get("literals")
    if not isinstance(literals, list):
        return ()
    return tuple(item for item in literals if isinstance(item, Mapping))


def _nonempty_scalar_literals(payload: Mapping[str, Any]) -> bool:
    data = payload.get("data")
    return (
        isinstance(data, Mapping)
        and isinstance(data.get("literals"), list)
        and bool(data["literals"])
    )


def _entity_literal_row_count(payload: Mapping[str, Any]) -> int | None:
    """Count flattened BloodHound scalar rows without double-counting columns.

    BloodHound CE renders ``RETURN n.objectid AS object_id, n.name AS name`` as
    one flat literal sequence rather than graph nodes. Every projected column
    repeats once per row. Identity columns establish the row count; optional
    columns must have the same cardinality or the shape is ambiguous.
    """

    literals = _scalar_literals(payload)
    if not literals:
        return None
    normalized_keys: list[str] = []
    for item in literals:
        key = item.get("key")
        if not isinstance(key, str) or not key.strip():
            return None
        normalized_keys.append(key.strip().casefold().replace("_", ""))
    counts = Counter(normalized_keys)
    object_id_count = counts.get("objectid", 0)
    name_count = counts.get("name", 0)
    row_count = object_id_count or name_count
    if row_count <= 0:
        return None
    if object_id_count not in {0, row_count} or name_count not in {0, row_count}:
        return None
    if any(count != row_count for count in counts.values()):
        return None
    identity_keys = {"objectid", "name"}
    for item, normalized_key in zip(literals, normalized_keys, strict=True):
        if normalized_key not in identity_keys:
            continue
        value = item.get("value")
        if not isinstance(value, str) or not value.strip():
            return None
    return row_count


_CYPHER_IDENTIFIER = r"`?[A-Za-z_][A-Za-z0-9_]*`?"
_CYPHER_LITERAL = r"(?:'(?:\\.|[^'])*'|\"(?:\\.|[^\"])*\")"


def _strip_cypher_identifier(value: str) -> str:
    """Return a Cypher variable or alias without changing its identity.

    Cypher keywords are case-insensitive, but variables and aliases are not.
    Collapsing ``g`` and ``G`` can falsely connect a public selector to a
    different graph pattern.
    """

    return value.strip().strip("`")


def _strip_cypher_identifier_exact(value: str) -> str:
    return value.strip().strip("`")


def _strip_cypher_literal(value: str) -> str:
    return value[1:-1].replace("\\'", "'").replace('\\"', '"')


def _split_projection_terms(projection: str) -> tuple[str, ...]:
    """Split a Cypher projection without splitting nested function arguments."""

    terms: list[str] = []
    start = 0
    depth = 0
    delimiter: str | None = None
    escaped = False
    for index, character in enumerate(projection):
        if delimiter is not None:
            if escaped:
                escaped = False
            elif character == "\\" and delimiter != "`":
                escaped = True
            elif character == delimiter:
                delimiter = None
            continue
        if character in {"'", '"', "`"}:
            delimiter = character
        elif character in "([{":
            depth += 1
        elif character in ")]}":
            depth = max(0, depth - 1)
        elif character == "," and depth == 0:
            terms.append(projection[start:index].strip())
            start = index + 1
    terms.append(projection[start:].strip())
    return tuple(term for term in terms if term)


def _query_node_labels(query: str, *, end: int | None = None) -> dict[str, frozenset[str]]:
    """Return labels explicitly attached to graph variables."""

    labels_by_variable: dict[str, set[str]] = {}
    material = query if end is None else query[:end]
    for match in re.finditer(
        (
            rf"\(\s*(?P<variable>{_CYPHER_IDENTIFIER})"
            rf"(?P<labels>(?:\s*:\s*{_CYPHER_IDENTIFIER})*)"
        ),
        material,
        flags=re.IGNORECASE,
    ):
        variable = _strip_cypher_identifier(match.group("variable"))
        labels = {
            _strip_cypher_identifier_exact(label)
            for label in re.findall(
                rf":\s*(?P<label>{_CYPHER_IDENTIFIER})",
                match.group("labels"),
                flags=re.IGNORECASE,
            )
        }
        labels_by_variable.setdefault(variable, set()).update(labels)
    return {
        variable: frozenset(labels)
        for variable, labels in labels_by_variable.items()
    }


def _query_variable_labels(query: str) -> dict[str, frozenset[str]]:
    return _query_node_labels(query)


def _query_count_star_populations(
    query: str,
) -> tuple[tuple[str | None, frozenset[str]], ...]:
    """Return every node population contributing rows to ``COUNT(*)``.

    Unlike ``_query_variable_labels()``, this keeps anonymous node patterns and
    repeated occurrences. ``COUNT(*)`` counts rows, so either can change the
    scalar even when only one named variable is visible.
    """

    populations: list[tuple[str | None, frozenset[str]]] = []
    for match in re.finditer(
        (
            r"\(\s*"
            rf"(?:(?P<variable>{_CYPHER_IDENTIFIER})\s*)?"
            rf"(?P<labels>(?:\s*:\s*{_CYPHER_IDENTIFIER})*)"
            r"(?:\s*\{[^{}]*\})?\s*\)"
        ),
        _population_prefix(query),
        flags=re.IGNORECASE,
    ):
        variable = match.group("variable")
        labels = frozenset(
            _strip_cypher_identifier_exact(label)
            for label in re.findall(
                rf":\s*(?P<label>{_CYPHER_IDENTIFIER})",
                match.group("labels"),
                flags=re.IGNORECASE,
            )
        )
        populations.append(
            (
                _strip_cypher_identifier(variable) if variable is not None else None,
                labels,
            )
        )
    return tuple(populations)


def _projection_scope(
    projection: str,
    inherited: Mapping[str, tuple[str, str]],
) -> dict[str, tuple[str, str]]:
    """Resolve node and identity aliases across one WITH projection."""

    scope: dict[str, tuple[str, str]] = {}
    for raw_term in _split_projection_terms(projection):
        term = re.sub(
            r"^\s*DISTINCT\s+",
            "",
            raw_term.strip(),
            flags=re.IGNORECASE,
        )
        alias_match = re.fullmatch(
            rf"(?P<expression>.*?)\s+AS\s+(?P<alias>{_CYPHER_IDENTIFIER})",
            term,
            flags=re.IGNORECASE,
        )
        expression = (
            alias_match.group("expression").strip()
            if alias_match is not None
            else term
        )
        alias = (
            _strip_cypher_identifier(alias_match.group("alias"))
            if alias_match is not None
            else None
        )
        property_match = re.fullmatch(
            (
                rf"(?P<variable>{_CYPHER_IDENTIFIER})\s*\.\s*"
                r"`?(?P<property>[A-Za-z_][A-Za-z0-9_]*)`?"
            ),
            expression,
            flags=re.IGNORECASE,
        )
        if (
            property_match is not None
            and property_match.group("property") in {"objectid", "name"}
        ):
            source = _strip_cypher_identifier(property_match.group("variable"))
            origin = inherited.get(source, (source, "node"))[0]
            if alias is not None:
                scope[alias] = (
                    origin,
                    property_match.group("property").casefold(),
                )
            continue
        passthrough = re.fullmatch(
            rf"(?P<variable>{_CYPHER_IDENTIFIER})",
            expression,
            flags=re.IGNORECASE,
        )
        if passthrough is None:
            continue
        source = _strip_cypher_identifier(passthrough.group("variable"))
        binding = inherited.get(source, (source, "node"))
        scope[alias or source] = binding
    return scope


def _identity_scope_at(query: str, position: int) -> dict[str, tuple[str, str]]:
    """Resolve the identity scope immediately before a query position."""

    scope: dict[str, tuple[str, str]] = {}
    cursor = 0
    for with_projection in re.finditer(
        (
            r"\bWITH\b(?P<body>.*?)"
            r"(?=\b(?:ORDER\s+BY|OPTIONAL\s+MATCH|MATCH|WITH|RETURN|WHERE|"
            r"UNWIND|CALL|SKIP|LIMIT)\b|$)"
        ),
        query[:position],
        flags=re.IGNORECASE | re.DOTALL,
    ):
        for variable in _query_node_labels(
            query[cursor : with_projection.start()]
        ):
            scope.setdefault(variable, (variable, "node"))
        scope = _projection_scope(with_projection.group("body"), scope)
        cursor = with_projection.end()
    for variable in _query_node_labels(query[cursor:position]):
        scope.setdefault(variable, (variable, "node"))
    return scope


def _return_projection(
    query: str,
) -> tuple[re.Match[str], dict[str, tuple[str, str]]] | None:
    matches = tuple(
        re.finditer(
            (
                r"\bRETURN\b(?P<body>.*?)"
                r"(?=\b(?:ORDER\s+BY|SKIP|LIMIT)\b|;|$)"
            ),
            query,
            flags=re.IGNORECASE | re.DOTALL,
        )
    )
    if not matches:
        return None
    projection = matches[-1]
    inherited = _identity_scope_at(query, projection.start())
    return projection, _projection_scope(projection.group("body"), inherited)


def _returned_identity_origins(query: str) -> frozenset[str]:
    parsed = _return_projection(query)
    if parsed is None:
        return frozenset()
    projection, scope = parsed
    origins = {
        origin
        for origin, kind in scope.values()
        if kind in {"node", "objectid", "name"}
    }
    inherited = _identity_scope_at(query, projection.start())
    for raw_term in _split_projection_terms(projection.group("body")):
        collect_match = re.search(
            (
                r"\bcollect\s*\(\s*(?:DISTINCT\s+)?"
                rf"(?P<variable>{_CYPHER_IDENTIFIER})"
                r"(?:\s*\.\s*`?(?P<property>[A-Za-z_][A-Za-z0-9_]*)`?)?"
                r"\s*\)"
            ),
            raw_term,
            flags=re.IGNORECASE,
        )
        if (
            collect_match is None
            or (
                collect_match.group("property") is not None
                and collect_match.group("property") not in {"objectid", "name"}
            )
        ):
            continue
        variable = _strip_cypher_identifier(collect_match.group("variable"))
        origins.add(inherited.get(variable, (variable, "node"))[0])
    return frozenset(origins)


def _ordering_matches_returned_identity(
    query: str,
    *,
    order_position: int,
    order_expression: str,
) -> bool:
    """Require stable ordering to derive from a returned entity object ID."""

    returned_origins = _returned_identity_origins(query)
    if not returned_origins:
        return False
    scope = _identity_scope_at(query, order_position)
    parsed_return = _return_projection(query)
    if parsed_return is not None and parsed_return[0].start() < order_position:
        scope.update(parsed_return[1])
    property_match = re.fullmatch(
        (
            rf"(?P<variable>{_CYPHER_IDENTIFIER})\s*\.\s*"
            r"`?(?P<property>[A-Za-z_][A-Za-z0-9_]*)`?"
        ),
        order_expression,
        flags=re.IGNORECASE,
    )
    if (
        property_match is not None
        and property_match.group("property") == "objectid"
    ):
        variable = _strip_cypher_identifier(property_match.group("variable"))
        origin = scope.get(variable, (variable, "node"))[0]
        return origin in returned_origins
    alias_match = re.fullmatch(
        rf"(?P<alias>{_CYPHER_IDENTIFIER})",
        order_expression,
        flags=re.IGNORECASE,
    )
    if alias_match is None:
        return False
    binding = scope.get(_strip_cypher_identifier(alias_match.group("alias")))
    return bool(
        binding is not None
        and binding[1] == "objectid"
        and binding[0] in returned_origins
    )


def _object_id_order_aliases(query: str) -> frozenset[str]:
    """Return result aliases proven to derive from an objectid projection."""

    normalized = " ".join(query.split())
    projection = re.search(
        r"\bRETURN\b(?P<body>.*?)\bORDER\s+BY\b",
        normalized,
        flags=re.IGNORECASE,
    )
    if projection is None:
        return frozenset()

    inherited: frozenset[str] = frozenset()
    prefix = normalized[: projection.start()]
    for with_projection in re.finditer(
        (
            r"\bWITH\b(?P<body>.*?)"
            r"(?=\b(?:OPTIONAL\s+MATCH|MATCH|WITH|RETURN|WHERE|UNWIND|CALL)\b|$)"
        ),
        prefix,
        flags=re.IGNORECASE,
    ):
        inherited = _identity_projection_aliases(
            with_projection.group("body"),
            inherited,
        )
    return _identity_projection_aliases(projection.group("body"), inherited)


def _identity_projection_aliases(
    projection: str,
    inherited: frozenset[str],
) -> frozenset[str]:
    """Resolve objectid aliases across one WITH or RETURN projection."""

    safe: set[str] = set()
    for raw_term in projection.split(","):
        term = re.sub(
            r"^\s*DISTINCT\s+",
            "",
            raw_term.strip(),
            flags=re.IGNORECASE,
        )
        direct = re.fullmatch(
            (
                rf"{_CYPHER_IDENTIFIER}\s*\.\s*"
                r"`?(?P<property>[A-Za-z_][A-Za-z0-9_]*)`?"
                rf"(?:\s+AS\s+(?P<alias>{_CYPHER_IDENTIFIER}))?"
            ),
            term,
            flags=re.IGNORECASE,
        )
        if direct is not None and direct.group("property") == "objectid":
            alias = direct.group("alias")
            if alias is not None:
                safe.add(_strip_cypher_identifier(alias))
            continue
        passthrough = re.fullmatch(
            (
                rf"(?P<source>{_CYPHER_IDENTIFIER})"
                rf"(?:\s+AS\s+(?P<alias>{_CYPHER_IDENTIFIER}))?"
            ),
            term,
            flags=re.IGNORECASE,
        )
        if passthrough is None:
            continue
        source = _strip_cypher_identifier(passthrough.group("source"))
        if source not in inherited:
            continue
        alias = passthrough.group("alias")
        safe.add(_strip_cypher_identifier(alias or source))
    return frozenset(safe)


def _graph_search_cardinality(payload: Mapping[str, Any]) -> int | None:
    """Count the pinned graph-analysis search result map."""

    wrapper = payload.get("data")
    results = wrapper.get("data") if isinstance(wrapper, Mapping) else None
    if isinstance(results, (list, Mapping)):
        return len(results)
    return None


def _page_window(query: str) -> tuple[int, int] | None:
    normalized = " ".join(query.split())
    limit = re.search(r"\bLIMIT\s+(?P<limit>\d+)\b", normalized, flags=re.IGNORECASE)
    skip = re.search(r"\bSKIP\s+(?P<skip>\d+)\b", normalized, flags=re.IGNORECASE)
    if limit is None:
        return None
    window_start = min(
        match.start() for match in (skip, limit) if match is not None
    )
    order_matches = tuple(
        re.finditer(
            (
                r"\bORDER\s+BY\b(?P<order>.*?)"
                r"(?=\b(?:OPTIONAL\s+MATCH|MATCH|RETURN|WITH|WHERE|UNWIND|CALL|"
                r"SKIP|LIMIT)\b|$)"
            ),
            normalized[:window_start],
            flags=re.IGNORECASE,
        )
    )
    if not order_matches:
        return None
    order = order_matches[-1]
    order_terms = order.group("order").split(",", maxsplit=1)
    first_order = order_terms[0].strip()
    if re.search(r"\bDESC\b", first_order, flags=re.IGNORECASE):
        return None
    first_order = re.sub(
        r"\s+ASC\s*$",
        "",
        first_order,
        flags=re.IGNORECASE,
    ).strip()
    if not _ordering_matches_returned_identity(
        normalized,
        order_position=order.start(),
        order_expression=first_order,
    ):
        return None
    return (
        int(skip.group("skip")) if skip is not None else 0,
        int(limit.group("limit")),
    )


def _page_query_key(query: str) -> str:
    """Bind pages to one alpha-equivalent graph population.

    Page windows and result projections are validated independently. The
    aggregation key therefore uses the same population canonicalization as the
    companion count, so changing only Cypher variable names or omitting
    ``SKIP 0`` cannot split one otherwise identical enumeration.
    """

    return _query_population_key(query)


def _is_count_query(query: str) -> bool:
    return bool(
        re.search(
            r"\bRETURN\s+(?:DISTINCT\s+)?COUNT\s*\([^)]*\)"
            r"\s*(?:AS\s+`?[A-Za-z_][A-Za-z0-9_]*`?)?"
            r"\s*(?:LIMIT\s+1)?\s*;?\s*\Z",
            query,
            flags=re.IGNORECASE,
        )
    )


def _returned_path_nodes(
    query: str,
) -> tuple[tuple[re.Match[str], ...], int, int, int] | None:
    """Return every node pattern of the path actually returned.

    Merely mentioning the public selectors elsewhere in a Cypher statement is
    not a claim-bound proof. This parser deliberately accepts only a named path
    whose contiguous relationship chain is returned by that same name.
    """

    node_pattern = re.compile(
        (
            r"\(\s*"
            rf"(?:(?P<variable>{_CYPHER_IDENTIFIER})\s*)?"
            rf"(?:\s*:\s*{_CYPHER_IDENTIFIER})*"
            r"(?:\s*\{[^{}]*\})?\s*\)"
        ),
        flags=re.IGNORECASE,
    )
    relationship_connector = re.compile(
        r"\s*(?:<-\s*\[[^\]]*\]\s*-|-\s*\[[^\]]*\]\s*(?:->|-))\s*",
        flags=re.IGNORECASE,
    )
    parsed_return = _return_projection(query)
    if parsed_return is None:
        return None
    return_projection, _return_scope = parsed_return
    for assignment in re.finditer(
        rf"\bMATCH\s+(?P<path>{_CYPHER_IDENTIFIER})\s*=\s*",
        query,
        flags=re.IGNORECASE,
    ):
        if assignment.end() >= return_projection.start():
            continue
        suffix = query[assignment.end() :]
        boundary = re.search(
            r"\b(?:OPTIONAL\s+MATCH|MATCH|WHERE|WITH|RETURN|UNWIND|CALL)\b",
            suffix,
            flags=re.IGNORECASE,
        )
        if boundary is None:
            continue
        path_expression = suffix[: boundary.start()]
        nodes = tuple(node_pattern.finditer(path_expression))
        if len(nodes) < 2:
            continue
        contiguous = [nodes[0]]
        for candidate in nodes[1:]:
            connector = path_expression[contiguous[-1].end() : candidate.start()]
            if relationship_connector.fullmatch(connector) is None:
                break
            contiguous.append(candidate)
        if len(contiguous) < 2:
            continue
        aliases = {_strip_cypher_identifier(assignment.group("path"))}
        between = query[assignment.end() : return_projection.start()]
        for with_projection in re.finditer(
            (
                r"\bWITH\b(?P<body>.*?)"
                r"(?=\b(?:ORDER\s+BY|OPTIONAL\s+MATCH|MATCH|WITH|RETURN|WHERE|"
                r"UNWIND|CALL|SKIP|LIMIT)\b|$)"
            ),
            between,
            flags=re.IGNORECASE | re.DOTALL,
        ):
            projected_aliases: set[str] = set()
            for raw_term in _split_projection_terms(with_projection.group("body")):
                term = re.sub(
                    r"^\s*DISTINCT\s+",
                    "",
                    raw_term.strip(),
                    flags=re.IGNORECASE,
                )
                if term == "*":
                    projected_aliases.update(aliases)
                    continue
                alias_match = re.fullmatch(
                    (
                        rf"(?P<source>{_CYPHER_IDENTIFIER})"
                        rf"(?:\s+AS\s+(?P<alias>{_CYPHER_IDENTIFIER}))?"
                    ),
                    term,
                    flags=re.IGNORECASE,
                )
                if alias_match is None:
                    continue
                source = _strip_cypher_identifier(alias_match.group("source"))
                if source not in aliases:
                    continue
                alias = alias_match.group("alias")
                projected_aliases.add(
                    _strip_cypher_identifier(alias) if alias is not None else source
                )
            aliases = projected_aliases
            if not aliases:
                break
        if not aliases:
            continue
        returned_path = False
        for raw_term in _split_projection_terms(return_projection.group("body")):
            term = re.sub(
                r"^\s*DISTINCT\s+",
                "",
                raw_term.strip(),
                flags=re.IGNORECASE,
            )
            return_match = re.fullmatch(
                (
                    rf"(?P<source>{_CYPHER_IDENTIFIER})"
                    rf"(?:\s+AS\s+(?P<alias>{_CYPHER_IDENTIFIER}))?"
                ),
                term,
                flags=re.IGNORECASE,
            )
            if (
                return_match is not None
                and _strip_cypher_identifier(return_match.group("source")) in aliases
            ):
                returned_path = True
                break
        if not returned_path:
            continue
        path_end = assignment.end() + boundary.start()
        return (
            tuple(contiguous),
            assignment.start(),
            path_end,
            return_projection.start(),
        )
    return None


def _path_endpoint_matches_public_role(
    endpoint: re.Match[str],
    *,
    entity: EntityRef,
    query: str,
    assignment_start: int,
    path_end: int,
    return_position: int,
) -> bool:
    endpoint_text = endpoint.group(0)
    selectors = _entity_query_selectors(entity)
    if any(
        _query_has_public_selector(endpoint_text, selector)
        for selector in selectors
    ):
        return True

    variable = endpoint.group("variable")
    if variable is None:
        return False
    aliases = frozenset({_strip_cypher_identifier(variable)})
    proven = bool(
        aliases
        & _selector_variables_in_scope(query, selectors, assignment_start)
    )

    cursor = path_end
    for with_projection in re.finditer(
        (
            r"\bWITH\b(?P<body>.*?)"
            r"(?=\b(?:ORDER\s+BY|OPTIONAL\s+MATCH|MATCH|WITH|RETURN|WHERE|"
            r"UNWIND|CALL|SKIP|LIMIT)\b|$)"
        ),
        query[path_end:return_position],
        flags=re.IGNORECASE | re.DOTALL,
    ):
        absolute_start = path_end + with_projection.start()
        absolute_end = path_end + with_projection.end()
        if aliases & _query_public_selector_variables(
            query[cursor:absolute_start],
            selectors,
        ):
            proven = True
        aliases = _project_selector_scope(
            with_projection.group("body"),
            aliases,
        )
        cursor = absolute_end
    if aliases & _query_public_selector_variables(
        query[cursor:return_position],
        selectors,
    ):
        proven = True
    return proven


def _query_matches_public_claim(
    task: TaskBundle,
    query: str,
    *,
    is_count: bool,
    allow_set_companion_count: bool = False,
) -> bool:
    """Validate only public claim selectors; never consult the sealed oracle."""

    contract = task.binding.mcp_evidence_contract
    if contract is None or not query.strip():
        return False
    if contract.result_kind == "scalar_count" and not is_count:
        return False
    companion_count = (
        contract.result_kind != "scalar_count"
        and is_count
        and allow_set_companion_count
        and task.claim_kind == "set"
        and task.binding.bounds.require_total_count
    )
    if contract.result_kind != "scalar_count" and is_count and not companion_count:
        return False
    normalized = normalize_query_for_fingerprint(query).replace("\x1f", " ")
    entities_by_role = {entity.role: entity for entity in task.input_entities}
    normalized = _bind_anonymous_public_selector_nodes(
        normalized,
        tuple(entities_by_role.values()),
    )
    public_selectors: list[QuerySelector] = []
    for role in contract.required_input_roles:
        entity = entities_by_role.get(role)
        if entity is None:
            return False
        selectors = _entity_query_selectors(entity)
        if not any(
            _query_has_public_selector(normalized, selector) for selector in selectors
        ):
            return False
        public_selectors.extend(selectors)
    if task.claim_kind == "absence" and _negative_query_scope_mode(task, query) is None:
        return False
    selector_variables = _query_public_selector_variables(
        normalized,
        tuple(public_selectors),
    )
    projected_variables = (
        _query_count_projection_variables(normalized)
        if is_count
        else _query_identity_projection_variables(normalized)
    )
    variable_labels = _query_variable_labels(normalized)
    count_star_single_population = False
    if is_count and not projected_variables and _query_uses_count_star(normalized):
        # COUNT(*) is a valid scalar proof only when the public projection
        # contains exactly one typed graph population. Count node-pattern
        # occurrences rather than only named variables: anonymous or repeated
        # fan-out patterns also multiply rows. A selector-only population is
        # still the counted population and must not be discarded.
        populations = _query_count_star_populations(normalized)
        abstract_projection = any(
            object_type.casefold() in {"any", "principal"}
            for object_type in contract.projection_types
        )
        if len(populations) == 1:
            variable, labels = populations[0]
            candidate = variable or "__ori_anonymous_count_population__"
            if variable is None:
                variable_labels[candidate] = labels
            projected_variables = frozenset({candidate})
            count_star_single_population = True
        if count_star_single_population and not abstract_projection:
            permitted_types = set(contract.projection_types)
            candidate = next(iter(projected_variables))
            labels = variable_labels.get(candidate, frozenset())
            if not labels or labels.isdisjoint(permitted_types):
                projected_variables = frozenset()
                count_star_single_population = False
    if contract.projection_types:
        if not projected_variables:
            return False
        if (
            selector_variables
            and projected_variables <= selector_variables
            and not count_star_single_population
        ):
            return False
        abstract_projection = any(
            object_type.casefold() in {"any", "principal"}
            for object_type in contract.projection_types
        )
        if not abstract_projection:
            permitted_types = set(contract.projection_types)
            for variable in projected_variables:
                labels = variable_labels.get(variable, frozenset())
                if labels and labels.isdisjoint(permitted_types):
                    return False
                if companion_count and not labels:
                    return False
    if (
        task.acceptance_spec.selection is not None
        and not _query_matches_public_selection(
            task,
            normalized,
            projected_variables=projected_variables,
        )
    ):
        return False
    if contract.result_kind == "path":
        if len(contract.required_input_roles) != 2:
            return False
        returned_path = _returned_path_nodes(normalized)
        if returned_path is None:
            return False
        path_nodes, assignment_start, path_end, return_position = returned_path
        source_role, target_role = contract.required_input_roles
        source = entities_by_role[source_role]
        target = entities_by_role[target_role]
        if task.claim_kind == "decision":
            # Decision/supporting-evidence claims may describe a composite
            # witness whose declared subjects are interior nodes. They still
            # must both belong to the one named path actually returned; a
            # selector on a detached MATCH cannot authorize that witness.
            for entity in (source, target):
                if not any(
                    _path_endpoint_matches_public_role(
                        node,
                        entity=entity,
                        query=normalized,
                        assignment_start=assignment_start,
                        path_end=path_end,
                        return_position=return_position,
                    )
                    for node in path_nodes
                ):
                    return False
        elif not _path_endpoint_matches_public_role(
            path_nodes[0],
            entity=source,
            query=normalized,
            assignment_start=assignment_start,
            path_end=path_end,
            return_position=return_position,
        ) or not _path_endpoint_matches_public_role(
            path_nodes[-1],
            entity=target,
            query=normalized,
            assignment_start=assignment_start,
            path_end=path_end,
            return_position=return_position,
        ):
            # Route claims retain their directional endpoint contract.
            return False
    elif contract.result_kind == "entities" and not companion_count:
        if re.search(r"\breturn\b", normalized, flags=re.IGNORECASE) is None:
            return False
    return True


def _query_covers_public_negative_scope(
    task: TaskBundle,
    query: str,
) -> bool:
    return _negative_query_scope_mode(task, query) is not None


def _negative_query_scope_mode(
    task: TaskBundle,
    query: str,
) -> Literal["exact", "broader"] | None:
    return negative_query_scope_mode(task, query)


def _entity_query_selectors(entity: EntityRef) -> tuple[QuerySelector, ...]:
    """Return exact values that public BloodHound identity properties can equal.

    Display aliases belong to answer normalization. They are not alternate
    values for the live graph's ``name`` or ``objectid`` properties and must
    never be used to prove that a query selected the declared population.
    """

    selectors: list[QuerySelector] = [("objectid", entity.object_id)]
    if entity.canonical_name is not None and entity.canonical_name.strip():
        selectors.append(("name", entity.canonical_name))
    return tuple(selectors)


def _bind_anonymous_public_selector_nodes(
    query: str,
    entities: tuple[EntityRef, ...],
) -> str:
    """Assign parser-only variables to exact anonymous public anchor nodes.

    ``(:Group {objectid: ...})`` and ``(g:Group {objectid: ...})`` are
    semantically equivalent Cypher anchor forms. The inserted name exists only
    in the public query validator; the submitted query is never rewritten
    before execution.
    """

    occupied = {
        _strip_cypher_identifier(match.group("variable"))
        for match in re.finditer(
            (
                rf"\(\s*(?P<variable>{_CYPHER_IDENTIFIER})"
                r"(?=\s*(?::|\{|\)))"
            ),
            query,
            flags=re.IGNORECASE,
        )
    }
    counter = 0

    def bind(match: re.Match[str]) -> str:
        nonlocal counter
        properties = match.group("properties")
        matched_entities = tuple(
            entity
            for entity in entities
            if any(
                _query_has_public_selector(properties, selector)
                for selector in _entity_query_selectors(entity)
            )
        )
        if len(matched_entities) != 1:
            return match.group(0)
        while True:
            variable = f"__ori_public_anchor_{counter}"
            counter += 1
            if variable not in occupied:
                occupied.add(variable)
                break
        return (
            "("
            + variable
            + match.group("labels")
            + " "
            + properties
            + ")"
        )

    return re.sub(
        (
            r"\(\s*"
            rf"(?P<labels>(?:\s*:\s*{_CYPHER_IDENTIFIER})*)"
            r"\s*(?P<properties>\{[^{}]*\})\s*\)"
        ),
        bind,
        query,
        flags=re.IGNORECASE,
    )


def _query_has_public_selector(query: str, selector: QuerySelector) -> bool:
    """Recognize exact public name/objectid selectors after comment stripping."""

    return bool(_query_selector_bindings(query, selector))


def _query_selector_bindings(
    query: str,
    selector: QuerySelector,
) -> tuple[str | None, ...]:
    """Return variables bound by an exact, semantically valid selector."""

    property_name, selector_value = selector
    matcher = re.compile(
        (
            r"(?<![A-Za-z0-9_`])"
            r"(?:(?P<lhs_function>TOUPPER|TOLOWER)\s*\(\s*)?"
            rf"(?:(?P<variable>{_CYPHER_IDENTIFIER})\s*\.\s*)?"
            r"`?(?P<property>objectid|name)`?"
            r"\s*(?(lhs_function)\))\s*(?::|=)\s*"
            r"(?:(?P<rhs_function>TOUPPER)\s*\(\s*)?"
            rf"(?P<literal>{_CYPHER_LITERAL})"
            r"\s*(?(rhs_function)\))"
            r"(?=\s*(?:,|\}|\)|AND\b|OR\b|RETURN\b|WITH\b|ORDER\b|"
            r"SKIP\b|LIMIT\b|$))"
        ),
        flags=re.IGNORECASE,
    )
    bindings: list[str | None] = []
    for match in matcher.finditer(" ".join(query.split())):
        if match.group("property") != property_name:
            continue
        literal = _strip_cypher_literal(match.group("literal"))
        lhs_function = (match.group("lhs_function") or "").casefold()
        rhs_function = (match.group("rhs_function") or "").casefold()
        expected = selector_value
        if lhs_function == "toupper":
            expected = expected.upper()
        elif lhs_function == "tolower":
            expected = expected.lower()
        actual = literal.upper() if rhs_function == "toupper" else literal
        if expected != actual:
            continue
        variable = match.group("variable")
        bindings.append(
            _strip_cypher_identifier(variable) if variable is not None else None
        )
    return tuple(bindings)


def _query_projects_identity(query: str) -> bool:
    """Recognize an identity-bearing entity projection for abstract principals."""

    return bool(_query_identity_projection_variables(query))


def _query_identity_projection_variables(query: str) -> frozenset[str]:
    """Return variables whose identity properties are present in the result."""

    return _returned_identity_origins(query)


def _query_count_projection_variables(query: str) -> frozenset[str]:
    """Return variables mechanically counted by a scalar result projection."""

    parsed_return = _return_projection(query)
    if parsed_return is None:
        return frozenset()
    projection, _ = parsed_return
    inherited = _identity_scope_at(query, projection.start())
    variables: set[str] = set()
    for match in re.finditer(
        (
            r"\bCOUNT\s*\(\s*(?:DISTINCT\s+)?"
            rf"(?P<variable>{_CYPHER_IDENTIFIER})"
            r"(?:\s*\.\s*`?(?P<property>[A-Za-z_][A-Za-z0-9_]*)`?)?"
            r"\s*\)"
        ),
        projection.group("body"),
        flags=re.IGNORECASE,
    ):
        if (
            match.group("property") is not None
            and match.group("property") not in {"objectid", "name"}
        ):
            continue
        variable = _strip_cypher_identifier(match.group("variable"))
        variables.add(inherited.get(variable, (variable, "node"))[0])
    return frozenset(variables)


def _query_uses_count_star(query: str) -> bool:
    """Return whether the scalar projection counts result rows explicitly."""

    parsed_return = _return_projection(query)
    if parsed_return is None:
        return False
    projection, _ = parsed_return
    return bool(
        re.search(
            r"\bCOUNT\s*\(\s*\*\s*\)",
            projection.group("body"),
            flags=re.IGNORECASE,
        )
    )


def _query_public_selector_variables(
    query: str,
    selectors: tuple[QuerySelector, ...],
) -> frozenset[str]:
    """Return variables bound by exact public selectors in the submitted query."""

    variables: set[str] = set()
    for selector in selectors:
        variables.update(
            variable
            for variable in _query_selector_bindings(query, selector)
            if variable is not None
        )
        for node_match in re.finditer(
            (
                rf"\(\s*(?P<variable>{_CYPHER_IDENTIFIER})"
                r"(?:\s*:\s*`?[A-Za-z_][A-Za-z0-9_]*`?)*"
                r"\s*\{(?P<properties>[^}]*)\}\s*\)"
            ),
            query,
            flags=re.IGNORECASE,
        ):
            if _query_has_public_selector(node_match.group("properties"), selector):
                variables.add(
                    _strip_cypher_identifier(node_match.group("variable"))
                )
    return frozenset(variables)


def _project_selector_scope(
    projection: str,
    inherited: frozenset[str],
) -> frozenset[str]:
    """Carry exact-selector node bindings across a WITH boundary."""

    projected: set[str] = set()
    for raw_term in _split_projection_terms(projection):
        term = re.sub(
            r"^\s*DISTINCT\s+",
            "",
            raw_term.strip(),
            flags=re.IGNORECASE,
        )
        if term == "*":
            projected.update(inherited)
            continue
        passthrough = re.fullmatch(
            (
                rf"(?P<source>{_CYPHER_IDENTIFIER})"
                rf"(?:\s+AS\s+(?P<alias>{_CYPHER_IDENTIFIER}))?"
            ),
            term,
            flags=re.IGNORECASE,
        )
        if passthrough is None:
            continue
        source = _strip_cypher_identifier(passthrough.group("source"))
        if source not in inherited:
            continue
        alias = passthrough.group("alias")
        projected.add(
            _strip_cypher_identifier(alias)
            if alias is not None
            else source
        )
    return frozenset(projected)


def _selector_variables_in_scope(
    query: str,
    selectors: tuple[QuerySelector, ...],
    position: int,
) -> frozenset[str]:
    """Resolve selector-bound node variables live at one query position.

    Exact selectors mentioned after a path leaves scope, or on a variable
    dropped by ``WITH``, must not authorize the returned path. Plain node
    passthrough and aliases remain valid.
    """

    scope: frozenset[str] = frozenset()
    cursor = 0
    for with_projection in re.finditer(
        (
            r"\bWITH\b(?P<body>.*?)"
            r"(?=\b(?:ORDER\s+BY|OPTIONAL\s+MATCH|MATCH|WITH|RETURN|WHERE|"
            r"UNWIND|CALL|SKIP|LIMIT)\b|$)"
        ),
        query[:position],
        flags=re.IGNORECASE | re.DOTALL,
    ):
        segment_bindings = _query_public_selector_variables(
            query[cursor : with_projection.start()],
            selectors,
        )
        scope = _project_selector_scope(
            with_projection.group("body"),
            frozenset((*scope, *segment_bindings)),
        )
        cursor = with_projection.end()
    return frozenset(
        (
            *scope,
            *_query_public_selector_variables(query[cursor:position], selectors),
        )
    )


def _selection_query_edges(
    query: str,
) -> tuple[tuple[str, str, str, int, int], ...] | None:
    """Project exact directed relationship patterns from a selection query."""

    population = _population_prefix(query)
    node_matches = tuple(
        re.finditer(
            (
                rf"\(\s*(?P<variable>{_CYPHER_IDENTIFIER})"
                rf"(?P<labels>(?:\s*:\s*{_CYPHER_IDENTIFIER})*)"
                r"(?:\s*\{[^{}]*\})?\s*\)"
            ),
            population,
            flags=re.IGNORECASE,
        )
    )
    edges: list[tuple[str, str, str, int, int]] = []
    for relationship in re.finditer(r"\[(?P<body>[^\]]*)\]", population):
        left_nodes = tuple(node for node in node_matches if node.end() <= relationship.start())
        right_nodes = tuple(node for node in node_matches if node.start() >= relationship.end())
        if not left_nodes or not right_nodes:
            return None
        left = left_nodes[-1]
        right = right_nodes[0]
        left_connector = population[left.end() : relationship.start()]
        right_connector = population[relationship.end() : right.start()]
        left_outbound = re.fullmatch(r"\s*-\s*", left_connector) is not None
        right_outbound = re.fullmatch(r"\s*->\s*", right_connector) is not None
        left_inbound = re.fullmatch(r"\s*<-\s*", left_connector) is not None
        right_inbound = re.fullmatch(r"\s*-\s*", right_connector) is not None
        if left_outbound and right_outbound:
            source = _strip_cypher_identifier(left.group("variable"))
            target = _strip_cypher_identifier(right.group("variable"))
        elif left_inbound and right_inbound:
            source = _strip_cypher_identifier(right.group("variable"))
            target = _strip_cypher_identifier(left.group("variable"))
        else:
            return None

        body = relationship.group("body")
        relationship_types = tuple(
            match.group("kind")
            for match in re.finditer(
                r"(?::|\|)\s*`?(?P<kind>[A-Za-z_][A-Za-z0-9_]*)`?",
                body,
                flags=re.IGNORECASE,
            )
        )
        if len(relationship_types) != 1 or "{" in body or "}" in body:
            return None
        range_match = re.search(
            r"\*\s*(?:(?P<lower>\d+)\s*)?"
            r"(?:\.\.\s*(?P<upper>\d+))?",
            body,
        )
        if range_match is None:
            min_hops = max_hops = 1
        elif range_match.group("upper") is not None:
            min_hops = int(range_match.group("lower") or 1)
            max_hops = int(range_match.group("upper"))
        elif range_match.group("lower") is not None:
            min_hops = max_hops = int(range_match.group("lower"))
        else:
            return None
        edges.append(
            (
                source,
                relationship_types[0],
                target,
                min_hops,
                max_hops,
            )
        )
    return tuple(edges)


def _selection_scalar(raw: str) -> Any:
    value = raw.strip()
    function = re.fullmatch(
        rf"TOUPPER\s*\(\s*(?P<literal>{_CYPHER_LITERAL})\s*\)",
        value,
        flags=re.IGNORECASE,
    )
    if function is not None:
        return _strip_cypher_literal(function.group("literal")).upper()
    if re.fullmatch(_CYPHER_LITERAL, value):
        return _strip_cypher_literal(value)
    if value.casefold() == "true":
        return True
    if value.casefold() == "false":
        return False
    if value.casefold() == "null":
        return None
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    raise ValueError("unsupported selection scalar")


def _selection_constraint(
    raw: str,
) -> tuple[str, str, str, Any] | None:
    """Parse one public property predicate or boolean shorthand."""

    term = raw.strip()
    coalesced_boolean = re.fullmatch(
        (
            r"COALESCE\s*\(\s*"
            rf"(?P<variable>{_CYPHER_IDENTIFIER})\s*\.\s*"
            r"`?(?P<property>[A-Za-z_][A-Za-z0-9_]*)`?\s*,\s*"
            r"(?P<fallback>true|false)\s*\)\s*=\s*"
            r"(?P<expected>true|false)"
        ),
        term,
        flags=re.IGNORECASE,
    )
    if coalesced_boolean is not None:
        fallback = coalesced_boolean.group("fallback").casefold() == "true"
        expected = coalesced_boolean.group("expected").casefold() == "true"
        return (
            _strip_cypher_identifier(coalesced_boolean.group("variable")),
            coalesced_boolean.group("property"),
            "not_equals" if fallback == expected else "equals",
            (not expected) if fallback == expected else expected,
        )
    shorthand = re.fullmatch(
        (
            r"(?P<not>NOT\s+)?"
            rf"(?P<variable>{_CYPHER_IDENTIFIER})\s*\.\s*"
            r"`?(?P<property>[A-Za-z_][A-Za-z0-9_]*)`?"
        ),
        term,
        flags=re.IGNORECASE,
    )
    if shorthand is not None:
        return (
            _strip_cypher_identifier(shorthand.group("variable")),
            shorthand.group("property"),
            "equals",
            shorthand.group("not") is None,
        )
    match = re.fullmatch(
        (
            r"(?:(?:TOUPPER|TOLOWER)\s*\(\s*)?"
            rf"(?P<variable>{_CYPHER_IDENTIFIER})\s*\.\s*"
            r"`?(?P<property>[A-Za-z_][A-Za-z0-9_]*)`?"
            r"\s*\)?\s*(?P<operator>=|<>|!=|IS\s+NOT\s+NULL|IS\s+NULL|"
            r"NOT\s+IN|IN)\s*"
            r"(?P<value>.*?)\s*"
        ),
        term,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    operator = " ".join(match.group("operator").casefold().split())
    if operator in {"is not null", "is null"}:
        return (
            _strip_cypher_identifier(match.group("variable")),
            match.group("property"),
            "exists" if operator == "is not null" else "not_exists",
            None,
        )
    if operator in {"in", "not in"}:
        raw_value = match.group("value").strip()
        if not raw_value.startswith("[") or not raw_value.endswith("]"):
            return None
        try:
            value = tuple(
                _selection_scalar(item)
                for item in _split_projection_terms(raw_value[1:-1])
            )
        except ValueError:
            return None
        if not value:
            return None
        return (
            _strip_cypher_identifier(match.group("variable")),
            match.group("property"),
            "in" if operator == "in" else "not_in",
            value,
        )
    try:
        value = _selection_scalar(match.group("value"))
    except ValueError:
        return None
    return (
        _strip_cypher_identifier(match.group("variable")),
        match.group("property"),
        "equals" if operator == "=" else "not_equals",
        value,
    )


def _selection_constraint_terms(query: str) -> tuple[str, ...] | None:
    """Extract all population-narrowing map and WHERE terms."""

    population = _population_prefix(query)
    terms: list[str] = []
    for node in re.finditer(
        (
            rf"\(\s*(?P<variable>{_CYPHER_IDENTIFIER})"
            rf"(?:\s*:\s*{_CYPHER_IDENTIFIER})*"
            r"\s*\{(?P<properties>[^{}]*)\}\s*\)"
        ),
        population,
        flags=re.IGNORECASE,
    ):
        for entry in _split_projection_terms(node.group("properties")):
            property_match = re.fullmatch(
                (
                    r"`?(?P<property>[A-Za-z_][A-Za-z0-9_]*)`?"
                    r"\s*:\s*(?P<value>.+)"
                ),
                entry,
            )
            if property_match is None:
                return None
            terms.append(
                f"{node.group('variable')}.{property_match.group('property')}"
                f" = {property_match.group('value')}"
            )
    masked = re.sub(_CYPHER_LITERAL, lambda match: " " * len(match.group(0)), population)
    for where in re.finditer(
        r"\bWHERE\b(?P<body>.*?)(?=\b(?:MATCH|OPTIONAL\s+MATCH|WITH)\b|$)",
        masked,
        flags=re.IGNORECASE,
    ):
        body = population[where.start("body") : where.end("body")]
        if re.search(r"\b(?:OR|XOR)\b", body, flags=re.IGNORECASE):
            return None
        terms.extend(
            term.strip()
            for term in re.split(r"\bAND\b", body, flags=re.IGNORECASE)
            if term.strip()
        )
    return tuple(terms)


def _selection_predicate_matches(
    fact: tuple[str, str, str, Any],
    *,
    variable: str,
    property_name: str,
    operator: str,
    value: Any,
) -> bool:
    fact_variable, fact_property, fact_operator, fact_value = fact
    if fact_variable != variable or fact_property != property_name:
        return False
    if fact_operator == operator and fact_value == value:
        return True
    return (
        operator == "not_equals"
        and value is True
        and fact_operator == "equals"
        and fact_value is False
    )


def _selection_role_type_is_implied(
    task: TaskBundle,
    *,
    role: str,
    expected_type: str,
) -> bool:
    """Return whether public selectors or edge contracts prove one role's type."""

    selection = task.acceptance_spec.selection
    if selection is None:
        return False
    if any(anchor.role == role for anchor in selection.anchors):
        # The caller has already proven that the query binds this role through
        # the task's exact, typed public input selector.
        return True

    allowed_types: set[str] | None = None
    for pattern in selection.relationships:
        contract = relationship_contract(pattern.relationship)
        if pattern.direction.value == "outbound":
            endpoint_types = (
                contract.source_types
                if pattern.source_role == role
                else contract.target_types
                if pattern.target_role == role
                else None
            )
        else:
            endpoint_types = (
                contract.target_types
                if pattern.source_role == role
                else contract.source_types
                if pattern.target_role == role
                else None
            )
        if endpoint_types is None:
            continue
        normalized = set(endpoint_types)
        allowed_types = (
            normalized
            if allowed_types is None
            else allowed_types.intersection(normalized)
        )

    return bool(allowed_types) and allowed_types <= {expected_type}


def _query_matches_public_selection(
    task: TaskBundle,
    query: str,
    *,
    projected_variables: frozenset[str],
) -> bool:
    """Prove a set/count query realizes the complete public selection graph."""

    selection = task.acceptance_spec.selection
    if selection is None or len(projected_variables) != 1:
        return selection is None
    population = _population_prefix(query)
    populations = _query_count_star_populations(population)
    synthetic_population = "__ori_anonymous_count_population__"
    if (
        len(populations) == 1
        and populations[0][0] is None
        and projected_variables == frozenset({synthetic_population})
    ):
        populations = ((synthetic_population, populations[0][1]),)
    if not populations or any(variable is None for variable, _labels in populations):
        return False
    graph_variables = {
        variable for variable, _labels in populations if variable is not None
    }
    role_order = tuple(
        dict.fromkeys(
            (
                *(anchor.role for anchor in selection.anchors),
                *(
                    role
                    for relationship in selection.relationships
                    for role in (relationship.source_role, relationship.target_role)
                ),
                selection.projection_role,
            )
        )
    )
    if len(graph_variables) != len(role_order):
        return False

    role_assignments: dict[str, str] = {
        selection.projection_role: next(iter(projected_variables))
    }
    entities_by_role = {entity.role: entity for entity in task.input_entities}
    for anchor in selection.anchors:
        entity = entities_by_role.get(anchor.role)
        if entity is None:
            return False
        selectors = _entity_query_selectors(entity)
        variables = _query_public_selector_variables(query, selectors)
        if len(variables) != 1:
            return False
        variable = next(iter(variables))
        previous = role_assignments.get(anchor.role)
        if previous is not None and previous != variable:
            return False
        role_assignments[anchor.role] = variable
    if len(set(role_assignments.values())) != len(role_assignments):
        return False

    remaining_roles = tuple(role for role in role_order if role not in role_assignments)
    remaining_variables = tuple(
        sorted(graph_variables - set(role_assignments.values()))
    )
    if len(remaining_roles) != len(remaining_variables):
        return False
    query_edges = _selection_query_edges(query)
    if query_edges is None:
        return False

    role_types: dict[str, set[str]] = {}
    for anchor in selection.anchors:
        if anchor.object_type:
            role_types.setdefault(anchor.role, set()).add(anchor.object_type)
    role_types.setdefault(selection.projection_role, set()).add(
        selection.projection_type
    )
    for relationship in selection.relationships:
        if relationship.source_type:
            role_types.setdefault(relationship.source_role, set()).add(
                relationship.source_type
            )
        if relationship.target_type:
            role_types.setdefault(relationship.target_role, set()).add(
                relationship.target_type
            )
    variable_labels = _query_variable_labels(query)
    for variable, labels in populations:
        if variable == synthetic_population:
            variable_labels[variable] = labels
    expected_edges = Counter(
        (
            (
                relationship.source_role
                if relationship.direction.value == "outbound"
                else relationship.target_role
            ),
            relationship.relationship,
            (
                relationship.target_role
                if relationship.direction.value == "outbound"
                else relationship.source_role
            ),
            relationship.min_hops,
            relationship.max_hops,
        )
        for relationship in selection.relationships
    )

    for assigned_variables in permutations(remaining_variables):
        candidate = {
            **role_assignments,
            **dict(zip(remaining_roles, assigned_variables, strict=True)),
        }
        labels_valid = True
        for role, variable in candidate.items():
            expected_types = {
                object_type
                for object_type in role_types.get(role, set())
                if object_type.casefold() not in {"any", "principal"}
            }
            labels = variable_labels.get(variable, frozenset())
            if not expected_types:
                continue
            if len(expected_types) != 1:
                labels_valid = False
                break
            expected_type = next(iter(expected_types))
            if labels:
                if labels != frozenset({expected_type}):
                    labels_valid = False
                    break
            elif not _selection_role_type_is_implied(
                task,
                role=role,
                expected_type=expected_type,
            ):
                labels_valid = False
                break
        if not labels_valid:
            continue
        role_by_variable = {variable: role for role, variable in candidate.items()}
        actual_edges = Counter(
            (
                role_by_variable.get(source, ""),
                relationship,
                role_by_variable.get(target, ""),
                min_hops,
                max_hops,
            )
            for source, relationship, target, min_hops, max_hops in query_edges
        )
        if actual_edges != expected_edges:
            continue

        terms = _selection_constraint_terms(query)
        if terms is None:
            return False
        remaining_predicates = list(selection.predicates)
        all_terms_valid = True
        for term in terms:
            selector_term = False
            for anchor in selection.anchors:
                entity = entities_by_role[anchor.role]
                expected_variable = candidate[anchor.role]
                for selector in _entity_query_selectors(entity):
                    if expected_variable in {
                        variable
                        for variable in _query_selector_bindings(term, selector)
                        if variable is not None
                    }:
                        selector_term = True
                        break
                if selector_term:
                    break
            if selector_term:
                continue
            fact = _selection_constraint(term)
            if fact is None:
                all_terms_valid = False
                break
            matched_index = next(
                (
                    index
                    for index, predicate in enumerate(remaining_predicates)
                    if _selection_predicate_matches(
                        fact,
                        variable=candidate[predicate.role],
                        property_name=predicate.property_name,
                        operator=predicate.operator.value,
                        value=predicate.value,
                    )
                ),
                None,
            )
            if matched_index is None:
                all_terms_valid = False
                break
            remaining_predicates.pop(matched_index)
        if all_terms_valid and not remaining_predicates:
            return True
    return False


def _population_prefix(query: str) -> str:
    """Return the graph-population clauses before the first projection boundary."""

    # A selector literal containing "RETURN" or "WITH" is data, not a Cypher
    # projection boundary. Replace literals with equal-width whitespace before
    # locating the boundary so string offsets remain valid.
    masked = re.sub(_CYPHER_LITERAL, lambda match: " " * len(match.group(0)), query)
    boundary = re.search(r"\b(?:WITH|RETURN)\b", masked, flags=re.IGNORECASE)
    return query[: boundary.start()] if boundary is not None else query


def _canonicalize_population_variables(population: str) -> str:
    """Canonicalize bound graph variables while preserving population semantics."""

    normalized = normalize_query_for_fingerprint(population).replace("\x1f", " ")
    literals: list[str] = []

    def mask_literal(match: re.Match[str]) -> str:
        literals.append(match.group(0))
        return f"__ori_literal_{len(literals) - 1}__"

    masked = re.sub(_CYPHER_LITERAL, mask_literal, normalized)
    declarations: list[tuple[int, str]] = []
    declaration_patterns = (
        # Node variables, including `(u:User)`, `(u {name: ...})`, and `(u)`.
        rf"\(\s*(?P<variable>{_CYPHER_IDENTIFIER})(?=\s*(?::|\{{|\)))",
        # Relationship variables. Relationship types remain untouched.
        rf"\[\s*(?P<variable>{_CYPHER_IDENTIFIER})(?=\s*(?::|\{{|\]))",
        # Named path variables such as `p=(...)`.
        rf"(?<![A-Za-z0-9_`])(?P<variable>{_CYPHER_IDENTIFIER})\s*=(?=\s*\()",
    )
    for pattern in declaration_patterns:
        declarations.extend(
            (match.start("variable"), _strip_cypher_identifier(match.group("variable")))
            for match in re.finditer(pattern, masked, flags=re.IGNORECASE)
        )

    variable_names: list[str] = []
    for _position, variable in sorted(declarations):
        if variable not in variable_names:
            variable_names.append(variable)

    canonical = masked
    canonical_names = {
        variable: f"v{index}" for index, variable in enumerate(variable_names)
    }

    def replace_declaration(match: re.Match[str]) -> str:
        variable = _strip_cypher_identifier(match.group("variable"))
        return (
            match.group("prefix")
            + canonical_names.get(variable, match.group("variable"))
        )

    canonical = re.sub(
        (
            rf"(?P<prefix>\(\s*)(?P<variable>{_CYPHER_IDENTIFIER})"
            r"(?=\s*(?::|\{|\)))"
        ),
        replace_declaration,
        canonical,
        flags=re.IGNORECASE,
    )
    canonical = re.sub(
        (
            rf"(?P<prefix>\[\s*)(?P<variable>{_CYPHER_IDENTIFIER})"
            r"(?=\s*(?::|\{|\]))"
        ),
        replace_declaration,
        canonical,
        flags=re.IGNORECASE,
    )
    canonical = re.sub(
        (
            rf"(?P<prefix>(?<![A-Za-z0-9_`]))"
            rf"(?P<variable>{_CYPHER_IDENTIFIER})(?=\s*=\s*\()"
        ),
        replace_declaration,
        canonical,
        flags=re.IGNORECASE,
    )

    for index, variable in enumerate(variable_names):
        # Literals were masked above. A leading ':' is a label/relationship
        # type, a leading '.' is a property key, and a trailing ':' is a map
        # key. None of those are graph-variable references.
        canonical = re.sub(
            (
                rf"(?<![A-Za-z0-9_`:.])`?{re.escape(variable)}`?"
                rf"(?![A-Za-z0-9_`]|\s*:)"
            ),
            f"v{index}",
            canonical,
        )

    for canonical_name in canonical_names.values():
        occurrences = tuple(
            re.finditer(
                (
                    rf"(?<![A-Za-z0-9_`]){re.escape(canonical_name)}"
                    rf"(?![A-Za-z0-9_`])"
                ),
                canonical,
            )
        )
        if len(occurrences) != 1:
            continue
        # A node/relationship variable used only at its declaration has no
        # population semantics. Normalize it to the equivalent anonymous
        # pattern so COUNT(*) and entity pages can use either spelling.
        canonical = re.sub(
            (
                rf"(?P<prefix>\(\s*){re.escape(canonical_name)}"
                r"(?=\s*(?::|\{|\)))"
            ),
            r"\g<prefix>",
            canonical,
            count=1,
        )
        canonical = re.sub(
            (
                rf"(?P<prefix>\[\s*){re.escape(canonical_name)}"
                r"(?=\s*(?::|\{|\]))"
            ),
            r"\g<prefix>",
            canonical,
            count=1,
        )

    canonical = " ".join(canonical.split()).casefold()
    for index, literal in enumerate(literals):
        canonical = canonical.replace(f"__ori_literal_{index}__", literal)
    return canonical


def _count_projection_identity(
    query: str,
    parsed_return: tuple[re.Match[str], dict[str, tuple[str, str]]],
) -> tuple[str | None, bool]:
    """Return the counted identity origin and whether the count is distinct."""

    projection, _return_scope = parsed_return
    count = re.fullmatch(
        (
            r"\s*COUNT\s*\(\s*(?P<distinct>DISTINCT\s+)?"
            rf"(?P<expression>\*|{_CYPHER_IDENTIFIER}"
            rf"(?:\s*\.\s*`?(?P<property>[A-Za-z_][A-Za-z0-9_]*)`?)?)"
            r"\s*\)"
            rf"(?:\s+AS\s+{_CYPHER_IDENTIFIER})?\s*"
        ),
        projection.group("body"),
        flags=re.IGNORECASE | re.DOTALL,
    )
    if (
        count is None
        or (
            count.group("property") is not None
            and count.group("property") not in {"objectid", "name"}
        )
    ):
        return None, False
    expression = count.group("expression").strip()
    if expression == "*":
        return None, count.group("distinct") is not None
    variable = _strip_cypher_identifier(expression.split(".", maxsplit=1)[0])
    inherited = _identity_scope_at(query, projection.start())
    return (
        inherited.get(variable, (variable, "node"))[0],
        count.group("distinct") is not None,
    )


def _terminal_identity_projection(
    query: str,
    population: str,
    parsed_return: tuple[re.Match[str], dict[str, tuple[str, str]]],
    *,
    is_count: bool,
) -> tuple[str, bool]:
    """Remove a terminal identity-only WITH while retaining its set semantics.

    ``WITH n`` and ``WITH n AS entity`` only rename or pass through the rows
    consumed by the final result projection. ``WITH DISTINCT n`` additionally
    deduplicates that identity population, so its distinctness becomes part of
    the population key. Any filter, aggregation, expansion, pagination, or
    multi-column projection remains in the key.
    """

    masked = re.sub(
        _CYPHER_LITERAL,
        lambda match: " " * len(match.group(0)),
        population,
    )
    with_matches = tuple(re.finditer(r"\bWITH\b", masked, flags=re.IGNORECASE))
    if not with_matches:
        return population, False
    terminal = with_matches[-1]
    body = population[terminal.end() :].strip()
    order_by = re.search(r"\bORDER\s+BY\b", body, flags=re.IGNORECASE)
    if order_by is not None:
        ordering = body[order_by.end() :].strip()
        body = body[: order_by.start()].strip()
        ordering = re.sub(
            r"\s+(?:ASC|DESC)\s*$",
            "",
            ordering,
            flags=re.IGNORECASE,
        ).strip()
    else:
        ordering = None
    distinct = re.match(r"^DISTINCT\b", body, flags=re.IGNORECASE)
    if distinct is not None:
        body = body[distinct.end() :].strip()
    passthrough = re.fullmatch(
        (
            rf"(?P<source>{_CYPHER_IDENTIFIER})"
            rf"(?:\s+AS\s+(?P<alias>{_CYPHER_IDENTIFIER}))?"
        ),
        body,
        flags=re.IGNORECASE,
    )
    if passthrough is None:
        return population, False

    source = _strip_cypher_identifier(passthrough.group("source"))
    alias = passthrough.group("alias")
    projected = (
        _strip_cypher_identifier(alias)
        if alias is not None
        else source
    )
    if ordering is not None:
        stable_order = re.fullmatch(
            (
                rf"(?P<variable>{_CYPHER_IDENTIFIER})\s*\.\s*"
                r"`?(?P<property>[A-Za-z_][A-Za-z0-9_]*)`?"
            ),
            ordering,
            flags=re.IGNORECASE,
        )
        if (
            stable_order is None
            or stable_order.group("property") != "objectid"
            or _strip_cypher_identifier(stable_order.group("variable"))
            not in {source, projected}
        ):
            return population, False
    inherited = _identity_scope_at(query, terminal.start())
    source_origin = inherited.get(source, (source, "node"))[0]
    if is_count:
        count_origin, _count_distinct = _count_projection_identity(
            query,
            parsed_return,
        )
        # COUNT(*) consumes the terminal single-column row population. A
        # named count must consume the same identity passed through WITH.
        if count_origin is not None and count_origin != source_origin:
            return population, False
    elif _returned_identity_origins(query) != frozenset({source_origin}):
        return population, False
    return population[: terminal.start()], distinct is not None


def _query_population_key(query: str) -> str:
    """Bind count/pages to one alpha-equivalent set population.

    The key preserves all population-changing clauses before the final result
    projection. It normalizes only a terminal identity passthrough and records
    whether either that passthrough or the final projection deduplicates the
    returned identity. This accepts harmless alias/formatting differences while
    refusing to bind an unfiltered count to a filtered page.
    """

    normalized_query = normalize_query_for_fingerprint(query).replace("\x1f", " ")
    parsed_return = _return_projection(normalized_query)
    if parsed_return is None:
        return _canonicalize_population_variables(normalized_query)
    projection, _return_scope = parsed_return
    population = normalized_query[: projection.start()]
    is_count = _is_count_query(normalized_query)
    population, terminal_distinct = _terminal_identity_projection(
        normalized_query,
        population,
        parsed_return,
        is_count=is_count,
    )
    if is_count:
        _count_origin, projection_distinct = _count_projection_identity(
            normalized_query,
            parsed_return,
        )
    else:
        projection_distinct = bool(
            re.match(
                r"\s*DISTINCT\b",
                projection.group("body"),
                flags=re.IGNORECASE,
            )
        )
    return (
        f"{_canonicalize_population_variables(population)}"
        f"|identity_distinct={str(terminal_distinct or projection_distinct).casefold()}"
    )


def _count_population_matches_page(
    count_key: str | None,
    page_key: str,
) -> bool:
    """Return whether a scalar count can certify one identity page population.

    Count and page queries must have the same population and identity
    distinctness. A distinct count paired with row-preserving pages is handled
    separately only when the returned receipts mechanically prove that every
    row contains one globally unique identity.
    """

    if count_key is None:
        return False
    marker = "|identity_distinct="
    if marker not in count_key or marker not in page_key:
        return count_key == page_key
    count_population, count_distinct = count_key.rsplit(marker, maxsplit=1)
    page_population, page_distinct = page_key.rsplit(marker, maxsplit=1)
    return (
        count_population == page_population
        and count_distinct == page_distinct
    )


def _distinct_count_matches_observed_rows(
    count_key: str | None,
    page_key: str,
    *,
    total_count: int | None,
    page_counts: Mapping[int, int],
    page_identity_ids: Mapping[int, frozenset[str]],
) -> bool:
    """Safely bind a distinct count to row-preserving identity pages.

    Some provider-generated Cypher counts distinct identities but emits
    ordinary identity rows. That is equivalent only when the receipts prove
    one explicit, globally unique object ID per returned row across every
    contiguous page. Duplicate or missing identity rows therefore cannot
    satisfy completeness merely because the raw row total equals the count.
    """

    if count_key is None or total_count is None:
        return False
    marker = "|identity_distinct="
    if marker not in count_key or marker not in page_key:
        return False
    count_population, count_distinct = count_key.rsplit(marker, maxsplit=1)
    page_population, page_distinct = page_key.rsplit(marker, maxsplit=1)
    if (
        count_population != page_population
        or count_distinct != "true"
        or page_distinct != "false"
        or set(page_counts) != set(page_identity_ids)
    ):
        return False
    returned_rows = sum(page_counts.values())
    identities = set().union(*page_identity_ids.values()) if page_identity_ids else set()
    return (
        returned_rows == total_count
        and len(identities) == total_count
        and all(
            len(page_identity_ids[offset]) == row_count
            for offset, row_count in page_counts.items()
        )
    )


def _contiguous_page_result_count(
    *,
    result_offset: int,
    page_size: int,
    page_counts: Mapping[int, int],
) -> int | None:
    """Return the accumulated row count only for one contiguous page prefix."""

    expected_offsets = tuple(
        result_offset + index * page_size
        for index in range(len(page_counts))
    )
    if tuple(sorted(page_counts)) != expected_offsets:
        return None
    return sum(page_counts.values())


def _successful_cypher_identity_ids(payload: Mapping[str, Any]) -> frozenset[str]:
    """Extract explicit graph-node and literal-row IDs from a successful receipt."""

    current: Any = payload
    nodes: Any = None
    for _depth in range(3):
        if not isinstance(current, Mapping):
            break
        candidate = current.get("nodes")
        if isinstance(candidate, (Mapping, list)):
            nodes = candidate
            break
        current = current.get("data")
    if isinstance(nodes, Mapping):
        node_values = nodes.values()
    elif isinstance(nodes, list):
        node_values = nodes
    else:
        node_values = ()

    identities: set[str] = set()
    for node in node_values:
        identity = _receipt_node_identity(node)
        if identity is not None:
            identities.add(identity)
    for _key, collection in _literal_node_collections(payload):
        for node in collection:
            identity = _receipt_node_identity(node)
            if identity is not None:
                identities.add(identity)
    for literal in _scalar_literals(payload):
        key = literal.get("key")
        value = literal.get("value")
        if (
            isinstance(key, str)
            and key.replace("_", "").casefold() == "objectid"
            and isinstance(value, str)
            and value.strip()
        ):
            identities.add(value.strip())
    return frozenset(identities)


def _receipt_node_identity(node: Any) -> str | None:
    if not isinstance(node, Mapping):
        return None
    containers = [node]
    for property_key in ("properties", "Props", "props"):
        properties = node.get(property_key)
        if isinstance(properties, Mapping):
            containers.append(properties)
    for container in containers:
        for key, value in container.items():
            if (
                str(key).replace("_", "").casefold() in {"objectid", "objectidentifier"}
                and isinstance(value, str)
                and value.strip()
            ):
                return value.strip()
    return None


def _successful_cypher_graph(
    payload: Mapping[str, Any],
) -> tuple[dict[str, Mapping[str, Any]], tuple[Mapping[str, Any], ...]]:
    """Return the pinned node map and edge rows from one successful receipt."""

    current: Any = payload
    for _depth in range(4):
        if not isinstance(current, Mapping):
            break
        raw_nodes = current.get("nodes")
        raw_edges = current.get("edges")
        if isinstance(raw_nodes, (Mapping, list)) and isinstance(raw_edges, list):
            if isinstance(raw_nodes, Mapping):
                nodes = {
                    str(key): value
                    for key, value in raw_nodes.items()
                    if isinstance(value, Mapping)
                }
            else:
                nodes = {
                    str(index): value
                    for index, value in enumerate(raw_nodes)
                    if isinstance(value, Mapping)
                }
            return nodes, tuple(edge for edge in raw_edges if isinstance(edge, Mapping))
        current = current.get("data")
    return {}, ()


def _receipt_graph_facts(
    task: TaskBundle,
    payload: Mapping[str, Any],
) -> tuple[
    tuple[str, ...],
    frozenset[tuple[str, str, str]],
    frozenset[str],
]:
    """Project only public-answer graph facts mechanically present in a receipt."""

    nodes, edges = _successful_cypher_graph(payload)
    identities_by_key = {
        key: identity
        for key, node in nodes.items()
        if (identity := _receipt_node_identity(node)) is not None
    }
    identities = tuple(dict.fromkeys(identities_by_key.values()))
    identity_lookup = {
        **identities_by_key,
        **{identity: identity for identity in identities},
    }
    edge_facts: set[tuple[str, str, str]] = set()
    for edge in edges:
        source = next(
            (
                identity_lookup.get(str(edge[key]))
                for key in (
                    "source_id",
                    "source",
                    "start_id",
                    "start",
                    "sourceNodeId",
                    "source_node_id",
                )
                if edge.get(key) is not None
            ),
            None,
        )
        target = next(
            (
                identity_lookup.get(str(edge[key]))
                for key in (
                    "target_id",
                    "target",
                    "end_id",
                    "end",
                    "targetNodeId",
                    "target_node_id",
                )
                if edge.get(key) is not None
            ),
            None,
        )
        relationship = next(
            (
                str(edge[key]).strip()
                for key in ("relationship", "kind", "label", "type", "edge", "name")
                if edge.get(key) is not None and str(edge[key]).strip()
            ),
            None,
        )
        if source is None or target is None or relationship is None:
            continue
        try:
            relationship = canonical_relationship_kind(relationship)
        except ValueError:
            pass
        edge_facts.add((source.casefold(), relationship, target.casefold()))

    required_property_keys = {
        predicate.property_name.casefold(): predicate.property_name
        for predicate in task.acceptance_spec.required_properties
    }
    property_facts: set[str] = set()
    for key, node in nodes.items():
        identity = identities_by_key.get(key)
        if identity is None:
            continue
        properties = node.get("properties")
        if not isinstance(properties, Mapping):
            properties = node.get("Props")
        if not isinstance(properties, Mapping):
            properties = node.get("props")
        if not isinstance(properties, Mapping):
            properties = node
        for raw_key, value in properties.items():
            property_key = required_property_keys.get(str(raw_key).casefold())
            if property_key is not None:
                property_facts.add(
                    entity_property_fact_key(identity, property_key, value)
                )
    return identities, frozenset(edge_facts), frozenset(property_facts)


def _answer_identity_token(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if not isinstance(value, Mapping):
        return None
    for key in ("object_id", "objectid", "entity_id", "id", "name"):
        token = value.get(key)
        if isinstance(token, str) and token.strip():
            return token.strip()
    return None


def _bind_final_answer_to_receipts(
    task: TaskBundle,
    answer: Mapping[str, Any] | None,
    *,
    projector: MCPTranscriptProjector,
    resolver: IdentityResolver,
) -> Mapping[str, Any] | None:
    """Bind model JSON facts to the latest complete claim-bound receipt.

    Set identities are projected directly from a mechanically complete page
    sequence. Witness entities are projected from the receipt, while asserted
    edges and properties absent from that receipt fail the answer before the
    shared EvidenceIR/comparator boundary.
    """

    if answer is None or not projector.finalization_ready:
        return answer
    bound = dict(answer)
    if task.claim_kind == "set":
        page_ids = (
            set().union(*projector.page_identity_ids.values())
            if (projector.page_identity_ids)
            else set()
        )
        row_count = sum(projector.page_counts.values())
        if row_count == len(page_ids) and row_count > 0:
            bound["entities"] = sorted(page_ids)
        elif row_count == 0 and any(
            event.unlocks_finalization for event in projector.events
        ):
            bound["entities"] = []
        return bound

    # Preserve the existing schema/identity failure taxonomy for witness
    # answers. Receipt binding is an additional truth boundary, not a repair
    # mechanism for malformed edge/property shapes or unknown identities.
    try:
        Draft202012Validator(task.answer_schema).validate(bound)
    except ValidationError:
        return answer

    receipt = next(
        (
            item
            for item in reversed(projector.receipts)
            if item.event.unlocks_finalization
            and item.observation.claim_relevant
            and not item.observation.truncated
        ),
        None,
    )
    if receipt is None:
        return bound
    identities, edge_facts, property_facts = _receipt_graph_facts(
        task,
        _tool_payload(receipt.result_text),
    )
    schema_properties = task.answer_schema.get("properties", {})

    def resolved(token: Any) -> str | None:
        raw = _answer_identity_token(token)
        if raw is None:
            return None
        try:
            return resolver.resolve(raw)
        except Exception:
            return None

    claimed_identity_tokens = tuple(
        token
        for token in (
            *(
                _answer_identity_token(item)
                for item in bound.get("entities", ())
                if isinstance(bound.get("entities"), list)
            ),
            *(
                token
                for field in ("edges", "supporting_edges")
                for edge in (
                    bound.get(field, ()) if isinstance(bound.get(field), list) else ()
                )
                if isinstance(edge, Mapping)
                for token in (
                    _answer_identity_token(edge.get("source_id") or edge.get("source")),
                    _answer_identity_token(edge.get("target_id") or edge.get("target")),
                )
            ),
            *(
                _answer_identity_token(fact.get("entity_id") or fact.get("object_id"))
                for fact in (
                    bound.get("observed_properties", ())
                    if isinstance(bound.get("observed_properties"), list)
                    else ()
                )
                if isinstance(fact, Mapping)
            ),
        )
        if token is not None
    )
    if any(resolved(token) is None for token in claimed_identity_tokens):
        return answer
    if "entities" in schema_properties or "entities" in bound:
        bound["entities"] = list(identities)

    for field in ("edges", "supporting_edges"):
        if (field not in schema_properties and field not in bound) or not isinstance(
            bound.get(field), list
        ):
            continue
        retained: list[Any] = []
        for edge in bound[field]:
            if not isinstance(edge, Mapping):
                continue
            source = resolved(edge.get("source_id") or edge.get("source"))
            target = resolved(edge.get("target_id") or edge.get("target"))
            relationship = (
                edge.get("relationship") or edge.get("kind") or edge.get("type")
            )
            if source is None or target is None or not isinstance(relationship, str):
                return None
            try:
                relationship = canonical_relationship_kind(relationship)
            except ValueError:
                relationship = relationship.strip()
            if (source.casefold(), relationship, target.casefold()) in edge_facts:
                retained.append(edge)
            else:
                return None
        bound[field] = retained

    if (
        "observed_properties" in schema_properties or "observed_properties" in bound
    ) and isinstance(bound.get("observed_properties"), list):
        retained_properties: list[Any] = []
        for fact in bound["observed_properties"]:
            if not isinstance(fact, Mapping):
                continue
            entity = resolved(fact.get("entity_id") or fact.get("object_id"))
            key = fact.get("key") or fact.get("property")
            if entity is None or not isinstance(key, str):
                return None
            try:
                value_key = entity_property_fact_key(
                    entity,
                    key,
                    fact.get("value"),
                )
            except (TypeError, ValueError):
                return None
            if value_key in property_facts:
                retained_properties.append(fact)
            else:
                return None
        bound["observed_properties"] = retained_properties
    return bound

class MCPTranscriptProjector:
    """Convert live tool outcomes into public-contract-derived evidence events."""

    def __init__(self, task: TaskBundle, profile: CapabilityProfile) -> None:
        self.task = task
        self.profile = profile
        self.events: list[EvidenceEvent] = []
        self.total_count: int | None = None
        self.total_count_query_key: str | None = None
        self.tool_calls = 0
        self.transcript_bytes = 0
        self.page_query_key: str | None = None
        self.page_counts: dict[int, int] = {}
        self.page_identity_ids: dict[int, frozenset[str]] = {}
        self.receipts: list[MCPToolAuditReceipt] = []
        self.observed_identity_ids: set[str] = set()

    @property
    def finalization_ready(self) -> bool:
        ready = False
        for event in self.events:
            if event.kind is EvidenceEventKind.TRUNCATED:
                ready = False
            elif event.unlocks_finalization:
                ready = True
        return ready

    def observe(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result_text: str,
        tool_error: ToolCallError | None,
    ) -> bool:
        self.tool_calls += 1
        output_bytes = len((result_text or "").encode("utf-8"))
        self.transcript_bytes += output_bytes
        operation = str(arguments.get("info_type") or arguments.get("operation") or "unknown")
        payload = _tool_payload(result_text)
        receipt_truncated = (
            output_bytes > self.task.binding.bounds.max_output_bytes
            or bool(payload.get("truncated"))
        )
        error_text = " ".join(
            part
            for part in (
                tool_error.message if tool_error is not None else "",
                str(payload.get("error") or ""),
                str(payload.get("error_type") or ""),
            )
            if part
        )
        lowered_error = error_text.casefold()
        error_type = str(payload.get("error_type") or "").casefold()
        policy_rejected = "policy_violation" in lowered_error or error_type == "policy_rejected"
        model_query_timeout = error_type == "query_timeout"
        model_query_error = error_type in {
            "query_error",
            "syntax_error",
            "cysql_syntax_error",
        }
        infrastructure_error_types = {
            "auth_error",
            "circuit_open",
            "client_timeout",
            "rate_limited",
            "server_error",
            "server_unavailable",
            "transport_error",
        }
        infrastructure_failure = not (model_query_error or model_query_timeout) and (
            error_type in infrastructure_error_types
            or any(
                marker in lowered_error
                for marker in (
                    "auth_error",
                    "authentication",
                    "circuit is open",
                    "circuit_open",
                    "connection error",
                    "connection refused",
                    "client_timeout",
                    "http 401",
                    "http 403",
                    "http 429",
                    "rate limit",
                    "rate_limited",
                    "server_error",
                    "server_unavailable",
                    "timed out",
                    "transport_error",
                    "unable to validate request signature",
                )
            )
            or bool(re.search(r"\bhttp\s+5\d\d\b", lowered_error))
        )
        arguments_valid = not any(
            marker in lowered_error
            for marker in ("invalid_arguments", "missing required", "validation error")
        )
        has_error = tool_error is not None or bool(payload.get("error"))
        succeeded = (
            not has_error
            and not policy_rejected
            and not infrastructure_failure
            and payload.get("success", True) is not False
        )
        if (
            succeeded
            and not receipt_truncated
            and tool_name == "cypher_query"
            and operation == "run"
        ):
            self.observed_identity_ids.update(
                _successful_cypher_identity_ids(payload)
            )
        query_error = (
            tool_name == "cypher_query"
            and has_error
            and arguments_valid
            and not policy_rejected
            and not infrastructure_failure
            and not model_query_timeout
            and (
                model_query_error
                or error_type
                not in infrastructure_error_types
            )
        )

        query = str(arguments.get("query") or "")
        scalar_count = _scalar_count(payload) if tool_name == "cypher_query" else None
        is_count = bool(query and _is_count_query(query))
        evidence_contract = self.task.binding.mcp_evidence_contract
        claim_relevant = bool(
            evidence_contract is not None
            and tool_name == evidence_contract.tool_name
            and operation == evidence_contract.operation
            and _query_matches_public_claim(
                self.task,
                query,
                is_count=is_count,
            )
        )
        negative_scope_mode = (
            _negative_query_scope_mode(self.task, query)
            if self.task.claim_kind == "absence"
            else None
        )
        if (
            negative_scope_mode == "broader"
            and scalar_count is not None
            and scalar_count > 0
        ):
            # A zero result over a broader wildcard or undirected search is a
            # stronger absence proof. A non-zero result may use an out-of-scope
            # relationship or direction, so it cannot contradict the narrower
            # public claim.
            claim_relevant = False
        companion_count_relevant = bool(
            is_count
            and evidence_contract is not None
            and tool_name == evidence_contract.tool_name
            and operation == evidence_contract.operation
            and _query_matches_public_claim(
                self.task,
                query,
                is_count=True,
                allow_set_companion_count=True,
            )
        )
        if (
            is_count
            and scalar_count is not None
            and (claim_relevant or companion_count_relevant)
            and succeeded
            and not receipt_truncated
        ):
            self.total_count = scalar_count
            self.total_count_query_key = _query_population_key(query)

        witness_claim = self.task.claim_kind in {"route", "decision"}
        if (
            witness_claim
            and claim_relevant
            and succeeded
            and not _has_positive_graph_path(payload)
        ):
            # A path-shaped query is not proof when BloodHound returned only
            # nodes or scalars. This checks the public result kind only; it does
            # not consult a sealed route, mechanism, or expected endpoint.
            claim_relevant = False
        result_count = _result_cardinality(
            payload,
            prefer_graph_counts=witness_claim,
        )
        if result_count is None and tool_name == "graph_analysis" and operation == "search":
            result_count = _graph_search_cardinality(payload)
        if (
            self.task.claim_kind in {"route", "decision"}
            and result_count is not None
            and result_count > 0
        ):
            # A graph response represents one bounded witness claim even when
            # its rendering contains several nodes and edges. Hop and byte
            # budgets are enforced separately and the comparator validates the
            # complete ordered route.
            result_count = 1
        total_count: int | None = None
        complete = False
        negative_proof = False

        if tool_name == "cypher_query" and operation == "run":
            if is_count:
                if self.task.claim_kind in {"count", "absence"}:
                    result_count = scalar_count
                    total_count = scalar_count
                    complete = scalar_count is not None
                    negative_proof = self.task.claim_kind == "absence" and scalar_count == 0
                else:
                    # A companion count is useful state but does not prove an
                    # entity set or route by itself. It can complete pages that
                    # arrived first, however; tool order must not change the
                    # meaning of an otherwise identical proof transcript.
                    result_count = None
                    page_query_key = self.page_query_key
                    if (
                        self.task.claim_kind == "set"
                        and page_query_key is not None
                        and self.total_count is not None
                        and (
                            _count_population_matches_page(
                                self.total_count_query_key,
                                page_query_key,
                            )
                            or _distinct_count_matches_observed_rows(
                                self.total_count_query_key,
                                page_query_key,
                                total_count=self.total_count,
                                page_counts=self.page_counts,
                                page_identity_ids=self.page_identity_ids,
                            )
                        )
                    ):
                        result_count = _contiguous_page_result_count(
                            result_offset=self.task.binding.bounds.result_offset,
                            page_size=self.task.binding.bounds.page_size,
                            page_counts=self.page_counts,
                        )
                        total_count = self.total_count
                        complete = (
                            result_count is not None
                            and result_count == total_count
                        )
                        if complete:
                            claim_relevant = True
            else:
                total_count = (
                    self.total_count
                    if _count_population_matches_page(
                        self.total_count_query_key,
                        _query_population_key(query),
                    )
                    else None
                )
                if self.task.claim_kind in {"route", "decision"}:
                    complete = False
                else:
                    window = _page_window(query)
                    if (
                        window is not None
                        and result_count is not None
                        and window[1] == self.task.binding.bounds.page_size
                        and window[0] >= self.task.binding.bounds.result_offset
                        and (window[0] - self.task.binding.bounds.result_offset)
                        % self.task.binding.bounds.page_size
                        == 0
                        and succeeded
                        and not receipt_truncated
                    ):
                        query_key = _page_query_key(query)
                        receipt_identity_ids = _successful_cypher_identity_ids(payload)
                        if self.page_query_key in {None, query_key}:
                            self.page_query_key = query_key
                            self.page_counts[window[0]] = result_count
                            self.page_identity_ids[window[0]] = receipt_identity_ids
                        else:
                            self.page_query_key = query_key
                            self.page_counts = {window[0]: result_count}
                            self.page_identity_ids = {
                                window[0]: receipt_identity_ids
                            }
                        result_count = _contiguous_page_result_count(
                            result_offset=self.task.binding.bounds.result_offset,
                            page_size=self.task.binding.bounds.page_size,
                            page_counts=self.page_counts,
                        )
                        if (
                            total_count is None
                            and _distinct_count_matches_observed_rows(
                                self.total_count_query_key,
                                query_key,
                                total_count=self.total_count,
                                page_counts=self.page_counts,
                                page_identity_ids=self.page_identity_ids,
                            )
                        ):
                            total_count = self.total_count
                    if self.task.binding.bounds.require_total_count:
                        complete = (
                            window is not None
                            and total_count is not None
                            and result_count == total_count
                        )
                    else:
                        complete = (
                            window
                            == (
                                self.task.binding.bounds.result_offset,
                                self.task.binding.bounds.page_size,
                            )
                            and result_count is not None
                            and result_count <= self.task.binding.bounds.max_result_cardinality
                        )
        elif tool_name == "graph_analysis" and operation == "shortest_path":
            complete = True
            negative_proof = self.task.claim_kind == "absence" and result_count == 0
        else:
            declared_total = _declared_total(payload)
            if result_count is None and succeeded:
                # Scalar object detail is one bounded result. Do not treat
                # metadata-only or malformed wrappers as positive evidence.
                data = payload.get("data")
                if isinstance(data, Mapping) and data:
                    result_count = 1
            total_count = declared_total
            if self.task.claim_kind == "count" and declared_total is not None:
                result_count = declared_total
                complete = True
            elif self.task.claim_kind == "set":
                tool_window = _declared_tool_window(arguments, payload)
                complete = bool(
                    tool_window is not None
                    and declared_total is not None
                    and result_count is not None
                    and tool_window[0] == self.task.binding.bounds.result_offset
                    and tool_window[1] == self.task.binding.bounds.page_size
                    and result_count == declared_total
                    and result_count <= tool_window[1]
                )
            else:
                complete = operation in {
                    "info",
                    "list",
                    "search",
                    "cert_template_info",
                    "enterprise_ca_info",
                    "root_ca_info",
                }

        if result_count is None:
            # Unknown or changed MCP response shapes are not completeness
            # proofs. Fail closed as inconclusive evidence without violating
            # the strict ToolObservation invariants or crashing the loop.
            total_count = None
            complete = False
            negative_proof = False

        truncated = (
            receipt_truncated
            or self.transcript_bytes > self.task.binding.bounds.max_transcript_bytes
            or self.tool_calls > self.task.binding.bounds.max_tool_calls
        )
        if truncated:
            complete = False
            negative_proof = False
        observation = ToolObservation(
            tool_name=tool_name,
            operation=operation,
            succeeded=succeeded,
            claim_relevant=claim_relevant,
            arguments_valid=arguments_valid,
            policy_rejected=policy_rejected,
            query_timeout=(
                tool_name == "cypher_query"
                and has_error
                and arguments_valid
                and not policy_rejected
                and model_query_timeout
            ),
            query_error=query_error,
            infrastructure_failure=infrastructure_failure,
            result_count=result_count,
            total_count=total_count,
            pages_received=max(1, len(self.page_counts)),
            complete=complete,
            truncated=truncated,
            negative_proof=negative_proof,
            output_bytes=output_bytes,
        )
        event = classify_tool_observation(self.task, self.profile, observation)
        self.events.append(event)
        receipt_payload = {
            "sequence": len(self.receipts) + 1,
            "tool_name": tool_name,
            "operation": operation,
            "arguments": dict(arguments),
            "result_text": result_text,
            "tool_error": tool_error.message if tool_error is not None else None,
            "observation": observation,
            "event": event,
            "receipt_fingerprint": "0" * 64,
        }
        receipt_payload["receipt_fingerprint"] = canonical_sha256(
            receipt_payload,
            exclude_fields=("receipt_fingerprint",),
        )
        self.receipts.append(MCPToolAuditReceipt.model_validate(receipt_payload))
        return self.finalization_ready


def _answer_schema_valid(
    task: TaskBundle,
    value: Mapping[str, Any] | None,
    *,
    resolver: IdentityResolver,
) -> bool:
    if value is None:
        return False
    try:
        validate_and_normalize_evidence(
            value,
            answer_schema=task.answer_schema,
            resolver=resolver,
            task_id=task.task_id,
        )
    except EvidenceIdentityCatalogError:
        # Identity binding is downstream graph/catalog classification, not a
        # JSON-schema defect. Do not spend the one schema-only retry asking the
        # model to reformat an already structured answer.
        return True
    except EvidenceNormalizationError:
        return False
    return True


async def _schema_only_retry(
    *,
    task: TaskBundle,
    model: str,
    malformed_output: str,
    model_base_url: str | None,
    ollama_options: dict[str, Any] | None,
    transport: TextTransport,
    max_tokens: int,
    api_surface: ProviderApiSurface | str,
    transcript: tuple[dict[str, Any], ...] = (),
    structured_output_schema: dict[str, Any] | None = None,
) -> ModelResponse:
    def provider_message(message: Mapping[str, Any]) -> dict[str, Any] | None:
        role = message.get("role")
        if role not in {"user", "assistant", "tool"}:
            return None
        projected: dict[str, Any] = {
            "role": role,
            "content": message.get("content") or "",
        }
        if role == "assistant" and isinstance(message.get("tool_calls"), list):
            calls: list[dict[str, Any]] = []
            for raw_call in message["tool_calls"]:
                if not isinstance(raw_call, Mapping):
                    continue
                raw_function = raw_call.get("function")
                if isinstance(raw_function, Mapping):
                    name = raw_function.get("name")
                    arguments = raw_function.get("arguments", "")
                else:
                    name = raw_function
                    arguments = raw_call.get("arguments", {})
                if not isinstance(name, str) or not name:
                    continue
                if not isinstance(arguments, str):
                    arguments = json.dumps(arguments, sort_keys=True, separators=(",", ":"))
                calls.append(
                    {
                        "id": str(raw_call.get("id") or ""),
                        "type": "function",
                        "function": {"name": name, "arguments": arguments},
                    }
                )
            if calls:
                projected["tool_calls"] = calls
        if role == "tool":
            tool_call_id = message.get("tool_call_id")
            if not isinstance(tool_call_id, str) or not tool_call_id:
                return None
            projected["tool_call_id"] = tool_call_id
        return projected

    prior_messages = [
        projected
        for message in transcript
        if (projected := provider_message(message)) is not None
    ]
    if not prior_messages:
        prior_messages = [
            {"role": "user", "content": task.question},
            {"role": "assistant", "content": malformed_output},
        ]
    return await transport(
        model=model,
        messages=[*prior_messages, {"role": "user", "content": SCHEMA_ONLY_RETRY_INSTRUCTION}],
        system=mcp_system_prompt(task),
        base_url=model_base_url,
        max_tokens=max_tokens,
        ollama_options=ollama_options,
        api_surface=api_surface,
        structured_output_schema=structured_output_schema,
    )


async def run_mcp_model_task_v2(
    *,
    task: TaskBundle,
    oracle: OracleBundle,
    resolver: IdentityResolver,
    profile: CapabilityProfile,
    bundle: MCPServerBundle,
    model: str,
    model_base_url: str | None,
    tool_loop: MCPToolLoop | str,
    max_steps: int,
    max_tokens: int = 2048,
    ollama_options: dict[str, Any] | None = None,
    telemetry_adapter: str = OPENAI_COMPAT_TELEMETRY_AUTO,
    read_timeout_seconds: float = DEFAULT_MCP_OLLAMA_READ_TIMEOUT_SECONDS,
    tool_timeout_seconds: float = 60.0,
    graph_fact_registry: GraphFactRegistry | None = None,
    api_surface: ProviderApiSurface | str = ProviderApiSurface.AUTO,
    structured_output_mode: str = "prompt_local_validation",
    transport: TextTransport = call_provider_text,
) -> tuple[MCPV2Outcome, ProviderRunRecord]:
    """Run one certified native MCP loop and finalize through the V2 reducer."""

    resolved_loop = validate_certified_mcp_loop(tool_loop)
    provider_name = model.split("/", 1)[0]
    requested_api_surface = ProviderApiSurface(api_surface)
    resolved_api_surface = resolve_api_surface(provider_name, requested_api_surface)
    validate_release1_api_surface(provider_name, resolved_api_surface)
    if task.binding.mcp_tool_loop != resolved_loop.value:
        raise V2ModelRuntimeError("runtime MCP loop does not match the task binding fingerprint")
    if task.binding.mcp_resource_mode != "off":
        raise V2ModelRuntimeError("certified v2 MCP tasks require resource_mode=off")

    projector = MCPTranscriptProjector(task, profile)
    bounded_steps = min(max_steps, task.binding.bounds.max_tool_calls)
    partial_response: ModelResponse | None = None
    partial_messages: list[Any] = []

    def observe_progress(response: ModelResponse, messages: list[Any]) -> None:
        nonlocal partial_response, partial_messages
        partial_response = response
        partial_messages = list(messages)

    loop_kwargs = {
        "task": None,
        "public_question": task.question,
        "model_name": model,
        "base_url": model_base_url,
        "max_tokens": max_tokens,
        "tools": bundle.tools,
        "max_steps": bounded_steps,
        # The discovered BloodHound prompt describes a different, resource-first
        # workflow and can contradict this certified resource_mode=off contract.
        # V2 retains discovery metadata in readiness artifacts but sends one
        # authoritative runtime system prompt to the evaluated model.
        "server_prompt_text": "",
        "server_prompt_name": "",
        "available_prompt_names": bundle.available_prompt_names,
        "prompt_discovery_status": bundle.prompt_discovery_status,
        "resource_mode": "off",
        "system_prompt_override": mcp_system_prompt(task),
        "tool_result_observer": projector.observe,
        "progress_observer": observe_progress,
        "tool_timeout_seconds": tool_timeout_seconds,
        "finalization_schema": (
            _json_schema_response_format(
                task.answer_schema,
                name="ori_mcp_submission",
            )
            if structured_output_mode == "json_schema"
            and resolved_loop is MCPToolLoop.NATIVE_OPENAI_COMPATIBLE
            else None
        ),
    }
    surface = (
        V2RuntimeSurface.MCP_NATIVE_OPENAI_COMPATIBLE
        if resolved_loop is MCPToolLoop.NATIVE_OPENAI_COMPATIBLE
        else V2RuntimeSurface.MCP_NATIVE_OLLAMA
    )
    task_started = asyncio.get_running_loop().time()
    deadline = asyncio.timeout(task.binding.bounds.timeout_seconds)
    try:
        async with deadline:
            if resolved_loop is MCPToolLoop.NATIVE_OPENAI_COMPATIBLE:
                response, _trajectory, messages = await _run_openai_compat_mcp_loop(
                    **loop_kwargs,
                    extra_body={"options": ollama_options} if ollama_options else None,
                    telemetry_adapter=telemetry_adapter,
                    read_timeout_seconds=read_timeout_seconds,
                )
            elif resolved_loop is MCPToolLoop.NATIVE_OLLAMA:
                response, _trajectory, messages = await _run_ollama_mcp_loop(
                    **loop_kwargs,
                    ollama_options=ollama_options,
                    ollama_read_timeout_seconds=read_timeout_seconds,
                )
            else:
                raise V2ModelRuntimeError(
                    "Inspect-backed model campaigns require an Inspect-bound task catalog"
                )
            response = replace(
                response,
                provider_metrics={
                    **dict(response.provider_metrics),
                    "requested_api_surface": requested_api_surface.value,
                    "resolved_api_surface": resolved_api_surface.value,
                },
            )
    except asyncio.CancelledError as exc:
        response = _failure_response(
            partial=partial_response,
            model=model,
            parse_stage="mcp_interrupted",
            error="MCP task interrupted before completion",
            elapsed_seconds=asyncio.get_running_loop().time() - task_started,
            metrics={
                "infra_scope": "operator",
                "infra_error_subtype": "INTERRUPTED",
                "infra_retryable": False,
            },
        )
        transcript = _transcript_payload(partial_messages)
        sample = _interrupted_sample(
            task,
            oracle,
            "MCP task interrupted before completion",
        )
        provider = _record(
            task=task,
            model=model,
            surface=surface.value,
            response=response,
            mcp_events=tuple(projector.events),
            mcp_tool_receipts=tuple(projector.receipts),
            mcp_transcript=transcript,
            transcript_digest=canonical_sha256(transcript),
        )
        raise V2ModelTaskCancelled(sample, provider) from exc
    except MCPNoProgressTimeout as exc:
        projector.events.append(
            classify_evidence_event(
                task,
                profile,
                kind=EvidenceEventKind.INFRASTRUCTURE_FAILURE,
                reason=f"{exc.subtype}: native MCP loop made no progress",
            )
        )
        response = _failure_response(
            partial=partial_response,
            model=model,
            parse_stage="mcp_no_progress_timeout",
            error=str(exc),
            elapsed_seconds=asyncio.get_running_loop().time() - task_started,
            metrics={
                "infra_scope": "provider",
                "infra_error_subtype": exc.subtype,
                "infra_retryable": True,
            },
        )
        messages = partial_messages
    except MCPToolInfrastructureError as exc:
        if not any(
            event.kind is EvidenceEventKind.INFRASTRUCTURE_FAILURE for event in projector.events
        ):
            projector.events.append(
                classify_evidence_event(
                    task,
                    profile,
                    kind=EvidenceEventKind.INFRASTRUCTURE_FAILURE,
                    reason=f"{exc.subtype}: MCP tool infrastructure failure",
                )
            )
        response = _failure_response(
            partial=partial_response,
            model=model,
            parse_stage="mcp_tool_infrastructure_failure",
            error=str(exc),
            elapsed_seconds=asyncio.get_running_loop().time() - task_started,
            metrics={
                "infra_scope": "mcp_tool",
                "infra_error_subtype": exc.subtype,
                "infra_retryable": True,
            },
        )
        messages = partial_messages
    except TimeoutError as exc:
        if deadline.expired():
            projector.events.append(
                classify_evidence_event(
                    task,
                    profile,
                    kind=EvidenceEventKind.TASK_TIMEOUT,
                    reason="MCP task execution budget exhausted",
                )
            )
            response = _failure_response(
                partial=partial_response,
                model=model,
                parse_stage="mcp_task_timeout",
                error="MCP task execution budget exhausted",
                elapsed_seconds=task.binding.bounds.timeout_seconds,
            )
        else:
            projector.events.append(
                classify_evidence_event(
                    task,
                    profile,
                    kind=EvidenceEventKind.HARNESS_FAILURE,
                    reason="untyped inner TimeoutError escaped its runtime boundary",
                )
            )
            response = _failure_response(
                partial=partial_response,
                model=model,
                parse_stage="mcp_untyped_inner_timeout",
                error=str(exc) or type(exc).__name__,
                elapsed_seconds=asyncio.get_running_loop().time() - task_started,
            )
        messages = partial_messages
    except Exception as exc:
        infrastructure = _provider_infrastructure_details(exc)
        if infrastructure is not None:
            subtype, retryable = infrastructure
            projector.events.append(
                classify_evidence_event(
                    task,
                    profile,
                    kind=EvidenceEventKind.INFRASTRUCTURE_FAILURE,
                    reason=f"{subtype}: provider infrastructure failure",
                )
            )
            response = _failure_response(
                partial=partial_response,
                model=model,
                parse_stage="mcp_provider_infrastructure_failure",
                error=str(exc),
                elapsed_seconds=asyncio.get_running_loop().time() - task_started,
                metrics={
                    "infra_scope": "provider",
                    "infra_error_subtype": subtype,
                    "infra_retryable": retryable,
                },
            )
        else:
            projector.events.append(
                classify_evidence_event(
                    task,
                    profile,
                    kind=EvidenceEventKind.HARNESS_FAILURE,
                    reason=f"MCP harness failure: {type(exc).__name__}",
                )
            )
            response = _failure_response(
                partial=partial_response,
                model=model,
                parse_stage="mcp_harness_failure",
                error=str(exc),
                elapsed_seconds=asyncio.get_running_loop().time() - task_started,
            )
        messages = partial_messages

    parsed_final: ParsedJsonObject | None = None
    try:
        parsed_final = _parse_json_object(response.raw_text)
        final_answer = parsed_final.payload
    except V2ModelRuntimeError:
        final_answer = None
    retry_response: ModelResponse | None = None
    retry_answer: Mapping[str, Any] | None = None
    parsed_retry: ParsedJsonObject | None = None
    retry_contract_error: str | None = None
    schema_retry_infrastructure: tuple[str, str, bool] | None = None
    terminal_runtime_failure = any(
        event.kind
        in {
            EvidenceEventKind.TASK_TIMEOUT,
            EvidenceEventKind.INFRASTRUCTURE_FAILURE,
            EvidenceEventKind.HARNESS_FAILURE,
        }
        for event in projector.events
    )
    if (
        projector.finalization_ready
        and not terminal_runtime_failure
        and not _answer_schema_valid(
            task,
            final_answer,
            resolver=resolver,
        )
    ):
        remaining_seconds = max(
            0.0,
            task.binding.bounds.timeout_seconds
            - (asyncio.get_running_loop().time() - task_started),
        )
        retry_max_tokens = min(
            32_768,
            max(
                2_048,
                (task.binding.bounds.max_output_bytes + 3) // 4,
            ),
        )
        try:
            retry_response = await asyncio.wait_for(
                _schema_only_retry(
                    task=task,
                    model=model,
                    malformed_output=response.raw_text,
                    model_base_url=model_base_url,
                    ollama_options=ollama_options,
                    transport=transport,
                    max_tokens=retry_max_tokens,
                    api_surface=requested_api_surface,
                    transcript=_transcript_payload(messages),
                    structured_output_schema=(
                        _json_schema_response_format(
                            task.answer_schema,
                            name="ori_mcp_submission",
                        )
                        if structured_output_mode == "json_schema"
                        else None
                    ),
                ),
                timeout=remaining_seconds,
            )
        except asyncio.CancelledError as exc:
            detail = "MCP schema-only retry interrupted before completion"
            interrupted_response = _failure_response(
                partial=response,
                model=model,
                parse_stage="mcp_interrupted",
                error=detail,
                elapsed_seconds=asyncio.get_running_loop().time() - task_started,
                metrics={
                    "infra_scope": "operator",
                    "infra_error_subtype": "INTERRUPTED",
                    "infra_retryable": False,
                },
            )
            transcript = _transcript_payload(messages)
            raise V2ModelTaskCancelled(
                _interrupted_sample(task, oracle, detail),
                _record(
                    task=task,
                    model=model,
                    surface=surface.value,
                    response=interrupted_response,
                    mcp_events=tuple(projector.events),
                    mcp_tool_receipts=tuple(projector.receipts),
                    mcp_transcript=transcript,
                    transcript_digest=canonical_sha256(transcript),
                ),
            ) from exc
        except TimeoutError:
            projector.events.append(
                classify_evidence_event(
                    task,
                    profile,
                    kind=EvidenceEventKind.TASK_TIMEOUT,
                    reason="MCP task execution budget exhausted during schema retry",
                )
            )
        except Exception as exc:
            infrastructure = _provider_infrastructure_details(exc)
            if infrastructure is not None:
                subtype, retryable = infrastructure
                schema_retry_infrastructure = ("provider", subtype, retryable)
                projector.events.append(
                    classify_evidence_event(
                        task,
                        profile,
                        kind=EvidenceEventKind.INFRASTRUCTURE_FAILURE,
                        reason=f"{subtype}: schema-only retry provider failure",
                    )
                )
            else:
                projector.events.append(
                    classify_evidence_event(
                        task,
                        profile,
                        kind=EvidenceEventKind.HARNESS_FAILURE,
                        reason=(f"schema-only retry harness failure: {type(exc).__name__}"),
                    )
                )
        if retry_response is not None and retry_response.error:
            schema_retry_infrastructure = (
                _provider_response_infrastructure_details(retry_response)
            )
            projector.events.append(
                classify_evidence_event(
                    task,
                    profile,
                    kind=EvidenceEventKind.INFRASTRUCTURE_FAILURE,
                    reason="schema-only retry provider failure",
                )
            )
        elif retry_response is not None:
            try:
                parsed_retry = _parse_json_object(retry_response.raw_text)
                candidate_retry_answer = parsed_retry.payload
                if _retry_adds_answer_facts(
                    response.raw_text,
                    candidate_retry_answer,
                ):
                    retry_contract_error = "SCHEMA_RETRY_ADDED_NEW_FACTS"
                    candidate_retry_answer = None
            except V2ModelRuntimeError:
                candidate_retry_answer = None
            retry_answer = candidate_retry_answer

    transcript_payload = list(_transcript_payload(messages))
    transcript_size = len(
        json.dumps(transcript_payload, sort_keys=True, default=str).encode("utf-8")
    )
    transcript_size += len(response.thinking.encode("utf-8"))
    if retry_response is not None:
        transcript_size += len(retry_response.raw_text.encode("utf-8"))
        transcript_size += len(retry_response.thinking.encode("utf-8"))
    output_size = len(response.raw_text.encode("utf-8"))
    if retry_response is not None:
        output_size += len(retry_response.raw_text.encode("utf-8"))
    bounds_exceeded = (
        projector.tool_calls > task.binding.bounds.max_tool_calls
        or transcript_size > task.binding.bounds.max_transcript_bytes
        or output_size > task.binding.bounds.max_output_bytes
    )
    if bounds_exceeded and not any(
        event.kind is EvidenceEventKind.INFRASTRUCTURE_FAILURE for event in projector.events
    ):
        source_event = next(
            (
                event
                for event in reversed(projector.events)
                if event.tool_name is not None and event.operation is not None
            ),
            None,
        )
        diagnostics = tuple(event for event in projector.events if not event.unlocks_finalization)
        if source_event is not None:
            diagnostics = (
                *diagnostics,
                classify_evidence_event(
                    task,
                    profile,
                    kind=EvidenceEventKind.TRUNCATED,
                    tool_name=source_event.tool_name,
                    operation=source_event.operation,
                    reason="MCP sample exceeded its execution bounds",
                ),
            )
        projector.events = list(diagnostics)

    # Claim-bound BloodHound receipts are authoritative for graph facts. Set
    # answers can be materialized from complete identity pages so a 500-row
    # proof does not depend on the model echoing every ID; witness assertions
    # fail closed when unsupported edges or properties are asserted.
    final_output_compliant = (
        Draft202012Validator(task.answer_schema).is_valid(dict(final_answer))
        if final_answer is not None
        else False
    )
    retry_output_compliant = (
        Draft202012Validator(task.answer_schema).is_valid(dict(retry_answer))
        if retry_answer is not None
        else False
    )
    submitted_final_answer = final_answer
    bound_final_answer = _bind_final_answer_to_receipts(
        task,
        final_answer,
        projector=projector,
        resolver=resolver,
    )
    final_receipt_attested = submitted_final_answer is None or bound_final_answer is not None
    final_answer = (
        bound_final_answer if bound_final_answer is not None else submitted_final_answer
    )
    submitted_retry_answer = retry_answer
    bound_retry_answer = _bind_final_answer_to_receipts(
        task,
        retry_answer,
        projector=projector,
        resolver=resolver,
    )
    retry_receipt_attested = submitted_retry_answer is None or bound_retry_answer is not None
    retry_answer = (
        bound_retry_answer if bound_retry_answer is not None else submitted_retry_answer
    )

    outcome = run_mcp_task_v2(
        surface=surface,
        task=task,
        oracle=oracle,
        resolver=resolver,
        profile=profile,
        events=tuple(projector.events),
        final_answer=final_answer,
        retry_answer=retry_answer,
        observed_identity_ids=tuple(sorted(projector.observed_identity_ids)),
        graph_fact_registry=graph_fact_registry,
        final_output_normalized=(
            parsed_final.format_normalized if parsed_final is not None else False
        ),
        retry_output_normalized=(
            parsed_retry.format_normalized if parsed_retry is not None else False
        ),
        final_output_compliant=final_output_compliant,
        retry_output_compliant=retry_output_compliant,
        final_receipt_attested=final_receipt_attested,
        retry_receipt_attested=retry_receipt_attested,
        retry_contract_error=retry_contract_error,
    )
    combined_response = response
    if retry_response is not None:
        combined_response = ModelResponse(
            raw_text=response.raw_text + "\n" + retry_response.raw_text,
            cypher=None,
            parse_stage="mcp_final_answer_with_schema_retry",
            tokens_input=response.tokens_input + retry_response.tokens_input,
            tokens_output=response.tokens_output + retry_response.tokens_output,
            elapsed_seconds=response.elapsed_seconds + retry_response.elapsed_seconds,
            model=response.model,
            thinking=response.thinking + retry_response.thinking,
            error=retry_response.error,
            provider_metrics={
                "initial": response.provider_metrics,
                "schema_retry": retry_response.provider_metrics,
            },
        )
    if schema_retry_infrastructure is not None:
        retry_scope, retry_subtype, retryable = schema_retry_infrastructure
        combined_response = replace(
            combined_response,
            provider_metrics={
                **dict(combined_response.provider_metrics),
                "infra_scope": retry_scope,
                "infra_error_subtype": retry_subtype,
                "infra_retryable": retryable,
            },
        )
    if (
        outcome.sample.execution_class is ExecutionClass.INFRA_FAILURE
        and "infra_scope" not in combined_response.provider_metrics
    ):
        failed_receipt = next(
            (
                receipt
                for receipt in reversed(projector.receipts)
                if receipt.observation.infrastructure_failure
            ),
            None,
        )
        if failed_receipt is not None:
            payload = _tool_payload(failed_receipt.result_text)
            subtype = str(payload.get("error_type") or "MCP_TOOL_INFRASTRUCTURE")
            retryable = subtype.casefold() not in {
                "auth_error",
                "authentication",
            }
            combined_response = replace(
                combined_response,
                provider_metrics={
                    **dict(combined_response.provider_metrics),
                    "infra_scope": (
                        "bloodhound" if failed_receipt.tool_name == "cypher_query" else "mcp_tool"
                    ),
                    "infra_error_subtype": subtype,
                    "infra_retryable": retryable,
                },
            )
    record = _record(
        task=task,
        model=model,
        surface=surface.value,
        response=combined_response,
        mcp_events=tuple(projector.events),
        mcp_tool_receipts=tuple(projector.receipts),
        mcp_finalization=outcome.finalization.model_dump(mode="json"),
        mcp_transcript=tuple(transcript_payload),
        transcript_digest=canonical_sha256(transcript_payload),
    )
    return outcome, record
