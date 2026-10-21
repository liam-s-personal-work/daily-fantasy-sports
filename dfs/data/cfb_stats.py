"""College-football player-game offensive stat lines and usage from cfbfastR play-by-play.

Attribution per play (scrimmage plays only, nullified plays dropped):

1. ESPN participant ids in the pbp (``rush_player_id``, ``reception_player_id``,
   ``completion_player_id`` ...) when present;
2. otherwise the play text, parsed in the three styles the feed uses
   (``#16 J.Overton, Jr. rush right for 14 yards``, ``Chris Zellous run for 4 yds``,
   ``Smith,Cameren rush middle``) and resolved to an athlete id on the offense through a
   per-team-season name index (ESPN roster: jersey + names; plus every participant name the
   pbp itself attaches to that team).

Conventions (match the official NCAA/ESPN box score, which is what the sites score):
sacks are rushing attempts with negative yards (``rush_yds`` includes them; ``carry_yds``
does not), interceptions are pass attempts, two-point conversions come from the scoring
play's text (passer, rusher and, when named, receiver each get one), kick/punt return TDs
from the returner named in the text. Fumbles lost: scrimmage plays where the opponent
recovered, charged to the named fumbler or else the ball carrier.

``validate_against_box`` compares lines with ESPN's official player box scores and
``validate_against_aggregates`` with the sportsdataverse season aggregate files.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Mapping

import numpy as np
import pandas as pd

SCORING_STATS = ["pass_yds", "pass_td", "int_thrown", "rush_yds", "rush_td", "rec", "rec_yds", "rec_td",
                 "fumble_lost", "two_pt", "return_td"]
COUNT_STATS = ["pass_att", "pass_cmp", "pass_yds", "pass_td", "int_thrown", "sacks", "sack_yds", "carries",
               "carry_yds", "rush_att", "rush_yds", "rush_td", "targets", "rec", "rec_yds", "rec_td",
               "fumble_lost", "two_pt", "return_td",
               # usage on competitive (non-garbage-time) snaps, see ``competitive_mask``
               "c_pass_att", "c_sacks", "c_carries", "c_targets"]
TEAM_STATS = ["team_pass_att", "team_dropbacks", "team_carries", "team_plays", "team_targets", "team_rec",
              "team_pass_yds", "team_rush_yds", "team_c_pass_att", "team_c_dropbacks", "team_c_carries"]
# Garbage time (Bill Connelly's CFB definition): the snap's margin exceeds this in the quarter.
GARBAGE_MARGIN = {1: 43, 2: 37, 3: 27, 4: 21}
OFFENSE_POS = {"QB", "RB", "WR", "TE", "FB", "ATH", "HB", "SB", "TB", "-", ""}
FUMBLE_LOST_TYPES = {"Fumble Recovery (Opponent)", "Fumble Recovery (Opponent) Touchdown", "Fumble Return Touchdown"}
RETURN_TD_TYPES = {"Punt Return Touchdown", "Kickoff Return Touchdown", "Kickoff Return (Offense) Touchdown"}
_SUFFIX = {"jr", "sr", "ii", "iii", "iv", "v", "lll"}


# --------------------------------------------------------------------------- names


def _ascii(s: str) -> str:
    return unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()


def _tokens(s: str) -> list[str]:
    s = _ascii(s).lower()
    s = re.sub(r"[.'`’]", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return [t for t in s.split() if t not in _SUFFIX]


def name_parts(raw: str) -> tuple[str, str, bool] | None:
    """Text name -> (first-or-initial, last, is_initial). ``None`` when nothing name-like remains.

    'J.Overton, Jr.' -> ('j', 'overton', True); 'M.Van Buren Jr.' -> ('m', 'vanburen', True);
    'Smith,Cameren' -> ('cameren', 'smith', False); 'Chris Zellous' -> ('chris', 'zellous', False).
    """
    if raw is None or (isinstance(raw, float) and np.isnan(raw)):
        return None
    s = _ascii(str(raw)).strip().strip(",;:")
    s = re.sub(r",\s*(jr|sr|ii|iii|iv|v|lll)\.?\s*$", "", s, flags=re.I).strip()
    m = re.match(r"^([A-Za-z])\.\s*([A-Za-z].*)$", s)
    if m:
        last = "".join(_tokens(m.group(2)))
        return (m.group(1).lower(), last, True) if last else None
    if "," in s:
        last, _, first = s.partition(",")
        ft, lt = _tokens(first), _tokens(last)
        if ft and lt:
            return ft[0], "".join(lt), False
    toks = _tokens(s)
    if len(toks) >= 2:
        return toks[0], "".join(toks[1:]), False
    return None


def compact_name(raw: str) -> str:
    return "".join(_tokens(raw))


@dataclass
class TeamIndex:
    """Name/jersey index of one team-season's players (athlete ids as int)."""

    full: dict[str, set[int]] = field(default_factory=lambda: defaultdict(set))
    init_last: dict[tuple[str, str], set[int]] = field(default_factory=lambda: defaultdict(set))
    last: dict[str, set[int]] = field(default_factory=lambda: defaultdict(set))
    jersey: dict[str, set[int]] = field(default_factory=lambda: defaultdict(set))
    offense: set[int] = field(default_factory=set)
    names: dict[int, str] = field(default_factory=dict)

    def add(self, athlete_id: int, name: str, jersey=None, position: str | None = None,
            first: str | None = None, last: str | None = None) -> None:
        aid = int(athlete_id)
        parts = name_parts(name)
        firsts, lasts = set(), set()
        if parts:
            firsts.add(parts[0]); lasts.add(parts[1])
        if first and last and _tokens(first) and _tokens(last):
            firsts.add(_tokens(first)[0]); lasts.add("".join(_tokens(last)))
        for f in firsts:
            for l in lasts:
                self.full[f + l].add(aid)
                self.init_last[(f[:1], l)].add(aid)
        for l in lasts:
            self.last[l].add(aid)
        self.full[compact_name(name)].add(aid)
        if jersey is not None and str(jersey).strip() not in ("", "nan", "None"):
            self.jersey[str(int(float(jersey))) if re.match(r"^\d+(\.0)?$", str(jersey)) else str(jersey)].add(aid)
        if position is None or str(position).upper() in OFFENSE_POS:
            self.offense.add(aid)
        self.names.setdefault(aid, str(name))

    def _pick(self, ids: set[int]) -> int | None:
        if len(ids) == 1:
            return next(iter(ids))
        off = ids & self.offense
        return next(iter(off)) if len(off) == 1 else None

    def resolve(self, raw: str | None, jersey: str | None = None) -> int | None:
        hit = self._resolve(raw, jersey)
        if hit is None and raw and jersey is None:
            # "Ryan Coleman-Williams Alabama Penalty": drop trailing tokens until a full name matches
            toks = str(raw).split()
            for n in range(len(toks) - 1, 1, -1):
                cand = self._pick(self.full.get(compact_name(" ".join(toks[:n])), set()))
                if cand is not None:
                    return cand
        return hit

    def _resolve(self, raw: str | None, jersey: str | None = None) -> int | None:
        parts = name_parts(raw) if raw else None
        j = str(int(jersey)) if jersey is not None and str(jersey).isdigit() else None
        jids = self.jersey.get(j, set()) if j else set()
        if parts:
            first, last, is_init = parts
            if not is_init:
                hit = self._pick(self.full.get(first + last, set()))
                if hit is not None:
                    return hit
            il = self.init_last.get((first[:1], last), set())
            if jids:
                hit = self._pick(il & jids) or self._pick(self.last.get(last, set()) & jids)
                if hit is not None:
                    return hit
            hit = self._pick(il)
            if hit is not None:
                return hit
            lids = self.last.get(last, set())
            if len(lids) == 1 and not jids:
                return next(iter(lids))
            if jids and not il and not lids:  # name unknown to the index: trust the jersey if unique on offense
                return self._pick(jids)
            return None
        return self._pick(jids) if jids else None


