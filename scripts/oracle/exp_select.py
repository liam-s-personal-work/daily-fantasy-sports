"""Experiment "select": keep the engine's projections, change HOW the lineup is chosen.

    python3 scripts/oracle/exp_select.py [--worlds 200] [--fresh 2000] [--workers 2]

Simulator (per night, from the engine's own distribution): a player plays with probability 1 - p_zero (drawn
independently); given he plays, his DK points are the engine's two-piece normal (median p50, sd 6.715 + 0.1365 x
engine_cond, skew 0.2, the provider's p10/p90), floored at 0. The normal scores are correlated by a Gaussian
factor model fitted on 2025 residuals (All-Star nights excluded):

    z_i = lam[b_i] * G_game + mu[b_i] * T_team + sqrt(1 - lam^2 - mu^2) * eps_i

b_i is the player's bucket of projected minutes (engine ``minutes``). G is shared by both teams in a game (pace /
garbage time), T by teammates only and signed (bench +, starters -): minutes competition, so a starter and a bench
player on the same team are negatively correlated. lam and mu are least-squares fits to the 2025 opponent-pair and
teammate-pair correlation matrices by bucket (z = PIT of actual points through the engine's played distribution,
played rows only, clipped at +-3).

Each night: draw W worlds, solve each world's optimal lineup exactly (HiGHS MILP; players pruned by an exact
dominance rule: a player with K or more cheaper, position-superset, higher-scoring players spanning 2+ games is in no
optimal lineup, K = number of roster slots those players can fill), then choose a lineup by rule:

- a_engine:  max sum of engine (baseline, = the evaluator's engine column);
- b_mode:    the lineup that is the world optimum most often (ties by engine sum);
- b_winprob: candidates = all distinct world optima + the other rules' lineups; pick the one that is best among
             candidates in the most of a fresh set of worlds (a smoother estimate of P(lineup is the optimum));
- c_lam:     max sum of engine + lambda x P(player in the world-optimal lineup); lambda tuned on 2025;
- d_stack:   max engine with at least 2 players from the game with the highest projected DK total (sum of engine);
- e_ratio:   max sum of E[X_i / optimum(world)] -- the lineup maximising the expected % of the optimum (the
             evaluation metric itself is linear in the lineup, so its expectation is a column);
- f_simmean: max sum of the simulator's mean (engine with the floor at 0) -- a diagnostic.

Outputs: data/cache/oracle/res_select.csv (test nights, seasons 2026-2027: slate, season, optimal, <rule>:pts,
<rule>:pct, <rule>:overlap), data/cache/oracle/exp_select.parquet (per player: select_pin, select_ratio,
select_simmean, select_c; select_split = "test" or "insample2025": 2025 rows used the 2025 correlation fit).
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import os
import sys
import time

for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):  # light: 2 worker processes, 1 thread each
    os.environ.setdefault(_v, "1")
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import highspy
import numpy as np
import pandas as pd
from scipy.optimize import Bounds, LinearConstraint, least_squares, milp
from scipy.sparse import csc_matrix

from dfs.oracle import ROSTERS, Rules, best_lineup, paired_bootstrap
from dfs.projections.nba_provider import NBAParams

ROOT = Path(__file__).resolve().parents[2]
SLATES = ROOT / "data/cache/oracle/nba_slates.parquet"
P = NBAParams()
EDGES = [0, 12, 18, 24, 30, 34, 99]  # buckets of projected minutes
SLOTS, ELIG = ROSTERS["nba"]
LAMBDAS = [0.0, 2.5, 5.0, 10.0, 20.0, 40.0, 80.0, 1e4]
RULES = ["a_engine", "b_mode", "b_winprob", "c_lam", "d_stack", "e_ratio", "f_simmean"]
TRAIN, TEST = [2025], [2026, 2027]


# ---------------------------------------------------------------- engine distribution

def two_piece(cond: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Median and the two half-sds of the engine's played distribution (dfs.distributions.quantiles_from_mean_sd)."""
    sd = np.maximum(P.sd_a + P.sd_b * cond, 0.5)
    s = sd / math.sqrt(1.0 + P.skew ** 2 * (1.0 - 2.0 / math.pi))
    sl, sh = s * (1 - P.skew), s * (1 + P.skew)
    return cond - (sh - sl) / math.sqrt(2 * math.pi), sl, sh


def bucket(minutes: np.ndarray) -> np.ndarray:
    return np.clip(np.searchsorted(EDGES, minutes, side="right") - 1, 0, len(EDGES) - 2)


