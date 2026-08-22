"""Bounded, identity-aware grading for compiled discovery campaigns."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from ori.eval.v2.fingerprint import canonical_sha256
from ori.eval.v2.graph import edge_fact_key
from ori.eval.v2.identity import IdentityResolutionError, IdentityResolver
from ori.eval.v2.schema import EdgeWitness
from ori.relationships import (
    LEGACY_SEMANTIC_ALIASES,
    LEGACY_SPELLING_ALIASES,
    RELATIONSHIP_CONTRACTS,
    canonical_relationship_kind,
)

from .compiler import iter_truth_variants
from .schema import (
    DiscoveryFinding,
    DiscoveryFindingResult,
    DiscoveryPrivateArtifact,
    DiscoveryPublicArtifact,
    DiscoveryScoreReport,
    DiscoverySubmission,
)


class DiscoverySubmissionError(ValueError):
    """Raised when an answer cannot cross the bounded submission boundary."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DiscoverySubmissionError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _load_json_bytes(raw: bytes, *, max_bytes: int) -> Mapping[str, Any]:
    if len(raw) > max_bytes:
        raise DiscoverySubmissionError(
            f"submission is {len(raw)} bytes; maximum is {max_bytes}"
        )
    try:
        value = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except DiscoverySubmissionError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DiscoverySubmissionError(f"submission is not valid JSON: {exc}") from exc
    if not isinstance(value, Mapping):
        raise DiscoverySubmissionError("submission root must be a JSON object")
    return value


def _canonical_relationship(value: str) -> str:
    try:
        return canonical_relationship_kind(value)
    except ValueError:
        folded = value.strip().casefold()
        lookup = {
            **{key.casefold(): key for key in RELATIONSHIP_CONTRACTS},
            **{
                key.casefold(): canonical
                for key, canonical in {
                    **LEGACY_SPELLING_ALIASES,
                    **LEGACY_SEMANTIC_ALIASES,
                }.items()
            },
        }
        if folded not in lookup:
            raise
        return canonical_relationship_kind(str(lookup[folded]))


def _normalize_edge(edge: EdgeWitness, resolver: IdentityResolver) -> EdgeWitness:
    return edge.model_copy(
        update={
            "source_id": resolver.resolve(edge.source_id),
            "relationship": _canonical_relationship(edge.relationship),
            "target_id": resolver.resolve(edge.target_id),
        }
    )


def _normalize_finding(
    finding: DiscoveryFinding,
    resolver: IdentityResolver,
) -> DiscoveryFinding:
    return finding.model_copy(
        update={
            "route": tuple(_normalize_edge(edge, resolver) for edge in finding.route),
            "evidence_edges": tuple(
                _normalize_edge(edge, resolver) for edge in finding.evidence_edges
            ),
        }
    )


def _edge_key(edge: EdgeWitness) -> tuple[str, str, str, str]:
    return (
        edge.source_id.casefold(),
        edge.relationship.casefold(),
        edge.target_id.casefold(),
        edge.direction.value,
    )


def _route_key(edges: Sequence[EdgeWitness]) -> tuple[tuple[str, str, str, str], ...]:
    return tuple(_edge_key(edge) for edge in edges)


def _is_contiguous(edges: Sequence[EdgeWitness]) -> bool:
    return bool(edges) and all(
        previous.target_id.casefold() == current.source_id.casefold()
        for previous, current in zip(edges, edges[1:], strict=False)
    )


def _is_strict_subpath(
    candidate: Sequence[EdgeWitness],
    container: Sequence[EdgeWitness],
) -> bool:
    if len(candidate) >= len(container):
        return False
    wanted = _route_key(candidate)
    available = _route_key(container)
    return any(
        available[offset : offset + len(wanted)] == wanted
        for offset in range(len(available) - len(wanted) + 1)
    )


def _finding_digest(finding: DiscoveryFinding) -> str:
    return canonical_sha256(finding)


def _finding_result(
    finding: DiscoveryFinding,
    *,
    classification: str,
    evidence_valid: bool,
    reason: str,
) -> DiscoveryFindingResult:
    return DiscoveryFindingResult(
        finding_digest=_finding_digest(finding),
        classification=classification,  # type: ignore[arg-type]
        evidence_valid=evidence_valid,
        reason=reason,
    )


