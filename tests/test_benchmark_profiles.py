from __future__ import annotations

from ori.generator.benchmark_profiles import build_benchmark_generation_profile


def test_seeded_profile_contract(subtests) -> None:
    first = build_benchmark_generation_profile("simple", seed=1234)
    second = build_benchmark_generation_profile("simple", seed=1234)
    changed = build_benchmark_generation_profile("simple", seed=5678)
    complex_profile = build_benchmark_generation_profile("complex", seed=1234)
    with subtests.test(msg="test_seeded_benchmark_profile_is_reproducible"):
        _assert_seeded_benchmark_profile_is_reproducible(first, second)
    with subtests.test(msg="test_seeded_benchmark_profile_varies_by_seed"):
        _assert_seeded_benchmark_profile_varies_by_seed(first, changed)
    with subtests.test(msg="test_simple_profile_stays_in_small_domain_band"):
        _assert_simple_profile_stays_in_small_domain_band(first)
    with subtests.test(msg="test_complex_profile_stays_in_large_domain_band"):
        _assert_complex_profile_stays_in_large_domain_band(complex_profile)
    with subtests.test(msg="test_same_seed_differs_between_simple_and_complex"):
        _assert_same_seed_differs_between_simple_and_complex(first, complex_profile)


def _assert_seeded_benchmark_profile_is_reproducible(first, second) -> None:
    assert first == second
    assert first.to_metadata() == second.to_metadata()


def _assert_seeded_benchmark_profile_varies_by_seed(first, second) -> None:
    assert first != second
    assert (first.company_name, first.domain, first.users) != (
        second.company_name,
        second.domain,
        second.users,
    )


def _assert_simple_profile_stays_in_small_domain_band(profile) -> None:
    assert 80 <= profile.users <= 120
    assert 30 <= profile.workstations <= 50
    assert 10 <= profile.servers <= 20
    assert profile.domain == profile.domain.upper()


def _assert_complex_profile_stays_in_large_domain_band(profile) -> None:
    assert 4500 <= profile.users <= 5500
    assert 1750 <= profile.workstations <= 2250
    assert 400 <= profile.servers <= 600
    assert profile.domain == profile.domain.upper()


def _assert_same_seed_differs_between_simple_and_complex(simple, complex_profile) -> None:
    assert simple.benchmark == "simple"
    assert complex_profile.benchmark == "complex"
    assert simple.domain != complex_profile.domain
    assert simple.users < complex_profile.users


def test_timestamp_version_preserves_existing_identity_and_scale() -> None:
    simple = build_benchmark_generation_profile("simple", seed=67)
    complex_profile = build_benchmark_generation_profile("complex", seed=67)

    assert simple.generator_version == "seeded-benchmark-v2"
    assert complex_profile.generator_version == "seeded-benchmark-v1"
    assert (
        simple.company_name,
        simple.domain,
        simple.users,
        simple.workstations,
        simple.servers,
    ) == ("Delta Dynamics", "DELTADYNAMICS.CORP", 99, 38, 20)
    assert (
        complex_profile.company_name,
        complex_profile.domain,
        complex_profile.users,
        complex_profile.workstations,
        complex_profile.servers,
    ) == ("Ironwood Foods", "IRONWOODFOODS.LAN", 4555, 1769, 437)
