"""Opponent field model for when real ownership is unknown (or only partly known).

Rivals are modelled as optimisers of noisy "public" projections: each field lineup is the
ILP optimum of ``mean * exp(sigma*z - sigma^2/2)`` with z iid N(0,1) per player and lineup,
and a share of NFL lineups is forced to stack its QB. sigma is calibrated on small pilot
pools so the pool's implied ownership has a realistic concentration (by default the three
most-owned players average 35%), capped at 0.5 so small slates and showdowns keep a
near-optimal, more concentrated field. A given ownership table is matched by first adding
lineups that contain under-covered players and then reweighting the pool by minimum
relative entropy (w_i proportional to w0_i * exp(lambda . a_i)).
"""

from __future__ import annotations

import time
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace

import numpy as np
from scipy.optimize import minimize
from scipy.special import logsumexp

from dfs.models import Contest, Lineup, Projection
from dfs.optimize.ilp import Infeasible, SolveOptions, solve

STACK_SPORTS = {"nfl", "cfb"}


# --------------------------------------------------------------------------- helpers


def point_values(projections: Mapping[str, Projection | float]) -> dict[str, float]:
    """Projection means (or plain floats) as a points dict."""
    return {k: float(v.mean if isinstance(v, Projection) else v) for k, v in projections.items()}


def lineup_key(lineup: Lineup) -> tuple:
    """Identity for de-duplication: the set of (player, points multiplier)."""
    return tuple(sorted((s.player_key, s.points_multiplier) for s in lineup.slots))


def lineup_matrix(lineups: Sequence[Lineup], keys: Sequence[str], strict: bool = True) -> np.ndarray:
    """(n_lineups, n_keys) float32 incidence matrix carrying slot multipliers (CPT 1.5).

    ``strict`` raises on a lineup player missing from ``keys``; otherwise he is skipped.
    """
    idx = {k: j for j, k in enumerate(keys)}
    m = np.zeros((len(lineups), len(keys)), dtype=np.float32)
    for i, lu in enumerate(lineups):
        for s in lu.slots:
            j = idx.get(s.player_key)
            if j is None:
                if strict:
                    raise KeyError(f"lineup player {s.player_key!r} has no simulated points")
                continue
            m[i, j] += s.points_multiplier
    return m


def implied_ownership(lineups: Sequence[Lineup], weights: np.ndarray) -> dict[str, float]:
    """Percent of the field rostering each player (sums to roster_size * 100)."""
    own: dict[str, float] = defaultdict(float)
    for lu, w in zip(lineups, weights):
        for k in set(lu.player_keys):
            own[k] += 100.0 * float(w)
    return dict(sorted(own.items(), key=lambda kv: -kv[1]))


def has_qb_stack(contest: Contest, lineup: Lineup) -> bool:
    players = contest.players
    keys = lineup.player_keys
    qbs = [k for k in keys if "QB" in players[k].positions]
    return any(players[o].team == players[q].team and {"WR", "TE"} & set(players[o].positions)
               for q in qbs for o in keys if o != q)


class Pruner:
    """Drops players that cannot be in an optimal lineup for a given points vector.

    A row is dominated when at least k other rows with the same eligible slots cost no more
    and score at least as much (one strictly); k = roster slots the row can fill + slack.
    Exact for plain cap/slot problems; the slack covers team/game rules in practice. A
    player survives if any of his rows (CPT/FLEX) survives.
    """

    def __init__(self, contest: Contest, keys: Sequence[str]):
        self.keys = list(keys)
        idx = {k: j for j, k in enumerate(self.keys)}
        slot_count = Counter(s.name for s in contest.rules.slots)
        groups: dict[frozenset, list] = defaultdict(list)
        for c in contest.candidates:
            if c.player_key in idx:
                groups[frozenset(c.eligible_slots)].append((idx[c.player_key], c.salary))
        self._groups = []
        for slots, rows in groups.items():
            k = sum(slot_count[s] for s in slots)
            if k == 0:
                continue
            pi = np.array([r[0] for r in rows])
            sal = np.array([r[1] for r in rows], dtype=float)
            self._groups.append((pi, sal[None, :] <= sal[:, None], sal[None, :] < sal[:, None], k))

    def keep_mask(self, values: np.ndarray, slack: int = 1) -> np.ndarray:
        keep = np.zeros(len(self.keys), dtype=bool)
        for pi, sal_le, sal_lt, k in self._groups:
            p = values[pi]
            ge, gt = p[None, :] >= p[:, None], p[None, :] > p[:, None]
            dominated_by = (sal_le & ge & (sal_lt | gt)).sum(axis=1)
            keep[pi[dominated_by < k + slack]] = True
        return keep

    def points(self, values: np.ndarray, slack: int = 1, keep: Iterable[str] = ()) -> dict[str, float]:
        mask = self.keep_mask(values, slack)
        out = {k: float(values[j]) for j, k in enumerate(self.keys) if mask[j]}
        for k in keep:
            if k in self.keys:
                out[k] = float(values[self.keys.index(k)])
        return out


