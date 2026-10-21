"""Field model, candidate evaluation / choice, optimal rates and late swap on synthetic slates."""

from __future__ import annotations

import random
import re
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from tests.conftest import make_toy_contest
from dfs.field import (FieldModel, Pruner, build_field, entropy_reweight, fit_ownership, lineup_matrix, point_values,
                       solve_pruned)
from dfs.lateswap import late_swap, rule_out
from dfs.models import Candidate, Contest, ContestRules, Lineup, LineupSlot, Slot
from dfs.optimize.ilp import SolveOptions, check_legal, solve
from dfs.select import (SelectConfig, choose, choose_portfolio, evaluate, optimal_rates, score_against_field,
                        select_lineups, stack_shape, stack_variants)
from dfs.sim import simulate


@pytest.fixture(scope="module")
def toy():
    return make_toy_contest()


@pytest.fixture(scope="module")
def field(toy):
    contest, projs = toy
    return build_field(contest, projs, n_lineups=120, seed=5)


@pytest.fixture(scope="module")
def sims(toy):
    contest, projs = toy
    return simulate(projs, contest.players, "nfl", n_sims=10000, seed=17)


def _lineup(keys: list[str], label: str = "", mult: list[float] | None = None) -> Lineup:
    mult = mult or [1.0] * len(keys)
    return Lineup(label=label, slots=[LineupSlot(slot=f"S{i}", player_key=k, site_id=k, salary=0, points_multiplier=m)
                                      for i, (k, m) in enumerate(zip(keys, mult))])


def _field_of(lineups: list[Lineup]) -> FieldModel:
    w = np.full(len(lineups), 1.0 / len(lineups))
    own: dict[str, float] = {}
    for lu in lineups:
        for k in lu.player_keys:
            own[k] = own.get(k, 0.0) + 100.0 / len(lineups)
    return FieldModel(lineups=lineups, weights=w, ownership=own, noise=0.0)


def _with_kickoffs(contest: Contest, early_games: set[str], t1: datetime, t2: datetime,
                   late_swap: bool = True) -> Contest:
    players = {k: p.model_copy(update={"start_time": t1 if p.game in early_games else t2})
               for k, p in contest.players.items()}
    rules = contest.rules.model_copy(update={"late_swap": late_swap})
    return contest.model_copy(update={"players": players, "rules": rules})


def _showdown(contest: Contest, projs: dict) -> tuple[Contest, dict]:
    """DK-showdown-shaped single game built from the toy slate: CPT (1.5x, own row) + 5 FLEX."""
    game = contest.games[0]
    keys = [k for k, p in contest.players.items() if p.game == game]
    cands = []
    for i, k in enumerate(keys):
        sal = contest.candidates_for(k)[0].salary
        cands.append(Candidate(player_key=k, site_id=f"C{i}", salary=int(sal * 1.5), eligible_slots=["CPT"]))
        cands.append(Candidate(player_key=k, site_id=f"F{i}", salary=sal, eligible_slots=["FLEX"]))
    rules = ContestRules(site="draftkings", sport="nfl", game_type="showdown", salary_cap=50000, min_teams=2,
                         multiplier_rows=True, scoring="draftkings.nfl",
                         slots=[Slot(name="CPT", eligible=["QB", "RB", "WR", "TE", "DST"], points_multiplier=1.5)]
                         + [Slot(name="FLEX", eligible=["QB", "RB", "WR", "TE", "DST"]) for _ in range(5)])
    sd = Contest(rules=rules, players={k: contest.players[k] for k in keys}, candidates=cands,
                 slot_headers=[s.name for s in rules.slots], source_file="synthetic", file_kind="dk_salaries",
                 games=[game])
    return sd, {k: projs[k] for k in keys}


# --------------------------------------------------------------------------- pruning + field


def test_pruner_is_exact_on_means_and_draws(toy, sims):
    contest, projs = toy
    keys, x = sims
    pruner = Pruner(contest, keys)
    rows = [np.array([projs[k].mean for k in keys])] + [x[i].astype(float) for i in range(12)]
    for vals in rows:
        full = solve(contest, dict(zip(keys, vals)), SolveOptions(workers=1))
        pruned = solve_pruned(contest, pruner, vals, SolveOptions(workers=1))
        assert pruned.mean == pytest.approx(full.mean, abs=1e-6)
        assert len(pruner.points(vals)) < len(keys)


