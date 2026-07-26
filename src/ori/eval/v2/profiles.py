"""Pinned execution profiles used by v2 task certification."""

from __future__ import annotations

from .compiler import DIRECT_CAPABILITY_PROFILE
from .fingerprint import canonical_sha256
from .mcp import build_mcp_capability_profile, validate_mcp_capability_profile
from .schema import (
    CONTAINMENT_BASE_COMMIT,
    DIRECT_QUERY_POLICY_VERSION,
    CapabilityProfile,
    Track,
)

DIRECT_BLOODHOUND_CE_VERSION = "9.1.0"


def build_direct_capability_profile() -> CapabilityProfile:
    """Return the immutable profile for the authoritative containment adapter."""

    payload = {
        "profile_id": DIRECT_CAPABILITY_PROFILE,
        "track": Track.DIRECT,
        "bloodhound_ce_version": DIRECT_BLOODHOUND_CE_VERSION,
        "direct_query_policy_version": DIRECT_QUERY_POLICY_VERSION,
        "containment_base_commit": CONTAINMENT_BASE_COMMIT,
        "profile_fingerprint": "0" * 64,
    }
    profile = CapabilityProfile.model_validate(payload)
    return profile.model_copy(
        update={
            "profile_fingerprint": canonical_sha256(
                profile,
                exclude_fields=("profile_fingerprint",),
            )
        }
    )


PINNED_DIRECT_CAPABILITY_PROFILE = build_direct_capability_profile()
DIRECT_CAPABILITY_PROFILE_FINGERPRINT = (
    PINNED_DIRECT_CAPABILITY_PROFILE.profile_fingerprint
)


def validate_direct_capability_profile(
    profile: CapabilityProfile,
) -> CapabilityProfile:
    """Reject any drift from the pinned containment execution contract."""

    expected = canonical_sha256(
        profile,
        exclude_fields=("profile_fingerprint",),
    )
    if profile.profile_fingerprint != expected:
        raise ValueError("direct capability profile fingerprint mismatch")
    if profile != PINNED_DIRECT_CAPABILITY_PROFILE:
        raise ValueError("direct capability profile content does not match the pin")
    return profile


def capability_profile_for_track(track: Track) -> CapabilityProfile:
    if track is Track.DIRECT:
        return PINNED_DIRECT_CAPABILITY_PROFILE
    return build_mcp_capability_profile()


def validate_capability_profile(profile: CapabilityProfile) -> CapabilityProfile:
    if profile.track is Track.DIRECT:
        return validate_direct_capability_profile(profile)
    return validate_mcp_capability_profile(profile)
