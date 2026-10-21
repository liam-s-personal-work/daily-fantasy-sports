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


def test_week6_kickers_for_showdown(week6):
    from dfs.projections.nfl import project_week

    data = week6[0]
    proj, frame = project_week(2026, 6, "draftkings.nfl", n_sims=1500, data=data, include_kickers=True)
    k = frame[frame["kind"] == "kicker"]
    assert len(k) == 28 and k["mean"].between(4, 14).all()
    assert all(proj[g].p10 <= proj[g].p50 <= proj[g].p90 for g in k["key"])


# --------------------------------------------------------------------------- review regressions (real data)


def test_week_context_holds_only_pregame_columns(week6):
    from dfs.projections.nfl import PREGAME_COLS, TEAM_STATS, build_context

    ctx = build_context(week6[0], 2026, 6)
    assert set(ctx.games.columns) <= set(PREGAME_COLS)
    assert not set(ctx.games.columns) & (set(TEAM_STATS) | {"points", "opp_points", "played", "def_sacks"})


def test_qb_start_probabilities_sum_to_one_when_starter_out(week6):
    from dfs.projections.nfl import build_context, player_means

    data = week6[0]
    for wk in (1, 3, 6, 7):
        m = player_means(build_context(data, 2026, wk))
        sums = m[m["pos"] == "QB"].groupby("team")["p_play"].sum()
        assert sums.between(1 - 1e-9, 1 + 1e-9).all(), (wk, sums[sums < 0.999])
    # 2026 wk 6: Hurts on IR, Tanner McKee depth-chart QB1 (he threw 45 passes)
    m = player_means(build_context(data, 2026, 6)).set_index("name")
    assert m.loc["Tanner McKee", "p_play"] > 0.9


def test_depth_snapshot_index_matches_full_filter_on_real_files(week6):
    from dfs.data import nflverse as nv

    data = week6[0]
    for season in (2025, 2026):
        raw = data.depth_raw[season]
        idx = nv.DepthSnapshots(raw)
        tg = data.team_games[data.team_games["season"] == season]
        for gameday in sorted(set(tg["gameday"]))[:: 7]:
            as_of = nv.kickoff_utc(gameday, "08:00")
            want = nv.depth_chart_asof(season, as_of=as_of, depth=raw[raw["dt"].notna()])
            got = idx.asof(season, as_of)
            assert got.reset_index(drop=True).equals(want.reset_index(drop=True)), (season, gameday)


def test_depth_chart_starters_without_recent_snaps_are_likely_to_play(week6):
    from dfs.projections.nfl import UNAVAILABLE_STATUS, build_context

    data = week6[0]
    pool = build_context(data, 2026, 1).pool  # season opener: many starters missed the end of 2025
    p = pool[(pool["role"] == "none") & (pool["dc"] == "starter") & (pool["rep"] == "none")
             & ~pool["status"].isin(UNAVAILABLE_STATUS) & (pool["pos"] != "QB")]
    assert len(p) >= 20 and p["p_active"].median() > 0.75


def test_actual_points_match_site_season_averages_for_returners(week6):
    """FD salary file for 2026 week 6 prints weeks 1-5 FPPG x Played; return fumbles must be charged."""
    from pathlib import Path

    import pandas as pd

    from dfs import backtest as bt
    from dfs.data.teams import canonicalize
    from dfs.projections.matching import normalize_name

    path = Path(__file__).resolve().parents[1] / "data" / "cache" / "real_samples" / "fd_classic_wk6_main_18Oct2026.csv"
    if not path.exists():
        pytest.skip("real FD sample file not cached")
    data = week6[0]
    bt.add_actual_points(data)
    pg = data.player_games
    w = pg[(pg["season"] == 2026) & (pg["week"] <= 5)]
    tot = w.assign(nk=w["name"].map(normalize_name)).groupby(["nk", "team"])["fp_fanduel.nfl"].sum()
    f = pd.read_csv(path)
    f = f[f["Position"].isin(["QB", "RB", "WR", "TE"]) & (f["FPPG"] > 0)]
    f = f.assign(nk=f["Nickname"].map(normalize_name), team=f["Team"].map(canonicalize))
    f = f.join(tot.rename("mine"), on=["nk", "team"]).dropna(subset=["mine"])
    ok = (f["FPPG"] * f["Played"] - f["mine"]).abs() < 0.06
    assert len(f) > 200 and ok.mean() > 0.99
    assert ok[f["Nickname"].isin(["Chimere Dike", "Dylan Laube", "Dareke Young"])].all()
