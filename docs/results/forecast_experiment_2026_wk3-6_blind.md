## Forecaster experiment: 2026 weeks 3-6, variants blind

Model calls: {'calls': 61, 'cached': 0, 'failed': 0, 'cost_usd': 7.116, 'web_searches': 0}. Forecast problems: 31.

### Variant `blind`: 1555 player-games with both forecasts, weeks [np.int64(3), np.int64(4), np.int64(5), np.int64(6)]


**all pool players (unconditional means)** (n = 1555). Optimal forecast weight w* (encompassing regression y - b = w (f - b)): **0.59**

| Position | n | MAE baseline | MAE forecast | MAE 15% fc | MAE 50% | MAE 85% (user prior) | w* |
|---|---:|---:|---:|---:|---:|---:|---:|
| ALL | 1555 | 4.27 | 4.11 | 4.23 | 4.16 | 4.12 | 0.59 |
| DST | 91 | 3.61 | 3.77 | 3.61 | 3.62 | 3.71 | 0.10 |
| QB | 244 | 3.74 | 3.49 | 3.69 | 3.58 | 3.52 | 0.83 |
| RB | 366 | 4.38 | 4.06 | 4.31 | 4.17 | 4.08 | 0.50 |
| TE | 244 | 4.38 | 4.52 | 4.40 | 4.44 | 4.49 | 0.00 |
| WR | 610 | 4.47 | 4.29 | 4.43 | 4.36 | 4.30 | 0.73 |

**players who played (means if active)** (n = 1325). Optimal forecast weight w* (encompassing regression y - b = w (f - b)): **0.60**

| Position | n | MAE baseline | MAE forecast | MAE 15% fc | MAE 50% | MAE 85% (user prior) | w* |
|---|---:|---:|---:|---:|---:|---:|---:|
| ALL | 1325 | 4.80 | 4.70 | 4.76 | 4.70 | 4.69 | 0.60 |
| DST | 91 | 3.61 | 3.77 | 3.61 | 3.62 | 3.71 | 0.10 |
| QB | 135 | 6.70 | 5.86 | 6.50 | 6.11 | 5.89 | 0.69 |
| RB | 315 | 4.78 | 4.60 | 4.73 | 4.65 | 4.61 | 0.49 |
| TE | 240 | 4.42 | 4.58 | 4.44 | 4.49 | 4.55 | 0.09 |
| WR | 544 | 4.69 | 4.67 | 4.67 | 4.65 | 4.66 | 0.64 |

80% interval coverage on players who played (target 80%): baseline 83.0%, forecaster 77.8%.
Mean within-week, within-position Spearman rank correlation (played): baseline 0.501, forecaster 0.506.
MAE(forecast) - MAE(baseline), all pool players: -0.154 (95% team-week cluster bootstrap CI -0.234 to -0.071).
