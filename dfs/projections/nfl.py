"""NFL statistical baseline: market-implied, top-down, point-in-time projections.

Layers (each fit only on games before the target week):

1. Market: implied team points from ``spread_line`` / ``total_line``.
2. Team volume: per-stat OLS on implied points for and against plus shrunk, recency-weighted
   team-offense and opponent-defense tendencies (``TeamModel``).
3. Usage: recency-weighted, empirical-Bayes shrunk conditional shares of team targets,
   carries and expected TDs (ff_opportunity ``*_exp``); QBs get a start probability.
4. Availability: P(plays) by (injury designation, practice status, recent role, pre-game
   depth-chart bucket) estimated from history; QBs start down the depth order by P(dressed),
   conditioned on exactly one starting; vacated usage is redistributed with position affinity
   so that expected shares are conserved.
5. Efficiency: catch rate, yards/target, yards/carry shrunk toward the player's own expected
   values from ff_opportunity; team yards / TDs are split by these and rescaled to team totals.
6. Simulation of stat lines (gamma-Poisson counts, gamma yardage, Poisson TDs) scored by a
   vectorized scorer that reproduces ``dfs.scoring`` exactly; DST and kicker are simulated
   from the opponent's / own team projections.

Output: ``Projection`` per player (p10/p50/p90 of the played outcome, ``p_zero`` = P(no
offensive snap / not starting for QBs), ``mean`` = (1 - p_zero) * E[played]).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Iterable, Mapping

import numpy as np
import pandas as pd

from dfs.data import nflverse as nv
from dfs.data.teams import canonicalize
from dfs.models import Player, Projection
from dfs.scoring import load_table, role_for

OFF_POS = ("QB", "RB", "WR", "TE")
POS_MAP = {"QB": "QB", "RB": "RB", "HB": "RB", "FB": "RB", "WR": "WR", "TE": "TE"}
SEASON_WEEKS = 18


# --------------------------------------------------------------------------- parameters


@dataclass(frozen=True)
class NFLParams:
    """Model hyper-parameters. Defaults were tuned on 2024-2025 weeks 3-17 by coordinate descent
    (``dfs.backtest.coordinate_descent``; log in docs/results/nfl_baseline_tuning.json)."""

    # recency / history
    half_life: float = 10.0  # weeks, player usage shares
    eff_half_life: float | None = 16.0  # weeks, efficiency rates (None: same as half_life)
    season_gap: float = 8.0  # extra weeks of age between seasons
    lookback_seasons: int = 1  # prior seasons of player history
    team_change_weight: float = 0.25  # weight of games played for another team (shares only)
    exit_downweight: float = 0.35  # weight of games with snap share < half the player's norm
    # empirical-Bayes prior strengths (pseudo team-units for shares, own units for rates)
    n0_target: float = 25.0
    n0_carry: float = 15.0
    n0_td: float = 1.0  # pseudo team expected TDs
    n0_catch: float = 30.0
    n0_ypt: float = 25.0
    n0_ypc: float = 40.0
    n0_qb_rush: float = 80.0  # QB carry share when starting (pseudo team carries)
    n0_qb_ypc: float = 5.0  # QB yards per carry (scrambles are a skill: shrink far less than RBs)
    td_actual_weight: float = 0.25  # weight of actual TDs vs ff_opportunity expected TDs in TD shares
    # redistribution
    gamma: float = 1.0
    cross_affinity_targets: float = 0.5
    cross_affinity_carries: float = 0.3
    max_target_share: float = 0.45
    max_carry_share: float = 0.90
    # team model
    team_half_life: float = 8.0
    team_k0: float = 8.0
    team_min_season: int = 2019
    # simulation dispersion
    usage_cv: Mapping[str, float] = field(default_factory=lambda: {"QB": 0.06, "RB": 0.20, "WR": 0.38, "TE": 0.36})
    eff_sd: Mapping[str, float] = field(default_factory=lambda: {"QB": 0.18, "RB": 0.18, "WR": 0.08, "TE": 0.08})
    catch_shape: float = 1.3  # gamma shape of yards per catch
    carry_shape: float = 1.5  # gamma shape of (yards per carry + carry_shift)
    carry_shift: float = 2.0
    completion_shape: float = 1.6
    p_exit: Mapping[str, float] = field(default_factory=lambda: {"QB": 0.03, "RB": 0.05, "WR": 0.04, "TE": 0.04})
    use_inactives: bool = False  # treat game-day INA roster status as known (post-inactives run)

    def with_(self, **kw) -> "NFLParams":
        return replace(self, **kw)


# --------------------------------------------------------------------------- data bundle


def _canon(series: pd.Series) -> pd.Series:
    return series.map(lambda t: canonicalize(t, strict=False) if isinstance(t, str) else t)


def _id_crosswalk(players: pd.DataFrame, rosters: pd.DataFrame) -> pd.DataFrame:
    cw = pd.concat([rosters[["pfr_id", "gsis_id"]], players[["pfr_id", "gsis_id"]]], ignore_index=True)
    return cw.dropna().drop_duplicates("pfr_id")


def offense_stat_line(df: pd.DataFrame) -> pd.DataFrame:
    """nflverse weekly player stats -> dfs.scoring offense stat keys (one row per input row).

    Fumbles lost use ``fumbles_lost_total`` when present: it adds fumbles lost on kick and punt
    returns, which DK and FD also charge (reproduces their 2026 season averages for returners).
    """
    g = lambda c: df[c].fillna(0).astype(float) if c in df.columns else pd.Series(0.0, index=df.index)
    fumbles = g("sack_fumbles_lost") + g("rushing_fumbles_lost") + g("receiving_fumbles_lost")
    if "fumbles_lost_total" in df.columns:
        fumbles = np.maximum(fumbles, g("fumbles_lost_total"))
    return pd.DataFrame({
        "pass_yds": g("passing_yards"), "pass_td": g("passing_tds"), "int_thrown": g("passing_interceptions"),
        "rush_yds": g("rushing_yards"), "rush_td": g("rushing_tds"), "rec": g("receptions"),
        "rec_yds": g("receiving_yards"), "rec_td": g("receiving_tds"), "return_td": g("special_teams_tds"),
        "fumble_lost": fumbles,
        "two_pt": g("passing_2pt_conversions") + g("rushing_2pt_conversions") + g("receiving_2pt_conversions"),
        "off_fumble_rec_td": g("fumble_recovery_tds"),
    }, index=df.index)


@dataclass
class NFLData:
    """Every nflverse frame the model needs, loaded once and filtered point-in-time per target."""

    seasons: list[int]
    schedules: pd.DataFrame
    team_games: pd.DataFrame  # one row per team per scheduled game, team stats merged when played
    player_games: pd.DataFrame  # one row per offensive player-game (stats and/or offensive snaps)
    injuries: pd.DataFrame
    rosters: pd.DataFrame
    depth_raw: dict[int, pd.DataFrame]
    kicking: pd.DataFrame  # per team-game FG/XP detail
    kicker_games: pd.DataFrame = field(default_factory=pd.DataFrame)  # per kicker-game FG distances / XP
    cache: dict = field(default_factory=dict, repr=False)  # memo of parameter-free per-week pieces

    @classmethod
    def load(cls, seasons: Iterable[int], max_age_hours: float | None = None) -> "NFLData":
        seasons = sorted(set(int(s) for s in seasons))
        L = lambda n: nv.load(n, seasons, max_age_hours)
        sched = L("schedules")
        ps = L("player_stats")
        ts = L("team_stats")
        snaps = L("snap_counts")
        inj = L("injuries")
        ros = L("rosters_weekly")
        ffo = L("ff_opportunity")
        players = nv.load_players(max_age_hours)
        depth = {s: nv.load_season("depth_charts", s, max_age_hours) for s in seasons}
        return cls.build(seasons, sched, ps, ts, snaps, inj, ros, ffo, players, depth)

    @classmethod
    def build(cls, seasons, sched, ps, ts, snaps, inj, ros, ffo, players, depth) -> "NFLData":
        for df in (ps, ts, snaps, inj, ros):
            if "team" in df.columns:
                df["team"] = _canon(df["team"])
        if "opponent_team" in ps.columns:
            ps["opponent_team"] = _canon(ps["opponent_team"])
        ts["opponent_team"] = _canon(ts["opponent_team"])
        tg = nv.team_games(sched)
        tg = _merge_team_stats(tg, ts, ps)
        pg = _player_games(ps, snaps, ros, ffo, players)
        ros = ros.copy()
        ros["pos"] = ros["position"].map(POS_MAP)
        inj = inj.copy()
        inj["week"] = inj["week"].astype(int)
        inj = nv.latest_injury_rows(inj)
        kg = ps.loc[ps["position"] == "K", ["player_id", "player_display_name", "season", "week", "team", "fg_made",
                                            "fg_made_list", "pat_made"]].rename(
            columns={"player_id": "gsis_id", "player_display_name": "name"})
        return cls(seasons=list(seasons), schedules=sched, team_games=tg, player_games=pg, injuries=inj,
                   rosters=ros, depth_raw=depth, kicking=_kicking(ts), kicker_games=kg.reset_index(drop=True))

    def depth_chart(self, season: int, week: int, as_of: pd.Timestamp | None) -> pd.DataFrame:
        fmt = ("depth_snapshots", season)
        if fmt not in self.cache:
            d = self.depth_raw.get(season)
            self.cache[fmt] = d is not None and "dt" in d.columns and bool(d["dt"].notna().any())
        key = ("depth", season, week, str(as_of) if self.cache[fmt] else None)  # weekly charts ignore as_of
        if key not in self.cache:
            self.cache[key] = self._depth_chart(season, week, as_of)
        return self.cache[key]

    def _depth_chart(self, season: int, week: int, as_of: pd.Timestamp | None) -> pd.DataFrame:
        d = self.depth_raw.get(season)
        if d is None or d.empty:
            return pd.DataFrame(columns=["team", "gsis_id", "pos", "rank", "player_name"])
        if "dt" in d.columns and d["dt"].notna().any():
            key = ("depth_index", season)
            if key not in self.cache:
                self.cache[key] = nv.DepthSnapshots(d)
            return self.cache[key].asof(season, as_of)
        return nv.depth_chart_asof(season, week=week, depth=d)

    def depth_ranks(self, rows: pd.DataFrame) -> np.ndarray:
        """Pre-game depth-chart rank (9 = not listed) of each (season, week, team, gsis_id, pos) row,
        from the chart known at 08:00 ET on the team's game day (the rule ``build_context`` uses)."""
        tg = self.team_games[["season", "week", "team", "gameday"]].drop_duplicates(["season", "week", "team"])
        r = rows[["season", "week", "team", "gsis_id", "pos"]].reset_index(drop=True).merge(
            tg, on=["season", "week", "team"], how="left")
        charts = []
        for (s, w, gd), _ in r.dropna(subset=["gameday"]).groupby(["season", "week", "gameday"]):
            dc = self.depth_chart(int(s), int(w), nv.kickoff_utc(gd, "08:00"))
            charts.append(dc[["team", "gsis_id", "pos", "rank"]].assign(season=s, week=w, gameday=gd))
        if not charts:
            return np.full(len(r), 9.0)
        dc = pd.concat(charts, ignore_index=True).drop_duplicates(["season", "week", "gameday", "team", "gsis_id", "pos"])
        r = r.merge(dc, on=["season", "week", "gameday", "team", "gsis_id", "pos"], how="left")
        return r["rank"].astype(float).fillna(9.0).to_numpy()


