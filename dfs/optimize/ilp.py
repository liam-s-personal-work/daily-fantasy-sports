"""Exact legal-lineup solver (OR-Tools CP-SAT).

One boolean per (CSV row, slot) pair the row may fill. Hard constraints are the site's
rules (cap, slots, team/game limits), each real player at most once (DK showdown lists a
player twice, as CPT and FLEX), the user's rules, and "differs from earlier lineups by at
least k players" cuts for building several distinct lineups.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from ortools.sat.python import cp_model

from dfs.models import Candidate, Contest, Lineup, LineupSlot, UserRules

PITCHERS = {"P", "SP", "RP"}
SCALE = 100  # CP-SAT needs integer coefficients: points are solved in hundredths


@dataclass
class SolveOptions:
    user_rules: UserRules | None = None
    avoid: list[Lineup] = field(default_factory=list)  # previously built lineups
    min_unique: int = 1  # each new lineup shares at most roster_size - min_unique players with each avoided one
    exclude_keys: set[str] = field(default_factory=set)
    lock_keys: set[str] = field(default_factory=set)
    # Late swap: slot index -> site id that must stay in exactly that slot (DK "(LOCKED)" cells).
    slot_locks: dict[int, str] = field(default_factory=dict)
    min_salary: int | None = None
    qb_stack: int = 0  # NFL: at least this many WR/TE from the chosen QB's team
    bring_back: int = 0  # NFL: at least this many players from the QB's opponent
    time_limit_s: float = 10.0
    workers: int = 4


class Infeasible(RuntimeError):
    pass


class RuleError(Infeasible):
    """A pick rule that names an unknown or ambiguous player, team or game."""


def _team_code(contest: Contest, team: str) -> str:
    """The contest's own code for a team the user named (aliases such as JAC/JAX, LA/LAR, WSH/WAS)."""
    teams = {p.team.upper() for p in contest.players.values()}
    up = team.strip().upper()
    if up in teams:
        return up
    if contest.rules.sport == "nfl":
        from dfs.data.teams import canonicalize
        want = canonicalize(up, strict=False)
        hit = [t for t in teams if canonicalize(t, strict=False) == want]
        if len(hit) == 1:
            return hit[0]
    raise RuleError(f"rule names team {team!r}, which is not in this contest ({', '.join(sorted(teams))})")


def _game_code(contest: Contest, game: str) -> str:
    if "@" not in game:
        raise RuleError(f"rule game {game!r} should look like AWAY@HOME")
    a, h = (_team_code(contest, t) for t in game.split("@", 1))
    g = f"{a}@{h}"
    if g not in {x.upper() for x in contest.games}:
        raise RuleError(f"rule names game {game!r}, which is not in this contest ({', '.join(contest.games)})")
    return g


def _resolve_names(contest: Contest, names: list[str]) -> set[str]:
    """Accept player keys, display names, or 'Name (TEAM)' when a name is shared by two players."""
    out: set[str] = set()
    by_name: dict[str, list[str]] = defaultdict(list)
    for k, p in contest.players.items():
        by_name[p.name.lower()].append(k)
    for n in names:
        m = re.match(r"^\s*(.+?)\s*\(([A-Za-z]{2,4})\)\s*$", n)
        if n in contest.players:
            out.add(n)
        elif m and m.group(1).lower() in by_name:
            team = _team_code(contest, m.group(2))
            hit = [k for k in by_name[m.group(1).lower()] if contest.players[k].team.upper() == team]
            if not hit:
                raise RuleError(f"rule names {n!r}, but {m.group(1)} is not on {team} in this contest")
            out.update(hit)
        elif n.lower() in by_name:
            hit = by_name[n.lower()]
            if len({contest.players[k].team for k in hit}) > 1:
                opts = ", ".join(f"'{contest.players[k].name} ({contest.players[k].team})'" for k in hit)
                raise RuleError(f"rule name {n!r} matches more than one player; write one of {opts}")
            out.update(hit)
        else:
            raise RuleError(f"rule names unknown player {n!r}")
    return out


def validate_rules(contest: Contest, ur: UserRules) -> None:
    """Raise RuleError naming the first unknown or ambiguous player, team or game in the user's rules."""
    _resolve_names(contest, list(ur.lock) + list(ur.exclude))
    for t in ur.exclude_teams:
        _team_code(contest, t)
    for g in ur.exclude_games:
        _game_code(contest, g)
    for st in ur.stack:
        _team_code(contest, st["team"])


