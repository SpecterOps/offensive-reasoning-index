"""CLI entry point for ori."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click
import yaml

from .benchmarks import describe_benchmark, get_benchmark, list_benchmarks
from .eval.direct_query_safety import DirectQuerySafetyConfig
from .eval.phase4_v2 import generate_phase4_v2_official_tasks
from .generator.archive_validation import _relationships_from_archive
from .generator.attack_paths import plant_all_paths
from .generator.benchmark_profiles import build_benchmark_generation_profile
from .generator.graph import ADGraph
from .generator.org import build_org
from .generator.phase4 import build_phase4_complex_graph, build_phase4_v1_graph
from .generator.phase4_v2 import build_phase4_v2_forest
from .generator.security import apply_baseline_security
from .generator.serializer import (
    _NODE_TYPE_TO_FILE,
    _VERSIONS,
    _build_zip,
    project_nodes_for_sharphound,
    serialize_forest_to_zip,
    serialize_to_dir,
    serialize_to_zip,
)
from .mcp_launcher import (
    MCPLauncherConfig,
    resolve_mcp_launcher_config,
    resolve_mcp_launcher_runtime,
)
from .relationships import (
    RELATIONSHIP_CONTRACT_VERSION,
    SHARPHOUND_PROFILE,
    canonical_relationship_kind,
)
from .run_config import (
    RunConfigOverrides,
    list_run_profiles,
    load_run_profile,
)


@dataclass(frozen=True)
class RunSpec:
    run_name: str
    requested_model: str
    concurrency: int
    runs_per_model: int
    ollama_options: dict | None
    model_base_url: str | None
    max_steps: int | None
    mcp_tool_loop: str | None
    openai_compat_telemetry_adapter: str | None
    mcp_ollama_read_timeout_seconds: float | None
    config_identity: dict[str, Any]
    config_identity_json: str
    file_slug: str


_MODEL_MATRIX_TOP_LEVEL_KEYS = {
    "version",
    "manifest",
    "modes",
    "output_dir",
    "defaults",
    "models",
}
_MODEL_MATRIX_DEFAULT_KEYS = {
    "concurrency",
    "runs_per_model",
    "bhce_url",
    "model_base_url",
    "max_model_reruns_on_infra",
    "health",
    "direct_query_safety",
    "mcp",
    "telemetry",
}
_MODEL_MATRIX_HEALTH_KEYS = {"timeout_seconds", "poll_interval"}
_MODEL_MATRIX_MCP_KEYS = {
    "mcp_dir",
    "launcher",
    "source",
    "revision",
    "executable",
    "max_steps",
    "resource_mode",
    "tool_loop",
    "openai_compat_telemetry_adapter",
    "ollama_read_timeout_seconds",
}
_MODEL_MATRIX_TELEMETRY_KEYS = {"enabled"}
_MODEL_MATRIX_MODEL_KEYS = {
    "name",
    "provider",
    "model",
    "concurrency",
    "runs_per_model",
    "model_base_url",
    "max_steps",
    "mcp_tool_loop",
    "tool_loop",
    "openai_compat_telemetry_adapter",
    "telemetry_adapter",
    "mcp_ollama_read_timeout_seconds",
    "options",
    "num_ctx",
}


def _reject_unknown_config_keys(
    value: dict[str, Any], *, supported: set[str], context: str
) -> None:
    unknown = sorted(set(value) - supported)
    if unknown:
        raise click.UsageError(f"Unsupported {context} setting(s): {', '.join(unknown)}")


def _validate_model_matrix_config(data: dict[str, Any]) -> None:
    """Reject misspelled or misplaced model-matrix settings."""

    _reject_unknown_config_keys(
        data,
        supported=_MODEL_MATRIX_TOP_LEVEL_KEYS,
        context="model-matrix",
    )
    defaults = data.get("defaults") or {}
    if not isinstance(defaults, dict):
        raise click.UsageError("defaults must be a mapping when present.")
    _reject_unknown_config_keys(
        defaults,
        supported=_MODEL_MATRIX_DEFAULT_KEYS,
        context="model-matrix defaults",
    )

    nested_sections = (
        ("health", _MODEL_MATRIX_HEALTH_KEYS),
        (
            "direct_query_safety",
            {field.name for field in DirectQuerySafetyConfig.__dataclass_fields__.values()},
        ),
        ("mcp", _MODEL_MATRIX_MCP_KEYS),
        ("telemetry", _MODEL_MATRIX_TELEMETRY_KEYS),
    )
    for section, supported in nested_sections:
        section_value = defaults.get(section) or {}
        if not isinstance(section_value, dict):
            raise click.UsageError(f"defaults.{section} must be a mapping when present.")
        _reject_unknown_config_keys(
            section_value,
            supported=supported,
            context=f"defaults.{section}",
        )

    model_entries = data.get("models")
    if not isinstance(model_entries, list) or not model_entries:
        raise click.UsageError("Model-matrix configs require a non-empty models list.")
    for index, entry in enumerate(model_entries):
        if isinstance(entry, str):
            continue
        if not isinstance(entry, dict):
            raise click.UsageError(f"models[{index}] must be a model string or mapping.")
        _reject_unknown_config_keys(
            entry,
            supported=_MODEL_MATRIX_MODEL_KEYS,
            context=f"models[{index}]",
        )


def _write_campaign_config_snapshot(
    *,
    config_file: Path,
    resolved_config: dict[str, Any],
    output_dir: Path,
    manifest_path: Path,
    modes: list[str],
    cli_overrides: dict[str, Any],
) -> None:
    """Preserve the exact source config and resolved run provenance."""

    output_dir.mkdir(parents=True, exist_ok=True)
    source_text = config_file.read_text()
    source_snapshot = output_dir / "campaign-config.source.yaml"
    runnable_snapshot = output_dir / "campaign-config.yaml"
    provenance_snapshot = output_dir / "campaign-provenance.yaml"
    runnable_text = yaml.safe_dump(resolved_config, sort_keys=False)
    campaign_config_sha256 = hashlib.sha256(runnable_text.encode("utf-8")).hexdigest()
    manifest_sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    provenance_record = {
        "version": 1,
        "source_config": str(config_file.resolve()),
        "source_config_sha256": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
        "campaign_config_sha256": campaign_config_sha256,
        "manifest": str(manifest_path),
        "manifest_sha256": manifest_sha256,
        "output_dir": str(output_dir),
        "modes": modes,
        "cli_overrides": cli_overrides,
    }
    provenance_text = yaml.safe_dump(provenance_record, sort_keys=False)

    if config_file.resolve() == runnable_snapshot.resolve():
        if not source_snapshot.exists() or not provenance_snapshot.exists():
            raise click.UsageError(
                f"{output_dir} is missing campaign provenance required to resume from "
                "campaign-config.yaml. Choose a new output_dir."
            )
        try:
            existing_provenance = yaml.safe_load(provenance_snapshot.read_text())
        except yaml.YAMLError as exc:
            raise click.UsageError(f"{provenance_snapshot} is not valid YAML: {exc}") from exc
        expected_provenance = {
            "campaign_config_sha256": campaign_config_sha256,
            "manifest": str(manifest_path),
            "manifest_sha256": manifest_sha256,
            "output_dir": str(output_dir),
            "modes": modes,
        }
        provenance_matches = isinstance(existing_provenance, dict) and all(
            existing_provenance.get(key) == value for key, value in expected_provenance.items()
        )
        if runnable_snapshot.read_text() != runnable_text or not provenance_matches:
            raise click.UsageError(
                f"{output_dir} contains campaign-config.yaml that no longer matches "
                "its recorded provenance. Choose a new output_dir."
            )
        return

    for snapshot_path, expected_text in (
        (source_snapshot, source_text),
        (runnable_snapshot, runnable_text),
        (provenance_snapshot, provenance_text),
    ):
        if snapshot_path.exists():
            if snapshot_path.read_text() != expected_text:
                raise click.UsageError(
                    f"{output_dir} already contains different campaign provenance "
                    f"in {snapshot_path.name}. Choose a new output_dir."
                )
            continue
        snapshot_path.write_text(expected_text)


def _parse_ollama_options(options: tuple[str, ...]) -> dict:
    parsed: dict = {}
    for option in options:
        if "=" not in option:
            raise click.BadParameter(f"Invalid --ollama-option {option!r}. Expected KEY=VALUE.")
        key, raw_value = option.split("=", 1)
        key = key.strip()
        if not key:
            raise click.BadParameter("Ollama option key cannot be empty.")
        parsed[key] = yaml.safe_load(raw_value)
    return parsed


def _normalize_identity_config(entry: dict[str, Any]) -> dict[str, Any]:
    identity = {k: v for k, v in entry.items() if k not in {"name", "runs_per_model"}}
    options = dict(identity.get("options") or {})
    if "num_ctx" in identity:
        options["num_ctx"] = identity.pop("num_ctx")
    if options:
        identity["options"] = options
    else:
        identity.pop("options", None)
    return identity


def _identity_parts(value: Any, prefix: str = "") -> list[str]:
    if isinstance(value, dict):
        parts: list[str] = []
        for key in sorted(value):
            next_prefix = f"{prefix}.{key}" if prefix else key
            parts.extend(_identity_parts(value[key], next_prefix))
        return parts
    return [f"{prefix}={json.dumps(value, sort_keys=True)}"]


def _fallback_run_name(model: str, config_identity: dict[str, Any]) -> str:
    extras = {key: value for key, value in config_identity.items() if key != "model"}
    if not extras:
        return model
    return f"{model} [{', '.join(_identity_parts(extras))}]"


def _slugify_run_name(text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("._-")
    return slug or "run"


def _run_spec_from_entry(
    entry: str | dict[str, Any],
    *,
    default_concurrency: int,
    default_runs_per_model: int = 1,
) -> RunSpec:
    if isinstance(entry, str):
        explicit_name = None
        model = entry
        concurrency = default_concurrency
        runs_per_model = default_runs_per_model
        identity: dict[str, Any] = {"model": entry}
    elif isinstance(entry, dict):
        provider = entry.get("provider")
        raw_model = entry.get("model")
        if raw_model is None:
            raise click.UsageError("Model config objects must include a model field.")
        if provider and "/" not in str(raw_model):
            model = f"{provider}/{raw_model}"
        else:
            model = str(raw_model)
        explicit_name = entry.get("name")
        concurrency = entry.get("concurrency", default_concurrency)
        runs_per_model = entry.get("runs_per_model", default_runs_per_model)
        identity = _normalize_identity_config({**entry, "model": model})
    else:
        raise click.UsageError("Model config entries must be strings or objects.")

    if isinstance(runs_per_model, bool) or not isinstance(runs_per_model, int):
        raise click.UsageError("runs_per_model must be an integer.")
    if runs_per_model < 1:
        raise click.UsageError("runs_per_model must be at least 1.")

    config_identity_json = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    run_name = explicit_name or _fallback_run_name(model, identity)
    return RunSpec(
        run_name=run_name,
        requested_model=model,
        concurrency=concurrency,
        runs_per_model=runs_per_model,
        ollama_options=identity.get("options") or None,
        model_base_url=identity.get("model_base_url"),
        max_steps=identity.get("max_steps"),
        mcp_tool_loop=identity.get("mcp_tool_loop") or identity.get("tool_loop"),
        openai_compat_telemetry_adapter=identity.get("openai_compat_telemetry_adapter")
        or identity.get("telemetry_adapter"),
        mcp_ollama_read_timeout_seconds=identity.get("mcp_ollama_read_timeout_seconds"),
        config_identity=identity,
        config_identity_json=config_identity_json,
        file_slug=_slugify_run_name(run_name),
    )


def _dedupe_run_specs(run_specs: list[RunSpec]) -> list[RunSpec]:
    run_names_seen: set[str] = set()
    slug_counts: dict[str, int] = {}
    deduped: list[RunSpec] = []

    for run_spec in run_specs:
        if run_spec.run_name in run_names_seen:
            raise click.UsageError(
                f"Duplicate model run identity {run_spec.run_name!r}. "
                "Add unique `name` values in the models file."
            )
        run_names_seen.add(run_spec.run_name)

        base_slug = _slugify_run_name(run_spec.run_name)
        file_slug = base_slug
        if base_slug in slug_counts:
            short_hash = hashlib.sha1(run_spec.config_identity_json.encode("utf-8")).hexdigest()[:8]
            file_slug = f"{base_slug}-{short_hash}"
        slug_counts[base_slug] = slug_counts.get(base_slug, 0) + 1

        deduped.append(
            RunSpec(
                run_name=run_spec.run_name,
                requested_model=run_spec.requested_model,
                concurrency=run_spec.concurrency,
                runs_per_model=run_spec.runs_per_model,
                ollama_options=run_spec.ollama_options,
                model_base_url=run_spec.model_base_url,
                max_steps=run_spec.max_steps,
                mcp_tool_loop=run_spec.mcp_tool_loop,
                openai_compat_telemetry_adapter=run_spec.openai_compat_telemetry_adapter,
                mcp_ollama_read_timeout_seconds=run_spec.mcp_ollama_read_timeout_seconds,
                config_identity=run_spec.config_identity,
                config_identity_json=run_spec.config_identity_json,
                file_slug=file_slug,
            )
        )

    return deduped


def _build_run_specs(models: tuple[str, ...], models_file: str | None) -> list[RunSpec]:
    run_specs: list[RunSpec] = []

    if models_file:
        with open(models_file) as f:
            cfg = yaml.safe_load(f)
        default_concurrency = cfg.get("defaults", {}).get("concurrency", 1)
        default_runs_per_model = cfg.get("defaults", {}).get("runs_per_model", 1)
        for entry in cfg.get("models", []):
            run_specs.append(
                _run_spec_from_entry(
                    entry,
                    default_concurrency=default_concurrency,
                    default_runs_per_model=default_runs_per_model,
                )
            )

    for model in models:
        run_specs.append(_run_spec_from_entry(model, default_concurrency=1))

    if not run_specs:
        raise click.UsageError("Provide at least one model via --model or --models-file.")

    return _dedupe_run_specs(run_specs)


def _build_inline_run_spec(model: str, ollama_options: dict | None = None) -> RunSpec:
    identity: dict[str, Any] = {"model": model}
    if ollama_options:
        identity["options"] = dict(ollama_options)
    config_identity_json = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    run_name = _fallback_run_name(model, identity)
    return RunSpec(
        run_name=run_name,
        requested_model=model,
        concurrency=1,
        runs_per_model=1,
        ollama_options=ollama_options,
        model_base_url=None,
        max_steps=None,
        mcp_tool_loop=None,
        openai_compat_telemetry_adapter=None,
        mcp_ollama_read_timeout_seconds=None,
        config_identity=identity,
        config_identity_json=config_identity_json,
        file_slug=_slugify_run_name(run_name),
    )


def _write_generated_dataset(
    *,
    graph: ADGraph,
    seed: int,
    output_zip: Path,
    output_manifest: Path,
    generator_profile: str,
    metadata: dict[str, Any] | None = None,
) -> dict:
    serialize_to_zip(graph, output_zip)
    manifest = _build_manifest(graph, seed, archive=output_zip.read_bytes())
    manifest.setdefault("metadata", {})
    manifest["metadata"].update(
        {
            "generator_profile": generator_profile,
            "profile_kind": "generate",
        }
    )
    if metadata:
        manifest["metadata"].update(metadata)
    output_manifest.parent.mkdir(parents=True, exist_ok=True)
    output_manifest.write_text(json.dumps(manifest, indent=2))
    return manifest


def _write_phase4_v2_artifacts(corpus: dict[str, Any], output_manifest: Path) -> None:
    output_manifest.parent.mkdir(parents=True, exist_ok=True)
    output_manifest.write_text(json.dumps(corpus, indent=2))
    artifact_map = {
        "generation_summary.json": corpus["generation_summary"],
        "template_instances.json": corpus["template_instances"],
        "tasks_official.json": corpus["tasks_official"],
        "tasks_candidates.json": corpus["tasks_candidates"],
        "answers.json": corpus["answers"],
        "matrix_validation.json": corpus["matrix_validation"],
        "run_summary.json": corpus["run_summary"],
    }
    for filename, payload in artifact_map.items():
        (output_manifest.parent / filename).write_text(json.dumps(payload, indent=2) + "\n")


def _generate_profile_dataset(resolved) -> None:
    if resolved.generator_profile in {"phase4_v2_medium", "phase4_v2_small"}:
        profile = resolved.generator_profile.removeprefix("phase4_v2_")
        forest = build_phase4_v2_forest(
            profile=profile,
            seed=resolved.seed,
            parent_domain=resolved.domain,
        )
        corpus = generate_phase4_v2_official_tasks(forest)
        serialize_forest_to_zip(forest, Path(resolved.output_zip))
        _write_phase4_v2_artifacts(corpus, Path(resolved.output_manifest))
        click.echo(f"Generated {resolved.generator_profile}:")
        click.echo(f"  Zip: {resolved.output_zip}")
        click.echo(f"  Manifest: {resolved.output_manifest}")
        click.echo(
            f"  Official tasks: {corpus['official_count']} | "
            f"Smoke: {corpus['matrix_validation']['startup_smoke_count']} | "
            f"Matrix: {corpus['matrix_validation']['benchmark_matrix_count']}"
        )
        return

    if resolved.generator_profile != "phase4_v1":
        raise click.UsageError(
            f"Unsupported generator profile {resolved.generator_profile!r}; expected phase4_v1, "
            "phase4_v2_small, or phase4_v2_medium."
        )
    graph = build_phase4_v1_graph(
        domain=resolved.domain,
        seed=resolved.seed,
        users=resolved.users or 32,
        workstations=resolved.workstations or 12,
        servers=resolved.servers or 6,
    )
    manifest = _write_generated_dataset(
        graph=graph,
        seed=resolved.seed,
        output_zip=Path(resolved.output_zip),
        output_manifest=Path(resolved.output_manifest),
        generator_profile=resolved.generator_profile,
    )
    click.echo(f"Generated {resolved.generator_profile}:")
    click.echo(f"  Zip: {resolved.output_zip}")
    click.echo(f"  Manifest: {resolved.output_manifest}")
    click.echo(
        f"  Nodes: {manifest['stats']['total_nodes']} | "
        f"Edges: {manifest['stats']['total_edges']} | "
        f"Paths: {len(manifest['planted_paths'])}"
    )


def _effective_run_config(
    run_spec: RunSpec,
    *,
    model_base_url: str | None = None,
    max_steps: int | None = None,
    resource_mode: str | None = None,
    mcp_tool_loop: str | None = None,
    openai_compat_telemetry_adapter: str | None = None,
    mcp_ollama_read_timeout_seconds: float | None = None,
    telemetry_enabled: bool | None = None,
    mcp_launcher: MCPLauncherConfig | None = None,
    run_index: int | None = None,
    runs_per_model: int | None = None,
    direct_query_safety: DirectQuerySafetyConfig | None = None,
) -> dict[str, Any]:
    config = dict(run_spec.config_identity)
    if model_base_url is not None:
        config["model_base_url"] = model_base_url
    if max_steps is not None:
        config["max_steps"] = max_steps
    if resource_mode is not None:
        config["resource_mode"] = resource_mode
    if mcp_tool_loop is not None:
        config.pop("tool_loop", None)
        config["mcp_tool_loop"] = mcp_tool_loop
    if openai_compat_telemetry_adapter is not None:
        config.pop("telemetry_adapter", None)
        config["openai_compat_telemetry_adapter"] = openai_compat_telemetry_adapter
    if mcp_ollama_read_timeout_seconds is not None:
        config["mcp_ollama_read_timeout_seconds"] = mcp_ollama_read_timeout_seconds
    if run_index is not None:
        config["run_index"] = run_index
    if runs_per_model is not None:
        config["runs_per_model"] = runs_per_model
    if direct_query_safety is not None:
        config["direct_query_safety"] = direct_query_safety.to_jsonable()
    if mcp_launcher is not None:
        config.update(resolve_mcp_launcher_runtime(mcp_launcher).provenance(mcp_launcher))
    return config


async def _run_baseline_with_specs(
    *,
    manifest_path: Path,
    run_specs: list[RunSpec],
    output_dir: Path,
    concurrency_override: int | None,
    bhce_url: str | None,
    default_model_base_url: str | None = None,
    model_base_url_override: str | None = None,
    max_model_reruns_on_infra: int = 1,
    health_timeout_seconds: float = 60.0,
    health_poll_interval: float = 5.0,
    telemetry_enabled: bool = True,
    direct_query_safety: DirectQuerySafetyConfig | None = None,
) -> dict[str, list]:
    from .eval.runner import run_eval_cli_bare

    output_dir.mkdir(parents=True, exist_ok=True)
    safety_config = direct_query_safety or DirectQuerySafetyConfig()
    results = {}
    total_runs = sum(run_spec.runs_per_model for run_spec in run_specs)
    run_number = 0
    for run_spec in run_specs:
        effective_concurrency = (
            concurrency_override if concurrency_override is not None else run_spec.concurrency
        )
        effective_model_base_url = (
            model_base_url_override
            if model_base_url_override is not None
            else run_spec.model_base_url or default_model_base_url
        )
        opts_str = f", options={run_spec.ollama_options}" if run_spec.ollama_options else ""
        base_url_str = f", base_url={effective_model_base_url}" if effective_model_base_url else ""
        for run_index in range(1, run_spec.runs_per_model + 1):
            run_number += 1
            if run_spec.runs_per_model == 1:
                csv_path = output_dir / f"{run_spec.file_slug}.csv"
                result_key = run_spec.run_name
            else:
                csv_path = output_dir / run_spec.file_slug / f"run-{run_index:03d}.csv"
                csv_path.parent.mkdir(parents=True, exist_ok=True)
                result_key = f"{run_spec.run_name} [run-{run_index:03d}]"
            click.echo(
                f"\n[{run_number}/{total_runs}] {result_key} -> {run_spec.requested_model}  "
                f"(concurrency={effective_concurrency}{opts_str}{base_url_str})"
            )
            results[result_key] = await run_eval_cli_bare(
                manifest_path=manifest_path,
                model=run_spec.requested_model,
                output_path=csv_path,
                concurrency=effective_concurrency,
                bhce_url=bhce_url,
                ollama_options=run_spec.ollama_options,
                max_model_reruns_on_infra=max_model_reruns_on_infra,
                run_name=run_spec.run_name,
                run_config=_effective_run_config(
                    run_spec,
                    model_base_url=effective_model_base_url,
                    telemetry_enabled=telemetry_enabled,
                    run_index=run_index,
                    runs_per_model=run_spec.runs_per_model,
                    direct_query_safety=safety_config,
                ),
                model_base_url=effective_model_base_url,
                health_timeout_seconds=health_timeout_seconds,
                health_poll_interval=health_poll_interval,
                telemetry_enabled=telemetry_enabled,
                direct_query_safety=safety_config,
            )
    return results


async def _run_baseline_mcp_with_specs(
    *,
    manifest_path: Path,
    run_specs: list[RunSpec],
    output_dir: Path,
    concurrency_override: int | None,
    bhce_url: str | None,
    mcp_dir: Path | None,
    mcp_launcher: MCPLauncherConfig | None,
    max_steps: int,
    resource_mode: str,
    mcp_tool_loop: str,
    openai_compat_telemetry_adapter: str,
    mcp_ollama_read_timeout_seconds: float,
    default_model_base_url: str | None = None,
    max_steps_override: int | None = None,
    model_base_url_override: str | None = None,
    mcp_tool_loop_override: str | None = None,
    openai_compat_telemetry_adapter_override: str | None = None,
    mcp_ollama_read_timeout_seconds_override: float | None = None,
    max_model_reruns_on_infra: int = 1,
    health_timeout_seconds: float = 60.0,
    health_poll_interval: float = 5.0,
    telemetry_enabled: bool = True,
) -> dict[str, list]:
    from .eval.runner import run_eval_mcp_cli_bare

    output_dir.mkdir(parents=True, exist_ok=True)
    resolved_mcp_launcher = mcp_launcher or MCPLauncherConfig.local_checkout(
        (mcp_dir or (Path.cwd().parent / "bloodhound-mcp")).resolve()
    )
    results = {}
    total_runs = sum(run_spec.runs_per_model for run_spec in run_specs)
    run_number = 0
    for run_spec in run_specs:
        effective_concurrency = (
            concurrency_override if concurrency_override is not None else run_spec.concurrency
        )
        effective_max_steps = (
            max_steps_override
            if max_steps_override is not None
            else run_spec.max_steps
            if run_spec.max_steps is not None
            else max_steps
        )
        effective_model_base_url = (
            model_base_url_override
            if model_base_url_override is not None
            else run_spec.model_base_url or default_model_base_url
        )
        effective_mcp_ollama_read_timeout_seconds = (
            mcp_ollama_read_timeout_seconds_override
            if mcp_ollama_read_timeout_seconds_override is not None
            else run_spec.mcp_ollama_read_timeout_seconds
            if run_spec.mcp_ollama_read_timeout_seconds is not None
            else mcp_ollama_read_timeout_seconds
        )
        effective_mcp_tool_loop = (
            mcp_tool_loop_override
            if mcp_tool_loop_override is not None
            else run_spec.mcp_tool_loop or mcp_tool_loop
        )
        effective_openai_compat_telemetry_adapter = (
            openai_compat_telemetry_adapter_override
            if openai_compat_telemetry_adapter_override is not None
            else run_spec.openai_compat_telemetry_adapter or openai_compat_telemetry_adapter
        )
        opts_str = f", options={run_spec.ollama_options}" if run_spec.ollama_options else ""
        base_url_str = f", base_url={effective_model_base_url}" if effective_model_base_url else ""
        for run_index in range(1, run_spec.runs_per_model + 1):
            run_number += 1
            if run_spec.runs_per_model == 1:
                csv_path = output_dir / f"{run_spec.file_slug}.csv"
                result_key = run_spec.run_name
            else:
                csv_path = output_dir / run_spec.file_slug / f"run-{run_index:03d}.csv"
                csv_path.parent.mkdir(parents=True, exist_ok=True)
                result_key = f"{run_spec.run_name} [run-{run_index:03d}]"
            click.echo(
                f"\n[{run_number}/{total_runs}] {result_key} -> {run_spec.requested_model}  "
                f"(concurrency={effective_concurrency}{opts_str}{base_url_str}, "
                f"max_steps={effective_max_steps})"
            )
            results[result_key] = await run_eval_mcp_cli_bare(
                manifest_path=manifest_path,
                model=run_spec.requested_model,
                output_path=csv_path,
                concurrency=effective_concurrency,
                bhce_url=bhce_url,
                ollama_options=run_spec.ollama_options,
                max_model_reruns_on_infra=max_model_reruns_on_infra,
                mcp_dir=resolved_mcp_launcher.mcp_dir,
                mcp_launcher=resolved_mcp_launcher,
                max_steps=effective_max_steps,
                resource_mode=resource_mode,
                mcp_tool_loop=effective_mcp_tool_loop,
                openai_compat_telemetry_adapter=effective_openai_compat_telemetry_adapter,
                mcp_ollama_read_timeout_seconds=effective_mcp_ollama_read_timeout_seconds,
                run_name=run_spec.run_name,
                run_config=_effective_run_config(
                    run_spec,
                    model_base_url=effective_model_base_url,
                    max_steps=effective_max_steps,
                    resource_mode=resource_mode,
                    mcp_tool_loop=effective_mcp_tool_loop,
                    openai_compat_telemetry_adapter=effective_openai_compat_telemetry_adapter,
                    mcp_ollama_read_timeout_seconds=effective_mcp_ollama_read_timeout_seconds,
                    telemetry_enabled=telemetry_enabled,
                    mcp_launcher=resolved_mcp_launcher,
                    run_index=run_index,
                    runs_per_model=run_spec.runs_per_model,
                ),
                model_base_url=effective_model_base_url,
                health_timeout_seconds=health_timeout_seconds,
                health_poll_interval=health_poll_interval,
                telemetry_enabled=telemetry_enabled,
            )
    return results


@click.group()
def main() -> None:
    """ori: benchmark for evaluating AI on AD attack path analysis."""
    # Load .env from the current directory (or any parent) on every invocation.
    # override=False means shell env vars and CI/CD vars take precedence over the file.
    from dotenv import load_dotenv

    load_dotenv(override=False)


@main.group(name="discovery")
def discovery_group() -> None:
    """Compile, preflight, and grade V28-native open-world discovery artifacts."""


@discovery_group.command(name="compile")
@click.option(
    "--manifest",
    "manifest_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    help="Standard ORI source manifest paired with the SharpHound archive.",
)
@click.option(
    "--archive",
    "archive_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    help="Exact standard ORI SharpHound ZIP.",
)
@click.option(
    "--v2-public",
    "v2_public_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    help="V28 public artifact compiled from the same manifest and archive.",
)
@click.option(
    "--v2-oracles",
    "v2_oracle_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    help="Matching scorer-only V28 oracle artifact.",
)
@click.option(
    "--output-dir",
    required=True,
    type=click.Path(file_okay=False),
)
def discovery_compile_command(
    manifest_path: str,
    archive_path: str,
    v2_public_path: str,
    v2_oracle_path: str,
    output_dir: str,
) -> None:
    """Compile closed V28 route truth into separated discovery artifacts."""
    from .discovery import compile_discovery_files

    try:
        paths = compile_discovery_files(
            manifest_path=Path(manifest_path),
            archive_path=Path(archive_path),
            v2_public_path=Path(v2_public_path),
            v2_oracle_path=Path(v2_oracle_path),
            output_dir=Path(output_dir),
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo("DISCOVERY COMPILE: PASS")
    click.echo(f"  public: {paths['public']}")
    click.echo(f"  private: {paths['private']}")


@discovery_group.command(name="preflight")
@click.option(
    "--public",
    "public_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
)
@click.option(
    "--private",
    "private_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
)
@click.option("--output", "output_path", type=click.Path(dir_okay=False))
def discovery_preflight_command(
    public_path: str,
    private_path: str,
    output_path: str | None,
) -> None:
    """Validate a discovery pair before any external model execution."""
    from .discovery import preflight_discovery_files

    try:
        report = preflight_discovery_files(
            public_path=Path(public_path),
            private_path=Path(private_path),
            output_path=Path(output_path) if output_path else None,
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo("DISCOVERY PREFLIGHT: PASS")
    click.echo(f"  objectives={report.target_count} variants={report.variant_count}")


@discovery_group.command(name="grade")
@click.option(
    "--public",
    "public_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
)
@click.option(
    "--private",
    "private_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
)
@click.option(
    "--submission",
    "submission_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
)
@click.option(
    "--output",
    "output_path",
    required=True,
    type=click.Path(dir_okay=False),
)
def discovery_grade_command(
    public_path: str,
    private_path: str,
    submission_path: str,
    output_path: str,
) -> None:
    """Grade one bounded structured submission; never execute a model or upload."""
    from .discovery.grader import grade_discovery_files

    try:
        report = grade_discovery_files(
            public_path=Path(public_path),
            private_path=Path(private_path),
            submission_path=Path(submission_path),
            output_path=Path(output_path),
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo("DISCOVERY GRADE: PASS")
    click.echo(
        f"  f1={report.f1:.6f} precision={report.precision:.6f} "
        f"recall={report.recall:.6f} false_positives={report.false_positive_count}"
    )


@main.group(name="benchmark")
def benchmark_group() -> None:
    """Explore named ORI benchmark products."""


@benchmark_group.command(name="list")
def benchmark_list() -> None:
    """List public benchmark products."""

    for benchmark in list_benchmarks():
        click.echo(
            f"{benchmark.name:8} {benchmark.status:8} "
            f"tasks/track={benchmark.default_task_count:<3} "
            f"tracks={','.join(track.name for track in benchmark.tracks)} "
            f"modes={','.join(benchmark.supported_modes)}"
        )
        click.echo(f"         {benchmark.summary}")


@benchmark_group.command(name="describe")
@click.argument("name")
def benchmark_describe(name: str) -> None:
    """Describe one public benchmark product."""

    try:
        click.echo(describe_benchmark(name))
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc


@benchmark_group.command(name="generate")
@click.argument("name")
@click.option("--seed", type=int, required=True, help="Seed for reproducible benchmark generation.")
@click.option(
    "--output-dir",
    type=click.Path(),
    default="datasets/benchmarks",
    show_default=True,
    help="Directory for generated benchmark zip and manifest artifacts.",
)
@click.option(
    "--output-prefix",
    default=None,
    help="Optional artifact filename prefix. Defaults to <benchmark>-<version>-seed-<seed>.",
)
def benchmark_generate(name: str, seed: int, output_dir: str, output_prefix: str | None) -> None:
    """Generate a seeded simple/complex benchmark dataset and manifest."""

    try:
        benchmark = get_benchmark(name)
        profile = build_benchmark_generation_profile(benchmark.name, seed=seed)
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc

    if benchmark.name == "simple":
        graph = ADGraph(domain=profile.domain, seed=seed)
        build_org(
            graph,
            num_users=profile.users,
            num_workstations=profile.workstations,
            num_servers=profile.servers,
        )
        apply_baseline_security(graph)
        plant_all_paths(graph)
        generator_profile = benchmark.graph_profile
    elif benchmark.name == "complex":
        graph = build_phase4_complex_graph(
            domain=profile.domain,
            seed=seed,
            users=profile.users,
            workstations=profile.workstations,
            servers=profile.servers,
        )
        generator_profile = benchmark.graph_profile
    else:  # defensive; get_benchmark already validates today.
        raise click.UsageError(f"Unsupported benchmark {benchmark.name!r}")

    artifact_prefix = output_prefix or f"{benchmark.name}-{profile.benchmark_version}-seed-{seed}"
    artifact_dir = Path(output_dir)
    zip_path = artifact_dir / f"{artifact_prefix}.zip"
    manifest_path = artifact_dir / f"{artifact_prefix}_manifest.json"
    manifest = _write_generated_dataset(
        graph=graph,
        seed=seed,
        output_zip=zip_path,
        output_manifest=manifest_path,
        generator_profile=generator_profile,
        metadata={
            **profile.to_metadata(),
            "graph_profile": benchmark.graph_profile,
            "official_task_set": benchmark.official_task_set,
            "diagnostic_task_set": benchmark.diagnostic_task_set,
            "default_task_count": benchmark.default_task_count,
            "diagnostic_task_count": benchmark.diagnostic_task_count,
            "benchmark_tracks": {
                track.name: {
                    "task_set": track.task_set,
                    "diagnostic_task_set": track.diagnostic_task_set,
                    "task_count": track.task_count,
                    "diagnostic_task_count": track.diagnostic_task_count,
                    "scoring_profile": track.scoring_profile,
                    "description": track.description,
                }
                for track in benchmark.tracks
            },
            "shared_dataset_across_tracks": True,
            "scoring_profile": benchmark.scoring_profile,
        },
    )

    click.echo(f"Generated benchmark: {benchmark.name}")
    click.echo(f"  Version: {profile.benchmark_version}")
    click.echo(f"  Generator: {profile.generator_version}")
    click.echo(f"  Seed: {seed}")
    click.echo(f"  Company: {profile.company_name}")
    click.echo(f"  Domain: {profile.domain}")
    click.echo(
        f"  Scale: users={profile.users}, "
        f"workstations={profile.workstations}, servers={profile.servers}"
    )
    click.echo(f"  Zip: {zip_path}")
    click.echo(f"  Manifest: {manifest_path}")
    click.echo(
        f"  Nodes: {manifest['stats']['total_nodes']} | "
        f"Edges: {manifest['stats']['total_edges']} | "
        f"Paths: {len(manifest['planted_paths'])}"
    )


@benchmark_group.command(name="run")
@click.argument("name")
@click.option(
    "--mode",
    type=click.Choice(["direct", "mcp", "mock", "diagnostic"]),
    default="mcp",
    show_default=True,
    help="Benchmark mode to run once benchmark launch plumbing is enabled.",
)
def benchmark_run(name: str, mode: str) -> None:
    """Show the planned stable run surface for a benchmark.

    This command is intentionally a dry-run placeholder while ORI migrates from
    phase-specific run-config files to simple/complex benchmark products.
    """

    try:
        benchmark = get_benchmark(name)
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc
    if mode not in benchmark.supported_modes:
        raise click.UsageError(
            f"Benchmark {benchmark.name!r} does not support mode {mode!r}. "
            f"Supported modes: {', '.join(benchmark.supported_modes)}"
        )
    if mode == "diagnostic":
        task_set = benchmark.diagnostic_task_set or benchmark.official_task_set
        task_count = benchmark.diagnostic_task_count or benchmark.default_task_count
        track_lines = [
            f"  {track.name}: {track.diagnostic_task_set or track.task_set} "
            f"({track.diagnostic_task_count or track.task_count} tasks)"
            for track in benchmark.tracks
        ]
    elif mode in ("direct", "mcp"):
        track = benchmark.track(mode)
        task_set = track.task_set
        task_count = track.task_count
        track_lines = [
            f"  {track.name}: {track.task_set} ({track.task_count} tasks)",
            "  dataset: shared generated benchmark manifest/zip",
        ]
    else:
        task_set = benchmark.official_task_set
        task_count = benchmark.default_task_count
        track_lines = [
            f"  {track.name}: {track.task_set} ({track.task_count} tasks)"
            for track in benchmark.tracks
        ]
    click.echo(f"Benchmark: {benchmark.name}")
    click.echo(f"Mode: {mode}")
    click.echo(f"Graph profile: {benchmark.graph_profile}")
    click.echo(f"Task set: {task_set}")
    click.echo(f"Task count: {task_count}")
    click.echo("Tracks:")
    for line in track_lines:
        click.echo(line)
    click.echo("Dataset: same generated manifest/zip can be used for direct and MCP tracks")
    click.echo(f"Scoring profile: {benchmark.scoring_profile}")
    click.echo(
        "Status: planned — use phase/run-config commands until benchmark launch plumbing lands."
    )


@main.command()
@click.argument("benchmark", required=False)
@click.option("--domain", default="CORP.LOCAL", help="AD domain name")
@click.option("--seed", type=int, default=42, help="Random seed for reproducibility")
@click.option("--users", type=int, default=20, help="Number of regular users to generate")
@click.option("--workstations", type=int, default=8, help="Number of workstations")
@click.option("--servers", type=int, default=4, help="Number of servers")
@click.option("--output", "-o", type=click.Path(), default="datasets/output", help="Output path")
@click.option("--zip/--no-zip", "as_zip", default=True, help="Output as zip (BH CE ingest format)")
def generate(
    benchmark: str | None,
    domain: str,
    seed: int,
    users: int,
    workstations: int,
    servers: int,
    output: str,
    as_zip: bool,
) -> None:
    """Generate a synthetic AD graph or named simple/complex benchmark."""
    if benchmark is not None:
        ctx = click.get_current_context()
        ctx.invoke(
            benchmark_generate,
            name=benchmark,
            seed=seed,
            output_dir=output,
            output_prefix=None,
        )
        return

    click.echo(f"Generating graph: domain={domain}, seed={seed}")

    graph = ADGraph(domain=domain, seed=seed)

    click.echo("  [1/4] Building org structure...")
    build_org(graph, num_users=users, num_workstations=workstations, num_servers=servers)
    click.echo(f"        {graph.node_count()} nodes created")

    click.echo("  [2/4] Applying baseline security...")
    apply_baseline_security(graph)
    click.echo(f"        {graph.edge_count()} edges total")

    click.echo("  [3/4] Planting attack paths...")
    planted = plant_all_paths(graph)
    click.echo(f"        {len(planted)} paths planted")

    click.echo("  [4/4] Serializing...")
    out_path = Path(output)
    if as_zip:
        zip_path = out_path.with_suffix(".zip") if out_path.suffix != ".zip" else out_path
        serialize_to_zip(graph, zip_path)
        click.echo(f"        Zip: {zip_path}")
        manifest_path = zip_path.parent / f"{zip_path.stem}_manifest.json"
    else:
        out_path.mkdir(parents=True, exist_ok=True)
        files = serialize_to_dir(graph, out_path)
        click.echo(f"        {len(files)} files written to {out_path}")
        manifest_path = out_path / "manifest.json"

    # Write manifest
    archive = zip_path.read_bytes() if as_zip else None
    manifest = _build_manifest(graph, seed, archive=archive)
    manifest_path.write_text(json.dumps(manifest, indent=2))
    click.echo(f"        Manifest: {manifest_path}")

    click.echo(
        f"\nDone. {graph.node_count()} nodes, {graph.edge_count()} edges, {len(planted)} planted paths."  # noqa: E501
    )
    for path in planted:
        click.echo(f"  [{path.tier}] {path.template_id}: {path.description[:80]}...")


@main.command(name="compile-v2")
@click.option(
    "--manifest",
    "manifest_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    help="Source ori-generated-manifest-v2 JSON.",
)
@click.option(
    "--archive",
    "archive_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    help="Exact generated SharpHound ZIP paired with the source manifest.",
)
@click.option(
    "--product",
    type=click.Choice(["simple", "complex"]),
    required=True,
)
@click.option(
    "--track",
    type=click.Choice(["direct", "mcp"]),
    required=True,
)
@click.option(
    "--output-dir",
    required=True,
    type=click.Path(file_okay=False),
    help="Directory for separated public/private v2 artifacts.",
)
def compile_v2_command(
    manifest_path: str,
    archive_path: str,
    product: str,
    track: str,
    output_dir: str,
) -> None:
    """Compile and offline-certify one explicit v2 product track."""
    from .eval.v2.cli_support import compile_v2_files
    from .eval.v2.schema import Track

    try:
        paths = compile_v2_files(
            source_manifest_path=Path(manifest_path),
            archive_path=Path(archive_path),
            product=product,
            track=Track(track),
            output_dir=Path(output_dir),
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo("V2 COMPILE: PASS")
    for label, path in paths.items():
        visibility = "private" if "private" in path.name else "public"
        click.echo(f"  {label} ({visibility}): {path}")


@main.command(name="run-v2")
@click.option(
    "--config",
    "config_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    help="Strict protocol-v2 campaign YAML.",
)
@click.option(
    "--execute",
    is_flag=True,
    default=False,
    help=(
        "Launch configured model calls after readiness passes. Without this "
        "flag, run only artifact, capability, health, and live-graph gates."
    ),
)
@click.option(
    "--verbose",
    "verbose_output",
    is_flag=True,
    default=False,
    help=(
        "Opt-in bounded operator view: show each public question, mechanical "
        "model/tool progress, terminal outcome, and a schema-only answer-shape "
        "placeholder."
    ),
)
def run_v2_command(config_path: str, execute: bool, verbose_output: bool) -> None:
    """Preflight or execute an explicit, candidate-certified V2 campaign."""
    import asyncio

    from .eval.v2.campaign_config import load_v2_campaign_config
    from .eval.v2.campaign_runner import run_v2_campaign

    try:
        readiness = asyncio.run(
            run_v2_campaign(
                Path(config_path),
                preflight_only=not execute,
                progress=click.echo,
                verbose=verbose_output,
            )
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    if execute:
        click.echo("V2 MODEL CAMPAIGN: COMPLETE")
    else:
        click.echo("V2 MODEL CAMPAIGN READINESS: PASS")
        click.echo("  No model calls were launched. Add --execute to run the campaign.")
    click.echo(f"  Graph: {readiness.graph_fingerprint}")
    click.echo(f"  Models: {readiness.model_count}")
    for model in readiness.models:
        click.echo(
            f"    {model.name}: {model.provider}/{model.model} "
            f"({model.credential_check}, {model.capability_check})"
        )
    for track in readiness.tracks:
        click.echo(
            f"  {track.track.value}: {track.task_count} candidate tasks "
            f"(release={track.candidate_release_fingerprint[:12]})"
        )
    resolved = load_v2_campaign_config(Path(config_path))
    click.echo(f"  Readiness: {resolved.output_dir / 'v2-run-readiness.private.json'}")


@main.command(name="campaign-status")
@click.option(
    "--config",
    "config_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    help="Exact protocol-v2 campaign YAML used to start the campaign.",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    default=False,
    help="Emit the redacted status projection as JSON.",
)
def campaign_status_command(config_path: str, json_output: bool) -> None:
    """Inspect a V2 campaign without contacting models or BloodHound."""
    from .eval.v2.campaign_status import inspect_v2_campaign_status

    try:
        status = inspect_v2_campaign_status(Path(config_path))
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    if json_output:
        click.echo(status.model_dump_json(indent=2))
        return

    click.echo(f"V2 CAMPAIGN: {status.observed_state.upper()}")
    click.echo(f"  Mode: {status.mode or 'not-started'}")
    click.echo(
        "  Progress: "
        f"{status.progress.terminal_results}/"
        f"{status.progress.expected_results or 'unknown'} terminal results; "
        f"{status.progress.checkpointed_results} checkpointed; "
        f"{status.progress.pending_infra_retries} pending infrastructure retries; "
        f"{status.progress.runs_reported}/{status.progress.expected_runs} runs reported"
    )
    click.echo(
        "  Usage: "
        f"{status.progress.total_tokens} total tokens "
        f"({status.progress.tokens_input} input + "
        f"{status.progress.tokens_output} output) across "
        f"{status.progress.provider_attempts} provider attempts"
    )
    if status.active_run is not None:
        click.echo(
            "  Active: "
            f"{status.active_run.track.value}/{status.active_run.model}/"
            f"run-{status.active_run.run_index:03d}"
        )
        click.echo(
            f"  Phase: {status.active_phase.replace('_', ' ')}"
            + (f" (recovery round {status.recovery_round})" if status.recovery_round else "")
        )
    for track in status.tracks:
        validity = (
            "valid"
            if track.campaign_valid is True
            else "invalid"
            if track.campaign_valid is False
            else "pending"
        )
        click.echo(
            f"  {track.track.value}: {track.terminal_results} terminal, "
            f"{track.checkpointed_results} checkpointed, "
            f"{track.pending_infra_retries} pending infrastructure retries, "
            f"{track.completed_results} completed ({validity})"
        )
    click.echo(f"  Next action: {status.next_action.replace('_', ' ')}")
    click.echo(f"  Resume allowed: {'yes' if status.resume_allowed else 'no'}")


@main.command(name="certify-v2-live")
@click.option(
    "--manifest",
    "manifest_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
)
@click.option(
    "--archive",
    "archive_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
)
@click.option(
    "--product",
    type=click.Choice(["simple", "complex"]),
    required=True,
)
@click.option(
    "--output-dir",
    required=True,
    type=click.Path(file_okay=False),
)
@click.option("--bhce-url", default=None, help="Override BH CE base URL.")
@click.option("--page-size", type=click.IntRange(min=1, max=2000), default=1000)
def certify_v2_live_command(
    manifest_path: str,
    archive_path: str,
    product: str,
    output_dir: str,
    bhce_url: str | None,
    page_size: int,
) -> None:
    """Certify both v2 tracks against a controlled BloodHound graph."""
    import asyncio

    from .eval.v2.cli_support import certify_v2_live_files

    try:
        paths = asyncio.run(
            certify_v2_live_files(
                source_manifest_path=Path(manifest_path),
                archive_path=Path(archive_path),
                product=product,
                output_dir=Path(output_dir),
                bhce_url=bhce_url,
                page_size=page_size,
            )
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo("V2 LIVE CERTIFICATION: PASS")
    for label, path in paths.items():
        click.echo(f"  {label}: {path}")


@main.command(name="preflight-tasks")
@click.option(
    "--manifest",
    "manifest_path",
    required=True,
    type=click.Path(exists=True),
    help="Generated task manifest JSON",
)
@click.option(
    "--track",
    type=click.Choice(["direct", "mcp", "cypher"]),
    default="mcp",
    show_default=True,
    help="Benchmark track; cypher is retained as a legacy alias for direct.",
)
@click.option("--valid-nodes", "valid_nodes_path", type=click.Path(exists=True), default=None)
@click.option(
    "--output", "output_path", type=click.Path(), default=None, help="Optional JSON report path"
)
def preflight_tasks_command(
    manifest_path: str, track: str, valid_nodes_path: str | None, output_path: str | None
) -> None:
    """Run task/scorer consistency preflight checks before a campaign."""
    from .eval.preflight import preflight_manifest

    report = preflight_manifest(
        Path(manifest_path),
        track=track,
        valid_nodes_path=Path(valid_nodes_path) if valid_nodes_path else None,
    )
    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    summary = report["summary"]
    click.echo(
        f"Preflight checked {summary['tasks_checked']} tasks: "
        f"{summary['errors']} errors, {summary['warnings']} warnings"
    )
    if output_path:
        click.echo(f"Report written to {output_path}")
    if not report["ok"]:
        raise SystemExit(1)


@main.command(name="score-answers")
@click.option(
    "--manifest",
    "manifest_path",
    required=True,
    type=click.Path(exists=True),
    help="Generated task manifest JSON",
)
@click.option(
    "--answers",
    "answers_path",
    required=True,
    type=click.Path(exists=True),
    help="Structured answer JSON to grade",
)
@click.option(
    "--track",
    type=click.Choice(["direct", "mcp", "cypher"]),
    default="mcp",
    show_default=True,
    help="Benchmark track; cypher is retained as a legacy alias for direct.",
)
@click.option(
    "--output", "output_path", required=True, type=click.Path(), help="Projection JSON output path"
)
@click.option(
    "--protocol",
    type=click.Choice(["v1", "v2"]),
    default="v1",
    show_default=True,
    help="Scoring protocol. V2 requires a separate --oracles artifact.",
)
@click.option(
    "--oracles",
    "oracle_path",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Sealed scorer-only oracle artifact required by protocol v2.",
)
def score_answers(
    manifest_path: str,
    answers_path: str,
    track: str,
    output_path: str,
    protocol: str,
    oracle_path: str | None,
) -> None:
    """Grade a structured answers file without launching a model campaign."""
    from .eval.answer_scoring import (
        score_official_answers_projection,
        write_score_answers_projection,
    )

    try:
        if protocol == "v2":
            if oracle_path is None:
                raise ValueError("protocol v2 requires --oracles")
            from .eval.v2.cli_support import score_v2_files
            from .eval.v2.schema import Track

            expected_track = "direct" if track == "cypher" else track
            scoring = score_v2_files(
                public_path=Path(manifest_path),
                oracle_path=Path(oracle_path),
                answers_path=Path(answers_path),
                output_path=Path(output_path),
                expected_track=Track(expected_track),
            )
            summary = scoring.summary
            click.echo(
                f"Scored {summary.completed}/{summary.scheduled} v2 samples; "
                f"reasoning_accuracy={summary.reasoning_accuracy or 0.0:.3f}; "
                f"effective_accuracy={summary.effective_accuracy or 0.0:.3f}; "
                f"campaign_valid={str(summary.campaign_valid).lower()}"
            )
            click.echo(f"Projection written to {output_path}")
            return
        manifest = json.loads(Path(manifest_path).read_text())
        if (
            manifest.get("schema_version") == "ori-generated-manifest-v3"
            or manifest.get("protocol_version") == "ori-eval-protocol-v2"
        ):
            raise ValueError("v2 artifacts require explicit --protocol v2 and --oracles")
        if manifest.get("schema_version") == "phase4b_v2.0" and "tasks_official" in manifest:
            projection = score_official_answers_projection(
                manifest_path=Path(manifest_path),
                answers_path=Path(answers_path),
            )
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_text(json.dumps(projection, indent=2, sort_keys=True) + "\n")
        else:
            projection = write_score_answers_projection(
                manifest_path=Path(manifest_path),
                answers_path=Path(answers_path),
                track="cypher" if track == "direct" else track,
                output_path=Path(output_path),
            )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    summary = projection["summary"]
    if "raw_score" in summary:
        click.echo(
            f"Scored {summary['raw_score']}/{summary['official_count']} official answers; "
            f"official_accuracy={summary['official_accuracy']:.3f}"
        )
    else:
        click.echo(
            f"Scored {summary['completed_samples']}/{summary['total_samples']} samples; "
            f"reasoning_accuracy={summary['reasoning_accuracy']:.3f}; "
            f"effective_accuracy={summary['effective_accuracy']:.3f}"
        )
    click.echo(f"Projection written to {output_path}")


@main.command()
@click.option(
    "--manifest", "-m", required=True, type=click.Path(exists=True), help="Path to manifest.json"
)
@click.option(
    "--model",
    required=True,
    help="Model string, e.g. anthropic/claude-sonnet-4-5 or ollama/llama3.1:8b",
)
@click.option("--output", "-o", required=True, type=click.Path(), help="Output CSV path")
@click.option("--concurrency", default=3, show_default=True, help="Max concurrent model calls")
@click.option(
    "--bhce-url", default=None, help="BH CE base URL (overrides BLOODHOUND_DOMAIN env var)"
)
@click.option(
    "--ollama-option",
    "ollama_options_raw",
    multiple=True,
    help="Repeatable Ollama option in KEY=VALUE form, e.g. --ollama-option num_ctx=16384",
)
@click.option(
    "--telemetry/--no-telemetry",
    "telemetry_enabled",
    default=True,
    show_default=True,
    help="Write portable run/model/system telemetry artifacts.",
)
def eval(
    manifest: str,
    model: str,
    output: str,
    concurrency: int,
    bhce_url: str | None,
    ollama_options_raw: tuple[str, ...],
    telemetry_enabled: bool,
) -> None:
    """Run evaluation: generate tasks from manifest, run model, grade results."""
    import asyncio

    from .eval.runner import run_eval_cli

    run_spec = _build_inline_run_spec(
        model=model, ollama_options=_parse_ollama_options(ollama_options_raw)
    )
    direct_query_safety = DirectQuerySafetyConfig()
    asyncio.run(
        run_eval_cli(
            manifest_path=Path(manifest),
            model=model,
            output_path=Path(output),
            concurrency=concurrency,
            bhce_url=bhce_url,
            ollama_options=run_spec.ollama_options,
            run_name=run_spec.run_name,
            run_config=_effective_run_config(
                run_spec,
                telemetry_enabled=telemetry_enabled,
                direct_query_safety=direct_query_safety,
            ),
            telemetry_enabled=telemetry_enabled,
            direct_query_safety=direct_query_safety,
        )
    )


@main.command(name="eval-mcp")
@click.option(
    "--manifest", "-m", required=True, type=click.Path(exists=True), help="Path to manifest.json"
)
@click.option("--model", required=True, help="Model string, e.g. ollama/qwen3:latest")
@click.option("--output", "-o", required=True, type=click.Path(), help="Output CSV path")
@click.option("--concurrency", default=1, show_default=True, help="Max concurrent model calls")
@click.option(
    "--bhce-url", default=None, help="BH CE base URL (overrides BLOODHOUND_DOMAIN env var)"
)
@click.option(
    "--mcp-dir",
    default="../bloodhound-mcp",
    type=click.Path(exists=True),
    show_default=True,
    help="Path to local bloodhound-mcp repo",
)
@click.option("--max-steps", default=12, show_default=True, help="Max agent/tool steps")
@click.option(
    "--resource-mode",
    type=click.Choice(["off", "on-demand"]),
    default="off",
    show_default=True,
    help="Whether MCP reference resources are available to the model.",
)
@click.option(
    "--mcp-tool-loop",
    type=click.Choice(["auto", "inspect", "native-ollama", "native-openai-compatible"]),
    default="auto",
    show_default=True,
    help="MCP tool-calling loop implementation.",
)
@click.option(
    "--openai-compat-telemetry-adapter",
    type=click.Choice(["auto", "generic", "llama-cpp", "mlx-lm", "vllm", "lm-studio"]),
    default="auto",
    show_default=True,
    help="Telemetry parser for native OpenAI-compatible MCP runs.",
)
@click.option(
    "--mcp-ollama-read-timeout",
    default=900.0,
    show_default=True,
    type=float,
    help="Read timeout in seconds for native Ollama MCP chat streams.",
)
@click.option(
    "--ollama-option",
    "ollama_options_raw",
    multiple=True,
    help="Repeatable Ollama option in KEY=VALUE form, e.g. --ollama-option num_ctx=16384",
)
@click.option(
    "--telemetry/--no-telemetry",
    "telemetry_enabled",
    default=True,
    show_default=True,
    help="Write portable run/model/system telemetry artifacts.",
)
def eval_mcp(
    manifest: str,
    model: str,
    output: str,
    concurrency: int,
    bhce_url: str | None,
    mcp_dir: str,
    max_steps: int,
    resource_mode: str,
    mcp_tool_loop: str,
    openai_compat_telemetry_adapter: str,
    mcp_ollama_read_timeout: float,
    ollama_options_raw: tuple[str, ...],
    telemetry_enabled: bool,
) -> None:
    """Run MCP-mode evaluation using BloodHound MCP tools."""
    import asyncio

    from .eval.runner import run_eval_mcp_cli

    run_spec = _build_inline_run_spec(
        model=model, ollama_options=_parse_ollama_options(ollama_options_raw)
    )
    mcp_launcher = MCPLauncherConfig.local_checkout(Path(mcp_dir))

    asyncio.run(
        run_eval_mcp_cli(
            manifest_path=Path(manifest),
            model=model,
            output_path=Path(output),
            concurrency=concurrency,
            bhce_url=bhce_url,
            mcp_dir=mcp_launcher.mcp_dir,
            mcp_launcher=mcp_launcher,
            max_steps=max_steps,
            resource_mode=resource_mode,
            mcp_tool_loop=mcp_tool_loop,
            openai_compat_telemetry_adapter=openai_compat_telemetry_adapter,
            mcp_ollama_read_timeout_seconds=mcp_ollama_read_timeout,
            ollama_options=run_spec.ollama_options,
            run_name=run_spec.run_name,
            run_config=_effective_run_config(
                run_spec,
                max_steps=max_steps,
                resource_mode=resource_mode,
                mcp_tool_loop=mcp_tool_loop,
                openai_compat_telemetry_adapter=openai_compat_telemetry_adapter,
                mcp_ollama_read_timeout_seconds=mcp_ollama_read_timeout,
                telemetry_enabled=telemetry_enabled,
                mcp_launcher=mcp_launcher,
            ),
            telemetry_enabled=telemetry_enabled,
        )
    )


@main.command()
@click.option(
    "--bhce-url", default=None, help="BH CE base URL (overrides BLOODHOUND_DOMAIN env var)"
)
@click.option(
    "--timeout",
    default=60.0,
    show_default=True,
    type=float,
    help="Seconds to wait for BHCE to become healthy",
)
@click.option(
    "--poll-interval",
    default=5.0,
    show_default=True,
    type=float,
    help="Seconds between health probes",
)
def verify_bh_health(bhce_url: str | None, timeout: float, poll_interval: float) -> None:
    """Verify that BloodHound CE is healthy before starting a run."""
    import asyncio

    from .eval.ops import print_verify_bh_health, verify_bh_health

    result = asyncio.run(
        verify_bh_health(
            bhce_url=bhce_url,
            timeout_seconds=timeout,
            poll_interval=poll_interval,
        )
    )
    print_verify_bh_health(result)
    if not result.ok:
        raise SystemExit(1)


@main.command(name="verify-mcp")
@click.option(
    "--config",
    "config_path",
    required=True,
    type=click.Path(exists=True),
    help="Run config containing the MCP launcher definition.",
)
@click.option("--profile", default=None, help="Profile name for a profile-based config.")
@click.option(
    "--manifest", "-m", required=True, type=click.Path(exists=True), help="Benchmark manifest."
)
@click.option(
    "--output",
    "-o",
    required=True,
    type=click.Path(),
    help="New immutable MCP readiness JSON path.",
)
@click.option(
    "--mcp-dir",
    default=None,
    type=click.Path(exists=True),
    help="Explicit legacy local checkout override.",
)
def verify_mcp(
    config_path: str,
    profile: str | None,
    manifest: str,
    output: str,
    mcp_dir: str | None,
) -> None:
    """Start MCP without a model and verify read-only launcher capabilities."""
    import asyncio

    from .eval.mcp_runtime import (
        verify_mcp_launcher_readiness,
        write_mcp_readiness_artifact,
    )

    config_file = Path(config_path).resolve()
    try:
        data = yaml.safe_load(config_file.read_text()) or {}
        if not isinstance(data, dict):
            raise ValueError("Run config root must be a mapping.")
        if "profiles" in data:
            resolved = load_run_profile(
                config_file,
                profile_name=profile,
                overrides=RunConfigOverrides(mcp_dir=mcp_dir),
            )
            launcher = resolved.mcp_launcher
        else:
            defaults = data.get("defaults") or {}
            if not isinstance(defaults, dict):
                raise ValueError("defaults must be a mapping when present.")
            mcp_section = defaults.get("mcp") or {}
            if not isinstance(mcp_section, dict):
                raise ValueError("defaults.mcp must be a mapping when present.")
            launcher = resolve_mcp_launcher_config(
                mcp_section,
                config_dir=config_file.parent,
                mcp_dir_override=mcp_dir,
            )
        manifest_data = json.loads(Path(manifest).read_text())
        domain_query = manifest_data.get("domain")
        if not isinstance(domain_query, str) or not domain_query.strip():
            raise ValueError("Manifest must define a non-empty domain for MCP readiness.")
        result = asyncio.run(verify_mcp_launcher_readiness(launcher, domain_query=domain_query))
        artifact = write_mcp_readiness_artifact(result, Path(output))
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        raise click.ClickException(str(exc)) from exc
    except Exception as exc:
        raise click.ClickException(
            f"MCP readiness startup failed ({exc.__class__.__name__})."
        ) from None

    provenance = artifact["provenance"]
    click.echo(f"MCP launcher: {provenance['mcp_launcher']}")
    click.echo(f"MCP revision: {provenance.get('mcp_revision') or 'local checkout'}")
    click.echo(f"Prompt discovery: {artifact['prompt_discovery']['status']}")
    click.echo(f"Resource discovery: {artifact['resource_discovery']['status']}")
    click.echo(f"Read-only tools: {len(artifact['read_only_tools'])}")
    click.echo(f"Readiness fingerprint: {artifact['readiness_fingerprint']}")
    click.echo(f"Readiness artifact: {Path(output).resolve()}")
    click.echo(f"MCP READINESS: {'PASS' if result.ok else 'FAIL'}")
    if not result.ok:
        raise SystemExit(1)


@main.command()
@click.option(
    "--manifest", "-m", required=True, type=click.Path(exists=True), help="Path to manifest.json"
)
@click.option(
    "--bhce-url", default=None, help="BH CE base URL (overrides BLOODHOUND_DOMAIN env var)"
)
def verify_ingest(manifest: str, bhce_url: str | None) -> None:
    """Verify that uploaded BloodHound data matches the generated manifest."""
    import asyncio

    from .eval.ops import print_verify_ingest, verify_ingest

    result = asyncio.run(verify_ingest(Path(manifest), bhce_url=bhce_url))
    print_verify_ingest(result)
    if not result.ok:
        raise SystemExit(1)


@main.command()
@click.option(
    "--manifest", "-m", required=True, type=click.Path(exists=True), help="Path to manifest.json"
)
@click.option(
    "--output-dir", "-o", required=True, type=click.Path(), help="Directory for smoke-test CSVs"
)
@click.option(
    "--bhce-url", default=None, help="BH CE base URL (overrides BLOODHOUND_DOMAIN env var)"
)
def smoke_eval(manifest: str, output_dir: str, bhce_url: str | None) -> None:
    """Run mock-model smoke tests for grading, parsing, and Cypher execution paths."""
    import asyncio

    from .eval.ops import print_smoke_eval, run_smoke_eval

    result = asyncio.run(
        run_smoke_eval(
            manifest_path=Path(manifest),
            output_dir=Path(output_dir),
            bhce_url=bhce_url,
        )
    )
    print_smoke_eval(result)
    if not result.ok:
        raise SystemExit(1)


@main.command(name="smoke-mcp")
@click.option(
    "--manifest", "-m", required=True, type=click.Path(exists=True), help="Path to manifest.json"
)
@click.option(
    "--output-dir", "-o", required=True, type=click.Path(), help="Directory for MCP smoke-test CSVs"
)
@click.option(
    "--bhce-url", default=None, help="BH CE base URL (overrides BLOODHOUND_DOMAIN env var)"
)
@click.option(
    "--mcp-dir",
    default="../bloodhound-mcp",
    type=click.Path(),
    show_default=True,
    help="Path to local bloodhound-mcp repo",
)
@click.option("--max-steps", default=12, show_default=True, help="Max agent/tool steps")
@click.option(
    "--resource-mode",
    type=click.Choice(["off", "on-demand"]),
    default="off",
    show_default=True,
    help="Whether MCP reference resources are available to the model.",
)
def smoke_mcp(
    manifest: str,
    output_dir: str,
    bhce_url: str | None,
    mcp_dir: str,
    max_steps: int,
    resource_mode: str,
) -> None:
    """Run mock-model MCP smoke tests for structured answers and reporting."""
    import asyncio

    from .eval.ops import print_smoke_eval, run_smoke_mcp_eval

    mcp_launcher = MCPLauncherConfig.local_checkout(Path(mcp_dir))

    result = asyncio.run(
        run_smoke_mcp_eval(
            manifest_path=Path(manifest),
            output_dir=Path(output_dir),
            bhce_url=bhce_url,
            mcp_dir=mcp_launcher.mcp_dir,
            mcp_launcher=mcp_launcher,
            max_steps=max_steps,
            resource_mode=resource_mode,
        )
    )
    print_smoke_eval(result)
    if not result.ok:
        raise SystemExit(1)


@main.command(name="run")
@click.option(
    "--config",
    "config_path",
    required=True,
    type=click.Path(exists=True),
    help="Path to versioned ORI run config YAML",
)
@click.option("--profile", default=None, help="Profile name inside the config file")
@click.option(
    "--run-all-profiles",
    is_flag=True,
    default=False,
    help="Run all enabled profiles in config order",
)
@click.option(
    "--keep-going",
    is_flag=True,
    default=False,
    help="With --run-all-profiles, continue after profile failures and report them at the end",
)
@click.option("--manifest", type=click.Path(), default=None, help="Override manifest path")
@click.option(
    "--output", type=click.Path(), default=None, help="Override single-run output CSV path"
)
@click.option(
    "--output-dir", type=click.Path(), default=None, help="Override multi-run output directory"
)
@click.option("--seed", type=int, default=None, help="Override generate-profile seed")
@click.option("--domain", default=None, help="Override generate-profile AD domain")
@click.option(
    "--output-zip",
    type=click.Path(),
    default=None,
    help="Override generated dataset zip path",
)
@click.option(
    "--output-manifest",
    type=click.Path(),
    default=None,
    help="Override generated manifest path",
)
@click.option("--bhce-url", default=None, help="Override BH CE base URL")
@click.option("--concurrency", type=int, default=None, help="Override run concurrency")
@click.option(
    "--mcp-dir", type=click.Path(), default=None, help="Override local bloodhound-mcp path"
)
@click.option("--max-steps", type=int, default=None, help="Override MCP max tool/agent steps")
@click.option(
    "--resource-mode",
    type=click.Choice(["off", "on-demand"]),
    default=None,
    help="Override MCP resource mode for config-driven runs.",
)
@click.option(
    "--mcp-tool-loop",
    type=click.Choice(["auto", "inspect", "native-ollama", "native-openai-compatible"]),
    default=None,
    help="Override MCP tool-calling loop for config-driven runs.",
)
@click.option(
    "--openai-compat-telemetry-adapter",
    type=click.Choice(["auto", "generic", "llama-cpp", "mlx-lm", "vllm", "lm-studio"]),
    default=None,
    help="Override telemetry parser for native OpenAI-compatible MCP runs.",
)
@click.option(
    "--mcp-ollama-read-timeout",
    type=float,
    default=None,
    help="Override native Ollama MCP stream read timeout in seconds.",
)
@click.option("--model-base-url", default=None, help="Override model provider base URL")
@click.option(
    "--max-model-reruns-on-infra",
    type=int,
    default=None,
    help="Override full-run retries after INFRA_ERROR",
)
@click.option(
    "--health-timeout",
    type=float,
    default=None,
    help="Override BloodHound health timeout in seconds",
)
@click.option(
    "--health-poll-interval",
    type=float,
    default=None,
    help="Override BloodHound health poll interval in seconds",
)
@click.option(
    "--telemetry/--no-telemetry",
    "telemetry_enabled",
    default=None,
    help="Override telemetry artifact collection for real eval/baseline profiles.",
)
def run_from_config(
    config_path: str,
    profile: str | None,
    run_all_profiles: bool,
    keep_going: bool,
    manifest: str | None,
    output: str | None,
    output_dir: str | None,
    seed: int | None,
    domain: str | None,
    output_zip: str | None,
    output_manifest: str | None,
    bhce_url: str | None,
    concurrency: int | None,
    mcp_dir: str | None,
    max_steps: int | None,
    resource_mode: str | None,
    mcp_tool_loop: str | None,
    openai_compat_telemetry_adapter: str | None,
    mcp_ollama_read_timeout: float | None,
    model_base_url: str | None,
    max_model_reruns_on_infra: int | None,
    health_timeout: float | None,
    health_poll_interval: float | None,
    telemetry_enabled: bool | None,
) -> None:
    """Run ORI from a single versioned config file."""
    import asyncio

    from .eval.ops import (
        print_preflight,
        print_smoke_eval,
        run_preflight,
        run_smoke_eval,
        run_smoke_mcp_eval,
    )
    from .eval.report import print_comparison, write_combined_csv, write_summary_csv
    from .eval.runner import run_eval_cli, run_eval_mcp_cli

    if profile is not None and run_all_profiles:
        raise click.UsageError("--profile and --run-all-profiles are mutually exclusive.")
    if keep_going and not run_all_profiles:
        raise click.UsageError("--keep-going requires --run-all-profiles.")

    overrides = RunConfigOverrides(
        manifest=manifest,
        output=output,
        output_dir=output_dir,
        seed=seed,
        domain=domain,
        output_zip=output_zip,
        output_manifest=output_manifest,
        bhce_url=bhce_url,
        concurrency=concurrency,
        mcp_dir=mcp_dir,
        max_steps=max_steps,
        resource_mode=resource_mode,
        mcp_tool_loop=mcp_tool_loop,
        openai_compat_telemetry_adapter=openai_compat_telemetry_adapter,
        mcp_ollama_read_timeout_seconds=mcp_ollama_read_timeout,
        model_base_url=model_base_url,
        max_model_reruns_on_infra=max_model_reruns_on_infra,
        health_timeout_seconds=health_timeout,
        health_poll_interval=health_poll_interval,
        telemetry_enabled=telemetry_enabled,
    )

    def _run_model_matrix_config(config_file: Path) -> bool:
        data = yaml.safe_load(config_file.read_text()) or {}
        if not isinstance(data, dict) or "profiles" in data:
            return False
        version = data.get("version", 1)
        if version != 1:
            raise click.UsageError(
                f"Unsupported model-matrix config version {version!r}; expected 1."
            )
        model_entries = data.get("models")
        if not isinstance(model_entries, list) or not model_entries:
            return False
        _validate_model_matrix_config(data)
        if profile is not None or run_all_profiles:
            raise click.UsageError("--profile/--run-all-profiles require a profile-based config.")

        defaults = data.get("defaults") or {}
        config_dir = config_file.parent
        raw_manifest = manifest if manifest is not None else data.get("manifest")
        if raw_manifest is None:
            raise click.UsageError("Model-list configs require top-level `manifest` or --manifest.")
        if not isinstance(raw_manifest, str) or not raw_manifest.strip():
            raise click.UsageError("Model-list config manifest must be a non-empty path.")
        manifest_path = Path(raw_manifest)
        if not manifest_path.is_absolute():
            manifest_path = (
                manifest_path.resolve()
                if manifest is not None
                else (config_dir / manifest_path).resolve()
            )

        default_concurrency = concurrency or defaults.get("concurrency", 1)
        default_runs_per_model = defaults.get("runs_per_model", 1)
        run_specs = _dedupe_run_specs(
            [
                _run_spec_from_entry(
                    entry,
                    default_concurrency=default_concurrency,
                    default_runs_per_model=default_runs_per_model,
                )
                for entry in model_entries
            ]
        )
        modes = data.get("modes", ["direct", "mcp"])
        if not isinstance(modes, list) or not modes:
            raise click.UsageError("modes must be a non-empty list when present.")
        unsupported = sorted(set(modes) - {"direct", "mcp"})
        if unsupported:
            raise click.UsageError(f"Unsupported run mode(s): {', '.join(unsupported)}")

        root_output_dir = Path(
            output_dir or data.get("output_dir") or f"results/benchmark-runs/{manifest_path.stem}"
        )
        if not root_output_dir.is_absolute():
            root_output_dir = (config_dir / root_output_dir).resolve()

        mcp_defaults = defaults.get("mcp") or {}
        if not isinstance(mcp_defaults, dict):
            raise click.UsageError("defaults.mcp must be a mapping when present.")
        health_defaults = defaults.get("health") or {}
        telemetry_defaults = defaults.get("telemetry") or {}
        direct_safety_defaults = defaults.get("direct_query_safety") or {}
        if not isinstance(direct_safety_defaults, dict):
            raise click.UsageError("defaults.direct_query_safety must be a mapping when present.")
        try:
            effective_direct_safety = DirectQuerySafetyConfig.from_mapping(direct_safety_defaults)
        except (TypeError, ValueError) as exc:
            raise click.UsageError(str(exc)) from exc
        effective_bhce_url = bhce_url if bhce_url is not None else defaults.get("bhce_url")
        effective_model_base_url = (
            model_base_url if model_base_url is not None else defaults.get("model_base_url")
        )
        effective_max_reruns = int(
            max_model_reruns_on_infra
            if max_model_reruns_on_infra is not None
            else defaults.get("max_model_reruns_on_infra", 1)
        )
        effective_health_timeout = float(
            health_timeout
            if health_timeout is not None
            else health_defaults.get("timeout_seconds", 60)
        )
        effective_health_poll = float(
            health_poll_interval
            if health_poll_interval is not None
            else health_defaults.get("poll_interval", 5)
        )
        effective_telemetry = (
            telemetry_enabled
            if telemetry_enabled is not None
            else bool(telemetry_defaults.get("enabled", True))
        )
        effective_mcp_launcher: MCPLauncherConfig | None = None
        if "mcp" in modes:
            try:
                effective_mcp_launcher = resolve_mcp_launcher_config(
                    mcp_defaults,
                    config_dir=config_dir,
                    mcp_dir_override=mcp_dir,
                )
            except ValueError as exc:
                raise click.UsageError(str(exc)) from exc
        effective_max_steps = int(
            max_steps if max_steps is not None else mcp_defaults.get("max_steps", 12)
        )
        effective_resource_mode = resource_mode or mcp_defaults.get("resource_mode", "off")
        if effective_resource_mode is False:
            effective_resource_mode = "off"
        if effective_resource_mode not in {"off", "on-demand"}:
            raise click.UsageError(
                "Unsupported MCP resource_mode. Supported values: off, on-demand"
            )
        effective_mcp_tool_loop = mcp_tool_loop or mcp_defaults.get("tool_loop", "auto")
        supported_tool_loops = {
            "auto",
            "inspect",
            "native-ollama",
            "native-openai-compatible",
        }
        if effective_mcp_tool_loop not in supported_tool_loops:
            raise click.UsageError(
                "Unsupported MCP tool_loop. Supported values: "
                + ", ".join(sorted(supported_tool_loops))
            )
        effective_telemetry_adapter = openai_compat_telemetry_adapter or mcp_defaults.get(
            "openai_compat_telemetry_adapter", "auto"
        )
        supported_telemetry_adapters = {
            "auto",
            "generic",
            "llama-cpp",
            "mlx-lm",
            "vllm",
            "lm-studio",
        }
        if effective_telemetry_adapter not in supported_telemetry_adapters:
            raise click.UsageError(
                "Unsupported MCP openai_compat_telemetry_adapter. Supported values: "
                + ", ".join(sorted(supported_telemetry_adapters))
            )
        effective_ollama_read_timeout = float(
            mcp_ollama_read_timeout
            if mcp_ollama_read_timeout is not None
            else mcp_defaults.get("ollama_read_timeout_seconds", 900)
        )

        resolved_config = deepcopy(data)
        resolved_config.setdefault("version", 1)
        resolved_config["manifest"] = str(manifest_path)
        resolved_config["modes"] = modes
        resolved_config["output_dir"] = str(root_output_dir)
        resolved_defaults = resolved_config.setdefault("defaults", {})
        resolved_mcp = (
            effective_mcp_launcher.to_config()
            if effective_mcp_launcher is not None
            else dict(mcp_defaults)
        )
        resolved_mcp.update(
            {
                "max_steps": effective_max_steps,
                "resource_mode": effective_resource_mode,
                "tool_loop": effective_mcp_tool_loop,
                "openai_compat_telemetry_adapter": effective_telemetry_adapter,
                "ollama_read_timeout_seconds": effective_ollama_read_timeout,
            }
        )
        resolved_defaults.update(
            {
                "concurrency": default_concurrency,
                "runs_per_model": default_runs_per_model,
                "bhce_url": effective_bhce_url,
                "model_base_url": effective_model_base_url,
                "max_model_reruns_on_infra": effective_max_reruns,
                "health": {
                    "timeout_seconds": effective_health_timeout,
                    "poll_interval": effective_health_poll,
                },
                "direct_query_safety": effective_direct_safety.to_jsonable(),
                "mcp": resolved_mcp,
                "telemetry": {"enabled": effective_telemetry},
            }
        )
        for entry, run_spec in zip(
            resolved_config["models"],
            run_specs,
            strict=True,
        ):
            if not isinstance(entry, dict):
                continue
            # CLI overrides become part of the runnable snapshot, but the active
            # run specs were derived from the source entries before those
            # overrides were applied. Preserve that original run identity so a
            # resume from campaign-config.yaml targets the same artifacts.
            entry.setdefault("name", run_spec.run_name)
            per_model_cli_overrides = {
                "concurrency": concurrency,
                "model_base_url": model_base_url,
                "max_steps": max_steps,
                "mcp_tool_loop": mcp_tool_loop,
                "openai_compat_telemetry_adapter": openai_compat_telemetry_adapter,
                "mcp_ollama_read_timeout_seconds": mcp_ollama_read_timeout,
            }
            entry.update(
                {key: value for key, value in per_model_cli_overrides.items() if value is not None}
            )

        cli_override_values = {
            "manifest": manifest,
            "output_dir": output_dir,
            "bhce_url": bhce_url,
            "concurrency": concurrency,
            "mcp_dir": mcp_dir,
            "max_steps": max_steps,
            "resource_mode": resource_mode,
            "mcp_tool_loop": mcp_tool_loop,
            "openai_compat_telemetry_adapter": openai_compat_telemetry_adapter,
            "mcp_ollama_read_timeout_seconds": mcp_ollama_read_timeout,
            "model_base_url": model_base_url,
            "max_model_reruns_on_infra": max_model_reruns_on_infra,
            "health_timeout_seconds": health_timeout,
            "health_poll_interval": health_poll_interval,
            "telemetry_enabled": telemetry_enabled,
        }
        _write_campaign_config_snapshot(
            config_file=config_file,
            resolved_config=resolved_config,
            output_dir=root_output_dir,
            manifest_path=manifest_path,
            modes=modes,
            cli_overrides={
                key: value for key, value in cli_override_values.items() if value is not None
            },
        )

        if "direct" in modes:
            direct_dir = root_output_dir / "direct"
            direct_dir.mkdir(parents=True, exist_ok=True)
            all_results = asyncio.run(
                _run_baseline_with_specs(
                    manifest_path=manifest_path,
                    run_specs=run_specs,
                    output_dir=direct_dir,
                    concurrency_override=concurrency,
                    bhce_url=effective_bhce_url,
                    default_model_base_url=effective_model_base_url,
                    model_base_url_override=model_base_url,
                    max_model_reruns_on_infra=effective_max_reruns,
                    health_timeout_seconds=effective_health_timeout,
                    health_poll_interval=effective_health_poll,
                    telemetry_enabled=effective_telemetry,
                    direct_query_safety=effective_direct_safety,
                )
            )
            write_combined_csv(all_results, direct_dir / "baseline_combined.csv")
            write_summary_csv(all_results, direct_dir / "baseline_summary.csv")
            click.echo(f"Direct benchmark results written to {direct_dir}")

        if "mcp" in modes:
            mcp_dir_out = root_output_dir / "mcp"
            mcp_dir_out.mkdir(parents=True, exist_ok=True)
            all_results = asyncio.run(
                _run_baseline_mcp_with_specs(
                    manifest_path=manifest_path,
                    run_specs=run_specs,
                    output_dir=mcp_dir_out,
                    concurrency_override=concurrency,
                    bhce_url=effective_bhce_url,
                    mcp_dir=effective_mcp_launcher.mcp_dir,
                    mcp_launcher=effective_mcp_launcher,
                    max_steps=effective_max_steps,
                    resource_mode=effective_resource_mode,
                    mcp_tool_loop=effective_mcp_tool_loop,
                    openai_compat_telemetry_adapter=effective_telemetry_adapter,
                    mcp_ollama_read_timeout_seconds=effective_ollama_read_timeout,
                    default_model_base_url=effective_model_base_url,
                    max_steps_override=max_steps,
                    model_base_url_override=model_base_url,
                    mcp_tool_loop_override=mcp_tool_loop,
                    openai_compat_telemetry_adapter_override=(openai_compat_telemetry_adapter),
                    mcp_ollama_read_timeout_seconds_override=mcp_ollama_read_timeout,
                    max_model_reruns_on_infra=effective_max_reruns,
                    health_timeout_seconds=effective_health_timeout,
                    health_poll_interval=effective_health_poll,
                    telemetry_enabled=effective_telemetry,
                )
            )
            write_combined_csv(all_results, mcp_dir_out / "baseline_combined.csv")
            write_summary_csv(all_results, mcp_dir_out / "baseline_summary.csv")
            click.echo(f"MCP benchmark results written to {mcp_dir_out}")
        return True

    def _run_one_profile(resolved) -> None:
        click.echo(f"Using config profile: {resolved.profile_name} ({resolved.kind})")

        if resolved.kind == "generate":
            _generate_profile_dataset(resolved)
            return

        if resolved.kind == "eval":
            run_spec = _dedupe_run_specs(
                [
                    _run_spec_from_entry(
                        resolved.model_entry,
                        default_concurrency=resolved.concurrency or 1,
                        default_runs_per_model=resolved.runs_per_model,
                    )
                ]
            )[0]
            effective_model_base_url = (
                model_base_url
                if model_base_url is not None
                else run_spec.model_base_url or resolved.model_base_url
            )
            if run_spec.runs_per_model > 1:
                repeated_output_dir = Path(resolved.output).with_suffix("")
                all_results = asyncio.run(
                    _run_baseline_with_specs(
                        manifest_path=Path(resolved.manifest),
                        run_specs=[run_spec],
                        output_dir=repeated_output_dir,
                        concurrency_override=concurrency,
                        bhce_url=resolved.bhce_url,
                        default_model_base_url=resolved.model_base_url,
                        model_base_url_override=model_base_url,
                        max_model_reruns_on_infra=resolved.max_model_reruns_on_infra,
                        health_timeout_seconds=resolved.health_timeout_seconds,
                        health_poll_interval=resolved.health_poll_interval,
                        telemetry_enabled=resolved.telemetry_enabled,
                        direct_query_safety=DirectQuerySafetyConfig.from_mapping(
                            resolved.direct_query_safety
                        ),
                    )
                )
                write_combined_csv(all_results, repeated_output_dir / "combined.csv")
                write_summary_csv(all_results, repeated_output_dir / "summary.csv")
                return
            asyncio.run(
                run_eval_cli(
                    manifest_path=Path(resolved.manifest),
                    model=run_spec.requested_model,
                    output_path=Path(resolved.output),
                    concurrency=concurrency if concurrency is not None else run_spec.concurrency,
                    bhce_url=resolved.bhce_url,
                    max_model_reruns_on_infra=resolved.max_model_reruns_on_infra,
                    ollama_options=run_spec.ollama_options,
                    run_name=run_spec.run_name,
                    run_config=_effective_run_config(
                        run_spec,
                        model_base_url=effective_model_base_url,
                        telemetry_enabled=resolved.telemetry_enabled,
                        direct_query_safety=DirectQuerySafetyConfig.from_mapping(
                            resolved.direct_query_safety
                        ),
                    ),
                    model_base_url=effective_model_base_url,
                    health_timeout_seconds=resolved.health_timeout_seconds,
                    health_poll_interval=resolved.health_poll_interval,
                    telemetry_enabled=resolved.telemetry_enabled,
                    direct_query_safety=DirectQuerySafetyConfig.from_mapping(
                        resolved.direct_query_safety
                    ),
                )
            )
            return

        if resolved.kind == "eval_mcp":
            run_spec = _dedupe_run_specs(
                [
                    _run_spec_from_entry(
                        resolved.model_entry,
                        default_concurrency=resolved.concurrency or 1,
                        default_runs_per_model=resolved.runs_per_model,
                    )
                ]
            )[0]
            effective_model_base_url = (
                model_base_url
                if model_base_url is not None
                else run_spec.model_base_url or resolved.model_base_url
            )
            effective_max_steps = (
                max_steps
                if max_steps is not None
                else run_spec.max_steps
                if run_spec.max_steps is not None
                else resolved.max_steps
            )
            effective_mcp_tool_loop = (
                mcp_tool_loop
                if mcp_tool_loop is not None
                else run_spec.mcp_tool_loop or resolved.mcp_tool_loop
            )
            effective_openai_compat_telemetry_adapter = (
                openai_compat_telemetry_adapter
                if openai_compat_telemetry_adapter is not None
                else run_spec.openai_compat_telemetry_adapter
                or resolved.openai_compat_telemetry_adapter
            )
            effective_mcp_ollama_read_timeout_seconds = (
                mcp_ollama_read_timeout
                if mcp_ollama_read_timeout is not None
                else run_spec.mcp_ollama_read_timeout_seconds
                if run_spec.mcp_ollama_read_timeout_seconds is not None
                else resolved.mcp_ollama_read_timeout_seconds
            )
            if run_spec.runs_per_model > 1:
                repeated_output_dir = Path(resolved.output).with_suffix("")
                all_results = asyncio.run(
                    _run_baseline_mcp_with_specs(
                        manifest_path=Path(resolved.manifest),
                        run_specs=[run_spec],
                        output_dir=repeated_output_dir,
                        concurrency_override=concurrency,
                        bhce_url=resolved.bhce_url,
                        mcp_dir=resolved.mcp_launcher.mcp_dir,
                        mcp_launcher=resolved.mcp_launcher,
                        max_steps=effective_max_steps,
                        resource_mode=resolved.resource_mode,
                        mcp_tool_loop=resolved.mcp_tool_loop,
                        openai_compat_telemetry_adapter=(resolved.openai_compat_telemetry_adapter),
                        mcp_ollama_read_timeout_seconds=(resolved.mcp_ollama_read_timeout_seconds),
                        default_model_base_url=resolved.model_base_url,
                        max_steps_override=max_steps,
                        model_base_url_override=model_base_url,
                        mcp_tool_loop_override=mcp_tool_loop,
                        openai_compat_telemetry_adapter_override=(openai_compat_telemetry_adapter),
                        mcp_ollama_read_timeout_seconds_override=(mcp_ollama_read_timeout),
                        max_model_reruns_on_infra=resolved.max_model_reruns_on_infra,
                        health_timeout_seconds=resolved.health_timeout_seconds,
                        health_poll_interval=resolved.health_poll_interval,
                        telemetry_enabled=resolved.telemetry_enabled,
                    )
                )
                write_combined_csv(all_results, repeated_output_dir / "combined.csv")
                write_summary_csv(all_results, repeated_output_dir / "summary.csv")
                return
            asyncio.run(
                run_eval_mcp_cli(
                    manifest_path=Path(resolved.manifest),
                    model=run_spec.requested_model,
                    output_path=Path(resolved.output),
                    concurrency=concurrency if concurrency is not None else run_spec.concurrency,
                    bhce_url=resolved.bhce_url,
                    max_model_reruns_on_infra=resolved.max_model_reruns_on_infra,
                    mcp_dir=resolved.mcp_launcher.mcp_dir,
                    mcp_launcher=resolved.mcp_launcher,
                    max_steps=effective_max_steps,
                    resource_mode=resolved.resource_mode,
                    mcp_tool_loop=effective_mcp_tool_loop,
                    openai_compat_telemetry_adapter=(effective_openai_compat_telemetry_adapter),
                    mcp_ollama_read_timeout_seconds=(effective_mcp_ollama_read_timeout_seconds),
                    ollama_options=run_spec.ollama_options,
                    run_name=run_spec.run_name,
                    run_config=_effective_run_config(
                        run_spec,
                        model_base_url=effective_model_base_url,
                        max_steps=effective_max_steps,
                        resource_mode=resolved.resource_mode,
                        mcp_tool_loop=effective_mcp_tool_loop,
                        openai_compat_telemetry_adapter=(effective_openai_compat_telemetry_adapter),
                        mcp_ollama_read_timeout_seconds=(effective_mcp_ollama_read_timeout_seconds),
                        telemetry_enabled=resolved.telemetry_enabled,
                        mcp_launcher=resolved.mcp_launcher,
                    ),
                    model_base_url=effective_model_base_url,
                    health_timeout_seconds=resolved.health_timeout_seconds,
                    health_poll_interval=resolved.health_poll_interval,
                    telemetry_enabled=resolved.telemetry_enabled,
                )
            )
            return

        if resolved.kind == "baseline":
            run_specs = _dedupe_run_specs(
                [
                    _run_spec_from_entry(
                        entry,
                        default_concurrency=resolved.concurrency or 1,
                        default_runs_per_model=resolved.runs_per_model,
                    )
                    for entry in resolved.model_entries or []
                ]
            )
            all_results = asyncio.run(
                _run_baseline_with_specs(
                    manifest_path=Path(resolved.manifest),
                    run_specs=run_specs,
                    output_dir=Path(resolved.output_dir),
                    concurrency_override=concurrency,
                    bhce_url=resolved.bhce_url,
                    default_model_base_url=resolved.model_base_url,
                    model_base_url_override=model_base_url,
                    max_model_reruns_on_infra=resolved.max_model_reruns_on_infra,
                    health_timeout_seconds=resolved.health_timeout_seconds,
                    health_poll_interval=resolved.health_poll_interval,
                    telemetry_enabled=resolved.telemetry_enabled,
                    direct_query_safety=DirectQuerySafetyConfig.from_mapping(
                        resolved.direct_query_safety
                    ),
                )
            )
            combined_csv_path = Path(resolved.output_dir) / "baseline_combined.csv"
            summary_csv_path = Path(resolved.output_dir) / "baseline_summary.csv"
            write_combined_csv(all_results, combined_csv_path)
            write_summary_csv(all_results, summary_csv_path)
            print_comparison(all_results)
            click.echo(f"\nPer-model CSVs written to {resolved.output_dir}/")
            click.echo(f"Combined CSV written to {combined_csv_path}")
            click.echo(f"Summary CSV written to {summary_csv_path}")
            return

        if resolved.kind == "baseline_mcp":
            run_specs = _dedupe_run_specs(
                [
                    _run_spec_from_entry(
                        entry,
                        default_concurrency=resolved.concurrency or 1,
                        default_runs_per_model=resolved.runs_per_model,
                    )
                    for entry in resolved.model_entries or []
                ]
            )
            all_results = asyncio.run(
                _run_baseline_mcp_with_specs(
                    manifest_path=Path(resolved.manifest),
                    run_specs=run_specs,
                    output_dir=Path(resolved.output_dir),
                    concurrency_override=concurrency,
                    bhce_url=resolved.bhce_url,
                    mcp_dir=resolved.mcp_launcher.mcp_dir,
                    mcp_launcher=resolved.mcp_launcher,
                    max_steps=resolved.max_steps,
                    resource_mode=resolved.resource_mode,
                    mcp_tool_loop=resolved.mcp_tool_loop,
                    openai_compat_telemetry_adapter=resolved.openai_compat_telemetry_adapter,
                    mcp_ollama_read_timeout_seconds=resolved.mcp_ollama_read_timeout_seconds,
                    default_model_base_url=resolved.model_base_url,
                    max_steps_override=max_steps,
                    model_base_url_override=model_base_url,
                    mcp_tool_loop_override=mcp_tool_loop,
                    openai_compat_telemetry_adapter_override=(openai_compat_telemetry_adapter),
                    mcp_ollama_read_timeout_seconds_override=mcp_ollama_read_timeout,
                    max_model_reruns_on_infra=resolved.max_model_reruns_on_infra,
                    health_timeout_seconds=resolved.health_timeout_seconds,
                    health_poll_interval=resolved.health_poll_interval,
                    telemetry_enabled=resolved.telemetry_enabled,
                )
            )
            combined_csv_path = Path(resolved.output_dir) / "baseline_combined.csv"
            summary_csv_path = Path(resolved.output_dir) / "baseline_summary.csv"
            write_combined_csv(all_results, combined_csv_path)
            write_summary_csv(all_results, summary_csv_path)
            print_comparison(all_results)
            click.echo(f"\nPer-model CSVs written to {resolved.output_dir}/")
            click.echo(f"Combined CSV written to {combined_csv_path}")
            click.echo(f"Summary CSV written to {summary_csv_path}")
            return

        if resolved.kind == "smoke_eval":
            result = asyncio.run(
                run_smoke_eval(
                    manifest_path=Path(resolved.manifest),
                    output_dir=Path(resolved.output_dir),
                    bhce_url=resolved.bhce_url,
                )
            )
            print_smoke_eval(result)
            if not result.ok:
                raise SystemExit(1)
            return

        if resolved.kind == "smoke_mcp":
            result = asyncio.run(
                run_smoke_mcp_eval(
                    manifest_path=Path(resolved.manifest),
                    output_dir=Path(resolved.output_dir),
                    bhce_url=resolved.bhce_url,
                    mcp_dir=resolved.mcp_launcher.mcp_dir,
                    mcp_launcher=resolved.mcp_launcher,
                    max_steps=resolved.max_steps,
                    resource_mode=resolved.resource_mode,
                )
            )
            print_smoke_eval(result)
            if not result.ok:
                raise SystemExit(1)
            return

        if resolved.kind == "preflight":
            result = asyncio.run(
                run_preflight(
                    manifest_path=Path(resolved.manifest),
                    output_dir=Path(resolved.output_dir),
                    bhce_url=resolved.bhce_url,
                    timeout_seconds=resolved.health_timeout_seconds,
                    poll_interval=resolved.health_poll_interval,
                )
            )
            print_preflight(result)
            if not result.ok:
                raise SystemExit(1)
            return

        raise click.UsageError(f"Unsupported profile kind: {resolved.kind}")

    config_file = Path(config_path)
    if _run_model_matrix_config(config_file):
        return

    if run_all_profiles:
        enabled_profiles = [info for info in list_run_profiles(config_file) if info.enabled]
        if not enabled_profiles:
            raise click.UsageError("Config contains no enabled profiles to run.")
        total = len(enabled_profiles)
        failures: list[tuple[str, str]] = []
        for index, info in enumerate(enabled_profiles, 1):
            click.echo(f"\n[{index}/{total}] {info.profile_name} ({info.kind})")
            try:
                resolved = load_run_profile(
                    config_file,
                    profile_name=info.profile_name,
                    overrides=overrides,
                )
                _run_one_profile(resolved)
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 1
                if code in (None, 0):
                    continue
                failures.append((info.profile_name, f"exit code {code}"))
                click.echo(f"Profile failed: {info.profile_name} (exit code {code})", err=True)
                if not keep_going:
                    raise
            except Exception as exc:
                failures.append((info.profile_name, str(exc) or exc.__class__.__name__))
                click.echo(f"Profile failed: {info.profile_name} ({exc})", err=True)
                if not keep_going:
                    raise
        if failures:
            click.echo("\nRun-all profile failures:", err=True)
            for profile_name, detail in failures:
                click.echo(f"- {profile_name}: {detail}", err=True)
            raise SystemExit(1)
        return

    resolved = load_run_profile(
        config_file,
        profile_name=profile,
        overrides=overrides,
    )
    _run_one_profile(resolved)


@main.command()
@click.option(
    "--manifest", "-m", required=True, type=click.Path(exists=True), help="Path to manifest.json"
)
@click.option(
    "--output-dir", "-o", required=True, type=click.Path(), help="Directory for smoke-test CSVs"
)
@click.option(
    "--bhce-url", default=None, help="BH CE base URL (overrides BLOODHOUND_DOMAIN env var)"
)
@click.option(
    "--timeout",
    default=60.0,
    show_default=True,
    type=float,
    help="Seconds to wait for BHCE to become healthy",
)
@click.option(
    "--poll-interval",
    default=5.0,
    show_default=True,
    type=float,
    help="Seconds between health probes",
)
def preflight(
    manifest: str,
    output_dir: str,
    bhce_url: str | None,
    timeout: float,
    poll_interval: float,
) -> None:
    """Run BloodHound health, ingest verification, and smoke eval in one command."""
    import asyncio

    from .eval.ops import print_preflight, run_preflight

    result = asyncio.run(
        run_preflight(
            manifest_path=Path(manifest),
            output_dir=Path(output_dir),
            bhce_url=bhce_url,
            timeout_seconds=timeout,
            poll_interval=poll_interval,
        )
    )
    print_preflight(result)
    if not result.ok:
        raise SystemExit(1)


@main.command()
@click.option(
    "--manifest", "-m", required=True, type=click.Path(exists=True), help="Path to manifest.json"
)
@click.option("--model", "models", multiple=True, help="Model to evaluate (repeat for multiple)")
@click.option(
    "--models-file",
    type=click.Path(exists=True),
    default=None,
    help="YAML file listing models to evaluate",
)
@click.option(
    "--output-dir", "-o", required=True, type=click.Path(), help="Directory for per-model CSV files"
)
@click.option("--concurrency", default=None, type=int, help="Override concurrency for all models")
@click.option(
    "--bhce-url", default=None, help="BH CE base URL (overrides BLOODHOUND_DOMAIN env var)"
)
@click.option(
    "--telemetry/--no-telemetry",
    "telemetry_enabled",
    default=True,
    show_default=True,
    help="Write portable run/model/system telemetry artifacts.",
)
def baseline(
    manifest: str,
    models: tuple[str, ...],
    models_file: str | None,
    output_dir: str,
    concurrency: int | None,
    bhce_url: str | None,
    telemetry_enabled: bool,
) -> None:
    """Evaluate multiple models against the same manifest and print a comparison table.

    Models can be specified via --model (repeatable), --models-file models.yaml, or both.
    """
    import asyncio

    from .eval.report import print_comparison, write_combined_csv, write_summary_csv

    run_specs = _build_run_specs(models=models, models_file=models_file)
    out = Path(output_dir)
    all_results = asyncio.run(
        _run_baseline_with_specs(
            manifest_path=Path(manifest),
            run_specs=run_specs,
            output_dir=out,
            concurrency_override=concurrency,
            bhce_url=bhce_url,
            telemetry_enabled=telemetry_enabled,
        )
    )

    combined_csv_path = out / "baseline_combined.csv"
    summary_csv_path = out / "baseline_summary.csv"
    write_combined_csv(all_results, combined_csv_path)
    write_summary_csv(all_results, summary_csv_path)
    print_comparison(all_results)
    click.echo(f"\nPer-model CSVs written to {output_dir}/")
    click.echo(f"Combined CSV written to {combined_csv_path}")
    click.echo(f"Summary CSV written to {summary_csv_path}")


def _run_baseline_mcp(
    manifest: str,
    models: tuple[str, ...],
    models_file: str | None,
    output_dir: str,
    concurrency: int | None,
    bhce_url: str | None,
    mcp_dir: str,
    max_steps: int,
    resource_mode: str,
    mcp_tool_loop: str = "auto",
    openai_compat_telemetry_adapter: str = "auto",
    mcp_ollama_read_timeout_seconds: float = 900.0,
    telemetry_enabled: bool = True,
) -> None:
    import asyncio

    from .eval.report import print_comparison, write_combined_csv, write_summary_csv

    run_specs = _build_run_specs(models=models, models_file=models_file)
    out = Path(output_dir)
    all_results = asyncio.run(
        _run_baseline_mcp_with_specs(
            manifest_path=Path(manifest),
            run_specs=run_specs,
            output_dir=out,
            concurrency_override=concurrency,
            bhce_url=bhce_url,
            mcp_dir=Path(mcp_dir),
            mcp_launcher=MCPLauncherConfig.local_checkout(Path(mcp_dir)),
            max_steps=max_steps,
            resource_mode=resource_mode,
            mcp_tool_loop=mcp_tool_loop,
            openai_compat_telemetry_adapter=openai_compat_telemetry_adapter,
            mcp_ollama_read_timeout_seconds=mcp_ollama_read_timeout_seconds,
            telemetry_enabled=telemetry_enabled,
        )
    )

    combined_csv_path = out / "baseline_combined.csv"
    summary_csv_path = out / "baseline_summary.csv"
    write_combined_csv(all_results, combined_csv_path)
    write_summary_csv(all_results, summary_csv_path)
    print_comparison(all_results)
    click.echo(f"\nPer-model CSVs written to {output_dir}/")
    click.echo(f"Combined CSV written to {combined_csv_path}")
    click.echo(f"Summary CSV written to {summary_csv_path}")


@main.command(name="baseline-mcp")
@click.option(
    "--manifest", "-m", required=True, type=click.Path(exists=True), help="Path to manifest.json"
)
@click.option("--model", "models", multiple=True, help="Model to evaluate (repeat for multiple)")
@click.option(
    "--models-file",
    type=click.Path(exists=True),
    default=None,
    help="YAML file listing models to evaluate",
)
@click.option(
    "--output-dir", "-o", required=True, type=click.Path(), help="Directory for per-model CSV files"
)
@click.option("--concurrency", default=None, type=int, help="Override concurrency for all models")
@click.option(
    "--bhce-url", default=None, help="BH CE base URL (overrides BLOODHOUND_DOMAIN env var)"
)
@click.option(
    "--mcp-dir",
    default="../bloodhound-mcp",
    type=click.Path(exists=True),
    show_default=True,
    help="Path to local bloodhound-mcp repo",
)
@click.option("--max-steps", default=12, show_default=True, help="Max agent/tool steps")
@click.option(
    "--resource-mode",
    type=click.Choice(["off", "on-demand"]),
    default="off",
    show_default=True,
    help="Whether MCP reference resources are available to the model.",
)
@click.option(
    "--mcp-tool-loop",
    type=click.Choice(["auto", "inspect", "native-ollama", "native-openai-compatible"]),
    default="auto",
    show_default=True,
    help="MCP tool-calling loop implementation.",
)
@click.option(
    "--openai-compat-telemetry-adapter",
    type=click.Choice(["auto", "generic", "llama-cpp", "mlx-lm", "vllm", "lm-studio"]),
    default="auto",
    show_default=True,
    help="Telemetry parser for native OpenAI-compatible MCP runs.",
)
@click.option(
    "--mcp-ollama-read-timeout",
    default=900.0,
    show_default=True,
    type=float,
    help="Read timeout in seconds for native Ollama MCP chat streams.",
)
@click.option(
    "--telemetry/--no-telemetry",
    "telemetry_enabled",
    default=True,
    show_default=True,
    help="Write portable run/model/system telemetry artifacts.",
)
def baseline_mcp(
    manifest: str,
    models: tuple[str, ...],
    models_file: str | None,
    output_dir: str,
    concurrency: int | None,
    bhce_url: str | None,
    mcp_dir: str,
    max_steps: int,
    resource_mode: str,
    mcp_tool_loop: str,
    openai_compat_telemetry_adapter: str,
    mcp_ollama_read_timeout: float,
    telemetry_enabled: bool,
) -> None:
    """Evaluate multiple models in MCP mode and print a comparison table."""
    _run_baseline_mcp(
        manifest=manifest,
        models=models,
        models_file=models_file,
        output_dir=output_dir,
        concurrency=concurrency,
        bhce_url=bhce_url,
        mcp_dir=mcp_dir,
        max_steps=max_steps,
        resource_mode=resource_mode,
        mcp_tool_loop=mcp_tool_loop,
        openai_compat_telemetry_adapter=openai_compat_telemetry_adapter,
        mcp_ollama_read_timeout_seconds=mcp_ollama_read_timeout,
        telemetry_enabled=telemetry_enabled,
    )


@main.command(name="baseline-mcp-resources")
@click.option(
    "--manifest", "-m", required=True, type=click.Path(exists=True), help="Path to manifest.json"
)
@click.option("--model", "models", multiple=True, help="Model to evaluate (repeat for multiple)")
@click.option(
    "--models-file",
    type=click.Path(exists=True),
    default=None,
    help="YAML file listing models to evaluate",
)
@click.option(
    "--output-dir", "-o", required=True, type=click.Path(), help="Directory for per-model CSV files"
)
@click.option("--concurrency", default=None, type=int, help="Override concurrency for all models")
@click.option(
    "--bhce-url", default=None, help="BH CE base URL (overrides BLOODHOUND_DOMAIN env var)"
)
@click.option(
    "--mcp-dir",
    default="../bloodhound-mcp",
    type=click.Path(exists=True),
    show_default=True,
    help="Path to local bloodhound-mcp repo",
)
@click.option("--max-steps", default=12, show_default=True, help="Max agent/tool steps")
@click.option(
    "--mcp-tool-loop",
    type=click.Choice(["auto", "inspect", "native-ollama", "native-openai-compatible"]),
    default="auto",
    show_default=True,
    help="MCP tool-calling loop implementation.",
)
@click.option(
    "--openai-compat-telemetry-adapter",
    type=click.Choice(["auto", "generic", "llama-cpp", "mlx-lm", "vllm", "lm-studio"]),
    default="auto",
    show_default=True,
    help="Telemetry parser for native OpenAI-compatible MCP runs.",
)
@click.option(
    "--mcp-ollama-read-timeout",
    default=900.0,
    show_default=True,
    type=float,
    help="Read timeout in seconds for native Ollama MCP chat streams.",
)
@click.option(
    "--telemetry/--no-telemetry",
    "telemetry_enabled",
    default=True,
    show_default=True,
    help="Write portable run/model/system telemetry artifacts.",
)
def baseline_mcp_resources(
    manifest: str,
    models: tuple[str, ...],
    models_file: str | None,
    output_dir: str,
    concurrency: int | None,
    bhce_url: str | None,
    mcp_dir: str,
    max_steps: int,
    mcp_tool_loop: str,
    openai_compat_telemetry_adapter: str,
    mcp_ollama_read_timeout: float,
    telemetry_enabled: bool,
) -> None:
    """Run the Phase 3C resources-enabled multi-model MCP baseline sweep."""
    _run_baseline_mcp(
        manifest=manifest,
        models=models,
        models_file=models_file,
        output_dir=output_dir,
        concurrency=concurrency,
        bhce_url=bhce_url,
        mcp_dir=mcp_dir,
        max_steps=max_steps,
        resource_mode="on-demand",
        mcp_tool_loop=mcp_tool_loop,
        openai_compat_telemetry_adapter=openai_compat_telemetry_adapter,
        mcp_ollama_read_timeout_seconds=mcp_ollama_read_timeout,
        telemetry_enabled=telemetry_enabled,
    )


def _build_manifest(graph: ADGraph, seed: int, *, archive: bytes | None = None) -> dict:
    """Build the ground-truth manifest for the generated dataset."""
    projected_nodes = list(project_nodes_for_sharphound(graph).values())
    projected_node_counts = Counter(
        _NODE_TYPE_TO_FILE[node.node_type]
        for node in projected_nodes
        if node.node_type in _NODE_TYPE_TO_FILE
    )
    projected_node_counts_by_file = {
        file_type: projected_node_counts.get(file_type, 0) for file_type in _VERSIONS
    }
    relationships = _relationships_from_archive(
        archive if archive is not None else _build_zip(graph)
    )
    relationship_counts = Counter(canonical_relationship_kind(kind) for _, kind, _ in relationships)

    return {
        "schema_version": "ori-generated-manifest-v2",
        "domain": graph.domain,
        "domain_sid": graph.domain_sid,
        "seed": seed,
        "metadata": {
            "relationship_contract_version": RELATIONSHIP_CONTRACT_VERSION,
            "sharphound": {
                "encoding_profile": SHARPHOUND_PROFILE,
                "file_versions": dict(_VERSIONS),
            },
            "compatibility": {
                "bloodhound_ce": {
                    "tested_version": "9.1.0",
                    "supported_baseline": "9.1.0",
                }
            },
        },
        "stats": {
            **projected_node_counts_by_file,
            "projected_nodes_by_file": projected_node_counts_by_file,
            "total_nodes": len(projected_nodes),
            "total_edges": graph.edge_count(),
            "total_edges_scope": "internal_graph",
            "internal_graph_edges": graph.edge_count(),
        },
        "relationship_summary": {
            "source": "sharphound_archive_projection",
            "counting": "unique_structural_relationships",
            "total_relationships": len(relationships),
            "counts_by_kind": dict(sorted(relationship_counts.items())),
        },
        "planted_paths": [
            {
                "template_id": p.template_id,
                "tier": p.tier,
                "category": p.category,
                "description": p.description,
                "source_node": p.source_node,
                "source_name": (
                    graph.get_node(p.source_node).properties.get("name", "")
                    if graph.get_node(p.source_node)
                    else ""
                ),
                "target_node": p.target_node,
                "target_name": (
                    graph.get_node(p.target_node).properties.get("name", "")
                    if graph.get_node(p.target_node)
                    else ""
                ),
                "path_edges": [
                    {
                        "source": src,
                        "edge": canonical_relationship_kind(edge),
                        "target": tgt,
                    }
                    for src, edge, tgt in p.path_edges
                ],
                "supporting_edges": [
                    {
                        "source": src,
                        "source_name": str(graph.require_node(src).properties.get("name", src)),
                        "edge": canonical_relationship_kind(edge),
                        "target": tgt,
                        "target_name": str(graph.require_node(tgt).properties.get("name", tgt)),
                    }
                    for src, edge, tgt in p.metadata.get("supporting_edges", [])
                ],
                "verification_cypher": p.verification_cypher,
                "mitre": p.mitre,
                "metadata": p.metadata,
                "scenario_family": p.metadata.get("scenario_family", ""),
                "critical_nodes": p.metadata.get("critical_nodes", []),
                "required_capabilities": p.metadata.get("required_capabilities", []),
                "required_mechanisms": p.metadata.get("required_mechanisms", []),
                "required_sequence": p.metadata.get("required_sequence", []),
                "terminal_escalation_type": p.metadata.get("terminal_escalation_type", ""),
                "tool_effort": p.metadata.get("tool_effort", {}),
                "negative_control": p.metadata.get("negative_control", False),
                "expected_rejection_reasons": p.metadata.get("expected_rejection_reasons", []),
                "decoy_edges": p.metadata.get("decoy_edges", []),
                "invalidated_edges": p.metadata.get("invalidated_edges", []),
                "template_version": p.metadata.get("template_version", ""),
            }
            for p in graph.planted_paths
        ],
    }
