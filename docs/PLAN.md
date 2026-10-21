# DFS engine — plan, design and evidence

This document is the plan you asked for, written after the engine was built and tested, so every
claim below is either a design decision (and why) or a number measured in this repo. Results that
depend on runs still in progress are marked _pending_ and filled in by the results section.

## 1. What it does

Give it the CSV you download from DraftKings or FanDuel (salary file, player list, or the entries
file with your registered entries). It:

1. reads the contest from the file plus the saved rules (salary cap, slots, team/game limits,
   captain/MVP multipliers), and stops with a warning when the file and the saved rules disagree;
2. scores with the contest's exact table (stored, verified against real 2026 DK-published scores);
3. builds a statistical baseline from real data (NFL: nflverse; college: sportsdataverse play-by-play;
   other sports: the site's own average, shrunk), point-in-time;
4. researches every game with a Claude agent and web search (facts only, every fact cited, only
   allowed sources; page fetches outside an allow-list are refused by the tool layer, not the prompt);
5. has a separate Claude forecast each relevant player's low/likely/high blind to salaries, site
   averages, past fantasy points and betting lines;
6. blends the two (quantile averaging; the weight is learned from graded results, starting from your
   85/15 and 100/0 priors), simulates every player jointly with correlations, and models the field;
7. builds three drafts with the solver under three lenses, has a strategist review them (it cannot
   change a number; it can only remove players with cited facts), has the solver build the lineup that
   maximises the contest's own objective, lets a fresh checker attack every pick (code decides pass/
   fail, ≤2 revisions), and runs a backup review in parallel in case the main path fails;
8. solves your own pick rules as a second lineup;
9. writes the upload file (and a report: lineup, top 3 per slot, close calls, weakest pick, checks);
10. in the last hour, re-checks news and inactives on every pick, re-forecasts only affected players,
    and changes the lineup only if a pick is out or a new lineup leads by >1% (beyond simulation noise);
11. after the slate, grades the lineup, names the step that cost the most, and updates the blend.

It never logs in to, scrapes, or automates DraftKings or FanDuel. You download and upload the CSVs.

```
dfs parse  DKEntries.csv             # what was detected + every warning (no model calls)
dfs run    DKEntries.csv --rules my_rules.yaml   # -> runs/<id>/upload.csv, report.md
dfs windows runs/<id>                # when to run the last call for each kickoff window
dfs lastcall runs/<id>               # -> runs/<id>/upload_final.csv
dfs grade  runs/<id> [--standings contest-standings.csv]
```

Inside Claude Code, the `/dfs` skill (`.claude/skills/dfs/SKILL.md`) drives the same commands.

## 2. The honest target

You asked for a top-2.3% finish in every contest. That cannot be promised by any method, and the plan
is built so it never pretends otherwise:

- 2.3% is the two-sigma tail (1 − Φ(2) = 0.0228). A system that finishes top 2.3% in **10%** of
  contests — more than four times chance, which would be elite — would do it in all of 10 contests with
  probability 10⁻¹⁰. Even with perfect knowledge of every player's true mean, synthetic slates put a
  lineup in the top 2.3% only about 15–17% of the time (`docs/research/optimization_game_theory.md` §7.4).
- In cash games a top-2.3% finish pays nothing extra; the right target there is beating the cash line.

So the engine puts your 2.3% **into the objective** instead of a promise: for tournaments it chooses the
lineup with the highest simulated probability of finishing above the field's 97.7th-percentile score;
for cash games, the highest probability of beating the cash line; for multi-entry, the set that
maximises the chance at least one entry gets there. Progress is measured with metrics that converge
quickly (player-level error, calibration, mean finish percentile), and the top-2.3% rate is tracked
against the 2.3% base rate with a confidence interval. Showing a 5% rate against the 2.3% base rate
takes on the order of 250 independent slates.

## 3. Your pipeline, step by step — what was built, and where it changed

