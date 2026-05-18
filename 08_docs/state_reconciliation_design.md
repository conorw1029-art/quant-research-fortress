# State Persistence and Broker Reconciliation Design
**Date:** 2026-05-18  
**Status:** DESIGN DOCUMENT — Implementation required before demo auto-trade

---

## 1. Problem Statement

The current system has no persistent state. If `tick_live_executor.py` restarts:
- It does not know whether any positions are open
- It does not know what bracket orders are active
- It does not know what entry price was used
- It does not know the day's realized P&L
- It cannot detect a mismatch between local memory and broker state

This means a restart during an open trade could result in:
- A second entry signal firing while a position already exists (double-long)
- A missed exit because the executor does not know a position was open
- A position left unprotected if the bracket was not re-confirmed after reconnect

**State persistence and broker reconciliation must exist before any demo auto-trade begins.**

---

## 2. Persistent State Files

All files live under `06_live_trading/state/`. All are JSON or JSONL. All writes are atomic (write to `.tmp`, then rename).

### 2.1 positions.json

Current open positions, as last confirmed by broker reconciliation.

```json
{
  "last_updated": "2026-05-18T14:32:01Z",
  "source": "broker_confirmed",
  "positions": {
    "MESM5": {
      "net_pos": 1,
      "strategy_id": 2,
      "entry_px": 5320.25,
      "entry_time": "2026-05-18T14:30:00Z",
      "stop_px": 5308.00,
      "target_px": 5340.00,
      "entry_order_id": "12345",
      "stop_order_id": "12346",
      "target_order_id": "12347",
      "oco_id": "oco_98765",
      "oso_id": "oso_11111"
    }
  }
}
```

Fields:
- `net_pos`: +1 (long), -1 (short), 0 (flat)
- `source`: `"broker_confirmed"` | `"local_unconfirmed"` | `"reconcile_pending"`
- `entry_px`, `stop_px`, `target_px`: prices as submitted
- All order IDs for this position's bracket

### 2.2 open_orders.json

Active orders not yet filled or cancelled.

```json
{
  "last_updated": "2026-05-18T14:32:01Z",
  "orders": {
    "12345": {
      "symbol": "MESM5",
      "strategy_id": 2,
      "side": "BUY",
      "order_type": "Limit",
      "price": 5320.25,
      "quantity": 1,
      "status": "Working",
      "submitted_at": "2026-05-18T14:30:00Z",
      "oso_id": "oso_11111",
      "oco_id": null
    }
  }
}
```

### 2.3 active_brackets.json

Active bracket groups. Key is strategy_id. Updated when entry fills; cleared when stop or target fills.

```json
{
  "last_updated": "2026-05-18T14:32:01Z",
  "brackets": {
    "2": {
      "symbol": "MESM5",
      "entry_order_id": "12345",
      "stop_order_id": "12346",
      "target_order_id": "12347",
      "oco_id": "oco_98765",
      "oso_id": "oso_11111",
      "entry_filled": true,
      "entry_fill_px": 5321.00,
      "entry_fill_time": "2026-05-18T14:30:15Z",
      "broker_confirmed": true
    }
  }
}
```

### 2.4 daily_pnl.json

Realized P&L for the current trading day. Resets at session open.

```json
{
  "date": "2026-05-18",
  "last_updated": "2026-05-18T14:32:01Z",
  "realized_pnl": 47.50,
  "per_strategy": {
    "2": { "pnl": 47.50, "trades": 1, "wins": 1, "losses": 0 },
    "7": { "pnl": 0.0, "trades": 0, "wins": 0, "losses": 0 }
  },
  "daily_loss_limit": -500.0,
  "daily_loss_remaining": -547.50,
  "halt_triggered": false
}
```

### 2.5 strategy_halts.json

Per-strategy halt flags. Persists across restarts.

```json
{
  "last_updated": "2026-05-18T14:32:01Z",
  "halts": {
    "2": {
      "halted": false,
      "reason": null,
      "halted_at": null
    },
    "7": {
      "halted": true,
      "reason": "consecutive_losses_3",
      "halted_at": "2026-05-18T11:15:00Z"
    }
  }
}
```

### 2.6 account_state.json

Account-level risk state.

