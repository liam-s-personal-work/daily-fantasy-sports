"""rate2: structured per-minute (DK points per minute) model on top of the shipped minutes components.

points = P(plays) x minutes|plays (shipped main tier, refit on 2025, seeds 0-2) x rate, where only the rate
differs between columns. Every setting is chosen on VALIDATION (season 2026, slate < 2026-01-01); the test
(2026 >= 2026-01-01 plus 2027) is run once by `python exp_rate2.py test`.
"""
import os
import sys, json
import numpy as np, pandas as pd
import lightgbm as lgb
sys.path.insert(0, "scripts/oracle")
from evaluate import run, summary
from dfs.oracle import paired_bootstrap

SCR = os.environ.get("DFS_EXP_SCRATCH", "data/cache/oracle/scratch")
OUT = f"{SCR}/rate2"
MODE = sys.argv[1] if len(sys.argv) > 1 else "val"

sl = pd.read_parquet("data/cache/oracle/nba_slates.parquet").drop_duplicates(["slate", "athlete_id"])
rf = pd.read_parquet(f"{OUT}/rate_feats.parquet").drop_duplicates(["slate", "athlete_id"])
mc = pd.read_parquet(f"{OUT}/minutes_components.parquet").drop_duplicates(["slate", "athlete_id"])
sh = pd.read_parquet("data/cache/oracle/exp_shipped2.parquet").drop_duplicates(["slate", "athlete_id"])
df = sl.merge(rf, on=["slate", "athlete_id"], how="left").merge(mc, on=["slate", "athlete_id"], how="left").merge(
    sh, on=["slate", "athlete_id"], how="left")
eng = df["rate"].clip(lower=0.2)

# ---- derived features
def rr(h, k):
    return (df[f"rw_dk_{h}"].fillna(0) + k * eng) / (df[f"rw_min_{h}"].fillna(0) + k)
for h in (3, 5, 10, 20, 40):
    df[f"lr_{h}"] = np.log(rr(h, 60) / eng)
df["cur_rate_rel"] = np.log(((df["cur_dk"].fillna(0) + 30 * eng) / (df["cur_min"].fillna(0) + 30)) / eng)
df["st_bn_rel"] = np.log(((df["st_dk"].fillna(0) + 60 * eng) / (df["st_min"].fillna(0) + 60)) /
                         ((df["bn_dk"].fillna(0) + 60 * eng) / (df["bn_min"].fillna(0) + 60)))
df["hi_lo_rel"] = np.log(df["hi_rate"].clip(0.2, 3) / df["lo_rate"].clip(0.2, 3))
df["wowy_rel"] = df["wowy_w"].fillna(0) / eng
df["wowy_joint_rel"] = df["wowy_joint"].fillna(0) / eng
df["redist_rel"] = df["redist_dk_pg"].fillna(0) / (df["minutes"].clip(lower=8) * eng)
df["min_role"] = df["minutes"] / df["avg_min_cur"].clip(lower=4)
df["rest_c"] = df["rest_days"].clip(upper=7)
FEATS = ["lr_3", "lr_5", "lr_10", "lr_20", "lr_40", "cur_rate_rel", "st_bn_rel", "hi_lo_rel", "wowy_rel", "wowy_joint_rel",
         "wowy_max", "redist_rel", "vac_dk_share", "own_dk_share", "n_absent", "absent_w", "pace_rel", "opp_pos_rel",
         "opp_allowed", "home", "rest_c", "b2b", "start_share", "min_role", "minutes", "rate", "pts_pm", "reb_pm",
         "ast_pm", "tov_pm", "grp", "n_cur", "n_prev", "moved", "vacated_min"]

for c in FEATS:
    df[c] = df[c].astype(float)
df["rate_act"] = df["actual"] / df["minutes_actual"].where(df["minutes_actual"] > 0)
tr_mask = (df["season"] == 2025) & (df["minutes_actual"] >= 6)
val_n = (df["season"] == 2026) & (df["slate"] < "2026-01-01")
test_n = ((df["season"] == 2026) & (df["slate"] >= "2026-01-01")) | (df["season"] == 2027)
played = df["minutes_actual"] > 0

