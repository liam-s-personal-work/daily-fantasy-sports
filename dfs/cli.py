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


def _now(text: str | None) -> datetime | None:
    if not text:
        return None
    t = datetime.fromisoformat(text)
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _run(args) -> int:
    from dfs.pipeline import RunConfig, run
    cfg = RunConfig(out_dir=Path(args.out), budget_usd=args.budget, deadline_min=args.deadline,
                    web_research=not args.no_web, forecaster=not args.no_forecast, n_sims=args.sims,
                    target_top=args.target_top, field_size=args.field_size, user_rules=_user_rules(args.rules),
                    season=args.season, week=args.week, replay_only=args.replay, allow_lookahead=args.allow_lookahead,
                    kind=args.kind, provider=args.provider)
    res = run(args.file, cfg, now=_now(args.now))
    print(f"\nupload file: {res.upload_path}")
    if res.rules_upload_path:
        print(f"lineup with your rules: {res.rules_upload_path}")
    elif args.rules:
        err = res.report.get("decision", {}).get("user_rules_error", "unknown reason")
        print(f"WARNING: no lineup with your rules: {err}")
    print(f"report: {res.run_dir / 'report.md'}")
    return 0


def _lastcall(args) -> int:
    from dfs.lastcall import lastcall
    out = lastcall(Path(args.run_dir), threshold=args.threshold, web=not args.no_web, now=_now(args.now),
                   entries_file=args.entries)
    from dfs.lastcall import load_run
    from dfs.models import Lineup
    c = load_run(Path(args.run_dir))["contest"]
    if out.get("news_failed"):
        print(f"\nWARNING: the news/inactives check FAILED for {', '.join(out['news_failed'])}; "
              "those picks were not checked")
    print(f"\n{'CHANGED' if out['changed'] else 'unchanged'}: {out['reason']}")
    for k, u in (out.get("news") or {}).items():
        print(f"  news  {c.players[k].name}: {u.get('status')}: {u.get('text', '')[:140]}")
    for s in out["final"].slots:
        p = c.players[s.player_key]
        print(f"  {s.slot:<6} {p.name} ({p.team}){'  [locked]' if s.player_key in set(out.get('locked') or []) else ''}")
    held = {}
    try:
        st = load_run(Path(args.run_dir))
        held = {eid: [s.site_id for s in Lineup.model_validate(lu).slots]
                for eid, lu in zip(st["report"].get("entry_ids") or [], st["report"].get("lineups") or [])}
    except Exception:  # the summary below is a convenience; lastcall.json has everything
        pass
    if held:
        import csv as _csv

        from dfs.io.upload import cell_id
        rows = list(_csv.reader(open(out["upload"])))[1:]
        for r in rows:
            before = held.get(r[0])
            n = sum(a != cell_id(b) for a, b in zip(before or [], r[4:])) if before else None
            print(f"  entry {r[0]}: " + ("unchanged" if n == 0 else f"{n} slot(s) changed" if n else "rebuilt"))
    e = out.get("entries") or {}
    for w in out.get("warnings") or []:
        print(f"WARNING: {w}")
    if e.get("reservations_left_empty"):
        print(f"WARNING: empty entries left as they are on the site (no legal lineup from the games not yet "
              f"started): {', '.join(map(str, e['reservations_left_empty']))}")
    if e.get("could_not_repair"):
        print(f"WARNING: entries still holding a ruled-out player (no legal repair): "
              f"{', '.join(map(str, e['could_not_repair']))}")
    m = out.get("metric")
    if m and out.get("current"):
        print(f"  {m}: current {out['current'].get(m, 0):.4f}, final {(out.get('new') or out['current']).get(m, 0):.4f}")
    print(f"upload file: {out['upload']}  (model spend ${out['llm'].get('cost_usd', 0):.2f}, "
          f"{out['llm'].get('web_searches', 0)} searches; details in lastcall.json)")
    return 3 if out.get("news_failed") and not out.get("news") else 0


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
    try:
        return bt_main(args.rest, prog="dfs backtest")
    except (FileNotFoundError, ValueError, KeyError) as e:
        print(f"dfs backtest: {e}", file=sys.stderr)
        return 2


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["backtest"]:  # its options belong to dfs.backtest's own parser (argparse REMAINDER drops '--x')
        return _backtest(argparse.Namespace(rest=argv[1:]))
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
    s.add_argument("--kind", choices=["cash", "gpp"],
                   help="contest type (salary files do not say; entries files are read from contest names)")
    s.add_argument("--provider", choices=["generic"], help="use the file's site averages instead of the stats baseline")
    s.add_argument("--allow-lookahead", action="store_true",
                   help="with --now in the past, still search the web (it will see the games' results)")
    s.set_defaults(fn=_run)

    s = sub.add_parser("lastcall"); s.add_argument("run_dir"); s.add_argument("--threshold", type=float, default=0.01)
    s.add_argument("--no-web", action="store_true")
    s.add_argument("--now", help="ISO time to run as; default: the real time")
    s.add_argument("--entries", help="a freshly downloaded entries file (the site's current lineups and LOCKED cells)")
    s.set_defaults(fn=_lastcall)

    s = sub.add_parser("windows"); s.add_argument("run_dir"); s.add_argument("--lead", type=int, default=60)
    s.set_defaults(fn=_windows)

    s = sub.add_parser("grade"); s.add_argument("run_dir"); s.add_argument("--standings")
    s.add_argument("--no-update", action="store_true"); s.set_defaults(fn=_grade)

    s = sub.add_parser("backtest", help="baseline accuracy on real games (options: dfs backtest --help)", add_help=False)
    s.set_defaults(fn=_backtest)

    args = p.parse_args(argv)
    from dfs.io.contest_file import ContestFileError
    from dfs.io.upload import UploadError
    from dfs.optimize.ilp import Infeasible
    from dfs.pipeline import SlateStarted
    try:
        return args.fn(args)
    except (SlateStarted, Infeasible, ContestFileError, FileNotFoundError, UploadError, ValueError) as e:
        print(f"dfs {args.cmd}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
