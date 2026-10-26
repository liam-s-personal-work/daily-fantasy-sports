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


def _left_early_box(minutes, starter=True):
    """Brunson (30 minutes a game last season) plays only ``minutes`` in the opener, NYK's last game; with
    fewer than 2 games this season his usual minutes are last season's."""
    box, ids = _box()
    opener = (box["athlete_display_name"] == "Jalen Brunson") & (box["season"] == 2027)
    box.loc[opener, "minutes"] = minutes
    box.loc[opener, "starter"] = starter
    return box, ids


def test_a_starter_who_left_early_is_flagged_and_research_is_told_to_check_him(tmp_path):
    c = _contest(tmp_path)
    ctx = NBAProvider(box=_left_early_box(8.0)[0]).prepare(c, now=TIP)
    k = next(k for k, p in c.players.items() if p.name == "Jalen Brunson")
    pr = ctx.baseline[k]
    assert pr.p_zero == 0.81 and k not in ctx.ruled_out  # under 30% of his usual: above the 0.37 for under 10 minutes
    assert pr.components["left_early"] == 1.0 and pr.components["uncertain"] == 1.0
    assert pr.components["p_zero_floor"] == 0.0  # not a floor that survives tonight's report
    f = next(f for fs in ctx.structured_facts.values() for f in fs if f.subject == "Jalen Brunson")
    assert f.text == ("Jalen Brunson played only 8 minutes in NYK's last game (Oct 21) after averaging 30: "
                      "possibly injured; check tonight's report")
    assert f.status == "none" and not agents.says_out(f.model_copy(update={"id": "NYK@BOS.S1"}))
    # the research desk gets him first in the known data and is told to search flagged players by name
    g = c.players[k].game
    task = next(t for t in agents.research_tasks(c, ctx.known, {}, now=TIP) if t.name == f"research:{g}")
    assert ctx.known[g].splitlines()[0] == f.text and f.text in task.prompt
    assert "check tonight's report" in task.system and "every player the known data says to check" in task.system
    # the chance grows the earlier he left: 13 of 30 minutes is the 40-50% tier
    assert NBAProvider(box=_left_early_box(13.0)[0]).prepare(c, now=TIP).baseline[k].p_zero == 0.43


def test_tonight_s_report_replaces_the_left_early_flag(tmp_path, monkeypatch):
    from dfs.projections import nba_minutes as nm
    monkeypatch.setattr(nm.MinutesModel, "load", classmethod(lambda cls, folder=None, tier="main": _StubModel()))
    c = _contest(tmp_path)
    prov = NBAProvider(box=_left_early_box(8.0)[0], minutes_dir=tmp_path / "no_rate_files_here")
    ctx = prov.prepare(c, now=TIP)
    k = next(k for k, p in c.players.items() if p.name == "Jalen Brunson")
    d = _dossier(c, k, ("probable", "high", "2026-10-23T21:00:00Z", "Brunson (ankle) probable tonight"))
    statused, _ = agents.apply_status(c, d, ctx.baseline)
    assert statused[k].p_zero == 0.05  # a cited status wins over the flag
    out, _ = prov.after_research(c, statused)
    assert out[k].p_zero == pytest.approx(1 - 0.9 * 0.95, abs=1e-3)
    out, _ = prov.after_research(c, ctx.baseline)  # no news: the flag's 81% stays on top of the model
    assert out[k].p_zero == pytest.approx(1 - 0.9 * (1 - 0.81), abs=1e-3)


