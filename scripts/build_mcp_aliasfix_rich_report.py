#!/usr/bin/env python3
"""Build a private historical Phase 4B/v2 alias-fix report.

This helper includes raw answers and diagnostic data. It is not a certified
public export and retains the historical campaign-specific narrative.
"""
# ruff: noqa: E501,E701,E702,F401,F541,F841

from __future__ import annotations

import argparse
import csv
import html
import json
import math
import shutil
from collections import Counter, defaultdict
from pathlib import Path

PROFILES = [
    ('GPT-5.4 Cyber MCP', 'codex_gpt_5_4_cyber_phase4b_v2_mcp', 42),
    ('GPT-5.5 MCP', 'codex_gpt_5_5_phase4b_v2_mcp', 65),
    ('GPT-5.5 Cyber Preview MCP', 'codex_gpt_5_5_cyber_preview_phase4b_v2_mcp', 61),
    ('Waluigi Safety Alpha MCP', 'codex_waluigi_safety_alpha_phase4b_v2_mcp', 65),
]

def esc(x):
    if x is None:
        return ''
    return html.escape(str(x), quote=True)

def pct(a,b):
    try:
        a=float(a); b=float(b)
        return f"{(a/b*100):.1f}%" if b else 'n/a'
    except Exception:
        return 'n/a'

def read_csv(path):
    with open(path, newline='') as f:
        return list(csv.DictReader(f))

def parse_jsonish(s):
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        return None

def pretty_json(s):
    obj=parse_jsonish(s)
    if obj is None:
        return esc(s)
    return esc(json.dumps(obj, indent=2, ensure_ascii=False))

def slug(s):
    return ''.join(c if c.isalnum() or c in '-_' else '-' for c in s).strip('-')

def load_profile(run_root, label, profile, baseline):
    out = run_root / 'outputs' / profile
    summary = read_csv(out / 'baseline_summary.csv')[0]
    rows = read_csv(out / 'baseline_combined.csv')
    return {'label': label, 'profile': profile, 'baseline': baseline, 'summary': summary, 'rows': rows}

