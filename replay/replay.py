#!/usr/bin/env python3
"""Phase 1/2 のローカル再生 — swegemma ハーネスの Container B を再現して採点する。

HARNESS_README.md §4.2 / §8.2 と sandbox/setup.py の手順をそのまま写している:
  1. snapshot(base_commit) を展開
  2. git の exclude 設定 + リポジトリを editable install + pytest.ini/conftest.py 作成
  3. baseline commit (HEAD)
  4. agent_patch を 4 段フォールバックで適用（失敗なら即 unresolved）
  5. test_patch が触るファイルを HEAD に強制リセット（テスト改変の無効化）
  6. test_patch 適用
  7. 対象テストのみ hermetic pytest（PYTHONSAFEPATH=1, -p no:anyio, timeout=0）
  8. pytest の終了コード == 0 なら resolved

使い方:
  ./venv3/bin/python replay.py --task fastapi_12942 --oracle
  ./venv3/bin/python replay.py --task fastapi_12942 --patch my.diff --keep
  ./venv3/bin/python replay.py --task fastapi_12942                 # patch なし = 床
  ./venv3/bin/python replay.py --batch work/selected_tasks.txt --oracle
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SNAP_DIR = HERE / "work" / "snapshots"
WHEELS = HERE / "work" / "wheels"
RUNS = HERE / "work" / "runs"
VENV_DIR = HERE / "work" / "venvs"
TASKS = HERE / "tasks.jsonl"
PY = next((HERE / v / "bin" / "python" for v in ("venv3", "venv2", "venv")
           if (HERE / v / "bin" / "python").exists()), Path(sys.executable))
FILE_RE = re.compile(r"^\+\+\+ b/(\S+)", re.M)

EXCLUDE = "__pycache__/\n*.pyc\n.pytest_cache/\n*.egg-info/\nbuild/\ndist/\n.coverage\n"
PYTEST_INI = ("[pytest]\naddopts = --import-mode=importlib -p no:anyio\n"
              "norecursedirs = .* build dist venv\npython_classes = Test* *Test\n"
              "python_files = test_*.py *_test.py\nfilterwarnings =\n"
              "    ignore::DeprecationWarning\n    ignore::UserWarning\n")
CONFTEST = "# Standard test discovery hook for SWE-gemma public benchmark\n"
# harness イメージに必ず入っている pytest 関連 + 公開リポジトリのテストで多用される補助
TEST_DEPS = [
    # harness イメージ（Dockerfile.public）に必ず入っているもの
    "pytest", "pytest-timeout==2.1.0", "typer", "pluggy", "iniconfig", "packaging",
    # 公開リポジトリのテストで多用されるもの + その依存クロージャ（すべて --no-deps で入れる）
    "dirty-equals", "inline-snapshot", "PyJWT", "python-multipart", "email-validator",
    "pwdlib", "asgiref", "trio", "coverage", "flask", "sqlmodel", "httpx", "requests",
    "pyyaml", "ujson", "orjson", "sniffio", "idna", "h11", "httpcore", "click", "pygments", "rich", "shellingham",
    "typing-extensions", "markdown-it-py", "mdurl", "anyio", "werkzeug", "jinja2",
    "markupsafe", "itsdangerous", "sortedcontainers", "outcome", "shellingham",
    "annotated-types", "colorama", "executing", "asttokens", "tomli", "black",
    "httpbin", "pytest-httpbin", "six", "decorator", "brotlicffi", "cffi", "pycparser",
    "trustme", "pytest-asyncio", "psutil", "setuptools", "wheel", "blinker",
    "pytest-cov", "py", "toml", "mock", "betamax", "charset-normalizer", "certifi",
]


def sh(cmd: str, cwd: Path, timeout: int = 900, env_extra: str = ""):
    r = subprocess.run(["bash", "-lc", (env_extra + " " if env_extra else "") + cmd],
                       cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout, r.stderr


def norm(p: str) -> str:
    return p if p.endswith("\n") else p + "\n"


def collect_requirements(ws: Path) -> list[str]:
    """pyproject / requirements*.txt から依存パッケージ名を集める（setup.py と同じ発想）。"""
    names: list[str] = []
    reqs = list(ws.glob("requirements*.txt"))
    try:
        import tomllib
        pp = ws / "pyproject.toml"
        if pp.exists():
            d = tomllib.loads(pp.read_text(encoding="utf-8", errors="replace"))
            proj = d.get("project", {})
            deps = list(proj.get("dependencies", []))
            for k, v in (proj.get("optional-dependencies") or {}).items():
                if k in ("all", "standard", "dev", "test", "testcov"):
                    deps += v
            reqs += [ws / "pyproject.toml"] if deps else []
            names += [re.split(r"[=<>!~;\[\s]", x.strip())[0] for x in deps]
    except Exception:
        pass
    for rf in reqs:
        if rf.name == "pyproject.toml":
            continue
        for line in rf.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith(("#", "-", "git+", "http")):
                continue
            n = re.split(r"[=<>!~;\[\s]", line)[0]
            if re.fullmatch(r"[A-Za-z0-9_.\-]+", n or ""):
                names.append(n)
    return sorted(set(names))


def build_env(ws: Path, log: list) -> tuple[Path, str]:
    """タスク専用 venv を作り、リポジトリを editable install、テスト補助依存も整える。

    実ハーネスではタスクごとに独立したコンテナなので、環境をタスク間で共有しない。
    """
    venv = VENV_DIR / ws.parent.name.replace("replay_", "")
    py_bin = venv / "bin" / "python"
    VENV_DIR.mkdir(parents=True, exist_ok=True)
    if not py_bin.exists():
        rc, so, se = sh(f"uv venv {venv} --python 3.13 2>&1 | tail -1", ws.parent, 300)
        log.append({"cmd": "uv venv", "rc": rc, "out": (so + se)[-200:]})
    py = str(py_bin)

    # harness が baseline commit に含めるファイル
    (ws / ".git" / "info").mkdir(parents=True, exist_ok=True)
    (ws / ".git" / "info" / "exclude").write_text(EXCLUDE)
    ini = ws / "pytest.ini"
    ini.write_text(PYTEST_INI + (("\n" + ini.read_text()) if ini.exists() else ""))
    ct = ws / "conftest.py"
    ct.write_text(CONFTEST + (ct.read_text() if ct.exists() else ""))

    # editable install（依存レンジはリポジトリ側の指定に従わせる = 実ハーネスの解決順）
    for cmd, tmo in [
        (f"uv pip install --python {py} -e . 2>&1 | tail -3", 900),
        (f"uv pip install --python {py} --no-index --find-links {WHEELS} --no-deps "
         f"--no-build-isolation -e . 2>&1 | tail -2", 600),
    ]:
        rc, so, se = sh(cmd, ws, tmo)
        log.append({"cmd": cmd[:70], "rc": rc, "out": (so + se)[-500:]})
        if rc == 0:
            break

    deps = collect_requirements(ws)
    if deps:
        rc, so, se = sh(f"uv pip install --python {py} --no-deps " + " ".join(deps) +
                        " 2>&1 | tail -2", ws, 900)
        log.append({"cmd": "peer requirements", "rc": rc,
                    "out": (so + se)[-400:], "n": len(deps)})

    # リポジトリ自身が提供する dist は helper として入れない（wheel が editable を覆い隠す事故を防ぐ）
    repo_dist = ""
    try:
        import tomllib
        pp = ws / "pyproject.toml"
        if pp.exists():
            repo_dist = (tomllib.loads(pp.read_text(encoding="utf-8", errors="replace"))
                         .get("project", {}).get("name", "") or "")
    except Exception:
        pass
    repo_dist_norm = repo_dist.lower().replace("_", "-")
    for known in ("rich", "requests", "httpx", "fastapi", "starlette", "flask", "typer"):
        if (ws / known).is_dir() and (ws / known / "__init__.py").exists():
            repo_dist_norm = repo_dist_norm or known
    # ヘルパー依存は「必ず・--no-deps で・リポジトリ自身の dist を除いて」入れる。
    # 個別の import 判定に頼ると、依存順で取りこぼす（pygments 等）ため列挙して入れる。
    helpers = [d for d in TEST_DEPS
               if d.lower().replace("_", "-").split("==")[0] != repo_dist_norm]
    rc, so, se = sh(f"uv pip install --python {py} --no-deps " + " ".join(helpers) +
                    " 2>&1 | tail -3", ws, 900)
    log.append({"cmd": "test helpers", "rc": rc, "out": (so + se)[-400:], "n": len(helpers)})

    # 環境の健全性チェック（ここが崩れていると採点結果を信用できない）
    rc, so, se = sh(f"{py} -c 'import pytest, pluggy, pygments'", ws)
    log.append({"cmd": "sanity import", "rc": rc, "out": (so + se)[-300:]})

    # 事故検知: リポジトリのパッケージが site-packages 側から解決されていたら、その wheel を外す
    pkg_dir = next((d for d in sorted(ws.iterdir())
                    if d.is_dir() and (d / "__init__.py").exists()
                    and d.name not in ("tests", "docs", "docs_src", "scripts", "venv")), None)
    if pkg_dir:
        rc, so, se = sh(f"{py} -c 'import {pkg_dir.name} as m; print(m.__file__)'", ws)
        origin = (so.strip().splitlines() or [""])[-1]
        if rc == 0 and origin and str(ws) not in origin and repo_dist:
            rc2, so2, se2 = sh(f"uv pip uninstall --python {py} {repo_dist}", ws, 300)
            log.append({"cmd": f"uninstall shadowing {repo_dist}", "rc": rc2,
                        "out": (so2 + se2)[-200:], "was": origin})
            sh(f"uv pip install --python {py} --no-deps --no-build-isolation -e . "
               f"2>&1 | tail -1", ws, 300)
        log.append({"cmd": "import origin", "pkg": pkg_dir.name, "origin": origin})

    sh("git add -A && git -c user.email=e@eval -c user.name=Agent commit -q -m baseline || true",
       ws, 300)
    return py_bin, (repo_dist_norm or "")


def apply_patch(ws: Path, patch: str, name: str) -> tuple[bool, str]:
    p = ws.parent / name
    p.write_text(norm(patch))
    for opts in ("--unsafe-paths -p1", "--unsafe-paths -p1 -3",
                 "--unsafe-paths -p1 --ignore-space-change --ignore-whitespace",
                 "--unsafe-paths -p1 --recount", "--unsafe-paths -p0"):
        rc, _, _ = sh(f"git apply {opts} {p}", ws)
        if rc == 0:
            return True, f"git apply {opts}"
    for opts in ("-p1", "-p0"):
        rc, so, se = sh(f"patch {opts} -l --batch --forward < {p}", ws)
        if rc == 0:
            return True, f"patch {opts}"
    return False, (so + se)[-300:]


def run_task(task: dict, patch: str, keep: bool = False) -> dict:
    iid = task["instance_id"]
    tgz = SNAP_DIR / f"{iid}.tgz"
    targets = sorted(set(FILE_RE.findall(task["test_patch"])))
    res = {"instance_id": iid, "repo": task["repo"], "resolved": False, "exit_code": None,
           "applied": None, "error": None, "patch_bytes": len(patch or ""), "seconds": 0.0,
           "targets": targets, "log": "", "env": []}
    if not tgz.exists():
        res["error"] = f"snapshot missing: {tgz}"
        return res
    t0 = time.time()
    run = RUNS / f"replay_{iid}"
    shutil.rmtree(run, ignore_errors=True)
    ws = run / "workspace"
    ws.mkdir(parents=True)
    try:
        with tarfile.open(tgz) as tf:
            tf.extractall(ws, filter="data")
        pybin, repo_dist_norm = build_env(ws, res["env"])
        if patch and patch.strip():
            ok, how = apply_patch(ws, patch, "agent.patch")
            res["applied"] = how if ok else None
            if not ok:
                res["error"] = f"agent_patch apply failed: {how}"
                return res
        else:
            res["applied"] = ""
        if targets:
            fl = " ".join(f"'{f}'" for f in targets)
            sh(f"git checkout HEAD -- {fl} 2>/dev/null || true", ws)
            sh(f"git clean -f -- {fl} 2>/dev/null || true", ws)
        ok, how = apply_patch(ws, task["test_patch"], "test.patch")
        if not ok:
            res["error"] = f"test_patch apply failed: {how}"
            return res
        tgt = " ".join(f"'{f}'" for f in targets) or "."
        cmd = (f"PYTHONSAFEPATH=1 TEST_TMPDIR=/tmp {pybin} -m pytest {tgt} -p no:anyio "
               f"-o timeout=0 -o 'norecursedirs=.* build dist venv' "
               f"-o 'python_classes=Test* *Test' -q")
        rc, so, se = sh(cmd, ws, 900)
        # 自己修復: pytest 自身/プラグインの起動でモジュールが欠けていたら導入して再実行
        # （実ハーネスのイメージには依存が全部入っている前提なので、これはローカル再現の補正）
        for attempt in range(3):
            blob = so + se
            missing = set(re.findall(r"No module named '([A-Za-z0-9_.]+)'", blob))
            fixable = {m for m in missing
                       if m.split(".")[0] not in (repo_dist_norm, "fastapi", "starlette")}
            if rc == 0 or not fixable:
                break
            pkgs = sorted(fixable)
            r2, so2, se2 = sh(f"uv pip install --python {pybin} " + " ".join(pkgs) +
                              " 2>&1 | tail -2", ws, 600)
            res["env"].append({"cmd": f"autofix missing {pkgs} (attempt {attempt + 1})",
                               "rc": r2, "out": (so2 + se2)[-300:]})
            rc, so, se = sh(cmd, ws, 900)
        res["exit_code"] = rc
        res["resolved"] = (rc == 0)
        res["log"] = (so + "\n--- stderr ---\n" + se)[-5000:]
        return res
    finally:
        res["seconds"] = round(time.time() - t0, 1)
        RUNS.mkdir(parents=True, exist_ok=True)
        (RUNS / f"result_{iid}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
        if not keep:
            shutil.rmtree(ws, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task")
    ap.add_argument("--batch", help="task id を並べたファイル")
    ap.add_argument("--oracle", action="store_true")
    ap.add_argument("--patch")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--out", default=str(HERE / "results" / "local"))
    a = ap.parse_args()
    tasks = {json.loads(l)["instance_id"]: json.loads(l)
             for l in TASKS.read_text(encoding="utf-8").splitlines() if l.strip()}
    ids = ([a.task] if a.task else
           [l.strip() for l in Path(a.batch).read_text().split() if l.strip()])
    ext = Path(a.patch).read_text() if a.patch else ""
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    summary = []
    t0 = time.time()
    for iid in ids:
        patch = ext or (tasks[iid]["patch"] if a.oracle else "")
        try:
            r = run_task(tasks[iid], patch, keep=a.keep)
        except Exception as e:  # noqa: BLE001
            r = {"instance_id": iid, "resolved": False, "error": f"{type(e).__name__}: {e}"[:300]}
        summary.append(r)
        print(f"{iid:18s} resolved={int(bool(r['resolved']))} rc={r.get('exit_code')} "
              f"{r.get('seconds','?')}s {r.get('error') or ''}", flush=True)
        (out / "summary.json").write_text(json.dumps(
            {"mode": "oracle" if a.oracle else ("agent" if ext else "empty"),
             "n": len(summary),
             "resolved": sum(bool(r["resolved"]) for r in summary),
             "results": summary}, ensure_ascii=False, indent=1))
    n = len(summary); k = sum(bool(r["resolved"]) for r in summary)
    print(f"\nResolution Rate = {k}/{n} = {k/max(n,1):.4f}  ({time.time()-t0:.0f}s)")
    print("wrote", out / "summary.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
