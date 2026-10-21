"""Contest-type-aware lineup selection on top of the simulator and the ILP.

1. Candidates: diverse mean-optimal lineups, optimal lineups on simulated draws (full and
   shrunk halfway to the mean), NFL stacks (QB + 1-2 pass catchers, optional bring-back) and
   the field's most common lineups, de-duplicated.
2. Evaluation: candidates and field lineups are scored on the same S correlated sims
   (incidence matrix @ sims). Each sim's weighted field distribution gives that sim's top-x%
   cut and cash line, hence P(top x%), P(cash), expected percentile, sd and quantiles.
3. Choice: cash maximises P(beat the cash line), GPP maximises P(top x%), h2h maximises
   P(beat one random rival); ties go to the higher mean.
Candidate draws and evaluation draws use different seeds, so a sim-optimal lineup is never
scored on the draw it was optimised for (that would inflate its tournament odds).
"""

from __future__ import annotations

import time
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace

import numpy as np

from dfs.field import (FieldModel, Pruner, build_field, fit_ownership, has_qb_stack, lineup_key, lineup_matrix,
                       point_values, solve_pruned)
from dfs.models import Contest, ContestKind, Lineup, Projection
from dfs.optimize.ilp import Infeasible, SolveOptions, solve
from dfs.sim import simulate

CASH_KINDS = {"cash", "double_up", "fifty_fifty", "50/50", "multiplier"}


def kind_of(kind: ContestKind | str | None) -> str:
    """Normalise a contest kind to 'cash', 'h2h' or 'gpp'."""
    k = (kind.value if isinstance(kind, ContestKind) else str(kind or "gpp")).lower()
    return "cash" if k in CASH_KINDS else "h2h" if k in ("h2h", "head_to_head") else "gpp"


@dataclass
class SelectConfig:
    n_sims: int = 10000  # evaluation sims
    top_frac: float = 0.023  # GPP target: finish in the top x of the field
    cash_quantile: float = 0.5  # cash line = this quantile of the field's scores in each sim
    cash_line: float | None = None  # or a fixed points line
    field_size: int | None = None  # for the duplicate estimate; contest.field_size if None
    n_mean: int = 20  # mean-optimal lineups
    min_unique: int = 2
    n_draws: int = 200  # optimal lineups on full draws (also the optimal-rate sample)
    n_half: int = 60  # optimal lineups on draws shrunk toward the mean
    half_shrink: float = 0.5
    stack_qbs: int = 6
    stack_sizes: tuple[int, ...] = (1, 2)
    bring_backs: tuple[int, ...] = (0, 1)
    n_field_candidates: int = 20
    field_lineups: int = 150
    time_budget_s: float = 240.0
    seed: int = 11


# --------------------------------------------------------------------------- candidates


def _base_opts(base: SolveOptions | None) -> SolveOptions:
    return replace(base or SolveOptions(), workers=1, time_limit_s=min((base or SolveOptions()).time_limit_s, 5.0))


def _can_prune(opts: SolveOptions, team_safe: bool = False) -> bool:
    """Dominance pruning is unsafe when rules can force or forbid dominating players, and
    for stack rules unless the caller keeps every player of the stacked teams."""
    ur = opts.user_rules
    if ur and (ur.lock or ur.exclude or ur.exclude_teams or ur.exclude_games or ur.stack
               or ur.max_from_team or ur.min_salary):
        return False
    return not opts.min_salary and (team_safe or not (opts.qb_stack or opts.bring_back))


def _unconstrained(opts: SolveOptions) -> bool:
    """No user/option rules, so any legal lineup (e.g. a field lineup) is admissible."""
    return _can_prune(opts) and not (opts.lock_keys or opts.exclude_keys or opts.slot_locks or opts.qb_stack
                                     or opts.bring_back)


