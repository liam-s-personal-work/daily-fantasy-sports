"""NBA baseline provider: a parsed DK/FD NBA contest -> ``SlateContext``.

Data: ESPN player box scores from the sportsdataverse release files (``dfs.data.nba``), this
season and the two before it. Everything is point-in-time: only games that tipped off before
the slate's first game (or ``now``, if earlier) are read.

Model (tuned on the first three weeks of 2025-26 with 2024-25 as the prior season, no lookahead;
``docs/results/nba_baseline.md``): fantasy points = minutes x points per minute.

- Minutes: this season's minutes per game played, with ``k_min`` pseudo-games of last season's
  average (fewer for a player on a new team). Rookies are shrunk toward ``rookie_minutes``.
- Points per minute: this season's rate with ``k_rate`` pseudo-minutes of last season's rate,
  itself shrunk toward the position-group league rate. A last season shorter than
  ``min_prior_games`` (a long injury) adds the season before it.
- Spread: ``sd = sd_a + sd_b * mean`` given he plays, right-skewed quantiles.
- Availability: missing from his team's last box score (injured or inactive) gives
  ``p_zero_missed``. In uniform but unused (a coach's decision) gives ``p_zero_benched``, at most
  ``benched_minutes``, and a "probable" listing cannot lower it. Otherwise a small chance by
  last-game minutes. Tonight's injury report
  comes from research: cited facts rule players out or set p_zero (``agents.apply_status``).

Players with no NBA game on record get about 10 minutes at 90% of the league rate, and are
listed in ``notes``.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from dfs.data import nba as nbadata
from dfs.distributions import quantiles_from_mean_sd
from dfs.io.contest_file import norm_name
from dfs.models import Contest, Fact, Projection
from dfs.providers import P_ZERO_BY_STATUS, GenericProvider, SlateContext, status_out
from dfs.sources import require

SOURCE_URL = "https://github.com/sportsdataverse/sportsdataverse-data/releases"
GUARDS, FORWARDS = {"PG", "SG", "G"}, {"SF", "PF", "F"}


@dataclass(frozen=True)
class NBAParams:
    """Tuned on 2025-26 games 1-21 days in (2024-25 as the prior season), no lookahead: RMSE 9.91 on
    players who played vs 11.05 for last season's DK average (docs/results/nba_baseline.md)."""

    k_min: float = 0.8  # pseudo-games of last season's minutes per game
    k_min_moved: float = 0.5  # ... for a player on a new team
    k_rate: float = 250.0  # pseudo-minutes of last season's points per minute
    k_rate_moved: float = 75.0
    m0: float = 50.0  # pseudo-minutes of the position-group league rate inside last season's rate
    prior_mpg_scale: float = 0.97  # last season's minutes (playoffs included) run high early on
    rookie_minutes: float = 10.0  # no prior season
    rookie_k_min: float = 0.25
    rookie_rate_share: float = 0.9  # of the league rate
    rookie_k_rate: float = 100.0
    min_prior_games: int = 20  # fewer games last season: add the season before (long injuries)
    sd_a: float = 6.715  # sd = sd_a + sd_b * mean, given he plays
    sd_b: float = 0.1365
    skew: float = 0.2  # empirical z10 -1.13, z90 +1.33, median -0.13 sd
    p_zero_missed: float = 0.6  # missing from the team's last box score (2025-26: 0.58-0.66 miss the next)
    p_zero_benched: float = 0.55  # in uniform but did not play (coach's decision): 0.46-0.54 sit again
    benched_minutes: float = 10.0  # a healthy scratch who does get in plays spot minutes
    # did not play next game, by minutes in the last game (2025-26: <10, 10-20, 20-30, 30+)
    p_zero_by_minutes: tuple[tuple[float, float], ...] = ((10.0, 0.37), (20.0, 0.095), (30.0, 0.04), (999.0, 0.02))


def _group(pos: str) -> str:
    p = (pos or "").upper()
    return "G" if p in GUARDS else "C" if p == "C" else "F"


