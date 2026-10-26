# daily-fantasy-sports

A lineup engine for DraftKings and FanDuel. Give it the CSV you download for a contest (salary file,
player list, or the entries file with your registered entries). It returns a legal upload file with
the highest expected score. On held-out historical slates that rule came closest to the best lineup in
hindsight ([docs/results/oracle_gap.md](docs/results/oracle_gap.md)). `--objective contest` instead
maximises the contest's own objective: P(top 2.3%) in a tournament, P(cash) in a cash game. It also
writes a report covering the top 3 per roster slot, close calls, the weakest pick and every check that
ran.

The plan, the design decisions and every measured result are in **[docs/PLAN.md](docs/PLAN.md)**.
How close the lineups come to the best possible lineup on held-out slates is in
**[docs/results/oracle_gap.md](docs/results/oracle_gap.md)**.

## Install

```
pip install -e ".[data,agents,dev]"     # Python 3.11+
```

Install it editable (`-e`), as above. The learned blend weights in `data/learned/` and the data caches live
in the repo folder, so a plain `pip install .` runs on the priors and says so. In that case its data cache
goes to `~/.cache/dfs`; set `DFS_CACHE_DIR` to put it anywhere else.

- `data` pulls in nflverse access.
- `agents` pulls in the Claude Agent SDK, which needs Claude credentials in the environment.
- Without `agents` the engine still runs end to end, using the statistical baseline only.

## Use

```
dfs parse    DKEntries.csv                      # what was detected, and every warning (no model calls)
dfs run      DKEntries.csv --rules my_rules.yaml --field-size 5000
                                                # -> runs/<id>/upload.csv, upload_my_rules.csv, report.md
dfs explain  runs/<id>                          # lineup_explained.csv: each pick's reasoning and point range
dfs windows  runs/<id>                          # when to run the last call for each kickoff window
dfs lastcall runs/<id>                          # news + inactives on every pick -> runs/<id>/upload_final.csv
dfs grade    runs/<id> [--standings contest-standings.csv]   # after the slate: score, best lineup, failed step
dfs rules    [draftkings.nfl.classic]           # saved contest rules and how well each is verified
dfs backtest --season 2026 --weeks 3-6          # NFL baseline accuracy on real games
```

Useful `run` flags:

- `--kind cash|gpp`: the contest type. Salary files do not say which; entries files are read from contest
  names.
- `--objective points|contest`: `points` (default) takes the highest expected score; `contest` maximises
  P(top x%) against a simulated field, or P(cash).
- `--no-web`: structured data only, no web research.
- `--no-forecast`: baseline only.
- `--budget`: hard dollar cap, default $15.
- `--deadline`: minutes, default 55.
- `--now`: test on a past slate as of a time before its kickoff. Web research then stays off unless you
  pass `--allow-lookahead`, because it would see the results.
- `--replay`: answer every model stage from cache.

The last call assumes the site holds the lineups the run wrote (`upload.csv`). If you entered something
else, or want to be safe after the first kickoff, download the entries file again and pass it:
`dfs lastcall runs/<id> --entries DKEntries.csv`. Each entry then starts from what the site holds, and the
site's `(LOCKED)` cells stay where they are.

NBA projections add a learned minutes model (`data/learned/nba_minutes_*`, trained by
`scripts/fit_nba_minutes.py`) to the stats baseline. At last call, teams whose starting five is confirmed
switch to a starter-aware version of it.

Keep your contest files wherever you like. In the repo folder, `DK*.csv`, `FD*.csv`, `FanDuel*.csv` and
`*contest-standings*.csv` are git-ignored, so they are never committed by accident.

Each run also writes `lineup_explained.csv` (and the last call `lineup_explained_final.csv`): every pick with
its reasoning, projected points, the 10th-90th percentile range and the chance he sits. It is for reading;
upload `upload.csv`.

Upload files name every player. DraftKings cells use DK's own `Name (ID)` text, which DK accepts; FanDuel
cells stay as IDs, with a `*_readable.csv` copy that shows the names.

Pick rules (`--rules`) are a small YAML file; see [examples/my_rules.yaml](examples/my_rules.yaml).

Inside Claude Code, the `/dfs` skill runs the same steps.

## Ground rules

- The engine never logs in to, scrapes or automates DraftKings or FanDuel. You download the CSV and
  upload the file it writes.
- Research uses web search, with page fetches limited to an allow-list enforced by the tool layer.
  Facts from DFS sites and from sites that forbid automated access are dropped (`dfs/sources.py`).
- No method can promise a top-2.3% finish in every contest, or the best possible lineup on any night;
  PLAN.md §2 and docs/results/oracle_gap.md explain why. The engine measures how close it gets.
- Before playing for money, check that DFS is legal where you are and that you meet the age limit.

## Tests

```
python -m pytest -q                      # offline suite (synthetic, format-exact fixtures)
python -m pytest -q -m network           # tests that download nflverse and sportsdataverse data
```

One network test also needs a real FD salary file in `data/cache/real_samples/` and skips without it.

## Data credits

- NFL data: [nflverse](https://github.com/nflverse/nflverse-data), CC-BY-4.0 (FTN charting data
  CC-BY-SA-4.0). The NFL keeps its rights in the underlying data. The derived results in `docs/results/`
  and `data/learned/` are built from it. Its depth charts are ESPN-derived copies: used for personal,
  non-commercial analysis and not redistributed.
- College data: [sportsdataverse](https://github.com/sportsdataverse/sportsdataverse-data) release files,
  CC-BY-4.0 (cfbfastR play-by-play; ESPN-derived box scores, rosters and lines). Used for personal,
  non-commercial analysis; not redistributed. See `docs/PLAN.md` §5.
- NBA data: the same sportsdataverse release files (ESPN-derived player box scores), on the same terms.
  How the NBA baseline was tuned: [docs/results/nba_baseline.md](docs/results/nba_baseline.md).
