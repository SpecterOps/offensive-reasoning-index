"""Single redaction boundary for every solver-visible protocol-v2 surface."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from pydantic import model_validator

from .fingerprint import canonical_sha256
from .schema import (
    PROTOCOL_VERSION,
    ExecutionBounds,
    Fingerprint,
    RelationshipSemantics,
    StrictModel,
    TaskBundle,
    Track,
)


class PublicSurface(StrEnum):
    PROVIDER_REQUEST = "provider_request"
    INSPECT_METADATA = "inspect_metadata"
    TRANSCRIPT = "transcript"
    CSV = "csv"
    TELEMETRY = "telemetry"
    EXPORT = "public_export"


FORBIDDEN_SOLVER_KEYS = frozenset(
    {
        "correct",
        "expected_answer",
        "expected_count",
        "expected_decision",
        "expected_entities",
        "forbidden_edges",
        "forbidden_entity_ids",
        "graph_edge_registry",
        "negative_witnesses",
        "oracle",
        "oracle_bundle",
        "oracle_fingerprint",
        "oracle_id",
        "reference_cypher",
        "reference_nodes",
        "reference_results",
        "ref_result",
        "required_context",
        "required_mechanisms",
        "required_properties",
        "route_variants",
        "valid_node_names",
    }
)


class SolverVisibleEnvelope(StrictModel):
    """The only task projection permitted on a model-visible runtime surface."""

    protocol_version: str = PROTOCOL_VERSION
    surface: PublicSurface
    task_id: str
    task_fingerprint: Fingerprint
    track: Track
    semantics: RelationshipSemantics
    execution_bounds: ExecutionBounds
    question: str
    answer_schema: dict[str, Any]
    generic_instructions: tuple[str, ...]
    envelope_fingerprint: Fingerprint

    @model_validator(mode="after")
    def envelope_is_redacted(self) -> SolverVisibleEnvelope:
        payload = self.model_dump(
            mode="json",
            exclude={"envelope_fingerprint"},
        )
        assert_solver_visible(payload)
        if self.envelope_fingerprint != canonical_sha256(payload):
            raise ValueError("solver-visible envelope fingerprint mismatch")
        return self


def assert_solver_visible(
    value: Any,
    *,
    sentinels: tuple[str, ...] = (),
    path: str = "$",
) -> None:
    """Recursively reject oracle keys and caller-provided leakage sentinels."""

    if isinstance(value, Mapping):
        for key, nested in value.items():
            if str(key).casefold() in FORBIDDEN_SOLVER_KEYS:
                raise ValueError(f"scorer-only field reached public surface at {path}.{key}")
            assert_solver_visible(
                nested,
                sentinels=sentinels,
                path=f"{path}.{key}",
            )
        return
    if isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            assert_solver_visible(
                nested,
                sentinels=sentinels,
                path=f"{path}[{index}]",
            )
        return
    if isinstance(value, str):
        leaked = next((sentinel for sentinel in sentinels if sentinel in value), None)
        if leaked is not None:
            raise ValueError(f"oracle sentinel reached public surface at {path}")


def build_solver_visible_envelope(
    task: TaskBundle,
    *,
    surface: PublicSurface,
) -> SolverVisibleEnvelope:
    """Project a public task identically for provider/log/report adapters."""

    if type(task) is not TaskBundle:
        raise TypeError("solver-visible surfaces accept only a public TaskBundle")
    payload = {
        "protocol_version": PROTOCOL_VERSION,
        "surface": surface,
        "task_id": task.task_id,
        "task_fingerprint": task.task_fingerprint,
        "track": task.binding.track,
        "semantics": task.binding.semantics,
        "execution_bounds": task.binding.bounds,
        "question": task.question,
        "answer_schema": task.answer_schema,
        "generic_instructions": task.generic_instructions,
    }
    return SolverVisibleEnvelope(
        **payload,
        envelope_fingerprint=canonical_sha256(payload),
    )


def build_all_solver_visible_envelopes(
    task: TaskBundle,
) -> tuple[SolverVisibleEnvelope, ...]:
    """Build provider, Inspect, transcript, CSV, telemetry, and export views."""

    return tuple(
        build_solver_visible_envelope(task, surface=surface)
        for surface in PublicSurface
    )
