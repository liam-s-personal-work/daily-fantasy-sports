"""Experiment "minutes2", step 1: season-2024 (2023-24) NBA nights with the same logic as nba_slates.build_night,
plus the shipped point-in-time minutes features (dfs.projections.nba_minutes.build_features), as extra training data.

    python3 scripts/oracle/exp_minutes2_slates.py

Writes data/cache/oracle/exp_minutes2_slates2024.parquet (slates) and
data/cache/oracle/exp_minutes2_feats2024.parquet (features). Does not touch nba_slates.parquet.
"""
from __future__ import annotations

import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import nba_slates  # noqa: E402
from dfs.data import nba  # noqa: E402
from dfs.projections import nba_minutes as nm  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data/cache/oracle/exp_minutes2_slates2024.parquet"
FEATS = ROOT / "data/cache/oracle/exp_minutes2_feats2024.parquet"

if __name__ == "__main__":
    t0 = time.time()
    box = nba.load_box([2023, 2024])
    nba_slates.BOX = box  # build_night reads this global; set before the workers fork
    reg = box[(box["season_type"] == 2) & (box["season"] == 2024)]
    nights = sorted(reg["game_date"].astype(str).unique())
    print(len(nights), "nights", time.time() - t0, flush=True)
    if not OUT.exists():
        with ProcessPoolExecutor(2) as ex:
            frames = list(ex.map(nba_slates.build_night, nights, chunksize=4))
        d = pd.concat([f for f in frames if len(f)], ignore_index=True)
        d["pos"] = d["pos"].map(lambda s: ",".join(sorted(s)))
        d.to_parquet(OUT)
        print(f"{d['slate'].nunique()} slates, {len(d)} rows -> {OUT}", time.time() - t0, flush=True)
    d = pd.read_parquet(OUT)
    f = nm.build_features(d, box)
    f.to_parquet(FEATS)
    print(f"features {f.shape} -> {FEATS}", time.time() - t0, flush=True)
