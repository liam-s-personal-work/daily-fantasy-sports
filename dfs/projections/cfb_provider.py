"""CFB baseline provider: a parsed DK/FD college-football contest -> ``SlateContext``.

Steps (all point-in-time: nothing from the target week's games is read):

1. Season from the slate's kickoff dates (or ``now``); the slate's games are matched jointly to
   ESPN scheduled games and team ids (``dfs.data.cfb_teams``), which also gives the week.
2. Contest players are matched to ESPN athlete ids on their team: exact name, nickname/formal
   first name, initial + last name, unique last name, then a close fuzzy match. Unmatched players
   are reported and get a site-average fallback projection; nobody is silently dropped.
3. Baseline from ``dfs.projections.cfb`` (season averages adjusted for recent workload and the
   game environment when a line exists). Players with no recorded play in their team's last two
   games are left out of the baseline and listed. Site injury status O/OUT rules a player out;
   Q/D raise p_zero.
4. Text for the agents: market (spread/total/implied points), known (structured facts),
   usage (blinded: volume and shares only, no fantasy points, salaries or lines), and Facts.

Lines: the sportsdataverse betting file only has games already played, so upcoming games have
no line unless one is passed in (``lines={"AWAY@HOME": {"home_spread": -7.5, "total": 55.5}}``).
"""

from __future__ import annotations

import difflib
from collections import defaultdict
from datetime import datetime, timezone
from typing import Mapping

import numpy as np
import pandas as pd

from dfs.data import cfb as cfbdata
from dfs.data import cfb_stats as cs
from dfs.data.cfb_teams import TeamMatchReport, match_slate, split_slate_game
from dfs.distributions import quantiles_from_mean_sd
from dfs.io.contest_file import norm_name
from dfs.models import Contest, Fact, Player, Projection
from dfs.projections import cfb as model
from dfs.providers import P_ZERO_BY_STATUS, SlateContext, cv_for, status_out
from dfs.sources import blind_text, require

SOURCE_URL = "https://github.com/sportsdataverse/sportsdataverse-data/releases"
NICKNAMES = {"cj": "calvin", "dj": "david", "aj": "anthony", "tj": "thomas", "jj": "james", "mike": "michael",
             "matt": "matthew", "chris": "christopher", "josh": "joshua", "nick": "nicholas", "tony": "anthony",
             "zach": "zachary", "ben": "benjamin", "will": "william", "rob": "robert", "jon": "jonathan", "joe": "joseph",
             "tom": "thomas", "dan": "daniel", "sam": "samuel", "andy": "andrew", "drew": "andrew", "nate": "nathan",
             "alex": "alexander", "pat": "patrick", "cam": "cameron", "jake": "jacob", "tim": "timothy", "greg": "gregory",
             "ed": "edward", "kenny": "kenneth", "jimmy": "james", "danny": "daniel", "johnny": "john", "bobby": "robert"}