```json
{
  "last_updated": "2026-05-18T14:32:01Z",
  "account_id": "123456",
  "account_halt": false,
  "account_halt_reason": null,
  "daily_loss_triggered": false,
  "trailing_drawdown_remaining": 1850.00,
  "max_drawdown_limit": 2000.00,
  "session_open": true
}
```

### 2.7 last_seen_bar.json

Last processed bar timestamp per symbol. Used to detect stale data and prevent duplicate signal processing.

```json
{
  "last_updated": "2026-05-18T14:32:01Z",
  "bars": {
    "MESM5": {
      "timestamp": "2026-05-18T14:30:00Z",
      "bar_minutes": 15,
      "processed_at": "2026-05-18T14:32:01Z"
    },
    "MGCM5": {
      "timestamp": "2026-05-18T14:30:00Z",
      "bar_minutes": 5,
      "processed_at": "2026-05-18T14:31:45Z"
    }
  }
}
```

### 2.8 processed_signals.json

Signal IDs processed in the current session. Used for duplicate-entry protection on restart.

```json
{
  "last_updated": "2026-05-18T14:32:01Z",
  "session_date": "2026-05-18",
  "processed_ids": [
    "strat2_MESM5_20260518_143000",
    "strat7_MESM5_20260518_110000"
  ]
}
```

### 2.9 heartbeat.json

Written every N seconds by the executor. Used by monitoring to detect process death.

```json
{
  "timestamp": "2026-05-18T14:32:01Z",
  "pid": 12345,
  "mode": "DRY_RUN",
  "uptime_seconds": 3600,
  "bar_loop_count": 720,
  "last_signal_time": "2026-05-18T14:30:15Z",
  "broker_connected": true,
  "data_fresh": true
}
```

### 2.10 broker_reconciliation_log.jsonl

Append-only log of all reconciliation events.

```jsonl
{"timestamp": "2026-05-18T14:32:01Z", "type": "OK", "detail": "All positions match broker", "local_positions": 1, "broker_positions": 1}
{"timestamp": "2026-05-18T14:45:00Z", "type": "MISMATCH_CRITICAL", "detail": "Local flat but broker has MESM5 long 1", "action": "HALT_NEW_ENTRIES"}
```

---

## 3. State File Inventory

| File | Reset Frequency | Notes |
|---|---|---|
| `positions.json` | On exit confirmation | Never reset on restart alone |
| `open_orders.json` | On fill or cancel | Verified against broker on startup |
| `active_brackets.json` | On exit | Verified against broker on startup |
| `daily_pnl.json` | Daily session open | Carry forward until new session |
| `strategy_halts.json` | Manual reset only | Halts persist through restarts |
| `account_state.json` | On reconciliation | Updated every reconcile cycle |
| `last_seen_bar.json` | On each bar | Used for stale detection |
| `processed_signals.json` | Daily | Cleared at session open |
| `heartbeat.json` | Every 30s | Written by live loop |
| `broker_reconciliation_log.jsonl` | Append-only | Never truncated in production |

---

## 4. Startup Recovery Sequence

On every executor start, before processing any bar:

```
1. Read positions.json  →  What do we think we have?
2. Read active_brackets.json  →  What brackets are we tracking?
3. Call broker for open positions  →  What does broker actually have?
4. Call broker for open orders  →  What orders are still working?
5. Compare local vs broker  →  Run reconciliation (Section 5)
6. If reconciliation result is SAFE:  →  Proceed to bar loop
7. If reconciliation result is UNSAFE:  →  Halt new entries, alert user
8. Read strategy_halts.json  →  Apply persistent halts
9. Read daily_pnl.json  →  Restore daily P&L tracking
10. Read processed_signals.json  →  Restore duplicate protection
11. Read last_seen_bar.json  →  Check data freshness
```

---

## 5. Broker Reconciliation Logic

Reconciliation runs at startup and every N minutes (suggested: 5 minutes) during live operation.

### Scenario 1: Local flat, broker has position (CRITICAL)
```
Detected:  positions.json shows no position  
Broker:    Returns open MESM5 long 1

Action:
- Log CRITICAL to broker_reconciliation_log.jsonl
- Halt all new entries
- Alert user (Telegram + console)
- Do NOT attempt to flatten automatically (except in demo mode with explicit config)
- Wait for human confirmation before resuming
```

