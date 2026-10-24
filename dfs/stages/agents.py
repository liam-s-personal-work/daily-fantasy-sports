"""Agent stages: build each stage's tasks, parse answers, and make every decision in code.

Agents supply facts and judgments; code verifies citations and decides. A strategist or
checker claim only counts when it cites fact ids that exist, are about that player (or his
team), and are of a kind that can change availability or role.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from dfs.io.contest_file import norm_name
from dfs.llm import prompts, schemas
from dfs.llm.backend import AgentResult, AgentTask
from dfs.models import Contest, Fact, Forecast, GameDossier, Lineup, Projection
from dfs.scoring import describe
from dfs.sources import host_blocked

# A fact says a player will not play: explicit phrases only (no bare "out", which also means
# "sat out practice" or "not ruled out"), and never when negated just before.
OUT_WORDS = re.compile(r"\b(ruled out|(?:listed|declared|designated|downgraded to|report(?: status)?)\s*:?\s*(?:as )?out\b|"
                       r"(?:is|are|will be) out\b|out for (?:the )?(?:game|season|week|sunday|monday|thursday|saturday)|"
                       r"inactive|injured reserve|placed on (?:ir|injured reserve|the reserve)|\bon ir\b|won't play|"
                       r"will not play|suspended|not expected to play|scratched)", re.I)
_NEGATED = re.compile(r"\b(not|no longer|n't|never|isn't|wasn't|avoid(?:ed)?|despite|sat|sitting|returned|"
                      r"activated|removed from)\b[\w\s,'-]{0,24}$", re.I)
OUT_STATUSES = {"out", "inactive", "injured_reserve", "suspended"}
AVAILABILITY_KINDS = {"injury", "status", "role", "depth_chart", "usage", "coaching"}


def says_out(fact: Fact) -> bool:
    """The fact states the player will not play. Web-research facts must say so in their status field
    (their prose often describes past games or returns from IR); structured facts (official injury
    report rows) may also be read from their fixed wording."""
    st = (getattr(fact, "status", None) or "").lower()
    if st:
        return st in OUT_STATUSES
    if ".F" in (fact.id or ""):
        return False
    for m in OUT_WORDS.finditer(fact.text or ""):
        if not _NEGATED.search((fact.text or "")[:m.start()]):
            return True
    return False


# ----------------------------------------------------------------------------- helpers

def key_name(player_key: str, contest: Contest | None = None) -> str:
    if contest is not None and player_key in contest.players:
        return norm_name(contest.players[player_key].name)
    parts = player_key.split("|")
    return parts[1] if len(parts) == 3 else norm_name(player_key)


def _key_team(player_key: str, contest: Contest | None) -> str | None:
    if contest is not None and player_key in contest.players:
        return contest.players[player_key].team.upper()
    return player_key.split("|")[0].upper() if "|" in player_key else None


_POS_WORDS = {"qb", "rb", "wr", "te", "k", "dst", "def", "fb", "pk"}


def _subject_name(subject: str) -> str:
    """'Josh Allen (QB, BUF)' / 'Josh Allen - QB' -> 'josh allen'."""
    s = re.sub(r"\(.*?\)|\s[-–]\s.*$", " ", subject or "")
    words = [w for w in norm_name(s).split() if w not in _POS_WORDS]
    return " ".join(words)


def same_team(a: str | None, b: str | None, sport: str | None = None) -> bool:
    """Team codes from different sources (nflverse 'LA' vs DK 'LAR', 'JAC' vs 'JAX') compare equal."""
    if not a or not b:
        return False
    a, b = a.strip().upper(), b.strip().upper()
    if a == b:
        return True
    if (sport or "nfl") == "nfl":
        from dfs.data.teams import canonicalize
        return canonicalize(a, strict=False) == canonicalize(b, strict=False)
    return False


def _team_names(team: str, contest: Contest | None) -> set[str]:
    names = {team.lower(), f"{team.lower()} dst", f"{team.lower()} defense", f"{team.lower()} offense"}
    if contest is not None:
        for k, p in contest.players.items():
            if k.endswith("|DST") and same_team(p.team, team, contest.rules.sport):
                n = norm_name(p.name)
                names |= {n, f"{n} dst", f"{n} defense", f"{n} d st", f"{n} offense"}
    return names


def fact_about(fact: Fact, player_key: str, contest: Contest | None = None) -> bool:
    """Is the fact's subject this player? The exact name, or the bare last name when the fact names his
    team and no teammate on the slate shares it. A team defense matches only facts about the team or
    its defense. The fact's text is never used: a teammate's name in it proves nothing."""
    subj = _subject_name(fact.subject)
    name = key_name(player_key, contest)
    team = _key_team(player_key, contest)
    sport = contest.rules.sport if contest is not None else "nfl"
    if fact.team and team and not same_team(fact.team, team, sport):
        return False
    if name == "dst" or player_key.endswith("|DST"):
        return bool(team and subj and subj in _team_names(team, contest))
    if not subj or not name:
        return False
    if subj == name:
        return True
    last = name.split()[-1]
    if fact.team and len(last) >= 3 and subj in (last, f"{name[0]} {last}"):
        mates = [k for k, p in (contest.players.items() if contest else [])
                 if same_team(p.team, team, sport) and key_name(k, contest).split()[-1:] == [last]]
        return len(mates) <= 1
    return False


def team_fact(fact: Fact, team: str | None, contest: Contest | None = None) -> bool:
    """A fact about a team as a whole (its offense, staff, game plan), not about one named player."""
    if not team or not same_team(fact.team, team, contest.rules.sport if contest else "nfl"):
        return False
    subj = _subject_name(fact.subject)
    return subj in _team_names(team, contest) or subj in {"game", "team", "offense", "coaching staff", "staff"}


def fact_index(dossiers: dict[str, GameDossier]) -> dict[str, Fact]:
    return {f.id: f for d in dossiers.values() for f in d.facts}


def facts_text(dossiers: dict[str, GameDossier], games: list[str] | None = None, max_chars: int = 60000) -> str:
    """Facts for the reviewers (strategist, checker, backup). Long slates are trimmed per game, never by
    cutting whole games off the end."""
    sel = [(g, d) for g, d in dossiers.items() if not games or g in games]
    per_game = max_chars // max(len(sel), 1)
    out = []
    for g, d in sel:
        block = [f"## {g}: {d.summary}"]
        if d.ruled_out:
            block.append(f"   ruled out: {', '.join(d.ruled_out)}")
        for f in d.facts:
            block.append(f"[{f.id}] ({f.kind}, {f.confidence}) {f.subject}: {f.text}"
                         + (f" <{f.source_url}>" if f.source_url else ""))
        text = "\n".join(block)
        out.append(text if len(text) <= per_game else text[:per_game] + "\n...(this game's facts trimmed)")
    return "\n".join(out)


def blinded_facts_text(dossiers: dict[str, GameDossier]) -> str:
    """Facts for the blinded forecaster: only fact ids, kind, subject and text that pass the blind.
    No research summary (it may repeat a line or a price) and no source URLs."""
    from dfs.sources import blind_text
    out = []
    for g, d in dossiers.items():
        # (no bare 'ruled out' list: an out must be shown by a fact, and the facts carry their status)
        rows = [f"[{f.id}] ({f.kind}, {f.confidence}{', ' + f.status if f.status and f.status != 'none' else ''}) "
                f"{f.subject}: {f.text}" for f in d.facts
                if f.kind != "line" and blind_text(f"{f.subject} {f.text}") is not None]
        out.append(f"## {g}\n" + "\n".join(rows))
    return "\n".join(out)


def _players_of_game(contest: Contest, game: str, keys: set[str] | None = None) -> list[str]:
    return sorted(k for k, p in contest.players.items() if p.game == game and (keys is None or k in keys))


# ----------------------------------------------------------------------------- research

def research_tasks(contest: Contest, known: dict[str, str], market: dict[str, str], *, model: str = "sonnet",
                   max_searches: int = 8, timeout_s: float = 900, now: datetime | None = None) -> list[AgentTask]:
    """One research agent per game. ``now`` (to the hour) is in the prompt: it tells the agent what is
    current, and a re-run hours later gets fresh research instead of the cached answer."""
    asof = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%d %H:00 UTC")
    sport = contest.rules.sport
    focus, checklist = prompts.RESEARCH_FOCUS.get(sport, prompts.RESEARCH_FOCUS["football"])
    known_source = {"nfl": "nflverse", "cfb": "sportsdataverse college data",
                    "nba": "ESPN box scores via sportsdataverse"}.get(sport, "the contest file")
    tasks = []
    for g in contest.games:
        players = [contest.players[k] for k in _players_of_game(contest, g)]
        kickoff = next((p.start_time.isoformat() for p in players if p.start_time), "kickoff time unknown")
        roster = "\n".join(f"- {p.name} | {p.team} | {'/'.join(p.positions)} | {p.injury_status or ''}"
                           for p in players)
        tasks.append(AgentTask(
            stage="research", name=f"research:{g}", model=model, tools=["WebSearch", "WebFetch"],
            system=prompts.RESEARCH_SYSTEM.format(max_searches=max_searches, focus=focus, checklist=checklist,
                                                  known_source=known_source),
            prompt=prompts.RESEARCH_PROMPT.format(game=g, kickoff=kickoff, now=asof,
                                                  known=known.get(g, "(none)"), roster=roster),
            schema=schemas.RESEARCH, max_turns=max_searches * 2 + 6, timeout_s=timeout_s))
    return tasks


AS_OF_SLACK = timedelta(minutes=30)  # agents stamp live reports with their own clock, seconds after the run's start


def _after(as_of: str | None, now: datetime | None) -> bool:
    """True when the fact's stated time (or date) is later than ``now`` (a past-slate run must not see the future).
    A time within ``AS_OF_SLACK`` of ``now`` counts as now: a live check's report is written while it runs."""
    if not as_of or now is None:
        return False
    try:
        t = datetime.fromisoformat(as_of.strip().replace("Z", "+00:00"))
        if t.tzinfo is not None:
            return t > now + AS_OF_SLACK
    except ValueError:
        pass
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", as_of)
    if not m:
        return False
    try:
        d = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
    except ValueError:
        return False
    return d > now.astimezone(timezone.utc).date()


