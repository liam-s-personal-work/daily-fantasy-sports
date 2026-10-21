"""Last hour before lock: news on every pick, inactives, targeted re-forecast, one final file.

Rules (the user's):
- search news on every pick and check inactives;
- in the NFL, re-forecast only the players the news affects (and their position group on the
  same team, so shares stay coherent);
- change the lineup only if a pick is out, or a new lineup leads the current one by more than
  ``threshold`` (default 1%) on the contest objective. Both are scored in the same simulated
  outcomes (common random numbers), so simulation noise is not mistaken for a lead;
- players whose games have started stay locked in their slots (late swap);
- with an entries file every entry is checked: entries holding the main lineup follow the rules
  above; every other entry that holds a ruled-out player gets the smallest legal repair (its other
  picks kept);
- hand over one final file.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from dfs import blend as blend_mod
from dfs import strategy
from dfs.io.upload import write_upload
from dfs.llm.backend import Backend, Runner, default_backend
from dfs.models import Contest, Forecast, GameDossier, Lineup, Projection
from dfs.optimize.ilp import Infeasible, SolveOptions, check_legal, solve
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
    base = j("baseline.json")
    baseline = {k: Projection.model_validate(v) for k, v in base["projections"].items()}
    return {"contest": contest, "projections": projections, "dossiers": dossiers, "report": report, "final": final,
            "forecasts": forecasts, "baseline": baseline, "usage": base.get("usage") or {},
            "config": report.get("config", {})}


def lastcall(run_dir: Path, *, threshold: float = 0.01, web: bool = True, now: datetime | None = None,
             entries_file: str | Path | None = None,
             backend: Backend | None = None, extra_out: set[str] | None = None,
             log: Callable[[str], None] = print) -> dict[str, Any]:
    return asyncio.run(lastcall_async(run_dir, threshold=threshold, web=web, now=now, backend=backend,
                                      extra_out=extra_out, log=log, entries_file=entries_file))


async def lastcall_async(run_dir: Path, *, threshold: float = 0.01, web: bool = True, now: datetime | None = None,
                         entries_file: str | Path | None = None,
                         backend: Backend | None = None, extra_out: set[str] | None = None,
                         log: Callable[[str], None] = print) -> dict[str, Any]:
    st = load_run(run_dir)
    c: Contest = st["contest"]
    proj: dict[str, Projection] = dict(st["projections"])
    final: Lineup = st["final"]
    cfg = st["config"]
    now = now or datetime.now(timezone.utc)
    if web and now < datetime.now(timezone.utc) - timedelta(minutes=15) and not cfg.get("allow_lookahead"):
        log("WARNING: --now is in the past, so the news search is off (it would surface the results)")
        web = False
    runner = Runner(backend or default_backend(), cache_dir=run_dir.parent / "cache" / "llm",
                    budget_usd=float(cfg.get("lastcall_budget_usd", 4.0)), log=log)
    entry_lus = [Lineup.model_validate(x) for x in st["report"].get("lineups") or []] or [final]
    picks = list(dict.fromkeys(k for lu in [final, *entry_lus] for k in lu.player_keys))

    # 1. news on every pick (web) + any outs supplied by a structured refresh
    news: dict[str, dict] = {}
    news_failed: list[str] = []
    if web:
        results = await runner.run_all(agents.news_tasks(c, picks, now))
        news = agents.parse_news(results, c, picks, now=now)
        news_failed = [r.name.split(":", 1)[1] for r in results if not r.ok]  # games whose news check did not run
        for g in news_failed:
            log(f"WARNING: the news and inactives check failed for {g}: re-run the last call, or check by hand")
    out = set(extra_out or set()) | {k for k, u in news.items() if u.get("status") in BAD}
    affected = {k for k, u in news.items() if u.get("status") in CHANGED} | out

    # 2. NFL: re-forecast only the affected players (plus their position group on the same team)
    refreshed: dict[str, Forecast] = {}
    if c.rules.sport == "nfl" and affected and st["forecasts"]:
        group = {k for k in proj if any(c.players[k].team == c.players[a].team and
                                        c.players[k].positions[0] == c.players[a].positions[0] for a in affected)}
        from dfs.pipeline import agents_blind
        games = {c.players[k].game for k in group}
        # the same blind as the main forecast: no prices, lines or fantasy points in any fact
        facts = {g: d.model_copy(update={"facts": [f for f in d.facts if agents_blind(f)]})
                 for g, d in st["dossiers"].items() if g in games}
        for k, u in news.items():  # news becomes citable facts
            g = c.players[k].game
            if g in facts:
                f = agents.Fact(id=f"{g}.N{len(facts[g].facts) + 1}", subject=c.players[k].name,
                                team=c.players[k].team, kind="status", text=u.get("text", ""),
                                source_url=u.get("source_url"), as_of=u.get("as_of"), confidence="high")
                if agents_blind(f):
                    facts[g].facts.append(f)
        sub = c.model_copy(update={"games": sorted(g for g in games if g)})
        ft = agents.forecast_tasks(sub, facts, st["usage"], {}, group - out, model=cfg.get("forecast_model", "opus"))
        refreshed, _ = agents.parse_forecasts(await runner.run_all(ft), set(c.players))
        if refreshed:
            base = json.loads((run_dir / "baseline.json").read_text())
            se, wk = base.get("season"), base.get("week")
            newp = blend_mod.blend({k: st["baseline"][k] for k in refreshed if k in st["baseline"]}, refreshed,
                                   c.players, "nfl",
                                   learned=blend_mod.unless_fit_on(blend_mod.load_learned(cfg.get("learned_blend")), se, wk),
                                   calib=blend_mod.unless_fit_on(blend_mod.load_calibration(cfg["calibration"]), se, wk)
                                   if cfg.get("calibration") else {},
                                   weight_override=cfg.get("forecast_weight_override"))
            proj.update(newp)
    # 3. late swap: players whose games have started stay in their slots, even if news calls them out
    def has_started(k: str) -> bool:
        t = c.players[k].start_time
        return bool(t and t <= now)

    def slot_locks(lu: Lineup) -> dict[int, str]:
        return {i: s.site_id for i, s in enumerate(lu.slots) if has_started(s.player_key)}

    started_all = {k for k in c.players if has_started(k)}
    warnings: list[str] = []
    no_time = sorted(c.players[k].name for k in picks if c.players[k].start_time is None)
    if no_time and c.rules.late_swap:
        warnings.append(f"kickoff times unknown for {len(no_time)} picks ({', '.join(no_time[:5])}...): started players "
                        "cannot be told apart, so do not upload this file after the first game starts")
        log(f"WARNING: {warnings[-1]}")
    locked = {s.player_key for s in final.slots if has_started(s.player_key)}
    started = started_all - locked
    swap_out = out - started_all  # ruled out and still replaceable
    for k in swap_out:  # an inactive player is not scored at zero: he is removed
        proj.pop(k, None)
    keys, sims = simulate(proj, c.players, c.rules.sport, n_sims=int(cfg.get("n_sims", 10000)),
                          seed=int(cfg.get("seed", 11)))
    fm = strategy.field_for(c, proj, kind=c.kind, seed=int(cfg.get("seed", 11)))
    target = float(cfg.get("target_top", 0.023))
    metric = "p_cash" if c.kind.value == "cash" else "p_top"

    current_ok = not (set(final.player_keys) & swap_out)
    cur = final.model_copy(deep=True)
    if current_ok:
        strategy.evaluate([cur], keys, sims, fm, target)
    res = strategy.best_lineups(c, proj, keys, sims, fm, kind=c.kind, target_top=float(cfg.get("target_top", 0.023)),
                                exclude=started | swap_out, seed=int(cfg.get("seed", 11)), slot_locks=slot_locks(final))
    new = res["lineups"][0]

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

    # 4. every entry: those holding the final lineup follow the decision above; the others are
    #    repaired only if a pick is out (their own locked cells and started players stay put)
    means = {k: p.mean for k, p in proj.items()}
    repaired, unrepaired, final_lus = 0, [], []
    for i, lu in enumerate(entry_lus):
        if lu.player_keys == final.player_keys:
            final_lus.append(chosen)
            continue
        bad = set(lu.player_keys) & swap_out
        if not bad:
            final_lus.append(lu)
            continue
        keep = set(lu.player_keys) - bad
        fixed = None
        for lock in (keep, set()):  # smallest change first, then any legal lineup around the started picks
            try:
                fixed = solve(c, means, SolveOptions(slot_locks=slot_locks(lu), lock_keys=lock - started_all,
                                                     exclude_keys=(started_all - set(lu.player_keys)) | swap_out,
                                                     time_limit_s=10), label=f"entry {i + 1} repaired")
                break
            except Infeasible:
                continue
        if fixed is None or check_legal(c, fixed):
            unrepaired.append(i + 1)
            final_lus.append(lu)
        else:
            repaired += 1
            final_lus.append(fixed)
    from dfs.pipeline import started_ids
    # Check the new file against what the site holds now: the run's own upload (assumed entered), or
    # a freshly downloaded entries file when one is given (its LOCKED marks are the site's truth).
    eids = st["report"].get("entry_ids") or [e.entry_id for e in c.entries][:len(entry_lus)]
    held = {eid: [s.site_id for s in lu.slots] for eid, lu in zip(eids, entry_lus)}
    site = c.model_copy(update={"entries": [e.model_copy(update={"current": held.get(e.entry_id, e.current),
                                                                 "locked": [False] * len(e.current)})
                                            for e in c.entries]})
    if entries_file:
        from dfs.io.contest_file import load_contest
        fresh = {e.entry_id: e for e in load_contest(entries_file).entries}
        site = c.model_copy(update={"entries": [fresh.get(e.entry_id, e) for e in c.entries]})
    path = write_upload(site, final_lus, run_dir / "upload_final.csv", only_changed=False,
                        entry_ids=st["report"].get("entry_ids"), started_ids=started_ids(c, now))
    summary = {"now": now.isoformat(), "news": news, "out": sorted(out), "affected": sorted(affected),
               "out_but_locked": sorted(out & started_all), "news_failed": news_failed, "warnings": warnings,
               "news_checked": bool(web) and not news_failed,
               "entries": {"n": len(final_lus), "repaired": repaired, "could_not_repair": unrepaired},
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
    lineups = [st["final"]] + [Lineup.model_validate(x) for x in st["report"].get("lineups") or []]
    for k in dict.fromkeys(s.player_key for lu in lineups for s in lu.slots):  # picks in every entry
        t = c.players[k].start_time
        if t is not None and t.year > 1970:
            by_time.setdefault(t, []).append(c.players[k].name)
    return [{"kickoff": t.isoformat(), "run_lastcall_at": (t - timedelta(minutes=lead_minutes)).isoformat(),
             "picks": names} for t, names in sorted(by_time.items())]
