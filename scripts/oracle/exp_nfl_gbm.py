"""Experiment "nfl gbm": a LightGBM correction on top of the NFL engine for the oracle-gap backtest.

Target: actual DK points (unconditional: a player who does not play scores 0).

Pool (selection-leakage fix): pool A rows are in the slate table because the player actually played
>= 25% of snaps, so "in the table" means "played". Training AND evaluation use a strictly pre-game pool,
``pool_pre`` = every DST row + per (slate, team, position) the top players by engine mean among the
table's rows (QB 1, RB 2, WR 3, TE 1). Pool B guarantees those top players are present whether or not
they played, so pool_pre membership is a pre-game fact. The full table is reported as a secondary view
(models trained on pool_pre, applied to every row).

Availability gate: game-day INA (and any other non-ACT roster status) players score 0 by construction
(337 INA rows in pool_pre, all with actual 0). INA is announced ~90 min before each kickoff: before the
1 PM lock for early games, late-swap information for 4 PM games. Every nflgbm_* column except the
``_noina`` variant sets these rows to 0 and the GBMs are fit on the remaining rows; the control
``nflgbm_eng_ina`` (engine with the same gate) isolates what the GBM adds beyond that information.

Features (pre-game only; actual, snap_pct_actual, pool and projected are never used): engine,
engine_cond, p_play, p10/p50/p90, base_std/base_last3/base_xfp, pseudo salary, home, late, implied,
opp_implied, spread, total, roof, projected usage/shares (s_*, proj_*), depth_rank, depth buckets,
role, injury report/practice (ordinal), p_active, pi_start, last_pct, last_start, season-to-date usage,
dk_* history, opp_pos_allowed, week, plus within-team features computed on pool_pre only (engine rank
in team-position, share of the team-position engine, team offensive engine sum, the team QB1's engine).

Models (one model for all positions, pos as a categorical):
- direct: LightGBM regression on actual;
- resid: LightGBM regression on actual - engine, prediction engine + resid_hat;
- blend: engine + w * resid_hat, w picked on 2024 (validation) by mean pct of optimal;
- q10 / q90: LightGBM quantile models (unconditional quantiles).
Small trees and strong regularisation; the configuration (leaves, min rows per leaf, L2, monotone
engine constraint for the direct model) and the number of rounds are chosen on 2024 player-level MSE.
Variance-reduced variants (added after the MSE-picked models lost on lineups through the winner's curse):
- resid_reg: heavier grid (3-6 leaves, 100-400 rows per leaf, L2 10-50, lr 0.02), the most regularised
  config within one slate-clustered SE of the best 2024 MSE, bagged over 10 seeds;
- resid_shr: resid_reg's correction shrunk per player toward the position mean by tau2 / (tau2 + v),
  v = max(slate-bootstrap, season-jackknife) variance of the correction, tau2 empirical Bayes;
- blend_reg: engine + w * resid_reg correction, w chosen on the 71 non-test slates (2021-23 OOF + 2024).

Protocol: fit on 2021-2023, validate on 2024, test on 2025 and 2026. Primary test columns are refit on
2021-2024 with the 2024-chosen configuration and rounds; the ``_s`` columns are the strict 2021-2023 fit.
Output predictions: 2021-2023 = leave-one-season-out within 2021-2023 (out-of-fold, not walk-forward),
2024 = the 2021-2023 fit, 2025-2026 = the refit (``_s``: the 2021-2023 fit).

    python3 scripts/oracle/exp_nfl_gbm.py   # -> data/cache/oracle/exp_nfl_gbm.parquet, res_nfl_gbm.csv
"""

from __future__ import annotations

import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dfs.oracle import Rules, evaluate_slate, paired_bootstrap  # noqa: E402

SLATES = ROOT / "data/cache/oracle/nfl_slates.parquet"
OUT = ROOT / "data/cache/oracle/exp_nfl_gbm.parquet"
RES = ROOT / "data/cache/oracle/res_nfl_gbm.csv"
CAPS = {"QB": 1, "RB": 2, "WR": 3, "TE": 1}
TRAIN, VAL, TEST = [2021, 2022, 2023], [2024], [2025, 2026]
THREADS, WORKERS = 2, 2
SEEDS = [7, 11, 23]
OUT_STATUS = {"INA", "RES", "CUT", "DEV", "RET", "EXE", "SUS", "PUP", "NWT", "TRC", "TRD", "UFA", "E14"}
T0 = time.time()

