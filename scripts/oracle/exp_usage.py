"""Experiment "usage": redistribute absent players' minutes and production to the active roster.

    python3 scripts/oracle/exp_usage.py [--feats-cache PATH] [--out data/cache/oracle/exp_usage.parquet]

Idea. The engine projects each active player from his own season averages and does nothing when a
teammate is out (it only scales minutes *down* when the team sums above 242). Teams still play ~241
minutes and score about the same DK total, so a fresh absence leaves a hole that the engine misses.

Point-in-time inputs per team-night (box-score games that tipped strictly before the night's first tip):
- absent rotation players: played for the team in its last 10 games this season (minutes > 0), not in
  tonight's box score and not playing for another team tonight (traded players dropped). Each carries the
  engine's projection (``player_table``: minutes, DK given he plays), his position group, his start share
  over his last 5 games played, whether he played the team's last game, his current missed-game streak,
  and his presence share p = games played for the team this season / team games (0.8 pseudo-games of
  "present" if he played for the team last season). Only the present share of an absence is new:
  teammates' season averages already include the games he missed.
- effective vacated minutes / DK points / starter slots, by position group: sum of A_min x p,
  A_dk x p, start_share x p over absent players;
- team hole = 241 - sum of active players' engine expected minutes (minutes x (1 - p_zero));
- per active player: position group, engine minutes, p_zero, rate, started the team's last game,
  start share and minutes per team game over the last 5 team games, rank of his minutes on the team and
  within his group, rank among his group's non-starters (the likely promotion), the group's open starter
  slots, his share of the team's active minutes;
- with/without (WOWY): for each absent player, the active player's mean minutes and DK points (DNPs as 0)
  in this season's team games without him minus with him (games after the absent player's first game for
  the team that the active player was in the box for), shrunk by 3 pseudo-games and weighted by p; summed
  over absent players (wowy_min, wowy_dk, wowy_n = games of evidence).

Models (fit on season 2025 = 2024-25 only; tested on 2026 and 2027):
- usage_prop: no learning; fill a positive hole in proportion to expected minutes (reference).
- usage_adj: structural share model. Extra minutes = hole+ x a+ x softmax(Z th+) + hole- x a- x softmax(Z th-)
  within each team-night; Z = the player's role / absence / WOWY features. Fit by least squares on 2025
  minutes (L-BFGS, small L2). Rate multiplier 1 + u, u linear in the freed DK share (and x minutes, guard,
  same-group, squared terms), fit in points given actual minutes (ridge).
- usage_gbm: LightGBM (L2, 400 trees, lr 0.03, 15 leaves, min 100 per leaf, L2 10, 0.8 row/feature
  subsampling) on the minutes residual minutes_actual - engine expected minutes, role + usage features;
  each team-night's expected minutes then rescaled to sum to 241.2 (the active players' minutes are the
  team's minutes); same rate multiplier.
- usage_ctrl: control. The same LightGBM on role features only (no hole / absence / team feature), no
  rescaling, no rate multiplier: what a better minutes model gives without any usage information.
- usage_wf: usage_gbm refit monthly on every NBA night strictly before the test month (2025 + earlier test).
- usage_lastcall: usage_gbm + tonight's starter flag and "promoted" (starts tonight, start share last 5 < 1)
  (public ~30 minutes before tip): last-call tier only.
Points = (engine expected minutes + predicted extra minutes) x engine rate x (1 + rate uplift).
Tuning choices (feature set, rescaling, which variant goes walk-forward) were made on 2025 out-of-fold only.
2025 rows carry 5-fold (month-contiguous) out-of-fold predictions, marked usage_split = "oof2025".
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dfs.data import nba  # noqa: E402
from dfs.projections.nba_provider import player_table  # noqa: E402

SLATES = ROOT / "data/cache/oracle/nba_slates.parquet"
OUT = ROOT / "data/cache/oracle/exp_usage.parquet"
TEAM_MIN = 241.0  # team minutes per game the hole is measured against (2024-25 mean incl. overtime: 241.4)
TEAM_MIN_RN = 241.2
GRP = {"G": 0, "F": 1, "C": 2}


def _ns(t) -> np.ndarray | int:
    if isinstance(t, pd.Series):
        return t.dt.tz_convert("UTC").dt.as_unit("ns").astype("int64").to_numpy()
    return int(pd.Timestamp(t).tz_convert("UTC").value)


def _grp_espn(p) -> str:
    p = str(p or "")
    return "G" if p in ("G", "PG", "SG") else "C" if p == "C" else "F"


def _grp_slate(p: str) -> str:
    s = set(str(p).split(","))
    return "C" if "C" in s else "G" if s & {"PG", "SG"} else "F"


def _team_logs(box: pd.DataFrame) -> dict:
    """(season, team) -> (game starts ns, minutes [games x athletes], NaN = not in box; starter; DK points)."""
    reg = box[box["season_type"] == 2].copy()
    reg["dk"] = np.where(reg["did_not_play"] | (reg["minutes"] <= 0), 0.0, nba.dk_points(reg))
    out = {}
    for (s, t), g in reg.groupby(["season", "team_abbreviation"]):
        order = g.groupby("game_id")["start"].first().sort_values()
        m = g.assign(m=np.where(g["did_not_play"], 0.0, g["minutes"])).pivot_table(
            index="game_id", columns="athlete_id", values="m", aggfunc="max").reindex(order.index)
        st = g.assign(s=(g["starter"].fillna(False).astype(bool) & ~g["did_not_play"] & (g["minutes"] > 0)).astype(float)
                      ).pivot_table(index="game_id", columns="athlete_id", values="s", aggfunc="max").reindex(order.index)
        dk = g.pivot_table(index="game_id", columns="athlete_id", values="dk", aggfunc="max").reindex(order.index)
        out[(s, t)] = (_ns(order), m, st, dk)
    return out


TEAM_FEATS = ["hole", "S_team", "n_active", "vac_eff", "vac_fresh", "vac_dk_eff", "vac_dk_share", "vac_st_eff",
              "vac_eff_G", "vac_eff_F", "vac_eff_C", "vac_dk_G", "vac_dk_F", "vac_dk_C", "open_st_G", "open_st_F",
              "open_st_C", "n_absent"]
PLAYER_FEATS = ["grp", "x", "starter_last", "start5", "min5", "gp5", "rank_team", "rank_grp", "promo_rank", "share_x",
                "vac_same", "vac_other", "vac_dk_same", "open_st_same", "promo_slot", "wowy_min", "wowy_dk", "wowy_n"]
WOWY_K = 3.0  # pseudo-games shrinking a with/without difference toward 0


def build_features(slates: pd.DataFrame, box: pd.DataFrame) -> pd.DataFrame:
    tl = _team_logs(box)
    reg = box[box["season_type"] == 2]
    cutoffs = reg.assign(slate=reg["game_date"].astype(str)).groupby("slate")["start"].min()
    apps = box.sort_values("start")
    app_t = {a: (_ns(g["start"]), g["team_abbreviation"].to_numpy()) for a, g in apps.groupby("athlete_id")}
    posg = apps.groupby("athlete_id")["athlete_position_abbreviation"].last().map(_grp_espn).to_dict()
    tonight = reg.assign(slate=reg["game_date"].astype(str))[["slate", "athlete_id", "starter"]]
    tonight = tonight.drop_duplicates(["slate", "athlete_id"]).rename(columns={"starter": "starter_tonight"})
    tonight["starter_tonight"] = tonight["starter_tonight"].fillna(False).astype(float)
    frames, absent_rows = [], []
    t0 = time.time()
    for k, (slate, d) in enumerate(slates.groupby("slate", sort=True)):
        cutoff_ts = cutoffs[slate]
        cutoff = _ns(cutoff_ts)
        season = int(d["season"].iloc[0])
        table = player_table(box, cutoff_ts, season)
        d = d.copy()
        d["x"] = d["minutes"] * (1 - d["p_zero"])
        d["grp"] = d["pos"].map(_grp_slate)
        on_tonight = set(d["athlete_id"])
        for c in PLAYER_FEATS[2:] + TEAM_FEATS:
            d[c] = 0.0
        for team, g in d.groupby("team"):
            ix = g.index
            S = float(g["x"].sum())
            d.loc[ix, "S_team"] = S
            d.loc[ix, "hole"] = TEAM_MIN - S
            d.loc[ix, "n_active"] = len(g)
            log = tl.get((season, team))
            n = int(np.searchsorted(log[0], cutoff, "left")) if log is not None else 0
            vac = {"eff": 0.0, "fresh": 0.0, "dk": 0.0, "st": 0.0, "n": 0.0}
            vg = {c: 0.0 for c in "GFC"}
            vdk = {c: 0.0 for c in "GFC"}
            open_st = {c: 0.0 for c in "GFC"}
            if n > 0:
                M = log[1].iloc[:n]
                ST = log[2].iloc[:n]
                played = (M > 0)
                # actives: recent role on this team
                last5 = M.iloc[-5:]
                st5 = ST.iloc[-5:]
                for i, a in zip(ix, g["athlete_id"]):
                    if a in M.columns:
                        d.at[i, "starter_last"] = float(ST[a].iloc[-1] == 1.0)
                        d.at[i, "start5"] = float(st5[a].fillna(0).mean())
                        d.at[i, "min5"] = float(last5[a].fillna(0).mean())
                        d.at[i, "gp5"] = float((last5[a] > 0).sum())
                # absent rotation players
                recent = played.iloc[-10:].any(axis=0)
                prev_log = tl.get((season - 1, team))
                prev_played = (prev_log[1] > 0).sum(axis=0) if prev_log is not None else pd.Series(dtype=float)
                ab = []
                for a in recent.index[recent.to_numpy()]:
                    if a in on_tonight:
                        continue
                    at = app_t.get(a)
                    j = int(np.searchsorted(at[0], cutoff, "left"))
                    if j == 0 or at[1][j - 1] != team:  # traded away
                        continue
                    pa = played[a].to_numpy()
                    gp = int(pa.sum())
                    q = 1.0 if float(prev_played.get(a, 0)) >= 10 else 0.0
                    p = (gp + 0.8 * q) / (n + 0.8)
                    streak = int(np.argmax(pa[::-1])) if pa.any() else n
                    sa = ST[a].to_numpy()[pa]
                    ss = float(np.nanmean(sa[-5:])) if len(sa) else 0.0
                    if a in table.index:
                        am, adk = float(table.at[a, "minutes"]), float(table.at[a, "mean"])
                    else:
                        mm = M[a].to_numpy()[pa]
                        am, adk = float(mm.mean()), float(mm.mean()) * 0.95
                    gr = posg.get(a, "F")
                    vac["eff"] += am * p
                    vac["fresh"] += am if streak == 0 else 0.0
                    vac["dk"] += adk * p
                    vac["st"] += am * p * ss
                    vac["n"] += 1
                    vg[gr] += am * p
                    vdk[gr] += adk * p
                    open_st[gr] += p * ss
                    absent_rows.append((slate, team, a, gr, am, adk, p, streak, ss))
                    ab.append((a, p))
                # with/without: each active player's minutes and DK points in this season's team games without each
                # absent player vs with him (games the active player was in the box for, after the absent player's
                # first game for the team), shrunk by WOWY_K games, weighted by the absent player's presence share p
                if ab:
                    Mv, DKv = M.to_numpy(), log[3].iloc[:n].reindex(columns=M.columns).to_numpy()
                    col = {c: j for j, c in enumerate(M.columns)}
                    for i, a_i in zip(ix, g["athlete_id"]):
                        if a_i not in col:
                            continue
                        mi, di = Mv[:, col[a_i]], DKv[:, col[a_i]]
                        inbox = ~np.isnan(mi)
                        wm = wd = wn = 0.0
                        for a, p in ab:
                            pa = Mv[:, col[a]]
                            first = int(np.argmax(~np.isnan(pa)))
                            on = inbox & (np.arange(n) >= first)
                            w_, wo = on & (pa > 0), on & ~(pa > 0)
                            nw, nwo = int(w_.sum()), int(wo.sum())
                            if nw == 0 or nwo == 0:
                                continue
                            shrink = nwo / (nwo + WOWY_K)
                            wm += p * shrink * (mi[wo].mean() - mi[w_].mean())
                            wd += p * shrink * (np.nan_to_num(di[wo]).mean() - np.nan_to_num(di[w_]).mean())
                            wn += nwo
                        d.at[i, "wowy_min"], d.at[i, "wowy_dk"], d.at[i, "wowy_n"] = wm, wd, wn
            team_dk = float((g["engine"]).sum())
            d.loc[ix, "vac_eff"] = vac["eff"]
            d.loc[ix, "vac_fresh"] = vac["fresh"]
            d.loc[ix, "vac_dk_eff"] = vac["dk"]
            d.loc[ix, "vac_dk_share"] = vac["dk"] / max(team_dk + vac["dk"], 1.0)
            d.loc[ix, "vac_st_eff"] = vac["st"]
            d.loc[ix, "n_absent"] = vac["n"]
            for c in "GFC":
                d.loc[ix, f"vac_eff_{c}"] = vg[c]
                d.loc[ix, f"vac_dk_{c}"] = vdk[c]
                d.loc[ix, f"open_st_{c}"] = open_st[c]
            gi = d.loc[ix]
            d.loc[ix, "rank_team"] = gi["x"].rank(ascending=False, method="first")
            d.loc[ix, "rank_grp"] = gi.groupby("grp")["x"].rank(ascending=False, method="first")
            d.loc[ix, "share_x"] = gi["x"] / max(S, 1.0)
            ns = gi["start5"] < 0.5
            pr = gi[ns].groupby("grp")["x"].rank(ascending=False, method="first")
            d.loc[ix, "promo_rank"] = 9.0
            d.loc[pr.index, "promo_rank"] = pr
        g3 = d["grp"]
        d["vac_same"] = np.select([g3 == "G", g3 == "F"], [d["vac_eff_G"], d["vac_eff_F"]], d["vac_eff_C"])
        d["vac_other"] = d["vac_eff"] - d["vac_same"]
        d["vac_dk_same"] = np.select([g3 == "G", g3 == "F"], [d["vac_dk_G"], d["vac_dk_F"]], d["vac_dk_C"])
        d["open_st_same"] = np.select([g3 == "G", g3 == "F"], [d["open_st_G"], d["open_st_F"]], d["open_st_C"])
        # expected starter slot for a backup: open slots in his group reached by his non-starter rank
        d["promo_slot"] = np.clip(d["open_st_same"] - (d["promo_rank"] - 1), 0, 1) * (d["start5"] < 0.5)
        d["grp"] = d["grp"].map(GRP).astype(float)
        frames.append(d)
        if k % 50 == 0:
            print(f"  features {k} nights {time.time() - t0:.0f}s", flush=True)
    f = pd.concat(frames, ignore_index=True)
    f = f.merge(tonight, on=["slate", "athlete_id"], how="left")
    f["starter_tonight"] = f["starter_tonight"].fillna(0.0)
    absent = pd.DataFrame(absent_rows, columns=["slate", "team", "athlete_id", "grp", "a_min", "a_dk", "p", "streak", "ss"])
    f.attrs["absent"] = absent
    return f


# ---------------------------------------------------------------- models

SHARE_FEATS = ["xs", "xs2", "lx", "start5", "st_x", "samefrac", "promo_slot", "p_zero", "benched", "absent_last",
               "isG", "isC", "m5", "wowy", "wowy_rel"]


def design(f: pd.DataFrame) -> pd.DataFrame:
    """Share-model inputs (one row per active player)."""
    z = pd.DataFrame(index=f.index)
    z["xs"] = f["x"] / 10
    z["xs2"] = z["xs"] ** 2
    z["lx"] = np.log1p(f["x"])
    z["start5"] = f["start5"]
    z["st_x"] = f["start5"] * z["xs"]
    z["samefrac"] = f["vac_same"] / f["vac_eff"].clip(lower=1.0)
    z["promo_slot"] = f["promo_slot"]
    z["p_zero"] = f["p_zero"]
    z["benched"] = f["benched"]
    z["absent_last"] = f["absent_last"]
    z["isG"] = (f["grp"] == 0).astype(float)
    z["isC"] = (f["grp"] == 2).astype(float)
    z["m5"] = f["min5"] / 10
    z["wowy"] = f["wowy_min"] / 10
    z["wowy_rel"] = f["wowy_min"] / f["hole"].abs().clip(lower=5.0)
    return z[SHARE_FEATS]


class ShareModel:
    """Extra minutes = hole+ x a+ x softmax(Z th+) + hole- x a- x softmax(Z th-) within each team-night."""

    def __init__(self, l2: float = 1e-3):
        self.l2 = l2
        self.theta = None

    @staticmethod
    def _groups(f: pd.DataFrame) -> np.ndarray:
        key = f["slate"].astype(str) + "|" + f["team"].astype(str)
        return pd.factorize(key)[0]

    def _pred(self, th: np.ndarray, Z: np.ndarray, g: np.ndarray, hp: np.ndarray, hn: np.ndarray, ng: int) -> np.ndarray:
        k = Z.shape[1]
        out = np.zeros(len(Z))
        for part, h in ((th[:k + 1], hp), (th[k + 1:], hn)):
            a, w = part[0], part[1:]
            e = np.exp(np.clip(Z @ w, -30, 30))
            den = np.bincount(g, weights=e, minlength=ng)
            out += a * h * e / den[g]
        return out

    def fit(self, f: pd.DataFrame, y: np.ndarray):
        from scipy.optimize import minimize
        Z = design(f).to_numpy()
        self.mu, self.sd = Z.mean(0), Z.std(0) + 1e-9
        Z = (Z - self.mu) / self.sd
        g = self._groups(f)
        ng = g.max() + 1
        h = f["hole"].to_numpy()
        hp, hn = np.clip(h, 0, None), np.clip(h, None, 0)
        k = Z.shape[1]

        def loss(th):
            p = self._pred(th, Z, g, hp, hn, ng)
            return float(np.mean((y - p) ** 2)) + self.l2 * float(np.sum(th[1:k + 1] ** 2) + np.sum(th[k + 2:] ** 2))

        th0 = np.zeros(2 * (k + 1))
        th0[0] = th0[k + 1] = 1.0
        res = minimize(loss, th0, method="L-BFGS-B", options={"maxiter": 400})
        self.theta = res.x
        return self

    def predict(self, f: pd.DataFrame) -> np.ndarray:
        Z = (design(f).to_numpy() - self.mu) / self.sd
        g = self._groups(f)
        h = f["hole"].to_numpy()
        return self._pred(self.theta, Z, g, np.clip(h, 0, None), np.clip(h, None, 0), g.max() + 1)

    def describe(self) -> str:
        k = len(SHARE_FEATS)
        a = self.theta
        s = f"fill+ {a[0]:.3f}, fill- {a[k + 1]:.3f}; logit weights per sd (+ / -):\n"
        return s + "\n".join(f"    {n:12s} {a[1 + i]:+.3f} / {a[k + 2 + i]:+.3f}" for i, n in enumerate(SHARE_FEATS))


RATE_FEATS = ["vds", "vds_x", "vds_G", "vds_same", "vds2"]


def rate_design(f: pd.DataFrame) -> pd.DataFrame:
    z = pd.DataFrame(index=f.index)
    v = f["vac_dk_share"]
    z["vds"] = v
    z["vds_x"] = v * f["x"] / 30
    z["vds_G"] = v * (f["grp"] == 0)
    z["vds_same"] = f["vac_dk_same"] / (f.groupby(["slate", "team"])["engine"].transform("sum") + f["vac_dk_eff"]).clip(lower=1)
    z["vds2"] = v ** 2
    return z[RATE_FEATS]


class RateModel:
    """Points-per-minute multiplier 1 + u, u linear in the freed DK share (fit in points, given actual minutes)."""

    def fit(self, f: pd.DataFrame):
        m = f["minutes_actual"].to_numpy() > 0
        base = (f["minutes_actual"] * f["rate"]).to_numpy()[m]
        X = rate_design(f).to_numpy()[m] * base[:, None]
        y = f["actual"].to_numpy()[m] - base
        lam = 1e3 * np.eye(X.shape[1])
        self.coef = np.linalg.solve(X.T @ X + lam, X.T @ y)
        return self

    def predict(self, f: pd.DataFrame) -> np.ndarray:
        return np.clip(rate_design(f).to_numpy() @ self.coef, -0.15, 0.4)

    def describe(self) -> str:
        return ", ".join(f"{n} {c:+.3f}" for n, c in zip(RATE_FEATS, self.coef))


ROLE = ["x", "minutes", "p_zero", "rate", "benched", "absent_last", "last_minutes", "n_cur", "n_prev", "moved",
        "starter_last", "start5", "min5", "gp5", "grp"]
USAGE = ["hole", "S_team", "n_active", "vac_eff", "vac_fresh", "vac_dk_eff", "vac_dk_share", "vac_st_eff",
         "vac_same", "vac_other", "vac_dk_same", "open_st_same", "promo_slot", "promo_rank", "rank_team", "rank_grp",
         "share_x", "wowy_min", "wowy_dk", "wowy_n"]
GBM_PARAMS = dict(objective="regression", learning_rate=0.03, num_leaves=15, min_child_samples=100,
                  feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1, lambda_l2=10.0, n_estimators=400,
                  num_threads=2, verbose=-1)


def gbm_fit(f: pd.DataFrame, feats: list[str], y: np.ndarray, seed: int = 0):
    import lightgbm as lgb
    return lgb.LGBMRegressor(**GBM_PARAMS, random_state=seed).fit(f[feats], y)


def points(f: pd.DataFrame, extra_min: np.ndarray, u: np.ndarray, renorm: bool = False) -> np.ndarray:
    """(expected minutes + extra) x rate x (1 + u). ``renorm``: scale each team-night's expected minutes to sum to
    TEAM_MIN_RN (active players' minutes add up to the team's minutes, 241.4 on average in 2024-25)."""
    xm = np.clip(f["x"].to_numpy() + extra_min, 0, 44)
    if renorm:
        key = f["slate"].astype(str) + "|" + f["team"].astype(str)
        xm = xm * TEAM_MIN_RN / pd.Series(xm, index=f.index).groupby(key.to_numpy()).transform("sum").to_numpy()
    return xm * f["rate"].to_numpy() * (1 + u)


def fit_predict(train: pd.DataFrame, test: pd.DataFrame, which: tuple[str, ...]) -> dict[str, np.ndarray]:
    """Fit on ``train`` and score ``test``: {variant: unconditional expected DK points}."""
    y = (train["minutes_actual"] - train["x"]).to_numpy()
    rm = RateModel().fit(train)
    u = rm.predict(test)
    out = {}
    if "adj" in which:
        sm = ShareModel().fit(train, y)
        out["adj"] = points(test, sm.predict(test), u)
        out["_share"], out["_rate"] = sm, rm
    if "gbm" in which:
        mdl = gbm_fit(train, ROLE + USAGE, y)
        out["gbm"] = points(test, mdl.predict(test[ROLE + USAGE]), u, renorm=True)
        out["_gbm"] = mdl
    if "ctrl" in which:  # control: same learner, no absence/hole/team feature, no renorm, no rate uplift
        mdl = gbm_fit(train, ROLE, y)
        out["ctrl"] = points(test, mdl.predict(test[ROLE]), np.zeros(len(test)))
    if "lastcall" in which:
        feats = ROLE + USAGE + ["starter_tonight", "promoted"]
        mdl = gbm_fit(train, feats, y)
        out["lastcall"] = points(test, mdl.predict(test[feats]), u, renorm=True)
    return out


def prop(f: pd.DataFrame) -> np.ndarray:
    """Reference: fill a positive hole in proportion to expected minutes (no learning)."""
    extra = np.clip(f["hole"], 0, None) * f["x"] / f["S_team"].clip(lower=1.0)
    return points(f, extra.to_numpy(), np.zeros(len(f)))


def prepare(f: pd.DataFrame) -> pd.DataFrame:
    f = f.copy()
    f["promoted"] = f["starter_tonight"] * (1 - f["start5"])
    f["month"] = f["slate"].astype(str).str[:7]
    counts = f.groupby("team").size()
    f["nba"] = f["team"].map(counts >= 500)  # drop exhibition rosters (All-Star / Rising Stars nights)
    return f


def folds_2025(f: pd.DataFrame, k: int = 5) -> np.ndarray:
    """Month-contiguous fold ids over 2025 nights (about equal numbers of nights)."""
    nights = np.array(sorted(f["slate"].unique()))
    fid = np.minimum((np.arange(len(nights)) * k) // len(nights), k - 1)
    return f["slate"].map(dict(zip(nights, fid))).to_numpy()


def player_metrics(d: pd.DataFrame, cols: list[str], label: str) -> str:
    lines = [f"{label} ({len(d)} rows)"]
    for c in cols:
        e = d[c] - d["actual"]
        lines.append(f"  {c:16s} MAE {np.mean(np.abs(e)):.3f}  RMSE {np.sqrt(np.mean(e ** 2)):.3f}  bias {e.mean():+.2f}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--feats-cache", help="parquet to read/write the feature table (skip the rebuild)")
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()
    t0 = time.time()
    if a.feats_cache and Path(a.feats_cache).exists():
        f = pd.read_parquet(a.feats_cache)
    else:
        box = nba.load_box([2024, 2025, 2026, 2027], max_age_hours=1e9)
        f = build_features(pd.read_parquet(SLATES), box)
        f.attrs = {}
        if a.feats_cache:
            f.to_parquet(a.feats_cache)
    f = prepare(f)
    print(f"features {time.time() - t0:.0f}s, {len(f)} rows")
    nba_rows = f["nba"].to_numpy()
    tr_mask = (f["season"] == 2025).to_numpy() & nba_rows
    te_mask = f["season"].isin([2026, 2027]).to_numpy()
    variants = ("adj", "gbm", "ctrl", "lastcall")
    cols = {w: np.full(len(f), np.nan) for w in variants + ("wf",)}
    split = np.full(len(f), "", dtype=object)

    # 2025: month-contiguous 5-fold out-of-fold predictions
    tr = f[tr_mask]
    fid = folds_2025(tr)
    tr_idx = np.flatnonzero(tr_mask)
    for k in range(5):
        m = fid == k
        o = fit_predict(tr[~m], tr[m], variants)
        for w in variants:
            cols[w][tr_idx[m]] = o[w]
    split[tr_idx] = "oof2025"
    print(f"oof done {time.time() - t0:.0f}s")

    # test: fit on all of 2025
    te = f[te_mask & nba_rows]
    te_idx = np.flatnonzero(te_mask & nba_rows)
    o = fit_predict(tr, te, variants)
    for w in variants:
        cols[w][te_idx] = o[w]
    split[np.flatnonzero(te_mask)] = "test"
    print("share model (fit on 2025):\n" + o["_share"].describe())
    print("rate model (fit on 2025): " + o["_rate"].describe())

    # walk-forward: refit at each test month on every NBA night strictly before it (2025 + earlier test months)
    for month in sorted(te["month"].unique()):
        trw = f[nba_rows & (f["slate"].astype(str) < f"{month}-01")]
        m = te["month"].to_numpy() == month
        o = fit_predict(trw, te[m], (WF,))
        cols["wf"][te_idx[m]] = o[WF]
    print(f"walk-forward done {time.time() - t0:.0f}s")

    out = f[["slate", "athlete_id"]].copy()
    out["usage_split"] = split
    out["usage_prop"] = prop(f)
    for w in variants + ("wf",):
        out[f"usage_{w}"] = cols[w]
    keep = te_mask | tr_mask
    eng = f["engine"].to_numpy()
    for c in [c for c in out.columns if c.startswith("usage_") and c != "usage_split"]:
        v = out[c].to_numpy(dtype=float, copy=True)
        v[~nba_rows] = eng[~nba_rows]  # exhibition rosters: no team history, keep the engine
        out[c] = v
    out = out[keep].reset_index(drop=True)
    out.loc[out["usage_split"] == "", "usage_split"] = "test"
    # the slate table repeats a few (slate, athlete_id) keys (All-Star night; one player listed by two teams on
    # 2026-10-23): keep one row per key so the evaluator's merge stays one-to-one with the slate table
    out = out.drop_duplicates(["slate", "athlete_id"]).reset_index(drop=True)
    out.to_parquet(a.out)
    print(f"wrote {a.out}: {len(out)} rows")

    # player-level accuracy
    ev = f[keep].merge(out, on=["slate", "athlete_id"], how="left")
    pcols = ["engine", "usage_prop", "usage_adj", "usage_gbm", "usage_ctrl", "usage_wf", "usage_lastcall"]
    for lab, s in (("TEST 2026+2027", ev["usage_split"] == "test"), ("OOF 2025", ev["usage_split"] == "oof2025")):
        d = ev[s]
        print(player_metrics(d, [c for c in pcols if d[c].notna().all()], f"{lab} all"))
        print(player_metrics(d[d["vacated_min"] >= 25], [c for c in pcols if d[c].notna().all()], f"{lab} vacated_min >= 25"))
        print(player_metrics(d[d["vacated_min"] < 25], [c for c in pcols if d[c].notna().all()], f"{lab} vacated_min < 25"))
    print(f"total {time.time() - t0:.0f}s")


WF = "gbm"

if __name__ == "__main__":
    main()