def solve(contest: Contest, points: dict[str, float], opts: SolveOptions | None = None,
          label: str = "") -> Lineup:
    """Return the legal lineup with the highest total of ``points`` (slot multipliers applied)."""
    opts = opts or SolveOptions()
    rules = contest.rules
    slots = rules.slots
    ur = opts.user_rules or UserRules()

    exclude = set(opts.exclude_keys) | _resolve_names(contest, ur.exclude)
    lock = set(opts.lock_keys) | _resolve_names(contest, ur.lock)
    if lock & exclude:
        raise Infeasible(f"players both locked and excluded: {sorted(lock & exclude)}")
    bad_teams = {_team_code(contest, t) for t in ur.exclude_teams}
    bad_games = {_game_code(contest, g) for g in ur.exclude_games}

    locked_ids = set(opts.slot_locks.values())

    def usable(c: Candidate) -> bool:
        k = c.player_key
        if c.site_id in locked_ids:
            return True
        if k not in points:
            return False
        if k in lock:
            return True
        if k in exclude:
            return False
        p = contest.players[k]
        return p.team.upper() not in bad_teams and not (p.game and p.game.upper() in bad_games)

    cands = [c for c in contest.candidates if usable(c)]
    # Identical slots (RB, RB, WR x3) are one group with a count: no symmetric duplicates.
    groups: dict[str, int] = {}
    mult: dict[str, float] = {}
    for s in slots:
        groups[s.name] = groups.get(s.name, 0) + 1
        mult[s.name] = s.points_multiplier
    m = cp_model.CpModel()
    x: dict[tuple[int, str], cp_model.IntVar] = {}
    for ci, c in enumerate(cands):
        for g in groups:
            if g in c.eligible_slots:
                x[ci, g] = m.NewBoolVar(f"x{ci}_{g}")

    by_slot: dict[str, list] = defaultdict(list)
    by_cand: dict[int, list] = defaultdict(list)
    by_player: dict[str, list] = defaultdict(list)
    for (ci, g), v in x.items():
        by_slot[g].append(v)
        by_cand[ci].append(v)
        by_player[cands[ci].player_key].append(v)

    for g, count in groups.items():
        if len(by_slot[g]) < count:
            raise Infeasible(f"not enough eligible players for slot {g}")
        m.Add(sum(by_slot[g]) == count)
    for vs in by_cand.values():
        m.AddAtMostOne(vs)
    for vs in by_player.values():
        m.AddAtMostOne(vs)

    salary = sum(cands[ci].salary * v for (ci, _), v in x.items())
    m.Add(salary <= rules.salary_cap)
    min_sal = opts.min_salary or ur.min_salary
    if min_sal:
        m.Add(salary >= min_sal)

    team_vars: dict[str, list] = defaultdict(list)
    hitter_vars: dict[str, list] = defaultdict(list)
    game_vars: dict[str, list] = defaultdict(list)
    for (ci, _), v in x.items():
        p = contest.players[cands[ci].player_key]
        team_vars[p.team].append(v)
        if not set(p.positions) & PITCHERS:
            hitter_vars[p.team].append(v)
        if p.game:
            game_vars[p.game].append(v)
    if rules.max_hitters_per_team:
        for vs in hitter_vars.values():
            m.Add(sum(vs) <= rules.max_hitters_per_team)

    max_team = min(filter(None, [rules.max_per_team, ur.max_from_team]), default=None)
    if max_team:
        for vs in team_vars.values():
            m.Add(sum(vs) <= max_team)
    if rules.min_teams and rules.min_teams > 1:
        used = []
        for t, vs in team_vars.items():
            y = m.NewBoolVar(f"team_{t}")
            m.Add(sum(vs) >= 1).OnlyEnforceIf(y)
            used.append(y)
        m.Add(sum(used) >= rules.min_teams)
    if rules.min_games and rules.min_games > 1:
        used = []
        for g, vs in game_vars.items():
            y = m.NewBoolVar(f"game_{g}")
            m.Add(sum(vs) >= 1).OnlyEnforceIf(y)
            used.append(y)
        m.Add(sum(used) >= rules.min_games)

    for k in lock:
        if k not in by_player:
            raise Infeasible(f"locked player {k} has no usable row")
        m.Add(sum(by_player[k]) == 1)
    for idx, sid in opts.slot_locks.items():
        g = slots[idx].name
        vs = [v for (ci, gg), v in x.items() if gg == g and cands[ci].site_id == sid]
        if not vs:
            raise Infeasible(f"locked slot {idx + 1} ({g}) cannot hold {sid}")
        m.Add(sum(vs) == 1)

    # Stacks expressed in user rules: {"team": "BUF", "positions": ["WR","TE"], "min": 2}
    for st in ur.stack:
        team = _team_code(contest, st["team"])
        pos = set(st.get("positions") or [])
        vs = [v for (ci, _), v in x.items()
              if contest.players[cands[ci].player_key].team.upper() == team
              and (not pos or pos & set(contest.players[cands[ci].player_key].positions))]
        m.Add(sum(vs) >= int(st.get("min", 1)))

    if opts.qb_stack or opts.bring_back:
        for (ci, _), v in x.items():
            p = contest.players[cands[ci].player_key]
            if "QB" not in p.positions:
                continue
            if opts.qb_stack:
                mates = [w for (cj, _), w in x.items()
                         if contest.players[cands[cj].player_key].team == p.team
                         and {"WR", "TE"} & set(contest.players[cands[cj].player_key].positions)]
                m.Add(sum(mates) >= opts.qb_stack).OnlyEnforceIf(v)
            if opts.bring_back and p.opponent:
                opps = [w for (cj, _), w in x.items()
                        if contest.players[cands[cj].player_key].team == p.opponent]
                m.Add(sum(opps) >= opts.bring_back).OnlyEnforceIf(v)

    size = rules.roster_size
    for prev in opts.avoid:
        keys = set(prev.player_keys)
        vs = [v for (ci, _), v in x.items() if cands[ci].player_key in keys]
        m.Add(sum(vs) <= size - opts.min_unique)

    obj = []
    for (ci, g), v in x.items():
        obj.append(int(round(points.get(cands[ci].player_key, 0.0) * mult[g] * SCALE)) * v)
    m.Maximize(sum(obj))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = opts.time_limit_s
    solver.parameters.num_workers = opts.workers
    solver.parameters.random_seed = 7
    status = solver.Solve(m)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise Infeasible(f"no legal lineup ({solver.StatusName(status)})")

    picked: dict[str, list[Candidate]] = defaultdict(list)
    for (ci, g), v in x.items():
        if solver.Value(v):
            picked[g].append(cands[ci])
    lineup_slots: list[LineupSlot | None] = [None] * size
    for idx, sid in opts.slot_locks.items():  # locked cells keep their exact position
        c = next(c for c in picked[slots[idx].name] if c.site_id == sid)
        picked[slots[idx].name].remove(c)
        lineup_slots[idx] = LineupSlot(slot=slots[idx].name, player_key=c.player_key, site_id=c.site_id,
                                       salary=c.salary, points_multiplier=slots[idx].points_multiplier)
    for i, s in enumerate(slots):  # file order; within a group, higher salary first (stable, readable)
        if lineup_slots[i] is not None:
            continue
        c = sorted(picked[s.name], key=lambda c: (-c.salary, c.site_id)).pop(0)
        picked[s.name].remove(c)
        lineup_slots[i] = LineupSlot(slot=s.name, player_key=c.player_key, site_id=c.site_id,
                                     salary=c.salary, points_multiplier=s.points_multiplier)
    mean = sum(points.get(s.player_key, 0.0) * s.points_multiplier for s in lineup_slots)
    return Lineup(slots=lineup_slots, label=label, salary=sum(s.salary for s in lineup_slots), mean=round(mean, 3),
                  metrics={"optimal": float(status == cp_model.OPTIMAL)})