NUM = ["engine", "engine_cond", "p_play", "p10", "p50", "p90", "base_std", "base_last3", "base_xfp", "salary",
       "home", "late", "implied", "opp_implied", "spread", "total", "s_tgt", "s_car", "s_rtd", "s_utd",
       "proj_targets", "proj_rec", "proj_rec_yds", "proj_carries", "proj_rush_yds", "proj_pass_att",
       "proj_pass_yds", "proj_pass_td", "proj_rush_td", "proj_rec_td", "depth_rank", "p_active", "pi_start",
       "last_pct", "last_start", "games_std", "snap_std", "tgt_share_std", "car_share_std", "snap_prev", "dk_ppg",
       "dk_n", "dk_ppg_prev", "dk_n_prev", "dk_l3", "opp_pos_allowed", "week"]
DERIVED = ["eng_rank", "eng_pos_share", "team_eng", "qb1_eng", "p_spread", "inj_report_o", "inj_practice_o"]
CAT = ["pos", "roof", "role", "depth_bucket", "depth_bucket_dk"]
FEATURES = NUM + DERIVED + CAT
POST_GAME = {"actual", "snap_pct_actual", "pool", "projected"}
assert not POST_GAME & set(FEATURES)


def log(msg: str) -> None:
    print(f"[{time.time() - T0:6.0f}s] {msg}", file=sys.stderr, flush=True)


# ------------------------------------------------------------------------------------------- data

def load() -> pd.DataFrame:
    d = pd.read_parquet(SLATES)
    d = d.sort_values(["slate", "team", "pos", "engine", "player_id"],
                      ascending=[True, True, True, False, True]).reset_index(drop=True)
    rk = d.groupby(["slate", "team", "pos"]).cumcount() + 1
    d["pool_pre"] = (d["pos"] == "DST") | (rk <= d["pos"].map(CAPS).fillna(0))
    # within-team features from pool_pre rows only (pre-game facts, independent of who else is in the table)
    pre = d[d["pool_pre"]]
    rank_of = pre.groupby(["slate", "team", "pos"]).cumcount() + 1
    d["eng_rank"] = (d["pos"].map(CAPS).fillna(0) + 1).astype(float)
    d.loc[pre.index, "eng_rank"] = rank_of.astype(float)
    d.loc[d["pos"] == "DST", "eng_rank"] = 1.0
    pos_sum = pre.groupby(["slate", "team", "pos"])["engine"].sum().rename("pos_sum")
    d = d.join(pos_sum, on=["slate", "team", "pos"])
    d["eng_pos_share"] = d["engine"] / d["pos_sum"].replace(0, np.nan)
    off = pre[pre["pos"] != "DST"]
    d["team_eng"] = d[["slate", "team"]].merge(off.groupby(["slate", "team"])["engine"].sum().rename("t").reset_index(),
                                               on=["slate", "team"], how="left")["t"].to_numpy()
    qb1 = pre[pre["pos"] == "QB"].set_index(["slate", "team"])["engine"].rename("q").reset_index()
    d["qb1_eng"] = d[["slate", "team"]].merge(qb1, on=["slate", "team"], how="left")["q"].to_numpy()
    d["p_spread"] = d["p90"] - d["p10"]
    d["inj_report_o"] = d["inj_report"].map({"none": 0, "questionable": 1, "doubtful": 2, "out": 3}).astype(float)
    d["inj_practice_o"] = d["inj_practice"].map({"none": 0, "fp": 1, "lp": 2, "dnp": 3}).astype(float)
    for c in CAT:
        d[c] = pd.Categorical(d[c].astype(str))
    d["gated"] = d["status"].isin(OUT_STATUS)  # known 0 (game-day inactive or off the active roster)
    return d.drop(columns=["pos_sum"])


# ----------------------------------------------------------------------------------------- models

BASE = dict(objective="regression", learning_rate=0.03, feature_fraction=0.6, bagging_fraction=0.8, bagging_freq=1,
            max_bin=63, min_data_per_group=50, cat_smooth=20, cat_l2=10, verbose=-1, num_threads=THREADS)


