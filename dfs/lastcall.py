"""Last hour before lock: news on every pick, inactives, targeted re-forecast, one final file.

Rules (the user's):
- search news on every pick and check inactives;
- in the NFL, re-forecast only the players the news affects (and their position group on the
  same team, so shares stay coherent);
- change the lineup only if a pick is out, or a new lineup leads the current one by more than
  ``threshold`` (default 1%) on the contest objective. Both are scored in the same simulated
  outcomes (common random numbers), so simulation noise is not mistaken for a lead;
- players whose games have started stay locked (late swap);
- hand over one final file.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from dfs import blend as blend_mod
from dfs import strategy
from dfs.io.upload import write_upload
from dfs.llm.backend import Backend, Runner, default_backend
from dfs.models import Contest, Forecast, GameDossier, Lineup, Projection
from dfs.optimize.ilp import check_legal
from dfs.sim import simulate
from dfs.stages import agents

BAD = {"inactive", "out"}
CHANGED = {"questionable", "game_time_decision", "limited_role", "expanded_role"}


def load_run(run_dir: Path) -> dict[str, Any]:
    j = lambda n: json.loads((run_dir / n).read_text())
    contest = Contest.model_validate(j("contest.json"))
    projections = {k: Projection.model_validate(v) for k, v in j("projections.json").items()}
    dossiers = {k: GameDossier.model_validate(v) for k, v in j("dossiers.json").items()}
    report = j("report.json")
    final = Lineup.model_validate(report["final"])
    forecasts = {k: Forecast.model_validate(v) for k, v in j("forecasts.json").items()} if (run_dir / "forecasts.json").exists() else {}
    baseline = {k: Projection.model_validate(v) for k, v in j("baseline.json")["projections"].items()}
    return {"contest": contest, "projections": projections, "dossiers": dossiers, "report": report, "final": final,
            "forecasts": forecasts, "baseline": baseline, "config": report.get("config", {})}


def lastcall(run_dir: Path, *, threshold: float = 0.01, web: bool = True, now: datetime | None = None,
             backend: Backend | None = None, extra_out: set[str] | None = None,
             log: Callable[[str], None] = print) -> dict[str, Any]:
    return asyncio.run(lastcall_async(run_dir, threshold=threshold, web=web, now=now, backend=backend,
                                      extra_out=extra_out, log=log))


async def lastcall_async(run_dir: Path, *, threshold: float = 0.01, web: bool = True, now: datetime | None = None,
                         backend: Backend | None = None, extra_out: set[str] | None = None,
                         log: Callable[[str], None] = print) -> dict[str, Any]:
    st = load_run(run_dir)
    c: Contest = st["contest"]
    proj: dict[str, Projection] = dict(st["projections"])
    final: Lineup = st["final"]
    cfg = st["config"]
    now = now or datetime.now(timezone.utc)
    runner = Runner(backend or default_backend(), cache_dir=run_dir.parent / "cache" / "llm",
                    budget_usd=float(cfg.get("lastcall_budget_usd", 4.0)), log=log)
    picks = final.player_keys

    # 1. news on every pick (web) + any outs supplied by a structured refresh
    news: dict[str, dict] = {}
    if web:
        results = await runner.run_all(agents.news_tasks(c, picks, now))
        news = agents.parse_news(results, c, picks)
    out = set(extra_out or set()) | {k for k, u in news.items() if u.get("status") in BAD}
    affected = {k for k, u in news.items() if u.get("status") in CHANGED} | out

    # 2. NFL: re-forecast only the affected players (plus their position group on the same team)
    refreshed: dict[str, Forecast] = {}
    if c.rules.sport == "nfl" and affected and st["forecasts"]:
        group = {k for k in proj if any(c.players[k].team == c.players[a].team and
                                        c.players[k].positions[0] == c.players[a].positions[0] for a in affected)}
        games = {c.players[k].game for k in group}
        facts = {g: d for g, d in st["dossiers"].items() if g in games}
        for k, u in news.items():  # news becomes citable facts
            g = c.players[k].game
            if g in facts:
                facts[g].facts.append(agents.Fact(id=f"{g}.N{len(facts[g].facts) + 1}", subject=c.players[k].name,
                                                  team=c.players[k].team, kind="status", text=u.get("text", ""),
                                                  source_url=u.get("source_url"), as_of=u.get("as_of"), confidence="high"))
        sub = c.model_copy(update={"games": sorted(g for g in games if g)})
        ft = agents.forecast_tasks(sub, facts, {}, {}, group - out, model=cfg.get("forecast_model", "opus"))
        refreshed, _ = agents.parse_forecasts(await runner.run_all(ft), set(c.players))
        if refreshed:
            newp = blend_mod.blend({k: st["baseline"][k] for k in refreshed if k in st["baseline"]}, refreshed,
                                   c.players, "nfl", learned=blend_mod.load_learned(cfg.get("learned_blend")),
                                   calib=blend_mod.load_calibration(cfg.get("calibration")))
            proj.update(newp)
    for k in out:  # an inactive player is not scored at zero: he is removed
        proj.pop(k, None)

    # 3. late swap: started games are locked
    locked = {s.player_key for s in final.slots
              if c.players[s.player_key].start_time and c.players[s.player_key].start_time <= now}
    started = {k for k, p in c.players.items() if p.start_time and p.start_time <= now} - locked
    keys, sims = simulate(proj, c.players, c.rules.sport, n_sims=int(cfg.get("n_sims", 10000)),
                          seed=int(cfg.get("seed", 11)))
    fm = strategy.field_for(c, proj, kind=c.kind, seed=int(cfg.get("seed", 11)))
    target = float(cfg.get("target_top", 0.023))
    metric = "p_cash" if c.kind.value == "cash" else "p_top"

    current_ok = not (set(picks) & out)
    cur = final.model_copy(deep=True)
    if current_ok:
        strategy.evaluate([cur], keys, sims, fm, target)
    res = strategy.best_lineups(c, proj, keys, sims, fm, kind=c.kind, target_top=float(cfg.get("target_top", 0.023)),
                                exclude=started | out, seed=int(cfg.get("seed", 11)))
    new = res["lineups"][0]
    if locked:  # keep locked slots fixed: re-solve with them locked if the best lineup moved any
        if not locked <= set(new.player_keys):
            from dfs.optimize.ilp import SolveOptions, solve
            new = solve(c, {k: p.mean for k, p in proj.items()}, SolveOptions(lock_keys=locked, exclude_keys=started | out))
            strategy.evaluate([new], keys, sims, fm, target)

    lead = (new.metrics[metric] - cur.metrics.get(metric, 0.0)) / max(cur.metrics.get(metric, 0.0), 1e-9) if current_ok else float("inf")
    # The lead must beat both the user's 1% bar and the simulation's own noise (2 standard errors).
    n_s = sims.shape[0]
    se = (sum(m.get(metric, 0.0) * (1 - m.get(metric, 0.0)) for m in (new.metrics, cur.metrics)) / n_s) ** 0.5
    noise_ok = (new.metrics.get(metric, 0.0) - cur.metrics.get(metric, 0.0)) > 2 * se
    change = (not current_ok) or (lead > threshold and noise_ok)
    chosen = new if change else cur
    problems = check_legal(c, chosen)
    if problems:
        raise RuntimeError(f"last-call lineup is not legal: {problems}")
    path = write_upload(c, [chosen], run_dir / "upload_final.csv", only_changed=False)
    summary = {"now": now.isoformat(), "news": news, "out": sorted(out), "affected": sorted(affected),
               "reforecast": sorted(refreshed), "locked": sorted(locked), "changed": change,
               "reason": ("a pick is out" if not current_ok else f"new lineup leads by {lead:.1%}" if change
                          else f"no change (best alternative leads by {lead:.1%} <= {threshold:.0%})"),
               "metric": metric, "current": cur.metrics, "new": new.metrics, "final": chosen, "upload": str(path),
               "llm": runner.summary()}
    (run_dir / "lastcall.json").write_text(json.dumps(summary, indent=1, default=lambda o: o.model_dump() if hasattr(o, "model_dump") else str(o)))
    return summary


def kickoff_windows(run_dir: Path, lead_minutes: int = 60) -> list[dict[str, Any]]:
    """Kickoff windows that still hold picks, with when to run the last call for each.

    NFL inactives come out about 90 minutes before each kickoff, so one pass per window at
    roughly T-60 sees them; late swap lets each window be revisited separately (not showdown).
    """
    from datetime import timedelta
    st = load_run(run_dir)
    c: Contest = st["contest"]
    by_time: dict[datetime, list[str]] = {}
    for s in st["final"].slots:
        t = c.players[s.player_key].start_time
        if t is not None:
            by_time.setdefault(t, []).append(c.players[s.player_key].name)
    return [{"kickoff": t.isoformat(), "run_lastcall_at": (t - timedelta(minutes=lead_minutes)).isoformat(),
             "picks": names} for t, names in sorted(by_time.items())]