def allstar_nights(df: pd.DataFrame) -> set[str]:
    n = df.groupby("team")["slate"].nunique()
    return set(df[df["team"].map(n) < 20]["slate"])


# ---------------------------------------------------------------- correlation fit (2025 only)

def pair_matrices(df: pd.DataFrame) -> dict:
    """Teammate and opponent pair correlations of played players' normal scores, by minutes bucket."""
    p50, sl, sh = two_piece(df["engine_cond"].to_numpy())
    a = df["actual"].to_numpy()
    x = df.assign(z=np.clip(np.where(a < p50, (a - p50) / sl, (a - p50) / sh), -3, 3), b=bucket(df["minutes"].to_numpy()))
    x = x[x["minutes_actual"] > 0].copy()
    x["z"] = x.groupby("b")["z"].transform(lambda v: (v - v.mean()) / v.std())
    K = len(EDGES) - 1
    key = ["slate", "game", "team"]
    S = x.pivot_table(index=key, columns="b", values="z", aggfunc="sum", fill_value=0.0).reindex(columns=range(K), fill_value=0.0)
    idx = S.index.to_frame(index=False)
    C = x.pivot_table(index=key, columns="b", values="z", aggfunc="count", fill_value=0).reindex(columns=range(K), fill_value=0)
    Q = x.assign(z2=x["z"] ** 2).pivot_table(index=key, columns="b", values="z2", aggfunc="sum", fill_value=0.0).reindex(columns=range(K), fill_value=0.0)
    S, C, Q = S.to_numpy(), C.to_numpy().astype(float), Q.to_numpy()
    tm, tn = np.zeros((K, K)), np.zeros((K, K))
    for k in range(K):
        for l in range(K):
            if k == l:
                tm[k, l], tn[k, l] = ((S[:, k] ** 2 - Q[:, k]).sum() / 2), (C[:, k] * (C[:, k] - 1)).sum() / 2
            else:
                tm[k, l], tn[k, l] = (S[:, k] * S[:, l]).sum(), (C[:, k] * C[:, l]).sum()
    pairs = [v for v in idx.groupby(["slate", "game"]).indices.values() if len(v) == 2]
    A, B = np.array([p[0] for p in pairs]), np.array([p[1] for p in pairs])
    om, on = np.zeros((K, K)), np.zeros((K, K))
    for k in range(K):
        for l in range(K):
            om[k, l] = (S[A, k] * S[B, l]).sum() + (S[B, k] * S[A, l]).sum()
            on[k, l] = (C[A, k] * C[B, l]).sum() + (C[B, k] * C[A, l]).sum()
    return {"tm": tm / tn, "tn": tn, "op": om / on, "on": on}


def fit_corr(df: pd.DataFrame) -> dict:
    m = pair_matrices(df)
    K = len(EDGES) - 1
    iu = np.triu_indices(K)
    wt, wo = np.sqrt(m["tn"][iu]), np.sqrt(m["on"][iu])

    def resid(th):
        lam, mu = th[:K], th[K:]
        return np.concatenate([wo * (m["op"][iu] - np.outer(lam, lam)[iu]),
                               wt * (m["tm"][iu] - np.outer(lam, lam)[iu] - np.outer(mu, mu)[iu])])
    th0 = np.concatenate([np.full(K, 0.15), np.linspace(0.2, -0.2, K)])
    r = least_squares(resid, th0, bounds=(-0.7, 0.7))
    lam, mu = r.x[:K], r.x[K:]
    if lam.sum() < 0:
        lam = -lam
    if mu[0] < 0:
        mu = -mu
    return {"edges": EDGES, "lam": lam.round(4).tolist(), "mu": mu.round(4).tolist(),
            "tm": m["tm"].round(4).tolist(), "op": m["op"].round(4).tolist()}


# ---------------------------------------------------------------- fast exact world solver

def hall_rows(types: list[str]) -> list[tuple[set[int], int]]:
    """Hall-condition constraints for filling the 8 NBA slots from players of these position types: for every set
    T of types, (# chosen of types in T) <= # slots any of them can fill. With exactly 8 chosen these are equivalent
    to a feasible slot assignment. Implied constraints are dropped."""
    nb = [frozenset(s for s in SLOTS if set(t.split(",")) & ELIG[s]) for t in types]
    out = []
    for r in range(1, len(types) + 1):
        for T in itertools.combinations(range(len(types)), r):
            cap = len(frozenset().union(*[nb[t] for t in T]))
            if cap < len(SLOTS):
                out.append((set(T), cap))
    return [(T, c) for T, c in out if not any(T < T2 and c2 <= c for T2, c2 in out)]


