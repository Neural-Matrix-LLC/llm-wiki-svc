# Quantitative Research Specification
## Global Next-Week Support & Resistance Forecasting System

**Version:** 1.0  
**Date:** 3 September 2026  
**Asset Class:** Listed equities  
**Target Markets:** US, Hong Kong, Mainland China, Japan, Korea, Taiwan, Singapore, India, Australia, UK, Continental Europe  
**Forecast Horizon:** 1 trading week  

---

## 1. Executive Summary

The system will not attempt to predict a single future stock price.

Instead, it will estimate a conditional distribution:

\[
P(\text{price path}_{t+1:t+H}\mid X_t)
\]

and use that distribution to identify price zones with unusually high probabilities of producing a meaningful reaction.

For each stock, the system will produce:

1. Current price
2. 2–4 support zones
3. 2–4 resistance zones
4. Probability each zone is touched
5. Probability the zone holds/rejects
6. Probability of a breakout/breakdown
7. Expected reaction magnitude
8. Expected weekly high/low distribution
9. Regime classification
10. Confidence/calibration score
11. Event-risk assessment
12. Cross-sectional/market-context assessment

The system will combine:

- Market structure
- OHLCV
- Volatility
- Volume profile
- VWAP
- Anchored VWAP
- Pivot/Fibonacci structures
- Multi-timeframe extrema
- Gap structure
- Options positioning
- Order-book/microstructure
- Cross-sectional factors
- Sector/index relationships
- Market regime
- Event/calendar information
- Statistical models
- Gradient boosting
- Deep learning
- Graph neural networks
- Monte Carlo simulation
- NLP/LLM-derived event features

The architecture will be ensemble-first. No individual methodology will be assumed to be universally predictive.

---

# 2. Precise Research Question

The primary research question is:

> Given information available immediately before the forecast week, can we identify price zones for which the subsequent probability of a statistically significant reaction is materially different from an unconditional baseline?

For candidate support zone \(S\):

\[
P(\text{bounce}\mid P_t\in S,X_t)
\]

For candidate resistance zone \(R\):

\[
P(\text{rejection}\mid P_t\in R,X_t)
\]

For breakout:

\[
P(\text{breakout}\mid P_t\in R,X_t)
\]

For breakdown:

\[
P(\text{breakdown}\mid P_t\in S,X_t)
\]

The research is successful only if these probabilities are:

- Out-of-sample predictive
- Properly calibrated
- Stable across market regimes
- Stable across countries
- Economically exploitable after transaction costs

---

# 3. Forecast Horizon

Primary horizon:

\[
H=5
\]

trading sessions.

Secondary horizons:

- 1 session
- 2 sessions
- 3 sessions
- 5 sessions
- 10 sessions

The five-session forecast is the production target.

For markets with different trading calendars, \(H\) is defined as five local trading sessions, not five calendar days.

---

# 4. Forecast Timestamp

The production forecast is generated after the final regular trading session before the forecast week.

```text
Friday close
    ↓
Data cutoff
    ↓
Feature calculation
    ↓
Model inference
    ↓
Weekend/event processing
    ↓
Monday–Friday forecast
```

No information occurring after the forecast timestamp may enter the model.

This includes:

- Future news
- Revised corporate data
- Future index membership
- Restated fundamentals
- Future earnings surprises
- Future prices

This is a hard anti-leakage requirement.

---

# 5. Primary Outputs

For every stock:

```text
Ticker
Market
Currency
Forecast timestamp
Current price
Regime
Expected weekly low
Expected weekly high
P10/P25/P50/P75/P90 price distribution

Support 1
Support 1 width
P(touch)
P(hold)
P(break)
Expected reaction
Confidence

Support 2
...

Resistance 1
P(touch)
P(reject)
P(break)
Expected reaction

Resistance 2
...

Event risk
Market risk
Sector risk
Model confidence
Data quality score
```

---

# 6. Mathematical Definition of a Support Zone

A support zone is an interval:

\[
S=[L,U]
\]

rather than a single price.

Define a meaningful reaction threshold:

\[
R_{min}=k\cdot ATR_{20}
\]

where initially:

\[
k=0.5
\]

A support touch occurs when:

\[
Low_t\le U
\]

and:

\[
High_t\ge L
\]

A successful support reaction occurs if, after entering the zone:

\[
P_{future}-P_{entry}\ge0.5ATR_{20}
\]

before a defined breakdown threshold is reached.

A breakdown occurs when:

\[
Close_t<L-\delta
\]

where:

\[
\delta = 0.25ATR_{20}
\]

Initial parameters should subsequently be optimized only through walk-forward research.

---

# 7. Mathematical Definition of Resistance

Resistance is:

\[
R=[L,U]
\]

A resistance touch occurs when:

\[
High_t\ge L
\]

and:

\[
Low_t\le U
\]

A successful rejection occurs when price subsequently falls:

\[
\ge0.5ATR_{20}
\]

before a confirmed breakout.

Breakout:

\[
Close_t>U+\delta
\]

with:

\[
\delta=0.25ATR_{20}
\]

---

# 8. Candidate Level Generation

The first stage generates a large set of potential levels.

Each candidate receives:

```text
level
upper_bound
lower_bound
source
timeframe
age
touch_count
reaction_count
volume
volatility
```

Candidate sources:

## Price Structure

- Previous day high
- Previous day low
- Previous week high
- Previous week low
- Previous month high
- Previous month low
- Year-to-date high
- Year-to-date low
- 52-week high
- 52-week low
- Local swing highs
- Local swing lows
- Consolidation boundaries
- Breakout levels
- Breakdown levels
- Gap boundaries