def grid(direct: bool) -> list[dict]:
    out = []
    for leaves in (4, 8, 15):
        for mdl in (40, 100):
            for l2 in (1.0, 10.0):
                for mono in ((False, True) if direct else (False,)):
                    out.append(dict(num_leaves=leaves, min_data_in_leaf=mdl, lambda_l2=l2, mono=mono))
    return out


def lgb_params(cfg: dict, seed: int, objective: str = "regression", alpha: float | None = None) -> dict:
    p = {**BASE, **{k: v for k, v in cfg.items() if k != "mono"}, "seed": seed, "objective": objective}
    if alpha is not None:
        p["alpha"] = alpha
    if cfg.get("mono"):
        p["monotone_constraints"] = [1 if f == "engine" else 0 for f in FEATURES]
        p["monotone_constraints_method"] = "advanced"
    return p


def fit(X, y, cfg, rounds, seed, objective="regression", alpha=None):
    ds = lgb.Dataset(X, y, categorical_feature=CAT, free_raw_data=False)
    return lgb.train(lgb_params(cfg, seed, objective, alpha), ds, num_boost_round=rounds)


def fit_es_booster(Xtr, ytr, Xva, yva, cfg, seed, objective="regression", alpha=None):
    tr = lgb.Dataset(Xtr, ytr, categorical_feature=CAT, free_raw_data=False)
    va = lgb.Dataset(Xva, yva, categorical_feature=CAT, reference=tr, free_raw_data=False)
    metric = "l2" if objective == "regression" else "quantile"
    p = {**lgb_params(cfg, seed, objective, alpha), "metric": metric}
    b = lgb.train(p, tr, num_boost_round=3000, valid_sets=[va], callbacks=[lgb.early_stopping(150, verbose=False)])
    return b, b.best_score["valid_0"][metric]


def fit_es(Xtr, ytr, Xva, yva, cfg, seed, objective="regression", alpha=None):
    """Early-stopped on the validation season; returns (best_iter, best_score)."""
    b, sc = fit_es_booster(Xtr, ytr, Xva, yva, cfg, seed, objective, alpha)
    return b.best_iteration, sc


def predict_avg(Xtr, ytr, Xte, cfg, rounds, objective="regression", alpha=None) -> np.ndarray:
    return np.mean([fit(Xtr, ytr, cfg, rounds, s, objective, alpha).predict(Xte) for s in SEEDS], axis=0)


def tune(d: pd.DataFrame, target: str, direct: bool, feats: list[str]) -> tuple[dict, int, pd.DataFrame]:
    tr = d[d["season"].isin(TRAIN)]
    va = d[d["season"].isin(VAL)]
    rows = []
    for cfg in grid(direct):
        it, sc = fit_es(tr[feats], tr[target], va[feats], va[target], cfg, SEEDS[0])
        rows.append({**cfg, "iters": it, "val_mse": sc})
    g = pd.DataFrame(rows).sort_values("val_mse")
    best = g.iloc[0]
    cfg = dict(num_leaves=int(best["num_leaves"]), min_data_in_leaf=int(best["min_data_in_leaf"]),
               lambda_l2=float(best["lambda_l2"]), mono=bool(best["mono"]))
    return cfg, int(best["iters"]), g


def run_model(d: pd.DataFrame, target: str, direct: bool, feats: list[str], name: str) -> tuple[pd.Series, pd.Series, dict]:
    """Tune on 2024, then: LOSO OOF on 2021-23, 2021-23 fit for 2024 (+ strict test), 2021-24 refit for test.
    Returns (primary prediction of ``target`` for every row, strict prediction, info)."""
    fit_rows = d[d["pool_pre"] & ~d["gated"]]
    cfg, iters, g = tune(fit_rows, target, direct, feats)
    log(f"{name}: best {cfg} iters={iters} val_mse={g['val_mse'].iloc[0]:.3f} (grid top 3:\n"
        f"{g.head(3).to_string(index=False)})")
    prim = pd.Series(np.nan, index=d.index)
    strict = pd.Series(np.nan, index=d.index)
    for s in TRAIN:  # out-of-fold within the training seasons
        tr = fit_rows[fit_rows["season"].isin([t for t in TRAIN if t != s])]
        te = d["season"] == s
        prim[te] = predict_avg(tr[feats], tr[target], d.loc[te, feats], cfg, iters)
    strict[d["season"].isin(TRAIN)] = prim[d["season"].isin(TRAIN)]
    tr = fit_rows[fit_rows["season"].isin(TRAIN)]
    te = d["season"].isin(VAL + TEST)
    strict[te] = predict_avg(tr[feats], tr[target], d.loc[te, feats], cfg, iters)
    prim[d["season"].isin(VAL)] = strict[d["season"].isin(VAL)]
    tr = fit_rows[fit_rows["season"].isin(TRAIN + VAL)]
    te = d["season"].isin(TEST)
    prim[te] = predict_avg(tr[feats], tr[target], d.loc[te, feats], cfg, iters)
    imp = fit(tr[feats], tr[target], cfg, iters, SEEDS[0]).feature_importance("gain")
    top = pd.Series(imp, index=feats).sort_values(ascending=False)
    return prim, strict, {"cfg": cfg, "iters": iters, "grid": g, "importance": top / top.sum()}


