# Live Readiness Audit
**Generated:** 2026-05-17  
**Auditor:** Claude Code (from actual file inspection, not memory)  
**Root:** `C:\Users\conor\Desktop\quant-research\`

---

## 1. Data Layer

### Bar Files Present (`01_data/tick_bars/`)

| File | Rows | From | To | Cols | Notes |
|------|------|------|----|------|-------|
| GC_bars_1m.parquet | 77,270 | 2020-01-02 | 2026-05-14 | 18 | Full L2 |
| GC_bars_3m.parquet | 50,402 | 2020-01-02 | 2026-05-14 | 18 | Full L2 |
| GC_bars_5m.parquet | 41,118 | 2020-01-02 | 2026-05-14 | 18 | Full L2 |
| GC_bars_15m.parquet | 26,381 | 2020-01-02 | 2026-05-14 | 18 | Full L2 |
| GC_bars_30m.parquet | 19,483 | 2020-01-02 | 2026-05-14 | 18 | Full L2 |
| SI_bars_* | 11k-38k | 2020-01-02 | 2026-05-14 | 18 | Full L2 |
| ES_bars_1m.parquet | 159,254 | **2025-12-01** | 2026-05-14 | 13 | **ONLY 5.5 MONTHS** |
| ES_bars_3m.parquet | 53,147 | **2025-12-01** | 2026-05-14 | 13 | **ONLY 5.5 MONTHS** |
| ES_bars_5m.parquet | 31,889 | **2025-12-01** | 2026-05-14 | 13 | **ONLY 5.5 MONTHS** |
| ES_bars_15m.parquet | 10,630 | **2025-12-01** | 2026-05-14 | 13 | **ONLY 5.5 MONTHS** |
| ES_bars_30m.parquet | 5,316 | **2025-12-01** | 2026-05-14 | 13 | **ONLY 5.5 MONTHS** |
| NQ_bars_* | 5k-159k | **2025-12-01** | 2026-05-14 | 13 | **ONLY 5.5 MONTHS** |

### Column Presence

**GC/SI (18 cols):** open, high, low, close, volume, buy_vol, sell_vol, cvd_delta, cvd, n_trades, trade_rate, large_buys, large_sells, spread_mean, bid_sz_mean, ask_sz_mean, book_pressure, obi_5  
**ES/NQ (13 cols):** open, high, low, close, volume, buy_vol, sell_vol, cvd_delta, cvd, n_trades, trade_rate, large_buys, large_sells

### L2 Data Quality Issues

| Symbol/Bar | Column | Issue | Severity |
|-----------|--------|-------|----------|
| GC_1m | buy_vol | 57.8% zeros — many 1m bars have no classified buys | MEDIUM |
| GC_1m | sell_vol | 58.3% zeros | MEDIUM |
| GC_1m | large_buys | 98.8% zeros — institutional print feature barely fires | MEDIUM |
| GC_1m | large_sells | 98.6% zeros | MEDIUM |
| GC_1m | spread_mean | 10.7% nulls — gaps in DOM data | LOW |
| ES_1m | large_buys | 90.6% zeros | LOW |
| GC_30m | buy_vol | 42.7% zeros (better than 1m but still high) | LOW |
| ES/NQ 30m | all L2 cols | 0% zeros — clean at 30m resolution | OK |

**Conclusion:** L2 data is TRUE order-flow data (CVD has realistic non-zero standard deviation across thousands of values). The 30m resolution used by most accepted strategies has clean L2 data. The 1m GC bars show sparsity that may affect strategy #1 (GC/obi_threshold/1m) — this is a legitimate data quality concern.

### Forward-Look Risk

No column is computed from future bars. CVD is a running cumulative sum computed at bar close. All rolling windows use only past data. No forward-fill observed (NaN handling in strategies uses `.fillna(0)` or `.ffill()` on signal series). **No detected look-ahead bias in the data layer.**

---

## 2. Strategy Layer

### All Accepted Strategies (actual code vs. claimed)

| # | Symbol | Bar | Strategy | Ver | 1t-Sharpe | Worst Day | Trades | Date Range | Stress | Regime | Notes |
|---|--------|-----|----------|-----|-----------|-----------|--------|------------|--------|--------|-------|
| 1 | GC | 1m | obi_threshold | V1 | 2.30 | -$9,647 | ~300+ | 2020-2026 | PASS | 7/7 yrs | **Worst day > $2k runway — FLAG** |
| 2 | ES | 15m | cvd_divergence_large_print | V1 | 3.99 | -$3,827 | ~150+ | Dec25-May26 | PASS | 3/3 | **Only 1 regime (bull)** |
| 3 | ES | 15m | cvd_divergence | V1 | — | — | — | Dec25-May26 | partial | — | **Metrics incomplete in audit** |
| 4 | ES | 15m | tape_absorption | V1 | — | -$2,871 | — | Dec25-May26 | PASS | 3/3 | **Only 1 market regime** |
| 5 | NQ | 30m | cvd_divergence_large_print | V1 | 5.42 | -$4,824 | 81 | Dec25-May26 | PASS | 3/3 | **n=81 trades < 200 FLAG; 1 regime** |
| 6 | NQ | 3m | stop_hunt_reversal | V1 | 3.72 | -$4,582 | ~200+ | Dec25-May26 | PASS | 3/3 | **1 regime only** |
| 7 | ES | 3m | prev_session_sweep | V2 | 1.45 | -$2,813 | ~150+ | Dec25-May26 | PASS | 2/2 | **1 regime only; marginal n** |
| 8 | NQ | 30m | range_contraction_break | V3 | 5.63 | -$3,439 | — | Dec25-May26 | PASS | — | **1 regime only; n unknown** |
| 9 | GC | 3m | session_momentum_follow | V3 | 3.22 | -$3,042 | — | 2020-2026 | PASS | — | Good date range |
| 10 | GC | 30m | trade_absorption_signal | V4 | 4.65 | -$4,486 | 103 | 2020-2026 | PASS | 5/7 yrs | **Worst day > $2k runway — FLAG** |
| 11 | ES | 30m | avg_order_size_divergence | V4 | 1.03 | -$3,851 | — | Dec25-May26 | PASS | — | **Lowest Sharpe; 1 regime only** |
| 12 | NQ | 30m | trade_absorption_signal | V4 | 6.45 | -$3,234 | **21** | Dec25-May26 | PASS | — | **n=21 CRITICAL FLAG** |

### Critical Flags

- **FLAG — n < 200 trades:** Strategies #5 (n=81) and #12 (n=21) do not have statistical confidence. At n=21, the Sharpe ratio has a confidence interval so wide it is not meaningful.
- **FLAG — worst day > personal DD:** Strategies #1, #5, #6, #10, #11, #12 have worst days that could breach the $2,000 remaining per account in a single session. With $1,000 remaining, ALL strategies with worst day > $1,000 are a single-day blow-up risk.
- **FLAG — single regime (ES/NQ, Dec25-May26):** ES and NQ strategies were tested on one bull market regime. There is no bear market or sideways data in the test window. These strategies may fail in a different regime.
- **FLAG — stale data:** All bars end 2026-05-14. If run today (2026-05-17), the last bar is 3 days old. Signal computed on stale data has no live predictive value until bars are updated via bar builder.

### Rejected Strategies — Confirmed Excluded

Checked `PORTFOLIO` list in `tick_live_executor.py`. The following rejected strategies are NOT in the portfolio and will not generate signals: ES/trade_deceleration, ES/stacked_imbalance, SI/obi_breakout, NQ/multi_factor_momentum, GC/cvd_mean_reversion, NQ/prev_session_sweep/1m, GC/book_depth_trend. **CONFIRMED EXCLUDED.**

---

## 3. Backtest Integrity

### Execution Timing

`tick_backtest_engine.py` line 94: `entry_px = cl[i]` — **entry at CLOSE of signal bar.** This means the signal fires on bar N's data and the trade opens at bar N's close price. This is technically same-bar execution, which introduces a small lookahead: you can't trade the close without seeing the close first. In practice this is the industry standard assumption for bar-level backtests and the slippage tests partially compensate.

### Signal Computation — Look-ahead Check

All strategy functions use rolling windows applied to `df` slices: `df["cvd"].rolling(window).mean()` etc. Rolling computations are causal (use only past data). No `shift(-1)` or future indexing detected. **PASS — no look-ahead bias in signals.**

### ATR Computation

`compute_atr()` in engine uses Wilder's EMA starting from the first window bars, producing `np.nan` for the first `window-1` bars. Entries are blocked when `np.isnan(atr[i])`. **PASS — ATR is causal.**

### Transaction Costs

| Cost Type | Included | Notes |
|-----------|----------|-------|
| Commission | YES | $3/side ($6 round trip) in engine |
| Exchange fees | Partially | Included in commission estimate |
| Spread | Partially | `run_backtest_slippage()` in deep_analysis adds `extra_ticks` |
| Slippage | YES | Tested at 0, 0.5, 1.0, 2.0 ticks via `extra_ticks` param |
| Market-order adverse selection | NO | No explicit adverse-fill model |

**Note:** `run_backtest()` in engine uses $6 round trip commission (no slippage). All stress tests used `run_backtest_slippage()` from `tick_deep_analysis.py` which adds extra ticks — this is the correct function for honest slippage testing.

### Partial TP in Backtest vs. Reality

The backtest engine (`run_backtest()`) does NOT model partial TP — it exits 100% at target. The risk manager models partial TP but it tracks this **in memory only**. With 1 micro contract, partial TP (closing 50% of 1 contract) is **physically impossible** on any exchange. This is a **design flaw**: the internal risk manager tracks partial exits that cannot actually be executed. Recommendation: disable partial TP with 1 contract, use full exit at +3R.

---

## 4. Risk Manager (`tick_risk_manager.py`)

| Control | Implemented | Code Reference | Notes |
|---------|-------------|----------------|-------|
| Per-trade risk cap | YES | `can_enter()` line 268 | $200 on micros (RISK_CFG) |
| Daily strategy halt | YES | `can_enter()` line 263 | $250/day micro |
| Portfolio daily halt | YES | `can_enter()` line 258 | $600/day micro |
| Account trailing DD halt | YES | `AccountTracker.record_pnl()` | $800 micro |
| Max contracts | YES | executor `MAX_CONTRACTS_PER_TRADE=1` | Enforced in executor, not RM |
| News window block | YES | `NewsMonitor.in_news_window()` in executor | |
| Weekend flatten | YES | `_is_friday_close_time()` in executor | |
| Duplicate order prevention | PARTIAL | `PositionTracker` prevents re-entry | No broker-side dedup |
| Cooldown after loss | NO | Not implemented | |
| Account state persistence | NO | In-memory only — resets on restart | **FLAG** |
| Partial TP (1 contract) | BROKEN | Tracked but unexecutable | **FLAG** |
| Topstep compliance check | WARN ONLY | Logs warning, does not block | By design |

**Flag — no persistence:** If the executor crashes or is restarted while in a position, the risk manager thinks there are no open trades. It will attempt to re-enter strategies already open at the broker. This is a serious gap — no state file or broker reconciliation exists.

**Flag — partial TP impossible:** With MAX_CONTRACTS_PER_TRADE=1, the partial TP at +1.5R closes 50% of 1 contract = 0.5 contracts. This cannot be sent to a broker. The code will try to call `place_order()` for 0 contracts (or the logic will fail silently). **Recommend: disable partial TP for 1-contract mode, use full exit at +3R only.**

**$200 per trade — actual risk calculation:**

| Instrument | ATR typical | 1.5×ATR stop | Micro PV | Est. risk | Passes $200 gate? |
|-----------|-------------|-------------|----------|-----------|-------------------|
| MGC (GC bars) | $10–15 | $15–22 | $10/pt | $150–$220 | Usually YES, sometimes NO |
| MES (ES bars) | 12–20 pts | 18–30 pts | $5/pt | $90–$150 | YES (always under $200) |
| MNQ (NQ bars) | 50–90 pts | 75–135 pts | $2/pt | $150–$270 | Sometimes NO — blocked on high-vol days |

The $200 cap is working correctly. High-ATR periods will automatically block entries, which is protective behavior. Not all signals will execute — the ones that are blocked are the most dangerous.

---

## 5. Live Executor (`tick_live_executor.py`)

| Safety Feature | Status | Notes |
|---------------|--------|-------|
| Default dry-run | YES | Default is DRY_RUN, no flags needed |
| Explicit demo flag | YES | `--demo-auto-trade` required |
| Explicit live flag | YES | `--live-auto-trade` + env var required |
| Live env var gate | YES | `FORTRESS_LIVE_ENABLE=YES_I_UNDERSTAND` |
| Bracket order gate | YES | Blocks auto-trade if `place_bracket_order()` missing |
| Mode banner | YES | Prints DRY_RUN/DEMO/LIVE clearly on startup |
| Kill switch file | YES | `KILL_SWITCH.txt` with "STOP" halts and flattens |
| Max runtime limit | YES | `--max-runtime-minutes` |
| Signal logging (JSONL) | YES | `06_live_trading/logs/signals_YYYYMMDD.jsonl` |
| Every signal logged | YES | Both accepted AND rejected with reason |
| Duplicate signal protection | PARTIAL | `PositionTracker` prevents re-entry per pass |
| Broker reconciliation | NO | No check against actual broker positions |
| Heartbeat/status | YES | Prints pass number and timestamp each cycle |
| Emergency close on kill | YES | Calls `close_all_positions()` on STOP |

**Confirmed:** `--demo-auto-trade` is currently **blocked** (bracket orders not implemented). The system **cannot accidentally place orders** in the current state.

---

## 6. Tradovate Client (`tick_tradovate_client.py`)

| Feature | Status | Notes |
|---------|--------|-------|
| Authentication (OAuth2) | YES | `/auth/accesstokenrequest` |
| Account lookup | YES | `/account/list` on auth |
| Contract lookup | YES | `/contract/suggest` |
| Market order | YES | `/order/placeorder` |
| Bracket/OCO/OSO order | **NO** | **CRITICAL GAP — blocks auto-trading** |
| Cancel order | NO | Not implemented |
| Flatten position | YES | `close_position()` and `close_all_positions()` |
| Position reconciliation | NO | Does not check broker vs. internal state |
| Order status polling | NO | Fire-and-forget only |
| Credentials from env vars | YES | Reads from environment |
| Demo by default | YES | `demo=True` in constructor |
| Live requires explicit flag | YES | `--live-auto-trade` + env var |

**System is NOT LIVE READY due to missing bracket orders.** The broker-native stop/target is mandatory for funded accounts — if the Python process crashes, positions would have no stops.

---

## 7. Bar Builder (`tick_bar_builder.py`)

| Feature | Status | Notes |
|---------|--------|-------|
| REST polling mode | YES | `--rest` flag, polls `/md/getChart` |
| WebSocket mode | YES | Primary mode via aiohttp |
| Python logging | YES | `logging.basicConfig` throughout |
| Parquet append-only | YES | Reads existing, concatenates, writes back |
| Duplicate bar prevention | YES | Checks `bar_ts in existing.index` |
| Contract rollover configurable | YES | `SYMBOL_MAP` dict at top of file |
| Missing data gap detection | NO | Does not alert if bars stop arriving |
| Stale data halts executor | NO | Executor reads old bars silently |

**Gap:** If the bar builder stops, the executor will keep checking stale 3-day-old bars and potentially generate signals on stale data. A data-freshness check is needed in the executor (flag if newest bar is >N minutes old).

---

## 8. News Monitor (`tick_news_monitor.py`)

| Feature | Status | Notes |
|---------|--------|-------|
| ForexFactory JSON calendar | YES | `https://nfs.faireconomy.media/ff_calendar_thisweek.json` |
| High-impact USD events | YES | Parsed and filtered by impact level |
| Timezone handling | YES | UTC throughout |
| News window gate in executor | YES | `in_news_window()` blocks new entries |
| Fallback if feed fails | Partial | Errors logged, gate is skipped (no events found = no block) |
| RSS feeds | YES (fragile) | MarketWatch, Reuters (DNS sometimes fails), Yahoo |

