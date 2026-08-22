from __future__ import annotations

import csv
import json

from ori.eval.adapter import ModelResponse
from ori.eval.bhce import CypherResult
from ori.eval.grader import GradeResult
from ori.eval.runner import EvalResult
from ori.eval.tasks import Task
from ori.telemetry import normalize_ollama_base_url, record_eval_telemetry


def _result() -> EvalResult:
    task = Task(
        id="t1",
        template_id="global",
        tier=1,
        category="enumeration",
        question="Question?",
        reference_cypher="MATCH (n) RETURN n",
        grade_mode="node_set",
        metadata={"domain": "CORP.LOCAL"},
    )
    response = ModelResponse(
        raw_text="MATCH (n) RETURN n",
        cypher="MATCH (n) RETURN n",
        parse_stage="bare_match",
        tokens_input=10,
        tokens_output=20,
        elapsed_seconds=2.0,
        model="mock/perfect",
        provider_metrics={"eval_duration_ns": 1_000_000_000},
    )
    grade = GradeResult(score=1.0, outcome="CORRECT", hallucination=False, details="ok")
    empty = CypherResult(success=True, nodes=[], node_names=set(), raw={})
    return EvalResult(
        task=task,
        model_response=response,
        grade=grade,
        ref_result=empty,
        model_result=empty,
        task_wall_seconds=2.5,
    )


def test_normalize_ollama_base_url_strips_openai_and_api_suffixes() -> None:
    assert normalize_ollama_base_url("http://host:11434/v1") == "http://host:11434"
    assert normalize_ollama_base_url("http://host:11434/api") == "http://host:11434"


def test_record_eval_telemetry_writes_artifacts_and_annotates_results(tmp_path) -> None:
    result = _result()
    output = tmp_path / "results" / "mock.csv"
    output.parent.mkdir()

    record_eval_telemetry(
        [result],
        output_path=output,
        model="mock/perfect",
        run_name="mock-run",
        requested_model="mock/perfect",
        run_config={"model": "mock/perfect"},
        model_base_url=None,
        enabled=True,
    )

    telemetry_root = output.parent / "telemetry"
    assert (telemetry_root / "run_environment.json").exists()
    assert (telemetry_root / "models" / "mock-run" / "model_metadata.json").exists()
    sample_path = telemetry_root / "samples" / "mock-run.jsonl"
    assert sample_path.exists()
    sample = json.loads(sample_path.read_text().splitlines()[0])
    assert sample["task_wall_seconds"] == 2.5
    assert sample["output_tokens_per_second"] == 20.0
    assert sample["tokens_per_second_source"] == "ollama_eval_duration"
    assert sample["direct_query"]["query_executed"] is True
    assert sample["direct_query"]["execution_attempts"] == 1
    assert sample["direct_query"]["circuit_state"] == "closed"
    assert result.telemetry
    assert result.telemetry["output_tokens_per_second"] == 20.0
    assert result.telemetry["sample_ref"].endswith("mock-run.jsonl:L1")

    with (telemetry_root / "telemetry_summary.csv").open() as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["run_name"] == "mock-run"
    assert rows[0]["total_output_tokens"] == "20"
