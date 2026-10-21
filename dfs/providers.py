"""Baseline providers: everything a sport needs before the agents run.

A provider turns a parsed contest into a ``SlateContext``: the statistical baseline per
player, market text per game, structured known facts (official injury report, depth chart,
usage) for the research agents, a blinded usage table for the forecaster, and who is out.

- ``GenericProvider`` works for any sport from the CSV alone (site average, shrunk, with
  injury status). It is the fallback and the baseline for sports without a data adapter.
- NFL and CFB providers (``dfs.projections.nfl_provider`` / ``cfb_provider``) add real data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from dfs.distributions import quantiles_from_mean_sd
from dfs.models import Contest, Fact, Projection

OUT_STATUSES = {"O", "OUT", "IR", "SUSP", "NA", "PUP", "D-IR", "INJ"}
P_ZERO_BY_STATUS = {"D": 0.75, "DOUBTFUL": 0.75, "Q": 0.25, "QUESTIONABLE": 0.25, "GTD": 0.3, "P": 0.05}
# Coefficient of variation of single-game fantasy points by sport (research: NFL WR/RB ~0.6, QB ~0.4).
CV = {"nfl": {"QB": 0.45, "RB": 0.65, "WR": 0.7, "TE": 0.8, "DST": 0.9, "D": 0.9, "K": 0.6},
      "cfb": {"QB": 0.45, "RB": 0.7, "WR": 0.75, "TE": 0.85},
      "nba": {"default": 0.3}, "wnba": {"default": 0.35}, "nhl": {"default": 0.8}, "mlb": {"default": 0.9},
      "pga": {"default": 0.35}, "mma": {"default": 0.75}, "nascar": {"default": 0.6}}


@dataclass
class SlateContext:
    baseline: dict[str, Projection]
    market: dict[str, str] = field(default_factory=dict)
    known: dict[str, str] = field(default_factory=dict)
    usage: dict[str, str] = field(default_factory=dict)
    structured_facts: dict[str, list[Fact]] = field(default_factory=dict)
    ruled_out: set[str] = field(default_factory=set)
    notes: list[str] = field(default_factory=list)
    data_source: str = "site CSV"


def status_out(status: str | None) -> bool:
    return (status or "").strip().upper() in OUT_STATUSES


def cv_for(sport: str, position: str) -> float:
    table = CV.get(sport, {})
    return table.get(position, table.get("default", 0.6))


class GenericProvider:
    """Site-average baseline: shrink small samples toward the position median, add spread by sport."""

    name = "generic"

    def __init__(self, shrink_games: float = 3.0):
        self.shrink_games = shrink_games

    def prepare(self, contest: Contest, now: datetime | None = None) -> SlateContext:
        sport = contest.rules.sport
        by_pos: dict[str, list[float]] = {}
        for p in contest.players.values():
            if p.site_avg and p.site_avg > 0:
                by_pos.setdefault(p.positions[0], []).append(p.site_avg)
        med = {pos: sorted(v)[len(v) // 2] for pos, v in by_pos.items()}
        base: dict[str, Projection] = {}
        out: set[str] = set()
        for k, p in contest.players.items():
            if status_out(p.injury_status):
                out.add(k)
                continue
            pos = p.positions[0] if p.positions else "?"
            avg = p.site_avg or 0.0
            # Without a games-played count in DK files, shrink a fixed share toward the median of
            # players with a non-zero average (keeps one-game wonders from topping the board).
            prior = med.get(pos, avg) * 0.6
            mean = (avg * 6 + prior * self.shrink_games) / (6 + self.shrink_games) if avg > 0 else 0.0
            pz = P_ZERO_BY_STATUS.get((p.injury_status or "").upper(), 0.0)
            sd = max(mean * cv_for(sport, pos), 0.5)
            p10, p50, p90 = quantiles_from_mean_sd(mean, sd)
            base[k] = Projection(player_key=k, mean=round(mean * (1 - pz), 3), p10=p10, p50=p50, p90=p90,
                                 p_zero=pz, source="site_avg", components={"site_avg": avg})
        return SlateContext(baseline=base, ruled_out=out, data_source="site CSV averages (no stats adapter)",
                            notes=[f"{sport}: baseline is the site's own average, shrunk; no stats adapter"])
