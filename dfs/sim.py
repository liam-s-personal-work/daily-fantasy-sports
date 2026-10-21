"""Correlated Monte Carlo of every player's fantasy points (Gaussian copula).

Each player's marginal is the two-piece distribution from ``dfs.distributions``. The
dependence between players comes from a correlation matrix built from pair roles (same
team QB-WR, opposing QBs, RB-own DST, ...), loaded from ``dfs/rules/correlations.yaml``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import yaml
from scipy.stats import norm

from dfs.distributions import sample_from_uniform
from dfs.models import Player, Projection

CORR_FILE = Path(__file__).parent / "rules" / "correlations.yaml"


@lru_cache(maxsize=None)
def load_correlations(sport: str) -> dict:
    data = yaml.safe_load(CORR_FILE.read_text()) if CORR_FILE.exists() else {}
    return (data or {}).get(sport, {})


POS_ALIAS = {"D": "DST", "DEF": "DST"}  # FanDuel / some files name the team defense D or DEF
ROLE_DEPTH = {"nfl": {"QB": 1, "RB": 2, "WR": 3, "TE": 1, "DST": 1, "K": 1},
              "cfb": {"QB": 1, "RB": 2, "WR": 4, "TE": 1}}
DEPTH_WEIGHT = 0.25  # correlation weight for players beyond their team's role depth (backups)


def canonical_pos(position: str, sport: str | None = None) -> str:
    """One name per role for tables keyed by position: FD's team defense 'D' is 'DST' in football."""
    up = (position or "?").upper()
    return POS_ALIAS.get(up, up) if (sport or "nfl") in ("nfl", "cfb") else up


def _pos(p: Player, sport: str = "nfl") -> str:
    return canonical_pos(p.positions[0] if p.positions else "?", sport)


def role_weights(players: list[Player], sport: str, means: list[float] | None) -> np.ndarray:
    """1 for each team's top players at a position (QB1, RB1-2, WR1-3, TE1 ...), DEPTH_WEIGHT for the
    rest. Pair-role correlations describe starters; giving them to every backup makes the matrix
    badly indefinite, and the repair then shrinks the starter correlations that matter for stacks."""
    depth = ROLE_DEPTH.get(sport)
    w = np.ones(len(players))
    if not depth or means is None:
        return w
    groups: dict[tuple[str, str], list[int]] = {}
    for i, p in enumerate(players):
        groups.setdefault((p.team, _pos(p, sport)), []).append(i)
    for (_, pos), idx in groups.items():
        keep = depth.get(pos, 1)
        for rank, i in enumerate(sorted(idx, key=lambda i: -means[i])):
            if rank >= keep:
                w[i] = DEPTH_WEIGHT
    return w


def pair_correlation(a: Player, b: Player, table: dict, sport: str = "nfl") -> float:
    """Look up the correlation for an ordered-insensitive (relation, posA, posB) pair."""
    if a.team == b.team:
        rel = "same_team"
    elif a.game and a.game == b.game:
        rel = "opponent"
    else:
        return 0.0
    rows = table.get(rel, {})
    pa, pb = _pos(a, sport), _pos(b, sport)
    return float(rows.get(f"{pa}-{pb}", rows.get(f"{pb}-{pa}", rows.get("default", 0.0))))


def nearest_psd_correlation(c: np.ndarray, eps: float = 1e-6) -> np.ndarray:
    """Clip negative eigenvalues and rescale to unit diagonal (Higham-style one step)."""
    c = (c + c.T) / 2
    w, v = np.linalg.eigh(c)
    w = np.clip(w, eps, None)
    c2 = (v * w) @ v.T
    d = np.sqrt(np.diag(c2))
    c2 = c2 / np.outer(d, d)
    np.fill_diagonal(c2, 1.0)
    return c2


def build_correlation(players: list[Player], sport: str, table: dict | None = None,
                      means: list[float] | None = None) -> np.ndarray:
    """Pair-role correlations, scaled down for backups (see ``role_weights``), repaired to PSD."""
    table = table if table is not None else load_correlations(sport)
    n = len(players)
    c = np.eye(n)
    if not table:
        return c
    w = role_weights(players, sport, means)
    for i in range(n):
        for j in range(i + 1, n):
            r = pair_correlation(players[i], players[j], table, sport)
            if r:
                c[i, j] = c[j, i] = r * w[i] * w[j]
    if np.linalg.eigvalsh(c)[0] > 1e-6:
        return c
    return nearest_psd_correlation(c)


def simulate(projections: dict[str, Projection], players: dict[str, Player], sport: str,
             n_sims: int = 10000, seed: int = 11, table: dict | None = None,
             floors: dict[str, float] | None = None) -> tuple[list[str], np.ndarray]:
    """Return (player_keys, sims) with sims shaped (n_sims, n_players), float32 points."""
    keys = sorted(k for k in projections if k in players)
    plist = [players[k] for k in keys]
    corr = build_correlation(plist, sport, table, means=[projections[k].mean for k in keys])
    rng = np.random.default_rng(seed)
    chol = np.linalg.cholesky(corr)
    z = rng.standard_normal((n_sims, len(keys))) @ chol.T
    u = norm.cdf(z)
    out = np.empty((n_sims, len(keys)), dtype=np.float32)
    floors = floors or {}
    for j, k in enumerate(keys):
        pr = projections[k]
        out[:, j] = sample_from_uniform(u[:, j], pr.p10, pr.p50, pr.p90, pr.p_zero, floors.get(k))
    return keys, out
