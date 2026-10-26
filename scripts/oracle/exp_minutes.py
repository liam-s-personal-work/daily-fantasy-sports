"""Experiment "minutes": predict tonight's minutes, then points = P(plays) x E[minutes | plays] x rate.

    python3 scripts/oracle/exp_minutes.py [--tune] [--features cache.parquet] [--out data/cache/oracle/exp_minutes.parquet]

Features (all point-in-time: box-score games that tipped strictly before the night's first tip; the active pool
tonight is the slate table's, as for every column):
- engine: minutes, p_zero, rate, last_minutes, benched, absent_last, vacated_min, n_cur, n_prev, moved,
  rest_days, b2b, home (from nba_slates.parquet), and engine expected minutes = minutes x (1 - p_zero);
- team-game logs this season: his minutes in the team's last 1/3/5 games (0 if he sat, was inactive or not on
  the team yet), share of the last 5 started / in uniform / played, current streak of team games missed;
- his own log: minutes in his last 1/3/5 games played, start share this season, days since he last played,
  gap before that game (returning from injury), last season's minutes;
- tonight's roster: rank of his engine minutes and of his last-5 minutes among active teammates, teammates'
  summed engine and last-5 minutes, his engine share of 240, minutes of last-5 regulars not active tonight
  (vacated5; same position group vac_pos; those who played the team's last game vac_new), last game's starters
  not active tonight (st_out, st_out_pos), rotation size, active count;
- with/without (WOWY): his minutes in this season's team games a now-absent regular missed minus games he played
  (shrunk n/(n+3)), summed over absent regulars (wowy_all) and over newly absent ones (wowy_new);
- rest history: share of games he was in uniform but sat after playing the previous game, on back-to-back second
  legs and otherwise (shrunk), with counts;
- strength: season-to-date point differential per game (shrunk to 0.6 x last season), opponent's, the gap and
  its absolute value (blowout proxy).

Model: LightGBM classifier P(minutes > 0) + LightGBM L2 regressor of minutes on rows that played (15 leaves,
min 100 rows per leaf, learning rate 0.03, 300 rounds each; 350/400 for last call; rounds from early stopping on a
2025 Oct-Jan / Feb-Apr split, scaled up for the full season). Train on season 2025 only; test 2026 and 2027.
Score columns (unconditional expected DK points):
- minutes_main: P x E[min | plays] x engine rate, no tonight-starter information;
- minutes_rate: the same minutes x a learned rate (LightGBM on DK points per minute, weighted by minutes played,
  from the engine rate, recent per-minute rates, opponent, role and vacated minutes);
- minutes_lastcall: + tonight's starter flag (public ~30 minutes before tip; LAST-CALL TIER ONLY);
- minutes_wf: main features, retrained monthly on every night strictly before the test month (walk-forward).
Diagnostics: minutes_xmin_* (expected minutes), minutes_pplay_* (P(plays)).
2025 rows carry month-grouped out-of-fold predictions (minutes_split = "oof2025": trained on the other 2025
months, so not forward-only); test rows have minutes_split = "test".
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from dfs.data import nba

ROOT = Path(__file__).resolve().parents[2]
SLATES = ROOT / "data/cache/oracle/nba_slates.parquet"
THREADS = 2

ENGINE_FEATS = ["minutes", "p_zero", "rate", "last_minutes", "benched", "absent_last", "vacated_min", "n_cur", "n_prev",
                "moved", "rest_days", "b2b", "home"]
NEW_FEATS = ["eng_xmin", "tm_k", "tm_min1", "tm_min3", "tm_min5", "tm_start1", "tm_start5", "tm_inbox5", "tm_played5",
             "tm_miss_streak", "tm_with", "own_min1", "own_min3", "own_min5", "own_start_share", "own_last_start",
             "own_n", "days_since_played", "gap_before_last", "own_min_prev", "rank_eng", "rank_l5", "team_eng_sum",
             "team_l5_sum", "vacated5", "n_active", "share_eng", "str_team", "str_opp", "str_gap", "str_absgap",
             "own_rate5", "own_rate10", "own_rate_cur",
             "rest_b2b", "rest_b2b_n", "rest_any", "rest_any_n", "wowy_new", "wowy_all", "rot_size5", "rot10_5",
             "vac_pos", "vac_new", "st_out", "st_out_pos"]
LC_FEATS = ["starter_tonight", "lc_new_starters", "lc_role_rank", "lc_starters_l5", "lc_bench_l5"]
RATE_ONLY = {"own_rate5", "own_rate10", "own_rate_cur"}
MAIN = ENGINE_FEATS + [c for c in NEW_FEATS if c not in RATE_ONLY]
LASTCALL = MAIN + ["starter_tonight"]  # the lc_* team features added nothing on the 2025 split
# points-per-minute model (given he plays), weighted by minutes played
RATE = ["rate", "own_rate5", "own_rate10", "own_rate_cur", "n_cur", "n_prev", "moved", "opp_allowed", "home", "b2b",
        "rest_days", "vacated_min", "vacated5", "eng_xmin", "minutes", "tm_min5", "tm_start5", "own_start_share",
        "rank_eng", "share_eng", "str_gap", "str_absgap", "days_since_played"]


# ---------------------------------------------------------------- features

def _ns(t) -> np.ndarray | int:
    """UTC nanoseconds (sortable int64) for a tz-aware Timestamp or Series."""
    if isinstance(t, pd.Series):
        return t.dt.tz_convert("UTC").dt.as_unit("ns").astype("int64").to_numpy()
    return int(pd.Timestamp(t).tz_convert("UTC").value)


def _team_logs(box: pd.DataFrame) -> dict:
    """(season, team) -> (starts, minutes pivot [games x athletes, NaN = not in box], starter pivot)."""
    reg = box[box["season_type"] == 2]
    out = {}
    for (s, t), g in reg.groupby(["season", "team_abbreviation"]):
        order = g.groupby("game_id")["start"].first().sort_values()
        mins = g.assign(m=np.where(g["did_not_play"], 0.0, g["minutes"])).pivot_table(
            index="game_id", columns="athlete_id", values="m", aggfunc="max").reindex(order.index)
        st = g.assign(s=(g["starter"].fillna(False).astype(bool) & ~g["did_not_play"] & (g["minutes"] > 0)).astype(float)
                      ).pivot_table(index="game_id", columns="athlete_id", values="s", aggfunc="max").reindex(order.index)
        out[(s, t)] = (_ns(order), mins, st)
    return out


def _group(p: str) -> str:
    p = str(p or "")
    return "C" if "C" in p.split(",") or p == "C" else "G" if any(x in p for x in ("PG", "SG")) or p == "G" else "F"


def _rest_events(tl: dict) -> dict:
    """athlete -> (start ns, b2b second leg?, sat?) for every team game he was in uniform for after playing the
    team's previous game (regular season). 'Sat' = in the box score with no minutes (rest / coach's decision)."""
    ev = []
    for (s, t), (starts, mins, _) in tl.items():
        if len(starts) < 2:
            continue
        m = mins.to_numpy()
        rest = np.diff(starts) / 86400e9
        prev_played = m[:-1] > 0
        inbox = ~np.isnan(m[1:])
        gi, ai = np.nonzero(prev_played & inbox)
        ev.append(pd.DataFrame({"athlete_id": mins.columns.to_numpy()[ai], "start": starts[1:][gi],
                                "b2b": rest[gi] < 1.5, "sat": ~(m[1:][gi, ai] > 0)}))
    e = pd.concat(ev).sort_values("start")
    return {a: (g["start"].to_numpy(), g["b2b"].to_numpy(), g["sat"].to_numpy()) for a, g in e.groupby("athlete_id")}


def _strength(box: pd.DataFrame) -> dict:
    """(season, team) -> (starts, cumulative point differential) over regular-season games."""
    reg = box[box["season_type"] == 2]
    pts = reg.groupby(["season", "game_id", "team_abbreviation"]).agg(pts=("points", "sum"), start=("start", "first"),
                                                                       opp=("opponent_team_abbreviation", "first"))
    pts = pts.reset_index()
    opp = pts[["game_id", "team_abbreviation", "pts"]].rename(columns={"team_abbreviation": "opp", "pts": "opp_pts"})
    pts = pts.merge(opp, on=["game_id", "opp"], how="left")
    pts["diff"] = pts["pts"] - pts["opp_pts"]
    out = {}
    for (s, t), g in pts.sort_values("start").groupby(["season", "team_abbreviation"]):
        out[(s, t)] = (_ns(g["start"]), np.cumsum(g["diff"].fillna(0.0).to_numpy()))
    return out


def _str_before(st: dict, season: int, team: str, cutoff) -> float:
    cur = st.get((season, team))
    prev = st.get((season - 1, team))
    prev_mean = float(prev[1][-1] / len(prev[1])) if prev is not None and len(prev[1]) else 0.0
    n, tot = 0, 0.0
    if cur is not None:
        n = int(np.searchsorted(cur[0], cutoff, "left"))
        tot = float(cur[1][n - 1]) if n else 0.0
    k = 10.0
    return (tot + k * 0.6 * prev_mean) / (n + k)


def build_features(slates: pd.DataFrame, box: pd.DataFrame) -> pd.DataFrame:
    reg = box[box["season_type"] == 2]
    tl = _team_logs(box)
    st = _strength(box)
    played = box[~box["did_not_play"] & (box["minutes"] > 0)].sort_values("start").copy()
    played["dk"] = nba.dk_points(played)
    own = {a: (_ns(g["start"]), g["minutes"].to_numpy(), g["season"].to_numpy(),
               g["starter"].fillna(False).astype(bool).to_numpy(), g["season_type"].to_numpy(), g["dk"].to_numpy())
           for a, g in played.groupby("athlete_id")}
    rest_ev = _rest_events(tl)
    posg = box.sort_values("start").groupby("athlete_id")["athlete_position_abbreviation"].first().map(_group).to_dict()
    tonight_start = reg[["game_date", "athlete_id", "starter", "did_not_play", "minutes"]].copy()
    tonight_start["slate"] = tonight_start["game_date"].astype(str)
    tonight_start["starter_tonight"] = tonight_start["starter"].fillna(False).astype(float)
    tonight_start = tonight_start.drop_duplicates(["slate", "athlete_id"])[["slate", "athlete_id", "starter_tonight"]]

    cutoffs = reg.assign(slate=reg["game_date"].astype(str)).groupby("slate")["start"].min()
    frames = []
    for slate, d in slates.groupby("slate", sort=True):
        cutoff = _ns(cutoffs[slate])
        season = int(d["season"].iloc[0])
        d = d.copy()
        f = {c: np.full(len(d), np.nan) for c in NEW_FEATS}
        idx = {a: i for i, a in enumerate(d["athlete_id"].to_numpy())}
        # own logs
        for i, a in enumerate(d["athlete_id"].to_numpy()):
            # rest history for everyone (audit fix): skipping players with no played game anywhere in the data
            # told the model who never plays
            r = rest_ev.get(a)
            nr = int(np.searchsorted(r[0], cutoff, "left")) if r is not None else 0
            b2b, sat = (r[1][:nr], r[2][:nr]) if r is not None else (np.zeros(0, bool), np.zeros(0))
            nb, na = int(b2b.sum()), int((~b2b).sum())
            f["rest_b2b_n"][i], f["rest_any_n"][i] = nb, na
            f["rest_b2b"][i] = (sat[b2b].sum() + 2 * 0.05) / (nb + 2)
            f["rest_any"][i] = (sat[~b2b].sum() + 4 * 0.03) / (na + 4)
            o = own.get(a)
            if o is None:
                continue
            n = int(np.searchsorted(o[0], cutoff, "left"))
            if n == 0:
                continue
            s_, m_, se_, sr_, ty_, dk_ = (x[:n] for x in o)
            f["days_since_played"][i] = (cutoff - s_[-1]) / 86400e9
            if n >= 2:
                f["gap_before_last"][i] = (s_[-1] - s_[-2]) / 86400e9
            cur = se_ == season
            prev = (se_ == season - 1) & (ty_ == 2)
            f["own_min_prev"][i] = m_[prev].mean() if prev.any() else np.nan
            mc, sc = m_[cur], sr_[cur]
            f["own_n"][i] = len(mc)
            if len(mc):
                f["own_min1"][i] = mc[-1]
                f["own_min3"][i] = mc[-3:].mean()
                f["own_min5"][i] = mc[-5:].mean()
                f["own_start_share"][i] = sc.mean()
                f["own_last_start"][i] = float(sc[-1])
                dc = dk_[cur]
                f["own_rate5"][i] = dc[-5:].sum() / mc[-5:].sum()
                f["own_rate10"][i] = dc[-10:].sum() / mc[-10:].sum()
                f["own_rate_cur"][i] = dc.sum() / mc.sum()
        # team logs
        for team, g in d.groupby("team"):
            rows = [idx[a] for a in g["athlete_id"]]
            tlog = tl.get((season, team))
            k = 0
            if tlog is not None:
                k = int(np.searchsorted(tlog[0], cutoff, "left"))
            for i in rows:
                f["tm_k"][i] = k
            if k:
                mins, sts = tlog[1].iloc[:k], tlog[2].iloc[:k]
                last5 = mins.iloc[-5:]
                l5 = last5.fillna(0.0).mean()  # all athletes on the team's recent box scores
                for i, a in zip(rows, g["athlete_id"]):
                    if a in mins.columns:
                        col = mins[a].to_numpy()
                        z = np.nan_to_num(col, nan=0.0)
                        f["tm_min1"][i] = z[-1]
                        f["tm_min3"][i] = z[-3:].mean()
                        f["tm_min5"][i] = z[-5:].mean()
                        scol = np.nan_to_num(sts[a].to_numpy(), nan=0.0)
                        f["tm_start1"][i] = scol[-1]
                        f["tm_start5"][i] = scol[-5:].mean()
                        f["tm_inbox5"][i] = (~np.isnan(col[-5:])).mean()
                        f["tm_played5"][i] = (z[-5:] > 0).mean()
                        miss = 0
                        for v in z[::-1]:
                            if v > 0:
                                break
                            miss += 1
                        f["tm_miss_streak"][i] = miss
                        f["tm_with"][i] = (~np.isnan(col)).sum()
                    else:
                        for c in ("tm_min1", "tm_min3", "tm_min5", "tm_start1", "tm_start5", "tm_inbox5", "tm_played5",
                                  "tm_with"):
                            f[c][i] = 0.0
                        f["tm_miss_streak"][i] = k
                active = set(g["athlete_id"])
                gone = [a for a in l5.index if a not in active]
                f_vac = float(l5[gone].sum())
                M = mins.to_numpy()
                played_g = M > 0
                f_rot = float(played_g[-5:].sum(1).mean())
                f_rot10 = float((M[-5:] >= 10).sum(1).mean())
                # with / without: each active player's minutes in team games a now-absent regular missed vs played
                cols = {a: j for j, a in enumerate(mins.columns)}
                regs = [a for a in gone if l5[a] >= 8 or (M[-10:, cols[a]] > 0).sum() >= 3]
                new = {a for a in regs if M[-1, cols[a]] > 0}
                vac_new = float(sum(l5[a] for a in new))
                last_st = sts.iloc[-1]
                st_gone = [a for a in gone if last_st.get(a, 0) == 1]
                f_st_out = float(len(st_gone))
                st_pos = {}
                for a in st_gone:
                    st_pos[posg.get(a, "F")] = st_pos.get(posg.get(a, "F"), 0) + 1
                gpos = {}
                for a in gone:
                    gpos[posg.get(a, "F")] = gpos.get(posg.get(a, "F"), 0.0) + float(l5[a])
                wn, wa = np.zeros(len(rows)), np.zeros(len(rows))
                act_idx = [cols.get(a) for a in g["athlete_id"]]
                for a in regs:
                    jc = M[:, cols[a]]
                    out_m, in_m = ~(jc > 0), jc > 0
                    for r_, ci in enumerate(act_idx):
                        if ci is None:
                            continue
                        x = M[:, ci]
                        v = ~np.isnan(x)
                        no, ni = int((out_m & v).sum()), int((in_m & v).sum())
                        if no == 0 or ni == 0:
                            continue
                        dlt = (x[out_m & v].mean() - x[in_m & v].mean()) * no / (no + 3)
                        wa[r_] += dlt
                        if a in new:
                            wn[r_] += dlt
            else:
                f_vac, f_rot, f_rot10, vac_new, gpos, f_st_out, st_pos = np.nan, np.nan, np.nan, np.nan, None, np.nan, None
                wn, wa = np.full(len(rows), np.nan), np.full(len(rows), np.nan)
            eng = (g["minutes"] * (1 - g["p_zero"])).to_numpy()
            l5v = np.array([f["tm_min5"][i] for i in rows])
            r_eng = pd.Series(-eng).rank(method="min").to_numpy()
            r_l5 = pd.Series(-np.nan_to_num(l5v, nan=0.0)).rank(method="min").to_numpy()
            for j, i in enumerate(rows):
                f["eng_xmin"][i] = eng[j]
                f["rank_eng"][i] = r_eng[j]
                f["rank_l5"][i] = r_l5[j]
                f["team_eng_sum"][i] = eng.sum()
                f["team_l5_sum"][i] = np.nansum(l5v) if k else np.nan
                f["vacated5"][i] = f_vac
                f["n_active"][i] = len(rows)
                f["share_eng"][i] = eng[j] / eng.sum() * 240 if eng.sum() > 0 else np.nan
                f["rot_size5"][i], f["rot10_5"][i], f["vac_new"][i] = f_rot, f_rot10, vac_new
                f["wowy_new"][i], f["wowy_all"][i] = wn[j], wa[j]
                f["vac_pos"][i] = gpos.get(_group(g["pos"].iloc[j]), 0.0) if gpos is not None else np.nan
                f["st_out"][i] = f_st_out
                f["st_out_pos"][i] = st_pos.get(_group(g["pos"].iloc[j]), 0) if st_pos is not None else np.nan
            s_t = _str_before(st, season, team, cutoff)
            s_o = _str_before(st, season, g["opp"].iloc[0], cutoff)
            for i in rows:
                f["str_team"][i], f["str_opp"][i] = s_t, s_o
                f["str_gap"][i], f["str_absgap"][i] = s_t - s_o, abs(s_t - s_o)
        for c in NEW_FEATS:
            d[c] = f[c]
        frames.append(d)
    out = pd.concat(frames)
    out = out.merge(tonight_start, on=["slate", "athlete_id"], how="left")
    out["starter_tonight"] = out["starter_tonight"].fillna(0.0)
    return lastcall_feats(out)


def lastcall_feats(d: pd.DataFrame) -> pd.DataFrame:
    """Last-call tier only: tonight's starting lineups (public ~30 minutes before tip)."""
    d = d.copy()
    key = [d["slate"], d["team"]]
    st = d["starter_tonight"]
    l5 = d["tm_min5"].fillna(0.0)
    d["lc_new_starters"] = ((st == 1) & (d["tm_start1"].fillna(0) == 0)).groupby(key).transform("sum")
    d["lc_starters_l5"] = (l5 * st).groupby(key).transform("sum")
    d["lc_bench_l5"] = (l5 * (1 - st)).groupby(key).transform("sum")
    d["lc_role_rank"] = (-l5).groupby([d["slate"], d["team"], st]).rank(method="min")
    return d


