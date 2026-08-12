"""Live parity proof, candidate promotion, and selector-facing catalogs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import model_validator

from .comparator import COMPARATOR_FINGERPRINT, compare
from .compiler import CompiledCorpus, CompiledTask, compiler_fingerprint
from .fingerprint import canonical_sha256, certifier_fingerprint
from .fixtures import OfflineCertification, offline_certify
from .graph import GraphSnapshot, LiveGraphVerification
from .live_projection import (
    ProjectionSource,
    SurfaceProjection,
    project_direct_fixture,
    project_mcp_fixture,
    semantic_evidence_fingerprint,
)
from .profiles import validate_capability_profile
from .schema import (
    CapabilityProfile,
    CatalogEntry,
    CatalogRelease,
    CertificationState,
    ExecutionClass,
    StrictModel,
    TaskCertification,
    Track,
    VerdictStatus,
)
from .scoring import SampleOutcomeCode

OFFLINE_CERTIFICATION_SCHEMA_VERSION = "ori-eval-offline-certification-v4"
LIVE_CERTIFICATION_SCHEMA_VERSION = "ori-eval-live-certification-v4"


class CertificationError(ValueError):
    """Raised when a task cannot be promoted through the v2 lifecycle."""


class OfflineCertificationCatalog(StrictModel):
    schema_version: Literal["ori-eval-offline-certification-v4"] = (
        OFFLINE_CERTIFICATION_SCHEMA_VERSION
    )
    protocol_version: Literal["ori-eval-protocol-v2"] = "ori-eval-protocol-v2"
    product: str
    track: Track
    seed: int
    graph_fingerprint: str
    compiler_fingerprint: str
    comparator_fingerprint: str
    certifier_fingerprint: str
    capability_profile_fingerprint: str
    certifications: tuple[OfflineCertification, ...]
    artifact_fingerprint: str

    @model_validator(mode="after")
    def fingerprint_matches(self) -> OfflineCertificationCatalog:
        task_ids = [
            item.certification.task_id for item in self.certifications
        ]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("offline certification catalog has duplicate tasks")
        current_certifier = certifier_fingerprint()
        if self.certifier_fingerprint != current_certifier or any(
            item.certification.certifier_fingerprint != current_certifier
            for item in self.certifications
        ):
            raise ValueError("offline certification certifier is stale")
        expected = canonical_sha256(
            self,
            exclude_fields=("artifact_fingerprint",),
        )
        if self.artifact_fingerprint != expected:
            raise ValueError("offline certification catalog fingerprint mismatch")
        return self


def build_offline_certification_catalog(
    corpus: CompiledCorpus,
    snapshot: GraphSnapshot,
    profile: CapabilityProfile,
) -> OfflineCertificationCatalog:
    """Run and bind every deterministic adversarial fixture in one track."""

    validate_capability_profile(profile)
    if snapshot.graph_fingerprint != corpus.graph_fingerprint:
        raise CertificationError("offline certification graph fingerprint mismatch")
    if profile.track is not corpus.track:
        raise CertificationError("offline certification profile track mismatch")
    certifications = tuple(
        offline_certify(task, snapshot) for task in corpus.tasks
    )
    payload = {
        "product": corpus.product,
        "track": corpus.track,
        "seed": corpus.seed,
        "graph_fingerprint": corpus.graph_fingerprint,
        "compiler_fingerprint": corpus.compiler_fingerprint,
        "comparator_fingerprint": COMPARATOR_FINGERPRINT,
        "certifier_fingerprint": certifier_fingerprint(),
        "capability_profile_fingerprint": profile.profile_fingerprint,
        "certifications": certifications,
        "artifact_fingerprint": "0" * 64,
    }
    payload["artifact_fingerprint"] = canonical_sha256(
        {
            "schema_version": OFFLINE_CERTIFICATION_SCHEMA_VERSION,
            "protocol_version": "ori-eval-protocol-v2",
            **payload,
        },
        exclude_fields=("artifact_fingerprint",),
    )
    return OfflineCertificationCatalog.model_validate(payload)


class ParityCase(StrictModel):
    name: str
    offline_evidence_fingerprint: str
    live_evidence_fingerprint: str
    offline_verdict_fingerprint: str
    live_verdict_fingerprint: str
    applicable: bool = True
    inapplicable_reason: str | None = None

    @model_validator(mode="after")
    def parity_is_exact_or_explained(self) -> ParityCase:
        if self.applicable:
            if (
                self.offline_evidence_fingerprint
                != self.live_evidence_fingerprint
            ):
                raise ValueError(f"{self.name} Evidence IR parity mismatch")
            if self.offline_verdict_fingerprint != self.live_verdict_fingerprint:
                raise ValueError(f"{self.name} verdict parity mismatch")
            if self.inapplicable_reason is not None:
                raise ValueError("applicable parity cases cannot declare an exception")
        elif not self.inapplicable_reason:
            raise ValueError("inapplicable parity cases require a reason")
        return self


class ProjectionParityCase(StrictModel):
    """Exact archive/live agreement after the declared track adapter."""

    name: str
    applicable: bool
    expected_status: VerdictStatus | None = None
    archive_projection_source: ProjectionSource | None = None
    live_projection_source: ProjectionSource | None = None
    archive_execution_class: ExecutionClass | None = None
    live_execution_class: ExecutionClass | None = None
    archive_outcome: SampleOutcomeCode | None = None
    live_outcome: SampleOutcomeCode | None = None
    archive_evidence_fingerprint: str | None = None
    live_evidence_fingerprint: str | None = None
    archive_verdict_fingerprint: str | None = None
    live_verdict_fingerprint: str | None = None
    archive_verdict_status: VerdictStatus | None = None
    live_verdict_status: VerdictStatus | None = None
    archive_raw_source_digest: str | None = None
    live_raw_source_digest: str | None = None
    archive_rejection_fingerprint: str | None = None
    live_rejection_fingerprint: str | None = None
    inapplicable_reason: str | None = None

    @model_validator(mode="after")
    def archive_and_live_agree(self) -> ProjectionParityCase:
        runtime_fields = (
            self.archive_projection_source,
            self.live_projection_source,
            self.archive_execution_class,
            self.live_execution_class,
            self.archive_outcome,
            self.live_outcome,
            self.archive_raw_source_digest,
            self.live_raw_source_digest,
        )
        if not self.applicable:
            if not self.inapplicable_reason:
                raise ValueError(
                    "inapplicable projection parity requires a reason"
                )
            if any(value is not None for value in runtime_fields):
                raise ValueError(
                    "inapplicable projection parity cannot carry runtime results"
                )
            return self

        if self.expected_status is None:
            raise ValueError("applicable projection parity requires an expectation")
        if self.inapplicable_reason is not None:
            raise ValueError(
                "applicable projection parity cannot declare an exception"
            )
        if any(value is None for value in runtime_fields):
            raise ValueError("applicable projection parity is incomplete")
        if self.archive_projection_source != self.live_projection_source:
            raise ValueError(f"{self.name} projection source mismatch")
        if self.archive_execution_class != self.live_execution_class:
            raise ValueError(f"{self.name} execution classification mismatch")
        if self.archive_outcome != self.live_outcome:
            raise ValueError(f"{self.name} outcome mismatch")
        if self.archive_raw_source_digest != self.live_raw_source_digest:
            raise ValueError(f"{self.name} raw projection source mismatch")

        evidence_pair = (
            self.archive_evidence_fingerprint,
            self.live_evidence_fingerprint,
        )
        verdict_pair = (
            self.archive_verdict_fingerprint,
            self.live_verdict_fingerprint,
        )
        rejection_pair = (
            self.archive_rejection_fingerprint,
            self.live_rejection_fingerprint,
        )
        if any(value is not None for value in evidence_pair):
            if (
                evidence_pair[0] is None
                or evidence_pair[0] != evidence_pair[1]
            ):
                raise ValueError(f"{self.name} Evidence IR parity mismatch")
            if (
                verdict_pair[0] is None
                or verdict_pair[0] != verdict_pair[1]
                or self.archive_verdict_status is None
                or self.archive_verdict_status != self.live_verdict_status
            ):
                raise ValueError(f"{self.name} verdict parity mismatch")
            if any(value is not None for value in rejection_pair):
                raise ValueError(
                    f"{self.name} cannot be gradeable and rejected"
                )
            if (
                self.archive_execution_class is not ExecutionClass.SUCCESS
                or self.archive_outcome is not SampleOutcomeCode.COMPLETED
            ):
                raise ValueError(
                    f"{self.name} gradeable projection is not successful"
                )
            if self.archive_verdict_status is not self.expected_status:
                raise ValueError(
                    f"{self.name} projected verdict violates its expectation"
                )
        else:
            if any(value is not None for value in verdict_pair) or any(
                value is not None
                for value in (
                    self.archive_verdict_status,
                    self.live_verdict_status,
                )
            ):
                raise ValueError(
                    f"{self.name} rejected projection cannot carry a verdict"
                )
            if (
                rejection_pair[0] is None
                or rejection_pair[0] != rejection_pair[1]
            ):
                raise ValueError(f"{self.name} rejection parity mismatch")
            if self.expected_status is not VerdictStatus.INCORRECT:
                raise ValueError(
                    f"{self.name} correct fixture cannot be adapter-rejected"
                )
            if (
                self.archive_execution_class is not ExecutionClass.MODEL_FAILURE
                or self.archive_outcome is not SampleOutcomeCode.OUTPUT_INVALID
            ):
                raise ValueError(
                    f"{self.name} rejection lacks typed model-failure accounting"
                )
        return self


class LiveCertificationProof(StrictModel):
    task_id: str
    task_fingerprint: str
    oracle_fingerprint: str
    fixture_fingerprint: str
    certifier_fingerprint: str
    capability_profile_id: str
    capability_profile_fingerprint: str
    graph_fingerprint_before: str
    graph_fingerprint_after: str
    parity_cases: tuple[ParityCase, ...]
    projection_cases: tuple[ProjectionParityCase, ...]
    proof_fingerprint: str

    @model_validator(mode="after")
    def proof_is_complete_and_immutable(self) -> LiveCertificationProof:
        if self.graph_fingerprint_before != self.graph_fingerprint_after:
            raise ValueError("live graph changed during task certification")
        names = [case.name for case in self.parity_cases]
        if len(names) != len(set(names)):
            raise ValueError("live parity proof contains duplicate fixture names")
        applicable = {case.name for case in self.parity_cases if case.applicable}
        if not {"perfect", "wrong", "empty"}.issubset(applicable):
            raise ValueError(
                "live certification requires perfect, wrong, and empty parity cases"
            )
        projection_names = [case.name for case in self.projection_cases]
        if projection_names != names:
            raise ValueError(
                "projection parity must cover the exact fixture order"
            )
        projected = {
            case.name for case in self.projection_cases if case.applicable
        }
        if not {"perfect", "wrong", "empty"}.issubset(projected):
            raise ValueError(
                "adapter parity requires perfect, wrong, and empty cases"
            )
        perfect = next(
            case for case in self.projection_cases if case.name == "perfect"
        )
        if (
            perfect.archive_projection_source
            != "graph_snapshot_replay"
        ):
            raise ValueError(
                "perfect adapter parity must derive from the graph snapshot"
            )
        expected = canonical_sha256(self, exclude_fields=("proof_fingerprint",))
        if self.proof_fingerprint != expected:
            raise ValueError("live certification proof fingerprint mismatch")
        return self


def build_live_certification_proof(
    task: CompiledTask,
    offline: OfflineCertification,
    profile: CapabilityProfile,
    *,
    graph_fingerprint_before: str,
    graph_fingerprint_after: str,
    parity_cases: tuple[ParityCase, ...],
    projection_cases: tuple[ProjectionParityCase, ...],
) -> LiveCertificationProof:
    """Bind deterministic live/offline parity evidence to one task."""

    validate_capability_profile(profile)
    payload = {
        "task_id": task.public.task_id,
        "task_fingerprint": task.public.task_fingerprint,
        "oracle_fingerprint": task.oracle.oracle_fingerprint,
        "fixture_fingerprint": offline.fixtures.fixture_fingerprint,
        "certifier_fingerprint": certifier_fingerprint(),
        "capability_profile_id": profile.profile_id,
        "capability_profile_fingerprint": profile.profile_fingerprint,
        "graph_fingerprint_before": graph_fingerprint_before,
        "graph_fingerprint_after": graph_fingerprint_after,
        "parity_cases": parity_cases,
        "projection_cases": projection_cases,
        "proof_fingerprint": "0" * 64,
    }
    payload["proof_fingerprint"] = canonical_sha256(
        payload,
        exclude_fields=("proof_fingerprint",),
    )
    return LiveCertificationProof.model_validate(payload)


def _require_correct_evidence_in_live_graph(
    task: CompiledTask,
    offline: OfflineCertification,
    live_snapshot: GraphSnapshot,
) -> None:
    known_ids = {entity.object_id for entity in live_snapshot.entities}
    live_properties = {
        (item.entity.object_id, fact.key.casefold(), fact.value)
        for item in live_snapshot.objects
        for fact in item.properties
    }
    correct_cases = tuple(
        case
        for case in offline.fixtures.cases
        if case.applicable
        and case.expected_status is not None
        and case.expected_status.value == "correct"
    )
    if not any(case.name == "perfect" for case in correct_cases):
        raise CertificationError(
            f"task {task.public.task_id} has no gradeable perfect fixture"
        )
    for case in correct_cases:
        if case.evidence is None:
            raise CertificationError(
                f"task {task.public.task_id} correct fixture {case.name} "
                "has no Evidence IR"
            )
        evidence_ids = {
            *(entity.object_id for entity in case.evidence.entities),
            *(
                endpoint
                for edge in (
                    *case.evidence.edges,
                    *case.evidence.supporting_edges,
                )
                for endpoint in (edge.source_id, edge.target_id)
            ),
            *(fact.entity_id for fact in case.evidence.observed_properties),
        }
        missing_ids = sorted(evidence_ids - known_ids)
        if missing_ids:
            raise CertificationError(
                f"task {task.public.task_id} {case.name} evidence references "
                f"missing live objects: {missing_ids}"
            )
        missing_edges = sorted(
            (
                edge.source_id,
                edge.relationship,
                edge.target_id,
            )
            for edge in (
                *case.evidence.edges,
                *case.evidence.supporting_edges,
            )
            if (
                edge.source_id,
                edge.relationship,
                edge.target_id,
            )
            not in live_snapshot.edge_keys
        )
        if missing_edges:
            raise CertificationError(
                f"task {task.public.task_id} {case.name} evidence is absent "
                f"from the live graph: {missing_edges}"
            )
        missing_properties = sorted(
            (fact.entity_id, fact.key, repr(fact.value))
            for fact in case.evidence.observed_properties
            if (fact.entity_id, fact.key.casefold(), fact.value)
            not in live_properties
        )
        if missing_properties:
            raise CertificationError(
                f"task {task.public.task_id} {case.name} property evidence is "
                f"absent from the live graph: {missing_properties}"
            )


def build_fixture_parity_cases(
    task: CompiledTask,
    offline: OfflineCertification,
    live_snapshot: GraphSnapshot,
) -> tuple[ParityCase, ...]:
    """Bind comparator fixtures to an identity-equivalent live graph."""

    if live_snapshot.graph_fingerprint != task.oracle.graph_fingerprint:
        raise CertificationError("fixture parity live graph fingerprint mismatch")
    _require_correct_evidence_in_live_graph(task, offline, live_snapshot)

    parity: list[ParityCase] = []
    for case in offline.fixtures.cases:
        if not case.applicable or case.evidence is None:
            reason = (
                case.inapplicable_reason
                or case.normalization_error
                or "fixture has no gradeable Evidence IR"
            )
            marker = canonical_sha256(
                {
                    "task_id": task.public.task_id,
                    "fixture": case.name,
                    "reason": reason,
                }
            )
            parity.append(
                ParityCase(
                    name=case.name,
                    offline_evidence_fingerprint=marker,
                    live_evidence_fingerprint=marker,
                    offline_verdict_fingerprint=marker,
                    live_verdict_fingerprint=marker,
                    applicable=False,
                    inapplicable_reason=reason,
                )
            )
            continue

        offline_verdict = compare(
            task.public.answer_policy,
            task.oracle,
            case.evidence,
        )
        # The exhaustive live snapshot is identity- and edge-equivalent to the
        # archive snapshot. Projecting canonical IR onto it is byte preserving.
        live_evidence = case.evidence
        live_verdict = compare(
            task.public.answer_policy,
            task.oracle,
            live_evidence,
        )
        parity.append(
            ParityCase(
                name=case.name,
                offline_evidence_fingerprint=canonical_sha256(case.evidence),
                live_evidence_fingerprint=canonical_sha256(live_evidence),
                offline_verdict_fingerprint=canonical_sha256(offline_verdict),
                live_verdict_fingerprint=canonical_sha256(live_verdict),
            )
        )
    return tuple(parity)


def _projection_case(
    fixture_name: str,
    expected_status: VerdictStatus,
    archive: SurfaceProjection,
    live: SurfaceProjection,
) -> ProjectionParityCase:
    def evidence_fingerprint(result: SurfaceProjection) -> str | None:
        return (
            semantic_evidence_fingerprint(result.evidence)
            if result.evidence is not None
            else None
        )

    def verdict_fingerprint(result: SurfaceProjection) -> str | None:
        return (
            canonical_sha256(result.verdict)
            if result.verdict is not None
            else None
        )

    def rejection_fingerprint(result: SurfaceProjection) -> str | None:
        return (
            canonical_sha256({"reason": result.rejection_reason})
            if result.rejection_reason is not None
            else None
        )

    return ProjectionParityCase(
        name=fixture_name,
        applicable=True,
        expected_status=expected_status,
        archive_projection_source=archive.projection_source,
        live_projection_source=live.projection_source,
        archive_execution_class=archive.execution_class,
        live_execution_class=live.execution_class,
        archive_outcome=archive.outcome,
        live_outcome=live.outcome,
        archive_evidence_fingerprint=evidence_fingerprint(archive),
        live_evidence_fingerprint=evidence_fingerprint(live),
        archive_verdict_fingerprint=verdict_fingerprint(archive),
        live_verdict_fingerprint=verdict_fingerprint(live),
        archive_verdict_status=(
            archive.verdict.status if archive.verdict is not None else None
        ),
        live_verdict_status=(
            live.verdict.status if live.verdict is not None else None
        ),
        archive_raw_source_digest=archive.raw_source_digest,
        live_raw_source_digest=live.raw_source_digest,
        archive_rejection_fingerprint=rejection_fingerprint(archive),
        live_rejection_fingerprint=rejection_fingerprint(live),
    )


def build_projection_parity_cases(
    task: CompiledTask,
    offline: OfflineCertification,
    profile: CapabilityProfile,
    *,
    archive_snapshot: GraphSnapshot,
    live_snapshot: GraphSnapshot,
) -> tuple[ProjectionParityCase, ...]:
    """Run every fixture through the declared direct or MCP adapter."""

    if (
        archive_snapshot.graph_fingerprint
        != live_snapshot.graph_fingerprint
        or archive_snapshot.graph_fingerprint
        != task.oracle.graph_fingerprint
    ):
        raise CertificationError("adapter parity graph fingerprint mismatch")
    perfect = next(
        (
            case
            for case in offline.fixtures.cases
            if case.name == "perfect"
            and case.applicable
            and case.evidence is not None
        ),
        None,
    )
    if perfect is None or perfect.evidence is None:
        raise CertificationError(
            f"task {task.public.task_id} lacks gradeable perfect evidence"
        )

    projections: list[ProjectionParityCase] = []
    for case in offline.fixtures.cases:
        if not case.applicable:
            projections.append(
                ProjectionParityCase(
                    name=case.name,
                    applicable=False,
                    inapplicable_reason=case.inapplicable_reason,
                )
            )
            continue
        if case.expected_status is None:
            raise CertificationError(
                f"task {task.public.task_id} fixture {case.name} "
                "has no expected status"
            )
        if task.public.binding.track is Track.DIRECT:
            archive_projection = project_direct_fixture(
                task=task.public,
                oracle=task.oracle,
                case=case,
                snapshot=archive_snapshot,
            )
            live_projection = project_direct_fixture(
                task=task.public,
                oracle=task.oracle,
                case=case,
                snapshot=live_snapshot,
            )
        else:
            archive_projection = project_mcp_fixture(
                task=task.public,
                oracle=task.oracle,
                case=case,
                perfect_evidence=perfect.evidence,
                profile=profile,
                snapshot=archive_snapshot,
            )
            live_projection = project_mcp_fixture(
                task=task.public,
                oracle=task.oracle,
                case=case,
                perfect_evidence=perfect.evidence,
                profile=profile,
                snapshot=live_snapshot,
            )
        projections.append(
            _projection_case(
                case.name,
                case.expected_status,
                archive_projection,
                live_projection,
            )
        )
    return tuple(projections)


def live_certify_task(
    task: CompiledTask,
    offline: OfflineCertification,
    profile: CapabilityProfile,
    *,
    archive_snapshot: GraphSnapshot,
    live_snapshot_before: GraphSnapshot,
    live_snapshot_after: GraphSnapshot,
) -> tuple[LiveCertificationProof, TaskCertification]:
    """Certify one task after exact pre/post live graph and fixture parity."""

    if live_snapshot_before.graph_fingerprint != task.oracle.graph_fingerprint:
        raise CertificationError("pre-track live graph does not match the task oracle")
    if live_snapshot_after.graph_fingerprint != task.oracle.graph_fingerprint:
        raise CertificationError("post-track live graph does not match the task oracle")
    parity_cases = build_fixture_parity_cases(
        task,
        offline,
        live_snapshot_before,
    )
    projection_cases = build_projection_parity_cases(
        task,
        offline,
        profile,
        archive_snapshot=archive_snapshot,
        live_snapshot=live_snapshot_before,
    )
    proof = build_live_certification_proof(
        task,
        offline,
        profile,
        graph_fingerprint_before=live_snapshot_before.graph_fingerprint,
        graph_fingerprint_after=live_snapshot_after.graph_fingerprint,
        parity_cases=parity_cases,
        projection_cases=projection_cases,
    )
    return proof, promote_candidate(task, offline, profile, proof)


class LiveCertificationCatalog(StrictModel):
    schema_version: Literal["ori-eval-live-certification-v4"] = (
        LIVE_CERTIFICATION_SCHEMA_VERSION
    )
    protocol_version: Literal["ori-eval-protocol-v2"] = "ori-eval-protocol-v2"
    product: str
    track: Track
    graph_fingerprint: str
    capability_profile_fingerprint: str
    certifier_fingerprint: str
    offline_catalog_fingerprint: str
    verification_before_fingerprint: str
    verification_after_fingerprint: str
    proofs: tuple[LiveCertificationProof, ...]
    certifications: tuple[TaskCertification, ...]
    candidate_catalog: CatalogRelease
    artifact_fingerprint: str

    @model_validator(mode="after")
    def catalog_is_exact(self) -> LiveCertificationCatalog:
        proof_ids = [proof.task_id for proof in self.proofs]
        certification_ids = [
            certification.task_id for certification in self.certifications
        ]
        entry_ids = [entry.task_id for entry in self.candidate_catalog.entries]
        if not (
            len(proof_ids)
            == len(set(proof_ids))
            == len(certification_ids)
            == len(set(certification_ids))
            == len(entry_ids)
            == len(set(entry_ids))
        ):
            raise ValueError("live certification catalog task accounting mismatch")
        if set(proof_ids) != set(certification_ids) or set(proof_ids) != set(
            entry_ids
        ):
            raise ValueError("live certification catalog task sets differ")
        current_certifier = certifier_fingerprint()
        if (
            self.certifier_fingerprint != current_certifier
            or any(
                proof.certifier_fingerprint != current_certifier
                for proof in self.proofs
            )
            or any(
                certification.certifier_fingerprint != current_certifier
                for certification in self.certifications
            )
        ):
            raise ValueError("live certification certifier is stale")
        expected = canonical_sha256(
            self,
            exclude_fields=("artifact_fingerprint",),
        )
        if self.artifact_fingerprint != expected:
            raise ValueError("live certification catalog fingerprint mismatch")
        return self


def live_certify_corpus(
    corpus: CompiledCorpus,
    offline: OfflineCertificationCatalog,
    profile: CapabilityProfile,
    *,
    archive_snapshot: GraphSnapshot,
    live_snapshot_before: GraphSnapshot,
    live_snapshot_after: GraphSnapshot,
    verification_before: LiveGraphVerification,
    verification_after: LiveGraphVerification,
) -> LiveCertificationCatalog:
    """Promote an entire track with exact accounting and pre/post receipts."""

    validate_capability_profile(profile)
    if offline.track is not corpus.track or profile.track is not corpus.track:
        raise CertificationError("live certification track mismatch")
    if (
        archive_snapshot.graph_fingerprint != corpus.graph_fingerprint
        or
        live_snapshot_before.graph_fingerprint != corpus.graph_fingerprint
        or live_snapshot_after.graph_fingerprint != corpus.graph_fingerprint
    ):
        raise CertificationError("live certification graph mismatch")
    if (
        verification_before.observed_graph_fingerprint
        != live_snapshot_before.graph_fingerprint
        or verification_after.observed_graph_fingerprint
        != live_snapshot_after.graph_fingerprint
    ):
        raise CertificationError("live verification receipt does not bind its snapshot")
    offline_by_task = {
        item.certification.task_id: item
        for item in offline.certifications
    }
    expected_ids = {task.public.task_id for task in corpus.tasks}
    if set(offline_by_task) != expected_ids:
        raise CertificationError("offline certification task accounting mismatch")

    proofs: list[LiveCertificationProof] = []
    certifications: dict[str, TaskCertification] = {}
    for task in corpus.tasks:
        proof, certification = live_certify_task(
            task,
            offline_by_task[task.public.task_id],
            profile,
            archive_snapshot=archive_snapshot,
            live_snapshot_before=live_snapshot_before,
            live_snapshot_after=live_snapshot_after,
        )
        proofs.append(proof)
        certifications[task.public.task_id] = certification
    release = build_catalog_release(corpus, certifications, profile)
    payload = {
        "product": corpus.product,
        "track": corpus.track,
        "graph_fingerprint": corpus.graph_fingerprint,
        "capability_profile_fingerprint": profile.profile_fingerprint,
        "certifier_fingerprint": certifier_fingerprint(),
        "offline_catalog_fingerprint": offline.artifact_fingerprint,
        "verification_before_fingerprint": (
            verification_before.verification_fingerprint
        ),
        "verification_after_fingerprint": (
            verification_after.verification_fingerprint
        ),
        "proofs": tuple(proofs),
        "certifications": tuple(
            certifications[task.public.task_id] for task in corpus.tasks
        ),
        "candidate_catalog": release,
        "artifact_fingerprint": "0" * 64,
    }
    payload["artifact_fingerprint"] = canonical_sha256(
        {
            "schema_version": LIVE_CERTIFICATION_SCHEMA_VERSION,
            "protocol_version": "ori-eval-protocol-v2",
            **payload,
        },
        exclude_fields=("artifact_fingerprint",),
    )
    return LiveCertificationCatalog.model_validate(payload)


def promote_candidate(
    task: CompiledTask,
    offline: OfflineCertification,
    profile: CapabilityProfile,
    proof: LiveCertificationProof,
) -> TaskCertification:
    """Promote one offline-certified task only after exact live parity."""

    validate_capability_profile(profile)
    base = offline.certification
    mismatches: list[str] = []
    if base.state is not CertificationState.OFFLINE_CERTIFIED:
        mismatches.append("offline state")
    if task.public.task_id != base.task_id or proof.task_id != base.task_id:
        mismatches.append("task ID")
    if task.public.task_fingerprint != base.task_fingerprint:
        mismatches.append("task fingerprint")
    if task.oracle.oracle_fingerprint != base.oracle_fingerprint:
        mismatches.append("oracle fingerprint")
    if proof.task_fingerprint != base.task_fingerprint:
        mismatches.append("proof task fingerprint")
    if proof.oracle_fingerprint != base.oracle_fingerprint:
        mismatches.append("proof oracle fingerprint")
    if proof.fixture_fingerprint != offline.fixtures.fixture_fingerprint:
        mismatches.append("fixture fingerprint")
    if (
        base.certifier_fingerprint != certifier_fingerprint()
        or proof.certifier_fingerprint != certifier_fingerprint()
    ):
        mismatches.append("certifier")
    if proof.graph_fingerprint_before != base.graph_fingerprint:
        mismatches.append("graph fingerprint")
    if (
        proof.capability_profile_id != profile.profile_id
        or proof.capability_profile_fingerprint != profile.profile_fingerprint
        or base.capability_profile_fingerprint != profile.profile_fingerprint
    ):
        mismatches.append("capability profile")
    if base.compiler_fingerprint != compiler_fingerprint():
        mismatches.append("compiler")
    if base.comparator_fingerprint != COMPARATOR_FINGERPRINT:
        mismatches.append("comparator")
    if base.bounds_fingerprint != canonical_sha256(task.public.binding.bounds):
        mismatches.append("bounds")
    if mismatches:
        raise CertificationError(
            f"task {task.public.task_id} cannot be promoted: "
            + ", ".join(mismatches)
        )

    payload = {
        **base.model_dump(mode="python"),
        "state": CertificationState.CANDIDATE,
        "certified_profile_id": profile.profile_id,
        "live_proof_fingerprint": proof.proof_fingerprint,
        "failures": (),
        "certification_fingerprint": "0" * 64,
    }
    payload["certification_fingerprint"] = canonical_sha256(
        payload,
        exclude_fields=("certification_fingerprint",),
    )
    return TaskCertification.model_validate(payload)


def build_catalog_release(
    corpus: CompiledCorpus,
    certifications: Mapping[str, TaskCertification],
    profile: CapabilityProfile,
) -> CatalogRelease:
    """Publish an exact one-track candidate catalog for later selection."""

    validate_capability_profile(profile)
    task_ids = [task.public.task_id for task in corpus.tasks]
    if set(certifications) != set(task_ids):
        raise CertificationError(
            "candidate certification accounting mismatch: "
            f"missing={sorted(set(task_ids) - set(certifications))} "
            f"extra={sorted(set(certifications) - set(task_ids))}"
        )

    entries: list[CatalogEntry] = []
    for task in corpus.tasks:
        certification = certifications[task.public.task_id]
        if certification.state is not CertificationState.CANDIDATE:
            raise CertificationError(
                f"task {task.public.task_id} is not candidate-certified"
            )
        if certification.task_fingerprint != task.public.task_fingerprint:
            raise CertificationError(
                f"task {task.public.task_id} certification is stale"
            )
        if certification.certifier_fingerprint != certifier_fingerprint():
            raise CertificationError(
                f"task {task.public.task_id} certifier is stale"
            )
        entries.append(
            CatalogEntry(
                task_id=task.public.task_id,
                revision=task.public.revision,
                product=corpus.product,
                track=corpus.track,
                family=task.migration.family,
                tier=task.migration.tier,
                claim_kind=task.public.claim_kind,
                semantics=task.public.binding.semantics,
                cost_band=task.migration.cost_band,
                path_concentration_key=task.migration.path_concentration_key,
                task_fingerprint=task.public.task_fingerprint,
                oracle_fingerprint=task.oracle.oracle_fingerprint,
                certification_fingerprint=certification.certification_fingerprint,
            )
        )

    sorted_entries = tuple(sorted(entries, key=lambda entry: entry.task_id))
    catalog_fingerprint = canonical_sha256(sorted_entries)
    payload = {
        "release_id": (
            f"{corpus.product}-{corpus.track.value}-seed-{corpus.seed}-candidates-v2"
        ),
        "product": corpus.product,
        "entries": sorted_entries,
        "graph_fingerprint": corpus.graph_fingerprint,
        "compiler_fingerprint": corpus.compiler_fingerprint,
        "comparator_fingerprint": COMPARATOR_FINGERPRINT,
        "capability_profile_fingerprint": profile.profile_fingerprint,
        "catalog_fingerprint": catalog_fingerprint,
        "release_fingerprint": "0" * 64,
    }
    payload["release_fingerprint"] = canonical_sha256(
        {
            "protocol_version": corpus.protocol_version,
            "manifest_schema_version": corpus.manifest_schema_version,
            **payload,
        },
        exclude_fields=("release_fingerprint",),
    )
    return CatalogRelease.model_validate(payload)
