"""Experiment "minutes2": extra point-in-time minutes features, written as one function that adds columns to the
nba_minutes.build_features table so it can be ported into dfs/projections/nba_minutes.py.

    add_features(feats, box, fouls) -> feats with NEW2 columns

All features use box rows of games that tipped strictly before the night's first tip (cutoff from the box, as in
build_features). ``fouls``: raw player_box rows (game_id, athlete_id, fouls, team_score, opponent_team_score) read
straight from the cached sportsdataverse parquet (load_box drops these columns).

Features (NEW2):
- volatility / trend: own_min10 (mean minutes last 10 played this season), own_min_sd10 (sd), own_min_cur (season
  mean), min_trend3 (last 3 minus season mean), own_min_max5 / own_min_min5;
- return ramp: ret_n (games played since his last absence of >= 3 straight team games this season; 99 if none),
  ret_len (length of that absence), ret_ratio (mean minutes since return / mean of last 5 played before it);
- fatigue: tm_g4, tm_g6 (team games in the last 4 / 6 days), own_load7 (his minutes in the last 7 days);
- fouls: pf36_10, pf36_cur (personal fouls per 36 minutes, last 10 / season);
- team rotation: tm_st_share10 (starters' share of team minutes, last 10 team games), tm_top7_10 (top-7 share),
  tm_rot_sd (sd of the team's per-game minutes allocation for the top-8 by minutes; lower = tighter);
- blowout: exp_margin (season net rating gap + 2.5 home court - 1.0 if b2b + 1.0 if opp b2b), abs_margin,
  blow_x_start (abs_margin x own_start_share), blow_x_min (abs_margin x own_min10 / 36);
- pace: opp_total, tm_total (mean game total points, season to date, shrunk to league 225 with k=5).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from dfs.projections import nba_minutes as nm

NEW2 = ["own_min10", "own_min_sd10", "own_min_cur", "min_trend3", "own_min_max5", "own_min_min5",
        "ret_n", "ret_len", "ret_ratio", "tm_g4", "tm_g6", "own_load7", "pf36_10", "pf36_cur",
        "tm_st_share10", "tm_top7_10", "tm_rot_sd", "exp_margin", "abs_margin", "blow_x_start", "blow_x_min",
        "opp_total", "tm_total"]
DAY = 86400e9


def load_extra(seasons) -> pd.DataFrame:
    from dfs.data import nba
    frames = []
    for s in seasons:
        p = nba.cache_path("player_box", s)
        if p.exists():
            frames.append(pd.read_parquet(p, columns=["game_id", "athlete_id", "fouls", "team_score",
                                                      "opponent_team_score"]))
    x = pd.concat(frames, ignore_index=True)
    x["athlete_id"] = pd.to_numeric(x["athlete_id"], errors="coerce")
    x = x[x["athlete_id"].notna()]
    x["athlete_id"] = x["athlete_id"].astype("int64")
    for c in ("fouls", "team_score", "opponent_team_score"):
        x[c] = pd.to_numeric(x[c], errors="coerce")
    return x.drop_duplicates(["game_id", "athlete_id"])


def add_features(feats: pd.DataFrame, box: pd.DataFrame, extra: pd.DataFrame, cutoffs: dict | None = None) -> pd.DataFrame:
    """``cutoffs``: slate -> first tip (UTC Timestamp), as in nm.build_features (live: {"live": cutoff});
    default: the night's first tip in ``box`` (historical nights only)."""
    reg = box[box["season_type"] == 2].copy()
    reg = reg.merge(extra, on=["game_id", "athlete_id"], how="left")
    if cutoffs is None:
        cutoffs = reg.assign(slate=reg["game_date"].astype(str)).groupby("slate")["start"].min()
    played = reg[~reg["did_not_play"] & (reg["minutes"] > 0)].sort_values("start")
    own = {a: (nm._ns(g["start"]), g["minutes"].to_numpy(float), g["season"].to_numpy(),
               g["fouls"].fillna(0).to_numpy(float))
           for a, g in played.groupby("athlete_id")}
    tl = nm._team_logs(box)
    # game totals per team (season, team) -> starts, totals
    gt = reg.groupby(["season", "team_abbreviation", "game_id"]).agg(
        start=("start", "first"), ts=("team_score", "first"), os=("opponent_team_score", "first")).reset_index()
    gt["tot"] = gt["ts"] + gt["os"]
    tot = {(s, t): (nm._ns(g.sort_values("start")["start"]), g.sort_values("start")["tot"].to_numpy(float))
           for (s, t), g in gt.groupby(["season", "team_abbreviation"])}

    out = []
    for slate, d in feats.groupby("slate", sort=True):
        cutoff = nm._ns(cutoffs[slate])
        season = int(d["season"].iloc[0])
        d = d.copy()
        n_ = len(d)
        f = {c: np.full(n_, np.nan) for c in NEW2}
        aids = d["athlete_id"].to_numpy()
        for i, a in enumerate(aids):
            o = own.get(a)
            if o is None:
                continue
            n = int(np.searchsorted(o[0], cutoff, "left"))
            if n == 0:
                continue
            s_, m_, se_, pf_ = o[0][:n], o[1][:n], o[2][:n], o[3][:n]
            f["own_load7"][i] = m_[s_ > cutoff - 7 * DAY].sum()
            cur = se_ == season
            mc, pc = m_[cur], pf_[cur]
            if len(mc):
                m10 = mc[-10:]
                f["own_min10"][i] = m10.mean()
                f["own_min_sd10"][i] = m10.std() if len(m10) >= 3 else np.nan
                f["own_min_cur"][i] = mc.mean()
                f["min_trend3"][i] = mc[-3:].mean() - mc.mean()
                f["own_min_max5"][i] = mc[-5:].max()
                f["own_min_min5"][i] = mc[-5:].min()
                f["pf36_10"][i] = 36 * pc[-10:].sum() / max(m10.sum(), 1.0)
                f["pf36_cur"][i] = 36 * pc.sum() / max(mc.sum(), 1.0)
        for team, g in d.groupby("team"):
            rows = np.flatnonzero(d["team"].to_numpy() == team)
            tlog = tl.get((season, team))
            k = int(np.searchsorted(tlog[0], cutoff, "left")) if tlog is not None else 0
            if k:
                starts = tlog[0][:k]
                f["tm_g4"][rows] = (starts > cutoff - 4 * DAY).sum()
                f["tm_g6"][rows] = (starts > cutoff - 6 * DAY).sum()
                mins, sts = tlog[1].iloc[:k], tlog[2].iloc[:k]
                M = np.nan_to_num(mins.to_numpy(float), nan=0.0)
                S = np.nan_to_num(sts.to_numpy(float), nan=0.0)
                M10, S10 = M[-10:], S[-10:]
                totm = M10.sum(1)
                ok = totm > 0
                if ok.any():
                    f["tm_st_share10"][rows] = float(((M10 * S10).sum(1)[ok] / totm[ok]).mean())
                    srt = -np.sort(-M10, axis=1)
                    f["tm_top7_10"][rows] = float((srt[:, :7].sum(1)[ok] / totm[ok]).mean())
                    top8 = np.argsort(-M10.sum(0))[:8]
                    f["tm_rot_sd"][rows] = float(M10[:, top8].std(0).mean())
                cols = {a: j for j, a in enumerate(mins.columns)}
                Mraw = mins.to_numpy(float)
                for i in rows:
                    j = cols.get(aids[i])
                    if j is None:
                        continue
                    z = np.nan_to_num(Mraw[:, j], nan=0.0)
                    # runs of >= 3 consecutive team games with 0 minutes, before the last game
                    pl = z > 0
                    if not pl.any():
                        continue
                    last_ret, ret_len = None, 0
                    run = 0
                    for gi in range(len(z)):
                        if not pl[gi]:
                            run += 1
                        else:
                            if run >= 3 and gi > run:  # an absence after he had played this season
                                last_ret, ret_len = gi, run
                            run = 0
                    if last_ret is None:
                        f["ret_n"][i] = 99
                        continue
                    post = z[last_ret:][pl[last_ret:]]
                    pre_idx = np.flatnonzero(pl[:last_ret - ret_len])
                    pre = z[pre_idx[-5:]] if len(pre_idx) else np.array([])
                    f["ret_n"][i] = len(post)
                    f["ret_len"][i] = ret_len
                    if len(pre) and len(post):
                        f["ret_ratio"][i] = post.mean() / max(pre.mean(), 1.0)
            for key, col in ((team, "tm_total"), (g["opp"].iloc[0], "opp_total")):
                tt = tot.get((season, key))
                kk = int(np.searchsorted(tt[0], cutoff, "left")) if tt is not None else 0
                v = tt[1][:kk] if kk else np.array([])
                v = v[~np.isnan(v)]
                f[col][rows] = (v.sum() + 5 * 225.0) / (len(v) + 5)
        out.append(pd.DataFrame(f, index=d.index))
    add = pd.concat(out)
    feats = feats.copy()
    for c in NEW2:
        feats[c] = add[c]
    # blowout: needs the opponent's b2b, which is a team-level feature of the other side of the game
    tb = feats.groupby(["slate", "team"])["b2b"].first()
    opp_b2b = pd.Series(list(zip(feats["slate"], feats["opp"]))).map(tb).to_numpy()
    feats["exp_margin"] = (feats["str_gap"] + 2.5 * (2 * feats["home"] - 1) - 1.0 * feats["b2b"]
                           + 1.0 * np.nan_to_num(opp_b2b.astype(float), nan=0.0))
    feats["abs_margin"] = feats["exp_margin"].abs()
    feats["blow_x_start"] = feats["abs_margin"] * feats["own_start_share"].fillna(0)
    feats["blow_x_min"] = feats["abs_margin"] * feats["own_min10"].fillna(0) / 36
    return feats
