"""College football: stat lines from play-by-play, the baseline model, team/player matching, provider.

Unit tests use small synthetic frames (no network). ``@pytest.mark.network`` tests read the real
sportsdataverse release files (skipped by default; run with ``pytest -m network tests/test_cfb.py``).
"""

from __future__ import annotations

import random

import numpy as np
import pandas as pd
import pytest

from dfs.data import cfb as cfbdata
from dfs.data import cfb_stats as cs
from dfs.data.cfb_teams import TeamMatcher, _ordered_abbrev_score, match_slate, norm_code, split_slate_game
from dfs.models import Contest, ContestRules, Player, Slot
from dfs.projections import cfb as model
from dfs.scoring import score
from dfs.sources import blind_text

# --------------------------------------------------------------------------- synthetic play-by-play

HOME, AWAY = 10, 20  # team ids
ROSTER = pd.DataFrame([
    # season, team_id, athlete_id, jersey, first, last, position
    (2026, HOME, 101, "7", "Jalen", "Passer", "QB"),
    (2026, HOME, 102, "5", "Jay", "Doe", "RB"),
    (2026, HOME, 103, "1", "Ken", "Catcher", "WR"),
    (2026, HOME, 104, "88", "Tim", "Tight", "TE"),
    (2026, HOME, 105, "16", "Jeff", "Overton", "RB"),
    (2026, AWAY, 201, "3", "Mark", "Thrower", "QB"),
    (2026, AWAY, 202, "9", "Rick", "Runner", "RB"),
], columns=["season", "team_id", "athlete_id", "jersey", "first_name", "last_name", "position_abbreviation"])
ROSTER["full_name"] = ROSTER["first_name"] + " " + ROSTER["last_name"]
ROSTER["athlete_display_name"] = ROSTER["full_name"]
ROSTER["display_name"] = ROSTER["first_name"].str[0] + ". " + ROSTER["last_name"]


def _play(n, play_type, text, yards=0.0, pos=HOME, **kw):
    row = {"season": 2026, "week": 3, "game_id": 999, "game_play_number": n, "pos_team": "Home U" if pos == HOME else "Away St",
           "def_pos_team": "Away St" if pos == HOME else "Home U", "home": "Home U", "away": "Away St",
           "home_team_id": HOME, "away_team_id": AWAY, "play_type": play_type, "play_text": text,
           "yards_gained": yards, "rush_td": 0, "pass_td": 0, "touchdown": 0, "penalty_no_play": False}
    row.update(kw)
    return row


def synthetic_pbp() -> pd.DataFrame:
    rows = [
        # 1 rush with participant id
        _play(1, "Rush", "Shotgun #5 J.Doe rush right for 4 yards gain to the AWY30", 4, rush_player_id=102.0,
              rush_player="Jay Doe", rush_yds=4.0),
        # 2 rush without id -> text (jersey + initial.last), suffix in text
        _play(2, "Rush", "(10:00) No Huddle-Shotgun #16 J.Overton, Jr. rush left for 7 yards gain to the AWY23", 7),
        # 3 completion with ids
        _play(3, "Pass Reception", "#7 J.Passer pass complete short left to #1 K.Catcher caught at AWY20, for 12 yards to the AWY11",
              12, completion_player_id=101.0, completion_player="Jalen Passer", reception_player_id=103.0,
              reception_player="Ken Catcher"),
        # 4 passing TD with ids
        _play(4, "Passing Touchdown", "#7 J.Passer pass complete short right to #88 T.Tight caught at AWY02, for 11 yards to the AWY00 TOUCHDOWN",
              11, pass_td=1, touchdown=1, completion_player_id=101.0, completion_player="Jalen Passer",
              reception_player_id=104.0, reception_player="Tim Tight"),
        # 5 incompletion, target only in text
        _play(5, "Pass Incompletion", "#7 J.Passer pass incomplete deep left to #1 K.Catcher thrown to AWY05", 0,
              incompletion_player_id=101.0, incompletion_player="Jalen Passer"),
        # 6 sack: a rushing attempt with negative yards (NCAA); yards_gained includes a penalty, yds_sacked is the sack
        _play(6, "Sack", "#7 J.Passer sacked for loss of 6 yards to the HOM40", -1, sack_taken_player_id=101.0,
              sack_taken_player="Jalen Passer", yds_sacked=-6.0),
        # 7 nullified play
        _play(7, "Penalty", "#5 J.Doe rush middle for 30 yards gain PENALTY HOM Holding 10 yards. NO PLAY", 0,
              penalty_no_play=True),
        # 8 lost fumble on a run typed by the recovery; yards_gained includes the return
        _play(8, "Fumble Recovery (Opponent)", "#5 J.Doe rush middle for 3 yards gain to the AWY27 fumbled by #5 J.Doe at "
              "AWY27 recovered by AWY #2 X.Def at AWY27 #2 X.Def return 50 yards to the HOM23", 53),
        # 9 enforced foul after a run: box credits the parenthesised yards
        _play(9, "Penalty", "#16 J.Overton, Jr. rush right for 29 yards gain (6) to the AWY02 PENALTY HOM Holding 10 yards "
              "from AWY25 to AWY35", 16),
        # 10 rushing TD + successful two-point run by another player
        _play(10, "Rushing Touchdown", "#5 J.Doe rush middle for 2 yards gain to the AWY00 TOUCHDOWN, clock 03:00 #16 J.Overton, Jr. "
              "rush attempt Successful", 2, rush_td=1, touchdown=1, rush_player_id=102.0, rush_player="Jay Doe", rush_yds=2.0),
        # 11 overturned TD: final ruling is an incompletion
        _play(11, "Passing Touchdown", "#7 J.Passer pass incomplete deep left to #1 K.Catcher thrown to AWY00. The previous play "
              "is under automatic review - \"Incomplete pass\". CALL OVERTURNED. (Original Play: #7 J.Passer pass complete deep "
              "left to #1 K.Catcher caught at AWY00, for 29 yards to the AWY00 TOUCHDOWN)", 29, pass_td=1, touchdown=1,
              completion_player_id=101.0, completion_player="Jalen Passer"),
        # 12 summary-style TD row with swapped participant ids (2024-25 feeds)
        _play(12, "Passing Touchdown", "Ken Catcher 40 Yd pass from Jalen Passer (Sam Kicker Kick)", 40, pass_td=1, touchdown=1,
              completion_player_id=103.0, completion_player="Ken Catcher", reception_player_id=101.0,
              reception_player="Jalen Passer"),
        # 13 away team: full-name text style, no ids
        _play(13, "Rush", "Rick Runner run for 9 yds to the HOM 41", 9, pos=AWAY),
        # 14 away interception, summary row naming only the defender -> passer from neighbouring play
        _play(14, "Pass Reception", "Mark Thrower pass complete to Rick Runner for 5 yds to the HOM 36", 5, pos=AWAY),
        _play(15, "Interception Return Touchdown", "Ken Defender 41 Yd Interception Return (Sam Kicker Kick)", 41, pos=AWAY),
    ]
    df = pd.DataFrame(rows)
    for c in cfbdata.PBP_COLUMNS:
        if c not in df.columns:
            df[c] = np.nan
    return df


