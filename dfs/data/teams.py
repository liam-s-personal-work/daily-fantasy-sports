"""NFL team abbreviations across DraftKings, FanDuel and nflverse.

nflverse codes are canonical (``LA`` Rams, ``LV`` Raiders, ``JAX``, ``WAS``). Site files and
older sources use variants (``LAR``, ``JAC``, ``WSH``, ``OAK``, ``SD``, ``STL`` ...);
``canonicalize`` maps any of them, plus city / nickname / "City Nickname" strings used for DST
rows, onto the nflverse code.
"""

from __future__ import annotations

import re

# nflverse code -> (city / region, nickname)
TEAMS: dict[str, tuple[str, str]] = {
    "ARI": ("Arizona", "Cardinals"), "ATL": ("Atlanta", "Falcons"), "BAL": ("Baltimore", "Ravens"),
    "BUF": ("Buffalo", "Bills"), "CAR": ("Carolina", "Panthers"), "CHI": ("Chicago", "Bears"),
    "CIN": ("Cincinnati", "Bengals"), "CLE": ("Cleveland", "Browns"), "DAL": ("Dallas", "Cowboys"),
    "DEN": ("Denver", "Broncos"), "DET": ("Detroit", "Lions"), "GB": ("Green Bay", "Packers"),
    "HOU": ("Houston", "Texans"), "IND": ("Indianapolis", "Colts"), "JAX": ("Jacksonville", "Jaguars"),
    "KC": ("Kansas City", "Chiefs"), "LA": ("Los Angeles", "Rams"), "LAC": ("Los Angeles", "Chargers"),
    "LV": ("Las Vegas", "Raiders"), "MIA": ("Miami", "Dolphins"), "MIN": ("Minnesota", "Vikings"),
    "NE": ("New England", "Patriots"), "NO": ("New Orleans", "Saints"), "NYG": ("New York", "Giants"),
    "NYJ": ("New York", "Jets"), "PHI": ("Philadelphia", "Eagles"), "PIT": ("Pittsburgh", "Steelers"),
    "SEA": ("Seattle", "Seahawks"), "SF": ("San Francisco", "49ers"), "TB": ("Tampa Bay", "Buccaneers"),
    "TEN": ("Tennessee", "Titans"), "WAS": ("Washington", "Commanders"),
}

# Alternate abbreviations seen on DK/FD, PFR, ESPN, sportsbooks and older nflverse seasons.
ALIASES: dict[str, str] = {
    "LAR": "LA", "STL": "LA", "SL": "LA", "RAM": "LA",
    "JAC": "JAX", "JAG": "JAX",
    "WSH": "WAS", "WFT": "WAS",
    "LVR": "LV", "OAK": "LV",
    "SD": "LAC", "SDG": "LAC",
    "GNB": "GB", "GBP": "GB",
    "KAN": "KC", "KCC": "KC",
    "NWE": "NE", "NEP": "NE",
    "NOR": "NO", "NOS": "NO",
    "SFO": "SF", "SFF": "SF",
    "TAM": "TB", "TBB": "TB",
    "ARZ": "ARI", "CRD": "ARI",
    "BLT": "BAL", "RAV": "BAL",
    "CLV": "CLE",
    "HST": "HOU", "HTX": "HOU",
    "CLT": "IND",
    "OTI": "TEN",
    "NYJETS": "NYJ", "NYGIANTS": "NYG",
}

# Codes the sites print, when they differ from nflverse (seen in 2026 DK and FD NFL exports: LAR).
SITE_CODES: dict[str, dict[str, str]] = {
    "draftkings": {"LA": "LAR"},
    "fanduel": {"LA": "LAR"},
}

_EXTRA_NAMES: dict[str, str] = {
    "redskins": "WAS", "football team": "WAS", "washington football team": "WAS",
    "niners": "SF", "bucs": "TB", "pats": "NE",
}


def _name_key(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", re.sub(r"\s+", " ", s.strip().lower()))


def _build_name_index() -> dict[str, str]:
    idx: dict[str, str] = {}
    city_counts: dict[str, int] = {}
    for city, _ in TEAMS.values():
        city_counts[city.lower()] = city_counts.get(city.lower(), 0) + 1
    for code, (city, nick) in TEAMS.items():
        idx[_name_key(nick)] = code
        idx[_name_key(f"{city} {nick}")] = code
        idx[_name_key(f"{nick} dst")] = code
        idx[_name_key(f"{nick} d st")] = code
        if city_counts[city.lower()] == 1:  # "New York" / "Los Angeles" are ambiguous
            idx[_name_key(city)] = code
    for k, v in _EXTRA_NAMES.items():
        idx[_name_key(k)] = v
    return idx


_NAME_INDEX = _build_name_index()


def canonicalize(team: str, strict: bool = True) -> str:
    """Map an abbreviation (any source) or a team name onto the nflverse code.

    Raises ``KeyError`` for an unknown team when ``strict``; otherwise returns the upper-cased input.
    """
    if team is None:
        raise KeyError("team is None")
    raw = str(team).strip()
    up = raw.upper()
    if up in TEAMS:
        return up
    if up in ALIASES:
        return ALIASES[up]
    key = _name_key(raw)
    if key in _NAME_INDEX:
        return _NAME_INDEX[key]
    key2 = re.sub(r"\b(dst|d st|defense|def|d)\b", "", key).strip()
    if key2 in _NAME_INDEX:
        return _NAME_INDEX[key2]
    if strict:
        raise KeyError(f"unknown NFL team {team!r}")
    return up


def is_team(team: str) -> bool:
    try:
        canonicalize(team)
        return True
    except KeyError:
        return False


def site_code(team: str, site: str) -> str:
    """The abbreviation a site prints for a team (``LA`` -> ``LAR`` on DK/FD)."""
    code = canonicalize(team)
    return SITE_CODES.get(str(site).lower(), {}).get(code, code)


def team_name(team: str) -> str:
    city, nick = TEAMS[canonicalize(team)]
    return f"{city} {nick}"


def split_game(game: str) -> tuple[str, str]:
    """``"AWAY@HOME"`` (any codes, optional trailing date text) -> canonical (away, home)."""
    head = str(game).strip().split()[0]
    away, _, home = head.partition("@")
    if not home:
        raise ValueError(f"not an AWAY@HOME game string: {game!r}")
    return canonicalize(away), canonicalize(home)
