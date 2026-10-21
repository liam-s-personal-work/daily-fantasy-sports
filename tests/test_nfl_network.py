"""Real-data sanity checks (download / read nflverse). Run with: pytest -m network tests/test_nfl_network.py"""

from __future__ import annotations

import math

import numpy as np
import pytest

pytestmark = pytest.mark.network


@pytest.fixture(scope="module")
def week6():
    from dfs.projections.nfl import NFLData, project_week

    data = NFLData.load(range(2019, 2027))
    proj, frame = project_week(2026, 6, "draftkings.nfl", n_sims=3000, data=data)
    return data, proj, frame


def test_week6_projections_are_finite_and_ordered(week6):
    _, proj, _ = week6
    assert len(proj) > 500
    for k, p in proj.items():
        vals = [p.mean, p.p10, p.p50, p.p90, p.p_zero]
        assert all(math.isfinite(v) for v in vals), k
        assert p.p10 <= p.p50 <= p.p90, k
        assert 0.0 <= p.p_zero <= 1.0 and p.mean >= -1.0, k


def test_week6_top_qbs_and_dsts(week6):
    _, proj, frame = week6
    qb = frame[frame["pos"] == "QB"].sort_values("mean", ascending=False)
    assert (qb["mean"].head(10) > 15).all(), qb[["name", "mean"]].head(10)
    assert qb["mean"].head(10).max() < 35
    dst = frame[frame["kind"] == "dst"]
    assert len(dst) == 28 and dst["mean"].between(2, 14).all()  # 14 games in week 6
    top = frame[frame["kind"] == "player"].nlargest(60, "mean")
    assert set(top["pos"]) >= {"QB", "RB", "WR", "TE"}


def test_week6_every_rostered_starter_has_a_projection(week6):
    from dfs.data import nflverse as nv

    data, proj, frame = week6
    games = data.team_games[(data.team_games["season"] == 2026) & (data.team_games["week"] == 6)]
    teams = set(games["team"])
    dc = data.depth_chart(2026, 6, nv.kickoff_utc(games["gameday"].min(), "08:00"))
    ros = data.rosters[(data.rosters["season"] == 2026) & (data.rosters["week"] == 6)]
    active = set(ros.loc[ros["status"] == "ACT", "gsis_id"])
    limit = {"QB": 1, "RB": 1, "WR": 3, "TE": 1}
    starters = dc[dc["team"].isin(teams) & dc["pos"].isin(limit) & dc["gsis_id"].isin(active)]
    starters = starters[starters["rank"] <= starters["pos"].map(limit)]
    assert len(starters) >= 150
    missing = starters[~starters["gsis_id"].isin(proj)]
    assert missing.empty, missing
    for g in starters["gsis_id"]:
        assert proj[g].mean > 0.5, (g, proj[g])


def test_week6_out_players_have_zero_mean(week6):
    _, proj, frame = week6
    out = frame[(frame["kind"] == "player") & (frame["rep"] == "out")]
    assert len(out) > 10
    assert np.allclose([proj[k].mean for k in out["key"]], 0.0)
    assert all(proj[k].p_zero == 1.0 for k in out["key"])


def test_week6_fanduel_and_contest_matching(week6):
    from dfs.models import Player
    from dfs.projections.nfl import project_week

    data, _, frame = week6
    sample = frame[frame["kind"] == "player"].nlargest(40, "mean")
    players = [Player(key=f"fd{i}", name=r["name"], team=("LAR" if r["team"] == "LA" else r["team"]),
                      positions=[r["pos"]]) for i, (_, r) in enumerate(sample.iterrows())]
    players.append(Player(key="fd-def", name="Kansas City Chiefs", team="KC", positions=["D"]))
    proj, fr = project_week(2026, 6, "fanduel.nfl", players=players, n_sims=1500, data=data)
    assert set(proj) == {p.key for p in players} and not fr.attrs["unmatched"]
    assert proj["fd-def"].mean > 0
