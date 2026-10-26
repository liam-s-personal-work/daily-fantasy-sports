"""Fit a DraftKings-like NBA salary model on real DK files (salaries are set from recent production,
not from our minutes model, so our projections face realistic mispricing)."""
from __future__ import annotations

import json
import sys
from datetime import timedelta

import numpy as np
import pandas as pd

from dfs.data import nba
from dfs.io.contest_file import load_contest
from dfs.projections.nba_provider import match_players, player_table

FILES = ["data/cache/real_samples/DKEntries_NBA_Mar2026.csv", sys.argv[1] if len(sys.argv) > 1 else None]


def dk_features(box: pd.DataFrame, cutoff, season: int) -> pd.DataFrame:
    """Per athlete, from games before ``cutoff`` minus 2 days (DK posts salaries early)."""
    h = nba.before(box, cutoff - timedelta(days=2))
    h = h[~h["did_not_play"] & (h["minutes"] > 0) & (h["season_type"] == 2)].copy()
    h["dk"] = nba.dk_points(h)
    cur = h[h["season"] == season].sort_values("start")
    prev = h[h["season"] == season - 1]
    f = pd.DataFrame({"ppg": cur.groupby("athlete_id")["dk"].mean(), "n": cur.groupby("athlete_id").size(),
                      "l5": cur.groupby("athlete_id")["dk"].apply(lambda s: s.tail(5).mean()),
                      "mpg": cur.groupby("athlete_id")["minutes"].mean()})
    f = f.join(pd.DataFrame({"ppg_prev": prev.groupby("athlete_id")["dk"].mean(),
                             "mpg_prev": prev.groupby("athlete_id")["minutes"].mean()}), how="outer")
    w = (f["n"].fillna(0) / (f["n"].fillna(0) + 5)).to_numpy()
    f["ppg_blend"] = np.where(f["ppg"].notna(), w * f["ppg"].fillna(0) + (1 - w) * f["ppg_prev"].fillna(f["ppg"]),
                              f["ppg_prev"])
    f["l5_blend"] = np.where(f["l5"].notna(), w * f["l5"].fillna(0) + (1 - w) * f["ppg_prev"].fillna(f["l5"]),
                             f["ppg_prev"])
    return f


def training_rows() -> pd.DataFrame:
    box = nba.load_box([2025, 2026, 2027])
    rows = []
    for path in [p for p in FILES if p]:
        c = load_contest(path)
        first = min(p.start_time for p in c.players.values() if p.start_time)
        season = nba.season_for(first.date())
        table = player_table(box, first, season)
        ids, _ = match_players(c, table)
        feats = dk_features(box, first, season)
        sal = {cd.player_key: cd.salary for cd in c.candidates}
        for k, aid in ids.items():
            if aid in feats.index:
                rows.append({"file": path, "salary": sal[k], **feats.loc[aid].to_dict()})
    return pd.DataFrame(rows)


def fit(d: pd.DataFrame) -> dict:
    d = d.dropna(subset=["ppg_blend"])
    X = np.column_stack([np.ones(len(d)), d["ppg_blend"], d["l5_blend"].fillna(d["ppg_blend"])])
    coef, *_ = np.linalg.lstsq(X, d["salary"].to_numpy(dtype=float), rcond=None)
    pred = np.clip(np.round(X @ coef, -2), 3000, 12500)
    r2 = 1 - ((d["salary"] - pred) ** 2).sum() / ((d["salary"] - d["salary"].mean()) ** 2).sum()
    resid_sd = float(np.std(d["salary"] - pred))
    return {"coef": coef.tolist(), "r2": float(r2), "resid_sd": resid_sd, "n": int(len(d)),
            "mae": float(np.mean(np.abs(d["salary"] - pred)))}


if __name__ == "__main__":
    d = training_rows()
    m = fit(d)
    print(json.dumps(m, indent=1))
    json.dump(m, open("scripts/oracle/nba_salary_model.json", "w"), indent=1)
