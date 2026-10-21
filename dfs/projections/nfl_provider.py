"""NFL provider: real nflverse baseline + structured facts for the agents, point-in-time.

- baseline: ``dfs.projections.nfl.project_week`` (market-implied, top-down, simulated);
  contest players it cannot match fall back to the site-average baseline, with a note.
- known facts (research agent): official injury report rows, roster status, depth chart, usage.
- usage (blinded forecaster): per-player history of volume and production in raw stats only:
  no fantasy points, no salaries, no site averages, no betting lines.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from dfs.data import nflverse as nv
from dfs.data.teams import canonicalize
from dfs.models import Contest, Fact
from dfs.projections.nfl import NFLData, project_week
from dfs.providers import GenericProvider, SlateContext
from dfs.sources import require

_DATA: dict[tuple, NFLData] = {}


def load_data(season: int, max_age_hours: float | None = None) -> NFLData:
    key = (season, max_age_hours)
    if key not in _DATA:
        require("nflverse")
        _DATA[key] = NFLData.load(range(max(2016, season - 7), season + 1), max_age_hours=max_age_hours)
    return _DATA[key]


def infer_week(contest: Contest, schedules: pd.DataFrame, season: int | None = None,
               now: datetime | None = None) -> tuple[int, int]:
    """(season, week) whose schedule contains the contest's games (by teams and date)."""
    games = []
    for p in contest.players.values():
        if p.game and "@" in p.game:
            a, h = p.game.split("@")
            games.append((canonicalize(a, strict=False), canonicalize(h, strict=False),
                          p.start_time.astimezone(timezone.utc).date() if p.start_time else None))
    s = schedules
    if season is not None:
        s = s[s["season"] == season]
    votes: dict[tuple[int, int], int] = defaultdict(int)
    for a, h, d in set(games):
        m = s[(s["away_team"] == a) & (s["home_team"] == h)]
        if d is not None and len(m) > 1:
            m = m[[abs((pd.Timestamp(g).date() - d).days) <= 1 for g in m["gameday"]]]
        elif d is None and len(m) > 1:  # FD files carry no dates: take the nearest game on or after today
            today = (now or datetime.now(timezone.utc)).date()
            m = m.assign(_gap=[(pd.Timestamp(g).date() - today).days for g in m["gameday"]])
            ahead = m[m["_gap"] >= -3]
            m = ahead.sort_values("_gap").head(1) if len(ahead) else m.sort_values("_gap").tail(1)
        for _, r in m.iterrows():
            votes[(int(r["season"]), int(r["week"]))] += 1
    if not votes:
        raise ValueError("could not find the contest's games in the nflverse schedule")
    return max(votes.items(), key=lambda kv: kv[1])[0]


def kickoffs(contest: Contest, data: NFLData, season: int, week: int) -> dict[str, datetime]:
    """Kickoff (UTC) of each contest game from the schedule; FD files carry no times, and late swap needs them."""
    from zoneinfo import ZoneInfo

    sched = nv.week_schedule(season, week, data.schedules)
    out = {}
    for g in contest.games:
        if "@" not in g:
            continue
        a, h = (canonicalize(x, strict=False) for x in g.split("@"))
        m = sched[(sched["away_team"] == a) & (sched["home_team"] == h)]
        if len(m) and isinstance(m.iloc[0].get("gametime"), str):
            r = m.iloc[0]
            et = datetime.fromisoformat(f"{r['gameday']}T{r['gametime']}").replace(tzinfo=ZoneInfo("America/New_York"))
            out[g] = et.astimezone(timezone.utc)
    return out


def _num(x) -> float:
    try:
        v = float(x)
        return 0.0 if np.isnan(v) else v
    except (TypeError, ValueError):
        return 0.0


