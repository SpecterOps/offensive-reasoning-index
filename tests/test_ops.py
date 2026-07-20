from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from ori.cli import _build_run_specs, main
from ori.eval.bhce import BHHealthResult, CypherResult
from ori.eval.ops import (
    SMOKE_EXPECTATIONS,
    PreflightResult,
    SmokeCheck,
    _edge_query,
    print_preflight,
    print_smoke_eval,
    print_verify_bh_health,
    print_verify_ingest,
    verify_bh_health,
    verify_ingest,
)
from ori.eval.runner import EvalResult
from ori.eval.tasks import Task


class FakeBHCEClient:
    async def __aenter__(self) -> FakeBHCEClient:
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

    async def run_cypher_resilient(
        self,
        query: str,
        *,
        recovery_timeout_seconds: float = 90.0,
        recovery_poll_interval: float = 5.0,
    ) -> CypherResult:
        return await self.run_cypher(query)

    async def wait_until_healthy(
        self,
        timeout_seconds: float = 60.0,
        poll_interval: float = 5.0,
        query: str = "MATCH (n:Domain) RETURN n LIMIT 1",
    ) -> BHHealthResult:
        return BHHealthResult(
            ok=True,
            detail="query succeeded",
            query=query,
            classification="ok",
        )


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


def test_verify_ingest_checks_supporting_edges(tmp_path: Path, monkeypatch) -> None:
    import asyncio
    import json

    manifest_path = _manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text())
    manifest["planted_paths"][0]["supporting_edges"] = [
        {"source": "DOMAIN-A", "edge": "TrustedBy", "target": "DOMAIN-B"}
    ]
    manifest_path.write_text(json.dumps(manifest))
    monkeypatch.setattr("ori.eval.ops.BHCEClient", lambda domain=None: FakeBHCEClient())

    result = asyncio.run(verify_ingest(manifest_path))

    assert result.ok is True
    assert [edge.edge_type for edge in result.path_checks[0].edge_checks] == [
        "MemberOf",
        "TrustedBy",
    ]


def test_verify_ingest_requires_negative_control_path_to_be_absent(
    tmp_path: Path, monkeypatch
) -> None:
    import asyncio
    import json

    class NegativeControlBHCEClient(FakeBHCEClient):
        async def run_cypher(self, query: str) -> CypherResult:
            if "RETURN p" in query and "coalesce(a.objectid" not in query:
                return CypherResult(success=True, nodes=[], node_names=set(), raw={})
            return await super().run_cypher(query)

    manifest_path = _manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text())
    manifest["planted_paths"][0]["negative_control"] = True
    manifest_path.write_text(json.dumps(manifest))
    monkeypatch.setattr("ori.eval.ops.BHCEClient", lambda domain=None: NegativeControlBHCEClient())

    result = asyncio.run(verify_ingest(manifest_path))

    assert result.ok is True
    assert result.path_checks[0].found is False
    assert result.path_checks[0].expected_found is False


def test_verify_ingest_rejects_negative_control_when_verification_query_fails(
    tmp_path: Path, monkeypatch
) -> None:
    import asyncio
    import json

    class FailedNegativeControlBHCEClient(FakeBHCEClient):
        async def run_cypher(self, query: str) -> CypherResult:
            if "RETURN p" in query and "coalesce(a.objectid" not in query:
                return CypherResult(
                    success=False,
                    nodes=[],
                    node_names=set(),
                    raw={},
                    error="verification query failed",
                )
            return await super().run_cypher(query)

    manifest_path = _manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text())
    manifest["planted_paths"][0]["negative_control"] = True
    manifest_path.write_text(json.dumps(manifest))
    monkeypatch.setattr(
        "ori.eval.ops.BHCEClient", lambda domain=None: FailedNegativeControlBHCEClient()
    )

    result = asyncio.run(verify_ingest(manifest_path))

    assert result.ok is False
    assert result.path_checks[0].found is False
    assert result.path_checks[0].query_succeeded is False
    assert result.path_checks[0].error == "verification query failed"


