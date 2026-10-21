"""The main run: contest file in, one upload file (plus a report) out, inside a time budget.

Stages (football; other sports skip the forecaster and lean on the baseline):
 1 read contest + saved rules           (code)
 2 baseline + structured facts          (code: provider)
 3 research per game                    (Sonnet + web search, parallel)
 4 prune pool                           (code)
 5 blinded forecast per game            (Opus, parallel; no salaries/averages/past points/lines)
 6 blend                                (code; learned weights replace the priors)
 7 simulate + three solver drafts       (code: cash lens, tournament lens, leverage lens)
 8 strategist (+ backup in parallel)    (Opus / Sonnet; cuts only with cited facts)
 9 solver builds the lineup             (code, contest-type objective)
10 checker, code verdict, <=2 revisions (Opus)
11 user rules -> second lineup          (code)
12 write upload file + report           (code)
Every stage's input and output is saved in the run directory, so any stage can be re-run,
replayed from cache, graded, and attributed when grading names the step that failed.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from dfs import blend as blend_mod
from dfs.io.contest_file import load_contest
from dfs.io.upload import UploadError, write_upload
from dfs.llm.backend import Backend, Runner, default_backend
from dfs.models import Contest, ContestKind, GameDossier, Lineup, Projection, UserRules
from dfs.optimize.ilp import Infeasible, SolveOptions, check_legal, lineup_points, solve
from dfs.providers import GenericProvider, SlateContext
from dfs.stages import agents
from dfs.stages.decide import Decider

FOOTBALL = {"nfl", "cfb"}


class SlateStarted(RuntimeError):
    """Every game in the file has kicked off: nothing can be entered or swapped any more."""


def started_ids(contest: Contest, now: datetime | None) -> set[str]:
    """Site ids of players whose games have started (they can be neither added nor moved)."""
    if now is None:
        return set()
    return {c.site_id for c in contest.candidates
            if (t := contest.players[c.player_key].start_time) is not None and t <= now}


def entry_lock_groups(contest: Contest, now: datetime | None = None) -> dict[tuple, list[int]]:
    """Entry rows grouped by their locked cells ((slot index, site id), ...); unlocked rows share ().
    A cell is locked when the file marks it (LOCKED) or its player's game has started by ``now``."""
    live = started_ids(contest, now)
    groups: dict[tuple, list[int]] = {}
    for i, e in enumerate(contest.entries):
        locks = tuple((j, sid) for j, (sid, lk) in enumerate(zip(e.current, e.locked)) if sid and (lk or sid in live))
        groups.setdefault(locks, []).append(i)
    return groups


def safety_lineups(contest: Contest, points: dict[str, float], started: set[str],
                   now: datetime | None = None) -> tuple[list[Lineup], list[str] | None]:
    """One legal lineup per entry row (one in total without entries), with the entry id of each:
    started players are not added, and each entry's locked cells stay where they are. Entries that
    can no longer be given a legal lineup are left out (they keep what they have on the site)."""
    if not contest.entries:
        return [solve(contest, points, SolveOptions(time_limit_s=20, exclude_keys=set(started)),
                      label="safety (baseline mean)")], None
    out: list[Lineup | None] = [None] * len(contest.entries)
    for locks, idx in entry_lock_groups(contest, now).items():
        try:
            lu = solve(contest, points, SolveOptions(time_limit_s=20, exclude_keys=set(started),
                                                     slot_locks=dict(locks)), label="safety (baseline mean)")
        except Infeasible:
            continue
        for i in idx:
            out[i] = lu
    keep = [i for i, lu in enumerate(out) if lu is not None]
    if not keep:
        raise Infeasible("no entry can be given a legal lineup from the players still available")
    return [out[i] for i in keep], [contest.entries[i].entry_id for i in keep]