def _merge_team_stats(tg: pd.DataFrame, ts: pd.DataFrame, ps: pd.DataFrame) -> pd.DataFrame:
    """Team-game stats. ``def_tds`` includes defensive fumble-recovery TDs (nflverse keeps those in
    ``fumble_recovery_tds`` only) and ``blocks`` includes blocked PATs; both reproduce the DK/FD
    DST season averages in the 2026 site files far better than the raw columns."""
    t = ts.copy()
    off_fr = ps[ps["position"].isin(["QB", "RB", "FB", "WR", "TE"])].groupby(
        ["season", "week", "team"], as_index=False)["fumble_recovery_tds"].sum().rename(
        columns={"fumble_recovery_tds": "off_fr_td"})
    t = t.merge(off_fr, on=["season", "week", "team"], how="left")
    t["off_fr_td"] = t["off_fr_td"].fillna(0.0)
    t["def_tds"] = t["def_tds"] + (t["fumble_recovery_tds"] - t["off_fr_td"]).clip(lower=0)
    t["fumbles_lost"] = t["sack_fumbles_lost"] + t["rushing_fumbles_lost"] + t["receiving_fumbles_lost"]
    t["two_pt"] = t["passing_2pt_conversions"] + t["rushing_2pt_conversions"]
    t["off_td"] = t["passing_tds"] + t["rushing_tds"]
    t["blocks"] = t["def_punt_blocks"] + t["def_fg_blocks"] + t["def_pat_blocks"]
    keep = ["season", "week", "team", "attempts", "completions", "sacks_suffered", "carries", "targets",
            "passing_yards", "rushing_yards", "passing_tds", "rushing_tds", "passing_interceptions",
            "fumbles_lost", "two_pt", "off_td", "fg_made", "fg_att", "pat_made", "pat_att", "def_sacks",
            "def_interceptions", "fumble_recovery_opp", "def_tds", "special_teams_tds", "def_safeties", "blocks",
            "def_2pt_made", "fumble_recovery_tds", "off_fr_td", "receptions", "receiving_tds"]
    t = t[keep].rename(columns={"receptions": "team_receptions", "receiving_tds": "team_receiving_tds"})
    out = tg.merge(t, on=["season", "week", "team"], how="left")
    t["fd_pa"] = 6 * (t["rushing_tds"] + t["team_receiving_tds"] + t["off_fr_td"]) + 2 * t["two_pt"] \
        + t["pat_made"] + 3 * t["fg_made"]
    opp = t[["season", "week", "team", "def_tds", "def_safeties", "special_teams_tds", "sacks_suffered",
             "fd_pa"]].rename(columns={"team": "opponent", "def_tds": "opp_def_tds", "def_safeties": "opp_def_safeties",
                                       "special_teams_tds": "opp_st_tds", "sacks_suffered": "opp_sacks_suffered",
                                       "fd_pa": "opp_fd_pa"})
    out = out.merge(opp, on=["season", "week", "opponent"], how="left")
    out["played"] = out["attempts"].notna() & out["points"].notna()
    return out


def _kicking(ts: pd.DataFrame) -> pd.DataFrame:
    cols = ["season", "week", "team", "fg_made", "fg_att", "pat_made", "pat_att", "fg_made_0_19", "fg_made_20_29",
            "fg_made_30_39", "fg_made_40_49", "fg_made_50_59", "fg_made_60_"]
    return ts[[c for c in cols if c in ts.columns]].copy()


def _player_games(ps: pd.DataFrame, snaps: pd.DataFrame, ros: pd.DataFrame, ffo: pd.DataFrame,
                  players: pd.DataFrame) -> pd.DataFrame:
    """Union of offensive stat rows and offensive-snap rows, with team totals and xFP columns."""
    cw = _id_crosswalk(players, ros)
    sn = snaps.merge(cw, left_on="pfr_player_id", right_on="pfr_id", how="left")
    sn = sn[(sn["offense_snaps"] > 0) & sn["gsis_id"].notna()]
    sn = sn.sort_values("offense_snaps", ascending=False).drop_duplicates(["season", "week", "gsis_id"])
    sn = sn[["season", "week", "gsis_id", "team", "game_id", "offense_snaps", "offense_pct", "position"]].rename(
        columns={"position": "snap_pos", "team": "snap_team", "game_id": "snap_game_id"})

    # team totals over all players (any position) so shares are of the true team total
    tot = ps.groupby(["season", "week", "team"], as_index=False)[["targets", "carries", "attempts"]].sum().rename(
        columns={"targets": "team_targets", "carries": "team_carries", "attempts": "team_attempts"})

    stat = ps[["player_id", "player_display_name", "position", "season", "week", "game_id", "team", "opponent_team",
               "season_type", "completions", "attempts", "passing_yards", "passing_tds", "passing_interceptions",
               "sacks_suffered", "carries", "rushing_yards", "rushing_tds", "receptions", "targets", "receiving_yards",
               "receiving_tds", "special_teams_tds", "fumble_recovery_tds", "sack_fumbles_lost", "rushing_fumbles_lost",
               "receiving_fumbles_lost", "passing_2pt_conversions", "rushing_2pt_conversions",
               "receiving_2pt_conversions", "fantasy_points_ppr"]
              + (["fumbles_lost_total"] if "fumbles_lost_total" in ps.columns else [])].rename(
        columns={"player_id": "gsis_id"})
    involved = (stat[["attempts", "carries", "targets"]].fillna(0).sum(axis=1) > 0) | stat["position"].isin(
        ["QB", "RB", "FB", "WR", "TE"])
    stat = stat[involved]
    pg = stat.merge(sn, on=["season", "week", "gsis_id"], how="outer")
    pg["team"] = pg["team"].fillna(pg["snap_team"])
    pg["game_id"] = pg["game_id"].fillna(pg["snap_game_id"])
    num = [c for c in stat.columns if c not in ("gsis_id", "player_display_name", "position", "season", "week",
                                                 "game_id", "team", "opponent_team", "season_type")]
    pg[num] = pg[num].fillna(0.0)
    pg["offense_pct"] = pg["offense_pct"].astype(float)

    # position: weekly roster, then player_stats, then snap counts
    rp = ros[["season", "week", "gsis_id", "position", "full_name"]].drop_duplicates(["season", "week", "gsis_id"])
    pg = pg.merge(rp.rename(columns={"position": "roster_pos"}), on=["season", "week", "gsis_id"], how="left")
    pos = pg["roster_pos"].map(POS_MAP)
    pos = pos.fillna(pg["position"].map(POS_MAP)).fillna(pg["snap_pos"].map(POS_MAP))
    pg["pos"] = pos
    pg["name"] = pg["player_display_name"].fillna(pg["full_name"])
    pg = pg[pg["pos"].isin(OFF_POS)].copy()

    # opponent for snap-only rows from the game id (season_week_AWAY_HOME)
    parts = pg["game_id"].astype(str).str.split("_", expand=True)
    away, home = _canon(parts[2]), _canon(parts[3])
    pg["opponent"] = np.where(pg["team"] == home, away, home)

    pg = pg.merge(tot, on=["season", "week", "team"], how="left")
    f = ffo.rename(columns={"player_id": "gsis_id", "posteam": "team"}).copy()
    f["week"] = f["week"].astype(int)
    f["team"] = _canon(f["team"])
    fcols = {"receptions_exp": "x_rec", "rec_yards_gained_exp": "x_rec_yds", "rush_yards_gained_exp": "x_rush_yds",
             "rec_touchdown_exp": "x_rec_td", "rush_touchdown_exp": "x_rush_td", "pass_yards_gained_exp": "x_pass_yds",
             "pass_touchdown_exp": "x_pass_td", "pass_completions_exp": "x_comp", "total_fantasy_points_exp": "x_fp",
             "rec_attempt": "ffo_targets", "rush_attempt": "ffo_carries"}
    f = f[["season", "week", "gsis_id", "team"] + list(fcols)].rename(columns=fcols)
    f = f.drop_duplicates(["season", "week", "gsis_id"])
    team_x = f.groupby(["season", "week", "team"], as_index=False)[["x_rec_td", "x_rush_td"]].sum().rename(
        columns={"x_rec_td": "team_x_rec_td", "x_rush_td": "team_x_rush_td"})
    pg = pg.merge(f.drop(columns="team"), on=["season", "week", "gsis_id"], how="left")
    pg["has_ffo"] = pg["x_fp"].notna()
    for c in fcols.values():
        pg[c] = pg[c].fillna(0.0)
    pg = pg.merge(team_x, on=["season", "week", "team"], how="left")
    team_td = ps.groupby(["season", "week", "team"], as_index=False)[["receiving_tds", "rushing_tds"]].sum().rename(
        columns={"receiving_tds": "team_rec_td", "rushing_tds": "team_rush_td"})
    pg = pg.merge(team_td, on=["season", "week", "team"], how="left")
    for c in ("team_targets", "team_carries", "team_attempts", "team_x_rec_td", "team_x_rush_td", "team_rec_td",
              "team_rush_td"):
        pg[c] = pg[c].fillna(0.0)
    pg["fumbles_lost"] = pg["sack_fumbles_lost"] + pg["rushing_fumbles_lost"] + pg["receiving_fumbles_lost"]
    pg["two_pt"] = pg["rushing_2pt_conversions"] + pg["receiving_2pt_conversions"]
    pg["wk"] = nv.week_key(pg["season"], pg["week"])
    keep = ["gsis_id", "name", "pos", "season", "week", "wk", "game_id", "team", "opponent", "offense_snaps",
            "offense_pct", "completions", "attempts", "passing_yards", "passing_tds", "passing_interceptions",
            "sacks_suffered", "carries", "rushing_yards", "rushing_tds", "receptions", "targets", "receiving_yards",
            "receiving_tds", "special_teams_tds", "fumble_recovery_tds", "fumbles_lost", "two_pt",
            "passing_2pt_conversions", "sack_fumbles_lost", "rushing_fumbles_lost", "receiving_fumbles_lost",
            "rushing_2pt_conversions", "receiving_2pt_conversions", "fantasy_points_ppr", "team_targets",
            "team_carries", "team_attempts", "team_x_rec_td", "team_x_rush_td", "team_rec_td", "team_rush_td",
            "has_ffo"] + list(fcols.values()) + (["fumbles_lost_total"] if "fumbles_lost_total" in pg.columns else [])
    return pg[keep].sort_values(["season", "week", "team", "gsis_id"]).reset_index(drop=True)


