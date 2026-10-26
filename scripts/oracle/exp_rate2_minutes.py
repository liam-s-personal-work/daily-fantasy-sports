"""rate2 step 1: shipped main-tier minutes components (P(plays), minutes|plays), refit on season 2025 from
feats_v2, seed-averaged over random_state 0,1,2, predicted for every row of seasons 2025 (in-sample, only used
for nothing but sanity) and 2026-27 (clean)."""
import os
import numpy as np, pandas as pd
from dfs.projections import nba_minutes as nm
import lightgbm as lgb

SCR = os.environ.get("DFS_EXP_SCRATCH", "data/cache/oracle/scratch")
f = pd.read_parquet(f"{SCR}/feats_v2.parquet")
f = f.drop_duplicates(["slate", "athlete_id"])
tr = f[f.season == 2025]
te = f[f.season >= 2026]
P, M = [], []
for seed in (0, 1, 2):
    y = (tr["minutes_actual"] > 0).astype(int)
    c = lgb.LGBMClassifier(**dict(nm.CLF, n_estimators=nm.ROUNDS["main"][0], random_state=seed)).fit(tr[nm.MAIN], y)
    pl = tr[y == 1]
    r = lgb.LGBMRegressor(**dict(nm.REG, n_estimators=nm.ROUNDS["main"][1], random_state=seed)).fit(pl[nm.MAIN], pl["minutes_actual"])
    P.append(c.predict_proba(te[nm.MAIN])[:, 1]); M.append(np.clip(r.predict(te[nm.MAIN]), 0, 48))
out = te[["slate", "athlete_id"]].copy()
out["p_play"] = np.mean(P, axis=0); out["min_pred"] = np.mean(M, axis=0)
out.to_parquet(f"{SCR}/rate2/minutes_components.parquet", index=False)
print(out.describe())
