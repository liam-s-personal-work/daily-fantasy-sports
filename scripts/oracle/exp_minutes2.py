"""Experiment "minutes2": improve the shipped NBA minutes model (dfs/projections/nba_minutes.py).

    python3 scripts/oracle/exp_minutes2.py build      # feature table 2024-2027 (+ NEW2 features) -> scratch
    python3 scripts/oracle/exp_minutes2.py val        # fit every candidate (seeds 0,1,2), validation nights only
    python3 scripts/oracle/exp_minutes2.py val2       # stage 2 on validation: team renormalisation of the winner
    python3 scripts/oracle/exp_minutes2.py test PRIMARY,SECONDARY   # the single final test run (pre-registered choice)

Points = P(plays) x clip(minutes, 0, 48) x engine rate, as shipped. Candidates differ in training data (season
2025 only, as shipped, vs 2024+2025), features (MAIN vs MAIN + NEW2 from exp_minutes2_feats.py), LightGBM
settings, regressor sample weights, and a post-hoc team renormalisation of expected minutes toward 241
(xmin *= (241 / team sum) ** alpha). Validation = season 2026 nights before 2026-01-01; test = 2026 nights from
2026-01-01 plus all 2027 nights. Every setting is chosen on validation; test is run once.
"""
from __future__ import annotations

import os
import json
import sys
import time
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from evaluate import run  # noqa: E402
from exp_minutes2_feats import NEW2, add_features, load_extra  # noqa: E402

from dfs.data import nba  # noqa: E402
from dfs.oracle import paired_bootstrap  # noqa: E402
from dfs.projections import nba_minutes as nm  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SCR = Path(os.environ.get("DFS_EXP_SCRATCH", "data/cache/oracle/scratch")) / "minutes2"
FEATS_V2 = SCR.parent / "feats_v2.parquet"
FEATS24 = ROOT / "data/cache/oracle/exp_minutes2_feats2024.parquet"
ALL = SCR / "feats_all.parquet"
SLATES = ROOT / "data/cache/oracle/nba_slates.parquet"
OUT = ROOT / "data/cache/oracle"
SEEDS = (0, 1, 2)
WORKERS = 2

MAIN = list(nm.MAIN)
MAIN2 = MAIN + NEW2
CONFIGS = {
    "ship25": dict(feats=MAIN, train=(2025,), rounds=(300, 300)),          # shipped configuration, refit
    "ship2425": dict(feats=MAIN, train=(2024, 2025), rounds=(300, 300)),
    "new25": dict(feats=MAIN2, train=(2025,), rounds=(300, 300)),
    "new2425": dict(feats=MAIN2, train=(2024, 2025), rounds=(300, 300)),
    "new2425_long": dict(feats=MAIN2, train=(2024, 2025), rounds=(500, 500)),
    "new2425_reg": dict(feats=MAIN2, train=(2024, 2025), rounds=(450, 450),
                        over=dict(num_leaves=31, min_child_samples=200, reg_lambda=5.0, colsample_bytree=0.6)),
    "new2425_w": dict(feats=MAIN2, train=(2024, 2025), rounds=(300, 300), weight="rate"),
}


def build() -> None:
    f = pd.read_parquet(FEATS_V2)
    f24 = pd.read_parquet(FEATS24)
    f = pd.concat([f24[f.columns.intersection(f24.columns)], f], ignore_index=True)
    f = f.drop_duplicates(["slate", "athlete_id"]).reset_index(drop=True)
    box = nba.load_box([2023, 2024, 2025, 2026, 2027])
    ex = load_extra([2023, 2024, 2025, 2026, 2027])
    t = time.time()
    f = add_features(f, box, ex)
    f.to_parquet(ALL)
    print(f.groupby("season")["slate"].nunique().to_dict(), len(f), f"{time.time() - t:.0f}s", flush=True)


def masks(f: pd.DataFrame):
    val = (f["season"] == 2026) & (f["slate"] < "2026-01-01")
    test = ((f["season"] == 2026) & (f["slate"] >= "2026-01-01")) | (f["season"] == 2027)
    return val, test


def fit_predict(f: pd.DataFrame, cfg: dict, seed: int, target: pd.Series, feats_key: str = "feats"):
    feats = cfg[feats_key]
    tr = f[f["season"].isin(cfg["train"])]
    over = cfg.get("over", {})
    y = (tr["minutes_actual"] > 0).astype(int)
    c = lgb.LGBMClassifier(**dict(nm.CLF, **over, n_estimators=cfg["rounds"][0], random_state=seed)).fit(tr[feats], y)
    pl = tr[y == 1]
    w = None
    if cfg.get("weight") == "rate":
        w = pl["rate"].clip(0.3, 2.0).to_numpy()
        w = w / w.mean()
    r = lgb.LGBMRegressor(**dict(nm.REG, **over, n_estimators=cfg["rounds"][1], random_state=seed)).fit(
        pl[feats], pl["minutes_actual"], sample_weight=w)
    x = f.loc[target, feats]
    return c.predict_proba(x)[:, 1], np.clip(r.predict(x), 0.0, 48.0)