# ---- variance-reduced residual model: heavier regularisation (1-SE rule), 10-seed bagging, and
# ---- empirical-Bayes shrinkage of each player's correction by its across-season / bootstrap variance

REG_BASE = dict(learning_rate=0.02, feature_fraction=0.5, bagging_fraction=0.7)
SEEDS_REG = list(range(101, 111))
N_BOOT = 8


def reg_grid() -> list[dict]:
    return [dict(num_leaves=nl, min_data_in_leaf=md, lambda_l2=l2, **REG_BASE)
            for nl in (3, 4, 6) for md in (100, 200, 400) for l2 in (10.0, 50.0)]


def tune_1se(fit_rows: pd.DataFrame, target: str) -> tuple[dict, int, pd.DataFrame]:
    """Most regularised config whose 2024 MSE is within one slate-clustered SE of the best one."""
    tr, va = fit_rows[fit_rows["season"].isin(TRAIN)], fit_rows[fit_rows["season"].isin(VAL)]
    rows, sq = [], []
    for cfg in reg_grid():
        b, sc = fit_es_booster(tr[FEATURES], tr[target], va[FEATURES], va[target], cfg, SEEDS_REG[0])
        e = (b.predict(va[FEATURES], num_iteration=b.best_iteration) - va[target].to_numpy()) ** 2
        sq.append(pd.Series(e, index=va.index).groupby(va["slate"]).sum())
        rows.append({k: cfg[k] for k in ("num_leaves", "min_data_in_leaf", "lambda_l2")} | {"iters": b.best_iteration, "val_mse": sc})
    g = pd.DataFrame(rows)
    best = int(g["val_mse"].idxmin())
    n = len(va)
    g["d_mse"] = [float((sq[k] - sq[best]).sum() / n) for k in range(len(g))]
    g["se"] = [float(np.sqrt(len(sq[k]) * (sq[k] - sq[best]).var(ddof=1)) / n) if k != best else 0.0 for k in range(len(g))]
    order = g.sort_values(["num_leaves", "min_data_in_leaf", "lambda_l2"], ascending=[True, False, False])
    pick = order[order["d_mse"] <= order["se"]].index[0]
    cfg = dict(num_leaves=int(g.loc[pick, "num_leaves"]), min_data_in_leaf=int(g.loc[pick, "min_data_in_leaf"]),
               lambda_l2=float(g.loc[pick, "lambda_l2"]), **REG_BASE)
    return cfg, int(g.loc[pick, "iters"]), g