def grade_discovery(
    public: DiscoveryPublicArtifact,
    private: DiscoveryPrivateArtifact,
    submission: DiscoverySubmission,
) -> DiscoveryScoreReport:
    """Grade a complete structured discovery answer without exposing truth IDs."""

    if private.public_artifact_fingerprint != public.artifact_fingerprint:
        raise ValueError("discovery public/private artifact fingerprints differ")
    if private.graph_fingerprint != public.graph_fingerprint:
        raise ValueError("discovery public/private graph fingerprints differ")
    if submission.public_artifact_fingerprint != public.artifact_fingerprint:
        raise DiscoverySubmissionError(
            "submission is bound to a different discovery public artifact"
        )
    if len(submission.findings) > public.bounds.max_result_cardinality:
        raise DiscoverySubmissionError(
            "submission exceeds the discovery result-cardinality bound"
        )

    resolver = IdentityResolver(private.identity_catalog)
    graph_edges = frozenset(private.graph_fact_registry.edge_keys)
    truth_by_route: dict[
        tuple[tuple[str, str, str, str], ...], str
    ] = {}
    truth_variants: list[tuple[str, Sequence[EdgeWitness]]] = []
    for target, variant in iter_truth_variants(private):
        truth_by_route[_route_key(variant.edges)] = target.target_id
        truth_variants.append((target.target_id, variant.edges))

    matched_targets: set[str] = set()
    results: list[DiscoveryFindingResult] = []
    false_positives = 0
    redundant = 0

    for raw_finding in submission.findings:
        try:
            finding = _normalize_finding(raw_finding, resolver)
        except (IdentityResolutionError, ValueError):
            false_positives += 1
            results.append(
                _finding_result(
                    raw_finding,
                    classification="false_positive",
                    evidence_valid=False,
                    reason="unknown or ambiguous identity/relationship",
                )
            )
            continue

        route_keys = {_edge_key(edge) for edge in finding.route}
        evidence_keys = {_edge_key(edge) for edge in finding.evidence_edges}
        evidence_covers_route = bool(finding.evidence_edges) and route_keys <= evidence_keys
        graph_attested = all(
            edge_fact_key(edge) in graph_edges
            for edge in (*finding.route, *finding.evidence_edges)
        )
        evidence_valid = evidence_covers_route and graph_attested
        if (
            not _is_contiguous(finding.route)
            or len(finding.route) > public.bounds.max_hops
            or not evidence_valid
        ):
            false_positives += 1
            results.append(
                _finding_result(
                    finding,
                    classification="false_positive",
                    evidence_valid=evidence_valid,
                    reason="route shape, bound, or graph-evidence contract failed",
                )
            )
            continue

        route_key = _route_key(finding.route)
        target_id = truth_by_route.get(route_key)
        if target_id is not None:
            if target_id in matched_targets:
                redundant += 1
                results.append(
                    _finding_result(
                        finding,
                        classification="alternate_redundant",
                        evidence_valid=True,
                        reason="valid alternate for an already discovered objective",
                    )
                )
            else:
                matched_targets.add(target_id)
                results.append(
                    _finding_result(
                        finding,
                        classification="matched",
                        evidence_valid=True,
                        reason="route and evidence matched one discovery objective",
                    )
                )
            continue

        if any(
            _is_strict_subpath(finding.route, truth_edges)
            for _, truth_edges in truth_variants
        ):
            redundant += 1
            results.append(
                _finding_result(
                    finding,
                    classification="overlap_redundant",
                    evidence_valid=True,
                    reason="graph-valid strict overlap; not an independent objective",
                )
            )
            continue

        false_positives += 1
        results.append(
            _finding_result(
                finding,
                classification="false_positive",
                evidence_valid=True,
                reason="graph-valid evidence did not satisfy a discovery objective",
            )
        )

    matched = len(matched_targets)
    objective_count = len(private.targets)
    precision_denominator = matched + false_positives
    precision = matched / precision_denominator if precision_denominator else 1.0
    recall = matched / objective_count
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0
    )
    payload = {
        "public_artifact_fingerprint": public.artifact_fingerprint,
        "submission_fingerprint": canonical_sha256(submission),
        "finding_count": len(submission.findings),
        "matched_objectives": matched,
        "objective_count": objective_count,
        "false_positive_count": false_positives,
        "redundant_count": redundant,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "finding_results": tuple(results),
        "report_fingerprint": "0" * 64,
    }
    payload["report_fingerprint"] = canonical_sha256(
        {
            "schema_version": "ori-discovery-report-v2",
            "protocol_version": "ori-open-world-discovery-v2",
            "scorer_version": "ori-discovery-scorer-v2.1",
            **payload,
        },
        exclude_fields=("report_fingerprint",),
    )
    return DiscoveryScoreReport.model_validate(payload)


def _load_public(path: Path) -> DiscoveryPublicArtifact:
    return DiscoveryPublicArtifact.model_validate_json(path.read_text())


def _load_private(path: Path) -> DiscoveryPrivateArtifact:
    return DiscoveryPrivateArtifact.model_validate_json(path.read_text())


def grade_discovery_files(
    *,
    public_path: Path,
    private_path: Path,
    submission_path: Path,
    output_path: Path,
) -> DiscoveryScoreReport:
    public = _load_public(public_path)
    private = _load_private(private_path)
    payload = _load_json_bytes(
        submission_path.read_bytes(),
        max_bytes=public.bounds.max_output_bytes,
    )
    errors = sorted(
        Draft202012Validator(public.answer_schema).iter_errors(payload),
        key=lambda item: list(item.absolute_path),
    )
    if errors:
        raise DiscoverySubmissionError(
            "submission violates the public answer schema: " + errors[0].message
        )
    try:
        submission = DiscoverySubmission.model_validate(payload)
    except ValueError as exc:
        raise DiscoverySubmissionError(str(exc)) from exc
    report = grade_discovery(public, private, submission)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
    )
    return report
