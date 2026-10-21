"""Contest-objective lineup choice from correlated simulations.

- cash: maximise P(score >= cash line), the cash line being the simulated field's median.
- tournament: maximise P(score >= the field's top-x% threshold) in the same simulated world.
Candidates come from the solver (mean-optimal set, optimal lineups of individual simulated
outcomes, and correlation stacks); every candidate is then scored in every simulation, so
"P(top 2.3%)" is a count, not a guess. Three drafts, each built alone under a different
lens, and a top-3-per-slot table (how often each player is in the best lineup of a
simulated outcome) are produced for the strategist.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import numpy as np

from dfs.field import FieldModel, build_field, lineup_key, lineup_matrix
from dfs.models import Contest, ContestKind, Lineup, Projection, UserRules
from dfs.optimize.ilp import Infeasible, SolveOptions, solve, solve_many

STACK_SPORTS = {"nfl", "cfb"}


def public_points(contest: Contest, proj: dict[str, Projection], w_site: float = 0.5) -> dict[str, float]:
    """What rivals are assumed to project: our mean blended with the site's own average.

    Rivals optimising exactly our numbers would make us look worse than we are; rivals with no
    information would flatter us. Mixing in the site average gives them different, public
    information. The resulting P(top x%) is still a model figure, not a promise.
    """
    out = {}
    for k, p in proj.items():
        avg = contest.players[k].site_avg if k in contest.players else None
        out[k] = (1 - w_site) * p.mean + w_site * avg if avg and avg > 0 else p.mean
    return out


def field_for(contest: Contest, proj: dict[str, Projection], *, kind: ContestKind, seed: int = 23,
              n_lineups: int = 150, time_budget_s: float = 60.0) -> FieldModel | None:
    try:
        return build_field(contest, proj, n_lineups=n_lineups, seed=seed, time_budget_s=time_budget_s,
                           public_points=public_points(contest, proj))
    except Infeasible:
        return None


def scores(lineups: list[Lineup], keys: list[str], sims: np.ndarray) -> np.ndarray:
    """(n_lineups, n_sims) simulated lineup scores."""
    if not lineups:
        return np.zeros((0, sims.shape[0]), dtype=np.float32)
    return lineup_matrix(lineups, keys, strict=False) @ sims.T


def field_lines(fm: FieldModel | None, keys: list[str], sims: np.ndarray, target_top: float) -> tuple[np.ndarray, np.ndarray]:
    """Per-simulation (top-x% threshold, median) of the weighted field's scores."""
    if fm is None or not fm.lineups:
        n = sims.shape[0]
        return np.full(n, np.inf, dtype=np.float32), np.zeros(n, dtype=np.float32)
    f = scores(fm.lineups, keys, sims)  # F x S
    w = np.asarray(fm.weights, dtype=np.float64)
    order = np.argsort(f, axis=0)
    cw = np.cumsum(w[order], axis=0)  # weights accumulated in ascending score order, per sim
    cols = np.arange(f.shape[1])

    def wq(q: float) -> np.ndarray:
        idx = np.argmax(cw >= q * cw[-1], axis=0)
        return f[order[idx, cols], cols]

    return wq(1.0 - target_top).astype(np.float32), wq(0.5).astype(np.float32)


def _solve_quiet(contest: Contest, points: dict[str, float], opts: SolveOptions, label: str) -> Lineup | None:
    try:
        return solve(contest, points, opts, label=label)
    except Infeasible:
        return None