class NFLProvider:
    name = "nfl"

    def __init__(self, season: int | None = None, week: int | None = None, max_age_hours: float | None = None,
                 n_sims: int = 4000):
        self.season, self.week, self.max_age_hours, self.n_sims = season, week, max_age_hours, n_sims

    def prepare(self, contest: Contest, now: datetime | None = None) -> SlateContext:
        season_guess = self.season or (now or datetime.now(timezone.utc)).year
        data = load_data(season_guess if (now or datetime.now(timezone.utc)).month >= 3 else season_guess - 1,
                         self.max_age_hours)
        season, week = (self.season, self.week) if (self.season and self.week) else \
            infer_week(contest, data.schedules, self.season, now)
        proj, frame = project_week(season, week, contest.rules.scoring, players=contest.players, data=data,
                                   n_sims=self.n_sims)
        notes = [f"nflverse baseline for {season} week {week} (data strictly before the week)"]
        unmatched = frame.attrs.get("unmatched", [])
        if unmatched:
            notes.append(f"{len(unmatched)} contest players unmatched to nflverse: "
                         + ", ".join(u["name"] for u in unmatched[:12]) + (" ..." if len(unmatched) > 12 else ""))
        generic = GenericProvider().prepare(contest)
        ruled_out = set(generic.ruled_out)
        for k, p in contest.players.items():  # unmatched players keep a site-average baseline
            if k not in proj and k in generic.baseline:
                proj[k] = generic.baseline[k]
        mapped = frame[frame["player_key"].notna()] if "player_key" in frame.columns else frame.iloc[0:0]
        for _, r in mapped.iterrows():
            if r.get("kind") == "player" and _num(r.get("p_play", 1.0)) <= 0.0:
                ruled_out.add(r["player_key"])
        for k in ruled_out:
            proj.pop(k, None)

        market, known, usage, sfacts = self._facts(contest, data, season, week, mapped, now=now)
        ids = {r["player_key"]: (f"DST:{r['team']}" if r.get("kind") == "dst" else r["gsis_id"])
               for _, r in mapped.iterrows() if isinstance(r.get("player_key"), str)}
        return SlateContext(baseline=proj, market=market, known=known, usage=usage, structured_facts=sfacts,
                            ruled_out=ruled_out, notes=notes, data_source=f"nflverse {season} wk{week}",
                            ids=ids, season=season, week=week, kickoffs=kickoffs(contest, data, season, week))

    # ------------------------------------------------------------------ facts
    def _facts(self, contest: Contest, data: NFLData, season: int, week: int, mapped: pd.DataFrame,
               now: datetime | None = None):
        """``now``: game-day inactives (roster status INA) are shown only from 90 minutes before a game's
        kickoff, when the NFL announces them. Without ``now`` they stay hidden (backtests, experiments)."""
        sched = nv.week_schedule(season, week, data.schedules)
        ko = kickoffs(contest, data, season, week)
        inj = nv.week_injuries(season, week, data.injuries)
        hist = data.player_games[(data.player_games["season"] == season) & (data.player_games["week"] < week)]
        rows_by_key = {r["player_key"]: r for _, r in mapped.iterrows()}
        market, known, usage, sfacts = {}, {}, {}, {}
        for g in contest.games:
            a, h = (canonicalize(x, strict=False) for x in g.split("@"))
            m = sched[(sched["away_team"] == a) & (sched["home_team"] == h)]
            if len(m):
                r = m.iloc[0]
                hi, ai = nv.implied_points(r["spread_line"], r["total_line"])
                fav = h if r["spread_line"] > 0 else a
                market[g] = (f"{fav} favoured by {abs(r['spread_line']):g}; total {r['total_line']:g}; "
                             f"implied {h} {float(hi):.1f}, {a} {float(ai):.1f}; roof {r.get('roof')}; "
                             f"surface {r.get('surface')}; kickoff {r.get('gameday')} {r.get('gametime')} ET")
            facts: list[Fact] = []
            lines_known: list[str] = []
            team_inj = inj[inj["team"].isin({a, h})].copy()
            team_inj["_skill"] = (~team_inj["position"].isin(["QB", "RB", "WR", "TE", "K"])).astype(int)
            team_inj = team_inj.sort_values(["_skill", "team"])
            for _, ir in team_inj.iterrows():
                status = ir.get("report_status")
                prac = ir.get("practice_status")
                if not isinstance(status, str) and not isinstance(prac, str):
                    continue
                txt = (f"{ir['full_name']} ({ir.get('position')}, {ir['team']}): official report "
                       f"{status if isinstance(status, str) else 'no game designation'}"
                       f"{' (' + str(ir.get('report_primary_injury')) + ')' if isinstance(ir.get('report_primary_injury'), str) else ''}; "
                       f"practice: {prac if isinstance(prac, str) else 'n/a'}")
                lines_known.append(txt)
                if ir.get("position") in ("QB", "RB", "WR", "TE", "K") and (isinstance(status, str) or prac == "Did Not Participate In Practice"):
                    st = {"out": "out", "doubtful": "doubtful", "questionable": "questionable"}.get(
                        str(status).strip().lower()) if isinstance(status, str) else None
                    facts.append(Fact(subject=ir["full_name"], team=ir["team"], kind="injury", text=txt,
                                      source_url="https://github.com/nflverse/nflverse-data (NFL official injury report)",
                                      as_of=f"{season} week {week}", confidence="high", status=st))
            ulines = []
            inactives_out = now is not None and g in ko and now >= ko[g] - timedelta(minutes=90)
            for k in sorted(k for k, p in contest.players.items() if p.game == g):
                r = rows_by_key.get(k)
                if r is None or r.get("kind") != "player":
                    continue
                gsis = r.get("gsis_id")
                hp = hist[hist["gsis_id"] == gsis].sort_values("week")
                if hp.empty and _num(r.get("p_play")) < 0.2:
                    continue
                last3 = hp.tail(3)
                pos = r.get("pos")

                def per_game(df, col):
                    v = df[col].mean() if len(df) else 0.0
                    return 0.0 if pd.isna(v) else float(v)

                roster = r.get("status")
                if roster == "INA" and not inactives_out:
                    roster = "ACT"  # game-day inactive lists are not out yet at this time
                parts = [f"{k} [{contest.players[k].name}, {r.get('team')} {pos}, depth {int(_num(r.get('depth_rank')))}"
                         f", roster {roster}, report {r.get('rep')}, practice {r.get('prac')}]",
                         f"games {len(hp)}; snap% last3 {100 * per_game(last3, 'offense_pct'):.0f} season {100 * per_game(hp, 'offense_pct'):.0f}"]
                if pos == "QB":
                    parts.append(f"att/g {per_game(hp, 'attempts'):.1f} pass yds/g {per_game(hp, 'passing_yards'):.0f} "
                                 f"pass TD {hp['passing_tds'].sum():.0f} INT {hp['passing_interceptions'].sum():.0f} "
                                 f"carries/g {per_game(hp, 'carries'):.1f} rush yds/g {per_game(hp, 'rushing_yards'):.0f} rush TD {hp['rushing_tds'].sum():.0f}")
                else:
                    tsh = hp["targets"].sum() / max(hp["team_targets"].sum(), 1)
                    csh = hp["carries"].sum() / max(hp["team_carries"].sum(), 1)
                    parts.append(f"targets/g {per_game(hp, 'targets'):.1f} (last3 {per_game(last3, 'targets'):.1f}, share {100 * tsh:.0f}%) "
                                 f"rec/g {per_game(hp, 'receptions'):.1f} rec yds/g {per_game(hp, 'receiving_yards'):.0f} "
                                 f"carries/g {per_game(hp, 'carries'):.1f} (last3 {per_game(last3, 'carries'):.1f}, share {100 * csh:.0f}%) "
                                 f"rush yds/g {per_game(hp, 'rushing_yards'):.0f} TD {hp['receiving_tds'].sum() + hp['rushing_tds'].sum():.0f} "
                                 f"expected TD/g {per_game(hp, 'x_rec_td') + per_game(hp, 'x_rush_td'):.2f}")
                ulines.append(" | ".join(parts))
            usage[g] = "\n".join(ulines) or "(no usage history)"
            known[g] = "\n".join(lines_known[:60]) or "(no injury report rows)"
            sfacts[g] = facts
        return market, known, usage, sfacts
