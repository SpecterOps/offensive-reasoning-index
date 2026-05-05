from __future__ import annotations

import csv

from ori.eval.adapter import ModelResponse
from ori.eval.bhce import CypherResult
from ori.eval.grader import GradeResult
from ori.eval.report import print_comparison, print_summary, write_combined_csv, write_summary_csv
from ori.eval.runner import EvalResult
from ori.eval.tasks import Task


def _task() -> Task:
    return Task(
        id="t1",
        template_id="global",
        tier=1,
        category="enumeration",
        question="List all domain admins",
        reference_cypher="MATCH (u:User) RETURN u",
        grade_mode="node_set",
        metadata={"domain": "CORP.LOCAL"},
    )


def _result(outcome: str, score: float = 0.0, hallucination: bool = False) -> EvalResult:
    task = _task()
    model_response = ModelResponse(
        raw_text="",
        cypher=None,
        parse_stage="none",
        tokens_input=0,
        tokens_output=0,
        elapsed_seconds=0.0,
        model="ollama/test:latest",
        error="404 page not found" if outcome == "MODEL_ERROR" else None,
    )
    grade = GradeResult(
        score=score,
        outcome=outcome,
        hallucination=hallucination,
        details="details",
    )
    empty = CypherResult(success=True, nodes=[], node_names=set(), raw={})
    return EvalResult(
        task=task,
        model_response=model_response,
        grade=grade,
        ref_result=empty,
        model_result=empty,
    )


def _result_for_tier(tier: int, outcome: str, score: float = 0.0) -> EvalResult:
    result = _result(outcome, score=score)
    result.task = Task(
        id=f"t{tier}",
        template_id=f"tier{tier}",
        tier=tier,
        category="path_finding",
        question="q",
        reference_cypher="MATCH (n) RETURN n",
        grade_mode="path_exists",
        metadata={"domain": "CORP.LOCAL"},
    )
    return result


def test_print_summary_includes_model_errors(capsys) -> None:
    print_summary([_result("MODEL_ERROR"), _result("CORRECT", score=1.0)], "ollama/test:latest")
    out = capsys.readouterr().out
    assert "Model errors: 1" in out


def test_print_summary_includes_infra_errors(capsys) -> None:
    print_summary([_result("INFRA_ERROR"), _result("CORRECT", score=1.0)], "ollama/test:latest")
    out = capsys.readouterr().out
    assert "Infra errors: 1" in out


def test_print_summary_includes_query_too_expensive(capsys) -> None:
    print_summary(
        [_result("QUERY_TOO_EXPENSIVE"), _result("CORRECT", score=1.0)],
        "ollama/test:latest",
    )
    out = capsys.readouterr().out
    assert "Query too expensive: 1" in out


def test_print_comparison_includes_model_error_column(capsys) -> None:
    print_comparison(
        {
            "ollama/test:latest": [_result("MODEL_ERROR"), _result("PARSE_FAIL")],
        }
    )
    out = capsys.readouterr().out
    assert "ModelErr" in out
    assert "1" in out


def test_print_comparison_includes_infra_error_column(capsys) -> None:
    print_comparison(
        {
            "ollama/test:latest": [_result("INFRA_ERROR"), _result("PARSE_FAIL")],
        }
    )
    out = capsys.readouterr().out
    assert "InfraErr" in out


def test_print_comparison_includes_query_too_expensive_column(capsys) -> None:
    print_comparison(
        {
            "ollama/test:latest": [_result("QUERY_TOO_EXPENSIVE"), _result("PARSE_FAIL")],
        }
    )
    out = capsys.readouterr().out
    assert "QExp" in out


def test_write_combined_csv_writes_rows_for_all_models(tmp_path) -> None:
    output = tmp_path / "baseline_combined.csv"
    write_combined_csv(
        {
            "ollama/a:latest": [_result("CORRECT", score=1.0)],
            "ollama/b:latest": [_result("INFRA_ERROR")],
        },
        output,
    )
    with output.open() as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2
    assert {row["run_name"] for row in rows} == {"ollama/test:latest"}
    assert {row["requested_model"] for row in rows} == {"ollama/test:latest"}
    assert {row["resolved_model"] for row in rows} == {"ollama/test:latest"}
    assert {row["model"] for row in rows} == {"ollama/test:latest"}
    assert {row["model_thinking"] for row in rows} == {""}
    assert {row["outcome"] for row in rows} == {"CORRECT", "INFRA_ERROR"}


