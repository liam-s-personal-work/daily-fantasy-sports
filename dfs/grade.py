"""After the slate: grade the lineup, find the step that cost the most, and learn from it.

Inputs: the contest, the run's saved stage outputs (projections by source, drafts, final
lineup, exclusions), and actual fantasy points per player. Optional: the site's contest
standings export (DK "contest-standings-*.csv": Rank, EntryId, EntryName, TimeRemaining,
Points, Lineup, , Player, Roster Position, %Drafted, FPTS) for the true finish percentile
and real ownership.
"""

from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from dfs.io.contest_file import norm_name
from dfs.models import Contest, Lineup, Projection
from dfs.optimize.ilp import Infeasible, SolveOptions, lineup_points, solve


@dataclass
class Standings:
    scores: np.ndarray  # every entry's points
    ownership: dict[str, float]  # normalized player name -> % drafted (0-100)
    actual_fpts: dict[str, float]  # normalized player name -> FPTS as the site scored it

    def percentile(self, points: float) -> float:
        """Share of the field this score beats or ties (1.0 = first place)."""
        return float((self.scores <= points).mean())

    def rank(self, points: float) -> int:
        return int((self.scores > points).sum()) + 1


def read_standings(path: str | Path) -> Standings:
    scores, own, fpts = [], {}, {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    head = [h.strip() for h in rows[0]]
    ip = head.index("Points")
    try:
        ipl, iown, ifp = head.index("Player"), head.index("%Drafted"), head.index("FPTS")
    except ValueError:
        ipl = iown = ifp = None
    for r in rows[1:]:
        if len(r) > ip and r[ip].strip():
            try:
                scores.append(float(r[ip]))
            except ValueError:
                pass
        if ipl is not None and len(r) > ifp and r[ipl].strip():
            name = norm_name(r[ipl])
            own[name] = float(r[iown].strip().rstrip("%") or 0)
            fpts[name] = float(r[ifp] or 0)
    return Standings(scores=np.array(scores, dtype=float), ownership=own, actual_fpts=fpts)


@dataclass
class Grade:
    actual: float
    projected: float
    hindsight_optimal: float
    efficiency: float  # actual / hindsight optimal
    percentile: float | None = None  # vs the real field when standings are given
    rank: int | None = None
    field_size: int | None = None
    hit_target: bool | None = None  # finished inside the target top fraction
    per_slot: list[dict] = field(default_factory=list)
    steps: dict[str, float] = field(default_factory=dict)  # points gained (+) or lost (-) by each step
    failed_step: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def grade_lineup(contest: Contest, lineup: Lineup, actual: dict[str, float], *,
                 projections: dict[str, Projection] | None = None,
                 stage_lineups: dict[str, Lineup] | None = None,
                 standings: Standings | None = None, target_top: float = 0.023) -> Grade:
    """Score the lineup, compare it with the best possible lineup and with each stage's lineup.

    ``stage_lineups`` maps stage names in pipeline order (e.g. "baseline_only", "forecast_only",
    "blend", "after_strategist", "after_checker", "final") to the lineup that stage would have
    submitted; consecutive differences in actual points say which step helped or hurt.
    """
    pts = {k: actual.get(k, 0.0) for k in contest.players}
    got = lineup_points(lineup, pts)
    try:
        best = solve(contest, pts, SolveOptions(time_limit_s=20)).mean
    except Infeasible:
        best = float("nan")
    g = Grade(actual=round(got, 2), projected=round(lineup.mean, 2), hindsight_optimal=round(best, 2),
              efficiency=round(got / best, 4) if best and not math.isnan(best) else float("nan"))
    for s in lineup.slots:
        pr = projections.get(s.player_key) if projections else None
        a = pts.get(s.player_key, 0.0) * s.points_multiplier
        g.per_slot.append({"slot": s.slot, "player": s.player_key, "actual": round(a, 2),
                           "projected": round(pr.mean * s.points_multiplier, 2) if pr else None,
                           "p10": pr.p10 if pr else None, "p90": pr.p90 if pr else None,
                           "outside_80pct_band": bool(pr and not (pr.p10 <= pts.get(s.player_key, 0.0) <= pr.p90))})
    if standings is not None and len(standings.scores):
        g.percentile = round(standings.percentile(got), 4)
        g.rank = standings.rank(got)
        g.field_size = int(len(standings.scores))
        g.hit_target = g.rank <= max(1, math.floor(target_top * g.field_size))
    if stage_lineups:
        names = list(stage_lineups)
        vals = {n: lineup_points(stage_lineups[n], pts) for n in names}
        for a, b in zip(names, names[1:]):
            g.steps[f"{a}->{b}"] = round(vals[b] - vals[a], 2)
        if "baseline_only" in vals and "forecast_only" in vals:
            g.notes.append(f"baseline-only lineup would have scored {vals['baseline_only']:.1f}, "
                           f"forecast-only {vals['forecast_only']:.1f}")
        worst = min(g.steps.items(), key=lambda kv: kv[1], default=None)
        if worst and worst[1] < 0:
            g.failed_step = worst[0].split("->")[1]
    if g.failed_step is None and g.per_slot:
        miss = min(g.per_slot, key=lambda r: r["actual"] - (r["projected"] or 0))
        g.failed_step = "projection" if (miss["projected"] or 0) - miss["actual"] > 8 else None
        if g.failed_step:
            g.notes.append(f"largest projection miss: {miss['player']} ({miss['projected']} projected, {miss['actual']} actual)")
    return g


# ----------------------------------------------------------------------------- learning the blend

def update_blend(path: str | Path, sport: str, errors: list[dict], prior_weight: float, n0: float = 400.0) -> dict:
    """Shrunk inverse-MSE weight on the forecast vs the baseline, per position.

    ``errors`` rows: {"position", "baseline_err", "forecast_err"} (actual - projected). Weights move
    toward the data only as evidence accumulates: w = (n0 * w_prior + n * w_data) / (n0 + n).
    """
    path = Path(path)
    state = json.loads(path.read_text()) if path.exists() else {}
    sp = state.setdefault(sport, {})
    by_pos: dict[str, list[dict]] = {}
    for e in errors:
        by_pos.setdefault(e["position"], []).append(e)
    for pos, rows in by_pos.items():
        rec = sp.setdefault(pos, {"n": 0, "sse_baseline": 0.0, "sse_forecast": 0.0, "weight": prior_weight})
        rec["n"] += len(rows)
        rec["sse_baseline"] += sum(r["baseline_err"] ** 2 for r in rows)
        rec["sse_forecast"] += sum(r["forecast_err"] ** 2 for r in rows)
        mse_b, mse_f = rec["sse_baseline"] / rec["n"], rec["sse_forecast"] / rec["n"]
        w_data = (1 / mse_f) / (1 / mse_f + 1 / mse_b) if mse_f > 0 and mse_b > 0 else prior_weight
        rec["weight"] = round((n0 * prior_weight + rec["n"] * w_data) / (n0 + rec["n"]), 4)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=1))
    return state