def _masked(values: np.ndarray, keys: Sequence[str], opts: SolveOptions) -> np.ndarray:
    """Excluded players get -1e6 so they never dominate anyone (the solver drops them anyway)."""
    v = np.asarray(values, dtype=float).copy()
    if opts.exclude_keys:
        v[[j for j, k in enumerate(keys) if k in opts.exclude_keys]] = -1e6
    return v


def _solve(contest: Contest, pruner: Pruner, values: np.ndarray, opts: SolveOptions, keep: set[str],
           label: str, team_safe: bool = False) -> Lineup | None:
    try:
        if _can_prune(opts, team_safe):
            pinned = {c.player_key for c in contest.candidates if c.site_id in set(opts.slot_locks.values())}
            return solve_pruned(contest, pruner, values, opts, keep=keep | set(opts.lock_keys) | pinned, label=label)
        pts = {k: float(v) for k, v in zip(pruner.keys, values) if k not in opts.exclude_keys}
        return solve(contest, pts, opts, label=label)
    except Infeasible:
        return None


def mean_optimal(contest: Contest, points: Mapping[str, float], n: int = 20, min_unique: int = 2,
                 base: SolveOptions | None = None, deadline: float | None = None) -> list[Lineup]:
    """The best ``n`` lineups on mean points, each differing from all earlier ones by ``min_unique``."""
    opts = _base_opts(base)
    out: list[Lineup] = []
    for i in range(n):
        if out and deadline and time.perf_counter() > deadline:
            break
        try:
            out.append(solve(contest, dict(points), replace(opts, avoid=list(opts.avoid) + out, min_unique=min_unique),
                             label=f"mean{i + 1}"))
        except Infeasible:
            break
    return out


def sim_optimal(contest: Contest, keys: Sequence[str], draws: np.ndarray, means: np.ndarray, shrink: float = 1.0,
                base: SolveOptions | None = None, deadline: float | None = None, label: str = "sim") -> list[Lineup]:
    """Optimal lineup on each draw (row of ``draws``), optionally shrunk toward ``means``."""
    opts = _base_opts(base)
    pruner = Pruner(contest, keys)
    out = []
    for i, d in enumerate(draws):
        if out and deadline and time.perf_counter() > deadline:
            break
        vals = _masked(means + shrink * (d.astype(float) - means), keys, opts)
        lu = _solve(contest, pruner, vals, opts, set(), f"{label}{i + 1}")
        if lu is not None:
            out.append(lu)
    return out


def stack_variants(contest: Contest, points: Mapping[str, float], n_qbs: int = 6, sizes: Sequence[int] = (1, 2),
                   bring_backs: Sequence[int] = (0, 1), base: SolveOptions | None = None,
                   deadline: float | None = None) -> list[Lineup]:
    """NFL: for the top QBs by mean, the best lineup with >= s same-team WR/TE and >= b opponents."""
    opts = _base_opts(base)
    players = contest.players
    keys = sorted(k for k in points if k in players)
    qbs = [k for k in keys if "QB" in players[k].positions and k not in opts.exclude_keys]
    qbs = sorted(qbs, key=lambda k: -points[k])[:n_qbs]
    pruner = Pruner(contest, keys)
    vals = _masked(np.array([points[k] for k in keys]), keys, opts)
    out = []
    for qb in qbs:
        team, opp = players[qb].team, players[qb].opponent
        keep = {k for k in keys if players[k].team in (team, opp)}
        for s in sizes:
            for b in bring_backs:
                if out and deadline and time.perf_counter() > deadline:
                    return out
                o = replace(opts, lock_keys=set(opts.lock_keys) | {qb}, qb_stack=s, bring_back=b)
                lu = _solve(contest, pruner, vals, o, keep, f"stack:{team}:{s}+{b}", team_safe=True)
                if lu is not None:
                    out.append(lu)
    return out


def dedupe(lineups: Sequence[Lineup]) -> list[Lineup]:
    seen: set[tuple] = set()
    out = []
    for lu in lineups:
        key = lineup_key(lu)
        if key not in seen:
            seen.add(key)
            out.append(lu)
    return out


