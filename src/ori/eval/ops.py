"""Operational verification helpers for ingest and smoke tests."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from .bhce import BHCEClient
from .report import print_comparison
from .runner import EvalResult, run_eval_cli_bare


@dataclass
class CountCheck:
    label: str
    actual: int
    expected: int | None

    @property
    def ok(self) -> bool:
        return self.expected is None or self.actual == self.expected


@dataclass
class EdgeCheck:
    edge_type: str
    source: str
    target: str
    found: bool
    error: str | None = None


@dataclass
class PathCheck:
    template_id: str
    found: bool
    source_name: str
    target_name: str
    edge_checks: list[EdgeCheck] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.found and all(edge.found for edge in self.edge_checks)


@dataclass
class VerifyIngestResult:
    count_checks: list[CountCheck]
    path_checks: list[PathCheck]

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.count_checks) and all(
            path.ok for path in self.path_checks
        )


def apply_bhce_url_override(bhce_url: str | None) -> str | None:
    """Apply CLI BHCE URL overrides to env and return parsed hostname."""
    domain = None
    if bhce_url:
        parsed = urlparse(bhce_url)
        domain = parsed.hostname
        if parsed.port:
            os.environ["BLOODHOUND_PORT"] = str(parsed.port)
        if parsed.scheme:
            os.environ["BLOODHOUND_SCHEME"] = parsed.scheme
    return domain


def _load_manifest(manifest_path: Path) -> dict:
    return json.loads(manifest_path.read_text())


def _count_queries() -> dict[str, str]:
    return {
        "users": "MATCH (n:User) RETURN n",
        "computers": "MATCH (n:Computer) RETURN n",
        "groups": "MATCH (n:Group) RETURN n",
        "ous": "MATCH (n:OU) RETURN n",
        "domains": "MATCH (n:Domain) RETURN n",
    }


def _edge_query(source_sid: str, edge_type: str, target_sid: str) -> str:
    return (
        f"MATCH (a)-[:{edge_type}]->(b) "
        f"WHERE coalesce(a.objectid, a.objectId) = '{source_sid}' "
        f"AND coalesce(b.objectid, b.objectId) = '{target_sid}' "
        "RETURN a, b"
    )


async def verify_ingest(manifest_path: Path, bhce_url: str | None = None) -> VerifyIngestResult:
    """Verify that manifest counts and planted paths all exist in BloodHound CE."""
    manifest = _load_manifest(manifest_path)
    stats = manifest.get("stats", {})
    domain = apply_bhce_url_override(bhce_url)

    async with BHCEClient(domain=domain) as bhce:
        count_checks: list[CountCheck] = []
        for key, query in _count_queries().items():
            result = await bhce.run_cypher(query)
            actual = len(result.nodes) if result.success else -1
            expected = stats.get(key)
            count_checks.append(CountCheck(label=key, actual=actual, expected=expected))

        path_checks: list[PathCheck] = []
        for planted in manifest.get("planted_paths", []):
            verification = await bhce.run_cypher(planted["verification_cypher"])
            found = verification.success and len(verification.nodes) > 0
            edge_checks: list[EdgeCheck] = []
            for edge in planted.get("path_edges", []):
                edge_result = await bhce.run_cypher(
                    _edge_query(edge["source"], edge["edge"], edge["target"])
                )
                edge_checks.append(
                    EdgeCheck(
                        edge_type=edge["edge"],
                        source=edge["source"],
                        target=edge["target"],
                        found=edge_result.success and len(edge_result.nodes) > 0,
                        error=None if edge_result.success else edge_result.error,
                    )
                )
            path_checks.append(
                PathCheck(
                    template_id=planted["template_id"],
                    found=found,
                    source_name=planted.get("source_name", ""),
                    target_name=planted.get("target_name", ""),
                    edge_checks=edge_checks,
                    error=None if verification.success else verification.error,
                )
            )

    return VerifyIngestResult(count_checks=count_checks, path_checks=path_checks)


def print_verify_ingest(result: VerifyIngestResult) -> None:
    print("== Node counts ==")
    for check in result.count_checks:
        expected = (
            f" expected={check.expected}" if check.expected is not None else ""
        )
        status = "OK" if check.ok else "MISMATCH"
        print(f"{check.label:10} actual={check.actual}{expected} {status}")

    print("\n== Planted path verification ==")
    for path in result.path_checks:
        status = "OK" if path.found else "MISSING"
        desc = f"{path.template_id}: {path.source_name} -> {path.target_name}".strip()
        print(f"{desc:50} {status}")
        for edge in path.edge_checks:
            edge_status = "OK" if edge.found else "MISSING"
            print(
                f"  - {edge.edge_type:24} "
                f"{edge.source[-6:]} -> {edge.target[-6:]} {edge_status}"
            )

    ok_paths = sum(1 for path in result.path_checks if path.ok)
    print(f"\nSummary: {ok_paths}/{len(result.path_checks)} planted paths verified")
    print(f"INGEST CHECK: {'PASS' if result.ok else 'FAIL'}")


SMOKE_EXPECTATIONS: dict[str, str] = {
    "mock/perfect": "CORRECT",
    "mock/hallucinate": "HALLUCINATION",
    "mock/wrong": "INCORRECT",
    "mock/syntax_error": "CYPHER_ERROR",
    "mock/empty": "PARSE_FAIL",
}


@dataclass
class SmokeCheck:
    model: str
    expected: str
    actual_counts: dict[str, int]

    @property
    def ok(self) -> bool:
        return self.actual_counts.get(self.expected, 0) > 0 and len(self.actual_counts) == 1


@dataclass
class SmokeEvalResult:
    checks: list[SmokeCheck]
    results_by_model: dict[str, list[EvalResult]]

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)


async def run_smoke_eval(
    manifest_path: Path,
    output_dir: Path,
    bhce_url: str | None = None,
) -> SmokeEvalResult:
    """Run mock-model smoke tests for grading/execution pipeline."""
    output_dir.mkdir(parents=True, exist_ok=True)
    results_by_model: dict[str, list[EvalResult]] = {}
    checks: list[SmokeCheck] = []

    for model, expected in SMOKE_EXPECTATIONS.items():
        csv_path = output_dir / f"{model.replace('/', '_').replace(':', '-')}.csv"
        results = await run_eval_cli_bare(
            manifest_path=manifest_path,
            model=model,
            output_path=csv_path,
            concurrency=1,
            bhce_url=bhce_url,
            ollama_options=None,
        )
        results_by_model[model] = results
        counts: dict[str, int] = {}
        for item in results:
            counts[item.grade.outcome] = counts.get(item.grade.outcome, 0) + 1
        checks.append(SmokeCheck(model=model, expected=expected, actual_counts=counts))

    return SmokeEvalResult(checks=checks, results_by_model=results_by_model)


def print_smoke_eval(result: SmokeEvalResult) -> None:
    print("== Smoke test outcome checks ==")
    for check in result.checks:
        print(
            f"{check.model:20} expected={check.expected:14} "
            f"actual={check.actual_counts} {'PASS' if check.ok else 'FAIL'}"
        )
    print()
    print_comparison(result.results_by_model)
    print(f"\nSMOKE TEST: {'PASS' if result.ok else 'FAIL'}")
