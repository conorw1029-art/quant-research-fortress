Quant Trading Research Factory: Master Strategy Universe + AI Oversight Blueprint
0. Core Principle

The goal is not to “find a magic strategy.” The goal is to build a machine that can safely test thousands of hypotheses while rejecting almost all of them.

Academic evidence supports broad families such as time-series momentum/trend following across futures markets, but even these require careful cost, regime, and overfitting controls. Moskowitz, Ooi, and Pedersen document time-series momentum across equity index, currency, commodity, and bond futures, while Hurst, Ooi, and Pedersen find long-run evidence for trend following across many market environments.

The existing use of Deflated Sharpe Ratio is correct because DSR directly addresses selection bias, multiple testing, and non-normal returns. This is essential because the fortress is intentionally running many trials.

The future L2/order-flow layer is also justified. Databento MBP-10 gives top-ten-level market-by-price updates, including trades, depth, size, and order count, while MBO gives order-level granularity and queue position. These are the correct data types for testing real-time order-flow hypotheses.

1. Strategy Families to Test
A. Existing OHLCV / 1-Minute Strategy Families

These should be tested first because the current system already supports them.

1. Trend Following / Time-Series Momentum

Test across ES/MES, NQ/MNQ, YM/MYM, RTY/M2K, CL/MCL, GC/MGC, SI/SIL, ZN, ZB, 6E/M6E, 6J, 6B, 6A, 6C.

Strategies:

Donchian breakout
Moving-average crossover
Price above/below rolling mean
Rolling return continuation
Volatility-adjusted trend
ATR channel breakout
Keltner channel breakout
20/50/100/200-bar trend continuation
Multi-timeframe trend alignment
Pullback-to-moving-average continuation
Breakout retest continuation
Previous day high/low breakout
Previous week high/low breakout
Session high/low continuation
Volatility-compressed breakout
Trend-following with ADX filter
Trend-following with realized-volatility filter
Trend-following with VIX proxy filter
Trend-following with time-of-day filter
Trend-following with carry/term-structure filter for commodities

Reason: trend following has one of the strongest long-run empirical bases, but the intraday version still needs strict testing because transaction costs can destroy the edge.

2. Mean Reversion / Exhaustion

Strategies:

Bollinger RSI mean reversion
Z-score reversion from VWAP
Z-score reversion from rolling mean
RSI 2-period reversal
RSI divergence reversal
Failed breakout reversal
Previous high/low sweep reversal
Intraday overextension reversal
ATR exhaustion reversal
Consecutive candle exhaustion reversal
Volume climax reversal
Reversion after news spike
Reversion after opening drive
Reversion at prior day high/low
Reversion at previous settlement
Reversion at overnight high/low
Reversion at value area high/low
Reversion at volume point of control
Reversion after large imbalance candle
Reversion only when ADX is low

This family is especially relevant because two of the five current survivors are Bollinger/RSI variants.

3. Opening Range / Session Breakout

Opening-range breakout has documented academic testing in crude oil futures, although robustness varies by time period, which is exactly why the fortress must walk-forward it rather than trust the raw idea.

Test:

5-minute opening range breakout
15-minute opening range breakout
30-minute opening range breakout
60-minute opening range breakout
NY RTH opening range breakout
London open breakout
Asia open breakout
London-NY overlap breakout
US pre-market breakout
Full overnight range breakout
Previous session high/low breakout
Opening range fakeout reversal
Opening range breakout with volume confirmation
Opening range breakout with ATR filter
Opening range breakout with VWAP filter
Opening range breakout only after compressed overnight range
Opening range breakout only after large overnight range
Opening drive continuation
Opening drive fade
Midday range expansion breakout
4. Calendar / Event Strategies

Already started with FOMC. Expand carefully.

The pre-FOMC drift is real in the literature, with Lucca and Moench documenting abnormal equity returns before scheduled FOMC announcements. There is also evidence of price drift before some U.S. macroeconomic news in stock index and Treasury futures, but this varies by event and period.

Test:

