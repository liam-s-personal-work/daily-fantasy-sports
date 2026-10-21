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


def _pos(p: Player) -> str:
    return p.positions[0] if p.positions else "?"


def pair_correlation(a: Player, b: Player, table: dict) -> float:
    """Look up the correlation for an ordered-insensitive (relation, posA, posB) pair."""
    if a.team == b.team:
        rel = "same_team"
    elif a.game and a.game == b.game:
        rel = "opponent"
    else:
        return 0.0
    rows = table.get(rel, {})
    pa, pb = _pos(a), _pos(b)
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


def build_correlation(players: list[Player], sport: str, table: dict | None = None) -> np.ndarray:
    table = table if table is not None else load_correlations(sport)
    n = len(players)
    c = np.eye(n)
    if not table:
        return c
    for i in range(n):
        for j in range(i + 1, n):
            r = pair_correlation(players[i], players[j], table)
            if r:
                c[i, j] = c[j, i] = r
    return nearest_psd_correlation(c)


def simulate(projections: dict[str, Projection], players: dict[str, Player], sport: str,
             n_sims: int = 10000, seed: int = 11, table: dict | None = None,
             floors: dict[str, float] | None = None) -> tuple[list[str], np.ndarray]:
    """Return (player_keys, sims) with sims shaped (n_sims, n_players), float32 points."""
    keys = sorted(k for k in projections if k in players)
    plist = [players[k] for k in keys]
    corr = build_correlation(plist, sport, table)
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