def solve_pruned(contest: Contest, pruner: Pruner, values: np.ndarray, opts: SolveOptions,
                 keep: Iterable[str] = (), slack: int = 1, label: str = "") -> Lineup:
    """Solve on ``values`` (aligned with ``pruner.keys``) after dominance pruning.

    Falls back to the unpruned problem if pruning made the rules infeasible.
    """
    try:
        return solve(contest, pruner.points(values, slack=slack, keep=keep), opts, label=label)
    except Infeasible:
        return solve(contest, {k: float(v) for k, v in zip(pruner.keys, values)}, opts, label=label)


# --------------------------------------------------------------------------- model


@dataclass
class FieldModel:
    lineups: list[Lineup]  # unique field lineups
    weights: np.ndarray  # probability of each lineup, sums to 1
    ownership: dict[str, float]  # player_key -> percent of field lineups, sums to roster_size*100
    noise: float  # calibrated sigma of the multiplicative public-projection noise
    stats: dict[str, float] = field(default_factory=dict)
    slot_ownership: dict[str, dict[str, float]] = field(default_factory=dict)  # slot name -> key -> pct
    generator: _Generator | None = field(default=None, repr=False)  # re-used to add lineups in fits

    def matrix(self, keys: Sequence[str]) -> np.ndarray:
        return lineup_matrix(self.lineups, keys)

    def own(self, key: str) -> float:
        return self.ownership.get(key, 0.0)

    def share_of(self, lineup: Lineup) -> float:
        """Pool probability that a random rival entered exactly this lineup."""
        key = lineup_key(lineup)
        return float(sum(w for lu, w in zip(self.lineups, self.weights) if lineup_key(lu) == key))

    def top_owned(self, n: int = 10) -> list[tuple[str, float]]:
        return list(self.ownership.items())[:n]


class _Generator:
    """Solves field lineups on noisy public projections (shared by build and ownership fit)."""

    def __init__(self, contest: Contest, base: Mapping[str, float], stack_share: float, seed: int,
                 solve_time_s: float = 2.0):
        self.contest = contest
        self.keys = sorted(k for k in base if k in contest.players)
        self.base = np.array([max(float(base[k]), 0.0) for k in self.keys])
        self.pruner = Pruner(contest, self.keys)
        self.rng = np.random.default_rng(seed)
        can_stack = contest.rules.sport.lower() in STACK_SPORTS and any(
            "QB" in contest.players[k].positions for k in self.keys)
        self.stack_share = stack_share if can_stack else 0.0
        self.solve_time_s = solve_time_s
        self.n_solves = 0

    def draws(self, n: int) -> tuple[np.ndarray, np.ndarray]:
        z = self.rng.standard_normal((n, len(self.keys)))
        stack = self.rng.random(n) < self.stack_share
        return z, stack

    def solve_one(self, z: np.ndarray, sigma: float, stack: bool, lock: str | None = None,
                  tilt: np.ndarray | None = None) -> Lineup | None:
        vals = self.base * np.exp(sigma * z - 0.5 * sigma * sigma)
        if tilt is not None:
            vals = vals * tilt
        keep = [lock] if lock else []
        opts = SolveOptions(qb_stack=1 if stack else 0, lock_keys={lock} if lock else set(),
                            workers=1, time_limit_s=self.solve_time_s)
        self.n_solves += 1
        try:
            return solve_pruned(self.contest, self.pruner, vals, opts, keep=keep, label="field")
        except Infeasible:
            if not stack:
                return None
            try:  # a forced stack can be impossible (e.g. no pass catcher left): drop it
                return solve_pruned(self.contest, self.pruner, vals, replace(opts, qb_stack=0), keep=keep,
                                    label="field")
            except Infeasible:
                return None

    def pool(self, z: np.ndarray, stack: np.ndarray, sigma: float, deadline: float, min_n: int = 1) -> list[Lineup]:
        out = []
        for i in range(len(z)):
            if i >= min_n and time.perf_counter() > deadline:
                break
            lu = self.solve_one(z[i], sigma, bool(stack[i]))
            if lu is not None:
                out.append(lu)
        return out


