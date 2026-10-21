"""Late swap: re-solve only the roster slots whose players have not started.

A slot is locked once its player's game has started (``Player.start_time <= now``); a
locked slot keeps its exact cell (slot index and site id). Players whose games have
started cannot be added. The open slots are re-solved on current points under the cap left
by the locked salaries, with every contest rule still applied to the whole lineup (locked
rows are pinned to their own slot name, so team, game and stack rules count them). A pick
ruled out before his game is force-excluded and only the open slots are re-solved.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone, tzinfo
from typing import Iterable, Mapping

from dfs.field import point_values
from dfs.models import Contest, Lineup, LineupSlot, Player, Projection, UserRules
from dfs.optimize.ilp import Infeasible, SolveOptions, check_legal, solve


@dataclass
class SwapResult:
    lineup: Lineup
    locked: list[int]  # slot indices that could not change
    changes: list[dict]  # one per changed cell: index, slot, out/in keys, ids, points
    old_mean: float  # original lineup under current points (ruled-out players count 0)
    new_mean: float
    feasible: bool = True
    notes: list[str] = field(default_factory=list)


def _aware(t: datetime, tz: tzinfo) -> datetime:
    return t if t.tzinfo is not None else t.replace(tzinfo=tz)


def has_started(player: Player, now: datetime, naive_tz: tzinfo = timezone.utc,
                unknown_started: bool = False) -> bool:
    """True once the player's game has kicked off. Naive datetimes are read in ``naive_tz``."""
    if player.start_time is None:
        return unknown_started
    return _aware(player.start_time, naive_tz) <= _aware(now, naive_tz)


def _keys_for(contest: Contest, names: Iterable[str]) -> set[str]:
    by_name: dict[str, set[str]] = defaultdict(set)
    for k, p in contest.players.items():
        by_name[p.name.lower()].add(k)
    out: set[str] = set()
    for n in names:
        if n in contest.players:
            out.add(n)
        elif n.lower() in by_name:
            out |= by_name[n.lower()]
        else:
            raise KeyError(f"unknown player {n!r}")
    return out


def _sanitize(ur: UserRules | None, contest: Contest, locked: set[str], started: set[str],
              notes: list[str]) -> UserRules | None:
    """Drop user excludes naming a locked player (he can no longer be removed) and user locks
    naming a player whose game started outside the lineup (he can no longer be added)."""
    if ur is None or not (ur.exclude or ur.lock):
        return ur
    exclude = [n for n in ur.exclude if not (_keys_for(contest, [n]) & locked)]
    lock = [n for n in ur.lock if not (_keys_for(contest, [n]) & (started - locked))]
    dropped = sorted(set(ur.exclude) - set(exclude)) + sorted(set(ur.lock) - set(lock))
    if dropped:
        notes.append(f"user rules no longer applicable after kickoff, ignored: {dropped}")
    return ur.model_copy(update={"exclude": exclude, "lock": lock})