def build_team_indexes(pbp: pd.DataFrame, rosters: pd.DataFrame | None) -> dict[tuple[int, int], TeamIndex]:
    """(season, team_id) -> TeamIndex from the roster file plus pbp participant names."""
    idx: dict[tuple[int, int], TeamIndex] = defaultdict(TeamIndex)
    if rosters is not None and len(rosters):
        r = rosters.dropna(subset=["athlete_id", "team_id"])
        for row in r.itertuples(index=False):
            # ESPN roster ``display_name`` is the abbreviated form ("Je. Smith"); the full name is
            # ``athlete_display_name`` / ``full_name``
            nm = next((v for v in (getattr(row, "athlete_display_name", None), getattr(row, "full_name", None),
                                   f"{getattr(row, 'first_name', '') or ''} {getattr(row, 'last_name', '') or ''}".strip())
                       if isinstance(v, str) and v.strip()), None)
            if nm is None:
                continue
            idx[(int(row.season), int(row.team_id))].add(
                row.athlete_id, nm, getattr(row, "jersey", None), getattr(row, "position_abbreviation", None),
                getattr(row, "first_name", None), getattr(row, "last_name", None))
    off_cols = [("rush_player_id", "rush_player"), ("reception_player_id", "reception_player"),
                ("completion_player_id", "completion_player"), ("incompletion_player_id", "incompletion_player"),
                ("target_player_id", "target_player"), ("interception_thrown_player_id", "interception_thrown_player"),
                ("sack_taken_player_id", "sack_taken_player")]
    team = _pos_team_id(pbp)
    for id_col, nm_col in off_cols:
        if id_col not in pbp.columns:
            continue
        sub = pd.DataFrame({"season": pbp["season"], "team_id": team, "aid": pd.to_numeric(pbp[id_col], errors="coerce"),
                            "name": pbp[nm_col]}).dropna().drop_duplicates(["season", "team_id", "aid"])
        for row in sub.itertuples(index=False):
            ti = idx[(int(row.season), int(row.team_id))]
            if int(row.aid) not in ti.names:
                ti.add(row.aid, row.name, None, None)
    return idx


# --------------------------------------------------------------------------- text parsing

_PFX = r"^(?:\(\s*\d+:\d+\s*\)\s*)?(?:\[[A-Za-z]+\]\s*)?(?:(?:No Huddle-Shotgun|No Huddle|Shotgun|Huddle-Shotgun|Huddle)\s+)?"
_NM = r"(?P<n>[A-Z][^#()]*?)"
_J = r"#(?P<j>\d+)\s+(?P<n>[^#()]+?)"
PATTERNS: dict[str, list[str]] = {
    "rusher": [_J + r"\s+rush\s+(?!attempt)", r"Kneel down by " + _J + r"\s+at\b", _PFX + _NM + r"\s+(?:run|rush)\s+(?!attempt)",
               _PFX + _NM + r"\s+takes a knee", r"^Kneel down by " + _NM + r"\s+at\b", r"^" + _NM + r"\s+\d+\s+Yd\s+Run\b"],
    "passer": [_J + r"\s+pass\s+(?:complete|incomplete|intercepted)", _PFX + _NM + r"\s+pass\s+(?:complete|incomplete|intercepted|to)\b",
               r"^[A-Z][^#()]*?\s+\d+\s+Yd\s+pass from\s+" + _NM + r"\s*(?:\(|$)"],
    "target": [r"pass\s+(?:complete|incomplete)[^#]*?\bto\s+" + _J + r"\s+(?:caught|thrown)\b",
               r"pass\s+(?:complete\s+|incomplete\s+)?to\s+" + _NM + r"(?:\s+for\b|\s*,|\.?\s*$|\s+[A-Z][\w.&' ]*? Penalty\b)",
               r"^" + _NM + r"\s+\d+\s+Yd\s+pass from\b"],
    "sacked": [_J + r"\s+sacked\b", _PFX + _NM + r"\s+sacked\b"],
    "fumbler": [r"fumbled? by " + _J + r"\s+at\b", r"fumbled? by " + _NM + r"\s+(?:at|recovered|forced)\b"],
    "returner": [_J + r"\s+return(?:s|ed)?\s+(?:for\s+)?\d+\s+yards?", r"^" + _NM + r"\s+\d+\s+Yd\s+(?:Punt|Kickoff|Kick)\s+Return\b",
                 _NM + r"\s+(?:punt|kickoff|kick)\s+return\s+for\b", r"\b" + _NM + r"\s+return\s+for\s+\d+\s+yds?\b"],
}
_COMPILED = {k: [re.compile(p) for p in v] for k, v in PATTERNS.items()}
_TWO_J = re.compile(r"#(?P<j>\d+)\s+(?P<n>[^#()]+?)\s+(?P<kind>rush|pass) attempt (?P<res>successful|failed|good|no good)", re.I)
# "(B Run for Two-Point Conversion)" (2026 feed) / "(T. Chambliss Run For Two-point Conversion)" (2025 feed)
_TWO_RUN = re.compile(r"\((?P<n>[A-Z][^()]*?)\s+(?:run|rush) for two-point conversion\)", re.I)
_TWO_PASS = re.compile(r"\((?P<n>[A-Z][^()]*?)\s+pass to\s+(?P<r>[A-Z][^()]*?)\s+for two-point conversion\)", re.I)
_TWO_LINE = re.compile(r"#(?P<j>\d+)\s+(?P<n>[^#()]+?)\s+rush attempt successful \(two-point conversion\)", re.I)


