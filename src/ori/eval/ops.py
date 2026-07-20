"""Operational verification helpers for ingest and smoke tests."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .bhce import BHCEClient, parse_bhce_url
from .mcp_runtime import RESOURCE_MODE_OFF
from .report import print_comparison
from .runner import EvalResult, run_eval_cli_bare, run_eval_mcp_cli_bare


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
    expected_found: bool = True
    query_succeeded: bool = True

    @property
    def ok(self) -> bool:
        return (
            self.query_succeeded
            and self.found is self.expected_found
            and all(edge.found for edge in self.edge_checks)
        )


@dataclass
class VerifyIngestResult:
    count_checks: list[CountCheck]
    path_checks: list[PathCheck]

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.count_checks) and all(
            path.ok for path in self.path_checks
        )


@dataclass
class VerifyBHHealthResult:
    ok: bool
    detail: str
    classification: str
    query: str


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
    bhce_kwargs = parse_bhce_url(bhce_url)

    async with BHCEClient(**bhce_kwargs) as bhce:
        count_checks: list[CountCheck] = []
        for key, query in _count_queries().items():
            result = await bhce.run_cypher_resilient(query)
            actual = len(result.nodes) if result.success else -1
            expected = stats.get(key)
            count_checks.append(CountCheck(label=key, actual=actual, expected=expected))

        path_checks: list[PathCheck] = []
        for planted in manifest.get("planted_paths", []):
            verification = await bhce.run_cypher_resilient(planted["verification_cypher"])
            found = verification.success and len(verification.nodes) > 0
            expected_found = not bool(
                planted.get("negative_control")
                or (planted.get("metadata") or {}).get("negative_control")
            )
            edge_checks: list[EdgeCheck] = []
            for edge in [
                *planted.get("path_edges", []),
                *planted.get("supporting_edges", []),
            ]:
                edge_result = await bhce.run_cypher_resilient(
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
                    expected_found=expected_found,
                    query_succeeded=verification.success,
                )
            )

    return VerifyIngestResult(count_checks=count_checks, path_checks=path_checks)


async def verify_bh_health(
    bhce_url: str | None = None,
    timeout_seconds: float = 60.0,
    poll_interval: float = 5.0,
) -> VerifyBHHealthResult:
    """Wait for BloodHound CE to become healthy using a safe graph query."""
    bhce_kwargs = parse_bhce_url(bhce_url)
    async with BHCEClient(**bhce_kwargs) as bhce:
        result = await bhce.wait_until_healthy(
            timeout_seconds=timeout_seconds,
            poll_interval=poll_interval,
        )
    return VerifyBHHealthResult(
        ok=result.ok,
        detail=result.detail,
        classification=result.classification,
        query=result.query,
    )


def print_verify_bh_health(result: VerifyBHHealthResult) -> None:
    print("== BloodHound health check ==")
    print(f"query: {result.query}")
    print(f"classification: {result.classification}")
    print(f"detail: {result.detail}")
    print(f"BH HEALTH: {'PASS' if result.ok else 'FAIL'}")


def print_verify_ingest(result: VerifyIngestResult) -> None:
    print("== Node counts ==")
    for check in result.count_checks:
        expected = f" expected={check.expected}" if check.expected is not None else ""
        status = "OK" if check.ok else "MISMATCH"
        print(f"{check.label:10} actual={check.actual}{expected} {status}")

    print("\n== Planted path verification ==")
    for path in result.path_checks:
        if not path.query_succeeded:
            status = "ERROR"
        else:
            status = "OK" if path.found is path.expected_found else "UNEXPECTED"
        expectation = "present" if path.expected_found else "absent"
        desc = f"{path.template_id}: {path.source_name} -> {path.target_name}".strip()
        print(f"{desc:50} {status} (expected {expectation})")
        if path.error:
            print(f"  verification query failed: {path.error}")
        for edge in path.edge_checks:
            edge_status = "OK" if edge.found else "MISSING"
            print(f"  - {edge.edge_type:24} {edge.source[-6:]} -> {edge.target[-6:]} {edge_status}")

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

SMOKE_MCP_EXPECTATIONS: dict[str, str] = {
    "mock/mcp_perfect": "CORRECT",
    "mock/mcp_wrong": "INCORRECT",
    "mock/mcp_empty": "PARSE_FAIL",
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


@dataclass
class PreflightResult:
    health: VerifyBHHealthResult
    ingest: VerifyIngestResult
    smoke: SmokeEvalResult

    @property
    def ok(self) -> bool:
        return self.health.ok and self.ingest.ok and self.smoke.ok


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


async def run_preflight(
    manifest_path: Path,
    output_dir: Path,
    bhce_url: str | None = None,
    timeout_seconds: float = 60.0,
    poll_interval: float = 5.0,
) -> PreflightResult:
    """Run BH health, ingest verification, and smoke eval in one sequence."""
    health = await verify_bh_health(
        bhce_url=bhce_url,
        timeout_seconds=timeout_seconds,
        poll_interval=poll_interval,
    )
    ingest = await verify_ingest(manifest_path=manifest_path, bhce_url=bhce_url)
    smoke = await run_smoke_eval(
        manifest_path=manifest_path,
        output_dir=output_dir,
        bhce_url=bhce_url,
    )
    return PreflightResult(health=health, ingest=ingest, smoke=smoke)


async def run_smoke_mcp_eval(
    manifest_path: Path,
    output_dir: Path,
    bhce_url: str | None = None,
    mcp_dir: Path | None = None,
    max_steps: int = 12,
    resource_mode: str = RESOURCE_MODE_OFF,
) -> SmokeEvalResult:
    """Run mock-model MCP smoke tests for structured answers and reporting."""
    output_dir.mkdir(parents=True, exist_ok=True)
    results_by_model: dict[str, list[EvalResult]] = {}
    checks: list[SmokeCheck] = []

    for model, expected in SMOKE_MCP_EXPECTATIONS.items():
        csv_path = output_dir / f"{model.replace('/', '_').replace(':', '-')}.csv"
        results = await run_eval_mcp_cli_bare(
            manifest_path=manifest_path,
            model=model,
            output_path=csv_path,
            concurrency=1,
            bhce_url=bhce_url,
            ollama_options=None,
            mcp_dir=mcp_dir,
            max_steps=max_steps,
            resource_mode=resource_mode,
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


def print_preflight(result: PreflightResult) -> None:
    print_verify_bh_health(result.health)
    print()
    print_verify_ingest(result.ingest)
    print()
    print_smoke_eval(result.smoke)
    print(f"\nPREFLIGHT: {'PASS' if result.ok else 'FAIL'}")
