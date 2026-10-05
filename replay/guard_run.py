#!/usr/bin/env python3
"""Guarded driver: run an evaluation one task at a time so that (a) a hung task can only
cost that task and (b) orphaned sandbox children are reaped.

Why this exists.  The subprocess sandbox runs the test runner with no timeout and starts it
in a new session, so one looping task blocks the whole evaluation and, if the driver dies,
leaves a test process pinned to a core forever (observed on a 4-core host).  Wall-clock per
task is capped here, and detached children older than a threshold are killed.

usage: python guard_run.py <eval.py> <task-id-file> [-- <extra args to eval.py>]
"""
import os, signal, subprocess, sys, time
from pathlib import Path

HARD = 420          # wall seconds allowed per task before the driver is killed
ORPHAN_AGE = 330    # kill detached test/agent children older than this


def kill_orphans(log):
    try:
        out = subprocess.run(["ps", "-eo", "pid,etimes,cmd"], capture_output=True, text=True).stdout
    except Exception:
        return
    for line in out.splitlines()[1:]:
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        pid, et, cmd = parts
        if not pid.isdigit() or not et.isdigit() or int(et) < ORPHAN_AGE:
            continue
        if ("sandbox" in cmd or "-m pytest" in cmd) and "guard_run" not in cmd:
            log.write(f"   guard: killing orphan pid={pid} age={et}s {cmd[:80]}\n"); log.flush()
            try:
                os.killpg(os.getpgid(int(pid)), signal.SIGKILL)
            except Exception:
                try:
                    os.kill(int(pid), signal.SIGKILL)
                except Exception:
                    pass


def main():
    driver, idfile = sys.argv[1], sys.argv[2]
    extra = sys.argv[3:]
    ids = Path(idfile).read_text().split()
    log = open("guard_run.log", "w")
    ok = 0
    for n, tid in enumerate(ids, 1):
        kill_orphans(log)
        cmd = [sys.executable, driver, "--tasks", tid] + extra
        t0 = time.time()
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=HARD)
            tail = [l for l in r.stdout.splitlines() if "resolved" in l]
            msg = tail[-1][:110] if tail else f"rc={r.returncode}"
        except subprocess.TimeoutExpired:
            msg = f"HARD TIMEOUT {HARD}s -> killed"
            kill_orphans(log)
        ok += "resolved=True" in msg
        log.write(f"[{n}/{len(ids)}] {tid} {time.time() - t0:6.1f}s {msg}\n"); log.flush()
        print(f"[{n}/{len(ids)}] {tid} {time.time() - t0:6.1f}s {msg}")
    log.write(f"done: {ok}/{len(ids)}\n"); log.close()


if __name__ == "__main__":
    main()
