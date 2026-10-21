"""Walk-forward backtest of the NFL baseline against naive baselines on real nflverse games.

For each (season, week) the model is projected from data strictly before that week; actual
fantasy points come from nflverse box scores scored by ``dfs.scoring`` (DK and FD tables).

Evaluation pool (same rows for every model): QB/RB/WR/TE player-games in the target week
with >= 25% offensive snap share, plus every DST that played. Model point forecast = mean
conditional on playing (QBs: on starting), the quantity comparable with "average when
played" baselines. A second pool adds each team's top players (QB1, RB1-2, WR1-3, TE1) by the
model's unconditional mean or by season average, whether or not they played, scored against
the unconditional mean: it measures availability calls (correct outs and misses).

CLI: ``python -m dfs.backtest --season 2026 --weeks 3-6 [--report --tune-seasons 2024,2025]``
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from dfs.data import nflverse as nv
from dfs.projections.nfl import NFLData, NFLParams, build_context, offense_stat_line, player_means, project_context
from dfs.scoring import score

TABLES = ("draftkings.nfl", "fanduel.nfl")
POSITIONS = ("QB", "RB", "WR", "TE", "DST", "K")
REPORT_PATH = nv.REPO_ROOT / "docs" / "results" / "nfl_baseline_backtest.md"
TUNE_LOG_PATH = REPORT_PATH.with_name("nfl_baseline_tuning.json")
SNAP_MIN = 0.25
TOP_N = {"QB": 1, "RB": 2, "WR": 3, "TE": 1}


# --------------------------------------------------------------------------- actual points


def add_actual_points(data: NFLData, tables: Iterable[str] = TABLES) -> None:
    """Score every historical player-game and team DST once with dfs.scoring (cached on ``data``)."""
    pg = data.player_games
    if all(f"fp_{t}" in pg.columns for t in tables):
        return
    recs = offense_stat_line(pg).to_dict("records")
    for t in tables:
        pg[f"fp_{t}"] = [score(r, t, role="offense") for r in recs]
    tg = data.team_games
    rows = tg.to_dict("records")
    for t in tables:
        tg[f"dst_{t}"] = [score(dst_stat_line(r, t), t, role="dst") if r["played"] else np.nan for r in rows]
    kg = data.kicker_games
    if len(kg):
        lines = [{"xp_made": float(x or 0), "fg_made_distances": _distances(d)} for x, d in
                 zip(kg["pat_made"], kg["fg_made_list"])]
        for t in tables:
            kg[f"fp_{t}"] = [score(line, t, role="kicker") for line in lines]


def _distances(v) -> list[float]:
    if not isinstance(v, str) or not v.strip():
        return []
    return [float(x) for x in v.replace(",", ";").split(";") if x.strip()]


def kicker_actuals(data: NFLData, season: int, week: int, table_id: str) -> pd.Series:
    """Team kicking points in a week (all kickers of the team), by team."""
    kg = data.kicker_games
    k = kg[(kg["season"] == season) & (kg["week"] == week)]
    return k.groupby(k["team"].map(lambda t: nv.canonicalize(t, strict=False)))[f"fp_{table_id}"].sum()


def kicker_baselines(data: NFLData, season: int, week: int, teams: Iterable[str], table_id: str) -> pd.DataFrame:
    kg = nv.before(data.kicker_games, season, week, min_season=season - 1).copy()
    kg["team"] = kg["team"].map(lambda t: nv.canonicalize(t, strict=False))
    per = kg.groupby(["season", "week", "team"], as_index=False)[f"fp_{table_id}"].sum()
    cur = per[per["season"] == season].groupby("team")[f"fp_{table_id}"].mean()
    prev = per[per["season"] == season - 1].groupby("team")[f"fp_{table_id}"].mean()
    l3 = per.sort_values(["season", "week"]).groupby("team").tail(3).groupby("team")[f"fp_{table_id}"].mean()
    idx = pd.Index(list(teams), name="team")
    return pd.DataFrame({"base_std": cur.reindex(idx).fillna(prev.reindex(idx)), "base_last3": l3.reindex(idx),
                         "base_xfp": np.nan}, index=idx)


def dst_stat_line(row: pd.Series, table_id: str) -> dict:
    """One team-game row -> dfs.scoring DST stats.

    Sacks are the opponent's official sacks taken (team sacks are not credited to a defender);
    defensive TDs include fumble-recovery TDs; blocked kicks include PATs. Points allowed: DK =
    opponent points minus 6 per opponent defensive TD (the XP after it still counts) and 2 per
    safety; FD = the site formula 6 * (offensive TDs + own-fumble-recovery TDs) + 2 * 2pt + XP +
    3 * FG of the opponent. These conventions reproduce the 2026 weeks 1-5 DST averages printed in
    real DK and FD salary files for every team on the week-6 main slates (the 7 games involving a
    defensive or return TD decide between the alternatives).
    """
    v = lambda k: float(np.nan_to_num(row.get(k, 0.0) if hasattr(row, "get") else row[k]))
    if table_id.startswith("fanduel") and pd.notna(row.get("opp_fd_pa")):
        pa = v("opp_fd_pa")
    else:
        pa = v("opp_points") - 6 * v("opp_def_tds") - 2 * v("opp_def_safeties")
    sacks = v("opp_sacks_suffered") if pd.notna(row.get("opp_sacks_suffered")) else v("def_sacks")
    return {"sack": sacks, "interception": v("def_interceptions"), "fumble_recovery": v("fumble_recovery_opp"),
            "def_td": v("def_tds"), "return_td": v("special_teams_tds"), "safety": v("def_safeties"),
            "blocked_kick": v("blocks"), "two_pt_return": v("def_2pt_made"), "points_allowed": max(pa, 0.0)}


# --------------------------------------------------------------------------- naive baselines


def naive_baselines(data: NFLData, season: int, week: int, ids: pd.Series, table_id: str,
                    positions: pd.Series | None = None) -> pd.DataFrame:
    """Season-to-date average, last-3-games average and ff_opportunity expected-points average,
    from games strictly before (season, week). Players without a game this season fall back to
    last season's average, then to the position's median season-to-date average. ``positions``
    (aligned with ``ids``) supplies the position of players with no game in either season, who
    would otherwise get 0."""
    pg = nv.before(data.player_games, season, week, min_season=season - 1)
    pg = pg[(pg["offense_pct"].fillna(0) > 0) | ((pg["targets"] + pg["carries"] + pg["attempts"]) > 0)]
    fp = pg[f"fp_{table_id}"]
    xfp = pg["x_fp"] - (0.5 * pg["x_rec"] if table_id.startswith("fanduel") else 0.0)
    pg = pg.assign(_fp=fp, _xfp=xfp)
    cur, prev = pg[pg["season"] == season], pg[pg["season"] == season - 1]
    std = cur.groupby("gsis_id")[["_fp", "_xfp"]].mean()
    last = prev.groupby("gsis_id")[["_fp", "_xfp"]].mean()
    l3 = pg.sort_values("wk").groupby("gsis_id").tail(3).groupby("gsis_id")["_fp"].mean()
    pos = pg.drop_duplicates("gsis_id", keep="last").set_index("gsis_id")["pos"]
    out = pd.DataFrame(index=pd.Index(ids.unique(), name="gsis_id"))
    out["base_std"] = std["_fp"].reindex(out.index).fillna(last["_fp"].reindex(out.index))
    out["base_xfp"] = std["_xfp"].reindex(out.index).fillna(last["_xfp"].reindex(out.index))
    out["base_last3"] = l3.reindex(out.index)
    med = std.join(pos).groupby("pos")[["_fp", "_xfp"]].median()
    p = pos.reindex(out.index)
    if positions is not None:
        given = pd.Series(np.asarray(positions, dtype=object), index=np.asarray(ids, dtype=object))
        p = p.fillna(given.groupby(level=0).first().reindex(out.index))
    for col, src in (("base_std", "_fp"), ("base_xfp", "_xfp"), ("base_last3", "_fp")):
        out[col] = out[col].fillna(p.map(med[src])).fillna(0.0)
    return out


def dst_baselines(data: NFLData, season: int, week: int, teams: Iterable[str], table_id: str) -> pd.DataFrame:
    tg = nv.before(data.team_games, season, week, min_season=season - 1)
    tg = tg[tg["played"]]
    col = f"dst_{table_id}"
    cur = tg[tg["season"] == season].groupby("team")[col].mean()
    prev = tg[tg["season"] == season - 1].groupby("team")[col].mean()
    l3 = tg.sort_values(["season", "week"]).groupby("team").tail(3).groupby("team")[col].mean()
    idx = pd.Index(list(teams), name="team")
    out = pd.DataFrame(index=idx)
    out["base_std"] = cur.reindex(idx).fillna(prev.reindex(idx))
    out["base_last3"] = l3.reindex(idx)
    out["base_xfp"] = np.nan
    return out


# --------------------------------------------------------------------------- one week


def evaluate_week(data: NFLData, season: int, week: int, params: NFLParams | None = None,
                  tables: Sequence[str] = TABLES, n_sims: int = 4000, seed: int = 7,
                  with_baselines: bool = True, kickers: bool = True) -> pd.DataFrame:
    """Project (season, week) from prior data and join actuals + naive baselines (long format)."""
    add_actual_points(data, tables)
    tg = data.team_games
    ctx = build_context(data, season, week, params)
    proj = project_context(ctx, tables, n_sims=n_sims, seed=seed, include_kickers=kickers)
    pg = data.player_games
    act = pg[(pg["season"] == season) & (pg["week"] == week)]
    out = []
    for t in tables:
        f = proj[t]
        players = f[f["kind"] == "player"].drop_duplicates("key").set_index("key")
        # pool A: played with >= 25% offensive snaps
        a = act[act["offense_pct"].fillna(0) >= SNAP_MIN][["gsis_id", "name", "team", "pos", "offense_pct",
                                                           f"fp_{t}"]].rename(columns={f"fp_{t}": "actual"})
        # pool B extra: each team's top players (QB1, RB1-2, WR1-3, TE1) by our unconditional mean and,
        # when baselines are on, by season-to-date average (so injured regulars enter too), played or not
        def top_ids(frame: pd.DataFrame, col: str) -> set[str]:
            f2 = frame.sort_values(col, ascending=False)
            return {k for (_, p), g in f2.groupby(["team", "pos"]) if p in TOP_N for k in g["key"].head(TOP_N[p])}

        cand = players.reset_index()
        b_ids = top_ids(cand, "mean")
        if with_baselines:
            # selection uses real history only (no position-median fill: a player without games is no "top" player)
            cand = cand.join(naive_baselines(data, season, week, cand["key"], t)["base_std"], on="key")
            b_ids |= top_ids(cand, "base_std")
        b_ids -= set(a["gsis_id"])
        allwk = act.set_index("gsis_id")
        b = pd.DataFrame({"gsis_id": sorted(b_ids)})
        b["actual"] = b["gsis_id"].map(allwk[f"fp_{t}"]).fillna(0.0)
        b["offense_pct"] = b["gsis_id"].map(allwk["offense_pct"]).fillna(0.0)
        b["name"] = b["gsis_id"].map(players["name"])
        b["team"] = b["gsis_id"].map(players["team"])
        b["pos"] = b["gsis_id"].map(players["pos"])
        a["pool"] = "A"
        b["pool"] = "B"
        rows = pd.concat([a, b], ignore_index=True)
        rows["pos"] = rows["gsis_id"].map(players["pos"]).fillna(rows["pos"])
        rows["projected"] = rows["gsis_id"].isin(players.index)
        for c in ("played_mean", "mean", "p10", "p50", "p90", "p_play"):
            rows[c] = rows["gsis_id"].map(players[c])
        rows.loc[~rows["projected"], ["played_mean", "mean", "p10", "p50", "p90"]] = 0.0
        rows.loc[~rows["projected"], "p_play"] = 0.0
        if with_baselines:
            rows = rows.join(naive_baselines(data, season, week, rows["gsis_id"], t, rows["pos"]), on="gsis_id")
        # DST
        d = f[f["kind"] == "dst"].set_index("team")
        wk = tg[(tg["season"] == season) & (tg["week"] == week) & tg["played"]].set_index("team")
        dd = pd.DataFrame({"gsis_id": "DST:" + wk.index, "name": wk.index + " DST", "team": wk.index, "pos": "DST",
                           "actual": wk[f"dst_{t}"].to_numpy(), "pool": "A", "offense_pct": np.nan})
        dd["projected"] = dd["team"].isin(d.index)
        for c in ("played_mean", "mean", "p10", "p50", "p90", "p_play"):
            dd[c] = dd["team"].map(d[c])
        if with_baselines:
            dd = dd.join(dst_baselines(data, season, week, dd["team"], t), on="team")
        rows = pd.concat([rows, dd], ignore_index=True)
        if kickers and "kicker" in set(f["kind"]):
            k = f[f["kind"] == "kicker"].drop_duplicates("team").set_index("team")
            ka = kicker_actuals(data, season, week, t)
            kk = pd.DataFrame({"team": ka.index, "gsis_id": "K:" + ka.index, "name": ka.index + " K", "pos": "K",
                               "actual": ka.to_numpy(), "pool": "A", "offense_pct": np.nan})
            kk["projected"] = kk["team"].isin(k.index)
            for c in ("played_mean", "mean", "p10", "p50", "p90", "p_play"):
                kk[c] = kk["team"].map(k[c])
            if with_baselines:
                kk = kk.join(kicker_baselines(data, season, week, kk["team"], t), on="team")
            rows = pd.concat([rows, kk[kk["projected"]]], ignore_index=True)
        rows["table"], rows["season"], rows["week"] = t, season, week
        out.append(rows)
    return pd.concat(out, ignore_index=True)


def run_backtest(season: int, weeks: Iterable[int], params: NFLParams | None = None,
                 tables: Sequence[str] = TABLES, n_sims: int = 4000, seed: int = 7, data: NFLData | None = None,
                 verbose: bool = True) -> pd.DataFrame:
    data = data or NFLData.load(range(season - 7, season + 1))
    frames = []
    for w in weeks:
        t0 = time.time()
        frames.append(evaluate_week(data, season, w, params, tables, n_sims, seed))
        if verbose:
            print(f"  {season} wk{w:02d}: {time.time() - t0:.1f}s", file=sys.stderr)
    return pd.concat(frames, ignore_index=True)


# --------------------------------------------------------------------------- metrics


def pinball(y: np.ndarray, q: np.ndarray, tau: float) -> np.ndarray:
    d = y - q
    return np.maximum(tau * d, (tau - 1) * d)


def _topk_hit(df: pd.DataFrame, pred: str, k: int = 3) -> float:
    hits = []
    for _, g in df.groupby(["season", "week"]):
        if len(g) < k:
            continue
        a = set(g.nlargest(k, "actual").index)
        p = set(g.nlargest(k, pred).index)
        hits.append(len(a & p) / k)
    return float(np.mean(hits)) if hits else np.nan


def _spearman(df: pd.DataFrame, pred: str) -> float:
    vals = []
    for _, g in df.groupby(["season", "week"]):
        if len(g) >= 5 and g[pred].nunique() > 1:
            vals.append(spearmanr(g[pred], g["actual"]).statistic)
    return float(np.mean(vals)) if vals else np.nan


def metrics(df: pd.DataFrame, pred: str, with_quantiles: bool = False) -> dict[str, float]:
    y, m = df["actual"].to_numpy(float), df[pred].to_numpy(float)
    e = m - y
    out = {"n": len(df), "MAE": np.mean(np.abs(e)), "RMSE": np.sqrt(np.mean(e ** 2)), "bias": np.mean(e),
           "spearman": _spearman(df, pred), "top3": _topk_hit(df, pred)}
    if with_quantiles:
        out["cover80"] = np.mean((y >= df["p10"]) & (y <= df["p90"]))
        out["pinball"] = np.mean([pinball(y, df[c].to_numpy(float), tau).mean()
                                  for c, tau in (("p10", 0.1), ("p50", 0.5), ("p90", 0.9))])
    return out


MODELS = {"model": "played_mean", "season_avg": "base_std", "last3": "base_last3", "xfp_avg": "base_xfp"}


def summary_table(res: pd.DataFrame, table_id: str, pool: str = "A") -> pd.DataFrame:
    """Per position x model metrics for one scoring table and pool."""
    r = res[(res["table"] == table_id)]
    r = r[r["pool"] == "A"] if pool == "A" else r
    rows = []
    for pos in list(POSITIONS) + ["ALL"]:
        g = r if pos == "ALL" else r[r["pos"] == pos]
        if pos == "ALL":
            g = g[~g["pos"].isin(["DST", "K"])]
        if g.empty:
            continue
        for name, col in MODELS.items():
            if pool == "B" and name == "model":
                col = "mean"
            if col not in g.columns or g[col].isna().all():
                continue
            gg = g.dropna(subset=[col])
            mm = metrics(gg, col, with_quantiles=(name == "model" and pool == "A"))
            rows.append({"pos": pos, "model": name, **mm})
    return pd.DataFrame(rows)


def fmt_table(t: pd.DataFrame) -> str:
    cols = ["pos", "model", "n", "MAE", "RMSE", "bias", "spearman", "top3", "cover80", "pinball"]
    t = t.reindex(columns=cols)
    head = "| " + " | ".join(cols) + " |\n|" + "---|" * len(cols) + "\n"
    lines = []
    for _, r in t.iterrows():
        cells = []
        for c in cols:
            v = r[c]
            if c in ("pos", "model"):
                cells.append(str(v))
            elif c == "n":
                cells.append(str(int(v)))
            elif pd.isna(v):
                cells.append("")
            elif c in ("spearman", "top3", "cover80"):
                cells.append(f"{v:.3f}")
            else:
                cells.append(f"{v:.2f}")
        lines.append("| " + " | ".join(cells) + " |")
    return head + "\n".join(lines) + "\n"


def tuning_objective(res: pd.DataFrame, table_id: str = "draftkings.nfl") -> float:
    """Mean MAE across QB/RB/WR/TE/DST on pool A (equal weight per position)."""
    r = res[(res["table"] == table_id) & (res["pool"] == "A") & (res["pos"] != "K")]
    return float(np.mean([np.mean(np.abs(g["played_mean"] - g["actual"])) for _, g in r.groupby("pos")]))


def bootstrap_mae_diff(res: pd.DataFrame, a: str, b: str, n_boot: int = 4000, seed: int = 0) -> tuple[float, float, float]:
    """MAE(a) - MAE(b) with a 95% interval from resampling whole weeks (errors within a week are correlated)."""
    r = res.dropna(subset=[a, b])
    g = r.assign(ea=(r[a] - r["actual"]).abs(), eb=(r[b] - r["actual"]).abs()).groupby(["season", "week"])
    sums = g[["ea", "eb"]].sum()
    n = g.size().to_numpy()
    ea, eb = sums["ea"].to_numpy(), sums["eb"].to_numpy()
    point = ea.sum() / n.sum() - eb.sum() / n.sum()
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(n), (n_boot, len(n)))
    diffs = (ea[idx].sum(1) - eb[idx].sum(1)) / n[idx].sum(1)
    lo, hi = np.quantile(diffs, [0.025, 0.975])
    return float(point), float(lo), float(hi)


def availability_metrics(data: NFLData, season: int, weeks: Iterable[int], params: NFLParams | None = None) -> pd.DataFrame:
    """Pre-game P(logs an offensive snap) for non-QBs and P(starts) for QBs over every rostered pool
    player (rows already fixed at 0 by status or an Out designation are left out): n, mean forecast,
    observed rate, Brier score and log loss. A start = at least half the team's pass attempts."""
    rows = []
    pg = data.player_games
    for w in weeks:
        m = player_means(build_context(data, season, w, params))
        act = pg[(pg["season"] == season) & (pg["week"] == w)]
        played = set(act.loc[act["offense_pct"].fillna(1.0) > 0, "gsis_id"])
        started = set(act.loc[(act["pos"] == "QB") & (act["attempts"] >= 0.5 * act["team_attempts"].clip(lower=1)),
                              "gsis_id"])
        is_qb = (m["pos"] == "QB").to_numpy()
        y = np.where(is_qb, m["gsis_id"].isin(started), m["gsis_id"].isin(played)).astype(float)
        rows.append(pd.DataFrame({"group": np.where(is_qb, "QB P(starts)", "RB/WR/TE P(plays)"), "p": m["p_play"],
                                  "y": y, "week": w}))
    r = pd.concat(rows, ignore_index=True)
    r = r[r["p"] > 0]
    out = []
    for g, x in r.groupby("group"):
        p = x["p"].clip(1e-4, 1 - 1e-4)
        out.append({"group": g, "n": len(x), "mean_p": x["p"].mean(), "observed": x["y"].mean(),
                    "brier": float(np.mean((x["p"] - x["y"]) ** 2)),
                    "log_loss": float(-np.mean(x["y"] * np.log(p) + (1 - x["y"]) * np.log(1 - p)))})
    return pd.DataFrame(out)


