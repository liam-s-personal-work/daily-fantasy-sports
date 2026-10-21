# Critical review of the draft DFS pipeline, plus a revised pipeline

Key: `pipeline_critique` · Written 2026-10-21 · Repo: `liam-clifford/daily-fantasy-sports`
Perspective: a DFS professional, a quantitative modeller and a software architect reviewing the user's 13-step
draft (quoted in §4). The aim is a design that can be coded. Every issue has a severity, a reason and a fix.

---

## 0. How this review was done, and its limits

- **Inputs**
  - The user's draft pipeline, verbatim.
  - The sibling research notes in this scratchpad: `scoring.md`, `site_formats.md`, `sources_compliance.md`,
    `projections.md` and `llm_orchestration.md`. These are cited as [sib:file §x].
  - The repo as it stands today (commit `74b4953` plus uncommitted working-tree files, read locally).
  - Real DraftKings entries exports in `data/cache/real_samples/`, inspected with Python today.
- **Web access was limited.**
  - WebSearch: the shared 200-call budget for this workflow turn was already used up, so no search ran.
  - WebFetch: it reached `platform.claude.com` and `github.com`. It could not resolve `arxiv.org`,
    `ideas.repec.org` or `en.wikipedia.org` (DNS failure).
  - Confirmed live today:
    - the Claude models overview (cutoffs, prices, latency);
    - the Opus 5.5 migration guide (sampling parameters);
    - the README of the Hunter–Vielma–Zaman code repository.
  - Everything else is relayed from sibling notes. Where I could not re-check a claim myself, it carries
    one confidence level lower than the sibling gave it (high becomes medium).
- **Derived numbers.** These come from
  `/tmp/claude-0/-home-user-daily-fantasy-sports/7b39a0e6-5812-5e7e-9a48-82f39febae8b/scratchpad/calc/critique_calcs.py`
  (seeded, run today).
  - Numbers from the toy model show orders of magnitude only. They are **not** empirical claims about
    real slates.
  - The toy model is a DK-NFL-like Classic: 24 teams, 8 players per team, $50,000 cap. Outcome noise uses
    coefficients of variation (CV) by position: QB 0.41, RB 0.63, WR 0.61, TE 0.70, DST 0.80. Salaries are
    set from a noisy market view.
- **Confidence tags**
  - **[H]**: confirmed today on a primary source, or by inspecting the file or code myself.
  - **[M]**: a reputable secondary source, or relayed from sibling research.
  - **[L]**: recollection.
  - **[D]**: derived in this session (mathematics or the toy simulation).
  - **UNCONFIRMED**: could not be confirmed. Do not build on it without checking.

Severity scale:

| Severity | Meaning |
|---|---|
| **blocker** | The step can't be implemented as written. Or, if it is, it will produce wrong or zero-point lineups, or break one of the user's hard constraints (compliance, 60 minutes). |
| **major** | It materially lowers expected results, reliability or cost efficiency. |
| **minor** | Clarity, cost, polish or maintainability. |

---

## 1. Executive summary

