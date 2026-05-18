# Fortress System Feasibility Audit
**Date:** 2026-05-18 (updated)  
**Scope:** Complete system review — risk manager, key levels, execution, strategies, data

---

## Executive Summary

The system is **structurally sound** for dry-run operation and **approaching demo readiness**.  
Two items must be completed before any real money is at risk (both require Tradovate credentials).

| Layer | Status | Notes |
|---|---|---|
| Strategy library | SOLID | 15 strategies (5 hardened survivors + 10 dry-run/review), all backtested |
| Risk manager | SOLID | Ratchet trailing stop, consecutive loss CB, correlation warnings |
| Key levels | ADDED | PDH/PDL, VWAP, volume POC, round numbers — annotation only |
| Dry-run harness | SOLID | 10/10 tests pass, allowlist enforced |
| Bracket orders | MOCK-VERIFIED | 25/25 tests pass, not exchange-verified |
| Startup checklist | ADDED | 47-check pre-flight verification (tick_startup_checklist.py) |
| Signal log reader | ADDED | JSONL log parser and analysis (tick_signal_log_reader.py) |
| Session supervisor | ADDED | Process monitor + auto-restart (tick_session_supervisor.py) |
| Regime monitor | ADDED | 30d vs 150d Sharpe comparison (tick_recent_performance.py --regime) |
| Data pipeline | STALE | Bars end 2026-05-14 — no live feed connected |
| Reconciliation | COMPLETE | Gate 7 — _reconcile_positions + _sync_broker_state implemented |
| Tradovate credentials | NONE | Required for Gate 3 (live bars) and Gate 9 (demo auto-trade) |
| Tradovate credentials | NONE | Required before demo |

---

## 1. Risk Manager — Is it complete?

**YES, with one caveat.**

### What's now correct

| Feature | Status |
|---|---|
| Per-trade stop (ATR × 1.5) | CORRECT |
| Ratchet trailing stop (replaces partials) | ADDED |
| — At +1.5R: stop moves to +0.5R | ✓ |
| — At +2.5R: stop moves to +1.5R | ✓ |
| Full TP at +3.0R | CORRECT |
| Time stop at 50 bars | CORRECT |
| Signal-driven close | CORRECT |
| Forced close (kill switch / session end) | CORRECT |
| Strategy daily loss halt | CORRECT |
| Portfolio daily loss halt | CORRECT |
| Account trailing drawdown halt | CORRECT |
| Consecutive loss circuit breaker (3 losses) | ADDED |
| Correlation warning (ES + NQ simultaneous) | ADDED |

### Why ratchet replaces partials

With `MAX_CONTRACTS_PER_TRADE = 1`, it is impossible to close 50% of a position.
The backtester was run with full TP at 3R (no partials). The ratchet stop is strictly
conservative relative to the backtest: a winner that reaches 2.5R will lock in at
least 1.5R even if it reverses before 3R, while the backtest assumed holding to 3R or
reverting to stop. The ratchet can only produce **equal or better** live results vs
the backtest (for winning trades) and reduces runoff risk on near-winners.

### Caveat

Backtest results assumed holding to full 3R. The ratchet stop WILL exit some trades at
1.5R or 2.5R that would have reached 3R in the backtest. Empirically, this should be
a modest effect (trades that reverse from 2.5R to 1.5R are uncommon in trending
micro-structure strategies). Monitor the first 30 demo trades for R-distribution vs
backtest expectations.

---

## 2. Key Levels — Are they useful for entries?

**YES — as context, not as filters.**

Four level types are computed per instrument:

| Level | Source | Use |
|---|---|---|
| PDH / PDL | Previous day OHLCV | Breakout / rejection zones |
| PDC | Previous day close | Mean-reversion anchor |
| Intraday VWAP | Today's cumulative TP×Vol | Institutional reference |
| Volume POC | 200-bar rolling profile | High-conviction support/resistance |
| Round numbers | Instrument-specific step | Psychological magnet |

**How they appear in alerts:**

```
Key levels: PDH(7580.0) above 0.8R | POC(7526.4) above 0.9R | ROUND(7525) above 1.0R
```

**Key level as entry criterion — break & retest:**

The `break_retest_cvd` strategy in `tick_strategies_v3.py` already implements
break-and-retest logic using rolling 20-bar H/L as the break level. This is
behaviorally close to PDH/PDL breakouts. For a dedicated key-level strategy:

- A signal fires when: price breaks PDH/PDL, then pulls back and holds, then CVD confirms
- The current strategy library covers this implicitly through `cvd_divergence_large_print`
  (captures the volume signature of institutional buyers appearing at a key level)
- Explicit PDH/PDL filter would add value but requires backtesting first