def test_field_ownership_sums_to_roster_size(toy, field):
    contest, _ = toy
    size = contest.rules.roster_size
    assert sum(field.ownership.values()) == pytest.approx(100.0 * size)
    assert field.weights.sum() == pytest.approx(1.0)
    for name in {s.name for s in contest.rules.slots}:
        count = sum(s.name == name for s in contest.rules.slots)
        assert sum(field.slot_ownership[name].values()) == pytest.approx(100.0 * count)
    for lu in field.lineups:
        assert check_legal(contest, lu) == []


def test_field_chalk_concentration_and_salary_use(toy, field):
    contest, _ = toy
    own = np.array(list(field.ownership.values()))
    n_players = len(contest.players)
    assert 25.0 <= own.max() <= 60.0  # a chalk play exists, but nobody is near-universal
    assert (own >= 25.0).sum() >= 1
    assert 1.0 - (own >= 5.0).sum() / n_players >= 0.5  # most players under 5%
    assert field.stats["share_salary_ge_98pct"] >= 0.9
    assert 0.0 < field.noise < 1.5


def test_field_fixed_noise_skips_calibration(toy):
    contest, projs = toy
    fm = build_field(contest, projs, n_lineups=20, noise=0.3, seed=1)
    assert fm.stats["n_solves"] == 20 and fm.stats["calibration_evals"] == 0
    assert fm.noise == 0.3


def test_entropy_reweight_recovers_feasible_target():
    rng = np.random.default_rng(0)
    a = (rng.random((300, 80)) < 0.1).astype(float)
    target = a.T @ rng.dirichlet(np.full(300, 0.5))
    w, info = entropy_reweight(a, np.full(300, 1 / 300), target)
    assert w.sum() == pytest.approx(1.0) and (w >= 0).all()
    assert info["fit_max_abs_err_pct"] < 0.05 and info["converged"] == 1.0


def test_entropy_reweight_unattainable_target_beats_no_reweighting():
    """Regression: damped Newton crawled on targets the pool cannot reach and returned a
    collapsed weight vector (ESS ~1) that fitted worse than the starting weights."""
    rng = np.random.default_rng(0)
    m, ridge = 150, 1e-4

    def pool(n: int) -> np.ndarray:
        pop = rng.dirichlet(np.full(m, 0.3))
        a = np.zeros((n, m))
        for i in range(n):
            a[i, rng.choice(m, 9, replace=False, p=pop)] = 1.0
        return a

    a = pool(200)
    target = pool(400).mean(axis=0)  # ownership of a field built from another public's view
    w0 = np.full(200, 1 / 200)

    def primal(w: np.ndarray) -> float:  # KL(w || w0) + ridge penalty: what the dual minimises
        w = np.clip(w, 1e-300, None)
        return float(np.sum(w * np.log(w / w0))) + 0.5 / ridge * float(np.sum((a.T @ w - target) ** 2))

    w, info = entropy_reweight(a, w0, target, ridge=ridge)
    assert info["converged"] == 1.0
    assert primal(w) < 0.8 * primal(w0)
    assert info["ess"] > 5


def test_fit_ownership_full_table_far_from_pool(toy, field):
    """Regression: a full ownership table (every player, zeros included) from a public that
    disagrees with us left the old solver unconverged at 3.5 pct points mean error."""
    contest, projs = toy
    pub = point_values(projs)
    rng = np.random.default_rng(3)
    for k in rng.choice(sorted(pub), 40, replace=False):
        pub[k] *= rng.choice([0.5, 1.6])
    other = build_field(contest, projs, public_points=pub, noise=0.6 * field.noise, n_lineups=150, seed=9)
    target = {k: other.own(k) for k in contest.players}
    before = np.mean([abs(field.own(k) - v) for k, v in target.items()])
    fitted = fit_ownership(field, contest, target)
    assert fitted.stats["converged"] == 1.0
    assert fitted.stats["fit_mean_abs_err_pct"] < 0.6 * before
    assert fitted.stats["ess"] > 5
    assert sum(fitted.ownership.values()) == pytest.approx(100.0 * contest.rules.roster_size)


