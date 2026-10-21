"""Offline tests for backtest metrics and actual-points conventions."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from dfs import backtest as bt
from dfs.scoring import score


def test_pinball_loss():
    y = np.array([10.0, 10.0])
    assert bt.pinball(y, np.array([12.0, 8.0]), 0.9).tolist() == pytest.approx([0.2, 1.8])
    assert bt.pinball(y, np.array([12.0, 8.0]), 0.5).tolist() == pytest.approx([1.0, 1.0])


def _frame():
    rows = []
    for wk in (1, 2):
        for i in range(6):
            rows.append({"season": 2026, "week": wk, "actual": float(i), "pred": float(i) + (0.5 if wk == 1 else -0.5),
                         "bad": float(5 - i), "p10": i - 1.0, "p50": float(i), "p90": i + 0.5})
    return pd.DataFrame(rows)


def test_metrics_rank_and_topk():
    df = _frame()
    m = bt.metrics(df, "pred", with_quantiles=True)
    assert m["n"] == 12 and m["MAE"] == pytest.approx(0.5) and m["bias"] == pytest.approx(0.0)
    assert m["spearman"] == pytest.approx(1.0) and m["top3"] == pytest.approx(1.0)
    assert m["cover80"] == pytest.approx(1.0)
    b = bt.metrics(df, "bad")
    assert b["spearman"] == pytest.approx(-1.0) and b["top3"] == pytest.approx(0.0)


def test_bootstrap_diff_sign_and_interval():
    df = _frame()
    d, lo, hi = bt.bootstrap_mae_diff(df, "pred", "bad", n_boot=500)
    want = 0.5 - np.mean(np.abs(df["bad"] - df["actual"]))
    assert d == pytest.approx(want) and lo <= d <= hi and hi < 0


def test_dst_points_allowed_conventions():
    row = {"opp_points": 27, "opp_def_tds": 1, "opp_def_safeties": 0, "opp_fd_pa": 14, "opp_sacks_suffered": 3,
           "def_sacks": 2.5, "def_interceptions": 1, "fumble_recovery_opp": 1, "def_tds": 0, "special_teams_tds": 0,
           "def_safeties": 0, "blocks": 0, "def_2pt_made": 0}
    dk = bt.dst_stat_line(pd.Series(row), "draftkings.nfl")
    fd = bt.dst_stat_line(pd.Series(row), "fanduel.nfl")
    assert dk["points_allowed"] == 21  # 27 minus a pick-six scored against the offense (its XP still counts)
    assert fd["points_allowed"] == 14  # formula from the opponent offense only
    assert dk["sack"] == 3  # official team sacks, not the sum credited to defenders
    assert score(dk, "draftkings.nfl", role="dst") == pytest.approx(3 + 2 + 2 + 0)
    assert score(fd, "fanduel.nfl", role="dst") == pytest.approx(3 + 2 + 2 + 1)
    row["opp_fd_pa"] = np.nan  # FD falls back to the final score when the formula inputs are missing
    assert bt.dst_stat_line(pd.Series(row), "fanduel.nfl")["points_allowed"] == 21


def test_weeks_parser():
    assert bt._weeks("3-6") == [3, 4, 5, 6] and bt._weeks("3,5") == [3, 5]
