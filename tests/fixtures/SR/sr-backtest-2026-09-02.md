# Weekly S/R Backtest Results — 2023–2026 (3y), 5 regions, 10 tickers

**Method:** regions `[L−w, L+w]`, w = 0.5×weekly ATR(14). No-lookahead (levels from
week-T data tested on week T+1). ~151 evaluated weeks/ticker. Tickers: US (AAPL,MSFT),
HK (0700,9988), CN (600519,300750), Asia/Tokyo-Seoul (7203,005930), EU (SAP,MC).
Full method: `doc/sr-region-algorithm.md`.

## Per-region pooled: respect_rate (touch-weighted, "holds when touched")

| family            |   CN |   EU |   HK |  JPKR |   US |
|-------------------|------|------|------|-------|------|
| **wk_pivot_S1**   | 0.926| 0.939| 0.928| 0.899 | 0.930|
| **wk_pivot_R1**   | 0.927| 0.880| 0.884| 0.889 | 0.889|
| **pw_low_prev**   | 0.785| 0.795| 0.783| 0.772 | 0.770|
| **pw_high_prev**  | 0.780| 0.697| 0.770| 0.720 | 0.708|
| mo_pivot_S1       | 0.944| 0.981| 0.956| 0.952 | 0.948|
| mo_pivot_R1       | 0.948| 0.904| 0.918| 0.904 | 0.940|
| ma50_S            | 0.551| 0.578| 0.571| 0.645 | 0.596|
| ma50_R            | 0.650| 0.436| 0.612| 0.572 | 0.471|
| poc_S (12m POC)   | 0.462| 0.519| 0.362| 0.427 | 0.398|
| poc_R (12m POC)   | 0.564| 0.239| 0.308| 0.257 | 0.208|

## Per-region touch_rate ("how often the zone is even reached")

| family          |   CN |   EU |   HK | JPKR |   US |
|-----------------|------|------|------|------|------|
| wk_pivot_S1     | 0.894| 0.871| 0.871| 0.821| 0.848|
| wk_pivot_R1     | 0.868| 0.881| 0.887| 0.868| 0.868|
| pw_low_prev     | 0.755| 0.679| 0.719| 0.682| 0.632|
| pw_high_prev    | 0.692| 0.775| 0.719| 0.722| 0.781|
| mo_pivot_S1     | 0.354| 0.348| 0.377| 0.344| 0.318|
| mo_pivot_R1     | 0.384| 0.381| 0.364| 0.381| 0.384|
| ma50_S          | 0.848| 0.682| 0.795| 0.765| 0.689|
| ma50_R          | 0.795| 0.851| 0.844| 0.851| 0.844|
| poc_S           | 0.795| 0.434| 0.623| 0.566| 0.424|
| poc_R           | 0.669| 0.831| 0.666| 0.722| 0.811|

Root tables + full per-ticker output: `scout/cache/sr_backtest_results.csv`
Figures: `scout/cache/figs/sr_scatter_region.png`, `sr_region_bar.png`,
`sr_chart_AAPL.png`, `sr_chart_SAP.DE.png`.
