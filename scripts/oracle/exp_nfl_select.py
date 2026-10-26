"""Experiment "select" (NFL DK classic): keep the engine's projections, change HOW the lineup is chosen.

    python3 scripts/oracle/exp_nfl_select.py [--worlds 3000] [--fresh 3000] [--workers 2] [--pools pre,full] [--indep]
    python3 scripts/oracle/exp_nfl_select.py --baselines     # engine / naive baselines on both pools, all seasons

Pools. ``pre`` (primary) = strictly pre-game pool: every DST + per (slate, team, position) the top rows by the engine's
unconditional mean (QB 1, RB 2, WR 3, TE 1); pool B of the slate builder guarantees those players are in the table
whether or not they played, so membership does not leak "played". ``full`` (secondary) = every row of the table
(pool A membership conditions on the realized snap share).

Simulator (per slate, from the engine's own distributions). A player plays with probability p_play (QB: starts),
drawn independently. Given he plays, his DK points are Q_i(z_i): the quantile function through the engine's p10 / p50
/ p90 (two half-normal pieces in z, sl = (p50 - p10) / 1.2816, sh = (p90 - p50) / 1.2816), floored at 0 (DST: -4) and
shifted by a constant so that its mean equals engine_cond exactly (so the simulated unconditional mean is the engine,
and rule a = "max simulated mean"). The z_i are correlated by a Gaussian copula: within a game, corr(z_i, z_j) depends
only on the pair of depth roles (QB1, RB1, RB2, WR1, WR2, WR3, TE1, DST; full pool adds QB2, RB3, WR4, TE2 = rank >=
2/3/4/2 by engine within team and position) and whether they are teammates or opponents. Each role-pair correlation
is the Pearson correlation of normal-score residuals (z = Q_i^-1(actual), i.e. actual - engine expressed through the
engine's own distribution) over all 2021-2023 pairs of played players in the same game, shrunk by n / (n + 30);
the per-game matrix is projected to the nearest positive-definite correlation (eigenvalue clip). This captures QB <->
own pass catchers (+), RB / QB <-> opposing DST (-), RB1 <-> own DST (+), and the shared game total (QB <-> opposing QB,
DST <-> opposing DST) without imposing a factor structure. Different games are independent.

Per slate: draw W worlds and solve every world's optimal lineup exactly with a salary dynamic program vectorised over
worlds (DPSolver: per-position cardinality knapsacks + max-plus convolution over salary; ~8x faster than one HiGHS
MILP per world, checked equal to the MILP on the first 10 worlds of every slate; a world optimum that uses a single
game is re-solved with dfs.oracle.best_lineup), then choose a lineup by rule (exact MILPs with dfs.oracle.best_lineup
unless noted):

- a_engine:    max sum of engine (= max expected points; the evaluator's engine column);
- b_mode:      the lineup that is the world optimum most often (ties by engine sum);
- b_winprob:   candidates = all distinct world optima + every other rule's lineup; pick the one that is best among the
               candidates in the most of W2 fresh worlds (smoother estimate of "most likely highest-scoring");
- c_lam:       max sum of engine + lambda x P(player in the world-optimal lineup); lambda tuned on 2024;
- d_stack:     max engine s.t. the QB has >= 1 WR/TE teammate;
- e_bringback: max engine s.t. QB + >= 2 WR/TE teammates + >= 1 RB/WR/TE from the QB's opponent;
- f_ratio:     max sum of E[X_i / optimum(world)] = the lineup maximising the expected fraction of the optimum
               (the evaluation metric is linear in the lineup given the world, so its expectation is a column).

Seasons: correlations fitted on 2021-2023; lambda tuned on 2024; test = 2025 and 2026. Outputs (data/cache/oracle):
res_nfl_select.csv (pool pre: slate, season, split, optimal, <rule>:pts, <rule>:pct, <rule>:overlap, diagnostics),
res_nfl_select_full.csv (same, full table), exp_nfl_select.parquet (slate, player_id, select_pin, select_ratio,
select_simmean, select_c for pool pre, and the same with suffix _full), nfl_select_corr.json (fitted correlations);
--indep: res_nfl_select_indep.csv (pool pre, same simulator without the copula); --baselines:
res_nfl_select_baselines_{pre,full}.csv (engine, base_std, base_last3, base_xfp and the two deterministic stack rules
d / e on all 95 slates). Runtime on 2 processes (shared 4-core machine): main ~17 min, --indep ~6 min, --baselines
~6 min.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time

for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):  # 2 worker processes, 1 thread each
    os.environ.setdefault(_v, "1")
from concurrent.futures import ProcessPoolExecutor
from functools import partial
from pathlib import Path

import highspy
import numpy as np
import pandas as pd
from scipy.sparse import csc_matrix
from scipy.stats import norm

from dfs.oracle import Rules, best_lineup, evaluate_slate, paired_bootstrap

ROOT = Path(__file__).resolve().parents[2]
SLATES = ROOT / "data/cache/oracle/nfl_slates.parquet"
OUTDIR = ROOT / "data/cache/oracle"
CAP = {"QB": 1, "RB": 2, "WR": 3, "TE": 1}
TOP_ROLE = {"QB": 2, "RB": 3, "WR": 4, "TE": 2}  # rank >= this shares one role (QB2, RB3, WR4, TE2)
K_DOM = {"QB": 1, "RB": 3, "WR": 4, "TE": 2, "DST": 1}  # max players of the position in a lineup
TRAIN, TUNE, TEST = [2021, 2022, 2023], [2024], [2025, 2026]
LAMBDAS = [0.0, 1.0, 2.5, 5.0, 10.0, 20.0, 40.0, 1e4]
BASE_COLS = ["engine", "base_std", "base_last3", "base_xfp"]
RULES = ["a_engine", "b_mode", "b_winprob", "c_lam", "d_stack", "e_bringback", "f_ratio"]
N0 = 30.0  # correlation shrinkage: r * n / (n + N0)
ZG = norm.ppf((np.arange(512) + 0.5) / 512)


# ---------------------------------------------------------------- pools, roles, marginals

def prepare(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    rk = df.groupby(["slate", "team", "pos"])["engine"].rank(ascending=False, method="first").astype(int)
    df["rk"] = rk
    df["pre"] = (df["pos"] == "DST") | (rk <= df["pos"].map(CAP).fillna(0))
    top = df["pos"].map(TOP_ROLE).fillna(1).astype(int)
    df["role"] = np.where(df["pos"] == "DST", "DST", df["pos"] + np.minimum(rk, top).astype(str))
    p10, p50, p90 = (df[c].to_numpy(dtype=float) for c in ("p10", "p50", "p90"))
    sl, sh = np.maximum((p50 - p10) / 1.2816, 0.0), np.maximum((p90 - p50) / 1.2816, 0.0)
    lo = np.where(df["pos"] == "DST", -4.0, 0.0)
    q = np.maximum(lo[:, None], p50[:, None] + np.where(ZG < 0, sl[:, None], sh[:, None]) * ZG[None])
    df["m_sl"], df["m_sh"], df["m_lo"] = sl, sh, lo
    df["m_shift"] = df["engine_cond"].to_numpy(dtype=float) - q.mean(1)
    # normal-score residual of the actual through the same played distribution (used only to fit correlations)
    r = df["actual"].to_numpy(dtype=float) - df["m_shift"].to_numpy() - p50
    df["z"] = np.clip(np.where(r < 0, r / np.maximum(sl, 0.5), r / np.maximum(sh, 0.5)), -3, 3)
    df["played"] = (df["snap_pct_actual"].fillna(0) > 0) | (df["pos"] == "DST")  # training filter only
    return df


def fit_corr(tr: pd.DataFrame) -> dict:
    """Role-pair Pearson correlations of normal-score residuals within games (played players, train seasons), shrunk
    by n / (n + N0) with n = number of distinct player pairs."""
    x = tr[tr["played"]][["slate", "game", "team", "role", "z"]].reset_index(drop=True)
    x["rid"] = np.arange(len(x))
    p = x.merge(x, on=["slate", "game"], suffixes=("_i", "_j"))
    p = p[p["rid_i"] != p["rid_j"]]
    p["same"] = p["team_i"] == p["team_j"]
    rows = []
    for (ri, rj, same), q in p.groupby(["role_i", "role_j", "same"]):
        n = len(q) / 2.0 if ri == rj else float(len(q))  # same-role pairs appear twice within the group
        r = float(np.corrcoef(q["z_i"], q["z_j"])[0, 1]) if len(q) > 10 else 0.0
        rows.append((ri, rj, same, r * n / (n + N0), n))
    return {f"{ri}|{rj}|{int(s)}": [round(r, 4), int(n)] for ri, rj, s, r, n in rows}


def corr_lookup(corr: dict, ri: str, rj: str, same: bool) -> float:
    v = corr.get(f"{ri}|{rj}|{int(same)}")
    return v[0] if v else 0.0


def game_factors(g: pd.DataFrame, corr: dict | None) -> list[tuple[np.ndarray, np.ndarray]]:
    """Per game: (row indices, Cholesky factor of the role correlation matrix projected to PD)."""
    out = []
    for _, idx in g.groupby("game").indices.items():
        k = len(idx)
        R = np.eye(k)
        if corr is not None:
            roles, teams = g["role"].to_numpy()[idx], g["team"].to_numpy()[idx]
            for a in range(k):
                for b in range(a + 1, k):
                    R[a, b] = R[b, a] = corr_lookup(corr, roles[a], roles[b], teams[a] == teams[b])
            w, V = np.linalg.eigh(R)
            R = (V * np.clip(w, 1e-3, None)) @ V.T
            d = np.sqrt(np.diag(R))
            R = R / np.outer(d, d)
        out.append((np.asarray(idx), np.linalg.cholesky(R)))
    return out


def draw(g: pd.DataFrame, fac, W: int, rng: np.random.Generator) -> np.ndarray:
    """(W, n) DK points: Gaussian copula over the engine's played distributions, times independent play draws."""
    n = len(g)
    Z = np.empty((W, n))
    for idx, L in fac:
        Z[:, idx] = rng.standard_normal((W, len(idx))) @ L.T
    p50, sl, sh = g["p50"].to_numpy(dtype=float), g["m_sl"].to_numpy(), g["m_sh"].to_numpy()
    x = np.maximum(g["m_lo"].to_numpy(), p50 + np.where(Z < 0, sl, sh) * Z) + g["m_shift"].to_numpy()
    x = np.where(g["engine_cond"].to_numpy() > 0, x, 0.0)
    return np.where(rng.random((W, n)) < g["p_play"].to_numpy(dtype=float), x, 0.0)


