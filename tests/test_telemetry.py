from __future__ import annotations

import csv
import json

import pytest

from ori.eval.adapter import ModelResponse
from ori.eval.bhce import CypherResult
from ori.eval.grader import GradeResult
from ori.eval.mcp_runtime import MCPRunMetadata
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


def test_mcp_telemetry_records_immutable_provenance_and_redacts_credentials(tmp_path) -> None:
    result = _result()
    result.mcp = MCPRunMetadata(
        mcp_launcher="uvx_git",
        mcp_source=(
            "git+https://github.com/mwnickerson/bloodhound_mcp@"
            "cdb17097e761c8a8622cb93bc3ba49a9e150bb6e"
        ),
        mcp_revision="cdb17097e761c8a8622cb93bc3ba49a9e150bb6e",
        mcp_executable="bloodhound-mcp",
        uv_version="uv 0.test",
        available_prompt_names=["bloodhound_assistant"],
        prompt_discovery_status="selected",
        available_resource_uris=["bloodhound://schema"],
        resource_discovery_status="listed",
    )
    output = tmp_path / "results" / "mock.csv"
    output.parent.mkdir()

    record_eval_telemetry(
        [result],
        output_path=output,
        model="mock/perfect",
        run_name="mock-mcp",
        requested_model="mock/perfect",
        run_config={
            "mcp_revision": result.mcp.mcp_revision,
            "unrelated_secret": "credential-sentinel-never-persist",
            "nested": {"api_key": "credential-sentinel-never-persist"},
        },
        model_base_url=None,
        enabled=True,
    )

    telemetry_root = output.parent / "telemetry"
    provenance_path = telemetry_root / "mcp_provenance.json"
    provenance = json.loads(provenance_path.read_text())
    assert provenance["mcp_revision"] == result.mcp.mcp_revision
    assert provenance["prompt_discovery_succeeded"] is True
    assert provenance["resource_discovery_succeeded"] is True
    sample = json.loads((telemetry_root / "samples" / "mock-mcp.jsonl").read_text())
    assert sample["run_config"]["unrelated_secret"] == "[REDACTED]"
    assert sample["run_config"]["nested"]["api_key"] == "[REDACTED]"
    assert "credential-sentinel-never-persist" not in "".join(
        path.read_text(errors="ignore") for path in telemetry_root.rglob("*") if path.is_file()
    )
    with (telemetry_root / "telemetry_summary.csv").open() as handle:
        row = next(csv.DictReader(handle))
    assert row["mcp_launcher"] == "uvx_git"
    assert row["resource_discovery_status"] == "listed"

    result.mcp.mcp_revision = "0" * 40
    with pytest.raises(RuntimeError, match="Refusing to overwrite"):
        record_eval_telemetry(
            [result],
            output_path=output,
            model="mock/perfect",
            run_name="mock-mcp",
            requested_model="mock/perfect",
            run_config={},
            model_base_url=None,
            enabled=True,
        )


def test_mcp_telemetry_falls_back_to_pinned_run_config_for_blank_metadata(tmp_path) -> None:
    result = _result()
    result.mcp = MCPRunMetadata()
    output = tmp_path / "results" / "mock.csv"
    output.parent.mkdir()
    revision = "cdb17097e761c8a8622cb93bc3ba49a9e150bb6e"
    run_config = {
        "mcp_launcher": "uvx_git",
        "mcp_source": ("git+https://github.com/mwnickerson/bloodhound_mcp@" + revision),
        "mcp_revision": revision,
        "mcp_executable": "bloodhound-mcp",
        "uv_version": "uv 0.test",
        "prompt_discovery_status": "not_requested",
        "resource_discovery_status": "not_requested",
    }

    record_eval_telemetry(
        [result],
        output_path=output,
        model="mock/perfect",
        run_name="mock-pinned",
        requested_model="mock/perfect",
        run_config=run_config,
        model_base_url=None,
        enabled=True,
    )

    provenance = json.loads((output.parent / "telemetry" / "mcp_provenance.json").read_text())
    assert provenance["mcp_launcher"] == "uvx_git"
    assert provenance["mcp_revision"] == revision
    assert provenance["mcp_executable"] == "bloodhound-mcp"
    assert provenance["prompt_discovery_status"] == "not_requested"
