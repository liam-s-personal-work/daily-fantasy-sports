## forecast_experiment_2026_wk3-6_blind, re-scored against the current (tuned) NFL baseline

Forecasts are unchanged from the original run; only the baseline columns were recomputed.

### Variant `blind`: 1517 player-games with both forecasts, weeks [np.int64(3), np.int64(4), np.int64(5), np.int64(6)]


**all pool players (unconditional means)** (n = 1517). Optimal forecast weight w* (encompassing regression y - b = w (f - b)): **0.51**

| Position | n | MAE baseline | MAE forecast | MAE 15% fc | MAE 50% | MAE 85% (user prior) | w* |
|---|---:|---:|---:|---:|---:|---:|---:|
| ALL | 1517 | 4.30 | 4.19 | 4.27 | 4.21 | 4.19 | 0.51 |
| DST | 91 | 3.60 | 3.77 | 3.60 | 3.61 | 3.71 | 0.08 |
| QB | 244 | 3.77 | 3.49 | 3.71 | 3.60 | 3.52 | 0.85 |
| RB | 353 | 4.38 | 4.17 | 4.32 | 4.21 | 4.17 | 0.47 |
| TE | 234 | 4.44 | 4.61 | 4.45 | 4.50 | 4.58 | 0.00 |
| WR | 595 | 4.53 | 4.38 | 4.50 | 4.44 | 4.39 | 0.54 |

**players who played (means if active)** (n = 1300). Optimal forecast weight w* (encompassing regression y - b = w (f - b)): **0.55**

| Position | n | MAE baseline | MAE forecast | MAE 15% fc | MAE 50% | MAE 85% (user prior) | w* |
|---|---:|---:|---:|---:|---:|---:|---:|
| ALL | 1300 | 4.83 | 4.75 | 4.78 | 4.73 | 4.74 | 0.55 |
| DST | 91 | 3.60 | 3.77 | 3.60 | 3.61 | 3.71 | 0.08 |
| QB | 135 | 6.76 | 5.86 | 6.54 | 6.14 | 5.90 | 0.70 |
| RB | 305 | 4.76 | 4.72 | 4.72 | 4.68 | 4.69 | 0.46 |
| TE | 230 | 4.51 | 4.67 | 4.51 | 4.56 | 4.64 | 0.07 |
| WR | 539 | 4.72 | 4.70 | 4.70 | 4.67 | 4.68 | 0.44 |

80% interval coverage on players who played (target 80%): baseline 81.8%, forecaster 77.5%.
Mean within-week, within-position Spearman rank correlation (played): baseline 0.496, forecaster 0.497.
MAE(forecast) - MAE(baseline), all pool players: -0.118 (95% team-week cluster bootstrap CI -0.204 to -0.030).
