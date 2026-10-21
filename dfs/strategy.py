"""Glue between the decision stage and ``dfs.select``: contest-objective choices with exclusions,
user rules, late-swap locks and bounded point adjustments.

- cash: maximise P(score >= cash line), the cash line being the simulated field's median;
- tournament: maximise P(finishing above the field's top-x% cut) in the same simulated world;
- head-to-head: maximise P(beating one random rival).
Candidates are built on one set of simulated outcomes and scored on another (different
seeds), so a lineup is never graded on the outcome it was optimised for.
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np

from dfs import select as sx
from dfs.field import FieldModel, build_field
from dfs.models import Contest, ContestKind, Lineup, Projection, UserRules
from dfs.optimize.ilp import Infeasible, SolveOptions, validate_rules
from dfs.sim import simulate


def public_points(contest: Contest, proj: dict[str, Projection], w_site: float = 0.5) -> dict[str, float]:
    """What rivals are assumed to project: our mean blended with the site's own average.

    Rivals optimising exactly our numbers would understate our edge; rivals with no
    information would overstate it. The resulting P(top x%) is still a model figure.
    """
    out = {}
    for k, p in proj.items():
        avg = contest.players[k].site_avg if k in contest.players else None
        out[k] = (1 - w_site) * p.mean + w_site * avg if avg and avg > 0 else p.mean
    return out


def field_for(contest: Contest, proj: dict[str, Projection], *, kind: ContestKind, seed: int = 23,
              n_lineups: int = 150, time_budget_s: float = 60.0) -> FieldModel | None:
    try:
        return build_field(contest, proj, n_lineups=n_lineups, seed=seed + 7, time_budget_s=time_budget_s,
                           target_top3=0.45 if sx.kind_of(kind) == "cash" else 0.35,
                           public_points=public_points(contest, proj))
    except Infeasible:
        return None


def _base(exclude: set[str], user_rules: UserRules | None, slot_locks: dict[int, str] | None,
          contest: Contest) -> SolveOptions:
    if user_rules is not None:
        validate_rules(contest, user_rules)  # a bad name or team is reported as such, not as "infeasible"
    locks = dict(slot_locks or {})
    lock_keys: set[str] = set()
    if locks:  # locked cells also pin the player, so dominance pruning can never drop him
        by_id = {c.site_id: c.player_key for c in contest.candidates}
        lock_keys = {by_id[s] for s in locks.values() if s in by_id}
    return SolveOptions(exclude_keys=set(exclude) - lock_keys, user_rules=user_rules, slot_locks=locks,
                        lock_keys=lock_keys, time_limit_s=5.0, workers=1)


_POOLS: dict[tuple, tuple[list[Lineup], list[Lineup]]] = {}


def _pool_key(contest: Contest, proj: dict[str, Projection], base: SolveOptions, seed: int,
              fm: FieldModel | None = None, sizes: tuple = ()) -> tuple:
    dist = tuple(sorted((k, round(p.mean, 4), round(p.p10, 3), round(p.p50, 3), round(p.p90, 3), round(p.p_zero, 4))
                        for k, p in proj.items()))
    ur = base.user_rules.model_dump_json() if base.user_rules else ""
    field_id = (len(fm.lineups), hash(tuple(round(float(w), 6) for w in fm.weights))) if fm is not None else None
    return (contest.source_file, contest.rules.game_type, contest.rules.salary_cap, hash(dist),
            tuple(sorted(base.exclude_keys)), ur, tuple(sorted(base.slot_locks.items())), seed, field_id, sizes)


def candidates(contest: Contest, proj: dict[str, Projection], *, base: SolveOptions, fm: FieldModel | None,
               seed: int, n_mean: int = 15, n_draws: int = 120, n_half: int = 40,
               time_budget_s: float = 150.0) -> tuple[list[Lineup], list[Lineup]]:
    """(candidates, sim-optimal lineups), built on their own simulated draws (seed + 1).
    Pools are memoised per (numbers, exclusions, rules, locks), so the drafts, the final choice
    and the top-3 table share one set of solves."""
    key = _pool_key(contest, proj, base, seed, fm, (n_mean, n_draws, n_half))
    if key in _POOLS:
        pool, draws = _POOLS[key]
        return [lu.model_copy(deep=True) for lu in pool], draws
    t0 = time.perf_counter()
    usable = {k: p for k, p in proj.items() if k not in base.exclude_keys}
    gkeys, gsims = simulate(usable, contest.players, contest.rules.sport, n_sims=max(n_draws, n_half), seed=seed + 1)
    means = np.array([usable[k].mean for k in gkeys])
    draws = sx.sim_optimal(contest, gkeys, gsims[:n_draws], means, 1.0, base, t0 + 0.45 * time_budget_s)
    half = sx.sim_optimal(contest, gkeys, gsims[:n_half], means, 0.5, base, t0 + 0.6 * time_budget_s, "half")
    pts = {k: p.mean for k, p in usable.items()}
    cands = sx.mean_optimal(contest, pts, n_mean, 2, base, t0 + 0.8 * time_budget_s)
    if contest.rules.sport.lower() in ("nfl", "cfb") and contest.rules.game_type == "classic":
        cands += sx.stack_variants(contest, pts, 6, (1, 2), (0, 1), base, t0 + time_budget_s)
    if fm is not None and sx._can_prune(base) and not base.lock_keys and not base.exclude_keys and not base.slot_locks:
        order = np.argsort(-fm.weights)[:15]
        cands += [fm.lineups[i].model_copy(update={"label": f"field{r + 1}"}) for r, i in enumerate(order)]
    pool = sx.dedupe(cands + half + draws)
    _POOLS[key] = (pool, draws)
    return [lu.model_copy(deep=True) for lu in pool], draws


def _scaled(sims: np.ndarray, keys: list[str], scale: dict[str, float] | None) -> np.ndarray:
    if not scale:
        return sims
    s = sims.copy()
    col = {k: j for j, k in enumerate(keys)}
    for k, f in scale.items():
        if k in col and f != 1.0:
            s[:, col[k]] *= f
    return s


def best_lineups(contest: Contest, proj: dict[str, Projection], keys: list[str], sims: np.ndarray,
                 fm: FieldModel | None, *, kind: ContestKind, target_top: float, n: int = 1,
                 exclude: set[str] = frozenset(), user_rules: UserRules | None = None, seed: int = 0,
                 scale: dict[str, float] | None = None, slot_locks: dict[int, str] | None = None) -> dict[str, Any]:
    """Best lineup(s) for the contest objective. ``sims`` are the evaluation draws (seed ``seed``)
    for ``keys``; ``scale`` multiplies a player's simulated points (backup review adjustments)."""
    base = _base(exclude, user_rules, slot_locks, contest)
    pool, _ = candidates(contest, proj, base=base, fm=fm, seed=seed)
    if not pool:
        raise Infeasible("no legal lineup for the current rules and exclusions")
    if fm is None:  # no field model: rank by mean
        for lu in pool:
            lu.metrics.update({"mean": lu.mean})
        return {"lineups": sorted(pool, key=lambda lu: -lu.mean)[:n], "pool_size": len(pool), "objective": "mean"}
    ev = sx.score_against_field(pool, keys, _scaled(sims, keys, scale), fm, top_frac=target_top,
                                field_size=contest.field_size)
    chosen = sx.choose_portfolio(ev, n, kind) if n > 1 else sx.choose(ev.lineups, kind)[:1]
    return {"lineups": chosen, "pool_size": len(pool), "objective": sx.objective_metric(kind),
            "best_metrics": chosen[0].metrics, "cash_line_mean": float(np.mean(ev.cash_line)),
            "top_cut_mean": float(np.mean(ev.top_cut))}