### Scenario 2: Local has position, broker is flat
```
Detected:  positions.json shows MESM5 long 1  
Broker:    Returns no positions

Action:
- Log MISMATCH to reconciliation log
- Halt the affected strategy
- Update positions.json to reflect broker truth (flat)
- Update active_brackets.json to clear the orphaned bracket
- Compute implied P&L if possible from last known entry_px
- Alert user
- Do NOT log a phantom P&L without human confirmation
```

### Scenario 3: Position exists, bracket orders are missing (CRITICAL)
```
Detected:  positions.json shows MESM5 long 1  
Broker:    Returns open position, but open_orders does not show stop or target orders

Action:
- Log CRITICAL: "Open position with no broker-native protection"
- Halt all new entries
- Alert user IMMEDIATELY
- Do NOT enter more positions
- Do NOT assume internal RiskManager stop is sufficient
```

### Scenario 4: Duplicate orders detected
```
Detected:  Broker returns two open BUY orders for same strategy/symbol

Action:
- Halt the affected strategy
- Log CRITICAL
- Alert user to manually review and cancel duplicates
- Do NOT cancel automatically (could cancel the wrong leg)
```

### Scenario 5: Broker API unreachable
```
Detected:  HTTP error or timeout when polling broker state

Action:
- Halt all new entries
- Keep existing broker-native stops/targets active (they live on the exchange)
- Log WARNING every 60 seconds
- Alert user
- Retry with exponential backoff
- Do NOT resume entries until at least one successful broker poll
```

### Scenario 6: Local and broker are consistent (CLEAN)
```
Detected:  All local positions match broker positions
           All active brackets confirmed present on broker

Action:
- Log OK to reconciliation log
- Update last_successful_reconciliation_time in account_state.json
- Resume normal operation
```

### Scenario 7: Stale reconciliation (no successful poll in > 10 minutes)
```
Detected:  account_state.last_reconciliation older than threshold

Action:
- Halt new entries
- Alert user
- Attempt immediate reconciliation
```

### Scenario 8: Position count mismatch (local has 2, broker has 1)
```
Action:
- Treat as CRITICAL
- Halt all entries
- Alert user
- Do not trade until counts match exactly
```

### Scenario 9: Clean startup — no positions anywhere
```
Detected:  Local is flat, broker is flat, no open orders

Action:
- Log CLEAN_STARTUP to reconciliation log
- Resume normal dry-run or demo operation
- This is the expected normal case
```

### Scenario 10: Unknown state — cannot determine (CRITICAL)
```
Detected:  Broker returned unexpected data structure or parse error

Action:
- Halt all new entries
- Log CRITICAL with full raw response
- Alert user
- Do not make assumptions about position state
```

---

## 6. State Distinction Definitions

| State Type | Description | Trading Allowed? |
|---|---|---|
| `local_confirmed` | Local state was confirmed by broker reconciliation in this session | Yes |
| `local_unconfirmed` | Local state not yet confirmed against broker (e.g., first seconds after restart) | No — must reconcile first |
| `broker_truth` | Data as returned directly from broker API | Yes — use to override local after mismatch |
| `reconciled` | Local and broker agree | Yes |
| `mismatch_safe` | Local says flat, broker says flat; minor field differences | Yes with warning |
| `mismatch_critical` | Position/order counts differ | No — halt and alert |
| `unknown` | Parse error or connection failure | No — halt and alert |

**Default behaviour in any non-`reconciled` or non-`local_confirmed` state:**
- No new entries
- No new bracket orders
- Alert user
- Preserve all existing broker-native orders (do not cancel)
- Do not trade until state is explicitly confirmed

---

## 7. Atomic Write Pattern

All state files must be written atomically to prevent partial writes on crash.

```python
import json
import os
from pathlib import Path

def write_state(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)   # atomic on POSIX; near-atomic on Windows NTFS
```

---

## 8. Implementation Priority Order

1. `positions.json` + `active_brackets.json` — highest priority; needed before any live order
2. `daily_pnl.json` + `strategy_halts.json` — needed for risk continuity across restarts
3. `broker_reconciliation_log.jsonl` + reconciliation logic — needed before demo
4. `account_state.json` + `heartbeat.json` — needed for monitoring
5. `last_seen_bar.json` + `processed_signals.json` — needed for duplicate protection

---

*State persistence and reconciliation must be built and tested before any connection to Tradovate demo API.*
