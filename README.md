# daily-fantasy-sports

A lineup engine for DraftKings and FanDuel. Give it the CSV you download for a contest (salary file,
player list, or the entries file with your registered entries). It returns a legal upload file built
to maximise that contest's own objective: P(top 2.3%) in a tournament, P(cash) in a cash game. It
also writes a report covering the top 3 per roster slot, close calls, the weakest pick and every
check that ran.

The plan, the design decisions and every measured result are in **[docs/PLAN.md](docs/PLAN.md)**.

## Install

```
pip install -e ".[data,agents,dev]"     # Python 3.11+
```

- `data` pulls in nflverse access.
- `agents` pulls in the Claude Agent SDK, which needs Claude credentials in the environment.
- Without `agents` the engine still runs end to end, using the statistical baseline only.

## Use

```
dfs parse    DKEntries.csv                      # what was detected, and every warning (no model calls)
dfs run      DKEntries.csv --rules my_rules.yaml --field-size 5000
                                                # -> runs/<id>/upload.csv, upload_my_rules.csv, report.md
dfs windows  runs/<id>                          # when to run the last call for each kickoff window
dfs lastcall runs/<id>                          # news + inactives on every pick -> runs/<id>/upload_final.csv
dfs grade    runs/<id> [--standings contest-standings.csv]   # after the slate: score, best lineup, failed step
dfs rules    [draftkings.nfl.classic]           # saved contest rules and how well each is verified
dfs backtest --season 2026 --weeks 3-6          # NFL baseline accuracy on real games
```

Useful `run` flags:

- `--kind cash|gpp`: the contest type. Salary files do not say which; entries files are read from contest
  names.
- `--no-web`: structured data only, no web research.
- `--no-forecast`: baseline only.
- `--budget`: hard dollar cap, default $15.
- `--deadline`: minutes, default 55.
- `--now`: test on a past slate as of a time before its kickoff. Web research then stays off unless you
  pass `--allow-lookahead`, because it would see the results.
- `--replay`: answer every model stage from cache.

`dfs lastcall runs/<id> --entries DKEntries.csv` checks against a freshly downloaded entries file, whose
locked cells are the site's own record.

Pick rules (`--rules`) are a small YAML file; see [examples/my_rules.yaml](examples/my_rules.yaml).

Inside Claude Code, the `/dfs` skill runs the same steps.

## Ground rules

- The engine never logs in to, scrapes or automates DraftKings or FanDuel. You download the CSV and
  upload the file it writes.
- Research uses web search, with page fetches limited to an allow-list enforced by the tool layer.
  Facts from DFS sites and from sites that forbid automated access are dropped (`dfs/sources.py`).
- No method can promise a top-2.3% finish in every contest; PLAN.md §2 explains why. The engine
  optimises for that finish and measures how often it gets there.
- Before playing for money, check that DFS is legal where you are and that you meet the age limit.

## Tests

```
python -m pytest -q                      # offline suite (synthetic, format-exact fixtures)
python -m pytest -q -m network           # tests that download nflverse data
DFS_LIVE=1 python -m pytest -q -m live   # tests that make real Claude calls (costs money)
```