def test_cli_verify_ingest(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("ori.eval.ops.BHCEClient", lambda domain=None: FakeBHCEClient())
    runner = CliRunner()
    result = runner.invoke(main, ["verify-ingest", "-m", str(_manifest(tmp_path))])
    assert result.exit_code == 0
    assert "INGEST CHECK: PASS" in result.output


def test_verify_bh_health_happy_path(monkeypatch) -> None:
    import asyncio

    monkeypatch.setattr("ori.eval.ops.BHCEClient", lambda domain=None: FakeBHCEClient())
    result = asyncio.run(verify_bh_health())
    assert result.ok is True
    assert result.classification == "ok"


def test_verify_bh_health_threads_explicit_bhce_url(monkeypatch) -> None:
    import asyncio

    captured: dict = {}

    def make_client(**kwargs):
        captured.update(kwargs)
        return FakeBHCEClient()

    monkeypatch.setattr("ori.eval.ops.BHCEClient", make_client)
    result = asyncio.run(verify_bh_health("http://bh.example.local:8080"))
    assert result.ok is True
    assert captured == {
        "domain": "bh.example.local",
        "scheme": "http",
        "port": 8080,
    }


def test_cli_verify_bh_health(monkeypatch) -> None:
    monkeypatch.setattr("ori.eval.ops.BHCEClient", lambda domain=None: FakeBHCEClient())
    runner = CliRunner()
    result = runner.invoke(
        main, ["verify-bh-health", "--timeout", "0.1", "--poll-interval", "0.01"]
    )
    assert result.exit_code == 0
    assert "BH HEALTH: PASS" in result.output


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
    from ori.eval.adapter import ModelResponse
    from ori.eval.grader import GradeResult
    from ori.eval.ops import SmokeEvalResult

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


def test_cli_preflight(tmp_path: Path, monkeypatch) -> None:
    from ori.eval.ops import (
        CountCheck,
        PathCheck,
        SmokeEvalResult,
        VerifyBHHealthResult,
        VerifyIngestResult,
    )

    async def fake_preflight(
        manifest_path: Path,
        output_dir: Path,
        bhce_url: str | None = None,
        timeout_seconds: float = 60.0,
        poll_interval: float = 5.0,
    ):
        return PreflightResult(
            health=VerifyBHHealthResult(
                ok=True,
                detail="query succeeded",
                classification="ok",
                query="MATCH (n:Domain) RETURN n LIMIT 1",
            ),
            ingest=VerifyIngestResult(
                count_checks=[CountCheck("users", 2, 2)],
                path_checks=[PathCheck("t1_admin_to", True, "J", "D", [])],
            ),
            smoke=SmokeEvalResult(
                checks=[SmokeCheck("mock/perfect", "CORRECT", {"CORRECT": 1})],
                results_by_model={"mock/perfect": []},
            ),
        )

    monkeypatch.setattr("ori.eval.ops.run_preflight", fake_preflight)
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "preflight",
            "-m",
            str(_manifest(tmp_path)),
            "-o",
            str(tmp_path / "smoke"),
            "--timeout",
            "0.1",
            "--poll-interval",
            "0.01",
        ],
    )
    assert result.exit_code == 0
    assert "PREFLIGHT: PASS" in result.output


def test_cli_eval_parses_ollama_options(tmp_path: Path, monkeypatch) -> None:
    manifest = _manifest(tmp_path)
    captured: dict = {}

    async def fake_run_eval_cli(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("ori.eval.runner.run_eval_cli", fake_run_eval_cli)
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "eval",
            "-m",
            str(manifest),
            "--model",
            "ollama/gemma4:e4b",
            "-o",
            str(tmp_path / "out.csv"),
            "--ollama-option",
            "num_ctx=16384",
            "--ollama-option",
            "temperature=0.2",
        ],
    )
    assert result.exit_code == 0
    assert captured["ollama_options"] == {"num_ctx": 16384, "temperature": 0.2}
    assert (
        captured["run_name"] == "ollama/gemma4:e4b [options.num_ctx=16384, options.temperature=0.2]"
    )
    assert captured["run_config"] == {
        "model": "ollama/gemma4:e4b",
        "options": {"num_ctx": 16384, "temperature": 0.2},
    }


