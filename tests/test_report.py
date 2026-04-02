from __future__ import annotations

from ori.eval.adapter import ModelResponse
from ori.eval.bhce import CypherResult
from ori.eval.grader import GradeResult
from ori.eval.report import print_comparison, print_summary
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


def test_print_summary_includes_model_errors(capsys) -> None:
    print_summary([_result("MODEL_ERROR"), _result("CORRECT", score=1.0)], "ollama/test:latest")
    out = capsys.readouterr().out
    assert "Model errors: 1" in out


def test_print_comparison_includes_model_error_column(capsys) -> None:
    print_comparison(
        {
            "ollama/test:latest": [_result("MODEL_ERROR"), _result("PARSE_FAIL")],
        }
    )
    out = capsys.readouterr().out
    assert "ModelErr" in out
    assert "1" in out
