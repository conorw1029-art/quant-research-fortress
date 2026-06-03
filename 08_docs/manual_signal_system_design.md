# Manual Signal System — Technical Design

## Overview

The Manual Signal System is the first real-world usable component of the Fortress. It generates trade alerts from L2 bar data and routes them to the trader for manual execution. No broker connection. No orders. Signals only.

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                      DATA LAYER                                     │
│  01_data/tick_bars/                                                 │
│  ┌─────────────────────┐   ┌─────────────────────┐                │
│  │  GC_bars_l2_1m.pq  │   │  SI_bars_l2_1m.pq  │                │
│  └──────────┬──────────┘   └──────────┬──────────┘                │
└─────────────┼───────────────────────────┼────────────────────────── ┘
              │ pd.read_parquet            │
              ▼                            ▼
┌─────────────────────────────────────────────────────────────────────┐
│              tick_manual_signal_engine.py                           │
│                                                                     │
│  ┌───────────────┐   ┌────────────────┐   ┌─────────────────────┐ │
│  │ load_l2_bars  │   │ check_stale    │   │ is_news_window      │ │
│  │ (per symbol)  │──▶│ (age vs thresh)│──▶│ (NFP/FOMC/CPI)     │ │
│  └───────────────┘   └────────────────┘   └─────────────────────┘ │
│                               │                                     │
│                               ▼                                     │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │                   Strategy Registry                            │ │
│  │  CVD_Microprice/SI  │  Sweep_Continuation/SI  │ CVD_VWAP/GC  │ │
│  └────────────────────────────────────────────────────────────────┘ │
│                               │ generate_signals(df)                │
│                               ▼                                     │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │                   Blocker Stack                                │ │
│  │  1. Stale bar?          → block (skip symbol)                 │ │
│  │  2. News window?        → block (log + skip)                  │ │
│  │  3. Symbol cooldown?    → block (30-bar cooldown)             │ │
│  └────────────────────────────────────────────────────────────────┘ │
│                               │                                     │
│                               ▼                                     │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │                  Signal Builder                                │ │
│  │  build_signal_record() → entry_zone, stop, target, risk       │ │
│  │  market_regime()       → TRENDING / RANGING / VOLATILE        │ │
│  │  HypotheticalTracker   → track fill outcomes for post-analysis│ │
│  └────────────────────────────────────────────────────────────────┘ │
│                               │                                     │
└───────────────────────────────┼─────────────────────────────────────┘
                                │ send_signal() / send_blocked()
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│              tick_alert_router.py (AlertRouter)                     │
│                                                                     │
│  ┌──────────┐  ┌──────────┐  ┌──────────────┐  ┌───────────────┐ │
│  │ Console  │  │  JSONL   │  │  Telegram    │  │   Discord     │ │
│  │ (box UI) │  │  (disk)  │  │  (REST API)  │  │  (webhook)    │ │
│  └──────────┘  └──────────┘  └──────────────┘  └───────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│  06_live_trading/logs/signals_YYYYMMDD.jsonl                        │
│  06_live_trading/reports/daily_YYYYMMDD.json                        │
└─────────────────────────────────────────────────────────────────────┘
                                │
                                ▼ (end of day)
┌─────────────────────────────────────────────────────────────────────┐
│              tick_daily_signal_report.py                            │
│  • Reads JSONL, computes metrics, writes report JSON                │
│  • Optional --send-telegram summary                                 │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Component Responsibilities

| File | Role |
|------|------|
| `tick_manual_signal_engine.py` | Core engine: bar loading, stale check, strategy execution, blocker stack, signal construction, hypothetical tracking |
| `tick_alert_router.py` | Output routing: console, JSONL, Telegram, Discord |
| `tick_daily_signal_report.py` | End-of-day analytics: reads JSONL, computes metrics, writes report |
| `08_docs/manual_signal_system_design.md` | This document |
| `08_docs/manual_signal_user_guide.md` | Operator guide for daily use |

---

## Signal Lifecycle

