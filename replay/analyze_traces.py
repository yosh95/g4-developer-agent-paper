#!/usr/bin/env python3
"""Summarise local evaluation traces: per-task tool-call counts, spin (longest run of
byte-identical calls), first edit index and submit index.

`spin` is the metric used in the paper: a maximal run of consecutive tool calls with
identical function name AND identical arguments.  It is a conservative lower bound on
wasted calls, because near-duplicate calls are not counted.

usage: python analyze_traces.py <run-dir>          # run-dir contains traces/trace_*.json
"""
from __future__ import annotations
import collections, json, sys
from pathlib import Path


def longest_repeat(seq):
    best = (0, None)
    i = 0
    while i < len(seq):
        j = i
        while j + 1 < len(seq) and seq[j + 1] == seq[i]:
            j += 1
        if j - i + 1 > best[0]:
            best = (j - i + 1, seq[i])
        i = j + 1
    return best


def analyse(trace_path: Path) -> dict:
    d = json.loads(trace_path.read_text())
    seq, authors, errs = [], collections.Counter(), collections.Counter()
    first_edit = first_submit = None
    n = 0
    for s in d["steps"]:
        a = s.get("extra", {}).get("author", "")
        if a:
            authors[a] += 1
        for tc in (s.get("tool_calls") or []):
            n += 1
            fn = tc["function_name"]
            seq.append((tc.get("extra", {}).get("author", ""), fn,
                        json.dumps(tc["arguments"], sort_keys=True)))
            if fn in ("edit_file", "write_file") and first_edit is None:
                first_edit = n
            if fn == "submit_patch" and first_submit is None:
                first_submit = n
            raw = (s.get("observation") or {}).get("content", "") or ""
            if raw.startswith('{"status": "error"'):
                try:
                    errs[json.loads(raw).get("error_type", "?")] += 1
                except Exception:
                    pass
    run, rep = longest_repeat(seq)
    return {
        "id": trace_path.stem.replace("trace_", ""),
        "steps": len(d["steps"]),
        "tool_calls": n,
        "analyzer_steps": authors.get("code_analyzer", 0),
        "coder_steps": authors.get("swe_coder", 0),
        "longest_identical_run": run,
        "repeated_with": (rep[1] + " " + rep[2][:60]) if rep else "",
        "repeated_ratio": round(run / n, 3) if n else 0.0,
        "first_edit_call": first_edit,
        "first_submit_call": first_submit,
        "errors": dict(errs),
    }


def main():
    run_dir = Path(sys.argv[1])
    rows = {}
    for tp in sorted((run_dir / "traces").glob("trace_*.json")):
        rows[tp.stem.replace("trace_", "")] = analyse(tp)
    if not rows:
        print("no traces under", run_dir / "traces"); return
    print(f"{'task':24s} {'calls':>6} {'spin':>5} {'1st_edit':>9} {'submit':>7}  errors")
    print("-" * 92)
    for tid in sorted(rows, key=lambda t: -rows[t]["tool_calls"]):
        r = rows[tid]
        print(f"{tid:24s} {r['tool_calls']:6d} {r['longest_identical_run']:5d} "
              f"{str(r['first_edit_call']):>9} {str(r['first_submit_call']):>7}  "
              f"{list(r['errors'])[:3]}")
    n = len(rows)
    tot = sum(r["tool_calls"] for r in rows.values())
    spin = sum(r["longest_identical_run"] for r in rows.values())
    print(f"\n{n} tasks, {tot} tool calls; calls inside longest identical chain: "
          f"{spin} ({spin / tot:.1%})")
    print("tasks with a chain >= 10:",
          sorted(t for t in rows if rows[t]["longest_identical_run"] >= 10))


if __name__ == "__main__":
    main()