@dataclass
class RunConfig:
    out_dir: Path = Path("runs")
    budget_usd: float = 15.0
    deadline_min: float = 55.0
    web_research: bool = True
    forecaster: bool = True  # football only
    research_model: str = "sonnet"
    forecast_model: str = "opus"
    review_model: str = "opus"
    backup_model: str = "sonnet"
    max_searches_per_game: int = 6
    n_sims: int = 10000
    target_top: float = 0.023  # finish inside the top 2.3% (tournaments)
    field_size: int | None = None
    user_rules: UserRules | None = None
    learned_blend: Path | None = blend_mod.LEARNED_DIR / "blend.json"
    calibration: Path | None = blend_mod.LEARNED_DIR / "calibration.json"
    concurrency: int = 6
    seed: int = 11
    season: int | None = None
    week: int | None = None
    replay_only: bool = False
    forecast_weight_override: float | None = None
    provider: str | None = None  # force "generic" (CSV averages only) instead of the sport's data provider
    allow_lookahead: bool = False  # past-slate runs (now well before the real clock) turn web research off unless set
    kind: str | None = None  # "cash" or "gpp"; default: from the entries' contest names, else gpp


@dataclass
class RunResult:
    run_dir: Path
    contest: Contest
    final: Lineup
    rules_lineup: Lineup | None
    projections: dict[str, Projection]
    report: dict[str, Any]
    upload_path: Path
    rules_upload_path: Path | None = None
    stage_lineups: dict[str, Lineup] = field(default_factory=dict)


class Clock:
    def __init__(self, deadline_min: float, log: Callable[[str], None]):
        self.t0 = time.monotonic()
        self.deadline = deadline_min * 60
        self.log = log
        self.marks: dict[str, float] = {}

    def mark(self, stage: str) -> None:
        self.marks[stage] = round(time.monotonic() - self.t0, 1)
        self.log(f"[{self.marks[stage]:7.1f}s] {stage}")

    def left(self) -> float:
        return self.deadline - (time.monotonic() - self.t0)


def provider_for(contest: Contest, cfg: RunConfig):
    sport = contest.rules.sport
    if cfg.provider == "generic":
        return GenericProvider()
    if sport == "nfl":
        from dfs.projections.nfl_provider import NFLProvider
        return NFLProvider(season=cfg.season, week=cfg.week)
    if sport == "cfb":
        try:
            from dfs.projections.cfb_provider import CFBProvider
            return CFBProvider(season=cfg.season, week=cfg.week)
        except ImportError:
            return GenericProvider()
    return GenericProvider()


def _run_id(contest: Contest) -> str:
    h = hashlib.sha256(Path(contest.source_file).read_bytes()).hexdigest()[:8]
    r = contest.rules
    return f"{r.site.value[:2]}-{r.sport}-{r.game_type}-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{h}"


def _save(run_dir: Path, name: str, obj: Any) -> None:
    def enc(o):
        if hasattr(o, "model_dump"):
            return o.model_dump(mode="json")
        if isinstance(o, set):
            return sorted(o)
        if isinstance(o, Path):
            return str(o)
        return str(o)
    (run_dir / name).write_text(json.dumps(obj, indent=1, default=enc))


def prune_pool(contest: Contest, ctx: SlateContext, flagged: set[str], max_per_pos: dict[str, int] | None = None) -> set[str]:
    """Players worth forecasting: not out, a real role (top-N baseline at the position per team) or flagged."""
    caps = max_per_pos or {"QB": 2, "RB": 3, "WR": 5, "TE": 2, "DST": 1, "D": 1, "K": 1}
    by_team_pos: dict[tuple[str, str], list[tuple[float, str]]] = {}
    for k, pr in ctx.baseline.items():
        if k in ctx.ruled_out or k not in contest.players:
            continue
        p = contest.players[k]
        by_team_pos.setdefault((p.team, p.positions[0]), []).append((pr.mean, k))
    keep: set[str] = set(flagged) - ctx.ruled_out
    for (team, pos), rows in by_team_pos.items():
        rows.sort(reverse=True)
        keep.update(k for m, k in rows[: caps.get(pos, 3)] if m > 0.5)
    return keep


