"""Build historical NBA DK classic slates for the oracle-gap backtest.

One slate per regular-season night with 2+ games. Every row is a player active that night (in the
box score, played or not); injured/inactive players are not in the pool, as on a site with the
injury report known. Columns:

- keys: slate (date), athlete_id, name, team, opp, game, pos (DK eligibility set), salary (DK-like
  model fit on real DK files, scripts/oracle/nba_salary.py), actual (DK points that night);
- engine: the live engine's pre-game projection (dfs.projections.nba_provider: minutes x rate,
  p_zero by last-game status, team-minutes reconciliation), unconditional mean; engine_cond, p10,
  p90, p_zero;
- naive: dk_avg (season-to-date DK points per game, blended with last season while n is small: a
  stand-in for the site's own average);
- features for learned models (all pre-game): minutes, rate, n_cur, n_prev, moved, last_minutes,
  benched, absent_last, home, rest_days, b2b, vacated_min (projected minutes of teammates absent
  tonight), opp_allowed (opponent's DK points allowed per game vs league, season to date).

    python scripts/oracle/nba_slates.py data/cache/oracle/nba_slates.parquet
"""

from __future__ import annotations

import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from dfs.data import nba
from dfs.projections.nba_provider import NBAParams, p_zero_after_playing, player_table, team_last_games

sys.path.insert(0, str(Path(__file__).parent))
from nba_salary import dk_features  # noqa: E402

POS = {"G": {"PG", "SG"}, "PG": {"PG"}, "SG": {"SG"}, "F": {"SF", "PF"}, "SF": {"SF"}, "PF": {"PF"}, "C": {"C"}}
MODEL = json.loads(Path(__file__).with_name("nba_salary_model.json").read_text())
P = NBAParams()
BOX: pd.DataFrame | None = None


def _box() -> pd.DataFrame:
    global BOX
    if BOX is None:
        BOX = nba.load_box([2024, 2025, 2026, 2027])
    return BOX


def salary(f: pd.DataFrame) -> np.ndarray:
    a, b1, b2 = MODEL["coef"]
    s = a + b1 * f["ppg_blend"].fillna(5.0) + b2 * f["l5_blend"].fillna(f["ppg_blend"]).fillna(5.0)
    return np.clip(np.round(s, -2), 3000, 12500).astype(int)


def opp_allowed(box: pd.DataFrame, cutoff, season: int) -> dict[str, float]:
    """Opponent DK points allowed per game relative to the league, season to date (all positions)."""
    h = nba.before(box, cutoff)
    h = h[(h["season"] == season) & ~h["did_not_play"] & (h["season_type"] == 2)].copy()
    if h.empty:
        return {}
    h["dk"] = nba.dk_points(h)
    per = h.groupby(["opponent_team_abbreviation", "game_id"])["dk"].sum().groupby(level=0).mean()
    return (per / per.mean()).to_dict()


