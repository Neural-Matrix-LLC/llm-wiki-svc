# Support-Resistance-Grok: Multi-Method Framework for Weekly Support & Resistance Levels

**Senior Hedge-Fund Analyst Research Framework**  
**Target Horizon:** Next 5 trading days (weekly durable zones)  
**Coverage:** US, HK/China, EU, and broader Asia equities  
**Last Updated:** September 2026

---

## 1. Objective & Design Principles

Identify **actionable support and resistance (S/R) zones** (price bands, not single ticks) expected to remain relevant over the coming week.  

Key requirements:
- Multi-method confluence (classical + quantitative + options microstructure + deep learning + LLM reasoning)
- Market-agnostic core with local adapters for liquidity, microstructure, holidays, and data availability
- Probabilistic output: ranked zones with confidence, expected hold probability, and explicit invalidation rules
- Production-ready: walk-forward validated, regime-aware, continuously recalibratable

---

## 2. Unified Data Layer

### Core Series (all markets)
- Adjusted OHLCV (daily + 1-min / 5-min / 15-min / 1-hour)
- Volume, VWAP, relative volume vs ADV
- Corporate actions, dividends, splits fully adjusted

### Enhanced Series (availability-dependent)
- **Options open interest, volume, Greeks** (priority for liquid names)
- Level-2 / order-book imbalance proxies
- Short interest, borrow rates
- Earnings dates, macro calendar, local holidays

### Market-Specific Notes
| Region       | Options Liquidity | Key Data Considerations                  |
|--------------|-------------------|------------------------------------------|
| US           | Highest           | Full chain, weekly options, GEX feasible |
| EU           | Good (majors)     | Solid on large-caps & indices            |
| HK / Japan / Korea / India | Moderate–High on indices & selected stocks | Index options stronger than single-stock |
| China A-shares | Limited / Growing | Index options (CSI 300/1000) more usable; single-stock thinner |

Time-zone alignment to local exchange time is mandatory. Continuous futures or ADRs handled separately when liquidity differs from the underlying.

---

## 3. Classical Technical Analysis Layer (Baseline)

- Pivot points: Classic, Fibonacci, Camarilla, Woodie (prior week / month)
- Swing high/low detection (ATR- or %-filtered local extrema, multi-timeframe)
- Fibonacci retracements & extensions from major impulses
- Dynamic levels: 20/50/200 EMA/SMA, Keltner channels, Bollinger bands
- Volume Profile / Market Profile: POC, VAH/VAL, HVN, LVN (prior 5–20 sessions)
- Psychological round numbers + prior day/week highs & lows
- Simple pattern recognition (double tops/bottoms, triangles, head-and-shoulders)

**Scoring:** Number of confluences × historical reaction strength (bounce/rejection frequency & magnitude) × volume participation.

---

## 4. Quantitative / Statistical Layer

- Kernel density / Gaussian mixture models on recent price distribution → high-probability reaction zones
- Change-point detection (PELT, Bayesian online) and regime-switching models
- GARCH-family conditional quantiles for probabilistic bands
- Volume-delta / cumulative volume-delta proxies
- Mean-reversion diagnostics (Hurst, half-life around candidate levels)
- Monte-Carlo / bootstrap path simulation conditioned on current vol & autocorrelation → P(level holds N sessions)
- Peer / index futures mapping (beta-adjusted translation of levels)

---

## 5. Options Open Interest Layer (Deep Dive)

Options positioning supplies **mechanical** S/R that classical charting often misses, especially into weekly expirations.

### Core Concepts
- **High-OI Strikes as Walls / Magnets**  
  Concentrated open interest creates “strike walls.” Price frequently reacts or pins near large OI concentrations, particularly in the final 1–5 days before expiration.

- **Put Walls ≈ Support**  
  Large put OI: dealers who sold puts must buy the underlying as price falls (to stay delta-hedged) → buying pressure supports the level.

- **Call Walls ≈ Resistance**  
  Large call OI: dealers who sold calls sell the underlying as price rises → selling pressure caps upside.