def page(title, body, active=''):
    nav = f"""
    <nav>
      <a href="index.html">Index</a>
      <a href="executive.html">Executive Summary</a>
      <a href="model-gpt-5-4-cyber-mcp.html">GPT-5.4 Cyber</a>
      <a href="model-gpt-5-5-mcp.html">GPT-5.5</a>
      <a href="model-gpt-5-5-cyber-preview-mcp.html">GPT-5.5 Cyber Preview</a>
      <a href="model-waluigi-safety-alpha-mcp.html">Waluigi</a>
      <a href="tasks.html">All Tasks</a>
      <a href="hallucinations.html">Hallucinations</a>
      <a href="failures.html">Failures</a>
      <a href="data/">Data</a>
    </nav>
    """
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title><link rel="stylesheet" href="assets/report.css"></head>
<body>{nav}<main><p class='callout'>Private historical diagnostic report — not a certified public export. Contains raw answers and source data.</p>{body}</main></body></html>"""

def badge(outcome):
    cls = {'CORRECT':'ok','INCORRECT':'bad','HALLUCINATION':'warn'}.get(outcome,'muted')
    return f'<span class="badge {cls}">{esc(outcome)}</span>'

def model_filename(label):
    return 'model-' + slug(label.lower().replace('gpt-5.4','gpt-5-4').replace('gpt-5.5','gpt-5-5').replace(' ', '-')).replace('--','-') + '.html'

def row_anchor(row):
    return slug(row.get('task_id','task'))

def metric_card(name, value, sub=''):
    return f'<div class="card"><div class="metric">{esc(value)}</div><div class="label">{esc(name)}</div><p>{esc(sub)}</p></div>'

CSS = r'''
:root{--bg:#0b0f14;--panel:#111821;--panel2:#0f151d;--text:#e8eef6;--muted:#9fb0c3;--line:#263241;--ok:#54d98c;--bad:#ff6b6b;--warn:#ffd166;--link:#8cc8ff;--accent:#b392ff}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif}nav{position:sticky;top:0;z-index:5;background:#070a0edd;border-bottom:1px solid var(--line);backdrop-filter:blur(8px);padding:10px 22px}nav a{color:var(--link);text-decoration:none;margin-right:18px;font-weight:600}main{max-width:1320px;margin:0 auto;padding:28px}h1{font-size:34px;line-height:1.1;margin:0 0 10px}h2{margin-top:34px;border-top:1px solid var(--line);padding-top:22px}h3{margin-top:22px}.lede{font-size:18px;color:#d7e3f2;max-width:980px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:14px;margin:18px 0}.card{background:linear-gradient(180deg,var(--panel),var(--panel2));border:1px solid var(--line);border-radius:14px;padding:16px}.metric{font-size:31px;font-weight:800}.label{color:var(--muted);font-weight:700;text-transform:uppercase;font-size:12px;letter-spacing:.06em}.card p{color:var(--muted);margin:.4em 0 0}table{border-collapse:collapse;width:100%;margin:16px 0;background:var(--panel2);border:1px solid var(--line);border-radius:12px;overflow:hidden}th,td{padding:9px 10px;border-bottom:1px solid var(--line);vertical-align:top}th{text-align:left;color:#c8d7e8;background:#15202c;position:sticky;top:43px}tr:hover td{background:#121e2a}.badge{display:inline-block;border-radius:999px;padding:2px 8px;font-weight:800;font-size:12px}.ok{background:#103b25;color:var(--ok)}.bad{background:#401b21;color:var(--bad)}.warn{background:#443819;color:var(--warn)}.muted{background:#263241;color:var(--muted)}a{color:var(--link)}code,pre{background:#071019;border:1px solid var(--line);border-radius:10px}code{padding:1px 5px}pre{padding:12px;overflow:auto;white-space:pre-wrap}.two{display:grid;grid-template-columns:1fr 1fr;gap:16px}.three{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}.small{font-size:13px;color:var(--muted)}details{background:#0d141c;border:1px solid var(--line);border-radius:12px;padding:10px;margin:10px 0}summary{cursor:pointer;font-weight:800}.filters{display:flex;gap:10px;flex-wrap:wrap;margin:12px 0}.filters input,.filters select{background:#071019;color:var(--text);border:1px solid var(--line);border-radius:8px;padding:8px}.bar{height:8px;background:#263241;border-radius:999px;overflow:hidden}.fill{height:100%;background:linear-gradient(90deg,var(--accent),var(--ok))}.callout{border-left:4px solid var(--accent);background:#111827;padding:14px;border-radius:8px;margin:18px 0}.badtext{color:var(--bad)}.oktext{color:var(--ok)}.warntext{color:var(--warn)}@media(max-width:850px){.two,.three{grid-template-columns:1fr}main{padding:18px}th{position:static}}
'''
JS = r'''
function filterTasks(tableId){const q=document.getElementById(tableId+'-q').value.toLowerCase();const outcome=document.getElementById(tableId+'-outcome').value;const tier=document.getElementById(tableId+'-tier').value;document.querySelectorAll('#'+tableId+' tbody tr').forEach(tr=>{const txt=tr.innerText.toLowerCase();const okQ=!q||txt.includes(q);const okO=!outcome||tr.dataset.outcome===outcome;const okT=!tier||tr.dataset.tier===tier;tr.style.display=(okQ&&okO&&okT)?'':'none';});}
'''

def build(run_root: Path, output_dir: Path | None = None) -> Path:
    run_root = run_root.resolve()
    output_dir = (output_dir or run_root / 'rich-report').resolve()
    if run_root.is_relative_to(output_dir):
        raise ValueError("Output directory must not contain the source run root")
    # Validate every required source before creating any report files.
    profiles = [load_profile(run_root, *p) for p in PROFILES]
    for name in ('current-status.json', 'status-events.jsonl'):
        if not (run_root / name).is_file():
            raise FileNotFoundError(run_root / name)
    if output_dir.exists():
        raise FileExistsError(f"Output directory already exists: {output_dir}")
    (output_dir/'assets').mkdir(parents=True)
    (output_dir/'models').mkdir()
    (output_dir/'tasks').mkdir()
    (output_dir/'data').mkdir()
    (output_dir/'assets'/'report.css').write_text(CSS)
    (output_dir/'assets'/'report.js').write_text(JS)
    shutil.copy2(run_root/'current-status.json', output_dir/'data'/'current-status.json')
    shutil.copy2(run_root/'status-events.jsonl', output_dir/'data'/'status-events.jsonl')
    for p in profiles:
        src=run_root/'outputs'/p['profile']/'baseline_combined.csv'
        shutil.copy2(src, output_dir/'data'/f"{p['profile']}-baseline_combined.csv")
        shutil.copy2(run_root/'outputs'/p['profile']/'baseline_summary.csv', output_dir/'data'/f"{p['profile']}-baseline_summary.csv")
    # Index
    cards=''.join(metric_card(p['label'], f"{p['summary']['correct']}/100", f"Δ +{int(p['summary']['correct'])-p['baseline']} vs prior MCP baseline") for p in profiles)
    rows=''.join(f"<tr><td><a href='{esc(model_filename(p['label']))}'>{esc(p['label'])}</a></td><td>{p['summary']['correct']}/100</td><td>{p['baseline']}/100</td><td class='oktext'>+{int(p['summary']['correct'])-p['baseline']}</td><td>{p['summary']['tier1_correct']}/{p['summary']['tier1_total']}</td><td>{p['summary']['tier3_correct']}/{p['summary']['tier3_total']}</td><td>{p['summary']['tier4_correct']}/{p['summary']['tier4_total']}</td><td>{p['summary']['tier5_correct']}/{p['summary']['tier5_total']}</td><td>{p['summary']['avg_tool_calls']}</td><td>{p['summary']['hallucinations']}</td></tr>" for p in profiles)
    body=f"""<h1>ORI Phase 4B/v2 MCP Alias-Fix Report</h1><p class='lede'>A clean MCP-only rerun after alias-equivalence scoring was fixed. This report is designed for drilldown: click a model to inspect every task, answer, tool/resource usage, and failure mode.</p><div class='grid'>{cards}</div><div class='callout'><b>Executive read:</b> GPT-5.5 leads at 80/100, Waluigi is essentially tied at 79/100 while using fewer tool calls, GPT-5.5 Cyber Preview lands at 73/100, and GPT-5.4 Cyber trails at 61/100. All four completed cleanly with zero model, infra, parse, loop, or tool-call failures.</div><h2>Model results</h2><table><thead><tr><th>Model</th><th>Score</th><th>Old baseline</th><th>Δ</th><th>T1</th><th>T3</th><th>T4</th><th>T5</th><th>Avg tools</th><th>Hallucs</th></tr></thead><tbody>{rows}</tbody></table><h2>Where to start</h2><ul><li><a href='executive.html'>Executive Summary</a> — narrative readout and interpretation.</li><li>Model pages — full task tables with filters and expandable answers/tool metadata.</li><li><a href='data/'>Data</a> — raw CSV/JSON files copied into the bundle.</li></ul>"""
    (output_dir/'index.html').write_text(page('ORI MCP Alias-Fix Report', body))
    # Executive
    best=max(profiles, key=lambda p:int(p['summary']['correct']))
    efficient=min(profiles, key=lambda p:float(p['summary']['avg_tool_calls']))
    exec_rows=''.join(f"<tr><td>{esc(p['label'])}</td><td>{p['summary']['correct']}/100</td><td>{p['summary']['no_path_reported']}</td><td>{p['summary']['incomplete_answers']}</td><td>{p['summary']['hallucinations']}</td><td>{p['summary']['avg_tool_calls']}</td><td>{p['summary']['cypher_query_calls']}/{p['summary']['non_cypher_tool_calls']}</td></tr>" for p in profiles)
    narrative=f"""<h1>Executive Summary</h1><p class='lede'>This run is reportable: all four MCP profiles completed all 100 tasks with no infra/model/parser/loop/tool failures. The alias-equivalence fix changed the measurement from string-fragile answer matching to graph-object matching, while preserving strictness: wrong objects, missing hops, invalid paths, and hallucinated nodes still fail.</p><div class='grid'>{metric_card('Winner', best['label'], best['summary']['correct']+'/100')}{metric_card('Most efficient', efficient['label'], efficient['summary']['avg_tool_calls']+' avg tool calls')}{metric_card('Ceiling signal', '3 models', 'cleared Tier 5 at 8/8')}{metric_card('Cleanliness', '0', 'model/infra/parse/loop failures')}</div><h2>Interpretation</h2><p>GPT-5.5 is the strongest overall at 80/100. Waluigi is one point back at 79/100 and uses noticeably fewer tools, which makes it interesting as an efficiency candidate. GPT-5.5 Cyber Preview remains strong, especially on Tier 5, but underperforms GPT-5.5 overall. GPT-5.4 Cyber is clearly behind, mostly because it reports no path too often and only gets 3/8 on Tier 5.</p><p>The main remaining model failure mode is not hallucination. It is search/finalization conservatism: <code>NO_PATH_REPORTED</code> and <code>INCOMPLETE_ANSWER</code>. That is a useful signal for future benchmark and harness work.</p><h2>Operational summary</h2><table><thead><tr><th>Model</th><th>Score</th><th>No path</th><th>Incomplete</th><th>Hallucinations</th><th>Avg tool calls</th><th>Cypher/non-Cypher</th></tr></thead><tbody>{exec_rows}</tbody></table><h2>Recommendation</h2><p>Keep Phase 4B/v2 as the calibrated baseline. Add the planned Phase 4C / Tier 6 diagnostic layer to restore ceiling pressure for the top cluster, especially because GPT-5.5, GPT-5.5 Cyber Preview, and Waluigi all cleared Tier 5 at 8/8.</p>"""
    (output_dir/'executive.html').write_text(page('Executive Summary', narrative))
    # Model pages and per-task pages
    for p in profiles:
        summary=p['summary']; rows=p['rows']; fname=model_filename(p['label'])
        outcomes=Counter(r['outcome'] for r in rows)
        tiers=defaultdict(lambda: [0,0])
        for r in rows:
            tiers[r['tier']][1]+=1
            if r['outcome']=='CORRECT': tiers[r['tier']][0]+=1
        tier_html=''.join(f"<tr><td>Tier {esc(t)}</td><td>{c}/{n}</td><td>{pct(c,n)}</td><td><div class='bar'><div class='fill' style='width:{(c/n*100 if n else 0):.1f}%'></div></div></td></tr>" for t,(c,n) in sorted(tiers.items(), key=lambda kv:int(kv[0] or 0)))
        filter_id=slug(p['profile'])
        task_rows=[]
        for r in rows:
            tid=row_anchor(r)
            ans=pretty_json(r.get('final_answer_normalized') or r.get('final_answer_raw'))
            tools=', '.join([x for x in (r.get('unique_tools_used') or '').split('|') if x]) or '—'
            resources=', '.join([x for x in (r.get('unique_resources_used') or '').split('|') if x]) or '—'
            task_rows.append(f"<tr data-outcome='{esc(r['outcome'])}' data-tier='{esc(r['tier'])}'><td><a href='tasks/{esc(p['profile'])}-{esc(tid)}.html'>{esc(r['task_id'])}</a></td><td>{esc(r['tier'])}</td><td>{badge(r['outcome'])}<br><span class='small'>{esc(r.get('failure_subtype'))}</span></td><td>{esc(r['question'])}</td><td>{esc(r.get('tool_calls_total'))} total<br><span class='small'>{esc(tools)}</span></td><td>{esc(r.get('resource_reads_total'))}<br><span class='small'>{esc(resources)}</span></td><td><details><summary>Answer</summary><pre>{ans}</pre></details></td></tr>")
            detail=f"""<h1>{esc(p['label'])}: {esc(r['task_id'])}</h1><p><a href='../{esc(fname)}'>← Back to model</a></p><div class='grid'>{metric_card('Outcome', r['outcome'], r.get('failure_subtype',''))}{metric_card('Tier', r['tier'], r.get('category',''))}{metric_card('Score', r['score'], 'strict score')}{metric_card('Tool calls', r.get('tool_calls_total',''), f"cypher {r.get('cypher_query_calls','')} / non-cypher {r.get('non_cypher_tool_calls','')}")}</div><h2>Question</h2><p>{esc(r['question'])}</p><h2>Final answer</h2><pre>{ans}</pre><div class='two'><section><h2>Tool use</h2><table><tr><th>Total</th><td>{esc(r.get('tool_calls_total'))}</td></tr><tr><th>Failed</th><td>{esc(r.get('failed_tool_calls'))}</td></tr><tr><th>Unique tools</th><td>{esc(tools)}</td></tr><tr><th>Cypher calls</th><td>{esc(r.get('cypher_query_calls'))}</td></tr><tr><th>Non-Cypher calls</th><td>{esc(r.get('non_cypher_tool_calls'))}</td></tr><tr><th>Agent turns</th><td>{esc(r.get('agent_turns'))}</td></tr></table></section><section><h2>Resource loads</h2><table><tr><th>Reads</th><td>{esc(r.get('resource_reads_total'))}</td></tr><tr><th>Resources</th><td>{esc(resources)}</td></tr><tr><th>Characters</th><td>{esc(r.get('resource_characters_total'))}</td></tr></table></section></div><h2>Diagnostics</h2><table><tr><th>Evidence found</th><td>{esc(r.get('evidence_found'))}</td></tr><tr><th>Evidence depth</th><td>{esc(r.get('evidence_depth_score'))}</td></tr><tr><th>Reference entities seen</th><td>{esc(r.get('reference_entities_seen_count'))}</td></tr><tr><th>Reference path nodes seen</th><td>{esc(r.get('reference_path_nodes_seen_count'))}</td></tr><tr><th>Contract valid</th><td>{esc(r.get('final_answer_contract_valid'))}</td></tr><tr><th>Invalid entities</th><td>{esc(r.get('invalid_entities'))}</td></tr><tr><th>Missing required entities</th><td>{esc(r.get('missing_required_entities'))}</td></tr><tr><th>Minimum evidence satisfied</th><td>{esc(r.get('minimum_evidence_satisfied'))}</td></tr></table><h2>Raw fields</h2><details><summary>Show row JSON</summary><pre>{esc(json.dumps(r, indent=2, ensure_ascii=False))}</pre></details>"""
            (output_dir/'tasks'/f"{p['profile']}-{tid}.html").write_text(page(f"{p['label']} {r['task_id']}", detail))
        model_body=f"""<h1>{esc(p['label'])}</h1><p class='lede'>Full model drilldown: task outcomes, answers, tool calls, resource reads, and diagnostics.</p><div class='grid'>{metric_card('Score', summary['correct']+'/100', f"Δ +{int(summary['correct'])-p['baseline']} vs previous MCP baseline")}{metric_card('No path', summary['no_path_reported'], 'largest remaining failure class')}{metric_card('Incomplete', summary['incomplete_answers'], 'found partial evidence but missed required answer')}{metric_card('Hallucinations', summary['hallucinations'], 'strict invalid entity failures')}{metric_card('Avg tools', summary['avg_tool_calls'], f"{summary['cypher_query_calls']} cypher / {summary['non_cypher_tool_calls']} non-cypher")}</div><h2>Tier performance</h2><table><thead><tr><th>Tier</th><th>Correct</th><th>Rate</th><th></th></tr></thead><tbody>{tier_html}</tbody></table><h2>Task drilldown</h2><div class='filters'><input id='{filter_id}-q' oninput="filterTasks('{filter_id}')" placeholder='Search task/question/answer'><select id='{filter_id}-outcome' onchange="filterTasks('{filter_id}')"><option value=''>All outcomes</option><option>CORRECT</option><option>INCORRECT</option><option>HALLUCINATION</option></select><select id='{filter_id}-tier' onchange="filterTasks('{filter_id}')"><option value=''>All tiers</option><option>1</option><option>3</option><option>4</option><option>5</option></select></div><table id='{filter_id}'><thead><tr><th>Task</th><th>Tier</th><th>Outcome</th><th>Question</th><th>Tools</th><th>Resources</th><th>Answer</th></tr></thead><tbody>{''.join(task_rows)}</tbody></table><script src='assets/report.js'></script>"""
        (output_dir/fname).write_text(page(p['label'], model_body))
    # Cross-model task explorer and issue drilldowns
    by_task = defaultdict(list)
    for p in profiles:
        for r in p['rows']:
            by_task[r['task_id']].append((p, r))

    all_task_rows = []
    for task_id, items in sorted(by_task.items()):
        question = items[0][1].get('question', '')
        tier = items[0][1].get('tier', '')
        cells = []
        for p, r in items:
            tid = row_anchor(r)
            cells.append(
                f"<td>{badge(r['outcome'])}<br><a href='tasks/{esc(p['profile'])}-{esc(tid)}.html'>details</a>"
                f"<br><span class='small'>tools {esc(r.get('tool_calls_total'))}, resources {esc(r.get('resource_reads_total'))}</span></td>"
            )
        all_task_rows.append(
            f"<tr data-tier='{esc(tier)}'><td>{esc(task_id)}</td><td>{esc(tier)}</td><td>{esc(question)}</td>{''.join(cells)}</tr>"
        )
    task_explorer = (
        "<h1>All Tasks</h1><p class='lede'>Cross-model task matrix. Each cell links to the model's exact answer, tool/resource usage, diagnostics, and raw row fields.</p>"
        "<table><thead><tr><th>Task</th><th>Tier</th><th>Question</th>"
        + ''.join(f"<th>{esc(p['label'])}</th>" for p in profiles)
        + "</tr></thead><tbody>" + ''.join(all_task_rows) + "</tbody></table>"
    )
    (output_dir/'tasks.html').write_text(page('All Tasks', task_explorer))

    def issue_page(filename, title, predicate, intro):
        issue_rows = []
        for p in profiles:
            for r in p['rows']:
                if predicate(r):
                    tid = row_anchor(r)
                    answer = pretty_json(r.get('final_answer_normalized') or r.get('final_answer_raw'))
                    issue_rows.append(
                        f"<tr><td>{esc(p['label'])}</td><td><a href='tasks/{esc(p['profile'])}-{esc(tid)}.html'>{esc(r['task_id'])}</a></td>"
                        f"<td>{esc(r.get('tier'))}</td><td>{badge(r.get('outcome'))}<br><span class='small'>{esc(r.get('failure_subtype'))}</span></td>"
                        f"<td>{esc(r.get('question'))}</td><td><details><summary>Answer</summary><pre>{answer}</pre></details></td>"
                        f"<td>{esc(r.get('invalid_entities'))}</td><td>{esc(r.get('missing_required_entities'))}</td></tr>"
                    )
        body = f"<h1>{esc(title)}</h1><p class='lede'>{esc(intro)}</p><table><thead><tr><th>Model</th><th>Task</th><th>Tier</th><th>Outcome</th><th>Question</th><th>Answer</th><th>Invalid entities</th><th>Missing required</th></tr></thead><tbody>{''.join(issue_rows)}</tbody></table>"
        (output_dir/filename).write_text(page(title, body))

    issue_page('hallucinations.html', 'Hallucinations', lambda r: r.get('outcome') == 'HALLUCINATION', 'Every strict hallucination case across all models. Click a task for the full drilldown, including tool/resource counts and raw diagnostics.')
    issue_page('failures.html', 'Failures', lambda r: r.get('outcome') != 'CORRECT', 'All non-correct tasks across all models. This is the fastest way to inspect no-path, incomplete-answer, and hallucination patterns.')

    # Data index
    data_links=''.join(f"<li><a href='{esc(x.name)}'>{esc(x.name)}</a></li>" for x in sorted((output_dir/'data').glob('*')))
    (output_dir/'data'/'index.html').write_text(page('Data files', f"<h1>Raw data files</h1><ul>{data_links}</ul>"))
    # Manifest
    (output_dir/'manifest.json').write_text(json.dumps({'source_root': str(run_root), 'profiles': [p['profile'] for p in profiles], 'pages': len(list(output_dir.rglob('*.html')))}, indent=2))

    return output_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-root', type=Path, required=True,
                        help='Historical run root containing outputs and status files')
    parser.add_argument('--output-dir', type=Path,
                        help='New private report directory (default: RUN_ROOT/rich-report)')
    args = parser.parse_args(argv)
    print(build(args.run_root, args.output_dir))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