def run_shrunk(d: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series, dict]:
    """resid_reg = 10-seed bagged regularised residual model; resid_shr = its correction shrunk per player
    toward the position mean by tau2 / (tau2 + v), v = max(slate-bootstrap variance, season-jackknife
    variance) of the correction, tau2 = per-position empirical-Bayes signal variance."""
    fit_rows = d[d["pool_pre"] & ~d["gated"]]
    cfg, iters, g = tune_1se(fit_rows, "resid")
    log(f"resid_reg: 1-SE pick {cfg} iters={iters}; grid:\n{g.sort_values('val_mse').head(6).to_string(index=False)}")
    reg, shr, sd = (pd.Series(np.nan, index=d.index) for _ in range(3))
    blocks = [([t for t in TRAIN if t != s], d["season"] == s) for s in TRAIN] + \
             [(TRAIN, d["season"].isin(VAL)), (TRAIN + VAL, d["season"].isin(TEST))]
    rng = np.random.default_rng(5)
    for T, mask in blocks:
        tr = fit_rows[fit_rows["season"].isin(T)]
        X = d.loc[mask, FEATURES]
        m = np.mean([fit(tr[FEATURES], tr["resid"], cfg, iters, s).predict(X) for s in SEEDS_REG], axis=0)
        by_slate = dict(tr.groupby("slate").indices)
        keys = list(by_slate)
        boot = []
        for b in range(N_BOOT):
            idx = np.concatenate([by_slate[k] for k in rng.choice(keys, len(keys))])
            t2 = tr.iloc[idx]
            boot.append(fit(t2[FEATURES], t2["resid"], cfg, iters, SEEDS_REG[b % len(SEEDS_REG)]).predict(X))
        v = np.var(boot, axis=0, ddof=1)
        if len(T) >= 3:
            jk = np.array([fit(tr.loc[tr["season"] != s, FEATURES], tr.loc[tr["season"] != s, "resid"], cfg, iters,
                               SEEDS_REG[0]).predict(X) for s in T])
            v = np.maximum(v, (len(T) - 1) / len(T) * ((jk - jk.mean(axis=0)) ** 2).sum(axis=0))
        pos = d.loc[mask, "pos"].astype(str).to_numpy()
        live = (d.loc[mask, "pool_pre"] & ~d.loc[mask, "gated"]).to_numpy()
        out = m.copy()
        for p in np.unique(pos):
            k = pos == p
            ref = k & live
            mu = m[ref].mean()
            tau2 = max(m[ref].var() - v[ref].mean(), 0.0)
            out[k] = mu + (m[k] - mu) * tau2 / (tau2 + v[k] + 1e-9)
        reg[mask], shr[mask], sd[mask] = m, out, np.sqrt(v)
        log(f"  block train={T}: corr sd {m[live].std():.2f}, boot/jk sd {np.sqrt(v[live]).mean():.2f}, "
            f"shrunk sd {out[live].std():.2f}")
    return reg, shr, sd, {"cfg": cfg, "iters": iters, "grid": g}


def run_quantile(d: pd.DataFrame, alpha: float) -> pd.Series:
    fit_rows = d[d["pool_pre"] & ~d["gated"]]
    tr, va = fit_rows[fit_rows["season"].isin(TRAIN)], fit_rows[fit_rows["season"].isin(VAL)]
    best = None
    for cfg in (dict(num_leaves=4, min_data_in_leaf=100, lambda_l2=10.0), dict(num_leaves=8, min_data_in_leaf=100, lambda_l2=10.0),
                dict(num_leaves=8, min_data_in_leaf=40, lambda_l2=1.0)):
        it, sc = fit_es(tr[FEATURES], tr["actual"], va[FEATURES], va["actual"], cfg, SEEDS[0], "quantile", alpha)
        if best is None or sc < best[2]:
            best = (cfg, it, sc)
    cfg, it, sc = best
    log(f"q{int(alpha * 100)}: {cfg} iters={it} val_pinball={sc:.3f}")
    out = pd.Series(np.nan, index=d.index)
    for s in TRAIN:
        trs = fit_rows[fit_rows["season"].isin([t for t in TRAIN if t != s])]
        out[d["season"] == s] = predict_avg(trs[FEATURES], trs["actual"], d.loc[d["season"] == s, FEATURES], cfg, it, "quantile", alpha)
    out[d["season"].isin(VAL)] = predict_avg(tr[FEATURES], tr["actual"], d.loc[d["season"].isin(VAL), FEATURES], cfg, it, "quantile", alpha)
    tr2 = fit_rows[fit_rows["season"].isin(TRAIN + VAL)]
    out[d["season"].isin(TEST)] = predict_avg(tr2[FEATURES], tr2["actual"], d.loc[d["season"].isin(TEST), FEATURES], cfg, it, "quantile", alpha)
    out[d["gated"]] = 0.0
    return out


