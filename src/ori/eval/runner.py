"""Async evaluation runner."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..telemetry import record_eval_telemetry
from .adapter import ModelResponse, call_model
from .bhce import BHCEClient, CypherResult, parse_bhce_url
from .grader import GradeResult, grade
from .inspect_runtime import InspectEvalMetadata, run_eval_with_inspect
from .mcp_runtime import (
    DEFAULT_MCP_OLLAMA_READ_TIMEOUT_SECONDS,
    RESOURCE_MODE_OFF,
    MCPRunMetadata,
    run_mcp_eval_with_inspect,
)
from .report import print_summary, write_csv
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
    run_name: str | None = None
    requested_model: str | None = None
    run_config: dict[str, Any] | None = None
    task_wall_seconds: float | None = None
    telemetry: dict[str, Any] | None = None


def _infra_task_ids(results: list[EvalResult]) -> set[str]:
    return {result.task.id for result in results if result.grade.outcome == "INFRA_ERROR"}


def _merge_results_by_task(
    task_order: list[Task],
    current_results: list[EvalResult],
    new_results: list[EvalResult],
) -> list[EvalResult]:
    """Merge rerun results while preserving manifest task order."""
    by_task_id = {result.task.id: result for result in current_results}
    for result in new_results:
        by_task_id[result.task.id] = result
    return [by_task_id[task.id] for task in task_order if task.id in by_task_id]


async def run_eval(
    tasks: list[Task],
    model: str,
    bhce: BHCEClient,
    concurrency: int = 3,
    base_url: str | None = None,
    ollama_options: dict | None = None,
    run_name: str | None = None,
    run_config: dict[str, Any] | None = None,
) -> list[EvalResult]:
    """Run evaluation for a list of tasks. Returns EvalResult per task."""

    print("Fetching valid node names for hallucination detection...")
    valid_names = await bhce.get_all_node_names()
    if len(valid_names) == 0:
        print("  WARNING: 0 node names loaded — BH CE graph appears empty.")
        print("  Make sure you have uploaded and ingested the generated zip before running eval.")
        print("  Continuing, but all tasks will likely fail with CYPHER_ERROR.")
    else:
        print(f"  {len(valid_names)} node names loaded")

    print(f"Pre-fetching reference Cypher results for {len(tasks)} tasks...")
    ref_results: dict[str, CypherResult] = {}
    for task in tasks:
        ref_results[task.id] = await bhce.run_cypher_resilient(task.reference_cypher)
    print("  Done")

    sem = asyncio.Semaphore(concurrency)
    results: list[EvalResult] = []

    async def run_one(task: Task, idx: int) -> EvalResult:
        async with sem:
            task_t0 = asyncio.get_running_loop().time()
            print(f"  [{idx}/{len(tasks)}] {task.id} ({task.tier=}, {task.grade_mode})")
            model_resp = await call_model(
                task, model, base_url=base_url, ollama_options=ollama_options
            )

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
                run_name=run_name or model,
                requested_model=model,
                run_config=run_config,
                task_wall_seconds=asyncio.get_running_loop().time() - task_t0,
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
    model_base_url: str | None = None,
    health_timeout_seconds: float = 60.0,
    health_poll_interval: float = 5.0,
    run_name: str | None = None,
    run_config: dict[str, Any] | None = None,
    telemetry_enabled: bool = True,
) -> list[EvalResult]:
    """Like run_eval_cli but returns results for multi-model comparison."""
    manifest = json.loads(manifest_path.read_text())
    tasks = generate_tasks(manifest)

    bhce_kwargs = parse_bhce_url(bhce_url)
    domain = bhce_kwargs.get("domain")

    attempt = 0
    pending_tasks = list(tasks)
    results: list[EvalResult] = []
    while True:
        async with BHCEClient(**bhce_kwargs) as bhce:
            health = await bhce.wait_until_healthy(
                timeout_seconds=health_timeout_seconds,
                poll_interval=health_poll_interval,
            )
            if not health.ok:
                raise RuntimeError(
                    f"BloodHound CE health check failed before eval: {health.detail} "
                    f"(classification={health.classification})"
                )
            batch_results = await run_eval_with_inspect(
                tasks=pending_tasks,
                model=model,
                bhce=bhce,
                output_path=output_path,
                concurrency=concurrency,
                base_url=model_base_url,
                ollama_options=ollama_options,
                bhce_domain=domain,
            )
            for result in batch_results:
                result.run_name = run_name or model
                result.requested_model = model
                result.run_config = run_config
            results = _merge_results_by_task(tasks, results, batch_results)
        infra_task_ids = _infra_task_ids(batch_results)
        if not infra_task_ids or attempt >= max_model_reruns_on_infra:
            break
        attempt += 1
        pending_tasks = [task for task in tasks if task.id in infra_task_ids]
        print(
            f"\nDetected INFRA_ERROR during {model} run. "
            f"Re-running {len(pending_tasks)} infra-error task(s) "
            f"({attempt}/{max_model_reruns_on_infra}) after recovery wait..."
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    record_eval_telemetry(
        results,
        output_path=output_path,
        model=model,
        run_name=run_name or model,
        requested_model=model,
        run_config=run_config,
        model_base_url=model_base_url,
        enabled=telemetry_enabled,
    )
    write_csv(results, output_path)
    print_summary(results, run_name or model)
    return results


async def run_eval_cli(
    manifest_path: Path,
    model: str,
    output_path: Path,
    concurrency: int = 3,
    bhce_url: str | None = None,
    max_model_reruns_on_infra: int = 1,
    ollama_options: dict | None = None,
    model_base_url: str | None = None,
    health_timeout_seconds: float = 60.0,
    health_poll_interval: float = 5.0,
    run_name: str | None = None,
    run_config: dict[str, Any] | None = None,
    telemetry_enabled: bool = True,
) -> None:
    """Entry point called from the CLI."""
    manifest = json.loads(manifest_path.read_text())
    tasks = generate_tasks(manifest)
    print(f"Generated {len(tasks)} tasks from {manifest_path.name}")
    print(f"Model: {run_name or model}")
    print("Running evaluation...\n")

    # Parse bhce_url if provided
    bhce_kwargs = parse_bhce_url(bhce_url)
    domain = bhce_kwargs.get("domain")

    attempt = 0
    pending_tasks = list(tasks)
    results: list[EvalResult] = []
    while True:
        async with BHCEClient(**bhce_kwargs) as bhce:
            health = await bhce.wait_until_healthy(
                timeout_seconds=health_timeout_seconds,
                poll_interval=health_poll_interval,
            )
            if not health.ok:
                raise RuntimeError(
                    f"BloodHound CE health check failed before eval: {health.detail} "
                    f"(classification={health.classification})"
                )
            batch_results = await run_eval_with_inspect(
                tasks=pending_tasks,
                model=model,
                bhce=bhce,
                output_path=output_path,
                concurrency=concurrency,
                base_url=model_base_url,
                ollama_options=ollama_options,
                bhce_domain=domain,
            )
            for result in batch_results:
                result.run_name = run_name or model
                result.requested_model = model
                result.run_config = run_config
            results = _merge_results_by_task(tasks, results, batch_results)
        infra_task_ids = _infra_task_ids(batch_results)
        if not infra_task_ids or attempt >= max_model_reruns_on_infra:
            break
        attempt += 1
        pending_tasks = [task for task in tasks if task.id in infra_task_ids]
        print(
            f"\nDetected INFRA_ERROR during {model} run. "
            f"Re-running {len(pending_tasks)} infra-error task(s) "
            f"({attempt}/{max_model_reruns_on_infra}) after recovery wait..."
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    record_eval_telemetry(
        results,
        output_path=output_path,
        model=model,
        run_name=run_name or model,
        requested_model=model,
        run_config=run_config,
        model_base_url=model_base_url,
        enabled=telemetry_enabled,
    )
    write_csv(results, output_path)
    print_summary(results, run_name or model)
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
    resource_mode: str = RESOURCE_MODE_OFF,
    mcp_ollama_read_timeout_seconds: float = DEFAULT_MCP_OLLAMA_READ_TIMEOUT_SECONDS,
    model_base_url: str | None = None,
    health_timeout_seconds: float = 60.0,
    health_poll_interval: float = 5.0,
    run_name: str | None = None,
    run_config: dict[str, Any] | None = None,
    telemetry_enabled: bool = True,
) -> list[EvalResult]:
    """Run MCP-mode evaluation and return results for multi-model comparison."""
    manifest = json.loads(manifest_path.read_text())
    tasks = generate_mcp_tasks(manifest)

    bhce_kwargs = parse_bhce_url(bhce_url)
    domain = bhce_kwargs.get("domain")

    attempt = 0
    pending_tasks = list(tasks)
    results: list[EvalResult] = []
    while True:
        async with BHCEClient(**bhce_kwargs) as bhce:
            health = await bhce.wait_until_healthy(
                timeout_seconds=health_timeout_seconds,
                poll_interval=health_poll_interval,
            )
            if not health.ok:
                raise RuntimeError(
                    f"BloodHound CE health check failed before MCP eval: {health.detail} "
                    f"(classification={health.classification})"
                )
            batch_results = await run_mcp_eval_with_inspect(
                tasks=pending_tasks,
                model=model,
                bhce=bhce,
                output_path=output_path,
                concurrency=concurrency,
                base_url=model_base_url,
                ollama_options=ollama_options,
                bhce_domain=domain,
                mcp_dir=mcp_dir,
                max_steps=max_steps,
                resource_mode=resource_mode,
                ollama_read_timeout_seconds=mcp_ollama_read_timeout_seconds,
            )
            for result in batch_results:
                result.run_name = run_name or model
                result.requested_model = model
                result.run_config = run_config
            results = _merge_results_by_task(tasks, results, batch_results)
        infra_task_ids = _infra_task_ids(batch_results)
        if not infra_task_ids or attempt >= max_model_reruns_on_infra:
            break
        attempt += 1
        pending_tasks = [task for task in tasks if task.id in infra_task_ids]
        print(
            f"\nDetected INFRA_ERROR during {model} MCP run. "
            f"Re-running {len(pending_tasks)} infra-error task(s) "
            f"({attempt}/{max_model_reruns_on_infra}) after recovery wait..."
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    record_eval_telemetry(
        results,
        output_path=output_path,
        model=model,
        run_name=run_name or model,
        requested_model=model,
        run_config=run_config,
        model_base_url=model_base_url,
        enabled=telemetry_enabled,
    )
    write_csv(results, output_path)
    print_summary(results, run_name or model)
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
    resource_mode: str = RESOURCE_MODE_OFF,
    mcp_ollama_read_timeout_seconds: float = DEFAULT_MCP_OLLAMA_READ_TIMEOUT_SECONDS,
    model_base_url: str | None = None,
    health_timeout_seconds: float = 60.0,
    health_poll_interval: float = 5.0,
    run_name: str | None = None,
    run_config: dict[str, Any] | None = None,
    telemetry_enabled: bool = True,
) -> None:
    """Entry point called from the CLI for MCP-mode evaluation."""
    manifest = json.loads(manifest_path.read_text())
    tasks = generate_mcp_tasks(manifest)
    print(f"Generated {len(tasks)} MCP tasks from {manifest_path.name}")
    print(f"Model: {run_name or model}")
    print("Running MCP evaluation...\n")

    bhce_kwargs = parse_bhce_url(bhce_url)
    domain = bhce_kwargs.get("domain")

    attempt = 0
    pending_tasks = list(tasks)
    results: list[EvalResult] = []
    while True:
        async with BHCEClient(**bhce_kwargs) as bhce:
            health = await bhce.wait_until_healthy(
                timeout_seconds=health_timeout_seconds,
                poll_interval=health_poll_interval,
            )
            if not health.ok:
                raise RuntimeError(
                    f"BloodHound CE health check failed before MCP eval: {health.detail} "
                    f"(classification={health.classification})"
                )
            batch_results = await run_mcp_eval_with_inspect(
                tasks=pending_tasks,
                model=model,
                bhce=bhce,
                output_path=output_path,
                concurrency=concurrency,
                base_url=model_base_url,
                ollama_options=ollama_options,
                bhce_domain=domain,
                mcp_dir=mcp_dir,
                max_steps=max_steps,
                resource_mode=resource_mode,
                ollama_read_timeout_seconds=mcp_ollama_read_timeout_seconds,
            )
            for result in batch_results:
                result.run_name = run_name or model
                result.requested_model = model
                result.run_config = run_config
            results = _merge_results_by_task(tasks, results, batch_results)
        infra_task_ids = _infra_task_ids(batch_results)
        if not infra_task_ids or attempt >= max_model_reruns_on_infra:
            break
        attempt += 1
        pending_tasks = [task for task in tasks if task.id in infra_task_ids]
        print(
            f"\nDetected INFRA_ERROR during {model} MCP eval. "
            f"Re-running {len(pending_tasks)} infra-error task(s) "
            f"({attempt}/{max_model_reruns_on_infra}) after recovery wait..."
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    record_eval_telemetry(
        results,
        output_path=output_path,
        model=model,
        run_name=run_name or model,
        requested_model=model,
        run_config=run_config,
        model_base_url=model_base_url,
        enabled=telemetry_enabled,
    )
    write_csv(results, output_path)
    print_summary(results, run_name or model)
    print(f"\nResults written to {output_path}")
