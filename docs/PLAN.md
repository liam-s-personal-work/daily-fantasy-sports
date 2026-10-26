# DFS engine: plan, design and evidence

This is the plan you asked for. It was written after the engine was built and tested, so every claim
below is one of two things:

- a design decision, with the reason for it; or
- a number measured in this repo, with the file that produced it.

Date: 2026-10-21. Branch: `claude/dfs-lineup-optimizer-plan-5yuexl`.

## 1. What it does

Give it the CSV you download from DraftKings or FanDuel: a salary file, a player list, or the entries
file with your registered entries. It then:

1. **Reads the contest.** It combines the file with the saved rules: salary cap, slots, team and game
   limits, and captain/MVP multipliers. It stops with a warning when the file and the saved rules
   disagree.
2. **Scores with the contest's exact table.** The tables are stored. The NFL tables were checked against
   real 2026 site data; the other sports' tables come from the sites' published rules and are marked with
   their verification level.
3. **Builds a point-in-time statistical baseline** from real data:
   - NFL: nflverse;
   - college: sportsdataverse play-by-play;
   - other sports: the site's own average, shrunk.
4. **Researches every game** with a Claude agent and web search:
   - it collects facts only, and every fact must cite a source (uncited facts are dropped);
   - facts from DFS sites and from no-automation sites are dropped;
   - page fetches outside an allow-list are refused by the tool layer, not by the prompt.
5. **Gets a separate blinded forecast.** A separate Claude gives each relevant football player a
   low / likely / high. It never sees salaries, site averages, past fantasy points or betting lines.
6. **Blends, simulates and models the field:**
   - blends the baseline and forecast by quantile averaging, with per-position weights learned from
     graded games and your 85/15 and 100/0 splits as the starting priors;
   - simulates every player jointly with correlations;
   - models the opponents' field and its ownership.
7. **Drafts, reviews and checks:**
   - the solver builds three drafts under three lenses;
   - a strategist reviews them. It cannot change a number and can only remove players by citing facts;
   - the solver then builds the lineup that maximises the contest's own objective;
   - a fresh checker attacks every pick. Code decides pass or fail, with at most 2 revisions;
   - a backup review runs in parallel in case the main path fails.
8. **Solves your own pick rules** as a second lineup.
9. **Writes the upload file and a report.** The report covers the lineup, the top 3 per slot, close
   calls, the weakest pick and every check that ran.
10. **Re-checks in the last hour before each kickoff window:**
    - it checks news and inactives on every pick;
    - it re-forecasts only the affected players, with their position group on the same team so the
      shares stay consistent;
    - it changes the lineup only if a pick is out, or if a new lineup leads by more than 1% and by more
      than simulation noise.
11. **Grades after the slate.** It grades the lineup, names the step that cost the most, and updates the
    blend.

It never logs in to, scrapes or automates DraftKings or FanDuel. You download and upload the CSVs.

```
dfs parse    DKEntries.csv                        # what was detected, and every warning (no model calls)
dfs run      DKEntries.csv --rules my_rules.yaml  # -> runs/<id>/upload.csv, upload_my_rules.csv, report.md
             [--kind cash|gpp]                    #    (salary files do not say which; entries files do)
dfs windows  runs/<id>                            # when to run the last call for each kickoff window
dfs lastcall runs/<id> [--entries DKEntries.csv]  # -> runs/<id>/upload_final.csv (fresh entries file optional)
dfs grade    runs/<id> [--standings contest-standings.csv]
```

Inside Claude Code, the `/dfs` skill (`.claude/skills/dfs/SKILL.md`) drives the same commands.

## 2. The honest target

You asked for a top-2.3% finish in every contest. No method can promise that, and the engine is built
so it never pretends to.

