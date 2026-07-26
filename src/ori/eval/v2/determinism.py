"""Seed-independent semantic-shape evidence for protocol-v2 corpora."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .compiler import CompiledCorpus, CompiledTask
from .fingerprint import canonical_sha256


def _replacement_map(task: CompiledTask) -> dict[str, str]:
    replacements: dict[str, str] = {}
    domains: set[str] = set()
    for entity in task.oracle.resolved_roles:
        marker = f"<role:{entity.role}>"
        replacements[entity.object_id] = marker
        if entity.canonical_name:
            replacements[entity.canonical_name] = marker
        for alias in entity.aliases:
            replacements[alias] = marker
        if entity.domain:
            domains.add(entity.domain)
    for domain in domains:
        replacements[domain] = "<domain>"
    return dict(sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True))


def _normalize(value: Any, replacements: Mapping[str, str]) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _normalize(nested, replacements)
            for key, nested in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return tuple(_normalize(item, replacements) for item in value)
    if isinstance(value, str):
        normalized = value
        for source, marker in replacements.items():
            normalized = normalized.replace(source, marker)
        return normalized
    return value


def task_contract_shape(task: CompiledTask) -> dict[str, Any]:
    """Return seed-independent authoring semantics for one compiled task."""

    replacements = _replacement_map(task)
    binding = task.public.binding.model_dump(mode="json")
    # These two limits are resolved from the selected seed's certified
    # cardinality.  The bound strategy remains compiler-fingerprinted while the
    # concrete values stay bound to the seed-specific TaskBundle.
    binding["bounds"]["max_result_cardinality"] = "<seed-resolved-cardinality>"
    binding["bounds"]["page_size"] = "<seed-resolved-page-size>"
    binding["bounds"]["max_pages"] = "<seed-resolved-page-count>"
    return {
        "task_id": task.public.task_id,
        "revision": task.public.revision,
        "product": task.public.product,
        "claim": _normalize(task.oracle.claim.model_dump(mode="json"), replacements),
        "answer_policy": task.public.answer_policy.model_dump(mode="json"),
        "binding": binding,
        "input_roles": tuple(
            (entity.role, entity.object_type)
            for entity in task.public.input_entities
        ),
        "question": _normalize(task.public.question, replacements),
        "answer_schema": task.public.answer_schema,
        "generic_instructions": task.public.generic_instructions,
        "family": task.migration.family,
        "tier": task.migration.tier,
        "path_concentration_key": task.migration.path_concentration_key,
    }


def corpus_contract_shape_fingerprint(corpus: CompiledCorpus) -> str:
    """Fingerprint task semantics while excluding seed-resolved graph identity."""

    return canonical_sha256(
        tuple(
            task_contract_shape(task)
            for task in sorted(corpus.tasks, key=lambda item: item.public.task_id)
        )
    )
