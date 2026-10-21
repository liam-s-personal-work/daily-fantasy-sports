# LLM agents for sports forecasting and a <60-minute, token-frugal Claude orchestration (VERIFIED EDITION)

> Paths under `scratch/` refer to verification scripts and downloads from the research session; they are not in the repo.

Research note for the DFS lineup engine (repo `liam-clifford/daily-fantasy-sports`, greenfield).
Date: 2026-10-21. Key: `llm_orchestration`. This file replaces `llm_orchestration.md`. A skeptical
reviewer re-checked every claim in it against primary sources.

**Tags used inline**
- `[VERIFIED: url]`: I re-read the claim today on that primary source and it says what is written.
- `[CORRECTED: was X; url]`: the original was wrong or overstated. The text shows the fixed version.
- `[UNVERIFIED]`: I could not confirm it today. Don't build on it, and don't repeat it to the user as fact.
- `(design)`: a recommendation or a computed estimate, not an empirical fact.

**Local copies.** I saved today's docs as Markdown (`<page>.md` on platform.claude.com and
code.claude.com) under
`scratch/verify_llm/`.
The ForecastBench CSVs and wiki are in its `fb/` subfolder and the paper-repo READMEs in `gh/`.

## 0. Verification limits (read first)

- **Reachable today:** `platform.claude.com`, `code.claude.com`, `anthropic.com`, `raw.githubusercontent.com`
  and `github.com`.
- **Not reachable:**
  - Through the session proxy (403 `connect_rejected`) or WebFetch (`ENOTFOUND`): `arxiv.org`,
    `export.arxiv.org`, `openreview.net`, `pmc.ncbi.nlm.nih.gov`, `doi.org`, `semanticscholar.org`,
    `ssrn.com`, `forecastbench.org` and `iclr.cc`.
  - WebSearch: this turn's shared budget of 200 searches was already used up.
- **What that leaves.** Paper results are verified only where the authors' own GitHub README states
  them. Everything else in the literature part stays `[UNVERIFIED]`.
- **Platform facts are well covered.** All Claude platform facts (prices, limits, tool behaviour) were
  re-read on today's docs.

---

## 1. Headline findings (corrected)

