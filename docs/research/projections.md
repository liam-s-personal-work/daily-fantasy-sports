# DFS player projections and distributions: methodology research (verified edition)

> Paths under `scratch/` refer to verification scripts and downloads from the research session; they are not in the repo.

Research input for the `liam-clifford/daily-fantasy-sports` lineup engine. The original was written 2026-10-21 and
skeptically reviewed the same day. This file stands alone and replaces `projections.md`.

Scope: how to turn a contest's player list into calibrated per-player **distributions**, not just point estimates. It goes
deepest on NFL and college football (CFB), with short recipes for NBA, MLB, NHL, PGA, MMA, NASCAR and soccer. Everything
here is meant to be turned into code.

---

## V. Verification status (read first)

### V.1 What could and could not be checked today

- **WebFetch** fails with `getaddrinfo ENOTFOUND` (for example on arxiv.org). **curl** through the session proxy gets
  `403 connect_rejected` for arxiv.org, stokastic.com, pff.com, establishtherun.com and others. **WebSearch** returned
  "budget is used up (limit: 200 WebSearch calls per turn, shared by every agent)" on the first query.
  [VERIFIED: tool errors and `$HTTPS_PROXY/__agentproxy/status`, this session]
- So **no paper, practitioner article or DFS site page was read today**. Every claim that rests only on such a page is
  tagged **[UNVERIFIED]**, including those the original marked "high". The original author also never opened those
  pages: it worked from search-result snippets.
- What **could** be checked, and was:
  1. Primary sources hosted on GitHub or PyPI: the nflverse data and docs, nflfastR source, nflreadr/nflreadpy, the
     ffopportunity package, the `implied` R package vignette, the `shin` Python package, `goalmodel`, the `loo` CRPS
     source, the CQR authors' repo, the CFBD official SDK docs, and the statsmodels 0.15.0 source.
  2. Every derived formula and worked example, re-computed numerically
     (`scratch/scripts/mathcheck.py`).
  3. **Many empirical NFL claims, re-estimated from nflverse 2021–2025 data** (§E). This is the strongest evidence in
     this file, and it overturns several of the original's published numbers.
  4. DFS scoring facts, cross-checked against the sibling verified file
     `scratch/research/scoring.verified.md`.

### V.2 Tag legend

- `[VERIFIED: url]`: confirmed today from that primary source. `derived` means I re-computed it, from public data or
  maths, with the script named.
- `[CORRECTED: was X; url]`: the original was wrong or overstated. The corrected fact follows.
- `[UNVERIFIED]`: not confirmed today. Where my own recollection agrees or disagrees, I say so. Recollection is
  low-confidence and is **not** evidence.

### V.3 The most important corrections (all affect code)

