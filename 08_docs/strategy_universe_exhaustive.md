# Fortress System — Exhaustive Strategy Universe

**Last updated:** 2026-06-03
**System:** Futures microstructure + trend/event hybrid
**Traded instruments:** GC, MGC, SI, SIL, ES, MES, NQ, MNQ, CL, MCL
**Available data:** mbp-10 (L2 depth), trades, OHLCV bars at 1m / 3m / 5m / 15m / 30m
**Backtested to date:** 345+ individual strategy parameter combos across 17 strategy archetypes

---

## How to Read This Document

Each family section describes the trading thesis, lists specific testable variants, states data requirements, applicable symbols, and known failure modes. Strategies already promoted to the live portfolio or eliminated in backtesting are listed separately in Section 14. All new entries are tracked in `strategy_universe_exhaustive.json`.

**Priority convention:**
- Priority 1 — Test next. High prior probability, data ready, close to proven archetypes.
- Priority 2 — Test after priority 1 wave. Solid thesis, may need minor data work.
- Priority 3 — Longer-term. Data gaps, model complexity, or speculative edge.

---

## Family A — OFI Strategies (Multi-Level, Shock, Decay, Filtered)

### Thesis
Order Flow Imbalance (OFI) measures the net signed change in depth at each price level per bar. When buyers or sellers dominate the order book modifications, price tends to follow the dominant side. The core thesis is already validated by survivors (CVD_VWAP, OFI_Continuation GC). Remaining OFI variants exploit: (1) multi-level alignment for confirmation, (2) temporal dynamics (OFI shock and decay), (3) regime and volatility conditioning, and (4) combination with microprice and spread.

### Variants

| Key | Description |
|-----|-------------|
| `ofi_multi_level_confirmation` | L1+L3+L5 OFI all aligned → higher conviction entry |
| `ofi_shock_post_sweep` | Large OFI spike immediately after a sweep event → trend continuation |
| `ofi_decay_reversal` | OFI declines from extreme back toward zero → fading exhausted momentum |
| `ofi_with_spread_filter` | Only trade OFI signals when bid-ask spread is tight (normal market) |
| `ofi_with_vol_filter` | Gate OFI signals by realized volatility regime (trade only in moderate vol) |
| `ofi_session_conditioned` | OFI signal strength normalized to session context (morning vs afternoon) |
| `ofi_fade_exhaustion` | After N bars of extreme OFI, fade the exhaustion as the move overshoots |
| `ofi_microprice_confirmation` | OFI direction must match microprice trend for entry |
| `ofi_l10_deep_imbalance` | Use all 10 levels of mbp-10 to compute a weighted OFI score |

### Data Requirements
- `mbp-10` schema: required for L3/L5/L10 OFI computation
- `trades` schema: required for sweep detection (post-sweep OFI)
- OHLCV bars: 1m primary, 5m for regime context

### Applicable Symbols
GC, SI primary (L2 data ready). ES, NQ, CL pending L2 data acquisition.

### Known Risks and Failure Modes
- Multi-level OFI columns are computed features — any implementation bug creates invisible lookahead bias.
- OFI percentile thresholds must use expanding or rolling windows anchored strictly to past bars.
- Aligned multi-level OFI may be rarer than expected, producing insufficient trade counts (<150/yr).
- Decay signals require correct identification of the OFI peak bar, which is only known after the fact — use N-bar lookback carefully.
- Spread filter thresholds vary dramatically between GC and SI tick structures.

---

## Family B — Queue / Depth Imbalance

### Thesis
The L2 order book encodes institutional intent through queue size asymmetry. When the bid side of the book holds significantly more size than the ask (or vice versa), price tends to move toward the thinner side as that side offers less resistance. Large passive walls act as both magnets and springboards — price is drawn toward them and then repelled when they hold. Strategies in this family exploit level-specific book shape rather than flow.

### Variants

