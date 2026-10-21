## Forecaster experiment: 2026 weeks 3-6, variants blind

Model calls: {'calls': 61, 'cached': 0, 'failed': 0, 'cost_usd': 7.1666, 'web_searches': 0}. Forecast problems: 29.

### Variant `blind`: 1557 player-games with both forecasts, weeks [np.int64(3), np.int64(4), np.int64(5), np.int64(6)]


**all pool players (unconditional means)** (n = 1557). Optimal forecast weight w* (encompassing regression y - b = w (f - b)): **0.20**

| Position | n | MAE baseline | MAE forecast | MAE 15% fc | MAE 50% | MAE 85% (user prior) | w* |
|---|---:|---:|---:|---:|---:|---:|---:|
| ALL | 1557 | 4.20 | 4.21 | 4.18 | 4.17 | 4.19 | 0.20 |
| DST | 93 | 3.71 | 3.59 | 3.67 | 3.61 | 3.58 | 0.43 |
| QB | 244 | 3.36 | 3.51 | 3.37 | 3.41 | 3.47 | 0.08 |
| RB | 366 | 4.34 | 4.20 | 4.29 | 4.21 | 4.19 | 0.33 |
| TE | 244 | 4.34 | 4.58 | 4.37 | 4.44 | 4.54 | 0.00 |
| WR | 610 | 4.46 | 4.43 | 4.45 | 4.43 | 4.42 | 0.37 |

**players who played (means if active)** (n = 1337). Optimal forecast weight w* (encompassing regression y - b = w (f - b)): **0.58**

| Position | n | MAE baseline | MAE forecast | MAE 15% fc | MAE 50% | MAE 85% (user prior) | w* |
|---|---:|---:|---:|---:|---:|---:|---:|
| ALL | 1337 | 4.79 | 4.72 | 4.76 | 4.71 | 4.70 | 0.58 |
| DST | 93 | 3.71 | 3.59 | 3.67 | 3.61 | 3.58 | 0.43 |
| QB | 138 | 6.94 | 5.75 | 6.70 | 6.19 | 5.83 | 0.78 |
| RB | 314 | 4.70 | 4.72 | 4.67 | 4.66 | 4.70 | 0.35 |
| TE | 240 | 4.40 | 4.63 | 4.42 | 4.49 | 4.58 | 0.00 |
| WR | 552 | 4.67 | 4.69 | 4.65 | 4.64 | 4.66 | 0.40 |

80% interval coverage on players who played (target 80%): baseline 81.8%, forecaster 77.2%.
Mean within-week, within-position Spearman rank correlation (played): baseline 0.490, forecaster 0.523.
MAE(forecast) - MAE(baseline), all pool players: +0.007 (95% team-week cluster bootstrap CI -0.072 to +0.082).
