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
    entry_ids: list[str] | None = None  # entry for each of ``lineups`` (entries files only)


class Decider:
    def __init__(self, contest: Contest, projections: dict[str, Projection], cfg, runner,
                 dossiers: dict[str, GameDossier], log: Callable[[str], None] = print,
                 n_lineups: int = 1, now=None, priced: set[str] = frozenset(), clock=None,
                 forecasted: set[str] | None = None):
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
        self.priced = set(priced)  # fact ids the forecaster already saw
        self.forecasted = forecasted  # players the forecaster forecast (the novelty rule applies only to them)
        # Players whose games have started can no longer be added (late swap); locked cells stay.
        self.now = now
        self.clock = clock  # the run's deadline: review stages get only the time that is left
        self._fallback_locks: dict[int, str] = {}  # when no fresh lineup is possible (late swap), solve around locks
        self.started = {k for k, p in contest.players.items() if now and p.start_time and p.start_time <= now}

    # ------------------------------------------------------------------ helpers
    def _means(self, exclude: set[str] = frozenset()) -> dict[str, float]:
        return {k: p.mean for k, p in self.proj.items() if k not in exclude}

    def _objective_name(self) -> str:
        if self.kind == ContestKind.CASH:
            return "maximise P(beating the cash line ~ field median)"
        return f"maximise P(finishing in the top {self.cfg.target_top:.1%}) against a simulated field"

    def _choose(self, points: dict[str, float], exclude: set[str], label: str,
                user_rules=None, n: int | None = None, slot_locks: dict[int, str] | None = None,
                kind: ContestKind | None = None) -> tuple[Lineup, dict]:
        """Contest-objective lineup(s); adjusted points rescale that player's simulated outcomes."""
        scale = {k: points[k] / self.proj[k].mean for k in points
                 if k in self.proj and self.proj[k].mean > 0 and abs(points[k] - self.proj[k].mean) > 1e-9}
        def scaled(p: Projection, f: float) -> Projection:  # the two-piece mean is linear in the quantiles
            return p.model_copy(update={"mean": p.mean * f, "p10": p.p10 * f, "p50": p.p50 * f, "p90": p.p90 * f})
        proj = {k: (scaled(self.proj[k], scale[k]) if k in scale else self.proj[k])
                for k in points if k in self.proj}
        res = sel.best_lineups(self.c, proj, self.keys, self.sims, self.field, kind=kind or self.kind,
                               target_top=self.cfg.target_top, n=n or 1, exclude=set(exclude) | self.started,
                               user_rules=user_rules, seed=self.cfg.seed, scale=scale,
                               slot_locks=self._fallback_locks if slot_locks is None else slot_locks)
        for lu in res["lineups"]:
            lu.label = label
        return res["lineups"][0], res

    def _timeout(self, share: float) -> float:
        """Seconds a review task may take: its share of the time left, between 45 s and 15 minutes."""
        if self.clock is None:
            return 900.0
        return max(45.0, min(900.0, self.clock.left() * share))

    def _apply(self, fn, *args):
        """Run an apply_* step; malformed agent output counts as that agent failing, never as a crash."""
        try:
            return fn(*args), None
        except (TypeError, ValueError, KeyError, AttributeError) as e:
            return None, f"malformed agent output: {e}"

    # ------------------------------------------------------------------ main
    async def decide(self) -> Decision:
        c, cfg = self.c, self.cfg
        self.keys, self.sims = simulate(self.proj, c.players, self.sport, n_sims=cfg.n_sims, seed=cfg.seed)
        self.field = sel.field_for(c, self.proj, kind=self.kind, seed=cfg.seed)
        means = self._means()

        # Three drafts, each built alone from the blended numbers under a different lens.
        try:
            drafts = sel.three_drafts(c, self.proj, self.keys, self.sims, self.field, target_top=cfg.target_top,
                                      seed=cfg.seed, exclude=self.started)
            top3 = sel.top_candidates_by_slot(c, self.proj, k=3, seed=cfg.seed, fm=self.field, exclude=self.started)
        except Infeasible:  # late swap with too few open games: drafts are built around the locked entries
            from dfs.pipeline import entry_lock_groups
            groups = [(locks, idx) for locks, idx in entry_lock_groups(c, self.now).items() if locks]
            if not groups:
                raise
            self._fallback_locks = dict(max(groups, key=lambda g: len(g[1]))[0])
            self.report["built_around_locks"] = self._fallback_locks
            drafts = sel.three_drafts(c, self.proj, self.keys, self.sims, self.field, target_top=cfg.target_top,
                                      seed=cfg.seed, exclude=self.started, slot_locks=self._fallback_locks)
            top3 = sel.top_candidates_by_slot(c, self.proj, k=3, seed=cfg.seed, fm=self.field, exclude=self.started,
                                              slot_locks=self._fallback_locks)
        self.report["drafts"] = [d.model_dump() for d in drafts]
        self.report["top3_by_slot"] = top3
        top3_text = sel.format_top3(c, top3)

        stage: dict[str, Lineup] = {"blend": drafts[0]}
        main_ok = True
        excluded: set[str] = set()

        # Strategist and backup run side by side.
        strat_t = agents.strategist_task(c, drafts, top3_text, self.dossiers, self.proj, self._objective_name(),
                                         model=cfg.review_model, timeout_s=self._timeout(0.25))
        backup_t = agents.strategist_task(c, drafts, top3_text, self.dossiers, self.proj, self._objective_name(),
                                          model=cfg.backup_model, backup=True, timeout_s=self._timeout(0.25))
        strat, backup = await asyncio.gather(self.runner.run(strat_t), self.runner.run(backup_t))
        applied, err = (self._apply(agents.apply_strategist, strat.output, self.idx, c, self.dossiers, self.priced,
                                    self.forecasted)
                        if strat.ok else (None, strat.error))
        if applied is not None:
            excluded, srep = applied
            self.report["strategist"] = {"raw": strat.output, "applied": srep}
        else:
            main_ok = False
            self.report["strategist"] = {"error": err}

        try:
            final, res = self._choose(means, excluded, "final")
        except Infeasible:
            # Late swap: the unstarted games alone may not make a legal lineup (e.g. one game left and a
            # two-game minimum). Then the main lineup is built around the largest group of locked entries.
            from dfs.pipeline import entry_lock_groups
            groups = [(locks, idx) for locks, idx in entry_lock_groups(c, self.now).items() if locks]
            if not groups:
                raise
            self._fallback_locks = dict(max(groups, key=lambda g: len(g[1]))[0])
            self.report["built_around_locks"] = self._fallback_locks
            final, res = self._choose(means, excluded, "final")
        stage["after_strategist"] = final

        # Checker: code decides, up to 2 revisions.
        checks = []
        for attempt in range(3):
            if not main_ok:
                break
            if self.clock is not None and self.clock.left() < 60:
                checks.append({"attempt": attempt, "error": "skipped: run deadline reached"})
                break
            chk = await self.runner.run(agents.checker_task(c, final, self.dossiers, self.proj, model=cfg.review_model,
                                                            attempt=attempt, timeout_s=self._timeout(0.2)))
            verdict, err = (self._apply(agents.checker_verdict, chk.output, self.idx, c, final, self.priced,
                                        self.forecasted)
                            if chk.ok else (None, chk.error))
            if verdict is None:
                main_ok = False
                checks.append({"attempt": attempt, "error": err})
                break
            passed, fails, notes = verdict
            checks.append({"attempt": attempt, "passed": passed, "failed_picks": sorted(fails), **notes})
            if passed:
                break
            excluded |= fails  # a verified failure is excluded from every later lineup, the backup's included
            if attempt == 2:
                main_ok = False  # still failing after 2 revisions -> backup path
                break
            final, res = self._choose(means, excluded, "final")
        self.report["checker"] = checks
        stage["after_checker"] = final

        # Backup path if the main review failed.
        bapplied, berr = (self._apply(agents.apply_backup, backup.output, self.idx, c, means)
                          if backup.ok else (None, backup.error))
        if bapplied is not None:
            bpoints, bremoved, brep = bapplied
            self.report["backup"] = {"raw": backup.output, "applied": brep}
        else:
            bpoints, bremoved = means, set()
            self.report["backup"] = {"error": berr}
        if not main_ok:
            if bapplied is not None:
                final, res = self._choose(bpoints, excluded | bremoved, "final (backup review)")
                self.report["used_backup"] = True
            else:  # both reviews failed: the solver's lineup on the blended numbers, honestly labelled
                final, res = self._choose(means, excluded, "final (no review: both reviews failed)")
                self.report["used_backup"] = False
                self.report["no_review"] = True
        stage["final"] = final
        self.report["selection"] = {k: v for k, v in res.items() if k != "lineups"}

        # User rules -> second lineup.
        rules_lu = None
        if cfg.user_rules is not None:
            try:
                # your own locks win over the agents' cuts (players whose games started stay excluded)
                from dfs.optimize.ilp import validate_rules, _resolve_names
                validate_rules(c, cfg.user_rules)
                mine = _resolve_names(c, list(cfg.user_rules.lock))
                if mine & excluded:
                    self.report["user_overrode_cuts"] = sorted(mine & excluded)
                # with an entries file the rules lineup goes to the first entry, so it keeps that entry's locks
                locks = {}
                if c.entries:
                    from dfs.pipeline import entry_lock_groups
                    locks = next((dict(lk) for lk, idx in entry_lock_groups(c, self.now).items() if 0 in idx), {})
                rules_lu, _ = self._choose(means, excluded - mine, "your rules", user_rules=cfg.user_rules,
                                           slot_locks=locks or None)
            except Infeasible as e:
                self.report["user_rules_error"] = str(e)
                self.log(f"WARNING: your rules lineup could not be built: {e}")
        legal = check_legal(c, final)
        if legal:
            raise Infeasible(f"final lineup failed the independent legality check: {legal}")
        lineups, entry_ids = self._entry_lineups(final, bpoints if self.report.get("used_backup") else means,
                                                 excluded | (bremoved if self.report.get("used_backup") else set()))
        return Decision(final=final, lineups=lineups, rules_lineup=rules_lu, drafts=drafts, report=self.report,
                        stage_lineups=stage, entry_ids=entry_ids)

    def _entry_lineups(self, final: Lineup, points: dict[str, float],
                       excluded: set[str]) -> tuple[list[Lineup], list[str] | None]:
        """One lineup per entry row (portfolio for multi-entry); locked and started cells stay put.
        Entries for which no legal lineup exists any more are left out of the file (and reported)."""
        entries = self.c.entries
        if not entries:
            return [final], None
        from dfs.io.contest_file import contest_kind
        from dfs.pipeline import entry_lock_groups
        # Entries are grouped by their locked cells and by their own contest's objective, so a
        # Double Up entry in a file that also holds a tournament gets a cash lineup.
        kinds = [contest_kind([e.contest_name]) for e in entries]
        mixed = len(set(kinds)) > 1
        groups: dict[tuple, list[int]] = {}
        for locks, idx in entry_lock_groups(self.c, self.now).items():
            for i in idx:
                groups.setdefault((locks, kinds[i] if mixed else self.kind), []).append(i)
        out: list[Lineup | None] = [None] * len(entries)
        unsolvable: list[str] = []
        for (locks, kind), idx in groups.items():
            main = dict(locks) == self._fallback_locks and kind == self.kind
            if main and len(idx) == 1:
                out[idx[0]] = final
                continue
            try:
                _, res = self._choose(points, excluded, "final", n=len(idx), slot_locks=dict(locks), kind=kind)
            except Infeasible:
                unsolvable += [entries[i].entry_id for i in idx]
                continue
            lus = res["lineups"]
            if main:
                lus = [final] + [lu for lu in lus if lu.player_keys != final.player_keys]
            for j, i in enumerate(idx):
                out[i] = lus[j % len(lus)]
        self.report["entries"] = {"n": len(entries), "groups": len(groups), "mixed_contest_kinds": mixed,
                                  "left_unchanged_no_legal_lineup": unsolvable}
        keep = [i for i, lu in enumerate(out) if lu is not None]
        if not keep:
            raise Infeasible("no entry can be given a legal lineup from the players still available")
        return [out[i] for i in keep], [entries[i].entry_id for i in keep]

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
        if self.report.get("user_rules_error"):
            lines += ["", f"> WARNING: your rules lineup was not built: {self.report['user_rules_error']}"]
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
        if self.report.get("no_review"):
            lines.append("- WARNING: both the main and the backup review failed: this is the solver's lineup "
                         "on the blended numbers, with no agent review")
        lines += ["", "## Run", "", f"- timing (s): {timing}", f"- model calls: {llm}"]
        return "\n".join(lines) + "\n"