def calibration_table(res: pd.DataFrame, table_id: str) -> pd.DataFrame:
    r = res[(res["table"] == table_id) & (res["pool"] == "A")]
    rows = []
    for pos, g in r.groupby("pos"):
        slope, icpt = np.polyfit(g["played_mean"], g["actual"], 1)
        rows.append({"pos": pos, "n": len(g), "below_p10": np.mean(g["actual"] < g["p10"]),
                     "below_p50": np.mean(g["actual"] < g["p50"]), "above_p90": np.mean(g["actual"] > g["p90"]),
                     "slope": slope, "intercept": icpt})
    return pd.DataFrame(rows)


def _md(df: pd.DataFrame, fmt: dict[str, str]) -> str:
    cols = list(df.columns)
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        out.append("| " + " | ".join(format(r[c], fmt.get(c, "")) if not isinstance(r[c], str) else r[c]
                                     for c in cols) + " |")
    return "\n".join(out) + "\n"


def write_report(holdout: pd.DataFrame, params: NFLParams, tuning: pd.DataFrame | None = None,
                 tune_log: list[dict] | None = None, inactive: pd.DataFrame | None = None,
                 path: Path = REPORT_PATH, notes: Sequence[str] = (), availability: pd.DataFrame | None = None,
                 changes: Sequence[str] = ()) -> str:
    """Markdown report of measured results (every number computed from the frames passed in)."""
    hs = sorted(set(zip(holdout["season"], holdout["week"])))
    label = f"{hs[0][0]} weeks {hs[0][1]}-{hs[-1][1]}" if len({s for s, _ in hs}) == 1 else str(hs)
    span = label  # ``label`` is reused by the QB split loop below
    L = [f"# NFL baseline backtest: {label}", "",
         f"Generated by `dfs.backtest.write_report` on {pd.Timestamp.now(tz='UTC'):%Y-%m-%d %H:%M} UTC. "
         "Every number below was computed from real nflverse games by the code in this repo.", ""]
    head = []
    for t in TABLES:
        r = holdout[(holdout["table"] == t) & (holdout["pool"] == "A")]
        if r.empty:
            continue
        allp = r[~r["pos"].isin(["DST", "K"])]
        bases = {b: float(np.mean(np.abs(allp[b] - allp["actual"]))) for b in ("base_std", "base_last3", "base_xfp")
                 if b in allp.columns and allp[b].notna().all()}
        if not bases:
            continue
        best = min(bases, key=bases.get)
        d, lo, hi = bootstrap_mae_diff(allp, "played_mean", best)
        wins = 0
        poss = [p for p in POSITIONS if p in set(r["pos"])]
        for pos in poss:
            g = r[r["pos"] == pos]
            bm = min(float(np.mean(np.abs(g[b] - g["actual"]))) for b in ("base_std", "base_last3", "base_xfp")
                     if b in g.columns and g[b].notna().all())
            wins += float(np.mean(np.abs(g["played_mean"] - g["actual"]))) < bm
        model_mae = float(np.mean(np.abs(allp["played_mean"] - allp["actual"])))
        head.append(f"- **{t}**: QB/RB/WR/TE MAE {model_mae:.2f} vs {bases[best]:.2f} for the best naive "
                    f"baseline ({best.replace('base_', '')}): {d:+.2f} points, 95% week-bootstrap interval "
                    f"[{lo:+.2f}, {hi:+.2f}]; the model has the lowest MAE at {wins} of {len(poss)} positions "
                    f"({', '.join(poss)}). n = {len(allp)} player-games.")
    if head:
        L += ["## Headline (pool A)", ""] + head + [""]
    L += ["## Protocol", "",
          "- Each target week is projected from data strictly before that week (`dfs.data.nflverse.before`): "
          "schedules with closing spread/total, the week's final injury report and weekly roster status "
          "(game-day `INA` inactives are NOT used unless stated), the last depth-chart snapshot before game day, "
          "and box scores / snap counts / ff_opportunity from earlier weeks only.",
          "- Actual fantasy points: nflverse weekly box scores scored by `dfs.scoring.score` with the repo's "
          "`draftkings.nfl` / `fanduel.nfl` tables. Fumbles lost = nflverse `fumbles_lost_total` (adds fumbles "
          "lost on kick/punt returns, which both sites charge). DST: sacks = the opponent's official sacks taken, "
          "defensive TDs include fumble-recovery TDs, blocked kicks include PATs; points allowed DK = opponent "
          "points minus 6 per opponent defensive TD (its XP still counts) and 2 per safety, FD = the site "
          "formula 6 x (offensive + own-fumble-recovery TDs) + 2pt + XP + 3 x FG. Checked against the season "
          "averages printed in the real 2026 week-6 DK and FD salary files (weeks 1-5): DST 20/20 teams on "
          "each site (DK rounds half up); offense, 257 players matched by name and team: FD season totals "
          "(FPPG x Played) agree for 256 (the other shows last season's line), DK averages for all 257 once "
          "DK's games count is used (10 players have extra special-teams-only games).",
          f"- **Pool A** (primary): every QB/RB/WR/TE player-game with >= {SNAP_MIN:.0%} offensive snap share "
          "in the target week, plus every DST. Same rows for every model. The model's point forecast is its "
          "mean *conditional on playing* (QB: on starting), comparable with 'average when played' baselines.",
          "- **Pool B**: pool A plus each team's top players (QB1, RB1-2, WR1-3, TE1) by the model's "
          "unconditional mean or by season-to-date average, who did not reach the snap threshold or did "
          "not play (actual = their real points, often 0; injured regulars therefore enter). The model is "
          "scored on its *unconditional* mean (availability included); baselines are unchanged.",
          "- Baselines (point-in-time): `season_avg` = season-to-date average points per game played; `last3` "
          "= last 3 games played; `xfp_avg` = season-to-date average of ff_opportunity expected fantasy points "
          "(PPR; minus 0.5 x expected receptions for FD). Players with no game this season fall back to last "
          "season, then to the position median.",
          "- Metrics: MAE, RMSE, bias = mean(pred - actual), spearman = mean within-week rank correlation, "
          "top3 = share of each week's actual top-3 at the position that were in the projected top-3, cover80 = "
          "share of actuals inside [p10, p90] (target 0.80), pinball = mean quantile loss over p10/p50/p90.",
          "- Hyper-parameters were tuned on 2024-2025 weeks 3-17 only (DK, pool A, mean of per-position MAE, "
          "then pinball loss for dispersion). No parameter was chosen from 2026 results. Disclosure: 2026 weeks "
          "3-6 were also run during development with the untuned defaults and one interim parameter set (as a "
          "smoke test), and 2026 QB-starter hit rates were visible when the depth-chart-first starter rule was "
          "adopted on 2025 evidence (95.4% vs 92.9% on 2025 games).", ""]
    for t in TABLES:
        if t not in set(holdout["table"]):
            continue
        L += [f"## {t}: pool A", "", fmt_table(summary_table(holdout, t, "A"))]
        rows = []
        r = holdout[(holdout["table"] == t) & (holdout["pool"] == "A")]
        for pos in ["ALL", *POSITIONS]:
            g = r if pos == "ALL" else r[r["pos"] == pos]
            if pos == "ALL":
                g = g[~g["pos"].isin(["DST", "K"])]
            for b in ("base_xfp", "base_std", "base_last3"):
                if b in g.columns and g[b].notna().any():
                    d, lo, hi = bootstrap_mae_diff(g, "played_mean", b)
                    rows.append({"pos": pos, "vs": b.replace("base_", ""), "MAE diff": d, "95% lo": lo, "95% hi": hi})
        if rows:
            L += [f"Model MAE minus baseline MAE ({t}, pool A; negative = model better; week-clustered bootstrap):", "",
                  _md(pd.DataFrame(rows), {"MAE diff": ".3f", "95% lo": ".3f", "95% hi": ".3f"})]
        q = holdout[(holdout["table"] == t) & (holdout["pool"] == "A") & (holdout["pos"] == "QB")]
        if len(q):
            qrows = []
            for label, g in (("projected starter (start prob >= 0.5)", q[q["p_play"] >= 0.5]),
                             ("not projected to start", q[q["p_play"] < 0.5])):
                for name, col in MODELS.items():
                    if col in g.columns and len(g) and g[col].notna().any():
                        qrows.append({"QB group": label, "model": name, "n": len(g),
                                      "MAE": float(np.mean(np.abs(g[col] - g["actual"])))})
            L += [f"QB pool A split by the pre-game start probability ({t}). The model's QB distribution is "
                  "conditional on starting, so relief appearances and surprise starts land in the second group:", "",
                  _md(pd.DataFrame(qrows), {"n": "d", "MAE": ".2f"})]
        L += [f"## {t}: pool B (availability included; quantile metrics omitted because the quantiles "
              "describe the played outcome)", "", fmt_table(summary_table(holdout, t, "B"))]
        L += [f"Calibration ({t}, pool A): share of actuals below p10 / p50 and above p90 (targets 0.10 / 0.50 / "
              "0.10) and the OLS slope of actual on projected mean (1 = well scaled).", "",
              _md(calibration_table(holdout, t), {"n": "d", "below_p10": ".3f", "below_p50": ".3f",
                                                   "above_p90": ".3f", "slope": ".3f", "intercept": ".2f"})]
    if availability is not None and len(availability):
        L += [f"## Availability ({span}, every rostered QB/RB/WR/TE)", "",
              "Pre-game P(logs an offensive snap) for RB/WR/TE and P(starts) for QBs, scored against what "
              "happened (rows fixed at 0 by roster status or an Out designation are excluded):", "",
              _md(availability, {"n": "d", "mean_p": ".3f", "observed": ".3f", "brier": ".4f", "log_loss": ".4f"})]
    if inactive is not None:
        L += ["## Post-inactives variant (game-day INA status known)", "",
              "Same weeks with `NFLParams(use_inactives=True)`; only availability changes, so pool B is the "
              "relevant comparison.", "", fmt_table(summary_table(inactive, TABLES[0], "B"))]
    if tuning is not None:
        ts = sorted(set(tuning["season"]))
        L += [f"## Tuning set ({', '.join(map(str, ts))}, weeks {tuning['week'].min()}-{tuning['week'].max()}), "
              "final parameters, DK pool A", "", fmt_table(summary_table(tuning, TABLES[0], "A"))]
    L += ["## Parameters", "", "```", json.dumps({k: (dict(v) if isinstance(v, dict) or hasattr(v, 'items') else v)
                                                  for k, v in asdict(params).items()}, indent=1), "```", ""]
    if tune_log:
        L += ["Tuning (coordinate descent, one parameter at a time, 2024-2025 weeks 3-17, DK pool A, 1000 sims "
              "with common random numbers). Accepted steps per pass; every evaluated setting is in "
              f"`{TUNE_LOG_PATH.name}`:", ""]
        for ps in tune_log:
            hist = ps["history"]
            L.append(f"- **{ps['name']}** (objective: {ps['objective']}; {len(hist)} settings evaluated)")
            best = hist[0]["objective"]
            L.append(f"  - start {best:.4f}")
            for h in hist[1:]:
                if h["objective"] < best - 1e-4:
                    best = h["objective"]
                    L.append(f"  - {best:.4f} with {json.dumps(h['params'])}")
        L += [""]
    if changes:
        L += ["## Changes after review", "",
              "Figures in this list describe the pre-fix code and were measured during the review; they are not "
              "recomputed by this report.", ""] + [f"- {c}" for c in changes] + [""]
    if notes:
        L += ["## Notes and known weaknesses", ""] + [f"- {n}" for n in notes] + [""]
    text = "\n".join(L)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return text