def wmae(rate, mask):
    m = mask & played
    return float((np.abs(rate[m] - df.loc[m, "rate_act"]) * df.loc[m, "minutes_actual"]).sum() / df.loc[m, "minutes_actual"].sum())

def wmae_pts(rate, mask):  # DK points error at actual minutes (= minutes-weighted rate MAE x mean minutes)
    return wmae(rate, mask)

log = {}
# ---- (4) recency: choose half-life h and pseudo-minutes k on validation weighted MAE
grid = {(h, k): wmae(rr(h, k), val_n) for h in (3, 5, 10, 20, 40) for k in (0.001, 30, 60, 120, 250, 500)}
best_hk = min(grid, key=grid.get)
log["recency_grid"] = {f"{h},{k}": v for (h, k), v in grid.items()}
log["recency_best"] = best_hk
df["rate_rec"] = rr(*best_hk)

# ---- (1) structured teammates-out uplift: rate x (1 + b1 wowy_rel + b2 redist_rel), coefficients on validation MAE
best_b, best_v = (0.0, 0.0), wmae(eng, val_n)
for b1 in (0, 0.25, 0.5, 0.75, 1.0):
    for b2 in (0, 0.25, 0.5, 0.75, 1.0):
        v = wmae(eng * (1 + b1 * df["wowy_rel"] + b2 * df["redist_rel"]).clip(0.8, 1.3), val_n)
        if v < best_v:
            best_b, best_v = (b1, b2), v
log["wowy_redist_best"] = best_b
df["rate_wr"] = eng * (1 + best_b[0] * df["wowy_rel"] + best_b[1] * df["redist_rel"]).clip(0.8, 1.3)

# ---- (5) residual GBM on log(rate_act / engine rate), heavy regularisation, weights = minutes
tr = df[tr_mask]
y = np.log((tr["rate_act"] / eng[tr_mask]).clip(0.25, 4.0))
P = dict(objective="l2", learning_rate=0.02, num_leaves=7, min_child_samples=400, reg_lambda=20.0, subsample=0.8,
         subsample_freq=1, colsample_bytree=0.7, num_threads=2, verbose=-1)
preds = {}
for seed in (0, 1, 2):
    m = lgb.LGBMRegressor(**dict(P, n_estimators=1500, random_state=seed)).fit(tr[FEATS], y, sample_weight=tr["minutes_actual"])
    for nr in (100, 200, 400, 700, 1000, 1500):
        preds.setdefault(nr, []).append(m.predict(df[FEATS], num_iteration=nr))
    if seed == 0:
        imp = pd.Series(m.booster_.feature_importance("gain"), FEATS).sort_values(ascending=False)
gbm_grid = {}
for nr, ps in preds.items():
    p = np.mean(ps, axis=0)
    for a in (0.5, 0.75, 1.0):
        for clip in (0.15, 0.3):
            gbm_grid[(nr, a, clip)] = wmae(eng * np.exp(np.clip(a * p, -clip, clip)), val_n)
best_g = min(gbm_grid, key=gbm_grid.get)
log["gbm_best"] = best_g
log["gbm_importance"] = imp.round(1).to_dict()
pg = np.mean(preds[best_g[0]], axis=0)
df["rate_gbm"] = eng * np.exp(np.clip(best_g[1] * pg, -best_g[2], best_g[2]))
# (round 2, added after the round-1 validation lineup run; the alpha=0.5 "half" variant was identical to r_gbm
# because validation already picked alpha=0.5, so it is dropped). Mean-unbiased variant: L2 on the ratio
# rate_act / engine rate (expected points need the mean, log-L2 targets the geometric mean); rounds, alpha and
# clip chosen on validation minutes-weighted squared rate error.
def wmse(rate, mask):
    m = mask & played
    return float((np.square(rate[m] - df.loc[m, "rate_act"]) * df.loc[m, "minutes_actual"]).sum() / df.loc[m, "minutes_actual"].sum())
