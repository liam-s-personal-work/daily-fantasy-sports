## forecast_experiment_2026_wk3-6_blind, re-scored against the current (tuned) NFL baseline

Forecasts are unchanged from the original run; only the baseline columns were recomputed.

### Variant `blind`: 1504 player-games with both forecasts, weeks [np.int64(3), np.int64(4), np.int64(5), np.int64(6)]


**all pool players (unconditional means)** (n = 1504). Optimal forecast weight w* (encompassing regression y - b = w (f - b)): **0.31**

| Position | n | MAE baseline | MAE forecast | MAE 15% fc | MAE 50% | MAE 85% (user prior) | w* |
|---|---:|---:|---:|---:|---:|---:|---:|
| ALL | 1504 | 4.27 | 4.22 | 4.25 | 4.21 | 4.21 | 0.31 |
| DST | 91 | 3.61 | 3.77 | 3.61 | 3.62 | 3.71 | 0.11 |
| QB | 228 | 3.58 | 3.73 | 3.58 | 3.60 | 3.69 | 0.06 |
| RB | 355 | 4.37 | 4.15 | 4.30 | 4.19 | 4.15 | 0.46 |
| TE | 234 | 4.43 | 4.61 | 4.44 | 4.50 | 4.58 | 0.00 |
| WR | 596 | 4.52 | 4.37 | 4.48 | 4.42 | 4.38 | 0.53 |

**players who played (means if active)** (n = 1303). Optimal forecast weight w* (encompassing regression y - b = w (f - b)): **0.55**

| Position | n | MAE baseline | MAE forecast | MAE 15% fc | MAE 50% | MAE 85% (user prior) | w* |
|---|---:|---:|---:|---:|---:|---:|---:|
| ALL | 1303 | 4.81 | 4.74 | 4.77 | 4.72 | 4.73 | 0.55 |
| DST | 91 | 3.61 | 3.77 | 3.61 | 3.62 | 3.71 | 0.11 |
| QB | 135 | 6.74 | 5.86 | 6.53 | 6.13 | 5.90 | 0.70 |
| RB | 307 | 4.73 | 4.69 | 4.69 | 4.65 | 4.67 | 0.45 |
| TE | 230 | 4.49 | 4.67 | 4.50 | 4.55 | 4.64 | 0.02 |
| WR | 540 | 4.71 | 4.69 | 4.69 | 4.66 | 4.68 | 0.47 |

80% interval coverage on players who played (target 80%): baseline 82.1%, forecaster 77.6%.
Mean within-week, within-position Spearman rank correlation (played): baseline 0.491, forecaster 0.498.
MAE(forecast) - MAE(baseline), all pool players: -0.050 (95% team-week cluster bootstrap CI -0.141 to +0.038).