def lincal(d: pd.DataFrame) -> pd.Series:
    """Control: per-position OLS actual ~ a + b * engine (gated rows excluded / set to 0), same fit schedule."""
    out = pd.Series(np.nan, index=d.index)
    fr = d[d["pool_pre"] & ~d["gated"]]

    def apply(train_seasons, mask):
        for p, g in fr[fr["season"].isin(train_seasons)].groupby("pos", observed=True):
            b, a = np.polyfit(g["engine"], g["actual"], 1)
            m = mask & (d["pos"] == p)
            out[m] = a + b * d.loc[m, "engine"]
    for s in TRAIN:
        apply([t for t in TRAIN if t != s], d["season"] == s)
    apply(TRAIN, d["season"].isin(VAL))
    apply(TRAIN + VAL, d["season"].isin(TEST))
    out[d["gated"]] = 0.0
    return out


# ------------------------------------------------------------------------------------- evaluation

def _eval_one(args):
    g, cols = args
    g = g.reset_index(drop=True).copy()
    g["pos"] = g["pos"].astype(str).map(lambda s: set(s.split(",")))
    r = evaluate_slate(g, cols, Rules("nfl"))
    return {"slate": g["slate"].iloc[0], "season": int(g["season"].iloc[0]), **r}


def evaluate(d: pd.DataFrame, cols: list[str], label: str) -> pd.DataFrame:
    keep = ["slate", "season", "pos", "salary", "game", "actual", *cols]
    items = [(g[keep], cols) for _, g in d.groupby("slate")]
    with ProcessPoolExecutor(WORKERS) as ex:
        res = pd.DataFrame(list(ex.map(_eval_one, items, chunksize=2)))
    res["pool"] = label
    return res


def lineup_report(res: pd.DataFrame, cols: list[str], seasons: list[int], title: str) -> list[str]:
    r = res[res["season"].isin(seasons)]
    L = [f"\n== {title}: seasons {seasons}, {len(r)} slates ==",
         f"{'column':26s} {'%opt':>7s} {'pts':>7s} {'proj':>7s} {'ovl':>5s}   vs engine [95% CI]            vs eng_ina [95% CI]"]
    for c in cols:
        line = (f"{c:26s} {100 * r[f'{c}:pct'].mean():7.2f} {r[f'{c}:pts'].mean():7.1f} {r[f'{c}:proj'].mean():7.1f} "
                f"{r[f'{c}:overlap'].mean():5.2f}")
        if c != "engine":
            m, lo, hi = paired_bootstrap(100 * r[f"{c}:pct"].to_numpy(), 100 * r["engine:pct"].to_numpy())
            line += f"   {m:+6.2f} [{lo:+6.2f}, {hi:+6.2f}]"
        if c not in ("engine", "nflgbm_eng_ina") and "nflgbm_eng_ina:pct" in r:
            m, lo, hi = paired_bootstrap(100 * r[f"{c}:pct"].to_numpy(), 100 * r["nflgbm_eng_ina:pct"].to_numpy())
            line += f"   {m:+6.2f} [{lo:+6.2f}, {hi:+6.2f}]"
        L.append(line)
    L.append(f"{'optimal':26s} {'':7s} {r['optimal'].mean():7.1f}")
    by = r.groupby("season")[[f"{c}:pct" for c in cols]].mean().mul(100).round(2)
    by.columns = [c.replace(":pct", "") for c in by.columns]
    by["n"] = r.groupby("season").size()
    L.append(by.T.to_string())
    return L


def player_report(d: pd.DataFrame, cols: list[str]) -> list[str]:
    t = d[d["season"].isin(TEST) & d["pool_pre"]]
    L = [f"\n== player level, pool_pre test rows (2025-2026), n={len(t)} ==",
         f"{'column':26s} {'MAE':>6s} {'RMSE':>6s} {'bias':>6s}   MAE-engine [95% slate-cluster CI]"]
    slates = t["slate"].unique()
    rng = np.random.default_rng(0)
    idx = {s: np.flatnonzero(t["slate"].to_numpy() == s) for s in slates}
    boots = [np.concatenate([idx[s] for s in rng.choice(slates, len(slates))]) for _ in range(2000)]
    ae_eng = (t["engine"] - t["actual"]).abs().to_numpy()
    for c in cols:
        e = (t[c].fillna(0) - t["actual"]).to_numpy()
        line = f"{c:26s} {np.abs(e).mean():6.3f} {np.sqrt((e ** 2).mean()):6.3f} {e.mean():+6.2f}"
        if c != "engine":
            diff = np.abs(e) - ae_eng
            bs = [diff[b].mean() for b in boots]
            line += f"   {diff.mean():+6.3f} [{np.percentile(bs, 2.5):+.3f}, {np.percentile(bs, 97.5):+.3f}]"
        L.append(line)
    L.append("MAE by position:")
    L.append(t.groupby("pos", observed=True).apply(
        lambda g: pd.Series({c: (g[c].fillna(0) - g["actual"]).abs().mean() for c in cols}), include_groups=False).round(3).to_string())
    return L