| Your step | Built as | Changed? Why |
|---|---|---|
| Read the contest from the entries file and saved rules | `dfs/io/contest_file.py` sniffs DK salaries / entries / template and FD player list / entries template; `dfs/rules/registry/contests.yaml` holds cap, slots, limits, multipliers, with a confidence level per entry | As specified. Formats were checked against **13 real DK/FD 2026 exports**. The CSVs never contain the cap or team limits, so they live in the registry and are cross-checked against the file each run |
| Learn the scoring | `dfs/scoring.py` + `dfs/rules/scoring/*.yaml` | Scoring is a stored table, not something an agent "learns" at run time: no tokens, no mistakes. DK NFL values reproduce DK-published 2026 lineup scores exactly |
| Research every game, allowed sites only | One Sonnet agent per game with web search; known structured facts (official injury report, depth chart, usage) are given to it so it only searches for gaps | Allowed sources are enforced in code (`dfs/sources.py`): fetches outside an allow-list are denied by the Agent SDK permission layer; facts sourced from DFS sites or no-automation sites are dropped |
| Baseline: NFL game logs adjusted by betting lines; college season averages adjusted for workload; remove anyone ruled out | NFL: a top-down model (implied team points → team volume → shrunk usage shares → shrunk efficiency → simulated stat lines → points) with availability from the injury report; college: season averages adjusted for recent workload share and the game line | As specified, made point-in-time and distributional (p10/p50/p90 + chance of not playing) |
| Fresh forecasts (football), blind to prices, site averages, past scores | One Opus agent per game; the blind is enforced by code (the packet has no salary/average/fantasy-point fields, and any free-text fact mentioning prices, lines, odds or fantasy points is dropped) | Betting lines are also hidden by default (the baseline already uses them; hiding them keeps the forecast an independent signal). The `market` experiment below tests that choice |
| Blend: NFL 85/15, college 100%, other sports baseline leads | `dfs/blend.py`: quantile averaging; your weights are the **priors**; per-position weights learned from graded results replace them once enough evidence exists (shrunk toward the prior) | Fixed weights are untested claims; the experiment below measures the right weight on 2026 games |
| Three independent drafts | The solver builds three drafts alone from the blended numbers: cash lens (floor), tournament lens (P(top x%)), leverage lens (least-owned near the best) | Drafts are solver-built: three identical deterministic solves would be pointless, and LLM-built lineups are not reliably legal |
| Strategist: can't change a number; removes non-players; explains close calls; names the weakest pick; cuts only by citing a fact; solver builds the best legal lineup | Exactly that, with citation checking in code and a **novelty rule**: a cut needs a fact the forecaster had not already priced in, unless it is a hard out/inactive | The solver's objective is the contest's (tournament vs cash), not raw points |
| Checker attacks every pick; code decides; ≤2 revisions | A fresh Opus checker; code fails a pick only on a cited blocker about availability or a new role fact; ≤2 revisions | As specified |
| Backup review that may adjust numbers, run alongside | Sonnet, in parallel; bounded multipliers 0.5–1.3, each needing a cited fact; its lineup ships only if the main path fails technically or still fails the checker after 2 revisions | Precedence made explicit |
| My rules → second lineup, I choose | `UserRules` (lock, exclude, team/game excludes, max per team, stacks, min salary) solved separately; `upload_my_rules.csv` | As specified |
| Last hour: news on every pick, inactives, NFL re-forecast only affected players, change only if a pick is out or a new lineup leads by >1%, one final file | `dfs/lastcall.py`; started games stay locked (late swap); the 1% is measured on the contest objective in the same simulations and must also exceed 2 standard errors of simulation noise | Run once per kickoff window (`dfs windows`), because inactives come out ~90 minutes before each kickoff |
| Grade vs best possible lineup, name the failed step, fix it, update the blend | `dfs/grading_run.py`: actual points, the hindsight optimum, every stage's lineup scored (baseline-only, forecast-only, each draft, after strategist, after checker, final), percentile vs the real field (standings file) or the simulated field, blend update | The hindsight optimum is reported but treated as a diagnostic: it mostly measures luck. Changes to the engine should follow player-level evidence and replays, not one slate |

