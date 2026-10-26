# How close to the best possible lineup? (2026-10-26)

The question: when you ask for a lineup, is it the highest-scoring lineup that could have been
entered? This page measures that on held-out historical slates, for NBA, NFL and college football,
and records which changes moved it.

## What "perfect" means here, and why it can't be promised

- **The target.** The ex-post optimal lineup: the legal lineup (roster slots, salary cap, players
  from 2+ games) with the most actual DK points, solved exactly after the games.
- **The metric.** `% of optimal` = actual points of the lineup we would have entered ÷ actual points
  of that optimum. 100% is perfect.
- **Why 100% can't be guaranteed.** The optimum is decided by who plays, for how long, and a
  per-minute (or per-touch) outcome nobody knows before tip. Even if every projection were exactly
  right on average, the best lineup on the night is the one whose players ran hot, and that is
  noise from before the game's point of view. Three checks on this:
  - In the selection experiments, every one of 3,000 simulated NFL Sundays (drawn from the engine's
    own projections) had a different optimal lineup. The lineup that was most often best across 3,000
    fresh simulations was best in 0.4% of them.
  - On real NBA nights, knowing every player's exact minutes still never produced the optimum
    (ceilings, below).
  - No method in any experiment hit the exact optimum on any held-out slate (0 of 169 NBA nights,
    0 of 24 NFL weeks).
- **So the goal is the highest expected score**, which is the lineup that gets closest to the optimum
  on average. Everything below is scored on that.

## The harness