def test_small_pool_noise_is_capped(toy):
    """Regression: on a showdown the 35% top-3 target is only reachable with near-random public
    projections; calibration ran sigma to 1.5 and the mean-optimal lineup read P(cash) 0.97."""
    contest, projs = toy
    sd, sp = _showdown(contest, projs)
    fm = build_field(sd, sp, seed=8)
    assert fm.noise <= 0.5 and fm.stats["noise_capped"] == 1.0
    assert fm.stats["top3_own_pct"] > 50  # a single-game field concentrates on its core players
    keys, x = simulate(sp, sd.players, "nfl", n_sims=4000, seed=3)
    m = evaluate([solve(sd, point_values(sp))], keys, x, fm)[0].metrics
    assert m["p_cash"] < 0.95 and m["p_top"] < 0.2
    big = build_field(contest, projs, n_lineups=60, seed=5)  # main-slate calibration is untouched by the cap
    assert big.noise < 0.5 and big.stats["noise_capped"] == 0.0


def test_non_finite_sims_raise_a_named_error(toy, field):
    contest, projs = toy
    lu = field.lineups[0]
    k = lu.slots[3].player_key
    bad = dict(projs)
    bad[k] = projs[k].model_copy(update={"p90": float("nan")})
    keys, x = simulate(bad, contest.players, "nfl", n_sims=500, seed=3)
    with pytest.raises(ValueError, match=re.escape(k)):
        score_against_field([lu], keys, x, field)


def test_fit_ownership_matches_user_table(toy, field):
    contest, projs = toy
    pub = point_values(projs)
    rng = np.random.default_rng(2)
    for k in rng.choice(sorted(pub), 20, replace=False):  # a public that likes different players
        pub[k] *= rng.choice([0.7, 1.35])
    other = build_field(contest, projs, public_points=pub, noise=field.noise, n_lineups=100, seed=9)
    target = dict(list(other.ownership.items())[:10])
    fitted = fit_ownership(field, contest, target)
    assert fitted.stats["fit_max_abs_err_pct"] < 1.0
    for k, v in target.items():
        assert fitted.own(k) == pytest.approx(v, abs=1.0)
    assert sum(fitted.ownership.values()) == pytest.approx(100.0 * contest.rules.roster_size)


# --------------------------------------------------------------------------- evaluation + choice


def test_evaluator_is_self_consistent_on_the_field(field, sims):
    keys, x = sims
    ev = evaluate(field.lineups, keys, x, field, top_frac=0.1)
    w = field.weights
    p_top = np.array([lu.metrics["p_top"] for lu in ev])
    assert w @ np.array([lu.metrics["p_cash"] for lu in ev]) == pytest.approx(0.5, abs=1e-6)
    assert w @ np.array([lu.metrics["exp_pct"] for lu in ev]) == pytest.approx(50.0, abs=1e-4)
    assert w @ p_top == pytest.approx(0.1, abs=1.0 / len(field.lineups))


def test_p_top_bounded_and_monotone(toy, field, sims):
    contest, projs = toy
    keys, x = sims
    base = solve(contest, point_values(projs))
    swap = base.slots[1].player_key
    j = keys.index(swap)
    keys2 = keys + ["BETTER"]
    x2 = np.concatenate([x, x[:, j:j + 1] + 5.0], axis=1)  # same player, +5 points in every sim
    better = _lineup(["BETTER" if k == swap else k for k in base.player_keys], "better")
    worse = _lineup(base.player_keys, "base")
    out = evaluate([worse, better], keys2, x2, field)
    for lu in out:
        for m in ("p_top", "p_cash"):
            assert 0.0 <= lu.metrics[m] <= 1.0
        assert 0.0 <= lu.metrics["exp_pct"] <= 100.0
    w, b = out
    assert b.metrics["p_top"] > w.metrics["p_top"]
    assert b.metrics["p_cash"] >= w.metrics["p_cash"]
    assert b.metrics["exp_pct"] > w.metrics["exp_pct"]
    assert b.metrics["mean"] == pytest.approx(w.metrics["mean"] + 5.0, abs=1e-3)