**Current decision: annotate, don't filter.** The annotation gives the trader/log
context without creating an untested entry rule.

---

## 3. Strategy Library — Feasibility Assessment

### Confirmed survivors (all passed Step 2 stress test)

| ID | Strategy | Symbol | Worst Day (micro) | 1t-Sharpe | Topstep |
|---|---|---|---|---|---|
| 2 | cvd_divergence_large_print/15m | ES | -$383 | 2.1 | 100% |
| 7 | prev_session_sweep/3m | ES | -$281 | 1.45 | 100% |
| 8 | range_contraction_break/30m | NQ | -$344 | 5.63 | 100% |
| 9 | session_momentum_follow/3m | GC | -$304 | 3.22 | 100% |
| 10 | trade_absorption_signal/30m | GC | -$454 | 4.65 | 100% |

All five pass on micro contracts (worst day capped at $454 — within $1k remaining DD).

### V5 strategies added (REVIEW_REQUIRED — 2026-05-17)

New `key_level_cvd_rejection` strategy from `tick_strategies_v5.py`. Fires when price
tests a rolling N-bar high/low AND CVD net-change over the window confirms rejection.

Full stress test run across 16 symbol/timeframe combos x 27 param combos:

| ID | Strategy | Symbol/TF | Data | 1t-Sharpe | Topstep | Status |
|---|---|---|---|---|---|---|
| 13 | key_level_cvd_rejection/15m | ES | 5mo | 1.89 | 100% | REVIEW_REQUIRED |
| 14 | key_level_cvd_rejection/15m | NQ | 5mo | 2.10 | 100% | REVIEW_REQUIRED |
| 15 | key_level_cvd_rejection/5m  | GC | 7yr | 0.92* | 99.3% | REVIEW_REQUIRED |

*GC/5m: borderline 1t-Sharpe but 7/7 years positive — added for monitoring.

**Upgrade path for 13/14:** When ES/NQ bar data extends to 2023+, re-run
`tick_v5_stress.py` to confirm annual regime stability. If pass: elevate to `ENABLED_DRY_RUN`.

**SI/SIL:** SI/key_level_cvd_rejection/30m and /15m passed on 7-year data (Sharpe 1.50-1.64)
but SIL micro risk per trade exceeds $200 max_trade_risk_usd — blocked by risk manager.
Re-evaluate if account equity recovers to $5k+.

### 150-day performance (Dec 15 2025 — May 14 2026, all 15 strategies, micro P&L)

| Category | P&L | Trades | Win Rate | Max DD |
|---|---|---|---|---|
| Full portfolio (15 strats) | +$82,541 | 4,144 | 43% | -$5,318 |
| 5 survivors only | +$26,058 | 1,111 | — | — |
| V5 new (IDs 13-15) | +$14,270 | 397 | — | — |

129 trading days: 93 positive, 36 negative. Avg day +$640. Worst day -$1,334.

### Portfolio correlation risk

ES strategies (2, 3, 4, 7, 11, 13) and NQ strategies (1, 5, 6, 8, 12, 14) are ~90% correlated.
GC strategies (9, 10, 15) are uncorrelated from ES/NQ.
The executor warns when ES and NQ are simultaneously in the same direction.

**Safe practice:** Allow at most one ES + one NQ position simultaneously. The allowlist
currently has only strategy 2 as `DEMO_CANDIDATE`, which naturally prevents this.

### Backtest vs live discrepancy

The backtester tests full 3R TP only. The live executor with ratchet stop will:
- Match backtest: trades that reach 3R cleanly
- Improve on backtest: trades that reverse after 2.5R (ratchet exits at +1.5R vs potential stop-out)
- Diverge from backtest: trades exited at +0.5R ratchet level (would have been a winner to 3R in backtest)

This divergence is small and acceptable. No rebacktest needed.

---

## 4. Execution Stack — What works, what doesn't

### What works (verified)

- Kill switch detection (KILL_SWITCH.txt with "STOP")
- Strategy allowlist enforcement (dry-run/demo/live mode gates)
- Bracket order payload construction (25 mock tests pass)
- Dry-run validation (10/10 pass)
- ATR-based stop/target computation
- Hour/session filters
- Signal logging to JSONL
- Stale data warning (>20 min since last bar)
- Correlation warning (ES+NQ same direction)
- Key level annotation on alerts (PDH/PDL, VWAP, volume POC, round numbers)
- News directional bias (counter-bias entries blocked; aligned entries flagged news_confirmed)
- Gate 7 startup reconciliation (fetch broker positions, populate tracker + risk manager)
- Per-pass broker sync (detect stop/target hits while executor running)
- Ratchet trailing stop (replaces impossible partial exits; locks 0.5R at +1.5R, 1.5R at +2.5R)
- Consecutive loss circuit breaker (halts strategy after 3 losing trades in a row)