- **Max Pain**  
  The strike that minimizes total intrinsic value paid to option holders at expiration. Acts as a gravitational attractor, strongest near expiry. Computed by evaluating every strike as a hypothetical settlement price and selecting the one with lowest aggregate payout.

- **Gamma Exposure (GEX) / Dealer Gamma**  
  - Positive net GEX (dealers long gamma): dealers buy dips / sell rips → dampens volatility, reinforces range.
  - Negative net GEX: dealers amplify moves.
  - **Gamma Flip / Zero-Gamma Level**: price where net GEX crosses zero — key pivot between stabilizing and destabilizing regimes.
  - **Call Wall**: strike with highest call gamma (strong resistance).
  - **Put Wall**: strike with highest put gamma (strong support).

- **Put/Call OI Ratio & Imbalance**  
  Extreme ratios signal directional skew that can reinforce or undermine walls.

### Practical Implementation for Weekly Horizon
1. Focus on front-week and next-week expirations (highest relevance for “coming week”).
2. Filter strikes within ±10–15 % of spot (or 2–3 ATR).
3. Rank strikes by:
   - Raw OI concentration (≥ 2× average of neighboring strikes)
   - Dollar-gamma-weighted OI (far more accurate than raw contracts)
   - Recent volume at the strike
   - Alignment with Max Pain and Gamma Flip
4. Score a wall 0–4 when multiple factors converge (OI concentration + high |GEX| + PCR confirmation + Max Pain proximity). Highest-conviction walls receive elevated weight in the final ensemble.
5. Track daily OI changes (new positions vs residual from prior cycle).

### Market Applicability
- **US**: Fully operational on most liquid single names and all major ETFs/indices. Weekly options make the signal especially powerful for a 5-day horizon.
- **EU**: Reliable on large-caps and indices (DAX, CAC, FTSE options).
- **HK / Japan / Korea / India**: Strong on index options; usable on the most liquid single stocks. Single-stock chains thinner → widen zones and lower confidence.
- **China A-shares**: Primarily index options (CSI 300, CSI 1000). Single-stock options still limited; treat as secondary confirmation only.

**Caveats**: Max Pain is a useful summary statistic but weaker than gamma-weighted walls for multi-day horizons. Always combine with volume and classical levels; never trade OI walls in isolation.

---

## 6. LSTM & Sequence-Model Layer (Deep Dive)

LSTM networks excel at capturing temporal dependencies in financial time series and can be trained either to forecast prices or, more usefully for this task, to **detect or score future reaction zones**.

### Problem Formulations
1. **Regression**: Predict the next significant support and resistance prices (or distance from current price).
2. **Classification**: For each candidate level, predict P(hold / bounce / break) over the next 1–5 sessions.
3. **Sequence-to-sequence**: Output a set of probable reaction levels directly from the recent price-volume sequence.
4. **Hybrid with Fibonacci / classical levels**: Condition the LSTM on pre-computed Fibonacci or pivot levels (as in published research combining Fibonacci retracements inside LSTM architectures).

### Recommended Architecture
- **Input features** (multi-timeframe, lookback 20–60 bars):
  - OHLCV, log-returns, ATR, RSI, MACD histogram
  - Distance to key MAs, volume profile nodes, VWAP deviation
  - Relative volume, order-flow imbalance proxies
  - Options-derived features when available (distance to nearest put/call wall, GEX regime flag, Max Pain distance)
  - Contextual: day-of-week, days-to-earnings, volatility regime
- **Core stack**:
  - 1–3 stacked LSTM (or Bi-LSTM) layers (50–128 units)
  - Optional attention mechanism or Transformer encoder on top for longer-range context
  - Dense head(s) for multi-horizon outputs (1-day, 3-day, 5-day hold probabilities or level forecasts)
- **Variants to ensemble**:
  - Standard LSTM
  - GRU (lighter, often competitive)
  - LSTM + CNN hybrid (candlestick “image” channels)
  - PSO- or Optuna-tuned LSTM (dynamic sequence length & architecture per stock/regime)
- **Training regime**:
  - Walk-forward / purged cross-validation
  - Labels derived from historical reaction success (e.g., bounce ≥ 1.5× ATR without decisive close beyond the level)
  - Loss: combination of MSE (for level regression) + binary/cross-entropy (for hold probability)
  - Online / continual fine-tuning with newest bars; market-specific adapters or separate heads for US vs Asia liquidity regimes