- **It is a two-sigma tail.** 2.3% is 1 − Φ(2) = 0.0228.
  - Take a system that finishes top 2.3% in **10%** of contests. That is more than four times chance,
    which would be elite. It would finish top 2.3% in all of 10 contests with probability 10⁻¹⁰.
  - Knowing every player's true mean doesn't fix this either: on synthetic slates such a lineup finishes
    top 2.3% only about 15–17% of the time (`docs/research/optimization_game_theory.md` §7.4).
- **Cash games.** A top-2.3% finish pays nothing extra there; the right target is beating the cash line.

With `--objective contest` the engine puts your 2.3% **into the objective** rather than promising it:

- **Tournaments:** it picks the lineup with the highest simulated chance of scoring above the field's
  97.7th percentile.
- **Cash games:** it picks the lineup with the highest chance of beating the cash line.
- **Multi-entry:** it builds the set greedily: each added tournament entry is the one that most raises the
  chance that at least one entry gets there. For cash games it takes the best entries one by one.

The default since 2026-10-26 is `--objective points`: the lineup with the highest expected score. You
asked for the highest-scoring lineup, and on held-out slates this rule came closest to the best lineup in
hindsight. Every simulated alternative did worse, or not significantly better
([docs/results/oracle_gap.md](results/oracle_gap.md)). That page also shows why the best lineup on a night
can't be promised: knowing every NBA player's exact minutes still never produced it.

Progress is tracked two ways:

- with metrics that settle quickly: player-level error, calibration, and mean finish percentile;
- with the top-2.3% rate itself, against the 2.3% base rate with a confidence interval. Showing a 5% rate
  against that base rate takes about 250 independent slates.

## 3. Your pipeline, step by step: what was built, and where it changed