def test_cash_and_gpp_choose_differently():
    rng = np.random.default_rng(4)
    s = 20000
    field_cols = rng.normal(100, 10, size=(s, 60))
    floor = rng.normal(108, 4, size=(s, 1))  # high floor, low ceiling
    ceiling = rng.normal(103, 25, size=(s, 1))  # lower mean, big ceiling
    x = np.concatenate([field_cols, floor, ceiling], axis=1).astype(np.float32)
    keys = [f"f{i}" for i in range(60)] + ["floor", "ceiling"]
    fm = _field_of([_lineup([f"f{i}"]) for i in range(60)])
    ev = evaluate([_lineup(["floor"], "floor"), _lineup(["ceiling"], "ceiling")], keys, x, fm, top_frac=0.023)
    by = {lu.label: lu.metrics for lu in ev}
    assert by["floor"]["p_cash"] > by["ceiling"]["p_cash"]
    assert by["ceiling"]["p_top"] > by["floor"]["p_top"]
    assert by["floor"]["mean"] > by["ceiling"]["mean"]
    assert choose(ev, "cash")[0].label == "floor"
    assert choose(ev, "gpp")[0].label == "ceiling"


def test_choose_breaks_ties_by_mean():
    a = _lineup(["a"], "a")
    b = _lineup(["b"], "b")
    a.metrics.update(p_top=0.05, p_cash=0.5, mean=100.0)
    b.metrics.update(p_top=0.05, p_cash=0.5, mean=101.0)
    assert [lu.label for lu in choose([a, b], "gpp")] == ["b", "a"]
    assert [lu.label for lu in choose([a, b], "cash")] == ["b", "a"]


def test_portfolio_adds_coverage(toy, field, sims):
    contest, projs = toy
    keys, x = sims
    cands = [solve(contest, point_values(projs), SolveOptions(avoid=[], min_unique=1), label="m1")]
    rng = np.random.default_rng(1)
    for i in range(15):
        noisy = {k: v * float(np.exp(0.3 * rng.standard_normal())) for k, v in point_values(projs).items()}
        cands.append(solve(contest, noisy, SolveOptions(workers=1), label=f"n{i}"))
    ev = score_against_field(cands, keys, x, field)
    best_single = max(lu.metrics["p_top"] for lu in ev.lineups)
    picks = choose_portfolio(ev, 5, "gpp")
    assert len(picks) == 5 and len({lu.label for lu in picks}) == 5
    assert picks[0].metrics["p_top"] == pytest.approx(best_single)
    assert picks[-1].metrics["portfolio_p_any_top"] >= best_single


# --------------------------------------------------------------------------- optimal rates


def test_optimal_rates_sum_to_roster_size(toy):
    contest, projs = toy
    rates = optimal_rates(contest, projs, n=40, seed=3)
    assert rates.n == 40
    assert sum(rates.player.values()) == pytest.approx(contest.rules.roster_size)
    for name in {s.name for s in contest.rules.slots}:
        count = sum(s.name == name for s in contest.rules.slots)
        assert sum(rates.slot[name].values()) == pytest.approx(count)
    for name in dict.fromkeys(s.name for s in contest.rules.slots):
        rows = [r for r in rates.table if r["slot"] == name]
        assert 1 <= len(rows) <= 3
        assert [r["optimal_rate"] for r in rows] == sorted((r["optimal_rate"] for r in rows), reverse=True)
        assert all(r["value"] is not None and r["mean"] is not None for r in rows)


def test_showdown_multiplier_slots(toy):
    contest, projs = toy
    sd, sp = _showdown(contest, projs)
    fm = build_field(sd, sp, n_lineups=40, seed=2, stack_share=0.0)
    assert sum(fm.ownership.values()) == pytest.approx(600.0)
    assert sum(fm.slot_ownership["CPT"].values()) == pytest.approx(100.0)
    lu = fm.lineups[0]
    m = lineup_matrix([lu], sorted(sp))
    assert m.sum() == pytest.approx(1.5 + 5)
    rates = optimal_rates(sd, sp, n=20, seed=1)
    assert sum(rates.player.values()) == pytest.approx(6)
    assert sum(rates.slot["CPT"].values()) == pytest.approx(1)


# --------------------------------------------------------------------------- late swap


T1 = datetime(2026, 10, 25, 17, 0, tzinfo=timezone.utc)
T2 = T1 + timedelta(hours=3, minutes=25)


def _late_setup(toy, late_swap: bool = True):
    contest, projs = toy
    pts = point_values(projs)
    lu = solve(contest, pts)
    early = {contest.players[lu.slots[i].player_key].game for i in (0, 7)}  # QB's and FLEX's games kick off first
    return _with_kickoffs(contest, early, T1, T2, late_swap), pts, lu, early


