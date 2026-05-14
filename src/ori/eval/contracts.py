"""Answer contracts for scorer/task alignment."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .tasks import Task


@dataclass(frozen=True)
class AnswerContract:
    task_id: str
    required_nodes: tuple[str, ...] = ()
    optional_nodes: tuple[str, ...] = ()
    forbidden_nodes: tuple[str, ...] = ()
    required_edges: tuple[dict[str, Any], ...] = ()
    grade_mode: str | None = None
    question: str = ""
    notes: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "required_nodes": list(self.required_nodes),
            "optional_nodes": list(self.optional_nodes),
            "forbidden_nodes": list(self.forbidden_nodes),
            "required_edges": list(self.required_edges),
            "grade_mode": self.grade_mode,
            "question": self.question,
            "notes": self.notes,
            "metadata": dict(self.metadata),
        }


def _node_tuple(values: Any) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple, set)):
        return ()
    return tuple(str(value).strip() for value in values if str(value).strip())


def task_contract_for(task: Task) -> AnswerContract | None:
    """Build a contract from task metadata and known Phase 4 templates."""
    metadata = dict(task.metadata or {})
    raw_contract = metadata.get("answer_contract")
    if isinstance(raw_contract, dict):
        return AnswerContract(
            task_id=task.id,
            required_nodes=_node_tuple(raw_contract.get("required_nodes")),
            optional_nodes=_node_tuple(raw_contract.get("optional_nodes")),
            forbidden_nodes=_node_tuple(raw_contract.get("forbidden_nodes")),
            required_edges=tuple(
                edge for edge in raw_contract.get("required_edges", []) if isinstance(edge, dict)
            ),
            grade_mode=str(raw_contract.get("grade_mode") or task.grade_mode),
            question=task.question,
            notes=str(raw_contract.get("notes") or "metadata contract"),
            metadata={"source": "metadata"},
        )

    critical_nodes = _node_tuple(metadata.get("critical_nodes"))
    if (
        task.template_id
        in {
            "t4_adcs_esc1",
            "t5_adcs_to_delegation_composite",
            "t3_unconstrained_delegation",
        }
        and critical_nodes
    ):
        return AnswerContract(
            task_id=task.id,
            required_nodes=critical_nodes,
            grade_mode=task.grade_mode,
            question=task.question,
            notes="Phase 4 contract generated from manifest critical_nodes.",
            metadata={"source": "phase4_critical_nodes", "template_id": task.template_id},
        )
    return None


def contract_nodes(contract: AnswerContract | None) -> set[str]:
    if contract is None:
        return set()
    return {
        *contract.required_nodes,
        *contract.optional_nodes,
        *contract.forbidden_nodes,
        *(str(edge.get("source")) for edge in contract.required_edges if edge.get("source")),
        *(str(edge.get("target")) for edge in contract.required_edges if edge.get("target")),
    }