FOMC pre-announcement drift
FOMC post-announcement reversal
FOMC statement-day volatility breakout
CPI pre-release positioning
CPI post-release momentum
CPI post-release reversal
NFP pre-release positioning
NFP post-release momentum
NFP post-release reversal
PPI reaction
Retail sales reaction
GDP release reaction
ISM manufacturing reaction
ISM services reaction
Jobless claims reaction
Treasury auction drift
EIA crude oil inventory reaction
Natural gas storage reaction
BOJ event drift in JPY futures
BOE event drift in GBP futures
ECB event drift in EUR futures
Fed minutes drift
Jackson Hole event drift
Quad witching / expiry effects
Month-end rebalance effects
Quarter-end rebalance effects
First trading day of month
Last trading day of month
Options-expiry pin/reversal proxy
Holiday half-day liquidity effects

Important: event strategies must use a proper economic calendar file. No look-ahead. Timestamp everything in ET and UTC.

5. Overnight / Session Inventory Strategies

These fit the new multi-session framework.

Test:

Asia open fade
Asia open breakout
Asia range mean reversion
Asia range breakout into London
London open breakout
London open fakeout
London close reversal
London-NY overlap momentum
London-NY overlap liquidity sweep reversal
US pre-market gap fill
US pre-market trend continuation
Overnight high/low sweep reversal
Overnight inventory rebalance
Full overnight drift
Sunday open gap fill
Monday open continuation
Friday close de-risking
Globex low-liquidity fade
Settlement-window drift
Cash-open futures dislocation
6. VWAP / Volume Profile / Market Profile

These are popular among discretionary futures traders and can be made testable.

Strategies:

VWAP mean reversion
VWAP trend continuation
VWAP reclaim long / reject short
VWAP band fade
VWAP band breakout
Anchored VWAP from session open
Anchored VWAP from event release
Anchored VWAP from prior high/low
Previous day VWAP reclaim
Previous session VWAP rejection
Volume point of control reversion
Value area high rejection
Value area low rejection
Value area breakout
Poor high/poor low repair
Single-print / low-volume-node breakout
High-volume-node magnet
Low-volume-node acceleration
VWAP + delta confirmation once L2/trades exist
VWAP + order book imbalance confirmation
7. Volatility Regime Strategies

Test:

ATR compression breakout
Realized volatility expansion breakout
Low-volatility mean reversion
High-volatility trend continuation
Volatility shock reversal
VIX proxy risk-on/risk-off filter
ES realized-vol filter for all equity strategies
CL realized-vol filter for energy
Gold volatility filter
Treasury volatility filter
News-day volatility breakout
Post-news volatility decay
Intraday volatility seasonality strategy
Avoid first/last 5 minutes filter
Avoid low-volume lunch filter
Trade only top liquidity windows
Trade only when spread proxy is tight
Regime-switching strategy selector
Risk-off detector: ES down + bonds/gold up
Volatility targeting overlay
8. Commodity Carry / Term Structure / Basis

This requires front/deferred contract data, not just continuous OHLCV.

Commodity futures strategies based on momentum and term structure have academic support. Erb and Harvey examine momentum and term-structure strategies in commodity futures, and basis-momentum has also been documented as a predictor of commodity spot and term premiums.

Test later:

Backwardation long / contango short
Roll yield filter
Momentum + backwardation confirmation
Momentum + contango avoidance
Basis momentum
Curve steepening/flattening
Front-vs-second spread momentum
Calendar spread mean reversion
Inventory-sensitive energy strategy
Gold/silver ratio mean reversion
Copper/gold macro regime
Crude crack spread proxy
Soy/corn/wheat relative value if data added
Seasonal commodity tendency filter
Commodity sector rotation
9. Cross-Market / Intermarket Strategies

Test:

ES vs NQ relative momentum
NQ leadership into ES
RTY risk-on confirmation
YM defensive confirmation
ES vs ZN risk-on/risk-off
ES down + ZN up continuation
Gold vs real-rate proxy
Dollar futures vs gold inverse filter
Crude vs CAD futures
EUR vs DXY proxy basket
JPY futures risk-off proxy
Bonds lead equities around macro releases
NQ leads ES during tech-heavy sessions
CL leads inflation-sensitive assets
Gold/silver divergence
Equity index pairs mean reversion
Treasury curve proxy: ZN vs ZB
Micro vs full-size divergence check
Cross-asset volatility filter
Correlation breakdown detector
2. Level 2 / Order-Flow Strategy Universe

