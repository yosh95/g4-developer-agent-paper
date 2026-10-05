#!/usr/bin/env python3
"""Compare two or more arms side by side from their rows.jsonl.

usage: python compare_arms.py results/arm_a results/arm_b [--exclude-timeouts]
"""
from __future__ import annotations
import argparse, json
from pathlib import Path

LOCAL_NOISE = ("exceeded session timeout", "driver timeout", "exceeded tool call budget")


def load(root: Path) -> dict:
    rows = {}
    for line in (root / "rows.jsonl").read_text().splitlines():
        r = json.loads(line)
        rows[r["id"]] = r          # later runs win
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("arms", nargs="+")
    ap.add_argument("--exclude-timeouts", action="store_true")
    a = ap.parse_args()
    data = {p: load(Path(p)) for p in a.arms}
    ids = sorted(set().union(*[set(d) for d in data.values()]))
    if a.exclude_timeouts:
        ids = [i for i in ids if not any((data[p].get(i, {}).get("error") or "").startswith(LOCAL_NOISE)
                                         for p in data)]
    print(f"{'task':24s} " + " ".join(f"{p.split('/')[-1][:14]:>14s}" for p in a.arms))
    for i in ids:
        cells = []
        for p in a.arms:
            r = data[p].get(i)
            cells.append("     -" if not r else ("   OK " if str(r.get("resolved")) == "True" else "  --- "))
        print(f"{i:24s} " + " ".join(f"{c:>14s}" for c in cells))
    print()
    for p in a.arms:
        d = {i: data[p][i] for i in ids if i in data[p]}
        ok = sum(1 for r in d.values() if str(r.get("resolved")) == "True")
        print(f"{p:50s} {ok}/{len(d)} = {ok / max(len(d), 1):.1%}")


if __name__ == "__main__":
    main()
