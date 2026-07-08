from pathlib import Path

from scripts.phase4b_failure_attribution import collect_profiles, profile_counts, validate_expected


def test_codex_phase4b_expected_counts_reproduce() -> None:
    run_root = Path(
        "/Users/anton/projects/ori-run-artifacts/codex-openai-phase4b-v2-20260618-132651"
    )
    if not run_root.exists():
        return
    summary = {"profiles": [profile_counts(p) for p in collect_profiles(run_root)]}
    assert validate_expected(summary) == []
