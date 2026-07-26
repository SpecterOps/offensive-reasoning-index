"""Canonical serialization and content fingerprints for ORI protocol v2."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel

CERTIFIER_VERSION = "ori-live-certifier-v3"


def _json_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", round_trip=True)
    if isinstance(value, Enum):
        return value.value
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("canonical JSON does not allow non-finite floats")
        return value
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("canonical JSON object keys must be strings")
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_value(item) for item in value]
    raise TypeError(f"unsupported canonical JSON value: {type(value).__name__}")


def _exclude_path(document: Any, dotted_path: str) -> None:
    if not dotted_path or any(not token for token in dotted_path.split(".")):
        raise ValueError(f"invalid exclusion path: {dotted_path!r}")
    current = document
    tokens = dotted_path.split(".")
    for token in tokens[:-1]:
        if not isinstance(current, dict) or token not in current:
            raise ValueError(f"exclusion path does not exist: {dotted_path}")
        current = current[token]
    leaf = tokens[-1]
    if not isinstance(current, dict) or leaf not in current:
        raise ValueError(f"exclusion path does not exist: {dotted_path}")
    del current[leaf]


def canonical_json_bytes(
    value: Any,
    *,
    exclude_fields: Iterable[str] = (),
) -> bytes:
    """Return UTF-8 canonical JSON, optionally excluding exact dotted field paths."""

    document = deepcopy(_json_value(value))
    exclusions = tuple(exclude_fields)
    if len(exclusions) != len(set(exclusions)):
        raise ValueError("exclusion paths must be unique")
    for dotted_path in sorted(exclusions):
        _exclude_path(document, dotted_path)
    return json.dumps(
        document,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_sha256(
    value: Any,
    *,
    exclude_fields: Iterable[str] = (),
) -> str:
    """Return a lowercase SHA-256 hex digest over canonical JSON."""

    return hashlib.sha256(
        canonical_json_bytes(value, exclude_fields=exclude_fields)
    ).hexdigest()


@lru_cache(maxsize=1)
def certifier_fingerprint() -> str:
    """Bind candidate promotion to every semantic certification boundary."""

    directory = Path(__file__).parent
    source_names = (
        "certification.py",
        "comparator.py",
        "direct_adapter.py",
        "evidence.py",
        "fingerprint.py",
        "fixtures.py",
        "graph.py",
        "identity.py",
        "live_projection.py",
        "mcp.py",
        "mcp_adapter.py",
        "model_runtime.py",
        "schema.py",
    )
    source_digests = {
        name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
        for name in source_names
    }
    return canonical_sha256(
        {
            "certifier_version": CERTIFIER_VERSION,
            "source_digests": source_digests,
        }
    )