def pinball_objective(res: pd.DataFrame, table_id: str = "draftkings.nfl") -> float:
    """Mean pinball loss (p10/p50/p90) across positions, pool A: the dispersion tuning target."""
    r = res[(res["table"] == table_id) & (res["pool"] == "A") & (res["pos"] != "K")]
    vals = []
    for _, g in r.groupby("pos"):
        y = g["actual"].to_numpy(float)
        vals.append(np.mean([pinball(y, g[c].to_numpy(float), t).mean()
                             for c, t in (("p10", 0.1), ("p50", 0.5), ("p90", 0.9))]))
    return float(np.mean(vals))


# --------------------------------------------------------------------------- tuning

_TUNE_DATA: NFLData | None = None


def params_from(overrides: dict) -> NFLParams:
    """NFLParams with overrides; dotted keys set one entry of a dict field (``usage_cv.QB``)."""
    base = NFLParams()
    flat: dict = {}
    for k, v in overrides.items():
        if "." in k:
            field, key = k.split(".", 1)
            d = dict(flat.get(field, getattr(base, field)))
            d[key] = v
            flat[field] = d
        else:
            flat[k] = v
    return base.with_(**flat)


def _param_value(overrides: dict, name: str):
    if name in overrides:
        return overrides[name]
    if "." in name:
        field, key = name.split(".", 1)
        return dict(getattr(NFLParams(), field)).get(key)
    return getattr(NFLParams(), name)


