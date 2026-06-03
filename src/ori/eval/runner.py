"""Async evaluation runner."""

from __future__ import annotations

import asyncio
import json
from collections import Counter
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
    partial_result: bool = False
    attempt_number: int = 1
    result_source: str = "first_pass"


def _validate_result_accounting(tasks: list[Task], results: list[EvalResult]) -> None:
    """Ensure every requested task produced exactly one terminal outcome row."""
    expected_ids = [task.id for task in tasks]
    result_ids = [result.task.id for result in results]
    missing = sorted(set(expected_ids) - set(result_ids))
    duplicate_counts = Counter(result_ids)
    duplicates = sorted(task_id for task_id, count in duplicate_counts.items() if count > 1)
    unexpected = sorted(set(result_ids) - set(expected_ids))
    outcome_counts = Counter(result.grade.outcome for result in results)
    outcome_total = sum(outcome_counts.values())
    expected_total = len(expected_ids)

    if missing or duplicates or unexpected or outcome_total != expected_total:
        raise RuntimeError(
            "Result accounting failed: "
            f"expected={expected_total}, outcome_total={outcome_total}, "
            f"missing={missing}, duplicates={duplicates}, unexpected={unexpected}, "
            f"outcomes={dict(sorted(outcome_counts.items()))}"
        )

    print(
        "Result accounting: "
        f"{outcome_total}/{expected_total} tasks accounted for by outcome "
        f"{dict(sorted(outcome_counts.items()))}"
    )


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


def _missing_mcp_results(
    requested_tasks: list[Task],
    batch_results: list[EvalResult],
    *,
    model: str,
    run_name: str | None,
    run_config: dict[str, Any] | None,
    resource_mode: str,
    attempt_number: int = 1,
    result_source: str = "first_pass",
) -> list[EvalResult]:
    """Represent interrupted Inspect samples that never reached result extraction."""
    returned_task_ids = {result.task.id for result in batch_results}
    missing_tasks = [task for task in requested_tasks if task.id not in returned_task_ids]
    if not missing_tasks:
        return []

    detail = (
        "subtype=batch_interrupted_missing_result; "
        "MCP run returned no sample result for this task; "
        "the model run likely interrupted before the full task set completed"
    )
    missing_results: list[EvalResult] = []
    for task in missing_tasks:
        response = ModelResponse(
            raw_text="",
            cypher=None,
            parse_stage="none",
            tokens_input=0,
            tokens_output=0,
            elapsed_seconds=0.0,
            model=model,
            thinking="",
            error=detail,
            provider_metrics={},
        )
        missing_results.append(
            EvalResult(
                task=task,
                model_response=response,
                grade=GradeResult(
                    score=0.0,
                    outcome="INFRA_ERROR",
                    hallucination=False,
                    details=detail,
                ),
                ref_result=CypherResult(success=False, error=detail),
                model_result=CypherResult(success=False, error=detail),
                mcp=MCPRunMetadata(
                    resource_mode=resource_mode,
                    infra_error_subtype="batch_interrupted_missing_result",
                ),
                run_name=run_name or model,
                requested_model=model,
                run_config=run_config,
                task_wall_seconds=0.0,
                partial_result=True,
                attempt_number=attempt_number,
                result_source=result_source,
            )
        )
    print(
        f"  WARNING: MCP run returned {len(batch_results)}/{len(requested_tasks)} "
        f"requested task result(s); marking {len(missing_results)} missing task(s) as INFRA_ERROR."
    )
    return missing_results


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
                result.attempt_number = attempt + 1
                result.result_source = "first_pass" if attempt == 0 else "retry"
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
    _validate_result_accounting(tasks, results)
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
                result.attempt_number = attempt + 1
                result.result_source = "first_pass" if attempt == 0 else "retry"
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
    _validate_result_accounting(tasks, results)
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
    mcp_tool_loop: str = "auto",
    openai_compat_telemetry_adapter: str = "auto",
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
                mcp_tool_loop=mcp_tool_loop,
                openai_compat_telemetry_adapter=openai_compat_telemetry_adapter,
                ollama_read_timeout_seconds=mcp_ollama_read_timeout_seconds,
            )
            for result in batch_results:
                result.run_name = run_name or model
                result.requested_model = model
                result.run_config = run_config
                result.attempt_number = attempt + 1
                result.result_source = "first_pass" if attempt == 0 else "retry"
            missing_results = _missing_mcp_results(
                pending_tasks,
                batch_results,
                model=model,
                run_name=run_name,
                run_config=run_config,
                resource_mode=resource_mode,
                attempt_number=attempt + 1,
                result_source=(
                    "first_pass_interrupted_placeholder"
                    if attempt == 0
                    else "retry_interrupted_placeholder"
                ),
            )
            batch_results = [*batch_results, *missing_results]
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
    _validate_result_accounting(tasks, results)
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
    mcp_tool_loop: str = "auto",
    openai_compat_telemetry_adapter: str = "auto",
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
                mcp_tool_loop=mcp_tool_loop,
                openai_compat_telemetry_adapter=openai_compat_telemetry_adapter,
                ollama_read_timeout_seconds=mcp_ollama_read_timeout_seconds,
            )
            for result in batch_results:
                result.run_name = run_name or model
                result.requested_model = model
                result.run_config = run_config
                result.attempt_number = attempt + 1
                result.result_source = "first_pass" if attempt == 0 else "retry"
            missing_results = _missing_mcp_results(
                pending_tasks,
                batch_results,
                model=model,
                run_name=run_name,
                run_config=run_config,
                resource_mode=resource_mode,
                attempt_number=attempt + 1,
                result_source=(
                    "first_pass_interrupted_placeholder"
                    if attempt == 0
                    else "retry_interrupted_placeholder"
                ),
            )
            batch_results = [*batch_results, *missing_results]
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
    _validate_result_accounting(tasks, results)
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