## Technical

- Pivot
- R1/R2/R3
- S1/S2/S3
- Fibonacci 23.6%
- Fibonacci 38.2%
- Fibonacci 50%
- Fibonacci 61.8%
- Fibonacci 78.6%
- Bollinger boundaries
- Keltner boundaries

## Volume

- POC
- VAH
- VAL
- HVN
- LVN

## VWAP

- Session VWAP
- Weekly VWAP
- Monthly VWAP
- Anchored VWAP

## Options

- High call OI strikes
- High put OI strikes
- Gamma concentration
- Dealer gamma transition levels
- Volatility concentration
- Expiration clusters

---

# 9. Swing-Point Engine

Swing points must be volatility adaptive.

A local high \(H_t\) is accepted if:

\[
H_t > H_{t-k:t+k}
\]

and:

\[
H_t-H_{subsequent\,low}>kATR
\]

Similarly for lows.

Use multiple values of \(k\):

```text
0.5 ATR
1.0 ATR
1.5 ATR
2.0 ATR
```

This generates structural levels at different significance thresholds.

---

# 10. Multi-Timeframe Engine

Calculate independently on:

- 5-minute
- 15-minute
- 30-minute
- 1-hour
- 4-hour
- Daily
- Weekly
- Monthly

Not every market will provide every timeframe.

Each level receives a timeframe importance score.

Initial hierarchy:

```text
Monthly     very high
Weekly      very high
Daily       high
4H          medium-high
1H          medium
30m         medium
15m         lower
5m          lower
```

The exact weights are learned later.

---

# 11. Volume Profile Engine

Create volume-at-price distributions for:

- 5 sessions
- 20 sessions
- 60 sessions
- 120 sessions
- 252 sessions

Calculate:

\[
VP(p)=\sum_t V_tI(P_t\in bin_p)
\]

Features:

- POC
- VAH
- VAL
- HVN count
- HVN density
- LVN count
- Distance to POC
- Distance to nearest HVN
- Distance to nearest LVN
- Volume concentration
- Volume profile skew
- Volume profile kurtosis

Use adaptive price bins based on:

\[
bin\ width = c\cdot ATR
\]

rather than fixed dollar increments.

---

# 12. VWAP Engine

Calculate:

\[
VWAP_t=\frac{\sum_{i=1}^{t}P_iV_i}
{\sum_{i=1}^{t}V_i}
\]

Use:

- Intraday VWAP
- Weekly VWAP
- Monthly VWAP
- Quarterly VWAP
- Year-to-date VWAP

Anchored VWAPs:

- Last major high
- Last major low
- Earnings gap
- Major gap
- Breakout
- Breakdown
- Year start
- Quarter start

Features:

\[
Distance=\frac{Price-VWAP}{ATR}
\]

and:

- Number of VWAPs within 0.25 ATR
- Number within 0.5 ATR
- Number within 1 ATR
- VWAP slope
- VWAP convergence

---

# 13. Level Clustering

Raw candidate levels will be clustered.

Let candidate levels be:

\[
L_1,L_2,...,L_n
\]

Normalize distance:

\[
d_{ij}=\frac{|L_i-L_j|}{ATR}
\]

Use:

- DBSCAN
- HDBSCAN
- Hierarchical clustering

A cluster becomes a candidate zone.

Zone center:

\[
Z=\frac{\sum_iw_iL_i}{\sum_iw_i}
\]

Zone width:

\[
W=q\cdot ATR
\]

where \(q\) is learned from historical reaction distributions.

---

# 14. Confluence Score

Each candidate zone receives:

\[
C =
C_{structure}
+C_{volume}
+C_{VWAP}
+C_{options}
+C_{timeframe}
+C_{momentum}
+C_{orderflow}
+C_{market}
\]

Features include:

- Number of independent evidence sources
- Evidence diversity
- Timeframe agreement
- Historical reaction strength
- Recentness
- Volume concentration
- Distance from current price
- Trend alignment

An important design principle:

> Five independent signals should be worth more than five variants of the same indicator.

Therefore the model should explicitly measure signal independence.

---

# 15. Historical Level-Reaction Database

Create a database of every historical candidate level.

Each observation:

```text
stock
market
timestamp
level
zone_width
source
timeframe
distance_from_price
ATR
volume
regime
market_state
sector_state
touch
reaction
hold
break
reaction_size
time_to_reaction
time_to_break
```

This becomes the training dataset.

---

# 16. Primary Labels

For each level:

### Touch

\[
Y_{touch}=1
\]

if price enters the zone within five sessions.

### Hold

\[
Y_{hold}=1
\]

if the zone is touched and price produces the required reaction before breaking.

### Break

\[
Y_{break}=1
\]

if the zone is touched and the breakout/breakdown condition occurs first.

### Reaction magnitude

\[
Y_{reaction}
=
\frac{Extreme_{post-touch}-Entry}
{ATR_{20}}
\]

### Time-to-event

Number of sessions until:

- Touch
- Reaction
- Break

---

# 17. Market-Regime Model

Features:

- Realized volatility
- ATR
- Trend
- Momentum
- Index returns
- Breadth
- Correlation
- Volatility index
- Rates
- FX
- Credit conditions
- Commodity conditions

Models:

1. Hidden Markov Model
2. Markov-switching model
3. Gradient-boosted classifier

Output:

```text
Strong bull
Weak bull
Range
Weak bear
Strong bear
High-volatility / crisis
```

Regime probabilities rather than hard labels should be retained.

---

# 18. Cross-Sectional Factor Engine

For every stock:

- Market beta
- Sector beta
- Momentum
- Size
- Value
- Quality
- Volatility
- Liquidity
- Growth
- Short interest where available
- Relative strength