1. **The goal as stated can't be met, so restate it, or the grading loop will chase noise (blocker).**
   - "Top 2.3%" is the two-sigma tail: 1 − Φ(2) = 0.02275 [D].
   - Suppose the system had a 10% top-2.3% rate per contest. That is 4.3× chance, which would be elite. It
     would still finish top 2.3% in all of 10 independent contests with probability 10⁻¹⁰ [D].
   - Just *demonstrating* a 5% rate against the 2.3% chance rate takes about 254 independent slates, which
     is about 14 NFL seasons of main slates [D].
   - Restated target:
     - Maximise expected payout in each contest.
     - For GPPs, maximise P(lineup score ≥ the field's 97.7th-percentile score). That puts the user's 2.3%
       *into the solver's objective* instead of leaving it as a promise.
     - Measure progress with dense metrics: CRPS and pinball loss per player, plus mean percentile finish.

2. **The pipeline optimises a single mean-maximising lineup (cash-game logic) and is aimed at GPP-style
   results (blocker).**
   - A top-2.3% finish needs distributions, correlation (stacks), an ownership and field model, an
     objective that depends on contest type, duplication avoidance, late swap, and multi-entry portfolios.
     The draft has none of these.
   - The low/likely/high triples are computed and then never used by the solver.

3. **"Who owns the numbers" is contradictory (blocker).**
   - The strategist "can't change a number", but the backup decider "may adjust numbers".
   - The checker's "revisions" don't say what gets revised.
   - A cut is itself the most extreme number change possible (−∞).
   - Fix: one owner of numbers, the projection layer. Downstream agents may only:
     - (a) exclude a player on a verified *hard* fact (out, inactive or suspended), or
     - (b) inject a *new* fact, which triggers a bounded re-forecast of that game.
   - The backup's bounded adjustments produce an *alternative* lineup. It ships only if the main path
     fails, and both lineups are graded.

4. **"Three independent drafts" are redundant or harmful (major).**
   - A deterministic solver on the same numbers returns the same lineup three times. I checked this [D].
   - LLM-built drafts aren't reliably legal or optimal.
   - Replace them with solver candidates under three distinct *theses* (mean, ceiling/stack, leverage),
     plus a pool of simulated optimal lineups. The pool directly yields the user's "top 3 per legal slot".

5. **The blinding is right for DFS prices, but as written college football never sees the market at all
   (major).**
   - The CFB baseline has no betting lines, the forecaster is blinded, and CFB is 100% forecast.
   - In the NFL the market enters only through the 15% baseline weight. Betting markets and props are the
     strongest single input [sib:projections §0].
   - Fix: keep the forecaster blind. Re-introduce the market *in code* in two ways: through the NFL
     baseline, and through a team-total reconciliation for all football whose strength is learned.

6. **The fixed blend weights are untested priors (major).**
   - For 85/15 to be optimal, the baseline's error SD must be 1.07–2.38× the forecaster's, depending on how
     correlated the two sets of errors are [D].
   - For 100/0 to be optimal, σ_L/σ_B ≤ ρ must hold [D].
   - Learn the weights per position with shrinkage, using only data from after the June 2026 cutoff, and
     test changes with a week-clustered method.

7. **Double counting (major).**
   - Suppose the strategist or checker cuts a player by citing a fact the forecaster already priced in.
     The same information then counts twice.
   - Code must check that a fact is *new* (not in the forecaster's packet, or published after the
     forecast). The only exception is hard availability.

8. **"Last hour" is undefined on slates with several lock times (blocker for the NFL).**
   - NFL inactives come out about 90 minutes before *each* kickoff [M].
   - DK Classic allows late swap player by player. DK Showdown locks as a whole [M].
   - So run one pass per kickoff window, on unlocked slots only. Keep late-game flexibility (put late-game
     players in FLEX). FanDuel files carry no kickoff times, so a schedule source is needed [M].

9. **Grading against the hindsight-best lineup mostly measures luck (major).**
   - In the toy model, a system that knows every player's *true mean* still reaches only 55.5% of the
     hindsight optimum on average (SD 9.2 points of percentage) [D].
   - Detecting a projection system that is 20% better needs about 1,000 slates if judged by lineup score
     or efficiency, but about 7 slates if judged by player-level MAE [D, toy, optimistic because outcomes
     there are independent].
   - So grade each layer with proper scores, attribute failures with counterfactual layer swaps, and change
     the pipeline only after a replay with a confidence interval.

10. **Operations (major).**
    - Sixty minutes is achievable: about 22–25 minutes for a typical main run [M, sib:llm_orchestration
      §4.9]. That needs:
      - a **safety lineup by about minute 5**,
      - hard timeouts per stage,
      - one LLM call per game, not per player,
      - code-built dossiers, research only for gaps, and news triage per team rather than per pick.
    - Reproducibility can't come from sampling settings. Opus 5.5 rejects any non-default `temperature`,
      and the docs say temperature 0 "never guaranteed identical outputs" [H]. Use frozen artifacts,
      seeds and pinned model IDs.
    - Compliance: never automate DK or FD. Write a file the user uploads, and enforce the source allowlist
      in code.

---

## 2. What the draft gets right (keep these)

| Element | Why it is right |
|---|---|
| Read slots, IDs and existing entries from the site's own entries file | The DK entries export carries the slot header, entry and contest IDs, fees, the embedded player pool and ` (LOCKED)` markers [H, local inspection; sib:site_formats §2, §5]. |
| Separate fact gathering from forecasting | LLMs add the most on qualitative, fresh information: roles, injuries, scheme, weather. They add the least on arithmetic extrapolation [M, sib:llm_orchestration §2.6]. |
| Blind the forecaster to DFS prices and site averages | LLMs anchor on numbers placed in the prompt [M, sib:projections §0 (Huang et al. 2025)]. Independent signals combine better than sequential exposure [M/L, Schoenegger 2024; Halawi 2024]. |
| Blend with a statistical baseline | Combining forecasts beats single sources, and simple averages are hard to beat [M, Smith & Wallis 2009]. |
| "Cut only by citing a fact" and "code decides pass/fail" | These make the stages auditable and stop narrative-driven churn. |
| Hysteresis on the last-hour switch | Prevents flip-flopping on noise. |
| User rules solved as a second lineup | Keeps the model lineup unbiased and reveals what the rule costs. |
| A post-contest grading loop | Required for any learning, if it is designed against variance (§4.13). |
| "Only sites that allow automated access" | Matches the compliance research. DK and FD are file-in, file-out only [M, sib:sources_compliance §9]. |

---

## 3. The goal: "top 2.3% of every single contest"

### 3.1 The arithmetic [D]

The probability of finishing top 2.3% in *all* N independent contests, given a per-contest hit rate p:

| p | N=1 | N=5 | N=10 | N=17 (one NFL season of Sundays) |
|---|---|---|---|---|
| 0.023 (chance) | 2.3e-2 | 6.4e-9 | 4.1e-17 | 1.4e-28 |
| 0.05 (2.2× chance) | 5.0e-2 | 3.1e-7 | 9.8e-14 | 7.6e-23 |
| 0.10 (4.3×) | 1.0e-1 | 1.0e-5 | 1.0e-10 | 1.0e-17 |
| 0.20 (8.7×) | 2.0e-1 | 3.2e-4 | 1.0e-7 | 1.3e-12 |

Independent slates needed to show a hit rate p1 above the 2.3% chance rate (one-sided α = 0.05, power 0.8,
normal approximation):

| p1 | 0.035 | 0.05 | 0.08 | 0.10 |
|---|---|---|---|---|
| slates | 1,118 | 254 | 69 | 42 |

Entries in the same slate share players, so they are **not** independent. The unit of evidence is the
slate, not the entry.

### 3.2 Why that matters for the design

- If "top 2.3% every time" is the pass criterion, nearly every contest "fails". Then "name the step that
  failed, fix it" fires after every normal loss. That is a recipe for overfitting and prompt drift (§4.13).
- Projection accuracy has a hard ceiling. The best public weekly NFL projections have MAE of about 6.2 (QB),
  5.2 (RB) and 4.9 (WR) [M, FFA via sib:projections §1]. Weekly CV is about 41% for QBs and 61–63% for RBs
  and WRs [M, 4for4]. No system is "hyper-accurate". The edge comes from slightly better projections,
  calibrated tails and correlations, and roster construction against the field.
- In cash contests (50/50s, double-ups, head-to-heads), a top-2.3% finish pays nothing extra. The target
  only means something for tournaments (GPPs).

### 3.3 Restated, measurable targets (recommended)

| Level | Metric | Target |
|---|---|---|
| Per contest (decision) | GPP: EV = E[payout(rank)] − fee, simulated against a field model. Proxy: P(S ≥ T₀.₉₇₇(field)). Cash: P(S ≥ cash line) | The solver's objective (§6.5) |
| Projections (weekly) | MAE, CRPS, pinball at τ ∈ {0.1, 0.5, 0.9}, and 80% interval coverage, per position and source | Beat our own baseline, and a consensus benchmark if one is licensed. The 3–5% MAE gain sib:projections suggests as "state of the art" is UNCONFIRMED |
| Lineups (rolling) | Mean percentile finish from the site's standings export. Realised vs *predicted* P(top 2.3%), which checks the system's calibration about itself. ROI with a week-clustered bootstrap CI | Report the CI. Never claim a guarantee |
| Process | Share of runs finished in under 60 min. Dollars per slate. Share of lineups with an inactive player | 100%, under the cap, 0% |

---

## 4. Step-by-step review

Each step gives the user's text, the most plausible reading, then the issues.

### 4.1 "Read the contest: roster slots, salary cap and positions, from the entries file and the site's saved rules."

Reading: the entries file supplies slots and pool, and "saved rules" means a stored copy of the site's rules.

| ID | Sev | Issue | Why it matters | Fix |
|---|---|---|---|---|
| C1 | **blocker** | No DK or FD CSV contains the **salary cap**, team limits, minimum games, CPT/MVP point multipliers, or late-swap flags [M/H, sib:site_formats §6]. The site's rules pages can't be fetched (DNS-blocked here), and automated access to DK/FD is reportedly prohibited [L, UNCONFIRMED] | The solver can't run without a cap. A wrong cap or team limit produces an illegal or sub-optimal lineup | A **versioned local rules registry** (YAML) keyed by site, sport and game type, with provenance and `verified_at`. At runtime, assert file against registry (slot header, salary range, positions). The user confirms each game type once per season, or whenever an assertion fails. Never guess a cap: stop and ask. The repo already has `dfs/rules/registry/contests.yaml` [H, local] |
| C2 | **blocker** | Contest type, field size, payout table and entry limit are not in the file. Only the contest *name* hints at them (`[Single Entry]`, `[150 Entry Max]`, "Double Up") [H, local samples; sib:site_formats §5] | The objective depends on contest type (§6.5). Without it, the only defensible objective is mean-max, which is wrong for GPPs | Per-contest metadata: parse the name for hints, the user confirms the type, and optionally pastes the payout table. Default: classify unknown contests as GPP *and* warn |
| C3 | major | **One DK entries file can hold several contests.** Seen today: a Showdown file with two different single-entry contests, and a late-swap file with two contests [H, local `DKEntriesShowdown.csv`, `DKEntriesClassicLateSwap.csv`]. DK's own instructions say "Column A lists all of your contest entries for this draftgroup" [H via sib:sources_compliance §7] | A double-up row and a GPP row in the same file need different lineups | Solve per contest group, using each contest's objective. Write a lineup per entry row, and never more rows than the file has |
| C4 | minor | Format traps: BOM, DK DST trailing space, FD empty header columns, `FPPG` blank vs 0, IDs that change per draft group [M/H, sib:site_formats §9] | Silent mis-parses | Parser tests against real exports. Already started: 48 tests at commit 74b4953 [H, local] |
| C5 | minor | FD player lists carry **no kickoff times** [M, sib:site_formats §5] | Lock-aware late swap can't run on FD without a schedule | Join with an allowlisted schedule source (nflverse schedules, already cached locally [H]) |

### 4.2 "Learn the scoring."

| ID | Sev | Issue | Why it matters | Fix |
|---|---|---|---|---|
| SC1 | major | "Learn" must not mean an LLM inferring scoring at run time | A misread bonus biases every player at a position systematically. DK's 300+ passing-yard bonus of +3 is one example [M, sib:scoring §1.1] | Versioned scoring tables (YAML) with worked-example unit tests. An LLM (Haiku) may only *diff* pasted rules text against the table and flag differences for the user. Already in the repo [H, local] |
| SC2 | major | Forecasts in fantasy points tie the forecast to one site, and leave the bonus arithmetic to the LLM | LLMs are weak at bonus-threshold arithmetic. DK and FD score differently (DK full PPR, FD half PPR [M, sib:site_formats W20]) | The forecaster outputs **stat components** (volume, efficiency, TD rate as quantiles). Code simulates the stat lines, then scores them for DK and FD [M, sib:llm_orchestration §2.6] |
| SC3 | minor | Captain/MVP multipliers and salary rules differ (FD MVP salary has been 1.5× since 2025 [M, sib:site_formats W2–W4]) | Wrong Showdown values | Normalise to `(athlete, slot_role, salary, points_multiplier)` [sib:site_formats §9] |

### 4.3 "Research every game: who's playing, their role, injuries and matchups. Only use sites that allow automated access."

| ID | Sev | Issue | Why it matters | Fix |
|---|---|---|---|---|
| R1 | major | "Every angle, every narrative, every detail" (from the user's request) has no bound | Search results are re-read as input on every later turn. Research and search tokens are the main cost drivers [H for pricing mechanics; M, sib:llm_orchestration §4.4, §4.10]. Narratives add little after usage and market data | **Code builds the dossier first** from structured adapters: schedule, injuries and practice participation, depth charts, usage (snaps, targets, carries), weather, and market lines for the baseline. The LLM researches only **gaps**: role changes, coach quotes, game-time decisions, extreme weather and new signings. One call per game, with `max_uses` capped (about 6 searches and 3 fetches) |
| R2 | **blocker (go-live)** | "Only sites that allow automated access" isn't enforceable by prompt | It is a hard constraint from the user | Enforce it in code with a domain allowlist (status ALLOWED, or CONDITIONAL with conditions met) [M, sib:sources_compliance §1, §5]. Our own adapters check `robots.txt`. Server tools get `allowed_domains`, and Anthropic web fetch returns `url_not_allowed` for robots.txt restrictions [M, sib:llm_orchestration §4.4]. DK and FD are never fetched. The ToS reads are still open (UNCONFIRMED) |
| R3 | major | No fact contract | Without timestamps and sources, the later stages can't verify cuts, detect stale news, or apply the blinding | Every fact carries: `id, subject_id, team, kind ∈ {status, injury, role, depth_chart, usage, coaching, weather, matchup}, text, source_url, published_at, retrieved_at, confidence`. Code drops facts after the lock or the run's `as_of`, and sanitises price-bearing text (§4.5) |
| R4 | major | Player identity across DK, FD, nflverse and news (suffixes, nicknames, DST names, two players with the same name) | One mismatch silently attaches the wrong player's facts or usage | ID crosswalk plus an override table. **Hard-fail** if any player who could enter a lineup is unmatched. Never join on display name alone [sib:site_formats §9.5] |
| R5 | minor | LLM narrative facts are where hallucinations hide | A fabricated "limited snaps" note can sink a good pick | Accept a fact only if its URL is on the allowlist, it was retrieved this run, and a quoted span is present. Low-confidence facts can't drive cuts. The current code already rejects `confidence == "low"` [H, local `_verified`] |

### 4.4 "Baseline numbers. NFL: game logs adjusted by betting lines. College: season averages adjusted for recent workload. Remove anyone ruled out."

| ID | Sev | Issue | Why it matters | Fix |
|---|---|---|---|---|
| BL1 | major | "Game logs adjusted by betting lines" is undefined. Averaging fantasy-point logs mixes role changes, game script and touchdown luck | Shares are sticky and efficiency is not: WR target share year to year r ≈ 0.70, yards per target R² ≈ 0.05 [M, sib:projections §0]. Points-based logs overreact | Top-down: **market-implied team points**, then plays and pass rate, then player shares (lightly shrunk), then efficiency (heavily shrunk), then simulated stat lines, then site points. Implied team total = (total − spread_team)/2, with spread_team negative for the favourite [D] |
| BL2 | major | The CFB baseline has **no market input**, and at a 100% forecast weight it is computed but never used except as a fallback | The CFB projection carries no market signal at all (see F2) | Make the CFB baseline market-aware: team implied totals × tempo × shares. Use it as the fallback, as a shadow model, and in the blend once validated |
| BL3 | major | "Remove anyone ruled out" is binary and happens too early | Questionable players need P(active). Inactives arrive about 90 min before each kickoff [M], long after the baseline runs. Removing a player without redistributing his volume under-projects his teammates | An **availability model**: hard removal only for official Out, IR, Suspended or Inactive (code). Otherwise p_active by status × practice pattern. That rate is UNCONFIRMED post-2016, so estimate it from historical injury reports joined to snaps [sib:projections §2.8]. Code redistributes the vacated targets and carries |
| BL4 | major | "In other sports the baseline leads", but no baseline is defined for NBA, NHL, MLB, PGA, MMA or NASCAR | The user wants "any sporting event", and the pipeline has nothing for it | v1 scope is NFL and CFB (Classic and Showdown). For any other sport, a **generic fallback**: the site's own `AvgPointsPerGame` / `FPPG` (in every DK/FD file [H, sib:site_formats §1, §3]), shrunk toward the position mean, with injury filtering. Label it "low quality", then add sport recipes [sib:projections §8] one at a time |

### 4.5 "Fresh forecasts (football only): a separate Claude sees the facts but no prices, site averages or past scores, and gives each player a low, likely and high."

| ID | Sev | Issue | Why it matters | Fix |
|---|---|---|---|---|
| F1 | **blocker** | The blinding scope is ambiguous. Does "prices" mean DFS salaries, or betting lines and props too? Does "past scores" mean fantasy points, raw stats or usage? | It decides what the forecaster sees, and therefore how independent and how good it is | A field-level schema, enforced by a sanitizer in code. **Allowed**: roles, snaps, routes, targets, carries, depth chart, injuries and practice, weather, scheme, opponent defensive metrics. **Forbidden**: salary, DFS projections, ownership, `FPPG`/`AvgPointsPerGame`, past *fantasy* points, and by default betting lines and props (see F2). Free text is scrubbed for `$\d`, "salary", "implied", moneyline patterns and similar [sib:llm_orchestration §2.4] |
| F2 | major | Hiding the market throws away the strongest single input. Props are "the strongest single input where they exist" [M, sib:projections §0]. In NFL the market then flows only through the 15% baseline weight. In CFB it flows nowhere | Expected accuracy loss, and a systematic one in CFB | Keep the forecaster blind, which preserves its independence and the user's intent. Put the market back **in code** at two points: (a) the NFL baseline, and (b) a **team-total reconciliation** for all football (§6.4, R-rule) with learned strength κ (κ = 0 reproduces the user's spec exactly). The siblings disagree here (sib:projections: show the market; sib:llm_orchestration: blind it). Settle it with a shadow arm, a market-aware forecaster on a sample of games, graded by CRPS |
| F3 | major | "Low, likely, high" has no semantics, and LLM intervals run narrow. FermiEval: nominal 99% intervals cover about 65%. QuantSightBench: no model reached 90% coverage on 90% intervals, best 79.1% [M, sib:projections §0] | For skewed fantasy distributions, mode, median and mean differ by points. Narrow tails break every GPP objective | Define low = p10, likely = **p50 (median)**, high = p90, all conditional on playing, with p_active separate. Compute the mean in code. **Calibrate**: at cold start use the LLM median plus the baseline's empirical residual widths per position. When warm, fit per-position scale factors on graded data [sib:llm_orchestration §2.3]. The repo widens intervals with conformal offsets [H, local `blend.calibrate`] |
| F4 | major | Forecasting each player in isolation gives incoherent team totals | Targets can sum to more than 100%, which inflates whole offences | **One call per game**, with team volume and pass rate first, then shares. Code enforces Σ shares ≤ 1 and rescales |
| F5 | major | Look-ahead leakage: the models' training data runs to June 2026 [H, models overview] | Any backtest on earlier games (all of the 2025 NFL season) is contaminated for LLM stages | Evaluate LLM stages only on games from July 2026 on. Log frozen, prospective artifacts. Use a canary test before using a game [sib:llm_orchestration §2.5] |
| F6 | minor | A "separate Claude" with the same model gives little error diversity | Ensembles help mostly when errors are independent | Optionally add a second forecaster on a different model (Sonnet 5.5). Estimated at about +$1.5 per NFL slate [M, sib:llm_orchestration §4.10]. Average the quantiles |
| F7 | minor | "Football only" conflicts with "any sporting event" | Expectations | Other sports: the LLM supplies facts and availability only. Run forecasts in shadow to gather evidence before they get any weight |

### 4.6 "Blend the two: NFL is 85% forecast, 15% baseline. College is 100% forecast. In other sports the baseline leads."

| ID | Sev | Issue | Why it matters | Fix |
|---|---|---|---|---|
| BD1 | major | The fixed weights have no validation. The Bates–Granger optimum is w* = (σ_B² − ρσ_Lσ_B)/(σ_L² + σ_B² − 2ρσ_Lσ_B). For 85/15, σ_B/σ_L must be 2.38, 1.48, 1.16 or 1.07 at ρ = 0, 0.5, 0.8 or 0.9. For 100/0, σ_L/σ_B ≤ ρ [D; matches sib:projections §7.2] | Public projection sources sit within a few percent of each other, so a forecaster with 7–58% lower error is not plausible a priori | Treat the user's weights as **priors w0**. Learn per position and per "has props" stratum: w_g = clip((Σ(f−b)(y−b) + λ·w0)/(Σ(f−b)² + λ), 0, 1), with λ = N0·mean((f−b)²) and N0 ≈ 200. This is the encompassing regression with shrinkage [sib:llm_orchestration §2.6]. Report β̂ with a CI. If the CI includes 0, the forecaster adds nothing for that group |
| BD2 | major | "Blend" doesn't say *what* is blended | Blending means only discards the distribution | Average the **calibrated quantiles** (Vincentization [M, Lichtendahl et al. 2013]). Average p_active separately. Simulate from the result. The repo already does quantile averaging [H, local `blend.py`] |
| BD3 | major | CFB at 100% forecast with no evidence, and the CFB forecaster sees no market (F2) | The least-informed component gets full weight on the noisiest sport | Start CFB at w0 = 0.5 to 0.7 until graded evidence exists, plus market reconciliation. If the user insists on 1.0, keep it but log a shadow blend so the data can settle it |
| BD4 | minor | "Baseline leads" in other sports contradicts "forecasts are football-only". With no forecast, there is nothing to blend | Ambiguity | Other sports: w = 0 by default, with a shadow forecast optional. "Leads" then means "only" |
| BD5 | minor | The repo's `update_blend` uses inverse-MSE weights shrunk with n0 = 400, and applies them after 150 graded player-games [H, local] | Inverse-MSE ignores error covariance. That is robust in small samples [M, Smith & Wallis 2009], but it can't show that the forecaster adds information | Keep inverse-MSE as the estimator, and add the encompassing-regression test and a Diebold–Mariano test (HAC, clustered by week) as the gate for changing weights |

### 4.7 "Three independent drafts, each built alone from the blended numbers."

| ID | Sev | Issue | Why it matters | Fix |
|---|---|---|---|---|
| D1 | major | If a solver builds them, the three drafts are **identical**. Checked: three CP-style solves on the same numbers returned the same lineup [D]. If LLMs build them, they aren't reliably legal or salary-optimal | Either wasted work, or noise passed off as "independence" | Drafts come from the **solver**, under three **theses**: (A) cash/mean-max; (B) ceiling, maximising P(S ≥ T₀.₉₇₇) with a QB stack and bring-back; (C) leverage, scored on EV against the field with ownership. Add a **simulated-optimal pool**: draw K outcome sets (for example K = 500–2,000), solve each, and count each player's "optimal-lineup rate" per slot |
| D2 | minor | The "top 3 per legal slot" deliverable isn't defined | Salary links the slots, so the per-slot top 3 don't add up to the optimal lineup | Report, per slot: the top 3 by optimal-lineup rate, by median, by p90, and by points per $1k. Grade with the top-k hit rate [sib:projections §6] |
| D3 | minor | "Each built alone": alone from what? | If the drafts see different facts, they carry different information. Otherwise they don't | Independence comes from the objectives and constraints, not from separate contexts |

### 4.8 "Decide: a strategist reviews the drafts but can't change a number. It removes anyone not playing, explains close calls and names the weakest pick. It can cut a pick only by citing a fact. A solver then builds the best legal lineup."

| ID | Sev | Issue | Why it matters | Fix |
|---|---|---|---|---|
| DC1 | **blocker** | A cut is the most extreme "number change" there is (projection set to −∞). The solver's result depends only on numbers and exclusions, so the strategist's only lever is exclusion, which invites over-cutting | Contradiction with "can't change a number", and a hidden channel for narrative bias | Cuts are limited to **hard facts**: official Out, Inactive, IR or Suspended, or a confirmed loss of role (benched or demoted with a source). Softer but *new* facts route to a bounded re-forecast of that game by the forecaster, the single owner of numbers. Nothing else changes the lineup |
| DC2 | major | **Double counting.** A cut citing a fact the forecaster already saw (for example "limited in practice", already priced into p_active and the median) counts it twice | A systematic downward bias on players with any injury note | Code checks **novelty**: `fact.id ∉ forecaster_packet_ids` and `fact.published_at > forecast_snapshot_at`. Hard-status facts are exempt, because exclusion is still right when status = Out. The current repo's `_verified` checks existence, subject, kind and confidence, but **not novelty** [H, local] |
| DC3 | major | "Removes anyone not playing" duplicates the code status filter, and makes an LLM the authority on availability | The official status feed is the authority. The LLM can only add facts | Code removes players from official statuses. The strategist may *propose* removals with a cited fact, and code accepts them only under DC1/DC2 |
| DC4 | minor | "Names the weakest pick" has no action attached | Wasted tokens | Trigger **one** targeted search on that player. A new fact goes to re-forecast. Log the pick and grade the strategist: is its "weakest pick" actually the worst performer more often than chance (1/9)? |
| DC5 | minor | "Explains close calls": the LLM shouldn't compute them | LLM arithmetic on objectives isn't reliable | Code computes, for each pick, Δobjective against the best legal single swap (using common random numbers). The strategist explains the numbers |
| DC6 | **blocker** | "Best legal lineup" doesn't name an objective | Mean-max is right for cash and wrong for GPPs (§5.3) | Use the objective that matches the contest type (§6.5) |

### 4.9 "Try to break it: a fresh checker attacks every pick. Code decides pass or fail, up to 2 revisions."

| ID | Sev | Issue | Why it matters | Fix |
|---|---|---|---|---|
| CK1 | **blocker** | Neither the pass/fail rule nor what a "revision" changes is defined | Can't be coded | **Fail** if (a) a hard check fails: illegal lineup, salary over the cap, duplicate athlete, player not in the pool, locked-slot violation, or a pick with official Out/Inactive status; or (b) a pick has a verified, **new** status or role fact at medium confidence or higher. A **revision** applies the exclusions and re-forecasts, then re-selects. After 2 revisions the main path is UNRESOLVED. Ship the backup lineup if it passes the hard checks and contains none of the unresolved picks. Otherwise ship the best main lineup that passes the hard checks, and flag what is unresolved (§6.4) |
| CK2 | major | A checker without fresh information can only re-argue facts the forecaster already used. The repo's checker task has `tools=[]` [H, local] | Double counting or noise | Give the checker **targeted** search: picks only, at most about 1–2 searches per pick, allowlisted. Require novelty (DC2). Its other job is consistency: facts versus numbers, scoring, legality |
| CK3 | minor | Attacks like "better use of salary" are solver questions | The lineup is optimal for the objective by construction (MIP gap 0) | Code rejects such attacks unless they point to a *number* error, such as a scoring-table mismatch or a stale status |
| CK4 | minor | Cost and time grow with revision loops | Up to 3 passes | Cap at 2 revisions (as the user specified). Each re-check covers only the changed picks |

### 4.10 "Backup: a second review, whose decider may adjust numbers with reasons, runs alongside in case the main one fails."

| ID | Sev | Issue | Why it matters | Fix |
|---|---|---|---|---|
| BK1 | **blocker** | It contradicts DC1 (the strategist can't change numbers), and neither "fails" nor precedence is defined | Two lineups built from different numbers, and no rule for which one ships | **Precedence**: the main path is canonical. Backup ships only if the main path fails *technically* (timeout, invalid output, refusal) or the main checker fails after 2 revisions. Backup adjustments are **bounded** (multiplier 0.5–1.3, as in the repo's `apply_backup` [H, local]), cite facts, and follow the novelty rule. Both lineups are always logged and graded, which gives an A/B test of whether adjusting numbers helps |
| BK2 | minor | "Runs alongside" doubles that stage's cost | Small: about $0.11 per slate on Sonnet [M, sib:llm_orchestration §4.10] | Keep it in parallel, since time is the binding constraint |

### 4.11 "My rules: any rule I set about who can be picked is solved as a second lineup, and I choose between the two."

| ID | Sev | Issue | Why it matters | Fix |
|---|---|---|---|---|
| U1 | major | Rule language isn't defined | Free-text rules parsed by an LLM can be silently wrong | A small DSL: `must`, `never`, `max_from_team`, `stack(qb, n)`, `bring_back(n)`, `exposure(player, ≤x%)`, `min_salary`. Code parses it deterministically. An LLM (Haiku) parses free text only with an echo-back the user confirms. Infeasible rules return the constraint that breaks |
| U2 | minor | The user chooses without information | Hard to decide well | Show the **price of the rule**: Δmean, ΔP(top 2.3%) and ΔEV against the model lineup (common random numbers) |
| U3 | minor | Rules don't persist into the last-hour step | A late swap could break the user's rule | Store the chosen lineup's rule set and enforce it in every later pass. If a must-play player is ruled out, drop the rule and notify |

### 4.12 "Last hour: search news on every pick and check inactives. In the NFL, re-forecast only players the news affects. Change the lineup only if a pick is out or a new lineup leads by over 1%. Hand over one final file."

| ID | Sev | Issue | Why it matters | Fix |
|---|---|---|---|---|
| L1 | **blocker** | "Last hour" before *which* lock? NFL slates have several kickoff windows. Inactives are due about 90 min before **each** kickoff [M, CBS/Falcons via sib:projections §2.8]. A single pass before the first lock can't see inactives for later games | Rostering an inactive player scores 0 | Run **one pass per kickoff window** with unlocked picks, starting about T_w − 60 (after inactives) and finishing by T_w − 50. Run a code-only status poll at T_w − 20 that can only force a swap. DK Classic allows late swap and locks each player at his own game's start. DK Showdown locks as a whole (`allowLateSwap: false`). FD allows late swap per game [M, sib:site_formats §5]. Showdown gets one pass |
| L2 | major | "Search news on every pick" misses non-picks whose news *changes* picks, such as a teammate ruled out or an opponent's cornerback out. Per-pick search is also expensive | Missed upside, and wasted tokens | News triage **per team** (one call each, at most 2 searches) on the teams of picks and their opponents, plus the code status feed |
| L3 | major | The "affected players" set isn't defined | Under-propagated news | Code rule: affected = news subject ∪ the same team's skill players in the same unit (vacated volume) ∪ opposing DST/K if a QB changes. Re-forecast the affected game, and re-use every other forecast unchanged |
| L4 | major | "Leads by over 1%" doesn't say 1% of what, and simulation noise can exceed 1%. With 10,000 independent simulations, P(top 2.3%) has a relative SE of about 6.5%; with 50,000, about 2.9% [D] | Switching on noise | Compare objectives under **common random numbers** (same draws for both lineups). Switch if forced (a pick is out or inactive), or if Δobj > max(1% · \|obj_cur\|, 2·SE_paired) [D, §6.4] |
| L5 | major | CFB news: only the NFL gets re-forecast. Yet CFB availability reports now exist: Big Ten (2023), SEC (2024), ACC and Big 12 (2025). The Big 12 publishes them up to 90 min before kickoff [M, sib:projections §3] | Stale CFB lineups | Apply status changes and redistribution in code for CFB too. Re-forecast on role news |
| L6 | major | No lock-aware re-optimisation | A late swap that re-solves from scratch can't touch locked players, or may produce an infeasible lineup | Fix locked players (DK marks them ` (LOCKED)` [H, local sample]). Re-solve the remaining slots under the same cap. Build lineups with **late-game flexibility**, putting late-game players in FLEX/UTIL to keep swap options (common practice [L]) |
| L7 | minor | "One final file" conflicts with multiple passes and multiple entries | Confusion over which file to upload | Each pass writes a complete upload file in the site's layout, marked SUPERSEDES the previous one. DK: same entries layout, only changed rows [H, DK instruction 5 via sib:sources_compliance §7]. FD: CSV-edit template with Ids [M] |
| L8 | minor | A questionable player in a late game | Zero risk | "Insurance" rule: roster him only if a same-slot pivot, within salary, exists in a game that locks *after* his inactives |

### 4.13 "Grade it against the best possible lineup, name the step that failed, fix that step for future contests, and use the results to update the blend."

| ID | Sev | Issue | Why it matters | Fix |
|---|---|---|---|---|
| GR1 | major | The hindsight-optimal benchmark is dominated by luck. Toy model, 300 slates: with **perfect knowledge of every true mean**, efficiency against the hindsight optimum averaged 0.555 (SD 0.092, max 0.80). The realistic system averaged 0.518 (SD 0.103). The hindsight optimum itself swung from 196 to 281 points [D] | It rewards luck and punishes variance. Most "failures" are noise | Report the hindsight optimum only as context. Grade **layers** with proper scores (MAE, CRPS, pinball, coverage) and grade **lineups** by percentile finish in the real contest (standings export) and by EV regret |
| GR2 | major | Statistical power: a projection system 20% better on true means (MAE 5.52 → 5.38, 2.6% better) is detected in about **7 slates** by player-level MAE, but needs about **968 slates** by lineup points and about **1,005** by efficiency against the optimum (paired, 80% power) [D, toy; real within-slate correlation raises the MAE figure] | "Grade the lineup, then fix" can't tell a better model from a worse one inside a human lifetime | Learn from player-level losses, and use lineup metrics only for monitoring |
| GR3 | major | "Name the step that failed" from one slate is noisy | Wrong blame | **Counterfactual layer swap**: replace one layer at a time with the truth (status, team volume, usage shares, efficiency), re-solve, and measure recovered points [sib:projections §6]. Aggregate across slates before acting |
| GR4 | **major** | "Fix that step for future contests" means the pipeline modifies itself after single contests | Overfitting, prompt drift, and no reproducibility | **Change control.** Each fix is a versioned proposal (prompt, parameter or code). Replay it on all frozen post-July-2026 slates, and accept it only if a week-clustered bootstrap CI on the metric excludes 0. No automatic self-edits |
| GR5 | major | "Update the blend" from a handful of contests | Detecting a 0.1-point MAE gain with loss-differential SD 1.5 needs about 1,766 independent player-games, before any clustering adjustment [D, sib:projections §7.3] | Shrinkage (BD1), a minimum n, and the DM test. The repo shrinks with n0 = 400 [H, local] |
| GR6 | minor | Data for grading | Can't scrape DK or FD | The user downloads the contest-standings CSV, which carries points, `%Drafted` and FPTS (the repo's reader [H, local `grade.py`]). Real ownership then also calibrates the ownership model |