def test_late_swap_keeps_locked_slots_and_stays_legal(toy):
    contest, pts, lu, early = _late_setup(toy)
    now = T1 + timedelta(minutes=30)
    for seed in range(5):
        rng = random.Random(seed)
        new_pts = {k: v * rng.uniform(0.5, 1.5) for k, v in pts.items()}
        res = late_swap(contest, lu, new_pts, now)
        assert res.feasible and check_legal(contest, res.lineup) == []
        assert 7 in res.locked and 0 in res.locked  # the FLEX lock stays in the FLEX cell
        for i in res.locked:
            assert res.lineup.slots[i] == lu.slots[i]
        for i, s in enumerate(res.lineup.slots):
            if i not in res.locked:
                assert contest.players[s.player_key].game not in early  # nobody already started is added
        assert res.new_mean >= res.old_mean - 0.05  # the old lineup was feasible (0.05: solver rounds to 0.01)
        assert len(res.changes) == sum(a.site_id != b.site_id for a, b in zip(lu.slots, res.lineup.slots))


def test_late_swap_rule_out(toy):
    contest, pts, lu, early = _late_setup(toy)
    now = T1 + timedelta(minutes=30)
    open_i = next(i for i, s in enumerate(lu.slots) if contest.players[s.player_key].game not in early)
    victim = lu.slots[open_i].player_key
    res = rule_out(contest, lu, [contest.players[victim].name], pts, now)
    assert victim not in res.lineup.player_keys
    assert check_legal(contest, res.lineup) == []
    assert all(res.lineup.slots[i] == lu.slots[i] for i in res.locked)
    locked_victim = lu.slots[0].player_key  # QB already started: cannot be removed
    res2 = rule_out(contest, lu, [locked_victim], pts, now)
    assert res2.lineup.slots[0] == lu.slots[0]
    assert any("game has started" in n for n in res2.notes)


def test_late_swap_before_and_after_all_games(toy):
    contest, pts, lu, _ = _late_setup(toy)
    before = late_swap(contest, lu, pts, T1 - timedelta(hours=1))
    assert before.locked == [] and before.lineup.mean == pytest.approx(lu.mean, abs=0.05)
    after = late_swap(contest, lu, {k: 2 * v for k, v in pts.items()}, T2 + timedelta(minutes=1))
    assert after.locked == list(range(len(lu.slots))) and after.changes == []
    naive = late_swap(contest, lu, pts, (T1 + timedelta(minutes=5)).replace(tzinfo=None))  # naive = UTC
    assert naive.locked == [i for i, s in enumerate(lu.slots) if contest.players[s.player_key].start_time == T1]


# --------------------------------------------------------------------------- runtime + pipeline


def test_evaluate_runtime_10k_sims_300_candidates(toy, field):
    contest, projs = toy
    t0 = time.perf_counter()
    keys, x = simulate(projs, contest.players, "nfl", n_sims=10000, seed=1)
    t_sim = time.perf_counter() - t0
    rng = random.Random(0)
    by_slot = {s.name: [c.player_key for c in contest.candidates if s.name in c.eligible_slots]
               for s in contest.rules.slots}
    cands = []
    for i in range(300):
        used: list[str] = []
        for s in contest.rules.slots:
            used.append(rng.choice([k for k in by_slot[s.name] if k not in used]))
        cands.append(_lineup(used, f"r{i}"))
    t1 = time.perf_counter()
    ev = evaluate(cands, keys, x, field, field_size=10000)
    t_eval = time.perf_counter() - t1
    print(f"\nsimulate 10k x {len(keys)}: {t_sim:.2f}s; "
          f"evaluate 300 candidates vs {len(field.lineups)} field lineups: {t_eval:.2f}s")
    assert len(ev) == 300 and t_sim + t_eval < 20.0