| Key | Description |
|-----|-------------|
| `depth_wall_rejection` | Price approaches large depth wall and fails; enter fade |
| `depth_wall_pull_breakout` | Large wall disappears (pulled) just before price breaks through |
| `depth_l1_imbalance_momentum` | L1 bid/ask size ratio extreme → momentum entry |
| `depth_l3_weighted_reversal` | Weighted L1-L3 depth skewed heavily one side → fade at extremes |
| `depth_l5_trend_confirm` | L5 imbalance aligns with L1 imbalance → confirmation entry |
| `depth_l10_composite_signal` | Full 10-level weighted imbalance score (more stable than L1 alone) |
| `liquidity_vacuum_continuation` | Ask side near-empty above current price → continuation long |
| `bid_ask_replenishment_cont` | Rapid replenishment of consumed side → defend signal, continue in that direction |

### Data Requirements
- `mbp-10`: level-by-level bid/ask sizes (essential)
- 1m bars with pre-computed `imbal_L*` columns

### Applicable Symbols
GC, SI (L2 bars ready). CL would benefit greatly given its thinner book.

### Known Risks and Failure Modes
- Wall detection requires level-specific size parsing — only aggregate imbalance exists in current bar features; individual level sizes need additional feature engineering.
- Walls are frequently pulled (spoofing) before execution, especially in metals at fast-market times.
- In GC/SI, the book is quoted in multiples of 1 tick; wall thresholds may need symbol-specific calibration.
- Low-volume sessions (overnight) show artificially large imbalance ratios due to thin book.

---

## Family C — Sweep Strategies

### Thesis
A sweep occurs when an aggressive order crosses multiple price levels, consuming available liquidity. Sweeps encode direction conviction: they are expensive to execute and signal that a participant is willing to pay up. Whether the swept side replenishes (absorption) or remains thin (vacuum) determines whether price continues or reverts. This family tests the sweep signal in novel contexts not already covered by the three tested sweep archetypes.

### Variants

| Key | Description |
|-----|-------------|
| `sweep_no_replenishment_continuation` | Sweep + no book replenishment → trend continuation |
| `sweep_prior_hl_reversal` | Sweep of prior session high/low that immediately reverses → fade |
| `sweep_news_fade` | Sweep within 5 minutes of scheduled news → fade the knee-jerk |
| `sweep_vacuum_continuation` | Sweep into thin book (vacuum condition) → acceleration entry |
| `sweep_failed_reversal` | Sweep that fails to trigger reversal after N bars → continuation |
| `sweep_multi_level` | Multiple sequential sweeps across several levels → high-conviction trend |
| `sweep_session_reversal` | Large sweep at RTH open fades after 15-min cooling period |
| `sweep_size_normalized` | Sweep size normalized to recent volume; relative strength signal |

### Data Requirements
- `trades` schema: required for real-time sweep detection
- `mbp-10`: book state after sweep
- 1m bars: `buy_sweeps`, `sell_sweeps`, `sweep_net_size`, `absorption_score`

### Applicable Symbols
GC, SI primary. CL highly relevant due to energy-driven sweep events. ES/NQ require L2 data.

### Known Risks and Failure Modes
- Sweep definition (minimum size, minimum levels crossed) is a free parameter that dramatically changes signal frequency.
- News sweeps require a reliable economic calendar feed aligned to bar timestamps.
- At RTH open, genuine trend sweeps are hard to distinguish from operational noise in the first few bars.
- Sweep detection can double-count if implemented bar-by-bar without deduplication.

---

## Family D — Absorption / Iceberg Proxy

### Thesis
Absorption occurs when large aggressive orders are met by even larger passive hidden orders, preventing price from moving. This suggests a significant institutional participant is accumulating or distributing at a specific price. High trade volume with minimal price movement is the visible signature. Iceberg orders — large orders dripped into the market — leave a characteristic replenishment pattern. This family focuses on identifying these hidden-order phenomena and trading with or against them.

### Variants

| Key | Description |
|-----|-------------|
| `failed_breakout_absorption` | Price breaks level, absorption immediately present → fade the false break |
| `iceberg_proxy_continuation` | Repeated replenishment at same price level → trade with the iceberg direction |
| `iceberg_proxy_reversal` | Iceberg exhausted (stops replenishing) → price free to move; enter reversal |
| `volume_cluster_reversal` | Volume spike with no price progress at key level → fade |
| `cvd_absorption_divergence` | CVD shows buying but price falls (absorbed) → reversal entry |
| `absorption_score_momentum` | High absorption_score in trend direction → trend continuation |
| `aggressive_vol_no_progress` | High `trades_count` with tight price range → fade direction of aggression |
| `multi_bar_absorption` | Absorption_score high across 3+ consecutive bars → institutional accumulation |