---

## 5. Cross-cutting findings

### 5.1 Contradictions and ambiguities

| # | Statement A | Statement B | Resolution |
|---|---|---|---|
| X1 | Strategist "can't change a number" | Strategist "can cut a pick", and a cut is a number change | Hard-fact cuts only. Soft new facts go to re-forecast (DC1) |
| X2 | Strategist can't change numbers | Backup "may adjust numbers" | Main path canonical. Backup bounded, ships only on failure, A/B graded (BK1) |
| X3 | "Three independent drafts" | A deterministic solver on the same numbers | Different theses and objectives, plus a simulated pool (D1) |
| X4 | Drafts "built alone" | Who builds them, LLM or solver, isn't said | The solver (D1) |
| X5 | Checker "revisions" | What changes in a revision isn't said | Exclusions and re-forecasts, then re-select (CK1) |
| X6 | "Football only" forecasts | "In other sports the baseline leads" | w = 0 outside football, shadow forecast optional (BD4) |
| X7 | Forecaster sees "no prices" | The baseline uses betting lines, and CFB is 100% forecast | Blind forecaster, market reconciled in code with κ (F2) |
| X8 | "Remove anyone ruled out" at baseline time | "Check inactives" in the last hour | Availability is a probability until the official status. Hard removal only from official status (BL3) |
| X9 | "Last hour" | Several kickoff windows and late swap | One pass per window (L1) |
| X10 | "Re-forecast" only in the NFL | CFB availability reports exist | Handle CFB status in code too (L5) |
| X11 | "Leads by over 1%" | No unit given, and simulation noise | Paired objective delta with CRN and an SE gate (L4) |
| X12 | "Hand over one final file" | Multiple windows and entries | One complete file per pass, superseding the last (L7) |
| X13 | "Top 2.3% of every contest" | Cash games and a high-variance payoff | Contest-specific objective. Measure the rate, never promise it (§3) |
| X14 | "Learn the scoring" | Scoring must be exact | Versioned tables plus tests, with the LLM only diffing text (SC1) |
| X15 | "Fix that step" | Reproducibility | Change control (GR4) |
| X16 | "Likely" | Mode, median or mean? | p50, conditional on playing, with the mean in code (F3) |

