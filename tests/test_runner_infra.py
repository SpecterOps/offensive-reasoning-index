from __future__ import annotations

import json
from pathlib import Path

from ori.eval.adapter import ModelResponse
from ori.eval.bhce import BHHealthResult, CypherResult
from ori.eval.grader import GradeResult
from ori.eval.runner import EvalResult, run_eval_cli_bare
from ori.eval.tasks import Task


def _task() -> Task:
    return Task(
        id="t1",
        template_id="global",
        tier=1,
        category="enumeration",
        question="q",
        reference_cypher="MATCH (n:Domain) RETURN n LIMIT 1",
        grade_mode="node_set",
        metadata={"domain": "TEST.LOCAL"},
    )


class FakeBHCEClient:
    def __init__(self, **kwargs) -> None:
        self.init_kwargs = kwargs
        self.health_calls: list[tuple[float, float, str]] = []

    async def __aenter__(self) -> "FakeBHCEClient":
        return self

    async def __aexit__(self, *args) -> None:
        return None

    async def wait_until_healthy(
        self,
        timeout_seconds: float = 90.0,
        poll_interval: float = 5.0,
        query: str = "MATCH (n:Domain) RETURN n LIMIT 1",
    ) -> BHHealthResult:
        self.health_calls.append((timeout_seconds, poll_interval, query))
        return BHHealthResult(ok=True, detail="query succeeded", query=query, classification="ok")


def _result(outcome: str) -> EvalResult:
    task = _task()
    model_response = ModelResponse(
        raw_text="MATCH (n) RETURN n",
        cypher="MATCH (n) RETURN n",
        parse_stage="bare_match",
        tokens_input=0,
        tokens_output=0,
        elapsed_seconds=0.0,
        model="ollama/test:latest",
        error=None,
    )
    grade = GradeResult(
        score=1.0 if outcome == "CORRECT" else 0.0,
        outcome=outcome,
        hallucination=False,
        details=outcome,
    )
    cypher = CypherResult(success=True, nodes=[], node_names=set(), raw={})
    return EvalResult(
        task=task,
        model_response=model_response,
        grade=grade,
        ref_result=cypher,
        model_result=cypher,
    )


def test_run_eval_cli_bare_reruns_once_on_infra(tmp_path: Path, monkeypatch) -> None:
    import asyncio

    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({"domain": "TEST.LOCAL"}))

    monkeypatch.setattr("ori.eval.runner.generate_tasks", lambda manifest: [_task()])
    monkeypatch.setattr("ori.eval.runner.BHCEClient", lambda **kwargs: FakeBHCEClient(**kwargs))

    calls = {"count": 0}

    async def fake_run_eval_with_inspect(**kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return [_result("INFRA_ERROR")]
        return [_result("CORRECT")]

    monkeypatch.setattr("ori.eval.runner.run_eval_with_inspect", fake_run_eval_with_inspect)

    results = asyncio.run(
        run_eval_cli_bare(
            manifest_path=manifest_path,
            model="ollama/test:latest",
            output_path=tmp_path / "results.csv",
            max_model_reruns_on_infra=1,
        )
    )

    assert calls["count"] == 2
    assert len(results) == 1
    assert results[0].grade.outcome == "CORRECT"


def test_run_eval_cli_bare_threads_health_and_base_url(tmp_path: Path, monkeypatch) -> None:
    import asyncio

    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({"domain": "TEST.LOCAL"}))

    fake_bh = FakeBHCEClient()
    monkeypatch.setattr("ori.eval.runner.generate_tasks", lambda manifest: [_task()])
    monkeypatch.setattr("ori.eval.runner.BHCEClient", lambda **kwargs: fake_bh)

    captured: dict = {}

    async def fake_run_eval_with_inspect(**kwargs):
        captured.update(kwargs)
        return [_result("CORRECT")]

    monkeypatch.setattr("ori.eval.runner.run_eval_with_inspect", fake_run_eval_with_inspect)

    asyncio.run(
        run_eval_cli_bare(
            manifest_path=manifest_path,
            model="ollama/test:latest",
            output_path=tmp_path / "results.csv",
            model_base_url="http://127.0.0.1:11434/v1",
            health_timeout_seconds=12.0,
            health_poll_interval=0.25,
        )
    )

    assert fake_bh.health_calls == [(12.0, 0.25, "MATCH (n:Domain) RETURN n LIMIT 1")]
    assert captured["base_url"] == "http://127.0.0.1:11434/v1"


def test_run_eval_cli_bare_threads_explicit_bhce_url_kwargs(tmp_path: Path, monkeypatch) -> None:
    import asyncio

    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({"domain": "TEST.LOCAL"}))

    monkeypatch.setattr("ori.eval.runner.generate_tasks", lambda manifest: [_task()])
    created: dict = {}

    def make_bh(**kwargs):
        client = FakeBHCEClient(**kwargs)
        created["client"] = client
        return client

    monkeypatch.setattr("ori.eval.runner.BHCEClient", make_bh)

    async def fake_run_eval_with_inspect(**kwargs):
        return [_result("CORRECT")]

    monkeypatch.setattr("ori.eval.runner.run_eval_with_inspect", fake_run_eval_with_inspect)

    results = asyncio.run(
        run_eval_cli_bare(
            manifest_path=manifest_path,
            model="ollama/test:latest",
            output_path=tmp_path / "results.csv",
            bhce_url="http://bh.example.local:8080",
        )
    )

    assert len(results) == 1
    assert created["client"].init_kwargs == {
        "domain": "bh.example.local",
        "scheme": "http",
        "port": 8080,
    }