def parse_research(results: list[AgentResult], now: datetime | None = None) -> dict[str, GameDossier]:
    out: dict[str, GameDossier] = {}
    for r in results:
        if not r.ok or not r.output:
            continue
        g = r.name.split(":", 1)[1]
        o = r.output
        facts = []
        for i, f in enumerate(o.get("facts", []), 1):
            if not re.match(r"https?://\S+", (f.get("source_url") or "").strip()):
                continue  # every fact must be cited with a URL
            if host_blocked(f.get("source_url")):
                continue  # never carry facts lifted from DFS sites or no-automation sources
            if _after(f.get("as_of"), now):
                continue  # dated after the run's time
            facts.append(Fact(id=f"{g}.F{i}", subject=f.get("subject", ""), team=f.get("team"), kind=f.get("kind", "other"),
                              text=f.get("text", ""), source_url=f.get("source_url"), as_of=f.get("as_of"),
                              confidence=f.get("confidence", "medium"), status=f.get("status")))
        out[g] = GameDossier(game=g, summary=o.get("summary", ""), facts=facts, ruled_out=o.get("ruled_out", []),
                             doubtful_or_questionable=o.get("doubtful_or_questionable", []), weather=o.get("weather"))
    return out


def structured_dossier(game: str, facts: list[Fact], ruled_out: list[str], summary: str = "") -> GameDossier:
    """Dossier built from structured data only (no web): used offline and in leakage-free backtests."""
    return GameDossier(game=game, summary=summary,
                       facts=[f.model_copy(update={"id": f.id or f"{game}.S{i}"}) for i, f in enumerate(facts, 1)],
                       ruled_out=ruled_out)