# --------------------------------------------------------------------------- evaluation


def _weighted_row_quantiles(sorted_vals: np.ndarray, sorted_w: np.ndarray, qs: Sequence[float]) -> list[np.ndarray]:
    """Per-row weighted quantiles with midpoint plotting positions and linear interpolation."""
    s, n = sorted_vals.shape
    if n == 1:
        return [sorted_vals[:, 0].copy() for _ in qs]
    pos = np.cumsum(sorted_w, axis=1) - 0.5 * sorted_w
    rows = np.arange(s)
    out = []
    for q in qs:
        hi = np.clip((pos < q).sum(axis=1), 1, n - 1)
        lo = hi - 1
        pl, ph = pos[rows, lo], pos[rows, hi]
        t = np.clip((q - pl) / np.maximum(ph - pl, 1e-12), 0.0, 1.0)
        out.append(sorted_vals[rows, lo] + t * (sorted_vals[rows, hi] - sorted_vals[rows, lo]))
    return out


def _row_cdf(sorted_vals: np.ndarray, sorted_w: np.ndarray, x: np.ndarray) -> np.ndarray:
    """Weighted share of each row's field strictly below x (+ half the ties), x shaped (S, C)."""
    s, n = sorted_vals.shape
    base = min(float(sorted_vals.min()), float(x.min()))
    span = max(float(sorted_vals.max()), float(x.max())) - base + 1.0
    off = (np.arange(s, dtype=np.float64) * span)[:, None]
    flat = (sorted_vals.astype(np.float64) - base + off).ravel()
    q = (x.astype(np.float64) - base + off).ravel()
    row0 = (np.arange(s) * n)[:, None]
    left = np.searchsorted(flat, q, side="left").reshape(x.shape) - row0
    right = np.searchsorted(flat, q, side="right").reshape(x.shape) - row0
    cw = np.concatenate([np.zeros((s, 1)), np.cumsum(sorted_w, axis=1)], axis=1)
    return 0.5 * (np.take_along_axis(cw, left, axis=1) + np.take_along_axis(cw, right, axis=1))


@dataclass
class Evaluation:
    lineups: list[Lineup]  # copies with metrics filled
    scores: np.ndarray  # (S, n_lineups) float32 points per sim
    top_cut: np.ndarray  # (S,) field top-x% score per sim
    cash_line: np.ndarray  # (S,) cash line per sim
    top_frac: float