Calculate:

\[
RS_i=
R_i-R_{benchmark}
\]

over:

- 1 day
- 5 days
- 20 days
- 60 days
- 120 days

---

# 19. Sector / Index Context

Each stock should be modeled jointly with:

- Primary index
- Sector index
- Major ETF
- Country index
- Relevant commodity
- Relevant FX

Features:

- Correlation
- Relative performance
- Sector breadth
- Leadership
- Dispersion
- Beta instability

---

# 20. Options Module

Where liquid options exist:

Features:

- Call OI
- Put OI
- Call volume
- Put volume
- OI change
- Volume/OI
- Put/call ratio
- Strike density
- Expiration density
- Implied volatility
- IV skew
- IV term structure
- Gamma exposure
- Delta exposure
- Vanna
- Charm
- Dealer positioning estimates

For every strike:

\[
D(K)=OI_{call}(K)+OI_{put}(K)
\]

Then calculate the distance from current price to major strike concentrations.

Options data is optional rather than mandatory because coverage differs substantially between global markets.

---

# 21. Order-Book Module

Where Level-2 data exists:

### Order-book imbalance

\[
OBI=
\frac{BidDepth-AskDepth}
{BidDepth+AskDepth}
\]

Additional features:

- Top-of-book imbalance
- 5-level imbalance
- 10-level imbalance
- Spread
- Relative spread
- Depth
- Depth slope
- Cancellation rate
- Aggressive buy volume
- Aggressive sell volume
- Signed trade volume
- Trade-size distribution
- Price impact

Calculate these over:

- 1 minute
- 5 minutes
- 30 minutes
- Session

---

# 22. Event Engine

Identify events occurring during the forecast window:

- Earnings
- Guidance
- Investor days
- Dividends
- Splits
- M&A
- Regulatory events
- Product launches
- FDA events
- Central-bank meetings
- CPI
- PPI
- Employment reports
- GDP
- PMI
- Major China data
- Geopolitical events

Output:

\[
EventRisk\in[0,1]
\]

and:

\[
ExpectedJumpMagnitude
\]

---

# 23. NLP / LLM Engine

The LLM is not the price predictor.

Its role is information extraction.

Inputs:

- Earnings transcripts
- SEC filings
- HKEX filings
- Exchange announcements
- Company announcements
- News
- Guidance
- Macro statements

Extract structured variables:

```text
sentiment
guidance_change
earnings_risk
regulatory_risk
demand_change
pricing_change
margin_change
capex_change
management_confidence
surprise_probability
event_severity
```

All LLM outputs must include:

```text
value
confidence
source
timestamp
```

No future information is permitted.

---

# 24. NLP Embedding

Represent textual information as:

\[
E_t=f_{\theta}(Text_t)
\]

Use either:

- Financial language model
- Transformer embeddings
- LLM-generated structured features

Then combine with market features.

Do not allow free-form LLM reasoning to directly generate trade signals in the production model.

---

# 25. Statistical Baseline Models

Before using machine learning, establish strong baselines.

### Model A — Historical frequency

\[
P(hold|zone)
\]

### Model B — Logistic regression

\[
P(Y=1)=\sigma(\beta X)
\]

### Model C — Bayesian model

\[
P(Hold|X)
\]

with priors by:

- market
- sector
- volatility regime

### Model D — Survival model

Estimate:

\[
P(T_{break}\le t)
\]

These models establish whether more sophisticated models actually add value.

---

# 26. Gradient-Boosting Model

Primary tabular ML model:

**LightGBM / XGBoost / CatBoost**

Target tasks:

1. Touch probability
2. Hold probability
3. Break probability
4. Reaction magnitude
5. Time to break

Feature count initially:

**150–250 engineered features**

The model should use:

- Missing-value handling
- Market-specific categorical variables
- Regime features
- Interaction terms where useful

---

# 27. Deep Learning Model

Primary architecture:

```text
OHLCV
Volume
Technical
VWAP
Volume Profile
Market
Sector
Options
Order Flow
Text
      ↓
Embedding layers
      ↓
Temporal encoder
      ↓
Transformer / TCN
      ↓
Shared representation
      ↓
Multi-task heads
```

Outputs:

\[
P(touch)
\]

\[
P(hold)
\]

\[
P(break)
\]

\[
Q_{10},Q_{25},Q_{50},Q_{75},Q_{90}
\]

for weekly price distribution.

---

# 28. Multi-Task Objective

Use:

\[
L=
\lambda_1L_{touch}
+\lambda_2L_{hold}
+\lambda_3L_{break}
+\lambda_4L_{quantile}
+\lambda_5L_{reaction}
\]

where:

- Classification losses = cross entropy
- Distribution = quantile loss
- Reaction = Huber loss

Weights are selected using validation performance.

---

# 29. Graph Neural Network

Represent stocks as nodes.

Edges can represent:

- Same sector
- Same country
- Supply chain
- High correlation
- ETF co-membership
- Common factor exposure

The GNN estimates contextual information:

\[
h_i^{GNN}=f(h_i,\{h_j:j\in N_i\})
\]

This provides information about whether a stock's support is being tested in isolation or as part of a broader sector/market breakdown.

---

# 30. Monte Carlo Engine

For each stock, simulate at least:

**10,000–50,000 weekly paths**

Use:

- Volatility regime
- Autocorrelation
- Historical residual bootstrap
- Factor shocks
- Correlation structure
- Gap probabilities
- Event risk

Generate:

\[
P(Low<L)
\]

\[
P(High>R)
\]

and:

\[
P(\text{zone touched})
\]

