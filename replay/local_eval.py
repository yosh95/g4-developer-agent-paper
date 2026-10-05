#!/usr/bin/env python3
"""Run the REAL swegemma harness locally against a submission bundle, using
OpenRouter's google/gemma-4-31b-it as the model backend instead of a local vLLM
server.  This supplies the "Phase 1" agent-execution loop that previously
required a Kaggle GPU notebook.

The harness image ships every test dependency; a local subprocess sandbox does
not, so each sandbox is provisioned the same way replay.py does it (validated:
oracle 18/18).  Without this, failures like `ModuleNotFoundError: inline_snapshot`
are false negatives.

Usage:
  ./work/harness_venv/bin/python local_eval.py --tasks rich_4077 --arm submission_v5
  ./work/harness_venv/bin/python local_eval.py --tasks rich_4077 --arm gold
"""
from __future__ import annotations
import argparse, asyncio, json, os, shutil, subprocess, sys, time
from pathlib import Path

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")
os.environ.setdefault("LITELLM_LOG", "ERROR")
import litellm
litellm.drop_params = True

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "work/local_eval/data"
WHEELS = ROOT / "work/wheels"
OR_BASE = "https://openrouter.ai/api/v1"

from adk_submission import ModelRegistry
from google.adk.agents.context_cache_config import ContextCacheConfig
from google.adk.apps._configs import EventsCompactionConfig
from google.adk.models.lite_llm import LiteLlm
from swegemma.config import EvalConfig, build_submission_limits
from swegemma.deduplication import resolve_task_snapshot_paths
from swegemma.evaluate import Evaluator
from swegemma.harness.verification import verify_task
from swegemma.models import load_tasks
from swegemma.sandbox.subprocess import SubprocessManager as BaseSubprocessManager

DEFAULTS = {"timeout_seconds": 300, "max_tool_calls": 100,
            "max_time_minutes": 60.0, "max_turns": 500}

# Same list as replay.py.  `uv` resolves these from the network; the sandboxes are
# not air-gapped locally, unlike the real scoring sandbox.
TEST_DEPS = [
    "pytest", "pytest-timeout==2.1.0", "typer", "pluggy", "iniconfig", "packaging",
    "dirty-equals", "inline-snapshot", "PyJWT", "python-multipart", "email-validator",
    "pwdlib", "asgiref", "trio", "coverage", "flask", "sqlmodel", "httpx", "requests",
    "pyyaml", "ujson", "orjson", "sniffio", "idna", "h11", "httpcore", "click",
    "pygments", "rich", "shellingham", "typing-extensions", "markdown-it-py", "mdurl",
    "anyio", "werkzeug", "jinja2", "markupsafe", "itsdangerous", "sortedcontainers",
    "outcome", "annotated-types", "colorama", "executing", "asttokens", "tomli",
    "black", "httpbin", "pytest-httpbin", "six", "decorator", "brotlicffi", "cffi",
    "pycparser", "trustme", "pytest-asyncio", "psutil", "setuptools", "wheel",
    "blinker", "pytest-cov", "py", "toml", "mock", "betamax", "charset-normalizer",
    "certifi",
]
REPO_DISTS = {"rich", "requests", "httpx", "fastapi", "starlette", "flask", "typer"}


def _sh(cmd: str, cwd: Path, timeout: int = 900):
    return subprocess.run(["bash", "-lc", cmd + " 2>&1 | tail -3"], cwd=str(cwd),
                          capture_output=True, text=True, timeout=timeout)


def prepare_env(paths: dict, repo: str, verbose: bool = True) -> None:
    """Provision a sandbox venv with the repo (editable) + test helpers."""
    ws = Path(paths["workspace"])
    py = str(Path(paths["venv"]) / "bin" / "python")
    if not Path(py).exists():
        if verbose:
            print("   env: no venv at %s" % py, flush=True)
        return
    deps = [d for d in TEST_DEPS
            if d.lower().replace("_", "-").split("==")[0] not in {repo.lower()}]
    for cmd in ("uv pip install --python %s --no-deps " % py + " ".join(deps),
                "uv pip install --python %s -e ." % py):
        try:
            r = _sh(cmd, ws)
            if verbose:
                print("   env: %-46s rc=%s %s" % (cmd[:46], r.returncode,
                      (r.stdout or "").strip().replace("\n", " ")[-130:]), flush=True)
        except Exception as e:
            if verbose:
                print("   env: %s FAILED %s" % (cmd[:46], e), flush=True)