### Data Requirements
- `trades`: volume, aggressor side (taker direction)
- `mbp-10`: depth replenishment tracking
- 1m bars: `absorption_score`, `absorption_buy`, `absorption_sell`, `trades_count`

### Applicable Symbols
GC, SI primary. ES at high-volume times (open/close) where iceberg activity is well-documented.

### Known Risks and Failure Modes
- Absorption score proxies from public data are noisy — true hidden order detection requires proprietary flow data.
- Multi-bar absorption detection increases accuracy but reduces trade frequency.
- CVD divergence + absorption is closely related to already-tested CVD_Absorption — must differentiate clearly.
- Iceberg detection at 1m bars may be too coarse; tick-level data is theoretically better but computationally expensive.

---

## Family E — CVD Strategies (Divergence Variants, Event-Anchored)

### Thesis
Cumulative Volume Delta (CVD) tracks the net signed volume (buys minus sells) over the session. When CVD and price diverge — price rises while CVD falls, or vice versa — the price move is not supported by genuine order flow and is likely to reverse. CVD divergence is especially powerful at structural levels (VWAP, session H/L, value area boundaries). Existing survivors already use CVD with VWAP and microprice. This family focuses on untested divergence contexts and event-anchored CVD resets.

### Variants

| Key | Description |
|-----|-------------|
| `cvd_divergence_vwap_explicit` | Price at VWAP + CVD opposing price trend → mean reversion (divergence form) |
| `cvd_divergence_session_hl` | CVD divergence at prior session high/low → reversal |
| `cvd_divergence_value_area` | CVD divergence at Value Area High or Low → mean reversion to VPOC |
| `cvd_trend_continuation` | CVD strongly trending in one direction → continuation entry on pullbacks |
| `cvd_exhaustion_reversal` | CVD reaches multi-session extreme then reverses → mean reversion |
| `cvd_event_anchored` | Reset CVD at FOMC/CPI announcement; track post-event directional flow |
| `cvd_regime_filtered` | Only trade CVD signals when rolling CVD autocorrelation is positive |
| `cvd_rsi_divergence` | CVD-derived RSI diverges from price RSI → dual-indicator divergence |

### Data Requirements
- `trades`: essential for CVD computation (aggressor-side volume)
- 1m bars: `ofi_5` as CVD proxy, `session_vwap`
- Economic calendar: for event-anchored variant

### Applicable Symbols
GC, SI (data ready). CL strongly applicable given its reaction to EIA/OPEC events.

### Known Risks and Failure Modes
- CVD divergence can persist for many bars before resolving — requires tight timeout rules.
- Session CVD reset timing (17:00 ET vs midnight) must be consistent with value area computation.
- Event-anchored CVD requires reliable timestamp alignment between announcement and bar data.
- CVD trend continuation is close to the already-tested CVD_Slope_Regime — must add a distinct element.

---

## Family F — VWAP / Volume Profile

### Thesis
VWAP (Volume Weighted Average Price) is the institutional benchmark for execution quality. Large traders use VWAP as a reference; this creates self-fulfilling price gravity toward VWAP and well-defined rejection behavior at VWAP band extremes. Volume Profile adds the distribution dimension: VPOC (the price with most volume traded) acts as a magnet; Value Area High/Low act as boundaries. Low-volume nodes in the profile represent areas of fast price travel — breakouts through them accelerate.

### Variants

| Key | Description |
|-----|-------------|
| `vwap_reclaim_ofi_confirm` | Price reclaims VWAP after dip + OFI confirms direction → trend continuation |
| `vwap_reject_imbalance` | Price rejects VWAP from below/above + depth imbalance on rejection side → fade |
| `vwap_deviation_cvd_divergence` | Price stretched >1 ATR from VWAP + CVD divergence → mean reversion |
| `vah_val_rejection` | Price touches Value Area High/Low and shows absorption → reversal to VPOC |
| `vpoc_magnet_approach` | Price below VPOC with upward OFI → VPOC target trade |
| `low_volume_node_breakout` | Price enters LVN with high OFI → acceleration through LVN |
| `anchored_vwap_event` | VWAP anchored to FOMC/earnings date acts as support/resistance |
| `vwap_band_fade` | Price beyond 2-std VWAP band + reversal candle → band reversion |
| `vwap_band_breakout` | Price breaks outside 2-std VWAP band with high volume → breakout continuation |