# ---------------------------------------------------------------- model

CLF = dict(objective="binary", learning_rate=0.03, num_leaves=15, min_child_samples=100, subsample=0.8, subsample_freq=1,
           colsample_bytree=0.8, reg_lambda=1.0, n_estimators=300, num_threads=THREADS, verbose=-1)
REG = dict(CLF, objective="regression")
ROUNDS = {"main": (300, 300), "lastcall": (350, 400)}
RATE_P = dict(REG, n_estimators=150, min_child_samples=200)


def fit(train: pd.DataFrame, feats: list[str], rounds: tuple[int, int] = (300, 300)):
    y = (train["minutes_actual"] > 0).astype(int)
    c = lgb.LGBMClassifier(**dict(CLF, n_estimators=rounds[0])).fit(train[feats], y)
    pl = train[y == 1]
    r = lgb.LGBMRegressor(**dict(REG, n_estimators=rounds[1])).fit(pl[feats], pl["minutes_actual"])
    return c, r


def fit_rate(train: pd.DataFrame):
    pl = train[train["minutes_actual"] >= 1]
    return lgb.LGBMRegressor(**RATE_P).fit(pl[RATE], pl["actual"] / pl["minutes_actual"], sample_weight=pl["minutes_actual"])


def predict(models, df: pd.DataFrame, feats: list[str]) -> tuple[np.ndarray, np.ndarray]:
    c, r = models
    p = c.predict_proba(df[feats])[:, 1]
    m = np.clip(r.predict(df[feats]), 0.0, 48.0)
    return p, m