def score_against_field(lineups: Sequence[Lineup], keys: Sequence[str], sims: np.ndarray, fm: FieldModel, *,
                        top_frac: float = 0.023, cash_quantile: float = 0.5, cash_line: float | None = None,
                        field_size: int | None = None, chunk: int = 2500) -> Evaluation:
    """Score lineups and the field on the same sims and fill each lineup's metrics.

    Metrics: mean, sd, p10/p50/p90, p_top (P(score > that sim's field top-x% cut)), p_cash,
    exp_pct (mean percentile vs the field = P(beat one random rival) x 100), sum_own,
    log_own (sum of log ownership fractions: the duplication proxy), field_share (pool
    probability a rival entered this exact lineup), dup_indep and dup_pool (expected
    duplicates under independent picks and from the pool share, given ``field_size``).
    """
    if not 0 < top_frac < 1:
        raise ValueError("top_frac must be in (0, 1)")
    x = np.ascontiguousarray(sims, dtype=np.float32)
    cm = lineup_matrix(lineups, keys)
    fmat = fm.matrix(keys)
    w = np.asarray(fm.weights, dtype=np.float64)
    s_total = x.shape[0]
    scores = np.empty((s_total, len(lineups)), dtype=np.float32)
    top_cut = np.empty(s_total)
    cash = np.empty(s_total)
    pct_sum = np.zeros(len(lineups))
    for a in range(0, s_total, chunk):  # chunks bound memory for big S x field
        xs = x[a:a + chunk]
        cs = xs @ cm.T
        fs = xs @ fmat.T
        order = np.argsort(fs, axis=1)
        fsorted = np.take_along_axis(fs, order, axis=1)
        wsorted = w[order]
        q_top, q_cash = _weighted_row_quantiles(fsorted, wsorted, [1.0 - top_frac, cash_quantile])
        scores[a:a + chunk] = cs
        top_cut[a:a + chunk] = q_top
        cash[a:a + chunk] = q_cash if cash_line is None else cash_line
        pct_sum += _row_cdf(fsorted, wsorted, cs).sum(axis=0)

    def beat(line: np.ndarray) -> np.ndarray:
        l2 = line[:, None]
        return (scores > l2).mean(axis=0) + 0.5 * (scores == l2).mean(axis=0)

    p_top, p_cash = beat(top_cut), beat(cash)
    qs = np.percentile(scores, [10, 50, 90], axis=0)
    mean, sd = scores.mean(axis=0, dtype=np.float64), scores.std(axis=0, dtype=np.float64)
    pool_share = defaultdict(float)
    for lu, wt in zip(fm.lineups, fm.weights):
        pool_share[lineup_key(lu)] += float(wt)
    n_field = field_size or 0
    out = []
    for i, lu in enumerate(lineups):
        own = [max(fm.own(k), 0.1) / 100.0 for k in set(lu.player_keys)]
        log_own = float(np.sum(np.log(own)))
        share = pool_share.get(lineup_key(lu), 0.0)
        metrics = {**lu.metrics,
                   "mean": float(mean[i]), "sd": float(sd[i]), "p10": float(qs[0, i]), "p50": float(qs[1, i]),
                   "p90": float(qs[2, i]), "p_top": float(p_top[i]), "p_cash": float(p_cash[i]),
                   "p_top_se": float(np.sqrt(max(p_top[i] * (1 - p_top[i]), 1e-12) / s_total)),
                   "exp_pct": float(100.0 * pct_sum[i] / s_total), "sum_own": float(100 * sum(own)),
                   "log_own": log_own, "field_share": share}
        if n_field:
            metrics["dup_indep"] = float((n_field - 1) * np.exp(log_own))
            metrics["dup_pool"] = float((n_field - 1) * share)
        out.append(lu.model_copy(update={"metrics": metrics}))
    return Evaluation(lineups=out, scores=scores, top_cut=top_cut, cash_line=cash, top_frac=top_frac)


def evaluate(lineups: Sequence[Lineup], keys: Sequence[str], sims: np.ndarray, fm: FieldModel, **kw) -> list[Lineup]:
    """Copies of ``lineups`` with field-relative metrics (see :func:`score_against_field`)."""
    return score_against_field(lineups, keys, sims, fm, **kw).lineups


def objective_metric(kind: ContestKind | str) -> str:
    return {"cash": "p_cash", "h2h": "exp_pct", "gpp": "p_top"}[kind_of(kind)]


def choose(evaluated: Sequence[Lineup], kind: ContestKind | str) -> list[Lineup]:
    """Rank evaluated lineups by the contest objective (ties by mean); sets metrics['rank']."""
    key = objective_metric(kind)
    ranked = sorted(evaluated, key=lambda lu: (-lu.metrics[key], -lu.metrics["mean"]))
    for i, lu in enumerate(ranked):
        lu.metrics["rank"] = float(i + 1)
    return ranked