def _tune_week(args: tuple) -> pd.DataFrame:
    season, week, overrides, n_sims = args
    params = params_from(overrides)
    return evaluate_week(_TUNE_DATA, season, week, params, tables=("draftkings.nfl",), n_sims=n_sims,
                         with_baselines=False, kickers=False)


def evaluate_many(data: NFLData, weeks: Sequence[tuple[int, int]], overrides: dict, n_sims: int = 1000,
                  processes: int = 4) -> pd.DataFrame:
    """Evaluate one parameter setting on many (season, week)s in parallel (fork shares ``data``)."""
    import multiprocessing as mp

    global _TUNE_DATA
    _TUNE_DATA = data
    jobs = [(s, w, overrides, n_sims) for s, w in weeks]
    if processes <= 1:
        return pd.concat([_tune_week(j) for j in jobs], ignore_index=True)
    with mp.get_context("fork").Pool(processes) as pool:
        return pd.concat(pool.map(_tune_week, jobs), ignore_index=True)


def warm_cache(data: NFLData, weeks: Sequence[tuple[int, int]], params: NFLParams | None = None) -> None:
    """Build the parameter-free per-week pieces once, before forking workers."""
    add_actual_points(data)
    for s, w in weeks:
        build_context(data, s, w, params)


def coordinate_descent(data: NFLData, weeks: Sequence[tuple[int, int]], grid: dict[str, list], start: dict | None = None,
                       objective=None, n_sims: int = 1000, passes: int = 1, log=print) -> tuple[dict, list[dict]]:
    """Greedy one-parameter-at-a-time search minimising ``objective(results)`` (default: pool-A MAE)."""
    objective = objective or tuning_objective
    best = dict(start or {})
    best_val = objective(evaluate_many(data, weeks, best, n_sims))
    history = [{"params": dict(best), "objective": best_val}]
    log(f"start {best_val:.4f} {best}")
    for _ in range(passes):
        for name, values in grid.items():
            for v in values:
                if _param_value(best, name) == v:
                    continue
                trial = {**best, name: v}
                val = objective(evaluate_many(data, weeks, trial, n_sims))
                history.append({"params": trial, "objective": val})
                log(f"  {name}={v}: {val:.4f}" + ("  *" if val < best_val - 1e-4 else ""))
                if val < best_val - 1e-4:
                    best, best_val = trial, val
    return best, history