def test_normal_minutes_bench_players_and_games_at_the_cutoff_are_not_flagged(tmp_path):
    from dfs.projections.nba_provider import left_early
    c = _contest(tmp_path)
    k = next(k for k, p in c.players.items() if p.name == "Jalen Brunson")

    def flagged(box, now=TIP):
        ctx = NBAProvider(box=box).prepare(c, now=now)
        return {c.players[j].name for j, pr in ctx.baseline.items() if pr.components.get("left_early")}

    assert flagged(_box()[0]) == set()  # 36 minutes in the opener: nobody
    assert flagged(_left_early_box(18.0)[0]) == set()  # 60% of his usual 30 is not enough
    bench, _ = _left_early_box(8.0, starter=False)
    assert flagged(bench) == set()  # a bench player's short night is usually the coach's call
    assert NBAProvider(box=bench).prepare(c, now=TIP).baseline[k].p_zero == 0.37  # by minutes only
    # a 5-minute start at the cutoff itself never counts; once it has tipped before the cutoff it does
    box, ids = _box()
    late = nba.normalize(pd.DataFrame([_row(ids["Jalen Brunson"], "Jalen Brunson", "NY", "BOS", "2026-10-23T23:00:00Z",
                                            2027, 5, 2, gid=999)]))
    box = pd.concat([box, late], ignore_index=True)
    assert flagged(box) == set() and left_early(box, TIP, 2027) == {}
    assert left_early(box, datetime(2026, 10, 24, 3, 0, tzinfo=timezone.utc), 2027) == {"NY": {ids["Jalen Brunson"]: (5.0, 30.0)}}
    # with two games this season his usual is this season's: 36 and 36 before a 17-minute start is under half
    third = nba.normalize(pd.DataFrame([_row(ids["Jalen Brunson"], "Jalen Brunson", "NY", "BOS", "2026-10-26T23:00:00Z",
                                             2027, 17, 8, gid=1001)]))
    box = pd.concat([box.assign(minutes=box["minutes"].where(box["game_id"] != 999, 36.0)), third], ignore_index=True)
    assert left_early(box, datetime(2026, 10, 27, 3, 0, tzinfo=timezone.utc), 2027)["NY"][ids["Jalen Brunson"]] == (17.0, 36.0)
    # an All-Star exhibition game (its own team code, 3 minutes) is not one of his club's games: his usual stays 36
    star = nba.normalize(pd.DataFrame([_row(ids["Jalen Brunson"], "Jalen Brunson", "STARS", "WORLD",
                                            "2026-10-25T01:00:00Z", 2027, 3, 2, gid=1002)]))
    early = left_early(pd.concat([box, star], ignore_index=True), datetime(2026, 10, 27, 3, 0, tzinfo=timezone.utc), 2027)
    assert early["NY"] == {ids["Jalen Brunson"]: (17.0, 36.0)}


class _StubModel:
    """Stands in for the learned boosters: every player plays with P=0.9 for 30 minutes."""

    def predict(self, feats):
        return np.full(len(feats), 0.9), np.full(len(feats), 30.0)


class _StubRate:
    """Stands in for the rate boosters: x1.1 on every player's points per minute (given the live rate features)."""

    def multiplier(self, feats):
        from dfs.projections import nba_rate as nr
        assert feats[nr.FEATS].shape == (len(feats), len(nr.FEATS))
        return np.full(len(feats), 1.1)


def test_minutes_model_applies_after_research_and_statuses_still_count(tmp_path, monkeypatch):
    from dfs.projections import nba_minutes as nm
    from dfs.projections import nba_rate as nr
    monkeypatch.setattr(nm.MinutesModel, "load", classmethod(lambda cls, folder=None, tier="main": _StubModel()))
    monkeypatch.setattr(nr.RateModel, "load", classmethod(lambda cls, folder=None: _StubRate()))
    c = _contest(tmp_path)
    prov = NBAProvider(box=_box()[0])
    ctx = prov.prepare(c, now=TIP)
    hart = next(k for k, p in c.players.items() if p.name == "Josh Hart")  # missing from the last box score
    brunson = next(k for k, p in c.players.items() if p.name == "Jalen Brunson")
    white = next(k for k, p in c.players.items() if p.name == "Derrick White")
    d = _dossier(c, white, ("questionable", "high", "2026-10-23T21:00:00Z", "White questionable"))
    statused, _ = agents.apply_status(c, d, ctx.baseline)
    out, notes = prov.after_research(c, statused)
    assert any("minutes model applied" in n for n in notes) and any("rate model applied" in n for n in notes)
    rate = ctx.baseline[brunson].components["rate"]
    assert out[brunson].mean == pytest.approx(0.9 * 30.0 * rate * 1.1, abs=0.01)  # no news: the models' numbers
    assert out[white].p_zero == pytest.approx(1 - 0.9 * 0.7, abs=1e-3)  # questionable keeps its 30% on top
    assert out[hart].p_zero == pytest.approx(1 - 0.9 * 0.4, abs=1e-3)  # absent last game, no news: 60% stays
    assert out[white].mean == pytest.approx(0.9 * 0.7 * 30.0 * ctx.baseline[white].components["rate"] * 1.1, abs=0.01)
    assert out[brunson].components["minutes_model"] == 30.0 and out[brunson].components["rate_model"] == 1.1