# ----------------------------------------------------------------------------- forecast

def forecast_tasks(contest: Contest, dossiers: dict[str, GameDossier], usage: dict[str, str],
                   market: dict[str, str], pool: set[str], *, model: str = "opus",
                   timeout_s: float = 900) -> list[AgentTask]:
    """One blinded forecaster per game: facts and usage only — no salaries, site averages or past points."""
    scoring = describe(contest.rules.scoring)
    tasks = []
    for g in contest.games:
        keys = _players_of_game(contest, g, pool)
        if not keys:
            continue
        lines = "\n".join(f"- {k} ({contest.players[k].name}, {contest.players[k].team} "
                          f"{'/'.join(contest.players[k].positions)})" for k in keys)
        d = {g: dossiers[g]} if g in dossiers else {}
        tasks.append(AgentTask(
            stage="forecast", name=f"forecast:{g}", model=model, tools=[],
            system=prompts.FORECAST_SYSTEM.format(scoring=scoring),
            prompt=prompts.FORECAST_PROMPT.format(game=g, market=market.get(g, "unknown"),
                                                  facts=blinded_facts_text(d) or "(no research facts)",
                                                  usage=usage.get(g, "(none)"), players=lines),
            schema=schemas.FORECAST, max_turns=4, timeout_s=timeout_s))
    return tasks


