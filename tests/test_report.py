from __future__ import annotations

import csv

from ori.eval.adapter import ModelResponse
from ori.eval.bhce import CypherResult
from ori.eval.grader import GradeResult
from ori.eval.mcp_runtime import MCPRunMetadata
from ori.eval.report import (
    classify_summary_health,
    print_comparison,
    print_summary,
    write_combined_csv,
    write_summary_csv,
)
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
    assert row["run_status"] == "partial"
    assert "completed_tasks=1<expected_tasks=2" in row["run_status_detail"]


def test_classify_summary_health_marks_fake_partial_summary_not_clean_complete() -> None:
    row = {
        "completed_tasks": "19",
        "expected_tasks": "43",
        "run_complete": "False",
        "partial_result": "True",
        "infra_errors": "26",
    }

    status, detail = classify_summary_health(row)

    assert status == "partial"
    assert "run_complete=False" in detail
    assert "partial_result=True" in detail
    assert "completed_tasks=19<expected_tasks=43" in detail
    assert "infra_errors=26" in detail


def test_write_combined_csv_includes_attempt_source_and_infra_subtype(tmp_path) -> None:
    output = tmp_path / "baseline_combined.csv"
    result = _result("INFRA_ERROR")
    result.mcp = MCPRunMetadata(
        resource_mode="on-demand",
        tool_loop="native-ollama",
        infra_error_subtype="batch_interrupted_missing_result",
    )
    result.partial_result = True
    result.attempt_number = 2
    result.result_source = "retry_interrupted_placeholder"

    write_combined_csv({"ollama/a:latest": [result]}, output)

    with output.open() as f:
        row = next(csv.DictReader(f))
    assert row["infra_error_subtype"] == "batch_interrupted_missing_result"
    assert row["attempt_number"] == "2"
    assert row["result_source"] == "retry_interrupted_placeholder"


def test_summary_uses_populated_mcp_provenance_after_initial_placeholder(tmp_path) -> None:
    output = tmp_path / "summary.csv"
    placeholder = _result("INFRA_ERROR")
    placeholder.mcp = MCPRunMetadata(infra_error_subtype="missing")
    populated = _result("CORRECT", score=1.0)
    populated.mcp = MCPRunMetadata(
        mcp_launcher="uvx_git",
        mcp_revision="a" * 40,
        mcp_executable="bloodhound-mcp",
        prompt_discovery_status="selected",
        resource_discovery_status="listed",
    )

    write_summary_csv({"model": [placeholder, populated]}, output)

    with output.open() as handle:
        row = next(csv.DictReader(handle))
    assert row["mcp_launcher"] == "uvx_git"
    assert row["mcp_revision"] == "a" * 40
    assert row["prompt_discovery_succeeded"] == "True"
    assert row["resource_discovery_succeeded"] == "True"


def test_reports_fall_back_to_pinned_run_config_for_blank_mcp_metadata(tmp_path) -> None:
    combined = tmp_path / "combined.csv"
    summary = tmp_path / "summary.csv"
    result = _result("INFRA_ERROR")
    result.mcp = MCPRunMetadata()
    revision = "a" * 40
    result.run_config = {
        "mcp_launcher": "uvx_git",
        "mcp_source": ("git+https://github.com/mwnickerson/bloodhound_mcp@" + revision),
        "mcp_revision": revision,
        "mcp_executable": "bloodhound-mcp",
        "uv_version": "uv 0.test",
        "prompt_discovery_status": "selected",
        "resource_discovery_status": "listed",
    }

    write_combined_csv({"model": [result]}, combined)
    write_summary_csv({"model": [result]}, summary)

    with combined.open() as handle:
        combined_row = next(csv.DictReader(handle))
    with summary.open() as handle:
        summary_row = next(csv.DictReader(handle))
    for row in (combined_row, summary_row):
        assert row["mcp_launcher"] == "uvx_git"
        assert row["mcp_revision"] == revision
        assert row["mcp_executable"] == "bloodhound-mcp"
        assert row["prompt_discovery_succeeded"] == "True"
        assert row["resource_discovery_succeeded"] == "True"


def test_write_summary_csv_tracks_query_too_expensive(tmp_path) -> None:
    output = tmp_path / "baseline_summary.csv"
    result = _result("QUERY_TOO_EXPENSIVE")
    result.model_result = CypherResult(
        success=False,
        error="rejected",
        failure_type="policy_rejected",
        failure_subtype="unbounded_wildcard_path_enumeration",
        query_executed=False,
        execution_attempts=0,
        query_fingerprint="abc123",
        safety_policy_version="bloodhound-cysql-direct-v3",
        safety_rule="unbounded_wildcard_path_enumeration",
        bhce_health_after="not_checked",
        circuit_state="closed",
    )
    write_summary_csv(
        {
            "ollama/a:latest": [result],
        },
        output,
    )
    with output.open() as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["query_too_expensive"] == "1"
    assert rows[0]["policy_rejections"] == "1"
    assert rows[0]["executed_direct_queries"] == "0"
    assert rows[0]["greedy_query_rate"] == "1.0"


def test_write_combined_csv_includes_direct_query_containment_fields(tmp_path) -> None:
    output = tmp_path / "baseline_combined.csv"
    result = _result("QUERY_TOO_EXPENSIVE")
    result.model_result = CypherResult(
        success=False,
        error="query timeout",
        failure_type="query_timeout",
        failure_subtype="bloodhound_query_timeout",
        query_executed=True,
        execution_attempts=1,
        query_fingerprint="fingerprint",
        safety_policy_version="bloodhound-cysql-direct-v3",
        safety_rule="allowed",
        bhce_health_after="healthy",
        circuit_state="closed",
    )

    write_combined_csv({"ollama/a:latest": [result]}, output)

    with output.open() as f:
        row = next(csv.DictReader(f))
    assert row["query_executed"] == "True"
    assert row["query_attempts"] == "1"
    assert row["query_fingerprint"] == "fingerprint"
    assert row["safety_policy_version"] == "bloodhound-cysql-direct-v3"
    assert row["safety_rule"] == "allowed"
    assert row["bhce_health_after"] == "healthy"
    assert row["circuit_state"] == "closed"


def test_write_summary_csv_includes_run_metadata(tmp_path) -> None:
    output = tmp_path / "baseline_summary.csv"
    result = _result("CORRECT", score=1.0)
    result.run_name = "gemma4-26b-32k"
    result.requested_model = "ollama/gemma4:26b"
    result.run_config = {
        "model": "ollama/gemma4:26b",
        "options": {"num_ctx": 32768},
        "run_index": 2,
        "runs_per_model": 5,
    }
    write_summary_csv({"gemma4-26b-32k": [result]}, output)
    with output.open() as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    row = rows[0]
    assert row["run_name"] == "gemma4-26b-32k"
    assert row["requested_model"] == "ollama/gemma4:26b"
    assert row["resolved_model"] == "ollama/test:latest"
    assert row["options_json"] == '{"num_ctx": 32768}'
    assert row["run_index"] == "2"
    assert row["runs_per_model"] == "5"


def test_write_summary_csv_reports_tier6_results(tmp_path) -> None:
    output = tmp_path / "baseline_summary.csv"
    result = _result("CORRECT", score=1.0)
    result.task.tier = 6

    write_summary_csv({"tier6-model": [result]}, output)

    with output.open() as f:
        row = next(csv.DictReader(f))
    assert row["tier6_correct"] == "1"
    assert row["tier6_total"] == "1"
    assert row["tier6_pct"] == "100"


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