### Data Requirements
- 1m OHLCV bars: for real-time VWAP computation
- `trades`: for volume-weighted calculations
- `mbp-10`: for OFI confirmation overlays
- 5m/15m bars: for higher-timeframe value area computation

### Applicable Symbols
All: GC, SI, ES, NQ, CL. VWAP strategies are instrument-agnostic.

### Known Risks and Failure Modes
- VWAP resets must match CME session boundaries (17:00 ET for futures) — using midnight reset creates wrong VWAP.
- Value Area computation requires at least 4 hours of data; early-session VAH/VAL are unreliable.
- VPOC can shift during the session — using a VPOC computed from current bar is forward-looking.
- Low-volume nodes from prior sessions may not be relevant on high-volume trend days.
- Anchored VWAP requires a clean event timestamp — FOMC decisions can come at irregular times.

---

## Family G — Multi-Timeframe L2

### Thesis
A single-timeframe L2 signal is noisy. Combining a higher-timeframe directional bias with a lower-timeframe precise entry reduces false positives and improves average trade quality. The 30-minute bar captures institutional positioning; the 1-minute bar captures the execution timing; L2 microstructure provides the confirmation trigger. Strategies in this family all require merging two timeframe datasets correctly without lookahead.

### Variants

| Key | Description |
|-----|-------------|
| `mtf_30m_trend_1m_ofi_entry` | 30m trend direction + 1m OFI aligned → enter |
| `mtf_15m_vwap_1m_sweep` | 15m price vs VWAP context + 1m sweep signal → trend trade |
| `mtf_5m_breakout_1m_absorption` | 5m ORB direction + 1m absorption confirms breakout is real |
| `mtf_30m_momentum_1m_depth` | 30m momentum score + 1m depth imbalance extreme → entry |
| `mtf_daily_level_1m_microstructure` | Daily pivot/prior-day HL context + 1m L2 signal at the level |

### Data Requirements
- 1m bars with L2 features (existing GC/SI files)
- 5m, 15m, 30m OHLCV bars (existing for GC/SI)
- Correct merge: 30m bar at time T is the bar that CLOSED before T, not the current bar

### Applicable Symbols
GC, SI (all timeframes available). ES/NQ when L2 bars are built.

### Known Risks and Failure Modes
- Using the current (incomplete) higher-timeframe bar constitutes lookahead — most common bug in MTF strategies.
- When resampling, bar alignment between timeframes must use `pd.merge_asof` with `direction='backward'`.
- Higher-timeframe trend signals can be stale by the time the 1m entry fires.
- Trade count can be low when two independent signals must align simultaneously.

---

## Family H — Cross-Market L2

### Thesis
Futures markets are not independent. GC and SI share precious metals risk; ES and NQ share equity risk with NQ leading at turning points; CL signals risk-on/risk-off sentiment that bleeds into metals. When microstructure signals align across correlated instruments, the probability of a real directional move increases. When they diverge, there is a pair trade or lead-lag opportunity.

### Variants

| Key | Description |
|-----|-------------|
| `gc_si_ofi_confirmation` | Both GC and SI OFI agree direction → stronger signal, enter GC |
| `gc_si_pair_divergence` | GC and SI price diverge from their historical ratio → convergence trade |
| `nq_leads_es_ofi` | NQ OFI fires direction on bar N → ES enters same direction on bar N+1 |
| `cl_risk_regime_metals` | CL trending up (risk-on) → take GC/SI bullish signals; CL down → bearish |
| `es_nq_spread_momentum` | ES-NQ spread widening or narrowing → momentum trade on the lagging leg |
| `gc_cl_safe_haven` | CL drops sharply → GC bid increases → trade GC continuation |

### Data Requirements
- L2 bars for both instruments (GC+SI ready; ES/NQ/CL need L2 data)
- Synchronized 1m bars with matching timestamps
- Pairs data requires careful alignment: use UTC timestamps, not local