def parse_actor(text: str, role: str) -> tuple[str | None, str | None]:
    """(jersey, raw name) of the first actor in ``role`` found in a play text, else (None, None)."""
    if not isinstance(text, str):
        return None, None
    for rx in _COMPILED[role]:
        m = rx.search(text)
        if m:
            g = m.groupdict()
            name = (g.get("n") or "").strip().rstrip(",")
            if name:
                return g.get("j"), name
    return None, None


def parse_two_point(text: str, play_type: str | None = None) -> list[tuple[str | None, str]]:
    """Successful two-point conversion participants [(jersey, name)] in a scoring play's text."""
    if not isinstance(text, str):
        return []
    out: list[tuple[str | None, str]] = []
    m = _TWO_PASS.search(text)
    if m:
        return [(None, m.group("n").strip()), (None, m.group("r").strip())]
    m = _TWO_RUN.search(text)
    if m:
        return [(None, m.group("n").strip())]
    m = _TWO_LINE.search(text)
    if m:
        return [(m.group("j"), m.group("n").strip())]
    hits = list(_TWO_J.finditer(text))
    if hits:
        last = hits[-1]
        tail = text[last.end():]
        if last.group("res").lower() in ("successful", "good") and "NO PLAY" not in tail.upper():
            out.append((last.group("j"), last.group("n").strip()))
    return out


# --------------------------------------------------------------------------- play-by-play -> stat lines


def _pos_team_id(pbp: pd.DataFrame) -> pd.Series:
    home_id = pd.to_numeric(pbp["home_team_id"], errors="coerce")
    away_id = pd.to_numeric(pbp["away_team_id"], errors="coerce")
    return pd.Series(np.where(pbp["pos_team"] == pbp["home"], home_id,
                              np.where(pbp["pos_team"] == pbp["away"], away_id, np.nan)), index=pbp.index)


def _def_team_id(pbp: pd.DataFrame) -> pd.Series:
    home_id = pd.to_numeric(pbp["home_team_id"], errors="coerce")
    away_id = pd.to_numeric(pbp["away_team_id"], errors="coerce")
    return pd.Series(np.where(pbp["pos_team"] == pbp["home"], away_id,
                              np.where(pbp["pos_team"] == pbp["away"], home_id, np.nan)), index=pbp.index)


def _flag(df: pd.DataFrame, col: str) -> pd.Series:
    return pd.to_numeric(df[col], errors="coerce").fillna(0).astype(float) > 0 if col in df.columns \
        else pd.Series(False, index=df.index)


@dataclass
class ParseStats:
    """How each role was attributed: participant id, parsed text, or unresolved."""

    counts: dict[str, dict[str, int]] = field(default_factory=lambda: defaultdict(lambda: defaultdict(int)))
    agreement: dict[str, tuple[int, int]] = field(default_factory=dict)  # role -> (text==id, both present)

    def summary(self) -> dict:
        out = {}
        for role, c in self.counts.items():
            n = sum(c.values())
            out[role] = {k: v for k, v in c.items()} | {"n": n, "unresolved_rate": round(c.get("unresolved", 0) / n, 4) if n else 0.0}
        out["text_vs_id_agreement"] = {r: round(a / b, 4) if b else None for r, (a, b) in self.agreement.items()}
        return out


def _attribute(plays: pd.DataFrame, role: str, id_col: str | None, team: pd.Series,
               indexes: Mapping[tuple[int, int], TeamIndex], stats: ParseStats, check_text: bool = False) -> pd.Series:
    """athlete id (float, NaN when unknown) per play for ``role``: participant id, else parsed text."""
    ids = pd.to_numeric(plays[id_col], errors="coerce") if id_col and id_col in plays.columns \
        else pd.Series(np.nan, index=plays.index)
    need = ids.isna() if not check_text else pd.Series(True, index=plays.index)
    out = ids.copy()
    if need.any():
        cache: dict[tuple, int | None] = {}
        parsed = []
        for i, text, season, tid in zip(plays.index[need], plays.loc[need, "play_text"], plays.loc[need, "season"],
                                        team[need]):
            j, nm = parse_actor(text, role)
            if nm is None and j is None or pd.isna(tid):
                parsed.append((i, None))
                continue
            key = (int(season), int(tid), j, nm)
            if key not in cache:
                ti = indexes.get((int(season), int(tid)))
                cache[key] = ti.resolve(nm, j) if ti is not None else None
            parsed.append((i, cache[key]))
        res = pd.Series({i: (np.nan if a is None else float(a)) for i, a in parsed}, dtype=float)
        if check_text:
            both = ids.notna() & res.reindex(ids.index).notna()
            stats.agreement[role] = (int((ids[both] == res.reindex(ids.index)[both]).sum()), int(both.sum()))
            fill = ids.isna()
            out[fill] = res.reindex(ids.index)[fill]
        else:
            out[need] = res.reindex(out.index[need])
    c = stats.counts[role]
    c["id"] += int(ids.notna().sum())
    c["text"] += int((ids.isna() & out.notna()).sum())
    c["unresolved"] += int(out.isna().sum())
    return out


BASE_KIND = {"Rush": "rush", "Rushing Touchdown": "rush", "Pass Reception": "complete", "Passing Touchdown": "complete",
             "Pass Completion": "complete", "Pass Incompletion": "incomplete", "Interception Return": "interception",
             "Interception Return Touchdown": "interception", "Pass Interception Return": "interception",
             "Pass Interception": "interception", "Sack": "sack"}
# Rows whose type does not say what the offense did: read the text (a fumbled run is typed by the recovery).
TEXT_KIND_TYPES = {"Fumble Recovery (Own)", "Fumble Recovery (Opponent)", "Fumble Recovery (Opponent) Touchdown",
                   "Fumble Return Touchdown", "Fumble", "Safety", "Penalty", "Uncategorized"}
_REVIEW_TAIL = re.compile(r"\.?\s*(?:The previous play is under (?:automatic )?review|[A-Za-z .&']+ is challenging the ruling)"
                          r".*$|\s*\(Original Play:.*$", re.S)
_KIND_RX = [("sack", re.compile(r"\bsacked\b|\bSacked at\b")),
            ("interception", re.compile(r"pass intercepted|intercepted by", re.I)),
            ("incomplete", re.compile(r"pass incomplete")),
            ("complete", re.compile(r"pass complete|\d+ Yd pass from|\bpass to [A-Z#]")),
            ("rush", re.compile(r"\brush\b(?! attempt)|\brun for\b|\d+ Yd Run\b|Kneel down|takes a knee"))]