def points(df: pd.DataFrame, p: np.ndarray, m: np.ndarray) -> np.ndarray:
    return p * m * df["rate"].to_numpy()


def report(df: pd.DataFrame, cols: list[str], label: str) -> None:
    print(f"\n{label} (n={len(df)})")
    pl = df["minutes_actual"] > 0
    print(f"  {'column':24s} {'MAE':>6s} {'RMSE':>6s} {'MAE|pl':>7s} {'RMSE|pl':>7s} {'bias':>6s}")
    for c in cols:
        e = df[c] - df["actual"]
        print(f"  {c:24s} {e.abs().mean():6.3f} {np.sqrt((e ** 2).mean()):6.3f} {e[pl].abs().mean():7.3f} "
              f"{np.sqrt((e[pl] ** 2).mean()):7.3f} {e.mean():+6.2f}")


def tune(d25: pd.DataFrame) -> None:
    """Time split inside 2025: train Oct-Jan, validate Feb-Apr. Early stopping picks the rounds."""
    tr, va = d25[d25["slate"] < "2025-02-01"], d25[d25["slate"] >= "2025-02-01"]
    for feats, name in ((MAIN, "main"), (LASTCALL, "lastcall")):
        ytr, yva = (tr["minutes_actual"] > 0).astype(int), (va["minutes_actual"] > 0).astype(int)
        for nl in (7, 15, 31, 63):
            for mcs in (50, 200):
                cp = dict(CLF, num_leaves=nl, min_child_samples=mcs, n_estimators=3000)
                rp = dict(REG, num_leaves=nl, min_child_samples=mcs, n_estimators=3000)
                c = lgb.LGBMClassifier(**cp).fit(tr[feats], ytr, eval_set=[(va[feats], yva)],
                                                 callbacks=[lgb.early_stopping(100, verbose=False)])
                ptr, pva = tr[ytr == 1], va[yva == 1]
                r = lgb.LGBMRegressor(**rp).fit(ptr[feats], ptr["minutes_actual"],
                                                eval_set=[(pva[feats], pva["minutes_actual"])],
                                                callbacks=[lgb.early_stopping(100, verbose=False)])
                p, m = c.predict_proba(va[feats])[:, 1], np.clip(r.predict(va[feats]), 0, 48)
                pts = p * m * va["rate"].to_numpy()
                e = pts - va["actual"].to_numpy()
                xm = p * m - va["minutes_actual"].to_numpy()
                print(f"{name} leaves={nl:3d} mcs={mcs:3d} clf_it={c.best_iteration_:4d} reg_it={r.best_iteration_:4d} "
                      f"logloss={c.best_score_['valid_0']['binary_logloss']:.4f} min_rmse|pl={r.best_score_['valid_0']['l2'] ** 0.5:.3f} "
                      f"xmin_rmse={np.sqrt((xm ** 2).mean()):.3f} pts_mae={np.abs(e).mean():.3f} pts_rmse={np.sqrt((e ** 2).mean()):.3f}",
                      flush=True)
    e = va["engine"] - va["actual"]
    xm = va["minutes"] * (1 - va["p_zero"]) - va["minutes_actual"]
    print(f"engine: xmin_rmse={np.sqrt((xm ** 2).mean()):.3f} pts_mae={e.abs().mean():.3f} pts_rmse={np.sqrt((e ** 2).mean()):.3f}")