def parse_forecasts(results: list[AgentResult], valid_keys: set[str]) -> tuple[dict[str, Forecast], list[str]]:
    out: dict[str, Forecast] = {}
    problems: list[str] = []
    for r in results:
        if not r.ok or not r.output:
            problems.append(f"{r.name}: {r.error}")
            continue
        for p in r.output.get("players", []):
            k = p.get("player_key")
            if k not in valid_keys:  # a truncated key ('IND|dst' for 'IND|dst|DST') is accepted if unambiguous
                hits = [v for v in valid_keys if isinstance(k, str) and v.lower().startswith(k.lower().rstrip("|") + "|")]
                if len(hits) != 1:
                    problems.append(f"{r.name}: unknown player_key {k!r}")
                    continue
                k = hits[0]
            lo, mid, hi = sorted([float(p["low"]), float(p["likely"]), float(p["high"])])
            out[k] = Forecast(player_key=k, low=lo, likely=mid, high=hi,
                              p_dnp=min(max(float(p.get("p_dnp", 0.0)), 0.0), 1.0),
                              rationale=p.get("rationale", "") + (f" | {p['stat_line']}" if p.get("stat_line") else ""))
    return out, problems


# ----------------------------------------------------------------------------- review

def lineup_text(contest: Contest, lu: Lineup, projections: dict | None = None) -> str:
    rows = []
    for s in lu.slots:
        p = contest.players[s.player_key]
        pr = projections.get(s.player_key) if projections else None
        extra = f" | mean {pr.mean:.1f} p90 {pr.p90:.1f}" if pr else ""
        rows.append(f"- {s.slot}: {s.player_key} ({p.name}, {p.team} vs {p.opponent}) ${s.salary}{extra}")
    return "\n".join(rows) + f"\nsalary {lu.salary}, mean {lu.mean:.1f}" + \
        "".join(f", {k} {v:.3g}" for k, v in lu.metrics.items() if k != "optimal")


def _games_of(contest: Contest, lineups: list[Lineup]) -> list[str]:
    return sorted({contest.players[s.player_key].game for lu in lineups for s in lu.slots
                   if contest.players[s.player_key].game})


def strategist_task(contest: Contest, drafts: list[Lineup], top3: str, dossiers: dict[str, GameDossier],
                    projections: dict, objective: str, *, model: str = "opus", backup: bool = False,
                    timeout_s: float = 900) -> AgentTask:
    r = contest.rules
    system = (prompts.BACKUP_SYSTEM if backup else prompts.STRATEGIST_SYSTEM.format(
        site=r.site.value, sport=r.sport.upper(), game_type=r.game_type, kind=contest.kind.value))
    prompt = prompts.STRATEGIST_PROMPT.format(
        contest=f"{r.site.value} {r.sport} {r.game_type}, cap {r.salary_cap}, {contest.kind.value}",
        objective=objective,
        drafts="\n\n".join(f"### {d.label}\n{lineup_text(contest, d, projections)}" for d in drafts),
        top3=top3, facts=facts_text(dossiers))
    return AgentTask(stage="backup" if backup else "strategist", name="backup" if backup else "strategist",
                     model=model, tools=[], system=system, prompt=prompt,
                     schema=schemas.BACKUP if backup else schemas.STRATEGIST, max_turns=4, timeout_s=timeout_s)