@pytest.fixture(scope="module")
def synth_lines():
    lines, teams, stats = cs.player_game_lines(synthetic_pbp(), ROSTER)
    return lines.set_index("athlete_id"), teams.set_index("team_id"), stats


def test_stat_lines_from_synthetic_plays(synth_lines):
    L, T, _ = synth_lines
    qb = L.loc[101]
    assert qb["pass_att"] == 5  # plays 3, 4, 5, 11 (overturned -> incomplete), 12 (summary TD)
    assert qb["pass_cmp"] == 3 and qb["pass_yds"] == 12 + 11 + 40
    assert qb["pass_td"] == 2  # the overturned TD does not count
    assert qb["sacks"] == 1 and qb["rush_att"] == 1 and qb["rush_yds"] == -6  # sack yards, not yards_gained
    assert qb["carries"] == 0
    rb = L.loc[102]
    assert rb["carries"] == 3 and rb["rush_yds"] == 4 + 3 + 2  # fumble play: run yards, not the return
    assert rb["rush_td"] == 1 and rb["fumble_lost"] == 1
    ov = L.loc[105]  # text-only attributions (jersey + initial.last, with ", Jr.")
    assert ov["carries"] == 2 and ov["rush_yds"] == 7 + 6  # credited "(6)" on the enforced-foul run
    assert ov["two_pt"] == 1
    wr = L.loc[103]
    assert wr["targets"] == 4  # play 3, play 5 (text target), play 11 (overturned -> incompletion), play 12 (summary TD)
    te = L.loc[104]
    assert te["rec"] == 1 and te["rec_td"] == 1 and te["rec_yds"] == 11


def test_summary_rows_swap_and_interception_passer(synth_lines):
    L, _, stats = synth_lines
    wr = L.loc[103]
    # the summary row's swapped ids were fixed: the catch and TD belong to the receiver
    assert wr["rec"] == 2 and wr["rec_yds"] == 12 + 40 and wr["rec_td"] == 1
    assert stats.summary()["summary_rows"]["swapped_ids_fixed"] == 1
    away_qb = L.loc[201]  # resolved from the full-name text, INT from the neighbouring play
    assert away_qb["pass_att"] == 2 and away_qb["int_thrown"] == 1 and away_qb["pass_yds"] == 5
    assert L.loc[202]["carries"] == 1 and L.loc[202]["rec"] == 1


def test_team_totals(synth_lines):
    _, T, _ = synth_lines
    h = T.loc[HOME]
    assert h["team_pass_att"] == 5 and h["team_dropbacks"] == 6
    assert h["team_carries"] == 5  # plays 1, 2, 8, 9, 10
    assert T.loc[AWAY]["team_pass_att"] == 2


