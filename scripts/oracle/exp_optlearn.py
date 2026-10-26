"""Experiment "optlearn": learn from REAL ex-post optimal lineups and real upside instead of only expected points.

    python3 scripts/oracle/exp_optlearn.py build     # labels + shipped OOF + stacker models -> exp_optlearn.parquet
    python3 scripts/oracle/exp_optlearn.py val       # grid of rules on VALIDATION nights (2026, slate < 2026-01-01)
    python3 scripts/oracle/exp_optlearn.py test      # ONE run on TEST nights (2026 >= 2026-01-01 + 2027)

Training: season 2025 only. For every 2025 night the ex-post optimum (dfs.oracle.best_lineup on actual) gives the
label in_opt. The projection input on 2025 rows is the shipped minutes main tier refit out-of-fold by month
(nm.fit on MAIN; points = P(plays) x clip(minutes, 0, 48) x rate), so it is comparable to shipped_main on 2026-27
(exp_shipped2.parquet, trained on all of 2025). Stacker models (LightGBM, 2 threads) on pre-game features
(MAIN + salary, projection, projection per $1000, position, rate history, slate-relative ranks), OOF by month on
2025 and fit on all of 2025 for 2026-27:

- popt:  P(player-night is in the ex-post optimum)
- q75, q90: quantile regression of DK points
- pv6, pv75: P(points >= 6x / 7.5x salary/1000)
- mean_gbm (own idea): direct DK-points regressor (L2) stacked on the shipped projection and its components
- rate_gbm (own idea): per-minute DK rate regressor (minutes-weighted, rows with >= 6 minutes), recombined as
  ship_rate = P(plays) x minutes x rate_gbm.
"""
from __future__ import annotations

import os
import sys
import time

for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "oracle"))
from dfs.oracle import Rules, best_lineup, paired_bootstrap  # noqa: E402
from dfs.projections import nba_minutes as nm  # noqa: E402

SCR = Path(os.environ.get("DFS_EXP_SCRATCH", "data/cache/oracle/scratch"))
OUT = SCR / "optlearn"
CACHE = ROOT / "data" / "cache" / "oracle"
THREADS = 2
KEY = ["slate", "athlete_id"]
GBM = dict(learning_rate=0.03, num_leaves=15, min_child_samples=100, subsample=0.8, subsample_freq=1,
           colsample_bytree=0.8, reg_lambda=1.0, n_estimators=300, num_threads=THREADS, verbose=-1)


def _opt_night(item):
    sid, g = item
    g = g.reset_index(drop=True).copy()
    g["pos"] = g["pos"].map(lambda s: set(s.split(",")) if isinstance(s, str) else s)
    lu = best_lineup(g, g["actual"].to_numpy(dtype=float), Rules("nba"))
    if lu is None:
        return pd.DataFrame({"slate": [], "athlete_id": [], "in_opt": []})
    y = np.zeros(len(g), dtype=int)
    y[lu] = 1
    return pd.DataFrame({"slate": sid, "athlete_id": g["athlete_id"].to_numpy(), "in_opt": y})


def add_context(d: pd.DataFrame) -> pd.DataFrame:
    """Slate-relative pre-game features (from the projection and salary only)."""
    d = d.copy()
    d["value"] = d["proj"] / (d["salary"] / 1000.0)
    for p in ("PG", "SG", "SF", "PF", "C"):
        d[f"is_{p}"] = d["pos"].map(lambda s, p=p: float(p in str(s).split(",")))
    d["grp"] = d["pos"].map(nm._group)
    g = d.groupby("slate")
    d["n_games"] = g["game"].transform("nunique")
    d["n_pool"] = g["athlete_id"].transform("size")
    d["proj_rank"] = g["proj"].rank(ascending=False, method="min")
    d["value_rank"] = g["value"].rank(ascending=False, method="min")
    d["proj_z"] = (d["proj"] - g["proj"].transform("mean")) / g["proj"].transform("std")
    d["value_z"] = (d["value"] - g["value"].transform("mean")) / g["value"].transform("std")
    d["value_gap10"] = d["value"] - g["value"].transform(lambda v: np.sort(v.to_numpy())[::-1][min(9, len(v) - 1)])
    d["proj_gap8"] = d["proj"] - g["proj"].transform(lambda v: np.sort(v.to_numpy())[::-1][min(7, len(v) - 1)])
    gp = d.groupby(["slate", "grp"])
    d["value_rank_grp"] = gp["value"].rank(ascending=False, method="min")
    d["proj_rank_grp"] = gp["proj"].rank(ascending=False, method="min")
    gt = d.groupby(["slate", "team"])
    d["team_proj_sum"] = gt["proj"].transform("sum")
    d["proj_rank_team"] = gt["proj"].rank(ascending=False, method="min")
    d["game_proj_sum"] = d.groupby(["slate", "game"])["proj"].transform("sum")
    d["sal_rank"] = g["salary"].rank(ascending=False, method="min")
    return d


