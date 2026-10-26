"""Lineup-level evaluation against the ex-post optimum ("oracle gap").

A slate is a table of players with DK roster eligibility, salary, game, actual DK points and
any number of pre-game score columns (projections). For each slate this module solves, exactly:

- the lineup each score column would have chosen (max sum of that column under the site's rules);
- the ex-post optimal lineup (max sum of *actual* points under the same rules).

The headline metric is ``pct_opt`` = actual points of the chosen lineup / actual points of the
ex-post optimal lineup. 100% means the chosen lineup was the best one possible.

Solver: scipy's HiGHS MILP (exact). Rules: slots with eligible positions, salary cap, one player
once, players from at least ``min_games`` games.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import lil_matrix

ROSTERS = {
    "nba": (["PG", "SG", "SF", "PF", "C", "G", "F", "UTIL"],
            {"PG": {"PG"}, "SG": {"SG"}, "SF": {"SF"}, "PF": {"PF"}, "C": {"C"}, "G": {"PG", "SG"},
             "F": {"SF", "PF"}, "UTIL": {"PG", "SG", "SF", "PF", "C"}}),
    "nfl": (["QB", "RB1", "RB2", "WR1", "WR2", "WR3", "TE", "FLEX", "DST"],
            {"QB": {"QB"}, "RB1": {"RB"}, "RB2": {"RB"}, "WR1": {"WR"}, "WR2": {"WR"}, "WR3": {"WR"},
             "TE": {"TE"}, "FLEX": {"RB", "WR", "TE"}, "DST": {"DST"}}),
    "cfb": (["QB", "RB1", "RB2", "WR1", "WR2", "WR3", "FLEX", "SFLEX"],
            {"QB": {"QB"}, "RB1": {"RB"}, "RB2": {"RB"}, "WR1": {"WR"}, "WR2": {"WR"}, "WR3": {"WR"},
             "FLEX": {"RB", "WR", "TE"}, "SFLEX": {"QB", "RB", "WR", "TE"}}),
}


@dataclass
class Rules:
    sport: str
    cap: int = 50000
    min_games: int = 2


def best_lineup(slate: pd.DataFrame, score: np.ndarray, rules: Rules, extra: list[tuple[np.ndarray, float]] | None = None,
                time_limit: float = 20.0) -> list[int] | None:
    """Row positions of the lineup maximising sum(score). ``slate`` needs columns ``pos`` (set of positions),
    ``salary`` and ``game``. ``extra``: additional (row-vector, upper bound) constraints on the players chosen."""
    slots, elig = ROSTERS[rules.sport]
    n, m = len(slate), len(slots)
    pos = slate["pos"].tolist()
    pairs = [(i, j) for i in range(n) for j, s in enumerate(slots) if pos[i] & elig[s]]
    if not pairs:
        return None
    games = sorted(set(slate["game"]))
    g_index = {g: k for k, g in enumerate(games)}
    nv = len(pairs) + len(games)  # x[i, slot] then y[game]
    c = np.zeros(nv)
    for v, (i, j) in enumerate(pairs):
        c[v] = -float(score[i])
    rows = m + n + 1 + len(games) + 1 + (len(extra) if extra else 0)
    A = lil_matrix((rows, nv))
    lo, hi = np.full(rows, -np.inf), np.full(rows, np.inf)
    sal = slate["salary"].to_numpy()
    gcol = slate["game"].map(g_index).to_numpy()
    for v, (i, j) in enumerate(pairs):
        A[j, v] = 1  # each slot once
        A[m + i, v] = 1  # each player at most once
        A[m + n, v] = sal[i]  # salary cap
        A[m + n + 1 + gcol[i], v] = -1  # y_g <= players from game g
    lo[:m], hi[:m] = 1, 1
    hi[m:m + n] = 1
    hi[m + n] = rules.cap
    for k in range(len(games)):
        A[m + n + 1 + k, len(pairs) + k] = 1
        hi[m + n + 1 + k] = 0
    r = m + n + 1 + len(games)
    for k in range(len(games)):
        A[r, len(pairs) + k] = 1
    lo[r] = rules.min_games
    for e, (vec, ub) in enumerate(extra or []):
        for v, (i, j) in enumerate(pairs):
            if vec[i]:
                A[r + 1 + e, v] = vec[i]
        hi[r + 1 + e] = ub
    res = milp(c, constraints=LinearConstraint(A.tocsr(), lo, hi), integrality=np.ones(nv),
               bounds=Bounds(0, 1), options={"time_limit": time_limit, "disp": False})
    if res.x is None:
        return None
    return sorted(i for v, (i, j) in enumerate(pairs) if res.x[v] > 0.5)


def evaluate_slate(slate: pd.DataFrame, columns: list[str], rules: Rules) -> dict:
    """Actual points of each column's lineup and of the ex-post optimum."""
    actual = slate["actual"].to_numpy(dtype=float)
    opt = best_lineup(slate, actual, rules)
    if opt is None:
        return {}
    best = float(actual[opt].sum())
    out = {"optimal": best, "n_players": len(slate)}
    for col in columns:
        lu = best_lineup(slate, slate[col].fillna(0.0).to_numpy(dtype=float), rules)
        if lu is None:
            continue
        pts = float(actual[lu].sum())
        out[f"{col}:pts"] = pts
        out[f"{col}:pct"] = pts / best if best > 0 else np.nan
        out[f"{col}:overlap"] = len(set(lu) & set(opt))
        out[f"{col}:proj"] = float(slate[col].to_numpy(dtype=float)[lu].sum())
    return out


def paired_bootstrap(a: np.ndarray, b: np.ndarray, n_boot: int = 4000, seed: int = 0) -> tuple[float, float, float]:
    """Mean of a - b with a 95% bootstrap interval over slates."""
    d = np.asarray(a, dtype=float) - np.asarray(b, dtype=float)
    d = d[~np.isnan(d)]
    rng = np.random.default_rng(seed)
    boots = rng.choice(d, size=(n_boot, len(d)), replace=True).mean(axis=1)
    return float(d.mean()), float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))
