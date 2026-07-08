#!/usr/bin/env python3
"""Read-only Phase 4B/v2 failure attribution extractor.

This script reads existing ORI run roots and emits diagnostic artifacts into a
separate scratch directory. It does not modify run roots or scoring.
"""
# ruff: noqa: E501,E701,E702

from __future__ import annotations

import argparse
import csv
import json
import re
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Any

TAXONOMY = [
    "STRICT_CORRECT",
    "TRUE_WRONG_ANSWER",
    "WRONG_PATH",
    "NO_PATH_REPORTED",
    "INCOMPLETE_ANSWER",
    "INVALID_ENTITY",
    "EVIDENCE_FOUND_NO_SYNTHESIS",
    "PREMATURE_FINALIZATION",
    "SHALLOW_SEARCH",
    "TOOL_MISUSE",
    "CYPHER_SYNTAX_ERROR",
    "CYPHER_TOO_EXPENSIVE",
    "PROMPT_REFERENCE_MISMATCH_SUSPECT",
    "SCORER_CONTRACT_MISMATCH_SUSPECT",
]

EXPECTED_CODEX_COUNTS = {
    "codex_gpt_5_4_cyber_phase4b_v2_direct": {"strict_score": 55, "CYPHER_SYNTAX_ERROR": 6, "CYPHER_TOO_EXPENSIVE": 3},
    "codex_gpt_5_4_cyber_phase4b_v2_mcp": {"strict_score": 42, "INVALID_ENTITY": 16, "NO_PATH_REPORTED": 36, "INCOMPLETE_ANSWER": 4},
    "codex_gpt_5_5_phase4b_v2_direct": {"strict_score": 51, "CYPHER_SYNTAX_ERROR": 5, "CYPHER_TOO_EXPENSIVE": 1},
    "codex_gpt_5_5_phase4b_v2_mcp": {"strict_score": 65, "INVALID_ENTITY": 17, "NO_PATH_REPORTED": 13, "INCOMPLETE_ANSWER": 5},
    "codex_gpt_5_5_cyber_preview_phase4b_v2_direct": {"strict_score": 46, "CYPHER_SYNTAX_ERROR": 1, "CYPHER_TOO_EXPENSIVE": 7},
    "codex_gpt_5_5_cyber_preview_phase4b_v2_mcp": {"strict_score": 61, "INVALID_ENTITY": 17, "NO_PATH_REPORTED": 13, "INCOMPLETE_ANSWER": 7},
    "codex_waluigi_safety_alpha_phase4b_v2_direct": {"strict_score": 54, "CYPHER_SYNTAX_ERROR": 4, "CYPHER_TOO_EXPENSIVE": 2},
    "codex_waluigi_safety_alpha_phase4b_v2_mcp": {"strict_score": 65, "INVALID_ENTITY": 17, "NO_PATH_REPORTED": 13, "INCOMPLETE_ANSWER": 3},
}

@dataclass
class ProfileData:
    profile: str
    mode: str
    model: str
    output_dir: Path
    rows: list[dict[str, str]]
    summary: dict[str, str]
    eval_files: list[str]
    telemetry_files: list[str]


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8", errors="replace") as f:
        return list(csv.DictReader(f))


def first_summary(path: Path) -> dict[str, str]:
    rows = read_csv(path)
    return rows[0] if rows else {}


def safe_int(v: Any, default: int = 0) -> int:
    try:
        if v is None or v == "":
            return default
        return int(float(str(v)))
    except Exception:
        return default


def safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(str(v))
    except Exception:
        return default


def normalize_outcome(row: dict[str, str], mode: str) -> str:
    if safe_float(row.get("score")) >= 1.0 or row.get("outcome") == "CORRECT":
        return "STRICT_CORRECT"
    outcome = (row.get("outcome") or "").upper()
    subtype = (row.get("failure_subtype") or "").upper()
    err = (row.get("error_detail") or "").lower()
    final_raw = (row.get("final_answer_raw") or "").strip()
    final_norm = (row.get("final_answer_normalized") or "").strip()
    if outcome in {"QUERY_TOO_EXPENSIVE"} or "too expensive" in err:
        return "CYPHER_TOO_EXPENSIVE"
    if outcome in {"CYPHER_ERROR"} or "syntax" in err or "cypher" in err and mode == "direct":
        return "CYPHER_SYNTAX_ERROR"
    if outcome in {"HALLUCINATION"} or row.get("hallucination") == "True":
        return "INVALID_ENTITY"
    if subtype == "NO_PATH_REPORTED" or outcome == "NO_PATH_REPORTED":
        return "NO_PATH_REPORTED"
    if subtype == "INCOMPLETE_ANSWER" or outcome == "INCOMPLETE_ANSWER":
        return "INCOMPLETE_ANSWER"
    if "wrong_path" in subtype.lower() or outcome == "WRONG_PATH":
        return "WRONG_PATH"
    # Heuristics for lossy MCP behavior. Keep strict score unchanged.
    if mode == "mcp" and safe_int(row.get("successful_tool_results")) > 0:
        if not final_raw and not final_norm:
            return "EVIDENCE_FOUND_NO_SYNTHESIS"
        if safe_int(row.get("tool_calls_total")) <= 1:
            return "SHALLOW_SEARCH"
    return "TRUE_WRONG_ANSWER"