| Your step | Built as | Changed? Why |
|---|---|---|
| Read the contest from the entries file and saved rules | `dfs/io/contest_file.py` detects DK salary, entries and template files, and FD player-list and entries-template files. `dfs/rules/registry/contests.yaml` holds the cap, slots, limits and multipliers, each with a confidence level. Late-swap locked cells in an entries file are kept as they are | As specified. Formats were checked against **13 real DK/FD exports** (2024–2026, most from 2026; kept locally, not in git). The CSVs never contain the cap or team limits, so those live in the registry and are cross-checked against the file on every run |
| Learn the scoring | `dfs/scoring.py` + `dfs/rules/scoring/*.yaml` | Scoring is a stored table, not something an agent "learns" at run time: no tokens and no mistakes. The NFL tables reproduce the season averages printed in real 2026 DK and FD files: all 20 team defenses, and 256 to 257 of 257 matched offensive players. A test also checks three single-player scores quoted from DK articles |
| Research every game, allowed sites only | One Sonnet agent per game, with web search and a checklist (injury report, practice, role changes, weather, suspensions). It is given the known structured facts (official injury report, depth chart, usage) so it only searches for what is missing. Every fact must carry a source URL and an availability status, plus a date when the source gives one | Allowed sources are enforced in code (`dfs/sources.py`). Page fetches outside an allow-list are denied by the Agent SDK permission layer. Facts from DFS, pick'em, betting or no-automation sites are dropped, and so are facts dated after the run's time. A "ruled out" claim counts only with a fact that proves it |
| Baseline: NFL game logs adjusted by betting lines; college season averages adjusted for workload; remove anyone ruled out | **NFL:** a top-down model. Implied team points give team volume; usage shares and efficiency are shrunk; stat lines are simulated and scored; availability comes from the injury report. **College:** season averages adjusted for recent workload share and the game line | As specified, but point-in-time and as a distribution (p10/p50/p90 plus the chance of not playing). Tuned on 2024–25 only and tested on 2026 (§6.2–6.3) |
| Fresh forecasts (football), blind to prices, site averages, past scores | One Opus agent per game. Code enforces the blind. The packet has no salary, average or fantasy-point fields, and no research summary or URL. Any fact that mentions prices, lines, odds or fantasy points is dropped. Game-day inactives appear only once they are announced | Betting lines are hidden too. The baseline already uses them, so hiding them keeps the forecast an independent signal. The `market` experiment (§6.4) tested that choice |
| Blend: NFL 85/15, college 100%, other sports baseline leads | `dfs/blend.py` uses quantile averaging. Your weights are the **priors**; per-position weights learned from graded games replace them as evidence builds, shrunk toward the prior | **Changed by evidence.** On 1,557 graded 2026 player-games (leak-free), a fixed 85% does worse than the baseline alone. The learned per-position weights (0.25–0.72) do best on squared error, measured in-sample; by MAE the baseline alone is slightly better (§6.4) |
| Three independent drafts | The solver builds three drafts from the blended numbers: a cash lens (floor), a tournament lens (P(top x%)) and a leverage lens (least-owned near the best) | Drafts are built by the solver. Three identical deterministic solves would be pointless, and lineups written by an LLM are not reliably legal |
| Strategist: can't change a number; removes non-players; explains close calls; names the weakest pick; cuts only by citing a fact; solver builds the best legal lineup | Exactly that, with citation checks in code and a **novelty rule**: a cut needs a fact the forecaster had not already priced in, unless the player is a hard out or inactive | The solver's objective is the contest's (tournament or cash), not raw points |
| Checker attacks every pick; code decides; ≤2 revisions | A fresh Opus checker. Code fails a pick only on a cited blocker about availability or a new role fact; at most 2 revisions | As specified |
| Backup review that may adjust numbers, run alongside | Sonnet, in parallel. Its multipliers are bounded at 0.5–1.3 and each needs a cited fact. Its lineup ships only if the main path fails technically, or still fails the checker after 2 revisions | Precedence made explicit |
| My rules → second lineup, I choose | `UserRules` (lock, exclude, team and game excludes, max per team, stacks, min salary) is solved separately and written to `upload_my_rules.csv`. See `examples/my_rules.yaml` | As specified |
| Last hour: news on every pick, inactives, NFL re-forecast only affected players, change only if a pick is out or a new lineup leads by >1%, one final file | `dfs/lastcall.py`. Every entry in the file is checked. Started players stay in their slots (late swap). The 1% is measured on the contest objective in the same simulations and must also exceed 2 standard errors of simulation noise. News from blocked sites is ignored, and a failed news check is reported, never silent | Run once per kickoff window (`dfs windows`), because inactives come out about 90 minutes before each kickoff |
| Grade vs best possible lineup, name the failed step, fix it, update the blend | `dfs/grading_run.py` computes actual points and the hindsight optimum. It scores the lineup after each step (baseline numbers, blended numbers, contest objective plus strategist, checker, final, last call) and the alternatives (forecast-only numbers, each draft). It reports the percentile against the real field (with a standings file) or the simulated field, and updates the blend | The hindsight optimum is reported but treated as a diagnostic, because it mostly measures luck. Engine changes should follow player-level evidence, not one slate |

Some additions were not in your draft; a top-2.3% objective needs them:

- correlated simulation (QB–receiver stacks, bring-backs, DST against the opposing offense);
- an opponent field model with ownership, and duplication awareness;
- multi-entry portfolios and late-swap locks;
- a safety lineup written in the first minute;
- a hard dollar budget per run.

## 4. Architecture

```
dfs/io/          contest_file.py (parse + detect), upload.py (write DK/FD upload files)
dfs/rules/       registry/contests.yaml, scoring/*.yaml, correlations.yaml
dfs/data/        nflverse.py, teams.py, cfb.py, cfb_stats.py, cfb_teams.py
dfs/projections/ nfl.py + nfl_provider.py (NFL baseline), cfb.py + cfb_provider.py, matching.py
dfs/providers.py SlateContext + generic (site-average) provider for other sports
dfs/llm/         backend.py (Agent SDK / replay / fake; cache, budget, timeouts), prompts.py, schemas.py
dfs/stages/      agents.py (build tasks, parse, verify citations), decide.py (drafts → review → final)
dfs/sim.py       Gaussian-copula simulation        dfs/distributions.py  low/likely/high → distribution
dfs/optimize/    ilp.py (CP-SAT, exact)            dfs/field.py, select.py, strategy.py (field, objectives)
dfs/blend.py  dfs/grade.py  dfs/grading_run.py  dfs/lastcall.py  dfs/pipeline.py  dfs/cli.py  dfs/sources.py
```