### Applicable Symbols
GC+SI pair (data ready). ES+NQ pair (requires L2 build). CL+GC regime (OHLCV available for CL).

### Known Risks and Failure Modes
- ES and NQ L2 data is not yet acquired; estimated cost $30-50 for 6 months.
- Lead-lag relationships are unstable — NQ does not always lead ES; regime detection needed.
- GC-SI ratio is driven by industrial demand shifts that are multi-week, not intraday.
- Cross-market join on timestamp is sensitive to bar boundary differences between instruments.
- Correlation during stress periods can invert (flight to quality in GC while SI sells off).

---

## Family I — Calendar / News / Event

### Thesis
Scheduled economic events create predictable pre-event positioning and post-event directional flow. FOMC announcements create drift in the hours before the decision. CPI and NFP create large, fast directional moves in metals and equity index futures. EIA crude oil inventory reports create reliable CL volatility. By conditioning entries on the event calendar, strategies can exploit the known temporal clustering of institutional activity.

### Variants

| Key | Description |
|-----|-------------|
| `fomc_drift_gc` | Pre-FOMC drift: GC tends to rally/sell toward FOMC expectation in hours before |
| `fomc_post_announcement_momentum` | Post-FOMC directional momentum in first 5 minutes after announcement |
| `cpi_reaction_momentum` | CPI print vs estimate: surprise direction momentum in GC, ES, NQ |
| `nfp_first_minute_reversal` | NFP creates spike that often reverses within 15 minutes → fade signal |
| `eia_crude_momentum` | EIA inventory surprise drives CL in one direction for 30-60 minutes |
| `month_end_positioning` | Last 2 days of month: institutional rebalancing creates predictable metal flows |
| `options_expiry_magnet` | Options expiration Friday: price gravitates toward max pain/pin strike |
| `quarterly_roll_drift` | Contract roll week: active contract thin, calendar spreads cause drift |

### Data Requirements
- OHLCV bars (1m, 5m): for event-reaction entry/exit timing
- Economic calendar CSV: announcement timestamps, expected vs actual values
- No L2 data strictly required, but OFI confirmation improves accuracy

### Applicable Symbols
GC, SI (FOMC, CPI, NFP). CL (EIA). ES, NQ (FOMC, NFP, CPI). Month-end: all instruments.

### Known Risks and Failure Modes
- Economic calendar data must be from a real-time feed — using next-day revised numbers is lookahead.
- FOMC drift is a known effect but has been diminishing as it has been arbitraged by algos.
- NFP reversal timing is highly variable; 5-minute window may be too tight or too wide.
- Month-end effects require multi-year data to have statistical power.
- Options expiry magnet requires options data (not currently in system) to compute max pain.

---

## Family J — Trend Following

### Thesis
Price momentum is one of the most robust documented return premia across asset classes. For futures, trend-following via channel breakouts, ATR-based trailing stops, and momentum signals has demonstrated out-of-sample edge. Unlike microstructure strategies, trend following relies on information contained in the OHLCV bar sequence alone, making it fully implementable with existing data. Its edge is diversifying: it is approximately uncorrelated to the L2 strategies already in portfolio.

### Variants

| Key | Description |
|-----|-------------|
| `keltner_channel_breakout` | Price closes outside Keltner channel → trend entry in breakout direction |
| `atr_channel_trailing` | Enter on ATR-multiple breakout, trail stop using ATR multiple |
| `rolling_return_momentum` | N-bar return z-score above threshold → momentum continuation |
| `dual_ma_crossover` | Fast MA crosses above slow MA → trend entry |
| `adx_trend_strength_filter` | Only enter trend trades when ADX > 25 (confirmed trend environment) |

### Data Requirements
- OHLCV bars (5m, 15m, 30m): ATR, MA, channel computations
- No L2 data required — makes this a true complement to L2 strategies

### Applicable Symbols
All: GC, SI, ES, NQ, CL. Trend following is instrument-agnostic.

### Known Risks and Failure Modes
- GC and SI have well-documented choppy intraday periods where trend following fails.
- Dual MA crossover overfits easily — walk-forward validation essential.
- ATR-based stops require sufficient ATR history; unstable at start of data.
- Trend strategies can have large drawdowns during mean-reverting regimes.
- Commission + slippage erosion is higher for trend strategies due to lower win rate.

