"""Walk-forward backtest of the NFL baseline against naive baselines on real nflverse games.

For each (season, week) the model is projected from data strictly before that week; actual
fantasy points come from nflverse box scores scored by ``dfs.scoring`` (DK and FD tables).

Evaluation pool (same rows for every model): QB/RB/WR/TE player-games in the target week
with >= 25% offensive snap share, plus every DST that played. Model point forecast = mean
conditional on playing (QBs: on starting), the quantity comparable with "average when
played" baselines. A second pool adds each team's top projected players (QB1, RB1-2, WR1-3,
TE1) whether or not they played, scored against the unconditional mean, to measure the cost
of availability misses.

CLI: ``python -m dfs.backtest --season 2026 --weeks 3-6 [--tune-seasons 2024,2025] [--report]``
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
from dfs.projections.nfl import NFLData, NFLParams, build_context, offense_stat_line, project_context
from dfs.scoring import score

TABLES = ("draftkings.nfl", "fanduel.nfl")
POSITIONS = ("QB", "RB", "WR", "TE", "DST")
REPORT_PATH = nv.REPO_ROOT / "docs" / "results" / "nfl_baseline_backtest.md"
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
    tg = _dst_frame(data)
    rows = tg.to_dict("records")
    for t in tables:
        tg[f"dst_{t}"] = [score(dst_stat_line(r, t), t, role="dst") if r["played"] else np.nan for r in rows]


def dst_stat_line(row: pd.Series, table_id: str) -> dict:
    """One team-game row -> dfs.scoring DST stats. Points allowed follow the site's convention:
    DK = opponent points minus the opponent's defensive TDs (+XP) and safeties; FD = the formula
    6 * (offensive TDs + own-fumble-recovery TDs) + 2pt + XP + 3 * FG of the opponent offense."""
    v = lambda k: float(np.nan_to_num(row.get(k, 0.0) if hasattr(row, "get") else row[k]))
    if table_id.startswith("fanduel") and pd.notna(row.get("_opp_offense")):
        pa = v("_opp_offense")
    else:
        pa = v("opp_points") - 7 * v("opp_def_tds") - 2 * v("opp_def_safeties")
    return {"sack": v("def_sacks_made"), "interception": v("def_int_made"), "fumble_recovery": v("def_fr_made"),
            "def_td": v("def_tds"), "return_td": v("special_teams_tds"), "safety": v("def_safeties"),
            "blocked_kick": v("blocks"), "two_pt_return": v("def_2pt_made"), "points_allowed": max(pa, 0.0)}


def _dst_frame(data: NFLData) -> pd.DataFrame:
    """Team-game rows with the defensive stats a DST scores (from nflverse team stats)."""
    tg = data.team_games
    if "def_sacks_made" in tg.columns:
        return tg
    ts = nv.load("team_stats", data.seasons)
    ts["team"] = ts["team"].map(lambda t: nv.canonicalize(t, strict=False))
    d = ts[["season", "week", "team", "def_sacks", "def_interceptions", "fumble_recovery_opp"]].rename(
        columns={"def_sacks": "def_sacks_made", "def_interceptions": "def_int_made", "fumble_recovery_opp": "def_fr_made"})
    off = ts[["season", "week", "team", "rushing_tds", "receiving_tds", "fumble_recovery_tds", "passing_2pt_conversions",
              "rushing_2pt_conversions", "pat_made", "fg_made"]].copy()
    off["_opp_offense"] = 6 * (off["rushing_tds"] + off["receiving_tds"] + off["fumble_recovery_tds"]) + \
        2 * (off["passing_2pt_conversions"] + off["rushing_2pt_conversions"]) + off["pat_made"] + 3 * off["fg_made"]
    off = off[["season", "week", "team", "_opp_offense"]].rename(columns={"team": "opponent"})
    tg = tg.merge(d, on=["season", "week", "team"], how="left").merge(off, on=["season", "week", "opponent"], how="left")
    data.team_games = tg
    return tg


# --------------------------------------------------------------------------- naive baselines


def naive_baselines(data: NFLData, season: int, week: int, ids: pd.Series, table_id: str) -> pd.DataFrame:
    """Season-to-date average, last-3-games average and ff_opportunity expected-points average,
    from games strictly before (season, week). Players without a game this season fall back to
    last season's average, then to the position's median season-to-date average."""
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
                  tables: Sequence[str] = TABLES, n_sims: int = 4000, seed: int = 7) -> pd.DataFrame:
    """Project (season, week) from prior data and join actuals + naive baselines (long format)."""
    add_actual_points(data, tables)
    tg = data.team_games
    ctx = build_context(data, season, week, params)
    proj = project_context(ctx, tables, n_sims=n_sims, seed=seed)
    pg = data.player_games
    act = pg[(pg["season"] == season) & (pg["week"] == week)]
    out = []
    for t in tables:
        f = proj[t]
        players = f[f["kind"] == "player"].drop_duplicates("key").set_index("key")
        # pool A: played with >= 25% offensive snaps
        a = act[act["offense_pct"].fillna(0) >= SNAP_MIN][["gsis_id", "name", "team", "pos", "offense_pct",
                                                           f"fp_{t}"]].rename(columns={f"fp_{t}": "actual"})
        # pool B extra: each team's top projected players (QB1, RB1-2, WR1-3, TE1) whether or not they played
        top = players.reset_index().sort_values("mean", ascending=False)
        top = pd.concat([g.head(TOP_N[p]) for (tm, p), g in top.groupby(["team", "pos"]) if p in TOP_N])
        b_ids = set(top["key"]) - set(a["gsis_id"])
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
        base = naive_baselines(data, season, week, rows["gsis_id"], t)
        rows = rows.join(base, on="gsis_id")
        # DST
        d = f[f["kind"] == "dst"].set_index("team")
        wk = tg[(tg["season"] == season) & (tg["week"] == week) & tg["played"]].set_index("team")
        dd = pd.DataFrame({"gsis_id": "DST:" + wk.index, "name": wk.index + " DST", "team": wk.index, "pos": "DST",
                           "actual": wk[f"dst_{t}"].to_numpy(), "pool": "A", "offense_pct": np.nan})
        dd["projected"] = dd["team"].isin(d.index)
        for c in ("played_mean", "mean", "p10", "p50", "p90", "p_play"):
            dd[c] = dd["team"].map(d[c])
        dd = dd.join(dst_baselines(data, season, week, dd["team"], t), on="team")
        rows = pd.concat([rows, dd], ignore_index=True)
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
            g = g[g["pos"] != "DST"]
        if g.empty:
            continue
        for name, col in MODELS.items():
            if pool == "B" and name == "model":
                col = "mean"
            if g[col].isna().all():
                continue
            gg = g.dropna(subset=[col])
            mm = metrics(gg, col, with_quantiles=(name == "model"))
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
    r = res[(res["table"] == table_id) & (res["pool"] == "A")]
    return float(np.mean([np.mean(np.abs(g["played_mean"] - g["actual"])) for _, g in r.groupby("pos")]))


# --------------------------------------------------------------------------- CLI


def _weeks(s: str) -> list[int]:
    if "-" in s:
        a, b = s.split("-")
        return list(range(int(a), int(b) + 1))
    return [int(x) for x in s.split(",")]


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--season", type=int, default=nv.current_season())
    ap.add_argument("--weeks", default="3-6")
    ap.add_argument("--sims", type=int, default=4000)
    ap.add_argument("--params", help="JSON file of NFLParams overrides")
    ap.add_argument("--out", help="write per-player results to this parquet/csv")
    args = ap.parse_args(argv)
    params = NFLParams()
    if args.params:
        params = params.with_(**json.loads(Path(args.params).read_text()))
    res = run_backtest(args.season, _weeks(args.weeks), params, n_sims=args.sims)
    for t in TABLES:
        print(f"\n## {t} {args.season} weeks {args.weeks} (pool A)\n")
        print(fmt_table(summary_table(res, t)))
    if args.out:
        (res.to_parquet if args.out.endswith(".parquet") else res.to_csv)(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