CTX = ["salary", "proj", "p_play", "min_pred", "value", "own_rate5", "own_rate10", "own_rate_cur", "opp_allowed", "ppg",
       "l5", "ppg_prev", "dk_avg", "engine", "p10", "p90", "is_PG", "is_SG", "is_SF", "is_PF", "is_C", "n_games", "n_pool",
       "proj_rank", "value_rank", "proj_z", "value_z", "value_gap10", "proj_gap8", "value_rank_grp", "proj_rank_grp",
       "team_proj_sum", "proj_rank_team", "game_proj_sum", "sal_rank"]
FEATS = list(dict.fromkeys(nm.MAIN + CTX))


def models(tr: pd.DataFrame, te: pd.DataFrame) -> dict:
    """Fit every stacker model on ``tr`` and predict ``te``."""
    import lightgbm as lgb
    X, Xt, y = tr[FEATS], te[FEATS], tr["actual"].to_numpy(dtype=float)
    out = {}
    out["popt"] = lgb.LGBMClassifier(**dict(GBM, objective="binary")).fit(X, tr["in_opt"]).predict_proba(Xt)[:, 1]
    for a in (0.75, 0.9):
        out[f"q{int(a * 100)}"] = lgb.LGBMRegressor(**dict(GBM, objective="quantile", alpha=a)).fit(X, y).predict(Xt)
    for t in (6.0, 7.5):
        lab = (y >= t * tr["salary"].to_numpy() / 1000.0).astype(int)
        out[f"pv{str(t).replace('.', '').rstrip('0')}"] = lgb.LGBMClassifier(**dict(GBM, objective="binary")).fit(
            X, lab).predict_proba(Xt)[:, 1]
    out["mean_gbm"] = lgb.LGBMRegressor(**dict(GBM, objective="regression")).fit(X, y).predict(Xt)
    pl = tr[tr["minutes_actual"] >= 6]
    rr = lgb.LGBMRegressor(**dict(GBM, objective="regression")).fit(
        pl[FEATS], pl["actual"] / pl["minutes_actual"], sample_weight=pl["minutes_actual"])
    out["rate_gbm"] = rr.predict(Xt)
    return out