### 5.2 Statistical risks (summary)

1. **Fixed weights without validation (BD1).** They also invert the evidence: in the NFL the market enters
   only at 15%.
2. **Blinding the forecaster discards the market (F2).** It must re-enter in code, or the forecast has
   less information than the baseline.
3. **"Baseline leads" is undefined (BD4, BL4).**
4. **Learning the blend from a few contests (GR5).** It needs shrinkage, a minimum n and a DM test, and
   only post-cutoff data (F5).
5. **Grading against the hindsight optimum (GR1, GR2).** Luck dominates. Use proper scoring rules.
6. **Overconfident LLM intervals (F3).** Calibrate them before they enter any tail-sensitive objective.
7. **Double counting (DC2, CK2)** between the forecaster, strategist and checker.
8. **Binary availability (BL3)** instead of p_active plus redistribution.
9. **Independence assumptions in simulation.** Teammates and opponents are correlated: QB–WR1 is about
   0.37–0.54, and DST against opponent points is about −0.78 [M, sib:projections §5.1]. Simulate with a
   copula on residuals, not independent draws.
10. **Look-ahead leakage (F5).**
11. **Simulation noise in decisions (L4).** Use CRN and SE gates.

### 5.3 Strategic gaps relative to "top 2.3%"

**Why mean-max is wrong for a top-2.3% target [D].** Under a normal approximation, a lineup with mean μ
and SD σ clears a threshold T with probability P = 1 − Φ((T − μ)/σ). Then

  ∂P/∂σ = φ(z)·(T − μ)/σ²  >  0   whenever T > μ.