def solve_many(contest: Contest, points: dict[str, float], n: int, opts: SolveOptions | None = None,
               label: str = "") -> list[Lineup]:
    """Best ``n`` lineups, each differing from all earlier ones by >= opts.min_unique players."""
    opts = opts or SolveOptions()
    out: list[Lineup] = []
    for i in range(n):
        o = SolveOptions(**{**opts.__dict__, "avoid": list(opts.avoid) + out})
        try:
            out.append(solve(contest, points, o, label=f"{label}{i + 1}"))
        except Infeasible:
            break
    return out


def lineup_points(lineup: Lineup, points: dict[str, float]) -> float:
    return sum(points.get(s.player_key, 0.0) * s.points_multiplier for s in lineup.slots)


def check_legal(contest: Contest, lineup: Lineup) -> list[str]:
    """Independent legality check (does not trust the solver). Returns a list of problems."""
    rules = contest.rules
    problems: list[str] = []
    if [s.slot for s in lineup.slots] != [s.name for s in rules.slots]:
        problems.append("slot order/names do not match the contest")
    rows: dict[str, list[Candidate]] = defaultdict(list)
    for c in contest.candidates:
        rows[c.site_id].append(c)  # FD single game: MVP and FLEX candidates share one Id
    seen: set[str] = set()
    salary = 0
    teams: dict[str, int] = defaultdict(int)
    hitters: dict[str, int] = defaultdict(int)
    games: set[str] = set()
    for ls in lineup.slots:
        options = rows.get(ls.site_id)
        if not options:
            problems.append(f"{ls.site_id} is not a row in this contest")
            continue
        c = next((o for o in options if ls.slot in o.eligible_slots), None)
        if c is None:
            problems.append(f"{ls.site_id} cannot fill {ls.slot}")
            continue
        if c.player_key in seen:
            problems.append(f"{c.player_key} used twice")
        seen.add(c.player_key)
        salary += c.salary
        p = contest.players[c.player_key]
        teams[p.team] += 1
        if not set(p.positions) & PITCHERS:
            hitters[p.team] += 1
        if p.game:
            games.add(p.game)
    if salary > rules.salary_cap:
        problems.append(f"salary {salary} over cap {rules.salary_cap}")
    if rules.max_per_team and any(n > rules.max_per_team for n in teams.values()):
        problems.append(f"more than {rules.max_per_team} from one team")
    if rules.max_hitters_per_team and any(n > rules.max_hitters_per_team for n in hitters.values()):
        problems.append(f"more than {rules.max_hitters_per_team} hitters from one team")
    if rules.min_teams and len(teams) < rules.min_teams:
        problems.append(f"fewer than {rules.min_teams} teams")
    if rules.min_games and len(games) < rules.min_games:
        problems.append(f"fewer than {rules.min_games} games")
    return problems