def profile_name_from_output_dir(path: Path) -> str:
    return path.name


def profile_model_mode(profile: str, rows: list[dict[str, str]], summary: dict[str, str]) -> tuple[str, str]:
    mode = "mcp" if profile.endswith("_mcp") or (rows and (rows[0].get("eval_mode") == "mcp" or safe_int(rows[0].get("tool_calls_total")) > 0)) else "direct"
    model = summary.get("model") or (rows[0].get("run_name") if rows else profile) or profile
    return str(model), mode


def inspect_eval_files(output_dir: Path) -> list[str]:
    return [str(p) for p in sorted((output_dir / "_inspect_logs").rglob("*.eval"))]


def inspect_eval_sample_count(eval_path: Path) -> int | None:
    try:
        with zipfile.ZipFile(eval_path) as z:
            return sum(1 for n in z.namelist() if n.startswith("samples/") and n.endswith(".json"))
    except Exception:
        return None


def collect_profiles(run_root: Path) -> list[ProfileData]:
    outputs = run_root / "outputs"
    candidates = []
    if (outputs / "baseline_combined.csv").exists():
        candidates.append(outputs)
    candidates += [p for p in sorted(outputs.glob("*")) if p.is_dir() and (p / "baseline_combined.csv").exists()]
    profiles = []
    for out in candidates:
        rows = read_csv(out / "baseline_combined.csv")
        summary = first_summary(out / "baseline_summary.csv")
        profile = profile_name_from_output_dir(out)
        model, mode = profile_model_mode(profile, rows, summary)
        telemetry = [str(p) for p in sorted(out.glob("telemetry/samples/*.jsonl"))]
        profiles.append(ProfileData(profile, mode, model, out, rows, summary, inspect_eval_files(out), telemetry))
    return profiles


def profile_counts(p: ProfileData) -> dict[str, Any]:
    tax = Counter()
    raw = Counter()
    sub = Counter()
    for row in p.rows:
        raw[row.get("outcome") or ""] += 1
        if row.get("failure_subtype"):
            sub[row.get("failure_subtype") or ""] += 1
        tax[normalize_outcome(row, p.mode)] += 1
    strict = tax["STRICT_CORRECT"]
    eval_sample_counts = {path: inspect_eval_sample_count(Path(path)) for path in p.eval_files}
    return {
        "profile": p.profile,
        "model": p.model,
        "mode": p.mode,
        "strict_score": strict,
        "total_tasks": len(p.rows),
        "raw_outcomes": dict(raw),
        "failure_subtypes": dict(sub),
        "taxonomy_counts": {k: tax.get(k, 0) for k in TAXONOMY},
        "cypher_errors": raw.get("CYPHER_ERROR", 0),
        "query_too_expensive": raw.get("QUERY_TOO_EXPENSIVE", 0),
        "hallucinations": raw.get("HALLUCINATION", 0),
        "no_path_reported": sub.get("NO_PATH_REPORTED", 0),
        "incomplete_answers": sub.get("INCOMPLETE_ANSWER", 0),
        "avg_query_length_correct": avg_query_len(p.rows, True),
        "avg_query_length_wrong": avg_query_len(p.rows, False),
        "avg_tool_calls_correct": avg_tools(p.rows, True),
        "avg_tool_calls_wrong": avg_tools(p.rows, False),
        "eval_files": p.eval_files,
        "eval_sample_counts": eval_sample_counts,
        "telemetry_files": p.telemetry_files,
    }