def player_table(box: pd.DataFrame, cutoff: datetime, season: int, params: NBAParams = NBAParams()) -> pd.DataFrame:
    """One row per athlete with history before ``cutoff``: projected minutes, rate and mean (given he plays)."""
    hist = nbadata.before(box, cutoff)
    hist = hist[~hist["did_not_play"] & (hist["minutes"] > 0)].copy()
    hist["dk"] = nbadata.dk_points(hist)
    hist["group"] = hist["athlete_position_abbreviation"].map(_group)
    cur = hist[hist["season"] == season].sort_values("start")
    prev = hist[hist["season"] == season - 1].sort_values("start")
    # A short last season (long injury) borrows the season before it: Haliburton missed all of 2025-26.
    n_prev = prev.groupby("athlete_id").size()
    older = hist[(hist["season"] == season - 2) & ~hist["athlete_id"].isin(n_prev[n_prev >= params.min_prior_games].index)]
    prev = pd.concat([older, prev]).sort_values("start")

    def agg(d: pd.DataFrame, tag: str) -> pd.DataFrame:
        g = d.groupby("athlete_id")
        return pd.DataFrame({f"n_{tag}": g.size(), f"min_{tag}": g["minutes"].sum(), f"dk_{tag}": g["dk"].sum()})

    t = agg(cur, "cur").join(agg(prev, "prev"), how="outer").fillna(0.0)
    last = hist.sort_values("start").groupby("athlete_id").tail(1).set_index("athlete_id")
    t["name"] = last["athlete_display_name"]
    t["team"] = last["team_abbreviation"]
    t["group"] = last["group"]
    t["last_minutes"] = last["minutes"]
    first_cur = cur.groupby("athlete_id")["team_abbreviation"].first()
    last_prev = prev.groupby("athlete_id")["team_abbreviation"].last()
    t["moved"] = (first_cur.reindex(t.index).notna() & last_prev.reindex(t.index).notna()
                  & (first_cur.reindex(t.index) != last_prev.reindex(t.index)))

    # position-group league rate (points per minute) over the prior seasons
    pool = prev if len(prev) else hist
    league = (pool.groupby("group")["dk"].sum() / pool.groupby("group")["minutes"].sum()).to_dict()
    lg = t["group"].map(league).fillna(float(pool["dk"].sum() / max(pool["minutes"].sum(), 1.0)))

    rookie = t["n_prev"] == 0
    prior_mpg = (t["min_prev"] / t["n_prev"].clip(lower=1)) * params.prior_mpg_scale
    prior_rate = (t["dk_prev"] + params.m0 * lg) / (t["min_prev"] + params.m0)
    km = np.where(t["moved"], params.k_min_moved, params.k_min)
    kr = np.where(t["moved"], params.k_rate_moved, params.k_rate)
    vet_min = (t["min_cur"] + km * prior_mpg) / (t["n_cur"] + km)
    vet_rate = (t["dk_cur"] + kr * prior_rate) / (t["min_cur"] + kr)
    new_min = (t["min_cur"] + params.rookie_k_min * params.rookie_minutes) / (t["n_cur"] + params.rookie_k_min)
    new_rate = ((t["dk_cur"] + params.rookie_k_rate * params.rookie_rate_share * lg)
                / (t["min_cur"] + params.rookie_k_rate))
    t["minutes"] = np.where(rookie, new_min, vet_min)
    t["rate"] = np.where(rookie, new_rate, vet_rate)
    t["mean"] = t["minutes"] * t["rate"]
    t["sd"] = params.sd_a + params.sd_b * t["mean"]
    t.attrs["league_rate"] = league
    return t


def p_zero_after_playing(last_minutes: float, params: NBAParams) -> float:
    for cap, pz in params.p_zero_by_minutes:
        if last_minutes < cap:
            return pz
    return params.p_zero_by_minutes[-1][1]


def team_last_games(box: pd.DataFrame, cutoff: datetime,
                    season: int) -> dict[str, tuple[pd.Timestamp, set[int], set[int], set[int]]]:
    """ESPN team code -> (tip-off of its last game this season, athletes who played, who started, and who
    were in uniform but did not play). Injured and inactive players are not in the box score at all."""
    h = nbadata.before(box, cutoff)
    h = h[h["season"] == season]
    out = {}
    for team, g in h.groupby("team_abbreviation"):
        gid = g.sort_values("start")["game_id"].iloc[-1]
        last = g[g["game_id"] == gid]
        played = last[~last["did_not_play"] & (last["minutes"] > 0)]
        benched = last[last["did_not_play"] | (last["minutes"] <= 0)]
        out[team] = (last["start"].iloc[0], set(played["athlete_id"]),
                     set(played[played["starter"].fillna(False).astype(bool)]["athlete_id"]), set(benched["athlete_id"]))
    return out


