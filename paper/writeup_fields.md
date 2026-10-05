== TITLE ==
Every Tool Call Is a Token You Cannot Buy Back

== SUBTITLE ==
Nine design rules we measured — and mostly falsified — while building a single-model SWE agent on consumer hardware

== BODY (paste into the Writeup body) ==


**Abstract**
Coding agents are usually improved by adding inference, context, or a larger model. We instead
study the *cost structure* of a single small model (Gemma 4 31B, INT4 QAT) fixing real bugs from
an offline SWE-bench-shaped benchmark, on a 4-core/3.7 GB CPU-only machine, by replaying the
official harness end-to-end. We report three kinds of findings. (1) *Repeated-argument spinning*
is a first-class failure mode: across 55 tasks, 20 tasks contain a maximal run of ≥10
byte-identical tool calls, the longest such chain consumes 74 calls, and up to 34.6 % of the
tool budget of failing tasks is burned inside a single identical chain. (2) *Sub-agent
delegation to the same model is not free and was not beneficial*: on a paired 18-task set the
single-agent configuration passed 12/18 versus 6/18 with a delegated analyzer (McNemar exact
p = 0.070); no task failed for lack of analysis, and one delegate spent 82 of 100 tool calls.
(3) *Public-task scores do not transfer*: our local 34.5 % becomes 3/58 on the hidden board, a
transfer ratio near 1/5, matching an independent team's 28 % → 4/58. We convert these into nine
falsified-or-measured design rules and one reusable artifact: the local replay harness that
turns a public Kaggle competition into a fully offline benchmark.

**1. Introduction**
The pitch of this competition is that a model that fits on one accelerator should be able to
navigate a large repository and fix a real bug. Our setup makes that claim concrete and
adversarial: no GPU, 3.7 GB of RAM, a single 31B INT4 model served by an OpenAI-compatible
endpoint, and the *actual* competition harness replayed offline so that every measurement is
exact rather than anecdotal.

Two things make this a useful research setting rather than a diary. First, the harness is
faithfully replayable: we reconstruct Container B (patch application, test reset, pytest exit
code) and Container A (the agent loop) with the same classes the host uses, so "resolved" means
the same thing locally as on the board. Second, because the local loop is cheap, we can afford
*paired* comparisons on the same tasks — the only way to say anything about a change whose
effect is smaller than run-to-run noise.

Our central observation is that for small agents the binding constraints are not reasoning but
*bookkeeping*: how the tool budget is spent, how failures are detected, and how the agent stops.
We organise the paper around nine rules, each of which was either measured or falsified.
All numbers come from runs recorded in the accompanying open log; no result here is estimated.

**2. Method**
*Task suite.* 55 tasks from the competition's public task file (37 fastapi, 9 rich, 8 requests,
1 httpx), with their repository snapshots at the base commit. Tasks whose snapshot is not
reachable offline are excluded.

*Harness.* We instantiate the host's `EvalConfig` with a substituted `ModelRegistry` that points
at an OpenAI-compatible endpoint, and use the host's subprocess sandbox manager. Because a bare
sandbox lacks the test dependencies shipped in the real image, we interpose a provisioning step
that installs the recorded test requirements and the package itself before each run; without it
every `resolve` is a false negative (we observed `inline_snapshot` missing, exit 2). We verified
the reconstruction by replaying the ground-truth patch on 18 tasks: 18/18 resolve.

*Interface.* The agent sees nine tools (command, read, edit, write, status, submit, and three
code-graph tools) or, in the single-agent arm, the six that survive an ablation. Budgets are
expressed in wall-clock minutes, tool calls and turns. All arms use the same model.

*Measurement.* For each task we record resolved, wall time, patch size, and the full tool-call
trace. From traces we compute the *longest identical chain*: the maximal run of consecutive,
argument-identical tool calls. This is a deliberately conservative spin metric: it ignores
near-duplicates and interleaved variations, so it is a lower bound on wasted calls.

**3. Findings**