| # | Original | Corrected | Evidence |
|---|---|---|---|
| 1 | nflverse `spread_line` is a "closing" line, with the favourite negative (the §2.1 convention) | `spread_line` is **positive when the home team is favoured**, the opposite of the betting convention. The docs say only "the spread line for the game", not "closing" | [VERIFIED: https://raw.githubusercontent.com/nflverse/nfldata/master/DATASETS.md] |
| 2 | Prop line → lognormal, so a 64.5 line with s = 0.75 gives a mean of 85.4 | For yards, weekly outcomes are **far less right-skewed than a lognormal**. For WRs averaging 55–75 receiving yards, the median is 0.94 × the mean (mean/median ≈ 1.06). A lognormal with the same CV implies 1.15, and s = 0.75 implies 1.32. A 64.5 median line implies a mean near **68–69, not 85** | [VERIFIED: derived, nflverse 2021–25, `checks_misc.py`] |
| 3 | Raw correlations QB–WR1 .53, QB–RB1 .42 (FantasyLabs); WR1–opp WR1 .56, WR1–WR2 .345 (Spike Week) | Pooled within-pair z-score correlations under DK scoring, 2021–25: **QB–WR1 .35, QB–WR2 .35, QB–TE1 .28, QB–RB1 .06, WR1–WR2 .02, WR1–RB1 −.02, QB–opp WR1 .12, WR1–opp WR1 .08, QB–opp QB .19** (per-pair medians in §E are within ±0.05 of these). The published numbers could not be reproduced, even pooled across pairs | [VERIFIED: derived, `nflemp.py`] |
| 4 | "When one QB scores 25+, the opponent does about 6 in 10 times" | **29%** (488 cases), against an 18% base rate, 2021–25 DK scoring | [VERIFIED: derived, `checks_misc.py`] |
| 5 | No post-2016 play rate is known for "questionable" | **Questionable players played 66.0%** of the time (n = 5,693, REG 2022–25). By final practice status: DNP 51%, Limited 67%, Full 73%. Doubtful 0.5%, Out 0% | [VERIFIED: derived, `qrate.py`] |
| 6 | Weekly CV for ~10-point players: QB 41% | At 8–12 FD points per game, **RB 64%, WR 64%, TE 66%**. A QB CV of 42% holds for QBs at **≥ 12 ppg**. The 8–12 ppg QB group (n = 21) is 61% | [VERIFIED: derived, `nflemp.py`] |
| 7 | NFL margin vs spread SD 13.45 (fallback for win probability) | 13.49 on 1999–2012, **12.87 on 2013–25 and 12.63 on 2021–25**. Use ≈ 12.7 now | [VERIFIED: derived from nflverse games.csv, `checks_misc.py`] |
| 8 | CFBD API "rate-limited" | **Monthly call limits**, not per-minute: free tier **1,000 calls/month**; Patreon tiers raise it | [VERIFIED: https://raw.githubusercontent.com/CFBD/cfbd-net/main/docs/authentication.md] |
| 9 | Only DK has the 300/100/100 yardage bonuses | FanDuel **also** lists +3 bonuses at 300 passing / 100 rushing / 100 receiving yards (added 2024) | per `scoring.verified.md` (its source V7, a 2026-09-13 note quoting fanduel.com/rules) [UNVERIFIED by me live] |
| 10 | The §2.3 usage layer uses routes and TPRR | **nflverse has no free 2026 route or participation data.** FTN participation is released only after the season. In season, use PFR snap share (in nflverse) and target share as the free proxies | per `sources_compliance.verified.md` (participation 2026 file returns 404) [UNVERIFIED by me] |
| 11 | ffopportunity licence not mentioned | Code is **GPL-3**. Models and xFP data are **CC-BY-SA 4.0** | [VERIFIED: https://raw.githubusercontent.com/ffverse/ffopportunity/main/README.md] |
| 12 | "Post-cutoff data: weeks 1–7, ~2,000 player-games" | By 2026-10-21, **93 regular-season games, weeks 1–6**, have been played. Week 7 has not been played | [VERIFIED: derived from nflverse games.csv] |
| 13 | `loo` CRPS formula `E|X−y| − ½E|X−X′|` | That is CRPS, where lower is better. **`loo::crps()` returns the negative**, `0.5·E|X−X′| − E|X−y|`, where higher is better. Mind the sign | [VERIFIED: https://raw.githubusercontent.com/stan-dev/loo/master/R/crps.R] |

---

## E. Empirical checks on nflverse data (new in this edition)

Data: nflverse weekly player stats 2021–2025
(`https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_<yyyy>.csv`), team stats
2023–25 (`.../stats_team/stats_team_week_<yyyy>.csv`), injuries and snap counts 2022–25 (`.../injuries/injuries_<yyyy>.csv`,
`.../snap_counts/snap_counts_<yyyy>.csv`), `players.csv` (gsis↔pfr ID map; 99.8–99.97% of snap rows mapped), and
`https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv`. All rows are regular season only.

Scripts (run with `python3 -I <script> <data dir>`), all in
`scratch/scripts/`:
`qrate.py` (injury designations), `nflemp.py` (CV and correlations), `yoy.py` (stability), `checks_misc.py` (spread,
plays, yardage shape, QB–QB, DST) and `mathcheck.py` (formulas).

| Quantity | Value | Definition / caveat |
|---|---|---|
| Played rate by final designation, REG 2022–25 | Out 0.000 (n 4,471); Doubtful 0.005 (602); **Questionable 0.660 (5,693)**; on the report but no game status 0.936 (11,866) | "Played" means at least 1 offense, defense or special-teams snap in PFR snap counts. Questionable is stable by season: 0.665, 0.662, 0.654, 0.658 |
| Questionable, by final practice status (all positions) | DNP 0.512 (907); Limited 0.674 (3,446); Full 0.727 (1,239) | QB/RB/WR/TE only: DNP 0.471 (263), Limited 0.652 (1,062), Full 0.733 (360); overall 0.642 (1,717) |
| Weekly CV, FD half-PPR (no bonuses), players at 8–12 ppg, ≥ 8 games | RB 0.643 (n 92), WR 0.639 (158), TE 0.655 (54), QB 0.607 (21, mostly part-time QBs) | Players at ≥ 12 ppg: QB 0.421 (153), RB 0.528 (98), WR 0.543 (90) |
| Year-over-year r (R²), players with ≥ 8 games in both seasons, 2021→25 | WR target share 0.81 (0.66); WR yards/target 0.26 (0.07, ≥ 30 targets); WR receiving yards per team pass attempt 0.77 (0.59) | TE: target share 0.79, YPT 0.33, yds/team att 0.79. RB: target share 0.69, YPT 0.08 |
| Within-pair Pearson r of DK points (median of per-team-season pairs, ≥ 8 shared games; n ≈ 143–159 pairs) | QB–WR1 0.36; QB–WR2 0.38; QB–TE1 0.30; QB–RB1 0.05; WR1–WR2 0.01; WR1–RB1 −0.03; QB–opp WR1 0.08; WR1–opp WR1 0.06; QB–opp QB 0.18; RB1–opp RB1 −0.05 | Pooled within-pair z-scores give almost the same: 0.35, 0.35, 0.28, 0.06, 0.02, −0.02, 0.12, 0.08, 0.19, −0.08. Roles are season ranks on the team (QB by attempts, WR/TE by targets, RB by carries + targets). Pooled raw r without centering: 0.36, 0.38, 0.33, 0.06, 0.08, 0.00, 0.11, 0.08, 0.18, −0.07 |
| P(opp QB ≥ 25 DK \| QB ≥ 25 DK) | 0.291 (n 488); base rate P(QB ≥ 25) = 0.180 | Main QB = most attempts for the team in that game |
| DST (approximate DK scoring) vs opponent points | r = −0.707 (n 1,632 team-games, 2023–25); DST vs INTs 0.55; vs sacks 0.59 | Points allowed uses all opponent points, slightly different from DK's definition |
| Margin vs spread, SD | 1999–2012: 13.49 (n 3,560); 2013–25: 12.87 (3,407); 2021–25: 12.63 (1,359) | `result − spread_line`, both from the home side |
| Total vs total line, SD | 13.54 / 13.24 / 13.07 for the same eras | |
| Team points vs implied team total, SD | 9.56 / 9.23 / **9.09** | Home and away residuals are almost uncorrelated (r = 0.00–0.035) |
| Offensive plays per team-game (attempts + sacks + carries) | 2023: 63.11; **2024: 62.15; 2025: 61.26** | This matches the PFR figures in §2.2 |
| Team targets ÷ team pass attempts | 0.957 (game-level 5th–95th percentile 0.88–1.00) | Use this in the target-normalisation rule |
| Team passing yards − Σ receiving yards | Maximum absolute difference 1 yard (an identity) | So the "QB pass yards ≈ Σ receiving yards" check is an identity on means when every passer and receiver is included |
| Weekly yardage shape (player-seasons ≥ 10 games) | WR 55–75 rec yds: median/mean 0.940, CV 0.568; RB 55–75 rush yds: 0.929, CV 0.553; TE 40–60: 0.931, CV 0.620 | Implied mean/median is 1.06–1.08. A gamma with the same CV implies 1.11–1.14, and a lognormal 1.14–1.18 |
| Post-cutoff NFL sample by 2026-10-21 | 93 games (weeks 1–6: 16, 16, 16, 16, 15, 14) | Week 6 ended 2026-10-19 |

---

## 0. Headline findings (corrected)

1. **The accuracy ceiling is low, and that changes the goal.** The original reports best public weekly NFL projection
   MAEs of about 6.2 (QB), 5.2 (RB) and 4.9 (WR) over 11 seasons, and a 0.01-MAE gap between the top two sources in 2025
   [UNVERIFIED: https://fantasyfootballanalytics.net/which-dfs-projections-are-most-accurate]. Independently, weekly
   outcomes are very noisy. Players at 8–12 FD points per game have a CV of about 64% at RB and WR, and starting-calibre
   QBs (≥ 12 ppg) about 42% [VERIFIED: derived, nflverse 2021–25]. No projection system is "hyper-accurate". The
   realistic edge is (a) a few percent lower error than consensus, (b) calibrated tails and correlations, and (c) roster
   construction and ownership. A top-2.3% finish in *every* contest cannot be engineered. Treat it as a long-run rate to
   measure.
2. **Build top-down and keep it consistent.** Start from market-implied team points. Project team plays and the pass/rush
   split, then each player's share of that volume, then shrunken efficiency, then simulated stat lines, then fantasy
   points. Player projections must add up to team totals.
3. **Player props are the strongest single input where they exist, and where a source permits automated access.**
   De-vig the odds. The line is roughly the median [UNVERIFIED: Stokastic]. **Do not convert median to mean with a
   lognormal.** Empirically, the mean is only about 6–8% above the median for yardage stats [VERIFIED: derived]. Fit the
   shape from residuals (§2.6). Anytime-TD prices give a scoring rate via λ = −ln(1 − p) [VERIFIED: derived]. Licensed
   prop access is itself an open question (§13).
4. **Shares are stable and efficiency is not.** WR target share year over year: r = 0.81 (R² 0.66). WR yards per target:
   r = 0.26 (R² 0.07) [VERIFIED: derived, nflverse 2021→25]. Shrink efficiency hard and usage lightly.
5. **LLM forecasts must be recalibrated before they are blended.** The original cites studies finding LLM intervals far
   too narrow: nominal 99% intervals covering about 65% (FermiEval), no model reaching 90% coverage on 90% intervals
   (QuantSightBench), and anchoring on numbers in the prompt [UNVERIFIED: arXiv 2510.26995, 2604.15859, 2505.15392;
   arXiv could not be reached]. Recalibrate on the engine's own post-cutoff data whatever those papers say (§4.3).
   Hiding DFS salaries is right. Hiding the **betting market** throws away the strongest input.
6. **The fixed 85/15 and 100/0 blends are untested claims.** Under standard forecast-combination maths, an 85/15 weight is
   MSE-optimal only if the baseline's error SD is 1.07–2.38 times the LLM's (for error correlation ρ from 0.9 down to 0).
   That means the LLM's error SD must be about 7–58% smaller [VERIFIED: derived, §7.2]. Learn the weights on games
   played **after the model's ~June 2026 training cutoff**, shrink them toward equal weights, and change them only on
   week-clustered evidence. Only 93 post-cutoff NFL games exist so far (§E). Simple averages are hard to beat in small
   samples [UNVERIFIED: Smith & Wallis 2009; recollection agrees].
7. **Simulation needs within-pair (residual) correlations.** Reproducible within-pair values are QB–WR1 ≈ 0.35,
   QB–TE1 ≈ 0.28 and QB–RB1 ≈ 0.05 [VERIFIED: derived]. These are well below the published figures the original quoted
   (0.53, 0.47, 0.42). Estimate correlations of normal-score residuals by role pair, repair the matrix to be positive
   definite (Higham), and sample through a Gaussian or t copula, or a game-factor model.

---

## 1. Evidence base: realistic accuracy floors

| Metric | Value | Status |
|---|---|---|
| Weekly MAE, QB, best source over 11 seasons | 6.20 (FantasyPros); 2023–25 best 6.13 (CBS); ESPN near the bottom | [UNVERIFIED: https://fantasyfootballanalytics.net/which-dfs-projections-are-most-accurate] |
| Weekly MAE, RB | 5.20 (FFA Average), 5.22 (FantasyPros); recent seasons three tied at 5.06 | [UNVERIFIED: same] |
| Weekly MAE, WR | 4.94 (FFA Weighted), 4.95 (numberFire, FantasyPros); recent 4.84 (FantasyPros) | [UNVERIFIED: same] |
| Weekly MAE, 2025 | numberFire 5.27, FantasyPros 5.28. The original says the position mix is unclear | [UNVERIFIED: same] |
| FFA 12-season study (2014–25), season MAE at QB | FantasyPros 61.0, FFA Average 61.7, both beating every single source | [UNVERIFIED: https://fantasyfootballanalytics.net/2026/08/we-analyzed-12-seasons-of-fantasy-football-projections-heres-what-we-found.html] |
| FFA 11-season DFS study | FFA Average ranked first on combined accuracy and consistency over the last three seasons (score 2.9) | [UNVERIFIED: https://fantasyfootballanalytics.net/2026/09/we-analyzed-11-seasons-of-dfs-projections-heres-what-we-found.html] |
| Season-long R² of the FFA average projection | 0.67 (2012), 0.50 (2013), 0.57 (2014) | [UNVERIFIED: https://www.r-bloggers.com/who-has-the-best-fantasy-football-projections-2015-update/] |
| Projections vs rankings, 2015 | R² 0.53 vs 0.27; free beat paid; combining both was best | [UNVERIFIED: https://www.r-bloggers.com/2016/04/which-are-more-accurate-fantasy-football-rankings-or-projections/] |
| Season-long bias | Projections over-estimated by about 4–10 points a season at most positions | [UNVERIFIED: https://www.r-bloggers.com/2017/03/who-has-the-best-fantasy-football-projections-2017-update/] |
| FantasyPros in-season method | Rankings converted to points; absolute error ("Accuracy Gap"), Half-PPR; snapshots Thursday night and Sunday 1pm ET | [UNVERIFIED: https://fantasypros.com/about/faq/football-inseason-accuracy-methodology] |
| Weekly CV (SD/mean) | Original: QB 41%, RB 63%, WR 61% for "~10 ppg" players (4for4 2016) | [CORRECTED: was "QB 41% for ~10-ppg players"; RB 64% and WR 64% at 8–12 ppg agree, but QB ≈ 42% only for QBs at ≥ 12 ppg; derived, nflverse 2021–25] |
| NBA DK points, student ridge regression | MAE 7.045, then 6.61 with fatigue and momentum features | [UNVERIFIED: https://web.stanford.edu/class/stats50/projects16/BarryCanovaCapiz-slide.pdf]. A student project, so illustrative only |
| NBA DK points, hackathon claim | MAE 7.37 vs commercial ~8.0 on 544,837 player-games | [UNVERIFIED: https://devpost.com/software/quantsim-fthcz5]. Unreviewed, so do not use as a benchmark |
| NBA individualised ML models (Springer 2024) | 203 players; MAPE gains 1.7–1.9% (evaluation), 0.2–2.1% (validation) | [UNVERIFIED: https://link.springer.com/article/10.1007/s41060-024-00523-y] |
| NFL margin vs spread | Stern (1991) about N(0, 13.86²); Paine 13.45 on 1978–2012; PFR's win-probability model uses 13.45 | [UNVERIFIED: https://www.pro-football-reference.com/about/win_prob.htm ; https://fivethirtyeight.com/features/how-unexpected-was-clevelands-shellacking-of-cincinnati]. nflverse shows the SD has **fallen**: 13.49 (1999–2012), 12.87 (2013–25), 12.63 (2021–25) [VERIFIED: derived] |

What these numbers mean for design:

- **Consensus averaging wins** in the FFA studies [UNVERIFIED]. The house projection should be an ensemble anyway (§7),
  since that is the robust default in the combination literature.
- **MAE is a misleading target on its own.** Projections are conditional on playing and, per FFA, biased high
  [UNVERIFIED]. Model the zero and injury mass separately (§4.4). A questionable player plays only 66% of the time
  [VERIFIED: derived].
- **Benchmark.** A weekly MAE about 3–5% below FantasyPros consensus on the same player pool would be strong for public
  data. **The 3–5% is the original author's judgment, not a published figure.** Measure against it, not against
  "hyper-accurate".

---

## 2. NFL: the top-down model

### 2.0 Overview (dataflow)

```
odds (spread, total, ML, props) ──► market layer ──► team points, win prob, TD expectation
schedule/pace/PROE history ───────► volume layer ──► plays, dropbacks, rush attempts per team
snaps/targets/carries (routes if licensed) ─► usage layer ──► per-player opportunity distributions
efficiency history + opponent ────► efficiency   ──► per-opportunity yards / catch / TD rates
                                      │
                                      ▼
                  stat-line simulator (counts → yards → TDs) under contest scoring table
                                      │
                                      ▼
            per-player distribution (quantiles, P(0), P(bonus)) + props reconciliation
```

### 2.1 Market layer

**De-vig the odds.** Convert American odds to decimal: +A → 1 + A/100; −A → 1 + 100/A. The raw implied probability is
πᵢ = 1/dᵢ, and the booksum is Π = Σπᵢ > 1. [VERIFIED: derived]

| Method | Formula | Notes |
|---|---|---|
| Multiplicative ("basic") | pᵢ = πᵢ / Π | The `implied` vignette: "This method tend to be the least accurate of the methods in this package" [VERIFIED: https://raw.githubusercontent.com/opisthokonta/implied/master/vignettes/introduction.Rmd] |
| Additive | pᵢ = πᵢ − (Π − 1)/n | "can produce negative probabilities, escpecially for outcomes with low probabilties"; the vignette's example is from Clarke, Kovalchik & Ingram (2017) [VERIFIED: same vignette]. Clip and renormalise, or switch method |
| Power | pᵢ = πᵢᵏ, k solved so that Σπᵢᵏ = 1 (k > 1) | "models the bookmaker probabilities as a power function of the proper probabilities" [VERIFIED: same vignette]. It shades longshots down (−1000/+600 → 0.895/0.105, against 0.883/0.117 additive) [VERIFIED: derived] |
| Shin | pᵢ = [√(z² + 4(1−z)πᵢ²/Π) − z] / [2(1−z)], z solved so that Σpᵢ = 1 | Assumes a share z of insider bettors [VERIFIED: implied vignette; https://raw.githubusercontent.com/mberk/shin/master/README.md]. The `shin` README says such probabilities "have been shown to be more accurate than … dividing the inverse odds by the booksum", citing Štrumbelj (2014), IJF 30(4):934–943 [VERIFIED: shin README]. The original's 2022 Annals of OR result (Shin unbiased in the EPL, favourite-biased in La Liga) [UNVERIFIED: https://link.springer.com/article/10.1007/s10479-022-04722-3] |

- **Two-way markets.** "If there are only two outcomes, the additive method and Shin's method are equivalent"
  [VERIFIED: implied vignette; shin README]. Numerically, −150/+130 gives 0.5826/0.4174 under both, and multiplicative
  gives 0.5798/0.4202 [VERIFIED: derived]. Use power or additive for two-way props, and Shin or power for many-outcome
  markets.
- **Libraries.** R `implied`: the GitHub DESCRIPTION is version **0.6.1** [CORRECTED: was "v0.5"; GitHub master
  https://raw.githubusercontent.com/opisthokonta/implied/master/DESCRIPTION; the CRAN version was not re-checked]. It
  also offers `wpo`, odds-ratio, `jsd` and balanced-book methods [VERIFIED: vignette]. Python `shin` 0.2.2 [VERIFIED:
  https://pypi.org/pypi/shin/json].

**Implied team points.** With the home spread S_h in betting convention (negative when home is favoured) and total T:

```
P_home = T/2 − S_h/2        P_away = T/2 + S_h/2           # 3-pt home favourite, T = 48 → 25.5 / 22.5
# nflverse convention (spread_line > 0 when HOME is favoured):  S_h = −spread_line
P_home = total_line/2 + spread_line/2 ;  P_away = total_line/2 − spread_line/2
```

[VERIFIED: derived; sign convention https://raw.githubusercontent.com/nflverse/nfldata/master/DATASETS.md: "A positive
number means the home team was favored by that many points … This lines up with the `result` column."] **Write a unit
test for this sign. It is the single easiest bug to ship.**

**Win probability.** Prefer the de-vigged moneyline (nflverse `games.csv` carries `home_moneyline`/`away_moneyline` and
spread/total odds [VERIFIED: file header]). As a fallback, P(home wins) = Φ(−S_h / σ_m) = Φ(spread_line / σ_m) with
**σ_m ≈ 12.7** for the current era [CORRECTED: was 13.45 (PFR) / 13.86 (Stern); derived from nflverse: 12.87 on
2013–25, 12.63 on 2021–25]. Refit σ_m each season.

**Team TD expectation.** Regress offensive TDs on implied points using historical games with lines. nflverse
`games.csv` has `spread_line` and `total_line` [VERIFIED: https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv
header]. The docs call it "the spread line" and do **not** say it is the closing line [CORRECTED: was "closing
spread_line" (recollection); DATASETS.md]. The licence of the line data is not stated (see `sources_compliance.verified.md`).
Do not hard-code points ÷ 7.

**Team-score simulation (new).** Team points minus the implied total have SD ≈ 9.1, and the two teams' residuals are
nearly uncorrelated (r ≈ 0.03) [VERIFIED: derived, 2021–25]. A game-factor simulator can therefore draw each team's
points around its implied total with SD ≈ 9 and almost independent errors. Use a discrete scoring model if exact
DST tiers matter.

**Line movement after news** is the best available estimate of a news item's team-level effect, for example a QB being
ruled out. Re-read lines in the last-hour pass and propagate the change top-down. (This is design judgment.)

### 2.2 Volume layer

- **League play volume.** Attempts + sacks + carries per team-game: 63.11 (2023), **62.15 (2024), 61.26 (2025)**
  [VERIFIED: derived, nflverse team stats]. These match the original's PFR figures of 62.2 and 61.3
  [UNVERIFIED: https://www.pro-football-reference.com/years/NFL/]. PFF's 65.3/64.3 (it counts plays differently) and
  "fewest per game since 1992" [UNVERIFIED: https://www.pff.com/news/fantasy-football-nfl-pace-volume-how-no-huddle-trends-shape-2026-value].
  **Use one definition consistently.** Use the nflverse one, because it can be recomputed.
- **Team plays model.** Fit a regression (SD of team plays ≈ 8.3 per game [VERIFIED: derived]):

```
plays_team = β0 + β1·neutral_pace_team + β2·neutral_pace_opp + β3·T + β4·winprob_team + ε
```

  Neutral situations are commonly defined as the first three quarters, score within 7–8 points, excluding the last two
  minutes of the half [UNVERIFIED: https://www.4for4.com/2025/preseason/how-play-calling-tendencies-affect-fantasy-football].
  This is a parameter. Pick one definition and backtest.
- **Pass rate.** nflfastR's `xpass` is the "Probability of dropback". `pass_oe` is "Dropback percent over expected".
  The model starts in 2006, "since that was before NFL started marking scrambles". The file header names Ben Baldwin as
  author. Model features: `down, ydstogo, yardline_100, qtr, wp, vegas_wp, era, score_differential, home,
  half_seconds_remaining, posteam/defteam timeouts, roof (outdoors/retractable/dome)`.
  [VERIFIED: https://raw.githubusercontent.com/nflverse/nflfastR/master/R/helper_add_xpass.R]
  **Note:** xpass already conditions on `wp` and `vegas_wp`. When you average it over simulated game states, generate
  those states from the same spread, or the script effect is counted twice.

```
dropback_rate_team = E_script[xpass | spread, total] + PROE_team_shrunk
PROE_team_shrunk   = (n_plays / (n_plays + n0)) · PROE_team_raw    # n0 ≈ a few hundred plays; fit it
```

  Favoured teams spend more time leading, so their xpass is lower. Get the script effect by averaging xpass over
  simulated game states, or by regressing team dropback rate on the spread.
- **Outputs:** dropbacks, then pass attempts (net of sacks and scrambles); designed rushes; and red-zone plays as the
  team TD expectation times an RZ-trip ratio fitted from play-by-play.

### 2.3 Usage layer (the most important player-level input)

**Data reality (omission in the original).** Free nflverse data in 2026 has **no per-player routes or participation**.
The 2026 participation file returns 404, FTN participation arrives only after the season, and the weekly player stats
have no route or snap columns. PFR snap counts (`offense_snaps`, `offense_pct`) are available weekly
[per `sources_compliance.verified.md`; snap counts "provided by Pro Football Reference" VERIFIED:
https://raw.githubusercontent.com/nflverse/nflreadr/main/R/load_snap_counts.R]. So the in-season free decomposition is:

```
targets_i  = team_targets · target_share_i                   # team_targets ≈ 0.957 · pass_attempts (VERIFIED: derived)
          (or, with licensed route data: dropbacks · route_participation_i · TPRR_i)
carries_i  = designed_rushes · carry_share_i
snaps_i    = team_plays · snap_share_i                       # free PFR proxy for "on the field"
rzTD_opp_i = team_RZ_plays · RZ_share_i ;   GL_carries_i = team_GL_rushes · GL_share_i
```

Then normalise so that Σᵢ targetsᵢ = team targets and Σᵢ carriesᵢ = designed rushes.

**Why shares (re-estimated).** Players with ≥ 8 games in consecutive seasons, 2021→25: WR target share r = 0.81
(R² 0.66), WR yards per target r = 0.26 (R² 0.07), WR receiving yards per team pass attempt r = 0.77 (R² 0.59).
TE: target share 0.79, YPT 0.33. RB: target share 0.69, YPT 0.08 [VERIFIED: derived, `yoy.py`]. The original's figures
point the same way: Sumer WR target share r ≈ 0.70; Sharp WR target share R² ≈ 0.40, YPT ≈ 0.05, yards per team
attempt ≈ 0.57; 4for4 TE target share 0.51, YPT 0.34; FTN targets per game R² ≈ 0.56
[UNVERIFIED: https://sumersports.com/the-zone/sticky-football-stats-predictive-nfl-metrics/ ;
https://www.sharpfootballanalysis.com/fantasy/wide-receiver-stats-that-matter-fantasy-football-2023/amp/ ;
https://www.4for4.com/2023/preseason/most-predictable-tight-end-stats ; https://ftnfantasy.com/?p=211]. My numbers are
higher partly because the ≥ 8-games filter selects stable players.

**Weighting the game log.** Combine recency weighting with empirical-Bayes shrinkage for every rate r with trials n:

```
w_g        = 0.5 ** (age_in_games_g / h)        # half-life h: fit by backtest, try h ∈ {3,4,6,8,12}
             × 1[normal role in game g]          # drop or downweight injury-exit and garbage-time-only games
             × context_g                         # downweight games where key teammates were out
r_post     = (Σ w_g · x_g + n0 · r_prior) / (Σ w_g · n_g + n0)
r_prior    = role prior (e.g. "outside WR1 on a 60%-dropback team"), prior-season value regressed,
             or draft-capital/age prior for rookies
n0         = prior strength in "pseudo-trials"
```

- **Choosing n0.** For a beta-binomial rate, the sample size at which split-half reliability reaches 0.5 equals the
  prior pseudo-count n0, since reliability = n/(n + n0). So published "stabilisation points" are direct n0 estimates.
  This is a standard result [UNVERIFIED as a citation; follows from the formula]. The original's Footballguys YPRR
  stabilisation points are about 351 routes (WR), 318 (TE) and 308 (RB) [UNVERIFIED:
  https://footballguys.com/article/tuccittotfp0wr ; https://www.footballguys.com/article/tuccittotfp0rb]. Routes are
  not free in 2026, so re-estimate n0 per stat from nflverse by split-half reliability.
- **Precedent.** Efron & Morris (1975) shrank 18 players' first-45-at-bat 1970 averages toward the grand mean and beat
  the raw averages for the rest of the season [UNVERIFIED: https://arxiv.org/pdf/2111.02829; recollection agrees].
- **Role drift.** Use a local-level model (θ_t = θ_{t−1} + η, y_t = θ_t + ε). Its steady-state Kalman gain equals the
  exponential-smoothing α = (√(q² + 4q) − q)/2 with q = σ_η²/σ_ε²: q = 0.01 → α = 0.095, q = 0.1 → 0.270, q = 1 → 0.618
  [VERIFIED: derived, Riccati iteration matches]. Fit q per stat.

**Opponent adjustment.** Use per-play efficiency allowed (EPA or success rate allowed against pass or rush) rather than
"fantasy points allowed to position". Shrink it:

```
opp_factor = 1 + k · (opp_rate_allowed / league_rate − 1),   k = n_opp / (n_opp + n0_opp)
```

Splits by receiver alignment (slot or outside) are noisier. Shrink them more or skip them.

**Composite usage metrics** work as features, not models. In nflfastR, `wopr = 1.5 * target_share + 0.7 *
air_yards_share` and `racr = receiving_yards / receiving_air_yards` (with an RB sign rule "following Josh Hermsmeyer's
definition") [VERIFIED: https://raw.githubusercontent.com/nflverse/nflfastR/master/R/calculate_stats.R]. Both ship in
the nflverse weekly stats. `ffopportunity` builds expected fantasy points by "applying an xgboost model to nflverse
play-by-play", "trained on public nflverse data from 2006-2020". Authors are Joe Sydlowski and Tan Ho (maintainer).
**Licence: code GPL-3; "models and expected points data … CC-BY-SA 4.0"**
[VERIFIED: https://raw.githubusercontent.com/ffverse/ffopportunity/main/DESCRIPTION and README.md].

### 2.4 Efficiency layer

Efficiency is shrunk hard. Starting points for n0, to be tuned in backtests:

| Rate | Prior | Note |
|---|---|---|
| Catch rate | Expected catch rate from aDOT and target depth | Moderate shrink |
| Yards per target, yards per catch | Role, aDOT and team-QB prior | Heavy shrink: WR YPT YoY R² = 0.07 [VERIFIED: derived]. Yards per team pass attempt is much stickier (R² 0.59) [VERIFIED: derived], because it carries the share |
| Yards per carry | Team rushing environment plus opponent | Very heavy shrink |
| TDs per opportunity | xTD from field position (RZ and GL shares) | Very heavy shrink. Prefer the usage-based expected TD rate |
| QB yards per attempt, TD% | Team plus prior seasons plus opponent pass defence | Moderate |
| QB INT% | League prior | Heavy shrink |

### 2.5 From stat lines to fantasy points (always simulate)

Bonuses make the mapping nonlinear, so E[FP] ≠ FP(E[stats]). **DraftKings** NFL Classic pays +3 each at 300 passing,
100 rushing and 100 receiving yards, plus 0.04 per passing yard, 4 per passing TD and 1 per reception, with DST
points-allowed tiers 0 → 10, 1–6 → 7, 7–13 → 4, 14–20 → 1, 21–27 → 0, 28–34 → −1, 35+ → −4
[VERIFIED via `scoring.verified.md`, its sources V2/V3/V4]. **FanDuel**: 0.5 per reception, 4 per passing TD, −1 per
interception thrown, defensive interception 2 [VERIFIED via `scoring.verified.md`, V5/V6/V7]. FanDuel **also** lists the
same three +3 yardage bonuses, added in 2024 [CORRECTED: original implied DK only; `scoring.verified.md` V7, a dated
copy of fanduel.com/rules; confirm on the first FD slate]. Simulate:

```
targets   ~ NegBin(mean = m_tgt, size = r_tgt)          # r fitted by position/role
catches   ~ Binomial(targets, catch_rate)
rec_yards = Σ_{catches} yards per catch ~ Gamma/lognormal per catch (fit per role); check the SUM's shape
            against the empirical weekly shape in §E (median ≈ 0.93–0.94 × mean)
rec_TD    ~ Poisson(λ_recTD)                             # tied to targets via RZ share
carries   ~ NegBin(m_car, r_car); rush_yards = Σ carry yards (heavy-tailed per carry; fit empirically)
FP        = score(stat_line, scoring_table)              # scoring table read from contest rules, never hard-coded
```

### 2.6 Props: the strongest single input

**Which markets.** Main-market props (passing yards and attempts, rushing yards and attempts, receiving yards,
receptions, anytime TD) usually exist for most NFL starters. CFB coverage is sparse. The original reports a practitioner
view that props are priced less carefully than sides and totals [UNVERIFIED: https://establishtherun.com/nfl-player-props-we-bet-week-8/].
It also reports PFF's blind-bet tally (passing-yard unders the only profitable category, +14.06 units; receiving-yard
unders winning 52.98%) [UNVERIFIED: https://www.pff.com/news/nfl-betting-2022-midseason-player-prop-performance-review],
and that no peer-reviewed study of NFL prop efficiency was found [UNVERIFIED: an absence claim, not re-searched].

**Line to distribution.** The line sits near the median [UNVERIFIED: https://www.stokastic.com/articles/dfs-strategy/player-props-for-dfs-players].
De-vig the two sides to get p_over.

- **Continuous yards: do not use a plain lognormal for the mean.** [CORRECTED: was "lognormal, s = 0.75: line 64.5 →
  mean 85.4"; derived, nflverse 2021–25.] Weekly yardage is much less right-skewed than a lognormal. For WRs averaging
  55–75 receiving yards, median/mean = 0.940 (CV 0.568). For RBs averaging 55–75 rushing yards, 0.929 (CV 0.553). For TEs
  averaging 40–60, 0.931 (CV 0.620). A lognormal with the same CV gives mean/median 1.14–1.18, a gamma 1.11–1.14, and the
  data 1.06–1.08. The original's s = 0.75 gives 1.32. Use an **empirical shape template**:

```
# Fit once per (position, stat, mean-bucket): the pooled distribution of r = x / player_season_mean   (§E)
# Line L with de-vigged p_over (half-point line):
m_hat  = L / Q_r(1 − p_over)              # Q_r = empirical quantile function of r; at p_over = 0.5, Q_r(0.5) ≈ 0.94
quantile_x(p) = m_hat · Q_r(p)
# Example: L = 64.5, p_over = 0.50 → m_hat ≈ 64.5 / 0.94 ≈ 68.6   (not 85.4)
```

  The lognormal algebra is still correct as maths: P(X > L) = p_over gives μ = ln L − s·Φ⁻¹(1 − p_over), and
  mean = e^{μ + s²/2}. With s = 0.75 and p_over = 0.5, mean = 85.45 [VERIFIED: derived]. It is the wrong **family**.
- **Counts (receptions, attempts, completions): NegBin or Poisson.** For a half-point line, solve for m by bisection so
  that 1 − F(⌊L⌋; m, r) = p_over. **For an integer line** (push possible), match P(X > L) / (1 − P(X = L)) = p_over
  (omission in the original).
- **Anytime TD:** P(≥ 1 TD) = 1 − e^{−λ}, so λ = −ln(1 − p). For +150, the raw p is 0.40 and λ = 0.511 before de-vig
  [VERIFIED: derived]. The method mirrors backing a Poisson λ out of over/under prices
  [UNVERIFIED: https://www.r-bloggers.com/2020/09/expected-goals-from-over-under-odds/amp/]. Anytime-TD markets are
  usually one-sided with a heavy margin. De-vig with a market-average margin, then rescale so Σᵢ λᵢ ≈ team expected
  non-passing TDs (§2.1). Note that anytime TD excludes passing TDs.

**Reconcile props with the model** using precision weighting, then a coherence step:

```
m_i = (m_model/v_model + m_prop/v_prop) / (1/v_model + 1/v_prop)
# v_prop: small for liquid main lines, larger for thin or stale lines; learn both v's from backtest residuals.
# Model and prop errors are positively correlated, so these weights over-trust the combination: inflate both v's,
# or fit the weight directly (§7).

# coherence: team constraint Σ m_i = M_team (targets, rush yards, team pass yards = Σ receiving yards)
m_i' = m_i + v_i · (M_team − Σ_j m_j) / Σ_j v_j          # WLS projection; uncertain players absorb more; clip at 0
```

[VERIFIED: derived. It is the minimiser of Σ(m'ᵢ − mᵢ)²/vᵢ subject to Σm'ᵢ = M.]

**Consistency check.** Team passing yards equal the sum of receiving yards exactly (maximum difference 1 yard in
2021–25 data) [VERIFIED: derived]. So compare **means to means**: the QB's prop-implied *mean* (via the shape template,
not the raw median line) against the sum of his pass-catchers' receiving-yard means, including those without props. Big
gaps reveal stale lines or a wrong usage split.

### 2.7 Redistributing usage when a player is ruled out ("vacated" targets and carries)

The original reports practitioner evidence that vacated targets **do not** follow position mechanically (for example,
Baltimore's 181 vacated targets went mostly to a TE), and that RBs often absorb a larger share
[UNVERIFIED: https://forums.footballguys.com/threads/wr-a-j-brown-phi.788301/page-17 ;
https://www.thefantasyfootballers.com/articles/vacated-targets-the-rb-position-in-fantasy-football/; a forum post, low].
NBA on/off splits are informative but small samples mislead [UNVERIFIED: Stokastic]. Fit the affinity matrix on
nflverse games where a starter was out; this can be estimated with free data. Algorithm, with fixes:

```python
def redistribute(team, out, depth_chart, gamma=1.0, n0_split=8):
    # 0. Team level first: if the market moved after the news, the re-read team total, pass rate and plays
    #    already reflect it. Otherwise apply a role-based prior impact (large for QB, small for one pass-catcher).
    # 1. Snaps go to the depth-chart replacement at the same role/alignment.
    repl = depth_chart.next_up(out.role, out.alignment)
    added_share = out.snap_share * p_inherit(out, repl)              # p_inherit ≈ 0.8–1.0; fit it
    repl_new_share = min(1.0, repl.snap_share + added_share)
    # 2. The replacement earns targets at HIS OWN rate per snap (or TPRR if routes are licensed), not the departed player's.
    V      = out.targets_mean
    V_repl = (repl_new_share - repl.snap_share) * team.plays_mean * repl.tgt_per_snap_post
    V_rest = max(0.0, V - V_repl)
    # 3. The remainder goes to the other pass-catchers ∝ share^gamma × affinity(position pair).
    pool = [p for p in team.pass_catchers if p not in (out, repl)]
    wts  = [p.tgt_share**gamma * affinity(out.pos, p.pos) for p in pool]
    W = sum(wts)
    if W > 0:
        for p, w in zip(pool, wts):
            p.targets_mean += V_rest * w / W
    repl.snap_share, repl.targets_mean = repl_new_share, repl.targets_mean + V_repl
    # 4. Shrink toward with/without-split evidence when it exists.
    for p in pool + [repl]:
        if p.split_n_games:                               # games played without `out`
            k = p.split_n_games / (p.split_n_games + n0_split)
            p.targets_mean = k * p.split_targets + (1 - k) * p.targets_mean
    # 5. Re-impose team totals (coherence step, §2.6) and recompute TD shares.
    #    The goal-line role transfers separately (often to a different back than the early-down role).
```

(Fixes: the original referenced undefined `routes_new`/`routes_old`, never credited the replacement's targets, and
could divide by zero.)

- **RB out.** Carries go mostly to the next RB on the depth chart. Split the goal-line and receiving-down roles
  separately.
- **QB out.** A team-efficiency shock. Trust the market change in the team total, lower YPT and TD rates for every
  pass-catcher, and weight the backup's own history more.
- **Depth charts.** nflverse 2026 depth charts are ESPN-derived (`sources_compliance.verified.md`). Record a timestamp
  and source for each role claim.

### 2.8 Availability, inactives, early exits

- **Status to P(active)** [CORRECTED: was "no reliable post-2016 questionable play rate"; derived, `qrate.py`, nflverse
  injuries + PFR snap counts, REG 2022–25]:

| Final status | P(plays ≥ 1 snap) | n |
|---|---|---|
| Out | 0.000 | 4,471 |
| Doubtful | 0.005 | 602 |
| Questionable, overall | 0.660 | 5,693 |
| Questionable, final practice DNP | 0.512 (skill positions 0.471) | 907 (263) |
| Questionable, final practice Limited | 0.674 (skill 0.652) | 3,446 (1,062) |
| Questionable, final practice Full | 0.727 (skill 0.733) | 1,239 (360) |
| On the practice report, no game status | 0.936 | 11,866 |

  Use these as priors. Refine with a logistic model on position, practice trajectory (Wed/Thu/Fri), day of game and
  beat-report language, and update once inactives post. "Played" counts any snap, so a questionable player who plays
  can still have a reduced role: model the snap share given active separately. The original also says the NFL dropped
  "probable" in 2016, and that about 95% of probable players played under the old system
  [UNVERIFIED: https://www.foxnews.com/sports/nfl-makes-change-to-team-injury-reports-that-could-have-major-impact;
  recollection agrees on the 2016 change]. Its claim that the old "questionable" meant "less than 50%" is
  [UNVERIFIED; my recollection is that the old definition was "50-50"]. It no longer matters given the data above.
- **Inactives** are due about 90 minutes before kickoff
  [UNVERIFIED: https://www.cbssports.com/nfl/news/nfl-makes-rare-in-season-rule-change-following-awkward-covid-19-testing-situation-with-dez-bryant ;
  recollection agrees]. Per `sources_compliance.verified.md`, no free structured feed for game-day inactives was found.
- **In-game exits.** The original cites 2,846 in-game injuries in 2013–16, 1.26 per game on short rest against 1.53 on
  regular rest, unit unclear [UNVERIFIED: https://pubmed.ncbi.nlm.nih.gov/32412782/]. The "about 46 active players per
  team" figure used to turn that into a per-player rate is also [UNVERIFIED; recollection says game-day actives have been
  47–48 since 2020]. **Estimate p_exit per position directly:** the share of games where the player's `offense_pct`
  falls below half his trailing median while he is listed active. This can be computed from nflverse snap counts.

---

## 3. College football specifics

| Issue | Why it matters | What to do |
|---|---|---|
| Tempo varies by team | The original reports average time between plays at about 26.9 s since 2023, with only two FBS teams under 22 s per play in 2025 (FAU 21.3, USF 21.8) [UNVERIFIED: https://www.espn.com/college-football/story/_/id/49577220/college-football-2026-trends-see-ohio-state-miami]. West Virginia was the fastest offence in one season (19.3 s left on the play clock, 879 snaps) [UNVERIFIED: PFF; low] | Plays_team from each offence's and defence's seconds per play and expected possessions. Shrink to the FBS mean early in the season |
| Opponent strength and talent gaps | SP+ is described as a tempo- and opponent-adjusted efficiency measure (Connelly, 2008); the preseason version leans on returning production and recruiting and is phased out [UNVERIFIED: https://abc7chicago.com/post/2026-college-football-sp-rankings-138-fbs-teams/19826490/; recollection agrees] | Use the market first. Use opponent ratings to adjust efficiency, shrunk more for FCS opponents |
| Blowouts | "Blowouts are the single most important force in CFB DFS"; starters on big favourites may sit [UNVERIFIED: https://www.stokastic.com/articles/dfs-strategy/college-football-dfs-strategy]. Coaches differ (Lane Kiffin is cited as leaving starters in late) [UNVERIFIED: RotoWire]. **Key these parameters by head coach, not by team**: coaches move. My recollection, [UNVERIFIED], is that Kiffin left Ole Miss for LSU after the 2025 regular season | Model snap share as a function of win probability or spread: `starter_share = 1 − b·σ(a·(|spread| − c))`, with a, b, c fitted per coach from play-by-play. Backups gain the remainder. The NBA analogue (RotoGrinders: cut minutes 1.5% per point of spread beyond 7) is [UNVERIFIED: https://rotogrinders.com/fantasy/lessons/accurately-predicting-minutes-nba-dfs] |
| Workload volatility | Committee backfields and rotations | Wider distributions. Mix "keeps role" and "loses role" scenarios |
| Thin data and roster churn | Transfers and short careers make prior seasons less relevant | Stronger role priors (recruiting rank, transfer history, position-room depth). Accept wider intervals |
| Availability reports | The original says: Big Ten (2023), SEC (2024), ACC and Big 12 (2025); Big 12 five categories from 3 days out with a final report 90 min before kickoff; Mountain West conference games only; CFP required from 2025 [UNVERIFIED: https://www.cbssports.com/college-football/news/big-12-to-mandate-player-availability-reports-for-football-basketball-starting-in-2025-26 ; https://www.si.com/college/boise-state/football/mountain-west-follows-lead-power-four-adds-player-availability-reports-2025-football-season] | Parse availability reports in the last-hour pass. Non-conference games may have none |
| Props | Sparse beyond marquee games [UNVERIFIED; judgment] | Use where available. Otherwise derive from team totals × shares |
| Data access | **CFBD API: free with a key; "monthly call limits" ("Free Tier: 1,000 calls per month"), not per-minute rate limits; Patreon tiers give more.** Some endpoints (e.g. `/games/weather`) require Patreon | [CORRECTED: was "rate-limited"; https://raw.githubusercontent.com/CFBD/cfbd-net/main/docs/authentication.md]. At 1,000 calls/month (≈ 33/day), pull history from sportsdataverse releases and spend CFBD calls only on the live slate. `GET /info` reports remaining calls (`sources_compliance.verified.md`) |

**CFB baseline** (an upgrade to "season averages adjusted for recent workload"):

```
plays_team   = f(tempo_off, tempo_def_opp, spread, total)
P_team       = implied team total (market) else SP+-style projection
share_i      = recency-weighted, shrunk season share (snaps, carries, targets), with the role prior
playtime_i   = starter_share(spread, coach) for starters ; 1 − starter_share for backups
eff_i        = shrunk efficiency × opponent adjustment, capped for FCS opponents
FP_i         = simulate(stat line) under the contest scoring
```

**Caution on "college = 100% LLM forecast".** The model knows less about college rosters than NFL rosters, rosters churn
through transfers, and its knowledge stops around June 2026. That makes it more likely to be wrong on CFB, not less. A
100% weight is optimal only if σ_LLM/σ_baseline ≤ ρ, the correlation between their errors [VERIFIED: derived, §7.2]. That
is testable, and probably false.

---

## 4. Turning a low/likely/high triple into a full distribution

### 4.1 Elicitation contract for the forecaster

Ask for **P10, P50 and P90 of fantasy points, conditional on being active and playing the normal role.** Ask separately
for `p_inactive` and `role_risk` notes. Fantasy points are computed in code under the contest scoring, so the model
should ideally forecast **stat lines** (targets, receptions, yards, TDs), which are then scored. Do not mix injury risk
into the triple. The code adds it (§4.4), seeded with the §2.8 priors.

### 4.2 Closed-form fits

**(a) Shifted lognormal** for right-skewed triples (q90 − q50 > q50 − q10); z = Φ⁻¹(0.9) = 1.28155:

```
c  = (q10·q90 − q50²) / (q10 + q90 − 2·q50)       # shift; requires q10 + q90 − 2·q50 > 0 (then c < q10)
μ  = ln(q50 − c)
σ  = ln((q90 − c)/(q50 − c)) / z
mean = c + exp(μ + σ²/2)
quantile(p) = c + exp(μ + σ·Φ⁻¹(p))
```

(5, 12, 25) → c = −3.1667, μ = 2.7191, σ = 0.4830, mean = 13.877, and the fit reproduces q10 = 5 exactly
[VERIFIED: derived]. If the triple is symmetric (denominator ≈ 0), use a normal with σ = (q90 − q10)/(2z). If it is
left-skewed, reflect or use the metalog. **Sanity check (new):** §E shows real weekly outcomes are only mildly right-skewed
(median ≈ 0.93–0.94 × mean for yardage). If fitted triples routinely imply mean/median > 1.15, the forecaster's upper
tail is probably too long.

**(b) Three-term metalog** (Keelin 2016, Decision Analysis 13(4):243–277) [citation UNVERIFIED:
https://ideas.repec.org/a/inm/ordeca/v13y2016i4p243-277.html ; https://www.informs-sim.org/wsc19papers/340.pdf]:

```
Q(y) = a1 + a2·ℓ(y) + a3·(y − 0.5)·ℓ(y),   ℓ(y) = ln(y/(1−y))
with y ∈ {0.1, 0.5, 0.9}, L = ln 9:
a1 = q50 ;  a2 = (q90 − q10)/(2L) ;  a3 = (q90 + q10 − 2·q50)/(0.8·L)
mean = a1 + a3/2                       # since E[(Y − 0.5)ℓ(Y)] = 1/2 for Y ~ U(0,1)
feasibility (k = 3): a2 > 0 and |a3|/a2 < 1.66711
```

The coefficients follow from solving the three equations. (5, 12, 25) gives a1 = 12, a2 = 4.5512, a3 = 3.4134 and
mean = 13.707; Monte Carlo with 2M draws gives 13.704 [VERIFIED: derived]. **Feasibility:** Q′(y) > 0 requires
1 + (a3/a2)·g(y) > 0 with g(y) = y(1−y)ℓ(y) + y − 0.5. Then max|g| = 0.59984, so the bound is |a3|/a2 < 1/0.59984 =
**1.66711** [CORRECTED: was "from memory, UNCONFIRMED"; now VERIFIED: derived numerically]. The example's ratio is 0.75.
For a lower bound b_l (for example −4 points), use the semi-bounded metalog: fit on ln(x − b_l).

**(c) Quick mean.** Swanson's 30-40-30 rule: mean ≈ 0.3·P10 + 0.4·P50 + 0.3·P90 [citation UNVERIFIED: Hurst, Brown &
Swanson, AAPG Bulletin 2000, https://archives.datapages.com/data/bulletns/2000/12dec/1883/1883.htm]. On the example it
gives 13.8, against 13.88 (lognormal) and 13.71 (metalog) [VERIFIED: derived]. Use it as a sanity check only.

### 4.3 Recalibrate the LLM's intervals before use

The original's cited evidence that LLM intervals are too narrow [all UNVERIFIED; arXiv unreachable today]:

- FermiEval: nominal 99% intervals cover about 65%; a conformal adjustment restored about 99% and cut the Winkler score by
  54% (https://arxiv.org/abs/2510.26995).
- QuantSightBench: no model reached 90% coverage on 90% intervals; best 79.1% (Gemini 3.1 Pro), Grok 4 76.4%, GPT-5.4
  75.3%, lowest about 61–63% (https://arxiv.org/abs/2604.15859).
- Eleven LLMs' 95% intervals on population statistics contained the truth 9–44% of the time; conformal prediction
  restored coverage (https://arxiv.org/abs/2604.01896).

**Whatever those papers say, the engine must measure its own coverage** on post-cutoff player-games. Apply
conformalised calibration. CQR "construct[s] prediction intervals that attain valid coverage in finite samples, without
making distributional assumptions", and the guarantee is **marginal**
[VERIFIED: https://raw.githubusercontent.com/yromano/cqr/master/README.md; paper https://arxiv.org/abs/1905.03222]:

```
score_j = max(q10_j − y_j, y_j − q90_j)                # past player-games j, per position group (and per forecaster version)
Q̂ = the ⌈(n+1)(1−α)⌉-th smallest score               # α = 0.2 for an 80% band; needs n ≥ 1/α − 1
q10' = q10 − Q̂ ;  q90' = q90 + Q̂
# asymmetric/scale variant for skewed bands:
score_j = max((q10_j − y_j)/(q50_j − q10_j), (y_j − q90_j)/(q90_j − q50_j)) ;  widen each side by Q̂ × its half-width
```

**Caveats (omission in the original).** The guarantee needs exchangeability. Players in one slate share game outcomes, and
forecaster prompts change over time. Calibrate on a rolling window, report coverage per week (not only pooled), and
re-fit after any prompt or model change.

Also check the median: regress y on q50 within each position (Mincer–Zarnowitz). A slope below 1 means the forecast's
location is over-dispersed; shrink it toward the baseline.

### 4.4 Add the zero, inactive and early-exit mass

```
X = 0                              with prob p_out            (inactive or scratched; DFS score 0)
X = U · X_full ,  U ~ Uniform(0,1) with prob (1−p_out)·p_exit (left early)
X = X_full                         with prob (1−p_out)(1−p_exit)
E[X] = (1−p_out)·[(1−p_exit)·m + p_exit·m/2]
```

m = 14, p_out = 0.15, p_exit = 0.04 → E[X] = 11.662 [VERIFIED: derived]. For a questionable player, p_out ≈ 0.34 before
news (§2.8). The lineup solver should mostly use **conditional-on-active** numbers after the inactive check, because an
inactive player is removed, not scored at zero. Before inactives are known, use the unconditional mixture for
questionable players.

**Ceiling and floor** are read from the **final mixture**: floor = P10 and ceiling = P90 (or P95 for large-field
tournaments). Also store P(X ≥ threshold) for site bonuses and "value" thresholds such as 4 × salary/1000.

---

## 5. Correlation structure and simulation

### 5.1 Magnitudes

**Use the reproducible within-pair values as priors** [VERIFIED: derived, nflverse 2021–25, DK scoring, `nflemp.py`].
The published figures are shown for reference. They could not be checked today, and most could not be reproduced.

| Pair | nflverse 2021–25 (median per-pair r / pooled within-pair z r) | Published (original) | Status of published |
|---|---|---|---|
| QB–WR1, same team | 0.36 / 0.35 | 0.53 (FantasyLabs), 0.542 (Spike Week), 0.37 (GitHub sim) | [UNVERIFIED; FantasyLabs and Spike Week not reproduced] |
| QB–WR2 | 0.38 / 0.35 | 0.51, 0.514 | [UNVERIFIED; not reproduced] |
| QB–TE1 | 0.30 / 0.28 | 0.47, 0.31 | [UNVERIFIED] |
| QB–RB1 | 0.05 / 0.06 | 0.42, 0.07 | [UNVERIFIED; the 0.42 is clearly not reproduced] |
| QB–opp WR1 ("bring-back") | 0.08 / 0.12 | 0.37 | [UNVERIFIED; not reproduced] |
| WR1–opp WR1 | 0.06 / 0.08 | 0.56 | [UNVERIFIED; not reproduced] |
| WR1–WR2, same team | 0.01 / 0.02 | 0.345 | [UNVERIFIED; not reproduced] |
| WR1–RB1, same team | −0.03 / −0.02 | 0.002; slightly negative at ceilings (Underdog) | [UNVERIFIED] (consistent) |
| QB–opp QB | 0.18 / 0.19 | n/a | new |
| RB1–opp RB1 | −0.05 / −0.08 | n/a | new (game-script effect) |
| DST–opponent points scored | −0.71 (2023–25, approximate DK DST scoring) | −0.784 (FantasyLabs, median since 2010); INT 0.639, sacks 0.577 | [UNVERIFIED] (consistent: derived INT 0.55, sacks 0.59) |
| DST vs the opposing offence of your own stack | negative (follows from the row above) | "never roster" (Stokastic) | [UNVERIFIED] (direction consistent) |
| Both QBs 25+ DK | P(opp ≥ 25 \| QB ≥ 25) = 0.29, base 0.18 | "about 6 in 10" (4for4, 2017) | [CORRECTED: was 6 in 10; derived] |

Sources of the published values: https://www.fantasylabs.com/articles/introducing-nfl-correlation-values/ ;
https://spikeweek.com/visualizing-single-game-correlation/ ; https://github.com/SrWildman/dfs/pull/3 ;
https://underdognetwork.com/football/best-ball-research/correlation-at-ceiling-outcomes-between-teammates-and-their-opponents ;
https://www.fantasylabs.com/?p=2683 ; https://www.stokastic.com/articles/nfl-dfs/nfl-dfs-defense-strategy ;
https://www.4for4.com/2017/w9/raybons-review-dfs-lessons-learned-week-8.

**Why the gap.** Possible reasons: different eras (pre-2021), scoring, role definitions (weekly rather than season role),
samples restricted to games where both players had their full role, or pooling that mixes in level differences between
teams. Pooling raw values without centering did **not** inflate them in my check, which rules out the simplest
explanation. Re-estimate the table yourself in normal-score residual space (§5.2) and use it. Do not import the
published numbers.

### 5.2 Estimate residual correlations in normal-score space

Projections already contain the shared game environment through team totals. The simulator needs the correlation of
**surprises**:

```
for each historical player-game: u = F̂_i(y_i)   # PIT using the forecast distribution at the time
                                 # for mixed/discrete outcomes (zero mass, integer TDs) use randomized PIT:
                                 # u = F(y−) + V·(F(y) − F(y−)), V ~ U(0,1)
                                 z = Φ⁻¹(clip(u, 1e-4, 1−1e-4))
group pairs by role-pair key (QB–WR1 same team, WR1–oppWR1, RB1–DST same team, …)
ρ̂_key = Pearson(z_a, z_b)                        # estimates the Gaussian-copula parameter
# from Spearman on raw residuals: r = 2·sin(π·ρ_s/6) ; from Kendall: r = sin(π·τ/2)
```

The rank-to-Pearson identities hold for population values under a Gaussian copula (Kruskal 1958). Spearman and Pearson
differ by at most 0.01808 (at |r| ≈ 0.594) [VERIFIED: derived numerically; citations UNVERIFIED:
https://arxiv.org/abs/1206.6488 ; https://rdrr.io/cran/copBasic/man/NORMcop.html]. The sample Kendall τ is a U-statistic
and unbiased for τ, while sample Spearman is biased [UNVERIFIED as a citation; standard result, recollection agrees].

Then shrink each ρ̂ toward a structural prior: ρ_shrunk = (n·ρ̂ + n0·ρ_prior)/(n + n0). Use §5.1's derived values as
ρ_prior. Assemble the slate matrix and repair it to the nearest correlation matrix (Higham 2002, IMA J Numer Anal
22(3):329–343, alternating projections with a Dykstra correction, linear convergence) [citation UNVERIFIED:
https://eprints.maths.manchester.ac.uk/232/; recollection agrees]. A cheap substitute: clip eigenvalues at ε > 0
(for example 1e-6), then rescale to a unit diagonal.

### 5.3 Simulation (Gaussian copula with a t option)

```python
import numpy as np
from scipy import stats

def simulate(players, R, n_sims=20000, df=None, seed=0):
    rng = np.random.default_rng(seed)                       # fresh generator per call (the original used a mutable default arg)
    w, V = np.linalg.eigh(R)                                # R must be PD for Cholesky: floor eigenvalues first
    R = (V * np.clip(w, 1e-6, None)) @ V.T
    d = np.sqrt(np.diag(R)); R = R / np.outer(d, d)
    L = np.linalg.cholesky(R)
    Z = rng.standard_normal((n_sims, len(players))) @ L.T
    if df:                                                  # t copula: joint tails (ceiling games together)
        W = rng.chisquare(df, size=(n_sims, 1)) / df
        U = stats.t.cdf(Z / np.sqrt(W), df)
    else:
        U = stats.norm.cdf(Z)
    X = np.column_stack([p.quantile(U[:, j]) for j, p in enumerate(players)])  # mixture quantile fn (§4), incl. zero mass
    return X                                                # n_sims × n_players fantasy points
```

- **Gaussian vs t copula.** A Gaussian copula has zero tail dependence at any correlation below 1. A t copula with low
  degrees of freedom has positive, symmetric tail dependence and tends to the Gaussian as df → ∞
  [UNVERIFIED: https://metricgate.com/docs/t-copula-model; standard result, recollection agrees]. Fit both by
  pseudo-likelihood on historical normal scores and pick by AIC.
- **Alternative: a game-factor model (recommended given §E).** Simulate each team's points around its implied total
  (SD ≈ 9.1, cross-team residual r ≈ 0.03) [VERIFIED: derived]. Then simulate the team pass/rush split, then players as
  shares of team volume plus idiosyncratic noise. It is automatically PSD, needs few parameters, and DST tiers and
  QB–WR stacks come out of the same draws.
- **Precedents.** Hunter, Vielma & Zaman, "Picking Winners Using Integer Programming" (arXiv 1604.01455). The paper and
  its public Julia code exist; by default the code builds 100 lineups with a maximum overlap of 7 players
  [VERIFIED: https://raw.githubusercontent.com/dscotthunter/Fantasy-Hockey-IP-Code/master/README.md]. Its results
  (top-ten finishes in DraftKings NHL and MLB) are [UNVERIFIED]. Haugh & Singal, Management Science 67(1):72–92 (2021):
  a Dirichlet-multinomial model of opponents' picks, reduced to binary quadratic programs
  [UNVERIFIED: https://ideas.repec.org/a/inm/ormnsc/v67y2021i1p72-92.html].

---

## 6. Evaluation metrics (definitions to implement)

| Metric | Formula | Use |
|---|---|---|
| MAE | mean \|y − m\| | Headline; comparable to the FFA numbers (§1) |
| RMSE | √mean (y − m)² | Punishes large misses |
| Bias (ME) | mean (y − m) | Over-projection check |
| Pinball loss at τ | L_τ(q, y) = (y − q)(τ − 1[y < q]) | Per-quantile accuracy at P10, P50, P90 |
| CRPS (sample form, lower is better) | CRPS(F, y) = E\|X − y\| − ½E\|X − X′\|, with X, X′ iid ~ F | Proper for the whole distribution, strictly proper when the first moment is finite (Gneiting & Raftery 2007) [VERIFIED: https://raw.githubusercontent.com/stan-dev/loo/master/R/crps.R docs]. **`loo::crps()` returns the negative** (`0.5*EXX - EXy`) [VERIFIED: same file]. CRPS = 2∫₀¹ L_τ(F⁻¹(τ), y)dτ, i.e. about 2 × the mean pinball loss over a fine τ grid (4.550 vs 4.546 in a numeric check) [VERIFIED: derived] |
| Interval coverage | share of y in [q10, q90]; target 80% | Calibration of the band; report it per week |
| PIT histogram | u = F(y) should be Uniform(0,1) (randomised for discrete outcomes) | A necessary but not sufficient check (Gneiting, Balabdaoui & Raftery 2007, JRSS-B 69(2):243–268) [UNVERIFIED: https://ideas.repec.org/a/bla/jorssb/v69y2007i2p243-268.html; recollection agrees] |
| Spearman ρ within position | rank correlation of m vs y per slate and position | What the lineup solver actually needs |
| Top-k hit rate | share of each position's actual top 3 inside our projected top 3 (or top 6) | Matches the user's "top 3 per slot" framing |
| Lineup efficiency | actual(our lineup) / actual(hindsight optimal) | Grading step. Also track percentile finish and ROI |
| Diebold–Mariano | DM statistic on the loss differential with a Newey–West (HAC) variance | statsmodels 0.15.0 ships `statsmodels.tsa.stattools.diebold_mariano_test(y, forecast_a, forecast_b, *, lags=None, criterion="mse"\|"mad"\|"mape"\|"poly"\|callable, power=2, harvey_adj=False, horizon=1)`, default lags `max(horizon-1, ceil(nobs**(1/3)))` [VERIFIED: statsmodels 0.15.0 sdist from https://pypi.org/pypi/statsmodels/json, `tsa/stattools/_stattools.py`]. It is a time-series HAC test: for a panel slate, **first aggregate the loss differential to one value per week** (or use a week-clustered bootstrap), because errors within a slate are correlated. Power falls under strong dependence (Coroneo & Iacone 2024) [UNVERIFIED: https://arxiv.org/pdf/2409.12662] |

**Grading: which step failed.** Replay the slate counterfactually, swapping in the truth for one layer at a time:
(1) actual active/inactive status; (2) actual team volume and pass rate; (3) actual usage shares; (4) actual efficiency.
Re-solve after each swap. The layer whose correction recovers the most lineup points is the step that failed. The order
matters, so also report the Shapley-style average over orderings when cheap (4 layers = 24 orders). Without this, "fix
that step" is guesswork.

**Look-ahead leakage.** Only games played **after about June 2026** are clean tests of the LLM forecaster. For earlier
games the model may "remember" outcomes. The original reports Glasserman & Lin (2023) finding a "distraction" effect larger
than look-ahead bias, and recommending anonymisation for de-biased backtests [UNVERIFIED: https://arxiv.org/abs/2309.17322].
For older backtests, anonymise player and team names and dates, accepting that this removes narrative context. Clean NFL
sample to date: 93 games [VERIFIED: derived].

---

## 7. Blending and ensembling

### 7.1 Methods

| Method | Weights | Notes |
|---|---|---|
| Equal weights | 1/K | Hard to beat in small samples. Smith & Wallis (2009, Oxford Bull Econ Stat 71(3):331–355) explain the "forecast combination puzzle" by finite-sample error in estimating the weights [UNVERIFIED: https://econpapers.repec.org/RePEc:bla:obuest:v:71:y:2009:i:3:p:331-355; recollection agrees] |
| Inverse variance | wₖ ∝ 1/σₖ² | Optimal when errors are uncorrelated (Bates & Granger 1969) [citation UNVERIFIED: https://arxiv.org/abs/2205.04216; follows from the §7.2 formula with ρ = 0, VERIFIED: derived] |
| Bates–Granger, covariance-optimal | w = Σ⁻¹1 / (1ᵀΣ⁻¹1) | Needs the error covariance Σ, which is noisy to estimate. Smith & Wallis reportedly recommend ignoring covariances [UNVERIFIED] |
| Constrained stacking regression | minimise loss over w ≥ 0, Σw = 1, plus an L2 penalty toward equal weights | Practical default. Fit per position group |
| Stacking of predictive distributions | maximise a proper score (log score or CRPS) of the mixture | Recommended over BMA when no candidate model is true (Yao, Vehtari, Simpson & Gelman 2018, Bayesian Analysis 13(3)) [UNVERIFIED: https://arxiv.org/abs/1704.02030; recollection agrees] |
| Quantile averaging (Vincentization) | average quantiles rather than CDFs | Same mean as probability averaging, but sharper; reported to beat probability averaging on SPF data (Lichtendahl, Grushka-Cockayne & Winkler 2013, Mgmt Sci 59(7):1594–1611) [UNVERIFIED: https://ideas.repec.org/a/inm/ormnsc/v59y2013i7p1594-1611.html]. The "same mean" part follows from E[X] = ∫Q(p)dp, which is linear in Q [VERIFIED: derived]. Use it to blend LLM and baseline distributions |

### 7.2 What the fixed weights imply

For two forecasts L (LLM) and B (baseline) with error SDs σ_L, σ_B and error correlation ρ, the MSE-optimal weight on L
is:

```
w* = (σ_B² − ρσ_Lσ_B) / (σ_L² + σ_B² − 2ρσ_Lσ_B)
```

[VERIFIED: derived, minimising Var(w·e_L + (1−w)·e_B)]. For w* = 0.85, r = σ_B/σ_L solves 0.15r² + 0.7ρr − 0.85 = 0:

| ρ | 0.0 | 0.5 | 0.8 | 0.9 |
|---|---|---|---|---|
| required σ_B/σ_L | 2.380 | 1.484 | 1.158 | 1.074 |
| LLM error SD smaller by | 58% | 33% | 14% | 7% |

[VERIFIED: derived, `mathcheck.py`]. Errors from competent projections are highly correlated, so ρ is plausibly 0.8 or
more [UNVERIFIED; judgment; measure it]. Even then, 85/15 needs the LLM to have 7–14% lower error SD than the baseline,
while public sources reportedly sit within a few percent of one another (§1). The cost of a wrong fixed weight is small
but certain. With σ_B = σ_L and ρ = 0.85, RMSE is 0.9807 for 85/15 against 0.9618 for 50/50 (and 1.0 for either alone)
[VERIFIED: derived]. "100% LLM" for college is optimal iff σ_L/σ_B ≤ ρ [VERIFIED: derived: w* ≥ 1 ⇔ ρσ_Lσ_B ≥ σ_L²].

### 7.3 Sample size before trusting learned weights

To detect a mean loss improvement δ with loss-differential SD s_d (two-sided α = 0.05, power 0.8):

```
n ≈ (z_0.975 + z_0.8)² · s_d² / δ²   = (1.95996 + 0.84162)² · s_d² / δ²
# in INDEPENDENT units: divide the raw player-game count by the design effect for within-week clustering
```

With assumed s_d = 1.5 points and δ = 0.1 points (2% of a 5.0 MAE), n ≈ 1,766 independent player-games
[VERIFIED: derived; inputs are assumptions]. **Clean post-cutoff NFL data on 2026-10-21: 93 games (weeks 1–6)**
[CORRECTED: was "weeks 1–7, ~2,000 player-games"; derived from nflverse games.csv]. At roughly 20 fantasy-relevant
player-games per game, that is about 1,900 raw player-games (an UNVERIFIED rough count). It is far fewer independent
units once within-week clustering is counted. **Learned weights are not identifiable at this δ yet.** Stay near the prior.

Recommended protocol:

1. **Start** from a prior blend: equal weights, or market/prop-heavy where props exist.
2. **Fit** weekly on a rolling window with stacking under an L2 penalty toward the prior. Run per position group, with
   "has props" and "no props" as separate strata.
3. **Change** weights only when a week-clustered bootstrap CI on the loss improvement excludes 0, or a DM test on
   week-aggregated loss differentials rejects. Log every weight change.
4. **Shrink:** w = (n0·w_prior + n·ŵ)/(n0 + n), with n0 of a few hundred player-games.

**LLM ensembles.** The original reports the following [all UNVERIFIED; arXiv unreachable]:

- Schoenegger et al. 2024 (https://arxiv.org/abs/2402.19379): 12 LLMs on 31 binary questions were not statistically
  different from a 925-person crowd, but showed an acquiescence bias. Exposure to the human median improved them by
  17–28%, which was still worse than simply averaging human and machine forecasts. My recollection agrees.
- Halawi et al. 2024: Brier .179 against a crowd .149, accuracy 71.5% against 77.0%, beating the crowd only in a
  selective setting. The paper, "Approaching Human-Level Forecasting with Language Models", arXiv 2402.18563, NeurIPS
  2024, exists [VERIFIED: https://raw.githubusercontent.com/dannyallover/llm_forecasting/main/README.md]. The numbers
  are UNVERIFIED.
- ForecastBench (ICLR 2025, arXiv 2409.19839): superforecasters 0.093, best LLM 0.111, public 0.107. The paper and venue
  exist [VERIFIED: https://raw.githubusercontent.com/forecastingresearch/forecastbench/main/README.md]. The numbers are
  UNVERIFIED.

The design conclusion does not depend on the exact numbers: run several independent LLM forecasts, blend them with a
non-LLM baseline, and do not fix the weight in advance.

---

## 8. Compact recipes for other sports

**NBA.** E[FP] = E[minutes] × FP per minute. Projected minutes come from rotation, coach habits, injuries,
back-to-backs and a blowout cut. The RotoGrinders rule of about 1.5% per point of spread beyond 7 is [UNVERIFIED:
https://rotogrinders.com/fantasy/lessons/accurately-predicting-minutes-nba-dfs]; fit it. FP per minute is shrunk, then
adjusted for pace (possessions from both teams' pace and the game total) and for usage changes when teammates sit.
On/off usage jumps can be large (one example: 25.5% → 37.8%) but need shrinkage [UNVERIFIED:
https://rotogrinders.com/articles/nba-situational-player-projections-dfs-betting-props-analysis-3-21-22-3769704 ;
https://www.stokastic.com/articles/dfs-strategy/nba-dfs-usage-rate-strategy]. Props for points, rebounds, assists, PRA and
threes are the strongest inputs. The distribution is a mixture over minutes scenarios. Correlation: teammates slightly
negative (competing for usage), opponents slightly positive through pace [UNVERIFIED; judgment]. News at lock dominates.

**MLB.** Hitters: PA by lineup slot (FanGraphs: 4.65 per game leading off, 3.77 batting ninth, about 0.1 per slot;
RotoWire: 4.83 vs 3.90) [UNVERIFIED: https://fantasy.fangraphs.com/buying-generic-plate-appearances-by-lineup-spot ;
https://www.rotowire.com/baseball/article/umpire-analysis-constructing-an-efficient-lineup-21283]. Then team implied
runs, opposing starter and bullpen quality, platoon splits (shrunk), park and weather. Simulate PA by PA with log5-style
event probabilities, because outcomes are zero-inflated and heavily right-skewed. Pitchers: strikeout and outs-recorded
props, win probability from the moneyline, implied runs allowed. Correlation: consecutive lineup slots positive; a
pitcher negative with opposing hitters [UNVERIFIED; judgment]. Confirmed lineups drive late swaps. On 2026-10-21 only
postseason MLB slates exist.

**NHL.** Lines and power-play units drive time on ice, which drives shots, goals, assists and blocks. Team implied goals
come from the moneyline, puck line and total, plus shots-on-goal and goal props. Goalies need a confirmed starter, then
implied win% and expected saves = opponent shots × save% [UNVERIFIED: https://www.dailyfaceoff.com/news/dfo-dfs-projections-02-13-25].
Correlation: linemates and PP1 strongly positive; a goalie negative with opposing skaters. Stacking lines was central in
Hunter et al.'s NHL work (the code repo exists; results UNVERIFIED).

**PGA.** Use adjusted strokes gained (scores adjusted for course difficulty), with player means estimated by
regression and backtesting rather than raw averages. Add course fit (driving distance and accuracy, course history) and
simulate the tournament round by round [UNVERIFIED: https://datagolf.com/predictive-model-methodology ;
https://datagolf.com/course-fit-tool]. Make-cut probability is crucial. Anchor to outright, top-N and make-cut odds,
de-vigged with Shin or power because of the longshot bias.

**MMA.** Moneyline for win probability; method-of-victory and round props for P(KO), P(submission), P(decision) and the
round distribution. Expected fight minutes × per-minute rates give significant strikes and takedowns. Simulate fights as
discrete scenarios, because scores are bimodal. Never roster both fighters in a bout, since only one can win (judgment).
Simulators pick the winner and method per simulated fight, then score the result [UNVERIFIED:
https://rotogrinders.com/articles/mma-dfs-optimal-lineup-simulation-tool-draftkings-3915353].

**NASCAR.** DraftKings: place differential (start − finish, 1 point per place), +0.25 per lap led, +0.45 per fastest
lap, plus finish points [VERIFIED via `scoring.verified.md` (its V13 table): place differential 1.0, fastest lap 0.45,
lap led 0.25; original source https://www.stokastic.com/articles/dfs-strategy/nascar-dfs-scoring-explained UNVERIFIED].
Inputs: starting position, track-type history, practice and qualifying speed, and win/top-N odds. Simulate the finishing
order with Plackett–Luce from de-vigged win odds (judgment). Allocate laps led to front-runners. Include DNF/crash mass,
larger at superspeedways [UNVERIFIED].

**Soccer.** Team expected goals from 1X2 plus over/under prices: Poisson, or Dixon–Coles with a low-score correction. The
R `goalmodel` package fits Dixon–Coles via `dc = TRUE` (it "modifies the probabilities for low scores … determined by
the parameter ρ") and offers `weights_dc` "to let results further back in time have" less weight
[VERIFIED: https://raw.githubusercontent.com/opisthokonta/goalmodel/master/README.md]. Python `penaltyblog` 1.13.1 exists
[VERIFIED: https://pypi.org/pypi/penaltyblog/json]; its Dixon–Coles class is [UNVERIFIED]. P(clean sheet) = e^{−λ_opp}
under Poisson [VERIFIED: derived]. For a player, λ_goal = −ln(1 − p_anytime), or team xG × goal share. Expected minutes
depend on confirmed starters, about an hour before kickoff [UNVERIFIED]. DraftKings rewards volume stats (shots,
chances, crosses, tackles), so defensive and role volume matter. RotoWire's GK study found GK points rise with win
probability, with very large variance [UNVERIFIED:
https://www.rotowire.com/soccer/article/dfs-soccer-strategy-draftkings-goalkeeper-analysis-78656]. Correlation:
attacking teammates positive; GK and defenders positive with their own clean sheet; GK negative with opposing attackers.

---

## 9. Which inputs matter most, by sport

Rank 1 is the highest. "Props" means de-vigged player prop markets. This is design judgment, not a measured ranking.

| Sport | 1 | 2 | 3 | 4 | 5 | Late-news sensitivity |
|---|---|---|---|---|---|---|
| NFL | Usage shares (snaps, targets, carries, RZ/GL; routes if licensed) | Props | Implied team total plus script (spread) | Team volume (pace, PROE) | Shrunk efficiency and opponent | Medium-high (questionable = 66% to play; inactives ~90 min before kickoff) |
| CFB | Implied team total plus blowout/playing-time model | Usage shares (often committees) | Tempo | QB rushing role | Opponent strength, heavily shrunk | High (availability reports, often thin) |
| NBA | Minutes | Usage with injuries (on/off, shrunk) | Props | Pace and total | Blowout risk (spread) | Very high (lineups at lock) |
| MLB hitters | Lineup slot (PA) and confirmed lineup | Team implied runs | Opposing starter, handedness | Park and weather | Hitter skill (shrunk) | High |
| MLB pitchers | K prop and outs prop | Win probability | Opposing lineup K% | Park and weather | Pitch-count leash | High (scratches) |
| NHL | PP unit and line (TOI) | Team implied goals | Shots/goal props | Confirmed goalie | Opponent shot suppression | High (goalies) |
| PGA | Skill (adjusted strokes gained) | Make-cut probability and odds | Course fit | Recent form (shrunk) | Weather wave | Low-medium |
| MMA | Win probability | Method and round props | Expected fight time | Volume rates | Style matchup | Medium (late replacements) |
| NASCAR | Starting position | Dominator potential (speed, track type) | Win/top-N odds | DNF risk | Pit crew/strategy | Medium (qualifying) |
| Soccer | Starting XI and minutes | Team xG and clean-sheet odds | Anytime goal/assist odds | Set pieces and penalties | Volume-stat role | Very high (lineups ~1 h before kickoff) |

---

## 10. Data contract: per-player projection record

All fantasy-point fields use the **contest's** scoring table (`scoring_id`). Quantities are unconditional unless the
field name ends in `_if_active`. Two changes from the original: `snap_share`/`route_participation` are now optional with a
`usage_source`, and `availability.prior_source` has been added.

```json
{
  "schema_version": "proj.v1",
  "slate_id": "DK-NFL-2026-10-25-main",
  "site": "DK",
  "scoring_id": "dk_nfl_classic_2026",
  "player_id": "site:12345",
  "canonical_id": "gsis:00-0031234",
  "name": "string",
  "team": "BUF", "opp": "MIA", "game_id": "2026_08_MIA_BUF",
  "positions_eligible": ["WR", "FLEX"],
  "salary": 7200,
  "role": {
    "depth": 1, "alignment": "slot|outside|inline|backfield",
    "snap_share": 0.88, "route_participation": null,
    "usage_source": "pfr_snaps|licensed_routes|beat_report",
    "target_share": 0.27, "carry_share": null, "rz_share": 0.22, "gl_share": null,
    "role_confidence": 0.85, "role_source": "url|depth-chart@timestamp"
  },
  "availability": {
    "status": "active|questionable|doubtful|out|inactive|unknown",
    "practice_trail": ["DNP", "LP", "LP"],
    "p_active": 0.97, "p_early_exit": 0.03,
    "prior_source": "nflverse_2022_2025_designation_table", "source": "url", "as_of": "ISO8601"
  },
  "team_context": {
    "implied_team_pts": 26.5, "spread_home_betting_conv": -4.5, "spread_line_nflverse": 4.5, "total": 48.5,
    "win_prob": 0.66, "proj_plays": 64.1, "proj_dropback_rate": 0.60
  },
  "stat_projection_if_active": {
    "targets": {"mean": 8.6, "q10": 5, "q50": 8, "q90": 12},
    "receptions": {"mean": 6.0}, "rec_yards": {"mean": 78.0, "q50": 73.3}, "rec_td": {"lambda": 0.52},
    "carries": null, "rush_yards": null, "pass_att": null
  },
  "fp_if_active": {
    "mean": 18.9, "q10": 8.1, "q25": 12.4, "q50": 17.6, "q75": 24.3, "q90": 31.5, "q95": 35.8,
    "dist": {"family": "shifted_lognormal|metalog3|empirical_template", "params": {"c": -3.2, "mu": 3.0, "sigma": 0.45}}
  },
  "fp": {
    "mean": 18.1, "p_zero": 0.03, "floor_q10": 7.0, "ceiling_q90": 31.0, "ceiling_q95": 35.4,
    "p_ge": {"20": 0.42, "25": 0.24, "30": 0.12, "salary_x3": 0.47, "salary_x4": 0.21}
  },
  "components": {
    "baseline": {"mean": 17.4, "q10": 7.5, "q50": 16.2, "q90": 29.0, "method": "nfl_gamelog_market_v1"},
    "llm": {"mean": 19.6, "q10": 9, "q50": 18, "q90": 31, "model": "claude-...", "calibrated": true, "cqr_widen": 2.4},
    "props": {"rec_yards_line": 69.5, "p_over": 0.52, "receptions_line": 5.5, "atd_price": 135, "book": "string",
              "source_licence": "string", "as_of": "ISO8601"},
    "blend": {"weights": {"baseline": 0.5, "llm": 0.5, "props": "precision-weighted"}, "weights_version": "w.2026-10-20"}
  },
  "correlation_keys": ["NFL:BUF:WR1", "NFL:2026_08_MIA_BUF"],
  "notes": ["vacated: +1.8 targets from WR2 out (redistribute v1)"],
  "sources": [{"url": "…", "fact": "…", "as_of": "ISO8601", "kind": "injury|depth|odds|stat"}],
  "created_at": "ISO8601", "pipeline_run_id": "uuid"
}
```

(The example `rec_yards.q50` was changed from 71.0 to 73.3 to match the §E shape: median ≈ 0.94 × mean.)

**Validation rules (code-enforced):**

- The quantiles are monotone.
- `fp.mean` ≈ (1 − p_out)·[(1 − p_exit)·m + p_exit·m/2] within tolerance.
- Σ team `targets.mean` ≈ 0.957 × team pass attempts, within 3% [ratio VERIFIED: derived].
- Σ team `carries.mean` ≈ designed rushes.
- Σ team receiving-yard means = team passing-yard mean (an identity) [VERIFIED: derived]. The prop comparison uses the
  QB's prop-implied mean, with a tolerance learned from history, not a fixed 5%.
- `spread_home_betting_conv == −spread_line_nflverse` (sign test).
- Every `role` and `availability` claim has a source and a timestamp.
- The LLM component is flagged `calibrated: true` before it enters the blend.
- Mean/median sanity: flag a player if `fp_if_active.mean / q50 > 1.20` (§E shows ≈ 1.06–1.08 for yardage).

---

## 11. End-to-end pseudo-code (projection stage only)

```python
def project_slate(slate, scoring, odds, stats, news, llm_triples=None, weights=None):
    mkt   = market_layer(odds)                         # §2.1 de-vig, team pts (sign-tested), win prob (σ≈12.7), TD expectation
    vol   = volume_layer(slate.games, stats, mkt)      # §2.2 plays, dropbacks, rushes
    use   = usage_layer(slate.players, stats, vol)     # §2.3 recency-weighted EB-shrunk shares (snaps/targets; routes if licensed)
    use   = apply_availability(use, news, prior=DESIGNATION_TABLE)  # §2.8 statuses; §2.7 vacated redistribution
    eff   = efficiency_layer(slate.players, stats, mkt)   # §2.4 heavy shrink + opponent factors
    sims  = simulate_statlines(use, eff, n=5000)       # §2.5 counts→yards→TDs per player
    base  = to_fp_distributions(sims, scoring)         # quantiles, P(bonus), p_ge thresholds (DK and FD both have bonuses)
    base  = reconcile_with_props(base, odds.props, shape=YARDAGE_TEMPLATES)  # §2.6 template (not lognormal) + coherence
    if llm_triples:                                    # football only
        llm  = {p: recalibrate(fit_triple(t), cqr_table[p.pos]) for p, t in llm_triples.items()}  # §4
        base = blend(base, llm, weights)               # §7 quantile averaging, learned + shrunk weights
    final = add_zero_mass(base, news)                  # §4.4
    R     = build_corr(slate, corr_table)              # §5 role-pair lookup (derived priors) → PD repair
    return final, R                                    # handed to drafters/solver/simulator
```

Compute cost is trivial. 5,000–20,000 simulations × about 300 players × a few stats takes seconds in NumPy. Each
nflverse check in §E took under 90 seconds on this sandbox [VERIFIED: timing, this session]. The 60-minute budget goes on
research and LLM calls, not this stage. CFBD calls are capped at 1,000 a month on the free tier (§3), so cache.

---

## 12. Implications for the user's draft pipeline (projection stage)

1. **Baseline (NFL "game logs adjusted by betting lines").** Implement it as §2's share × volume × efficiency structure
   with market-derived volume, not as averages of fantasy points. Points-based logs mix together role changes, game
   script and TD luck. Use snap share and target share, since free 2026 route data does not exist.
2. **Fresh forecast with no prices, site averages or past scores.** Keep the blinding to DFS **salaries** and site
   projections; prompt-number anchoring is reported in the literature [UNVERIFIED: https://arxiv.org/abs/2505.15392 ;
   https://aclanthology.org/2026.acl-short.16/]. Note that the original's prose names "Huang et al." and "Owusu & Feldman"
   as authors, but those names were not confirmed. Read "no prices" as "no DFS salaries" and **give the forecaster the
   betting market** (implied team totals, spread, props) plus snap/target shares and depth/injury facts, as dated facts.
   If the user truly means no betting prices either, that is the user's call, but expect worse accuracy. Hiding past
   fantasy scores is fine if usage shares are provided.
3. **Ask for stat-line triples**, or at least fantasy-point triples conditional on playing. Fit them in code, recalibrate
   with conformal widening, and add availability mass in code using the §2.8 table.
4. **Blend weights.** Replace the fixed 85/15 (NFL) and 100/0 (CFB) with learned, shrunk, per-position weights. Use 50/50
   quantile averaging plus prop precision-weighting as the cold-start prior. With 93 clean games, the data cannot yet
   distinguish 85/15 from 50/50 (§7.3).
5. **Last-hour re-forecast.** Re-run only the affected team's §2.7 redistribution plus the market re-read. Code can do
   this deterministically, with an LLM reading only the news text. The rule "a new lineup leads by over 1%" should compare
   **simulated** objective values (expected score, or the tournament payoff proxy) under common random numbers, so
   simulation noise is not mistaken for a 1% lead.
6. **Grading.** Use the counterfactual layer-swap attribution in §6 to find the failed step, and update weights only
   through the §7.3 protocol.

---

## 13. Open questions (updated)

- **Answered here:** the post-2016 "questionable" play rate (§2.8). Still open: a logistic refinement on the
  practice-day trajectory and beat-report language.
- Licence and terms for nflverse play-by-play, snap counts (PFR-sourced) and odds fields. Confirmed: nflreadr code is
  "MIT + file LICENSE" [VERIFIED: https://raw.githubusercontent.com/nflverse/nflreadr/main/DESCRIPTION]. FTN
  participation **and** FTN charting data are CC-BY-SA 4.0 [VERIFIED: https://raw.githubusercontent.com/nflverse/nflreadr/main/NEWS.md ;
  https://raw.githubusercontent.com/nflverse/nflreadr/main/R/load_ftn_charting.R]. The rest of nflverse data is
  "broadly licensed as CC-BY 4.0" [VERIFIED: https://raw.githubusercontent.com/nflverse/nflreadpy/main/README.md].
  `games.csv` (lines) has no stated licence (`sources_compliance.verified.md`).
- Whether a legal, automation-permitting source exists for player props. Sportsbook terms usually forbid scraping
  [UNVERIFIED]. Without one, the "strongest single input" is unavailable.
- Residual dispersion per stat and role (NegBin size r; empirical shape templates). §E gives the yardage templates'
  medians and CVs; build full templates by mean bucket.
- Does a t copula beat a Gaussian copula, or a game-factor model beat both, on historical normal scores for same-team
  stacks?
- How do LLM P10/P50/P90 triples perform on post-cutoff 2026 games, before and after conformal widening?
- CFB blowout and playing-time parameters per **coach**. Which conferences' availability reports are machine-readable?
- Why the published correlation tables (FantasyLabs, Spike Week) disagree so much with nflverse 2021–25. Re-check if
  those pages become reachable.

---

## Sources

### Verified today (primary, fetched)

- nflverse data and docs: https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv ;
  https://raw.githubusercontent.com/nflverse/nfldata/master/DATASETS.md ;
  https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_2021.csv (… 2022–2025) ;
  https://github.com/nflverse/nflverse-data/releases/download/stats_team/stats_team_week_2023.csv (… 2024, 2025) ;
  https://github.com/nflverse/nflverse-data/releases/download/injuries/injuries_2022.csv (… 2023–2025) ;
  https://github.com/nflverse/nflverse-data/releases/download/snap_counts/snap_counts_2022.csv (… 2023–2025) ;
  https://github.com/nflverse/nflverse-data/releases/download/players/players.csv
- nflfastR: https://raw.githubusercontent.com/nflverse/nflfastR/master/R/helper_add_xpass.R ;
  https://raw.githubusercontent.com/nflverse/nflfastR/master/R/calculate_stats.R
- nflreadr / nflreadpy: https://raw.githubusercontent.com/nflverse/nflreadr/main/DESCRIPTION ;
  https://raw.githubusercontent.com/nflverse/nflreadr/main/NEWS.md ;
  https://raw.githubusercontent.com/nflverse/nflreadr/main/R/load_snap_counts.R ;
  https://raw.githubusercontent.com/nflverse/nflreadr/main/R/load_ftn_charting.R ;
  https://raw.githubusercontent.com/nflverse/nflreadpy/main/README.md
- ffopportunity: https://raw.githubusercontent.com/ffverse/ffopportunity/main/DESCRIPTION ;
  https://raw.githubusercontent.com/ffverse/ffopportunity/main/README.md
- Odds: https://raw.githubusercontent.com/opisthokonta/implied/master/vignettes/introduction.Rmd ;
  https://raw.githubusercontent.com/opisthokonta/implied/master/DESCRIPTION ;
  https://raw.githubusercontent.com/mberk/shin/master/README.md ; https://pypi.org/pypi/shin/json
- Soccer: https://raw.githubusercontent.com/opisthokonta/goalmodel/master/README.md ; https://pypi.org/pypi/penaltyblog/json
- Statistics: https://raw.githubusercontent.com/stan-dev/loo/master/R/crps.R ;
  https://raw.githubusercontent.com/yromano/cqr/master/README.md ; statsmodels 0.15.0 sdist via
  https://pypi.org/pypi/statsmodels/json (`statsmodels/tsa/stattools/_stattools.py`)
- Existence of papers: https://raw.githubusercontent.com/dscotthunter/Fantasy-Hockey-IP-Code/master/README.md
  (arXiv 1604.01455) ; https://raw.githubusercontent.com/dannyallover/llm_forecasting/main/README.md (arXiv 2402.18563) ;
  https://raw.githubusercontent.com/forecastingresearch/forecastbench/main/README.md (arXiv 2409.19839, ICLR 2025)
- CFBD: https://raw.githubusercontent.com/CFBD/cfbd-net/main/docs/authentication.md
- Sibling verified files (same workflow):
  `scratch/research/scoring.verified.md`,
  `scratch/research/sources_compliance.verified.md`
- Scripts: `scratch/scripts/`
  (`mathcheck.py`, `qrate.py`, `nflemp.py`, `yoy.py`, `checks_misc.py`)

### Cited by the original, not reachable today (UNVERIFIED)

FFA / r-bloggers: https://fantasyfootballanalytics.net/which-dfs-projections-are-most-accurate ;
https://fantasyfootballanalytics.net/2026/08/we-analyzed-12-seasons-of-fantasy-football-projections-heres-what-we-found.html ;
https://fantasyfootballanalytics.net/2026/09/we-analyzed-11-seasons-of-dfs-projections-heres-what-we-found.html ;
https://www.r-bloggers.com/who-has-the-best-fantasy-football-projections-2015-update/ ;
https://www.r-bloggers.com/2016/04/which-are-more-accurate-fantasy-football-rankings-or-projections/ ;
https://www.r-bloggers.com/2017/03/who-has-the-best-fantasy-football-projections-2017-update/ ·
FantasyPros: https://fantasypros.com/about/faq/football-inseason-accuracy-methodology ·
4for4: https://www.4for4.com/2016/preseason/leveraging-weekly-player-consistency-daily-fantasy-football ;
https://www.4for4.com/2025/preseason/how-play-calling-tendencies-affect-fantasy-football ;
https://www.4for4.com/2023/preseason/most-predictable-tight-end-stats ; https://www.4for4.com/2017/w9/raybons-review-dfs-lessons-learned-week-8 ·
NBA: https://web.stanford.edu/class/stats50/projects16/BarryCanovaCapiz-slide.pdf ; https://devpost.com/software/quantsim-fthcz5 ;
https://link.springer.com/article/10.1007/s41060-024-00523-y ;
https://rotogrinders.com/fantasy/lessons/accurately-predicting-minutes-nba-dfs ;
https://rotogrinders.com/articles/nba-situational-player-projections-dfs-betting-props-analysis-3-21-22-3769704 ;
https://www.stokastic.com/articles/dfs-strategy/nba-dfs-usage-rate-strategy ·
Spread/odds: https://www.pro-football-reference.com/about/win_prob.htm ;
https://fivethirtyeight.com/features/how-unexpected-was-clevelands-shellacking-of-cincinnati ;
https://www.si.com/nfl/2017/09/01/fantasy-football-2017-using-vegas-lines-daily-fantasy-football ;
https://cran.r-project.org/web/packages/implied/index.html ; https://rdrr.io/cran/implied/f/vignettes/introduction.Rmd ;
https://arxiv.org/pdf/1802.08848 ; https://link.springer.com/article/10.1007/s10479-022-04722-3 ·
Props: https://www.stokastic.com/articles/dfs-strategy/player-props-for-dfs-players ;
https://establishtherun.com/nfl-player-props-we-bet-week-8/ ;
https://www.pff.com/news/nfl-betting-2022-midseason-player-prop-performance-review ;
https://www.r-bloggers.com/2020/09/expected-goals-from-over-under-odds/amp/ ·
Volume/usage: https://www.pro-football-reference.com/years/NFL/ ;
https://www.pff.com/news/fantasy-football-nfl-pace-volume-how-no-huddle-trends-shape-2026-value ;
https://establishtherun.com/pass-rate-over-expectation/ ; https://r-packages.io/packages/nflfastR/add_xpass ;
https://fivethirtyeight.com/features/the-bucs-defense-dares-opposing-offenses-to-pass-so-they-do/ ;
https://sumersports.com/the-zone/sticky-football-stats-predictive-nfl-metrics/ ;
https://www.sharpfootballanalysis.com/fantasy/wide-receiver-stats-that-matter-fantasy-football-2023/amp/ ;
https://ftnfantasy.com/?p=211 ; https://footballguys.com/article/tuccittotfp0wr ; https://www.footballguys.com/article/tuccittotfp0rb ;
https://www.cbssports.com/fantasy/football/news/2019-fantasy-football-draft-prep-how-should-i-use-air-yards-in-my-research-process/ ;
https://workloadreport.substack.com/p/the-workload-report-introduction ; https://github.com/ffverse/ffopportunity ;
https://metricgate.com/calculator/hierarchical-beta-binomial-baseball ; https://arxiv.org/pdf/2111.02829 ;
https://forums.footballguys.com/threads/wr-a-j-brown-phi.788301/page-17 ;
https://www.thefantasyfootballers.com/articles/vacated-targets-the-rb-position-in-fantasy-football/ ·
Scoring: https://www.stokastic.com/articles/nfl-dfs/draftkings-vs-fanduel-nfl-dfs ;
https://www.rotowire.com/football/article/dfs-football-101-how-to-play-on-draftkings-46369 ; https://fanduel.com/rules ·
Availability: https://www.foxnews.com/sports/nfl-makes-change-to-team-injury-reports-that-could-have-major-impact ;
https://www.cbssports.com/nfl/news/nfl-makes-rare-in-season-rule-change-following-awkward-covid-19-testing-situation-with-dez-bryant ;
https://www.atlantafalcons.com/news/starters-and-inactives-falcons-at-49ers-16242019 ; https://pubmed.ncbi.nlm.nih.gov/32412782/ ·
CFB: https://www.espn.com/college-football/story/_/id/49577220/college-football-2026-trends-see-ohio-state-miami ;
https://www.pff.com/news/college-football-the-5-fastest-operating-offenses-in-the-country ;
https://roundtable.io/sports/ncaa/ohio-state/opinion/ohio-state-s-offense-ranks-nearly-last-in-this-important-offensive-statistic ;
https://abc7chicago.com/post/2026-college-football-sp-rankings-138-fbs-teams/19826490/ ;
https://www.stokastic.com/articles/dfs-strategy/college-football-dfs-strategy ;
https://www.rotowire.com/cfootball/article/cfb-fanduel-dfs-week-0-breakdown-130567 ;
https://www.cbssports.com/college-football/news/big-12-to-mandate-player-availability-reports-for-football-basketball-starting-in-2025-26 ;
https://www.si.com/college/boise-state/football/mountain-west-follows-lead-power-four-adds-player-availability-reports-2025-football-season ;
https://jentic.com/apis/collegefootballdata ·
Distributions/LLM: https://ideas.repec.org/a/inm/ordeca/v13y2016i4p243-277.html ; https://www.informs-sim.org/wsc19papers/340.pdf ;
https://archives.datapages.com/data/bulletns/2000/12dec/1883/1883.htm ; https://arxiv.org/abs/2510.26995 ;
https://arxiv.org/abs/2604.15859 ; https://arxiv.org/abs/2604.01896 ; https://arxiv.org/abs/1905.03222 ;
https://arxiv.org/abs/2505.15392 ; https://aclanthology.org/2026.acl-short.16/ ; https://arxiv.org/abs/2402.18563 ;
https://proceedings.iclr.cc/paper_files/paper/2025/hash/ea74e45a229dac70b5b63b28d8934db6-Abstract-Conference.html ;
https://arxiv.org/abs/2402.19379 ; https://arxiv.org/abs/2309.17322 ·
Correlation/simulation: https://www.fantasylabs.com/articles/introducing-nfl-correlation-values/ ;
https://spikeweek.com/visualizing-single-game-correlation/ ; https://github.com/SrWildman/dfs/pull/3 ;
https://underdognetwork.com/football/best-ball-research/correlation-at-ceiling-outcomes-between-teammates-and-their-opponents ;
https://www.fantasylabs.com/?p=2683 ; https://www.stokastic.com/articles/nfl-dfs/nfl-dfs-defense-strategy ;
https://arxiv.org/abs/1206.6488 ; https://arxiv.org/pdf/1810.07126 ; https://rdrr.io/cran/copBasic/man/NORMcop.html ;
https://eprints.maths.manchester.ac.uk/232/ ; https://metricgate.com/docs/t-copula-model ; https://arxiv.org/abs/1604.01455 ;
https://ideas.repec.org/a/inm/ormnsc/v67y2021i1p72-92.html ; https://papers.ssrn.com/abstract=3393127 ·
Evaluation/combination: https://mc-stan.org/loo/reference/crps.html ;
https://search.r-project.org/CRAN/refmans/ensembleBMA/html/crps.html ;
https://ideas.repec.org/a/bla/jorssb/v69y2007i2p243-268.html ;
https://www.statsmodels.org/dev/generated/statsmodels.tsa.stattools.diebold_mariano_test.html (the function exists in
0.15.0, VERIFIED from source; the docs page itself was not fetched) ; https://arxiv.org/pdf/2409.12662 ;
https://econpapers.repec.org/RePEc:bla:obuest:v:71:y:2009:i:3:p:331-355 ; https://arxiv.org/abs/2205.04216 ;
https://econweb.ucsd.edu/~gelliott/AveragingOptimal.pdf ; https://arxiv.org/abs/1704.02030 ;
https://ideas.repec.org/a/inm/ormnsc/v59y2013i7p1594-1611.html ·
Other sports: https://fantasy.fangraphs.com/buying-generic-plate-appearances-by-lineup-spot ;
https://www.rotowire.com/baseball/article/umpire-analysis-constructing-an-efficient-lineup-21283 ;
https://datagolf.com/predictive-model-methodology ; https://datagolf.com/course-fit-tool ;
https://www.stokastic.com/articles/dfs-strategy/nascar-dfs-scoring-explained ;
https://www.dailyfaceoff.com/news/dfo-dfs-projections-02-13-25 ;
https://rotogrinders.com/articles/mma-dfs-optimal-lineup-simulation-tool-draftkings-3915353 ;
https://github.com/opisthokonta/goalmodel ; https://eprints.lancs.ac.uk/id/eprint/19492 ;
https://www.rotowire.com/soccer/article/dfs-soccer-strategy-draftkings-goalkeeper-analysis-78656 ·
nflverse docs: https://nflverse.r-universe.dev/nflreadr/NEWS.txt ; https://www.rdocumentation.org/packages/nflreadr/versions/1.4.1