def _verified(fact_ids: list[str], idx: dict[str, Fact], key: str, contest: Contest,
              kinds: set[str] = AVAILABILITY_KINDS) -> list[Fact]:
    team = contest.players[key].team if key in contest.players else None
    good = []
    for fid in fact_ids or []:
        f = idx.get(fid)
        if f is None or f.kind not in kinds or f.confidence == "low":
            continue
        if fact_about(f, key, contest) or (f.kind in ("role", "depth_chart", "coaching") and team_fact(f, team, contest)):
            good.append(f)
    return good


def _names_player(f: Fact, key: str, contest: Contest) -> bool:
    """For inactive lists: a fact whose subject lists several players ('A / B / C') or the team, and
    whose text names this player in full."""
    if (f.status or "").lower() not in OUT_STATUSES:
        return False
    n = key_name(key, contest)
    in_subject = n in {_subject_name(x) for x in re.split(r"\s*[/,&]\s*", f.subject or "")}
    if not in_subject and not team_fact(f, contest.players[key].team, contest):
        return False
    # The clause that names him decides: it must not give him a playing status, and either it says he
    # is out or the whole fact is an inactives list (every name on it is out).
    clause = next((cl for cl in re.split(r"[;.\n]", f.text or "") if re.search(rf"\b{re.escape(n)}\b", norm_name(cl))), None)
    if clause is None:
        return in_subject and bool(re.search(r"\binactives?\b", f.text or "", re.I))
    if re.search(r"questionable|doubtful|probable|limited|game[- ]time|full participant|will play|expected to play|"
                 r"(?<!in)active\b", clause, re.I):
        return False
    return bool(OUT_WORDS.search(clause) or re.search(r"\bout\b|\binactives?\b", clause, re.I)
                or re.search(r"\binactives?\b", f.text or "", re.I))


def ruled_out_keys(contest: Contest, dossiers: dict[str, GameDossier]) -> set[str]:
    """Players a dossier lists as ruled out AND proves with one of its own facts (an availability fact
    about him, not low-confidence, that says he will not play). A bare name in the list removes nobody:
    it could be a hallucination or text injected by a web page."""
    out = set()
    for d in dossiers.values():
        names = {_subject_name(n) for n in d.ruled_out}  # 'Josh Allen (concussion)' -> 'josh allen'
        for k in _players_of_game(contest, d.game):
            if key_name(k, contest) in names and any(
                    f.kind in ("injury", "status") and f.confidence != "low" and not host_blocked(f.source_url)
                    and (fact_about(f, k, contest) or _names_player(f, k, contest)) and says_out(f) for f in d.facts):
                out.add(k)
    return out


# Chance of not playing for a cited, non-final availability status (out/inactive are removed elsewhere).
STATUS_P_ZERO = {"doubtful": 0.75, "questionable": 0.3, "probable": 0.05, "active": 0.0}


def apply_status(contest: Contest, dossiers: dict[str, GameDossier],
                 baseline: dict[str, Projection]) -> tuple[dict[str, Projection], list[dict]]:
    """Code decides p_zero from the latest cited availability fact about each player, for sports without
    the forecaster (it prices these facts itself). Only the chance of not playing changes; the
    distribution given that the player plays is kept. Low-confidence and blocked-host facts never count."""
    out = dict(baseline)
    changes = []
    for k, pr in baseline.items():
        d = dossiers.get(contest.players[k].game or "")
        if d is None:
            continue
        facts = [f for f in d.facts if f.kind in ("injury", "status") and f.confidence != "low"
                 and (f.status or "").lower() in STATUS_P_ZERO and not host_blocked(f.source_url)
                 and fact_about(f, k, contest)]
        if not facts:
            continue
        f = max(facts, key=lambda f: (f.as_of or "", f.confidence == "high"))  # the latest (ISO dates sort)
        # a provider's floor holds: "probable" says a healthy scratch is available, not that he will play
        pz = max(STATUS_P_ZERO[f.status.lower()], pr.components.get("p_zero_floor", 0.0))
        if abs(pz - pr.p_zero) < 1e-9 or pr.p_zero >= 1:
            continue
        given_play = pr.mean / (1 - pr.p_zero)
        out[k] = pr.model_copy(update={"p_zero": pz, "mean": round(given_play * (1 - pz), 3)})
        changes.append({"player": k, "status": f.status, "fact": f.id, "p_zero": [pr.p_zero, pz],
                        "mean": [pr.mean, out[k].mean]})
    return out, changes