*3.1 Rule 1 — Spinning is the dominant cost, not the dominant cause.* Of 55 tasks, 20 contain an
identical chain of ≥10 calls and 12 contain ≥30. In the strongest case the chain is 74 calls and
the trace's total is 200. Inside failing tasks, up to 34.6 % of all tool calls are spent in the
single longest chain (18-task analyzer arm: 113 of 327 calls). Two mechanism-specific patterns
appear: (a) *submit-spinning* — `submit_patch()` called with an empty argument object dozens of
times in a row (74, 64, 56, 54 … calls on 11 tasks) after a rejection; and (b) *grep-spinning* —
the same `git grep` re-issued identically up to 73 times.

*3.2 Rule 2 — A stop-loss rule removes spinning but does not buy accuracy.* Adding explicit
"never repeat a call with the same arguments" instructions plus staged deadlines cut the *mean*
longest identical chain on the paired 18 tasks from 16.9 calls to 3.2, and the mean call count
from 58 to 39 — a 29 % reduction in total tool calls with no accuracy change (11 → 12, inside
the ±2-task noise). The reduction is visible in the distribution rather than in the mean alone:
under the previous generation 9 of the same 18 tasks contained a chain ≥10 calls, versus 2 after
the change. We therefore treat spinning as an *efficiency* finding, not an accuracy finding — a
sharpening of the common claim that agents loop, with a number attached.

*3.3 Rule 3 — Delegating to a second agent of the same model is a bad trade.* On the same 18
tasks: single agent 12/18, delegated analyzer 6/18, McNemar exact p = 0.070 — suggestive but not
decisive; the point estimate is a 33-point gap. The mechanism is visible in the traces: the
delegate and the parent share one tool budget, and the analyzer spent 82 of 100 calls on one
task and 40–82 calls per task across the set, while producing no task that the single agent could
not also produce (only 1 task, `fastapi_9425`, was solved by the analyzer arm alone). Delegation
also *did* increase spin in failing tasks (34.6 % versus 2.8 %), i.e. it moved budget from
editing to searching without improving the edit.

*3.4 Rule 4 — Budget is the wrong lever.* Widening it (5 → 10 minutes, 100 → 200 calls) left
accuracy unchanged across three arms (11, 11, 11 on the same 18 tasks). What the larger budget
changes is the *shape* of failure: under a 200-turn cap, 8 of 36 failures are "budget exhausted"
and 13 end with a zero-byte patch. Failed trajectories cost 151 s median against 49 s for
successful ones, and 81 % of total wall-clock is spent on tasks that fail — one failed rollout
costs about three successful ones. For a hosted evaluation whose wall-clock is capped, this is
the quantity to optimise, not the per-task limit.

*3.5 Rule 5 — A "self-verify before submit" rule produces confident empty patches.* Requiring a
pre-submission verification pass moved several tasks from "wrong patch" to "no patch". The
mechanism is a loop the rule invites: verification fails, so the agent verifies again — the
`submit_patch` chains above are its footprint. *Verification is not free, and for a small model
it competes with the edit for budget.* We report this as a falsified design rule rather than a
success.

*3.6 Rule 6 — Message-text mismatches are a real and cheap failure class.* One task fails only
because the raised string is "Query param … must be of one of the supported types" while the test
regex demands "Query parameter … must be one of the supported types" — an 8-character difference.
The correct string exists verbatim eight lines above in the same file. Similarly, one task failed
because the agent edited the file into an `IndentationError` (syntactically invalid patch), and
another because a symbol was re-exported under the wrong name (`httpx.Stream`). These are
*verifiable* failure classes with cheap countermeasures: copy sibling strings verbatim, and
require a syntax/import check after every edit.

*3.7 Rule 7 — Success is task-family-specific.* Requests: 7/8. FastAPI: 10/37. Rich: 2/9.
The pattern is not "easy repository" but "short, local, single-file diff": the solved set is
dominated by one-to-few-file changes, and 91 of the suite's gold patches touch a single file.
Generalisation claims should therefore be reported per family; a headline number hides a 3–7×
spread.

*3.8 Rule 8 — Public-task scores do not transfer to hidden tasks, and the ratio is measurable.*
On the hidden board every distinct public score is exactly floor(k/58): the board is 58 tasks,
so one task is 1.7 points. Our most extensively measured configuration solves 19/55 = 34.5 %
of public tasks and 3/58 = 5.2 % of hidden ones (ratio 1/6.6); an earlier configuration of ours
reached k = 4 (0.06), and another team on the same public suite reports 28 % locally at
k = 4 (ratio 1/4). The two ratios bracket 1/5: in round numbers, five public tasks are worth
about one hidden task. We suggest the ratio is a property of *task
distribution shift* (hidden repos are private), not of any particular agent, and that it should
be reported explicitly when public benchmarks are used for selection.