def test_without_rate_files_or_if_it_fails_the_minutes_model_result_stands(tmp_path, monkeypatch):
    from dfs.projections import nba_minutes as nm
    from dfs.projections import nba_rate as nr
    monkeypatch.setattr(nm.MinutesModel, "load", classmethod(lambda cls, folder=None, tier="main": _StubModel()))
    c = _contest(tmp_path)
    prov = NBAProvider(box=_box()[0], minutes_dir=tmp_path / "no_rate_files_here")
    ctx = prov.prepare(c, now=TIP)
    brunson = next(k for k, p in c.players.items() if p.name == "Jalen Brunson")
    rate = ctx.baseline[brunson].components["rate"]
    out, notes = prov.after_research(c, ctx.baseline)
    assert any("minutes model applied" in n for n in notes) and any("rate model not applied" in n for n in notes)
    assert out[brunson].mean == pytest.approx(0.9 * 30.0 * rate, abs=0.01)
    assert all(pr.components.get("rate_model") == 1.0 for pr in out.values() if "minutes_model" in pr.components)

    class Broken:
        def multiplier(self, feats):
            raise RuntimeError("boom")
    monkeypatch.setattr(nr.RateModel, "load", classmethod(lambda cls, folder=None: Broken()))
    out2, notes2 = prov.after_research(c, ctx.baseline)
    assert any("rate model not applied (boom)" in n for n in notes2)
    assert {k: v.mean for k, v in out2.items()} == {k: v.mean for k, v in out.items()}


def test_without_saved_model_files_the_baseline_stands(tmp_path):
    c = _contest(tmp_path)
    prov = NBAProvider(box=_box()[0], minutes_dir=tmp_path / "no_model_here")
    ctx = prov.prepare(c, now=TIP)
    out, notes = prov.after_research(c, ctx.baseline)
    assert not any("minutes model applied" in n for n in notes)
    assert all("minutes_model" not in pr.components for pr in out.values())


def test_shipped_minutes_model_loads_and_predicts_on_live_features(tmp_path):
    from dfs.projections import nba_minutes as nm
    m = nm.MinutesModel.load()
    if m is None:
        pytest.skip("no trained minutes model in data/learned (scripts/fit_nba_minutes.py)")
    c = _contest(tmp_path)
    prov = NBAProvider(box=_box()[0])
    ctx = prov.prepare(c, now=TIP)
    out, notes = prov.after_research(c, ctx.baseline)
    vals = [pr.components.get("minutes_model") for pr in out.values()]
    assert any("minutes model applied" in n for n in notes) and all(v is not None and 0 <= v <= 48 for v in vals)


def test_shipped_rate_model_loads_and_predicts_bounded_multipliers_on_live_features(tmp_path):
    from dfs.projections import nba_minutes as nm
    from dfs.projections import nba_rate as nr
    if nr.RateModel.load() is None or nm.MinutesModel.load() is None:
        pytest.skip("no trained rate / minutes model in data/learned (scripts/fit_nba_minutes.py)")
    c = _contest(tmp_path)
    prov = NBAProvider(box=_box()[0])
    ctx = prov.prepare(c, now=TIP)
    out, notes = prov.after_research(c, ctx.baseline)
    vals = np.array([pr.components.get("rate_model", np.nan) for pr in out.values() if "minutes_model" in pr.components])
    assert any("rate model applied" in n for n in notes) and len(vals) > 20 and np.isfinite(vals).all()
    assert ((vals >= np.exp(-0.15) - 1e-4) & (vals <= np.exp(0.15) + 1e-4)).all()