def test_vectorized_points_equal_dfs_scoring():
    rng = random.Random(3)
    rows = []
    for _ in range(300):
        rows.append({"pass_yds": rng.choice([0, 150, 299, 300, 412]), "pass_td": rng.randint(0, 4), "int_thrown": rng.randint(0, 2),
                     "rush_yds": rng.choice([-7, 0, 45, 99, 100, 180]), "rush_td": rng.randint(0, 3), "rec": rng.randint(0, 11),
                     "rec_yds": rng.choice([0, 37, 100, 154]), "rec_td": rng.randint(0, 2), "fumble_lost": rng.randint(0, 1),
                     "two_pt": rng.randint(0, 1), "return_td": rng.randint(0, 1)})
    df = pd.DataFrame(rows)
    for table in ("draftkings.cfb", "fanduel.cfb"):
        vec = cs.fantasy_points(df, table)
        ref = [score(r, table, position="WR") for r in rows]
        assert np.allclose(vec.to_numpy(), ref), table


@pytest.mark.parametrize("raw,expect", [
    ("J.Overton, Jr.", ("j", "overton", True)), ("M.Van Buren Jr.", ("m", "vanburen", True)),
    ("Smith,Cameren", ("cameren", "smith", False)), ("Chris Zellous", ("chris", "zellous", False)),
    ("B. Edwards Jr.", ("b", "edwards", True)), ("K.Colón", ("k", "colon", True)), ("TEAM", None),
])
def test_name_parts(raw, expect):
    assert cs.name_parts(raw) == expect


def test_text_parsers():
    assert cs.parse_actor("(01:00) Shotgun #3 Q.Brown rush left for 3 yards gain", "rusher") == ("3", "Q.Brown")
    assert cs.parse_actor("Kneel down by #6 T.Hughes at WYO24 for loss of 1 yard", "rusher") == ("6", "T.Hughes")
    assert cs.parse_actor("T. Owens pass to J. Bonelli", "target") == (None, "J. Bonelli")
    assert cs.parse_actor("Keelon Russell pass incomplete to Ryan Coleman-Williams Alabama Penalty, Illegal Forward Pass",
                          "target")[1] == "Ryan Coleman-Williams"
    assert cs.yards_from_text("#5 X.Y rush middle for 6 yards loss to the A20", "rush") == -6
    assert cs.yards_from_text("Kneel down by #6 T.Hughes at WYO24 for loss of 1 yard", "rush") == -1
    assert cs.yards_from_text("Bo Back run for no gain to the X 30", "rush") == 0
    assert cs.yards_from_text("#7 Q.B sacked for loss of 9 yards to the X10", "sack") == -9
    assert cs.parse_two_point("A 3 Yd pass from B (B Pass to C for Two-Point Conversion)") == [(None, "B"), (None, "C")]
    assert cs.parse_two_point("TOUCHDOWN #9 A.B pass attempt failed") == []
    assert cs.parse_two_point("TOUCHDOWN #9 A.B pass attempt Successful PENALTY X. NO PLAY #9 A.B pass attempt failed") == []


def test_team_index_resolution():
    ti = cs.TeamIndex()
    ti.add(1, "Jeremiah Smith", "4", "WR", "Jeremiah", "Smith")
    ti.add(2, "James Smith", "3", "DT", "James", "Smith")
    ti.add(3, "Will Smith Jr.", "53", "DT", "Will", "Smith Jr.")
    assert ti.resolve("Je. Smith", None) is None or ti.resolve("Jeremiah Smith") == 1
    assert ti.resolve("J.Smith", "4") == 1  # jersey breaks the J. Smith tie
    assert ti.resolve("J.Smith", None) == 1  # only one J. Smith on offense
    assert ti.resolve("W.Smith Jr.", "53") == 3


# --------------------------------------------------------------------------- synthetic season for the model


