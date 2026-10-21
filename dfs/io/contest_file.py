"""Read any DraftKings or FanDuel contest CSV into a ``Contest``.

Handles, by sniffing the file itself (never the file name):
- ``dk_salaries``  DK player export ("Position,Name + ID,Name,ID,Roster Position,...")
- ``dk_entries``   DK "Edit Entries" download: entries on the left, player pool embedded on the right
- ``dk_template``  DK blank lineup-upload template (slot header + embedded pool)
- ``fd_players``   FD "Download players list" (classic or single game with "MVP 1.5x Salary")
- ``fd_entries``   FD entries template (entry_id, contest_id, contest_name, entry_fee, slots...)

Site, sport and game type are detected from headers, positions, roster-position tokens
and team abbreviations; the salary cap and structural rules come from the saved registry.
"""

from __future__ import annotations

import csv
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dfs.models import Candidate, Contest, ContestKind, ContestRules, Entry, Player, Site, Slot
from dfs.rules import NFL_TEAMS, POSITION_SPORT, load_rules

ET = ZoneInfo("America/New_York")
DK_POOL_HEADER = ["Position", "Name + ID", "Name", "ID", "Roster Position", "Salary", "Game Info", "TeamAbbrev"]
CASH_WORDS = ("double up", "double-up", "50/50", "head-to-head", "h2h", "triple up", "quintuple up", "cash")
SLOT_ALIASES = {"SUPER FLEX": "SUPERFLEX", "S-FLEX": "SUPERFLEX", "SFLEX": "SUPERFLEX", "SUPER-FLEX": "SUPERFLEX",
                "ANYFLEX": "FLEX", "D/ST": "DST", "D": "D"}
_SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b\.?$")


class ContestFileError(ValueError):
    pass