def late_swap(contest: Contest, lineup: Lineup, points: Mapping[str, float | Projection], now: datetime, *,
              ruled_out: Iterable[str] = (), opts: SolveOptions | None = None, naive_tz: tzinfo = timezone.utc,
              unknown_started: bool = False) -> SwapResult:
    """Re-solve the open slots of ``lineup`` at time ``now`` with current ``points``.

    ``ruled_out`` (keys or names) are excluded; a ruled-out player in a locked slot stays
    (his game has started) and is reported in ``notes``. If no legal re-solve exists the
    original lineup is returned with ``feasible=False``.
    """
    pts = point_values(points)
    out_keys = _keys_for(contest, ruled_out)
    for k in out_keys:
        pts[k] = 0.0
    notes: list[str] = []
    started = {k for k, p in contest.players.items() if has_started(p, now, naive_tz, unknown_started)}
    no_time = sorted(k for k in lineup.player_keys if contest.players[k].start_time is None)
    if no_time:
        notes.append(f"no start time (treated as {'started' if unknown_started else 'not started'}): {no_time}")
    locked_idx = [i for i, s in enumerate(lineup.slots) if s.player_key in started]
    locked_rows = {lineup.slots[i].site_id: lineup.slots[i] for i in locked_idx}
    locked_keys = {lineup.slots[i].player_key for i in locked_idx}
    for k in sorted(out_keys & locked_keys):
        notes.append(f"{contest.players[k].name} is ruled out but his game has started: slot locked")
    old_mean = sum(pts.get(s.player_key, 0.0) * s.points_multiplier for s in lineup.slots)

    def result(lu: Lineup, changes: list[dict], feasible: bool = True) -> SwapResult:
        new_mean = sum(pts.get(s.player_key, 0.0) * s.points_multiplier for s in lu.slots)
        return SwapResult(lu, locked_idx, changes, round(old_mean, 3), round(new_mean, 3), feasible, notes)

    if len(locked_idx) == len(lineup.slots):
        notes.append("every slot is locked")
        return result(lineup, [])

    cands = []
    for c in contest.candidates:
        if c.site_id in locked_rows:  # pin a locked row to the exact slot name it occupies
            cands.append(c.model_copy(update={"eligible_slots": [locked_rows[c.site_id].slot]}))
        elif c.player_key not in started and c.player_key not in locked_keys:
            cands.append(c)
    sub = contest.model_copy(update={"candidates": cands})
    for k in locked_keys:
        pts.setdefault(k, 0.0)
    base = opts or SolveOptions()
    stale = set(base.lock_keys) & (started - locked_keys)
    if stale:
        notes.append(f"locks on players already started outside the lineup ignored: {sorted(stale)}")
    o = replace(base, lock_keys=(set(base.lock_keys) - stale - out_keys) | locked_keys,
                exclude_keys=(set(base.exclude_keys) | out_keys | started) - locked_keys,
                user_rules=_sanitize(base.user_rules, contest, locked_keys, started, notes))
    try:
        solved = solve(sub, pts, o, label="lateswap")
    except Infeasible as e:
        try:
            if not (o.qb_stack or o.bring_back):
                raise
            solved = solve(sub, pts, replace(o, qb_stack=0, bring_back=0), label="lateswap")
            notes.append(f"stack rule dropped: {e}")
        except Infeasible as e2:
            notes.append(f"no legal re-solve ({e2}); original lineup kept")
            return result(lineup, [], feasible=False)

    new_slots = _reassign(lineup, solved, locked_idx)
    lu = Lineup(slots=new_slots, label=lineup.label or "lateswap", salary=sum(s.salary for s in new_slots),
                mean=round(sum(pts.get(s.player_key, 0.0) * s.points_multiplier for s in new_slots), 3),
                metrics={**lineup.metrics})
    changes = [{"index": i, "slot": a.slot, "out": a.player_key, "in": b.player_key, "out_id": a.site_id,
                "in_id": b.site_id, "out_points": round(pts.get(a.player_key, 0.0) * a.points_multiplier, 3),
                "in_points": round(pts.get(b.player_key, 0.0) * b.points_multiplier, 3)}
               for i, (a, b) in enumerate(zip(lineup.slots, new_slots)) if a.site_id != b.site_id]
    lu.metrics["late_swap_changes"] = float(len(changes))
    problems = check_legal(contest, lu)
    if problems:  # should not happen: the solver saw every rule; never hand back an illegal lineup
        notes.append(f"re-solved lineup failed the legality check {problems}; original kept")
        return result(lineup, [], feasible=False)
    return result(lu, changes)


def _reassign(original: Lineup, solved: Lineup, locked_idx: list[int]) -> list[LineupSlot]:
    """Put locked rows back in their own cells; fill open cells of each slot name with the
    solver's picks for that name, leaving a kept player in his old cell."""
    locked = set(locked_idx)
    locked_ids = {original.slots[i].site_id for i in locked}
    pool: dict[str, list[LineupSlot]] = defaultdict(list)
    for s in solved.slots:
        if s.site_id not in locked_ids:
            pool[s.slot].append(s)
    out: list[LineupSlot | None] = [original.slots[i] if i in locked else None for i in range(len(original.slots))]
    for i, s in enumerate(original.slots):  # unchanged picks stay put
        if out[i] is None:
            same = next((p for p in pool[s.slot] if p.site_id == s.site_id), None)
            if same is not None:
                out[i] = same
                pool[s.slot].remove(same)
    for i, s in enumerate(original.slots):
        if out[i] is None:
            out[i] = pool[s.slot].pop(0)
    return [s for s in out if s is not None]


def rule_out(contest: Contest, lineup: Lineup, players: Iterable[str], points: Mapping[str, float | Projection],
             now: datetime, **kw) -> SwapResult:
    """A pick is ruled out: exclude him (and anyone else listed) and re-solve the open slots."""
    return late_swap(contest, lineup, points, now, ruled_out=players, **kw)
