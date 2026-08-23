from __future__ import annotations

import pytest

from ori.eval.v2.identity import (
    AmbiguousIdentityError,
    IdentityResolver,
    UnknownIdentityError,
)
from ori.eval.v2.schema import EntityRef


def _entity(
    object_id: str,
    canonical_name: str,
    *,
    domain: str = "EXAMPLE.LOCAL",
    object_type: str = "User",
    aliases: tuple[str, ...] = (),
) -> EntityRef:
    return EntityRef(
        object_id=object_id,
        object_type=object_type,
        domain=domain,
        role=f"role-{object_id}",
        canonical_name=canonical_name,
        aliases=aliases,
    )


def test_resolves_object_id_sid_name_and_domain_aliases_case_insensitively() -> None:
    alice = _entity(
        "S-1-5-21-1000",
        "ALICE",
        aliases=("alice@example.local", "EXAMPLE\\alice"),
    )
    resolver = IdentityResolver((alice,))

    assert resolver.resolve("s-1-5-21-1000") == alice.object_id
    assert resolver.resolve(" alice ") == alice.object_id
    assert resolver.resolve("ALICE@EXAMPLE.LOCAL") == alice.object_id
    assert resolver.resolve("example\\ALICE") == alice.object_id
    assert resolver.entity_for("Alice") is alice


def test_upn_canonical_name_synthesizes_netbios_qualified_alias() -> None:
    alice = _entity(
        "S-1-5-21-1000",
        "ALICE@EXAMPLE.LOCAL",
        aliases=("ALICE",),
    )
    resolver = IdentityResolver((alice,))

    assert resolver.resolve("EXAMPLE\\ALICE") == alice.object_id
    assert resolver.resolve("alice@example.local") == alice.object_id


def test_plain_alias_collision_is_rejected_but_domain_qualified_names_resolve() -> None:
    east = _entity("EAST-500", "ADMIN", domain="EAST.LOCAL")
    west = _entity("WEST-500", "ADMIN", domain="WEST.LOCAL")
    resolver = IdentityResolver((east, west))

    with pytest.raises(AmbiguousIdentityError, match="Ambiguous graph identity"):
        resolver.resolve("admin")

    assert resolver.resolve("admin@east.local") == east.object_id
    assert resolver.resolve("WEST\\admin") == west.object_id


def test_type_filter_disambiguates_only_matching_entity_type() -> None:
    user = _entity("USER-1", "SHARED", object_type="User")
    group = _entity("GROUP-1", "SHARED", object_type="Group")
    resolver = IdentityResolver((user, group))

    assert resolver.resolve("shared", object_type="group") == group.object_id
    assert resolver.resolve("shared", object_type="USER") == user.object_id


def test_unknown_identity_is_rejected() -> None:
    resolver = IdentityResolver((_entity("USER-1", "ALICE"),))

    with pytest.raises(UnknownIdentityError, match="Unknown graph identity"):
        resolver.resolve("BOB")
