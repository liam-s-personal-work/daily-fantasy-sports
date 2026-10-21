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
2. **Scores with the contest's exact table.** The tables are stored and were checked against real 2026
   site scores.
3. **Builds a point-in-time statistical baseline** from real data:
   - NFL: nflverse;
   - college: sportsdataverse play-by-play;
   - other sports: the site's own average, shrunk.
4. **Researches every game** with a Claude agent and web search:
   - it collects facts only, and every fact is cited;
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
    - it re-forecasts only the affected players;
    - it changes the lineup only if a pick is out, or if a new lineup leads by more than 1% and by more
      than simulation noise.
11. **Grades after the slate.** It grades the lineup, names the step that cost the most, and updates the
    blend.

It never logs in to, scrapes or automates DraftKings or FanDuel. You download and upload the CSVs.

```
dfs parse    DKEntries.csv                        # what was detected, and every warning (no model calls)
dfs run      DKEntries.csv --rules my_rules.yaml  # -> runs/<id>/upload.csv, upload_my_rules.csv, report.md
dfs windows  runs/<id>                            # when to run the last call for each kickoff window
dfs lastcall runs/<id>                            # -> runs/<id>/upload_final.csv
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

So the engine puts your 2.3% **into the objective** rather than promising it:

- **Tournaments:** it picks the lineup with the highest simulated chance of scoring above the field's
  97.7th percentile.
- **Cash games:** it picks the lineup with the highest chance of beating the cash line.
- **Multi-entry:** it picks the set that maximises the chance that at least one entry gets there.

Progress is tracked two ways:

- with metrics that settle quickly: player-level error, calibration, and mean finish percentile;
- with the top-2.3% rate itself, against the 2.3% base rate with a confidence interval. Showing a 5% rate
  against that base rate takes about 250 independent slates.

## 3. Your pipeline, step by step: what was built, and where it changed

| Your step | Built as | Changed? Why |
|---|---|---|
| Read the contest from the entries file and saved rules | `dfs/io/contest_file.py` detects DK salary, entries and template files, and FD player-list and entries-template files. `dfs/rules/registry/contests.yaml` holds the cap, slots, limits and multipliers, each with a confidence level. Late-swap locked cells in an entries file are kept as they are | As specified. Formats were checked against **13 real DK/FD 2026 exports**. The CSVs never contain the cap or team limits, so those live in the registry and are cross-checked against the file on every run |
| Learn the scoring | `dfs/scoring.py` + `dfs/rules/scoring/*.yaml` | Scoring is a stored table, not something an agent "learns" at run time: no tokens and no mistakes. DK NFL scores reproduce DK-published 2026 lineup scores exactly. DST rules were checked against all 20 team averages in real 2026 DK and FD files |
| Research every game, allowed sites only | One Sonnet agent per game, with web search and a checklist (injury report, practice, role changes, weather, suspensions). It is given the known structured facts (official injury report, depth chart, usage) so it only searches for what is missing | Allowed sources are enforced in code (`dfs/sources.py`): page fetches outside an allow-list are denied by the Agent SDK permission layer, and facts sourced from DFS sites or no-automation sites are dropped |
| Baseline: NFL game logs adjusted by betting lines; college season averages adjusted for workload; remove anyone ruled out | **NFL:** a top-down model. Implied team points give team volume; usage shares and efficiency are shrunk; stat lines are simulated and scored; availability comes from the injury report. **College:** season averages adjusted for recent workload share and the game line | As specified, but point-in-time and as a distribution (p10/p50/p90 plus the chance of not playing). Tuned on 2024–25 only and tested on 2026 (§6.2–6.3) |
| Fresh forecasts (football), blind to prices, site averages, past scores | One Opus agent per game. Code enforces the blind: the packet has no salary, average or fantasy-point fields, and any free-text fact that mentions prices, lines, odds or fantasy points is dropped | Betting lines are hidden too. The baseline already uses them, so hiding them keeps the forecast an independent signal. The `market` experiment (§6.4) tested that choice |
| Blend: NFL 85/15, college 100%, other sports baseline leads | `dfs/blend.py` uses quantile averaging. Your weights are the **priors**; per-position weights learned from graded games replace them as evidence builds, shrunk toward the prior | **Changed by evidence.** On 1,517 graded 2026 player-games the forecaster helps, but the error-minimising weight is about 0.5 overall, high for QBs and near 0 for TEs and DST (§6.4). A fixed 85% is wrong for TEs and DST |
| Three independent drafts | The solver builds three drafts from the blended numbers: a cash lens (floor), a tournament lens (P(top x%)) and a leverage lens (least-owned near the best) | Drafts are built by the solver. Three identical deterministic solves would be pointless, and lineups written by an LLM are not reliably legal |
| Strategist: can't change a number; removes non-players; explains close calls; names the weakest pick; cuts only by citing a fact; solver builds the best legal lineup | Exactly that, with citation checks in code and a **novelty rule**: a cut needs a fact the forecaster had not already priced in, unless the player is a hard out or inactive | The solver's objective is the contest's (tournament or cash), not raw points |
| Checker attacks every pick; code decides; ≤2 revisions | A fresh Opus checker. Code fails a pick only on a cited blocker about availability or a new role fact; at most 2 revisions | As specified |
| Backup review that may adjust numbers, run alongside | Sonnet, in parallel. Its multipliers are bounded at 0.5–1.3 and each needs a cited fact. Its lineup ships only if the main path fails technically, or still fails the checker after 2 revisions | Precedence made explicit |
| My rules → second lineup, I choose | `UserRules` (lock, exclude, team and game excludes, max per team, stacks, min salary) is solved separately and written to `upload_my_rules.csv`. See `examples/my_rules.yaml` | As specified |
| Last hour: news on every pick, inactives, NFL re-forecast only affected players, change only if a pick is out or a new lineup leads by >1%, one final file | `dfs/lastcall.py`. Started games stay locked (late swap). The 1% is measured on the contest objective in the same simulations and must also exceed 2 standard errors of simulation noise | Run once per kickoff window (`dfs windows`), because inactives come out about 90 minutes before each kickoff |
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
| nflverse (GitHub releases) | NFL schedules with lines, weekly stats, snaps, injury reports, rosters, depth charts, expected-points model | CONDITIONAL: CC-BY-4.0 (attribute nflverse); the NFL keeps its upstream rights; depth charts are ESPN-derived (personal use) |
| sportsdataverse (GitHub releases) | college play-by-play, schedules, lines | CONDITIONAL: pre-built release files, no scraping |
| Claude web search | game research and last-hour news | ALLOWED; every fact carries its source URL |
| DraftKings / FanDuel sites, Rotowire, FantasyPros, Sports Reference, ESPN endpoints | not used | prohibited or unverified; blocked in code |

DK's and FD's Terms of Use could not be read from the build environment, because their hosts are blocked
here. The engine therefore treats both sites as off-limits for automation. Before playing for money:

- confirm DFS is legal in your state and that you meet the age limit;
- use the sites' deposit and entry limits.

## 6. Evidence

Everything here was produced by code in this repo on real 2026 games. 2026 games are after the model's
training cutoff, so the forecaster cannot remember the outcomes.

### 6.1 Contest files and scoring

- **Real files.** 13 real DK/FD exports parse correctly:
  - DK classic, showdown and late-swap entries;
  - DK NBA, NHL and PGA;
  - FD classic, the new MVP single-game format and the legacy single-game format;
  - FD NBA classic and single-game.
- **Upload files.** Uploads written from them re-parse and pass the legality check.
- **Synthetic edge cases** cover the formats that no real sample had.
- **Scoring.** DK NFL scoring reproduces DK-published 2026 lineup scores.
- **DST scoring.** It reproduces the season average printed in the real week 6 DK and FD files for 20 of
  20 teams.
- **Offense scoring.** FD season totals match for 100% of QBs and TEs, 98.6% of RBs and 95.1% of WRs. The
  remainder differ in how games played are counted.

### 6.2 NFL baseline (`docs/results/nfl_baseline_backtest.md`)

**Protocol:**

- tuned on 2024–25 weeks 3–17 only, tested on 2026 weeks 3–6;
- every week is projected from data strictly before it;
- the pool is QB/RB/WR/TE player-games with at least 25% of snaps, plus every DST: 1,055 player-games.

| | DK MAE | FD MAE |
|---|---:|---:|
| this model | **5.00** | **4.50** |
| best naive baseline (expected fantasy points) | 5.33 | 4.79 |
| season average | 5.70 | |
| last 3 games | 5.66 | |

The model beats every naive baseline at every position (QB, RB, WR, TE, DST, K):

- DK: −0.33 points per player, 95% interval −0.49 to −0.16.
- QBs: −0.96 points against the best baseline.
- With availability included (injured regulars count as 0): MAE 4.49 against 5.70.
- 80% intervals cover 82% of outcomes.

### 6.3 College football baseline (`docs/results/cfb_baseline_backtest.md`)

**Protocol:**

- fit on 2025 weeks 3–13, tested on 2026 weeks 3–7;
- 4,032 pre-game player-games, with players who did not play counted as 0.

| | MAE |
|---|---:|
| this model | **6.27** (QB 8.73, RB 6.37, WR 5.95, TE 4.23) |
| season average | 7.03 |
| last game | 8.21 |

- 10–90% coverage is 83.1%.
- Fantasy points rebuilt from play-by-play are within 1 point of the official box score for 97.1% of 2026
  player-games.

### 6.4 The blinded forecaster, and your 85/15 split

**Protocol:**

- the forecaster ran blind on 2026 weeks 3–6: 61 calls, $7.12;
- 1,517 graded player-games;
- results re-scored against the tuned baseline in
  `docs/results/forecast_experiment_2026_wk3-6_blind_rescored.md`.

**Findings:**

- **The forecaster adds real information.** Its MAE is lower than the baseline's by 0.12 points (95%
  team-week bootstrap interval −0.20 to −0.03).
- **The best weight is about 0.5, not 0.85.** The weight that minimises error (encompassing regression)
  is 0.51 overall and varies by position:

  | Position | n | Baseline MAE | Forecast MAE | Best forecast weight |
  |---|---:|---:|---:|---:|
  | QB | 244 | 3.77 | **3.49** | 0.85 |
  | WR | 595 | 4.53 | **4.38** | 0.54 |
  | RB | 353 | 4.38 | **4.17** | 0.47 |
  | TE | 234 | **4.44** | 4.61 | 0.00 |
  | DST | 91 | **3.60** | 3.77 | 0.08 |

- **What the engine does with this.** It starts from your priors and moves toward the measured weights,
  shrunk toward the prior (`data/learned/blend.json`). Every graded slate updates them.
- **Showing it betting lines didn't help.** A variant that let the forecaster see betting lines (weeks
  5–6, 748 player-games) gained no clear accuracy (−0.10, interval −0.21 to +0.02). The blind stays.
- **Calibration.** The forecaster's 80% ranges cover 77.5% (too narrow at the top). Per-position widening
  fit on these weeks is stored in `data/learned/calibration.json`.

### 6.5 Whole-pipeline replays on real site files (`docs/results/replays.md`)

**Setup:**

- five real 2026 DK/FD slates, run end to end with live agents as of the morning of the games;
- web research off, because searching today would surface the results;
- your prior weights and raw forecaster ranges, because the learned values were fit on these weeks.

**Results:**

- **Timing:** 85–214 s per slate.
- **Cost:** $0.24–$1.72 in model calls.
- **Legality:** every final lineup passed the independent legality check.
- **Against the simulated field:** the lineups beat 83% and 93% of it on the two DK main slates, 61% on FD
  main, and 35–37% on the two single-game slates.
- **Top-2.3% cut:** none reached it.

Five slates are noise at the lineup level, so this shows the machinery works, not an edge. The evidence
that the forecasts are good is the player-level evidence above.

### 6.6 Live, before the games (`docs/results/live_week7_projections.md`)

**Setup:** NFL week 7 (14 games), run on 2026-10-21 before Thursday's game, with live web research and the
blinded forecaster.

**Results:**

- 92 cited facts, 355 forecasts;
- **251 s** wall clock for research and forecasting together;
- **$3.54**.

These projections were committed before kickoff, so they can be graded honestly next week.

### 6.7 Engine tests

- **Test suite:** 235 offline tests pass (`python -m pytest -q`).
- **Solver:**
  - it matches brute force on a small slate;
  - its rules hold on showdown, FD single game, FD max-per-team, locks and excludes, and late swap;
  - the candidate pruner was exact in 360 random solves.
- **Distributions:** they reproduce their quantiles and moments.
- **Simulation:** it reproduces the target correlations, and its PSD repair is checked.
- **Uploads:** the files written for DK salary files, DK entries (locked cells kept) and FD entries
  re-parse.
- **Blind:** facts that leak prices, lines or fantasy points are dropped.
- **Agent stages:**
  - a strategist cut or a checker blocker without a verified fact is ignored;
  - a failed strategist hands over to the backup;
  - the backup's adjustments stay bounded;
  - the last call swaps only when a pick is out, and keeps started players.
- **Adversarial review:** the review covered file formats, mathematics, leakage, agent logic and
  compliance. Each finding was reproduced by a second agent before it counted (§9).

## 7. Time and money per run

| Part | Measured | Where |
|---|---|---|
| Parse + baseline + safety lineup | 15–20 s | replays |
| Research + blinded forecasts, 14-game main slate (pipelined, 6 at a time) | 251 s, $3.54 | live week 7 |
| Drafts, strategist, checker, backup, field model, final | 70–200 s, $0.25–$1.70 | replays |
| Last call per kickoff window | about 1–2 min, under $1 | `dfs lastcall` |

A full main slate is roughly **8–10 minutes and $4–6**, well inside the 60-minute and $15 limits.

**The limits are enforced in code:**

- **Budget:** a hard dollar cap; each agent also has its own budget and timeout.
- **Deadline:** stages shrink to fit it.
- **Safety lineup:** written in the first minute, so a legal upload exists even if something later fails.

## 8. Known limits

- **Saved rules.** The rules for several game types are medium or low confidence. They are marked in the
  registry and printed as warnings. Confirm the cap and slots from the contest's rules page once per game
  type.
- **Other sports.** NBA, MLB, NHL, golf, MMA and NASCAR run on the site-average baseline plus research.
  They have no stats adapter yet, so expect much weaker projections than in football.
- **College.** No betting lines are available for upcoming college games in the open data, so the
  game-environment factor is off unless you pass lines. Sacks are counted in QB rushing (the NCAA
  convention), which is not yet confirmed against a DK/FD college box score.
- **NFL baseline:**
  - it has no player props;
  - backup QBs are projected as if they start;
  - its 80% bands are slightly too wide below and slightly too narrow above.
- **The simulated field is a model.** Its P(top 2.3%) is optimistic because it assumes our projections
  are right; the real measure is graded results against the site's standings file.
- **Research.** In this build environment most sports news sites are blocked, so research relies on web
  search snippets. On your own machine, add sites you have confirmed allow automated access to
  `DFS_FETCH_ALLOW` so agents can open their pages.
- **What would prove an edge.** Player-level metrics settle in a few weeks. A real top-2.3% rate needs
  hundreds of graded contests. Run `dfs grade` after every slate, with the standings file when you have
  it.

## 9. Adversarial review

Findings and fixes are listed in `docs/results/code_review.md`.