def synthetic_data(line_week5: bool = True) -> model.CFBData:
    """Team 10 (players 1-5) plays weeks 1-5; week 5 is the target. Team 20 is the opponent."""
    games = []
    for w in range(1, 6):
        games.append({"game_id": 1000 + w, "season": 2026, "week": w, "start_date": f"2026-09-{5 + 7 * w:02d}T19:00:00Z"
                      if 5 + 7 * w <= 30 else f"2026-10-{5 + 7 * w - 30:02d}T19:00:00Z",
                      "home_id": 10, "away_id": 20, "home_team": "Home U", "away_team": "Away St",
                      "home_abbreviation": "HOM", "away_abbreviation": "AWY", "neutral_site": False, "conference_game": True,
                      "completed": w < 5, "home_points": 30.0 if w < 5 else np.nan, "away_points": 20.0 if w < 5 else np.nan,
                      "fbs_game": True, "venue": "Field", "home_division": "fbs", "away_division": "fbs"})
    sched = pd.DataFrame(games)
    lines = []
    tt = []
    for w in range(1, 5):
        gid = 1000 + w
        # team plays: 30 pass attempts, 35 carries, 2 sacks
        tt.append({"season": 2026, "week": w, "game_id": gid, "team_id": 10, "team_pass_att": 30.0, "team_dropbacks": 32.0,
                   "team_carries": 35.0, "team_plays": 67.0, "team_targets": 30.0, "team_rec": 20.0, "team_pass_yds": 250.0,
                   "team_rush_yds": 150.0, "usage_ok": True})
        tt.append({"season": 2026, "week": w, "game_id": gid, "team_id": 20, "team_pass_att": 30.0, "team_dropbacks": 32.0,
                   "team_carries": 30.0, "team_plays": 62.0, "team_targets": 30.0, "team_rec": 18.0, "team_pass_yds": 200.0,
                   "team_rush_yds": 120.0, "usage_ok": True})
        # WR1 steady 6 targets; WR2 3 targets in weeks 1-2, 12 in weeks 3-4 (role grows); WR3 8 targets weeks 1-2 then gone;
        # RB1 20 carries; QB 30 attempts; WR4 played week 3 only... and missed week 4 (role vanished last game)
        def add(aid, name, **st):
            base = {k: 0.0 for k in cs.COUNT_STATS}
            base.update(st)
            for c in ("pass_att", "sacks", "carries", "targets"):  # every synthetic snap is competitive
                base[f"c_{c}"] = base[c]
            lines.append({"season": 2026, "week": w, "game_id": gid, "team_id": 10, "athlete_id": aid, "name": name,
                          "stat_source": "pbp", "usage_ok": True, **base})
        add(1, "Qb One", pass_att=30, pass_cmp=20, pass_yds=250, pass_td=2, sacks=2, rush_att=6, carries=4, rush_yds=20, carry_yds=26)
        add(2, "Rb One", carries=20, rush_att=20, rush_yds=100, carry_yds=100, rush_td=1, targets=2, rec=2, rec_yds=10)
        add(3, "Wr One", targets=6, rec=4, rec_yds=50)
        t2 = 3 if w <= 2 else 12
        add(4, "Wr Two", targets=t2, rec=t2 * 0.6, rec_yds=t2 * 8)
        if w <= 2:
            add(5, "Wr Three", targets=8, rec=5, rec_yds=60)
        if w == 3:
            add(6, "Wr Four", targets=5, rec=3, rec_yds=40)
    L = pd.DataFrame(lines)
    T = pd.DataFrame(tt)
    gl = pd.DataFrame({"game_id": [1005], "home_spread": [-14.0], "total": [64.0], "line_source": ["test"]}) if line_week5 \
        else pd.DataFrame(columns=["game_id", "home_spread", "total", "line_source"])
    ros = pd.DataFrame({"season": 2026, "team_id": 10, "athlete_id": [1, 2, 3, 4, 5, 6],
                        "position_abbreviation": ["QB", "RB", "WR", "WR", "WR", "WR"],
                        "first_name": ["Qb", "Rb", "Wr", "Wr", "Wr", "Wr"], "last_name": ["One", "One", "One", "Two", "Three", "Four"],
                        "jersey": ["1", "2", "3", "4", "5", "6"]})
    ros["full_name"] = ros["first_name"] + " " + ros["last_name"]
    ros["athlete_display_name"] = ros["full_name"]
    teams = pd.DataFrame({"team_id": [10, 20], "abbreviation": ["HOM", "AWY"], "school": ["Home U", "Away St"],
                          "location": ["Home U", "Away State"], "nickname": ["Home U", "Away St"],
                          "short_display_name": ["Home U", "Away St"], "display_name": ["Home U Hawks", "Away St Owls"],
                          "mascot": ["Hawks", "Owls"], "alt_name1": [None, None], "alt_name2": [None, None], "alt_name3": [None, None],
                          "is_fbs": [True, True]})
    return model.CFBData.build([2026], L, T, sched, gl, ros, teams)


def test_workload_adjustment_and_removal():
    d = synthetic_data()
    p = model.CFBParams(k_recent=20.0, alpha=1.0, k0=0.0, w_prev=0.0, beta=0.0)
    proj, removed = model.project_week(d, 2026, 5, "draftkings.cfb", p)
    P = proj.set_index("athlete_id")
    # WR2: season share (3+3+12+12)/120 = 0.25; last two 24/60 = 0.40; shrunk (24 + 20*0.25)/(60+20) = 0.3625
    assert P.loc[4, "season_share"] == pytest.approx(0.25)
    assert P.loc[4, "recent_share"] == pytest.approx((24 + 20 * 0.25) / (60 + 20))
    assert P.loc[4, "workload"] == pytest.approx(P.loc[4, "recent_share"] / 0.25)
    assert P.loc[4, "mean_played"] > P.loc[4, "fpg_s"]  # growing role projects above the season average
    # WR1 steady: workload 1, base = season average (k0 = 0, no prior season)
    assert P.loc[3, "workload"] == pytest.approx(1.0)
    assert P.loc[3, "base"] == pytest.approx(P.loc[3, "fpg_s"])
    # WR3 had no play in the last two games -> removed; WR4 missed only the latest game -> kept with a high p_zero
    assert 5 not in P.index and 5 in set(removed["athlete_id"])
    assert not P.loc[6, "appeared_last"] and P.loc[6, "p_zero"] > P.loc[3, "p_zero"]
    assert P.loc[6, "workload"] < 1.0


