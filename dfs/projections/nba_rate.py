"""NBA per-minute rate model: a heavily regularised residual on the engine's DK points per minute.

Tuned and tested in ``scripts/oracle/exp_rate2.py`` (train 2024-25, settings chosen on 2025-26 before January, test
the rest of 2025-26 and 2026-27: with the same minutes, lineups reached 71.4% of the ex-post optimum vs 69.7% with the
engine rate, +1.76 points [+0.66, +2.78] on 101 nights). The live engine applies it with the minutes model
(``NBAProvider._apply_minutes_model``): expected points = P(plays) x minutes x engine rate x multiplier, the multiplier
within exp(+-0.15). The feature code below is shared by training (``scripts/fit_nba_minutes.py``) and live inference.

Box scores have no FGA/FTA, so usage is proxied by production shares (points, rebounds, assists, DK points) and pace
by team points for + against per game. Features use only games that tipped strictly before the slate's first tip;
tonight's pool (who is absent) is the slate's rows of each team.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from dfs.data import nba
from dfs.projections.nba_minutes import LEARNED, THREADS, _group, _ns

HL = (3, 5, 10, 20, 40)  # half-lives (games) of the recency-weighted rates


FEATS = ["lr_3", "lr_5", "lr_10", "lr_20", "lr_40", "cur_rate_rel", "st_bn_rel", "hi_lo_rel", "wowy_rel", "wowy_joint_rel",
         "wowy_max", "redist_rel", "vac_dk_share", "own_dk_share", "n_absent", "absent_w", "pace_rel", "opp_pos_rel",
         "opp_allowed", "home", "rest_c", "b2b", "start_share", "min_role", "minutes", "rate", "pts_pm", "reb_pm",
         "ast_pm", "tov_pm", "grp", "n_cur", "n_prev", "moved", "vacated_min"]


PARAMS = dict(objective="l2", learning_rate=0.02, num_leaves=7, min_child_samples=400, reg_lambda=20.0, subsample=0.8,
              subsample_freq=1, colsample_bytree=0.7, num_threads=THREADS, verbose=-1)


SETTINGS = {"rounds": 700, "seeds": [0, 1, 2], "alpha": 0.5, "clip": 0.15, "min_minutes": 6.0, "rate_floor": 0.2,
            "target_clip": [0.25, 4.0]}



def _opp_allowed(reg: pd.DataFrame) -> pd.DataFrame:
    """(season, opponent, game) -> DK points allowed and tip (ns): ``scripts/oracle/nba_slates.py`` opp_allowed."""
    h = reg[~reg["did_not_play"]]
    return h.assign(dk=nba.dk_points(h), ns=_ns(h["start"])).groupby(
        ["season", "opponent_team_abbreviation", "game_id"]).agg(dk=("dk", "sum"), ns=("ns", "first")).reset_index()


def build_rate_features(slates: pd.DataFrame, box: pd.DataFrame, cutoffs: dict | None = None) -> pd.DataFrame:
    """Point-in-time rate features (``FEATS``) for every row of ``slates`` (one slate = one night; needs athlete_id,
    team, opp, pos, season and the engine's minutes, rate, rest_days, home, b2b, n_cur, n_prev, moved, vacated_min).
    ``cutoffs``: slate -> first tip (UTC Timestamp); default: the night's first tip in ``box``."""
    reg = box[box["season_type"] == 2]
    pl = box[~box["did_not_play"] & (box["minutes"] > 0)].sort_values("start").copy()
    pl["dk"] = nba.dk_points(pl)
    pl["ns"] = _ns(pl["start"])
    own = {a: (g["ns"].to_numpy(), g["minutes"].to_numpy(float), g["dk"].to_numpy(float), g["season"].to_numpy(),
               g["starter"].fillna(False).astype(bool).to_numpy(), g["points"].to_numpy(float),
               g["rebounds"].to_numpy(float), g["assists"].to_numpy(float), g["turnovers"].to_numpy(float))
           for a, g in pl.groupby("athlete_id")}
    # team-season pivots (regular season, games x athletes, 0 = did not play) of minutes and production
    regp = pl[pl["season_type"] == 2]
    tl = {}
    for (s, t), g in regp[regp["season"].isin(slates["season"].unique())].groupby(["season", "team_abbreviation"]):
        order = g.groupby("game_id")["ns"].first().sort_values()
        tl[(s, t)] = (order.to_numpy(), {k: g.pivot_table(index="game_id", columns="athlete_id", values=k, aggfunc="max")
                                         .reindex(order.index).fillna(0.0)
                                         for k in ("minutes", "dk", "points", "rebounds", "assists")})
    # team games: points for + against (pace proxy)
    tg = regp.groupby(["season", "game_id", "team_abbreviation"]).agg(
        pts=("points", "sum"), ns=("ns", "first"), opp=("opponent_team_abbreviation", "first")).reset_index()
    tg = tg.merge(tg[["game_id", "team_abbreviation", "pts"]].rename(columns={"team_abbreviation": "opp", "pts": "opp_pts"}),
                  on=["game_id", "opp"], how="left")
    tg["tot"] = tg["pts"] + tg["opp_pts"]
    tgd = {k: (g["ns"].to_numpy(), g["tot"].to_numpy(float))
           for k, g in tg.sort_values("ns").groupby(["season", "team_abbreviation"])}
    # DK points and minutes the defence allowed to each position group, and the league's, cumulative by tip
    posg = box.sort_values("start").groupby("athlete_id")["athlete_position_abbreviation"].first().map(_group).to_dict()
    og = regp.assign(grp=regp["athlete_id"].map(posg)).groupby(["season", "game_id", "opponent_team_abbreviation", "grp"]).agg(
        dk=("dk", "sum"), mn=("minutes", "sum"), ns=("ns", "first")).reset_index().sort_values("ns")
    ogd = {k: (g["ns"].to_numpy(), np.cumsum(g["dk"].to_numpy(float)), np.cumsum(g["mn"].to_numpy(float)))
           for k, g in og.groupby(["season", "opponent_team_abbreviation", "grp"])}
    lg_g = {k: (g["ns"].to_numpy(), np.cumsum(g["dk"].to_numpy(float)), np.cumsum(g["mn"].to_numpy(float)))
            for k, g in og.groupby(["season", "grp"])}
    allowed = _opp_allowed(reg)

    if cutoffs is None:
        cutoffs = reg.assign(slate=reg["game_date"].astype(str)).groupby("slate")["start"].min()
    frames = []
    for slate, d in slates.groupby("slate", sort=True):
        cutoff = _ns(cutoffs[slate])
        season = int(d["season"].iloc[0])
        d = d.copy()
        lt = tg[(tg["season"] == season) & (tg["ns"] < cutoff)]
        lg_tot = lt["tot"].mean() if len(lt) > 20 else tg[tg["season"] == season - 1]["tot"].mean()
        al = allowed[(allowed["season"] == season) & (allowed["ns"] < cutoff)]
        per = al.groupby("opponent_team_abbreviation")["dk"].mean()
        d["opp_allowed"] = d["opp"].map((per / per.mean()).to_dict()).fillna(1.0)
        ctx = {t: _team_ctx(tl.get((season, t)), set(g["athlete_id"]), cutoff) for t, g in d.groupby("team")}
        for t, c in ctx.items():
            tt = tgd.get((season, t))
            if tt is not None:
                m = int(np.searchsorted(tt[0], cutoff, "left"))
                c["tot_pg"] = (tt[1][:m].sum() + 10 * lg_tot) / (m + 10)
        rows = [_row_feats(a, t, o, p, season, cutoff, own.get(a), ctx[t], tgd, ogd, lg_g, lg_tot)
                for a, t, o, p in zip(d["athlete_id"], d["team"], d["opp"], d["pos"])]
        d = d.drop(columns=[c for c in RAW if c in d.columns]).reset_index(drop=True)
        frames.append(pd.concat([d, pd.DataFrame(rows, columns=RAW)], axis=1))
    return _derived(pd.concat(frames, ignore_index=True))


RAW = ["grp"] + [f"rw_{x}_{h}" for h in HL for x in ("dk", "min")] + [
    "cur_min", "cur_dk", "cur_n", "start_share", "st_min", "st_dk", "bn_min", "bn_dk", "avg_min_cur", "hi_rate", "lo_rate",
    "pts_pm", "reb_pm", "ast_pm", "tov_pm", "wowy_w", "wowy_sum", "wowy_max", "wowy_joint", "n_absent", "absent_w",
    "redist_dk_pg", "vac_dk_share", "vac_dk_all_share", "own_dk_share", "pace_rel", "opp_pos_rel"]


def _team_ctx(tv, pool: set, cutoff: int) -> dict:
    """A team's games this season before ``cutoff``: regulars absent from tonight's pool and production per game."""
    if tv is None:
        return {"n": 0}
    n = int(np.searchsorted(tv[0], cutoff, "left"))
    if n < 1:
        return {"n": 0}
    pv = {k: v.iloc[:n] for k, v in tv[1].items()}
    mins = pv["minutes"]
    gp = (mins > 0).sum()
    avgm = mins.sum() / gp.clip(lower=1)
    last10 = (mins.iloc[-10:] > 0).mean()
    absent = [b for b in mins.columns if gp[b] >= 3 and avgm[b] >= 15 and b not in pool]
    wt = {b: float(last10[b]) for b in absent}  # recently active absentees matter (long-term ones are in the rate)
    per_g = {k: v.sum() / gp.clip(lower=1) for k, v in pv.items()}
    act = [b for b in mins.columns if b in pool]
    stats = ("points", "rebounds", "assists", "dk")
    return {"n": n, "pv": pv, "absent": absent, "wt": wt, "per_g": per_g,
            "act_tot": {k: float(sum(per_g[k][b] * last10[b] for b in act)) + 1e-6 for k in stats},
            "vac": {k: float(sum(per_g[k][b] * wt[b] for b in absent)) for k in stats},
            "vac_all": float(sum(per_g["dk"][b] for b in absent)), "team_dk_pg": float(pv["dk"].sum(axis=1).mean())}


def _row_feats(a, t, o, pos, season, cutoff, ow, c, tgd, ogd, lg_g, lg_tot) -> dict:
    q = _group(pos)
    f = {"grp": {"G": 0, "F": 1, "C": 2}[q]}
    # recency-weighted rates over the last 82 games played, this season's role and minute splits, production rates
    n = int(np.searchsorted(ow[0], cutoff, "left")) if ow is not None else 0
    if n:
        _, mn, dk, se, sr, pts, reb, ast, tov = (x[:n] for x in ow)
        mn82, dk82, se82, sr82 = mn[-82:], dk[-82:], se[-82:], sr[-82:]
        age = np.arange(len(mn82))[::-1]
        for h in HL:
            w = 0.5 ** (age / h)
            f[f"rw_dk_{h}"], f[f"rw_min_{h}"] = float((w * dk82).sum()), float((w * mn82).sum())
        cur = se82 == season
        f["cur_min"], f["cur_dk"], f["cur_n"] = float(mn82[cur].sum()), float(dk82[cur].sum()), int(cur.sum())
        if cur.any():
            f["start_share"] = float(sr82[cur].mean())
            for lab, msk in (("st", cur & sr82), ("bn", cur & ~sr82)):  # starter vs bench rate (role)
                f[f"{lab}_min"], f[f"{lab}_dk"] = float(mn82[msk].sum()), float(dk82[msk].sum())
            mc, dc = mn82[cur], dk82[cur]  # rate in high- vs low-minute games (diminishing returns)
            hi = mc > np.median(mc)
            f["avg_min_cur"] = float(mc.mean())
            f["hi_rate"] = float(dc[hi].sum() / max(mc[hi].sum(), 1)) if hi.any() else np.nan
            f["lo_rate"] = float(dc[~hi].sum() / max(mc[~hi].sum(), 1))
        m20 = mn[-20:]
        f["pts_pm"], f["reb_pm"], f["ast_pm"], f["tov_pm"] = (float(x[-20:].sum() / m20.sum()) for x in (pts, reb, ast, tov))
    # teammates out: with/without rate and usage redistribution. A player with no game for the team yet gets
    # zeros: the pivots' columns span the whole season, so whether he has one would tell if he plays later
    if c["n"] >= 1:
        pv = c["pv"]
        has = a in pv["minutes"].columns
        ma, da = (pv["minutes"][a].to_numpy(), pv["dk"][a].to_numpy()) if has else (np.zeros(c["n"]), np.zeros(c["n"]))
        playd = ma > 0
        tot_m, tot_d = ma[playd].sum(), da[playd].sum()
        rate_all = tot_d / tot_m if tot_m > 0 else np.nan
        ws, wsum, wmax = 0.0, 0.0, 0.0
        joint = playd.copy()
        for b in c["absent"]:
            mb = pv["minutes"][b].to_numpy()
            wo = playd & ~(mb > 0)
            mwo = ma[wo].sum()
            if mwo > 0 and tot_m > 0:
                dl = (da[wo].sum() / mwo - rate_all) * mwo / (mwo + 100.0)
                ws += dl * c["wt"][b]
                wsum += dl
                wmax = max(wmax, dl * c["wt"][b])
            joint &= ~(mb > 0)
        f["wowy_w"], f["wowy_sum"], f["wowy_max"] = ws, wsum, wmax
        mj = ma[joint].sum()
        f["wowy_joint"] = ((da[joint].sum() / mj - rate_all) * mj / (mj + 100.0)) if (mj > 0 and len(c["absent"])) else 0.0
        f["n_absent"], f["absent_w"] = len(c["absent"]), float(sum(c["wt"].values()))
        # vacated (recently active) production, split by tonight's pool production shares
        red = 0.0
        for k, wk in (("points", 1.0), ("rebounds", 1.25), ("assists", 1.5)):
            red += wk * c["vac"][k] * float(c["per_g"][k].get(a, 0.0)) / c["act_tot"][k]
        f["redist_dk_pg"] = red
        f["vac_dk_share"] = c["vac"]["dk"] / max(c["team_dk_pg"], 1.0)
        f["vac_dk_all_share"] = c["vac_all"] / max(c["team_dk_pg"], 1.0)
        f["own_dk_share"] = float(c["per_g"]["dk"].get(a, 0.0)) / max(c["team_dk_pg"], 1.0)
    # environment: pace of both teams, opponent's DK allowed per minute to this position group
    tto = tgd.get((season, o))
    if tto is not None:
        m = int(np.searchsorted(tto[0], cutoff, "left"))
        oto = (tto[1][:m].sum() + 10 * lg_tot) / (m + 10)
    else:
        oto = lg_tot
    f["pace_rel"] = (0.5 * (c.get("tot_pg", lg_tot) + oto)) / lg_tot
    od, lgq = ogd.get((season, o, q)), lg_g.get((season, q))
    f["opp_pos_rel"] = 1.0
    if od is not None and lgq is not None:
        odk, omn = _cum_before(od, cutoff)
        ldk, lmn = _cum_before(lgq, cutoff)
        if lmn > 0:
            lrate = ldk / lmn
            f["opp_pos_rel"] = ((odk + 400 * lrate) / (omn + 400)) / lrate
    return f


def _cum_before(arrs, cutoff: int) -> tuple[float, float]:
    n = int(np.searchsorted(arrs[0], cutoff, "left"))
    return (arrs[1][n - 1], arrs[2][n - 1]) if n else (0.0, 0.0)


def _derived(d: pd.DataFrame) -> pd.DataFrame:
    """Rates relative to the engine's (shrunk toward it by pseudo-minutes) and the other model inputs."""
    eng = d["rate"].clip(lower=SETTINGS["rate_floor"])

    def rel(dk, mn, k):
        return (d[dk].fillna(0) + k * eng) / (d[mn].fillna(0) + k)

    for h in HL:
        d[f"lr_{h}"] = np.log(rel(f"rw_dk_{h}", f"rw_min_{h}", 60) / eng)
    d["cur_rate_rel"] = np.log(rel("cur_dk", "cur_min", 30) / eng)
    d["st_bn_rel"] = np.log(rel("st_dk", "st_min", 60) / rel("bn_dk", "bn_min", 60))
    d["hi_lo_rel"] = np.log(d["hi_rate"].clip(0.2, 3) / d["lo_rate"].clip(0.2, 3))
    d["wowy_rel"] = d["wowy_w"].fillna(0) / eng
    d["wowy_joint_rel"] = d["wowy_joint"].fillna(0) / eng
    d["redist_rel"] = d["redist_dk_pg"].fillna(0) / (d["minutes"].clip(lower=8) * eng)
    d["min_role"] = d["minutes"] / d["avg_min_cur"].clip(lower=4)
    d["rest_c"] = d["rest_days"].clip(upper=7)
    for c in FEATS:
        d[c] = d[c].astype(float)
    return d


# ---------------------------------------------------------------- fit / save / load / predict

def target(d: pd.DataFrame) -> pd.Series:
    """log(actual DK points per minute / engine rate), clipped to [1/4, 4] times."""
    lo, hi = SETTINGS["target_clip"]
    return np.log((d["actual"] / d["minutes_actual"] / d["rate"].clip(lower=SETTINGS["rate_floor"])).clip(lo, hi))


def fit(train: pd.DataFrame) -> list:
    """One L2 booster per seed on rows that played ``min_minutes``+, weighted by minutes played."""
    import lightgbm as lgb

    tr = train[train["minutes_actual"] >= SETTINGS["min_minutes"]]
    return [lgb.LGBMRegressor(**dict(PARAMS, n_estimators=SETTINGS["rounds"], random_state=s)).fit(
        tr[FEATS], target(tr), sample_weight=tr["minutes_actual"]) for s in SETTINGS["seeds"]]


def multiplier(preds: list[np.ndarray], settings: dict = SETTINGS) -> np.ndarray:
    """Per-seed predicted log-ratios -> rate multiplier: alpha x their mean, within exp(+-clip)."""
    return np.exp(np.clip(settings["alpha"] * np.mean(preds, axis=0), -settings["clip"], settings["clip"]))


def save(models: list, meta: dict, folder: Path = LEARNED) -> None:
    """Boosters as LightGBM text files plus a JSON with the features, settings and what they were trained on."""
    folder.mkdir(parents=True, exist_ok=True)
    for seed, m in zip(SETTINGS["seeds"], models):
        m.booster_.save_model(str(folder / f"nba_rate_s{seed}.txt"))
    meta = dict(meta, features=FEATS, settings=SETTINGS, params=PARAMS)
    (folder / "nba_rate.json").write_text(json.dumps(meta, indent=1))


class RateModel:
    """Saved per-seed boosters of the per-minute residual."""

    def __init__(self, folder: Path = LEARNED):
        import lightgbm as lgb

        self.meta = json.loads((folder / "nba_rate.json").read_text())
        self.feats = self.meta["features"]
        files = [folder / f"nba_rate_s{s}.txt" for s in self.meta["settings"]["seeds"]]
        missing = [str(f) for f in files if not f.exists()]
        if missing:
            raise FileNotFoundError(", ".join(missing))
        self.boosters = [lgb.Booster(model_file=str(f)) for f in files]

    @classmethod
    def load(cls, folder: Path = LEARNED) -> "RateModel | None":
        try:
            return cls(folder)
        except (OSError, ImportError, KeyError, json.JSONDecodeError):
            return None

    def multiplier(self, feats: pd.DataFrame) -> np.ndarray:
        """Multiplier on the engine's points per minute, one per row."""
        x = feats[self.feats].astype(float)
        return multiplier([b.predict(x) for b in self.boosters], self.meta["settings"])