def test_cli_eval_mcp_parses_ollama_options(tmp_path: Path, monkeypatch) -> None:
    manifest = _manifest(tmp_path)
    mcp_dir = tmp_path / "bloodhound-mcp"
    mcp_dir.mkdir()
    captured: dict = {}

    async def fake_run_eval_mcp_cli(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("ori.eval.runner.run_eval_mcp_cli", fake_run_eval_mcp_cli)
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "eval-mcp",
            "-m",
            str(manifest),
            "--model",
            "ollama/gemma4:e4b",
            "-o",
            str(tmp_path / "out.csv"),
            "--mcp-dir",
            str(mcp_dir),
            "--resource-mode",
            "on-demand",
            "--ollama-option",
            "num_ctx=16384",
            "--ollama-option",
            "temperature=0.2",
        ],
    )
    assert result.exit_code == 0
    assert captured["ollama_options"] == {"num_ctx": 16384, "temperature": 0.2}
    assert captured["resource_mode"] == "on-demand"
    assert captured["mcp_tool_loop"] == "auto"
    assert captured["openai_compat_telemetry_adapter"] == "auto"
    assert (
        captured["run_name"] == "ollama/gemma4:e4b [options.num_ctx=16384, options.temperature=0.2]"
    )
    assert captured["run_config"] == {
        "max_steps": 12,
        "mcp_tool_loop": "auto",
        "openai_compat_telemetry_adapter": "auto",
        "mcp_ollama_read_timeout_seconds": 900.0,
        "model": "ollama/gemma4:e4b",
        "options": {"num_ctx": 16384, "temperature": 0.2},
        "resource_mode": "on-demand",
    }


def test_build_run_specs_uses_explicit_name_and_config_identity(tmp_path: Path) -> None:
    models_file = tmp_path / "models.yaml"
    models_file.write_text(
        """
defaults:
  concurrency: 1
models:
  - name: gemma4-26b-32k
    model: ollama/gemma4:26b
    options:
      num_ctx: 32768
"""
    )
    specs = _build_run_specs(models=(), models_file=str(models_file))
    assert len(specs) == 1
    spec = specs[0]
    assert spec.run_name == "gemma4-26b-32k"
    assert spec.requested_model == "ollama/gemma4:26b"
    assert spec.ollama_options == {"num_ctx": 32768}
    assert spec.config_identity == {
        "model": "ollama/gemma4:26b",
        "options": {"num_ctx": 32768},
    }


def test_build_run_specs_supports_provider_model_shorthand(tmp_path: Path) -> None:
    models_file = tmp_path / "models.yaml"
    models_file.write_text(
        """
models:
  - name: codex-gpt-5-5
    provider: codex
    model: gpt-5.5
    mcp_tool_loop: native-openai-compatible
"""
    )

    spec = _build_run_specs(models=(), models_file=str(models_file))[0]

    assert spec.run_name == "codex-gpt-5-5"
    assert spec.requested_model == "codex/gpt-5.5"
    assert spec.mcp_tool_loop == "native-openai-compatible"


