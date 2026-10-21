"""Stages 7-11: simulate, three independent drafts, strategist/backup, solve, checker loop, user rules.

All lineup building is the solver's; agents can only remove players with cited facts
(strategist), adjust numbers with cited facts within bounds (backup), or fail picks with
cited blockers (checker). The objective follows the contest type: cash lineups maximise the
chance of beating the cash line, tournaments maximise the chance of a top-x% finish
against a simulated field.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from dfs.models import Contest, ContestKind, GameDossier, Lineup, Projection
from dfs.optimize.ilp import Infeasible, SolveOptions, check_legal, lineup_points, solve
from dfs.sim import simulate
from dfs.stages import agents
from dfs import strategy as sel


@dataclass
class Decision:
    final: Lineup
    lineups: list[Lineup]
    rules_lineup: Lineup | None
    drafts: list[Lineup]
    report: dict[str, Any]
    stage_lineups: dict[str, Lineup] = field(default_factory=dict)


class Decider:
    def __init__(self, contest: Contest, projections: dict[str, Projection], cfg, runner,
                 dossiers: dict[str, GameDossier], log: Callable[[str], None] = print,
                 n_lineups: int = 1, now=None):
        self.c = contest
        self.proj = projections
        self.cfg = cfg
        self.runner = runner
        self.dossiers = dossiers
        self.log = log
        self.n_lineups = n_lineups
        self.idx = agents.fact_index(dossiers)
        self.sport = contest.rules.sport
        self.kind = contest.kind
        self.report: dict[str, Any] = {}
        # Players whose games have started can no longer be added (late swap); locked cells stay.
        self.started = {k for k, p in contest.players.items() if now and p.start_time and p.start_time <= now}

    # ------------------------------------------------------------------ helpers
    def _means(self, exclude: set[str] = frozenset()) -> dict[str, float]:
        return {k: p.mean for k, p in self.proj.items() if k not in exclude}

    def _objective_name(self) -> str:
        if self.kind == ContestKind.CASH:
            return "maximise P(beating the cash line ~ field median)"
        return f"maximise P(finishing in the top {self.cfg.target_top:.1%}) against a simulated field"

    def _choose(self, points: dict[str, float], exclude: set[str], label: str,
                user_rules=None, n: int | None = None, slot_locks: dict[int, str] | None = None) -> tuple[Lineup, dict]:
        """Contest-objective lineup(s); adjusted points rescale that player's simulated outcomes."""
        scale = {k: points[k] / self.proj[k].mean for k in points
                 if k in self.proj and self.proj[k].mean > 0 and abs(points[k] - self.proj[k].mean) > 1e-9}
        proj = {k: (self.proj[k].model_copy(update={"mean": points[k]}) if k in scale else self.proj[k])
                for k in points if k in self.proj}
        res = sel.best_lineups(self.c, proj, self.keys, self.sims, self.field, kind=self.kind,
                               target_top=self.cfg.target_top, n=n or 1, exclude=set(exclude) | self.started,
                               user_rules=user_rules, seed=self.cfg.seed, scale=scale, slot_locks=slot_locks)
        for lu in res["lineups"]:
            lu.label = label
        return res["lineups"][0], res

    # ------------------------------------------------------------------ main
    async def decide(self) -> Decision:
        c, cfg = self.c, self.cfg
        self.keys, self.sims = simulate(self.proj, c.players, self.sport, n_sims=cfg.n_sims, seed=cfg.seed)
        self.field = sel.field_for(c, self.proj, kind=self.kind, seed=cfg.seed)
        means = self._means()

        # Three drafts, each built alone from the blended numbers under a different lens.
        drafts = sel.three_drafts(c, self.proj, self.keys, self.sims, self.field, target_top=cfg.target_top,
                                  seed=cfg.seed)
        top3 = sel.top_candidates_by_slot(c, self.proj, self.keys, self.sims, n_draws=min(300, cfg.n_sims), k=3)
        self.report["drafts"] = [d.model_dump() for d in drafts]
        self.report["top3_by_slot"] = top3
        top3_text = sel.format_top3(c, top3)

        stage: dict[str, Lineup] = {"blend": drafts[0]}
        main_ok = True
        excluded: set[str] = set()

        # Strategist and backup run side by side.
        strat_t = agents.strategist_task(c, drafts, top3_text, self.dossiers, self.proj, self._objective_name(),
                                         model=cfg.review_model)
        backup_t = agents.strategist_task(c, drafts, top3_text, self.dossiers, self.proj, self._objective_name(),
                                          model=cfg.backup_model, backup=True)
        strat, backup = await asyncio.gather(self.runner.run(strat_t), self.runner.run(backup_t))
        if strat.ok:
            excluded, srep = agents.apply_strategist(strat.output, self.idx, c, self.dossiers)
            self.report["strategist"] = {"raw": strat.output, "applied": srep}
        else:
            main_ok = False
            self.report["strategist"] = {"error": strat.error}

        final, res = self._choose(means, excluded, "final")
        stage["after_strategist"] = final

        # Checker: code decides, up to 2 revisions.
        checks = []
        for attempt in range(3):
            if not main_ok:
                break
            chk = await self.runner.run(agents.checker_task(c, final, self.dossiers, self.proj,
                                                            model=cfg.review_model, attempt=attempt))
            if not chk.ok:
                main_ok = False
                checks.append({"attempt": attempt, "error": chk.error})
                break
            passed, fails, notes = agents.checker_verdict(chk.output, self.idx, c, final)
            checks.append({"attempt": attempt, "passed": passed, "failed_picks": sorted(fails), **notes})
            if passed:
                break
            if attempt == 2:
                main_ok = False  # still failing after 2 revisions -> backup path
                break
            excluded |= fails
            final, res = self._choose(means, excluded, "final")
        self.report["checker"] = checks
        stage["after_checker"] = final

        # Backup path if the main review failed.
        if backup.ok:
            bpoints, bremoved, brep = agents.apply_backup(backup.output, self.idx, c, means)
            self.report["backup"] = {"raw": backup.output, "applied": brep}
        else:
            bpoints, bremoved = means, set()
            self.report["backup"] = {"error": backup.error}
        if not main_ok:
            final, res = self._choose(bpoints, excluded | bremoved, "final (backup review)")
            self.report["used_backup"] = True
        stage["final"] = final
        self.report["selection"] = {k: v for k, v in res.items() if k != "lineups"}

        # User rules -> second lineup.
        rules_lu = None
        if cfg.user_rules is not None:
            try:
                rules_lu, _ = self._choose(means, excluded, "your rules", user_rules=cfg.user_rules)
            except Infeasible as e:
                self.report["user_rules_error"] = str(e)
        legal = check_legal(c, final)
        if legal:
            raise Infeasible(f"final lineup failed the independent legality check: {legal}")
        lineups = self._entry_lineups(final, bpoints if self.report.get("used_backup") else means,
                                      excluded | (bremoved if self.report.get("used_backup") else set()))
        return Decision(final=final, lineups=lineups, rules_lineup=rules_lu, drafts=drafts, report=self.report,
                        stage_lineups=stage)

    def _entry_lineups(self, final: Lineup, points: dict[str, float], excluded: set[str]) -> list[Lineup]:
        """One lineup per entry row (portfolio for multi-entry); entries with locked cells keep them."""
        entries = self.c.entries
        if not entries:
            return [final]
        groups: dict[tuple, list[int]] = {}
        for i, e in enumerate(entries):
            locks = tuple((j, sid) for j, (sid, lk) in enumerate(zip(e.current, e.locked)) if lk and sid)
            groups.setdefault(locks, []).append(i)
        out: list[Lineup | None] = [None] * len(entries)
        for locks, idx in groups.items():
            if not locks and len(idx) == 1:
                out[idx[0]] = final
                continue
            _, res = self._choose(points, excluded, "final", n=len(idx), slot_locks=dict(locks))
            lus = res["lineups"]
            if not locks:
                lus = [final] + [lu for lu in lus if lu.player_keys != final.player_keys]
            for j, i in enumerate(idx):
                out[i] = lus[j % len(lus)]
        self.report["entries"] = {"n": len(entries), "lock_groups": len(groups)}
        return [lu for lu in out if lu is not None]

    # ------------------------------------------------------------------ report
    def markdown(self, d: Decision, timing: dict, llm: dict) -> str:
        c = self.c
        r = c.rules
        lines = [f"# {r.site.value.title()} {r.sport.upper()} {r.game_type} — {c.kind.value}",
                 "", f"Objective: {self._objective_name()}.", ""]
        for w in c.warnings:
            lines.append(f"> WARNING: {w}")
        lines += ["", "## Final lineup", "", "| Slot | Player | Team | Salary | Mean | p10 | p90 |", "|---|---|---|---:|---:|---:|---:|"]
        for s in d.final.slots:
            p = c.players[s.player_key]
            pr = self.proj[s.player_key]
            m = s.points_multiplier
            lines.append(f"| {s.slot} | {p.name} | {p.team} | {s.salary} | {pr.mean * m:.1f} | {pr.p10 * m:.1f} | {pr.p90 * m:.1f} |")
        lines += ["", f"Salary {d.final.salary} / {r.salary_cap}; projected mean {d.final.mean:.1f}"]
        for k, v in d.final.metrics.items():
            lines.append(f"- {k}: {v:.4g}")
        if d.rules_lineup is not None:
            lines += ["", "## Lineup with your rules", ""]
            lines += [f"- {s.slot}: {c.players[s.player_key].name} ({s.salary})" for s in d.rules_lineup.slots]
            lines.append(f"- mean {d.rules_lineup.mean:.1f}; " + ", ".join(f"{k} {v:.4g}" for k, v in d.rules_lineup.metrics.items()))
        lines += ["", "## Top candidates per slot", "", sel.format_top3(c, self.report.get("top3_by_slot", {}))]
        strat = self.report.get("strategist", {})
        if "applied" in strat:
            ap = strat["applied"]
            lines += ["", "## Review", "", f"- strategist accepted: {ap['accepted']}", f"- rejected (no fact): {ap['rejected']}",
                      f"- weakest pick: {ap.get('weakest_pick')}", f"- close calls: {ap.get('close_calls')}"]
        for chk in self.report.get("checker", []):
            lines.append(f"- checker attempt {chk.get('attempt')}: passed={chk.get('passed')} failed={chk.get('failed_picks')}")
        if self.report.get("used_backup"):
            lines.append("- main review failed; the backup review's lineup was used")
        lines += ["", "## Run", "", f"- timing (s): {timing}", f"- model calls: {llm}"]
        return "\n".join(lines) + "\n"