def _dedupe(lineups: Sequence[Lineup], weights: Sequence[float] | None = None) -> tuple[list[Lineup], np.ndarray]:
    weights = np.ones(len(lineups)) if weights is None else np.asarray(weights, dtype=float)
    acc: dict[tuple, float] = {}
    first: dict[tuple, Lineup] = {}
    for lu, w in zip(lineups, weights):
        key = lineup_key(lu)
        acc[key] = acc.get(key, 0.0) + float(w)
        first.setdefault(key, lu)
    keys = list(acc)
    w = np.array([acc[k] for k in keys])
    return [first[k] for k in keys], w / w.sum()


def _top3(lineups: Sequence[Lineup], n_boot: int = 0, seed: int = 0) -> float:
    """Mean of the three largest ownership shares (fractions) of an equal-weight pool.

    With ``n_boot`` > 0 the value is bootstrap bias-corrected (2*raw - mean of resamples):
    the top shares of a small pool are inflated by sampling noise, so an uncorrected pilot
    would calibrate sigma too high for the larger final pool.
    """
    keys = sorted({k for lu in lineups for k in lu.player_keys})
    a = (lineup_matrix(lineups, keys) > 0).astype(np.float32)
    raw = float(np.sort(a.mean(axis=0))[-3:].mean())
    if not n_boot:
        return raw
    idx = np.random.default_rng(seed).integers(0, len(a), size=(n_boot, len(a)))
    boot = np.sort(a[idx].mean(axis=1), axis=1)[:, -3:].mean(axis=1)
    return 2.0 * raw - float(boot.mean())