def test_run_with_model_matrix_config_runs_direct_and_mcp(tmp_path: Path, monkeypatch) -> None:
    manifest = _manifest(tmp_path)
    config = tmp_path / "models.yaml"
    config.write_text(
        """
modes: [direct, mcp]
output_dir: out

defaults:
  concurrency: 1
  mcp:
    mcp_dir: bloodhound-mcp
    max_steps: 9
    tool_loop: auto

models:
  - name: local-qwen
    provider: openai-compat
    model: qwen-fast
    model_base_url: http://127.0.0.1:8080/v1
    mcp_tool_loop: native-openai-compatible
  - name: anthropic-sonnet
    provider: anthropic
    model: claude-sonnet-4-5
"""
    )
    (tmp_path / "bloodhound-mcp").mkdir()
    captured: dict[str, dict] = {}

    async def fake_direct(**kwargs):
        captured["direct"] = kwargs
        return []

    async def fake_mcp(**kwargs):
        captured["mcp"] = kwargs
        return []

    monkeypatch.setattr("ori.cli._run_baseline_with_specs", fake_direct)
    monkeypatch.setattr("ori.cli._run_baseline_mcp_with_specs", fake_mcp)
    monkeypatch.setattr("ori.eval.report.write_combined_csv", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("ori.eval.report.write_summary_csv", lambda *_args, **_kwargs: None)

    result = CliRunner().invoke(
        main,
        ["run", "--config", str(config), "--manifest", str(manifest)],
    )

    assert result.exit_code == 0, result.output
    assert [spec.run_name for spec in captured["direct"]["run_specs"]] == [
        "local-qwen",
        "anthropic-sonnet",
    ]
    assert [spec.requested_model for spec in captured["mcp"]["run_specs"]] == [
        "openai-compat/qwen-fast",
        "anthropic/claude-sonnet-4-5",
    ]
    assert captured["direct"]["output_dir"] == tmp_path / "out" / "direct"
    assert captured["mcp"]["output_dir"] == tmp_path / "out" / "mcp"
    assert captured["mcp"]["mcp_dir"] == tmp_path / "bloodhound-mcp"
    assert captured["mcp"]["max_steps"] == 9


def test_build_run_specs_preserves_model_base_url_and_max_steps(tmp_path: Path) -> None:
    models_file = tmp_path / "models.yaml"
    models_file.write_text(
        """
defaults:
  concurrency: 1
models:
  - name: gemma4-26b-32k
    model: ollama/gemma4:26b
    model_base_url: http://127.0.0.1:11434/v1
    max_steps: 24
    mcp_tool_loop: native-openai-compatible
    openai_compat_telemetry_adapter: lm-studio
    mcp_ollama_read_timeout_seconds: 1200
    options:
      num_ctx: 32768
"""
    )
    spec = _build_run_specs(models=(), models_file=str(models_file))[0]
    assert spec.model_base_url == "http://127.0.0.1:11434/v1"
    assert spec.max_steps == 24
    assert spec.mcp_tool_loop == "native-openai-compatible"
    assert spec.openai_compat_telemetry_adapter == "lm-studio"
    assert spec.mcp_ollama_read_timeout_seconds == 1200
    assert spec.config_identity == {
        "max_steps": 24,
        "mcp_tool_loop": "native-openai-compatible",
        "openai_compat_telemetry_adapter": "lm-studio",
        "mcp_ollama_read_timeout_seconds": 1200,
        "model": "ollama/gemma4:26b",
        "model_base_url": "http://127.0.0.1:11434/v1",
        "options": {"num_ctx": 32768},
    }


def test_death_star_models_file_uses_openai_compat_qwen_fast() -> None:
    spec = _build_run_specs(models=(), models_file="models-death-star.yaml")[0]

    assert spec.run_name == "death-star-qwen-fast-64k"
    assert spec.requested_model == "openai-compat/qwen-fast"
    assert spec.model_base_url == "http://death-star:8080/v1"
    assert spec.concurrency == 1
    assert spec.mcp_tool_loop == "native-openai-compatible"
    assert spec.openai_compat_telemetry_adapter == "llama-cpp"
    assert spec.mcp_ollama_read_timeout_seconds == 1800
    assert spec.ollama_options == {"temperature": 0}


def test_build_run_specs_auto_names_same_model_different_options(tmp_path: Path) -> None:
    models_file = tmp_path / "models.yaml"
    models_file.write_text(
        """
defaults:
  concurrency: 1
models:
  - model: ollama/gemma4:26b
    options:
      num_ctx: 32768
  - model: ollama/gemma4:26b
    options:
      num_ctx: 65536
"""
    )
    specs = _build_run_specs(models=(), models_file=str(models_file))
    assert len(specs) == 2
    assert specs[0].run_name != specs[1].run_name
    assert "options.num_ctx=32768" in specs[0].run_name
    assert "options.num_ctx=65536" in specs[1].run_name


def test_build_run_specs_rejects_duplicate_run_names(tmp_path: Path) -> None:
    models_file = tmp_path / "models.yaml"
    models_file.write_text(
        """
defaults:
  concurrency: 1
models:
  - name: gemma
    model: ollama/gemma4:26b
    options:
      num_ctx: 32768
  - name: gemma
    model: ollama/gemma4:26b
    options:
      num_ctx: 65536
"""
    )
    try:
        _build_run_specs(models=(), models_file=str(models_file))
    except Exception as exc:
        assert "Duplicate model run identity" in str(exc)
    else:
        raise AssertionError("Expected duplicate run names to fail")


def test_build_run_specs_supports_default_and_per_model_run_counts(tmp_path: Path) -> None:
    models_file = tmp_path / "models.yaml"
    models_file.write_text(
        """
defaults:
  runs_per_model: 3
models:
  - name: default-sample
    model: mock/perfect
  - name: larger-sample
    model: mock/perfect
    runs_per_model: 5
"""
    )

    specs = _build_run_specs(models=(), models_file=str(models_file))

    assert [spec.runs_per_model for spec in specs] == [3, 5]
    assert "runs_per_model" not in specs[1].config_identity


def test_build_run_specs_rejects_non_positive_run_count(tmp_path: Path) -> None:
    models_file = tmp_path / "models.yaml"
    models_file.write_text(
        """
defaults:
  runs_per_model: 0
models:
  - model: mock/perfect
"""
    )

    try:
        _build_run_specs(models=(), models_file=str(models_file))
    except Exception as exc:
        assert "runs_per_model must be at least 1" in str(exc)
    else:
        raise AssertionError("Expected non-positive runs_per_model to fail")


def test_cli_baseline_runs_each_model_repetition_to_an_isolated_csv(
    tmp_path: Path, monkeypatch
) -> None:
    manifest = _manifest(tmp_path)
    models_file = tmp_path / "models.yaml"
    models_file.write_text(
        """
defaults:
  runs_per_model: 3
models:
  - name: repeated-model
    model: mock/perfect
"""
    )
    calls: list[dict] = []

    async def fake_run_eval_cli_bare(**kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setattr("ori.eval.runner.run_eval_cli_bare", fake_run_eval_cli_bare)

    result = CliRunner().invoke(
        main,
        [
            "baseline",
            "-m",
            str(manifest),
            "--models-file",
            str(models_file),
            "-o",
            str(tmp_path / "out"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert len(calls) == 3
    assert [call["run_config"]["run_index"] for call in calls] == [1, 2, 3]
    assert {call["run_config"]["runs_per_model"] for call in calls} == {3}
    assert [call["output_path"].name for call in calls] == [
        "run-001.csv",
        "run-002.csv",
        "run-003.csv",
    ]
    assert len({call["output_path"] for call in calls}) == 3


def test_cli_baseline_mcp_runs_each_model_repetition_to_an_isolated_csv(
    tmp_path: Path, monkeypatch
) -> None:
    manifest = _manifest(tmp_path)
    mcp_dir = tmp_path / "bloodhound-mcp"
    mcp_dir.mkdir()
    models_file = tmp_path / "models.yaml"
    models_file.write_text(
        """
defaults:
  runs_per_model: 2
models:
  - name: repeated-mcp-model
    model: mock/mcp_perfect
"""
    )
    calls: list[dict] = []

    async def fake_run_eval_mcp_cli_bare(**kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setattr("ori.eval.runner.run_eval_mcp_cli_bare", fake_run_eval_mcp_cli_bare)

    result = CliRunner().invoke(
        main,
        [
            "baseline-mcp",
            "-m",
            str(manifest),
            "--models-file",
            str(models_file),
            "-o",
            str(tmp_path / "out"),
            "--mcp-dir",
            str(mcp_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    assert len(calls) == 2
    assert [call["run_config"]["run_index"] for call in calls] == [1, 2]
    assert [call["output_path"].name for call in calls] == ["run-001.csv", "run-002.csv"]


def test_cli_baseline_separates_same_model_different_contexts(tmp_path: Path, monkeypatch) -> None:

    manifest = _manifest(tmp_path)
    models_file = tmp_path / "models.yaml"
    models_file.write_text(
        """
defaults:
  concurrency: 1
models:
  - name: gemma4-26b-32k
    model: ollama/gemma4:26b
    options:
      num_ctx: 32768
  - name: gemma4-26b-64k
    model: ollama/gemma4:26b
    options:
      num_ctx: 65536
"""
    )
    calls: list[dict] = []

    async def fake_run_eval_cli_bare(**kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setattr("ori.eval.runner.run_eval_cli_bare", fake_run_eval_cli_bare)
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "baseline",
            "-m",
            str(manifest),
            "--models-file",
            str(models_file),
            "-o",
            str(tmp_path / "out"),
        ],
    )
    assert result.exit_code == 0
    assert len(calls) == 2
    assert calls[0]["run_name"] == "gemma4-26b-32k"
    assert calls[0]["model"] == "ollama/gemma4:26b"
    assert calls[0]["ollama_options"] == {"num_ctx": 32768}
    assert calls[1]["run_name"] == "gemma4-26b-64k"
    assert calls[1]["model"] == "ollama/gemma4:26b"
    assert calls[1]["ollama_options"] == {"num_ctx": 65536}
    assert calls[0]["output_path"] != calls[1]["output_path"]


def test_cli_run_config_baseline_mcp_uses_profile_settings(tmp_path: Path, monkeypatch) -> None:
    manifest = _manifest(tmp_path)
    mcp_dir = tmp_path / "bloodhound-mcp"
    mcp_dir.mkdir()
    config = tmp_path / "run.yaml"
    config.write_text(
        f"""
version: 1
defaults:
  bhce_url: http://bh.local
  concurrency: 2
  model_base_url: http://127.0.0.1:11434/v1
  max_model_reruns_on_infra: 3
  mcp:
    mcp_dir: {mcp_dir}
    max_steps: 20
    tool_loop: native-openai-compatible
    openai_compat_telemetry_adapter: llama-cpp
    ollama_read_timeout_seconds: 1200
profiles:
  phase3b:
    kind: baseline-mcp
    manifest: {manifest}
    output_dir: out
    models:
      - name: gemma
        model: ollama/gemma4:e4b
      - name: qwen
        model: ollama/qwen3:14b
        concurrency: 1
        max_steps: 24
        model_base_url: http://custom.local/v1
        mcp_tool_loop: inspect
        openai_compat_telemetry_adapter: vllm
        mcp_ollama_read_timeout_seconds: 1500
"""
    )
    calls: list[dict] = []

    async def fake_run_eval_mcp_cli_bare(**kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setattr("ori.eval.runner.run_eval_mcp_cli_bare", fake_run_eval_mcp_cli_bare)
    runner = CliRunner()
    result = runner.invoke(main, ["run", "--config", str(config), "--profile", "phase3b"])
    assert result.exit_code == 0
    assert len(calls) == 2
    assert calls[0]["concurrency"] == 2
    assert calls[0]["max_steps"] == 20
    assert calls[0]["mcp_tool_loop"] == "native-openai-compatible"
    assert calls[0]["openai_compat_telemetry_adapter"] == "llama-cpp"
    assert calls[0]["mcp_ollama_read_timeout_seconds"] == 1200.0
    assert calls[0]["max_model_reruns_on_infra"] == 3
    assert calls[0]["bhce_url"] == "http://bh.local"
    assert calls[0]["model_base_url"] == "http://127.0.0.1:11434/v1"
    assert calls[1]["concurrency"] == 1
    assert calls[1]["max_steps"] == 24
    assert calls[1]["mcp_tool_loop"] == "inspect"
    assert calls[1]["openai_compat_telemetry_adapter"] == "vllm"
    assert calls[1]["mcp_ollama_read_timeout_seconds"] == 1500
    assert calls[1]["model_base_url"] == "http://custom.local/v1"


def test_cli_run_config_preflight_threads_health_settings(tmp_path: Path, monkeypatch) -> None:
    manifest = _manifest(tmp_path)
    config = tmp_path / "run.yaml"
    config.write_text(
        f"""
version: 1
profiles:
  prep:
    kind: preflight
    manifest: {manifest}
    output_dir: out
    health:
      timeout_seconds: 12
      poll_interval: 0.5
"""
    )
    captured: dict = {}

    async def fake_preflight(**kwargs):
        captured.update(kwargs)
        return PreflightResult(
            health=BHHealthResult(ok=True, detail="ok", query="q", classification="ok"),
            ingest=__import__("ori.eval.ops", fromlist=["VerifyIngestResult"]).VerifyIngestResult(
                [], []
            ),
            smoke=__import__("ori.eval.ops", fromlist=["SmokeEvalResult"]).SmokeEvalResult([], {}),
        )

    monkeypatch.setattr("ori.eval.ops.run_preflight", fake_preflight)
    runner = CliRunner()
    result = runner.invoke(main, ["run", "--config", str(config), "--profile", "prep"])
    assert result.exit_code == 0
    assert captured["timeout_seconds"] == 12.0
    assert captured["poll_interval"] == 0.5


def test_cli_run_config_run_all_profiles_runs_enabled_in_order(tmp_path: Path, monkeypatch) -> None:
    manifest = _manifest(tmp_path)
    config = tmp_path / "run.yaml"
    config.write_text(
        f"""
version: 1
profiles:
  prep:
    kind: preflight
    manifest: {manifest}
    output_dir: out-prep
  skipped:
    kind: preflight
    enabled: false
    manifest: {manifest}
    output_dir: out-skipped
  smoke:
    kind: smoke-eval
    manifest: {manifest}
    output_dir: out-smoke
"""
    )
    calls: list[tuple[str, str]] = []

    async def fake_preflight(**kwargs):
        calls.append(("preflight", str(kwargs["output_dir"])))
        return PreflightResult(
            health=BHHealthResult(ok=True, detail="ok", query="q", classification="ok"),
            ingest=__import__("ori.eval.ops", fromlist=["VerifyIngestResult"]).VerifyIngestResult(
                [], []
            ),
            smoke=__import__("ori.eval.ops", fromlist=["SmokeEvalResult"]).SmokeEvalResult([], {}),
        )

    async def fake_smoke_eval(**kwargs):
        calls.append(("smoke_eval", str(kwargs["output_dir"])))
        return __import__("ori.eval.ops", fromlist=["SmokeEvalResult"]).SmokeEvalResult([], {})

    monkeypatch.setattr("ori.eval.ops.run_preflight", fake_preflight)
    monkeypatch.setattr("ori.eval.ops.run_smoke_eval", fake_smoke_eval)
    runner = CliRunner()
    result = runner.invoke(main, ["run", "--config", str(config), "--run-all-profiles"])
    assert result.exit_code == 0
    assert calls == [
        ("preflight", str((tmp_path / "out-prep").resolve())),
        ("smoke_eval", str((tmp_path / "out-smoke").resolve())),
    ]
    assert "[1/2] prep (preflight)" in result.output
    assert "[2/2] smoke (smoke_eval)" in result.output


def test_cli_run_config_run_all_profiles_rejects_profile_argument(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    config = tmp_path / "run.yaml"
    config.write_text(
        f"""
version: 1
profiles:
  prep:
    kind: preflight
    manifest: {manifest}
    output_dir: out-prep
"""
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["run", "--config", str(config), "--profile", "prep", "--run-all-profiles"],
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_cli_run_config_keep_going_requires_run_all_profiles(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    config = tmp_path / "run.yaml"
    config.write_text(
        f"""
version: 1
profiles:
  prep:
    kind: preflight
    manifest: {manifest}
    output_dir: out-prep
"""
    )
    runner = CliRunner()
    result = runner.invoke(main, ["run", "--config", str(config), "--keep-going"])
    assert result.exit_code != 0
    assert "--keep-going requires --run-all-profiles" in result.output


def test_cli_run_config_run_all_profiles_keep_going_continues_after_failure(
    tmp_path: Path, monkeypatch
) -> None:
    manifest = _manifest(tmp_path)
    config = tmp_path / "run.yaml"
    config.write_text(
        f"""
version: 1
profiles:
  prep:
    kind: preflight
    manifest: {manifest}
    output_dir: out-prep
  smoke:
    kind: smoke-eval
    manifest: {manifest}
    output_dir: out-smoke
"""
    )
    calls: list[str] = []

    async def fake_preflight(**kwargs):
        calls.append("preflight")
        return PreflightResult(
            health=BHHealthResult(ok=True, detail="ok", query="q", classification="ok"),
            ingest=__import__("ori.eval.ops", fromlist=["VerifyIngestResult"]).VerifyIngestResult(
                [], []
            ),
            smoke=__import__("ori.eval.ops", fromlist=["SmokeEvalResult"]).SmokeEvalResult([], {}),
        )

    async def fake_smoke_eval(**kwargs):
        calls.append("smoke_eval")
        raise RuntimeError("boom")

    monkeypatch.setattr("ori.eval.ops.run_preflight", fake_preflight)
    monkeypatch.setattr("ori.eval.ops.run_smoke_eval", fake_smoke_eval)
    runner = CliRunner()
    result = runner.invoke(
        main, ["run", "--config", str(config), "--run-all-profiles", "--keep-going"]
    )
    assert calls == ["preflight", "smoke_eval"]
    assert result.exit_code != 0
    assert "Profile failed: smoke (boom)" in result.output
    assert "Run-all profile failures:" in result.output
    assert "- smoke: boom" in result.output


def test_cli_run_config_run_all_profiles_without_keep_going_stops_on_failure(
    tmp_path: Path, monkeypatch
) -> None:
    manifest = _manifest(tmp_path)
    config = tmp_path / "run.yaml"
    config.write_text(
        f"""
version: 1
profiles:
  smoke:
    kind: smoke-eval
    manifest: {manifest}
    output_dir: out-smoke
  prep:
    kind: preflight
    manifest: {manifest}
    output_dir: out-prep
"""
    )
    calls: list[str] = []

    async def fake_smoke_eval(**kwargs):
        calls.append("smoke_eval")
        raise RuntimeError("boom")

    async def fake_preflight(**kwargs):
        calls.append("preflight")
        return PreflightResult(
            health=BHHealthResult(ok=True, detail="ok", query="q", classification="ok"),
            ingest=__import__("ori.eval.ops", fromlist=["VerifyIngestResult"]).VerifyIngestResult(
                [], []
            ),
            smoke=__import__("ori.eval.ops", fromlist=["SmokeEvalResult"]).SmokeEvalResult([], {}),
        )

    monkeypatch.setattr("ori.eval.ops.run_smoke_eval", fake_smoke_eval)
    monkeypatch.setattr("ori.eval.ops.run_preflight", fake_preflight)
    runner = CliRunner()
    result = runner.invoke(main, ["run", "--config", str(config), "--run-all-profiles"])
    assert result.exit_code != 0
    assert calls == ["smoke_eval"]


def test_cli_baseline_mcp_parses_resource_mode(tmp_path: Path, monkeypatch) -> None:
    manifest = _manifest(tmp_path)
    mcp_dir = tmp_path / "bloodhound-mcp"
    mcp_dir.mkdir()
    captured: list[dict] = []

    async def fake_run_eval_mcp_cli_bare(**kwargs):
        captured.append(kwargs)
        return []

    monkeypatch.setattr("ori.eval.runner.run_eval_mcp_cli_bare", fake_run_eval_mcp_cli_bare)
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "baseline-mcp",
            "-m",
            str(manifest),
            "--model",
            "mock/mcp_perfect",
            "-o",
            str(tmp_path / "out"),
            "--mcp-dir",
            str(mcp_dir),
            "--resource-mode",
            "on-demand",
            "--mcp-tool-loop",
            "native-openai-compatible",
            "--openai-compat-telemetry-adapter",
            "llama-cpp",
        ],
    )
    assert result.exit_code == 0
    assert captured[0]["resource_mode"] == "on-demand"
    assert captured[0]["mcp_tool_loop"] == "native-openai-compatible"
    assert captured[0]["openai_compat_telemetry_adapter"] == "llama-cpp"


def test_cli_baseline_mcp_resources_uses_on_demand_mode(tmp_path: Path, monkeypatch) -> None:
    manifest = _manifest(tmp_path)
    mcp_dir = tmp_path / "bloodhound-mcp"
    mcp_dir.mkdir()
    captured: list[dict] = []

    async def fake_run_eval_mcp_cli_bare(**kwargs):
        captured.append(kwargs)
        return []

    monkeypatch.setattr("ori.eval.runner.run_eval_mcp_cli_bare", fake_run_eval_mcp_cli_bare)
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "baseline-mcp-resources",
            "-m",
            str(manifest),
            "--model",
            "mock/mcp_perfect",
            "-o",
            str(tmp_path / "out"),
            "--mcp-dir",
            str(mcp_dir),
        ],
    )
    assert result.exit_code == 0
    assert captured[0]["resource_mode"] == "on-demand"
    assert captured[0]["mcp_tool_loop"] == "auto"
    assert captured[0]["openai_compat_telemetry_adapter"] == "auto"


def test_print_helpers(capsys) -> None:
    from ori.eval.ops import (
        CountCheck,
        PathCheck,
        SmokeEvalResult,
        VerifyBHHealthResult,
        VerifyIngestResult,
    )

    verify = VerifyIngestResult(
        count_checks=[CountCheck("users", 2, 2)],
        path_checks=[PathCheck("t1_admin_to", True, "J", "D", [])],
    )
    print_verify_ingest(verify)
    out = capsys.readouterr().out
    assert "INGEST CHECK: PASS" in out

    health = VerifyBHHealthResult(
        ok=True,
        detail="query succeeded",
        classification="ok",
        query="MATCH (n:Domain) RETURN n LIMIT 1",
    )
    print_verify_bh_health(health)
    out = capsys.readouterr().out
    assert "BH HEALTH: PASS" in out

    smoke = SmokeEvalResult(
        checks=[SmokeCheck("mock/perfect", "CORRECT", {"CORRECT": 1})],
        results_by_model={"mock/perfect": []},
    )
    print_smoke_eval(smoke)
    out = capsys.readouterr().out
    assert "SMOKE TEST: PASS" in out

    preflight = PreflightResult(
        health=VerifyBHHealthResult(
            ok=True,
            detail="query succeeded",
            classification="ok",
            query="MATCH (n:Domain) RETURN n LIMIT 1",
        ),
        ingest=verify,
        smoke=smoke,
    )
    print_preflight(preflight)
    out = capsys.readouterr().out
    assert "PREFLIGHT: PASS" in out