def candidate_pool(contest: Contest, proj: dict[str, Projection], keys: list[str], sims: np.ndarray, *,
                   exclude: set[str] = frozenset(), user_rules: UserRules | None = None, n_mean: int = 12,
                   n_sim: int = 48, seed: int = 0, workers: int = 4,
                   slot_locks: dict[int, str] | None = None) -> list[Lineup]:
    means = {k: p.mean for k, p in proj.items() if k not in exclude}
    base = SolveOptions(exclude_keys=set(exclude), user_rules=user_rules, time_limit_s=10, workers=2,
                        slot_locks=dict(slot_locks or {}))
    pool = solve_many(contest, means, n_mean, SolveOptions(**{**base.__dict__, "min_unique": 1}), label="mean")
    rng = np.random.default_rng(seed)
    draws = rng.choice(sims.shape[0], size=min(n_sim, sims.shape[0]), replace=False)
    jobs = []
    for i, d in enumerate(draws):
        pts = {k: float(sims[d, j]) for j, k in enumerate(keys) if k in means}
        jobs.append((pts, SolveOptions(**{**base.__dict__, "workers": 1}), f"sim{i}"))
    if contest.rules.sport in STACK_SPORTS and contest.rules.game_type == "classic":
        jobs.append((means, SolveOptions(**{**base.__dict__, "qb_stack": 1}), "stack1"))
        jobs.append((means, SolveOptions(**{**base.__dict__, "qb_stack": 2}), "stack2"))
        jobs.append((means, SolveOptions(**{**base.__dict__, "qb_stack": 2, "bring_back": 1}), "stack2+1"))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        pool += [lu for lu in ex.map(lambda j: _solve_quiet(contest, *j), jobs) if lu is not None]
    seen, out = set(), []
    for lu in pool:
        key = lineup_key(lu)
        if key not in seen:
            seen.add(key)
            lu.mean = round(sum(means.get(s.player_key, 0.0) * s.points_multiplier for s in lu.slots), 3)
            out.append(lu)
    return out


def evaluate(pool: list[Lineup], keys: list[str], sims: np.ndarray, top_line: np.ndarray,
             cash_line: np.ndarray, fm: FieldModel | None = None) -> np.ndarray:
    """Fills lineup.metrics and returns the (n_lineups, n_sims) score matrix."""
    sc = scores(pool, keys, sims)
    for i, lu in enumerate(pool):
        s = sc[i]
        lu.metrics.update({
            "sim_mean": float(s.mean()), "sim_sd": float(s.std()), "p90": float(np.quantile(s, 0.9)),
            "p_top": float((s >= top_line).mean()), "p_cash": float((s >= cash_line).mean()),
        })
        if fm is not None:
            own = [max(fm.own(k), 0.1) / 100 for k in lu.player_keys]
            lu.metrics["sum_log_own"] = float(np.sum(np.log(own)))
            lu.metrics["field_share"] = fm.share_of(lu)
    return sc


def _rank_key(kind: ContestKind):
    if kind == ContestKind.CASH:
        return lambda lu: (lu.metrics["p_cash"], lu.metrics["sim_mean"])
    return lambda lu: (lu.metrics["p_top"], lu.metrics["sim_mean"])


def best_lineups(contest: Contest, proj: dict[str, Projection], keys: list[str], sims: np.ndarray,
                 fm: FieldModel | None, *, kind: ContestKind, target_top: float, n: int = 1,
                 exclude: set[str] = frozenset(), user_rules: UserRules | None = None, seed: int = 0,
                 scale: dict[str, float] | None = None, slot_locks: dict[int, str] | None = None) -> dict[str, Any]:
    """Best lineup(s) for the contest objective. ``scale`` multiplies a player's simulated points
    (the backup reviewer's bounded adjustments)."""
    s = sims
    if scale:
        s = sims.copy()
        col = {k: j for j, k in enumerate(keys)}
        for k, f in scale.items():
            if k in col and f != 1.0:
                s[:, col[k]] *= f
    sims_use = s
    top_line, cash_line = field_lines(fm, keys, sims_use, target_top)
    pool = candidate_pool(contest, proj, keys, sims_use, exclude=exclude, user_rules=user_rules, seed=seed,
                          slot_locks=slot_locks)
    if not pool:
        raise Infeasible("no legal lineup for the current rules and exclusions")
    sc = evaluate(pool, keys, sims_use, top_line, cash_line, fm)
    order = sorted(range(len(pool)), key=lambda i: _rank_key(kind)(pool[i]), reverse=True)
    chosen = [order[0]]
    if n > 1:  # portfolio: each next lineup adds the most P(at least one hits the target)
        hit = (sc >= (cash_line if kind == ContestKind.CASH else top_line)).astype(bool)
        covered = hit[order[0]].copy()
        while len(chosen) < min(n, len(pool)):
            gains = [(np.logical_or(covered, hit[i]).mean(), pool[i].metrics["sim_mean"], i)
                     for i in order if i not in chosen]
            _, _, best = max(gains)
            chosen.append(best)
            covered |= hit[best]
    lus = [pool[i] for i in chosen]
    return {"lineups": lus, "pool_size": len(pool),
            "objective": "p_cash" if kind == ContestKind.CASH else "p_top",
            "best_metrics": lus[0].metrics, "cash_line_mean": float(np.mean(cash_line)),
            "top_line_mean": float(np.mean(top_line[np.isfinite(top_line)])) if np.isfinite(top_line).any() else None}