---

## Family K — Mean Reversion

### Thesis
In the absence of trend, prices oscillate around a fair value. Intraday mean reversion exploits overshoot: after a rapid price move away from a statistical anchor (VWAP, rolling mean, Bollinger Band), price tends to snap back. This is distinct from VWAP strategies in that it uses price-only statistical signals rather than volume profile context.

### Variants

| Key | Description |
|-----|-------------|
| `rsi_2_extreme_reversal` | RSI-2 < 10 → long; RSI-2 > 90 → short; rapid mean reversion signal |
| `zscore_price_reversion` | Rolling 20-bar z-score extreme → fade entry |
| `bollinger_band_squeeze_reversal` | Price at band extreme during low-volatility (squeeze) period → fade |
| `failed_breakout_reversion` | Price breaks N-bar high/low then closes back inside → fade |
| `roc_exhaustion_reversal` | Rate of change extreme then decelerating → momentum exhaustion fade |

### Data Requirements
- OHLCV bars (1m, 5m): RSI, Bollinger, z-score computation
- Existing proven variant: `bollinger_rsi_gc` is a survivor — these are extensions

### Applicable Symbols
GC, SI, ES, NQ, CL. Mean reversion works best in liquid, range-bound instruments.

### Known Risks and Failure Modes
- RSI-2 is highly sensitive to bar count; needs careful parameter stability testing.
- Z-score reversion can suffer from trending regimes where z-score extremes continue.
- Bollinger Band squeeze requires volatility contraction detection — misidentifying the squeeze is a frequent failure.
- Failed breakout reversion is closely correlated to session HL sweep reversal (already tested).

---

## Family L — Opening Range Breakout / Session

### Thesis
The opening range — the high and low established in the first N minutes of regular trading — acts as a decision boundary for the session. Breakouts of this range have documented edge in equity futures and commodities. Strategies in this family trade the first directional commitment of institutional participants. The London Open variant exploits the European session open as a secondary decision point for metals.

### Variants

| Key | Description |
|-----|-------------|
| `orb_5m_rth` | 5-minute opening range; RTH open at 09:30 ET; enter first breakout |
| `orb_15m_rth` | 15-minute opening range; wider range filter reduces false breakouts |
| `orb_30m_rth` | 30-minute opening range; high probability directional conviction |
| `london_open_breakout_metals` | GC/SI London open (08:20 ET); European metals session breakout |
| `overnight_hl_sweep_fade` | Price sweeps overnight high/low at RTH open then reverses → fade |
| `asian_session_range_breakout` | Define Asian session range (23:00-06:00 ET); break at London open |

### Data Requirements
- 1m or 5m OHLCV bars: for range definition and entry timing
- Session boundary definitions: RTH, London, Asian (per instrument)
- Existing proven variant: `london_open_breakout` (OHLCV data); `fomc_drift` (calendar)

### Applicable Symbols
GC, SI: London open most relevant. ES, NQ: RTH opening range classic. CL: has its own pit session dynamics.

### Known Risks and Failure Modes
- Opening range must be defined from strictly past bars; the range-defining bars must be fully closed.
- High-impact news at 08:30 ET (CPI, NFP) can violate the opening range in both directions — news filter essential.
- Range too narrow (low-volatility open) or too wide (news open) requires adaptive filters.
- Overnight sweep fade requires identifying the overnight high/low correctly — data continuity at roll is critical.

---

## Family M — ML / Meta-Labeling (Future)

### Thesis
Machine learning is not a strategy — it is a framework for combining signals, labeling regimes, and calibrating probabilities. Meta-labeling (Lopez de Prado) uses a primary model to generate entry signals and a secondary ML model to filter which entries to actually take. Regime classifiers identify the market state (trending, mean-reverting, choppy) allowing conditional routing of strategies. These approaches sit on top of the existing strategy universe, not beside it.

### Variants

| Key | Description |
|-----|-------------|
| `metalabel_ofi_continuation` | Primary: OFI_Continuation; Secondary ML: probability filter for high-conviction setups |
| `regime_classifier_hmm` | Hidden Markov Model regime classifier → route strategies by state |
| `probability_threshold_filter` | Logistic regression on feature set → only trade when P(win) > threshold |
| `ensemble_strategy_router` | Ensemble of base strategies; ML model selects which strategy to run each session |

