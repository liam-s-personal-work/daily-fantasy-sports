## Forecaster experiment: 2026 weeks 5-6, variants market

Model calls: {'calls': 29, 'cached': 0, 'failed': 0, 'cost_usd': 3.5365, 'web_searches': 0}. Forecast problems: 6.

### Variant `market`: 748 player-games with both forecasts, weeks [np.int64(5), np.int64(6)]


**all pool players (unconditional means)** (n = 748). Optimal forecast weight w* (encompassing regression y - b = w (f - b)): **0.59**

| Position | n | MAE baseline | MAE forecast | MAE 15% fc | MAE 50% | MAE 85% (user prior) | w* |
|---|---:|---:|---:|---:|---:|---:|---:|
| ALL | 748 | 4.24 | 4.14 | 4.21 | 4.17 | 4.15 | 0.59 |
| DST | 52 | 4.15 | 3.94 | 4.12 | 4.04 | 3.97 | 0.80 |
| QB | 116 | 3.77 | 3.37 | 3.71 | 3.55 | 3.42 | 1.00 |
| RB | 174 | 4.23 | 4.20 | 4.22 | 4.19 | 4.19 | 0.13 |
| TE | 116 | 4.41 | 4.75 | 4.46 | 4.57 | 4.69 | 0.00 |
| WR | 290 | 4.37 | 4.21 | 4.33 | 4.28 | 4.23 | 0.93 |

**players who played (means if active)** (n = 637). Optimal forecast weight w* (encompassing regression y - b = w (f - b)): **0.65**

| Position | n | MAE baseline | MAE forecast | MAE 15% fc | MAE 50% | MAE 85% (user prior) | w* |
|---|---:|---:|---:|---:|---:|---:|---:|
| ALL | 637 | 4.80 | 4.72 | 4.77 | 4.74 | 4.72 | 0.65 |
| DST | 52 | 4.15 | 3.94 | 4.12 | 4.04 | 3.97 | 0.80 |
| QB | 64 | 7.10 | 5.77 | 6.89 | 6.40 | 5.95 | 0.85 |
| RB | 148 | 4.66 | 4.78 | 4.67 | 4.69 | 4.74 | 0.14 |
| TE | 115 | 4.42 | 4.79 | 4.47 | 4.59 | 4.73 | 0.00 |
| WR | 258 | 4.60 | 4.56 | 4.58 | 4.55 | 4.55 | 0.75 |

80% interval coverage on players who played (target 80%): baseline 84.3%, forecaster 79.7%.
Mean within-week, within-position Spearman rank correlation (played): baseline 0.481, forecaster 0.535.
MAE(forecast) - MAE(baseline), all pool players: -0.095 (95% team-week cluster bootstrap CI -0.207 to +0.015).