Monte Carlo is primarily a distribution generator and scenario engine, not the sole price predictor.

---

# 31. Meta-Ensemble

Final prediction:

```text
                 Logistic
                    │
             Gradient Boost
                    │
                Transformer
                    │
                   GNN
                    │
               Survival
                    │
               Monte Carlo
                    │
                    ↓
              Meta Learner
                    ↓
          Calibrated Probability
```

Initial meta-model:

**Logistic regression**

followed by:

- Isotonic regression
- Platt scaling

depending on validation results.

The purpose is probability calibration.

---

# 32. Final Zone Score

For each zone \(z\):

\[
Score_z=
f(
Technical,
Volume,
VWAP,
Structure,
Options,
OrderFlow,
Regime,
Market,
Sector,
ML,
DL,
Events
)
\]

The model outputs:

\[
P_{touch}
\]

\[
P_{hold}
\]

\[
P_{break}
\]

and:

\[
E[reaction]
\]

---

# 33. Confidence Score

Confidence must not simply equal model probability.

Define:

\[
Confidence =
f(
ModelAgreement,
Calibration,
DataQuality,
RegimeStability,
HistoricalSampleSize,
CrossMarketConsistency
)
\]

Example:

```text
High:
probability > 70%
+
strong model agreement
+
good historical calibration
+
high data quality

Medium:
probability 55–70%

Low:
probability <55%
or
model disagreement
or
event risk is extreme
```

---

# 34. Model-Agreement Feature

Example of high agreement:

```text
Technical       Hold = 71%
XGBoost         Hold = 74%
Transformer     Hold = 68%
Order flow      Hold = 77%
Options         Hold = 73%
```

Contrast with:

```text
Technical       75%
XGBoost         71%
Transformer     38%
Order flow      31%
Options         29%
```

The second case should materially reduce confidence.

---

# 35. Walk-Forward Backtesting

Never use random train/test splitting.

Use:

```text
Train          Validation        Test
2010–2017      2018              2019
2010–2018      2019              2020
2010–2019      2020              2021
2010–2020      2021              2022
...
```

Use:

- Expanding windows
- Rolling windows
- Purged CV
- Embargo periods

The final test set must remain untouched until model selection is complete.

---

# 36. Cross-Market Validation

Evaluate separately:

```text
US
Hong Kong
China
Japan
Korea
Taiwan
India
Singapore
Australia
UK
Germany
France
Switzerland
Italy
Spain
```

Then compare:

1. Global pooled model
2. Market-specific models
3. Hierarchical model

Hierarchical specification:

\[
Prediction=
Global+
MarketEffect+
SectorEffect
\]

---

# 37. Regime-Based Validation

Report performance separately for:

- Bull market
- Bear market
- Range
- High volatility
- Low volatility
- Crisis
- Earnings week
- Non-event week

A model that works only in calm bull markets is not production quality.

---

# 38. Key Evaluation Metrics

## Probability

### Brier score

\[
BS=\frac1N\sum(p_i-y_i)^2
\]

### Log loss

\[
LL=-\frac1N\sum[y_i\log p_i+(1-y_i)\log(1-p_i)]
\]

### Calibration error

Compare predicted probabilities with realized frequencies.

---

# 39. Level Metrics

### Zone precision

How often does a predicted zone subsequently experience the intended reaction?

### Zone recall

How many important historical reactions were captured?

Also measure:

- Touch accuracy
- Hold accuracy
- Breakout accuracy
- Average reaction magnitude
- Time-to-reaction

---

# 40. Economic Metrics

Measure:

- Gross return
- Net return
- Sharpe
- Sortino
- Maximum drawdown
- Calmar
- Turnover
- Hit rate
- Profit factor
- Average holding period
- Transaction costs
- Slippage
- Market impact

---

# 41. Trading Simulation

For a support signal:

```text
Price approaches support
        ↓
P(hold) > threshold
        ↓
Order-flow confirmation
        ↓
Enter
        ↓
Stop below structural invalidation
        ↓
Target next resistance
```

For resistance:

```text
Price approaches resistance
        ↓
P(reject) > threshold
        ↓
Negative order flow
        ↓
Short / reduce exposure
        ↓
Target support
```

This is optional for the initial research phase. The first objective is forecast validation, not strategy optimization.

---

# 42. Transaction-Cost Model

For each market:

\[
NetReturn=
GrossReturn-
Commission-
Spread-
Slippage-
MarketImpact
\]

Use market-specific assumptions.

For liquid US large caps:

- Tight spreads
- Relatively low impact

For less-liquid Asian/European securities:

- Wider spreads
- Larger impact
- Lower capacity

Capacity must be tested separately.

---

# 43. Data Quality Layer

Every prediction receives:

\[
DQ\in[0,1]
\]

Factors:

- Missing bars
- Bad ticks
- Corporate actions
- Volume quality
- Delayed data
- Order-book completeness
- Options coverage
- Currency conversion
- Trading-calendar correctness

If:

\[
DQ<0.7
\]

the system should either suppress the signal or explicitly downgrade confidence.

---

# 44. Corporate Actions

All historical price series must handle:

- Splits
- Reverse splits
- Special dividends
- Rights offerings
- Spin-offs
- Mergers
- Delistings

The system should retain both:

1. Raw market data
2. Research-adjusted data

Do not overwrite raw data.

---

# 45. Currency Treatment

Models should operate primarily on normalized returns rather than absolute currency.

For cross-market comparisons retain both local-currency and base-currency returns.

For a US investor holding a Japanese stock:

\[
R_{USD}
\approx
(1+R_{JPY})(1+R_{FX})-1
\]