class WorldSolver:
    """max score.x over 8 players (Hall rows), salary <= cap; columns outside ``keep`` fixed to 0. One HiGHS model per
    night, reused across worlds. A single-game solution (min 2 games) falls back to a model with game variables."""

    def __init__(self, g: pd.DataFrame, cap: int = 50000):
        self.n = n = len(g)
        types = sorted(set(g["pos"]))
        self.tidx = g["pos"].map({t: i for i, t in enumerate(types)}).to_numpy()
        self.sal = g["salary"].to_numpy().astype(float)
        self.gidx = pd.factorize(g["game"])[0]
        self.cap = cap
        rows, lo, hi = [np.ones(n), self.sal], [8.0, -highspy.kHighsInf], [8.0, float(cap)]
        for T, c in hall_rows(types):
            v = np.isin(self.tidx, list(T)).astype(float)
            if v.sum() > c:
                rows.append(v); lo.append(-highspy.kHighsInf); hi.append(float(c))
        self.A = np.array(rows); self.lo = np.array(lo); self.hi = np.array(hi)
        A = csc_matrix(self.A)
        h = highspy.Highs()
        h.setOptionValue("output_flag", False)
        h.setOptionValue("threads", 1)
        h.setOptionValue("mip_rel_gap", 1e-9)
        h.setOptionValue("mip_abs_gap", 1e-7)
        lp = highspy.HighsLp()
        lp.num_col_, lp.num_row_ = n, A.shape[0]
        lp.col_cost_, lp.col_lower_, lp.col_upper_ = np.zeros(n), np.zeros(n), np.ones(n)
        lp.row_lower_, lp.row_upper_ = self.lo, self.hi
        lp.a_matrix_.format_ = highspy.MatrixFormat.kColwise
        lp.a_matrix_.start_, lp.a_matrix_.index_, lp.a_matrix_.value_ = A.indptr, A.indices, A.data
        lp.integrality_ = [highspy.HighsVarType.kInteger] * n
        lp.sense_ = highspy.ObjSense.kMaximize
        h.passModel(lp)
        self.h, self.cols = h, np.arange(n, dtype=np.int32)
        self.fallbacks = 0
        # dominance: d dominates i if pos(d) >= pos(i), salary(d) <= salary(i) and (per world) points(d) > points(i)
        pos = [set(s.split(",")) for s in g["pos"]]
        sup = np.array([[pd_ >= pi for pi in pos] for pd_ in pos])
        self.D0 = sup & (self.sal[:, None] <= self.sal[None, :])
        np.fill_diagonal(self.D0, False)
        slot_ok = np.array([[bool(p & ELIG[s]) for s in SLOTS] for p in pos])
        self.K = np.array([slot_ok[sup[:, i]].any(0).sum() for i in range(n)])  # slots i's dominators can occupy
        self.onehot = np.eye(self.gidx.max() + 1, dtype=np.float32)[self.gidx]

    def keep_masks(self, X: np.ndarray) -> np.ndarray:
        """X (W, n) -> (W, n) bool: players that can be in an optimal lineup of that world (exact pruning)."""
        D = (self.D0[None] & (X[:, :, None] > X[:, None, :])).astype(np.float32)  # (W, d, i)
        per_game = self.onehot.T[None] @ D  # (W, games, i): dominators of i by game
        cnt, span = per_game.sum(1), (per_game > 0).sum(1)
        return ~((cnt >= self.K[None]) & (span >= 2))

    def solve(self, score: np.ndarray, keep: np.ndarray | None = None, extra=None) -> np.ndarray:
        if keep is None:
            keep = np.ones(self.n, dtype=bool)
        if extra:
            return self._solve_games(score, keep, extra)
        h = self.h
        h.changeColsCost(self.n, self.cols, score.astype(float))
        h.changeColsBounds(self.n, self.cols, np.zeros(self.n), keep.astype(float))
        h.run()
        sel = np.nonzero(np.asarray(h.getSolution().col_value) > 0.5)[0]
        if len(sel) == 8 and len(np.unique(self.gidx[sel])) >= 2:
            return sel
        self.fallbacks += 1
        return self._solve_games(score, keep)

    def _solve_games(self, score, keep, extra=None):
        """Same model plus game indicators (min 2 games) and optional extra (vector, upper bound) rows."""
        n, G = self.n, self.gidx.max() + 1
        Ag = np.zeros((G + 1, n + G))
        Ag[np.arange(G), n + np.arange(G)] = 1
        Ag[self.gidx, np.arange(n)] = -1
        Ag[G, n:] = 1
        ex = [np.concatenate([vec, np.zeros(G)]) for vec, _ in (extra or [])]
        A = np.vstack([np.hstack([self.A, np.zeros((len(self.A), G))]), Ag] + ([np.array(ex)] if ex else []))
        lo = np.concatenate([self.lo, np.full(G, -np.inf), [2], np.full(len(ex), -np.inf)])
        hi = np.concatenate([self.hi, np.zeros(G), [np.inf], [ub for _, ub in (extra or [])]])
        ub = np.concatenate([keep.astype(float), np.ones(G)])
        r = milp(-np.concatenate([score, np.zeros(G)]), constraints=LinearConstraint(A, lo, hi), integrality=np.ones(n + G),
                 bounds=Bounds(0, ub), options={"disp": False, "mip_rel_gap": 1e-9})
        return np.nonzero(r.x[:n] > 0.5)[0] if r.x is not None else np.array([], dtype=int)


