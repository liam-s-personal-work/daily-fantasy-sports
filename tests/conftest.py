"""Shared fixtures: small synthetic slates for solver/simulation tests (not real players)."""

from __future__ import annotations

import random

import pytest

from dfs.models import Candidate, Contest, ContestRules, Player, Projection, Slot

DK_NFL_SLOTS = [("QB", ["QB"]), ("RB", ["RB"]), ("RB", ["RB"]), ("WR", ["WR"]), ("WR", ["WR"]),
                ("WR", ["WR"]), ("TE", ["TE"]), ("FLEX", ["RB", "WR", "TE"]), ("DST", ["DST"])]


def make_toy_contest(seed: int = 1, n_games: int = 6) -> tuple[Contest, dict[str, Projection]]:
    """A DK-NFL-classic-shaped slate with made-up players, salaries and projections."""
    rules = ContestRules(site="draftkings", sport="nfl", game_type="classic", salary_cap=50000,
                         scoring="draftkings.nfl", min_games=2,
                         slots=[Slot(name=n, eligible=e) for n, e in DK_NFL_SLOTS])
    rng = random.Random(seed)
    teams = [f"T{i:02d}" for i in range(2 * n_games)]
    players: dict[str, Player] = {}
    cands: list[Candidate] = []
    projs: dict[str, Projection] = {}
    pid = 10000
    for i in range(0, 2 * n_games, 2):
        away, home = teams[i], teams[i + 1]
        for team, opp in ((away, home), (home, away)):
            for pos, n in (("QB", 2), ("RB", 3), ("WR", 5), ("TE", 2), ("DST", 1)):
                for k in range(n):
                    key = f"{team}:{pos}{k}"
                    pid += 1
                    players[key] = Player(key=key, name=f"{team} {pos}{k}", team=team, opponent=opp,
                                          positions=[pos], game=f"{away}@{home}")
                    lo, hi = (2000, 4500) if pos == "DST" else (3000, 9500)
                    sal = rng.randrange(lo, hi, 100)
                    elig = [pos] + (["FLEX"] if pos in ("RB", "WR", "TE") else [])
                    cands.append(Candidate(player_key=key, site_id=str(pid), salary=sal, eligible_slots=elig))
                    mean = max(0.5, sal / 1000 * 2.1 + rng.gauss(0, 3)) * (0.35 if k >= 2 and pos != "WR" else 1)
                    sd = mean * 0.6 + 1
                    projs[key] = Projection(player_key=key, mean=mean, p10=max(mean - 1.0 * sd, -2),
                                            p50=mean - 0.2 * sd, p90=mean + 1.5 * sd)
    contest = Contest(rules=rules, players=players, candidates=cands, slot_headers=[s.name for s in rules.slots],
                      source_file="synthetic", file_kind="dk_salaries",
                      games=[f"{teams[i]}@{teams[i + 1]}" for i in range(0, 2 * n_games, 2)])
    return contest, projs


@pytest.fixture
def toy_contest():
    return make_toy_contest()