This is the biggest future edge area, but also the easiest to fool yourself with. The fortress should use MBP-10 first, then MBO later.

Cont, Kukanov, and Stoikov show that short-horizon price changes are strongly related to order-flow imbalance, and that this relation is more robust than trade volume alone.

Queue imbalance also has documented predictive value for one-tick-ahead price movement in limit order books.

Deep learning models such as DeepLOB use convolutional and recurrent structures to predict price movement from limit order book data, but this should be a late-stage experiment after simple interpretable L2 features are fully tested.

A. MBP-10 Features to Build

From each book update:

Best bid
Best ask
Midprice
Spread
Microprice
Bid size level 1
Ask size level 1
Bid depth levels 1–10
Ask depth levels 1–10
Total bid depth
Total ask depth
Level-1 imbalance
Level-3 imbalance
Level-5 imbalance
Level-10 imbalance
Weighted book imbalance
Book slope bid side
Book slope ask side
Liquidity near touch
Liquidity far from touch
Depth replenishment rate
Depth cancellation rate
Trade aggressor side
Signed trade volume
Cumulative delta
Rolling order-flow imbalance
Rolling trade imbalance
Rolling quote imbalance
Spread widening event
Spread compression event
Depth shock
Liquidity vacuum
Liquidity wall
Sweep event
Replenishment after sweep
Absorption proxy
Failed absorption proxy
Iceberg proxy from repeated replenishment
Spoofing-like pull/stack proxy
Volatility-normalized imbalance

Databento MBP-10 is appropriate here because it includes top-ten book updates, trades, aggregate depth, size, and order count.

B. First L2 Strategy Tests

These are the first ones Claude Code should implement once MBP-10 ingestion exists.

1. Order Book Imbalance Continuation

Signal:

if weighted_bid_ask_imbalance > threshold
and spread <= max_spread
and realized_vol not extreme:
    long for 5s / 10s / 30s / 1min

Variants:

Level-1 imbalance
Level-3 imbalance
Level-5 imbalance
Level-10 imbalance
Weighted imbalance
Imbalance + recent aggressive buy volume
Imbalance + microprice above mid
Imbalance + depth replenishment
Imbalance only during London-NY overlap
Imbalance only during NY open
2. Order Flow Imbalance Momentum

Signal:

OFI = bid_adds - bid_cancels - ask_adds + ask_cancels + signed_trades
if OFI_zscore > threshold:
    long short-horizon

Variants:

1-second OFI
5-second OFI
10-second OFI
30-second OFI
OFI normalized by depth
OFI normalized by volatility
OFI with trend filter
OFI with VWAP filter
OFI after liquidity sweep
OFI around macro events

This family should be prioritized because OFI has strong microstructure support.

3. Queue Imbalance One-Tick Prediction

Signal:

QI = best_bid_size / (best_bid_size + best_ask_size)
if QI > 0.70:
    predict next midprice move up
if QI < 0.30:
    predict next midprice move down

Test:

Next tick
Next 5 seconds
Next 10 seconds
Next 30 seconds
With spread filter
With trade direction filter
With volatility filter
With session filter
With liquidity regime filter
With market-specific thresholds
4. Liquidity Sweep Continuation

Signal:

if aggressive buy volume removes multiple ask levels:
    long continuation unless immediate replenishment appears

Variants:

Sweep one level
Sweep two levels
Sweep three+ levels
Sweep + no replenishment
Sweep + delta confirmation
Sweep + breakout above prior high
Sweep failure reversal
Sweep into high-volume node
Sweep out of low-volume node
Sweep during news event
5. Absorption Reversal

Bookmap and discretionary order-flow traders often discuss absorption and iceberg behavior. Treat this as trader-originated, not proven alpha, then make it testable. Bookmap describes iceberg/hidden-liquidity detection as repeated execution against replenishing liquidity, and suggests combining it with delta divergence, volume imbalance, or liquidity shifts.

Signal:

if aggressive buying is high
but price fails to rise
and ask liquidity replenishes repeatedly:
    short absorption reversal

Variants:

