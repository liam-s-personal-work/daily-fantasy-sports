"""Live projections for an upcoming NFL week, before any contest file exists.

Runs the real research (Claude + web search) and blinded-forecast stages on every game of the
week, blends with the nflverse baseline and writes a projections table, timing and spend.
When the DraftKings/FanDuel file arrives, `dfs run <file>` builds the lineups.

    python scripts/live_week.py --season 2026 --week 7
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from forecast_experiment import week_pool  # noqa: E402

from dfs.blend import blend
from dfs.llm.backend import Runner, SDKBackend
from dfs.models import Projection
from dfs.pipeline import agents_blind
from dfs.projections.nfl_provider import NFLProvider, load_data
from dfs.stages import agents


async def main_async(a) -> None:
    t0 = time.monotonic()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    data = load_data(a.season, max_age_hours=1.0)
    contest, pool = week_pool(a.season, a.week, data)
    if a.games:
        keep = set(a.games.split(","))
        contest = contest.model_copy(update={"games": [g for g in contest.games if g in keep]})
    market, known, usage, sfacts = NFLProvider()._facts(contest, data, a.season, a.week, pool)
    runner = Runner(SDKBackend(), cache_dir=Path("runs/cache/llm"), budget_usd=a.budget, concurrency=a.concurrency,
                    log=print)
    marks = {"setup": round(time.monotonic() - t0, 1)}
    res = await runner.run_all(agents.research_tasks(contest, known, market, max_searches=a.searches))
    dossiers = agents.parse_research(res)
    for g in contest.games:
        d = dossiers.get(g) or agents.structured_dossier(g, [], [])
        start = len(d.facts)
        d.facts.extend(f.model_copy(update={"id": f"{g}.S{start + i}"}) for i, f in enumerate(sfacts.get(g, []), 1))
        dossiers[g] = d
    marks["research"] = round(time.monotonic() - t0, 1)
    blinded = {g: d.model_copy(update={"facts": [f for f in d.facts if agents_blind(f)]}) for g, d in dossiers.items()}
    fres = await runner.run_all(agents.forecast_tasks(contest, blinded, usage, {}, set(contest.players)))
    forecasts, problems = agents.parse_forecasts(fres, set(contest.players))
    marks["forecast"] = round(time.monotonic() - t0, 1)
    base = {r["player_key"]: Projection(player_key=r["player_key"], mean=float(r["mean"]), p10=float(r["p10"]),
                                        p50=float(r["p50"]), p90=float(r["p90"]),
                                        p_zero=1 - float(r["p_play"]) if pd.notna(r.get("p_play")) else 0.0)
            for _, r in pool.iterrows() if r["player_key"] in contest.players
            and contest.players[r["player_key"]].game in contest.games}
    out_keys = agents.ruled_out_keys(contest, dossiers)
    proj = blend({k: v for k, v in base.items() if k not in out_keys},
                 {k: v for k, v in forecasts.items() if k not in out_keys}, contest.players, "nfl")
    rows = [{"player": contest.players[k].name, "team": contest.players[k].team, "pos": contest.players[k].positions[0],
             "game": contest.players[k].game, "mean": round(p.mean, 2), "p10": round(p.p10, 1), "p90": round(p.p90, 1),
             "baseline": round(base[k].mean, 2) if k in base else None,
             "forecast_likely": forecasts[k].likely if k in forecasts else None,
             "why": (forecasts[k].rationale if k in forecasts else "")[:200]} for k, p in proj.items()]
    df = pd.DataFrame(rows).sort_values("mean", ascending=False)
    df.to_csv(out / f"projections_{a.season}_wk{a.week}.csv", index=False)
    (out / "dossiers.json").write_text(json.dumps({g: d.model_dump() for g, d in dossiers.items()}, indent=1))
    marks["total"] = round(time.monotonic() - t0, 1)
    lines = [f"# Live projections: {a.season} week {a.week} (DraftKings scoring)", "",
             f"Games: {len(contest.games)}; facts gathered: {sum(len(d.facts) for d in dossiers.values())}; "
             f"ruled out by research: {len(out_keys)}; forecasts: {len(forecasts)} (problems: {len(problems)})",
             f"Timing (s): {marks}", f"Model calls: {runner.summary()}", ""]
    for pos in ("QB", "RB", "WR", "TE", "DST"):
        lines += [f"## Top {pos}", "", "| Player | Team | Game | Mean | p10 | p90 | Baseline | Forecast |",
                  "|---|---|---|---:|---:|---:|---:|---:|"]
        for _, r in df[df.pos == pos].head(8).iterrows():
            lines.append(f"| {r.player} | {r.team} | {r.game} | {r['mean']} | {r.p10} | {r.p90} | {r.baseline} | {r.forecast_likely} |")
        lines.append("")
    (out / "README.md").write_text("\n".join(lines))
    print("\n".join(lines[:6]))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--week", type=int, required=True)
    ap.add_argument("--games", help="comma-separated AWAY@HOME subset")
    ap.add_argument("--searches", type=int, default=6)
    ap.add_argument("--budget", type=float, default=12.0)
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--out", default="runs/live")
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