The 97.7th percentile of a GPP field sits above any single lineup's mean. So trading a little mean for more
variance raises P(top 2.3%). Correlation is the cheap source of variance: stacks and bring-backs. This is
the core idea of the integer-programming "picking winners" work, which stacked correlated players and
controlled overlap between lineups. The paper exists (arXiv 1604.01455 [H, repo README]), and the
stacking and overlap results are relayed [M, sib:projections §5.3]. Its exact objective is UNCONFIRMED here.

| Gap | Severity | Fix |
|---|---|---|
| No contest-type awareness | blocker | §6.5 objective table, per contest (C2, C3) |
| No ownership or field model ("game theory") | major | Code ownership model: logit(own) on value, projection, salary rank within position and position scarcity, calibrated on `%Drafted` from standings exports. Then a simulated field: noisy-projection optimal solves reweighted to match ownership (repo `field.py` already does entropy reweighting [H, local]). Haugh & Singal model opponents with a Dirichlet-multinomial [M, Management Science 67(1):72–92, 2021] |
| No correlation or stacking | major | Correlated simulation, plus solver options `qb_stack` and `bring_back` (present in repo `ilp.py` [H, local]), used as thesis constraints and evaluated by simulation, not forced |
| Low/likely/high never used | major | They feed the simulation, and the simulation feeds every objective |
| No duplication awareness | major | EV divides the payout by 1 + expected duplicates, estimated from the field model. Penalise high ownership-product lineups when EV is close |
| No late-swap strategy | major | L1, L6, L8 |
| No multi-entry handling | major | Portfolio: greedily add the lineup with the highest marginal EV of the portfolio, under exposure caps and a `min_unique` overlap constraint (repo `SolveOptions.min_unique` [H, local]). Never exceed the contest's `[N Entry Max]` or the file's rows |
| "Expert agents in DFS, poker, game theory, maths" | major (framing) | Expertise lives in **code**: objectives, simulation, ownership and portfolio maths. Personas don't create it. LLM roles stay narrow: gathering facts, forecasting from facts, and adversarial fact-checking |
| Bankroll and variance | minor | Optional entry-size guard and spend log. GPP outcomes are extremely skewed, so a slate's spend should be a small fixed share of bankroll (the user's choice; no number is claimed here). Responsible-gaming limits [sib:sources_compliance §10] |

### 5.4 Operational risks

