"""CLI entry point for ori."""

from __future__ import annotations

import json
from pathlib import Path

import click
import yaml

from .generator.attack_paths import plant_all_paths
from .generator.graph import ADGraph
from .generator.org import build_org
from .generator.security import apply_baseline_security
from .generator.serializer import serialize_to_dir, serialize_to_zip


def _parse_ollama_options(options: tuple[str, ...]) -> dict:
    parsed: dict = {}
    for option in options:
        if "=" not in option:
            raise click.BadParameter(
                f"Invalid --ollama-option {option!r}. Expected KEY=VALUE."
            )
        key, raw_value = option.split("=", 1)
        key = key.strip()
        if not key:
            raise click.BadParameter("Ollama option key cannot be empty.")
        parsed[key] = yaml.safe_load(raw_value)
    return parsed


@click.group()
def main() -> None:
    """ori: benchmark for evaluating AI on AD attack path analysis."""
    # Load .env from the current directory (or any parent) on every invocation.
    # override=False means shell env vars and CI/CD vars take precedence over the file.
    from dotenv import load_dotenv
    load_dotenv(override=False)


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

    click.echo(f"\nDone. {graph.node_count()} nodes, {graph.edge_count()} edges, {len(planted)} planted paths.")
    for path in planted:
        click.echo(f"  [{path.tier}] {path.template_id}: {path.description[:80]}...")


@main.command()
@click.option("--manifest", "-m", required=True, type=click.Path(exists=True), help="Path to manifest.json")
@click.option("--model", required=True, help="Model string, e.g. anthropic/claude-sonnet-4-5 or ollama/llama3.1:8b")
@click.option("--output", "-o", required=True, type=click.Path(), help="Output CSV path")
@click.option("--concurrency", default=3, show_default=True, help="Max concurrent model calls")
@click.option("--bhce-url", default=None, help="BH CE base URL (overrides BLOODHOUND_DOMAIN env var)")
@click.option(
    "--ollama-option",
    "ollama_options_raw",
    multiple=True,
    help="Repeatable Ollama option in KEY=VALUE form, e.g. --ollama-option num_ctx=16384",
)
def eval(
    manifest: str,
    model: str,
    output: str,
    concurrency: int,
    bhce_url: str | None,
    ollama_options_raw: tuple[str, ...],
) -> None:
    """Run evaluation: generate tasks from manifest, run model, grade results."""
    import asyncio
    from .eval.runner import run_eval_cli
    asyncio.run(run_eval_cli(
        manifest_path=Path(manifest),
        model=model,
        output_path=Path(output),
        concurrency=concurrency,
        bhce_url=bhce_url,
        ollama_options=_parse_ollama_options(ollama_options_raw),
    ))


@main.command(name="eval-mcp")
@click.option("--manifest", "-m", required=True, type=click.Path(exists=True), help="Path to manifest.json")
@click.option("--model", required=True, help="Model string, e.g. ollama/qwen3:latest")
@click.option("--output", "-o", required=True, type=click.Path(), help="Output CSV path")
@click.option("--concurrency", default=1, show_default=True, help="Max concurrent model calls")
@click.option("--bhce-url", default=None, help="BH CE base URL (overrides BLOODHOUND_DOMAIN env var)")
@click.option("--mcp-dir", default="../bloodhound-mcp", type=click.Path(exists=True), show_default=True, help="Path to local bloodhound-mcp repo")
@click.option("--max-steps", default=12, show_default=True, help="Max agent/tool steps")
@click.option(
    "--ollama-option",
    "ollama_options_raw",
    multiple=True,
    help="Repeatable Ollama option in KEY=VALUE form, e.g. --ollama-option num_ctx=16384",
)
def eval_mcp(
    manifest: str,
    model: str,
    output: str,
    concurrency: int,
    bhce_url: str | None,
    mcp_dir: str,
    max_steps: int,
    ollama_options_raw: tuple[str, ...],
) -> None:
    """Run MCP-mode evaluation using BloodHound MCP tools."""
    import asyncio
    from .eval.runner import run_eval_mcp_cli

    asyncio.run(run_eval_mcp_cli(
        manifest_path=Path(manifest),
        model=model,
        output_path=Path(output),
        concurrency=concurrency,
        bhce_url=bhce_url,
        mcp_dir=Path(mcp_dir),
        max_steps=max_steps,
        ollama_options=_parse_ollama_options(ollama_options_raw),
    ))


