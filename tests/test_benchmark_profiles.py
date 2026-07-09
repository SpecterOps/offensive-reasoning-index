from __future__ import annotations

from ori.generator.benchmark_profiles import build_benchmark_generation_profile


def test_seeded_benchmark_profile_is_reproducible() -> None:
    first = build_benchmark_generation_profile("simple", seed=1234)
    second = build_benchmark_generation_profile("simple", seed=1234)

    assert first == second
    assert first.to_metadata() == second.to_metadata()


def test_seeded_benchmark_profile_varies_by_seed() -> None:
    first = build_benchmark_generation_profile("simple", seed=1234)
    second = build_benchmark_generation_profile("simple", seed=5678)

    assert first != second
    assert (first.company_name, first.domain, first.users) != (
        second.company_name,
        second.domain,
        second.users,
    )


def test_simple_profile_stays_in_small_domain_band() -> None:
    profile = build_benchmark_generation_profile("simple", seed=1234)

    assert 80 <= profile.users <= 120
    assert 30 <= profile.workstations <= 50
    assert 10 <= profile.servers <= 20
    assert profile.domain == profile.domain.upper()


def test_complex_profile_stays_in_large_domain_band() -> None:
    profile = build_benchmark_generation_profile("complex", seed=1234)

    assert 4500 <= profile.users <= 5500
    assert 1750 <= profile.workstations <= 2250
    assert 400 <= profile.servers <= 600
    assert profile.domain == profile.domain.upper()


def test_same_seed_differs_between_simple_and_complex() -> None:
    simple = build_benchmark_generation_profile("simple", seed=1234)
    complex_profile = build_benchmark_generation_profile("complex", seed=1234)

    assert simple.benchmark == "simple"
    assert complex_profile.benchmark == "complex"
    assert simple.domain != complex_profile.domain
    assert simple.users < complex_profile.users
