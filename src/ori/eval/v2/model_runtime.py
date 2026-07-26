"""Model-backed protocol-v2 execution without legacy task or scorer dispatch."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import httpx
from inspect_ai.tool import ToolCallError
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from pydantic import Field, model_validator

from ori.eval.adapter import ModelResponse, call_provider_text
from ori.eval.mcp_runtime import (
    DEFAULT_MCP_OLLAMA_READ_TIMEOUT_SECONDS,
    OPENAI_COMPAT_TELEMETRY_AUTO,
    MCPServerBundle,
    _run_ollama_mcp_loop,
    _run_openai_compat_mcp_loop,
)

from .compiler import DIRECT_RESULT_CONTRACT_VERSION
from .direct_adapter import DirectV2Outcome
from .evidence import EvidenceNormalizationError, validate_and_normalize_evidence
from .fingerprint import canonical_sha256
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
from .runtime import V2RuntimeSurface, run_direct_task_v2, run_mcp_task_v2
from .schema import (
    CapabilityProfile,
    DirectExecutionReceipt,
    ExecutionClass,
    NegativeReasonCode,
    OracleBundle,
    StrictModel,
    TaskBundle,
)
from .scoring import SampleOutcomeCode, SampleResult


class V2ModelRuntimeError(ValueError):
    """Raised when a model-facing v2 contract is mixed, stale, or unsupported."""


class DirectSubmissionV2(StrictModel):
    """The model's query plus claim-only assertions not derivable from graph rows."""

    query: str = Field(strict=True, min_length=1)
    assertion: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def query_is_nonempty(self) -> DirectSubmissionV2:
        if not self.query.strip():
            raise ValueError("direct submission query cannot be blank")
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
    direct_query_digest: str | None = None
    direct_receipt: DirectExecutionReceipt | None = None
    mcp_events: tuple[EvidenceEvent, ...] = ()
    mcp_finalization: dict[str, Any] | None = None
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


def _record(
    *,
    task: TaskBundle,
    model: str,
    surface: str,
    response: ModelResponse,
    direct_query: str | None = None,
    direct_receipt: DirectExecutionReceipt | None = None,
    mcp_events: tuple[EvidenceEvent, ...] = (),
    mcp_finalization: Mapping[str, Any] | None = None,
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
        "direct_query_digest": (
            canonical_sha256(direct_query) if direct_query is not None else None
        ),
        "direct_receipt": direct_receipt,
        "mcp_events": mcp_events,
        "mcp_finalization": (
            dict(mcp_finalization) if mcp_finalization is not None else None
        ),
        "transcript_digest": transcript_digest,
        "record_fingerprint": "0" * 64,
    }
    payload["record_fingerprint"] = canonical_sha256(
        payload,
        exclude_fields=("record_fingerprint",),
    )
    return ProviderRunRecord.model_validate(payload)


def _extract_json_object(text: str) -> Mapping[str, Any]:
    stripped = text.strip()
    candidates = [stripped]
    if "```" in stripped:
        for block in stripped.split("```"):
            candidate = block.strip()
            if candidate.casefold().startswith("json"):
                candidate = candidate[4:].strip()
            candidates.append(candidate)
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        candidates.append(stripped[start : end + 1])
    for candidate in candidates:
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, Mapping):
            return parsed
    raise V2ModelRuntimeError("model output did not contain one JSON object")


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


def parse_direct_submission(text: str, task: TaskBundle) -> DirectSubmissionV2:
    payload = _extract_json_object(text)
    schema = direct_submission_schema(task)
    try:
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(dict(payload))
    except (SchemaError, ValidationError) as exc:
        raise V2ModelRuntimeError(f"direct submission schema mismatch: {exc.message}") from exc
    assert_solver_visible(payload)
    return DirectSubmissionV2.model_validate(payload)


def _provider_request(task: TaskBundle, *, direct: bool) -> dict[str, Any]:
    envelope = build_solver_visible_envelope(
        task,
        surface=PublicSurface.PROVIDER_REQUEST,
    )
    payload = {
        "task": envelope.model_dump(mode="json"),
        "submission_schema": (
            direct_submission_schema(task) if direct else task.answer_schema
        ),
    }
    if direct:
        payload["query_result_contract"] = _direct_query_result_contract(task)
    assert_solver_visible(payload)
    return payload


