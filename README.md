# Every Tool Call Is a Token You Cannot Buy Back

Artifacts for the paper submitted to **Google – The Gemma 4 Developer Agent Paper Track**
(Kaggle, 2026).  The paper reports *cost accounting* for a single small coding agent
(Gemma 4 31B, INT4 QAT) fixing real bugs from an offline SWE-bench-shaped benchmark on a
4-core / 3.7 GB CPU-only host.  Everything here is reproducible without any competition
data: only aggregate metrics (per-task outcome rows derived from our runs) are included.

## Contents

```
paper/
  writeup_draft.md        the paper (camera-ready body, ~2,100 words)
  writeup_fields.md       same text split into Kaggle Title / Subtitle / Body fields
  fig1_cover.png          figure: local resolution rate vs the 58-task hidden board
  fig2_spin.png           figure: fraction of tool calls spent inside identical-call chains
  task_outcomes.csv       per-task outcome for 5 configurations (182 rows)
  spin_stats.json         trace-derived spin / budget statistics per configuration
replay/
  analyze_traces.py       spin metric (longest run of byte-identical tool calls) + per-task table
  compare_arms.py         paired side-by-side comparison of two or more runs
  guard_run.py            per-task wall-clock guard + orphan reaping for hung evaluations
  replay.py               offline re-implementation of the official verification phase
  local_eval.py           driver that runs the agent loop against any OpenAI-compatible endpoint
```

## Key numbers (all measured)

| Claim | Value |
|---|---|
| Identical-call chains (spin) | 20/55 tasks have a chain ≥ 10; longest chain 74 calls |
| Budget burned inside one identical chain | up to 34.6 % of all calls of a failing configuration |
| Delegation to a second agent of the same model | 12/18 (single) vs 6/18 (delegated), paired; McNemar exact p = 0.070 |
| Widening the budget (5→10 min, 100→200 calls) | 11/11/11 on the same 18 tasks |
| Cost of failure | 151 s vs 49 s median wall-clock; 81 % of total time spent on failing tasks |
| Local → hidden transfer | 34.5 % local ≈ 6.9 % on the hidden board (≈ 1/5), matching an independent team |
| Localizer quality (separate from resolution) | gold file in top-5 for 14/18 tasks, top-1 for 9/18 |

## Reproducing the spin metric

`analyze_traces.py` needs only a directory of traces and no network access:

```bash
python replay/analyze_traces.py path/to/run_dir        # expects run_dir/traces/trace_*.json
# e.g. on the run reported in the paper:
#   55 tasks, 4759 tool calls; calls inside longest identical chain: 843 (17.7%)
#   tasks with a chain >= 10: 20
```

A trace is a JSON object `{"steps": [{"tool_calls": [{"function_name":..., "arguments":...}],
...}]}`; the harness that emits it is the competition's own agent runner.  `spin_stats.json`
contains the same statistics for every configuration discussed in the paper, so the table in
Section 3 can be checked without re-running anything.

## Notes on what is *not* here

Task statements, repository snapshots, test patches and raw traces are competition data and
are **not redistributed** (competition rules prohibit redistribution to non-participants).
`task_outcomes.csv` contains only identifiers, repository names and outcome metrics.  Re-running
the agent loop yourself requires joining the competition and obtaining the public task suite.

## License

Apache-2.0 (see `LICENSE`).  If you use these artifacts, please cite the Kaggle writeup.