### Data Requirements
- Full feature matrix from L2 bars: `ofi_*`, `cvd_*`, `absorption_*`, `imbal_*`, `sweep_*`
- Historical labeled trade outcomes for training
- Walk-forward train/test split required; no expanding window lookahead

### Applicable Symbols
GC, SI (training data exists). Extend to other instruments when L2 bars are built.

### Known Risks and Failure Modes
- Meta-labeling requires sufficient trades in the primary model — minimum ~500 labeled examples.
- Regime classifiers trained on the same data as the strategies they filter will overfit.
- Feature engineering must be frozen before training; any feature that uses future bars is silent lookahead.
- ML strategies are harder to audit for live deployment; explainability requirements are higher.
- Model decay: ML edge deteriorates faster than rule-based edge as market microstructure evolves.

---

## Family N — Existing Proven Survivors (Summary)

These five strategies have passed Step 1 conservative backtesting and Step 2 stress testing. They are the current portfolio core and are NOT in the backlog.

| Strategy | Symbol | DSR | Status |
|----------|--------|-----|--------|
| CVD_VWAP | GC | High | Portfolio live |
| CVD_VWAP | SI | High | Portfolio live |
| CVD_Microprice | SI | High | Portfolio live |
| OFI_Continuation | GC | Positive | Portfolio live |
| Sweep_Absorption_Reversal | GC | Positive | Portfolio live |

**Portfolio-level results (Phase 4):** DSR +8.84, 10/12 positive years, Topstep PASS, correlations near zero across pairs.

**Pre-live gate (completed):** Monte Carlo P(DD breach) = 0.15%, 82% monthly win rate, unit sizing confirmed.

---

## Appendix: Already Tested (Eliminated or Promoted)

The following 17+ strategy archetypes have completed backtesting and are not eligible for re-testing without a meaningful structural change to the hypothesis:

1. OFI_Continuation (GC survivor, SI tested)
2. OFI_Reversal (tested, eliminated)
3. OFI_Microprice (tested, eliminated)
4. Sweep_Continuation (tested, eliminated)
5. Sweep_Absorption_Reversal (GC survivor, SI eliminated)
6. SessionHighLow_Sweep_Reversal (tested, eliminated)
7. Absorption_Reversal (tested, eliminated)
8. CVD_Absorption (tested, eliminated)
9. Repeated_Replenishment (tested, eliminated)
10. CVD_Microprice (SI survivor, GC tested)
11. CVD_Slope_Regime (tested, eliminated)
12. CVD_Acceleration (tested, eliminated)
13. CVD_VWAP (GC survivor, SI survivor — top performers)
14. Depth_Imbalance_Momentum (tested, eliminated)
15. Depth_Imbalance_MeanRev (tested, eliminated)
16. MultiTF_OFI (tested, eliminated — overtrading bug fixed but edge insufficient)
17. VWAP_Reclaim (GC/SI survivors — promoted to portfolio)
18. Bollinger_RSI (GC survivor)
19. Donchian_Breakout (CL survivor)
20. FOMC_Drift (tested)

---

## Testing Priority Queue

Based on prior probability of edge and data readiness:

**Immediate (data ready, high prior):**
1. `ofi_multi_level_confirmation` — close to proven OFI family
2. `vwap_reject_imbalance` — extends proven VWAP survivors
3. `cvd_divergence_vwap_explicit` — divergence form of top survivor
4. `sweep_no_replenishment_continuation` — novel sweep variant
5. `failed_breakout_absorption` — clean hypothesis, data ready

**Next wave (data ready, moderate prior):**
6. `mtf_30m_trend_1m_ofi_entry` — multi-TF context for OFI
7. `orb_15m_rth` — session structure, OHLCV only
8. `depth_wall_rejection` — requires level-specific feature engineering
9. `cpi_reaction_momentum` — needs calendar feed

**Future (data gaps or model complexity):**
10. `nq_leads_es_ofi` — requires ES/NQ L2 data (~$40 cost)
11. `metalabel_ofi_continuation` — requires labeled outcome dataset
12. `options_expiry_magnet` — requires options data