def _direct_query_result_contract(task: TaskBundle) -> dict[str, Any]:
    """Describe supported CySQL output without exposing scorer-only material."""

    common = {
        "one_statement": True,
        "return_only_answer_evidence": True,
        "unsupported_cysql": [
            "CALL",
            "UNION",
            "UNWIND",
            "CASE",
            "map literals",
            "list comprehensions",
            "labels()",
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
    elif task.claim_kind == "count":
        claim = {
            "result_shape": "Return exactly one non-negative scalar AS count.",
        }
    else:
        claim = {
            "result_shape": (
                "Return an actual BloodHound path variable, for example "
                "RETURN p ORDER BY length(p) LIMIT 1. Return required supporting "
                "relationship variables separately; never construct JSON in Cypher."
            ),
            "recursive_limit_position": "after the final RETURN projection",
        }
    return {
        "version": DIRECT_RESULT_CONTRACT_VERSION,
        "claim_kind": task.claim_kind,
        "bounds": task.binding.bounds.model_dump(mode="json"),
        "common": common,
        "claim": claim,
    }


def direct_system_prompt(task: TaskBundle) -> str:
    request = _provider_request(task, direct=True)
    return (
        "You are being evaluated on a controlled synthetic BloodHound CE graph. "
        "Write exactly one bounded read-only Cypher query that answers the public "
        "question. Return only one JSON object matching submission_schema. The query "
        "result must follow query_result_contract; it does not need to match the "
        "task answer_schema because ORI projects returned graph evidence itself. "
        "Use RETURN p for path queries so BloodHound returns ordered nodes and edges. "
        "For set queries return only the answer nodes, not source or context nodes. "
        "Do not use CALL, UNION, UNWIND, CASE expressions, map literals, list "
        "comprehensions, labels(), or XOR. Put a recursive route's ORDER BY and LIMIT "
        "after its final RETURN projection. shortestPath requires one variable-length "
        "pattern. The assertion object may contain only fields declared by "
        "submission_schema. Do not use write clauses, unbounded traversal, or hidden "
        "assumptions.\n\n"
        + json.dumps(request, sort_keys=True)
    )


def mcp_system_prompt(task: TaskBundle) -> str:
    request = _provider_request(task, direct=False)
    return (
        "You are being evaluated on a controlled synthetic BloodHound CE graph. "
        "Use only the provided read-only BloodHound tools. Prefer high-level graph "
        "tools when they can prove the claim. If Cypher is necessary, use bounded "
        "queries; exact set/count work requires a companion total count and stable "
        "ORDER BY pagination. Never claim completeness after truncation or an "
        "incomplete page. Finish with only one JSON object matching answer_schema. "
        "Use stable object IDs when available and include ordered edge witnesses for "
        "routes. Do not include commentary or markdown.\n\n"
        + json.dumps(request, sort_keys=True)
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
        reasoning_correct=False,
        detail=detail,
    )


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


TextTransport = Callable[..., Awaitable[ModelResponse]]


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
    transport: TextTransport = call_provider_text,
) -> tuple[DirectV2Outcome | None, SampleResult, ProviderRunRecord]:
    """Call one model with a public direct contract, then execute via policy v3."""

    response = await transport(
        model=model,
        messages=[{"role": "user", "content": task.question}],
        system=direct_system_prompt(task),
        base_url=model_base_url,
        max_tokens=max_tokens,
        ollama_options=ollama_options,
    )
    if response.error:
        sample = _model_infrastructure_sample(task, oracle, response.error)
        return None, sample, _record(
            task=task,
            model=model,
            surface=V2RuntimeSurface.DIRECT.value,
            response=response,
        )
    try:
        submission = parse_direct_submission(response.raw_text, task)
    except V2ModelRuntimeError as exc:
        sample = _model_output_invalid_sample(task, oracle, str(exc))
        return None, sample, _record(
            task=task,
            model=model,
            surface=V2RuntimeSurface.DIRECT.value,
            response=response,
        )

    outcome, sample = await run_direct_task_v2(
        coordinator,
        query=submission.query,
        task=task,
        oracle=oracle,
        resolver=resolver,
        answer_payload=submission.assertion,
    )
    return outcome, sample, _record(
        task=task,
        model=model,
        surface=V2RuntimeSurface.DIRECT.value,
        response=response,
        direct_query=submission.query,
        direct_receipt=outcome.receipt,
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


def _result_cardinality(payload: Mapping[str, Any]) -> int | None:
    """Count only explicit result containers from pinned MCP response shapes."""

    top_node_count = _nonnegative_int(payload.get("node_count"))
    top_edge_count = _nonnegative_int(payload.get("edge_count"))
    if top_node_count is not None or top_edge_count is not None:
        return max(top_node_count or 0, top_edge_count or 0)

    current: Any = payload.get("data")
    for _depth in range(3):
        if isinstance(current, list):
            return len(current)
        if not isinstance(current, Mapping):
            return None
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
        if counts:
            return max(counts)
        nested = current.get("data")
        if nested is current:
            break
        current = nested
    return None


def _graph_search_cardinality(payload: Mapping[str, Any]) -> int | None:
    """Count the pinned graph-analysis search result map."""

    wrapper = payload.get("data")
    results = wrapper.get("data") if isinstance(wrapper, Mapping) else None
    if isinstance(results, (list, Mapping)):
        return len(results)
    return None


def _page_window(query: str) -> tuple[int, int] | None:
    normalized = " ".join(query.split())
    order = re.search(
        r"\bORDER\s+BY\b(?P<order>.*?)(?:\bSKIP\b|\bLIMIT\b)",
        normalized,
        flags=re.IGNORECASE,
    )
    limit = re.search(r"\bLIMIT\s+(?P<limit>\d+)\b", normalized, flags=re.IGNORECASE)
    skip = re.search(r"\bSKIP\s+(?P<skip>\d+)\b", normalized, flags=re.IGNORECASE)
    if order is None or limit is None:
        return None
    if "objectid" not in order.group("order").casefold():
        return None
    return (
        int(skip.group("skip")) if skip is not None else 0,
        int(limit.group("limit")),
    )


def _page_query_key(query: str) -> str:
    normalized = " ".join(query.split())
    normalized = re.sub(
        r"\bSKIP\s+\d+\b",
        "SKIP ?",
        normalized,
        flags=re.IGNORECASE,
    )
    return re.sub(
        r"\bLIMIT\s+\d+\b",
        "LIMIT ?",
        normalized,
        flags=re.IGNORECASE,
    ).casefold()


def _is_count_query(query: str) -> bool:
    return bool(
        re.search(
            r"\bRETURN\s+(?:DISTINCT\s+)?COUNT\s*\([^)]*\)"
            r"\s*(?:AS\s+`?[A-Za-z_][A-Za-z0-9_]*`?)?\s*;?\s*\Z",
            query,
            flags=re.IGNORECASE,
        )
    )


class MCPTranscriptProjector:
    """Convert live tool outcomes into public-contract-derived evidence events."""

    def __init__(self, task: TaskBundle, profile: CapabilityProfile) -> None:
        self.task = task
        self.profile = profile
        self.events: list[EvidenceEvent] = []
        self.total_count: int | None = None
        self.tool_calls = 0
        self.transcript_bytes = 0
        self.page_query_key: str | None = None
        self.page_counts: dict[int, int] = {}

    @property
    def finalization_ready(self) -> bool:
        return any(event.unlocks_finalization for event in self.events)

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
        operation = str(
            arguments.get("info_type")
            or arguments.get("operation")
            or "unknown"
        )
        payload = _tool_payload(result_text)
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
        policy_rejected = (
            "policy_violation" in lowered_error
            or error_type == "policy_rejected"
        )
        infrastructure_failure = any(
            marker in lowered_error
            for marker in (
                "auth_error",
                "authentication",
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
        ) or bool(re.search(r"\bhttp\s+5\d\d\b", lowered_error))
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

        query = str(arguments.get("query") or "")
        scalar_count = _scalar_count(payload) if tool_name == "cypher_query" else None
        is_count = bool(query and _is_count_query(query))
        if is_count and scalar_count is not None:
            self.total_count = scalar_count

        result_count = _result_cardinality(payload)
        if (
            result_count is None
            and tool_name == "graph_analysis"
            and operation == "search"
        ):
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
                    negative_proof = (
                        self.task.claim_kind == "absence" and scalar_count == 0
                    )
                else:
                    # A companion count is useful state but does not prove an
                    # entity set or route by itself.
                    result_count = None
            else:
                total_count = self.total_count
                if self.task.claim_kind in {"route", "decision"}:
                    complete = False
                else:
                    window = _page_window(query)
                    if (
                        window is not None
                        and result_count is not None
                        and window[1] == self.task.binding.bounds.page_size
                    ):
                        query_key = _page_query_key(query)
                        if self.page_query_key in {None, query_key}:
                            self.page_query_key = query_key
                            self.page_counts[window[0]] = result_count
                        else:
                            self.page_query_key = query_key
                            self.page_counts = {window[0]: result_count}
                        expected_offsets = tuple(
                            index * self.task.binding.bounds.page_size
                            for index in range(len(self.page_counts))
                        )
                        received_offsets = tuple(sorted(self.page_counts))
                        if received_offsets == expected_offsets:
                            result_count = sum(self.page_counts.values())
                        else:
                            result_count = None
                    complete = (
                        window is not None
                        and total_count is not None
                        and result_count == total_count
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
            output_bytes > self.task.binding.bounds.max_output_bytes
            or self.transcript_bytes > self.task.binding.bounds.max_transcript_bytes
            or self.tool_calls > self.task.binding.bounds.max_tool_calls
            or bool(payload.get("truncated"))
        )
        if truncated:
            complete = False
            negative_proof = False
        observation = ToolObservation(
            tool_name=tool_name,
            operation=operation,
            succeeded=succeeded,
            arguments_valid=arguments_valid,
            policy_rejected=policy_rejected,
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
) -> ModelResponse:
    return await transport(
        model=model,
        messages=[
            {"role": "user", "content": task.question},
            {"role": "assistant", "content": malformed_output},
            {"role": "user", "content": SCHEMA_ONLY_RETRY_INSTRUCTION},
        ],
        system=mcp_system_prompt(task),
        base_url=model_base_url,
        max_tokens=2048,
        ollama_options=ollama_options,
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
    ollama_options: dict[str, Any] | None = None,
    telemetry_adapter: str = OPENAI_COMPAT_TELEMETRY_AUTO,
    read_timeout_seconds: float = DEFAULT_MCP_OLLAMA_READ_TIMEOUT_SECONDS,
    transport: TextTransport = call_provider_text,
) -> tuple[MCPV2Outcome, ProviderRunRecord]:
    """Run one certified native MCP loop and finalize through the V2 reducer."""

    resolved_loop = validate_certified_mcp_loop(tool_loop)
    if task.binding.mcp_tool_loop != resolved_loop.value:
        raise V2ModelRuntimeError(
            "runtime MCP loop does not match the task binding fingerprint"
        )
    if task.binding.mcp_resource_mode != "off":
        raise V2ModelRuntimeError("certified v2 MCP tasks require resource_mode=off")

    projector = MCPTranscriptProjector(task, profile)
    bounded_steps = min(max_steps, task.binding.bounds.max_tool_calls)
    loop_kwargs = {
        "task": None,
        "public_question": task.question,
        "model_name": model,
        "base_url": model_base_url,
        "tools": bundle.tools,
        "max_steps": bounded_steps,
        "server_prompt_text": bundle.server_prompt_text,
        "server_prompt_name": bundle.server_prompt_name,
        "available_prompt_names": bundle.available_prompt_names,
        "prompt_discovery_status": bundle.prompt_discovery_status,
        "resource_mode": "off",
        "system_prompt_override": mcp_system_prompt(task),
        "tool_result_observer": projector.observe,
    }
    try:
        if resolved_loop is MCPToolLoop.NATIVE_OPENAI_COMPATIBLE:
            response, _trajectory, messages = await asyncio.wait_for(
                _run_openai_compat_mcp_loop(
                    **loop_kwargs,
                    extra_body={"options": ollama_options} if ollama_options else None,
                    telemetry_adapter=telemetry_adapter,
                    read_timeout_seconds=read_timeout_seconds,
                ),
                timeout=task.binding.bounds.timeout_seconds,
            )
            surface = V2RuntimeSurface.MCP_NATIVE_OPENAI_COMPATIBLE
        elif resolved_loop is MCPToolLoop.NATIVE_OLLAMA:
            response, _trajectory, messages = await asyncio.wait_for(
                _run_ollama_mcp_loop(
                    **loop_kwargs,
                    ollama_options=ollama_options,
                    ollama_read_timeout_seconds=read_timeout_seconds,
                ),
                timeout=task.binding.bounds.timeout_seconds,
            )
            surface = V2RuntimeSurface.MCP_NATIVE_OLLAMA
        else:
            raise V2ModelRuntimeError(
                "Inspect-backed model campaigns require an Inspect-bound task catalog"
            )
    except TimeoutError:
        projector.events.append(
            classify_evidence_event(
                task,
                profile,
                kind=EvidenceEventKind.INFRASTRUCTURE_FAILURE,
                reason="MCP task timeout",
            )
        )
        response = ModelResponse(
            raw_text="",
            cypher=None,
            parse_stage="none",
            tokens_input=0,
            tokens_output=0,
            elapsed_seconds=task.binding.bounds.timeout_seconds,
            model=model,
            error="MCP task timeout",
        )
        messages = []
        surface = (
            V2RuntimeSurface.MCP_NATIVE_OPENAI_COMPATIBLE
            if resolved_loop is MCPToolLoop.NATIVE_OPENAI_COMPATIBLE
            else V2RuntimeSurface.MCP_NATIVE_OLLAMA
        )
    except httpx.HTTPError as exc:
        projector.events.append(
            classify_evidence_event(
                task,
                profile,
                kind=EvidenceEventKind.INFRASTRUCTURE_FAILURE,
                reason=f"MCP transport failure: {type(exc).__name__}",
            )
        )
        response = ModelResponse(
            raw_text="",
            cypher=None,
            parse_stage="none",
            tokens_input=0,
            tokens_output=0,
            elapsed_seconds=0.0,
            model=model,
            error=str(exc),
        )
        messages = []
        surface = (
            V2RuntimeSurface.MCP_NATIVE_OPENAI_COMPATIBLE
            if resolved_loop is MCPToolLoop.NATIVE_OPENAI_COMPATIBLE
            else V2RuntimeSurface.MCP_NATIVE_OLLAMA
        )
    except Exception as exc:
        projector.events.append(
            classify_evidence_event(
                task,
                profile,
                kind=EvidenceEventKind.HARNESS_FAILURE,
                reason=f"MCP harness failure: {type(exc).__name__}",
            )
        )
        response = ModelResponse(
            raw_text="",
            cypher=None,
            parse_stage="none",
            tokens_input=0,
            tokens_output=0,
            elapsed_seconds=0.0,
            model=model,
            error=str(exc),
        )
        messages = []
        surface = (
            V2RuntimeSurface.MCP_NATIVE_OPENAI_COMPATIBLE
            if resolved_loop is MCPToolLoop.NATIVE_OPENAI_COMPATIBLE
            else V2RuntimeSurface.MCP_NATIVE_OLLAMA
        )

    try:
        final_answer = _extract_json_object(response.raw_text)
    except V2ModelRuntimeError:
        final_answer = None
    retry_response: ModelResponse | None = None
    retry_answer: Mapping[str, Any] | None = None
    if projector.finalization_ready and not _answer_schema_valid(
        task,
        final_answer,
        resolver=resolver,
    ):
        retry_response = await _schema_only_retry(
            task=task,
            model=model,
            malformed_output=response.raw_text,
            model_base_url=model_base_url,
            ollama_options=ollama_options,
            transport=transport,
        )
        if retry_response.error:
            projector.events.append(
                classify_evidence_event(
                    task,
                    profile,
                    kind=EvidenceEventKind.INFRASTRUCTURE_FAILURE,
                    reason="schema-only retry provider failure",
                )
            )
        else:
            try:
                retry_answer = _extract_json_object(retry_response.raw_text)
            except V2ModelRuntimeError:
                retry_answer = None

    transcript_payload = [
        getattr(message, "model_dump", lambda **_: str(message))(mode="json")
        for message in messages
    ]
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
        event.kind is EvidenceEventKind.INFRASTRUCTURE_FAILURE
        for event in projector.events
    ):
        source_event = next(
            (
                event
                for event in reversed(projector.events)
                if event.tool_name is not None and event.operation is not None
            ),
            None,
        )
        diagnostics = tuple(
            event for event in projector.events if not event.unlocks_finalization
        )
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

    outcome = run_mcp_task_v2(
        surface=surface,
        task=task,
        oracle=oracle,
        resolver=resolver,
        profile=profile,
        events=tuple(projector.events),
        final_answer=final_answer,
        retry_answer=retry_answer,
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
    record = _record(
        task=task,
        model=model,
        surface=surface.value,
        response=combined_response,
        mcp_events=tuple(projector.events),
        mcp_finalization=outcome.finalization.model_dump(mode="json"),
        transcript_digest=canonical_sha256(transcript_payload),
    )
    return outcome, record