Buy absorption short
Sell absorption long
Absorption at prior high/low
Absorption at VWAP band
Absorption at overnight high/low
Absorption after news spike
Absorption + delta divergence
Absorption + failed breakout
Absorption only in high-liquidity sessions
Absorption only with tight spread
6. Cumulative Delta Divergence

Cumulative delta measures net aggressive buying vs selling pressure. NinjaTrader defines its cumulative delta tool as accumulating volume filled at bid/ask or up/down ticks to compare buy/sell pressure.

Test:

Price higher high, delta lower high: short
Price lower low, delta higher low: long
Delta breakout before price breakout
Delta failure after price breakout
Cumulative delta trend confirmation
Session-reset cumulative delta
Rolling cumulative delta
Delta divergence at VWAP
Delta divergence at prior day high/low
Delta divergence during opening range
7. Liquidity Wall Magnet / Rejection

Test:

Price attracted toward large resting liquidity
Price rejects large resting liquidity
Large bid wall continuation
Large ask wall continuation
Wall pulled before price reaches it
Wall replenished after partial fill
Wall disappears = breakout trigger
Wall near prior high/low
Wall near round number
Wall near VWAP/value area

Important: liquidity can be spoofed or cancelled, so this strategy must include cancellation-rate features and conservative fill assumptions.

8. Spread / Liquidity Regime Filter

Use L2 not only for alpha, but also for when not to trade.

Test:

Do not trade when spread widens
Do not trade when top-of-book depth collapses
Do not trade when cancel rate spikes
Do not trade when quote updates become unstable
Do not trade when book imbalance is too noisy
Reduce size when depth is thin
Increase size only when spread tight and depth stable
Avoid news-window liquidity vacuums
Avoid rollover liquidity distortion
Avoid CME outage/abnormal data periods
9. Market Making / Passive Fill Models

Only later. This requires MBO or very careful queue modeling.

MBO is the right future data source because it gives individual order events and queue position.

Test:

Passive bid/ask quoting around fair value
Inventory-skewed quoting
Queue-position-aware quoting
Join best bid/ask only when imbalance favorable
Pull quote when toxicity rises
Pull quote when OFI turns adverse
Quote wider in high volatility
Quote only during high-liquidity windows
Quote around VWAP fair value
Quote with adverse-selection filter

This should not be attempted until the simulator can model queue position, partial fills, cancellations, and adverse selection.

3. Machine Learning Strategy Families

These must come after robust feature engineering, not before.

A. Interpretable ML First
Logistic regression for next-bar direction
Ridge regression for next return
Random forest classifier
Gradient boosting classifier
XGBoost/LightGBM if installed
Regime classifier
Volatility classifier
Trade/no-trade classifier
Meta-labeling on existing strategy signals
Probability-of-profit filter

Use purged/embargoed validation for ML because ordinary k-fold cross-validation leaks information in financial time series. López de Prado’s financial ML framework explicitly discusses purged k-fold CV and embargo to reduce leakage.

B. Deep Learning Later
CNN on L2 book states
LSTM on order-flow sequences
Transformer on event/order-flow states
DeepLOB-style architecture
Autoencoder regime detection
Contrastive learning on market states
Reinforcement learning for execution only
RL for position management only
RL for strategy selection, not raw trading
Online learning with strict drift controls

DeepLOB-style models are credible research directions, but they are dangerous in a small retail system unless the validation framework is extremely strict.

4. Research Validation Ladder

Every single strategy must pass this ladder.

Stage 0: Hypothesis Card

Each strategy must have:

strategy_key
market
data_required
economic rationale
signal definition
entry rule
exit rule
stop rule
time stop
risk rule
parameter grid
expected failure mode
lookahead risks
cost assumptions
source category: academic / market microstructure / trader folklore / original
Stage 1: Smoke Test

Must verify:

No look-ahead
No future bars used
No timestamp leakage
No session mismatch
No duplicate trades
No broken contract multiplier
No broken tick value
No impossible fills
No empty OOS crash
No unrealistic same-bar execution
Stage 2: Walk-Forward

Use current fortress walk-forward process.

Must output:

OOS trades
DSR
PSR
PF
max drawdown dollars
p-value
number of trades
both halves result
average trade after costs
stability by year/session/regime
Stage 3: Multiple-Testing Penalty