_YDS_RX = {
    "rush": [re.compile(r"rush\s+(?:[a-z]+\s+)?for\s+(?P<y>\d+)\s+yards?\s*(?P<s>gain|loss)?"),
             re.compile(r"Kneel down by .*? for (?P<s>loss) of (?P<y>\d+) yards?"),
             re.compile(r"run for (?P<s>a loss of )?(?P<y>\d+) y(?:ar)?ds?"), re.compile(r"(?P<y>\d+) Yd Run\b"),
             re.compile(r"(?:rush\s+(?:[a-z]+\s+)?|run )for (?P<z>no gain)")],
    "complete": [re.compile(r"caught at [^,]*?,? for (?P<y>\d+) yards?\s*(?P<s>gain|loss)?"),
                 re.compile(r"pass complete to .+? for (?P<s>a loss of )?(?P<y>\d+) y(?:ar)?ds?"),
                 re.compile(r"(?P<y>\d+) Yd pass from"), re.compile(r"pass to .+? for (?P<y>\d+) yds"),
                 re.compile(r"pass complete .*?for (?P<z>no gain)")],
    "sack": [re.compile(r"sacked (?:by .+? )?for (?:a )?loss of (?P<y>\d+) yards?"),
             re.compile(r"Sacked at \S+(?: \S+)? for -(?P<y>\d+) yards?")],
}


def final_ruling(text: str) -> str:
    """Play text without replay-review tails, so an overturned call reads as the final ruling."""
    if not isinstance(text, str):
        return ""
    return _REVIEW_TAIL.sub("", text) if ("OVERTURNED" in text.upper() or "Original Play" in text) else text


def kind_from_text(text: str) -> str | None:
    d = final_ruling(text)
    for kind, rx in _KIND_RX:
        if rx.search(d):
            return kind
    return None


def yards_from_text(text: str, kind: str) -> float | None:
    """Signed yards of the offensive action in ``text`` (sacks negative), ``None`` when not stated."""
    d = final_ruling(text)
    for rx in _YDS_RX.get(kind, []):
        m = rx.search(d)
        if m:
            g = m.groupdict()
            if g.get("z"):
                return 0.0
            y = float(g["y"])
            if kind == "sack":
                return -y
            return -y if (g.get("s") or "").strip().lower() in ("loss", "a loss of") else y
    return None


def classify_plays(pbp: pd.DataFrame, sack_yards: str = "yds_sacked") -> pd.DataFrame:
    """One row per counting offensive play: ``kind`` (rush|sack|complete|incomplete|interception),
    ``td`` (offensive TD by the play's ball carrier / passer-receiver), signed ``yds``, ``fumble_lost``.

    Kinds come from ``play_type`` except for fumble/safety/penalty/uncategorized rows and overturned
    calls, which are read from the final-ruling text. Rows nullified by penalty ("NO PLAY") and
    two-point tries are dropped. Yards: ``yards_gained`` on ordinary rows; on fumble/penalty rows it
    includes return or penalty yardage, so the text (or ``rush_yds``) is used; sacks use ``yds_sacked``.
    """
    p = pbp
    pt = p["play_type"].fillna("")
    text = p["play_text"].fillna("")
    kind = pt.map(BASE_KIND)
    overturned = text.str.contains("OVERTURNED", case=False, regex=False)
    reread = pt.isin(TEXT_KIND_TYPES) | (overturned & kind.notna())
    if reread.any():
        kind = kind.where(~reread, text[reread].map(kind_from_text))
    no_play = text.str.contains(r"NO PLAY", case=False, regex=True) & (pt.isin(TEXT_KIND_TYPES) | kind.isna())
    if "penalty_no_play" in p.columns:
        no_play |= p["penalty_no_play"].fillna(False).astype(bool)
    keep = kind.notna() & ~no_play
    q = p.loc[keep].copy()
    q["kind"] = kind[keep]
    t = text[keep]
    ordinary = q["play_type"].isin(BASE_KIND) & ~overturned[keep]
    yg = pd.to_numeric(q["yards_gained"], errors="coerce")
    parsed = pd.Series([yards_from_text(x, k) if not o else None for x, k, o in zip(t, q["kind"], ordinary)],
                       index=q.index, dtype=float)
    yds = yg.where(ordinary, parsed)
    if "rush_yds" in q.columns:
        ry = pd.to_numeric(q["rush_yds"], errors="coerce")
        yds = yds.where(~((q["kind"] == "rush") & yds.isna()), ry)
    if sack_yards in q.columns:
        sy = pd.to_numeric(q[sack_yards], errors="coerce")
        sy = -sy.abs()
        yds = yds.where(~((q["kind"] == "sack") & sy.notna()), sy)
    yds = yds.where(yds.notna(), yg.where(q["kind"].isin(["rush", "complete", "sack"]), 0.0)).fillna(0.0)
    # "rush right for 29 yards gain (6) ... PENALTY ... Holding": the box credits the yards in
    # parentheses (to the spot of a foul), on every row type
    credited = pd.to_numeric(t.str.extract(r"(?:rush|run)\b[^#]*?for \d+ yards? ?(?:gain|loss)? ?\((-?\d+)\)",
                                           expand=False), errors="coerce")
    yds = yds.where(~((q["kind"] == "rush") & credited.notna()), credited)
    q["yds"] = yds.where(~q["kind"].isin(["incomplete", "interception"]), 0.0)
    final = t.map(final_ruling)
    nullified = final.str.contains("nullified", case=False, regex=False)
    td_text = final.str.contains(r"TOUCHDOWN|for a TD|\d+ Yd (?:Run|pass from)", regex=True)
    td_flag = (_flag(q, "rush_td") | _flag(q, "pass_td"))
    td = (td_flag.where(ordinary, td_text) & ~nullified)
    # a fumble/safety row's TOUCHDOWN is the defense's (or a recovery TD), never the ball carrier's
    td &= ~q["play_type"].isin(TEXT_KIND_TYPES - {"Penalty", "Uncategorized"})
    q["td"] = td & q["kind"].isin(["rush", "complete"])
    q["fumble_lost"] = q["play_type"].isin(FUMBLE_LOST_TYPES)
    return q


