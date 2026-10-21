"""Does the blinded LLM forecaster add information over the statistical baseline? (NFL, 2026)

For each week, every game's relevant players (per team: QB1-2, RB1-3, WR1-5, TE1-2, DST)
are forecast by the blinded forecaster from point-in-time structured facts only (official
injury report rows, usage history in raw stats). Games are after the model's training
cutoff, and no web research is run, so nothing can leak the outcomes. Both sources are then
scored against actual DraftKings points.

Variants: "blind" (the user's design: no prices, site averages, past fantasy points or
betting lines) and "market" (same, plus the game's spread/total/implied team points).

    python scripts/forecast_experiment.py --weeks 3-6 --variants blind
    python scripts/forecast_experiment.py --weeks 5-6 --variants market
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from dfs.backtest import add_actual_points
from dfs.distributions import mixture_mean
from dfs.io.contest_file import norm_name
from dfs.llm.backend import Runner, SDKBackend
from dfs.models import Contest, Player
from dfs.pipeline import agents_blind
from dfs.projections.nfl import project_week
from dfs.projections.nfl_provider import NFLProvider, load_data
from dfs.rules import load_rules
from dfs.stages import agents

CAPS = {"QB": 2, "RB": 3, "WR": 5, "TE": 2, "DST": 1}
OUT = Path("docs/results")
CACHE = Path("runs/cache/llm")


def week_pool(season: int, week: int, data):
    proj, frame = project_week(season, week, "draftkings.nfl", data=data)
    frame = frame.copy()
    frame["pos"] = frame["pos"].fillna("DST")
    frame.loc[frame["kind"] == "dst", "pos"] = "DST"
    frame = frame[frame["kind"].isin(["player", "dst"])]
    keep = []
    for (team, pos), g in frame.groupby(["team", "pos"]):
        keep.append(g.sort_values("mean", ascending=False).head(CAPS.get(pos, 0)))
    pool = pd.concat(keep)
    sched = data.schedules[(data.schedules["season"] == season) & (data.schedules["week"] == week)]
    game_of = {}
    for _, r in sched.iterrows():
        g = f"{r['away_team']}@{r['home_team']}"
        game_of[r["away_team"]] = g
        game_of[r["home_team"]] = g
    players, key_of = {}, {}
    for _, r in pool.iterrows():
        name = r["name"] if isinstance(r["name"], str) else f"{r['team']} DST"
        key = f"{r['team']}|dst|DST" if r["pos"] == "DST" else f"{r['team']}|{norm_name(name)}|{r['pos']}"
        players[key] = Player(key=key, name=name, team=r["team"], opponent=r["opponent"], positions=[r["pos"]],
                              game=game_of.get(r["team"]))
        key_of[r["key"]] = key
    pool["player_key"] = pool["key"].map(key_of)
    contest = Contest(rules=load_rules("draftkings", "nfl", "classic"), players=players, candidates=[],
                      slot_headers=[s.name for s in load_rules("draftkings", "nfl", "classic").slots],
                      source_file=f"synthetic-{season}-wk{week}", file_kind="dk_salaries",
                      games=sorted({p.game for p in players.values() if p.game}))
    return contest, pool


def actual_points(data, season, week, pool) -> dict[str, float]:
    add_actual_points(data, ["draftkings.nfl"])
    pg = data.player_games
    wk = pg[(pg["season"] == season) & (pg["week"] == week)]
    by_gsis = dict(zip(wk["gsis_id"], wk["fp_draftkings.nfl"]))
    snaps = dict(zip(wk["gsis_id"], wk["offense_snaps"].fillna(0)))
    tg = data.team_games
    tw = tg[(tg["season"] == season) & (tg["week"] == week)]
    by_team = dict(zip(tw["team"], tw["dst_draftkings.nfl"]))
    out, played = {}, {}
    for _, r in pool.iterrows():
        if r["pos"] == "DST":
            v = by_team.get(r["team"])
            out[r["player_key"]] = float(v) if v is not None and not np.isnan(v) else 0.0
            played[r["player_key"]] = v is not None and not np.isnan(v)
        else:
            out[r["player_key"]] = float(by_gsis.get(r["key"], 0.0) or 0.0)
            played[r["player_key"]] = (snaps.get(r["key"], 0) or 0) > 0 or r["key"] in by_gsis and by_gsis[r["key"]] != 0
    return out, played


async def forecast_week(season, week, variant, data, runner):
    contest, pool = week_pool(season, week, data)
    mapped = pool.copy()
    market, known, usage, sfacts = NFLProvider()._facts(contest, data, season, week, mapped)
    dossiers = {g: agents.structured_dossier(g, sfacts.get(g, []), []) for g in contest.games}
    blinded = {g: d.model_copy(update={"facts": [f for f in d.facts if agents_blind(f)]}) for g, d in dossiers.items()}
    tasks = agents.forecast_tasks(contest, blinded, usage, market if variant == "market" else {}, set(contest.players),
                                  model="opus")
    if variant == "market":  # a distinct task identity so the two variants never share cache entries
        for t in tasks:
            t.prompt += "\n\n(Market facts above are allowed for this run.)"
    res = await runner.run_all(tasks)
    fc, problems = agents.parse_forecasts(res, set(contest.players))
    actual, played = actual_points(data, season, week, pool)
    rows = []
    for _, r in pool.iterrows():
        k = r["player_key"]
        f = fc.get(k)
        rows.append({"season": season, "week": week, "variant": variant, "key": k, "pos": r["pos"], "team": r["team"],
                     "actual": actual[k], "played": bool(played[k]),
                     "b_mean": float(r["mean"]), "b_played": float(r["played_mean"]) if "played_mean" in r and pd.notna(r["played_mean"]) else float(r["mean"]),
                     "b_p10": float(r["p10"]), "b_p90": float(r["p90"]), "b_pzero": 1 - float(r.get("p_play", 1.0) if pd.notna(r.get("p_play", 1.0)) else 1.0),
                     "f_low": f.low if f else np.nan, "f_likely": f.likely if f else np.nan, "f_high": f.high if f else np.nan,
                     "f_pdnp": f.p_dnp if f else np.nan,
                     "f_mean": mixture_mean(f.low, f.likely, f.high, f.p_dnp) if f else np.nan,
                     "f_played_mean": mixture_mean(f.low, f.likely, f.high, 0.0) if f else np.nan})
    return rows, problems


def summarize(df: pd.DataFrame) -> str:
    out = []
    for variant, dv in df.groupby("variant"):
        d = dv.dropna(subset=["f_mean"])
        out.append(f"\n### Variant `{variant}`: {len(d)} player-games with both forecasts, weeks {sorted(d.week.unique())}\n")
        for label, sub, b, f in (("all pool players (unconditional means)", d, "b_mean", "f_mean"),
                                 ("players who played (means if active)", d[d.played], "b_played", "f_played_mean")):
            y = sub["actual"].to_numpy()
            bb, ff = sub[b].to_numpy(), sub[f].to_numpy()
            x = ff - bb
            w_opt = float(np.clip((x * (y - bb)).sum() / max((x * x).sum(), 1e-9), 0, 1))
            out.append(f"\n**{label}** (n = {len(sub)}). Optimal forecast weight w* (encompassing regression "
                       f"y - b = w (f - b)): **{w_opt:.2f}**\n")
            out.append("| Position | n | MAE baseline | MAE forecast | MAE 15% fc | MAE 50% | MAE 85% (user prior) | w* |")
            out.append("|---|---:|---:|---:|---:|---:|---:|---:|")
            for pos, g in [("ALL", sub)] + list(sub.groupby("pos")):
                y = g["actual"].to_numpy(); bb = g[b].to_numpy(); ff = g[f].to_numpy()
                mae = lambda p: float(np.mean(np.abs(y - p)))
                xx = ff - bb
                w = float(np.clip((xx * (y - bb)).sum() / max((xx * xx).sum(), 1e-9), 0, 1))
                out.append(f"| {pos} | {len(g)} | {mae(bb):.2f} | {mae(ff):.2f} | {mae(0.85 * bb + 0.15 * ff):.2f} | "
                           f"{mae(0.5 * bb + 0.5 * ff):.2f} | {mae(0.15 * bb + 0.85 * ff):.2f} | {w:.2f} |")
        p = d[d.played]
        cov_b = float(((p.actual >= p.b_p10) & (p.actual <= p.b_p90)).mean())
        cov_f = float(((p.actual >= p.f_low) & (p.actual <= p.f_high)).mean())
        out.append(f"\n80% interval coverage on players who played (target 80%): baseline {cov_b:.1%}, forecaster {cov_f:.1%}.")
        rho_b, rho_f = [], []
        for (wk, pos), g in p.groupby(["week", "pos"]):
            if len(g) >= 8:
                rho_b.append(spearmanr(g.b_played, g.actual).statistic)
                rho_f.append(spearmanr(g.f_played_mean, g.actual).statistic)
        out.append(f"Mean within-week, within-position Spearman rank correlation (played): baseline {np.nanmean(rho_b):.3f}, "
                   f"forecaster {np.nanmean(rho_f):.3f}.")
        # bootstrap by week-game cluster for the MAE difference
        rng = np.random.default_rng(0)
        g = d.assign(err_b=(d.actual - d.b_mean).abs(), err_f=(d.actual - d.f_mean).abs())
        clusters = [c for _, c in g.groupby(["week", "team"])]
        diffs = []
        for _ in range(2000):
            pick = rng.integers(0, len(clusters), len(clusters))
            s = pd.concat([clusters[i] for i in pick])
            diffs.append(float(s.err_f.mean() - s.err_b.mean()))
        lo, hi = np.percentile(diffs, [2.5, 97.5])
        out.append(f"MAE(forecast) - MAE(baseline), all pool players: {g.err_f.mean() - g.err_b.mean():+.3f} "
                   f"(95% team-week cluster bootstrap CI {lo:+.3f} to {hi:+.3f}).")
    return "\n".join(out)


async def main_async(a):
    data = load_data(a.season)
    runner = Runner(SDKBackend(), cache_dir=CACHE, budget_usd=a.budget, concurrency=a.concurrency, log=print)
    lo, hi = (int(x) for x in a.weeks.split("-"))
    rows, problems = [], []
    for variant in a.variants.split(","):
        for wk in range(lo, hi + 1):
            r, p = await forecast_week(a.season, wk, variant, data, runner)
            rows += r
            problems += p
            print(f"{variant} week {wk}: {sum(1 for x in r if not np.isnan(x['f_mean']))}/{len(r)} forecast")
    df = pd.DataFrame(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    tag = f"{a.season}_wk{a.weeks}_{a.variants.replace(',', '+')}"
    df.to_csv(OUT / f"forecast_experiment_{tag}.csv", index=False)
    text = (f"## Forecaster experiment: {a.season} weeks {a.weeks}, variants {a.variants}\n\n"
            f"Model calls: {runner.summary()}. Forecast problems: {len(problems)}.\n" + summarize(df))
    (OUT / f"forecast_experiment_{tag}.md").write_text(text + "\n")
    print(text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--weeks", default="3-6")
    ap.add_argument("--variants", default="blind")
    ap.add_argument("--budget", type=float, default=20.0)
    ap.add_argument("--concurrency", type=int, default=5)
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