class ProvisionedSubprocessManager(BaseSubprocessManager):
    """SubprocessManager that provisions each new sandbox like the scoring image.

    Both the agent sandbox (Container A) and the verification sandbox (Container B)
    go through start(), so hooking start() covers both.  active_repo is set per task.
    """

    active_repo: str = "fastapi"

    def start(self) -> str:
        sid = super().start()
        try:
            prepare_env(self.sandboxes[sid], self.active_repo)
        except Exception as e:  # never let env prep kill the run
            print("   env prep failed: %s: %s" % (type(e).__name__, e), flush=True)
        return sid


def read_eval_config(bundle: Path) -> dict:
    p = bundle / "eval_config.yaml"
    if not p.exists():
        return dict(DEFAULTS)
    import yaml
    ev = (yaml.safe_load(p.read_text()) or {}).get("evaluation") or {}
    return {**DEFAULTS, **{k: ev[k] for k in DEFAULTS if k in ev}}


def model_registry(model: str, base_url: str = OR_BASE,
                   api_key_env: str = "OPENROUTER_API_KEY") -> ModelRegistry:
    """Register every alias the harness may ask for against one OpenAI-compatible endpoint.

    Default endpoint is OpenRouter (google/gemma-4-31b-it).  Point --base-url at
    https://ollama.com/v1 with --model gemma4:31b to use the free Ollama Cloud quota.
    """
    reg = ModelRegistry()
    api_key = os.environ.get(api_key_env) or os.environ["OPENROUTER_API_KEY"]
    # Ollama Cloud names the model "gemma4:31b"; OpenRouter needs the "google/" prefix.
    names = [model, "google/" + model] if not model.startswith(("google/", "openai/")) else [model]
    backend = {n: LiteLlm(model="openai/" + n, api_base=base_url,
                          api_key=api_key, num_retries=3) for n in names}
    for alias in ["gemma-4-31b-it-qat-w4a16-ct", "gemma-4-31b-it", "gemma-4-27b-it",
                  "gemma-4-26b-a4b-it", "gemma-4-12b-it", "gemma-4-9b-it",
                  "gemma-4-e4b-it", "gemma-4-e2b-it"]:
        reg.register(alias, backend[names[0]])
    return reg


def make_evaluator(bundle: Path, results: Path, model: str, budget: dict, sandbox_root: Path,
                   base_url: str = OR_BASE, api_key_env: str = "OPENROUTER_API_KEY"):
    limits, gen = build_submission_limits()
    cfg = EvalConfig(
        tasks_path=DATA / "tasks.jsonl", snapshots_dir=DATA / "snapshots",
        results_dir=results, submission_dir=bundle, models=model_registry(model, base_url, api_key_env),
        sandbox="subprocess", timeout_seconds=budget["timeout_seconds"],
        max_time_minutes=budget["max_time_minutes"], max_tool_calls=budget["max_tool_calls"],
        max_turns=budget["max_turns"], limits=limits, generation_constraints=gen,
        adapter_manifest=None, wheels_dir=WHEELS if WHEELS.is_dir() else None,
        context_cache_config=ContextCacheConfig(min_tokens=2048, ttl_seconds=1800, cache_intervals=10),
        events_compaction_config=EventsCompactionConfig(compaction_interval=5, overlap_size=2,
                                                        token_threshold=14336, event_retention_size=5),
        verbose=False, display_mode="quiet", enable_sandbox_testing=True)
    ev = Evaluator(cfg)
    mgr = ProvisionedSubprocessManager(timeout_seconds=budget["timeout_seconds"])
    mgr.base_dir = sandbox_root
    ev.sandbox = mgr
    ev.docker = mgr
    return ev


async def run_one(ev, arm, task):
    if arm == "gold":
        snap, base_snap, patch_path = resolve_task_snapshot_paths(
            ev.config.snapshots_dir, task.instance_id, task.repo)
        return await verify_task(ev.docker, ev.config, task, snap, base_snapshot_path=base_snap,
                                 patch_path=patch_path, agent_patch=task.patch,
                                 start_time=time.perf_counter())
    return await ev.evaluate_task(task=task, task_index=1, total_tasks=1)


