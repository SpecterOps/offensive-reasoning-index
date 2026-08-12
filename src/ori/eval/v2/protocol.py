"""Explicit v1/v2 dispatch and sealed artifact boundaries."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from .comparator import COMPARATOR_FINGERPRINT
from .compiler import CompiledCorpus
from .fingerprint import canonical_sha256
from .schema import (
    MANIFEST_SCHEMA_VERSION,
    PROTOCOL_VERSION,
    EntityRef,
    GraphFactRegistry,
    OracleBundle,
    StrictModel,
    TaskBundle,
    Track,
)

ORACLE_ARTIFACT_VERSION = "ori-eval-oracle-v2"
CERTIFICATION_ARTIFACT_VERSION = "ori-eval-certification-v2"

_PUBLIC_FORBIDDEN_FIELDS = frozenset(
    {
        "correct",
        "expected_answer",
        "expected_count",
        "expected_decision",
        "expected_entities",
        "forbidden_edges",
        "forbidden_entity_ids",
        "graph_edge_registry",
        "graph_fact_attestation",
        "graph_fact_registry",
        "graph_fact_registry_fingerprint",
        "negative_witnesses",
        "oracle",
        "oracle_bundle",
        "oracle_fingerprint",
        "oracle_id",
        "reference_cypher",
        "reference_nodes",
        "reference_results",
        "ref_result",
        "route_variants",
        "valid_node_names",
    }
)


class ProtocolDispatchError(ValueError):
    """Raised for mixed, unknown, or misrouted protocol artifacts."""


class PublicV2Artifact(StrictModel):
    schema_version: Literal["ori-generated-manifest-v3"] = MANIFEST_SCHEMA_VERSION
    protocol_version: Literal["ori-eval-protocol-v2"] = PROTOCOL_VERSION
    product: str
    track: Track
    seed: int = Field(strict=True)
    source_manifest_fingerprint: str
    graph_fingerprint: str
    graph_object_count: int = Field(strict=True, gt=0)
    compiler_fingerprint: str
    comparator_fingerprint: str
    tasks: tuple[TaskBundle, ...]
    catalog_fingerprint: str
    artifact_fingerprint: str

    @model_validator(mode="after")
    def fingerprints_match(self) -> PublicV2Artifact:
        expected_catalog = canonical_sha256(self.tasks)
        if self.catalog_fingerprint != expected_catalog:
            raise ValueError("public catalog fingerprint mismatch")
        expected_artifact = canonical_sha256(
            self,
            exclude_fields=("artifact_fingerprint",),
        )
        if self.artifact_fingerprint != expected_artifact:
            raise ValueError("public artifact fingerprint mismatch")
        _assert_public(self.model_dump(mode="json"))
        return self


class OracleV2Artifact(StrictModel):
    schema_version: Literal["ori-eval-oracle-v2"] = ORACLE_ARTIFACT_VERSION
    protocol_version: Literal["ori-eval-protocol-v2"] = PROTOCOL_VERSION
    product: str
    track: Track
    seed: int = Field(strict=True)
    public_artifact_fingerprint: str
    graph_fingerprint: str
    compiler_fingerprint: str
    comparator_fingerprint: str
    identity_catalog: tuple[EntityRef, ...]
    identity_catalog_fingerprint: str
    graph_fact_registry: GraphFactRegistry
    oracles: tuple[OracleBundle, ...]
    oracle_catalog_fingerprint: str
    artifact_fingerprint: str

    @model_validator(mode="after")
    def fingerprints_match(self) -> OracleV2Artifact:
        object_ids = [entity.object_id for entity in self.identity_catalog]
        if len(object_ids) != len(set(object_ids)):
            raise ValueError("sealed identity catalog contains duplicate object IDs")
        if self.identity_catalog_fingerprint != canonical_sha256(
            self.identity_catalog
        ):
            raise ValueError("sealed identity catalog fingerprint mismatch")
        if any(
            oracle.graph_fact_registry_fingerprint
            != self.graph_fact_registry.registry_fingerprint
            for oracle in self.oracles
        ):
            raise ValueError("oracle graph fact registry fingerprint mismatch")
        expected_catalog = canonical_sha256(self.oracles)
        if self.oracle_catalog_fingerprint != expected_catalog:
            raise ValueError("oracle catalog fingerprint mismatch")
        expected_artifact = canonical_sha256(
            self,
            exclude_fields=("artifact_fingerprint",),
        )
        if self.artifact_fingerprint != expected_artifact:
            raise ValueError("oracle artifact fingerprint mismatch")
        return self


class V2ArtifactPair(StrictModel):
    """One exact public catalog and its separately loaded scorer-only state."""

    public: PublicV2Artifact
    private: OracleV2Artifact

    @model_validator(mode="after")
    def pair_is_exact(self) -> V2ArtifactPair:
        mismatches: list[str] = []
        if self.private.public_artifact_fingerprint != self.public.artifact_fingerprint:
            mismatches.append("public artifact fingerprint")
        if self.private.product != self.public.product:
            mismatches.append("product")
        if self.private.track is not self.public.track:
            mismatches.append("track")
        if self.private.seed != self.public.seed:
            mismatches.append("seed")
        if self.private.graph_fingerprint != self.public.graph_fingerprint:
            mismatches.append("graph")
        if self.private.compiler_fingerprint != self.public.compiler_fingerprint:
            mismatches.append("compiler")
        if (
            self.private.comparator_fingerprint != self.public.comparator_fingerprint
            or self.public.comparator_fingerprint != COMPARATOR_FINGERPRINT
        ):
            mismatches.append("comparator")
        if len(self.private.identity_catalog) != self.public.graph_object_count:
            mismatches.append("identity catalog cardinality")
        if mismatches:
            raise ValueError(
                "public/oracle artifact pair mismatch: " + ", ".join(mismatches)
            )

        public_ids = [task.task_id for task in self.public.tasks]
        oracle_ids = [oracle.task_id for oracle in self.private.oracles]
        if len(public_ids) != len(set(public_ids)):
            raise ValueError("public task catalog contains duplicate task IDs")
        if len(oracle_ids) != len(set(oracle_ids)):
            raise ValueError("oracle catalog contains duplicate task IDs")
        if set(public_ids) != set(oracle_ids):
            raise ValueError(
                "public/oracle task sets differ: "
                f"missing={sorted(set(public_ids) - set(oracle_ids))} "
                f"extra={sorted(set(oracle_ids) - set(public_ids))}"
            )

        oracle_by_task = {oracle.task_id: oracle for oracle in self.private.oracles}
        identity_ids = {entity.object_id for entity in self.private.identity_catalog}
        for task in self.public.tasks:
            if task.binding.track is not self.public.track:
                raise ValueError(f"task {task.task_id} has the wrong track binding")
            oracle = oracle_by_task[task.task_id]
            if oracle.task_fingerprint != task.task_fingerprint:
                raise ValueError(f"task {task.task_id} fingerprint mismatch")
            if oracle.claim_fingerprint != task.claim_fingerprint:
                raise ValueError(f"task {task.task_id} claim fingerprint mismatch")
            if oracle.graph_fingerprint != self.public.graph_fingerprint:
                raise ValueError(f"task {task.task_id} graph fingerprint mismatch")
            referenced_ids = {
                *(entity.object_id for entity in oracle.resolved_roles),
                *(entity.object_id for entity in oracle.expected_entities),
                *(
                    endpoint
                    for edge in (
                        *oracle.graph_edge_registry,
                        *oracle.required_context,
                        *oracle.forbidden_edges,
                    )
                    for endpoint in (edge.source_id, edge.target_id)
                ),
                *(fact.entity_id for fact in oracle.required_properties),
                *oracle.forbidden_entity_ids,
                *(
                    entity_id
                    for witness in oracle.negative_witnesses
                    for entity_id in witness.checked_entity_ids
                ),
            }
            if oracle.source_id is not None:
                referenced_ids.add(oracle.source_id)
            if oracle.target_id is not None:
                referenced_ids.add(oracle.target_id)
            missing_identities = sorted(referenced_ids - identity_ids)
            if missing_identities:
                raise ValueError(
                    f"task {task.task_id} references identities absent from the "
                    f"sealed catalog: {missing_identities}"
                )
        return self


def _assert_public(value: Any, *, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if str(key).casefold() in _PUBLIC_FORBIDDEN_FIELDS:
                raise ProtocolDispatchError(
                    f"scorer-only field reached public artifact at {path}.{key}"
                )
            _assert_public(nested, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            _assert_public(nested, path=f"{path}[{index}]")


def build_artifacts(
    corpus: CompiledCorpus,
    *,
    identity_catalog: Iterable[EntityRef],
) -> tuple[PublicV2Artifact, OracleV2Artifact]:
    """Split one compiled corpus into solver-visible and scorer-only artifacts."""

    tasks = tuple(item.public for item in corpus.tasks)
    public_payload = {
        "product": corpus.product,
        "track": corpus.track,
        "seed": corpus.seed,
        "source_manifest_fingerprint": corpus.source_manifest_fingerprint,
        "graph_fingerprint": corpus.graph_fingerprint,
        "graph_object_count": corpus.graph_object_count,
        "compiler_fingerprint": corpus.compiler_fingerprint,
        "comparator_fingerprint": COMPARATOR_FINGERPRINT,
        "tasks": tasks,
        "catalog_fingerprint": canonical_sha256(tasks),
        "artifact_fingerprint": "0" * 64,
    }
    public_payload["artifact_fingerprint"] = canonical_sha256(
        {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "protocol_version": PROTOCOL_VERSION,
            **public_payload,
        },
        exclude_fields=("artifact_fingerprint",),
    )
    public = PublicV2Artifact.model_validate(public_payload)

    oracles = tuple(item.oracle for item in corpus.tasks)
    known_entities = iter(identity_catalog)
    identities_by_id: dict[str, EntityRef] = {}
    for entity in known_entities:
        existing = identities_by_id.get(entity.object_id)
        if existing is not None:
            existing_identity = (
                existing.object_type,
                existing.domain,
                existing.canonical_name,
            )
            candidate_identity = (
                entity.object_type,
                entity.domain,
                entity.canonical_name,
            )
            if existing_identity != candidate_identity:
                raise ProtocolDispatchError(
                    f"conflicting sealed identity definitions for {entity.object_id}"
                )
            aliases = tuple(sorted({*existing.aliases, *entity.aliases}))
        else:
            aliases = entity.aliases
        identities_by_id[entity.object_id] = EntityRef(
            object_id=entity.object_id,
            object_type=entity.object_type,
            domain=entity.domain,
            role="sealed_identity",
            canonical_name=entity.canonical_name,
            aliases=aliases,
        )
    sealed_identities = tuple(
        identities_by_id[object_id] for object_id in sorted(identities_by_id)
    )
    if len(sealed_identities) != corpus.graph_object_count:
        raise ProtocolDispatchError(
            "sealed identity catalog is incomplete: "
            f"expected={corpus.graph_object_count} actual={len(sealed_identities)}"
        )
    oracle_payload = {
        "product": corpus.product,
        "track": corpus.track,
        "seed": corpus.seed,
        "public_artifact_fingerprint": public.artifact_fingerprint,
        "graph_fingerprint": corpus.graph_fingerprint,
        "compiler_fingerprint": corpus.compiler_fingerprint,
        "comparator_fingerprint": COMPARATOR_FINGERPRINT,
        "identity_catalog": sealed_identities,
        "identity_catalog_fingerprint": canonical_sha256(sealed_identities),
        "graph_fact_registry": corpus.graph_fact_registry,
        "oracles": oracles,
        "oracle_catalog_fingerprint": canonical_sha256(oracles),
        "artifact_fingerprint": "0" * 64,
    }
    oracle_payload["artifact_fingerprint"] = canonical_sha256(
        {
            "schema_version": ORACLE_ARTIFACT_VERSION,
            "protocol_version": PROTOCOL_VERSION,
            **oracle_payload,
        },
        exclude_fields=("artifact_fingerprint",),
    )
    private = OracleV2Artifact.model_validate(oracle_payload)
    return public, private


def _read_json(source: Path | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(source, Path):
        payload = json.loads(source.read_text())
    else:
        payload = source
    if not isinstance(payload, Mapping):
        raise ProtocolDispatchError("protocol artifact must be a JSON object")
    return payload


def detect_protocol(source: Path | Mapping[str, Any]) -> Literal["v1", "v2"]:
    payload = _read_json(source)
    schema_version = payload.get("schema_version")
    protocol_version = payload.get("protocol_version")
    if (
        schema_version in {MANIFEST_SCHEMA_VERSION, ORACLE_ARTIFACT_VERSION}
        and protocol_version == PROTOCOL_VERSION
    ):
        return "v2"
    if schema_version == "ori-generated-manifest-v2" and protocol_version is None:
        return "v1"
    raise ProtocolDispatchError(
        f"unknown or mixed protocol: schema={schema_version!r} "
        f"protocol={protocol_version!r}"
    )


def load_public_v2(source: Path | Mapping[str, Any]) -> PublicV2Artifact:
    payload = _read_json(source)
    if detect_protocol(payload) != "v2" or payload.get("schema_version") != (
        MANIFEST_SCHEMA_VERSION
    ):
        raise ProtocolDispatchError("artifact is not a public v2 manifest")
    return PublicV2Artifact.model_validate_json(json.dumps(payload))


def load_oracles_v2(source: Path | Mapping[str, Any]) -> OracleV2Artifact:
    payload = _read_json(source)
    if detect_protocol(payload) != "v2" or payload.get("schema_version") != (
        ORACLE_ARTIFACT_VERSION
    ):
        raise ProtocolDispatchError("artifact is not a sealed v2 oracle catalog")
    return OracleV2Artifact.model_validate_json(json.dumps(payload))


def load_v2_pair(
    public_source: Path | Mapping[str, Any],
    oracle_source: Path | Mapping[str, Any],
) -> V2ArtifactPair:
    """Load and cross-check an exact public/scorer artifact pair."""

    return V2ArtifactPair(
        public=load_public_v2(public_source),
        private=load_oracles_v2(oracle_source),
    )


class OracleRegistry:
    """Scorer-side lookup that never exposes oracle IDs to solver artifacts."""

    def __init__(self, artifact: OracleV2Artifact) -> None:
        self._by_id: dict[str, OracleBundle] = {}
        self._by_task: dict[str, OracleBundle] = {}
        for oracle in artifact.oracles:
            if oracle.oracle_id in self._by_id:
                raise ProtocolDispatchError(f"duplicate oracle ID: {oracle.oracle_id}")
            if oracle.task_id in self._by_task:
                raise ProtocolDispatchError(
                    f"duplicate oracle task binding: {oracle.task_id}"
                )
            self._by_id[oracle.oracle_id] = oracle
            self._by_task[oracle.task_id] = oracle

    def get(self, oracle_id: str, oracle_fingerprint: str) -> OracleBundle:
        try:
            oracle = self._by_id[oracle_id]
        except KeyError as exc:
            raise ProtocolDispatchError(f"unknown oracle ID: {oracle_id}") from exc
        if oracle.oracle_fingerprint != oracle_fingerprint:
            raise ProtocolDispatchError("oracle fingerprint mismatch")
        return oracle

    def for_task(self, task_id: str) -> OracleBundle:
        try:
            return self._by_task[task_id]
        except KeyError as exc:
            raise ProtocolDispatchError(
                f"no sealed oracle for task: {task_id}"
            ) from exc


def write_artifacts(
    corpus: CompiledCorpus,
    *,
    public_path: Path,
    oracle_path: Path,
    identity_catalog: Iterable[EntityRef],
) -> tuple[PublicV2Artifact, OracleV2Artifact]:
    """Write physically separated public and scorer-only JSON artifacts."""

    if public_path.resolve() == oracle_path.resolve():
        raise ProtocolDispatchError("public and oracle artifacts require separate paths")
    public, private = build_artifacts(corpus, identity_catalog=identity_catalog)
    public_path.parent.mkdir(parents=True, exist_ok=True)
    oracle_path.parent.mkdir(parents=True, exist_ok=True)
    public_path.write_text(
        json.dumps(public.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
    )
    oracle_path.write_text(
        json.dumps(private.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
    )
    return public, private