def match_players(contest: Contest, table: pd.DataFrame) -> tuple[dict[str, int], list[str]]:
    """Contest key -> ESPN athlete id: exact name on the team, then anywhere (moved teams), then
    first initial + last name on the team, then a close fuzzy match on the team."""
    names = table.assign(nn=table["name"].fillna("").map(norm_name))
    by_name: dict[str, list[tuple[int, str]]] = {}
    for aid, r in names.iterrows():
        by_name.setdefault(r["nn"], []).append((int(aid), r["team"]))
    out: dict[str, int] = {}
    missing: list[str] = []
    for k, p in contest.players.items():
        team = nbadata.espn_team(p.team)
        nn = norm_name(p.name)
        cands = by_name.get(nn, [])
        on_team = [a for a, t in cands if t == team]
        pick = on_team[0] if len(on_team) == 1 else cands[0][0] if len(cands) == 1 else None
        if pick is None:
            roster = names[names["team"] == team]
            parts = nn.split()
            if parts:
                same = roster[roster["nn"].map(lambda s: s.split()[-1:] == parts[-1:] and s[:1] == nn[:1])]
                if len(same) == 1:
                    pick = int(same.index[0])
            if pick is None and len(roster):
                close = difflib.get_close_matches(nn, list(roster["nn"]), n=2, cutoff=0.88)
                if len(close) == 1:
                    pick = int(roster.index[roster["nn"] == close[0]][0])
        if pick is None:
            missing.append(k)
        else:
            out[k] = pick
    return out, missing


def reconcile_minutes(contest: Contest, baseline: dict[str, Projection], total: float = 242.0,
                      floor: float = 0.85) -> tuple[dict[str, Projection], dict[str, float]]:
    """Keep each team's expected minutes near ``total`` (48 x 5 plus a little overtime).

    Regulars' minutes come from games that players now returning from injury or inactivity sat
    out, so a possible return's expected minutes come out of the regulars, in proportion, down to
    ``floor``. Healthy scratches are left out of the sum: their garbage-time minutes are already in
    the games the regulars' numbers were fit on (the backtest found those unbiased). Run after
    tonight's statuses are applied: players ruled out are gone and a confirmed return counts in
    full. Returns (baseline, team -> factor)."""
    teams: dict[str, list[str]] = {}
    for k in baseline:
        teams.setdefault(contest.players[k].team, []).append(k)
    out, factors = dict(baseline), {}
    for team, keys in teams.items():
        exp = {k: baseline[k].components.get("minutes", 0.0) * (1 - baseline[k].p_zero) for k in keys
               if not baseline[k].components.get("benched")}
        unc = sum(v for k, v in exp.items() if baseline[k].components.get("uncertain"))
        reg = sum(v for k, v in exp.items() if not baseline[k].components.get("uncertain"))
        if reg <= 0 or reg + unc <= total:
            continue
        f = max(floor, min(1.0, (total - unc) / reg))
        if f >= 1.0:
            continue
        factors[team] = round(f, 3)
        for k in keys:
            pr = baseline[k]
            if pr.components.get("uncertain") or pr.components.get("benched"):
                continue
            comp = dict(pr.components, minutes=round(pr.components.get("minutes", 0.0) * f, 2), minutes_factor=round(f, 3))
            out[k] = pr.model_copy(update={"mean": round(pr.mean * f, 3), "p10": round(pr.p10 * f, 2),
                                           "p50": round(pr.p50 * f, 2), "p90": round(pr.p90 * f, 2), "components": comp})
    return out, factors


def lastcall_projections(contest: Contest, dossiers: dict, excluded: set[str], starters: dict[str, set[str]],
                         now: datetime, season: int | None = None,
                         provider: "NBAProvider | None" = None) -> tuple[dict[str, Projection], list[str]]:
    """Re-project an NBA slate at last call: the same baseline (only games before the first tip), the run's
    research statuses, the players ruled out since, and the starter-aware minutes tier for confirmed lineups."""
    from dfs.stages import agents

    prov = provider or NBAProvider(season=season)
    ctx = prov.prepare(contest, now=now)
    base = {k: v for k, v in ctx.baseline.items() if k not in excluded and k not in ctx.ruled_out}
    statused, _ = agents.apply_status(contest, dossiers, base)
    return prov.after_research(contest, statused, starters=starters)


