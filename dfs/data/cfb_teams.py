"""Map DraftKings / FanDuel college-football team codes onto ESPN team ids.

Site codes are not ESPN's (and differ between sites, e.g. ``OHST``/``OSU``, ``TXAM``/``TAMU``/``A&M``).
Every team has many names in the sportsdataverse files: the ESPN abbreviation, the CFBD
abbreviation in the schedule, ``alt_name1-3``, school, location, nickname, short and display
names. A code is scored against all of them (exact abbreviation > exact name > initials >
ordered-letter abbreviation of the school name > fuzzy), and each slate game ``AWAY@HOME`` is
then matched *jointly* to one scheduled game, so a code that fits several schools
(``MIA``: Miami / Miami (OH); ``MSU``: Michigan St / Mississippi St) is decided by its opponent.
Neutral-site games may list home/away either way. Nothing is guessed silently: every code gets
a ``TeamMatch`` with its method and score, and codes that cannot be placed are reported.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Mapping

import pandas as pd

# A handful of site codes that no generic rule can derive (checked against the 2026 teams file).
SITE_ALIASES: dict[str, str] = {
    "OLEMISS": "Ole Miss", "MISS": "Ole Miss", "UM": "Miami", "MIAFL": "Miami", "MIAOH": "Miami (OH)", "MOH": "Miami (OH)",
    "TAMU": "Texas A&M", "TXAM": "Texas A&M", "AM": "Texas A&M", "PITT": "Pittsburgh", "CUSE": "Syracuse",
    "UVA": "Virginia", "VT": "Virginia Tech", "GT": "Georgia Tech", "BC": "Boston College", "ND": "Notre Dame",
    "LSU": "LSU", "BAMA": "Alabama", "UGA": "Georgia", "UK": "Kentucky", "ULM": "UL Monroe", "ULL": "Louisiana",
    "LOU": "Louisville", "UL": "Louisiana", "USM": "Southern Miss", "FIU": "Florida International", "FAU": "Florida Atlantic",
    "WKU": "Western Kentucky", "EKU": "Eastern Kentucky", "UTSA": "UTSA", "UTEP": "UTEP", "UNLV": "UNLV",
    "SJSU": "San José State", "SDSU": "San Diego State", "USU": "Utah State", "BYU": "BYU", "TCU": "TCU", "SMU": "SMU",
    "UCF": "UCF", "USF": "South Florida", "UNC": "North Carolina", "NCST": "NC State", "NCSU": "NC State",
    "APP": "App State", "APST": "App State", "JMU": "James Madison", "ODU": "Old Dominion", "ECU": "East Carolina",
    "CCU": "Coastal Carolina", "GASO": "Georgia Southern", "GSU": "Georgia State", "UAB": "UAB", "UNT": "North Texas",
    "MTSU": "Middle Tennessee", "MTU": "Middle Tennessee", "NIU": "Northern Illinois", "WMU": "Western Michigan",
    "CMU": "Central Michigan", "EMU": "Eastern Michigan", "BGSU": "Bowling Green", "UMASS": "UMass", "UCONN": "UConn",
    "LT": "Louisiana Tech", "LATECH": "Louisiana Tech", "NMSU": "New Mexico State", "UNM": "New Mexico",
    "WSU": "Washington State", "WASH": "Washington", "UW": "Washington", "ORST": "Oregon State", "OSU": "Ohio State",
    "OHST": "Ohio State", "OKST": "Oklahoma State", "OKLA": "Oklahoma", "OU": "Oklahoma", "MSST": "Mississippi State",
    "MSU": "Michigan State", "MICHST": "Michigan State", "PSU": "Penn State", "ASU": "Arizona State", "UA": "Arizona",
}


def _ascii(s) -> str:
    return unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()


def norm_code(s) -> str:
    """Upper-case alphanumerics only: 'Texas A&M' -> 'TEXASAM', 'M-OH' -> 'MOH', 'San José St.' -> 'SANJOSEST'."""
    return re.sub(r"[^A-Z0-9]", "", _ascii(s).upper())


def _words(s) -> list[str]:
    return [w for w in re.split(r"[^A-Za-z0-9]+", _ascii(s)) if w]


_STATE_EXPAND = {"ST": "STATE", "MICH": "MICHIGAN", "MISS": "MISSISSIPPI", "CAROLINA": "CAROLINA"}


def _expand_state(s: str) -> str:
    return re.sub(r"\bSt\.?$", "State", str(s).strip())


@dataclass
class TeamMatch:
    code: str
    team_id: int | None
    school: str | None = None
    method: str = ""
    score: float = 0.0
    game_id: int | None = None
    note: str = ""


@dataclass
class TeamMatchReport:
    matches: dict[str, TeamMatch] = field(default_factory=dict)
    games: dict[str, int] = field(default_factory=dict)  # slate "AWAY@HOME" -> ESPN game_id
    unmatched: list[dict] = field(default_factory=list)

    def team_id(self, code: str) -> int | None:
        m = self.matches.get(str(code).upper())
        return m.team_id if m else None

    def summary(self) -> str:
        ok = [m for m in self.matches.values() if m.team_id is not None]
        lines = [f"teams matched {len(ok)}/{len(self.matches)}; games matched {len(self.games)}"]
        for m in sorted(self.matches.values(), key=lambda m: m.code):
            lines.append(f"  {m.code:>8} -> {m.school or '?'} ({m.team_id}) [{m.method} {m.score:.2f}]{' ' + m.note if m.note else ''}")
        lines += [f"  UNMATCHED {u}" for u in self.unmatched]
        return "\n".join(lines)


class TeamMatcher:
    """Scores site codes against every name a team has in the ESPN teams file and the schedule."""

    def __init__(self, teams: pd.DataFrame, schedules: pd.DataFrame | None = None):
        self.forms: dict[int, dict[str, set[str]]] = {}
        self.school: dict[int, str] = {}
        self.fbs: dict[int, bool] = {}
        t = teams.drop_duplicates("team_id")
        for r in t.itertuples(index=False):
            tid = int(r.team_id)
            abbr = {norm_code(x) for x in (getattr(r, "abbreviation", None),) if isinstance(x, str)}
            alts = {norm_code(x) for x in (getattr(r, "alt_name1", None), getattr(r, "alt_name2", None),
                                           getattr(r, "alt_name3", None)) if isinstance(x, str) and x}
            names = set()
            for col in ("school", "location", "nickname", "short_display_name", "display_name"):
                v = getattr(r, col, None)
                if isinstance(v, str) and v:
                    names.add(norm_code(v))
                    names.add(norm_code(_expand_state(v)))
            mascot = getattr(r, "mascot", None) or getattr(r, "name", None)
            disp = getattr(r, "display_name", None)
            if isinstance(disp, str) and isinstance(mascot, str) and disp.endswith(mascot):
                names.add(norm_code(disp[: -len(mascot)]))
            self.forms[tid] = {"abbr": abbr, "alt": alts, "name": names}
            self.school[tid] = str(getattr(r, "school", None) or getattr(r, "location", None) or tid)
            self.fbs[tid] = bool(getattr(r, "is_fbs", False)) if "is_fbs" in t.columns else False
        if schedules is not None and len(schedules):
            for side in ("home", "away"):
                ids = pd.to_numeric(schedules[f"{side}_id"], errors="coerce")
                for tid, ab, nm in zip(ids, schedules.get(f"{side}_abbreviation", pd.Series(index=schedules.index)),
                                       schedules[f"{side}_team"]):
                    if pd.isna(tid):
                        continue
                    f = self.forms.setdefault(int(tid), {"abbr": set(), "alt": set(), "name": set()})
                    if isinstance(ab, str) and ab:
                        f["abbr"].add(norm_code(ab))
                    if isinstance(nm, str) and nm:
                        f["name"].add(norm_code(nm))
                        self.school.setdefault(int(tid), nm)
        self.alias_name = {k: norm_code(v) for k, v in SITE_ALIASES.items()}

    def score(self, code: str, tid: int) -> tuple[float, str]:
        c = norm_code(code)
        f = self.forms.get(tid)
        if not c or f is None:
            return 0.0, ""
        if c in f["abbr"]:
            return 1.0, "abbreviation"
        if c in f["alt"]:
            return 0.97, "alt_name"
        if c in f["name"]:
            return 0.96, "name"
        alias = self.alias_name.get(c)
        if alias and alias in f["name"]:
            return 0.93, "site_alias"
        school = self.school.get(tid, "")
        words = _words(_expand_state(school))
        initials = "".join(w[0] for w in words).upper()
        if len(c) >= 2 and c == initials:
            return 0.9, "initials"
        sc = _ordered_abbrev_score(c, words)
        best_fuzzy = max((difflib.SequenceMatcher(None, c, x).ratio() for x in f["abbr"] | f["alt"] | f["name"]), default=0.0)
        if sc >= best_fuzzy * 0.8 and sc > 0:
            return sc, "letters"
        return round(best_fuzzy * 0.8, 4), "fuzzy"

    def best(self, code: str, candidates: list[int]) -> list[tuple[float, int, str]]:
        return sorted(((s, tid, m) for tid in candidates for s, m in [self.score(code, tid)]), reverse=True)


def _ordered_abbrev_score(code: str, words: list[str]) -> float:
    """How well ``code`` reads as an abbreviation of the words (TXST/Texas State, COST/Colorado State).

    Code letters are aligned in order to the name's letters; a letter is free when it starts a
    word or directly follows the previous matched letter, and costs a "jump" when it lands mid-word.
    The first letter must start the name. Score 0.88 for a clean abbreviation covering every word,
    minus 0.08 per jump and 0.1 per significant word left uncovered.
    """
    if not words or not code:
        return 0.0
    ws = [w.upper() for w in words]
    text = "".join(ws)
    if code[0] != text[0]:
        return 0.0
    starts, word_of = set(), []
    for k, w in enumerate(ws):
        starts.add(len(word_of))
        word_of += [k] * len(w)
    n, m = len(code), len(text)
    INF = 10 ** 6
    # dp[i][j]: min jumps aligning code[:i+1] with code[i] at text[j]
    dp = [[INF] * m for _ in range(n)]
    dp[0][0] = 0
    for i in range(1, n):
        for j in range(i, m):
            if text[j] != code[i]:
                continue
            best = INF
            for k in range(i - 1, j):
                if dp[i - 1][k] >= INF:
                    continue
                cost = 0 if (j in starts or j == k + 1) else 1
                best = min(best, dp[i - 1][k] + cost)
            dp[i][j] = best
    best_j = min(range(m), key=lambda j: dp[n - 1][j]) if n > 0 else 0
    jumps = dp[n - 1][best_j]
    if jumps >= INF:
        return 0.0
    # words covered: trace greedily which words contain matched letters (approximate: first & last)
    covered = {word_of[0], word_of[best_j]}
    minor = {"OF", "THE", "AT", "AND"}
    uncovered = sum(1 for k, w in enumerate(ws) if k not in covered and w not in minor and k not in
                    {word_of[j] for j in range(m) if text[j] in code and j in starts})
    return round(max(0.0, 0.88 - 0.08 * jumps - 0.1 * uncovered), 4)


def _utc(t) -> pd.Timestamp | None:
    if t is None or (not isinstance(t, pd.Timestamp) and pd.isna(t)):
        return None
    t = pd.Timestamp(t)
    if pd.isna(t):
        return None
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


_GAME_RX = re.compile(r"^\s*(?P<away>.+?)\s*@\s*(?P<home>.+?)(?:\s+\d{1,2}/\d{1,2}/\d{2,4}.*)?\s*$")


def split_slate_game(game: str) -> tuple[str, str]:
    """'ALA@UGA 10/24/2026 03:30PM ET' -> ('ALA', 'UGA'); names with spaces are kept whole."""
    m = _GAME_RX.match(str(game))
    if not m:
        raise ValueError(f"not an AWAY@HOME game string: {game!r}")
    return m.group("away").strip(), m.group("home").strip()


def match_slate(slate_games: list[str], teams: pd.DataFrame, schedules: pd.DataFrame, season: int | None = None,
                week: int | None = None, near: pd.Timestamp | None = None, min_score: float = 0.6,
                overrides: dict[str, int] | None = None,
                game_times: Mapping[str, pd.Timestamp] | None = None) -> TeamMatchReport:
    """Match each slate game ``AWAY@HOME`` (site codes) to one scheduled game and both team ids.

    Candidates: the scheduled games of ``week`` (else of the whole season). Each slate game is
    scored against every candidate as (weaker side, sum) of the two code scores, home/away
    reversal allowed (penalised unless the venue is neutral). Games are then assigned globally,
    most confident first, each scheduled game used at most once and each code kept to one team.
    ``overrides`` pins codes to team ids.

    Dates come first, names second. A slate game with a listed kickoff (``game_times``; DK files
    carry it) prefers scheduled games within 36 hours of it, falls back to games within 8 days
    (postponements) and never takes a game further away. With the week not given, games without a
    kickoff (FD lists) are held to the slate's consensus week, the most common week of a first
    matching pass, once one exists. So a code that reads more like another school cannot pull in
    that school's game from another week: such a slate game is reported unmatched instead.
    ``near`` breaks remaining ties by date.
    """
    s = schedules
    if season is not None and "season" in s.columns:
        s = s[s["season"] == season]
    if week is not None:
        s = s[s["week"] == week]
    s = s.dropna(subset=["home_id", "away_id"]).copy()
    s["home_id"] = s["home_id"].astype("int64")
    s["away_id"] = s["away_id"].astype("int64")
    s["kick_utc"] = pd.to_datetime(s["start_date"], utc=True, errors="coerce") if "start_date" in s.columns else pd.NaT
    tm = TeamMatcher(teams, schedules)
    near = _utc(near)
    kicks = {str(g): _utc(t) for g, t in (game_times or {}).items() if _utc(t) is not None}
    cand_ids = sorted(set(s["home_id"]) | set(s["away_id"]))
    overrides = {str(k).upper(): int(v) for k, v in (overrides or {}).items()}
    cache: dict[str, dict[int, tuple[float, str]]] = {}

    def sc(code: str) -> dict[int, tuple[float, str]]:
        if code not in cache:
            if code.upper() in overrides:
                cache[code] = {tid: ((1.0, "override") if tid == overrides[code.upper()] else (0.0, "")) for tid in cand_ids}
            else:
                cache[code] = {tid: (sc_ + (0.02 if tm.fbs.get(tid) and sc_ < 1.0 else 0.0), m_)
                               for tid in cand_ids for sc_, m_ in [tm.score(code, tid)]}
        return cache[code]

    def dist(r, g: str) -> pd.Timedelta:
        ref = kicks.get(g, near)
        if ref is None or pd.isna(r.kick_utc):
            return pd.Timedelta(0)
        return abs(r.kick_utc - ref)

    def date_fit(r, g: str, consensus: int | None) -> int:
        """2 = on the slate's date / week, 1 = allowed fallback, 0 = excluded."""
        if g in kicks and pd.notna(r.kick_utc):
            d = abs(r.kick_utc - kicks[g])
            return 2 if d <= pd.Timedelta(hours=36) else (1 if d <= pd.Timedelta(days=8) else 0)
        if consensus is None or pd.isna(r.week):
            return 2
        return 2 if int(r.week) == consensus else 0

    parsed: dict[str, tuple[str, str]] = {}
    bad_games: list[dict] = []
    for g in slate_games:
        try:
            parsed[g] = split_slate_game(g)
        except ValueError as e:
            bad_games.append({"game": g, "reason": str(e)})

    def assign(consensus: int | None) -> TeamMatchReport:
        rep = TeamMatchReport(unmatched=list(bad_games))
        options: dict[str, list[tuple]] = {}
        for g, (away, home) in parsed.items():
            a, h = sc(away), sc(home)
            opts = []
            for r in s.itertuples(index=False):
                neutral = bool(getattr(r, "neutral_site", False))
                for swapped in (False, True):
                    ta, th = (r.home_id, r.away_id) if swapped else (r.away_id, r.home_id)
                    (sa, ma), (sh, mh) = a.get(ta, (0.0, "")), h.get(th, (0.0, ""))
                    pen = 0.0 if (not swapped or neutral) else 0.05
                    lo = min(sa, sh) - pen
                    fit = date_fit(r, g, consensus) if lo >= min_score else 0
                    if fit:
                        # +1 on the slate's date: ranks every such fit above any fallback
                        lo_rank = lo + (1.0 if fit == 2 else 0.0)
                        opts.append((lo_rank, sa + sh - pen, -dist(r, g).total_seconds(), int(r.game_id), int(ta), int(th),
                                     swapped, (sa, ma), (sh, mh)))
            opts.sort(reverse=True)
            options[g] = opts

        used_games: set[int] = set()
        code_team: dict[str, int] = {}
        pending = [g for g in parsed]
        while pending:
            def viable(g):
                away, home = parsed[g]
                return [o for o in options[g] if o[3] not in used_games
                        and code_team.get(away.upper(), o[4]) == o[4] and code_team.get(home.upper(), o[5]) == o[5]
                        and o[4] not in {t for c, t in code_team.items() if c != away.upper()}
                        and o[5] not in {t for c, t in code_team.items() if c != home.upper()}]

            ranked = []
            for g in pending:
                v = viable(g)
                if not v:
                    ranked.append((float("-inf"), g, v))
                    continue
                margin = (v[0][0] - v[1][0]) if len(v) > 1 else 1.0
                if len(v) > 1 and abs(v[0][0] - v[1][0]) < 1e-9:
                    margin = (v[0][1] - v[1][1]) / 2 if abs(v[0][1] - v[1][1]) > 1e-9 else (1e-6 if near is not None else 0.0)
                ranked.append((v[0][0] + margin, g, v))
            ranked.sort(key=lambda x: x[0], reverse=True)
            score, g, v = ranked[0]
            pending.remove(g)
            away, home = parsed[g]
            if not v:
                rep.unmatched.append({"game": g, "reason": f"no scheduled game on the slate's dates fits both codes at >= {min_score}"})
                for code in (away, home):
                    d = sc(code)
                    best = max(d.items(), key=lambda kv: kv[1][0]) if d else None
                    rep.matches.setdefault(code.upper(), TeamMatch(code.upper(), None, note=(
                        f"best guess {tm.school.get(best[0])} {best[1][0]:.2f}" if best else "")))
                continue
            top = v[0]
            if len(v) > 1 and abs(v[0][0] - v[1][0]) < 1e-9 and abs(v[0][1] - v[1][1]) < 1e-9 and near is None:
                rep.unmatched.append({"game": g, "reason": f"{len(v)} scheduled games fit equally; pass week= or a date"})
                continue
            _, _, _, gid, a_id, h_id, swapped, (sa, ma), (sh, mh) = top
            used_games.add(gid)
            rep.games[g] = gid
            note = "site lists home/away reversed" if swapped else ""
            for code, tid, sco, meth in ((away, a_id, sa, ma), (home, h_id, sh, mh)):
                code_team[code.upper()] = tid
                rep.matches[code.upper()] = TeamMatch(code.upper(), tid, tm.school.get(tid), meth, round(min(sco, 1.0), 4),
                                                      gid, note)
        return rep

    rep = assign(None)
    untimed = [g for g in rep.games if g not in kicks]
    if week is None and untimed and len(rep.games) >= 2:
        wk_of = s.set_index("game_id")["week"]
        weeks = pd.Series([wk_of.get(gid) for gid in rep.games.values()]).dropna().astype(int)
        counts = weeks.value_counts()
        tops = sorted(counts[counts == counts.max()].index)
        if len(tops) == 1 and (weeks != tops[0]).any():  # a clear consensus that some games disagree with
            rep = assign(int(tops[0]))
    return rep
