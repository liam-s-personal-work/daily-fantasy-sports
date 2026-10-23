"""NBA baseline: DK scoring, point-in-time history, matching, availability and status facts."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from dfs.data import nba
from dfs.io.contest_file import load_contest
from dfs.models import Fact, GameDossier
from dfs.projections.nba_provider import NBAParams, NBAProvider, player_table
from dfs.scoring import score
from dfs.stages import agents

TIP = datetime(2026, 10, 23, 23, 0, tzinfo=timezone.utc)
HEADER = "Position,Name + ID,Name,ID,Roster Position,Salary,Game Info,TeamAbbrev,AvgPointsPerGame"
POS = ["PG", "SG", "SF", "PF", "C", "PG", "SF", "C"]
ROSTER = {"NYK": ["Jalen Brunson", "Josh Hart", "Mikal Bridges", "Karl-Anthony Towns", "Mitchell Robinson",
                  "Miles McBride", "OG Anunoby", "Ariel Hukporti"],
          "BOS": ["Derrick White", "Payton Pritchard", "Jaylen Brown", "Jayson Tatum", "Neemias Queta",
                  "Anfernee Simons", "Sam Hauser", "Luka Garza"],
          "IND": ["Tyrese Haliburton", "Andrew Nembhard", "Aaron Nesmith", "Pascal Siakam", "Jay Huff",
                  "TJ McConnell", "Jarace Walker", "Tony Bradley"],
          "DAL": ["Kyrie Irving", "Max Christie", "Cooper Flagg", "PJ Washington", "Daniel Gafford",
                  "Brandon Williams", "Naji Marshall", "Dwight Powell"]}
ESPN = {"NYK": "NY", "BOS": "BOS", "IND": "IND", "DAL": "DAL"}


def _contest(tmp_path):
    rows, i = [HEADER], 1000
    for team, names in ROSTER.items():
        game = "NYK@BOS 10/23/2026 07:00PM ET" if team in ("NYK", "BOS") else "DAL@IND 10/23/2026 07:00PM ET"
        for n, pos in zip(names, POS):
            i += 1
            rows.append(f"{pos},{n} ({i}),{n},{i},{pos}/{'G' if pos in ('PG', 'SG') else 'F' if pos != 'C' else 'C'}/UTIL,"
                        f"{4000 + i % 7 * 900},{game},{team},20")
    path = tmp_path / "DKSalaries_nba.csv"
    path.write_text("\n".join(rows) + "\n")
    return load_contest(path)


def _row(aid, name, team, opp, start, season, minutes, pts, reb=5, ast=3, dnp=False, starter=True, pos="G", gid=None):
    return {"game_id": gid or hash((team, opp, start)) % 10**9, "season": season, "season_type": 2,
            "game_date": start[:10], "game_date_time": start, "athlete_id": aid, "athlete_display_name": name,
            "team_abbreviation": team, "opponent_team_abbreviation": opp, "home_away": "home", "minutes": minutes,
            "points": pts, "three_point_field_goals_made": 1, "rebounds": reb, "assists": ast, "steals": 1, "blocks": 0,
            "turnovers": 2, "starter": starter, "did_not_play": dnp, "athlete_position_abbreviation": pos}


def _box(include_tonight=False):
    """Two prior seasons and one game this season for every player, plus edge cases."""
    rows, aid = [], 0
    ids = {}
    for team, names in ROSTER.items():
        e = ESPN[team]
        for n in names:
            aid += 1
            ids[n] = aid
            for g in range(30):
                for season, year in ((2026, 2026), (2025, 2025)):
                    if n == "Tyrese Haliburton" and season == 2026:
                        continue  # missed all of last season
                    rows.append(_row(aid, n, e, "XXX", f"{year}-02-{g % 28 + 1:02d}T00:00:00Z", season, 30, 20,
                                     gid=hash((e, season, g)) % 10**9))
            if n != "Josh Hart":  # Hart is missing from the opener's box score
                rows.append(_row(aid, n, e, "YYY", "2026-10-21T23:30:00Z", 2027, 36, 30, gid=hash((e, "opener")) % 10**9))
    if include_tonight:  # a game that tips after the cutoff must never count
        rows.append(_row(ids["Jalen Brunson"], "Jalen Brunson", "NY", "BOS", "2026-10-23T23:00:00Z", 2027, 48, 80,
                         gid=999))
    return nba.normalize(pd.DataFrame(rows)), ids


def test_dk_points_match_the_scoring_table():
    rng = np.random.default_rng(0)
    df = pd.DataFrame({c: rng.integers(0, 16, 300) for c in ("points", "rebounds", "assists", "steals", "blocks",
                                                             "turnovers", "three_point_field_goals_made")})
    mine = nba.dk_points(df)
    for i, r in df.iterrows():
        s = {"pts": r.points, "fg3m": r.three_point_field_goals_made, "reb": r.rebounds, "ast": r.assists,
             "stl": r.steals, "blk": r.blocks, "tov": r.turnovers}
        assert mine[i] == pytest.approx(score(s, "draftkings.nba", position="PG"), abs=0.01)


def test_baseline_is_point_in_time_and_borrows_an_older_season(tmp_path):
    c = _contest(tmp_path)
    box, ids = _box(include_tonight=True)
    ctx = NBAProvider(box=box).prepare(c, now=datetime(2026, 10, 23, 20, 0, tzinfo=timezone.utc))
    clean = NBAProvider(box=_box()[0]).prepare(c, now=datetime(2026, 10, 23, 20, 0, tzinfo=timezone.utc))
    k = next(k for k, p in c.players.items() if p.name == "Jalen Brunson")
    assert ctx.baseline[k].mean == clean.baseline[k].mean  # tonight's 80-point game is invisible
    hali = next(k for k, p in c.players.items() if p.name == "Tyrese Haliburton")
    assert ctx.baseline[hali].components["n_prev"] == 30  # 2024-25 stands in for the missed 2025-26
    assert ctx.baseline[hali].components["minutes"] > 30
    assert len(ctx.ids) == len(c.players) and ctx.season == 2027


def test_missing_from_the_last_box_score_raises_p_zero_without_ruling_out(tmp_path):
    c = _contest(tmp_path)
    ctx = NBAProvider(box=_box()[0]).prepare(c, now=TIP)
    hart = next(k for k, p in c.players.items() if p.name == "Josh Hart")
    assert ctx.baseline[hart].p_zero == NBAParams().p_zero_missed and hart not in ctx.ruled_out
    facts = [f for fs in ctx.structured_facts.values() for f in fs]
    assert [f.subject for f in facts] == ["Josh Hart"] and facts[0].status == "none"
    assert not agents.says_out(facts[0].model_copy(update={"id": "NYK@BOS.S1"}))


def test_minutes_shrink_toward_last_season():
    box, _ = _box()
    t = player_table(box, TIP, 2027, NBAParams(k_min=3.0, prior_mpg_scale=1.0))
    r = t.loc[1]  # Brunson: 36 minutes in one game this season, 30 a game last season
    assert r["minutes"] == pytest.approx((36 + 3 * 30) / 4)


def test_team_codes_and_moved_players(tmp_path):
    assert nba.espn_team("NYK") == "NY" and nba.espn_team("PHO") == "PHX" and nba.espn_team("BOS") == "BOS"
    c = _contest(tmp_path)
    box, ids = _box()
    box.loc[box["athlete_display_name"] == "Kyrie Irving", "team_abbreviation"] = "BKN"  # listed elsewhere before
    ctx = NBAProvider(box=box).prepare(c, now=TIP)
    k = next(k for k, p in c.players.items() if p.name == "Kyrie Irving")
    assert ctx.ids[k] == str(ids["Kyrie Irving"])


def _dossier(c, k, *facts):
    g = c.players[k].game
    return {g: GameDossier(game=g, facts=[Fact(id=f"{g}.F{i}", subject=c.players[k].name, team=c.players[k].team,
                                               kind="status", text=t, status=s, confidence=conf, as_of=asof,
                                               source_url="https://www.nba.com/news/x")
                                          for i, (s, conf, asof, t) in enumerate(facts, 1)])}


def test_cited_status_sets_p_zero_latest_fact_wins(tmp_path):
    c = _contest(tmp_path)
    ctx = NBAProvider(box=_box()[0]).prepare(c, now=TIP)
    hart = next(k for k, p in c.players.items() if p.name == "Josh Hart")
    before = ctx.baseline[hart]
    d = _dossier(c, hart, ("questionable", "high", "2026-10-23T17:30:00Z", "Hart questionable (ankle)"),
                 ("probable", "high", "2026-10-23T21:30:00Z", "Hart upgraded to probable"))
    out, changes = agents.apply_status(c, d, ctx.baseline)
    assert out[hart].p_zero == 0.05 and changes[0]["status"] == "probable"
    assert out[hart].mean == pytest.approx(before.mean / (1 - before.p_zero) * 0.95, abs=1e-3)
    assert (out[hart].p10, out[hart].p90) == (before.p10, before.p90)  # given he plays, nothing changes


def test_low_confidence_or_other_players_status_is_ignored(tmp_path):
    c = _contest(tmp_path)
    ctx = NBAProvider(box=_box()[0]).prepare(c, now=TIP)
    hart = next(k for k, p in c.players.items() if p.name == "Josh Hart")
    brunson = next(k for k, p in c.players.items() if p.name == "Jalen Brunson")
    d = _dossier(c, hart, ("active", "low", "2026-10-23T21:00:00Z", "rumor: Hart will play"))
    assert agents.apply_status(c, d, ctx.baseline)[1] == []
    d = _dossier(c, brunson, ("doubtful", "high", "2026-10-23T21:00:00Z", "Brunson doubtful"))
    out, changes = agents.apply_status(c, d, ctx.baseline)
    assert [ch["player"] for ch in changes] == [brunson] and out[hart] == ctx.baseline[hart]


def test_moved_players_lean_on_this_season_and_rookies_get_the_small_prior():
    box, ids = _box()
    moved = box["athlete_display_name"] == "Kyrie Irving"
    box.loc[moved & (box["season"] < 2027), "team_abbreviation"] = "BKN"  # new team this season
    p = NBAParams(prior_mpg_scale=1.0)
    t = player_table(box, TIP, 2027, p)
    r = t.loc[ids["Kyrie Irving"]]
    assert bool(r["moved"]) and r["minutes"] == pytest.approx((36 + p.k_min_moved * 30) / (1 + p.k_min_moved))
    rookie = box[box["athlete_display_name"] == "Ariel Hukporti"].copy()
    rookie = rookie[rookie["season"] == 2027].assign(athlete_id=777, athlete_display_name="New Rookie")
    t = player_table(pd.concat([box, rookie]), TIP, 2027, p)
    assert t.loc[777, "minutes"] == pytest.approx((36 + p.rookie_k_min * p.rookie_minutes) / (1 + p.rookie_k_min))


def test_quantiles_follow_the_fitted_spread(tmp_path):
    c = _contest(tmp_path)
    ctx = NBAProvider(box=_box()[0]).prepare(c, now=TIP)
    k = next(k for k, p in c.players.items() if p.name == "Jalen Brunson")
    pr, p = ctx.baseline[k], NBAParams()
    given_play = pr.mean / (1 - pr.p_zero)
    sd = p.sd_a + p.sd_b * given_play
    assert pr.p10 == pytest.approx(given_play - 1.17 * sd, abs=0.3) and pr.p90 == pytest.approx(given_play + 1.36 * sd, abs=0.3)
    assert pr.p_zero == 0.02  # played 36 minutes last game


def test_team_minutes_are_reconciled_after_statuses(tmp_path):
    from dfs.projections.nba_provider import reconcile_minutes
    c = _contest(tmp_path)
    ctx = NBAProvider(box=_box()[0]).prepare(c, now=TIP)
    hart = next(k for k, p in c.players.items() if p.name == "Josh Hart")
    nyk = [k for k in ctx.baseline if c.players[k].team == "NYK"]
    exp = lambda b: sum(b[k].components["minutes"] * (1 - b[k].p_zero) for k in nyk)
    assert 200 < exp(ctx.baseline) < 242  # seven regulars near 33 minutes and Hart at 40% of 29
    out, f = reconcile_minutes(c, ctx.baseline, total=200)  # a tight cap: the regulars are cut to the floor
    assert f["NYK"] == 0.85 and out[hart] == ctx.baseline[hart]  # the uncertain player keeps his minutes
    k = next(k for k in nyk if k != hart)
    assert out[k].mean == pytest.approx(ctx.baseline[k].mean * 0.85, abs=1e-3)
    # a team already at or under 242 is left alone
    small = {k: ctx.baseline[k].model_copy(update={"components": dict(ctx.baseline[k].components, minutes=20.0)})
             for k in nyk}
    assert reconcile_minutes(c, small)[1] == {} and exp(small) < 242


def test_a_healthy_scratch_is_not_lifted_by_a_probable_listing(tmp_path):
    c = _contest(tmp_path)
    box, ids = _box()
    hart_row = box[box["athlete_display_name"] == "Jalen Brunson"].iloc[[-1]].assign(
        athlete_id=ids["Josh Hart"], athlete_display_name="Josh Hart", minutes=0.0, did_not_play=True)
    box = pd.concat([box, hart_row], ignore_index=True)  # in uniform for the opener, did not play
    ctx = NBAProvider(box=box).prepare(c, now=TIP)
    hart = next(k for k, p in c.players.items() if p.name == "Josh Hart")
    pr, p = ctx.baseline[hart], NBAParams()
    assert pr.p_zero == p.p_zero_benched and pr.components["minutes"] == p.benched_minutes
    assert "coach's decision" in next(f.text for fs in ctx.structured_facts.values() for f in fs)
    d = _dossier(c, hart, ("probable", "medium", "2026-10-23T21:00:00Z", "Two-way player listed as probable"))
    out, _ = agents.apply_status(c, d, ctx.baseline)
    assert out[hart].p_zero == p.p_zero_benched  # available is not the same as in the rotation
