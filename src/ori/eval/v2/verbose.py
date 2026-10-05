"""Bounded, operator-only visibility for protocol-v2 model execution.

The normal runner progress stream is deliberately model-blind.  This module
adds the small amount of context useful to an operator while keeping the
private provider trace and sealed scorer state out of terminal output.
"""

from __future__ import annotations

import json
from typing import Any

from .model_runtime import ProviderRunRecord
from .schema import TaskBundle

MAX_QUESTION_CHARS = 1_200
MAX_ANSWER_SHAPE_CHARS = 2_000
MAX_SCHEMA_DEPTH = 8
MAX_SCHEMA_FIELDS = 24
MAX_LABEL_CHARS = 64


def _bounded_line(value: str, *, limit: int) -> str:
    """Keep one operator line bounded and avoid terminal-control surprises."""

    printable = "".join(character for character in value if character.isprintable())
    normalized = " ".join(printable.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "…"


def _schema_placeholder(schema: Any, *, depth: int = 0) -> Any:
    """Project public answer-schema structure using non-answer placeholders."""

    if depth >= MAX_SCHEMA_DEPTH or not isinstance(schema, dict):
        return "<value>"
    if "const" in schema or "enum" in schema:
        return "<fixed value>"
    if schema.get("oneOf") or schema.get("anyOf") or schema.get("allOf"):
        return "<schema variant>"

    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        schema_type = next((item for item in schema_type if item != "null"), "null")

    properties = schema.get("properties")
    if schema_type == "object" or isinstance(properties, dict):
        if not isinstance(properties, dict):
            return {}
        required = schema.get("required", ())
        names = list(dict.fromkeys((*required, *properties)))
        return {
            name: _schema_placeholder(properties[name], depth=depth + 1)
            for name in names[:MAX_SCHEMA_FIELDS]
            if name in properties
        }
    if schema_type == "array":
        # One item sketches the item shape; it does not encode expected count.
        return [_schema_placeholder(schema.get("items", {}), depth=depth + 1)]
    if schema_type == "integer":
        return "<integer>"
    if schema_type == "number":
        return "<number>"
    if schema_type == "boolean":
        return "<boolean>"
    if schema_type == "string":
        return "<string>"
    if schema_type == "null":
        return "<null>"
    return "<value>"


def answer_shape(task: TaskBundle) -> Any:
    """Build a shape-only example from the public answer schema, never scorer data."""

    return _schema_placeholder(task.answer_schema)


def answer_shape_line(task: TaskBundle) -> str:
    payload = json.dumps(
        answer_shape(task),
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(payload) > MAX_ANSWER_SHAPE_CHARS:
        payload = json.dumps("<bounded answer shape omitted>", separators=(",", ":"))
    return (
        "    Answer-shape placeholder (schema-derived; values and cardinality "
        f"are not expected answers): {payload}"
    )


def task_question_lines(task: TaskBundle, *, position: int, total: int) -> tuple[str, ...]:
    question = _bounded_line(task.question, limit=MAX_QUESTION_CHARS)
    return (
        f"    Question [{position}/{total}]: {question}",
        "    Model activity: task attempt started (response and reasoning hidden)",
    )


def tool_activity_lines(provider: ProviderRunRecord) -> tuple[str, ...]:
    """Project only mechanical MCP operation facts, never arguments/results."""

    if provider.surface == "direct":
        receipt = provider.direct_receipt
        if receipt is None:
            return ("    Model activity: direct query not submitted (query text hidden)",)
        failure_type = str(receipt.failure_type or "").casefold()
        failure_status = {
            "policy_rejected": "policy-rejected",
            "query_timeout": "query-timeout",
            "query_error": "query-error",
            "circuit_open": "circuit-open",
        }.get(failure_type, "failed" if failure_type else None)
        if receipt.query_executed:
            suffix = f" ({failure_status})" if failure_status else ""
            return (
                "    Model activity: bounded direct query executed"
                f"{suffix} (query text hidden)",
            )
        if failure_status == "policy-rejected":
            return (
                "    Model activity: direct query rejected before execution "
                "(query text hidden)",
            )
        suffix = f" ({failure_status})" if failure_status else ""
        return (
            "    Model activity: bounded direct query not executed"
            f"{suffix} (query text hidden)",
        )
    receipts = provider.mcp_tool_receipts
    if not receipts:
        return ("    Model/tool activity: no observable tool calls",)
    lines: list[str] = []
    for receipt in receipts:
        observation = receipt.observation
        if observation.succeeded:
            status = "success"
        elif observation.policy_rejected:
            status = "policy-rejected"
        elif observation.query_timeout:
            status = "query-timeout"
        elif observation.query_error:
            status = "query-error"
        elif observation.infrastructure_failure:
            status = "infrastructure-failure"
        else:
            status = "failed"
        details = [status]
        if observation.complete:
            details.append("complete")
        if observation.truncated:
            details.append("truncated")
        lines.append(
            f"    Tool {receipt.sequence}: {_safe_label(receipt.tool_name)}/"
            f"{_safe_label(receipt.operation)} "
            f"({', '.join(details)})"
        )
    return tuple(lines)


def _safe_label(value: Any) -> str:
    """Allowlist dynamic tool labels so model text cannot inject terminal controls."""

    raw = str(value)[: MAX_LABEL_CHARS * 4]
    sanitized = "".join(
        character
        if character.isascii() and (character.isalnum() or character in "_.-")
        else "_"
        for character in raw
    )
    return sanitized.strip("_")[:MAX_LABEL_CHARS] or "unknown"


def verbose_task_lines(
    *,
    task: TaskBundle,
    provider: ProviderRunRecord,
    position: int,
    total: int,
) -> tuple[str, ...]:
    """Return all bounded operator-visible lines for one terminal attempt."""

    return (
        *task_question_lines(task, position=position, total=total),
        *tool_activity_lines(provider),
        answer_shape_line(task),
    )
