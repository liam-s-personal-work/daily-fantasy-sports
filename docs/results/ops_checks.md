# Operational checks (2026-10-21)

Measurements quoted in `docs/PLAN.md` that do not come from a backtest report, with how they were taken.

## 150-entry portfolio on a real DK file

- **Input.** The real DK week 3 2026 entries file (`DKEntriesClassic.csv`, one entry). Its entry row was
  copied to 150 entry ids. The real files are kept locally in `data/cache/real_samples/`, not in git.
- **Command.**
  `dfs run DKEntries150.csv --no-web --no-forecast --replay --season 2026 --week 3 --now 2026-09-27T15:00:00+00:00`
- **Model calls.** With `--replay` and an empty cache, every model stage fails over. This tests the
  slowest code path: the portfolio is built with no agent help, labelled "no review".

**Results:**

| Measure | Value |
|---|---|
| Wall clock | 68 s, of which the field, candidates and 150-lineup portfolio took 47 s |
| Lineups | 150 rows, 150 distinct |
| Most-used player | 56 of 150 lineups (37%) |
| Legality | every lineup passed the independent legality check |
| Warnings | the run said the learned weights were fit on week 3 and used the priors instead |

## Last call, live (DK showdown DAL@GB, 2026 week 6)

- **Run.** `dfs lastcall <run> --now 2026-10-18T19:30:00+00:00`, on a showdown run with live web news.
- **Cost.** 1 news call, 3 web searches, $0.096; wall clock 35 s (terminal timing).
- **Outcome.** No pick was out, so the lineup was unchanged. The full output is in
  `lastcall_smoke_dal_gb.json`.
- **Caveat.** This ran after the game had been played, so the news it found is post-game. It measures
  time and cost only. Since then, a last call with a past `--now` turns web news off unless you pass
  `--allow-lookahead`.

## The inactives leak in the first forecaster experiment

The first run (`forecast_experiment_2026_wk3-6_blind_v1_inactives_leak*`) showed the forecaster each
player's game-day roster status. On past weeks, that status is `INA` for players declared inactive about
90 minutes before kickoff.

- **Rows affected.** Matching the v1 re-scored rows to that week's nflverse roster status gives 39 player-games with
  `INA`.

| Rows | n | MAE baseline | MAE forecast | Best forecast weight |
|---|---:|---:|---:|---:|
| all | 1,504 | 4.272 | 4.222 | 0.31 |
| without the 39 `INA` rows | 1,465 | 4.267 | **4.286** | 0.20 |

- **An independent cut.** The pre-merge review took rows where the forecaster gave p(not playing) ≥ 0.8 and
  the baseline < 0.8: 24 rows. Those rows carry 74.1 of the forecaster's 75.9 points of total absolute-error
  gain.
- **The fix and the re-run.** Inactive status is now shown only from 90 minutes before each kickoff. The
  clean re-run is `forecast_experiment_2026_wk3-6_blind.md`.

## Correlations on the real replay slates

These are the matrices built for the five replays in `replays.md`. Starters carry the role correlations;
backups get a quarter of them.

| Slate | Players | Smallest eigenvalue | QB1–WR1 correlation, mean over teams |
|---|---:|---:|---:|
| DK classic wk 6 | 306 | 0.235 | 0.350 |
| DK classic wk 3 | 433 | 0.227 | 0.350 |
| DK showdown wk 6 | 35 | 0.226 | 0.350 |
| FD classic wk 6 | 308 | 0.234 | 0.350 |
| FD single game wk 1 | 38 | 0.222 | 0.350 |

- **No repair needed.** Every matrix is positive definite.
- **Before the fix.** Every listed depth player got the full role correlation. The raw matrices had smallest
  eigenvalues of −0.75 to −1.03, and the repair shrank QB1–WR1 to about 0.27.

## Candidate pruner

- **Committed test.** `test_pruner_is_exact_on_means_and_draws` checks the pruned solve against the full
  solve on the means and 12 simulated draws.
- **Randomised probe.** The field/selection review ran a larger probe that was not committed: 360 solves
  across DK classic, forced QB stacks, FD max-4-per-team / min-3-teams, 2-game and showdown slates. None
  was suboptimal.
