# True L2 Feature Engine Design
**Date:** 2026-05-18  
**Status:** DESIGN DOCUMENT — Build feature engine before new strategies

---

## 1. Problem Statement

The current system uses aggregated L2/tick/order-flow bars. These are constructed by sampling best-bid/ask, CVD, and approximate depth at fixed intervals. This is not the same as processing event-level L2 order book data.

Limitations of the current approach:
- CVD is computed from trade direction heuristics, not raw aggressor flags
- Order flow imbalance (OFI) is approximated, not event-driven
- Sweep detection is absent
- Absorption detection is absent
- Book depth dynamics (replenishment, cancellation, pull rate) are not captured
- Quality/safety checks (stale book, missing events, abnormal spread) are not implemented

The next system should separate feature engineering from strategy logic entirely. Features must be reusable across backtest and live, timestamp-safe, and independently testable.

---

## 2. Architecture

```
04_codebase/src/l2/
├── __init__.py
├── schemas.py           — TypedDicts / dataclasses for all feature outputs
├── feature_engine.py    — Orchestrator: takes event stream → returns FeatureSnapshot
├── ofi.py               — Order flow imbalance features
├── imbalance.py         — Depth imbalance features
├── microprice.py        — Price/book state features
├── sweeps.py            — Sweep detection and classification
├── absorption.py        — Absorption detection features
├── liquidity_walls.py   — Liquidity wall features
├── quality_checks.py    — Stale book, missing data, abnormal conditions
└── tests/
    ├── test_ofi.py
    ├── test_imbalance.py
    ├── test_microprice.py
    ├── test_sweeps.py
    ├── test_absorption.py
    ├── test_liquidity_walls.py
    ├── test_quality_checks.py
    └── fixtures/           — Recorded L2 event sequences for replay tests
```

### Core Interface

```python
# schemas.py
from typing import Optional
from dataclasses import dataclass

@dataclass
class L2Event:
    """A single L2 order book update or trade event."""
    timestamp: float          # Unix epoch seconds (nanosecond precision float)
    event_type: str           # "quote" | "trade" | "book_update"
    symbol: str
    bid: Optional[float]
    ask: Optional[float]
    bid_size: Optional[int]
    ask_size: Optional[int]
    bid_levels: Optional[list]  # [(price, size), ...] up to 10 levels
    ask_levels: Optional[list]
    trade_price: Optional[float]
    trade_size: Optional[int]
    trade_side: Optional[str]   # "buy" | "sell" | None (unknown aggressor)

@dataclass
class FeatureSnapshot:
    """All features for a single moment in time. All Optional — never assume populated."""
    timestamp: float
    symbol: str
    # Price/book state
    best_bid: Optional[float] = None
    best_ask: Optional[float] = None
    midprice: Optional[float] = None
    weighted_midprice: Optional[float] = None
    microprice: Optional[float] = None
    spread: Optional[float] = None
    spread_zscore: Optional[float] = None
    tick_normalized_spread: Optional[float] = None
    book_slope: Optional[float] = None
    book_pressure: Optional[float] = None
    # Depth imbalance
    depth_imbalance_l1: Optional[float] = None
    depth_imbalance_l3: Optional[float] = None
    depth_imbalance_l5: Optional[float] = None
    depth_imbalance_l10: Optional[float] = None
    weighted_depth_imbalance: Optional[float] = None
    near_touch_depth: Optional[float] = None
    far_depth_imbalance: Optional[float] = None
    bid_depth_decay: Optional[float] = None
    ask_depth_decay: Optional[float] = None
    # OFI (multiple windows)
    ofi_1s: Optional[float] = None
    ofi_5s: Optional[float] = None
    ofi_15s: Optional[float] = None
    ofi_60s: Optional[float] = None
    ofi_multi_level: Optional[float] = None
    ofi_norm_depth: Optional[float] = None
    ofi_norm_vol: Optional[float] = None
    ofi_zscore: Optional[float] = None
    ofi_slope: Optional[float] = None
    ofi_shock: Optional[float] = None
    ofi_decay: Optional[float] = None
    # Trade flow
    agg_buy_vol: Optional[float] = None
    agg_sell_vol: Optional[float] = None
    cvd_1s: Optional[float] = None
    cvd_5s: Optional[float] = None
    cvd_15s: Optional[float] = None
    cvd_60s: Optional[float] = None
    cvd_slope: Optional[float] = None
    cvd_zscore: Optional[float] = None
    large_trade_clusters: Optional[int] = None
    trade_size_pct: Optional[float] = None
    avg_buy_size: Optional[float] = None
    avg_sell_size: Optional[float] = None
    trade_count_intensity: Optional[float] = None
    trade_to_quote_ratio: Optional[float] = None
    # Liquidity dynamics
    depth_replenish_rate: Optional[float] = None
    depth_cancel_rate: Optional[float] = None
    liquidity_pull_rate: Optional[float] = None
    quote_update_intensity: Optional[float] = None
    book_flicker_score: Optional[float] = None
    liq_wall_distance: Optional[float] = None
    liq_wall_size: Optional[float] = None
    liq_wall_persistence: Optional[float] = None
    liq_wall_pull_event: Optional[bool] = None
    liq_vacuum_score: Optional[float] = None
    # Sweeps
    sweep_detected: Optional[bool] = None
    sweep_side: Optional[str] = None
    sweep_depth: Optional[float] = None
    sweep_volume: Optional[float] = None
    sweep_recovery_time: Optional[float] = None
    sweep_continuation_score: Optional[float] = None
    sweep_failure_score: Optional[float] = None
    sweep_into_high_low: Optional[bool] = None
    sweep_into_vwap: Optional[bool] = None
    # Absorption
    absorption_score: Optional[float] = None
    buy_absorption: Optional[float] = None
    sell_absorption: Optional[float] = None
    repeated_replenish_at_px: Optional[bool] = None
    price_stuck_despite_vol: Optional[bool] = None
    iceberg_proxy: Optional[bool] = None
    cvd_divergence_at_level: Optional[float] = None
    absorption_reversal_trigger: Optional[bool] = None
    # Quality
    stale_book: Optional[bool] = None
    missing_data: Optional[bool] = None
    duplicate_event: Optional[bool] = None
    latency_gap: Optional[bool] = None
    abnormal_spread: Optional[bool] = None
    low_liquidity: Optional[bool] = None
    rollover_warning: Optional[bool] = None
    session_closed: Optional[bool] = None
```