def build():
    t0 = time.time()
    f = pd.read_parquet(SCR / "feats_v2.parquet").drop_duplicates(KEY).reset_index(drop=True)
    f["month"] = f["slate"].str[:7]
    tr = f[f["season"] == 2025].copy()
    te = f[f["season"].isin([2026, 2027])].copy()
    # 1) shipped main tier: OOF by month on 2025, full-2025 fit for 2026-27 (check vs exp_shipped2)
    tr["p_play"], tr["min_pred"] = np.nan, np.nan
    for mo in sorted(tr["month"].unique()):
        m = tr["month"] == mo
        c, r = nm.fit(tr[~m], nm.MAIN, nm.ROUNDS["main"])
        tr.loc[m, "p_play"] = c.predict_proba(tr.loc[m, nm.MAIN])[:, 1]
        tr.loc[m, "min_pred"] = np.clip(r.predict(tr.loc[m, nm.MAIN]), 0, 48)
    c, r = nm.fit(tr, nm.MAIN, nm.ROUNDS["main"])
    te["p_play"] = c.predict_proba(te[nm.MAIN])[:, 1]
    te["min_pred"] = np.clip(r.predict(te[nm.MAIN]), 0, 48)
    tr["proj"] = tr["p_play"] * tr["min_pred"] * tr["rate"]
    te["proj_refit"] = te["p_play"] * te["min_pred"] * te["rate"]
    sh = pd.read_parquet(CACHE / "exp_shipped2.parquet").drop_duplicates(KEY)
    te = te.merge(sh[KEY + ["shipped_main"]], on=KEY, how="left")
    print("refit vs shipped_main: max abs diff", float((te["proj_refit"] - te["shipped_main"]).abs().max()),
          "corr", float(te[["proj_refit", "shipped_main"]].corr().iloc[0, 1]), "missing", int(te["shipped_main"].isna().sum()))
    te["proj"] = te["shipped_main"].fillna(te["proj_refit"])
    print(f"shipped done {time.time() - t0:.0f}s", flush=True)
    # 2) ex-post optimum labels on 2025
    with ProcessPoolExecutor(2) as ex:
        labs = pd.concat(list(ex.map(_opt_night, tr.groupby("slate"), chunksize=4)))
    tr = tr.merge(labs, on=KEY, how="left")
    tr["in_opt"] = tr["in_opt"].fillna(0).astype(int)
    print(f"labels done {time.time() - t0:.0f}s, in_opt rows {tr['in_opt'].sum()} on {tr['slate'].nunique()} nights",
          flush=True)
    tr, te = add_context(tr), add_context(te)
    # 3) stacker models: OOF by month on 2025, full-2025 for 2026-27
    names = None
    for mo in sorted(tr["month"].unique()):
        m = (tr["month"] == mo).to_numpy()
        o = models(tr[~m], tr[m])
        for k, v in o.items():
            tr.loc[m, k] = v
        names = list(o)
        print(f"  oof {mo} {time.time() - t0:.0f}s", flush=True)
    o = models(tr, te)
    for k, v in o.items():
        te[k] = v
    keep = KEY + ["season", "month", "proj", "p_play", "min_pred", "rate", "salary", "actual", "minutes_actual"] + names
    tr["shipped_main"] = tr["proj"]
    allp = pd.concat([tr[keep + ["shipped_main", "in_opt"]], te[keep + ["shipped_main"]]], ignore_index=True)
    allp.to_parquet(CACHE / "exp_optlearn.parquet")
    print(f"build done {time.time() - t0:.0f}s", flush=True)


# ---------------------------------------------------------------- fast exact evaluation

POSB = {"PG": 1, "SG": 2, "SF": 4, "PF": 8, "C": 16}


SLOTM = np.array([1, 2, 4, 8, 16, 3, 12, 31])  # PG SG SF PF C G F UTIL as position bitmasks


def prune(score: np.ndarray, sal: np.ndarray, mask: np.ndarray, game: np.ndarray) -> np.ndarray:
    """Exact dominance prune: player i is dropped if its dominators D (salary <=, score >=, positions a superset;
    strict order with salary/index tie-breaks) number at least K = the roster slots D's positions can fill, and D
    spans 2+ games. Then some optimal lineup survives (swap i for a dominator outside the lineup)."""
    n = len(score)
    idx = np.arange(n)
    better = (score[None, :] > score[:, None]) | ((score[None, :] == score[:, None]) & (
        (sal[None, :] < sal[:, None]) | ((sal[None, :] == sal[:, None]) & (idx[None, :] < idx[:, None]))))
    dom = better & (sal[None, :] <= sal[:, None]) & ((mask[None, :] & mask[:, None]) == mask[:, None])
    np.fill_diagonal(dom, False)
    cnt = dom.sum(1)
    union = np.zeros(n, dtype=int)
    for b in (1, 2, 4, 8, 16):
        union |= np.where((dom & ((mask[None, :] & b) > 0)).any(1), b, 0)
    k = ((union[:, None] & SLOTM[None, :]) > 0).sum(1)
    gmin = np.where(dom, game[None, :], 10 ** 6).min(1)
    gmax = np.where(dom, game[None, :], -1).max(1)
    return ~((cnt >= k) & (cnt > 0) & (gmax > gmin))


def fast_lineup(g: pd.DataFrame, score: np.ndarray, sal, mask, game) -> list[int] | None:
    keep = np.flatnonzero(prune(score, sal, mask, game))
    sub = g.iloc[keep].reset_index(drop=True)
    lu = best_lineup(sub, score[keep], Rules("nba"))
    return None if lu is None else sorted(int(keep[i]) for i in lu)