# ---------------------------------------------------------------- fast exact world solver

class WorldSolver:
    """max score.x over 9 players (QB 1, DST 1, RB >= 2, WR >= 3, TE >= 1), salary <= cap; columns outside ``keep``
    fixed to 0. One HiGHS model per slate reused across worlds. A single-game solution falls back to
    dfs.oracle.best_lineup (game variables) on the kept players."""

    def __init__(self, g: pd.DataFrame, gp: pd.DataFrame, cap: int = 50000):
        self.n = n = len(g)
        self.gp = gp
        pos = g["pos"].to_numpy()
        self.sal = g["salary"].to_numpy().astype(float)
        self.gidx = pd.factorize(g["game"])[0]
        inf = highspy.kHighsInf
        rows = [(np.ones(n), 9, 9), ((pos == "QB") * 1.0, 1, 1), ((pos == "DST") * 1.0, 1, 1),
                ((pos == "RB") * 1.0, 2, inf), ((pos == "WR") * 1.0, 3, inf), ((pos == "TE") * 1.0, 1, inf),
                (self.sal, -inf, float(cap))]
        A = csc_matrix(np.array([r[0] for r in rows]))
        h = highspy.Highs()
        h.setOptionValue("output_flag", False)
        h.setOptionValue("threads", 1)
        h.setOptionValue("mip_rel_gap", 1e-9)
        h.setOptionValue("mip_abs_gap", 1e-7)
        lp = highspy.HighsLp()
        lp.num_col_, lp.num_row_ = n, A.shape[0]
        lp.col_cost_, lp.col_lower_, lp.col_upper_ = np.zeros(n), np.zeros(n), np.ones(n)
        lp.row_lower_ = np.array([float(r[1]) for r in rows])
        lp.row_upper_ = np.array([float(r[2]) for r in rows])
        lp.a_matrix_.format_ = highspy.MatrixFormat.kColwise
        lp.a_matrix_.start_, lp.a_matrix_.index_, lp.a_matrix_.value_ = A.indptr, A.indices, A.data
        lp.integrality_ = [highspy.HighsVarType.kInteger] * n
        lp.sense_ = highspy.ObjSense.kMaximize
        h.passModel(lp)
        self.h, self.cols = h, np.arange(n, dtype=np.int32)
        self.fallbacks = 0
        # d dominates i: same position, salary(d) <= salary(i), (per world) points(d) > points(i). i is in no optimal
        # lineup if it has >= K_pos dominators spanning >= 2 games (one of them is unused and swapping keeps 2 games).
        self.D0 = (pos[:, None] == pos[None, :]) & (self.sal[:, None] <= self.sal[None, :])
        np.fill_diagonal(self.D0, False)
        self.K = np.array([K_DOM[p] for p in pos])
        self.onehot = np.eye(self.gidx.max() + 1, dtype=np.float32)[self.gidx]

    def keep_masks(self, X: np.ndarray) -> np.ndarray:
        D = (self.D0[None] & (X[:, :, None] > X[:, None, :])).astype(np.float32)  # (W, d, i)
        per_game = self.onehot.T[None] @ D
        cnt, span = per_game.sum(1), (per_game > 0).sum(1)
        return ~((cnt >= self.K[None]) & (span >= 2))

    def solve(self, score: np.ndarray, keep: np.ndarray) -> np.ndarray:
        h = self.h
        h.changeColsCost(self.n, self.cols, score.astype(float))
        h.changeColsBounds(self.n, self.cols, np.zeros(self.n), keep.astype(float))
        h.run()
        sel = np.nonzero(np.asarray(h.getSolution().col_value) > 0.5)[0]
        if len(sel) == 9 and len(np.unique(self.gidx[sel])) >= 2:
            return sel
        self.fallbacks += 1
        kidx = np.nonzero(keep)[0]
        lu = best_lineup(self.gp.iloc[kidx].reset_index(drop=True), score[kidx], Rules("nfl"))
        return np.sort(kidx[lu]) if lu is not None else np.array([], dtype=int)