def test_shrinkage_and_environment():
    d = synthetic_data()
    p = model.CFBParams(k0=2.0, w_prev=0.0, beta=1.0, k_team=3.0)
    proj, _ = model.project_week(d, 2026, 5, "draftkings.cfb", p)
    P = proj.set_index("athlete_id")
    slope = model.role_prior_slope(d, 2026, 5, "draftkings.cfb")
    r = P.loc[2]
    prior = slope["RB"] * r["season_share"]
    assert r["base"] == pytest.approx((4 * r["fpg_s"] + 2.0 * prior) / (4 + 2.0))
    # implied home points (64 + 14) / 2 = 39 vs team scoring 30/game shrunk toward the league average (25 here)
    league = 25.0  # both teams' FBS games: (30 + 20) / 2
    team_avg = (4 * 30 + 3 * league) / (4 + 3)
    assert r["env"] == pytest.approx(min(39 / team_avg, p.env_hi) ** 1.0)
    no_line, _ = model.project_week(synthetic_data(line_week5=False), 2026, 5, "draftkings.cfb", p)
    assert (no_line["env"] == 1.0).all()
    # quantiles are ordered and the mean includes p_zero
    assert (proj["p10"] <= proj["p50"]).all() and (proj["p50"] <= proj["p90"]).all()
    assert np.allclose(proj["mean"], (1 - proj["p_zero"]) * proj["mean_played"])


def test_point_in_time_no_leakage():
    d = synthetic_data()
    proj, _ = model.project_week(d, 2026, 5, "draftkings.cfb")
    # add a monster target-week game: projections for week 5 must not change
    extra = d.lines[d.lines["week"] == 4].copy()
    extra["week"], extra["game_id"], extra["rec_yds"], extra["targets"] = 5, 1005, 500.0, 40.0
    d2 = model.CFBData.build([2026], pd.concat([d.lines.drop(columns=["pos"]), extra.drop(columns=["pos"])]),
                             d.team_totals, d.schedules, d.game_lines, d.rosters, d.teams)
    proj2, _ = model.project_week(d2, 2026, 5, "draftkings.cfb")
    a = proj.set_index("athlete_id")["mean"].sort_index()
    b = proj2.set_index("athlete_id")["mean"].sort_index()
    pd.testing.assert_series_equal(a, b)


def test_vectorised_tuning_mean_matches_project():
    from dfs import backtest_cfb as bt

    d = synthetic_data()
    p = model.CFBParams(alpha=0.9, alpha_down=0.2, k0=0.7, w_prev=0.3, beta=0.6, k_recent=15.0)
    f = model.features(d, 2026, 5, "draftkings.cfb", p)
    proj = model.project(f, model.role_prior_slope(d, 2026, 5, "draftkings.cfb", p.usage_basis), p)
    slope = model.role_prior_slope(d, 2026, 5, "draftkings.cfb", p.usage_basis)
    g = f[f["appeared_recent"]].copy()
    g["prior_role"] = [slope.get(pos, 0.0) * s for pos, s in zip(g["pos"], g["season_share"])]
    assert np.allclose(bt.mean_played_vec(g, p), proj["mean_played"].to_numpy())


def test_fit_tables_shapes():
    rng = np.random.default_rng(0)
    m_ = rng.uniform(3, 25, 2000)
    rows = pd.DataFrame({"pos": rng.choice(["QB", "RB", "WR", "TE"], 2000), "mean_played": m_,
                         "actual": m_ + rng.normal(0, 2 + 0.4 * m_)})
    disp = model.fit_dispersion(rows)
    for pos in ("QB", "RB", "WR", "TE"):
        assert disp[pos]["b"] == pytest.approx(0.4, abs=0.12) and 0.05 < disp[pos]["fit_below_p10"] < 0.15
    pzr = pd.DataFrame({"pos": "WR", "appeared_last": rng.random(1000) > 0.3, "exp_opps": rng.uniform(0, 15, 1000),
                        "played": rng.random(1000) > 0.2})
    pz = model.fit_pzero(pzr)
    assert set(pz["WR"]) == {"last", "missed_last"} and len(pz["WR"]["last"]) == len(model.OPP_BUCKETS) - 1


# --------------------------------------------------------------------------- team matching