def _eval_night(item, cols):
    sid, g = item
    g = g.reset_index(drop=True).copy()
    g["pos"] = g["pos"].map(lambda s: set(s.split(",")) if isinstance(s, str) else s)
    sal = g["salary"].to_numpy(dtype=float)
    mask = np.array([sum(POSB[p] for p in s) for s in g["pos"]])
    game = pd.factorize(g["game"])[0]
    act = g["actual"].to_numpy(dtype=float)
    opt = fast_lineup(g, act, sal, mask, game)
    if opt is None:
        return {}
    best = float(act[opt].sum())
    out = {"slate": sid, "season": int(g["season"].iloc[0]), "optimal": best}
    for c in cols:
        sc = g[c].fillna(0.0).to_numpy(dtype=float)
        lu = fast_lineup(g, sc, sal, mask, game)
        if lu is None:
            continue
        out[f"{c}:pts"] = float(act[lu].sum())
        out[f"{c}:pct"] = out[f"{c}:pts"] / best
        out[f"{c}:overlap"] = len(set(lu) & set(opt))
        out[f"{c}:proj"] = float(g["shipped_main"].fillna(0).to_numpy()[lu].sum())
    return out


def run_eval(df: pd.DataFrame, cols: list[str], workers: int = 2) -> pd.DataFrame:
    from functools import partial
    with ProcessPoolExecutor(workers) as ex:
        res = list(ex.map(partial(_eval_night, cols=cols), df.groupby("slate"), chunksize=2))
    return pd.DataFrame([r for r in res if "optimal" in r])


def frame(seasons) -> pd.DataFrame:
    s = pd.read_parquet(CACHE / "nba_slates.parquet").drop_duplicates(KEY)
    s = s[s["season"].isin(seasons)]
    p = pd.read_parquet(CACHE / "exp_optlearn.parquet").drop_duplicates(KEY)
    p = p.drop(columns=[c for c in ("season", "rate", "salary", "actual", "minutes_actual") if c in p.columns])
    d = s.merge(p, on=KEY, how="left")
    miss = d["shipped_main"].isna().mean()
    print(f"rows {len(d)}, missing predictions {100 * miss:.2f}%", flush=True)
    return d


LAMS = [2, 5, 10, 20, 40]
KAPS = [0.1, 0.25, 0.5, 0.75, 1.0]
WS = [0.25, 0.5, 0.75, 1.0]


def add_rules(d: pd.DataFrame) -> list[str]:
    sm = d["shipped_main"].fillna(0.0)
    d["ship_rate"] = d["p_play"] * d["min_pred"] * d["rate_gbm"]
    cols = ["engine", "shipped_main", "r1_popt", "r4_pv6", "r4_pv75"]
    d["r1_popt"], d["r4_pv6"], d["r4_pv75"] = d["popt"], d["pv6"], d["pv75"]
    for l in LAMS:
        d[f"r2_lam{l}"] = sm + l * d["popt"]
        cols.append(f"r2_lam{l}")
    for q in ("q90", "q75"):
        for k in KAPS:
            d[f"r3_{q}_k{k}"] = sm + k * (d[q] - sm)
            cols.append(f"r3_{q}_k{k}")
    for base in ("mean_gbm", "ship_rate"):
        for w in WS:
            d[f"r5_{base}_w{w}"] = (1 - w) * sm + w * d[base]
            cols.append(f"r5_{base}_w{w}")
    for l in (5, 10, 20):
        d[f"r5_rate_lam{l}"] = d["ship_rate"] + l * d["popt"]
        cols.append(f"r5_rate_lam{l}")
    return cols


def report(res: pd.DataFrame, cols: list[str], base: str = "shipped_main") -> pd.DataFrame:
    rows = []
    for c in cols:
        r = {"column": c, "nights": int(res[f"{c}:pct"].notna().sum()), "pct": 100 * res[f"{c}:pct"].mean(),
             "pts": res[f"{c}:pts"].mean(), "overlap": res[f"{c}:overlap"].mean(), "proj_shipped": res[f"{c}:proj"].mean()}
        for b in (base, "engine"):
            m, lo, hi = paired_bootstrap(100 * res[f"{c}:pct"], 100 * res[f"{b}:pct"])
            r[f"vs_{b}"], r[f"vs_{b}_lo"], r[f"vs_{b}_hi"] = m, lo, hi
        for s, g in res.groupby("season"):
            r[f"pct_{s}"] = 100 * g[f"{c}:pct"].mean()
        rows.append(r)
    return pd.DataFrame(rows)


