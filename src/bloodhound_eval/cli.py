"""CLI entry point for bloodhound-eval."""

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


@click.group()
def main() -> None:
    """bloodhound-eval: benchmark for evaluating AI on AD attack path analysis."""


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
                "target_node": p.target_node,
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