| Risk | Severity | Detail | Fix |
|---|---|---|---|
| 60-minute limit | major | The largest variable costs are research tool loops and checker revisions [M, sib:llm_orchestration §4.9] | Hard timeouts per stage and a global watchdog. Safety lineup by about minute 5. Parallel calls per game. Budget allocator in §6.3 |
| Token and dollar hot spots | major | Search results re-read across turns, forecaster thinking tokens, checker searches, and per-pick news. Estimate: about $10.6 per typical 12-game NFL slate, with a recommended hard cap of $15 [M, sib:llm_orchestration §4.10, built on confirmed prices] | Code-built dossiers, research only for gaps, `max_uses` caps, pruning (about 20 players per game), news per team, effort tuned on graded data |
| Batch API | minor | 50% off, but batches *usually* finish within an hour, with no guarantee [H, models overview (50%); M, batch timing via sib] | Keep it off the critical path. Use it for grading and pre-week research |
| Failure handling | major | API errors, refusals, `pause_turn`, invalid JSON, timeouts | Every stage has a fallback (§6.2). Invariant: **a legal, all-active lineup always exists on disk** |
| Determinism and reproducibility | major | Opus 5.5 rejects non-default `temperature`, `top_p` and `top_k`. "If you were using `temperature = 0` for determinism, note that it never guaranteed identical outputs on prior models" [H, Opus 5.5 migration guide] | Freeze every stage's artifacts (content hashes), pin model IDs and prompt versions, seed the solver and simulation, keep `run_manifest.json`, and replay from artifacts |
| Data freshness | major | Injury, depth and news sources update at different times | Every fact and feed row carries `retrieved_at`. Re-poll status in each window pass. Staleness flags in the report |
| Sources that forbid automation | blocker (go-live) | DK and FD ToS not read; scraping reportedly prohibited [L, UNCONFIRMED]. Many third-party sites unverified [sib:sources_compliance §0] | Allowlist gate in code: a source stays off until `verified_at` is set. No browser automation. The user uploads every file |
| Inactives timing | blocker (NFL) | About 90 min before each kickoff [M] | Window passes (L1). No free, allowed structured inactives feed was found [M, sib:sources_compliance §2.5], so use web search on official team and league domains, or a paid licensed feed |
| Different lock times on one slate | blocker | DK `Game Info` carries the ET kickoff. FD needs a schedule [M] | Lock-aware solver. Schedule per window |
| Rate limits | minor | Start tier per model: 2M ITPM and 400K OTPM. The NFL fan-out uses about 8% and 14% of those [M, sib:llm_orchestration §4.7] | Semaphores per model |
| Human in the loop at each window | major | The user must upload after each late pass | Notify when a file is ready. Code-only passes take about 1 minute when there is no news |

### 5.5 Code or LLM: who owns what

| Function | Owner | Reason |
|---|---|---|
| CSV parsing, site/sport/type detection, ID crosswalk | **code** | Deterministic and testable |
| Contest rules, scoring tables, contest metadata | **code** (registry plus user confirmation). Haiku 5.5 may diff pasted rules text | Exactness |
| Structured data adapters, allowlist, robots, rate limits | **code** | Compliance |
| Availability status (official) and vacated-volume redistribution | **code** | Authority, plus arithmetic |
| Baseline projections | **code** | Backtestable on any season (no LLM leakage) |
| Gap research and fact extraction with citations | **Sonnet 5.5** (medium effort) | Tool-heavy, parallel, judgement on relevance |
| Blinded forecast (stat-component quantiles, p_active hints) | **Opus 5.5** (high effort); optional Sonnet 5.5 second | Judgement-heavy synthesis |
| Sanitizer, calibration, blend, reconciliation, simulation | **code** | Maths |
| Ownership and field model, objectives, candidates, selection, portfolio | **code** (CP-SAT plus simulation) | Optimality guarantees |
| Strategist explanations, weakest pick, fact-cited exclusion proposals | **Opus 5.5** (high) | Adversarial reasoning over facts |
| Accepting or rejecting a cut, checker pass/fail, backup precedence, switch rule | **code** | The user's own requirement, and auditability |
| Checker attacks (targeted new-fact search, consistency) | **Opus 5.5** (medium) or Sonnet 5.5 | A fresh context finds missed facts |
| Backup reviewer (bounded adjustments) | **Sonnet 5.5** (high) | Model diversity, low cost |
| Last-window news triage | **Haiku 5.5** if its web search is supported (check `capabilities.server_tools.web_search.supported` via the Models API [H, models overview]), otherwise Sonnet 5.5 at low effort | Volume and latency |
| User-rule parsing | **code DSL**. Haiku for free text, with echo-back | Exactness |
| Grading, attribution, weight updates, change control | **code** (Batch-API Sonnet for narrative summaries only) | Statistics |
| Fable 5.1 | not by default | 2.5× Opus 5.5 prices ($10/$50 vs $4/$20 per MTok), latency "Slower" [H, models overview] |

---

## 6. Revised pipeline (same spirit, fixed)

### 6.1 Principles

1. **One owner of numbers**, the projection layer (baseline, blinded forecast, calibrated blend, market
   reconciliation). Everything downstream changes the lineup only through:
   - (a) code-verified **hard-fact exclusions**, or
   - (b) **new facts** that trigger a bounded re-forecast.
2. **Code decides; LLMs inform.** Every accept or reject is a deterministic rule over structured fields.
3. **Distributions, not points.** Every objective is computed by correlated simulation.
4. **The objective follows the contest.** Each contest row in the entries file gets its own objective.
5. **Always have a legal, all-active lineup** on disk from minute about 5 onward. Each stage can only
   replace it with something better validated.
6. **Freeze everything.** Content-hashed artifacts, pinned models and prompts, seeds. The same artifacts
   replay to the same lineup.
7. **Learn slowly.** Proper scores, shrinkage, minimum n, clustered tests, and change control through
   replay.

### 6.2 Stages

T = the run's start. Times are typical / cap, in minutes. Model tiers and per-stage estimates follow
sib:llm_orchestration §4.2 and §4.9 [M]. Latencies are UNCONFIRMED until the first dry run.

| # | Stage | Inputs | Outputs | Owner | Budget | Fallback |
|---|---|---|---|---|---|---|
| 0 | **Intake and contest model** | User's DK/FD file(s); rules registry; per-contest metadata (type, field size, payout, entry max) | `contest.json`: slots, cap, rules, contests[] with type and objective, pool, locks | code (Haiku only to diff pasted rules text) | 0.5 / 1 | Unknown cap or slots: **stop and ask**. Unknown contest type: GPP plus a warning |
| 1 | **Scoring** | Registry scoring table | `scoring@version` | code | <0.1 | No table: stop |
| 2 | **Structured data** | Allowlisted adapters: schedule and kickoffs, injuries and practice, depth, usage, weather, market lines and props (where licensed) | `feeds/*.parquet` with `retrieved_at` | code | 2–3 / 5 | Last good cache plus a staleness flag |
| 3 | **Baseline and availability** | Feeds, scoring | `baseline.json` (quantiles, p_active per player) | code | <1 / 2 | Generic: site FPPG shrunk to the position mean |
| 3b | **Safety lineup** | Baseline, rules | `out/safety.csv` (upload format) | code (CP-SAT, mean-max, hard filters) | <0.5 | — (always produced) |
| 4 | **Prune** | Baseline, flags | `pool.json` (about 20 per game), with reasons | code | <0.5 | Keep everyone |
| 5 | **Gap research** (per game, parallel) | Code-built dossier, allowlist | `facts/{game}.json` (fact contract, R3) | Sonnet 5.5 medium, ≤6 searches and ≤3 fetches | 3–4 / 6 | Code-only dossier |
| 6 | **Sanitize** | Facts | `packet/{game}.json` with no salaries, DFS projections, ownership, FPPG or past fantasy points; lines and props off by default | code | <0.2 | — |
| 7 | **Blinded forecast** (per game, parallel) | Packet | `forecast/{game}.json`: team volume and pass rate; per-player component quantiles (p10/p50/p90); p_active hints; key fact ids | Opus 5.5 high (optional second: Sonnet 5.5 high) | 4–5 / 8 | Baseline-only for that game (flagged) |
| 8 | **Calibrate, blend, reconcile, simulate** | Baseline, forecasts, learned weights, calibration factors, market totals, correlations | `proj.json` (quantiles, mean, p_active); `sims.npy` (10k–20k correlated draws, seeded) | code | 1 / 2 | Baseline distributions |
| 9 | **Ownership and field** | Projections, salaries, contest size, ownership model | `own.json`; field sample (scores per simulation) | code | 1–2 / 3 | Heuristic ownership (value-rank) |
| 10 | **Candidates** | Sims, rules | Thesis lineups A (mean), B (ceiling/stack), C (leverage); simulated-optimal pool (K solves); per-slot top 3 | code (CP-SAT) | 1–2 / 3 | Fewer K |
| 11 | **Select per contest** | Candidates, objective per contest, field | `selected.json` (one lineup, or a portfolio per contest) | code | <1 | Thesis A |
| 12 | **Strategist** | Candidates, Δobjective table, facts, the safety and selected lineups | Exclusion proposals (fact ids), close-call explanations, weakest pick (one targeted search) | Opus 5.5 high | 2–3 / 4 | Skip (logged) |
| 12a | Apply strategist (code) | Proposals | Accepted exclusions; new facts sent to re-forecast (stage 7 for that game only) | code | <1 | — |
| 13 | **Checker loop** (≤2 revisions) | Selected lineup, facts, scoring and rules | Attacks with fact ids, verdict by code | Opus 5.5 medium, ≤1–2 searches per pick | 3–4 / 10 | UNRESOLVED: the backup lineup if it is hard-valid and avoids the unresolved picks, otherwise the last hard-valid main lineup, flagged |
| 13b | **Backup reviewer** (parallel with 12–13) | Same as 12 | Bounded adjustments, then `backup.csv` | Sonnet 5.5 high | parallel / 4 | — |
| 14 | **User-rule lineup** | Rules DSL | `user_rule.csv` plus the price of the rule | code (Haiku for free text, with echo-back) | <1 | Rule rejected, with the broken constraint named |
| 15 | **Output** | Precedence (§6.4) | `out/final_{pass}.csv` in upload format; `report.html/md` (per-slot top 3, distributions, exposures, reasons, flags); `run_manifest.json` | code | <0.5 | Safety lineup |
| W | **Window pass** (per kickoff window with unlocked picks; Showdown: once) | Official status and inactives, team news, locks | Re-forecast of affected games only; lock-aware re-solve; switch decision; superseding file | code + Haiku/Sonnet (triage) + Opus (re-forecast) + Sonnet (quick check of changed picks) | 6–8 / 10 | Code-only pass (status poll plus forced swaps): about 1 min |
| G | **Grade and learn** (after the slate) | Frozen artifacts, standings CSV, actual stats | Per-layer scores, lineup percentile and EV regret, counterfactual attribution, weight and calibration updates, change proposals | code (Batch Sonnet for the narrative summary) | offline | — |