```
1. BAR ARRIVES
   └─ Parquet file updated by bar builder (external process)
   └─ Engine polls on --interval (default 60s)

2. LOAD BARS
   └─ load_l2_bars(symbol, timeframe)
   └─ Prefer GC_bars_l2_1m.parquet → fallback to GC_bars_1m.parquet
   └─ Must have >= 60 rows

3. STALE CHECK
   └─ check_stale(df, timeframe, override)
   └─ Thresholds: 1m→3min, 3m→6min, 5m→10min, 15m→25min, 30m→45min
   └─ If stale: log WARN, write blocked record to JSONL, skip symbol

4. ATR COMPUTE
   └─ compute_atr(df, period=14) — True Range 14-bar rolling mean

5. COOLDOWN TICK
   └─ CooldownTracker.update_bar_count(symbol) — always increments

6. STRATEGY RUNS
   └─ strategy.generate_signals(df) → pd.Series of +1/0/-1
   └─ Only last bar's signal value examined
   └─ If 0: no event, continue

7. BLOCKER STACK (ordered)
   a. News window: is_news_window(now_utc) → (blocked, reason)
   b. Symbol cooldown: cooldown.is_in_cooldown(symbol) → (blocked, reason)
   └─ First blocker wins; rest not checked

8. SIGNAL RECORD BUILT
   └─ build_signal_record(cfg, side, bar_ts, data, atr, blocked, reason)
   └─ Fields: entry_zone, stop_price, target_price, risk_dollars,
              confidence, context, invalidation_condition, market_regime

9. ROUTING
   └─ If blocked: router.send_blocked(signal) → console + JSONL only
   └─ If fired:   router.send_signal(signal)  → all enabled destinations

10. JSONL LOGGING
    └─ Every signal (fired + blocked) written to signals_YYYYMMDD.jsonl
    └─ Blocked signals have is_blocked=True, block_reason set

11. HYPOTHETICAL TRACKING
    └─ HypotheticalTracker.add(signal) for fired signals
    └─ On next pass: update() scans forward bars for target/stop hit
    └─ Closed hypotheticals updated in JSONL with hypo_* fields
```

---

## Allowlist Management

### Adding a Strategy

1. Create (or verify) the strategy class in `src/strategies/l2_*.py`
2. Add a `StrategyConfig` entry to `build_default_registry()` in `tick_manual_signal_engine.py`
3. Add the strategy `name` to the `--strategy-allowlist` CLI default or pass it explicitly
4. Run one pass with `--dry-run` to verify signal output format before going live

### Removing a Strategy

Remove or comment-out the `StrategyConfig` entry in `build_default_registry()`. The strategy will no longer be instantiated. Historical JSONL records are unaffected.

### Deployed Strategies (initial)

| Strategy | Symbol | Key Params | R/R |
|----------|--------|-----------|-----|
| CVD_Microprice | SI | cvd_pct=60, mp_ticks=1.0, hold_bars=5 | 2.0 |
| Sweep_Continuation | SI | min_sweeps=3, confirm_bars=2, hold_bars=5 | 1.5 |
| CVD_VWAP | GC | vwap_band=0.5, cvd_pct=60, hold_bars=8 | 2.0 |

---

## Alert Format Specification

### Console (box-drawn)

```
╔══════════════════════════════════════════════════════════════╗
║ SIGNAL: CVD_Microprice | SI | LONG                           ║
║ Entry: 32.1500–32.2000  Stop: 31.9500  Target: 32.5500       ║
║ Risk: $125  R/R: 2.0  Confidence: HIGH                       ║
║ Context: cvd_delta=140 | mp=32.175 | session_vwap=32.100     ║
║ Regime: RANGING | Bar: 2026-06-03 14:30 UTC                  ║
║ Invalidation: Cancel if price < 32.050 before entry          ║
╚══════════════════════════════════════════════════════════════╝
```

Blocked signals print a single line:
```
[BLOCKED] CVD_Microprice | SI | LONG | Reason: NEWS: NFP 13:00-14:00 UTC
```

### Telegram (plain text)

```
SIGNAL: CVD_Microprice | SI | LONG
Entry: 32.1500–32.2000
Stop: 31.9500  Target: 32.5500
Risk: $125  R/R: 2.0  Confidence: HIGH
Regime: RANGING | Bar: 2026-06-03 14:30 UTC
Invalidation: Cancel if price < 32.050 before entry
```

---

## Log Format Specification

### signals_YYYYMMDD.jsonl

One JSON object per line. Fields:

| Field | Type | Description |
|-------|------|-------------|
| timestamp | ISO-8601 UTC | When the signal was generated |
| strategy_name | str | e.g. "CVD_Microprice" |
| symbol | str | e.g. "SI", "GC" |
| side | str | "LONG", "SHORT", or "N/A" (stale/blocked) |
| entry_zone | str | "32.1500–32.2000" |
| entry_low | float | Lower bound of entry zone |
| entry_high | float | Upper bound of entry zone |
| stop_price | float | 1.0 × ATR from entry_ref |
| target_price | float | rr_ratio × ATR from entry_ref |
| risk_points | float | abs(entry_ref - stop_price) |
| risk_dollars | float | risk_points × point_value |
| rr_ratio | float | Configured R/R ratio |
| confidence | str | "HIGH", "MEDIUM", "LOW" |
| context | dict | L2 feature values at signal bar |
| invalidation_condition | str | Text description |
| market_regime | str | "TRENDING", "RANGING", "VOLATILE" |
| is_blocked | bool | True if signal was suppressed |
| block_reason | str | Reason string if blocked |
| bar_timestamp | ISO-8601 | Bar that triggered the signal |
| atr | float | ATR value at signal bar |
| entry_ref | float | Close price of signal bar |
| hypo_fill_price | float\|null | Hypothetical fill price |
| hypo_exit_price | float\|null | Hypothetical exit price |
| hypo_outcome | str\|null | "WIN", "LOSS", or null |
| hypo_pnl_dollars | float\|null | Hypothetical P&L in dollars |
| hypo_r_achieved | float\|null | Hypothetical R multiple |

