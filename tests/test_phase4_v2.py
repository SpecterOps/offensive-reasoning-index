from __future__ import annotations

import json
import zipfile
from copy import deepcopy
from pathlib import Path

from click.testing import CliRunner

from ori.cli import main
from ori.eval.answer_scoring import score_official_answers_projection
from ori.eval.phase4_v2 import (
    generate_phase4_v2_official_tasks,
    validate_phase4_v2_matrix,
)
from ori.eval.tasks import generate_tasks
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
    assert validation["matrix_balance"]["by_family"] == {
        "adcs_delegation_composite": 32,
        "adcs_esc1": 32,
        "delegation_rbcd": 32,
    }
    assert validation["matrix_balance"]["by_track"] == {"cypher": 48, "mcp": 48}
    assert validation["matrix_balance"]["by_task_type"] == {
        "cypher_generation": 24,
        "enumeration": 24,
        "mcp_analysis": 24,
        "path_finding": 24,
    }


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


def test_phase4_v2_generated_zip_and_manifest_cover_parent_child_forest(tmp_path: Path) -> None:
    config = tmp_path / "run.yaml"
    config.write_text(
        """
version: 1
profiles:
  phase4_v2_small:
    kind: generate
    domain: forest.example
    seed: 4402
    generator:
      profile: phase4_v2_small
    output_manifest: out/manifest.json
    output_zip: out/forest.zip
"""
    )

    result = CliRunner().invoke(
        main,
        ["run", "--config", str(config), "--profile", "phase4_v2_small"],
    )

    assert result.exit_code == 0, result.output
    out = tmp_path / "out"
    manifest = json.loads((out / "manifest.json").read_text())
    with zipfile.ZipFile(out / "forest.zip") as zf:
        domains = json.loads(zf.read("domains.json"))["data"]
    domain_names = {item["Properties"]["domain"] for item in domains}
    assert domain_names == {"FOREST.EXAMPLE", "CHILD.FOREST.EXAMPLE"}
    assert all(item["Trusts"] for item in domains)
    assert manifest["generation_summary"]["forest"]["type"] == "parent_child"
    assert manifest["generation_summary"]["stats"]["domains"] == 2
    assert manifest["stats"]["domains"] == 2
    assert manifest["planted_paths"]
    assert {path["domain_role"] for path in manifest["planted_paths"]} == {"parent", "child"}
    adcs_paths = [
        path
        for path in manifest["planted_paths"]
        if path["template_id"] in {"t4_adcs_esc1", "t5_adcs_to_delegation_composite"}
    ]
    assert adcs_paths
    for path in adcs_paths:
        edges = [edge["edge"] for edge in path["path_edges"]]
        assert "PublishedTo" in edges
        assert "IssuedSignedBy" not in edges
        assert "TrustedForNTAuth" not in edges
    composite_edges = [
        edge["edge"]
        for path in adcs_paths
        if path["template_id"] == "t5_adcs_to_delegation_composite"
        for edge in path["path_edges"]
    ]
    assert "GenericWrite" in composite_edges

    with zipfile.ZipFile(out / "forest.zip") as zf:
        bridge_users = [
            user
            for user in json.loads(zf.read("users.json"))["data"]
            if user["Properties"]["samaccountname"] == "svc_phase4_bridge"
        ]
    assert bridge_users
    assert all(
        any(ace["RightName"] == "GenericWrite" for ace in bridge["Aces"])
        for bridge in bridge_users
    )


def test_phase4_v2_manifest_preflight_fields_cannot_be_empty_zero_of_zero() -> None:
    forest = build_phase4_v2_forest(profile="small", seed=4402)
    corpus = generate_phase4_v2_official_tasks(forest)

    assert corpus["stats"]["users"] > 0
    assert corpus["stats"]["computers"] > 0
    assert corpus["stats"]["groups"] > 0
    assert corpus["stats"]["ous"] > 0
    assert corpus["stats"]["domains"] == 2
    assert len(corpus["planted_paths"]) == len(forest.template_instances)
    assert all(path["path_edges"] for path in corpus["planted_paths"])


def test_phase4_v2_generated_smoke_tasks_have_unique_parent_child_ids_and_domains() -> None:
    forest = build_phase4_v2_forest(profile="medium", seed=4402)
    corpus = generate_phase4_v2_official_tasks(forest)

    tasks = generate_tasks(corpus)
    task_ids = [task.id for task in tasks]

    assert len(task_ids) == len(set(task_ids))
    planted_tasks = [task for task in tasks if task.template_id != "global"]
    assert {task.metadata["domain_role"] for task in planted_tasks} == {"parent", "child"}
    assert any(task.id.startswith("parent-t1_admin_to") for task in tasks)
    assert any(task.id.startswith("child-t1_admin_to") for task in tasks)
    assert any("@CHILD.FOREST.EXAMPLE" in task.question for task in tasks)
    assert all("CORP.LOCAL" not in task.question for task in tasks)


def test_phase4_v2_score_answers_cli_uses_official_projection(tmp_path: Path) -> None:
    forest = build_phase4_v2_forest(profile="medium", seed=4402)
    corpus = generate_phase4_v2_official_tasks(forest)
    manifest_path = tmp_path / "manifest.json"
    answers_path = tmp_path / "answers.json"
    output_path = tmp_path / "projection.json"
    manifest_path.write_text(json.dumps(corpus))
    answers_path.write_text(
        json.dumps(
            {
                "answers": [
                    {"task_id": task["id"], "correct": index < 7}
                    for index, task in enumerate(corpus["tasks_official"])
                ]
            }
        )
    )

    result = CliRunner().invoke(
        main,
        [
            "score-answers",
            "--manifest",
            str(manifest_path),
            "--answers",
            str(answers_path),
            "--output",
            str(output_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Scored 7/100 official answers" in result.output
    projection = json.loads(output_path.read_text())
    assert projection["summary"]["raw_score"] == 7
    assert projection["summary"]["official_count"] == 100
    assert projection["summary"]["startup_smoke"] == {"count": 4, "correct": 4}
    assert projection["summary"]["benchmark_matrix"] == {"count": 96, "correct": 3}


def test_phase4_v2_matrix_validation_rejects_invalid_reported_balances() -> None:
    forest = build_phase4_v2_forest(profile="medium", seed=4402)
    corpus = generate_phase4_v2_official_tasks(forest)
    bad_track = deepcopy(corpus)
    for task in bad_track["tasks_official"]:
        if task["phase"] == "benchmark_matrix" and task["track"] == "mcp":
            task["track"] = "cypher"
            break
    bad_type = deepcopy(corpus)
    for task in bad_type["tasks_official"]:
        if task["phase"] == "benchmark_matrix" and task["category"] == "enumeration":
            task["category"] = "path_finding"
            break
    bad_family = deepcopy(corpus)
    for task in bad_family["tasks_official"]:
        if task["phase"] == "benchmark_matrix":
            task["scenario_family"] = "unexpected_family"
            break

    assert validate_phase4_v2_matrix(bad_track)["ok"] is False
    assert validate_phase4_v2_matrix(bad_type)["ok"] is False
    validation = validate_phase4_v2_matrix(bad_family)
    assert validation["ok"] is False
    assert any("scenario family set" in error for error in validation["errors"])