def build_night(date: str) -> pd.DataFrame:
    box = _box()
    night = box[(box["game_date"].astype(str) == date) & (box["season_type"] == 2)]
    games = night["game_id"].unique()
    if len(games) < 2:
        return pd.DataFrame()
    cutoff = night["start"].min()
    season = int(night["season"].iloc[0])
    table = player_table(box, cutoff, season, P)
    lastg = team_last_games(box, cutoff, season)
    feats = dk_features(box, cutoff, season)
    allowed = opp_allowed(box, cutoff, season)
    league = table.attrs["league_rate"]
    hist = nba.before(box, cutoff)
    team_dates = hist.groupby("team_abbreviation")["start"].max()
    rows = []
    for gid in games:
        g = night[night["game_id"] == gid]
        teams = sorted(g["team_abbreviation"].unique())
        home = g[g["home_away"] == "home"]["team_abbreviation"].iloc[0] if (g["home_away"] == "home").any() else teams[-1]
        away = [t for t in teams if t != home][0] if len(teams) == 2 else teams[0]
        label = f"{away}@{home}"
        for _, r in g.iterrows():
            aid, team = int(r["athlete_id"]), r["team_abbreviation"]
            tg = lastg.get(team)
            last_played = tg is not None and aid in tg[1]
            benched = tg is not None and aid in tg[3]
            absent_last = tg is not None and not last_played and not benched
            if aid in table.index:
                t = table.loc[aid]
                minutes, rate, cond = float(t["minutes"]), float(t["rate"]), float(t["mean"])
                n_cur, n_prev, moved, last_min = float(t["n_cur"]), float(t["n_prev"]), float(t["moved"]), float(t["last_minutes"])
            else:  # no NBA game on record
                grp = "G" if r["athlete_position_abbreviation"] in ("G", "PG", "SG") else "C" if r["athlete_position_abbreviation"] == "C" else "F"
                rate = P.rookie_rate_share * float(league.get(grp, 1.0))
                minutes, cond, n_cur, n_prev, moved, last_min = P.rookie_minutes, P.rookie_minutes * rate, 0.0, 0.0, 0.0, 0.0
            if benched:
                pz = P.p_zero_benched
                if minutes > P.benched_minutes:
                    cond *= P.benched_minutes / minutes
                    minutes = P.benched_minutes
            elif last_played:
                pz = p_zero_after_playing(last_min, P)
            else:
                pz = 0.0  # absent last game but active tonight: research reports him active
            rest = (cutoff - team_dates.get(team, cutoff - pd.Timedelta(days=3))).total_seconds() / 86400 if team in team_dates else 3.0
            f = feats.loc[aid] if aid in feats.index else None
            rows.append({
                "slate": date, "season": season, "athlete_id": aid, "name": r["athlete_display_name"], "team": team,
                "opp": r["opponent_team_abbreviation"], "game": label, "pos": POS.get(r["athlete_position_abbreviation"], {"SF", "PF"}),
                "actual": 0.0 if (r["did_not_play"] or r["minutes"] <= 0) else float(nba.dk_points(pd.DataFrame([r])).iloc[0]),
                "minutes_actual": float(r["minutes"]), "minutes": minutes, "rate": rate, "engine_cond": cond, "p_zero": pz,
                "n_cur": n_cur, "n_prev": n_prev, "moved": moved, "last_minutes": last_min, "benched": float(benched),
                "absent_last": float(absent_last), "home": float(team == home), "rest_days": rest, "b2b": float(rest < 1.5),
                "opp_allowed": allowed.get(r["opponent_team_abbreviation"], 1.0),
                "ppg": f["ppg"] if f is not None else np.nan, "l5": f["l5"] if f is not None else np.nan,
                "ppg_prev": f["ppg_prev"] if f is not None else np.nan,
                "ppg_blend": f["ppg_blend"] if f is not None else np.nan, "l5_blend": f["l5_blend"] if f is not None else np.nan,
            })
    d = pd.DataFrame(rows)
    # team minutes: players returning to the lineup share the 242 with the regulars (engine's reconcile rule)
    out = []
    for team, g in d.groupby("team"):
        exp = g["minutes"] * (1 - g["p_zero"])
        reg = exp[(g["benched"] == 0) & (g["absent_last"] == 0)].sum()
        unc = exp[(g["benched"] == 0) & (g["absent_last"] == 1)].sum()
        f = max(P_FLOOR, min(1.0, (242 - unc) / reg)) if reg > 0 and reg + unc > 242 else 1.0
        g = g.copy()
        regs = (g["benched"] == 0) & (g["absent_last"] == 0)
        g.loc[regs, "minutes"] *= f
        g.loc[regs, "engine_cond"] *= f
        out.append(g)
    d = pd.concat(out, ignore_index=True)
    # vacated minutes: projected minutes of teammates who played the team's last game but are absent tonight
    tonight = set(d["athlete_id"])
    vac = {}
    for team in d["team"].unique():
        tg = lastg.get(team)
        gone = [a for a in (tg[1] if tg else set()) if a not in tonight and a in table.index]
        vac[team] = float(table.loc[gone, "minutes"].sum()) if gone else 0.0
    d["vacated_min"] = d["team"].map(vac).fillna(0.0)
    d["engine"] = d["engine_cond"] * (1 - d["p_zero"])
    sd = P.sd_a + P.sd_b * d["engine_cond"]
    d["p10"] = (d["engine_cond"] - 1.17 * sd).clip(lower=0)
    d["p90"] = d["engine_cond"] + 1.36 * sd
    d["salary"] = salary(d.assign(ppg_blend=d["ppg_blend"].fillna(d["engine_cond"]),
                                  l5_blend=d["l5_blend"].fillna(d["engine_cond"])))
    d["dk_avg"] = d["ppg_blend"].fillna(0.0)
    return d


P_FLOOR = 0.85


def all_nights() -> list[str]:
    box = _box()
    reg = box[(box["season_type"] == 2) & box["season"].isin([2025, 2026, 2027])]
    return sorted(reg["game_date"].astype(str).unique())


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "data/cache/oracle/nba_slates.parquet")
    out.parent.mkdir(parents=True, exist_ok=True)
    nights = all_nights()
    with ProcessPoolExecutor(4) as ex:
        frames = list(ex.map(build_night, nights, chunksize=4))
    d = pd.concat([f for f in frames if len(f)], ignore_index=True)
    d["pos"] = d["pos"].map(lambda s: ",".join(sorted(s)))
    d.to_parquet(out)
    print(f"{d['slate'].nunique()} slates, {len(d)} player rows -> {out}")
