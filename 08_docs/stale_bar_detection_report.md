# Stale Bar Detection Report
**Date:** 2026-06-03  
**Status:** COMPLETE  
**New file:** `04_codebase/tick_stale_bar_detector.py`

---

## Purpose

In a live signal system, the bar builder may stop writing new bars due to:
- Data feed disconnection
- CME maintenance window
- NinjaTrader / Databento adapter crash
- Local machine sleep/hibernate
- Holiday with reduced liquidity

Without staleness detection, the signal engine would continue generating signals from bars
that are hours old, creating phantom signals on stale data. At best these are ignored; at
worst they trigger erroneous manual entries.

---

## Implementation

### `StaleBarDetector` class

**File:** `04_codebase/tick_stale_bar_detector.py`

```python
detector = StaleBarDetector()
result   = detector.check(bars_df, symbol="GC", timeframe="1m")

if result.is_stale:
    print(f"STALE: {result.age_minutes:.1f} min — {result.reason}")
```

The detector computes the age of the newest bar timestamp relative to `now_utc` and
compares it against a per-timeframe threshold. It also detects CME maintenance windows
and suppresses false positives during scheduled downtime.

### Staleness thresholds

| Timeframe | Max age before stale |
|-----------|----------------------|
| 1m / 1min | 3 minutes |
| 3m / 3min | 6 minutes |
| 5m / 5min | 10 minutes |
| 15m / 15min | 25 minutes |
| 30m / 30min | 45 minutes |

Rationale: each threshold is 3× the bar duration, allowing for minor delays without
false positives. The bar builder writes a new bar within seconds of close, so a 3×
window gives headroom for processing jitter.

### CME maintenance window suppression

CME Globex maintenance windows are known-good downtime — no new bars are expected:

| Window | Local ET |
|--------|----------|
| Daily (Mon–Thu) | 16:00–17:00 ET |
| Weekend | Friday 16:00 ET → Sunday 17:00 ET |

When a check fires during maintenance, `is_stale` returns `False` with reason
`"CME maintenance window — N.N min old but expected"`. This prevents false-positive
alerts while the exchange is intentionally offline.

### Result structure

```python
@dataclass
class StalenessResult:
    symbol:               str
    timeframe:            str
    is_stale:             bool
    newest_bar_time:      Optional[pd.Timestamp]
    check_time_utc:       datetime
    age_minutes:          float
    threshold_minutes:    float
    reason:               str
    is_maintenance_window: bool = False
```

### API surface

| Method | Description |
|--------|-------------|
| `check(bars, symbol, timeframe, now_utc)` | Check a DataFrame in memory |
| `check_file(bar_path, symbol, timeframe)` | Load parquet and check |
| `check_all(bar_dir, symbols, timeframe)` | Batch check multiple symbols |
| `log_result(result)` | Append to daily JSONL log |

---

## Integration with Manual Signal Engine

`tick_manual_signal_engine.py` calls `StaleBarDetector.check()` before running any
strategy. If the result is stale and not in a maintenance window, signal generation is
blocked for that symbol with reason `"STALE_BARS"`.

```python
stale_result = detector.check(bars, symbol, "1m")
if stale_result.is_stale:
    return SignalResult(
        is_blocked=True,
        block_reason=f"stale_bars: {stale_result.reason}",
        ...
    )
```

Blocked signals are logged to the JSONL file with `is_blocked=True` for later audit, but
are never routed to Telegram or the alert console.

---

## Log Output

Results are written to:
```
06_live_trading/logs/stale_bar_checks_YYYYMMDD.jsonl
```

Each line is a JSON object:
```json
{
  "symbol": "GC",
  "timeframe": "1m",
  "is_stale": false,
  "newest_bar_time": "2026-06-03 14:32:00+00:00",
  "check_time_utc": "2026-06-03T14:32:47.123456+00:00",
  "age_minutes": 0.8,
  "threshold_minutes": 3.0,
  "reason": "FRESH — 0.8 min old (threshold: 3 min)",
  "is_maintenance_window": false
}
```

---

## Usage in Production

```bash
# Standalone check (both symbols, 1m bars)
python tick_stale_bar_detector.py

# In signal engine (called automatically every loop cycle)
# See tick_manual_signal_engine.py --watch mode
```

---

## Files Created

| File | Purpose |
|------|---------|
| `04_codebase/tick_stale_bar_detector.py` | Staleness detector — new file, ~266 lines |