### Integration with Broader Framework
LSTM outputs are treated as one high-weight vote in the ensemble. When LSTM-predicted levels cluster with classical pivots, volume-profile nodes, **and** options walls, confidence rises sharply.

Published evidence shows LSTM (and GRU) architectures can successfully learn support/resistance behavior when properly labeled and when Fibonacci or other structural features are injected as auxiliary inputs.

---

## 7. LLM / Foundation-Model Layer

- Structured prompting with: recent price summary, all candidate levels already computed, news/earnings, macro calendar.
- Tasks: rank zones, supply narrative catalysts that could invalidate them, surface qualitative levels (earnings gaps, psychological handles).
- RAG over historical analogous setups (similar vol regime, sector, technical configuration).
- Multi-agent critique: extraction agent → market-specific realism agent (China limit rules, European half-days, etc.) → synthesis agent.

---

## 8. Aggregation, Scoring & Final Output

1. Collect candidates from all layers (classical, quant, options OI/GEX/Max Pain, LSTM, LLM).
2. Cluster into zones (ATR- or percentage-based distance, hierarchical clustering / DBSCAN).
3. Score each zone:
   - Confluence count & diversity of sources
   - Historical reaction success rate
   - LSTM-predicted hold probability
   - Options wall strength (0–4 scale) and GEX regime alignment
   - Volume / OI participation
   - Distance from spot & current volatility
   - LLM narrative confidence
4. Deliver ranked list of 3–7 primary weekly zones containing:
   - Price band (mid ± width)
   - Confidence / expected hold probability
   - Invalidation rule (e.g., decisive close beyond zone by X ATR)
   - Suggested stop / position-sizing guidance
5. Portfolio overlay: correlation of potential S/R breaks across book; stress under high-vol or negative-GEX regimes.

---

## 9. Multi-Market Adaptation Summary

- Widen zones and lower confidence weights in thinner markets.
- Session-aware volume profiles (respect overnight gaps, local open/close).
- Automatic holiday & event de-weighting.
- Graceful degradation: when options or high-frequency data are sparse, fall back to classical + LSTM + volume-profile core.
- Cross-listing / ADR mapping when dual-listed.

---

## 10. Implementation Roadmap

1. Unified data ingestion + classical TA + volume profile (Weeks 1–2)
2. Options OI / GEX / Max Pain engine (US first, then EU & liquid Asia) (Week 3)
3. Quantitative statistical baselines (Week 4)
4. Feature store + supervised ML + LSTM/GRU sequence models with walk-forward validation (Weeks 5–8)
5. Ensemble scoring + LLM/RAG layer (Weeks 9–10)
6. Multi-market adapters, holiday logic, production monitoring (Weeks 11–12)
7. Continuous live performance tracking, recalibration triggers, A/B testing of model variants

**Key Metrics**: Hit rate of reactions, average bounce size, false-break frequency, risk-adjusted returns of simple strategies that trade the zones. Segment by market, market-cap, and volatility/GEX regime.

---

## 11. Risk & Operational Caveats

- All levels are probabilistic. Pair with strict position sizing, stops, and portfolio risk limits.
- Over-fitting risk is high with deep models — mandatory walk-forward and regime-stratified validation.
- Options signals decay rapidly after expiration; refresh daily.
- Data quality and latency vary sharply across regions; design fallback paths.
- Compliance and model-risk review required before live deployment.

---

## 12. Summary of Edge

The framework gains robustness from **orthogonal information sources**:
- Classical & volume-profile → structural price memory
- Options OI / GEX / Max Pain → dealer hedging mechanics and near-term pinning forces
- LSTM sequence models → learned temporal reaction patterns
- Quant statistics & Monte-Carlo → probabilistic hold estimates
- LLM layer → contextual and narrative filters

When these layers align, the resulting weekly S/R zones carry materially higher expected durability across US, HK/China, EU, and broader Asian equities.

---

*Document prepared for internal research use. Not investment advice.*