# ---------------------------------------------------------------- simulator

def draw(g: pd.DataFrame, corr: dict, W: int, rng: np.random.Generator) -> np.ndarray:
    """(W, n) correlated DK points from the engine's distribution."""
    cond = g["engine_cond"].to_numpy()
    p50, sl, sh = two_piece(cond)
    b = bucket(g["minutes"].to_numpy())
    lam, mu = np.asarray(corr["lam"])[b], np.asarray(corr["mu"])[b]
    gi = pd.factorize(g["game"])[0]
    ti = pd.factorize(g["team"])[0]
    z = (lam * rng.standard_normal((W, gi.max() + 1))[:, gi] + mu * rng.standard_normal((W, ti.max() + 1))[:, ti]
         + np.sqrt(np.clip(1 - lam ** 2 - mu ** 2, 0, 1)) * rng.standard_normal((W, len(g))))
    x = np.maximum(0.0, p50 + np.where(z < 0, sl, sh) * z)
    return x * (rng.random((W, len(g))) >= g["p_zero"].to_numpy())


def night(item, corr: dict, W: int, W2: int) -> dict:
    sid, g = item
    t0 = time.time()
    g = g.reset_index(drop=True).copy()
    gp = g.assign(pos=g["pos"].map(lambda s: set(s.split(","))))
    rules = Rules("nba")
    if best_lineup(gp, g["actual"].to_numpy(dtype=float), rules) is None:  # no legal lineup (evaluator skips it too)
        return {"res": None, "players": None}
    seed = int(hashlib.md5(str(sid).encode()).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)
    ws = WorldSolver(g)
    X = draw(g, corr, W, rng)
    keep = np.concatenate([ws.keep_masks(X[i:i + 50]) for i in range(0, W, 50)])
    n = len(g)
    counts: dict[tuple, int] = {}
    inopt = np.zeros(n)
    ratio = np.zeros(n)
    opt_vals = np.zeros(W)
    for w in range(W):
        sel = ws.solve(X[w], keep[w])
        key = tuple(sel.tolist())
        counts[key] = counts.get(key, 0) + 1
        inopt[sel] += 1
        opt_vals[w] = X[w, sel].sum()
        ratio += X[w] / opt_vals[w]
    pin, ratio = inopt / W, ratio / W
    simmean = X.mean(0)
    eng = g["engine"].to_numpy()

    def canon(score):  # dfs.oracle's solver: the evaluator's lineup for this column
        lu = best_lineup(gp, np.asarray(score, dtype=float), rules)
        return tuple(sorted(lu)) if lu is not None else None

    def lineup(score, extra=None):  # same rules, fast exact solver (dominance pruning is exact without extra rows)
        score = np.asarray(score, dtype=float)
        keep = None if extra else ws.keep_masks(score[None])[0]
        return tuple(ws.solve(score, keep, extra=extra).tolist())

    lus = {"a_engine": canon(eng), "e_ratio": lineup(ratio), "f_simmean": lineup(simmean)}
    top = max(counts.values())
    modes = [k for k, v in counts.items() if v == top]
    lus["b_mode"] = max(modes, key=lambda k: eng[list(k)].sum())
    gtot = g.groupby("game")["engine"].sum()
    hot = (g["game"] == gtot.idxmax()).to_numpy().astype(float)
    lus["d_stack"] = lineup(eng, extra=[(-hot, -2.0)]) or lus["a_engine"]
    lam_lus = {lam: lineup(eng + lam * pin) for lam in LAMBDAS}
    # candidate re-simulation: P(best among candidates) on fresh worlds
    cands = list(dict.fromkeys(list(counts) + [v for v in lus.values() if v] + [v for v in lam_lus.values() if v]))
    M = np.zeros((len(cands), n))
    for k, c in enumerate(cands):
        M[k, list(c)] = 1
    X2 = draw(g, corr, W2, rng)
    S = M @ X2.T  # (cands, W2)
    win = np.bincount(S.argmax(0), minlength=len(cands)) / W2
    best = np.flatnonzero(win == win.max())
    lus["b_winprob"] = cands[max(best, key=lambda k: eng[list(cands[k])].sum())]

    actual = g["actual"].to_numpy()
    opt = canon(actual)
    optimal = float(actual[list(opt)].sum())
    opt_check = float(actual[list(lineup(actual))].sum()) - optimal  # fast solver vs dfs.oracle (should be 0)
    eng_check = float(eng[list(lam_lus[0.0])].sum() - eng[list(lus["a_engine"])].sum())
    out = {"slate": sid, "season": int(g["season"].iloc[0]), "optimal": optimal, "n_players": n,
           "n_distinct_opt": len(counts), "mode_count": top, "winprob_max": float(win.max()),
           "mean_keep": float(keep.sum(1).mean()), "fallbacks": ws.fallbacks, "secs": time.time() - t0,
           "sim_opt_mean": float(opt_vals.mean()),
           "opt_check": opt_check, "eng_check": eng_check}

    def score(name, lu):
        pts = float(actual[list(lu)].sum())
        out[f"{name}:pts"], out[f"{name}:pct"] = pts, pts / optimal
        out[f"{name}:overlap"] = len(set(lu) & set(opt))
        out[f"{name}:winprob"] = float(win[cands.index(lu)]) if lu in cands else np.nan

    for name, lu in lus.items():
        score(name, lu)
    for lam, lu in lam_lus.items():
        score(f"c_{lam:g}", lu)
    players = pd.DataFrame({"slate": sid, "athlete_id": g["athlete_id"].to_numpy(), "select_pin": pin,
                            "select_ratio": ratio, "select_simmean": simmean})
    return {"res": out, "players": players}


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--worlds", type=int, default=200)
    ap.add_argument("--fresh", type=int, default=2000)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--limit", type=int, default=0, help="debug: first N nights per season")
    ap.add_argument("--out", default=str(ROOT / "data/cache/oracle/exp_select.parquet"))
    ap.add_argument("--res", default=str(ROOT / "data/cache/oracle/res_select.csv"))
    ap.add_argument("--res-train", default="", help="optional csv for the 2025 (tuning) nights")
    a = ap.parse_args()

    df = pd.read_parquet(SLATES)
    star = allstar_nights(df)
    train = df[df["season"].isin(TRAIN) & ~df["slate"].isin(star)]
    corr = fit_corr(train)
    print("All-Star nights (excluded from the fit only):", sorted(star))
    print("correlation fit (2025):", json.dumps({k: corr[k] for k in ("edges", "lam", "mu")}))
    lam, mu = np.array(corr["lam"]), np.array(corr["mu"])
    print("fitted opponent corr:\n", np.round(np.outer(lam, lam), 3), "\nempirical:\n", np.round(np.array(corr["op"]), 3))
    print("fitted teammate corr:\n", np.round(np.outer(lam, lam) + np.outer(mu, mu), 3), "\nempirical:\n",
          np.round(np.array(corr["tm"]), 3))
    sys.stdout.flush()

    sel = df[df["season"].isin(TRAIN + TEST)]
    groups = list(sel.groupby("slate"))
    if a.limit:
        groups = [x for s in TRAIN + TEST for x in [gg for gg in groups if gg[1]["season"].iloc[0] == s][:a.limit]]
    t0 = time.time()
    from functools import partial
    with ProcessPoolExecutor(a.workers) as ex:
        outs = list(ex.map(partial(night, corr=corr, W=a.worlds, W2=a.fresh), groups, chunksize=2))
    print(f"simulated {len(outs)} nights in {time.time() - t0:.0f}s")
    skipped = [sid for (sid, _), o in zip(groups, outs) if o["res"] is None]
    print("nights with no legal lineup (skipped, as the evaluator does):", skipped)
    outs = [o for o in outs if o["res"] is not None]
    res = pd.DataFrame([o["res"] for o in outs])
    players = pd.concat([o["players"] for o in outs], ignore_index=True)

    tr, te = res[res["season"].isin(TRAIN)], res[res["season"].isin(TEST)]
    grid = {lam: tr[f"c_{lam:g}:pct"].mean() for lam in LAMBDAS}
    lam_star = max(grid, key=grid.get)
    print("lambda grid on 2025 (mean % of optimal):", {f"{k:g}": round(100 * v, 2) for k, v in grid.items()}, "->", lam_star)
    for c in ("pts", "pct", "overlap", "winprob"):
        res[f"c_lam:{c}"] = res[f"c_{lam_star:g}:{c}"]
    tr, te = res[res["season"].isin(TRAIN)], res[res["season"].isin(TEST)]

    cols = ["slate", "season", "optimal"] + [f"{r}:{c}" for r in RULES for c in ("pts", "pct", "overlap", "winprob")]
    cols += [c for c in res.columns if c.startswith("c_") and not c.startswith("c_lam") and c.endswith((":pts", ":pct"))]
    cols += ["n_players", "n_distinct_opt", "mode_count", "winprob_max", "mean_keep", "fallbacks", "secs", "sim_opt_mean",
             "opt_check", "eng_check"]
    te[cols].to_csv(a.res, index=False)
    if a.res_train:
        tr[cols].to_csv(a.res_train, index=False)

    df_s = sel.merge(players, on=["slate", "athlete_id"], how="left")
    df_s["select_c"] = df_s["engine"] + lam_star * df_s["select_pin"]
    df_s["select_split"] = np.where(df_s["season"].isin(TEST), "test", "insample2025")
    df_s[["slate", "athlete_id", "select_pin", "select_ratio", "select_simmean", "select_c", "select_split"]].to_parquet(a.out)

    def report(r, label):
        print(f"\n## {label}: {len(r)} nights")
        print("| rule | % of optimal | points | optimal | overlap | exact hits | >=90% | P(best) est. |")
        print("|---|---:|---:|---:|---:|---:|---:|---:|")
        for rule in RULES:
            p = r[f"{rule}:pct"]
            print(f"| {rule} | {100 * p.mean():.2f}% | {r[f'{rule}:pts'].mean():.1f} | {r['optimal'].mean():.1f} | "
                  f"{r[f'{rule}:overlap'].mean():.2f} | {(p > 0.99999).sum()} | {(p >= 0.9).mean():.3f} | "
                  f"{r[f'{rule}:winprob'].mean():.4f} |")
        print("\nPaired difference vs a_engine (pp of optimal, 95% bootstrap over nights):")
        for rule in RULES[1:]:
            m, lo, hi = paired_bootstrap(100 * r[f"{rule}:pct"], 100 * r["a_engine:pct"])
            print(f"- {rule}: {m:+.2f} [{lo:+.2f}, {hi:+.2f}]")
        for s, gs in r.groupby("season"):
            print(f"season {s}: " + ", ".join(f"{rule} {100 * gs[f'{rule}:pct'].mean():.2f}%" for rule in RULES) + f" (n={len(gs)})")

    report(tr, "2025 (tuning, in-sample)")
    report(te, "TEST 2026+2027")
    print(f"\nlambda* = {lam_star:g}; mean distinct world optima {te['n_distinct_opt'].mean():.1f} of {a.worlds}; "
          f"mean mode count {te['mode_count'].mean():.2f}; mean kept players {te['mean_keep'].mean():.1f}; "
          f"fallbacks {int(res['fallbacks'].sum())}; mean secs/night {res['secs'].mean():.1f}; "
          f"solver check |opt diff| max {res['opt_check'].abs().max():.4f}, engine-objective diff max {res['eng_check'].abs().max():.4f}")
    pl = sel[sel["season"].isin(TEST)].merge(players, on=["slate", "athlete_id"])
    for c in ("engine", "select_simmean"):
        e = pl[c] - pl["actual"]
        print(f"player-level test {c}: MAE {e.abs().mean():.3f} RMSE {np.sqrt((e ** 2).mean()):.3f} bias {e.mean():+.3f} (n={len(pl)})")


if __name__ == "__main__":
    main()
