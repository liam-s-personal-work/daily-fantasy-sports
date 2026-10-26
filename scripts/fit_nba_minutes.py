"""Train the NBA minutes and rate models and save them to data/learned/ (used live by NBAProvider.after_research).

    python3 scripts/oracle/nba_slates.py              # historical nights -> data/cache/oracle/nba_slates.parquet
    python3 scripts/fit_nba_minutes.py                # train on every completed night, save boosters + json
    python3 scripts/fit_nba_minutes.py --holdout      # train on 2024-25 only and score 2025-26 + 2026-27 against
                                                      # the ex-post optimal lineup (dfs.oracle): the shipped code's
                                                      # held-out result, next to the engine without the models

``--features PATH`` / ``--rate-features PATH`` reuse (or, if missing, write) the minutes / rate feature tables, which
take a few minutes / about a minute to build.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from dfs.data import nba
from dfs.projections import nba_minutes as nm
from dfs.projections import nba_rate as nr

ROOT = Path(__file__).resolve().parents[1]
SLATES = ROOT / "data/cache/oracle/nba_slates.parquet"
NBA_TEAMS = {"ATL", "BKN", "BOS", "CHA", "CHI", "CLE", "DAL", "DEN", "DET", "GS", "HOU", "IND", "LAC", "LAL", "MEM",
             "MIA", "MIL", "MIN", "NO", "NY", "OKC", "ORL", "PHI", "PHX", "POR", "SA", "SAC", "TOR", "UTAH", "WSH"}


@lru_cache(maxsize=1)
def _box(seasons: tuple[int, ...]) -> pd.DataFrame:
    return nba.load_box(sorted({s - 1 for s in seasons} | set(seasons)))


def features(seasons: list[int], build=nm.build_features) -> pd.DataFrame:
    slates = pd.read_parquet(SLATES)
    slates = slates[slates["season"].isin(seasons)]
    bad = set(slates.loc[~slates["team"].isin(NBA_TEAMS), "slate"])  # All-Star nights
    slates = slates[~slates["slate"].isin(bad)]
    return build(slates, _box(tuple(seasons))).reset_index(drop=True)


def cached_features(seasons: list[int], path: str | None, build=nm.build_features) -> pd.DataFrame:
    if path and Path(path).exists():
        df = pd.read_parquet(path)
        return df[df["season"].isin(seasons)].reset_index(drop=True)
    df = features(seasons, build)
    if path:
        df.to_parquet(path)
    return df


def holdout(df: pd.DataFrame, rf: pd.DataFrame) -> pd.DataFrame:
    """Fit both minutes tiers and the rate model on 2024-25, predict 2025-26 and 2026-27 (DK points = P(plays) x
    minutes x engine rate, times the rate model's multiplier in the x_rate columns)."""
    sys.path.insert(0, str(ROOT / "scripts/oracle"))
    from evaluate import run, summary

    tr, te = df[df["season"] == 2025], df[df["season"].isin([2026, 2027])].copy()
    for tier, cols in (("main", nm.MAIN), ("lastcall", nm.LASTCALL)):
        c, r = nm.fit(tr, cols, nm.ROUNDS[tier])
        te[f"minutes_{tier}"] = c.predict_proba(te[cols])[:, 1] * np.clip(r.predict(te[cols]), 0, 48) * te["rate"].to_numpy()
    rte = rf[rf["season"].isin([2026, 2027])]
    mult = nr.multiplier([m.predict(rte[nr.FEATS]) for m in nr.fit(rf[rf["season"] == 2025])])
    te = te.merge(rte[["slate", "athlete_id"]].assign(rate_model=mult), on=["slate", "athlete_id"], how="left")
    for tier in ("main", "lastcall"):
        te[f"minutes_{tier}_x_rate"] = te[f"minutes_{tier}"] * te["rate_model"]
    cols = ["minutes_main", "engine", "minutes_main_x_rate", "minutes_lastcall", "minutes_lastcall_x_rate"]
    pred = te[["slate", "athlete_id"] + [c for c in cols if c != "engine"]].drop_duplicates(["slate", "athlete_id"])
    slates = pd.read_parquet(SLATES)
    slates = slates[slates["season"].isin([2026, 2027])].merge(pred, on=["slate", "athlete_id"], how="left")
    for c in cols:
        slates[c] = slates[c].fillna(slates["engine"])  # All-Star nights: not modelled, engine stands
    res = run(slates, cols, "nba", workers=2)
    print(summary(res, cols))
    for s, g in res.groupby("season"):
        print(f"season {s}: " + ", ".join(f"{c} {100 * g[f'{c}:pct'].mean():.2f}%" for c in cols) + f" (n={len(g)})")
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", default="2025,2026,2027")
    ap.add_argument("--holdout", action="store_true")
    ap.add_argument("--features", help="minutes feature table cache (parquet)")
    ap.add_argument("--rate-features", help="rate feature table cache (parquet)")
    ap.add_argument("--out", default=str(nm.LEARNED))
    a = ap.parse_args()
    if a.holdout:
        seasons = [2025, 2026, 2027]
        holdout(cached_features(seasons, a.features), cached_features(seasons, a.rate_features, nr.build_rate_features))
        return 0
    seasons = [int(s) for s in a.seasons.split(",")]
    df = cached_features(seasons, a.features)
    models = {"main": nm.fit(df, nm.MAIN, nm.ROUNDS["main"]), "lastcall": nm.fit(df, nm.LASTCALL, nm.ROUNDS["lastcall"])}
    created = datetime.now(timezone.utc).isoformat(timespec="seconds")
    nm.save(models, {"trained_on_seasons": seasons, "nights": int(df["slate"].nunique()), "rows": int(len(df)),
                     "last_night": str(df["slate"].max()), "created": created,
                     "source": "scripts/fit_nba_minutes.py (features: dfs.projections.nba_minutes.build_features)"},
            Path(a.out))
    print(f"minutes model: trained on {df['slate'].nunique()} nights ({len(df)} rows), seasons {seasons} -> {a.out}")
    rf = cached_features(seasons, a.rate_features, nr.build_rate_features)
    n_fit = int((rf["minutes_actual"] >= nr.SETTINGS["min_minutes"]).sum())
    nr.save(nr.fit(rf), {"trained_on_seasons": seasons, "nights": int(rf["slate"].nunique()), "rows": n_fit,
                         "last_night": str(rf["slate"].max()), "created": created,
                         "source": "scripts/fit_nba_minutes.py (features: dfs.projections.nba_rate.build_rate_features)"},
            Path(a.out))
    print(f"rate model: trained on {rf['slate'].nunique()} nights ({n_fit} rows played {nr.SETTINGS['min_minutes']:g}+ "
          f"minutes), seasons {seasons} -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
