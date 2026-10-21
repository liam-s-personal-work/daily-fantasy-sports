"""Deterministic core: file parsing, rules, scoring, distributions, simulation, solver, upload."""

from __future__ import annotations

import csv
import io
import itertools
from pathlib import Path

import numpy as np
import pytest

from dfs.distributions import (mixture_mean, played_mean, played_sd, quantiles_from_mean_sd,
                               sample_from_uniform)
from dfs.io.contest_file import ContestFileError, load_contest, norm_name, player_key
from dfs.io.upload import UploadError, upload_rows
from dfs.models import Lineup
from dfs.optimize.ilp import Infeasible, SolveOptions, check_legal, solve, solve_many
from dfs.rules import load_rules, registry_keys
from dfs.scoring import innings_to_outs, score
from dfs.sim import build_correlation, simulate

FIX = Path(__file__).parent / "fixtures"


# ----------------------------------------------------------------------------- files

def test_detects_every_fixture_kind():
    kinds = {f.name: load_contest(f).file_kind for f in FIX.glob("*.csv")}
    assert kinds == {
        "dk_salaries_classic.csv": "dk_salaries", "dk_entries_classic.csv": "dk_entries",
        "dk_salaries_showdown.csv": "dk_salaries", "fd_players_classic.csv": "fd_players",
        "fd_players_single_game.csv": "fd_players", "fd_entries_classic.csv": "fd_entries",
        "fd_players_single_game_legacy.csv": "fd_players", "fd_players_classic_no_roster_col.csv": "fd_players",
    }


def test_dk_classic_rows_and_rules():
    c = load_contest(FIX / "dk_salaries_classic.csv")
    assert (c.rules.site.value, c.rules.sport, c.rules.game_type) == ("draftkings", "nfl", "classic")
    assert c.rules.salary_cap == 50000 and c.rules.min_games == 2
    assert c.slot_headers == ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "DST"]
    rb = next(x for x in c.candidates if c.players[x.player_key].positions == ["RB"])
    assert rb.eligible_slots == ["RB", "FLEX"]
    dst = next(p for p in c.players.values() if p.positions == ["DST"])
    assert dst.name == dst.name.strip() and dst.key.endswith("|dst|DST")
    p = next(iter(c.players.values()))
    assert p.game and "@" in p.game and p.start_time is not None and p.start_time.tzinfo is not None
    assert p.opponent in p.game.split("@")
    assert {pl.injury_status for pl in c.players.values()} >= {None, "OUT"}


def test_dk_entries_locked_and_ids():
    c = load_contest(FIX / "dk_entries_classic.csv")
    assert [e.entry_id for e in c.entries] == ["5300000001", "5300000002", "5300000003"]
    e0 = c.entries[0]
    assert e0.locked[0] is True and not any(e0.locked[1:])
    assert all(x and x.isdigit() for x in e0.current)
    assert c.entries[2].current == [None] * 9  # reservation
    assert e0.entry_fee == "$3 "  # fee kept verbatim (DK writes a trailing space)


def test_dk_showdown_cpt_rows():
    c = load_contest(FIX / "dk_salaries_showdown.csv")
    assert c.rules.game_type == "showdown" and c.rules.multiplier_rows
    assert c.rules.slots[0].name == "CPT" and c.rules.slots[0].points_multiplier == 1.5
    for k in c.players:
        rows = c.candidates_for(k)
        assert sorted(r.eligible_slots[0] for r in rows) == ["CPT", "FLEX"]
        cpt = next(r for r in rows if r.eligible_slots == ["CPT"])
        flex = next(r for r in rows if r.eligible_slots == ["FLEX"])
        assert cpt.site_id != flex.site_id and abs(cpt.salary - 1.5 * flex.salary) <= 100
    assert not [w for w in c.warnings if "CPT rows" in w]


def test_fd_classic_and_single_game():
    c = load_contest(FIX / "fd_players_classic.csv")
    assert (c.rules.site.value, c.rules.game_type, c.rules.salary_cap) == ("fanduel", "classic", 60000)
    assert c.rules.max_per_team == 4 and c.slot_headers[-1] == "DEF"
    d = next(x for x in c.candidates if c.players[x.player_key].positions == ["D"])
    assert d.eligible_slots == ["DEF"]
    sg = load_contest(FIX / "fd_players_single_game.csv")
    assert sg.rules.game_type == "single_game"
    for k in sg.players:
        rows = sg.candidates_for(k)
        mvp = next(r for r in rows if r.eligible_slots == ["MVP"])
        flex = next(r for r in rows if r.eligible_slots == ["FLEX"])
        assert mvp.site_id == flex.site_id and mvp.salary == int(flex.salary * 1.5)