Keep DSR and add PBO / CSCV later.

Bailey et al. propose CSCV to estimate probability of backtest overfitting, and White’s Reality Check is a classic approach for data-snooping adjustment across many technical trading rules.

Add later:

Probability of Backtest Overfitting
CSCV ranking degradation
White Reality Check / bootstrap reality check
False discovery rate by strategy family
Strategy-family-level trial accounting
Stage 4: Conservative Cost Stress

Every survivor must pass:

--cost-scenario realistic
--cost-scenario conservative
--cost-scenario stress

For L2 strategies, also test:

0.5 tick worse fills
1 tick worse fills
2 ticks worse fills
missed fill probability
partial fill model
queue delay
spread widening
latency delay
execution throttling
exchange fee/slippage sensitivity
Stage 5: RiskManager Replay

Before paper trading, replay every survivor through the RiskManager.

RiskManager must include:

Per-trade stop
Daily loss limit
Weekly loss limit
Max consecutive losses
Max open positions
Max correlated exposure
Max contracts per market
Max contracts portfolio-wide
Stop trading after abnormal slippage
Stop trading after data gap
Stop trading after latency spike
Stop trading before/after major news if strategy not event-approved
Topstep max loss limit simulation
Topstep daily loss limit simulation
Consistency target simulation

Topstep currently states that the 50K Trading Combine / Express Funded Account maximum loss limit is $2,000, and its Daily Loss Limit article lists $1,000 for the 50K account when selected at purchase. These rules can change, so the RiskManager should store them in config rather than hard-code them.

5. AI Brain Architecture

The AI brain should not be a mystical “trading oracle.” It should be a controlled research and monitoring system.

A. AI Roles
1. Research Scout

Input:

Academic papers
Market microstructure literature
Official data docs
Futures exchange docs
Quant blogs
Trader forums
Social media claims

Output:

candidate_hypothesis_card.json

Rule: social media ideas are tagged as SOURCE_CONFIDENCE_LOW.

2. Strategy Designer

Converts idea into exact testable spec.

Output:

strategy_spec.yaml

Must define:

Signal
Entry
Exit
Stop
Parameter grid
Data needed
Expected edge
Failure mode
Lookahead traps
Cost sensitivity
3. Code Generator

Claude Code writes implementation, but only on a branch.

Rules:

Never edit zoo manually
Never alter historical results
Never loosen go/no-go rules without explicit approval
Never change costs to rescue a strategy
Never remove failed results
Always add tests
Always commit significant changes
4. Validator

Runs:

Unit tests
Smoke tests
Walk-forward
DSR
Conservative cost stress
Regime stability
RiskManager replay
Portfolio impact

Output:

validation_report.json
5. Risk Sentinel

Runs during paper/live simulation.

Monitors:

Daily loss
Weekly loss
Drawdown
Position size
Correlation exposure
Strategy drift
Trade frequency anomaly
Fill slippage anomaly
Data quality anomaly
Market regime mismatch

Can do:

Reduce size
Disable strategy
Flatten positions
Block new trades
Alert human

Cannot do:

Override hard risk limit
Increase size after losses
Add untested strategy
Trade live without approved config
6. Portfolio Allocator

Takes validated survivors and assigns risk.

Allocation methods to test:

Equal notional
Equal contract
Equal volatility
Equal risk contribution
Drawdown-aware allocation
DSR-weighted allocation
Correlation-penalized allocation
Regime-dependent allocation
Kelly fraction capped at tiny size
Topstep-safe sizing
6. Fortress Workflow After All Testing
Phase 1: Finish Research Expansion
Complete Batch 4
Complete session framework
Add all OHLCV/session strategy families
Run full zoo re-evaluation
Freeze broad OHLCV expansion temporarily
Phase 2: Build RiskManager

Must be integrated into backtest first, not live first.

Files likely needed:

src/risk/risk_manager.py
src/risk/risk_config.py
src/risk/account_state.py
src/risk/position_sizer.py
src/risk/risk_events.py
tests/test_risk_manager.py

RiskManager must support:

can_enter_trade()
size_position()
register_fill()
update_unrealized_pnl()
check_stop_loss()
check_daily_loss()
check_max_drawdown()
check_consecutive_losses()
check_correlation_limits()
force_flatten()
disable_strategy()
Phase 3: Conservative Stress

For each survivor:

realistic cost
conservative cost
stress cost
half-size test
double-cost test
slippage shock
missed-trade test
stop-loss replay
Topstep rule replay
paper-trading eligibility score
Phase 4: Portfolio Construction

Build:

src/portfolio/portfolio_backtest.py
src/portfolio/allocation.py
src/portfolio/correlation.py
src/portfolio/portfolio_metrics.py

Metrics:

Portfolio DSR
Portfolio PF
Max drawdown dollars
Daily loss breach frequency
Weekly loss breach frequency
Best-day contribution
Worst-day contribution
Correlation matrix
Strategy dependency graph
Contribution by market/session/regime
Phase 5: L2 Data Expansion

Order:

Add MBP-10 loader
Build book feature engine
Build L2 event bars
Build L2 simulator
Test simple imbalance strategies
Test OFI strategies
Test sweep/absorption strategies
Add MBO only when queue simulation is needed
Build passive fill model
Only then consider market making
Phase 6: Paper Trading

Paper trading requirements:

Same code path as backtest
Same RiskManager
Same strategy registry
Same cost model
Live logs stored append-only
Every order/fill/event timestamped
Daily report generated
Weekly degradation check
AI brain reviews but does not override risk
Minimum consistency period before live attempt
Phase 7: Live Prop Attempt

Only after:

Survivors pass conservative costs
RiskManager passes replay
Portfolio drawdown is Topstep-safe
Paper trading is consistent
No unresolved execution bugs
No strategy relies on unrealistic fills
Kill switch tested
Monitoring tested
GitHub state committed
Live config locked
7. Immediate Claude Code Prompt

Paste this into Claude Code after the current batch finishes:

We are continuing the Quant Trading Research Factory (“fortress”).

Your next job is NOT to loosen validation or search for shortcuts. Your job is to expand the research universe safely and preserve the statistical honesty of the system.

Current priorities:

1. Finish Batch 4 and run zoo_reevaluate.py.
2. Complete the multi-session strategy framework.
3. Add a structured strategy backlog based on the master taxonomy below.
4. Do not manually delete or edit zoo records.
5. Do not loosen go/no-go rules.
6. Do not trade live.
7. Do not implement L2 strategies until MBP-10 ingestion and an L2 feature engine exist.

Create a new docs file:

08_docs/strategy_universe_master.md

Add the following sections:
- OHLCV strategy families
- Session strategy families
- Calendar/event strategies
- VWAP/volume profile strategies
- Cross-market strategies
- Commodity carry/term-structure strategies
- Level 2 / MBP-10 strategy families
- MBO / queue-position future strategies
- ML/meta-labeling future strategies
- Validation ladder
- RiskManager requirements
- AI brain architecture

Then create a machine-readable backlog file:

08_docs/strategy_backlog.json

Each entry should include:
{
  "key": "",
  "family": "",
  "market_candidates": [],
  "data_required": "OHLCV|MBP10|MBO|EVENTS|TERM_STRUCTURE",
  "source_confidence": "academic|microstructure|platform_docs|trader_folklore|original",
  "rationale": "",
  "entry_logic": "",
  "exit_logic": "",
  "risk_notes": "",
  "lookahead_risks": [],
  "priority": 1
}

Do not implement every strategy yet. First create the backlog and then recommend the top 10 OHLCV/session strategies to implement next, based on compatibility with the current codebase.
8. The Top 10 Strategies I Would Implement Next

Since Claude Code is already running Batch 4/session work, the next ten should use current OHLCV data before L2.

London open breakout
London open fakeout reversal
Asia range breakout into London
US pre-market gap fill
Overnight high/low sweep reversal
VWAP reclaim/reject strategy
Previous day high/low sweep reversal
ATR compression breakout
ADX/ATR regime filter for bollinger_rsi_fxe
Portfolio-level equal-risk combination of the five current survivors

Then move to MBP-10.

The first L2 strategy should be order-flow imbalance momentum, not deep learning, not market making. Start simple, interpretable, and brutal with costs.