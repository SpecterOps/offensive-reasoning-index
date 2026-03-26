"""Tests for the SharpHound JSON serializer."""

import json
import zipfile
from pathlib import Path

import pytest

from ori.generator.attack_paths import plant_all_paths
from ori.generator.graph import ADGraph
from ori.generator.org import build_org
from ori.generator.security import apply_baseline_security
from ori.generator.serializer import serialize_to_dir, serialize_to_zip


@pytest.fixture()
def sample_graph(tmp_path):
    graph = ADGraph("EVAL.TEST", seed=42)
    build_org(graph, num_users=10, num_workstations=4, num_servers=2)
    apply_baseline_security(graph)
    plant_all_paths(graph)
    return graph


def test_serialize_to_zip_creates_file(sample_graph, tmp_path):
    out = tmp_path / "test.zip"
    result = serialize_to_zip(sample_graph, out)
    assert result.exists()
    assert result.stat().st_size > 0


def test_zip_contains_expected_files(sample_graph, tmp_path):
    out = tmp_path / "test.zip"
    serialize_to_zip(sample_graph, out)
    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
    assert "users.json" in names
    assert "computers.json" in names
    assert "groups.json" in names
    assert "domains.json" in names
    assert "ous.json" in names


def test_json_meta_envelope(sample_graph, tmp_path):
    """Each JSON file must have data[] and meta{type, count, methods, version}."""
    out_dir = tmp_path / "output"
    serialize_to_dir(sample_graph, out_dir)

    users_file = out_dir / "users.json"
    assert users_file.exists()
    data = json.loads(users_file.read_text())

    assert "data" in data
    assert "meta" in data
    assert data["meta"]["type"] == "users"
    assert data["meta"]["count"] == len(data["data"])
    assert data["meta"]["methods"] == 0
    assert data["meta"]["version"] == 6


def test_user_objects_have_required_fields(sample_graph, tmp_path):
    out_dir = tmp_path / "output"
    serialize_to_dir(sample_graph, out_dir)
    data = json.loads((out_dir / "users.json").read_text())

    for user in data["data"]:
        assert "ObjectIdentifier" in user
        assert "Properties" in user
        assert "Aces" in user
        props = user["Properties"]
        assert "name" in props
        assert "domain" in props
        assert "enabled" in props


def test_computer_objects_have_required_fields(sample_graph, tmp_path):
    out_dir = tmp_path / "output"
    serialize_to_dir(sample_graph, out_dir)
    data = json.loads((out_dir / "computers.json").read_text())

    for comp in data["data"]:
        assert "ObjectIdentifier" in comp
        assert "Properties" in comp
        props = comp["Properties"]
        assert "name" in props
        assert "isdc" in props


def test_group_members_reference_real_sids(sample_graph, tmp_path):
    """All group member SIDs must exist in the graph."""
    out_dir = tmp_path / "output"
    serialize_to_dir(sample_graph, out_dir)

    all_sids = sample_graph.all_sids()

    data = json.loads((out_dir / "groups.json").read_text())
    for group in data["data"]:
        for member in group.get("Members", []):
            assert member["ObjectIdentifier"] in all_sids, (
                f"Group {group['ObjectIdentifier']} has member {member['ObjectIdentifier']} "
                f"that doesn't exist in the graph"
            )


def test_user_names_are_uppercase_domain(sample_graph, tmp_path):
    """User names must be in UPPERCASE@DOMAIN format."""
    out_dir = tmp_path / "output"
    serialize_to_dir(sample_graph, out_dir)
    data = json.loads((out_dir / "users.json").read_text())

    for user in data["data"]:
        name = user["Properties"]["name"]
        assert "@" in name, f"Name missing @: {name}"
        local, domain = name.rsplit("@", 1)
        assert local == local.upper(), f"Local part not uppercase: {name}"
        assert domain == domain.upper(), f"Domain not uppercase: {name}"


def test_domain_count(sample_graph, tmp_path):
    out_dir = tmp_path / "output"
    serialize_to_dir(sample_graph, out_dir)
    data = json.loads((out_dir / "domains.json").read_text())
    assert data["meta"]["count"] == 1
    assert data["data"][0]["ObjectIdentifier"] == sample_graph.domain_sid


def test_planted_paths_are_in_manifest(sample_graph):
    assert len(sample_graph.planted_paths) == 2
    ids = {p.template_id for p in sample_graph.planted_paths}
    assert "t1_admin_to" in ids
    assert "t1_has_session" in ids
