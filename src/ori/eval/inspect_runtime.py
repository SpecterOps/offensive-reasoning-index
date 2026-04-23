"""Inspect AI-backed runtime for direct-Cypher evaluation."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from inspect_ai import Task as InspectTask
from inspect_ai import eval_async as inspect_eval_async
from inspect_ai.dataset import Sample
from inspect_ai.log import EvalLog, EvalSample
from inspect_ai.model import ChatMessageSystem, ChatMessageUser, ModelOutput
from inspect_ai.scorer import Score, accuracy, scorer, stderr
from inspect_ai.solver import Generate, TaskState, solver

from .adapter import ModelResponse, call_model, extract_cypher_details
from .bhce import BHCEClient, CypherResult
from .grader import GradeResult, grade
from .tasks import Task

if TYPE_CHECKING:
    from .runner import EvalResult


@dataclass
class InspectEvalMetadata:
    log_location: str | None = None
    sample_id: str | None = None
    sample_uuid: str | None = None
    model_calls: int = 0
    error_retries: int = 0


def _configure_inspect_runtime_dirs(root: Path) -> None:
    """Point Inspect runtime data/cache paths at a writable project-local directory."""
    from inspect_ai._util import appdirs as inspect_appdirs
    from inspect_ai._util import kvstore as inspect_kvstore
    from inspect_ai._util import trace as inspect_trace
    from inspect_ai._view import notify as inspect_view_notify
    from inspect_ai.log._recorders.buffer import database as inspect_buffer_db

    data_root = root / "_inspect_runtime"
    cache_root = data_root / "cache"
    trace_root = data_root / "traces"
    data_root.mkdir(parents=True, exist_ok=True)
    cache_root.mkdir(parents=True, exist_ok=True)
    trace_root.mkdir(parents=True, exist_ok=True)

    def runtime_data_dir(subdir: str | None) -> Path:
        path = data_root if subdir is None else data_root / subdir
        path.mkdir(parents=True, exist_ok=True)
        return path

    def runtime_cache_dir(subdir: str | None) -> Path:
        path = cache_root if subdir is None else cache_root / subdir
        path.mkdir(parents=True, exist_ok=True)
        return path

    inspect_appdirs.inspect_data_dir = runtime_data_dir
    inspect_appdirs.inspect_cache_dir = runtime_cache_dir
    inspect_kvstore.inspect_data_dir = runtime_data_dir
    inspect_buffer_db.inspect_data_dir = runtime_data_dir
    inspect_trace.inspect_data_dir = runtime_data_dir
    inspect_view_notify.inspect_data_dir = runtime_data_dir
    inspect_trace.inspect_trace_dir = lambda: runtime_data_dir("traces")


def _system_prompt(domain: str) -> str:
    return f"""\
You are analyzing an Active Directory environment using BloodHound CE.
Domain: {domain}

Write a single Cypher query to answer the question.
Return ONLY the Cypher query — no explanation, no markdown fences, no commentary.

