"""A readable explainer for a run's lineups: one row per pick with the reasoning and the range.

Written next to the upload as ``lineup_explained.csv`` (the upload itself stays exactly what
the site accepts). Everything comes from the run's own files; nothing new is claimed:

- reasoning: the forecaster's cited rationale (football) or the baseline's minutes x rate (NBA),
  the player's role and usage, his latest availability fact from research, and how he fits the
  lineup (stack partners, projected ownership);
- range: the 10th-90th percentile of his projected points (8 in 10 outcomes land inside it), the
  projected mean, and the chance he does not play.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from dfs.sources import host_blocked

HEADER = ["Slot", "Player", "Team", "Game", "Salary", "Projected points", "Range (10th-90th pct)",
          "Chance he sits", "Reasoning"]
_FACT_REF = re.compile(r"\s*\((?:[A-Z&]+@[A-Z&]+\.)?[SF]\d+(?:\s*,\s*(?:[A-Z&]+@[A-Z&]+\.)?[SF]\d+)*\)")
AVAILABILITY = {"out", "inactive", "injured_reserve", "suspended", "doubtful", "questionable", "probable", "active"}


def _clean(text: str) -> str:
    """Drop internal fact ids like "(S10, F6)" so the sentence reads on its own."""
    return re.sub(r"\s+", " ", _FACT_REF.sub("", text or "")).strip()


def _status(name: str, facts: list[dict]) -> str:
    rows = [f for f in facts if (f.get("status") or "").lower() in AVAILABILITY and f.get("subject", "").strip() == name
            and f.get("confidence") != "low" and not host_blocked(f.get("source_url"))]
    if not rows:
        return "No injury designation found in research."
    f = max(rows, key=lambda f: f.get("as_of") or "")
    first = re.split(r"(?<=[.;])\s", _clean(f.get("text", "")), maxsplit=1)[0].rstrip(".;")
    return f"Status: {f['status'].replace('_', ' ')} ({first})."


def _usage(key: str, usage: dict[str, str], game: str) -> str:
    for line in (usage.get(game) or "").splitlines():
        if key in line:
            return line.split(":", 1)[1].strip().rstrip(".") + "."
    return ""


def _role(name: str, facts: list[dict]) -> str:
    """The latest cited role or usage fact about the player (e.g. more minutes with a starter out)."""
    rows = [f for f in facts if f.get("kind") in ("role", "usage", "depth_chart", "matchup")
            and f.get("subject", "").strip() == name and f.get("confidence") != "low" and ".F" in (f.get("id") or "")
            and not host_blocked(f.get("source_url")) and (f.get("status") or "none").lower() not in AVAILABILITY]
    if not rows:  # availability facts are reported by _status
        return ""
    f = max(rows, key=lambda f: f.get("as_of") or "")
    return "Research: " + re.split(r"(?<=[.;])\s", _clean(f.get("text", "")), maxsplit=1)[0].rstrip(".;") + "."


def rows_for(run_dir: str | Path, lineups: list[dict] | None = None) -> list[list[str]]:
    """Rows for the run's lineups, or for ``lineups`` (e.g. the last call's final ones)."""
    run_dir = Path(run_dir)
    rep = json.loads((run_dir / "report.json").read_text())
    proj = json.loads((run_dir / "projections.json").read_text())
    contest = json.loads((run_dir / "contest.json").read_text())
    base = json.loads((run_dir / "baseline.json").read_text())
    dossiers = json.loads((run_dir / "dossiers.json").read_text()) if (run_dir / "dossiers.json").exists() else {}
    forecasts = json.loads((run_dir / "forecasts.json").read_text()) if (run_dir / "forecasts.json").exists() else {}
    players = contest["players"]
    own = {c["player_key"]: c for cands in (rep.get("decision", {}).get("top3_by_slot") or {}).values() for c in cands}
    review = rep.get("decision", {}).get("strategist", {}).get("raw") or {}
    weakest = (review.get("weakest_pick") or {}).get("player_key")
    out = [HEADER]
    for n, lu in enumerate(lineups or rep.get("lineups") or [rep["final"]]):
        if n:
            out.append([""] * len(HEADER))
        keys = [s["player_key"] for s in lu["slots"]]
        for s in lu["slots"]:
            k = s["player_key"]
            p, pr = players[k], proj.get(k, {})
            game = p.get("game") or ""
            parts = []
            f = forecasts.get(k)
            if f and f.get("rationale"):
                parts.append(_clean(f["rationale"].split(" | ")[0]))
            elif pr.get("notes") and pr.get("source") != "baseline":
                parts.append(_clean(pr["notes"].split(" | ")[0]))
            comp = pr.get("components") or {}
            if "minutes" in comp and "rate" in comp:  # NBA baseline: minutes x points per minute
                parts.append(f"Projected {comp['minutes']:.0f} minutes at {comp['rate']:.2f} DK points per minute.")
            use = _usage(k, base.get("usage") or {}, game)
            if use and not f:
                parts.append(f"Usage: {use}")
            facts = (dossiers.get(game) or {}).get("facts") or []
            parts.append(_role(p["name"], facts))
            parts.append(_status(p["name"], facts))
            mates = [players[o]["name"] for o in keys if o != k and players[o].get("game") == game]
            if mates:
                parts.append(f"Stacked with {', '.join(mates)} (same game, so their scores rise and fall together).")
            if k in own:
                parts.append(f"Projected ownership about {own[k]['ownership']:.0%}.")
            if k == weakest:
                parts.append(f"Weakest pick per the review: {_clean(review['weakest_pick'].get('why', ''))}")
            mean, lo, hi = pr.get("mean", 0.0), pr.get("p10", 0.0), pr.get("p90", 0.0)
            out.append([s["slot"], f"{p['name']} ({s['site_id']})", p["team"], game, f"${s['salary']:,}",
                        f"{mean:.1f}", f"{lo:.0f}-{hi:.0f}", f"{pr.get('p_zero', 0.0):.0%}", " ".join(x for x in parts if x)])
    return out


def write_explained(run_dir: str | Path, name: str = "lineup_explained.csv", lineups: list[dict] | None = None) -> Path:
    path = Path(run_dir) / name
    with open(path, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows_for(run_dir, lineups))
    return path
