"""Explicit test ownership for every frozen complex-v1 correctness defect."""

from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "tests" / "fixtures" / "complex_v1_development" / "regressions.json"

V2_REGRESSION_TESTS = {
    "CV1-001": (
        "tests/test_v2_runtime.py",
        "test_direct_runtime_preserves_execution_reasoning_separation",
    ),
    "CV1-002": (
        "tests/test_v2_compiler.py",
        "test_public_bundles_are_sealed_and_fingerprints_bind_claims",
    ),
    "CV1-003": (
        "tests/test_v2_comparator.py",
        "test_mechanism_valid_route_accepts_sealed_alternate_route",
    ),
    "CV1-004": (
        "tests/test_v2_comparator.py",
        "test_exact_set_accepts_aliases_and_rejects_wrong_empty_and_extra_entities",
    ),
    "CV1-005": (
        "tests/test_v2_compiler.py",
        "test_offline_scoring_uses_sealed_identity_catalog_and_shared_comparator",
    ),
    "CV1-006": (
        "tests/test_v2_compiler.py",
        "test_every_mcp_candidate_is_supported_by_the_pinned_capability_profile",
    ),
    "CV1-007": (
        "tests/test_v2_compiler.py",
        "test_complex_corpus_replaces_oversized_enumerations",
    ),
    "CV1-008": (
        "tests/test_v2_mcp.py",
        "test_common_loop_conformance_matrix_has_identical_semantics",
    ),
}


def test_every_frozen_cv1_defect_has_an_executable_v2_regression() -> None:
    ledger = json.loads(LEDGER.read_text())
    frozen_ids = {case["id"] for case in ledger["cases"]}

    assert set(V2_REGRESSION_TESTS) == frozen_ids
    for test_path, test_name in V2_REGRESSION_TESTS.values():
        path = ROOT / test_path
        tree = ast.parse(path.read_text(), filename=str(path))
        discovered = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("test_")
        }
        assert test_name in discovered, f"{test_path} no longer defines {test_name}"