def choose_portfolio(ev: Evaluation, n: int, kind: ContestKind | str) -> list[Lineup]:
    """Greedy multi-entry set. GPP: each pick maximises the gain in P(any entry in the top x%)
    (Hunter-Vielma-Zaman style diversification); cash/h2h: the n best singles (payouts add)."""
    if kind_of(kind) != "gpp":
        return choose(ev.lineups, kind)[:n]
    hits = ev.scores > ev.top_cut[:, None]
    covered = np.zeros(hits.shape[0], dtype=bool)
    mean = np.array([lu.metrics["mean"] for lu in ev.lineups])
    picked: list[int] = []
    for _ in range(min(n, hits.shape[1])):
        gain = (hits & ~covered[:, None]).mean(axis=0)
        gain[picked] = -1.0
        j = int(np.lexsort((-mean, -gain))[0])
        picked.append(j)
        covered |= hits[:, j]
    out = []
    for r, j in enumerate(picked):
        lu = ev.lineups[j]
        out.append(lu.model_copy(update={"metrics": {**lu.metrics, "portfolio_rank": float(r + 1)}}))
    if out:
        out[-1].metrics["portfolio_p_any_top"] = float(covered.mean())
    return out


# --------------------------------------------------------------------------- optimal rates


@dataclass
class OptimalRates:
    n: int  # sims solved
    player: dict[str, float]  # share of sims in which the player is in the optimal lineup (sums to roster size)
    slot: dict[str, dict[str, float]]  # slot name -> player -> share of sims he fills a slot of that name
    table: list[dict]  # top candidates per slot name


def rates_from_lineups(contest: Contest, lineups: Sequence[Lineup], points: Mapping[str, float],
                       top_n: int = 3) -> OptimalRates:
    n = len(lineups)
    if n == 0:
        return OptimalRates(0, {}, {}, [])
    overall: Counter = Counter()
    by_slot: dict[str, Counter] = defaultdict(Counter)
    for lu in lineups:
        overall.update(set(lu.player_keys))
        for s in lu.slots:
            by_slot[s.slot][s.player_key] += 1
    player = {k: c / n for k, c in overall.most_common()}
    slot = {g: {k: c / n for k, c in cnt.most_common()} for g, cnt in by_slot.items()}
    multiplied = contest.rules.multiplier_rows or any(s.points_multiplier != 1 for s in contest.rules.slots)
    table = []
    for g in dict.fromkeys(s.name for s in contest.rules.slots):
        mult = next(s.points_multiplier for s in contest.rules.slots if s.name == g)
        rows = {}
        for c in contest.candidates:
            if g in c.eligible_slots and c.player_key in points:
                rows.setdefault(c.player_key, c)
        rate = slot.get(g, {}) if multiplied else player
        best = sorted(rows, key=lambda k, rate=rate: (-rate.get(k, 0.0), -points[k]))[:top_n]
        for r, k in enumerate(best):
            p, c = contest.players[k], rows[k]
            table.append({"slot": g, "rank": r + 1, "player_key": k, "name": p.name, "team": p.team,
                          "positions": "/".join(p.positions), "salary": c.salary,
                          "optimal_rate": round(player.get(k, 0.0), 4),
                          "slot_rate": round(slot.get(g, {}).get(k, 0.0), 4),
                          "mean": round(points[k] * mult, 2),
                          "value": round(points[k] * mult / (c.salary / 1000.0), 3) if c.salary else None})
    return OptimalRates(n=n, player=player, slot=slot, table=table)


def optimal_rates(contest: Contest, projections: Mapping[str, Projection], n: int = 200, seed: int = 101,
                  top_n: int = 3, keys: Sequence[str] | None = None, sims: np.ndarray | None = None,
                  base: SolveOptions | None = None, time_budget_s: float = 60.0) -> OptimalRates:
    """Solve the optimal lineup in each of ``n`` sims and count how often each player (and
    each player in each slot) is in it: the "top 3 per roster slot" table."""
    if sims is None or keys is None:
        keys, sims = simulate(projections, contest.players, contest.rules.sport, n_sims=n, seed=seed)
    means = np.array([projections[k].mean for k in keys])
    lus = sim_optimal(contest, keys, sims[:n], means, 1.0, base, time.perf_counter() + time_budget_s)
    return rates_from_lineups(contest, lus, point_values(projections), top_n)


# --------------------------------------------------------------------------- summary + pipeline


