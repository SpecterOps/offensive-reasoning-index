from __future__ import annotations

import json
from types import SimpleNamespace

from click.testing import CliRunner

from ori.cli import main
from ori.eval.v2 import campaign_config, campaign_runner
from ori.eval.v2.schema import Track
from ori.eval.v2.verbose import (
    MAX_ANSWER_SHAPE_CHARS,
    MAX_QUESTION_CHARS,
    answer_shape,
    answer_shape_line,
    task_question_lines,
    tool_activity_lines,
)


def _task(kind: str = "count", question: str = "What is the answer?") -> SimpleNamespace:
    schemas = {
        "set": {
            "type": "object",
            "properties": {
                "entities": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["entities"],
        },
        "count": {
            "type": "object",
            "properties": {"count": {"type": "integer", "minimum": 0}},
            "required": ["count"],
        },
        "decision": {
            "type": "object",
            "properties": {"decision": {"type": "boolean"}},
            "required": ["decision"],
        },
        "route": {
            "type": "object",
            "properties": {
                "edges": {"type": "array", "items": {"type": "object"}},
                "path_status": {"const": "found"},
            },
            "required": ["edges", "path_status"],
        },
        "absence": {
            "type": "object",
            "properties": {
                "path_status": {"const": "no_path"},
                "negative_reason_codes": {"type": "array", "items": {"enum": ["reason"]}},
            },
            "required": ["path_status", "negative_reason_codes"],
        },
    }
    return SimpleNamespace(
        claim_kind=kind,
        question=question,
        answer_schema=schemas[kind],
    )


def test_answer_shape_uses_only_public_schema_and_non_answer_placeholders() -> None:
    count_line = answer_shape_line(_task())
    route_line = answer_shape_line(_task(kind="route"))
    absence_line = answer_shape_line(_task(kind="absence"))

    assert '"count":"<integer>"' in count_line
    assert '"path_status":"<fixed value>"' in route_line
    assert '"path_status":"<fixed value>"' in absence_line
    assert '"negative_reason_codes":["<fixed value>"]' in absence_line
    assert "expected answer" in count_line
    assert "oracle" not in count_line.casefold()


def test_question_line_is_bounded_and_tool_activity_is_mechanical_only() -> None:
    question_lines = task_question_lines(
        _task(question="Question oracle expected_count \x1b[2J" + ("x" * 10_000)),
        position=1,
        total=2,
    )
    assert len(question_lines[0]) <= MAX_QUESTION_CHARS + 20
    assert question_lines[0].endswith("…")
    assert "\x1b" not in question_lines[0]
    assert "Model activity: task attempt started" in question_lines[1]
    assert "provider request started" not in question_lines[1]

    observation = SimpleNamespace(
        succeeded=True,
        policy_rejected=False,
        query_timeout=False,
        query_error=False,
        infrastructure_failure=False,
        result_count=42,
        total_count=99,
        complete=True,
        truncated=False,
    )
    receipt = SimpleNamespace(
        sequence=1,
        tool_name="cypher_query\x1b[2J",
        operation="run\x1b[2J",
        arguments={"query": "SECRET_QUERY"},
        result_text="SECRET_TOOL_RESULT",
        observation=observation,
    )
    lines = tool_activity_lines(
        SimpleNamespace(surface="mcp", mcp_tool_receipts=(receipt,))
    )
    assert "cypher_query" in lines[0]
    assert "\x1b" not in lines[0]
    assert all(character.isprintable() for character in lines[0])
    assert "42" not in lines[0]
    assert "99" not in lines[0]
    assert "results=" not in lines[0]
    assert "total=" not in lines[0]
    assert "SECRET_QUERY" not in lines[0]
    assert "SECRET_TOOL_RESULT" not in lines[0]


def test_tool_activity_hides_answer_bearing_scalars_for_all_claim_shapes() -> None:
    for kind, result_count in (
        ("count", 4),
        ("absence", 0),
        ("set", 23),
    ):
        observation = SimpleNamespace(
            succeeded=True,
            policy_rejected=False,
            query_timeout=False,
            query_error=False,
            infrastructure_failure=False,
            result_count=result_count,
            total_count=result_count,
            complete=True,
            truncated=False,
        )
        receipt = SimpleNamespace(
            sequence=1,
            tool_name="cypher_query",
            operation="run",
            observation=observation,
        )

        rendered = "\n".join(
            tool_activity_lines(
                SimpleNamespace(surface="mcp", mcp_tool_receipts=(receipt,))
            )
        )
        assert str(result_count) not in rendered, kind
        assert "results=" not in rendered
        assert "total=" not in rendered


def test_large_answer_shape_stays_valid_json_and_bounded() -> None:
    task = _task(kind="set")
    task.answer_schema = {
        "type": "object",
        "properties": {
            f"field-{index}-{'x' * 100}": {"type": "string"}
            for index in range(80)
        },
        "required": [],
    }

    line = answer_shape_line(task)
    payload = line.rsplit(": ", 1)[1]
    parsed = json.loads(payload)

    assert len(line) <= MAX_ANSWER_SHAPE_CHARS + 120
    assert parsed == "<bounded answer shape omitted>"
    assert isinstance(answer_shape(task), dict)


def test_direct_activity_does_not_claim_execution_after_rejection() -> None:
    lines = tool_activity_lines(
        SimpleNamespace(
            surface="direct",
            provider_error=None,
            direct_receipt=SimpleNamespace(
                query_executed=False,
                failure_type="policy_rejected",
            ),
        )
    )

    assert "rejected before execution" in lines[0]
    assert "completed" not in lines[0]


def test_run_v2_help_advertises_opt_in_verbose_view() -> None:
    result = CliRunner().invoke(main, ["run-v2", "--help"])

    assert result.exit_code == 0
    assert "--verbose" in result.output
    assert "answer-shape placeholder" in result.output


def test_run_v2_verbose_flag_is_forwarded_without_changing_default_mode(
    tmp_path, monkeypatch
) -> None:
    config = tmp_path / "models.yaml"
    config.write_text("placeholder: true\n")
    captured: dict[str, object] = {}

    async def fake_run(path, *, preflight_only, progress, verbose):
        captured.update(
            {
                "path": path,
                "preflight_only": preflight_only,
                "progress": progress,
                "verbose": verbose,
            }
        )
        return SimpleNamespace(
            graph_fingerprint="g" * 64,
            model_count=1,
            models=(
                SimpleNamespace(
                    name="test",
                    provider="mock",
                    model="test-model",
                    credential_check="not-required",
                    capability_check="pass",
                ),
            ),
            tracks=(
                SimpleNamespace(
                    track=Track.DIRECT,
                    task_count=1,
                    candidate_release_fingerprint="f" * 64,
                ),
            ),
        )

    monkeypatch.setattr(campaign_runner, "run_v2_campaign", fake_run)
    monkeypatch.setattr(
        campaign_config,
        "load_v2_campaign_config",
        lambda _path: SimpleNamespace(output_dir=tmp_path / "campaign"),
    )

    result = CliRunner().invoke(
        main,
        ["run-v2", "--config", str(config), "--execute", "--verbose"],
    )

    assert result.exit_code == 0, result.output
    assert captured["path"] == config
    assert captured["preflight_only"] is False
    assert captured["verbose"] is True