def renorm(d: pd.DataFrame, xmin: np.ndarray, alpha: float, total: float = 241.0) -> np.ndarray:
    if alpha == 0:
        return xmin
    s = pd.Series(xmin, index=d.index).groupby([d["slate"], d["team"]]).transform("sum").to_numpy()
    return xmin * (total / np.maximum(s, 1.0)) ** alpha


def slates_with(pred: pd.DataFrame) -> pd.DataFrame:
    s = pd.read_parquet(SLATES)
    ship = pd.read_parquet(OUT / "exp_shipped2.parquet").drop_duplicates(["slate", "athlete_id"])
    s = s.merge(ship, on=["slate", "athlete_id"], how="left")
    s = s.merge(pred.drop_duplicates(["slate", "athlete_id"]), on=["slate", "athlete_id"], how="left")
    return s


def minutes_err(d: pd.DataFrame, xmin: np.ndarray) -> tuple[float, float]:
    e = xmin - d["minutes_actual"].to_numpy()
    return float(np.abs(e).mean()), float(np.sqrt((e ** 2).mean()))


def stage_val(names: list[str], tag: str, alphas=(0.0,)) -> None:
    f = pd.read_parquet(ALL)
    val, _ = masks(f)
    d = f.loc[val, ["slate", "athlete_id", "team", "minutes_actual", "rate"]].copy()
    cols, rows = [], []
    for n in names:
        for sd in SEEDS:
            t = time.time()
            p, m = fit_predict(f, CONFIGS[n], sd, val)
            for a in alphas:
                xm = renorm(d, p * m, a)
                col = f"{n}_a{a:g}_s{sd}"
                d[col] = xm * d["rate"].to_numpy()
                mae, rmse = minutes_err(d, xm)
                rows.append(dict(cfg=n, alpha=a, seed=sd, col=col, mae=mae, rmse=rmse))
                cols.append(col)
            print(n, sd, f"{time.time() - t:.0f}s", flush=True)
        for a in alphas:  # seed bag: mean projection over the 3 seeds
            col = f"{n}_a{a:g}_bag"
            d[col] = d[[f"{n}_a{a:g}_s{sd}" for sd in SEEDS]].mean(1)
            xm = d[col].to_numpy() / d["rate"].replace(0, np.nan).to_numpy()
            mae, rmse = minutes_err(d[np.isfinite(xm)], xm[np.isfinite(xm)])
            rows.append(dict(cfg=n + "_bag", alpha=a, seed=-1, col=col, mae=mae, rmse=rmse))
            cols.append(col)
    s = slates_with(d.drop(columns=["team", "minutes_actual", "rate"]))
    s = s[(s["season"] == 2026) & (s["slate"] < "2026-01-01")]
    for c in cols:
        s[c] = s[c].fillna(0.0)
    allc = ["engine", "shipped_main"] + cols
    res = run(s, allc, "nba", workers=WORKERS)
    res.to_csv(OUT / f"res_minutes2_{tag}.csv", index=False)
    m = pd.DataFrame(rows)
    m["pct"] = [100 * res[f"{c}:pct"].mean() for c in m["col"]]
    m.to_csv(SCR / f"val_{tag}.csv", index=False)
    print(f"val nights {len(res)}; engine {100 * res['engine:pct'].mean():.2f} shipped_main "
          f"{100 * res['shipped_main:pct'].mean():.2f}")
    g = m.groupby(["cfg", "alpha"]).agg(pct=("pct", "mean"), pct_sd=("pct", "std"), mae=("mae", "mean"),
                                        rmse=("rmse", "mean")).reset_index()
    base = [f"ship25_a0_s{sd}" for sd in SEEDS]
    if all(f"{b}:pct" in res for b in base):
        bmean = res[[f"{b}:pct" for b in base]].mean(1) * 100
        for i, r in g.iterrows():
            cc = ([f"{r['cfg'][:-4]}_a{r['alpha']:g}_bag"] if r["cfg"].endswith("_bag")
                  else [f"{r['cfg']}_a{r['alpha']:g}_s{sd}" for sd in SEEDS])
            mm = res[[f"{c}:pct" for c in cc]].mean(1) * 100
            g.loc[i, "d_vs_ship25"], g.loc[i, "lo"], g.loc[i, "hi"] = paired_bootstrap(mm, bmean)
    print(g.to_string(index=False))