def avg_query_len(rows: list[dict[str, str]], correct: bool) -> float | None:
    vals = []
    for r in rows:
        is_correct = safe_float(r.get("score")) >= 1.0 or r.get("outcome") == "CORRECT"
        if is_correct == correct and r.get("model_cypher"):
            vals.append(len(r.get("model_cypher") or ""))
    return round(mean(vals), 1) if vals else None


def avg_tools(rows: list[dict[str, str]], correct: bool) -> float | None:
    vals = []
    for r in rows:
        is_correct = safe_float(r.get("score")) >= 1.0 or r.get("outcome") == "CORRECT"
        if is_correct == correct and r.get("tool_calls_total") not in (None, ""):
            vals.append(safe_int(r.get("tool_calls_total")))
    return round(mean(vals), 2) if vals else None


def common_misses(profiles: list[ProfileData]) -> list[dict[str, Any]]:
    by_task: dict[str, list[tuple[ProfileData, dict[str, str], str]]] = defaultdict(list)
    for p in profiles:
        for r in p.rows:
            if safe_float(r.get("score")) < 1.0 and r.get("outcome") != "CORRECT":
                by_task[r.get("task_id") or "UNKNOWN"].append((p, r, normalize_outcome(r, p.mode)))
    out = []
    for task, misses in by_task.items():
        mcp_misses = [m for m in misses if m[0].mode == "mcp"]
        direct_misses = [m for m in misses if m[0].mode == "direct"]
        sample = misses[0][1]
        out.append({
            "task_id": task,
            "template_id": sample.get("template_id"),
            "category": sample.get("category"),
            "tier": sample.get("tier"),
            "miss_count": len(misses),
            "mcp_miss_count": len(mcp_misses),
            "direct_miss_count": len(direct_misses),
            "profiles": [m[0].profile for m in misses],
            "taxonomy": dict(Counter(m[2] for m in misses)),
            "question": sample.get("question", "")[:500],
        })
    return sorted(out, key=lambda x: (-x["miss_count"], -x["mcp_miss_count"], x["task_id"]))


def diagnostic_slice(profiles: list[ProfileData], misses: list[dict[str, Any]]) -> dict[str, Any]:
    # Determine GPT-5.4 Cyber direct-correct/MCP-wrong path-ish tasks.
    rows_by_profile = {p.profile: {r.get("task_id"): r for r in p.rows} for p in profiles}
    g54_direct = rows_by_profile.get("codex_gpt_5_4_cyber_phase4b_v2_direct", {})
    g54_mcp = rows_by_profile.get("codex_gpt_5_4_cyber_phase4b_v2_mcp", {})
    path_regressions = []
    for tid, dr in g54_direct.items():
        mr = g54_mcp.get(tid)
        if mr and safe_float(dr.get("score")) >= 1.0 and safe_float(mr.get("score")) < 1.0:
            if "path" in (mr.get("question") or "").lower() or "path" in (mr.get("template_id") or "").lower():
                path_regressions.append(task_candidate(tid or "", mr, "gpt5.4-direct-correct-mcp-wrong-path"))
    def by_keywords(words: list[str], n: int, reason: str):
        got=[]
        for m in misses:
            hay = " ".join(str(m.get(k,"")) for k in ["template_id","category","question","task_id"]).lower()
            matched = False
            for w in words:
                wl = w.lower()
                if len(wl) <= 3:
                    matched = bool(re.search(rf"(?<![a-z0-9]){re.escape(wl)}(?![a-z0-9])", hay))
                else:
                    matched = wl in hay
                if matched:
                    break
            if matched:
                got.append({"task_id": m["task_id"], "reason": reason, "template_id": m.get("template_id"), "category": m.get("category"), "mcp_miss_count": m.get("mcp_miss_count"), "miss_count": m.get("miss_count")})
        return unique(got)[:n]
    incomplete=[]
    for p in profiles:
        if p.mode != "mcp" or not ("gpt_5_5" in p.profile or "waluigi" in p.profile):
            continue
        for r in p.rows:
            if normalize_outcome(r, p.mode) in {"NO_PATH_REPORTED", "INCOMPLETE_ANSWER"}:
                incomplete.append(task_candidate(r.get("task_id") or "", r, f"{p.profile}:{normalize_outcome(r,p.mode)}"))
    return {
        "adcs_esc1": by_keywords(["adcs", "esc1", "certificate", "template", "ca"], 5, "ADCS/ESC1 common miss candidate"),
        "active_session_delegation": by_keywords(["session", "delegation", "unconstrained"], 5, "active-session/delegation common miss candidate"),
        "gpt54_mcp_regressed_paths": unique(path_regressions)[:5],
        "gpt55_waluigi_incomplete_no_path": unique(incomplete)[:5],
    }