### 6.3 Time budget and timeline (NFL Sunday main slate)

- **Main run**: about T_first − 3 h.
  - Typical 22–28 min, hard cap 40.
  - The research (5) and forecast (7) fan-outs dominate.
  - The checker (13) is the variance source and is capped at 10.
- **Window passes**: start at T_w − 60, done by T_w − 50, giving the user about 40 minutes to upload.
  - A code-only status poll at T_w − 20 can only force swaps.
  - Typical cost is 6–8 min per window with news, about 1 min without.
- **Allocator**: remaining_budget = 60 − main_actual, divided across the windows that contain unlocked
  picks.
  - If remaining_budget < 8 × windows, window passes drop to code plus triage only. Re-forecast then runs
    only for windows where a pick's status changed.
- **Example**: main 28 + 1 pm window 8 + 4 pm window 8 + SNF window (if on the slate) 6 = 50 min.

### 6.4 Decision rules (pseudo-code; deterministic)

```python
HARD_OUT = {"OUT", "INACTIVE", "IR", "SUSPENDED", "NFI", "PUP"}
SOFT_KINDS = {"role", "depth_chart", "snap_limit", "coaching", "injury"}

def classify_claim(claim, facts, packet_ids, forecast_at, player):
    f = facts.get(claim.fact_id)
    if f is None or not allowlisted(f.source_url) or f.confidence == "low":
        return "reject"
    if not about(f, player):                       # subject id, or team-level role fact
        return "reject"
    if f.kind == "status" and f.status in HARD_OUT:
        return "exclude"                           # hard availability: no novelty needed
    novel = (f.id not in packet_ids) and (f.published_at > forecast_at)
    if f.kind in SOFT_KINDS and novel:
        return "reforecast"                        # numbers change only through the forecaster
    return "reject"                                # already priced in: no double counting

def checker_verdict(lineup, attacks, facts, packet_ids, forecast_at, rules, status):
    hard_fail = (not legal(lineup, rules)) or any(status[p] in HARD_OUT for p in lineup.unlocked)
    soft = [a for a in attacks if a.severity in ("blocker", "major")
            and classify_claim(a, facts, packet_ids, forecast_at, a.player) in ("exclude", "reforecast")]
    return ("FAIL" if hard_fail or soft else "PASS"), soft

def run_checker_loop(lineup, max_rev=2):
    for rev in range(max_rev + 1):
        verdict, soft = checker_verdict(...)
        if verdict == "PASS":
            return lineup, "PASS"
        if rev == max_rev:
            break
        apply_exclusions_and_reforecasts(soft)     # stage 7 for affected games only
        lineup = select(...)                       # same objective, same CRN seeds
    return best_hard_valid(lineup_history), "UNRESOLVED"

def choose_final(main, backup, safety):
    if main.ok and main.verdict == "PASS":
        return main
    # main failed technically, or is UNRESOLVED after 2 revisions
    if backup.ok and hard_valid(backup.lineup) and not (set(backup.lineup) & main.unresolved_picks):
        return backup                               # flagged; both are graded as A/B anyway
    if main.ok and hard_valid(main.lineup):
        return main                                 # UNRESOLVED main ships with a flag
    return safety

def team_total_reconcile(team_pts_forecast, market_implied, kappa):   # football, code only
    target = (1 - kappa) * team_pts_forecast + kappa * market_implied   # kappa=0 → user's spec
    r = target / team_pts_forecast
    return r      # scale that team's volume components by r, then re-simulate

def switch(cur, new, sims, objective):            # window pass
    forced = any(status[p] in HARD_OUT for p in cur.unlocked)
    a = objective.per_sim(cur, sims); b = objective.per_sim(new, sims)   # common random numbers
    d = b - a; delta = d.mean(); se = d.std(ddof=1) / len(d) ** 0.5
    return forced or (delta > max(0.01 * abs(a.mean()), 2 * se))
```

### 6.5 Objectives by contest type (all evaluated on the same correlated simulations)

| Contest type (from metadata) | Payout shape | Objective, per simulation s | Construction notes |
|---|---|---|---|
| Cash: 50/50, double-up, H2H | Flat, about the top half (exact share UNCONFIRMED; read the payout table) | maximise P(Sˢ ≥ cash_lineˢ). Cash line from the field model, or historical standings | High floor. Avoid late questionable players without insurance. Correlation matters less |
| Single-entry or small-field GPP | Top-heavy | maximise P(Sˢ ≥ T₀.₉₇₇ˢ), or EV = mean_s[payout(rankˢ)/(1+dupesˢ)] − fee | Moderate stack and bring-back. Some leverage |
| Large-field GPP, single entry | Very top-heavy | EV with duplication | Strong correlation, leverage, uniqueness |
| Multi-entry GPP (N ≤ entry max) | Very top-heavy | Greedy portfolio: L_k = argmax ΔEV(portfolio ∪ L) subject to exposure caps and `min_unique` | Diversify theses. Overlap control [M, Hunter et al. relayed] |
| Showdown (any) | Varies | As above, with CPT/MVP multipliers | Captain choice by ceiling. Duplication is very high in Showdown, so weight `dupes` more (practitioner consensus [L]) |

T₀.₉₇₇ˢ is the 97.7th-percentile score of the simulated field in simulation s. This is where the user's
"top 2.3%" becomes code.

### 6.6 Grading and learning protocol

1. **Per slate (code, automatic).**
   - Per player and source (baseline, forecast A/B, blend, calibrated): MAE, bias, pinball at
     0.1/0.5/0.9, CRPS, 80% coverage, and Brier on p_active for questionable players.
   - Per lineup: actual score, percentile in the real contest (standings CSV), predicted vs realised
     P(top 2.3%), and EV regret.
   - Counterfactual layer swaps (GR3).
   - Hindsight optimum as context only.
2. **Rolling (weekly).**
   - Blend weights per position and stratum, via the shrunk encompassing regression (BD1).
   - Interval calibration factors (F3), the κ reconciliation strength, and p_active calibration.
   - Ownership-model recalibration from `%Drafted`.
   - Only games played from July 2026 on.
3. **Change control (human-approved).**
   - The attribution produces *proposals*.
   - Each is replayed on every frozen slate.
   - Accept only if the week-clustered bootstrap CI on the target metric excludes 0, or if a DM test with
     HAC errors rejects.
   - Every accepted change is a versioned commit: prompt version, parameter file, model ID.
4. **Strategist, checker and backup skill.**
   - Track the accuracy of cuts: did the excluded players underperform their projections?
   - Track the weakest-pick hit rate against 1/9.
   - Track backup against main lineup outcomes.
   - Drop any stage that doesn't beat a no-op over enough slates.

### 6.7 Data contracts (minimum)

```text
Fact        {id, subject_id, team, kind, status?, text, quote, source_url, published_at, retrieved_at, confidence}
Packet      {game_id, as_of, facts[], usage{player_id: {snap, route?, tgt_share, carry_share, rz}}, weather, depth}
            # NO salary, FPPG, DFS projection, ownership, past fantasy points; lines/props only if policy says so
Forecast    {game_id, team: {plays_q, pass_rate_q, points_q}, players: [{player_id, components: [{stat, p10, p50, p90}],
             p_active_hint, key_fact_ids[]}], model, prompt_version, created_at}
Projection  {player_id, site, p10, p50, p90, mean, p_active, sources{baseline, forecast, weights}, flags[]}
Contest     {site, sport, game_type, contest_id, name, type, field_size?, payout_table?, entry_max, slots[], cap, locks{game: ts}}
Decision    {stage, claim, fact_id, outcome: exclude|reforecast|reject, rule_version}
RunManifest {run_id, as_of, inputs_sha256{}, models{}, prompt_versions{}, seeds{}, budgets{}, timings{}, cost_usd}
```

### 6.8 Mapping from the user's steps to the revised stages

