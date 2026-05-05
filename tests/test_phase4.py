from __future__ import annotations

import json
import zipfile
from pathlib import Path

from click.testing import CliRunner

from ori.cli import _build_manifest, main
from ori.eval.tasks import generate_tasks
from ori.generator.phase4 import build_phase4_v1_graph
from ori.generator.serializer import serialize_to_zip


def _manifest_for_seed(seed: int) -> dict:
    graph = build_phase4_v1_graph(
        domain="PHASE4.TEST",
        seed=seed,
        users=18,
        workstations=7,
        servers=4,
    )
    return _build_manifest(graph, seed)


def test_phase4_same_seed_manifest_is_identical() -> None:
    assert _manifest_for_seed(4401) == _manifest_for_seed(4401)


def test_phase4_same_seed_zip_is_identical(tmp_path: Path) -> None:
    first = build_phase4_v1_graph(
        domain="PHASE4.TEST",
        seed=4401,
        users=18,
        workstations=7,
        servers=4,
    )
    second = build_phase4_v1_graph(
        domain="PHASE4.TEST",
        seed=4401,
        users=18,
        workstations=7,
        servers=4,
    )
    first_zip = tmp_path / "first.zip"
    second_zip = tmp_path / "second.zip"
    serialize_to_zip(first, first_zip)
    serialize_to_zip(second, second_zip)
    assert first_zip.read_bytes() == second_zip.read_bytes()


def test_phase4_different_seed_keeps_templates_but_varies_names() -> None:
    a = _manifest_for_seed(4401)
    b = _manifest_for_seed(4402)
    assert [p["template_id"] for p in a["planted_paths"]] == [
        p["template_id"] for p in b["planted_paths"]
    ]
    assert [p["source_name"] for p in a["planted_paths"]] != [
        p["source_name"] for p in b["planted_paths"]
    ]


def test_phase4_adcs_objects_serialize_to_sharphound_files(tmp_path: Path) -> None:
    graph = build_phase4_v1_graph(
        domain="PHASE4.TEST",
        seed=4401,
        users=18,
        workstations=7,
        servers=4,
    )
    zip_path = tmp_path / "phase4.zip"
    serialize_to_zip(graph, zip_path)
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        template_data = json.loads(zf.read("certtemplates.json"))

    assert {
        "rootcas.json",
        "enterprisecas.json",
        "ntauthstores.json",
        "certtemplates.json",
    } <= names
    assert template_data["meta"]["type"] == "certtemplates"
    assert any(
        item["Properties"]["enrolleesuppliessubject"] is True
        for item in template_data["data"]
    )


def test_phase4_tasks_include_tier4_and_tier5_metadata() -> None:
    manifest = _manifest_for_seed(4401)
    tasks = generate_tasks(manifest)
    phase4_tasks = [task for task in tasks if task.tier in {4, 5}]
    assert {task.template_id for task in phase4_tasks} == {
        "t4_adcs_esc1",
        "t4_rbcd_delegation",
        "t5_adcs_to_delegation_composite",
    }
    assert all(task.metadata["critical_nodes"] for task in phase4_tasks)
    assert all(task.metadata["template_version"] == "phase4_v1.0" for task in phase4_tasks)


def test_phase4_reference_cypher_covers_privileged_targets() -> None:
    manifest = _manifest_for_seed(4401)
    paths = {path["template_id"]: path for path in manifest["planted_paths"]}
    assert paths["t4_adcs_esc1"]["target_name"] in paths["t4_adcs_esc1"]["verification_cypher"]
    assert (
        paths["t5_adcs_to_delegation_composite"]["target_name"]
        in paths["t5_adcs_to_delegation_composite"]["verification_cypher"]
    )
    assert len(paths["t4_rbcd_delegation"]["critical_nodes"]) == 2


def test_run_config_generate_profile_writes_phase4_artifacts(tmp_path: Path) -> None:
    config = tmp_path / "run.yaml"
    config.write_text(
        """
version: 1
profiles:
  phase4_v1:
    kind: generate
    domain: phase4.test
    seed: 4401
    generator:
      profile: phase4_v1
    sizing:
      users: 18
      workstations: 7
      servers: 4
    output_zip: out/phase4.zip
    output_manifest: out/phase4_manifest.json
"""
    )

    runner = CliRunner()
    result = runner.invoke(main, ["run", "--config", str(config), "--profile", "phase4_v1"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "out/phase4.zip").exists()
    manifest = json.loads((tmp_path / "out/phase4_manifest.json").read_text())
    assert manifest["metadata"]["generator_profile"] == "phase4_v1"
    assert any(path["tier"] == 5 for path in manifest["planted_paths"])