FX risk becomes a model feature.

---

# 46. Trading Calendar Engine

Must support:

- US holidays
- HK holidays
- China holidays
- Japan holidays
- Korea holidays
- Taiwan holidays
- European exchange calendars
- Half-days
- Market-specific auction periods

Never assume five calendar days equals five trading sessions.

---

# 47. Data Architecture

Recommended storage:

```text
Raw Data
   ↓
Normalized Data
   ↓
Feature Store
   ↓
Training Dataset
   ↓
Model Store
   ↓
Forecast Database
   ↓
Analytics Dashboard
```

Recommended technologies:

```text
Python
DuckDB / PostgreSQL
Parquet
Polars / Pandas
NumPy
scikit-learn
LightGBM/XGBoost
PyTorch
MLflow
Docker
Airflow / Prefect
```

---

# 48. Suggested Data Schema

## security_master

```text
security_id
ticker
exchange
country
currency
sector
industry
asset_type
listing_date
delisting_date
```

## market_bar

```text
security_id
timestamp
open
high
low
close
volume
vwap
```

## order_book

```text
security_id
timestamp
bid_price
ask_price
bid_depth
ask_depth
level_depth
```

## options

```text
security_id
timestamp
expiration
strike
call_oi
put_oi
call_volume
put_volume
iv
delta
gamma
vega
theta
```

## event

```text
security_id
timestamp
event_type
severity
source
text
embedding
sentiment
```

## candidate_level

```text
security_id
timestamp
level
lower_bound
upper_bound
source
timeframe
score
```

## forecast

```text
security_id
forecast_timestamp
forecast_week
support_1
support_2
resistance_1
resistance_2
p_touch
p_hold
p_break
confidence
```

---

# 49. Feature Store

Target:

**200–400 features per security-date observation.**

Organize them into:

```text
01_price
02_return
03_volatility
04_volume
05_structure
06_vwap
07_volume_profile
08_momentum
09_trend
10_options
11_orderflow
12_market
13_sector
14_factor
15_macro
16_event
17_text
18_regime
19_liquidity
20_cross_market
```

---

# 50. Feature Examples

### Price

- Close
- High
- Low
- Open
- Range
- Body
- Upper wick
- Lower wick

### Returns

- 1D
- 2D
- 5D
- 10D
- 20D
- 60D

### Volatility

- ATR
- Realized vol
- Parkinson volatility
- Garman-Klass
- Rogers-Satchell
- Volatility percentile

### Momentum

- RSI
- ROC
- Stochastic
- MACD
- Momentum acceleration

### Trend

- MA distance
- EMA distance
- MA slope
- ADX
- Trend persistence

### Volume

- Relative volume
- OBV
- Volume acceleration
- Volume percentile
- Volume surprise

---

# 51. Advanced Feature: Level Age

For each level:

\[
Age=t-t_{formation}
\]

Test nonlinear decay:

\[
Weight=e^{-\lambda Age}
\]

Older levels may be less relevant, but major historical levels may retain importance.

Therefore include both:

- Age
- Historical significance

rather than assuming all old levels decay identically.

---

# 52. Advanced Feature: Touch Count

For each zone:

\[
N_{touch}
\]

But also:

- Successful reactions
- Failed reactions
- Average reaction
- Reaction volatility
- Time between touches

The research should test whether repeated testing strengthens a level, weakens it, or has no systematic effect.

---

# 53. Advanced Feature: Approach Direction

A support level approached from above after a rapid selloff is not equivalent to support approached after slow consolidation.

Encode:

- Approach velocity
- Approach ATR
- Momentum
- Volume acceleration
- Gap
- Number of consecutive down days
- Intraday trend

Same principle for resistance.

---

# 54. Advanced Feature: Approach Shape

Encode the last \(N\) bars before entering the zone.

Potential representations:

- Returns
- OHLC normalized by ATR
- Volume
- VWAP distance

Then allow the neural network to learn whether different approach patterns have different reaction probabilities.

These are hypotheses to test, not assumptions.

---

# 55. Quantile Forecasting

Instead of predicting:

\[
E[P_{t+5}]
\]

predict:

\[
Q_{0.05},Q_{0.10},Q_{0.25},
Q_{0.50},Q_{0.75},Q_{0.90},Q_{0.95}
\]

This gives the full weekly distribution.

Example:

```text
5%      $171
10%     $174
25%     $178
50%     $183
75%     $189
90%     $194
95%     $198
```

This is directly useful for defining expected support/resistance interactions.

---

# 56. Conformal Prediction

Add conformal prediction to produce empirical prediction intervals.

Instead of:

> The model thinks weekly high is $193.

produce:

> 90% calibrated prediction interval for weekly high: $187–198.

This provides an additional uncertainty-control layer.

---

# 57. Uncertainty Decomposition

Separate:

### Aleatoric uncertainty

Market randomness.

### Epistemic uncertainty

Model uncertainty.

Estimate using:

- Deep ensembles
- Bootstrap models
- Monte Carlo dropout
- Quantile models

A signal should be downgraded if different models disagree substantially.

---

# 58. Model Governance

Every production model must have:

- Version
- Training period
- Feature version
- Data version
- Hyperparameters
- Validation results
- Calibration results
- Market coverage
- Known limitations

Store every forecast permanently.

This permits later attribution:

> Why did the system call this zone support?

---

# 59. Forecast Attribution

For every prediction, provide feature attribution.

For gradient boosting:

- SHAP

For neural models:

- Integrated gradients
- Attention diagnostics
- Feature ablation

Example:

```text
Support score: 81%

Main contributors:
+ Weekly swing low
+ 60D HVN
+ Anchored VWAP
+ Positive sector relative strength

Negative:
- Elevated volatility
- Upcoming earnings
```