### daily_YYYYMMDD.json

Aggregated report. See `compute_report()` in `tick_daily_signal_report.py` for full schema.

---

## Configuration Reference

### Environment Variables

| Variable | Used By | Description |
|----------|---------|-------------|
| TELEGRAM_BOT_TOKEN | AlertRouter, report | Telegram bot token |
| TELEGRAM_CHAT_ID | AlertRouter, report | Telegram chat/channel ID |
| DISCORD_WEBHOOK_URL | AlertRouter | Discord webhook URL |

### Command-Line Flags — tick_manual_signal_engine.py

| Flag | Default | Description |
|------|---------|-------------|
| --watch | True | Run continuously (default mode) |
| --once | False | Run one pass and exit |
| --dry-run | False | Print signals, no disk writes |
| --symbols | GC SI | Symbols to monitor |
| --strategy-allowlist | (all) | Whitelist of strategy names |
| --telegram | False | Enable Telegram routing |
| --stale-threshold-minutes | auto | Override stale bar age threshold |
| --interval | 60 | Watch poll interval in seconds |

### Command-Line Flags — tick_daily_signal_report.py

| Flag | Default | Description |
|------|---------|-------------|
| --date | today | Date to report (YYYY-MM-DD) |
| --send-telegram | False | Send summary to Telegram |
| --no-write | False | Skip writing report JSON to disk |

---

## Known Limitations and Failure Modes

### L2 Data Dependency

- All three deployed strategies require L2 bar features (cvd, cvd_delta, buy_sweeps, sell_sweeps, microprice_last, session_vwap)
- If `GC_bars_l2_1m.parquet` is missing, the engine falls back to `GC_bars_1m.parquet` — signals that depend on L2 features will not fire (strategy returns zero signals)
- Missing columns are handled gracefully: strategies return pd.Series(0, ...) when required columns are absent

### Stale Bar False Positives

- Market is closed (weekends, holidays): all bars will appear stale. The engine logs warnings but does not have a market-hours filter built in. Stale blocks are expected and correct during off-hours.
- If the bar builder stops, bars age out and all symbols are blocked. Monitor the `STALE_BAR` count in the daily report.

### News Calendar

- FOMC dates are hardcoded for 2026. Update `_FOMC_DATES_2026` at start of each year.
- CPI and NFP dates are computed algorithmically (approximate). Exact BLS release dates occasionally differ from the 2nd-Tuesday/1st-Friday approximation by 1-2 days. For precise blocking, override the calendar with exact dates.
- The 30-minute buffer (before and after) is conservative. Adjust by changing the `buffer = timedelta(minutes=30)` value in `build_news_windows()`.

### Cooldown Counting

- The cooldown tracker counts *pass iterations*, not *clock minutes*. At the default 60-second poll interval, 30-bar cooldown = 30 minutes. If the interval is changed, the effective cooldown duration changes proportionally.

### Telegram Rate Limits

- Telegram Bot API allows ~30 messages/second. With multiple strategies firing simultaneously, this is not a concern. If running at very short intervals with many strategies, add a small delay between sends.

### Hypothetical Fill Assumptions

- Fill assumed at `entry_ref` (last bar close). Real fills will differ due to spread, slippage, and execution delay.
- Target/stop checked against bar high/low — assumes any price within a bar is achievable. This is optimistic; actual fills may miss by a tick.
- Timed-out positions (hold_bars elapsed with no target/stop hit) are classified as LOSS at final close price — this may understate or overstate the actual outcome.

---

## Monitoring Checklist

Run daily before session start:

- [ ] Check `06_live_trading/logs/signals_YYYYMMDD.jsonl` exists (created after first pass)
- [ ] Verify bar file timestamps are current: `Get-Item 01_data/tick_bars/GC_bars_l2_1m.parquet | Select-Object LastWriteTime`
- [ ] Check for STALE_BAR entries in today's JSONL — indicates bar builder stopped
- [ ] Confirm Telegram test message arrives (send manually via `tick_credentials_test.py` or curl)
- [ ] Verify news calendar: check if today has an NFP/FOMC/CPI release and confirm block fires
- [ ] Run `python tick_daily_signal_report.py --date YYYY-MM-DD` for yesterday's performance review
- [ ] Check for Python import errors in terminal output at startup (strategy import failure degrades to stub mode)