def calibrate_noise(gen: _Generator, target_top3: float, pilot_size: int, deadline: float, sigma0: float = 0.3,
                    max_evals: int = 4, tol: float = 0.02, max_sigma: float = 0.5) -> tuple[float, list[Lineup], list[dict]]:
    """Secant search on log(sigma) so the pilot's (bias-corrected) top-3 ownership hits the target.

    Pilots share their random draws (common random numbers), which keeps the response
    close to monotone in sigma. If no pilot lands within ``tol``, sigma is interpolated
    between the closest pilots on either side of the target. sigma never exceeds
    ``max_sigma``: on small slates and showdowns the target is only reachable with
    near-random public projections, so there the field stays more concentrated than the
    target. Returns (sigma, the pilot lineups if sigma is a pilot's own value else [], trace).
    """
    z, stack = gen.draws(pilot_size)
    trace: list[dict] = []
    pools: dict[float, list[Lineup]] = {}

    def f(sigma: float) -> float:
        pools[sigma] = gen.pool(z, stack, sigma, deadline, min_n=max(4, pilot_size // 3))
        top3 = _top3(pools[sigma], n_boot=300)
        trace.append({"sigma": sigma, "top3": top3, "n": len(pools[sigma])})
        return top3 - target_top3

    lo, hi, fixed = np.log(0.03), np.log(max_sigma), np.log(1.6)
    sigma0 = min(sigma0, max_sigma)
    ys, fs = [np.log(sigma0)], [f(sigma0)]
    while abs(fs[-1]) > tol and len(trace) < max_evals and time.perf_counter() < deadline:
        slope = (fs[-1] - fs[-2]) / (ys[-1] - ys[-2]) if len(ys) > 1 and ys[-1] != ys[-2] else 0.0
        if slope < -1e-6:
            step = float(np.clip(-fs[-1] / slope, -np.log(2.5), np.log(2.5)))
        else:  # first step, or a flat / wrong-signed response from sampling noise
            step = fixed if fs[-1] > 0 else -fixed
        y = float(np.clip(ys[-1] + step, lo, hi))
        if np.isclose(y, ys).any():
            break
        ys.append(y)
        fs.append(f(float(np.exp(y))))
    best = int(np.argmin(np.abs(fs)))
    above = [i for i in range(len(fs)) if fs[i] > 0]
    below = [i for i in range(len(fs)) if fs[i] < 0]
    if abs(fs[best]) <= tol or not above or not below:
        sigma = float(np.exp(ys[best]))
        return sigma, pools[sigma], trace
    i = min(above, key=lambda j: fs[j])
    j = max(below, key=lambda j: fs[j])
    y = ys[i] + (0.0 - fs[i]) * (ys[j] - ys[i]) / (fs[j] - fs[i])
    return float(np.exp(y)), [], trace


def entropy_reweight(a: np.ndarray, w0: np.ndarray, target: np.ndarray, ridge: float = 1e-4,
                     max_iter: int = 3000) -> tuple[np.ndarray, dict[str, float]]:
    """Weights closest to ``w0`` in KL divergence with ``a.T @ w`` ~= ``target``.

    Dual: minimise logsumexp(log w0 + a @ lam) - lam . target + ridge/2 |lam|^2 by L-BFGS
    (matrix-vector BLAS only: small multithreaded LAPACK calls were ~300x slower on 4-core
    hosts). Newton steps crawled here: when the target is unattainable from the pool the
    Hessian is near-singular and 100 damped steps stopped on a collapsed weight vector.
    The ridge keeps the problem bounded when a target is unattainable.
    """
    w0 = np.asarray(w0, dtype=float)
    target = np.asarray(target, dtype=float)
    logw0 = np.log(np.clip(w0, 1e-300, None))
    m = a.shape[1]
    if m == 0:
        return w0 / np.sum(w0), {"fit_max_abs_err_pct": 0.0, "fit_mean_abs_err_pct": 0.0,
                                 "ess": float(1.0 / np.sum((w0 / np.sum(w0)) ** 2)), "converged": 1.0}

    def value_grad(lam: np.ndarray) -> tuple[float, np.ndarray]:
        s = logw0 + a @ lam
        lse = float(logsumexp(s))
        p = np.exp(s - lse)
        return lse - lam @ target + 0.5 * ridge * lam @ lam, a.T @ p - target + ridge * lam

    res = minimize(value_grad, np.zeros(m), jac=True, method="L-BFGS-B",
                   options={"maxiter": max_iter, "gtol": 1e-9, "ftol": 1e-15, "maxcor": 30})
    s = logw0 + a @ res.x
    p = np.exp(s - logsumexp(s))
    g = a.T @ p - target + ridge * res.x
    converged = float(np.abs(g).max() < 1e-6)  # ownership fractions: 1e-4 percentage points
    err = np.abs(a.T @ p - target)
    return p, {"fit_max_abs_err_pct": float(100 * err.max()) if err.size else 0.0,
               "fit_mean_abs_err_pct": float(100 * err.mean()) if err.size else 0.0,
               "ess": float(1.0 / np.sum(p * p)), "converged": converged}


def _finish(contest: Contest, lineups: list[Lineup], weights: np.ndarray, sigma: float,
            stats: dict[str, float]) -> FieldModel:
    own = implied_ownership(lineups, weights)
    slot_own: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for lu, w in zip(lineups, weights):
        for s in lu.slots:
            slot_own[s.slot][s.player_key] += 100.0 * float(w)
    cap = contest.rules.salary_cap
    sal = np.array([lu.salary for lu in lineups], dtype=float)
    stacked = np.array([has_qb_stack(contest, lu) for lu in lineups], dtype=float)
    n_players = len({c.player_key for c in contest.candidates})
    vals = np.array(list(own.values()))
    top = np.sort(vals)[::-1]
    stats = {**stats,
             "n_unique": float(len(lineups)),
             "max_own_pct": float(top[0]) if top.size else 0.0,
             "top3_own_pct": float(top[:3].mean()) if top.size else 0.0,
             "n_players_ge_25pct": float((vals >= 25).sum()),
             "share_players_lt_5pct": float(1 - (vals >= 5).sum() / max(n_players, 1)),
             "share_rostered_lt_5pct": float((vals < 5).mean()) if vals.size else 0.0,
             "n_players_rostered": float(vals.size),
             "salary_mean": float(weights @ sal),
             "share_salary_ge_98pct": float(weights @ (sal >= 0.98 * cap)),
             "share_qb_stacked": float(weights @ stacked),
             "ess": float(1.0 / np.sum(weights * weights))}
    lineups = [lu.model_copy(update={"metrics": {**lu.metrics, "field_weight": float(w)}})
               for lu, w in zip(lineups, weights)]  # copies: a refit must not rewrite the source model
    return FieldModel(lineups=lineups, weights=weights, ownership=own, noise=sigma, stats=stats,
                      slot_ownership={k: dict(sorted(v.items(), key=lambda kv: -kv[1])) for k, v in slot_own.items()})


# --------------------------------------------------------------------------- public API


def build_field(contest: Contest, projections: Mapping[str, Projection | float], *, n_lineups: int = 150,
                noise: float | None = None, target_top3: float = 0.35, stack_share: float = 0.5,
                ownership: Mapping[str, float] | None = None, public_points: Mapping[str, float] | None = None,
                pilot_size: int = 40, seed: int = 23, time_budget_s: float = 45.0,
                max_noise: float = 0.5) -> FieldModel:
    """Pool of plausible rival lineups with weights and implied ownership.

    ``public_points`` replaces our means as the public's baseline (e.g. a site-average
    blend). ``ownership`` (player_key -> percent) is matched by :func:`fit_ownership`.
    A calibrated sigma is capped at ``max_noise`` (stats["noise_capped"] = 1 when the cap
    binds); an explicit ``noise`` is used as given.
    About ``n_lineups`` + 4 * ``pilot_size`` solves at most, stopped early by the budget.
    """
    t0 = time.perf_counter()
    deadline = t0 + time_budget_s
    base = dict(public_points) if public_points is not None else point_values(projections)
    gen = _Generator(contest, base, stack_share, seed)
    trace: list[dict] = []
    pilot: list[Lineup] = []
    capped = 0.0
    if noise is None:
        noise, pilot, trace = calibrate_noise(gen, target_top3, pilot_size, t0 + 0.45 * time_budget_s,
                                              max_sigma=max_noise)
        capped = float(noise >= max_noise * (1 - 1e-9))
    z, stack = gen.draws(max(n_lineups - len(pilot), 0))
    lineups = pilot + gen.pool(z, stack, noise, deadline, min_n=max(1, 20 - len(pilot)))
    if not lineups:
        raise Infeasible("field model could not build any lineup")
    uniq, w = _dedupe(lineups)
    stats = {"n_solves": float(gen.n_solves), "n_lineups": float(len(lineups)),
             "calibration_evals": float(len(trace)), "target_top3_pct": 100 * target_top3,
             "noise_capped": capped, "build_s": time.perf_counter() - t0}
    fm = _finish(contest, uniq, w, noise, stats)
    fm.stats.update({f"pilot{i}_sigma": t["sigma"] for i, t in enumerate(trace)})
    fm.stats.update({f"pilot{i}_top3_pct": 100 * t["top3"] for i, t in enumerate(trace)})
    fm.generator = gen
    if ownership:
        fm = fit_ownership(fm, contest, ownership, time_budget_s=max(deadline - time.perf_counter(), 5.0))
    fm.stats["runtime_s"] = time.perf_counter() - t0
    return fm


def fit_ownership(fm: FieldModel, contest: Contest, ownership: Mapping[str, float], *, min_cover: int = 3,
                  max_extra: int = 60, tilt_rounds: int = 2, tilt_size: int = 40, tol_pct: float = 2.0,
                  ridge: float = 1e-4, time_budget_s: float = 20.0,
                  projections: Mapping[str, Projection | float] | None = None) -> FieldModel:
    """Reweight (and if needed augment) the pool so its ownership matches ``ownership`` (pct).

    Players missing from ``ownership`` are left unconstrained. Each listed player with at
    least 1% target ownership is first covered by ``min_cover`` lineups (solved with him
    locked on fresh noisy projections), then weights are fitted by minimum relative entropy.
    While the worst miss exceeds ``tol_pct``, up to ``tilt_rounds`` batches of lineups are
    added, solved on public projections tilted by sqrt(target / current ownership), so the
    pool holds lineups shaped like the target field instead of a few heavily weighted ones.
    """
    t0 = time.perf_counter()
    gen = fm.generator
    if gen is None:
        if projections is None:
            raise ValueError("fit_ownership needs projections for a field not built by build_field")
        gen = _Generator(contest, point_values(projections), 0.5, seed=97)
    target = {k: float(v) / 100.0 for k, v in ownership.items() if k in contest.players}
    lineups, w0 = list(fm.lineups), list(fm.weights)
    cover = Counter(k for lu in lineups for k in set(lu.player_keys))
    need = sorted(((t, k) for k, t in target.items() if t >= 0.01 and cover[k] < min_cover), reverse=True)
    extra: list[Lineup] = []
    for _, k in need:
        if k not in gen.keys:
            continue
        for _ in range(min_cover - cover[k]):
            if len(extra) >= max_extra or time.perf_counter() - t0 > time_budget_s:
                break
            z, stack = gen.draws(1)
            try:
                lu = gen.solve_one(z[0], fm.noise, bool(stack[0]), lock=k)
            except Infeasible:
                lu = None
            if lu is not None:
                extra.append(lu)
                cover.update(set(lu.player_keys))
    base_w = float(np.median(w0)) if w0 else 1.0
    keys = list(target)
    t_vec = np.array([target[k] for k in keys])
    gidx = {k: j for j, k in enumerate(gen.keys)}

    def fit(pool: list[Lineup], weights: list[float]) -> tuple[list[Lineup], np.ndarray, dict]:
        uniq, w_start = _dedupe(pool, weights)
        a = (lineup_matrix(uniq, keys, strict=False) > 0).astype(float)  # a player counts once per lineup
        w, info = entropy_reweight(a, w_start, t_vec, ridge=ridge)
        return uniq, w, info

    pool, weights = lineups + extra, w0 + [base_w] * len(extra)
    uniq, w, info = fit(pool, weights)
    rounds = 0
    while (info["fit_max_abs_err_pct"] > tol_pct and rounds < tilt_rounds
           and time.perf_counter() - t0 < time_budget_s):
        rounds += 1
        cur = implied_ownership(uniq, w)
        tilt = np.ones(len(gen.keys))
        for k, t in target.items():
            if k in gidx:
                tilt[gidx[k]] = np.clip(np.sqrt((t + 0.01) / (cur.get(k, 0.0) / 100 + 0.01)), 0.5, 2.0)
        z, stack = gen.draws(tilt_size)
        for i in range(tilt_size):
            if time.perf_counter() - t0 > time_budget_s:
                break
            lu = gen.solve_one(z[i], fm.noise, bool(stack[i]), tilt=tilt)
            if lu is not None:
                pool.append(lu)
                weights.append(base_w)
                extra.append(lu)
        uniq, w, info = fit(pool, weights)
    stats = {**fm.stats, **info, "n_extra_lineups": float(len(extra)), "n_solves": float(gen.n_solves),
             "tilt_rounds": float(rounds), "ownership_targets": float(len(keys)), "fit_s": time.perf_counter() - t0}
    out = _finish(contest, uniq, w, fm.noise, stats)
    out.generator = gen
    return out