def run(contest_path: str | Path, cfg: RunConfig | None = None, backend: Backend | None = None,
        log: Callable[[str], None] = print, now: datetime | None = None) -> RunResult:
    return asyncio.run(run_async(contest_path, cfg, backend, log, now))


async def run_async(contest_path: str | Path, cfg: RunConfig | None = None, backend: Backend | None = None,
                    log: Callable[[str], None] = print, now: datetime | None = None) -> RunResult:
    cfg = cfg or RunConfig()
    clock = Clock(cfg.deadline_min, log)
    contest = load_contest(contest_path)
    if cfg.field_size:
        contest = contest.model_copy(update={"field_size": cfg.field_size})
    if cfg.kind:  # salary files never say whether the contest is a cash game or a tournament
        contest = contest.model_copy(update={"kind": ContestKind(cfg.kind)})
    run_dir = Path(cfg.out_dir) / _run_id(contest)
    run_dir.mkdir(parents=True, exist_ok=True)
    for w in contest.warnings:
        log(f"WARNING: {w}")
    _save(run_dir, "contest.json", contest)
    t_now = now or datetime.now(timezone.utc)
    started = {k for k, p in contest.players.items() if p.start_time and p.start_time <= t_now}
    if contest.players and len(started) == len(contest.players):
        first = min(p.start_time for p in contest.players.values())
        raise SlateStarted(f"every game in this file has already started (first kickoff {first:%Y-%m-%d %H:%M %Z}, "
                           f"now {t_now:%Y-%m-%d %H:%M %Z}); nothing can be entered or swapped. To test on a past "
                           "slate, pass --now with a time before its first kickoff.")
    clock.mark(f"read contest: {contest.rules.site.value} {contest.rules.sport} {contest.rules.game_type}, "
               f"{len(contest.players)} players, {len(contest.games)} games, {len(contest.entries)} entries")

    runner = Runner(backend or default_backend(), cache_dir=Path(cfg.out_dir) / "cache" / "llm",
                    budget_usd=cfg.budget_usd, concurrency=cfg.concurrency, replay_only=cfg.replay_only, log=log)
    # Agents must finish with time to spare for the solver and the files (about 4 minutes on a main slate).
    runner.deadline_at = clock.t0 + clock.deadline - min(240.0, 0.3 * clock.deadline)
    sport = contest.rules.sport

    # 2. baseline + structured facts
    provider = provider_for(contest, cfg)
    try:
        ctx: SlateContext = provider.prepare(contest, now=now)
    except (ValueError, KeyError, OSError) as e:  # e.g. the slate's games are not in the stats schedule
        if isinstance(provider, GenericProvider):
            raise
        log(f"WARNING: {type(provider).__name__} failed ({e}); using the site averages in the file instead")
        contest.warnings.append(f"stats baseline unavailable ({e}); site-average baseline used")
        ctx = GenericProvider().prepare(contest, now=now)
    _save(run_dir, "baseline.json", {"source": ctx.data_source, "notes": ctx.notes, "season": ctx.season,
                                     "week": ctx.week, "ids": ctx.ids, "ruled_out": ctx.ruled_out,
                                     "projections": ctx.baseline, "market": ctx.market, "usage": ctx.usage})
    clock.mark(f"baseline ({ctx.data_source}): {len(ctx.baseline)} players, {len(ctx.ruled_out)} ruled out")
    no_time = sum(1 for p in contest.players.values() if p.start_time is None)
    if no_time and contest.rules.late_swap and not ctx.kickoffs:
        w = (f"kickoff times unknown for {no_time} players (FanDuel files carry none): late swap cannot tell which "
             "games have started, so do not re-upload after the first game starts")
        contest.warnings.append(w)
        log(f"WARNING: {w}")
    if ctx.kickoffs and any(p.start_time is None for p in contest.players.values()):
        # FD files have no kickoff times; late swap and the last-call windows need them
        for p in contest.players.values():
            if p.start_time is None and p.game in ctx.kickoffs:
                p.start_time = ctx.kickoffs[p.game]
        _save(run_dir, "contest.json", contest)
        started = {k for k, p in contest.players.items() if p.start_time and p.start_time <= t_now}
        if len(started) == len(contest.players):
            raise SlateStarted(f"every game in this file has already started (now {t_now:%Y-%m-%d %H:%M %Z}); "
                               "nothing can be entered or swapped. To test on a past slate, pass --now with a "
                               "time before its first kickoff.")
    try:  # a legal safety lineup within the first minutes, in case anything later fails or runs long
        safety, safety_ids = safety_lineups(contest, {k: p.mean for k, p in ctx.baseline.items()
                                                      if k not in ctx.ruled_out}, started, t_now)
        write_upload(contest, safety, run_dir / "upload_safety.csv", only_changed=False,
                     entry_ids=safety_ids, started_ids=started_ids(contest, t_now))
        clock.mark(f"safety lineup written (mean {safety[0].mean:.1f})")
    except Exception as e:  # never block the run on the safety net
        log(f"safety lineup failed: {e}")

    # 3. research
    dossiers: dict[str, GameDossier] = {}
    web = cfg.web_research
    if web and now is not None and now < datetime.now(timezone.utc) - timedelta(minutes=15) and not cfg.allow_lookahead:
        # a run "as of" a past time would search today's web, which reports the games' results
        log("WARNING: --now is in the past, so web research is off (it would surface the results); "
            "pass --allow-lookahead to override")
        web = False
    if web:
        tasks = agents.research_tasks(contest, ctx.known, ctx.market, model=cfg.research_model,
                                      max_searches=cfg.max_searches_per_game,
                                      timeout_s=max(120.0, min(900.0, clock.left() * 0.25)), now=t_now)
        dossiers = agents.parse_research(await runner.run_all(tasks), now=t_now)
    for g in contest.games:  # structured facts always join the dossier (and stand in when research failed)
        sfacts = ctx.structured_facts.get(g, [])
        if g in dossiers:
            d = dossiers[g]
            start = len(d.facts)
            d.facts.extend(f.model_copy(update={"id": f"{g}.S{start + i}"}) for i, f in enumerate(sfacts, 1))
        else:
            dossiers[g] = agents.structured_dossier(g, sfacts, [])
    out_keys = set(ctx.ruled_out) | agents.ruled_out_keys(contest, dossiers)
    _save(run_dir, "dossiers.json", dossiers)
    clock.mark(f"research: {sum(len(d.facts) for d in dossiers.values())} facts, {len(out_keys)} out")

    # 4-6. prune, forecast, blend
    pool = prune_pool(contest, ctx, flagged=set())
    forecasts = {}
    priced: set[str] = set()
    if cfg.forecaster and sport in FOOTBALL and pool:
        blinded = {g: d.model_copy(update={"facts": [f for f in d.facts if agents_blind(f)]}) for g, d in dossiers.items()}
        priced = {f.id for d in blinded.values() for f in d.facts}
        ftasks = agents.forecast_tasks(contest, blinded, ctx.usage, {}, pool - out_keys, model=cfg.forecast_model,
                                       timeout_s=max(120.0, min(900.0, clock.left() * 0.3)))
        fres = await runner.run_all(ftasks)
        forecasts, problems = agents.parse_forecasts(fres, set(contest.players))
        for p in problems:
            log(f"forecast issue: {p}")
        # The novelty rule only counts a fact as priced in where the forecaster actually ran
        done = {r.name.split(":", 1)[1] for r in fres if r.ok}
        priced = {f.id for g, d in blinded.items() if g in done for f in d.facts}
    _save(run_dir, "forecasts.json", forecasts)
    learned = blend_mod.load_learned(cfg.learned_blend)
    calib = blend_mod.load_calibration(cfg.calibration) if cfg.calibration else {}
    season, week = ctx.season or cfg.season, ctx.week or cfg.week
    for name in ("blend weights", "calibration"):
        table = learned if name == "blend weights" else calib
        if table and not blend_mod.unless_fit_on(table, season, week):  # never use numbers fit on this week
            log(f"WARNING: learned {name} were fit on {season} week {week}; using the priors instead")
            if name == "blend weights":
                learned = {}
            else:
                calib = {}
    baseline = {k: v for k, v in ctx.baseline.items() if k not in out_keys}
    projections = blend_mod.blend(baseline, {k: v for k, v in forecasts.items() if k not in out_keys},
                                  contest.players, sport, learned=learned, calib=calib,
                                  weight_override=cfg.forecast_weight_override)
    _save(run_dir, "projections.json", projections)
    clock.mark(f"forecast+blend: {len(forecasts)} forecasts, {len(projections)} projections")

    # 7-11. decide (simulate, drafts, strategist/backup, solve, checker loop, user rules)
    decider = Decider(contest, projections, cfg, runner, dossiers, log=log, n_lineups=max(1, len(contest.entries)),
                      now=now or datetime.now(timezone.utc), priced=priced if forecasts else set(), clock=clock,
                      forecasted=set(forecasts) if forecasts else None)
    decision = await decider.decide()
    clock.mark(f"decision: {decision.final.label} mean {decision.final.mean:.1f}")

    # 12. outputs: every lineup passes the independent legality check before it is written
    for i, lu in enumerate(decision.lineups):
        bad = check_legal(contest, lu)
        if bad:
            raise Infeasible(f"lineup for entry {i + 1} failed the legality check: {bad}")
    if decision.rules_lineup is not None and check_legal(contest, decision.rules_lineup):
        decision.report["user_rules_error"] = f"rules lineup failed the legality check: {check_legal(contest, decision.rules_lineup)}"
        decision.rules_lineup = None
    upload = write_upload(contest, decision.lineups, run_dir / "upload.csv", only_changed=False,
                          entry_ids=decision.entry_ids, started_ids=started_ids(contest, t_now))
    rules_upload = None
    if decision.rules_lineup is not None:
        try:
            rules_upload = write_upload(contest, [decision.rules_lineup], run_dir / "upload_my_rules.csv",
                                        only_changed=False, started_ids=started_ids(contest, t_now))
        except UploadError as e:  # e.g. the first entry's locked cells conflict with your rules
            decision.report["user_rules_error"] = f"rules lineup not written: {e}"
            log(f"WARNING: rules lineup not written: {e}")
    report = {
        "contest": {"site": contest.rules.site.value, "sport": sport, "game_type": contest.rules.game_type,
                    "kind": contest.kind.value, "games": contest.games, "warnings": contest.warnings,
                    "rules_confidence": contest.rules.confidence},
        "timing_s": clock.marks, "llm": runner.summary(), "decision": decision.report,
        "final": decision.final, "rules_lineup": decision.rules_lineup, "lineups": decision.lineups,
        "entry_ids": decision.entry_ids,
        "stage_lineups": decision.stage_lineups, "legal": check_legal(contest, decision.final),
        "config": {k: (str(v) if isinstance(v, Path) else v.model_dump() if hasattr(v, "model_dump") else v)
                   for k, v in cfg.__dict__.items()},
    }
    _save(run_dir, "report.json", report)
    (run_dir / "report.md").write_text(decider.markdown(decision, clock.marks, runner.summary()))
    clock.mark(f"wrote {upload}")
    return RunResult(run_dir=run_dir, contest=contest, final=decision.final, rules_lineup=decision.rules_lineup,
                     projections=projections, report=report, upload_path=upload, rules_upload_path=rules_upload,
                     stage_lineups=decision.stage_lineups)


def agents_blind(fact) -> bool:
    """Exactly what the forecaster is shown (subject and text both pass the blind)."""
    from dfs.sources import blind_text
    return blind_text(f"{fact.subject} {fact.text}") is not None and fact.kind != "line"
