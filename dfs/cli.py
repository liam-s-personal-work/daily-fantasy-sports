"""Command line: ``dfs run|lastcall|grade|backtest|rules|parse``.

    dfs parse  DKSalaries.csv                  # what the engine sees in a file (no model calls)
    dfs run    DKEntries.csv [--no-web] ...    # the main run -> runs/<id>/upload.csv + report.md
    dfs windows  runs/<id>                     # when to run the last call for each kickoff window
    dfs lastcall runs/<id>                     # last-hour news/inactives check -> upload_final.csv
    dfs grade  runs/<id> [--standings contest-standings.csv]
    dfs backtest --season 2026 --weeks 3-6     # baseline accuracy on real games
    dfs rules  [key]                           # saved contest rules and how well they are verified
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def _rules(args) -> int:
    from dfs.rules import load_rules, registry_keys
    keys = [args.key] if args.key else registry_keys()
    for k in keys:
        site, sport, gt = k.split(".")
        r = load_rules(site, sport, gt)
        print(f"{k}: cap {r.salary_cap}, slots {[s.name for s in r.slots]}, min_games {r.min_games}, "
              f"min_teams {r.min_teams}, max_per_team {r.max_per_team}, confidence {r.confidence}")
    return 0


def _parse(args) -> int:
    from dfs.io.contest_file import load_contest
    c = load_contest(args.file, sport=args.sport, game_type=args.game_type)
    r = c.rules
    print(f"{c.file_kind}: {r.site.value} {r.sport} {r.game_type} ({c.kind.value}); cap {r.salary_cap}; "
          f"slots {c.slot_headers}; {len(c.players)} players, {len(c.candidates)} rows, {len(c.games)} games, "
          f"{len(c.entries)} entries")
    for w in c.warnings:
        print("WARNING:", w)
    return 0


def _user_rules(path: str | None):
    if not path:
        return None
    from dfs.models import UserRules
    import yaml
    return UserRules(**yaml.safe_load(Path(path).read_text()))


def _run(args) -> int:
    from dfs.pipeline import RunConfig, run
    cfg = RunConfig(out_dir=Path(args.out), budget_usd=args.budget, deadline_min=args.deadline,
                    web_research=not args.no_web, forecaster=not args.no_forecast, n_sims=args.sims,
                    target_top=args.target_top, field_size=args.field_size, user_rules=_user_rules(args.rules),
                    season=args.season, week=args.week, replay_only=args.replay)
    now = datetime.fromisoformat(args.now) if args.now else None
    if now is not None and now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    res = run(args.file, cfg, now=now)
    print(f"\nupload file: {res.upload_path}")
    if res.rules_upload_path:
        print(f"lineup with your rules: {res.rules_upload_path}")
    print(f"report: {res.run_dir / 'report.md'}")
    return 0


def _lastcall(args) -> int:
    from dfs.lastcall import lastcall
    out = lastcall(Path(args.run_dir), threshold=args.threshold, web=not args.no_web)
    print(json.dumps(out, indent=1, default=str))
    return 0


def _windows(args) -> int:
    from dfs.lastcall import kickoff_windows
    for w in kickoff_windows(Path(args.run_dir), args.lead):
        print(f"kickoff {w['kickoff']}: run `dfs lastcall {args.run_dir}` at {w['run_lastcall_at']} for {', '.join(w['picks'])}")
    return 0


def _grade(args) -> int:
    from dfs.grading_run import grade_run
    out = grade_run(Path(args.run_dir), standings=args.standings, update=not args.no_update)
    print(json.dumps(out, indent=1, default=str))
    return 0


def _backtest(args) -> int:
    from dfs.backtest import main as bt_main
    return bt_main(args.rest)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="dfs", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("rules"); s.add_argument("key", nargs="?"); s.set_defaults(fn=_rules)
    s = sub.add_parser("parse"); s.add_argument("file"); s.add_argument("--sport"); s.add_argument("--game-type")
    s.set_defaults(fn=_parse)

    s = sub.add_parser("run")
    s.add_argument("file")
    s.add_argument("--out", default="runs")
    s.add_argument("--budget", type=float, default=15.0, help="hard cap on model spend for the run (USD)")
    s.add_argument("--deadline", type=float, default=55.0, help="minutes; stages shrink to fit")
    s.add_argument("--no-web", action="store_true", help="skip web research (structured data only)")
    s.add_argument("--no-forecast", action="store_true", help="skip the blinded forecaster (baseline only)")
    s.add_argument("--sims", type=int, default=10000)
    s.add_argument("--target-top", type=float, default=0.023)
    s.add_argument("--field-size", type=int)
    s.add_argument("--rules", help="YAML file with your pick rules (solved as a second lineup)")
    s.add_argument("--season", type=int); s.add_argument("--week", type=int)
    s.add_argument("--replay", action="store_true", help="answer model stages from cache only")
    s.add_argument("--now", help="ISO time to run as (e.g. 2026-10-18T16:00:00+00:00); default: the real time")
    s.set_defaults(fn=_run)

    s = sub.add_parser("lastcall"); s.add_argument("run_dir"); s.add_argument("--threshold", type=float, default=0.01)
    s.add_argument("--no-web", action="store_true"); s.set_defaults(fn=_lastcall)

    s = sub.add_parser("windows"); s.add_argument("run_dir"); s.add_argument("--lead", type=int, default=60)
    s.set_defaults(fn=_windows)

    s = sub.add_parser("grade"); s.add_argument("run_dir"); s.add_argument("--standings")
    s.add_argument("--no-update", action="store_true"); s.set_defaults(fn=_grade)

    s = sub.add_parser("backtest"); s.add_argument("rest", nargs=argparse.REMAINDER); s.set_defaults(fn=_backtest)

    args = p.parse_args(argv)
    from dfs.io.contest_file import ContestFileError
    from dfs.optimize.ilp import Infeasible
    from dfs.pipeline import SlateStarted
    try:
        return args.fn(args)
    except (SlateStarted, Infeasible, ContestFileError, FileNotFoundError) as e:
        print(f"dfs {args.cmd}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