def norm_name(name: str) -> str:
    """Lowercase ASCII name without punctuation or generational suffix: "D.J. Moore Jr." -> "dj moore"."""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower().strip()
    s = re.sub(r"[.'`’]", "", s)
    s = re.sub(r"[-_,]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    prev = None
    while prev != s:
        prev, s = s, _SUFFIX.sub("", s).strip()
    return s


def player_key(team: str, name: str, position: str) -> str:
    pos = position.upper()
    if pos in ("DST", "D", "DEF"):
        return f"{team.upper()}|dst|DST"
    return f"{team.upper()}|{norm_name(name)}|{pos}"


def read_rows(path: str | Path) -> list[list[str]]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return [list(r) for r in csv.reader(f)]


def _find(rows: list[list[str]], first: str, second: str, max_rows: int = 40) -> tuple[int, int] | None:
    for r, row in enumerate(rows[:max_rows]):
        for c in range(len(row) - 1):
            if row[c].strip() == first and row[c + 1].strip() == second:
                return r, c
    return None


def _norm_header(h: str) -> str:
    return re.sub(r"[\s_]+", "_", h.strip().lower())


def detect_kind(rows: list[list[str]]) -> str:
    if not rows or not rows[0]:
        raise ContestFileError("empty file")
    head = [h.strip() for h in rows[0]]
    if head[0] == "Entry ID":
        return "dk_entries"
    if head[0] == "Position" and "Name + ID" in head:
        return "dk_salaries"
    if head[0] == "Id" and "Nickname" in head:
        return "fd_players"
    if [_norm_header(h) for h in head[:4]] == ["entry_id", "contest_id", "contest_name", "entry_fee"]:
        return "fd_entries"
    if "Instructions" in head and _find(rows, "Position", "Name + ID"):
        return "dk_template"
    raise ContestFileError(f"unrecognised contest file header: {head[:6]}")


def _slot_header(row: list[str], start: int) -> list[str]:
    out = []
    for cell in row[start:]:
        if not cell.strip():
            break
        out.append(cell.strip())
    return out


def _col(header: list[str]) -> dict[str, int]:
    idx: dict[str, int] = {}
    for i, h in enumerate(header):
        h = h.strip()
        if h and h not in idx:
            idx[h] = i
    return idx


def _parse_game_info(info: str) -> tuple[str | None, datetime | None]:
    """'BUF@MIA 10/26/2026 01:00PM ET' -> ('BUF@MIA', aware datetime); tournaments -> (None, None)."""
    m = re.match(r"^\s*([A-Za-z0-9&.]+@[A-Za-z0-9&.]+)\s+(\d{2}/\d{2}/\d{4}\s+\d{1,2}:\d{2}[AP]M)", info or "")
    if m:
        return m.group(1).upper(), datetime.strptime(m.group(2), "%m/%d/%Y %I:%M%p").replace(tzinfo=ET)
    m = re.match(r"^\s*([A-Za-z0-9&.]+@[A-Za-z0-9&.]+)\s*$", info or "")
    if m:
        return m.group(1).upper(), None
    return None, None


def _float(s: str) -> float | None:
    try:
        return float(s) if s not in ("", None) else None
    except ValueError:
        return None


def _detect_sport(positions: set[str], teams: set[str], site: Site, has_cpt: bool) -> str:
    for marker, sport in POSITION_SPORT:
        if positions & marker:
            return sport
    if teams == {"GOLF"} or positions == {"G"}:
        return "pga"
    if positions <= {"QB", "RB", "WR", "TE", "DST", "D", "K", "FB"}:
        return "nfl" if teams and teams <= NFL_TEAMS else "cfb"
    if positions <= {"C", "W", "D", "G"}:
        return "nhl"
    if positions == {"F"}:
        return "mma"
    if positions == {"D"} and site == Site.DRAFTKINGS:
        return "nascar"
    if positions == {"P"}:
        return "cs2" if has_cpt else "tennis"
    raise ContestFileError(f"cannot tell the sport from positions {sorted(positions)}; pass sport= explicitly")


def _canon_slot(label: str) -> str:
    up = label.strip().upper()
    if up.startswith("MVP"):
        return "MVP"
    if up.startswith("CPT") or up.startswith("CAPTAIN"):
        return "CPT"
    return SLOT_ALIASES.get(up, label.strip())


def _contest_kind(names: list[str]) -> ContestKind:
    """Cash only when every entered contest's name says so; otherwise treat as a tournament."""
    if names and all(any(w in n.lower() for w in CASH_WORDS) for n in names):
        return ContestKind.CASH
    return ContestKind.GPP


def load_contest(path: str | Path, *, sport: str | None = None, game_type: str | None = None,
                 rules: ContestRules | None = None) -> Contest:
    """Parse ``path`` and attach the saved rules. Raises ContestFileError on anything ambiguous."""
    rows = read_rows(path)
    kind = detect_kind(rows)
    site = Site.DRAFTKINGS if kind.startswith("dk") else Site.FANDUEL
    warnings: list[str] = []

    file_slots: list[str] | None = None
    entries: list[Entry] = []
    if kind == "dk_salaries":
        pool_r, pool_c = 0, 0
    elif kind in ("dk_entries", "dk_template"):
        file_slots = _slot_header(rows[0], 4 if kind == "dk_entries" else 0)
        found = _find(rows, "Position", "Name + ID")
        if not found:
            raise ContestFileError("DK entries file without an embedded player pool")
        pool_r, pool_c = found
    elif kind == "fd_players":
        pool_r, pool_c = 0, 0
    else:  # fd_entries
        file_slots = _slot_header(rows[0], 4)
        found = _find(rows, "Id", "Position", max_rows=60)
        if not found:
            raise ContestFileError("FD entries template without an embedded player pool")
        pool_r, pool_c = found

    header = [h.strip() for h in rows[pool_r][pool_c:]]
    col = _col(header)
    body = [r[pool_c:] for r in rows[pool_r + 1:] if len(r) > pool_c and any(x.strip() for x in r[pool_c:])]

    players: dict[str, Player] = {}
    raw: list[dict] = []
    if site == Site.DRAFTKINGS:
        missing = [h for h in DK_POOL_HEADER if h not in col]
        if missing:
            raise ContestFileError(f"DK pool missing columns {missing}")
        for r in body:
            get = lambda h: r[col[h]].strip() if col.get(h) is not None and col[h] < len(r) else ""
            if not get("ID"):
                continue
            positions = [p for p in get("Position").split("/") if p]
            game, start = _parse_game_info(get("Game Info"))
            team = get("TeamAbbrev").upper()
            opp = None
            if game:
                a, h = game.split("@")
                opp = h if team == a else a if team == h else None
            raw.append(dict(id=get("ID"), name=get("Name"), display=get("Name + ID"), positions=positions,
                            roster=[t for t in get("Roster Position").split("/") if t], salary=int(float(get("Salary"))),
                            team=team, opp=opp, game=game, start=start, avg=_float(get("AvgPointsPerGame")),
                            status=get("Status") or None, detail=None, mvp_salary=None))
    else:
        need = ["Id", "Position", "Nickname", "Salary", "Game", "Team", "Opponent", "Roster Position"]
        missing = [h for h in need if h not in col]
        if missing:
            raise ContestFileError(f"FD player list missing columns {missing}")
        for r in body:
            get = lambda h: r[col[h]].strip() if col.get(h) is not None and col[h] < len(r) else ""
            if not re.match(r"^\d+-\d+$", get("Id")):
                continue
            game, _ = _parse_game_info(get("Game"))
            raw.append(dict(id=get("Id"), name=get("Nickname"), display=f"{get('Id')}:{get('Nickname')}",
                            positions=[p for p in get("Position").split("/") if p], roster=get("Roster Position"),
                            salary=int(float(get("Salary"))), team=get("Team").upper(), opp=get("Opponent").upper() or None,
                            game=game, start=None, avg=_float(get("FPPG")), status=get("Injury Indicator") or None,
                            detail=get("Injury Details") or None,
                            mvp_salary=int(float(get("MVP 1.5x Salary"))) if get("MVP 1.5x Salary") else None))
    if not raw:
        raise ContestFileError("no player rows found")

    has_cpt = any("CPT" in (x["roster"] if isinstance(x["roster"], list) else [x["roster"]]) for x in raw)
    has_mvp = any(isinstance(x["roster"], str) and x["roster"].upper().startswith("MVP") for x in raw)
    all_pos = {p.upper() for x in raw for p in x["positions"]}
    teams = {x["team"] for x in raw if x["team"]}
    sport = sport or _detect_sport(all_pos, teams, site, has_cpt)
    games = sorted({x["game"] for x in raw if x["game"]})
    if game_type is None:
        if has_cpt:
            game_type = "showdown"
        elif has_mvp:
            game_type = "single_game"
        else:
            game_type = "classic"
    rules = rules or load_rules(site, sport, game_type)

    # Candidates: one per (row, slot-family). DK CPT rows and FD MVP pricing become their own candidates.
    cands: list[Candidate] = []
    for x in raw:
        key = player_key(x["team"], x["name"], x["positions"][0] if x["positions"] else "?")
        if key not in players:
            players[key] = Player(key=key, name=x["name"].strip(), team=x["team"], opponent=x["opp"],
                                  positions=[p.upper() for p in x["positions"]], game=x["game"],
                                  start_time=x["start"], site_avg=x["avg"], injury_status=x["status"],
                                  injury_detail=x["detail"])
        if site == Site.DRAFTKINGS:
            elig = [_canon_slot(t) for t in x["roster"]]
            cands.append(Candidate(player_key=key, site_id=x["id"], salary=x["salary"], eligible_slots=elig,
                                   display=x["display"]))
        else:
            tokens = [t.strip() for t in x["roster"].split("/") if t.strip()]
            mvp = [t for t in tokens if t.upper().startswith("MVP")]
            rest = [_canon_slot(t) for t in tokens if not t.upper().startswith("MVP")]
            if rest:
                cands.append(Candidate(player_key=key, site_id=x["id"], salary=x["salary"], eligible_slots=rest,
                                       display=x["display"]))
            if mvp:
                sal = x["mvp_salary"]
                if sal is None:
                    sal = int(round(x["salary"] * 1.5))
                    warnings.append("MVP salary column missing; assumed 1.5x Salary")
                cands.append(Candidate(player_key=key, site_id=x["id"], salary=sal, eligible_slots=["MVP"],
                                       display=x["display"]))

    # Slots: the entries header (if any) is the truth for labels and order.
    slot_headers = [s.name for s in rules.slots]
    if file_slots:
        canon = [_canon_slot(s) for s in file_slots]
        by_name = {s.name: s for s in rules.slots}
        if canon != [s.name for s in rules.slots]:
            warnings.append(f"file slots {file_slots} differ from saved rules {[s.name for s in rules.slots]}; using the file")
            rules = rules.model_copy(update={"slots": [by_name.get(c, Slot(name=c, eligible=[c])) for c in canon]})
        slot_headers = file_slots

    # Entries
    if kind in ("dk_entries", "fd_entries"):
        n = len(slot_headers)
        for r in rows[1:]:
            if not r or not r[0].strip().isdigit():
                continue
            if kind == "dk_entries":
                eid, cname, cid, fee = r[0].strip(), r[1].strip(), r[2].strip(), r[3]
            else:
                eid, cid, cname, fee = r[0].strip(), r[1].strip(), r[2].strip(), r[3]
            current: list[str | None] = []
            locked: list[bool] = []
            for cell in (r[4:4 + n] + [""] * n)[:n]:
                cell = cell.strip()
                m = re.search(r"\((\d+)\)\s*(\(LOCKED\))?\s*$", cell)
                if m:
                    current.append(m.group(1)); locked.append(bool(m.group(2)))
                elif re.match(r"^\d+(-\d+)?(:.*)?$", cell):
                    current.append(cell.split(":")[0]); locked.append(False)
                else:
                    current.append(None); locked.append(False)
            entries.append(Entry(entry_id=eid, contest_id=cid, contest_name=cname, entry_fee=fee,
                                 current=current, locked=locked))

    # Consistency checks the user must see.
    if rules.multiplier_rows:
        flex_sal = {c.player_key: c.salary for c in cands if "CPT" not in c.eligible_slots}
        bad = [c.player_key for c in cands if "CPT" in c.eligible_slots and c.player_key in flex_sal
               and abs(c.salary - 1.5 * flex_sal[c.player_key]) > 100]
        if bad:
            warnings.append(f"{len(bad)} CPT rows are not priced at 1.5x their FLEX row")
    if rules.game_type in ("showdown", "single_game") and len(games) > 1:
        warnings.append(f"{rules.game_type} file spans {len(games)} games")
    if rules.confidence != "high":
        warnings.append(f"saved rules for {site.value}.{sport}.{game_type} are {rules.confidence}-confidence; "
                        f"confirm cap {rules.salary_cap} and slots against the contest's rules page")

    names = sorted({e.contest_name for e in entries})
    return Contest(rules=rules, players=players, candidates=cands, entries=entries, slot_headers=slot_headers,
                   source_file=str(path), file_kind=kind, kind=_contest_kind(names), games=games,
                   warnings=warnings)
