from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from ori.cli import main
from ori.eval.answer_scoring import score_official_answers_projection
from ori.eval.phase4_v2 import (
    generate_phase4_v2_official_tasks,
    validate_phase4_v2_matrix,
)
from ori.generator.phase4_v2 import build_phase4_v2_forest


def test_phase4_v2_medium_official_has_four_smoke_and_ninety_six_matrix_tasks() -> None:
    forest = build_phase4_v2_forest(profile="medium", seed=4402)
    corpus = generate_phase4_v2_official_tasks(forest)

    assert corpus["profile"] == "medium"
    assert corpus["official_count"] == 100
    assert len(corpus["tasks_official"]) == 100
    smoke = [task for task in corpus["tasks_official"] if task["phase"] == "startup_smoke"]
    matrix = [task for task in corpus["tasks_official"] if task["phase"] == "benchmark_matrix"]
    assert len(smoke) == 4
    assert len(matrix) == 96
    assert all(task["smoke_task"] is True for task in smoke)
    assert all(task["benchmark_weight"] == "official_score" for task in smoke)
    assert all(task["technical_difficulty"] in range(1, 6) for task in corpus["tasks_official"])
    assert all(task["reasoning_difficulty"] in range(1, 6) for task in corpus["tasks_official"])


def test_phase4_v2_matrix_validation_scopes_balance_to_benchmark_tasks() -> None:
    forest = build_phase4_v2_forest(profile="medium", seed=4402)
    corpus = generate_phase4_v2_official_tasks(forest)

    validation = validate_phase4_v2_matrix(corpus)

    assert validation["ok"] is True
    assert validation["official_count"] == 100
    assert validation["startup_smoke_count"] == 4
    assert validation["benchmark_matrix_count"] == 96
    assert sum(validation["matrix_balance"]["by_family"].values()) == 96
    assert sum(validation["matrix_balance"]["by_track"].values()) == 96


def test_phase4_v2_scoring_is_mechanical_and_includes_smoke_in_raw_score(tmp_path: Path) -> None:
    forest = build_phase4_v2_forest(profile="medium", seed=4402)
    corpus = generate_phase4_v2_official_tasks(forest)
    manifest_path = tmp_path / "manifest.json"
    answers_path = tmp_path / "answers.json"
    manifest_path.write_text(json.dumps(corpus))
    answers = [
        {"task_id": task["id"], "correct": index < 5}
        for index, task in enumerate(corpus["tasks_official"])
    ]
    answers_path.write_text(json.dumps({"answers": answers}))

    projection = score_official_answers_projection(
        manifest_path=manifest_path,
        answers_path=answers_path,
    )

    assert projection["summary"]["raw_score"] == 5
    assert projection["summary"]["official_count"] == 100
    assert projection["summary"]["official_accuracy"] == 0.05
    assert projection["summary"]["startup_smoke"]["correct"] == 4
    assert projection["summary"]["benchmark_matrix"]["correct"] == 1
    assert all(task["score"] in {0, 1} for task in projection["tasks"])


def test_phase4_v2_generate_profile_writes_expected_schema_artifacts(tmp_path: Path) -> None:
    config = tmp_path / "run.yaml"
    config.write_text(
        """
version: 1
profiles:
  phase4_v2_medium:
    kind: generate
    domain: forest.example
    seed: 4402
    generator:
      profile: phase4_v2_medium
    output_manifest: out/manifest.json
    output_zip: out/forest.zip
"""
    )

    result = CliRunner().invoke(
        main,
        ["run", "--config", str(config), "--profile", "phase4_v2_medium"],
    )

    assert result.exit_code == 0, result.output
    out = tmp_path / "out"
    assert (out / "generation_summary.json").exists()
    assert (out / "template_instances.json").exists()
    assert (out / "tasks_official.json").exists()
    assert (out / "tasks_candidates.json").exists()
    assert (out / "answers.json").exists()
    assert (out / "matrix_validation.json").exists()
    assert (out / "run_summary.json").exists()
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["profile"] == "medium"
    assert manifest["official_count"] == 100
    assert manifest["matrix_validation"]["ok"] is True