- **Code:** `dfs/oracle.py` (exact MILP through scipy's HiGHS, the same rules as the site),
  `scripts/oracle/evaluate.py` (scores any set of projection columns on every slate),
  `scripts/oracle/{nba,nfl,cfb}_slates.py` (historical slates).
- **Projections are point-in-time.** Each slate's projections use only games before that slate's
  first tip or kickoff, through the same code the live engine runs.
- **Salaries are DK-like, not DK's.** Historical DK salaries aren't available from a source that
  allows automated access, so each sport has a salary model fit on real DK salary files
  (`scripts/oracle/*_salary.py`) from what DK can see: season and recent DK points per game, not
  our projections. NBA fit: R² 0.78.
- **Who is in the pool.** NBA: everyone on tonight's box score except players out with a stated
  reason (injury, illness, rest, suspension), since those are on the injury report before tip.
  Coach's-decision DNPs stay in, at 0 points. NFL: the top players at each position per team by
  the engine's own pre-game ranking (QB 1, RB 2, WR 3, TE 1) plus every DST, so pool membership
  doesn't depend on who played. College: the same idea per team.
- **Statistics.** Differences are in percentage points of optimal, with 95% paired bootstrap
  intervals over slates. A per-slate difference has a standard deviation of 4–8 points, so small
  effects need many slates.
- **Protocol.** Every learned model trains on earlier seasons only and is scored on later ones:
  - NBA: train 2024-25; test 2025-26 and 2026-27 (169 nights).
  - NFL: train 2021-23; validate 2024; test 2025-26 (24 weeks).
  - College: train 2024 and 2025; test 2026 weeks 2-8 (7 slates, too few to settle much).
- **Audits.** Every experiment had an independent audit that tried to break it (leakage, training on
  test, joins, slate selection). What they found is in the sections below.

## NBA

Held-out nights: 2025-26 and 2026-27 (169 nights; the 2026 All-Star night has no legal lineup). Learned
models are trained on 2024-25 only. On these nights the best possible lineup averaged 414.6 DK points.

| Projection | % of optimal | DK points | vs engine [95% CI] | nights ≥ 80% of optimal | Shipped |
|---|---:|---:|---:|---:|:---:|
| Site's own average (DK points per game) | 64.4% | 266.0 | −1.83 [−3.25, −0.48] | 3.6% | |
| Engine: the stats baseline before this work | 66.3% | 273.6 | | 4.7% | |
| **+ learned minutes model** | **70.4%** | **290.8** | **+4.12 [+2.80, +5.49]** | 11.2% | **yes** |
| **+ confirmed starting fives (last call)** | **71.1%** | **294.4** | **+4.88 [+3.55, +6.26]** | 13.6% | **yes** |
| Usage model: absent players' minutes and shots redistributed | 71.2% | 294.8 | +4.97 [+3.59, +6.41] | | no |
| Average of the minutes and usage models | 71.2% | 294.2 | +0.79 [−0.14, +1.76] vs minutes | | no |

The learned minutes model:

- **Code.** `dfs/projections/nba_minutes.py`, trained by `scripts/fit_nba_minutes.py`. Two LightGBM
  stages: P(plays), then minutes if he plays. Points = P(plays) × minutes × the engine's per-minute rate.
- **Features**, all from games before tonight's first tip:
  - the engine's own numbers;
  - minutes in the team's last 1/3/5 games and the share he started;
  - his rank on tonight's roster;
  - minutes vacated by absent regulars;
  - his minutes with and without each absent regular;
  - how often he has sat after playing the night before;
  - both teams' point differential.
- **Live.** It runs after research, so research statuses still apply on top (`NBAProvider.after_research`).
  The production model is retrained on all three seasons.
- **Last call.** When a team's starting five is confirmed (cited, about 30 minutes before tip), that team
  switches to the starter-aware tier.
- **The last-call tier is noisier.** Over the six 2026-27 nights it is the weakest column (65.5%
  against the engine's 67.3%). Its gain comes from 2025-26.
- **Early season,** which is now: over the 26 opening nights (the first 20 of 2025-26 plus the first 6
  of 2026-27), the main model is +3.04 [+0.84, +5.46] and last call +3.11 [+1.14, +4.97]. Over the
  rest of 2025-26 they are +4.32 and +5.20.
- **One night with real DK prices** (2026-10-23, the full engine run with structured data only):

  | Projection | DK points | % of the optimum (383.0) |
  |---|---:|---:|
  | Site's average | 191.0 | 49.9% |
  | Engine | 229.5 | 59.9% |
  | With the minutes model | 247.5 | 64.6% |
  | With the minutes and rate models (second round) | 217.8 | 56.9% |

  One night is anecdote, not evidence. The minutes model pointed the right way here. The rate model,
  added in the second round, pointed the wrong way: it swapped Dyson Daniels (44.2) and Jalen Suggs
  (33.5) for Ausar Thompson, Kyshawn George and Jonathan Kuminga. A single night's difference has a
  standard deviation of about 7 points, so this says little against the 169-night holdout (+0.55).
- `python3 scripts/fit_nba_minutes.py --holdout` reproduces the two shipped rows above from scratch.
- **Expect about +3.7, not +4.1.** Five other random seeds of the same main model score +3.64 to +3.73
  (69.9–70.0% of optimal), so the shipped fit's +4.12 is a lucky draw of about 0.4. Averaging five seeds
  doesn't help: +3.47 for main, +4.83 for last call (shipped last call: +4.88).

**What didn't help:**

- **A direct points model** (LightGBM on DK points, 48 features): +3.05 [+1.49, +4.63] over the engine.
  That was measured on the earlier pool with injured players dropped afterwards, so it is not directly
  comparable. It is weaker than the minutes model and was not shipped.
- **A learned per-minute rate** on top of the learned minutes: −0.27 against the minutes model alone
  (earlier pool, audit rerun).
- **Retraining every month** (walk-forward): −0.23 against the single 2024-25 fit (earlier pool).
- **The usage model.** It is as good as the minutes model on its own. The average of the two is +0.79
  [−0.14, +1.76] over the minutes model, and worse on the six 2026-27 nights. That is not enough to carry
  a second live model.

**Ceilings.** The engine scored again with one piece replaced by the truth:

| If we had known exactly… | % of optimal | nights ≥ 90% | exact optimum |
|---|---:|---:|---:|
| nothing (engine) | 66.3% | 1.2% | 0 of 169 |
| who plays (the injury report is already known) | 66.5% | 1.2% | 0 |
| every player's minutes | 82.8% | 12.4% | 0 |
| every player's points per minute | 86.7% | 39.6% | 0 |
| both | 100% | 100% | 169 |

Even with the exact minutes, or the exact per-minute scoring, no night lands on the optimum. The
optimum needs both, and per-minute scoring (shooting luck) is the larger half. The minutes model closes
about a quarter of the minutes gap: 66.3% → 70.4% of a possible 82.8%.

**Selection rules** (2025-26 + 2026-27, engine projections, simulated correlated nights with
correlations fit on 2024-25):

| How the lineup is chosen | % of optimal | vs max expected points |
|---|---:|---:|
| Max expected points | 66.26% | |
| Optimal in the most simulated nights | 63.75% | −2.51 [−3.81, −1.15] |
| Wins most often in 2,000 fresh simulated nights | 66.78% | +0.52 [−0.11, +1.18] |
| Bonus for P(in the optimal lineup), λ tuned on 2024-25 | 65.84% | −0.43 [−0.95, +0.09] |
| 2+ players from the highest-total game | 65.68% | −0.58 [−1.32, +0.19] |
| Max expected share of the optimum | 65.92% | −0.34 [−1.22, +0.55] |

Nothing beats max expected points, so that is now the default objective (`--objective points`).

**The chosen lineups' projections are honest.** The minutes model's lineups were projected at 292.2
points and scored 290.8 on average; the engine's, 276.5 and 273.6. Picking the top of a noisy list
usually overstates it ("winner's curse"), but here that costs 1–3 points, so shrinking projections has
little to recover. The site's own average, by contrast, projected 283.0 and scored 266.0.

## Pushing for 85% (2026-10-26, second round)

The target was 85% of the optimum. The ceilings above already say one lineup can't get there: exact
minutes give 82.8%. Four more experiments ran in parallel, each audited by a separate agent that tried to
break it. All four audits came back clean.

- **The protocol was stricter this round.** Training used 2024-25 only. Every setting was chosen on the
  68 nights before 2026-01-01. The test was the remaining 101 nights (2026-01-01 on), each column scored
  once.
- **On those nights:** engine 65.9%, shipped minutes model 70.6%, last call 71.1%.

| Experiment | Test result | Shipped |
|---|---|:---:|
| Minutes model v2: 2023-24 added as training data, 23 new features (minutes ramp after an absence, volatility, fatigue, foul rate, blowout risk, pace, rotation shape) | −0.08 [−0.98, +0.78] vs the same model refit with the same seeds. Minutes error −0.5%; lineups didn't move. | no |
| Per-minute rate model on top of the minutes model (WOWY with absent teammates, usage redistribution, opponent per-minute defence by position, pace, role, recency), heavily regularised | +1.76 [+0.66, +2.78] vs the same minutes with the engine's rate. +0.80 [−0.26, +1.84] on the shipped minutes. | yes (small) |
| Learn from real optimal lineups: P(in the optimum), upside quantiles, value thresholds | Best rule: the learned 75th percentile, +0.85 [−0.55, +2.21]. P(in the optimum) alone: −0.99. Value thresholds: −8 to −17. | no |
| Several entries (best of N) | See below | yes |

**The rate model on the shipped code path.** `scripts/fit_nba_minutes.py --holdout` trains on 2024-25 and
scores all 169 nights of 2025-26 and 2026-27:

| Column | % of optimal | vs minutes model |
|---|---:|---:|
| Minutes model | 70.38% | |
| Minutes model × rate model | 70.93% | +0.55 [−0.30, +1.38] |
| Last call | 71.14% | +0.76 [−0.23, +1.80] |
| Last call × rate model | 71.87% | +1.49 [+0.37, +2.63] |

- **Every comparison points the same way.** The model also scales a player's points per minute by at
  most ±16%, so it ships. But on its own it is not significant over the shipped minutes model: +0.55
  on the main tier and +0.73 [−0.11, +1.56] on last call.
- **Its larger +1.76 was measured against a typical-seed minutes model.** The shipped fit is the lucky one.
- **The port reproduces the experiment exactly:** all 60 feature columns on 63,354 rows, and the same
  lineup on all 101 test nights.
- **It also fixed a look-ahead in the experiment.** Whether a player appears in his team's season table
  told the model whether he plays for the team later. It affected 130 rows, none of which played, and
  changes no test lineup.
- **Live:** `dfs/projections/nba_rate.py`, `data/learned/nba_rate_*`. If its files are missing or it
  fails, the minutes model's numbers stand and the run log says so.

**Why the optimum can't be learned.** In 2024-25 the players in each night's optimal lineup were the
slate's 30th-best projection on the median (middle half: 11th to 62nd). They scored 52 points against 32
projected. The optimum is made of players who beat their projection by about 20 points each, and before
tip that looks like noise.

**Several entries.** The night's score here is your best entry ÷ the optimum. DK's mini-MAX allows 150
entries at $0.50.

| Entries | Cost | Best entry, % of optimal | Nights ≥ 85% |
|---:|---:|---:|---:|
| 1 | $0.50 | 70.6% | 5% |
| 3 | $1.50 | 74.4% | |
| 5 | $2.50 | 75.7% | |
| 10 | $5 | 77.4% (+6.8 [+5.8, +7.7]) | 12% |
| 20 | $10 | 79.1% | 13% |
| 50 | $25 | 80.5% | 17% |
| ~128 | $64 | 82.0% | 26% |

- **Method for 1 to 10 entries:** the best lineup, then each next-best lineup that differs from every
  earlier one by at least 4 players (4 chosen from 1-4 on validation). That beats the plain top 10 by
  1.4 points [0.7, 2.1].
- **Simulation methods** (lineups that are optimal in simulated nights, or chosen to maximise the
  expected best entry) did no better at 1 to 10 entries.
- **Rows for 20 or more entries** use the simulation method, since the plain method was not run that far.
- **The ceiling.** The curve gains less than half a point per doubling past 50 entries. No number of
  entries averages 85%, and none hit the exact optimum on any night.
- **Shipped.** With `--objective points`, an entries file with several open entries for one contest now
  gets this portfolio instead of near-copies of one lineup (`dfs/strategy.py`, `PORTFOLIO_MIN_DIFF`).
  A single entry is unchanged.

## NFL

| Change | Test slates | % of optimal | vs before [95% CI] | Shipped |
|---|---:|---:|---:|:---:|
| Engine before this work | 95 (all) | 53.3% | | |
| Data fixes: depth-chart kickers and blank positions; week-1 QB starts read from the right season | 95 (all) | 54.7% | +1.41 [+0.24, +2.65] | yes |
| Learned correction (LightGBM on the engine's residual, direct or blended) | 24 | 51.6–52.9% | −2.1 to −3.4 | no |
| Heavily regularised residual model | 24 | 56.0% | +0.93 [−1.11, +3.07] | no |
| Engine + game-day inactives zeroed | 24 | 55.3% | +0.22 [+0.00, +0.67] | already live (last call) |

- **The learned correction lost.** It lowered player-level error slightly but its corrections are
  mostly noise (correlation with the engine's actual error: 0.05), and the solver picks exactly the
  players whose correction is wrongly high. Only heavy regularisation removed the damage.
- **The audit removed the one positive.** The engine's own settings were tuned on 2024-25 weeks
  3-17, which covers 15 of the 24 test weeks. On the 9 weeks it wasn't tuned on, the regularised model
  is −0.14 [−4.05, +3.67] against the engine. It was also the best of about 8 variants viewed on the
  test set. Not shipped.
- **Selection rules.** Using the engine's simulator, with correlations fit on 2021-23 (QB with own
  WR1 +0.36, QB with opposing DST −0.37), none of these beat max expected points:
  - the most common simulated optimum;
  - the lineup that wins most often;
  - max expected share of the optimum;
  - a bonus for P(in the optimal lineup).
- **QB stacking.** Requiring the QB plus one of his own receivers scored +1.57 on the test weeks but
  −1.38 on 2024 and +0.01 over all 95 weeks. Not shipped.
- **Inactives.** The last call already rules out inactive players.
- **Real prices.** Only two weeks have real DK prices (2026 weeks 3 and 6). There the engine scored
  61.0% and 56.1% (pseudo-salaries: 53.0% and 64.5%). That is too few weeks to say whether the
  pseudo-salaries flatter or hurt the engine.

## College football

33 DK classic slates: 2024 (13), 2025 (13) and 2026 weeks 2-8 (7). The salary model, pool and engine
are as above.

| Projection | 2024 | 2025 | 2026 (test) | 2026 vs engine [95% CI] | Shipped |
|---|---:|---:|---:|---:|:---:|
| Site-style average (DK points per game) | 43.1% | 42.6% | 44.9% | −6.5 [−19.1, +6.4] | |
| Engine | 49.6% | 49.8% | 51.4% | | |
| Learned model, direct (LightGBM on DK points) | 50.6% | 57.3% | 54.3% | +2.97 [−3.71, +9.90] | no |
| Learned model, residual on the engine | 51.3% | 54.5% | 52.2% | +0.85 [−6.87, +9.28] | no |

- **The protocol.** The 2026 models train on 2024 and 2025 only. The 2024 and 2025 columns are
  out-of-fold, and the engine's own settings were fit on 2025, so only 2026 is a real test.
- **Not shipped.** Seven test slates can't separate +3 points from zero.
  - Each single-seed copy of the direct model scores between −2.2 and +0.2 on 2026, so most of the +3
    is the luck of the seed average.
  - Player-level error does improve in every season (RMSE −0.15 on 2026).
  - The model also needs its own live feature pipeline.
- **Audit: clean.** Retrained from scratch, the stored predictions matched exactly. The test season was
  never used for training or tuning.
- **The best lead to re-test** as 2026 slates accumulate.
- **Shipped for college: a data fix.** Team-games missing from the play-by-play release (it lags a few
  days) are now filled from the official box score instead of looking like missed games.

## What shipped

1. **NBA learned minutes model** (`dfs/projections/nba_minutes.py`, `data/learned/nba_minutes_*`): +4.1
   points of optimal on held-out nights (+3.7 for a typical seed).
2. **NBA confirmed starting fives at last call** (`dfs lastcall`): +4.9 in all over the engine.
3. **Max expected points is the default objective.** The contest objective (P(top x%), P(cash)) is
   still there with `--objective contest`.
4. **NFL data fixes:** +1.4 over 95 weeks.
5. **College play-by-play gap fill.**
6. **The harness itself** (`dfs/oracle.py`, `scripts/oracle/`), so every future change is scored the
   same way. Experiment scripts are kept in `scripts/oracle/exp_*.py`.
7. **NBA per-minute rate model** (`dfs/projections/nba_rate.py`): +0.55 on the main tier and +0.73 at
   last call, not significant on its own (second round, above).
8. **Several entries get a diverse portfolio** under the points objective: the best of 10 averages
   77.4% of the optimum, against 70.6% for one entry (second round, above).

**What a lineup is now, in plain numbers.** On a typical held-out NBA night it scores about 71% of the
best lineup anyone could have entered. Before this work it scored 66%, and the site's average scored 64%.
It reaches 80% of the optimum about one night in seven, up from one in twenty.

## Caveats

- **Pseudo-salaries.** Real DK prices are set by people who also read the news, so real slates have
  fewer cheap players with big roles than these do. The absolute % of optimal is probably a little
  high; the comparisons between methods use the same salaries for every method.
- **NBA pools assume the injury report is known**, as it is at lock. A late scratch that research
  misses still costs a lineup; the last call exists for that.
- **The test seasons were reused** across several NBA experiments, so the best of them is slightly
  flattered. The minutes model was chosen first, then checked again by audit.
- **The engines were tuned on some test data.** The NBA engine's minutes settings came from the first 3
  weeks of 2025-26 and its spread and p_zero settings from 2025-26. The NFL engine's settings came from
  2024-25. That flatters the engine rows; the learned models were trained on 2024-25 (NBA) and 2021-23
  (NFL) only.
- **NBA features use tonight's active list**, as the injury report gives it, and live runs use
  research for that.
- **2026-27 is only 6 NBA nights** (the season started on 2026-10-21) and the minutes model is weaker
  there than in 2025-26. Early-season minutes are the hardest part to project.
- **College has 33 slates**, so its intervals are wide.