TEAMS = pd.DataFrame([
    (1, "MIA", "Miami", "Miami", "Hurricanes", "Miami"), (2, "M-OH", "Miami (OH)", "Miami (OH)", "RedHawks", "Miami OH"),
    (3, "MSU", "Michigan State", "Michigan State", "Spartans", "Michigan St"),
    (4, "MSST", "Mississippi State", "Mississippi State", "Bulldogs", "Mississippi St"),
    (5, "OSU", "Ohio State", "Ohio State", "Buckeyes", "Ohio State"), (6, "OKST", "Oklahoma State", "Oklahoma State", "Cowboys", "Oklahoma St"),
    (7, "BC", "Boston College", "Boston College", "Eagles", "Boston College"), (8, "WMU", "Western Michigan", "Western Michigan", "Broncos", "W Michigan"),
    (9, "TTU", "Texas Tech", "Texas Tech", "Red Raiders", "Texas Tech"), (10, "ARK", "Arkansas", "Arkansas", "Razorbacks", "Arkansas"),
    (11, "TA&M", "Texas A&M", "Texas A&M", "Aggies", "Texas A&M"), (12, "LSU", "LSU", "LSU", "Tigers", "LSU"),
], columns=["team_id", "abbreviation", "school", "location", "mascot", "short_display_name"])
TEAMS["display_name"] = TEAMS["school"] + " " + TEAMS["mascot"]
TEAMS["nickname"] = TEAMS["short_display_name"]
for c in ("alt_name1", "alt_name2", "alt_name3"):
    TEAMS[c] = None
TEAMS["is_fbs"] = True
SCHED = pd.DataFrame([
    (501, 2026, 8, "2026-10-24T16:00:00Z", 7, 1, "Boston College", "Miami", False),  # Miami @ BC
    (502, 2026, 8, "2026-10-24T19:30:00Z", 3, 8, "Michigan State", "Western Michigan", False),  # WMU @ MSU
    (503, 2026, 8, "2026-10-24T23:00:00Z", 2, 4, "Miami (OH)", "Mississippi State", True),  # neutral: MSST vs M-OH
    (504, 2026, 8, "2026-10-24T20:00:00Z", 9, 5, "Texas Tech", "Ohio State", False),
    (505, 2026, 8, "2026-10-24T20:00:00Z", 12, 11, "LSU", "Texas A&M", False),
    (506, 2026, 9, "2026-10-31T20:00:00Z", 6, 10, "Oklahoma State", "Arkansas", False),
], columns=["game_id", "season", "week", "start_date", "home_id", "away_id", "home_team", "away_team", "neutral_site"])
SCHED["home_abbreviation"] = None
SCHED["away_abbreviation"] = None


def test_split_slate_game():
    assert split_slate_game("ALA@UGA 10/24/2026 03:30PM ET") == ("ALA", "UGA")
    assert split_slate_game("Mississippi State@LSU") == ("Mississippi State", "LSU")
    assert norm_code("Texas A&M") == "TEXASAM" and norm_code("M-OH") == "MOH"


def test_letters_score_prefers_clean_abbreviations():
    s_good = _ordered_abbrev_score("COST", ["Colorado", "State"])
    s_bad = _ordered_abbrev_score("COST", ["Chicago", "State"])
    assert s_good > s_bad
    assert _ordered_abbrev_score("TXST", ["Texas", "State"]) >= 0.8
    assert _ordered_abbrev_score("XYZ", ["Texas", "State"]) == 0.0


def test_match_slate_site_codes_and_ambiguity():
    # site-style codes: MIAMI (two Miamis), MICHST, WMICH, MOH vs MISSST listed reversed at a neutral site,
    # school names with spaces, and an ESPN abbreviation
    slate = ["MIAMI@BC", "WMICH@MICHST", "MISSST@MIAOH", "OHIOST@TEXASTECH", "TAMU@LSU"]
    rep = match_slate(slate, TEAMS, SCHED, season=2026, week=8)
    assert not rep.unmatched, rep.summary()
    assert rep.team_id("MIAMI") == 1 and rep.team_id("BC") == 7
    assert rep.team_id("MICHST") == 3 and rep.team_id("WMICH") == 8
    assert rep.team_id("MIAOH") == 2 and rep.team_id("MISSST") == 4
    assert rep.matches["MIAOH"].note == "" or "reversed" in rep.matches["MIAOH"].note
    assert rep.team_id("OHIOST") == 5 and rep.team_id("TEXASTECH") == 9
    assert rep.team_id("TAMU") == 11 and rep.games["TAMU@LSU"] == 505


def test_match_slate_infers_week_and_reports_unknown():
    rep = match_slate(["OKST@ARK", "QQQ@ZZZ"], TEAMS, SCHED, season=2026, week=None,
                      near=pd.Timestamp("2026-10-30", tz="UTC"))
    assert rep.games["OKST@ARK"] == 506
    assert any(u.get("game") == "QQQ@ZZZ" for u in rep.unmatched)
    assert rep.team_id("QQQ") is None
    pinned = match_slate(["SOONERS@BC"], TEAMS, SCHED, season=2026, week=8, overrides={"SOONERS": 1})
    assert pinned.team_id("SOONERS") == 1