---

## 3. Feature Category Specifications

### 3.1 Price / Book State (`microprice.py`)

| Feature | Definition | Lookback |
|---|---|---|
| `best_bid` | Top-of-book bid price | Current snapshot |
| `best_ask` | Top-of-book ask price | Current snapshot |
| `midprice` | (bid + ask) / 2 | Current snapshot |
| `weighted_midprice` | (bid*ask_size + ask*bid_size) / (bid_size+ask_size) | Current snapshot |
| `microprice` | bid + spread * (bid_size / (bid_size + ask_size)) | Current snapshot |
| `spread` | ask - bid in ticks | Current snapshot |
| `spread_zscore` | (spread - mean_spread) / std_spread | Rolling 60s window |
| `tick_normalized_spread` | spread / instrument_tick_size | Current snapshot |
| `book_slope` | Linear slope of cumulative depth vs price levels | L5 depth snapshot |
| `book_pressure` | Weighted sum of bid sizes above vs ask sizes below midprice | L5 depth snapshot |

### 3.2 Depth Imbalance (`imbalance.py`)

```
depth_imbalance_LN = (sum_bid_N - sum_ask_N) / (sum_bid_N + sum_ask_N)
```
Where N = 1, 3, 5, 10 levels. Range: [-1.0, +1.0]. +1 = all bids, -1 = all asks.

| Feature | Definition |
|---|---|
| `weighted_depth_imbalance` | Size-weighted depth imbalance across all available levels |
| `near_touch_depth` | Total size at best bid + best ask |
| `far_depth_imbalance` | Imbalance at levels 6-10 only |
| `bid_depth_decay` | Rate of size decrease from L1 → L5 on bid side |
| `ask_depth_decay` | Rate of size decrease from L1 → L5 on ask side |

**MBO fallback:** If true MBO data is unavailable, queue position estimated as average size at level. This is conservative — do not overfit to MBO-dependent features unless MBO feed is confirmed.

### 3.3 Order Flow Imbalance (`ofi.py`)

