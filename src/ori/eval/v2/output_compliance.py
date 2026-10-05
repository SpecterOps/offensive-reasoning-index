"""Shared structured-output compliance checks for live and offline V2 paths."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from jsonschema import Draft202012Validator


class OutputComplianceError(ValueError):
    """Raised when structured output is not valid finite JSON data."""


def require_finite_json(value: Any) -> None:
    """Reject JSON extensions that cannot be represented as finite JSON numbers."""

    if isinstance(value, float) and not math.isfinite(value):
        raise OutputComplianceError("structured output contains a non-finite number")
    if isinstance(value, Mapping):
        for item in value.values():
            require_finite_json(item)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            require_finite_json(item)


def is_schema_compliant_json(
    value: Mapping[str, Any] | None,
    schema: Mapping[str, Any],
) -> bool | None:
    """Apply the shared finite-JSON and JSON-Schema output boundary."""

    if value is None:
        return None
    try:
        require_finite_json(value)
    except OutputComplianceError:
        return False
    return Draft202012Validator(dict(schema)).is_valid(dict(value))
