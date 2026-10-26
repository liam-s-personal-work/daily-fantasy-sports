"""Experiment "portfolio": best-of-N multi-entry lineups (DK NBA classic, up to 150 entries).

    python3 scripts/oracle/exp_portfolio.py --phase val|test [--proj shipped_main] [--workers 2]

For each night build N distinct legal lineups pre-game; the night's score is max_i actual(L_i) / optimum. Methods
(pre-registered; every setting chosen on VALIDATION = season-2026 nights before 2026-01-01):

 (a) a_k{k}: successive max-projection lineups, each new one differs from every previous one by >= k players
     (row sum_{i in L_j} x_i <= 8 - k for each earlier L_j); k in {1,2,3,4}; k* chosen on validation.
 (b) b_freq: simulated-world optima. W correlated worlds are drawn around the projection (P(plays) = 1 - p_zero of
     the slate, conditional points = proj / (1 - p_zero) with the engine's two-piece normal, sd x s, s calibrated on
     validation rows for 80% interval coverage; game/team factor correlation from exp_select.fit_corr on 2025).
     Each world's optimum is solved exactly; distinct optima ranked by frequency, ties by projected sum.
 (c) c_greedy: greedy max of E[max_i points_i] over W2 fresh worlds; candidates = all (a) lineups + all (b) optima.
 (d) d_ratio (own idea): greedy max of E[max_i points_i / optimum(world)] -- the expected best-of-N % of optimal,
     the metric itself -- on the second half of the solved worlds (their optima are known), candidates = (a) lineups
     + optima of the first half of the worlds (so a candidate is never scored on the world that produced it).
 (e) e_mix (own idea): (a) k* for the first ceil(N/2) entries, then d_ratio's greedy continues from that set.

Outputs: data/cache/oracle/res_portfolio_<phase>_<proj>.csv (one row per night: <method>:N<n> pct of optimal).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time

for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")
from concurrent.futures import ProcessPoolExecutor
from functools import partial
from pathlib import Path

import highspy
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from exp_select import WorldSolver, allstar_nights, bucket, fit_corr  # noqa: E402

from dfs.oracle import Rules, best_lineup  # noqa: E402
from dfs.projections.nba_provider import NBAParams  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SLATES = ROOT / "data/cache/oracle/nba_slates.parquet"
SHIPPED = ROOT / "data/cache/oracle/exp_shipped2.parquet"
P = NBAParams()
NS = [1, 2, 3, 5, 10, 20, 50, 100, 150]
KS = [1, 2, 3, 4]
MARGIN = 6


# ---------------------------------------------------------------- data

def load(phase: str) -> pd.DataFrame:
    df = pd.read_parquet(SLATES)
    df = df[df["season"].isin([2026, 2027])].drop_duplicates(["slate", "athlete_id"])
    sh = pd.read_parquet(SHIPPED).drop_duplicates(["slate", "athlete_id"])
    df = df.merge(sh, on=["slate", "athlete_id"], how="left")
    val = (df["season"] == 2026) & (df["slate"] < "2026-01-01")
    df = df[val] if phase == "val" else df[~val]
    return df.reset_index(drop=True)


def dist_params(proj: np.ndarray, pz: np.ndarray, s: float):
    pz = np.clip(pz, 0.0, 0.6)
    cond = np.maximum(proj, 0.0) / (1.0 - pz)
    sd = s * np.maximum(P.sd_a + P.sd_b * cond, 0.5)
    k = sd / math.sqrt(1.0 + P.skew ** 2 * (1.0 - 2.0 / math.pi))
    sl, sh = k * (1 - P.skew), k * (1 + P.skew)
    return cond - (sh - sl) / math.sqrt(2 * math.pi), sl, sh, pz


def calibrate_s(df: pd.DataFrame, proj: str, grid=np.round(np.arange(0.6, 1.81, 0.1), 2)) -> tuple[float, dict]:
    """Choose the sd multiplier on validation rows: 80% central interval coverage of actual closest to 0.80
    (plus the 50% interval to 0.50). Monte Carlo, independent draws per row."""
    rng = np.random.default_rng(7)
    out = {}
    a = df["actual"].to_numpy()
    for s in grid:
        p50, sl, sh, pz = dist_params(df[proj].to_numpy(), df["p_zero"].to_numpy(), s)
        z = rng.standard_normal((400, len(df)))
        x = np.maximum(0.0, p50 + np.where(z < 0, sl, sh) * z) * (rng.random((400, len(df))) >= pz)
        q = np.quantile(x, [0.1, 0.25, 0.75, 0.9], axis=0)
        c80 = float(((a >= q[0]) & (a <= q[3])).mean())
        c50 = float(((a >= q[1]) & (a <= q[2])).mean())
        out[float(s)] = (round(c80, 4), round(c50, 4))
    best = min(out, key=lambda s: abs(out[s][0] - 0.8) + abs(out[s][1] - 0.5))
    return best, out


def draw(g, proj, s, corr, W, rng):
    p50, sl, sh, pz = dist_params(proj, g["p_zero"].to_numpy(), s)
    b = bucket(g["minutes"].to_numpy())
    lam, mu = np.asarray(corr["lam"])[b], np.asarray(corr["mu"])[b]
    gi = pd.factorize(g["game"])[0]
    ti = pd.factorize(g["team"])[0]
    z = (lam * rng.standard_normal((W, gi.max() + 1))[:, gi] + mu * rng.standard_normal((W, ti.max() + 1))[:, ti]
         + np.sqrt(np.clip(1 - lam ** 2 - mu ** 2, 0, 1)) * rng.standard_normal((W, len(g))))
    x = np.maximum(0.0, p50 + np.where(z < 0, sl, sh) * z)
    return x * (rng.random((W, len(g))) >= pz)


# ---------------------------------------------------------------- method (a): successive max-projection lineups

def successive(ws: WorldSolver, score: np.ndarray, k: int, N: int, pool: np.ndarray | None = None) -> list[tuple]:
    """Top lineups by projection, each differing from all earlier ones by >= k players. Own HiGHS model (rows added
    incrementally); single-game solutions fall back to the game-indicator MILP with the same extra rows."""
    n = ws.n
    # prune: a player with >= K + MARGIN dominators (pos superset, cheaper, higher projection) spanning 2+ games is
    # dropped (exact for the first lineup at MARGIN 0; a margin keeps the deeper lineups practically unaffected)
    D = (ws.D0 & (score[:, None] > score[None, :])).astype(np.float32)
    per_game = ws.onehot.T @ D
    drop = (per_game.sum(0) >= ws.K + MARGIN) & ((per_game > 0).sum(0) >= 2)
    if pool is not None:
        drop |= ~pool
    h = highspy.Highs()
    h.setOptionValue("output_flag", False)
    h.setOptionValue("threads", 1)
    h.setOptionValue("mip_rel_gap", 3e-3)  # ~0.8 projected points: near-ties are equivalent, solves 10x faster
    h.setOptionValue("time_limit", 0.5)
    inf = highspy.kHighsInf
    for j in range(n):
        h.addVar(0.0, 0.0 if drop[j] else 1.0)
    h.changeColsIntegrality(n, np.arange(n, dtype=np.int32), np.array([highspy.HighsVarType.kInteger] * n))
    h.changeObjectiveSense(highspy.ObjSense.kMaximize)
    h.changeColsCost(n, np.arange(n, dtype=np.int32), score.astype(float))
    for row, lo, hi in zip(ws.A, ws.lo, ws.hi):
        idx = np.nonzero(row)[0].astype(np.int32)
        h.addRow(lo if np.isfinite(lo) else -inf, hi if np.isfinite(hi) else inf, len(idx), idx, row[idx].astype(float))
    G = int(ws.gidx.max()) + 1  # game indicators y_g <= sum_{i in g} x_i, sum_g y_g >= 2 (in-model, no fallback)
    for gg in range(G):
        h.addVar(0.0, 1.0)
    h.changeColsIntegrality(G, np.arange(n, n + G, dtype=np.int32), np.array([highspy.HighsVarType.kInteger] * G))
    for gg in range(G):
        idx = np.concatenate([[n + gg], np.nonzero(ws.gidx == gg)[0]]).astype(np.int32)
        val = np.concatenate([[1.0], -np.ones(len(idx) - 1)])
        h.addRow(-inf, 0.0, len(idx), idx, val)
    h.addRow(2.0, inf, G, np.arange(n, n + G, dtype=np.int32), np.ones(G))
    out, extra = [], []
    for _ in range(N):
        h.run()
        sel = np.nonzero(np.asarray(h.getSolution().col_value)[:n] > 0.5)[0]
        if len(sel) != 8 or len(np.unique(ws.gidx[sel])) < 2:
            break
        lu = tuple(sorted(sel.tolist()))
        out.append(lu)
        vec = np.zeros(n)
        vec[list(lu)] = 1.0
        extra.append((vec, 8.0 - k))
        idx = np.array(lu, dtype=np.int32)
        h.addRow(-inf, 8.0 - k, 8, idx, np.ones(8))
    return out


def greedy(S: np.ndarray, N: int, start: list[int] | None = None) -> list[int]:
    """Greedy max of mean_w max_{i in set} S[i, w]. S: (candidates, worlds)."""
    picked = list(start or [])
    cur = S[picked].max(0) if picked else np.zeros(S.shape[1])
    avail = np.ones(len(S), dtype=bool)
    avail[picked] = False
    while len(picked) < min(N, len(S)):
        gain = np.maximum(S, cur[None]).mean(1)
        gain[~avail] = -np.inf
        j = int(np.argmax(gain))
        picked.append(j)
        avail[j] = False
        cur = np.maximum(cur, S[j])
    return picked


# ---------------------------------------------------------------- one night

def night(item, proj: str, s: float, corr: dict, W: int, W2: int, ks: list[int], na: int, kstar: int | None) -> dict | None:
    sid, g = item
    t0 = time.time()
    g = g.reset_index(drop=True).copy()
    gp = g.assign(pos=g["pos"].map(lambda v: set(v.split(","))))
    actual = g["actual"].to_numpy(dtype=float)
    opt = best_lineup(gp, actual, Rules("nba"))
    if opt is None:
        return None
    optimal = float(actual[opt].sum())
    n = len(g)
    score = g[proj].fillna(0.0).to_numpy(dtype=float)
    seed = int(hashlib.md5(f"{sid}|{proj}".encode()).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)
    ws = WorldSolver(g)
    Nmax = max(NS)

    lus: dict[str, list[tuple]] = {}
    # worlds
    X = draw(g, score, s, corr, W, rng)
    keep = np.concatenate([ws.keep_masks(X[i:i + 50]) for i in range(0, W, 50)])
    wopt, wval = [], np.zeros(W)
    for w in range(W):
        sel = tuple(sorted(ws.solve(X[w], keep[w]).tolist()))
        wopt.append(sel)
        wval[w] = X[w, list(sel)].sum()
    t_w = time.time() - t0
    # (a) on the pool of players in any world optimum or in the projection optimum (speed; live-implementable)
    pool = np.zeros(n, dtype=bool)
    for sel in wopt:
        pool[list(sel)] = True
    pool[ws.solve(score, ws.keep_masks(score[None])[0])] = True
    for k in ks:
        lus[f"a_k{k}"] = successive(ws, score, k, na, pool=pool)
    counts: dict[tuple, int] = {}
    for sel in wopt:
        counts[sel] = counts.get(sel, 0) + 1
    lus["b_freq"] = sorted(counts, key=lambda c: (-counts[c], -score[list(c)].sum()))[:Nmax]
    a_all = list(dict.fromkeys(lu for k in ks for lu in lus[f"a_k{k}"]))

    def mat(cands):
        M = np.zeros((len(cands), n))
        for i, c in enumerate(cands):
            M[i, list(c)] = 1.0
        return M

    # (c) E[max points] on fresh worlds
    cands_c = list(dict.fromkeys(a_all + list(counts)))
    X2 = draw(g, score, s, corr, W2, rng)
    Sc = mat(cands_c) @ X2.T
    lus["c_greedy"] = [cands_c[i] for i in greedy(Sc, Nmax)]
    # (d) E[max points / world optimum] on held-out solved worlds
    h = W // 2
    cands_d = list(dict.fromkeys(a_all + wopt[:h]))
    Sd = (mat(cands_d) @ X[h:].T) / wval[h:][None]
    lus["d_ratio"] = [cands_d[i] for i in greedy(Sd, Nmax)]
    # (e) a_k first half (at most na), then d's greedy; for every k (k* picked on validation)
    for k in ks:
        ak = lus[f"a_k{k}"]
        e = []
        for N in NS:
            start = [cands_d.index(c) for c in ak[: math.ceil(N / 2)]]
            e.append([cands_d[i] for i in greedy(Sd, N, start=start)])
        lus[f"e_mix{k}"] = e  # list per N (not nested)

    out = {"slate": sid, "season": int(g["season"].iloc[0]), "optimal": optimal, "n_players": n,
           "n_distinct_opt": len(counts), "secs": 0.0, "fallbacks": ws.fallbacks, "pool": int(pool.sum()),
           "secs_worlds": t_w}
    for name, L in lus.items():
        if name.startswith("e_mix"):
            for N, eN in zip(NS, L):
                pts = max(actual[list(c)].sum() for c in eN)
                out[f"{name}:N{N}"] = pts / optimal
            continue
        vals = np.array([actual[list(c)].sum() for c in L])
        pv = np.array([score[list(c)].sum() for c in L])
        target = na if name.startswith("a_k") else Nmax
        for N in NS:
            ok = len(vals) > 0 and (len(vals) >= N or N <= target)  # ran out of distinct lineups -> use all
            out[f"{name}:N{N}"] = vals[:N].max() / optimal if ok else np.nan
        out[f"{name}:len"] = len(vals)
        out[f"{name}:proj1"] = float(pv[0]) if len(pv) else np.nan
        out[f"{name}:hit"] = int(len(vals) and (vals.max() >= optimal - 1e-6))
    out["secs"] = time.time() - t0
    print(f"{sid} n={n} pool={int(pool.sum())} worlds {t_w:.1f}s total {out['secs']:.1f}s", flush=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=["val", "test"], required=True)
    ap.add_argument("--proj", default="shipped_main")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--worlds", type=int, default=200)
    ap.add_argument("--fresh", type=int, default=1000)
    ap.add_argument("--na", type=int, default=20, help="(a) sequence length")
    ap.add_argument("--kstar", type=int, default=0)
    ap.add_argument("--s", type=float, default=0.0)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--ks", default="1,2,3,4", help="(a) k values")
    a = ap.parse_args()

    full = pd.read_parquet(SLATES)
    star = allstar_nights(full)
    corr = fit_corr(full[(full["season"] == 2025) & ~full["slate"].isin(star)])
    print("corr (2025):", json.dumps({k: corr[k] for k in ("lam", "mu")}))
    df = load(a.phase)
    if a.s:
        s = a.s
    else:
        s, grid = calibrate_s(load("val"), a.proj)  # always validation rows
        print("sd-multiplier calibration on validation rows (cov80, cov50):", grid, "-> s =", s)
    kstar = a.kstar or None
    groups = list(df.groupby("slate"))
    if a.limit:
        groups = groups[: a.limit]
    t0 = time.time()
    with ProcessPoolExecutor(a.workers) as ex:
        res = list(ex.map(partial(night, proj=a.proj, s=s, corr=corr, W=a.worlds, W2=a.fresh, ks=[int(k) for k in a.ks.split(",")], na=a.na,
                                  kstar=kstar), groups, chunksize=1))
    res = pd.DataFrame([r for r in res if r is not None])
    print(f"{len(res)} nights in {time.time() - t0:.0f}s; mean secs/night {res['secs'].mean():.1f}; fallbacks {res['fallbacks'].sum()}")
    tag = f"{a.phase}_{a.proj}" + (f"_lim{a.limit}" if a.limit else "")
    res.to_csv(ROOT / f"data/cache/oracle/res_portfolio_{tag}.csv", index=False)
    meths = [c.split(":")[0] for c in res.columns if c.endswith(":N1")]
    print(f"\n## {a.phase} {a.proj}: {len(res)} nights, s={s}, k*={kstar}")
    print("| method | " + " | ".join(f"N={N}" for N in NS) + " |")
    print("|---|" + "---:|" * len(NS))
    for m in meths:
        print(f"| {m} | " + " | ".join(f"{100 * res[f'{m}:N{N}'].mean():.2f}" if res[f'{m}:N{N}'].notna().all() else "-" for N in NS) + " |")


if __name__ == "__main__":
    main()