| User step | Revised stage(s) | Main change |
|---|---|---|
| Read the contest | 0 | Registry plus per-contest metadata. Several contests per file |
| Learn the scoring | 1 | Versioned tables and tests, with the LLM only diffing |
| Research every game | 2, 5 | Code feeds first. LLM researches gaps only. Fact contract. Allowlist enforced |
| Baseline numbers | 3, 3b | Top-down market baseline. CFB made market-aware. Availability as a probability. Safety lineup |
| Fresh forecasts | 6, 7 | Explicit blinding schema. Stat components. Per-game calls. Calibrated quantiles |
| Blend | 8 | User weights become priors that are learned. Quantile averaging. Market reconciliation κ |
| Three drafts | 10 | Solver theses plus a simulated-optimal pool (which yields the per-slot top 3) |
| Decide | 11, 12, 12a | Objective by contest type. Hard-fact exclusions. Novelty rule. Close calls computed in code |
| Try to break it | 13 | Defined verdict and revision. Targeted new-fact search |
| Backup | 13b | Bounded, ships only on failure, A/B graded |
| My rules | 14 | DSL, price of the rule, rules persist |
| Last hour | W | One pass per window. Affected-set rule. Lock-aware. CRN switch rule. Superseding files |
| Grade | G | Proper scores, layer attribution, change control, learning only after the cutoff |

---

## 7. Notes on the current repo (working tree read 2026-10-21; may change)

Already aligned [H, local]:
- CP-SAT exact solver with stack, bring-back, `min_unique`, exclude and lock options (`dfs/optimize/ilp.py`).
- Rules registry and YAML scoring tables.
- Quantile-averaging blend with the user's priors and learned weights (`dfs/blend.py`).
- Fact-verified strategist cuts and a code checker verdict (`dfs/stages/agents.py`).
- Bounded backup multipliers (0.5–1.3).
- A field model with entropy reweighting to ownership (`dfs/field.py`).
- A standings-CSV reader and a shrunk blend update (`dfs/grade.py`).

Suggested changes:
1. Add the **novelty check** (DC2) to `_verified`, `apply_strategist` and `checker_verdict`. Pass the
   forecaster packet's fact ids and the forecast timestamp.
2. Split strategist "cuts" into `exclude` (hard status) and `reforecast` (new soft fact) instead of
   excluding on any role-kind fact.
3. Give the checker targeted, allowlisted search, or document that it is a consistency-only checker.
4. Add the encompassing-regression and DM gates beside the inverse-MSE `update_blend`. Stratify by
   "has props".
5. Add per-contest objectives (§6.5) and the CRN switch rule (§6.4).
6. Add window-pass scheduling from `Contest.locks`.
7. CFB: add the market-aware baseline and the κ reconciliation, and log a shadow blend while the prior
   stays 1.0.
8. The grader's single-slate "largest projection miss > 8 points means the projection failed" heuristic is
   noisy (GR3). Use aggregated counterfactual attribution before naming a failed step.

---

## 8. Open questions for the user

1. **Contest mix.** Cash or GPP? Single-entry or multi-entry (and how many entries)? Typical field sizes?
   This decides the objectives.
2. **Market policy for the forecaster.** Fully blind, including betting lines and props (recommended), with
   the market reconciled in code? Or may the forecaster see team totals?
3. **CFB weight.** Accept a learned weight starting at 0.5–0.7, or keep 1.0 with a shadow blend?
4. **Sports in v1.** NFL and CFB only, with the generic fallback elsewhere?
5. **Availability on game day.** Can you upload after each kickoff-window pass (about 40 minutes of
   slack)? If not, build for the first lock only, with late-game insurance.
6. **Inactives source.** Web search on official domains, or a paid licensed feed?
7. **Budget per slate.** The sibling estimate is about $10.6 for an NFL main slate, with a $15 cap. Is that
   acceptable?
8. **State of residence.** Needed to confirm DFS legality and age rules [sib:sources_compliance §10].
9. **Success target.** Do you accept the restated, measurable targets in §3.3 in place of "top 2.3% of
   every contest"?

---

## 9. Sources

Confirmed today [H]:
- Claude models overview: model IDs, June 2026 reliable-knowledge and training cutoffs, prices
  ($4/$20 Opus 5.5, $2/$10 Sonnet 5.5, from $0.10/$0.50 Haiku 5.5, $10/$50 Fable 5.1), latency labels,
  Batch 50% off, `capabilities.server_tools.web_search.supported`.
  https://platform.claude.com/docs/en/about-claude/models/overview
- Claude Opus 5.5 migration guide: non-default `temperature`, `top_p` and `top_k` return a 400 error, and
  temperature 0 "never guaranteed identical outputs".
  https://platform.claude.com/docs/en/models/opus-5-5/migration-guide
- Hunter, Vielma & Zaman code repository README, naming the paper and its arXiv link:
  https://github.com/dscotthunter/Fantasy-Hockey-IP-Code (paper: http://arxiv.org/pdf/1604.01455v2.pdf)
- Local inspection of real DK entries exports: several contests per file, ` (LOCKED)` marker, slot headers.
  `data/cache/real_samples/DKEntriesShowdown.csv`, `DKEntriesClassicLateSwap.csv`, `DKEntriesClassic.csv`,
  `DKEntries_NBA_Mar2026.csv`
- Local repo code (commit 74b4953 plus working tree): `dfs/blend.py`, `dfs/grade.py`,
  `dfs/stages/agents.py`, `dfs/optimize/ilp.py`, `dfs/field.py`

Relayed from sibling research [M] (original URLs in those notes):
- FFA projection accuracy (weekly MAE QB 6.2, RB 5.2, WR 4.9):
  https://fantasyfootballanalytics.net/which-dfs-projections-are-most-accurate
- 4for4 weekly CV (QB 41%, RB 63%, WR 61%):
  https://www.4for4.com/2016/preseason/leveraging-weekly-player-consistency-daily-fantasy-football
- Share stickiness (WR target share r ≈ 0.70, yards per target R² ≈ 0.05): Sumer and Sharp Football, via
  sib:projections §0
- FermiEval (99% intervals cover about 65%): https://arxiv.org/abs/2510.26995
- QuantSightBench (best 79.1% coverage on 90% intervals): https://arxiv.org/abs/2604.15859
- LLM anchoring: https://arxiv.org/abs/2505.15392 ; https://aclanthology.org/2026.acl-short.16/
- Smith & Wallis 2009, the forecast-combination puzzle:
  https://econpapers.repec.org/RePEc:bla:obuest:v:71:y:2009:i:3:p:331-355
- Lichtendahl, Grushka-Cockayne & Winkler 2013, quantile averaging:
  https://ideas.repec.org/a/inm/ormnsc/v59y2013i7p1594-1611.html
- Schoenegger et al. 2024 (silicon crowd): https://arxiv.org/abs/2402.19379
- Halawi et al. 2024: https://arxiv.org/abs/2402.18563
- Hunter, Vielma & Zaman, stacking and overlap: https://arxiv.org/abs/1604.01455
- Haugh & Singal 2021, Management Science 67(1):72–92, Dirichlet-multinomial opponents:
  https://ideas.repec.org/a/inm/ormnsc/v67y2021i1p72-92.html
- NFL inactives about 90 minutes before kickoff:
  https://www.cbssports.com/nfl/news/nfl-makes-rare-in-season-rule-change-following-awkward-covid-19-testing-situation-with-dez-bryant ;
  https://www.atlantafalcons.com/news/starters-and-inactives-falcons-at-49ers-16242019
- CFB availability reports (Big Ten 2023, SEC 2024, ACC and Big 12 2025; Big 12 up to 90 minutes before
  kickoff):
  https://www.cbssports.com/college-football/news/big-12-to-mandate-player-availability-reports-for-football-basketball-starting-in-2025-26
- Correlations: QB–WR1 0.37–0.54, https://www.fantasylabs.com/articles/introducing-nfl-correlation-values/ and
  https://spikeweek.com/visualizing-single-game-correlation/ ; DST against opponent points −0.784 (FantasyLabs
  DST, via sib:projections §5.1)
- DK/FD late swap (DK Classic `allowLateSwap: true`, Showdown `false`; FD per game) and the CSV contents,
  including that the cap is absent: sib:site_formats §5–§6 (captured DK rules JSON from
  https://github.com/gacolitti/draft.kings ; FD rules snippets)
- DK entries instructions ("Column A lists all of your contest entries for this draftgroup"; "only include
  entries you are changing"):
  https://raw.githubusercontent.com/troyseanwilliams-hash/Fantasy-Lineup-Optimizer/main/attached_assets/DKEntries_(17)_1773687635103.csv
- Claude cost, latency, rate limits, web tools and the Batch API: sib:llm_orchestration §4.2–§4.10
  (platform.claude.com pricing, rate-limits, web-search-tool, web-fetch-tool, batch-processing pages)

Low or UNCONFIRMED:
- The DK/FD ToS ban on scripts and scraping (paraphrased recollection).
- The play rate of "questionable" players after 2016.
- The exact share of entries that cash in DK/FD cash and GPP contests.
- The exact objective in Hunter et al.
- That Showdown duplication is higher.
- The late-game FLEX practice (common practice, not cited).