def test_rate_features_never_see_later_games():
    """Tonight's rate features are the same whether or not players (and the opponent) play after the cutoff."""
    from dfs.projections import nba_rate as nr
    box, ids = _box(include_tonight=True)  # a game at the cutoff itself must not count either
    later = nba.normalize(pd.DataFrame(
        [_row(ids[n], n, "NY", "BOS", "2026-10-25T23:00:00Z", 2027, 40, 60, reb=15, ast=12, gid=4242)
         for n in ROSTER["NYK"][:5]]
        + [_row(ids[n], n, "BOS", "NY", "2026-10-25T23:00:00Z", 2027, 40, 5, gid=4242) for n in ROSTER["BOS"][:5]]))
    names = ROSTER["NYK"] + ROSTER["BOS"][:6]
    slate = pd.DataFrame({"slate": "live", "season": 2027, "athlete_id": [ids[n] for n in names],
                          "team": ["NY"] * 8 + ["BOS"] * 6, "opp": ["BOS"] * 8 + ["NY"] * 6, "pos": "PG,SG",
                          "minutes": 28.0, "rate": 1.0, "rest_days": 2.0, "home": 1.0, "b2b": 0.0, "n_cur": 1.0,
                          "n_prev": 30.0, "moved": 0.0, "vacated_min": 0.0})
    cut = {"live": pd.Timestamp(TIP)}
    a = nr.build_rate_features(slate, box, cutoffs=cut)
    b = nr.build_rate_features(slate, pd.concat([box, later], ignore_index=True), cutoffs=cut)
    pd.testing.assert_frame_equal(a[nr.FEATS], b[nr.FEATS])
    # the opener counts; Hart (not in it, plays later) gets zeros like a player who never plays for the team
    assert a["lr_3"].notna().all() and a["own_dk_share"].notna().all() and a["wowy_max"].notna().all()


def test_rate_features_do_not_depend_on_how_many_seasons_are_loaded():
    """Training loads more past seasons than a live run: only this season and last may count."""
    from dfs.projections import nba_rate as nr
    box, ids = _box()
    old = nba.normalize(pd.DataFrame([_row(ids[n], n, "NY", "XXX", f"2024-02-{g + 1:02d}T00:00:00Z", 2024, 10, 2, gid=7000 + g)
                                      for n in ROSTER["NYK"] for g in range(20)]))
    names = ROSTER["NYK"] + ROSTER["BOS"][:6]
    slate = pd.DataFrame({"slate": "live", "season": 2027, "athlete_id": [ids[n] for n in names],
                          "team": ["NY"] * 8 + ["BOS"] * 6, "opp": ["BOS"] * 8 + ["NY"] * 6, "pos": "PG,SG",
                          "minutes": 28.0, "rate": 1.0, "rest_days": 2.0, "home": 1.0, "b2b": 0.0, "n_cur": 1.0,
                          "n_prev": 30.0, "moved": 0.0, "vacated_min": 0.0})
    cut = {"live": pd.Timestamp(TIP)}
    a = nr.build_rate_features(slate, box, cutoffs=cut)
    b = nr.build_rate_features(slate, pd.concat([old, box], ignore_index=True), cutoffs=cut)
    pd.testing.assert_frame_equal(a[nr.FEATS], b[nr.FEATS])


def test_minutes_features_never_see_later_games():
    """A player in uniform with no minutes yet gets the same features whether or not he plays after tonight."""
    from dfs.projections import nba_minutes as nm
    box, ids = _box()
    opener = box[(box["team_abbreviation"] == "NY") & (box["season"] == 2027)]["game_id"].iloc[0]
    rookie = nba.normalize(pd.DataFrame([_row(9001, "Rookie", "NY", "YYY", "2026-10-21T23:30:00Z", 2027, 0, 0,
                                              dnp=True, starter=False, gid=opener)]))
    later = nba.normalize(pd.DataFrame([_row(9001, "Rookie", "NY", "BOS", "2026-10-25T23:00:00Z", 2027, 25, 12,
                                             gid=4242)]))
    names = ROSTER["NYK"][:6]
    slate = pd.DataFrame({"slate": "live", "season": 2027, "athlete_id": [ids[n] for n in names] + [9001],
                          "team": "NY", "opp": "BOS", "pos": "G", "minutes": 25.0, "p_zero": 0.0})
    cut = {"live": pd.Timestamp(TIP)}
    a = nm.build_features(slate, pd.concat([box, rookie], ignore_index=True), cutoffs=cut)
    b = nm.build_features(slate, pd.concat([box, rookie, later], ignore_index=True), cutoffs=cut)
    pd.testing.assert_frame_equal(a[nm.NEW_FEATS].reset_index(drop=True), b[nm.NEW_FEATS].reset_index(drop=True))
    assert a.loc[a["athlete_id"] == 9001, "rest_any"].notna().all()  # the prior, not a missing value