OFI measures the net change in order flow pressure:
```
OFI_t = delta_bid_size_if_price_unchanged + trade_buy_size - trade_sell_size - delta_ask_size_if_price_unchanged
```

Multi-window variants are computed over rolling windows of 1s, 5s, 15s, 60s. Each normalised by:
- Depth: `OFI / (bid_size + ask_size)`
- Volatility: `OFI / rolling_px_std`
- Z-score: `(OFI - mean_OFI_30s) / std_OFI_30s`

Slope, shock, and decay features:
- `ofi_slope`: linear regression of OFI values over last 10 events
- `ofi_shock`: z-score of current OFI vs last 30s (single-event spike detector)
- `ofi_decay`: exponential smoothing of OFI magnitude over 5s

### 3.4 Trade Flow (`ofi.py` CVD section)

| Feature | Window | Notes |
|---|---|---|
| `agg_buy_vol` | Rolling 15s | Confirmed buy-aggressor volume |
| `agg_sell_vol` | Rolling 15s | Confirmed sell-aggressor volume |
| `cvd_1s` through `cvd_60s` | 1/5/15/60s | Cumulative volume delta by window |
| `cvd_slope` | 10-event regression | Direction and acceleration |
| `cvd_zscore` | 60s rolling | Normalised deviation |
| `large_trade_clusters` | 15s window | Count of trades > 90th percentile size |
| `trade_size_pct` | 60s rolling | Current trade size vs distribution |
| `avg_buy_size` | 15s rolling | Mean buy-aggressor size |
| `avg_sell_size` | 15s rolling | Mean sell-aggressor size |
| `trade_count_intensity` | 5s window | Trades per second |
| `trade_to_quote_ratio` | 15s window | Trade events / quote update events |

### 3.5 Liquidity Dynamics (`liquidity_walls.py`)

| Feature | Definition |
|---|---|
| `depth_replenish_rate` | Rate at which cancelled/consumed depth is replaced at same price level |
| `depth_cancel_rate` | Rate of order cancellations per second |
| `liquidity_pull_rate` | Fraction of depth pulled (not traded) in last 5s |
| `quote_update_intensity` | Book update events per second |
| `book_flicker_score` | Count of rapid add/cancel pairs at same price within 1s (spoofing proxy) |
| `liq_wall_distance` | Distance in ticks to nearest large order (>10x median level size) |
| `liq_wall_size` | Size of the nearest large order |
| `liq_wall_persistence` | How many update cycles the wall has survived |
| `liq_wall_pull_event` | Boolean: large order just disappeared without trading |
| `liq_vacuum_score` | Fraction of book empty in 5-tick range above/below current price |

### 3.6 Sweeps (`sweeps.py`)

A sweep is defined as: multiple consecutive trades on the same side hitting successive price levels within T seconds.

Detection parameters:
- Window: 0.5s
- Minimum levels consumed: 2
- Minimum sweep volume: 5x median trade size

| Feature | Definition |
|---|---|
| `sweep_detected` | Boolean: sweep just occurred |
| `sweep_side` | "buy" or "sell" |
| `sweep_depth` | Number of price levels consumed |
| `sweep_volume` | Total volume of sweep |
| `sweep_recovery_time` | Seconds for book to refill to pre-sweep depth |
| `sweep_continuation_score` | Probability of continuation based on prior sweeps |
| `sweep_failure_score` | Probability of reversal based on post-sweep order flow |
| `sweep_into_high_low` | Boolean: sweep touched prior session high/low |
| `sweep_into_vwap` | Boolean: sweep touched VWAP or value area (if available from bar builder) |

### 3.7 Absorption (`absorption.py`)

Absorption is defined as: aggressive volume hitting a level without price movement.

| Feature | Definition |
|---|---|
| `absorption_score` | Composite score: (aggressive_vol_at_px - expected_price_move) / expected_price_move |
| `buy_absorption` | Buy-side absorption strength (large buy vol, price not rising) |
| `sell_absorption` | Sell-side absorption strength (large sell vol, price not falling) |
| `repeated_replenish_at_px` | Boolean: same price level refilled 3+ times in 5s |
| `price_stuck_despite_vol` | Boolean: CVD moved > 2 std but price moved < 1 tick in 5s |
| `iceberg_proxy` | Boolean: size appears to refill instantly after partial consumption |
| `cvd_divergence_at_level` | CVD change / price change ratio (high = strong absorption signal) |
| `absorption_reversal_trigger` | Boolean: absorption score crossed threshold → potential reversal |