async def main(a):
    bundle = ROOT / "submission_v5" if a.arm == "gold" else (ROOT / a.arm)
    budget = read_eval_config(bundle)
    if a.max_minutes:
        budget["max_time_minutes"] = a.max_minutes
    if a.max_calls:
        budget["max_tool_calls"] = a.max_calls
    print("arm=%s bundle=%s model=%s" % (a.arm, bundle.name, a.model))
    print("budget=%s" % budget, flush=True)

    tasks = {t.instance_id: t for t in load_tasks(DATA / "tasks.jsonl")}
    results = ROOT / "results/local_eval" / (a.arm + ("@" + a.tag if a.tag else ""))
    results.mkdir(parents=True, exist_ok=True)
    sandbox_root = ROOT / "work/local_eval/sandboxes"
    sandbox_root.mkdir(parents=True, exist_ok=True)

    rows = []
    for tid in a.tasks:
        task = tasks[tid]
        repo = task.repo.rsplit("/", 1)[-1]
        t0 = time.time()
        ev = make_evaluator(bundle, results, a.model, budget, sandbox_root, a.base_url, a.api_key_env)
        ev.docker.active_repo = repo
        try:
            res = await asyncio.wait_for(run_one(ev, a.arm, task),
                                         timeout=budget["max_time_minutes"] * 60 + 1200)
            patch = res.agent_patch or ""
            row = {"id": tid, "repo": repo, "resolved": bool(res.resolved),
                   "exit": res.test_exit_code, "error": (res.error or "")[:400],
                   "wall": round(time.time() - t0, 1), "patch_bytes": len(patch),
                   "test_tail": (res.test_output or "")[-2500:]}
            if patch:
                p = results / "patches"
                p.mkdir(parents=True, exist_ok=True)
                (p / (tid + ".patch")).write_text(patch)
        except asyncio.TimeoutError:
            row = {"id": tid, "repo": repo, "resolved": False, "patch_bytes": 0,
                   "error": "driver timeout", "wall": round(time.time() - t0, 1)}
        except Exception as e:
            import traceback
            row = {"id": tid, "repo": repo, "resolved": False,
                   "patch_bytes": 0, "error": "%s: %s" % (type(e).__name__, e),
                   "wall": round(time.time() - t0, 1), "tb": traceback.format_exc()[-2000:]}
        rows.append(row)
        print("[%s] resolved=%s exit=%s patch=%sB wall=%ss %s" % (
            tid, row["resolved"], row.get("exit"), row.get("patch_bytes"),
            row["wall"], row.get("error", "")[:180]), flush=True)
        (results / "rows.jsonl").open("a").write(json.dumps(row) + "\n")
        if not a.keep:
            shutil.rmtree(sandbox_root, ignore_errors=True)
            sandbox_root.mkdir(parents=True, exist_ok=True)

    safe_arm = a.arm.replace("/", "__").replace("@", "_at_")
    (results / ("summary_%s_%d.json" % (safe_arm, int(time.time())))).write_text(
        json.dumps(rows, indent=1))
    n = len(rows)
    done = sum(1 for r in rows if r["resolved"])
    print("=== %s: %d/%d resolved = %.2f%%" % (a.arm, done, n, 100.0 * done / max(n, 1)))
    for r in rows:
        print("   ", r["id"], r["resolved"], r.get("error", "")[:140])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", nargs="+", required=True)
    ap.add_argument("--arm", default="submission_v5", help="bundle dir name, or 'gold'")
    ap.add_argument("--model", default="google/gemma-4-31b-it")
    ap.add_argument("--base-url", default=OR_BASE,
                    help="OpenAI-compatible base url. Use https://ollama.com/v1 with --model gemma4:31b")
    ap.add_argument("--api-key-env", default="OPENROUTER_API_KEY")
    ap.add_argument("--tag", default=None, help="suffix for the results dir, e.g. 'ollama'")
    ap.add_argument("--max-minutes", type=float, default=None)
    ap.add_argument("--max-calls", type=int, default=None)
    ap.add_argument("--keep", action="store_true")
    asyncio.run(main(ap.parse_args()))
