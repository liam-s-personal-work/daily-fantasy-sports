# Real-slate replays (2026 NFL)

Each replay runs the whole pipeline on a real DraftKings/FanDuel salary file with live Claude agents
(forecaster, strategist, checker, backup), as of the morning of the games. Web research is off (searching
today would surface the results); facts come from point-in-time nflverse data, and the model's training
data ends before the 2026 season. Blend weights are the user's priors (0.85) and the forecaster's ranges
are uncalibrated, because the learned values were fit on these same weeks. Graded with
`scripts/replays_report.py` against actual DK/FD points from nflverse box scores. "Simulated field" is
our field model, not the real contest (no standings files were available).

| Slate | Wall clock | Model cost | Lineup pts | Best possible | Beat sim. field | Sim. top-2.3% cut | Player MAE | 80% band coverage |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| DK NFL classic, 2026 wk 6 main (10 games) | 214 s | $1.63 | 145.5 | 236.3 | 97% | 146.6 | 4.52 | 81% |
| DK NFL classic, 2026 wk 3 main (13 games) | 228 s | $1.79 | 151.1 | 256.8 | 95% | 157.8 | 4.72 | 75% |
| DK NFL showdown, 2026 wk 6 DAL@GB | 101 s | $0.28 | 87.1 | 117.3 | 78% | 105.4 | 4.62 | 72% |
| FD NFL classic, 2026 wk 6 main (10 games) | 154 s | $1.53 | 130.8 | 215.2 | 95% | 140.2 | 4.23 | 82% |
| FD NFL single game, 2026 wk 1 NE@SEA | 97 s | $0.27 | 42.6 | 95.1 | 33% | 73.6 | 4.72 | 90% |

Points gained (+) or lost (−) by each step, in actual points (alternatives are not steps):

| Slate | Baseline → blended numbers | → contest objective + strategist | Checker | Forecast-only lineup | Draft A / B / C |
|---|---:|---:|---:|---:|---|
| DK NFL classic, 2026 wk 6 main (10 games) | -10.1 | +19.0 | +0.0 | 136.8 | 126.5 / 145.46 / 137.5 |
| DK NFL classic, 2026 wk 3 main (13 games) | -13.1 | +9.3 | +0.0 | 151.1 | 142.7 / 151.1 / 143.24 |
| DK NFL showdown, 2026 wk 6 DAL@GB | -4.4 | +16.8 | +0.0 | 70.28 | 70.28 / 87.07 / 86.96 |
| FD NFL classic, 2026 wk 6 main (10 games) | +12.5 | +6.2 | +0.0 | 130.78 | 124.58 / 130.78 / 134.08 |
| FD NFL single game, 2026 wk 1 NE@SEA | +18.8 | -18.8 | +0.0 | 51.35 | 51.75 / 42.64 / 47.4 |

Totals: 5 slates, 13.2 minutes of wall clock, $5.49 of model calls; every final lineup
passed the independent legality check; 0 of 5 reached the simulated field's top-2.3% cut.

What this shows: the full pipeline runs on real site files, legally, in minutes, for a dollar or two per
slate. What it does not show: an edge. At the lineup level five slates are noise (see the step column);
showing even a 5% top-2.3% rate takes on the order of 250 independent slates. Player-level evidence
converges far faster: see `forecast_experiment_2026_wk3-6_blind.md`.