def _hard_out(facts: list[Fact]) -> bool:
    return any(f.kind in ("injury", "status") and says_out(f) for f in facts)


def apply_strategist(output: dict, idx: dict[str, Fact], contest: Contest,
                     dossiers: dict[str, GameDossier], priced: set[str] = frozenset(),
                     forecasted: set[str] | None = None) -> tuple[set[str], dict]:
    """Return (keys to exclude, report). Only fact-backed removals and cuts are accepted.

    Novelty rule: a cut needs a fact the forecaster did NOT already price in (``priced``), unless
    the fact is a hard availability one (out / inactive / IR / suspended). Otherwise the same
    information would count twice.
    """
    excluded: set[str] = set()
    report = {"accepted": [], "rejected": [], "weakest_pick": output.get("weakest_pick"),
              "close_calls": output.get("close_calls", []), "preferred_draft": output.get("preferred_draft")}
    out_keys = ruled_out_keys(contest, dossiers)
    for item in output.get("remove_not_playing", []):
        k = item.get("player_key")
        facts = _verified(item.get("fact_ids", []), {i: f for i, f in idx.items()}, k, contest, {"injury", "status"})
        if k in contest.players and (k in out_keys or any(says_out(f) for f in facts)):
            excluded.add(k)
            report["accepted"].append({"remove": k, "facts": [f.id for f in facts]})
        else:
            report["rejected"].append({"remove": k, "why": "no verified not-playing fact"})
    for item in output.get("cuts", []):
        k = item.get("player_key")
        facts = _verified(item.get("fact_ids", []), idx, k, contest) if k in contest.players else []
        # a fact is "priced in" only for a player the forecaster actually forecast
        new = facts if forecasted is not None and k not in forecasted else [f for f in facts if f.id not in priced]
        if facts and (new or _hard_out(facts)):
            excluded.add(k)
            report["accepted"].append({"cut": k, "facts": [f.id for f in facts], "reason": item.get("reason")})
        elif facts:
            report["rejected"].append({"cut": k, "why": "facts already priced in by the forecaster (no new information)"})
        else:
            report["rejected"].append({"cut": k, "why": "cited facts missing, low-confidence or not about the player"})
    return excluded, report


def checker_task(contest: Contest, lu: Lineup, dossiers: dict[str, GameDossier], projections: dict,
                 *, model: str = "opus", attempt: int = 0, timeout_s: float = 900) -> AgentTask:
    r = contest.rules
    return AgentTask(stage="checker", name=f"checker:{attempt}", model=model, tools=[],
                     system=prompts.CHECKER_SYSTEM,
                     prompt=prompts.CHECKER_PROMPT.format(
                         contest=f"{r.site.value} {r.sport} {r.game_type} ({contest.kind.value})",
                         lineup=lineup_text(contest, lu, projections),
                         facts=facts_text(dossiers, games=_games_of(contest, [lu]))),
                     schema=schemas.CHECKER, max_turns=4, timeout_s=timeout_s)


