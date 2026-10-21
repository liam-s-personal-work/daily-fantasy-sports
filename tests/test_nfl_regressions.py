"""Offline regression tests for defects found in review of the NFL baseline (synthetic frames only)."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from dfs import backtest as bt
from dfs.data import nflverse as nv
from dfs.projections import nfl as N
from dfs.scoring import score


def test_return_fumbles_lost_are_scored():
    # a kick returner with no scrimmage fumble who lost one on a return (nflverse fumbles_lost_total)
    row = pd.DataFrame([{"receptions": 2, "receiving_yards": 20, "sack_fumbles_lost": 0, "rushing_fumbles_lost": 0,
                         "receiving_fumbles_lost": 0, "fumbles_lost_total": 1}])
    line = N.offense_stat_line(row).iloc[0].to_dict()
    assert line["fumble_lost"] == 1
    assert score(line, "draftkings.nfl", role="offense") == pytest.approx(2 + 2 - 1)
    assert score(line, "fanduel.nfl", role="offense") == pytest.approx(1 + 2 - 2)
    # without the column the scrimmage parts are still used; the total never lowers them
    row2 = row.drop(columns="fumbles_lost_total").assign(rushing_fumbles_lost=1)
    assert N.offense_stat_line(row2).iloc[0]["fumble_lost"] == 1
    assert N.offense_stat_line(row2.assign(fumbles_lost_total=0)).iloc[0]["fumble_lost"] == 1


def test_latest_injury_row_wins_regardless_of_order():
    inj = pd.DataFrame({"season": [2024, 2024, 2024], "week": [15, 15, 15], "gsis_id": ["A", "A", "B"],
                        "report_status": ["Out", "Questionable", "Questionable"],
                        "date_modified": pd.to_datetime(["2024-12-15 13:57", "2024-12-14 20:55", "2024-12-14 20:00"],
                                                        utc=True)})
    for frame in (inj, inj.iloc[::-1]):
        got = nv.latest_injury_rows(frame).set_index("gsis_id")["report_status"]
        assert got.to_dict() == {"A": "Out", "B": "Questionable"}


def _snapshots():
    rows = []
    for dt, qb1 in (("2026-10-01T06:00:00Z", "QB_A"), ("2026-10-10T06:00:00Z", "QB_B"), ("2026-10-20T06:00:00Z", "QB_C")):
        for team, tag in (("LAR", ""), ("KC", "k")):
            rows += [{"dt": dt, "team": team, "player_name": qb1 + tag, "gsis_id": qb1 + tag, "pos_grp": "3WR 1TE",
                      "pos_abb": "QB", "pos_rank": 1},
                     {"dt": dt, "team": team, "player_name": "WRX" + tag, "gsis_id": "WRX" + tag, "pos_grp": "3WR 1TE",
                      "pos_abb": "WR", "pos_rank": 1},
                     {"dt": dt, "team": team, "player_name": "K" + tag, "gsis_id": "K" + tag, "pos_grp": "Special Teams",
                      "pos_abb": "PK", "pos_rank": 1}]
    d = pd.DataFrame(rows)
    return d[~((d["team"] == "KC") & (d["dt"] == "2026-10-10T06:00:00Z"))].reset_index(drop=True)  # KC skipped one


@pytest.mark.parametrize("as_of", [None, "2026-09-01", "2026-10-05", "2026-10-10T06:00:00Z", "2026-10-15", "2026-11-01"])
def test_depth_snapshot_index_matches_full_filter(as_of):
    d = _snapshots()
    want = nv.depth_chart_asof(2026, as_of=as_of, depth=d)
    got = nv.DepthSnapshots(d).asof(2026, as_of)
    pd.testing.assert_frame_equal(got.reset_index(drop=True), want.reset_index(drop=True))


def test_depth_bucket():
    got = N.depth_bucket(["QB", "QB", "QB", "WR", "WR", "WR", "RB", "TE"], [1, 2, 9, 3, 5, 6, 3, 2])
    assert got.tolist() == ["starter", "backup", "other", "starter", "backup", "other", "backup", "backup"]


def test_availability_uses_depth_cell_then_falls_back():
    av = N.AvailabilityModel({("none",): 0.8, ("none", "none"): 0.4, ("WR", "none", "none"): 0.5,
                              ("WR", "none", "none", "none"): 0.52, ("dc", "WR", "none", "none", "starter"): 0.9},
                             counts={})
    assert av.prob("WR", "none", "none", "none", "starter") == pytest.approx(0.9)
    assert av.prob("WR", "none", "none", "none", "backup") == pytest.approx(0.52)  # no depth cell: old lookup
    assert av.prob("WR", "none", "none", "none") == pytest.approx(0.52)


def _qb_ctx(p_dress: list[float] | None) -> N.WeekContext:
    pool = pd.DataFrame({
        "gsis_id": ["Q1", "Q2", "Q3", "W1"], "team": ["AAA"] * 4, "pos": ["QB", "QB", "QB", "WR"],
        "p_active": [0.0, 0.2, 0.06, 0.97], "last_start": [1, 0, 0, 0], "depth_rank": [1, 2, 9, 1],
        "starts_w": [3, 0, 0, 0], "c_tgt": [0, 0, 0, 0.3], "c_rtd": [0, 0, 0, 0.3], "c_car": [0.1, 0.1, 0.1, 0.0],
        "c_utd": [0.1, 0.1, 0.1, 0.0], "q_att": [0.97] * 3 + [0], "q_car": [0.12, 0.1, 0.1, 0],
        "q_utd": [0.1, 0.1, 0.1, 0], "catch": [0.6] * 4, "ypt": [8.0] * 4, "ypc": [4.5] * 4, "comp_pct": [0.64] * 4,
        "fumble_rate": [0.01] * 4, "two_pt_per_td": [0.03] * 4, "st_td_rate": [0.0] * 4, "fum_rec_td_rate": [0.0] * 4,
        "name": list("abcd"), "status": ["ACT"] * 4, "rep": ["out", "none", "none", "none"], "prac": ["none"] * 4,
        "role": ["regular", "none", "none", "regular"]})
    if p_dress is not None:
        pool["p_dress"] = p_dress
    tp = pd.DataFrame([{s: 1.0 for s in N.TEAM_STATS}], index=["AAA"])
    tp.loc["AAA", ["targets", "attempts", "carries", "completions", "passing_yards", "rushing_yards"]] = \
        [33, 35, 26, 22, 240, 115]
    games = pd.DataFrame({"team": ["AAA"], "opponent": ["BBB"], "implied": [21.0]})
    return N.WeekContext(season=2026, week=7, params=N.NFLParams(), games=games, team_pred=tp, pool=pool, vacated={},
                         league={"QB": {"pass_2pt_per_td": 0.03}}, team_league={}, kickers=pd.DataFrame(),
                         fg_bin_probs=np.ones(6) / 6, depth_snapshot=True)


def test_backup_qb_starts_when_starter_out():
    # starter Out; the backup rarely logs a snap (p_active 0.2) but is dressed (p_dress 0.95)
    m = N.player_means(_qb_ctx([0.0, 0.95, 0.25, 0.97])).set_index("gsis_id")
    qb = m.loc[["Q1", "Q2", "Q3"], "p_play"]
    assert qb.sum() == pytest.approx(1.0)
    assert qb["Q1"] == 0.0 and qb["Q2"] > 0.94
    assert (m["p_play"] * m["pass_att"]).sum() == pytest.approx(0.97 * 35, rel=1e-9)  # team passing is conserved
    # without p_dress (older callers) the chain still conditions on someone starting
    m0 = N.player_means(_qb_ctx(None)).set_index("gsis_id")
    assert m0.loc[["Q1", "Q2", "Q3"], "p_play"].sum() == pytest.approx(1.0)


def _baseline_data() -> SimpleNamespace:
    rows = []
    for gid, pos, pts in (("Q_OLD", "QB", [18.0, 22.0]), ("W_OLD", "WR", [10.0, 6.0])):
        for wk, p in zip((1, 2), pts):
            rows.append({"gsis_id": gid, "pos": pos, "season": 2026, "week": wk, "wk": 202600 + wk,
                         "offense_pct": 0.9, "targets": 5, "carries": 1, "attempts": 30 if pos == "QB" else 0,
                         "fp_draftkings.nfl": p, "x_fp": p, "x_rec": 3.0})
    return SimpleNamespace(player_games=pd.DataFrame(rows))


def test_baseline_without_history_falls_back_to_position_median():
    data = _baseline_data()
    ids = pd.Series(["Q_OLD", "Q_NEW", "W_NEW"])
    old = bt.naive_baselines(data, 2026, 3, ids, "draftkings.nfl")
    assert old.loc["Q_NEW", "base_std"] == 0.0  # no position known: documented fallback cannot apply
    got = bt.naive_baselines(data, 2026, 3, ids, "draftkings.nfl", positions=pd.Series(["QB", "QB", "WR"]))
    assert got.loc["Q_OLD", "base_std"] == pytest.approx(20.0)
    assert got.loc["Q_NEW", ["base_std", "base_last3", "base_xfp"]].tolist() == pytest.approx([20.0, 20.0, 20.0])
    assert got.loc["W_NEW", "base_std"] == pytest.approx(8.0)