Rules:
- Use RETURN p for path queries (not RETURN n, m separately — edges won't render)
- Do NOT use: UNWIND, CASE expressions, list comprehensions [x IN y | ...], labels(), XOR
- shortestPath requires a single variable-length pattern: (a)-[*1..]->(b)
- String ops: STARTS WITH, CONTAINS, ENDS WITH, =~ are supported
- count() works but use RETURN node for set queries
"""


def _task_to_dict(task: Task) -> dict[str, Any]:
    return {
        "id": task.id,
        "template_id": task.template_id,
        "tier": task.tier,
        "category": task.category,
        "question": task.question,
        "reference_cypher": task.reference_cypher,
        "grade_mode": task.grade_mode,
        "tags": list(task.tags),
        "metadata": dict(task.metadata),
    }


def _task_from_dict(data: dict[str, Any]) -> Task:
    return Task(
        id=data["id"],
        template_id=data["template_id"],
        tier=data["tier"],
        category=data["category"],
        question=data["question"],
        reference_cypher=data["reference_cypher"],
        grade_mode=data["grade_mode"],
        tags=list(data.get("tags", [])),
        metadata=dict(data.get("metadata", {})),
    )


def _cypher_result_to_dict(result: CypherResult) -> dict[str, Any]:
    return {
        "success": result.success,
        "nodes": result.nodes,
        "node_names": sorted(result.node_names),
        "error": result.error,
        "raw": result.raw,
    }


def _cypher_result_from_dict(data: dict[str, Any]) -> CypherResult:
    return CypherResult(
        success=bool(data["success"]),
        nodes=list(data.get("nodes", [])),
        node_names=set(data.get("node_names", [])),
        error=data.get("error"),
        raw=dict(data.get("raw", {})),
    )


def _model_response_to_dict(response: ModelResponse) -> dict[str, Any]:
    return {
        "raw_text": response.raw_text,
        "cypher": response.cypher,
        "parse_stage": response.parse_stage,
        "tokens_input": response.tokens_input,
        "tokens_output": response.tokens_output,
        "elapsed_seconds": response.elapsed_seconds,
        "model": response.model,
        "thinking": response.thinking,
        "error": response.error,
        "provider_metrics": dict(response.provider_metrics),
    }


def _model_response_from_dict(data: dict[str, Any]) -> ModelResponse:
    return ModelResponse(
        raw_text=data.get("raw_text", ""),
        cypher=data.get("cypher"),
        parse_stage=data.get("parse_stage", "none"),
        tokens_input=int(data.get("tokens_input", 0)),
        tokens_output=int(data.get("tokens_output", 0)),
        elapsed_seconds=float(data.get("elapsed_seconds", 0.0)),
        model=data.get("model", ""),
        thinking=data.get("thinking", ""),
        error=data.get("error"),
        provider_metrics=dict(data.get("provider_metrics") or {}),
    )


def _grade_result_to_dict(result: GradeResult) -> dict[str, Any]:
    return {
        "score": result.score,
        "outcome": result.outcome,
        "hallucination": result.hallucination,
        "details": result.details,
    }


def _grade_result_from_dict(data: dict[str, Any]) -> GradeResult:
    return GradeResult(
        score=float(data["score"]),
        outcome=data["outcome"],
        hallucination=bool(data["hallucination"]),
        details=data["details"],
    )


def _score_metadata_to_grade_result(score: Score) -> GradeResult:
    metadata = score.metadata or {}
    if "grade" not in metadata:
        raise ValueError("Inspect score metadata missing serialized grade")
    return _grade_result_from_dict(metadata["grade"])


def _resolve_model_base_url(model: str, base_url: str | None) -> str | None:
    provider, _name = model.split("/", 1)
    if provider == "ollama":
        resolved = (base_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")).rstrip("/")
        if not resolved.endswith("/v1"):
            resolved = f"{resolved}/v1"
        return resolved
    if provider == "openai-compat":
        return base_url
    return base_url


def _inspect_supported_model(model: str) -> bool:
    provider, name = model.split("/", 1)
    if provider in {"anthropic", "openai", "ollama", "gemini"}:
        return True
    if provider == "openai-compat":
        return "@" not in name
    return False


def _use_adapter_path(model: str) -> bool:
    if model.startswith("mock/"):
        return True
    provider, _name = model.split("/", 1)
    if provider == "ollama":
        return True
    return not _inspect_supported_model(model)


@solver
def ori_direct_cypher_solver() -> Generate:
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        task_t0 = time.monotonic()
        metadata = state.metadata
        task = _task_from_dict(metadata["ori_task"])
        model_name = metadata.get("requested_model", str(state.model))
        base_url = metadata.get("model_base_url")
        ollama_options = metadata.get("ollama_options")
        model_response: ModelResponse

        if _use_adapter_path(model_name):
            model_response = await call_model(
                task=task,
                model=model_name,
                base_url=base_url,
                ollama_options=ollama_options,
            )
            state.messages = [
                ChatMessageSystem(
                    content=_system_prompt(task.metadata.get("domain", "CORP.LOCAL"))
                ),
                ChatMessageUser(content=task.question),
            ]
            state.output = ModelOutput.from_content(
                model=model_response.model,
                content=model_response.raw_text,
                error=model_response.error,
            )
        else:
            state.messages = [
                ChatMessageSystem(
                    content=_system_prompt(task.metadata.get("domain", "CORP.LOCAL"))
                ),
                ChatMessageUser(content=task.question),
            ]
            try:
                state = await generate(state)
                output = state.output
                usage = output.usage
                cypher, parse_stage = extract_cypher_details(output.completion)
                model_response = ModelResponse(
                    raw_text=output.completion,
                    cypher=cypher,
                    parse_stage=parse_stage,
                    tokens_input=usage.input_tokens if usage else 0,
                    tokens_output=usage.output_tokens if usage else 0,
                    elapsed_seconds=output.time or 0.0,
                    model=output.model or model_name,
                    thinking="",
                    error=output.error,
                    provider_metrics={},
                )
            except Exception as exc:
                model_response = ModelResponse(
                    raw_text="",
                    cypher=None,
                    parse_stage="none",
                    tokens_input=0,
                    tokens_output=0,
                    elapsed_seconds=0.0,
                    model=model_name,
                    thinking="",
                    error=str(exc),
                    provider_metrics={},
                )
                state.output = ModelOutput.from_content(
                    model=model_name,
                    content="",
                    error=str(exc),
                )

        if model_response.cypher:
            domain = metadata.get("bhce_domain")
            async with BHCEClient(domain=domain) as bhce:
                model_result = await bhce.run_cypher_resilient(model_response.cypher)
        else:
            model_result = CypherResult(success=False, error="No Cypher extracted")

        state.store.set("ori_model_response", _model_response_to_dict(model_response))
        state.store.set("ori_model_result", _cypher_result_to_dict(model_result))
        state.store.set("ori_model_calls", 1)
        state.store.set("ori_task_wall_seconds", time.monotonic() - task_t0)
        return state

    return solve


@scorer(metrics=[accuracy(), stderr()])
def ori_direct_cypher_scorer():
    async def score(state: TaskState, target: Any) -> Score:
        metadata = state.metadata
        task = _task_from_dict(metadata["ori_task"])
        ref_result = _cypher_result_from_dict(metadata["ref_result"])
        valid_names = set(metadata.get("valid_node_names", []))
        model_response = _model_response_from_dict(state.store.get("ori_model_response"))
        model_result = _cypher_result_from_dict(state.store.get("ori_model_result"))

        result = grade(
            task=task,
            model_response=model_response,
            model_result=model_result,
            ref_result=ref_result,
            valid_node_names=valid_names,
        )
        sample_index = metadata.get("sample_index", "?")
        sample_total = metadata.get("sample_total", "?")
        print(f"  [{sample_index}/{sample_total}] {task.id} ({task.tier=}, {task.grade_mode})")
        print(f"           → {result.outcome} (score={result.score})")
        return Score(
            value=result.score,
            answer=model_response.cypher,
            explanation=result.details,
            metadata={
                "grade": _grade_result_to_dict(result),
                "parse_stage": model_response.parse_stage,
                "model_error": model_response.error,
            },
        )

    return score


def _sample_for_task(
    task: Task,
    ref_result: CypherResult,
    valid_node_names: set[str],
    requested_model: str,
    model_base_url: str | None,
    ollama_options: dict[str, Any] | None,
    bhce_domain: str | None,
    sample_index: int,
    sample_total: int,
) -> Sample:
    return Sample(
        id=task.id,
        input=task.question,
        target="",
        metadata={
            "ori_task": _task_to_dict(task),
            "ref_result": _cypher_result_to_dict(ref_result),
            "valid_node_names": sorted(valid_node_names),
            "requested_model": requested_model,
            "model_base_url": model_base_url,
            "ollama_options": ollama_options,
            "bhce_domain": bhce_domain,
            "sample_index": sample_index,
            "sample_total": sample_total,
        },
    )


def _task_name_for_model(model: str) -> str:
    return f"ori_direct_cypher_{model.replace('/', '_').replace(':', '_').replace('@', '_')}"


def _log_dir_for_output(output_path: Path, model: str) -> Path:
    slug = model.replace("/", "_").replace(":", "-").replace("@", "_")
    return output_path.parent / "_inspect_logs" / slug


def _result_from_sample(sample: EvalSample, log: EvalLog) -> EvalResult:
    from .runner import EvalResult

    task = _task_from_dict(sample.metadata["ori_task"])
    model_response = _model_response_from_dict(sample.store["ori_model_response"])
    model_result = _cypher_result_from_dict(sample.store["ori_model_result"])
    if not sample.scores:
        raise ValueError(f"Inspect sample {sample.id!r} missing scores")
    score = next(iter(sample.scores.values()))
    grade_result = _score_metadata_to_grade_result(score)
    inspect_meta = InspectEvalMetadata(
        log_location=log.location or None,
        sample_id=str(sample.id),
        sample_uuid=sample.uuid,
        model_calls=int(sample.store.get("ori_model_calls", 0)),
        error_retries=len(sample.error_retries or []),
    )
    return EvalResult(
        task=task,
        model_response=model_response,
        grade=grade_result,
        ref_result=_cypher_result_from_dict(sample.metadata["ref_result"]),
        model_result=model_result,
        inspect=inspect_meta,
        task_wall_seconds=float(
            sample.store.get("ori_task_wall_seconds", model_response.elapsed_seconds)
        ),
    )


async def run_eval_with_inspect(
    tasks: list[Task],
    model: str,
    bhce: BHCEClient,
    output_path: Path,
    concurrency: int = 3,
    base_url: str | None = None,
    ollama_options: dict[str, Any] | None = None,
    log_dir: Path | None = None,
    bhce_domain: str | None = None,
) -> list[EvalResult]:
    """Run ORI direct-Cypher eval using Inspect AI as the runtime."""

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

    resolved_base_url = _resolve_model_base_url(model, base_url)
    inspect_model = model if not _use_adapter_path(model) else "none/none"
    samples = [
        _sample_for_task(
            task=task,
            ref_result=ref_results[task.id],
            valid_node_names=valid_names,
            requested_model=model,
            model_base_url=resolved_base_url,
            ollama_options=ollama_options,
            bhce_domain=bhce_domain,
            sample_index=i + 1,
            sample_total=len(tasks),
        )
        for i, task in enumerate(tasks)
    ]
    inspect_task = InspectTask(
        dataset=samples,
        solver=ori_direct_cypher_solver(),
        scorer=ori_direct_cypher_scorer(),
        name=_task_name_for_model(model),
    )

    resolved_log_dir = log_dir or _log_dir_for_output(output_path, model)
    resolved_log_dir.mkdir(parents=True, exist_ok=True)
    _configure_inspect_runtime_dirs(resolved_log_dir)

    eval_logs = await inspect_eval_async(
        inspect_task,
        model=inspect_model,
        model_base_url=resolved_base_url,
        log_dir=str(resolved_log_dir),
        max_samples=max(concurrency, 1),
        max_subprocesses=1,
        log_level="warning",
        log_level_transcript="warning",
        extra_body={"options": ollama_options}
        if ollama_options and inspect_model.startswith("ollama/")
        else None,
    )
    if not eval_logs:
        raise RuntimeError("Inspect eval returned no logs")
    log = eval_logs[0]
    if not log.samples:
        raise RuntimeError("Inspect eval log did not contain sample results")
    results = [_result_from_sample(sample, log) for sample in log.samples]
    return results