**What a run records.** Every stage writes its inputs and outputs to `runs/<id>/`:

- contest, baseline, dossiers, forecasts and projections;
- drafts, reviews and the final lineup;
- upload files and the report;
- timings and model spend.

**How agents and code split the work.** Model answers are cached by content hash, so a run can be
replayed exactly and graded later. Agents return schema-validated JSON; code does all arithmetic,
legality and decisions.

**Every lineup is solved exactly.** The CP-SAT solver uses integer salaries and slot multipliers. An
independent legality check re-validates every lineup before any file is written.

## 5. Data sources and compliance

| Source | Used for | Status |
|---|---|---|
| Your DK/FD CSVs | contest, salaries, IDs, entries, site injury status | you download and upload them by hand |
| nflverse (GitHub releases) | NFL schedules with lines, weekly stats, snaps, injury reports, rosters, depth charts, expected-points model | CONDITIONAL: CC-BY-4.0, credited in the README. The NFL keeps its upstream rights. Depth charts are ESPN-derived copies |
| sportsdataverse (GitHub releases) | college play-by-play, schedules, rosters, box scores, lines | CONDITIONAL: pre-built release files, no scraping. Most college sets are ESPN-derived copies |
| Claude web search | game research and last-hour news | ALLOWED; every fact must cite its source URL |
| DraftKings / FanDuel sites, Rotowire, FantasyPros, Sports Reference, ESPN's data APIs | not used | prohibited or unverified; blocked in code |

**ESPN-derived data: a judgment call.**

- **What it is.** The engine downloads only from GitHub, which allows automated downloads. It never touches
  ESPN. But nflverse's depth charts and most sportsdataverse college sets are copies of ESPN data.
- **What I decided.** The engine uses them for personal, non-commercial use and never redistributes them.
- **Why it's a judgment call.** The research note `docs/research/sources_compliance.md` (question 5)
  first proposed excluding ESPN-derived sets by default, because ESPN's terms restrict automated
  collection. I chose to use them because the engine itself only downloads public GitHub releases, and the
  use is personal.
- **How to take the stricter line.** College slates can run without them today with `--provider generic`.
  The NFL depth charts feed the starter and availability model, so removing them would need a code change
  and would cost accuracy.

DK's and FD's Terms of Use could not be read from the build environment, because their hosts are blocked
here. The engine therefore treats both sites as off-limits for automation. Before playing for money:

- confirm DFS is legal in your state and that you meet the age limit;
- use the sites' deposit and entry limits.

## 6. Evidence

Everything here was produced by code in this repo on real 2026 games. 2026 games are after the model's
training cutoff, so the forecaster cannot remember the outcomes.

### 6.1 Contest files and scoring

- **Real files.** 13 real DK/FD exports parse correctly. They are public files from other people's GitHub
  repositories, mostly from 2026; one is from October 2025 and one (the DK NHL template) from November
  2024. They are kept locally, not committed. They cover:
  - DK classic, showdown and late-swap entries;
  - DK NBA, NHL and PGA;
  - FD classic and the MVP single-game format;
  - FD NBA classic and single-game.
- **Upload files.** Uploads written from them re-parse and pass the legality check.
- **Synthetic fixtures** cover the formats no real sample had: the legacy FD single-game format, entries
  files with locked cells, and more.
- **DST scoring.** It reproduces the season average printed in the real week 6 DK and FD files for 20 of
  20 teams.
- **Offense scoring.**
  - FD season totals agree for 256 of 257 matched players; the other shows last season's line.
  - DK averages agree for all 257 once DK's games count is used.
- **Other sports.** Their scoring tables come from the sites' published rules and have not been checked
  against real scores.