def test_fd_legacy_single_game_has_no_mvp_premium():
    c = load_contest(FIX / "fd_players_single_game_legacy.csv")
    assert c.rules.game_type == "single_game_legacy" and len(c.rules.slots) == 5
    for k in c.players:
        rows = c.candidates_for(k)
        assert len({r.salary for r in rows}) == 1  # MVP costs the FLEX salary
    assert any("pre-Aug-2025" in w for w in c.warnings)


def test_fd_list_without_roster_position_uses_saved_slots():
    a = load_contest(FIX / "fd_players_classic.csv")
    b = load_contest(FIX / "fd_players_classic_no_roster_col.csv")
    elig = lambda c: sorted((x.site_id, tuple(x.eligible_slots)) for x in c.candidates)
    assert elig(a) == elig(b)


def test_fd_entries_template():
    c = load_contest(FIX / "fd_entries_classic.csv")
    assert [e.contest_id for e in c.entries] == ["140001-279208572", "140001-279208573"]
    assert c.entries[0].contest_name == "NFL Sunday Million"
    assert len(c.candidates) == 48


def test_unknown_file_is_rejected(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("foo,bar\n1,2\n")
    with pytest.raises(ContestFileError):
        load_contest(p)


@pytest.mark.parametrize("raw,expect", [("D.J. Moore", "dj moore"), ("Kenneth Walker III", "kenneth walker"),
                                        ("Marvin Harrison Jr.", "marvin harrison"), ("Ja'Marr Chase", "jamarr chase"),
                                        ("Amon-Ra St. Brown", "amon ra st brown"), ("Titans ", "titans")])
def test_norm_name(raw, expect):
    assert norm_name(raw) == expect


def test_player_key_dst():
    assert player_key("buf", "Bills ", "DST") == player_key("BUF", "Buffalo Bills", "D") == "BUF|dst|DST"


def test_registry_loads_every_entry():
    for key in registry_keys():
        site, sport, gt = key.split(".")
        r = load_rules(site, sport, gt)
        assert r.salary_cap > 0 and r.roster_size >= 5


# ----------------------------------------------------------------------------- scoring

@pytest.mark.parametrize("table,role,stats,expected", [
    ("draftkings.nfl", "offense", {"rec": 12, "rec_yds": 165}, 31.5),  # DK-published 2026 line
    ("draftkings.nfl", "offense", {"rush_yds": 128, "rush_td": 2, "rec": 2, "rec_yds": 11}, 30.9),
    ("draftkings.nfl", "offense", {"rec": 11, "rec_yds": 87, "rec_td": 1}, 25.7),
    ("draftkings.nfl", "dst", {"sack": 4, "interception": 1, "fumble_recovery": 1, "points_allowed": 6}, 15),
    ("draftkings.nfl", "dst", {"sack": 4, "interception": 1, "fumble_recovery": 1, "points_allowed": 3, "def_td": 1}, 21),
    ("fanduel.nfl", "offense", {"rec": 12, "rec_yds": 165}, 25.5),
    ("draftkings.nfl", "offense", {"pass_yds": 300, "pass_td": 2, "int_thrown": 1, "rush_yds": 20}, 24.0),
    ("draftkings.nfl", "offense", {"pass_yds": 299, "pass_td": 2, "int_thrown": 1, "rush_yds": 20}, 20.96),
    ("draftkings.nfl", "offense", {"rush_yds": 99, "rush_td": 1, "rec": 3, "rec_yds": 20, "fumble_lost": 1}, 19.9),
    ("fanduel.nfl", "offense", {"rush_yds": 99, "rush_td": 1, "rec": 3, "rec_yds": 20, "fumble_lost": 1}, 17.4),
    ("draftkings.nfl", "dst", {"points_allowed": 35}, -4),
    ("draftkings.nfl", "dst", {"points_allowed": 0}, 10),
    ("fanduel.nfl", "kicker", {"xp_made": 2, "fg_made_distances": [39, 40, 55]}, 14),
])
def test_scoring_examples(table, role, stats, expected):
    assert score(stats, table, role=role) == pytest.approx(expected, abs=1e-9)


def test_points_allowed_tiers_cover_every_score():
    for pa in range(0, 80):
        score({"points_allowed": pa}, "draftkings.nfl", role="dst")


def test_role_from_position():
    assert score({"sack": 2}, "fanduel.nfl", position="D") == 2
    assert score({"rec": 1}, "draftkings.nfl", position="WR") == 1


def test_innings_to_outs():
    assert innings_to_outs("6.2") == 20 and innings_to_outs(9) == 27


# ----------------------------------------------------------------------------- distributions

@pytest.mark.parametrize("q", [(5, 12, 25), (10, 11, 30), (0, 3, 4), (14, 20, 26)])
def test_two_piece_matches_quantiles_and_moments(q):
    u = np.random.default_rng(0).random(400_000)
    x = sample_from_uniform(u, *q)
    assert np.quantile(x, [0.1, 0.5, 0.9]) == pytest.approx(q, abs=0.1)
    assert x.mean() == pytest.approx(played_mean(*q), abs=0.05)
    assert x.std() == pytest.approx(played_sd(*q), rel=0.01)


def test_zero_mass_and_mixture_mean():
    u = np.random.default_rng(1).random(400_000)
    x = sample_from_uniform(u, 5, 12, 25, p_zero=0.2)
    assert (x == 0).mean() == pytest.approx(0.2, abs=0.005)
    assert x.mean() == pytest.approx(mixture_mean(5, 12, 25, 0.2), abs=0.05)


def test_quantiles_from_mean_sd_roundtrip():
    q = quantiles_from_mean_sd(14.0, 7.0, skew=0.3)
    assert played_mean(*q) == pytest.approx(14.0, abs=1e-6)
    assert played_sd(*q) == pytest.approx(7.0, abs=1e-6)
    assert q[2] - q[1] > q[1] - q[0]  # right-skewed


# ----------------------------------------------------------------------------- simulation

def test_simulation_correlation_and_psd(toy_contest):
    contest, projs = toy_contest
    keys, sims = simulate(projs, contest.players, "nfl", n_sims=20000, seed=3)
    assert sims.shape == (20000, len(keys)) and np.isfinite(sims).all()
    qb = keys.index("T00:QB0")
    wr = keys.index("T00:WR0")
    opp_dst = keys.index("T01:DST0")
    r = np.corrcoef(sims[:, qb], sims[:, wr])[0, 1]
    assert 0.25 < r < 0.5
    assert np.corrcoef(sims[:, qb], sims[:, opp_dst])[0, 1] < -0.15
    c = build_correlation([contest.players[k] for k in keys], "nfl")
    assert np.linalg.eigvalsh(c).min() > 0


def test_simulation_is_deterministic(toy_contest):
    contest, projs = toy_contest
    a = simulate(projs, contest.players, "nfl", n_sims=1000, seed=5)[1]
    b = simulate(projs, contest.players, "nfl", n_sims=1000, seed=5)[1]
    assert np.array_equal(a, b)


# ----------------------------------------------------------------------------- solver

def _brute_force(contest, pts):
    """Exhaustive optimum for tiny slates (checks the CP-SAT model, not its output format)."""
    from dfs.optimize.ilp import lineup_points
    slots = [s.name for s in contest.rules.slots]
    best = -1e9
    opts = {s: [c for c in contest.candidates if s in c.eligible_slots] for s in set(slots)}
    for combo in itertools.product(*[opts[s] for s in slots]):
        keys = [c.player_key for c in combo]
        if len(set(keys)) < len(keys) or len({c.site_id for c in combo}) < len(combo):
            continue
        if sum(c.salary for c in combo) > contest.rules.salary_cap:
            continue
        games = {contest.players[k].game for k in keys}
        if contest.rules.min_games and len(games) < contest.rules.min_games:
            continue
        best = max(best, sum(pts[k] * s.points_multiplier for k, s in zip(keys, contest.rules.slots)))
    return best


def _tiny(contest):
    """Keep 1 QB/DST and 2 RB/WR per game so brute force stays small but min_games still bites."""
    keep, seen = [], {}
    for c in contest.candidates:
        p = contest.players[c.player_key]
        k = (p.positions[0], p.game)
        if seen.get(k, 0) < (2 if p.positions[0] in ("RB", "WR") else 1):
            seen[k] = seen.get(k, 0) + 1
            keep.append(c)
    return contest.model_copy(update={"candidates": keep})


def test_solver_matches_brute_force_on_tiny_slate():
    from tests.conftest import make_toy_contest
    contest, projs = make_toy_contest(seed=9, n_games=2)
    tiny = _tiny(contest)
    tiny = tiny.model_copy(update={"rules": tiny.rules.model_copy(
        update={"slots": [s for s in tiny.rules.slots if s.name in ("QB", "RB", "WR", "DST")][:5]})})
    pts = {k: p.mean for k, p in projs.items()}
    lu = solve(tiny, pts)
    assert lu.mean == pytest.approx(_brute_force(tiny, pts), abs=0.01)


def test_solver_respects_rules(toy_contest):
    contest, projs = toy_contest
    pts = {k: p.mean for k, p in projs.items()}
    lu = solve(contest, pts)
    assert check_legal(contest, lu) == []
    top = lu.slots[0].player_key
    lu2 = solve(contest, pts, SolveOptions(exclude_keys={top}))
    assert top not in lu2.player_keys and lu2.mean <= lu.mean
    some = next(k for k in pts if k not in lu.player_keys and contest.players[k].positions == ["WR"])
    lu3 = solve(contest, pts, SolveOptions(lock_keys={some}))
    assert some in lu3.player_keys and check_legal(contest, lu3) == []
    stacked = solve(contest, pts, SolveOptions(qb_stack=2, bring_back=1))
    qb = next(s for s in stacked.slots if s.slot == "QB").player_key
    team, opp = contest.players[qb].team, contest.players[qb].opponent
    mates = [k for k in stacked.player_keys if contest.players[k].team == team and contest.players[k].positions[0] in ("WR", "TE")]
    assert len(mates) >= 2 and any(contest.players[k].team == opp for k in stacked.player_keys)


def test_solve_many_is_diverse_and_descending(toy_contest):
    contest, projs = toy_contest
    pts = {k: p.mean for k, p in projs.items()}
    many = solve_many(contest, pts, 5, SolveOptions(min_unique=2))
    assert len(many) == 5
    assert all(a.mean >= b.mean - 1e-6 for a, b in zip(many, many[1:]))
    for a, b in itertools.combinations(many, 2):
        assert len(set(a.player_keys) & set(b.player_keys)) <= 7


def test_infeasible_lock_and_exclude(toy_contest):
    contest, projs = toy_contest
    pts = {k: p.mean for k, p in projs.items()}
    k = next(iter(pts))
    with pytest.raises(Infeasible):
        solve(contest, pts, SolveOptions(lock_keys={k}, exclude_keys={k}))


def test_showdown_and_single_game_solve_legally():
    for f in ("dk_salaries_showdown.csv", "fd_players_single_game.csv"):
        c = load_contest(FIX / f)
        pts = {k: (p.site_avg or 0) for k, p in c.players.items()}
        lu = solve(c, pts)
        assert check_legal(c, lu) == []
        assert len(set(lu.player_keys)) == 6
        assert lu.mean == pytest.approx(sum(pts[s.player_key] * s.points_multiplier for s in lu.slots), abs=1e-3)


def test_fd_max_per_team():
    c = load_contest(FIX / "fd_players_classic.csv")
    pts = {k: 0.0 for k in c.players}
    team = next(iter(c.players.values())).team
    for k, p in c.players.items():
        if p.team == team:
            pts[k] = 50.0  # make one team irresistible
    lu = solve(c, pts)
    assert sum(c.players[k].team == team for k in lu.player_keys) == 4 and check_legal(c, lu) == []


# ----------------------------------------------------------------------------- upload

def test_template_upload_for_salary_file():
    c = load_contest(FIX / "dk_salaries_classic.csv")
    lu = solve(c, {k: (p.site_avg or 0) for k, p in c.players.items()})
    rows = upload_rows(c, [lu])
    assert rows[0] == c.slot_headers and rows[1] == [s.site_id for s in lu.slots]


def test_entries_upload_keeps_locked_and_only_changed():
    c = load_contest(FIX / "dk_entries_classic.csv")
    e0 = c.entries[0]
    locked_id = e0.current[0]
    locked_key = next(x.player_key for x in c.candidates if x.site_id == locked_id)
    pts = {k: (p.site_avg or 0) for k, p in c.players.items()}
    lu = solve(c, pts, SolveOptions(lock_keys={locked_key}))
    rows = upload_rows(c, [lu, lu], entry_ids=["5300000001", "5300000003"])
    assert rows[0][:4] == ["Entry ID", "Contest Name", "Contest ID", "Entry Fee"]
    assert rows[1][0] == "5300000001" and rows[1][3] == "$3 " and rows[1][4] == locked_id
    other = solve(c, pts, SolveOptions(exclude_keys={locked_key}))
    with pytest.raises(UploadError):
        upload_rows(c, [other], entry_ids=["5300000001"])
    # an unchanged lineup is left out of the file
    same = Lineup(slots=[s.model_copy(update={"site_id": e0.current[i]}) for i, s in enumerate(lu.slots)])
    assert len(upload_rows(c, [same], entry_ids=["5300000001"])) == 1


def test_fd_entries_upload_header():
    c = load_contest(FIX / "fd_entries_classic.csv")
    lu = solve(c, {k: (p.site_avg or 0) for k, p in c.players.items()})
    rows = upload_rows(c, [lu])
    assert rows[0][:4] == ["entry_id", "contest_id", "contest_name", "entry_fee"]
    assert rows[1][:2] == ["3670015043", "140001-279208572"] and all("-" in x for x in rows[1][4:])
