"""Offline tests for contest-player -> nflverse id matching."""

from __future__ import annotations

import pandas as pd
import pytest

from dfs.models import Player
from dfs.projections.matching import compact, match_players, normalize_name


@pytest.mark.parametrize("raw,norm", [
    ("A.J. Brown", "aj brown"), ("Marvin Harrison Jr.", "marvin harrison"), ("Kenneth Walker III", "kenneth walker"),
    ("Ja'Marr Chase", "jamarr chase"), ("Amon-Ra St. Brown", "amon ra st brown"), ("D'Andre Swift", "dandre swift"),
    ("Michael Penix Jr", "michael penix"), ("JOSH ALLEN", "josh allen"), ("Tre’ Harris", "tre harris"),
    ("Odell Beckham Sr.", "odell beckham"), ("Patrick Mahomes II", "patrick mahomes"), ("José Pérez IV", "jose perez"),
])
def test_normalize_name(raw, norm):
    assert normalize_name(raw) == norm


def test_compact_ignores_spacing_and_dots():
    assert compact("D.J. Moore") == compact("DJ Moore") == "djmoore"
    assert compact("Amon-Ra St. Brown") == compact("AmonRa St Brown")


CANDS = pd.DataFrame([
    ("00-1", "A.J. Brown", "PHI", "WR", []), ("00-2", "Marvin Harrison", "ARI", "WR", []),
    ("00-3", "Kenneth Walker", "KC", "RB", []), ("00-4", "Puka Nacua", "LA", "WR", []),
    ("00-5", "Gabriel Davis", "BUF", "WR", []), ("00-6", "Marquise Brown", "KC", "WR", ["Hollywood Brown"]),
    ("00-7", "Josh Allen", "BUF", "QB", []), ("00-8", "Christian McCaffrey", "SF", "RB", []),
    ("00-9", "Taysom Hill", "NO", "TE", []), ("00-10", "Davante Adams", "LA", "WR", []),
    ("00-11", "Mike Williams", "PIT", "WR", []), ("00-12", "Michael Williams", "PIT", "WR", []),
    ("00-13", "Stefon Diggs", "NE", "WR", []), ("DST:LA", "LA DST", "LA", "DST", []),
    ("DST:BAL", "BAL DST", "BAL", "DST", []),
], columns=["data_id", "name", "team", "position", "alt_names"])


def P(key, name, team, pos):
    return Player(key=key, name=name, team=team, positions=[pos])


def test_match_methods():
    players = [
        P("k1", "A.J. Brown", "PHI", "WR"),              # exact
        P("k2", "Marvin Harrison Jr.", "ARI", "WR"),     # suffix
        P("k3", "Kenneth Walker III", "KC", "RB"),       # suffix
        P("k4", "Puka Nacua", "LAR", "WR"),              # site team alias
        P("k5", "Gabe Davis", "BUF", "WR"),              # nickname
        P("k6", "Hollywood Brown", "KC", "WR"),          # alt name from roster
        P("k7", "Christian McCaffery", "SF", "RB"),      # misspelling -> fuzzy
        P("k8", "Taysom Hill", "NO", "QB"),              # listed at another position
        P("k9", "Stefon Diggs", "HOU", "WR"),            # stale team -> exact name elsewhere, unique
        P("k10", "Rams", "LAR", "DST"),                  # DK DST
        P("k11", "Baltimore Ravens", "BAL", "D"),        # FD defense
        P("k12", "Nobody Here", "BUF", "WR"),            # unmatched, reported
    ]
    rep = match_players(players, CANDS)
    got = {k: (m.data_id, m.method) for k, m in rep.matched.items()}
    assert got["k1"] == ("00-1", "exact")
    assert got["k2"][0] == "00-2" and got["k3"][0] == "00-3" and got["k4"][0] == "00-4"
    assert got["k5"] == ("00-5", "nickname")
    assert got["k6"][0] == "00-6"
    assert got["k7"] == ("00-8", "fuzzy")
    assert got["k8"] == ("00-9", "exact_any_pos")
    assert got["k9"] == ("00-13", "exact_other_team")
    assert got["k10"] == ("DST:LA", "dst") and got["k11"] == ("DST:BAL", "dst")
    assert [u["player_key"] for u in rep.unmatched] == ["k12"]
    assert rep.rate == pytest.approx(11 / 12)
    assert "UNMATCHED Nobody Here" in rep.summary()


def test_ambiguity_is_reported_not_guessed():
    # "M. Williams" fits two PIT receivers equally well
    rep = match_players([P("x", "M. Williams", "PIT", "WR")], CANDS)
    assert not rep.matched and rep.unmatched and rep.unmatched[0]["player_key"] == "x"


def test_one_id_one_player():
    rep = match_players([P("a", "Josh Allen", "BUF", "QB"), P("b", "Josh Allen", "BUF", "QB")], CANDS)
    assert len(rep.matched) == 1 and len(rep.unmatched) == 1
    assert "already matched" in rep.unmatched[0]["reason"]


def test_apply_sets_data_id():
    players = {"k1": P("k1", "A.J. Brown", "PHI", "WR"), "zz": P("zz", "Nope", "PHI", "WR")}
    rep = match_players(players, CANDS)
    out = rep.apply(players)
    assert out["k1"].data_id == "00-1" and out["zz"].data_id is None


def test_dst_without_candidate_row_still_matches_by_team():
    rep = match_players([P("d", "Chiefs", "KC", "DST")], CANDS)
    assert rep.matched["d"].data_id == "DST:KC"