def _starters_result(game, team, names, confirmed=True, url="https://www.nba.com/news/knicks-lineup",
                     as_of="2026-10-23T22:30:00Z"):
    from dfs.llm.backend import AgentResult
    return AgentResult(name=f"starters:{game}", ok=True, output={"teams": [
        {"team": team, "confirmed": confirmed, "starters": names, "source_url": url, "as_of": as_of}]})


def test_parse_starters_counts_only_confirmed_cited_lineups(tmp_path):
    c = _contest(tmp_path)
    now = datetime(2026, 10, 23, 22, 40, tzinfo=timezone.utc)
    five = ROSTER["NYK"][:5]
    ok = agents.parse_starters([_starters_result("NYK@BOS", "NYK", five)], c, now)
    assert {c.players[k].name for k in ok["NYK"]} == set(five)
    for bad in (_starters_result("NYK@BOS", "NYK", five, confirmed=False),
                _starters_result("NYK@BOS", "NYK", five, url="https://www.rotoballer.com/x"),
                _starters_result("NYK@BOS", "NYK", five, url=""),
                _starters_result("NYK@BOS", "NYK", five, as_of="2026-10-24T03:00:00Z"),
                _starters_result("NYK@BOS", "NYK", ["Nobody One", "Nobody Two", "Jalen Brunson"])):
        assert agents.parse_starters([bad], c, now) == {}


class _TierStub:
    def __init__(self, tier):
        self.tier = tier

    def predict(self, feats):
        if self.tier == "main":
            return np.full(len(feats), 0.9), np.full(len(feats), 20.0)
        st = feats["starter_tonight"].to_numpy()
        return np.full(len(feats), 0.95), np.where(st == 1, 34.0, 15.0)


def test_confirmed_starters_switch_their_team_to_the_starter_aware_tier(tmp_path, monkeypatch):
    from dfs.projections import nba_minutes as nm
    from dfs.projections.nba_provider import lastcall_projections
    monkeypatch.setattr(nm.MinutesModel, "load", classmethod(lambda cls, folder=None, tier="main": _TierStub(tier)))
    c = _contest(tmp_path)
    five = {k for k, p in c.players.items() if p.team == "NYK" and p.name in ROSTER["NYK"][:5]}
    out, notes = lastcall_projections(c, {}, set(), {"NYK": five}, TIP, provider=NBAProvider(box=_box()[0]))
    assert any("confirmed starters for 1 teams" in n for n in notes)
    for k, pr in out.items():
        team = c.players[k].team
        want = 34.0 if k in five else 15.0 if team == "NYK" else 20.0
        assert pr.components["minutes_model"] == want, (c.players[k].name, team)


@pytest.mark.network
def test_nba_last_call_reprojects_with_confirmed_starters(tmp_path):
    import json
    import shutil
    from pathlib import Path
    from dfs.lastcall import lastcall
    from dfs.llm.backend import FakeBackend
    src = Path("runs/live_2026-10-23/dr-nba-classic-20261023-202049-49c83dbf")
    if not src.exists():
        pytest.skip("live NBA run not on this machine")
    run_dir = tmp_path / src.name
    shutil.copytree(src, run_dir)
    rep = json.loads((run_dir / "report.json").read_text())
    rep["config"]["allow_lookahead"] = True  # the slate is in the past now; the fake agents see nothing new
    (run_dir / "report.json").write_text(json.dumps(rep))
    c = json.loads((run_dir / "contest.json").read_text())
    by_team = {}
    for k, p in c["players"].items():
        by_team.setdefault(p["team"], []).append(p["name"])

    def starters(t):
        game = t.name.split(":", 1)[1]
        teams = game.split("@")
        return {"teams": [{"team": tm, "confirmed": True, "starters": by_team[tm][:5],
                           "source_url": "https://www.nba.com/news/lineups", "as_of": "2026-10-23T22:30:00Z"}
                          for tm in teams if tm in by_team]}
    be = FakeBackend({"news": lambda t: {"updates": []}, "starters": starters})
    out = lastcall(run_dir, web=True, now=datetime(2026, 10, 23, 22, 35, tzinfo=timezone.utc), backend=be,
                   log=lambda s: None)
    assert len(out["starters"]) >= 10 and out["reprojected"] > 100
    assert check_legal_ok(out)


def check_legal_ok(out):
    return out["final"] is not None and len(out["final"].slots) == 8
