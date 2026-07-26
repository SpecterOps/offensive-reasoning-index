"""Fail-closed, model-backed campaign orchestration for protocol V2."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from ori.eval.bhce import BHCEClient, parse_bhce_url, resolve_bhce_target
from ori.eval.direct_query_safety import (
    DirectQueryCoordinator,
    DirectQuerySafetyConfig,
    QueryDenyCache,
)
from ori.eval.mcp_runtime import _load_bloodhound_mcp_bundle

from .campaign import (
    CheckpointV2,
    PublicReportV2,
    RunIdentity,
    RunProvenanceV2,
    build_checkpoint,
    build_public_report,
    build_run_provenance,
    validate_checkpoint,
)
from .campaign_config import (
    ResolvedV2CampaignConfig,
    V2ModelEntry,
    load_v2_campaign_config,
)
from .certification import LiveCertificationCatalog
from .fingerprint import canonical_sha256
from .graph import (
    GraphSnapshot,
    LiveGraphVerification,
    build_archive_snapshot,
    collect_live_snapshot,
    require_live_graph_match,
)
from .identity import IdentityResolver
from .mcp import MCPToolLoop
from .model_runtime import (
    ProviderRunRecord,
    run_direct_model_task_v2,
    run_mcp_model_task_v2,
)
from .profiles import capability_profile_for_track
from .protocol import OracleRegistry, V2ArtifactPair, load_v2_pair
from .schema import (
    PROTOCOL_VERSION,
    CapabilityProfile,
    CatalogRelease,
    CertificationState,
    ExecutionClass,
    StrictModel,
    TaskCertification,
    Track,
)
from .scoring import SampleResult, summarize_results

RUNNER_VERSION = "ori-v2-model-campaign-v2"
RUN_STATE_SCHEMA_VERSION = "ori-v2-private-run-state-v1"
MODEL_REPORT_SCHEMA_VERSION = "ori-v2-model-report-v1"
READINESS_SCHEMA_VERSION = "ori-v2-run-readiness-v1"
_RUNNER_IMPLEMENTATION_SOURCES = {
    "campaign": Path(__file__).with_name("campaign.py"),
    "campaign_runner": Path(__file__),
    "direct_adapter": Path(__file__).with_name("direct_adapter.py"),
    "evidence": Path(__file__).with_name("evidence.py"),
    "identity": Path(__file__).with_name("identity.py"),
    "mcp_adapter": Path(__file__).with_name("mcp_adapter.py"),
    "mcp_state_machine": Path(__file__).with_name("mcp.py"),
    "model_runtime": Path(__file__).with_name("model_runtime.py"),
    "provider_loops": Path(__file__).parent.parent / "mcp_runtime.py",
    "runtime": Path(__file__).with_name("runtime.py"),
    "schema": Path(__file__).with_name("schema.py"),
    "scoring": Path(__file__).with_name("scoring.py"),
}
RUNNER_IMPLEMENTATION_FINGERPRINT = canonical_sha256(
    {
        name: hashlib.sha256(path.read_bytes()).hexdigest()
        for name, path in sorted(_RUNNER_IMPLEMENTATION_SOURCES.items())
    }
)
ProgressReporter = Callable[[str], None]


class V2CampaignRunError(ValueError):
    """Raised before model work when campaign state is unsafe or inconsistent."""


def _emit_progress(progress: ProgressReporter | None, message: str) -> None:
    """Report model-blind operator progress without affecting campaign state."""

    if progress is None:
        return
    try:
        progress(message)
    except Exception:
        # Progress output must never change scoring or execution.
        return


def _graph_verification_progress(
    *,
    track: Track,
    stage: Literal["pre", "post"],
    receipt: LiveGraphVerification,
) -> str:
    return (
        f"[{track.value}] {stage}-track graph verified "
        f"({receipt.observed_graph_fingerprint[:12]})"
    )


def _display_outcome(sample: SampleResult) -> str:
    if sample.reasoning_correct is True:
        return "CORRECT"
    if sample.reasoning_correct is False and sample.outcome.value == "COMPLETED":
        return "INCORRECT"
    return sample.outcome.value


def _task_completion_progress(
    *,
    sample: SampleResult,
    provider: ProviderRunRecord,
    task_elapsed_seconds: float,
    results: Sequence[SampleResult],
) -> str:
    correct = sum(item.reasoning_correct is True for item in results)
    graded = sum(item.reasoning_correct is not None for item in results)
    infrastructure = sum(
        item.execution_class.value == "infra_failure" for item in results
    )
    score = (
        "1.0"
        if sample.reasoning_correct is True
        else "0.0"
        if sample.reasoning_correct is False
        else "n/a"
    )
    details = [
        f"score={score}",
        f"elapsed={task_elapsed_seconds:.1f}s",
        f"tokens={provider.tokens_input}+{provider.tokens_output}",
    ]
    if provider.surface.startswith("mcp"):
        tool_events = [
            event for event in provider.mcp_events if event.tool_name is not None
        ]
        details.extend(
            (
                f"tools={len(tool_events)}",
                "cypher="
                + str(
                    sum(
                        event.tool_name == "cypher_query"
                        for event in tool_events
                    )
                ),
            )
        )
    details.extend(
        (
            f"running_correct={correct}/{graded}",
            f"infra={infrastructure}",
        )
    )
    return f"           → {_display_outcome(sample)} ({', '.join(details)})"


def _infrastructure_attempt_progress(
    *,
    sample: SampleResult,
    attempt_number: int,
    max_infra_retries: int,
    attempt_index: int | None = None,
) -> str:
    retry_index = attempt_number if attempt_index is None else attempt_index
    if retry_index <= max_infra_retries:
        state = "infrastructure healthy, retrying"
        arrow = "↻"
    else:
        state = "retry budget exhausted"
        arrow = "→"
    return (
        f"           {arrow} {sample.outcome.value} on attempt "
        f"{attempt_number}; {state}"
    )


class ModelRunProvenanceV2(StrictModel):
    schema_version: Literal["ori-v2-model-campaign-v2"] = RUNNER_VERSION
    protocol_version: Literal["ori-eval-protocol-v2"] = PROTOCOL_VERSION
    base: RunProvenanceV2
    run_identity: RunIdentity
    source_manifest_sha256: str
    archive_sha256: str
    candidate_release_fingerprint: str
    live_certification_fingerprint: str
    containment_config_fingerprint: str
    runtime_implementation_fingerprint: str
    runtime_config_fingerprint: str
    provenance_fingerprint: str

    @model_validator(mode="after")
    def fingerprint_matches(self) -> ModelRunProvenanceV2:
        expected = canonical_sha256(
            self,
            exclude_fields=("provenance_fingerprint",),
        )
        if self.provenance_fingerprint != expected:
            raise ValueError("model-run provenance fingerprint mismatch")
        return self


class ProviderAttemptV2(StrictModel):
    task_id: str
    attempt: int = Field(strict=True, ge=1)
    sample: SampleResult
    provider: ProviderRunRecord
    attempt_fingerprint: str

    @model_validator(mode="after")
    def attempt_is_exact(self) -> ProviderAttemptV2:
        if self.task_id != self.sample.task_id or self.task_id != self.provider.task_id:
            raise ValueError("provider attempt task IDs do not match")
        expected = canonical_sha256(
            self,
            exclude_fields=("attempt_fingerprint",),
        )
        if self.attempt_fingerprint != expected:
            raise ValueError("provider attempt fingerprint mismatch")
        return self


class PrivateRunStateV2(StrictModel):
    schema_version: Literal["ori-v2-private-run-state-v1"] = RUN_STATE_SCHEMA_VERSION
    protocol_version: Literal["ori-eval-protocol-v2"] = PROTOCOL_VERSION
    provenance_fingerprint: str
    checkpoint: CheckpointV2
    attempts: tuple[ProviderAttemptV2, ...]
    state_fingerprint: str

    @model_validator(mode="after")
    def state_is_exact(self) -> PrivateRunStateV2:
        final_by_task: dict[str, ProviderAttemptV2] = {}
        attempt_numbers: dict[str, list[int]] = {}
        for attempt in self.attempts:
            final_by_task[attempt.task_id] = attempt
            attempt_numbers.setdefault(attempt.task_id, []).append(attempt.attempt)
        for task_id, numbers in attempt_numbers.items():
            if numbers != list(range(1, len(numbers) + 1)):
                raise ValueError(
                    f"provider attempts for {task_id} are not contiguous"
                )
        result_by_task = {
            result.task_id: result for result in self.checkpoint.results
        }
        if set(final_by_task) != set(result_by_task):
            raise ValueError("private trace and checkpoint task sets differ")
        for task_id, result in result_by_task.items():
            if final_by_task[task_id].sample != result:
                raise ValueError(f"final provider attempt for {task_id} is stale")
        expected = canonical_sha256(
            self,
            exclude_fields=("state_fingerprint",),
        )
        if self.state_fingerprint != expected:
            raise ValueError("private run-state fingerprint mismatch")
        return self


class ModelPublicReportV2(StrictModel):
    schema_version: Literal["ori-v2-model-report-v1"] = MODEL_REPORT_SCHEMA_VERSION
    protocol_version: Literal["ori-eval-protocol-v2"] = PROTOCOL_VERSION
    run_identity: RunIdentity
    candidate_release_fingerprint: str
    live_certification_fingerprint: str
    graph_verification_before_fingerprint: str
    graph_verification_after_fingerprint: str
    report: PublicReportV2
    artifact_fingerprint: str

    @model_validator(mode="after")
    def fingerprint_matches(self) -> ModelPublicReportV2:
        expected = canonical_sha256(
            self,
            exclude_fields=("artifact_fingerprint",),
        )
        if self.artifact_fingerprint != expected:
            raise ValueError("model public report fingerprint mismatch")
        return self


class ReadinessTrackV2(StrictModel):
    track: Track
    public_artifact_fingerprint: str
    oracle_artifact_fingerprint: str
    candidate_release_fingerprint: str
    live_certification_fingerprint: str
    capability_profile_fingerprint: str
    graph_verification_fingerprint: str
    task_count: int = Field(strict=True, gt=0)


class ModelReadinessV2(StrictModel):
    name: str
    provider: str
    model: str
    credential_check: str
    capability_check: str


class CampaignReadinessV2(StrictModel):
    schema_version: Literal["ori-v2-run-readiness-v1"] = READINESS_SCHEMA_VERSION
    protocol_version: Literal["ori-eval-protocol-v2"] = PROTOCOL_VERSION
    runner_version: Literal["ori-v2-model-campaign-v2"] = RUNNER_VERSION
    source_config_fingerprint: str
    source_manifest_sha256: str
    archive_sha256: str
    graph_fingerprint: str
    target_fingerprint: str
    mcp_server_revision: str
    tracks: tuple[ReadinessTrackV2, ...]
    models: tuple[ModelReadinessV2, ...]
    model_count: int = Field(strict=True, gt=0)
    readiness_fingerprint: str

    @model_validator(mode="after")
    def fingerprint_matches(self) -> CampaignReadinessV2:
        if self.model_count != len(self.models):
            raise ValueError("readiness model count does not match model receipts")
        expected = canonical_sha256(
            self,
            exclude_fields=("readiness_fingerprint",),
        )
        if self.readiness_fingerprint != expected:
            raise ValueError("campaign readiness fingerprint mismatch")
        return self


@dataclass(frozen=True)
class PreparedTrack:
    track: Track
    pair: V2ArtifactPair
    profile: CapabilityProfile
    release: CatalogRelease
    live: LiveCertificationCatalog
    certifications: Mapping[str, TaskCertification]

    @property
    def task_ids(self) -> tuple[str, ...]:
        return tuple(entry.task_id for entry in self.release.entries)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def _write_model(path: Path, model: Any) -> None:
    _atomic_write(path, model.model_dump(mode="json"))


def _load_release(path: Path) -> CatalogRelease:
    return CatalogRelease.model_validate_json(path.read_text())


def _load_live(path: Path) -> LiveCertificationCatalog:
    return LiveCertificationCatalog.model_validate_json(path.read_text())


def _prepare_track(
    resolved: ResolvedV2CampaignConfig,
    track: Track,
    snapshot: GraphSnapshot,
) -> PreparedTrack:
    paths = resolved.tracks[track]
    pair = load_v2_pair(paths.public, paths.oracles)
    profile = capability_profile_for_track(track)
    release = _load_release(paths.candidates)
    live = _load_live(paths.live_certification)
    mismatches: list[str] = []
    if pair.public.track is not track:
        mismatches.append("public track")
    if pair.public.product != snapshot.product:
        mismatches.append("product")
    if pair.public.graph_fingerprint != snapshot.graph_fingerprint:
        mismatches.append("public graph")
    if release != live.candidate_catalog:
        mismatches.append("candidate/live catalog")
    if live.track is not track:
        mismatches.append("live certification track")
    if live.graph_fingerprint != snapshot.graph_fingerprint:
        mismatches.append("live certification graph")
    if release.graph_fingerprint != snapshot.graph_fingerprint:
        mismatches.append("candidate graph")
    if release.compiler_fingerprint != pair.public.compiler_fingerprint:
        mismatches.append("compiler")
    if release.comparator_fingerprint != pair.public.comparator_fingerprint:
        mismatches.append("comparator")
    if release.capability_profile_fingerprint != profile.profile_fingerprint:
        mismatches.append("capability profile")
    if live.capability_profile_fingerprint != profile.profile_fingerprint:
        mismatches.append("live capability profile")

    task_by_id = {task.task_id: task for task in pair.public.tasks}
    oracle_by_id = {oracle.task_id: oracle for oracle in pair.private.oracles}
    certification_by_id = {
        certification.task_id: certification
        for certification in live.certifications
    }
    entry_ids = [entry.task_id for entry in release.entries]
    if len(entry_ids) != len(set(entry_ids)):
        mismatches.append("duplicate candidate IDs")
    if set(entry_ids) != set(task_by_id):
        mismatches.append("candidate/public task set")
    if set(entry_ids) != set(oracle_by_id):
        mismatches.append("candidate/oracle task set")
    if set(entry_ids) != set(certification_by_id):
        mismatches.append("candidate/certification task set")
    for entry in release.entries:
        task = task_by_id.get(entry.task_id)
        oracle = oracle_by_id.get(entry.task_id)
        certification = certification_by_id.get(entry.task_id)
        if task is None or oracle is None or certification is None:
            continue
        if entry.track is not track:
            mismatches.append(f"{entry.task_id} entry track")
        if entry.task_fingerprint != task.task_fingerprint:
            mismatches.append(f"{entry.task_id} task")
        if entry.oracle_fingerprint != oracle.oracle_fingerprint:
            mismatches.append(f"{entry.task_id} oracle")
        if entry.certification_fingerprint != certification.certification_fingerprint:
            mismatches.append(f"{entry.task_id} certification")
        if certification.state is not CertificationState.CANDIDATE:
            mismatches.append(f"{entry.task_id} state")
    if mismatches:
        raise V2CampaignRunError(
            f"{track.value} candidate certification mismatch: "
            + ", ".join(sorted(set(mismatches)))
        )
    return PreparedTrack(
        track=track,
        pair=pair,
        profile=profile,
        release=release,
        live=live,
        certifications=certification_by_id,
    )


def _git_revision(path: Path) -> str:
    try:
        revision = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(path), "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except subprocess.CalledProcessError as exc:
        raise V2CampaignRunError(f"cannot inspect MCP checkout: {exc}") from exc
    if dirty:
        raise V2CampaignRunError("certified v2 MCP checkout must be clean")
    return revision


def _codex_model_slug(model: V2ModelEntry) -> str:
    slug = model.model.split("/", 1)[1] if model.model.startswith("codex/") else model.model
    return slug.split("@", 1)[0]


def _model_readiness(
    resolved: ResolvedV2CampaignConfig,
) -> tuple[ModelReadinessV2, ...]:
    """Verify local credentials/configuration without invoking a model."""

    receipts: list[ModelReadinessV2] = []
    codex_status_checked = False
    codex_slugs: set[str] | None = None
    for model in resolved.config.models:
        if model.provider == "codex":
            if not codex_status_checked:
                try:
                    status = subprocess.run(
                        ["codex", "login", "status"],
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                except (FileNotFoundError, subprocess.CalledProcessError) as exc:
                    raise V2CampaignRunError(
                        "Codex OAuth readiness failed; run `codex login`"
                    ) from exc
                if "logged in" not in (status.stdout + status.stderr).casefold():
                    raise V2CampaignRunError(
                        "Codex OAuth readiness failed; run `codex login`"
                    )
                cache_path = Path.home() / ".codex" / "models_cache.json"
                try:
                    cache = json.loads(cache_path.read_text())
                    codex_slugs = {
                        str(item.get("slug") or "")
                        for item in cache.get("models", ())
                        if isinstance(item, Mapping)
                    }
                except (FileNotFoundError, json.JSONDecodeError, AttributeError) as exc:
                    raise V2CampaignRunError(
                        "Codex model cache is unavailable; run Codex once to refresh it"
                    ) from exc
                codex_status_checked = True
            slug = _codex_model_slug(model)
            if codex_slugs is None or slug not in codex_slugs:
                raise V2CampaignRunError(
                    f"Codex model {slug!r} is absent from ~/.codex/models_cache.json"
                )
            receipts.append(
                ModelReadinessV2(
                    name=model.name,
                    provider=model.provider,
                    model=slug,
                    credential_check="codex-login-status",
                    capability_check="codex-model-cache",
                )
            )
            continue

        required_key = {
            "anthropic": "ANTHROPIC_API_KEY",
            "gemini": "GEMINI_API_KEY",
            "openai": "OPENAI_API_KEY",
        }.get(model.provider)
        if required_key is not None and not os.getenv(required_key):
            raise V2CampaignRunError(
                f"model {model.name} requires {required_key}"
            )
        if model.provider == "openai-compat":
            base_url = (
                model.model_base_url
                or resolved.config.defaults.model_base_url
                or os.getenv("OPENAI_COMPAT_BASE_URL")
            )
            if not base_url and "@" not in model.model:
                raise V2CampaignRunError(
                    f"model {model.name} requires an OpenAI-compatible base URL"
                )
        receipts.append(
            ModelReadinessV2(
                name=model.name,
                provider=model.provider,
                model=model.model,
                credential_check=(
                    required_key or "provider-does-not-require-a-key"
                ),
                capability_check="configured-not-probed",
            )
        )
    return tuple(receipts)


def _model_loop(
    model: V2ModelEntry,
    resolved: ResolvedV2CampaignConfig,
) -> MCPToolLoop:
    raw = model.mcp_tool_loop or resolved.config.defaults.mcp.tool_loop
    loop = MCPToolLoop(raw)
    if loop is MCPToolLoop.AUTO:
        raise V2CampaignRunError("certified v2 campaigns forbid MCP tool_loop=auto")
    if loop is MCPToolLoop.INSPECT:
        raise V2CampaignRunError(
            "Inspect-backed v2 model campaigns are not enabled by run-v2"
        )
    if model.provider == "ollama" and loop is not MCPToolLoop.NATIVE_OLLAMA:
        raise V2CampaignRunError(
            f"model {model.name} requires native-ollama MCP loop"
        )
    if model.provider != "ollama" and loop is MCPToolLoop.NATIVE_OLLAMA:
        raise V2CampaignRunError(
            f"model {model.name} cannot use native-ollama MCP loop"
        )
    return loop


def _validate_model_bindings(
    resolved: ResolvedV2CampaignConfig,
    prepared: Mapping[Track, PreparedTrack],
) -> None:
    if Track.MCP not in prepared:
        return
    for model in resolved.config.models:
        loop = _model_loop(model, resolved)
        mismatched = [
            task.task_id
            for task in prepared[Track.MCP].pair.public.tasks
            if task.binding.mcp_tool_loop != loop.value
        ]
        if mismatched:
            raise V2CampaignRunError(
                f"model {model.name} loop {loop.value!r} does not match "
                f"the certified MCP catalog binding ({len(mismatched)} tasks)"
            )


def prepare_v2_campaign(
    config_path: Path,
) -> tuple[
    ResolvedV2CampaignConfig,
    GraphSnapshot,
    dict[Track, PreparedTrack],
    str,
    tuple[ModelReadinessV2, ...],
]:
    """Load every immutable artifact and reject mismatches before network/model work."""

    resolved = load_v2_campaign_config(config_path)
    manifest = json.loads(resolved.source_manifest.read_text())
    metadata = manifest.get("metadata") or {}
    snapshot = build_archive_snapshot(
        resolved.archive,
        manifest,
        product=str(
            metadata.get("benchmark")
            or metadata.get("benchmark_name")
            or metadata.get("generator_profile")
            or ""
        ),
    )
    prepared = {
        track: _prepare_track(resolved, track, snapshot)
        for track in resolved.config.track_modes
    }
    _validate_model_bindings(resolved, prepared)
    model_readiness = _model_readiness(resolved)
    mcp_revision = _git_revision(resolved.mcp_dir)
    if Track.MCP in prepared:
        expected_revision = prepared[Track.MCP].profile.mcp_server_revision
        if mcp_revision != expected_revision:
            raise V2CampaignRunError(
                "MCP checkout revision mismatch: "
                f"expected={expected_revision} actual={mcp_revision}"
            )
    return resolved, snapshot, prepared, mcp_revision, model_readiness


async def _health_and_graph(
    resolved: ResolvedV2CampaignConfig,
    snapshot: GraphSnapshot,
    bhce: BHCEClient,
) -> tuple[GraphSnapshot, LiveGraphVerification]:
    health = await bhce.wait_until_healthy(
        timeout_seconds=resolved.config.defaults.health.timeout_seconds,
        poll_interval=resolved.config.defaults.health.poll_interval,
    )
    if not health.ok:
        raise V2CampaignRunError(
            "BloodHound health failed before v2 model work: "
            f"{health.detail} ({health.classification})"
        )
    observed, receipt = await collect_live_snapshot(
        bhce,
        snapshot,
        page_size=resolved.config.defaults.graph_page_size,
    )
    require_live_graph_match(snapshot, observed)
    return observed, receipt


def _readiness(
    *,
    resolved: ResolvedV2CampaignConfig,
    snapshot: GraphSnapshot,
    prepared: Mapping[Track, PreparedTrack],
    receipts: Mapping[Track, LiveGraphVerification],
    mcp_revision: str,
    model_readiness: tuple[ModelReadinessV2, ...],
) -> CampaignReadinessV2:
    payload = {
        "source_config_fingerprint": resolved.source_config_fingerprint,
        "source_manifest_sha256": _sha256(resolved.source_manifest),
        "archive_sha256": _sha256(resolved.archive),
        "graph_fingerprint": snapshot.graph_fingerprint,
        "target_fingerprint": canonical_sha256(
            resolve_bhce_target(resolved.config.defaults.bhce_url)
        ),
        "mcp_server_revision": mcp_revision,
        "tracks": tuple(
            ReadinessTrackV2(
                track=track,
                public_artifact_fingerprint=item.pair.public.artifact_fingerprint,
                oracle_artifact_fingerprint=item.pair.private.artifact_fingerprint,
                candidate_release_fingerprint=item.release.release_fingerprint,
                live_certification_fingerprint=item.live.artifact_fingerprint,
                capability_profile_fingerprint=item.profile.profile_fingerprint,
                graph_verification_fingerprint=receipts[
                    track
                ].verification_fingerprint,
                task_count=len(item.release.entries),
            )
            for track, item in prepared.items()
        ),
        "models": model_readiness,
        "model_count": len(model_readiness),
        "readiness_fingerprint": "0" * 64,
    }
    payload["readiness_fingerprint"] = canonical_sha256(
        {
            "schema_version": READINESS_SCHEMA_VERSION,
            "protocol_version": PROTOCOL_VERSION,
            "runner_version": RUNNER_VERSION,
            **payload,
        },
        exclude_fields=("readiness_fingerprint",),
    )
    return CampaignReadinessV2.model_validate(payload)


async def preflight_v2_campaign(config_path: Path) -> CampaignReadinessV2:
    """Perform exact artifact, capability, health, and live-graph gates only."""

    (
        resolved,
        snapshot,
        prepared,
        mcp_revision,
        model_readiness,
    ) = prepare_v2_campaign(config_path)
    receipts: dict[Track, LiveGraphVerification] = {}
    async with BHCEClient(
        **parse_bhce_url(resolved.config.defaults.bhce_url)
    ) as bhce:
        for track in resolved.config.track_modes:
            _observed, receipts[track] = await _health_and_graph(
                resolved,
                snapshot,
                bhce,
            )
    readiness = _readiness(
        resolved=resolved,
        snapshot=snapshot,
        prepared=prepared,
        receipts=receipts,
        mcp_revision=mcp_revision,
        model_readiness=model_readiness,
    )
    _write_model(
        resolved.output_dir / "v2-run-readiness.private.json",
        readiness,
    )
    return readiness


def _provenance(
    *,
    resolved: ResolvedV2CampaignConfig,
    prepared: PreparedTrack,
    model: V2ModelEntry,
    run_index: int,
    loop: MCPToolLoop | None,
) -> ModelRunProvenanceV2:
    run_identity = RunIdentity(
        provider=model.provider,
        model=model.model,
        run_index=run_index,
        target_fingerprint=canonical_sha256(
            resolve_bhce_target(resolved.config.defaults.bhce_url)
        ),
        tool_loop=loop.value if loop is not None else None,
    )
    direct_config = DirectQuerySafetyConfig()
    payload = {
        "base": build_run_provenance(prepared.pair, prepared.profile),
        "run_identity": run_identity,
        "source_manifest_sha256": _sha256(resolved.source_manifest),
        "archive_sha256": _sha256(resolved.archive),
        "candidate_release_fingerprint": prepared.release.release_fingerprint,
        "live_certification_fingerprint": prepared.live.artifact_fingerprint,
        "containment_config_fingerprint": canonical_sha256(
            direct_config.to_jsonable()
        ),
        "runtime_implementation_fingerprint": (
            RUNNER_IMPLEMENTATION_FINGERPRINT
        ),
        "runtime_config_fingerprint": canonical_sha256(
            {
                "runner_version": RUNNER_VERSION,
                "source_config_fingerprint": resolved.source_config_fingerprint,
                "model": model.model_dump(mode="json"),
                "track": prepared.track,
                "loop": loop,
            }
        ),
        "provenance_fingerprint": "0" * 64,
    }
    payload["provenance_fingerprint"] = canonical_sha256(
        {
            "schema_version": RUNNER_VERSION,
            "protocol_version": PROTOCOL_VERSION,
            **payload,
        },
        exclude_fields=("provenance_fingerprint",),
    )
    return ModelRunProvenanceV2.model_validate(payload)


def _guard_run_dir(path: Path, provenance: ModelRunProvenanceV2) -> None:
    path.mkdir(parents=True, exist_ok=True)
    guard = path / "campaign-provenance-v2.json"
    if guard.exists():
        existing = ModelRunProvenanceV2.model_validate_json(guard.read_text())
        if existing != provenance:
            raise V2CampaignRunError(
                f"{path} contains incompatible v2 campaign provenance"
            )
        return
    entries = tuple(path.iterdir())
    if entries:
        raise V2CampaignRunError(
            f"{path} is non-empty and has no v2 provenance guard"
        )
    _write_model(guard, provenance)


def _attempt(
    task_id: str,
    number: int,
    sample: SampleResult,
    provider: ProviderRunRecord,
) -> ProviderAttemptV2:
    payload = {
        "task_id": task_id,
        "attempt": number,
        "sample": sample,
        "provider": provider,
        "attempt_fingerprint": "0" * 64,
    }
    payload["attempt_fingerprint"] = canonical_sha256(
        payload,
        exclude_fields=("attempt_fingerprint",),
    )
    return ProviderAttemptV2.model_validate(payload)


def _state(
    *,
    provenance: ModelRunProvenanceV2,
    checkpoint: CheckpointV2,
    attempts: Sequence[ProviderAttemptV2],
) -> PrivateRunStateV2:
    payload = {
        "provenance_fingerprint": provenance.provenance_fingerprint,
        "checkpoint": checkpoint,
        "attempts": tuple(attempts),
        "state_fingerprint": "0" * 64,
    }
    payload["state_fingerprint"] = canonical_sha256(
        {
            "schema_version": RUN_STATE_SCHEMA_VERSION,
            "protocol_version": PROTOCOL_VERSION,
            **payload,
        },
        exclude_fields=("state_fingerprint",),
    )
    return PrivateRunStateV2.model_validate(payload)


def _load_state(
    path: Path,
    *,
    provenance: ModelRunProvenanceV2,
    prepared: PreparedTrack,
) -> PrivateRunStateV2 | None:
    if not path.exists():
        return None
    state = PrivateRunStateV2.model_validate_json(path.read_text())
    if state.provenance_fingerprint != provenance.provenance_fingerprint:
        raise V2CampaignRunError("private run state belongs to different provenance")
    validate_checkpoint(
        state.checkpoint,
        prepared.pair,
        prepared.profile,
        provenance.run_identity,
    )
    return state


def _model_report(
    *,
    provenance: ModelRunProvenanceV2,
    prepared: PreparedTrack,
    results: Sequence[SampleResult],
    before: LiveGraphVerification,
    after: LiveGraphVerification,
) -> ModelPublicReportV2:
    summary = summarize_results(prepared.task_ids, results)
    report = build_public_report(
        prepared.pair,
        prepared.profile,
        results,
        summary,
        certifications=prepared.certifications,
    )
    payload = {
        "run_identity": provenance.run_identity,
        "candidate_release_fingerprint": prepared.release.release_fingerprint,
        "live_certification_fingerprint": prepared.live.artifact_fingerprint,
        "graph_verification_before_fingerprint": before.verification_fingerprint,
        "graph_verification_after_fingerprint": after.verification_fingerprint,
        "report": report,
        "artifact_fingerprint": "0" * 64,
    }
    payload["artifact_fingerprint"] = canonical_sha256(
        {
            "schema_version": MODEL_REPORT_SCHEMA_VERSION,
            "protocol_version": PROTOCOL_VERSION,
            **payload,
        },
        exclude_fields=("artifact_fingerprint",),
    )
    return ModelPublicReportV2.model_validate(payload)


async def _run_model(
    *,
    resolved: ResolvedV2CampaignConfig,
    prepared: PreparedTrack,
    model: V2ModelEntry,
    run_index: int,
    bhce: BHCEClient,
    coordinator: DirectQueryCoordinator,
    loop: MCPToolLoop | None,
    runs_total: int,
    progress: ProgressReporter | None = None,
) -> tuple[ModelRunProvenanceV2, tuple[SampleResult, ...]]:
    provenance = _provenance(
        resolved=resolved,
        prepared=prepared,
        model=model,
        run_index=run_index,
        loop=loop,
    )
    run_dir = (
        resolved.output_dir
        / prepared.track.value
        / model.name
        / f"run-{run_index:03d}"
    )
    _guard_run_dir(run_dir, provenance)
    state_path = run_dir / "run-state-v2.private.json"
    state = _load_state(
        state_path,
        provenance=provenance,
        prepared=prepared,
    )
    results = list(state.checkpoint.results if state is not None else ())
    attempts = list(state.attempts if state is not None else ())
    retryable_execution_classes = {
        ExecutionClass.INFRA_FAILURE,
        ExecutionClass.UNEXECUTED,
    }
    completed = {
        result.task_id
        for result in results
        if result.execution_class not in retryable_execution_classes
    }
    _emit_progress(
        progress,
        (
            f"\n[{prepared.track.value}] {model.name} -> "
            f"{model.requested_model} run {run_index}/{runs_total} "
            f"({len(prepared.task_ids)} tasks, {len(completed)} resumed)"
        ),
    )

    bundle = None
    if prepared.track is Track.MCP:
        bundle = await _load_bloodhound_mcp_bundle(
            resolved.mcp_dir,
            include_resources=False,
            include_prompt=True,
            cypher_executor=coordinator.execute,
        )

    task_by_id = {
        task.task_id: task for task in prepared.pair.public.tasks
    }
    registry = OracleRegistry(prepared.pair.private)
    resolver = IdentityResolver(prepared.pair.private.identity_catalog)
    for task_index, task_id in enumerate(prepared.task_ids, start=1):
        if task_id in completed:
            continue
        task = task_by_id[task_id]
        oracle = registry.for_task(task_id)
        previous_attempt_number = max(
            (
                attempt.attempt
                for attempt in attempts
                if attempt.task_id == task_id
            ),
            default=0,
        )
        results = [
            result for result in results if result.task_id != task_id
        ]
        task_started = time.monotonic()
        _emit_progress(
            progress,
            (
                f"  [{task_index}/{len(prepared.task_ids)}] {task.task_id} "
                f"({task.claim_kind}, {task.answer_policy.kind})"
            ),
        )
        last_sample: SampleResult | None = None
        last_provider: ProviderRunRecord | None = None
        for attempt_index in range(
            1,
            resolved.config.defaults.max_infra_retries + 2,
        ):
            attempt_number = previous_attempt_number + attempt_index
            model_base_url = (
                model.model_base_url
                if model.model_base_url is not None
                else resolved.config.defaults.model_base_url
            )
            if prepared.track is Track.DIRECT:
                _outcome, sample, provider = await run_direct_model_task_v2(
                    coordinator=coordinator,
                    task=task,
                    oracle=oracle,
                    resolver=resolver,
                    model=model.requested_model,
                    model_base_url=model_base_url,
                    ollama_options=model.options,
                )
            else:
                if bundle is None or loop is None:
                    raise AssertionError("MCP model run is missing its runtime bundle")
                outcome, provider = await run_mcp_model_task_v2(
                    task=task,
                    oracle=oracle,
                    resolver=resolver,
                    profile=prepared.profile,
                    bundle=bundle,
                    model=model.requested_model,
                    model_base_url=model_base_url,
                    tool_loop=loop,
                    max_steps=resolved.config.defaults.mcp.max_steps,
                    ollama_options=model.options,
                    telemetry_adapter=resolved.config.defaults.mcp.telemetry_adapter,
                    read_timeout_seconds=(
                        resolved.config.defaults.mcp.read_timeout_seconds
                    ),
                )
                sample = outcome.sample
            attempts.append(_attempt(task_id, attempt_number, sample, provider))
            last_sample = sample
            last_provider = provider
            if sample.execution_class.value != "infra_failure":
                break
            health = await bhce.wait_until_healthy(
                timeout_seconds=resolved.config.defaults.health.timeout_seconds,
                poll_interval=resolved.config.defaults.health.poll_interval,
            )
            if not health.ok:
                break
            coordinator.close_circuit()
            _emit_progress(
                progress,
                _infrastructure_attempt_progress(
                    sample=sample,
                    attempt_number=attempt_number,
                    attempt_index=attempt_index,
                    max_infra_retries=(
                        resolved.config.defaults.max_infra_retries
                    ),
                ),
            )

        if last_sample is None or last_provider is None:
            raise AssertionError("v2 task loop produced no terminal attempt")
        results.append(last_sample)
        checkpoint = build_checkpoint(
            prepared.pair,
            prepared.profile,
            provenance.run_identity,
            results=results,
        )
        current_state = _state(
            provenance=provenance,
            checkpoint=checkpoint,
            attempts=attempts,
        )
        _write_model(state_path, current_state)
        _emit_progress(
            progress,
            _task_completion_progress(
                sample=last_sample,
                provider=last_provider,
                task_elapsed_seconds=time.monotonic() - task_started,
                results=results,
            ),
        )

    summary = summarize_results(prepared.task_ids, results)
    _write_model(
        run_dir / "campaign-summary-v2.private.json",
        summary,
    )
    _emit_progress(
        progress,
        (
            f"[{prepared.track.value}] {model.name} run {run_index}/{runs_total} "
            f"complete: correct={summary.correct}/{summary.correct + summary.incorrect}, "
            f"infra={summary.infrastructure_failures}, "
            f"campaign_valid={summary.campaign_valid}"
        ),
    )
    return provenance, tuple(results)


async def run_v2_campaign(
    config_path: Path,
    *,
    preflight_only: bool = False,
    progress: ProgressReporter | None = None,
) -> CampaignReadinessV2:
    """Run exact V2 candidate catalogs, or stop after readiness when requested."""

    _emit_progress(progress, "V2 CAMPAIGN: validating sealed artifacts and capabilities")
    (
        resolved,
        snapshot,
        prepared,
        mcp_revision,
        model_readiness,
    ) = prepare_v2_campaign(config_path)
    _emit_progress(
        progress,
        (
            "V2 CAMPAIGN: artifact readiness passed "
            f"({len(resolved.config.models)} models, "
            f"{len(resolved.config.track_modes)} tracks)"
        ),
    )
    receipts_before: dict[Track, LiveGraphVerification] = {}
    receipts_after: dict[Track, LiveGraphVerification] = {}
    pending_reports: list[
        tuple[
            ModelRunProvenanceV2,
            PreparedTrack,
            tuple[SampleResult, ...],
            Path,
        ]
    ] = []

    async with BHCEClient(
        **parse_bhce_url(resolved.config.defaults.bhce_url)
    ) as bhce:
        direct_config = DirectQuerySafetyConfig()
        coordinator = DirectQueryCoordinator(
            bhce=bhce,
            config=direct_config,
            deny_cache=QueryDenyCache(
                (
                    resolved.output_dir
                    / "direct-query-deny-cache-v3.private.json"
                ),
                manifest_fingerprint=_sha256(resolved.source_manifest),
                policy_version=direct_config.policy_version,
            ),
        )
        for track in resolved.config.track_modes:
            _emit_progress(
                progress,
                f"[{track.value}] verifying BloodHound graph before track",
            )
            _observed, before = await _health_and_graph(
                resolved,
                snapshot,
                bhce,
            )
            receipts_before[track] = before
            track_dir = resolved.output_dir / track.value
            _write_model(
                track_dir / "graph-verification-before-v2.private.json",
                before,
            )
            _emit_progress(
                progress,
                _graph_verification_progress(
                    track=track,
                    stage="pre",
                    receipt=before,
                ),
            )
            if not preflight_only:
                for model in resolved.config.models:
                    loop = (
                        _model_loop(model, resolved)
                        if track is Track.MCP
                        else None
                    )
                    runs = (
                        model.runs_per_model
                        if model.runs_per_model is not None
                        else resolved.config.defaults.runs_per_model
                    )
                    for run_index in range(1, runs + 1):
                        provenance, results = await _run_model(
                            resolved=resolved,
                            prepared=prepared[track],
                            model=model,
                            run_index=run_index,
                            bhce=bhce,
                            coordinator=coordinator,
                            loop=loop,
                            runs_total=runs,
                            progress=progress,
                        )
                        run_dir = (
                            resolved.output_dir
                            / track.value
                            / model.name
                            / f"run-{run_index:03d}"
                        )
                        pending_reports.append(
                            (
                                provenance,
                                prepared[track],
                                results,
                                run_dir,
                            )
                        )
            _emit_progress(
                progress,
                f"[{track.value}] verifying BloodHound graph after track",
            )
            _observed, after = await _health_and_graph(
                resolved,
                snapshot,
                bhce,
            )
            receipts_after[track] = after
            _write_model(
                track_dir / "graph-verification-after-v2.private.json",
                after,
            )
            _emit_progress(
                progress,
                _graph_verification_progress(
                    track=track,
                    stage="post",
                    receipt=after,
                ),
            )

    readiness = _readiness(
        resolved=resolved,
        snapshot=snapshot,
        prepared=prepared,
        receipts=receipts_before,
        mcp_revision=mcp_revision,
        model_readiness=model_readiness,
    )
    _write_model(
        resolved.output_dir / "v2-run-readiness.private.json",
        readiness,
    )
    if preflight_only:
        _emit_progress(progress, "V2 CAMPAIGN: readiness checks complete")
        return readiness

    invalid_campaigns: list[str] = []
    for provenance, item, results, run_dir in pending_reports:
        report = _model_report(
            provenance=provenance,
            prepared=item,
            results=results,
            before=receipts_before[item.track],
            after=receipts_after[item.track],
        )
        _write_model(run_dir / "public-report-v2.json", report)
        if not report.report.summary.campaign_valid:
            invalid_campaigns.append(
                f"{item.track.value}/{provenance.run_identity.model}/"
                f"run-{provenance.run_identity.run_index:03d}:"
                + ",".join(report.report.summary.invalid_reasons)
            )
    if invalid_campaigns:
        raise V2CampaignRunError(
            "v2 campaign completed with invalid execution accounting: "
            + "; ".join(invalid_campaigns)
        )
    _emit_progress(progress, "V2 CAMPAIGN: all model runs and reports complete")
    return readiness