This makes the system usable by human portfolio managers.

---

# 60. LLM Explanation Layer

Only after the numerical model generates its output should an LLM create the human-readable explanation.

Input:

```text
Model output
Feature attribution
Events
Market regime
```

The LLM may explain the result but must not alter numerical model outputs.

---

# 61. Production Decision Logic

Example:

```text
IF
P(touch) >= 0.60
AND
P(hold) >= 0.65
AND
confidence >= 0.70
AND
DQ >= 0.80
AND
event_risk <= threshold
THEN
publish as HIGH-CONVICTION SUPPORT
```

Similar rules apply to resistance.

Thresholds should be optimized on validation data and frozen before the final test.

---

# 62. Signal Ranking

Not every support/resistance zone should be published.

Rank by:

\[
Priority =
P(touch)
\times
P(reaction)
\times
ExpectedMagnitude
\times
Confidence
\]

Then publish:

- 2 supports
- 2 resistances

plus secondary levels if warranted.

---

# 63. Breakout/Breakdown State Machine

Each level should have a state:

```text
UNTESTED
   ↓
APPROACHING
   ↓
TOUCHED
   ↓
 ┌─┴──────┐
 ↓        ↓
HELD    BROKEN
 ↓        ↓
RETEST   NEW LEVEL
 ↓
CONTINUATION
```

This is important because support/resistance is dynamic.

A broken resistance can become support.

A broken support can become resistance.

---

# 64. Dynamic Level Updating

After a breakout:

\[
Resistance \rightarrow Support
\]

if the retest succeeds.

The system should therefore maintain level identity over time.

Each zone receives:

```text
zone_id
original_source
formation_time
current_state
polarity
strength
```

---

# 65. Research Hypotheses

The project should formally test:

### H1
Multi-timeframe confluence improves support/resistance prediction.

### H2
Volume-profile information improves prediction beyond price-only technical indicators.

### H3
Anchored VWAP improves prediction beyond conventional VWAP.

### H4
Order-flow data improves short-horizon reaction prediction.

### H5
Options positioning improves prediction for liquid optionable US equities.

### H6
Regime conditioning improves calibration.

### H7
Sector/index context improves prediction.

### H8
Text/event information improves prediction around corporate events.

### H9
Deep learning outperforms gradient boosting on sequential representations.

### H10
Ensembling improves out-of-sample calibration.

### H11
Global pooled models outperform isolated country models after market-specific normalization.

### H12
Support/resistance zones have greater economic value than exact-price predictions.

---

# 66. Ablation Study

Run the complete model and then remove components:

```text
Model 0   Price only
Model 1   + Technical
Model 2   + Volume
Model 3   + VWAP
Model 4   + Market/sector
Model 5   + Options
Model 6   + Order flow
Model 7   + Events/NLP
Model 8   + ML
Model 9   + Deep learning
Model 10  Full ensemble
```

Measure incremental improvement.

A complicated model should not be considered successful if it adds no out-of-sample information.

---

# 67. Feature Leakage Tests

For every feature ask:

> Could this value have been known at the exact forecast timestamp?

Automated checks should reject:

- Future prices
- Future volume
- Restated fundamentals
- Future index membership
- Revised corporate information
- Post-close announcements incorrectly timestamped
- Survivorship-biased securities

---

# 68. Survivorship Bias

The universe must include:

- Delisted stocks
- Bankrupt companies
- Acquired companies
- Suspended securities

Otherwise historical performance will be overstated.

---

# 69. Selection Bias

Do not test only today's liquid winners.

Define the universe at each historical timestamp.

For example:

```text
At 2018-01-01:
use stocks actually eligible at that date.

At 2019-01-01:
reconstruct the universe again.
```

---

# 70. Liquidity Filter

For each market, calculate:

- ADV
- Median dollar volume
- Spread
- Turnover
- Days traded
- Price
- Free float where available

Create tiers:

```text
Tier 1: institutional liquid
Tier 2: tradable
Tier 3: research only
```

The model may forecast Tier 3, but those signals should not automatically enter the trading simulation.

---

# 71. Benchmark Models

Required baselines:

1. Previous week's high/low
2. Previous day's high/low
3. ATR bands
4. Bollinger Bands
5. VWAP
6. Pivot points
7. Volume Profile
8. Logistic regression
9. Random forest
10. XGBoost
11. Transformer
12. Full ensemble

The full system must beat meaningful baselines.

---

# 72. Minimum Statistical Significance

Require:

- Multiple market cycles
- Large number of level observations
- Confidence intervals
- Bootstrap estimates
- Out-of-sample validation

Avoid declaring success based on a small number of successful predictions.

The correct question is whether the improvement is statistically and economically significant.

---

# 73. Multiple-Hypothesis Control

Hundreds of features and strategies will be tested.

Use:

- Holdout test set
- False discovery controls
- Deflated Sharpe ratio
- Reality-check style procedures
- Multiple-testing corrections where appropriate

Otherwise the research will almost certainly find spurious patterns.

---

# 74. Stress Tests

Test:

- 2008-style crisis
- COVID-type crash
- 2022 rate shock
- Flash crash
- Volatility spike
- Gap opens
- Trading halts
- Limit-up/limit-down
- China market restrictions
- Extreme FX moves

Also test data outages and missing options/order-book data.

---

# 75. Global Market Adaptation

The architecture should be:

\[
GlobalRepresentation
+
MarketEmbedding
+
ExchangeSpecificFeatures
\]

Examples:

```text
Global:
returns
volatility
trend
volume
structure

US:
options
ATS/order flow

China:
limit-price structure
margin
A/H relationships

HK:
Stock Connect
HKEX structure

Japan:
foreign flows
JPY
TSE structure

Europe:
cross-exchange
EUR/GBP/CHF
STOXX
```

The model should gracefully degrade when a feature is unavailable.

---

# 76. Missing Data Strategy

Never simply fill unavailable features with zero without marking them.

Use:

```text
feature_value
feature_available
```

Example:

```text
options_gamma = NaN
options_available = 0
```

The model can learn that the feature is unavailable.

---

# 77. Model Hierarchy

Recommended final hierarchy:

```text
Global Model
     │
     ├── US
     ├── Europe
     ├── HK
     ├── China
     ├── Japan
     ├── Korea
     └── Other Asia
          │
          ↓
Market-specific calibration
          │
          ↓
Stock-specific prediction
```

This prevents forcing identical market microstructure assumptions onto every exchange.

---

# 78. Initial Production Model

Do not start with the Transformer.

The initial production candidate should be:

```text
Candidate-level statistical engine
+
LightGBM
+
Regime model
+
Monte Carlo
+
Probability calibration
```

Then benchmark deep learning against it.

If Transformer/GNN models produce no incremental out-of-sample value, do not deploy them simply because they are more sophisticated.

---

# 79. Phase-1 Research Universe

Start with highly liquid securities.

Example:

```text
US:
S&P 500 / Nasdaq large caps

HK:
Hang Seng constituents

China:
CSI 300

Japan:
TOPIX large caps

Korea:
KOSPI large caps

Europe:
STOXX 600
```

This provides enough liquidity and history to test the concept.

Expand later.

---

# 80. Development Timeline

## Phase 1 — 4 weeks

Data infrastructure.

Deliverables:

- Security master
- Historical OHLCV
- Corporate actions
- Calendars
- Benchmark data

## Phase 2 — 4 weeks

Candidate-level engine.

Deliverables:

- Swings
- Pivots
- Fibonacci
- VWAP
- Volume Profile
- Clustering

## Phase 3 — 4 weeks

Statistical/ML models.

Deliverables:

- Labels
- XGBoost
- Logistic
- Survival
- Calibration

## Phase 4 — 4–6 weeks

Deep learning.

Deliverables:

- Transformer
- TCN
- Multi-task model
- GNN prototype

## Phase 5 — 3–4 weeks

Options/order flow/events.

## Phase 6 — 3 weeks

Ensemble and productionization.

Approximate initial research program:

**22–25 weeks.**

---

# 81. Python Project Structure

```text
sr_forecasting/
│
├── config/
│   ├── markets.yaml
│   ├── features.yaml
│   └── models.yaml
│
├── data/
│   ├── ingestion/
│   ├── normalization/
│   ├── corporate_actions/
│   └── calendars/
│
├── features/
│   ├── price.py
│   ├── volatility.py
│   ├── volume.py
│   ├── structure.py
│   ├── vwap.py
│   ├── volume_profile.py
│   ├── options.py
│   ├── orderflow.py
│   ├── market.py
│   ├── sector.py
│   ├── events.py
│   └── text.py
│
├── levels/
│   ├── swing.py
│   ├── pivots.py
│   ├── fibonacci.py
│   ├── clustering.py
│   └── scoring.py
│
├── labels/
│   ├── touch.py
│   ├── hold.py
│   ├── breakout.py
│   └── reaction.py
│
├── models/
│   ├── baseline.py
│   ├── logistic.py
│   ├── lightgbm.py
│   ├── survival.py
│   ├── transformer.py
│   ├── gnn.py
│   └── ensemble.py
│
├── simulation/
│   ├── monte_carlo.py
│   └── execution.py
│
├── validation/
│   ├── walk_forward.py
│   ├── purged_cv.py
│   ├── calibration.py
│   └── stress.py
│
├── reporting/
│   ├── forecast.py
│   ├── attribution.py
│   └── dashboard.py
│
└── tests/
```

---

# 82. Daily Production Workflow

Although forecasts are generated weekly, the system should update continuously.

```text
Real-time / intraday
        ↓
Data ingestion
        ↓
Feature update
        ↓
Level state update
        ↓
Order-flow update
        ↓
Event update
        ↓
Model monitoring
        ↓
Friday forecast
        ↓
Monday–Friday live monitoring
```

This allows a Friday forecast to be compared with what actually happened during the week.

---

# 83. Monitoring

Monitor:

### Data

- Missingness
- Latency
- Bad ticks
- Corporate-action errors

### Model

- Prediction distribution
- Probability calibration
- Feature drift
- Regime drift
- Model disagreement

### Performance

- Hit rate
- Brier score
- Calibration
- Reaction magnitude
- Net P&L

---

# 84. Model Drift

Run population-stability and distribution-shift checks.

Examples:

\[
PSI
\]

and:

- KS test
- Wasserstein distance
- Feature distribution monitoring

If drift becomes extreme:

```text
WARNING
    ↓
Reduce confidence
    ↓
Recalibrate
    ↓
Retrain
```

---

# 85. Research Dashboard

For each stock, display:

```text
                 R2
             $193–196
                 │
                 │
             R1 $185–187
                 │
            CURRENT $182
                 │
             S1 $178–180
                 │
             S2 $171–174
```

Alongside:

```text
Touch
Hold
Break
Confidence
Regime
Volume
VWAP
Options
Order Flow
Event Risk
```

The visual is for the portfolio manager; the underlying prediction remains probabilistic.

---

# 86. Example Production Record