def task_candidate(tid: str, row: dict[str, str], reason: str) -> dict[str, Any]:
    return {"task_id": tid, "reason": reason, "template_id": row.get("template_id"), "category": row.get("category"), "tier": row.get("tier"), "question": (row.get("question") or "")[:400]}


def unique(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen=set(); out=[]
    for item in items:
        key=item.get("task_id")
        if key and key not in seen:
            seen.add(key); out.append(item)
    return out


def qwen_forensics(qwen_root: Path, out_dir: Path) -> dict[str, Any] | None:
    out_dir.mkdir(parents=True, exist_ok=True)
    combined = qwen_root / "outputs" / "baseline_combined.csv"
    if not combined.exists():
        return None
    rows = read_csv(combined)
    thinking_rows = [r for r in rows if (r.get("model_thinking") or "").strip()]
    evidence_loss=[]
    for r in rows:
        if safe_float(r.get("score")) < 1.0 and safe_int(r.get("successful_tool_results")) > 0 and (r.get("model_thinking") or ""):
            text=(r.get("model_thinking") or "").lower()
            hints=sum(1 for w in ["found", "identified", "appears", "path", "group", "template", "session", "delegation"] if w in text)
            if hints >= 2 or r.get("final_answer_raw"):
                evidence_loss.append({
                    "task_id": r.get("task_id"),
                    "outcome": r.get("outcome"),
                    "failure_subtype": r.get("failure_subtype"),
                    "tool_calls_total": r.get("tool_calls_total"),
                    "successful_tool_results": r.get("successful_tool_results"),
                    "thinking_excerpt": (r.get("model_thinking") or "")[:1200],
                    "final_answer_raw": (r.get("final_answer_raw") or "")[:500],
                })
    md=["# Qwen reasoning forensics\n\n", f"Run root: `{qwen_root}`\n\n", f"Rows with model_thinking: {len(thinking_rows)}/{len(rows)}\n\n", f"Candidate evidence-found/final-output-loss rows: {len(evidence_loss)}\n\n"]
    for item in evidence_loss[:25]:
        md.append(f"## {item['task_id']} — {item['outcome']} / {item['failure_subtype']}\n\n")
        md.append(f"tool_calls={item['tool_calls_total']} successful_tool_results={item['successful_tool_results']}\n\n")
        md.append("Thinking excerpt:\n\n```text\n" + item["thinking_excerpt"] + "\n```\n\n")
        if item["final_answer_raw"]:
            md.append("Final answer excerpt:\n\n```json\n" + item["final_answer_raw"] + "\n```\n\n")
    (out_dir / "reasoning-forensics-qwen.md").write_text("".join(md), encoding="utf-8")
    return {"rows": len(rows), "model_thinking_rows": len(thinking_rows), "evidence_loss_candidates": evidence_loss[:50]}


def write_outputs(summary: dict[str, Any], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "failure-attribution-summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    with (out_dir / "common-miss-tasks.csv").open("w", newline="", encoding="utf-8") as f:
        fields=["task_id","template_id","category","tier","miss_count","mcp_miss_count","direct_miss_count","profiles","taxonomy","question"]
        w=csv.DictWriter(f, fieldnames=fields); w.writeheader()
        for row in summary["common_misses"]:
            rr=row.copy(); rr["profiles"]=json.dumps(rr["profiles"]); rr["taxonomy"]=json.dumps(rr["taxonomy"]); w.writerow({k: rr.get(k,"") for k in fields})
    (out_dir / "diagnostic-slice-candidates.json").write_text(json.dumps(summary["diagnostic_slice_candidates"], indent=2, sort_keys=True), encoding="utf-8")
    md=["# Phase 4B/v2 Failure Attribution Summary\n\n", f"Run root: `{summary['run_root']}`\n\n"]
    md.append("## Per-profile counts\n\n")
    md.append("| Profile | Mode | Strict | Total | Cypher errors | Too expensive | Hallucination/invalid entity | No path | Incomplete | Avg tools wrong |\n")
    md.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|\n")
    for p in summary["profiles"]:
        md.append(f"| {p['profile']} | {p['mode']} | {p['strict_score']} | {p['total_tasks']} | {p['cypher_errors']} | {p['query_too_expensive']} | {p['hallucinations']} | {p['no_path_reported']} | {p['incomplete_answers']} | {p.get('avg_tool_calls_wrong')} |\n")
    md.append("\n## Common misses\n\n")
    for row in summary["common_misses"][:25]:
        md.append(f"- `{row['task_id']}` miss_count={row['miss_count']} mcp_miss_count={row['mcp_miss_count']} taxonomy={row['taxonomy']} template={row.get('template_id')}\n")
    md.append("\n## Next patch plan\n\n")
    md.append(summary["next_patch_plan_md"])
    (out_dir / "failure-attribution-summary.md").write_text("".join(md), encoding="utf-8")


def validate_expected(summary: dict[str, Any]) -> list[str]:
    errors=[]
    by={p["profile"]:p for p in summary["profiles"]}
    for profile, expected in EXPECTED_CODEX_COUNTS.items():
        got=by.get(profile)
        if not got:
            errors.append(f"missing profile {profile}"); continue
        for key, value in expected.items():
            if key == "strict_score":
                actual=got["strict_score"]
            elif key == "CYPHER_SYNTAX_ERROR":
                actual=got["cypher_errors"]
            elif key == "CYPHER_TOO_EXPENSIVE":
                actual=got["query_too_expensive"]
            elif key == "INVALID_ENTITY":
                actual=got["hallucinations"]
            elif key == "NO_PATH_REPORTED":
                actual=got["no_path_reported"]
            elif key == "INCOMPLETE_ANSWER":
                actual=got["incomplete_answers"]
            else:
                actual=got["taxonomy_counts"].get(key,0)
            if actual != value:
                errors.append(f"{profile} {key}: expected {value}, got {actual}")
    return errors


def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--run-root", required=True, type=Path)
    ap.add_argument("--qwen-root", type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--validate-codex-counts", action="store_true")
    args=ap.parse_args()
    profiles=collect_profiles(args.run_root)
    prof_summaries=[profile_counts(p) for p in profiles]
    misses=common_misses(profiles)
    summary={
        "run_root": str(args.run_root),
        "taxonomy": TAXONOMY,
        "profiles": prof_summaries,
        "common_misses": misses,
        "task_families_failed_across_3plus_mcp_profiles": [m for m in misses if m["mcp_miss_count"] >= 3],
        "diagnostic_slice_candidates": diagnostic_slice(profiles, misses),
        "next_patch_plan_md": NEXT_PATCH_PLAN,
    }
    if args.qwen_root:
        summary["qwen_forensics"] = qwen_forensics(args.qwen_root, args.out_dir)
    if args.validate_codex_counts:
        errors=validate_expected(summary)
        summary["expected_count_validation"]={"ok": not errors, "errors": errors}
        if errors:
            write_outputs(summary, args.out_dir)
            print("Expected count validation failed:")
            for e in errors: print(" -", e)
            return 2
    write_outputs(summary, args.out_dir)
    print(f"Wrote {args.out_dir}")
    if args.validate_codex_counts:
        print("Expected Codex/OpenAI counts reproduced.")
    return 0

NEXT_PATCH_PLAN = """
1. Diagnostic CSV fields: add `failure_stage`, `evidence_found`, `evidence_depth_score`, `reference_entities_seen_count`, `reference_path_nodes_seen_count`, `final_answer_contract_valid`, `invalid_entities`, `missing_required_entities`, `finalization_guard_used`, `repair_turn_used`, `minimum_evidence_satisfied`, `successful_tool_results`, `reasoning_capture_mode`, and `reasoning_token_count` as diagnostic-only columns. Preserve strict score columns unchanged.
2. Entity/path pre-grade validation: validate final entities against the allowed graph universe, validate `path_found` and answer type, require source/target presence for path tasks, and record invalid/missing entities without changing the default score.
3. Optional one-turn repair: config-gated only; expose only contract defects and model-observed entities/tool evidence, never reference answers. Emit `repair_turn_used` and keep baseline strict mode off by default.
4. MCP finalization guard: near loop budget, if successful tool results/evidence exist, force one no-tools synthesis turn and record `finalization_guard_used`; separate no-evidence failures from evidence-found/no-synthesis.
5. Prompt/reference reconciliation: inspect common-miss families before changing task text. Patch only real wording/reference mismatches and version any changed task wording.
6. Query strategy resources/templates: add non-answer-leaking templates for ADCS/ESC1, unconstrained delegation plus sessions, active sessions, source-to-target pathfinding, and nested group membership. Make template/resource use visible in telemetry where practical.
""".strip() + "\n"

if __name__ == "__main__":
    raise SystemExit(main())
