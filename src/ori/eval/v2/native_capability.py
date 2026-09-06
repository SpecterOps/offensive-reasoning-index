"""Explicit native execution contracts, not runtime or campaign qualification.

Unlike the historical CE profile, a native profile binds its actual backend.
Fingerprints bind expectations; they do not prove privileges, graph isolation,
source execution, or that any task is supported by the native proof surface.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

from pydantic import model_validator

from .fingerprint import canonical_sha256
from .native_mcp_profiles import get_native_implementation, validate_native_discovery
from .schema import Fingerprint, GitCommit, StrictModel, Track


def native_implementation_fingerprint() -> str:
    """Conservatively bind packaged ORI code, including transitive helpers.

    This works in an installed package without a Git checkout. External runtime
    dependencies have their own explicit lock binding. Native certification is
    intentionally stale after any packaged Python source change; an incomplete
    hand-maintained import list must not leave a proof helper unbound.
    """
    root = Path(__file__).parent.parent.parent
    sources = {path.relative_to(root).as_posix(): path for path in root.rglob("*.py")}
    return canonical_sha256({
        "version": "ori-native-implementation-v1",
        "sources": {name: hashlib.sha256(path.read_bytes()).hexdigest()
                    for name, path in sorted(sources.items())},
    })


class NativeCapabilityProfile(StrictModel):
    """Private, immutable configured capability; no implicit certification."""

    contract_version: Literal["ori-native-capability-v1"] = "ori-native-capability-v1"
    track: Literal[Track.MCP] = Track.MCP
    implementation_id: Literal["mwnickerson", "mordavid", "armadin"]
    source_revision: GitCommit
    backend: Literal["bhce", "neo4j"]
    runtime_fingerprint: Fingerprint
    dependency_lock_fingerprint: Fingerprint
    backend_binding_fingerprint: Fingerprint
    discovery_fingerprint: Fingerprint
    implementation_fingerprint: Fingerprint
    profile_id: str
    profile_fingerprint: Fingerprint

    @model_validator(mode="after")
    def bindings_are_consistent(self) -> NativeCapabilityProfile:
        source = get_native_implementation(self.implementation_id)
        if self.source_revision != source.revision or self.backend != source.backend:
            raise ValueError("native source/backend binding mismatch")
        expected = canonical_sha256(
            self, exclude_fields=("profile_id", "profile_fingerprint"),
        )
        if self.profile_fingerprint != expected:
            raise ValueError("native capability fingerprint mismatch")
        if self.profile_id != f"ori-native-{self.implementation_id}-v1-{expected}":
            raise ValueError("native capability identifier mismatch")
        return self


def validate_native_capability_profile(profile: NativeCapabilityProfile) -> NativeCapabilityProfile:
    """Reject historical substitutes, tampering and stale interpretation code."""
    if type(profile) is not NativeCapabilityProfile:
        raise TypeError("native execution requires a NativeCapabilityProfile")
    # model_copy(update=...) is deliberately not an admission bypass.
    validated = NativeCapabilityProfile.model_validate(profile.model_dump(mode="python"))
    if validated.implementation_fingerprint != native_implementation_fingerprint():
        raise ValueError("native implementation fingerprint is stale")
    return validated


def build_native_capability_profile(
    implementation_id: str,
    *,
    runtime_fingerprint: str,
    dependency_lock_fingerprint: str,
    backend_binding_fingerprint: str,
    tools: list[dict],
    prompts: list[dict],
    resources: list[dict],
    resource_templates: list[dict],
    surface_availability: dict[str, bool],
) -> NativeCapabilityProfile:
    """Bind exact discovered descriptors without altering or certifying them."""
    source = get_native_implementation(implementation_id)
    discovery = validate_native_discovery(
        implementation_id, tools=tools, prompts=prompts, resources=resources,
        resource_templates=resource_templates,
    )
    if set(surface_availability) != {"tools", "prompts", "resources"} or any(
        type(value) is not bool for value in surface_availability.values()
    ):
        raise ValueError("invalid native surface availability")
    if any(items and not surface_availability[key] for key, items in (
        ("tools", tools), ("prompts", prompts), ("resources", resources + resource_templates),
    )):
        raise ValueError("unavailable native surface contains descriptors")
    discovery = canonical_sha256({
        "native_descriptors": discovery, "surface_availability": surface_availability,
    })
    payload = dict(
        contract_version="ori-native-capability-v1", track=Track.MCP,
        implementation_id=implementation_id, source_revision=source.revision,
        backend=source.backend, runtime_fingerprint=runtime_fingerprint,
        dependency_lock_fingerprint=dependency_lock_fingerprint,
        backend_binding_fingerprint=backend_binding_fingerprint,
        discovery_fingerprint=discovery,
        implementation_fingerprint=native_implementation_fingerprint(),
    )
    fingerprint = canonical_sha256(payload)
    return NativeCapabilityProfile(
        **payload, profile_fingerprint=fingerprint,
        profile_id=f"ori-native-{implementation_id}-v1-{fingerprint}",
    )