# --------------------------------------------------------------------------- shared math


def age_weeks(season, week, target_season: int, target_week: int, gap: float) -> np.ndarray:
    """Weeks between a past game and the target week, with ``gap`` extra weeks per season boundary."""
    s = np.asarray(season, dtype=float)
    w = np.asarray(week, dtype=float)
    return (target_season - s) * (SEASON_WEEKS + gap) + (target_week - w)


def recency_weights(age: np.ndarray, half_life: float) -> np.ndarray:
    return np.power(0.5, np.asarray(age, dtype=float) / max(half_life, 1e-6))


def shrink(num: np.ndarray | float, den: np.ndarray | float, prior: np.ndarray | float, n0: float):
    """Empirical-Bayes rate: (sum x + n0*prior) / (sum n + n0)."""
    return (np.asarray(num, dtype=float) + n0 * np.asarray(prior, dtype=float)) / (np.asarray(den, dtype=float) + n0)


def allocate_shares(c: np.ndarray, p: np.ndarray, pos: np.ndarray, vacated_by_pos: Mapping[str, float] | None = None,
                    gamma: float = 1.0, cross_affinity: float = 0.5, cap: float = 1.0) -> np.ndarray:
    """Conditional shares that conserve expected team volume: sum(p * s) == 1.

    ``c``: shares if everyone played (conditional on playing), ``p``: P(plays). When the
    expected total ``sum(p*c)`` is short of 1, the gap goes to likely players in proportion to
    ``c**gamma`` times a position affinity with the positions that vacated it (same position 1,
    others ``cross_affinity``). When it exceeds 1 (overlapping histories), shares scale down.
    Shares are capped at ``cap`` with the excess passed to the uncapped players.
    """
    c = np.clip(np.asarray(c, dtype=float), 0.0, None)
    p = np.clip(np.asarray(p, dtype=float), 0.0, 1.0)
    pos = np.asarray(pos)
    total = float((p * c).sum())
    if total <= 0 or (p > 0).sum() == 0:
        return c.copy()
    if total >= 1.0:
        s = c / total
    else:
        gap = 1.0 - total
        vac = dict(vacated_by_pos or {})
        for i in range(len(c)):
            if p[i] < 1.0 and c[i] > 0:
                vac[pos[i]] = vac.get(pos[i], 0.0) + (1.0 - p[i]) * c[i]
        vsum = sum(v for v in vac.values() if v > 0)
        if vsum > 0:
            aff = np.array([sum(v / vsum * (1.0 if q == pos[j] else cross_affinity) for q, v in vac.items() if v > 0)
                            for j in range(len(c))])
        else:
            aff = np.ones(len(c))
        w = np.power(c, gamma) * aff
        denom = float((p * w).sum())
        if denom <= 0:
            w, denom = c.copy(), float((p * c).sum())
        s = c + gap * w / denom
    for _ in range(5):  # cap and pass the excess on
        over = s > cap
        if not over.any():
            break
        excess = float((p[over] * (s[over] - cap)).sum())
        s[over] = cap
        if excess <= 0:
            break
        room = (~over) & (p > 0) & (s > 0) & (s < cap)
        if room.any():
            s[room] += excess * s[room] / float((p[room] * s[room]).sum())
        else:  # nobody else holds a share: spread it evenly over the other likely players
            room = (~over) & (p > 0) & (s < cap)
            if not room.any():
                break
            s[room] += excess / float(p[room].sum())
    return s


# --------------------------------------------------------------------------- team model

TEAM_STATS = ["attempts", "completions", "sacks_suffered", "carries", "targets", "passing_yards", "rushing_yards",
              "passing_tds", "rushing_tds", "passing_interceptions", "fumbles_lost", "two_pt", "off_td", "fg_made",
              "pat_made", "def_tds", "special_teams_tds", "def_safeties", "blocks", "def_2pt_made", "points"]
NONNEG = set(TEAM_STATS)


