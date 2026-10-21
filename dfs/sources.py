"""Which data sources the engine may touch, enforced in code.

The user's rule is "only use sites that allow automated access". Data adapters declare a
source id and refuse to run unless its status is ALLOWED or CONDITIONAL. Agents search only
through the server-side web search tool; page fetches are limited to an allow-list by the
Agent SDK's permission rules (anything else is denied by the harness, not by the prompt).
Research notes behind every status: docs/research/sources_compliance.md.
"""

from __future__ import annotations

import os
import re
from urllib.parse import urlparse

REGISTER: dict[str, dict] = {
    "nflverse": {"status": "CONDITIONAL", "terms": "CC-BY-4.0 (FTN data CC-BY-SA-4.0); attribute nflverse; upstream NFL rights retained",
                 "url": "https://github.com/nflverse/nflverse-data"},
    "sportsdataverse": {"status": "CONDITIONAL", "terms": "pre-built release files, no scraping; ESPN/CFBD-derived, upstream rights retained",
                        "url": "https://github.com/sportsdataverse/sportsdataverse-data/releases"},
    "anthropic_web_search": {"status": "ALLOWED", "terms": "server-side search via Claude; cite sources",
                             "url": "https://platform.claude.com/docs"},
    "collegefootballdata": {"status": "CONDITIONAL", "terms": "free API key required", "url": "https://collegefootballdata.com"},
    "the_odds_api": {"status": "CONDITIONAL", "terms": "API key required; credit-metered", "url": "https://the-odds-api.com"},
    "open_meteo": {"status": "CONDITIONAL", "terms": "free for non-commercial use with attribution", "url": "https://open-meteo.com"},
    "nws": {"status": "ALLOWED", "terms": "US government API; send a User-Agent", "url": "https://api.weather.gov"},
    "sleeper": {"status": "UNCLEAR", "terms": "docs not read; believed free read-only API", "url": "https://docs.sleeper.com"},
    "espn_api": {"status": "UNCLEAR", "terms": "undocumented endpoints; Disney terms", "url": "https://disneytermsofuse.com"},
    "sports_reference": {"status": "PROHIBITED", "terms": "bot policy restricts automated collection", "url": "https://www.sports-reference.com/bot-traffic.html"},
    "fantasypros": {"status": "PROHIBITED", "terms": "no scraping; consensus numbers also break the forecaster's blind", "url": "https://www.fantasypros.com"},
    "rotowire": {"status": "PROHIBITED", "terms": "paywall", "url": "https://www.rotowire.com"},
    "draftkings": {"status": "PROHIBITED", "terms": "user downloads/uploads CSVs by hand; no automation of the site", "url": "https://www.draftkings.com"},
    "fanduel": {"status": "PROHIBITED", "terms": "user downloads/uploads CSVs by hand; no automation of the site", "url": "https://www.fanduel.com"},
}

# Facts sourced from these hosts are dropped (DFS sites, paywalled or no-automation sites).
BLOCKED_HOSTS = re.compile(r"(^|\.)(draftkings\.com|fanduel\.com|dkapi|rotowire\.com|fantasypros\.com|"
                           r"sports-reference\.com|pro-football-reference\.com|stathead\.com)$", re.I)


# Page fetches agents may make (everything else is denied by the SDK permission layer).
FETCH_ALLOW_DOMAINS = ["api.weather.gov", "www.weather.gov", "forecast.weather.gov", "open-meteo.com",
                       "api.open-meteo.com", "raw.githubusercontent.com"]
# Add domains you have confirmed allow automated access: DFS_FETCH_ALLOW="www.patriots.com,www.chicagobears.com"
FETCH_ALLOW_DOMAINS += [d.strip() for d in os.environ.get("DFS_FETCH_ALLOW", "").split(",")
                        if d.strip() and not BLOCKED_HOSTS.search(d.strip())]


class SourceNotAllowed(PermissionError):
    pass


def require(source_id: str) -> None:
    row = REGISTER.get(source_id)
    if row is None or row["status"] not in ("ALLOWED", "CONDITIONAL"):
        raise SourceNotAllowed(f"source {source_id!r} is {row['status'] if row else 'unregistered'}; "
                               "see dfs/sources.py before enabling it")


def host_blocked(url: str | None) -> bool:
    if not url:
        return False
    host = urlparse(url if "://" in url else f"https://{url}").hostname or ""
    return bool(BLOCKED_HOSTS.search(host))


# --------------------------------------------------------------------------- forecaster blind

_LEAKS = [
    re.compile(r"\$\s?\d"),  # prices / salaries
    re.compile(r"\bsalar(y|ies)\b", re.I),
    re.compile(r"\b(spread|over/under|o/u|moneyline|implied (team )?total|point total|vegas)\b", re.I),
    re.compile(r"(?<![\w.])[+-]\d{3}\b"),  # American odds
    re.compile(r"\b(favou?rites?|underdogs?|favou?red by|dogs? of|line (moved|movement)|odds)\b", re.I),
    re.compile(r"(?<![\w.])[+-]\d{1,2}(\.5)?(?![\w.%])"),  # spreads like -3.5 / +7
    re.compile(r"\b(fantasy points?|fpts|fppg|dk points|fd points|ppr points|projected .{0,20}points|ownership)\b", re.I),
]


def blind_text(text: str) -> str | None:
    """None when the text would leak prices, market lines or fantasy scores to the blinded forecaster."""
    return None if any(p.search(text or "") for p in _LEAKS) else text
