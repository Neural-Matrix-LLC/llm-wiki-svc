# S/R Region Algorithm — Exact Method + Multi-Region Backtest Protocol

**Companion to:** `doc/weekly-support-resistance-plan.md`
**Focus of this doc:** the *precise, falsifiable algorithm* for turning "support and
resistance" into **ranges/regions**, and how to backtest whether those regions work —
region by region (US, HK/China, Japan/Korea/Asia, Europe).

---

## 1. The central idea: S/R is a zone, and its width scales with volatility

A "level" in isolation is untestable (price almost never lands exactly on a number).
We convert every level into a **region** of width proportional to the stock's own
volatility:

```
region(S) = [ S − w ,  S + w ]      where  w = k × ATR_weekly ,  k ∈ {0.5, 1.0}
```

Using **ATR (weekly) as the width ruler** is what makes the method portable across
US/HK/A-shares/Asia/Europe: a $2 zone on an $880 mega-cap and a $0.02 zone on a HK$8
mid-cap carry the *same statistical meaning*. Never a fixed point-spread.

## 2. Level-candidate families (all computed at week T from data ≤ T — strict no-lookahead)

| # | Family | Support = | Resistance = | Data window |
|---|---|---|---|---|
| 1 | **Prior-period extremes** | prior week's low | prior week's high | last week |
| 2 | **Prior-month extremes** | trailing 21d low | trailing 21d high | 21d |
| 3 | **Weekly pivot S1/R1** | 2·P − H | 2·P − L, P=(H+L+C)/3 | last week |
| 4 | **Monthly pivot S1/R1** | 2·P − H | 2·P − L | 21d |
| 5 | **Volume POC** | trailing 12m volume node of control | same | 12m |
| 6 | **50-day moving average** | 50d SMA | 50d SMA | 50d |

Each family is evaluated as both support and resistance. The **volume POC** (Point of
Control — the price level where the trailing year traded the most volume) and the
**50-day MA** are dense pressure zones; the pivots and prior-period extremes are
precise, well-known levels.

## 3. The backtest "does a region hold?" definition (falsifiable)

For each level family, for every week T+1 (levels built from week-T data):

```
bracket      = [ L − w , L + w ]
touched      = price reached the bracket from the correct side
               · support: week.low  ≤ L + w        (price dipped down into zone)
               · resist.: week.high ≥ L − w        (price pushed up into zone)
broke        = weekly close exited the far side
               · support: week.close < L − w       (closed BELOW the zone)
               · resist.: week.close > L + w       (closed ABOVE the zone)
held         = touched AND not broke

respect_rate = held / touched        (only weeks where price actually reached it)
touch_rate   = touched / total_weeks  (how often the level is even relevant)
```

**Reading:** a *good* S/R region shows **high touch_rate** (it's a real magnet price
actually reaches) **AND high respect_rate** (when touched it holds / bounces).
A region with high respect but ~0 touches is useless (never relevant); high touches
but low respect = the level is noise that price walks through.

**Baseline for comparison:** naive prior-week low (support) / prior-week high
(resistance). Any fancier family must match or beat family #1 to be worth its cost.

## 4. Region-by-region differences we EXPECT and will verify in the backtest

| Region | What changes vs the base algo |
|---|---|
| **US** | Cleanest. Liquid, continuous, no hard daily limit → touch/held stats are clean. Options-rich (IV layer later, not in this v1). |
| **Hong Kong** | T+0, closing auction 16:00–16:10 sharpens closes → breaks/pivots on Friday close are meaningful. A/H-link drift; however thin liquidity on smaller names inflates gaps. |
| **China A-shares** | **±10% daily price limit (±5% ST, ±20% STAR/ChiNext)**. Limit-up/down days *gap through* levels rather than trade through them → respect_rate artificially inflates (price can't close through a 10% wall in one day), and many weeks never touch S/R. Expect high respect / low touch. Must flag limit-moves as non-informative. |
| **Japan/Korea/Asia** | Board-lot + coarse tick sizes blunt exact touches; Korea ±30% still lets strong breaks through. Price steps mean "touch within w" needs w large enough to clear tick granularity. |
| **Europe** | Fragmented venues make the listed close a composite; index-heavy; thinner single-name liquidity below mega-caps → same touch/held stats but noisier. |

The backtest's **per-region result table** is precisely how we *measurably* confirm or
refute these expectations instead of assuming them.

## 5. Deliverables of this run

1. **Per-ticker, per-family** `touch_rate` + `respect_rate` for support and resistance.
2. **Per-region aggregation** (2 representative liquid tickers each): US, HK, CN,
   JP/KR/Asia, EU.
3. **Interpretation**: which level families work where, which region shows the
   expected price-limit artifacts, and a "good/bad" verdict per cell.
4. **Visuals**:
   - One annotated price chart with the top S/R regions drawn as shaded bands
     (shows a respected zone vs a broken zone visually).
   - A **respect-rate × touch-rate scatter** per region (the "good quadrant" = NE).
   - A **per-region bar** of respect-rate for support vs resistance.
5. **Next step** the numbers justify: e.g., CN needs a limit-move flag + w large
   enough to matter; thin regions may need the ML P(hold) layer from the big plan.

---

*Scope note:* this v1 tests the *deterministic, interpretable* families on daily/weekly
OHLCV (works in all 5 regions). The options-IV layer (US/HK only), HMM regime tags,
and ML P(hold) scoring are the next phases — they layer on exactly these touch/held
statistics as their evaluation target.

*Not financial advice — do your own DD.*
