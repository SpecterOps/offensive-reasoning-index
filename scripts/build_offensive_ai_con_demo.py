#!/usr/bin/env python3
# ruff: noqa: E501
"""Build the self-contained Offensive AI Con offline demo from public evidence."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path
from string import Template
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "docs/assets/offensive-ai-con/offline-demo"
DIRECT_EVIDENCE = (
    REPO_ROOT / "docs/assets/offensive-ai-con/historical-v29-direct-evidence.json"
)
DIRECT_CHART = REPO_ROOT / "docs/assets/offensive-ai-con/historical-v29-direct-results.svg"
PROVIDER_EVIDENCE = REPO_ROOT / "docs/evidence/provider-hardening-v13-acceptance.json"


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _validate_direct_evidence(evidence: dict[str, Any]) -> None:
    _require(
        evidence.get("schema_version") == "ori-offensive-ai-con-direct-evidence-v1",
        "unexpected Direct evidence schema",
    )
    _require(evidence.get("track") == "direct", "demo evidence must be Direct-only")
    _require(evidence.get("runner_clean") is True, "historical runner must be clean")
    _require(evidence.get("model_count") == 6, "expected six historical models")
    _require(evidence.get("runs_per_model") == 5, "expected five runs per model")
    _require(evidence.get("scheduled_samples") == 1260, "expected 1,260 samples")
    _require(evidence.get("infrastructure_failures") == 0, "unexpected infra failure")
    _require(evidence.get("harness_failures") == 0, "unexpected harness failure")
    _require(evidence.get("unexecuted") == 0, "unexpected unexecuted sample")
    models = evidence.get("models")
    _require(isinstance(models, list) and len(models) == 6, "invalid model evidence")
    _require(
        sum(int(model["scheduled"]) for model in models) == 1260,
        "model denominators do not sum to 1,260",
    )


def _validate_provider_evidence(evidence: dict[str, Any]) -> None:
    _require(evidence.get("schema_version") == 2, "unexpected provider evidence schema")
    _require(evidence.get("live_certification") == "PASS", "live certification did not pass")
    readiness = evidence.get("full_v29_readiness")
    _require(isinstance(readiness, dict), "missing full V29 readiness")
    _require(readiness.get("status") == "PASS", "full V29 readiness did not pass")
    _require(readiness.get("direct_tasks") == 42, "unexpected Direct release size")
    _require(readiness.get("mcp_tasks") == 55, "unexpected MCP release size")
    _require(readiness.get("provider_calls") == 0, "readiness made provider calls")
    canaries = evidence.get("canaries")
    _require(isinstance(canaries, list) and len(canaries) == 2, "expected two canaries")
    _require({canary.get("track") for canary in canaries} == {"direct", "mcp"}, "bad tracks")
    for canary in canaries:
        _require(canary.get("campaign_valid") is True, "canary campaign was invalid")
        _require(canary.get("infrastructure_failures") == 0, "canary infra failure")
        _require(canary.get("harness_failures") == 0, "canary harness failure")
        _require(canary.get("outcome") == "OUTPUT_INVALID", "unexpected canary outcome")
    redaction = evidence.get("redaction")
    _require(isinstance(redaction, dict), "missing redaction declaration")
    for field in (
        "prompts_included",
        "provider_responses_included",
        "api_keys_included",
        "bloodhound_credentials_included",
    ):
        _require(redaction.get(field) is False, f"unsafe provider evidence field: {field}")


def _clean_svg(svg: str) -> str:
    _require("<script" not in svg.lower(), "chart SVG must not contain scripts")
    return svg.replace('<?xml version="1.0" encoding="UTF-8"?>', "").strip()


def _source(path: Path) -> dict[str, str]:
    return {"path": path.relative_to(REPO_ROOT).as_posix(), "sha256": _sha256(path)}


def _render_html(
    direct: dict[str, Any],
    provider: dict[str, Any],
    chart_svg: str,
) -> str:
    graph = html.escape(str(provider["graph_fingerprint"]))
    runner = html.escape(str(direct["runner_commit"]))
    canaries = {item["track"]: item for item in provider["canaries"]}
    direct_canary = canaries["direct"]
    mcp_canary = canaries["mcp"]
    template = Template(
        """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="Offline evidence walkthrough for the Offensive Reasoning Index benchmark.">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='10' fill='%231764c0'/%3E%3Ctext x='32' y='42' text-anchor='middle' font-family='Arial' font-size='28' font-weight='700' fill='white'%3EORI%3C/text%3E%3C/svg%3E">
