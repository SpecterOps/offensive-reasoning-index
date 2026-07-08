"""CLI entry point for ori."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click
import yaml

from .benchmarks import describe_benchmark, get_benchmark, list_benchmarks
from .generator.attack_paths import plant_all_paths
from .generator.graph import ADGraph
from .generator.org import build_org
from .generator.phase4 import build_phase4_v1_graph
from .generator.security import apply_baseline_security
from .generator.serializer import serialize_to_dir, serialize_to_zip
from .run_config import RunConfigOverrides, list_run_profiles, load_run_profile


@dataclass(frozen=True)
class RunSpec:
    run_name: str
    requested_model: str
    concurrency: int
    ollama_options: dict | None
    model_base_url: str | None
    max_steps: int | None
    mcp_tool_loop: str | None
    openai_compat_telemetry_adapter: str | None
    mcp_ollama_read_timeout_seconds: float | None
    config_identity: dict[str, Any]
    config_identity_json: str
    file_slug: str


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
    identity = {k: v for k, v in entry.items() if k != "name"}
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
) -> RunSpec:
    if isinstance(entry, str):
        explicit_name = None
        model = entry
        concurrency = default_concurrency
        identity: dict[str, Any] = {"model": entry}
    elif isinstance(entry, dict):
        if "model" not in entry:
            raise click.UsageError("Model config objects must include a model field.")
        explicit_name = entry.get("name")
        model = entry["model"]
        concurrency = entry.get("concurrency", default_concurrency)
        identity = _normalize_identity_config(entry)
    else:
        raise click.UsageError("Model config entries must be strings or objects.")

    config_identity_json = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    run_name = explicit_name or _fallback_run_name(model, identity)
    return RunSpec(
        run_name=run_name,
        requested_model=model,
        concurrency=concurrency,
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
        for entry in cfg.get("models", []):
            run_specs.append(_run_spec_from_entry(entry, default_concurrency=default_concurrency))

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
) -> dict:
    serialize_to_zip(graph, output_zip)
    manifest = _build_manifest(graph, seed)
    manifest.setdefault("metadata", {})
    manifest["metadata"].update(
        {
            "generator_profile": generator_profile,
            "profile_kind": "generate",
        }
    )
    output_manifest.parent.mkdir(parents=True, exist_ok=True)
    output_manifest.write_text(json.dumps(manifest, indent=2))
    return manifest


def _generate_profile_dataset(resolved) -> None:
    if resolved.generator_profile != "phase4_v1":
        raise click.UsageError(
            f"Unsupported generator profile {resolved.generator_profile!r}; expected phase4_v1."
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
) -> dict[str, Any]:
    config = dict(run_spec.config_identity)
    if model_base_url is not None and "model_base_url" not in config:
        config["model_base_url"] = model_base_url
    if max_steps is not None and "max_steps" not in config:
        config["max_steps"] = max_steps
    if resource_mode is not None and "resource_mode" not in config:
        config["resource_mode"] = resource_mode
    if mcp_tool_loop is not None and "mcp_tool_loop" not in config and "tool_loop" not in config:
        config["mcp_tool_loop"] = mcp_tool_loop
    if (
        openai_compat_telemetry_adapter is not None
        and "openai_compat_telemetry_adapter" not in config
        and "telemetry_adapter" not in config
    ):
        config["openai_compat_telemetry_adapter"] = openai_compat_telemetry_adapter
    if (
        mcp_ollama_read_timeout_seconds is not None
        and "mcp_ollama_read_timeout_seconds" not in config
    ):
        config["mcp_ollama_read_timeout_seconds"] = mcp_ollama_read_timeout_seconds
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
) -> dict[str, list]:
    from .eval.runner import run_eval_cli_bare

    output_dir.mkdir(parents=True, exist_ok=True)
    results = {}
    for i, run_spec in enumerate(run_specs, 1):
        effective_concurrency = (
            concurrency_override if concurrency_override is not None else run_spec.concurrency
        )
        effective_model_base_url = (
            model_base_url_override
            if model_base_url_override is not None
            else run_spec.model_base_url or default_model_base_url
        )
        csv_path = output_dir / f"{run_spec.file_slug}.csv"
        opts_str = f", options={run_spec.ollama_options}" if run_spec.ollama_options else ""
        base_url_str = f", base_url={effective_model_base_url}" if effective_model_base_url else ""
        click.echo(
            f"\n[{i}/{len(run_specs)}] {run_spec.run_name} -> {run_spec.requested_model}  "
            f"(concurrency={effective_concurrency}{opts_str}{base_url_str})"
        )
        results[run_spec.run_name] = await run_eval_cli_bare(
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
            ),
            model_base_url=effective_model_base_url,
            health_timeout_seconds=health_timeout_seconds,
            health_poll_interval=health_poll_interval,
            telemetry_enabled=telemetry_enabled,
        )
    return results


async def _run_baseline_mcp_with_specs(
    *,
    manifest_path: Path,
    run_specs: list[RunSpec],
    output_dir: Path,
    concurrency_override: int | None,
    bhce_url: str | None,
    mcp_dir: Path,
    max_steps: int,
    resource_mode: str,
    mcp_tool_loop: str,
    openai_compat_telemetry_adapter: str,
    mcp_ollama_read_timeout_seconds: float,
    default_model_base_url: str | None = None,
    max_steps_override: int | None = None,
    model_base_url_override: str | None = None,
    max_model_reruns_on_infra: int = 1,
    health_timeout_seconds: float = 60.0,
    health_poll_interval: float = 5.0,
    telemetry_enabled: bool = True,
) -> dict[str, list]:
    from .eval.runner import run_eval_mcp_cli_bare

    output_dir.mkdir(parents=True, exist_ok=True)
    results = {}
    for i, run_spec in enumerate(run_specs, 1):
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
            run_spec.mcp_ollama_read_timeout_seconds
            if run_spec.mcp_ollama_read_timeout_seconds is not None
            else mcp_ollama_read_timeout_seconds
        )
        effective_mcp_tool_loop = run_spec.mcp_tool_loop or mcp_tool_loop
        effective_openai_compat_telemetry_adapter = (
            run_spec.openai_compat_telemetry_adapter or openai_compat_telemetry_adapter
        )
        csv_path = output_dir / f"{run_spec.file_slug}.csv"
        opts_str = f", options={run_spec.ollama_options}" if run_spec.ollama_options else ""
        base_url_str = f", base_url={effective_model_base_url}" if effective_model_base_url else ""
        click.echo(
            f"\n[{i}/{len(run_specs)}] {run_spec.run_name} -> {run_spec.requested_model}  "
            f"(concurrency={effective_concurrency}{opts_str}{base_url_str}, max_steps={effective_max_steps})"  # noqa: E501
        )
        results[run_spec.run_name] = await run_eval_mcp_cli_bare(
            manifest_path=manifest_path,
            model=run_spec.requested_model,
            output_path=csv_path,
            concurrency=effective_concurrency,
            bhce_url=bhce_url,
            ollama_options=run_spec.ollama_options,
            max_model_reruns_on_infra=max_model_reruns_on_infra,
            mcp_dir=mcp_dir,
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


@main.group(name="benchmark")
def benchmark_group() -> None:
    """Explore named ORI benchmark products."""


@benchmark_group.command(name="list")
def benchmark_list() -> None:
    """List public benchmark products."""

    for benchmark in list_benchmarks():
        click.echo(
            f"{benchmark.name:8} {benchmark.status:8} "
            f"tasks={benchmark.default_task_count:<3} "
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
    task_set = (
        benchmark.diagnostic_task_set
        if mode == "diagnostic" and benchmark.diagnostic_task_set
        else benchmark.official_task_set
    )
    task_count = (
        benchmark.diagnostic_task_count
        if mode == "diagnostic" and benchmark.diagnostic_task_count is not None
        else benchmark.default_task_count
    )
    click.echo(f"Benchmark: {benchmark.name}")
    click.echo(f"Mode: {mode}")
    click.echo(f"Graph profile: {benchmark.graph_profile}")
    click.echo(f"Task set: {task_set}")
    click.echo(f"Task count: {task_count}")
    click.echo(f"Scoring profile: {benchmark.scoring_profile}")
    click.echo(
        "Status: planned — use phase/run-config commands until benchmark launch plumbing lands."
    )


@main.command()
@click.option("--domain", default="CORP.LOCAL", help="AD domain name")
@click.option("--seed", type=int, default=42, help="Random seed for reproducibility")
@click.option("--users", type=int, default=20, help="Number of regular users to generate")
@click.option("--workstations", type=int, default=8, help="Number of workstations")
@click.option("--servers", type=int, default=4, help="Number of servers")
@click.option("--output", "-o", type=click.Path(), default="datasets/output", help="Output path")
@click.option("--zip/--no-zip", "as_zip", default=True, help="Output as zip (BH CE ingest format)")
def generate(
    domain: str,
    seed: int,
    users: int,
    workstations: int,
    servers: int,
    output: str,
    as_zip: bool,
) -> None:
    """Generate a synthetic AD graph with planted attack paths."""
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
    manifest = _build_manifest(graph, seed)
    manifest_path.write_text(json.dumps(manifest, indent=2))
    click.echo(f"        Manifest: {manifest_path}")

    click.echo(
        f"\nDone. {graph.node_count()} nodes, {graph.edge_count()} edges, {len(planted)} planted paths."  # noqa: E501
    )
    for path in planted:
        click.echo(f"  [{path.tier}] {path.template_id}: {path.description[:80]}...")


@main.command(name="preflight-tasks")
@click.option(
    "--manifest",
    "manifest_path",
    required=True,
    type=click.Path(exists=True),
    help="Generated task manifest JSON",
)
@click.option("--track", type=click.Choice(["mcp", "cypher"]), default="mcp", show_default=True)
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
@click.option("--track", type=click.Choice(["mcp", "cypher"]), default="mcp", show_default=True)
@click.option(
    "--output", "output_path", required=True, type=click.Path(), help="Projection JSON output path"
)
def score_answers(manifest_path: str, answers_path: str, track: str, output_path: str) -> None:
    """Grade a structured answers file without launching a model campaign."""
    from .eval.answer_scoring import write_score_answers_projection

    try:
        projection = write_score_answers_projection(
            manifest_path=Path(manifest_path),
            answers_path=Path(answers_path),
            track=track,
            output_path=Path(output_path),
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    summary = projection["summary"]
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
    asyncio.run(
        run_eval_cli(
            manifest_path=Path(manifest),
            model=model,
            output_path=Path(output),
            concurrency=concurrency,
            bhce_url=bhce_url,
            ollama_options=run_spec.ollama_options,
            run_name=run_spec.run_name,
            run_config=_effective_run_config(run_spec, telemetry_enabled=telemetry_enabled),
            telemetry_enabled=telemetry_enabled,
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

    asyncio.run(
        run_eval_mcp_cli(
            manifest_path=Path(manifest),
            model=model,
            output_path=Path(output),
            concurrency=concurrency,
            bhce_url=bhce_url,
            mcp_dir=Path(mcp_dir),
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

    result = asyncio.run(
        run_smoke_mcp_eval(
            manifest_path=Path(manifest),
            output_dir=Path(output_dir),
            bhce_url=bhce_url,
            mcp_dir=Path(mcp_dir),
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
                    )
                ]
            )[0]
            effective_model_base_url = (
                model_base_url
                if model_base_url is not None
                else run_spec.model_base_url or resolved.model_base_url
            )
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
                    ),
                    model_base_url=effective_model_base_url,
                    health_timeout_seconds=resolved.health_timeout_seconds,
                    health_poll_interval=resolved.health_poll_interval,
                    telemetry_enabled=resolved.telemetry_enabled,
                )
            )
            return

        if resolved.kind == "eval_mcp":
            run_spec = _dedupe_run_specs(
                [
                    _run_spec_from_entry(
                        resolved.model_entry,
                        default_concurrency=resolved.concurrency or 1,
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
            asyncio.run(
                run_eval_mcp_cli(
                    manifest_path=Path(resolved.manifest),
                    model=run_spec.requested_model,
                    output_path=Path(resolved.output),
                    concurrency=concurrency if concurrency is not None else run_spec.concurrency,
                    bhce_url=resolved.bhce_url,
                    max_model_reruns_on_infra=resolved.max_model_reruns_on_infra,
                    mcp_dir=Path(resolved.mcp_dir),
                    max_steps=effective_max_steps,
                    resource_mode=resolved.resource_mode,
                    mcp_tool_loop=run_spec.mcp_tool_loop or resolved.mcp_tool_loop,
                    openai_compat_telemetry_adapter=(
                        run_spec.openai_compat_telemetry_adapter
                        or resolved.openai_compat_telemetry_adapter
                    ),
                    mcp_ollama_read_timeout_seconds=resolved.mcp_ollama_read_timeout_seconds,
                    ollama_options=run_spec.ollama_options,
                    run_name=run_spec.run_name,
                    run_config=_effective_run_config(
                        run_spec,
                        model_base_url=effective_model_base_url,
                        max_steps=effective_max_steps,
                        resource_mode=resolved.resource_mode,
                        mcp_tool_loop=run_spec.mcp_tool_loop or resolved.mcp_tool_loop,
                        openai_compat_telemetry_adapter=(
                            run_spec.openai_compat_telemetry_adapter
                            or resolved.openai_compat_telemetry_adapter
                        ),
                        mcp_ollama_read_timeout_seconds=resolved.mcp_ollama_read_timeout_seconds,
                        telemetry_enabled=resolved.telemetry_enabled,
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
                    _run_spec_from_entry(entry, default_concurrency=resolved.concurrency or 1)
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
                    _run_spec_from_entry(entry, default_concurrency=resolved.concurrency or 1)
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
                    mcp_dir=Path(resolved.mcp_dir),
                    max_steps=resolved.max_steps,
                    resource_mode=resolved.resource_mode,
                    mcp_tool_loop=resolved.mcp_tool_loop,
                    openai_compat_telemetry_adapter=resolved.openai_compat_telemetry_adapter,
                    mcp_ollama_read_timeout_seconds=resolved.mcp_ollama_read_timeout_seconds,
                    default_model_base_url=resolved.model_base_url,
                    max_steps_override=max_steps,
                    model_base_url_override=model_base_url,
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
                    mcp_dir=Path(resolved.mcp_dir),
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


def _build_manifest(graph: ADGraph, seed: int) -> dict:
    """Build the ground-truth manifest for the generated dataset."""
    users = graph.nodes_by_type("User")
    computers = graph.nodes_by_type("Computer")
    groups = graph.nodes_by_type("Group")
    ous = graph.nodes_by_type("OU")

    return {
        "domain": graph.domain,
        "domain_sid": graph.domain_sid,
        "seed": seed,
        "stats": {
            "users": len(users),
            "computers": len(computers),
            "groups": len(groups),
            "ous": len(ous),
            "total_nodes": graph.node_count(),
            "total_edges": graph.edge_count(),
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
                    {"source": src, "edge": edge, "target": tgt} for src, edge, tgt in p.path_edges
                ],
                "verification_cypher": p.verification_cypher,
                "mitre": p.mitre,
                "metadata": p.metadata,
                "scenario_family": p.metadata.get("scenario_family", ""),
                "critical_nodes": p.metadata.get("critical_nodes", []),
                "required_capabilities": p.metadata.get("required_capabilities", []),
                "template_version": p.metadata.get("template_version", ""),
            }
            for p in graph.planted_paths
        ],
    }