### 3.8 Quality and Safety (`quality_checks.py`)

| Feature | Trigger Condition | Action If True |
|---|---|---|
| `stale_book` | No book update in > 2s during RTH | Block feature output |
| `missing_data` | Event sequence gap > 500ms | Flag all features as unreliable |
| `duplicate_event` | Same timestamp + same type as prior event | Drop and log |
| `latency_gap` | Processing timestamp > 500ms after event timestamp | Log, continue |
| `abnormal_spread` | Spread > 5x rolling median | Block entry signals |
| `low_liquidity` | Total L1 depth < 10% of session median | Block entry signals |
| `rollover_warning` | Current contract expires in < 7 days | Log warning |
| `session_closed` | Outside defined RTH + extended hours | Block all entries |

---

## 4. Implementation Requirements

### Timestamp Safety
- Every feature uses only events with `timestamp <= current_event.timestamp`
- Rolling windows defined in seconds, not event counts (event counts vary by session)
- Features computed on event receipt, not bar close

### Backtest / Live Compatibility
- All features implemented as stateful processors with `update(event)` method
- Backtest feeds recorded events in chronological order; live feeds WebSocket events
- State is reset at session open
- No shared global state between instruments

### Unit Tests (per module)
Each module must have tests covering:
1. Empty input → no crash, returns None for all features
2. Single event → only current-snapshot features populated, window features are None
3. Known input sequence → verify exact feature values match hand-computed expected values
4. Out-of-order events → handled gracefully (log and skip, do not corrupt state)
5. Missing side of book (bid only, no ask) → partial features, no crash
6. Session boundary → state resets correctly at session open
7. Stale book detector → triggers correctly at 2s gap

### MBO Fallback
If true MBO (market by order) data is unavailable and only MBP-10 (market by price, 10 levels) is available:
- Queue position is estimated as `size_at_level / typical_order_size_estimate`
- OFI computed from level-size changes, not individual order events
- Mark all OFI features as `approximate=True` in metadata
- Do not use OFI features in strategies without confirming which data source is available

---

## 5. Strategy Families to Build After Feature Engine Exists

Do not build these now. Build the feature engine first and verify all features produce correct values on recorded data.

| # | Strategy Family | Key Features Required |
|---|---|---|
| 1 | OFI continuation | ofi_zscore, ofi_slope, depth_imbalance_l3 |
| 2 | Queue imbalance entry filter | weighted_depth_imbalance, near_touch_depth, microprice |
| 3 | Sweep + no replenishment continuation | sweep_detected, sweep_side, depth_replenish_rate |
| 4 | Sweep + absorption reversal | sweep_detected, absorption_score, absorption_reversal_trigger |
| 5 | Iceberg / replenishment reversal proxy | iceberg_proxy, repeated_replenish_at_px, cvd_divergence_at_level |
| 6 | Liquidity wall magnet / rejection | liq_wall_distance, liq_wall_persistence, liq_wall_pull_event |
| 7 | CVD divergence with book confirmation | cvd_zscore, depth_imbalance_l5, price_stuck_despite_vol |
| 8 | Existing strategy meta-filter | abnormal_spread, stale_book, low_liquidity → veto existing signals |

---

## 6. Build Sequence

1. Define all schemas in `schemas.py` — establish the interface contract first
2. Build `quality_checks.py` — safety gates must exist before any feature computation
3. Build `microprice.py` — basic price/book state, simplest to verify
4. Build `imbalance.py` — depth imbalance features, requires level data
5. Build `ofi.py` — order flow imbalance; most complex, requires clean event stream
6. Build `sweeps.py` — sweep detection; requires consecutive-trade tracking
7. Build `absorption.py` — absorption; requires sweep + OFI + depth
8. Build `liquidity_walls.py` — wall detection; requires depth history
9. Build `feature_engine.py` — orchestrator that wires all modules together
10. Write fixture-based integration tests using recorded L2 data samples

---

*Do not add strategies using these features until the feature engine has full test coverage and has been validated on at least 10 recorded trading sessions.*