class TeamModel:
    """Per-stat OLS: y ~ 1 + implied + opp_implied + team-offense tendency + opponent-defense tendency.

    Tendencies are shrunk, recency-weighted means of each team's residuals from the market-only
    fit, computed from strictly earlier games (point-in-time), so fitting and prediction use the
    same information set.
    """

    def __init__(self, hist: pd.DataFrame, target_season: int, target_week: int, params: NFLParams):
        self.p = params
        h = hist[hist["played"] & hist["spread_line"].notna() & hist["total_line"].notna()].copy()
        h = h.sort_values(["season", "week"]).reset_index(drop=True)
        self.hist = h
        X = self._x(h)
        Y = h[TEAM_STATS].astype(float).to_numpy()
        fit_rows = (h["season"] >= params.team_min_season).to_numpy()
        self.base_coef = np.linalg.lstsq(X[fit_rows], Y[fit_rows], rcond=None)[0]
        self.resid = Y - X @ self.base_coef
        self.t = self._time(h["season"], h["week"])
        off_t, def_t = self._tendencies_pit(h)
        coefs = []
        for j in range(len(TEAM_STATS)):
            Xj = np.column_stack([X, off_t[:, j], def_t[:, j]])
            coefs.append(np.linalg.lstsq(Xj[fit_rows], Y[fit_rows, j], rcond=None)[0])
        self.coef = np.array(coefs)  # (n_stats, 5)
        self.league_mean = dict(zip(TEAM_STATS, Y[fit_rows].mean(axis=0)))
        self.league = self._league_rates(h[fit_rows])

    def _time(self, season, week) -> np.ndarray:
        return np.asarray(season, float) * (SEASON_WEEKS + self.p.season_gap) + np.asarray(week, float)

    @staticmethod
    def _x(df: pd.DataFrame) -> np.ndarray:
        return np.column_stack([np.ones(len(df)), df["implied"].astype(float), df["opp_implied"].astype(float)])

    def _weights(self, t_now: np.ndarray, t_past: np.ndarray) -> np.ndarray:
        age = t_now[:, None] - t_past[None, :]
        w = np.where(age > 0, np.power(0.5, np.clip(age, 0, None) / self.p.team_half_life), 0.0)
        return w

    def _tendencies_pit(self, h: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        n, k = len(h), len(TEAM_STATS)
        off_t, def_t = np.zeros((n, k)), np.zeros((n, k))
        for key, out in (("team", off_t), ("opponent", def_t)):
            for _, idx in h.groupby(key).indices.items():
                t = self.t[idx]
                W = self._weights(t, t)
                out[idx] = (W @ self.resid[idx]) / (W.sum(axis=1, keepdims=True) + self.p.team_k0)
        return off_t, def_t

    def tendency_now(self, team: str, side: str, t_now: float) -> np.ndarray:
        idx = np.flatnonzero((self.hist["team" if side == "off" else "opponent"] == team).to_numpy())
        if len(idx) == 0:
            return np.zeros(len(TEAM_STATS))
        w = self._weights(np.array([t_now]), self.t[idx])[0]
        return (w @ self.resid[idx]) / (w.sum() + self.p.team_k0)

    def predict(self, games: pd.DataFrame, target_season: int, target_week: int) -> pd.DataFrame:
        """Expected team stats for target team-games (rows: team, opponent, implied, opp_implied)."""
        t_now = float(self._time(target_season, target_week))
        X = self._x(games)
        rows = []
        for i, (team, opp) in enumerate(zip(games["team"], games["opponent"])):
            to, td = self.tendency_now(team, "off", t_now), self.tendency_now(opp, "def", t_now)
            pred = [float(self.coef[j] @ np.array([*X[i], to[j], td[j]])) for j in range(len(TEAM_STATS))]
            rows.append(pred)
        out = pd.DataFrame(rows, columns=TEAM_STATS, index=games.index)
        floor = {s: 0.0 for s in NONNEG}
        floor.update({"attempts": 12.0, "carries": 10.0, "targets": 11.0, "completions": 7.0})
        for s, f in floor.items():
            out[s] = out[s].clip(lower=f)
        return out

    @staticmethod
    def _league_rates(h: pd.DataFrame) -> dict[str, float]:
        tds = float(h["off_td"].sum())
        return {
            "xp_per_td": float(h["pat_made"].sum()) / max(tds, 1.0),
            "two_pt_per_td": float(h["two_pt"].sum()) / max(tds, 1.0),
            "fg_made": float(h["fg_made"].mean()),
            "def_td_per_takeaway": float(h["def_tds"].sum()) / max(float((h["passing_interceptions"]
                                                                          + h["fumbles_lost"]).sum()), 1.0),
            "st_td": float(h["special_teams_tds"].mean()),
            "safety": float(h["def_safeties"].mean()),
            "blocks": float(h["blocks"].mean()),
            "two_pt_return": float(h["def_2pt_made"].mean()),
            "sack_cv": 0.35,
        }


# --------------------------------------------------------------------------- availability

REPORT = {"Out": "out", "Doubtful": "doubtful", "Questionable": "questionable"}
PRACTICE = {"did not participate in practice": "dnp", "limited participation in practice": "lp",
            "full participation in practice": "fp"}
# weekly-roster statuses that mean "not available this week" (IR/PUP/practice squad/cut/...)
UNAVAILABLE_STATUS = {"RES", "CUT", "DEV", "RET", "EXE", "TRD", "TRC", "PUP", "NWT", "SUS", "RSN", "RSR", "UFA",
                      "RFA", "E01", "E14", "W04", "NON"}


def _report_cat(s) -> str:
    return REPORT.get(s, "none") if isinstance(s, str) else "none"


def _practice_cat(s) -> str:
    return PRACTICE.get(s.strip().lower(), "none") if isinstance(s, str) else "none"


def recent_roles(rows: pd.DataFrame, data: "NFLData") -> pd.Series:
    """Role of each (gsis_id, team, wk) row from games strictly before ``wk``.

    regular / fringe: played the team's previous game with >= / < 30% offensive snaps (QB: 50%);
    returning(_regular): played within the last 6 weeks but not the team's previous game (with
    >= 50% snaps in his last game); none: otherwise.
    """
    pg = data.player_games
    played = pg.loc[pg["offense_pct"].fillna(1.0) > 0, ["gsis_id", "wk", "offense_pct"]].sort_values("wk")
    tg = data.team_games.loc[data.team_games["played"], ["season", "week", "team"]].copy()
    tg["twk"] = nv.week_key(tg["season"], tg["week"])
    tg = tg.sort_values("twk")
    r = rows[["gsis_id", "team", "wk"]].copy()
    r["_qb"] = (rows["pos"] == "QB").to_numpy() if "pos" in rows.columns else False
    r["_i"] = np.arange(len(r))
    r["q"] = r["wk"] - 1
    r = r.sort_values("q")
    r = pd.merge_asof(r, tg[["team", "twk"]].rename(columns={"twk": "prev_wk"}).assign(q=tg["twk"]),
                      on="q", by="team", direction="backward")
    r = pd.merge_asof(r.sort_values("q"), played.rename(columns={"wk": "last_wk"}).assign(q=played["wk"]),
                      on="q", by="gsis_id", direction="backward")
    last_ok = r["last_wk"].notna()
    on_last = last_ok & r["prev_wk"].notna() & (r["last_wk"] >= r["prev_wk"])
    gap = (r["wk"] // 100 - r["last_wk"] // 100) * (SEASON_WEEKS + 8) + (r["wk"] % 100 - r["last_wk"] % 100)
    recent = last_ok & ~on_last & (gap <= 6)
    pct = r["offense_pct"].fillna(1.0)
    regular = pct >= np.where(r["_qb"], 0.5, 0.3)  # a QB is a regular only if he played most snaps
    role = np.select([on_last & regular, on_last, recent & (pct >= 0.5), recent],
                     ["regular", "fringe", "returning_regular", "returning"], "none")
    return pd.Series(role, index=r["_i"].to_numpy()).sort_index().set_axis(rows.index)


STARTER_SLOTS = {"QB": 1, "RB": 1, "WR": 3, "TE": 1}
BACKUP_SLOTS = {"QB": 2, "RB": 3, "WR": 5, "TE": 2}


def depth_bucket(pos, rank) -> np.ndarray:
    """'starter' / 'backup' / 'other' from a pre-game depth-chart rank (9 = not listed)."""
    pos = np.asarray(pos, dtype=object)
    r = np.asarray(rank, dtype=float)
    s = np.array([STARTER_SLOTS.get(p, 1) for p in pos], dtype=float)
    b = np.array([BACKUP_SLOTS.get(p, 2) for p in pos], dtype=float)
    return np.where(r <= s, "starter", np.where(r <= b, "backup", "other"))


class AvailabilityModel:
    """P(logs an offensive snap | on the roster) by (position, report, practice, role, depth), estimated
    from history and smoothed toward (position, report, role, depth), (position, report, role),
    (report, role) and (report,).

    ``depth`` is the pre-game depth-chart bucket: recent snaps alone put a healthy starter who
    missed the end of last season, or one back from IR, in the deep-backup cell (role 'none').
    ``target='active'`` estimates P(dressed: game-day status ACT) instead, the availability a QB
    needs to start; a backup QB is almost always active but rarely logs a snap.
    """

    def __init__(self, table: dict[tuple, float], counts: dict[tuple, int]):
        self.table, self.counts = table, counts

    @classmethod
    def fit(cls, data: "NFLData", season: int, week: int, n_seasons: int = 4, m: float = 25.0,
            target: str = "played") -> "AvailabilityModel":
        return cls.fit_many(data, season, week, (target,), n_seasons, m)[target]

    @classmethod
    def fit_many(cls, data: "NFLData", season: int, week: int, targets: Iterable[str] = ("played", "active"),
                 n_seasons: int = 4, m: float = 25.0) -> dict[str, "AvailabilityModel"]:
        """One model per target from the same history rows (roles and depth ranks are computed once)."""
        targets = list(targets)
        unknown = set(targets) - {"played", "active"}
        if unknown:
            raise ValueError(f"unknown availability target(s) {sorted(unknown)}")
        ros = data.rosters
        k = nv.week_key(ros["season"], ros["week"])
        mask = (k < nv.week_key(season, week)) & (ros["season"] >= season - n_seasons) & ros["pos"].isin(OFF_POS)
        mask &= ros["status"].isin(["ACT", "INA"]) & (ros["game_type"] == "REG")
        r = ros.loc[mask, ["season", "week", "gsis_id", "team", "pos", "status"]].drop_duplicates(
            ["season", "week", "gsis_id"])
        r["wk"] = nv.week_key(r["season"], r["week"])
        pg = data.player_games
        pl = pg.loc[pg["offense_pct"].fillna(1.0) > 0, ["gsis_id", "wk"]].drop_duplicates().assign(played=1.0)
        r = r.merge(pl, on=["gsis_id", "wk"], how="left")
        r["played"] = r["played"].fillna(0.0)
        r["active"] = (r["status"] == "ACT").astype(float)
        r["role"] = recent_roles(r, data)
        r["dc"] = depth_bucket(r["pos"], data.depth_ranks(r))
        inj = data.injuries[["season", "week", "gsis_id", "report_status", "practice_status"]].drop_duplicates(
            ["season", "week", "gsis_id"])
        r = r.merge(inj, on=["season", "week", "gsis_id"], how="left")
        r["rep"] = r["report_status"].map(_report_cat)
        r["prac"] = r["practice_status"].map(_practice_cat)
        return {t: cls._from_rows(r, t, m) for t in targets}

    @classmethod
    def _from_rows(cls, r: pd.DataFrame, y: str, m: float) -> "AvailabilityModel":
        table: dict[tuple, float] = {}
        counts: dict[tuple, int] = {}
        base = (r[y].sum() + 1) / (len(r) + 2)
        for rep, g in r.groupby("rep"):
            table[(rep,)] = (g[y].sum() + m * base) / (len(g) + m)
            counts[(rep,)] = len(g)
        for (rep, role), g in r.groupby(["rep", "role"]):
            table[(rep, role)] = (g[y].sum() + m * table[(rep,)]) / (len(g) + m)
            counts[(rep, role)] = len(g)
        for (pos, rep, role), g in r.groupby(["pos", "rep", "role"]):
            table[(pos, rep, role)] = (g[y].sum() + m * table[(rep, role)]) / (len(g) + m)
            counts[(pos, rep, role)] = len(g)
        for (pos, rep, prac, role), g in r.groupby(["pos", "rep", "prac", "role"]):
            table[(pos, rep, prac, role)] = (g[y].sum() + m * table[(pos, rep, role)]) / (len(g) + m)
            counts[(pos, rep, prac, role)] = len(g)
        for (pos, rep, role, dc), g in r.groupby(["pos", "rep", "role", "dc"]):
            table[("dc", pos, rep, role, dc)] = (g[y].sum() + m * table[(pos, rep, role)]) / (len(g) + m)
            counts[("dc", pos, rep, role, dc)] = len(g)
        for (pos, rep, prac, role, dc), g in r.groupby(["pos", "rep", "prac", "role", "dc"]):
            key = ("dc", pos, rep, prac, role, dc)
            table[key] = (g[y].sum() + m * table[("dc", pos, rep, role, dc)]) / (len(g) + m)
            counts[key] = len(g)
        return cls(table, counts)

    def prob(self, pos: str, rep: str, prac: str, role: str, dc: str | None = None) -> float:
        keys = [("dc", pos, rep, prac, role, dc), ("dc", pos, rep, role, dc)] if dc is not None else []
        for key in (*keys, (pos, rep, prac, role), (pos, rep, role), (rep, role), (rep,)):
            if key in self.table:
                return float(self.table[key])
        return 0.9


# --------------------------------------------------------------------------- usage and efficiency

# Conditional (when playing) shares for players with little history, by position and depth rank
# (rank 9 = not on the depth chart): (share of team targets, share of team carries).
ROLE_PRIOR: dict[str, dict[int, tuple[float, float]]] = {
    "WR": {1: (0.20, 0.01), 2: (0.15, 0.01), 3: (0.11, 0.005), 4: (0.06, 0.0), 9: (0.04, 0.0)},
    "TE": {1: (0.15, 0.0), 2: (0.07, 0.0), 3: (0.04, 0.0), 9: (0.03, 0.0)},
    "RB": {1: (0.10, 0.48), 2: (0.06, 0.24), 3: (0.04, 0.10), 9: (0.03, 0.06)},
    "QB": {1: (0.0, 0.12), 2: (0.0, 0.08), 9: (0.0, 0.06)},
}


def role_prior(pos: str, rank: float) -> tuple[float, float]:
    table = ROLE_PRIOR[pos]
    r = int(rank) if np.isfinite(rank) else 9
    return table.get(r, table[9])


def league_efficiency(h: pd.DataFrame) -> dict[str, dict[str, float]]:
    """Per-position league rates from history rows (used as priors)."""
    out: dict[str, dict[str, float]] = {}
    for pos, g in h.groupby("pos"):
        tg, car = g["targets"].sum(), g["carries"].sum()
        touches = g["carries"].sum() + g["receptions"].sum() + g["sacks_suffered"].sum()
        tds = g["receiving_tds"].sum() + g["rushing_tds"].sum()
        out[pos] = {
            "catch": g["receptions"].sum() / max(tg, 1), "ypt": g["receiving_yards"].sum() / max(tg, 1),
            "ypc": g["rushing_yards"].sum() / max(car, 1), "fumble": g["fumbles_lost"].sum() / max(touches, 1),
            "two_pt_per_td": g["two_pt"].sum() / max(tds, 1),
            "st_td": g["special_teams_tds"].sum() / max(len(g), 1),
            "fum_rec_td": g["fumble_recovery_tds"].sum() / max(len(g), 1),
        }
    qb = h[h["pos"] == "QB"]
    att = max(qb["attempts"].sum(), 1)
    out.setdefault("QB", {}).update({
        "comp": qb["completions"].sum() / att, "ypa": qb["passing_yards"].sum() / att,
        "pass_2pt_per_td": qb["passing_2pt_conversions"].sum() / max(qb["passing_tds"].sum(), 1),
    })
    return out


def estimate_usage(h: pd.DataFrame, pool: pd.DataFrame, season: int, week: int, params: NFLParams,
                   league: Mapping[str, Mapping[str, float]]) -> pd.DataFrame:
    """Shrunk conditional usage shares and efficiency for every pool player.

    ``h``: player-game rows strictly before the target week; ``pool``: gsis_id, team, pos, depth_rank.
    """
    P = params
    h = h[h["gsis_id"].isin(pool["gsis_id"])].copy()
    touches = h["targets"] + h["carries"] + h["attempts"]
    h = h[(h["offense_pct"].fillna(0) > 0) | (touches > 0)]
    age = age_weeks(h["season"], h["week"], season, week, P.season_gap)
    w = recency_weights(age, P.half_life)
    pct = h["offense_pct"].fillna(h["offense_pct"].median()).to_numpy()
    norm = pd.Series(w * pct).groupby(h["gsis_id"].to_numpy()).transform("sum").to_numpy() / \
        pd.Series(w).groupby(h["gsis_id"].to_numpy()).transform("sum").to_numpy()
    exit_w = np.where(pct < 0.5 * norm, P.exit_downweight, 1.0)
    team_of = pool.set_index("gsis_id")["team"]
    ws = w * exit_w * np.where(h["team"].to_numpy() == h["gsis_id"].map(team_of).to_numpy(), 1.0,
                               P.team_change_weight)
    w = recency_weights(age, P.eff_half_life or P.half_life) * exit_w  # efficiency weights
    wx = w * h["has_ffo"].to_numpy()
    a = P.td_actual_weight
    att_share = np.where(h["team_attempts"] > 0, h["attempts"] / h["team_attempts"].clip(lower=1), 0.0)
    start = (att_share >= 0.5).astype(float)
    cols = {
        "tgt": ws * h["targets"], "tgt_N": ws * h["team_targets"],
        "car": ws * h["carries"], "car_N": ws * h["team_carries"],
        "rtd": ws * ((1 - a) * h["x_rec_td"] + a * h["receiving_tds"]),
        "rtd_N": ws * ((1 - a) * h["team_x_rec_td"] + a * h["team_rec_td"]),
        "utd": ws * ((1 - a) * h["x_rush_td"] + a * h["rushing_tds"]),
        "utd_N": ws * ((1 - a) * h["team_x_rush_td"] + a * h["team_rush_td"]),
        "rec_w": w * h["receptions"], "tgt_w": w * h["targets"], "ry_w": w * h["receiving_yards"],
        "xrec_w": wx * h["x_rec"], "xry_w": wx * h["x_rec_yds"], "tgt_wx": wx * h["targets"],
        "car_w": w * h["carries"], "uy_w": w * h["rushing_yards"], "xuy_w": wx * h["x_rush_yds"],
        "car_wx": wx * h["carries"],
        "fl_w": w * h["fumbles_lost"], "touch_w": w * (h["carries"] + h["receptions"] + h["sacks_suffered"]),
        "st_w": w * h["special_teams_tds"], "frtd_w": w * h["fumble_recovery_tds"], "g_w": w,
        # QB, in his starts
        "s_att": ws * start * h["attempts"], "s_att_N": ws * start * h["team_attempts"],
        "s_car": ws * start * h["carries"], "s_car_N": ws * start * h["team_carries"],
        "s_utd": ws * start * ((1 - a) * h["x_rush_td"] + a * h["rushing_tds"]),
        "s_utd_N": ws * start * ((1 - a) * h["team_x_rush_td"] + a * h["team_rush_td"]),
        "comp_w": w * h["completions"], "att_w": w * h["attempts"], "starts_w": ws * start,
    }
    agg = pd.DataFrame({k: np.asarray(v, dtype=float) for k, v in cols.items()})
    agg["gsis_id"] = h["gsis_id"].to_numpy()
    sums = agg.groupby("gsis_id").sum()
    last = h.sort_values("wk").groupby("gsis_id").agg(last_wk=("wk", "last"), last_team=("team", "last"),
                                                       last_pct=("offense_pct", "last"), n_games=("wk", "size"))
    u = pool.set_index("gsis_id").join(sums).join(last)
    u[sums.columns] = u[sums.columns].fillna(0.0)
    u["n_games"] = u["n_games"].fillna(0).astype(int)

    pri = np.array([role_prior(p, r) for p, r in zip(u["pos"], u["depth_rank"])])
    tgt_prior, car_prior = pri[:, 0], pri[:, 1]
    is_qb = (u["pos"] == "QB").to_numpy()
    u["c_tgt"] = np.where(is_qb, 0.0, shrink(u["tgt"], u["tgt_N"], tgt_prior, P.n0_target))
    u["c_car"] = shrink(u["car"], u["car_N"], car_prior, P.n0_carry)
    u["c_rtd"] = np.where(is_qb, 0.0, shrink(u["rtd"], u["rtd_N"], tgt_prior, P.n0_td))
    u["c_utd"] = shrink(u["utd"], u["utd_N"], car_prior, P.n0_td)
    # QBs: shares conditional on starting
    qb_car_prior = league.get("QB", {}).get("start_car_share", 0.12)
    u["q_att"] = shrink(u["s_att"], u["s_att_N"], 0.97, 60.0)
    u["q_car"] = shrink(u["s_car"], u["s_car_N"], qb_car_prior, P.n0_qb_rush)
    u["q_utd"] = shrink(u["s_utd"], u["s_utd_N"], qb_car_prior, P.n0_td)

    eff = lambda key: np.array([league.get(p, {}).get(key, np.nan) for p in u["pos"]])
    cr0, ypt0, ypc0 = eff("catch"), eff("ypt"), eff("ypc")
    x_cr = shrink(u["xrec_w"], u["tgt_wx"], cr0, 10.0)
    x_ypt = shrink(u["xry_w"], u["tgt_wx"], ypt0, 10.0)
    x_ypc = shrink(u["xuy_w"], u["car_wx"], ypc0, 15.0)
    u["catch"] = np.clip(shrink(u["rec_w"], u["tgt_w"], x_cr, P.n0_catch), 0.2, 0.95)
    u["ypt"] = np.clip(shrink(u["ry_w"], u["tgt_w"], x_ypt, P.n0_ypt), 2.0, 16.0)
    n0_ypc = np.where(is_qb, P.n0_qb_ypc, P.n0_ypc)
    u["ypc"] = np.clip(shrink(u["uy_w"], u["car_w"], x_ypc, n0_ypc), 1.0, 9.0)
    u["fumble_rate"] = shrink(u["fl_w"], u["touch_w"], eff("fumble"), 150.0)
    u["st_td_rate"] = shrink(u["st_w"], u["g_w"], eff("st_td"), 8.0)
    u["fum_rec_td_rate"] = eff("fum_rec_td")
    u["two_pt_per_td"] = eff("two_pt_per_td")
    qb = league.get("QB", {})
    u["comp_pct"] = np.clip(shrink(u["comp_w"], u["att_w"], qb.get("comp", 0.64), 120.0), 0.45, 0.8)
    return u.reset_index()


def qb_start_probs(qbs: pd.DataFrame, depth_first: bool = False) -> np.ndarray:
    """Start probabilities for one team's QBs (rows: p_active, last_start, depth_rank, starts_w).

    Order by (started the team's last game, depth-chart QB1, recency-weighted starts), or with
    depth-chart QB1 first when the chart is a timestamped pre-game snapshot (2025+ files; on
    2025 games that order picked the actual starter 95.4% of the time vs 92.9%). The first
    available QB starts with his availability probability; the next one inherits the remainder.
    """
    if qbs.empty:
        return np.zeros(0)
    w_last, w_depth = (2.0, 3.0) if depth_first else (2.0, 1.5)
    score = w_last * qbs["last_start"].to_numpy() + w_depth * (qbs["depth_rank"].to_numpy() == 1) \
        + np.minimum(qbs["starts_w"].to_numpy(), 3.0) / 3.0
    order = np.argsort(-score, kind="stable")
    probs = np.zeros(len(qbs))
    remaining = 1.0
    for i in order:
        pa = float(qbs["p_active"].to_numpy()[i])
        if pa <= 0.02:
            continue
        probs[i] = remaining * pa
        remaining -= probs[i]
        if remaining < 1e-3:
            break
    return probs


# --------------------------------------------------------------------------- vectorized scoring


def vector_score(stats: Mapping[str, np.ndarray], table_id: str, role: str) -> np.ndarray:
    """Vectorized ``dfs.scoring.score`` for arrays of stat lines (unrounded).

    Linear, thresholds, tiers and multi_cat blocks take same-shaped arrays; a ``buckets`` stat
    takes an array with one extra trailing axis of items (NaN = no item), e.g. FG distances.
    """
    block = load_table(table_id)["roles"][role]
    ref = next(np.asarray(v) for k, v in stats.items() if k not in (block.get("buckets") or {}))
    pts = np.zeros(np.shape(ref), dtype=float)
    for stat, wgt in (block.get("linear") or {}).items():
        if stat in stats:
            pts = pts + float(wgt) * np.asarray(stats[stat], dtype=float)
    for t in block.get("thresholds") or []:
        if t["stat"] in stats:
            pts = pts + float(t["pts"]) * (np.asarray(stats[t["stat"]], dtype=float) >= t["gte"])
    for stat, bands in (block.get("tiers") or {}).items():
        if stat in stats:
            v = np.asarray(stats[stat], dtype=float)
            add = np.full(v.shape, np.nan)
            for lo, hi, p in reversed(bands):  # first matching band wins, as in score()
                m = (v >= lo) if hi is None else ((v >= lo) & (v <= hi))
                add = np.where(m, float(p), add)
            pts = pts + add
    for stat, bands in (block.get("buckets") or {}).items():
        if stat in stats:
            items = np.asarray(stats[stat], dtype=float)
            for lo, hi, p in bands:
                ok = ~np.isnan(items) & (items >= lo)
                if hi is not None:
                    ok &= items <= hi
                pts = pts + float(p) * ok.sum(axis=-1)
    mc = block.get("multi_cat")
    if mc:
        n = sum((np.asarray(stats.get(c, 0.0), dtype=float) >= mc.get("at_least", 10)).astype(int)
                for c in mc["categories"])
        bonus = np.zeros(np.shape(n))
        for k, p in mc["bonuses"]:
            bonus = bonus + p * (n >= k) if mc.get("cumulative") else np.where(n >= k, np.maximum(bonus, p), bonus)
        pts = pts + bonus
    return pts


# --------------------------------------------------------------------------- stat-line simulation

SIM_STATS = ["pass_att", "comp", "pass_yds", "pass_td", "int_thrown", "targets", "rec", "rec_yds", "rec_td",
             "carries", "rush_yds", "rush_td", "fumble_lost", "two_pt", "return_td", "off_fumble_rec_td"]


def _gamma_factor(rng, cv: np.ndarray, n: int) -> np.ndarray:
    cv = np.maximum(np.asarray(cv, dtype=float), 1e-4)
    k = 1.0 / cv ** 2
    return rng.gamma(k[:, None], 1.0 / k[:, None], size=(len(cv), n))


def simulate_offense(m: pd.DataFrame, params: NFLParams, n_sims: int, rng: np.random.Generator) -> dict[str, np.ndarray]:
    """Simulate stat lines conditional on playing. ``m`` holds per-player stat means (SIM_STATS)."""
    P = params
    n = len(m)
    pos = m["pos"].to_numpy()
    cv = np.array([P.usage_cv.get(p, 0.3) for p in pos])
    esd = np.array([P.eff_sd.get(p, 0.15) for p in pos])
    pex = np.array([P.p_exit.get(p, 0.04) for p in pos])
    G = _gamma_factor(rng, cv, n_sims)
    exit_ = rng.random((n, n_sims)) < pex[:, None]
    G = np.where(exit_, G * rng.random((n, n_sims)), G) / (1.0 - pex[:, None] / 2.0)
    E = np.exp(rng.normal(0, 1, (n, n_sims)) * esd[:, None] - esd[:, None] ** 2 / 2)
    col = lambda c: m[c].to_numpy(dtype=float)[:, None]
    out: dict[str, np.ndarray] = {}
    # passing
    att = rng.poisson(col("pass_att") * G)
    comp = rng.binomial(att, np.clip(col("comp") / np.maximum(col("pass_att"), 1e-9), 0, 1))
    ypcmp = col("pass_yds") / np.maximum(col("comp"), 1e-9)
    k = P.completion_shape
    out["pass_att"], out["comp"] = att, comp
    out["pass_yds"] = rng.gamma(k * comp, ypcmp * E / k)
    att_ratio = att / np.maximum(col("pass_att"), 1e-9)
    out["pass_td"] = rng.poisson(col("pass_td") * att_ratio * E)
    out["int_thrown"] = rng.poisson(col("int_thrown") * att_ratio)
    # receiving
    tg = rng.poisson(col("targets") * G)
    rec = rng.binomial(tg, np.clip(col("rec") / np.maximum(col("targets"), 1e-9), 0, 1))
    ypr = col("rec_yds") / np.maximum(col("rec"), 1e-9)
    kc = P.catch_shape
    out["targets"], out["rec"] = tg, rec
    out["rec_yds"] = rng.gamma(kc * rec, ypr * E / kc)
    out["rec_td"] = rng.poisson(col("rec_td") * tg / np.maximum(col("targets"), 1e-9) * E)
    # rushing: per-carry yards are gamma(shape, scale) - shift so losses are possible
    car = rng.poisson(col("carries") * G)
    ypc = col("rush_yds") / np.maximum(col("carries"), 1e-9)
    kr, sh = P.carry_shape, P.carry_shift
    out["carries"] = car
    out["rush_yds"] = rng.gamma(kr * car, np.maximum(ypc + sh, 0.5) * E / kr) - sh * car
    out["rush_td"] = rng.poisson(col("rush_td") * car / np.maximum(col("carries"), 1e-9) * E)
    out["fumble_lost"] = rng.poisson(col("fumble_lost") * G)
    out["two_pt"] = rng.poisson(col("two_pt") * G)
    out["return_td"] = rng.poisson(np.broadcast_to(col("return_td"), (n, n_sims)))
    out["off_fumble_rec_td"] = rng.poisson(np.broadcast_to(col("off_fumble_rec_td"), (n, n_sims)))
    return out


def scoring_stats(sim: Mapping[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Simulated stats -> dfs.scoring offense keys."""
    keys = ["pass_yds", "pass_td", "int_thrown", "rush_yds", "rush_td", "rec", "rec_yds", "rec_td", "return_td",
            "fumble_lost", "two_pt", "off_fumble_rec_td"]
    return {k: sim[k] for k in keys}


def summarize(points: np.ndarray) -> dict[str, np.ndarray]:
    q = np.quantile(points, [0.1, 0.5, 0.9], axis=1)
    return {"played_mean": points.mean(axis=1), "p10": q[0], "p50": q[1], "p90": q[2],
            "sd": points.std(axis=1)}


def simulate_dst(t: pd.DataFrame, league: Mapping[str, float], table_id: str, n_sims: int,
                 rng: np.random.Generator) -> pd.DataFrame:
    """DST fantasy points per team. ``t`` rows: team with opponent's predicted offense
    (opp_sacks, opp_int, opp_fumbles, opp_off_td, opp_fg, opp_two_pt) and own st/def rates."""
    n = len(t)
    col = lambda c: t[c].to_numpy(dtype=float)[:, None]
    cv = league.get("sack_cv", 0.35)
    k = 1 / cv ** 2
    sacks = rng.poisson(col("opp_sacks") * rng.gamma(k, 1 / k, (n, n_sims)))
    ints = rng.poisson(np.broadcast_to(col("opp_int"), (n, n_sims)))
    frs = rng.poisson(np.broadcast_to(col("opp_fumbles"), (n, n_sims)))
    q_td = np.clip(league["def_td_per_takeaway"], 0, 0.5)
    def_td = rng.binomial(ints + frs, q_td)
    ret_td = rng.poisson(np.broadcast_to(col("st_td"), (n, n_sims)))
    safety = rng.poisson(np.broadcast_to(col("safety"), (n, n_sims)))
    blocks = rng.poisson(np.broadcast_to(col("blocks"), (n, n_sims)))
    two_ret = rng.poisson(np.broadcast_to(col("two_pt_return"), (n, n_sims)))
    # opponent scoring (TD/FG counts are under-dispersed vs Poisson: binomial with fixed trials)
    otd = rng.binomial(12, np.clip(col("opp_off_td") / 12, 0, 1), (n, n_sims))
    fg = rng.binomial(14, np.clip(col("opp_fg") / 14, 0, 1), (n, n_sims))
    xp = rng.binomial(otd, np.clip(league["xp_per_td"], 0, 1))
    two = rng.binomial(otd - xp, np.clip(league["two_pt_per_td"] / max(1 - league["xp_per_td"], 1e-6), 0, 1))
    pa = 6 * otd + xp + 2 * two + 3 * fg
    if load_table(table_id).get("site") == "draftkings":
        # DK: return TDs allowed count in full; after a defensive TD by the opponent only its XP counts
        pa = pa + 7 * rng.poisson(np.broadcast_to(col("st_td_allowed"), (n, n_sims))) \
            + rng.poisson(np.broadcast_to(col("opp_def_td"), (n, n_sims)))
    stats = {"sack": sacks, "interception": ints, "fumble_recovery": frs, "def_td": def_td, "return_td": ret_td,
             "safety": safety, "blocked_kick": blocks, "two_pt_return": two_ret, "points_allowed": pa}
    pts = vector_score(stats, table_id, "dst")
    s = summarize(pts)
    out = pd.DataFrame({k: v for k, v in s.items()}, index=t.index)
    for name, arr in (("sacks", sacks), ("interceptions", ints), ("fumble_recoveries", frs), ("def_tds", def_td),
                      ("points_allowed", pa)):
        out[name] = arr.mean(axis=1)
    return out


FG_BINS = [("fg_made_0_19", 19.0), ("fg_made_20_29", 25.0), ("fg_made_30_39", 35.0), ("fg_made_40_49", 45.0),
           ("fg_made_50_59", 54.0), ("fg_made_60_", 61.0)]


def simulate_kicker(t: pd.DataFrame, fg_bin_probs: np.ndarray, xp_per_td: float, table_id: str, n_sims: int,
                    rng: np.random.Generator) -> pd.DataFrame:
    """Kicker points from the team's predicted FGs and TDs; FG distances from league bins."""
    n = len(t)
    fg = rng.binomial(14, np.clip(t["fg"].to_numpy(float)[:, None] / 14, 0, 1), (n, n_sims))
    td = rng.binomial(12, np.clip(t["off_td"].to_numpy(float)[:, None] / 12, 0, 1), (n, n_sims))
    xp = rng.binomial(td, np.clip(xp_per_td, 0, 1))
    kmax = 8
    fgc = np.minimum(fg, kmax)
    bins = rng.choice(len(FG_BINS), size=(n, n_sims, kmax), p=fg_bin_probs)
    dist = np.array([d for _, d in FG_BINS])[bins]
    dist = np.where(np.arange(kmax)[None, None, :] < fgc[..., None], dist, np.nan)
    pts = vector_score({"xp_made": xp, "fg_made_distances": dist}, table_id, "kicker")
    s = summarize(pts)
    out = pd.DataFrame(s, index=t.index)
    out["fg_made"], out["xp_made"] = fg.mean(axis=1), xp.mean(axis=1)
    return out


# --------------------------------------------------------------------------- week context


PREGAME_COLS = ["game_id", "season", "week", "game_type", "gameday", "gametime", "spread_line", "total_line", "roof",
                "team", "opponent", "is_home", "implied", "opp_implied", "team_spread"]


@dataclass
class WeekContext:
    season: int
    week: int
    params: NFLParams
    games: pd.DataFrame  # target team-games (team, opponent, implied, opp_implied, ...)
    team_pred: pd.DataFrame  # predicted team stats, indexed by team
    pool: pd.DataFrame  # offensive players with availability and usage features
    vacated: dict[str, dict[str, dict[str, float]]]  # team -> stat -> pos -> departed share
    league: dict
    team_league: dict
    kickers: pd.DataFrame
    fg_bin_probs: np.ndarray
    depth_snapshot: bool = False  # depth chart is a timestamped pre-game snapshot (2025+ format)


def _week_roster(data: NFLData, season: int, week: int, teams: set[str]) -> pd.DataFrame:
    ros = data.rosters
    r = ros[(ros["season"] == season) & (ros["week"] == week)]
    if r.empty:  # a future week without a roster file yet: use the latest one of the season
        prior = ros[(ros["season"] == season) & (ros["week"] < week)]
        if not prior.empty:
            r = prior[prior["week"] == prior["week"].max()]
    r = r[r["team"].isin(teams)].copy()
    r["status_rank"] = (r["status"] != "ACT").astype(int)
    return r.sort_values("status_rank").drop_duplicates("gsis_id", keep="first")


def build_context(data: NFLData, season: int, week: int, params: NFLParams | None = None,
                  teams: Iterable[str] | None = None) -> WeekContext:
    """Everything known before kickoff of (season, week), plus history strictly before it."""
    P = params or NFLParams()
    tg = data.team_games
    games = tg.loc[(tg["season"] == season) & (tg["week"] == week), [c for c in PREGAME_COLS if c in tg.columns]].copy()
    if teams is not None:
        keep = {canonicalize(t) for t in teams}
        games = games[games["team"].isin(keep) | games["opponent"].isin(keep)]
    if games.empty:
        raise ValueError(f"no NFL games found for {season} week {week}")
    if games[["spread_line", "total_line"]].isna().any().any():
        raise ValueError(f"missing betting lines for {season} week {week}: "
                         f"{games.loc[games['spread_line'].isna(), 'game_id'].unique().tolist()}")
    tkey = ("team_model", season, week, P.team_half_life, P.team_k0, P.season_gap, P.team_min_season)
    if tkey not in data.cache:
        data.cache[tkey] = TeamModel(nv.before(tg, season, week), season, week, P)
    tm = data.cache[tkey]
    team_pred = tm.predict(games, season, week)
    team_pred.index = games["team"].to_numpy()

    team_set = set(games["team"])
    wk = int(nv.week_key(season, week))
    hist = nv.before(data.player_games, season, week, min_season=season - P.lookback_seasons)
    league = league_efficiency(nv.before(data.player_games, season, week, min_season=season - 3))
    qbh = hist[(hist["pos"] == "QB") & (hist["attempts"] >= 0.5 * hist["team_attempts"].clip(lower=1))]
    league.setdefault("QB", {})["start_car_share"] = float(qbh["carries"].sum() / max(qbh["team_carries"].sum(), 1))

    # pool: the target week's roster at offensive positions
    r = _week_roster(data, season, week, team_set)
    pool = r[r["pos"].isin(OFF_POS) & r["gsis_id"].notna()][["gsis_id", "team", "pos", "status", "full_name"]].rename(
        columns={"full_name": "name"}).reset_index(drop=True)
    pool["wk"] = wk
    inj = nv.week_injuries(season, week, data.injuries)[["gsis_id", "report_status", "practice_status"]]
    pool = pool.merge(inj.drop_duplicates("gsis_id"), on="gsis_id", how="left")
    pool["rep"] = pool["report_status"].map(_report_cat)
    pool["prac"] = pool["practice_status"].map(_practice_cat)
    pool["role"] = recent_roles(pool, data)

    # depth chart known before each game day
    ranks = []
    for gameday, g in games.groupby("gameday"):
        as_of = nv.kickoff_utc(gameday, "08:00")
        dc = data.depth_chart(season, week, as_of)
        ranks.append(dc[dc["team"].isin(set(g["team"]))])
    dc = pd.concat(ranks, ignore_index=True) if ranks else pd.DataFrame(columns=["team", "gsis_id", "pos", "rank"])
    dc = dc.drop_duplicates(["team", "gsis_id", "pos"])
    pool = pool.merge(dc[["team", "gsis_id", "pos", "rank"]].rename(columns={"rank": "depth_rank"}),
                      on=["team", "gsis_id", "pos"], how="left")
    pool["depth_rank"] = pool["depth_rank"].astype(float).fillna(9.0)
    pool["dc"] = depth_bucket(pool["pos"], pool["depth_rank"])

    akey = ("availability", season, week)
    if akey not in data.cache:
        data.cache[akey] = AvailabilityModel.fit_many(data, season, week, ("played", "active"))
    for col, target in (("p_active", "played"), ("p_dress", "active")):
        av = data.cache[akey][target]
        pool[col] = [av.prob(*k) for k in zip(pool["pos"], pool["rep"], pool["prac"], pool["role"], pool["dc"])]
    out_status = pool["status"].isin(UNAVAILABLE_STATUS) | (P.use_inactives & (pool["status"] == "INA")) \
        | (pool["rep"] == "out")  # the official "Out" designation is final
    pool.loc[out_status, ["p_active", "p_dress"]] = 0.0

    usage = estimate_usage(hist, pool, season, week, P, league)
    pool = usage
    # QB "started the team's last game" flag
    last_team_game = hist.groupby("team")["wk"].max()
    lastg = hist[hist["wk"] == hist["team"].map(last_team_game)]
    starters = lastg[lastg["pos"] == "QB"].sort_values("attempts").drop_duplicates("team", keep="last")
    started = set(zip(starters["gsis_id"], starters["team"]))
    pool["last_start"] = [float((g, t) in started) for g, t in zip(pool["gsis_id"], pool["team"])]

    vacated = _departed_vacated(hist, pool, season, week, P)
    kickers = r[r["position"] == "K"][["gsis_id", "team", "status", "full_name"]].rename(columns={"full_name": "name"})
    kickers = kickers[~kickers["status"].isin(UNAVAILABLE_STATUS)].drop_duplicates("team")
    kh = data.kicking[(nv.week_key(data.kicking["season"], data.kicking["week"]) < wk)
                      & (data.kicking["season"] >= season - 3)]
    bins = np.array([kh[c].sum() for c, _ in FG_BINS], dtype=float) + 1.0
    return WeekContext(season=season, week=week, params=P, games=games.reset_index(drop=True), team_pred=team_pred,
                       pool=pool, vacated=vacated, league=league, team_league=tm.league, kickers=kickers,
                       fg_bin_probs=bins / bins.sum(),
                       depth_snapshot="dt" in data.depth_raw.get(season, pd.DataFrame()).columns)


def _departed_vacated(hist: pd.DataFrame, pool: pd.DataFrame, season: int, week: int,
                      P: NFLParams) -> dict[str, dict[str, dict[str, float]]]:
    """Recent unconditional usage of players who played for a team but are not in its pool now."""
    age = age_weeks(hist["season"], hist["week"], season, week, P.season_gap)
    h = hist[(age <= 6)].copy()
    h["w"] = recency_weights(age[age <= 6], P.half_life)
    in_pool = set(zip(pool["gsis_id"], pool["team"]))
    games = h.drop_duplicates(["team", "wk"])
    den = {t: {"targets": float((g["w"] * g["team_targets"]).sum()), "carries": float((g["w"] * g["team_carries"]).sum())}
           for t, g in games.groupby("team")}
    dep = h[[(g, t) not in in_pool for g, t in zip(h["gsis_id"], h["team"])]]
    out: dict[str, dict[str, dict[str, float]]] = {}
    for (team, pos), g in dep.groupby(["team", "pos"]):
        d = den.get(team, {})
        for stat in ("targets", "carries"):
            if d.get(stat, 0) > 0:
                v = float((g["w"] * g[stat]).sum()) / d[stat]
                if v > 0:
                    out.setdefault(team, {}).setdefault(stat, {})[pos] = v
    return out


# --------------------------------------------------------------------------- player stat means


def player_means(ctx: WeekContext) -> pd.DataFrame:
    """Per-player stat means conditional on playing (QBs: on starting) and P(plays)."""
    P, L = ctx.params, ctx.league
    m = ctx.pool[ctx.pool["team"].isin(ctx.team_pred.index)].reset_index(drop=True).copy()
    T = ctx.team_pred.loc[m["team"]].reset_index(drop=True)
    n = len(m)
    is_qb = (m["pos"] == "QB").to_numpy()
    pa = m["p_active"].to_numpy(float)
    pos = m["pos"].to_numpy()
    s_tgt, s_rtd, s_car, s_utd, pi = (np.zeros(n) for _ in range(5))
    for team, idx in m.groupby("team").indices.items():
        vac = ctx.vacated.get(team, {})
        q = idx[is_qb[idx]]
        o = idx[~is_qb[idx]]
        if len(q):
            qbs = m.iloc[q]
            if "p_dress" in m.columns:  # availability to start = dressed, not "logs a snap"
                qbs = qbs.assign(p_active=qbs["p_dress"])
            pi[q] = qb_start_probs(qbs, ctx.depth_snapshot)
            if pi[q].sum() > 0:  # exactly one QB starts: the chain's leftover ("nobody dressed") is impossible
                pi[q] = pi[q] / pi[q].sum()
        e_qcar = min(float((pi[q] * m["q_car"].to_numpy()[q]).sum()), 0.6)
        e_qutd = min(float((pi[q] * m["q_utd"].to_numpy()[q]).sum()), 0.6)
        if len(o):
            alloc = lambda c, v, aff, cap, o=o: allocate_shares(c, pa[o], pos[o], v, P.gamma, aff, cap)
            s_tgt[o] = alloc(m["c_tgt"].to_numpy()[o], vac.get("targets"), P.cross_affinity_targets, P.max_target_share)
            s_rtd[o] = alloc(m["c_rtd"].to_numpy()[o], vac.get("targets"), P.cross_affinity_targets, 0.7)
            s_car[o] = (1 - e_qcar) * alloc(m["c_car"].to_numpy()[o] / (1 - e_qcar), vac.get("carries"),
                                            P.cross_affinity_carries, P.max_carry_share / (1 - e_qcar))
            s_utd[o] = (1 - e_qutd) * alloc(m["c_utd"].to_numpy()[o] / (1 - e_qutd), vac.get("carries"),
                                            P.cross_affinity_carries, 0.9 / (1 - e_qutd))
        s_car[q] = m["q_car"].to_numpy()[q]
        s_utd[q] = m["q_utd"].to_numpy()[q]
    t = lambda c: T[c].to_numpy(float)
    qa = np.where(is_qb, m["q_att"].to_numpy(float), 0.0)
    out = {"s_tgt": s_tgt, "s_rtd": s_rtd, "s_car": s_car, "s_utd": s_utd,
           "pi_start": np.where(is_qb, pi, np.nan), "p_play": np.where(is_qb, pi, pa)}
    out["targets"] = s_tgt * t("targets")
    out["rec"] = out["targets"] * m["catch"].to_numpy(float)
    out["rec_yds"] = out["targets"] * m["ypt"].to_numpy(float)
    out["rec_td"] = s_rtd * t("passing_tds")
    out["carries"] = s_car * t("carries")
    out["rush_yds"] = out["carries"] * m["ypc"].to_numpy(float)
    out["rush_td"] = s_utd * t("rushing_tds")
    out["pass_att"] = qa * t("attempts")
    out["comp"] = out["pass_att"] * m["comp_pct"].to_numpy(float)
    out["pass_yds"] = qa * t("passing_yards")
    out["pass_td"] = qa * t("passing_tds")
    out["int_thrown"] = qa * t("passing_interceptions")
    # coherence with team totals, in expectation over availability / starts
    w = out["p_play"]
    team = m["team"]
    tsum = lambda v: pd.Series(w * v).groupby(team.to_numpy()).transform("sum").to_numpy()
    ratio = lambda target, tot, lo, hi: np.where(tot > 0, np.clip(target / np.maximum(tot, 1e-9), lo, hi), 1.0)
    out["rec"] = out["rec"] * ratio(t("completions"), tsum(out["rec"]), 0.85, 1.15)
    out["rec_yds"] = out["rec_yds"] * ratio(t("passing_yards"), tsum(out["rec_yds"]), 0.75, 1.33)
    out["rush_yds"] = out["rush_yds"] * ratio(t("rushing_yards"), tsum(out["rush_yds"]), 0.75, 1.33)
    touches = out["carries"] + out["rec"] + qa * t("sacks_suffered")
    out["fumble_lost"] = m["fumble_rate"].to_numpy(float) * touches
    two = m["two_pt_per_td"].fillna(L.get("WR", {}).get("two_pt_per_td", 0.03)).to_numpy(float)
    out["two_pt"] = two * (out["rec_td"] + out["rush_td"]) + np.where(
        is_qb, L.get("QB", {}).get("pass_2pt_per_td", 0.03) * out["pass_td"], 0.0)
    out["return_td"] = m["st_td_rate"].fillna(0.0).to_numpy(float)
    out["off_fumble_rec_td"] = m["fum_rec_td_rate"].fillna(0.0).to_numpy(float)
    opp_of = dict(zip(ctx.games["team"], ctx.games["opponent"]))
    implied = dict(zip(ctx.games["team"], ctx.games["implied"]))
    out["opponent"] = team.map(opp_of).to_numpy()
    out["implied"] = team.map(implied).to_numpy()
    return pd.concat([m.drop(columns=[c for c in out if c in m.columns]), pd.DataFrame(out)], axis=1)


# --------------------------------------------------------------------------- projection


STAT_COMPONENTS = ["pass_att", "comp", "pass_yds", "pass_td", "int_thrown", "targets", "rec", "rec_yds", "rec_td",
                   "carries", "rush_yds", "rush_td", "fumble_lost", "two_pt", "return_td"]


def project_context(ctx: WeekContext, tables: Iterable[str], n_sims: int = 4000, seed: int = 7,
                    include_kickers: bool = False, chunk: int = 128) -> dict[str, pd.DataFrame]:
    """Simulate every pool player, DST (and kicker) once and score under each table.

    Returns {table_id: frame} with one row per entity: key, kind, stat means, p_play, played
    quantiles, played_mean and mean = p_play * played_mean.
    """
    tables = list(tables)
    m = player_means(ctx)
    rng = np.random.default_rng(seed)
    res = {t: [] for t in tables}
    for start in range(0, len(m), chunk):
        part = m.iloc[start:start + chunk]
        sim = scoring_stats(simulate_offense(part, ctx.params, n_sims, rng))
        for t in tables:
            role = role_for(load_table(t), "QB")
            s = summarize(vector_score(sim, t, role))
            res[t].append(pd.DataFrame(s, index=part.index))
    base_cols = ["gsis_id", "name", "team", "opponent", "pos", "status", "rep", "prac", "role", "depth_rank",
                 "p_active", "pi_start", "p_play", "implied", "s_tgt", "s_car", "s_rtd", "s_utd"] + STAT_COMPONENTS
    out: dict[str, pd.DataFrame] = {}
    for t in tables:
        f = pd.concat([m[base_cols], pd.concat(res[t])], axis=1)
        f["key"] = f["gsis_id"]
        f["kind"] = "player"
        f["mean"] = f["p_play"] * f["played_mean"]
        out[t] = f
    # DST
    tp = ctx.team_pred
    TL = ctx.team_league
    g = ctx.games
    dst_in = pd.DataFrame({
        "team": g["team"].to_numpy(), "opponent": g["opponent"].to_numpy(),
        "opp_sacks": tp.loc[g["opponent"], "sacks_suffered"].to_numpy(),
        "opp_int": tp.loc[g["opponent"], "passing_interceptions"].to_numpy(),
        "opp_fumbles": tp.loc[g["opponent"], "fumbles_lost"].to_numpy(),
        "opp_off_td": tp.loc[g["opponent"], "off_td"].to_numpy(),
        "opp_fg": tp.loc[g["opponent"], "fg_made"].to_numpy(),
        "st_td": tp.loc[g["team"], "special_teams_tds"].to_numpy(),
        "safety": tp.loc[g["team"], "def_safeties"].to_numpy(),
        "blocks": tp.loc[g["team"], "blocks"].to_numpy(),
        "two_pt_return": tp.loc[g["team"], "def_2pt_made"].to_numpy(),
        "st_td_allowed": tp.loc[g["opponent"], "special_teams_tds"].to_numpy(),
        "opp_def_td": tp.loc[g["opponent"], "def_tds"].to_numpy(),
        "implied": g["implied"].to_numpy(), "opp_implied": g["opp_implied"].to_numpy(),
    })
    for t in tables:
        d = simulate_dst(dst_in, TL, t, n_sims, rng)
        d = pd.concat([dst_in[["team", "opponent", "implied", "opp_implied"]], d], axis=1)
        d["key"] = "DST:" + d["team"]
        d["gsis_id"] = None
        d["name"] = d["team"] + " DST"
        d["pos"] = "DST"
        d["kind"] = "dst"
        d["p_play"] = 1.0
        d["mean"] = d["played_mean"]
        out[t] = pd.concat([out[t], d], ignore_index=True)
    if include_kickers and len(ctx.kickers):
        k = ctx.kickers.copy()
        k = k[k["team"].isin(tp.index)]
        k["fg"] = tp.loc[k["team"], "fg_made"].to_numpy()
        k["off_td"] = tp.loc[k["team"], "off_td"].to_numpy()
        for t in tables:
            if "kicker" not in load_table(t)["roles"]:
                continue
            kk = simulate_kicker(k.reset_index(drop=True), ctx.fg_bin_probs, TL["xp_per_td"], t, n_sims, rng)
            kk = pd.concat([k.reset_index(drop=True)[["gsis_id", "team", "name"]], kk], axis=1)
            kk["key"], kk["pos"], kk["kind"], kk["p_play"] = kk["gsis_id"], "K", "kicker", 0.98
            kk["mean"] = kk["p_play"] * kk["played_mean"]
            out[t] = pd.concat([out[t], kk], ignore_index=True)
    return out


def to_projections(frame: pd.DataFrame, key_map: Mapping[str, str] | None = None) -> dict[str, Projection]:
    """Stat table rows -> Projection objects keyed by ``key_map[row.key]`` (default: row.key)."""
    out: dict[str, Projection] = {}
    for r in frame.itertuples(index=False):
        key = key_map.get(r.key) if key_map is not None else r.key
        if key is None:
            continue
        comps = {c: float(getattr(r, c)) for c in STAT_COMPONENTS + ["sacks", "interceptions", "fumble_recoveries",
                                                                      "def_tds", "points_allowed", "fg_made", "xp_made"]
                 if hasattr(r, c) and pd.notna(getattr(r, c))}
        comps.update({"p_play": float(r.p_play), "played_mean": float(r.played_mean), "sd_played": float(r.sd)})
        if hasattr(r, "implied") and pd.notna(r.implied):
            comps["team_implied"] = float(r.implied)
        note = None
        if getattr(r, "kind", "player") == "player":
            note = f"{r.pos} {r.team} vs {r.opponent}; status={r.status}; report={r.rep}/{r.prac}; role={r.role}"
        out[key] = Projection(player_key=key, mean=float(r.mean), p10=float(r.p10), p50=float(r.p50),
                              p90=float(r.p90), p_zero=float(np.clip(1.0 - r.p_play, 0.0, 1.0)), source="baseline",
                              components=comps, notes=note)
    return out


def project_week(season: int, week: int, table_id: str = "draftkings.nfl",
                 players: Iterable[Player] | Mapping[str, Player] | None = None, keys: Iterable[str] | None = None,
                 n_sims: int = 4000, seed: int = 7, params: NFLParams | None = None, data: NFLData | None = None,
                 include_kickers: bool = False) -> tuple[dict[str, Projection], pd.DataFrame]:
    """Project an NFL week under ``table_id`` (any DK/FD NFL table) using only pre-week data.

    With ``players`` (contest Player objects) the result is keyed by ``Player.key`` after
    matching to nflverse ids (unmatched players are listed in ``stat_table.attrs['unmatched']``
    and get no projection); otherwise by gsis_id, ``DST:<team>`` for defenses. ``keys`` filters.
    """
    if data is None:
        data = NFLData.load(range(max(2016, season - 7), season + 1))
    plist = list(players.values()) if isinstance(players, Mapping) else (list(players) if players else None)
    teams = None
    if plist:
        teams = {canonicalize(p.team, strict=False) for p in plist}
        teams |= {canonicalize(p.opponent, strict=False) for p in plist if p.opponent}
    ctx = build_context(data, season, week, params, teams=teams)
    frame = project_context(ctx, [table_id], n_sims=n_sims, seed=seed,
                            include_kickers=include_kickers or bool(plist and any("K" in p.positions for p in plist)))[table_id]
    key_map = None
    if plist:
        from dfs.projections.matching import match_players

        report = match_players(plist, candidates_from_stat_table(frame))
        key_map = {}
        for pkey, m in report.matched.items():
            key_map.setdefault(m.data_id, pkey)
        frame["player_key"] = frame["key"].map(key_map)
        frame.attrs["unmatched"] = [dict(u) for u in report.unmatched]
        frame.attrs["match_methods"] = report.by_method()
    if keys is not None:
        wanted = set(keys)
        frame = frame[frame["key"].isin(wanted) | frame.get("player_key", pd.Series(index=frame.index)).isin(wanted)]
    projections = to_projections(frame, key_map)
    return projections, frame


def candidates_from_stat_table(frame: pd.DataFrame) -> pd.DataFrame:
    """Matching candidates (gsis_id, name, team, position) from a projection stat table."""
    c = frame[["key", "name", "team", "pos"]].rename(columns={"key": "data_id", "pos": "position"})
    return c.dropna(subset=["data_id"]).reset_index(drop=True)