*3.9 Rule 9 — Local evaluation is trustworthy only if you provision the sandbox.* The most
dangerous entry in our log is a wrong *measurement*, not a wrong answer. (a) Without
provisioned test dependencies, correct patches read as failures: replaying one task naively
gives exit 2 from a missing test-only import, while the provisioned replay resolves it. (b) The
subprocess sandbox runs the test runner with **no timeout**, so one looping task blocks the
evaluation indefinitely and leaves an orphaned process pinned to a core (observed on a
four-core host). We therefore add (i) a provisioning step before every evaluation and (ii) a
per-task guard with a hard wall-clock and orphan reaping. We stress this because "I could not
reproduce it" is, in our experience, the most common source of false negative results in agent
research.


**4. Related Work**
Coding-agent evaluation has advanced largely by scaling models and inference budgets, most
visibly through repository-level benchmarks and their scaffolded agents [1,2] and through
deliberately scaffold-free pipelines that isolate where the gain comes from [3]. Scaffolds are
in turn built out of reasoning and self-critique loops [4,5] and multi-agent conversations [6];
the Gemma family provides the open weights that make small-model agents runnable on consumer
hardware [7]. Our contribution is orthogonal to all of these: a *cost-accounting* methodology
for fixed inference. Where the above work asks "which scaffold is best" under a growing budget,
we hold the budget fixed and ask where it actually goes, and we treat the local-vs-hidden
transfer ratio as a measured quantity rather than an assumption. Repository-graph retrieval is a
main thread of the host competition [8]; we measure that a lexical/graph localizer places the
gold file in the top 5 for 14/18 tasks (77.8 %) and first for 9/18 (50 %), yet those tasks still
fail downstream (edit, stop, submit) — which separates *localisation quality* from *resolution
rate*.

**References**
[1] C. E. Jimenez et al. SWE-bench: Can Language Models Resolve Real-World GitHub Issues?
    arXiv:2310.06770, 2023.
[2] J. Yang et al. SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering.
    arXiv:2405.15793, 2024.
[3] C. S. Xia et al. Agentless: Demystifying LLM-based Software Engineering Agents.
    arXiv:2407.01489, 2024.
[4] S. Yao et al. ReAct: Synergizing Reasoning and Acting in Language Models.
    arXiv:2210.03629, 2022.
[5] N. Shinn et al. Reflexion: Language Agents with Verbal Reinforcement Learning.
    arXiv:2303.11366, 2023.
[6] Q. Wu et al. AutoGen: Enabling Next-Gen LLM Applications via Multi-Agent Conversation.
    arXiv:2308.08155, 2023.
[7] Gemma Team, Google DeepMind. Gemma technical report / model cards, 2024-2026.
[8] Google DeepMind. The Gemma 4 Developer Agent Competition (Kaggle, 2026): public task
    suite (129 tasks), repository snapshots, code graphs and embeddings, and the official
    evaluation harness.

**5. Limitations**
Single model family and quantisation; one benchmark family; 55 of ~129 public tasks; the hidden
board is small (58 tasks, so one task = 1.7 points, and our k = 4 vs k = 3 is a two-sided
difference of 0.03, i.e. within noise); local serving is faster than the hosted accelerator in
some configurations and slower in others, so wall-clock findings are indicative, not
transferable. We report paired comparisons precisely because absolute numbers are not.

**6. Conclusion**
Building a small SWE agent is mostly not a reasoning problem; it is a resource-allocation and
measurement problem. The cheapest reliable gains came from removing machinery (a delegated
analyzer), not adding it; the largest honest gains would require a capability this hardware
cannot host. We offer the nine rules, the falsifications, and the offline replay harness, so
that the next attempt starts from a smaller search space.

**Appendix / Artifacts.** Per-task outcomes for every arm (`task_outcomes.csv`), trace-derived
spin statistics (`spin_stats.json`), and the replay driver with its provisioning and guard
modules are released with this writeup under Apache-2.0. Competition context:
https://www.kaggle.com/competitions/gemma-4-developer-agent