# --------------------------------------------------------------------------- provider (offline)


def _cfb_contest(players: list[Player]) -> Contest:
    slots = [("QB", ["QB"]), ("RB", ["RB"]), ("RB", ["RB"]), ("WR", ["WR"]), ("WR", ["WR"]), ("WR", ["WR"]),
             ("FLEX", ["RB", "WR"]), ("SUPERFLEX", ["QB", "RB", "WR"])]
    rules = ContestRules(site="draftkings", sport="cfb", game_type="classic", salary_cap=50000,
                         slots=[Slot(name=n, eligible=e) for n, e in slots], scoring="draftkings.cfb")
    return Contest(rules=rules, players={p.key: p for p in players}, candidates=[], slot_headers=[s[0] for s in slots],
                   source_file="synthetic", file_kind="dk_salaries", games=["AWY@HOM"])


def test_provider_offline_contract():
    from dfs.projections.cfb_provider import CFBProvider
    from dfs.providers import SlateContext

    d = synthetic_data()
    mk = lambda name, pos, team="HOM", status=None, avg=None: Player(
        key=f"{team}|{name.lower()}|{pos}", name=name, team=team, opponent="AWY" if team == "HOM" else "HOM",
        positions=[pos], game="AWY@HOM", start_time=pd.Timestamp("2026-10-10T19:00:00Z").to_pydatetime(),
        injury_status=status, site_avg=avg)
    players = [mk("Qb One", "QB"), mk("Rb One", "RB"), mk("Wr One", "WR"), mk("Wr Two", "WR", status="Q"),
               mk("Wr Three", "WR"), mk("Wr Four", "WR", status="O"), mk("Nobody Known", "WR", avg=9.0)]
    contest = _cfb_contest(players)
    ctx = CFBProvider(season=2026, data=d).prepare(contest)
    assert isinstance(ctx, SlateContext)
    keys = {p.name: p.key for p in players}
    assert set(ctx.baseline) <= set(contest.players)
    assert keys["Qb One"] in ctx.baseline and ctx.baseline[keys["Qb One"]].source == "baseline"
    assert ctx.baseline[keys["Wr Two"]].p_zero >= 0.25  # site Q raises p_zero
    assert keys["Wr Three"] not in ctx.baseline  # no recent role
    assert keys["Wr Four"] in ctx.ruled_out and keys["Wr Four"] not in ctx.baseline
    nb = ctx.baseline[keys["Nobody Known"]]  # unmatched -> reported, site-average fallback
    assert nb.source == "site_avg" and any("Nobody Known" in n for n in ctx.notes)
    assert "AWY@HOM" in ctx.market and "implied" in ctx.market["AWY@HOM"]
    assert "no recent role" in ctx.known["AWY@HOM"] and "Wr Three" in ctx.known["AWY@HOM"]
    for txt in ctx.usage.values():
        assert blind_text(txt) is not None, txt  # nothing a blinded forecaster may not see
    assert all(blind_text(f.text) is not None for f in ctx.structured_facts["AWY@HOM"] if f.kind != "line")


def test_provider_falls_back_when_slate_unmatched_or_no_history():
    from dfs.projections.cfb_provider import CFBProvider

    d = synthetic_data()
    p = Player(key="ZZZ|some one|WR", name="Some One", team="ZZZ", opponent="YYY", positions=["WR"], game="YYY@ZZZ",
               start_time=pd.Timestamp("2026-10-10T19:00:00Z").to_pydatetime(), site_avg=8.0)
    c = _cfb_contest([p]).model_copy(update={"games": ["YYY@ZZZ"]})
    ctx = CFBProvider(season=2026, data=d).prepare(c)
    assert ctx.baseline[p.key].source == "site_avg" and "unavailable" in ctx.notes[0]
    # week 1: the team has no game yet -> reported site-average fallback, not silently dropped
    q = Player(key="HOM|qb one|QB", name="Qb One", team="HOM", opponent="AWY", positions=["QB"], game="AWY@HOM",
               start_time=pd.Timestamp("2026-09-12T19:00:00Z").to_pydatetime(), site_avg=20.0)
    ctx1 = CFBProvider(season=2026, week=1, data=synthetic_data()).prepare(_cfb_contest([q]))
    assert ctx1.baseline[q.key].source == "site_avg"
    assert any("NO TEAM HISTORY" in n for n in ctx1.notes)


def test_pipeline_uses_cfb_provider():
    from dfs.pipeline import RunConfig, provider_for
    from dfs.projections.cfb_provider import CFBProvider

    c = _cfb_contest([])
    prov = provider_for(c, RunConfig(season=2026, week=8))
    assert isinstance(prov, CFBProvider) and prov.season == 2026 and prov.week == 8


# --------------------------------------------------------------------------- real data (network)


@pytest.fixture(scope="module")
def real_data():
    return model.CFBData.load([2025, 2026])