# --------------------------------------------------------------------------- CLI


def _weeks(s: str) -> list[int]:
    if "-" in s:
        a, b = s.split("-")
        return list(range(int(a), int(b) + 1))
    return [int(x) for x in s.split(",")]


KNOWN_WEAKNESSES = [
    "No player props or opponent-specific player adjustments: the market enters only through team "
    "spread/total; props would be the strongest single extra input.",
    "Usage is share-based without routes or alignment data; role changes inside a week (new starter "
    "announced, trade) reach the model only via the depth chart snapshot and the injury report.",
    "QB starter identification picks the actual starter ~93-95% of the time from depth chart + last "
    "start + availability; a wrong pick moves the QB and his receivers. The LLM research stage must "
    "confirm starters.",
    "Pool A conditions on >= 25% snaps, which biases every model's error low for players who barely "
    "played and makes low projections look under-projected (selection effect on the snap threshold).",
    "Week 17-18 rest decisions, weather, and in-game injury risk beyond a flat per-position early-exit "
    "rate are not modelled.",
    "DST points allowed conventions were inferred from 2026 site averages (20/20 teams reproduced on DK "
    "and FD); DK's rule (opponent points minus 6 per opponent defensive TD, the XP still counts) "
    "differs from the research note and should be re-checked as more games accumulate.",
    "Stat-line simulation draws players independently; cross-player correlation is left to dfs.sim.",
    "Quantiles in pool A: fewer actuals fall below p10 than the 10% target (lower tail too long) and more "
    "exceed p90. Part of this is selection (pool A excludes the early-exit and low-snap games the played "
    "distribution includes); the pinball-tuned dispersion was chosen on 2024-2025 with that same pool.",
    "Backup QBs are projected as if they start (p_zero = 1 - start probability); relief appearances are not "
    "modelled, so their played distribution is too high when they only enter late.",
    "When a backup QB starts, team passing volume and efficiency still come from the team's history with its "
    "usual starter; receivers and the opposing DST are not adjusted for the QB change.",
    "The below-p10 rate understates lower-tail coverage for low-usage players whose played distribution has "
    "an atom at 0 (p10 = 0, so an actual 0 is not 'below' it); cover80 counts those as covered.",
]

