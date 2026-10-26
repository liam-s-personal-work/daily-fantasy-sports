"""Train the NBA minutes model and save it to data/learned/ (used live by NBAProvider.after_research).

    python3 scripts/oracle/nba_slates.py              # historical nights -> data/cache/oracle/nba_slates.parquet
    python3 scripts/fit_nba_minutes.py                # train on every completed night, save boosters + json
    python3 scripts/fit_nba_minutes.py --holdout      # train on 2024-25 only and score 2025-26 + 2026-27 against
                                                      # the ex-post optimal lineup (dfs.oracle): the shipped code's
                                                      # held-out result, next to the engine without the model

``--features PATH`` reuses (or, if missing, writes) the feature table, which takes a few minutes to build.
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


def cached_features(seasons: list[int], path: str | None) -> pd.DataFrame:
    if path and Path(path).exists():
        df = pd.read_parquet(path)
        return df[df["season"].isin(seasons)].reset_index(drop=True)
    df = features(seasons)
    if path:
        df.to_parquet(path)
    return df


def holdout(df: pd.DataFrame) -> pd.DataFrame:
    """Fit both tiers on 2024-25, predict 2025-26 and 2026-27 (DK points = P(plays) x minutes x engine rate)."""
    sys.path.insert(0, str(ROOT / "scripts/oracle"))
    from evaluate import run, summary

    tr, te = df[df["season"] == 2025], df[df["season"].isin([2026, 2027])].copy()
    for tier, cols in (("main", nm.MAIN), ("lastcall", nm.LASTCALL)):
        c, r = nm.fit(tr, cols, nm.ROUNDS[tier])
        te[f"minutes_{tier}"] = c.predict_proba(te[cols])[:, 1] * np.clip(r.predict(te[cols]), 0, 48) * te["rate"].to_numpy()
    pred = te[["slate", "athlete_id", "minutes_main", "minutes_lastcall"]].drop_duplicates(["slate", "athlete_id"])
    slates = pd.read_parquet(SLATES)
    slates = slates[slates["season"].isin([2026, 2027])].merge(pred, on=["slate", "athlete_id"], how="left")
    for c in ("minutes_main", "minutes_lastcall"):
        slates[c] = slates[c].fillna(slates["engine"])  # All-Star nights: not modelled, engine stands
    cols = ["engine", "minutes_main", "minutes_lastcall"]
    res = run(slates, cols, "nba")
    print(summary(res, cols))
    for s, g in res.groupby("season"):
        print(f"season {s}: " + ", ".join(f"{c} {100 * g[f'{c}:pct'].mean():.2f}%" for c in cols) + f" (n={len(g)})")
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", default="2025,2026,2027")
    ap.add_argument("--holdout", action="store_true")
    ap.add_argument("--features", help="feature table cache (parquet)")
    ap.add_argument("--out", default=str(nm.LEARNED))
    a = ap.parse_args()
    if a.holdout:
        holdout(cached_features([2025, 2026, 2027], a.features))
        return 0
    seasons = [int(s) for s in a.seasons.split(",")]
    df = cached_features(seasons, a.features)
    models = {"main": nm.fit(df, nm.MAIN, nm.ROUNDS["main"]), "lastcall": nm.fit(df, nm.LASTCALL, nm.ROUNDS["lastcall"])}
    nm.save(models, {"trained_on_seasons": seasons, "nights": int(df["slate"].nunique()), "rows": int(len(df)),
                     "last_night": str(df["slate"].max()), "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                     "source": "scripts/fit_nba_minutes.py (features: dfs.projections.nba_minutes.build_features)"},
            Path(a.out))
    print(f"trained on {df['slate'].nunique()} nights ({len(df)} rows), seasons {seasons} -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