### 6.2 NFL baseline (`docs/results/nfl_baseline_backtest.md`)

**Protocol:**

- tuned on 2024–25 weeks 3–17 only, tested on 2026 weeks 3–6;
- every week is projected from data strictly before it;
- the headline pool is QB/RB/WR/TE player-games with at least 25% of snaps: 1,055 player-games. DSTs and
  kickers are scored separately (122 each).

| | DK MAE | FD MAE |
|---|---:|---:|
| this model | **4.99** | **4.50** |
| best naive baseline (expected fantasy points) | 5.31 | 4.77 |
| season average | 5.68 | |
| last 3 games | 5.64 | |

The model beats every naive baseline at every position (QB, RB, WR, TE, DST, K):

- DK: −0.32 points per player, 95% interval −0.48 to −0.13.
- FD: −0.27 points per player, 95% interval −0.41 to −0.11.
- QBs: about −0.8 points against the best baseline.
- With availability included (injured regulars count as 0): MAE 4.43 against 5.69.
- 80% intervals cover 82% of outcomes.

A review of the baseline found and fixed real defects, each with a regression test. Together they cut
the QB starting-probability error (Brier score) by about a quarter on 2026 weeks 3–6: 0.017 → 0.013 over
all 421 QB rows (`docs/results/ops_checks.md`). The fixes were:

- the QB start chain;
- depth-chart-aware availability for starters returning from injury;
- return fumbles in scoring.

### 6.3 College football baseline (`docs/results/cfb_baseline_backtest.md`)

**Protocol:**

- fit on 2025 weeks 3–13, tested on 2026 weeks 3–7;
- 4,032 pre-game player-games, with players who did not play counted as 0.

| | MAE |
|---|---:|
| this model | **6.28** (QB 8.72, RB 6.38, WR 5.96, TE 4.25) |
| season average | 7.03 |
| last game | 8.21 |

- 10–90% coverage is 82.1%.
- The review fixed a look-ahead leak: bowl games were counted as week-1 history for every regular-season
  week. It also fixed team-row parsing and slate matching. The numbers above are after the refit.
- Fantasy points rebuilt from play-by-play are within 1 point of the official box score for 97.4% of 2026
  player-games.

### 6.4 The blinded forecaster, and your 85/15 split

**Protocol** (`docs/results/forecast_experiment_2026_wk3-6_blind.md`):

- the forecaster ran blind on 2026 weeks 3–6, game by game, from point-in-time structured facts only
  (official injury report, depth chart, usage in raw stats): 61 calls, $7.17;
- 1,557 graded player-games, scored against actual DraftKings points;
- web research was off, because searching today would surface the results;
- 2026 is after the model's training cutoff, so it cannot remember the games.

**A leak found and fixed on the way.** The first run of this experiment showed the forecaster each
player's game-day inactive status, which the NFL publishes 90 minutes before kickoff. All of its
apparent edge came from 39 such rows (`docs/results/ops_checks.md`). Those files are kept as
`*_v1_inactives_leak*`. Inactive status now
appears only from 90 minutes before each kickoff, and the numbers below come from the clean re-run.