REVIEW_CHANGES = [
    "Fumbles lost now use nflverse `fumbles_lost_total`, which includes fumbles lost on kick/punt returns; "
    "before, returners' DK/FD season averages were off by 1 (DK) / 2 (FD) points per return fumble.",
    "QB start probabilities: the start chain used P(logs an offensive snap), which for a backup is ~0.15-0.4 "
    "even when he is the only healthy QB, so when the starter was out the team's start probabilities summed "
    "to ~0.3-0.6 (6% of 2024-25 team-weeks below 0.9). It now uses P(dressed: game-day ACT) and is "
    "conditioned on exactly one QB starting.",
    "Availability adds the pre-game depth-chart bucket (starter / backup / other): healthy depth-chart "
    "starters without recent snaps (season openers after a late-season injury, returns from IR) were scored "
    "as deep backups (P(plays) ~0.5 against an observed ~0.9).",
    "Naive baselines: the documented position-median fallback never applied (the position came from the "
    "same empty history), so players without a game in two seasons got 0 (e.g. two starting QBs in 2026 "
    "weeks 3-4); they now get the position median.",
    "The week context keeps only pre-game schedule columns (target-week box-score columns were carried in "
    "`WeekContext.games`, unused but one step from a leak); duplicate injury rows keep the latest report.",
]