def test_select_pipeline_small(toy):
    contest, projs = toy
    cfg = SelectConfig(n_sims=4000, n_mean=6, n_draws=40, n_half=10, stack_qbs=3, field_lineups=60,
                       n_field_candidates=5, time_budget_s=120, field_size=5000)
    t0 = time.perf_counter()
    sel = select_lineups(contest, projs, kind="gpp", cfg=cfg)
    took = time.perf_counter() - t0
    print(f"\nselect_lineups small config: {took:.1f}s {sel.timings}")
    assert sel.ranked and all(check_legal(contest, lu) == [] for lu in sel.ranked)
    p = [lu.metrics["p_top"] for lu in sel.ranked]
    assert p == sorted(p, reverse=True)
    s = sel.summary
    assert s["kind"] == "gpp" and s["chosen"]["label"] == sel.ranked[0].label
    assert {"mean", "stack", "sim"} <= set(s["candidate_sources"])
    assert "dup_indep" in sel.ranked[0].metrics
    assert sum(sel.rates.player.values()) == pytest.approx(contest.rules.roster_size)
    cash = choose([lu.model_copy() for lu in sel.ranked], "cash")
    pc = [lu.metrics["p_cash"] for lu in cash]
    assert pc == sorted(pc, reverse=True)


def test_stack_variants_match_unpruned_solves(toy):
    contest, projs = toy
    pts = point_values(projs)
    lus = stack_variants(contest, pts, n_qbs=2, sizes=(1, 2), bring_backs=(0, 1))
    assert len(lus) == 8
    for lu in lus:
        team, rest = lu.label.split(":")[1:]
        s, b = (int(v) for v in rest.split("+"))
        qb = next(k for k in lu.player_keys if "QB" in contest.players[k].positions)
        full = solve(contest, pts, SolveOptions(lock_keys={qb}, qb_stack=s, bring_back=b))
        assert lu.mean == pytest.approx(full.mean, abs=1e-6)
        shape = stack_shape(contest, lu)
        assert shape["team"] == team and shape["stack"] >= s and shape["bring_back"] >= b


def test_candidates_respect_locks_and_excludes(toy):
    contest, projs = toy
    pts = point_values(projs)
    best = solve(contest, pts)
    lock, excl = best.player_keys[1], best.player_keys[2]
    base = SolveOptions(lock_keys={lock}, exclude_keys={excl})
    cfg = SelectConfig(n_sims=2000, n_mean=4, n_draws=15, n_half=5, stack_qbs=2, stack_sizes=(1,),
                       bring_backs=(0,), field_lineups=30, time_budget_s=60)
    fm = build_field(contest, projs, n_lineups=30, noise=0.35, seed=4)
    sel = select_lineups(contest, projs, kind="cash", cfg=cfg, fm=fm, base=base)
    assert "field" not in sel.summary["candidate_sources"]  # field lineups ignore the user's rules
    for lu in sel.ranked:
        assert lock in lu.player_keys and excl not in lu.player_keys
        assert check_legal(contest, lu) == []


def test_fixed_cash_line(field, sims):
    keys, x = sims
    lu = field.lineups[0]
    ev = score_against_field([lu], keys, x, field, cash_line=150.0)
    assert ev.lineups[0].metrics["p_cash"] == pytest.approx(float((ev.scores[:, 0] > 150.0).mean()))


def test_no_late_swap_contest_locks_everything_at_first_kickoff(toy):
    contest, pts, lu, _ = _late_setup(toy, late_swap=False)
    before = late_swap(contest, lu, pts, T1 - timedelta(minutes=1), ruled_out=[lu.slots[3].player_key])
    assert before.locked == [] and lu.slots[3].player_key not in before.lineup.player_keys
    after = late_swap(contest, lu, pts, T1 + timedelta(minutes=1), ruled_out=[lu.slots[3].player_key])
    assert after.locked == list(range(len(lu.slots))) and after.lineup == lu
    assert any("no late swap" in n for n in after.notes)


def test_late_swap_keeps_caller_slot_locks(toy):
    contest, pts, lu, early = _late_setup(toy)
    now = T1 + timedelta(minutes=30)
    open_i = next(i for i, s in enumerate(lu.slots) if contest.players[s.player_key].game not in early)
    keep = {open_i: lu.slots[open_i].site_id}  # the user pins one open cell too
    res = late_swap(contest, lu, {k: v * (0.2 if k == lu.slots[open_i].player_key else 1.0) for k, v in pts.items()},
                    now, opts=SolveOptions(slot_locks=keep))
    assert res.lineup.slots[open_i] == lu.slots[open_i]
    assert check_legal(contest, res.lineup) == []