def stage_test(names: list[str]) -> None:
    """The single test run (pre-registered on validation): primary = names[0] (highest validation seed-mean),
    secondary = names[1:] (highest validation seed-bag), and the shipped refit ship25; 3 seeds each + seed bag."""
    f = pd.read_parquet(ALL)
    _, test = masks(f)
    d = f.loc[test, ["slate", "season", "athlete_id", "team", "minutes_actual", "rate"]].copy()
    keys = ["ship25"] + [n for n in names if n != "ship25"]
    cols = {k: [] for k in keys}
    for k in keys:
        for sd in SEEDS:
            t = time.time()
            p, m = fit_predict(f, CONFIGS[k], sd, test)
            d[f"m2_{k}_s{sd}"] = p * m * d["rate"].to_numpy()
            d[f"xmin_{k}_s{sd}"] = p * m
            cols[k].append(f"m2_{k}_s{sd}")
            print(k, sd, f"{time.time() - t:.0f}s", flush=True)
        d[f"m2_{k}_bag"] = d[cols[k]].mean(axis=1)
        d[f"xmin_{k}_bag"] = d[[c.replace("m2_", "xmin_") for c in cols[k]]].mean(axis=1)
    keep = d.drop(columns=["team", "minutes_actual", "rate", "season"])
    keep.to_parquet(OUT / "exp_minutes2.parquet")
    s = slates_with(keep)
    s = s[((s["season"] == 2026) & (s["slate"] >= "2026-01-01")) | (s["season"] == 2027)]
    sc = [c for c in keep.columns if c.startswith("m2_")]
    for c in sc:
        s[c] = s[c].fillna(0.0)
    allc = ["engine", "shipped_main", "shipped_lastcall"] + sc
    res = run(s, allc, "nba", workers=WORKERS)
    res.to_csv(OUT / "res_minutes2_final.csv", index=False)
    pct = lambda r, c: 100 * r[f"{c}:pct"]  # noqa: E731
    sm = lambda r, k: r[[f"{c}:pct" for c in cols[k]]].mean(axis=1) * 100  # noqa: E731
    summ = {"n": len(res), "cols": {c: float(pct(res, c).mean()) for c in allc}}
    print(f"TEST nights {len(res)}")
    for c in allc:
        print(f"  {c}: {pct(res, c).mean():.2f}")
    for k in keys:
        print(f"  {k} seed-mean: {sm(res, k).mean():.2f}  (seeds " + ", ".join(f"{pct(res, c).mean():.2f}" for c in cols[k]) + ")")
    cmp = []
    for k in keys:
        for lab, a in ((f"{k} seed-mean", sm(res, k)), (f"{k}_bag", pct(res, f"m2_{k}_bag"))):
            refs = [("ship25 seed-mean", sm(res, "ship25")), ("ship25_bag", pct(res, "m2_ship25_bag")),
                    ("shipped_main", pct(res, "shipped_main")), ("engine", pct(res, "engine")),
                    ("shipped_lastcall", pct(res, "shipped_lastcall"))]
            for rl, b in refs:
                mm, lo, hi = paired_bootstrap(a, b)
                cmp.append(dict(a=lab, b=rl, mean=mm, lo=lo, hi=hi))
                print(f"  {lab} vs {rl}: {mm:+.2f} [{lo:+.2f}, {hi:+.2f}]")
    summ["cmp"] = cmp
    for season, rs in res.groupby("season"):
        line = {c: float(pct(rs, c).mean()) for c in ["engine", "shipped_main", "shipped_lastcall"]}
        line.update({f"{k} seed-mean": float(sm(rs, k).mean()) for k in keys})
        line.update({f"m2_{k}_bag": float(pct(rs, f"m2_{k}_bag").mean()) for k in keys})
        summ[f"season_{season}"] = dict(n=len(rs), **line)
        print(f"  season {season} (n={len(rs)}): " + ", ".join(f"{a} {b:.2f}" for a, b in line.items()))
    err = {}
    for k in keys:
        e = [minutes_err(d, d[c.replace("m2_", "xmin_")].to_numpy()) for c in cols[k]]
        eb = minutes_err(d, d[f"xmin_{k}_bag"].to_numpy())
        err[k] = dict(mae_seedmean=float(np.mean([x[0] for x in e])), rmse_seedmean=float(np.mean([x[1] for x in e])),
                      mae_bag=eb[0], rmse_bag=eb[1])
        print(f"  minutes test rows {k}: " + json.dumps(err[k]))
    eng = (f.loc[test, "minutes"] * (1 - f.loc[test, "p_zero"])).to_numpy()
    err["engine"] = dict(zip(("mae", "rmse"), minutes_err(d, eng)))
    print("  minutes test rows engine: " + json.dumps(err["engine"]))
    summ["minutes"] = err
    (SCR / "final_summary.json").write_text(json.dumps(summ, indent=1))


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "build":
        build()
    elif cmd == "val":
        stage_val(sys.argv[2].split(",") if len(sys.argv) > 2 else list(CONFIGS), "val")
    elif cmd == "val2":
        stage_val([sys.argv[2]], "val2", alphas=(0.0, 0.5, 1.0))
    elif cmd == "test":
        stage_test(sys.argv[2].split(","))