def score(models: dict, d: pd.DataFrame) -> pd.DataFrame:
    """All score columns for rows ``d`` from models fit on other rows."""
    out = pd.DataFrame(index=d.index)
    for name, feats in (("main", MAIN), ("lastcall", LASTCALL)):
        p, m = predict(models[name], d, feats)
        out[f"minutes_{name}"] = points(d, p, m)
        out[f"minutes_xmin_{name}"] = p * m
        out[f"minutes_pplay_{name}"] = p
    out["minutes_rate"] = out["minutes_xmin_main"] * models["rate"].predict(d[RATE])
    return out


def fit_all(train: pd.DataFrame) -> dict:
    return {"main": fit(train, MAIN, ROUNDS["main"]), "lastcall": fit(train, LASTCALL, ROUNDS["lastcall"]),
            "rate": fit_rate(train)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tune", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "data/cache/oracle/exp_minutes.parquet"))
    ap.add_argument("--features", default="", help="feature cache (built if missing)")
    a = ap.parse_args()
    cache = Path(a.features) if a.features else None
    if cache is not None and cache.exists():
        df = pd.read_parquet(cache)
    else:
        slates = pd.read_parquet(SLATES)
        box = nba.load_box([2024, 2025, 2026, 2027])
        df = build_features(slates, box)
        if cache is not None:
            df.to_parquet(cache)
    df = df.reset_index(drop=True)
    d25 = df[df["season"] == 2025]
    if a.tune:
        tune(d25)
        return
    test = df[df["season"].isin([2026, 2027])].copy()
    test = test.join(score(fit_all(d25), test))
    # walk-forward: retrain monthly on all nights strictly before the test month
    test["month"] = test["slate"].str[:7]
    wf = pd.Series(np.nan, index=test.index)
    for mo in sorted(test["month"].unique()):
        tr = df[df["slate"] < f"{mo}-01"]
        sel = test["month"] == mo
        p, m = predict(fit(tr, MAIN, ROUNDS["main"]), test[sel], MAIN)
        wf[sel] = points(test[sel], p, m)
        print(f"walk-forward {mo}: trained on {tr['slate'].nunique()} nights", flush=True)
    test["minutes_wf"] = wf
    test["minutes_split"] = "test"
    # 2025 out-of-fold (month-grouped; not forward-only)
    d25 = d25.copy()
    d25["month"] = d25["slate"].str[:7]
    parts = [score(fit_all(d25[d25["month"] != mo]), d25[d25["month"] == mo]) for mo in sorted(d25["month"].unique())]
    d25 = d25.join(pd.concat(parts))
    d25["minutes_wf"] = np.nan
    d25["minutes_split"] = "oof2025"
    res = pd.concat([test, d25], ignore_index=True)
    cols = ["minutes_main", "minutes_rate", "minutes_lastcall", "minutes_wf", "minutes_xmin_main", "minutes_xmin_lastcall",
            "minutes_pplay_main", "minutes_pplay_lastcall", "minutes_split"]
    # (slate, athlete_id) repeats on All-Star nights and once on 2026-10-23: keep one row per key so the evaluator's
    # merge stays one-to-one with the slate table (the repeated rows share the first row's scores)
    res[["slate", "athlete_id"] + cols].drop_duplicates(["slate", "athlete_id"]).to_parquet(a.out, index=False)
    print(f"wrote {res.drop_duplicates(['slate', 'athlete_id']).shape[0]} rows -> {a.out}")
    scores = ["engine", "dk_avg", "minutes_main", "minutes_rate", "minutes_lastcall", "minutes_wf"]
    t = res[res["minutes_split"] == "test"]
    report(t, scores, "test 2026+2027, player level")
    for s, g in t.groupby("season"):
        report(g, scores, f"test season {s}")
    report(res[res["minutes_split"] == "oof2025"], ["engine", "dk_avg", "minutes_main", "minutes_rate", "minutes_lastcall"],
           "2025 out-of-fold")
    xm = t["minutes"] * (1 - t["p_zero"])
    print("\nexpected minutes vs actual minutes, test rows")
    for c, v in (("engine", xm), ("minutes_xmin_main", t["minutes_xmin_main"]),
                 ("minutes_xmin_lastcall", t["minutes_xmin_lastcall"])):
        e = v - t["minutes_actual"]
        print(f"  {c:22s} MAE {e.abs().mean():.3f} RMSE {np.sqrt((e ** 2).mean()):.3f} bias {e.mean():+.2f}")


if __name__ == "__main__":
    sys.exit(main())
