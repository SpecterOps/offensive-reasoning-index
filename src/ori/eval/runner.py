"""Async evaluation runner."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ori.mcp_launcher import MCPLauncherConfig

from ..telemetry import record_eval_telemetry
from .adapter import ModelResponse, call_model
from .bhce import BHCEClient, CypherResult, parse_bhce_url, resolve_bhce_target
from .direct_query_safety import (
    DirectQueryCoordinator,
    DirectQuerySafetyConfig,
    QueryDenyCache,
)
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


def _manifest_fingerprint(manifest_path: Path) -> str:
    return hashlib.sha256(manifest_path.read_bytes()).hexdigest()


def _checkpoint_path(output_path: Path) -> Path:
    return output_path.with_suffix(output_path.suffix + ".checkpoint.json")


def _cypher_result_to_checkpoint(result: CypherResult) -> dict[str, Any]:
    payload = asdict(result)
    payload["node_names"] = sorted(result.node_names)
    return payload


def _cypher_result_from_checkpoint(payload: dict[str, Any]) -> CypherResult:
    data = dict(payload)
    data["node_names"] = set(data.get("node_names", []))
    return CypherResult(**data)


def _direct_result_to_checkpoint(result: EvalResult) -> dict[str, Any]:
    return {
        "task": asdict(result.task),
        "model_response": asdict(result.model_response),
        "grade": asdict(result.grade),
        "ref_result": _cypher_result_to_checkpoint(result.ref_result),
        "model_result": _cypher_result_to_checkpoint(result.model_result),
        "inspect": asdict(result.inspect) if result.inspect else None,
        "run_name": result.run_name,
        "requested_model": result.requested_model,
        "run_config": result.run_config,
        "task_wall_seconds": result.task_wall_seconds,
        "telemetry": result.telemetry,
        "partial_result": result.partial_result,
        "attempt_number": result.attempt_number,
        "result_source": result.result_source,
    }


def _direct_result_from_checkpoint(payload: dict[str, Any]) -> EvalResult:
    inspect_payload = payload.get("inspect")
    return EvalResult(
        task=Task(**payload["task"]),
        model_response=ModelResponse(**payload["model_response"]),
        grade=GradeResult(**payload["grade"]),
        ref_result=_cypher_result_from_checkpoint(payload["ref_result"]),
        model_result=_cypher_result_from_checkpoint(payload["model_result"]),
        inspect=InspectEvalMetadata(**inspect_payload) if inspect_payload else None,
        run_name=payload.get("run_name"),
        requested_model=payload.get("requested_model"),
        run_config=payload.get("run_config"),
        task_wall_seconds=payload.get("task_wall_seconds"),
        telemetry=payload.get("telemetry"),
        partial_result=bool(payload.get("partial_result", False)),
        attempt_number=int(payload.get("attempt_number", 1)),
        result_source=payload.get("result_source", "first_pass"),
    )


def _write_direct_checkpoint(
    results: list[EvalResult],
    *,
    output_path: Path,
    manifest_fingerprint: str,
    model: str,
    run_name: str,
    model_config_identity: dict[str, Any],
    safety_config: DirectQuerySafetyConfig,
) -> None:
    path = _checkpoint_path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 2,
        "manifest_fingerprint": manifest_fingerprint,
        "model": model,
        "run_name": run_name,
        "model_config_identity": model_config_identity,
        "safety_policy": safety_config.to_jsonable(),
        "complete": bool(results)
        and not any(result.grade.outcome == "INFRA_ERROR" for result in results),
        "results": [_direct_result_to_checkpoint(result) for result in results],
    }
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def _load_direct_checkpoint(
    *,
    output_path: Path,
    manifest_fingerprint: str,
    model: str,
    run_name: str,
    model_config_identity: dict[str, Any],
    safety_config: DirectQuerySafetyConfig,
) -> list[EvalResult]:
    path = _checkpoint_path(output_path)
    if not path.exists():
        return []
    payload = json.loads(path.read_text())
    expected = {
        "manifest_fingerprint": manifest_fingerprint,
        "model": model,
        "run_name": run_name,
        "model_config_identity": model_config_identity,
        "safety_policy": safety_config.to_jsonable(),
    }
    mismatches = [key for key, value in expected.items() if payload.get(key) != value]
    if mismatches:
        raise RuntimeError(
            f"Direct checkpoint {path} is incompatible for: {', '.join(mismatches)}. "
            "Use a new output path for a new campaign."
        )
    return [_direct_result_from_checkpoint(item) for item in payload.get("results", [])]


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
                    mcp_launcher=str((run_config or {}).get("mcp_launcher") or ""),
                    mcp_source=str((run_config or {}).get("mcp_source") or ""),
                    mcp_revision=str((run_config or {}).get("mcp_revision") or ""),
                    mcp_executable=str((run_config or {}).get("mcp_executable") or ""),
                    uv_version=str((run_config or {}).get("uv_version") or ""),
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


def _mcp_runtime_run_config(
    run_config: dict[str, Any] | None,
    batch_results: list[EvalResult],
) -> dict[str, Any] | None:
    """Add safe launcher and discovery provenance reported by the MCP runtime."""
    config = dict(run_config or {})
    all_metadata = [result.mcp for result in batch_results if result.mcp]
    metadata = next((item for item in all_metadata if item.mcp_launcher), None)
    metadata = metadata or (all_metadata[0] if all_metadata else None)
    if metadata is None:
        return config or None
    for key, value in {
        "mcp_launcher": metadata.mcp_launcher,
        "mcp_source": metadata.mcp_source,
        "mcp_revision": metadata.mcp_revision,
        "mcp_executable": metadata.mcp_executable,
        "uv_version": metadata.uv_version,
    }.items():
        if value:
            config[key] = value
    if metadata.prompt_discovery_status:
        config["prompt_discovery_status"] = metadata.prompt_discovery_status
        config["prompt_discovery_succeeded"] = metadata.prompt_discovery_status == "selected"
    if metadata.resource_discovery_status:
        config["resource_discovery_status"] = metadata.resource_discovery_status
        config["resource_discovery_succeeded"] = metadata.resource_discovery_status == "listed"
    return config


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
    safety_config = DirectQuerySafetyConfig()
    coordinator = DirectQueryCoordinator(
        bhce=bhce,
        config=safety_config,
        deny_cache=QueryDenyCache(
            None,
            manifest_fingerprint="adhoc-direct-eval",
            policy_version=safety_config.policy_version,
        ),
    )

    async def run_one(task: Task, idx: int) -> EvalResult:
        async with sem:
            task_t0 = asyncio.get_running_loop().time()
            print(f"  [{idx}/{len(tasks)}] {task.id} ({task.tier=}, {task.grade_mode})")
            model_resp = await call_model(
                task, model, base_url=base_url, ollama_options=ollama_options
            )

            if model_resp.cypher:
                model_result = await coordinator.execute(model_resp.cypher)
            else:
                model_result = CypherResult(
                    success=False,
                    error="No Cypher extracted",
                    failure_type="parse_error",
                    failure_subtype="no_cypher_extracted",
                    query_executed=False,
                    execution_attempts=0,
                    safety_policy_version=safety_config.policy_version,
                )

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


async def _run_direct_eval_core(
    *,
    tasks: list[Task],
    manifest_path: Path,
    model: str,
    output_path: Path,
    concurrency: int,
    bhce_url: str | None,
    ollama_options: dict | None,
    max_model_reruns_on_infra: int,
    model_base_url: str | None,
    health_timeout_seconds: float,
    health_poll_interval: float,
    run_name: str | None,
    run_config: dict[str, Any] | None,
    telemetry_enabled: bool,
    direct_query_safety: DirectQuerySafetyConfig,
) -> list[EvalResult]:
    fingerprint = _manifest_fingerprint(manifest_path)
    effective_run_name = run_name or model
    bhce_kwargs = parse_bhce_url(bhce_url)
    model_config_identity = {
        "bhce_target": resolve_bhce_target(bhce_url),
        "model_base_url": model_base_url,
        "ollama_options": ollama_options,
        "run_config": run_config,
    }
    results = _load_direct_checkpoint(
        output_path=output_path,
        manifest_fingerprint=fingerprint,
        model=model,
        run_name=effective_run_name,
        model_config_identity=model_config_identity,
        safety_config=direct_query_safety,
    )
    if results:
        print(
            f"Loaded direct checkpoint with {len(results)}/{len(tasks)} task result(s) "
            f"from {_checkpoint_path(output_path)}"
        )

    completed_ids = {result.task.id for result in results if result.grade.outcome != "INFRA_ERROR"}
    pending_tasks = [task for task in tasks if task.id not in completed_ids]
    if not pending_tasks:
        print("Direct checkpoint is complete; no model or BloodHound calls are required.")

    domain = bhce_kwargs.get("domain")
    retry_index = 0
    next_attempt_number = (
        max(
            (result.attempt_number for result in results),
            default=0,
        )
        + 1
    )

    while pending_tasks:
        async with BHCEClient(**bhce_kwargs) as bhce:
            health = await bhce.wait_until_healthy(
                timeout_seconds=health_timeout_seconds,
                poll_interval=health_poll_interval,
            )
            if not health.ok:
                if results:
                    print(
                        "BloodHound did not recover; preserving the direct checkpoint "
                        f"with {len(pending_tasks)} pending task(s): {health.detail}"
                    )
                    break
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
                direct_query_safety=direct_query_safety,
                manifest_fingerprint=fingerprint,
            )
            for result in batch_results:
                result.run_name = effective_run_name
                result.requested_model = model
                result.run_config = run_config
                result.attempt_number = next_attempt_number
                if result.partial_result:
                    result.result_source = (
                        "circuit_open_placeholder"
                        if next_attempt_number == 1
                        else "resume_circuit_open_placeholder"
                    )
                elif next_attempt_number == 1:
                    result.result_source = "first_pass"
                elif results:
                    result.result_source = "resume"
                else:
                    result.result_source = "retry"
            results = _merge_results_by_task(tasks, results, batch_results)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        _validate_result_accounting(tasks, results)
        write_csv(results, output_path)
        _write_direct_checkpoint(
            results,
            output_path=output_path,
            manifest_fingerprint=fingerprint,
            model=model,
            run_name=effective_run_name,
            model_config_identity=model_config_identity,
            safety_config=direct_query_safety,
        )

        infra_task_ids = _infra_task_ids(batch_results)
        if not infra_task_ids or retry_index >= max_model_reruns_on_infra:
            break
        retry_index += 1
        next_attempt_number += 1
        pending_tasks = [task for task in tasks if task.id in infra_task_ids]
        print(
            f"\nDetected INFRA_ERROR during {model} run. "
            f"Re-running {len(pending_tasks)} infra-error task(s) "
            f"({retry_index}/{max_model_reruns_on_infra}) after recovery wait..."
        )

    if not results:
        raise RuntimeError("Direct evaluation produced no results.")

    _validate_result_accounting(tasks, results)
    record_eval_telemetry(
        results,
        output_path=output_path,
        model=model,
        run_name=effective_run_name,
        requested_model=model,
        run_config=run_config,
        model_base_url=model_base_url,
        enabled=telemetry_enabled,
    )
    write_csv(results, output_path)
    _write_direct_checkpoint(
        results,
        output_path=output_path,
        manifest_fingerprint=fingerprint,
        model=model,
        run_name=effective_run_name,
        model_config_identity=model_config_identity,
        safety_config=direct_query_safety,
    )
    print_summary(results, effective_run_name)
    return results


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
    direct_query_safety: DirectQuerySafetyConfig | None = None,
) -> list[EvalResult]:
    """Like run_eval_cli but returns results for multi-model comparison."""
    manifest = json.loads(manifest_path.read_text())
    tasks = generate_tasks(manifest)

    return await _run_direct_eval_core(
        tasks=tasks,
        manifest_path=manifest_path,
        model=model,
        output_path=output_path,
        concurrency=concurrency,
        bhce_url=bhce_url,
        ollama_options=ollama_options,
        max_model_reruns_on_infra=max_model_reruns_on_infra,
        model_base_url=model_base_url,
        health_timeout_seconds=health_timeout_seconds,
        health_poll_interval=health_poll_interval,
        run_name=run_name,
        run_config=run_config,
        telemetry_enabled=telemetry_enabled,
        direct_query_safety=direct_query_safety or DirectQuerySafetyConfig(),
    )


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
    direct_query_safety: DirectQuerySafetyConfig | None = None,
) -> None:
    """Entry point called from the CLI."""
    manifest = json.loads(manifest_path.read_text())
    tasks = generate_tasks(manifest)
    print(f"Generated {len(tasks)} tasks from {manifest_path.name}")
    print(f"Model: {run_name or model}")
    print("Running evaluation...\n")

    await _run_direct_eval_core(
        tasks=tasks,
        manifest_path=manifest_path,
        model=model,
        output_path=output_path,
        concurrency=concurrency,
        bhce_url=bhce_url,
        ollama_options=ollama_options,
        max_model_reruns_on_infra=max_model_reruns_on_infra,
        model_base_url=model_base_url,
        health_timeout_seconds=health_timeout_seconds,
        health_poll_interval=health_poll_interval,
        run_name=run_name,
        run_config=run_config,
        telemetry_enabled=telemetry_enabled,
        direct_query_safety=direct_query_safety or DirectQuerySafetyConfig(),
    )
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
    mcp_launcher: MCPLauncherConfig | None = None,
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
                mcp_launcher=mcp_launcher,
                max_steps=max_steps,
                resource_mode=resource_mode,
                mcp_tool_loop=mcp_tool_loop,
                openai_compat_telemetry_adapter=openai_compat_telemetry_adapter,
                ollama_read_timeout_seconds=mcp_ollama_read_timeout_seconds,
            )
            for result in batch_results:
                result.run_name = run_name or model
                result.requested_model = model
                result.run_config = _mcp_runtime_run_config(run_config, batch_results)
                result.attempt_number = attempt + 1
                result.result_source = "first_pass" if attempt == 0 else "retry"
            missing_results = _missing_mcp_results(
                pending_tasks,
                batch_results,
                model=model,
                run_name=run_name,
                run_config=_mcp_runtime_run_config(run_config, batch_results),
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
    telemetry_run_config = results[0].run_config if results else run_config
    record_eval_telemetry(
        results,
        output_path=output_path,
        model=model,
        run_name=run_name or model,
        requested_model=model,
        run_config=telemetry_run_config,
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
    mcp_launcher: MCPLauncherConfig | None = None,
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
                mcp_launcher=mcp_launcher,
                max_steps=max_steps,
                resource_mode=resource_mode,
                mcp_tool_loop=mcp_tool_loop,
                openai_compat_telemetry_adapter=openai_compat_telemetry_adapter,
                ollama_read_timeout_seconds=mcp_ollama_read_timeout_seconds,
            )
            for result in batch_results:
                result.run_name = run_name or model
                result.requested_model = model
                result.run_config = _mcp_runtime_run_config(run_config, batch_results)
                result.attempt_number = attempt + 1
                result.result_source = "first_pass" if attempt == 0 else "retry"
            missing_results = _missing_mcp_results(
                pending_tasks,
                batch_results,
                model=model,
                run_name=run_name,
                run_config=_mcp_runtime_run_config(run_config, batch_results),
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
    telemetry_run_config = results[0].run_config if results else run_config
    record_eval_telemetry(
        results,
        output_path=output_path,
        model=model,
        run_name=run_name or model,
        requested_model=model,
        run_config=telemetry_run_config,
        model_base_url=model_base_url,
        enabled=telemetry_enabled,
    )
    write_csv(results, output_path)
    print_summary(results, run_name or model)
    print(f"\nResults written to {output_path}")
