"""Structural checks for the immutable complex-v1 regression characterization."""

from __future__ import annotations

import json
from pathlib import Path

FIXTURE_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "complex_v1_development"
    / "regressions.json"
)


def test_complex_v1_regression_characterization_is_complete_and_stable() -> None:
    payload = json.loads(FIXTURE_PATH.read_text())

    assert payload["schema_version"] == "complex-v1-regression-characterization-v1"
    assert payload["campaign_status"] == "development_invalid_for_ranking"
    assert payload["benchmark_seed"] == 4401

    cases = payload["cases"]
    assert len(cases) == 8
    assert [case["id"] for case in cases] == [
        "CV1-001",
        "CV1-002",
        "CV1-003",
        "CV1-004",
        "CV1-005",
        "CV1-006",
        "CV1-007",
        "CV1-008",
    ]
    assert {case["track"] for case in cases} <= {"direct", "mcp", "both"}
    assert len({case["category"] for case in cases}) == len(cases)
    assert all(case["observed_behavior"] for case in cases)
    assert all(case["required_v2_behavior"] for case in cases)
    assert all(case["evidence"] for case in cases)