class CFBProvider:
    name = "cfb"

    def __init__(self, season: int | None = None, week: int | None = None, *, data: model.CFBData | None = None,
                 lines: Mapping[str, Mapping[str, float]] | None = None, team_overrides: Mapping[str, int] | None = None,
                 params: model.CFBParams | None = None, dispersion=None, pzero=None,
                 max_age_hours: float | None = None, log=None):
        self.season, self.week = season, week
        self.data = data
        self.lines = dict(lines or {})
        self.team_overrides = dict(team_overrides or {})
        self.params = params or model.CFBParams()
        self.dispersion = dispersion
        self.pzero = pzero
        self.max_age_hours = max_age_hours
        self.log = log or (lambda *_: None)
        self.team_report: TeamMatchReport | None = None
        self.player_matches: dict[str, dict] = {}
        self.unmatched: list[dict] = []

    # ------------------------------------------------------------------ public

    def prepare(self, contest: Contest, now: datetime | None = None) -> SlateContext:
        if contest.rules.sport != "cfb":
            raise ValueError(f"CFBProvider got a {contest.rules.sport} contest")
        require("sportsdataverse")
        table = contest.rules.scoring
        when = self._slate_time(contest, now)
        season = self.season or cfbdata.current_season(when.date())
        data = self.data or model.CFBData.load([season - 1, season], self.max_age_hours, log=self.log)
        self.data = data
        notes: list[str] = []

        # 1. teams and week
        games = contest.games or sorted({p.game for p in contest.players.values() if p.game})
        rep = match_slate(games, data.teams, data.schedules, season=season, week=self.week,
                          near=pd.Timestamp(when).tz_convert("UTC") if pd.Timestamp(when).tzinfo else pd.Timestamp(when, tz="UTC"),
                          overrides=self.team_overrides)
        self.team_report = rep
        week = self.week
        if week is None:
            wk = data.schedules.set_index("game_id").loc[[g for g in rep.games.values()], "week"] if rep.games else pd.Series(dtype=int)
            if wk.empty:
                raise ValueError(f"could not match any slate game to the {season} schedule:\n{rep.summary()}")
            week = int(wk.mode().iloc[0])
            if wk.nunique() > 1:
                notes.append(f"slate games fall in weeks {sorted(wk.unique().tolist())}; projecting week {week}")
        notes.append(f"season {season} week {week}; {rep.summary().splitlines()[0]}")
        for u in rep.unmatched:
            notes.append(f"TEAM/GAME UNMATCHED: {u}")
        for c, mt in rep.matches.items():
            if mt.team_id is not None and mt.method not in ("abbreviation", "override", "alt_name", "name"):
                notes.append(f"team {c} -> {mt.school} matched by {mt.method} ({mt.score:.2f}); pass team_overrides if wrong")
        for w in cfbdata.LOAD_WARNINGS:
            notes.append(w)

        # optional user-supplied lines for the target week
        if self.lines:
            self._apply_lines(data, rep, season, week, notes)

        # 2. players
        team_ids = {mt.team_id for mt in rep.matches.values() if mt.team_id is not None}
        proj, removed = model.project_week(data, season, week, table, self.params, team_ids=team_ids,
                                           dispersion=self.dispersion, pzero=self.pzero)
        idx = self._indexes(data, season, team_ids)
        base: dict[str, Projection] = {}
        ruled_out: set[str] = set()
        by_ath = proj.set_index(["athlete_id", "team_id"]) if len(proj) else None
        rem = removed.set_index(["athlete_id", "team_id"]) if len(removed) else None
        no_role: dict[str, list[str]] = defaultdict(list)
        matched_rows: dict[str, pd.Series] = {}
        self.player_matches, self.unmatched = {}, []
        for k, p in contest.players.items():
            tid = rep.team_id(p.team)
            if status_out(p.injury_status):
                ruled_out.add(k)
            if tid is None:
                self._unmatched(p, f"team {p.team} not matched", base, contest)
                continue
            aid, how = self._match_player(p, idx.get(tid))
            if aid is None:
                self._unmatched(p, how, base, contest)
                continue
            self.player_matches[k] = {"athlete_id": aid, "team_id": tid, "method": how}
            if k in ruled_out:
                continue
            if by_ath is not None and (aid, tid) in by_ath.index:
                row = by_ath.loc[(aid, tid)]
                row = row.iloc[0] if isinstance(row, pd.DataFrame) else row
                matched_rows[k] = row
                base[k] = self._projection(k, p, row)
            else:
                last = rem.loc[(aid, tid)] if rem is not None and (aid, tid) in rem.index else None
                last = last.iloc[0] if isinstance(last, pd.DataFrame) else last
                why = (f"last recorded play week {int(last['last_week'])}" if last is not None
                       else f"no recorded offensive play in {season}")
                no_role[p.game or "?"].append(f"{p.name} ({'/'.join(p.positions)}): {why}")
        n_noroles = sum(len(v) for v in no_role.values())
        notes.append(f"players: {len(contest.players)} on slate, {len(self.player_matches)} matched to ESPN ids, "
                     f"{len(self.unmatched)} unmatched (site-average fallback), {len(base)} projected, "
                     f"{n_noroles} without a recent role (left out of the baseline), {len(ruled_out)} ruled out by site status")
        for u in self.unmatched:
            notes.append(f"PLAYER UNMATCHED: {u['name']} ({u['team']} {'/'.join(u['positions'])}): {u['reason']}")

        market, known, usage, facts = self._texts(contest, data, rep, season, week, matched_rows, no_role, ruled_out)
        return SlateContext(baseline=base, market=market, known=known, usage=usage, structured_facts=facts,
                            ruled_out=ruled_out, notes=notes,
                            data_source=f"sportsdataverse cfbfastR pbp + ESPN box/rosters/betting, {season} wk {week} (games before it)")

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _slate_time(contest: Contest, now: datetime | None) -> datetime:
        times = [p.start_time for p in contest.players.values() if p.start_time]
        if times:
            t = min(times)
            return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
        return now or datetime.now(timezone.utc)

    def _apply_lines(self, data: model.CFBData, rep: TeamMatchReport, season: int, week: int, notes: list[str]) -> None:
        tg = data.team_games
        for g, ln in self.lines.items():
            gid = rep.games.get(g) if not str(g).isdigit() else int(g)
            if gid is None:
                notes.append(f"line for {g} ignored: game not on the matched slate")
                continue
            hs, tot = float(ln["home_spread"]), float(ln["total"])
            hi, ai = cfbdata.implied_points(hs, tot)
            m = (tg["game_id"] == gid)
            tg.loc[m, "home_spread"], tg.loc[m, "total"], tg.loc[m, "line_source"] = hs, tot, "user"
            tg.loc[m & (tg["is_home"] == 1), "implied"] = float(hi)
            tg.loc[m & (tg["is_home"] == 0), "implied"] = float(ai)
            tg.loc[m & (tg["is_home"] == 1), "opp_implied"] = float(ai)
            tg.loc[m & (tg["is_home"] == 0), "opp_implied"] = float(hi)
        data.cache.clear()

    @staticmethod
    def _indexes(data: model.CFBData, season: int, team_ids: set[int]) -> dict[int, cs.TeamIndex]:
        out: dict[int, cs.TeamIndex] = {}
        r = data.rosters
        if len(r):
            r = r[(r["season"] == season) & r["team_id"].isin(team_ids)]
        L = data.lines[(data.lines["season"] == season) & data.lines["team_id"].isin(team_ids)]
        has_stats = set(zip(L["team_id"], L["athlete_id"]))
        for tid in team_ids:
            ti = cs.TeamIndex()
            rr = r[r["team_id"] == tid] if len(r) else r
            for row in rr.itertuples(index=False):
                nm = next((v for v in (getattr(row, "athlete_display_name", None), getattr(row, "full_name", None))
                           if isinstance(v, str) and v), None)
                if nm:
                    ti.add(row.athlete_id, nm, getattr(row, "jersey", None), getattr(row, "position_abbreviation", None),
                           getattr(row, "first_name", None), getattr(row, "last_name", None))
            for aid, nm in L[L["team_id"] == tid][["athlete_id", "name"]].drop_duplicates("athlete_id").itertuples(index=False):
                if isinstance(nm, str) and int(aid) not in ti.names:
                    ti.add(aid, nm)
            ti.offense = {a for a in ti.offense} | {a for (t, a) in has_stats if t == tid}
            ti._has_stats = {a for (t, a) in has_stats if t == tid}  # type: ignore[attr-defined]
            out[tid] = ti
        return out

    @staticmethod
    def _match_player(p: Player, ti: cs.TeamIndex | None) -> tuple[int | None, str]:
        if ti is None or not ti.names:
            return None, "no roster or stats for the team"
        stats = getattr(ti, "_has_stats", set())
        name = p.name
        key = cs.compact_name(name)
        hits = ti.full.get(key, set())
        if len(hits) == 1:
            return next(iter(hits)), "exact"
        if len(hits) > 1:
            with_stats = hits & stats
            if len(with_stats) == 1:
                return next(iter(with_stats)), "exact (only one with stats)"
            return None, f"ambiguous name: {len(hits)} roster entries"
        parts = cs.name_parts(name)
        if parts:
            first, last, _ = parts
            for alt in {NICKNAMES.get(first), *[k for k, v in NICKNAMES.items() if v == first]} - {None}:
                h = ti.full.get(alt + last, set())
                if len(h) == 1:
                    return next(iter(h)), "nickname"
            il = ti.init_last.get((first[:1], last), set())
            if len(il) == 1:
                return next(iter(il)), "initial+last"
            if len(il) > 1 and len(il & stats) == 1:
                return next(iter(il & stats)), "initial+last (only one with stats)"
            ln = ti.last.get(last, set())
            if len(ln) == 1:
                return next(iter(ln)), "last name"
        pool = {cs.compact_name(n): a for a, n in ti.names.items()}
        close = difflib.get_close_matches(key, list(pool), n=2, cutoff=0.85)
        if close:
            r1 = difflib.SequenceMatcher(None, key, close[0]).ratio()
            r2 = difflib.SequenceMatcher(None, key, close[1]).ratio() if len(close) > 1 else 0.0
            if r1 - r2 >= 0.04:
                return pool[close[0]], f"fuzzy {r1:.2f}"
            return None, f"ambiguous fuzzy match: {close}"
        return None, f"no player on the team matching {name!r}"

    def _unmatched(self, p: Player, reason: str, base: dict, contest: Contest) -> None:
        self.unmatched.append({"player_key": p.key, "name": p.name, "team": p.team, "positions": p.positions,
                               "reason": reason})
        if status_out(p.injury_status) or not p.site_avg or p.site_avg <= 0:
            return
        pos = p.positions[0] if p.positions else "WR"
        pz = P_ZERO_BY_STATUS.get((p.injury_status or "").upper(), 0.0)
        mean = float(p.site_avg) * 0.85  # site averages over-state small samples; mild shrink
        sd = max(mean * cv_for("cfb", pos), 0.5)
        p10, p50, p90 = quantiles_from_mean_sd(mean, sd)
        base[p.key] = Projection(player_key=p.key, mean=round(mean * (1 - pz), 3), p10=p10, p50=p50, p90=p90,
                                 p_zero=pz, source="site_avg", components={"site_avg": float(p.site_avg)},
                                 notes=f"unmatched to stats ({reason}); site-average fallback")

    @staticmethod
    def _projection(key: str, p: Player, row: pd.Series) -> Projection:
        pz = float(row["p_zero"])
        site = P_ZERO_BY_STATUS.get((p.injury_status or "").upper())
        if site is not None:
            pz = max(pz, site)
        mp = float(row["mean_played"])
        comp = {"fpg_season": float(row["fpg_s"]), "games": float(row["n_s"]), "fpg_prev": float(np.nan_to_num(row["fpg_prev"])),
                "games_prev": float(row["n_prev"]), "base": float(row["base"]), "workload": float(row["workload"]),
                "env": float(row["env"]), "season_share": float(row["season_share"]),
                "recent_share": float(row["recent_share_raw"]), "mean_played": mp}
        if pd.notna(row.get("implied")):
            comp["implied"] = float(row["implied"])
        return Projection(player_key=key, mean=round((1 - pz) * mp, 3), p10=round(float(row["p10"]), 3),
                          p50=round(float(row["p50"]), 3), p90=round(float(row["p90"]), 3), p_zero=round(pz, 4),
                          source="baseline", components=comp,
                          notes=f"{row['pos']}; {int(row['n_s'])} g; workload x{row['workload']:.2f}; env x{row['env']:.2f}")

    # ------------------------------------------------------------------ agent texts

    def _texts(self, contest, data, rep, season, week, rows, no_role, ruled_out):
        market, known, usage = {}, {}, {}
        facts: dict[str, list[Fact]] = defaultdict(list)
        tg = data.team_games
        tgw = tg[(tg["season"] == season) & (tg["week"] == week)]
        tt = cfbdata.before(data.team_totals[data.team_totals["season"] == season], season, week)
        tt = tt[tt["usage_ok"]]
        tvol = tt.groupby("team_id")[["team_plays", "team_pass_att", "team_dropbacks", "team_carries"]].mean()
        tgames = tt.groupby("team_id").size()
        L = cfbdata.before(data.lines[data.lines["season"] == season], season, week)
        as_of = str(cfbdata.before(tg[tg["season"] == season], season, week)["start_date"].max())[:10]
        by_game_players: dict[str, list[tuple[str, pd.Series]]] = defaultdict(list)
        for k, row in rows.items():
            by_game_players[contest.players[k].game or "?"].append((k, row))
        for g in contest.games or sorted(by_game_players):
            gid = rep.games.get(g)
            try:
                away, home = split_slate_game(g)
            except ValueError:
                away, home = g, ""
            gm = tgw[tgw["game_id"] == gid] if gid is not None else tgw.iloc[0:0]
            k_lines, u_lines = [], []
            if len(gm):
                h = gm[gm["is_home"] == 1].iloc[0] if (gm["is_home"] == 1).any() else gm.iloc[0]
                a = gm[gm["is_home"] == 0].iloc[0] if (gm["is_home"] == 0).any() else gm.iloc[0]
                kick = str(h.get("start_date", ""))
                k_lines.append(f"{a['team']} at {h['team']}, kickoff {kick} UTC, venue {h.get('venue', '?')}"
                               + (", neutral site" if bool(h.get("neutral_site")) else "")
                               + (", conference game" if bool(h.get("conference_game")) else ""))
                if pd.notna(h.get("total")) and pd.notna(h.get("home_spread")):
                    market[g] = (f"{h['team']} {h['home_spread']:+.1f} (home spread), total {h['total']:.1f}; implied points "
                                 f"{a['team']} {a['implied']:.1f}, {h['team']} {h['implied']:.1f} [{h.get('line_source')}]")
                    facts[g].append(Fact(subject="game", kind="line", text=market[g], source_url=SOURCE_URL, as_of=as_of,
                                         confidence="high"))
                else:
                    market[g] = ("no pre-game line in the sportsdataverse files (they are added after kickoff); "
                                 "game-environment adjustment off for this game")
                for side in (a, h):
                    tid = int(side["team_id"])
                    code = next((c for c, mt in rep.matches.items() if mt.team_id == tid), str(tid))
                    if tid in tvol.index:
                        v = tvol.loc[tid]
                        pr = v["team_dropbacks"] / max(v["team_dropbacks"] + v["team_carries"], 1)
                        txt = (f"{side['team']} ({code}) over {int(tgames.get(tid, 0))} games: {v['team_plays']:.1f} plays per game, "
                               f"{v['team_pass_att']:.1f} pass attempts, {v['team_carries']:.1f} designed runs and scrambles, "
                               f"dropback rate {pr:.0%}")
                        u_lines.append(txt)
                        k_lines.append(txt)
                        facts[g].append(Fact(subject=code, team=code, kind="usage", text=txt, source_url=SOURCE_URL,
                                             as_of=as_of, confidence="high"))
                    pts = tg[(tg["season"] == season) & (tg["team_id"] == tid) & (tg["week"] < week) & tg["points"].notna()]
                    if len(pts):
                        k_lines.append(f"{side['team']} scoring this season: {pts['points'].mean():.1f} points per game "
                                       f"over {len(pts)} games")
            else:
                market[g] = "game not matched to the schedule"
            for k, row in sorted(by_game_players.get(g, []), key=lambda kr: (contest.players[kr[0]].team, -kr[1]["recent_share_raw"])):
                p = contest.players[k]
                txt = self._usage_line(p, row, L)
                if blind_text(txt) is None:  # never leak; drop the offending sentence
                    continue
                u_lines.append(f"- {p.name} ({p.team} {'/'.join(p.positions)}): {txt}")
                k_lines.append(f"- {p.name} ({p.team}): {txt}")
                facts[g].append(Fact(subject=p.name, team=p.team, kind="usage", text=txt, source_url=SOURCE_URL,
                                     as_of=as_of, confidence="high"))
            for line in no_role.get(g, []):
                k_lines.append(f"- no recent role: {line}")
                nm = line.split(" (")[0]
                facts[g].append(Fact(subject=nm, team=None, kind="role",
                                     text=f"{line} (no recorded offensive play in the team's last two games)",
                                     source_url=SOURCE_URL, as_of=as_of, confidence="medium"))
            for u in self.unmatched:
                if contest.players[u["player_key"]].game == g:
                    k_lines.append(f"- not matched to stats: {u['name']} ({u['team']}): {u['reason']}")
            for k in sorted(ruled_out):
                if contest.players[k].game == g:
                    p = contest.players[k]
                    k_lines.append(f"- site injury status {p.injury_status}: {p.name} ({p.team})")
            known[g] = "\n".join(k_lines) if k_lines else "(none)"
            usage[g] = "\n".join(u_lines) if u_lines else "(none)"
        return market, known, usage, dict(facts)

    @staticmethod
    def _usage_line(p: Player, row: pd.Series, L: pd.DataFrame) -> str:
        """Blinded usage summary: volume, shares and production counts (no fantasy points, no lines)."""
        aid, tid = int(row["athlete_id"]), int(row["team_id"])
        d = L[(L["athlete_id"] == aid)]
        n = max(len(d), 1)
        per = lambda c: max(float(d[c].sum()) / n, 0.0)
        share_s, share_r = float(row["season_share"]), float(row["recent_share_raw"])
        pos = str(row["pos"])
        what = {"QB": "team dropbacks", "RB": "team carries plus targets"}.get(pos, "team pass attempts as targets")
        parts = [f"{int(row['n_s'])} games this season", f"share of {what}: last two games {share_r:.0%}, season {share_s:.0%}"]
        if not bool(row.get("appeared_last", True)):
            parts.append("no recorded play in the team's most recent game")
        if pos == "QB":
            parts.append(f"per game {per('pass_att'):.1f} attempts, {per('pass_yds'):.0f} passing yards, "
                         f"{per('pass_td'):.1f} passing TD, {per('int_thrown'):.1f} interceptions, {per('sacks'):.1f} sacks, "
                         f"{per('carries'):.1f} carries for {per('carry_yds'):.0f} yards, {per('rush_td'):.1f} rushing TD")
        else:
            parts.append(f"per game {per('carries'):.1f} carries for {per('carry_yds'):.0f} yards, {per('targets'):.1f} targets, "
                         f"{per('rec'):.1f} catches for {per('rec_yds'):.0f} yards, "
                         f"{per('rush_td') + per('rec_td'):.2f} touchdowns")
        return "; ".join(parts)