def pre_snap_margin(pbp: pd.DataFrame) -> pd.Series:
    """Offense's score margin before each play (pbp scores are after the play), aligned to ``pbp``."""
    if not {"pos_team_score", "def_pos_team_score", "pos_team", "home"}.issubset(pbp.columns):
        return pd.Series(0.0, index=pbp.index)
    p = pbp
    is_home = (p["pos_team"] == p["home"]).to_numpy()
    ps = pd.to_numeric(p["pos_team_score"], errors="coerce")
    ds = pd.to_numeric(p["def_pos_team_score"], errors="coerce")
    home_after = pd.Series(np.where(is_home, ps, ds), index=p.index)
    away_after = pd.Series(np.where(is_home, ds, ps), index=p.index)
    order = ["game_id", "game_play_number"] if "game_play_number" in p.columns else ["game_id"]
    o = p[order].assign(_h=home_after, _a=away_after).sort_values(order, kind="stable")
    hb = o.groupby("game_id")["_h"].shift(1).fillna(0.0)
    ab = o.groupby("game_id")["_a"].shift(1).fillna(0.0)
    diff = (hb - ab).reindex(p.index)
    return pd.Series(np.where(is_home, diff, -diff), index=p.index)


def competitive_mask(pbp: pd.DataFrame) -> pd.Series:
    """True for snaps outside garbage time (margin within ``GARBAGE_MARGIN`` for the quarter; OT always)."""
    margin = pre_snap_margin(pbp).abs()
    per = pd.to_numeric(pbp["period"], errors="coerce") if "period" in pbp.columns else pd.Series(1, index=pbp.index)
    thr = per.map(GARBAGE_MARGIN).fillna(10 ** 6)
    return (margin <= thr) | margin.isna()


def scrimmage_plays(pbp: pd.DataFrame) -> pd.DataFrame:
    """Counting offensive plays (see ``classify_plays``)."""
    return classify_plays(pbp)


