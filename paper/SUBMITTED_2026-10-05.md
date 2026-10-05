# Submitted writeup (verbatim as live on Kaggle, 2026-10-05)

Canonical text = `writeup_fields.md` (TITLE / SUBTITLE / BODY).
Live URL: https://www.kaggle.com/competitions/gemma-4-developer-agent-paper/writeups/every-tool-call-is-a-token-you-cannot-buy-back
Project link: https://github.com/yosh95/g4-developer-agent-paper
License shown by Kaggle: CC BY 4.0   |   Deadline: 2026-11-12 23:59 UTC (Nov 13 08:59 JST)

## Number audit (every figure in the live text re-checked against the artifacts)

| Claim in text | Check | Result |
|---|---|---|
| 20/55 tasks with identical chain >=10 | spin_stats.json v9@55 | 20 OK |
| 12 tasks with chain >=30 | spin_stats.json v9@55 | 12 OK |
| longest chain 74, trace total 200 | trace fastapi_14791 | 74 / 200 OK |
| submit chains 74,64,56,54,54,53 on 6 tasks; 11 total | traces | OK |
| command-spinning at most 73 | trace fastapi_9425 | 73 OK |
| up to 34.6 % of calls in one chain | spin_stats.json v8_anzcap@18 | 113/327 OK |
| single 12/18 vs analyzer 6/18, McNemar p=0.070 | task_outcomes.csv | OK |
| analyzer 82/100 calls on one task | NOTES.md 21.0 / trace fastapi_14361 | OK |
| budget 5->10min, 100->200 calls: 11/11/11 | task_outcomes.csv | OK |
| 8 of 36 failures budget-exhausted; 13 zero-byte patches | v9@55 rows | 8 / 13 OK |
| 151 s vs 49 s median; 81 % wall-clock on failures | task_outcomes.csv | OK |
| requests 7/8, fastapi 10/37, rich 2/9 | task_outcomes.csv v9@55 | OK |
| 91 gold patches touch a single file | tasks.jsonl (129) | 91 OK |
| local 19/55 = 34.5 % vs board 3/58 = 5.2 % (1/6.6) | rows + leaderboard grid | OK |
| Rule 2: mean longest chain 16.9 -> 3.2; calls 58 -> 39 (29 %); chain>=10: 9 -> 2 | spin_stats.json | OK |
| localizer top-5 14/18, top-1 9/18 | localization_bench.json | OK |

Notes kept honest on purpose:
- Rule 8 states the ratio as ~1/5 explicitly *because* our own configuration gives 1/6.6 and the
  other team's gives 1/4; the text brackets the two rather than claiming a single precise value.
- Rule 2 is written as an *efficiency* result, not an accuracy result, because the accuracy change
  (11 -> 12) is inside the measured +-2-task noise.
- Two rules (5 and 2) are reported as falsified design rules.
