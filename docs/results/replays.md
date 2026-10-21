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
| DK NFL classic, 2026 wk 6 main (10 games) | 206 s | $1.63 | 122.5 | 236.3 | 83% | 150.2 | 4.56 | 82% |
| DK NFL classic, 2026 wk 3 main (13 games) | 214 s | $1.72 | 146.6 | 256.8 | 93% | 159.8 | 4.79 | 75% |
| DK NFL showdown, 2026 wk 6 DAL@GB | 86 s | $0.24 | 70.3 | 117.3 | 35% | 105.4 | 4.48 | 67% |
| FD NFL classic, 2026 wk 6 main (10 games) | 170 s | $1.30 | 109.3 | 215.2 | 61% | 150.8 | 4.23 | 85% |
| FD NFL single game, 2026 wk 1 NE@SEA | 85 s | $0.25 | 43.0 | 95.1 | 37% | 75.8 | 4.82 | 90% |

Points gained (+) or lost (−) by each step, in actual points (alternatives are not steps):

| Slate | Baseline → blended numbers | → contest objective + strategist | Checker | Forecast-only lineup | Draft A / B / C |
|---|---:|---:|---:|---:|---|
| DK NFL classic, 2026 wk 6 main (10 games) | +16.8 | -19.0 | +0.0 | 132.7 | 113.1 / 122.5 / 119.9 |
| DK NFL classic, 2026 wk 3 main (13 games) | +32.2 | +3.9 | +0.0 | 142.7 | 142.7 / 146.64 / 138.44 |
| DK NFL showdown, 2026 wk 6 DAL@GB | -37.6 | +0.0 | +0.0 | 70.28 | 73.88 / 70.28 / 86.96 |
| FD NFL classic, 2026 wk 6 main (10 games) | -24.4 | +0.0 | +0.0 | 109.28 | 116.8 / 109.28 / 109.28 |
| FD NFL single game, 2026 wk 1 NE@SEA | -16.5 | +0.0 | +0.0 | 61.45 | 43.05 / 43.05 / 61.45 |

Totals: 5 slates, 12.7 minutes of wall clock, $5.14 of model calls; every final lineup
passed the independent legality check; 0 of 5 reached the simulated field's top-2.3% cut.

What this shows: the full pipeline runs on real site files, legally, in minutes, for a dollar or two per
slate. What it does not show: an edge. At the lineup level five slates are noise (see the step column);
showing even a 5% top-2.3% rate takes on the order of 250 independent slates. Player-level evidence
converges far faster: see `forecast_experiment_2026_wk3-6_blind_rescored.md`.
