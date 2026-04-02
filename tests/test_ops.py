from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from ori.cli import main
from ori.eval.bhce import CypherResult
from ori.eval.ops import (
    SMOKE_EXPECTATIONS,
    SmokeCheck,
    _edge_query,
    print_smoke_eval,
    print_verify_ingest,
    verify_ingest,
)
from ori.eval.runner import EvalResult
from ori.eval.tasks import Task


class FakeBHCEClient:
    async def __aenter__(self) -> "FakeBHCEClient":
        return self

    async def __aexit__(self, *args) -> None:
        return None

    async def run_cypher(self, query: str) -> CypherResult:
        if "MATCH (n:User)" in query:
            return CypherResult(success=True, nodes=[{}, {}], node_names=set(), raw={})
        if "MATCH (n:Computer)" in query:
            return CypherResult(success=True, nodes=[{}], node_names=set(), raw={})
        if "MATCH (n:Group)" in query:
            return CypherResult(success=True, nodes=[{}, {}, {}], node_names=set(), raw={})
        if "MATCH (n:OU)" in query:
            return CypherResult(success=True, nodes=[{}], node_names=set(), raw={})
        if "MATCH (n:Domain)" in query:
            return CypherResult(success=True, nodes=[{}], node_names=set(), raw={})
        if "coalesce(a.objectid" in query:
            return CypherResult(success=True, nodes=[{}, {}], node_names=set(), raw={})
        if "RETURN p" in query:
            return CypherResult(success=True, nodes=[{}, {}], node_names=set(), raw={})
        return CypherResult(success=False, error=query)


def _manifest(tmp_path: Path) -> Path:
    manifest = {
        "domain": "TEST.LOCAL",
        "stats": {
            "users": 2,
            "computers": 1,
            "groups": 3,
            "ous": 1,
        },
        "planted_paths": [
            {
                "template_id": "t1_admin_to",
                "source_name": "JDOE@TEST.LOCAL",
                "target_name": "DC01.TEST.LOCAL",
                "verification_cypher": "MATCH p=(a)-[:AdminTo]->(b) RETURN p",
                "path_edges": [
                    {
                        "source": "S-1-5-21-1-2-3-1100",
                        "edge": "MemberOf",
                        "target": "S-1-5-21-1-2-3-512",
                    }
                ],
            }
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(__import__("json").dumps(manifest))
    return path


def test_edge_query_uses_objectid_coalesce() -> None:
    query = _edge_query("SRC", "AdminTo", "DST")
    assert "[:AdminTo]" in query
    assert "coalesce(a.objectid, a.objectId) = 'SRC'" in query
    assert "coalesce(b.objectid, b.objectId) = 'DST'" in query


def test_verify_ingest_happy_path(tmp_path: Path, monkeypatch) -> None:
    import asyncio

    monkeypatch.setattr("ori.eval.ops.BHCEClient", lambda domain=None: FakeBHCEClient())
    result = asyncio.run(verify_ingest(_manifest(tmp_path)))
    assert result.ok is True
    assert len(result.path_checks) == 1
    assert result.path_checks[0].ok is True


def test_cli_verify_ingest(tmp_path: Path, monkeypatch) -> None:
    import asyncio

    async def fake_verify(manifest_path: Path, bhce_url: str | None = None):
        return asyncio.run(verify_ingest(manifest_path, bhce_url))

    monkeypatch.setattr("ori.eval.ops.BHCEClient", lambda domain=None: FakeBHCEClient())
    runner = CliRunner()
    result = runner.invoke(main, ["verify-ingest", "-m", str(_manifest(tmp_path))])
    assert result.exit_code == 0
    assert "INGEST CHECK: PASS" in result.output


def test_smoke_check_passes_only_single_expected_outcome() -> None:
    good = SmokeCheck(
        model="mock/perfect",
        expected="CORRECT",
        actual_counts={"CORRECT": 5},
    )
    bad = SmokeCheck(
        model="mock/perfect",
        expected="CORRECT",
        actual_counts={"CORRECT": 4, "INCORRECT": 1},
    )
    assert good.ok is True
    assert bad.ok is False


def test_cli_smoke_eval(tmp_path: Path, monkeypatch) -> None:
    import asyncio
    from ori.eval.ops import SmokeEvalResult
    from ori.eval.adapter import ModelResponse
    from ori.eval.grader import GradeResult

    async def fake_smoke_eval(manifest_path: Path, output_dir: Path, bhce_url: str | None = None):
        task = Task(
            id="t1",
            template_id="t1_admin_to",
            tier=1,
            category="path_finding",
            question="q",
            reference_cypher="MATCH (n) RETURN n",
            grade_mode="path_exists",
            metadata={"domain": "TEST.LOCAL"},
        )
        results_by_model = {}
        checks = []
        for model, expected in SMOKE_EXPECTATIONS.items():
            results_by_model[model] = [
                EvalResult(
                    task=task,
                    model_response=ModelResponse(
                        raw_text="MATCH (n) RETURN n",
                        cypher="MATCH (n) RETURN n",
                        parse_stage="bare_match",
                        tokens_input=0,
                        tokens_output=0,
                        elapsed_seconds=0.0,
                        model=model,
                    ),
                    grade=GradeResult(
                        score=1.0 if expected == "CORRECT" else 0.0,
                        outcome=expected,
                        hallucination=expected == "HALLUCINATION",
                        details="ok",
                    ),
                    ref_result=CypherResult(success=True),
                    model_result=CypherResult(success=True),
                )
            ]
            checks.append(SmokeCheck(model=model, expected=expected, actual_counts={expected: 1}))
        return SmokeEvalResult(checks=checks, results_by_model=results_by_model)

    monkeypatch.setattr("ori.eval.ops.run_smoke_eval", fake_smoke_eval)
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["smoke-eval", "-m", str(_manifest(tmp_path)), "-o", str(tmp_path / "smoke")],
    )
    assert result.exit_code == 0
    assert "SMOKE TEST: PASS" in result.output


def test_print_helpers(capsys) -> None:
    from ori.eval.ops import (
        CountCheck,
        PathCheck,
        SmokeEvalResult,
        VerifyIngestResult,
    )

    verify = VerifyIngestResult(
        count_checks=[CountCheck("users", 2, 2)],
        path_checks=[PathCheck("t1_admin_to", True, "J", "D", [])],
    )
    print_verify_ingest(verify)
    out = capsys.readouterr().out
    assert "INGEST CHECK: PASS" in out

    smoke = SmokeEvalResult(
        checks=[SmokeCheck("mock/perfect", "CORRECT", {"CORRECT": 1})],
        results_by_model={"mock/perfect": []},
    )
    print_smoke_eval(smoke)
    out = capsys.readouterr().out
    assert "SMOKE TEST: PASS" in out