def quantile_report(d: pd.DataFrame) -> list[str]:
    t = d[d["season"].isin(TEST) & d["pool_pre"]]
    y = t["actual"].to_numpy()

    def pin(q, a):
        e = y - q
        return np.mean(np.maximum(a * e, (a - 1) * e))
    L = ["\n== quantiles, pool_pre test rows (engine p10/p90 = played distribution, not unconditional) =="]
    for name, lo, hi in (("engine p10/p90", t["p10"], t["p90"]), ("nflgbm q10/q90", t["nflgbm_q10"], t["nflgbm_q90"])):
        lo, hi = lo.to_numpy(), hi.to_numpy()
        L.append(f"{name:16s} below lo {np.mean(y < lo):.3f}  above hi {np.mean(y > hi):.3f}  "
                 f"pinball10 {pin(lo, 0.1):.3f}  pinball90 {pin(hi, 0.9):.3f}")
    return L


# ------------------------------------------------------------------------------------------- main

def main() -> None:
    d = load()
    pre = d["pool_pre"]
    log(f"{len(d)} rows, pool_pre {pre.sum()}, gated in pool_pre {int((pre & d['gated']).sum())} "
        f"(actual mean {d.loc[pre & d['gated'], 'actual'].mean():.2f})")
    d["resid"] = d["actual"] - d["engine"]
    d["nflgbm_eng_ina"] = np.where(d["gated"], 0.0, d["engine"])
    d["nflgbm_lincal"] = lincal(d)

    infos = {}
    dir_p, dir_s, infos["direct"] = run_model(d, "actual", True, FEATURES, "direct")
    res_p, res_s, infos["resid"] = run_model(d, "resid", False, FEATURES, "resid")
    d["nflgbm_direct"] = np.where(d["gated"], 0.0, dir_p)
    d["nflgbm_direct_s"] = np.where(d["gated"], 0.0, dir_s)
    d["_resid_hat"], d["_resid_hat_s"] = res_p, res_s
    d["nflgbm_resid"] = np.where(d["gated"], 0.0, d["engine"] + res_p)
    d["nflgbm_resid_s"] = np.where(d["gated"], 0.0, d["engine"] + res_s)

    # no-INA sensitivity: no availability gate, no status information (status was not a feature anyway;
    # gated rows stay in the training data)
    dn = d.copy()
    dn["gated"] = False
    rn, _, infos["resid_noina"] = run_model(dn, "resid", False, FEATURES, "resid_noina")
    d["nflgbm_resid_noina"] = d["engine"] + rn

    # blend weight on 2024 (validation, out-of-sample predictions from the 2021-23 fit)
    ws = np.round(np.arange(0, 1.01, 0.1), 2)
    v = d[d["season"].isin(VAL) & pre].copy()
    for w in ws:
        v[f"w{w}"] = np.where(v["gated"], 0.0, v["engine"] + w * v["_resid_hat"])
    vres = evaluate(v, [f"w{w}" for w in ws], "val")
    curve = {w: 100 * vres[f"w{w}:pct"].mean() for w in ws}
    mse = {w: float(((v.loc[~v["gated"], "engine"] + w * v.loc[~v["gated"], "_resid_hat"] - v.loc[~v["gated"], "actual"]) ** 2).mean()) for w in ws}
    w_best = max(ws, key=lambda w: (round(curve[w], 6), -abs(w - 0.5)))
    log("2024 blend curve (w: pct, mse): " + ", ".join(f"{w}: {curve[w]:.2f}/{mse[w]:.2f}" for w in ws) + f" -> w={w_best}")
    d["nflgbm_blend"] = np.where(d["gated"], 0.0, d["engine"] + w_best * d["_resid_hat"])
    d["nflgbm_blend_s"] = np.where(d["gated"], 0.0, d["engine"] + w_best * d["_resid_hat_s"])

    d["nflgbm_q10"] = run_quantile(d, 0.1)
    d["nflgbm_q90"] = run_quantile(d, 0.9)

    reg, shr, sd, infos["resid_reg"] = run_shrunk(d)
    d["nflgbm_resid_reg"] = np.where(d["gated"], 0.0, d["engine"] + reg)
    d["nflgbm_resid_shr"] = np.where(d["gated"], 0.0, d["engine"] + shr)
    d["nflgbm_resid_sd"] = sd
    # blend weight for the regularised correction, chosen on all non-test slates (2021-23 out-of-fold + 2024)
    v = d[d["season"].isin(TRAIN + VAL) & pre].copy()
    for w in ws:
        v[f"w{w}"] = np.where(v["gated"], 0.0, v["engine"] + w * reg[v.index])
    vres2 = evaluate(v, [f"w{w}" for w in ws], "val")
    curve2 = {w: 100 * vres2[f"w{w}:pct"].mean() for w in ws}
    curve2_24 = {w: 100 * vres2.loc[vres2["season"].isin(VAL), f"w{w}:pct"].mean() for w in ws}
    w_reg = max(ws, key=lambda w: (round(curve2[w], 6), -abs(w - 0.5)))
    log("2021-24 blend curve for resid_reg (w: pct all / 2024): " +
        ", ".join(f"{w}: {curve2[w]:.2f}/{curve2_24[w]:.2f}" for w in ws) + f" -> w={w_reg}")
    d["nflgbm_blend_reg"] = np.where(d["gated"], 0.0, d["engine"] + w_reg * reg)

    out_cols = ["nflgbm_direct", "nflgbm_resid", "nflgbm_blend", "nflgbm_direct_s", "nflgbm_resid_s", "nflgbm_blend_s",
                "nflgbm_resid_noina", "nflgbm_resid_reg", "nflgbm_resid_shr", "nflgbm_blend_reg", "nflgbm_resid_sd",
                "nflgbm_q10", "nflgbm_q90", "nflgbm_eng_ina", "nflgbm_lincal"]
    o = d[["slate", "player_id", "pool_pre", *out_cols]].copy()
    o["nflgbm_blend_w"] = w_best
    o["nflgbm_blend_reg_w"] = w_reg
    o.to_parquet(OUT, index=False)
    log(f"wrote {OUT}")

    cols = ["engine", "base_std", "nflgbm_eng_ina", "nflgbm_lincal", "nflgbm_direct", "nflgbm_resid", "nflgbm_blend",
            "nflgbm_direct_s", "nflgbm_resid_s", "nflgbm_blend_s", "nflgbm_resid_noina", "nflgbm_resid_reg",
            "nflgbm_resid_shr", "nflgbm_blend_reg"]
    r_pre = evaluate(d[pre], cols, "pool_pre")
    log("pool_pre evaluated")
    r_full = evaluate(d, cols, "full")
    log("full table evaluated")
    res = pd.concat([r_pre, r_full], ignore_index=True)
    res.to_csv(RES, index=False)

    L = [f"blend w (2024, pct of optimal) = {w_best}; 2024 curve: " + ", ".join(f"{w}:{curve[w]:.2f}" for w in ws),
         f"blend_reg w (2021-24 non-test slates) = {w_reg}; curve: " + ", ".join(f"{w}:{curve2[w]:.2f}" for w in ws)]
    for k, inf in infos.items():
        L.append(f"{k}: cfg {inf['cfg']} iters {inf['iters']}" + ("; top gain: " + ", ".join(
            f"{f} {v:.2f}" for f, v in inf["importance"].head(10).items()) if "importance" in inf else ""))
    L += lineup_report(r_pre, cols, TEST, "POOL_PRE test")
    L += lineup_report(r_pre, cols, VAL, "POOL_PRE validation")
    L += lineup_report(r_pre, cols, TRAIN, "POOL_PRE train seasons (OOF)")
    L += lineup_report(r_full, cols, TEST, "FULL TABLE test")
    L += lineup_report(r_full, cols, TRAIN + VAL + TEST, "FULL TABLE all seasons")
    L += player_report(d, cols)
    L += quantile_report(d)
    print("\n".join(L))


if __name__ == "__main__":
    main()