**Findings, on what the lineup is scored on (each player's mean, chance of not playing included):**

| Position | n | Baseline MAE | Forecast MAE | 50/50 blend MAE | Best forecast weight |
|---|---:|---:|---:|---:|---:|
| all | 1,557 | 4.20 | 4.21 | **4.17** | 0.20 |
| QB | 244 | **3.36** | 3.51 | 3.41 | 0.08 |
| RB | 366 | 4.34 | 4.20 | 4.21 | 0.33 |
| WR | 610 | 4.46 | 4.43 | 4.43 | 0.37 |
| TE | 244 | **4.34** | 4.58 | 4.44 | 0.00 |
| DST | 93 | 3.71 | **3.59** | 3.61 | 0.43 |

- **On its own, the forecaster is no more accurate than the corrected baseline.** The difference is
  +0.007 points, 95% interval −0.07 to +0.08.
- **Blended, it adds a little.**
  - The 50/50 blend has the lowest MAE.
  - The forecaster ranks players better: within-position rank correlation 0.523 vs 0.490.
  - It is much better on how a QB scores when he plays: 5.75 vs 6.94.
- **Your 85% is too high.** With calibrated forecasts, a fixed 85/15 blend does worse than the baseline
  alone: RMSE 6.016 vs 5.975.
- **The learned weights win narrowly, on squared error and in-sample.** Per position, shrunk toward your 85%
  (`data/learned/blend.json`, rebuilt by `scripts/fit_learned.py`), they have the lowest squared error,
  RMSE 5.946. That is measured on the same rows they were fit on. By MAE the baseline alone is still
  slightly better (4.199 vs 4.233):

  | QB | RB | WR | TE | DST |
  |---:|---:|---:|---:|---:|
  | 0.48 | 0.59 | 0.62 | 0.25 | 0.72 |

  Squared error is the right loss here, because a lineup's value is the sum of means. Every graded slate
  refines the weights, and only out-of-sample grading will show whether they help.
- **What this test cannot see.** The forecaster's real advantage should be web research, which the
  baseline never has: news, role changes, beat reporters. That can't be tested on past games without
  seeing the results. It will show up, or not, in graded live slates.
- **Betting lines.** A variant that let the forecaster see them (weeks 5–6) gained no clear accuracy. The
  blind stays. Caveat: that variant ran before the inactives fix.
- **Calibration.** The forecaster's 80% ranges cover 77% of outcomes, too short at the top. Per-position
  widening is in `data/learned/calibration.json`; the top half is stretched about 1.4×.
- **No peeking.** Both learned files record the weeks they were fit on, and a run of those weeks ignores
  them.

### 6.5 Whole-pipeline replays on real site files (`docs/results/replays.md`)

**Setup:**

- five real 2026 DK/FD slates, run end to end with live agents as of the morning of the games;
- the engine after the review fixes in §9. One later fix, which accepts the forecaster's truncated defense
  keys (`IND|dst`), came after these runs. So in the two week-6 main slates, the TEN and IND defenses ran
  on the baseline alone;
- web research off, because searching today would surface the results;
- your prior weights and raw forecaster ranges, because the learned values were fit on these weeks.

| Slate | Wall clock | Cost | Lineup | Best possible | Beat sim. field | Sim. top-2.3% cut |
|---|---:|---:|---:|---:|---:|---:|
| DK classic, wk 6 main (10 games) | 214 s | $1.63 | 145.5 | 236.3 | 97% | 146.6 |
| DK classic, wk 3 main (13 games) | 228 s | $1.79 | 151.1 | 256.8 | 95% | 157.8 |
| DK showdown, wk 6 DAL@GB | 101 s | $0.28 | 87.1 | 117.3 | 78% | 105.4 |
| FD classic, wk 6 main (10 games) | 154 s | $1.53 | 130.8 | 215.2 | 95% | 140.2 |
| FD single game, wk 1 NE@SEA | 97 s | $0.27 | 42.6 | 95.1 | 33% | 73.6 |

- **Legality:** every lineup passed the independent legality check.
- **Top-2.3% cut:** none reached it; the DK week 6 lineup missed by 1.2 points.
- **Steps:** the contest-objective step (with the strategist) added points on four of five slates.
- **Before the fixes:** the same slates scored 122.5 / 146.6 / 70.3 / 109.3 / 43.0.

Five slates are noise at the lineup level, so this shows the machinery works, not an edge. Proving an
edge takes graded slates by the hundred (§2).

### 6.6 Live, before the games (`docs/results/live_week7_projections.md`)

**Setup:** NFL week 7, all 14 games, run on 2026-10-21 (16:58 UTC) before Thursday's first kickoff. It used
live web research, the blinded forecaster and the learned blend weights.

**Results:**

- 48 web searches, 82 cited facts (each with an availability status), 364 forecasts, no parse problems;
- **228 s** wall clock for research and forecasting together;
- **$3.48**.

**The blind in action:**

- Two research summaries mentioned betting lines, and three facts mentioned prices or lines.
- None of that reached the forecaster: it never sees summaries, and those facts were dropped.

**The first live run.** An earlier run the same morning, before the review fixes, did pass three summaries
with lines to the forecaster. It is kept as `live_week7_projections_v1.md` for the record.

These projections were committed before kickoff, so they can be graded honestly once the week is played.

### 6.7 Engine tests

- **Test suite:** 334 offline tests pass (`python -m pytest -q`), plus 15 network tests (`-m network`).
- **Solver:**
  - it matches brute force on a small slate;
  - its rules hold on showdown, FD single game, FD max-per-team, locks and excludes, and late swap;
  - the candidate pruner matches the full solve in the tests. A 360-solve randomized probe during review
    found no suboptimal solve (`docs/results/ops_checks.md`).
- **Distributions:** they reproduce their quantiles and moments.
- **Simulation:** starters keep the measured stack correlations exactly; backups get a quarter of them. No
  repair is needed on the real replay slates: smallest eigenvalue 0.22–0.24 (`docs/results/ops_checks.md`).
- **Uploads:** the files written for DK salary files, DK entries (locked cells kept) and FD entries
  re-parse.
- **Blind:**
  - dozens of real-world phrasings of prices, lines and fantasy scores are dropped;
  - real facts are kept;
  - no research summary or URL reaches the forecaster.
- **Agent stages:**
  - a strategist cut or a checker blocker without a verified fact is ignored;
  - a failed strategist hands over to the backup;
  - the backup's adjustments stay bounded;
  - the last call swaps only when a pick is out, keeps started players in their slots, and covers every
    entry.
- **Late swap:**
  - "In Progress" / "Final" games are locked;
  - started players are never moved or added;
  - a slate with too few open games is built around the locked entries.
- **Adversarial review:** the review covered file formats, mathematics, leakage, agent logic and
  compliance. Each finding was reproduced by a second agent before it counted (§9).

### 6.8 How close to the best possible lineup (`docs/results/oracle_gap.md`)

- **The measure.** Each held-out slate is scored as the actual points of the lineup we would have
  entered ÷ the actual points of the best legal lineup, solved exactly after the games.
- **NBA, 169 held-out nights:**

  | Projection | % of the optimum |
  |---|---:|
  | Site's average | 64.4% |
  | Engine before this work | 66.3% |
  | + learned minutes model | 70.4% (+4.1 [+2.8, +5.5]; other seeds +3.6 to +3.7) |
  | + confirmed starters at last call | 71.1% |

- **Exact optimum:** 0 nights, for every method, even with each player's exact minutes known.
- **Second round, aimed at 85%:**
  - a per-minute rate model adds +0.55 (+0.73 at last call), not significant;
  - more data and new minutes features, and rules learned from real optimal lineups, didn't help;
  - several diverse entries do help: the best of 10 averages 77.4%, and the best of about 128 averages
    82.0%;
  - no method averages 85%.
- **NFL:** data fixes moved the engine from 53.3% to 54.7% (+1.4 [+0.2, +2.7], 95 weeks). Learned
  corrections didn't help.
- **College:** a learned correction scored +3.0 [−3.7, +9.9] on 7 test slates, with single-seed copies of it
  at −2.2 to +0.2. Not shipped.

## 7. Time and money per run

| Part | Measured | Where |
|---|---|---|
| Parse + baseline + safety lineup | 10–25 s | replays |
| Research + blinded forecasts, 14-game main slate (pipelined, 6 at a time) | 228 s, $3.48 | live week 7 |
| Field model, drafts, strategist, checker, backup, final | 55–120 s | replays |
| Whole run without web research (replays) | 97–228 s, $0.27–$1.79 | replays |
| 150-entry portfolio, real DK file (all reviews failing over) | 68 s in total | `docs/results/ops_checks.md` |
| Last call per kickoff window | 35 s, $0.10 (showdown, 3 searches) | `docs/results/ops_checks.md` |

A full main slate is roughly **8–10 minutes and $4–6**, well inside the 60-minute and $15 limits.

**The limits are enforced in code:**

- **Budget:** a hard dollar cap.
  - Each agent call may spend only its share of what is left.
  - Calls that time out are charged.
- **Deadline:** a hard stop for agent calls. They end about 4 minutes early, leaving time for the solver
  and the files.
- **Safety lineup:** written in the first minute, so a legal upload exists even if something later fails.

## 8. Known limits

- **The forecaster's edge is not proven.**
  - On leak-free past games it is no better than the statistical baseline on its own, and helps only a
    little in the blend (§6.4).
  - Its expected advantage, live web research, can only be measured on live slates. Grade every slate.
- **Saved rules.** The rules for several game types are medium or low confidence. They are marked in the
  registry and printed as warnings. Confirm the cap and slots from the contest's rules page once per game
  type.
- **Other sports.**
  - NBA, MLB, NHL, golf, MMA and NASCAR run on the site-average baseline plus research.
  - DK soccer, tennis and CS2, and FanDuel golf, have no stored scoring table yet. Runs work from the
    file's averages, and grading needs the site's standings file. The registry says so per entry. They have no
    stats adapter yet, so expect much weaker projections than in football.
  - FanDuel files carry no kickoff times outside the NFL, so late swap there cannot tell which games have
    started. The engine says so; don't re-upload after the first game starts.
- **Upload headers.**
  - Upload files written from a plain player list use the saved slot names.
  - For FanDuel, and for DK college football, download the site's own entries or upload template and run
    on that. Its header is then used exactly.
- **College.** No betting lines are available for upcoming college games in the open data, so the
  game-environment factor is off unless you pass lines. Sacks are counted in QB rushing (the NCAA
  convention), which is not yet confirmed against a DK/FD college box score.
- **NFL baseline:**
  - it has no player props;
  - backup QBs are projected as if they start;
  - when a backup QB starts, his team's passing volume still comes from history with the usual starter.
- **The simulated field is a model.** Its P(top 2.3%) is optimistic because it assumes our projections
  are right; the real measure is graded results against the site's standings file.
- **Research.** In this build environment most sports news sites are blocked, so research relies on web
  search snippets. On your own machine, add sites you have confirmed allow automated access to
  `DFS_FETCH_ALLOW` so agents can open their pages.
- **What would prove an edge.** Player-level metrics settle in a few weeks. A real top-2.3% rate needs
  hundreds of graded contests. Run `dfs grade` after every slate, with the standings file when you have
  it.

## 9. Adversarial review

The finished engine was attacked from five angles by independent reviewers:

- contest files and uploads;
- optimisation and statistics;
- information leakage;
- agent decisions;
- compliance and operations.

Every reviewer had to demonstrate each defect by running code, and a second agent re-ran each finding.

**78 findings:** 14 critical, 33 major, 31 minor. All were fixed, except one that could not happen and one
that is documented as a limit. `docs/results/code_review.md` lists each finding with its outcome.

The most important:

- **Forecaster blind breached.** The research summary, and the betting line it was given, reached the
  blinded forecaster.
- **Game-day inactives leaked into the experiment.** Fixing this turned the forecaster's measured edge
  into roughly none (§6.4).
- **Late swap could produce illegal uploads.** Started players could be moved, dropped or added, and DK's
  "In Progress" markers were ignored.
- **NHL defensemen merged.** All of a team's defensemen became one player.
- **DK MLB's 5-hitter limit was not enforced.**
- **Fact matching hit the wrong player.** A teammate's fact could cut a player ("Kyle Allen inactive"
  cutting Josh Allen).
- **Cuts without facts.** The backup review and the research agent's "ruled out" list could remove
  players without citing a fact.
- **The last call covered only the first entry.**
