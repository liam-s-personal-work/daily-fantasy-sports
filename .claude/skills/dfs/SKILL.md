---
name: dfs
description: Build a DraftKings or FanDuel lineup from a contest CSV (salary file, player list, or entries file) with research, blinded forecasts, review agents and an exact solver; also last-hour checks and grading. Use when the user drops a DK/FD CSV or asks for a DFS lineup, a last-call update, or a grade of a past run.
---

# DFS lineup engine

The engine is the `dfs` Python package in this repo (`pip install -e .[data,agents]`). It does the
work in code and calls Claude agents only for research, forecasting and review. Your job is to run
it, surface its warnings, and relay its results honestly.

## Run a contest

1. Find the user's CSV (DKSalaries.csv, DKEntries.csv, an FD players list or FD entries template).
2. `python -m dfs.cli parse <file>`: show what was detected (site, sport, game type, cap, slots,
   entries) and every WARNING. If the saved rules are not high-confidence, ask the user to confirm
   the cap and slots from the contest's rules page before entering money.
3. Ask for, or default, the contest kind (cash vs tournament) and field size, and whether the user
   has pick rules. Pick rules go in a YAML file, e.g. `lock: [Name]`, `exclude: [Name]`,
   `max_from_team: 3`, `stack: [{team: BUF, positions: [QB, WR], min: 2}]`.
4. `python -m dfs.cli run <file> [--kind cash|gpp] [--rules my_rules.yaml] [--field-size N]`. Pass
   `--kind` for salary files (they do not say which it is; entries files are read from contest names).
   Default budget $15, 55-minute deadline. Use `--no-web` for a quick structured-data-only run.
   The lineup is the one with the highest expected score (`--objective points`, the default). Pass
   `--objective contest` only if the user wants P(top x%) or P(cash) maximised instead.
5. Report: the final lineup table from `runs/<id>/report.md`, the upload file path
   (`runs/<id>/upload.csv`), the second lineup with the user's rules if any, the strategist's
   weakest pick and close calls, and the checker's verdict. Quote the model's P(top x%) only as a
   model estimate. It assumes our projections are right, so it is optimistic.
6. If the user wants each pick's reasoning and point range, `python -m dfs.cli explain runs/<id>` writes
   `runs/<id>/lineup_explained.csv`.

## Last hour before lock

`python -m dfs.cli lastcall runs/<id>`: searches news on every pick, checks inactives,
re-forecasts affected NFL players, re-projects NBA players from confirmed starting fives, keeps started
games locked, and changes the lineup only if a pick is out or a new lineup leads by more than 1%. Hand
over `runs/<id>/upload_final.csv`. It assumes the site holds the run's `upload.csv`; if the user entered
something else, or games have started, ask them to download the entries file again and pass
`--entries <file>`. If it prints
"WARNING: the news/inactives check FAILED", say so plainly: those picks were not checked.

## After the slate

`python -m dfs.cli grade runs/<id> [--standings contest-standings.csv]`: actual score, the
hindsight-best lineup, percentile when standings are given, and the step that cost the most. Blend
weights update from the graded errors.

## Rules you must keep

- Never log in to, scrape, or automate DraftKings or FanDuel. The user downloads and uploads CSVs.
- Never promise results, or that a lineup is the best possible one. Every contest has variance; the
  engine's own measured results are in `docs/PLAN.md` and `docs/results/oracle_gap.md`.
