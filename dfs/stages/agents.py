"""Agent stages: build each stage's tasks, parse answers, and make every decision in code.

Agents supply facts and judgments; code verifies citations and decides. A strategist or
checker claim only counts when it cites fact ids that exist, are about that player (or his
team), and are of a kind that can change availability or role.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime

from dfs.io.contest_file import norm_name
from dfs.llm import prompts, schemas
from dfs.llm.backend import AgentResult, AgentTask
from dfs.models import Contest, Fact, Forecast, GameDossier, Lineup
from dfs.scoring import describe
from dfs.sources import host_blocked

OUT_WORDS = re.compile(r"\b(out|inactive|ruled out|injured reserve|\bIR\b|won't play|will not play|suspended|"
                       r"placed on|doubtful|not expected to play|scratch)\b", re.I)
AVAILABILITY_KINDS = {"injury", "status", "role", "depth_chart", "usage", "coaching"}


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


def fact_about(fact: Fact, player_key: str, contest: Contest | None = None) -> bool:
    """Does the fact name this player (full name, or last name + team)? DSTs match their team."""
    subj = norm_name(fact.subject)
    name = key_name(player_key, contest)
    team = _key_team(player_key, contest)
    if name == "dst" or player_key.endswith("|DST"):
        return bool(team and ((fact.team or "").upper() == team or subj == team.lower()))
    if not subj or not name:
        return False
    if subj == name or (len(subj) >= 5 and (name in subj or subj in name)):
        return True
    last = name.split()[-1]
    text = norm_name(fact.text)
    return bool(team and fact.team and fact.team.upper() == team and len(last) >= 3
                and (last in subj.split() or f" {last} " in f" {text} "))


def fact_index(dossiers: dict[str, GameDossier]) -> dict[str, Fact]:
    return {f.id: f for d in dossiers.values() for f in d.facts}


def facts_text(dossiers: dict[str, GameDossier], games: list[str] | None = None, max_chars: int = 40000) -> str:
    out = []
    for g, d in dossiers.items():
        if games and g not in games:
            continue
        out.append(f"## {g}: {d.summary}")
        if d.ruled_out:
            out.append(f"   ruled out: {', '.join(d.ruled_out)}")
        for f in d.facts:
            out.append(f"[{f.id}] ({f.kind}, {f.confidence}) {f.subject}: {f.text}"
                       + (f" <{f.source_url}>" if f.source_url else ""))
    text = "\n".join(out)
    return text if len(text) <= max_chars else text[:max_chars] + "\n...(truncated)"


def _players_of_game(contest: Contest, game: str, keys: set[str] | None = None) -> list[str]:
    return sorted(k for k, p in contest.players.items() if p.game == game and (keys is None or k in keys))


# ----------------------------------------------------------------------------- research

def research_tasks(contest: Contest, known: dict[str, str], market: dict[str, str], *, model: str = "sonnet",
                   max_searches: int = 8, timeout_s: float = 900) -> list[AgentTask]:
    tasks = []
    for g in contest.games:
        players = [contest.players[k] for k in _players_of_game(contest, g)]
        kickoff = next((p.start_time.isoformat() for p in players if p.start_time), "kickoff time unknown")
        roster = "\n".join(f"- {p.name} | {p.team} | {'/'.join(p.positions)} | {p.injury_status or ''}"
                           for p in players)
        tasks.append(AgentTask(
            stage="research", name=f"research:{g}", model=model, tools=["WebSearch", "WebFetch"],
            system=prompts.RESEARCH_SYSTEM.format(max_searches=max_searches),
            prompt=prompts.RESEARCH_PROMPT.format(game=g, kickoff=kickoff, market=market.get(g, "unknown"),
                                                  known=known.get(g, "(none)"), roster=roster),
            schema=schemas.RESEARCH, max_turns=max_searches * 2 + 6, timeout_s=timeout_s))
    return tasks


def parse_research(results: list[AgentResult]) -> dict[str, GameDossier]:
    out: dict[str, GameDossier] = {}
    for r in results:
        if not r.ok or not r.output:
            continue
        g = r.name.split(":", 1)[1]
        o = r.output
        facts = []
        for i, f in enumerate(o.get("facts", []), 1):
            if host_blocked(f.get("source_url")):
                continue  # never carry facts lifted from DFS sites or no-automation sources
            facts.append(Fact(id=f"{g}.F{i}", subject=f.get("subject", ""), team=f.get("team"), kind=f.get("kind", "other"),
                              text=f.get("text", ""), source_url=f.get("source_url"), as_of=f.get("as_of"),
                              confidence=f.get("confidence", "medium")))
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
                                                  facts=facts_text(d) or "(no research facts)",
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
            if k not in valid_keys:
                problems.append(f"{r.name}: unknown player_key {k!r}")
                continue
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


def strategist_task(contest: Contest, drafts: list[Lineup], top3: str, dossiers: dict[str, GameDossier],
                    projections: dict, objective: str, *, model: str = "opus", backup: bool = False) -> AgentTask:
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
                     schema=schemas.BACKUP if backup else schemas.STRATEGIST, max_turns=4, timeout_s=900)


def _verified(fact_ids: list[str], idx: dict[str, Fact], key: str, contest: Contest,
              kinds: set[str] = AVAILABILITY_KINDS) -> list[Fact]:
    team = contest.players[key].team if key in contest.players else None
    good = []
    for fid in fact_ids or []:
        f = idx.get(fid)
        if f is None or f.kind not in kinds or f.confidence == "low":
            continue
        if fact_about(f, key, contest) or (team and f.team and f.team.upper() == team.upper()
                                            and f.kind in ("role", "depth_chart", "coaching")):
            good.append(f)
    return good


def ruled_out_keys(contest: Contest, dossiers: dict[str, GameDossier]) -> set[str]:
    out = set()
    for d in dossiers.values():
        names = {norm_name(n) for n in d.ruled_out}
        for k in _players_of_game(contest, d.game):
            if key_name(k, contest) in names:
                out.add(k)
    return out


def apply_strategist(output: dict, idx: dict[str, Fact], contest: Contest,
                     dossiers: dict[str, GameDossier]) -> tuple[set[str], dict]:
    """Return (keys to exclude, report). Only fact-backed removals and cuts are accepted."""
    excluded: set[str] = set()
    report = {"accepted": [], "rejected": [], "weakest_pick": output.get("weakest_pick"),
              "close_calls": output.get("close_calls", []), "preferred_draft": output.get("preferred_draft")}
    out_keys = ruled_out_keys(contest, dossiers)
    for item in output.get("remove_not_playing", []):
        k = item.get("player_key")
        facts = _verified(item.get("fact_ids", []), {i: f for i, f in idx.items()}, k, contest, {"injury", "status"})
        if k in contest.players and (k in out_keys or any(OUT_WORDS.search(f.text) for f in facts)):
            excluded.add(k)
            report["accepted"].append({"remove": k, "facts": [f.id for f in facts]})
        else:
            report["rejected"].append({"remove": k, "why": "no verified not-playing fact"})
    for item in output.get("cuts", []):
        k = item.get("player_key")
        facts = _verified(item.get("fact_ids", []), idx, k, contest) if k in contest.players else []
        if facts:
            excluded.add(k)
            report["accepted"].append({"cut": k, "facts": [f.id for f in facts], "reason": item.get("reason")})
        else:
            report["rejected"].append({"cut": k, "why": "cited facts missing, low-confidence or not about the player"})
    return excluded, report


def checker_task(contest: Contest, lu: Lineup, dossiers: dict[str, GameDossier], projections: dict,
                 *, model: str = "opus", attempt: int = 0) -> AgentTask:
    r = contest.rules
    return AgentTask(stage="checker", name=f"checker:{attempt}", model=model, tools=[],
                     system=prompts.CHECKER_SYSTEM,
                     prompt=prompts.CHECKER_PROMPT.format(
                         contest=f"{r.site.value} {r.sport} {r.game_type} ({contest.kind.value})",
                         lineup=lineup_text(contest, lu, projections), facts=facts_text(dossiers)),
                     schema=schemas.CHECKER, max_turns=4, timeout_s=900)


def checker_verdict(output: dict, idx: dict[str, Fact], contest: Contest, lu: Lineup) -> tuple[bool, set[str], dict]:
    """Code decides: fail only on a blocker that is about availability/role and is fact-backed."""
    fails: set[str] = set()
    notes = []
    picks = set(lu.player_keys)
    for a in output.get("attacks", []):
        k = a.get("player_key")
        if k not in picks:
            continue
        backed = _verified(a.get("fact_ids", []), idx, k, contest)
        decisive = a.get("severity") == "blocker" and a.get("claim") in ("not_playing", "limited_or_role_cut") and backed
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
    for k in output.get("remove", []):
        if k in new:
            removed.add(k)
    return new, removed, {"applied": applied, "removed": sorted(removed), "notes": output.get("notes")}


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
                               schema=schemas.NEWS, max_turns=max_searches * 2 + 4, timeout_s=600))
    return tasks


def parse_news(results: list[AgentResult], contest: Contest, keys: list[str]) -> dict[str, dict]:
    """player_key -> latest update dict (only for players named in the update)."""
    by_name = {key_name(k, contest): k for k in keys}
    out: dict[str, dict] = {}
    for r in results:
        if not r.ok or not r.output:
            continue
        for u in r.output.get("updates", []):
            k = by_name.get(norm_name(u.get("player", "")))
            if k:
                out[k] = u
    return out


def dump(obj) -> str:
    return json.dumps(obj, indent=1, default=str)