def stack_shape(contest: Contest, lineup: Lineup) -> dict:
    """QB team, same-team pass catchers and opponents of the QB in a lineup (NFL)."""
    players = contest.players
    keys = lineup.player_keys
    qbs = [k for k in keys if "QB" in players[k].positions]
    if not qbs:
        return {}
    qb = max(qbs, key=lambda k: next(s.points_multiplier for s in lineup.slots if s.player_key == k))
    team, opp = players[qb].team, players[qb].opponent
    mates = [k for k in keys if k != qb and players[k].team == team and {"WR", "TE"} & set(players[k].positions)]
    back = [k for k in keys if opp and players[k].team == opp]
    return {"qb": qb, "team": team, "stack": len(mates), "bring_back": len(back),
            "label": f"QB+{len(mates)} {team}" + (f" +{len(back)} {opp}" if back else "")}


def strategy_summary(contest: Contest, ranked: Sequence[Lineup], fm: FieldModel, kind: ContestKind | str,
                     cfg: SelectConfig | None = None, rates: OptimalRates | None = None,
                     timings: Mapping[str, float] | None = None, n_alternatives: int = 5) -> dict:
    """Plain dict for reports: objective, chosen lineup and runners-up, field, timings."""
    cfg = cfg or SelectConfig()
    k = kind_of(kind)
    metric = objective_metric(k)
    players = contest.players

    def brief(lu: Lineup) -> dict:
        m = lu.metrics
        return {"label": lu.label, "players": [players[s.player_key].name for s in lu.slots],
                "salary": lu.salary, "objective": round(m[metric], 4),
                **{x: round(m[x], 4) for x in ("mean", "sd", "p10", "p90", "p_top", "p_cash", "exp_pct",
                                              "sum_own", "log_own") if x in m},
                **({"stack": stack_shape(contest, lu).get("label")} if contest.rules.sport.lower() == "nfl" else {})}

    sources = Counter(lu.label.split(":")[0].rstrip("0123456789") for lu in ranked)
    best = ranked[0] if ranked else None
    out = {
        "kind": k, "objective": metric,
        "objective_text": {"cash": f"maximise P(beat the cash line: field q{cfg.cash_quantile:.2f}"
                                   + (f" or {cfg.cash_line} pts" if cfg.cash_line else "") + ")",
                           "gpp": f"maximise P(finish in the top {100 * cfg.top_frac:.1f}%)",
                           "h2h": "maximise P(beat one random rival)"}[k],
        "n_candidates": len(ranked), "candidate_sources": dict(sources), "n_sims": cfg.n_sims,
        "chosen": brief(best) if best else None,
        "alternatives": [brief(lu) for lu in ranked[1:1 + n_alternatives]],
        "field": {"n_lineups": len(fm.lineups), "noise_sigma": round(fm.noise, 4),
                  "top_owned": [(players[p].name, round(v, 1)) for p, v in fm.top_owned(8)],
                  **{x: round(fm.stats[x], 4) for x in ("max_own_pct", "top3_own_pct", "share_salary_ge_98pct",
                                                        "share_qb_stacked", "ess", "fit_max_abs_err_pct",
                                                        "noise_capped")
                     if x in fm.stats}},
        "timings_s": {k2: round(v, 2) for k2, v in (timings or {}).items()},
    }
    if best is not None and k == "gpp":
        out["chosen_vs_mean_optimal"] = next(
            ({"label": lu.label, "mean": round(lu.metrics["mean"], 2), "p_top": round(lu.metrics["p_top"], 4)}
             for lu in ranked if lu.label == "mean1"), None)
    if rates is not None:
        out["optimal_rates_n"] = rates.n
        out["top_by_slot"] = [{x: r[x] for x in ("slot", "rank", "name", "optimal_rate", "mean", "value")}
                              for r in rates.table]
    return out