def player_game_lines(pbp: pd.DataFrame, rosters: pd.DataFrame | None = None,
                      indexes: Mapping[tuple[int, int], TeamIndex] | None = None,
                      check_text: bool = False) -> tuple[pd.DataFrame, pd.DataFrame, ParseStats]:
    """Play-by-play -> (player-game lines, team-game totals, attribution stats).

    Player lines: one row per (season, week, game_id, team_id, athlete_id) with ``COUNT_STATS``
    plus ``name``. Team totals: one row per (season, week, game_id, team_id) with ``TEAM_STATS``.
    ``check_text`` parses every play's text too and records text-vs-id agreement (slower).
    """
    if indexes is None:
        indexes = build_team_indexes(pbp, rosters)
    stats = ParseStats()
    comp_all = competitive_mask(pbp)
    p = classify_plays(pbp)
    p["competitive"] = comp_all.reindex(p.index).fillna(True).astype(bool)
    p["team_id"] = _pos_team_id(p)
    p = p[p["team_id"].notna()]
    team = p["team_id"]
    kind = p["kind"]
    rush = kind == "rush"
    sack = kind == "sack"
    att = kind.isin(["complete", "incomplete", "interception"])
    cmp_ = kind == "complete"
    intc = kind == "interception"
    yards = p["yds"].astype(float)
    rush_y = yards
    rush_td = p["td"] & rush
    pass_td = p["td"] & cmp_

    def role(mask: pd.Series, name: str, id_col: str | None) -> pd.Series:
        sub = p.loc[mask]
        return _attribute(sub, name, id_col, team[mask], indexes, stats, check_text) if len(sub) else pd.Series(dtype=float)

    rusher = role(rush, "rusher", "rush_player_id")
    sacked = role(sack, "sacked", "sack_taken_player_id")
    # passer / target participant ids depend on the result
    pid = pd.Series(np.nan, index=p.index)
    for mask, col in ((cmp_, "completion_player_id"), (att & ~cmp_ & ~intc, "incompletion_player_id"),
                      (intc, "interception_thrown_player_id")):
        if mask.any():
            pid[mask] = pd.to_numeric(p.loc[mask, col], errors="coerce") if col in p.columns else np.nan
    tid = pd.Series(np.nan, index=p.index)
    tid[cmp_] = pd.to_numeric(p.loc[cmp_, "reception_player_id"], errors="coerce")
    inc = att & ~cmp_ & ~intc
    if "target_player_id" in p.columns:
        tid[inc] = pd.to_numeric(p.loc[inc, "target_player_id"], errors="coerce")
    # Summary-style scoring rows ("Romello Brinson 75 Yd pass from Kevin Jennings"): the 2024-2025
    # feeds swap completion/reception participants on ~95% of them. Match participant names to the
    # text roles; swap when reversed, otherwise fall back to the text.
    summ = p["play_text"].fillna("").str.extract(
        r"^(?P<rec>[A-Z][^#()]*?)\s+\d+\s+Yd\s+pass from\s+(?P<qb>[A-Z][^#()]*?)\s*(?:\(|$)")
    is_s = summ["rec"].notna() & cmp_
    if is_s.any() and "completion_player" in p.columns:
        cn = lambda s: s.fillna("").map(compact_name)
        c_cmp, c_rec = cn(p["completion_player"]), cn(p["reception_player"])
        t_rec, t_qb = cn(summ["rec"]), cn(summ["qb"])
        swapped = is_s & (c_cmp == t_rec) & (c_rec == t_qb) & (t_rec != "")
        straight = is_s & (c_cmp == t_qb) & (c_rec == t_rec)
        pid0, tid0 = pid.copy(), tid.copy()
        pid[swapped], tid[swapped] = tid0[swapped], pid0[swapped]
        bad = is_s & ~swapped & ~straight
        pid[bad], tid[bad] = np.nan, np.nan
        stats.counts["summary_rows"]["swapped_ids_fixed"] += int(swapped.sum())
        stats.counts["summary_rows"]["ids_ok"] += int(straight.sum())
        stats.counts["summary_rows"]["ids_dropped_for_text"] += int(bad.sum())
    p["_passer_id"] = pid
    p["_target_id"] = tid
    passer = role(att, "passer", "_passer_id")
    if len(passer) and passer.isna().any():
        # summary-style rows ("X 41 Yd Interception Return") name only the defender: use the team's
        # previous (else next) identified passer in the same game
        order = "game_play_number" if "game_play_number" in p.columns else None
        frame = pd.DataFrame({"g": p.loc[passer.index, "game_id"], "t": team[passer.index], "a": passer})
        if order:
            frame["o"] = pd.to_numeric(p.loc[passer.index, order], errors="coerce")
            frame = frame.sort_values(["g", "t", "o"], kind="stable")
        filled = frame.groupby(["g", "t"])["a"].transform(lambda s: s.ffill().bfill())
        n_inf = int((frame["a"].isna() & filled.notna()).sum())
        passer = filled.reindex(passer.index)
        stats.counts["passer"]["inferred_from_neighbouring_play"] += n_inf
        stats.counts["passer"]["unresolved"] -= n_inf
    target = role(att & ~intc, "target", "_target_id")

    ev: list[pd.DataFrame] = []
    meta = p[["season", "week", "game_id", "team_id"]]

    def add(ids: pd.Series, cols: dict[str, pd.Series]) -> None:
        ids = ids.dropna()
        if ids.empty:
            return
        d = meta.loc[ids.index].copy()
        d["athlete_id"] = ids.astype("int64")
        for k, v in cols.items():
            d[k] = v.reindex(ids.index).astype(float)
        ev.append(d)

    one = pd.Series(1.0, index=p.index)
    comp = p["competitive"].astype(float)
    add(rusher, {"carries": one, "carry_yds": rush_y, "rush_att": one, "rush_yds": rush_y,
                 "rush_td": rush_td.astype(float), "c_carries": comp})
    add(sacked, {"sacks": one, "sack_yds": yards, "rush_att": one, "rush_yds": yards, "c_sacks": comp})
    add(passer, {"pass_att": one, "pass_cmp": cmp_.astype(float), "pass_yds": yards.where(cmp_, 0.0),
                 "pass_td": pass_td.astype(float), "int_thrown": intc.astype(float), "c_pass_att": comp})
    add(target, {"targets": one, "rec": cmp_.astype(float), "rec_yds": yards.where(cmp_, 0.0),
                 "rec_td": pass_td.astype(float), "c_targets": comp})

    # fumbles lost: scrimmage plays recovered by the opponent
    lost = p["play_type"].isin(FUMBLE_LOST_TYPES)
    if lost.any():
        f = pd.to_numeric(p.loc[lost, "fumble_player_id"], errors="coerce") if "fumble_player_id" in p.columns \
            else pd.Series(np.nan, index=p.index[lost])
        caught = target.reindex(f.index).where(cmp_.reindex(f.index, fill_value=False))
        carrier = pd.concat([rusher.reindex(f.index), caught, sacked.reindex(f.index)],
                            axis=1).bfill(axis=1).iloc[:, 0]
        p["_fum_id"] = np.nan
        p.loc[lost, "_fum_id"] = f.fillna(carrier)
        fum = role(lost, "fumbler", "_fum_id")
        add(fum, {"fumble_lost": one})

    # two-point conversions (scoring team = offense on offensive TDs, else the defense)
    two_rows = []
    # scoring plays: ``touchdown`` is 0 on some TD-typed rows (2026: 396), so the type and the
    # rush/pass TD flags count too
    tp_mask = (pd.to_numeric(pbp.get("touchdown"), errors="coerce").fillna(0) > 0) | _flag(pbp, "rush_td") | \
        _flag(pbp, "pass_td") | pbp["play_type"].fillna("").str.contains("Touchdown", regex=False) | \
        pbp["play_type"].isin(["Two Point Pass", "Two Point Rush"])
    tp = pbp.loc[tp_mask & pbp["play_text"].fillna("").str.contains(r"(?i)two-point|attempt succe", regex=True)]
    if len(tp):
        tp_team, tp_def = _pos_team_id(tp), _def_team_id(tp)
        for i, text in tp["play_text"].items():
            for j, nm in parse_two_point(text, tp.at[i, "play_type"]):
                aid = None
                for t in (tp_team[i], tp_def[i]):
                    if pd.notna(t) and (ti := indexes.get((int(tp.at[i, "season"]), int(t)))) is not None:
                        aid = ti.resolve(nm, j)
                        if aid is not None:
                            two_rows.append({"season": tp.at[i, "season"], "week": tp.at[i, "week"],
                                             "game_id": tp.at[i, "game_id"], "team_id": float(t), "athlete_id": aid,
                                             "two_pt": 1.0})
                            break
                stats.counts["two_pt"]["text" if aid is not None else "unresolved"] += 1
    if two_rows:
        ev.append(pd.DataFrame(two_rows))

    # kick / punt return touchdowns
    rt = pbp.loc[pbp["play_type"].isin(RETURN_TD_TYPES)]
    ret_rows = []
    for i, text in rt["play_text"].items():
        j, nm = parse_actor(text, "returner")
        aid = None
        if nm:
            teams = [_def_team_id(rt.loc[[i]]).iloc[0], _pos_team_id(rt.loc[[i]]).iloc[0]]
            if "Kickoff" in str(rt.at[i, "play_type"]):
                teams = teams[::-1]
            for t in teams:
                if pd.notna(t) and (ti := indexes.get((int(rt.at[i, "season"]), int(t)))) is not None:
                    aid = ti.resolve(nm, j)
                    if aid is not None:
                        ret_rows.append({"season": rt.at[i, "season"], "week": rt.at[i, "week"],
                                         "game_id": rt.at[i, "game_id"], "team_id": float(t), "athlete_id": aid,
                                         "return_td": 1.0})
                        break
        stats.counts["returner"]["text" if aid is not None else "unresolved"] += 1
    if ret_rows:
        ev.append(pd.DataFrame(ret_rows))

    keys = ["season", "week", "game_id", "team_id", "athlete_id"]
    if ev:
        allev = pd.concat(ev, ignore_index=True)
        for c in COUNT_STATS:
            if c not in allev.columns:
                allev[c] = 0.0
        lines = allev.groupby(keys, as_index=False)[COUNT_STATS].sum()
    else:
        lines = pd.DataFrame(columns=keys + COUNT_STATS)
    lines[["season", "week", "game_id", "team_id", "athlete_id"]] = lines[keys].astype("int64")
    names: dict[int, str] = {}
    for ti in indexes.values():
        names.update(ti.names)
    lines["name"] = lines["athlete_id"].map(names)

    tg = pd.DataFrame({"season": p["season"], "week": p["week"], "game_id": p["game_id"], "team_id": team,
                       "team_pass_att": att.astype(float), "team_dropbacks": (att | sack).astype(float),
                       "team_carries": rush.astype(float), "team_plays": (rush | att | sack).astype(float),
                       "team_targets": target.reindex(p.index).notna().astype(float),
                       "team_rec": cmp_.astype(float), "team_pass_yds": yards.where(cmp_, 0.0),
                       "team_rush_yds": rush_y.where(rush, 0.0) + yards.where(sack, 0.0),
                       "team_c_pass_att": (att & p["competitive"]).astype(float),
                       "team_c_dropbacks": ((att | sack) & p["competitive"]).astype(float),
                       "team_c_carries": (rush & p["competitive"]).astype(float)})
    team_totals = tg.groupby(["season", "week", "game_id", "team_id"], as_index=False)[TEAM_STATS].sum()
    team_totals[["season", "week", "game_id", "team_id"]] = team_totals[["season", "week", "game_id", "team_id"]].astype("int64")
    return lines, team_totals, stats


# --------------------------------------------------------------------------- official box scores


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").fillna(0.0)