<title>ORI Offensive AI Con Offline Demo</title>
<style>
:root{--ink:#111;--muted:#62676f;--rule:#b8bcc4;--panel:#f2f2f2;--blue:#1764c0;--blue2:#d0edfa;--red:#c23b22;--green:#2a7a52}
*{box-sizing:border-box}html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#fff;color:var(--ink);font-family:"Helvetica Neue",Arial,sans-serif}
.stage{position:relative;width:1280px;height:720px;margin:0 auto;background:#fff}.beat{display:none;position:absolute;inset:0;padding:30px 54px 48px}.beat.active{display:block}
.eyebrow{font-size:14px;font-weight:700;color:var(--blue);letter-spacing:.02em}.title{font-size:42px;line-height:1.05;margin:34px 0 0;font-weight:700}.rule{height:1px;background:var(--rule);margin-top:28px}.footer{position:absolute;left:54px;right:54px;bottom:20px;display:flex;justify-content:space-between;gap:28px;font-size:13px;color:var(--muted)}.footer span:first-child{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.footer span:last-child{flex:none}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:44px;margin-top:48px}.panel{background:var(--panel);padding:26px 30px}.panel h2,.plain h2{font-size:26px;margin:0 0 18px}.panel p,.plain p{font-size:20px;line-height:1.38;margin:0;color:var(--muted)}
.flow{display:flex;align-items:center;gap:12px;margin-top:58px}.step{flex:1;min-height:120px;border:1px solid var(--rule);background:var(--panel);display:flex;align-items:center;justify-content:center;text-align:center;padding:15px;font-size:20px;font-weight:700}.step.stop{border-color:var(--blue);background:var(--blue2)}.arrow{font-size:27px;color:var(--muted)}
.finger{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:18px;word-break:break-all}.callout{margin-top:34px;text-align:center;font-size:27px;font-weight:700}.red{color:var(--red)}.green{color:var(--green)}.blue{color:var(--blue)}
.checks{display:grid;grid-template-columns:repeat(5,1fr);gap:18px;margin-top:54px}.check{padding:22px 18px;border-top:5px solid var(--blue);background:var(--panel);min-height:150px}.check strong{display:block;font-size:22px;margin-bottom:14px}.check span{font-size:18px;line-height:1.35;color:var(--muted)}
.metricline{display:flex;justify-content:center;gap:90px;margin-top:46px}.metric{text-align:center}.metric b{display:block;font-size:60px}.metric span{font-size:19px;color:var(--muted)}
table{width:100%;border-collapse:collapse;margin-top:40px;font-size:19px}th{text-align:left;color:var(--blue);font-size:15px;text-transform:uppercase;letter-spacing:.04em}th,td{padding:16px 14px;border-bottom:1px solid var(--rule)}td:first-child{font-weight:700}.pill{display:inline-block;padding:5px 10px;background:var(--blue2);font-weight:700}.pill.redpill{background:#fff1ee;color:var(--red)}
.claimflow{display:grid;grid-template-columns:210px 320px 260px;grid-template-rows:150px 150px;gap:45px 80px;align-items:center;justify-content:center;margin-top:44px}.box{border:2px solid var(--ink);padding:24px;font-size:23px;font-weight:700}.typed{grid-row:1/3;background:var(--blue2);border-color:var(--blue);text-align:center}.public{border-color:var(--blue)}.sealed{background:var(--panel)}.evidence{grid-row:1/3;grid-column:3;border-color:var(--green);text-align:center}.label{font-size:14px;color:var(--blue);text-transform:uppercase;margin-bottom:12px}.sealed .label{color:var(--red)}
.incident{display:grid;grid-template-columns:320px 80px 1fr;align-items:center;gap:20px;padding:17px 8px;border-bottom:1px solid var(--rule);font-size:20px}.incident b{font-size:21px}.incident .to{text-align:center;color:var(--rule);font-size:28px}.incident span{color:var(--muted)}
.canaryline{display:flex;gap:24px;margin-top:26px}.canary{flex:1;border-left:5px solid var(--green);padding:12px 18px;background:#f5faf7;font-size:18px}.canary b{display:block;font-size:21px;margin-bottom:8px}.chart{position:absolute;left:0;top:0;width:1280px;height:720px}.chart svg{display:block;width:1280px;height:720px}.navhint{position:absolute;right:54px;top:32px;color:var(--muted);font-size:13px}
</style>
</head>
<body>
<main class="stage">
<div class="navhint">← → or 1–6</div>
<section class="beat" data-beat="1">
  <div class="eyebrow">ORI · OFFLINE CONFERENCE DEMO</div><h1 class="title">Graph truth comes before model spend</h1><div class="rule"></div>
  <div class="flow"><div class="step">Manifest + archive</div><div class="arrow">→</div><div class="step">Archive validation</div><div class="arrow">→</div><div class="step">Live ingest verification</div><div class="arrow">→</div><div class="step stop">Compile + certify</div></div>
  <div class="grid2"><div class="panel"><h2>Certified development graph</h2><p class="finger">$graph</p></div><div class="panel"><h2>Why the gate matters</h2><p>A healthy BloodHound API does not prove that the loaded graph matches the benchmark artifact. A mismatch stops readiness.</p></div></div>
  <div class="callout">Health is not ingest. Certification is not execution.</div>
  <div class="footer"><span>Source: provider-hardening-v13-acceptance.json; benchmark-v2-certification-evidence.md</span><span>1 / 6</span></div>
</section>
<section class="beat" data-beat="2">
  <div class="eyebrow">ORI · OFFLINE CONFERENCE DEMO</div><h1 class="title">No-model readiness proves the boundary without spending tokens</h1><div class="rule"></div>
  <div class="checks"><div class="check"><strong>Artifacts</strong><span>Candidate catalogs, sealed oracles, archive, and manifest agree.</span></div><div class="check"><strong>Runtime</strong><span>Runner, provider surface, structured-output mode, and launcher are fingerprinted.</span></div><div class="check"><strong>Capability</strong><span>MCP revision and tool loop match the certified capability.</span></div><div class="check"><strong>Graph</strong><span>The exact live graph fingerprint matches certification.</span></div><div class="check"><strong>Credentials</strong><span>Required source name is present; secret material is never recorded.</span></div></div>
  <div class="metricline"><div class="metric"><b>42</b><span>Direct tasks</span></div><div class="metric"><b>55</b><span>MCP tasks</span></div><div class="metric"><b class="green">0</b><span>provider calls</span></div></div>
  <div class="footer"><span>Redacted V13 readiness: PASS</span><span>2 / 6</span></div>
</section>
<section class="beat" data-beat="3">
  <div class="eyebrow">ORI · OFFLINE CONFERENCE DEMO</div><h1 class="title">The monitor observes ORI; it does not invent campaign state</h1><div class="rule"></div>
  <table><thead><tr><th>Observed state</th><th>Resume?</th><th>Typed next action</th><th>Supervisor behavior</th></tr></thead><tbody>
  <tr><td>readiness_complete</td><td><span class="pill">operator gate</span></td><td>execute_campaign</td><td>Stop for paid-run approval.</td></tr>
  <tr><td>running</td><td>No</td><td>monitor</td><td>Never launch a second process.</td></tr>
  <tr><td>stale_running · readiness</td><td>Yes</td><td>run_readiness</td><td>Rerun without <code>--execute</code>.</td></tr>
  <tr><td>stale_running · execution</td><td>Yes</td><td>resume_campaign</td><td>Use the exact original execute command.</td></tr>
  <tr><td>completed · invalid</td><td><span class="pill redpill">No</span></td><td>campaign_complete_invalid</td><td>Preserve evidence and alert.</td></tr>
  </tbody></table>
  <div class="callout">Status is read-only: no provider, MCP, BloodHound, graph, or state writes.</div>
  <div class="footer"><span>Source: v2-campaign-supervisor-contract.md; campaign_status.py</span><span>3 / 6</span></div>
</section>
<section class="beat" data-beat="4">
  <div class="eyebrow">ORI · OFFLINE CONFERENCE DEMO</div><h1 class="title">One typed claim creates public and sealed evidence boundaries</h1><div class="rule"></div>
  <div class="claimflow"><div class="box typed">Typed<br>claim</div><div class="box public"><div class="label">Solver-visible</div>Question + AcceptanceSpec + answer schema</div><div class="box sealed"><div class="label">Sealed</div>Oracle + expected graph facts + comparator policy</div><div class="box evidence">Evidence IR<br>+ comparator</div></div>
  <div class="callout">A hidden rule that cannot be derived from the public contract blocks compilation.</div>
  <div class="footer"><span>Source: benchmark-v2-design-rationale.md; benchmark-v2-task-authoring.md</span><span>4 / 6</span></div>
</section>
<section class="beat" data-beat="5">
  <div class="eyebrow">ORI · OFFLINE CONFERENCE DEMO</div><h1 class="title">Failures become typed outcomes—not tracebacks or contaminated scores</h1><div class="rule"></div>
  <div style="margin-top:32px"><div class="incident"><b>Nullable provider content</b><div class="to">→</div><span>Typed text, refusal, reasoning, and tool-call items; text is never nullable.</span></div><div class="incident"><b>Malformed envelope</b><div class="to">→</div><span>Non-retryable provider-protocol outcome.</span></div><div class="incident"><b>Refusal / truncation / empty final</b><div class="to">→</div><span>Model or output outcome, not HARNESS_ERROR.</span></div><div class="incident"><b>Open BloodHound circuit</b><div class="to">→</div><span>Zero-token CIRCUIT_OPEN result before a provider or tool call.</span></div></div>
  <div class="canaryline"><div class="canary"><b>Nous Direct interoperability</b>$direct_outcome · $direct_in / $direct_out tokens · 0 infra · 0 harness</div><div class="canary"><b>Nous MCP interoperability</b>$mcp_outcome · 1 tool call · 0 infra · 0 harness</div></div>
  <div class="footer"><span>Pre-final-review V13 canaries: interoperability only; rerun required after the signed correction.</span><span>5 / 6</span></div>
</section>
<section class="beat" data-beat="6">
  <div class="chart">$chart_svg</div>
  <div class="footer"><span>Frozen campaign · runner $runner · 30 valid runs · zero infra/harness/unexecuted</span><span>6 / 6</span></div>
</section>
</main>
<script>
const beats=[...document.querySelectorAll('.beat')];let active=Math.min(6,Math.max(1,Number(new URLSearchParams(location.search).get('beat')||1)));
function show(value){active=Math.min(6,Math.max(1,value));beats.forEach((node,index)=>node.classList.toggle('active',index===active-1));history.replaceState(null,'',`?beat=$${active}`)}
addEventListener('keydown',event=>{if(event.key==='ArrowRight'||event.key===' '){show(active+1)}else if(event.key==='ArrowLeft'){show(active-1)}else if(/^[1-6]$$/.test(event.key)){show(Number(event.key))}});show(active);
</script>
</body>
</html>
"""
    )
    return template.substitute(
        graph=graph,
        runner=runner[:12],
        chart_svg=chart_svg,
        direct_outcome=html.escape(str(direct_canary["outcome"])),
        direct_in=int(direct_canary["tokens_input"]),
        direct_out=int(direct_canary["tokens_output"]),
        mcp_outcome=html.escape(str(mcp_canary["outcome"])),
    )


def build(output_dir: Path) -> tuple[Path, Path]:
    direct = _load_json(DIRECT_EVIDENCE)
    provider = _load_json(PROVIDER_EVIDENCE)
    _validate_direct_evidence(direct)
    _validate_provider_evidence(provider)
    chart_svg = _clean_svg(DIRECT_CHART.read_text(encoding="utf-8"))

    output_dir.mkdir(parents=True, exist_ok=True)
    index_path = output_dir / "index.html"
    evidence_path = output_dir / "demo-evidence.json"
    index_path.write_text(_render_html(direct, provider, chart_svg), encoding="utf-8")

    manifest = {
        "schema_version": "ori-offensive-ai-con-offline-demo-v1",
        "beat_count": 6,
        "offline_only": True,
        "provider_calls": 0,
        "graph_fingerprint": provider["graph_fingerprint"],
        "v29_release": provider["full_v29_readiness"],
        "historical_direct": {
            "campaign_id": direct["campaign_id"],
            "runner_commit": direct["runner_commit"],
            "runs_per_model": direct["runs_per_model"],
            "model_count": direct["model_count"],
            "scheduled_samples": direct["scheduled_samples"],
            "infrastructure_failures": direct["infrastructure_failures"],
            "harness_failures": direct["harness_failures"],
            "unexecuted": direct["unexecuted"],
            "warnings": direct["warnings"],
        },
        "nous_interoperability_canaries": provider["canaries"],
        "redaction": provider["redaction"],
        "source_artifacts": [
            _source(DIRECT_EVIDENCE),
            _source(DIRECT_CHART),
            _source(PROVIDER_EVIDENCE),
        ],
        "generated_artifacts": {
            "index.html": _sha256(index_path),
        },
        "claim_boundary": [
            "Historical Direct development evidence; not current Release 1 qualification.",
            "Nous canaries prove interoperability only; both final outputs were OUTPUT_INVALID.",
            "Nous canaries predate the final fail-closed endpoint correction and must be rerun.",
            "OpenRouter live qualification is absent from this demo.",
            "No historical MCP ranking is presented.",
            "V29 is not the future official 100 Direct / 100 MCP suite.",
        ],
    }
    evidence_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return index_path, evidence_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    index_path, evidence_path = build(args.output_dir.resolve())
    print(index_path)
    print(evidence_path)


if __name__ == "__main__":
    main()
