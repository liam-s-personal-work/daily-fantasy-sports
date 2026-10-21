"""Deterministic fantasy scoring from a stat line and a saved scoring table.

Tables live in ``dfs/rules/scoring/<site>.<sport>.yaml``. A table has one block per role
(offense, dst, kicker, hitter, pitcher, skater, goalie, ...) and each block may contain:

    linear:      {stat: points_per_unit}
    thresholds:  [{stat, gte, pts}]               one-time bonus when stat >= gte
    tiers:       {stat: [[min, max|null, pts]]}   exactly one band applies
    buckets:     {list_stat: [[min, max|null, pts]]}  per item in a list (FG distances)
    multi_cat:   {categories: [...], at_least: 10, bonuses: [[k, pts]], cumulative: bool}

Stats a role does not use are ignored. Slot multipliers (CPT/MVP) are applied by callers
to the total, after bonuses, exactly as the sites do.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

import yaml

TABLE_DIR = Path(__file__).parent / "rules" / "scoring"


class ScoringError(ValueError):
    pass


@lru_cache(maxsize=None)
def load_table(table_id: str) -> dict[str, Any]:
    path = TABLE_DIR / f"{table_id}.yaml"
    if not path.exists():
        raise ScoringError(f"no scoring table {table_id!r} (looked for {path})")
    table = yaml.safe_load(path.read_text())
    if table.get("id") != table_id:
        raise ScoringError(f"{path} declares id {table.get('id')!r}")
    return table


def available_tables() -> list[str]:
    return sorted(p.stem for p in TABLE_DIR.glob("*.yaml"))


def role_for(table: Mapping[str, Any], position: str) -> str:
    roles = table.get("role_by_position", {})
    role = roles.get(position.upper(), roles.get("default"))
    if role is None:
        raise ScoringError(f"{table['id']}: no scoring role for position {position!r}")
    return role


def _band(value: float, bands: list[list]) -> float:
    for lo, hi, pts in bands:
        if value >= lo and (hi is None or value <= hi):
            return float(pts)
    raise ScoringError(f"value {value} falls in no band {bands}")


def derive(stats: Mapping[str, Any], table: Mapping[str, Any]) -> dict[str, Any]:
    """Fill derived stats the table's sport needs (all optional, only if inputs present)."""
    s = dict(stats)
    sport = table.get("sport")
    if sport in ("nhl",) and "points" not in s and ("goal" in s or "assist" in s):
        s["points"] = s.get("goal", 0) + s.get("assist", 0)
    if sport == "mlb":
        if "outs" not in s and "ip" in s:
            s["outs"] = innings_to_outs(s["ip"])
        if "single" not in s and "hits" in s:
            s["single"] = s["hits"] - s.get("double", 0) - s.get("triple", 0) - s.get("hr", 0)
        if "quality_start" not in s and "outs" in s and "er" in s and s.get("started", 1):
            s["quality_start"] = int(s["outs"] >= 18 and s["er"] <= 3)
    return s


def innings_to_outs(ip: Any) -> int:
    """Box-score innings ("6.2" = 6 and 2/3) to outs."""
    whole, _, frac = str(ip).partition(".")
    thirds = int(frac or 0)
    if thirds not in (0, 1, 2):
        raise ScoringError(f"bad innings value {ip!r}")
    return int(whole) * 3 + thirds


def score(stats: Mapping[str, Any], table_id: str, role: str | None = None,
          position: str | None = None) -> float:
    """Fantasy points for one stat line at 1x (no slot multiplier). Rounded to 2 decimals."""
    table = load_table(table_id)
    if role is None:
        if position is None:
            raise ScoringError("score() needs a role or a position")
        role = role_for(table, position)
    block = table["roles"].get(role)
    if block is None:
        raise ScoringError(f"{table_id}: unknown role {role!r}")
    s = derive(stats, table)
    pts = 0.0
    for stat, w in (block.get("linear") or {}).items():
        v = s.get(stat)
        if v:
            pts += float(w) * float(v)
    for t in block.get("thresholds") or []:
        if float(s.get(t["stat"], 0) or 0) >= t["gte"]:
            pts += float(t["pts"])
    for stat, bands in (block.get("tiers") or {}).items():
        if stat in s and s[stat] is not None:
            pts += _band(float(s[stat]), bands)
    for stat, bands in (block.get("buckets") or {}).items():
        for item in s.get(stat) or []:
            pts += _band(float(item), bands)
    mc = block.get("multi_cat")
    if mc:
        n = sum(1 for c in mc["categories"] if float(s.get(c, 0) or 0) >= mc.get("at_least", 10))
        earned = [p for k, p in mc["bonuses"] if n >= k]
        if earned:
            pts += sum(earned) if mc.get("cumulative") else max(earned)
    return round(pts + 1e-9, 2)


def describe(table_id: str, roles: list[str] | None = None) -> str:
    """Plain-text rendering of a scoring table for agent prompts."""
    table = load_table(table_id)
    lines = []
    for role, block in table["roles"].items():
        if roles and role not in roles:
            continue
        parts = [f"{k} {v:+g}" for k, v in (block.get("linear") or {}).items()]
        parts += [f"bonus {t['pts']:+g} at {t['stat']} >= {t['gte']}" for t in block.get("thresholds") or []]
        for stat, bands in (block.get("tiers") or {}).items():
            parts.append(f"{stat}: " + ", ".join(
                f"{lo}{'+' if hi is None else '-' + str(hi)} -> {pts:+g}" for lo, hi, pts in bands))
        for stat, bands in (block.get("buckets") or {}).items():
            parts.append(f"each {stat[:-1] if stat.endswith('s') else stat}: " + ", ".join(
                f"{lo}{'+' if hi is None else '-' + str(hi)} -> {pts:+g}" for lo, hi, pts in bands))
        mc = block.get("multi_cat")
        if mc:
            parts.append(f"bonuses when {', '.join(mc['categories'])} reach {mc.get('at_least', 10)}: "
                         + ", ".join(f"{k} categories {p:+g}" for k, p in mc["bonuses"]))
        lines.append(f"{role}: " + "; ".join(parts))
    return "\n".join(lines)
