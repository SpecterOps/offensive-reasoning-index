"""Async evaluation runner."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path

from .adapter import ModelResponse, call_model
from .bhce import BHCEClient, CypherResult
from .grader import GradeResult, grade
from .inspect_runtime import InspectEvalMetadata, run_eval_with_inspect
from .mcp_runtime import MCPRunMetadata, run_mcp_eval_with_inspect
from .report import write_csv, print_summary
from .tasks import Task, generate_mcp_tasks, generate_tasks


@dataclass
class EvalResult:
    task: Task
    model_response: ModelResponse
    grade: GradeResult
    ref_result: CypherResult
    model_result: CypherResult
    inspect: InspectEvalMetadata | None = None
    mcp: MCPRunMetadata | None = None


def _has_infra_errors(results: list[EvalResult]) -> bool:
    return any(result.grade.outcome == "INFRA_ERROR" for result in results)


async def run_eval(
    tasks: list[Task],
    model: str,
    bhce: BHCEClient,
    concurrency: int = 3,
    base_url: str | None = None,
    ollama_options: dict | None = None,
) -> list[EvalResult]:
    """Run evaluation for a list of tasks. Returns EvalResult per task."""

    print(f"Fetching valid node names for hallucination detection...")
    valid_names = await bhce.get_all_node_names()
    if len(valid_names) == 0:
        print(f"  WARNING: 0 node names loaded — BH CE graph appears empty.")
        print(f"  Make sure you have uploaded and ingested the generated zip before running eval.")
        print(f"  Continuing, but all tasks will likely fail with CYPHER_ERROR.")
    else:
        print(f"  {len(valid_names)} node names loaded")

    print(f"Pre-fetching reference Cypher results for {len(tasks)} tasks...")
    ref_results: dict[str, CypherResult] = {}
    for task in tasks:
        ref_results[task.id] = await bhce.run_cypher_resilient(task.reference_cypher)
    print(f"  Done")

    sem = asyncio.Semaphore(concurrency)
    results: list[EvalResult] = []

    async def run_one(task: Task, idx: int) -> EvalResult:
        async with sem:
            print(f"  [{idx}/{len(tasks)}] {task.id} ({task.tier=}, {task.grade_mode})")
            model_resp = await call_model(task, model, base_url=base_url, ollama_options=ollama_options)

            if model_resp.cypher:
                model_result = await bhce.run_cypher_resilient(model_resp.cypher)
            else:
                model_result = CypherResult(success=False, error="No Cypher extracted")

            g = grade(
                task=task,
                model_response=model_resp,
                model_result=model_result,
                ref_result=ref_results[task.id],
                valid_node_names=valid_names,
            )
            print(f"           → {g.outcome} (score={g.score})")
            return EvalResult(
                task=task,
                model_response=model_resp,
                grade=g,
                ref_result=ref_results[task.id],
                model_result=model_result,
            )

    coros = [run_one(task, i + 1) for i, task in enumerate(tasks)]
    results = await asyncio.gather(*coros)
    return list(results)


async def run_eval_cli_bare(
    manifest_path: Path,
    model: str,
    output_path: Path,
    concurrency: int = 1,
    bhce_url: str | None = None,
    ollama_options: dict | None = None,
    max_model_reruns_on_infra: int = 1,
) -> list[EvalResult]:
    """Like run_eval_cli but returns results for multi-model comparison."""
    manifest = json.loads(manifest_path.read_text())
    tasks = generate_tasks(manifest)

    domain, scheme, port = None, None, None
    if bhce_url:
        from urllib.parse import urlparse
        parsed = urlparse(bhce_url)
        domain = parsed.hostname
        if parsed.port:
            os.environ["BLOODHOUND_PORT"] = str(parsed.port)
        if parsed.scheme:
            os.environ["BLOODHOUND_SCHEME"] = parsed.scheme

    attempt = 0
    while True:
        async with BHCEClient(domain=domain) as bhce:
            health = await bhce.wait_until_healthy()
            if not health.ok:
                raise RuntimeError(
                    f"BloodHound CE health check failed before eval: {health.detail} "
                    f"(classification={health.classification})"
                )
            results = await run_eval_with_inspect(
                tasks=tasks,
                model=model,
                bhce=bhce,
                output_path=output_path,
                concurrency=concurrency,
                base_url=None,
                ollama_options=ollama_options,
                bhce_domain=domain,
            )
        if not _has_infra_errors(results) or attempt >= max_model_reruns_on_infra:
            break
        attempt += 1
        print(
            f"\nDetected INFRA_ERROR during {model} run. "
            f"Re-running full model benchmark ({attempt}/{max_model_reruns_on_infra}) after recovery wait..."
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_csv(results, output_path)
    print_summary(results, model)
    return results


async def run_eval_cli(
    manifest_path: Path,
    model: str,
    output_path: Path,
    concurrency: int = 3,
    bhce_url: str | None = None,
    max_model_reruns_on_infra: int = 1,
    ollama_options: dict | None = None,
) -> None:
    """Entry point called from the CLI."""
    manifest = json.loads(manifest_path.read_text())
    tasks = generate_tasks(manifest)
    print(f"Generated {len(tasks)} tasks from {manifest_path.name}")
    print(f"Model: {model}")
    print(f"Running evaluation...\n")

    # Parse bhce_url if provided
    domain = None
    if bhce_url:
        # e.g. https://bloodhound.example.com or http://localhost:8080
        from urllib.parse import urlparse
        parsed = urlparse(bhce_url)
        domain = parsed.hostname
        if parsed.port:
            os.environ["BLOODHOUND_PORT"] = str(parsed.port)
        if parsed.scheme:
            os.environ["BLOODHOUND_SCHEME"] = parsed.scheme

    attempt = 0
    while True:
        async with BHCEClient(domain=domain) as bhce:
            health = await bhce.wait_until_healthy()
            if not health.ok:
                raise RuntimeError(
                    f"BloodHound CE health check failed before eval: {health.detail} "
                    f"(classification={health.classification})"
                )
            results = await run_eval_with_inspect(
                tasks=tasks,
                model=model,
                bhce=bhce,
                output_path=output_path,
                concurrency=concurrency,
                base_url=None,
                ollama_options=ollama_options,
                bhce_domain=domain,
            )
        if not _has_infra_errors(results) or attempt >= max_model_reruns_on_infra:
            break
        attempt += 1
        print(
            f"\nDetected INFRA_ERROR during {model} run. "
            f"Re-running full eval ({attempt}/{max_model_reruns_on_infra}) after recovery wait..."
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_csv(results, output_path)
    print_summary(results, model)
    print(f"\nResults written to {output_path}")


async def run_eval_mcp_cli_bare(
    manifest_path: Path,
    model: str,
    output_path: Path,
    concurrency: int = 1,
    bhce_url: str | None = None,
    ollama_options: dict | None = None,
    max_model_reruns_on_infra: int = 1,
    mcp_dir: Path | None = None,
    max_steps: int = 12,
) -> list[EvalResult]:
    """Run MCP-mode evaluation and return results for multi-model comparison."""
    manifest = json.loads(manifest_path.read_text())
    tasks = generate_mcp_tasks(manifest)

    domain = None
    if bhce_url:
        from urllib.parse import urlparse
        parsed = urlparse(bhce_url)
        domain = parsed.hostname
        if parsed.port:
            os.environ["BLOODHOUND_PORT"] = str(parsed.port)
        if parsed.scheme:
            os.environ["BLOODHOUND_SCHEME"] = parsed.scheme

    attempt = 0
    while True:
        async with BHCEClient(domain=domain) as bhce:
            health = await bhce.wait_until_healthy()
            if not health.ok:
                raise RuntimeError(
                    f"BloodHound CE health check failed before MCP eval: {health.detail} "
                    f"(classification={health.classification})"
                )
            results = await run_mcp_eval_with_inspect(
                tasks=tasks,
                model=model,
                bhce=bhce,
                output_path=output_path,
                concurrency=concurrency,
                ollama_options=ollama_options,
                bhce_domain=domain,
                mcp_dir=mcp_dir,
                max_steps=max_steps,
            )
        if not _has_infra_errors(results) or attempt >= max_model_reruns_on_infra:
            break
        attempt += 1
        print(
            f"\nDetected INFRA_ERROR during {model} MCP run. "
            f"Re-running full model benchmark ({attempt}/{max_model_reruns_on_infra}) after recovery wait..."
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_csv(results, output_path)
    print_summary(results, model)
    return results


async def run_eval_mcp_cli(
    manifest_path: Path,
    model: str,
    output_path: Path,
    concurrency: int = 1,
    bhce_url: str | None = None,
    max_model_reruns_on_infra: int = 1,
    mcp_dir: Path | None = None,
    max_steps: int = 12,
    ollama_options: dict | None = None,
) -> None:
    """Entry point called from the CLI for MCP-mode evaluation."""
    manifest = json.loads(manifest_path.read_text())
    tasks = generate_mcp_tasks(manifest)
    print(f"Generated {len(tasks)} MCP tasks from {manifest_path.name}")
    print(f"Model: {model}")
    print("Running MCP evaluation...\n")

    domain = None
    if bhce_url:
        from urllib.parse import urlparse
        parsed = urlparse(bhce_url)
        domain = parsed.hostname
        if parsed.port:
            os.environ["BLOODHOUND_PORT"] = str(parsed.port)
        if parsed.scheme:
            os.environ["BLOODHOUND_SCHEME"] = parsed.scheme

    attempt = 0
    while True:
        async with BHCEClient(domain=domain) as bhce:
            health = await bhce.wait_until_healthy()
            if not health.ok:
                raise RuntimeError(
                    f"BloodHound CE health check failed before MCP eval: {health.detail} "
                    f"(classification={health.classification})"
                )
            results = await run_mcp_eval_with_inspect(
                tasks=tasks,
                model=model,
                bhce=bhce,
                output_path=output_path,
                concurrency=concurrency,
                ollama_options=ollama_options,
                bhce_domain=domain,
                mcp_dir=mcp_dir,
                max_steps=max_steps,
            )
        if not _has_infra_errors(results) or attempt >= max_model_reruns_on_infra:
            break
        attempt += 1
        print(
            f"\nDetected INFRA_ERROR during {model} MCP eval. "
            f"Re-running full eval ({attempt}/{max_model_reruns_on_infra}) after recovery wait..."
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_csv(results, output_path)
    print_summary(results, model)
    print(f"\nResults written to {output_path}")
