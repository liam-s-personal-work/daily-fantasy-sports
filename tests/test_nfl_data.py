"""Offline tests for team aliases, nflverse point-in-time helpers and the parquet cache policy."""

from __future__ import annotations

import os
import time

import numpy as np
import pandas as pd
import pytest

from dfs.data import nflverse as nv
from dfs.data.teams import canonicalize, is_team, site_code, split_game


@pytest.mark.parametrize("raw,code", [
    ("LAR", "LA"), ("LA", "LA"), ("STL", "LA"), ("JAC", "JAX"), ("jax", "JAX"), ("WSH", "WAS"), ("WAS", "WAS"),
    ("OAK", "LV"), ("LVR", "LV"), ("SD", "LAC"), ("GNB", "GB"), ("KAN", "KC"), ("NWE", "NE"), ("NOR", "NO"),
    ("SFO", "SF"), ("TAM", "TB"), ("ARZ", "ARI"), ("Rams", "LA"), ("Titans ", "TEN"), ("Baltimore Ravens", "BAL"),
    ("49ers", "SF"), ("Ravens DST", "BAL"), ("Commanders", "WAS"), ("Kansas City", "KC"),
])
def test_canonicalize(raw, code):
    assert canonicalize(raw) == code


def test_canonicalize_unknown_and_ambiguous():
    with pytest.raises(KeyError):
        canonicalize("XYZ")
    with pytest.raises(KeyError):
        canonicalize("New York")  # two teams: must not guess
    assert canonicalize("XYZ", strict=False) == "XYZ"
    assert is_team("LAR") and not is_team("ZZZ")


def test_site_code_and_game():
    assert site_code("LA", "draftkings") == "LAR"
    assert site_code("JAX", "draftkings") == "JAX"
    assert split_game("CHI@ATL 10/18/2026 01:00PM ET") == ("CHI", "ATL")
    assert split_game("ARI@LAR") == ("ARI", "LA")


def test_implied_points_sign_convention():
    # spread_line > 0: HOME favoured by that many points
    home, away = nv.implied_points([3.0, -7.0], [48.0, 44.0])
    assert home.tolist() == [25.5, 18.5]
    assert away.tolist() == [22.5, 25.5]


def _schedule():
    return pd.DataFrame({
        "game_id": ["2026_01_AAA_BBB", "2026_02_BBB_AAA"], "season": [2026, 2026], "week": [1, 2],
        "game_type": ["REG", "REG"], "gameday": ["2026-09-13", "2026-09-20"], "gametime": ["13:00", "16:25"],
        "away_team": ["LAR", "KC"], "home_team": ["KC", "LA"], "away_score": [20, np.nan], "home_score": [27, np.nan],
        "spread_line": [3.5, -1.0], "total_line": [47.5, 50.0], "roof": ["outdoors", "dome"],
        "result": [7, np.nan], "total": [47, np.nan], "overtime": [0, np.nan],
    })


def test_team_games_rows_and_implied():
    tg = nv.team_games(_schedule())
    assert len(tg) == 4
    kc1 = tg[(tg.week == 1) & (tg.team == "KC")].iloc[0]
    la1 = tg[(tg.week == 1) & (tg.team == "LA")].iloc[0]
    assert kc1.implied == pytest.approx(25.5) and kc1.opp_implied == pytest.approx(22.0)
    assert la1.implied == pytest.approx(22.0) and la1.team_spread == -3.5
    assert kc1.points == 27 and la1.points == 20
    # implied points always add up to the total
    assert np.allclose(tg.implied + tg.opp_implied, tg.total_line)


def test_before_excludes_target_week_and_week_schedule_hides_scores():
    df = pd.DataFrame({"season": [2025, 2025, 2026, 2026, 2026], "week": [17, 20, 1, 2, 3], "x": range(5)})
    got = nv.before(df, 2026, 2)
    assert got["x"].tolist() == [0, 1, 2]
    assert nv.before(df, 2026, 2, min_season=2026)["x"].tolist() == [2]
    ws = nv.week_schedule(2026, 1, _schedule())
    assert len(ws) == 1 and not {"home_score", "away_score", "result"} & set(ws.columns)