def evaluate(lineups: list[Lineup], keys: list[str], sims: np.ndarray, fm: FieldModel | None,
             target_top: float = 0.023) -> None:
    """Fill metrics in place (the last call compares current vs new on the same draws)."""
    if fm is None:
        return
    ev = sx.score_against_field(lineups, keys, sims, fm, top_frac=target_top)
    for lu, e in zip(lineups, ev.lineups):
        lu.metrics.update(e.metrics)


def three_drafts(contest: Contest, proj: dict[str, Projection], keys: list[str], sims: np.ndarray,
                 fm: FieldModel | None, *, target_top: float, seed: int = 0, exclude: set[str] = frozenset(),
                 slot_locks: dict[int, str] | None = None) -> list[Lineup]:
    """Each draft is built alone from the blended numbers under one lens (``exclude``: players who can no
    longer be added, e.g. their game has started)."""
    base = _base(set(exclude), None, slot_locks, contest)
    pool, _ = candidates(contest, proj, base=base, fm=fm, seed=seed)
    if not pool:
        raise Infeasible("no legal draft from the players still available")
    if fm is None:
        top = sorted(pool, key=lambda lu: -lu.mean)[:3]
        return [lu.model_copy(update={"label": f"Draft {'ABC'[i]}"}) for i, lu in enumerate(top)]
    lus = sx.score_against_field(pool, keys, sims, fm, top_frac=target_top, field_size=contest.field_size).lineups
    cash = max(lus, key=lambda lu: (lu.metrics["p_cash"], lu.metrics["mean"]))
    gpp = max(lus, key=lambda lu: (lu.metrics["p_top"], lu.metrics["mean"]))
    near = [lu for lu in lus if lu.metrics["p_top"] >= 0.85 * gpp.metrics["p_top"]] or [gpp]
    lev = min(near, key=lambda lu: (lu.metrics["log_own"], -lu.metrics["p_top"]))
    labels = ("Draft A - cash lens (floor)", "Draft B - tournament lens (P top x%)",
              "Draft C - leverage lens (least owned near the best)")
    return [lu.model_copy(update={"label": lab}, deep=True) for lu, lab in zip((cash, gpp, lev), labels)]