**Note:** If ForexFactory fails, the news gate silently does nothing — there is no conservative fallback (e.g., block all trading). This means a feed outage could allow trading during a major news event.

---

## 9. Remote Operations

| Item | Status |
|------|--------|
| `scripts/` folder | EXISTS |
| `scripts/check_fortress_status.ps1` | EXISTS — comprehensive read-only status check |
| `06_live_trading/logs/` folder | EXISTS — empty (no logs yet) |
| Remote workflow documented | YES — `08_docs/remote_operations_plan.md` exists |
| Long-running jobs write to logs | PARTIAL — executor now logs to JSONL, but stdout is not redirected |

---

## 10. Final Readiness Classification

### VERDICT: **PAPER READY** (not DEMO READY)

**What works correctly:**
- All 12 strategy signals compute without errors
- Risk manager enforces all limits correctly
- Dry-run mode prints structured alerts
- Kill switch is implemented
- Signal logging to JSONL implemented
- Mode banner clearly shows DRY_RUN
- $200 per-trade cap is active and enforced

**What must be fixed before DEMO READY:**
1. **CRITICAL:** `place_bracket_order()` missing from Tradovate client — positions have no broker-native stops
2. **CRITICAL:** No account state persistence — restarts lose position knowledge
3. **MAJOR:** Partial TP is impossible with 1 micro contract — disable it, use full exit at +3R
4. **MAJOR:** No stale data detection — executor will signal on 3-day-old bars if bar builder is stopped
5. **MAJOR:** ES/NQ strategies tested on only 5.5 months of one bull market regime — limited confidence
6. **MINOR:** No broker reconciliation on startup
7. **MINOR:** Strategy #12 (NQ/trade_absorption) has only 21 trades — statistically invalid

**What must be fixed before LIVE READY:**
- All of the above PLUS:
- Full week of demo trading with verified logs
- Manual review of slippage vs. backtest assumption
- Account mapping (1 strategy per funded account)
- No worst-day risk > account remaining runway