@main.command()
@click.option("--bhce-url", default=None, help="BH CE base URL (overrides BLOODHOUND_DOMAIN env var)")
@click.option("--timeout", default=60.0, show_default=True, type=float, help="Seconds to wait for BHCE to become healthy")
@click.option("--poll-interval", default=5.0, show_default=True, type=float, help="Seconds between health probes")
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
@click.option("--manifest", "-m", required=True, type=click.Path(exists=True), help="Path to manifest.json")
@click.option("--bhce-url", default=None, help="BH CE base URL (overrides BLOODHOUND_DOMAIN env var)")
def verify_ingest(manifest: str, bhce_url: str | None) -> None:
    """Verify that uploaded BloodHound data matches the generated manifest."""
    import asyncio

    from .eval.ops import print_verify_ingest, verify_ingest

    result = asyncio.run(verify_ingest(Path(manifest), bhce_url=bhce_url))
    print_verify_ingest(result)
    if not result.ok:
        raise SystemExit(1)


@main.command()
@click.option("--manifest", "-m", required=True, type=click.Path(exists=True), help="Path to manifest.json")
@click.option("--output-dir", "-o", required=True, type=click.Path(), help="Directory for smoke-test CSVs")
@click.option("--bhce-url", default=None, help="BH CE base URL (overrides BLOODHOUND_DOMAIN env var)")
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
@click.option("--manifest", "-m", required=True, type=click.Path(exists=True), help="Path to manifest.json")
@click.option("--output-dir", "-o", required=True, type=click.Path(), help="Directory for MCP smoke-test CSVs")
@click.option("--bhce-url", default=None, help="BH CE base URL (overrides BLOODHOUND_DOMAIN env var)")
@click.option("--mcp-dir", default="../bloodhound-mcp", type=click.Path(), show_default=True, help="Path to local bloodhound-mcp repo")
@click.option("--max-steps", default=12, show_default=True, help="Max agent/tool steps")
def smoke_mcp(manifest: str, output_dir: str, bhce_url: str | None, mcp_dir: str, max_steps: int) -> None:
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
        )
    )
    print_smoke_eval(result)
    if not result.ok:
        raise SystemExit(1)


@main.command()
@click.option("--manifest", "-m", required=True, type=click.Path(exists=True), help="Path to manifest.json")
@click.option("--output-dir", "-o", required=True, type=click.Path(), help="Directory for smoke-test CSVs")
@click.option("--bhce-url", default=None, help="BH CE base URL (overrides BLOODHOUND_DOMAIN env var)")
@click.option("--timeout", default=60.0, show_default=True, type=float, help="Seconds to wait for BHCE to become healthy")
@click.option("--poll-interval", default=5.0, show_default=True, type=float, help="Seconds between health probes")
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
@click.option("--manifest", "-m", required=True, type=click.Path(exists=True), help="Path to manifest.json")
@click.option("--model", "models", multiple=True, help="Model to evaluate (repeat for multiple)")
@click.option("--models-file", type=click.Path(exists=True), default=None, help="YAML file listing models to evaluate")
@click.option("--output-dir", "-o", required=True, type=click.Path(), help="Directory for per-model CSV files")
@click.option("--concurrency", default=None, type=int, help="Override concurrency for all models")
@click.option("--bhce-url", default=None, help="BH CE base URL (overrides BLOODHOUND_DOMAIN env var)")
def baseline(
    manifest: str,
    models: tuple[str, ...],
    models_file: str | None,
    output_dir: str,
    concurrency: int | None,
    bhce_url: str | None,
) -> None:
    """Evaluate multiple models against the same manifest and print a comparison table.

    Models can be specified via --model (repeatable), --models-file models.yaml, or both.
    """
    import asyncio
    import yaml
    from .eval.runner import run_eval_cli_bare
    from .eval.report import print_comparison, write_combined_csv, write_summary_csv

    # Build model list: [(model_string, concurrency, ollama_options), ...]
    model_entries: list[tuple[str, int, dict | None]] = []

    if models_file:
        with open(models_file) as f:
            cfg = yaml.safe_load(f)
        default_concurrency = cfg.get("defaults", {}).get("concurrency", 1)
        for entry in cfg.get("models", []):
            if isinstance(entry, str):
                model_entries.append((entry, default_concurrency, None))
            elif isinstance(entry, dict):
                opts = entry.get("options") or {}
                # Allow top-level num_ctx as shorthand for options.num_ctx
                if "num_ctx" in entry:
                    opts["num_ctx"] = entry["num_ctx"]
                model_entries.append((
                    entry["model"],
                    entry.get("concurrency", default_concurrency),
                    opts or None,
                ))

    for m in models:
        model_entries.append((m, 1, None))

    if not model_entries:
        raise click.UsageError("Provide at least one model via --model or --models-file.")

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Run all models in a single event loop to avoid httpx cleanup errors
    # that occur when asyncio.run() is called multiple times (closes loop between runs)
    async def _run_all() -> dict:
        results = {}
        for i, (model, model_concurrency, model_options) in enumerate(model_entries, 1):
            effective_concurrency = concurrency if concurrency is not None else model_concurrency
            slug = model.replace("/", "_").replace(":", "-")
            csv_path = out / f"{slug}.csv"
            opts_str = f", options={model_options}" if model_options else ""
            click.echo(f"\n[{i}/{len(model_entries)}] {model}  (concurrency={effective_concurrency}{opts_str})")
            results[model] = await run_eval_cli_bare(
                manifest_path=Path(manifest),
                model=model,
                output_path=csv_path,
                concurrency=effective_concurrency,
                bhce_url=bhce_url,
                ollama_options=model_options,
            )
        return results

    all_results = asyncio.run(_run_all())

    combined_csv_path = out / "baseline_combined.csv"
    summary_csv_path = out / "baseline_summary.csv"
    write_combined_csv(all_results, combined_csv_path)
    write_summary_csv(all_results, summary_csv_path)
    print_comparison(all_results)
    click.echo(f"\nPer-model CSVs written to {output_dir}/")
    click.echo(f"Combined CSV written to {combined_csv_path}")
    click.echo(f"Summary CSV written to {summary_csv_path}")


