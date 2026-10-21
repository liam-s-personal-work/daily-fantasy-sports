"""Match contest players (name, team, position) to stats-source ids (nflverse gsis ids).

Names are normalized (accents, case, punctuation, Jr/Sr/II-V suffixes). Each player is tried
in order: exact name on the same team, exact name on any team (trades, stale team codes),
nickname / first-initial / unique-last-name on the team, then a difflib fuzzy match within
the team. Defenses match by team (``DST:<team>``). Anything left is reported in
``MatchReport.unmatched``; nothing is silently dropped.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Mapping

import pandas as pd

from dfs.data.teams import canonicalize
from dfs.models import Player

SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
DST_POSITIONS = {"DST", "D", "DEF", "D/ST"}
POSITION_ALIASES = {"FB": "RB", "HB": "RB", "PK": "K", "D": "DST", "DEF": "DST", "D/ST": "DST"}
# common short / formal first names (both directions are tried)
NICKNAMES = {"gabe": "gabriel", "mike": "michael", "matt": "matthew", "chris": "christopher", "josh": "joshua",
             "dave": "david", "nick": "nicholas", "tony": "anthony", "zach": "zachary", "jeff": "jeffery",
             "ben": "benjamin", "will": "william", "rob": "robert", "bob": "robert", "jon": "jonathan",
             "joe": "joseph", "tom": "thomas", "dan": "daniel", "sam": "samuel", "andy": "andrew",
             "drew": "andrew", "kenny": "kenneth", "nate": "nathan", "alex": "alexander", "pat": "patrick",
             "cam": "cameron", "jake": "jacob", "tim": "timothy", "greg": "gregory", "ed": "edward",
             "hollywood": "marquise", "chig": "chigoziem", "scotty": "scott", "bam": "johnathan"}


def normalize_name(name: str) -> str:
    """'A.J. Brown Jr.' -> 'aj brown'; "Ja'Marr Chase" -> 'jamarr chase'; 'Amon-Ra St. Brown' -> 'amon ra st brown'."""
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[.'`’]", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    tokens = [t for t in s.split() if t not in SUFFIXES]
    return " ".join(tokens)


def compact(name: str) -> str:
    return normalize_name(name).replace(" ", "")


def canonical_position(pos: str) -> str:
    p = str(pos).upper().strip()
    return POSITION_ALIASES.get(p, p)


@dataclass
class Match:
    player_key: str
    data_id: str
    method: str  # dst | exact | exact_any_pos | exact_other_team | nickname | initial | last_name | fuzzy
    score: float
    candidate_name: str
    candidate_team: str


@dataclass
class MatchReport:
    matched: dict[str, Match] = field(default_factory=dict)
    unmatched: list[dict] = field(default_factory=list)

    @property
    def rate(self) -> float:
        n = len(self.matched) + len(self.unmatched)
        return len(self.matched) / n if n else 1.0

    def by_method(self) -> dict[str, int]:
        out: dict[str, int] = defaultdict(int)
        for m in self.matched.values():
            out[m.method] += 1
        return dict(out)

    def apply(self, players: Mapping[str, Player]) -> dict[str, Player]:
        """Copies of the players with ``data_id`` set where matched."""
        return {k: (p.model_copy(update={"data_id": self.matched[k].data_id}) if k in self.matched else p)
                for k, p in players.items()}

    def summary(self) -> str:
        lines = [f"matched {len(self.matched)}/{len(self.matched) + len(self.unmatched)} "
                 f"({self.rate:.1%}) {self.by_method()}"]
        lines += [f"  UNMATCHED {u['name']} ({u['team']} {'/'.join(u['positions'])}): {u['reason']}" for u in self.unmatched]
        return "\n".join(lines)


def _first_variants(first: str) -> set[str]:
    out = {first}
    if first in NICKNAMES:
        out.add(NICKNAMES[first])
    out |= {k for k, v in NICKNAMES.items() if v == first}
    return out


class _Index:
    def __init__(self, cands: pd.DataFrame):
        self.rows = cands.reset_index(drop=True)
        self.by_key: dict[str, list[int]] = defaultdict(list)
        self.by_team: dict[str, list[int]] = defaultdict(list)
        self.dst: dict[str, int] = {}
        for i, r in self.rows.iterrows():
            if r["position"] == "DST":
                self.dst[r["team"]] = i
                continue
            self.by_team[r["team"]].append(i)
            for nm in [r["name"], *r["alt_names"]]:
                self.by_key[compact(nm)].append(i)

    def pos_ok(self, i: int, positions: set[str]) -> bool:
        return not positions or self.rows.at[i, "position"] in positions


def _prepare(candidates: pd.DataFrame) -> pd.DataFrame:
    c = candidates.copy()
    if "data_id" not in c.columns:
        c = c.rename(columns={"gsis_id": "data_id"})
    c["team"] = c["team"].map(lambda t: canonicalize(t, strict=False))
    c["position"] = c["position"].map(canonical_position)
    if "alt_names" not in c.columns:
        c["alt_names"] = [[] for _ in range(len(c))]
    c["alt_names"] = c["alt_names"].map(lambda v: [x for x in (v if isinstance(v, (list, tuple)) else [v])
                                                   if isinstance(x, str) and x])
    return c.dropna(subset=["data_id", "name"]).drop_duplicates("data_id")


def match_players(players: Iterable[Player] | Mapping[str, Player], candidates: pd.DataFrame,
                  fuzzy_cutoff: float = 0.82) -> MatchReport:
    """Match contest players to candidate rows (data_id|gsis_id, name, team, position[, alt_names]).

    Candidates should include one ``position == 'DST'`` row per team (data_id ``DST:<team>``);
    if absent, defenses still match to ``DST:<team>`` by team.
    """
    plist = list(players.values()) if isinstance(players, Mapping) else list(players)
    by_key = {p.key: p for p in plist}
    idx = _Index(_prepare(candidates))
    report = MatchReport()
    claimed: dict[str, str] = {}
    rank = {"dst": 0, "exact": 1, "exact_any_pos": 2, "nickname": 3, "initial": 4, "last_name": 5,
            "exact_other_team": 6, "fuzzy": 7}

    for p in plist:
        positions = {canonical_position(x) for x in p.positions}
        try:
            team = canonicalize(p.team)
        except KeyError:
            team = canonicalize(p.name, strict=False) if positions & {"DST"} else str(p.team).upper()
        m = _match_one(p, team, positions, idx, fuzzy_cutoff)
        if isinstance(m, str):
            report.unmatched.append({"player_key": p.key, "name": p.name, "team": p.team, "positions": p.positions,
                                     "reason": m})
            continue
        prev = claimed.get(m.data_id)
        if prev is not None:
            other = report.matched[prev]
            if rank[m.method] < rank[other.method]:
                del report.matched[prev]
                q = by_key[prev]
                report.unmatched.append({"player_key": prev, "name": q.name, "team": q.team, "positions": q.positions,
                                         "reason": f"lost id {m.data_id} to {p.key} ({m.method})"})
            else:
                report.unmatched.append({"player_key": p.key, "name": p.name, "team": p.team,
                                         "positions": p.positions,
                                         "reason": f"id {m.data_id} already matched to {prev} ({other.method})"})
                continue
        claimed[m.data_id] = p.key
        report.matched[p.key] = m
    return report


def _match_one(p: Player, team: str, positions: set[str], idx: _Index, cutoff: float) -> Match | str:
    rows = idx.rows
    mk = lambda i, method, score=1.0: Match(p.key, str(rows.at[i, "data_id"]), method, score,
                                            str(rows.at[i, "name"]), str(rows.at[i, "team"]))
    if positions & {"DST"}:
        if team in idx.dst:
            return mk(idx.dst[team], "dst")
        return Match(p.key, f"DST:{team}", "dst", 1.0, f"{team} DST", team)
    key = compact(p.name)
    hits = idx.by_key.get(key, [])
    same_team = [i for i in hits if rows.at[i, "team"] == team]
    pos_hits = [i for i in same_team if idx.pos_ok(i, positions)]
    if len(pos_hits) == 1:
        return mk(pos_hits[0], "exact")
    if len(pos_hits) > 1:
        return f"ambiguous exact name on {team}: {[rows.at[i, 'data_id'] for i in pos_hits]}"
    if len(same_team) == 1:
        return mk(same_team[0], "exact_any_pos")
    team_rows = [i for i in idx.by_team.get(team, []) if idx.pos_ok(i, positions)]
    norm = normalize_name(p.name).split()
    if norm:
        first, last = norm[0], " ".join(norm[1:]) if len(norm) > 1 else norm[0]
        # nickname / formal first name on the same team
        variants = {f"{v}{last.replace(' ', '')}" for v in _first_variants(first)}
        nick = [i for i in team_rows if any(compact(n) in variants for n in [rows.at[i, "name"], *rows.at[i, "alt_names"]])]
        if len(nick) == 1:
            return mk(nick[0], "nickname")
        surname = lambda i: " ".join(normalize_name(rows.at[i, "name"]).split()[1:])
        lastn = [i for i in team_rows if surname(i) == last]
        ini = [i for i in lastn if normalize_name(rows.at[i, "name"])[:1] == first[:1]]
        if len(ini) == 1:
            return mk(ini[0], "initial")
        if len(lastn) == 1:
            return mk(lastn[0], "last_name")
    other = [i for i in hits if idx.pos_ok(i, positions)] or hits
    if len(other) == 1:
        return mk(other[0], "exact_other_team")
    pool = {compact(rows.at[i, "name"]): i for i in team_rows}
    close = difflib.get_close_matches(key, list(pool), n=2, cutoff=cutoff)
    if close:
        best = difflib.SequenceMatcher(None, key, close[0]).ratio()
        second = difflib.SequenceMatcher(None, key, close[1]).ratio() if len(close) > 1 else 0.0
        if best - second >= 0.04:
            return mk(pool[close[0]], "fuzzy", round(best, 3))
        return f"ambiguous fuzzy match on {team}: {close}"
    if not idx.by_team.get(team):
        return f"no candidates for team {team!r}"
    return f"no candidate on {team} matching {p.name!r} at {sorted(positions)}"