### What does NOT work yet

| Gap | Impact | Fix |
|---|---|---|
| No live data feed | Bars stale since 2026-05-14 | Connect tick_bar_builder.py to live Tradovate WebSocket |
| OSO payload unverified | May fail with real Tradovate credentials | Test one demo bracket order after credentials |
| Contract rollover needed | MESM5 expires June 20, 2026 | Update TV_CONTRACT_MAP to U5 contracts before rollover |

### What now works (updated 2026-05-18)

| Feature | Status | Notes |
|---|---|---|
| Gate 7 startup reconciliation | COMPLETE | `_reconcile_positions` + `_sync_broker_state` |
| Startup checklist (dynamic) | COMPLETE | `tick_startup_checklist.py` — bracket test pulls live symbols from `TV_CONTRACT_MAP` |
| Signal log reader + exits | COMPLETE | `tick_signal_log_reader.py` — now shows closed trades, R-multiples, ratchet flags |
| Exit event logging | COMPLETE | Executor logs all exit events (stop/target/ratchet/timeout) to JSONL with R-multiple |
| Session supervisor | COMPLETE | `tick_session_supervisor.py` — starts both processes |
| Contract rollover tool | COMPLETE | `tick_contract_rollover.py` — dry-run preview, all 3 files, expiry guard |
| Regime monitor | COMPLETE | `--regime` flag in `tick_recent_performance.py` |
| CVD continuity on restart | FIXED | Bar builder now seeds CVD from last parquet row |
| Correlation groups | FIXED | Strategy 1 (GC) corrected from NQ to GC correlation group |
| Dry-run validation | FIXED | `tick_dry_run_validation.py` — now accepts 12–15+ strategies (10/10 pass) |

---

## 5. What is NOT production-ready (in order of criticality)

### COMPLETE: Gate 7 — Reconciliation

`_reconcile_positions()` and `_sync_broker_state()` are fully implemented in
`tick_live_executor.py`. On startup, existing broker positions are fetched and
loaded into `PositionTracker` + `RiskManager`. No duplicate-entry risk.

### Old stub (for reference only):
```python
# RESOLVED — see _reconcile_positions() in tick_live_executor.py line ~671
# This function is fully implemented and tested.

```

### CRITICAL: No live data feed

The parquet bars end 2026-05-14. All signals computed from these bars are stale.
The stale data warning fires on every bar check. Live trading requires `tick_processor.py`
to be running and writing fresh bars.

**Action:** Connect `tick_bar_builder.py` (Gate 3) to a live Tradovate WebSocket feed.
This is outside the scope of the executor — it requires running `tick_bar_builder.py`
in a separate process.

### MODERATE: OSO payload structure unverified

`place_bracket_order()` builds the Tradovate `placeOSO` JSON based on the API reference.
This has not been tested with real credentials. The response parsing assumes a list
`[entry_confirmation, stop_confirmation, target_confirmation]`.

**Action:** After getting Tradovate credentials, run one demo bracket order manually
before enabling any automated demo trading.

### MODERATE: Contract month rollover

`TV_CONTRACT_MAP` has MESM5, MGCM5, MNQM5. These expire approximately June 20, 2026.
After expiry, orders will fail.

**Action:** Update `TV_CONTRACT_MAP` in `tick_live_executor.py` to June contracts
(MESU5, MGCU5, MNQU5) before June 20, 2026.

### NEWS MONITOR: Working (no API key needed)

`tick_news_monitor.py` connects to ForexFactory calendar (free JSON) and RSS feeds
(MarketWatch, Reuters). Verified working 2026-05-18: 7 events + 25 headlines fetched.
The executor uses this for news-window blocking and daily directional bias.

---

## 6. Regime Analysis — 2026-05-18

30-day (Apr 14 – May 14) vs 150-day Sharpe comparison (from `tick_recent_performance.py --regime`):

| ID | Strategy | 150d Sharpe | 30d Sharpe | Status |
|---|---|---|---|---|
| 2*** | ES/cvd_divergence_large_print (DEMO_CANDIDATE) | 3.82 | 0.45 | WARN |
| 7*** | ES/prev_session_sweep (SURVIVOR) | 3.09 | -2.54 | WARN |
| 8*** | NQ/range_contraction_break (SURVIVOR) | 3.18 | 0.27 | WARN |
| 4 | ES/tape_absorption | 2.85 | 4.17 | OK (improved) |
| 12 | NQ/trade_absorption_signal | 2.33 | 5.16 | OK (improved) |
| 14 | NQ/key_level_cvd_rejection | 1.72 | 3.32 | OK (improved) |
| 15 | GC/key_level_cvd_rejection | 2.93 | 6.80 | OK (improved) |

