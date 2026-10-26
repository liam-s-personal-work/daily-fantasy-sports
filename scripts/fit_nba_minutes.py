"""Train the NBA minutes model and save it to data/learned/ (used live by NBAProvider.after_research).

    python3 scripts/oracle/nba_slates.py              # historical nights -> data/cache/oracle/nba_slates.parquet
    python3 scripts/fit_nba_minutes.py                # train on every completed night, save boosters + json
    python3 scripts/fit_nba_minutes.py --reproduce    # train on 2024-25 only and compare with the experiment's
                                                      # test predictions (exp_minutes.parquet): proves the shipped
                                                      # feature code is the one that was evaluated
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from dfs.data import nba
from dfs.projections import nba_minutes as nm

ROOT = Path(__file__).resolve().parents[1]
SLATES = ROOT / "data/cache/oracle/nba_slates.parquet"
NBA_TEAMS = {"ATL", "BKN", "BOS", "CHA", "CHI", "CLE", "DAL", "DEN", "DET", "GS", "HOU", "IND", "LAC", "LAL", "MEM",
             "MIA", "MIL", "MIN", "NO", "NY", "OKC", "ORL", "PHI", "PHX", "POR", "SA", "SAC", "TOR", "UTAH", "WSH"}


def features(seasons: list[int]) -> pd.DataFrame:
    slates = pd.read_parquet(SLATES)
    slates = slates[slates["season"].isin(seasons)]
    bad = set(slates.loc[~slates["team"].isin(NBA_TEAMS), "slate"])  # All-Star nights
    slates = slates[~slates["slate"].isin(bad)]
    box = nba.load_box(sorted({s - 1 for s in seasons} | set(seasons)))
    return nm.build_features(slates, box).reset_index(drop=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", default="2025,2026,2027")
    ap.add_argument("--reproduce", action="store_true")
    ap.add_argument("--out", default=str(nm.LEARNED))
    a = ap.parse_args()
    if a.reproduce:
        df = features([2025, 2026, 2027])
        tr, te = df[df["season"] == 2025], df[df["season"].isin([2026, 2027])].copy()
        c, r = nm.fit(tr, nm.MAIN, nm.ROUNDS["main"])
        p, m = c.predict_proba(te[nm.MAIN])[:, 1], np.clip(r.predict(te[nm.MAIN]), 0, 48)
        te["mine"] = p * m * te["rate"].to_numpy()
        ref = pd.read_parquet(ROOT / "data/cache/oracle/exp_minutes.parquet")
        j = te.merge(ref[["slate", "athlete_id", "minutes_main"]], on=["slate", "athlete_id"])
        d = (j["mine"] - j["minutes_main"]).abs()
        print(f"{len(j)} test rows: max |diff| {d.max():.4f}, mean {d.mean():.5f}, corr {j['mine'].corr(j['minutes_main']):.6f}")
        return 0 if d.mean() < 0.05 else 1
    seasons = [int(s) for s in a.seasons.split(",")]
    df = features(seasons)
    models = {"main": nm.fit(df, nm.MAIN, nm.ROUNDS["main"]), "lastcall": nm.fit(df, nm.LASTCALL, nm.ROUNDS["lastcall"])}
    nm.save(models, {"trained_on_seasons": seasons, "nights": int(df["slate"].nunique()), "rows": int(len(df)),
                     "last_night": str(df["slate"].max()), "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                     "source": "scripts/fit_nba_minutes.py (features: dfs.projections.nba_minutes.build_features)"},
            Path(a.out))
    print(f"trained on {df['slate'].nunique()} nights ({len(df)} rows), seasons {seasons} -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