ylin = (tr["rate_act"] / eng[tr_mask]).clip(0.25, 4.0) - 1.0
pl_ = {}
for seed in (0, 1, 2):
    m = lgb.LGBMRegressor(**dict(P, n_estimators=1500, random_state=seed)).fit(tr[FEATS], ylin, sample_weight=tr["minutes_actual"])
    for nr in (100, 200, 400, 700, 1000, 1500):
        pl_.setdefault(nr, []).append(m.predict(df[FEATS], num_iteration=nr))
lin_grid = {}
for nr, ps in pl_.items():
    p = np.mean(ps, axis=0)
    for a in (0.5, 0.75, 1.0):
        for clip in (0.15, 0.3):
            lin_grid[(nr, a, clip)] = wmse(eng * (1 + np.clip(a * p, -clip, clip)), val_n)
best_l = min(lin_grid, key=lin_grid.get)
log["gbm_lin_best"] = best_l
pl1 = np.mean(pl_[best_l[0]], axis=0)
df["rate_gbm_lin"] = eng * (1 + np.clip(best_l[1] * pl1, -best_l[2], best_l[2]))

RATES = {"r_eng": eng, "r_rec": df["rate_rec"], "r_wr": df["rate_wr"], "r_gbm": df["rate_gbm"], "r_gbm_lin": df["rate_gbm_lin"]}
for k, r in RATES.items():
    df[k.replace("r_", "pts_")] = df["p_play"].fillna(0) * df["min_pred"].fillna(0) * r
log["mae_val"] = {k: wmae(r, val_n) for k, r in RATES.items()}
log["mae_train2025"] = {k: wmae(r, df["season"] == 2025) for k, r in RATES.items()}
log["mse_val"] = {k: wmse(r, val_n) for k, r in RATES.items()}
print(json.dumps(log, indent=1, default=str))

COLS = ["pts_eng", "pts_gbm", "pts_gbm_lin", "pts_wr", "shipped_main", "engine"]  # test: pts_gbm = pre-registered primary (best validation lineup %); pts_rec dropped (no validation gain)
if MODE == "val2":
    COLS = ["pts_eng", "pts_gbm_lin"]
keep = ["slate", "season", "athlete_id", "pos", "salary", "game", "actual"] + COLS
if MODE in ("val", "val2"):
    res = run(df.loc[val_n, keep], COLS, "nba", workers=2)
    res.to_csv(f"{OUT}/res_rate2_{MODE}.csv", index=False)
    print(summary(res, COLS))
    json.dump(log, open(f"{OUT}/log_{MODE}.json", "w"), indent=1, default=str)
else:
    m = test_n & played
    mae = {k: wmae(r, test_n) for k, r in RATES.items()}
    mae["mse"] = {k: wmse(r, test_n) for k, r in RATES.items()}
    mae["pts_mae_all_rows"] = {c: float((df.loc[test_n, c].fillna(0) - df.loc[test_n, "actual"]).abs().mean()) for c in COLS}
    for ss in (2026, 2027):
        mae[f"mae_{ss}"] = {k: wmae(r, test_n & (df["season"] == ss)) for k, r in RATES.items()}
    print("test minutes-weighted rate MAE (played rows):", mae)
    for k, r in RATES.items():
        df[k] = r
    df.loc[df["season"] >= 2026, ["slate", "athlete_id", "p_play", "min_pred"] + list(RATES) + COLS].to_parquet(
        "data/cache/oracle/exp_rate2.parquet", index=False)
    res = run(df.loc[test_n, keep], COLS, "nba", workers=2)
    res.to_csv("data/cache/oracle/res_rate2.csv", index=False)
    print(summary(res, COLS))
    for base in ("shipped_main", "engine", "pts_eng"):
        for c in COLS:
            if c != base:
                print(base, c, paired_bootstrap(100 * res[f"{c}:pct"], 100 * res[f"{base}:pct"]))
    for s, g in res.groupby("season"):
        print(s, len(g), {c: round(100 * g[f"{c}:pct"].mean(), 2) for c in COLS})
    json.dump({**log, "mae_test": mae}, open(f"{OUT}/log_test.json", "w"), indent=1, default=str)
