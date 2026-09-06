"""Seeded benchmark generation profiles for public ORI benchmark products."""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class SizeBand:
    """Inclusive seeded size range for one benchmark product."""

    users: tuple[int, int]
    workstations: tuple[int, int]
    servers: tuple[int, int]


@dataclass(frozen=True)
class BenchmarkGenerationProfile:
    """Concrete seeded generation profile for one benchmark instance."""

    benchmark: str
    benchmark_version: str
    generator_version: str
    seed: int
    company_name: str
    domain: str
    users: int
    workstations: int
    servers: int

    def to_metadata(self) -> dict:
        return {
            "benchmark": self.benchmark,
            "benchmark_version": self.benchmark_version,
            "generator_version": self.generator_version,
            "seed": self.seed,
            "identity": {
                "company_name": self.company_name,
                "domain": self.domain,
            },
            "scale": {
                "users": self.users,
                "workstations": self.workstations,
                "servers": self.servers,
            },
        }


GENERATOR_VERSION = "seeded-benchmark-v1"
# Keep identity and scale RNG streams stable when an artifact encoding changes.
_RNG_NAMESPACE_VERSION = "seeded-benchmark-v1"
_SIMPLE_GENERATOR_VERSION = "seeded-benchmark-v2"
BENCHMARK_VERSION = "v1"

_SIZE_BANDS: dict[str, SizeBand] = {
    "oaic-2026-v1": SizeBand(users=(4500, 5500), workstations=(1750, 2250), servers=(400, 600)),
    "simple": SizeBand(users=(80, 120), workstations=(30, 50), servers=(10, 20)),
    "complex": SizeBand(users=(4500, 5500), workstations=(1750, 2250), servers=(400, 600)),
}

_COMPANY_PREFIXES = (
    "Apex",
    "Beacon",
    "Cinder",
    "Delta",
    "Evergreen",
    "Frontier",
    "Granite",
    "Helix",
    "Ironwood",
    "Juniper",
    "Keystone",
    "Lumina",
)

_COMPANY_SUFFIXES = (
    "Analytics",
    "Biologics",
    "Capital",
    "Dynamics",
    "Energy",
    "Foods",
    "Health",
    "Logistics",
    "Manufacturing",
    "Systems",
    "Telecom",
    "Works",
)

_TLDS = ("LOCAL", "CORP", "INTERNAL", "LAN")


def _rng_for(*, benchmark: str, seed: int, namespace: str) -> random.Random:
    material = f"{_RNG_NAMESPACE_VERSION}:{benchmark}:{seed}:{namespace}"
    return random.Random(material)


def _company_identity(*, benchmark: str, seed: int) -> tuple[str, str]:
    rng = _rng_for(benchmark=benchmark, seed=seed, namespace="identity")
    prefix = rng.choice(_COMPANY_PREFIXES)
    suffix = rng.choice(_COMPANY_SUFFIXES)
    company_name = f"{prefix} {suffix}"
    left = f"{prefix}{suffix}".upper()
    domain = f"{left}.{rng.choice(_TLDS)}"
    return company_name, domain


def _pick_size(rng: random.Random, band: tuple[int, int]) -> int:
    return rng.randint(band[0], band[1])


def build_benchmark_generation_profile(
    benchmark: str,
    *,
    seed: int,
) -> BenchmarkGenerationProfile:
    """Resolve benchmark + seed into a reproducible concrete generation profile."""

    normalized = benchmark.strip().lower()
    try:
        band = _SIZE_BANDS[normalized]
    except KeyError as exc:
        known = ", ".join(sorted(_SIZE_BANDS))
        raise ValueError(f"Unknown benchmark {benchmark!r}. Available benchmarks: {known}") from exc

    size_rng = _rng_for(benchmark=normalized, seed=seed, namespace="size")
    company_name, domain = _company_identity(benchmark=normalized, seed=seed)
    return BenchmarkGenerationProfile(
        benchmark=normalized,
        benchmark_version=BENCHMARK_VERSION,
        generator_version=(
            _SIMPLE_GENERATOR_VERSION if normalized == "simple" else GENERATOR_VERSION
        ),
        seed=seed,
        company_name=company_name,
        domain=domain,
        users=_pick_size(size_rng, band.users),
        workstations=_pick_size(size_rng, band.workstations),
        servers=_pick_size(size_rng, band.servers),
    )