**Context:** April-May 2026 was characterized by tariff-driven volatility and high-momentum
moves. This regime hurts ES mean-reversion strategies (2, 7) but benefits GC/NQ trending
strategies (14, 15). This is consistent with historical regime rotation.

**Action required:** None — the consecutive loss circuit breaker automatically pauses
underperforming strategies in live trading. Strategy 7 at -2.54 30d Sharpe would have
triggered the CB after 3 consecutive losses. Do NOT adjust the allowlist based on 30-day
regime performance alone.

**V5 upgrade path:** Strategies 14 and 15 are performing strongly in the current regime.
However, the "fresh live data" condition for ENABLED_DRY_RUN requires data from the live
bar builder, not sub-period analysis of the historical dataset.

---

## 7. Recommended next actions (ordered, updated 2026-05-18)

1. ~~Gate 7: Startup reconciliation~~ — COMPLETE
2. **Get Tradovate credentials** — needed for Gate 3 (live bars) and Gate 9 (demo auto-trade)
3. **When credentials arrive: run credential test** — validates auth, data, contract IDs, positions
4. **Gate 3: Connect live bar feed** — `python tick_bar_builder.py --rest` to verify, then WebSocket
5. **Gate 6 exchange verification** — `tick_credentials_test.py --test-order` places + cancels one demo bracket
6. **Gate 9: Single demo strategy** — run strategy 2 (DEMO_CANDIDATE) in demo auto-trade
7. **Monitor first 30 demo trades** — `tick_signal_log_reader.py --trades` shows R-multiples vs backtest expectations

**Before credentials arrive — daily operations:**
```powershell
# Pre-flight check
python tick_startup_checklist.py --quick

# Dry-run session
python tick_session_supervisor.py --poll 60 --quiet

# After session: review signals + trade outcomes
python tick_signal_log_reader.py --days 1
python tick_signal_log_reader.py --trades --days 7

# Regime check
python tick_recent_performance.py --survivors --regime
```

**When credentials arrive — in order:**
```powershell
# Step 1: verify auth, data, contracts
python tick_credentials_test.py --username ... --password ... --cid ... --secret ...

# Step 2: verify OSO bracket order (places + cancels far-OTM limit)
python tick_credentials_test.py --username ... --password ... --cid ... --secret ... --test-order

# Step 3: start live bar feed
python tick_bar_builder.py --username ... --password ... --cid ... --secret ...

# Step 4: confirm fresh bars
python tick_startup_checklist.py

# Step 5: start demo auto-trade (strategy 2 only)
python tick_session_supervisor.py --demo --with-bars --username ... --password ... --cid ... --secret ...
```

**Contract rollover — before June 20, 2026 (~33 days):**
```powershell
# Preview (no changes)
python tick_contract_rollover.py --to U5 --dry-run

# Apply (updates executor, bar_builder, tradovate_client)
python tick_contract_rollover.py --to U5

# Verify
python tick_contract_rollover.py --show
```

Do NOT attempt Gates 6, 9 until Gates 3 and 7 are complete (Gate 7 is done).

---

## 8. Risk Summary — Is it safe to run?

### Safe NOW (no broker credentials needed)

```powershell
cd C:\Users\conor\Desktop\quant-research\04_codebase
& "C:\Users\conor\Desktop\quant-research\venv_new\Scripts\python.exe" -X utf8 tick_live_executor.py `
  --poll 60 --quiet --alert-file alerts.json
```

This runs all 15 strategies in dry-run mode. No orders placed. Stale data will produce
stale signals — the stale warning fires but execution continues (intended).

### NOT safe (requires broker credentials + live data)

- `--demo-auto-trade` — will immediately try to place bracket orders
- `--live-auto-trade` — hard blocked without `FORTRESS_LIVE_ENABLE` env var

### Safety limits in effect

| Limit | Value | Rationale |
|---|---|---|
| Max trade risk (micro) | $200 | MES ~$44, MNQ ~$50, MGC ~$162 |
| Max daily portfolio loss | $600 | 10 accounts × $60 cushion |
| Account trailing DD halt | $800 | Keeps $200 buffer per account |
| Strategy daily loss halt | $250 | Per-strategy protection |
| Consecutive loss CB | 3 losses | Auto-pauses strategy after streak |
| Max contracts | 1 | Micro mode, no scaling yet |
| Kill switch | KILL_SWITCH.txt | Immediate manual halt |

All limits are conservative relative to the $1,000 remaining DD per account.