@main.command(name="baseline-mcp")
@click.option("--manifest", "-m", required=True, type=click.Path(exists=True), help="Path to manifest.json")
@click.option("--model", "models", multiple=True, help="Model to evaluate (repeat for multiple)")
@click.option("--models-file", type=click.Path(exists=True), default=None, help="YAML file listing models to evaluate")
@click.option("--output-dir", "-o", required=True, type=click.Path(), help="Directory for per-model CSV files")
@click.option("--concurrency", default=None, type=int, help="Override concurrency for all models")
@click.option("--bhce-url", default=None, help="BH CE base URL (overrides BLOODHOUND_DOMAIN env var)")
@click.option("--mcp-dir", default="../bloodhound-mcp", type=click.Path(exists=True), show_default=True, help="Path to local bloodhound-mcp repo")
@click.option("--max-steps", default=12, show_default=True, help="Max agent/tool steps")
def baseline_mcp(
    manifest: str,
    models: tuple[str, ...],
    models_file: str | None,
    output_dir: str,
    concurrency: int | None,
    bhce_url: str | None,
    mcp_dir: str,
    max_steps: int,
) -> None:
    """Evaluate multiple models in MCP mode and print a comparison table."""
    import asyncio
    from .eval.runner import run_eval_mcp_cli_bare
    from .eval.report import print_comparison, write_combined_csv, write_summary_csv

    model_entries: list[tuple[str, int, dict | None]] = []

    if models_file:
        with open(models_file) as f:
            cfg = yaml.safe_load(f)
        default_concurrency = cfg.get("defaults", {}).get("concurrency", 1)
        for entry in cfg.get("models", []):
            if isinstance(entry, str):
                model_entries.append((entry, default_concurrency, None))
            elif isinstance(entry, dict):
                opts = entry.get("options") or {}
                if "num_ctx" in entry:
                    opts["num_ctx"] = entry["num_ctx"]
                model_entries.append((
                    entry["model"],
                    entry.get("concurrency", default_concurrency),
                    opts or None,
                ))

    for m in models:
        model_entries.append((m, 1, None))

    if not model_entries:
        raise click.UsageError("Provide at least one model via --model or --models-file.")

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    async def _run_all() -> dict:
        results = {}
        for i, (model, model_concurrency, model_options) in enumerate(model_entries, 1):
            effective_concurrency = concurrency if concurrency is not None else model_concurrency
            slug = model.replace("/", "_").replace(":", "-")
            csv_path = out / f"{slug}.csv"
            opts_str = f", options={model_options}" if model_options else ""
            click.echo(f"\n[{i}/{len(model_entries)}] {model}  (concurrency={effective_concurrency}{opts_str}, max_steps={max_steps})")
            results[model] = await run_eval_mcp_cli_bare(
                manifest_path=Path(manifest),
                model=model,
                output_path=csv_path,
                concurrency=effective_concurrency,
                bhce_url=bhce_url,
                ollama_options=model_options,
                mcp_dir=Path(mcp_dir),
                max_steps=max_steps,
            )
        return results

    all_results = asyncio.run(_run_all())

    combined_csv_path = out / "baseline_combined.csv"
    summary_csv_path = out / "baseline_summary.csv"
    write_combined_csv(all_results, combined_csv_path)
    write_summary_csv(all_results, summary_csv_path)
    print_comparison(all_results)
    click.echo(f"\nPer-model CSVs written to {output_dir}/")
    click.echo(f"Combined CSV written to {combined_csv_path}")
    click.echo(f"Summary CSV written to {summary_csv_path}")


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
                "source_name": (graph.get_node(p.source_node).properties.get("name", "") if graph.get_node(p.source_node) else ""),
                "target_node": p.target_node,
                "target_name": (graph.get_node(p.target_node).properties.get("name", "") if graph.get_node(p.target_node) else ""),
                "path_edges": [
                    {"source": src, "edge": edge, "target": tgt}
                    for src, edge, tgt in p.path_edges
                ],
                "verification_cypher": p.verification_cypher,
                "mitre": p.mitre,
            }
            for p in graph.planted_paths
        ],
    }
