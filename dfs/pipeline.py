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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from dfs import blend as blend_mod
from dfs.io.contest_file import load_contest
from dfs.io.upload import write_upload
from dfs.llm.backend import Backend, Runner, default_backend
from dfs.models import Contest, ContestKind, GameDossier, Lineup, Projection, UserRules
from dfs.optimize.ilp import Infeasible, SolveOptions, check_legal, lineup_points, solve
from dfs.providers import GenericProvider, SlateContext
from dfs.stages import agents
from dfs.stages.decide import Decider

FOOTBALL = {"nfl", "cfb"}


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
    learned_blend: Path | None = Path("data/learned/blend.json")
    concurrency: int = 6
    seed: int = 11
    season: int | None = None
    week: int | None = None
    replay_only: bool = False
    forecast_weight_override: float | None = None
    provider: str | None = None  # force "generic" (CSV averages only) instead of the sport's data provider


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
    run_dir = Path(cfg.out_dir) / _run_id(contest)
    run_dir.mkdir(parents=True, exist_ok=True)
    for w in contest.warnings:
        log(f"WARNING: {w}")
    _save(run_dir, "contest.json", contest)
    clock.mark(f"read contest: {contest.rules.site.value} {contest.rules.sport} {contest.rules.game_type}, "
               f"{len(contest.players)} players, {len(contest.games)} games, {len(contest.entries)} entries")

    runner = Runner(backend or default_backend(), cache_dir=Path(cfg.out_dir) / "cache" / "llm",
                    budget_usd=cfg.budget_usd, concurrency=cfg.concurrency, replay_only=cfg.replay_only, log=log)
    sport = contest.rules.sport

    # 2. baseline + structured facts
    provider = provider_for(contest, cfg)
    ctx: SlateContext = provider.prepare(contest, now=now)
    _save(run_dir, "baseline.json", {"source": ctx.data_source, "notes": ctx.notes,
                                     "ruled_out": ctx.ruled_out, "projections": ctx.baseline})
    clock.mark(f"baseline ({ctx.data_source}): {len(ctx.baseline)} players, {len(ctx.ruled_out)} ruled out")

    # 3. research
    dossiers: dict[str, GameDossier] = {}
    if cfg.web_research:
        tasks = agents.research_tasks(contest, ctx.known, ctx.market, model=cfg.research_model,
                                      max_searches=cfg.max_searches_per_game,
                                      timeout_s=max(120.0, min(900.0, clock.left() * 0.25)))
        dossiers = agents.parse_research(await runner.run_all(tasks))
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
    if cfg.forecaster and sport in FOOTBALL and pool:
        blinded = {g: d.model_copy(update={"facts": [f for f in d.facts if agents_blind(f)]}) for g, d in dossiers.items()}
        ftasks = agents.forecast_tasks(contest, blinded, ctx.usage, {}, pool - out_keys, model=cfg.forecast_model,
                                       timeout_s=max(120.0, min(900.0, clock.left() * 0.3)))
        forecasts, problems = agents.parse_forecasts(await runner.run_all(ftasks), set(contest.players))
        for p in problems:
            log(f"forecast issue: {p}")
    _save(run_dir, "forecasts.json", forecasts)
    learned = blend_mod.load_learned(cfg.learned_blend)
    baseline = {k: v for k, v in ctx.baseline.items() if k not in out_keys}
    projections = blend_mod.blend(baseline, {k: v for k, v in forecasts.items() if k not in out_keys},
                                  contest.players, sport, learned=learned, weight_override=cfg.forecast_weight_override)
    _save(run_dir, "projections.json", projections)
    clock.mark(f"forecast+blend: {len(forecasts)} forecasts, {len(projections)} projections")

    # 7-11. decide (simulate, drafts, strategist/backup, solve, checker loop, user rules)
    decider = Decider(contest, projections, cfg, runner, dossiers, log=log)
    decision = await decider.decide()
    clock.mark(f"decision: {decision.final.label} mean {decision.final.mean:.1f}")

    # 12. outputs
    upload = write_upload(contest, [decision.final] * max(1, min(len(contest.entries), 1)), run_dir / "upload.csv",
                          only_changed=False)
    rules_upload = None
    if decision.rules_lineup is not None:
        rules_upload = write_upload(contest, [decision.rules_lineup], run_dir / "upload_my_rules.csv", only_changed=False)
    report = {
        "contest": {"site": contest.rules.site.value, "sport": sport, "game_type": contest.rules.game_type,
                    "kind": contest.kind.value, "games": contest.games, "warnings": contest.warnings,
                    "rules_confidence": contest.rules.confidence},
        "timing_s": clock.marks, "llm": runner.summary(), "decision": decision.report,
        "final": decision.final, "rules_lineup": decision.rules_lineup,
        "legal": check_legal(contest, decision.final),
    }
    _save(run_dir, "report.json", report)
    (run_dir / "report.md").write_text(decider.markdown(decision, clock.marks, runner.summary()))
    clock.mark(f"wrote {upload}")
    return RunResult(run_dir=run_dir, contest=contest, final=decision.final, rules_lineup=decision.rules_lineup,
                     projections=projections, report=report, upload_path=upload, rules_upload_path=rules_upload,
                     stage_lineups=decision.stage_lineups)


def agents_blind(fact) -> bool:
    from dfs.sources import blind_text
    return blind_text(fact.text) is not None and fact.kind != "line"
