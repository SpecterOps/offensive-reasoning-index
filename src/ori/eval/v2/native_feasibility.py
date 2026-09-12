"""Offline selected-cell assessment, never campaign or publication admission."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from . import compiler
from .diagnostic_selection import DiagnosticSelectionReceiptV1
from .fingerprint import canonical_sha256, certifier_fingerprint
from .fixtures import OfflineCertification, offline_certify
from .graph import build_archive_snapshot
from .live_projection import NativeProofUnsupported
from .native_capability import NativeCapabilityProfile, validate_native_capability_profile
from .oaic_recipes import (
    OAIC_RECIPE_REGISTRY,
    _compile_oaic_recipe,
    build_oaic_recipe_metadata,
    compile_oaic_product,
)
from .protocol import V2ArtifactPair, build_artifacts
from .release_selection import QUOTAS, SelectedReleaseReceipt, _recipe_rank
from .schema import CertificationState, Track


class NativeFeasibilityInputError(ValueError):
    """An explicit source/selection/profile input binding failed validation."""


@dataclass(frozen=True)
class NativeTaskFeasibility:
    task_id: str
    status: Literal["offline_supported", "unsupported", "harness_error"]
    reason: Literal[
        "fixture_replay_passed",
        "native_contract_unsupported",
        "native_proof_unimplemented",
        "native_compiler_or_certifier_error",
    ]
    certification: OfflineCertification | None = None


@dataclass(frozen=True)
class NativeDevelopmentTrack:
    """Private offline preparation; deliberately not a campaign PreparedTrack.

    There is no candidate release, live proof, ranking or admission authority.
    Only the complete original selected cell can produce this object.
    """

    pair: V2ArtifactPair = field(repr=False)
    profile: NativeCapabilityProfile = field(repr=False)
    certifications: tuple[OfflineCertification, ...] = field(repr=False)
    selection: SelectedReleaseReceipt | DiagnosticSelectionReceiptV1 = field(repr=False)
    corpus: compiler.CompiledCorpus = field(repr=False)

    @property
    def selection_fingerprint(self) -> str:
        return self.selection.selection_fingerprint

    @property
    def task_ids(self) -> tuple[str, ...]:
        return tuple(task.task_id for task in self.pair.public.tasks)


def validate_native_development_track(prepared: NativeDevelopmentTrack) -> NativeDevelopmentTrack:
    """Revalidate private inputs before dispatch; never promote offline evidence."""
    if type(prepared) is not NativeDevelopmentTrack:
        raise NativeFeasibilityInputError("native development preparation required")
    profile = validate_native_capability_profile(prepared.profile)
    pair = V2ArtifactPair.model_validate(prepared.pair.model_dump(mode="python"))
    corpus = compiler.CompiledCorpus.model_validate_json(prepared.corpus.model_dump_json())
    rebuilt_public, rebuilt_private = build_artifacts(
        corpus,
        identity_catalog=pair.private.identity_catalog,
    )
    if V2ArtifactPair(public=rebuilt_public, private=rebuilt_private) != pair:
        raise NativeFeasibilityInputError("native development compiled corpus mismatch")
    selection_type = (
        DiagnosticSelectionReceiptV1
        if isinstance(prepared.selection, DiagnosticSelectionReceiptV1)
        else SelectedReleaseReceipt
    )
    selection = selection_type.model_validate(prepared.selection.model_dump(mode="python"))
    expected_count = 5 if selection_type is DiagnosticSelectionReceiptV1 else 50
    certifications = tuple(
        OfflineCertification.model_validate(c.model_dump(mode="python"))
        for c in prepared.certifications
    )
    if (
        len(pair.public.tasks) != expected_count
        or len(certifications) != expected_count
        or tuple(t.task_id for t in pair.public.tasks) != selection.selected_task_ids
        or pair.public.track is not Track.MCP
        or selection.track is not Track.MCP
        or pair.public.seed != selection.seed
        or pair.public.product != selection.product
        or pair.public.graph_fingerprint != selection.graph_fingerprint
        or pair.public.compiler_fingerprint != selection.compiler_fingerprint
        or pair.public.source_manifest_fingerprint != selection.source_manifest_fingerprint
    ):
        raise NativeFeasibilityInputError("native development roster/source mismatch")
    current_certifier = certifier_fingerprint()
    for task, oracle, offline in zip(
        pair.public.tasks,
        pair.private.oracles,
        certifications,
        strict=True,
    ):
        cert, fixtures = offline.certification, offline.fixtures
        if (
            cert.state is not CertificationState.OFFLINE_CERTIFIED
            or cert.failures
            or cert.live_proof_fingerprint is not None
            or cert.task_id != task.task_id
            or fixtures.task_id != task.task_id
            or cert.task_fingerprint != task.task_fingerprint
            or fixtures.task_fingerprint != task.task_fingerprint
            or cert.oracle_fingerprint != oracle.oracle_fingerprint
            or fixtures.oracle_fingerprint != oracle.oracle_fingerprint
            or cert.graph_fingerprint != pair.public.graph_fingerprint
            or cert.compiler_fingerprint != pair.public.compiler_fingerprint
            or cert.comparator_fingerprint != pair.public.comparator_fingerprint
            or fixtures.comparator_fingerprint != pair.public.comparator_fingerprint
            or cert.certifier_fingerprint != current_certifier
            or cert.capability_profile_fingerprint != profile.profile_fingerprint
            or task.binding.capability_profile_id != profile.profile_id
            or cert.bounds_fingerprint != canonical_sha256(task.binding.bounds)
        ):
            raise NativeFeasibilityInputError("native development certification mismatch")
    return NativeDevelopmentTrack(pair, profile, certifications, selection, corpus)


def write_native_development_artifacts(
    prepared: NativeDevelopmentTrack,
    output_dir: Path,
) -> None:
    """Export the existing artifact formats into a new private directory.

    Never overwrite an export. An interrupted export is not admitted by preflight;
    retain it for diagnosis and choose a fresh output directory for another attempt.
    """
    from .campaign_runner import _atomic_write, _exclusive_output_dir_lock

    validated = validate_native_development_track(prepared)
    output_dir.mkdir(parents=True, mode=0o700)
    with _exclusive_output_dir_lock(output_dir):
        _atomic_write(
            output_dir / "native-public-v2.json", validated.pair.public.model_dump(mode="json")
        )
        _atomic_write(
            output_dir / "native-oracles-v2.private.json",
            validated.pair.private.model_dump(mode="json"),
        )


def native_qualification_artifacts(prepared: NativeDevelopmentTrack):
    """Reuse the exact selected compilation and completed offline fixture results."""
    from .certification import _offline_catalog_from_certifications
    from .fixtures import validate_fixture_coverage_artifacts

    validated = validate_native_development_track(prepared)
    validate_fixture_coverage_artifacts(validated.certifications)
    return validated.corpus, _offline_catalog_from_certifications(
        validated.corpus,
        validated.profile,
        validated.certifications,
    )


@dataclass(frozen=True)
class NativeCellFeasibility:
    implementation_id: str
    selection_fingerprint: str
    tasks: tuple[NativeTaskFeasibility, ...]
    development: NativeDevelopmentTrack | None = field(default=None, repr=False)

    @property
    def offline_feasible(self) -> bool:
        return len(self.tasks) == 50 and all(t.status == "offline_supported" for t in self.tasks)


def assess_native_selected_cell(*, manifest, archive: bytes, selection, profile):
    """Replay all original selected contracts against one expected native profile.

    Rebuild the source and seed-selected roster, not its upstream live receipts.
    Results contain private certification data and must not be publicly exported.
    No provider, MCP, graph service, candidate promotion or file writes occur.
    """
    try:
        selection = SelectedReleaseReceipt.model_validate(selection.model_dump(mode="python"))
        profile = validate_native_capability_profile(profile)
    except (ValueError, TypeError) as exc:
        raise NativeFeasibilityInputError("invalid native selection/profile") from exc
    if selection.track is not Track.MCP:
        raise NativeFeasibilityInputError("native feasibility requires the MCP selected track")
    import hashlib

    if selection.source_archive_sha256 != hashlib.sha256(
        archive
    ).hexdigest() or selection.source_manifest_fingerprint != canonical_sha256(manifest):
        raise NativeFeasibilityInputError("native feasibility archive/manifest mismatch")
    snapshot = build_archive_snapshot(archive, manifest, product=selection.product)
    corpus = compile_oaic_product(manifest, snapshot, track=Track.MCP)
    public, _ = build_artifacts(corpus, identity_catalog=snapshot.entities)
    metadata = build_oaic_recipe_metadata(corpus)
    if (
        selection.seed != corpus.seed
        or selection.graph_fingerprint != corpus.graph_fingerprint
        or selection.compiler_fingerprint != corpus.compiler_fingerprint
        or selection.public_artifact_fingerprint != public.artifact_fingerprint
        or selection.metadata_fingerprint != metadata.metadata_fingerprint
    ):
        raise NativeFeasibilityInputError("native feasibility stale or mixed source artifacts")
    expected = []
    for kind, _, quota in QUOTAS:
        pool = [
            entry
            for entry in metadata.entries
            if entry.eligibility == "main" and entry.claim_kind == kind
        ]
        expected.extend(
            sorted(
                pool,
                key=lambda entry: _recipe_rank(
                    entry,
                    seed=corpus.seed,
                    track=Track.MCP,
                ),
            )[:quota]
        )
    fields = (
        "task_id",
        "recipe_id",
        "variant_id",
        "claim_kind",
        "task_fingerprint",
        "public_semantic_fingerprint",
    )
    if len(expected) != 50 or [tuple(getattr(e, f) for f in fields) for e in expected] != [
        tuple(getattr(e, f) for f in fields) for e in selection.entries
    ]:
        raise NativeFeasibilityInputError(
            "native feasibility must preserve the exact seed-selected 50 tasks",
        )
    recipes = {recipe.recipe_id: recipe for recipe in OAIC_RECIPE_REGISTRY}
    paths = compiler._paths_by_template(manifest)
    outcomes = []
    compiled_tasks = []
    for entry in selection.entries:
        try:
            task = _compile_oaic_recipe(
                recipes[entry.recipe_id],
                snapshot,
                paths,
                corpus.graph_fact_registry,
                track=Track.MCP,
                native_profile=profile,
            )
            certified = offline_certify(task, snapshot, native_profile=profile)
        except compiler.NativeContractUnsupported:
            outcome = NativeTaskFeasibility(
                entry.task_id,
                "unsupported",
                "native_contract_unsupported",
            )
        except NativeProofUnsupported:
            outcome = NativeTaskFeasibility(
                entry.task_id,
                "unsupported",
                "native_proof_unimplemented",
            )
        except Exception:
            # Keep all original cells visible. Never relabel a harness defect
            # as lack of model skill or a native structural incompatibility.
            outcome = NativeTaskFeasibility(
                entry.task_id,
                "harness_error",
                "native_compiler_or_certifier_error",
            )
        else:
            compiled_tasks.append(task)
            outcome = NativeTaskFeasibility(
                entry.task_id, "offline_supported", "fixture_replay_passed", certified
            )
        outcomes.append(outcome)
    development = None
    if len(compiled_tasks) == 50 and all(t.status == "offline_supported" for t in outcomes):
        payload = corpus.model_dump(mode="python")
        payload["tasks"] = tuple(compiled_tasks)
        payload["catalog_fingerprint"] = canonical_sha256(
            payload,
            exclude_fields=("catalog_fingerprint",),
        )
        native_corpus = compiler.CompiledCorpus.model_validate(payload)
        public, private = build_artifacts(native_corpus, identity_catalog=snapshot.entities)
        development = NativeDevelopmentTrack(
            pair=V2ArtifactPair(public=public, private=private),
            profile=profile,
            certifications=tuple(t.certification for t in outcomes),
            selection=selection,
            corpus=native_corpus,
        )
        if development.task_ids != selection.selected_task_ids:
            raise NativeFeasibilityInputError("native preparation changed selected task order")
    return NativeCellFeasibility(
        profile.implementation_id,
        selection.selection_fingerprint,
        tuple(outcomes),
        development,
    )
