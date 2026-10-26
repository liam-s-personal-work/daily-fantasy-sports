"""Cached sportsdataverse NBA loaders: ESPN player box scores (pre-built release files).

Same source and terms as the college data (``sportsdataverse`` in ``dfs.sources``): the release
parquets at ``github.com/sportsdataverse/sportsdataverse-data/releases``, no scraping. The box
scores are ESPN-derived, used for personal, non-commercial analysis and not redistributed.

Seasons are named by the year they end (2026-27 is ``2027``). Past seasons are cached forever;
the current season is re-downloaded when its file is older than ``max_age_hours`` (default
``DFS_NBA_MAX_AGE_H`` or 6). A failed refresh falls back to the cached copy with a warning in
``LOAD_WARNINGS``.

Point-in-time rule: projection code reads only rows whose game started before the slate
(``before()``), so tonight's results never reach a baseline.
"""

from __future__ import annotations

import os
import time
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

import pandas as pd

from dfs.data import cfb as sdv  # shared release-file downloader and cache folder

DEFAULT_MAX_AGE_H = float(os.environ.get("DFS_NBA_MAX_AGE_H", "6"))
CACHE_DIR = sdv.CACHE_DIR.parent / "sportsdataverse_nba"
DATASETS: dict[str, tuple[str, str]] = {
    "player_box": ("espn_nba_player_boxscores", "player_box_{season}.parquet"),
}
BOX_COLUMNS = ["game_id", "season", "season_type", "game_date", "game_date_time", "athlete_id", "athlete_display_name",
               "team_abbreviation", "opponent_team_abbreviation", "home_away", "minutes", "points",
               "three_point_field_goals_made", "rebounds", "assists", "steals", "blocks", "turnovers", "starter",
               "did_not_play", "reason", "athlete_position_abbreviation"]
LOAD_WARNINGS: list[str] = []

# DK / FD team codes -> ESPN codes in the box scores
TEAM_ALIASES = {"GSW": "GS", "NOP": "NO", "NOR": "NO", "NYK": "NY", "PHO": "PHX", "SAS": "SA", "UTA": "UTAH",
                "WAS": "WSH", "BRK": "BKN", "CHO": "CHA"}


def espn_team(code: str) -> str:
    c = (code or "").strip().upper()
    return TEAM_ALIASES.get(c, c)


def season_for(d: date) -> int:
    """Season named by the year it ends: October 2026 -> 2027 (August on counts as the new season)."""
    return d.year + 1 if d.month >= 8 else d.year


def url_for(name: str, season: int) -> str:
    tag, tmpl = DATASETS[name]
    return f"{sdv.BASE_URL}/{tag}/{tmpl.format(season=season)}"


def cache_path(name: str, season: int) -> Path:
    return CACHE_DIR / f"{name}_{season}.parquet"


def load_season(name: str, season: int, max_age_hours: float | None = None, today: date | None = None) -> pd.DataFrame:
    max_age = DEFAULT_MAX_AGE_H if max_age_hours is None else max_age_hours
    path = cache_path(name, season)
    current = season >= season_for(today or date.today())
    stale = not path.exists() or (current and time.time() - path.stat().st_mtime > max_age * 3600.0)
    if stale:
        try:
            sdv.fetch_release(url_for(name, season), path)
        except sdv.DataUnavailable as e:
            if not path.exists():
                raise
            msg = f"sportsdataverse NBA {name} {season}: refresh failed ({e}); using the cached copy"
            if msg not in LOAD_WARNINGS:
                LOAD_WARNINGS.append(msg)
    import pyarrow.parquet as pq

    have = set(pq.read_schema(path).names)
    return pd.read_parquet(path, columns=[c for c in BOX_COLUMNS if c in have])


def load_box(seasons: Iterable[int], max_age_hours: float | None = None, today: date | None = None) -> pd.DataFrame:
    """Player box rows for ``seasons`` (a missing season file is skipped with a warning)."""
    frames = []
    for s in seasons:
        try:
            frames.append(load_season("player_box", s, max_age_hours, today))
        except sdv.DataUnavailable as e:
            msg = f"sportsdataverse NBA box {s} unavailable: {e}"
            if msg not in LOAD_WARNINGS:
                LOAD_WARNINGS.append(msg)
    frames = [f for f in frames if len(f)]
    if not frames:
        raise sdv.DataUnavailable("no NBA box-score season could be loaded")
    return normalize(pd.concat(frames, ignore_index=True))


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    """Typed columns; ``start`` is the tip-off time in UTC; exhibition teams (All-Star) dropped."""
    df = df.copy()
    df["start"] = pd.to_datetime(df["game_date_time"], utc=True, errors="coerce")
    df["season"] = pd.to_numeric(df["season"], errors="coerce").astype("int64")
    df["season_type"] = pd.to_numeric(df["season_type"], errors="coerce")
    df = df[df["season_type"].isin([2, 3, 5])]  # regular season, playoffs, play-in (no All-Star games)
    for c in ("minutes", "points", "three_point_field_goals_made", "rebounds", "assists", "steals", "blocks", "turnovers"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    df["did_not_play"] = df["did_not_play"].fillna(False).astype(bool)
    df["athlete_id"] = pd.to_numeric(df["athlete_id"], errors="coerce")
    df = df[df["athlete_id"].notna() & df["start"].notna()]  # team-total rows carry no athlete
    df["athlete_id"] = df["athlete_id"].astype("int64")
    return df.reset_index(drop=True)


def before(df: pd.DataFrame, cutoff: datetime) -> pd.DataFrame:
    """Rows of games that tipped off strictly before ``cutoff`` (tz-aware)."""
    return df[df["start"] < pd.Timestamp(cutoff).tz_convert("UTC")]


def dk_points(df: pd.DataFrame) -> pd.Series:
    """DraftKings NBA points per box row (same table as ``rules/scoring/draftkings.nba.yaml``)."""
    pts, reb, ast = df["points"], df["rebounds"], df["assists"]
    stl, blk = df["steals"], df["blocks"]
    n10 = sum((x >= 10).astype(int) for x in (pts, reb, ast, blk, stl))
    return (pts + 0.5 * df["three_point_field_goals_made"] + 1.25 * reb + 1.5 * ast + 2 * stl + 2 * blk
            - 0.5 * df["turnovers"] + 1.5 * (n10 >= 2) + 3.0 * (n10 >= 3))