def test_write_summary_csv_writes_one_row_per_model(tmp_path) -> None:
    output = tmp_path / "baseline_summary.csv"
    write_summary_csv(
        {
            "ollama/a:latest": [_result("CORRECT", score=1.0), _result("PARSE_FAIL")],
            "ollama/b:latest": [_result("INFRA_ERROR")],
        },
        output,
    )
    with output.open() as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2
    by_model = {row["model"]: row for row in rows}
    assert by_model["ollama/a:latest"]["correct"] == "1"
    assert by_model["ollama/a:latest"]["parse_fails"] == "1"
    assert by_model["ollama/a:latest"]["completed_tasks"] == "2"
    assert by_model["ollama/a:latest"]["expected_tasks"] == "2"
    assert by_model["ollama/a:latest"]["run_complete"] == "True"
    assert by_model["ollama/a:latest"]["partial_result"] == "False"
    assert by_model["ollama/b:latest"]["infra_errors"] == "1"


def test_write_summary_csv_includes_tier4_and_tier5(tmp_path) -> None:
    output = tmp_path / "baseline_summary.csv"
    write_summary_csv(
        {
            "ollama/a:latest": [
                _result_for_tier(4, "CORRECT", score=1.0),
                _result_for_tier(5, "INCORRECT", score=0.0),
            ],
        },
        output,
    )
    with output.open() as f:
        row = next(csv.DictReader(f))
    assert row["tier4_correct"] == "1"
    assert row["tier4_total"] == "1"
    assert row["tier4_pct"] == "100"
    assert row["tier5_correct"] == "0"
    assert row["tier5_total"] == "1"
    assert row["tier5_pct"] == "0"


def test_write_summary_csv_flags_partial_results(tmp_path) -> None:
    output = tmp_path / "baseline_summary.csv"
    completed = _result("CORRECT", score=1.0)
    missing = _result("INFRA_ERROR")
    missing.partial_result = True
    write_summary_csv({"ollama/a:latest": [completed, missing]}, output)
    with output.open() as f:
        row = next(csv.DictReader(f))
    assert row["completed_tasks"] == "1"
    assert row["expected_tasks"] == "2"
    assert row["run_complete"] == "False"
    assert row["partial_result"] == "True"
    assert row["infra_errors"] == "1"


def test_write_summary_csv_tracks_query_too_expensive(tmp_path) -> None:
    output = tmp_path / "baseline_summary.csv"
    write_summary_csv(
        {
            "ollama/a:latest": [_result("QUERY_TOO_EXPENSIVE")],
        },
        output,
    )
    with output.open() as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["query_too_expensive"] == "1"


def test_write_summary_csv_includes_run_metadata(tmp_path) -> None:
    output = tmp_path / "baseline_summary.csv"
    result = _result("CORRECT", score=1.0)
    result.run_name = "gemma4-26b-32k"
    result.requested_model = "ollama/gemma4:26b"
    result.run_config = {"model": "ollama/gemma4:26b", "options": {"num_ctx": 32768}}
    write_summary_csv({"gemma4-26b-32k": [result]}, output)
    with output.open() as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    row = rows[0]
    assert row["run_name"] == "gemma4-26b-32k"
    assert row["requested_model"] == "ollama/gemma4:26b"
    assert row["resolved_model"] == "ollama/test:latest"
    assert row["options_json"] == '{"num_ctx": 32768}'


def test_write_combined_csv_preserves_model_thinking(tmp_path) -> None:
    output = tmp_path / "baseline_combined.csv"
    result = _result("CORRECT", score=1.0)
    result.model_response.thinking = "first think then answer"
    write_combined_csv({"ollama/a:latest": [result]}, output)
    with output.open() as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["model_thinking"] == "first think then answer"


def test_write_combined_csv_includes_telemetry_columns(tmp_path) -> None:
    output = tmp_path / "baseline_combined.csv"
    result = _result("CORRECT", score=1.0)
    result.task_wall_seconds = 2.5
    result.telemetry = {
        "task_wall_seconds": 2.5,
        "output_tokens_per_second": 12.34567,
        "tokens_per_second_source": "model_elapsed_seconds",
        "ollama_version": "0.12.6",
        "ollama_model_digest": "sha256:test",
        "ollama_model_context_length": 32768,
        "model_quantization_level": "Q4_K_M",
        "sample_ref": "telemetry/samples/test.jsonl:L1",
    }
    write_combined_csv({"ollama/a:latest": [result]}, output)
    with output.open() as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["task_wall_seconds"] == "2.50"
    assert rows[0]["output_tokens_per_second"] == "12.3457"
    assert rows[0]["tokens_per_second_source"] == "model_elapsed_seconds"
    assert rows[0]["ollama_version"] == "0.12.6"
    assert rows[0]["model_quantization_level"] == "Q4_K_M"
    assert rows[0]["telemetry_sample_ref"] == "telemetry/samples/test.jsonl:L1"
