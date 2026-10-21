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

# Facts sourced from these hosts are dropped: DFS and pick'em operators, paywalled projection sites,
# odds and betting sites, and sites that forbid automated access.
BLOCKED_HOSTS = re.compile(r"(^|\.)(draftkings\.com|fanduel\.com|draftkings\.\w+|fanduel\.\w+|rotowire\.com|fantasypros\.com|"
                           r"sports-reference\.com|pro-football-reference\.com|stathead\.com|prizepicks\.com|"
                           r"underdogfantasy\.com|numberfire\.com|rotogrinders\.com|4for4\.com|actionnetwork\.com|"
                           r"covers\.com|vegasinsider\.com|oddsshark\.com|sportsline\.com|betmgm\.com|caesars\.com|"
                           r"williamhill\.com|pointsbet\.com|bet365\.com|betrivers\.com|fanatics\.com|espnbet\.com|"
                           r"sleeper\.com|dailyfantasyfuel\.com|fantasylabs\.com|establishtherun\.com|stokastic\.com)$", re.I)
BLOCKED_PATHS = re.compile(r"/(betting|odds|picks|props|dfs|daily-fantasy|fantasy-projections)(/|$|-)", re.I)


# Page fetches agents may make (everything else is denied by the SDK permission layer).
FETCH_ALLOW_DOMAINS = ["api.weather.gov", "www.weather.gov", "forecast.weather.gov", "open-meteo.com",
                       "api.open-meteo.com", "raw.githubusercontent.com"]
# Add domains you have confirmed allow automated access: DFS_FETCH_ALLOW="www.patriots.com,www.chicagobears.com"
FETCH_ALLOW_DOMAINS += [d.strip().rstrip(".").lower() for d in os.environ.get("DFS_FETCH_ALLOW", "").split(",")
                        if d.strip() and not BLOCKED_HOSTS.search(d.strip().rstrip(".").lower())]


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
    u = urlparse(url if "://" in url else f"https://{url}")
    host = (u.hostname or "").rstrip(".").lower()  # urlparse already drops the port
    return bool(BLOCKED_HOSTS.search(host) or BLOCKED_PATHS.search(u.path or ""))


# --------------------------------------------------------------------------- forecaster blind

_SIGN = r"[+\-−–]"  # plus, hyphen-minus, minus sign, en dash
_LEAKS = [
    # prices and salaries
    re.compile(r"\$\s?\d"),
    re.compile(r"\b(salar(y|ies)|priced at|price tag|costs? \d|\d{1,2}(\.\d)?k\b)", re.I),
    re.compile(r"\b(draftkings|fanduel|dk|fd|prizepicks|underdog)\b", re.I),
    re.compile(r"(avg ?points ?per ?game|site average|points per game average)", re.I),
    # betting markets
    re.compile(r"\b(spread|over[-/ ]?under|o/u|moneyline|money line|implied (team )?total|point total|team total|"
               r"game total|vegas|sportsbooks?|oddsmakers?|bookmakers?|books? (have|has|make)|props?\b|"
               r"(laying|giving|getting) \d|by \d{1,2}(\.5)? points|totals? (of|is|at|sits at|:)\s*\d{2})", re.I),
    re.compile(rf"(?<![\w.]){_SIGN}\d{{3}}\b"),  # American odds
    re.compile(r"\b(favou?rites?|underdogs?|favou?red by|dogs? of|line (moved|movement)|odds)\b", re.I),
    re.compile(rf"(?<![\w.]){_SIGN}\d{{1,2}}(\.5)?(?![\w.%])"),  # spreads like -3.5 / +7 / -2.5 with a minus sign
    re.compile(r"\b(over|under) \d{1,3}\.5\b", re.I),  # "over 7.5 receptions", "under 42.5"
    # fantasy points, projections, ownership and DFS advice
    re.compile(r"\b(fantasy points?|fpts|fppg|ppg|dk points|fd points|ppr|half-ppr|fp|pts|projected .{0,20}points|"
               r"projections?|expected to score|ownership|rostership|\d+(\.\d)?% owned|owned at|chalk|value play|smash|must[- ]start|"
               r"start/sit|start or sit|sleepers?|dfs|gpp|cash games?)\b", re.I),
    re.compile(r"\b(low|mid|high)-end (qb|rb|wr|te)\d\b|\b(qb|rb|wr|te)\d{2}\b|\btop-\d{1,2} (option|play|finish)", re.I),
]


def blind_text(text: str) -> str | None:
    """None when the text would leak prices, market lines or fantasy scores to the blinded forecaster."""
    return None if any(p.search(text or "") for p in _LEAKS) else text