1. **LLM forecasters with retrieval beat the public but trail superforecasters.**
   - On today's ForecastBench tournament leaderboard, the best entry with "claude" in its name is
     `claude-opus-5-adaptive-thinking-max-web-search-code-execution-128k†` (rank 58).
     - Brier overall 0.112, against 0.099 for the superforecaster median and 0.135 for the public median.
     - Superforecasters are significantly better (p<0.01). The entry beats the public (p<0.001).
     [VERIFIED: https://raw.githubusercontent.com/forecastingresearch/forecastbench-datasets/main/leaderboards/csv/leaderboard_tournament.csv]
   - 27 rows are marked "Supers > Forecaster? = No". All of them sit in ranks 1–27, out of 463 rows.
     - They are mostly codenamed team submissions whose methods the CSV doesn't describe, for example
       Google DeepMind "fire hedgehog", Anonymous 24 "model-4", Torchcast AI, FutureSearch and Voicetree.
     - Only two are labelled ensembles: NeuroFusion "ensemble 1" and "ensemble 3".
     [CORRECTED: was "mostly scaffolded systems and ensembles"; same CSV]
2. **Without fresh information, LLMs don't beat the public.**
   - The baseline leaderboard is for models that have no context past their training cutoff:
     - Best LLM `o3-2025-04-16-scratchpad`: Brier 0.149.
     - Public median: 0.141. Superforecaster median: 0.106.
   - The CSV only tests "better than public". The best LLM is **not** significantly better. Its point
     estimate is worse, but "worse" isn't tested.
     [CORRECTED: was "LLMs do worse than the public"; https://raw.githubusercontent.com/forecastingresearch/forecastbench-datasets/main/leaderboards/csv/leaderboard_baseline.csv]
   - **Separate tournament-leaderboard evidence:**
     - `claude-opus-4-8-adaptive-thinking-high-24000†` scores 0.140 (N=3,350) and isn't better than the
       public (p=0.80).
     - `claude-opus-4-8-adaptive-thinking-max-web-search-128000†` scores 0.113 (N=1,579).
     - This comparison **confounds effort with web search** and uses different question sets, so it
       can't separate the two effects.
     [CORRECTED: was presented as baseline-leaderboard evidence that "effort and search also matter a lot"; tournament CSV above]
   - Design implication, unchanged: run the fresh-forecast stage only on top of a current fact packet.
     Sweep effort separately from search.
3. **I confirmed no peer-reviewed study of LLM fantasy or sports *point projections*.** Search was
   unavailable, so this absence is `[UNVERIFIED]`. Results on binary events (ForecastBench) don't carry
   over to point projections in size. Treat the LLM forecaster as an unvalidated signal until
   prospective grading shows it adds information over the baseline (§2.6).
4. **"Blind the forecaster, then blend" is the right shape.**
   - The evidence that averaging independent forecasts beats letting one forecaster see the other
     (Halawi 2024; Schoenegger 2024) is `[UNVERIFIED]` here (§2.1).
   - The fixed 85/15 (NFL) and 100/0 (CFB) weights are **not** evidence-based. Use them as priors `w0`
     and learn the weights with the closed-form shrinkage update in §2.6 (design).
5. **Don't treat LLM-stated low/likely/high as calibrated quantiles.**
   - Verbalized LLM confidence tends to be overconfident (Xiong et al.). That paper covers answer
     confidence, not numeric intervals.
     [VERIFIED: https://raw.githubusercontent.com/MiaoXiong2320/llm-uncertainty/main/README.md, authors' abstract]
   - Calibrate interval widths per position from graded data (§2.3).
6. **Look-ahead leakage is the main threat to any backtest.**
   - Reliable knowledge cutoff and training-data cutoff are both **Jun 2026** for Fable 5.1, Opus 5.5,
     Sonnet 5.5 and Haiku 5.5.
     [VERIFIED: https://platform.claude.com/docs/en/about-claude/models/overview]
   - So only games kicking off on or after 2026-07-01 can evaluate LLM stages.
   - As of today those are NFL 2026 **weeks 1–6**: week 7 opens Thu 2026-10-22, and the last completed
     game was 2026-10-19.
     [CORRECTED: was "NFL 2026 weeks 1–7"; sibling `sources_compliance.verified.md` (nflverse `snap_counts_2026`/`stats_player_week_2026` = weeks 1–6)]
   - CFB games from late August to about 2026-10-18 also qualify. I didn't verify the CFB week
     numbering. `[UNVERIFIED]` [CORRECTED: was "CFB weeks 0–8"; 2026-10-24 is the next Saturday]
7. **Run the pipeline itself on the plain Messages API (Python `anthropic`, asyncio).** (design)
   - The claude-api skill maps "multi-step pipelines with code-controlled logic" to "Claude API + tool
     use" [VERIFIED: claude-api skill, bundled 2026-10-06].
   - The Agent SDK or `claude -p --bare` makes a good operator shell around the pipeline (§4.1).
8. **Budget (estimated; see §4.10).**
   - NFL 12-game main slate, main run + last hour + grading:
     - About **$10.6** with the original token estimates.
     - About **$15.7** if output tokens are double those estimates, which is plausible (the forecaster's
       JSON alone is about 5K tokens, not 3K).
     - Add about $1.5–2.7 for an optional second forecaster.
     [CORRECTED: was "$10.59 (≈$12.05 with 2nd forecaster)" with no sensitivity; `calc/budget_verified.py`]
   - Start-tier rate limits aren't binding for one user (§4.7). New organizations may start in a lower
     "Evaluation" tier. [VERIFIED: https://platform.claude.com/docs/en/api/rate-limits]
   - Cost drivers are output (thinking + JSON) tokens and search-result tokens, not the cacheable prompt.
9. **The Batch API (50% off) can't sit on the 60-minute critical path.** The docs promise only that "most
   batches" finish within an hour, and batches expire at 24 h.
   [VERIFIED: https://platform.claude.com/docs/en/build-with-claude/batch-processing]
   Use it for early-week context research, grading and backtests.
10. **Wall clock is gated by the longest single forecaster call.**
    - At 24K output tokens and an assumed 40–70 tok/s, one Opus call takes 5.7–10 min. The speed is
      `[UNVERIFIED]`; measure it.
    - That can overrun an 8-min stage cap. Splitting the forecaster per team, or lowering effort, are
      the levers (§4.9) (design).

---

## PART A — Evidence

### 2.1 LLM forecasting accuracy

**ForecastBench**

- **What it is.** Karger, Bastani, Yueh-Han, Jacobs, Halawi, Zhang and Tetlock, "ForecastBench: A Dynamic
  Benchmark of AI Forecasting Capabilities", ICLR 2025, arXiv:2409.19839. The README says "A dynamic,
  contamination-free benchmark of LLM forecasting accuracy with human comparison groups", and
  "Leaderboards and datasets are updated nightly".
  [VERIFIED: https://raw.githubusercontent.com/forecastingresearch/forecastbench/main/README.md]
- **Tournament leaderboard, fetched 2026-10-21.**
  - Columns: "Overall" is the Brier Index (higher is better). "Brier Overall" is lower-is-better.
  - All rows below are [VERIFIED: https://raw.githubusercontent.com/forecastingresearch/forecastbench-datasets/main/leaderboards/csv/leaderboard_tournament.csv].

  | Entry | Rank | Brier Index | Brier dataset | Brier market | Brier overall | N | Supers better? | Beats public? |
  |---|---|---|---|---|---|---|---|---|
  | Anonymous 24 "model-4" | 1 | 69.7 | 0.105 | 0.078 | 0.092 | 567 | No (p=0.87) | Yes (<0.001) |
  | NeuroFusion "ensemble 1" / "ensemble 3" | 5 | 68.6 | 0.118 | 0.079 | 0.099 / 0.098 | 567 | No (0.51 / 0.52) | Yes |
  | Superforecaster median | 8 | 68.5 | 0.133 | 0.065 | 0.099 | 578 | — | Yes |
  | claude-opus-5 adaptive-thinking max, web-search + code-exec, 128k † | 58 | 66.5 | 0.148 | 0.077 | 0.112 | 1204 | Yes (<0.01) | Yes (<0.001) |
  | claude-opus-4-8 adaptive-thinking max, web-search, 128000 † | 68 | 66.3 | 0.148 | 0.079 | 0.113 | 1579 | Yes (<0.01) | Yes (<0.01) |
  | claude-fable-5 max, web-search + code-exec, 128k | 71 | 66.2 | 0.150 | 0.079 | 0.114 | 1204 | Yes (<0.01) | Yes (<0.01) |
  | Public median | 267 | 63.3 | 0.167 | 0.102 | 0.135 | 578 | Yes | — |
  | claude-opus-4-8 adaptive-thinking high, 24000 † | 303 | 62.6 | 0.178 | 0.101 | 0.140 | 3350 | Yes (<0.001) | No (p=0.80) |
  | claude-opus-4-8, 1024 † | 338 | 61.5 | 0.174 | 0.122 | 0.148 | 5051 | Yes | No (p=0.97) |

  - The † suffix isn't explained in the CSV or the changelog. `[UNVERIFIED]` meaning.
  - Different N means different question subsets, so compare rows with care.
- **Baseline leaderboard** (models with no context past their training cutoff):
  - Superforecaster median 0.106, public median 0.141.
  - Best LLM `o3-2025-04-16-scratchpad` 0.149, not better than public.
  - `claude-sonnet-4-6-adaptive-thinking-16000` 0.155.
  [VERIFIED: https://raw.githubusercontent.com/forecastingresearch/forecastbench-datasets/main/leaderboards/csv/leaderboard_baseline.csv]
- **Method notes from the changelog**
  [VERIFIED: https://raw.githubusercontent.com/wiki/forecastingresearch/forecastbench/Changelog.md; rendered at https://github.com/forecastingresearch/forecastbench/wiki/Changelog]:
  - **2025-10-07:** a difficulty-adjusted Brier score via a "two-way fixed effects model to separate
    forecaster ability from question difficulty". Leaderboard inclusion is delayed "50 days after the
    forecast due date".
  - **2025-10-17:** combination questions were removed, so a question set now has 500 questions.
  - **2026-03-04:** results are displayed as the Brier Index.
  - **2026-05-20:** two-way fixed effects estimate market-question difficulty on the Baseline
    leaderboard, because "models face additional difficulty in that they have absolutely no context
    with which to forecast beyond their training cutoff". [CORRECTED: was quoted as "baseline models
    have no context beyond their training cutoff", which is a paraphrase, not the wording]
  - **2026-08-19:** Kalshi added as a market source, first in the set due 2026-08-30.
- **How ForecastBench works**
  [VERIFIED: https://raw.githubusercontent.com/wiki/forecastingresearch/forecastbench/How-does-ForecastBench-work%3F.md]:
  - Question sets are generated on day D−10. The 500-question LLM set is released at 00:00 UTC on day D.
  - A 200-question human set (a subset) opens on D−10.
  - Resolution files are "not released publicly".
- **What this means for us (design):**
  - With search and high effort, frontier Claude beats the public on binary events but trails
    superforecasters.
  - The top systems are opaque team submissions. Some are ensembles.
  - These are geopolitical, economic and market questions, not fantasy points. The direction of the
    lessons (fresh retrieval, effort, aggregation) probably carries over. The sizes don't.

**Halawi et al. 2024, "Approaching Human-Level Forecasting with Language Models" (NeurIPS 2024; arXiv:2402.18563)**
[CORRECTED: title was "…with Language Model Systems"; https://raw.githubusercontent.com/dannyallover/llm_forecasting/main/README.md]
- The pipeline [VERIFIED: same README]:
  1. LM-generated search queries "to retrieve articles published before a certain date from a news API".
  2. Relevance filtering.
  3. Summarization.
  4. Several sampled reason-and-predict calls.
  5. Aggregation.
  The forecasts are "_simulated_" (retrodictive) forecasts.
- Reported Brier scores: system 0.179 against a crowd of 0.149, and 0.146 for the average of the two.
  The system was relatively better when the crowd was uncertain. `[UNVERIFIED]`, because arXiv was
  unreachable.
- Lesson, if the numbers hold: an LLM combined with an independent crowd or market beats either alone.

**Schoenegger et al. 2024, "Wisdom of the Silicon Crowd" (Science Advances).** `[UNVERIFIED]`: no source
was reachable, and the DOI and arXiv ID (recalled as 2402.19379) are unconfirmed.
- Recalled study 1: a 12-LLM ensemble was not statistically different from a human crowd over a
  3-month tournament.
- Recalled study 2: GPT-4 and Claude 2 improved by about 17–28% when shown the human median, but a
  simple average of human and machine forecasts was more accurate.
- If true, it's the cleanest evidence for "keep it blind, then blend".

### 2.2 Sports- and fantasy-specific LLM evidence

- I confirmed no peer-reviewed or primary study of LLM fantasy-point projection accuracy, for any
  sport. `[UNVERIFIED]`: search was unavailable.
- Candidate live benchmarks to check: "Prophet Arena" (LLMs on Kalshi markets) and "FutureX".
  `[UNVERIFIED]`
- ForecastBench added Kalshi on 2026-08-19 [VERIFIED: changelog above]. Whether its questions include
  sports markets is `[UNVERIFIED]`.
- **Implication (design).** The forecaster must earn its weight on prospective data (§2.6). No method can
  guarantee "top 2.3% in every contest". Track it as a KPI with a test against the 2.3% base rate:
  - `p_value = P(X ≥ k | n, 0.023) = Σ_{i≥k} C(n,i)·0.023^i·0.977^(n−i)`, with a Wilson interval on
    k/n.
  - Contests needed for 80% power at α=0.05 (computed, treating contests as independent): about **294**
    to detect a true rate of 5%, **112** for 7% and **54** for 10%.
  - Several entries on one slate are correlated, so the effective n is smaller.

### 2.3 Calibration of LLM-stated intervals

- Anthropic (2022), "Language models (mostly) know what they know":
  - Larger models "are well-calibrated on diverse multiple choice and true/false questions when they are
    provided in the right format".
  - Models trained to predict P(IK) "struggle with calibration of P(IK) on new tasks".
  - This covers discrete answers, not numeric intervals.
  [VERIFIED: https://www.anthropic.com/research/language-models-mostly-know-what-they-know]
- Xiong, Hu, Lu, Li, Fu, He and Hooi, "Can LLMs Express Their Uncertainty? An Empirical Evaluation of
  Confidence Elicitation in LLMs":
  - "LLMs, when verbalizing their confidence, tend to be overconfident."
  - "Sampling strategies paired with specific aggregators can effectively enhance failure prediction."
  [VERIFIED: https://raw.githubusercontent.com/MiaoXiong2320/llm-uncertainty/main/README.md, which links https://openreview.net/forum?id=gjeQKFxFpZ]
  - The venue (ICLR 2024) and arXiv ID (2306.13063) are `[UNVERIFIED]`.
- I confirmed no study of LLM *numeric interval* coverage (for example "90% intervals cover X%").
  `[UNVERIFIED]` The human analogue is overprecise intervals (Alpert & Raiffa; Soll & Klayman).
  `[UNVERIFIED]`
- **Design rule:** treat the LLM's (low, likely, high) as uncalibrated.
  1. **Cold start** (fewer than about 300 graded player-games per position group):
     - Use the LLM median only.
     - Build quantiles from the baseline's empirical residuals: `q_τ = m_blend + r_τ(pos, proj_bin)`,
       where `r_τ` is the τ-quantile of (actual − baseline mean) over past seasons.
     - Bin by projected mean (or salary tier). Residual spread grows with projection level.
     - The baseline can be backtested on any season because no LLM is involved.
  2. **Warm** (≥ 300 per group): rescale the LLM half-widths on each side separately.
     - `lo' = p50 − a_pos·(p50 − p10)` and `hi' = p50 + b_pos·(p90 − p50)`.
     - `a_pos` and `b_pos` have a **closed form**; no root-finding is needed:
       - `a_pos = Quantile_0.90 of z_lo`, where `z_lo = (p50 − y)/max(p50 − p10, ε)`.
       - `b_pos = Quantile_0.90 of z_hi`, where `z_hi = (y − p50)/max(p90 − p50, ε)`.
       - With these, the empirical P(y < lo') = 0.10 and P(y > hi') = 0.10 on the graded set.
       [CORRECTED: was "fit via 1-D root-finding". Correct but unnecessary, since P(y<lo')=0.1 ⇔ P(z_lo>a)=0.1.]
       Use ε = 0.5 fantasy points so that cases with p10 = p50 don't blow up.
     - Score with pinball loss `L_τ(y,q) = max(τ(y−q), (τ−1)(y−q))` at τ ∈ {0.1, 0.5, 0.9}.
     - After each slate, report coverage and mean pinball loss per position. Residuals are correlated
       within a game, so bootstrap by game.
  3. **Distribution fed to the simulator.**
     - Use the 50/50 two-piece normal **quantile function**: `Q(u) = m + σ_L·Φ⁻¹(u)` for u < 0.5 and
       `m + σ_R·Φ⁻¹(u)` for u ≥ 0.5.
     - Set `σ_L = (m − lo')/1.2816` and `σ_R = (hi' − m)/1.2816`.
     - This reproduces p10, p50 and p90 exactly, and correlated uniforms from a copula plug straight in.
     - [CORRECTED: was "a split-normal … through (lo', m, hi')". A standard split normal has
       P(X < mode) = σ1/(σ1+σ2), so putting m at the mode doesn't make m the median.]
     - **Don't truncate at fixed floors.** On DK and FD a DST's minimum is −4 (35+ points allowed), and
       skill players have no fixed floor. Simulate components (§2.6) and apply scoring in code.
       [CORRECTED: was "truncated at QB/RB/WR/TE ≥ −4, DST ≥ −6 on DK"; sibling `scoring.verified.md` (DK and FD `{min: 35, pts: -4}`)]

### 2.4 Anchoring when the LLM sees market numbers

- Studies of LLM anchoring on numbers placed in the prompt `[UNVERIFIED]`:
  - Lou & Sun 2024, "Anchoring Bias in Large Language Models: An Experimental Study" (recalled
    arXiv:2412.06593).
  - Nguyen 2024 (J. Behavioral & Experimental Finance).
  - Both are recalled as finding that simple "ignore the anchor" instructions only partly help.
- Schoenegger study 2, recalled: models shown the human median moved toward it but underweighted it.
  `[UNVERIFIED]`
- **Implications (design):**
  - **Blind the fresh forecaster to market data:** salaries, site averages, past fantasy points **and**
    betting lines. The NFL baseline already uses the lines, and the blend is worth having only if the
    two inputs err independently. Whether lines count as "prices" in the user's spec is open question 1
    (§8).
  - **Enforce blinding in code, not in the prompt.**
    - The fact-packet schema has no fields for salary, spread, total, implied points, ownership,
      projections or past fantasy points.
    - A sanitizer rejects free-text facts that match any of these patterns:
      `\$\d`, `salary`, `spread`, `o/u`, `over/under`, `implied`, `[+-]\d{3}\b` (moneylines),
      `projected .* points`, `DK|FD .*points`, `\b(favou?red|underdog|odds|vegas|moneyline|line moved)\b`
      and `[+-]\d{1,2}\.5\b` (spreads).
    - It logs every rejection so false positives can be tuned.
    - These are Python `re` patterns run in code. They are *not* JSON-schema `pattern`s: structured
      outputs don't support `\b` or lookarounds.
      [VERIFIED: https://platform.claude.com/docs/en/build-with-claude/structured-outputs]
  - **Residual leakage** remains: the model "knows" stars from pretraining. That's knowledge, not a market
    anchor, and is acceptable.

### 2.5 Look-ahead (training-data) leakage, and how to avoid it

- **Cutoffs:** reliable knowledge cutoff and training-data cutoff are both **Jun 2026** for Fable 5.1,
  Opus 5.5, Sonnet 5.5 and Haiku 5.5.
  [VERIFIED: https://platform.claude.com/docs/en/about-claude/models/overview]
- **Known leakage channels** `[UNVERIFIED]` for the paper citations:
  - **Parametric memory** of outcomes, for any game before the cutoff. See Glasserman & Lin 2023 (recalled
    arXiv:2309.17322) and Sarkar & Vafa 2024, "Lookahead Bias in Pretrained Language Models".
  - **Retrieval leakage:** search returns post-event pages, even with date filters.
  - **Logical leakage** in question construction. See Paleka et al. 2025, "Pitfalls in Evaluating
    Language Model Forecasters".
- **Protocol for this repo (design):**
  1. **Evaluate LLM stages only on games with kickoff ≥ 2026-07-01.**
     - Completed today: NFL 2026 weeks 1–6, and CFB games through about 2026-10-18.
       [CORRECTED: was "NFL weeks 1–7, CFB weeks 0–8"; sibling `sources_compliance.verified.md`]
     - All earlier games, including the whole 2025 NFL season, are contaminated for LLM stages but fine
       for **baseline-only** backtests.
  2. **Prospective freeze.** Every live run writes immutable, timestamped artifacts, each with a `sha256`
     hash:
     - `fact_packet.json`: every fact carries `source_url`, `published_at` and `retrieved_at`.
     - `forecast.json` and `lineups.json`.
     - `run_manifest.json`: the model actually served (`response.model`, which may be a fallback model),
       effort, `prompt_version` and `schema_version`.

     Grading reads only frozen artifacts. They become the gold-standard dataset for learning blend
     weights.
  3. **Reconstructed backtests** of post-cutoff weeks that weren't logged live:
     - Use only point-in-time structured data, with **no web search**: timestamped injury reports, depth
       charts and play-by-play available before kickoff.
     - Mark them `reconstructed=true` and weight them lower.
  4. **Canary test** before a game is used for evaluation:
     - Ask the model, with no tools, for the final score and the top fantasy scorer.
     - If it beats a frequency baseline, exclude the game.
     - For post-cutoff games this mostly guards against an undocumented later cutoff.
  5. **Never use web search inside a backtest.**
     - In live runs, give the search tool `allowed_domains`, and have the research agent stamp each fact's
       publish time.
     - The sanitizer drops facts timestamped after the slate lock, or after the run's `as_of` in replays.

### 2.6 Independent forecaster, or adjuster of a baseline?

Evidence:
- **Combining independent signals beats sequential exposure** (Halawi; Schoenegger study 2).
  `[UNVERIFIED]`
- **LLM backbones in time-series forecasting.** Tan et al., "Are Language Models Actually Useful for Time
  Series Forecasting?" (NeurIPS 2024 Spotlight), ran ablations on three LLM-based time-series methods:
  OneFitsAll, Time-LLM and CALF.
  - "removing the LLM component or replacing it with a simple attention layer did not worsen results".
  - LLMs "do not appear to meaningfully improve performance".
  [VERIFIED: https://raw.githubusercontent.com/BennyTMT/LLMsForTimeSeries/main/README.md]
  - Scope: this concerns LLM *backbones inside numeric models*. It is not about prompting a frontier
    chat model with qualitative news.
- **Zero-shot numeric extrapolation.** Gruver et al. (LLMTime, NeurIPS 2023) extrapolate numbers encoded
  as text, zero-shot. "Models that undergo alignment (e.g. RLHF) do not follow the scaling trend. For
  example, GPT-4 demonstrates inferior performance to GPT-3."
  [VERIFIED: https://raw.githubusercontent.com/ngruver/llmtime/main/README.md]
  These are 2023 models.
- **Judgmental adjustments to statistical forecasts.** Fildes, Goodwin, Lawrence & Nikolopoulos 2009, IJF
  25(1): large, information-driven adjustments help, while small, frequent (often optimistic)
  adjustments hurt. `[UNVERIFIED]`

Synthesis (design):
- **Where the LLM adds value:** qualitative, fresh information. Role changes, injuries and their knock-on
  effects, scheme and coaching notes, weather, and narratives not yet in the stats.
- **Where it doesn't:** arithmetic. Extrapolating volume × efficiency, and scoring rules with bonuses.
- **So the blinded forecaster outputs stat components**: volume and efficiency quantiles, and TD
  probability.
  - Code simulates DK and FD points from the components. One forecast then serves both sites.
  - **Simulate the components jointly, not as independent marginals** (important omission in the
    original). For example: targets → receptions ~ Binomial(targets, catch rate) → yards per catch →
    TDs ~ Poisson(rate × volume), with team-level shares that sum to a team total.
  - Draw independent quantiles per stat and you destroy the target, reception and yards correlation, and
    the stacking correlations the GPP objective needs.
- **Keep the user's two-signal blend, but learn the weight.**
  - Notation: baseline `b`, LLM forecast `f`, outcome `y`, for each player-game in position group `g`.
  - **Encompassing test / optimal weight:** fit `y − b = β·(f − b) + ε`, which gives
    `β̂_g = Σ(f−b)(y−b) / Σ(f−b)²`.
    - β̂ is the optimal weight on `f` within the family `b + w(f−b)`.
    - Use **cluster-robust standard errors by game**: players in one game share errors. If the CI
      includes 0, the forecaster adds nothing for that group.
    - Also fit a diagnostic intercept, `y − b = α + β(f − b)`. A non-zero α flags bias in one of the
      inputs.
  - **Shrunk update toward the configured prior `w0_g`:**
    - Priors are the user's 0.85 for NFL and 1.0 for CFB. Optionally start CFB at 0.5; see open
      question 6.
    - `w_g = clip( (Σ(f−b)(y−b) + λ·w0_g) / (Σ(f−b)² + λ), 0, 1 )`, with `λ = N0 · mean((f−b)²)` and
      `N0 ≈ 200` pseudo-observations.
    - This is the exact minimizer of `Σ(y − b − w(f−b))² + λ(w − w0)²`. Setting d/dw = 0 gives
      `Σ(f−b)(y−b) − wΣ(f−b)² − λw + λw0 = 0`. [VERIFIED: algebra]
  - **Fit on medians or means.** Refit the interval calibration (§2.3) separately.
  - To blend whole distributions, average the quantiles: `q_blend(τ) = w·q_f(τ) + (1−w)·q_b(τ)`.
    That is quantile averaging (Lichtendahl, Grushka-Cockayne & Winkler 2013, Management Science).
    `[UNVERIFIED]` citation.
  - **"Other sports: baseline leads"** just means a small `w0`, for example 0.2. The LLM is then used
    for status and role facts only.
- **Event flags from the research agent:** `role_change`, `new_starter`, `snap_limit`, `weather_severe`.
  - They route a player into the "LLM forecast required" set even when pruning would drop him (§4.8).
  - This is where an LLM is most likely to beat the stats.

### 2.7 Implications for the user's pipeline (summary, design)

| User step | Evidence-based adjustment |
|---|---|
| Fresh forecasts (football), blinded | Keep. Enforce blinding in code. Use Opus 5.5 at `high`, with a rich fact packet (sweep `medium`/`high`/`xhigh`). Output components plus (low, likely, high) |
| Blend 85/15 NFL, 100/0 CFB | Use these as priors `w0`. Learn `w_g` per position from frozen prospective data (closed form, cluster-robust CIs) |
| Low/likely/high | Recalibrate (§2.3). Cold start: LLM median plus the baseline's empirical residual quantiles |
| Three independent drafts | Solver-built (three objectives or perturbations), not LLM-built. LLMs are unreliable at knapsack problems and roster legality. An LLM may propose stack or exposure constraints |
| Grade and update | Encompassing regression plus pinball-loss tracking. Update `w_g`, `a_pos` and `b_pos` after each slate |

---

## PART B — Orchestration with Claude

### 4.1 Which surface?

| Option | What it gives | Fit for this pipeline |
|---|---|---|
| **Messages API + Python `anthropic` SDK (AsyncAnthropic)** | You own the loop: explicit `cache_control`, server-side `web_search`/`web_fetch`, structured outputs (`output_config.format`, `strict` tools), Batch, `usage` per call | **Recommended** for every LLM stage (design) |
| **Claude Agent SDK** (`claude-agent-sdk`) | "Build production AI agents with Claude Code as a library": built-in tools, hooks, subagents, MCP, permissions, sessions [VERIFIED: https://code.claude.com/docs/en/agent-sdk/overview] | Good **operator shell**, such as a `/dfs-run contest.csv` command that runs the Python pipeline. Not needed in the hot path |
| **`claude -p` headless** | `--output-format json`/`stream-json`, `--json-schema` (output in `structured_output`), `--bare` ("the recommended mode for scripted and SDK calls"; skips hooks, skills, commands, subagents, plugins, MCP, auto memory, CLAUDE.md), `--max-budget-usd`, `--allowedTools`, `--permission-mode` [VERIFIED: https://code.claude.com/docs/en/headless] | Same as the Agent SDK. Useful for cron and CI wrappers |
| **Managed Agents** | Hosted loop and sandbox. $0.08 per session-hour; the Batch discount doesn't apply [VERIFIED: https://platform.claude.com/docs/en/about-claude/pricing]. 300 create and 1,200 read requests/min [VERIFIED: https://platform.claude.com/docs/en/api/rate-limits] | Overkill. No long-lived workspace is needed |

Agent SDK facts, if it's used for an operator or dev agent:
- **Login.** "Unless previously approved, Anthropic does not allow third party developers to offer
  claude.ai login or rate limits for their products … Use the API key authentication methods."
  [VERIFIED: https://code.claude.com/docs/en/agent-sdk/overview] [CORRECTED: was "must use API keys",
  without the "unless previously approved" qualifier]
- **`AgentDefinition` fields** include `description`, `prompt`, `tools`, `disallowedTools`, `model`,
  `maxTurns` and `effort`.
- **Limits.** Default concurrency is 20 subagents (`CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS`) and default
  depth is 3 (`CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH`).
  - `max_budget_usd` has no default. When hit, it ends the query with `error_max_budget_usd`.
  - These limits are documented for TypeScript SDK v0.3.219+ and Python SDK v0.2.127+.
  [VERIFIED: https://code.claude.com/docs/en/agent-sdk/subagents]
- **Structured output.** `output_format` JSON Schema, validated as draft-07. The SDK re-prompts on
  mismatch; final failure gives the `error_max_structured_output_retries` subtype.
  [VERIFIED: https://code.claude.com/docs/en/agent-sdk/structured-outputs]
- **Cost.**
  - `total_cost_usd` is a "client-side estimate, not authoritative billing data". Per-step
    `output_tokens` is a placeholder, so read it from the result message.
  - Prompt caching is automatic. `ENABLE_PROMPT_CACHING_1H` requests the 1 h TTL.
  [VERIFIED: https://code.claude.com/docs/en/agent-sdk/cost-tracking]

### 4.2 Model tiers, effort and API rules (current models)

Prices per MTok [VERIFIED: https://platform.claude.com/docs/en/about-claude/pricing]:

| Model | Input | Output | 5m cache write | 1h cache write | Cache read | Batch in / out |
|---|---|---|---|---|---|---|
| Opus 5.5 `claude-opus-5-5` | $4 | $20 | $5 | $8 | $0.20 (0.05×) | $2 / $10 |
| Sonnet 5.5 `claude-sonnet-5-5` | $2 | $10 | $2.50 | $4 | $0.10 (0.05×) | $1 / $5 |
| Haiku 5.5 `claude-haiku-5-5` (prompt ≤100K) | $0.10 | $0.50 | $0.125 | $0.20 | $0.01 | $0.05 / $0.25 |
| Haiku 5.5 (prompt >100K) | $0.50 | $2.50 | $0.625 | $1 | $0.05 | $0.25 / $1.25 |
| Fable 5.1 `claude-fable-5-1` | $10 | $50 | $12.50 | $20 | $0.25 (0.025×) | $5 / $25 [CORRECTED: was "—"] |

Notes on the table:
- **Haiku's 100K threshold** counts "all of its input tokens, including cache reads and cache writes".
  Each request is priced on its own. [VERIFIED: pricing page]
- **Multipliers.**
  - US-only `inference_geo` costs 1.1× on Claude 4.6+ models.
  - Fast mode on Opus 5.5 is $8 / $40 and isn't available with Batch.
  - Caching and Batch discounts stack.
  [VERIFIED: pricing page]
- **The claude-api skill's cached text** lists Sonnet 5.5 cache reads at $0.20. Today's pricing page says
  **$0.10**, and the live page wins.

Models overview [VERIFIED: https://platform.claude.com/docs/en/about-claude/models/overview]:
- Comparative latency: Fable 5.1 "Slower", Opus 5.5 "Moderate", Sonnet 5.5 "Fast", Haiku 5.5 "Fastest".
- Every model has a 1M context window and 128K max output (synchronous). On Batches, up to 300K output
  with the `output-300k-2026-03-24` beta.
- Default effort: Fable 5.1 `high`, Opus 5.5 `medium`, Sonnet 5.5 `high`, Haiku 5.5 `medium`.
- The Haiku description reads "high-volume, latency-sensitive tasks such as classification, extraction,
  and routing".

Recommended assignment (design). Per the claude-api skill, first measure "the most capable model at
lower effort" before building a cascade: caches are model-scoped.
[VERIFIED: claude-api skill; model scoping of caches isn't stated on the prompt-caching page]

| Stage | Model / effort | Why |
|---|---|---|
| CSV, rules and user-rule parsing; name-match fallback | Haiku 5.5, `low` (most of this is plain code) | Extraction |
| Per-game research with web tools | Sonnet 5.5, `medium` | Tool-heavy and parallel. Half Opus's price |
| Fresh forecaster (blinded) | Opus 5.5, `high` (sweep `medium`/`xhigh`) | Judgment-heavy |
| Optional 2nd forecaster (ensemble diversity) | Sonnet 5.5, `high` | A different model, so errors are more independent. About +$1.5–2.7 per NFL slate |
| Strategist, checker | Opus 5.5, `high` / `medium` | Adversarial reasoning |
| Backup reviewer (may adjust numbers) | Sonnet 5.5, `high` | Model diversity from the main path |
| Last-hour news triage | Haiku 5.5 if web search works on it, otherwise Sonnet 5.5 `low` | Check `capabilities.server_tools.web_search.supported` with the Models API [VERIFIED: models overview]. Haiku 5.5 support is `[UNVERIFIED]`. If Haiku lacks programmatic tool calling, `web_search_20260209+` needs `allowed_callers: ["direct"]` or it returns a 400 [VERIFIED: https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool] |
| Fable 5.1 | Not by default | 2.5× Opus's price. Start-tier limits are 500K ITPM / 100K OTPM (shared by Fable 5.1 and 5), against Opus 5.5's 2M / 400K [VERIFIED: rate-limits page]. A/B it on the strategist only |

API rules that bite:
- **Opus 5.5 thinking.** Thinking can't be disabled: `{type: "disabled"}` or `budget_tokens` returns a 400.
  Control it with `output_config.effort`. The default is `medium`, so set it explicitly.
  [VERIFIED: https://platform.claude.com/docs/en/models/opus-5-5/whats-new-opus-5-5]
- **Forced tool choice.** `tool_choice` `any`/`tool` returns a 400 on Opus 5.5 and Sonnet 5.5, including
  on `count_tokens`. Use `auto` plus `strict: true` and steer from the prompt, or use
  `output_config.format`.
  [VERIFIED: whats-new-opus-5-5; https://platform.claude.com/docs/en/models/sonnet-5-5/whats-new-sonnet-5-5]
- **Sampling parameters.** On Opus 4.7 and later, including Opus 5.5, a *non-default* `temperature`,
  `top_p` or `top_k` returns a 400. The Python SDK v1.0 doesn't define them at all.
  [CORRECTED: was "removed (400)"; https://platform.claude.com/docs/en/models/opus-5-5/migration-guide]
  Sonnet 5.5 and Haiku 5.5 also reject non-default values [VERIFIED: claude-api skill]. Diversity has to
  come from prompts or models, not temperature.
- **Prefill.** Assistant prefill returns a 400. Use structured outputs. [VERIFIED: opus-5-5 migration guide]
- **Refusals.** Handle `stop_reason: "refusal"`.
  - On Opus 5.5 and Sonnet 5.5 (Claude API), add `betas=["server-side-fallback-2026-07-01"]` and
    `fallbacks="default"`.
  - `fallbacks` isn't supported on Message Batches: "a batch item that includes it comes back as an
    errored result". [CORRECTED: was "rejected on the Batch API"]
  - **Haiku 5.5 has no server-side fallback.**
  [VERIFIED: https://platform.claude.com/docs/en/build-with-claude/refusals-and-fallback]
  - A fallback rescue bills at the fallback model's own rates, so the Ledger must price by the model that
    actually served [VERIFIED: claude-api skill README]. The shape of `usage.iterations` is `[UNVERIFIED]`.
- **Server-tool loops.**
  - Handle `pause_turn`: "Pass the paused response back as-is … Include the same tools in the
    continuation request". Cap the number of continuations.
    [VERIFIED: https://platform.claude.com/docs/en/agents-and-tools/tool-use/server-tools]
  - Also handle `max_tokens`.
- **Preserved thinking.** Thinking blocks are tied to the model and the conversation. Keep every loop
  **append-only**: never edit, strip or truncate earlier turns. Accounts created on or after 2026-08-31
  get a 400 on edited history. [VERIFIED: whats-new-opus-5-5 and claude-api skill]
- **Streaming.** Stream any request whose `max_tokens` is above about 16K, using `.get_final_message()`.
  [VERIFIED: claude-api skill]
- **Effort and caching.** Setting effort explicitly *to the model's default* is equivalent to omitting it
  and doesn't invalidate the cache.
  [VERIFIED: https://platform.claude.com/docs/en/build-with-claude/prompt-caching]

### 4.3 Structured outputs and data contracts

Platform facts [VERIFIED: https://platform.claude.com/docs/en/build-with-claude/structured-outputs]:
- **How to call it.** Use `output_config.format` with `json_schema`, or `client.messages.parse(...)`.
  The old `output_format` is deprecated; the Python SDK v1.0 raises a `TypeError` for it on `beta`
  create.
- **Not supported in schemas:**
  - recursive schemas;
  - numeric constraints (`minimum`/`maximum`/`multipleOf`) and string length;
  - array constraints beyond `minItems` 0 or 1;
  - `additionalProperties` other than `false`;
  - complex enum types and external `$ref`;
  - in regex `pattern`s: backreferences, lookaround and `\b`.
- **Explicit limits:** 20 strict tools per request, **24 optional parameters** in total and **16
  union-type parameters**, both counted across all strict schemas in the request.
- **Other limits:** a "Schema is too complex for compilation" 400 from an internal grammar-size limit, and
  a 180 s compile timeout.
- **Grammar cache.** Grammars are cached 24 h from last use, and the first use adds latency. Changing
  the schema structure, or the tool set when tools are combined, invalidates it. Changing only
  name/description doesn't.
- **Changing `output_config.format`** "will invalidate any prompt cache for that conversation thread".
- **Incompatible** with document citations (400) and with prefill.
- **Property order.** Required properties are emitted before optional ones.
- **Enum casing** isn't guaranteed, so compare case-insensitively.
- **Reasoning fields.** A property that asks for "thinking or step-by-step reasoning may lead to a
  `reasoning_extraction` refusal". Ask for a short explanation instead.
- **Grammar scope.** Grammars apply only to direct output, not to thinking, so the model can think freely.

Consequences for schema design (design):
- Make **every field required** to stay under the 24-optional limit. Use fixed lists of
  `{stat, p10, p50, p90}` rather than many optional keys.
- Validate in code (Pydantic), because the grammar can't: `p10 ≤ p50 ≤ p90`, non-negative counts (yards
  may be negative), and every `key_fact_ids` entry existing in the packet.
- Name free-text fields `rationale_short` and cap them in the prompt (≤ 40 words). Never ask for a
  "reasoning" field.
- **Research calls use server web tools.**
  - Web-search citations "are always enabled" [VERIFIED: web-search-tool page]. Whether `web_search`
    combined with `output_config.format` is accepted is `[UNVERIFIED]`.
  - Avoid the question: the research agent ends by calling a **strict client tool**,
    `submit_fact_packet` (`strict: true`, `tool_choice: auto`). The prompt tells it to call the tool
    exactly once, as its last action.
  - If Claude calls it in the same parallel group as a server tool, "the API does not run the server
    tool. It returns immediately", with `stop_reason: "tool_use"`.
    [VERIFIED: https://platform.claude.com/docs/en/agents-and-tools/tool-use/server-tools]
  - Accept the packet and stop. The unfinished server call is simply abandoned.

Minimal contracts (Pydantic sketch; all fields required). [CORRECTED: research runs once per *game*, so it
now emits one `GamePacket` holding two `TeamPacket`s. The original's "FactPacket per team-game" didn't
match the 12 per-game research calls.]

```python
class Source(BaseModel):
    source_id: str; url: str; publisher: str; published_at: str; retrieved_at: str

class Fact(BaseModel):
    fact_id: str; text: str            # <= 30 words, no market numbers (sanitizer)
    kind: Literal["status","role","usage","scheme","matchup","weather","narrative"]
    source_ids: list[str]; as_of: str

class PlayerFacts(BaseModel):
    player_id: str; name: str; team: str; pos: str
    status: Literal["active","questionable","doubtful","out","ir","suspended","unknown"]
    flags: list[Literal["role_change","new_starter","snap_limit","returning","weather_severe","none"]]
    facts: list[Fact]

class TeamPacket(BaseModel):
    team: str; opponent: str; team_facts: list[Fact]; players: list[PlayerFacts]

class GamePacket(BaseModel):          # one per game; written by the research agent via the strict tool
    game_id: str; kickoff_utc: str; game_facts: list[Fact]
    teams: list[TeamPacket]           # exactly 2, checked in code (minItems>1 isn't supported)
    sources: list[Source]

class StatQ(BaseModel):
    stat: Literal["pass_att","pass_yds","pass_td","int","rush_att","rush_yds","rush_td",
                  "targets","rec","rec_yds","rec_td","fumbles_lost","two_pt",
                  "dst_pa","dst_sacks","dst_int","dst_fr","dst_td"]
    p10: float; p50: float; p90: float

class PlayerForecast(BaseModel):
    player_id: str; components: list[StatQ]
    fp_canonical: StatQ        # sanity check only; code recomputes DK/FD points from the components
    rationale_short: str; key_fact_ids: list[str]

class TeamForecast(BaseModel):
    team: str; team_plays_p50: float; team_pass_rate_p50: float
    team_points: StatQ; players: list[PlayerForecast]

class GameForecast(BaseModel):
    game_id: str; teams: list[TeamForecast]

class Removal(BaseModel):
    player_id: str; fact_id: str; reason: Literal["out","inactive","doubtful_no_role","suspended","other_fact"]

class CloseCall(BaseModel):
    player_ids: list[str]; explanation_short: str; deciding_fact_ids: list[str]

class WeakestPick(BaseModel):
    player_id: str; explanation_short: str; fact_ids: list[str]

class StrategistDecision(BaseModel):  # cannot change numbers
    removals: list[Removal]; close_calls: list[CloseCall]; weakest_pick: WeakestPick

class Attack(BaseModel):
    claim: str; severity: Literal["fatal","major","minor"]
    check: Literal["status_out","inactive","role_loss","weather","other"]   # maps to a code-checkable assertion
    evidence_urls: list[str]

class PickAttacks(BaseModel):
    player_id: str; attacks: list[Attack]

class CheckerReport(BaseModel):
    picks: list[PickAttacks]

class NewsDelta(BaseModel):
    player_id: str; change: Literal["status","role","weather","lineup_note","none"]
    new_status: str; source_url: str; published_at: str; affected_player_ids: list[str]
```

JSON-size check (computed, design):
- A `GameForecast` for 20 players with 8 components each serializes to about 16.1K characters.
- That is about **4.6–5.4K output tokens** at 3.0–3.5 characters per token.
- [CORRECTED: was "about 3K of JSON for about 20 players"; `calc/budget_verified.py`]

Code-side gates (deterministic, outside the LLM; design):
- **Strategist removals.** Accept one only if its `fact_id` exists in a `GamePacket` and the fact is either
  `kind == "status"` with a hard status, or a role fact carrying a `role_change` flag. Cross-check hard
  statuses against the code adapter's official injury or inactive data whenever it's available.
- **Checker verdict, decided in code.**
  - **Fail** if any `fatal` attack's `check` is *confirmed by code*: the status adapter or a re-fetched
    allowlisted source shows out or inactive.
  - **Fail** if one pick has ≥ 2 `major` attacks with allowlisted evidence.
  - A URL alone is never enough (design tightening).
  - Allow at most 2 revisions. A revision changes only the excluded set and re-solves.
- **Last-hour switch rule.** Switch only if a pick is `out` or inactive, or if
  `obj(new) > 1.01 · obj(current)`.
  - Compute both objectives on the **same blended numbers and the same random draws** (common random
    numbers).
  - Without that, Monte Carlo noise swamps the 1% margin. With p = 0.023, the relative SE of
    P(top 2.3%) is 6.5% at 10K sims, 4.6% at 20K and 2.9% at 50K (computed).
  - If the objective is a probability or can be ≤ 0, use an absolute margin with a paired-difference SE
    instead of the ratio.

### 4.4 Server-side web search and web fetch

**Web search** [VERIFIED: https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool ; price: https://platform.claude.com/docs/en/about-claude/pricing]
- **Price:** $10 per 1,000 searches, plus tokens. Results count as input "in search iterations executed
  during a single turn and in subsequent conversation turns". Errored searches aren't billed.
- **Versions:** `web_search_20250305` (basic), `_20260209` (dynamic filtering, Claude 4.6+) and
  `_20260318` (adds `response_inclusion`).
- **`allowed_callers`.** On `_20260209+` it defaults to `["code_execution_20260120"]`. Models without
  programmatic tool calling must set `["direct"]`.
- **Code execution cost.** It's free when `web_search_20260209+` or `web_fetch_20260209+` is in the request.
  Otherwise there are 1,550 free hours per org per month, then $0.05 per container-hour, with a 5-minute
  minimum.
  [VERIFIED: pricing page]
- **Parameters:**
  - `max_uses`;
  - `allowed_domains` **or** `blocked_domains`, not both (400). Bare domains, optional path, no scheme;
  - `user_location`.
- **Sizing:** "Simple factual queries typically use 1–3 searches; comparative or multientity research can
  use 10 or more."
- **`response_inclusion: "excluded"`** drops nested result blocks that a completed code execution
  consumed, "reducing output token costs". Direct-call results are always returned in full.
- **Org setting:** an admin can disable web search in the Console. Requests then fail with a 400.

**Web fetch** [VERIFIED: https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-fetch-tool]
- **Price:** no extra charge beyond tokens. "Average web page (10 kB): ~2,500 tokens; Large documentation
  page (100 kB): ~25,000; Research paper PDF (500 kB): ~125,000."
- **Parameters:**
  - `max_uses`: no default limit, and failed fetches count against it;
  - `allowed_domains`/`blocked_domains`;
  - `max_content_tokens`, approximate. It "applies to text content, not to binary content such as PDFs":
    **official PDF injury reports aren't capped**, so prefer HTML or code adapters;
  - `citations`, off by default;
  - `use_cache` (`_20260309+`, default true). Set it to false for last-hour news;
  - `response_inclusion` (`_20260318+`) and `url_sources`.
- **URL rule.** Claude can fetch only URLs that appeared earlier in the conversation: user messages,
  client-tool results, or earlier search and fetch results. It "cannot fetch URLs that appear only in
  Claude's own output or only in the system prompt". Put the official injury-report URLs in the user
  message.
- **Error codes:**
  - `url_not_allowed` covers domain filters and "Anthropic-side restrictions, such as private addresses,
    `robots.txt`". This backs the user's "only sites that allow automated access" rule for server-side
    fetches;
  - `url_not_in_prior_context`, `url_too_long` (over 250 characters), `unsupported_content_type` (only
    text, HTML and PDF), `max_uses_exceeded` and `too_many_requests`.
- **Unsupported:** JavaScript-rendered sites.
- **Caching:** fetch results are cached server-side by default.

**Domain filtering** [VERIFIED: https://platform.claude.com/docs/en/agents-and-tools/tool-use/server-tools]
- Subdomains are included automatically.
- Wildcards are allowed only in the path.
- Paths work for web search, but "Web fetch matches on the domain only: an entry that includes a path
  never matches a web fetch URL".
- Request-level `allowed_domains` must be a subset of the org's allowlist, or the request fails
  validation.

**Our own Python data adapters** (design): official injury and inactive feeds, depth charts, odds,
weather and nflverse game logs.
- They run client-side, so they must check `robots.txt` themselves (`urllib.robotparser`) as well as the
  site's ToS.
- Keep the domain allowlist in config, with the date each domain was checked and a note.
- Use the same allowlist for the server tools' `allowed_domains`.

**Prompt-injection hygiene** (design; omitted in the original):
- Web pages can carry instructions or fake "news".
- Mitigations:
  - allowlisted domains only;
  - a strict-tool output schema;
  - code-side cross-checks of hard statuses against official adapters;
  - the forecaster sees only sanitized, structured facts, never raw page text;
  - facts whose source isn't on the allowlist are dropped.

### 4.5 Prompt caching

[VERIFIED: https://platform.claude.com/docs/en/build-with-claude/prompt-caching unless noted]
- **Matching.** Caching is a prefix match over `tools` → `system` → `messages`. A change at one level
  invalidates that level and every later level.
- **Breakpoints:** at most **4**. Automatic top-level `cache_control` uses one slot.
- **Lookback.** Each breakpoint looks back at most 20 blocks for an earlier entry. Writes happen only at
  breakpoints.
- **Minimum cacheable prefix:** 512 tokens for Fable 5.1, Opus 5.5, Opus 5, Sonnet 5.5, Fable 5 and Haiku
  5.5. Shorter prefixes silently don't cache.
- **TTL.**
  - 5 min by default, "refreshed for no additional cost each time the cached content is used".
  - The lifetime runs from the *start* of the request, so generation time counts against it.
  - A 1 h TTL costs 2× input to write (5 m writes cost 1.25×).
- **Read price** [VERIFIED: pricing page]: 0.05× input on Opus 5.5 and Sonnet 5.5, 0.025× on Fable 5.1,
  0.1× elsewhere.
- **Parallel requests.** "A cache entry only becomes available after the first response begins". See
  the fan-out pattern in §4.12.
- **Pre-warming with `max_tokens: 0`.** It writes the cache and returns immediately.
  - It's rejected with `stream: true`, `thinking.type: "enabled"`, `output_config.format`, forced
    `tool_choice`, or inside a Batch.
  - The pre-warm must use the **same thinking config and effort** as the real calls.
  - It *is* allowed for the research stage, which uses a strict client tool, not `output_config.format`.
    [CORRECTED: was "pre-warming otherwise isn't worth it here". For research it's the simplest
    fan-out pattern.]
- **Isolation.** Caches are per workspace on the Claude API.
- **Invalidators:**
  - tool-definition changes (everything);
  - the web-search toggle (system and messages);
  - the citations toggle;
  - `speed`;
  - `tool_choice` and images (messages);
  - thinking or effort changes (messages always; tools and system depending on the model).
  - Changing `output_config.format` invalidates the thread's cache [VERIFIED: structured-outputs page].
- **Server-tool results** (web search, web fetch, code execution) get an automatic 5-minute breakpoint
  before the next loop iteration, **only when the request already has at least one `cache_control`
  marker**. These writes appear under `cache_creation.ephemeral_5m_input_tokens`.
  [VERIFIED: https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-use-with-prompt-caching]
- **Verification.** Check `usage.cache_read_input_tokens`. Total input =
  `input_tokens + cache_creation_input_tokens + cache_read_input_tokens`.
- **Rate limits.** Cache reads "do not count toward ITPM for most models". Only `input_tokens` and
  `cache_creation_input_tokens` count. [VERIFIED: rate-limits page]

How much caching matters here (computed, design):
- The shared static prefixes (role prompt, rubric, tool definitions) are about 3–6K tokens per role.
- Caching them across 12 parallel calls saves about **$0.08** per stage for Sonnet research (4K × 11 reads)
  and about **$0.21** for the Opus forecaster (5K × 11).
  [CORRECTED: was "$0.03–$0.10 per stage"]
- The bigger savings come from the **server-tool loop**: each search iteration re-reads a growing context
  at 0.05×, thanks to the automatic breakpoints. Put one `cache_control` on the system prompt so they
  trigger.
- Keep prefixes stable:
  - Freeze the system prompt: no dates or IDs; put the slate date in the user turn.
  - Sort tools deterministically.
  - Serialize all prompt JSON with `sort_keys=True`.
  - Keep thinking config and effort identical within a fan-out.

### 4.6 Batch API: compatible with a 60-minute window?

[VERIFIED: https://platform.claude.com/docs/en/build-with-claude/batch-processing]
- **Discount:** 50% off input and output.
- **Size:** at most 100,000 requests or 256 MB per batch.
- **Timing.**
  - "Most batches completing within 1 hour". Results arrive when everything finishes or after 24 h.
  - Unfinished requests expire, unbilled. Results are kept 29 days.
  - "Processing may be slowed down based on current demand."
- **Supported:**
  - All server tools. Web search is throttled per organization inside batches and retried automatically.
  - Caching stacks with the discount, but hits are "best-effort … typically … 30% to 98%". The docs say
    to "consider using" the 1 h TTL.
- **Not supported:** `stream: true`, `speed` (fast mode) and `max_tokens: 0`. `fallbacks` makes the item
  an errored result [VERIFIED: refusals-and-fallback page].
- **Spend limits.** Batches "may go slightly over" a workspace spend limit.

Verdict (design): keep Batch off the critical path. Use it for:
- (a) **T−72h to T−24h team-context research** (the "season dossier"), reused as cached context;
- (b) **post-contest grading and failure attribution**;
- (c) **prompt and eval sweeps, and reconstructed backtests**.

### 4.7 Rate limits and parallelism

[VERIFIED: https://platform.claude.com/docs/en/api/rate-limits]

| Tier | Opus 5.5 / Sonnet 5.5 / Haiku 5.5 (each its own limit) | Fable 5.x (5.1 and 5 combined) | Batch queue (all models) | Monthly spend cap |
|---|---|---|---|---|
| Start | 1,000 RPM; 2M ITPM; 400K OTPM | 1,000 RPM; 500K ITPM; 100K OTPM | 1,000 RPM; 200K in queue; 100K per batch | $500 |
| Build | 5,000 RPM; 5M ITPM; 1M OTPM | 2,000 RPM; 1.5M ITPM; 300K OTPM | 2,000 RPM; 300K in queue | $1,000 |
| Scale | 10,000 RPM; 10M ITPM; 2M OTPM | 4,000 RPM; 4M ITPM; 800K OTPM | 4,000 RPM; 500K in queue | $200,000 |

- **Evaluation tier.** New organizations "may start in the Evaluation tier, with limits below the
  standard limits" (omitted in the original).
- **Algorithm and bursts.** Rate limiting uses a token bucket. Limits may be enforced over shorter
  intervals, and sharp ramps can trip acceleration limits.
- **OTPM** is counted in real time from tokens actually generated. "The `max_tokens` parameter does not
  factor into OTPM", so a large `max_tokens` costs nothing in rate-limit terms.
- **Spend cap.** At the cap the API returns 429 `enforced_spend_limit_reached` until the 1st of the next
  month. A self-set limit returns a 400 instead.

Peak load for the NFL research fan-out (12 parallel Sonnet calls; computed, design):
- **Input:** about 12 × (12K uncached + 30K cache writes) ≈ 0.5M over about 3 min, or about 0.17M ITPM.
  That's about 8% of Start.
- **Output:** 12 streams × about 80 tok/s ≈ 58K OTPM, about 14% of Start. The speed is `[UNVERIFIED]`.
- **Verdict:** not binding at Start. The spend cap matters more: at about $11–16 per NFL slate, $500 covers
  about 31–45 slates a month.

Client-side controls (design):
- **Concurrency:** one `asyncio.Semaphore` per model (Sonnet 16, Opus 12, Haiku 32).
- **Retries:** `AsyncAnthropic(max_retries=3)`. The SDK retries connection errors, 408, 409, 429 and
  ≥500, with a default of 2 retries. A timeout can stretch wall clock to `timeout × (max_retries+1)`, so
  set per-request timeouts. [VERIFIED: claude-api skill]
- **Rate-limit headers:** read `anthropic-ratelimit-*` through `.with_raw_response` (awaited in SDK 1.x).
- **Server-side spend limit:** set a workspace spend limit in the Console as a hard backstop to the
  client-side Ledger.
- **Per-stage hard timeouts with degradation:**
  - Research: 6 min, then the code-only dossier.
  - Forecaster: 8 min, then baseline-only for that game, flagged in the output.

### 4.8 Player-pool pruning before LLM stages

Purpose: forecaster output tokens scale with the number of players.

Typical counts:
- A DK NFL main-slate salary file has about 500 rows. One Oct 2026 main slate had **513**.
  [VERIFIED: sibling `site_formats.verified.md` §1.3, "228 of 513 rows in the Oct 2026 NFL main slate"]
- Each game's LLM call should cover about 18–22 relevant players.

Pruning rules (design; starting values to tune on graded data):
1. **Drop** players marked `O`, `IR`, `SUSP` or `NA` in official or site status, plus anyone the research
   packet rules out after the code cross-check.
2. **Depth caps per team.**
   - NFL: QB1 (plus QB2 if QB1 is questionable or flagged), RB1–3, WR1–5, TE1–2, DST, and K (Showdown
     and FD only).
   - CFB: QB1–2, RB1–3, WR1–5, TE1–2.
3. **Value floor:** keep a player if `baseline_p90 ≥ v_pos · salary / 1000`, with `v_pos` = 2.0 (DK) or
   1.6 (FD) as starting values.
4. **Always keep** players flagged `role_change`, `new_starter` or `returning`, and anyone in the user's
   must-include rules.
5. **Solver-side dominance pruning, after blending.**
   - Player B at slot set S_B is dominated if at least `k` players A satisfy all of:
     - same team as B;
     - eligible for every slot in S_B (`S_A ⊇ S_B`);
     - salary_A ≤ salary_B, mean_A ≥ mean_B and p90_A ≥ p90_B.
   - Here `k` is the maximum number of players who can fill those slots in one legal lineup (flex
     included).
   - [CORRECTED: was "at least k_p players A at position p … a monotone objective can never prefer them".
     The exchange argument (swap B for an absent dominator) is valid only for a **separable mean**
     objective, and only if the swap keeps slot eligibility, team limits, game minimums, stacks and user
     rules. Same-team dominators keep the team and game constraints intact.]
   - For simulation-based GPP objectives (correlation and ownership), any pruning is heuristic. Use a
     looser cut, such as the top N per position per salary band.
6. **Report the pruned list** in the output, with reasons. Pruned players get no LLM forecast and are
   excluded from solving unless flagged back in.

Expected pool sizes (estimated):
- NFL 12-game: about 240 players reach the forecaster (about 20 per game).
- CFB 8-game: about 175.
- Showdown: about 30.

### 4.9 Wall-clock plan (main run)

Latency assumptions are `[UNVERIFIED]` and must be measured in the first dry run:
- Output speed: Opus 5.5 about 40–70 tok/s, Sonnet 5.5 about 60–100, Haiku 5.5 about 150+.
- Web search: about 2–4 s per search.

| Step | Work | Parallelism | Typical | p95 / cap |
|---|---|---|---|---|
| 0 | Parse CSV, load site rules and scoring (code); start adapters | — | 0.5 min | 1 |
| 1 | Data adapters: injuries, depth, schedule and weather, odds (baseline only), game logs; daily HTTP cache | async HTTP, polite rate limits | 2–3 min | 5 |
| 2 | Research: `max_tokens: 0` pre-warm, then 12 Sonnet calls (streamed) | 12 | 3–4 min | cap 6, then fallback |
| 2b | Baseline (code), in parallel with step 2 | — | <1 min | 1 |
| 3 | Sanitize packets, prune pool (code) | — | <0.5 min | — |
| 4 | Forecaster: 12 Opus `high` calls (first streamed, the rest fanned out at its first event), plus an optional 12 Sonnet | 12–24 | 3–5 min at 12K output; **6–10 min at 24K** | cap 8, then baseline-only for that game |
| 5 | Blend, calibrate, simulate (10–50K sims, common random numbers) (code) | vectorized | 1 min | 2 |
| 6 | Three solver drafts plus one Sonnet strategy-constraints note | 3 | 1–2 min | 3 |
| 7 | Strategist (Opus), then solver | 1 | 2–3 min | 4 |
| 8 | Checker (Opus, `web_search` max 9), code verdict, up to 2 revisions; backup reviewer (Sonnet) in parallel | 2 | 3–4 min (one pass) | 12 (three passes) |
| 9 | User-rule lineup (Haiku parse, then solver) | — | <1 min | 1 |
| **Total main** | | | **≈ 22–27 min** | **≈ 40–45 min** |

- **If the forecaster runs long** (design): split it per team, as 24 calls of about 10 players each.
  That roughly halves the longest call.
  - Give both team calls the same blinded game-context facts, so pace and script stay coherent.
  - Or drop to `medium` effort for that slate. Either way, decide from measured `usage.output_tokens`.
- **Global deadline** (design): a scheduler that stops revising at T_main = 40 min and ships the best
  validated lineup.

Last hour: a separate trigger, at about T−75 to T−45 min before the relevant lock.
1. Official inactives and status (code), about 1 min.
2. News triage per team: 24 calls, at most 2 searches each, `use_cache: false` on fetch. About 2–3 min.
3. Compute the affected set.
4. Re-forecast only the affected games, about 4 min (up to 8 at 24K output).
5. Re-blend, re-solve with locked players fixed, and apply the 1% rule under common random numbers.
   About 1 min.
6. One checker pass, about 3 min.

That totals about 10–12 min.

Timing caveats:
- **NFL inactives** are released about 90 minutes before each kickoff. `[UNVERIFIED]` by me. The sibling
  `projections.md` cites https://www.atlantafalcons.com/news/starters-and-inactives-falcons-at-49ers-16242019
  (medium).
- **Late swap.** Run the last-hour step **per kickoff window** (early, late, night).
  - DK Classic allows late swap and DK Showdown locks as a whole, per sibling `pipeline_critique.md`
    (medium; DK rules JSON).
  - Each extra window pass adds about 10 min of compute.
  - So the "under 60 minutes" budget covers the main run plus the first window pass (about 35 min
    typical, about 57 worst case). Later window passes are separate short runs.

### 4.10 Token and dollar budgets

Computed with `calc/budget.py` (original) and `calc/budget_verified.py` (re-check) in
`scratch/calc/`.
- Prices are [VERIFIED: pricing page]. I re-ran the arithmetic and it reproduces.
- Per-call token counts are **the author's estimates** (design). Replace them with measured `usage` after
  the first dry run.

Assumptions per call (base case):
- **Research (Sonnet `medium`):**
  - 4K static prefix (cached) plus an 8K code-built dossier.
  - About 8 server-loop iterations, at most 6 searches and 3 fetches (`max_content_tokens` 6000).
  - About 12K uncached input, 30K cache writes, 150K cache reads and **8K output**.
  - 8K output is probably low: the packet JSON, dynamic-filtering code and thinking together could reach
    12–20K.
- **Forecaster (Opus `high`):**
  - 5K static prefix (1 write, then reads) plus a 10K sanitized packet.
  - **12K output**, of which about 5K is JSON (§4.3). That leaves only about 7K for thinking, so 20–30K
    total is plausible.
- **Checker (Opus):** about 30K uncached, 20K writes, 80K reads, 6K output and 9 searches per pass;
  1.5 passes on average.

**NFL 12-game main slate, base case** (author's token estimates):

| Stage | Model | Calls | Input processed | Output | Searches | $ |
|---|---|---|---|---|---|---|
| Research per game | Sonnet | 12 | 2,304K (mostly cache reads) | 96K | 72 | 3.05 |
| Forecaster per game | Opus | 12 | 180K | 144K | 0 | 3.40 |
| *Optional 2nd forecaster* | Sonnet | 12 | 180K | 120K | 0 | *1.46* |
| Draft strategy note | Sonnet | 1 | 15K | 4K | 0 | 0.07 |
| Strategist | Opus | 1 | 25K | 6K | 0 | 0.22 |
| Checker (×1.5 avg) | Opus | 1.5 | 195K | 9K | 13.5 | 0.67 |
| Backup reviewer | Sonnet | 1 | 25K | 6K | 0 | 0.11 |
| User-rule parse | Haiku | 1 | 3K | 0.5K | 0 | 0.00 |
| Last-hour triage, per team | Sonnet `low` | 24 | 360K | 60K | 48 | 1.66 |
| Last-hour re-forecast (about 3 games) | Opus | 3 | 45K | 36K | 0 | 0.92 |
| Last-hour checker | Opus | 1 | 130K | 6K | 9 | 0.45 |
| Grading attribution (Batch, 50% off) | Sonnet | 0.5 | 15K | 2K | 0 | **0.03** [CORRECTED: was 0.05; `budget.py` labelled the row Batch but didn't apply the discount] |
| **Total** | | | **≈ 3.5M** | **≈ 0.49M** | **≈ 143** | **≈ $10.56** (≈ $12.02 with the 2nd forecaster) |

**Sensitivity (computed, design):**

| Scenario | NFL total | Forecaster | Research |
|---|---|---|---|
| Base (above) | $10.56 | $3.40 | $3.05 |
| Base + 2nd forecaster | $12.02 | $3.40 | $3.05 |
| High: forecaster 24K out, research 16K out, +50% search tokens | **$15.66** | $6.28 | $4.55 |
| High + 2nd forecaster | **$18.32** | $6.28 | $4.55 |

- **Worst case** (3 checker passes) adds about $0.67.
- **Haiku triage.** If Haiku supports search, moving last-hour triage to it cuts that row from $1.66 to
  about $0.54: the $0.48 search fee plus about $0.06 of tokens.
- **Hard cap.** Set the per-slate cap at **$20** (or $15 once measured usage confirms the base case),
  enforced by both the Ledger and a workspace spend limit. When it's hit, abort LLM stages and fall back
  to baseline-only. [CORRECTED: was "$15", which the plausible high scenario already exceeds]

**CFB 8-game slate (base case):**
- Research: 8 calls, at most 8 searches each, $2.41.
- Forecaster: 8 calls of about 22 players each, $2.46.
- Strategist, checker and backup reviewer: about $1.07.
- Last hour: about $2.22.
- **Total ≈ $8.16** (about 2.8M input and 0.27M output).
- About $1.5 of that is fixed. The rest scales roughly with game count: a 14-game slate is about $13.

**Showdown, single game (base case):**
- Two deep research calls, one per team (at most 10 searches each): $0.67.
- Forecaster ensemble of 2 Opus framings plus 1 Sonnet: $0.86.
- Strategist with captain logic: $0.22.
- Checker ×1.5: $0.55.
- Backup reviewer: $0.10.
- Last hour: $0.52.
- **Total ≈ $2.92.** Wall clock is about 15–20 min. Ensembling is cheap enough here to always run.

Cost-sensitivity, ranked levers (design):
1. **Effort on the forecaster.** 12K output × $20/MTok = $0.24 per game. Halving the *thinking* share
   saves about $1.1–1.4 per slate, depending on how much of the output is thinking. [CORRECTED: was "about
   $1.4"] Sweep `medium`, `high` and `xhigh` on graded data.
2. **`max_uses`, dynamic filtering, code-built dossiers and task budgets on research.**
   - The LLM should search only for gaps (news, role and practice notes), not for structured data the
     adapters already have.
   - A **task budget** (beta `task-budgets-2026-03-13`, in `output_config.task_budget`) is supported on
     Opus 5.5, Sonnet 5.5 and Haiku 5.5. It gives the agent an advisory token countdown for the whole
     loop, so it can finish gracefully.
     [VERIFIED: https://platform.claude.com/docs/en/build-with-claude/task-budgets]
     The minimum total of 20,000 comes from the claude-api skill and is otherwise `[UNVERIFIED]`.
3. **Pruning.** Forecaster output grows linearly with the number of players.
4. **`response_inclusion: "excluded"` and `max_content_tokens`** on the web tools. Note again that the
   latter doesn't cap PDFs.
5. **Prompt caching** of static prefixes: small (about $0.3 per slate) but free.

Accounting formula, implemented in the Ledger (code; design):
```
cost_call = m_geo · m_batch · [ in·p_in + cw5m·p_cw5 + cw1h·p_cw1h + cr·p_cr + out·p_out ]
            + 0.01 · web_search_requests
```
- **Inputs** come from the `usage` fields [VERIFIED: pricing and prompt-caching pages]:
  `usage.input_tokens`, `usage.cache_creation.ephemeral_5m_input_tokens`,
  `usage.cache_creation.ephemeral_1h_input_tokens`, `usage.cache_read_input_tokens`,
  `usage.output_tokens` and `usage.server_tool_use.web_search_requests`.
- **Multipliers:**
  - `m_batch = 0.5` on Batch.
  - `m_geo = 1.1` when `inference_geo` is `"us"` (Claude 4.6+). Per-request fees such as web search
    aren't multiplied.
    [VERIFIED: https://code.claude.com/docs/en/agent-sdk/cost-tracking; https://platform.claude.com/docs/en/about-claude/pricing]
- **Prices** are those of the model that actually served (fallbacks). For Haiku 5.5, pick the price tier
  by total prompt tokens, cache included. How that threshold applies across iterations of one
  server-tool loop is `[UNVERIFIED]`.
- **Reconcile** monthly against the Usage & Cost API.

### 4.11 Caching and reuse across the main run and the last-hour re-check (design)

1. **Artifact cache (the big win).** A content-addressed store:
   `artifacts/{slate_id}/{stage}/{sha256(key)}.json`.
   - GamePacket key: `(game_id, sources_hash, prompt_version, schema_version, model)`.
   - Forecast key: `(game_id, sha256(canonical_json(sanitized_packet)), model, effort, prompt_version, schema_version)`.

   Consequences:
   - One slate's packets and forecasts serve **every contest on that slate** (DK Classic, FD Full Roster,
     GPP and cash), because scoring is applied in code from the stat components.
   - Showdowns on main-slate games reuse the main slate's game packet.
   - In the last hour, the fact hash is recomputed per team after news triage. Unchanged games make
     **zero** LLM calls.
2. **The affected set for re-forecasting.**
   - It starts with "players the news affects", as in the user's rule: the player whose status or role
     changed.
   - Target and carry shares are coupled within a team, so expand it to that team's skill players in the
     same position group, plus the QB.
   - Re-forecast at the team level, so the team's shares stay coherent. Unaffected players keep their
     main-run forecast byte for byte.
3. **API prompt cache.**
   - Each role's prefix is frozen: `system` with an explicit `cache_control` on its last block, and tools
     sorted.
   - Use the 5 m TTL inside the main run.
   - Use the 1 h TTL only for the forecaster prefix, and only if the last-hour step is scheduled within
     60 min of the main forecaster stage. Otherwise accept a cold write: 5K × $5/MTok ≈ $0.025.
   - Pre-warm with `max_tokens: 0` for research and triage, which have no `output_config.format`. The
     forecaster uses the first-event fan-out.
4. **Server-side caches.**
   - `web_fetch` caches pages by default. Set `use_cache: false` (`_20260309+`) in the last hour.
   - The structured-output grammar cache lasts 24 h from last use. Keep schemas stable between the main
     run and the last hour.
5. **Batch pre-work.**
   - Store the T−72h team dossiers (Batch, 50% off) as artifacts, and insert them into research prompts
     as stable per-team context.
   - Place them *before* the volatile per-run content, so they're cache hits across contests and runs on
     the same day.

### 4.12 Orchestrator skeleton (Python, asyncio; illustrative)

Verify SDK signatures against the claude-api skill's `python/` docs before coding.

Changes from the original:
- **Fan-out** now waits for the first streamed event, or pre-warms, instead of `asyncio.sleep(0)`. That
  never waited for the cache.
- **Research** streams, validates the packet, and nudges or retries once.
- **Fallbacks** are on the forecaster too.
- **Text extraction** finds the text block instead of assuming it's last.
- **The Ledger** prices by the model that served.

```python
import anthropic, asyncio, json, hashlib
from pydantic import ValidationError

client = anthropic.AsyncAnthropic(max_retries=3)    # retries connection errors, 408/409/429/>=500
OPUS, SONNET, HAIKU = "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"
FALLBACK = dict(betas=["server-side-fallback-2026-07-01"], fallbacks="default")  # Claude API only; not Batches; none on Haiku 5.5
SEM = {OPUS: asyncio.Semaphore(12), SONNET: asyncio.Semaphore(16), HAIKU: asyncio.Semaphore(32)}

def research_tools(allow_search, allow_fetch, fresh=False):
    fetch = {"type": "web_fetch_20260318", "name": "web_fetch", "max_uses": 3,
             "allowed_domains": allow_fetch,            # domain-only matching for fetch
             "max_content_tokens": 6000,                # not applied to PDFs
             "response_inclusion": "excluded"}
    if fresh:
        fetch["use_cache"] = False                      # last-hour news only (changes tools -> separate cache)
    return sorted([
        {"type": "web_search_20260318", "name": "web_search", "max_uses": 6,
         "allowed_domains": allow_search, "response_inclusion": "excluded"},
        fetch,
        SUBMIT_GAME_PACKET_TOOL,   # {"name": "submit_game_packet", "strict": True, "input_schema": {..., "additionalProperties": False}}
    ], key=lambda t: t["name"])                         # deterministic tool order for cache stability

async def prewarm(model, system, tools, effort):
    # Writes the tools+system cache, then returns at once. Allowed because there is no stream,
    # no output_config.format and no forced tool_choice, and this is not a Batch. Keep effort/thinking identical.
    return await client.messages.create(model=model, max_tokens=0, system=system, tools=tools,
                                        output_config={"effort": effort},
                                        messages=[{"role": "user", "content": "warm-up"}])

async def research_game(game, ledger, tools):
    async with SEM[SONNET]:
        msgs = [{"role": "user", "content": render_dossier(game)}]   # official URLs go here so web_fetch may use them
        nudged = False
        for _ in range(4):                                            # caps pause_turn / retry continuations
            async with client.beta.messages.stream(
                    model=SONNET, max_tokens=32000, **FALLBACK,
                    system=RESEARCH_SYSTEM_BLOCKS,                    # last block carries cache_control
                    tools=tools, output_config={"effort": "medium"},
                    messages=msgs) as s:
                resp = await s.get_final_message()
            ledger.add(resp)                                          # prices by resp.model + server_tool_use
            if resp.stop_reason == "refusal":
                break
            call = next((b for b in resp.content
                         if b.type == "tool_use" and b.name == "submit_game_packet"), None)
            if call:
                try:
                    return sanitize(GamePacket.model_validate(call.input))   # code-enforced blinding
                except ValidationError as e:                          # append-only retry with an error tool_result
                    msgs += [{"role": "assistant", "content": resp.content},
                             {"role": "user", "content": [{"type": "tool_result", "tool_use_id": call.id,
                                                           "is_error": True, "content": str(e)[:2000]}]}]
                    continue
            if resp.stop_reason == "pause_turn":                      # resend unchanged, same tools
                msgs.append({"role": "assistant", "content": resp.content})
                continue
            if resp.stop_reason == "end_turn" and not nudged:
                msgs += [{"role": "assistant", "content": resp.content},
                         {"role": "user", "content": "Call submit_game_packet now with what you have."}]
                nudged = True
                continue
            break
        return code_only_dossier(game)                                # degrade gracefully

async def forecast_game(packet, ledger, first_event: asyncio.Event | None = None):
    async with SEM[OPUS]:
        async with client.beta.messages.stream(
                model=OPUS, max_tokens=64000, **FALLBACK,
                thinking={"type": "adaptive", "display": "summarized"},  # same billing; gives an audit trail for grading
                system=FORECAST_SYSTEM_BLOCKS,
                output_config={"effort": "high",
                               "format": {"type": "json_schema", "schema": GAME_FORECAST_SCHEMA}},
                messages=[{"role": "user", "content": canonical_json(packet)}]) as s:
            async for _ev in s:
                if first_event is not None and not first_event.is_set():
                    first_event.set()                                 # the response has begun, so the cache is readable
            msg = await s.get_final_message()
        ledger.add(msg)
        if msg.stop_reason in ("refusal", "max_tokens"):
            return None                                               # -> baseline-only for this game
        text = next(b.text for b in msg.content if b.type == "text")
        return validate_forecast(GameForecast.model_validate_json(text))

async def fanout_first_event(fn, items, ledger):
    ev = asyncio.Event()
    first = asyncio.create_task(fn(items[0], ledger, first_event=ev))
    waiter = asyncio.create_task(ev.wait())
    await asyncio.wait({first, waiter}, return_when=asyncio.FIRST_COMPLETED)
    waiter.cancel()
    rest = [asyncio.create_task(fn(x, ledger)) for x in items[1:]]
    return await asyncio.gather(first, *rest, return_exceptions=True)

async def research_stage(games, ledger):
    tools = research_tools(SEARCH_ALLOW, FETCH_ALLOW)
    ledger.add(await prewarm(SONNET, RESEARCH_SYSTEM_BLOCKS, tools, "medium"))
    tasks = [asyncio.wait_for(research_game(g, ledger, tools), timeout=360) for g in games]
    out = await asyncio.gather(*tasks, return_exceptions=True)
    return [r if not isinstance(r, Exception) else code_only_dossier(g) for g, r in zip(games, out)]
```

The Ledger (code) does three things:
- It prices each `usage` with the §4.2 table and the §4.10 formula.
- It raises `BudgetExceeded` at the run's cap, which triggers baseline-only fallbacks.
- It writes `run_manifest.json` with:
  - per-stage tokens, dollars and wall time;
  - the cache-hit ratio, `cache_read / total_input`;
  - search counts;
  - the served model.

Integration assertion: after the pre-warm or first event, every later call in a fan-out must show
`cache_read_input_tokens > 0` (prefixes ≥ 512 tokens).

---

## 8. Open questions for the user or orchestrator

1. **Lines as "prices".** Do the "prices" the fresh forecaster must not see include **betting lines and
   totals**? I recommend yes, to keep the forecaster independent of the line-adjusted NFL baseline.
2. **"Past scores".** Do they exclude only fantasy points, or also past **stat lines** (targets, snaps,
   carries)? Usage shares are among the most predictive facts. I recommend allowing usage facts and
   disallowing fantasy-point histories.
3. **Drafts.** Should the "three independent drafts" be **solver-built** (recommended) or LLM-built? LLM
   drafts cost about $0.15 each on Opus and need legality repair.
4. **Usage tier.** Confirm the org's tier. A new org may be in the Evaluation tier. Start caps spend at
   $500 a month, about 31–45 NFL slates.
5. **Haiku search.** Is Haiku 5.5 eligible for server-side web search, and for programmatic tool calling,
   in this org? Check `capabilities.server_tools` with the Models API. If it is, last-hour triage drops to
   about $0.54.
6. **CFB prior.** Keep the user's 1.0, or start at 0.5 until about 300 graded player-games per position
   exist?
7. **The 60-minute budget.** Does it cover the main run plus the first last-hour window only, with later
   late-swap windows as separate ~10-minute runs?

## 9. Verification to-do (`[UNVERIFIED]` items)

- **Papers (all `[UNVERIFIED]`):**
  - Halawi et al. 2024: the Brier numbers (0.179 vs 0.149, combined 0.146) and "better when the crowd was
    uncertain". arXiv:2402.18563.
  - Schoenegger et al. 2024 (Science Advances): the 12-model ensemble result, the 17–28% improvement, the
    DOI and the arXiv ID.
  - Xiong et al.: the venue and arXiv ID. Also look for any study of LLM numeric-interval coverage.
  - Anchoring: Lou & Sun 2024, Nguyen 2024.
  - Look-ahead: Glasserman & Lin 2023, Sarkar & Vafa 2024, Paleka et al. 2025.
  - Fildes et al. 2009 (IJF); Lichtendahl, Grushka-Cockayne & Winkler 2013 (quantile averaging).
  - Any sports or fantasy LLM projection study (Prophet Arena, FutureX, others).
- **Measure in a dry run:**
  - per-model output speed and web-search latency;
  - per-call research and forecaster output tokens (the budgets hinge on them).
- **API behaviour:**
  - whether `web_search` and `output_config.format` are accepted together in one request (the design
    avoids depending on it);
  - Haiku 5.5 web search and programmatic tool calling;
  - the shape of `usage.iterations` for fallback billing;
  - how Haiku's 100K threshold applies inside server-tool loops;
  - the 20,000 minimum for task budgets.
- **Schedules and site rules:**
  - NFL inactive-list timing (about 90 min before each kickoff), and DK/FD late-swap rules by contest type;
  - CFB 2026 week numbering.
- **ForecastBench:** the meaning of the "†" suffix.

## 10. Sources (fetched 2026-10-21; local copies in `scratchpad/verify_llm/`)

- Pricing: https://platform.claude.com/docs/en/about-claude/pricing
- Models overview: https://platform.claude.com/docs/en/about-claude/models/overview
- Opus 5.5 what's new and migration guide: https://platform.claude.com/docs/en/models/opus-5-5/whats-new-opus-5-5 ; https://platform.claude.com/docs/en/models/opus-5-5/migration-guide
- Sonnet 5.5 what's new: https://platform.claude.com/docs/en/models/sonnet-5-5/whats-new-sonnet-5-5
- Refusals and fallback: https://platform.claude.com/docs/en/build-with-claude/refusals-and-fallback
- Rate limits: https://platform.claude.com/docs/en/api/rate-limits
- Batch processing: https://platform.claude.com/docs/en/build-with-claude/batch-processing
- Prompt caching: https://platform.claude.com/docs/en/build-with-claude/prompt-caching
- Tool use with prompt caching: https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-use-with-prompt-caching
- Structured outputs: https://platform.claude.com/docs/en/build-with-claude/structured-outputs
- Task budgets: https://platform.claude.com/docs/en/build-with-claude/task-budgets
- Web search tool: https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool
- Web fetch tool: https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-fetch-tool
- Server tools: https://platform.claude.com/docs/en/agents-and-tools/tool-use/server-tools
- Agent SDK: https://code.claude.com/docs/en/agent-sdk/overview ; …/structured-outputs ; …/subagents ; …/cost-tracking
- Headless: https://code.claude.com/docs/en/headless
- claude-api skill (bundled, cached 2026-10-06): SKILL.md and python/claude-api/README.md
- ForecastBench README: https://raw.githubusercontent.com/forecastingresearch/forecastbench/main/README.md
- ForecastBench changelog: https://raw.githubusercontent.com/wiki/forecastingresearch/forecastbench/Changelog.md
- ForecastBench "How does it work": https://raw.githubusercontent.com/wiki/forecastingresearch/forecastbench/How-does-ForecastBench-work%3F.md
- ForecastBench CSVs: https://raw.githubusercontent.com/forecastingresearch/forecastbench-datasets/main/leaderboards/csv/leaderboard_tournament.csv ; …/leaderboard_baseline.csv
- Halawi et al. repo: https://raw.githubusercontent.com/dannyallover/llm_forecasting/main/README.md (paper https://arxiv.org/abs/2402.18563 unreachable)
- Xiong et al. repo: https://raw.githubusercontent.com/MiaoXiong2320/llm-uncertainty/main/README.md
- Tan et al. repo: https://raw.githubusercontent.com/BennyTMT/LLMsForTimeSeries/main/README.md
- Gruver et al. LLMTime repo: https://raw.githubusercontent.com/ngruver/llmtime/main/README.md
- Anthropic 2022: https://www.anthropic.com/research/language-models-mostly-know-what-they-know
- Sibling research files (same scratchpad):
  - `site_formats.verified.md` (513-row DK NFL file);
  - `sources_compliance.verified.md` (NFL 2026 weeks 1–6 complete);
  - `scoring.verified.md` (DST 35+ PA = −4 on DK and FD);
  - `pipeline_critique.md` and `projections.md` (inactives timing, late swap: medium).
- `[UNVERIFIED]` and not reachable today:
  - Schoenegger et al. 2024;
  - Lou & Sun 2024; Nguyen 2024;
  - Glasserman & Lin 2023; Sarkar & Vafa 2024; Paleka et al. 2025;
  - Fildes et al. 2009; Lichtendahl et al. 2013;
  - Alpert & Raiffa; Soll & Klayman;
  - Prophet Arena; FutureX.

## Appendix: correction log

| # | Original | Corrected | Source |
|---|---|---|---|
| 1 | Halawi title "…with Language Model Systems" | "Approaching Human-Level Forecasting with Language Models" (NeurIPS 2024) | authors' repo README |
| 2 | Opus 4.8 high-24k (0.140) vs max+search (0.113) given as baseline-leaderboard evidence that effort and search matter | Tournament-leaderboard rows. The comparison confounds effort with search, and N differs (3,350 vs 1,579) | tournament CSV |
| 3 | "Without fresh information, LLMs do worse than the public" | Best baseline LLM 0.149 vs public 0.141: worse point estimate, not significantly better; "worse" isn't tested | baseline CSV |
| 4 | Top 27 "mostly scaffolded systems and ensembles" | Mostly codenamed team submissions with undisclosed methods; two are labelled ensembles | tournament CSV |
| 5 | Changelog quote "baseline models have no context beyond their training cutoff" | Paraphrase. Actual: "models face additional difficulty in that they have absolutely no context with which to forecast beyond their training cutoff" | changelog |
| 6 | Usable LLM-eval data: NFL 2026 weeks 1–7, CFB weeks 0–8 | NFL weeks 1–6 (week 7 opens 2026-10-22); CFB through about 2026-10-18 (week numbers unverified) | sibling `sources_compliance.verified.md` |
| 7 | Truncate QB/RB/WR/TE ≥ −4, DST ≥ −6 on DK | DST minimum is −4 on DK and FD; skill players have no fixed floor; don't truncate | sibling `scoring.verified.md` |
| 8 | Split-normal "through (lo', m, hi')" | The split normal's mode isn't its median; use the 50/50 two-piece quantile function | math |
| 9 | Fit a_pos, b_pos by root-finding | Closed form: 0.9-quantiles of the standardized one-sided residuals | math |
| 10 | Dominance pruning valid for "a monotone objective" | Valid only for a separable mean objective with same-team, superset-eligible dominators; heuristic for GPP | logic |
| 11 | Forecaster JSON "about 3K" tokens | About 4.6–5.4K tokens for 20 players × 8 components | computed |
| 12 | NFL ≈ $10.59 / $12.05 with no sensitivity; $15 hard cap | $10.56 / $12.02 base (Batch discount applied to grading); $15.66 / $18.32 high; cap $20 until measured | `calc/budget_verified.py` |
| 13 | Caching saves $0.03–$0.10 per stage | About $0.08 (Sonnet research) to $0.21 (Opus forecaster) per stage | pricing page arithmetic |
| 14 | Halving thinking saves about $1.4 per slate | About $1.1–1.4, depending on thinking share | arithmetic |
| 15 | Sampling params "removed (400)" on Opus 5.5 | Non-default values return 400; omitting or defaults accepted; Python SDK v1.0 doesn't define them | opus-5-5 migration guide |
| 16 | `fallbacks` "rejected on the Batch API" | A batch item with `fallbacks` comes back as an errored result; Haiku 5.5 has no fallback | refusals-and-fallback page |
| 17 | Fan-out with `asyncio.sleep(0)`; "pre-warming isn't worth it here" | Wait for the first streamed event; `max_tokens: 0` pre-warm is allowed and simplest for research | prompt-caching page |
| 18 | Fable 5.1 batch price "—" | $5 / $25 | pricing page |
| 19 | "FactPacket per team-game" with per-game research calls | `GamePacket` (two `TeamPacket`s) per game | design consistency |
| 20 | Agent SDK: "must use API keys" | "Unless previously approved", third parties may not offer claude.ai login or rate limits | Agent SDK overview |
| 21 | Rate limits not binding (no tier caveat) | New orgs may start in a lower Evaluation tier | rate-limits page |
| 22 | (omitted) `max_content_tokens` limits fetches | Not applied to PDFs | web-fetch page |
| 23 | (omitted) last-hour 1% rule | Needs common random numbers; relative SE of P(top 2.3%) is 6.5% at 10K sims | computed |
| 24 | (omitted) component marginals simulated independently | Simulate components jointly (volume → catches → yards → TDs) | design |