def box_lines(player_box: pd.DataFrame) -> pd.DataFrame:
    """ESPN player box (one row per athlete per category) -> one row per player-game, same stat keys."""
    if player_box is None or player_box.empty:
        return pd.DataFrame()
    # ESPN's team pseudo-player is " Team" (leading space) with a negative athlete id: bad snaps,
    # team kneel-downs and team fumbles. It is not a player, so it is dropped.
    nm = player_box["athlete_name"].fillna("").astype(str).str.strip().str.lower()
    aid = pd.to_numeric(player_box["athlete_id"], errors="coerce")
    b = player_box[(nm != "team") & (aid > 0)].copy()
    keys = ["season", "game_id", "team_id", "athlete_id"]
    parts = []
    ps = b[b["category"] == "passing"]
    if len(ps):
        ca = ps["completions/passingAttempts"].fillna("0/0").astype(str).str.split("/", expand=True)
        parts.append(ps[keys].assign(pass_cmp=_num(ca[0]), pass_att=_num(ca[1]), pass_yds=_num(ps["passingYards"]),
                                     pass_td=_num(ps["passingTouchdowns"]), int_thrown=_num(ps["interceptions"])))
    ru = b[b["category"] == "rushing"]
    if len(ru):
        parts.append(ru[keys].assign(rush_att=_num(ru["rushingAttempts"]), rush_yds=_num(ru["rushingYards"]),
                                     rush_td=_num(ru["rushingTouchdowns"])))
    rc = b[b["category"] == "receiving"]
    if len(rc):
        parts.append(rc[keys].assign(rec=_num(rc["receptions"]), rec_yds=_num(rc["receivingYards"]),
                                     rec_td=_num(rc["receivingTouchdowns"])))
    fu = b[b["category"] == "fumbles"]
    if len(fu):
        parts.append(fu[keys].assign(fumble_lost=_num(fu["fumblesLost"])))
    kr = b[b["category"] == "kickReturns"]
    if len(kr):
        parts.append(kr[keys].assign(return_td=_num(kr["kickReturnTouchdowns"])))
    pr = b[b["category"] == "puntReturns"]
    if len(pr):
        parts.append(pr[keys].assign(return_td=_num(pr["puntReturnTouchdowns"])))
    if not parts:
        return pd.DataFrame()
    allp = pd.concat(parts, ignore_index=True)
    stat_cols = [c for c in allp.columns if c not in keys]
    out = allp.groupby(keys, as_index=False)[stat_cols].sum()
    names = b.drop_duplicates("athlete_id").set_index("athlete_id")["athlete_name"]
    out["name"] = out["athlete_id"].map(names)
    return out


def fantasy_points(lines: pd.DataFrame, table_id: str, rush_yds_col: str = "rush_yds") -> pd.Series:
    """Vectorized dfs.scoring for the offense role (linear + thresholds); equals ``score()`` row by row."""
    from dfs.scoring import load_table

    block = load_table(table_id)["roles"]["offense"]
    cols = {k: (rush_yds_col if k == "rush_yds" else k) for k in (block.get("linear") or {})}
    pts = pd.Series(0.0, index=lines.index)
    for stat, w in (block.get("linear") or {}).items():
        c = cols[stat]
        if c in lines.columns:
            pts += float(w) * pd.to_numeric(lines[c], errors="coerce").fillna(0.0)
    for t in block.get("thresholds") or []:
        c = rush_yds_col if t["stat"] == "rush_yds" else t["stat"]
        if c in lines.columns:
            pts += np.where(pd.to_numeric(lines[c], errors="coerce").fillna(0.0) >= t["gte"], float(t["pts"]), 0.0)
    return (pts + 1e-9).round(2)


VALIDATE_STATS = ["pass_att", "pass_cmp", "pass_yds", "pass_td", "int_thrown", "rush_att", "rush_yds", "rush_td",
                  "rec", "rec_yds", "rec_td", "fumble_lost", "return_td"]
BOX_STATS = VALIDATE_STATS  # stats the ESPN box carries (no two-point conversions, no targets)


def incomplete_team_games(team_totals: pd.DataFrame, team_box: pd.DataFrame, tol: float = 0.15,
                          min_diff: int = 4) -> pd.DataFrame:
    """Team-games whose play-by-play is visibly incomplete: pass attempts or rushing attempts
    (sacks included, NCAA convention) differ from ESPN's official team box by more than ``tol``
    (and at least ``min_diff`` plays). Team-games missing from the pbp entirely are not listed."""
    if team_box is None or team_box.empty:
        return pd.DataFrame(columns=["game_id", "team_id", "pbp_att", "box_att", "pbp_rush", "box_rush"])
    tb = team_box[["game_id", "team_id", "completionAttempts", "rushingAttempts"]].copy()
    tb["box_att"] = pd.to_numeric(tb["completionAttempts"].astype(str).str.split("/").str[1], errors="coerce")
    tb["box_rush"] = pd.to_numeric(tb["rushingAttempts"], errors="coerce")
    tb["game_id"] = pd.to_numeric(tb["game_id"], errors="coerce").astype("Int64")
    tb["team_id"] = pd.to_numeric(tb["team_id"], errors="coerce").astype("Int64")
    t = team_totals.copy()
    t["pbp_att"] = t["team_pass_att"]
    t["pbp_rush"] = t["team_carries"] + (t["team_dropbacks"] - t["team_pass_att"])
    j = t.merge(tb.dropna(subset=["game_id", "team_id"]).astype({"game_id": "int64", "team_id": "int64"}),
                on=["game_id", "team_id"], how="inner")

    def off(a, b):
        return ((a - b).abs() > tol * b.clip(lower=1)) & ((a - b).abs() >= min_diff)

    bad = off(j["pbp_att"], j["box_att"]) | off(j["pbp_rush"], j["box_rush"])
    return j.loc[bad, ["season", "week", "game_id", "team_id", "pbp_att", "box_att", "pbp_rush", "box_rush"]].reset_index(drop=True)