@pytest.mark.network
def test_real_stat_lines_match_official_box(real_data):
    v = real_data.validation[2026]["box_after_repair"]
    assert v["fantasy_points"]["within_1"] >= 0.95
    for s in ("pass_yds", "rec_yds", "pass_td", "rec_td", "rush_td", "int_thrown"):
        assert abs(v["stats"][s]["total_diff_pct"]) < 1.0, (s, v["stats"][s])
    assert abs(v["stats"]["rush_yds"]["total_diff_pct"]) < 2.0


@pytest.mark.network
def test_real_stat_lines_match_season_aggregates(real_data):
    lines = real_data.lines[(real_data.lines["season"] == 2026) & (real_data.lines["stat_source"] == "pbp")]
    rep = cs.validate_against_aggregates(lines, cfbdata.load_season("passing", 2026), cfbdata.load_season("rushing", 2026),
                                         cfbdata.load_season("receiving", 2026), cfbdata.load_season("schedules", 2026))
    for group in ("passing", "rushing", "receiving"):
        for stat, r in rep[group]["stats"].items():
            assert abs(r["total_diff_pct"]) < 3.0, (group, stat, r)


@pytest.mark.network
def test_real_week_beats_naive(real_data):
    from dfs import backtest_cfb as bt

    rows, _ = bt.run(real_data, 2026, [6, 7], "draftkings.cfb", model.CFBParams())
    d = rows[rows["pool_dfs"]]
    mae = lambda c: float((d[c] - d["actual"]).abs().mean())
    assert len(d) > 500
    assert mae("mean") < mae("naive_avg") < mae("naive_last")


@pytest.mark.network
def test_real_provider_end_to_end(real_data, tmp_path):
    """A DK-format CFB salary file built from real 2026 week-7 games (made-up salaries/ids, site-style codes)."""
    from dfs.io.contest_file import load_contest
    from dfs.projections.cfb_provider import CFBProvider

    tg = real_data.team_games
    wk = tg[(tg["season"] == 2026) & (tg["week"] == 7) & tg["fbs_game"].fillna(False).astype(bool)]
    games = wk.drop_duplicates("game_id").head(6)
    teams = real_data.teams.drop_duplicates("team_id").set_index("team_id")
    code = lambda tid: str(teams.loc[tid, "school"]).upper().replace(" ", "")[:8]  # site-style, not ESPN's
    L = real_data.lines[(real_data.lines["season"] == 2026) & (real_data.lines["week"] < 7)]
    rows = ["Position,Name + ID,Name,ID,Roster Position,Salary,Game Info,TeamAbbrev,AvgPointsPerGame"]
    pid = 30000000
    for g in games.itertuples():
        home = wk[(wk["game_id"] == g.game_id) & (wk["is_home"] == 1)].iloc[0]
        away = wk[(wk["game_id"] == g.game_id) & (wk["is_home"] == 0)].iloc[0]
        ginfo = f"{code(away.team_id)}@{code(home.team_id)} 10/17/2026 03:30PM ET"
        for side in (home, away):
            top = (L[L["team_id"] == side.team_id].groupby(["athlete_id", "name", "pos"])["targets"].sum()
                   .reset_index().sort_values("targets", ascending=False))
            qb = (L[(L["team_id"] == side.team_id)].groupby(["athlete_id", "name", "pos"])["pass_att"].sum()
                  .reset_index().sort_values("pass_att", ascending=False).head(1))
            for r in pd.concat([qb, top.head(5)]).drop_duplicates("athlete_id").itertuples():
                pos = {"TE": "WR"}.get(r.pos, r.pos)
                roster = {"QB": "QB/SUPERFLEX", "RB": "RB/FLEX/SUPERFLEX"}.get(pos, "WR/FLEX/SUPERFLEX")
                pid += 1
                rows.append(f"{pos},{r.name} ({pid}),{r.name},{pid},{roster},{5000},{ginfo},{code(side.team_id)},10.0")
    rows.append(f"WR,Made Up Player ({pid + 1}),Made Up Player,{pid + 1},WR/FLEX/SUPERFLEX,3000,{ginfo},{code(home.team_id)},4.0")
    f = tmp_path / "DKSalaries_cfb.csv"
    f.write_text("\n".join(rows) + "\n")
    contest = load_contest(f)
    assert contest.rules.sport == "cfb"
    prov = CFBProvider(data=real_data)
    ctx = prov.prepare(contest)
    assert prov.team_report and len(prov.team_report.games) == len(games), prov.team_report.summary()
    assert any("season 2026 week 7" in n for n in ctx.notes)
    assert len(prov.unmatched) == 1 and prov.unmatched[0]["name"] == "Made Up Player"
    assert len(ctx.baseline) >= 0.8 * (len(contest.players) - 1)
    for g in contest.games:
        assert "implied" in ctx.market[g]  # week 7 lines exist in the betting file
        assert blind_text(ctx.usage[g]) is not None