def main(argv: Sequence[str] | None = None, prog: str | None = None) -> int:
    ap = argparse.ArgumentParser(prog=prog, description=__doc__.splitlines()[0])
    ap.add_argument("--season", type=int, default=nv.current_season())
    ap.add_argument("--weeks", default="3-6")
    ap.add_argument("--sims", type=int, default=4000)
    ap.add_argument("--params", help="JSON file of NFLParams overrides")
    ap.add_argument("--out", help="write per-player results to this parquet/csv")
    ap.add_argument("--report", action="store_true", help=f"write the markdown report to {REPORT_PATH}")
    ap.add_argument("--tune-seasons", default="", help="also evaluate these seasons (e.g. 2024,2025) in the report")
    ap.add_argument("--tune-weeks", default="3-17")
    ap.add_argument("--tune-log", help="tuning history JSON ({'passes': [{name, objective, history}]})")
    args = ap.parse_args(argv)
    params = params_from(json.loads(Path(args.params).read_text())) if args.params else NFLParams()
    tune_seasons = [int(x) for x in args.tune_seasons.split(",") if x]
    first = min([args.season, *tune_seasons]) - 7
    data = NFLData.load(range(first, args.season + 1))
    res = run_backtest(args.season, _weeks(args.weeks), params, n_sims=args.sims, data=data)
    for t in TABLES:
        print(f"\n## {t} {args.season} weeks {args.weeks} (pool A)\n")
        print(fmt_table(summary_table(res, t)))
    if args.out:
        (res.to_parquet if args.out.endswith(".parquet") else res.to_csv)(args.out)
    if args.report:
        inactive = run_backtest(args.season, _weeks(args.weeks), params.with_(use_inactives=True), n_sims=args.sims,
                                data=data, tables=TABLES[:1])
        tuning = None
        if tune_seasons:
            tuning = pd.concat([run_backtest(s, _weeks(args.tune_weeks), params, n_sims=args.sims, data=data,
                                             tables=TABLES[:1]) for s in tune_seasons], ignore_index=True)
        log = json.loads(Path(args.tune_log).read_text())["passes"] if args.tune_log else None
        avail = availability_metrics(data, args.season, _weeks(args.weeks), params)
        write_report(res, params, tuning=tuning, tune_log=log, inactive=inactive, notes=KNOWN_WEAKNESSES,
                     availability=avail, changes=REVIEW_CHANGES)
        print(f"wrote {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