def checker_verdict(output: dict, idx: dict[str, Fact], contest: Contest, lu: Lineup,
                    priced: set[str] = frozenset(), forecasted: set[str] | None = None) -> tuple[bool, set[str], dict]:
    """Code decides: fail only on a fact-backed blocker about availability or role. Role claims
    need a fact the forecaster had not priced in; hard outs always count."""
    fails: set[str] = set()
    notes = []
    picks = set(lu.player_keys)
    for a in output.get("attacks", []):
        k = a.get("player_key")
        if k not in picks:
            continue
        backed = _verified(a.get("fact_ids", []), idx, k, contest)
        claim = a.get("claim")
        new = backed if forecasted is not None and k not in forecasted else [f for f in backed if f.id not in priced]
        decisive = a.get("severity") == "blocker" and bool(backed) and (
            (claim == "not_playing" and (_hard_out(backed) or bool(new))) or (claim == "limited_or_role_cut" and bool(new)))
        if decisive:
            fails.add(k)
        notes.append({"player": k, "claim": a.get("claim"), "severity": a.get("severity"),
                      "backed_by": [f.id for f in backed], "decisive": bool(decisive), "argument": a.get("argument")})
    return not fails, fails, {"attacks": notes}


def apply_backup(output: dict, idx: dict[str, Fact], contest: Contest, points: dict[str, float]) -> tuple[dict[str, float], set[str], dict]:
    new = dict(points)
    removed, applied = set(), []
    for a in output.get("adjustments", []):
        k = a.get("player_key")
        if k not in new:
            continue
        facts = _verified(a.get("fact_ids", []), idx, k, contest,
                          AVAILABILITY_KINDS | {"matchup", "weather", "line"})
        if not facts:
            continue
        m = min(max(float(a.get("multiplier", 1.0)), 0.5), 1.3)
        new[k] *= m
        applied.append({"player": k, "multiplier": m, "facts": [f.id for f in facts], "reason": a.get("reason")})
    rejected = []
    for item in output.get("remove", []):  # a removal needs a verified not-playing fact, like the strategist's
        k = item.get("player_key") if isinstance(item, dict) else item
        facts = _verified(item.get("fact_ids", []) if isinstance(item, dict) else [], idx, k, contest,
                          {"injury", "status"}) if k in new else []
        if k in new and any(says_out(f) for f in facts):
            removed.add(k)
        elif k:
            rejected.append({"remove": k, "why": "no verified not-playing fact"})
    return new, removed, {"applied": applied, "removed": sorted(removed), "rejected": rejected,
                          "notes": output.get("notes")}


# ----------------------------------------------------------------------------- last hour

def news_tasks(contest: Contest, keys: list[str], now: datetime, *, model: str = "sonnet",
               max_searches: int = 6) -> list[AgentTask]:
    by_game: dict[str, list[str]] = defaultdict(list)
    for k in keys:
        by_game[contest.players[k].game or "?"].append(k)
    tasks = []
    for g, ks in sorted(by_game.items()):
        lines = "\n".join(f"- {contest.players[k].name} | {contest.players[k].team} | "
                          f"{'/'.join(contest.players[k].positions)} | {g} | "
                          f"{contest.players[k].start_time.isoformat() if contest.players[k].start_time else ''}"
                          for k in ks)
        tasks.append(AgentTask(stage="news", name=f"news:{g}", model=model, tools=["WebSearch", "WebFetch"],
                               system=prompts.NEWS_SYSTEM.format(max_searches=max_searches),
                               prompt=prompts.NEWS_PROMPT.format(now=now.isoformat(), players=lines),
                               schema=schemas.NEWS, max_turns=max_searches * 2 + 4, timeout_s=600,
                               cacheable=False))
    return tasks


def parse_news(results: list[AgentResult], contest: Contest, keys: list[str],
               now: datetime | None = None) -> dict[str, dict]:
    """player_key -> latest update dict (only for players named in the update)."""
    by_name = {key_name(k, contest): k for k in keys}
    out: dict[str, dict] = {}
    for r in results:
        if not r.ok or not r.output:
            continue
        for u in r.output.get("updates", []):
            k = by_name.get(_subject_name(u.get("player", "")))
            if not k or host_blocked(u.get("source_url")) or _after(u.get("as_of"), now):
                continue  # never act on news from DFS, betting or no-automation sites, or dated after now
            if u.get("status") in ("inactive", "out") and not u.get("source_url"):
                u = {**u, "status": "questionable", "text": f"(unsourced 'out' downgraded) {u.get('text', '')}"}
            out[k] = u
    return out


def dump(obj) -> str:
    return json.dumps(obj, indent=1, default=str)
