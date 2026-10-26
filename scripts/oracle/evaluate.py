"""Score projection columns against the ex-post optimum on historical slates.

    python scripts/oracle/evaluate.py data/cache/oracle/nba_slates.parquet nba engine dk_avg [--extra cols.parquet] [--out res.csv]

``--extra``: a parquet with key columns (slate, athlete_id or player_id) plus new score columns to join
(how experiments plug in their out-of-sample projections). Prints mean % of optimal, points and
overlap per column and season, and paired bootstrap differences against the first column.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
from functools import partial

import pandas as pd

from dfs.oracle import Rules, evaluate_slate, paired_bootstrap


def _one(item, cols, sport):
    sid, g = item
    g = g.reset_index(drop=True).copy()
    g["pos"] = g["pos"].map(lambda s: set(s.split(",")) if isinstance(s, str) else s)
    r = evaluate_slate(g, cols, Rules(sport))
    return {"slate": sid, "season": g["season"].iloc[0], **r}


def run(df: pd.DataFrame, cols: list[str], sport: str, workers: int = 4) -> pd.DataFrame:
    with ProcessPoolExecutor(workers) as ex:
        res = list(ex.map(partial(_one, cols=cols, sport=sport), df.groupby("slate"), chunksize=4))
    return pd.DataFrame([r for r in res if "optimal" in r])


def summary(res: pd.DataFrame, cols: list[str]) -> str:
    lines = ["| column | slates | % of optimal | points | optimal | overlap (of 8-9) |", "|---|---:|---:|---:|---:|---:|"]
    for c in cols:
        lines.append(f"| {c} | {res[f'{c}:pct'].notna().sum()} | {100 * res[f'{c}:pct'].mean():.2f}% | "
                     f"{res[f'{c}:pts'].mean():.1f} | {res['optimal'].mean():.1f} | {res[f'{c}:overlap'].mean():.2f} |")
    base = cols[0]
    lines += ["", f"Paired difference vs {base} (percentage points of optimal, 95% bootstrap over slates):", ""]
    for c in cols[1:]:
        m, lo, hi = paired_bootstrap(100 * res[f"{c}:pct"], 100 * res[f"{base}:pct"])
        lines.append(f"- {c}: {m:+.2f} [{lo:+.2f}, {hi:+.2f}]")
    return "\n".join(lines)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("parquet"); ap.add_argument("sport"); ap.add_argument("cols", nargs="+")
    ap.add_argument("--extra"); ap.add_argument("--out"); ap.add_argument("--seasons", default="")
    a = ap.parse_args()
    df = pd.read_parquet(a.parquet)
    if a.extra:
        ex = pd.read_parquet(a.extra)
        key = [k for k in ("slate", "athlete_id", "player_id") if k in ex.columns and k in df.columns]
        df = df.merge(ex, on=key, how="left")
    if a.seasons:
        df = df[df["season"].isin([int(s) for s in a.seasons.split(",")])]
    res = run(df, a.cols, a.sport)
    if a.out:
        res.to_csv(a.out, index=False)
    print(summary(res, a.cols))
    for s, g in res.groupby("season"):
        print(f"\nseason {s}: " + ", ".join(f"{c} {100 * g[f'{c}:pct'].mean():.2f}%" for c in a.cols) + f" (n={len(g)})")
