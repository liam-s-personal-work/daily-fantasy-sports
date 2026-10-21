"""Saved contest rules (the registry) and sport vocabularies used for file detection."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from dfs.models import ContestRules, Site, Slot

REGISTRY = Path(__file__).parent / "registry" / "contests.yaml"

# Every abbreviation DK, FD or nflverse uses for the 32 NFL teams.
NFL_TEAMS = {
    "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN", "DET", "GB", "HOU", "IND",
    "JAX", "JAC", "KC", "LV", "LAC", "LAR", "LA", "MIA", "MIN", "NE", "NO", "NYG", "NYJ", "PHI", "PIT",
    "SF", "SEA", "TB", "TEN", "WAS", "WSH",
}

POSITION_SPORT = [  # first match wins; checked against the set of natural positions in a file
    ({"PG", "SG", "SF", "PF"}, "nba"),
    ({"LW", "RW"}, "nhl"),
    ({"1B", "2B", "3B", "SS", "OF", "SP", "RP"}, "mlb"),
    ({"GK", "M"}, "soccer"),
]


class RulesError(KeyError):
    pass


@lru_cache(maxsize=None)
def _registry() -> dict:
    return yaml.safe_load(REGISTRY.read_text())


def registry_keys() -> list[str]:
    return sorted(_registry()["contests"])


def load_rules(site: Site | str, sport: str, game_type: str) -> ContestRules:
    site = Site(site)
    key = f"{site.value}.{sport}.{game_type}"
    entry = _registry()["contests"].get(key)
    if entry is None:
        raise RulesError(f"no saved rules for {key}; add it to {REGISTRY} (known: {', '.join(registry_keys())})")
    slots = [Slot(name=s[0], eligible=list(s[1]),
                  points_multiplier=float(s[2]) if len(s) > 2 else 1.0,
                  salary_multiplier=float(s[3]) if len(s) > 3 else 1.0) for s in entry["slots"]]
    return ContestRules(
        site=site, sport=sport, game_type=game_type, salary_cap=int(entry["salary_cap"]), slots=slots,
        max_per_team=entry.get("max_per_team"), max_hitters_per_team=entry.get("max_hitters_per_team"), min_teams=entry.get("min_teams"), min_games=entry.get("min_games"),
        multiplier_rows=bool(entry.get("multiplier_rows", False)), scoring=entry["scoring"],
        late_swap=bool(entry.get("late_swap", False)), salary_column_by_slot=entry.get("salary_column_by_slot") or {},
        confidence=entry.get("confidence", "medium"), notes=list(entry.get("notes") or []),
        source_urls=list(entry.get("sources") or []), verified_on=str(_registry().get("verified_on")),
    )
