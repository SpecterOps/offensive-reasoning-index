"""Offline revalidation of native qualification artifacts, not campaign admission."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from .certification import LiveCertificationCatalog, live_certify_native_corpus
from .fingerprint import canonical_json_bytes, canonical_sha256
from .graph import GraphSnapshot
from .native_feasibility import NativeDevelopmentTrack, native_qualification_artifacts
from .release_selection import PairedSelectedRelease
from .schema import CertificationState, TaskCertification, Track


class NativeQualificationArtifactError(ValueError):
    """Public-safe artifact rejection without private payloads or paths."""


@dataclass(frozen=True)
class NativeQualifiedArtifacts:
    """Replayed private artifacts; current runtime readiness remains mandatory."""

    prepared: NativeDevelopmentTrack = field(repr=False)
    catalog: LiveCertificationCatalog = field(repr=False)
    qualification_fingerprint: str
    work_fingerprint: str
    paired_release: PairedSelectedRelease | None = None

    def __post_init__(self):
        if self.paired_release is not None:
            paired = PairedSelectedRelease.model_validate_json(
                self.paired_release.model_dump_json(),
            )
            selected = self.prepared.selection
            original_selection = getattr(
                selected, "original_mcp_selection_fingerprint", selected.selection_fingerprint
            )
            if paired.mcp_selection_fingerprint != original_selection or any(
                getattr(paired, key) != getattr(selected, key)
                for key in (
                    "product",
                    "seed",
                    "source_manifest_fingerprint",
                    "source_archive_sha256",
                    "graph_fingerprint",
                    "compiler_fingerprint",
                )
            ):
                raise NativeQualificationArtifactError("NATIVE_PAIRED_RELEASE_MISMATCH")
            if hasattr(selected, "spec_fingerprint") and paired.spec_fingerprint != (
                selected.spec_fingerprint
            ):
                raise NativeQualificationArtifactError("NATIVE_PAIRED_RELEASE_MISMATCH")
            if hasattr(selected, "paired_release_fingerprint") and paired.release_fingerprint != (
                selected.paired_release_fingerprint
            ):
                raise NativeQualificationArtifactError("NATIVE_PAIRED_RELEASE_MISMATCH")
        # Catalog order is lexical; campaign order is the original seeded roster.
        # Never derive the latter from candidate catalog serialization.
        ids = self.prepared.selection.selected_task_ids
        entries = self.catalog.candidate_catalog.entries
        certifications = self.catalog.certifications
        if (
            self.prepared.task_ids != ids
            or len(ids) != len(set(ids))
            or len(entries) != len(ids)
            or {e.task_id for e in entries} != set(ids)
            or len(certifications) != len(ids)
            or {c.task_id for c in certifications} != set(ids)
        ):
            raise NativeQualificationArtifactError("NATIVE_QUALIFICATION_ARTIFACT_INVALID")
        by_id = {c.task_id: c for c in certifications}
        entries_by_id = {e.task_id: e for e in entries}
        for task, oracle in zip(self.pair.public.tasks, self.pair.private.oracles, strict=True):
            cert, entry = by_id[task.task_id], entries_by_id[task.task_id]
            if (
                oracle.task_id != task.task_id
                or cert.state is not CertificationState.CANDIDATE
                or cert.failures
                or cert.task_fingerprint != task.task_fingerprint
                or cert.oracle_fingerprint != oracle.oracle_fingerprint
                or cert.capability_profile_fingerprint != self.profile.profile_fingerprint
                or entry.task_fingerprint != task.task_fingerprint
                or entry.oracle_fingerprint != oracle.oracle_fingerprint
                or entry.certification_fingerprint != cert.certification_fingerprint
            ):
                raise NativeQualificationArtifactError("NATIVE_QUALIFICATION_ARTIFACT_INVALID")

    @property
    def track(self) -> Track:
        return Track.MCP

    @property
    def pair(self):
        return self.prepared.pair

    @property
    def profile(self):
        return self.prepared.profile

    @property
    def release(self):
        return self.catalog.candidate_catalog

    @property
    def live(self):
        return self.catalog

    @property
    def task_ids(self) -> tuple[str, ...]:
        return self.prepared.selection.selected_task_ids

    @property
    def selection_fingerprint(self) -> str:
        return self.prepared.selection_fingerprint

    @property
    def paired_release_fingerprint(self) -> str | None:
        return self.paired_release.release_fingerprint if self.paired_release is not None else None

    @property
    def certifications(self) -> Mapping[str, TaskCertification]:
        return MappingProxyType({c.task_id: c for c in self.catalog.certifications})


def native_launcher_provenance(
    qualified: NativeQualifiedArtifacts,
    observations: dict,
) -> dict[str, str]:
    """Project validated native bindings into the existing private launcher mapping.

    Observations must come from the owning native session. Revalidation here checks
    their runtime/discovery content, not their freshness or graph/lifecycle gates.
    This mapping contains no raw runtime paths, discovery bodies or credentials and
    is not a public report or an execution-admission receipt.
    """
    from .native_capability import validate_native_capability_profile
    from .native_mcp_runtime import validate_native_session_observations

    try:
        if type(qualified) is not NativeQualifiedArtifacts:
            raise ValueError("qualified native artifacts required")
        qualified.__post_init__()
        profile = validate_native_capability_profile(qualified.profile)
        # Take one JSON snapshot so hashing and validation consume identical data.
        observations = json.loads(canonical_json_bytes(observations))
        validate_native_session_observations(
            observations,
            profile=profile,
            backend_binding_fingerprint=profile.backend_binding_fingerprint,
        )
        return {
            "mode": "native",
            "implementation_id": profile.implementation_id,
            "backend": profile.backend,
            "source_revision": profile.source_revision,
            "runtime_fingerprint": profile.runtime_fingerprint,
            "dependency_lock_fingerprint": profile.dependency_lock_fingerprint,
            "capability_profile_fingerprint": profile.profile_fingerprint,
            "backend_binding_fingerprint": profile.backend_binding_fingerprint,
            "qualification_fingerprint": qualified.qualification_fingerprint,
            "qualification_work_fingerprint": qualified.work_fingerprint,
            # Interval graph receipts and cleanup observations are retained by
            # their lifecycle owner, not mixed into stable resume identity.
            "session_observation_fingerprint": canonical_sha256(
                {key: observations[key] for key in ("runtime", "native_discovery")}
            ),
        }
    except (ValueError, TypeError, KeyError, AttributeError):
        raise NativeQualificationArtifactError("NATIVE_LAUNCHER_PROVENANCE_INVALID") from None


def _read_object(path: Path) -> dict:
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError("nonfinite JSON constant")

    with path.open(encoding="utf-8") as stream:
        value = json.load(stream, object_pairs_hook=unique_object, parse_constant=reject_constant)
    if not isinstance(value, dict):
        raise ValueError("JSON object required")
    # Also rejects numeric overflow (for example 1e999) accepted by json.load.
    canonical_json_bytes(value)
    return value


def load_native_qualification_artifacts(
    prepared: NativeDevelopmentTrack,
    snapshot: GraphSnapshot,
    *,
    qualification_path: Path,
    work_path: Path,
    candidate_path: Path,
    live_path: Path,
) -> NativeQualifiedArtifacts:
    """Replay every selected native task and require exact supplied catalogs.

    The snapshot is the expected archive graph, not a new live observation. The
    completed qualification must independently prove its pre/post graph bindings
    inside the native certifier. This function performs no service or model calls
    and grants no authority to execute or publish a campaign.
    """
    try:
        corpus, offline = native_qualification_artifacts(prepared)
        snapshot = GraphSnapshot.model_validate_json(snapshot.model_dump_json())
        qualification = _read_object(qualification_path)
        work = _read_object(work_path)
        candidate = _read_object(candidate_path)
        live = _read_object(live_path)
        rebuilt = live_certify_native_corpus(
            corpus,
            offline,
            prepared.profile,
            archive_snapshot=snapshot,
            live_snapshot_before=snapshot,
            live_snapshot_after=snapshot,
            qualification=qualification,
            work_result=work,
        )
        if canonical_json_bytes(candidate) != canonical_json_bytes(
            rebuilt.candidate_catalog.model_dump(mode="json")
        ) or canonical_json_bytes(live) != canonical_json_bytes(rebuilt.model_dump(mode="json")):
            raise ValueError("native qualification catalog mismatch")
        return NativeQualifiedArtifacts(
            prepared,
            rebuilt,
            canonical_sha256(qualification),
            canonical_sha256(work),
        )
    except (OSError, KeyError, TypeError, ValueError, AttributeError):
        raise NativeQualificationArtifactError("NATIVE_QUALIFICATION_ARTIFACT_INVALID") from None
