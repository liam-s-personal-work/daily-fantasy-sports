# Lineup optimization and DFS game theory: what a top-~2% finish takes (VERIFIED EDITION)

> Paths under `scratch/` refer to verification scripts and downloads from the research session; they are not in the repo.

Research input for the DFS lineup engine (repo `liam-clifford/daily-fantasy-sports`). Draft written 2026-10-21; skeptical review and corrections on 2026-10-21.
This file replaces `optimization_game_theory.md`. It is self-contained.

## How this was verified, and what the tags mean

**Tags.**

- **[VERIFIED: S#/url]** means I confirmed it today on a primary source: code, a data file or a package, by reading or running it, or by a computation I re-ran.
- **[CORRECTED: was X; S#/url]** means the draft said X and the source says otherwise.
- **[UNVERIFIED]** means I could not confirm it today. Treat it as a lead, not a fact.

S-keys resolve to URLs in the table below.

**Tool limits (important for how much to trust the literature rows):**

- `WebFetch` fails DNS for every host (arXiv, dknetwork.draftkings.com and others tried).
- The sandbox proxy refuses CONNECT (403) to arXiv, SSRN, INFORMS, Wiley, Columbia, Imperial, McKinsey, DraftKings, the DFS media sites, Crossref, Semantic Scholar, OpenAlex and the Internet Archive.
- The **shared WebSearch budget for this turn (200 calls) was already used up** when this review started. The tool refused further searches and said not to work around it.
- So **every claim that came only from a WebSearch summary in the draft (papers, DK Network scores, vendor descriptions, practitioner benchmarks) is [UNVERIFIED] here.** Those numbers may still be right. They need re-checking from the PDFs or pages before anything quotes them.
- What I could reach: `raw.githubusercontent.com` (authors' and practitioners' code and real DK/FD export files), the GitHub code-search API, `pypi.org`, and the local Python stack. Everything tagged VERIFIED comes from those or from local runs.

**Verification scripts and data (all in the scratchpad):**

- Downloads: `scratch/verify_og/dl/` and `scratch/verify_og/dl2/`
- Scripts:
  - `scratch/verify_og/scripts/stats_check.py`: quantiles, sample sizes, payout SD
  - `scratch/verify_og/scripts/hall_check.py`: Hall exactness and timings
  - `scratch/verify_og/scripts/dist_check.py`: lineup score distribution
- The original benchmark and simulation code is in `scratch/bench/`. I re-ran `bench_ilp.py`, `bench_slots.py` and `bench_hall.py`, and re-aggregated `results/*.jsonl`.

### Source keys

| Key | URL | What it is |
|---|---|---|
| S1 | https://raw.githubusercontent.com/zlisto/Daily-Fantasy-Hockey-for-DraftKings/master/README.md | HVZ hockey code README (authors' repo) |
| S2 | https://raw.githubusercontent.com/zlisto/Daily-Fantasy-Hockey-for-DraftKings/master/code_for_Github.jl | HVZ Julia/JuMP formulations |
| S3 | https://raw.githubusercontent.com/zlisto/Daily-Fantasy-Baseball-Contests-in-DraftKings/master/README.md | HVZ baseball code README |
| S4 | https://raw.githubusercontent.com/chanzer0/NFL-DFS-Tools/main/README.md | chanzer0 NFL-DFS-Tools README |
| S5 | https://raw.githubusercontent.com/chanzer0/NFL-DFS-Tools/main/src/nfl_gpp_simulator.py | chanzer0 GPP simulator source |
| S6 | https://raw.githubusercontent.com/chanzer0/NFL-DFS-Tools/main/sample.config.json | chanzer0 sample config |
| S7 | https://raw.githubusercontent.com/DimaKudosh/pydfs-lineup-optimizer/master/pydfs_lineup_optimizer/sites/ (`draftkings/classic/settings.py`, `fanduel/classic/settings.py`, `draftkings/captain_mode/settings.py`, `draftkings/captain_mode/importer.py`, `fanduel/single_game/settings.py`, `fanduel/single_game/importer.py`, `fanduel/classic/importer.py`) | pydfs-lineup-optimizer. **Last PyPI release 3.6.1 on 2021-09-27** [VERIFIED: https://pypi.org/pypi/pydfs-lineup-optimizer/json], so it encodes 2021-era rules |
| S8 | https://pypi.org/pypi/PuLP/json and the installed PuLP 4.1.0 (`/usr/local/lib/python3.13/dist-packages/pulp/apis/__init__.py`) | PuLP 4.1.0, released 2026-10-19, requires Python ≥ 3.12 |
| S9 | https://pypi.org/pypi/ortools/json , https://pypi.org/pypi/highspy/json , https://pypi.org/pypi/scipy/json | OR-Tools 9.15.6755 (2026-01-14), highspy 1.15.1 (2026-07-02), SciPy 1.18.1 (2026-08-21) |
| S10 | https://raw.githubusercontent.com/umrebel3092/Umreble3092/HEAD/FanDuel-NFL-2025-CDT-10-CDT-19-CDT-121692-players-list.csv | Real FD NFL Single Game player list, ATL@SF, 19 Oct 2025 |
| S11 | https://raw.githubusercontent.com/jackdegen/nba-props/HEAD/data/current-draftkings-sg.csv | Real DK NBA Showdown export, Jan 2026 |
| S12 | https://raw.githubusercontent.com/gacolitti/draft.kings/HEAD/tests/testthat/optimize/url/lineups/1/rules.json (also `/70/` and `/96/`) | Captured DK gametype-rules API responses: NFL classic (1), NBA classic (70), NFL Showdown (96). Capture date unknown |
| S13 | https://raw.githubusercontent.com/NastyAnalytics/DFS/HEAD/CFB/DKSalaries.csv | Real DK CFB salary export (Oct 2022) |
| S14 | https://raw.githubusercontent.com/rogerfitz/tutorials/HEAD/draft_kings_contests_scrape/data/contest-standings-43293784.csv | Real DK contest-standings export (about 2017) |
| S15 | https://raw.githubusercontent.com/kbw612/Fantasy/HEAD/NFL/2025/DK/DK-main-slate-contest-standings-week16.csv (also `.../2025/DK/...week13.csv`, `.../2024/DK/...week16.csv`) | Real DK NFL main-slate standings, 2025 W16 (top 409 ranks of a 150-max GPP) |
| L1 | local: `bench/bench_ilp.py`, `bench_slots.py`, `bench_hall.py`, `sim_gpp.py`, `results/*.jsonl`, plus the `verify_og/scripts/*.py` above | re-runs and recomputations, 4 vCPU sandbox, Python 3.13 |
| SIB | `scratch/research/site_formats.verified.md` | sibling review of real DK/FD files; cited where I did not re-fetch its sources myself |

### The most important corrections (read these first)

1. **FanDuel Single Game is mis-described.**
   - The draft: MVP + 4 UTIL, "MVP multiplier UNCONFIRMED (commonly no salary change)".
   - Current FD NFL Single Game files carry an **`MVP 1.5x Salary` column equal to exactly 1.5 × Salary (57/57 rows, Oct 2025)**, with Roster Position `MVP - 1.5X Points/AnyFLEX` [VERIFIED: S10]. The current format is MVP + 5 FLEX with a 1.5× salary premium [VERIFIED in SIB; slot count not re-fetched here].
   - pydfs encodes the **legacy** 2021 rules: MVP + 4, 1.5× points, no salary change; FD NBA MVP 2×, STAR 1.5×, PRO 1.2× [VERIFIED: S7]. Never use pydfs rule encodings for current contests.
2. **DK Showdown salary is double-counted by the draft's formula.** DK exports give the CPT row its own ID and a salary already equal to 1.5× the FLEX/UTIL salary (34/34 pairs) [VERIFIED: S11]. The draft's `Σ (1.5 c_i + f_i) salary_i ≤ 50,000` is correct only when `salary_i` is the FLEX salary. With the CSV's CPT rows, use the row salary as is.
3. **PuLP 4.1.0 does not default to HiGHS.**
   - The default is CBC via `COIN_CMD` if a `cbc` binary or the `cbcbox` extra exists, else GLPK, else **none**. A bare `prob.solve()` raised `PulpError: No solver available` even though HiGHS was installed.
   - You must pass `pulp.HiGHS()`.
   - `pulp.LpStatus` is also gone: `solve()` returns an `LpSolveStatus` object.

   [CORRECTED: was "The default solver is HiGHS"; S8, L1]
4. **Cash-game break-even was conflated.**
   - A 1.8× payout (50/50 or H2H) breaks even at 55.6%.
   - A **double-up pays 2×** and breaks even at a **50%** cash rate, against a random-entrant base of about 43.5%.
   - The draft applied 55.6% to double-ups, including to its own synthetic double-up experiment.
   - "14.9% rake with 43.5% of the field paid" is internally inconsistent for a 2× double-up: 1 − 0.435 × 2 = 13.0% rake. [CORRECTED: computed, L1]
5. **Sample sizes for small hit rates were understated.** The normal approximation was used where exact binomial is needed. Exact binomial (one-sided α = 0.05, 80% power, base 2.3%):
   - about **368** contests for a 4.6% rate (not 339);
   - **114** for 6.9% (not 100);
   - **54** for 10% (not 43).

   These also require **independent slates**: the same lineup entered in several contests on one slate counts as one trial, not many. [CORRECTED: computed, L1]
6. **The synthetic "E[payout] beats max-mean" result is not statistically supported per slate.**
   - Paired differences in P(top 2.3%) over the 6 slates have p = 0.11 to 0.67.
   - E[payout] tied max-mean on several slates and lost on one (no-edge seed 201: −0.04 ROI).
   - The scenario-mean ordering is real, but "improved the top-2.3% rate whenever information was imperfect" overstates it. [CORRECTED: L1, `results/*.jsonl`]
7. **chanzer0's correlation table is a last-resort fallback, and its diagonal is dangerous.**
   - The values are exactly as the draft quoted [VERIFIED: S5].
   - The simulator first looks up empirical YAML/NPZ correlations by position and projection window. It uses the hardcoded matrix only if those files are missing, and they are not at the expected raw paths (404).
   - In the fallback, same-team same-position pairs (RB1–RB2, WR1–WR2) get the **diagonal value 1.00, clipped to 0.95** [VERIFIED: S5 `get_corr_value`]. Do not port the diagonal.
8. **"Proven optimality" overstated the benchmark.**
   - The benchmark never checks solver status. It only compared objective values across solvers.
   - HiGHS and OR-Tools MIP both default to a **0.01% relative MIP gap** (`mip_rel_gap = 1e-4`). CP-SAT's gap is effectively exact on integer-scaled objectives.

   [CORRECTED: L1, queried `highspy.Highs().getOptionValue('mip_rel_gap')` and `MPSolverParameters`]. Set the gap to 0 and assert OPTIMAL.

---

## 0. Headline findings (corrected)

1. **The lineup ILP is easy. The inputs are the hard part.**
   - Speed:
     - A DK NFL classic lineup solves in **about 0.02 to 0.75 s** for 156 to 416 synthetic players. The constraints are cap, slots, FLEX, the 2-game minimum and a QB + 2 stack.
     - The solvers tried were HiGHS (SciPy or PuLP), CBC, SCIP and CP-SAT. All returned the same objective; CP-SAT was within 0.01 because of ×100 rounding. [VERIFIED: re-run L1; times vary about ±50% run to run on this shared 4-vCPU box]
   - Exactness: optimality is within the solver's default 0.01% gap unless you set it to 0 (correction 8).
   - NBA multi-position: an aggregated **Hall's-condition** formulation solves in 0.2 to 1.35 s, against 0.46 to 2.9 s for slot assignment, with identical optima. A tighter version needs only one row per position union [VERIFIED: L1 `hall_check.py`, §2.3].
   - 150 diverse lineups take about 14 to 64 s depending on solver and machine load.
   - The real difficulty is the inputs: projections, distributions, correlations and ownership.
2. **The objective depends on the contest type.** This is standard DFS and contest theory; the paper attributions are [UNVERIFIED] today.
   - **Cash** (double-up, 50/50, H2H): maximize P(our score > the field's cash line). That is roughly the mean with a floor tilt.
   - **GPP** (top-heavy): maximize E[payout], which rewards variance, correlation and low duplication.
   - **Multi-entry:** greedy portfolio selection. P(at least one entry hits) is a coverage function, so it is monotone submodular, and greedy carries the classical (1 − 1/e) guarantee for a cardinality constraint (Nemhauser, Wolsey & Fisher 1978, Math. Programming 14:265–294) [UNVERIFIED: citation from memory]. The guarantee is relative to the sample-average objective over the candidate set, not the true contest.
3. **Opponent modelling paid in published work** [UNVERIFIED].
   - The draft relays Haugh & Singal (Management Science 2021): an opponent-aware portfolio returned over 350% across the 17-week 2017 NFL season in top-heavy contests, against about 50% for an opponent-agnostic benchmark, with a modelled weekly P(loss) of about 0.57.
   - Even if confirmed, this is **one season at small stakes** ($50/week top-heavy, by the draft's account). It is not statistical proof of an edge.
4. **"Top 2.3% of every contest" cannot be guaranteed.**
   - The 97.7th percentile is μ + 1.995σ under normality (Φ(2) = 0.97725) [VERIFIED: computed].
   - At a per-contest hit probability p = 0.20 (far above anything realistic), P(10 in a row) = 1.0e-7 [VERIFIED: computed].
   - In the synthetic experiment (§7.4):
     - a lineup chosen with **perfect knowledge of true means** finished top-2.3% 15 to 17% of the time;
     - a lineup with information no better than the field's finished there 3.4 to 4.3% of the time, against a 2.3% base rate.
   - The no-edge rate is above base only because the synthetic field contains weak, non-optimized builders; see §7.4 caveats. [VERIFIED: re-aggregated L1]
5. **Realistic, measurable targets (corrected):**
   - **GPP top-2.3% rate ≥ 2× base (4.6%):** about **370 to 400 independent slates** to show at 80% power, α = 0.05 (exact binomial: n = 368 with a 14-hit cutoff, or n = 400 with a 15-hit cutoff, power 0.82).
   - **Double-up (2×) cash rate ≥ 55 to 58%** against a **50%** break-even: about 620 / 240 slates.
   - **50/50 or H2H (1.8×) cash rate ≥ 58 to 60%** against **55.6%**: about 2,540 / 770 slates.
   - **Mean percentile finish** is the fast-feedback metric: 52 slates to detect a 10-point improvement, 206 for 5 points (one-sided).

   [CORRECTED: was "340 contests" and "double-up 58–60% vs 55.6%"; computed L1]
   - GPP ROI is hopeless as a short-term metric. For the synthetic top-heavy curve, the per-entry payout SD is **23.8× the entry (50k entries) and 39.5× (150k)**. Detecting +10% ROI takes **≈ 350k to 960k entries** [VERIFIED: computed L1].
6. **Grade against the hindsight-optimal lineup (HOL) only as a diagnostic.**
   - In the synthetic slates, HOL averaged 337, the best of a 10k field 246 (73%), and our oracle-informed lineup about 47% of HOL [VERIFIED: L1].
   - Those ratios are artefacts of an over-volatile synthetic world (§7.4).
   - Use calibration, ex-ante regret, ownership error and percentile finish instead (§8).
7. **"Top 3 per slot"**: rank by lock-delta under the contest objective, then optimal %, then leverage (optimal % − projected ownership), not by raw projection or value (§9).

---

## 1. Contest rules feed the constraint set (read them, don't hard-code them)

Build the solver entirely from a `ContestRules` object parsed from the DK/FD entries or salary CSV plus the site's rules. DK exposes a machine-readable rules object per game type. Third-party test fixtures capture it as `salaryCap`, `gameCount`, `teamCount`, `uniquePlayers`, `allowLateSwap` and `lineupTemplate` [VERIFIED: S12]. Whether the engine may fetch it automatically is a compliance question (see `sources_compliance.verified.md`).

| Format | Slots | Cap | Team / game rules | Multipliers | Evidence |
|---|---|---|---|---|---|
| DK NFL Classic | QB, RB, RB, WR, WR, WR, TE, FLEX (RB/WR/TE), DST | $50,000 | ≥ 2 games and ≥ 2 teams; late swap allowed | none | [VERIFIED: S12 gametype 1 (`gameCount` min 2, `teamCount` min 2, `allowLateSwap` true, template as listed); S7 `min_games = 2`] |
| DK NBA Classic | PG, SG, SF, PF, C, G (PG/SG), F (SF/PF), UTIL (any incl. C) | $50,000 | ≥ 2 games, ≥ 2 teams; late swap allowed | none | [VERIFIED: S12 gametype 70; S7] |
| DK NHL Classic | C, C, W, W, W, D, D, G, UTIL (C/W/D) | $50,000 | `min_teams = 3`, goalie excluded from the team count | none | [VERIFIED as 2021 code: S7]; current DK rule [UNVERIFIED] |
| DK Showdown (Captain), NFL | CPT + 5 FLEX (any position, incl. K and DST) | $50,000 | one game; must include players from **both** teams (`teamCount` min 2); unique players; **no late swap** | CPT scores **1.5× points**; **CPT salary is already 1.5× in the CPT row** | [VERIFIED: S12 gametype 96 (`positionTip` "Scores 1.5x Fantasy Points", error `INVALID_TEAM_COUNT_SD` "must include players from both teams", `allowLateSwap` false); S11 (CPT salary = 1.5 × UTIL on 34/34 pairs; separate IDs); S7 `max_from_one_team = 5`] [CORRECTED: CPT-row fact was tagged *low, recollection*] |
| DK CFB Classic | QB, RB, RB, WR, WR, WR, FLEX (RB/WR), S-FLEX (QB/RB/WR) | $50,000 [UNVERIFIED] | ≥ 2 games (pydfs) | none | [VERIFIED: S13 Roster Position tokens `QB/S-FLEX` (144), `RB/FLEX/S-FLEX` (199), `WR/FLEX/S-FLEX` (628); **no TE, DST or K position**; S7 `SUPER FLEX`, `min_games = 2`]. Missing from the draft; the user plays college football |
| FD NFL full roster | QB, RB, RB, WR, WR, WR, TE, FLEX (RB/WR/TE), DEF | $60,000 | ≤ 4 per team; ≥ 3 teams | none | [VERIFIED as 2021 code: S7]; [VERIFIED in SIB on 2026 files]. Older guides list a kicker, so read slots from the file |
| FD NFL Single Game (current, since about Aug 2025) | MVP + 5 AnyFLEX (QB/RB/WR/TE/K/D) | $60,000 | one game; ≥ 1 from each team | **MVP 1.5× points and 1.5× salary** (`MVP 1.5x Salary` column) | [VERIFIED: S10 (column = 1.5 × Salary on 57/57 rows; Roster Position `MVP - 1.5X Points/AnyFLEX`)]; slot count and team rule [VERIFIED in SIB, not re-fetched] [CORRECTED: was "MVP + 4 UTIL, multiplier UNCONFIRMED (commonly no salary change)"] |
| FD NFL Single Game (legacy, pydfs) | MVP + 4 UTIL (QB/WR/RB/TE/K) | $60,000 | ≤ 4 per team; max QB rule | MVP 1.5× points, no salary change | [VERIFIED as 2021 code: S7 `FanDuelMVPCSVImporter` (`fppg *= 1.5`, salary untouched)]. **Do not use for current contests** |
| FD NBA Single Game (current, 2025-26) | MVP + UTIL slots | $60,000 | one game | MVP 1.5× points and 1.5× salary | [VERIFIED in SIB (Jan 2026 file has `MVP 1.5x Salary`)]; slot count [UNVERIFIED] |
| FD NBA Single Game (legacy, pydfs) | MVP, STAR, PRO, UTIL, UTIL | $60,000 | ≤ 4 per team | MVP 2×, STAR 1.5×, PRO 1.2× points | [VERIFIED as 2021 code: S7 `build_fanduel_single_game_importer`] [CORRECTED: was "multipliers UNCONFIRMED"] |
| DK MLB Classic | P, P, C, 1B, 2B, 3B, SS, OF, OF, OF | $50,000 | max 5 hitters per team; ≥ 2 games | none | [VERIFIED in SIB; S7 `DraftKingsBaseballRosterRule`]; added because the MLB postseason is on |

**Design implication:**

- Parse `Roster Position` (the authoritative eligibility list) and each row's own salary from the CSV.
- Treat multipliers, minimum games and teams, team caps and late-swap permission as contest parameters that must be present before solving.
- **Fail closed:** the engine refuses to submit when any rule is unknown.
- Distinguish FD Single Game current from legacy by the **presence of the `MVP 1.5x Salary` column**, not by the Roster Position text, which is identical in both [VERIFIED in SIB].

---

## 2. Integer linear programming formulation

### 2.1 Sets and data

- Players `i ∈ I`. For captain or MVP contests, each (player, slot-type) pair is its own entity (DK already supplies separate CPT and FLEX rows with separate IDs [VERIFIED: S11]). Add one constraint per real player: at most one of their entities.
- Slots `s ∈ S`, each with an eligible position set `E_s`. Player `i` has eligibility `P_i`.
- Salary `c_i`, cap `C`, optional salary floor `C_min`.
- Team `t(i)`, game `g(i)`, opponent `o(i)`.
- Objective coefficient `v_i`: mean, a simulated outcome, or a risk-adjusted score (§2.5).

### 2.2 General slot-assignment formulation (always correct)

Variables: `y_{i,s} ∈ {0,1}`, defined only when `P_i ∩ E_s ≠ ∅`; `x_i = Σ_s y_{i,s}`; `z_g ∈ {0,1}` (game used); `w_t ∈ {0,1}` (team used).

```
max  Σ_i v_i x_i  (+ captain terms)
s.t. Σ_{i: P_i∩E_s≠∅} y_{i,s} = 1            ∀ s ∈ S                  (fill every slot)
     Σ_s y_{i,s} = x_i ≤ 1                     ∀ i                      (a player used at most once)
     Σ_i c_i x_i ≤ C        (and ≥ C_min if a floor is used)
     Σ_{i: t(i)=t} x_i ≤ T_max                 ∀ teams t                (FD: 4)
     z_g ≤ Σ_{i: g(i)=g} x_i,  Σ_g z_g ≥ G_min                          (DK classic: 2 games)
     w_t ≤ Σ_{i: t(i)=t} x_i,  Σ_t w_t ≥ T_min                          (FD: 3 teams; DK: 2; Showdown: both teams)
```

I checked this formulation and it is correct.

### 2.3 Faster aggregated formulations (no slot variables)

**Single-position sports (NFL).** Drop `y` and use position-count ranges:

- `Σ x = 9`, `QB = 1`, `DST = 1`, `2 ≤ RB ≤ 3`, `3 ≤ WR ≤ 4`, `1 ≤ TE ≤ 2`.
- Proof sketch: the minimums sum to 6 and RB + WR + TE = 7, so exactly one group sits at its upper bound. That is exactly the FLEX choice, so the feasible counts are exactly the legal rosters [VERIFIED: reasoning; used by `bench_ilp.py`].
- HVZ use the same range trick for hockey: "between 2 and 3 centers", "between 3 and 4 wingers", "between 2 and 3 defenders" [VERIFIED: S2 lines 427–437].
- DK CFB has the same structure with FLEX = RB/WR and S-FLEX = QB/RB/WR: `QB 1–2`, `RB 2–4`, `WR 3–5`, `Σ = 8`, plus `RB + WR ≥ 6`. Derive the ranges from the slots programmatically rather than by hand, or use Hall below, which is always exact.

**Multi-position eligibility (NBA, MLB, CFB).** A legal slot assignment of the chosen players exists **iff Hall's condition** holds:

- For every nonempty slot subset `W ⊆ S`, count the chosen players eligible for at least one slot in `W`: `Σ_i x_i · 1[P_i ∩ (∪_{s∈W} E_s) ≠ ∅] ≥ |W|`.
- Also require `Σ_i x_i = |S|`.
- For DK NBA that is 255 rows. Empirically exact: 0 mismatches against brute-force matching on 400 random 8-player eligibility sets [VERIFIED: L1 `hall_check.py`].
- **Tighter equivalent (new):** the row for `W` depends only on the union `U = ∪_{s∈W} E_s`, and it is implied by the row for `W' = {s : E_s ⊆ U} ⊇ W`. So use one row per position subset `U`: `Σ_i x_i · 1[P_i ∩ U ≠ ∅] ≥ #{s : E_s ⊆ U}`.
  - DK NBA: at most 2⁵ − 1 = 31 rows.
  - DK MLB: at most 2⁷ − 1 = 127 rows, against 1,023 slot subsets.
  - Same optima as the 255-row version; slightly faster (0.22 / 0.44 / 0.98 s against 0.23 / 0.51 / 1.35 s for 80 / 160 / 240 players) [VERIFIED: L1].
- Recover slots afterwards with a bipartite matching (Hopcroft–Karp or brute force over ≤ 9 slots).

**Captain / MVP (showdown).** Use two entities per player, `c_i` (captain) and `f_i` (flex):

- `Σ c_i = 1`, `Σ f_i = 5` (DK and current FD NFL), `c_i + f_i ≤ 1`.
- **Salary**:
  - with CSV entity rows, `Σ_i (sal^CPT_i c_i + sal^FLEX_i f_i) ≤ cap`, using each row's own `Salary`;
  - DK CPT rows are already 1.5× [VERIFIED: S11];
  - FD current uses `MVP 1.5x Salary` for the MVP entity [VERIFIED: S10];
  - only when you build entities yourself from FLEX salaries is it `Σ (1.5 c_i + f_i) sal_i ≤ cap`.

  [CORRECTED: was "Σ (1.5 c_i + f_i) salary_i ≤ 50,000 on DK; on FD check whether the MVP salary multiplier is 1.0"]
- **Objective**: `Σ (m_cpt v_i c_i + v_i f_i)` with `m_cpt = 1.5`. Use the **unmultiplied** projection: DK's `AvgPointsPerGame` is the same unmultiplied value on CPT and FLEX rows [VERIFIED in SIB].
- Team rule: each team has ≥ 1 player (DK Showdown) [VERIFIED: S12 gametype 96].

### 2.4 Stacking, correlation and "my rules" constraints (all linear; I checked each)

- **QB stack:** `Σ_{j ∈ WR∪TE, t(j)=t(q)} x_j ≥ k · x_q` for each QB q (k = 1 or 2).
- **Bring-back:** `Σ_{j ∈ WR∪TE∪RB, t(j)=o(q)} x_j ≥ b · x_q`.
- **No offense against your own DST:** `x_d + x_j ≤ 1` for each DST d and offensive player j with `t(j) = o(d)`. Aggregated form: `Σ_{j: t(j)=o(d)} x_j ≤ M(1 − x_d)` with M = lineup size − 1.
- **Game stack:** `u_g ∈ {0,1}`, `Σ_{i: g(i)=g} x_i ≥ m · u_g`, `Σ_g u_g ≥ 1`.
- **Hockey line stacks (HVZ Type 4, the code's default)** [VERIFIED: S2 lines 410–477, S1]:
  - one goalie and 8 skaters, with C 2–3, W 3–4 and D 2–3;
  - salary ≤ 50,000;
  - **exactly 3** skater teams;
  - no goalie facing any of your skaters: `6·g_i + Σ_k opp_{k,i} x_k ≤ 6`;
  - at least one full line (`3·L_ℓ ≤ Σ_{i∈ℓ} x_i`, `Σ L_ℓ ≥ 1`);
  - at least 2 lines with ≥ 2 players;
  - all defensemen on power-play 1;
  - overlap with earlier lineups ≤ `num_overlap`.

  The code also has no-stacking and Types 1, 2, 3 and 5 variants [CORRECTED: was "Type 1 to 4"; S2 line 2]. The default is set at line 610, not 608 as the README says [VERIFIED: S2].
- **Diversity / overlap:** for each prior lineup `L_k`, `Σ_{i∈L_k} x_i ≤ |L| − u`.
  - HVZ hockey code default: 100 lineups, max overlap 7 [VERIFIED: S1, S2 lines 26 and 29].
  - HVZ baseball code: 150 lineups, max overlap 6, a stack of 5 consecutive batters [VERIFIED: S3].
- **Late swap (missing from the draft).** Once games lock, add `x_i = 1` for every player in our lineup whose game has started, and `x_i = 0` for every non-lineup player whose game has started.
  - DK entries files mark such cells `(LOCKED)` [VERIFIED in SIB].
  - DK classic allows late swap; DK Showdown does not [VERIFIED: S12 `allowLateSwap`].
- **"My rules" (user rules)**, each a constraint:
  - lock `x_i = 1`; exclude `x_i = 0`;
  - group at-most or at-least: `Σ_{i∈G} x_i ≤ / ≥ k`;
  - team or game exposure;
  - salary floor;
  - "if A then B": `x_A ≤ x_B`.

  Solve twice, with base constraints and with base plus user rules. Report both objectives; the difference is the **price of the rule**.

### 2.5 Objectives the ILP can carry

1. Mean: `v_i = μ_i`.
2. A simulated outcome: `v_i = X_i^{(k)}`. One ILP per sim gives the **sim-optimal lineup set**, used for candidate generation and "optimal %" (§5, §9).
3. Perturbed means: `v_i = μ_i · exp(ε_i)`, `ε_i ~ N(0, 0.10–0.15)`, for diversity (chanzer0 `randomness` [VERIFIED: S6]).
4. **Mean + λ·variance.** Lineup variance `x'Σx` is quadratic.
   - HVZ avoid it by constraining structure (stacks, overlap) rather than putting variance in the objective [VERIFIED for the code: S2, whose objective is the projection sum].
   - The draft says Haugh–Singal solve a series of binary quadratic programs over λ [UNVERIFIED].
   - Scenario-linear alternatives both need a binary per scenario (big-M): maximize the sample-average P(score > τ), or `Σ_k (score_k − τ)_+`. For the latter, `s_k ≤ score_k − τ + M(1 − b_k)`, `s_k ≤ M b_k`, `s_k ≥ 0`; maximizing a positive part is not LP-representable without the binaries.
   - Those MILPs get slow beyond a few hundred scenarios. Prefer **generate-then-evaluate** (§5).

### 2.6 Python-ish reference implementation (fixed)

```python
# Data contract (parsed from DK/FD CSV + rules object)
ContestRules = dict(site="DK", sport="NFL", game_type=1, cap=50000, salary_floor=None,
    slots=[("QB",{"QB"}),("RB",{"RB"}),("RB",{"RB"}),("WR",{"WR"}),("WR",{"WR"}),("WR",{"WR"}),
           ("TE",{"TE"}),("FLEX",{"RB","WR","TE"}),("DST",{"DST"})],
    min_games=2, min_teams=2, max_per_team=None, allow_late_swap=True,
    entity_multipliers={"CPT": {"points": 1.5}},   # salary comes from each CSV row; never multiply it again
    lineup_size=9)

def build_model(players, rules, v, extra_rules=(), prev_lineups=(), min_uniques=0, stack=None, locked=None):
    m = Solver("CBC"); m.set_rel_gap(0.0)            # default is 1e-4 in HiGHS/OR-Tools MIP
    x = {i: m.bool() for i in players}
    if all_single_position(players, rules): add_position_count_ranges(m, x, rules)   # derived from slots
    else:                                   add_hall_union_rows(m, x, players, rules)  # one row per position union
    m.add(sum(x.values()) == rules["lineup_size"])
    m.add(sum(p.salary * x[i] for i, p in players.items()) <= rules["cap"])          # row salary as given
    if rules["salary_floor"]: m.add(sum(p.salary * x[i] for i, p in players.items()) >= rules["salary_floor"])
    for real_player, ents in entities_by_player(players).items():
        if len(ents) > 1: m.add(sum(x[e] for e in ents) <= 1)                          # CPT/FLEX twins
    add_min_distinct(m, x, players, key="game", k=rules["min_games"])                  # z_g rows
    add_min_distinct(m, x, players, key="team", k=rules["min_teams"])                  # w_t rows
    if rules["max_per_team"]:
        for t in teams(players): m.add(sum(x[i] for i in team_players(t)) <= rules["max_per_team"])
    if locked: apply_late_swap_locks(m, x, locked)                                     # §2.4
    if stack: add_stack_rows(m, x, players, **stack)
    for L in prev_lineups: m.add(sum(x[i] for i in L) <= rules["lineup_size"] - min_uniques)
    for r in extra_rules: r.apply(m, x)
    m.maximize(sum(v[i] * x[i] for i in players))
    return m, x

def solve_lineup(...):
    m, x = build_model(...)
    status = m.solve(time_limit=10)
    if status != OPTIMAL: raise SolveError(status)   # never ship a non-optimal or infeasible result silently
    L = [i for i in x if x[i].value() > 0.5]
    slots = assign_slots(L, rules)                    # bipartite matching -> slot order for upload CSV
    validate_lineup(L, slots, rules)                  # independent re-check in plain Python
    return L, slots
```

Performance pattern: build the model once and change only the objective between solves (`LineupILP` in `sim_gpp.py`). That gives about **30 to 37 ms per solve** on a 312-player slate with CBC (500 solves in 16.9 to 18.3 s in the stored runs) [VERIFIED: L1 `results/*.jsonl` timing]. Diversity constraints that change per lineup still need adding rows; OR-Tools supports adding constraints to a live model.

### 2.7 Solver options

| Solver | Access from Python | Notes |
|---|---|---|
| HiGHS | `scipy.optimize.milp` (bundled in SciPy 1.18.1), `highspy` 1.15.1, PuLP 4 via **explicit** `pulp.HiGHS()` | MIT licence; `mip_rel_gap` default 1e-4 [VERIFIED: L1] |
| CBC | OR-Tools `pywraplp.Solver.CreateSolver("CBC")`; PuLP via the `pulp[cbc]` extra (`cbcbox`) | Fastest single-thread in these runs; OR-Tools MIP default relative gap 1e-4 [VERIFIED: L1, S8] |
| SCIP | OR-Tools `CreateSolver("SCIP")` | Fine, slower here |
| CP-SAT | OR-Tools `cp_model` | Needs **integer** coefficients (scale points ×100). Multi-threaded. Default `relative_gap_limit` 0 and `absolute_gap_limit` 1e-4, so it is exact on integer objectives [VERIFIED: L1] |
| GLPK | HVZ used GLPK through JuMP, finding it "slightly faster in practice" than CBC for their formulations | [VERIFIED: S1, S3] |

**Gotchas verified today:**

- **PuLP 4.x (4.1.0, released 2026-10-19, Python ≥ 3.12) broke the 3.x API** [VERIFIED: S8, L1]:
  - variables are created with `prob.add_variable(name, lowBound, upBound, cat)`;
  - `pulp.LpVariable("x", cat=...)` raises `TypeError` ("unexpected keyword argument 'cat'"), and the positional 3.x form raises `TypeError` too;
  - `PULP_CBC_CMD` is gone, CBC is not bundled, and `pulp.LpStatus` is gone (`solve()` returns an `LpSolveStatus` object);
  - **the default solver is CBC via `COIN_CMD`, else GLPK, else none**, so `prob.solve()` with no argument fails on a machine with only HiGHS. [CORRECTED: was "The default solver is HiGHS"]
  - Most online DFS code (pydfs-lineup-optimizer and others) uses the 3.x API. **Pin `pulp<4`, or don't use PuLP.**
- **`highspy` and OR-Tools `pywraplp` cannot be imported into one process** [VERIFIED: L1, both import orders]. OR-Tools 9.15.6755 bundles its own HiGHS, so whichever library loads second raises a catchable `ImportError` (undefined symbol):
  - `…setLocalOptionValue…HighsLogOptions…` when OR-Tools loads second;
  - `_ZN5Highs13releaseMemoryEv` when highspy loads second.

  SciPy's `milp` and OR-Tools do coexist. PuLP's HiGHS path uses `highspy`, so **PuLP-HiGHS and OR-Tools also conflict**. Pick one stack per process.

### 2.8 Measured solve times (4 vCPU sandbox, Python 3.13; synthetic slates; median of 5)

DK NFL classic with the 2-game minimum and a QB + ≥ 2 WR/TE stack, maximizing mean. Objective values: 153.12, 154.60 and 158.52 for all solvers (CP-SAT 154.59). First number: the draft's run. Second: my re-run today [VERIFIED: L1].

| Players (games) | SciPy-HiGHS | PuLP-HiGHS | OR-Tools CBC | OR-Tools SCIP | CP-SAT (8 workers on 4 vCPU) |
|---|---|---|---|---|---|
| 156 (6 g) | 0.18 / 0.22 s | 0.19 / 0.22 s | 0.04 / 0.04 s | 0.21 / 0.20 s | 0.06 / 0.42 s |
| 312 (12 g) | 0.55 / 0.75 s | 0.61 / 0.61 s | 0.12 / 0.19 s | 0.74 / 0.71 s | 0.12 / 0.16 s |
| 416 (16 g) | 0.03 / 0.04 s | 0.05 / 0.08 s | 0.02 / 0.02 s | 0.14 / 0.08 s | 0.05 / 0.09 s |

- Solve time depends on the instance, not just size: the 416-player slate is the fastest.
- CP-SAT with 8 workers on 4 vCPU is oversubscribed, and its variance is high.
- 150 sequential lineups (perturbed means, ≥ 3 uniques against all previous, QB stack, Python model rebuild per lineup): CBC 14.2 / **18.4 s**, CP-SAT 15.1 / **25.6 s**, SciPy-HiGHS 48.0 / **64.0 s** (draft / re-run) [VERIFIED: L1; machine shared with other jobs].
- DK NBA classic (8 slots, multi-position), slot assignment against 255-row Hall, for 80 / 160 / 240 players:
  - draft: 0.46 / 2.13 / 1.68 s against 0.20 / 0.34 / 0.59 s;
  - re-run: 0.56 / 2.88 / 2.87 s against 0.23 / 0.51 / 1.35 s.

  Identical objectives. Speed-up **about 2 to 6×** [CORRECTED: was "3 to 6×"; L1].
- DK Showdown (46 players, captain entities): 0.11 / 0.18 s [VERIFIED: L1].

**Recommendation:**

- OR-Tools: CBC through `pywraplp` for MILP, CP-SAT when there are many logical side constraints.
- SciPy `milp` as the no-extra-dependency fallback, in a process without OR-Tools if you need highspy.
- Set the MIP gap to 0, assert OPTIMAL, and re-validate the lineup in plain Python.

---

## 3. Contest types and their correct objectives

Notation: our lineup score `S_L`; the field's relevant order statistic (cash line or top-x% line) `T`. Both are random and **correlated**, because they share player outcomes.

### 3.1 Cash games (double-up, 50/50, H2H)

**Payout arithmetic.** For a fraction paid `f` at multiplier `m`: rake = 1 − f·m and break-even cash rate = 1/m [VERIFIED: algebra].

| Type | Paid | Pays | Rake | Break-even | Random-entrant base rate |
|---|---|---|---|---|---|
| Double-up | about 43.5% | 2× | 13.0% | **50.0%** | 43.5% |
| 50/50 | 50% | 1.8× | 10% | **55.6%** | 50% |
| H2H | — | $10 → $18 (1.8×) | 10% | **55.6%** | 50% |

- The draft's "$25 DK double-up with 14.9% rake and 43.5% paid" (Rotowire) is internally inconsistent for a 2× payout. 14.9% rake implies 42.6% paid at 2×, or a 1.956× payout at 43.5% paid. Read the payout table from the contest. [CORRECTED: arithmetic L1; source page UNVERIFIED]
- Practitioner benchmarks ("58 to 62% very favorable", "60% over a season is professional") [UNVERIFIED]. They do not say whether they mean 50/50s or double-ups, which have different break-evens.

**Correct objective:** `max_L P(S_L > T_cash)`. Under a normal approximation:

`P(S_L > T) ≈ Φ( (μ_L − μ_T) / sqrt(σ_L² + σ_T² − 2·Cov(S_L, T)) )` [VERIFIED: algebra]

- When `μ_L > μ_T`, lower variance helps. That is the floor tilt.
- Owning the chalk raises Cov(S_L, T) and shrinks the denominator. That is why cash lineups "eat chalk".
- Stacking is optional.

**Implementation:**

- Generate candidates: mean-optimal, `μ_i − κσ_i` for κ ∈ {0, 0.25, 0.5}, plus sim-optimal lineups.
- **Evaluate P(S_L > q̂_{1−f}(field))** in simulation (the 56.5th percentile when 43.5% are paid, the 50th for a 50/50) and pick the argmax.
- Verify on history that the chosen κ beats pure max-mean.

### 3.2 GPP (top-heavy tournaments)

**Payout facts relayed by the draft (all [UNVERIFIED]; read the real payout table from the contest page):**

- About the top 15 to 25% are paid (about 18 to 20% for Milly-style 150k+ fields).
- Min-cash about 1.5 to 2× the entry.
- First place about 29% of the pool in recent DK NFL Millionaire contests ($1M of $3.5M), and about 40% in a 2015 contest.
- Rake about 15%, up to 16%.

**Correct objective:** `max_L E[payout(rank(S_L; field), dup_L)]`. Payouts are convex and decreasing in rank, so this:

1. rewards **variance and positive within-lineup correlation** (QB + pass-catchers, bring-backs);
2. rewards **low ownership at equal points** (leverage);
3. penalizes **expected duplication** (prizes split).

**Proxy objectives:**

- P(top x%), with x where the payout curve bends;
- P(first) only for winner-take-all;
- E[payout] whenever the payout table is known.

### 3.3 Single-entry vs multi-entry

**Single-entry or 3-max:** pick the one lineup with max E[payout] (or P(top x%)).

**Multi-entry:**

- DK contest names carry the entry cap, e.g. `[150 Entry Max]`, `[20 Entry Max]`, `[Single Entry]` [VERIFIED in SIB].
- 150-max DK NFL main-slate GPPs exist: 2025 W16 standings entry names carry counters such as `(95/150)`, and the largest denominator seen is 150 [VERIFIED: S15]. [CORRECTED: was *low, unconfirmed*]
- Read `max_entries` from the contest name.

**HVZ greedy portfolio:**

- The draft relays: sequential lineups maximizing mean, subject to a variance floor and a correlation or overlap cap with earlier entries; goal = max P(at least one wins), described as submodular [UNVERIFIED for the paper].
- The code's overlap cap is verified [S1, S2].

**Implemented (`greedy_portfolio` in `sim_gpp.py`)** [VERIFIED: L1 code]:

- From a candidate × sim boolean "hit" matrix, repeatedly add the candidate covering the most uncovered sims.
- For E[max payout]: `gain_j = E[max(current_best, payout_j)] − E[current_best]`. This is also monotone submodular (facility location).
- **Caveat (missing from the draft):** the real multi-entry objective is E[Σ payouts of our entries], and our own entries compete with one another for places and split prizes when they duplicate. E[max] ignores min-cashes from second and later entries, and P(any hit) ignores how far above the line each hit lands.
  - Use them to choose, then score the final portfolio by inserting **all** our entries into the simulated field together.
  - That objective is not guaranteed submodular, but greedy is the standard heuristic.

### 3.4 Showdown / single game

- The pool is small (about 40 to 60 entities), so **duplication dominates GPP EV**. The captain choice drives both correlation and leverage.
- Enumerating lineups is feasible for small pools, and the full sim-optimal approach works.
- Model CPT and FLEX ownership separately (chanzer0 accepts a `CptOwn%` column [VERIFIED: S4]).
- DK Showdown has **no late swap** [VERIFIED: S12 gametype 96].

### 3.5 Recommendation per contest type (design guidance, not empirical claims)

| Contest | Objective used to pick | Candidate generator | Ownership use | Stacking | Sims |
|---|---|---|---|---|---|
| H2H / 50-50 / double-up | max P(S > field's (1 − f) quantile) | mean-optimal; μ − κσ, κ ∈ {0, 0.25, 0.5}; 100 sim-optimal | only through the field model | not required | 5k |
| Small-field GPP (< 1k), SE / 3-max | max E[payout] with dup splitting | sim-optimal (300) + perturbed stacked (200) | implicit via field sim | QB + 1 to 2 | 10k |
| Large-field GPP (Milly-style), SE | max E[payout]; check P(top 0.1%) and P(top 1%) | same, plus forced contrarian stacks | strong; penalize dupes | QB + 2 + bring-back typical | 10k to 20k sims; **field ≥ 50k to 100k** (§5.2) |
| Large-field GPP, MME | greedy on E[max payout] or P(any hit), then joint Σ-payout check | 2k+ candidates | strong; cap exposure | varied | 10k to 20k |
| Showdown GPP | max E[payout] with explicit dup model | enumerate or sim-optimal over CPT × FLEX | very strong | CPT + correlated FLEX | 10k |

---

## 4. Ownership projection, leverage and duplication

### 4.1 Ownership projection methods

1. **Statistical model of the field (Haugh & Singal)** [UNVERIFIED]. The draft relays: opponents' lineups follow a Dirichlet-multinomial process with parameters from a Dirichlet regression on player features. The same model is used to value insider information and collusion.
2. **Field-simulation ownership (SaberSim)** [UNVERIFIED]. The draft relays: ownership is a descriptive statistic of a large simulated field, recomputed whenever sims rerun.
3. **Feature regression, the practical default.** For each slate and position `p`:

   `own_i = n_p · softmax_i( β_v·z(value_i) + β_μ·z(μ_i) + β_s·z(salary_i) + β_vegas·z(implied_total_t(i)) + β_news·news_i + β_min·1[min-priced starter] )`

   - `n_p` = expected lineup slots for position p, including the FLEX share. For DK NFL: QB 1, DST 1, RB ≈ 2.35, WR ≈ 3.45, TE ≈ 1.2 (these sum to 9; FLEX share RB 0.35, WR 0.45, TE 0.2: an assumption, so estimate it from data).
   - The sum rule is right: **%Drafted in DK standings sums to about 900%** across the pool for a 9-player lineup (899% in three 2024-25 files) [VERIFIED: S15], so ownership is the fraction of lineups containing the player.
   - **Fix (missing from the draft):** `n_p · softmax` can exceed 1 for a dominant player (for example RB, n = 2.35 × 0.5 = 1.17). Cap at 1 (better, at 0.95) and redistribute the excess to the others proportionally, iterating until no player exceeds the cap. Clipping without redistribution (as `sim_gpp.py` does at 0.95) breaks the sum.
   - Fit the β's by minimizing ownership MAE or multinomial log-loss against historical **%Drafted**.
4. **DK contest-standings export format** [VERIFIED: S14, S15]:
   - About 2017: `Rank, EntryId, EntryName, TimeRemaining, Points, Lineup, (blank), Player, %Drafted, FPTS`.
   - 2024-25: the same plus **`Roster Position`** between `Player` and `%Drafted`.
   - `%Drafted` is a string like `38.19%`. `EntryName` carries a multi-entry counter like `PhilliphJM83 (20/26)`. `Lineup` is one string of slot-label/name pairs in no fixed slot order, e.g. `DST Giants  FLEX Harold Fannin Jr. QB Tyler Shough RB Bijan Robinson …`. Parse it by splitting on known slot labels.
   - The Player / %Drafted / FPTS block is a separate player table running down the same rows.
   - Parse by header name, never by column index. [CORRECTED: was "column name UNCONFIRMED"]
5. **LLM role:** research agents supply news and narrative features, for example a backup RB starting at minimum salary. A code model turns those into ownership. Never let an LLM emit raw ownership numbers without a calibrated model behind them.

**Accuracy:**

- The draft says no vendor publishes independent ownership-accuracy benchmarks [UNVERIFIED: an absence claim from search].
- It also relays the Fantasy Projection Lab point that projection and ownership accuracy are coupled [UNVERIFIED].
- Measure our own ownership MAE per contest, by salary tier.

### 4.2 Leverage

- Vendor definitions relayed by the draft [UNVERIFIED]:
  - **Stokastic:** *optimal %* is how often a player is in the sim-optimal lineup; *leverage* = optimal % − projected ownership (16% optimal at 4% owned = +12).
  - **4for4:** converts tournament-winning odds into an implied ownership and compares it with projected ownership.
- **Formal definitions for code:** `lev_i = π_i − own_i` and `lev_ratio_i = log(π_i / own_i)`, where `π_i` = P(i is in the sim-optimal or winning lineup), estimated from sims (§5).
- Leverage is a **diagnostic and ranking feature**. The **decision** comes from E[payout] in the contest simulation, which already prices ownership through the field.

### 4.3 Duplication

- Expected duplicates of lineup L in a contest with N entries: `E[d_L] ≈ (N − 1) · P(a field lineup = L)`.
  - Estimate from exact matches in the simulated field, or analytically as `(N − 1) · κ · Π_{i∈L} own_i`, with κ calibrated on history.
  - κ absorbs slot-permutation and FLEX-share effects; κ = 1 is not justified a priori.
- Establish The Run findings relayed by the draft [UNVERIFIED; the draft itself never opened the article]:
  - the *product* of ownerships predicts duplicates better than the *sum* (r² 0.56 vs 0.24);
  - top-1% lineups containing a sub-$2,000 player averaged 18.9 duplicates vs 14.7;
  - 52.7% of lineups used ≤ $49,500.
- **Payout with ties:** if L finishes at place r with d **other** identical entries, each copy receives the mean of `payout[r], payout[r+1], …, payout[r+d]` (d + 1 places, inclusive).
  - As a Python slice that is `payout[r : r+d+1]`. The draft's `mean(payout[r : r+d])` is off by one if read as a slice.
  - `sim_gpp.py::evaluate` does it correctly (`hi = place + d`, inclusive). [CORRECTED: notation; L1]
  - Ties between *different* lineups with equal scores also split prizes; the sim ignores these, which is minor.

---

## 5. Contest simulation and GPP lineup selection (the algorithm)

### 5.1 Player outcome model

**Marginals.** Per player: mean μ_i, SD σ_i and skew.

- chanzer0 [VERIFIED: S4, S5]:
  - bins players by projection window;
  - samples from **empirically fitted** families per position and window (gamma, lognormal, Weibull, skew-normal, ex-Gaussian, generalized gamma, shifted gamma);
  - affine-matches each player's draws to the projected mean and SD exactly;
  - falls back to a calibrated gamma when a heavy-tailed fit gives extreme tails.
- **Low / likely / high → distribution (closed form, new).** Define the LLM's triple as **p10 / p50 / p90**, and say "median", not "most likely", in the prompt: "likely" invites the mode.
  - A 2-parameter lognormal cannot match three arbitrary quantiles. Use a **shifted lognormal** `X = c + exp(m + sZ)` with z = Φ⁻¹(0.9) = 1.28155:
    - `r = (p90 − p50)/(p50 − p10)`, which must be > 1 (right skew);
    - `s = ln(r)/z`;
    - `e^m = (p50 − p10)/(1 − e^{−z s})`;
    - `c = p50 − e^m`;
    - mean = `c + e^{m + s²/2}`, SD = `e^{m + s²/2}·sqrt(e^{s²} − 1)`.
  - Round-trip exact on test triples, e.g. (8, 15, 27) → c = −1.80, m = 2.821, s = 0.421, mean 16.55, SD 8.07 [VERIFIED: L1].
  - If r ≤ 1, fall back to a normal or skew-normal.
  - **Truncate at the site's minimum possible score.** For (−1, 6, 16) the fitted shift is c = −17.3, an impossible floor for most positions.
  - Consider a point mass near 0 for injury exits.
- **Typical spread** (CV of weekly fantasy points), relayed from Underdog Network and PlayerProfiler [UNVERIFIED]: QB ≈ 0.36 to 0.40, RB ≈ 0.5 to 0.63, WR ≈ 0.58 to 0.70, TE ≈ 0.63 to 0.80. Order QB < RB < WR < TE.

**Correlation.**

- chanzer0 uses a Gaussian copula: correlated normals via Cholesky, then each player's sorted draws are re-ordered to the normals' ranks. The README calls this Iman–Conover [VERIFIED: S5 lines 1710–1724].
- **Note:** the copula parameter is a normal-score correlation, not the outcome Pearson or Spearman correlation. For a target Spearman ρ_s, use `r = 2·sin(π·ρ_s/6)`. Checked by simulation: 0.36 → 0.3748, achieving 0.3602 [VERIFIED: L1].
- **chanzer0 lookup order** [VERIFIED: S5 `get_corr_value`]: player override → YAML by (pos1, pos2, same_team, projection window) → NPZ → **hardcoded positional matrix** → 0.
  - The YAML and NPZ files were not found at `distribution_data/fp_correlations_dk.yaml` or `.npz` (HTTP 404).
  - So a fresh clone may run entirely on the fallback matrix below. Its empirical provenance is unknown.

Hardcoded fallback values [VERIFIED: S5 lines 905–912]. All values match the draft.

| pair | same team | vs opponent |
|---|---|---|
| QB–WR | 0.36 | 0.12 |
| QB–TE | 0.35 | 0.10 |
| QB–RB | 0.10 | 0.07 |
| QB–QB | (diagonal, see below) | 0.23 |
| QB–DST | 0.04 | −0.30 |
| RB–DST | 0.10 | −0.21 |
| RB–K | 0.16 | −0.13 |
| K–DST | 0.23 | −0.32 |
| WR–DST | 0.06 | −0.12 |
| WR–TE | 0.03 | 0.06 |
| DST–DST | n/a | −0.13 |

**Same-team, same-position pairs.**

- chanzer0's fallback uses the matrix diagonal (1.00), clipped to **0.95**, for teammates at the same position (RB1–RB2, WR1–WR2). That is almost certainly wrong, since teammates compete for volume [VERIFIED: S5; CORRECTED: draft said these pairs were "not in that table"].
- `bench/slate.py` instead assumes RB–RB −0.15, TE–TE −0.10, QB–QB (backup) −0.50 and WR–WR 0.00. These are assumptions; estimate them from data.
- Repair the matrix to PSD (eigenvalue clipping and re-normalizing the diagonal), as chanzer0 does [VERIFIED: S5 `ensure_positive_semidefinite`].

### 5.2 Field model

The field is a **mixture of builder types**, calibrated to projected ownership.

1. **Ownership-weighted builders.**
   - Sample each slot by Gumbel-top-k on `log w_i`. That gives Plackett-Luce sampling without replacement, so marginal inclusion is **not** proportional to w for k > 1. Hence calibration step 3.
   - With probability `p_stack`, add a QB-stack bonus to the QB's teammates. chanzer0 sample config: `pct_field_using_stacks 0.65`, `pct_field_double_stacks 0.4` [VERIFIED: S6, S4]. The code picks a 2- or 3-player stack with P(3) = `pct_field_double_stacks` [VERIFIED: S5 line 1267].
   - Accept only lineups with salary in [`min_lineup_salary`, cap] and projection within `max_pct_off_optimal` of the optimum.
     - Sample config: 49,200 and 0.25. Class defaults: 48,000 and 0.4.
     - Also in the sample config: `randomness` 25 (class default 100) and `global_team_limit` 4. [VERIFIED: S6, S5 lines 52–54]
2. **Optimizer users:** noisy-projection ILP lineups (`μ·exp(N(0, 0.12))`), sampled with replacement. That produces duplication: the synthetic field had a **27% duplicate rate** (0.269 ± 0.002) [VERIFIED: L1]. Whether that is realistic is [UNVERIFIED].
3. **Calibration:** multiplicative reweighting `w ← min(w·clip(target/realized, 0.5, 2)^0.6, 3·target)` until exposure matches projected ownership [VERIFIED: L1 code]. Realized ownership was within 0.9 points MAE of target. Aggressive steps collapse the salary-window acceptance rate, so use damped steps.

**Field size (correction and omission).** Map the fraction of the simulated field beating us to a place: `place = 1 + floor(frac·(N − 1))`, with `searchsorted(side="right")` so ties are not counted above us.

- With a 10k simulated field and N = 150k entries, each simulated lineup stands for 15 real entries. The top 0.1% (150 places, where most of a Milly's prize money sits) is represented by about 10 simulated lineups.
- E[payout] for large GPPs is therefore dominated by a poorly resolved tail.
- Use a field of **≥ 50k to 100k** for large GPPs, or fit a parametric tail (e.g. GPD) to the field's top scores per sim. A 10k field is fine for percentile lines up to about the 99th.

### 5.3 Candidate generation

- `K_sim` **sim-optimal** lineups: solve the ILP on simulated outcome vectors. This also yields optimal % `π_i`.
- `K_pert` **perturbed-mean stacked** lineups.
- The mean-optimal lineup, the μ − κσ lineups, and **forced-stack variants** (one per QB or per game) for coverage.
- Typical unique count: about 470 to 500 from 300 + 200 solves [VERIFIED: L1, n_cands 470 to 501].
- Use **different sims** for generation and evaluation, as `sim_gpp.py` does (`sims[:K_sim]` vs `sims[K_sim:]`) [VERIFIED: L1].

### 5.4 Evaluation and selection (pseudo-code fixed)

```python
def select(contest, players, proj, own_proj, rules, n_sims=10_000, n_field=None):
    n_field = n_field or (100_000 if contest.N > 20_000 else 10_000)              # §5.2 tail resolution
    sims   = simulate_outcomes(players, proj.mean, proj.sd, corr, n_sims + 300)   # float32
    gen, ev, holdout = sims[:300], sims[300:300 + n_sims//2], sims[300 + n_sims//2:]
    field  = build_field(players, own_proj, n_field, field_params)
    cands  = gen_candidates(players, rules, gen, proj)
    dup    = np.array([(contest.N - 1) * kappa * np.prod(own_proj[list(L)]) for L in cands])  # per candidate
    pay    = contest.payout_table                                                  # place -> $, 1-indexed
    for chunk in chunks(ev, 500):
        F = np.sort(chunk @ Mf, axis=1); C = chunk @ Mc
        above = n_field - searchsorted_rows(F, C, side="right")                    # field lineups strictly above us
        place = 1 + np.floor(above / n_field * (contest.N - 1)).astype(int)
        epay += tie_block_mean(pay, place, dup)                                    # mean of pay[place .. place+d]
        hit_top  |= ...; hit_cash += C > F[:, q_index(1 - contest.paid_frac)]
    j = choose(contest, epay, hit_top, hit_cash)                                   # by contest type / entries
    report = evaluate(holdout, field, [cands[j]])                                  # unbiased estimate (winner's curse)
    return cands[j], report
```

**Winner's curse (missing from the draft).** Picking the argmax of ~500 noisy estimates biases the chosen lineup's estimated P(top) and E[payout] upward. Report metrics from **held-out sims** (or a fresh field draw), and compare candidates with common random numbers, i.e. paired differences on the same sims.

### 5.5 Complexity and measured time (12-game NFL slate, 312 players, 4 vCPU)

Stored runs; each "total" includes a second "true world" that production would not build [VERIFIED: L1 `results/*.jsonl`].

| Step | Measured (3 stored runs) |
|---|---|
| Outcome sims, 2 × 10k × 312, copula + lognormal | 0.28 to 0.43 s |
| Field build: 2 fields × 10k (400 noisy-ILP lineups each + vectorized sampler) | 33.2 to 34.3 s (dominated by ILP solves; cache them or cut to 200) |
| Candidates: about 500 ILP solves, persistent CBC | 16.9 to 18.3 s |
| Evaluation: about 500 candidates × 10k field × 9.7k sims, two worlds | 2.5 to 3.5 s |
| Greedy portfolio, 20 lineups | 0.09 to 0.15 s |
| **Total per slate** | **83 to 86 s**; production with one world about 45 to 60 s (estimate) |

- Memory: a 10k × 312 float32 sim matrix is 12.5 MB; a 500-sim × 10k-field chunk of scores is 20 MB [VERIFIED: arithmetic].
- A 100k-entry field (§5.2) multiplies field build and evaluation by about 10×. Pre-build the field once per slate, and vectorize the noisy-ILP part by sampling from a cached pool of 400 to 1,000 optimizer lineups.
- Everything stays within minutes and uses **no LLM tokens**.

---

## 6. Literature summary

| Work | What it reportedly did | Key numbers / findings | Status |
|---|---|---|---|
| Hunter, Vielma & Zaman, arXiv 1604.01455 | Portfolio of lineups for top-heavy DK hockey and MLB; P(at least one entry wins), described as submodular; sequential IPs with a variance floor and an overlap or correlation cap; hockey stacking Types 1 to 5 in Julia/JuMP + GLPK | Code facts: GLPK chosen as "slightly faster in practice" than CBC; 100 lineups and max overlap 7 (hockey); 150 lineups, overlap 6, 5-batter stack (baseball); Type 4 default [VERIFIED: S1, S2, S3]. The authors' READMEs title it **"Picking Winners Using Integer Programming"** and link the **v2** PDF [VERIFIED: S1, S3]. The draft's v1 title ("Winning Daily Fantasy Sports Hockey Contests Using Integer Programming", Apr 2016) and v3 title ("Picking Winners in Daily Fantasy Sports Using Integer Programming", 23 Jan 2019) [UNVERIFIED]. Results ("top ten multiple times", "even able to come in first place"; 200 lineups per hockey contest, Nov 15 to 23 2015; DK cutting max entries to 100; profits to charity) [UNVERIFIED] | code VERIFIED; paper content UNVERIFIED |
| Haugh & Singal, "How to Play Fantasy Sports Strategically (and Win)", *Management Science* 67(1):72–92, 2021, doi 10.1287/mnsc.2019.3528 (SSRN 3393127) | Risk-neutral optimization for double-up and top-heavy payouts; Dirichlet-multinomial opponent model via Dirichlet regression; series of binary quadratic programs; parimutuel-motivated multi-entry; value of insider trading and collusion | 2017 NFL: top-heavy > 350% over 17 weeks vs ≈ 50% benchmark; stakes $50/week at $1/entry (top-heavy) and $20/week at $2/entry (double-up); E(P&L) 58.0, sd 123.8, p(loss) 0.57; top-heavy λ "considerably higher" than double-up | **UNVERIFIED** (all hosts blocked; search budget exhausted). Note the 350% is one season at small stakes |
| Mlčoch & Hubáček, "Competing in daily fantasy sports using generative models", *ITOR* 31(3):1515–1532, 2024 | Generative-model ensemble + MIQP over mean, variance and covariance; EPL | €2,439 profit on €7,133 over 8 weeks (34.2%: arithmetic checks) | **UNVERIFIED** |
| Clair & Letscher, "Optimal Strategies for Sports Betting Pools", *Operations Research* 55(6):1163–1177, 2007 | Opponent-aware pool strategies; contrarian picks in large pools | beats score-maximizing "often by orders of magnitude" | **UNVERIFIED** |
| Getty, Li, Yano, Gao & Hosoi, "Luck and the Law", *SIAM Review* 60(4):869–887, 2018 | Skill-luck metric on FanDuel 2013–14 data | DFS on the skill side; football the most luck-driven DFS sport (press summary) | **UNVERIFIED** |
| Easton & Newell, "Are daily fantasy sports gambling?", *J. Sport Analytics* 5(1):35–43, 2019 | Random lineups in real contests | random lineups lost all 35 contests (2016 NFL) | **UNVERIFIED** |
| McKinsey (Miller & Singer), Sep 2015 | DFS profit concentration, MLB first half 2015 | 91% of player profits won by 1.3% of players; top group paid about 40% of fees; Boston Globe: 85% lost money | **UNVERIFIED** |
| arXiv 2309.15253 (NFL) and 2411.11012 (MLB, Grody) | single-lineup NN / LP pipelines without opponent modelling | MLB author: success against other contestants not yet assessable | **UNVERIFIED** |
| chanzer0 NFL-DFS-Tools (with B. Jungwirth) | optimizer + GPP simulator | method, defaults and fallback matrix as in §5 | **VERIFIED**: S4, S5, S6 |
| pydfs-lineup-optimizer 3.6.1 | ILP optimizer with site rules | 2021 rule encodings; PuLP 3.x API | **VERIFIED**: S7 (stale for FD Single Game) |
| Stokastic, SaberSim, SimSlate, FantasyLabs (vendors) | sims-first GPP tools | vendor descriptions; no independent validation found | **UNVERIFIED** |

---

## 7. Empirical facts about DFS outcomes, and what "top 2.3%" really means

### 7.1 Concentration of winnings [UNVERIFIED]

- McKinsey via the draft: 1.3% of players won 91% of profits in the first half of the 2015 MLB season. Boston Globe: about 85% of players lost money.
- Structural reasons (sound reasoning, not an empirical claim): rake, top-heavy payout curves, and a top of the field dominated by multi-entry.

### 7.2 Winning-score thresholds (DK NFL Millionaire / Milly Maker main slate)

- Relayed from DK Network, DFS Army, SI and NBC [UNVERIFIED]:
  - 2026: W1 273.98 (832,342 entries; $3.5M with $1M to 1st), W2 232.38 (172,761), W3 239.8 (161,764), W4 234.2;
  - season averages: about 236 (low 193.92, high 277), 239 in 2025, 229.14 in 2024;
  - record week 294.38 (2023 W3);
  - NBC: hindsight-optimal is on average about 21 points above the Milly winner.
- **Conflicting evidence on 2025 W16.** The draft says the 2025 W16 Milly was won with **218.2** (132,352 entries).
  - A real DK 2025 W16 main-slate **150-max** GPP standings file shows **rank 1 at 220.62** and **rank 2 at 218.2** (entry `BoangTao33 (95/150)`) [VERIFIED: S15, top 409 rows only].
  - If that file is the Milly Maker, the 218.2 "winner" is wrong. The file does not name the contest, so the contest's identity is [UNVERIFIED].
  - For calibration: the same file's 409th place scored 191.68. 2024 W16's rank 1 was 239.96. [VERIFIED: S15]
- **Cash lines:** no public dataset of DK NFL double-up lines was found. Collect them from our own contest results.

### 7.3 Variance of a single lineup (synthetic)

- Synthetic slates with per-player CV 0.42 (QB) to 0.90 (DST) and the chanzer0 fallback correlations, 10k sims [VERIFIED: L1 `dist_check.py`]:
  - the max-mean DK NFL lineup scored **mean 156 to 162, SD 33 to 35**;
  - QB + 2 stacked lineups had SD 34 to 35.5, i.e. stacking added 0.3 to 2.7 SD points at a 0.5 to 4 point cost in mean;
  - the lineup's own 97.7th percentile was 233 to 244.

  [CORRECTED: draft said mean 163 to 168, SD 35 to 38, stacked SD 38 to 39, 97.7th 246 to 259. Ranges depend on slate seed; qualitative conclusion unchanged]
- **Takeaway:** a real projection edge moves a lineup's mean by a few points, while its SD is about 35. Single-contest outcomes are mostly noise.
- **Top-x% line:** under normality the 97.7th percentile is μ + 1.995σ; top 1% z = 2.326; top 0.1% z = 3.090 [VERIFIED: computed]. Real field scores are right-skewed and the line moves with each sim's chalk, so use simulated quantiles.

### 7.4 Synthetic experiment: how often does a strong lineup finish top 2.3%?

Setup (`sim_gpp.py`; I re-read the code and re-aggregated the stored results) [VERIFIED: L1]:

- **Slate:** 12-game synthetic DK NFL slate, 312 players.
- **Field:** 30% optimizer users and 70% ownership-weighted builders within 20% of optimal projection. Field projection log-error SD 0.22.
- **Contest:** 50,000 entries, $5, 15% rake, 22% paid, min-cash 1.6×, first prize 10.5% of the pool [VERIFIED: `payout_table`].
- **Runs:** 10k true-outcome sims; 6 slates (seeds 201 to 206, the same seeds in every scenario).
- **Scenarios:**
  - Oracle: we know the true means;
  - Edge: our log-error 0.15, correlation 0.5 with the field's errors;
  - No-edge: log-error 0.22, correlation 0.7 with the field's.

P(cash) = P(beat the field's 56.5th percentile), i.e. a **2× double-up paying the top 43.5%**. Break-even is **50%**, the random base 43.5%. Means over 6 slates ± SE across slates.

| Scenario | Selection rule | P(top 2.3%) | P(cash) | Σown |
|---|---|---|---|---|
| Oracle | max mean (= max P(cash) here) | 15.2% ± 1.5 | 76.3% ± 1.4 | 1.87 |
| Oracle | max P(top 2.3%) | 16.8% ± 1.4 | 70.1% ± 2.0 | 1.15 |
| Oracle | max E[payout] | 15.8% ± 1.6 | 62.6% ± 3.0 | 0.66 |
| Edge | max mean | 5.6% ± 1.1 | 56.9% ± 2.8 | 1.93 |
| Edge | max P(top 2.3%) | 6.0% ± 0.5 | 53.0% ± 2.2 | 1.56 |
| Edge | max E[payout] | 6.6% ± 0.4 | 49.6% ± 2.2 | 1.22 |
| Edge | max P(cash) | 5.0% ± 1.1 | 57.9% ± 2.8 | 2.08 |
| No-edge | max mean (= max P(top) here) | 3.4% ± 0.6 | 47.4% ± 3.9 | 1.92 |
| No-edge | max E[payout] | 4.3% ± 0.9 | 45.1% ± 3.1 | 1.56 |
| No-edge | max P(cash) | 2.6% ± 0.6 | 48.4% ± 3.9 | 2.43 |

All values reproduce exactly from `results/*.jsonl` [VERIFIED: L1 `agg.py`].

Further results [VERIFIED: L1]:

- 20-lineup greedy portfolio, P(at least one top-2.3%): oracle 76.5%, edge 60.8%, no-edge 47.1%.
- Best single candidate in hindsight (of about 470 to 500 per slate), true P(top 2.3%): 16.9% / 13.0% / 10.5%.
- Hindsight-optimal averaged 336.9. The best of the 10k field averaged 246.2 (73%). The E[payout]-chosen lineups averaged 156.7 / 144.4 / 139.3 points (47% / 43% / 42% of HOL).
- Field duplicate rate 27%; ownership calibration MAE 0.9 points.
- **ROI** (true world, scenario means): E[payout] beat max-mean (oracle +4,513% vs +1,967%; edge +541% vs +248%; no-edge +267% vs +76%).
  - Per slate it was not "in every case": E[payout] and max-mean picked the same lineup on 2 no-edge slates and 1 edge slate, and E[payout] was worse on one no-edge slate (−0.04 ROI points).
  - Absolute levels are unrealistic (soft synthetic field). [CORRECTED: was "the GPP objective beat max-mean in every scenario", read as uniform]

**Significance (missing from the draft).** Paired differences in true P(top 2.3%) across the 6 slates [VERIFIED: L1]:

- E[payout] − max-mean: oracle +0.5 ± 1.2 pp (p = 0.67), edge +1.0 ± 1.0 (p = 0.35), no-edge +0.9 ± 0.7 (p = 0.25).
- max P(cash) − max-mean: edge −0.5 ± 0.35 (p = 0.19), no-edge −0.8 ± 0.4 (p = 0.13).
- **None is significant with 6 slates.** The direction is consistent with theory, but the experiment does not establish it. Re-run with ≥ 30 slates before quoting effect sizes.

What the experiment shows, qualitatively:

1. The E[payout] objective (with duplicate splitting) chose **lower-owned** lineups: Σown 0.66 to 1.56, against 1.87 to 1.93 for max-mean [VERIFIED: L1].
2. The cash objective chose **chalkier** lineups (Σown 1.87 to 2.43) and gave the best cash rate and the worst top-2.3% rate. One lineup cannot be optimal for both contest types, so the contest type must drive the objective.
3. **Cash results against the correct break-even:**
   - Edge max-P(cash): 57.9% is *above* the 50% double-up break-even, i.e. +15.8% ROI. That is implausibly good, consistent with a soft synthetic field.
   - No-edge: 47 to 48% is *below* the 50% break-even but above the 43.5% base.

   [CORRECTED: was "below the 55.6% double-up break-even"; computed L1]
4. **Why no-edge still beats the 2.3% base:** 70% of the synthetic field are non-optimized, ownership-weighted builders, so an optimizer gains a structural edge even with field-quality information. Real large-field GPPs are optimizer-heavy, so expect less.

These numbers are **illustrative**. The synthetic world is too volatile and the field too soft: HOL averaged about 337 against about 246 for the field's best of 10k, far more than the ~21-point optimal-vs-winner gap NBC reportedly found in real Millys [UNVERIFIED]. The real 2025 W16 rank-1 score was 220.6 [VERIFIED: S15].

Robust conclusions:

1. Per-contest P(top 2.3%) for a single lineup is in the single digits to low teens, even with perfect information.
2. With realistic information it falls to a small multiple of the 2.3% base.
3. Contest selection (field softness) and real information edges dominate optimizer refinements.

### 7.5 Why "top 2.3% in every contest" is impossible, and what to measure instead

- With per-contest hit probability p: P(hit in all n) = pⁿ. p = 0.10 gives 1e-10 over 10 contests; p = 0.20 gives 1.0e-7. P(at least one in 10) = 1 − (1 − p)^10: 40.1% at p = 0.05, 65.1% at p = 0.10 [VERIFIED: computed].
- **Independence:** count **slates**, not contests. One lineup entered in several contests on the same slate gives highly correlated outcomes. Use the slate as the unit, or a cluster-robust test.
- **Binomial power** (one-sided α = 0.05, 80% power, base p₀ = 0.023) [VERIFIED and CORRECTED: computed L1 `stats_check.py`]:

| True rate | × base | Normal approximation (draft) | **Exact binomial (use this)** |
|---|---|---|---|
| 3.0% | 1.3× | 3,107 | (≈ normal at this n) |
| 3.5% | 1.5× | 1,119 | (≈ normal at this n) |
| **4.6%** | **2×** | 339 | **368** (reject if ≥ 14 hits); n = 400 with ≥ 15 hits gives α = 0.046, power 0.82 |
| 6.9% | 3× | 100 | **114** (≥ 6 hits) |
| 10% | 4.3× | 43 | **54** (≥ 4 hits) |

- **Cash rate:**
  - against the **50%** double-up break-even: 53% → 1,716 slates; 55% → 617; 58% → 240; 60% → 153;
  - against the **55.6%** 50/50 or H2H break-even: 58% → 2,544; 60% → 766; 62% → 362.

  [CORRECTED: draft applied 55.6% to double-ups; computed L1]
- **ROI** is very noisy for top-heavy GPPs. With the synthetic payout curve, per-entry payout SD = 23.8× entry (50k entries) and 39.5× (150k). Detecting +10% ROI at 80% power takes ≈ 350,600 / 962,000 entries; +25% takes ≈ 56,100 / 153,900 [VERIFIED: computed L1]. Practitioner "≈ 10% long-run ROI is professional-grade" and "19 to 20% for experienced players" [UNVERIFIED].
- **Mean percentile finish** (a random entrant is Uniform(0,1), SD 0.2887): detecting a 10-point mean improvement takes 52 slates (one-sided) or 65 (two-sided); 5 points takes 206 or 262 [VERIFIED: computed L1]. Use it as the fast feedback metric.

**Statement of realistic targets for the engine:**

- **Per contest:** no guarantee. Report P(top 2.3%) and E[ROI] from **held-out** sims, with honest uncertainty.
- **Over a season, measured per independent slate:**
  - top-2.3% rate ≥ 4.6% over ≥ 370 GPP slates;
  - double-up cash rate ≥ 55% over ≥ 620 slates (or ≥ 58% over ≥ 240);
  - mean percentile finish ≥ 60th;
  - ownership MAE and projection calibration within targets (§8).

---

## 8. Grading: hindsight-optimal lineup vs better process metrics

**Hindsight-optimal lineup (HOL):** after the games, solve the same ILP (same rules, same locks) on actual points.

- **Useful for:**
  - showing which slots lost the most points;
  - checking whether HOL players were in our candidate pool or our top 3 per slot (**coverage**);
  - catching rule or legality errors: **if our score exceeds the HOL, something is wrong**, because either our lineup broke a rule or the HOL solve failed. Equality is fine; it means we hit the optimum. [CORRECTED: was "if HOL ≤ our score, something is wrong"]
  - spotting missed news (an HOL player who was a known late swap).
- **Misleading as a target.** In the synthetic slates our oracle-informed lineup averaged about 47% of HOL, the best of a 10k field about 73%. The gap is mostly irreducible noise.

**Better metrics (compute every contest; aggregate over many slates):**

1. **Projection calibration:**
   - p10–p90 band coverage (about 80% inside, about 10% per tail);
   - PIT histogram (flat);
   - CRPS, or pinball loss at τ ∈ {0.1, 0.5, 0.9};
   - MAE / RMSE of p50 against (a) our baseline and (b) the site's FPPG, by position;
   - within-position Spearman correlation.
2. **Ownership calibration:** MAE and log-loss against actual `%Drafted` (§4.1, column verified), by salary tier.
3. **Decision quality / ex-ante regret.**
   - Re-run the simulation with the **information available at lock**.
   - `regret = E_model[obj(best candidate)] − E_model[obj(chosen)]`, estimated on held-out sims with common random numbers. It should be ≈ 0 within Monte-Carlo error.
   - A re-run with **corrected inputs** (true inactives, late news) separates information failures from modelling failures.
4. **Outcome metrics:** percentile finish, top-x% indicator, cash indicator, payout. Aggregate over independent slates with confidence intervals (§7.5).

**Blame assignment ("name the step that failed").** Decompose `HOL − ours` by slot and classify each slot's loss:

- **(a) Legality or rules error:** the lineup was invalid or a rule was misread. Hard failure.
- **(b) Information error:** the player was ruled out, or news broke before lock, and it was not captured. Blame research or the last-hour step.
- **(c) Projection error outside the band:** the actual fell outside p10–p90 for a player we used, or for an HOL player we faded. Blame the forecast step only if band coverage is miscalibrated over many contests; a single miss is expected 20% of the time.
- **(d) Ownership or game-theory error:** large ownership MAE on the players that decided the outcome.
- **(e) Optimization or selection error:** a better lineup existed **under our own inputs**. With exact ILP (gap 0) and enough sims this should not happen; if it does, it is a bug.

**Updating the blend weights** (the 85/15 NFL forecast/baseline split and the others):

- Fit `w* = argmin_w Σ loss(w·forecast + (1 − w)·baseline, actual)`, w ∈ [0, 1], on out-of-sample CRPS or MAE, with **rolling-origin** validation.
- **Cluster by week** when computing uncertainty: player-games within a slate share game script and weather, so the effective sample size is much smaller than the player-game count.
- **Shrink toward the prior:** `w_new = (n·ŵ + n₀·w_prior)/(n + n₀)`, with n₀ ≈ 300 player-games (a heuristic choice).
- Update only after a few hundred player-games per position group, never from a single contest.
- **Look-ahead leakage:** the models' training data runs to about June 2026. Only games after that (the 2026 NFL season onward) give a valid estimate of the LLM forecast's skill. By 2026-10-21 that is weeks 1 to 6, 93 games, per the sibling projections review.

---

## 9. "Top 3 candidates per roster slot", done in a principled way

For each slot type s (QB, RB, WR, TE, FLEX, DST, CPT, S-FLEX…), rank the eligible players and report the top 3 with all of these numbers:

1. **Optimal %** `π_i`: the share of the K sim-optimal lineups containing i (§5.3), free as a by-product. For FLEX, count only appearances in the FLEX role, after slot assignment.
2. **Lock-delta** `Δ_i = V* − V(lock i)`: the drop in the contest objective when player i is forced in.
   - Under the mean objective, one ILP per player: 312 × ~30 ms ≈ 9 to 11 s.
   - Under the simulation objective, use the best candidate containing i.
   - Δ_i = 0 means i is in the optimal lineup. A small Δ_i means a near-equivalent swap: the close calls the strategist must explain.
   - **With the default 1e-4 MIP gap, Δ values below about 0.02 points are solver noise.** Set the gap to 0.
3. **Leverage** `π_i − own_i` (GPP only).
4. **Exposure in the top-M candidates** by E[payout] (e.g. M = 50).
5. **Value per $1k**, as context only.

Primary sort: lock-delta under the contest objective. Tie-breaks: optimal %, then leverage.

**Strategist step:**

- "Weakest pick" = the chosen player with the smallest **exclusion-delta** `Δ⁻_i = V* − V(x_i = 0)`, i.e. the most replaceable pick. Also flag the worst p10 relative to alternatives.
- The strategist may cut a pick only by citing a fact. The solver then re-solves with that player excluded and reports the objective cost: the price of the cut.

---

## 10. Implications for the engine design

1. **All optimization and game theory is deterministic code, with no LLM tokens.** ILP plus simulation runs in minutes per contest. LLM agents supply facts, p10/p50/p90 forecasts and news flags; code turns them into distributions, correlations, ownership and decisions.
2. **Data contracts (JSON):**
   - `ContestRules` (§2.6, incl. `game_type`, `allow_late_swap`, entity multipliers, row salaries)
   - `PlayerPool[]` {id, entity_of, name, positions[], roster_positions[], team, opp, game, salary, status, locked}
   - `Projection[]` {id, p10, p50, p90, mean, sd, dist: {family, params}, source_weights, as_of}
   - `CorrelationSpec` {pair rules incl. same-position teammates, overrides, copula_param_type: "normal-score"}
   - `OwnershipProjection[]` {id, own, own_cpt?, model_version}
   - `ContestStructure` {N, entry, payout_table[], max_entries, type, paid_frac}
   - `Candidate[]` {players[], slots[], salary, proj_mean, sims: {p_top_x, p_cash, e_payout, e_dups, holdout: {...}}}
   - `Decision` {chosen, alternative_with_user_rules, price_of_rules, top3_per_slot, weakest_pick, rationale_facts[]}
   - `StandingsImport` {rank, entry_id, entry_name, points, lineup_str, player, roster_position?, pct_drafted, fpts}: parse by header (§4.1)
3. **Objective by contest type** (§3.5). The contest type and payout table come from the contest page or user input. If missing, default to the GPP E[payout] objective with a generic top-heavy curve, and flag it.
4. **"Three independent drafts"** are best implemented as three candidate generators (mean-optimal, sim-optimal, perturbed-stacked) feeding one shared simulation evaluation. That avoids having an LLM "draft" lineups the solver finds exactly.
5. **Last-hour re-optimization rule** ("change only if a pick is out or a new lineup leads by > 1%"):
   - Measure the 1% on the contest objective (E[payout] or P(cash)), as a paired difference on common sims, and require the lead to exceed about 2 paired SEs.
   - With 10k independent sims, P = 5% has SE ≈ 0.22 pp; paired differences have smaller SE.
   - Apply late-swap locks (§2.4). DK Showdown allows no late swap, so the last-hour step there can only confirm or withdraw before lock.
6. **Measure honestly.** Log every contest's inputs, candidate set, simulated metrics (held-out) and actual result. Build the §7.5 and §8 metrics per independent slate from day 1.

---

## 11. Open questions

- Whether site terms allow automated download of DK standings and rules objects, or the user must supply them (see `sources_compliance.verified.md`). The standings **format** is now verified (§4.1).
- **Re-verify everything tagged [UNVERIFIED] once the search budget resets** (Haugh–Singal numbers, HVZ results, Milly scores, payout and rake facts, CVs), preferably from the PDFs and contest pages.
- Is the 2025 W16 standings file in S15 the Milly Maker? If so, that week's winning score was 220.62, not 218.2.
- Current FD NBA Single Game slot count, and current DK NHL rules.
- Real payout tables and field sizes for the user's contests, which determine the objective.
- Empirical NFL correlations by position and depth-chart role for the current season, including same-position teammates.
- Field-model calibration against real contests: duplicate rate, optimizer share, salary usage, top-percentile scores.

---

## 12. Sources (URLs)

**Verified today on primary sources** (see the S-key table at the top for exact URLs):

- HVZ code and READMEs: S1, S2, S3
- chanzer0 NFL-DFS-Tools: S4, S5, S6. Expected correlation file paths returned 404: https://raw.githubusercontent.com/chanzer0/NFL-DFS-Tools/main/distribution_data/fp_correlations_dk.yaml
- pydfs-lineup-optimizer: S7; release date https://pypi.org/pypi/pydfs-lineup-optimizer/json
- PuLP 4.1.0: S8; OR-Tools, highspy, SciPy: S9
- Real DK/FD files: S10 (FD Single Game, Oct 2025), S11 (DK NBA Showdown, Jan 2026), S13 (DK CFB, Oct 2022), S14 and S15 (DK contest standings 2017, 2024, 2025)
- DK gametype rules captures: S12
- Local re-runs and computations: L1
- Sibling review: SIB (site_formats.verified.md), for FD current Single Game slot counts, FD NBA current format, DK `(LOCKED)` cells, entry-limit tags and DK MLB rules

**Cited by the draft but not reachable today (all [UNVERIFIED]):**

- Papers:
  - HVZ: https://arxiv.org/abs/1604.01455 (v1, v3), https://mitsloan.mit.edu/shared/ods/documents?DocumentID=2858, https://juan-pablo-vielma.github.io/publications/Picking-Winners.pdf
  - Haugh & Singal: https://pubsonline.informs.org/doi/10.1287/mnsc.2019.3528, https://ideas.repec.org/a/inm/ormnsc/v67y2021i1p72-92.html, https://papers.ssrn.com/abstract=3393127, http://www.columbia.edu/~mh2078/DFS_Revision_1_May2019.pdf, https://spiral.imperial.ac.uk/server/api/core/bitstreams/86bdc149-69a5-46d5-b086-632ad5cf4054/content, https://imperial.ac.uk/business-school/ib-knowledge/strategy-leadership/how-play-fantasy-sports-strategically-and-win
  - Mlčoch & Hubáček: https://onlinelibrary.wiley.com/doi/10.1111/itor.13344
  - Clair & Letscher: https://pubsonline.informs.org/doi/10.1287/opre.1070.0448, https://cs.slu.edu/~clair/pools
  - Getty et al.: https://dspace.mit.edu/handle/1721.1/119466, https://www.thescore.com/news/1846139
  - Easton & Newell (coverage): https://www.legalsportsreport.com/20030/daily-fantasy-contests-chance-vs-skill/
  - arXiv: https://arxiv.org/abs/2309.15253, https://arxiv.org/pdf/2411.11012
- Industry:
  - McKinsey: http://www.mckinsey.com/industries/media-and-entertainment/our-insights/for-daily-fantasy-sports-operators-the-curse-of-too-much-skill
  - Boston Globe: https://www.bostonglobe.com/business/2015/11/29/daily-fantasy-sports-companies-offer-conflicting-messages-fans-courts/vybJqhZXxIMGXdAPZJvStL/story.html
  - DK Network Milly pages, 2026 W1 to W4 and 2025 W16: https://dknetwork.draftkings.com/2026/09/14/draftkings-nfl-millionaire-winning-lineup-week-1/ and siblings
  - DFS Army, SI and NBC Milly reviews: https://www.dfsarmy.com/2026/01/nfl-dfs-week-18-draftkings-milly-maker-review, https://www.si.com/onsi/fantasy/dfs/draftkings-millionaire-maker-winning-dfs-stacks-lessons-past-champions, https://www.nbcsports.com/fantasy/football/news/week-5-2024-dfs-draftkings-milly-maker-breakdown
  - Payouts and rake: https://www.topendsports.com/en-us/sports-betting/dfs/draftkings-dfs/, https://rotogrinders.com/articles/nfl-dfs-tournament-strategy-basics-4051877, https://www.legalsportsreport.com/15721/draftkings-fanduel-rake-increases/, https://rotogrinders.com/articles/bang-for-your-buck-a-look-at-dfs-industry-rake-153302, https://www.rotowire.com/football/article/dfs-football-101-draftkings-cash-game-breakdown-35702
  - Cash and ROI benchmarks: https://www.dailyfantasysports101.com/5050-strategy-for-draftkings-7-rules-to-win/, https://dfsdegen.com/blog/dfs-roi-math-hourly-rate
  - CVs: https://underdognetwork.com/football/best-ball-research/weekly-variance-by-position-a-key-to-best-ball, https://www.playerprofiler.com/article/the-player-variance-manifesto/
  - Vendors: https://support.sabersim.com/en/articles/12078831-how-projections-work, https://www.stokastic.com/articles/nfl-dfs/nfl-dfs-leverage-game-theory, https://www.4for4.com/fantasy-football/gpp-leverage-scores-balancing-value-ownership-dfs, https://simslate.com/, https://fantasyprojectionlab.com/daily-fantasy-sports-projections/
  - ETR: https://establishtherun.com/?p=335655
  - Showdown and FD guides: https://www.4for4.com/2018/w7/win-draftkings-showdowns, https://www.thefantasyfootballers.com/dfs/dfs-101-beginners-guide-fanduel

---

## Appendix A. Claim-by-claim verification log

| # | Draft claim (short) | Verdict | Evidence |
|---|---|---|---|
| 1 | HVZ arXiv 1604.01455 titles v1/v3 | UNVERIFIED (README gives v2 as "Picking Winners Using Integer Programming") | S1, S3 |
| 2 | HVZ submodular P(≥1 win), variance floor, correlation cap | UNVERIFIED (paper); code has overlap cap only | S2 |
| 3 | HVZ top-ten finishes, first place | UNVERIFIED | — |
| 4 | HVZ 200 lineups; DK cut to 100; charity | UNVERIFIED | — |
| 5 | HVZ code: GLPK, 100 lineups, overlap 7, Type 4, constraint list | VERIFIED (Types are no-stacking + 1–5, not 1–4) | S1, S2 |
| 6 | Haugh–Singal citation | UNVERIFIED | — |
| 7 | Haugh–Singal methods | UNVERIFIED | — |
| 8 | Haugh–Singal 350% vs 50% | UNVERIFIED (one season, small stakes) | — |
| 9 | Haugh–Singal stakes and Table 1 | UNVERIFIED | — |
| 10 | Haugh–Singal λ | UNVERIFIED | — |
| 11 | Mlčoch & Hubáček €2,439 / €7,133 | UNVERIFIED (34.2% arithmetic OK) | — |
| 12 | Clair & Letscher | UNVERIFIED | — |
| 13 | Getty et al. | UNVERIFIED | — |
| 14 | Easton & Newell 0/35 | UNVERIFIED | — |
| 15 | McKinsey 91% / 1.3% | UNVERIFIED | — |
| 16 | Boston Globe 85% | UNVERIFIED | — |
| 17 | DK Milly 2026 W1–W4, 2025 W16 scores | UNVERIFIED; **2025 W16 conflicting evidence** (a 150-max main-slate GPP had 220.62 first, 218.2 second) | S15 |
| 18 | Milly season averages, record | UNVERIFIED | — |
| 19 | NBC optimal ≈ winner + 21 | UNVERIFIED | — |
| 20 | GPP % paid, first-prize share, min-cash | UNVERIFIED | — |
| 21 | Rake figures; $25 DU 14.9% rake / 43.5% paid; H2H $18 | UNVERIFIED; **14.9%/43.5% internally inconsistent** for 2× (implies 13.0%) | algebra |
| 22 | Cash win-rate benchmarks | UNVERIFIED; break-even conflated (corrected) | algebra |
| 23 | ROI benchmarks | UNVERIFIED | — |
| 24 | Positional CVs | UNVERIFIED | — |
| 25 | chanzer0 correlation values | VERIFIED (values); **fallback-only and diagonal 0.95 caveat added** | S5 |
| 26 | chanzer0 simulation method | VERIFIED | S4, S5 |
| 27 | chanzer0 config defaults | VERIFIED | S5, S6 |
| 28 | SaberSim ownership definition | UNVERIFIED | — |
| 29 | Stokastic optimal % / leverage | UNVERIFIED | — |
| 30 | 4for4 leverage | UNVERIFIED | — |
| 31 | SimSlate | UNVERIFIED | — |
| 32 | ETR dupes | UNVERIFIED | — |
| 33 | No vendor accuracy benchmarks | UNVERIFIED | — |
| 34 | Projection–ownership coupling | UNVERIFIED | — |
| 35 | pydfs settings (DK NFL/NBA/NHL, FD NFL) | VERIFIED as 2021 code | S7 |
| 36 | DK Showdown CPT 1.5×, cap, both teams | VERIFIED (and CPT row salary already 1.5×) | S11, S12 |
| 37 | FD single-game MVP + 4 UTIL; multiplier UNCONFIRMED | **CORRECTED**: legacy layout; current MVP 1.5× points **and** 1.5× salary | S7, S10, SIB |
| 38 | FD NFL $60k, ≤ 4/team, ≥ 3 teams | VERIFIED as code | S7, SIB |
| 39 | PuLP 4.1.0 API break; CBC unbundled; default HiGHS | VERIFIED except **default solver CORRECTED** (CBC → GLPK → none) | S8, L1 |
| 40 | highspy + OR-Tools conflict; SciPy coexists | VERIFIED (ImportError in both orders) | L1 |
| 41 | NFL ILP solve times | VERIFIED approximately (re-run within run-to-run noise) | L1 |
| 42 | 150 sequential lineups times | VERIFIED approximately (re-run 18.4 / 25.6 / 64.0 s) | L1 |
| 43 | NBA Hall vs slot times | VERIFIED; speed-up **2–6×** not 3–6× | L1 |
| 44 | Persistent CBC 27–36 ms | VERIFIED approximately (30–37 ms in stored runs) | L1 |
| 45 | Full pipeline timing 80–86 s | VERIFIED (83–86 s) | L1 |
| 46 | Synthetic P(top 2.3%) table | VERIFIED (exact reproduction); **differences not significant** | L1 |
| 47 | 20-lineup portfolio, best-in-hindsight | VERIFIED | L1 |
| 48 | Synthetic cash results / Σown | VERIFIED numbers; **break-even interpretation corrected** | L1 |
| 49 | Hindsight 337 / 246 / 47%; dup 27%; ROI ordering "all scenarios" | VERIFIED numbers; "all" true for scenario means only | L1 |
| 50 | Lineup score distribution | **CORRECTED** ranges (seed-dependent) | L1 |
| 51 | Normal quantiles | VERIFIED | L1 |
| 52 | Binomial sample sizes | VERIFIED as normal approximation; **exact binomial larger for small p** | L1 |
| 53 | Double-up 1.8× break-even 55.6%; cash sample sizes | Arithmetic VERIFIED for 1.8×; **double-up is 2× / 50%** | L1 |
| 54 | Payout SD and ROI detection | VERIFIED | L1 |
| 55 | Mean percentile detection | VERIFIED (one-sided) | L1 |
| 56 | pⁿ and P(≥ 1 in 10) | VERIFIED | L1 |
| 57 | Nemhauser–Wolsey–Fisher 1 − 1/e | UNVERIFIED (textbook result; guarantee is over the sample objective and candidate set) | — |
| 58 | arXiv 2309.15253 / 2411.11012 | UNVERIFIED | — |
| 59 | DK GPPs allow 150 entries | VERIFIED (150-max DK NFL main-slate GPP standings) | S15, SIB |
| 60 | DK standings %Drafted column | VERIFIED; column list given; 2024-25 adds `Roster Position` | S14, S15 |