@dataclass
class Selection:
    kind: str
    ranked: list[Lineup]
    field: FieldModel
    rates: OptimalRates
    summary: dict
    timings: dict[str, float]
    evaluation: Evaluation | None = field(default=None, repr=False)


def select_lineups(contest: Contest, projections: Mapping[str, Projection], *, kind: ContestKind | str | None = None,
                   cfg: SelectConfig | None = None, fm: FieldModel | None = None,
                   ownership: Mapping[str, float] | None = None, base: SolveOptions | None = None) -> Selection:
    """Field model -> candidates -> evaluation over correlated sims -> ranked choice."""
    cfg = cfg or SelectConfig()
    k = kind_of(kind or contest.kind)
    t0 = time.perf_counter()
    budget = cfg.time_budget_s
    timings: dict[str, float] = {}
    sport = contest.rules.sport
    base = _base_opts(base)

    def lap(name: str, start: float) -> None:
        timings[name] = time.perf_counter() - start

    t = time.perf_counter()
    if fm is None:
        fm = build_field(contest, projections, n_lineups=cfg.field_lineups, ownership=ownership,
                         target_top3=0.45 if k == "cash" else 0.35, seed=cfg.seed + 7,
                         time_budget_s=0.25 * budget)
    elif ownership:
        fm = fit_ownership(fm, contest, ownership, projections=projections)
    lap("field", t)

    t = time.perf_counter()
    gkeys, gsims = simulate(projections, contest.players, sport, n_sims=max(cfg.n_draws, cfg.n_half),
                            seed=cfg.seed + 1)
    means = np.array([projections[x].mean for x in gkeys])
    draws = sim_optimal(contest, gkeys, gsims[:cfg.n_draws], means, 1.0, base, t + 0.25 * budget)
    half = sim_optimal(contest, gkeys, gsims[:cfg.n_half], means, cfg.half_shrink, base, t + 0.35 * budget, "half")
    lap("sim_optimal", t)

    t = time.perf_counter()
    pts = point_values(projections)
    cands = mean_optimal(contest, pts, cfg.n_mean, cfg.min_unique, base, t + 0.1 * budget)
    lap("mean_optimal", t)
    t = time.perf_counter()
    if sport.lower() in ("nfl", "cfb") and cfg.stack_qbs:
        cands += stack_variants(contest, pts, cfg.stack_qbs, cfg.stack_sizes, cfg.bring_backs, base, t + 0.1 * budget)
    lap("stacks", t)
    if cfg.n_field_candidates and _unconstrained(base):
        order = np.argsort(-fm.weights)[:cfg.n_field_candidates]
        cands += [fm.lineups[i].model_copy(update={"label": f"field{r + 1}"}) for r, i in enumerate(order)]
    cands = dedupe(cands + half + draws)

    t = time.perf_counter()
    keys, sims = simulate(projections, contest.players, sport, n_sims=cfg.n_sims, seed=cfg.seed)
    lap("simulate", t)
    t = time.perf_counter()
    ev = score_against_field(cands, keys, sims, fm, top_frac=cfg.top_frac, cash_quantile=cfg.cash_quantile,
                             cash_line=cfg.cash_line, field_size=cfg.field_size or contest.field_size)
    ranked = choose(ev.lineups, k)
    lap("evaluate", t)
    rates = rates_from_lineups(contest, draws, pts)
    timings["total"] = time.perf_counter() - t0
    summary = strategy_summary(contest, ranked, fm, k, cfg, rates, timings)
    return Selection(kind=k, ranked=ranked, field=fm, rates=rates, summary=summary, timings=timings, evaluation=ev)


__all__ = ["SelectConfig", "Selection", "Evaluation", "OptimalRates", "select_lineups", "mean_optimal", "sim_optimal",
           "stack_variants", "dedupe", "evaluate", "score_against_field", "choose", "choose_portfolio",
           "optimal_rates", "rates_from_lineups", "strategy_summary", "stack_shape", "kind_of", "has_qb_stack"]
