"""The oracle-gap evaluator: exact lineups under DK rules and the ex-post optimum."""

from __future__ import annotations

import numpy as np
import pandas as pd

from dfs.oracle import Rules, best_lineup, evaluate_slate, paired_bootstrap


def _nba_slate(seed=0, n=40):
    rng = np.random.default_rng(seed)
    pos = [{"PG"}, {"SG"}, {"SF"}, {"PF"}, {"C"}, {"PG", "SG"}, {"SF", "PF"}]
    return pd.DataFrame({"pos": [pos[i % len(pos)] for i in range(n)],
                         "salary": rng.integers(30, 110, n) * 100, "game": [f"G{i % 4}" for i in range(n)],
                         "actual": rng.uniform(0, 60, n), "proj": rng.uniform(5, 50, n)})


def test_best_lineup_is_legal_and_beats_other_legal_lineups():
    s = _nba_slate()
    actual = s["actual"].to_numpy()
    lu = best_lineup(s, actual, Rules("nba"))
    assert len(lu) == 8 and s["salary"].iloc[lu].sum() <= 50000 and s["game"].iloc[lu].nunique() >= 2
    rng = np.random.default_rng(5)
    for _ in range(50):  # legal lineups chosen for random scores never beat the optimum on the actual points
        other = best_lineup(s, rng.uniform(0, 1, len(s)), Rules("nba"))
        assert actual[other].sum() <= actual[lu].sum() + 1e-9


def test_evaluating_the_actual_points_reaches_100_percent():
    s = _nba_slate(1)
    s["perfect"] = s["actual"]
    r = evaluate_slate(s, ["perfect", "proj"], Rules("nba"))
    assert r["perfect:pct"] == 1.0 and 0 < r["proj:pct"] <= 1.0 and r["perfect:overlap"] == 8


def test_cfb_roster_has_a_superflex():
    s = pd.DataFrame({"pos": [{"QB"}] * 4 + [{"RB"}] * 6 + [{"WR"}] * 8, "salary": [5000] * 18,
                      "game": [f"G{i % 3}" for i in range(18)], "actual": np.arange(18, dtype=float)})
    lu = best_lineup(s, s["actual"].to_numpy(), Rules("cfb", cap=50000))
    assert len(lu) == 8 and sum(1 for i in lu if s["pos"].iloc[i] == {"QB"}) <= 2


def test_paired_bootstrap_sign():
    m, lo, hi = paired_bootstrap(np.array([2.0, 3.0, 4.0, 5.0]), np.array([1.0, 1.0, 1.0, 1.0]))
    assert m == 2.5 and lo > 0