def val():
    d = frame([2026])
    d = d[d["slate"] < "2026-01-01"].copy()
    cols = add_rules(d)
    t0 = time.time()
    res = run_eval(d, cols)
    res.to_csv(CACHE / "res_optlearn_val.csv", index=False)
    rep = report(res, cols)
    rep.to_csv(OUT / "val_report.csv", index=False)
    pd.set_option("display.width", 250)
    print(rep.round(2).to_string(), f"\n{time.time() - t0:.0f}s on {len(res)} nights")


def oof2025():
    """Secondary validation: 2025 nights with OOF stacker inputs (shipped_main here = OOF refit)."""
    d = frame([2025])
    cols = add_rules(d)
    res = run_eval(d, cols)
    res.to_csv(OUT / "res_oof2025.csv", index=False)
    rep = report(res, cols)
    rep.to_csv(OUT / "oof2025_report.csv", index=False)
    pd.set_option("display.width", 250)
    print(rep.round(2).to_string())


FAMILIES = {"r2": "r2_lam", "r3_q90": "r3_q90_k", "r3_q75": "r3_q75_k", "r5_mean": "r5_mean_gbm_w",
            "r5_rate": "r5_ship_rate_w", "r5_ratelam": "r5_rate_lam"}


def choose() -> dict:
    """Pre-registered: per family, the setting with the best pooled mean % of optimal over the validation nights
    (2026 before 2026-01-01) and the 2025 out-of-fold nights (all nights weighted equally)."""
    v = pd.read_csv(CACHE / "res_optlearn_val.csv")
    o = pd.read_csv(OUT / "res_oof2025.csv") if (OUT / "res_oof2025.csv").exists() else v.iloc[:0]
    pool = pd.concat([v, o], ignore_index=True)
    pick = {}
    for fam, pre in FAMILIES.items():
        cs = [c[:-4] for c in pool.columns if c.startswith(pre) and c.endswith(":pct")]
        sc = {c: pool[f"{c}:pct"].mean() for c in cs}
        pick[fam] = max(sc, key=sc.get)
        print(fam, {k: round(100 * x, 2) for k, x in sc.items()}, "->", pick[fam], flush=True)
    return pick


def mae_table(d: pd.DataFrame) -> pd.DataFrame:
    a = d["actual"]
    rows = []
    for c in ("engine", "shipped_main", "mean_gbm", "ship_rate", "q75", "q90"):
        e = d[c] - a
        rows.append({"col": c, "mae": e.abs().mean(), "bias": e.mean(), "rmse": float(np.sqrt((e ** 2).mean()))})
    return pd.DataFrame(rows)


def test():
    pick = choose()
    d = frame([2026, 2027])
    d = d[(d["season"] == 2027) | (d["slate"] >= "2026-01-01")].copy()
    add_rules(d)
    cols = ["engine", "shipped_main", "r1_popt", pick["r2"], pick["r3_q90"], pick["r3_q75"], "r4_pv6", "r4_pv75",
            pick["r5_mean"], pick["r5_rate"], pick["r5_ratelam"]]
    print("TEST columns (one run):", cols, flush=True)
    res = run_eval(d, cols)
    res.to_csv(CACHE / "res_optlearn.csv", index=False)
    rep = report(res, cols)
    rep.to_csv(OUT / "test_report.csv", index=False)
    pd.set_option("display.width", 250)
    print(rep.round(2).to_string(), f"\non {len(res)} nights")
    print(mae_table(d).round(3).to_string())
    for s_, g in d.groupby("season"):
        print("season", s_, mae_table(g).round(3).to_string())
    keep = KEY + ["season"] + cols[2:] + ["popt", "q75", "q90", "pv6", "pv75", "mean_gbm", "ship_rate"]
    d[list(dict.fromkeys(keep))].to_parquet(CACHE / "exp_optlearn_test.parquet")


if __name__ == "__main__":
    {"build": build, "val": val, "oof2025": oof2025, "test": test}[sys.argv[1]]()