class DPSolver:
    """Exact world optima for many worlds at once by dynamic programming over salary (DK salaries are multiples of
    100, so the cap is 500 units). Per position, a 0/1 knapsack with a cardinality index, F[k, s] = best points of
    exactly k players costing exactly s, is run for all worlds in one vectorised pass (decisions kept for backtracking);
    positions are then combined by max-plus convolution over salary for each FLEX choice (RB3 / WR4 / TE2), the last
    one against a prefix maximum so the cap is "<=". About 10x faster than one MILP per world; solutions that use a
    single game (min 2 games) are re-solved with the MILP fallback."""

    COMBOS = [(3, 3, 1), (2, 4, 1), (2, 3, 2)]  # (RB, WR, TE) counts

    def __init__(self, g: pd.DataFrame, cap: int = 50000):
        sal = g["salary"].to_numpy()
        assert (sal % 100 == 0).all(), "salaries must be multiples of 100"
        self.c = (sal // 100).astype(int)
        self.U = cap // 100
        pos = g["pos"].to_numpy()
        self.ids = {p: np.nonzero(pos == p)[0] for p in K_DOM}
        self.n = len(g)

    def _knap(self, X: np.ndarray, p: str):
        U, kmax, ids = self.U, K_DOM[p], self.ids[p]
        W = len(X)
        F = np.full((W, kmax + 1, U + 1), -np.inf)
        F[:, 0, 0] = 0.0
        T = np.zeros((len(ids), W, kmax + 1, U + 1), dtype=bool)
        for j, i in enumerate(ids):
            c = self.c[i]
            x = X[:, i][:, None]
            for k in range(min(kmax, j + 1), 0, -1):
                cand = F[:, k - 1, :U + 1 - c] + x
                cur = F[:, k, c:]
                t = cand > cur
                np.copyto(cur, cand, where=t)
                T[j, :, k, c:] = t
        return F, T

    def _conv(self, a: np.ndarray, b: np.ndarray):
        """out[s] = max over s1 in support(a) of a[s1] + b[s - s1]; also the maximising s1."""
        W, U = a.shape[0], self.U
        out = np.full((W, U + 1), -np.inf)
        arg = np.zeros((W, U + 1), dtype=np.int16)
        for s1 in np.nonzero(np.isfinite(a[0]))[0]:
            cand = a[:, s1, None] + b[:, :U + 1 - s1]
            cur = out[:, s1:]
            t = cand > cur
            np.copyto(cur, cand, where=t)
            arg[:, s1:][t] = s1
        return out, arg

    def solve_many(self, X: np.ndarray) -> np.ndarray:
        """(W, n) scores -> (W, 9) row indices of each world's optimal lineup (ignoring the 2-game rule)."""
        W, U = len(X), self.U
        F, T = {}, {}
        for p in K_DOM:
            F[p], T[p] = self._knap(X, p)
        a1, arg1 = self._conv(F["DST"][:, 1], F["QB"][:, 1])
        a2 = {kt: self._conv(F["TE"][:, kt], a1) for kt in (1, 2)}
        best = np.full(W, -np.inf)
        choice = np.zeros((W, 6), dtype=int)  # combo, s3, s_wr, s_rb, s_te, s_a1
        rw = np.arange(W)
        for ci, (kr, kw, kt) in enumerate(self.COMBOS):
            a3, arg3 = self._conv(a2[kt][0], F["RB"][:, kr])  # loops over the (QB+DST+TE) salary support
            wr = F["WR"][:, kw]
            pm = np.maximum.accumulate(wr, axis=1)
            pa = np.maximum.accumulate(np.where(wr == pm, np.arange(U + 1), 0), axis=1)
            tot = a3 + pm[:, ::-1]  # s3 + s_wr <= U
            s3 = tot.argmax(1)
            v = tot[rw, s3]
            better = v > best
            best = np.where(better, v, best)
            s_wr = pa[rw, U - s3]
            s_a2 = arg3[rw, s3]
            s_te = a2[kt][1][rw, s_a2]
            new = np.stack([np.full(W, ci), s3, s_wr, s3 - s_a2, s_te, s_a2 - s_te], 1)
            choice = np.where(better[:, None], new, choice)
        s_dst = arg1[rw, choice[:, 5]]
        ks = np.array(self.COMBOS)[choice[:, 0]]
        need = {"QB": (np.ones(W, int), choice[:, 5] - s_dst), "DST": (np.ones(W, int), s_dst),
                "RB": (ks[:, 0], choice[:, 3]), "WR": (ks[:, 1], choice[:, 2]), "TE": (ks[:, 2], choice[:, 4])}
        sel = np.zeros((W, self.n), dtype=bool)
        for p, (k, s) in need.items():
            k, s = k.copy(), s.copy()
            ids = self.ids[p]
            for j in range(len(ids) - 1, -1, -1):
                t = T[p][j, rw, k, s] & (k > 0)
                sel[t, ids[j]] = True
                k -= t
                s -= self.c[ids[j]] * t
            assert (k == 0).all() and (s == 0).all(), "DP backtrack failed"
        out = np.nonzero(sel)[1].reshape(W, 9)
        assert np.allclose(X[rw[:, None], out].sum(1), best), "DP value mismatch"
        return out


# ---------------------------------------------------------------- stack constraints (rows for best_lineup extra)

def stack_rows(g: pd.DataFrame, bringback: bool) -> list[tuple[np.ndarray, float]]:
    pos, team, opp = g["pos"].to_numpy(), g["team"].to_numpy(), g["opp"].to_numpy()
    rows = []
    for t in np.unique(team[pos == "QB"]):
        qb = ((pos == "QB") & (team == t)).astype(float)
        pc = (np.isin(pos, ["WR", "TE"]) & (team == t)).astype(float)
        o = opp[(team == t)][0]
        if bringback:
            rows.append((2 * qb - pc, 0.0))
            rows.append((qb - (np.isin(pos, ["RB", "WR", "TE"]) & (team == o)).astype(float), 0.0))
        else:
            rows.append((qb - pc, 0.0))
    return rows


# ---------------------------------------------------------------- one slate

def slate_run(item, corr: dict | None, W: int, W2: int, tag: str) -> dict:
    sid, g = item
    t0 = time.time()
    g = g.reset_index(drop=True).copy()
    gp = g.assign(pos=g["pos"].map(lambda s: set(s.split(","))))
    rules = Rules("nfl")
    actual = g["actual"].to_numpy(dtype=float)
    opt = best_lineup(gp, actual, rules)
    if opt is None:
        return {"res": None, "players": None}
    optimal = float(actual[opt].sum())
    rng = np.random.default_rng(int(hashlib.md5(f"{sid}|{tag}".encode()).hexdigest()[:8], 16))
    fac = game_factors(g, corr)
    n = len(g)
    X = draw(g, fac, W, rng)
    dp = DPSolver(g)
    sols = np.concatenate([dp.solve_many(X[i:i + 200]) for i in range(0, W, 200)])
    gid = pd.factorize(g["game"])[0]
    single = np.flatnonzero(np.array([len(set(gid[s_])) < 2 for s_ in sols]))
    lineups = [tuple(s_.tolist()) for s_ in sols]
    for w in single:  # min 2 games: exact MILP with game variables
        lineups[w] = tuple(sorted(best_lineup(gp, X[w], rules)))
    ws = WorldSolver(g, gp)  # spot check of the DP against the MILP on the first worlds
    dp_check = max(abs(X[w, list(lineups[w])].sum() - X[w, ws.solve(X[w], np.ones(n, bool))].sum()) for w in range(10))
    counts: dict[tuple, int] = {}
    inopt = np.zeros(n)
    for lu in lineups:
        counts[lu] = counts.get(lu, 0) + 1
        inopt[list(lu)] += 1
    opt_vals = np.array([X[w, list(lu)].sum() for w, lu in enumerate(lineups)])
    ratio = (X / opt_vals[:, None]).sum(0)
    pin, ratio, simmean = inopt / W, ratio / W, X.mean(0)
    eng = g["engine"].to_numpy(dtype=float)

    def canon(score, extra=None):
        lu = best_lineup(gp, np.asarray(score, dtype=float), rules, extra=extra)
        return tuple(sorted(lu)) if lu is not None else None

    lus = {"a_engine": canon(eng), "f_ratio": canon(ratio)}
    top = max(counts.values())
    modes = [k for k, v in counts.items() if v == top]
    lus["b_mode"] = max(modes, key=lambda k: eng[list(k)].sum())
    lus["d_stack"] = canon(eng, stack_rows(g, False)) or lus["a_engine"]
    lus["e_bringback"] = canon(eng, stack_rows(g, True)) or lus["d_stack"]
    lam_lus = {lam: canon(eng + lam * pin) for lam in LAMBDAS}
    cands = list(dict.fromkeys(list(counts) + [v for v in lus.values() if v] + [v for v in lam_lus.values() if v]))
    M = np.zeros((len(cands), n), dtype=np.float32)
    for k, c in enumerate(cands):
        M[k, list(c)] = 1
    win = np.zeros(len(cands))
    for i in range(0, W2, 1000):
        X2 = draw(g, fac, min(1000, W2 - i), rng).astype(np.float32)
        win += np.bincount((M @ X2.T).argmax(0), minlength=len(cands))
    win /= W2
    best = np.flatnonzero(win == win.max())
    lus["b_winprob"] = cands[max(best, key=lambda k: eng[list(cands[k])].sum())]

    a_lu = list(lus["a_engine"])
    out = {"slate": sid, "season": int(g["season"].iloc[0]), "optimal": optimal, "n_players": n,
           "n_distinct_opt": len(counts), "mode_count": top, "winprob_max": float(win.max()),
           "single_game_worlds": len(single), "dp_check": float(dp_check), "secs": time.time() - t0,
           "sim_opt_mean": float(opt_vals.mean()), "pit_opt": float((opt_vals < optimal).mean()),
           "pit_engine_lu": float((X[:, a_lu].sum(1) < actual[a_lu].sum()).mean()),
           "sim_engine_lu_sd": float(X[:, a_lu].sum(1).std())}

    def score(name, lu):
        pts = float(actual[list(lu)].sum())
        out[f"{name}:pts"], out[f"{name}:pct"] = pts, pts / optimal
        out[f"{name}:overlap"] = len(set(lu) & set(opt))
        out[f"{name}:winprob"] = float(win[cands.index(lu)]) if lu in cands else np.nan
        out[f"{name}:proj"] = float(eng[list(lu)].sum())

    for name, lu in lus.items():
        score(name, lu)
    for lam, lu in lam_lus.items():
        score(f"c_{lam:g}", lu)
    players = pd.DataFrame({"slate": sid, "player_id": g["player_id"].to_numpy(), "select_pin": pin,
                            "select_ratio": ratio, "select_simmean": simmean})
    return {"res": out, "players": players}


# ---------------------------------------------------------------- reporting

def report(r: pd.DataFrame, label: str) -> None:
    print(f"\n## {label}: {len(r)} slates")
    print("| rule | % of optimal | points | optimal | overlap (of 9) | proj | sim P(best among cands) |")
    print("|---|---:|---:|---:|---:|---:|---:|")
    for rule in RULES:
        print(f"| {rule} | {100 * r[f'{rule}:pct'].mean():.2f}% | {r[f'{rule}:pts'].mean():.1f} | {r['optimal'].mean():.1f} | "
              f"{r[f'{rule}:overlap'].mean():.2f} | {r[f'{rule}:proj'].mean():.1f} | {r[f'{rule}:winprob'].mean():.4f} |")
    print("\nPaired difference vs a_engine, 95% bootstrap over slates: pp of optimal | points")
    for rule in RULES[1:]:
        m, lo, hi = paired_bootstrap(100 * r[f"{rule}:pct"], 100 * r["a_engine:pct"])
        mp, lop, hip = paired_bootstrap(r[f"{rule}:pts"], r["a_engine:pts"])
        same = (r[f"{rule}:pts"] == r["a_engine:pts"]).mean()
        print(f"- {rule}: {m:+.2f} [{lo:+.2f}, {hi:+.2f}] pp | {mp:+.1f} [{lop:+.1f}, {hip:+.1f}] pts | "
              f"same pts as engine on {100 * same:.0f}% of slates")
    for s, gs in r.groupby("season"):
        print(f"season {s} (n={len(gs)}): " + ", ".join(f"{rule} {100 * gs[f'{rule}:pct'].mean():.2f}%" for rule in RULES))


def _base_one(item):
    sid, g = item
    raw = g.reset_index(drop=True)
    g = raw.assign(pos=raw["pos"].map(lambda s: set(s.split(","))))
    r = evaluate_slate(g, BASE_COLS, Rules("nfl"))
    if "optimal" in r:  # the deterministic stack rules need no simulation: score them on every season too
        actual, eng = g["actual"].to_numpy(dtype=float), g["engine"].to_numpy(dtype=float)
        for name, bb in (("d_stack", False), ("e_bringback", True)):
            lu = best_lineup(g, eng, Rules("nfl"), extra=stack_rows(raw, bb))
            if lu is not None:
                r[f"{name}:pts"] = float(actual[lu].sum())
                r[f"{name}:pct"] = r[f"{name}:pts"] / r["optimal"]
    return {"slate": sid, "season": int(g["season"].iloc[0]), **r}


def baselines(df: pd.DataFrame, workers: int) -> None:
    """Engine and naive baselines on pool pre and the full table, every season (dfs.oracle.evaluate_slate)."""
    for pool, d in (("pre", df[df["pre"]]), ("full", df)):
        with ProcessPoolExecutor(workers) as ex:
            res = pd.DataFrame([r for r in ex.map(_base_one, list(d.groupby("slate"))) if "optimal" in r])
        res.to_csv(OUTDIR / f"res_nfl_select_baselines_{pool}.csv", index=False)
        print(f"\n## baselines, pool {pool}: {len(res)} slates")
        for lab, r in (("all", res), ("2021-2023", res[res["season"] <= 2023]), ("2024", res[res["season"] == 2024]),
                       ("test 2025-2026", res[res["season"] >= 2025])):
            print(f"{lab} (n={len(r)}): " + ", ".join(f"{c} {100 * r[f'{c}:pct'].mean():.2f}% ({r[f'{c}:pts'].mean():.1f} pts)"
                                                      for c in BASE_COLS + ["d_stack", "e_bringback"])
                  + f"; optimal {r['optimal'].mean():.1f}")
            for c in ("d_stack", "e_bringback"):
                m, lo, hi = paired_bootstrap(100 * r[f"{c}:pct"], 100 * r["engine:pct"])
                print(f"    {c} - engine: {m:+.2f} [{lo:+.2f}, {hi:+.2f}] pp")
        te = res[res["season"] >= 2025]
        for c in BASE_COLS[1:] + ["d_stack", "e_bringback"]:
            m, lo, hi = paired_bootstrap(100 * te[f"{c}:pct"], 100 * te["engine:pct"])
            print(f"test {c} - engine: {m:+.2f} [{lo:+.2f}, {hi:+.2f}] pp")
        for s_, gs in res.groupby("season"):
            print(f"season {s_} (n={len(gs)}): " + ", ".join(f"{c} {100 * gs[f'{c}:pct'].mean():.2f}%" for c in BASE_COLS))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--worlds", type=int, default=3000)
    ap.add_argument("--fresh", type=int, default=3000)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--pools", default="pre,full")
    ap.add_argument("--indep", action="store_true", help="ablation: independent players (no copula)")
    ap.add_argument("--limit", type=int, default=0, help="debug: first N slates per season")
    ap.add_argument("--suffix", default="", help="appended to output file names (debug runs)")
    ap.add_argument("--baselines", action="store_true", help="only evaluate engine / naive baselines on both pools")
    a = ap.parse_args()
    suf = ("_indep" if a.indep else "") + a.suffix
    if a.baselines:
        baselines(prepare(pd.read_parquet(SLATES)), a.workers)
        return

    df = prepare(pd.read_parquet(SLATES))
    tr = df[df["season"].isin(TRAIN)]
    corr = fit_corr(tr)
    if not suf:
        (OUTDIR / "nfl_select_corr.json").write_text(json.dumps(corr, indent=0))
    roles = ["QB1", "RB1", "RB2", "WR1", "WR2", "WR3", "TE1", "DST"]
    for same, lab in ((1, "teammates"), (0, "opponents (row = team, col = opponent)")):
        m = pd.DataFrame([[corr_lookup(corr, ri, rj, bool(same)) if (ri != rj or not same) else 1.0 for rj in roles]
                          for ri in roles], index=roles, columns=roles)
        print(f"fitted role correlations, {lab} (2021-2023, shrunk):\n{m.round(2).to_string()}")
    sys.stdout.flush()

    sim = df[df["season"].isin(TUNE + TEST)]
    allres, allplayers = {}, {}
    for pool in a.pools.split(","):
        d = sim[sim["pre"]] if pool == "pre" else sim
        groups = list(d.groupby("slate"))
        if a.limit:
            groups = [x for s in TUNE + TEST for x in [gg for gg in groups if gg[1]["season"].iloc[0] == s][:a.limit]]
        groups.sort(key=lambda x: -len(x[1]))
        t0 = time.time()
        with ProcessPoolExecutor(a.workers) as ex:
            outs = list(ex.map(partial(slate_run, corr=None if a.indep else corr, W=a.worlds, W2=a.fresh,
                                       tag=pool), groups, chunksize=1))
        print(f"\n[{pool}] simulated {len(outs)} slates in {time.time() - t0:.0f}s")
        outs = [o for o in outs if o["res"] is not None]
        res = pd.DataFrame([o["res"] for o in outs]).sort_values("slate").reset_index(drop=True)
        res["split"] = np.where(res["season"].isin(TEST), "test", "tune")
        tu = res[res["split"] == "tune"]
        grid = {lam: tu[f"c_{lam:g}:pct"].mean() for lam in LAMBDAS}
        lam_star = max(grid, key=grid.get)
        print(f"[{pool}] lambda grid on 2024 (mean % of optimal):",
              {f"{k:g}": round(100 * v, 2) for k, v in grid.items()}, "->", f"{lam_star:g}")
        for c in ("pts", "pct", "overlap", "winprob", "proj"):
            res[f"c_lam:{c}"] = res[f"c_{lam_star:g}:{c}"]
        res["lambda_star"] = lam_star
        cols = ["slate", "season", "split", "optimal"] + [f"{r}:{c}" for r in RULES for c in ("pts", "pct", "overlap", "proj", "winprob")]
        cols += [c for c in res.columns if c.startswith("c_") and not c.startswith("c_lam") and c.endswith((":pts", ":pct"))]
        cols += ["lambda_star", "n_players", "n_distinct_opt", "mode_count", "winprob_max", "single_game_worlds", "dp_check", "secs",
                 "sim_opt_mean", "pit_opt", "pit_engine_lu", "sim_engine_lu_sd"]
        name = "res_nfl_select" + ("" if pool == "pre" else "_full") + suf + ".csv"
        res[cols].to_csv(OUTDIR / name, index=False)
        pl = pd.concat([o["players"] for o in outs], ignore_index=True)
        pl = pl.merge(d[["slate", "player_id", "engine"]], on=["slate", "player_id"])
        pl["select_c"] = pl["engine"] + lam_star * pl["select_pin"]
        allres[pool], allplayers[pool] = res, pl.drop(columns="engine")

        te = res[res["split"] == "test"]
        report(res[res["split"] == "tune"], f"[{pool}{suf}] 2024 (tuning)")
        report(te, f"[{pool}{suf}] TEST 2025-2026")
        print(f"[{pool}] lambda* = {lam_star:g}; mean distinct world optima {te['n_distinct_opt'].mean():.1f} of {a.worlds}; "
              f"mean mode count {te['mode_count'].mean():.2f}; mean max win prob {te['winprob_max'].mean():.4f}; "
              f"players/slate {te['n_players'].mean():.0f}; single-game world optima re-solved "
              f"{int(res['single_game_worlds'].sum())}; DP vs MILP max |diff| {res['dp_check'].max():.2e}; "
              f"mean secs/slate {res['secs'].mean():.1f}")
        print(f"[{pool}] lineup-level calibration (test): actual optimum {te['optimal'].mean():.1f} vs simulated optimum mean "
              f"{te['sim_opt_mean'].mean():.1f}; PIT of actual optimum mean {te['pit_opt'].mean():.3f} (uniform 0.5), "
              f"share in [0.1, 0.9] {te['pit_opt'].between(0.1, 0.9).mean():.2f}; PIT of engine lineup actual pts mean "
              f"{te['pit_engine_lu'].mean():.3f}, share in [0.1, 0.9] {te['pit_engine_lu'].between(0.1, 0.9).mean():.2f}; "
              f"sim sd of engine lineup {te['sim_engine_lu_sd'].mean():.1f}")
        tp = d[d["season"].isin(TEST)].merge(pl.drop(columns="engine"), on=["slate", "player_id"])
        for c in ("engine", "select_simmean"):
            e = tp[c] - tp["actual"]
            print(f"[{pool}] player-level test {c}: MAE {e.abs().mean():.3f} RMSE {np.sqrt((e ** 2).mean()):.3f} "
                  f"bias {e.mean():+.3f} (n={len(tp)})")
        sys.stdout.flush()

    if not suf and set(allplayers) == {"pre", "full"}:
        p = allplayers["full"].rename(columns={c: c + "_full" for c in ("select_pin", "select_ratio", "select_simmean", "select_c")})
        p = p.merge(allplayers["pre"], on=["slate", "player_id"], how="left")
        p.to_parquet(OUTDIR / "exp_nfl_select.parquet", index=False)
    elif not suf and "pre" in allplayers:
        allplayers["pre"].to_parquet(OUTDIR / "exp_nfl_select.parquet", index=False)


if __name__ == "__main__":
    main()