def top_candidates_by_slot(contest: Contest, proj: dict[str, Projection], *, k: int = 3, seed: int = 0,
                           fm: FieldModel | None = None, exclude: set[str] = frozenset(),
                           slot_locks: dict[int, str] | None = None) -> dict[str, list[dict]]:
    """For each slot, the k players most often in the best lineup of a simulated outcome
    (re-uses the sim-optimal lineups of the candidate pool)."""
    _, draws = candidates(contest, proj, base=_base(set(exclude), None, slot_locks, contest), fm=fm, seed=seed)
    rates = sx.rates_from_lineups(contest, draws, {kk: p.mean for kk, p in proj.items()}, top_n=k)
    out: dict[str, list[dict]] = {}
    for r in rates.table:
        if fm is not None:  # leverage: how much more often he is optimal than the field rosters him
            r["ownership"] = round(fm.own(r["player_key"]) / 100.0, 4)
            r["leverage"] = round(r["optimal_rate"] - r["ownership"], 4)
        out.setdefault(r["slot"], []).append(r)
    return out


def format_top3(contest: Contest, top3: dict[str, list[dict]]) -> str:
    lines = ["| Slot | Player | Team | Salary | Mean | Value (pts/$1k) | Optimal rate | Field own | Leverage |",
             "|---|---|---|---:|---:|---:|---:|---:|---:|"]
    for s in dict.fromkeys(x.name for x in contest.rules.slots):
        for r in top3.get(s, []):
            own = f"{r['ownership']:.1%}" if "ownership" in r else "-"
            lev = f"{r['leverage']:+.1%}" if "leverage" in r else "-"
            lines.append(f"| {s} | {r['name']} | {r['team']} | {r['salary']} | {r['mean']} | {r['value']} | "
                         f"{r['optimal_rate']:.1%} | {own} | {lev} |")
    return "\n".join(lines)