Additions not in your draft, because a top-2.3% objective needs them: correlated simulation (QB–receiver
stacks, bring-backs, DST vs opposing offense), an opponent field model with ownership, duplication
awareness, multi-entry portfolios, late-swap locks, a safety lineup written within the first minutes,
and a hard dollar budget per run.

## 4. Architecture

```
dfs/io/          contest_file.py (parse + detect), upload.py (write DK/FD upload files)
dfs/rules/       registry/contests.yaml, scoring/*.yaml, correlations.yaml
dfs/data/        nflverse.py, teams.py, cfb.py
dfs/projections/ nfl.py (baseline model), nfl_provider.py, cfb*.py, matching.py
dfs/providers.py SlateContext + generic (site-average) provider
dfs/llm/         backend.py (Agent SDK / replay / fake; cache, budget, timeouts), prompts.py, schemas.py
dfs/stages/      agents.py (build tasks, parse, verify citations), decide.py (drafts → review → final)
dfs/sim.py       Gaussian-copula simulation      dfs/distributions.py  low/likely/high → distribution
dfs/optimize/    ilp.py (CP-SAT, exact)          dfs/field.py, select.py, strategy.py (field, objectives)
dfs/blend.py     dfs/grade.py  dfs/grading_run.py  dfs/lastcall.py  dfs/pipeline.py  dfs/cli.py
```

Every stage writes its inputs and outputs to `runs/<id>/` (contest, baseline, dossiers, forecasts,
projections, drafts, reviews, final lineup, upload files, report, timings, model spend). Model answers
are cached by content hash, so a run can be replayed exactly and graded later. Agents return
schema-validated JSON; code does all arithmetic, legality, and decisions.

## 5. Data sources and compliance

| Source | Used for | Status |
|---|---|---|
| Your DK/FD CSVs | contest, salaries, IDs, entries, site injury status | you download and upload them by hand |
| nflverse (GitHub releases) | NFL schedules with lines, weekly stats, snaps, injury reports, rosters, depth charts, expected-points model | CONDITIONAL: CC-BY-4.0 (attribute nflverse); upstream rights stay with the NFL; depth charts are ESPN-derived (personal use) |
| sportsdataverse (GitHub releases) | college play-by-play, schedules, lines | CONDITIONAL: pre-built release files, no scraping |
| Claude web search | game research and last-hour news | ALLOWED; facts carry their source URL |
| DraftKings / FanDuel sites, Rotowire, FantasyPros, Sports Reference, ESPN endpoints | — | not used (prohibited or unverified); blocked in code |

DK and FD Terms of Use could not be read from the build environment (their hosts are blocked here), so
the engine treats both sites as off-limits for automation. Before real money: confirm DFS is legal in
your state and that you meet the age limit, and use the sites' deposit and entry limits.

## 6. Evidence

See section 9 (filled from `docs/results/`).

## 7. Time and money per run

Measured on real replays (section 9). Budget defaults: $15 hard cap per run, 55-minute deadline with
per-stage timeouts; a safety lineup is written right after the baseline (first minute).

## 8. Known limits

- The saved rules for several game types are medium/low confidence (marked in the registry and printed
  as warnings). Confirm the cap and slots from the contest's rules page once per game type.
- Other sports (NBA, MLB, NHL, golf, MMA, NASCAR) run on the site-average baseline plus research; they
  have no stats adapter yet, so expect much weaker projections there than in football.
- The simulated field is a model. Its P(top 2.3%) is optimistic because it assumes our projections are
  right; the real measure is graded results.
- In this build environment most sports APIs and news sites are blocked by the network policy, so live
  research relies on web search results; your own machine or a less restricted environment can do more.