def test_depth_chart_asof_snapshot_format():
    rows = []
    for dt, qb1 in (("2026-10-01T06:00:00Z", "QB_A"), ("2026-10-10T06:00:00Z", "QB_B"), ("2026-10-20T06:00:00Z", "QB_C")):
        rows += [{"dt": dt, "team": "LAR", "player_name": qb1, "gsis_id": qb1, "pos_grp": "3WR 1TE", "pos_abb": "QB",
                  "pos_rank": 1},
                 {"dt": dt, "team": "LAR", "player_name": "WRX", "gsis_id": "WRX", "pos_grp": "3WR 1TE", "pos_abb": "WR",
                  "pos_rank": 2},
                 {"dt": dt, "team": "LAR", "player_name": "WRY", "gsis_id": "WRY", "pos_grp": "3WR 1TE", "pos_abb": "WR",
                  "pos_rank": 1}]
    d = pd.DataFrame(rows)
    got = nv.depth_chart_asof(2026, as_of="2026-10-15T00:00:00Z", depth=d)
    qb = got[got.pos == "QB"].iloc[0]
    assert qb.gsis_id == "QB_B" and qb.team == "LA" and qb["rank"] == 1  # last snapshot strictly before as_of
    wr = got[got.pos == "WR"].sort_values("rank")
    assert wr.gsis_id.tolist() == ["WRY", "WRX"]
    assert nv.depth_chart_asof(2026, as_of="2026-09-01", depth=d).empty


def test_depth_chart_weekly_format():
    d = pd.DataFrame({"club_code": ["KC"] * 4, "week": [5, 5, 5, 6], "formation": ["Offense"] * 4,
                      "depth_team": [2, 1, 1, 1], "position": ["QB", "QB", "WR", "QB"],
                      "depth_position": ["QB", "QB", "WR", "QB"], "gsis_id": ["Q2", "Q1", "W1", "Q9"],
                      "full_name": ["b", "a", "w", "z"]})
    got = nv.depth_chart_asof(2024, week=5, depth=d)
    assert got[(got.pos == "QB") & (got["rank"] == 1)].gsis_id.item() == "Q1"
    assert set(got.gsis_id) == {"Q1", "Q2", "W1"}


def test_cache_policy(tmp_path, monkeypatch):
    calls = []

    class Frame:
        def __init__(self, s):
            self.s = s

        def to_pandas(self):
            return pd.DataFrame({"season": [str(self.s)], "week": [1.0], "v": [len(calls)]})

    monkeypatch.setattr(nv, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(nv, "_loader", lambda name: (lambda s: calls.append(s) or Frame(s)))
    cur = nv.current_season()
    old = nv.load_season("x", cur - 1, max_age_hours=0.0)
    assert old["season"].dtype.kind == "i" and old["week"].dtype.kind == "i"  # keys normalized to int
    nv.load_season("x", cur - 1, max_age_hours=0.0)  # past season: cached forever
    assert calls == [cur - 1]
    nv.load_season("x", cur, max_age_hours=1.0)
    nv.load_season("x", cur, max_age_hours=1.0)  # fresh enough
    assert calls == [cur - 1, cur]
    p = nv.cache_path("x", cur)
    os.utime(p, (time.time() - 7200, time.time() - 7200))
    nv.load_season("x", cur, max_age_hours=1.0)  # stale current season: refreshed
    assert calls == [cur - 1, cur, cur]


def test_current_season_boundary():
    from datetime import date

    assert nv.current_season(date(2026, 10, 21)) == 2026
    assert nv.current_season(date(2026, 3, 1)) == 2025
    assert nv.current_season(date(2026, 9, 8)) == 2025  # Labor Day 2026 is Sep 7; the opener was Wed Sep 9
    assert nv.current_season(date(2026, 9, 9)) == 2026


def test_stale_cache_used_when_refresh_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(nv, "CACHE_DIR", tmp_path)
    cur = nv.current_season()
    ok = lambda name: (lambda s: pd.DataFrame({"season": [s], "week": [1], "v": [1]}))
    monkeypatch.setattr(nv, "_loader", ok)
    nv.load_season("y", cur, max_age_hours=0.0)

    def boom(name):
        def f(s):
            raise ConnectionError("offline")
        return f

    monkeypatch.setattr(nv, "_loader", boom)
    with pytest.warns(UserWarning, match="refresh failed"):
        df = nv.load_season("y", cur, max_age_hours=0.0)
    assert df["v"].tolist() == [1]
    with pytest.raises(ConnectionError):
        nv.load_season("never_cached", cur, max_age_hours=0.0)
