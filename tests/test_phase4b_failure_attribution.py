import csv
from pathlib import Path

from scripts.phase4b_failure_attribution import (
    EXPECTED_CODEX_COUNTS,
    collect_profiles,
    profile_counts,
    validate_expected,
)


def test_codex_phase4b_expected_counts_reproduce(tmp_path: Path) -> None:
    """Exercise CSV discovery and attribution without a private archived run."""
    outcomes = {
        "strict_score": ("CORRECT", ""),
        "CYPHER_SYNTAX_ERROR": ("CYPHER_ERROR", ""),
        "CYPHER_TOO_EXPENSIVE": ("QUERY_TOO_EXPENSIVE", ""),
        "INVALID_ENTITY": ("HALLUCINATION", ""),
        "NO_PATH_REPORTED": ("INCORRECT", "NO_PATH_REPORTED"),
        "INCOMPLETE_ANSWER": ("INCORRECT", "INCOMPLETE_ANSWER"),
    }
    for profile, counts in EXPECTED_CODEX_COUNTS.items():
        out = tmp_path / "outputs" / profile
        out.mkdir(parents=True)
        with (out / "baseline_combined.csv").open("w", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["outcome", "failure_subtype"])
            for category, count in counts.items():
                writer.writerows([outcomes[category]] * count)
    profiles = collect_profiles(tmp_path)
    assert len(profiles) == 8
    assert {p.mode for p in profiles} == {"direct", "mcp"}
    summary = {"profiles": [profile_counts(p) for p in profiles]}
    assert validate_expected(summary) == []
    direct = next(p for p in summary["profiles"] if p["profile"] ==
                  "codex_gpt_5_4_cyber_phase4b_v2_direct")
    assert direct["strict_score"] == 55
    assert direct["taxonomy_counts"]["CYPHER_SYNTAX_ERROR"] == 6
    assert direct["taxonomy_counts"]["CYPHER_TOO_EXPENSIVE"] == 3
    direct["strict_score"] -= 1
    assert len(validate_expected(summary)) == 1
    assert len(validate_expected({"profiles": []})) == 8