```text
AAPL
Forecast date: 2026-09-04

Current: 182.40

S1:
178.2–180.0

P(touch):       0.68
P(hold):        0.74
P(break):       0.26
Expected move: +1.15 ATR
Confidence:     0.82

S2:
171.5–174.0

P(touch):       0.31
P(hold):        0.69
P(break):       0.31

R1:
185.0–186.7

P(touch):       0.76
P(reject):      0.58
P(break):       0.42

R2:
192.0–195.0

P(touch):       0.41

Regime:
Bull / high-volatility

Event risk:
Medium

Primary risk:
Market/sector correlation

Primary support evidence:
Weekly structure
+
60D HVN
+
Anchored VWAP
+
sector relative strength
```

---

# 87. Investment-Committee Decision Layer

The forecasting engine should remain separate from the portfolio decision engine.

```text
S/R Forecast
     ↓
Probability
     ↓
Expected reaction
     ↓
Portfolio exposure
     ↓
Risk constraints
     ↓
Execution
```

The S/R model should not independently decide:

- Position size
- Portfolio leverage
- Gross exposure
- Sector exposure
- Stop size

Those belong to the portfolio/risk system.

---

# 88. Position Sizing Interface

The forecasting engine can provide:

\[
Edge=P(win)\times E(win)
-P(loss)\times E(loss)
\]

The portfolio system can then determine position size using:

- Volatility targeting
- Kelly fraction
- Risk budget
- Maximum position
- Sector limits
- Correlation limits

Do not embed portfolio sizing inside the S/R prediction model.

---

# 89. Final Research Deliverables

The research project should produce:

### Deliverable 1
**Level Engine** — Candidate support/resistance zones.

### Deliverable 2
**Probability Engine** — Touch/hold/break probabilities.

### Deliverable 3
**Distribution Engine** — Weekly high/low/close quantiles.

### Deliverable 4
**Regime Engine** — Market-state probabilities.

### Deliverable 5
**Event Engine** — Corporate/macro risk.

### Deliverable 6
**Explanation Engine** — Human-readable attribution.

### Deliverable 7
**Backtesting Framework** — Walk-forward research.

### Deliverable 8
**Production API**

```text
GET /forecast/{ticker}
```

### Deliverable 9
**Portfolio Dashboard** — Cross-sectional ranking of highest-conviction zones.

---

# 90. Definition of Success

The project should not be declared successful because:

- A chart looks good
- An LSTM produces plausible forecasts
- The model has a high in-sample \(R^2\)
- A few individual trades work

The minimum success criterion is:

> The system generates statistically calibrated support/resistance probabilities that remain predictive in genuinely out-of-sample data across multiple markets and regimes, and the resulting signals retain economically meaningful value after realistic transaction costs and execution assumptions.

The ultimate metric is therefore:

\[
\boxed{
Out\text{-}of\text{-}sample\ calibrated\ predictive\ value
+
economic\ value
}
\]

---

# 91. Recommended Final Architecture

```text
                     GLOBAL MARKET DATA
                                │
             ┌──────────────────┼──────────────────┐
             ↓                  ↓                  ↓
          Price              Volume             Events
             ↓                  ↓                  ↓
       Market Structure    Volume Profile       NLP/LLM
             │                  │                  │
             └──────────────┬───┴──────────────────┘
                            ↓
                     FEATURE STORE
                            │
        ┌───────────────────┼───────────────────┐
        ↓                   ↓                   ↓
     Regime             Cross-section       Microstructure
        │                   │                   │
        └───────────────────┼───────────────────┘
                            ↓
                 CANDIDATE LEVEL ENGINE
                            │
                            ↓
                    LEVEL CLUSTERING
                            │
                            ↓
        ┌───────────────────┼───────────────────┐
        ↓                   ↓                   ↓
     Logistic           LightGBM          Transformer
        ↓                   ↓                   ↓
     Survival                GNN           Quantile model
        └───────────────────┼───────────────────┘
                            ↓
                      META ENSEMBLE
                            │
                            ↓
                    PROBABILITY CALIBRATION
                            │
                            ↓
                     MONTE CARLO ENGINE
                            │
                            ↓
                    UNCERTAINTY ENGINE
                            │
                            ↓
                    EVENT-RISK FILTER
                            │
                            ↓
              NEXT-WEEK S/R FORECAST
                            │
              ┌─────────────┼─────────────┐
              ↓             ↓             ↓
           SUPPORT      RESISTANCE     DISTRIBUTION
              ↓             ↓             ↓
          P(touch)      P(touch)       P(low/high)
          P(hold)       P(reject)      Quantiles
          P(break)      P(break)       Scenarios
                            │
                            ↓
                  PORTFOLIO/RISK SYSTEM
                            │
                            ↓
                       EXECUTION
```

---

# 92. Bottom Line

The central design decision is to stop treating support and resistance as technical-analysis lines.

The research target should instead be:

\[
\boxed{
\text{Where are the statistically significant price zones?}
}
\]

followed by:

\[
\boxed{
P(\text{touch}),\
P(\text{hold}),\
P(\text{break}),\
E[\text{reaction}]
}
\]

conditioned on:

\[
\boxed{
\text{price structure}
+\text{volume}
+\text{VWAP}
+\text{options}
+\text{order flow}
+\text{market regime}
+\text{sector}
+\text{events}
+\text{text}
}
\]

The first production benchmark should be a statistical + LightGBM + regime + Monte Carlo system. Deep learning, GNNs and LLM-derived features should have to demonstrate incremental out-of-sample value before being promoted into the production ensemble.

The resulting research hierarchy is:

**simple baseline → statistical model → ML → deep learning → multimodal ensemble → calibrated probability forecast → economic validation.**