def three_drafts(contest: Contest, proj: dict[str, Projection], keys: list[str], sims: np.ndarray,
                 fm: FieldModel | None, *, target_top: float, seed: int = 0) -> list[Lineup]:
    """Each draft is built alone from the blended numbers under one lens."""
    top_line, cash_line = field_lines(fm, keys, sims, target_top)
    pool = candidate_pool(contest, proj, keys, sims, seed=seed)
    evaluate(pool, keys, sims, top_line, cash_line, fm)
    cash = max(pool, key=lambda lu: (lu.metrics["p_cash"], lu.metrics["sim_mean"]))
    gpp = max(pool, key=lambda lu: (lu.metrics["p_top"], lu.metrics["sim_mean"]))
    # Leverage lens: among lineups within 85% of the best P(top), the least-owned (most unique).
    best = gpp.metrics["p_top"]
    near = [lu for lu in pool if lu.metrics["p_top"] >= 0.85 * best] or [gpp]
    lev = min(near, key=lambda lu: (lu.metrics.get("sum_log_own", 0.0), -lu.metrics["p_top"]))
    out = []
    for lu, label in ((cash, "Draft A — cash lens (floor)"), (gpp, "Draft B — tournament lens (P top %)"),
                      (lev, "Draft C — leverage lens (least owned near the best)")):
        out.append(lu.model_copy(update={"label": label}, deep=True))
    return out


def top_candidates_by_slot(contest: Contest, proj: dict[str, Projection], keys: list[str], sims: np.ndarray,
                           *, n_draws: int = 200, k: int = 3, seed: int = 1, workers: int = 4) -> dict[str, list[dict]]:
    """For each slot, the k players most often in the best lineup of a simulated outcome."""
    rng = np.random.default_rng(seed)
    draws = rng.choice(sims.shape[0], size=min(n_draws, sims.shape[0]), replace=False)
    usable = set(proj)

    def one(d):
        pts = {kk: float(sims[d, j]) for j, kk in enumerate(keys) if kk in usable}
        return _solve_quiet(contest, pts, SolveOptions(time_limit_s=5, workers=1), "")

    with ThreadPoolExecutor(max_workers=workers) as ex:
        sols = [lu for lu in ex.map(one, draws) if lu is not None]
    by_slot: dict[str, Counter] = defaultdict(Counter)
    for lu in sols:
        for s in lu.slots:
            by_slot[s.slot][s.player_key] += 1
    sal = {}
    for c in contest.candidates:
        sal.setdefault(c.player_key, c.salary)
    out = {}
    for slot, cnt in by_slot.items():
        n_slot = sum(1 for s in contest.rules.slots if s.name == slot)
        rows = []
        for key, n in cnt.most_common(k):
            p = proj.get(key)
            rows.append({"player_key": key, "optimal_rate": round(n / max(len(sols), 1) / n_slot, 3),
                         "mean": round(p.mean, 2) if p else None, "p90": round(p.p90, 2) if p else None,
                         "salary": sal.get(key)})
        out[slot] = rows
    return out


def format_top3(contest: Contest, top3: dict[str, list[dict]]) -> str:
    lines = ["| Slot | Player | Team | Salary | Mean | p90 | Optimal rate |", "|---|---|---|---:|---:|---:|---:|"]
    for s in dict.fromkeys(x.name for x in contest.rules.slots):
        for r in top3.get(s, []):
            p = contest.players.get(r["player_key"])
            lines.append(f"| {s} | {p.name if p else r['player_key']} | {p.team if p else ''} | {r['salary']} | "
                         f"{r['mean']} | {r['p90']} | {r['optimal_rate']:.1%} |")
    return "\n".join(lines)
