from __future__ import annotations

from pathlib import Path

from ori.eval.adapter import ModelResponse
from ori.eval.bhce import CypherResult
from ori.eval.inspect_runtime import _resolve_model_base_url, run_eval_with_inspect
from ori.eval.tasks import Task


class FakeBHCEClient:
    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self) -> FakeBHCEClient:
        return self

    async def __aexit__(self, *args) -> None:
        return None

    async def run_cypher(self, query: str) -> CypherResult:
        if "JDOE@TEST.LOCAL" in query and "DC01.TEST.LOCAL" in query:
            return CypherResult(
                success=True,
                nodes=[
                    {"properties": {"name": "JDOE@TEST.LOCAL"}},
                    {"properties": {"name": "DC01.TEST.LOCAL"}},
                ],
                node_names={"JDOE@TEST.LOCAL", "DC01.TEST.LOCAL"},
                raw={},
            )
        if "FAKE_SENTINEL_NODE" in query:
            return CypherResult(
                success=True,
                nodes=[{"properties": {"name": "FAKE_SENTINEL_NODE@TEST.LOCAL"}}],
                node_names={"FAKE_SENTINEL_NODE@TEST.LOCAL"},
                raw={},
            )
        return CypherResult(success=False, error=f"unexpected query: {query}")

    async def run_cypher_resilient(
        self,
        query: str,
        *,
        recovery_timeout_seconds: float = 90.0,
        recovery_poll_interval: float = 5.0,
    ) -> CypherResult:
        return await self.run_cypher(query)

    async def get_all_node_names(self) -> set[str]:
        return {"JDOE@TEST.LOCAL", "DC01.TEST.LOCAL"}


def _task() -> Task:
    return Task(
        id="t1_admin_to-01",
        template_id="t1_admin_to",
        tier=1,
        category="path_finding",
        question="What is the attack path from JDOE@TEST.LOCAL to DC01.TEST.LOCAL?",
        reference_cypher=(
            "MATCH p=shortestPath((u:User {name: 'JDOE@TEST.LOCAL'})-[*1..]->"
            "(c:Computer {name: 'DC01.TEST.LOCAL'})) RETURN p"
        ),
        grade_mode="path_exists",
        metadata={"domain": "TEST.LOCAL"},
    )


async def _run(model: str, tmp_path: Path, monkeypatch) -> list:
    monkeypatch.setattr("ori.eval.inspect_runtime.BHCEClient", FakeBHCEClient)
    bhce = FakeBHCEClient()
    return await run_eval_with_inspect(
        tasks=[_task()],
        model=model,
        bhce=bhce,
        output_path=tmp_path / "results.csv",
        concurrency=1,
        bhce_domain="bloodhound.test.local",
    )


def test_run_eval_with_inspect_mock_perfect(tmp_path: Path, monkeypatch) -> None:
    import asyncio

    results = asyncio.run(_run("mock/perfect", tmp_path, monkeypatch))
    assert len(results) == 1
    result = results[0]
    assert result.grade.outcome == "CORRECT"
    assert result.model_response.cypher == _task().reference_cypher
    assert result.inspect is not None
    assert result.inspect.model_calls == 1
    assert result.inspect.log_location


def test_run_eval_with_inspect_mock_hallucinate(tmp_path: Path, monkeypatch) -> None:
    import asyncio

    results = asyncio.run(_run("mock/hallucinate", tmp_path, monkeypatch))
    assert len(results) == 1
    result = results[0]
    assert result.grade.outcome == "HALLUCINATION"
    assert result.model_response.parse_stage == "mock_hallucinate"


def test_run_eval_with_inspect_prints_per_task_outcome(tmp_path: Path, monkeypatch, capsys) -> None:
    import asyncio

    results = asyncio.run(_run("mock/perfect", tmp_path, monkeypatch))
    assert len(results) == 1
    out = capsys.readouterr().out
    assert "[1/1] t1_admin_to-01" in out
    assert "→ CORRECT (score=1.0)" in out


def test_run_eval_with_inspect_ollama_uses_adapter_path_and_preserves_thinking(
    tmp_path: Path, monkeypatch
) -> None:
    import asyncio

    monkeypatch.setattr("ori.eval.inspect_runtime.BHCEClient", FakeBHCEClient)

    async def fake_call_model(*args, **kwargs):
        return ModelResponse(
            raw_text="MATCH p=shortestPath((u:User {name: 'JDOE@TEST.LOCAL'})-[*1..]->(c:Computer {name: 'DC01.TEST.LOCAL'})) RETURN p",  # noqa: E501
            cypher="MATCH p=shortestPath((u:User {name: 'JDOE@TEST.LOCAL'})-[*1..]->(c:Computer {name: 'DC01.TEST.LOCAL'})) RETURN p",  # noqa: E501
            parse_stage="bare_match",
            tokens_input=12,
            tokens_output=8,
            elapsed_seconds=1.5,
            model="ori-qwen35-9b-32k",
            thinking="Reasoning about BloodHound schema first",
        )

    monkeypatch.setattr("ori.eval.inspect_runtime.call_model", fake_call_model)
    bhce = FakeBHCEClient()
    results = asyncio.run(
        run_eval_with_inspect(
            tasks=[_task()],
            model="ollama/ori-qwen35-9b-32k",
            bhce=bhce,
            output_path=tmp_path / "results.csv",
            concurrency=1,
            bhce_domain="bloodhound.test.local",
        )
    )

    assert len(results) == 1
    result = results[0]
    assert result.grade.outcome == "CORRECT"
    assert result.model_response.model == "ori-qwen35-9b-32k"
    assert result.model_response.thinking == "Reasoning about BloodHound schema first"
    assert result.model_response.cypher is not None


def test_resolve_model_base_url_adds_v1_for_ollama(monkeypatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    assert _resolve_model_base_url("ollama/qwen3:latest", None) == "http://localhost:11434/v1"


def test_resolve_model_base_url_prefers_explicit_base_url_for_ollama(monkeypatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://env-host:11434")
    assert (
        _resolve_model_base_url("ollama/qwen3:latest", "http://yaml-host:11434")
        == "http://yaml-host:11434/v1"
    )


def test_resolve_model_base_url_preserves_existing_v1_for_ollama(monkeypatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    assert _resolve_model_base_url("ollama/qwen3:latest", None) == "http://localhost:11434/v1"
