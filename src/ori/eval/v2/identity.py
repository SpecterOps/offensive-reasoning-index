"""Typed identity resolution for the v2 evaluation protocol.

The resolver deliberately treats display names as aliases, never as identity.
Every successful lookup returns the stable ``object_id`` from an ``EntityRef``.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from .schema import EntityRef


class IdentityResolutionError(ValueError):
    """Base class for identity resolution failures."""


class UnknownIdentityError(IdentityResolutionError):
    """Raised when an answer token is not present in the sealed identity index."""


class AmbiguousIdentityError(IdentityResolutionError):
    """Raised when an answer token refers to more than one graph object."""


def normalize_identity_token(value: str) -> str:
    """Return the comparison form used for object IDs and aliases."""

    return " ".join(str(value).strip().split()).casefold()


def _identity_tokens(entity: EntityRef) -> set[str]:
    tokens = {entity.object_id}
    if entity.canonical_name:
        tokens.add(entity.canonical_name)
        if entity.domain:
            # BloodHound answers commonly use either UPN or DOMAIN\\name
            # notation. Both remain aliases for the same typed graph object.
            local_name = entity.canonical_name
            domain_suffix = f"@{entity.domain}"
            if local_name.casefold().endswith(domain_suffix.casefold()):
                local_name = local_name[: -len(domain_suffix)]
            if "@" not in local_name and "\\" not in local_name:
                tokens.add(f"{local_name}@{entity.domain}")
            short_domain = entity.domain.split(".", 1)[0]
            if "\\" not in local_name:
                tokens.add(f"{short_domain}\\{local_name}")
    tokens.update(entity.aliases)

    return {normalize_identity_token(token) for token in tokens if token}


class IdentityResolver:
    """Resolve graph answer tokens to stable typed object IDs.

    Alias collisions are retained in the index. They are rejected at lookup
    time instead of being silently resolved by insertion order.
    """

    def __init__(self, entities: Iterable[EntityRef]) -> None:
        self._entities: dict[str, EntityRef] = {}
        self._aliases: dict[str, set[str]] = defaultdict(set)

        for entity in entities:
            canonical_id = entity.object_id
            if canonical_id in self._entities and self._entities[canonical_id] != entity:
                raise IdentityResolutionError(
                    f"Duplicate object_id has conflicting EntityRef values: {canonical_id}"
                )
            self._entities[canonical_id] = entity
            for token in _identity_tokens(entity):
                self._aliases[token].add(canonical_id)

    @property
    def object_ids(self) -> frozenset[str]:
        return frozenset(self._entities)

    def resolve(self, value: str, *, object_type: str | None = None) -> str:
        """Resolve one token, optionally requiring an entity type."""

        token = normalize_identity_token(value)
        candidates = set(self._aliases.get(token, ()))
        if object_type is not None:
            expected = normalize_identity_token(object_type)
            candidates = {
                object_id
                for object_id in candidates
                if normalize_identity_token(
                    self._entities[object_id].object_type
                )
                == expected
            }

        if not candidates:
            qualifier = f" with type {object_type!r}" if object_type else ""
            raise UnknownIdentityError(f"Unknown graph identity {value!r}{qualifier}")
        if len(candidates) > 1:
            raise AmbiguousIdentityError(
                f"Ambiguous graph identity {value!r}: {sorted(candidates)}"
            )
        return next(iter(candidates))

    def resolve_many(self, values: Iterable[str]) -> tuple[str, ...]:
        """Resolve answer tokens while preserving their declared order."""

        return tuple(self.resolve(value) for value in values)

    def entity_for(self, value: str) -> EntityRef:
        """Return the typed entity associated with an ID or alias."""

        return self._entities[self.resolve(value)]