class NBAProvider:
    name = "nba"

    def after_research(self, contest: Contest, baseline: dict[str, Projection],
                       starters: dict[str, set[str]] | None = None) -> tuple[dict[str, Projection], list[str]]:
        """Called once tonight's statuses are applied: team minutes are reconciled, then the learned minutes
        model (``dfs.projections.nba_minutes``, if its files are in data/learned/) re-estimates each matched
        player's chance of playing and minutes from tonight's roster. Research statuses still apply on top."""
        out, factors = reconcile_minutes(contest, baseline)
        notes = [f"{t}: regulars' minutes x{f} (players returning from injury share the 242)" for t, f in sorted(factors.items())]
        try:
            out, n = self._apply_minutes_model(contest, out, baseline, starters or {})
            if n:
                notes.append(f"minutes model applied to {n} players"
                             + (f" (confirmed starters for {len(starters)} teams)" if starters else ""))
        except Exception as e:  # the learned layer never blocks a run: the baseline stands
            notes.append(f"minutes model not applied ({e})")
        return out, notes

    def _apply_minutes_model(self, contest: Contest, out: dict[str, Projection], statused: dict[str, Projection],
                             starters: dict[str, set[str]]) -> tuple[dict[str, Projection], int]:
        live = getattr(self, "_live", None)
        if live is None:
            return out, 0
        from dfs.projections import nba_minutes as nm

        model = nm.MinutesModel.load(self.minutes_dir)
        if model is None:
            return out, 0
        info, table, lastg = live["info"], live["table"], live["lastg"]
        keys = [k for k in out if k in info]
        if not keys:
            return out, 0
        active = {info[k]["athlete_id"] for k in keys}
        vac: dict[str, float] = {}
        for k in keys:
            team = info[k]["team"]
            if team not in vac:
                tg = lastg.get(team)
                gone = [a for a in (tg[1] if tg else set()) if a not in active and a in table.index]
                vac[team] = float(table.loc[gone, "minutes"].sum()) if gone else 0.0
        rows = []
        for k in keys:
            f = info[k]
            rows.append({**{c: f[c] for c in ("athlete_id", "team", "opp", "pos", "rate", "last_minutes", "benched",
                                               "absent_last", "n_cur", "n_prev", "moved", "rest_days", "b2b", "home")},
                         "slate": "live", "season": live["season"], "key": k, "p_zero": f["p_zero_feature"],
                         "minutes": float(out[k].components.get("minutes", f["minutes"])), "vacated_min": vac[f["team"]]})
        feats = nm.build_features(pd.DataFrame(rows), self.box, cutoffs={"live": live["cutoff"]})
        p_play, mins = model.predict(feats)
        # last call: teams whose starting five is confirmed use the starter-aware tier (public ~30 min before tip)
        lc_teams = {nbadata.espn_team(t) for t in starters}
        lc = nm.MinutesModel.load(self.minutes_dir, "lastcall") if lc_teams else None
        if lc is not None:
            starting = {k for ks in starters.values() for k in ks}
            feats["starter_tonight"] = [float(k in starting) for k in feats["key"]]
            sel = feats["team"].isin(lc_teams).to_numpy()
            if sel.any():
                pp_lc, m_lc = lc.predict(feats[sel])
                p_play, mins = p_play.copy(), mins.copy()
                p_play[sel], mins[sel] = pp_lc, m_lc
        res = dict(out)
        for (k, pp, m) in zip(feats["key"], p_play, mins):
            f, pr = info[k], out[k]
            orig = live["p_zero"][k]
            # research statuses still count (the model never saw tonight's injury report): a status that moved
            # p_zero sets the injury risk; a player missing from the last box score with no news keeps it
            p_status = statused[k].p_zero if abs(statused[k].p_zero - orig) > 1e-9 else (
                self.params.p_zero_missed if f["absent_last"] else 0.0)
            if f["benched"] and p_status <= self.params.p_zero_benched:
                p_status = 0.0  # a healthy scratch's risk is the model's (it sees he was benched)
            play = float(pp) * (1.0 - p_status)
            cond_new = float(m) * f["rate"]
            cond_old = pr.mean / (1 - pr.p_zero) if pr.p_zero < 1 else cond_new
            g = cond_new / cond_old if cond_old > 0 else 1.0
            res[k] = pr.model_copy(update={
                "mean": round(play * cond_new, 3), "p_zero": round(1.0 - play, 3),
                "p10": round(pr.p10 * g, 2), "p50": round(pr.p50 * g, 2), "p90": round(pr.p90 * g, 2),
                "components": dict(pr.components, minutes_model=round(float(m), 2), p_play_model=round(float(pp), 3),
                                   p_status=round(p_status, 3))})
        return res, len(keys)

    def __init__(self, season: int | None = None, *, box: pd.DataFrame | None = None, params: NBAParams | None = None,
                 max_age_hours: float | None = None, minutes_dir=None):
        from dfs.projections.nba_minutes import LEARNED

        self.season = season
        self.box = box
        self.params = params or NBAParams()
        self.max_age_hours = max_age_hours
        self.minutes_dir = minutes_dir or LEARNED  # learned minutes model (None files: baseline only)

    def prepare(self, contest: Contest, now: datetime | None = None) -> SlateContext:
        if contest.rules.sport != "nba":
            raise ValueError(f"NBAProvider got a {contest.rules.sport} contest")
        try:
            return self._prepare(contest, now)
        except nbadata.sdv.DataUnavailable as e:
            ctx = GenericProvider().prepare(contest, now)
            ctx.notes.insert(0, f"NBA stats baseline unavailable ({e}); using site averages")
            return ctx

    def _prepare(self, contest: Contest, now: datetime | None) -> SlateContext:
        require("sportsdataverse")
        starts = [p.start_time for p in contest.players.values() if p.start_time]
        first = min(starts) if starts else (now or datetime.now(timezone.utc))
        cutoff = min(first, now) if now else first
        season = self.season or nbadata.season_for(first.date())
        box = self.box if self.box is not None else nbadata.load_box([season - 2, season - 1, season],
                                                                       self.max_age_hours, today=first.date())
        self.box = box
        table = player_table(box, cutoff, season, self.params)
        ids, missing = match_players(contest, table)
        lastg = team_last_games(box, cutoff, season)
        notes = [f"NBA baseline: ESPN box scores {season - 2}-{season} before {cutoff:%Y-%m-%d %H:%M} UTC; "
                 f"{len(ids)} of {len(contest.players)} players matched"]
        if missing:
            notes.append(f"{len(missing)} players with no NBA game on record (10-minute rookie rule): "
                         + ", ".join(sorted(contest.players[k].name for k in missing)[:40]))
        notes += nbadata.LOAD_WARNINGS

        base: dict[str, Projection] = {}
        out: set[str] = set()
        facts: dict[str, list[Fact]] = {}
        hist = nbadata.before(box, cutoff)
        team_last = hist.groupby("team_abbreviation")["start"].max()
        info: dict[str, dict] = {}
        known_lines: dict[str, list[str]] = {}
        usage_lines: dict[str, list[str]] = {}
        for k, p in contest.players.items():
            if status_out(p.injury_status):
                out.add(k)
                continue
            aid = ids.get(k)
            team = nbadata.espn_team(p.team)
            tg = lastg.get(team)
            g = p.game or "?"
            pz = P_ZERO_BY_STATUS.get((p.injury_status or "").upper(), 0.0)
            if aid is None:  # no NBA game on record: about 10 minutes at 90% of the league rate
                mean = self.params.rookie_minutes * self.params.rookie_rate_share * float(
                    table.attrs["league_rate"].get(_group(p.positions[0] if p.positions else ""), 1.0))
                if tg is not None:
                    pz = max(pz, self.params.p_zero_missed)
                p10, p50, p90 = quantiles_from_mean_sd(mean, self.params.sd_a + self.params.sd_b * mean, self.params.skew)
                base[k] = Projection(player_key=k, mean=round(mean * (1 - pz), 3), p10=round(p10, 2), p50=round(p50, 2),
                                     p90=round(p90, 2), p_zero=round(pz, 3), source="baseline",
                                     components={"minutes": self.params.rookie_minutes, "n_cur": 0.0, "n_prev": 0.0,
                                                 "uncertain": 1.0},
                                     notes="no NBA games on record")
                continue
            r = table.loc[aid]
            missed = tg is not None and aid not in tg[1]  # did not play the team's last game
            benched = missed and aid in tg[3]  # ... in uniform: a coach's decision, not an injury
            minutes, mean = float(r["minutes"]), float(r["mean"])
            floor = 0.0
            if not missed:
                pz = max(pz, p_zero_after_playing(float(r["last_minutes"]), self.params))
            elif benched:
                floor = self.params.p_zero_benched  # "probable" says available, not in the rotation
                pz = max(pz, floor)
                if minutes > self.params.benched_minutes:
                    mean *= self.params.benched_minutes / minutes
                    minutes = self.params.benched_minutes
            else:
                pz = max(pz, self.params.p_zero_missed)
            if missed:
                when = f"{tg[0].tz_convert('America/New_York'):%b %d}"
                text = (f"{p.name} was in uniform but did not play (coach's decision) in {p.team}'s last game ({when})"
                        if benched else f"{p.name} was not in {p.team}'s last box score ({when}): injured or inactive")
                facts.setdefault(g, []).append(Fact(subject=p.name, team=p.team, kind="status", text=text, status="none",
                                                    source_url=SOURCE_URL, confidence="medium",
                                                    as_of=f"{tg[0]:%Y-%m-%d}"))
                known_lines.setdefault(g, []).append(text)
            elif tg is not None and aid in tg[2]:
                known_lines.setdefault(g, []).append(f"{p.name} started {p.team}'s last game")
            rest = ((cutoff - team_last[team]).total_seconds() / 86400) if team in team_last.index else 3.0
            home = (p.game or "").split("@")[-1]
            info[k] = {"athlete_id": int(aid), "team": team, "opp": nbadata.espn_team(p.opponent or ""),
                       "pos": {"G": "PG,SG", "F": "PF,SF", "C": "C"}.get(str(r["group"]), "PF,SF"),
                       "minutes": minutes, "rate": float(r["rate"]), "last_minutes": float(r["last_minutes"]),
                       "benched": float(benched), "absent_last": float(missed and not benched),
                       "n_cur": float(r["n_cur"]), "n_prev": float(r["n_prev"]), "moved": float(bool(r["moved"])),
                       "rest_days": rest, "b2b": float(rest < 1.5), "home": float(p.team == home),
                       # the model was trained on players known active: their own p_zero by last game only
                       "p_zero_feature": (self.params.p_zero_benched if benched else 0.0 if missed
                                          else p_zero_after_playing(float(r["last_minutes"]), self.params))}
            sd = max(self.params.sd_a + self.params.sd_b * mean, 1.0)
            p10, p50, p90 = quantiles_from_mean_sd(mean, sd, self.params.skew)
            base[k] = Projection(player_key=k, mean=round(mean * (1 - pz), 3), p10=round(p10, 2), p50=round(p50, 2),
                                 p90=round(p90, 2), p_zero=round(pz, 3), source="baseline",
                                 components={"minutes": round(minutes, 2), "rate": round(float(r["rate"]), 4),
                                             "n_cur": float(r["n_cur"]), "n_prev": float(r["n_prev"]),
                                             "moved": float(bool(r["moved"])), "uncertain": float(missed),
                                             "benched": float(benched), "p_zero_floor": floor})
            mins_cur = r["min_cur"] / r["n_cur"] if r["n_cur"] else None
            mins_prev = r["min_prev"] / r["n_prev"] if r["n_prev"] else None
            usage_lines.setdefault(g, []).append(
                f"- {k}: minutes per game this season {mins_cur:.1f} ({int(r['n_cur'])} g)" if mins_cur is not None
                else f"- {k}: no games this season")
            if mins_prev is not None:
                usage_lines[g][-1] += f"; last season {mins_prev:.1f} ({int(r['n_prev'])} g)"
        self._live = {"cutoff": pd.Timestamp(cutoff).tz_convert("UTC"), "season": season, "table": table,
                      "lastg": lastg, "info": info, "p_zero": {k: base[k].p_zero for k in info}}
        return SlateContext(baseline=base, ruled_out=out, structured_facts=facts,
                            known={g: "\n".join(v) for g, v in known_lines.items()},
                            usage={g: "\n".join(v) for g, v in usage_lines.items()},
                            notes=notes, data_source=f"sportsdataverse ESPN NBA box scores ({season - 2}-{season})",
                            ids={k: str(v) for k, v in ids.items()}, season=season,
                            kickoffs={p.game: p.start_time for p in contest.players.values() if p.game and p.start_time})
