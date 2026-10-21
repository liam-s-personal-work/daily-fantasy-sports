"""Offline tests for the NFL baseline: shrinkage, redistribution, QB starts, scorer parity,
simulation, team model and per-player stat means (synthetic frames only)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from dfs.projections import nfl as N
from dfs.scoring import score

RNG = np.random.default_rng(123)


# --------------------------------------------------------------------------- shrinkage / weights


def test_recency_weights_half_life():
    w = N.recency_weights(np.array([0.0, 4.0, 8.0]), 4.0)
    assert w.tolist() == pytest.approx([1.0, 0.5, 0.25])


def test_age_weeks_season_gap():
    assert N.age_weeks(2026, 3, 2026, 6, gap=8).item() == 3
    assert N.age_weeks(2025, 17, 2026, 2, gap=8).item() == (18 + 8) + (2 - 17)


def test_shrink_limits():
    assert N.shrink(0, 0, 0.2, 10) == pytest.approx(0.2)  # no data -> prior
    assert N.shrink(30, 100, 0.2, 0) == pytest.approx(0.3)  # no prior -> raw rate
    assert N.shrink(30, 100, 0.2, 100) == pytest.approx(0.25)  # equal weight
    assert N.shrink(3000, 10000, 0.2, 10) == pytest.approx(0.3, abs=1e-3)  # data dominates


# --------------------------------------------------------------------------- redistribution


@pytest.mark.parametrize("seed", range(25))
def test_allocate_conserves_expected_volume(seed):
    rng = np.random.default_rng(seed)
    n = rng.integers(3, 12)
    c = rng.dirichlet(np.ones(n)) * rng.uniform(0.6, 1.3)
    p = np.where(rng.random(n) < 0.3, rng.uniform(0, 1, n), 0.97)
    pos = rng.choice(["RB", "WR", "TE"], n)
    s = N.allocate_shares(c, p, pos, gamma=rng.uniform(0.5, 2), cross_affinity=rng.uniform(0, 1), cap=1.0)
    assert (p * s).sum() == pytest.approx(1.0, abs=1e-9)
    assert (s >= 0).all()
    if (p * c).sum() < 1:
        assert (s >= c - 1e-12).all()  # nobody loses usage when usage is vacated
    else:
        assert np.allclose(s, c / (p * c).sum())


def test_allocate_out_player_usage_goes_to_teammates_by_affinity():
    c = np.array([0.30, 0.20, 0.15, 0.15, 0.20])
    p = np.array([0.0, 1.0, 1.0, 1.0, 1.0])  # WR1 out
    pos = np.array(["WR", "WR", "WR", "TE", "RB"])
    s = N.allocate_shares(c, p, pos, gamma=1.0, cross_affinity=0.5)
    gain = s - c
    assert (p * s).sum() == pytest.approx(1.0)
    assert gain[1] > gain[3] and gain[1] > gain[4]  # same-position WR gains most
    assert gain[2] / c[2] > gain[3] / c[3]  # per unit of share, WR > TE (affinity 1 vs 0.5)
    assert gain[3] == pytest.approx(gain[4] * 0.15 / 0.20)  # TE and RB share the same affinity


def test_allocate_departed_players_and_cap():
    c = np.array([0.40, 0.10, 0.10])
    p = np.ones(3)
    pos = np.array(["WR", "WR", "TE"])
    s = N.allocate_shares(c, p, pos, vacated_by_pos={"WR": 0.4}, gamma=1.0, cross_affinity=0.5, cap=0.45)
    assert s.max() <= 0.45 + 1e-12
    assert (p * s).sum() == pytest.approx(1.0)
    s2 = N.allocate_shares(c, p, pos, vacated_by_pos={"WR": 0.4}, gamma=2.0, cross_affinity=0.5, cap=1.0)
    assert s2[0] / c[0] > s[0] / c[0]  # gamma > 1 favours the alpha


def test_allocate_degenerate_inputs():
    assert N.allocate_shares(np.zeros(3), np.ones(3), np.array(["WR"] * 3)).tolist() == [0, 0, 0]
    c = np.array([0.5, 0.5])
    assert N.allocate_shares(c, np.zeros(2), np.array(["WR", "WR"])).tolist() == [0.5, 0.5]


# --------------------------------------------------------------------------- QB starts


def _qbs(p_active, last_start, depth_rank, starts_w):
    return pd.DataFrame({"p_active": p_active, "last_start": last_start, "depth_rank": depth_rank,
                         "starts_w": starts_w})


def test_qb_start_probs():
    healthy = N.qb_start_probs(_qbs([0.98, 0.98], [1, 0], [1, 2], [3, 0]))
    assert healthy[0] == pytest.approx(0.98) and healthy[1] == pytest.approx(0.02 * 0.98)
    starter_out = N.qb_start_probs(_qbs([0.0, 0.97], [1, 0], [1, 2], [3, 0]))
    assert starter_out.tolist() == pytest.approx([0.0, 0.97])
    questionable = N.qb_start_probs(_qbs([0.6, 0.98], [1, 0], [1, 2], [3, 0]))
    assert questionable.sum() <= 1 + 1e-12 and questionable[1] == pytest.approx(0.4 * 0.98)
    # returning starter is depth-chart QB1 while the backup started last week
    ret = _qbs([0.95, 0.98], [0, 1], [1, 2], [1.0, 2.0])
    assert N.qb_start_probs(ret)[1] > N.qb_start_probs(ret)[0]
    assert N.qb_start_probs(ret, depth_first=True)[0] == pytest.approx(0.95)


# --------------------------------------------------------------------------- scorer parity

OFF_KEYS = ["pass_yds", "pass_td", "int_thrown", "rush_yds", "rush_td", "rec", "rec_yds", "rec_td", "return_td",
            "fumble_lost", "two_pt", "off_fumble_rec_td"]


def _random_offense(n):
    s = {
        "pass_yds": RNG.choice([0, 150, 299, 300, 301, 425], n) + RNG.integers(-3, 3, n) * (RNG.random(n) < 0.3),
        "pass_td": RNG.poisson(1.2, n), "int_thrown": RNG.poisson(0.6, n),
        "rush_yds": RNG.choice([-4, 0, 37, 99, 100, 101, 160], n), "rush_td": RNG.poisson(0.4, n),
        "rec": RNG.poisson(4, n), "rec_yds": RNG.choice([0, 12, 99, 100, 145], n) + RNG.integers(0, 2, n),
        "rec_td": RNG.poisson(0.3, n), "return_td": RNG.poisson(0.05, n), "fumble_lost": RNG.poisson(0.2, n),
        "two_pt": RNG.poisson(0.1, n), "off_fumble_rec_td": RNG.poisson(0.02, n),
    }
    return {k: v.astype(float) for k, v in s.items()}


@pytest.mark.parametrize("table", ["draftkings.nfl", "fanduel.nfl"])
def test_vector_score_matches_dfs_scoring_offense(table):
    stats = _random_offense(400)
    vec = N.vector_score(stats, table, "offense")
    for i in range(400):
        line = {k: stats[k][i] for k in OFF_KEYS}
        assert round(vec[i] + 1e-9, 2) == pytest.approx(score(line, table, role="offense"), abs=1e-9)


@pytest.mark.parametrize("table", ["draftkings.nfl", "fanduel.nfl"])
def test_vector_score_matches_dfs_scoring_dst(table):
    n = 300
    stats = {"sack": RNG.poisson(2.5, n), "interception": RNG.poisson(0.8, n), "fumble_recovery": RNG.poisson(0.5, n),
             "def_td": RNG.poisson(0.1, n), "return_td": RNG.poisson(0.05, n), "safety": RNG.poisson(0.03, n),
             "blocked_kick": RNG.poisson(0.05, n), "two_pt_return": RNG.poisson(0.01, n),
             "points_allowed": np.concatenate([[0, 1, 6, 7, 13, 14, 20, 21, 27, 28, 34, 35, 60],
                                               RNG.integers(0, 50, n - 13)])}
    stats = {k: np.asarray(v, dtype=float) for k, v in stats.items()}
    vec = N.vector_score(stats, table, "dst")
    for i in range(n):
        assert round(vec[i] + 1e-9, 2) == pytest.approx(
            score({k: v[i] for k, v in stats.items()}, table, role="dst"), abs=1e-9)


@pytest.mark.parametrize("table", ["draftkings.nfl", "fanduel.nfl"])
def test_vector_score_matches_dfs_scoring_kicker(table):
    n, kmax = 200, 6
    counts = RNG.integers(0, kmax + 1, n)
    dist = RNG.choice([19, 25, 39, 40, 45, 49, 50, 54, 61], (n, kmax)).astype(float)
    dist = np.where(np.arange(kmax)[None, :] < counts[:, None], dist, np.nan)
    xp = RNG.integers(0, 6, n).astype(float)
    vec = N.vector_score({"xp_made": xp, "fg_made_distances": dist}, table, "kicker")
    for i in range(n):
        line = {"xp_made": xp[i], "fg_made_distances": [d for d in dist[i] if not np.isnan(d)]}
        assert round(vec[i] + 1e-9, 2) == pytest.approx(score(line, table, role="kicker"), abs=1e-9)


def test_offense_stat_line_matches_nflverse_ppr_formula():
    row = pd.DataFrame([{"passing_yards": 312, "passing_tds": 2, "passing_interceptions": 1, "rushing_yards": 21,
                         "rushing_tds": 1, "receptions": 0, "receiving_yards": 0, "receiving_tds": 0,
                         "special_teams_tds": 0, "sack_fumbles_lost": 1, "rushing_fumbles_lost": 0,
                         "receiving_fumbles_lost": 0, "passing_2pt_conversions": 1, "rushing_2pt_conversions": 0,
                         "receiving_2pt_conversions": 0, "fumble_recovery_tds": 0}])
    line = N.offense_stat_line(row).iloc[0].to_dict()
    # DK: 312*.04 + 8 + 3 (300 bonus) - 1 + 2.1 + 6 - 1 (fumble) + 2 (2pt) = 31.58
    assert score(line, "draftkings.nfl", role="offense") == pytest.approx(31.58)
    # FD: same except fumble -2 and receptions 0.5 -> 30.58
    assert score(line, "fanduel.nfl", role="offense") == pytest.approx(30.58)


# --------------------------------------------------------------------------- simulation


def _means(**kw):
    base = {c: 0.0 for c in N.SIM_STATS}
    base.update(kw)
    return base


def test_simulate_offense_preserves_means():
    m = pd.DataFrame([
        {"pos": "WR", **_means(targets=8.0, rec=5.5, rec_yds=70.0, rec_td=0.45, carries=0.3, rush_yds=2.0)},
        {"pos": "RB", **_means(targets=3.0, rec=2.4, rec_yds=18.0, rec_td=0.12, carries=17.0, rush_yds=75.0,
                               rush_td=0.6, fumble_lost=0.1)},
        {"pos": "QB", **_means(pass_att=34.0, comp=22.0, pass_yds=245.0, pass_td=1.6, int_thrown=0.7, carries=4.0,
                               rush_yds=18.0, rush_td=0.2, two_pt=0.08)},
    ])
    sim = N.simulate_offense(m, N.NFLParams(), 60000, np.random.default_rng(5))
    for stat in ("targets", "rec", "rec_yds", "rec_td", "carries", "rush_yds", "rush_td", "pass_att", "comp",
                 "pass_yds", "pass_td", "int_thrown", "fumble_lost"):
        got = sim[stat].mean(axis=1)
        want = m[stat].to_numpy()
        assert got == pytest.approx(want, rel=0.03, abs=0.02), stat
    assert (sim["targets"] >= sim["rec"]).all() and (sim["pass_att"] >= sim["comp"]).all()
    assert sim["rush_yds"].min() < 0  # losses are possible


def test_simulate_dst_points_allowed_tracks_opponent_offense():
    league = {"def_td_per_takeaway": 0.05, "xp_per_td": 0.9, "two_pt_per_td": 0.05, "sack_cv": 0.35}
    t = pd.DataFrame({"opp_sacks": [2.5, 2.5], "opp_int": [0.8, 0.8], "opp_fumbles": [0.5, 0.5],
                      "opp_off_td": [1.6, 3.4], "opp_fg": [1.6, 1.8], "st_td": [0.03, 0.03], "safety": [0.03, 0.03],
                      "blocks": [0.05, 0.05], "two_pt_return": [0.002, 0.002], "st_td_allowed": [0.03, 0.03],
                      "opp_def_td": [0.06, 0.06]})
    out = N.simulate_dst(t, league, "draftkings.nfl", 20000, np.random.default_rng(1))
    exp_pa = 6 * t["opp_off_td"] * (1 + 0.9 / 6 + 2 * 0.05 / 6 / 0.1 * 0.1 * 0) + 3 * t["opp_fg"]
    assert out["points_allowed"].iloc[0] < out["points_allowed"].iloc[1]
    assert out["played_mean"].iloc[0] > out["played_mean"].iloc[1]  # weaker opponent -> better DST
    assert out["points_allowed"].to_numpy() == pytest.approx(exp_pa.to_numpy() + 7 * 0.03 + 0.06, rel=0.05)
    assert out["sacks"].to_numpy() == pytest.approx([2.5, 2.5], rel=0.03)


# --------------------------------------------------------------------------- team model


def _synthetic_team_games(n_weeks=40, seed=0):
    rng = np.random.default_rng(seed)
    teams = [f"T{i}" for i in range(8)]
    rows = []
    for w in range(n_weeks):
        season, week = 2020 + w // 18, w % 18 + 1
        order = rng.permutation(teams)
        for a, b in zip(order[::2], order[1::2]):
            imp_a, imp_b = rng.uniform(16, 30), rng.uniform(16, 30)
            for team, opp, imp, oimp in ((a, b, imp_a, imp_b), (b, a, imp_b, imp_a)):
                r = {"season": season, "week": week, "team": team, "opponent": opp, "implied": imp,
                     "opp_implied": oimp, "spread_line": 0.0, "total_line": imp + oimp, "played": True}
                bump = 4.0 if team == "T0" else 0.0  # T0 passes more than its market says
                for s in N.TEAM_STATS:
                    r[s] = 1.0 + 0.1 * imp
                r["attempts"] = 20 + 0.5 * imp - 0.2 * oimp + bump + rng.normal(0, 1.0)
                rows.append(r)
    return pd.DataFrame(rows)


def test_team_model_recovers_market_slope_and_team_tendency():
    h = _synthetic_team_games()
    tm = N.TeamModel(h, 2022, 10, N.NFLParams(team_min_season=2020))
    j = N.TEAM_STATS.index("attempts")
    games = pd.DataFrame({"team": ["T0", "T1"], "opponent": ["T1", "T0"], "implied": [25.0, 25.0],
                          "opp_implied": [20.0, 20.0]})
    pred = tm.predict(games, 2022, 10)
    base = 20 + 0.5 * 25 - 0.2 * 20
    assert pred["attempts"].iloc[1] == pytest.approx(base, abs=1.2)
    assert pred["attempts"].iloc[0] - pred["attempts"].iloc[1] == pytest.approx(4.0, abs=1.5)
    assert tm.coef[j][1] == pytest.approx(0.5, abs=0.1)
    assert pred["points"].iloc[0] == pytest.approx(1 + 2.5, abs=1e-6)  # exact linear stat


# --------------------------------------------------------------------------- usage


def _hist_rows():
    rows = []
    for wk, (tg, team_tg) in enumerate([(8, 32), (6, 30), (10, 36)], start=1):
        rows.append({"gsis_id": "W1", "pos": "WR", "season": 2026, "week": wk, "team": "AAA", "offense_pct": 0.9,
                     "targets": tg, "team_targets": team_tg, "carries": 0, "team_carries": 25, "attempts": 0,
                     "team_attempts": 34, "receptions": tg * 0.6, "receiving_yards": tg * 8.0, "rushing_yards": 0,
                     "x_rec": tg * 0.62, "x_rec_yds": tg * 8.5, "x_rush_yds": 0, "x_rec_td": 0.3, "x_rush_td": 0,
                     "team_x_rec_td": 1.5, "team_x_rush_td": 1.0, "receiving_tds": 0, "rushing_tds": 0,
                     "team_rec_td": 1, "team_rush_td": 1, "has_ffo": True, "fumbles_lost": 0, "sacks_suffered": 0,
                     "special_teams_tds": 0, "fumble_recovery_tds": 0, "completions": 0, "wk": 202600 + wk})
    return pd.DataFrame(rows)


def test_estimate_usage_matches_hand_calculation():
    h = _hist_rows()
    pool = pd.DataFrame({"gsis_id": ["W1", "NEW"], "team": ["AAA", "AAA"], "pos": ["WR", "WR"],
                         "depth_rank": [1.0, 3.0]})
    P = N.NFLParams(half_life=2.0, n0_target=20.0, exit_downweight=1.0)
    league = {"WR": {"catch": 0.63, "ypt": 8.0, "ypc": 5.0, "fumble": 0.005, "two_pt_per_td": 0.03, "st_td": 0.0,
                     "fum_rec_td": 0.0}, "QB": {"comp": 0.64}}
    u = N.estimate_usage(h, pool, 2026, 4, P, league).set_index("gsis_id")
    w = 0.5 ** (np.array([3, 2, 1]) / 2.0)
    want = ((w * [8, 6, 10]).sum() + 20 * 0.20) / ((w * [32, 30, 36]).sum() + 20)
    assert u.loc["W1", "c_tgt"] == pytest.approx(want)
    assert u.loc["NEW", "c_tgt"] == pytest.approx(N.role_prior("WR", 3)[0])  # no history -> role prior
    assert 0.55 < u.loc["W1", "catch"] < 0.65
    assert u.loc["NEW", "n_games"] == 0


def _ctx_for_means(p_wr1: float) -> N.WeekContext:
    pool = pd.DataFrame({
        "gsis_id": ["Q1", "Q2", "R1", "W1", "W2", "T1"], "team": ["AAA"] * 6,
        "pos": ["QB", "QB", "RB", "WR", "WR", "TE"], "p_active": [0.98, 0.98, 0.97, p_wr1, 0.97, 0.97],
        "last_start": [1, 0, 0, 0, 0, 0], "depth_rank": [1, 2, 1, 1, 2, 1], "starts_w": [3, 0, 0, 0, 0, 0],
        "c_tgt": [0, 0, 0.12, 0.30, 0.22, 0.20], "c_rtd": [0, 0, 0.1, 0.3, 0.2, 0.25],
        "c_car": [0.1, 0.1, 0.65, 0.02, 0.01, 0.0], "c_utd": [0.1, 0.1, 0.7, 0.01, 0.0, 0.0],
        "q_att": [0.97, 0.97, 0, 0, 0, 0], "q_car": [0.12, 0.1, 0, 0, 0, 0], "q_utd": [0.1, 0.1, 0, 0, 0, 0],
        "catch": [0.6, 0.6, 0.78, 0.65, 0.62, 0.7], "ypt": [7, 7, 6.5, 9.0, 8.5, 7.5], "ypc": [5, 5, 4.4, 6, 6, 4],
        "comp_pct": [0.66, 0.6, 0.64, 0.64, 0.64, 0.64], "fumble_rate": [0.01] * 6, "two_pt_per_td": [0.03] * 6,
        "st_td_rate": [0.0] * 6, "fum_rec_td_rate": [0.0] * 6,
        "name": list("abcdef"), "status": ["ACT"] * 6, "rep": ["none"] * 6, "prac": ["none"] * 6,
        "role": ["regular"] * 6})
    tp = pd.DataFrame([{s: 1.0 for s in N.TEAM_STATS}], index=["AAA"])
    tp.loc["AAA", ["targets", "attempts", "carries", "completions", "passing_yards", "rushing_yards", "passing_tds",
                   "rushing_tds", "passing_interceptions", "sacks_suffered"]] = [33, 35, 26, 22, 240, 115, 1.6, 0.9,
                                                                                 0.8, 2.3]
    games = pd.DataFrame({"team": ["AAA"], "opponent": ["BBB"], "implied": [24.0]})
    return N.WeekContext(season=2026, week=7, params=N.NFLParams(), games=games, team_pred=tp, pool=pool,
                         vacated={}, league={"QB": {"pass_2pt_per_td": 0.03}}, team_league={},
                         kickers=pd.DataFrame(), fg_bin_probs=np.ones(6) / 6)


def test_player_means_conserve_team_volume_and_redistribute():
    full = N.player_means(_ctx_for_means(0.97)).set_index("gsis_id")
    out = N.player_means(_ctx_for_means(0.0)).set_index("gsis_id")
    for m in (full, out):
        w = m["p_play"]
        assert (w * m["targets"]).sum() == pytest.approx(33, rel=1e-6)
        assert (w * m["carries"]).sum() == pytest.approx(26, rel=1e-6)
        assert (w * m["rec_td"]).sum() == pytest.approx(1.6, rel=1e-6)
        assert (w * m["rush_td"]).sum() == pytest.approx(0.9, rel=1e-6)
        assert (w * m["rec_yds"]).sum() == pytest.approx(240, rel=1e-6)
        assert (w * m["pass_att"]).sum() == pytest.approx(0.97 * 35, rel=0.03)
        assert (m["s_tgt"] <= N.NFLParams().max_target_share + 1e-9).all()
    assert out.loc["W2", "targets"] > full.loc["W2", "targets"]  # WR1 out -> WR2 up
    assert out.loc["T1", "targets"] > full.loc["T1", "targets"]
    assert (out.loc["W2", "targets"] - full.loc["W2", "targets"]) / full.loc["W2", "targets"] > \
        (out.loc["T1", "targets"] - full.loc["T1", "targets"]) / full.loc["T1", "targets"]
    # start chain 0.98 / 0.02 * 0.98, conditioned on exactly one QB starting
    assert out.loc["Q1", "p_play"] == pytest.approx(0.98 / (0.98 + 0.02 * 0.98)) and out.loc["Q2", "p_play"] < 0.03
    assert out.loc[["Q1", "Q2"], "p_play"].sum() == pytest.approx(1.0)
