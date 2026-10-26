"""Report for experiment "portfolio" (best-of-N entries). Reads res_portfolio_<phase>_<proj>.csv from
exp_portfolio.py and the harness's single-lineup columns (engine, shipped_main, shipped_lastcall).

    python3 scripts/oracle/exp_portfolio_eval.py val      # choose k* and the primary method (validation only)
    python3 scripts/oracle/exp_portfolio_eval.py test --kstar K --primary M   # the one test read

Pre-registered selection rules (validation only):
  k*      = argmax_k mean over N in {2,3,5,10} of mean best-of-N % of optimal of a_k;
  primary = the method among b_freq, c_greedy, d_ratio, e_mix<k*> with the highest mean over N in {2,3,5,10}.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/oracle"))
from evaluate import run  # noqa: E402
from exp_portfolio import NS, load  # noqa: E402

from dfs.oracle import paired_bootstrap  # noqa: E402

SEL_NS = [2, 3, 5, 10]


def harness(phase: str) -> pd.DataFrame:
    path = ROOT / f"data/cache/oracle/res_portfolio_harness_{phase}.csv"
    if path.exists():
        return pd.read_csv(path)
    df = load(phase)
    r = run(df, ["engine", "shipped_main", "shipped_lastcall"], "nba", workers=1)
    r.to_csv(path, index=False)
    return r


def table(res: pd.DataFrame, meths: list[str], base_single: pd.DataFrame, a_ref: str | None) -> list[str]:
    lines = ["| method | N | entry cost | mean best-of-N % opt | >=85% nights | >=90% nights | exact hits | vs shipped_main (1 entry) | vs engine (1 entry) | vs " + (a_ref or "-") + " |",
             "|---|---:|---:|---:|---:|---:|---:|---|---|---|"]
    m = res.merge(base_single[["slate", "engine:pct", "shipped_main:pct", "shipped_lastcall:pct"]], on="slate", how="left")
    for meth in meths:
        for N in NS:
            c = f"{meth}:N{N}"
            if c not in m or m[c].isna().any():
                continue
            v = 100 * m[c]
            d1 = paired_bootstrap(v, 100 * m["shipped_main:pct"])
            d2 = paired_bootstrap(v, 100 * m["engine:pct"])
            ra = f"{a_ref}:N{N}"
            d3 = paired_bootstrap(v, 100 * m[ra]) if (a_ref and ra in m and m[ra].notna().all() and meth != a_ref) else None
            f = lambda d: f"{d[0]:+.2f} [{d[1]:+.2f}, {d[2]:+.2f}]"
            lines.append(f"| {meth} | {N} | ${0.5 * N:.2f} | {v.mean():.2f} | {(m[c] >= 0.85).mean():.3f} | {(m[c] >= 0.90).mean():.3f} | "
                         f"{int((m[c] >= 0.99999).sum())} | {f(d1)} | {f(d2)} | {f(d3) if d3 else '-'} |")
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=["val", "test"])
    ap.add_argument("--proj", default="shipped_main")
    ap.add_argument("--kstar", type=int, default=0)
    ap.add_argument("--primary", default="")
    a = ap.parse_args()
    res = pd.read_csv(ROOT / f"data/cache/oracle/res_portfolio_{a.phase}_{a.proj}.csv")
    hs = harness(a.phase)
    print(f"{a.phase} {a.proj}: {len(res)} nights; harness single-lineup: " +
          ", ".join(f"{c} {100 * hs[c + ':pct'].mean():.2f}%" for c in ("engine", "shipped_main", "shipped_lastcall")))
    a1 = res.merge(hs[["slate", f"{a.proj}:pct"]], on="slate")
    ka = [c for c in a1.columns if c.startswith("a_k") and c.endswith(":N1")][0]
    print(f"check: {ka} vs harness {a.proj}: mean diff {100 * (a1[ka] - a1[f'{a.proj}:pct']).mean():+.3f} pp, "
          f"identical nights {np.isclose(a1[ka], a1[f'{a.proj}:pct']).mean():.3f}")
    meths = [c.split(":")[0] for c in res.columns if c.endswith(":N1")]
    if a.phase == "val":
        sel = {k: np.mean([res[f"a_k{k}:N{N}"].mean() for N in SEL_NS]) for k in (1, 2, 3, 4)}
        kstar = max(sel, key=sel.get)
        print("k selection (mean over N=2,3,5,10):", {k: round(100 * v, 3) for k, v in sel.items()}, "-> k* =", kstar)
        cand = ["b_freq", "c_greedy", "d_ratio", f"e_mix{kstar}"]
        psel = {mm: np.mean([res[f"{mm}:N{N}"].mean() for N in SEL_NS]) for mm in cand}
        primary = max(psel, key=psel.get)
        print("primary selection:", {k: round(100 * v, 3) for k, v in psel.items()}, "->", primary)
    else:
        kstar, primary = a.kstar, a.primary
    show = [m for m in [f"a_k{k}" for k in (1, 2, 3, 4)] + ["b_freq", "c_greedy", "d_ratio", f"e_mix{kstar}"] if m in meths]
    print("\n".join(table(res, show, hs, f"a_k{kstar}")))
    print("\nper season (mean best-of-N %):")
    for s, g in res.groupby("season"):
        print(f"season {s} (n={len(g)}): " + "; ".join(f"{mm} " + ",".join(f"N{N}={100 * g[f'{mm}:N{N}'].mean():.1f}" for N in NS if g[f'{mm}:N{N}'].notna().all()) for mm in show))
    for mm in show:
        hit = [N for N in NS if f"{mm}:N{N}" in res and res[f"{mm}:N{N}"].notna().all() and res[f"{mm}:N{N}"].mean() >= 0.85]
        print(f"smallest N with mean >= 85%: {mm}: {hit[0] if hit else 'none'}")
    print(f"mean distinct world optima {res['n_distinct_opt'].mean():.1f}; mean pool {res['pool'].mean():.1f}; secs/night {res['secs'].mean():.1f}")


if __name__ == "__main__":
    main()