def repair_with_box(lines: pd.DataFrame, team_totals: pd.DataFrame, box: pd.DataFrame,
                    team_box: pd.DataFrame, tol: float = 0.15) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Replace scoring stats with the official box line for team-games whose pbp is incomplete.

    Returns (lines with ``stat_source`` in {pbp, box} and ``usage_ok``, flagged team-games). Usage
    columns (targets, carries, sacks) of repaired team-games keep their partial pbp values but are
    marked ``usage_ok = False`` so share models can skip them.
    """
    out = lines.copy()
    out["stat_source"] = "pbp"
    out["usage_ok"] = True
    flagged = incomplete_team_games(team_totals, team_box, tol)
    if flagged.empty or box is None or box.empty:
        return out, flagged
    key = set(zip(flagged["game_id"], flagged["team_id"]))
    in_bad = pd.Series([(g, t) in key for g, t in zip(out["game_id"], out["team_id"])], index=out.index)
    b = box[[c for c in ["game_id", "team_id", "athlete_id", "name", *BOX_STATS] if c in box.columns]].copy()
    b = b[[(g, t) in key for g, t in zip(b["game_id"], b["team_id"])]]
    wk = flagged[["game_id", "team_id", "season", "week"]]
    b = b.merge(wk, on=["game_id", "team_id"], how="left")
    keep_cols = [c for c in out.columns if c not in BOX_STATS and c not in ("name",)]
    partial = out.loc[in_bad, keep_cols]
    rep = b.merge(partial, on=["season", "week", "game_id", "team_id", "athlete_id"], how="outer")
    for c in COUNT_STATS:
        if c in rep.columns:
            rep[c] = rep[c].fillna(0.0)
    rep["stat_source"] = "box"
    rep["usage_ok"] = False
    rep["name"] = rep["name"].fillna(rep["athlete_id"].map(out.drop_duplicates("athlete_id").set_index("athlete_id")["name"]))
    out = pd.concat([out.loc[~in_bad], rep[out.columns]], ignore_index=True)
    for c in ("season", "week", "game_id", "team_id", "athlete_id"):
        out[c] = out[c].astype("int64")
    return out, flagged


def validate_against_box(lines: pd.DataFrame, box: pd.DataFrame, table_id: str = "draftkings.cfb",
                         stats: Iterable[str] = VALIDATE_STATS) -> dict:
    """Per-stat totals and exact-match rates of pbp lines vs official box lines on the box's games.

    Player-games are outer-joined on (game_id, athlete_id); a stat missing on one side counts as 0.
    Also compares DK fantasy points per player-game (MAE, share within 1 point).
    """
    games = set(box["game_id"].unique())
    pl = lines[lines["game_id"].isin(games)]
    keys = ["game_id", "athlete_id"]
    stats = [s for s in stats if s in box.columns]
    m = pl[keys + stats].merge(box[keys + stats], on=keys, how="outer", suffixes=("_pbp", "_box")).fillna(0.0)
    rep: dict = {"games": len(games), "player_games_pbp": len(pl), "player_games_box": len(box),
                 "player_games_joined": len(m), "stats": {}}
    for s in stats:
        a, b = m[f"{s}_pbp"], m[f"{s}_box"]
        tot_a, tot_b = float(a.sum()), float(b.sum())
        rep["stats"][s] = {"pbp_total": round(tot_a, 1), "box_total": round(tot_b, 1),
                           "total_diff_pct": round(100 * (tot_a - tot_b) / tot_b, 3) if tot_b else None,
                           "exact_rate": round(float((a == b).mean()), 4),
                           "mae": round(float((a - b).abs().mean()), 4)}
    fa = fantasy_points(m.rename(columns={f"{s}_pbp": s for s in stats}), table_id)
    fb = fantasy_points(m.rename(columns={f"{s}_box": s for s in stats}), table_id)
    d = (fa - fb).abs()
    rel = m[(fa.abs() + fb.abs()) > 0]
    d_rel = d.loc[rel.index]
    rep["fantasy_points"] = {"table": table_id, "player_games_nonzero": int(len(rel)),
                             "mae": round(float(d_rel.mean()), 4), "within_0.5": round(float((d_rel <= 0.5).mean()), 4),
                             "within_1": round(float((d_rel <= 1.0).mean()), 4),
                             "within_3": round(float((d_rel <= 3.0).mean()), 4),
                             "pbp_total": round(float(fa.sum()), 1), "box_total": round(float(fb.sum()), 1)}
    return rep


def validate_against_aggregates(lines: pd.DataFrame, passing: pd.DataFrame, rushing: pd.DataFrame,
                                receiving: pd.DataFrame, schedules: pd.DataFrame | None = None) -> dict:
    """Season sums per player vs the sportsdataverse season aggregates (themselves cfbfastR-pbp derived).

    Scope and conventions of the aggregate files (checked on 2026): FBS-vs-FBS games only
    (``schedules.fbs_game``), so lines are restricted to those games when ``schedules`` is given;
    passing ``att`` excludes interceptions (compared with pbp ``pass_att - int_thrown``); sacks are
    counted with passing, so pbp ``carries``/``carry_yds`` (non-sack rushes) are compared with the
    rushing ``plays``/``yards``.
    """
    if schedules is not None and "fbs_game" in schedules.columns:
        fbs = set(schedules.loc[schedules["fbs_game"].fillna(False).astype(bool), "game_id"].astype("int64"))
        lines = lines[lines["game_id"].isin(fbs)]
    l = lines.assign(att_ex_int=lines["pass_att"] - lines["int_thrown"])
    season = l.groupby("athlete_id")[["att_ex_int", "pass_cmp", "pass_yds", "pass_td", "int_thrown", "sacks", "carries",
                                      "carry_yds", "rush_td", "targets", "rec", "rec_yds", "rec_td"]].sum()
    pairs = {
        "passing": (passing, {"att": "att_ex_int", "comp": "pass_cmp", "yards": "pass_yds", "passing_td": "pass_td",
                              "pass_int": "int_thrown", "sacked": "sacks"}),
        "rushing": (rushing, {"plays": "carries", "yards": "carry_yds", "rushing_td": "rush_td"}),
        "receiving": (receiving, {"targets": "targets", "comp": "rec", "yards": "rec_yds", "passing_td": "rec_td"}),
    }
    rep: dict = {"scope": "FBS-vs-FBS games" if schedules is not None else "all games"}
    for name, (agg, cols) in pairs.items():
        if agg is None or agg.empty:
            continue
        a = agg.copy()
        a["athlete_id"] = pd.to_numeric(a["player_id"], errors="coerce")
        a = a.dropna(subset=["athlete_id"]).groupby("athlete_id")[list(cols)].sum()
        j = a.join(season.add_prefix("pbp_"), how="left").fillna(0.0)
        rep[name] = {"players": len(j), "stats": {}}
        for src, dst in cols.items():
            dst = f"pbp_{dst}"
            tot_a, tot_p = float(j[src].sum()), float(j[dst].sum())
            rep[name]["stats"][src] = {"aggregate_total": round(tot_a, 1), "pbp_total": round(tot_p, 1),
                                       "total_diff_pct": round(100 * (tot_p - tot_a) / tot_a, 3) if tot_a else None,
                                       "player_exact_rate": round(float((j[src] == j[dst]).mean()), 4),
                                       "player_within_2pct": round(float(((j[src] - j[dst]).abs()
                                                                          <= 0.02 * j[src].abs().clip(lower=1)).mean()), 4)}
    return rep
